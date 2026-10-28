"""Ontology compatibility matrix (plan-v2 C10 / 1.11 / 2.5).

Publishing a new ontology version must not silently narrow what existing
ABox data was allowed to be.  Widening is compatible; the changes below are
incompatible and must be announced with migration advice:

* object/relation/property removal
* identity change
* property type change
* ``required`` false -> true
* ``enum`` removed values (or enum introduced where none existed)
* ``minimum`` raised / ``maximum`` lowered
* ``unique`` false -> true
* relation cardinality narrowed, endpoint or inverse changed
"""

from __future__ import annotations

from .model import normalize_cardinality

#: old cardinality -> cardinalities it may widen to
_CARDINALITY_WIDENING: dict[str, set[str]] = {
    "one-to-one": {"one-to-one", "one-to-many", "many-to-one", "many-to-many"},
    "one-to-many": {"one-to-many", "many-to-many"},
    "many-to-one": {"many-to-one", "many-to-many"},
    "many-to-many": {"many-to-many"},
    "": {"", "one-to-one", "one-to-many", "many-to-one", "many-to-many"},
}


def check_compatible(old: dict, new: dict) -> list[str]:
    """Return incompatibility problems (empty list = compatible release)."""
    problems: list[str] = []
    old_objects = old.get("objects") or {}
    new_objects = new.get("objects") or {}
    for name, old_def in old_objects.items():
        new_def = new_objects.get(name)
        if new_def is None:
            problems.append(
                f"object type {name!r} removed; migrate or archive its objects "
                "before publishing")
            continue
        if list(old_def.get("identity") or []) != list(new_def.get("identity") or []):
            problems.append(
                f"object type {name!r} identity changed "
                f"{old_def.get('identity') or []} -> {new_def.get('identity') or []}; "
                "existing objects may be re-identified - add a migration")
        old_props = _props(old_def)
        new_props = _props(new_def)
        for pname, old_prop in old_props.items():
            new_prop = new_props.get(pname)
            if new_prop is None:
                problems.append(
                    f"{name}.{pname} removed; migrate its data before publishing")
                continue
            problems.extend(_check_property(f"{name}.{pname}", old_prop, new_prop))

    old_relations = old.get("relations") or {}
    new_relations = new.get("relations") or {}
    for rname, old_rel in old_relations.items():
        new_rel = new_relations.get(rname)
        if new_rel is None:
            problems.append(f"relation {rname!r} removed; migrate links first")
            continue
        old_card = normalize_cardinality(old_rel.get("cardinality") or "")
        new_card = normalize_cardinality(new_rel.get("cardinality") or "")
        if new_card not in _CARDINALITY_WIDENING.get(old_card, {new_card}):
            problems.append(
                f"relation {rname!r} cardinality narrowed {old_card or '(any)'} -> "
                f"{new_card or '(any)'}; existing links may violate it")
        if (old_rel.get("source"), old_rel.get("target")) != (
                new_rel.get("source"), new_rel.get("target")):
            problems.append(
                f"relation {rname!r} endpoints changed; add a migration for links")
    return problems


def _props(definition: dict) -> dict[str, dict]:
    props = definition.get("properties") or {}
    return props if isinstance(props, dict) else {
        p.get("name"): p for p in props if isinstance(p, dict)}


def _check_property(where: str, old: dict, new: dict) -> list[str]:
    problems: list[str] = []
    if old.get("type") != new.get("type"):
        problems.append(
            f"{where} type changed {old.get('type')!r} -> {new.get('type')!r}; "
            "values need a migration")
    if not old.get("required") and new.get("required"):
        problems.append(f"{where} became required; existing objects may lack it")
    old_enum = list(old.get("enum") or [])
    new_enum = list(new.get("enum") or [])
    if new_enum and not old_enum:
        problems.append(f"{where} enum introduced; existing values may fall outside it")
    elif old_enum and new_enum:
        dropped = [v for v in old_enum if v not in new_enum]
        if dropped:
            problems.append(
                f"{where} enum narrowed, dropped {dropped}; migrate affected objects")
    old_min, new_min = old.get("minimum"), new.get("minimum")
    if new_min is not None and (old_min is None or new_min > old_min):
        problems.append(f"{where} minimum raised to {new_min}; existing values may violate it")
    old_max, new_max = old.get("maximum"), new.get("maximum")
    if new_max is not None and (old_max is None or new_max < old_max):
        problems.append(f"{where} maximum lowered to {new_max}; existing values may violate it")
    if not old.get("unique") and new.get("unique"):
        problems.append(f"{where} became unique; existing duplicates may exist")
    return problems
