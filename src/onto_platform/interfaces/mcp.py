"""MCP adapter: the PLATFORM exposed as MCP tools (design.txt §36/§46).

Where the kernel's MCP adapter exposes one runtime, this adapter exposes
the platform's business world of one workspace:

    workspace_describe      - ontology + runtime index
    workspace_find          - find objects by type + filters
    workspace_get           - one object by Type/Id
    workspace_related       - navigate relations
    workspace_ask           - deterministic natural-language query
    capability_invoke       - one published capability as a tool call
    agent_run               - goal-driven agent loop (needs an LLM provider)

MCP only translates protocol; all logic stays in the use cases
(design.txt §40).  Requires the optional dependency:

    pip install onto-platform[mcp]
"""

from __future__ import annotations

import json

from ..bootstrap import Platform, PlatformConfig, build_platform


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def build_server(platform: Platform | None = None, workspace_id: str = "",
                 db_path: str = ".onto/platform.db"):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "The MCP adapter requires the 'mcp' package. "
            "Install with: pip install onto-platform[mcp]"
        ) from exc

    if platform is None:
        platform = build_platform(PlatformConfig(store="sqlite", db_path=db_path))
    mcp = FastMCP("onto-platform")

    def _wid() -> str:
        if workspace_id:
            return workspace_id
        workspaces = platform.workspaces.list("admin")
        if len(workspaces) == 1:
            return workspaces[0]["id"]
        raise ValueError(
            "workspace_id is required when the platform has zero or "
            "multiple workspaces"
        )

    @mcp.tool()
    def workspace_describe() -> str:
        """Ontology + runtime index of the workspace: object types,
        relations, published capabilities (tools for this server)."""
        wid = _wid()
        ontology = platform.ontologies.get(wid, "admin")
        runtime = platform.ontologies.describe_runtime(wid, "admin")
        capabilities = platform.capabilities.list(wid, "admin")
        return _dumps({
            "workspace_id": wid,
            "ontology": {
                "name": ontology.get("name"),
                "objects": ontology.get("objects", {}),
                "relations": ontology.get("relations", {}),
                "vocabulary": ontology.get("vocabulary", {}),
            },
            "runtime_objects": runtime.get("objects", []),
            "capabilities": [
                {"name": c["name"], "function": f"{c['target_type']}.{c['key']}",
                 "description": c.get("description", "")}
                for c in capabilities
                if c.get("lifecycle") == "published"
            ],
        })

    @mcp.tool()
    def workspace_find(object_type: str, filters: dict | None = None) -> str:
        """Find runtime objects of a type, e.g. filters={"status": "congested"}."""
        gateway = platform.ctx.gateway_for(_wid())
        return _dumps(gateway.find_objects(object_type, filters or {}))

    @mcp.tool()
    def workspace_get(ref: str) -> str:
        """Get one runtime object by its reference, e.g. 'Vehicle/V001'."""
        gateway = platform.ctx.gateway_for(_wid())
        return _dumps(gateway.get_object(ref))

    @mcp.tool()
    def workspace_related(ref: str, relation: str, direction: str = "out") -> str:
        """Navigate a relation from an object ('out' or 'in')."""
        gateway = platform.ctx.gateway_for(_wid())
        return _dumps(gateway.related(ref, relation, direction))

    @mcp.tool()
    def workspace_ask(question: str) -> str:
        """Deterministic natural-language query over the business world
        (vocabulary -> concepts -> runtime state)."""
        return _dumps(platform.intelligence.ask(_wid(), "admin", question))

    @mcp.tool()
    def capability_invoke(function: str, target: str | None = None,
                          args: dict | None = None) -> str:
        """Execute a published business capability, e.g.
        function='Vehicle.recharge', target='Vehicle/V001', args={"amount": 100}.
        Returns output + the state changes it made."""
        result = platform.capabilities.invoke(
            _wid(), "admin", function, target=target, args=dict(args or {})
        )
        return _dumps(result)

    @mcp.tool()
    def agent_run(goal: str) -> str:
        """Run the platform agent towards a goal. The agent observes the
        ontology and runtime state and uses capabilities to act."""
        run = platform.intelligence.agent(_wid(), "admin", goal)
        return _dumps(run)

    return mcp


def main() -> None:  # pragma: no cover - stdio entry point
    import argparse

    parser = argparse.ArgumentParser(prog="onto-mcp")
    parser.add_argument("--db", default=".onto/platform.db")
    parser.add_argument("--workspace", default="")
    args = parser.parse_args()
    platform = build_platform(PlatformConfig(store="sqlite", db_path=args.db))
    mcp = build_server(platform=platform, workspace_id=args.workspace)
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
