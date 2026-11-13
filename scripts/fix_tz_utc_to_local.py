"""Repair timestamps written while the container ran in UTC.

What happened
-------------
The KB stores *naive local* timestamps (`datetime('now','localtime')`) and the
front-end renders them as browser-local wall clock. The Docker container had
no TZ set, so SQLite resolved `localtime` to **UTC** while the host and the
browser are **Asia/Shanghai** — every timestamp written by the container is 8
hours behind, and the UI showed the wrong time ("八小时时差").

The host deployment that ran before the container wrote correct CST times, so
the database contains a *mix*: CST-era rows and UTC-era rows.

This script shifts the UTC-era rows by +8h so the whole database is CST.

How the era is decided
----------------------
Let T be the instant the container took over (in real/CST time). Then:

  * a CST-era row stores its own CST wall clock  -> stored <= T
  * a UTC-era row stores UTC wall clock          -> stored >= T - 8h

so a stored value is unambiguous outside the band (T-8h, T]:

  * stored >  T      -> certainly UTC-era  (a CST row could not be written
                        after the cutover)            -> shift +8h
  * stored <  T - 8h -> certainly CST-era  (a UTC row would imply a real
                        time before the cutover)      -> keep as is
  * in between        -> ambiguous, needs evidence

The cutover is a deployment event, so it is passed in explicitly
(`--cutover`) rather than guessed. The handful of rows inside the ambiguous
band are resolved from the nginx access log (which timestamps in CST) and
listed explicitly via `--shift-page`.

Usage
-----
    # 1. look at what would change (no writes)
    python scripts/fix_tz_utc_to_local.py --cutover "2026-09-26 23:27:49" --dry-run

    # 2. apply (take a backup first!)
    python scripts/fix_tz_utc_to_local.py --cutover "2026-09-26 23:27:49" \
        --shift-page 2026-09-26 --apply

The database path defaults to $KB_DB_PATH, then to the server default.
"""
import argparse
import os
import sqlite3
import sys

DEFAULT_DB_PATH = os.environ.get(
    "KB_DB_PATH", "/home/user/services/kb/data/kb.db"
)

SHIFT_HOURS = 8

# Timestamp columns that hold a wall clock written by the app.
PAGE_COLUMNS = ("created_at", "updated_at")


def shift(dt: str, hours: int = SHIFT_HOURS) -> str:
    """Shift a 'YYYY-MM-DD HH:MM:SS' string by N hours (SQLite semantics)."""
    sign = "+" if hours >= 0 else "-"
    conn = sqlite3.connect(":memory:")
    try:
        return conn.execute(
            "SELECT datetime(?, ?)", (dt, f"{sign}{abs(hours)} hours")
        ).fetchone()[0]
    finally:
        conn.close()


def _era(value: str, cutover: str, ambiguous_lo: str) -> str:
    """Classify one stored timestamp as 'utc', 'cst' or 'ambiguous'."""
    if value >= cutover:
        return "utc"
    if value >= ambiguous_lo:
        return "ambiguous"
    return "cst"


def fix(
    db_path: str,
    cutover: str,
    shift_pages: list[str],
    apply: bool = False,
) -> dict:
    """Shift UTC-era timestamps by +8h. Returns a report dict."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    forced = {p.casefold() for p in shift_pages}
    ambiguous_lo = shift(cutover, -SHIFT_HOURS)

    report = {
        "pages_created": 0,
        "pages_updated": 0,
        "versions": 0,
        "kept_cst": 0,
        "ambiguous": [],
        "samples": [],
    }

    def note(label: str, old: str, new: str) -> None:
        if len(report["samples"]) < 12:
            report["samples"].append(f"{label}: {old} -> {new}")

    try:
        pages = conn.execute(
            "SELECT id, name, created_at, updated_at FROM pages"
        ).fetchall()
        for page in pages:
            is_forced = page["name"].casefold() in forced
            updates = {}
            for col in PAGE_COLUMNS:
                value = page[col]
                era = _era(value, cutover, ambiguous_lo)
                if era == "utc" or (era == "ambiguous" and is_forced):
                    new_value = shift(value)
                    updates[col] = new_value
                    if col == "created_at":
                        report["pages_created"] += 1
                    else:
                        report["pages_updated"] += 1
                    note(
                        f"pages {page['name']}.{col}", value, new_value
                        + (" (evidence)" if era == "ambiguous" else "")
                    )
                elif era == "ambiguous":
                    report["ambiguous"].append(f"pages {page['name']}.{col} = {value}")
                else:
                    report["kept_cst"] += 1
            if apply and updates:
                conn.execute(
                    "UPDATE pages SET created_at = ?, updated_at = ? WHERE id = ?",
                    (
                        updates.get("created_at", page["created_at"]),
                        updates.get("updated_at", page["updated_at"]),
                        page["id"],
                    ),
                )

        versions = conn.execute(
            "SELECT id, page_name, created_at FROM page_versions"
        ).fetchall()
        for version in versions:
            value = version["created_at"]
            era = _era(value, cutover, ambiguous_lo)
            if era == "utc":
                report["versions"] += 1
                if apply:
                    conn.execute(
                        "UPDATE page_versions SET created_at = ? WHERE id = ?",
                        (shift(value), version["id"]),
                    )
            elif era == "ambiguous":
                report["ambiguous"].append(
                    f"page_versions {version['page_name']} = {value}"
                )

        if apply:
            conn.commit()
        return report
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB_PATH, help="path to kb.db")
    ap.add_argument(
        "--cutover",
        required=True,
        help="cutover instant in CST, 'YYYY-MM-DD HH:MM:SS'; "
        "stored values >= this are UTC-era",
    )
    ap.add_argument(
        "--shift-page",
        action="append",
        default=[],
        help="page name inside the ambiguous band that evidence proves is "
        "UTC-era (repeatable)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default: report only)",
    )
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 1

    report = fix(args.db, args.cutover, args.shift_page, apply=args.apply)
    print("tz repair " + ("APPLIED" if args.apply else "DRY RUN (nothing written)"))
    print(f"  pages.created_at shifted: {report['pages_created']}")
    print(f"  pages.updated_at shifted: {report['pages_updated']}")
    print(f"  page_versions shifted:    {report['versions']}")
    print(f"  timestamps kept (CST):    {report['kept_cst']}")
    for s in report["samples"]:
        print(f"    {s}")
    if report["ambiguous"]:
        print("  AMBIGUOUS (left untouched, needs evidence):")
        for a in report["ambiguous"]:
            print(f"    {a}")
    else:
        print("  ambiguous band: empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
