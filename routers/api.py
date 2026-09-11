"""Core API routes for the knowledge base."""
import aiosqlite
import re
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from database import get_db

router = APIRouter()

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


# ── Pydantic models ──────────────────────────────────────────────

class PageCreate(BaseModel):
    slug: str
    title: str
    content: str = ""
    tags: list[str] = []


class PageUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None


class JournalUpdate(BaseModel):
    content: str


# ── Helpers ───────────────────────────────────────────────────────

async def _fetchone(db, query, params=()):
    cursor = await db.execute(query, params)
    return await cursor.fetchone()


async def _fetchall(db, query, params=()):
    cursor = await db.execute(query, params)
    return await cursor.fetchall()


async def _sync_links(db: aiosqlite.Connection, page_id: int, content: str):
    """Extract [[wiki-links]] from content and sync to links table."""
    targets = set(WIKILINK_RE.findall(content))
    for target in targets:
        await db.execute(
            "INSERT OR IGNORE INTO links (source_id, target_slug) VALUES (?, ?)",
            (page_id, target),
        )


# ── Pages CRUD ────────────────────────────────────────────────────

@router.get("/pages")
async def list_pages(
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List pages, optionally filtered by tag or search query."""
    db = await get_db()
    try:
        if tag:
            rows = await _fetchall(db,
                """
                SELECT p.slug, p.title, p.updated_at, p.created_at
                FROM pages p
                JOIN page_tags pt ON p.id = pt.page_id
                JOIN tags t ON pt.tag_id = t.id
                WHERE t.name = ?
                ORDER BY p.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (tag, limit, offset),
            )
        elif search:
            # Try FTS first, fall back to LIKE for CJK / partial matches
            rows = await _fetchall(db,
                """
                SELECT p.slug, p.title, p.updated_at, p.created_at
                FROM pages_fts f
                JOIN pages p ON p.id = f.rowid
                WHERE pages_fts MATCH ?
                ORDER BY rank
                LIMIT ? OFFSET ?
                """,
                (search, limit, offset),
            )
            if not rows:
                rows = await _fetchall(db,
                    """
                    SELECT slug, title, updated_at, created_at
                    FROM pages
                    WHERE title LIKE ? OR content LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (f"%{search}%", f"%{search}%", limit, offset),
                )
        else:
            rows = await _fetchall(db,
                "SELECT slug, title, updated_at, created_at FROM pages ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )

        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.get("/pages/{slug}")
async def get_page(slug: str):
    """Get a single page with tags, outgoing links, and backlinks."""
    db = await get_db()
    try:
        row = await _fetchone(db, "SELECT * FROM pages WHERE slug = ?", (slug,))
        if not row:
            raise HTTPException(404, "Page not found")

        page = dict(row)

        # Get tags
        tags = await _fetchall(db,
            "SELECT t.name FROM tags t JOIN page_tags pt ON t.id = pt.tag_id WHERE pt.page_id = ?",
            (page["id"],),
        )
        page["tags"] = [t["name"] for t in tags]

        # Get outgoing links
        links = await _fetchall(db,
            "SELECT target_slug FROM links WHERE source_id = ?", (page["id"],)
        )
        page["links"] = [l["target_slug"] for l in links]

        # Get backlinks
        backlinks = await _fetchall(db,
            """
            SELECT p.slug, p.title FROM links l
            JOIN pages p ON l.source_id = p.id
            WHERE l.target_slug = ?
            """,
            (slug,),
        )
        page["backlinks"] = [dict(b) for b in backlinks]

        return page
    finally:
        await db.close()


@router.post("/pages")
async def create_page(data: PageCreate):
    """Create a new page."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO pages (slug, title, content) VALUES (?, ?, ?)",
            (data.slug, data.title, data.content),
        )
        page_id = cursor.lastrowid

        # Handle tags
        for tag_name in data.tags:
            await db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
            tag_row = await _fetchone(db, "SELECT id FROM tags WHERE name = ?", (tag_name,))
            await db.execute(
                "INSERT OR IGNORE INTO page_tags (page_id, tag_id) VALUES (?, ?)",
                (page_id, tag_row["id"]),
            )

        # Parse and create links from content
        await _sync_links(db, page_id, data.content)

        await db.commit()
        return {"slug": data.slug, "id": page_id}
    except Exception as e:
        await db.rollback()
        if "UNIQUE constraint" in str(e):
            raise HTTPException(409, f"Page with slug '{data.slug}' already exists")
        raise
    finally:
        await db.close()


@router.put("/pages/{slug}")
async def update_page(slug: str, data: PageUpdate):
    """Update a page."""
    db = await get_db()
    try:
        row = await _fetchone(db, "SELECT id FROM pages WHERE slug = ?", (slug,))
        if not row:
            raise HTTPException(404, "Page not found")
        page_id = row["id"]

        # Update fields
        if data.title is not None:
            await db.execute("UPDATE pages SET title = ? WHERE id = ?", (data.title, page_id))
        if data.content is not None:
            await db.execute("UPDATE pages SET content = ? WHERE id = ?", (data.content, page_id))
            # Re-sync links
            await db.execute("DELETE FROM links WHERE source_id = ?", (page_id,))
            await _sync_links(db, page_id, data.content)

        if data.tags is not None:
            await db.execute("DELETE FROM page_tags WHERE page_id = ?", (page_id,))
            for tag_name in data.tags:
                await db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
                tag_row = await _fetchone(db, "SELECT id FROM tags WHERE name = ?", (tag_name,))
                await db.execute(
                    "INSERT OR IGNORE INTO page_tags (page_id, tag_id) VALUES (?, ?)",
                    (page_id, tag_row["id"]),
                )

        await db.execute(
            "UPDATE pages SET updated_at = datetime('now') WHERE id = ?", (page_id,)
        )
        await db.commit()
        return {"slug": slug, "updated": True}
    finally:
        await db.close()


@router.delete("/pages/{slug}")
async def delete_page(slug: str):
    """Delete a page."""
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM pages WHERE slug = ?", (slug,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Page not found")
        return {"deleted": True}
    finally:
        await db.close()


# ── Tags ──────────────────────────────────────────────────────────

@router.get("/tags")
async def list_tags():
    """List all tags with page counts."""
    db = await get_db()
    try:
        rows = await _fetchall(db,
            """
            SELECT t.name, COUNT(pt.page_id) as count
            FROM tags t
            LEFT JOIN page_tags pt ON t.id = pt.tag_id
            GROUP BY t.id
            ORDER BY count DESC, t.name
            """
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.get("/tags/{tag_name}/pages")
async def get_pages_by_tag(tag_name: str):
    """Get all pages with a specific tag."""
    db = await get_db()
    try:
        rows = await _fetchall(db,
            """
            SELECT p.slug, p.title, p.updated_at
            FROM pages p
            JOIN page_tags pt ON p.id = pt.page_id
            JOIN tags t ON pt.tag_id = t.id
            WHERE t.name = ?
            ORDER BY p.updated_at DESC
            """,
            (tag_name,),
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


# ── Journal ───────────────────────────────────────────────────────

@router.get("/journal/{date}")
async def get_journal(date: str):
    """Get journal entry for a specific date (YYYY-MM-DD)."""
    db = await get_db()
    try:
        row = await _fetchone(db, "SELECT * FROM journals WHERE date = ?", (date,))
        if not row:
            raise HTTPException(404, "No journal entry for this date")
        return dict(row)
    finally:
        await db.close()


@router.put("/journal/{date}")
async def update_journal(date: str, data: JournalUpdate):
    """Create or update a journal entry."""
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO journals (date, content, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(date) DO UPDATE SET content = excluded.content, updated_at = datetime('now')
            """,
            (date, data.content),
        )
        await db.commit()
        return {"date": date, "updated": True}
    finally:
        await db.close()


@router.get("/journal")
async def list_journal_dates():
    """List all dates that have journal entries."""
    db = await get_db()
    try:
        rows = await _fetchall(db,
            "SELECT date, updated_at FROM journals ORDER BY date DESC"
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


# ── Graph ─────────────────────────────────────────────────────────

@router.get("/graph")
async def get_graph():
    """Get the full link graph (nodes + edges)."""
    db = await get_db()
    try:
        pages = await _fetchall(db, "SELECT slug, title FROM pages")
        links = await _fetchall(db,
            "SELECT p1.slug as source, l.target_slug as target FROM links l JOIN pages p1 ON l.source_id = p1.id"
        )
        return {
            "nodes": [{"slug": p["slug"], "title": p["title"]} for p in pages],
            "edges": [{"source": l["source"], "target": l["target"]} for l in links],
        }
    finally:
        await db.close()
