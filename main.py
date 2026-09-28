"""Main FastAPI application."""
import ipaddress
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from routers.api import router as api_router

app = FastAPI(title="Knowledge Base", version="0.1.0")

# ── IP whitelist (Tailscale + localhost bypass) ────────────────
# Default: allow Tailscale CGNAT range + loopback. External IPs get 403.
# Behind nginx, the real client IP comes from X-Forwarded-For (trusted proxy).
# Disable with KB_IP_WHITELIST=0 (local dev). Customize with KB_ALLOWED_CIDRS.
IP_WHITELIST_ENABLED = os.environ.get("KB_IP_WHITELIST", "1") == "1"
_ALLOWED_CIDRS = os.environ.get(
    "KB_ALLOWED_CIDRS", "100.64.0.0/10,127.0.0.0/8,::1/128,fd7a:115c:a1e0::/48"
)
_ALLOWED_NETS = [
    ipaddress.ip_network(c.strip()) for c in _ALLOWED_CIDRS.split(",") if c.strip()
]

FORBIDDEN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>KB · 仅内网访问</title>
<style>
  body { font-family: system-ui, sans-serif; background: #1a1b26; color: #c0caf5;
        display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  .card { text-align: center; padding: 40px; }
  h1 { font-size: 22px; margin-bottom: 12px; }
  p { color: #565f89; font-size: 14px; line-height: 1.7; }
  code { background: #24283b; padding: 2px 8px; border-radius: 4px; }
</style>
</head>
<body>
  <div class="card">
    <h1>🔒 KB 仅允许内网访问</h1>
    <p>当前 IP 不在白名单内。<br>
    请通过 <code>Tailscale</code> 或本机访问本服务。<br>
    管理员可设置 <code>KB_ALLOWED_CIDRS</code> 调整白名单。</p>
  </div>
</body>
</html>"""


def _client_ip(request: Request) -> str:
    """Real client IP: X-Forwarded-For (behind nginx) or direct peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _is_allowed(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _ALLOWED_NETS)


@app.middleware("http")
async def ip_whitelist(request: Request, call_next):
    if not IP_WHITELIST_ENABLED:
        return await call_next(request)
    if _is_allowed(_client_ip(request)):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "仅允许内网（Tailscale/本机）访问"}, status_code=403)
    return HTMLResponse(FORBIDDEN_HTML, status_code=403)


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
