
# tool工具参数校验失败Error
class ToolPlanValidationError(ValueError):
    """保留内部异常，同时提供不含参数值的模型修复提示。"""

    def __init__(self, message, *, feedback=None, code="TOOL_PLAN_INVALID"):
        super().__init__(message)
        self.feedback = feedback or "请检查工具名、必填参数及依赖；只能引用排在前面的步骤。"
        self.code = code
