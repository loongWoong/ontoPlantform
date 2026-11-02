"""Engine-facing view descriptors.

The deterministic engine still owns planning/compilation.  This registry is the
small extension seam between a view contribution and the engine: a view manifest
declares which perspective/type it can render and which frontend component is
the matching renderer.  The API exposes the registry so the shell can verify a
blueprint's views are backed by an installed renderer instead of guessing from
hard-coded names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import PluginManifest


@dataclass(frozen=True)
class EngineDescriptor:
    perspective: str
    view_type: str
    component: str
    plugin_id: str
    order: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "perspective": self.perspective,
            "viewType": self.view_type,
            "component": self.component,
            "pluginId": self.plugin_id,
            "order": self.order,
        }


class EngineDescriptorRegistry:
    """Collects view descriptors declared by registered view plugins."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], EngineDescriptor] = {}

    def register(self, manifest: PluginManifest) -> None:
        if manifest.kind != "view":
            return
        engine = dict(manifest.metadata.get("engine") or {})
        perspectives: Iterable[str] = (
            engine.get("perspectives") or manifest.perspectives
            or ([engine["perspective"]] if engine.get("perspective") else [])
        )
        component = str(engine.get("component") or manifest.component or "")
        view_type = str(engine.get("viewType") or manifest.view_type or "")
        for perspective in perspectives:
            perspective = str(perspective or "")
            if not perspective or not view_type:
                continue
            descriptor = EngineDescriptor(
                perspective=perspective,
                view_type=view_type,
                component=component,
                plugin_id=manifest.id,
                order=manifest.order,
            )
            self._items[(perspective, view_type)] = descriptor

    def unregister(self, plugin_id: str) -> None:
        for key, descriptor in list(self._items.items()):
            if descriptor.plugin_id == plugin_id:
                self._items.pop(key, None)

    def descriptors(self) -> list[EngineDescriptor]:
        return sorted(self._items.values(),
                      key=lambda item: (item.perspective, item.order,
                                        item.view_type))

    def as_dicts(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.descriptors()]

    def get(self, perspective: str, view_type: str) -> EngineDescriptor | None:
        return self._items.get((perspective, view_type))

    def for_perspective(self, perspective: str) -> list[EngineDescriptor]:
        return [item for item in self.descriptors()
                if item.perspective == perspective]
