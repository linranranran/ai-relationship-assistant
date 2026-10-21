"""只读业务 Tool：取证据，不生成文本、不修改任何客户资料。"""

from backend.agent.execution_policy import ToolErrorCode
from backend.sales.context import build_sales_preparation_context
from backend.sales.importer import SalesPersonNotFound
from backend.tools.person import tool_response


def get_sales_preparation_context(**kwargs) -> dict:
    try:
        context = build_sales_preparation_context(owner_id=kwargs.get("owner_id", ""),
                                                  person_id=kwargs["person_id"],
                                                  meeting_goal=kwargs["meeting_goal"])
        return tool_response("已读取客户沟通证据", True, context.model_dump(mode="json"))
    except SalesPersonNotFound:
        return tool_response("客户不存在，请先查找或选择客户", False,
                             error_code=ToolErrorCode.NOT_FOUND)
    except (KeyError, ValueError):
        return tool_response("准备参数或证据窗口配置不合法", False,
                             error_code=ToolErrorCode.INVALID_ARGUMENT)
    except Exception:
        # 不把连接串或原文包装进错误消息；观察节点可按 TRANSIENT 等待原 call_id。
        return tool_response("客户沟通证据暂不可用，请稍后重试", False,
                             error_code=ToolErrorCode.TRANSIENT, retryable=True)
