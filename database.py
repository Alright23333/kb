"""SQLite storage engine for the knowledge base.

Model: single-namespace "File-version" like Logseq.
- A page is identified by its `name` only (title = link target = URL key).
- Markdown content is the source of truth; this engine indexes it into
  refs (links + tags) and properties on every write.
"""
import os

import aiosqlite

DB_PATH = os.environ.get("KB_DB_PATH", "/home/user/services/kb/data/kb.db")

SCHEMA = """
-- Pages: `name` is the single unique key (title = link target = URL key).
CREATE TABLE IF NOT EXISTS pages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
    content    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Refs: unified table for both wiki-links and tags.
-- `kind` is 'link' for [[x]] and 'tag' for #x.
-- `target_name` may not exist as a page yet (unresolved link).
CREATE TABLE IF NOT EXISTS refs (
    source_id   INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    target_name TEXT NOT NULL COLLATE NOCASE,
    kind        TEXT NOT NULL DEFAULT 'link',
    PRIMARY KEY (source_id, target_name, kind)
);
CREATE INDEX IF NOT EXISTS idx_refs_target ON refs(target_name);

-- Properties: key:: value parsed from content (classification/query hooks).
CREATE TABLE IF NOT EXISTS properties (
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT,
    PRIMARY KEY (page_id, key)
);

-- Full-text search. trigram tokenizer indexes CJK n-grams; queries shorter
-- than 3 chars fall back to LIKE automatically by SQLite.
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    name, content,
    content='pages',
    content_rowid='id',
    tokenize='trigram'
);

-- Page versions: snapshot on every content change or delete.
-- Tracked by page NAME (not FK) so versions survive deletion/rename.
CREATE TABLE IF NOT EXISTS page_versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    page_name  TEXT NOT NULL COLLATE NOCASE,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_versions_name ON page_versions(page_name);

-- Snapshot previous content when it actually changes.
CREATE TRIGGER IF NOT EXISTS pages_version_on_update
AFTER UPDATE OF content ON pages
WHEN old.content != new.content
BEGIN
    INSERT INTO page_versions (page_name, content, created_at)
    VALUES (old.name, old.content, datetime('now', 'localtime'));
END;

-- Snapshot full page before deletion (enables restore).
CREATE TRIGGER IF NOT EXISTS pages_version_on_delete
BEFORE DELETE ON pages
BEGIN
    INSERT INTO page_versions (page_name, content, created_at)
    VALUES (old.name, old.content, datetime('now', 'localtime'));
END;

CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, name, content) VALUES (new.id, new.name, new.content);
END;

CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, name, content)
    VALUES ('delete', old.id, old.name, old.content);
END;

CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, name, content)
    VALUES ('delete', old.id, old.name, old.content);
    INSERT INTO pages_fts(rowid, name, content) VALUES (new.id, new.name, new.content);
END;
"""


async def get_db() -> aiosqlite.Connection:
    """Return a connection with sane pragmas."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db() -> None:
    """Create the schema if it does not exist yet."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
