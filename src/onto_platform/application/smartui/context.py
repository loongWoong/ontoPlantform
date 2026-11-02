"""Context contribution for Smart-UI generation.

The context pack is the only workspace state a generator is allowed to read.
It contains names, counts, focus and capabilities; it never contains instance
records or property values.  Keeping this behind a contribution point means a
workspace pack can narrow the perspectives offered to the model without the
Studio knowing about traffic/finance specifics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import _engine, smart_ui_meta, smart_ui_snapshot


@dataclass(frozen=True)
class ContextBuildRequest:
    """Inputs a context contribution may read."""

    platform: Any
    workspace_id: str
    actor: str
    snapshot: dict[str, Any] | None = None
    workspace_pack: Any = None


class DefaultContextPack:
    """The built-in L0/L1/L2 context pack.

    The implementation stays deliberately small: it is a projection of the
    public Smart-UI meta/snapshot contracts, not a second source of truth.
    """

    id = "onto.smartui.context.default"
    version = "1.1.0"

    def build(self, request: ContextBuildRequest) -> dict[str, Any]:
        platform = request.platform
        workspace_id = request.workspace_id
        actor = request.actor
        snapshot = request.snapshot or smart_ui_snapshot(
            platform, workspace_id, actor)
        ontology = snapshot["ontology"]
        meta = smart_ui_meta(platform, workspace_id, actor)
        modules = _engine()
        pack = request.workspace_pack
        allowed = set(getattr(pack, "perspectives", ()) or ())
        objects: list[dict[str, Any]] = []
        for object_name in meta.get("objectTypes") or []:
            obj = ontology.object(str(object_name))
            if obj is None:
                continue
            caps = modules["derive_capabilities"](ontology, str(object_name))
            if allowed:
                caps = [cap for cap in caps if cap.perspective in allowed]
            objects.append({
                "name": obj.name,
                "displayName": obj.display_name or obj.name,
                "role": getattr(obj, "role", ""),
                # Only fact/aggregate_root objects can be a Blueprint
                # context.object.  A dimension is still listed with the
                # legal root the platform will resolve it to.
                "queryable": bool(getattr(obj, "is_query_root", False)),
                "resolvedQueryRoot": (
                    None if getattr(obj, "is_query_root", False)
                    else modules["resolve_query_root"](ontology, obj.name)[0]
                ),
                "properties": [
                    {"name": prop.name, "type": prop.data_type,
                     "temporal": bool(getattr(prop, "is_temporal", False))}
                    for prop in obj.properties
                ],
                "perspectives": [
                    {"perspective": cap.perspective, "title": cap.title,
                     "evidence": list(cap.evidence)}
                    for cap in caps
                ],
            })
        actions = [
            {
                "key": action.get("key"),
                "name": action.get("name"),
                "target": action.get("target"),
                "parameters": action.get("parameters") or [],
            }
            for action in (meta.get("actions") or [])
        ]
        if pack is not None and getattr(pack, "permissions", ()):
            allowed_actions = set(pack.permissions)
            actions = [action for action in actions
                       if action["key"] in allowed_actions]
        context: dict[str, Any] = {
            "workspace": meta.get("workspace"),
            "actorRole": meta.get("actorRole"),
            "roles": meta.get("roles"),
            "focus": meta.get("focus"),
            "queryRoots": [
                item["name"] for item in objects if item["queryable"]
            ],
            "objects": objects,
            "actions": actions,
        }
        if pack is not None and hasattr(pack, "as_dict"):
            context["workspacePack"] = pack.as_dict()
        return context
