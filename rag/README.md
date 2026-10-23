# RAG 服务

从 Tuling-Ai 项目迁入的 RAG API 与 MCP 代理基线。运行数据、真实环境配置和测试 PDF 不随代码提交。

从本目录运行：

```powershell
uv run --project rag_api_service python rag_api_service/run.py
uv run --project mcp_service python mcp_service/run.py
```

RAG 查询地址：`POST http://127.0.0.1:8011/api/docs/query`。MCP 地址：`http://127.0.0.1:8010/mcp`。
