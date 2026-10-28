"""Platform error model.

Design-time and governance failures raise; runtime failures never do
(``RuntimeGateway.invoke`` mirrors the kernel contract and reports errors
in the result).  Interfaces translate these into HTTP status codes /
CLI exit codes.
"""

from __future__ import annotations


class PlatformError(Exception):
    """Base class of every OntoPlatform error."""


class ValidationError(PlatformError):
    """A definition violates platform or ontology semantics."""


class CompilerError(PlatformError):
    """Platform model -> Kernel schema compilation failed."""


class NotFoundError(PlatformError):
    """A referenced platform resource does not exist."""


class ConcurrencyConflictError(PlatformError):
    """An aggregate was saved with a stale version (optimistic lock)."""


class InvariantViolationError(PlatformError):
    """A domain invariant (not just a schema rule) was violated."""


class LifecycleError(PlatformError):
    """An illegal lifecycle transition was attempted."""


class AccessDeniedError(PlatformError):
    """The actor lacks the required role in the workspace."""


class RuntimeUnavailableError(PlatformError):
    """The runtime kernel is not published / not installed."""


class ConnectorError(PlatformError):
    """A data source connector failed."""


class RateLimitError(PlatformError):
    """A per-workspace quota / rate limit was exceeded (retry later)."""


class ExecutionInProgressError(PlatformError):
    """The same idempotency key is already executing (409 + Retry-After)."""


class GateError(PlatformError):
    """A promotion/close gate is not satisfied (409, no automatic retry)."""


#: Stable error taxonomy for interfaces/agents (plan-v2 1.10): category, HTTP
#: status, and whether the caller may retry the same request.  Retry means
#: "same request may succeed later", *not* "replay blindly with a new key".
ERROR_CLASSES: dict[type, tuple[str, int, bool]] = {
    ValidationError: ("validation", 400, False),
    CompilerError: ("compilation", 422, False),
    NotFoundError: ("not_found", 404, False),
    ConcurrencyConflictError: ("conflict", 409, True),
    InvariantViolationError: ("invariant", 409, False),
    LifecycleError: ("lifecycle", 409, False),
    AccessDeniedError: ("access_denied", 403, False),
    RuntimeUnavailableError: ("runtime_unavailable", 503, True),
    ConnectorError: ("connector", 502, True),
    RateLimitError: ("rate_limited", 429, True),
    ExecutionInProgressError: ("execution_in_progress", 409, True),
    GateError: ("gate_failed", 409, False),
}


def classify(exc: BaseException) -> dict:
    """Return ``{code, http_status, retryable, message, type}`` for any error."""
    if isinstance(exc, PlatformError):
        for cls in type(exc).__mro__:
            entry = ERROR_CLASSES.get(cls)
            if entry is not None:
                code, status, retryable = entry
                break
        else:  # pragma: no cover - every concrete error is mapped
            code, status, retryable = "platform_error", 400, False
    else:
        code, status, retryable = "internal", 500, False
    return {
        "code": code,
        "http_status": status,
        "retryable": retryable,
        "message": str(exc),
        "type": type(exc).__name__,
    }
