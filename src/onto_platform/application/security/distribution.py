"""Data distribution with the same PEP/masker as the API (design §4.4/§8.3).

Two independent services live here, both deliberately *additive* to the
security package: no existing module is modified.

``ExportService``
    Exports kernel object rows and authorization decisions.  Object rows are
    passed through the same row-scope + field-mask PEP the REST adapter uses
    (``SecurityUseCases.filter_objects``); the original rows are copied and
    never mutated.  Decisions are evidence, not business data, so they are
    exported verbatim (they still have to pass ``security.read``).

``WebhookService``
    Stores per-workspace webhook subscriptions (kind ``webhook``) and delivers
    outbox events that have not yet been accepted by every matching hook.
    Delivery does not depend on the in-process outbox ``dispatched_at`` flag:
    an event can be consumed by local subscribers and still owe a webhook.
    Payloads are masked against the emitting actor's field obligations before
    they leave the platform, and the request is signed with
    ``X-Onto-Signature: sha256=<hmac>``.  Delivery is intentionally *not*
    recorded on the outbox fields (``dispatched_at`` / ``attempts``); a
    separate ``webhook_delivered_at`` marker keeps webhook delivery from
    rewriting the transactional-outbox semantics.

Zero runtime dependencies: only the standard library is used, and network
I/O is injected through ``transport`` so tests never open a socket.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import inspect
import io
import ipaddress
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ...domain.security.model import Resource
from ...errors import (
    AccessDeniedError,
    ConcurrencyConflictError,
    NotFoundError,
    ValidationError,
)
from .masking import MASKERS, apply_field_masks, validate_mask_spec
from .webhook_delivery import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    WEBHOOK_DELIVERY_KIND,
    WebhookDeliveryLedger,
    iso,
    parse_ts,
)

WEBHOOK_KIND = "webhook"
DEFAULT_WEBHOOK_TIMEOUT = 5.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_kernel(security: Any, ctx: Any) -> Any:
    """Return the ``SecurityService`` behind a facade/mock, if there is one.

    ``Platform.security`` is ``SecurityUseCases`` while ``ctx.security`` is the
    kernel.  Both call styles are accepted so callers can construct these
    services with either object without changing existing wiring.
    """
    for candidate in (
        security,
        getattr(security, "service", None),
        getattr(ctx, "security", None),
        getattr(getattr(ctx, "security", None), "service", None),
    ):
        if candidate is None:
            continue
        if callable(getattr(candidate, "decision_for", None)) \
                and callable(getattr(candidate, "principal", None)):
            return candidate
    return None


def _clock_of(ctx: Any) -> Callable[[], str]:
    clock = getattr(ctx, "clock", None)
    return clock if callable(clock) else _now


def _new_id(ctx: Any, prefix: str) -> str:
    factory = getattr(ctx, "id_factory", None)
    if callable(factory):
        try:
            return f"{prefix}_{factory()}"
        except TypeError:  # pragma: no cover - alternate factory contract
            pass
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex[:12]}"


def _mask_specs(decision: Any) -> list[dict]:
    """Flatten every ``field_mask`` obligation of a decision into specs."""
    specs: list[dict] = []
    for obligation in getattr(decision, "obligations", ()) or ():
        if getattr(obligation, "kind", "") != "field_mask":
            continue
        fields = (getattr(obligation, "value", {}) or {}).get("fields") or []
        if isinstance(fields, Mapping):  # design §3.3 shorthand {field: mask}
            fields = [{"field": key, "mask": value}
                      for key, value in fields.items()]
        for spec in fields:
            if isinstance(spec, Mapping):
                specs.append(dict(spec))
    return specs


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportService:
    """Authorized JSON/CSV export for objects and decisions (design §4.4)."""

    def __init__(self, ctx: Any, security: Any = None) -> None:
        self.ctx = ctx
        #: Accepts either ``SecurityUseCases`` (has ``filter_objects``), the
        #: kernel (has ``decision_for``) or a test double exposing both.
        self.security = security
        self._kernel = _resolve_kernel(security, ctx)

    # ---- authorization helpers -------------------------------------------

    def _require(self, actor: str, workspace_id: str, action: str) -> None:
        checker = getattr(self.ctx, "require_action", None)
        if callable(checker):
            checker(actor, workspace_id, action)
            return
        if self._kernel is not None:
            self._kernel.check_action(actor, workspace_id, action)
            return
        raise RuntimeError("no PEP is available to authorize the export")

    def _filterer(self) -> Callable[..., list[dict]] | None:
        for candidate in (
            getattr(self.security, "filter_objects", None),
            getattr(getattr(self.ctx, "security", None), "filter_objects", None),
        ):
            if callable(candidate):
                return candidate
        if self._kernel is not None:
            return self._filter_objects_in_process
        return None

    def _filter_objects_in_process(
        self, workspace_id: str, actor: str, objects: Iterable[Mapping[str, Any]],
        *, action: str, resource_type: str,
    ) -> list[dict]:
        """Fallback PEP when no ``filter_objects`` is wired (kernel only)."""
        kernel = self._kernel
        if kernel is None:  # pragma: no cover - guarded by _filterer
            raise RuntimeError("no security kernel is available")
        self._require(actor, workspace_id, "workspace.read")
        principal = kernel.principal(actor, workspace_id)
        tenant = kernel.tenant_context(workspace_id)
        decision = kernel.decision_for(
            principal, tenant, action,
            Resource(type=resource_type, id="",
                     tenant_id=tenant.tenant_id, workspace_id=workspace_id),
            check_scope=False,
        )
        if not decision.allow:
            raise AccessDeniedError(
                f"action {action!r} denied on {resource_type!r}: "
                f"{decision.reason}")
        scope = kernel.authorized_scope(decision, principal, tenant)
        masks = _mask_specs(decision)
        kept: list[dict] = []
        for obj in objects:
            props = dict(obj.get("properties") or {})
            flat = {**props, **obj, "tenant_id": tenant.tenant_id,
                    "workspace_id": workspace_id}
            if not scope.filter([flat]):
                continue
            masked = apply_field_masks([flat], masks)[0] if masks else flat
            out = deepcopy(obj)
            out_props = dict(out.get("properties") or {})
            for key in list(out_props):
                if key in masked:
                    out_props[key] = masked[key]
            out["properties"] = out_props
            kept.append(out)
        return kept

    def _mask_top_level_fields(
        self, workspace_id: str, actor: str, rows: list[dict], *,
        action: str, resource_type: str,
    ) -> list[dict]:
        """Belt-and-braces masking for flat rows.

        ``filter_objects`` applies masks to a flattened view but writes the
        result back only into ``properties``.  A caller that exports flat rows
        (business fields at the top level) would therefore keep the original
        value.  Re-apply the same obligations to same-named top-level fields;
        ``properties`` is copied back verbatim because the filterer already
        masked it (avoiding a double hash).
        """
        if not rows:
            return rows
        kernel = self._kernel
        if kernel is None:
            raise AccessDeniedError(
                "export masking requires the security kernel")
        try:
            principal = kernel.principal(actor, workspace_id)
            tenant = kernel.tenant_context(workspace_id)
            decision = kernel.decision_for(
                principal, tenant, action,
                Resource(type=resource_type, id="",
                         tenant_id=tenant.tenant_id,
                         workspace_id=workspace_id),
                check_scope=False,
            )
            if not decision.allow:
                raise AccessDeniedError(
                    "export masking was denied by the policy decision")
            prepared: dict[str, dict] = {}
            for spec in _mask_specs(decision):
                validated = validate_mask_spec(spec)
                prepared[validated["field"]] = validated
        except AccessDeniedError:
            raise
        except Exception as exc:
            # Fail closed: a transient PIP error must never downgrade an
            # export to the original (unmasked) rows.
            raise AccessDeniedError(
                "export masking failed; refusing to emit unmasked data") from exc
        if not prepared:
            return rows
        names = set(prepared)
        out: list[dict] = []
        for row in rows:
            if not isinstance(row, Mapping) or not (names & set(row)):
                out.append(row)
                continue
            masked = _recursive_mask(dict(row), prepared)
            # The filterer owns the nested object payload; only top-level
            # duplicates are repaired here.
            if "properties" in row:
                masked["properties"] = row["properties"]
            out.append(masked)
        return out

    def _call_filterer(self, filterer: Callable[..., list[dict]],
                       workspace_id: str, actor: str, rows: list[dict],
                       *, action: str, resource_type: str) -> list[dict]:
        """Call ``filter_objects`` while tolerating a facade without ``action``.

        The kernel's canonical signature accepts ``action``/``resource_type``;
        a facade or test double may only accept the latter.  Never swallow a
        TypeError raised *inside* the filterer - inspect the signature.
        """
        kwargs: dict[str, Any] = {"resource_type": resource_type}
        try:
            parameters = inspect.signature(filterer).parameters
        except (TypeError, ValueError):  # pragma: no cover - builtins
            parameters = {}
        accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD
                             for parameter in parameters.values())
        if accepts_kwargs or "action" in parameters:
            kwargs["action"] = action
        return filterer(workspace_id, actor, rows, **kwargs)

    # ---- object export ----------------------------------------------------

    def export_objects(
        self,
        workspace_id: str,
        actor: str,
        objects: list[dict],
        *,
        fmt: str = "json",
        action: str = "object.read",
        resource_type: str = "object",
        bom: bool = False,
    ) -> dict:
        """Filter + mask rows, then serialize them as JSON or CSV.

        ``objects`` are kernel object rows (business fields under
        ``properties``).  An empty result is a valid export, not an error.
        """
        if not isinstance(objects, (list, tuple)):
            raise ValidationError("objects must be a list of object rows")
        filterer = self._filterer()
        if filterer is None:  # pragma: no cover - defensive
            raise RuntimeError(
                "security.filter_objects is not available for export")
        # ``filter_objects`` is itself the PEP: it checks ``workspace.read``
        # and applies row scope + field masks.  No second check here, so one
        # export produces exactly one authorization decision.
        rows = self._call_filterer(
            filterer, workspace_id, actor,
            [deepcopy(dict(row)) for row in objects],
            action=action, resource_type=resource_type,
        )
        rows = self._mask_top_level_fields(
            workspace_id, actor, list(rows),
            action=action, resource_type=resource_type,
        )
        normalized = str(fmt or "json").strip().lower()
        if normalized == "json":
            body = json.dumps(list(rows), ensure_ascii=False, indent=2,
                              default=str).encode("utf-8")
            return {"content_type": "application/json",
                    "filename": "objects.json",
                    "body": body}
        if normalized == "csv":
            return {"content_type": "text/csv; charset=utf-8",
                    "filename": "objects.csv",
                    "body": _rows_to_csv(rows, bom=bom)}
        raise ValidationError(
            f"unsupported export format {fmt!r}; use 'json' or 'csv'")

    # ---- decision export --------------------------------------------------

    def export_decisions(self, workspace_id: str, actor: str,
                         limit: int = 1000, *, fmt: str = "jsonl") -> dict:
        """Export decision evidence (JSONL by default) after ``security.read``.

        Decisions carry no field-mask obligation of their own: they are the
        audit record of one, so they are exported verbatim.
        """
        self._require(actor, workspace_id, "security.read")
        source = self._kernel if self._kernel is not None else self.security
        if source is None or not callable(getattr(source, "list_decisions", None)):
            raise RuntimeError("no decision store is available for export")
        rows = list(source.list_decisions(workspace_id, limit=limit))
        normalized = str(fmt or "jsonl").strip().lower()
        if normalized == "json":
            body = json.dumps(rows, ensure_ascii=False, indent=2,
                              default=str).encode("utf-8")
            return {"content_type": "application/json",
                    "filename": "decisions.json",
                    "body": body}
        if normalized == "jsonl":
            text = "".join(json.dumps(row, ensure_ascii=False, default=str)
                           + "\n" for row in rows)
            return {"content_type": "application/x-ndjson; charset=utf-8",
                    "filename": "decisions.jsonl",
                    "body": text.encode("utf-8")}
        raise ValidationError(
            f"unsupported decision export format {fmt!r}; use 'json' or 'jsonl'")


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return value


def _rows_to_csv(rows: Iterable[Mapping[str, Any]], *, bom: bool = False) -> bytes:
    materialized = [dict(row) for row in rows]
    fieldnames: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    buffer = io.StringIO(newline="")
    if fieldnames:
        writer = csv.DictWriter(buffer, fieldnames=fieldnames,
                                extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow({key: _csv_value(row.get(key))
                             for key in fieldnames})
    body = buffer.getvalue().encode("utf-8")
    # Optional BOM so Excel opens UTF-8 Chinese fields without mojibake.
    return (b"\xef\xbb\xbf" + body) if bom else body


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

#: ``transport(url, body, headers, timeout) -> status code``.  Injecting this
#: keeps tests offline and lets an operator plug in a proxying HTTP client.
Transport = Callable[[str, bytes, dict, float], Any]


def _urllib_transport(url: str, body: bytes, headers: dict,
                      timeout: float) -> int:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - http(s)
        return int(getattr(response, "status", 200))


def _status_code(result: Any) -> int:
    if result is None:
        return 200
    if isinstance(result, bool):
        return 200 if result else 500
    if isinstance(result, int):
        return result
    status = getattr(result, "status", None)
    if status is None:
        status = getattr(result, "code", None)
    try:
        return int(status) if status is not None else 200
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return 200


#: Private/loopback targets are rejected by default: a webhook URL is
#: tenant-supplied outbound traffic and must not become an SSRF pivot.  Tests
#: and controlled deployments can opt in with ``ONTO_WEBHOOK_ALLOW_PRIVATE=1``.
_PRIVATE_HOST_ENV = "ONTO_WEBHOOK_ALLOW_PRIVATE"


def _allow_private_webhook_hosts() -> bool:
    raw = os.environ.get(_PRIVATE_HOST_ENV, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _reject_private_host(host: str) -> None:
    host = host.strip().lower().rstrip(".")
    if not host:
        raise ValidationError("webhook url must include a host")
    if host in {"localhost", "localhost.localdomain"} \
            or host.endswith(".localhost"):
        raise ValidationError(
            "webhook url must not target localhost "
            f"(set {_PRIVATE_HOST_ENV}=1 to allow)")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A DNS name is not resolved here: resolving at validation time would
        # add network I/O and DNS-rebinding risk is a deployment concern.
        return
    if address.is_loopback or address.is_private or address.is_link_local \
            or address.is_reserved or address.is_unspecified \
            or address.is_multicast:
        raise ValidationError(
            f"webhook url must not target a private address {host!r} "
            f"(set {_PRIVATE_HOST_ENV}=1 to allow)")


def _validate_url(url: Any) -> str:
    text = str(url or "").strip()
    if not text:
        raise ValidationError("webhook url must not be empty")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValidationError(
            f"webhook url must be an absolute http(s) URL, got {text!r}")
    if parts.username is not None or parts.password is not None:
        raise ValidationError("webhook url must not contain userinfo")
    host = parts.hostname or ""
    if not _allow_private_webhook_hosts():
        _reject_private_host(host)
    return text


def _validate_event_types(event_types: Any, ctx: Any = None,
                          workspace_id: str | None = None) -> list[str]:
    # Imported lazily: ``events`` imports ``common``, which imports the security
    # package; a module-level import here would be circular.
    from ..events import EVENT_CATALOG

    if isinstance(event_types, str) or not isinstance(
            event_types, (list, tuple, set, frozenset)):
        raise ValidationError("event_types must be a list of event type names")
    normalized: list[str] = []
    for event_type in event_types:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValidationError("event_types entries must be non-empty strings")
        name = event_type.strip()
        if name not in EVENT_CATALOG:
            # P1 business events are declared per workspace; a webhook may
            # subscribe to one only after its contract is published.
            contract = None
            if ctx is not None and workspace_id:
                contract = ctx.repo.get("event_contract", workspace_id, name)
            if contract is None:
                raise ValidationError(f"unknown event type: {name!r}")
            if contract.get("lifecycle") != "published":
                raise ValidationError(
                    f"event contract {name!r} is not published")
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ValidationError("event_types must not be empty")
    return normalized


def _positive_int(value: Any, name: str, default: int) -> int:
    """Validate an optional positive-integer webhook setting."""
    if value is None:
        return int(default)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be an integer") from exc
    if parsed < 1:
        raise ValidationError(f"{name} must be at least 1")
    return parsed


def _recursive_mask(value: Any, specs: Mapping[str, dict]) -> Any:
    """Apply masks to same-named fields at any depth; never mutates input."""
    if isinstance(value, Mapping):
        out: dict = {}
        for key, item in value.items():
            spec = specs.get(key)
            if spec is not None:
                if spec["mask"] == "strip":
                    continue
                out[key] = MASKERS[spec["mask"]](item)
            else:
                out[key] = _recursive_mask(item, specs)
        return out
    if isinstance(value, list):
        return [_recursive_mask(item, specs) for item in value]
    return value


class WebhookService:
    """Per-workspace webhook subscriptions + signed event delivery."""

    def __init__(self, ctx: Any, security: Any = None, dispatcher: Any = None,
                 *, transport: Transport | None = None,
                 timeout: float = DEFAULT_WEBHOOK_TIMEOUT,
                 lease_seconds: float = DEFAULT_LEASE_SECONDS,
                 backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
                 ) -> None:
        self.ctx = ctx
        self.security = security
        #: Kept for composition compatibility; the service does not require a
        #: dispatcher and never calls ``EventLog.mark_dispatched``.
        self.dispatcher = dispatcher
        self._kernel = _resolve_kernel(security, ctx)
        self.transport: Transport = transport or _urllib_transport
        self.timeout = float(timeout)
        self._clock = _clock_of(ctx)
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))
        #: Unique per worker: identifies the holder of a delivery lease.
        self.worker_id = f"whd_worker_{_new_id(ctx, 'w')}"
        self.ledger = WebhookDeliveryLedger(ctx, clock=self._clock)

    # ---- authorization / storage helpers ---------------------------------

    def _check(self, actor: str, workspace_id: str, action: str) -> Any:
        kernel = self._kernel
        if kernel is not None and callable(getattr(kernel, "check_action", None)):
            return kernel.check_action(actor, workspace_id, action)
        checker = getattr(self.security, "check_action", None)
        if callable(checker):
            return checker(actor, workspace_id, action)
        checker = getattr(self.ctx, "require_action", None)
        if callable(checker):
            return checker(actor, workspace_id, action)
        raise RuntimeError("no PEP is available to authorize webhook management")

    def _require_manage(self, actor: str, workspace_id: str) -> None:
        # The specification pins this action; a custom policy can still deny it
        # (check_action always goes through the PDP).
        self._check(actor, workspace_id, "security.manage")

    def _audit(self, actor: str, workspace_id: str, action: str, target: str,
               detail: dict | None = None) -> None:
        writer = getattr(self.ctx, "audit_event", None)
        if not callable(writer):
            return
        try:
            writer(actor, workspace_id, action, target, dict(detail or {}))
        except Exception:  # pragma: no cover - audit must not mask delivery
            pass

    def _rows(self, workspace_id: str) -> list[dict]:
        rows = [deepcopy(dict(row)) for row in
                self.ctx.repo.list(WEBHOOK_KIND, workspace_id)]
        rows.sort(key=lambda row: (str(row.get("created_at") or ""),
                                   str(row.get("id") or "")))
        return rows

    # ---- CRUD -------------------------------------------------------------

    def list_webhooks(self, workspace_id: str, actor: str | None = None
                      ) -> list[dict]:
        """List subscriptions.

        ``actor`` is optional because the dispatcher reads subscriptions
        internally.  When supplied, the caller must hold ``security.manage``
        (the row contains the signing secret, so viewer-level reads are not
        enough).
        """
        if actor is not None:
            self._require_manage(actor, workspace_id)
        return self._rows(workspace_id)

    def get_webhook(self, workspace_id: str, webhook_id: str,
                    actor: str | None = None) -> dict:
        if actor is not None:
            self._require_manage(actor, workspace_id)
        row = self.ctx.repo.get(WEBHOOK_KIND, workspace_id, webhook_id)
        if row is None:
            raise NotFoundError(f"webhook not found: {webhook_id!r}")
        return deepcopy(dict(row))

    def create_webhook(self, workspace_id: str, actor: str, url: str,
                       event_types: list[str], secret: str = "",
                       *, rate_limit_per_minute: int | None = None,
                       max_attempts: int | None = None) -> dict:
        self._require_manage(actor, workspace_id)
        now = self._clock()
        row = {
            "id": _new_id(self.ctx, "wh"),
            "workspace_id": workspace_id,
            "url": _validate_url(url),
            "event_types": _validate_event_types(
                event_types, self.ctx, workspace_id),
            "secret": str(secret or ""),
            "active": True,
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
            "last_delivery_at": None,
            "last_error": None,
            "delivery_count": 0,
            "rate_limit_per_minute": _positive_int(
                rate_limit_per_minute, "rate_limit_per_minute",
                DEFAULT_RATE_LIMIT_PER_MINUTE),
            "max_attempts": _positive_int(
                max_attempts, "max_attempts", DEFAULT_MAX_ATTEMPTS),
            # Fixed-window quota state (CAS-updated by the dispatcher).
            "window_start": None,
            "window_count": 0,
        }
        self.ctx.repo.put(WEBHOOK_KIND, workspace_id, row["id"], row)
        self._audit(actor, workspace_id, "webhook.created", row["id"],
                    {"url": row["url"], "event_types": row["event_types"]})
        return deepcopy(row)

    def update_webhook(self, workspace_id: str, actor: str, webhook_id: str,
                       *, url: str | None = None,
                       event_types: list[str] | None = None,
                       secret: str | None = None,
                       active: bool | None = None,
                       rate_limit_per_minute: int | None = None,
                       max_attempts: int | None = None) -> dict:
        self._require_manage(actor, workspace_id)
        row = self.ctx.repo.get(WEBHOOK_KIND, workspace_id, webhook_id)
        if row is None:
            raise NotFoundError(f"webhook not found: {webhook_id!r}")
        row = deepcopy(dict(row))
        if url is not None:
            row["url"] = _validate_url(url)
        if event_types is not None:
            row["event_types"] = _validate_event_types(
                event_types, self.ctx, workspace_id)
        if secret is not None:
            row["secret"] = str(secret or "")
        if active is not None:
            row["active"] = bool(active)
        if rate_limit_per_minute is not None:
            row["rate_limit_per_minute"] = _positive_int(
                rate_limit_per_minute, "rate_limit_per_minute",
                DEFAULT_RATE_LIMIT_PER_MINUTE)
        if max_attempts is not None:
            row["max_attempts"] = _positive_int(
                max_attempts, "max_attempts", DEFAULT_MAX_ATTEMPTS)
        row["updated_at"] = self._clock()
        self.ctx.repo.put(WEBHOOK_KIND, workspace_id, webhook_id, row)
        self._audit(actor, workspace_id, "webhook.updated", webhook_id,
                    {"url": row.get("url"), "active": row.get("active"),
                     "event_types": row.get("event_types")})
        return deepcopy(row)

    def delete_webhook(self, workspace_id: str, actor: str,
                       webhook_id: str) -> dict:
        self._require_manage(actor, workspace_id)
        row = self.ctx.repo.get(WEBHOOK_KIND, workspace_id, webhook_id)
        if row is None:
            raise NotFoundError(f"webhook not found: {webhook_id!r}")
        self.ctx.repo.delete(WEBHOOK_KIND, workspace_id, webhook_id)
        self._audit(actor, workspace_id, "webhook.deleted", webhook_id,
                    {"url": row.get("url")})
        return {"id": webhook_id, "deleted": True}

    # ---- payload masking (design §4.4) -----------------------------------

    def mask_event(self, workspace_id: str, actor: str,
                   event: Mapping[str, Any]) -> dict:
        """Return a masked deep copy of ``event``; minimal on deny/failure.

        The decision is evaluated exactly like an ``object.read`` on an
        ``event`` resource with ``check_scope=False`` (the event's scope is
        already fixed by its workspace).  Field obligations are collected and
        applied both with ``security.redact`` and recursively, so masks hit
        nested payload objects too.  A caller that cannot read the event gets
        only ``{event_id, type}`` - never the payload.
        """
        if not isinstance(event, Mapping):
            return {"event_id": "", "type": ""}
        event_id = str(event.get("event_id") or event.get("id") or "")
        event_type = str(event.get("type") or "")
        minimal = {"event_id": event_id, "type": event_type}
        kernel = self._kernel
        if kernel is None:
            return minimal
        try:
            principal = kernel.principal(actor, workspace_id)
            tenant = kernel.tenant_context(workspace_id)
            decision = kernel.decision_for(
                principal, tenant, "object.read",
                Resource(type="event", id=event_id,
                         tenant_id=tenant.tenant_id,
                         workspace_id=workspace_id),
                check_scope=False,
            )
        except Exception:  # pragma: no cover - PIP failure must fail closed
            return minimal
        if not decision.allow:
            return minimal
        try:
            masked = deepcopy(dict(event))
            payload = masked.get("payload")
            if not isinstance(payload, Mapping):
                return masked
            specs = _mask_specs(decision)
            if not specs:
                return masked
            # Explicitly exercise the shared masker first (top-level fields),
            # then walk the structure so same-named nested fields cannot leak.
            redact = getattr(kernel, "redact", None)
            payload = deepcopy(dict(payload))
            if callable(redact):
                payload = redact(decision, [payload])[0]
            prepared: dict[str, dict] = {}
            for spec in specs:
                validated = validate_mask_spec(spec)
                prepared[validated["field"]] = validated
            masked["payload"] = _recursive_mask(payload, prepared)
            return masked
        except Exception:  # pragma: no cover - fail closed on mask errors
            return minimal

    # ---- delivery ledger --------------------------------------------------

    def list_deliveries(self, workspace_id: str, webhook_id: str | None = None,
                        status: str | None = None, limit: int = 100, *,
                        actor: str | None = None) -> list[dict]:
        """List per-(event, webhook) delivery rows, newest first.

        ``actor`` is optional for internal workers; a caller-supplied actor
        must hold ``security.manage`` (delivery rows are operational telemetry
        for a tenant's outbound traffic).
        """
        if actor is not None:
            self._require_manage(actor, workspace_id)
        return self.ledger.list(workspace_id, webhook_id=webhook_id,
                                status=status, limit=limit)

    def dead_letters(self, workspace_id: str, *, actor: str | None = None,
                     limit: int = 100) -> list[dict]:
        """Return deliveries that exhausted ``max_attempts``."""
        if actor is not None:
            self._require_manage(actor, workspace_id)
        return self.ledger.dead(workspace_id, limit=limit)

    def replay_delivery(self, workspace_id: str, actor: str,
                        delivery_id: str) -> dict:
        """Reopen a ``dead``/``failed`` delivery and re-arm its event.

        A terminal ``dead`` delivery counts as settled for the event marker, so
        replaying also clears ``webhook_delivered_at``: the next dispatch pass
        may attempt the event again.  The attempt counter restarts at zero.
        """
        self._require_manage(actor, workspace_id)
        row = self.ledger.get(workspace_id, delivery_id)
        if row is None:
            raise NotFoundError(
                f"webhook delivery not found: {delivery_id!r}")
        if row.get("status") not in ("dead", "failed"):
            raise ValidationError(
                "only dead or failed deliveries can be replayed "
                f"(current status {row.get('status')!r})")
        replayed = self._reopen_delivery(workspace_id, delivery_id)
        self._reopen_event(workspace_id, replayed)
        self._audit(actor, workspace_id, "webhook.delivery_replayed",
                    delivery_id,
                    {"webhook_id": replayed.get("webhook_id"),
                     "event_id": replayed.get("event_id"),
                     "attempts": row.get("attempts")})
        return replayed

    def _reopen_delivery(self, workspace_id: str, delivery_id: str) -> dict:
        now = self._clock()
        fallback: dict = {}
        for _ in range(8):
            got = self.ctx.repo.get_versioned(
                WEBHOOK_DELIVERY_KIND, workspace_id, delivery_id)
            if got is None:
                raise NotFoundError(
                    f"webhook delivery not found: {delivery_id!r}")
            row, version = got
            row = dict(row)
            fallback = deepcopy(row)
            if row.get("status") not in ("dead", "failed"):
                return fallback
            updated = deepcopy(row)
            updated.update({
                "status": "pending",
                "attempts": 0,
                "next_attempt_at": None,
                "lease_expires_at": None,
                "lease_owner": None,
                "last_error": None,
                "response_excerpt": None,
                "updated_at": now,
            })
            try:
                self.ctx.repo.put_if_version(
                    WEBHOOK_DELIVERY_KIND, workspace_id, delivery_id,
                    updated, version)
                return updated
            except ConcurrencyConflictError:
                continue
        return fallback

    def _reopen_event(self, workspace_id: str, delivery: Mapping[str, Any]
                      ) -> None:
        event_doc_id = str(delivery.get("event_doc_id")
                           or delivery.get("event_id") or "")
        if not event_doc_id:
            return
        current = self.ctx.repo.get("event", workspace_id, event_doc_id)
        if current is None:
            return
        row = deepcopy(dict(current))
        row.pop("webhook_delivered_at", None)
        self.ctx.repo.put("event", workspace_id, event_doc_id, row)

    def _mark_event_delivered(self, workspace_id: str,
                              event: Mapping[str, Any]) -> None:
        event_doc_id = str(event.get("id") or event.get("event_id") or "")
        if not event_doc_id:
            return
        current = self.ctx.repo.get("event", workspace_id, event_doc_id)
        row = (deepcopy(dict(current)) if current is not None
               else deepcopy(dict(event)))
        if row.get("webhook_delivered_at"):
            return
        row["webhook_delivered_at"] = self._clock()
        self.ctx.repo.put("event", workspace_id, event_doc_id, row)

    def _reserve_quota(self, workspace_id: str, hook: Mapping[str, Any],
                       now: str) -> tuple[bool, dict | None]:
        """Reserve one fixed-window slot for ``hook`` via CAS.

        Returns ``(allowed, latest_row)``.  ``False`` means the per-minute
        quota is exhausted (or the hook was deleted mid-flight); the caller
        must not call the transport and leaves the delivery pending.
        """
        hook_id = str(hook.get("id") or "")
        now_dt = parse_ts(now) or datetime.now(timezone.utc)
        latest: dict | None = None
        for _ in range(8):
            got = self.ctx.repo.get_versioned(
                WEBHOOK_KIND, workspace_id, hook_id)
            if got is None:
                return False, None
            row, version = got
            row = dict(row)
            latest = deepcopy(row)
            limit = _positive_int(row.get("rate_limit_per_minute"),
                                  "rate_limit_per_minute",
                                  DEFAULT_RATE_LIMIT_PER_MINUTE)
            window_start = parse_ts(row.get("window_start"))
            count = int(row.get("window_count") or 0)
            if window_start is None or (now_dt - window_start) \
                    >= timedelta(seconds=60):
                window_start = now_dt
                count = 0
            if count >= limit:
                return False, latest
            updated = deepcopy(row)
            updated["window_start"] = iso(window_start)
            updated["window_count"] = count + 1
            updated["updated_at"] = now
            try:
                self.ctx.repo.put_if_version(
                    WEBHOOK_KIND, workspace_id, hook_id, updated, version)
                return True, updated
            except ConcurrencyConflictError:
                continue
        return False, latest

    # ---- delivery ---------------------------------------------------------

    def dispatch_webhooks(self, workspace_id: str | None = None,
                          limit: int = 50, *, actor: str | None = None) -> dict:
        """POST undispatched events to matching active webhooks.

        Every ``(event, webhook)`` pair owns a ``webhook_delivery`` row.  The
        dispatcher claims that row with a lease (CAS, so multiple workers can
        share one repository without double-delivering), applies the per-hook
        fixed-window quota, then records the outcome: ``delivered``, ``failed``
        with exponential backoff, or ``dead`` after ``max_attempts``.
        ``skipped`` keeps its old meaning (events with no matching active
        subscription); ``throttled``/``dead_lettered`` are added only when
        non-zero so legacy exact-shape callers keep working.  The event's
        ``webhook_delivered_at`` marker is written only once every matching
        delivery is terminal; the outbox fields stay untouched.
        """
        try:
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValidationError("limit must be an integer") from exc
        events = sorted(
            (dict(row) for row in
             self.ctx.repo.list("event", workspace_id)),
            key=lambda row: (str(row.get("ts") or ""),
                             str(row.get("id") or "")),
        )
        pending = [row for row in events
                   if not row.get("webhook_delivered_at")]
        delivered = failed = skipped = throttled = dead_lettered = 0
        errors: list[str] = []
        hooks_cache: dict[str, list[dict]] = {}
        attempted = 0
        for event in pending:
            # ``limit`` bounds deliveries, not the scan: an old event with no
            # matching hook must not starve a newer one forever.  Events that
            # are only skipped remain pending for a later subscription.
            if attempted >= max(0, limit):
                break
            target_workspace = str(
                event.get("workspace_id") or workspace_id or "")
            event_id = str(event.get("event_id") or event.get("id") or "")
            event_type = str(event.get("type") or "")
            hooks = hooks_cache.get(target_workspace)
            if hooks is None:
                hooks = self._rows(target_workspace)
                hooks_cache[target_workspace] = hooks
            matching = [hook for hook in hooks
                        if hook.get("active", True)
                        and event_type in (hook.get("event_types") or [])]
            if not matching:
                # No subscriber is not a delivery; leave the event pending so a
                # later subscription can still receive it.
                skipped += 1
                continue
            attempted += 1
            delivery_actor = actor or str(event.get("actor") or "system")
            body = None
            all_terminal = True
            for hook in matching:
                hook_id = str(hook.get("id") or "")
                delivery = self.ledger.get_or_create(
                    target_workspace, hook_id, event)
                if delivery.get("status") in ("delivered", "dead"):
                    continue
                now = self._clock()
                allowed, hook_row = self._reserve_quota(
                    target_workspace, hook, now)
                if not allowed:
                    # Quota exhausted (or the hook vanished mid-flight): keep
                    # the delivery pending and do not touch the transport.
                    if hook_row is not None:
                        throttled += 1
                    all_terminal = False
                    continue
                claimed = self.ledger.claim(
                    target_workspace, str(delivery["id"]), self.worker_id,
                    self.lease_seconds)
                if claimed is None:
                    # Another worker holds a live lease, or the retry backoff
                    # has not elapsed: leave the event pending.
                    all_terminal = False
                    continue
                if body is None:
                    masked_event = self.mask_event(
                        target_workspace, delivery_actor, event)
                    body = json.dumps(
                        masked_event, ensure_ascii=False, sort_keys=True,
                        default=str).encode("utf-8")
                status: int | None = None
                try:
                    status = self._deliver(
                        hook_row or hook, event_type, event_id, body)
                    if status >= 400:
                        raise RuntimeError(f"HTTP {status}")
                except Exception as exc:
                    failed += 1
                    message = str(exc) or type(exc).__name__
                    errors.append(f"{event_id}:{hook_id}: {message}")
                    max_attempts = _positive_int(
                        (hook_row or hook).get("max_attempts"),
                        "max_attempts", DEFAULT_MAX_ATTEMPTS)
                    updated = self.ledger.finish(
                        target_workspace, str(delivery["id"]),
                        delivered=False, status_code=status, error=message,
                        max_attempts=max_attempts,
                        backoff_base_seconds=self.backoff_base_seconds)
                    if updated is not None \
                            and updated.get("status") == "dead":
                        dead_lettered += 1
                    else:
                        # Still retriable: the event owes this hook, so it
                        # must not get the terminal marker yet.
                        all_terminal = False
                    self._record_delivery(hook, target_workspace, ok=False,
                                          error=message, event_id=event_id,
                                          event_type=event_type,
                                          actor=delivery_actor)
                else:
                    delivered += 1
                    self.ledger.finish(
                        target_workspace, str(delivery["id"]),
                        delivered=True, status_code=status)
                    self._record_delivery(hook, target_workspace, ok=True,
                                          error="", event_id=event_id,
                                          event_type=event_type,
                                          actor=delivery_actor)
            if all_terminal:
                # Independent marker: keep the transactional outbox contract
                # (dispatched_at / attempts / next_attempt_at) intact.  Only
                # written after *every* matching delivery is terminal.
                self._mark_event_delivered(target_workspace, event)
        result = {"delivered": delivered, "failed": failed,
                  "skipped": skipped, "errors": errors}
        if throttled:
            result["throttled"] = throttled
        if dead_lettered:
            result["dead_lettered"] = dead_lettered
        return result

    def _deliver(self, hook: Mapping[str, Any], event_type: str,
                 event_id: str, body: bytes) -> int:
        url = _validate_url(hook.get("url"))
        secret = str(hook.get("secret") or "")
        signature = "sha256=" + hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Onto-Event": event_type,
            "X-Onto-Event-Id": event_id,
            "X-Onto-Signature": signature,
            "User-Agent": "onto-platform-webhook/1",
        }
        return _status_code(self.transport(url, body, headers, self.timeout))

    def _record_delivery(self, hook: Mapping[str, Any], workspace_id: str, *,
                         ok: bool, error: str, event_id: str, event_type: str,
                         actor: str) -> None:
        hook_id = str(hook.get("id") or "")
        updated = False
        # CAS so a concurrent quota reservation cannot be lost.
        for _ in range(8):
            got = self.ctx.repo.get_versioned(
                WEBHOOK_KIND, workspace_id, hook_id)
            if got is None:  # deleted mid-flight: nothing to update
                return
            current, version = got
            current = deepcopy(dict(current))
            now = self._clock()
            if ok:
                current["delivery_count"] = int(
                    current.get("delivery_count") or 0) + 1
                current["last_delivery_at"] = now
                current["last_error"] = None
            else:
                current["last_error"] = error[:500]
            try:
                self.ctx.repo.put_if_version(
                    WEBHOOK_KIND, workspace_id, hook_id, current, version)
            except ConcurrencyConflictError:
                continue
            updated = True
            break
        if not updated:  # pragma: no cover - extreme contention
            return
        self._audit(actor, workspace_id,
                    "webhook.delivered" if ok else "webhook.delivery_failed",
                    event_id,
                    {"webhook_id": hook_id, "event_type": event_type,
                     "error": None if ok else error[:500]})


__all__ = [
    "DEFAULT_WEBHOOK_TIMEOUT",
    "ExportService",
    "Transport",
    "WEBHOOK_KIND",
    "WebhookService",
]
