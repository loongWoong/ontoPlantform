"""WorkflowRunner Port: capability orchestration.

A Workflow composes Functions into a Capability (design.txt §18/§19).
The runner executes workflow steps against the RuntimeGateway; the
kernel sees the whole workflow as ONE function (its binding points at
``workflow://<capability id>``).
"""

from __future__ import annotations

from typing import Protocol


class WorkflowRunner(Protocol):

    def run(self, steps: list[dict], context: dict) -> dict:
        """Execute steps sequentially.

        ``context``  - ``{"args": ..., "target": "Type/Id"|None}``
        returns      - the final workflow context, including
                       ``{"steps": {alias: output}, "output": last}``

        Raises WorkflowStepError when a step fails; the executor turns
        that into FunctionResult.error (never a partial success).
        """
        ...
