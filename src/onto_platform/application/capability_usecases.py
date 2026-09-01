"""Capability use cases: define, publish, invoke (design.txt §17-§20)."""

from __future__ import annotations

from ..domain.capability.model import (
    Capability,
    CapabilityInput,
    Implementation,
    validate_capability,
)
from ..domain.governance.model import transition
from ..errors import ValidationError
from .common import ApplicationContext


class CreateCapability:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(
        self,
        workspace_id: str,
        actor: str,
        key: str,
        name: str,
        target_type: str,
        description: str = "",
        inputs: list[dict] | None = None,
        implementation: dict | None = None,
    ) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        capability = Capability(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            key=key,
            name=name,
            target_type=target_type,
            description=description,
            inputs=[CapabilityInput.from_dict(i) for i in (inputs or [])],
            implementation=Implementation.from_dict(implementation or {"kind": "python"}),
            created_at=self._ctx.clock(),
            updated_at=self._ctx.clock(),
        )
        problems = validate_capability(capability)
        if problems:
            raise ValidationError("capability validation failed: " + "; ".join(problems))
        # the target object type must exist in the workspace ontology
        ontologies = self._ctx.repo.list("ontology", workspace_id)
        if not ontologies or target_type not in ontologies[0].get("objects", {}):
            raise ValidationError(
                f"target object type {target_type!r} is not defined in the ontology"
            )
        self._ctx.save("capability", capability.to_dict())
        self._ctx.audit_event(actor, workspace_id, "capability.create", capability.id,
                              {"function": capability.function_name})
        return capability.to_dict()


class PublishCapability:
    def __init__(self, ctx: ApplicationContext, publisher) -> None:
        self._ctx = ctx
        self._publisher = publisher

    def __call__(self, workspace_id: str, capability_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data = self._ctx.must_get("capability", workspace_id, capability_id)
        capability = Capability.from_dict(data)

        problems = validate_capability(capability)
        if problems:
            raise ValidationError("capability validation failed: " + "; ".join(problems))

        # materialize inline python source -> entrypoint file
        impl = capability.implementation
        if impl.kind == "python" and impl.source and not impl.entrypoint:
            entrypoint = self._ctx.code_store.put(
                f"ws{workspace_id}/{capability.function_name}", impl.source
            )
            capability.implementation = Implementation(
                kind="python", source=impl.source, entrypoint=entrypoint,
                steps=impl.steps,
            )

        if capability.lifecycle == "draft":
            capability.lifecycle = transition(capability.lifecycle, "validate")
        capability.lifecycle = transition(capability.lifecycle, "publish")
        capability.updated_at = self._ctx.clock()
        self._ctx.save("capability", capability.to_dict())

        describe = self._publisher.publish(workspace_id, actor, action="capability.publish")
        return {
            "capability": capability.to_dict(),
            "runtime": describe,
        }


class ListCapabilities:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("capability", workspace_id)


class InvokeCapability:
    """The runtime door used by applications and agents.

    Agent -> Capability -> RuntimeGateway -> Kernel (design.txt §28/§47 原则五).
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, function: str,
                 target: str | None = None, args: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        gateway = self._ctx.gateway_for(workspace_id)
        result = gateway.invoke(function, target=target, args=args)
        self._ctx.audit_event(
            actor, workspace_id, "capability.invoke", function,
            {"target": target, "ok": result.ok, "error": result.error,
             "changes": list(result.changes)},
        )
        return {
            "execution_id": result.execution_id,
            "output": result.output,
            "changes": list(result.changes),
            "error": result.error,
            "ok": result.ok,
        }
