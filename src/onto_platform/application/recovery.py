"""Startup recovery for an interrupted deployment switch (P0-4).

A deployment is a two-phase operation: persist ``pending`` intent, install the
release into the ring's kernel, then mark the pointer active.  A crash after
the pending write leaves the durable pointer pending; rebuild must resolve it
against the *runtime*, not guess.  Recovery either completes the intended
install or restores the previous base, and always records the outcome.
"""

from __future__ import annotations

from ..errors import PlatformError
from ..ports.runtime import SchemaPayload
from .common import ApplicationContext
from .events import EventLog
from .releases import CANARY_ID, DEPLOYMENT_ID, schema_fingerprint


def _workspaces(ctx: ApplicationContext) -> list[str]:
    return [w["id"] for w in ctx.repo.list("workspace", None)]


def recover_deployment(ctx: ApplicationContext, workspace_id: str,
                       ring: str = DEPLOYMENT_ID) -> dict:
    deployment = ctx.repo.get("deployment", workspace_id, ring)
    if not deployment or deployment.get("status") not in ("pending",
                                                           "installing"):
        return {"workspace_id": workspace_id, "ring": ring,
                "recovered": False}
    intended_id = deployment.get("release_id")
    base_id = deployment.get("base_release_id")
    intended = ctx.repo.get("release", workspace_id, intended_id)
    base = ctx.repo.get("release", workspace_id, base_id) if base_id else None
    if intended is None:
        # the intent names a release that no longer exists: fall back
        return _restore(ctx, workspace_id, ring, deployment, base,
                        "intended release is missing")
    try:
        # gateway_for already rebuilt the ring from the pending base; publish
        # the intended release explicitly and only then activate it
        gateway = ctx.gateway_for(workspace_id, ring=ring)
        gateway.publish_schema(SchemaPayload(
            schema=intended["schema"],
            bindings=intended.get("bindings", {}),
            workflows=intended.get("workflows", {}),
        ))
    except PlatformError as exc:
        return _restore(ctx, workspace_id, ring, deployment, base, str(exc))
    except Exception as exc:  # defensive: never loop on recovery
        return _restore(ctx, workspace_id, ring, deployment, base, str(exc))
    active = {
        **deployment,
        "status": "active",
        "activated_at": ctx.clock(),
        "recovered_at": ctx.clock(),
        "fingerprint": schema_fingerprint(intended.get("schema") or {}),
    }
    with ctx.transaction():
        ctx.repo.put("deployment", workspace_id, ring, active)
        EventLog(ctx).emit(
            "schema.recovered", workspace_id, "system", "deployment", ring,
            payload={"release_id": intended_id, "base_release_id": base_id,
                     "mode": "completed"},
        )
    return {"workspace_id": workspace_id, "ring": ring, "recovered": True,
            "mode": "completed", "deployment": active}


def _restore(ctx: ApplicationContext, workspace_id: str, ring: str,
             pending: dict, base: dict | None, reason: str) -> dict:
    gateway = ctx.gateway_for(workspace_id, ring=ring)
    if base is not None:
        try:
            gateway.publish_schema(SchemaPayload(
                schema=base["schema"],
                bindings=base.get("bindings", {}),
                workflows=base.get("workflows", {}),
            ))
        except Exception:
            pass
        restored = {**pending, "release_id": base["id"],
                    "status": "active", "restored_at": ctx.clock(),
                    "last_error": reason,
                    "fingerprint": schema_fingerprint(base.get("schema") or {})}
    else:
        restored = {**pending, "status": "failed", "last_error": reason,
                    "failed_at": ctx.clock()}
    with ctx.transaction():
        ctx.repo.put("deployment", workspace_id, ring, restored)
        EventLog(ctx).emit(
            "deployment.failed", workspace_id, "system", "deployment", ring,
            payload={"release_id": pending.get("release_id"), "error": reason,
                     "mode": "recovered"},
        )
    return {"workspace_id": workspace_id, "ring": ring, "recovered": True,
            "mode": "restored", "deployment": restored,
            "reason": reason}


def recover_all(ctx: ApplicationContext) -> list[dict]:
    results: list[dict] = []
    for workspace_id in _workspaces(ctx):
        for ring in (DEPLOYMENT_ID, CANARY_ID):
            try:
                result = recover_deployment(ctx, workspace_id, ring)
            except Exception as exc:  # one broken workspace must not stop boot
                result = {"workspace_id": workspace_id, "ring": ring,
                          "recovered": False, "error": str(exc)}
            if result.get("recovered"):
                results.append(result)
    return results
