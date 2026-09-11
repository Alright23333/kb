"""Import a Logseq file-version graph into the KB.

Reads `journals/*.md` (name = normalized date) and `pages/**/*.md`
(name = relative path without extension), parses `[[links]]`, `#tags`,
and `key:: value` properties, and writes them into the KB database.

This is a full rebuild: the existing KB database is dropped first.
"""
import asyncio
import os
import re

import aiosqlite

from database import DB_PATH, init_db, get_db
from parse import parse as parse_content

LOGSEQ_DIR = os.environ.get("LOGSEQ_DIR", "/home/user/Documents/DocumentsNya")

# journals/2026_6_18.md -> name "2026-06-18" (month/day zero-padded)
JOURNAL_RE = re.compile(r"^(\d{4})_(\d{1,2})_(\d{1,2})\.md$")


def journal_name(filename: str) -> str | None:
    m = JOURNAL_RE.match(filename)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


async def _fetchone(db, query, params=()):
    cur = await db.execute(query, params)
    return await cur.fetchone()


async def import_page(db: aiosqlite.Connection, name: str, content: str) -> tuple[int, int, int]:
    """Insert or update a page and re-derive its refs + properties."""
    row = await _fetchone(db, "SELECT id FROM pages WHERE name = ? COLLATE NOCASE", (name,))
    if row:
        page_id = row["id"]
        await db.execute(
            "UPDATE pages SET content = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (content, page_id),
        )
    else:
        cur = await db.execute("INSERT INTO pages (name, content) VALUES (?, ?)", (name, content))
        page_id = cur.lastrowid

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

    return len(seen_links), len(seen_tags), len(sig["properties"])


def _reset_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        path = DB_PATH + suffix
        if os.path.exists(path):
            os.remove(path)


async def main() -> None:
    _reset_db()
    await init_db()
    db = await get_db()

    stats = {"pages": 0, "links": 0, "tags": 0, "props": 0}
    skipped: list[str] = []

    try:
        # Journals
        journals_dir = os.path.join(LOGSEQ_DIR, "journals")
        if os.path.isdir(journals_dir):
            for fn in sorted(os.listdir(journals_dir)):
                if not fn.endswith(".md"):
                    continue
                name = journal_name(fn)
                if not name:
                    skipped.append(f"journal/{fn}")
                    continue
                with open(os.path.join(journals_dir, fn), encoding="utf-8") as f:
                    content = f.read()
                n_links, n_tags, n_props = await import_page(db, name, content)
                stats["pages"] += 1
                stats["links"] += n_links
                stats["tags"] += n_tags
                stats["props"] += n_props

        # Pages (flat or hierarchical)
        pages_dir = os.path.join(LOGSEQ_DIR, "pages")
        if os.path.isdir(pages_dir):
            for root, _dirs, files in os.walk(pages_dir):
                for fn in sorted(files):
                    if not fn.endswith(".md"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fn), pages_dir)
                    name = os.path.splitext(rel)[0].replace(os.sep, "/")
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        content = f.read()
                    n_links, n_tags, n_props = await import_page(db, name, content)
                    stats["pages"] += 1
                    stats["links"] += n_links
                    stats["tags"] += n_tags
                    stats["props"] += n_props

        await db.commit()

        print("导入完成：")
        print(f"  页面: {stats['pages']}")
        print(f"  链接: {stats['links']}")
        print(f"  标签: {stats['tags']}")
        print(f"  属性: {stats['props']}")
        if skipped:
            print(f"  跳过 {len(skipped)} 个非日记命名的 journal 文件")
            for s in skipped[:20]:
                print(f"    - {s}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
