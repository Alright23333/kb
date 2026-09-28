"""Main FastAPI application."""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from routers.api import router as api_router
import os

app = FastAPI(title="Knowledge Base", version="0.1.0")

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(api_router, prefix="/api")

# Static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
async def spa_catch_all(full_path: str):
    """SPA catch-all: serve index.html for any non-API GET.

    Supports both deployment styles:
    - Subdomain:  /page/Ideas          → base = ''
    - Path prefix: /kb/page/Ideas      → base = '/kb' (detected by frontend)
    nginx must pass the prefix through WITHOUT stripping:
        location /kb/ { proxy_pass http://kb:8080; }   # no trailing slash
    """
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(template_path, "r") as f:
        return f.read()
