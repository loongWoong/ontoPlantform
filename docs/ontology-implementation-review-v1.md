# OntoPlatform 本体实现审计与完善方案

> **对照基准**：《本体建模与应用指南》（`ontology-guide/`，43 节：标准与语言、建模、实现、应用、落地）
> **审计对象**：OntoPlatform v0.3（`src/onto_platform`，126 个 Python 文件，74 个测试文件；运行时依赖同级 `ontoRK` Kernel）
> **审计日期**：2026-11-05
> **审计方法**：代码与真实运行实证（结论均带 `file:line` 证据）+ 指南逐章对照；测试基线 `python -m pytest -q` → **599 passed / 53.87s**
> **边界说明**：本报告审计“平台实现”而非 `ontology-guide` 文档站本身；`ontology-guide` 作为基准。前端视觉不在此范围。

---

## 0. 审计结论速览

平台已经是一个**“可判定对象图 + 强治理运行时”**，工程完整度明显高于多数同阶段项目：
身份/枚举/范围/唯一性/基数/状态机在**内核写路径真实强制**（不是元数据摆设）；发布有兼容性矩阵、发布环、回滚、幂等三态、审计与投影删除传播；测试 599 全绿。

但对照指南，它**还不是“标准本体平台”**：本体资产不能以 RDF/OWL/SKOS/SHACL/SPARQL 形式沉淀、交换、推理与复用；类层次与公理缺失；OWA/CWA 只是声明；语义检索是子串匹配。

一句话：**现在能“运行一个业务世界”，还不能“沉淀、交换、推理和复用本体知识资产”。**

按影响排序，六类差距：

| 优先级 | 差距 | 指南对应 |
|---|---|---|
| P0 | 无语义标准互操作（RDF/OWL/SKOS/SHACL/SPARQL 全无，本体不可导出/导入，API 只有状态导出） | `spec/*`、`application/integration` |
| P0 | 类层次与公理缺失：ObjectType 扁平，无 subClassOf/等价/不相交/属性继承 | `modeling/elements/concept`、`spec/owl`、`modeling/elements/axiom` |
| P0 | OWA/CWA `profile` 声明无任何运行时行为 | `ontology-model`、`modeling/method/quality`（红线：OWA/CWA 不能混用） |
| P1 | 约束不可共享：facets 只在内核内部生效，无 SHACL 形状导出/校验/导入 | `spec/shacl`、`application/rules-validation` |
| P1 | 查询表达力弱：`filters+expand`、单跳 related；无多跳/可选/聚合/SPARQL | `spec/sparql`、`application/knowledge-graph` |
| P1 | 推理缺失：无 RDFS/OWL-RL、无声明式规则、无物化派生 | `impl/arch-pattern/inference` |
| P1 | 语义检索弱：`SemanticIndex.resolve` 子串匹配，无 embedding/GraphRAG；Ask 启发式 | `application/semantic-search` |
| P1 | 单 workspace 单本体、无模块化/导入/命名空间/IRI | `modeling/elements/module`、`ontology-model` |
| P2 | 版本 diff/impact 无 API；provenance 只到对象级；无 valid-time 历史；LLM 无强 grounding | `impl/arch-pattern/versioning`、`application/lifecycle`、`application/semantic-search` |

---

## 1. 问题清单（P0 / P1 / P2，含证据）

### P0-1 无语义标准互操作，本体资产不可交换

- **现状**：全仓（`src/ tests/ scripts/ examples/`，排除 `ontology-guide/`）grep：`rdf=0`、`skos=0`、`shacl=0`、`sparql=0`、`jsonld=0`、`turtle=0`；`owl=14` 全部是 `showLabels` / `allowlist` 等误命中；`pyproject.toml` 运行依赖 `dependencies = []`，无 `rdflib`。
- **接口证据**：`interfaces/api.py:99`（安全决策导出 JSONL）、`interfaces/api.py:113`（`state/export` JSON/CSV）——只有**运行时状态**导出，没有**本体（TBox）导出**、没有导入，也没有 content-negotiation。
- **影响**：平台本体不能交付给数据治理/数据目录/图数据库/BI/其他系统复用；无法与行业标准本体（Schema.org、FOAF、行业词表）对齐；也无法用现成工具（Protégé、pyshacl、Jena/Fuseki）审查。这正是指南 `application/integration` 所说的“语义集成与互操作”缺位。
- **指南依据**：`spec/rdf.md`、`spec/owl.md`、`spec/skos.md`、`application/integration.md`。

### P0-2 类层次与公理缺失（扁平对象模型）

- **代码证据**：`domain/ontology/model.py:121-159` 的 `ObjectTypeSpec` 只有 `name/description/properties/identity/category/state_machine`，**没有父类/等价类/不相交/属性继承**；`domain/ontology/validation.py:30-92` 的发布校验只做引用完整性、identity、facets、基数、profile、类别（无公理可校验）。
- **影响**：无法表达“货车是车辆的子类”“合同与订单互斥”“某个属性在子类上收紧”等 TBox 知识；子类实例查询只能逐类型 `find`；无法导出为 `rdfs:subClassOf`/`owl:equivalentClass`/`owl:disjointWith`；指南 `modeling/elements/concept.md` 的“概念与类”方法论和 `spec/owl.md` 的类公理无法落地。
- **指南依据**：`modeling/elements/concept.md`、`modeling/elements/axiom.md`、`spec/owl.md`。
- **注**：这不是“再补一个字段”能解决的，必须同时定义：编译规则（属性继承如何进入扁平的内核 schema）、查询规则（子类实例如何 union）、导出规则（OWL 表达式），见 §4 Phase A1。

### P0-3 OWA/CWA `profile` 只是标签，没有行为

- **代码证据**：`domain/ontology/model.py:239` 声明 `profile: str = "closed"`；`domain/ontology/compiler.py:39` 原样编译进 schema；内核 `ontoRK/src/ontology_runtime/loader/__init__.py:191-195` 只校验“非空字符串”，`runtime.py:83` 原样回显；全仓 grep `profile` 无任何**行为分支**（只有创建/编辑/编译/回显）。`find_objects(report=True)` 的 `excluded_missing` 仅是计数（内核 `runtime.py:167-172`），不改变“未命中=不存在/未知”的语义。
- **影响**：指南红线“开放世界与封闭世界不能混用”在当前实现中**无法被表达**。`find` 返回空到底意味着“确实没有”还是“属性缺失/过滤写错”没有区分；Agent/控制回路会把“未知”判成“不存在”。
- **指南依据**：`ontology-model.md`（OWA/CWA 章节）、`modeling/method/quality.md`（红线）。
- **对比**：项目自己的 `docs/plan-v2.md` §3.1 已给出正确的 profile 三元组设计（object_existence / property_completeness / unknown_value），但只落地为一个字符串。

### P0-4 示例模型自身带有指南警告的反模式（且 lint 未阻止发布）

- **实证**：对 `examples/toll/expected-ontology.json` 直接跑 `validate_ontology` 与 `lint_ontology`：
  - `validate_ontology` → `[]`（能发布）；
  - `lint_ontology` → 2 条 `duplicated-endpoint`：
    - `TollTransaction.entry_station` “looks like a direct reference to TollStation while relation 'exits' already models that endpoint; keep one representation”
    - `TollTransaction.exit_station` 同上。
- **其它建模问题**：`TollStation.status` 用 `string` + 描述写枚举值（`free/busy/congested`）而没有 `enum`；`TollTransaction.fee` 无 `unit`；四个对象均无 `identity`；`Lane.station_id` 也是属性化外键。
- **影响**：官方示例即示范“属性即外键/字符串枚举/无单位/无身份”，与指南 `modeling/method/quality.md` 的反模式清单直接冲突；lint 只 warning 不阻断（`ontology_usecases.py:755-769`），示例永远不会被修。
- **指南依据**：`modeling/method/quality.md`、`modeling/elements/relation.md`、`modeling/elements/attribute.md`。

### P1-1 约束不可共享（有强制、无 SHACL 形状层）

- **正面**：facets 是**真强制**——内核 `schema.py:133-200`（`PropertyDef`）与 `runtime.py:507`（`_validate_property_map`）、`608`（`_check_unique_conflicts`）、`662`（`_check_state_transitions`）、`800`（`_check_cardinality`）在写路径校验；平台侧 `tests/test_constraints_platform.py` 通过真实网关验证。
- **缺**：这些约束只存在于“平台模型 + 内核 schema”，无法导出为 SHACL 形状给数据治理/数据接入方复用，也无法导入外部 SHACL 约束；跨系统约束一致只能靠人工对齐。
- **指南依据**：`spec/shacl.md`、`application/rules-validation.md`。

### P1-2 查询表达力只覆盖“单类型过滤 + 单跳扩展”

- **代码证据**：`domain/application/model.py:56-105` 的 `QuerySpec` = `filters` + `expand`；内核过滤器只有 `eq/ne/gt/gte/lt/lte/contains/in`（`ontoRK/src/ontology_runtime/infrastructure/filters.py:6-8`）；`RuntimeGateway.related` 是**单跳**（`infrastructure/runtime/kernel_gateway.py:308`）。
- **影响**：多跳路径、可选模式、聚合、`UNION`、属性路径、联邦查询都无法表达；`application/knowledge-graph.md` 的图谱探索与 `spec/sparql.md` 的查询能力存在代差。
- **指南依据**：`spec/sparql.md`、`application/knowledge-graph.md`。

### P1-3 语义检索与 RAG 是子串匹配，不是语义检索

- **代码证据**：`domain/intelligence/model.py:28-35` 的 `SemanticIndex.resolve` 对 term/alias 做 `in` 子串判断；`application/intelligence_usecases.py` 的 `AskOntology` 用命中的概念 + 字面匹配 `_mentions` + 一跳关联拼答案（无 LLM、无向量、无排序打分）。
- **实证**：对 toll 本体执行 `resolve("济南西收费站为什么拥堵？")` → `['收费站', '拥堵']`；`"哪些车欠费超过3天？"` → `['车辆']`（“欠费”没有对应概念时无降级/澄清）；`"通行费是多少"` → `['通行费', '通行']`（术语重叠、无消歧）。
- **缺**：无 embedding/向量索引、无混合检索、无 GraphRAG（指南明示 GraphRAG 是新兴实践，可作为可选项）、无答案 citation/grounding 校验；`AgentRun.verified` 只对 capability 的验收断言生效（`application/agent_runner.py:34-67`、`202-225`）。
- **指南依据**：`application/semantic-search.md`、`application/knowledge-graph.md`。

### P1-4 单 workspace 单本体，无模块化 / 复用 / 外部引用

- **代码证据**：`application/ontology_usecases.py:221-225` 明确 “this workspace already has an ontology; v0.1 supports one …”；`_load_workspace_ontology` 只取一个活动本体；`VocabularyTerm`（`domain/ontology/model.py:196-222`）只有 `term/concept/kind/aliases`，无 SKOS `broader/narrower/related`、无多语言标签、无外部词表 URI。
- **影响**：无法做“领域本体拆分 + 依赖引用”（指南 `modeling/elements/module.md`），无法引用 Schema.org/FOAF/行业词表，无法做本体的版本化组件复用。
- **指南依据**：`modeling/elements/module.md`、`ontology-model.md`。

### P1-5 无推理（连 RDFS 子集也没有）

- **证据**：grep `inference/reasoner` 在 `src/` 为 0；`materialize*` 的命中全部是“能力实现代码物化”（如 `compiler.py:107` 的 “materialized entrypoint”），与本体物化无关；无 `subClassOf` 传递、无 inverse 使用、无等价类推理。
- **影响**：本体的“可推理”承诺无法兑现；关系反向导航、子类实例、属性继承都要调用方自己拼；`impl/arch-pattern/inference.md` 的分层（RDFS/OWL-RL/规则/物化策略）完全没有对应实现。
- **指南依据**：`impl/arch-pattern/inference.md`、`spec/owl.md`。

### P2-1 版本演进有“兼容矩阵”，缺“diff / impact 报告”

- **正面**：`domain/ontology/compat.py:31-109` 已实现收窄变更矩阵（删类型/改 identity/改类型/required/枚举收窄/范围收窄/unique/基数收窄），`PublishOntology` 在发布前强制（`ontology_usecases.py:564-650`），`ReviseOntology` 做版本链（`652-696`），`schema_fingerprint` 记录发布产物。
- **缺**：没有 `diff(from,to)` / `impact` 查询与 UI；迁移建议仍是 markdown 手工流程；没有“受影响对象数/受影响能力数/受影响视图数”的量化报告。
- **指南依据**：`impl/arch-pattern/versioning.md`。

### P2-2 provenance 与时间语义不足

- **正面**：内核 `core/state.py:33-35` 每个对象带 `version / updated_at / source`；变更流可通过 `observe_changes` 获取。
- **缺**：属性级最后写入者、来源系统、导入批次；投影没有“源行 → 语义对象”的 row-level lineage；没有 valid-time/transaction-time 历史（`state.py:9` 明确 History 只在 change log），`temporal: snapshot` 只被 lint 提示需要 `valid_from`（`validation.py:230+`），内核不产生历史快照。
- **指南依据**：`impl/arch-pattern/versioning.md`、`application/data-governance.md`。

### P2-3 LLM/Agent 语义正确性缺“可验证 grounding”

- **现状**：Agent 循环有工具调用与 `verified/termination` 设计（`agent_runner.py`），但“验证”只覆盖 capability 的验收断言；Ask 是确定性启发式；LLM 提示上下文来自 `SemanticIndex.context_text`（`domain/intelligence/model.py:37-75`，包含 identity/enum/unit/temporal/cardinality，值得肯定），但**不包含公理/子类/约束形状**，也不强制回答引用概念或出处。
- **影响**：答案可读但不可审计；「AI 在同一张本体上工作」目前只到“把 schema 塞进 prompt”，还没有“本体约束校验 + citation + 可执行验证”的闭环。
- **指南依据**：`application/semantic-search.md`、`application/rules-validation.md`。

### P2-4 文档与实现边界的同步风险

- `README.md` 与 `docs/plan-v2.md`、`docs/progress-v0.3.md` 声称的能力与代码基本一致，但**缺少一份明确的“本体能力边界/不支持清单”**（例如：不支持 OWL/RDF、不支持类层次、不支持 SPARQL、profile 无语义）。指南读者/交付方容易误判平台能力。
- **建议**：在 `README` 增补“能力矩阵（支持/部分/不支持）”，并与本报告互链。

---

## 2. 指南 43 节对照矩阵（关键行）

| 指南章节 | 平台现状 | 判定 |
|---|---|---|
| `ontology-model.md` 本体 vs 数据库 Schema、OWA/CWA | 模型有对象/属性/关系/词表；profile 仅声明 | ⚠️ 部分 |
| `spec/rdf.md` RDF 数据模型 | 无三元组/IRI/序列化 | ❌ 缺 |
| `spec/owl.md` OWL 2（类/属性/公理/键） | 扁平类型；无类公理、无 hasKey | ❌ 缺 |
| `spec/skos.md` 词表 | Vocabulary 是 term→concept 映射，非 SKOS | ❌ 缺 |
| `spec/shacl.md` 形状约束 | facets 内核强制；无 SHACL 形状层 | ⚠️ 部分 |
| `spec/sparql.md` 查询 | filters + 单跳 expand | ❌ 缺 |
| `modeling/method/*` CQ/流程/质量 | CQ 未见实现；lint 只告警；质量反模式在示例中出现 | ⚠️ 部分 |
| `modeling/elements/concept` 概念与类 | 有 ObjectType；无子类/等价/不相交 | ❌ 缺 |
| `modeling/elements/relation` 对象属性 | source/target/cardinality/inverse（真强制） | ✅ 基本 |
| `modeling/elements/attribute` 数据属性 | 7 种类型 + facets（required/enum/range/unit/precision/unique/temporal） | ✅ 基本 |
| `modeling/elements/individual` 个体 | 运行期对象；identity 唯一性内核强制；category object/event/value | ✅ 基本 |
| `modeling/elements/axiom` 公理 | 无 | ❌ 缺 |
| `modeling/elements/constraint` 约束 | facets + 状态机 + 基数（内核执行） | ⚠️ 部分（缺声明式规则/共享形状） |
| `modeling/elements/module` 模块化 | 单本体；无 import/namespace | ❌ 缺 |
| `modeling/patterns` 设计模式 | 只有 duplicated-endpoint 等少量 lint | ⚠️ 部分 |
| `impl/arch-pattern/layered` 分层 | DDD 分层 + 防腐层编译，做得扎实 | ✅ 强 |
| `impl/arch-pattern/storage` 存储 | sqlite/memory；无 RDF 存储/三元组库 | ⚠️ 部分 |
| `impl/arch-pattern/inference` 推理与物化 | 只有投影物化，无本体推理 | ❌ 缺 |
| `impl/arch-pattern/versioning` 版本演进 | 兼容矩阵/修订链/指纹；缺 diff/impact | ⚠️ 部分 |
| `impl/objects` 实现本体对象 | 内核对 Object/Relation/Function 有实现 | ✅ 基本 |
| `impl/tech/*` 技术栈 | Python 平台 + Python 内核 | ✅ 强 |
| `application/knowledge-graph` KG 构建 | 投影生成对象与边；无标准 KG 导出/实体消解 | ⚠️ 部分 |
| `application/semantic-search` 语义检索与 RAG | 子串匹配；无 embedding/GraphRAG | ❌ 弱 |
| `application/data-governance` 数据治理 | 权限/审计/生命周期/投影模式；缺 lineage | ⚠️ 部分 |
| `application/rules-validation` 规则与校验 | 内核 facets/状态机；缺声明式规则 | ⚠️ 部分 |
| `application/integration` 语义集成互操作 | 无标准导入导出 | ❌ 缺 |
| `application/lifecycle` 运营与生命周期 | 生命周期状态机 + 发布环 + 回滚 | ✅ 基本 |
| `adoption/index.md` 落地路线图 | 有 `docs/plan-v2.md`；缺“按指南能力”的对外表述 | ⚠️ 部分 |

---

## 3. 完善方案

### 3.1 目标能力分层

```
┌─────────────────────────────────────────────────────────────┐
│ 应用 / Agent（Ask、View、Capability、Smart-UI）              │
├─────────────────────────────────────────────────────────────┤
│ 语义检索与 RAG：概念检索 + 混合检索（BM25/向量）+ 约束过滤     │  ← 新增
├─────────────────────────────────────────────────────────────┤
│ 查询与推理：QuerySpec v2（多跳/可选/聚合）｜SPARQL 只读快照    │  ← 新增
│             可选 RDFS/OWL-RL 物化（feature flag）             │
├─────────────────────────────────────────────────────────────┤
│ 标准语义层：IRI/命名空间｜TBox 公理｜RDF/OWL/SKOS 导出        │  ← 新增（本方案核心）
│             SHACL 形状导出/校验｜（后期）OWL/SKOS 导入        │
├─────────────────────────────────────────────────────────────┤
│ 平台本体模型（ObjectType/属性/关系/词表/约束）                │  现有，补类层次/profile 语义
├─────────────────────────────────────────────────────────────┤
│ Kernel：Schema/State/Function/Change（事实来源，不 import）    │  现有，保持稳定
└─────────────────────────────────────────────────────────────┘
```

**原则（必须保留的现有设计约束）**：
1. `Kernel` 是唯一事实来源，平台不缓存业务状态（README 设计原则 1）；
2. 平台不 import Kernel，只有 `KernelRuntimeGateway` 认识两个世界（原则 3）；
3. 新增标准层**只做读导出优先**，不做“双写”；ABox 导出从 kernel `state/export` 快照来，避免第二事实来源；
4. 零依赖核心不破：`rdflib` / `pyshacl` 作为**可选 extra**（`pip install -e ".[semantic]"`），核心 `dependencies = []` 不变。

### 3.2 新增包 `src/onto_platform/semantic/`（建议结构）

```text
semantic/
├─ iri.py            # base IRI 规则、term IRI、prefix map、冲突检测
├─ tbox.py           # 类层次/公理/键的 TBox 视图（从 Ontology 聚合派生，只读）
├─ rdf_export.py     # TBox+ABox → Turtle / JSON-LD（可选 rdflib，或自写 N-Triples 最小实现）
├─ owl_export.py     # ObjectType→owl:Class、Relation→owl:ObjectProperty(domain/range)、
│                    # 属性→owl:DatatypeProperty、基数→owl:Restriction、enum→owl:oneOf
├─ skos_export.py    # Vocabulary→skos:Concept，aliases→skos:altLabel，层级→skos:broader
├─ shacl_export.py   # facets → sh:NodeShape/PropertyShape（required/min/max/unique/enum/unit）
└─ sparql.py         # 快照 SPARQL（rdflib Graph，只读；或转发外部 Fuseki）
```

**接口契约（新增）**：

| 接口 | 说明 | 验收 |
|---|---|---|
| `GET /api/workspaces/{wid}/ontology/export?format=turtle\|jsonld` | 导出当前发布版本的 TBox（+可选 ABox） | `rdflib.Graph().parse()` 成功；三元组数/关键语句断言 |
| `GET .../ontology/export?format=shacl` | 导出 SHACL 形状 | `pyshacl.validate(abox, shapes)` 对样例数据给出预期违规 |
| `GET .../ontology/diff?from=vX&to=vY` | 引用 `compat.check_compatible` + 新增“影响面”统计 | 新增/收窄/破坏性变更三类用例 |
| `POST .../ontology/validate?shacl=...` | （Phase B 后）用外部形状校验 | 违规/通过两组用例 |
| CLI：`onto ontology export --format turtle` / `onto ontology diff` | 离线导出 | 命令输出可被 rdflib 解析 |

### 3.3 分阶段路线图

> 估算按“一名熟悉本仓的后端工程师”计，含测试；每阶段可独立交付、独立验收。

#### Phase A｜语义严谨性收口（2–3 周，先做，不引入新依赖）

| # | 工作项 | 改动点 | 验收（可测） |
|---|---|---|---|
| A1 | **类层次最小集**：`ObjectTypeSpec.supertype(s)`、环检测、属性继承（编译期展开，内核保持扁平）、子类实例查询 union | `domain/ontology/model.py`、`validation.py`、`compiler.py`、`application/ontology_usecases.py` | 新增 `test_class_hierarchy.py`：子类继承父类属性；环被拒；`find("Vehicle")` 含 Truck 实例；导出前 lint 通过 |
| A2 | **profile 语义化**：采用 `docs/plan-v2.md §3.1` 的三元组（object_existence/property_completeness/unknown_value），落地 `find` 的“未命中/未知”区分与 `unknown_fields` 返回 | `domain/ontology/model.py`、`validation.py`、`compiler.py`、网关读路径（平台层先实现；如内核需透传字段再单独做兼容扩展） | `test_profile_semantics.py`：closed 未命中 vs open 未知；`get` 区分 missing/null；Agent 观察遵循该语义 |
| A3 | **lint 门禁化**：`duplicated-endpoint` 等反模式在 publish 时默认拦截（或 workspace 配置为 warning）；示例仓库纳入 lint 门禁 | `validation.py`、`ontology_usecases.py`、`scripts/` CI | `test_lint_gate.py`：示例本体发布被拒并给出可操作提示；修复后通过 |
| A4 | **示例校准**：toll/finance_lease 改为关系替代外键属性、status 用 enum、fee/金额用 unit、补 identity、补 datetime | `examples/toll/expected-ontology.json`、`examples/finance_lease/bootstrap.py`、文档 | `test_toll_sample.py` 扩展：示例 `validate_ontology()` 与 `lint_ontology()` 均为空 |

#### Phase B｜标准语义层（3–4 周，核心价值）

| # | 工作项 | 改动点 | 验收 |
|---|---|---|---|
| B1 | IRI/命名空间体系：`workspace → base IRI`、`ObjectType→{base}#Class`、版本化 IRI、冲突检测 | 新增 `semantic/iri.py`；`Ontology` 增 `base_iri`/`namespace` | IRI 稳定性测试（版本变化不换 IRI；非法字符被规范化） |
| B2 | TBox 导出：OWL 类/对象属性/数据属性/domain/range/基数限制/enum（oneOf）/identity（hasKey） | `semantic/owl_export.py`、`semantic/rdf_export.py`、API/CLI | `rdflib` 解析 + 关键三元组断言；Protégé 可打开（人工验收） |
| B3 | SKOS 导出：Vocabulary→Concept/altLabel（无 broader 时输出 flat，预留） | `semantic/skos_export.py` | 词表 15 条 → 15 个 skos:Concept + altLabel 数一致 |
| B4 | SHACL 导出：required/min/max/unique/enum/unit/precision → 形状 | `semantic/shacl_export.py` | `pyshacl` 对好/坏两组数据：0 违规 / 预期违规 |
| B5 | API/CLI/控制台下载按钮：`/ontology/export` + `onto ontology export` + “下载本体（Turtle/JSON-LD）” | `interfaces/api.py`、`interfaces/cli.py`、`frontend`（可选） | HTTP 200 + `Content-Type: text/turtle`；错误码映射 |
| B6 | （可选，后期）OWL/SKOS subset 导入 + 映射治理 | `semantic/import.py`、冲突报告 | 导入外部本体后 lint/冲突报告符合预期 |

#### Phase C｜查询与推理（3–4 周）

| # | 工作项 | 改动点 | 验收 |
|---|---|---|---|
| C1 | `QuerySpec v2`：多跳路径、可选字段、聚合（count/sum）、结果排序/分页；编译为 kernel find/related 组合 | `domain/application/model.py`、`application/application_usecases.py` | 10 组查询对照期望（含多跳 2 跳） |
| C2 | 只读 SPARQL（快照）：把 Phase B 导出的 Graph 加载进内存/外部端点执行 SELECT | `semantic/sparql.py`、API `POST /ontology/query` | 3 条基准查询（含子类、反向关系）结果断言 |
| C3 | 可选推理：RDFS/OWL-RL 子集（inverse、子类、传递属性），物化到 kernel 的 feature flag（默认关） | 新增 `semantic/inference.py`，能力侧注册派生关系 | 派生关系在 `find/related` 可见；关闭时不产生物化；幂等/重放安全 |
| C4 | 声明式业务规则：`state_machine` 已有；补跨对象规则（如“欠费超 3 天禁止上高速”）用规则 DSL/SHACL-AF 表达并接入 verifier | `domain/ontology` 规则段、`application/verifier.py` | 规则命中/未命中与调用拒绝/警告用例 |

#### Phase D｜语义检索与 AI grounding（3 周）

| # | 工作项 | 验收 |
|---|---|---|
| D1 | 检索基线：BM25（零依赖实现）+ 概念约束（命中概念 → 类型过滤），替代纯子串；`SemanticIndex` 保留 | 10 条问题召回率对比（基线 vs 新）|
| D2 | 可选向量：`LLMProvider` 或本地 `sentence-transformers` 生成 embedding，混合排序；`pip install -e ".[semantic]"` | 离线 mock 下工具测试；不引入默依赖 |
| D3 | LLM grounding：context 注入公理/子类/约束；回答必须引用概念与对象 ref；`AgentRun.verified` 扩展为“引用可校验” | 幻觉样本（问不存在属性）被拒/标注未知；verified 覆盖 |
| D4 | GraphRAG pilot（feature flag，指南明确为新兴实践） | 与 D1 基线对比报告，默认关闭 |

#### Phase E｜治理与质量（2 周）

| # | 工作项 | 验收 |
|---|---|---|
| E1 | diff/impact API + 控制台展示（复用 `compat.check_compatible`，统计受影响对象/能力/视图） | 用例断言新增/收窄/破坏三类 |
| E2 | provenance 扩展：属性级最后写入、投影 row-level lineage、导入批次；`state export` 带来源 | 对象/属性/行三级来源可追溯 |
| E3 | 质量门禁：round-trip（导出→解析→再导出稳定）、SHACL 一致性、SPARQL 快照回归、导出大对象性能（10 万实例分页 < X 秒） | CI 全绿；性能基准记录 |

### 3.4 验收定义（DoD）

1. **导出可解析**：Turtle/JSON-LD 能被 `rdflib`（可选）与 Protégé 打开；关键构造（类、对象属性、domain/range、基数、enum、SKOS、hasKey）有断言覆盖。
2. **约束可共享**：SHACL 形状导出后，`pyshacl` 对“好数据 0 违规 / 坏数据命中预期形状”成立。
3. **OWA/CWA 有行为**：closed/open 两种 profile 下 `find`/`get`/Agent 观察返回值不同，且测试断言清晰。
4. **类层次可用**：子类继承属性、子类实例查询、环检测、导出 `rdfs:subClassOf` 全部通过。
5. **不破坏既有契约**：599 个既有测试保持全绿；kernel `parse_schema` 兼容（新增字段可选、默认省略）；`pytest --strict-markers` 全绿。
6. **文档同步**：`README` 能力矩阵 + 本审计报告链接；`docs/plan-v2.md` 状态更新。

### 3.5 风险、兼容与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| 双表示漂移 | TBox 导出与平台模型可能不一致 | **单向导出**（平台为源）；导出时记录 `schema_fingerprint`/版本到图元数据；CI round-trip 校验 |
| 类层次与扁平内核冲突 | 内核 schema 无子类概念 | 编译期**属性继承展开**（内核仍扁平）+ 平台层查询 union 子类；OWL 导出保留层次；文档明确“内核不承载类公理” |
| 可选依赖 | 核心零依赖是设计原则 | `rdflib`/`pyshacl` 作为 extra；无 extra 时导出用自写最小 N-Triples 序列化（无解析器），校验不可用则明确报错 |
| OWA 行为变更 | 影响 Agent/控制回路判断 | 默认 `closed`；open 需显式选择；灰度开关 + 测试 |
| 性能 | 状态全量导出大 | 分页/流式导出；TBox 导出与 ABox 分离；快照缓存 |
| 全 DL 推理/上层本体 | 指南明确不要过度设计 | 不做完整 DL/BFO/DOLCE；只做 RDFS/OWL-RL 子集 + SHACL；GraphRAG 作为 flag |

**明确不做（本期）**：完整 OWL 2 DL 推理、BFO/DOLCE/SSN 等上层本体对齐、任意 OWL 导入、把内核改成 RDF 存储、把 `rdflib` 变为默认依赖。

### 3.6 立即可做（1–2 天，quick wins）

1. **修示例**：`examples/toll/expected-ontology.json` 的 `status` 加 `enum`、`fee` 加 `unit: CNY`、补 `identity`、把 `entry_station/exit_station` 改为关系端点；同步 `test_toll_sample.py`。
2. **profile 语义决策**：在 `docs/plan-v2.md` 基础上确定三元组字段并加 `test_profile_semantics.py`（先红后绿）。
3. **最小 TBox 导出**：无依赖手写 N-Triples 导出（类/属性/关系/词表），先加 CLI `onto ontology export --format nt`，不等 rdflib。
4. **diff 复用**：加 `GET /ontology/diff?from&to`，内部复用 `check_compatible` + 新增影响统计。
5. **README 能力矩阵**：把“支持/部分/不支持”写清楚（含 RDf/OWL/SHACL/SPARQL/类层次/推理），避免交付误解。

---

## 4. 附录

### A. 关键证据索引

| 结论 | 证据 |
|---|---|
| 扁平对象模型、无类层次/公理 | `domain/ontology/model.py:121-159`；`validation.py:30-92` |
| 无 RDF/OWL/SHACL/SPARQL | 全仓 grep（排除 ontology-guide）：rdf/skos/shacl/sparql/jsonld/turtle = 0；owl 14 全为误命中；`pyproject.toml` dependencies=[] |
| 无本体导出/导入接口 | `interfaces/api.py:99,113` 仅安全决策/状态导出 |
| profile 只声明无行为 | `model.py:239`、`compiler.py:39`、kernel `loader/__init__.py:191-195`、`runtime.py:83`；全仓无 profile 分支 |
| 内核真实强制 facets/基数/状态机/identity | kernel `schema.py:133-200`、`runtime.py:507/608/662/728/800`；`tests/test_constraints_platform.py` |
| 版本兼容矩阵与修订链 | `domain/ontology/compat.py:31-109`；`ontology_usecases.py:564-650/652-696` |
| 单 workspace 单本体 | `ontology_usecases.py:221-225` |
| 词汇表非 SKOS | `model.py:196-222` |
| 语义检索为子串匹配 | `intelligence/model.py:28-35`；实测 `resolve()` 输出 |
| 查询表达力 | `application/model.py:56-105`；kernel `filters.py:6-8`；`kernel_gateway.py:308` |
| Agent 验证范围 | `agent_runner.py:34-67/202-225` |
| 示例反模式 | `examples/toll/expected-ontology.json`；`lint_ontology` 实测 2 条 duplicated-endpoint |
| 治理/生命周期 | `domain/governance/model.py`（draft→validated→published→deprecated；角色矩阵） |
| 测试基线 | `python -m pytest -q` → 599 passed / 53.87s |

### B. 与项目自身计划的衔接

`docs/plan-v2.md` 已正确识别“轻量对象图不是可判定本体”，并完成 O-1..O-12 的大部分（identity、facets、cardinality、profile 声明、版本矩阵、lint）。本报告的增量在于：**指南视角的标准语义层（RDF/OWL/SKOS/SHACL/SPARQL）、类层次/公理、推理、语义检索**——这些在 plan-v2 中属 Phase 2 之后或未覆盖。建议把本报告 §3.3 的 Phase A–E 并入 `docs/plan-v2.md` 的路线图，并保持其 W0–W5 的测试优先原则。

### C. 建议的下一步

- 若只做一件事：**Phase A（语义严谨性收口）**，它不引入新依赖、直接提升模型质量，并为 Phase B 的 OWL/SHACL 导出打好语义基础。
- 若需要立刻对外交付“标准本体资产”：优先 **B1+B2（IRI + Turtle/OWL 导出）**，2 周内可交付。
- 建议在 `README` 明确当前边界，避免把“可运行对象图”误认为“标准本体平台”。

### D. 审计局限

本报告基于代码阅读、单元测试基线与小范围运行探针（lint/词表解析），**未做**完整端到端压测、未在真实生产数据规模上验证导出/推理性能、未审计前端与 Smart-UI 的语义正确性。Phase B/C 的工期与性能结论需在实施时用真实数据复核。
