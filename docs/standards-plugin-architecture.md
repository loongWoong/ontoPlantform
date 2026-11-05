# 以插件形式适配标准：OntoPlatform 标准适配插件化方案

> **配套架构图**：[`standards-plugin-architecture.svg`](./standards-plugin-architecture.svg)（1744×1177，已过 svg-infovis 几何门禁：无文本压线、无节点重叠）
> **前置审计**：[`ontology-implementation-review-v1.md`](./ontology-implementation-review-v1.md)（P0-1 标准互操作缺失）
> **关联项目计划**：[`plan-v2.md`](./plan-v2.md)（O-3 命名空间、Phase 2/3 路线）

---

## 1. 目标与不变量

**目标**：把 RDF / OWL / SKOS / SHACL / SPARQL 等标准能力，以“可独立安装、独立升级、可启停、能审计”的**插件**形式接入平台，而不是把 `rdflib`、`pyshacl` 直接塞进平台核心。

**为什么不直接改核心**：

| 方式 | 优点 | 代价 |
|---|---|---|
| 核心内置 rdflib | 少一层抽象 | 破坏“核心零依赖”；标准依赖版本与平台强耦合；错误隔离差；合规审查面扩大 |
| **插件（本方案）** | 核心零标准依赖；插件可独立发版/回滚/禁用；第三方可贡献行业适配器（行业词表、BFO 对齐、GraphRAG） | 需要定义 SPI 与注册/治理机制；产物质量取决于契约测试 |

**必须保持的不变量**（沿用现有设计原则）：

1. **Kernel 是唯一事实来源**：插件不直连 Kernel，只读 `AdapterContext` 中的快照视图；
2. **平台不 import Kernel**：ABox 快照只经由 `KernelRuntimeGateway`（现有防腐层）；
3. **核心 `dependencies = []` 不动**：标准依赖只进插件自己的包或可选 extra；
4. **单向导出优先**：平台 → 标准产物；导入能力作为独立插件后置，避免双写漂移。

---

## 2. 插件模型

### 2.1 统一契约：`StandardAdapter` SPI

平台核心定义一个极小的 Python Protocol（不依赖任何第三方标准库）：

```python
# src/onto_platform/semantic/spi.py（核心侧，零依赖）
from typing import Protocol, runtime_checkable

@runtime_checkable
class StandardAdapter(Protocol):
    manifest: "AdapterManifest"

    # 能力按 manifest 声明裁剪：未声明的能力不实现即可
    def export(self, ctx: "AdapterContext", req: "ExportRequest") -> "Artifact": ...
    def validate(self, ctx: "AdapterContext", payload: bytes) -> "ValidationReport": ...
    def query(self, ctx: "AdapterContext", query: str) -> "QueryResult": ...
    def import_(self, ctx: "AdapterContext", bundle: bytes) -> "ImportResult": ...
```

四类能力、四种返回对象，全部是核心定义的 dataclass（插件只需要构造它们，不接触平台内部）：

| 能力 | 输入 | 输出 | 典型插件 |
|---|---|---|---|
| `export` | TBox（± ABox 快照）、格式、IRI 策略 | `Artifact(bytes, media_type, filename, metadata)` | OWL/RDF、SKOS |
| `validate` | 形状/数据文档 | `ValidationReport(pass, violations[])` | SHACL |
| `query` | 只读查询语句 | `QueryResult(rows, variables, truncated)` | SPARQL |
| `import` | 标准文档 | `ImportResult(draft_patch, conflicts[])` | OWL/SKOS 导入 |

### 2.2 插件清单（manifest）——插件的“身份证 + 能力声明”

```json
{
  "name": "owl-rdf",
  "aliases": ["onto-standards-owl"],
  "version": "0.1.0",
  "api_version": "1.0",
  "standards": ["owl", "rdf", "turtle"],
  "capabilities": ["export"],
  "formats": { "out": ["text/turtle"], "in": [] },
  "requires": { "ontoplatform": ">=0.4", "extras": [] },
  "permissions": { "network": false, "write": false },
  "config_schema": {}
}
```

- `name` 是**插件 ID**（短名，已随 P0 发布：`owl-rdf` / `skos`）；`aliases` 兼容更早的清单命名（`onto-standards-owl` / `onto-standards-skos`，后者可视作包名），Registry 的 `get()` 同时接受两种写法，`names()` 只返回 canonical；
- `api_version` 与平台常量 `ADAPTER_API_VERSION` 做**主版本兼容**校验；
- `capabilities` 决定平台暴露哪些入口；Registry 在注册/发现时还会校验**声明的能力是否有对应可调用方法**（`export/validate/query/import_`），避免“能力面撒谎”；
- `formats` 早期扁平字段 `formats_out` / `formats_in` 保留为 dataclass 存储（向后兼容），`to_dict()` 同时输出嵌套 `formats`；
- `permissions` 在 v1 一律 `network=false`、`write=false`；插件只拿到只读 `context`，P0 在 manifest 校验与调用隔离层面保证只读，运行时权限注入属 P1；
- `requires.ontoplatform` 在加载时与 `onto_platform.__version__` 做兼容检查（不兼容 → `status=invalid`，`problems` 文案含 "incompatible" 并在列表标红）；`requires.extras` 告诉运维该插件需要安装哪个 extra，**平台不替它装**；
- `config_schema` 为可选 JSON Schema 片段；P0 仅声明与校验，配置注入属 P1。

### 2.3 发现、安装与生命周期

```text
[pip install onto-standards-owl]
   └─ entry point: onto_platform.standards → owl = "onto_standards_owl:Adapter"
        └─ PluginRegistry.discover()  ← importlib.metadata.entry_points()
             ├─ manifest 校验（schema / api_version / 依赖）
             ├─ 能力协商（平台版本 × 插件要求）
             └─ workspace 级启停（settings 表，默认 disabled）
```

| 阶段 | 行为 |
|---|---|
| 安装 | 插件是普通 Python 包，依赖装在插件自己的依赖树；核心不变 |
| 发现 | 启动时扫描 entry points（P0 已实现）；`POST /standards/reload` 热重载属 P1 |
| 启用 | 按 workspace 启用/禁用（P1）；P0 的 `adapters` 列表已显示版本、能力、健康状态 |
| 调用 | `StandardsService` 统一鉴权、审计（`standards.export` / `standards.export.failed`）、异常隔离（P0 已实现：插件异常 → 502 `AdapterInvocationError`，不影响平台主流程） |
| 升级/回滚 | 插件版本独立；manifest `api_version` 不兼容时拒绝加载并在列表里标红 |
| 卸载 | entry point 消失 → 自动标记 unavailable，历史产物保留 |

### 2.4 `AdapterContext`：插件唯一的读口

```python
@dataclass(frozen=True)
class AdapterContext:
    workspace_id: str
    tbox: OntologyView      # 只读：类型/属性/关系/词表/profile/类层次（无写句柄）
    abox: SnapshotView | None  # 只读、分页、经 ExportService 行级过滤 + 字段脱敏
    iri: IriPolicy          # base IRI、term IRI、版本 IRI、前缀表
    actor: str
    scope: Literal["tbox", "abox", "both"]
    version: str            # 发布版本（写入产物元数据）
```

关键设计：**ABox 不直接暴露 gateway**，而是复用平台已有的 `ExportService`（它已实现行级过滤与字段脱敏，见审计 B 报告）。插件拿不到 `repo`、`gateway`、数据库句柄，因此“插件绕过权限”在结构上不可能发生。

> P0 实现：`abox=None`、仅支持 `scope="tbox"`；`abox`/`both` 或 `include_abox=True` 会显式抛 `AdapterError`（不再静默忽略）。ABox 快照与 `ExportService` 接线属 P2。

### 2.5 语义正确性：OWL 与 SHACL 分工（红线）

审计 B 指出的关键风险是“导出资产看起来标准、语义却是错的”，比不导出更危险。因此本方案规定：

| 平台概念 | 导出到 OWL | 导出到 SHACL | 说明 |
|---|---|---|---|
| ObjectType | `owl:Class` + 父类/子类 | `sh:NodeShape`（targetClass） | 类层次单向展开 |
| Relation | `owl:ObjectProperty` + `rdfs:domain/range` + `owl:inverseOf` | 端点/基数形状 | cardinality 只写 SHACL 计数 |
| Property（类型） | `owl:DatatypeProperty` + `rdfs:range`（xsd） | `sh:datatype` | — |
| required / enum / min-max / unique | **不写**（不用 `someValuesFrom`、`FunctionalProperty` 冒充） | `sh:minCount/maxCount/in` | 平台约束是运行期约束 |
| identity | **不写 `owl:hasKey`** | `sh:uniqueLang` 不适用 → 用平台唯一性说明 | hasKey 蕴含 sameAs，语义过强 |
| Vocabulary | SKOS `skos:Concept` + `skos:altLabel`（层级就绪后 `broader/narrower`） | — | 与 OWL 类分离 |
| profile（closed/open） | OWL 本体本体不表达 | `sh:closed`（可选，默认关闭） | — |

> 结论：**OWL 只承载结构性公理（类/属性/domain-range/反向/层级），SHACL 承载运行期约束**；两者不混写，测试用例按此断言。

---

## 3. 架构图说明

架构图见 [`standards-plugin-architecture.svg`](./standards-plugin-architecture.svg)，自上而下五层：

| 层 | 内容 | 关键边 |
|---|---|---|
| **入口层** | REST API（`/adapters`、`/export`、`/validate`）、CLI（`onto standards …`）、控制台下载/校验按钮 | 向下到宿主：导出/校验/查询请求 |
| **插件宿主（平台核心）** | `StandardAdapter SPI`、`PluginRegistry`、`AdapterContext`、统一 `Export/Validate/Query` 服务、插件治理（权限/审计/隔离/可选依赖） | 向下到适配器：发现/装配/注入上下文 |
| **适配器插件** | OWL/RDF 导出、SKOS 导出、SHACL 形状、SPARQL 查询、OWL/SKOS 导入、可选语义检索；每个插件标注其产物与外部消费方 | 向上收数据视图；向下（概念上）产出标准文件 |
| **数据基础（插件只读）** | 本体聚合 TBox（类型/关系/词表/profile/类层次）、运行时快照 ABox（只读视图） | 向适配器注入只读快照 |
| **运行时内核** | Kernel（Schema/State/Function/Change），唯一事实来源 | 经 `KernelRuntimeGateway` 生成 ABox 快照 |

---

## 4. 首批插件清单

| 插件（ID / 别名） | 能力 | 依赖 | 阶段 | 外部消费方 |
|---|---|---|---|---|
| `owl-rdf` / `onto-standards-owl` | export（P0：Turtle，含对象/数据属性、domain/range、inverse；JSON-LD 与类层次延后：JSON-LD P1、类层次依赖模型层） | 无（核心自写最小序列化器）；CI 用 rdflib 校验 | **P0** | Protégé、数据目录、图数据库 |
| `skos` / `onto-standards-skos` | export（P0：词表 → SKOS Concept/prefLabel（CJK 带 `@zh`）/altLabel/notation/inScheme；broader/narrower 依赖词表层级模型，P2） | 无 | **P0** | 词表平台、搜索 |
| `onto-standards-shacl` | export + validate（形状 + pyshacl） | `pyshacl` | **P1** | 数据接入方、pyshacl |
| `onto-standards-sparql` | export（快照）+ query（rdflib 只读 SPARQL） | `rdflib` | **P2** | Fuseki/BI/分析 |
| `onto-standards-import` | import（OWL/SKOS → OntologyDraft + 冲突报告） | `rdflib` | **P2** | 行业本体、标准词表 |
| `onto-standards-search` | query（embedding/GraphRAG，可选） | 独立 embedding 依赖 | **P3** | 平台内语义检索 |

**核心侧不需要为标准能力新增任何依赖**；`interop` extra 只给“官方插件”的依赖做便捷安装，第三方插件自带依赖。

---

## 5. 平台核心改动清单

| 位置 | 改动 | 说明 |
|---|---|---|
| `src/onto_platform/semantic/spi.py` | 新增 SPI：Protocol、manifest dataclass、Artifact/Report/Result 类型 | 零依赖 |
| `src/onto_platform/semantic/registry.py` | 新增 `PluginRegistry`：entry_points 发现、manifest/能力/requires 校验、别名解析、健康状态（workspace 启停属 P1） | 失败隔离 |
| `src/onto_platform/semantic/context.py` | 新增 `AdapterContext` 构造器：TBox 视图 + `IriPolicy`（ABox 快照属 P2） | 权限不旁路 |
| `src/onto_platform/semantic/iri.py` | 新增确定性 IRI 生成（base `https://ontoplatform.local/ws/{workspace}/onto/{name}/v{version}#`，term 稳定化，冲突检测） | 对应 O-3（平台级 ID 冲突/撤回仍属模型路线） |
| `src/onto_platform/application/standards_usecases.py` | 用例：ListAdapters / ExportStandard（P0）；ValidateStandard / QueryStandard 属 P1/P2 | 鉴权 + 审计 + 异常隔离 |
| `src/onto_platform/interfaces/api.py` | 路由：`GET /standards/adapters`、`GET /standards/export`（P0）；`POST /standards/validate`、`GET /standards/artifacts/{id}`、`POST /standards/reload` 属 P1 | 统一错误码（含 502） |
| `src/onto_platform/interfaces/cli.py` | `onto standards list|export`（P0）；`validate` 属 P1 | 离线 |
| `pyproject.toml` | `[project.optional-dependencies] interop = ["rdflib>=7", "pyshacl>=0.29"]`；dev extra 含 `pytest-cov`；**core dependencies 保持 `[]`** | — |
| 文档/前端 | 控制台“下载本体 / 校验”按钮，URL 指向 `/standards/export?adapter=owl-rdf&format=text/turtle` | 可选 P1 |

**Kernel 零改动**（符合 ADR-1 边界）。

---

## 6. 版本与产物治理

- 平台常量 `ADAPTER_API_VERSION = "1.0"`；插件 manifest `api_version` 主版本一致才加载；
- P0 产物元数据（`Artifact.metadata` + 响应头）：`workspace_id`、`ontology_version`、`schema_fingerprint`、`adapter_version`、`actor`；Turtle 内只有 `owl:versionInfo`（含 fingerprint）与 `owl:versionIRI`，**时间戳不进图**；
- 命名图 `urn:ontoplatform:ws:{wid}:v{ontology_version}` 属 P1（当前 Turtle 为单图；后续随 JSON-LD/产物治理落地）；
- 产物必须**确定性**（三元组排序、IRI 稳定、无时间戳进图数据），保证同一版本两次导出 diff 为空；跨进程（不同 `PYTHONHASHSEED`）字节一致由契约测试锁定；
- 兼容性遵循 `compat.check_compatible`：导出前对当前发布版本做一次 `compile → fingerprint`，产物与版本号绑定；
- 语义红线测试：`test_export_does_not_emit_has_key`、`test_export_does_not_emit_owl_cardinality`、`test_required_maps_to_shacl_only`。

---

## 7. 实施路线

### P0（2–3 周）打通最小闭环
1. 核心 SPI + Registry + Context + IRI（零依赖）；
2. `owl-rdf` 与 `skos`（自写 Turtle 序列化器，零依赖），先只导出 TBox；JSON-LD 延后到 P1；
3. `GET /standards/export` + CLI `onto standards export`；
4. 契约一致性测试 + golden 文件（逐字节 + 关键三元组数量）；CI 安装 `[dev,interop]`，用 `rdflib` 解析产物；
5. 验收：Turtle 可被 Protégé 打开；关键三元组数量断言；599 既有测试保持全绿。

### P0 实现状态（已落地 + C0/C1 补全）

P0 已随 `v0.3` 实现完成，并按 [`standards-plugin-completion-review-v1.md`](./standards-plugin-completion-review-v1.md) 完成 C0/C1 收口（执行记录见 [`standards-plugin-completion-plan-v1.md`](./standards-plugin-completion-plan-v1.md)）：

- 接口冻结与任务分解：[`standards-plugin-implementation-plan-v1.md`](./standards-plugin-implementation-plan-v1.md)
- 独立验证结论（DoD 逐条）：[`standards-plugin-implementation-report.md`](./standards-plugin-implementation-report.md)
- 产出：`src/onto_platform/semantic/`（`spi`/`iri`/`context`/`registry`/`turtle`/`formats`/`owl`/`skos`）、`application/standards_usecases.py`、`GET /api/workspaces/{wid}/standards/adapters|export`、`onto standards list|export`；核心 `dependencies = []` 不变，内置插件 `owl-rdf` / `skos`（别名 `onto-standards-owl` / `onto-standards-skos`）。
- 契约：manifest 含 `formats{out,in}/requires/config_schema/aliases`；能力-方法一致性、`requires` 兼容、别名解析、插件异常 502 隔离与失败审计、`scope/include_abox` 显式拒绝、provenance 元数据与响应头、CJK `@zh` 语言标签、golden + 数量断言、跨进程确定性。
- 实测：全量 `794 passed, 0 skipped`（60–66s 量级）；覆盖率 79.94%（门禁 70%）；CI 安装 `[dev,interop]` 后 rdflib 解析/契约用例不再 skip。Protégé 人工打开验收仍待执行（本机环境无 Protégé）。

### P1（2–3 周）治理与 SHACL
1. workspace 启停 + `POST /standards/reload` + 健康状态接入 `/health`（entry-point 发现与 `adapters` 列表已在 P0）；
2. `onto-standards-shacl`：facets → 形状（export）+ `pyshacl` 校验（validate），`requires.extras` 提示；
3. 插件治理：运行时权限检查、`POST /standards/validate` + CLI、`GET /standards/artifacts/{id}`、插件示例与开发文档、控制台按钮；
4. 验收：禁用插件后入口返回 unavailable 且其他插件不受影响；坏插件 manifest 被拒绝并给出可操作错误；好/坏数据 SHACL 校验结果符合预期。

### P2（3–4 周）查询与导入
1. `onto-standards-sparql`：ABox 快照 + TBox 合并为只读图，支持 SELECT/ASK；限制 10⁵ 边/workspace；
2. `onto-standards-import`：OWL/SKOS 子集 → `OntologyDraft` patch + 冲突报告（不直接改发布版本）；
3. 第三方插件示例（`examples/plugins/`）+ 开发文档；
4. 验收：SPARQL 基准查询（含子类、反向）结果正确；导入产物通过 lint 与兼容矩阵。

### P3（可选）语义检索插件
embedding/GraphRAG 作为 `onto-standards-search` 插件，不进核心、默认关闭。

---

## 8. 验收标准（DoD）

1. **零依赖不破**：`pip install onto-platform` 后无 rdflib/pyshacl；核心 `dependencies = []` 不变；
2. **边界不破**：插件代码中不出现 `onto_platform.bootstrap.Platform` / `KernelRuntimeGateway` / `repo` 的引用（grep 测试）；
3. **契约测试**：对每个插件跑同一套 conformance suite（manifest 合法、能力-方法一致、export 确定性、产物可解析、错误映射、无网络/无写权限）；
4. **语义红线**：OWL 产物中不含 `owl:hasKey`、不含 `owl:cardinality` 系列冒充运行期约束；SHACL 覆盖 required/enum/range/unique；
5. **隔离性**：插件抛异常 → 平台返回结构化 502、不崩溃、审计留痕（P0 已实现）；
6. **可回滚**：禁用插件后所有入口返回明确 unavailable，不影响其他插件（P1）；
7. **既有基线**：599 个测试保持全绿；新增测试覆盖 SPI/Registry/API/CLI/golden。

---

## 9. 风险与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| 产物“看起来标准、语义错误” | OWL 表达力强，容易过度映射 | §2.5 分工表 + 语义红线测试；OWL 只表达结构 |
| 双表示漂移 | TBox 导出与平台模型可能不一致 | 单向导出；产物带 `schema_fingerprint`；CI round-trip 校验 |
| 自写 Turtle 序列化器有 bug | 零依赖的最小实现 | 范围收窄（仅 TBox）；CI 用 rdflib 双向校验；后续可切换插件内部实现而不改 SPI |
| 供应链/插件质量 | 第三方插件可能恶意或低质 | manifest 权限声明 + 只读上下文 + 无网络；版本固定；审计 |
| 插件进程内崩溃 | v1 同进程执行 | try/except 隔离；P2 可选子进程 worker（SPI 不变） |
| 大对象导出性能 | 全量 ABox 可能很大 | 分页/流式；TBox 与 ABox 分离；SPARQL 模式限边数 |

---

## 10. 与指南的对齐

| 指南能力 | 本方案对应 |
|---|---|
| `spec/rdf`、`spec/owl`、`spec/skos`、`spec/shacl`、`spec/sparql` | 五个标准插件（P0/P1/P2） |
| `modeling/elements/module` | 命名空间 + IRI 策略（P0）提供模块化基础 |
| `application/integration` | 插件即集成边界：标准产物导出、外部本体导入 |
| `impl/arch-pattern/versioning` | 产物元数据绑定版本 + fingerprint + 确定性导出 |
| `application/rules-validation` | SHACL 形状导出/校验（与内核 facets 同源，不重写约束） |

---

*本方案与审计报告 [`ontology-implementation-review-v1.md`](./ontology-implementation-review-v1.md) 互补：审计给出“缺什么”，本方案给出“以什么形态补、边界怎么画、按什么顺序落地”。*
