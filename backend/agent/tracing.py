"""本地日志保留排障元数据；Langfuse 另存脱敏后的节点、模型和工具审计。"""

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from backend.agent.audit import audit_request, record_event, record_node_result

_trace = ContextVar("agent_request_trace", default={})
logger = logging.getLogger("agent.trace")


@contextmanager
def request_trace(request_id: str, thread_id: str, **audit_metadata):
    token = _trace.set({"request_id": request_id, "thread_id": thread_id})
    try:
        # 在实际执行线程进入上下文，后续 LangGraph 线程会继承它的回调/上下文。
        with audit_request(request_id, thread_id, **audit_metadata) as scope:
            if scope:
                _trace.set({**_trace.get(), "trace_id": scope["trace_id"]})
            yield
    finally:
        _trace.reset(token)


def trace_event(event: str, **metadata):
    """调用方只传 ID、状态、数量和耗时；禁止传原文、参数、工具结果及凭据。"""
    logger.info(
        json.dumps(
            {**_trace.get(), "event": event, **metadata},
            ensure_ascii=False,
            default=str,
        )
    )
    record_event(event, {**_trace.get(), **metadata})


def traced_node(name, node):
    """节点函数被包裹后仍保留签名，LangGraph 可正常注入状态参数。"""

    # name 是日志中的节点名称，例如 "classify_intent"。
    # node 是实际业务函数，例如 classify_intent；这里传函数本身，不是 node(state)。
    # 本方法在注册图节点时调用，创建并返回 wrapped；此时还不会执行业务逻辑。

    # 保留原函数的名称、文档、类型注解以及 __wrapped__ 等信息。
    # inspect 等工具可沿 __wrapped__ 获取原函数签名，便于框架识别状态参数和调试。
    # @wraps 不会调用 node，也不会让 wrapped 自动执行。
    @wraps(node)
    def wrapped(state):
        # wrapped 才是 LangGraph 运行到该节点时实际调用的函数。
        # state 是本次节点收到的图状态，包含请求 ID、目标、工具结果等。

        # 读取当前执行上下文已有的日志信息，例如 request_id、thread_id、trace_id。
        # _trace 是 ContextVar，不是所有请求共享的一份普通全局字典。
        parent = _trace.get()

        # 为当前执行上下文设置节点日志信息；set() 返回可恢复旧值的 token。
        # 这个 token 只是 ContextVar 的恢复凭据，不是模型 Token、JWT 或执行令牌。
        token = _trace.set(
            {
                # 复制父上下文的字段，保留请求级的 trace_id 等信息。
                # 后面同名字段会覆盖 parent 中的旧值，不会直接修改 parent 字典。
                **parent,
                # 后续 trace_event() 会自动带上当前节点名称。
                "node": name,
                # 优先取图状态里的请求 ID；只有键不存在时，才回退到父上下文。
                # dict.get(key, default) 不会把已经存在的 None 自动替换为 default。
                "request_id": state.get("request_id", parent.get("request_id")),
                # 同样补齐线程 ID，把本节点的日志关联到对应的检查点线程。
                "thread_id": state.get("thread_id", parent.get("thread_id")),
            }
        )

        # 记录高精度计时起点；perf_counter 用于计算耗时，不是业务时间戳。
        started = perf_counter()
        # 记录节点开始事件；trace_event 会读取刚才设置的上下文并记录审计事件。
        trace_event("node.start")

        # try 管理下面的检查和业务执行：正常返回走 else，抛异常走 except。
        # 一旦进入 try，离开它时还会执行 finally，恢复日志上下文。
        try:
            # 按需导入执行检查函数，避免这里在模块加载时就展开全部运行时依赖。
            from backend.agent.streaming import check_execution

            # 在节点开始前强制检查取消标志、执行权等条件。
            # 具体检查由执行工作者提供；没有注入检查函数时，此调用不做检查。
            # 这是协作式停止边界，不会强行中断已经执行中的数据库事务。
            check_execution(force=True)  # 节点边界重新读取取消标志，不强杀数据库事务。
            # budget_scope 是上下文管理器，负责挂载本节点的请求预算上下文。
            from backend.agent.budget import budget_scope

            # finalize_memory 是不调用模型的收尾节点。
            # 即使前面的任务已耗尽预算，也应允许它保存本轮消息和结束状态。
            if name == "finalize_memory":
                # 直接调用原业务函数，把它的返回值保存到 result。
                result = node(state)  # 无模型的收尾必须在预算耗尽后仍可执行。
            else:
                # 普通节点进入预算范围：复用请求级账本，检查并记录活跃执行时间。
                # 范围内的模型/工具调用仍需通过 reserve/settle 预占和结算消耗。
                # 退出 with 时会清理预算上下文；这不是包住整个节点的一条长数据库事务。
                with budget_scope(state, name):
                    # 图步数和 Token 预算是两种限制：这里检查 LangGraph 剩余执行步数。
                    # 只对 V2 生效；若 state 没有 remaining_steps，使用 64 作为兼容默认值。
                    if (
                        state.get("execution_version") == 2
                        and state.get("remaining_steps", 64) <= 5
                    ):
                        # 剩余步数不足时，保留收尾空间，不再启动新的业务步骤。
                        # 5 是本项目的安全阈值，不是每次工具调用需要 5 步的意思。
                        from backend.agent.budget import BudgetExceeded

                        # 抛出项目定义的停止信号，下面的 except 会把它转成收尾路由。
                        raise BudgetExceeded("图剩余执行步数不足，提前安全收尾")

                    # 真正执行节点的核心位置：这里才调用分类、提取、规划等业务函数。
                    # result 通常是 Command，也可能是返回给 LangGraph 的状态更新字典。
                    result = node(state)

        # BaseException 比 Exception 范围更大，还包含退出等控制信号。
        # 捕获后只处理明确认识的情况，其他异常和控制信号仍会重新抛出，不会吞掉。
        except BaseException as exc:
            # 在异常分支按需导入：用于识别预算停止、模型协议错误和图路由结果。
            from backend.agent.budget import BudgetExceeded
            from backend.agent.contracts import StructuredOutputError
            from langgraph.types import Command

            # 第一类：预算耗尽或剩余图步数不足，是可预期的停止，不应无限重试。
            if isinstance(exc, BudgetExceeded):
                # 记录预算停止事件，只记录事件类型和耗时等排障元数据。
                trace_event(
                    "node.budget_exceeded",
                    # 秒转换为毫秒并取整数，例如 0.123 秒记录成 123 毫秒。
                    latency_ms=int((perf_counter() - started) * 1000),
                )
                # Command.update 更新图状态；goto 指定接下来运行 finish_budget。
                # 它不是在此处直接调用 finish_budget，由 LangGraph 接收返回值后调度。
                result = Command(update={"budget_reason": str(exc)}, goto="finish_budget")
                # 审计记录节点的实际处理结果，并明确标记为预算停止。
                record_node_result(state, result, outcome="budget_exceeded")
                # 返回可处理的路由结果，不再把 BudgetExceeded 交给 RetryPolicy 重试。
                return result

            # 第二类：模型结构化输出或业务契约校验最终失败。
            # ModelBusinessValidationError 是它的子类，因此也会进入这里。
            if isinstance(exc, StructuredOutputError):
                from backend.agent.strategies.contracts import is_request_direct
                if is_request_direct(state):
                    # Direct 可能已有成功写入，不能用“没有开始操作”覆盖这些事实。
                    result = Command(update={"stop_reason": "MODEL_PROTOCOL_INVALID"}, goto="direct_finish")
                    record_node_result(state, result, outcome="validation_failed")
                    return result
                # 如果当前已选中一个目标，只结束这个目标；其他独立目标仍可继续调度。
                if state.get("active_goal_id"):
                    # 把失败原因放入 execution_errors，交给 finalize_goal 记录目标结果。
                    result = Command(
                        update={"execution_errors": ["模型输出两次未通过结构校验"]},
                        goto="finalize_goal",
                    )
                    # 记录校验失败的审计结果，区别于正常执行完成。
                    record_node_result(state, result, outcome="validation_failed")
                    # 返回后仍会执行 finally，然后 LangGraph 才运行目标收尾节点。
                    return result

                # 连分类都未通过时，不伪造目标、也不让前端无限重试缓存的坏输出。
                # 直接以明确的未完成终态收尾，不再花一次模型调用解释模型错误。
                # 没有 active_goal_id，说明无法按“当前目标失败”收尾，改为请求级收尾。
                result = Command(
                    update={
                        # 标记本次请求未能完整完成，供响应转换和状态记录使用。
                        "execution_decision": "PARTIALLY_COMPLETED",
                        # 使用固定文案，不再调用模型解释一次模型解析失败。
                        "response": "未能可靠解析本次请求，没有开始工具操作。请重新发起请求。",
                    },
                    # 转到保存消息和结束状态的节点，不进入业务工具执行链路。
                    goto="finalize_memory",
                )
                # 请求级解析失败同样作为 validation_failed 审计。
                record_node_result(state, result, outcome="validation_failed")
                return result

            # GraphInterrupt 是框架正常的暂停控制信号，不作为业务异常报警。
            # interrupt() 需要把信号交回 LangGraph，框架才能保存暂停位置并等待 resume。
            from langgraph.errors import GraphInterrupt

            # 其他异常只在这里分类记录：正常中断用 node.interrupt，故障用 node.failed。
            trace_event(
                "node.interrupt" if isinstance(exc, GraphInterrupt) else "node.failed",
                # 只记录异常类名，例如 APITimeoutError，不在此处输出完整客户参数。
                error_type=type(exc).__name__,
                latency_ms=int((perf_counter() - started) * 1000),
            )
            # 不带参数的 raise 重新抛出当前异常，并保留原始异常信息。
            # GraphInterrupt 由框架处理暂停；其他异常由框架判断是否符合 RetryPolicy。
            # 不符合重试条件或达到上限的系统故障继续向外传播，保留恢复的机会。
            raise

        # 只有 try 中没有抛异常时才进入 else，不是“所有异常都处理完后”都会进入。
        else:
            # 审计记录原节点返回的状态或 Command，不改变其业务结果。
            record_node_result(state, result)
            # node.finished 表示函数正常返回，不等于所有业务目标都已成功。
            # 例如原节点正常返回 Command(goto="ask_clarification")，也会记录 finished。
            trace_event(
                "node.finished", latency_ms=int((perf_counter() - started) * 1000)
            )
            # 原样返回业务结果；状态更新与下一步路由仍由 LangGraph 应用。
            return result

        # 正常 return、异常分支 return、重新 raise、中断都会经过这个 finally。
        finally:
            # 用进入节点时保存的 token 恢复旧上下文，而不是把上下文全部清空。
            # 这样离开当前节点后，日志不会继续沿用错误的 node 名称。
            _trace.reset(token)

    # 返回的是“包装后的函数对象”，没有括号，不是在这里执行 wrapped。
    # graph.add_node(...) 注册它，图运行到该节点时才调用 wrapped(state)。
    # wrapped 通过闭包记住外层的 name 和 node，知道应记录哪个节点、调用哪个函数。
    return wrapped
