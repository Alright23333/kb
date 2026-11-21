"""KB MCP server over HTTP (Streamable HTTP transport).

同一个 FastMCP 实例，区别只在传输方式：
  stdio  → 每个 MCP 客户端都要 stdio 拉起一份 python 进程（本机一度有 4 份，各 ~80MB）
  http   → 一份 server 服务多个客户端，常驻内存只有一份

容器内跑，宿主通过 127.0.0.1:8081 访问（仅本机，tailnet 内其他机器需要走 nginx/SSH）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# mcp_server.py 在 import 时就会创建 FastMCP 实例并绑定 DB_PATH
from mcp_server import mcp  # noqa: E402

if __name__ == "__main__":
    host = os.environ.get("KB_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("KB_MCP_PORT", "8081"))
    path = os.environ.get("KB_MCP_PATH", "/mcp/")
    # stateless_http=True：默认有状态，服务端发的 mcp-session-id 在服务端
    # 重启后失效，客户端再带旧 id 就吃 404 "Session not found"。AstrBot 的
    # MCP 客户端只在 anyio.ClosedResourceError 时重连，404 被包成
    # McpError("Session terminated") 不触发重连 → 容器一重启，工具永久坏掉，
    # 必须连带重启 AstrBot。改 stateless 后客户端无需重连、互不牵连。
    mcp.run(
        transport="http",
        host=host,
        port=port,
        path=path,
        stateless_http=True,
    )
