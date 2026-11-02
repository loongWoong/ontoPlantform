"""Declarative workspace packs.

A pack is data, never code: it can choose a prompt pack, name the ontology
profile it expects and ship view presets, but it cannot add a generator,
execute a resolver or change permissions.  Operator-installed signed code
plugins are a later, separate trust level.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ...errors import NotFoundError, ValidationError
from .catalog import PERSPECTIVES
from .contracts import PERMISSION_PROMPT_RENDER

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_PRESET_KEYS = {
    "chartType", "display", "groupBy", "metric", "sort", "limit",
    "columns", "filters",
}


@dataclass(frozen=True)
class WorkspacePack:
    """One declarative Smart-UI workspace preset."""

    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    prompt_pack: str = "onto.smartui.prompt.default"
    ontology_profile: str = ""
    perspectives: tuple[str, ...] = ()
    view_presets: tuple[dict[str, Any], ...] = ()
    default_question: str = ""
    permissions: tuple[str, ...] = field(default=())
    builtin: bool = False

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValidationError(f"invalid pack id: {self.id!r}")
        if not _VERSION_RE.match(self.version):
            raise ValidationError(f"invalid pack version: {self.version!r}")
        unknown = [item for item in self.perspectives if item not in PERSPECTIVES]
        if unknown:
            raise ValidationError(f"pack {self.id} uses unknown perspectives: {unknown}")
        for preset in self.view_presets:
            if not isinstance(preset, dict):
                raise ValidationError(f"pack {self.id} view preset must be an object")
            perspective = preset.get("perspective")
            if perspective not in PERSPECTIVES:
                raise ValidationError(
                    f"pack {self.id} preset has unknown perspective: {perspective!r}")
            options = preset.get("options") or {}
            if not isinstance(options, dict):
                raise ValidationError(f"pack {self.id} preset options must be an object")
            extra = set(options) - _PRESET_KEYS
            if extra:
                raise ValidationError(
                    f"pack {self.id} preset contains non-declarative keys: {sorted(extra)}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "promptPack": self.prompt_pack,
            "ontologyProfile": self.ontology_profile,
            "perspectives": list(self.perspectives),
            "viewPresets": [dict(item) for item in self.view_presets],
            "defaultQuestion": self.default_question,
            "permissions": list(self.permissions),
            "builtin": self.builtin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkspacePack":
        if not isinstance(data, dict):
            raise ValidationError("pack must be a JSON object")
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            version=str(data.get("version") or "1.0.0"),
            description=str(data.get("description") or ""),
            prompt_pack=str(data.get("promptPack") or data.get("prompt_pack")
                            or "onto.smartui.prompt.default"),
            ontology_profile=str(data.get("ontologyProfile")
                                 or data.get("ontology_profile") or ""),
            perspectives=tuple(str(item) for item in data.get("perspectives") or ()),
            view_presets=tuple(
                dict(item) for item in data.get("viewPresets")
                or data.get("view_presets") or ()),
            default_question=str(data.get("defaultQuestion")
                                 or data.get("default_question") or ""),
            permissions=tuple(str(item) for item in data.get("permissions") or ()),
        )


def load_pack(source: str | Path | dict[str, Any]) -> WorkspacePack:
    """Load a pack from JSON text, a JSON file, or an already-decoded object."""
    if isinstance(source, dict):
        return WorkspacePack.from_dict(source)
    if isinstance(source, Path):
        data = json.loads(source.read_text(encoding="utf-8"))
    else:
        text = str(source).strip()
        if text.startswith("{"):
            data = json.loads(text)
        else:
            data = json.loads(Path(text).read_text(encoding="utf-8"))
    return WorkspacePack.from_dict(data)


class WorkspacePackCatalog:
    """Registry for declarative packs; no plugin code can be stored here."""

    def __init__(self, packs: Iterable[WorkspacePack] = ()) -> None:
        self._packs: dict[str, WorkspacePack] = {}
        for pack in packs:
            self.register(pack)

    def register(self, pack: WorkspacePack) -> WorkspacePack:
        if pack.id in self._packs:
            raise ValidationError(f"workspace pack already registered: {pack.id}")
        self._packs[pack.id] = pack
        return pack

    def get(self, pack_id: str) -> WorkspacePack:
        pack = self._packs.get(pack_id)
        if pack is None:
            raise NotFoundError(f"workspace pack not found: {pack_id}")
        return pack

    def list(self) -> list[WorkspacePack]:
        return sorted(self._packs.values(), key=lambda pack: pack.id)

    def summaries(self) -> list[dict[str, Any]]:
        return [pack.as_dict() for pack in self.list()]


BUILTIN_PACKS: tuple[WorkspacePack, ...] = (
    WorkspacePack(
        id="onto.pack.traffic",
        name="高速运营",
        description="车辆、收费站、车道与通行的运营视图预设",
        ontology_profile="traffic",
        perspectives=("identity", "statistics", "timeline", "location"),
        view_presets=(
            {"perspective": "identity", "options": {}},
            {"perspective": "statistics",
             "options": {"chartType": "bar", "limit": 20}},
        ),
        default_question="查看车辆当前情况并按站点对比",
        builtin=True,
    ),
    WorkspacePack(
        id="onto.pack.finance-lease",
        name="租赁财务",
        description="租赁合同、租金与风险的财务视图预设",
        ontology_profile="finance_lease",
        perspectives=("identity", "statistics", "rule", "state"),
        view_presets=(
            {"perspective": "identity", "options": {}},
            {"perspective": "statistics",
             "options": {"display": "table", "limit": 50}},
        ),
        default_question="哪些租赁合同存在回款风险",
        builtin=True,
    ),
)

#: A workspace-level Smart-UI configuration document.  It is deliberately
#: separate from ontology/data documents and from ``runtime_config`` (which
#: controls the kernel store), so changing a pack never rewrites infrastructure.
SMARTUI_CONFIG_KIND = "smartui_config"
_CONFIG_ID = "config"


def workspace_pack_id(platform: Any, workspace_id: str, actor: str) -> str:
    """Read the bound pack id; absent configuration means "no pack"."""
    platform.ctx.require(actor, workspace_id, "viewer")
    row = platform.ctx.repo.get(SMARTUI_CONFIG_KIND, workspace_id, _CONFIG_ID)
    return str((row or {}).get("pack_id") or "")


def resolve_generation_pack(platform: Any, workspace_id: str, actor: str,
                            requested_pack_id: str = "") -> WorkspacePack | None:
    """Resolve a request override, then the workspace default, then no pack."""
    pack_id = str(requested_pack_id or "").strip()
    if not pack_id:
        pack_id = workspace_pack_id(platform, workspace_id, actor)
    if not pack_id:
        return None
    host = getattr(platform, "smartui", None)
    if host is None:
        raise ValidationError("Smart-UI plugin host is not available")
    return host.pack(pack_id)


def set_workspace_pack(platform: Any, workspace_id: str, actor: str,
                       pack_id: str) -> dict[str, Any]:
    """Bind (or clear) the workspace default pack.

    The pack must exist and its ``prompt_pack`` must resolve to a registered
    prompt contribution; otherwise a workspace could silently bind a prompt
    contract that nothing can render.
    """
    platform.ctx.require(actor, workspace_id, "editor")
    host = getattr(platform, "smartui", None)
    if host is None:
        raise ValidationError("Smart-UI plugin host is not available")
    wanted = str(pack_id or "").strip()
    pack: WorkspacePack | None = None
    if wanted:
        pack = host.pack(wanted)
        prompt_id = str(pack.prompt_pack or "").strip()
        if prompt_id:
            if not host.has(prompt_id):
                raise ValidationError(
                    f"pack {pack.id} references unknown prompt plugin {prompt_id!r}")
            prompt_record = host.get(prompt_id)
            if prompt_record.manifest.kind != "prompt":
                raise ValidationError(
                    f"pack {pack.id} prompt_pack {prompt_id!r} is not a prompt plugin")
            if PERMISSION_PROMPT_RENDER not in prompt_record.manifest.permissions:
                raise ValidationError(
                    f"pack {pack.id} prompt_pack {prompt_id!r} lacks "
                    f"{PERMISSION_PROMPT_RENDER!r}")
    row = {
        "id": _CONFIG_ID,
        "workspace_id": workspace_id,
        "pack_id": pack.id if pack is not None else "",
        "updated_by": actor,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    previous = platform.ctx.repo.get(
        SMARTUI_CONFIG_KIND, workspace_id, _CONFIG_ID) or {}
    platform.ctx.repo.put(SMARTUI_CONFIG_KIND, workspace_id, _CONFIG_ID, row)
    platform.ctx.audit_event(actor, workspace_id, "smart_ui.pack.bind",
                             pack.id if pack is not None else "", {
                                 "previous_pack_id": previous.get("pack_id", ""),
                                 "pack_id": row["pack_id"],
                             })
    return {"workspace_id": workspace_id, "packId": row["pack_id"],
            "pack": pack.as_dict() if pack is not None else None}


def apply_view_presets(blueprint: dict[str, Any],
                       pack: WorkspacePack | None) -> dict[str, Any]:
    """Apply declarative pack presets as defaults; model options always win."""
    if pack is None or not pack.view_presets:
        return blueprint
    result = copy.deepcopy(blueprint)
    presets = {str(item.get("perspective")): dict(item.get("options") or {})
               for item in pack.view_presets}
    for view in result.get("views") or []:
        if not isinstance(view, dict):
            continue
        preset = presets.get(str(view.get("perspective") or ""))
        if not preset:
            continue
        options = dict(preset)
        options.update(view.get("options") or {})
        view["options"] = options
    return result
