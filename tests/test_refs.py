"""Integration tests for the refs/properties index behaviour.

These exercise the DB-level rules that the bug report touched:
  * deleting a page must not leave orphan refs (unused tags)
  * renaming a page must not rewrite *tag* refs (phantom tags)
  * the tag list must not count orphans
  * reindex must rebuild the index from content

Run with:  python -m unittest discover -s tests -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parse import parse as parse_content


def _schema():
    """Read SCHEMA out of database.py without importing it.

    database.py pulls in aiosqlite, which is a runtime dependency — not
    necessarily installed where the tests run. The schema is plain SQL, so
    it is read straight from the source file.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "database.py"), encoding="utf-8") as fh:
        source = fh.read()
    start = source.index('SCHEMA = """')
    end = source.index('"""', start + len('SCHEMA = """'))
    return source[start + len('SCHEMA = """'):end]


def _seed(conn):
    conn.executescript(_schema())


def _add_page(conn, name, content):
    cur = conn.execute(
        "INSERT INTO pages (name, content) VALUES (?, ?)", (name, content)
    )
    return cur.lastrowid


def _sync_refs(conn, page_id, content):
    """Mirror of the API's _sync: delete-then-insert."""
    sig = parse_content(content)
    conn.execute("DELETE FROM refs WHERE source_id = ?", (page_id,))
    conn.execute("DELETE FROM properties WHERE page_id = ?", (page_id,))
    seen = {}
    for t in sig["tags"]:
        seen.setdefault(t.casefold(), t)
    for t in seen.values():
        conn.execute(
            "INSERT INTO refs (source_id, target_name, kind) VALUES (?, ?, 'tag')",
            (page_id, t),
        )
    seen = {}
    for t in sig["links"]:
        seen.setdefault(t.casefold(), t)
    for t in seen.values():
        conn.execute(
            "INSERT INTO refs (source_id, target_name, kind) VALUES (?, ?, 'link')",
            (page_id, t),
        )


class TestForeignKeys(unittest.TestCase):
    """A deleted page must not leave refs behind."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        _seed(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_delete_cascades_refs(self):
        self.conn.execute("PRAGMA foreign_keys=ON")
        pid = _add_page(self.conn, "A", "#tag [[B]]")
        _sync_refs(self.conn, pid, "#tag [[B]]")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0], 2
        )
        self.conn.execute("DELETE FROM pages WHERE name = 'A'")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0], 0,
            "orphan refs left behind after deleting a page",
        )

    def test_without_fk_orphans_appear(self):
        # Documents the bug: without the pragma, SQLite ignores
        # ON DELETE CASCADE and the rows survive.
        self.conn.execute("PRAGMA foreign_keys=OFF")
        pid = _add_page(self.conn, "A", "#tag")
        _sync_refs(self.conn, pid, "#tag")
        self.conn.execute("DELETE FROM pages WHERE name = 'A'")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0], 1,
            "expected orphans when foreign_keys is off",
        )


class TestRename(unittest.TestCase):
    """Renaming a page must only rewrite link refs, never tag refs."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        _seed(self.conn)
        self.conn.execute("PRAGMA foreign_keys=ON")

    def tearDown(self):
        self.conn.close()

    def test_rename_rewrites_link_refs_only(self):
        target = _add_page(self.conn, "画画", "content")
        src = _add_page(self.conn, "Source", "见 [[画画]] 和 #画画")
        _sync_refs(self.conn, src, "见 [[画画]] 和 #画画")

        old_name, new_name = "画画", "绘画-练习日志"
        self.conn.execute(
            "DELETE FROM refs WHERE target_name = ? COLLATE NOCASE AND kind = 'link' "
            "AND source_id IN (SELECT DISTINCT source_id FROM refs "
            "WHERE target_name = ? COLLATE NOCASE AND kind = 'link')",
            (old_name, new_name),
        )
        self.conn.execute(
            "UPDATE pages SET name = ? WHERE id = ?", (new_name, target)
        )
        self.conn.execute(
            "UPDATE refs SET target_name = ? WHERE target_name = ? COLLATE NOCASE "
            "AND kind = 'link'",
            (new_name, old_name),
        )

        kinds = dict(
            self.conn.execute(
                "SELECT target_name, kind FROM refs WHERE source_id = ?", (src,)
            ).fetchall()
        )
        self.assertEqual(kinds.get(new_name), "link", "link ref must be rewritten")
        self.assertEqual(
            kinds.get(old_name), "tag",
            "tag ref must NOT be rewritten — it comes from the content",
        )


class TestTagListQuery(unittest.TestCase):
    """The tag list must not count refs whose source page is gone."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        _seed(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_orphan_tag_not_counted(self):
        pid = _add_page(self.conn, "A", "#ghost")
        _sync_refs(self.conn, pid, "#ghost")
        # Simulate a delete that left the row behind (pre-fix behaviour).
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self.conn.execute("DELETE FROM pages WHERE name = 'A'")

        with_orphans = self.conn.execute(
            "SELECT target_name, COUNT(*) FROM refs WHERE kind = 'tag' "
            "GROUP BY target_name"
        ).fetchall()
        self.assertEqual(len(with_orphans), 1, "orphan row still present in refs")

        joined = self.conn.execute(
            "SELECT r.target_name, COUNT(*) FROM refs r "
            "JOIN pages p ON p.id = r.source_id WHERE r.kind = 'tag' "
            "GROUP BY r.target_name"
        ).fetchall()
        self.assertEqual(joined, [], "orphan tag must not appear in the tag list")


class TestReindex(unittest.TestCase):
    """reindex rebuilds refs from content and purges orphans."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        _seed(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_reindex_rebuilds_and_purges(self):
        import scripts.reindex as reindex  # noqa: E402

        pid = _add_page(self.conn, "A", "第12周#Monday [[B]]")
        _sync_refs(self.conn, pid, "第12周#Monday [[B]]")
        # Stale junk left by the old parser.
        self.conn.execute(
            "INSERT INTO refs (source_id, target_name, kind) VALUES (?, ?, 'tag')",
            (pid, "cookie"),
        )
        # Orphan: a refs row whose source page is gone (what a delete made
        # before the foreign_keys pragma was enabled left behind).
        self.conn.execute(
            "INSERT INTO refs (source_id, target_name, kind) VALUES (?, ?, 'tag')",
            (9999, "unused"),
        )
        self.conn.commit()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            path = tmp.name
        try:
            # Copy the in-memory DB to disk so the script can open it.
            disk = sqlite3.connect(path)
            self.conn.backup(disk)
            disk.close()
            self.conn.close()

            stats = reindex.reindex(path, dry_run=True)
            self.assertEqual(stats["orphan_refs"], 1)
            self.assertEqual(stats["orphan_tag_names"], ["unused"])
            self.assertEqual(stats["tag_rows_before"], 3)

            stats = reindex.reindex(path, dry_run=False)
            check = sqlite3.connect(path)
            tags = dict(
                check.execute(
                    "SELECT target_name, COUNT(*) FROM refs WHERE kind = 'tag' "
                    "GROUP BY target_name"
                ).fetchall()
            )
            check.close()
            self.assertIn("Monday", tags)
            self.assertNotIn("cookie", tags)
            self.assertNotIn("unused", tags)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
