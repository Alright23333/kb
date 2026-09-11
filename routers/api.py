"""REST API for the knowledge base.

Single-namespace model: a page is identified by `name` only
(title = link target = URL key). Journal entries are just pages whose
name is a date (YYYY-MM-DD).
"""
import aiosqlite
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database import get_db
from parse import parse as parse_content

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────

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


# ── Pages CRUD ────────────────────────────────────────────────────

@router.get("/pages")
async def list_pages(
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
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
                dup = await _fetchone(
                    db,
                    "SELECT id FROM pages WHERE name = ? COLLATE NOCASE AND id != ?",
                    (new_name, page_id),
                )
                if dup:
                    raise HTTPException(409, f"Page '{new_name}' already exists")
                await db.execute("UPDATE pages SET name = ? WHERE id = ?", (new_name, page_id))
                # Rewrite inbound links that pointed at the old name.
                await db.execute(
                    "UPDATE refs SET target_name = ? WHERE target_name = ? COLLATE NOCASE",
                    (new_name, name),
                )

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
