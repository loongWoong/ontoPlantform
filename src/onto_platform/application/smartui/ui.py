"""UI contribution for AI视图.

The shipped frontend bundle contains the actual React components.  This
contribution publishes the *manifest* of slots and renderer capabilities so the
shell can hide unknown/missing slots and the API can expose the same contract
as the backend plugin lock.  No remote script is loaded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UISlot:
    slot: str
    id: str
    order: int
    label: str
    builtin: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "id": self.id,
            "order": self.order,
            "label": self.label,
            "builtin": self.builtin,
        }


class AIViewUIProvider:
    """Built-in AI视图 slot + renderer manifest."""

    id = "onto.smartui.ui.aiview"
    version = "1.1.0"

    def __init__(self) -> None:
        self._slots = (
            UISlot("aiview.tabs", "studio", 10, "AI 生成台"),
            UISlot("aiview.tabs", "history", 20, "生成记录"),
        )
        self._renderers = (
            {"viewType": "table", "component": "DataTable"},
            {"viewType": "chart", "component": "StatisticChart"},
        )

    def slots(self) -> list[dict[str, Any]]:
        return [slot.as_dict() for slot in self._slots]

    def renderers(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._renderers]

    def manifest(self) -> dict[str, Any]:
        return {
            "slots": self.slots(),
            "renderers": self.renderers(),
            "remoteScripts": False,
        }
