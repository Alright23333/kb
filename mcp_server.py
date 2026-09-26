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
  list_pages()            → all page names
  get_tags()              → all tags with counts
  get_backlinks(name)     → pages linking to this page
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


if __name__ == "__main__":
    mcp.run()
