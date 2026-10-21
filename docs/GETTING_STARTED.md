# 本地运行指南

## 环境与依赖

- Python 3.12（项目声明至少3.11，当前验证环境为3.12）。
- Node.js 22.12+，本地构建使用Node 24。
- Neo4j 5、PostgreSQL，或安装Docker Compose后使用仓库中的本地数据库编排。
- LLM与Embedding的可用API。两者分别配置，不假定一个供应商支持全部接口。

推荐使用 `uv sync --locked` 安装Python锁定依赖；`requirements.txt`与项目声明保持一致，作为pip兼容入口，但不替代完整锁文件。

## 复制配置

PowerShell：

```powershell
Copy-Item .env.example .env
```

macOS / Linux：

```bash
cp .env.example .env
```

编辑 `.env`，按用途填写：

| 配置 | 用途 |
|---|---|
| `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL` | 对话、工具决策、结构化生成 |
| `EMBEDDING_*` | 人物资料的语义索引同步/搜索 |
| `NEO4J_*` | 客户档案、关系与登录身份 |
| `POSTGRES_*` | 业务账本、聊天、记忆与检查点 |
| `SECRET_KEY` | 自己生成的随机JWT签名密钥 |
| `AGENT_TOOL_STRATEGY=direct` | 当前销售沟通准备支持的入口 |
| `AGENT_CHECKPOINTER_BACKEND=postgres` | 可恢复HTTP/SSE服务必需 |
| `LANGFUSE_TRACING_ENABLED=false` | 默认不上传；启用前填写自己的项目凭据 |

可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成签名密钥，再手动填入配置。`.env`和前端本地配置受Git忽略，不提交真实密钥。

Qwen等供应商支持时，可明确设置 `LLM_ENABLE_THINKING=false`，降低思考过程挤占结构化响应预算的风险；其他供应商留空。兼容接口不代表全部Schema或strict能力一致。

## 本地数据库

`.env.example`默认指向localhost。为Neo4j和PostgreSQL设置本地开发密码后：

```bash
docker compose up -d neo4j postgres
docker compose ps
```

Compose只提供数据库，应用在本机运行。端口仅绑定回环地址；Neo4j原有绑定目录继续沿用，PostgreSQL使用Docker命名卷。

已有云数据库时不需要启动容器，直接修改连接配置。数据库账号需要建表/迁移权限；初始化脚本仅在目标库不存在时尝试创建数据库，权限受限时请先创建目标库。

## 后端

```bash
uv sync --locked
uv run python -m backend.db.init_postgres
uv run python -m backend.main
```

若使用pip，请先创建并激活Python3.12虚拟环境，再运行 `python -m pip install -r requirements.txt`，后续命令去掉 `uv run`。

- API文档：<http://localhost:8000/docs>
- 存活/基础运行信息：<http://localhost:8000/api/health>。它不是所有依赖的完整健康认证。
- Neo4j控制台：<http://localhost:7474>，使用自己的本地配置登录。

`init_postgres`执行 `backend/db/migrations/` 的当前迁移；Checkpointer自己的表由运行时setup管理。根目录没有要执行的MySQL初始化脚本。

## 前端与第一次体验

```bash
cd frontend
npm ci
npm run dev
```

默认连接 `http://localhost:8000/api`。需要改地址时复制 `frontend/.env.example` 为 `frontend/.env.development.local`。`VITE_*`会进入浏览器，不能存后端密钥。

1. 注册一个虚构本地账号并登录。
2. 输入“我有个客户叫杨鑫，职业是采购负责人”，检查人物与关系结果；资料写入会使用Embedding。
3. 在客户沟通准备面板搜索并选择杨鑫，载入虚构示例聊天，点击导入。
4. 点击生成准备单，检查需求/约定和“查看依据”。
5. 修改客户备注，体验确认、取消、刷新后的历史恢复。

同名时需要选择，找不到客户时先补建资料。准备单仅说明本次可见资料，不推断未检索到的历史从未发生。

## 开发验证

以下配置用于离线检查，避免测试意外连接个人环境。每个终端单独设置，不要覆盖自己的 `.env`。

PowerShell：

```powershell
$env:LANGFUSE_TRACING_ENABLED='false'
$env:POSTGRES_HOST='127.0.0.1'
$env:POSTGRES_PORT='9'
$env:POSTGRES_POOL_TIMEOUT_SECONDS='1'
$env:NEO4J_URI='bolt://127.0.0.1:9'
$env:LLM_BASE_URL='http://127.0.0.1:9/v1'
uv run python -m unittest discover -s tests -p 'test_*.py'
```

macOS / Linux：

```bash
LANGFUSE_TRACING_ENABLED=false POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=9 \
POSTGRES_POOL_TIMEOUT_SECONDS=1 NEO4J_URI=bolt://127.0.0.1:9 \
LLM_BASE_URL=http://127.0.0.1:9/v1 \
uv run python -m unittest discover -s tests -p 'test_*.py'
```

```bash
uv run python -m compileall -q backend scripts
cd frontend
npm test
npm run lint
npm run build
```

这些检查不证明真实模型质量或数据库容灾。`examples/`部分脚本和 `scripts/evaluation/run.py` 会调用模型，单独阅读后再运行。

## 常见问题

- **启动时连不上PostgreSQL**：检查端口、密码、数据库名与迁移；检查点选择memory不能替代可恢复Web服务的postgres要求。
- **人物写入后报索引失败**：图数据可能已经提交，应按原request_id查询/重试，不重新创建同一操作；检查Embedding配置。
- **模型403/模型未开通**：核对供应商账号、业务空间和可用模型，不将供应商失败当作Agent正确率。
- **无法生成准备单**：确认Direct模式、客户归属和聊天导入；没有证据不会返回虚构的成功准备单。
- **流式连接断开**：前端按原请求查询与重连；不要生成新request_id冒充重试。
- **应用配置更改没有生效**：后端重新启动；前端 `VITE_*` 变更需要重启开发服务器或重新构建。
