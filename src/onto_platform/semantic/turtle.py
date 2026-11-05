"""Dependency-free, deterministic Turtle 1.1 writer.

The platform core must stay third-party-free (``pyproject``:
``dependencies = []``), yet the ``owl-rdf`` and ``skos`` adapters have to
emit parseable Turtle.  This module is the small writer they share:

* prefix declarations come from ``IriPolicy.prefix_map()``;
* IRIs are abbreviated to QNames when the prefix table allows it, and
  rendered as ``<...>`` otherwise;
* literal escaping covers the Turtle ``ECHAR`` set we actually use
  (``"``, ``\\``, newline, carriage return, tab) plus ``\\uXXXX`` for the
  remaining control characters;
* ``xsd:integer`` / ``xsd:decimal`` / ``xsd:double`` / ``xsd:boolean`` are
  written as bare literals, everything else as a quoted typed literal
  (``xsd:string`` is written explicitly, never half-implicit);
* statements are de-duplicated and sorted by (subject, predicate, object),
  so one statement *set* always serializes to byte-identical output
  regardless of insertion order (docs/standards-plugin-architecture.md §6).

The writer only serializes.  Which axioms an adapter emits -- and, more
importantly, which OWL axioms it must *not* emit (see
docs/standards-plugin-architecture.md §2.5) -- is the adapter's decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: XML Schema datatype namespace.
XSD = "http://www.w3.org/2001/XMLSchema#"
XSD_STRING = XSD + "string"

#: A statement is ``(subject IRI, predicate IRI, object)`` where the object
#: is either an IRI (``str``) or a :class:`Literal` (a ``(value, datatype)``
#: or ``(value, "@lang")`` tuple is accepted as shorthand).
Statement = tuple[str, str, object]

_PREFIX_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")

#: Conservative PN_LOCAL check: ASCII word chars, dot, dash and uppercase
#: percent escapes only.  Anything fancier falls back to a full IRI, which
#: is always safe.
_PN_LOCAL = re.compile(
    r"^(?:[A-Za-z0-9_]|%[0-9A-Fa-f]{2})"
    r"(?:(?:[A-Za-z0-9_.\-]|%[0-9A-Fa-f]{2})*"
    r"(?:[A-Za-z0-9_\-]|%[0-9A-Fa-f]{2}))?$"
)

_ECHAR = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

_INTEGER = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?(?:\d+\.\d+|\.\d+)$")
_DOUBLE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_BOOLEANS = {"true": "true", "false": "false", "1": "true", "0": "false"}

_INTEGER_DATATYPES = tuple(
    XSD + name
    for name in (
        "integer",
        "long",
        "int",
        "short",
        "byte",
        "nonNegativeInteger",
        "positiveInteger",
        "nonPositiveInteger",
        "negativeInteger",
        "unsignedLong",
        "unsignedInt",
        "unsignedShort",
        "unsignedByte",
    )
)
_FLOATING_DATATYPES = (XSD + "double", XSD + "float")


def _escape_literal(value: str) -> str:
    """Escape a string for use between double quotes in Turtle."""
    out: list[str] = []
    for char in value:
        escape = _ECHAR.get(char)
        if escape is not None:
            out.append(escape)
            continue
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04X}")
        else:
            out.append(char)
    return "".join(out)


def _iri_ref(iri: str) -> str:
    """Render an IRI as ``<...>``, escaping characters IRIREF forbids."""
    out: list[str] = []
    for char in iri:
        code = ord(char)
        if char in '<>"{}|^`\\' or code <= 0x20:
            out.append(f"\\u{code:04X}" if code <= 0xFFFF else f"\\U{code:08X}")
        else:
            out.append(char)
    return "<" + "".join(out) + ">"


def _bare_literal(datatype: str, value: str) -> str | None:
    """Return the bare Turtle form of a numeric/boolean literal, if any."""
    if datatype == XSD + "boolean":
        return _BOOLEANS.get(value)
    if datatype in _INTEGER_DATATYPES:
        return value if _INTEGER.match(value) else None
    if datatype == XSD + "decimal":
        return value if _DECIMAL.match(value) else None
    if datatype in _FLOATING_DATATYPES:
        return value if _DOUBLE.match(value) else None
    return None


@dataclass(frozen=True, slots=True)
class Literal:
    """A literal object value.

    ``datatype`` defaults to ``xsd:string`` and is written explicitly.
    When ``lang`` is set the literal is a language-tagged string
    (``"..."@lang``) and ``datatype`` is irrelevant.
    """

    value: str
    datatype: str = XSD_STRING
    lang: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            object.__setattr__(self, "value", str(self.value))
        if self.lang:
            object.__setattr__(self, "lang", str(self.lang).lstrip("@"))
        elif not self.datatype:
            object.__setattr__(self, "datatype", XSD_STRING)

    @property
    def effective_datatype(self) -> str:
        """The datatype actually serialized ("" for language literals)."""
        return "" if self.lang else (self.datatype or XSD_STRING)


def literal(value: object, datatype: str | None = None, lang: str = "") -> Literal:
    """Build a :class:`Literal` (``datatype`` defaults to ``xsd:string``)."""
    return Literal(str(value), datatype or XSD_STRING, lang)


class TurtleWriter:
    """Serialize ``(subject, predicate, object)`` statements as Turtle.

    The writer is stateless apart from its prefix table, so one instance may
    be reused for several documents.
    """

    media_type = "text/turtle"
    suffix = ".ttl"

    def __init__(self, prefixes: Mapping[str, str] | None = None) -> None:
        self._prefixes = self._checked_prefixes(prefixes)

    @staticmethod
    def _checked_prefixes(prefixes: Mapping[str, str] | None) -> dict[str, str]:
        checked: dict[str, str] = {}
        for name, namespace in (prefixes or {}).items():
            name = str(name)
            if not _PREFIX_NAME.match(name):
                raise ValueError(f"invalid Turtle prefix name {name!r}")
            if not isinstance(namespace, str) or not namespace:
                raise ValueError(
                    f"prefix {name!r} needs a non-empty namespace IRI")
            checked[name] = namespace
        return checked

    @property
    def prefixes(self) -> dict[str, str]:
        """A copy of the declared prefix table."""
        return dict(self._prefixes)

    # ---- public API ---------------------------------------------------

    def serialize(self, statements: Iterable[Statement]) -> str:
        """Render statements as a deterministic Turtle document (text)."""
        rows = self._sorted_statements(statements)
        lines = [
            f"@prefix {name}: {_iri_ref(namespace)} ."
            for name, namespace in sorted(self._prefixes.items())
        ]
        if rows:
            lines.append("")
            lines.extend(
                f"{self._term(subject)} {self._term(predicate)} "
                f"{self._object_text(object_)} ."
                for subject, predicate, object_ in rows
            )
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    def write(self, statements: Iterable[Statement]) -> bytes:
        """Render statements as UTF-8 Turtle bytes."""
        return self.serialize(statements).encode("utf-8")

    # ---- statement normalisation --------------------------------------

    def _sorted_statements(
        self, statements: Iterable[Statement]
    ) -> list[tuple[str, str, object]]:
        unique: dict[tuple[str, str, tuple[str, str, str]], tuple[str, str, object]] = {}
        for statement in statements:
            try:
                subject, predicate, object_ = statement
            except (TypeError, ValueError):
                raise TypeError(
                    "each statement must be a (subject, predicate, object) "
                    f"triple, got {statement!r}") from None
            if not isinstance(subject, str) or not subject:
                raise TypeError(f"statement subject must be an IRI, got {subject!r}")
            if not isinstance(predicate, str) or not predicate:
                raise TypeError(
                    f"statement predicate must be an IRI, got {predicate!r}")
            normalised = self._object(object_)
            key = (subject, predicate, self._object_key(normalised))
            unique.setdefault(key, (subject, predicate, normalised))
        return [unique[key] for key in sorted(unique)]

    def _object(self, value: object) -> str | Literal:
        if isinstance(value, Literal) or isinstance(value, str):
            return value
        if isinstance(value, tuple):
            if len(value) != 2:
                raise TypeError(
                    "literal shorthand must be (value, datatype) or "
                    f"(value, '@lang'), got {value!r}")
            text, tag = value
            if tag is None:
                return Literal(str(text))
            tag = str(tag)
            if tag.startswith("@"):
                return Literal(str(text), lang=tag[1:])
            return Literal(str(text), datatype=tag)
        raise TypeError(
            "object must be an IRI string, a Literal, or a "
            f"(value, datatype) tuple, got {type(value).__name__}")

    @staticmethod
    def _object_key(object_: str | Literal) -> tuple[str, str, str]:
        if isinstance(object_, Literal):
            if object_.lang:
                return ("0", object_.lang, object_.value)
            return ("1", object_.effective_datatype, object_.value)
        return ("2", "", object_)

    # ---- term rendering ----------------------------------------------

    def _term(self, iri: str) -> str:
        best: tuple[int, str, str] | None = None
        for name, namespace in self._prefixes.items():
            if not iri.startswith(namespace):
                continue
            local = iri[len(namespace):]
            if not _PN_LOCAL.match(local):
                continue
            candidate = (len(namespace), name, local)
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is not None:
            return f"{best[1]}:{best[2]}"
        return _iri_ref(iri)

    def _object_text(self, object_: str | Literal) -> str:
        if isinstance(object_, Literal):
            quoted = '"' + _escape_literal(object_.value) + '"'
            if object_.lang:
                return f"{quoted}@{object_.lang}"
            datatype = object_.effective_datatype
            bare = _bare_literal(datatype, object_.value)
            if bare is not None:
                return bare
            return f"{quoted}^^{self._term(datatype)}"
        return self._term(object_)


def serialize_turtle(
    statements: Iterable[Statement],
    prefixes: Mapping[str, str] | None = None,
) -> str:
    """Convenience wrapper around :class:`TurtleWriter.serialize`."""
    return TurtleWriter(prefixes).serialize(statements)


def write_turtle(
    statements: Iterable[Statement],
    prefixes: Mapping[str, str] | None = None,
) -> bytes:
    """Convenience wrapper around :class:`TurtleWriter.write`."""
    return TurtleWriter(prefixes).write(statements)


__all__ = [
    "XSD",
    "XSD_STRING",
    "Literal",
    "Statement",
    "TurtleWriter",
    "literal",
    "serialize_turtle",
    "write_turtle",
]
