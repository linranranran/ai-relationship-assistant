"""把模型异常翻译成安全、可操作的提示，不把供应商原文直接返回浏览器。

连接失败没有供应商响应，不能推断为额度不足。429 也可能是限流或计费限制，
必须先看结构化错误码，再看 HTTP 状态；不同供应商的同名错误码含义可能不同。
本模块只分类，不重试、不修改检查点、不重新执行业务工具。
"""
from dataclasses import dataclass

from fastapi import HTTPException
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError


@dataclass(frozen=True)
class ModelFailure:
    code: str
    message: str
    retryable: bool  # False 表示先修复账户或配置，之后仍可继续原请求。
    http_status: int


class ModelServiceHTTPError(HTTPException):
    """HTTP detail 保持字符串兼容；批次层通过 failure 取得分类，避免二次覆盖。

    上游的 401/403 不代表当前用户登录失效，不能原样返回以触发前端退出登录。
    """
    def __init__(self, failure: ModelFailure):
        self.failure = failure
        super().__init__(status_code=failure.http_status, detail=failure.message)


def _failure(code: str, message: str, retryable: bool, http_status=503) -> ModelFailure:
    return ModelFailure(code, message + "\n继续时会复用已保存的成功步骤。", retryable, http_status)


def _provider_details(error: APIError) -> tuple[str, str, str]:
    """兼容 error.code 与 body.error.code；只读取元信息，不输出原始请求或响应。"""
    body = error.body if isinstance(error.body, dict) else {}
    if isinstance(body.get("error"), dict):
        body = body["error"]
    code = body.get("code") or body.get("Code") or error.code or body.get("type")
    message = body.get("message") or body.get("Message")
    return (code.casefold() if isinstance(code, str) else "",
            message.casefold() if isinstance(message, str) else "",
            error.request.url.host or "")


def _classify_api_error(error: APIError) -> ModelFailure:
    # APITimeoutError 继承 APIConnectionError，必须先判断超时。
    if isinstance(error, APITimeoutError):
        return _failure("MODEL_TIMEOUT", "模型服务响应超时，请稍后继续原任务。", True, 504)
    if isinstance(error, APIConnectionError):
        return _failure("MODEL_CONNECTION_ERROR", "无法连接模型服务，请检查后端网络或代理，恢复后继续原任务。", True)

    code, message, host = _provider_details(error)
    status = error.status_code if isinstance(error, APIStatusError) else None
    aliyun = host.endswith(".maas.aliyuncs.com") or host in {
        "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com",
    }
    billing_codes = {
        "arrearage", "isv.out_of_service", "allocationquota.freetieronly",
        "credit_balance_exhausted", "billing_hard_limit_reached",
        "project_spend_limit_exceeded", "organization_spend_limit_exceeded",
        "organization_usage_limit_exceeded", "usage_limit_exceeded", "prepaidbilloverdue",
    }
    # 阿里云免费额度用完和付费账户欠费都有明确错误码，不能把普通 403 当作欠费。
    free_quota_exhausted = aliyun and message.startswith("free allocated quota exceeded")
    if code in billing_codes or status == 402 or free_quota_exhausted:
        return _failure("MODEL_QUOTA_EXHAUSTED", "模型服务余额或可用额度不足，请检查服务商账户余额、免费额度及用量上限，处理后继续原任务。", False)

    # 百炼文本调用的 insufficient_quota / Throttling.AllocationQuota 可能是 TPS/TPM。
    # 依据：https://www.alibabacloud.com/help/en/model-studio/error-code
    # OpenAI 的同名 insufficient_quota 表示用量限制；未知供应商则不猜计费原因。
    if code == "insufficient_quota" and not aliyun:
        if host == "api.openai.com":
            return _failure("MODEL_QUOTA_EXHAUSTED", "模型服务可用额度不足，请检查服务商账户及用量限制，处理后继续原任务。", False)
        return _failure("MODEL_USAGE_LIMIT", "模型服务触发配额限制，请检查服务商的额度与限流设置，处理后继续原任务。", False)

    if status == 429:
        return _failure("MODEL_RATE_LIMITED", "模型调用过于频繁或 Token 速率受限，请稍后继续原任务；持续出现时请检查服务商的限流设置。", True, 429)
    if status == 401:
        return _failure("MODEL_AUTHENTICATION_FAILED", "模型服务鉴权失败，请检查后端 API Key 是否有效，以及是否与接口地址匹配，修复后继续原任务。", False)
    if status == 403:
        return _failure("MODEL_PERMISSION_DENIED", "没有当前模型的调用权限，请检查模型授权和 API Key 所属业务空间，修复后继续原任务。", False)
    if status in {408, 504}:
        return _failure("MODEL_TIMEOUT", "模型服务响应超时，请稍后继续原任务。", True, 504)
    if status is not None and status >= 500:
        return _failure("MODEL_SERVICE_UNAVAILABLE", "模型服务暂时不可用，请稍后继续原任务。", True)
    if status in {400, 404, 422}:
        return _failure("MODEL_REQUEST_INVALID", "模型服务拒绝当前请求，请检查模型名称、接口地址或调用参数，修复后继续原任务。", False, 502)
    return _failure("MODEL_CALL_FAILED", "模型调用失败，请查看后端日志定位原因，处理后继续原任务。", False, 502)


def classify_model_error(error: BaseException) -> ModelFailure | None:
    """沿明确的异常原因链查找模型错误，覆盖 HTTP 层包装后进入后台批次的场景。

    不根据任意异常文本中的“quota/token”等词猜原因，也不遍历隐式 context：
    处理模型异常时发生的新数据库故障，不能被误标成模型额度问题。
    """
    visited = set()
    while error is not None and id(error) not in visited:
        visited.add(id(error))
        if isinstance(error, ModelServiceHTTPError):
            return error.failure
        if isinstance(error, APIError):
            return _classify_api_error(error)
        error = error.__cause__
    return None
