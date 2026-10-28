"""Capability: the platform's world of verbs (design.txt §17-§20).

    Function   = 原子执行 (Kernel)
    Workflow   = 能力编排 (Platform)
    Capability = 业务能力 (Platform, 用户看到的)
    Tool       = Agent 接口 (协议形式)

A Capability is implemented by ONE of:
    - a single python function  (kind="python")
    - a workflow of steps       (kind="workflow")
    - an LLM agent run          (kind="agent", design.txt §19 Agent/AI+Function)

Users only ever see the capability; workflow DAGs, agent loops and
function bindings stay inside the platform (design.txt §19/§42 第二原则).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...errors import ValidationError

IMPLEMENTATION_KINDS = ("python", "workflow", "agent")

# Workflow step kinds (v0.1): observation, invocation, tiny reductions and
# one bounded mutation primitive (set_property -> gateway.apply_delta).
# Everything a workflow does goes through the RuntimeGateway - never the
# kernel store directly (design.txt §15/§49).
STEP_KINDS = ("invoke", "find", "related", "get", "count", "set", "set_property")

_STEP_REQUIRED = {
    "invoke": ("function",),
    "find": ("object_type",),
    "related": ("ref", "relation"),
    "get": ("ref",),
    "count": ("list",),
    "set": ("name", "value"),
    "set_property": ("ref", "properties"),
}


@dataclass(frozen=True, slots=True)
class Implementation:
    """How a capability is realized."""

    kind: str
    source: str | None = None      # python: inline source code
    entrypoint: str | None = None  # python: materialized path (set at publish)
    steps: tuple[dict, ...] = ()   # workflow: ordered step specs
    goal: str = ""                 # agent: the goal the LLM pursues

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "source": self.source,
            "entrypoint": self.entrypoint,
            "steps": [dict(s) for s in self.steps],
            "goal": self.goal,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Implementation":
        return cls(
            kind=data.get("kind", "python"),
            source=data.get("source"),
            entrypoint=data.get("entrypoint"),
            steps=tuple(dict(s) for s in data.get("steps", [])),
            goal=data.get("goal", ""),
        )


@dataclass(slots=True)
class CapabilityInput:
    name: str
    type: str = "string"
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> "CapabilityInput":
        return cls(
            name=data["name"], type=data.get("type", "string"),
            description=data.get("description", ""),
        )


@dataclass(slots=True)
class Capability:
    """面向业务的能力抽象，例如 '异常车辆处置'。"""

    id: str
    workspace_id: str
    key: str                 # "recharge" -> kernel function "Vehicle.recharge"
    name: str                # 业务名 "车辆充值"
    target_type: str         # ObjectType name
    description: str = ""
    inputs: list[CapabilityInput] = field(default_factory=list)
    implementation: Implementation = field(default_factory=lambda: Implementation(kind="python"))
    #: Executable contract (plan-v2 1.4): pre/postconditions, declared
    #: effects, idempotency and compensation.  The verifier judges outcomes
    #: independently of the implementation; effects make the blast radius
    #: explicit instead of implicit.
    contract: dict = field(default_factory=dict)
    lifecycle: str = "draft"
    created_at: str = ""
    updated_at: str = ""

    @property
    def function_name(self) -> str:
        """Kernel function name: ``{target_type}.{key}``."""
        return f"{self.target_type}.{self.key}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "key": self.key,
            "name": self.name,
            "target_type": self.target_type,
            "description": self.description,
            "inputs": [i.to_dict() for i in self.inputs],
            "implementation": self.implementation.to_dict(),
            "contract": dict(self.contract),
            "lifecycle": self.lifecycle,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Capability":
        return cls(
            id=data["id"],
            workspace_id=data["workspace_id"],
            key=data["key"],
            name=data["name"],
            target_type=data["target_type"],
            description=data.get("description", ""),
            inputs=[CapabilityInput.from_dict(i) for i in data.get("inputs", [])],
            implementation=Implementation.from_dict(data.get("implementation", {})),
            contract=dict(data.get("contract") or {}),
            lifecycle=data.get("lifecycle", "draft"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------


CONTRACT_OPS = ("eq", "ne", "gte", "lte", "gt", "lt", "in",
                "changed", "unchanged", "eq_prev_plus")
COMPENSATION_KINDS = ("none", "python", "invoke", "workflow")


def validate_contract(contract: dict) -> list[str]:
    """Structural validation of an executable capability contract."""
    problems: list[str] = []
    if not isinstance(contract, dict):
        return ["contract must be an object"]
    allowed = {"preconditions", "postconditions", "effects", "idempotent",
               "compensate"}
    unknown = sorted(set(contract) - allowed)
    if unknown:
        problems.append(
            f"contract has unknown fields: {unknown}; allowed fields are "
            f"{sorted(allowed)}")
    for section in ("preconditions", "postconditions"):
        conditions = contract.get(section, [])
        if not isinstance(conditions, list):
            problems.append(f"contract.{section} must be a list")
            continue
        for i, cond in enumerate(conditions):
            where = f"contract.{section}[{i}]"
            if not isinstance(cond, dict):
                problems.append(f"{where} must be an object")
                continue
            if not isinstance(cond.get("property"), str) or not cond["property"]:
                problems.append(f"{where} needs a 'property'")
            if cond.get("op") not in CONTRACT_OPS:
                problems.append(
                    f"{where}.op {cond.get('op')!r} not in {list(CONTRACT_OPS)}")
            if cond.get("op") == "eq_prev_plus":
                value = cond.get("value")
                if not (isinstance(value, dict) and isinstance(value.get("args"), str)):
                    problems.append(f"{where}.value must be {{'args': '<input>'}}")
    effects = contract.get("effects", [])
    if not isinstance(effects, list) or any(not isinstance(e, str) for e in effects):
        problems.append("contract.effects must be a list of property paths")
    idempotent = contract.get("idempotent")
    if idempotent is not None and not isinstance(idempotent, bool):
        problems.append("contract.idempotent must be a boolean")
    compensate = contract.get("compensate")
    if compensate not in (None, ""):
        if not isinstance(compensate, dict):
            problems.append("contract.compensate must be an object")
        elif compensate.get("kind") not in COMPENSATION_KINDS:
            problems.append(
                f"contract.compensate.kind {compensate.get('kind')!r} "
                f"not in {list(COMPENSATION_KINDS)}")
        elif compensate.get("kind") == "invoke" and not compensate.get("function"):
            problems.append("contract.compensate needs a 'function'")
        elif compensate.get("kind") == "python" and not compensate.get("source"):
            problems.append("contract.compensate needs a 'source'")
    return problems


def validate_capability(capability: Capability) -> list[str]:
    problems: list[str] = []
    if not capability.key or set(capability.key) - set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
    ):
        problems.append(f"capability key {capability.key!r} is not an identifier")
    if capability.target_type and not capability.target_type.replace("_", "").isalnum():
        problems.append(f"target_type {capability.target_type!r} is not an object type name")
    impl = capability.implementation
    if impl.kind not in IMPLEMENTATION_KINDS:
        problems.append(
            f"implementation kind {impl.kind!r} not in {list(IMPLEMENTATION_KINDS)}"
        )
    elif impl.kind == "python" and not (impl.source or impl.entrypoint):
        problems.append("python implementation needs 'source' or 'entrypoint'")
    elif impl.kind == "workflow":
        problems.extend(validate_steps(list(impl.steps)))
    elif impl.kind == "agent" and not impl.goal.strip():
        problems.append("agent implementation needs a 'goal'")
    problems.extend(validate_contract(capability.contract or {}))
    return problems


def validate_steps(steps: list[dict]) -> list[str]:
    problems: list[str] = []
    for i, step in enumerate(steps):
        kind = step.get("kind")
        if kind not in STEP_KINDS:
            problems.append(
                f"step {i}: unknown kind {kind!r}, expected one of {list(STEP_KINDS)}"
            )
            continue
        for field_name in _STEP_REQUIRED[kind]:
            if field_name not in step:
                problems.append(f"step {i} ({kind}): missing {field_name!r}")
        if "as" in step and not isinstance(step["as"], str):
            problems.append(f"step {i}: 'as' must be a string alias")
    return problems
