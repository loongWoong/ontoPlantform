"""AgentRunner: Goal -> Understand -> Plan -> Act -> Observe -> Answer.

The agent is a tool-loop over the RuntimeGateway (design.txt §28):

    基础观察:  describe / describe_object / find / related / get
    业务行动:  invoke_capability  (Agent -> Capability -> Runtime -> Kernel)

The LLM decides; the platform executes.  The provider only needs to
return strict JSON: ``{"think": ..., "action": {tool, args}}`` or
``{"think": ..., "answer": ...}``.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..domain.intelligence.model import AgentRun, AgentStep
from ..errors import RuntimeUnavailableError
from ..ports.llm import LLMMessage, LLMProvider
from ..ports.runtime import RuntimeGateway

MAX_OBSERVATION_CHARS = 4000


@dataclass(slots=True)
class GoalSpec:
    """A machine-readable goal, not just a sentence.

    ``success_when`` is optional and deliberately narrow: only conditions the
    platform can check against the runtime count as verification.  A free
    text goal has no such condition, so its answer is reported as
    ``verified=False`` instead of being presented as proven.
    """

    goal: str
    success_when: dict = field(default_factory=dict)
    max_steps: int = 8


class GoalVerifier:
    def verify(self, spec: GoalSpec, run: AgentRun,
               gateway) -> dict:
        condition = spec.success_when or {}
        if not condition:
            return {"verified": False,
                    "reason": "no machine-checkable acceptance criterion"}
        try:
            ref = condition.get("object_ref")
            prop = condition.get("property")
            expected = condition.get("equals")
            if not ref or not prop:
                return {"verified": False,
                        "reason": "success_when needs object_ref and property"}
            obj = gateway.get_object(ref)
            actual = (obj or {}).get(prop)
            ok = actual == expected
            return {"verified": ok, "object_ref": ref, "property": prop,
                    "expected": expected, "actual": actual,
                    "reason": "acceptance criterion passed" if ok
                    else "acceptance criterion failed"}
        except Exception as exc:
            return {"verified": False,
                    "reason": f"verification could not run: {exc}"}


TOOLS_SPEC = """\
You may call these tools (one per turn):

1. {"tool": "describe_ontology", "args": {}}
   - index of object types / relations / functions
2. {"tool": "describe_object", "args": {"name": "<ObjectType>"}}
   - properties, relations and functions of one object type
3. {"tool": "find", "args": {"object_type": "<ObjectType>", "filter": {"prop__op": value}}}
   - filter operators: eq, ne, gt, gte, lt, lte, contains, in
4. {"tool": "related", "args": {"ref": "Type/Id", "relation": "<name>", "direction": "out|in"}}
5. {"tool": "get", "args": {"ref": "Type/Id"}}
6. {"tool": "invoke_capability", "args": {"function": "Type.capability", "target": "Type/Id", "args": {...}}}
   - business capabilities change the world; use them for actions
"""

SYSTEM_TEMPLATE = """\
You are the business agent of an ontology-driven platform.
You answer questions and perform actions through the business world's
ontology - never by guessing data.

# Business world (ontology + vocabulary)
{ontology_context}

# Runtime index
{runtime_index}

{tools}

Reply with STRICT JSON only, no markdown, no prose:
- to act:      {{"think": "<one sentence>", "action": {{"tool": "...", "args": {{...}}}}}}
- to finish:   {{"think": "<one sentence>", "answer": "<final answer in the user's language>"}}
"""


class AgentRunner:
    def __init__(self, gateway: RuntimeGateway, provider: LLMProvider,
                 ontology_context: str, max_steps: int = 8,
                 forbidden_functions=(), invoker=None) -> None:
        self._gateway = gateway
        self._provider = provider
        self._ontology_context = ontology_context
        # bounded controller: the step budget is a hard stability limit,
        # never an unbounded value supplied by a request body
        self._max_steps = max(1, min(int(max_steps), 20))
        # an agent-kind capability must never offer itself as a tool: the
        # LLM would happily "implement" the goal by invoking its own
        # function again, recursing through the executor until the stack or
        # the token budget runs out
        self._forbidden = frozenset(forbidden_functions)
        # agent write actions go through the audited capability use case
        # (governance + execution record), not straight to the kernel
        self._invoker = invoker

    @property
    def max_steps(self) -> int:
        return self._max_steps

    def run(self, goal: str, spec: GoalSpec | None = None) -> AgentRun:
        run = AgentRun(goal=goal)
        started_monotonic = time.monotonic()
        run.run_id = f"run_{uuid.uuid4().hex[:12]}"
        run.started_at = datetime.now(timezone.utc).isoformat()
        usage: dict = {}
        spec = spec or GoalSpec(goal=goal, max_steps=self._max_steps)
        verifier = GoalVerifier()
        messages = [
            LLMMessage(
                role="system",
                content=SYSTEM_TEMPLATE.format(
                    ontology_context=self._ontology_context or "(not available)",
                    runtime_index=json.dumps(
                        self._safe_describe(), ensure_ascii=False
                    ),
                    tools=TOOLS_SPEC,
                ),
            ),
            LLMMessage(role="user", content=goal),
        ]
        invalid = 0
        attempts = 0
        while len(run.steps) < self._max_steps and attempts < self._max_steps + 3:
            attempts += 1
            try:
                response = self._provider.complete(messages)
            except TimeoutError:
                # a timed-out controller turn means the effect is unknown:
                # let the execution layer report tri-state, do not fake an
                # answer or a failure (K6)
                raise
            except Exception as exc:
                run.ok = False
                run.termination = "provider_error"
                run.answer = f"LLM provider failed: {exc}"
                return self._finish(run, started_monotonic, usage)
            parsed = _parse_json(response.content)
            reported = getattr(response, "usage", None)
            if isinstance(reported, dict):
                for key, value in reported.items():
                    if isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + value
            if parsed is None:
                # a non-JSON reply is a formatting miss, not a reasoning
                # step: it must not eat the step budget (a single slip used
                # to end the run with "max steps reached" and no answer)
                invalid += 1
                messages.append(LLMMessage(role="assistant", content=response.content))
                messages.append(
                    LLMMessage(
                        role="user",
                        content='Your reply was not valid JSON. Reply with strict JSON: '
                                '{"think": "...", "action": {...}} or {"think": "...", "answer": "..."}.',
                    )
                )
                if invalid >= 3:
                    run.ok = False
                    run.termination = "invalid_json"
                    run.answer = "LLM 连续 3 次未返回合法 JSON，无法继续推理"
                    return self._finish(run, started_monotonic, usage)
                continue
            invalid = 0
            thought = str(parsed.get("think", ""))
            if "answer" in parsed and not parsed.get("action"):
                run.answer = str(parsed["answer"])
                run.steps.append(
                    AgentStep(index=len(run.steps), thought=thought,
                              action={"tool": "finish"}, observation="")
                )
                # the LLM's prose is not evidence; only the declared
                # acceptance criterion can mark a run verified
                result = verifier.verify(spec, run, self._gateway)
                run.verification = result
                run.verified = bool(result.get("verified"))
                return self._finish(run, started_monotonic, usage)
            action = parsed.get("action") or {}
            observation = self._execute_tool(action)
            run.steps.append(
                AgentStep(index=len(run.steps), thought=thought, action=action,
                          observation=observation)
            )
            messages.append(LLMMessage(role="assistant", content=response.content))
            messages.append(
                LLMMessage(role="user", content=f"OBSERVATION:\n{observation}")
            )
        # step budget spent: the agent did not reach a final answer.  Asking
        # the same loop once more for prose would manufacture an unverified
        # answer, so report an explicit failure instead (P1 agent truth).
        run.ok = False
        run.termination = "max_steps"
        run.answer = (
            f"达到最大推理步数 {self._max_steps} 仍未得出可验证结论；"
            "本轮未完成，请缩小目标或提高步数预算")
        run.verification = {
            "verified": False,
            "reason": "step budget exhausted before a final answer",
        }
        return self._finish(run, started_monotonic, usage)

    def _finish(self, run: AgentRun, started_monotonic: float,
                usage: dict) -> AgentRun:
        run.finished_at = datetime.now(timezone.utc).isoformat()
        run.duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        # never invent token counts: null means the provider did not report
        run.usage = dict(usage) if usage else None
        return run

    def _force_answer(self, messages: list) -> str:
        prompt = list(messages) + [
            LLMMessage(role="user", content=(
                "STEP LIMIT REACHED. Do not call any tool. Reply with STRICT JSON only: "
                '{"think": "...", "answer": "<final answer in the user\'s language, '
                'based only on the observations above>"}'
            ))
        ]
        try:
            response = self._provider.complete(prompt)
        except TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as a failed run
            return f"LLM provider failed: {exc}"
        parsed = _parse_json(response.content)
        answer = str((parsed or {}).get("answer") or "").strip()
        if answer:
            return answer
        raw = str(response.content or "").strip()
        if parsed is None and raw and not raw.startswith("{"):
            return raw[:2000]  # prose answer still beats no answer at all
        return ""

    # ------------------------------------------------------------------

    def _safe_describe(self) -> dict:
        try:
            described = self._gateway.describe()
        except RuntimeUnavailableError:
            return {"error": "runtime not published"}
        return _hide_functions(described, self._forbidden)

    def _execute_tool(self, action: dict) -> str:
        tool = action.get("tool")
        args = action.get("args") or {}
        try:
            if tool == "describe_ontology":
                return _dump(self._gateway.describe())
            if tool == "describe_object":
                return _dump(self._gateway.describe_object(args["name"]))
            if tool == "find":
                objects = self._gateway.find_objects(
                    args["object_type"], args.get("filter") or {}
                )
                return _dump(objects[:20])
            if tool == "related":
                return _dump(
                    self._gateway.related(
                        args["ref"], args["relation"],
                        direction=args.get("direction", "out"),
                    )
                )
            if tool == "get":
                return _dump(self._gateway.get_object(args["ref"]))
            if tool == "invoke_capability":
                function = args["function"]
                if function in self._forbidden:
                    return (f"tool error: {function!r} is the capability you are "
                            f"implementing - invoking it again would recurse; "
                            f"call it only from outside, or use get/find/related")
                if self._invoker is not None:
                    result = self._invoker(
                        args["function"],
                        target=args.get("target"),
                        args=args.get("args") or {},
                    )
                else:
                    result = self._gateway.invoke(
                        args["function"],
                        target=args.get("target"),
                        args=args.get("args") or {},
                    )
                return _dump(
                    {
                        "ok": result.ok,
                        "status": result.status,
                        "error": result.error,
                        "output": result.output,
                        "changes": list(result.changes),
                    }
                )
            return f"unknown tool {tool!r}; available: describe_ontology, " \
                   f"describe_object, find, related, get, invoke_capability"
        except Exception as exc:
            return f"tool error: {type(exc).__name__}: {exc}"


def _dump(value) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > MAX_OBSERVATION_CHARS:
        text = text[:MAX_OBSERVATION_CHARS] + " ...(truncated)"
    return text


def _hide_functions(described: dict, forbidden: frozenset) -> dict:
    """Drop the functions an agent must not call from the runtime index it
    sees (kernel describe() formats: functions as a list or a dict)."""
    if not forbidden or not isinstance(described, dict):
        return described
    functions = described.get("functions")
    if isinstance(functions, dict):
        hidden = dict(described)
        hidden["functions"] = {
            name: spec for name, spec in functions.items() if name not in forbidden
        }
        return hidden
    if isinstance(functions, list):
        hidden = dict(described)
        hidden["functions"] = [
            spec for spec in functions if spec.get("name") not in forbidden
        ]
        return hidden
    return described


def _parse_json(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


# ----------------------------------------------------------------------
# agent-implemented capabilities (design.txt §19: Agent / AI + Function)
# ----------------------------------------------------------------------


def make_agent_runner(gateway: RuntimeGateway, provider_resolver,
                      ontology_context: str, goal: str, max_steps: int = 8,
                      function_name: str | None = None, invoker=None):
    """Build the platform-side runner for one agent-kind capability.

    The returned callable is registered on the gateway (see
    ``KernelRuntimeGateway.register_agent_runner``) and invoked by the
    kernel's executor when the capability's function is called:

        runner(target: str | None, args: dict) -> dict

    ``provider_resolver`` is a zero-arg callable returning the current LLM
    provider (or None) - resolved at call time so late platform
    configuration and process restarts pick up the configured provider.
    ``{target}`` / ``{arg_name}`` placeholders in the goal are substituted
    before the loop starts.  A missing provider surfaces as an invocation
    error (the kernel never lets executor exceptions escape).
    """

    def run(target: str | None, args: dict) -> dict:
        provider = provider_resolver()
        if provider is None:
            raise RuntimeError(
                "capability is implemented by an agent but no LLM provider "
                "is configured on the platform"
            )
        text = goal
        if target is not None:
            text = text.replace("{target}", target)
        for key, value in (args or {}).items():
            text = text.replace("{" + str(key) + "}", str(value))
        agent_run = AgentRunner(
            gateway, provider, ontology_context, max_steps=max_steps,
            forbidden_functions=(function_name,) if function_name else (),
            invoker=invoker,
        ).run(text)
        return {
            "ok": agent_run.ok,
            "answer": agent_run.answer,
            "steps": len(agent_run.steps),
        }

    return run


def make_audited_invoker(ctx, workspace_id: str, gateway,
                         actor: str | None = None):
    """Agent write path = InvokeCapability (execution record, contract check,
    domain event, audit).  The acting identity comes from the gateway's
    invocation context so nested agent actions are attributed to the actor
    who invoked the agent capability; a direct ``RunAgent`` request passes
    the requesting actor explicitly (plan-v2 1.8 / K7)."""

    def invoke(function: str, target: str | None = None, args: dict | None = None):
        from .capability_usecases import InvokeCapability

        acting = None
        if hasattr(gateway, "execution_actor"):
            acting = gateway.execution_actor()
        acting = acting or actor or "agent"
        result = InvokeCapability(ctx)(
            workspace_id, acting, function,
            target=target, args=args or {},
        )
        from types import SimpleNamespace

        return SimpleNamespace(
            ok=result.get("ok", False),
            output=result.get("output"),
            error=result.get("error"),
            status=result.get("status", "failed"),
            changes=result.get("changes") or [],
            execution_id=result.get("execution_id", ""),
        )

    return invoke
