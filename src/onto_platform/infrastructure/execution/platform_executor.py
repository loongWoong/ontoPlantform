"""PlatformExecutor: the kernel-side FunctionExecutor for platform capabilities.

Dispatches a kernel FunctionCall by binding kind:

    python   -> kernel PythonFunctionExecutor (inline/file python source)
    workflow -> GatewayWorkflowRunner (platform orchestration)

The kernel only sees the FunctionExecutor Port - platform orchestration
plugs in exactly where the kernel reserved the extension point.
"""

from __future__ import annotations


class PlatformExecutor:
    def __init__(self, bindings, workflows: dict, python_executor, runner) -> None:
        self._bindings = bindings            # kernel FunctionBinding dict
        self._workflows = workflows          # {"workflow://<id>": steps}
        self._python = python_executor
        self._runner = runner

    def execute(self, call, context) -> object:
        binding = self._bindings.get(call.function)
        if binding is None:
            from ontology_runtime.core.errors import FunctionExecutionError

            raise FunctionExecutionError(
                f"function {call.function!r} has no implementation binding"
            )
        if binding.executor == "python":
            return self._python.execute(call, context)
        if binding.executor == "workflow":
            from ontology_runtime.core.errors import FunctionExecutionError
            from ontology_runtime.core.execution import FunctionResult

            steps = self._workflows.get(binding.entrypoint or "")
            if steps is None:
                raise FunctionExecutionError(
                    f"workflow definition missing for {call.function!r}"
                )
            result_ctx = self._runner.run(
                steps,
                {
                    "args": dict(call.args),
                    "target": str(call.target) if call.target is not None else None,
                },
            )
            return FunctionResult(
                execution_id=context.execution_id, output=result_ctx.get("output")
            )
        from ontology_runtime.core.errors import FunctionExecutionError

        raise FunctionExecutionError(
            f"function {call.function!r} is bound to unsupported executor "
            f"{binding.executor!r}"
        )
