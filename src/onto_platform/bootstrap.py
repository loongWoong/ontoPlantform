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
    CreateApplication,
    GetApplication,
    ListApplications,
    PublishApplication,
    RunAppAgent,
    RunAppQuery,
)
from .application.capability_usecases import (
    CreateCapability,
    InvokeCapability,
    ListCapabilities,
    PublishCapability,
)
from .application.common import ApplicationContext
from .application.data_usecases import (
    ConnectDataSource,
    CreateProjection,
    ListDataSources,
    ListProjections,
    RunProjection,
)
from .application.intelligence_usecases import AskOntology, RunAgent
from .application.ontology_usecases import (
    AddVocabularyTerm,
    CreateOntology,
    DescribeRuntime,
    GetOntology,
    PublishOntology,
    SchemaPublisher,
    UpsertObjectType,
    UpsertRelation,
    ValidateOntology,
)
from .application.workspace_usecases import (
    AddMember,
    CreateWorkspace,
    GetWorkspace,
    ListWorkspaces,
)
from .infrastructure.persistence.file_code_store import FileCodeStore
from .infrastructure.persistence.memory_repo import InMemoryAuditLog, InMemoryRepository
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


class WorkspaceUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.create = CreateWorkspace(ctx)
        self.get = GetWorkspace(ctx)
        self.list = ListWorkspaces(ctx)
        self.add_member = AddMember(ctx)


class OntologyUseCases:
    def __init__(self, ctx: ApplicationContext, publisher: SchemaPublisher) -> None:
        self.create = CreateOntology(ctx)
        self.get = GetOntology(ctx)
        self.upsert_object = UpsertObjectType(ctx)
        self.upsert_relation = UpsertRelation(ctx)
        self.add_term = AddVocabularyTerm(ctx)
        self.validate = ValidateOntology(ctx)
        self.publish = PublishOntology(ctx, publisher)
        self.describe_runtime = DescribeRuntime(ctx)


class DataUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.connect_source = ConnectDataSource(ctx)
        self.create_projection = CreateProjection(ctx)
        self.run_projection = RunProjection(ctx)
        self.list_sources = ListDataSources(ctx)
        self.list_projections = ListProjections(ctx)


class CapabilityUseCases:
    def __init__(self, ctx: ApplicationContext, publisher: SchemaPublisher) -> None:
        self.create = CreateCapability(ctx)
        self.publish = PublishCapability(ctx, publisher)
        self.list = ListCapabilities(ctx)
        self.invoke = InvokeCapability(ctx)


class ApplicationUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.create = CreateApplication(ctx)
        self.add_view = AddView(ctx)
        self.add_query = AddQuery(ctx)
        self.attach_capability = AttachCapability(ctx)
        self.add_agent = AddAgent(ctx)
        self.publish = PublishApplication(ctx)
        self.run_query = RunAppQuery(ctx)
        self.run_agent = RunAppAgent(ctx)
        self.get = GetApplication(ctx)
        self.list = ListApplications(ctx)


class IntelligenceUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.ask = AskOntology(ctx)
        self.agent = RunAgent(ctx)


def build_platform(config: PlatformConfig | None = None) -> Platform:
    config = config or PlatformConfig()

    if config.store == "sqlite":
        if not config.db_path:
            raise ValidationError("PlatformConfig.db_path is required when store == sqlite")
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        repo = SqliteRepository(config.db_path)
        audit = SqliteAuditLog(config.db_path)
    elif config.store == "memory":
        repo, audit = InMemoryRepository(), InMemoryAuditLog()
    else:
        raise ValidationError(f"unknown platform store: {config.store!r}")

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
    publisher = SchemaPublisher(ctx)
    return Platform(
        config=config,
        ctx=ctx,
        workspaces=WorkspaceUseCases(ctx),
        ontologies=OntologyUseCases(ctx, publisher),
        data=DataUseCases(ctx),
        capabilities=CapabilityUseCases(ctx, publisher),
        applications=ApplicationUseCases(ctx),
        intelligence=IntelligenceUseCases(ctx),
    )
