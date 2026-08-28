"""Standalone MCP server 启动器。

独立进程模式：仅暴露 MCP 端点（streamable-http），不启动业务 API，便于
部署在隔离网络/权限环境，或由外部 MCP 客户端直接对接。默认端口 8099。

用法：
    python mcp_server.py            # 127.0.0.1:8099
    GEORANK_MCP_HOST=0.0.0.0 GEORANK_MCP_PORT=18090 python mcp_server.py
"""
import os

import uvicorn

from app.mcp.app import mcp_asgi_app


def main() -> None:
    host = os.environ.get("GEORANK_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("GEORANK_MCP_PORT", "8099"))
    uvicorn.run(mcp_asgi_app(), host=host, port=port)


if __name__ == "__main__":
    main()
