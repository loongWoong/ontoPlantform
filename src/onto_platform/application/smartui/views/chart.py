"""Chart view plugin (built-in proof that a second presentation can register)."""

from __future__ import annotations

from typing import Any

from .table import adjudicate_options

CHART_OPTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chartType": {"enum": ["bar", "line", "pie"]},
        "display": {"enum": ["panel"]},
        "groupBy": {"type": "string", "x-ontology": "property"},
        "metric": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "x-ontology": "property"},
                "agg": {"enum": ["sum", "avg", "count", "min", "max"]},
            },
        },
        "sort": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "direction": {"enum": ["asc", "desc"]},
            },
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    },
}


class ChartPlugin:
    """The chart presentation shares v1.1 option adjudication with the table.

    Its separate manifest is what allows the engine/runtime to grow another
    component later without touching Studio's adjudication code.
    """

    id = "onto.smartui.view.chart"
    version = "1.0.0"
    perspective = "statistics"
    view_type = "chart"
    component = "StatisticChart"

    def options_schema(self) -> dict[str, Any]:
        return CHART_OPTIONS_SCHEMA

    def adjudicate_options(self, raw: Any, obj: Any, perspective: str,
                           pruned: list[dict[str, Any]]) -> dict[str, Any]:
        return adjudicate_options(raw, obj, perspective, pruned)

    def resolve(self, context: dict[str, Any],
                options: dict[str, Any]) -> dict[str, Any]:
        # Chart series are emitted by the deterministic engine from the same
        # records; a plugin only needs a payload when it adds a new component.
        return {}
