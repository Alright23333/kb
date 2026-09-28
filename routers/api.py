"""REST API for the knowledge base.

Single-namespace model: a page is identified by `name` only
(title = link target = URL key). Journal entries are just pages whose
name is a date (YYYY-MM-DD).
"""
import aiosqlite
import io
import json
import os
import zipfile
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Body, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from database import get_db, DB_PATH
from parse import parse as parse_content

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────

class PageRename(BaseModel):
    name: str


class PageCreate(BaseModel):
    name: str
    content: str = ""


class PageUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────

async def _fetchone(db, query, params=()):
    cur = await db.execute(query, params)
    return await cur.fetchone()


async def _fetchall(db, query, params=()):
    cur = await db.execute(query, params)
    return await cur.fetchall()


async def _sync(db: aiosqlite.Connection, page_id: int, content: str) -> None:
    """Re-derive refs (links + tags) and properties from content."""
    sig = parse_content(content)

    await db.execute("DELETE FROM refs WHERE source_id = ?", (page_id,))
    await db.execute("DELETE FROM properties WHERE page_id = ?", (page_id,))

    # Deduplicate case-insensitively to avoid UNIQUE collisions (refs.target_name COLLATE NOCASE).
    seen_links: dict[str, str] = {}
    for t in sig["links"]:
        key = t.casefold()
        if key not in seen_links:
            seen_links[key] = t
    seen_tags: dict[str, str] = {}
    for t in sig["tags"]:
        key = t.casefold()
        if key not in seen_tags:
            seen_tags[key] = t

    for target in seen_links.values():
        await db.execute(
            "INSERT INTO refs (source_id, target_name, kind) VALUES (?, ?, 'link')",
            (page_id, target),
        )
    for target in seen_tags.values():
        await db.execute(
            "INSERT INTO refs (source_id, target_name, kind) VALUES (?, ?, 'tag')",
            (page_id, target),
        )
    for key, value in sig["properties"].items():
        await db.execute(
            "INSERT INTO properties (page_id, key, value) VALUES (?, ?, ?)",
            (page_id, key, value),
        )


async def _search(db, q: str, limit: int, offset: int) -> list[dict]:
    """Full-text search with trigram FTS + LIKE fallback for short CJK queries."""
    if len(q) >= 3:
        try:
            phrase = '"' + q.replace('"', '""') + '"'
            rows = await _fetchall(
                db,
                """
                SELECT p.name, p.updated_at, p.created_at
                FROM pages_fts f JOIN pages p ON p.id = f.rowid
                WHERE pages_fts MATCH ?
                ORDER BY rank LIMIT ? OFFSET ?
                """,
                (phrase, limit, offset),
            )
            if rows:
                return [dict(r) for r in rows]
        except Exception:
            pass

    like = f"%{q}%"
    rows = await _fetchall(
        db,
        """
        SELECT name, updated_at, created_at FROM pages
        WHERE name LIKE ? OR content LIKE ?
        ORDER BY updated_at DESC LIMIT ? OFFSET ?
        """,
        (like, like, limit, offset),
    )
    return [dict(r) for r in rows]


async def _rename_page(db: aiosqlite.Connection, page_id: int, old_name: str, new_name: str) -> None:
    """Rename a page atomically, handling refs PK collisions.

    Collision case: page A links to both [[OldName]] and [[NewName]].
    After rename, refs would have (A.id, 'NewName', 'link') twice → PK violation.
    Fix: delete the old-name refs first, then update.
    """
    # 1. Check new name doesn't exist (excluding self)
    dup = await _fetchone(
        db,
        "SELECT id FROM pages WHERE name = ? COLLATE NOCASE AND id != ?",
        (new_name, page_id),
    )
    if dup:
        raise HTTPException(409, f"Page '{new_name}' already exists")

    # 2. Pre-delete refs that would collide after rename
    #    (refs pointing to old_name from pages that also link to new_name)
    await db.execute(
        """
        DELETE FROM refs
        WHERE target_name = ? COLLATE NOCASE
          AND kind = 'link'
          AND source_id IN (
            SELECT DISTINCT source_id FROM refs
            WHERE target_name = ? COLLATE NOCASE AND kind = 'link'
          )
        """,
        (old_name, new_name),
    )

    # 3. Update page name
    await db.execute("UPDATE pages SET name = ? WHERE id = ?", (new_name, page_id))

    # 4. Rewrite inbound links
    await db.execute(
        "UPDATE refs SET target_name = ? WHERE target_name = ? COLLATE NOCASE",
        (new_name, old_name),
    )


# ── Custom CSS ─────────────────────────────────────────────────

CUSTOM_CSS_PATH = os.path.join(os.path.dirname(DB_PATH), "custom.css")


@router.get("/custom.css")
async def get_custom_css():
    """Serve user-defined custom CSS (data/custom.css). 404 if not set."""
    if not os.path.exists(CUSTOM_CSS_PATH):
        raise HTTPException(404, "No custom CSS")
    return StreamingResponse(
        open(CUSTOM_CSS_PATH, "rb"),
        media_type="text/css",
        headers={"Cache-Control": "no-cache"},
    )


@router.put("/custom.css")
async def put_custom_css(request: Request):
    """Save user-defined custom CSS to data/custom.css."""
    content = (await request.body()).decode("utf-8")
    os.makedirs(os.path.dirname(CUSTOM_CSS_PATH), exist_ok=True)
    with open(CUSTOM_CSS_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return {"saved": True, "path": CUSTOM_CSS_PATH}


# ── Pages CRUD ────────────────────────────────────────────────────

@router.get("/pages")
async def list_pages(
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db = await get_db()
    try:
        if tag:
            rows = await _fetchall(
                db,
                """
                SELECT p.name, p.updated_at, p.created_at
                FROM pages p
                JOIN refs r ON r.source_id = p.id
                WHERE r.kind = 'tag' AND r.target_name = ? COLLATE NOCASE
                ORDER BY p.updated_at DESC LIMIT ? OFFSET ?
                """,
                (tag, limit, offset),
            )
            return [dict(r) for r in rows]

        if search:
            return await _search(db, search, limit, offset)

        rows = await _fetchall(
            db,
            """
            SELECT name, updated_at, created_at FROM pages
            ORDER BY updated_at DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.get("/pages/names")
async def list_page_names():
    """Return every page name (lightweight, for unresolved-link detection)."""
    db = await get_db()
    try:
        rows = await _fetchall(db, "SELECT name FROM pages ORDER BY name")
        return [r["name"] for r in rows]
    finally:
        await db.close()


# ── Page Versions ────────────────────────────────────────────────

@router.get("/pages/{name:path}/versions")
async def list_versions(name: str):
    """List version snapshots for a page (newest first)."""
    db = await get_db()
    try:
        rows = await _fetchall(
            db,
            "SELECT id, page_name, created_at, length(content) AS size "
            "FROM page_versions WHERE page_name = ? COLLATE NOCASE "
            "ORDER BY id DESC LIMIT 100",
            (name,),
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.get("/pages/{name:path}/versions/{version_id}")
async def get_version(name: str, version_id: int):
    """Get full content of a specific version."""
    db = await get_db()
    try:
        row = await _fetchone(
            db,
            "SELECT id, page_name, content, created_at "
            "FROM page_versions WHERE id = ? AND page_name = ? COLLATE NOCASE",
            (version_id, name),
        )
        if not row:
            raise HTTPException(404, "Version not found")
        return dict(row)
    finally:
        await db.close()


@router.post("/pages/{name:path}/versions/{version_id}/restore")
async def restore_version(name: str, version_id: int):
    """Restore a page to a previous version. Creates the page if deleted."""
    db = await get_db()
    try:
        ver = await _fetchone(
            db,
            "SELECT content FROM page_versions WHERE id = ? AND page_name = ? COLLATE NOCASE",
            (version_id, name),
        )
        if not ver:
            raise HTTPException(404, "Version not found")

        row = await _fetchone(db, "SELECT id FROM pages WHERE name = ? COLLATE NOCASE", (name,))
        if row:
            await db.execute("UPDATE pages SET content = ? WHERE id = ?", (ver["content"], row["id"]))
            await _sync(db, row["id"], ver["content"])
            await db.execute(
                "UPDATE pages SET updated_at = datetime('now', 'localtime') WHERE id = ?",
                (row["id"],),
            )
        else:
            # Page was deleted; re-create it from the version snapshot.
            cur = await db.execute("INSERT INTO pages (name, content) VALUES (?, ?)", (name, ver["content"]))
            await _sync(db, cur.lastrowid, ver["content"])
        await db.commit()
        return {"name": name, "restored": True, "version_id": version_id}
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


@router.delete("/pages/{name:path}/versions")
async def purge_versions(name: str):
    """Delete all version snapshots for a page."""
    db = await get_db()
    try:
        cur = await db.execute("DELETE FROM page_versions WHERE page_name = ? COLLATE NOCASE", (name,))
        await db.commit()
        return {"name": name, "purged": cur.rowcount}
    finally:
        await db.close()


@router.get("/trash")
async def list_trash():
    """List deleted pages: names with version snapshots but no live page."""
    db = await get_db()
    try:
        rows = await _fetchall(
            db,
            """
            SELECT page_name,
                   COUNT(*) AS version_count,
                   MAX(created_at) AS last_modified,
                   MAX(id) AS latest_version_id
            FROM page_versions
            WHERE page_name NOT IN (SELECT name FROM pages)
            GROUP BY page_name
            ORDER BY last_modified DESC
            """,
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()



@router.get("/pages/{name:path}")
async def get_page(name: str):
    db = await get_db()
    try:
        row = await _fetchone(db, "SELECT * FROM pages WHERE name = ? COLLATE NOCASE", (name,))
        if not row:
            raise HTTPException(404, "Page not found")

        page = dict(row)

        tags = await _fetchall(
            db,
            "SELECT target_name FROM refs WHERE source_id = ? AND kind = 'tag'",
            (page["id"],),
        )
        page["tags"] = [t["target_name"] for t in tags]

        links = await _fetchall(
            db,
            "SELECT target_name FROM refs WHERE source_id = ? AND kind = 'link'",
            (page["id"],),
        )
        page["links"] = [l["target_name"] for l in links]

        backlinks = await _fetchall(
            db,
            """
            SELECT p.name, p.updated_at FROM refs r
            JOIN pages p ON p.id = r.source_id
            WHERE r.kind = 'link' AND r.target_name = ? COLLATE NOCASE
            ORDER BY p.updated_at DESC
            """,
            (name,),
        )
        page["backlinks"] = [dict(b) for b in backlinks]

        props = await _fetchall(
            db, "SELECT key, value FROM properties WHERE page_id = ?", (page["id"],)
        )
        page["properties"] = {p["key"]: p["value"] for p in props}

        return page
    finally:
        await db.close()


@router.post("/pages")
async def create_page(data: PageCreate):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Name must not be empty")

    db = await get_db()
    try:
        dup = await _fetchone(db, "SELECT id FROM pages WHERE name = ? COLLATE NOCASE", (name,))
        if dup:
            raise HTTPException(409, f"Page '{name}' already exists")

        cur = await db.execute(
            "INSERT INTO pages (name, content) VALUES (?, ?)", (name, data.content)
        )
        page_id = cur.lastrowid
        await _sync(db, page_id, data.content)
        await db.commit()
        return {"name": name, "id": page_id}
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        if "UNIQUE constraint" in str(e):
            raise HTTPException(409, f"Page '{name}' already exists")
        raise
    finally:
        await db.close()


@router.put("/pages/{name:path}")
async def update_page(name: str, data: PageUpdate):
    db = await get_db()
    try:
        row = await _fetchone(db, "SELECT id FROM pages WHERE name = ? COLLATE NOCASE", (name,))
        if not row:
            raise HTTPException(404, "Page not found")
        page_id = row["id"]

        new_name = name
        if data.name is not None and data.name.strip():
            new_name = data.name.strip()
            if new_name.casefold() != name.casefold():
                await _rename_page(db, page_id, name, new_name)

        if data.content is not None:
            await db.execute("UPDATE pages SET content = ? WHERE id = ?", (data.content, page_id))
            await _sync(db, page_id, data.content)

        await db.execute(
            "UPDATE pages SET updated_at = datetime('now', 'localtime') WHERE id = ?", (page_id,)
        )
        await db.commit()
        return {"name": new_name, "updated": True}
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        if "UNIQUE constraint" in str(e):
            raise HTTPException(409, f"Page '{name}' already exists")
        raise
    finally:
        await db.close()


@router.patch("/pages/{name:path}/rename")
async def rename_page(name: str, data: PageRename):
    """Rename a page. Atomic: either succeeds or rolls back entirely."""
    new_name = data.name.strip()
    if not new_name:
        raise HTTPException(400, "New name must not be empty")

    db = await get_db()
    try:
        row = await _fetchone(db, "SELECT id FROM pages WHERE name = ? COLLATE NOCASE", (name,))
        if not row:
            raise HTTPException(404, "Page not found")

        if new_name.casefold() == name.casefold():
            return {"name": new_name, "renamed": False, "reason": "same name"}

        await _rename_page(db, row["id"], name, new_name)
        await db.execute(
            "UPDATE pages SET updated_at = datetime('now', 'localtime') WHERE id = ?",
            (row["id"],),
        )
        await db.commit()
        return {"name": new_name, "renamed": True}
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        if "UNIQUE constraint" in str(e):
            raise HTTPException(409, f"Page '{new_name}' already exists")
        raise
    finally:
        await db.close()


@router.delete("/pages/{name:path}")
async def delete_page(name: str):
    db = await get_db()
    try:
        cur = await db.execute("DELETE FROM pages WHERE name = ? COLLATE NOCASE", (name,))
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Page not found")
        return {"deleted": True}
    finally:
        await db.close()


# ── Tags ──────────────────────────────────────────────────────────

@router.get("/tags")
async def list_tags():
    db = await get_db()
    try:
        rows = await _fetchall(
            db,
            """
            SELECT target_name AS name, COUNT(*) AS count
            FROM refs WHERE kind = 'tag'
            GROUP BY target_name
            ORDER BY count DESC, target_name
            """,
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.get("/tags/{tag_name:path}/pages")
async def get_pages_by_tag(tag_name: str):
    db = await get_db()
    try:
        rows = await _fetchall(
            db,
            """
            SELECT p.name, p.updated_at, p.created_at FROM pages p
            JOIN refs r ON r.source_id = p.id
            WHERE r.kind = 'tag' AND r.target_name = ? COLLATE NOCASE
            ORDER BY p.updated_at DESC
            """,
            (tag_name,),
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


# ── Export / Import ─────────────────────────────────────────────


def _safe_filename(name: str) -> str:
    return (
        name.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "-")
        .replace("?", "")
        .replace("*", "")
        .replace('"', "'")
        .replace("<", "(")
        .replace(">", ")")
        .replace("|", "_")
    )


async def _export_all_pages(db: aiosqlite.Connection) -> list[dict]:
    """Fetch all pages with tags/links/properties for export."""
    pages = []
    rows = await _fetchall(db, "SELECT * FROM pages ORDER BY name")
    for row in rows:
        p = dict(row)
        p["tags"] = [
            r["target_name"]
            for r in await _fetchall(
                db, "SELECT target_name FROM refs WHERE source_id=? AND kind='tag'", (row["id"],)
            )
        ]
        p["links"] = [
            r["target_name"]
            for r in await _fetchall(
                db, "SELECT target_name FROM refs WHERE source_id=? AND kind='link'", (row["id"],)
            )
        ]
        p["properties"] = {
            r["key"]: r["value"]
            for r in await _fetchall(
                db, "SELECT key, value FROM properties WHERE page_id=?", (row["id"],)
            )
        }
        pages.append(p)
    return pages


def _build_front_matter(p: dict) -> str:
    lines = ["---"]
    lines.append(f"name: {p['name']}")
    lines.append(f"created: {p['created_at']}")
    lines.append(f"updated: {p['updated_at']}")
    if p.get("tags"):
        lines.append(f"tags: [{', '.join(p['tags'])}]")
    for k, v in p.get("properties", {}).items():
        lines.append(f"{k}: {v or ''}")
    lines.append("---\n")
    return "\n".join(lines)


@router.get("/export/zip")
async def export_zip():
    """Download all pages as a zip of .md files (with front matter)."""
    db = await get_db()
    try:
        pages = await _export_all_pages(db)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in pages:
                zf.writestr(_safe_filename(p["name"]) + ".md", _build_front_matter(p) + p["content"])
        buf.seek(0)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="kb_export_{ts}.zip"'},
        )
    finally:
        await db.close()


@router.get("/export/json")
async def export_json():
    """Download full database dump as JSON."""
    db = await get_db()
    try:
        pages = await _export_all_pages(db)
        refs = await _fetchall(
            db,
            "SELECT p.name AS source_name, r.target_name, r.kind "
            "FROM refs r JOIN pages p ON p.id = r.source_id",
        )
        data = {
            "version": 1,
            "pages": pages,
            "refs": [dict(r) for r in refs],
        }
        content = json.dumps(data, ensure_ascii=False, indent=2)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="kb_export_{ts}.json"'},
        )
    finally:
        await db.close()


def _parse_front_matter(text: str) -> tuple[str, str]:
    """Extract front matter and body. Returns (name, body)."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            fm = text[4:end]
            body = text[end + 5 :]
            name = ""
            for line in fm.split("\n"):
                if line.startswith("name:"):
                    name = line[5:].strip()
                    break
            return name, body
    return "", text.lstrip("\ufeff")


@router.post("/import")
async def import_pages(
    file: UploadFile = File(...),
    conflict: str = Form("skip"),  # skip | overwrite
):
    """Import .md file or zip of .md files.

    Front matter `name:` overrides filename. Conflict resolution:
    skip (default) or overwrite existing pages.
    """
    raw = await file.read()
    entries: list[tuple[str, str]] = []  # (name, content)

    if file.filename and file.filename.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            raise HTTPException(400, "Invalid zip file")
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".md"):
                continue
            text = zf.read(info).decode("utf-8", errors="replace")
            fm_name, body = _parse_front_matter(text)
            name = fm_name or info.filename.rsplit("/", 1)[-1][:-3]
            entries.append((name, body))
    elif file.filename and file.filename.lower().endswith(".md"):
        text = raw.decode("utf-8", errors="replace")
        fm_name, body = _parse_front_matter(text)
        name = fm_name or file.filename[:-3]
        entries.append((name, body))
    else:
        raise HTTPException(400, "Only .md or .zip files are supported")

    db = await get_db()
    try:
        created, skipped, overwritten = 0, 0, 0
        for name, content in entries:
            name = name.strip()
            if not name:
                skipped += 1
                continue
            existing = await _fetchone(db, "SELECT id FROM pages WHERE name = ? COLLATE NOCASE", (name,))
            if existing:
                if conflict == "overwrite":
                    await db.execute("UPDATE pages SET content = ? WHERE id = ?", (content, existing["id"]))
                    await _sync(db, existing["id"], content)
                    await db.execute(
                        "UPDATE pages SET updated_at = datetime('now', 'localtime') WHERE id = ?",
                        (existing["id"],),
                    )
                    overwritten += 1
                else:
                    skipped += 1
            else:
                cur = await db.execute(
                    "INSERT INTO pages (name, content) VALUES (?, ?)", (name, content)
                )
                page_id = cur.lastrowid
                await _sync(db, page_id, content)
                created += 1
        await db.commit()
        return {
            "created": created,
            "skipped": skipped,
            "overwritten": overwritten,
            "total": len(entries),
        }
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


# ── Graph ─────────────────────────────────────────────────────────

@router.get("/graph")
async def get_graph():
    db = await get_db()
    try:
        pages = await _fetchall(db, "SELECT name FROM pages")
        links = await _fetchall(
            db,
            """
            SELECT p.name AS source, r.target_name AS target
            FROM refs r JOIN pages p ON p.id = r.source_id
            WHERE r.kind = 'link'
            """,
        )
        return {
            "nodes": [{"name": p["name"]} for p in pages],
            "edges": [{"source": l["source"], "target": l["target"]} for l in links],
        }
    finally:
        await db.close()
