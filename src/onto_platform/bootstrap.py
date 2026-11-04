"""Composition root: build a fully wired OntoPlatform.

    build_platform(config)
        ├── repositories (memory | sqlite)
        ├── audit log
        ├── code store (capability source files)
        ├── gateway provider (one kernel runtime per workspace)
        └── use case groups

This is the only module that knows how the pieces fit together; the
kernel itself is imported lazily by the gateway when first needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .application.application_usecases import (
    AddAgent,
    AddQuery,
    AddView,
    AttachCapability,
    AttachUiArtifact,
    CloneApplication,
    CreateApplication,
    DeleteApplication,
    DetachCapability,
    GetApplication,
    ListApplications,
    PublishApplication,
    RemoveAgent,
    RemoveQuery,
    RemoveView,
    RunAppAgent,
    RunAppQuery,
    UpdateApplication,
    UpdateQuery,
    UpdateView,
)
from .application.capability_usecases import (
    CloneCapability,
    CreateCapability,
    DeleteCapability,
    InvokeCapability,
    ListCapabilities,
    PublishCapability,
    UpdateCapability,
)
from .application.common import ApplicationContext
from .application.data_usecases import (
    ConnectDataSource,
    CreateProjection,
    DeleteDataSource,
    DeleteProjection,
    GetProjectionRun,
    ListDataSources,
    ListProjections,
    PreviewDataSource,
    RunProjection,
    TestDataSource,
    UpdateDataSource,
    UpdateProjection,
)
from .application.execution_ledger import ExecutionLedger
from .application.controller import DriftController
from .application.scheduler import Scheduler
from .application.events import Dispatcher, EventLog
from .application.history import StateHistory
from .application.intelligence_usecases import AskOntology, RunAgent
from .application.llm_config import LlmConfig
from .application.metrics import WorkspaceMetrics
from .application.objective_usecases import ObjectiveUseCases
from .application.slo import ServiceLevels
from .application.ontology_draft import DraftOntologyFromText
from .application.ontology_usecases import (
    AddVocabularyTerm,
    CreateOntology,
    DescribeRuntime,
    DiscardOntologyDraft,
    GetOntology,
    LintOntology,
    PublishOntology,
    RemoveObjectType,
    RemoveRelation,
    RemoveVocabularyTerm,
    ReviseOntology,
    SchemaPublisher,
    UpdateOntologyMeta,
    UpsertObjectType,
    UpsertRelation,
    ValidateOntology,
)
from .application.releases import ReleaseUseCases
from .application.security import SecurityService, SecurityUseCases
from .application.security import context as auth_context
from .application.security import (
    AsyncTaskService,
    ExportService,
    IdentityDirectory,
    IdentityService,
    LDAPAuthenticator,
    OIDCVerifier,
    OrganizationService,
    PolicyDraftService,
    SCIMService,
    SessionIssuer,
    WebhookService,
)
from .application.smartui.builtin import build_default_host
from .application.smartui.host import PluginHost
from .application.state_usecases import StateUseCases
from .application.verifier import (
    CapabilityVerifier,
    Reconciler,
    RuntimeVerifier,
)
from .application.workspace_usecases import (
    AddMember,
    CreateWorkspace,
    DeleteWorkspace,
    GetWorkspace,
    ListMembers,
    ListWorkspaces,
    RemoveMember,
    ResetWorkspace,
)
from .infrastructure.persistence.file_code_store import FileCodeStore
from .infrastructure.persistence.memory_repo import InMemoryAuditLog, InMemoryRepository
from .infrastructure.persistence.scoped_repo import GovernedRepository
from .infrastructure.persistence.sqlite_repo import SqliteAuditLog, SqliteRepository
from .infrastructure.runtime.provider import WorkspaceGatewayProvider
from .ports.llm import LLMProvider
from .errors import ValidationError


@dataclass
class PlatformConfig:
    """Deployment knobs - everything else is architecture."""

    store: str = "memory"                 # platform definitions: memory | sqlite
    db_path: str | None = None            # required when store == sqlite
    runtime_store: str | None = None      # kernel state: memory | sqlite;
                                          # None -> follows ``store`` so that a
                                          # persistent platform has persistent state
    runtime_data_dir: str | None = None   # where runtime dbs live
    code_dir: str | None = None           # capability source materialization
    llm: LLMProvider | None = None        # default LLM provider (optional)
    extra: dict = field(default_factory=dict)


@dataclass
class Platform:
    """Facade over the composed use case groups."""

    config: PlatformConfig
    ctx: ApplicationContext
    #: unified permission kernel + policy lifecycle use cases (V0.1 security)
    security: "SecurityUseCases"

    def configure_llm(self, provider: "LLMProvider") -> None:
        """Attach (or replace) the default LLM provider at runtime.

        Agent-kind capabilities resolve the provider at call time, so this
        works before or after they are published."""
        self.config.llm = provider
        self.ctx.llm = provider
    workspaces: "WorkspaceUseCases"
    ontologies: "OntologyUseCases"
    data: "DataUseCases"
    capabilities: "CapabilityUseCases"
    applications: "ApplicationUseCases"
    intelligence: "IntelligenceUseCases"
    llm_config: "LlmConfig"
    releases: "ReleaseUseCases"
    state: "StateUseCases"
    events: EventLog
    dispatcher: Dispatcher
    verifier: RuntimeVerifier
    reconciler: Reconciler
    controller: DriftController
    metrics: WorkspaceMetrics
    service_levels: ServiceLevels
    history: StateHistory
    objectives: ObjectiveUseCases
    #: Smart-UI contribution registry; built-in plugins are registered by the
    #: composition root so a Platform never depends on a concrete adapter.
    smartui: PluginHost
    #: long-running slow loop; started explicitly by the HTTP adapter (or a
    #: test) so building a Platform never opens a background thread
    scheduler: Scheduler | None = None
    #: org tree (materialized path -> principal.org_path), identity directory +
    #: OIDC/session/SCIM, and the export/webhook distribution surfaces
    organization: "OrganizationService | None" = None
    identity_directory: "IdentityDirectory | None" = None
    identity: "IdentityService | None" = None
    scim: "SCIMService | None" = None
    exports: "ExportService | None" = None
    webhooks: "WebhookService | None" = None
    policy_drafts: "PolicyDraftService | None" = None
    async_tasks: "AsyncTaskService | None" = None

    def start_scheduler(self, poll_seconds: float = 1.0) -> Scheduler:
        if self.scheduler is None:
            self.scheduler = Scheduler(self.ctx)
            self.scheduler.every("outbox.dispatch", 5.0,
                                 lambda: self.dispatcher.dispatch())
            # Webhook delivery is independent of the transactional outbox:
            # an event can be marked dispatched for in-process subscribers
            # while still owing a signed, masked delivery to external hooks.
            self.scheduler.every("webhooks.deliver", 5.0,
                                 self._dispatch_webhooks)
            self.scheduler.every("objectives.evaluate", 30.0,
                                 self._evaluate_objectives)
            self.scheduler.every("controller.cycle", 60.0,
                                 self._controller_tick)
        self.scheduler.start(poll_seconds=poll_seconds)
        return self.scheduler

    def _dispatch_webhooks(self) -> dict:
        if self.webhooks is None:
            return {"delivered": 0, "failed": 0, "skipped": 0, "errors": []}
        return self.webhooks.dispatch_webhooks()

    def stop_scheduler(self) -> None:
        if self.scheduler is not None:
            self.scheduler.stop()

    def close(self) -> None:
        """Graceful shutdown: stop background work and release storage handles."""
        self.stop_scheduler()
        for component in (self.ctx.repo, self.ctx.audit):
            closer = getattr(component, "close", None)
            if callable(closer):
                closer()

    def _workspaces(self) -> list[str]:
        return [w["id"] for w in self.ctx.repo.list("workspace", None)]

    def _evaluate_objectives(self) -> list[dict]:
        results: list[dict] = []
        for workspace_id in self._workspaces():
            for app in self.ctx.repo.list("application", workspace_id):
                if not (app.get("objective") or {}).get("metric"):
                    continue
                try:
                    self.objectives.evaluate(workspace_id, app["id"], "system")
                    results.append({"workspace_id": workspace_id,
                                    "application_id": app["id"], "ok": True})
                except Exception as exc:
                    results.append({"workspace_id": workspace_id,
                                    "application_id": app["id"], "ok": False,
                                    "error": str(exc)})
        return results

    def _controller_tick(self) -> list[dict]:
        results: list[dict] = []
        for workspace_id in self._workspaces():
            try:
                results.append(self.controller.act(workspace_id, "system",
                                                   accept=True))
            except Exception as exc:
                results.append({"workspace_id": workspace_id, "ok": False,
                                "error": str(exc)})
        return results


class WorkspaceUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.create = CreateWorkspace(ctx)
        self.get = GetWorkspace(ctx)
        self.list = ListWorkspaces(ctx)
        self.delete = DeleteWorkspace(ctx)
        self.reset = ResetWorkspace(ctx)
        self.add_member = AddMember(ctx)
        self.remove_member = RemoveMember(ctx)
        self.list_members = ListMembers(ctx)


class OntologyUseCases:
    def __init__(self, ctx: ApplicationContext, publisher: SchemaPublisher) -> None:
        self.create = CreateOntology(ctx)
        self.get = GetOntology(ctx)
        self.upsert_object = UpsertObjectType(ctx)
        self.upsert_relation = UpsertRelation(ctx)
        self.add_term = AddVocabularyTerm(ctx)
        self.remove_object = RemoveObjectType(ctx)
        self.remove_relation = RemoveRelation(ctx)
        self.remove_term = RemoveVocabularyTerm(ctx)
        self.draft = DraftOntologyFromText(ctx)
        self.validate = ValidateOntology(ctx)
        self.publish = PublishOntology(ctx, publisher)
        self.revise = ReviseOntology(ctx)
        self.update_meta = UpdateOntologyMeta(ctx)
        self.discard_draft = DiscardOntologyDraft(ctx)
        self.lint = LintOntology(ctx)
        self.describe_runtime = DescribeRuntime(ctx)


class DataUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.connect_source = ConnectDataSource(ctx)
        self.update_source = UpdateDataSource(ctx)
        self.test_source = TestDataSource(ctx)
        self.preview_source = PreviewDataSource(ctx)
        self.create_projection = CreateProjection(ctx)
        self.update_projection = UpdateProjection(ctx)
        self.run_projection = RunProjection(ctx)
        self.get_projection_run = GetProjectionRun(ctx)
        self.list_sources = ListDataSources(ctx)
        self.list_projections = ListProjections(ctx)
        self.delete_source = DeleteDataSource(ctx)
        self.delete_projection = DeleteProjection(ctx)


class CapabilityUseCases:
    def __init__(self, ctx: ApplicationContext, publisher: SchemaPublisher) -> None:
        self.create = CreateCapability(ctx)
        self.update = UpdateCapability(ctx)
        self.clone = CloneCapability(ctx)
        self.publish = PublishCapability(ctx, publisher)
        self.delete = DeleteCapability(ctx, publisher)
        self.list = ListCapabilities(ctx)
        self.invoke = InvokeCapability(ctx)


class ApplicationUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.create = CreateApplication(ctx)
        self.add_view = AddView(ctx)
        self.add_query = AddQuery(ctx)
        self.update = UpdateApplication(ctx)
        self.update_view = UpdateView(ctx)
        self.update_query = UpdateQuery(ctx)
        self.clone = CloneApplication(ctx)
        self.attach_capability = AttachCapability(ctx)
        self.attach_ui_artifact = AttachUiArtifact(ctx)
        self.add_agent = AddAgent(ctx)
        self.publish = PublishApplication(ctx)
        self.run_query = RunAppQuery(ctx)
        self.run_agent = RunAppAgent(ctx)
        self.get = GetApplication(ctx)
        self.list = ListApplications(ctx)
        self.delete = DeleteApplication(ctx)
        self.remove_view = RemoveView(ctx)
        self.remove_query = RemoveQuery(ctx)
        self.remove_agent = RemoveAgent(ctx)
        self.detach_capability = DetachCapability(ctx)


class IntelligenceUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.ask = AskOntology(ctx)
        self.agent = RunAgent(ctx)


def _notification_handler(ctx: ApplicationContext):
    """Internal outbox consumer: materialize an operator notification.

    Delivery is at-least-once, so the handler is idempotent by event id.  It
    stores the notification in the design-time store; the UI reads it as a
    normal document.  This is the one real subscriber the platform owns, so
    "delivered" in the outbox means something was actually consumed (P1-7).
    """

    def handle(event: dict) -> dict:
        event_id = event["id"]
        key = f"notif_{event_id}"
        existing = ctx.repo.get("notification", event["workspace_id"], key)
        if existing is not None:
            return {"notification": key, "duplicate": True}
        row = {
            "id": key,
            "workspace_id": event["workspace_id"],
            "event_id": event_id,
            "type": event["type"],
            "payload": event.get("payload") or {},
            "created_at": event.get("occurred_at") or ctx.clock(),
            "read": False,
        }
        ctx.save("notification", row)
        return {"notification": key, "duplicate": False}

    # the outbox records this name as the consumer; "handle" would be opaque
    handle.__name__ = "notification"
    return handle


def build_platform(config: PlatformConfig | None = None) -> Platform:
    config = config or PlatformConfig()

    if config.store == "sqlite":
        if not config.db_path:
            raise ValidationError("PlatformConfig.db_path is required when store == sqlite")
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        repo = SqliteRepository(config.db_path)
        audit = SqliteAuditLog(config.db_path, repository=repo)
    elif config.store == "memory":
        repo, audit = InMemoryRepository(), InMemoryAuditLog()
    else:
        raise ValidationError(f"unknown platform store: {config.store!r}")

    # Every repository call made under an authorization context is pinned to
    # that decision's workspace and row scope.  System/migration code has no
    # context and keeps using the raw storage semantics.
    repo = GovernedRepository(repo, scope_provider=auth_context.current_scope)

    # kernel state follows the platform store unless pinned explicitly:
    # a persistent platform must not lose semantic state between processes
    if config.runtime_store is None:
        runtime_store = "sqlite" if config.store == "sqlite" else "memory"
        runtime_data_dir = config.runtime_data_dir
        if runtime_store == "sqlite" and runtime_data_dir is None:
            base = Path(config.db_path).parent if config.db_path else Path(".onto")
            runtime_data_dir = str(base / "runtime")
    else:
        runtime_store = config.runtime_store
        runtime_data_dir = config.runtime_data_dir

    code_store = FileCodeStore(config.code_dir or ".onto/code")
    gateways = WorkspaceGatewayProvider(
        default_store=runtime_store,
        runtime_data_dir=runtime_data_dir,
    )
    ctx = ApplicationContext(
        repo=repo,
        audit=audit,
        gateways=gateways,
        code_store=code_store,
        default_runtime_config={"store": runtime_store},
        llm=config.llm,
    )
    # multi-workspace limits (plan-v2 2.4): config wins, env is the fallback
    import os

    from .application.limits import LeaseManager, RateLimiter, rate_limit_from_env

    per_minute = config.extra.get("rate_limit_per_minute")
    ctx.rate_limiter = RateLimiter(
        per_minute if per_minute is not None else rate_limit_from_env())
    ctx.max_workspaces = int(config.extra.get("max_workspaces") or 0)
    lease_timeout = config.extra.get("lease_timeout_seconds", 10.0)
    ctx.leases = LeaseManager(
        timeout=None if lease_timeout is None else float(lease_timeout))
    admins = config.extra.get("platform_admins")
    if admins is None:
        admins = [a.strip() for a in
                  os.environ.get("ONTO_PLATFORM_ADMINS", "").split(",")
                  if a.strip()]
    ctx.platform_admins = frozenset(admins or [])
    # One authorization kernel owns membership/binding/group/policy reads and
    # every ALLOW/DENY decision.  It is installed before any use case can run.
    security_service = SecurityService(
        repo=repo,
        audit=audit,
        legacy_policy=ctx.policy,
        clock=ctx.clock,
        id_factory=lambda prefix="sec": ctx.id_factory(),
        platform_admins=ctx.platform_admins,
    )
    ctx.security = security_service
    publisher = SchemaPublisher(ctx)
    platform = Platform(
        config=config,
        ctx=ctx,
        security=SecurityUseCases(ctx, security_service),
        workspaces=WorkspaceUseCases(ctx),
        ontologies=OntologyUseCases(ctx, publisher),
        data=DataUseCases(ctx),
        capabilities=CapabilityUseCases(ctx, publisher),
        applications=ApplicationUseCases(ctx),
        intelligence=IntelligenceUseCases(ctx),
        llm_config=LlmConfig(ctx),
        releases=ReleaseUseCases(ctx),
        state=StateUseCases(ctx),
        events=EventLog(ctx),
        dispatcher=Dispatcher(ctx),
        verifier=RuntimeVerifier(ctx),
        reconciler=Reconciler(ctx),
        controller=DriftController(ctx),
        metrics=WorkspaceMetrics(ctx),
        service_levels=ServiceLevels(ctx),
        history=StateHistory(ctx),
        objectives=ObjectiveUseCases(ctx),
        smartui=build_default_host(),
    )
    platform.dispatcher.subscribe("todo.opened", _notification_handler(ctx))
    platform.dispatcher.subscribe("application.objective_breached",
                                  _notification_handler(ctx))

    # Organization and identity are composed after the facade so the tools see
    # the same platform.security facade the API does.
    platform.organization = OrganizationService(
        repo, ctx.clock, ctx.id_factory, security_service, ctx=ctx)
    platform.identity_directory = IdentityDirectory(ctx, security_service)
    platform.identity = IdentityService(
        platform.identity_directory, OIDCVerifier(), SessionIssuer(),
        LDAPAuthenticator())
    platform.scim = SCIMService(
        platform.identity_directory,
        bearer_token=os.environ.get("ONTO_SCIM_BEARER_TOKEN", ""),
        allow_anonymous=(
            os.environ.get("ONTO_SCIM_ALLOW_ANONYMOUS", "") == "1"),
    )
    platform.exports = ExportService(ctx, platform.security)
    platform.webhooks = WebhookService(ctx, platform.security,
                                       dispatcher=platform.dispatcher)
    platform.policy_drafts = PolicyDraftService(ctx, security_service)
    platform.async_tasks = AsyncTaskService(
        ctx, security_service, scheduler=lambda: platform.scheduler,
        invoke=platform.capabilities.invoke)
    # an execution that was running when the previous process died is
    # unknown, never silently retried (P0-2)
    try:
        ExecutionLedger(ctx).recover_stale()
    except Exception:
        pass
    # a deployment switch interrupted between the pending write and the
    # active pointer must be resolved against the ring runtime (P0-4)
    try:
        from .application.recovery import recover_all

        recover_all(ctx)
    except Exception:
        pass
    return platform
