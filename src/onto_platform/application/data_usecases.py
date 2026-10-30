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
from .common import (
    ApplicationContext,
    active_ontology_row,
    published_ontology_row,
)


def _source_watermark(rows_by_asset: dict[str, list[dict]]) -> str:
    """Stable content hash of the fetched source rows (the projection v2
    watermark: unchanged source -> unchanged hash -> run is skipped)."""
    canonical = json.dumps(
        {asset: rows for asset, rows in sorted(rows_by_asset.items())},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _source_connector(kind: str, config: dict):
    """Build a connector for ``kind``/``config``, mapping bad input to 400."""
    from ..infrastructure.datasource.registry import connector_for

    try:
        return connector_for(kind, dict(config or {}))
    except ValidationError:
        raise
    except Exception as exc:  # connector configs raise ValueError from __init__
        raise ValidationError(
            f"invalid datasource config for kind {kind!r}: {exc}") from exc


def _assert_connection(connector) -> None:
    """Run the connector's cheap probe; failures never touch the stored doc."""
    try:
        ok = connector.test()
    except Exception as exc:
        raise ConnectorError(f"datasource connection failed: {exc}") from exc
    if ok is False:
        raise ConnectorError(
            "datasource connection failed: connector reported it is not ready")


def _parse_assets(assets) -> dict[str, DataAsset]:
    """Validate the asset list of a datasource update."""
    if not isinstance(assets, list):
        raise ValidationError("datasource assets must be a list")
    parsed: dict[str, DataAsset] = {}
    for item in assets:
        if not isinstance(item, dict):
            raise ValidationError("each datasource asset must be a JSON object")
        asset_name = str(item.get("name") or "").strip()
        if not asset_name:
            raise ValidationError("datasource asset name must not be empty")
        if asset_name in parsed:
            raise ValidationError(
                f"duplicate datasource asset name {asset_name!r}")
        asset_config = item.get("config") or {}
        if not isinstance(asset_config, dict):
            raise ValidationError(
                f"datasource asset {asset_name!r} config must be a JSON object")
        parsed[asset_name] = DataAsset(
            name=asset_name,
            config=dict(asset_config),
            description=str(item.get("description") or ""))
    return parsed


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


class UpdateDataSource:
    """Edit a datasource in place; the kind is immutable.

    Editing keeps the id, so existing projections keep pointing at the same
    source.  An asset may only disappear while no projection of this source
    maps it.  ``test_connection`` defaults to True: the resulting config must
    connect before anything is saved, so a failed edit leaves the old document
    exactly as it was.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, source_id: str,
                 name: str | None = None, config: dict | None = None,
                 assets: list[dict] | None = None, kind: str | None = None,
                 test_connection: bool = True) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data = self._ctx.must_get("datasource", workspace_id, source_id)
        source = DataSource.from_dict(data)
        if kind is not None and str(kind) != source.kind:
            raise ValidationError(
                f"datasource kind cannot be changed (it is {source.kind!r}); "
                "connect a new datasource instead")
        if name is None and config is None and assets is None:
            raise ValidationError(
                "nothing to update: provide at least one of name, config or assets")
        if name is not None:
            name = str(name).strip()
            if not name:
                raise ValidationError("datasource name must not be empty")
            source.name = name
        if config is not None:
            if not isinstance(config, dict):
                raise ValidationError("datasource config must be a JSON object")
            source.config = dict(config)
        if assets is not None:
            source.assets = _parse_assets(assets)
        removed = sorted(set(data.get("assets") or {}) - set(source.assets))
        if removed:
            removed_set = set(removed)
            used_by = []
            for projection in self._ctx.repo.list("projection", workspace_id):
                if projection.get("datasource_id") != source.id:
                    continue
                mapped = {
                    m.get("asset")
                    for m in (projection.get("object_mappings") or [])
                } | {
                    m.get("asset")
                    for m in (projection.get("relation_mappings") or [])
                }
                if removed_set & mapped:
                    used_by.append(projection.get("name") or projection["id"])
            if used_by:
                raise ValidationError(
                    f"cannot remove datasource assets {removed}: still used by "
                    f"projections {used_by}; update or delete those projections "
                    "first")
        connector = _source_connector(source.kind, source.config)
        if test_connection:
            _assert_connection(connector)
        self._ctx.save("datasource", source.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "data.update_source", source.id,
            {"kind": source.kind, "assets": sorted(source.assets),
             "removed_assets": removed})
        return source.to_dict()


class TestDataSource:
    """Probe a stored datasource without changing anything."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, source_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        source = DataSource.from_dict(
            self._ctx.must_get("datasource", workspace_id, source_id))
        _assert_connection(_source_connector(source.kind, source.config))
        return {"ok": True, "kind": source.kind, "tested_at": self._ctx.clock()}


class PreviewDataSource:
    """Fetch one asset for real so the console can show actual rows.

    Preview is read-only: nothing is projected into the kernel and no
    document is written.  ``limit`` defaults to 20 and is clamped to 1..100.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, source_id: str,
                 asset_name: str | None = None,
                 limit: int | None = 20) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        source = DataSource.from_dict(
            self._ctx.must_get("datasource", workspace_id, source_id))
        asset = source.assets.get(str(asset_name or ""))
        if asset is None:
            raise ValidationError(
                f"unknown asset {asset_name!r} in datasource")
        try:
            limit_value = int(limit if limit is not None else 20)
        except (TypeError, ValueError) as exc:
            raise ValidationError("preview limit must be an integer") from exc
        limit_value = max(1, min(100, limit_value))
        connector = _source_connector(source.kind, source.config)
        try:
            rows = connector.fetch(asset.config)
        except Exception as exc:
            raise ConnectorError(
                f"failed to fetch asset {asset_name!r}: {exc}") from exc
        if not isinstance(rows, list):
            raise ConnectorError(
                f"connector for kind {source.kind!r} returned a non-list payload")
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(str(key))
        return {"asset": asset.name, "total": len(rows),
                "columns": columns, "rows": rows[:limit_value]}


def _validate_projection_definition(ctx: ApplicationContext, workspace_id: str,
                                    projection: Projection,
                                    source: DataSource) -> None:
    """Shared validation for creating and editing a projection.

    Kept in one place so ``CreateProjection`` and ``UpdateProjection`` cannot
    drift: editing must reject exactly what creating rejects (unknown asset or
    object type, missing id_field, identity mismatch, unit mismatch, unknown
    relation).
    """
    problems = validate_projection(projection, source)
    if problems:
        raise ValidationError("projection validation failed: " + "; ".join(problems))
    # every mapped object type / relation must exist in the ontology being
    # edited (draft wins); never depend on storage row order across revisions
    ontology = active_ontology_row(ctx, workspace_id)
    ontology_objects = (ontology.get("objects", {}) if ontology else {})
    ontology_relations = (ontology.get("relations", {}) if ontology else {})
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
        _validate_projection_definition(self._ctx, workspace_id, projection, source)
        self._ctx.save("projection", projection.to_dict())
        self._ctx.audit_event(actor, workspace_id, "data.create_projection", projection.id,
                              {"name": name})
        return projection.to_dict()


class UpdateProjection:
    """Edit a projection in place; the datasource binding is immutable.

    Creating a projection validates it against the ontology; editing reuses the
    very same helper so an edit can never accept something creation would
    reject.  Changing the mapping only affects the next run - already projected
    objects are not rolled back.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, projection_id: str,
                 name: str | None = None,
                 object_mappings: list[dict] | None = None,
                 relation_mappings: list[dict] | None = None,
                 mode: str | None = None, row_limit: int | None = None,
                 datasource_id: str | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        projection = Projection.from_dict(
            self._ctx.must_get("projection", workspace_id, projection_id))
        if datasource_id is not None \
                and str(datasource_id) != projection.datasource_id:
            raise ValidationError(
                "projection datasource cannot be changed (it is "
                f"{projection.datasource_id!r}); create a new projection instead")
        if name is None and object_mappings is None and relation_mappings is None \
                and mode is None and row_limit is None:
            raise ValidationError(
                "nothing to update: provide at least one of name, "
                "object_mappings, relation_mappings, mode or row_limit")
        if name is not None:
            name = str(name).strip()
            if not name:
                raise ValidationError("projection name must not be empty")
            projection.name = name
        if mode is not None:
            if mode not in ("additive", "authoritative"):
                raise ValidationError(
                    "projection mode must be 'additive' or 'authoritative'")
            projection.mode = str(mode)
        if row_limit is not None:
            try:
                projection.row_limit = max(0, int(row_limit or 0))
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    "projection row_limit must be an integer") from exc
        if object_mappings is not None:
            if not isinstance(object_mappings, list):
                raise ValidationError("object_mappings must be a list")
            try:
                projection.object_mappings = [
                    ObjectMapping.from_dict(m) for m in object_mappings]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(
                    f"invalid object mapping: {exc}") from exc
        if relation_mappings is not None:
            if not isinstance(relation_mappings, list):
                raise ValidationError("relation_mappings must be a list")
            try:
                projection.relation_mappings = [
                    RelationMapping.from_dict(m) for m in relation_mappings]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(
                    f"invalid relation mapping: {exc}") from exc
        source = DataSource.from_dict(
            self._ctx.must_get("datasource", workspace_id,
                               projection.datasource_id))
        _validate_projection_definition(
            self._ctx, workspace_id, projection, source)
        projection.updated_at = self._ctx.clock()
        self._ctx.save("projection", projection.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "data.update_projection", projection.id,
            {"name": projection.name,
             "datasource_id": projection.datasource_id,
             "mode": projection.mode, "row_limit": projection.row_limit})
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
        # the runtime is serving the published contract; never take relations
        # from an un-published draft (they may not exist in the kernel yet)
        ontology = published_ontology_row(self._ctx, workspace_id)
        if ontology is None:
            raise ValidationError(
                "workspace has no published ontology; publish the ontology "
                "before running a projection")
        relations = ontology.get("relations", {})

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


class DeleteDataSource:
    """Remove a datasource configuration, never the projections using it."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, datasource_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        source = self._ctx.must_get("datasource", workspace_id, datasource_id)
        used_by = [
            p.get("name") or p["id"]
            for p in self._ctx.repo.list("projection", workspace_id)
            if p.get("datasource_id") == datasource_id
        ]
        if used_by:
            raise ValidationError(
                f"datasource {datasource_id!r} is used by projections {used_by}; "
                "delete them first")
        if not self._ctx.repo.delete("datasource", workspace_id, datasource_id):
            raise NotFoundError(f"datasource not found: {datasource_id!r}")
        self._ctx.audit_event(
            actor, workspace_id, "data.delete_source", datasource_id,
            {"name": source.get("name"), "kind": source.get("kind")})
        return {"deleted": True, "id": datasource_id,
                "name": source.get("name", ""), "kind": source.get("kind", "")}


class DeleteProjection:
    """Remove a projection recipe and its run history.

    Runtime objects materialized by earlier runs are intentionally preserved:
    deleting a mapping is a design-time operation, not a business-data delete.
    The audit row records what the last run wrote so the effect is traceable.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, projection_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        projection = self._ctx.must_get("projection", workspace_id, projection_id)
        runs = [
            run for run in self._ctx.repo.list("projection_run", workspace_id)
            if run.get("projection_id") == projection_id
        ]
        last = runs[-1] if runs else {}
        with self._ctx.transaction():
            self._ctx.repo.delete("projection", workspace_id, projection_id)
            for run in runs:
                self._ctx.repo.delete("projection_run", workspace_id, run["id"])
        self._ctx.audit_event(
            actor, workspace_id, "data.delete_projection", projection_id,
            {
                "name": projection.get("name"),
                "datasource_id": projection.get("datasource_id"),
                "removed_runs": len(runs),
                "last_run_id": last.get("id"),
                "last_run_upserted": last.get("objects_upserted"),
                "runtime_objects_retained": True,
            })
        return {
            "deleted": True,
            "id": projection_id,
            "name": projection.get("name", ""),
            "removed_runs": len(runs),
            "runtime_objects_retained": True,
        }
