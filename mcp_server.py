"""MCP server exposing KB tools for AI agents.

Run: python mcp_server.py  (stdio transport)

NOTE: MCP SDK only inherits whitelist env vars (PATH, HOME, etc).
You MUST set KB_DB_PATH in your MCP client config, or place the DB
at <script_dir>/data/kb.db (the default fallback).

Claude Desktop config (claude_desktop_config.json):
{
  "mcpServers": {
    "kb": {
      "command": "python3",
      "args": ["/path/to/kb/mcp_server.py"],
      "env": { "KB_DB_PATH": "/path/to/kb/data/kb.db" }
    }
  }
}

Tools:
  search(query)           → FTS search
  get_page(name)          → full page + tags + links + backlinks
  create_page(name, content) → create new page
  update_page(name, content) → update existing page
  delete_page(name)       → delete page (restorable via versions)
  rename_page(old, new)   → rename + rewrite inbound links (atomic)
  list_pages()            → all page names
  get_recent_pages(limit) → recently updated pages
  get_tags()              → all tags with counts
  get_pages_by_tag(tag)   → pages having a tag
  get_backlinks(name)     → pages linking to this page
  get_page_versions(name) → version history (works for deleted pages)
  restore_page_version(name, version_id) → restore old version
"""
import asyncio
import json
import os
import sqlite3

from fastmcp import FastMCP

DB_PATH = os.environ.get("KB_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "kb.db"))
DB_PATH = os.path.expanduser(DB_PATH)

mcp = FastMCP("KB")


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _sync_refs(conn: sqlite3.Connection, page_id: int, content: str) -> None:
    """Re-index refs/properties from content (delete-then-insert)."""
    from parse import parse as parse_content

    conn.execute("DELETE FROM refs WHERE source_id=?", (page_id,))
    conn.execute("DELETE FROM properties WHERE page_id=?", (page_id,))

    sig = parse_content(content)
    seen = {}
    for t in sig["links"]:
        k = t.casefold()
        if k not in seen:
            seen[k] = t
    for target in seen.values():
        conn.execute(
            "INSERT INTO refs (source_id, target_name, kind) VALUES (?,?,'link')",
            (page_id, target),
        )
    seen = {}
    for t in sig["tags"]:
        k = t.casefold()
        if k not in seen:
            seen[k] = t
    for target in seen.values():
        conn.execute(
            "INSERT INTO refs (source_id, target_name, kind) VALUES (?,?,'tag')",
            (page_id, target),
        )
    for k, v in sig["properties"].items():
        conn.execute(
            "INSERT INTO properties (page_id, key, value) VALUES (?,?,?)",
            (page_id, k, v),
        )


@mcp.tool
def search(query: str, limit: int = 10) -> str:
    """Full-text search across all pages. Returns matching page names with dates.

    Args:
        query: Search query (supports CJK, min 3 chars for FTS, shorter falls back to LIKE)
        limit: Max results (default 10)
    """
    conn = _db()
    try:
        rows = []
        if len(query) >= 3:
            phrase = '"' + query.replace('"', '""') + '"'
            try:
                rows = conn.execute(
                    "SELECT p.name, p.updated_at FROM pages_fts f "
                    "JOIN pages p ON p.id = f.rowid "
                    "WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?",
                    (phrase, limit),
                ).fetchall()
            except Exception:
                pass
        if not rows:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT name, updated_at FROM pages "
                "WHERE name LIKE ? OR content LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        if not rows:
            return f"No results for '{query}'"
        return "\n".join(f"- {r['name']} (updated {r['updated_at']})" for r in rows)
    finally:
        conn.close()


@mcp.tool
def get_page(name: str) -> str:
    """Get a page's full content, tags, links, backlinks, and properties.

    Args:
        name: Page name (case-insensitive)
    """
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM pages WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if not row:
            return f"Page '{name}' not found"

        tags = [r["target_name"] for r in conn.execute(
            "SELECT target_name FROM refs WHERE source_id=? AND kind='tag'",
            (row["id"],),
        ).fetchall()]
        links = [r["target_name"] for r in conn.execute(
            "SELECT target_name FROM refs WHERE source_id=? AND kind='link'",
            (row["id"],),
        ).fetchall()]
        backlinks = [dict(r) for r in conn.execute(
            "SELECT p.name, p.updated_at FROM refs r "
            "JOIN pages p ON p.id = r.source_id "
            "WHERE r.kind='link' AND r.target_name=? COLLATE NOCASE "
            "ORDER BY p.updated_at DESC",
            (name,),
        ).fetchall()]
        props = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM properties WHERE page_id=?",
            (row["id"],),
        ).fetchall()}

        parts = [f"# {row['name']}", ""]
        if props:
            parts.append("Properties: " + json.dumps(props, ensure_ascii=False))
        if tags:
            parts.append(f"Tags: {', '.join('#' + t for t in tags)}")
        if links:
            parts.append(f"Links to: {', '.join(links)}")
        if backlinks:
            parts.append(f"Backlinks from: {', '.join(b['name'] for b in backlinks)}")
        parts.append(f"\n---\n{row['content']}")
        return "\n".join(parts)
    finally:
        conn.close()


@mcp.tool
def create_page(name: str, content: str) -> str:
    """Create a new page. Fails if page already exists.

    Args:
        name: Page name (becomes the [[link]] target)
        content: Markdown content (supports [[links]], #tags, key:: value)
    """
    from parse import parse as parse_content
    conn = _db()
    try:
        dup = conn.execute(
            "SELECT id FROM pages WHERE name=? COLLATE NOCASE", (name.strip(),)
        ).fetchone()
        if dup:
            return f"Error: Page '{name}' already exists"

        cur = conn.execute(
            "INSERT INTO pages (name, content) VALUES (?, ?)",
            (name.strip(), content),
        )
        page_id = cur.lastrowid
        _sync_refs(conn, page_id, content)
        conn.commit()
        return f"Created page '{name}' (id={page_id})"
    except Exception as e:
        conn.rollback()
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool
def update_page(name: str, content: str) -> str:
    """Update a page's content. Parses and re-indexes links/tags/properties.

    Args:
        name: Page name (case-insensitive)
        content: New markdown content
    """
    from parse import parse as parse_content
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id FROM pages WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if not row:
            return f"Error: Page '{name}' not found"
        page_id = row["id"]

        conn.execute("UPDATE pages SET content=?, updated_at=datetime('now','localtime') WHERE id=?",
                     (content, page_id))
        conn.execute("DELETE FROM refs WHERE source_id=?", (page_id,))
        conn.execute("DELETE FROM properties WHERE page_id=?", (page_id,))

        _sync_refs(conn, page_id, content)
        conn.commit()
        return f"Updated page '{name}'"
    except Exception as e:
        conn.rollback()
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool
def list_pages() -> str:
    """List all page names, sorted alphabetically."""
    conn = _db()
    try:
        rows = conn.execute("SELECT name FROM pages ORDER BY name").fetchall()
        if not rows:
            return "No pages in knowledge base"
        return "\n".join(r["name"] for r in rows)
    finally:
        conn.close()


@mcp.tool
def get_tags() -> str:
    """List all tags with page counts, sorted by count descending."""
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT target_name AS name, COUNT(*) AS count FROM refs "
            "WHERE kind='tag' GROUP BY target_name ORDER BY count DESC, target_name"
        ).fetchall()
        if not rows:
            return "No tags"
        return "\n".join(f"#{r['name']} ({r['count']})" for r in rows)
    finally:
        conn.close()


@mcp.tool
def get_backlinks(name: str) -> str:
    """Get all pages that link to the given page.

    Args:
        name: Target page name (case-insensitive)
    """
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT p.name, p.updated_at FROM refs r "
            "JOIN pages p ON p.id = r.source_id "
            "WHERE r.kind='link' AND r.target_name=? COLLATE NOCASE "
            "ORDER BY p.updated_at DESC",
            (name,),
        ).fetchall()
        if not rows:
            return f"No backlinks to '{name}'"
        return "\n".join(f"- {r['name']} (updated {r['updated_at']})" for r in rows)
    finally:
        conn.close()


@mcp.tool
def rename_page(old_name: str, new_name: str) -> str:
    """Rename a page and rewrite all inbound [[links]]. Atomic.

    Args:
        old_name: Current page name (case-insensitive)
        new_name: New page name
    """
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not new_name:
        return "Error: new name must not be empty"

    conn = _db()
    try:
        row = conn.execute(
            "SELECT id FROM pages WHERE name=? COLLATE NOCASE", (old_name,)
        ).fetchone()
        if not row:
            return f"Error: Page '{old_name}' not found"
        page_id = row["id"]

        if new_name.casefold() == old_name.casefold():
            return f"Same name, nothing to do"

        dup = conn.execute(
            "SELECT id FROM pages WHERE name=? COLLATE NOCASE AND id != ?",
            (new_name, page_id),
        ).fetchone()
        if dup:
            return f"Error: Page '{new_name}' already exists"

        # Pre-delete refs that would collide (page links both old and new names)
        conn.execute(
            """
            DELETE FROM refs
            WHERE target_name = ? COLLATE NOCASE AND kind = 'link'
              AND source_id IN (
                SELECT DISTINCT source_id FROM refs
                WHERE target_name = ? COLLATE NOCASE AND kind = 'link'
              )
            """,
            (old_name, new_name),
        )
        conn.execute("UPDATE pages SET name=? WHERE id=?", (new_name, page_id))
        conn.execute(
            "UPDATE refs SET target_name=? WHERE target_name=? COLLATE NOCASE",
            (new_name, old_name),
        )
        conn.commit()
        return f"Renamed '{old_name}' → '{new_name}'"
    except Exception as e:
        conn.rollback()
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool
def delete_page(name: str) -> str:
    """Delete a page. A version snapshot is saved automatically,
    so it can be restored via restore_page_version later.

    Args:
        name: Page name (case-insensitive)
    """
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id FROM pages WHERE name=? COLLATE NOCASE", (name.strip(),)
        ).fetchone()
        if not row:
            return f"Error: Page '{name}' not found"
        conn.execute("DELETE FROM pages WHERE id=?", (row["id"],))
        conn.commit()
        return f"Deleted page '{name}' (restorable via restore_page_version)"
    except Exception as e:
        conn.rollback()
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool
def get_pages_by_tag(tag: str) -> str:
    """List all pages that have the given tag.

    Args:
        tag: Tag name without # prefix (case-insensitive)
    """
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT p.name, p.updated_at FROM refs r "
            "JOIN pages p ON p.id = r.source_id "
            "WHERE r.kind='tag' AND r.target_name=? COLLATE NOCASE "
            "ORDER BY p.updated_at DESC",
            (tag.strip().lstrip("#"),),
        ).fetchall()
        if not rows:
            return f"No pages with tag '#{tag}'"
        return f"Pages with #{tag}:\n" + "\n".join(
            f"- {r['name']} (updated {r['updated_at']})" for r in rows
        )
    finally:
        conn.close()


@mcp.tool
def get_recent_pages(limit: int = 10) -> str:
    """List recently updated pages.

    Args:
        limit: Max results (default 10)
    """
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT name, updated_at FROM pages ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if not rows:
            return "No pages"
        return "\n".join(f"- {r['name']} (updated {r['updated_at']})" for r in rows)
    finally:
        conn.close()


@mcp.tool
def get_page_versions(name: str) -> str:
    """List version snapshots of a page (newest first).
    Works even for deleted pages.

    Args:
        name: Page name (case-insensitive)
    """
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, created_at, length(content) AS size "
            "FROM page_versions WHERE page_name=? COLLATE NOCASE "
            "ORDER BY id DESC LIMIT 20",
            (name.strip(),),
        ).fetchall()
        if not rows:
            return f"No versions for '{name}'"
        return f"Versions of '{name}':\n" + "\n".join(
            f"- v{r['id']} {r['created_at']} ({r['size']} bytes)" for r in rows
        )
    finally:
        conn.close()


@mcp.tool
def restore_page_version(name: str, version_id: int) -> str:
    """Restore a page to a previous version. Re-creates the page
    if it was deleted.

    Args:
        name: Page name (case-insensitive)
        version_id: Version ID from get_page_versions
    """
    conn = _db()
    try:
        ver = conn.execute(
            "SELECT content FROM page_versions WHERE id=? AND page_name=? COLLATE NOCASE",
            (version_id, name.strip()),
        ).fetchone()
        if not ver:
            return f"Error: Version {version_id} of '{name}' not found"

        row = conn.execute(
            "SELECT id FROM pages WHERE name=? COLLATE NOCASE", (name.strip(),)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE pages SET content=? WHERE id=?", (ver["content"], row["id"])
            )
            _sync_refs(conn, row["id"], ver["content"])
            conn.execute(
                "UPDATE pages SET updated_at=datetime('now','localtime') WHERE id=?",
                (row["id"],),
            )
        else:
            cur = conn.execute(
                "INSERT INTO pages (name, content) VALUES (?, ?)",
                (name.strip(), ver["content"]),
            )
            _sync_refs(conn, cur.lastrowid, ver["content"])
        conn.commit()
        return f"Restored '{name}' to version {version_id}"
    except Exception as e:
        conn.rollback()
        return f"Error: {e}"
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
