"""HTTP 鉴权信息和已校验的 request_id 组成的 Agent 请求上下文。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    owner_id: str
    self_person_id: str
    user_name: str
    request_id: str

    @classmethod
    def from_current_user(
        cls,
        current_user: dict,
        request_id: str,
    ) -> "RequestContext":
        return cls(
            owner_id=current_user["user_id"],
            self_person_id=current_user["self_person_id"],
            user_name=current_user.get("phone") or "我",
            request_id=request_id,
        )
