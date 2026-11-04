"""Request-scoped authorization context and its serialized form.

The PDP result is attached to the current execution context so the storage
boundary (``GovernedRepository``), audit writer and async scheduler can all see
the same decision without threading it through every call signature.  A
serialized context is what a worker/Scheduler persists: principal, tenant,
decision and the originating policy version.  Restoring it re-validates the
decision, so a revoked role or a new policy version denies the queued action
instead of replaying an old allow (design §4.3 Async PEP).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping

from ...domain.security.model import Decision, Principal, Resource, TenantContext
from ...errors import AccessDeniedError, ValidationError

#: The current decision is immutable for the duration of one request/task.
_CURRENT: ContextVar[dict[str, Any] | None] = ContextVar(
    "onto_authorization_context", default=None)

CONTEXT_VERSION = 1
DEFAULT_TTL_SECONDS = 900
_PROCESS_SECRET = secrets.token_hex(32)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(payload: Mapping[str, Any]) -> bytes:
    import json

    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _secret() -> str:
    configured = (os.environ.get("ONTO_SECURITY_CONTEXT_SECRET")
                  or os.environ.get("ONTO_SESSION_SECRET"))
    if configured:
        return configured
    # A deferred task is always signed.  When no deployment secret is set the
    # process generates an ephemeral one: queued contexts remain unforgeable
    # for the lifetime of this process, and a restart intentionally invalidates
    # them (the in-process scheduler queue does not survive a restart either).
    return _PROCESS_SECRET


def _signature(payload: Mapping[str, Any], secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), _canonical(payload),
                    hashlib.sha256).hexdigest()


def bind(decision: Decision, principal: Principal,
         tenant: TenantContext) -> Token:
    return _CURRENT.set({
        "decision": decision,
        "principal": principal,
        "tenant": tenant,
        "decision_id": decision.decision_id,
        "action": decision.action,
        "resource_type": decision.resource_type,
        "resource_id": decision.resource_id,
    })


def reset(token: Token | None) -> None:
    if token is not None:
        _CURRENT.reset(token)


def clear() -> None:
    _CURRENT.set(None)


def current() -> dict[str, Any] | None:
    return _CURRENT.get()


def current_decision() -> Decision | None:
    value = _CURRENT.get()
    return value.get("decision") if value else None


def current_decision_id() -> str:
    value = _CURRENT.get()
    return str(value.get("decision_id") or "") if value else ""


def current_scope():
    """Return the authorized scope object for the current decision, if any."""
    value = _CURRENT.get()
    if not value:
        return None
    decision = value.get("decision")
    principal = value.get("principal")
    tenant = value.get("tenant")
    if decision is None or principal is None or tenant is None:
        return None
    if not decision.allow:
        return None
    from .service import AuthorizedScope

    return AuthorizedScope(decision=decision, principal=principal,
                           tenant=tenant)


def snapshot() -> dict | None:
    """Serialized form of the current decision, for handing to a worker."""
    value = current()
    if not value:
        return None
    return serialize(value["principal"], value["tenant"], value["decision"])


# ---------------------------------------------------------------------------
# serialized async context
# ---------------------------------------------------------------------------


def serialize(principal: Principal, tenant: TenantContext, decision: Decision,
              *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict:
    """Return a signed, self-describing context safe to persist in a task row."""
    ttl = max(30, min(int(ttl_seconds), 24 * 3600))
    now = _now()
    body = {
        "v": CONTEXT_VERSION,
        "principal": principal.to_dict(),
        "tenant": tenant.to_dict(),
        "action": decision.action,
        "resource": {
            "type": decision.resource_type,
            "id": decision.resource_id,
            "tenant_id": tenant.tenant_id,
            "workspace_id": tenant.workspace_id,
        },
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
        "decision_id": decision.decision_id,
        "scope": decision.scope,
        "issued_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(timespec="seconds"),
    }
    secret = _secret()
    if secret:
        body["signature"] = _signature({k: v for k, v in body.items()
                                        if k != "signature"}, secret)
    return body


def _decode(payload: Mapping[str, Any]) -> dict:
    if not isinstance(payload, Mapping):
        raise AccessDeniedError("authorization context must be an object")
    if int(payload.get("v") or 0) != CONTEXT_VERSION:
        raise AccessDeniedError("unsupported authorization context version")
    secret = _secret()
    signature = str(payload.get("signature") or "")
    if not signature:
        # ``serialize`` always signs (with the deployment secret or the
        # process-ephemeral secret).  An unsigned payload is a stripped
        # credential, never a context to trust.
        raise AccessDeniedError("authorization context is not signed")
    if not secret:
        raise AccessDeniedError(
            "authorization context is signed but no secret is configured")
    expected = _signature({k: v for k, v in payload.items()
                           if k != "signature"}, secret)
    if not hmac.compare_digest(signature, expected):
        raise AccessDeniedError("authorization context signature is invalid")
    try:
        expires = datetime.fromisoformat(str(payload.get("expires_at")))
    except ValueError:
        raise AccessDeniedError("authorization context has no valid expiry")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= _now():
        raise AccessDeniedError("authorization context expired")
    return dict(payload)


def deserialize(payload: Mapping[str, Any]) -> tuple[Principal, TenantContext,
                                                      Decision, dict]:
    """Verify a persisted context and return its typed parts."""
    body = _decode(payload)
    principal = Principal.from_dict(body.get("principal") or {})
    tenant = TenantContext.from_dict(body.get("tenant") or {})
    resource = Resource.from_dict(body.get("resource") or {})
    decision = Decision.from_dict({
        **{k: v for k, v in (body.get("decision") or {}).items()},
        "allow": True,
        "reason": "async_context_restored",
        "action": body.get("action") or resource.type,
        "principal_id": principal.id,
        "tenant_id": tenant.tenant_id,
        "workspace_id": tenant.workspace_id,
        "resource_type": resource.type,
        "resource_id": resource.id,
        "policy_id": body.get("policy_id") or "",
        "policy_version": int(body.get("policy_version") or 0),
        "scope": body.get("scope"),
        "decision_id": body.get("decision_id") or "",
    })
    return principal, tenant, decision, body


def revalidate(security, payload: Mapping[str, Any]) -> tuple[Principal,
                                                              TenantContext,
                                                              Decision]:
    """Worker-side PEP: the stored allow is a claim, never a grant.

    A role removed after the task was queued, a newer policy, or a scope
    mismatch must turn into a denial.  The restored decision is only a
    fallback when the active policy cannot be read (which itself denies).
    """
    principal, tenant, stored, body = deserialize(payload)
    if security is None:
        raise AccessDeniedError("authorization kernel is not installed")
    action = str(body.get("action") or "")
    if action.startswith("role."):
        # A legacy role PEP (editor/viewer) has no ACTION_POLICY row; re-run
        # the same role check instead of asking the policy for a fake action.
        security.check_role(principal.id, tenant.workspace_id,
                            action.split(".", 1)[1])
        fresh = current_decision()
        if fresh is None or not fresh.allow:
            raise AccessDeniedError(
                "async authorization re-check denied: role no longer grants")
        return principal, tenant, fresh
    resource = Resource.from_dict(body.get("resource") or {})
    fresh = security.decision_for(principal, tenant,
                                  action, resource, record=True)
    if not fresh.allow:
        raise AccessDeniedError(
            f"async authorization re-check denied: {fresh.reason}")
    return principal, tenant, fresh


@contextmanager
def activated(payload: Mapping[str, Any] | None) -> Iterator[dict | None]:
    """Restore a serialized context for the duration of a scheduled action."""
    if not payload:
        yield None
        return
    principal, tenant, decision, body = deserialize(payload)
    token = bind(decision, principal, tenant)
    try:
        yield {"principal": principal, "tenant": tenant,
               "decision": decision, "payload": body}
    finally:
        reset(token)


@contextmanager
def activated_verified(security, payload: Mapping[str, Any] | None
                       ) -> Iterator[dict | None]:
    """Restore + revalidate; used by Scheduler workers."""
    if not payload:
        yield None
        return
    principal, tenant, decision = revalidate(security, payload)
    token = bind(decision, principal, tenant)
    try:
        yield {"principal": principal, "tenant": tenant,
               "decision": decision, "payload": dict(payload)}
    finally:
        reset(token)


def encode_state(payload: Mapping[str, Any]) -> str:
    """Compact URL/JSON transport form (base64url of canonical JSON)."""
    import json

    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_state(value: str) -> dict:
    import json

    pad = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode((value + pad).encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValidationError("invalid encoded authorization context") from exc


__all__ = [
    "activated", "activated_verified", "bind", "clear", "current",
    "current_decision", "current_decision_id", "current_scope", "decode_state",
    "deserialize", "encode_state", "revalidate", "reset", "serialize",
    "snapshot",
]
