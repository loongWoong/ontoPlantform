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

from ..domain.intelligence.model import AgentRun, AgentStep
from ..errors import RuntimeUnavailableError
from ..ports.llm import LLMMessage, LLMProvider
from ..ports.runtime import RuntimeGateway

MAX_OBSERVATION_CHARS = 4000

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
                 ontology_context: str, max_steps: int = 8) -> None:
        self._gateway = gateway
        self._provider = provider
        self._ontology_context = ontology_context
        self._max_steps = max_steps

    def run(self, goal: str) -> AgentRun:
        run = AgentRun(goal=goal)
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
        for index in range(self._max_steps):
            try:
                response = self._provider.complete(messages)
            except Exception as exc:
                run.ok = False
                run.answer = f"LLM provider failed: {exc}"
                return run
            parsed = _parse_json(response.content)
            if parsed is None:
                messages.append(LLMMessage(role="assistant", content=response.content))
                messages.append(
                    LLMMessage(
                        role="user",
                        content='Your reply was not valid JSON. Reply with strict JSON: '
                                '{"think": "...", "action": {...}} or {"think": "...", "answer": "..."}.',
                    )
                )
                continue
            thought = str(parsed.get("think", ""))
            if "answer" in parsed and not parsed.get("action"):
                run.answer = str(parsed["answer"])
                run.steps.append(
                    AgentStep(index=index, thought=thought,
                              action={"tool": "finish"}, observation="")
                )
                return run
            action = parsed.get("action") or {}
            observation = self._execute_tool(action)
            run.steps.append(
                AgentStep(index=index, thought=thought, action=action,
                          observation=observation)
            )
            messages.append(LLMMessage(role="assistant", content=response.content))
            messages.append(
                LLMMessage(role="user", content=f"OBSERVATION:\n{observation}")
            )
        run.ok = False
        run.answer = "达到最大推理步数仍未得出答案"
        return run

    # ------------------------------------------------------------------

    def _safe_describe(self) -> dict:
        try:
            return self._gateway.describe()
        except RuntimeUnavailableError:
            return {"error": "runtime not published"}

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
                result = self._gateway.invoke(
                    args["function"],
                    target=args.get("target"),
                    args=args.get("args") or {},
                )
                return _dump(
                    {
                        "ok": result.ok,
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
                      ontology_context: str, goal: str, max_steps: int = 8):
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
        agent_run = AgentRunner(gateway, provider, ontology_context,
                                max_steps=max_steps).run(text)
        return {
            "ok": agent_run.ok,
            "answer": agent_run.answer,
            "steps": len(agent_run.steps),
        }

    return run
