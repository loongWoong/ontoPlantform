"""Field-level masking (design §8.3).

Masking is an *obligation*, not a UI concern.  The PDP attaches a field policy
to an ALLOW; the PEP must apply it before serialization or the request is
denied.  Built-ins are deterministic and dependency-free so tests can pin exact
output.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from ...errors import ValidationError


def _redact(_: Any) -> str:
    return "***"


def _last4(value: Any) -> str:
    text = "" if value is None else str(value)
    return "***" + text[-4:] if len(text) > 4 else "***"


def _plate(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) <= 4:
        return "***"
    return text[:2] + "***" + text[-2:]


def _phone(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) < 7:
        return "***"
    return text[:3] + "****" + text[-4:]


def _email(value: Any) -> str:
    text = "" if value is None else str(value)
    if "@" not in text:
        return "***"
    local, _, domain = text.partition("@")
    return (local[:1] or "*") + "***@" + domain


def _id_number(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) < 8:
        return "***"
    return text[:3] + "********" + text[-4:]


def _amount(value: Any) -> str:
    # Keep sign but hide magnitude: enough for UI context, not for leakage.
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "***"
    return "***" if number >= 0 else "-***"


def _hash(value: Any) -> str:
    from hashlib import sha256

    return "sha256:" + sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _strip(_: Any) -> None:
    return None


MASKERS: dict[str, Callable[[Any], Any]] = {
    "redact": _redact,
    "strip": _strip,
    "last4": _last4,
    "plate": _plate,
    "phone": _phone,
    "email": _email,
    "id_number": _id_number,
    "amount": _amount,
    "currency": _amount,
    "hash": _hash,
    "none": lambda value: value,
}


def validate_mask_spec(spec: Mapping[str, Any]) -> dict:
    """Validate one field-policy entry: ``{"field": ..., "mask": ...}``."""
    if not isinstance(spec, Mapping):
        raise ValidationError("field policy must be an object")
    field = spec.get("field")
    if not isinstance(field, str) or not field.strip():
        raise ValidationError("field policy needs a field name")
    if "." in field:
        raise ValidationError("field mask targets a top-level field")
    mask = spec.get("mask", "redact")
    if mask not in MASKERS:
        raise ValidationError(f"unknown field mask {mask!r}")
    out = {"field": field, "mask": mask}
    if spec.get("when"):
        # Reuse the filter language for conditional masks.  Validation is done
        # lazily by the evaluator so one bad condition cannot crash a read.
        out["when"] = spec["when"]
    return out


def apply_field_masks(records: Iterable[Mapping[str, Any]],
                      specs: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Return copies with masked fields; never mutate repository rows."""
    prepared = [validate_mask_spec(spec) for spec in specs]
    if not prepared:
        return [dict(row) for row in records]
    out: list[dict] = []
    for record in records:
        row = dict(record)
        for spec in prepared:
            field = spec["field"]
            if field not in row:
                continue
            if spec["mask"] == "strip":
                row.pop(field, None)
                continue
            masker = MASKERS[spec["mask"]]
            row[field] = masker(row[field])
        out.append(row)
    return out


def mask_record(record: Mapping[str, Any], specs: Iterable[Mapping[str, Any]]) -> dict:
    return apply_field_masks([record], specs)[0]


class FieldMasker:
    """Small facade used by serializers."""

    def __init__(self, specs: Iterable[Mapping[str, Any]]) -> None:
        self.specs = [validate_mask_spec(spec) for spec in specs]

    def apply(self, records: Iterable[Mapping[str, Any]]) -> list[dict]:
        return apply_field_masks(records, self.specs)

    def one(self, record: Mapping[str, Any]) -> dict:
        return mask_record(record, self.specs)
