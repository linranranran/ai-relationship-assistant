# AI 人际关系助手 — 进度跟踪

> 最后更新：2026-09-18

---

## 总进度

```
第 0 步 ████████████ ✅ 环境准备
第 1 步 ████████████ ✅ Neo4j 数据库层（11 个函数）
第 2 步 ████████████ ✅ ChromaDB 数据库层（4 个函数）
第 3 步 ████████████ ✅ Tool 层 — 人物 + 关系 + 查询（13 个 Tool）
第 4 步 ████████████ ✅ Tool 层 — 亲属称谓推理
第 5 步 ████████████ ✅ Agent 层（6 个节点 + graph）
                ─────
第 6 步 ░░░░░░░░░░░░ ⬜ API 路由 + FastAPI
第 7 步 ░░░░░░░░░░░░ ⬜ 前端（React/Vue 响应式）
第 8 步 ░░░░░░░░░░░░ ⬜ 部署到云服务器
```

---

## 今日总结（2026-09-15）

### 完成的所有工作

#### Tool 层收尾
- ✅ **kinship.py** — 亲属称谓推理引擎完成
  - `INVERSE_RELATION`：20+ 种关系的反向映射表
  - `KINSHIP_RULES`：60+ 条规则，覆盖父系/母系/直系/配偶
  - `get_kinship_title`：方向归一化 → 规则匹配 → LLM 兜底
  - `resolve_age_prefix`：排行前缀（大/二/三/小）+ 叠字处理（叔叔→叔）
  - `_resolve_ambiguous_title`："堂哥/堂弟" 按性别消歧
- ✅ **neo4j_client.py**：`get_relation_path` 新增 `direction` 字段（forward/reverse）
- ✅ **query.py**：修复 `list_person_relations` 调用链，`relation_type` 传到底层
- ✅ **search.py**：删除，语义搜索统一走 `find_person(search_mode="semantic")`

#### Agent 层（6 个节点全部写完）

| 节点 | 文件 | 跳转方式 | 状态 |
|------|------|:--:|:--:|
| `classify_intent` | classify_intent.py | Command goto | ✅ |
| `extract_info` | extract_info.py | Command goto | ✅ |
| `ask_clarification` | ask_clarification.py | 终端（无跳转） | ✅ |
| `plan_tasks` | plan_tasks.py | Command goto | ✅ |
| `execute_tools` | execute_tools.py | Command goto | ✅ |
| `generate_response` | generate_response.py | 终端（无跳转） | ✅ |

**设计决策：**
- Agent 路由使用 **Command(update=state, goto="node")** 模式，不用 conditional edges
- `classify_intent` 空输入/低置信度/UNCLEAR → ask_clarification；EXTRACT_INFO → extract_info；PLAN_TASKS → plan_tasks；CHITCHAT → generate_response
- `extract_info` 无条件跳 plan_tasks，提取不出来由下游处理
- `execute_tools` 按需重试：业务错误不重试，临时错误重试一次；全部失败 → ask_clarification
- `plan_tasks` LLM 返回空或解析失败 → ask_clarification；正常 → execute_tools
- 所有 Tool 的 `owner_id` 由 `execute_tools` 自动注入，LLM 不感知

#### 跨文件一致性修复
- ✅ 删除 `search_by_semantic`：definitions.py、registry.py、system_prompts.py 全部同步
- ✅ `list_relations` → `list_person_relations`：definitions.py、registry.py 同步改名
- ✅ `pyproject.toml` 依赖修复：langgraph 1.x → 0.2.61，chromadb → 0.6.3，补全 fastapi/langchain 等缺失依赖

#### 依赖安装踩坑
- `tokenizers` 的 `pyproject.toml` 缺少 `version` 字段导致 uv 编译失败
- 解决：删掉 pin，让 chromadb 自动拉取兼容版本的预编译 wheel

### 今日最重要的认知收获

**Command 模式 vs Conditional Edges：** Agent 图中 6 个节点，`classify_intent`、`plan_tasks`、`execute_tools` 用 Command 做多路跳转；`extract_info` 用 Command 做单路跳转；`ask_clarification` 和 `generate_response` 是终端节点，直接 return state。所有路由逻辑都在节点内部，graph.py 只需 add_node + add_edge 定义默认拓扑。

**Agent 节点的"单行本职责"：** 每个节点只做一件事——classify_intent 只管分类、extract_info 只管提取、plan_tasks 只管规划、execute_tools 只管执行。节点自己不判断"信息够不够"，交给下游处理。这样每个节点可以独立测试、独立调试。

**依赖管理：** uv 读的是 pyproject.toml 不是 requirements.txt。两者的依赖列表必须同步，否则 uv sync 装的版本和预期不一致。

### 今日踩坑

1. **shortestPath 无向遍历中的反向关系** — 箭头指向的"父亲"逆着走 → "儿子"，**必须方向归一化**

   ```
   chatgpt建议我，设计一个Kinship Reasoning Engine，用户将Neo4j数据库中返回的关系进行梳理，给LLM标准化的关系，而不是让LLM来推算关系，随着关系越来越复杂，LLM的推算结果不可控。
   
   非常重要的结论：
   不要让 LLM 直接负责从 Neo4j 返回的路径箭头里“猜亲属关系”。
   正确的架构应该是：
   Neo4j 负责找事实 → Tool 负责把图结构标准化 → 关系推理层负责确定性推理 → LLM 负责理解用户问题、调用工具、组织最终回答。
   
   可以写在简历中的Kinship Reasoning Engine设计：
   版本 B：项目经历用（3 条 bullet）
   基于 Neo4j 构建人际关系图谱，针对图遍历中关系箭头方向与遍历方向不一致的问题，设计方向归一化算法，将逆向关系自动映射为正向语义
   构建 60+ 条亲属称谓规则表（覆盖三代直系/父系/母系），实现 O(1) 规则匹配；规则未命中时由 LLM 兜底推理，保证 100% 称谓覆盖率
   实现排行前缀系统（大/二/三/小），结合出生年份自动推导"三叔""小姑"等精确称呼
   
   为什么不建立双向关系：
   建立双向关系会引入三个问题：
   第一，数据冗余。方向相反的边本质上是同一条信息的两种表述。存两条边就是存了冗余数据。图里 500 个人、1000 条关系，双向存就变成 2000 条——翻倍了。
   第二，一致性陷阱。 双向意味着每一次写入都要执行两次操作，出错的概率直接翻倍。
   第三，维护噩梦。 加一条边要确保反向也加了，删一条要确保反向也删了，改一条要确保反向也改了。这些逻辑散落在每个增删改的代码里，时间长了必然会有遗漏。
   
   而我的方案是存一条边，在查询时实时归一化。图遍历返回路径时，我判断遍历方向是否与边的箭头一致——不一致就用反向映射表翻转语义。一次写入，一次查询，语义正确，没有一致性问题。
   ```

2. **`chat()` 参数签名** — 3 个必传参数 `(client, system_prompt, messages)`，不能只传 2 个

3. **叠字称谓 `{prefix}{title}` 双写** — "三" + "叔叔" → "三叔"（挤掉第一个字）

4. **OPTIONAL MATCH 无关系时返回 null map** — `collect(DISTINCT {...})` 需 `WHERE rel.relation_id IS NOT NULL`

5. **两个 `list_` 函数** — `list_relations` 和 `list_person_relations` 重复，现已合并为一个

---

## 明天要做（2026-09-16）

### 接入 registry（收尾）

把 `kinship.py` 接入 `registry.py`。

### 第 5 步（收尾）：graph.py

**文件：** `backend/agent/graph.py`（只有一个函数 `build_agent_graph`）

用 `add_node` + `add_edge` 把 6 个节点串成图。节点用 Command 自己路由，graph 只负责定义默认拓扑。

### 第 6 步：API 路由

**文件：** `backend/routers/chat.py` + `backend/main.py`

FastAPI 端点：接收用户消息 → 初始化 AgentState → 跑 graph → 返回 `state["response"]`。

---

## 今日学习（2026-09-17 · 纯复习 · 无代码提交）

### LangGraph 四大组件

| 组件 | 是什么 | 你项目中的对应 |
|------|------|------|
| State | 节点间传递数据的共享字典 | `AgentState` TypedDict（`backend/agent/state.py`） |
| Node | 处理业务逻辑的 Python 函数 | 6 个 node：classify_intent / extract_info / plan_tasks / execute_tools / generate_response / ask_clarification |
| Edge | 定义节点执行顺序 | `graph.add_edge("A", "B")` — Command 可动态覆盖 |
| Graph | State + Node + Edge 的组合体 | `build_agent_graph()` 返回值 |

**State 的实际流转：**

```
classify_intent     → state["intent"] = "ADD_PERSON"
extract_info        → state["extracted_info"] = {...}
plan_tasks          → state["tool_calls"] = [...]
execute_tools       → state["tool_results"] = [...]  + state["execution_errors"] = [...]
generate_response   → state["response"] = "已为你添加张三"
```

**State 的附加能力：**
- 保存公共数据（user_id、user_name 等所有节点要用到的字段）
- 控制最终返回数据（`generate_response` 节点可以选择性输出字段，过滤敏感信息不展示给用户）

### LangGraph vs LangChain 核心区别

| | LangChain AgentExecutor | LangGraph |
|------|------|------|
| 控制流 | 隐式（黑盒循环，代码不可干预） | 显式（你画图，每一步跳转由代码决定） |
| LLM 角色 | **唯一决策者**，控制整个循环 | **一个节点的参与者**，只在该节点内决策 |
| Tool 调用 | LLM 选 Tool → 自动执行 → 自动循环 | 你决定何时停、何时重试、何时跳转 |
| 错误处理 | Tool 报错塞回 prompt，LLM 自己想办法 | 可以在图中加入重试/降级/人工审核节点 |
| 人机交互 | 不支持（AgentExecutor 停不下来） | `interrupt` 暂停等用户输入 |
| 并行 | 不支持 | `Send` API 并行调用多个节点 |
| 持久化 | 无 | 内置 checkpoint，崩溃后从断点恢复 |

**LangGraph 的"异常处理"三层能力：**

1. **图级重试** — 某个 Tool 失败，回到 plan_tasks 重新规划，而不是硬着头皮继续（你的 `execute_tools` 已实现此逻辑）
2. **人机交互（Human-in-the-Loop）** — 删除等危险操作暂停等用户确认后再继续
3. **持久化恢复** — 自动存 checkpoint，服务重启后从断点继续，不需要从头跑

**一句话概括：LangChain 是"把 prompt 和 Tool 给 LLM，让它自己跑"；LangGraph 是"你画流程图，LLM 只是流程中的几个决策方块"。**

### 节点路由的两种方式

| 方式 | 路由逻辑在哪 | 你项目的选择 |
|------|------|:--:|
| Conditional Edges | 图定义文件（graph.py） | ❌ |
| Command(update, goto) | 节点函数内部 | ✅ |

**你选 Command 的收益：** 每个节点的决策和功能写在同一个函数里，不需要跳回 graph.py 搜路由配置。6 个节点中 4 个用 Command 做跳转，2 个是终端节点（直接 return state）。

### 昨天实际开发 vs 今天纯复习

昨天（9-15）一口气写完 6 个 Agent 节点，但当时对 LangGraph 的理解是"边写边试"。今天系统性地回顾了这四个核心概念后，**昨天写的每个设计决策都能在框架层面找到依据了**——为什么空输入要跳 ask_clarification、为什么 execute_tools 内部做重试而不是外部条件边、为什么终端节点只需 return state。

---

## 今日学习（2026-09-18 · 纯学习 · 无代码提交）

### 1. Map-Reduce 并行模式

使用 `Send` API 实现类似"多线程"的方式同时执行某个或多个节点。LangGraph 提供 `Annotated` 类，可以用 `List` 的形式保存多个并行节点的返回值，框架自动合并。

**应用场景：** 当需要同时对多个人物做语义搜索，或多条查询路径并行计算时，可以分发到多个节点同时执行，收集结果后再汇总。

### 2. Command 的三个作用

| 作用 | 说明 |
|------|------|
| `update` 更新 state | 和节点流转是原子操作，避免不一致 |
| 精确控制图执行 | 代码层面决定下一步跳转去向 |
| 人机交互 / 中断恢复 | 暂停等用户输入后从断点继续 |

**为什么用 Command.update 而不是直接操作 state？**

1. **原子性：** `Command(update=..., goto=...)` 中 state 的更新和节点路由是一个原子操作——要么同时生效，要么都不生效。直接写 `state["key"] = value` 再 `return Command(goto=...)` 的话，中间如果出异常，state 已经被部分污染了
2. **Map-Reduce 正确性：** 并行节点同时修改同一个 state 字段时，框架需要根据 `Annotated` 声明的合并策略（如 `operator.add`）来合并，直接 `state["list"].append(x)` 会绕过框架的合并逻辑，造成数据丢失或覆盖

### 3. Runtime 配置 & 递归次数限制

LangGraph 内置 `recursion_limit` 参数，默认值为 **25**。当图的循环次数超过这个阈值时抛出 `GraphRecursionError`。通过 runtime config 传入：

```python
graph.invoke(state, config={"recursion_limit": 50})
```

**你的项目：** `execute_tools` 失败后会跳转到 `ask_clarification`（终端节点），不会形成死循环。但如果后续加入"失败→重试→再失败→再重试"的循环边时，记得设置合理的上限。

### 4. 框架级重试 vs 代码级重试

LangGraph 在 `add_node` 时支持 `retry_policy`：

```python
graph.add_node("call_llm", call_llm, retry_policy=RetryPolicy(max_attempts=3))
```

| 对比维度 | 框架重试 `retry_policy` | 代码重试（你现在的做法） |
|------|:--:|:--:|
| 配置方式 | 声明式，加一个参数 | 命令式，手写 for 循环 |
| 粒度 | 整个节点作为一个重试单元 | 可以针对特定 code path 重试 |
| 区分错误类型 | ❌ 只能按异常类型过滤 | ✅ 可以判断"缺少参数不重试" |
| 状态回滚 | ❌ state 可能已被污染 | ✅ 你控制了什么时候改 state |
| 代码可读性 | 一行配置 | 10+ 行循环逻辑 |

**遗留疑问：** 哪种方式更适合你的项目？框架重试更适合"整个节点是纯函数、无副作用"的场景；代码重试更适合"Tool 链路中部分失败、需要选择性重试"的场景——你的 `execute_tools` 明显属于后者。

### 5. 子图（Subgraph）

一个完整的 LangGraph 图可以作为另一个图的节点来使用。有两种方式：

**方式一：作为节点注册（共享 State）**

```python
subgraph = build_subgraph()
parent.add_node("sub_process", subgraph)  # 子图和父图共用同一个 State
parent.add_edge("A", "sub_process")
parent.add_edge("sub_process", "B")
```

父子图共享 state，子图内部的节点可以直接读写父图的字段。

**方式二：代码中手动调用（State 隔离）**

```python
def my_node(state):
    sub_state = {"input": state["data"]}  # 创建一个新的、隔离的 state
    sub_result = subgraph.invoke(sub_state)
    state["output"] = sub_result
    return state
```

父子图 state 完全隔离，父图手动决定传什么进去、取什么出来。

**应用场景：** 你的项目中，如果未来"人脸识别处理"是一套独立的流程（上传图片→检测人脸→提取向量→匹配），可以把它封装成子图，作为主图的一个节点调用。主图不需要知道人脸识别内部的步骤，只需要拿到最终结果。

---

## 历史记录

### 2026-09-14

- ✅ 接入 registry：person.py 4 个 + relation.py 3 个 = 7 个 Tool 全部入册
- ✅ `registry.py` 删掉占位函数，`execute_tool()` 可以真正调用
- ✅ `get_person_detail` 函数 Tool + 测试完成
- ✅ 实现了 Neo4j `get_person_detail` 的 Cypher 改造，从多语句分开查询优化为单语句用 `collect(r)`
- 踩坑：`collect(r)` 返回值处理、PyCharm 测试文件中文字符串编码

### 2026-09-13

- ✅ `backend/tools/relation.py` — 3 个 Tool 全部实现
  - `add_relation` — 参数校验 → neo4j_client.add_relation()
  - `update_relation` — 查旧边 → 合并新旧值 → 删旧建新
  - `delete_relation` — 带 owner_id 校验，只能删自己的关系
- ✅ `neo4j_client.py` 新增：`get_relation_by_id`、`update_relation`
- ✅ `delete_relation` 加入 `WHERE r.owner = $owner_id`，防止越权删除
- ✅ `get_relation_by_id` 修复：查不到时返回 None 而不是炸 AttributeError
- ✅ 讨论了知识图谱多跳推理（方案 C）的企业级实现，可写入简历
- 踩坑：`update_relation` 参数顺序反了、死检查、反关系自动推导要不要做

### 2026-09-12

- ✅ `backend/tools/person.py`：4 个 Tool 全部实现
  - `add_person` — 创建人物 + 同步向量，含补偿回滚
  - `update_person` — 按需重刷向量（`EMBEDDING_FIELDS` 判断），合并新旧数据
  - `delete_person` — 确认机制 + 双库同步删除
  - `find_person` — exact / fuzzy / semantic 三种模式路由
- ✅ `note` 排除出 embedding 字段（噪音 + 误匹配风险）
- ✅ 接入 logging，替换 print
- ✅ `tool_response` 统一返回格式
- 学到：操作顺序决定可靠性、补偿回滚、一个 try vs 碎 try、跨库无真正原子性

### 2026-09-11

- ✅ VM 部署 Neo4j Docker，解决磁盘满 + 镜像拉取问题
- ✅ 配置重写：pydantic-settings → python-dotenv + os.getenv
- ✅ `backend/db/neo4j_client.py`：11 个函数全部实现
  - CREATE / MATCH / SET / DETACH DELETE / shortestPath
  - 动态 CQL 拼接、关系边操作
- ✅ `backend/db/chroma_client.py`：4 个函数全部实现
  - 属性文本拼自然语言、embedding 存取、语义搜索、向量删除
- ✅ 两个文件名冲突修复：`neo4j.py` → `neo4j_client.py`，`chromadb.py` → `chroma_client.py`
- 踩坑：磁盘满、shell 吃引号、`query` 参数名冲突、`.env` 路径问题