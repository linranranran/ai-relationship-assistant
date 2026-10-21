# 独立学习示例

这些脚本从早期 `tests/` 中整理而来，不属于离线回归测试。部分在运行或导入时就会调用 `.env` 中的模型，请先阅读源码并确认费用。

| 模块 | 内容 |
|---|---|
| `function_calling.py` | 原生Function Calling与ToolPlan实验 |
| `multi_turn_chat.py` | 多轮消息实验 |
| `thinking_mode.py` | 模型思考模式实验 |
| `langgraph_streaming.py` | LangGraph流式事件实验 |
| `sse_server.py` | 独立SSE示例服务，使用8080端口 |
| `bm25_demo.py` | BM25教学示例；不代表产品已接入BM25检索 |

从项目根目录使用模块方式运行，例如 `uv run python -m examples.bm25_demo`。不要对本目录进行自动导入测试，也不要在生产环境运行这些实验。

产品回归检查位于[tests](../tests/)，真实模型评测位于[scripts/evaluation](../scripts/evaluation/README.md)。
