"""Ontology semantic validation (design-time, pre-publish).

Returns human-readable problems; an empty list means publishable.  This is
the one place that decides "what counts as a well-formed world" - the
publish use case calls :meth:`Ontology.assert_publishable` instead of
keeping its own copy of these rules.

v0.2 checks the decidable minimum: identity criteria, property facets,
enumerated cardinality, inverse relations, category/profile declarations
and the kernel-compatible type system.
"""

from __future__ import annotations

from .model import (
    CARDINALITIES,
    CATEGORIES,
    PROFILES,
    TEMPORAL_MODES,
    TYPE_KINDS,
    Ontology,
    PropertySpec,
    normalize_cardinality,
)

_IDENTIFIER_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SCALAR_KINDS = ("string", "number", "integer", "boolean", "datetime")


def validate_ontology(ontology: Ontology) -> list[str]:
    problems: list[str] = []

    if ontology.profile not in PROFILES:
        problems.append(
            f"unknown profile {ontology.profile!r}; expected one of {list(PROFILES)}"
        )
    if not ontology.objects:
        problems.append("ontology defines no object types")

    for otype in ontology.objects.values():
        if not otype.name or set(otype.name) - _IDENTIFIER_OK or otype.name[0].isdigit():
            problems.append(f"object type name {otype.name!r} is not an identifier")
        if not otype.properties:
            problems.append(f"object type {otype.name!r} has no properties")
        if otype.category not in CATEGORIES:
            problems.append(
                f"object type {otype.name!r}: unknown category {otype.category!r}; "
                f"expected one of {list(CATEGORIES)}"
            )
        _validate_identity(otype, problems)
        _validate_state_machine(otype, problems)
        for prop in otype.properties.values():
            problems.extend(_validate_property(otype.name, prop))
        if otype.category == "event":
            occurred = otype.properties.get("occurred_at")
            if occurred is None or occurred.type != "datetime":
                problems.append(
                    f"object type {otype.name!r}: category 'event' requires a "
                    "'occurred_at' datetime property"
                )
        if otype.category == "value" and otype.identity:
            problems.append(
                f"object type {otype.name!r}: category 'value' must not declare "
                "an identity (values are identified by their content)"
            )

    for rel in ontology.relations.values():
        if rel.source not in ontology.objects:
            problems.append(
                f"relation {rel.name!r}: source {rel.source!r} is not defined"
            )
        if rel.target not in ontology.objects:
            problems.append(
                f"relation {rel.name!r}: target {rel.target!r} is not defined"
            )
        if normalize_cardinality(rel.cardinality) not in CARDINALITIES:
            problems.append(
                f"relation {rel.name!r}: unknown cardinality {rel.cardinality!r}; "
                f"expected one of {[c for c in CARDINALITIES if c]}"
            )
        _validate_inverse(rel, ontology, problems)
        for endpoint in (rel.source, rel.target):
            endpoint_type = ontology.objects.get(endpoint)
            if endpoint_type is not None and endpoint_type.category == "value":
                problems.append(
                    f"relation {rel.name!r}: value type {endpoint!r} cannot be a "
                    "relation endpoint"
                )

    _validate_vocabulary(ontology, problems)

    return problems


def _validate_vocabulary(ontology: Ontology, problems: list[str]) -> None:
    """Vocabulary contract (plan-v2 1.11): term/alias uniqueness, real concepts."""
    seen: dict[str, str] = {}
    for term in ontology.vocabulary.values():
        if not str(term.term or "").strip():
            problems.append("vocabulary: term must not be empty")
            continue
        if term.kind not in ("object", "property", "relation"):
            problems.append(
                f"vocabulary {term.term!r}: unknown kind {term.kind!r}; "
                "expected object/property/relation")
        if term.kind == "object" and term.concept not in ontology.objects:
            problems.append(
                f"vocabulary {term.term!r}: concept {term.concept!r} is not defined")
        if term.kind == "relation" and term.concept not in ontology.relations:
            problems.append(
                f"vocabulary {term.term!r}: relation {term.concept!r} is not defined")
        if term.kind == "property":
            otype, _, pname = str(term.concept).partition(".")
            spec = ontology.objects.get(otype)
            if not pname or spec is None or pname not in spec.properties:
                problems.append(
                    f"vocabulary {term.term!r}: property concept {term.concept!r} "
                    "is not defined (expected 'Type.property')")
        for label in [term.term, *term.aliases]:
            key = str(label).strip().casefold()
            if not key:
                problems.append(f"vocabulary {term.term!r}: alias must not be empty")
                continue
            owner = seen.get(key)
            if owner is not None and owner != term.term:
                problems.append(
                    f"vocabulary alias {label!r} is used by both {owner!r} and "
                    f"{term.term!r}; aliases must be unambiguous")
            else:
                seen[key] = term.term


# ----------------------------------------------------------------------
# property facets
# ----------------------------------------------------------------------


def _validate_property(otype: str, prop: PropertySpec) -> list[str]:
    problems: list[str] = []
    where = f"{otype}.{prop.name}"
    if prop.type not in TYPE_KINDS:
        problems.append(f"{where}: unknown property type {prop.type!r}")
        return problems
    if prop.required and prop.default is not None:
        problems.append(f"{where}: 'required' and 'default' are mutually exclusive")
    if prop.temporal and prop.temporal not in TEMPORAL_MODES:
        problems.append(
            f"{where}: unknown temporal mode {prop.temporal!r}; "
            f"expected one of {[t for t in TEMPORAL_MODES if t]}"
        )
    if prop.precision is not None and (
        not isinstance(prop.precision, int) or prop.precision < 0
    ):
        problems.append(f"{where}: precision must be a non-negative integer")
    if prop.minimum is not None and prop.type not in ("number", "integer"):
        problems.append(f"{where}: minimum/maximum only apply to numeric types")
    if prop.maximum is not None and prop.type not in ("number", "integer"):
        problems.append(f"{where}: minimum/maximum only apply to numeric types")
    if (
        prop.minimum is not None
        and prop.maximum is not None
        and prop.minimum > prop.maximum
    ):
        problems.append(f"{where}: minimum {prop.minimum} > maximum {prop.maximum}")
    if prop.unit and prop.type not in ("number", "integer"):
        problems.append(f"{where}: unit only applies to numeric types")
    if prop.enum:
        for value in prop.enum:
            if not _value_matches(prop.type, value):
                problems.append(
                    f"{where}: enum value {value!r} is not a valid {prop.type}"
                )
    if prop.default is not None and not _value_matches(prop.type, prop.default):
        problems.append(
            f"{where}: default {prop.default!r} is not a valid {prop.type}"
        )
    if prop.unique and prop.type not in _SCALAR_KINDS:
        problems.append(f"{where}: unique only applies to scalar types")
    return problems


def _value_matches(kind: str, value: object) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "datetime":
        from ..values import is_datetime

        return is_datetime(value)
    return False


# ----------------------------------------------------------------------
# lint (plan-v2 O-6/O-7): warnings, never publish blockers
# ----------------------------------------------------------------------

_RESERVED_PROPERTIES = {"id", "ref", "version", "updated_at", "source"}
_DATETIME_NAMES = {"hour", "date", "time", "timestamp", "day", "month", "week"}
_DATETIME_SUFFIXES = ("_at", "_time", "_date", "_ts")


def _looks_temporal(name: str) -> bool:
    lowered = name.lower()
    return lowered in _DATETIME_NAMES or lowered.endswith(_DATETIME_SUFFIXES)


def _type_words(name: str) -> list[str]:
    import re

    return [w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+", name)]


def _normalized_ref_name(name: str) -> str:
    lowered = name.lower()
    for suffix in ("_id", "_ref"):
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
    return lowered


def lint_ontology(ontology: Ontology) -> list[dict]:
    """Modeling lint (never blocks publishing): duplicated endpoints, temporal
    names typed as string, snapshot temporal properties without ``valid_from``,
    reserved names and category mismatches."""
    warnings: list[dict] = []

    def warn(code: str, subject: str, message: str) -> None:
        warnings.append({"code": code, "subject": subject, "message": message})

    for otype in ontology.objects.values():
        for prop in otype.properties.values():
            subject = f"{otype.name}.{prop.name}"
            if prop.name in _RESERVED_PROPERTIES:
                warn("reserved-name", subject,
                     f"{subject} uses a name reserved by the runtime metadata")
            if prop.type == "string" and _looks_temporal(prop.name):
                warn("string-datetime", subject,
                     f"{subject} looks temporal but is typed string; use "
                     "type: datetime so values are normalized and sortable")
            if prop.temporal == "snapshot":
                valid_from = otype.properties.get("valid_from")
                if valid_from is None or valid_from.type != "datetime":
                    warn("temporal-without-valid-from", subject,
                         f"{subject} is temporal=snapshot but {otype.name} has "
                         "no valid_from datetime property")
        if otype.category == "event":
            occurred = otype.properties.get("occurred_at")
            if occurred is None or occurred.type != "datetime":
                warn("event-without-occurred-at", otype.name,
                     f"event {otype.name} should declare occurred_at: datetime")
        if otype.category == "value" and otype.identity:
            warn("value-with-identity", otype.name,
                 f"value type {otype.name} must not declare an identity")

    for rel in ontology.relations.values():
        source = ontology.objects.get(rel.source)
        target = ontology.objects.get(rel.target)
        for role, owner in (("source", source), ("target", target)):
            if owner is not None and owner.category == "value":
                warn("value-as-relation-endpoint", f"{rel.name}.{role}",
                     f"relation {rel.name!r} has value type {owner.name!r} as "
                     f"its {role}; values are identified by content and cannot "
                     "carry relation identity")
        if source is None:
            continue
        target_words = set(_type_words(rel.target))
        for prop in source.properties.values():
            if prop.type not in ("string", "integer"):
                continue
            ref_name = _normalized_ref_name(prop.name)
            words = set(_type_words(ref_name)) | set(ref_name.split("_"))
            if ref_name == rel.name.lower() or (target_words & words):
                warn("duplicated-endpoint", f"{rel.source}.{prop.name}",
                     f"{rel.source}.{prop.name} looks like a direct reference to "
                     f"{rel.target} while relation {rel.name!r} already models "
                     "that endpoint; keep one representation")
    return warnings



# ----------------------------------------------------------------------
# identity / state machine / inverse
# ----------------------------------------------------------------------


def _validate_identity(otype, problems: list[str]) -> None:
    seen: set[str] = set()
    for field_name in otype.identity:
        where = f"object type {otype.name!r}: identity"
        if field_name in seen:
            problems.append(f"{where}: field {field_name!r} is listed twice")
            continue
        seen.add(field_name)
        prop = otype.properties.get(field_name)
        if prop is None:
            problems.append(f"{where}: field {field_name!r} is not a property")
            continue
        if prop.type not in ("string", "integer"):
            problems.append(
                f"{where}: field {field_name!r} must be string/integer, "
                f"got {prop.type!r}"
            )
        if prop.required is False:
            problems.append(
                f"{where}: field {field_name!r} must be required "
                "(the identity key can never be absent)"
            )
    if otype.identity and otype.category == "event":
        problems.append(
            f"object type {otype.name!r}: events are identified by event id, "
            "identity must be declared on their parent object"
        )


def _validate_state_machine(otype, problems: list[str]) -> None:
    spec = otype.state_machine
    if not spec:
        return
    where = f"object type {otype.name!r}: state_machine"
    if not isinstance(spec, dict):
        problems.append(f"{where} must be an object")
        return
    field_name = spec.get("field")
    if field_name not in otype.properties:
        problems.append(f"{where}: field {field_name!r} is not a property")
        return
    transitions = spec.get("transitions")
    if not isinstance(transitions, dict) or not transitions:
        problems.append(f"{where}: 'transitions' must be a non-empty object")
        return
    declared = set(otype.properties[field_name].enum or [])
    for source, targets in transitions.items():
        if not isinstance(targets, list) or not targets:
            problems.append(f"{where}: transitions[{source!r}] must be a non-empty list")
            continue
        if declared and source not in declared:
            problems.append(f"{where}: state {source!r} is not in the enum")
        for target in targets:
            if declared and target not in declared:
                problems.append(f"{where}: state {target!r} is not in the enum")


def _validate_inverse(rel, ontology, problems: list[str]) -> None:
    if not rel.inverse:
        return
    inverse = ontology.relations.get(rel.inverse)
    if inverse is None:
        problems.append(
            f"relation {rel.name!r}: inverse {rel.inverse!r} is not defined"
        )
        return
    if inverse.inverse and inverse.inverse != rel.name:
        problems.append(
            f"relation {rel.name!r}: inverse {rel.inverse!r} points to "
            f"{inverse.inverse!r}, not back to {rel.name!r}"
        )
    if (inverse.source, inverse.target) != (rel.target, rel.source):
        problems.append(
            f"relation {rel.name!r}: inverse {rel.inverse!r} must reverse the "
            f"endpoints ({rel.target} -> {rel.source})"
        )
