"""Trusted request identity passed from HTTP authentication to the Agent layer."""

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RequestContext:
    owner_id: str
    self_person_id: str
    user_name: str
    request_id: str

    @classmethod
    def from_current_user(cls, current_user: dict) -> "RequestContext":
        return cls(
            owner_id=current_user["user_id"],
            self_person_id=current_user["self_person_id"],
            user_name=current_user.get("phone") or "我",
            request_id=str(uuid4()),
        )
