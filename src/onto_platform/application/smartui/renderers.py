"""Renderer contribution for Smart-UI views.

A renderer is the *presentation* half of a view: the view plugin decides which
options are legal and how data is fetched, while a renderer declares which
frontend component can draw that view type.  The shipped frontend bundle still
contains the React components; this registry only makes the mapping explicit,
versioned and part of the execution lock.  It deliberately does not load remote
scripts (see ``ui.py`` and the project README).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...errors import ValidationError
from .contracts import PluginManifest


@dataclass(frozen=True)
class RendererDescriptor:
    view_type: str
    component: str
    plugin_id: str
    order: int
    format: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "viewType": self.view_type,
            "component": self.component,
            "pluginId": self.plugin_id,
            "order": self.order,
            "format": self.format,
        }


class RendererRegistry:
    """The viewType -> frontend component mapping declared by renderers."""

    def __init__(self) -> None:
        self._items: dict[str, RendererDescriptor] = {}

    def register(self, manifest: PluginManifest,
                 implementation: Any = None) -> None:
        if manifest.kind != "renderer":
            return
        view_type = str(manifest.view_type or "").strip()
        component = str(manifest.component or "").strip()
        if not view_type or not component:
            return
        existing = self._items.get(view_type)
        if existing is not None and existing.plugin_id != manifest.id:
            raise ValidationError(
                f"renderer for {view_type!r} is already registered by "
                f"{existing.plugin_id!r}")
        fmt = str(getattr(implementation, "format", "")
                  or manifest.metadata.get("format") or "")
        self._items[view_type] = RendererDescriptor(
            view_type=view_type,
            component=component,
            plugin_id=manifest.id,
            order=manifest.order,
            format=fmt,
        )

    def unregister(self, plugin_id: str) -> None:
        for view_type, descriptor in list(self._items.items()):
            if descriptor.plugin_id == plugin_id:
                self._items.pop(view_type, None)

    def get(self, view_type: str) -> RendererDescriptor | None:
        return self._items.get(str(view_type or ""))

    def descriptors(self) -> list[RendererDescriptor]:
        return sorted(self._items.values(),
                      key=lambda item: (item.order, item.view_type))

    def as_dicts(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.descriptors()]


class TableRenderer:
    """Built-in table renderer descriptor (component ships in the bundle)."""

    id = "onto.smartui.renderer.table"
    version = "1.1.0"
    view_type = "table"
    component = "DataTable"
    format = "table"

    def descriptor(self) -> dict[str, Any]:
        return {
            "viewType": self.view_type,
            "component": self.component,
            "format": self.format,
        }


class ChartRenderer:
    """Built-in chart renderer descriptor (component ships in the bundle)."""

    id = "onto.smartui.renderer.chart"
    version = "1.1.0"
    view_type = "chart"
    component = "StatisticChart"
    format = "chart"

    def descriptor(self) -> dict[str, Any]:
        return {
            "viewType": self.view_type,
            "component": self.component,
            "format": self.format,
        }


class GISRenderer:
    """Layered GIS renderer descriptor (GISMap ships in the runtime bundle)."""

    id = "onto.smartui.renderer.gis"
    version = "1.0.0"
    view_type = "gis"
    component = "GISMap"
    format = "gis"

    def descriptor(self) -> dict[str, Any]:
        return {
            "viewType": self.view_type,
            "component": self.component,
            "format": self.format,
        }


class Scene25DRenderer:
    """Isometric 2.5D renderer descriptor (Scene25D ships in the bundle)."""

    id = "onto.smartui.renderer.scene25d"
    version = "1.0.0"
    view_type = "scene_2_5d"
    component = "Scene25D"
    format = "scene25d"

    def descriptor(self) -> dict[str, Any]:
        return {
            "viewType": self.view_type,
            "component": self.component,
            "format": self.format,
        }


class Twin3DRenderer:
    """Digital-twin renderer descriptor (Twin3D ships in the bundle)."""

    id = "onto.smartui.renderer.twin3d"
    version = "1.0.0"
    view_type = "twin_3d"
    component = "Twin3D"
    format = "twin3d"

    def descriptor(self) -> dict[str, Any]:
        return {
            "viewType": self.view_type,
            "component": self.component,
            "format": self.format,
        }
