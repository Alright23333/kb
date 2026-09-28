"""Export KB data to Markdown files or JSON.

Usage:
  python export.py markdown [output_dir]    # each page → one .md file
  python export.py json [output_file]       # full dump → single .json
  python export.py zip [output_file]        # all .md files → zip archive
"""
import json
import os
import sqlite3
import sys
import zipfile
from datetime import datetime

DB_PATH = os.environ.get(
    "KB_DB_PATH",
    os.path.join(os.path.dirname(__file__), "data", "kb.db"),
)
DB_PATH = os.path.expanduser(DB_PATH)


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_all_pages():
    """Return list of dicts: name, content, created_at, updated_at, tags, links, properties."""
    conn = _db()
    try:
        pages = []
        for row in conn.execute("SELECT * FROM pages ORDER BY name"):
            page = dict(row)
            page["tags"] = [
                r["target_name"]
                for r in conn.execute(
                    "SELECT target_name FROM refs WHERE source_id=? AND kind='tag'",
                    (row["id"],),
                )
            ]
            page["links"] = [
                r["target_name"]
                for r in conn.execute(
                    "SELECT target_name FROM refs WHERE source_id=? AND kind='link'",
                    (row["id"],),
                )
            ]
            page["properties"] = {
                r["key"]: r["value"]
                for r in conn.execute(
                    "SELECT key, value FROM properties WHERE page_id=?",
                    (row["id"],),
                )
            }
            pages.append(page)
        return pages
    finally:
        conn.close()


def safe_filename(name):
    """Sanitize page name for filesystem."""
    return name.replace("/", "_").replace("\\", "_").replace(":", "-").replace("?", "").replace("*", "").replace('"', "'").replace("<", "(").replace(">", ")").replace("|", "_")


def export_markdown(output_dir):
    """Export each page as a .md file."""
    pages = get_all_pages()
    os.makedirs(output_dir, exist_ok=True)

    for p in pages:
        filename = safe_filename(p["name"]) + ".md"
        filepath = os.path.join(output_dir, filename)

        # Front matter with metadata
        fm_lines = ["---"]
        fm_lines.append(f"name: {p['name']}")
        fm_lines.append(f"created: {p['created_at']}")
        fm_lines.append(f"updated: {p['updated_at']}")
        if p["tags"]:
            fm_lines.append(f"tags: [{', '.join(p['tags'])}]")
        if p["properties"]:
            for k, v in p["properties"].items():
                fm_lines.append(f"{k}: {v or ''}")
        fm_lines.append("---\n")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(fm_lines))
            f.write(p["content"])

    print(f"Exported {len(pages)} pages to {output_dir}/")
    return len(pages)


def export_json(output_file):
    """Export full database as JSON."""
    pages = get_all_pages()
    conn = _db()
    try:
        refs = [
            dict(r)
            for r in conn.execute(
                "SELECT r.source_id, p.name as source_name, r.target_name, r.kind "
                "FROM refs r JOIN pages p ON p.id = r.source_id"
            )
        ]
    finally:
        conn.close()

    data = {
        "exported_at": datetime.now().isoformat(),
        "version": 1,
        "pages": pages,
        "refs": refs,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Exported {len(pages)} pages + {len(refs)} refs to {output_file}")
    return len(pages)


def export_zip(output_file):
    """Export all pages as .md files in a zip archive."""
    pages = get_all_pages()

    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in pages:
            filename = safe_filename(p["name"]) + ".md"
            fm_lines = ["---"]
            fm_lines.append(f"name: {p['name']}")
            fm_lines.append(f"created: {p['created_at']}")
            fm_lines.append(f"updated: {p['updated_at']}")
            if p["tags"]:
                fm_lines.append(f"tags: [{', '.join(p['tags'])}]")
            fm_lines.append("---\n")
            content = "\n".join(fm_lines) + p["content"]
            zf.writestr(filename, content)

    print(f"Exported {len(pages)} pages to {output_file}")
    return len(pages)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    fmt = sys.argv[1]
    if fmt == "markdown":
        out = sys.argv[2] if len(sys.argv) > 2 else "export_md"
        export_markdown(out)
    elif fmt == "json":
        out = sys.argv[2] if len(sys.argv) > 2 else "kb_export.json"
        export_json(out)
    elif fmt == "zip":
        out = sys.argv[2] if len(sys.argv) > 2 else "kb_export.zip"
        export_zip(out)
    else:
        print(f"Unknown format: {fmt}")
        print(__doc__)
        sys.exit(1)
