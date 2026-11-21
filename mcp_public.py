"""KB MCP server — 公开只读子集（对外开放用）。

与 mcp_server.py 的关系：
  mcp_server.py    全量 17 个工具，读写皆可 —— 只给主人（AstrBot 里设 admin）
  mcp_public.py    8 个只读工具，SQL 层强制只返回「带 #<PUBLIC_TAG> 标签」的页面
                   —— 可以给任何人（AstrBot 里设 member）

安全模型（三层，缺一不可）：
  1. 工具集本身只读：这里没有任何 create/update/delete/rename 工具，
     所以即使被越权调用也写不进 KB。
  2. SQL 白名单：每个查询都追加
        p.id IN (SELECT source_id FROM refs WHERE kind='tag' AND lower(target_name)=?)
     页面没打 #public 标签就是查不到，不是靠 prompt 约束模型「不要看」。
  3. 不泄露存在性：查到非公开页面时统一返回 "Page 'x' not found"，
     不区分「不存在」和「存在但私有」，避免被当成存在性探测。

标签索引来自哪里：
  写入时 routers/api.py（网页端）和 mcp_server.py::_sync_refs（MCP 端）
  都会调用 parse.parse() 重算 refs，所以网页上给某页加上 #public 之后，
  本端点立刻生效，**不需要重启、也不需要重建索引**。

工具名一律带 public_ 前缀：
  AstrBot 的 FuncToolManager 以工具名做 key，同名会互相覆盖，
  所以不能直接复用 search/get_page 这些名字。

端口：容器内 8082（127.0.0.1:8098 由 compose 映射到宿主）。
"""
import json
import logging
import os
import sqlite3
import sys

# 允许从任意工作目录启动（容器里通常是 /app）
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
if not os.path.exists(os.path.join(_HERE, "search.py")):
    # 脚本被拷到别处自测时（例如 /tmp），仍要能 import 同目录模块
    for _cand in ("/app", os.getcwd()):
        if os.path.exists(os.path.join(_cand, "search.py")):
            sys.path.insert(0, _cand)
            break

from fastmcp import FastMCP
from search import build_search_query, rerank_by_name, make_snippet

import timeutil  # noqa: F401  — importing pins TZ

log = logging.getLogger("kb.mcp.public")

DB_PATH = os.environ.get("KB_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "kb.db"))
DB_PATH = os.path.expanduser(DB_PATH)

# 哪個标签算“公开”。改这里（或用 env）即可换标签名。
PUBLIC_TAG = os.environ.get("KB_PUBLIC_TAG", "public").strip().lower()

mcp = FastMCP("KB-public")

# 公开页面子查询。所有工具的 SQL 都必须带上它。
_PUBLIC_SUBQ = (
    "SELECT source_id FROM refs "
    "WHERE kind='tag' AND lower(target_name) = ?"
)


def _db():
    # 只读连接。三层保险：
    #   mode=ro      → SQLite 层拒绝对 db 的任何写入（会抛 readonly database）
    #   query_only   → 再挡一次，防止 mode=ro 以外路径意外写入
    # 为什么不能把 data 目录挂成 :ro：KB 用的是 WAL 模式，SQLite 打开 WAL
    # 库时必须能创建/写 -shm 共享内存文件，只读挂载会直接 "unable to open
    # database file"。所以目录得 rw，只读性在连接层强制。
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _is_public(conn: sqlite3.Connection, page_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM refs WHERE source_id=? AND kind='tag' "
        "AND lower(target_name)=? LIMIT 1",
        (page_id, PUBLIC_TAG),
    ).fetchone()
    return row is not None


@mcp.tool
def public_search(query: str, limit: int = 8) -> str:
    """Search the public subset of the note collection. Returns only pages
    that their author explicitly marked as public.

    This is the ONLY search tool available to you. If you need information
    that is not here, say so — do not guess.

    Args:
        query: Search query (supports CJK with jieba segmentation)
        limit: Max results (default 8)
    """
    conn = _db()
    try:
        fts_expr, like_patterns = build_search_query(query)
        rows = []

        if fts_expr:
            try:
                rows = conn.execute(
                    "SELECT p.name, p.updated_at, p.content FROM pages_fts f "
                    "JOIN pages p ON p.id = f.rowid "
                    "WHERE pages_fts MATCH ? "
                    f"AND p.id IN ({_PUBLIC_SUBQ}) "
                    "ORDER BY rank LIMIT ?",
                    (fts_expr, PUBLIC_TAG, limit),
                ).fetchall()
            except sqlite3.OperationalError as e:
                log.warning("FTS match failed for %r: %s — falling back to LIKE", fts_expr, e)

        if not rows and like_patterns:
            conditions = []
            params = []
            for pat in like_patterns:
                like = f"%{pat}%"
                conditions.append("(p.name LIKE ? OR p.content LIKE ?)")
                params.extend([like, like])
            params.extend([PUBLIC_TAG, limit])
            rows = conn.execute(
                f"SELECT p.name, p.updated_at, p.content FROM pages p "
                f"WHERE ({' OR '.join(conditions)}) "
                f"AND p.id IN ({_PUBLIC_SUBQ}) "
                "ORDER BY p.updated_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()

        if not rows:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT p.name, p.updated_at, p.content FROM pages p "
                "WHERE (p.name LIKE ? OR p.content LIKE ?) "
                f"AND p.id IN ({_PUBLIC_SUBQ}) "
                "ORDER BY p.updated_at DESC LIMIT ?",
                (like, like, PUBLIC_TAG, limit),
            ).fetchall()

        if not rows:
            return f"No public results for '{query}'"
        ranked = rerank_by_name([dict(r) for r in rows], query)
        return "\n".join(
            f"- {r['name']} (updated {r['updated_at']})\n"
            f"  > {make_snippet(query, r.get('content', ''))}"
            for r in ranked
        )
    finally:
        conn.close()


@mcp.tool
def public_get_page(name: str) -> str:
    """Get the full content of a PUBLIC page by exact name.

    Non-public pages return "not found" — do not treat that as a hint that
    the page exists privately.

    Args:
        name: Page name (case-insensitive)
    """
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM pages WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        # 统一话术：私有页面和不存在页面返回完全相同的内容
        if not row or not _is_public(conn, row["id"]):
            return f"Page '{name}' not found"

        tags = [r["target_name"] for r in conn.execute(
            "SELECT target_name FROM refs WHERE source_id=? AND kind='tag'",
            (row["id"],),
        ).fetchall()]
        # links 只列「目标也是公开页」的那些。否则这一行元数据会把
        # 私有页的名字泄露出去 —— 页面作者只授权了内容，没有授权
        # 它链向了谁。实测 树莓-小组进阶 的 links 曾列出 4 个私有页名。
        links = [r["target_name"] for r in conn.execute(
            "SELECT r.target_name FROM refs r "
            "JOIN pages t ON t.name = r.target_name COLLATE NOCASE "
            f"WHERE r.source_id=? AND r.kind='link' AND t.id IN ({_PUBLIC_SUBQ})",
            (row["id"], PUBLIC_TAG),
        ).fetchall()]
        # 反向链接只列公开来源，否则这条通道本身就成了泄露面
        backlinks = [dict(r) for r in conn.execute(
            "SELECT p.name, p.updated_at FROM refs r "
            "JOIN pages p ON p.id = r.source_id "
            f"WHERE r.kind='link' AND r.target_name=? COLLATE NOCASE "
            f"AND p.id IN ({_PUBLIC_SUBQ}) "
            "ORDER BY p.updated_at DESC",
            (name, PUBLIC_TAG),
        ).fetchall()]

        return json.dumps({
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "public": True,
            "tags": tags,
            "links": links,
            "backlinks": backlinks,
            "content": row["content"],
        }, ensure_ascii=False, indent=2)
    finally:
        conn.close()


@mcp.tool
def public_list_pages() -> str:
    """List every page marked public, alphabetically."""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT name FROM pages WHERE id IN ({_PUBLIC_SUBQ}) ORDER BY name",
            (PUBLIC_TAG,),
        ).fetchall()
        if not rows:
            return "No public pages"
        return "\n".join(r["name"] for r in rows)
    finally:
        conn.close()


@mcp.tool
def public_get_tags() -> str:
    """List tags that appear on public pages, with counts."""
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT r.target_name AS name, COUNT(*) AS count FROM refs r "
            "JOIN pages p ON p.id = r.source_id "
            f"WHERE r.kind='tag' AND p.id IN ({_PUBLIC_SUBQ}) "
            "GROUP BY r.target_name ORDER BY count DESC, r.target_name",
            (PUBLIC_TAG,),
        ).fetchall()
        if not rows:
            return "No tags on public pages"
        return "\n".join(f"#{r['name']} ({r['count']})" for r in rows)
    finally:
        conn.close()


@mcp.tool
def public_get_pages_by_tag(tag: str) -> str:
    """List public pages carrying the given tag.

    Args:
        tag: Tag name without # prefix (case-insensitive)
    """
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT p.name, p.updated_at FROM refs r "
            "JOIN pages p ON p.id = r.source_id "
            "WHERE r.kind='tag' AND r.target_name=? COLLATE NOCASE "
            f"AND p.id IN ({_PUBLIC_SUBQ}) "
            "ORDER BY p.updated_at DESC",
            (tag.strip().lstrip("#"), PUBLIC_TAG),
        ).fetchall()
        if not rows:
            return f"No public pages with tag '#{tag}'"
        return f"Public pages with #{tag}:\n" + "\n".join(
            f"- {r['name']} (updated {r['updated_at']})" for r in rows
        )
    finally:
        conn.close()


@mcp.tool
def public_get_recent_pages(limit: int = 10) -> str:
    """List recently updated PUBLIC pages.

    Args:
        limit: Max results (default 10)
    """
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT name, updated_at FROM pages "
            f"WHERE id IN ({_PUBLIC_SUBQ}) "
            "ORDER BY updated_at DESC LIMIT ?",
            (PUBLIC_TAG, limit),
        ).fetchall()
        if not rows:
            return "No public pages"
        return "\n".join(f"- {r['name']} (updated {r['updated_at']})" for r in rows)
    finally:
        conn.close()


@mcp.tool
def public_recently_changed(since: str, limit: int = 50) -> str:
    """List PUBLIC pages updated after a timestamp.

    Args:
        since: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' (local time)
        limit: Max results (default 50)
    """
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT name, updated_at FROM pages WHERE updated_at > ? "
            f"AND id IN ({_PUBLIC_SUBQ}) "
            "ORDER BY updated_at DESC LIMIT ?",
            (since.strip(), PUBLIC_TAG, limit),
        ).fetchall()
        if not rows:
            return f"No public pages changed since {since}"
        return f"Public pages changed since {since} ({len(rows)}):\n" + "\n".join(
            f"- {r['name']} (updated {r['updated_at']})" for r in rows
        )
    finally:
        conn.close()


@mcp.tool
def public_get_backlinks(name: str) -> str:
    """List PUBLIC pages that link to this page.

    Args:
        name: Target page name (case-insensitive)
    """
    conn = _db()
    try:
        target = conn.execute(
            "SELECT id FROM pages WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if not target or not _is_public(conn, target["id"]):
            return f"Page '{name}' not found"

        rows = conn.execute(
            "SELECT p.name, p.updated_at, p.content FROM refs r "
            "JOIN pages p ON p.id = r.source_id "
            "WHERE r.kind='link' AND r.target_name=? COLLATE NOCASE "
            f"AND p.id IN ({_PUBLIC_SUBQ}) "
            "ORDER BY p.updated_at DESC",
            (name, PUBLIC_TAG),
        ).fetchall()
        if not rows:
            return f"No public backlinks to '{name}'"
        from parse import find_link_lines
        lines = []
        for r in rows:
            snippet = (find_link_lines(r["content"], name, max_lines=1) or [""])[0]
            if snippet:
                lines.append(f"- {r['name']} (updated {r['updated_at']}): {snippet}")
            else:
                lines.append(f"- {r['name']} (updated {r['updated_at']})")
        return "\n".join(lines)
    finally:
        conn.close()


if __name__ == "__main__":
    host = os.environ.get("KB_PUBLIC_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("KB_PUBLIC_MCP_PORT", "8082"))
    path = os.environ.get("KB_PUBLIC_MCP_PATH", "/mcp/")
    log.info("public MCP starting, tag=%r db=%s", PUBLIC_TAG, DB_PATH)
    # stateless_http=True 的理由（2026-11-20 踩过的坑）：
    #   默认是有状态的 —— 服务端给每个客户端发一个 mcp-session-id，之后
    #   所有请求都要带这个 id，服务端查不到就回 404 "Session not found"。
    #   AstrBot 的 MCP 客户端只在 anyio.ClosedResourceError 时才重连，
    #   404 被包成 McpError("Session terminated") 不触发重连，
    #   于是**本容器一重启，AstrBot 的公开工具就永久 404**，必须重启 AstrBot。
    #   这个端点是纯只读、无会话状态，用 stateless 完全安全，
    #   而且重启后客户端自动恢复，不需要联动重启。
    mcp.run(
        transport="http",
        host=host,
        port=port,
        path=path,
        stateless_http=True,
    )
