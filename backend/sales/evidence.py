"""模型输出的业务校验：来源必须授权、引用必须真实、准备对象必须一致。

此模块能拒绝伪造 ID/原文，不能数学证明一句引用支持模型推理。转述、否定、
冲突和销售建议的可用性仍要用固定案例人工评估，不能把原文匹配率称为事实准确率。
"""

from backend.agent.contracts import ModelBusinessValidationError
from backend.sales.contracts import SalesPreparationBrief, SalesPreparationContext


def validate_brief(brief: SalesPreparationBrief, *, context: SalesPreparationContext) -> None:
    def reject(code, feedback):
        raise ModelBusinessValidationError(code, feedback)

    if brief.person_id != context.person_id:
        reject("SALES_PERSON_MISMATCH", "准备单必须只针对本次已确认的客户。")
    sources = {source.source_id: source for source in context.sources}

    def check(refs):
        for ref in refs:
            source = sources.get(ref.source_id)
            if source is None or not ref.quote.strip() or ref.quote not in source.excerpt:
                reject("SALES_EVIDENCE_INVALID", "引用必须来自提供的 source_id，quote 必须逐字出现在对应 excerpt 中。")

    for group in (brief.customer_summary, brief.recent_communications,
                  brief.customer_needs, brief.previous_commitments):
        for item in group:
            check(item.evidence_refs)
    for item in brief.customer_needs:
        # 销售自己的设想不能被升级为“客户已经表达的需求”。这只是来源规则，
        # 客户转述他人的需求仍需语义评测，不能认为 speaker=customer 就一定成立。
        if not any(sources[ref.source_id].speaker == "customer" for ref in item.evidence_refs):
            reject("SALES_NEED_UNSUPPORTED", "客户需求至少引用一条客户发言，不能用销售设想或档案猜测。")
    for item in brief.previous_commitments:
        check(item.status_evidence_refs)
        if item.completion_state != "UNKNOWN" and not item.status_evidence_refs:
            reject("SALES_COMMITMENT_STATUS_UNSUPPORTED", "OPEN/DONE 必须另外引用状态记录；只有约定而无后续时使用 UNKNOWN。")
    for item in [*brief.suggested_questions, *([brief.suggested_opening] if brief.suggested_opening else [])]:
        check(item.basis_refs)


def context_from_records(records: list[dict]) -> SalesPreparationContext | None:
    """从本请求真实成功回执取快照，不从模型草稿、历史记忆或旧回复取来源。"""
    found = []
    for index, record in enumerate(records):
        result = record.get("result") or {}
        if (record.get("tool") != "get_sales_preparation_context"
                or record.get("status") not in {"SUCCESS", "RESOLVED"} or not result.get("success")):
            continue
        context = SalesPreparationContext.model_validate(result["data"])
        if context.person_id != record.get("args", {}).get("person_id"):
            raise ModelBusinessValidationError("SALES_PERSON_MISMATCH", "工具证据与请求客户不一致。")
        found.append((index, context))
    if not found:
        return None
    if len({context.person_id for _, context in found}) != 1:
        raise ModelBusinessValidationError("SALES_MULTIPLE_CUSTOMERS", "一次仅为一位客户准备，请分开提问。")
    position, context = found[-1]
    for record in records[position + 1:]:
        if (record.get("tool") in {"update_person", "delete_person"}
                and record.get("args", {}).get("person_id") == context.person_id
                and (record.get("result") or {}).get("success")):
            raise ModelBusinessValidationError("SALES_CONTEXT_STALE", "读取证据后客户资料已变化，请新建请求重新生成准备单。")
    return context
