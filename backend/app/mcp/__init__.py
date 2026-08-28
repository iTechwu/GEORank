"""GEOrank — MCP (Model Context Protocol) 服务模块。

把 GEOrank 的诊断、问答、拓词、方案、内容与工具能力，以符合 MCP 规范的
tools / resources 形式对外暴露，供 AI Agent（如 DSH）通过 `mcp__georank__*`
调用。该模块使用官方 `mcp` SDK 的 FastMCP 高层接口，复用后端现有 services 与
models，不复制业务逻辑。

两种托管方式（见 README）：

1. 内嵌到现有 FastAPI 应用的 `/mcp`（streamable-http），随 main.py 一起运行；
2. 独立进程（standalone MCP proxy / server），仅暴露 MCP 端点，用于隔离网络与权限。

对外工具命名统一为 `georank_<operation>`；DSH 通过 mcp client 插件绑定后，
agent 侧调用名会带上 `mcp__georank__` 前缀。
"""
