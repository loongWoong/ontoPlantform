"""Shared ``ExportRequest`` validation for standard-format adapters.

P0 is deliberately narrow (docs/standards-plugin-completion-plan-v1.md, D4):
**Turtle only, TBox only**.  Keeping the checks here -- instead of one
adapter importing another adapter -- is what makes every plugin independently
installable/removable (review G-12): the SKOS adapter must not depend on the
OWL adapter just to validate a media type.

These helpers raise :class:`~onto_platform.semantic.spi.AdapterError`
(HTTP 400) and never mutate the request.
"""

from __future__ import annotations

from .spi import AdapterError, AdapterManifest, ExportRequest

#: Accepted ``ExportRequest.format`` values ("" means the default format).
SUPPORTED_FORMATS: tuple[str, ...] = ("", "text/turtle", "turtle")

#: Scopes P0 export adapters can honour: the TBox (serving contract) only.
SUPPORTED_SCOPES: tuple[str, ...] = ("", "tbox")


def check_export_format(
        request: ExportRequest | None, manifest: AdapterManifest) -> str:
    """Validate the requested media type, raising ``AdapterError`` otherwise."""
    format_name = str(getattr(request, "format", "") or "")
    if format_name not in SUPPORTED_FORMATS:
        raise AdapterError(
            f"{manifest.name} adapter does not support format {format_name!r}; "
            "supported formats: text/turtle, turtle")
    return format_name


def check_scope(
        request: ExportRequest | None, manifest: AdapterManifest) -> str:
    """Validate the requested export scope (P0: TBox only).

    An ABox request is *rejected explicitly* rather than silently returning a
    TBox document (review G-04): a consumer that asked for instance data must
    never mistake a schema export for one.
    """
    scope = str(getattr(request, "scope", "") or "")
    include_abox = bool(getattr(request, "include_abox", False))
    if scope not in SUPPORTED_SCOPES or include_abox:
        requested = scope or "tbox"
        raise AdapterError(
            f"{manifest.name} adapter exports the TBox only in P0; requested "
            f"scope {requested!r} (include_abox={include_abox}) is not "
            "supported")
    return scope or "tbox"


__all__ = [
    "SUPPORTED_FORMATS",
    "SUPPORTED_SCOPES",
    "check_export_format",
    "check_scope",
]
