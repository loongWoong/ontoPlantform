"""PlatformExecutor: the kernel-side FunctionExecutor for platform capabilities.

Dispatches a kernel FunctionCall by binding kind:

    python   -> kernel PythonFunctionExecutor (inline/file python source)
    workflow -> GatewayWorkflowRunner (platform orchestration)
    agent    -> a runner registered via gateway.register_agent_runner()

The kernel only sees the FunctionExecutor Port - platform orchestration
plugs in exactly where the kernel reserved the extension point.

Kernel types (FunctionResult / FunctionExecutionError) are injected as
factories by KernelRuntimeGateway, so no platform module besides the
gateway imports the kernel (design.txt §47 原则三).
"""

from __future__ import annotations


class PlatformExecutor:
    def __init__(self, bindings, workflows: dict, python_executor, runner,
                 agents: dict | None = None, execution_error=None,
                 make_result=None) -> None:
        self._bindings = bindings            # kernel FunctionBinding dict
        self._workflows = workflows          # {"workflow://<id>": steps}
        self._python = python_executor
        self._runner = runner
        # keep the caller's dict by reference: agent runners register AFTER
        # publish_schema built this executor
        self._agents = {} if agents is None else agents
        self._error = execution_error or (lambda message: RuntimeError(message))
        self._make_result = make_result or (lambda execution_id, output=None: output)

    def execute(self, call, context) -> object:
        binding = self._bindings.get(call.function)
        if binding is None:
            raise self._error(
                f"function {call.function!r} has no implementation binding"
            )
        if binding.executor == "python":
            return self._python.execute(call, context)
        if binding.executor == "workflow":
            steps = self._workflows.get(binding.entrypoint or "")
            if steps is None:
                raise self._error(
                    f"workflow definition missing for {call.function!r}"
                )
            result_ctx = self._runner.run(
                steps,
                {
                    "args": dict(call.args),
                    "target": str(call.target) if call.target is not None else None,
                },
            )
            return self._make_result(context.execution_id, result_ctx.get("output"))
        if binding.executor == "agent":
            agent = self._agents.get(binding.entrypoint or "")
            if agent is None:
                raise self._error(
                    f"agent runner missing for {call.function!r}; "
                    "republish the workspace to register it"
                )
            output = agent(
                str(call.target) if call.target is not None else None,
                dict(call.args),
            )
            return self._make_result(context.execution_id, output)
        raise self._error(
            f"function {call.function!r} is bound to unsupported executor "
            f"{binding.executor!r}"
        )
