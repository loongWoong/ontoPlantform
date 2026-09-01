"""Governance: Access / Lifecycle / Audit (design.txt §29).

v0.1 keeps all three deliberately small:

    Lifecycle  Draft -> Validated -> Published -> Deprecated
    Access     workspace membership with owner/editor/viewer roles
    Audit      append-only log; runtime changes come from the kernel's
               Change stream via the gateway's observe_changes()
"""

from __future__ import annotations

from dataclasses import dataclass

from ...errors import AccessDeniedError, LifecycleError

# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------

LIFECYCLE_STATES = ("draft", "validated", "published", "deprecated")

# action -> (allowed from, to)
TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "validate": (("draft",), "validated"),
    "publish": (("validated",), "published"),
    "deprecate": (("published",), "deprecated"),
    "revise": (("validated",), "draft"),
}


def transition(current: str, action: str) -> str:
    """Apply a lifecycle action; raise LifecycleError when illegal."""
    if current not in LIFECYCLE_STATES:
        raise LifecycleError(f"unknown lifecycle state {current!r}")
    if action not in TRANSITIONS:
        raise LifecycleError(f"unknown lifecycle action {action!r}")
    allowed_from, target = TRANSITIONS[action]
    if current not in allowed_from:
        raise LifecycleError(
            f"cannot {action} from {current!r} (allowed from {list(allowed_from)})"
        )
    return target


# ----------------------------------------------------------------------
# Access
# ----------------------------------------------------------------------

ROLES = ("viewer", "editor", "owner")
_ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}


@dataclass(slots=True)
class Membership:
    workspace_id: str
    user: str
    role: str  # viewer | editor | owner

    def to_dict(self) -> dict:
        return {"workspace_id": self.workspace_id, "user": self.user, "role": self.role}

    @classmethod
    def from_dict(cls, data: dict) -> "Membership":
        return cls(
            workspace_id=data["workspace_id"], user=data["user"], role=data["role"]
        )


class AccessPolicy:
    """Pure decision object; storage-agnostic."""

    def check(self, memberships: list[Membership], user: str, workspace_id: str,
              required: str = "viewer") -> None:
        rank_needed = _ROLE_RANK.get(required)
        if rank_needed is None:
            raise AccessDeniedError(f"unknown role requirement {required!r}")
        for m in memberships:
            if m.user == user and m.workspace_id == workspace_id:
                if _ROLE_RANK.get(m.role, -1) >= rank_needed:
                    return
                raise AccessDeniedError(
                    f"user {user!r} has role {m.role!r} in workspace "
                    f"{workspace_id!r}; {required!r} is required"
                )
        raise AccessDeniedError(
            f"user {user!r} is not a member of workspace {workspace_id!r}"
        )

    def role_of(self, memberships: list[Membership], user: str, workspace_id: str) -> str | None:
        for m in memberships:
            if m.user == user and m.workspace_id == workspace_id:
                return m.role
        return None


# ----------------------------------------------------------------------
# Audit
# ----------------------------------------------------------------------


@dataclass(slots=True)
class AuditEntry:
    ts: str
    actor: str
    workspace_id: str
    action: str
    target: str
    detail: dict

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "actor": self.actor,
            "workspace_id": self.workspace_id,
            "action": self.action,
            "target": self.target,
            "detail": self.detail,
        }
