"""Spatial presentation plugins: GIS, 2.5D and 3D digital-twin views.

All three contributions share the engine's ``location`` perspective and the
same platform payload (``view_location``).  A *display* option selects the
presentation variant; the deterministic engine maps that option to one of the
components registered in the build-time runtime bundle:

    gis      -> GISMap    (layered business map: basemap, route, points, fence)
    scene25d -> Scene25D  (isometric extruded scene with selectable columns)
    twin3d   -> Twin3D    (depth-sorted pseudo-3D scene with object blocks)

The plugins own only the declarative option contract.  They never receive raw
instance data, never emit layout/style/code, and never select a component on
their own: Studio adjudicates the request, then the engine's validated
``ViewSpec`` decides which component the program calls.
"""

from __future__ import annotations

from typing import Any

#: Display value -> engine view type.  Keep in sync with the engine's
#: ``capabilities.LOCATION_VARIANTS`` and the runtime library registration.
SPATIAL_VIEW_TYPES: dict[str, str] = {
    "gis": "gis",
    "scene25d": "scene_2_5d",
    "twin3d": "twin_3d",
}
SPATIAL_DISPLAYS = tuple(SPATIAL_VIEW_TYPES)

_LAYERS = frozenset({"route", "points", "labels", "geofence"})
_BASEMAPS = frozenset({"grid", "terrain", "night"})
_THEMES = frozenset({"light", "dark"})


def _property_names(obj: Any) -> set[str]:
    return {
        str(getattr(prop, "name", "") or "")
        for prop in (getattr(obj, "properties", ()) or ())
        if getattr(prop, "name", None)
    }


def _numeric_names(obj: Any) -> set[str]:
    return {
        str(getattr(prop, "name", "") or "")
        for prop in (getattr(obj, "properties", ()) or ())
        if getattr(prop, "name", None) and getattr(prop, "is_numeric", False)
    }


def _property_option(raw: dict[str, Any], key: str, allowed: set[str],
                     pruned: list[dict[str, Any]]) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        return ""
    if value not in allowed:
        pruned.append({
            "kind": "option",
            "key": key,
            "reason": f"{value!r} 不是该对象可用的属性",
        })
        return ""
    return value


def adjudicate_spatial_options(
    raw: Any,
    obj: Any,
    perspective: str,
    pruned: list[dict[str, Any]],
    *,
    display: str,
    allow_basemap: bool = False,
    allow_cluster: bool = False,
    allow_height: bool = False,
) -> dict[str, Any]:
    """Validate a spatial view's options against the focus object.

    Only declarative, ontology-backed values survive.  Unknown property names,
    unsupported enum values and arbitrary presentation keys are pruned with a
    reason so the Studio can show *why* a proposal was narrowed.
    """
    if not isinstance(raw, dict) or perspective != "location":
        return {}
    # The engine derives the location capability from the focus object *or* a
    # directly related object.  This plugin only sees the focus object, so it
    # must not re-gate on focus geo properties: a Vehicle can have a map view
    # through its related Stations.  The engine still drops location options
    # when no location capability exists.

    props = _property_names(obj)
    numeric = _numeric_names(obj)
    options: dict[str, Any] = {}

    requested = str(raw.get("display") or "").strip()
    if requested:
        if requested == display:
            options["display"] = display
        else:
            pruned.append({
                "kind": "option",
                "key": "display",
                "reason": f"当前空间插件只支持 display={display!r}",
            })

    if allow_basemap:
        basemap = str(raw.get("basemap") or "").strip()
        if basemap:
            if basemap in _BASEMAPS:
                options["basemap"] = basemap
            else:
                pruned.append({
                    "kind": "option",
                    "key": "basemap",
                    "reason": "basemap 只能是 grid|terrain|night",
                })
        layers = raw.get("layers")
        if isinstance(layers, list):
            kept = [str(item) for item in layers if str(item) in _LAYERS][:4]
            if kept:
                options["layers"] = kept
            elif layers:
                pruned.append({
                    "kind": "option",
                    "key": "layers",
                    "reason": "layers 只能是 route|points|labels|geofence",
                })
        if allow_cluster and "cluster" in raw:
            if isinstance(raw.get("cluster"), bool):
                options["cluster"] = bool(raw["cluster"])
            else:
                pruned.append({
                    "kind": "option", "key": "cluster",
                    "reason": "cluster 必须是布尔值",
                })

    for key in ("colorField", "labelField"):
        if key in raw:
            value = _property_option(raw, key, props, pruned)
            if value:
                options[key] = value
    if allow_height and "heightField" in raw:
        value = _property_option(raw, "heightField", numeric, pruned)
        if value:
            options["heightField"] = value

    if "showLabels" in raw:
        if isinstance(raw.get("showLabels"), bool):
            options["showLabels"] = bool(raw["showLabels"])
        else:
            pruned.append({
                "kind": "option", "key": "showLabels",
                "reason": "showLabels 必须是布尔值",
            })
    if "theme" in raw:
        theme = str(raw.get("theme") or "")
        if theme in _THEMES:
            options["theme"] = theme
        elif theme:
            pruned.append({
                "kind": "option", "key": "theme",
                "reason": "theme 只能是 light|dark",
            })
    return options


def _schema(display: str, *, basemap: bool, height: bool,
            cluster: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "display": {"enum": [display]},
        "colorField": {"type": "string", "x-ontology": "property"},
        "labelField": {"type": "string", "x-ontology": "property"},
        "showLabels": {"type": "boolean"},
        "theme": {"enum": ["light", "dark"]},
    }
    if basemap:
        properties["basemap"] = {"enum": ["grid", "terrain", "night"]}
        properties["layers"] = {
            "type": "array",
            "maxItems": 4,
            "items": {"enum": ["route", "points", "labels", "geofence"]},
        }
    if cluster:
        properties["cluster"] = {"type": "boolean"}
    if height:
        properties["heightField"] = {
            "type": "string",
            "x-ontology": "property",
        }
    return {"type": "object", "properties": properties}


class _SpatialPlugin:
    """Shared plumbing for the three location presentation variants."""

    id = ""
    version = "1.0.0"
    perspective = "location"
    view_type = ""
    component = ""
    display = ""
    allow_basemap = False
    allow_cluster = False
    allow_height = False

    def options_schema(self) -> dict[str, Any]:
        return _schema(self.display, basemap=self.allow_basemap,
                       height=self.allow_height, cluster=self.allow_cluster)

    def adjudicate_options(self, raw: Any, obj: Any, perspective: str,
                           pruned: list[dict[str, Any]]) -> dict[str, Any]:
        return adjudicate_spatial_options(
            raw, obj, perspective, pruned,
            display=self.display,
            allow_basemap=self.allow_basemap,
            allow_cluster=self.allow_cluster,
            allow_height=self.allow_height,
        )

    def resolve(self, context: Any, options: dict[str, Any]) -> dict[str, Any]:
        # The platform's view_location payload is shared by all three displays.
        # A plugin only contributes options, never a second source of truth.
        return {}

    def descriptor(self) -> dict[str, Any]:
        return {
            "perspective": self.perspective,
            "viewType": self.view_type,
            "component": self.component,
            "display": self.display,
        }


class GISPlugin(_SpatialPlugin):
    id = "onto.smartui.view.gis"
    view_type = "gis"
    component = "GISMap"
    display = "gis"
    allow_basemap = True
    allow_cluster = True


class Scene25DPlugin(_SpatialPlugin):
    id = "onto.smartui.view.scene25d"
    view_type = "scene_2_5d"
    component = "Scene25D"
    display = "scene25d"
    allow_height = True


class Twin3DPlugin(_SpatialPlugin):
    id = "onto.smartui.view.twin3d"
    view_type = "twin_3d"
    component = "Twin3D"
    display = "twin3d"
    allow_height = True
