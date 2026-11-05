"""Application use cases for standard-format adapter plugins.

This is the platform-facing half of the plugin story
(docs/standards-plugin-architecture.md §2.3, §5): the composition root
installs one :class:`StandardsUseCases` facade, the REST/CLI adapters call it,
and it is the only place that combines

    authorization (``ctx.require``)
    -> published ontology (``published_ontology_row``)
    -> read-only adapter context (``semantic.context.build_context``)
    -> plugin registry (``semantic.registry.default_registry``)
    -> audit (``ctx.audit_event``)

The platform core stays dependency-free: adapters are discovered through the
registry, never imported here.  An adapter that is missing, broken or
SPI-incompatible is reported by the registry as a platform error, so the
interfaces keep their normal error mapping (unknown adapter -> 404,
invalid request/format -> 400) and a broken plugin can never take the
platform down.

Two endpoints are backed by this facade:

    GET /api/workspaces/{wid}/standards/adapters
    GET /api/workspaces/{wid}/standards/export?adapter=owl-rdf&format=text/turtle
"""

from __future__ import annotations

from .common import ApplicationContext, published_ontology_row
from ..domain.ontology.model import Ontology
from ..errors import NotFoundError
from ..semantic.context import build_context
from ..semantic.registry import default_registry
from ..semantic.spi import AdapterError, ExportRequest

#: exports are TBox-only in P0; ABox export/query is P1/P2 work.
DEFAULT_SCOPE = "tbox"


def _published_ontology(ctx: ApplicationContext, workspace_id: str) -> Ontology:
    """The served ontology version, or ``NotFoundError``.

    Export must be reproducible: it is bound to the *published* revision, not
    to whatever draft an editor happens to be working on.  Exporting a draft
    would produce an artifact that no released version corresponds to.
    """
    row = published_ontology_row(ctx, workspace_id)
    if row is None:
        raise NotFoundError(
            f"workspace {workspace_id!r} has no published ontology; publish "
            "one before exporting a standard artifact")
    return Ontology.from_dict(row)


class StandardsUseCases:
    """List installed standard adapters and export the published ontology.

    ``registry`` is injectable for hosts/tests that need a private plugin
    registry; by default the process-wide :func:`default_registry` is used
    (lazy discovery, so importing the platform never imports third-party
    plugin code).
    """

    def __init__(self, ctx: ApplicationContext, registry=None) -> None:
        self._ctx = ctx
        self._registry = registry

    @property
    def registry(self):
        """The plugin registry backing this facade (lazy default discovery)."""
        if self._registry is None:
            return default_registry()
        return self._registry

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def list_adapters(self, workspace_id: str, actor: str) -> list[dict]:
        """Manifest rows of every discovered adapter, sorted by name.

        Each row carries ``status`` (``ready`` / ``invalid``) and
        ``problems``, so an operator can see why a plugin is unusable without
        reading logs.  A broken plugin is listed, not hidden.
        """
        self._ctx.require(actor, workspace_id, "viewer")
        return self.registry.manifests()

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------

    def export(self, workspace_id: str, actor: str, adapter: str,
               format: str | None = None) -> dict:
        """Export the published ontology through one adapter.

        Returns ``{"adapter","format","media_type","filename","content",
        "metadata"}`` where ``content`` is raw bytes and ``format`` is the
        artifact's canonical media type (a request for ``"turtle"`` is
        normalized to ``"text/turtle"`` so the response header never lies).

        Errors: no published ontology -> ``NotFoundError`` (404); unknown
        adapter -> ``AdapterNotFound`` (404); registered-but-unusable adapter
        or unsupported format -> ``AdapterError`` (400).
        """
        self._ctx.require(actor, workspace_id, "viewer")
        name = str(adapter or "").strip()
        if not name:
            raise AdapterError("standard export requires an adapter name")

        ontology = _published_ontology(self._ctx, workspace_id)
        export_adapter = self.registry.get(name)
        context = build_context(ontology, actor, scope=DEFAULT_SCOPE)
        request = ExportRequest(format=str(format or ""), scope=DEFAULT_SCOPE)
        artifact = export_adapter.export(context, request)

        content = artifact.content
        self._ctx.audit_event(
            actor, workspace_id, "standards.export", name,
            {
                "format": artifact.media_type,
                "bytes": len(content),
                "version": int(ontology.version),
                "schema_fingerprint": str(ontology.schema_fingerprint),
            },
        )
        return {
            "adapter": name,
            "format": artifact.media_type,
            "media_type": artifact.media_type,
            "filename": artifact.filename,
            "content": content,
            "metadata": dict(artifact.metadata or {}),
        }


__all__ = ["DEFAULT_SCOPE", "StandardsUseCases"]
