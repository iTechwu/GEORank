"""MCP server 的 ASGI 托管辅助。

- mount_mcp_app(fastapi_app, path="/mcp")：把 MCP 端点挂到现有 FastAPI 应用；
- mcp_asgi_app()：返回独立的 ASGI 应用（供 uvicorn 单独跑 standalone MCP 进程）。

优先使用 FastMCP 的 streamable-http 现成 ASGI 应用；老版本 SDK 退回 SSE。
"""
from __future__ import annotations

from app.mcp.auth import McpAuthConfig, McpAuthMiddleware
from app.mcp.server import mcp


def mcp_asgi_app(auth_config: McpAuthConfig | None = None):
    """返回可被 uvicorn 直接运行的 ASGI 应用（streamable-http 或 SSE 回退）。"""
    if hasattr(mcp, "streamable_http_app"):
        app = mcp.streamable_http_app()
    elif hasattr(mcp, "sse_app"):
        app = mcp.sse_app()
    else:
        raise RuntimeError(
            "当前安装的 mcp SDK 不提供 streamable_http_app/sse_app；请升级到 mcp>=1.12"
        )
    return McpAuthMiddleware(app, auth_config)


def mount_mcp_app(fastapi_app, path: str = "/mcp"):
    """把 MCP 端点挂到现有 FastAPI 应用。"""
    fastapi_app.mount(path, mcp_asgi_app())
    return fastapi_app
