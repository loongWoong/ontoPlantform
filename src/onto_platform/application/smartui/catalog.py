"""Core Smart-UI semantic catalog.

Perspectives and goals are ontology semantics, not presentation choices.
Keeping them in one module prevents the platform, prompt pack and engine
adapter from drifting into three slightly different enums.
"""

from __future__ import annotations

GOALS = (
    "overview", "profile", "anomaly_analysis", "trace",
    "comparison", "trend", "operation",
)

PERSPECTIVES = (
    "identity", "relationship", "timeline", "location",
    "statistics", "state", "rule", "action",
)

MAX_VIEWS = 8
MAX_QUESTION = 500
SCHEMA_VERSION = "1.0"
