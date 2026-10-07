"""Main FastAPI application."""
import ipaddress
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from database import init_db, DB_PATH
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

# Peers allowed to set X-Forwarded-For. Only these can speak for another IP.
# - loopback: nginx on the host, or bare-metal uvicorn behind a local proxy
# - 172.16.0.0/12: Docker bridge gateways (host -> container port mapping)
# A request arriving directly from any other address has its XFF ignored,
# so a client cannot spoof the header to bypass the whitelist.
_TRUSTED_PROXY_CIDRS = os.environ.get(
    "KB_TRUSTED_PROXIES", "127.0.0.0/8,::1/128,172.16.0.0/12"
)
_TRUSTED_PROXY_NETS = [
    ipaddress.ip_network(c.strip()) for c in _TRUSTED_PROXY_CIDRS.split(",") if c.strip()
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


def _in_nets(ip: str, nets: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in nets)


def _client_ip(request: Request) -> str:
    """Real client IP.

    X-Forwarded-For is honored ONLY when the direct peer is a trusted proxy
    (localhost / Docker bridge). Otherwise the header is ignored and the peer
    address is used, so a direct client cannot spoof the whitelist.
    """
    peer = request.client.host if request.client else ""
    if _in_nets(peer, _TRUSTED_PROXY_NETS):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return peer


def _is_allowed(ip: str) -> bool:
    return _in_nets(ip, _ALLOWED_NETS)


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


# ── Static assets under a deployment prefix ─────────────────────
# nginx passes the prefix through untouched (proxy_pass without a trailing
# slash), so /kb/static/vendor/x.js arrives verbatim while StaticFiles only
# knows about /static. Rewrite the path before routing so the vendored assets
# resolve under both subdomain and path-prefix deployments, with no extra
# nginx location block and no duplicated file-serving logic.
@app.middleware("http")
async def strip_static_prefix(request: Request, call_next):
    path = request.scope.get("path", "")
    idx = path.find("/static/")
    if idx > 0:  # >0 means there is a prefix; /static/... is already correct
        trimmed = path[idx:]
        request.scope["path"] = trimmed
        request.scope["raw_path"] = trimmed.encode("utf-8")
    return await call_next(request)


app.mount("/static", StaticFiles(directory=static_dir), name="static")


# Route words that can occupy the first path segment — a request like
# /page/sw.js names a page, not the service worker.
_SPA_ROUTE_WORDS = {
    "api", "static", "page", "all", "graph", "todo", "journal", "tag", "edit", "new",
}


def _is_app_root_file(full_path: str, filename: str) -> bool:
    """True for '<filename>' or '<base>/<filename>' where <base> is the
    deployment prefix (e.g. /kb). Excludes '<known-route>/<filename>' so a
    page genuinely named e.g. 'sw.js' stays reachable."""
    if full_path == filename:
        return True
    parts = full_path.split("/")
    return (len(parts) == 2 and parts[1] == filename
            and parts[0] not in _SPA_ROUTE_WORDS)


def _assert_data_dir_writable() -> None:
    """Fail fast with an actionable message instead of a cryptic SQLite error.

    The container runs as a non-root uid (see Dockerfile USER) that must own
    the bind-mounted data directory. A mismatch otherwise shows up later as
    "attempt to write a readonly database" on the first save.
    """
    d = os.path.dirname(DB_PATH) or "."
    os.makedirs(d, exist_ok=True)
    probe = os.path.join(d, ".kb-write-probe")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise RuntimeError(
            f"数据目录不可写: {d} ({e})\n"
            f"  当前进程以 uid={os.getuid()} gid={os.getgid()} 运行，"
            f"需与挂载目录属主一致。\n"
            f"  容器部署请指定构建参数，例如：\n"
            f"    KB_UID=$(id -u) KB_GID=$(id -g) docker compose up -d --build"
        ) from e


@app.on_event("startup")
async def startup():
    _assert_data_dir_writable()
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

    # PWA assets must sit at the app root: a service worker's scope comes from
    # its own URL, and the manifest's start_url/scope resolve relative to the
    # manifest. nginx passes the deployment prefix through, so serve them for
    # any prefix here.
    if _is_app_root_file(full_path, "sw.js"):
        return FileResponse(
            os.path.join(static_dir, "sw.js"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )
    if _is_app_root_file(full_path, "manifest.webmanifest"):
        return FileResponse(
            os.path.join(static_dir, "manifest.webmanifest"),
            media_type="application/manifest+json",
        )

    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(template_path, "r") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-cache"})
