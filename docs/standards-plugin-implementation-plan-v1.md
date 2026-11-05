# 插件化标准适配 P0 实现计划

> **依据**：[`standards-plugin-architecture.md`](./standards-plugin-architecture.md)（方案）、[`ontology-implementation-review-v1.md`](./ontology-implementation-review-v1.md)（审计）
> **状态**：P0 已实现并通过独立验证（全量 `715 passed, 0 skipped`，含 rdflib round-trip）；验收记录见 [`standards-plugin-implementation-report.md`](./standards-plugin-implementation-report.md)
> **范围**：P0（SPI + 注册/发现 + 只读上下文 + 确定性 IRI + 零依赖 Turtle/OWL/SKOS 导出 + use case + REST + CLI + 契约测试）
> **不做**：SHACL/SPARQL/导入/ABox 导出/GraphRAG（P1–P3）、Kernel 改动、核心运行时依赖新增
> **基线**：`python -m pytest -q` → 599 passed / 51.44s

---

## 1. 交付物

| # | 交付物 | 路径 |
|---|---|---|
| 1 | 插件 SPI（零依赖） | `src/onto_platform/semantic/spi.py` |
| 2 | 确定性 IRI 策略 | `src/onto_platform/semantic/iri.py` |
| 3 | 只读上下文 | `src/onto_platform/semantic/context.py` |
| 4 | 注册表（builtin + entry points） | `src/onto_platform/semantic/registry.py` |
| 5 | 零依赖 Turtle 序列化器 | `src/onto_platform/semantic/turtle.py` |
| 6 | OWL/RDF 导出插件 | `src/onto_platform/semantic/owl.py` |
| 7 | SKOS 导出插件 | `src/onto_platform/semantic/skos.py` |
| 8 | 用例 | `src/onto_platform/application/standards_usecases.py` |
| 9 | 组合根 + REST + CLI 接线 | `bootstrap.py` / `interfaces/api.py` / `interfaces/cli.py` |
| 10 | 测试（单元/API/CLI/契约） | `tests/test_semantic_*.py`、`tests/test_standards_*.py` |
| 11 | 实现报告（验证结论） | `docs/standards-plugin-implementation-report.md`（QA 产出） |

---

## 2. 冻结接口（各工作流必须按此实现，不得改名）

### 2.1 `semantic/spi.py`（Lead 已交付）

```python
ADAPTER_API_VERSION = "1.0"
CAPABILITY_KINDS = ("export", "validate", "query", "import")
SCOPE_KINDS = ("tbox", "abox", "both")

class AdapterError(ValidationError): ...            # 400 类错误
class AdapterIncompatible(AdapterError): ...
class AdapterNotFound(NotFoundError): ...

@dataclass(frozen=True, slots=True)
class AdapterManifest:
    name: str
    version: str
    api_version: str
    standards: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    formats_out: tuple[str, ...] = ()
    formats_in: tuple[str, ...] = ()
    description: str = ""
    permissions: dict[str, bool] = field(default_factory=lambda: {"network": False, "write": False})
    def to_dict(self) -> dict: ...

def manifest_problems(manifest: AdapterManifest) -> list[str]: ...  # 空 = 合法

@dataclass(frozen=True, slots=True)
class ExportRequest:
    format: str = ""
    scope: str = "tbox"
    include_abox: bool = False

@dataclass(frozen=True, slots=True)
class Artifact:
    content: bytes
    media_type: str
    filename: str = ""
    metadata: dict = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool
    violations: tuple[dict, ...] = ()

@dataclass(frozen=True, slots=True)
class QueryResult:
    rows: tuple[dict, ...] = ()
    variables: tuple[str, ...] = ()
    truncated: bool = False

@dataclass(frozen=True, slots=True)
class ImportResult:
    patch: dict = field(default_factory=dict)
    conflicts: tuple[dict, ...] = ()

@runtime_checkable
class StandardAdapter(Protocol):
    manifest: AdapterManifest
    def export(self, ctx: "AdapterContext", request: ExportRequest) -> Artifact: ...
```

### 2.2 `semantic/iri.py`（Lead 已交付）

```python
@dataclass(frozen=True, slots=True)
class IriPolicy:
    workspace_id: str
    ontology_name: str
    version: int
    base: str                            # 以 '#' 结尾的命名空间
    @classmethod
    def for_ontology(cls, ontology: Ontology) -> "IriPolicy": ...
    def ontology_iri(self) -> str: ...   # 去掉 '#'
    def class_iri(self, name: str) -> str: ...
    def relation_iri(self, name: str) -> str: ...
    def property_iri(self, owner: str, prop: str) -> str: ...
    def vocabulary_iri(self, term: str) -> str: ...
    def prefix_map(self) -> dict[str, str]: ...   # onto/owl/rdf/rdfs/xsd/skos
    def assert_no_collision(self, ontology: Ontology) -> None: ...  # 冲突抛 AdapterError
```

- IRI 稳定规则：`https://ontoplatform.local/ws/{workspace}/onto/{name}/v{version}#{Fragment}`；
- 片段：类 = `Name`；关系 = 关系名；数据属性 = `Owner.prop`；词表术语 = `term.{term}`；
- 版本升级 → 版本段变化、术语片段不换；同名跨版本不算冲突。

### 2.3 `semantic/context.py`（T1）

```python
@dataclass(frozen=True, slots=True)
class OntologyView:
    id: str
    workspace_id: str
    name: str
    version: int
    schema_version: int
    schema_fingerprint: str
    profile: str
    objects: Mapping[str, ObjectTypeSpec]
    relations: Mapping[str, RelationSpec]
    vocabulary: Mapping[str, VocabularyTerm]
    def compiled_schema(self) -> dict: ...   # OntologyCompiler().compile(ontology, []).schema

@dataclass(frozen=True, slots=True)
class SnapshotView:      # P0 只留位，不实现 ABox
    objects: tuple[dict, ...] = ()

@dataclass(frozen=True, slots=True)
class AdapterContext:
    workspace_id: str
    tbox: OntologyView
    iri: IriPolicy
    actor: str
    scope: str = "tbox"
    abox: SnapshotView | None = None
    version: str = ""
```

### 2.4 `semantic/registry.py`（T1）

```python
BUILTIN_ADAPTERS: tuple[str, ...] = (
    "onto_platform.semantic.owl:OwlRdfAdapter",
    "onto_platform.semantic.skos:SkosAdapter",
)

class PluginRegistry:
    ENTRY_POINT_GROUP = "onto_platform.standards"
    def __init__(self, entry_points=None) -> None: ...
    def register(self, adapter, *, source: str = "manual") -> None: ...   # 重名抛 AdapterError
    def discover(self) -> list[str]: ...          # builtin + entry points；非法项记入 self.problems，不炸启动
    def names(self) -> list[str]: ...             # 排序
    def get(self, name: str) -> StandardAdapter: ...   # 未知抛 AdapterNotFound
    def manifests(self) -> list[dict]: ...        # 排序 + status(ready/invalid) + problems

def default_registry() -> PluginRegistry: ...     # 进程级单例，懒发现
def reset_default_registry() -> None: ...         # 测试用
```

### 2.5 用例/API/CLI 契约（T3）

**Use case**（`application/standards_usecases.py`）：

```python
class StandardsUseCases:
    def __init__(self, ctx, registry=None) -> None: ...
    def list_adapters(self, workspace_id: str, actor: str) -> list[dict]
    def export(self, workspace_id: str, actor: str, adapter: str,
               format: str | None = None) -> dict
        # 返回 {"adapter","format","media_type","filename","content": bytes,"metadata": dict}
        # 无发布本体 → NotFoundError(404)；未知 adapter → AdapterNotFound(404)；
        # 已注册但不可用 adapter / 不支持的 format → AdapterError(400)
        # audit: action="standards.export", target=adapter
```

**REST**（`interfaces/api.py`，`_get` 内新增分支）：

```text
GET /api/workspaces/{wid}/standards/adapters
    → 200 {"adapters": [{name, version, api_version, standards, capabilities,
                         formats_out, description, status, problems}]}
GET /api/workspaces/{wid}/standards/export?adapter=owl-rdf&format=text/turtle
    → 200, Content-Type: <artifact.media_type>,
      Content-Disposition: attachment; filename="<artifact.filename>"
    → 404 unknown adapter / no published ontology；400 unsupported format
```

**CLI**（`interfaces/cli.py`）：

```text
onto standards list   --workspace W
onto standards export --workspace W --adapter owl-rdf --format text/turtle [--out FILE]
    --out 缺省：Turtle 文本写 stdout；二进制格式写 --out 或报错
```

**pyproject**：`[project.optional-dependencies] interop = ["rdflib>=7", "pyshacl>=0.29"]`；`dependencies = []` 不动。

---

## 3. 文件所有权（写作用域，避免冲突）

| 角色 | 写作用域 |
|---|---|
| Lead | `docs/standards-plugin-implementation-plan-v1.md`、`src/onto_platform/semantic/spi.py`、`semantic/iri.py`、`semantic/__init__.py`（初始）、最终集成/复核 |
| T1 dev-core | `src/onto_platform/semantic/context.py`、`registry.py`、`semantic/__init__.py`（导出补充）、`tests/test_semantic_context.py`、`tests/test_semantic_registry.py`、`tests/test_semantic_iri.py` |
| T2 dev-exporters | `src/onto_platform/semantic/turtle.py`、`owl.py`、`skos.py`、`tests/test_standards_turtle.py`、`tests/test_standards_export.py` |
| T3 dev-platform | `application/standards_usecases.py`、`bootstrap.py`、`interfaces/api.py`、`interfaces/cli.py`、`pyproject.toml`、`tests/test_standards_api.py`、`tests/test_standards_cli.py` |
| T4 qa-verifier | `tests/test_standards_contract.py`、`docs/standards-plugin-implementation-report.md` |

---

## 4. 任务分解与依赖

```text
Lead: 计划 + spi.py + iri.py            [立即]
  ├─ T1 dev-core:  context + registry + 单测      (无阻塞，依赖 Lead 接口)
  ├─ T2 dev-exporters: turtle + owl + skos + 单测 (无阻塞，依赖 Lead 接口)
  │
  └─ T3 dev-platform: usecase + bootstrap + api + cli + pyproject + 测试
        blocked_by: T1, T2
        └─ T4 qa-verifier: 独立契约/边界/端到端验证 + 实现报告
              blocked_by: T3
```

### T1 验收（dev-core）
- `PluginRegistry` 发现 builtin；重复注册/非法 manifest/api_version 不兼容 → 明确错误；
- entry points 用可注入 `entry_points` 参数模拟（不依赖安装态）；
- `AdapterContext` 从 `Ontology` 构造，`compiled_schema()` 与 `OntologyCompiler().compile(..)` 一致；
- `IriPolicy` 稳定/冲突检测测试；
- 新增测试文件可独立 `python -m pytest tests/test_semantic_*.py -q` 全绿。

### T2 验收（dev-exporters）
- `TurtleWriter`：QName/前缀、字符串转义、xsd 数据类型、确定性排序（同一 ontology 两次输出逐字节相同）；
- `OwlRdfAdapter`：类、对象属性（domain/range、inverse）、数据属性（domain/range xsd）、`owl:Ontology` 头（含 `owl:versionInfo` + `schema_fingerprint`）；
- `SkosAdapter`：`skos:ConceptScheme` + 每术语 `skos:Concept` + `prefLabel`/`altLabel`/`notation`；
- **红线测试**：OWL 产物不含 `owl:hasKey`、`owl:cardinality`、`owl:FunctionalProperty`、`owl:someValuesFrom`；
- manifest 合法（`name="owl-rdf"` / `"skos"`，`api_version="1.0"`，capabilities=("export",)，formats_out 匹配 medial type）；
- 产物可被外部解析（rdflib 未安装则 `pytest.skip`，安装后启用）。

### T3 验收（dev-platform）
- 组合根：`Platform.standards` 可用；`build_platform` 不产生新依赖；
- API：`GET /standards/adapters`（400/404 场景）、`GET /standards/export` 返回正确 `Content-Type`/`Content-Disposition`/Turtle 内容；
- CLI：`onto standards list/export` 在 sqlite 临时库上可用（`--out` 落盘）；
- 审计：`standards.export` 事件写入；
- 回归：既有 599 测试全绿 + 新增测试全绿。

### T4 验收（qa-verifier）
- 契约测试：所有发现插件跑同一套断言（manifest 合法、export 确定性、artifact 类型、异常映射）；
- 边界：`grep` 证明插件不 import `bootstrap`/`KernelRuntimeGateway`/`repo` 写句柄；`pyproject` core deps 不变；
- 端到端：`http_server` 上真实 socket 导出 Turtle，字节非空、前缀/类声明存在；
- 语义红线断言；可选 rdflib 解析（装了才跑，否则 skip）；
- 全量 `python -m pytest -q` 通过；产出实现报告（含命令、数字、残余风险）。

---

## 5. 测试矩阵

| 层 | 文件 | 关键断言 |
|---|---|---|
| SPI/IRI | `tests/test_semantic_iri.py` | IRI 稳定性、冲突、非法标识符 |
| 注册表 | `tests/test_semantic_registry.py` | builtin 发现、重复/非法 manifest、entry point 模拟、排序 |
| 上下文 | `tests/test_semantic_context.py` | 视图字段、compiled_schema 一致性、只读性（frozen） |
| Turtle | `tests/test_standards_turtle.py` | 转义、前缀、确定性、数据类型 |
| 导出插件 | `tests/test_standards_export.py` | OWL 类/属性/domain/range/inverse；SKOS 词表；红线 |
| API | `tests/test_standards_api.py` | 列表、导出、错误码、Content-Type/Disposition |
| CLI | `tests/test_standards_cli.py` | list/export/--out、错误退出码 |
| 契约 | `tests/test_standards_contract.py` | 跨插件一致性、边界 grep、E2E、确定性 |

---

## 6. 里程碑

| 里程碑 | 内容 | 判定 |
|---|---|---|
| M1 | Lead：计划 + SPI + IRI 冻结 | 文件可导入、`pytest tests/test_semantic_iri.py` 绿 |
| M2 | T1 + T2 并行完成 | 各自测试绿；`registry.discover()` 看到 2 个插件 |
| M3 | T3 接线完成 | API/CLI 端到端导出 Turtle 成功；599 回归绿 |
| M4 | T4 独立验证 | 契约测试绿、全量测试绿、报告产出 |

---

## 7. DoD（最终验收）

1. `python -m pytest -q` 全绿且总数 ≥ 599 + 新增；
2. `GET /api/workspaces/{wid}/standards/export?adapter=owl-rdf&format=text/turtle` 返回可解析 Turtle；
3. 同一发布版本连续两次导出逐字节相同；
4. OWL 产物无 `hasKey`/`owl:cardinality`/`FunctionalProperty`/`someValuesFrom`；
5. `pyproject.toml` `dependencies = []` 不变，`ontology-guide` 与 Kernel 无改动；
6. 插件不 import 平台内部（bootstrap/gateway/repo）——QA grep 断言；
7. 文档：实现报告 + README/架构文档链接更新（Lead 收尾）。

---

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 多人同时改 `api.py`/`bootstrap.py` | 只有 T3 写；其他角色只读 |
| Lead 冻结接口后 T1/T2 需要微调 | 只允许“新增不改名”；改名需 Lead 同步所有人 |
| rdflib 不可用 | 测试 skip；零依赖断言为主 |
| 导出产物断言过死 | 断言关键三元组/前缀，不做全文 golden（除 Turtle 单测） |
| 合并顺序 | T1/T2 先合并；T3 依赖它们；T4 最后 |

---

## 9. 团队执行说明

- 每个任务用共享任务板 + `write_scopes` 标注；开工前 `team_task_get` → `claim`（带 revision），完成后 `complete`；
- 有阻塞用任务 `blocked_by`，不要跨作用域改文件；
- 每个角色完成后发消息给 Lead（目标 `lead`），附：改了哪些文件、测试命令与结果、遗留问题；
- Lead 负责最终 diff 复核 + 全量测试 + 收尾文档。
