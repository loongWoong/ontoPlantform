"""Ontology use cases + the shared SchemaPublisher.

SchemaPublisher is the single place where Design Time becomes Run Time
(design.txt §9/§20): it compiles the workspace's published ontology plus
all published capabilities and hands the payload to the RuntimeGateway.
Both PublishOntology and PublishCapability go through it, so the kernel
schema is always the exact composition of published platform assets.
"""

from __future__ import annotations

from ..domain.governance.model import transition
from ..domain.ontology.compiler import OntologyCompiler
from ..domain.ontology.model import (
    PROFILES,
    Ontology,
    ObjectTypeSpec,
    PropertySpec,
    RelationSpec,
    VocabularyTerm,
)
from ..domain.ontology.validation import validate_ontology
from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext


def _active_ontology_item(ctx: ApplicationContext, workspace_id: str) -> dict | None:
    """Pick the ontology version currently being edited/deployed.

    Revisions create a new draft document and deprecate the old one
    (plan-v2 2.5), so "the" workspace ontology is: newest non-deprecated
    version, and among equal versions a draft beats a published document.
    """
    items = ctx.repo.list("ontology", workspace_id)
    if not items:
        return None

    def rank(data: dict) -> tuple:
        return (
            data.get("lifecycle") != "deprecated",
            int(data.get("version") or 0),
            data.get("lifecycle") == "draft",
        )

    return max(items, key=rank)


def _load_workspace_ontology(ctx: ApplicationContext, workspace_id: str,
                             required: bool = True) -> Ontology | None:
    """The active ontology of the workspace (highest non-deprecated version)."""
    item = _active_ontology_item(ctx, workspace_id)
    if item is None:
        if required:
            raise NotFoundError(
                f"workspace {workspace_id!r} has no ontology yet; create one first"
            )
        return None
    return Ontology.from_dict(item)


def _load_versioned_ontology(
    ctx: ApplicationContext, workspace_id: str
) -> tuple[Ontology, int]:
    """Load the active workspace ontology plus its aggregate version (CAS)."""
    item = _active_ontology_item(ctx, workspace_id)
    if item is None:
        raise NotFoundError(
            f"workspace {workspace_id!r} has no ontology yet; create one first"
        )
    data, version = ctx.load_versioned("ontology", workspace_id, item["id"])
    return Ontology.from_dict(data), version


def _last_published_ontology(ctx: ApplicationContext, workspace_id: str,
                             exclude_id: str) -> Ontology | None:
    """Newest previously published version (for the narrowing matrix)."""
    candidates = [
        d for d in ctx.repo.list("ontology", workspace_id)
        if d["id"] != exclude_id and d.get("lifecycle") in ("published", "deprecated")
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda d: int(d.get("version") or 0))
    return Ontology.from_dict(latest)


def _state_violations(ctx: ApplicationContext, workspace_id: str,
                      ontology: Ontology) -> list[dict]:
    """ABox compliance against a *prospective* schema (plan-v2 O-8).

    Existing runtime objects must still satisfy the new TBox: required
    properties, value types and enum narrowing.  Removed object types whose
    runtime still holds instances are reported too - otherwise the publish
    would silently strand state nobody can query.
    """
    from ..domain.values import is_datetime

    gateway = ctx.gateway_for(workspace_id)
    violations: list[dict] = []

    def check_object(name: str, spec, obj: dict) -> list[str]:
        problems: list[str] = []
        props = obj.get("properties") or {}
        for prop in spec.properties.values():
            if prop.required and prop.name not in props:
                problems.append(f"required property {prop.name!r} is missing")
            if prop.name not in props:
                continue
            value = props[prop.name]
            if prop.enum and value not in prop.enum:
                problems.append(
                    f"{prop.name}={value!r} is outside enum {prop.enum}")
            if prop.type == "datetime" and not is_datetime(value):
                problems.append(f"{prop.name}={value!r} is not a datetime")
            if prop.type in ("number", "integer"):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    problems.append(f"{prop.name}={value!r} is not numeric")
                else:
                    if prop.minimum is not None and value < prop.minimum:
                        problems.append(f"{prop.name}={value!r} < {prop.minimum}")
                    if prop.maximum is not None and value > prop.maximum:
                        problems.append(f"{prop.name}={value!r} > {prop.maximum}")
        return problems

    for name, spec in ontology.objects.items():
        try:
            objects = gateway.find_objects(name, report=True)["objects"]
        except Exception:  # type not materialized yet in this runtime
            continue
        for obj in objects:
            problems = check_object(name, spec, obj)
            if problems:
                violations.append({"ref": f"{name}/{obj['id']}",
                                   "problems": problems[:5]})
                if len(violations) >= 5:
                    return violations

    previous = _last_published_ontology(
        ctx, workspace_id, ontology.id)
    if previous is not None:
        for removed in set(previous.objects) - set(ontology.objects):
            try:
                count = len(gateway.find_objects(removed, report=True)["objects"])
            except Exception:
                count = 0
            if count:
                violations.append({
                    "ref": f"{removed}/*",
                    "problems": [f"object type removed while runtime still "
                                 f"holds {count} instance(s)"],
                })
    return violations



def _property_references(ctx: ApplicationContext, workspace_id: str,
                        ontology: Ontology, object_name: str,
                        removed: set[str]) -> list[str]:
    """Editable assets that still reference dropping properties.

    A property cannot silently disappear from the schema while the aggregate
    (identity / state machine / vocabulary) or a sibling asset (projection,
    application view/query) still consumes it; the publish would otherwise
    install a runtime that cannot satisfy those references.  Returns
    human-readable reasons, empty when the deletion is safe.
    """
    previous = ontology.objects[object_name]
    reasons: list[str] = []

    identity = [prop for prop in previous.identity if prop in removed]
    if identity:
        reasons.append(f"identity fields {identity}")

    state_field = (previous.state_machine or {}).get("field")
    if state_field in removed:
        reasons.append(f"state machine field {state_field!r}")

    terms = sorted(
        term for term, item in ontology.vocabulary.items()
        if item.kind == "property"
        and item.concept.startswith(object_name + ".")
        and item.concept[len(object_name) + 1:] in removed)
    if terms:
        reasons.append(f"vocabulary terms {terms}")

    projections = sorted(
        p.get("name") or p["id"]
        for p in ctx.repo.list("projection", workspace_id)
        if any(
            mapping.get("object_type") == object_name
            and any(field.get("property") in removed
                    for field in mapping.get("fields") or [])
            for mapping in p.get("object_mappings") or []))
    if projections:
        reasons.append(f"projections {projections}")

    applications = sorted(
        app.get("name") or app["id"]
        for app in ctx.repo.list("application", workspace_id)
        if any(
            query.get("object_type") == object_name
            and any(str(key).split("__")[0] in removed
                    for key in (query.get("filters") or {}))
            for query in (app.get("queries") or {}).values())
        or any(
            view.get("object_type") == object_name
            and any(prop in removed for prop in view.get("properties") or [])
            for view in (app.get("views") or {}).values()))
    if applications:
        reasons.append(f"applications {applications}")
    return reasons


class CreateOntology:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, name: str, actor: str,
                 description: str = "", profile: str = "closed") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        existing = self._ctx.repo.list("ontology", workspace_id)
        if existing:
            raise ValidationError(
                "this workspace already has an ontology; v0.1 supports one "
                "ontology per workspace"
            )
        ontology = Ontology(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=name.strip() or "untitled",
            description=description,
            profile=profile,
            created_at=self._ctx.clock(),
            updated_at=self._ctx.clock(),
        )
        self._ctx.save("ontology", ontology.to_dict())
        self._ctx.audit_event(actor, workspace_id, "ontology.create", ontology.id,
                              {"name": ontology.name, "profile": ontology.profile})
        return ontology.to_dict()


class GetOntology:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        return ontology.to_dict()


class UpdateOntologyMeta:
    """Edit the display metadata (name/description/profile) of a draft.

    The published/validated artifact is immutable, so this only accepts the
    active *draft*: a published ontology can only change through a revision.
    The write is a normal versioned aggregate update (optimistic lock) and is
    audited as ``ontology.update_meta``.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str,
                 name: str | None = None, description: str | None = None,
                 profile: str | None = None,
                 expected_version: int | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        if ontology.lifecycle != "draft":
            raise ValidationError(
                "only a draft ontology can be edited; the active version is "
                f"{ontology.lifecycle!r} - create a revision first")
        if name is None and description is None and profile is None:
            raise ValidationError(
                "nothing to update: provide at least one of name, description "
                "or profile")
        if name is not None:
            stripped = str(name).strip()
            if not stripped:
                raise ValidationError("ontology name must not be empty")
            ontology.name = stripped
        if description is not None:
            ontology.description = str(description)
        if profile is not None:
            normalized = str(profile).strip().lower()
            if normalized not in PROFILES:
                raise ValidationError(
                    f"unknown ontology profile {profile!r}; expected "
                    f"{list(PROFILES)}")
            ontology.profile = normalized
        ontology.updated_at = self._ctx.clock()
        self._ctx.save_versioned(
            "ontology", ontology.to_dict(),
            expected_version if expected_version is not None else version,
        )
        self._ctx.audit_event(
            actor, workspace_id, "ontology.update_meta", ontology.id,
            {"name": ontology.name, "profile": ontology.profile})
        return ontology.to_dict()


class UpsertObjectType:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str,
                 properties: list[dict] | dict, description: str = "",
                 identity: list[str] | None = None,
                 category: str = "object",
                 state_machine: dict | None = None,
                 expected_version: int | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        props = (
            [PropertySpec.from_dict(p) for p in properties.values()]
            if isinstance(properties, dict)
            else [PropertySpec.from_dict(p) for p in properties]
        )
        spec = ObjectTypeSpec(
            name=name,
            description=description,
            properties={p.name: p for p in props},
            identity=list(identity or []),
            category=category,
            state_machine=state_machine,
        )
        # Dropping a property is a schema narrowing; refuse it while the
        # aggregate or a sibling asset still references that property.
        previous = ontology.objects.get(name)
        if previous is not None and ontology.lifecycle != "published":
            dropped = set(previous.properties) - set(spec.properties)
            if dropped:
                reasons = _property_references(
                    self._ctx, workspace_id, ontology, name, dropped)
                if reasons:
                    raise ValidationError(
                        f"object type {name!r} cannot drop properties "
                        f"{sorted(dropped)}: still referenced by "
                        + "; ".join(reasons)
                        + "; remove those references first")
        ontology.upsert_object(spec)
        ontology.updated_at = self._ctx.clock()
        self._ctx.save_versioned(
            "ontology", ontology.to_dict(),
            expected_version if expected_version is not None else version,
        )
        self._ctx.audit_event(actor, workspace_id, "ontology.upsert_object", name,
                              {"properties": sorted(spec.properties),
                               "identity": list(spec.identity)})
        return ontology.to_dict()


class UpsertRelation:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str, source: str,
                 target: str, description: str = "", cardinality: str = "",
                 inverse: str = "",
                 expected_version: int | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        ontology.upsert_relation(
            RelationSpec(name=name, source=source, target=target,
                         description=description, cardinality=cardinality,
                         inverse=inverse)
        )
        ontology.updated_at = self._ctx.clock()
        self._ctx.save_versioned(
            "ontology", ontology.to_dict(),
            expected_version if expected_version is not None else version,
        )
        self._ctx.audit_event(actor, workspace_id, "ontology.upsert_relation", name,
                              {"source": source, "target": target,
                               "cardinality": cardinality})
        return ontology.to_dict()


class AddVocabularyTerm:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, term: str, concept: str,
                 kind: str = "object", aliases: list[str] | None = None,
                 expected_version: int | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        ontology.add_term(
            VocabularyTerm(term=term, concept=concept, kind=kind, aliases=aliases or [])
        )
        ontology.updated_at = self._ctx.clock()
        self._ctx.save_versioned(
            "ontology", ontology.to_dict(),
            expected_version if expected_version is not None else version,
        )
        self._ctx.audit_event(actor, workspace_id, "ontology.add_term", term,
                              {"concept": concept})
        return ontology.to_dict()


def _app_rows_using(ctx: ApplicationContext, workspace_id: str,
                    predicate) -> list[dict]:
    return [app for app in ctx.repo.list("application", workspace_id)
            if predicate(app)]


class RemoveObjectType:
    """Remove one object type from the editable ontology draft.

    Deleting a type is a schema edit, not a data delete: runtime instances of
    that type may still exist and are deliberately left untouched.  The use
    case refuses while other editable assets still reference the type, so a
    delete cannot leave capabilities/projections/applications dangling.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        if name not in ontology.objects:
            raise NotFoundError(f"object type not found: {name!r}")

        capabilities = [
            c.get("name") or c.get("key") or c["id"]
            for c in self._ctx.repo.list("capability", workspace_id)
            if c.get("target_type") == name
        ]
        if capabilities:
            raise ValidationError(
                f"object type {name!r} is used by capabilities {capabilities}; "
                "delete or retarget them first")
        projections = [
            p.get("name") or p["id"]
            for p in self._ctx.repo.list("projection", workspace_id)
            if any(m.get("object_type") == name
                   for m in p.get("object_mappings") or [])
        ]
        if projections:
            raise ValidationError(
                f"object type {name!r} is used by projections {projections}; "
                "delete or update them first")
        applications = [
            app.get("name") or app["id"]
            for app in _app_rows_using(
                self._ctx, workspace_id,
                lambda app: any(v.get("object_type") == name
                                for v in (app.get("views") or {}).values())
                or any(q.get("object_type") == name
                       for q in (app.get("queries") or {}).values()))
        ]
        if applications:
            raise ValidationError(
                f"object type {name!r} is used by applications {applications}; "
                "remove those views/queries first")
        terms = [
            term for term, item in ontology.vocabulary.items()
            if item.concept == name or item.concept.startswith(name + ".")
        ]
        if terms:
            raise ValidationError(
                f"object type {name!r} is used by vocabulary terms {terms}; "
                "remove them first")

        ontology.remove_object(name)  # also guards relations inside the aggregate
        ontology.updated_at = self._ctx.clock()
        self._ctx.save_versioned("ontology", ontology.to_dict(), version)
        self._ctx.audit_event(actor, workspace_id, "ontology.remove_object", name, {})
        return ontology.to_dict()


class RemoveRelation:
    """Remove one relation type while no editable asset references it."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        if name not in ontology.relations:
            raise NotFoundError(f"relation not found: {name!r}")

        projections = [
            p.get("name") or p["id"]
            for p in self._ctx.repo.list("projection", workspace_id)
            if any(m.get("relation") == name
                   for m in p.get("relation_mappings") or [])
        ]
        if projections:
            raise ValidationError(
                f"relation {name!r} is used by projections {projections}; "
                "delete or update them first")
        applications = [
            app.get("name") or app["id"]
            for app in _app_rows_using(
                self._ctx, workspace_id,
                lambda app: any(
                    entry.get("relation") == name
                    for q in (app.get("queries") or {}).values()
                    for entry in q.get("expand") or []))
        ]
        if applications:
            raise ValidationError(
                f"relation {name!r} is used by applications {applications}; "
                "remove those query expansions first")
        terms = [
            term for term, item in ontology.vocabulary.items()
            if item.kind == "relation" and item.concept == name
        ]
        if terms:
            raise ValidationError(
                f"relation {name!r} is used by vocabulary terms {terms}; "
                "remove them first")

        ontology.remove_relation(name)
        ontology.updated_at = self._ctx.clock()
        self._ctx.save_versioned("ontology", ontology.to_dict(), version)
        self._ctx.audit_event(actor, workspace_id, "ontology.remove_relation", name, {})
        return ontology.to_dict()


class RemoveVocabularyTerm:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, term: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        ontology.remove_term(term)
        ontology.updated_at = self._ctx.clock()
        self._ctx.save_versioned("ontology", ontology.to_dict(), version)
        self._ctx.audit_event(actor, workspace_id, "ontology.remove_term", term, {})
        return ontology.to_dict()


class ValidateOntology:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str,
                 expected_version: int | None = None) -> list[str]:
        """Run the publish precondition and persist the validated lifecycle.

        v0.2 keeps "validate" and "publish" as two explicit steps, but both
        use the exact same rule set (`Ontology.assert_publishable`).
        """
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        problems = validate_ontology(ontology)
        if not problems and ontology.lifecycle == "draft":
            ontology.lifecycle = transition(ontology.lifecycle, "validate")
            ontology.updated_at = self._ctx.clock()
            self._ctx.save_versioned(
                "ontology", ontology.to_dict(),
                expected_version if expected_version is not None else version,
            )
        return problems


class PublishOntology:
    def __init__(self, ctx: ApplicationContext, publisher: "SchemaPublisher") -> None:
        self._ctx = ctx
        self._publisher = publisher

    def __call__(self, workspace_id: str, actor: str,
                 expected_version: int | None = None,
                 allow_breaking: bool = False) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        # one gate, one rule set: fail before any lifecycle or runtime change
        ontology.assert_publishable()
        self._assert_compatible(workspace_id, ontology)
        violations = _state_violations(self._ctx, workspace_id, ontology)
        if violations:
            samples = "; ".join(
                f"{v['ref']}: {', '.join(v['problems'])}" for v in violations)
            if not allow_breaking:
                raise ValidationError(
                    "existing runtime state does not satisfy the new schema "
                    f"(pass allow_breaking to override): {samples}")
            self._ctx.audit_event(
                actor, workspace_id, "ontology.publish_breaking", ontology.id,
                {"samples": [v["ref"] for v in violations],
                 "problems": [p for v in violations for p in v["problems"]][:10]})
        if ontology.lifecycle == "draft":
            ontology.lifecycle = transition(ontology.lifecycle, "validate")
        ontology.lifecycle = transition(ontology.lifecycle, "publish")
        # the published artifact records the schema version it was compiled from
        ontology.schema_version = ontology.version
        ontology.updated_at = self._ctx.clock()
        describe = self._publisher.publish(
            workspace_id, actor, action="ontology.publish", ontology=ontology
        )
        from .events import EventLog
        from .releases import schema_fingerprint

        # the aggregate records the stable fingerprint of the exact schema
        # the runtime accepted, so a deployment can be checked without
        # trusting version counters (plan-v2 2.5)
        payload = self._ctx.repo.get("schema_payload", workspace_id,
                                     workspace_id) or {}
        if payload.get("schema"):
            ontology.schema_fingerprint = schema_fingerprint(payload["schema"])
        from .releases import cut_release, deploy_release

        # aggregate + release + outbox event commit atomically; the runtime
        # pointer only moves after the kernel accepted the payload (1.1/1.2/1.3)
        with self._ctx.transaction():
            self._ctx.save_versioned(
                "ontology", ontology.to_dict(),
                expected_version if expected_version is not None else version,
            )
            release = cut_release(
                self._ctx, workspace_id, actor, note="ontology.publish")
            events = EventLog(self._ctx)
            events.emit(
                "ontology.published", workspace_id, actor, "ontology", ontology.id,
                payload={"schema_version": ontology.schema_version},
                aggregate_version=expected_version or version,
            )
            events.emit(
                "release.cut", workspace_id, actor, "release", release["id"],
                payload={"schema_version": release.get("schema_version")},
            )
        deployed = deploy_release(self._ctx, workspace_id, actor, release["id"])
        return {
            "ontology": ontology.to_dict(),
            "runtime": describe,
            "release": release,
            "deployment": deployed["deployment"],
        }

    def _assert_compatible(self, workspace_id: str, ontology: Ontology) -> None:
        """Narrowing check between revisions (C10): widening may publish,
        narrowing needs a migration and is rejected here."""
        from ..domain.ontology.compat import check_compatible

        previous = _last_published_ontology(self._ctx, workspace_id, ontology.id)
        if previous is None or previous.version >= ontology.version:
            return
        problems = check_compatible(previous.to_dict(), ontology.to_dict())
        if problems:
            raise ValidationError(
                f"ontology revision v{ontology.version} narrows v{previous.version} "
                "and cannot be published without a migration: " + " | ".join(problems))


class ReviseOntology:
    """Start a new draft version of the published ontology (plan-v2 2.5).

    The published aggregate is immutable, so a revision copies it into a new
    document (version+1, schema_version+1, draft) and deprecates the old one.
    Publishing the draft runs the narrowing matrix before any runtime change.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology, version = _load_versioned_ontology(self._ctx, workspace_id)
        if ontology.lifecycle != "published":
            raise ValidationError(
                "only a published ontology can be revised; publish it first")
        data = ontology.to_dict()
        new_id = self._ctx.id_factory()
        data.update({
            "id": new_id,
            "version": ontology.version + 1,
            "schema_version": ontology.schema_version + 1,
            "parent_version": ontology.version,
            "schema_fingerprint": "",
            "lifecycle": "draft",
            "created_at": self._ctx.clock(),
            "updated_at": self._ctx.clock(),
        })
        from .events import EventLog

        with self._ctx.transaction():
            ontology.lifecycle = transition(ontology.lifecycle, "deprecate")
            ontology.updated_at = self._ctx.clock()
            self._ctx.save_versioned("ontology", ontology.to_dict(), version)
            self._ctx.save("ontology", data)
            EventLog(self._ctx).emit(
                "ontology.revised", workspace_id, actor, "ontology", new_id,
                payload={"version": data["version"],
                         "previous_version": ontology.version},
            )
        self._ctx.audit_event(actor, workspace_id, "ontology.revise", new_id,
                              {"version": data["version"],
                               "previous": ontology.id})
        return data


class DiscardOntologyDraft:
    """Drop the active revision draft and restore the published version.

    Revision deprecates the served version and creates a draft copy; discarding
    reverses exactly that: the deprecated parent row is transitioned back to
    ``published`` and the draft row is deleted in one transaction.  Neither the
    runtime nor the deployment pointer is touched - the served Release keeps
    running the restored version's schema.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        item = _active_ontology_item(self._ctx, workspace_id)
        if item is None:
            raise NotFoundError(
                f"workspace {workspace_id!r} has no ontology yet; create one first"
            )
        if item.get("lifecycle") != "draft":
            raise ValidationError(
                "only a draft ontology can be discarded; the active version "
                f"is {item.get('lifecycle')!r}")
        parent_version = item.get("parent_version")
        if parent_version is None:
            raise ValidationError(
                "cannot discard this ontology draft: it was not created by a "
                "revision, so there is no parent version to restore")
        candidates = [
            row for row in self._ctx.repo.list("ontology", workspace_id)
            if row["id"] != item["id"]
            and int(row.get("version") or 0) == int(parent_version)
            and row.get("lifecycle") in ("published", "deprecated")
        ]
        if not candidates:
            raise ValidationError(
                f"cannot discard this ontology draft: parent version "
                f"v{parent_version} was not found")
        restored_row = max(candidates,
                           key=lambda row: row.get("created_at") or "")
        restored, restored_version = self._ctx.load_versioned(
            "ontology", workspace_id, restored_row["id"])
        if restored.get("lifecycle") == "deprecated":
            restored["lifecycle"] = transition(restored["lifecycle"], "restore")
        restored["updated_at"] = self._ctx.clock()
        with self._ctx.transaction():
            self._ctx.save_versioned("ontology", restored, restored_version)
            self._ctx.repo.delete("ontology", workspace_id, item["id"])
            self._ctx.audit_event(
                actor, workspace_id, "ontology.discard_draft", item["id"],
                {"restored_id": restored["id"],
                 "restored_version": restored.get("version")})
        return {"discarded": item["id"], "restored": restored}


class LintOntology:
    """Modeling lint (plan-v2 O-6/O-7): warnings do not block publishing."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        from ..domain.ontology.validation import lint_ontology

        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        warnings = lint_ontology(ontology)
        return {"ok": not warnings, "warnings": warnings,
                "ontology_id": ontology.id, "version": ontology.version}


class SchemaPublisher:
    """Compile published platform assets -> kernel schema (one door)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def publish(self, workspace_id: str, actor: str, action: str,
                ontology: Ontology | None = None,
                capability_overrides: list[dict] | None = None) -> dict:
        if ontology is None:
            ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        if ontology.lifecycle != "published":
            raise ValidationError(
                "the workspace ontology is not published; publish it first"
            )
        if capability_overrides is not None:
            capability_rows = list(capability_overrides)
        else:
            capability_rows = [
                c
                for c in self._ctx.repo.list("capability", workspace_id)
                if c.get("lifecycle") == "published"
            ]
        from ..domain.capability.model import Capability

        capabilities = [Capability.from_dict(c) for c in capability_rows]
        payload = OntologyCompiler().compile(ontology, capabilities)
        gateway = self._ctx.gateway_for(workspace_id)
        # publish first; only a successfully compiled+accepted payload is
        # persisted for cross-process self-healing (see gateway_for)
        gateway.publish_schema(payload)
        self._register_agent_runners(workspace_id, gateway, capability_rows, ontology)
        # persist the compiled payload so a new process can rebuild the
        # runtime without a re-publish (see ApplicationContext.gateway_for)
        self._ctx.save("schema_payload", {
            "id": workspace_id,
            "workspace_id": workspace_id,
            "schema": payload.schema,
            "bindings": payload.bindings,
            "workflows": payload.workflows,
        })
        self._ctx.audit_event(
            actor, workspace_id, action, ontology.id,
            {
                "objects": sorted(ontology.objects),
                "relations": sorted(ontology.relations),
                "functions": sorted(payload.schema.get("functions", {})),
                "schema_version": ontology.schema_version,
            },
        )
        return gateway.describe()

    def _register_agent_runners(self, workspace_id: str, gateway,
                                capabilities: list[dict],
                                ontology: Ontology | None = None) -> None:
        """Agent-kind capabilities need a live runner (goal + LLM provider);
        it cannot live in the compiled payload, so it is attached here."""
        from .agent_runner import make_agent_runner, make_audited_invoker
        from ..domain.intelligence.model import SemanticIndex

        if ontology is None:
            ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        context_text = SemanticIndex.from_ontology(ontology).context_text(ontology)
        for data in capabilities:
            impl = data.get("implementation", {})
            if impl.get("kind") != "agent":
                continue
            function_name = f"{data['target_type']}.{data['key']}"
            gateway.register_agent_runner(
                f"agent://{data['id']}",
                make_agent_runner(
                    gateway,
                    lambda: self._ctx.llm,
                    context_text,
                    impl.get("goal", ""),
                    function_name=function_name,
                    invoker=make_audited_invoker(self._ctx, workspace_id, gateway),
                ),
            )


class DescribeRuntime:
    """Runtime introspection for UIs and agents (index level)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.gateway_for(workspace_id).describe()
