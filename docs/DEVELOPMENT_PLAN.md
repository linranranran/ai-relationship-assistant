# AI 人际关系助手 — 开发计划

> 照着这份计划一步步做。每完成一步打 ✅，卡住了回头看提示或者找我。

---

## ⚠️ 开始之前

### 你的工作方式

1. **每一步都有一个明确目标** — 做到什么程度算完成，写得清清楚楚
2. **小步快跑** — 一个函数写完、跑通、再写下一个，不要攒
3. **卡住了找我** — 带上「文件 + 行号 + 报错信息 + 期望行为」
4. **做完一步打勾** — 打勾带来的成就感是坚持下去的动力

### 环境要求

- Python 3.11+
- Docker Desktop（跑 Neo4j）
- 一个 LLM API Key（DeepSeek 或 OpenAI 兼容）
- 你的 2核4G 云服务器（最后部署用，前面在本地开发）

---

## 第 0 步：环境准备

**目标：能跑通健康检查接口。**

### 0.1 启动 Neo4j

```bash
cd ai-relationship-assistant
docker compose up -d neo4j
```

验证：浏览器打开 http://localhost:7474，用 `neo4j` / `password` 登录。

### 0.2 创建 Python 虚拟环境

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Mac/Linux
source .venv/bin/activate
```

### 0.3 安装依赖

```bash
pip install -r requirements.txt
```

### 0.4 配置 .env

```bash
cp .env.example .env
```

编辑 `.env`，填入：
- `LLM_API_KEY`：你的 DeepSeek API Key
- `LLM_BASE_URL`：默认 `https://api.deepseek.com/v1`
- `EMBEDDING_API_KEY`：你的 Embedding API Key（DeepSeek 不支持 embedding，用 OpenAI 或其他）
- `EMBEDDING_BASE_URL`：默认 `https://api.openai.com/v1`
- `EMBEDDING_MODEL`：默认 `text-embedding-3-small`

### 0.5 跑起来

```bash
python -m backend.main
```

打开 http://localhost:8000/api/health，看到 `{"status": "ok"}` 就成功了。

### ✅ 第 0 步完成标志

- [ ] Neo4j 在 Docker 中运行，能登录 Web UI
- [ ] `pip install` 无报错
- [ ] `/api/health` 返回 `{"status": "ok"}`

---

## 第 1 步：Neo4j 数据库层

**目标：实现 `backend/db/neo4j.py` 中的所有函数。**

文件位置：`ai-relationship-assistant/backend/db/neo4j.py`

### 1.1 先读一遍文件

打开 `backend/db/neo4j.py`，从头到尾读一遍。理解每个函数的签名、参数、返回值。

### 1.2 第一个函数：`create_person`

这是最难的一个，写完它就打开了局面。

**你需要做的事情：**

1. 生成一个唯一的 `person_id`（提示：`import uuid; str(uuid.uuid4())[:8]`）
2. 用 Neo4j Python driver 执行 Cypher 语句
3. 创建 Person 节点，把所有属性存进去
4. 返回创建的人物信息

**Cypher 模板：**

```python
with driver.session() as session:
    result = session.run("""
        CREATE (p:Person {
            id: $id,
            name: $name,
            gender: $gender,
            birth_year: $birth_year,
            occupation: $occupation,
            education: $education,
            hobbies: $hobbies,
            personality: $personality,
            tags: $tags,
            note: $note,
            owner: $owner_id,
            created_at: datetime()
        })
        RETURN p {.id, .name, .gender, .birth_year, .occupation,
                   .education, .hobbies, .personality, .tags, .note} AS person
    """, id=person_id, name=properties.get("name"), ...)
    return result.single()["person"]
```

**测试方法：** 在文件末尾临时加一段测试代码，直接运行 `python -m backend.db.neo4j`

```python
if __name__ == "__main__":
    driver = get_neo4j_driver()
    result = create_person(driver, {"name": "测试用户", "gender": "男"}, "user_test")
    print(result)
    driver.close()
```

### 1.3 按顺序实现剩余函数

| 序号 | 函数 | 难度 | 关键点 |
|------|------|------|--------|
| 1 | `create_person` | ⭐⭐ | `session.run()` + Cypher CREATE |
| 2 | `person_exists` | ⭐ | Cypher MATCH ... RETURN count |
| 3 | `find_person_exact` | ⭐ | MATCH ... WHERE p.name = $name AND p.owner = $owner_id |
| 4 | `update_person` | ⭐ | MATCH ... SET ...（注意 hobbies 是 list） |
| 5 | `add_relation` | ⭐⭐ | MATCH 两个节点，CREATE 关系边 |
| 6 | `get_relation_path` | ⭐⭐⭐ | `shortestPath` 函数，需解析路径 |
| 7 | `get_person_detail` | ⭐⭐ | 同时查节点属性 + 关系 |
| 8 | `list_person_relations` | ⭐ | MATCH ... RETURN 关系 |
| 9 | `find_person_fuzzy` | ⭐⭐ | CONTAINS 或 `=~` 正则 |
| 10 | `delete_relation` | ⭐ | MATCH ... DELETE |
| 11 | `delete_person` | ⭐ | `DETACH DELETE` 同时删节点和边 |

### ✅ 第 1 步完成标志

- [x] 所有 11 个函数都能跑通
- [x] 每个函数都手动测试过，结果符合预期

---

## 第 2 步：ChromaDB 数据库层

**目标：实现 `backend/db/chromadb.py` 中的所有函数。**

文件位置：`ai-relationship-assistant/backend/db/chromadb.py`

### 2.1 `build_person_text`

把人物属性 dict 拼接成一句话。例如：

```python
{"name": "张三", "occupation": "金融", "hobbies": ["钓鱼", "茅台"]}
→ "张三，金融行业，爱好钓鱼、茅台"
```

**提示：** 跳过空值字段，hobbies 和 tags 用「、」连接。

### 2.2 `embed_and_store_person`

把一个已经生成好的 embedding 存入 ChromaDB。

```python
collection.add(
    ids=[person_id],
    embeddings=[embedding],
    documents=[text],
    metadatas=[{"person_id": person_id, "owner_id": owner_id}]
)
```

如果 person_id 已存在（更新场景），先 `collection.delete(ids=[person_id])` 再 `add`。

### 2.3 `semantic_search_person`

```python
results = collection.query(
    query_embeddings=[query_embedding],
    n_results=limit,
    where={"owner_id": owner_id}
)
```

把 ChromaDB 的返回格式转成我们的格式：`[{"person_id": ..., "similarity": ..., "text": ...}]`。

### 2.4 `delete_person_embedding`

一行代码：`collection.delete(ids=[person_id])`

### ✅ 第 2 步完成标志

- [ ] 能创建人物向量
- [ ] 能语义搜索，结果过滤正确
- [ ] 能删除向量

---

## 第 3 步：Tool 层 — 人物管理

**目标：实现 `backend/tools/person.py`，然后接入 registry。**

文件位置：`ai-relationship-assistant/backend/tools/person.py`

### 3.1 `add_person`

调用链路：
1. `backend.db.neo4j.create_person()` → 存 Neo4j
2. `backend.db.chromadb.build_person_text()` → 拼文本
3. `backend.services.embedding.embed_text()` → 生成向量
4. `backend.db.chromadb.embed_and_store_person()` → 存 ChromaDB

**注意：** `kwargs` 里会有 `owner_id`（Agent 层注入），你从 kwargs 中取。

### 3.2 `find_person`

根据 `search_mode` 分发：
- `"exact"` → `find_person_exact()`
- `"fuzzy"` → `find_person_fuzzy()`
- `"semantic"` → 先 embed_text(query)，再 semantic_search_person()

### 3.3 `update_person`

1. 调用 `backend.db.neo4j.update_person()`
2. 如果更新了会影响语义搜索的字段（name, occupation, hobbies 等），重新生成 embedding 更新 ChromaDB

### 3.4 `delete_person`

1. 如果 `confirmed != True`，返回 `{"needs_confirmation": True}`
2. 否则 → neo4j.delete_person() + chromadb.delete_person_embedding()

### 3.5 接入 registry

打开 `backend/tools/registry.py`，把上面的 import 取消注释，把 TOOL_REGISTRY 里的占位函数替换成真实函数。

```python
from backend.tools.person import add_person, update_person, delete_person, find_person

TOOL_REGISTRY = {
    "add_person": add_person,
    "update_person": update_person,
    # ...
}
```

### ✅ 第 3 步完成标志

- [ ] `add_person` 能创建人物 + 存入向量
- [ ] `find_person` 三种模式都通
- [ ] `update_person` 能更新 + 同步向量
- [ ] `delete_person` 确认机制工作正常
- [ ] `execute_tool("add_person", name="测试", owner_id="xxx")` 能跑通

---

## 第 4 步：Tool 层 — 剩余 Tools

**目标：实现所有 Tools，registry 完全可用。**

### 4.1 关系管理（`backend/tools/relation.py`）

三个函数：
- `add_relation` — 调用 `neo4j.add_relation()`
- `update_relation` — 调用 `neo4j.update_relation()`
- `delete_relation` — 调用 `neo4j.delete_relation()`

**记得接入 registry。**

### 4.2 查询（`backend/tools/query.py`）

三个函数：
- `query_relation_path` — 调用 `neo4j.get_relation_path()`，格式化路径
- `get_person_detail` — 调用 `neo4j.get_person_detail()`，同时查关系路径
- `list_relations` — 调用 `neo4j.list_person_relations()`

### 4.3 语义搜索（`backend/tools/search.py`）

- `search_by_semantic` — embed_text() → semantic_search_person() → 对每个结果查 Neo4j 获取姓名

### 4.4 亲属称谓（`backend/tools/kinship.py`）

这是最有意思的一个：

1. 把 `relation_path`（如 `["父亲", "弟弟"]`）拼成 `"父亲的弟弟"`
2. 在 `KINSHIP_RULES` 字典中查找
3. 命中 → `confidence="rule_match"`
4. 没命中 → 调用 LLM 推理 → `confidence="llm_inferred"`
5. 实现 `resolve_age_prefix`：根据排行把"叔叔"变成"三叔"

**LLM 推理 Prompt 参考：**

```
用户和目标人物的关系路径是：我 → 父亲 → 父亲的堂弟。
请告诉我，按照中国亲属称谓，用户应该怎么称呼这个人？
只需要回复称谓，如"堂叔"。
```

### ✅ 第 4 步完成标志

- [ ] 12 个 Tool 全部接入 registry
- [ ] `execute_tool(tool_name, **args)` 对每个 Tool 都能跑通
- [ ] 亲属称谓规则库覆盖常见关系
- [ ] LLM 兜底推理能给出合理称谓

---

## 第 5 步：Agent 层

**目标：实现 7 个 Agent 节点 + graph 编排。**

### 5.1 从最简单的开始：`generate_response`

文件：`backend/agent/nodes/generate_response.py`

- 格式化 `GENERATE_RESPONSE_PROMPT`
- 调用 `chat()` 
- 存入 `state["response"]`

**这是最简单的节点，先写它建立信心。**

### 5.2 `classify_intent` + `route_by_intent`

文件：`backend/agent/nodes/classify_intent.py`

- 格式化 `INTENT_CLASSIFY_PROMPT`，填入 history
- 调用 `chat()`，让 LLM 返回 `{"intent": "...", "confidence": 0.95}`
- 如果 confidence < 0.6，强制设为 `UNCLEAR`

`route_by_intent` 返回路由字符串：
- ADD_PERSON / UPDATE_PERSON → `"extract"`
- QUERY_* / DELETE_* / SEMANTIC_SEARCH → `"direct"`
- UNCLEAR → `"clarify"`
- CHITCHAT → `"chitchat"`

### 5.3 `extract_info`

文件：`backend/agent/nodes/extract_info.py`

- 用 `EXTRACT_INFO_PROMPT`，让 LLM 提取结构化信息
- 解析 JSON 存入 `state["extracted_info"]`

### 5.4 `plan_tasks`

文件：`backend/agent/nodes/plan_tasks.py`

- 把 `ALL_TOOL_DEFINITIONS` 转成 JSON 嵌入 prompt
- 让 LLM 返回 `{"tool_calls": [...], "needs_confirmation": false}`
- 存入 `state["tool_calls"]` 和 `state["needs_confirmation"]`

### 5.5 `execute_tools` + `route_after_execution`

文件：`backend/agent/nodes/execute_tools.py`

- 遍历 `state["tool_calls"]`
- 每个 call 调用 `execute_tool(tool_name, **args)`
- 自动往 args 里注入 `owner_id=state["user_id"]`
- 结果存 `state["tool_results"]`，错误存 `state["execution_errors"]`

`route_after_execution`：
- 有错误 + retry_count < 2 → `"retry"`
- needs_confirmation → `"confirm"`
- 其他 → `"respond"`

### 5.6 `ask_clarification` + `confirm_action`

文件：`backend/agent/nodes/ask_clarification.py` 和 `confirm_action.py`

这两个比较简单，都是格式化 prompt → 调 LLM → 存 response。

### 5.7 编排 graph

文件：`backend/agent/graph.py`

构建完整的 LangGraph 状态图：

```python
from langgraph.graph import StateGraph, END

def build_agent_graph():
    graph = StateGraph(AgentState)
    
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("extract_info", extract_info)
    graph.add_node("plan_tasks", plan_tasks)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("generate_response", generate_response)
    graph.add_node("ask_clarification", ask_clarification)
    graph.add_node("confirm_action", confirm_action)
    
    graph.set_entry_point("classify_intent")
    
    graph.add_conditional_edges("classify_intent", route_by_intent, {
        "extract": "extract_info",
        "direct": "plan_tasks",
        "clarify": "ask_clarification",
        "chitchat": "generate_response",
    })
    
    graph.add_edge("extract_info", "plan_tasks")
    graph.add_edge("plan_tasks", "execute_tools")
    
    graph.add_conditional_edges("execute_tools", route_after_execution, {
        "respond": "generate_response",
        "confirm": "confirm_action",
        "retry": "plan_tasks",
    })
    
    graph.add_conditional_edges("confirm_action", route_after_confirmation, {
        "execute": "execute_tools",
        "cancel": "generate_response",
    })
    
    graph.add_edge("ask_clarification", END)
    graph.add_edge("generate_response", END)
    
    return graph.compile()
```

### ✅ 第 5 步完成标志

- [ ] 每个节点都能独立测试
- [ ] graph 编译无报错
- [ ] graph.invoke(initial_state) 能跑通完整流程
- [ ] "今天见的张三，李四的朋友，做金融的" → 自动新增人物
- [ ] "张三是谁" → 返回人物信息
- [ ] "把张三删了" → 先确认再删除

---

## 第 6 步：API 路由

**目标：用户能通过 HTTP 接口和 Agent 对话。**

### 6.1 对话接口

文件：`backend/routers/chat.py`

实现 `POST /api/chat`：
1. 接收 `ChatRequest`
2. 构建 `AgentState` 初始状态
3. `agent_graph.invoke(initial_state)`
4. 返回 `ChatResponse`

### 6.2 认证接口

文件：`backend/routers/auth.py`

第一版做最简单的：
- `POST /api/auth/register` — 存用户名+密码，在 Neo4j 中创建"我"节点
- `POST /api/auth/login` — 验证用户名+密码，返回简单 token

### 6.3 注册路由

修改 `backend/main.py`，取消路由注册的注释。

### ✅ 第 6 步完成标志

- [ ] `POST /api/chat` 能用 curl 测试
- [ ] 能注册用户
- [ ] Swagger UI（`/docs`）能看到所有接口

---

## 第 7 步：前端

**目标：一个能用的 Web 界面。**

技术栈选你熟悉的：React 或 Vue。

### 7.1 页面清单

| 页面 | 设备 | 功能 |
|------|------|------|
| 登录/注册 | 两端 | 用户名+密码 |
| 对话主页 | 两端 | 聊天框 + 消息列表，和 Agent 对话 |
| 人物列表 | 电脑端 | 所有人物的列表，点击查看详情 |
| 人物详情 | 两端 | 属性、关系、关系路径 |
| 图谱可视化 | 电脑端 | 以自己为中心的关系网图 |

### 7.2 移动端适配

- 手机端只保留对话和查询功能
- 搜索框 + 结果卡片，极简设计
- CSS media query 做响应式

### 7.3 图谱可视化推荐

用 ECharts 或 D3.js 的关系图组件，数据从 `/api/chat` 返回结果中提取。

### ✅ 第 7 步完成标志

- [ ] 能登录
- [ ] 能对话
- [ ] 手机端能查称呼
- [ ] 电脑端能看到图谱

---

## 第 8 步：部署

**目标：部署到你的 2核4G 云服务器。**

### 8.1 服务器准备

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | sh

# 上传代码
scp -r ai-relationship-assistant user@your-server:/home/user/

# 启动
cd /home/user/ai-relationship-assistant
docker compose up -d
```

### 8.2 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location /api {
        proxy_pass http://127.0.0.1:8000;
    }
    
    location / {
        root /home/user/ai-relationship-assistant/frontend/dist;
        try_files $uri /index.html;
    }
}
```

### ✅ 第 8 步完成标志

- [ ] 手机能通过域名访问
- [ ] 所有功能在服务器上正常运行
- [ ] Neo4j 内存占用不超过 1.5G

---

## 进度总览

| 步骤 | 内容 | 状态 |
|------|------|------|
| 第 0 步 | 环境准备 | ✅ |
| 第 1 步 | Neo4j 数据库层（11 个函数） | ✅ |
| 第 2 步 | ChromaDB 数据库层（4 个函数） | ✅ |
| 第 3 步 | Tool 层 — 人物管理 | ✅ |
| 第 4 步 | Tool 层 — 剩余 Tools | ✅ |
| 第 5 步 | Agent 层（6 个节点，graph 待组装） | ✅ |
| 第 6 步 | API 路由 | ⬜ |
| 第 7 步 | 前端 | ⬜ |
| 第 8 步 | 部署 | ⬜ |

---

### 自我排查清单（先试这 3 个）

1. **看报错信息最后一行** — 90% 的问题答案就在报错里
2. **print 大法** — 在怀疑的地方 `print(variable)`，看值对不对
3. **简化复现** — 写一个 5 行的最小测试脚本，排除无关代码

### 找我时用这个模板

```
1. 我在做第 X 步的 XX 函数
2. 代码在 XX 文件第 YY 行
3. 报错信息是：（完整复制）
4. 我期望的行为是：
5. 我试过的方法：
```

---

> **记住：你不是在赶进度，你是在做一个属于自己的产品。**
> **每天写一点，保持节奏，每一步完成打勾的那一刻就是正反馈。**
> **做到哪一步都行——哪怕只做完第 1 步，你也已经学会了 Neo4j + Python。**