"""Release / Deployment (plan-v2 1.1, ADR-3).

A Release is an immutable artifact: the exact compiled schema payload that
was accepted by the runtime, plus the schema version and the capability set.
A Deployment is a mutable *pointer* (``deployment/current``) to one Release.
Publishing cuts a Release and moves the pointer; rolling back moves the
pointer to the previous Release - no re-compilation, no partial state.
"""

from __future__ import annotations

import hashlib
import json

from ..errors import NotFoundError, ValidationError
from ..ports.runtime import SchemaPayload
from .common import ApplicationContext
from .events import EventLog

DEPLOYMENT_ID = "current"
CANARY_ID = "canary"


def schema_fingerprint(schema: dict) -> str:
    """Stable identity of a compiled schema (plan-v2 2.5).

    Two releases with the same fingerprint describe exactly the same runtime
    contract, so a deployment can be verified without trusting version
    counters alone.
    """
    canonical = json.dumps(schema, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _payload_problem(payload: dict | None) -> str | None:
    if not payload:
        return "workspace has no published schema payload to release"
    if not isinstance(payload.get("schema"), dict):
        return "release payload has no compiled schema"
    return None


def cut_release(ctx: ApplicationContext, workspace_id: str, actor: str,
                note: str = "") -> dict:
    """Snapshot the current published schema as a new immutable Release."""
    ontology = ctx.repo.get("ontology", workspace_id, _ontology_id(ctx, workspace_id))
    payload = ctx.repo.get("schema_payload", workspace_id, workspace_id)
    problem = _payload_problem(payload)
    if problem:
        raise ValidationError(problem)
    current = ctx.repo.get("deployment", workspace_id, DEPLOYMENT_ID) or {}
    release = {
        "id": f"rel_{ctx.id_factory()}",
        "workspace_id": workspace_id,
        "schema_version": (ontology or {}).get("schema_version"),
        "fingerprint": schema_fingerprint(payload["schema"]),
        "schema": payload["schema"],
        "bindings": payload.get("bindings", {}),
        "workflows": payload.get("workflows", {}),
        "capability_ids": sorted(
            c["id"] for c in ctx.repo.list("capability", workspace_id)
            if c.get("lifecycle") == "published"),
        "previous_release_id": current.get("release_id"),
        "created_at": ctx.clock(),
        "actor": actor,
        "note": note,
    }
    ctx.repo.put("release", workspace_id, release["id"], release)
    return release


def _ontology_id(ctx: ApplicationContext, workspace_id: str) -> str:
    rows = ctx.repo.list("ontology", workspace_id)
    if not rows:
        raise ValidationError("workspace has no ontology to release")
    active = [r for r in rows if r.get("lifecycle") != "deprecated"] or rows
    return max(active, key=lambda r: int(r.get("version") or 0))["id"]
    if not rows:
        raise NotFoundError(f"workspace {workspace_id!r} has no ontology")
    return rows[0]["id"]


def deploy_release(ctx: ApplicationContext, workspace_id: str, actor: str,
                   release_id: str, *, action: str = "release.deployed",
                   ring: str = DEPLOYMENT_ID) -> dict:
    """Point a runtime ring (current/canary) at a Release.

    Installing the payload and moving the pointer are separate steps: the
    kernel either accepts the payload or the call fails before any pointer
    moves.  A canary deploy installs the runtime but leaves ``current``
    untouched until :meth:`ReleaseUseCases.promote_canary` moves the pointer.
    """
    release = ctx.repo.get("release", workspace_id, release_id)
    if release is None:
        raise NotFoundError(f"release not found: {release_id!r}")
    previous = ctx.repo.get("deployment", workspace_id, ring) or {}
    if ring == CANARY_ID:
        # a canary is a shadow of the serving ring, so its base is the
        # current deployment, not an older canary
        previous = ctx.repo.get("deployment", workspace_id,
                                DEPLOYMENT_ID) or {}
    pending = {
        "id": ring,
        "workspace_id": workspace_id,
        "release_id": release["id"],
        "schema_version": release.get("schema_version"),
        "fingerprint": schema_fingerprint(release["schema"]),
        "base_release_id": previous.get("release_id"),
        "status": "pending",
        "checked_at": None,
        "deployed_at": ctx.clock(),
        "actor": actor,
    }
    with ctx.transaction():
        ctx.repo.put("deployment", workspace_id, ring, pending)
        EventLog(ctx).emit(
            "deployment.pending", workspace_id, actor, "deployment", ring,
            payload={"release_id": release["id"],
                     "base_release_id": pending["base_release_id"]},
        )
    # the ring owns its own kernel: deploying a canary must not publish into
    # the serving runtime (P0-5)
    gateway = ctx.gateway_for(workspace_id, ring=ring)
    try:
        # kernel accepts or rejects the payload; the pointer only becomes
        # active on success
        gateway.publish_schema(SchemaPayload(
            schema=release["schema"],
            bindings=release.get("bindings", {}),
            workflows=release.get("workflows", {}),
        ))
    except Exception as exc:
        # restore the previous runtime immediately, not just the pointer
        try:
            if pending["base_release_id"]:
                base = ctx.repo.get("release", workspace_id,
                                    pending["base_release_id"])
                if base is not None:
                    gateway.publish_schema(SchemaPayload(
                        schema=base["schema"],
                        bindings=base.get("bindings", {}),
                        workflows=base.get("workflows", {}),
                    ))
        except Exception:
            pass
        failed = {**pending, "status": "failed",
                  "last_error": f"{type(exc).__name__}: {exc}",
                  "failed_at": ctx.clock()}
        try:
            if pending["base_release_id"]:
                # the serving pointer must never point at an uncommitted
                # release: restore the previous active deployment
                restored = {**failed, "release_id": pending["base_release_id"],
                            "status": "active", "restored_at": ctx.clock()}
                restored.pop("last_error", None)
                ctx.repo.put("deployment", workspace_id, ring, restored)
            else:
                ctx.repo.put("deployment", workspace_id, ring, failed)
            EventLog(ctx).emit(
                "deployment.failed", workspace_id, actor, "deployment", ring,
                payload={"release_id": release["id"],
                         "base_release_id": pending["base_release_id"],
                         "error": failed["last_error"]},
            )
        finally:
            raise
    deployment = {
        **pending,
        "status": "active",
        "state_mode": ctx.gateways.state_mode(workspace_id, ring),
        "activated_at": ctx.clock(),
    }
    if ring == CANARY_ID:
        deployment["promoted"] = False
    with ctx.transaction():
        ctx.repo.put("deployment", workspace_id, ring, deployment)
        EventLog(ctx).emit(
            action, workspace_id, actor, "deployment", ring,
            payload={"release_id": release["id"],
                     "schema_version": release.get("schema_version"),
                     "ring": ring,
                     "state_mode": deployment["state_mode"]},
        )
    return {"release": release, "deployment": deployment,
            "runtime": gateway.describe(), "isolated": ring == CANARY_ID,
            "state_mode": deployment["state_mode"]}


def current_release(ctx: ApplicationContext, workspace_id: str) -> dict | None:
    deployment = ctx.repo.get("deployment", workspace_id, DEPLOYMENT_ID)
    if not deployment:
        return None
    return ctx.repo.get("release", workspace_id, deployment["release_id"])


def canary_status(ctx: ApplicationContext, workspace_id: str) -> dict:
    """Shadow-verify the canary ring before it is allowed to serve.

    This is deliberately a *smoke* check of the staged kernel, not real user
    traffic: the canary is an isolated ring, so the honest claim is "the
    release builds and exposes the released functions", never "it served
    traffic well" (P0-5).
    """
    canary = ctx.repo.get("deployment", workspace_id, CANARY_ID)
    checks: list[dict] = []
    if not canary:
        return {"ok": False, "checks": [
            {"name": "canary_deployed", "ok": False,
             "detail": "no canary deployment"}]}
    release = ctx.repo.get("release", workspace_id, canary.get("release_id"))
    checks.append({
        "name": "release_exists", "ok": release is not None,
        "detail": canary.get("release_id"),
    })
    if release is None:
        return {"ok": False, "checks": checks}
    gateway = ctx.gateway_for(workspace_id, ring=CANARY_ID)
    try:
        described = gateway.describe()
        raw_functions = described.get("functions") or {}
        if isinstance(raw_functions, dict):
            functions = set(raw_functions)
        else:
            functions = {
                f.get("name") for f in raw_functions
                if isinstance(f, dict) and f.get("name")
            }
    except Exception as exc:
        checks.append({"name": "canary_describe", "ok": False,
                       "detail": f"{type(exc).__name__}: {exc}"})
        return {"ok": False, "checks": checks}
    checks.append({"name": "canary_describe", "ok": True,
                   "detail": f"{len(functions)} functions"})
    expected = _release_functions(ctx, workspace_id, release)
    missing = sorted(name for name in expected if name not in functions)
    checks.append({
        "name": "released_functions_present", "ok": not missing,
        "detail": {"expected": sorted(expected), "missing": missing},
    })
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "release_id": release["id"],
            "fingerprint": release.get("fingerprint")}


def _release_functions(ctx: ApplicationContext, workspace_id: str,
                       release: dict) -> set[str]:
    names: set[str] = set()
    for capability in ctx.repo.list("capability", workspace_id):
        if capability.get("id") in (release.get("capability_ids") or []):
            key = capability.get("key")
            target = capability.get("target_type")
            if key and target:
                names.add(f"{target}.{key}")
    return names


def list_releases(ctx: ApplicationContext, workspace_id: str) -> list[dict]:
    releases = ctx.repo.list("release", workspace_id)
    return sorted(releases, key=lambda r: r.get("created_at") or "")


class ReleaseUseCases:
    """Release = cut/immutable artifact; Deployment = pointer."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx
        self._events = EventLog(ctx)

    def cut(self, workspace_id: str, actor: str, note: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        with self._ctx.transaction():
            release = cut_release(self._ctx, workspace_id, actor, note)
            self._events.emit(
                "release.cut", workspace_id, actor, "release", release["id"],
                payload={"schema_version": release.get("schema_version")},
            )
        return release

    def deploy(self, workspace_id: str, actor: str, release_id: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "release.deploy")
        return deploy_release(self._ctx, workspace_id, actor, release_id)

    def canary(self, workspace_id: str, actor: str, release_id: str) -> dict:
        """Stage a release on the canary ring without moving ``current``."""
        self._ctx.require_action(actor, workspace_id, "release.deploy")
        result = deploy_release(
            self._ctx, workspace_id, actor, release_id,
            action="release.canary_deployed", ring=CANARY_ID)
        # a canary deployment must not disturb the serving pointer
        return {**result,
                "current": self._ctx.repo.get("deployment", workspace_id,
                                              DEPLOYMENT_ID)}

    def check_canary(self, workspace_id: str, actor: str) -> dict:
        from .release_health import ReleaseHealth

        self._ctx.require_action(actor, workspace_id, "release.deploy")
        return ReleaseHealth(self._ctx).check(workspace_id, actor)

    def canary_check(self, workspace_id: str, actor: str) -> dict | None:
        from .release_health import latest_check

        self._ctx.require(actor, workspace_id, "viewer")
        return latest_check(self._ctx, workspace_id)

    def promote_canary(self, workspace_id: str, actor: str,
                       require_healthy: bool = True) -> dict:
        """Move ``current`` to the canary release (the explicit canary gate)."""
        from .release_health import require_fresh_check
        from .slo import ServiceLevels

        self._ctx.require_action(actor, workspace_id, "release.deploy")
        canary = self._ctx.repo.get("deployment", workspace_id, CANARY_ID)
        if not canary:
            raise ValidationError("workspace has no canary deployment to promote")
        check = require_fresh_check(self._ctx, workspace_id)
        if check.get("release_id") != canary.get("release_id"):
            raise ValidationError(
                "cannot promote canary: the shadow check belongs to a "
                "different release")
        smoke = canary_status(self._ctx, workspace_id)
        if not smoke["ok"]:
            failed = [c["name"] for c in smoke["checks"] if not c["ok"]]
            raise ValidationError(
                "cannot promote canary: shadow verification failed: "
                + ", ".join(failed))
        if require_healthy:
            health = ServiceLevels(self._ctx).evaluate(workspace_id)
            success = health["indicators"].get("success_rate") or {}
            # an empty window has no evidence either way; the shadow check
            # remains the gate.  Once there is data, a breach blocks.
            if success.get("status") not in ("no_data",) and not health["ok"]:
                breached = [k for k, v in health["indicators"].items()
                            if v["status"] in ("breach", "no_data",
                                               "insufficient")]
                raise ValidationError(
                    "cannot promote canary while SLO is breached: "
                    + ", ".join(breached))
        previous = self._ctx.repo.get("deployment", workspace_id,
                                      DEPLOYMENT_ID) or {}
        previous_release_id = previous.get("release_id")
        try:
            result = deploy_release(
                self._ctx, workspace_id, actor, canary["release_id"],
                action="release.canary_promoted")
        except Exception:
            # compensating rollback: leave the serving pointer on the old
            # release and rebuild its runtime immediately (P0-5)
            if previous_release_id:
                try:
                    deploy_release(self._ctx, workspace_id, actor,
                                   previous_release_id,
                                   action="release.canary_promotion_failed")
                except Exception:
                    pass
            raise
        canary["promoted"] = True
        canary["promoted_at"] = self._ctx.clock()
        with self._ctx.transaction():
            self._ctx.repo.put("deployment", workspace_id, CANARY_ID, canary)
        return {**result, "canary": canary, "canary_check": smoke}

    def rollback(self, workspace_id: str, actor: str,
                 to_release_id: str | None = None) -> dict:
        self._ctx.require_action(actor, workspace_id, "release.rollback")
        current = self._ctx.repo.get("deployment", workspace_id, DEPLOYMENT_ID)
        target = to_release_id or (current or {}).get("release_id")
        if target is None:
            raise ValidationError("workspace has no deployment to roll back")
        release = self._ctx.repo.get("release", workspace_id, target)
        if release is None:
            raise NotFoundError(f"release not found: {target!r}")
        previous = release.get("previous_release_id")
        if not previous:
            raise ValidationError("release has no previous release to roll back to")
        return deploy_release(self._ctx, workspace_id, actor, previous,
                              action="release.rolled_back")

    def list(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        deployment = self._ctx.repo.get("deployment", workspace_id, DEPLOYMENT_ID)
        canary = self._ctx.repo.get("deployment", workspace_id, CANARY_ID)
        return {"releases": list_releases(self._ctx, workspace_id),
                "deployment": deployment, "canary": canary}
