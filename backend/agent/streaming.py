"""LangGraph 流式适配层：框架事件 -> 安全的产品进度。

框架的 tasks 包含输入/结果，不能直接返回浏览器。这里只映射节点名、状态和
耗时；custom 只接受明确约定的工具进度/回复增量，不暴露规划 JSON 和推理内容。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

from langgraph.config import get_stream_writer
from backend.agent.request_recovery import interrupted_state

STAGE_LABELS = {
    "resolve_person_reference": "识别本轮涉及的人物",
    "prepare_context": "加载相关记忆和聊天上下文",
    "classify_intent": "开始分析用户意图",
    "extract_info": "提取人物和关系信息",
    "plan_tasks": "开始规划任务",
    "execute_tools": "开始执行工具",
    "observe_execution": "检查执行结果，决定下一步",
    "generate_response": "开始生成回复",
    "ask_clarification": "准备需要补充的问题",
    "request_confirmation": "准备操作确认信息",
    "finalize_memory": "保存本轮聊天记录",
}
TOOL_LABELS = {
    "find_person": "查询人物", "get_person_detail": "查询人物资料",
    "list_relations": "查询关系记录", "query_relation_path": "查询关系路径",
    "get_kinship_title": "计算亲属称谓", "add_person": "新增人物",
    "update_person": "修改人物资料", "delete_person": "删除人物",
    "add_relation": "新增关系", "update_relation": "修改关系", "delete_relation": "删除关系",
}
CUSTOM_TYPES = frozenset({"tool.started", "tool.finished", "tool.retrying", "tool.reused",
                          "response.started", "response.delta"})
_control = ContextVar("agent_execution_control", default=None)


class ExecutionCancelled(RuntimeError):
    """合作式取消信号：当前数据库调用完成后，在下一个边界停止。"""


@contextmanager
def execution_control(check):
    token = _control.set(check)
    try:
        yield
    finally:
        _control.reset(token)


def check_execution(*, force=False):
    # 由独立工作者提供检查函数；兼容原本直接调用节点的学习脚本和单元测试。
    check = _control.get()
    if check:
        check(force=force)


def emit_progress(event_type, **data):
    """使用官方 custom stream；离开图执行上下文时不做任何传输。"""
    if event_type not in CUSTOM_TYPES:
        raise ValueError("未定义的产品事件")
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"type": event_type, **data})


class StreamingGraph:
    """保留原 recovery.invoke 接口，内部改用 LangGraph stream。

    这样 run_or_recover 仍负责首次运行、None 恢复、Command(resume=...) 的选择；
    不为了增加 SSE 再实现一套检查点恢复算法。durability='sync' 保证每步完成
    后检查点提交，再进入下一步；最终完整回复以图状态和请求表为准。
    """

    def __init__(self, graph, publish):
        self.graph = graph
        self.publish = publish

    def get_state(self, config):
        return self.graph.get_state(config)

    def invoke(self, input, config):
        starts = {}
        for part in self.graph.stream(input, config=config,
                                      stream_mode=["tasks", "custom"], version="v2", durability="sync"):
            data = part["data"]
            if part["type"] == "custom":
                if isinstance(data, dict) and data.get("type") in CUSTOM_TYPES:
                    # 白名单防止意外把 Tool 参数、数据库原始结果、模型推理内容发出去。
                    allowed = {key: value for key, value in data.items() if key in {
                        "node", "step_id", "call_id", "message", "tool", "attempt",
                        "success", "latency_ms", "response_id", "delta"}}
                    self.publish(data["type"], **allowed)
            elif part["type"] == "tasks":
                name = data.get("name")
                if name not in STAGE_LABELS:
                    continue
                task_id = data["id"]
                # 官方 task-start 有 input，task-finish 有 result/error/interrupts。
                if "input" in data:
                    starts[task_id] = perf_counter()
                    self.publish("stage.started", node=name, message=STAGE_LABELS[name])
                else:
                    self.publish("stage.finished", node=name,
                                 message=STAGE_LABELS[name],
                                 payload={"latency_ms": int((perf_counter() - starts.pop(task_id, perf_counter())) * 1000),
                                          "outcome": "interrupted" if data.get("interrupts") else
                                          "failed" if data.get("error") else "completed"})
        return interrupted_state(self.graph.get_state(config))
