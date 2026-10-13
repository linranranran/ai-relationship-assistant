"""Tool 依赖与结果引用解析。

第一版只允许引用已经排在前面的步骤，因此执行器可以稳定地按顺序运行，
不用让大模型决定并发调度。引用格式：

    {"$ref": {"step_id": "find_target", "path": "data.id"}}

``path`` 使用点号访问字典字段，数字片段访问列表下标。
"""

import re
from typing import Any

from backend.exception.AgentException import ToolPlanValidationError


REFERENCE_KEY = "$ref"
STEP_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def validate_known_result_contracts(calls: list[dict]) -> None:
    """拒绝已经知道错误的结果路径，不能为了定位参数错误先执行一次 Tool。

    只校验项目明确的返回形状，任意动态字段仍由执行器解析真实结果。
    这里拒绝而非自动改写，避免系统改变模型原本意图中的“第一个候选”。
    """
    sources = {c["step_id"]: c["tool"] for c in calls}
    def visit(value):
        if is_result_reference(value):
            step, path = _parse_reference(value)
            tool = sources.get(step)
            wrong_find = tool == "find_person" and re.match(r"^data\.\d+(?:\.|$)", path)
            wrong_detail = tool == "get_person_detail" and path in {"data.id", "data.name"}
            if wrong_find or wrong_detail:
                correct = "data.id" if wrong_find else "data.person.id / data.person.name"
                from backend.agent.contracts import ModelBusinessValidationError
                raise ModelBusinessValidationError("TOOL_RESULT_PATH_INVALID", f"{tool} 成功返回对象；请使用 {correct}，不能假设它是候选数组。")
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for call in calls:
        visit(call.get("args", {}))


def is_result_reference(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {REFERENCE_KEY}


def _parse_reference(value: dict) -> tuple[str, str]:
    reference = value.get(REFERENCE_KEY)
    if not isinstance(reference, dict):
        raise ToolPlanValidationError("$ref 必须是对象")

    step_id = reference.get("step_id")
    path = reference.get("path", "")
    if not isinstance(step_id, str) or not STEP_ID_PATTERN.fullmatch(step_id):
        raise ToolPlanValidationError("$ref.step_id 格式不正确")
    if not isinstance(path, str):
        raise ToolPlanValidationError("$ref.path 必须是字符串")
    return step_id, path


def collect_reference_steps(value: Any) -> set[str]:
    """递归收集参数中引用的前置 step_id，同时校验引用结构。"""
    if is_result_reference(value):
        step_id, _ = _parse_reference(value)
        return {step_id}
    if isinstance(value, dict):
        result: set[str] = set()
        for nested in value.values():
            result.update(collect_reference_steps(nested))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for nested in value:
            result.update(collect_reference_steps(nested))
        return result
    return set()


def build_step_metadata(
    item: dict,
    index: int,
    known_step_ids: set[str],
) -> tuple[str, list[str]]:
    """生成/校验 step_id，并确保依赖只能指向前置步骤。"""
    step_id = item.get("step_id") or f"step_{index + 1}"
    if not isinstance(step_id, str) or not STEP_ID_PATTERN.fullmatch(step_id):
        raise ToolPlanValidationError(f"第 {index + 1} 个步骤的 step_id 格式不正确")
    if step_id in known_step_ids:
        raise ToolPlanValidationError(f"step_id 重复：{step_id}")

    raw_dependencies = item.get("depends_on", [])
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(value, str) for value in raw_dependencies
    ):
        raise ToolPlanValidationError(f"{step_id}.depends_on 必须是字符串列表")

    inferred_dependencies = collect_reference_steps(item.get("args", {}))
    dependencies = list(dict.fromkeys([*raw_dependencies, *sorted(inferred_dependencies)]))
    unknown = [dependency for dependency in dependencies if dependency not in known_step_ids]
    if unknown:
        raise ToolPlanValidationError(
            f"步骤 {step_id} 引用了尚未完成的步骤：{', '.join(unknown)}"
        )
    return step_id, dependencies


def _read_result_path(source: Any, path: str) -> Any:
    current = source
    if not path:
        return current

    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise ToolPlanValidationError(f"结果引用下标越界：{path}")
            current = current[index]
        else:
            raise ToolPlanValidationError(f"结果引用路径不存在：{path}")
    return current


def resolve_result_references(value: Any, results_by_step: dict[str, dict]) -> Any:
    """把嵌套参数里的 $ref 替换成前置 Tool 的真实结果。"""
    if is_result_reference(value):
        step_id, path = _parse_reference(value)
        if step_id not in results_by_step:
            raise ToolPlanValidationError(f"依赖步骤尚无成功结果：{step_id}")
        return _read_result_path(results_by_step[step_id], path)
    if isinstance(value, dict):
        return {
            key: resolve_result_references(nested, results_by_step)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [resolve_result_references(nested, results_by_step) for nested in value]
    return value
