# 标准适配插件 P0 完成度复核与补全计划

> **复核对象**：`docs/standards-plugin-architecture.md`（设计 SSOT）、`docs/standards-plugin-implementation-plan-v1.md`（P0 计划）、`docs/standards-plugin-implementation-report.md`（独立验证），以及仓库中的实现与测试。
> **复核时间**：v0.3 工作区（未提交状态）。
> **结论摘要**：P0 **功能闭环可用**（真能导出正确、确定、可解析的 Turtle；零依赖；既有 599 测试无回归），但按架构文档的 P0 合同与 DoD，**完成度约 72%**：契约字段（manifest）、插件调用隔离、CI 中的 interop 验证、golden/数量断言、文档同步仍缺失；治理（workspace 启停/健康/重载）与 SHACL 属 P1，未做。**建议先做 C0（~1 天）+ C1（~2–3 天）把 P0 收口，再进入 C2/P1。**

---

## 1. 复核基线与方法

### 1.1 基线（复核时实测，非引用报告）

| 项目 | 结果 |
|---|---|
| 全量测试 | `python -m pytest -q` → **715 passed, 0 skipped, 60.93s**（599 基线 + 116 新增） |
| 覆盖率（CI 门禁口径，补装 pytest-cov 后） | **79.75%** ≥ 70%；`semantic/` 各模块 83–100%（spi 83%、iri 96%、context 100%、registry 93%、turtle 93%、owl 96%、skos 93%） |
| 插件发现 | `default_registry().names() == ["owl-rdf", "skos"]`，两者 `status=ready`、`problems=[]` |
| 依赖边界 | `[project] dependencies = []`；新增 `interop = ["rdflib>=7", "pyshacl>=0.29"]` |
| 工作区 | 未 commit；`ontology-guide/*` 改动为既有 PDF 功能，与本次无关 |

### 1.2 复核方法

1. 以**架构文档**（`standards-plugin-architecture.md`）为合同，逐条对照 §2.2/§2.3/§2.4/§2.5/§4/§5/§6/§7/§8；
2. 阅读全部新增实现（`semantic/*.py`、`standards_usecases.py`、API/CLI 接线、pyproject/CI）与全部新增测试；
3. 用只读探针验证发现结果、覆盖率、依赖边界与路由行为；
4. **审计报告的 B2 已过时**：`ontology-implementation-review-v1.md:231` 要求“OWL 导出基数限制/enum/hasKey”，这与架构 §2.5 红线（OWL 只写结构，约束归 SHACL）**直接冲突**；实现按架构执行是正确的，复核时以架构为准，并建议给审计 B2 加“已被架构 §2.5 取代”的注记。

---

## 2. 完成度总览

| 领域 | 架构/P0 要求 | 实现证据 | 完成度（估） | 判定 |
|---|---|---|---|---|
| SPI / manifest 契约 | Protocol、manifest、Artifact/Report/Result、能力裁剪 | `semantic/spi.py`：Protocol 与返回类型齐；manifest 缺 `formats`（嵌套）、`requires`、`config_schema` | **65%** | ⚠️ 契约字段不完整 |
| 确定性 IRI | base/term/版本 IRI/冲突检测 | `semantic/iri.py`：稳定、可复现、冲突检测；base 与架构 §5 写法不同 | **90%** | ⚠️ 文档待对齐 |
| AdapterContext | 只读 TBox（+ABox 占位）、IriPolicy、actor/scope/version | `semantic/context.py`：deepcopy 快照 + MappingProxyType + compiled_schema；ABox 仅占位（P0 设计如此） | **85%** | ✅（ABox 按 P2） |
| Registry / 发现 | builtin + entry points、失败隔离、健康状态 | `semantic/registry.py`：发现/注入/隔离/problems/invalid 列表齐；缺能力-方法一致性检查、workspace 启停 | **80%** | ⚠️ 治理待 P1 |
| 导出插件 | OWL 结构 + SKOS 词表，TBox，Turtle | `owl.py`/`skos.py`：红线与确定性达标；无类层次（模型无 parent）、无 JSON-LD、无语言标签、`skos:definition` 因模型无 description 永不输出 | **75%** | ⚠️ 语义子集可用 |
| Use case / REST / CLI | list/export（P0）；validate/query/artifacts 按阶段 | `standards_usecases.py`、`api.py:1193-1218`、`cli.py:228-250,411-418`：list/export 全链路通；缺 validate/artifacts/reload；插件异常未做专门隔离/审计 | **70%** | ⚠️ P0 最小闭环 |
| 插件治理（权限/启停/兼容） | permissions 调用前检查、workspace 启停、requires 兼容、reload | 仅 manifest 校验时拒绝 `network/write=true`；无启停、无 reload、无 requires 检查、无运行时权限检查 | **35%** | ❌ P1 |
| 测试 | 单元/API/CLI/契约/E2E/确定性/红线 | 116 个新增测试，3 路径确定性、9 个红线串、坏插件隔离、E2E socket；缺 golden、关键三元组**数量**断言、跨进程确定性、插件抛异常路径 | **85%** | ⚠️ 强但非完整 |
| CI / 交付验证 | interop extra 解析、门禁 | CI 只装 `[dev]`，rdflib 解析用例在 CI 会 skip；`[dev]` 未含 pytest-cov（既有问题） | **40%** | ❌ CI 未验证关键项 |
| 文档 | 方案/计划/报告/实现状态/路线同步 | plan/report/README/架构 §7 状态已写；架构 §2.2/§4/§5/§6 与实现不一致；`plan-v2.md:194` O-3 未更新 | **60%** | ⚠️ 需同步 |

> **加权判断**：按“架构 P0 合同”口径约 **72%**；按“能不能用”口径约 **90%**。差距集中在契约完整性、治理、CI 与文档，不在核心导出能力。

---

## 3. 已达成（复核确认，不需要重做）

1. **零依赖不破**：核心 `dependencies = []`；`semantic/` 仅标准库 + 平台内部只读模块；rdflib/pyshacl 只在 `[interop]`。
2. **确定性导出**：同一发布版本连续导出逐字节一致（adapter 级 / 独立 context / HTTP 双 GET 三路径验证）。
3. **语义红线**：OWL 产物无 `hasKey`/`owl:cardinality` 系列/`FunctionalProperty`/`someValuesFrom`/`allValuesFrom`/`sh:*`/`Restriction`；结构三元组（Class/ObjectProperty/DatatypeProperty/domain/range/inverse）齐全。
4. **可解析**：装 rdflib 后 Turtle round-trip 通过（原 3 个 skip 全部转 PASS）。
5. **发现与隔离**：builtin + entry-point 注入、坏 manifest 不炸启动、`status=invalid` + `problems` 可见。
6. **接口可用**：`GET …/standards/adapters|export`、`onto standards list|export`、审计事件 `standards.export`、错误映射（404/400）。
7. **回归**：599 基线全绿，全量 715 passed / 0 failed。

---

## 4. 缺失与偏差清单

> 优先级：**S1** = 不修就不能宣称“按架构完成 P0”；**S2** = P0 硬化/短期应收口；**S3** = 属 P1–P3 路线（不是 P0 缺陷）。

### 4.1 契约与 SPI（S1）

| ID | 缺失/偏差 | 证据 | 影响 |
|---|---|---|---|
| G-01 | manifest 与架构 §2.2 不一致：架构是 `formats: {out:[...]}、requires、config_schema`；实现是扁平 `formats_out/formats_in`，且**没有 `requires`/`config_schema`** | `semantic/spi.py:58-84`；架构 §2.2 第 61-75 行 | 第三方插件无法声明平台版本要求/配置；manifest 作为“身份证”不完整 |
| G-02 | 只校验 manifest，不校验**声明的能力是否有对应方法**（`export/validate/query/import_`） | `registry.py:278-308` 仅调用 `manifest_problems` | 一个声明 `validate` 但没实现的插件会被列为 `ready`，能力面撒谎 |
| G-03 | 插件调用**异常未隔离**：`adapter.export` 直接抛到 API 通用 `except`（500），无失败审计；架构要求“结构化 502 + 不影响主流程 + 审计留痕” | `standards_usecases.py:115`；架构 §2.3 调用行、§8 DoD 5 | 坏插件把错误语义混进 500；运维无法从审计定位插件故障 |
| G-04 | `ExportRequest.scope` / `include_abox` **被静默忽略**：adapter 永远导 TBox，传 `scope=abox` 也返回 TBox | `owl.py:123-152`、`skos.py:69-93`；`SCOPE_KINDS` 只在 `build_context` 校验 | 调用方以为导出了 ABox，实际拿到 TBox，静默错误比报错更危险 |

### 4.2 插件命名 / IRI / 产物治理（S1–S2）

| ID | 缺失/偏差 | 证据 | 影响 |
|---|---|---|---|
| G-05 | 插件名与架构 §4 清单不一致：架构 `onto-standards-owl`/`onto-standards-skos`，实现 `owl-rdf`/`skos` | `owl.py:110-121`、`skos.py:56-67`；架构 §4 | 文档/第三方生态按名字对接会失败；需统一口径（改名或改文档，建议保留别名） |
| G-06 | IRI base 与架构 §5/§2.4 写法不同：架构 `https://{workspace}.ontoplatform.local/onto/{version}#`；实现 `https://ontoplatform.local/ws/{workspace}/onto/{name}/v{version}#` | `iri.py:14-15, 56-70, 80-95`；架构 §5 `iri.py` 行 | 实现更安全（含 ontology name、避免 workspace 主机名限制），但**契约文本不一致**，必须先定 SSOT |
| G-07 | 产物 provenance 未按架构 §6 落图：无命名图 `urn:ontoplatform:ws:{wid}:v{version}`；`Artifact.metadata` 缺 `actor`、`ontology_version`、`generated_at` 策略；Turtle 内只有 `owl:versionInfo` 含 fingerprint | `owl.py:141-152`；架构 §6 | 下游拿到文件后无法追溯“谁/哪个平台版本/哪个 workspace 导出的”；与确定性策略（时间戳不进图）需要明确 |
| G-08 | **无类层次**：模型没有 `ObjectTypeSpec.parent`，OWL 无法输出 `rdfs:subClassOf`；SKOS 无 `broader/narrower/topConceptOf` | `model.py` 无 parent 字段（仅 `parent_version` 为版本血缘）；架构 §2.5、审计 P0-2 | 架构承诺的“类层次单向展开”无法兑现；属平台模型（O-3/O-5/P0-2）依赖，不能只改插件 |
| G-09 | SKOS 标签**无语言标签**（`skos:prefLabel`/`altLabel` 为 `xsd:string`）；OWL `rdfs:label` 同 | `skos.py:101-133`、`owl.py:178-202`；`turtle.literal` 已支持 lang 参数但未用 | 中文词表在标准工具里无语言区分，SKOS 质量扣分 |
| G-10 | `skos:definition` **永不输出**：`VocabularyTerm` 模型没有 `description` 字段，`getattr` 永远空 | `skos.py:129-133`；`model.py:196-213` | 词表缺释义；属模型（词表契约 O-5）+ 插件双重依赖 |
| G-11 | 无 JSON-LD：架构 §4 把 `application/ld+json` 列在 P0 owl 插件格式中；实现仅 Turtle | `owl.py:67-68, 114-116`；架构 §4 | 交付格式少于方案；需决定“P0 只 Turtle”还是补 JSON-LD |
| G-12 | **SKOS 插件 import OWL 插件的 `check_export_format`**，插件间横向耦合 | `skos.py:26` | 卸载/替换 owl 插件会连带 SKOS 不可用，违反“插件独立安装/回滚” |

### 4.3 平台接线与治理（S2–S3）

| ID | 缺失/偏差 | 证据 | 影响 |
|---|---|---|---|
| G-13 | API 路由少于架构 §5：缺 `POST /standards/export`（架构写法）、`POST /standards/validate`、`GET /standards/artifacts/{id}`、`POST /standards/reload` | `api.py:1193-1218`；架构 §5 | 校验/重载/产物治理入口缺失；GET vs POST 需统一（实现用 GET 有理由：只读导出） |
| G-14 | Use case 缺 `ValidateStandard` / `QueryStandard` / workspace 启停过滤 | `standards_usecases.py:55-134` 仅 list/export；架构 §5 | 与架构“核心改动清单”不符（P0 可只做 export，但需在文档标 P1） |
| G-15 | CLI 缺 `onto standards validate`；list 无 enabled/health 状态过滤 | `cli.py:228-250,411-418`；架构 §5 | 运维只能用 HTTP；离线校验缺失 |
| G-16 | 无 workspace 级启停、无 `requires.ontoplatform` 兼容校验、无运行时 permissions 检查、无 reload/健康检查 | `registry.py` 全文；架构 §2.3、§8 DoD 6 | DoD 6“可回滚”不成立；企业治理场景不可用 |
| G-17 | `Platform.standards` 为**必填字段**，外部直接构造 `Platform` 的代码会 breaking | `bootstrap.py:176-177` | 公共 API 兼容性风险（仓库内测试全绿，但库使用者会受影响） |

### 4.4 测试 / CI / 验证（S1–S2）

| ID | 缺失/偏差 | 证据 | 影响 |
|---|---|---|---|
| G-18 | **无 golden 文件**，且无“关键三元组数量”断言（QA 仅断言存在与 `len>0`） | `tests/` 无 `*.ttl`；审计 B2/B3 要求数量断言（如 15 词表 → 15 Concept） | 无法防止“结构还在但内容漂移”；架构 P0 第 4 条明确 golden |
| G-19 | **CI 不装 `[interop]`**：`.github/workflows/ci.yml:40` 只 `pip install -e ontoPlantform[dev]`；rdflib round-trip 用例在 CI 会 skip（本地因临时装了 rdflib 才全 PASS） | `ci.yml:32-52` | DoD“CI 用 rdflib 校验产物”在 CI 实际未执行；P0 验收依赖开发者本机状态 |
| G-20 | 无跨进程确定性测试（`PYTHONHASHSEED` 两次子进程字节比对） | `tests/test_standards_contract.py` 确定性仅进程内 | 跨 Python 进程/CI 的字节一致性未证明（风险中低，但架构 §6 要求 diff 为空） |
| G-21 | 无“插件抛异常”路径测试：未验证 502/结构化错误/失败审计/平台不崩 | QA 契约只测了坏 manifest 的**发现**隔离 | G-03 的验收缺口 |
| G-22 | `[dev]` extra 未含 `pytest-cov`，而 CI 用 `--cov` 门禁（本次本地补装才跑通） | `pyproject.toml:24-31`、`ci.yml:51-52` | 既有 CI 风险，会导致平台门禁无法运行（非本功能引入，但影响“验证完成”的判定） |

### 4.5 文档同步（S1）

| ID | 缺失/偏差 | 证据 | 影响 |
|---|---|---|---|
| G-23 | 架构文档 §2.2（manifest 字段）、§4（插件名/JSON-LD）、§5（API 路由/POST/artifacts）、§6（命名图 provenance）与实现不一致 | 架构 §2.2/§4/§5/§6 vs 实现 | 后续开发者/第三方按文档接入会踩空；必须选“改文档”或“改实现” |
| G-24 | `docs/plan-v2.md:194` O-3“命名空间/ID 冲突/撤回”仍是未完成状态；`docs/ontology-implementation-review-v1.md` P0-1 未标注“部分解决”；审计 B2 的 hasKey/基数要求未标注被架构取代 | 上述文件 | 路线图与审计结论仍显示“标准全缺”，与实际交付不一致 |
| G-25 | 缺“能力矩阵/不支持清单”对外说明（哪些已支持、哪些 P1/P2、哪些不打算做） | README 只有 P0 用法；审计 §121 建议 | 交付方容易误判；P1 排期缺共同基线 |

### 4.6 路线图内（非 P0 缺陷，S3）

SHACL 导出/校验、SPARQL 查询、OWL/SKOS 导入、ABox 快照导出/查询、workspace 治理 UI、插件示例与开发文档、语义检索、RDFS/OWL-RL 推理——均属架构 §4/§7 的 P1–P3，**不计入本次 P0 缺口**，但需在补全计划中给出入口与依赖。

---

## 5. 补全计划

> 顺序原则：**先对齐合同（C0）→ 再补 P0 硬缺口（C1）→ 再进 P1 治理/SHACL（C2）→ 最后 P2/P3（C3）**。每个阶段结束都要求全量 + interop 双跑绿。

### C0：立即对齐与低风险修复（约 1–1.5 人日，P0 阻塞项）

| ID | 任务 | 主要文件 | 验收 | 依赖 |
|---|---|---|---|---|
| C0-1 | **确定 SSOT 并同步文档**（与用户决策 1–4 绑定）：manifest 字段、插件名、IRI 方案、API 动词；更新架构 §2.2/§4/§5/§6、`plan-v2.md` O-3、审计 B2 注记、README 能力矩阵 | `docs/*.md` | 文档与实现逐条一致；review 文档中的偏差清零 | 决策 1–4 |
| C0-2 | CI 装 `[dev,interop]`（或新增 interop job）让 rdflib 解析/契约用例真实执行；`[dev]` 补 `pytest-cov`（既有门禁修复） | `.github/workflows/ci.yml`、`pyproject.toml` | CI 日志中 0 skip（或仅显式环境 skip）；coverage 命令可运行 | — |
| C0-3 | 解耦 SKOS→OWL：把 `check_export_format`/`SUPPORTED_FORMATS` 移到 `turtle.py`（或新建 `semantic/formats.py`），两个插件都只依赖 SPI/共享序列化 | `semantic/skos.py`、`owl.py`、`turtle.py` | 删 owl.py 后 skos 单测仍绿；插件独立性测试 | — |
| C0-4 | scope/include_abox 显式拒绝：P0 仅 `tbox`，`abox/both/include_abox=True` 抛 `AdapterError`（或明确“P0 忽略并写 metadata”二选一，需决策） | `owl.py`、`skos.py` | 新增 2 个负例测试；API 400 | 决策 4 |
| C0-5 | 能力-方法一致性校验：注册/发现时按 `capabilities` 断言 `export/validate/query/import_` 可调用，否则 `status=invalid` | `registry.py` | 假插件声明 validate 但无方法 → invalid；现有 ready 项不受影响 | — |
| C0-6 | 补 golden 基线：`tests/golden/standards/owl-traffic-v2.ttl`、`skos-traffic-v2.ttl`（从固定 fixture 生成），测试逐字节比对；补“关键三元组数量”断言（类/属性/关系/Concept/altLabel 计数） | `tests/golden/*`、`tests/test_standards_export.py` | golden diff 为空；数量断言通过 | C0-1 |
| C0-7 | 失败插件调用隔离最小版：use case `try/except Exception` → `AdapterError`（或新增 `AdapterUnavailable` 502），审计 `standards.export.failed`，保留原异常类型/消息 | `standards_usecases.py`、`api.py` | 假插件抛异常 → 结构化 502/400 + 审计 + 平台其他路由正常 | — |

### C1：P0 合同补完（约 2–3 人日）

| ID | 任务 | 验收 |
|---|---|---|
| C1-1 | manifest 扩展：`formats: {out,in}`、`requires: {ontoplatform, extras}`、`config_schema`（保持向后兼容：旧扁平字段保留为属性/别名） | `to_dict()` 同时含架构字段；旧插件不炸；契约测试 |
| C1-2 | `requires` 兼容检查：解析 `>=x.y` 与当前平台版本比较（不引入第三方依赖）；不兼容 → `status=incompatible` + 列表可见 | 单测（兼容/不兼容/无 requires） |
| C1-3 | provenance 元数据：`Artifact.metadata` 增 `workspace_id`、`ontology_version`、`schema_fingerprint`、`adapter_version`、`actor`、`exported_at`（仅 metadata，不进 Turtle 图，保证字节确定性）；API 响应头加 `X-Adapter-Name`/`X-Schema-Fingerprint`/`X-Ontology-Version` | 头/元数据断言；确定性仍通过 |
| C1-4 | 语言标签：`rdfs:label`、SKOS `prefLabel/altLabel` 增加默认语言（中文术语 `@zh`；ASCII 术语不标或 `@en` 可配置），确保确定性 | 单测 + golden 更新；rdflib 解析断言 lang |
| C1-5 | JSON-LD（决策 5）：若保留架构承诺，基于同一 statement 列表输出 `application/ld+json`（紧凑 JSON-LD，手写序列化，零依赖）；若砍掉则更新架构 §4 为“P0 Turtle，P1 JSON-LD” | 二选一；测试/文档同步 |
| C1-6 | 跨进程确定性：`PYTHONHASHSEED=1/2` 各跑一次导出子进程，比对 golden 字节 | 测试通过 |
| C1-7 | 小修：`iri._slug` 的条件写法清理、未使用 `ValidationError` 导入、`Content-Type: text/turtle; charset=utf-8`、非 ASCII filename 的 `filename*` | lint/测试；头断言 |
| C1-8 | `Platform.standards` 兼容：改为可选默认或提供 `build_platform` 外的构造帮助，避免库使用方 breaking（决策 6） | 构造兼容测试；类型检查 |

### C2：P1 治理 + SHACL（约 8–10 人日）

| ID | 任务 | 验收 |
|---|---|---|
| C2-1 | workspace 级启停（settings 存储）：`list` 显示 enabled；disabled → export 明确 404/403 + 审计；CLI/API 管理入口 | 启停/回滚测试；DoD 6 转通过 |
| C2-2 | `POST /standards/reload`（admin）+ `registry.discover()` 热重载；`problems` 暴露到 `/health` 或列表 | reload 后新增/消失插件可见；审计 |
| C2-3 | `requires` 平台版本/extra 提示接入列表与错误信息 | 不兼容插件明确提示缺哪个 extra |
| C2-4 | SHACL 适配器（`export`+`validate`）：required→`sh:minCount 1`、enum→`sh:in`、unique→`sh:maxCount 1`、min/max→`sh:minInclusive/maxInclusive`、datatype→`sh:datatype`、targetClass；`pyshacl` 在 `[interop]` 下校验 | 好/坏两组数据：0 违规 / 预期违规；CI interop job 跑通 |
| C2-5 | `ValidateStandard` + `POST /standards/validate` + CLI `onto standards validate` | HTTP/CLI 各 3 个场景（好/坏/未知 adapter） |
| C2-6 | `GET /standards/artifacts/{id}` 或 ETag/下载缓存策略（需决策 7） | 产物可重复下载、校验和一致 |
| C2-7 | 控制台“下载/校验”按钮（P1 可选） | 手工验收 |
| C2-8 | 第三方插件示例 + 开发文档（`examples/plugins/`） | 示例插件可被 entry point 发现并通过契约测试 |

### C3：P2/P3（约 15–20 人日，另行排期）

1. **ABox 快照**：`AdapterContext.abox` 经 `ExportService` 行级过滤/脱敏，分页/流式；`scope=abox|both`（G-04 的另一半）。
2. **SPARQL 插件**：rdflib 只读图（TBox+快照），LIMIT/超时/白名单，`query` capability。
3. **导入插件**：OWL/SKOS 子集 → `OntologyDraft` patch + 冲突报告（不直接发布）。
4. **模型层（真正的前置）**：`ObjectTypeSpec.parent`、`VocabularyTerm.broader/narrower/related/description`、编译器继承展开、查询 union；解锁 G-08/G-10。
5. **语义检索**（P3，可选）：embedding/GraphRAG 作为独立插件。
6. **RDFS/OWL-RL 推理**（审计 P1-5，feature flag，默认关）。

**依赖关系**：C0 → C1 → C2；C3 中 ABox 依赖 C2 的治理与快照；类层次/SKOS 层级依赖模型工作（与 `plan-v2.md` O-3/O-5、审计 P0-2 同批），**不要**承诺“插件可单独解决”。

---

## 6. 验收与度量（每阶段结束必须重跑）

```powershell
# 1) 全量回归 + 覆盖率（CI 门禁口径）
python -m pytest -q
python -m pytest -q --cov=onto_platform --cov-report=term --cov-fail-under=70

# 2) interop 真实解析（C0-2 后 CI 也执行）
python -m pytest tests/test_standards_contract.py tests/test_standards_export.py -q -rs

# 3) 确定性（C1-6）
$env:PYTHONHASHSEED="1"; python <export_script>
$env:PYTHONHASHSEED="2"; python <export_script>
# 两次输出逐字节相同

# 4) E2E + 错误码
curl ".../api/workspaces/{wid}/standards/adapters"
curl ".../api/workspaces/{wid}/standards/export?adapter=owl-rdf&format=text/turtle"
# 200 / text/turtle / attachment；未知 adapter 404；非法 format 400；无发布本体 404
```

**C0/C1 完成后的目标**：715 + 新增测试全绿、0 环境 skip（CI）、golden diff 为空、覆盖率 ≥70%、DoD 1–7 中除“启停回滚”（P1）外全部通过；架构文档与实现逐条一致。

---

## 7. 需用户决策（补全计划开工前）

1. **插件命名**：保留 `owl-rdf`/`skos` 并改架构文档，还是改为 `onto-standards-owl`/`onto-standards-skos` 并保留旧名别名？（推荐后者 + 别名，兼容 CLI/API 老用法。）
2. **IRI 方案**：以实现的 `…/ws/{workspace}/onto/{name}/v{version}#` 为准更新文档，还是严格按架构的 `https://{workspace}.ontoplatform.local/onto/{version}#` 改实现？（推荐前者：更安全、测试与 golden 已锁定。）
3. **manifest 字段**：以架构的嵌套 `formats: {out,in}` + `requires` + `config_schema` 补齐，还是维持扁平字段并改架构？（推荐按架构补齐，扁平字段保留为兼容属性。）
4. **P0 是否包含 JSON-LD / ABox**：按架构 §4，JSON-LD 属 P0 范围但当前未做；ABox 属 P2。建议 P0 明确“Turtle-only、TBox-only”，文档同步。
5. **API 动词与产物接口**：导出保持 `GET`（只读、可缓存，推荐）还是按架构 §5 改 `POST`；`/artifacts/{id}` 是否 P1 做（需要产物存储/校验和设计）。
6. **`Platform.standards` 兼容**：接受内部 API 破坏（保持必填）还是做成可选/懒加载（推荐可选，减少库使用方破坏）。
7. **scope/include_abox 语义**：P0 显式报错（推荐）还是静默忽略并在 metadata 标注（不推荐）。

---

## 8. 风险与建议

| 风险 | 说明 | 缓解 |
|---|---|---|
| 文档/实现继续漂移 | 已有 6 处架构与实现不一致，若不先对齐，后续 P1 会按错误契约开发 | C0-1 先定 SSOT；本复核文档作为差距基线 |
| 误以为 P0 已全部完成 | 功能可用但治理/CI/契约不完整 | 对外表述用“P0 核心闭环完成，P0 合同收口中”，避免承诺 DoD 5/6 |
| 类层次承诺无法兑现 | 模型无 parent，单靠插件做不到 | 在路线图显式标注“依赖模型层 O-3/O-5”，不在插件阶段承诺 |
| CI 跳过关键解析 | rdflib 用例在 CI skip | C0-2 修 CI；本地复核已用 rdflib 7.6 验证 |
| 治理延后导致回滚不可用 | DoD 6 不满足 | P1 第一优先 C2-1；在对外文档写清“P0 无启停” |

**建议执行节奏**：先批准决策 1–7，然后一轮团队任务：C0（可与文档并行）+ C1（依赖 C0-1/C0-3）；C2 作为下个迭代。若只做一件事：**C0-2（CI interop）+ C0-6（golden+数量断言）+ C0-1（文档对齐）**，这三项把“可验证的 P0”从 72% 拉到约 85%，成本最低。

---

## 9. 证据索引

| 证据 | 位置 |
|---|---|
| SPI/manifest/返回类型 | `src/onto_platform/semantic/spi.py`（manifest 58-84；红线性见 12-20） |
| IRI 策略 | `src/onto_platform/semantic/iri.py`（14-15、56-95、136-173） |
| 只读上下文 | `src/onto_platform/semantic/context.py`（52-126、129-157） |
| 注册/发现/隔离 | `src/onto_platform/semantic/registry.py`（93-180、215-321） |
| OWL 导出 | `src/onto_platform/semantic/owl.py`（107-152、156-231） |
| SKOS 导出 | `src/onto_platform/semantic/skos.py`（53-135；跨插件 import 在 26 行） |
| Turtle 序列化 | `src/onto_platform/semantic/turtle.py`（158-291） |
| 用例 | `src/onto_platform/application/standards_usecases.py`（55-134） |
| API/CLI | `src/onto_platform/interfaces/api.py:909-910,1193-1218`；`cli.py:228-250,411-418` |
| 组合根 | `src/onto_platform/bootstrap.py:95,176-177,485-486` |
| 测试/契约 | `tests/test_semantic_*.py`、`tests/test_standards_*.py`（116 项） |
| 既有审计/路线 | `docs/ontology-implementation-review-v1.md:230-235`（B2 已过时）、`docs/plan-v2.md:194` |
| 设计 SSOT | `docs/standards-plugin-architecture.md` §2.2/§2.3/§4/§5/§6/§8 |
| 验证报告 | `docs/standards-plugin-implementation-report.md`（QA，712/3 → 收尾 715/0） |

---

## 10. 补全状态（C0+C1 执行后，2026-11）

> 本节追加于 C0+C1 实际实施完成后；上文 §2–§4 的评分与缺口保留，用于对照“复核时 → 现在”的变化。

**执行结果**：task-10（SPI/Registry）、task-11（导出器）、task-12（接线）、task-13（独立验证）全部完成；未 commit（工作区改动待评审后提交）。Lead 最终复跑 `python -m pytest -q` → **794 passed, 0 skipped, 65.60s**；D 独立复跑 **794 passed, 0 skipped, 64.37s**；覆盖率 **79.94%**（`--cov-fail-under=70` 通过）。

| 领域 | 复核时 | 现在 | 变化说明 |
|---|---|---|---|
| SPI / manifest 契约 | 65% | **92%** | `formats{out,in}`/`requires`/`config_schema`/`aliases` 补齐；能力-方法校验；`AdapterInvocationError` 502 映射 |
| 确定性 IRI | 90% | **95%** | 实现保持为 SSOT，架构 §5/§2.4 已对齐实际 base |
| AdapterContext | 85% | **90%** | scope 显式拒绝（不再静默忽略）；ABox 仍按 P2 |
| Registry / 发现 | 80% | **92%** | 别名解析、能力校验、requires 兼容、失败隔离；workspace 启停仍 P1 |
| 导出插件 | 75% | **88%** | SKOS/OWL 解耦、CJK `@zh`、golden + 数量断言；类层次/JSON-LD/词表层级仍缺（模型/P1） |
| Use case/REST/CLI | 70% | **90%** | 插件异常隔离 + 失败审计 + 502、provenance 头/metadata、standards 可选守卫、别名可用 |
| 测试 | 85% | **95%** | 契约 30 项；golden 逐字节；跨进程确定性；异常/scope/别名/headers 全覆盖 |
| CI / 交付验证 | 40% | **85%** | CI 安装 `[dev,interop]`；dev extra 含 pytest-cov；覆盖率门禁可执行 |
| 文档 | 60% | **90%** | 架构 §2.2/§2.4/§4/§5/§6/§7/§8、plan-v2 O-3、审计 B2 注记、README 能力边界、本复核/执行计划同步 |

**缺口关闭情况**：G-01/G-02/G-03/G-04/G-05/G-06(文档)/G-07(P0 范围)/G-09/G-12/G-17/G-18/G-19/G-20/G-21/G-22/G-23/G-24/G-25 关闭；G-16（workspace 启停/reload/health/运行时权限）部分，属 C2；G-08/G-10（类层次、词表层级/description）依赖模型层，G-11（JSON-LD）、G-13/G-14/G-15（validate/artifacts/reload 路由与 CLI）留待 C2/C3。

**仍属 P1/P2（非本次缺陷）**：SHACL 导出/校验、SPARQL、导入、ABox 快照、类层次/词表层级、JSON-LD、workspace 治理与前端按钮——见执行计划 §6。
