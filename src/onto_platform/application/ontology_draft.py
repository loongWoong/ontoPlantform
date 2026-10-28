"""AI-assisted ontology drafting: one paragraph -> objects / relations / terms.

建模是平台的主入口，但逐个对象手填成本很高。这个用例把「一段业务说明」
交给 LLM 解析成平台本体模型（对象类型 + 属性、关系、业务术语），再走与手工
建模**完全相同**的领域校验与持久化路径：

    business text --LLM--> draft JSON --> normalize --> Ontology domain
                                                    --> (apply?) repo

LLM 只负责提议，平台负责裁决：非法标识符、未知数据类型、悬空关系、无法
解析的术语都会被规范化或丢弃，并以 ``warnings`` 返回，绝不静默写坏本体。
"""

from __future__ import annotations

import json
import re

from ..domain.ontology.model import (
    TYPE_KINDS,
    ObjectTypeSpec,
    Ontology,
    PropertySpec,
    RelationSpec,
    VocabularyTerm,
)
from ..errors import ConnectorError, ValidationError
from ..ports.llm import LLMMessage, LLMProvider
from .common import ApplicationContext

MAX_TEXT_CHARS = 20000

_SYSTEM_PROMPT = """你是 OntoPlatform 的本体建模助手。请把用户给出的一段业务说明，解析成平台本体模型。
只输出一个 JSON 对象，不要输出解释、不要输出 Markdown 代码块。

JSON 结构：
{
  "objects": [
    {"name": "TollStation", "description": "收费站",
     "properties": [
       {"name": "name", "type": "string", "description": "收费站名称"},
       {"name": "status", "type": "string", "description": "通行状态：free/congested"}
     ]}
  ],
  "relations": [
    {"name": "passes", "source": "Vehicle", "target": "TollStation",
     "description": "车辆通过收费站", "cardinality": "many_to_many"}
  ],
  "vocabulary": [
    {"term": "收费站", "concept": "TollStation", "kind": "object", "aliases": ["Toll Station"]},
    {"term": "通行费", "concept": "Pass.fee", "kind": "property", "aliases": ["过路费"]}
  ]
}

规则：
1. name 必须是 ASCII 标识符：对象类型用大驼峰（TollStation、Vehicle），属性与关系用小写下划线（station_id、entry_station、has_lane）。
2. description 用中文，一句话说明业务含义。
3. type 只能是 string / number / integer / boolean / object / array / datetime。
4. relations 的 source / target 必须是本次 objects 中出现的对象类型名（或用户已有本体中的对象类型名）。
5. vocabulary 的 kind 只能是 object / property / relation：
   object 的 concept 是对象类型名；property 的 concept 是 "对象类型.属性名"；relation 的 concept 是关系名。
6. 对象数量 3~10 个，每个对象 2~8 个业务上真正需要的属性；只覆盖业务说明里出现的概念，不要编造。
7. 业务术语（vocabulary）挑选用户口语里会说的词，例如「收费站」「拥堵」「通行费」「车牌」。
8. 宁缺毋滥：拿不准的概念不要给，绝不要输出空对象或占位字段。"""

# LLM 常见的类型别名 -> 平台标量类型（与 Kernel TypeDef 1:1）
_TYPE_ALIASES: dict[str, str] = {
    "str": "string", "text": "string", "varchar": "string", "char": "string",
    "int": "integer", "long": "integer", "int32": "integer", "int64": "integer",
    "float": "number", "double": "number", "decimal": "number", "number": "number",
    "bool": "boolean", "boolean": "boolean",
    "datetime": "datetime", "date": "datetime", "time": "datetime",
    "timestamp": "datetime",
    "object": "object", "dict": "object", "map": "object", "json": "object",
    "array": "array", "list": "array", "set": "array",
}


class DraftOntologyFromText:
    """Parse a business description into ontology elements (optionally apply).

    ``apply=False`` returns the normalized draft only (preview, nothing is
    persisted); ``apply=True`` writes it into the workspace's draft ontology,
    creating that ontology when the workspace has none yet.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, text: str,
                 apply: bool = False, provider: LLMProvider | None = None,
                 name: str = "", draft: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        existing = self._ctx.repo.list("ontology", workspace_id)
        existing_data = existing[0] if existing else None

        if draft is not None:
            # "apply the exact preview the user just approved": no second LLM
            # round, so what the UI showed is what gets written.
            if not isinstance(draft, dict):
                raise ValidationError("draft must be an object")
            payload = draft
        else:
            body = (text or "").strip()
            if not body:
                raise ValidationError("business description text is required")
            if len(body) > MAX_TEXT_CHARS:
                raise ValidationError(
                    f"business description is too long: {len(body)} > {MAX_TEXT_CHARS} chars"
                )
            effective = provider or self._ctx.llm
            if effective is None:
                raise ValidationError(
                    "no LLM provider configured; configure the platform "
                    "(ONTO_LLM_*) or pass one with the request"
                )
            payload = _extract_json(self._complete(effective, body, existing_data))

        warnings: list[str] = []
        normalized = _normalize(payload, existing_data, warnings)

        if not apply:
            return {"applied": False, "draft": normalized, "warnings": warnings}

        ontology = self._apply(workspace_id, actor, name, normalized, warnings)
        return {
            "applied": True,
            "draft": normalized,
            "warnings": warnings,
            "ontology": ontology.to_dict(),
        }

    # ------------------------------------------------------------------

    def _complete(self, provider: LLMProvider, text: str,
                  existing_data: dict | None) -> str:
        messages = [LLMMessage(role="system", content=_SYSTEM_PROMPT)]
        if existing_data:
            existing_objects = sorted(existing_data.get("objects", {}))
            existing_relations = sorted(existing_data.get("relations", {}))
            if existing_objects or existing_relations:
                messages.append(LLMMessage(
                    role="system",
                    content="用户已有本体（请补充，不要重复定义同名对象）："
                            + json.dumps(
                                {"objects": existing_objects,
                                 "relations": existing_relations},
                                ensure_ascii=False),
                ))
        messages.append(LLMMessage(role="user", content="业务说明：\n" + text))
        try:
            response = provider.complete(messages)
        except Exception as exc:  # transport / auth / model errors
            raise ConnectorError(f"LLM request failed: {exc}") from exc
        return getattr(response, "content", "") or ""

    def _apply(self, workspace_id: str, actor: str, name: str,
               draft: dict, warnings: list[str]) -> Ontology:
        existing = self._ctx.repo.list("ontology", workspace_id)
        if existing:
            ontology = Ontology.from_dict(existing[0])
            if ontology.lifecycle == "published":
                raise ValidationError(
                    "the workspace ontology is already published and immutable; "
                    "create a new draft version before applying an AI draft"
                )
        else:
            ontology = Ontology(
                id=self._ctx.id_factory(),
                workspace_id=workspace_id,
                name=(name or "ai-draft").strip() or "ai-draft",
                description="由业务描述经 AI 解析生成",
                created_at=self._ctx.clock(),
                updated_at=self._ctx.clock(),
            )
            self._ctx.save("ontology", ontology.to_dict())
            self._ctx.audit_event(actor, workspace_id, "ontology.create_from_draft",
                                  ontology.id, {"name": ontology.name})

        added_objects = added_relations = added_terms = 0
        for item in draft["objects"]:
            try:
                ontology.upsert_object(ObjectTypeSpec(
                    name=item["name"],
                    description=item["description"],
                    properties={p["name"]: PropertySpec(**p) for p in item["properties"]},
                ))
                added_objects += 1
            except ValidationError as exc:
                warnings.append(f"对象 {item['name']} 写入失败：{exc}")
        for item in draft["relations"]:
            try:
                ontology.upsert_relation(RelationSpec(**item))
                added_relations += 1
            except ValidationError as exc:
                warnings.append(f"关系 {item['name']} 写入失败：{exc}")
        for item in draft["vocabulary"]:
            try:
                ontology.add_term(VocabularyTerm(**item))
                added_terms += 1
            except ValidationError as exc:
                warnings.append(f"术语 {item['term']} 写入失败：{exc}")

        ontology.updated_at = self._ctx.clock()
        self._ctx.save("ontology", ontology.to_dict())
        self._ctx.audit_event(actor, workspace_id, "ontology.draft", ontology.id, {
            "objects": added_objects,
            "relations": added_relations,
            "terms": added_terms,
            "warnings": len(warnings),
        })
        return ontology


# ----------------------------------------------------------------------
# normalization (LLM proposal -> platform semantics)
# ----------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    body = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fence:
        body = fence.group(1).strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ConnectorError("LLM reply is not a JSON object enclosing an ontology draft")
    try:
        payload = json.loads(body[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"LLM reply is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConnectorError("LLM reply must be a JSON object")
    return payload


def _identifier(raw) -> str:
    text = re.sub(r"[^0-9A-Za-z_]+", "_", str(raw or "").strip()).strip("_")
    if not text or text[0].isdigit():
        return ""
    return text


def _snake(raw) -> str:
    name = _identifier(raw)
    return name[:1].lower() + name[1:] if name else ""


def _type_name(raw) -> str:
    text = str(raw or "string").strip().lower()
    if text in _TYPE_ALIASES:
        return _TYPE_ALIASES[text]
    return text if text in TYPE_KINDS else "string"


def _text(value, limit: int = 300) -> str:
    return str(value or "").strip().replace("\n", " ")[:limit]


def _list(raw) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [{**v, "name": k} if isinstance(v, dict) else {"name": k}
                for k, v in raw.items()]
    return []


def _normalize(payload: dict, existing_data: dict | None,
               warnings: list[str]) -> dict:
    existing_objects: dict = (existing_data or {}).get("objects", {}) or {}
    existing_relations: dict = (existing_data or {}).get("relations", {}) or {}

    objects: list[dict] = []
    object_props: dict[str, set[str]] = {
        name: set((spec or {}).get("properties", {})) for name, spec in existing_objects.items()
    }
    object_names = set(existing_objects)

    for raw in _list(payload.get("objects") or payload.get("object_types")):
        if not isinstance(raw, dict):
            continue
        obj_name = _identifier(raw.get("name"))
        if not obj_name:
            warnings.append(f"丢弃对象：名称 {raw.get('name')!r} 不是合法标识符")
            continue
        props: dict[str, dict] = {}
        for prop_raw in _list(raw.get("properties")):
            if not isinstance(prop_raw, dict):
                continue
            prop_name = _snake(prop_raw.get("name"))
            if not prop_name:
                warnings.append(f"{obj_name}：丢弃属性 {prop_raw.get('name')!r}（非法标识符）")
                continue
            props[prop_name] = {
                "name": prop_name,
                "type": _type_name(prop_raw.get("type")),
                "description": _text(prop_raw.get("description")),
            }
        objects.append({
            "name": obj_name,
            "description": _text(raw.get("description")),
            "properties": list(props.values()),
        })
        object_names.add(obj_name)
        object_props.setdefault(obj_name, set()).update(props)

    relations: list[dict] = []
    relation_names = set(existing_relations)
    for raw in _list(payload.get("relations")):
        if not isinstance(raw, dict):
            continue
        rel_name = _snake(raw.get("name"))
        source = _identifier(raw.get("source"))
        target = _identifier(raw.get("target"))
        if not rel_name:
            warnings.append(f"丢弃关系：名称 {raw.get('name')!r} 不是合法标识符")
            continue
        if source not in object_names or target not in object_names:
            warnings.append(
                f"丢弃关系 {rel_name}：{source or '?'} -> {target or '?'} "
                "指向未定义的对象类型"
            )
            continue
        relations.append({
            "name": rel_name,
            "source": source,
            "target": target,
            "description": _text(raw.get("description")),
            "cardinality": _text(raw.get("cardinality"), 40),
        })
        relation_names.add(rel_name)

    vocabulary: list[dict] = []
    seen_terms: set[str] = set()
    for raw in _list(payload.get("vocabulary") or payload.get("terms")):
        if not isinstance(raw, dict):
            continue
        term = _text(raw.get("term"), 60)
        if not term or term in seen_terms:
            continue
        kind = str(raw.get("kind") or "object").strip().lower()
        if kind not in ("object", "property", "relation"):
            warnings.append(f"丢弃术语 {term}：kind {kind!r} 不支持")
            continue
        concept = _concept(raw.get("concept"), kind)
        if kind == "object" and concept not in object_names:
            warnings.append(f"丢弃术语 {term}：概念 {concept or '?'} 不是已定义的对象类型")
            continue
        if kind == "relation" and concept not in relation_names:
            warnings.append(f"丢弃术语 {term}：概念 {concept or '?'} 不是已定义的关系")
            continue
        if kind == "property":
            owner, _, prop = concept.partition(".")
            if owner not in object_names or prop not in object_props.get(owner, set()):
                warnings.append(f"丢弃术语 {term}：概念 {concept or '?'} 不是已定义的属性")
                continue
        aliases = [_text(a, 60) for a in _list(raw.get("aliases") or raw.get("alias"))]
        vocabulary.append({
            "term": term,
            "concept": concept,
            "kind": kind,
            "aliases": [a for a in aliases if a],
        })
        seen_terms.add(term)

    return {"objects": objects, "relations": relations, "vocabulary": vocabulary}


def _concept(raw, kind: str) -> str:
    text = str(raw or "").strip()
    if kind == "property" and "." in text:
        owner, _, prop = text.partition(".")
        owner, prop = _identifier(owner), _snake(prop)
        return f"{owner}.{prop}" if owner and prop else ""
    return _snake(text) if kind == "relation" else _identifier(text)
