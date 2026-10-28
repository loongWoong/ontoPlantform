"""Shadow verification for the isolated canary ring (P0-5).

The canary is not real traffic.  Promotion is gated on a *fresh* check of the
staged kernel: the released functions exist, the object types match, a sample
query works, and (when the state was cloned) sample counts are comparable.
A check is a persisted document with an explicit timestamp so an old or
missing check cannot be mistaken for a healthy one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..errors import GateError
from .common import ApplicationContext
from .releases import (CANARY_ID, DEPLOYMENT_ID, canary_status,
                       schema_fingerprint)

#: a promotion gate accepts a check no older than this
MAX_CHECK_AGE_SECONDS = 600


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def latest_check(ctx: ApplicationContext, workspace_id: str) -> dict | None:
    rows = [r for r in ctx.repo.list("canary_check", workspace_id)
            if r.get("release_id")]
    if not rows:
        return None
    return max(rows, key=lambda r: r.get("checked_at") or "")


def require_fresh_check(ctx: ApplicationContext, workspace_id: str,
                        max_age_seconds: int = MAX_CHECK_AGE_SECONDS) -> dict:
    check = latest_check(ctx, workspace_id)
    if check is None:
        raise GateError(
            "cannot promote canary: no shadow verification check exists")
    if not check.get("ok"):
        raise GateError(
            "cannot promote canary: the latest shadow verification failed")
    checked = _parse(check.get("checked_at"))
    now = _parse(ctx.clock()) or datetime.now(timezone.utc)
    if checked is None or (now - checked).total_seconds() > max_age_seconds:
        raise GateError(
            "cannot promote canary: the shadow verification is stale")
    return check


class ReleaseHealth:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def check(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        canary = self._ctx.repo.get("deployment", workspace_id, CANARY_ID)
        if not canary:
            raise GateError("workspace has no canary deployment to verify")
        release = self._ctx.repo.get("release", workspace_id,
                                     canary.get("release_id"))
        if release is None:
            raise GateError("canary deployment points to a missing release")
        smoke = canary_status(self._ctx, workspace_id)
        checks = list(smoke.get("checks") or [])
        gateway = self._ctx.gateway_for(workspace_id, ring=CANARY_ID)
        current = self._ctx.gateway_for(workspace_id, ring=DEPLOYMENT_ID)
        try:
            described = gateway.describe()
            object_types = set((described.get("objects") or {}).keys()) \
                if isinstance(described.get("objects"), dict) else {
                    o.get("name") for o in described.get("objects") or []
                    if isinstance(o, dict)}
            current_types = set(current.describe().get("objects") or {}) \
                if isinstance(current.describe().get("objects"), dict) else {
                    o.get("name") for o in
                    current.describe().get("objects") or []
                    if isinstance(o, dict)}
            checks.append({
                "name": "object_types_match",
                "ok": object_types == current_types,
                "detail": {"canary": sorted(t for t in object_types if t),
                           "current": sorted(t for t in current_types if t)},
            })
        except Exception as exc:
            checks.append({"name": "object_types_match", "ok": False,
                           "detail": f"{type(exc).__name__}: {exc}"})
        state_mode = canary.get("state_mode") or self._ctx.gateways.state_mode(
            workspace_id, CANARY_ID)
        try:
            probe = self._probe(gateway)
            checks.append({"name": "read_probe", "ok": probe["ok"],
                           "detail": probe})
            if state_mode == "cloned":
                counts = {
                    "canary": _count(gateway),
                    "current": _count(current),
                }
                checks.append({
                    "name": "state_sample_count",
                    "ok": counts["canary"] == counts["current"],
                    "detail": counts,
                })
        except Exception as exc:
            checks.append({"name": "read_probe", "ok": False,
                           "detail": f"{type(exc).__name__}: {exc}"})
        result = {
            "id": f"chk_{self._ctx.id_factory()}",
            "workspace_id": workspace_id,
            "release_id": release["id"],
            "base_release_id": canary.get("base_release_id"),
            "fingerprint": schema_fingerprint(release.get("schema") or {}),
            "state_mode": state_mode,
            "ok": all(c["ok"] for c in checks),
            "checks": checks,
            "checked_at": self._ctx.clock(),
            "actor": actor,
        }
        with self._ctx.transaction():
            self._ctx.save("canary_check", result)
            self._ctx.audit_event(actor, workspace_id, "canary.checked",
                                  release["id"],
                                  {"ok": result["ok"],
                                   "checks": [c["name"] for c in checks]})
        return result

    def _probe(self, gateway) -> dict:
        described = gateway.describe()
        objects = described.get("objects") or {}
        if isinstance(objects, dict):
            names = sorted(objects)
        else:
            names = sorted({o.get("name") for o in objects
                            if isinstance(o, dict) and o.get("name")})
        sampled: list[dict] = []
        for object_type in names:
            found = gateway.find_objects(object_type, report=True)
            sampled.extend(found.get("objects") or [])
            if sampled:
                break
        return {"ok": True, "objects_sampled": len(sampled),
                "has_object": bool(sampled)}


def _count(gateway) -> int:
    total = 0
    described = gateway.describe()
    objects = described.get("objects") or {}
    if isinstance(objects, dict):
        names = sorted(objects)
    else:
        names = sorted({o.get("name") for o in objects
                        if isinstance(o, dict) and o.get("name")})
    for name in names:
        if not name:
            continue
        try:
            total += len(gateway.find_objects(name, report=True)
                         .get("objects") or [])
        except Exception:
            continue
    return total
