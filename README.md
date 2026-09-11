# Knowledge Base

A self-hosted knowledge base with SQLite backend, REST API, and web UI. Designed for both human editing and AI agent access.

## Features

- **Web UI** — Browser-based editing with live preview (CodeMirror 6 + markdown-it)
- **SQLite** — Single-file database, fast queries, easy backups
- **Bidirectional links** — `[[wiki-links]]` with automatic backlink tracking
- **Tags** — Many-to-many tagging with filtered views
- **Journal** — Daily notes with date-based navigation
- **Full-text search** — FTS5 with LIKE fallback for CJK characters
- **Knowledge graph** — Visualize page connections as an interactive graph
- **Slash commands** — `/` for formatting (headings, lists, code blocks, etc.)
- **Reference autocomplete** — `[[` triggers page search
- **REST API** — Full CRUD for pages, tags, journals, and graph data

## Quick Start

```bash
# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn aiosqlite

# Run
./run.sh
# or
uvicorn main:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080` in your browser.

## Project Structure

```
kb/
├── main.py              # FastAPI application & routes
├── database.py          # SQLite schema, connection, initialization
├── routers/
│   └── api.py           # REST API endpoints
├── templates/
│   └── index.html       # Single-page web UI
├── static/              # Static assets (if needed)
├── run.sh               # Startup script
├── data/
│   └── kb.db            # SQLite database (created on first run)
└── venv/                # Python virtual environment
```

## API Reference

### Pages

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pages` | List pages (`?tag=`, `?search=`, `?limit=`, `?offset=`) |
| `GET` | `/api/pages/{slug}` | Get page with tags, links, and backlinks |
| `POST` | `/api/pages` | Create page: `{"slug", "title", "content", "tags"}` |
| `PUT` | `/api/pages/{slug}` | Update page |
| `DELETE` | `/api/pages/{slug}` | Delete page |

### Tags

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tags` | List all tags with page counts |
| `GET` | `/api/tags/{name}/pages` | Get pages by tag |

### Journal

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/journal/{date}` | Get journal for date (YYYY-MM-DD) |
| `PUT` | `/api/journal/{date}` | Create/update journal |
| `GET` | `/api/journal` | List all journal dates |

### Graph

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/graph` | Full link graph (nodes + edges) |

## Configuration

- `KB_DB_PATH` — Database file path (default: `./data/kb.db`)
- Host/port configured in `run.sh` or via uvicorn arguments

## Tech Stack

- **Backend**: Python, FastAPI, aiosqlite
- **Frontend**: Vanilla JS, CodeMirror 6, markdown-it
- **Database**: SQLite with FTS5 full-text search

## License

MIT
