"""Rebuild the refs/properties index from page content, and purge orphans.

Why this exists
---------------
`refs` (links + tags) and `properties` are a *derived* index: the markdown
content is the source of truth. The index is refreshed per page on write
(`_sync` in routers/api.py, `_sync_refs` in mcp_server.py), so it can drift
whenever the parser changes — old rows keep whatever the old rules produced
until that page is edited again.

Drift is not cosmetic:

  * stale tags linger in the sidebar long after the text stopped using them
  * a *stricter* parser silently loses tags that the old one accepted, and
    the page keeps showing the old tag until it is re-saved
  * deleted pages leave orphan refs behind (see PRAGMA foreign_keys in
    mcp_server._db) — those still show up in /api/tags with no page behind
    them, because the tag query does not join `pages`

This script recomputes the whole index from the current content and deletes
orphan rows, so the index always matches the parser.

Usage
-----
    python scripts/reindex.py --dry-run     # report only, change nothing
    python scripts/reindex.py               # apply
    python scripts/reindex.py --db /path/to/kb.db

The database path defaults to $KB_DB_PATH, then to the server default.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parse import parse as parse_content  # noqa: E402

DEFAULT_DB_PATH = os.environ.get(
    "KB_DB_PATH", "/home/user/services/kb/data/kb.db"
)


def _dedupe(names):
    """Case-insensitive dedup, keeping the first spelling seen.

    `refs.target_name` is COLLATE NOCASE, so "Tag" and "tag" would violate
    the primary key if both were inserted.
    """
    seen = {}
    for name in sorted(names):
        key = name.casefold()
        if key not in seen:
            seen[key] = name
    return list(seen.values())


def reindex(db_path: str, dry_run: bool = False) -> dict:
    """Rebuild refs + properties for every page. Returns a stats dict."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    stats = {
        "pages": 0,
        "tag_rows_before": 0,
        "tag_rows_after": 0,
        "link_rows_before": 0,
        "link_rows_after": 0,
        "tag_names_before": 0,
        "tag_names_after": 0,
        "orphan_refs": 0,
        "orphan_properties": 0,
        "orphan_tag_names": [],
    }
    try:
        def count(kind):
            return conn.execute(
                "SELECT COUNT(*) FROM refs WHERE kind = ?", (kind,)
            ).fetchone()[0]

        def distinct(kind):
            return conn.execute(
                "SELECT COUNT(DISTINCT target_name) FROM refs WHERE kind = ?", (kind,)
            ).fetchone()[0]

        stats["tag_rows_before"] = count("tag")
        stats["link_rows_before"] = count("link")
        stats["tag_names_before"] = distinct("tag")

        # Orphan rows: a ref whose source page is gone. These keep a tag
        # visible in the sidebar even though no page uses it.
        orphans = conn.execute(
            "SELECT r.kind, r.target_name FROM refs r "
            "LEFT JOIN pages p ON p.id = r.source_id WHERE p.id IS NULL"
        ).fetchall()
        stats["orphan_refs"] = len(orphans)
        stats["orphan_properties"] = conn.execute(
            "SELECT COUNT(*) FROM properties pr "
            "LEFT JOIN pages p ON p.id = pr.page_id WHERE p.id IS NULL"
        ).fetchone()[0]
        stats["orphan_tag_names"] = sorted(
            {r["target_name"] for r in orphans if r["kind"] == "tag"}
        )

        pages = conn.execute("SELECT id, name, content FROM pages").fetchall()
        new_tags = set()
        new_links = set()
        tag_rows = 0
        link_rows = 0

        for page in pages:
            sig = parse_content(page["content"] or "")
            links = _dedupe(sig["links"])
            tags = _dedupe(sig["tags"])
            new_links.update(t.casefold() for t in links)
            new_tags.update(t.casefold() for t in tags)
            tag_rows += len(tags)
            link_rows += len(links)

            if not dry_run:
                conn.execute("DELETE FROM refs WHERE source_id = ?", (page["id"],))
                conn.execute("DELETE FROM properties WHERE page_id = ?", (page["id"],))
                rows = [(page["id"], t, "link") for t in links]
                rows += [(page["id"], t, "tag") for t in tags]
                if rows:
                    conn.executemany(
                        "INSERT INTO refs (source_id, target_name, kind) VALUES (?, ?, ?)",
                        rows,
                    )
                props = list(sig["properties"].items())
                if props:
                    conn.executemany(
                        "INSERT INTO properties (page_id, key, value) VALUES (?, ?, ?)",
                        [(page["id"], k, v) for k, v in props],
                    )
            stats["pages"] += 1

        if not dry_run:
            # Drop rows whose source page no longer exists.
            conn.execute(
                "DELETE FROM refs WHERE source_id NOT IN (SELECT id FROM pages)"
            )
            conn.execute(
                "DELETE FROM properties WHERE page_id NOT IN (SELECT id FROM pages)"
            )
            conn.commit()

        stats["tag_rows_after"] = tag_rows
        stats["link_rows_after"] = link_rows
        stats["tag_names_after"] = len(new_tags)
        return stats
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB_PATH, help="path to kb.db")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing anything",
    )
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 1

    stats = reindex(args.db, dry_run=args.dry_run)
    mode = "DRY RUN (nothing written)" if args.dry_run else "APPLIED"
    print(f"reindex {mode}")
    print(f"  pages re-parsed:        {stats['pages']}")
    print(
        f"  tag rows:               {stats['tag_rows_before']} -> {stats['tag_rows_after']}"
        f"  (distinct names {stats['tag_names_before']} -> {stats['tag_names_after']})"
    )
    print(
        f"  link rows:              {stats['link_rows_before']} -> {stats['link_rows_after']}"
    )
    print(f"  orphan refs deleted:    {stats['orphan_refs']}")
    print(f"  orphan properties:      {stats['orphan_properties']}")
    if stats.get("orphan_tag_names"):
        print(f"  orphaned tags purged:   {stats['orphan_tag_names']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
