"""Value objects shared by domain and application layers (plan-v2 1.10).

They are intentionally tiny and dependency-free: a value object carries
semantics (what a valid subject/identifier is), so the same rules are not
re-implemented in every use case, gateway and interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..errors import ValidationError


def normalize_datetime(value: str) -> str:
    """Canonicalize an ISO-8601 datetime string to UTC (plan-v2 O-7).

    Naive values are interpreted as UTC (the platform never guesses a local
    zone).  The output always carries an offset, so lexicographic string
    comparison is also chronological comparison - which is what filters and
    change-log readers rely on.
    """
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def is_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """A subject reference rendered as ``Type/id`` (design.txt §7).

    Equality and hashing are structural, so sets of references behave as
    identity sets in the verifier, reconciler and console.
    """

    type: str
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", str(self.type or "").strip())
        object.__setattr__(self, "id", str(self.id or "").strip())
        if not self.type or not self.id:
            raise ValidationError(
                f"object reference needs both type and id, got {self.type!r}/{self.id!r}")
        for part in (self.type, self.id):
            if "/" in part:
                raise ValidationError(
                    "object reference segments must not contain '/': "
                    f"{self.type!r}/{self.id!r}")

    @classmethod
    def parse(cls, text: str | "ObjectRef") -> "ObjectRef":
        if isinstance(text, ObjectRef):
            return text
        parts = str(text or "").split("/")
        if len(parts) != 2:
            raise ValidationError(
                f"object reference must be 'Type/id', got {text!r}")
        return cls(parts[0], parts[1])

    def __str__(self) -> str:
        return f"{self.type}/{self.id}"


@dataclass(frozen=True, slots=True)
class ExecutionHandle:
    """Tri-state execution handle returned by an invocation boundary."""

    execution_id: str
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not self.execution_id and not self.idempotency_key:
            raise ValidationError(
                "an execution handle needs an execution id or an idempotency key")
