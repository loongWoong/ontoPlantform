"""Data use cases: connect reality, project it into the kernel (链二).

    External Data -> DataSource -> Mapping/Projection
                  -> RuntimeGateway -> Kernel Object / Relation
"""

from __future__ import annotations

import hashlib
import json

from ..domain.data.model import (
    DataAsset,
    DataSource,
    ObjectMapping,
    Projection,
    RelationMapping,
    validate_projection,
)
from ..errors import ConnectorError, NotFoundError, ValidationError
from ..ports.runtime import LinkAssert, ObjectUpsert, SemanticDelta
from .common import ApplicationContext


def _source_watermark(rows_by_asset: dict[str, list[dict]]) -> str:
    """Stable content hash of the fetched source rows (the projection v2
    watermark: unchanged source -> unchanged hash -> run is skipped)."""
    canonical = json.dumps(
        {asset: rows for asset, rows in sorted(rows_by_asset.items())},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class ConnectDataSource:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str, kind: str,
                 config: dict | None = None, assets: list[dict] | None = None,
                 test_connection: bool = True) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        from ..infrastructure.datasource.registry import connector_for

        try:  # unknown kinds are rejected by the connector registry
            connector_for(kind, dict(config or {}))
        except ValidationError as exc:
            raise ValidationError(str(exc)) from exc
        source = DataSource(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=name,
            kind=kind,
            config=dict(config or {}),
            assets={
                a["name"]: DataAsset.from_dict(a) for a in (assets or [])
            },
            created_at=self._ctx.clock(),
        )
        if test_connection:
            from ..infrastructure.datasource.registry import connector_for

            try:
                connector_for(kind, source.config).test()
            except Exception as exc:
                raise ConnectorError(f"datasource connection failed: {exc}") from exc
        self._ctx.save("datasource", source.to_dict())
        self._ctx.audit_event(actor, workspace_id, "data.connect_source", source.id,
                              {"kind": kind, "assets": sorted(source.assets)})
        return source.to_dict()


class CreateProjection:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str, datasource_id: str,
                 object_mappings: list[dict] | None = None,
                 relation_mappings: list[dict] | None = None,
                 row_limit: int = 0, mode: str = "additive") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        if mode not in ("additive", "authoritative"):
            raise ValidationError(
                "projection mode must be 'additive' or 'authoritative'")
        source_data = self._ctx.must_get("datasource", workspace_id, datasource_id)
        source = DataSource.from_dict(source_data)
        projection = Projection(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=name,
            datasource_id=datasource_id,
            object_mappings=[ObjectMapping.from_dict(m) for m in (object_mappings or [])],
            relation_mappings=[
                RelationMapping.from_dict(m) for m in (relation_mappings or [])
            ],
            row_limit=max(0, int(row_limit or 0)),
            mode=mode,
            created_at=self._ctx.clock(),
            updated_at=self._ctx.clock(),
        )
        problems = validate_projection(projection, source)
        if problems:
            raise ValidationError("projection validation failed: " + "; ".join(problems))
        # every mapped object type / relation must exist in the ontology
        ontologies = self._ctx.repo.list("ontology", workspace_id)
        ontology_objects = (ontologies[0].get("objects", {}) if ontologies else {})
        ontology_relations = (ontologies[0].get("relations", {}) if ontologies else {})
        for m in projection.object_mappings:
            if m.object_type not in ontology_objects:
                raise ValidationError(
                    f"object mapping targets unknown object type {m.object_type!r}"
                )
            # identity criterion is declared in the TBox; the projection's
            # business key must be exactly that criterion (O-1)
            identity = list(ontology_objects[m.object_type].get("identity") or [])
            if identity and m.id_field != identity[0]:
                raise ValidationError(
                    f"object mapping {m.object_type!r}: id_field {m.id_field!r} "
                    f"does not match the declared identity field {identity[0]!r}"
                )
            properties = ontology_objects[m.object_type].get("properties") or {}
            for field_map in m.fields:
                declared = (properties.get(field_map.property) or {}).get("unit") or ""
                if field_map.unit and declared and field_map.unit != declared:
                    raise ValidationError(
                        f"object mapping {m.object_type}.{field_map.property}: "
                        f"source unit {field_map.unit!r} does not match the "
                        f"ontology unit {declared!r}")
        for m in projection.relation_mappings:
            if m.relation not in ontology_relations:
                raise ValidationError(
                    f"relation mapping targets unknown relation {m.relation!r}"
                )
        self._ctx.save("projection", projection.to_dict())
        self._ctx.audit_event(actor, workspace_id, "data.create_projection", projection.id,
                              {"name": name})
        return projection.to_dict()


class RunProjection:
    """Source State -> Semantic Delta -> Kernel (one logical, idempotent run).

    v2 additions (plan-v2 1.6): a content watermark (hash of the source rows
    actually mapped) skips runs when nothing changed; every run is recorded
    with status/watermark/outcome; a failed run leaves no semantic residue
    because ``apply_delta`` is one kernel transaction.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, projection_id: str, actor: str,
                 force: bool = False) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        projection = Projection.from_dict(
            self._ctx.must_get("projection", workspace_id, projection_id)
        )
        source = DataSource.from_dict(
            self._ctx.must_get("datasource", workspace_id, projection.datasource_id)
        )
        ontologies = self._ctx.repo.list("ontology", workspace_id)
        relations = (ontologies[0].get("relations", {}) if ontologies else {})

        from ..infrastructure.datasource.registry import connector_for

        connector = connector_for(source.kind, source.config)
        rows_by_asset: dict[str, list[dict]] = {}
        for asset_name in {
            *(m.asset for m in projection.object_mappings),
            *(m.asset for m in projection.relation_mappings),
        }:
            asset = source.assets.get(asset_name)
            if asset is None:
                raise ValidationError(f"unknown asset {asset_name!r} in datasource")
            try:
                rows_by_asset[asset_name] = connector.fetch(asset.config)
            except Exception as exc:
                raise ConnectorError(
                    f"failed to fetch asset {asset_name!r}: {exc}"
                ) from exc

        truncated = False
        if projection.row_limit:
            over = {
                asset_name: len(rows) for asset_name, rows in rows_by_asset.items()
                if len(rows) > projection.row_limit
            }
            if over:
                # A partial projection would silently leave objects behind, so
                # exceeding the bound is a hard, visible failure before any
                # kernel side effect. Operators can raise row_limit or fix the
                # source query and run again.
                run_id = f"prj_{self._ctx.id_factory()}"
                started_at = self._ctx.clock()
                detail = ", ".join(f"{name}={count}" for name, count in sorted(over.items()))
                error = (f"projection row limit {projection.row_limit} exceeded "
                         f"before apply: {detail}")
                self._record_run(
                    workspace_id, projection.id, actor, run_id, "",
                    started_at, status="failed", error=error, force=force,
                    summary={"projection": projection.id,
                             "row_limit": projection.row_limit,
                             "exceeded": over, "truncated": False})
                raise ValidationError(error)

        watermark = _source_watermark(rows_by_asset)
        last = self._last_run(workspace_id, projection.id)
        if not force and last is not None and last.get("watermark") == watermark \
                and last.get("status") == "succeeded":
            # a skipped run is still a run: it gets a run record so freshness
            # and operators do not see an unexplained gap (P1-9)
            run_id = f"prj_{self._ctx.id_factory()}"
            started_at = self._ctx.clock()
            summary = {"projection": projection.id, "run_id": run_id,
                       "watermark": watermark, "skipped": True,
                       "objects_upserted": 0, "links_asserted": 0,
                       "objects_deleted": 0, "changes": 0,
                       "truncated": truncated}
            summary.update(self._record_run(
                workspace_id, projection.id, actor, run_id, watermark,
                started_at, status="skipped", force=force, summary=summary,
                object_refs=(last or {}).get("object_refs") or []))
            return summary

        run_id = f"prj_{self._ctx.id_factory()}"
        started_at = self._ctx.clock()
        upserts = tuple(self._object_upserts(projection, rows_by_asset))
        # deletion propagation (1.6): objects this projection materialized
        # last time but that no longer exist upstream must disappear too.
        # Deletes travel inside the same SemanticDelta as the upserts so the
        # gateway has one logical unit to validate and journal (P1-10).
        deleted_refs = sorted(
            set((last or {}).get("object_refs") or [])
            - {f"{u.type}/{u.id}" for u in upserts}
        )
        delta = SemanticDelta(
            upserts=upserts,
            links=tuple(self._links(projection, rows_by_asset, relations)),
            deletes=tuple(deleted_refs),
        )

        gateway = self._ctx.gateway_for(workspace_id)
        summary: dict = {
            "projection": projection.id,
            "run_id": run_id,
            "watermark": watermark,
            "objects_upserted": len(delta.upserts),
            "links_asserted": len(delta.links),
            "truncated": truncated,
        }
        snapshots = {
            ref: gateway.get_object(ref) for ref in deleted_refs
        }
        changes: list[dict] = []
        try:
            changes.extend(gateway.apply_delta(delta))
            summary["objects_deleted"] = len(deleted_refs)
        except Exception as exc:
            self._compensate_projection(gateway, snapshots, changes)
            self._record_run(workspace_id, projection.id, actor, run_id,
                             watermark, started_at, status="failed",
                             error=str(exc), force=force,
                             summary={"projection": projection.id,
                                      "truncated": truncated})
            raise
        summary["changes"] = len(changes)
        summary.update(self._record_run(
            workspace_id, projection.id, actor, run_id, watermark,
            started_at, status="succeeded", force=force, summary=summary,
            object_refs=[f"{u.type}/{u.id}" for u in delta.upserts]))
        self._ctx.audit_event(actor, workspace_id, "data.run_projection",
                              projection.id, summary)
        return summary

    def _compensate_projection(self, gateway, snapshots: dict[str, dict | None],
                               changes: list[dict]) -> None:
        """Best-effort restore of objects deleted before a failed upsert."""
        restore = [
            ObjectUpsert(type=obj["type"], id=str(obj["id"]),
                         properties=dict(obj.get("properties") or {}))
            for obj in snapshots.values() if obj
        ]
        if not restore:
            return
        try:
            gateway.apply_delta(SemanticDelta(upserts=tuple(restore)),
                                source="projection-compensation")
        except Exception:
            pass

    def _last_run(self, workspace_id: str, projection_id: str) -> dict | None:
        runs = [
            r for r in self._ctx.repo.list("projection_run", workspace_id)
            if r.get("projection_id") == projection_id
        ]
        return runs[-1] if runs else None

    def _record_run(self, workspace_id: str, projection_id: str, actor: str,
                    run_id: str, watermark: str, started_at: str, *,
                    status: str, error: str | None = None,
                    force: bool = False, summary: dict | None = None,
                    object_refs: list[str] | None = None) -> dict:
        from .events import EventLog

        run = {
            "id": run_id,
            "workspace_id": workspace_id,
            "projection_id": projection_id,
            "actor": actor,
            "status": status,
            "watermark": watermark,
            "force": force,
            "error": error,
            "object_refs": sorted(object_refs or []),
            "started_at": started_at,
            "finished_at": self._ctx.clock(),
            "checked_at": self._ctx.clock(),
            "summary": summary or {},
        }
        with self._ctx.transaction():
            self._ctx.save("projection_run", run)
            EventLog(self._ctx).emit(
                "projection.run_completed", workspace_id, actor,
                "projection", projection_id,
                payload={"run_id": run_id, "status": status,
                         "watermark": watermark, "error": error},
            )
        return {"run_status": status, "finished_at": run["finished_at"]}

    # ------------------------------------------------------------------

    @staticmethod
    def _match(row: dict, row_filter: dict) -> bool:
        return all(row.get(k) == v for k, v in row_filter.items())

    def _object_upserts(self, projection: Projection, rows_by_asset: dict):
        for mapping in projection.object_mappings:
            for row in rows_by_asset.get(mapping.asset, []):
                if not self._match(row, mapping.filter):
                    continue
                object_id = row.get(mapping.id_field)
                if object_id is None or object_id == "":
                    if mapping.exclude_missing_id:
                        continue
                    raise ValidationError(
                        f"object mapping {mapping.object_type!r}: row missing "
                        f"id field {mapping.id_field!r}"
                    )
                properties = {
                    fm.property: row[fm.source_field]
                    for fm in mapping.fields
                    if fm.source_field in row
                }
                yield ObjectUpsert(
                    type=mapping.object_type, id=str(object_id), properties=properties
                )

    def _links(self, projection: Projection, rows_by_asset: dict, relations: dict):
        for mapping in projection.relation_mappings:
            rdef = relations.get(mapping.relation) or {}
            source_type = rdef.get("source", "?")
            target_type = rdef.get("target", "?")
            for row in rows_by_asset.get(mapping.asset, []):
                if not self._match(row, mapping.filter):
                    continue
                source_id = row.get(mapping.source_field)
                target_id = row.get(mapping.target_field)
                if source_id in (None, "") or target_id in (None, ""):
                    continue
                yield LinkAssert(
                    relation=mapping.relation,
                    source=f"{source_type}/{source_id}",
                    target=f"{target_type}/{target_id}",
                )


class ListDataSources:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("datasource", workspace_id)


class GetProjectionRun:
    """Read one recorded projection run so the ``run_id`` from a sync call can
    be checked later without keeping the HTTP response."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, projection_id: str, run_id: str,
                 actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        self._ctx.must_get("projection", workspace_id, projection_id)
        run = self._ctx.must_get("projection_run", workspace_id, run_id)
        if run.get("projection_id") != projection_id:
            raise NotFoundError(f"projection run not found: {run_id!r}")
        return run


class ListProjections:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("projection", workspace_id)
