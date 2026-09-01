"""Data use cases: connect reality, project it into the kernel (链二).

    External Data -> DataSource -> Mapping/Projection
                  -> RuntimeGateway -> Kernel Object / Relation
"""

from __future__ import annotations

from ..domain.data.model import (
    DataAsset,
    DataSource,
    ObjectMapping,
    Projection,
    RelationMapping,
    validate_projection,
)
from ..errors import ConnectorError, ValidationError
from ..ports.runtime import LinkAssert, ObjectUpsert, SemanticDelta
from .common import ApplicationContext


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
                 relation_mappings: list[dict] | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
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
    """Source State -> Semantic Delta -> Kernel (one logical run)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, projection_id: str, actor: str) -> dict:
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

        delta = SemanticDelta(
            upserts=tuple(self._object_upserts(projection, rows_by_asset)),
            links=tuple(self._links(projection, rows_by_asset, relations)),
        )

        gateway = self._ctx.gateway_for(workspace_id)
        changes = gateway.apply_delta(delta)
        summary = {
            "projection": projection.id,
            "objects_upserted": len(delta.upserts),
            "links_asserted": len(delta.links),
            "changes": len(changes),
        }
        self._ctx.audit_event(actor, workspace_id, "data.run_projection",
                              projection.id, summary)
        return summary

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


class ListProjections:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("projection", workspace_id)
