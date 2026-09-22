# AI 人际关系助手

## 当前开发入口（2026-09-21）

项目当前主线是：**客户记忆 + 有依据的沟通建议 + 常见亲属称谓**，用于 AI Agent 应用开发岗位的求职展示。

- [项目方向与一个月路线](docs/PROJECT_DIRECTION.md)：每天开工前阅读，明确目标、范围和验收标准。
- [目标代码框架](docs/REFACTOR_FRAMEWORK.md)：目录、模块职责、接口和迁移顺序。
- [30 天每日开发计划](docs/superpowers/plans/2026-09-21-30-day-development-schedule.md)：每天的编码、测试和交付目标。
- [代码审阅与整改清单](docs/CODE_REVIEW_2026-09-21.md)：依据当前源码记录的问题及优先级。
- [Agent 身份上下文契约](docs/AGENT_IDENTITY_CONTRACT.md)：你改造 Agent 时可直接依赖的 `owner_id`、`self_person_id` 和请求边界。
- [分步整改实施计划](docs/superpowers/plans/2026-09-21-project-remediation.md)：具体文件、接口、任务和测试案例。
- [带注释的学习骨架](docs/scaffolds/README.md)：由你完成函数实现，尚未接入运行中的应用。

以下保留最初版本的介绍和启动说明；旧需求、旧计划中的范围和完成状态如与上述文档冲突，以最新文档和实际验证结果为准。

用 AI 管理你的人际关系网络。再也不会叫不出名字、不知道该怎么称呼。

## 能做什么

- 🧠 **智能录入**：说一句话，AI 自动提取人物信息、建立关系
- 👨‍👩‍👦 **称谓推理**：自动计算中国亲属称呼——"你应该叫他三叔"
- 🔍 **语义搜索**：搜"谁喜欢钓鱼"，AI 帮你找到那个人
- 🕸️ **关系图谱**：以你为中心，可视化展示人际关系网
- 📱 **多端使用**：电脑端管理 + 手机端随时查询

## 技术栈

| 层 | 技术 |
|---|------|
| Agent 编排 | LangGraph |
| 图数据库 | Neo4j |
| 向量数据库 | ChromaDB |
| 后端框架 | FastAPI |
| LLM | DeepSeek / OpenAI 兼容 API |
| 前端（计划中） | React / Vue |

## 快速开始

### 1. 环境要求

- Python 3.11+
- Docker（用于运行 Neo4j）

### 2. 启动 Neo4j

```bash
docker compose up -d neo4j
```

访问 http://localhost:7474 确认 Neo4j 已启动（用户名 `neo4j`，密码 `password`）。

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 LLM API Key
```

### 5. 启动服务

```bash
python -m backend.main
```

访问 http://localhost:8000/docs 查看 API 文档。

## 项目结构

```
ai-relationship-assistant/
├── backend/
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 配置管理
│   ├── agent/                   # LangGraph Agent
│   │   ├── state.py             # 状态定义
│   │   ├── graph.py             # 状态图编排
│   │   ├── nodes/               # 各节点实现
│   │   │   ├── classify_intent.py
│   │   │   ├── extract_info.py
│   │   │   ├── plan_tasks.py
│   │   │   ├── execute_tools.py
│   │   │   ├── generate_response.py
│   │   │   ├── ask_clarification.py
│   │   │   └── confirm_action.py
│   │   └── prompts/             # System Prompt
│   ├── tools/                   # Tool 定义和实现
│   │   ├── definitions.py       # OpenAI Function Calling 格式
│   │   ├── registry.py          # Tool 注册表
│   │   ├── person.py            # 人物管理
│   │   ├── relation.py          # 关系管理
│   │   ├── query.py             # 查询
│   │   ├── kinship.py           # 亲属称谓
│   │   └── search.py            # 语义搜索
│   ├── db/                      # 数据库连接
│   │   ├── neo4j.py             # Neo4j 操作
│   │   └── chromadb.py          # ChromaDB 操作
│   ├── services/                # 外部服务
│   │   ├── llm.py               # LLM 调用
│   │   └── embedding.py         # Embedding 调用
│   ├── models/                  # 数据模型
│   │   └── schemas.py
│   └── routers/                 # API 路由
│       ├── chat.py
│       └── auth.py
├── frontend/                    # 前端（计划中）
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## 开发路线

- [ ] 数据库层：Neo4j Cypher 操作函数
- [ ] 数据库层：ChromaDB 向量操作函数
- [ ] Tool 层：12 个 Tool 的实现
- [ ] Agent 层：意图分类、信息提取、任务规划、执行、回复生成
- [ ] 路由层：对话接口、认证接口
- [ ] 前端：响应式 Web 界面
- [ ] 第二版：人脸识别

## License

MIT
