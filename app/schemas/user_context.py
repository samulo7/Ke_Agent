from __future__ import annotations

from dataclasses import dataclass

IdentitySource = str


@dataclass(frozen=True)
class UserContext:
    user_id: str
    user_name: str
    dept_id: str
    dept_name: str
    identity_source: IdentitySource
    is_degraded: bool
    resolved_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "dept_id": self.dept_id,
            "dept_name": self.dept_name,
            "identity_source": self.identity_source,
            "is_degraded": self.is_degraded,
            "resolved_at": self.resolved_at,
        }

