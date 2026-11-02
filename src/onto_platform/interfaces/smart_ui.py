"""Platform ⇄ ontology-driven UI bridge.

The ``vibe-可视化engine`` project is a deterministic L3/L4 compiler over an
ontology.  This module adapts a *real* ``ontoPlantform`` workspace to the
engine and then turns the workspace's current runtime state into the payloads
that the compiled OpenUI program queries.

The bridge is deliberately an adapter, not a second domain model:

* the platform ontology stays the source of truth;
* generic semantics (focus object, identity key, time/numeric/status fields and
  related objects) are inferred from the published schema plus real instance
  values, so no domain-specific ``plate`` is required;
* view payloads are computed from the workspace gateway at request time, so
  the generated console shows the current platform data, not the engine demo
  fixtures;
* mutations are routed back through ``platform.capabilities.invoke`` so the
  platform's authorization, ledger and audit path stays in charge.

No business values are invented: when a workspace has no instances the API
returns ``ready: false`` with guidance instead of fabricating a view.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..errors import AccessDeniedError, ValidationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..bootstrap import Platform

ENGINE_VERSION = "0.1.0"
ROLE_OPTIONS = (
    {"id": "owner", "name": "空间所有者", "glyph": "主"},
    {"id": "editor", "name": "运营编辑", "glyph": "运"},
    {"id": "viewer", "name": "只读观察", "glyph": "观"},
)
ROLE_IDS = tuple(role["id"] for role in ROLE_OPTIONS)

PLATE_RE = re.compile(r"[\u4e00-\u9fa5][A-Z][A-Z0-9]{4,7}")

# Engine (source checkout) location.  The platform stays installable without
# the compiler; the smart-ui endpoints simply return a clear 4xx until the
# engine is available or ONTO_UI_ENGINE_SRC points at ``engine/src``.
_ENGINE_MODULES: dict[str, Any] | None = None


def _engine_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("ONTO_UI_ENGINE_SRC", "").strip()
    if configured:
        candidates.append(Path(configured))
    here = Path(__file__).resolve()
    # .../ontoPlantform/src/onto_platform/interfaces/smart_ui.py
    for parent in here.parents:
        if parent.name == "ontoPlantform":
            candidates.append(parent.parent / "vibe-可视化engine" / "engine" / "src")
            break
    candidates.append(Path.cwd() / ".." / "vibe-可视化engine" / "engine" / "src")
    return candidates


def _engine() -> dict[str, Any]:
    global _ENGINE_MODULES
    if _ENGINE_MODULES is not None:
        return _ENGINE_MODULES
    for candidate in _engine_candidates():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not (resolved / "onto_ui_engine" / "__init__.py").is_file():
            continue
        if str(resolved) not in sys.path:
            sys.path.insert(0, str(resolved))
        try:
            import onto_ui_engine  # noqa: PLC0415
            from onto_ui_engine import compiler, models, planner  # noqa: PLC0415
            from onto_ui_engine.capabilities import (  # noqa: PLC0415
                derive_capabilities,
                resolve_query_root,
            )
        except Exception as exc:  # pragma: no cover - environment failure
            raise ValidationError(
                f"smart-ui engine found at {resolved} but could not be imported: {exc}"
            ) from exc
        _ENGINE_MODULES = {
            "compiler": compiler,
            "models": models,
            "planner": planner,
            "derive_capabilities": derive_capabilities,
            "resolve_query_root": resolve_query_root,
            "version": str(getattr(onto_ui_engine, "__version__", ENGINE_VERSION)),
            "source": resolved,
        }
        return _ENGINE_MODULES
    raise ValidationError(
        "smart-ui engine is unavailable; set ONTO_UI_ENGINE_SRC to "
        "vibe-可视化engine/engine/src"
    )


def smart_ui_engine_available() -> bool:
    """Non-throwing capability probe used by health/feature discovery."""
    try:
        _engine()
    except ValidationError:
        return False
    return True


# ---------------------------------------------------------------------------
# Platform data helpers
# ---------------------------------------------------------------------------


def _rows(value: Any) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, dict):
        for key in ("objects", "rows", "items"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _find(platform: "Platform", workspace_id: str, object_type: str) -> list[dict]:
    gateway = platform.ctx.gateway_for(workspace_id)
    try:
        return _rows(gateway.find_objects(object_type))
    except Exception:
        return []


def _related(platform: "Platform", workspace_id: str, ref: str,
             relation: str, direction: str = "out") -> list[str]:
    if not ref or not relation:
        return []
    gateway = platform.ctx.gateway_for(workspace_id)
    try:
        return list(gateway.related(ref, relation, direction=direction) or [])
    except Exception:
        return []


def _props(obj: dict) -> dict[str, Any]:
    props = obj.get("properties")
    return props if isinstance(props, dict) else {}


def _obj_id(obj: dict) -> str:
    return str(obj.get("id") or "")


def _ontology_of(platform: "Platform", workspace_id: str, actor: str) -> dict:
    ontology = platform.ontologies.get(workspace_id, actor)
    if not isinstance(ontology, dict) or not ontology.get("objects"):
        raise ValidationError(
            "当前工作空间还没有已发布的本体；先发布本体再生成智能视图"
        )
    return ontology


def _capabilities_of(platform: "Platform", workspace_id: str, actor: str) -> list[dict]:
    try:
        caps = platform.capabilities.list(workspace_id, actor)
    except Exception:
        return []
    return [cap for cap in caps if isinstance(cap, dict)]


def _action_parameters(cap: dict) -> list[dict[str, Any]]:
    """Normalize capability inputs into smart-UI action parameters."""
    parameters = []
    for item in cap.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        parameters.append({
            "name": name,
            "displayName": str(item.get("description") or name),
            "type": str(item.get("type") or "string"),
            # CapabilityInput currently has no optional flag: declared inputs
            # are required by the capability contract.
            "required": True,
        })
    return parameters


def _executable_capabilities(platform: "Platform", workspace_id: str,
                             actor: str) -> list[dict]:
    """Capabilities the generated action panel can actually invoke.

    Agent implementations and unpublished lifecycle states stay in the platform
    capability page.  Parameterized capabilities are included: the runtime
    renders their declared inputs and sends the collected values as ``args``.
    """
    result = []
    for cap in _capabilities_of(platform, workspace_id, actor):
        key = str(cap.get("key") or "").strip()
        if not key:
            continue
        if str(cap.get("lifecycle") or "published") != "published":
            continue
        implementation = cap.get("implementation") or {}
        if str(implementation.get("kind") or "") == "agent":
            continue
        result.append(cap)
    return result


def _object_specs(ontology: dict) -> list[dict]:
    raw = ontology.get("objects") or {}
    if isinstance(raw, dict):
        return [spec for spec in raw.values() if isinstance(spec, dict)]
    return [spec for spec in raw if isinstance(spec, dict)]


def _relation_specs(ontology: dict) -> list[dict]:
    raw = ontology.get("relations") or {}
    if isinstance(raw, dict):
        return [spec for spec in raw.values() if isinstance(spec, dict)]
    return [spec for spec in raw if isinstance(spec, dict)]


def _property_names(spec: dict) -> dict[str, dict]:
    raw = spec.get("properties") or {}
    if isinstance(raw, dict):
        return {str(name): prop for name, prop in raw.items() if isinstance(prop, dict)}
    return {
        str(prop.get("name")): prop
        for prop in raw
        if isinstance(prop, dict) and prop.get("name")
    }


# ---------------------------------------------------------------------------
# Generic ontology semantics
#
# The bridge used to require a toll-shaped ontology (Vehicle / plate /
# TollStation).  Real workspaces can describe any domain, so the adapter now
# infers a small semantic layer from the ontology spec and the actual records:
# which object is the focus, which property identifies it, which properties are
# temporal/numeric/status fields, and which objects are related.  Nothing below
# assumes a vehicle, a plate, or a station.
# ---------------------------------------------------------------------------

TYPE_LABELS = {
    "vehicle": "车辆",
    "tollstation": "收费站",
    "tolltransaction": "通行交易",
    "passagetransaction": "通行交易",
    "transaction": "交易",
    "lane": "车道",
    "site": "站点",
    "station": "站点",
    "device": "设备",
    "sensor": "传感器",
    "order": "订单",
    "customer": "客户",
    "product": "产品",
    "asset": "资产",
    "ticket": "工单",
    "alert": "告警",
    "leasecontract": "租赁合同",
    "lessee": "承租企业",
    "equipment": "租赁物",
    "rentalpayment": "租金账单",
    "riskalert": "风险预警",
    "contract": "合同",
    "payment": "账单",
    "invoice": "发票",
}

PROPERTY_LABELS = {
    "id": "标识",
    "name": "名称",
    "title": "标题",
    "label": "标签",
    "code": "编码",
    "status": "状态",
    "state": "状态",
    "level": "级别",
    "type": "类型",
    "category": "分类",
    "time": "时间",
    "timestamp": "时间",
    "ts": "时间",
    "passage_time": "通行时间",
    "plate": "车牌",
    "plate_no": "车牌",
    "plate_number": "车牌",
    "plate_color": "车牌颜色",
    "vehicle_type": "车型",
    "owner_type": "归属类型",
    "fee": "费用",
    "toll_fee": "通行费",
    "amount": "金额",
    "price": "价格",
    "balance": "余额",
    "count": "数量",
    "quantity": "数量",
    "score": "分值",
    "temperature": "温度",
    "humidity": "湿度",
    "pressure": "压力",
    "mileage_km": "里程",
    "entry_station": "入口站",
    "exit_station": "出口站",
    "entry_lane": "入口车道",
    "exit_lane": "出口车道",
    "station_id": "站点标识",
    "lane_id": "车道标识",
    "transaction_id": "交易标识",
    "station_code": "站点编码",
    "lane_code": "车道编码",
    "device_id": "设备编号",
    "site_code": "站点编码",
    "contract_id": "合同编号",
    "contract_no": "合同号",
    "lessee_id": "承租企业编号",
    "lessee_name": "承租企业",
    "equipment_id": "租赁物编号",
    "equipment_name": "租赁物名称",
    "contract_amount": "合同金额",
    "monthly_rent": "月租金",
    "start_date": "起租日",
    "end_date": "到期日",
    "overdue_days": "逾期天数",
    "risk_level": "风险等级",
    "credit_score": "信用评分",
    "industry": "所属行业",
    "region": "所属区域",
    "original_value": "原始价值",
    "book_value": "账面价值",
    "condition_status": "设备状态",
    "location": "存放地点",
    "payment_id": "账单编号",
    "payment_status": "缴费状态",
    "due_date": "应缴日期",
    "paid_at": "实缴时间",
    "due_amount": "应缴金额",
    "paid_amount": "实缴金额",
    "alert_id": "预警编号",
    "alert_type": "预警类型",
    "severity": "严重程度",
    "raised_at": "预警时间",
    "alert_status": "预警状态",
}

ID_PROPERTY_HINTS = (
    "id", "uuid", "code", "key", "number", "no",
)
NAME_PROPERTY_HINTS = (
    "name", "title", "label", "display_name", "displayname", "subject",
)
GROUP_PROPERTY_HINTS = (
    "plate", "plate_no", "plate_number", "vehicle_plate",
)
TEMPORAL_NAMES = {
    "ts", "time", "timestamp", "occurred_at", "created_at", "updated_at",
    "entry_time", "exit_time", "passage_time", "event_time", "start_time",
    "end_time", "last_seen", "observed_at", "recorded_at",
}
STATUS_NAMES = {
    "status", "state", "health", "health_status", "level", "severity",
    "stage", "phase", "risk", "result",
}
NUMERIC_NAMES = {
    "fee", "toll_fee", "amount", "price", "balance", "count", "quantity",
    "score", "temperature", "humidity", "pressure", "throughput",
    "avg_queue_length", "mileage_km", "rate_per_km",
}
GEO_LONGITUDE_NAMES = ("longitude", "lon", "lng", "long")
GEO_LATITUDE_NAMES = ("latitude", "lat")
# The platform capability policy requires ``editor`` for every invoke today.
# Keep one source of truth here instead of routing permissions by demo key.
ACTION_ROLES = ("owner", "editor")
_ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _type_label(name: str) -> str:
    return TYPE_LABELS.get(_norm_name(name), name)


def _prop_label(name: str) -> str:
    if name.lower() in PROPERTY_LABELS:
        return PROPERTY_LABELS[name.lower()]
    lowered = name.lower()
    for suffix, label in (
        ("_status", "状态"), ("_state", "状态"), ("_time", "时间"),
        ("_at", "时间"), ("_name", "名称"), ("_code", "编码"),
        ("_id", "标识"), ("_type", "类型"), ("_fee", "费用"),
        ("_count", "数量"), ("_level", "级别"),
    ):
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return label
    return name


def _property_type(prop: dict) -> str:
    return str(prop.get("type") or "string").lower()


def _value_text(value: Any, limit: int = 72) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    elif isinstance(value, (list, tuple)):
        text = "、".join(_value_text(item, limit) for item in value[:4])
    elif isinstance(value, dict):
        text = "、".join(f"{key} {_value_text(item, 30)}" for key, item in list(value.items())[:3])
    else:
        text = str(value)
    text = text.replace("T", " ") if re.match(r"^\d{4}-\d{2}-\d{2}T", text) else text
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _sample(records: list[dict]) -> dict[str, Any]:
    for record in records:
        props = _props(record)
        if props:
            return props
    return {}


def _has_value(value: Any) -> bool:
    return value not in (None, "")


def _is_temporal_prop(name: str, prop: dict, sample: Any) -> bool:
    if str(prop.get("temporal") or "").strip():
        return True
    if _property_type(prop) in {"date", "datetime", "time"}:
        return True
    lowered = name.lower()
    if lowered in TEMPORAL_NAMES or lowered.endswith("_time") or lowered.endswith("_at"):
        return True
    return isinstance(sample, str) and bool(
        re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?", sample)
    )


def _is_numeric_prop(name: str, prop: dict, sample: Any) -> bool:
    if _property_type(prop) in {"number", "integer", "int", "long", "float", "double"}:
        return not any(token in name.lower() for token in ("id", "code", "no"))
    if isinstance(sample, bool) or not isinstance(sample, (int, float)):
        return False
    return not any(token in name.lower() for token in ("id", "code", "no"))


def _is_status_prop(name: str, prop: dict, sample: Any) -> bool:
    if name.lower() in STATUS_NAMES:
        return True
    return isinstance(sample, str) and name.lower().endswith("_status")


def _property_order(spec: dict) -> list[tuple[str, dict]]:
    return list(_property_names(spec).items())


def _pick_property(
    properties: list[tuple[str, dict]],
    hints: tuple[str, ...],
    sample: dict[str, Any],
) -> str | None:
    lowered = {name.lower(): name for name, _ in properties}
    for hint in hints:
        if hint in lowered:
            return lowered[hint]
    for name, _prop in properties:
        if not _has_value(sample.get(name)):
            continue
        for hint in hints:
            if name.lower().endswith(hint):
                return name
    return None


def _best_id_key(properties: list[tuple[str, dict]], type_name: str) -> str | None:
    """Pick the most business-identifying property, not the first ``*_id``.

    A transaction has ``transaction_id`` and ``lane_id``; the former identifies
    the focus object while the latter points at a related object.  Prefer an
    exact ``id``, then a key whose prefix matches the object type, then common
    entity prefixes, and only then any other id/code-like field.
    """
    norm_type = _norm_name(type_name)
    best: tuple[int, str] | None = None
    for name, prop in properties:
        lowered = name.lower()
        if _property_type(prop) not in {"string", "integer", "int", "long"}:
            continue
        score = 0
        if lowered == "id":
            score = 14
        elif lowered in {"uuid", "key", "ref"}:
            score = 12
        elif lowered.endswith("_id") or lowered.endswith("id"):
            score = 4
            prefix = re.sub(r"_?id$", "", lowered)
            if prefix and prefix in norm_type:
                score += 9
            elif prefix in {"transaction", "record", "object", "entity", "order",
                           "device", "asset", "ticket", "event", "alert"}:
                score += 7
        elif lowered.endswith("_code") or lowered.endswith("code"):
            score = 3
            prefix = re.sub(r"_?code$", "", lowered)
            if prefix and prefix in norm_type:
                score += 5
        elif lowered.endswith("_no") or lowered.endswith("number"):
            score = 2
            prefix = re.sub(r"_?no$|_?number$", "", lowered)
            if prefix and prefix in norm_type:
                score += 5
        if score and (best is None or score > best[0]):
            best = (score, name)
    # A bare foreign key such as ``lane_id`` identifies a *related* object, not
    # the focus itself.  Only accept an id when it is plausibly own identity
    # (exact id, type-prefixed, or a well-known entity id); otherwise let the
    # caller fall back to the business group key (plate/name/code).
    return best[1] if best and best[0] >= 7 else None


def _semantic_keys(spec: dict, records: list[dict]) -> dict[str, Any]:
    """Infer identity, label, temporal, numeric and status properties."""
    properties = _property_order(spec)
    sample = _sample(records)
    names = [name for name, _ in properties]
    lowered = {name.lower(): name for name in names}

    def first(hints: tuple[str, ...]) -> str | None:
        for hint in hints:
            if hint in lowered:
                return lowered[hint]
        for name in names:
            if any(name.lower().endswith(hint) for hint in hints if hint in {"id", "code", "no", "key"}):
                return name
        return None

    group_key = None
    for hint in GROUP_PROPERTY_HINTS:
        group_key = lowered.get(hint)
        if group_key:
            break
    if group_key is None:
        group_key = _pick_property(properties, NAME_PROPERTY_HINTS, sample)
    if group_key is None:
        group_key = first(("id", "code", "key", "no"))
    if group_key is None:
        group_key = next((name for name, prop in properties if _property_type(prop) in {"string", "text"}), None)
    if group_key is None and names:
        group_key = names[0]

    id_key = _best_id_key(properties, str(spec.get("name") or "")) or group_key
    label_key = _pick_property(properties, NAME_PROPERTY_HINTS, sample) or group_key

    temporal_key = next(
        (name for name, prop in properties
         if _is_temporal_prop(name, prop, sample.get(name))),
        None,
    )
    numeric_keys = [
        name for name, prop in properties
        if _is_numeric_prop(name, prop, sample.get(name))
    ][:4]
    status_key = next(
        (name for name, prop in properties
         if _is_status_prop(name, prop, sample.get(name))),
        None,
    )
    return {
        "groupKey": group_key,
        "idKey": id_key,
        "labelKey": label_key,
        "timeKey": temporal_key,
        "numericKeys": numeric_keys,
        "statusKey": status_key,
        "properties": properties,
        "sample": sample,
    }


def _record_value(record: dict, key: str | None) -> Any:
    if not key:
        return None
    props = _props(record)
    value = props.get(key)
    if _has_value(value):
        return value
    return record.get("id") if key in {"id", "key"} else None


def _record_group_value(record: dict, keys: dict[str, Any]) -> str:
    value = _record_value(record, keys.get("groupKey"))
    if not _has_value(value):
        value = _record_value(record, keys.get("idKey")) or _obj_id(record)
    return str(value)


def _focus_score(spec: dict, records: list[dict], relations: list[dict]) -> float:
    name = str(spec.get("name") or "")
    lowered = name.lower()
    semantic = _semantic_keys(spec, records)
    score = 10.0 + min(len(records), 50) / 10
    if any(word in lowered for word in
           ("transaction", "passage", "pass", "record", "event", "order", "log",
            "ticket", "reading", "alert", "measurement", "sample")):
        score += 7
    if any(word in lowered for word in ("contract", "policy", "account", "invoice")):
        score += 5
    if semantic["timeKey"]:
        score += 4
    if semantic["numericKeys"]:
        score += 3
    if semantic["statusKey"]:
        score += 2
    if semantic["groupKey"]:
        score += 1
    score += min(len([
        rel for rel in relations
        if rel.get("source") == name or rel.get("target") == name
    ]), 4)
    return score


def _select_focus(platform: "Platform", workspace_id: str, ontology: dict,
                  focus: dict[str, Any] | None = None) -> dict[str, Any]:
    """Choose a focus object for *any* published ontology.

    Selection is deterministic: a fact/record-like object with real instances
    and rich semantics wins over dimensions, but a dimension is also valid when
    it is the only object with data.  No ``plate`` is required anywhere.
    """
    specs = _object_specs(ontology)
    relations = _relation_specs(ontology)
    requested_focus = focus
    records_by_type: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    candidates: list[tuple[float, int, str, dict, list[dict]]] = []
    for spec in specs:
        name = str(spec.get("name") or "")
        if not name:
            continue
        records = _find(platform, workspace_id, name)
        records_by_type[name] = records
        counts[name] = len(records)
        if records:
            candidates.append((_focus_score(spec, records, relations), len(records), name, spec, records))
    if not candidates:
        return {
            "focus": None,
            "focusName": None,
            "records": [],
            "recordsByType": records_by_type,
            "counts": counts,
            "relations": relations,
            "specs": specs,
            "ready": False,
        }
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    _score, _count, _name, focus, records = candidates[0]
    focus_name = str(focus.get("name"))
    keys = _semantic_keys(focus, records)
    ordered = sorted(records, key=lambda item: str(_record_value(item, keys["timeKey"]) or ""), reverse=True)
    focus_record = ordered[0] if ordered else records[0]
    focus_value = _record_group_value(focus_record, keys)
    value_groups = [record for record in records
                    if _record_group_value(record, keys) == focus_value]
    if not value_groups:
        value_groups = [focus_record]

    # An explicit user/blueprint focus must actually scope the bundle.  Before
    # this, ``context.focus`` only reached the audit intent and the rendered
    # view stayed on the platform's default focus (U7).
    if isinstance(requested_focus, dict) and requested_focus.get("key") \
            and requested_focus.get("value"):
        target_name = str(requested_focus.get("object") or focus_name)
        target_records = records_by_type.get(target_name) or []
        target_spec = next(
            (item for item in specs if str(item.get("name") or "") == target_name),
            None,
        )
        target_key = str(requested_focus.get("key") or "")
        target_value = str(requested_focus.get("value"))
        if target_spec is not None and target_records:
            matched = [
                record for record in target_records
                if str(_record_value(record, target_key)) == target_value
            ]
            if matched:
                focus = target_spec
                focus_name = target_name
                records = target_records
                keys = _semantic_keys(target_spec, target_records)
                focus_record = matched[0]
                focus_value = target_value
                value_groups = matched

    def related_with(predicate) -> dict | None:
        for spec in specs:
            name = str(spec.get("name") or "")
            if name == focus_name or not records_by_type.get(name):
                continue
            if predicate(spec, _semantic_keys(spec, records_by_type[name])):
                return spec
        return None

    station = related_with(lambda spec, sem: bool(sem["statusKey"]))
    vehicle = related_with(
        lambda spec, sem: any(key in _property_names(spec) for key in GROUP_PROPERTY_HINTS)
    )
    lane = next(
        (spec for spec in specs if "lane" in str(spec.get("name") or "").lower()),
        None,
    )
    return {
        "focus": focus,
        "focusName": focus_name,
        "focusLabel": _type_label(focus_name),
        "focusValue": focus_value,
        "focusRecord": focus_record,
        "records": records,
        "group": value_groups,
        "recordsByType": records_by_type,
        "counts": counts,
        "relations": relations,
        "specs": specs,
        "ontology": ontology,
        "keys": keys,
        "ready": True,
        "stationObject": station.get("name") if station else None,
        "vehicleObject": vehicle.get("name") if vehicle else None,
        "laneObject": lane.get("name") if lane else None,
    }


def _foreign_value_matches(
    focus_record: dict | None,
    target_type: str,
    target_records: list[dict],
    keys: dict[str, Any],
) -> list[dict]:
    """Return target records whose id/label/group value appears on the focus."""
    if not focus_record:
        return []
    props = _props(focus_record)
    values = {str(value) for value in props.values() if _has_value(value)}
    for key in (keys.get("groupKey"), keys.get("idKey")):
        value = _record_value(focus_record, key)
        if _has_value(value):
            values.add(str(value))
    matches: list[dict] = []
    for record in target_records:
        target_values = {str(_obj_id(record))}
        for name, value in _props(record).items():
            if not _has_value(value):
                continue
            if name.lower() in STATUS_NAMES or name.lower().endswith("_status"):
                continue
            target_values.add(str(value))
        if values & target_values:
            matches.append(record)
    return matches


def _related_objects(platform: "Platform", workspace_id: str,
                     context: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve real related objects through ontology links and foreign values."""
    focus_name = context.get("focusName")
    focus_record = context.get("focusRecord")
    if not focus_name or not focus_record:
        return []
    records_by_type = context.get("recordsByType") or {}
    focus_id = _obj_id(focus_record)
    seen: set[tuple[str, str]] = set()
    candidates: list[dict[str, Any]] = []

    def add(type_name: str, record: dict | None, relation: str, direction: str) -> None:
        if not type_name or type_name == focus_name or record is None:
            return
        key = (type_name, _obj_id(record))
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "type": type_name,
            "record": record,
            "relation": relation or "related",
            "direction": direction,
        })

    for relation in context.get("relations") or []:
        name = str(relation.get("name") or "")
        source = str(relation.get("source") or "")
        target = str(relation.get("target") or "")
        if not name:
            continue
        if source == focus_name and target in records_by_type:
            for ref in _related(platform, workspace_id, f"{focus_name}/{focus_id}", name, "out"):
                rid = ref.split("/", 1)[-1]
                record = next((item for item in records_by_type[target]
                               if _obj_id(item) == rid), None)
                add(target, record, name, "out")
            for record in _foreign_value_matches(
                    focus_record, target, records_by_type[target], context.get("keys") or {}):
                add(target, record, name, "out")
        elif target == focus_name and source in records_by_type:
            for ref in _related(platform, workspace_id, f"{focus_name}/{focus_id}", name, "in"):
                rid = ref.split("/", 1)[-1]
                record = next((item for item in records_by_type[source]
                               if _obj_id(item) == rid), None)
                add(source, record, name, "in")
            for record in records_by_type[source]:
                props = _props(record)
                if any(str(value) in {focus_id, context.get("focusValue")}
                       for value in props.values() if _has_value(value)):
                    add(source, record, name, "in")

    # Value-based association even when the ontology does not declare a link:
    # e.g. two objects carrying the same plate/device code.  This is a data
    # association, not a fabricated ontology relation.
    for type_name, records in records_by_type.items():
        if type_name == focus_name:
            continue
        for record in _foreign_value_matches(
                focus_record, type_name, records, context.get("keys") or {}):
            add(type_name, record, "same_key", "value")

    # Keep the graph legible: represent every related type once before filling
    # the remaining slots with additional instances of a chatty relation.
    balanced: list[dict[str, Any]] = []
    represented: set[str] = set()
    for item in candidates:
        if item["type"] not in represented:
            represented.add(item["type"])
            balanced.append(item)
    balanced_keys = {(item["type"], _obj_id(item["record"])) for item in balanced}
    for item in candidates:
        key = (item["type"], _obj_id(item["record"]))
        if key not in balanced_keys:
            balanced.append(item)
    return balanced[:8]



def _type_of(prop: dict) -> str:
    raw = str(prop.get("type") or "string").lower()
    return {
        "integer": "int",
        "int": "int",
        "long": "long",
        "number": "double",
        "float": "double",
        "double": "double",
        "boolean": "bool",
        "bool": "bool",
        "date": "date",
        "datetime": "datetime",
        "time": "time",
    }.get(raw, "string")


def _temporal_names(spec: dict) -> set[str]:
    names: set[str] = set()
    for name, prop in _property_names(spec).items():
        if str(prop.get("temporal") or "").strip():
            names.add(name)
        if name.lower() in {
            "ts", "time", "timestamp", "occurred_at", "created_at", "updated_at",
            "entry_time", "exit_time",
        }:
            names.add(name)
    return names


def _action_roles(_key: str) -> tuple[str, ...]:
    """Roles allowed to see/execute a capability action.

    Platform ``capability.invoke`` requires editor today, regardless of the
    capability key.  Never advertise a write action to a viewer just because
    its key is not in a demo-specific list.
    """
    return ACTION_ROLES


# ---------------------------------------------------------------------------
# Platform ontology → engine ontology
# ---------------------------------------------------------------------------


def build_engine_ontology(platform: "Platform", workspace_id: str, actor: str,
                          ontology_data: dict | None = None,
                          context: dict[str, Any] | None = None):
    modules = _engine()
    models = modules["models"]
    ontology_data = ontology_data or _ontology_of(platform, workspace_id, actor)
    context = context or _select_focus(platform, workspace_id, ontology_data)
    if not context.get("ready"):
        raise ValidationError(
            "当前工作空间没有可用的对象实例；先连接数据源并运行投影，"
            "智能视图不会为空工作空间伪造数据"
        )
    specs = context["specs"]
    relations = context["relations"]
    focus_name = str(context["focusName"])
    focus_label = str(context["focusLabel"])
    keys = context["keys"]

    objects = []
    for spec in specs:
        name = str(spec.get("name") or "")
        if not name:
            continue
        properties = []
        for prop_name, prop in _property_order(spec):
            data_type = _type_of(prop)
            temporal = _is_temporal_prop(prop_name, prop, keys["sample"].get(prop_name))
            if temporal:
                data_type = "datetime" if data_type == "string" else data_type
            properties.append(
                models.Property(
                    name=prop_name,
                    display_name=_prop_label(prop_name),
                    data_type=data_type,
                    required=bool(prop.get("required")),
                    description=str(prop.get("description") or ""),
                )
            )
        if name == focus_name:
            if not any(prop.name == "is_abnormal" for prop in properties):
                properties.append(models.Property(
                    name="is_abnormal",
                    display_name="是否异常",
                    data_type="bool",
                    derived=True,
                    description="由焦点对象的实时状态与关键指标派生",
                ))
        objects.append(
            models.ObjectType(
                name=name,
                display_name=_type_label(name),
                role="aggregate_root" if name == focus_name else "dimension",
                properties=tuple(properties),
                description=str(spec.get("description") or ""),
            )
        )

    links = []
    used_links: set[tuple[str, str, str]] = set()
    connected: set[str] = {focus_name}
    for rel in relations:
        name = str(rel.get("name") or "")
        source = str(rel.get("source") or "")
        target = str(rel.get("target") or "")
        if not (name and source and target):
            continue
        links.append(models.LinkType(
            name=name,
            display_name=name,
            source_type=source,
            target_type=target,
            cardinality=str(rel.get("cardinality") or "one-to-many"),
        ))
        used_links.add((source, target, name))
        if source == focus_name:
            connected.add(target)
        if target == focus_name:
            connected.add(source)

    # Value-derived associations (for ontologies that do not declare a link)
    # get one transparent derived link each, so the generic relationship view
    # has real evidence and actions on the related type stay reachable.  These
    # links are labelled "关联对象", never presented as ontology declarations.
    related_context = _related_objects(platform, workspace_id, context)
    for item in related_context:
        type_name = str(item.get("type") or "")
        if not type_name or type_name in connected:
            continue
        link_name = f"related_{type_name.lower()}"
        if (focus_name, type_name, link_name) not in used_links:
            links.append(models.LinkType(
                name=link_name,
                display_name=f"关联{_type_label(type_name)}",
                source_type=focus_name,
                target_type=type_name,
            ))
            used_links.add((focus_name, type_name, link_name))
            connected.add(type_name)

    actions = []
    executable = _executable_capabilities(platform, workspace_id, actor)
    spec_names = {str(spec.get("name")) for spec in specs}
    records_by_type = context.get("recordsByType") or {}
    related_types = {str(item.get("type")) for item in related_context}
    for cap in executable:
        key = str(cap.get("key") or "").strip()
        target_type = str(cap.get("target_type") or "") or focus_name
        # Do not advertise an action that cannot resolve a real instance of
        # its target type.  Silently retargeting to the focus type was how a
        # click could hit an unintended object.
        if target_type not in spec_names or not records_by_type.get(target_type):
            continue
        if target_type != focus_name and target_type not in related_types:
            continue
        roles = _action_roles(key)
        parameters = tuple(
            models.ActionParameter(
                name=item["name"],
                display_name=item["displayName"],
                type=item["type"],
                required=True,
            )
            for item in _action_parameters(cap)
        )
        actions.append(models.Action(
            action_id=key,
            name=key,
            display_name=str(cap.get("name") or key),
            description=str(cap.get("description") or ""),
            primary_object_type=target_type,
            roles=roles,
            parameters=parameters,
            enabled=True,
            provenance="platform_capability",
        ))
        if target_type != focus_name and target_type not in connected:
            link_name = f"capability_{target_type.lower()}"
            links.append(models.LinkType(
                name=link_name,
                display_name="可执行能力对象",
                source_type=focus_name,
                target_type=target_type,
            ))
            used_links.add((focus_name, target_type, link_name))
            connected.add(target_type)

    related_fields = tuple(
        name for name, _prop in keys["properties"]
        if name not in {keys["groupKey"], keys["idKey"]}
    )[:3]
    if not related_fields:
        related_fields = tuple(name for name, _prop in keys["properties"])[:1]
    rule = models.Rule(
        name=f"{focus_name}.state_anomaly",
        display_name=f"{focus_label}状态解释",
        output_type="string",
        related_fields=related_fields,
        business_cause=f"{focus_label}的实时状态或关键指标偏离预期",
        business_effect="需要运营人员结合平台当前数据确认原因与处置方式",
        causal_chain=(
            f"{focus_label}实例由平台投影写入运行时状态",
            "智能视图读取同一工作空间的本体、实例与关联对象",
            "状态、规则与统计按本体能力展开解释",
        ),
        reason_template=f"{focus_label}的业务状态需要结合当前平台数据解释",
        description=f"{focus_name} 的状态解释规则（由平台本体与实时数据适配）",
    )

    ontology = models.Ontology(
        version=str(ontology_data.get("version") or "1"),
        namespace=str(ontology_data.get("name") or "platform"),
        objects=tuple(objects),
        links=tuple(links),
        rules=(rule,),
        actions=tuple(actions),
    )
    return ontology, {
        "focusObject": focus_name,
        "focusLabel": focus_label,
        "focusValue": context["focusValue"],
        "identityKey": keys["idKey"],
        "identityLabel": _prop_label(str(keys["idKey"] or "")),
        "relatedObjects": [
            {"type": item["type"], "relation": item["relation"]}
            for item in related_context
        ],
        # Compatibility aliases for existing hosts/tests.  They are inferred,
        # not required by the planner.
        "vehicleObject": context.get("vehicleObject"),
        "stationObject": context.get("stationObject"),
        "laneObject": context.get("laneObject"),
        "relationNames": sorted({str(rel.get("name")) for rel in relations if rel.get("name")}),
        "source": "ontoPlantform ontology",
        "enginePackage": "onto_ui_engine",
    }


# ---------------------------------------------------------------------------
# Runtime state → view payloads
# ---------------------------------------------------------------------------


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_time(value: str) -> str:
    if not value:
        return "—"
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.strftime("%H:%M:%S")
    except ValueError:
        return value[11:19] if len(value) >= 19 else value


def _fmt_date(value: str) -> str:
    if not value:
        return "—"
    return value[5:10] if len(value) >= 10 else value


def _semantics_for_type(context: dict[str, Any], type_name: str) -> dict[str, Any]:
    spec = next(
        (item for item in context.get("specs") or []
         if str(item.get("name") or "") == type_name),
        None,
    )
    if spec is None:
        return {}
    return _semantic_keys(spec, context.get("recordsByType", {}).get(type_name, []))


def _display_of(record: dict | None, semantics: dict[str, Any]) -> str:
    if record is None:
        return "—"
    for key in (semantics.get("labelKey"), semantics.get("groupKey"), semantics.get("idKey")):
        value = _record_value(record, key)
        if _has_value(value):
            return _value_text(value)
    return _obj_id(record) or "—"


def _primary_numeric(
    context: dict[str, Any], keys: dict[str, Any]
) -> tuple[str | None, str, float | None]:
    for name in keys.get("numericKeys") or []:
        record = context.get("focusRecord")
        value = _as_float(_record_value(record, name), default=float("nan"))
        if value == value:  # not NaN
            return name, _prop_label(name), value
    return None, "关键指标", None


def _status_text(value: Any) -> str:
    text = str(value or "").strip()
    return {
        "free": "畅通", "busy": "繁忙", "congested": "拥堵",
        "online": "在线", "offline": "离线", "normal": "正常",
        "warning": "警告", "error": "错误", "failed": "失败",
        "critical": "严重", "alarm": "告警", "danger": "危险",
        "healthy": "健康", "degraded": "降级", "unknown": "未知",
        "high": "高", "medium": "中", "low": "低",
        "open": "开启", "closed": "关闭", "active": "活跃", "inactive": "未激活",
        "paid": "已缴", "pending": "待缴", "overdue": "逾期",
        "watch": "关注", "risk": "风险", "settled": "已结清",
        "in_use": "使用中", "idle": "闲置", "maintenance": "维保中",
        "abnormal": "异常", "unavailable": "不可用", "stopped": "停用",
        "running": "运行中", "completed": "已完成", "success": "成功",
    }.get(text.lower(), text or "未知")


_STATUS_HIGH = {
    "congested", "critical", "error", "failed", "offline", "high", "alarm",
    "danger", "overdue", "risk", "abnormal", "unavailable", "stopped",
    "异常", "故障", "失败", "错误", "严重", "告警", "报警", "危险", "离线",
    "停用", "禁用", "逾期", "风险", "高", "拥堵", "堵塞", "不可用", "中断",
}
_STATUS_MEDIUM = {
    "busy", "warning", "degraded", "medium", "alert", "watch", "pending",
    "繁忙", "警告", "降级", "中", "关注", "待处理", "待缴", "预警", "延迟",
}
_STATUS_LOW = {
    "normal", "healthy", "free", "online", "idle", "settled", "paid",
    "正常", "健康", "畅通", "在线", "空闲", "已结清", "已缴", "完成", "低",
}
_STATUS_COMPOSITE = {
    "高风险": "HIGH", "中风险": "MEDIUM", "低风险": "LOW",
    "严重异常": "HIGH", "轻微异常": "MEDIUM",
}


def _status_risk(value: Any) -> str:
    """Normalize English and Chinese status values to a risk band.

    Real ontologies commonly store ``正常/异常/停用`` rather than English
    enums.  Unknown values stay LOW (the old behavior), but known Chinese
    values must never silently render as "运行正常".
    """
    text = str(value or "").strip().lower()
    if not text:
        return "LOW"
    composite = _STATUS_COMPOSITE.get(text)
    if composite:
        return composite
    if text in _STATUS_HIGH:
        return "HIGH"
    if text in _STATUS_MEDIUM:
        return "MEDIUM"
    if text in _STATUS_LOW:
        return "LOW"
    # Compound labels such as ``异常告警`` / ``正常-在线``.  Single-character
    # labels are handled by exact matching above so "中国" is not "中".
    if any(len(token) > 1 and token in text for token in _STATUS_HIGH):
        return "HIGH"
    if any(len(token) > 1 and token in text for token in _STATUS_MEDIUM):
        return "MEDIUM"
    return "LOW"


def _tone_for_status(value: Any) -> str:
    risk = _status_risk(value)
    return "danger" if risk == "HIGH" else ("warning" if risk == "MEDIUM" else "neutral")


def _status_is_bad(value: Any) -> bool:
    return _status_risk(value) != "LOW"


def _related_status(
    context: dict[str, Any], related: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None, Any]:
    """Resolve the most authoritative status.

    A focus object with its own status wins over a related object; otherwise
    fall back to the first related instance that carries one.  This avoids a
    transaction showing its station's status while its own status is normal.
    """
    focus_key = (context.get("keys") or {}).get("statusKey")
    focus_value = _record_value(context.get("focusRecord"), focus_key)
    if focus_key and _has_value(focus_value):
        return None, focus_key, focus_value
    for item in related:
        semantics = _semantics_for_type(context, item["type"])
        key = semantics.get("statusKey")
        value = _record_value(item["record"], key)
        if key and _has_value(value):
            return item, key, value
    return None, None, None


def _geo_coordinates(record: dict | None) -> tuple[float, float] | None:
    if record is None:
        return None
    props = _props(record)
    lowered = {str(name).lower(): value for name, value in props.items()}
    lon = next((lowered[name] for name in GEO_LONGITUDE_NAMES if name in lowered), None)
    lat = next((lowered[name] for name in GEO_LATITUDE_NAMES if name in lowered), None)
    lon_value = _as_float(lon, default=float("nan"))
    lat_value = _as_float(lat, default=float("nan"))
    if lon_value != lon_value or lat_value != lat_value:  # NaN
        return None
    if not (-180 <= lon_value <= 180 and -90 <= lat_value <= 90):
        return None
    return lon_value, lat_value


def _location_payload(platform: "Platform", workspace_id: str,
                      context: dict[str, Any], related: list[dict[str, Any]],
                      workspace_name: str) -> dict[str, Any]:
    """Build the generic ``view_location`` payload from real geo properties.

    The engine derives a location capability when the focus or a directly
    related object carries longitude/latitude.  The adapter must therefore
    provide a payload for that capability; otherwise the compiled program asks
    for ``view_location`` and the runtime raises ``E_TOOL_UNKNOWN``.
    """
    points: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(record: dict, semantics: dict[str, Any], kind: str) -> None:
        coordinates = _geo_coordinates(record)
        if coordinates is None:
            return
        point_id = _obj_id(record) or f"point-{len(points)}"
        if point_id in seen:
            return
        seen.add(point_id)
        status_key = semantics.get("statusKey")
        status_value = _record_value(record, status_key)
        points.append({
            "id": point_id,
            "label": _display_of(record, semantics),
            "kind": kind,
            "lng": coordinates[0],
            "lat": coordinates[1],
            "time": _fmt_time(str(_record_value(record, semantics.get("timeKey")) or "")),
            "tone": _tone_for_status(status_value) if status_key else "neutral",
        })

    focus_semantics = context.get("keys") or {}
    # Prefer the focus group (its own location history), then real related
    # objects (e.g. a site/station carrying coordinates).
    for record in context.get("group") or []:
        add(record, focus_semantics, "gate")
    add(context.get("focusRecord") or {}, focus_semantics, "gate")
    for item in related:
        semantics = _semantics_for_type(context, str(item.get("type") or ""))
        add(item["record"], semantics, "station")
        if len(points) >= 8:
            break
    return {
        "region": workspace_name,
        "points": points[:8],
        "distanceKm": 0,
        "durationMin": 0,
        "mode": "points",
        "summary": f"{len(points[:8])} 个位置点",
    }


def _table_request(blueprint: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(blueprint, dict):
        return None
    for view in blueprint.get("views") or []:
        if not isinstance(view, dict):
            continue
        options = view.get("options") or {}
        if options.get("chartType") == "table" or options.get("display") == "table":
            return options
    return None


def _relative_cutoff(target: str) -> datetime | None:
    match = re.fullmatch(r"^-(\d+)\s*([dhwm])$", str(target or "").strip(),
                         re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = {"h": 3600, "d": 86400, "w": 7 * 86400, "m": 30 * 86400}[unit]
    return datetime.now(timezone.utc) - timedelta(seconds=amount * seconds)


def _as_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _table_match(value: Any, op: str, target: Any) -> bool:
    text = "" if value is None else str(value)
    if op in {"gt", "gte", "lt", "lte"}:
        cutoff = _relative_cutoff(str(target)) if isinstance(target, str) else None
        current = _as_datetime(value)
        if cutoff is not None and current is not None:
            left, right = current, cutoff
        else:
            try:
                left = float(text)
                right = float(target)
            except (TypeError, ValueError):
                left, right = text, str(target)
        return {
            "gt": left > right, "gte": left >= right,
            "lt": left < right, "lte": left <= right,
        }[op]
    if isinstance(target, bool) or isinstance(value, bool):
        flag = text.strip().lower() in {"true", "1", "yes", "是"}
        return (flag == target) if op == "eq" else (flag != target)
    if op in {"eq", "ne"}:
        try:
            left: Any = float(text)
            right = float(target)
        except (TypeError, ValueError):
            left, right = text.strip().lower(), str(target).strip().lower()
        return (left == right) if op == "eq" else (left != right)
    if op == "contains":
        return str(target).lower() in text.lower()
    if op == "in":
        candidates = target if isinstance(target, (list, tuple, set)) else [target]
        return text.lower() in {str(item).strip().lower() for item in candidates}
    return False


def _table_payload(context: dict[str, Any],
                   options: dict[str, Any]) -> dict[str, Any]:
    """Rows for a Blueprint v1.1 DataTable, derived from real workspace records.

    Filters/sort/limit are applied to the same record set the bundle already
    uses; unknown fields were pruned by the Studio adjudicator and the engine.
    """
    records = list(context.get("records") or [])
    spec = context.get("focus") or {}
    raw_props = spec.get("properties") if isinstance(spec, dict) else None
    if isinstance(raw_props, dict):
        props = [str(name) for name in raw_props]
    else:
        props = [
            str(prop.get("name") if isinstance(prop, dict)
                else getattr(prop, "name", ""))
            for prop in (raw_props or [])
        ]
    props = [name for name in props if name]
    filters = options.get("filters") or []
    if filters:
        records = [
            record for record in records
            if all(_table_match(_record_value(record, str(item.get("field"))),
                                str(item.get("op")), item.get("value"))
                   for item in filters)
        ]
    sort = options.get("sort") or {}
    group_by = str(options.get("groupBy") or "")
    try:
        limit = int(options.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(200, limit))

    if group_by:
        metric = options.get("metric") or {}
        metric_field = str(metric.get("field") or "")
        agg = str(metric.get("agg") or "count")
        buckets: dict[str, list[dict]] = {}
        for record in records:
            key = _value_text(_record_value(record, group_by)) or "未分组"
            buckets.setdefault(key, []).append(record)

        def aggregate(bucket: list[dict]) -> float:
            if agg == "count" or not metric_field:
                return float(len(bucket))
            values = [
                _as_float(_record_value(record, metric_field), default=float("nan"))
                for record in bucket
            ]
            values = [value for value in values if value == value]
            if not values:
                return 0.0
            if agg == "sum":
                return sum(values)
            if agg == "avg":
                return sum(values) / len(values)
            if agg == "min":
                return min(values)
            return max(values)

        grouped = [
            (key, aggregate(bucket)) for key, bucket in buckets.items()
        ]
        field = str(sort.get("field") or "")
        if field == group_by:
            grouped.sort(key=lambda item: item[0],
                         reverse=sort.get("direction") != "asc")
        elif field:
            grouped.sort(key=lambda item: item[1],
                         reverse=sort.get("direction") != "asc")
        total = len(grouped)
        grouped = grouped[:limit]
        columns = [group_by, "__value"]
        labels = {group_by: _prop_label(group_by)}
        if agg == "count" or not metric_field:
            labels["__value"] = "记录数"
            rows = [
                {group_by: key, "__value": f"{value:.0f}"}
                for key, value in grouped
            ]
        else:
            agg_label = {"sum": "合计", "avg": "均值", "min": "最小",
                         "max": "最大"}.get(agg, agg)
            labels["__value"] = f"{_prop_label(metric_field)}{agg_label}"
            rows = [
                {group_by: key, "__value": f"{value:.2f}"}
                for key, value in grouped
            ]
        return {
            "columns": columns,
            "columnLabels": labels,
            "rows": rows,
            "total": total,
        }

    if isinstance(sort, dict) and sort.get("field"):
        field = str(sort["field"])

        def sort_key(record: dict) -> tuple[bool, Any]:
            value = _record_value(record, field)
            if value is None:
                return True, ""
            try:
                return False, float(value)
            except (TypeError, ValueError):
                return False, str(value)

        records.sort(key=sort_key, reverse=sort.get("direction") != "asc")
    requested_columns = [str(item) for item in options.get("columns") or []
                         if str(item) in props]
    columns = requested_columns or props[:12]
    total = len(records)
    records = records[:limit]
    rows = [
        {column: _value_text(_record_value(record, column)) or ""
         for column in columns}
        for record in records
    ]
    return {
        "columns": columns,
        "columnLabels": {column: _prop_label(column) for column in columns},
        "rows": rows,
        "total": total,
    }


def build_bundle(platform: "Platform", workspace_id: str, actor: str,
                 ontology_data: dict, bridge: dict, question: str,
                 context: dict[str, Any] | None = None,
                 blueprint: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or _select_focus(platform, workspace_id, ontology_data)
    if not context.get("ready"):
        raise ValidationError(
            "当前工作空间没有可用的对象实例；先连接数据源并运行投影，"
            "智能视图不会为空工作空间伪造数据"
        )
    focus_name = str(context["focusName"])
    focus_label = str(context["focusLabel"])
    focus_record = context["focusRecord"]
    group = context["group"]
    keys = context["keys"]
    related = _related_objects(platform, workspace_id, context)
    related_labels = {
        item["type"]: _display_of(
            item["record"], _semantics_for_type(context, item["type"]))
        for item in related
    }

    label = _display_of(focus_record, keys)
    identity_value = _record_value(focus_record, keys["idKey"])
    group_value = _record_group_value(focus_record, keys)
    time_value = _record_value(focus_record, keys["timeKey"])
    numeric_key, numeric_label, numeric_value = _primary_numeric(context, keys)
    status_item, status_key, status_value = _related_status(context, related)
    status_label = _status_text(status_value) if status_key else ""
    status_object_label = (
        _display_of(status_item["record"], _semantics_for_type(context, status_item["type"]))
        if status_item else ""
    )
    risk = _status_risk(status_value)

    # -- identity -----------------------------------------------------------
    def _fact_text(name: str, value: Any) -> str:
        lowered = name.lower()
        if (name == keys["statusKey"] or lowered in STATUS_NAMES
                or lowered.endswith(("_status", "_level"))):
            return _status_text(value)
        return _value_text(value)

    facts: list[dict[str, str]] = []
    seen_fact_keys: set[str] = set()
    for candidate in (
        keys["idKey"], keys["groupKey"], keys["timeKey"],
        *(keys["numericKeys"] or []), *(keys["statusKey"],),
    ):
        if not candidate or candidate in seen_fact_keys:
            continue
        seen_fact_keys.add(candidate)
        value = _record_value(focus_record, candidate)
        if _has_value(value):
            facts.append({"label": _prop_label(candidate), "value": _fact_text(candidate, value)})
    for prop_name, _prop in keys["properties"]:
        if len(facts) >= 6:
            break
        if prop_name in seen_fact_keys:
            continue
        value = _record_value(focus_record, prop_name)
        if _has_value(value):
            facts.append({"label": _prop_label(prop_name), "value": _fact_text(prop_name, value)})
            seen_fact_keys.add(prop_name)

    tags = ["平台实时投影", f"对象类型：{focus_label}"]
    if len(group) > 1:
        tags.append(f"同组记录：{len(group)}")
    if status_label:
        tags.append(f"状态：{status_label}")
    if risk == "HIGH":
        tags.append("重点关注")
    headline_parts = [f"{focus_label} {label}"]
    if status_label:
        headline_parts.append(f"当前{status_label}")
    if related_labels:
        headline_parts.append(f"关联 {len(related)} 个对象")
    headline_parts.append(f"共 {len(group)} 条平台记录")
    identity = {
        # Legacy toll-shaped fields are kept for the existing renderer.  The
        # generic fields below are authoritative when present.
        "plate": label,
        "plateColor": "",
        "vehicleType": focus_label,
        "etcType": "",
        "recognizedType": "",
        "axleCount": 0,
        "riskLevel": risk,
        "riskLabel": {"HIGH": "高关注", "MEDIUM": "需关注", "LOW": "正常"}[risk],
        "passCount": len(group),
        "owner": "—",
        "passId": _obj_id(focus_record) or "—",
        "passTime": _value_text(time_value) if _has_value(time_value) else "—",
        "station": status_object_label or "—",
        "fee": f"{numeric_value:.2f}" if numeric_value is not None else "—",
        "feeRule": numeric_label,
        "tags": tags,
        "headline": "；".join(headline_parts) + "。",
        "objectType": focus_name,
        "objectLabel": focus_label,
        "title": label,
        "identityLabel": _prop_label(str(keys["idKey"] or "")),
        "identityValue": _value_text(identity_value),
        "subtitle": f"{focus_label} · {_prop_label(str(keys['groupKey'] or ''))} {group_value}",
        "facts": facts,
    }

    # -- relationship -------------------------------------------------------
    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: set[str] = set()
    center_id = f"focus_{_obj_id(focus_record)}"

    def add_node(node_id: str, type_name: str, node_label: str, sub: str,
                 tone: str = "neutral", rank: int = 1) -> str:
        if node_id not in node_ids:
            node_ids.add(node_id)
            nodes.append({
                "id": node_id, "type": type_name, "label": node_label,
                "sub": sub, "tone": tone, "rank": rank,
            })
        return node_id

    def add_edge(source: str, target: str, edge_label: str) -> None:
        edge = {"source": source, "target": target, "label": edge_label}
        if edge not in edges:
            edges.append(edge)

    center_sub = " · ".join(
        part for part in (
            _value_text(time_value) if _has_value(time_value) else "",
            status_label,
            f"{numeric_label} {numeric_value:.2f}" if numeric_value is not None else "",
        ) if part
    ) or _value_text(identity_value)
    add_node(
        center_id, focus_name, label, center_sub,
        "danger" if risk == "HIGH" else ("warning" if risk == "MEDIUM" else "neutral"),
        0,
    )
    for item in related:
        type_name = item["type"]
        semantics = _semantics_for_type(context, type_name)
        node_label = _display_of(item["record"], semantics)
        status_value_for_node = _record_value(item["record"], semantics.get("statusKey"))
        sub_parts = [f"{item['relation']}"]
        if _has_value(status_value_for_node):
            sub_parts.append(_status_text(status_value_for_node))
        key_value = _record_value(item["record"], semantics.get("idKey"))
        if _has_value(key_value) and _value_text(key_value) != node_label:
            sub_parts.append(_value_text(key_value))
        add_node(
            f"{type_name}_{_obj_id(item['record'])}",
            type_name,
            node_label,
            " · ".join(sub_parts),
            _tone_for_status(status_value_for_node),
            1,
        )
        add_edge(center_id, f"{type_name}_{_obj_id(item['record'])}", item["relation"])
    if not related:
        for record in group[1:3]:
            other_id = f"focus_{_obj_id(record)}"
            other_label = _display_of(record, keys)
            add_node(
                other_id, focus_name, other_label,
                f"{_prop_label(str(keys['timeKey'] or ''))} "
                f"{_value_text(_record_value(record, keys['timeKey']))}",
                "neutral", 2,
            )
            add_edge(center_id, other_id, "同组记录")

    relationship = {
        "nodes": nodes,
        "edges": edges,
        "centerId": center_id if nodes else "",
        "typeLabels": {node["type"]: _type_label(node["type"]) for node in nodes},
        "ariaLabel": f"{focus_label}关系网络",
    }

    # -- timeline -----------------------------------------------------------
    events = []
    if keys["timeKey"]:
        ordered = sorted(group, key=lambda item: str(_record_value(item, keys["timeKey"]) or ""))
        for index, record in enumerate(ordered):
            props = _props(record)
            detail_parts = [
                f"{_prop_label(name)} {_value_text(props.get(name))}"
                for name in (keys["numericKeys"] or [])[:2]
                if _has_value(props.get(name))
            ]
            if len(group) > 1:
                detail_parts.insert(
                    0, f"同一 {_prop_label(str(keys['groupKey'] or ''))} {group_value}")
            events.append({
                "id": f"ev_{_obj_id(record)}",
                "time": _fmt_time(str(_record_value(record, keys["timeKey"]) or "")),
                "title": f"{focus_label}记录 · {_display_of(record, keys)}",
                "detail": " · ".join(detail_parts) or _value_text(_record_value(record, keys["idKey"])),
                "source": focus_name,
                "nodeId": center_id,
                "tone": "danger" if risk == "HIGH" else ("warning" if risk == "MEDIUM" else "neutral"),
                "anchor": index == len(ordered) - 1,
            })
    timeline = {
        "anchor": _value_text(time_value) if _has_value(time_value) else "",
        "events": events,
    }

    # -- statistics ---------------------------------------------------------
    metrics: list[dict[str, Any]] = [
        {"label": "平台记录", "value": str(len(group)), "unit": "条",
         "hint": focus_label, "tone": "neutral"},
    ]
    numeric_values = [
        _as_float(_record_value(record, numeric_key), default=float("nan"))
        for record in group
    ] if numeric_key else []
    numeric_values = [value for value in numeric_values if value == value]
    if numeric_key and numeric_values:
        metrics.append({
            "label": f"{_prop_label(numeric_key)}合计",
            "value": f"{sum(numeric_values):.2f}",
            "unit": "", "hint": f"{len(numeric_values)} 条记录", "tone": "neutral",
        })
        metrics.append({
            "label": f"{_prop_label(numeric_key)}均值",
            "value": f"{sum(numeric_values) / len(numeric_values):.2f}",
            "unit": "", "hint": "按记录平均", "tone": "neutral",
        })
    if status_label:
        metrics.append({
            "label": "当前状态", "value": status_label, "unit": "",
            "hint": status_object_label or focus_label,
            "tone": "danger" if risk == "HIGH" else ("warning" if risk == "MEDIUM" else "neutral"),
        })
    metrics.append({
        "label": "关联对象", "value": str(len(related)), "unit": "个",
        "hint": "本体关系 / 数据关联", "tone": "neutral",
    })

    series: list[dict[str, Any]] = []
    series_label = "记录趋势"
    if keys["timeKey"]:
        by_date: dict[str, float] = {}
        for record in group:
            date_key = _fmt_date(str(_record_value(record, keys["timeKey"]) or ""))
            amount = _as_float(_record_value(record, numeric_key)) if numeric_key else 1.0
            by_date[date_key] = by_date.get(date_key, 0.0) + amount
        series = [
            {"label": date_key, "value": round(value, 2)}
            for date_key, value in sorted(by_date.items())
        ][-6:]
        if numeric_key:
            series_label = f"{_prop_label(numeric_key)}趋势"
    if not series:
        series = [{"label": "当前", "value": len(group)}]
    statistics = {
        "metrics": metrics,
        "series": series,
        "seriesLabel": series_label,
        "note": (
            f"数据来自平台工作空间 {context['ontology'].get('name') or workspace_id} 的实时投影；"
            f"焦点 {focus_label} {label}，同组 {len(group)} 条记录，"
            f"关联 {len(related)} 个对象。"
        ),
    }
    table_options = _table_request(blueprint)
    if table_options is not None:
        statistics["table"] = _table_payload(context, table_options)

    # -- state --------------------------------------------------------------
    bad_status = _status_is_bad(status_value)
    metric_high = False
    if numeric_key and numeric_values:
        all_numeric = [
            _as_float(_record_value(record, numeric_key), default=float("nan"))
            for record in context["records"]
        ]
        all_numeric = [value for value in all_numeric if value == value]
        baseline = sum(all_numeric) / len(all_numeric) if all_numeric else 0.0
        declared = False
        if numeric_key:
            raw_rules = (context.get("ontology") or {}).get("rules") or []
            declared = numeric_key in json.dumps(raw_rules, ensure_ascii=False)
        # Without a declared rule for this metric, a numeric deviation is not
        # evidence of an anomaly; arbitrary ontologies must not be painted red.
        metric_high = bool(declared and baseline and numeric_values[0] > baseline * 1.2)
    state_name = "需要关注" if (bad_status or metric_high) else "运行正常"
    state_tone = "danger" if risk == "HIGH" else ("warning" if (risk == "MEDIUM" or metric_high) else "ok")
    status_owner = status_item["type"] if status_item else focus_name
    status_condition = (
        f"{_type_label(status_owner)}.{_prop_label(status_key)} != 正常"
        if status_key else "无状态字段"
    )
    state = {
        "state": state_name,
        "tone": state_tone,
        "description": (
            f"{focus_label} {label} 当前"
            + (f"状态为{status_label}；" if status_label else "已同步平台实时数据；")
            + (f"{numeric_label} {numeric_value:.2f}，" if numeric_value is not None else "")
            + f"同组 {len(group)} 条记录，关联 {len(related)} 个对象。"
        ),
        "confidence": 0.9,
        "rules": [
            {"priority": 1, "condition": status_condition,
             "value": status_label or "未声明", "hit": bad_status},
            {"priority": 2, "condition": f"{numeric_label} > 基线 20%",
             "value": f"{numeric_value:.2f}" if numeric_value is not None else "无指标",
             "hit": metric_high},
            {"priority": 3, "condition": "同组记录 >= 2",
             "value": f"{len(group)} 条", "hit": len(group) >= 2},
        ],
        "recommendations": [
            str(cap.get("key")) for cap in _executable_capabilities(
                platform, workspace_id, actor) if cap.get("key")
        ],
    }

    # -- rule ---------------------------------------------------------------
    rule = {
        "title": f"{focus_label}状态解释",
        "ruleName": f"{focus_name}.state_anomaly",
        "summary": (
            f"平台记录显示 {focus_label} {label}"
            + (f" 当前{status_label}" if status_label else "")
            + f"，同组 {len(group)} 条记录、关联 {len(related)} 个对象。"
            "该解释完全由平台当前数据派生，而不是引擎示例数据。"
        ),
        "rootCause": (
            f"{_prop_label(str(keys['statusKey'] or 'status'))} 为 {status_label or '未知'}"
            + (f"，{numeric_label} 为 {numeric_value:.2f}" if numeric_value is not None else "")
        ),
        "causalChain": [
            f"{focus_label}实例由平台投影写入运行时状态",
            "智能视图读取同一工作空间的本体、实例与关联对象",
            "L3 规划器按本体能力选择画像、关系、时间线、统计、状态与解释",
            "L4 编译器生成确定性 OpenUI 程序并绑定平台数据",
        ],
        "template": (
            f"{focus_label} {label} 的"
            + (f"{numeric_label}为 {numeric_value:.2f}，" if numeric_value is not None else "")
            + f"状态为 {status_label or '未知'}"
        ),
        "fields": facts[:5],
        "confidence": 0.9,
        "evidence": [f"platform:{workspace_id}", f"object:{focus_name}"],
    }

    # -- actions ------------------------------------------------------------
    action_meta = {}
    for cap in _executable_capabilities(platform, workspace_id, actor):
        key = cap.get("key")
        if not key:
            continue
        action_meta[str(key)] = {
            "displayName": str(cap.get("name") or key),
            "description": str(cap.get("description") or ""),
            "roles": list(_action_roles(str(key))),
            "parameters": _action_parameters(cap),
        }

    pass_payload = {
        "id": _obj_id(focus_record) or "—",
        "plate": label,
        "time": _value_text(time_value) if _has_value(time_value) else "—",
        "fee": round(numeric_value, 2) if numeric_value is not None else 0.0,
        "feeRule": numeric_label,
        "station": status_object_label or "—",
    }

    workspace = platform.workspaces.get(workspace_id, actor)
    workspace_name = str(workspace.get("name") or workspace_id)
    location = _location_payload(
        platform, workspace_id, context, related, workspace_name)

    view_payloads = {
        "view_identity": {"rows": [identity]},
        "view_relationship": {"rows": [relationship]},
        "view_timeline": {"rows": [timeline]},
        "view_statistics": {"rows": [statistics]},
        "view_state": {"rows": [state]},
        "view_rule": {"rows": [rule]},
        "view_action": {"rows": [{
            "note": "动作由平台能力页发布，执行时经平台授权与审计。",
            "allowed": [],
            "denied": [],
        }]},
    }
    if location["points"]:
        # The location capability is derived by the engine whenever geo
        # properties exist; its query must always be answerable.
        view_payloads["view_location"] = {"rows": [location]}

    return {
        "workspace": {
            "id": workspace_id,
            "name": workspace_name,
            "domain": "业务世界",
            "operator": str(workspace.get("description") or "ontoPlantform"),
        },
        "pass": pass_payload,
        "identity": identity,
        "relationship": relationship,
        "timeline": timeline,
        "location": location,
        "statistics": statistics,
        "state": state,
        "rule": rule,
        "actionMeta": action_meta,
        "viewPayloads": view_payloads,
    }


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def _actor_role(platform: "Platform", workspace_id: str, actor: str) -> str:
    """Return the actor's real workspace role, defaulting to read-only."""
    try:
        members = platform.workspaces.list_members(workspace_id, actor)
    except AccessDeniedError:
        return "viewer"
    for member in members:
        if str(member.get("user") or "") == actor:
            role = str(member.get("role") or "")
            if role in _ROLE_RANK:
                return role
    return "viewer"


def _require_roles_at_most(actor_role: str, roles: list[str]) -> None:
    actor_rank = _ROLE_RANK.get(actor_role, -1)
    for role in roles:
        if _ROLE_RANK.get(role, -1) > actor_rank:
            raise AccessDeniedError(
                f"actor role {actor_role!r} cannot request {role!r} perspective; "
                f"allowed roles are those at or below {actor_role!r}"
            )


def smart_ui_focus_options(platform: "Platform", workspace_id: str, actor: str,
                           object_type: str = "", query: str = "",
                           limit: int = 20) -> dict[str, Any]:
    """Searchable instance choices for an explicit focus selector.

    The Studio used to send only a free-text question; users could not say
    "this vehicle" without hoping the model guessed the plate.  This endpoint
    lists real instances (value + display label) so the console can send a
    structured focus that the compiler now applies to the bundle.
    """
    platform.ctx.require(actor, workspace_id, "viewer")
    ontology_data = _ontology_of(platform, workspace_id, actor)
    specs = _object_specs(ontology_data)
    wanted = (object_type or "").strip()
    needle = (query or "").strip().lower()
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for spec in specs:
        name = str(spec.get("name") or "")
        if not name or (wanted and name != wanted):
            continue
        records = _find(platform, workspace_id, name)
        if not records:
            continue
        keys = _semantic_keys(spec, records)
        key = str(keys.get("idKey") or keys.get("groupKey") or "id")
        for record in records:
            value = _record_value(record, key)
            if value in (None, ""):
                value = _obj_id(record)
            text = str(value or "").strip()
            if not text:
                continue
            label = _display_of(record, keys) or text
            if needle and needle not in text.lower() and needle not in label.lower():
                continue
            dedupe = (name, key, text)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            items.append({
                "objectType": name,
                "key": key,
                "value": text,
                "label": label,
            })
    items.sort(key=lambda item: (item["objectType"], item["value"]))
    return {
        "items": items[:max(1, min(100, limit))],
        "total": len(items),
        "objectTypes": sorted({item["objectType"] for item in items}),
    }


def smart_ui_meta(platform: "Platform", workspace_id: str, actor: str) -> dict[str, Any]:
    ontology = _ontology_of(platform, workspace_id, actor)
    specs = _object_specs(ontology)
    context = _select_focus(platform, workspace_id, ontology)
    workspace = platform.workspaces.get(workspace_id, actor)
    caps = _capabilities_of(platform, workspace_id, actor)
    executable = _executable_capabilities(platform, workspace_id, actor)
    actor_role = _actor_role(platform, workspace_id, actor)
    allowed_roles = [
        role for role in ROLE_OPTIONS
        if _ROLE_RANK.get(str(role["id"]), -1) <= _ROLE_RANK.get(actor_role, -1)
    ]
    common = {
        "workspace": {
            "id": workspace_id,
            "name": str(workspace.get("name") or workspace_id),
            "description": str(workspace.get("description") or ""),
        },
        "actorRole": actor_role,
        "roles": allowed_roles,
        "objectTypes": [str(spec.get("name")) for spec in specs if spec.get("name")],
        "counts": context.get("counts") or {},
        "capabilities": [
            {"key": cap.get("key"), "name": cap.get("name"), "target": cap.get("target_type")}
            for cap in caps if cap.get("key")
        ],
        "actions": [
            {
                "key": cap.get("key"),
                "name": cap.get("name"),
                "target": cap.get("target_type"),
                "parameters": _action_parameters(cap),
            }
            for cap in executable if cap.get("key")
        ],
    }
    if not context.get("ready"):
        return {
            **common,
            "ready": False,
            "reason": (
                "当前工作空间还没有可用的对象实例，智能视图不会为空工作空间伪造数据。"
                "请先在数据页连接数据源并把数据投影到本工作空间，再打开智能视图。"
            ),
            "guidance": [
                "确认本体已经发布，并且至少有一个对象类型声明了实例",
                "在数据页连接数据源、创建投影并运行投影",
                "回到智能视图刷新；焦点对象会自动选择有实例的业务对象，不要求车辆或车牌",
            ],
            "focus": None,
            "defaultQuestion": "",
            "bridge": {
                "focusObject": None,
                "focusLabel": None,
                "focusValue": None,
                "identityKey": None,
                "identityLabel": None,
                "relatedObjects": [],
                "vehicleObject": None,
                "stationObject": None,
                "laneObject": None,
                "engineVersion": ENGINE_VERSION,
            },
        }
    keys = context["keys"]
    focus_name = str(context["focusName"])
    focus_value = str(context.get("focusValue") or "")
    related = _related_objects(platform, workspace_id, context)
    _status_item, status_key, status_value = _related_status(context, related)
    if focus_value and _status_is_bad(status_value):
        default_question = f"{focus_value} 为什么{_status_text(status_value)}？"
    elif focus_value:
        default_question = f"查看 {focus_value} 的完整业务视图"
    else:
        default_question = f"{context['focusLabel']}当前情况如何？"
    return {
        **common,
        "ready": True,
        "focus": {
            "objectType": focus_name,
            "label": context["focusLabel"],
            "identityKey": keys.get("idKey"),
            "value": focus_value,
            "status": _status_text(status_value) if status_key else None,
            "statusRisk": _status_risk(status_value) if status_key else None,
        },
        "defaultQuestion": default_question,
        "bridge": {
            "focusObject": focus_name,
            "focusLabel": context["focusLabel"],
            "focusValue": focus_value,
            "identityKey": keys.get("idKey"),
            "identityLabel": _prop_label(str(keys.get("idKey") or "")),
            "relatedObjects": [
                {"type": item["type"], "relation": item["relation"]}
                for item in related
            ],
            "vehicleObject": context.get("vehicleObject"),
            "stationObject": context.get("stationObject"),
            "laneObject": context.get("laneObject"),
            "engineVersion": ENGINE_VERSION,
        },
    }


def compile_smart_ui(platform: "Platform", workspace_id: str, actor: str,
                     question: str, roles: list[str],
                     focus: dict[str, Any] | None = None) -> dict[str, Any]:
    modules = _engine()
    planner = modules["planner"]
    compiler = modules["compiler"]

    question = (question or "").strip()
    if not question:
        raise ValidationError("question must not be empty")
    roles = [role for role in (roles or []) if role in ROLE_IDS]
    if not roles:
        raise ValidationError(f"roles must contain at least one of {list(ROLE_IDS)}")
    actor_role = _actor_role(platform, workspace_id, actor)
    _require_roles_at_most(actor_role, roles)

    # Snapshot the ontology/focus once so the compiled program and the payload
    # bundle cannot disagree if a concurrent writer changes the workspace
    # between the two builders.
    ontology_data = _ontology_of(platform, workspace_id, actor)
    context = _select_focus(platform, workspace_id, ontology_data, focus=focus)
    ontology, bridge = build_engine_ontology(
        platform, workspace_id, actor,
        ontology_data=ontology_data, context=context,
    )
    plan = planner.plan_application(
        ontology,
        question,
        tuple(roles),
        focus_object=str(bridge.get("focusObject") or "") or None,
        focus_key=str(bridge.get("identityKey") or "") or None,
        focus_value=str(bridge.get("focusValue") or "") or None,
    )
    if plan.blueprint is None:
        details = [d.to_dict() for d in plan.diagnostics if d.severity.value == "error"]
        raise ValidationError(
            "L3 planning failed: "
            + "; ".join(d.get("message", "") for d in details[:3])
        )
    compiled = compiler.compile_blueprint(
        plan.blueprint,
        ontology,
        tuple(roles),
        base_diagnostics=plan.diagnostics,
        permission_context=plan.permissions,
    )
    if compiled.errors:
        raise ValidationError(
            "L4 compile failed: "
            + "; ".join(d.message for d in compiled.errors[:3])
        )

    capabilities = [
        {
            **cap.to_dict(),
            "derivedFrom": {
                "object": plan.intent.object,
                "ontologyVersion": ontology.ontology_version,
            },
        }
        for cap in plan.capabilities
    ]
    artifacts = {
        "engineVersion": str(modules.get("version") or ENGINE_VERSION),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "intent": plan.intent.to_dict(),
        "ontology": ontology.to_summary(),
        "capabilities": capabilities,
        "requestedPerspectives": list(plan.requested),
        "finalPerspectives": list(plan.final),
        "prunedPerspectives": list(plan.pruned),
        "planningDiagnostics": [d.to_dict() for d in plan.diagnostics],
        "programBytes": len(compiled.program.encode("utf-8")),
        "programLines": len(compiled.program.splitlines()),
        "toolClosure": {
            "programTools": sorted(
                modules["models"].extract_program_tool_names(compiled.program)
            ),
            "compiledTools": [tool.name for tool in compiled.tools],
            "closed": modules["models"].extract_program_tool_names(compiled.program)
            <= {tool.name for tool in compiled.tools},
        },
    }
    payload = compiled.to_dict()
    payload.update({
        "artifacts": artifacts,
        "question": question,
        "ok": bool(compiled.program) and not compiled.errors,
    })

    bundle = build_bundle(
        platform, workspace_id, actor, ontology_data, bridge, question,
        context=context,
    )
    # Add a short adapter trail so the console can prove where each payload
    # came from without exposing platform internals.
    payload["bridge"] = {
        **bridge,
        "workspaceId": workspace_id,
        "actorRole": actor_role,
        "engineVersion": str(modules.get("version") or ENGINE_VERSION),
        "capabilities": [cap.get("key") for cap in _capabilities_of(platform, workspace_id, actor)
                         if cap.get("key")],
        "dataSource": "ontoPlantform runtime gateway",
    }
    return {"compiled": payload, "bundle": bundle}


def smart_ui_snapshot(platform: "Platform", workspace_id: str,
                      actor: str, focus: dict[str, Any] | None = None) -> dict[str, Any]:
    """One consistent view of ontology + focus for a generation.

    Studio builds the LLM context pack and the compiler input from the same
    snapshot so the proposal cannot be judged against a different ontology
    revision than the one used to compile it.  ``focus`` is an optional
    user-selected ``{object,key,value}`` override; it is validated by
    ``_select_focus`` against real instances and silently falls back to the
    deterministic choice when it cannot be resolved.
    """
    ontology_data = _ontology_of(platform, workspace_id, actor)
    context = _select_focus(platform, workspace_id, ontology_data, focus=focus)
    ontology, bridge = build_engine_ontology(
        platform, workspace_id, actor,
        ontology_data=ontology_data, context=context,
    )
    return {
        "ontologyData": ontology_data,
        "context": context,
        "ontology": ontology,
        "bridge": bridge,
    }


def compile_smart_ui_blueprint(
    platform: "Platform",
    workspace_id: str,
    actor: str,
    question: str,
    roles: list[str],
    blueprint: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
    base_diagnostics: tuple[Any, ...] = (),
    pruned: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile a platform-adjudicated blueprint instead of re-planning it.

    The LLM never reaches the compiler: Studio normalizes its proposal into a
    schema-valid blueprint and this function runs the *existing* deterministic
    validator/compiler on it.  A rejected blueprint raises ``ValidationError``
    with the engine diagnostics, so nothing half-built can be rendered.
    """
    modules = _engine()
    compiler = modules["compiler"]

    question = (question or "").strip()
    if not question:
        raise ValidationError("question must not be empty")
    roles = [role for role in (roles or []) if role in ROLE_IDS]
    if not roles:
        raise ValidationError(f"roles must contain at least one of {list(ROLE_IDS)}")
    actor_role = _actor_role(platform, workspace_id, actor)
    _require_roles_at_most(actor_role, roles)

    if not isinstance(blueprint, dict):
        raise ValidationError("blueprint must be an object")
    context_spec = blueprint.get("context") or {}
    focus_spec = context_spec.get("focus") or {}
    snapshot = snapshot or smart_ui_snapshot(platform, workspace_id, actor)
    if isinstance(focus_spec, dict) and focus_spec.get("key") \
            and focus_spec.get("value"):
        # The blueprint's focus is the contract the user asked for; apply it to
        # the bundle instead of compiling against the platform default focus.
        snapshot = smart_ui_snapshot(
            platform, workspace_id, actor,
            focus={"object": str(context_spec.get("object") or ""),
                   "key": str(focus_spec.get("key")),
                   "value": str(focus_spec.get("value"))},
        )
    ontology = snapshot["ontology"]
    bridge = snapshot["bridge"]
    context = snapshot["context"]
    ontology_data = snapshot["ontologyData"]

    compiled = compiler.compile_blueprint(
        blueprint,
        ontology,
        tuple(roles),
        base_diagnostics=tuple(base_diagnostics),
    )
    if compiled.errors:
        raise ValidationError(
            "L4 compile failed: "
            + "; ".join(d.message for d in compiled.errors[:3])
        )

    context_spec = blueprint.get("context") or {}
    focus = context_spec.get("focus") or {}
    intent = modules["models"].Intent(
        goal=str(context_spec.get("goal") or "overview"),
        object=str(context_spec.get("object") or ""),
        focus_key=str(focus.get("key")) if focus.get("key") else None,
        focus_value=str(focus.get("value")) if focus.get("value") else None,
        source_question=question,
        confidence=float((blueprint.get("meta") or {}).get("confidence") or 0.85),
    )
    capabilities = [
        cap.to_dict()
        for cap in modules["derive_capabilities"](
            ontology, str(context_spec.get("object") or ""))
    ]
    view_list = [
        str(view.get("perspective"))
        for view in (blueprint.get("views") or [])
        if isinstance(view, dict) and view.get("perspective")
    ]
    artifacts = {
        "engineVersion": str(modules.get("version") or ENGINE_VERSION),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "intent": intent.to_dict(),
        "ontology": ontology.to_summary(),
        "capabilities": capabilities,
        "requestedPerspectives": view_list,
        "finalPerspectives": view_list,
        "prunedPerspectives": list(pruned or []),
        "planningDiagnostics": [
            d.to_dict() for d in compiled.diagnostics
        ],
        "programBytes": len(compiled.program.encode("utf-8")),
        "programLines": len(compiled.program.splitlines()),
        "toolClosure": {
            "programTools": sorted(
                modules["models"].extract_program_tool_names(compiled.program)
            ),
            "compiledTools": [tool.name for tool in compiled.tools],
            "closed": modules["models"].extract_program_tool_names(compiled.program)
            <= {tool.name for tool in compiled.tools},
        },
    }
    payload = compiled.to_dict()
    payload.update({
        "artifacts": artifacts,
        "question": question,
        "ok": bool(compiled.program) and not compiled.errors,
    })
    bundle = build_bundle(
        platform, workspace_id, actor, ontology_data, bridge, question,
        context=context,
        blueprint=blueprint,
    )
    payload["bridge"] = {
        **bridge,
        "workspaceId": workspace_id,
        "actorRole": actor_role,
        "engineVersion": str(modules.get("version") or ENGINE_VERSION),
        "capabilities": [
            cap.get("key")
            for cap in _capabilities_of(platform, workspace_id, actor)
            if cap.get("key")
        ],
        "dataSource": "ontoPlantform runtime gateway",
    }
    return {"compiled": payload, "bundle": bundle}


def _capability_key_by_id(caps: list[dict], action_id: str) -> dict | None:
    for cap in caps:
        if str(cap.get("key") or "") == action_id:
            return cap
    return None


def _as_ref(object_type: str, value: Any) -> str | None:
    """Platform capabilities require a ``Type/id`` ref, not a bare id."""
    if value in (None, ""):
        return None
    text = str(value)
    if "/" in text:
        return text
    return f"{object_type}/{text}"


def _resolve_target(platform: "Platform", workspace_id: str, context: dict[str, Any],
                    target_type: str) -> str | None:
    """Resolve the platform object ref an action should run against.

    The smart-UI surface carries only the focus value.  For a capability on the
    focus object we use the selected focus instance; for a related object we
    use a real relation/data association.  If no relation exists we refuse to
    guess: silently targeting the first instance of a type caused writes to the
    wrong object.  Callers can never inject an arbitrary ref.
    """
    focus_name = str(context.get("focusName") or "")
    focus_record = context.get("focusRecord")
    if target_type == focus_name and focus_record is not None:
        return _as_ref(focus_name, _obj_id(focus_record))
    related = _related_objects(platform, workspace_id, context)
    for item in related:
        if str(item.get("type")) == target_type:
            return _as_ref(target_type, _obj_id(item["record"]))
    if focus_record is not None:
        for relation in context.get("relations") or []:
            name = str(relation.get("name") or "")
            source = str(relation.get("source") or "")
            target = str(relation.get("target") or "")
            if source == focus_name and target == target_type and name:
                refs = _related(
                    platform, workspace_id,
                    f"{focus_name}/{_obj_id(focus_record)}", name, "out",
                )
                if refs:
                    return _as_ref(target_type, refs[0].split("/", 1)[-1])
            if target == focus_name and source == target_type and name:
                refs = _related(
                    platform, workspace_id,
                    f"{focus_name}/{_obj_id(focus_record)}", name, "in",
                )
                if refs:
                    return _as_ref(target_type, refs[0].split("/", 1)[-1])
    return None


def _coerce_action_arg(name: str, type_name: str, value: Any) -> Any:
    kind = str(type_name or "string").strip().lower()
    if kind in ("number", "float", "double"):
        if isinstance(value, bool):
            raise ValidationError(f"action input {name!r} must be a number")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"action input {name!r} must be a number") from exc
    if kind in ("integer", "int", "long"):
        if isinstance(value, bool):
            raise ValidationError(f"action input {name!r} must be an integer")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"action input {name!r} must be an integer") from exc
        if number != number or number != int(number):
            raise ValidationError(f"action input {name!r} must be an integer")
        return int(number)
    if kind in ("boolean", "bool"):
        text = str(value).strip().lower()
        if text in ("true", "1", "yes", "是"):
            return True
        if text in ("false", "0", "no", "否"):
            return False
        raise ValidationError(f"action input {name!r} must be a boolean")
    if isinstance(value, (dict, list)):
        raise ValidationError(f"action input {name!r} must be a scalar value")
    return str(value)


def _action_args(cap: dict, supplied: Any) -> dict[str, Any]:
    parameters = _action_parameters(cap)
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, dict):
        raise ValidationError("action args must be an object")
    names = {item["name"] for item in parameters}
    unknown = sorted(set(supplied) - names)
    if unknown:
        raise ValidationError(f"action does not accept inputs {unknown}")
    if not parameters:
        if supplied:
            raise ValidationError("this action does not accept args")
        return {}
    missing = [
        item["name"] for item in parameters
        if item["name"] not in supplied
        or supplied[item["name"]] in (None, "")
    ]
    if missing:
        raise ValidationError(f"action requires inputs {missing}")
    return {
        item["name"]: _coerce_action_arg(
            item["name"], item["type"], supplied[item["name"]])
        for item in parameters
    }


def invoke_smart_action(platform: "Platform", workspace_id: str, actor: str,
                        action_id: str, body: dict[str, Any]) -> dict[str, Any]:
    actor_role = _actor_role(platform, workspace_id, actor)
    role = str(body.get("role") or actor_role)
    if role not in ROLE_IDS:
        raise ValidationError(f"unknown role {role!r}")
    _require_roles_at_most(actor_role, [role])
    caps = _capabilities_of(platform, workspace_id, actor)
    cap = _capability_key_by_id(caps, action_id)
    if cap is None:
        raise ValidationError(f"unknown platform capability {action_id!r}")
    if role not in _action_roles(action_id):
        raise AccessDeniedError(
            f"role {role!r} is not allowed to execute {action_id!r}; "
            f"required: {list(_action_roles(action_id))}"
        )
    if str(cap.get("lifecycle") or "published") != "published":
        raise ValidationError(f"capability {action_id!r} is not published")
    # Parameterized capabilities declare their inputs in the platform model;
    # collect and type-check them here instead of silently dropping values.
    action_args = _action_args(cap, body.get("args"))
    ontology = _ontology_of(platform, workspace_id, actor)
    context = _select_focus(platform, workspace_id, ontology)
    if not context.get("ready"):
        raise ValidationError(
            "当前工作空间没有可用的对象实例，无法解析动作目标；"
            "先在数据页运行投影"
        )
    focus_name = str(context["focusName"])
    keys = context["keys"]
    requested_value = str(
        body.get("focusValue") or body.get("plate") or context.get("focusValue") or ""
    )
    focus_record = context["focusRecord"]
    if requested_value:
        matched = [
            record for record in context["records"]
            if _record_group_value(record, keys) == requested_value
            or str(_record_value(record, keys["idKey"])) == requested_value
        ]
        if not matched:
            raise ValidationError(
                f"focusValue {requested_value!r} is not an instance of "
                f"{focus_name!r}; refusing to guess an action target"
            )
        matched.sort(
            key=lambda item: str(_record_value(item, keys["timeKey"]) or ""),
            reverse=True,
        )
        focus_record = matched[0]
    # Target resolution must see the *selected* record, not the default focus
    # chosen earlier by _select_focus.
    action_context = {
        **context,
        "focusRecord": focus_record,
        "focusValue": _record_group_value(focus_record, keys),
    }
    target_type = str(cap.get("target_type") or focus_name)
    if target_type not in {str(spec.get("name")) for spec in context["specs"]}:
        raise ValidationError(
            f"capability {action_id!r} targets {target_type!r}, which is not "
            "part of the current ontology"
        )
    target = _resolve_target(platform, workspace_id, action_context, target_type)
    if not target:
        raise ValidationError(
            f"could not resolve a {target_type} target for capability {action_id!r}"
        )
    result = platform.capabilities.invoke(
        workspace_id,
        actor,
        f"{target_type}.{action_id}",
        target=target,
        args=action_args,
        idempotency_key=body.get("idempotency_key"),
    )
    focus_value = _display_of(focus_record, keys)
    return {
        "ok": bool(result.get("ok", result.get("status") == "succeeded")),
        "actionId": action_id,
        "target": target,
        "focusObject": focus_name,
        "focusValue": focus_value,
        "args": action_args,
        # Compatibility field for the existing runtime toast/detail renderer.
        "plate": focus_value,
        "role": role,
        "message": (
            f"{cap.get('name') or action_id} 已通过平台能力执行"
            if result.get("ok", result.get("status") == "succeeded")
            else f"{cap.get('name') or action_id} 执行失败"
        ),
        "result": result,
        "audit": {
            "actionId": action_id,
            "role": role,
            "target": target,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }
