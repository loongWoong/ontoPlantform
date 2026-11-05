# 标准格式插件 P0 实现：QA 独立验证报告（T4）

- 日期：2026-02（本次会话）
- 验证者：qa-verifier（T4，独立于 T1/T2/T3 实现者）
- 被验对象：`src/onto_platform/semantic/`（SPI、IRI、Context、Registry、Turtle、owl-rdf、skos）、`application/standards_usecases.py`、REST/CLI 接线
- 依据合同：`docs/standards-plugin-implementation-plan-v1.md` §2.5 / §5 / §7，`docs/standards-plugin-architecture.md` §2.5
- 结论：**P0 合同通过（有条件）**。0 失败；12 项新增契约测试通过、1 项因环境缺 `rdflib` 跳过；全量回归 **712 passed, 3 skipped**，相对基线 **700 passed, 2 skipped** 无回归（+12 通过 / +1 跳过）。两处需 Lead 决策的事项见 §6（计划文档 404/400 表述过期）与 §9（工作区既有 `ontology-guide` 改动不属于本功能）。

---

## 1. 验证环境

| 项目 | 值 |
|---|---|
| OS / Shell | Windows，PowerShell 7（`pwsh`） |
| Python | 3.12.10（`[MSC v.1943 64 bit (AMD64)]`） |
| pytest / pytest-asyncio | 9.0.3 / 1.4.0 |
| fastapi / pydantic / httpx | 0.115.6 / 2.13.4 / 0.28.1 |
| onto-platform | 0.2.0，editable 引用工作区源码：`import onto_platform` → `F:\piAgent\ontoPlantform\src\onto_platform\__init__.py` |
| rdflib / pyshacl | **未安装**（`importlib.util.find_spec("rdflib") is None`）；未执行任何 `pip install` |
| 仓库 / HEAD | `F:\piAgent\ontoPlantform` / `616d2a1` |
| pytest 配置 | `testpaths=["tests"]`，`addopts="--strict-markers"`，未改配置、未加 marker |

> 说明：`pip show onto-platform` 的元数据版本为 0.2.0（`pyproject.toml` 已升到 0.3.0），是 editable 元数据未刷新所致；pytest 实际导入/执行的是工作区 `src/` 代码，不影响本次验证结论，但建议 Lead 收尾时重新 `pip install -e .` 以免元数据误导。

---

## 2. 验证方法（独立性声明）

1. **未修改实现**：`src/`、现有 `tests/` 均未改动；未 `git commit`；未安装依赖、未改 `pyproject.toml`。
2. **未复用开发者断言**：新增 `tests/test_standards_contract.py` 自带最小 TBox（`Vehicle`/`TollStation` + 数据属性 + 双向关系 + 三条词表术语 + 别名），只依赖公共 SPI（`onto_platform.semantic`）与领域模型，不 import 任何开发者测试辅助函数；契约点逐条从计划 §2.5/§7 与架构 §2.5 反推。
3. **独立通道验证**：除直接调用 adapter 外，另用真实 socket（`http_server` fixture → `urllib`）验证 REST 行为与字节流，而不是只测应用层。
4. **交叉验证**：确定性同时覆盖“同 context 调两次”“独立重建 context”“HTTP 同一 URL 取两次”三种路径；语义红线同时扫 adapter 产物与 HTTP 响应正文。

新增文件：`tests/test_standards_contract.py`（13 个测试：12 pass / 1 skip）。交付报告：本文件。

---

## 3. 验证命令与准确输出

> PowerShell 不会为原生进程展开 `*` 通配符，故命令 1 用文件列表展开；其等价逻辑即任务要求的 `pytest tests/test_standards_contract.py tests/test_semantic_*.py tests/test_standards_*.py -q`。

### 3.1 契约 + 语义/标准相邻套件（定向回归）

```powershell
$files = Get-ChildItem tests/test_semantic_*.py, tests/test_standards_*.py | % FullName
python -m pytest tests/test_standards_contract.py @files -q
```

```text
...........s............................................................ [ 62%]
.........................ss.................                             [100%]
113 passed, 3 skipped in 8.45s
```

仅新增契约套件：

```text
tests/test_standards_contract.py
...........s.                                                            [100%]
12 passed, 1 skipped in 3.30s
```

### 3.2 全量回归（DoD 1）

```powershell
python -m pytest -q -rs
```

```text
........................................................................ [ 30%]
...
...................................................................      [100%]
=========================== short test summary info ===========================
SKIPPED [1] tests\test_standards_contract.py:377: rdflib not installed (pyproject interop extra)
SKIPPED [2] tests\test_standards_export.py:427: rdflib is not installed (the core stays dependency-free); the [interop] extra enables this check
712 passed, 3 skipped in 61.56s (0:01:01)
```

- 相对基线 `700 passed, 2 skipped`：**+12 passed, +1 skipped，无回归、无新失败**。
- 3 个 skip 全部是 `rdflib` 可选解析用例（既有 2 个在 `test_standards_export.py:427`，新增 1 个即本套件的 round-trip 测试），与未安装 `[interop]` extra 一致；未以跳过掩盖任何红线/边界断言。

### 3.3 pyproject 依赖边界（DoD 5）

```powershell
python -c "import tomllib, pathlib; d=tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8')); print(d['project']['dependencies'])"
```

```text
dependencies = []
extras = ['dev', 'interop', 'kernel', 'mcp']
interop = ['rdflib>=7', 'pyshacl>=0.29']
```

`git diff pyproject.toml` 仅新增 `interop` extra（5 行，含注释）；`[project] dependencies = []` 未被改动的 `+/-` 触及。

### 3.4 工作区状态

```powershell
git status --short
git diff --stat
```

`git diff --stat`：`10 files changed, 183 insertions(+), 5 deletions(-)`，只涉及 `pyproject.toml`、`src/onto_platform/bootstrap.py`、`interfaces/api.py`、`interfaces/cli.py` 与 `ontology-guide/*`；`semantic/`、`application/standards_usecases.py`、新增测试均为未跟踪文件，与 T1–T3 交付一致。

`git status --short` 中本 T4 新增的两项：`?? tests/test_standards_contract.py`、`?? docs/standards-plugin-implementation-report.md`（本报告）。**注意**：`ontology-guide/*` 的 6 个修改与 4 个未跟踪文件属于既有 PDF 下载功能改动，与 standards 插件无关（详见 §9 与 DoD 5）。

---

## 4. 覆盖矩阵（契约点 → 测试 → 结果）

| # | 契约点（出处） | 测试函数 | 结果 |
|---|---|---|---|
| a1 | 发现结果含 `owl-rdf`/`skos`（§5 契约） | `test_discovered_adapters_expose_a_usable_export_contract` | PASS |
| a2 | 每个 `manifests()` 行 `status=="ready"`、`problems==[]`、`manifest_problems(manifest)==[]` | 同上 | PASS |
| a3 | manifest 含 `export` capability、`formats_out` 非空 | 同上 | PASS |
| a4 | `artifact.media_type ∈ formats_out`、`content` 为 `bytes` 且非空、有 filename | 同上 | PASS |
| b1 | 同一 Ontology + 同 IriPolicy 连续两次导出逐字节相同（DoD 3） | `test_export_is_byte_identical_for_the_same_ontology_and_iri_policy` | PASS |
| b2 | 独立重建的等价 context 不改变任何字节 | 同上 | PASS |
| c1 | OWL 产物含 `owl:Class`/`owl:ObjectProperty`/`owl:DatatypeProperty` | `test_owl_export_contains_structure_but_no_runtime_axioms` | PASS |
| c2 | 无 `owl:hasKey`/cardinality/FunctionalProperty/someValuesFrom/allValuesFrom/`sh:target`/`owl:Restriction`（§2.5 红线） | 同上（9 个禁串全量扫描） | PASS |
| d1 | `semantic/*.py` 8 个模块均无 `onto_platform.bootstrap` / `from ..bootstrap` / `KernelRuntimeGateway` / `onto_platform.infrastructure` / `from ..infrastructure` / `infrastructure.runtime` / `import rdflib` / `import pyshacl`（DoD 6） | `test_semantic_package_does_not_reach_into_platform_internals` | PASS |
| d2 | `[project] dependencies == []`（DoD 5）、`interop` extra 含 `rdflib` | `test_core_distribution_keeps_zero_runtime_dependencies` | PASS |
| e1 | `GET .../standards/adapters` → 200，两个内置插件均 ready | `test_http_adapter_listing_reflects_the_plugin_registry` | PASS |
| e2 | `GET .../standards/export?adapter=owl-rdf&format=text/turtle` → 200、`Content-Type` 含 `text/turtle`、`Content-Disposition` 含 attachment+filename、正文含 `@prefix`/`rdf:type`、无红线串（DoD 2/4） | `test_http_owl_export_returns_a_turtle_document` | PASS |
| e3 | 同一 URL 连续两次返回字节相同（DoD 3，经 socket 复验） | 同上 | PASS |
| e4 | skos 导出正文含 `skos:ConceptScheme` | `test_http_skos_export_returns_a_concept_scheme` | PASS |
| e5 | 未知 adapter → 404（**当前实现行为**，见 §6） | `test_http_unknown_adapter_is_404_in_the_current_implementation` | PASS |
| e6 | 不支持的 format → 400，错误体 `type=="AdapterError"` | `test_http_unsupported_format_is_400` | PASS |
| e7 | 无已发布本体的 workspace → 404（`not_found`） | `test_http_export_without_a_published_ontology_is_404` | PASS |
| f1 | 有 rdflib 时 Turtle 可被 `rdflib.Graph().parse` 解析且含 `owl:Class` 类型三元组 | `test_owl_artifact_parses_with_rdflib_when_available` | **SKIP**（rdflib 未装，按约定不 install） |
| g1 | 注入非法 manifest 插件：`status=="invalid"`、`problems` 非空，ready 插件不受影响；`get("qa-broken")` 抛 `AdapterError`（非 NotFound） | `test_registry_isolates_a_broken_plugin_and_still_serves_ready_ones` | PASS |
| g2 | `get(unknown)` 抛 `AdapterNotFound` | 同上 | PASS |

---

## 5. DoD §7 逐条判定

| # | DoD 条目 | 判定 | 证据/说明 |
|---|---|---|---|
| 1 | 全量测试全绿且 ≥ 599 + 新增 | **通过** | 712 passed / 0 failed；新增 12 pass + 1 skip；相对基线 700/2 无回归 |
| 2 | `GET .../export?adapter=owl-rdf&format=text/turtle` 返回**可解析** Turtle | **有条件通过** | HTTP 200、`text/turtle`、含 `@prefix`/`rdf:type`/结构三元组，且红线串为零；严格 `rdflib` 解析因环境未装 `[interop]` 而 SKIP（计划 §8 已把该风险列为可选依赖）。建议 CI 装 interop extra 后复跑该项（测试已就绪，装包即从 skip 变 pass） |
| 3 | 同一发布版本连续两次导出逐字节相同 | **通过** | adapter 级重复调用、独立 context、HTTP 双 GET 三条路径均字节相等 |
| 4 | OWL 产物无 `hasKey`/`owl:cardinality`/`FunctionalProperty`/`someValuesFrom` | **通过** | 除 §7 列出的 4 个外，另验证 `minCardinality`/`maxCardinality`/`allValuesFrom`/`sh:target`/`owl:Restriction` 亦不存在；同时确认结构三元组存在 |
| 5 | `dependencies = []` 不变；`ontology-guide` 与 Kernel 无改动 | **有条件通过** | `dependencies=[]`、`interop` extra 为本次预期新增；`src/` 仅 bootstrap/api/cli 三处接线改动；**但工作区存在既有的 `ontology-guide/*` 改动（PDF 下载功能），不是本功能产生的、也不含 standards 相关内容，无法仅凭 git 状态证明“本任务未动它”**，需 Lead 按任务边界确认（Kernel 不在本仓库，未见相关文件改动） |
| 6 | 插件不 import 平台内部（bootstrap/gateway/repo）—— QA grep 断言 | **通过** | 8 个 `semantic/*.py` 全量文本扫描，8 类禁串均无命中；允许的 `..domain`（只读 TBox 模型）/`..errors` 不在禁列 |
| 7 | 文档：实现报告 + README/架构文档链接更新（Lead 收尾） | **部分完成** | 本实现报告已交付；README/架构文档的链接更新属 Lead 收尾项，本次未改动（未越权） |

---

## 6. 偏差与文档不一致（需 Lead 决策）

### 6.1 未知 adapter 的状态码：计划写 400，实现是 404

- 计划 `docs/standards-plugin-implementation-plan-v1.md`：
  - §2.5 Use case 注释：`# 无发布本体 → NotFoundError；未知 adapter/format → AdapterError(400)`；
  - §2.5 REST 契约：`→ 400 unknown adapter/format；404 no published ontology`。
- 实际实现（T3 交付，且已被开发者测试锁定）：
  - 未知 adapter：`PluginRegistry.get()` 抛 `AdapterNotFound`，它继承 `NotFoundError`，`errors.classify` 映射为 404（`src/onto_platform/semantic/spi.py`、`errors.py`）；
  - 不支持的 format：adapter 抛 `AdapterError`（`ValidationError`）→ 400，**与计划一致**；
  - 无已发布本体：404，与计划一致。
- 代码内的 docstring 已按 404 描述（`application/standards_usecases.py:17-18,102-104`；`interfaces/api.py` 的 standards 分支注释），说明是实现阶段的有意选择，**是计划文档过期，而非实现缺陷**。
- 本套件处理：`test_http_unknown_adapter_is_404_in_the_current_implementation` 锁定可观察的 404 行为，并在测试注释中写明计划差异；不擅自改实现或改计划（不在 T4 写权限内）。
- 建议：Lead 二选一——
  1. **推荐**：更新计划 §2.5 的 Use case 注释与 REST 契约为“未知 adapter → 404（AdapterNotFound）”，与代码、现有测试、平台错误分类学（`NotFoundError→404`）一致；
  2. 若坚持 400，则需改 `StandardsUseCases.export` 把 `AdapterNotFound` 转成 `AdapterError`，会与 `test_standards_api.py::test_unknown_adapter_maps_to_404` 等既有断言冲突，并要求同步修正平台错误语义——不建议在 P0 收尾期做。

### 6.2 其他观察

- `export` 返回的 `format` 归一化（`turtle` → `text/turtle`）由 adapter 的 `check_export_format` 承担；本次未单测 `format="turtle"` 别名，但既有 `test_standards_export.py` 覆盖，未发现风险。
- API 未设置 `X-Content-Type-Options` 等安全头不在 P0 合同内，未作为失败项。

---

## 7. 残余风险

1. **Turtle 可解析性未在真实解析器上验证**（DoD 2）：`rdflib`/`pyshacl` 未安装，round-trip 测试 SKIP；零依赖 writer 的正确性目前依赖字符串断言 + `test_standards_turtle.py` 单测。风险中低——若 CI 安装 `[interop]` extra，本套件对应测试会自动执行。建议 P0 收尾前在带 extra 的环境跑一次（无需改代码）。
2. **确定性只在单进程内证明**：adapter 内部已对 objects/properties/relations/词表 `sorted()` 迭代，语句再经 `TurtleWriter` 去重排序，跨进程（不同 `PYTHONHASHSEED`）风险低；当前测试未显式跑两个不同 hash seed 的进程。若要求更强保证，可在 CI 加一条 `PYTHONHASHSEED` 随机化的双进程字节比对（P1）。
3. **E2E 依赖 loopback 本地身份**：HTTP 测试依赖默认 actor `admin`（未设置 `ONTO_API_TOKEN`/`ONTO_LOCAL_ACTOR`）；在设置了这些环境变量的 CI 上行为会不同。当前针对本仓库测试环境成立，建议 CI 显式隔离这些环境变量。
4. **工作区既有 `ontology-guide` 改动与 DoD 5 的字面判定**：非本功能引入，但会让“ontology-guide 无改动”的机械检查失败；需 Lead 在最终验收说明中澄清，或另起提交隔离。
5. **未知 adapter 404/400 文档分歧**：若不更新计划，后续按计划实现的消费方会误判状态码（见 §6.1）。

---

## 8. P1 待办建议（超出 P0 合同，供排期）

1. **SHACL 适配器**：把平台运行时约束（`required`/`enum`/`min`/`max`/`unique`/基数）导出为 `sh:targetClass` + `sh:property` 形状；`validate` capability 用 `pyshacl` 在 `[interop]` extra 下实现——这正是红线 §2.5 指定给 SHACL 的语义归宿。
2. **SPARQL 查询适配器**：只读、超时/结果上限、模板白名单；manifest 声明 `capabilities=("query",)`，避免与导出共用权限模型。
3. **import 适配器**：Turtle/OWL 导入 → 生成 ontology draft patch（冲突检测沿用 `assert_no_collision`），`formats_in` 与 `ExportRequest` 对称设计。
4. **ABox 导出**：基于 `SnapshotView` 分页/流式导出实例数据，`include_abox` 已有占位参数；需先定 snapshot 快照一致性语义。
5. **可选格式**：JSON-LD / RDF/XML 输出（`rdflib` 序列化在 extra 内），`formats_out` 多值时 API 的 `format` 协商与 406/400 语义要一并定。
6. **插件治理**：workspace 级启用/禁用与权限（`permissions` 字段已预留 `network`/`write`），entry-point 发现失败的集中告警（`registry.problems` 已有，可接 `/health`）。
7. **CI**：新增 `[interop]` job 跑 rdflib round-trip + 双 `PYTHONHASHSEED` 确定性比对。

---

## 9. 交付物

| 文件 | 说明 |
|---|---|
| `tests/test_standards_contract.py` | T4 独立契约/边界/E2E 测试：13 个测试（12 pass + 1 环境 skip）；覆盖矩阵见 §4 |
| `docs/standards-plugin-implementation-report.md` | 本报告 |

未改动任何 `src/` 或既有 `tests/`；未 `git commit`；未安装依赖。

**给 Lead 的收尾动作建议**：
1. 对齐计划 §2.5 的 404/400 表述（§6.1），或确认保留现状并记录；
2. 澄清 `ontology-guide/*` 的既有改动归属（§5 DoD 5、§7 风险 4）；
3. 在带 `[interop]` extra 的环境复跑 `python -m pytest tests/test_standards_contract.py -q`，使 DoD 2 的严格解析验证由 SKIP 转 PASS；
4. 更新 README/架构文档链接（DoD 7 的剩余部分）。

---

## 附：Lead 收尾确认（P0 验收）

1. **状态码分歧已消除**：计划 §2.5 已对齐实现——未知 adapter → **404**（`AdapterNotFound`），已注册但不可用的 adapter / 不支持的 format → **400**（`AdapterError`）；用例 docstring、API 分支与契约测试口径一致。
2. **严格解析验证转通过**：收尾环境补装 `[interop]`（rdflib 7.6.0）后复跑，`python -m pytest -q` → **715 passed, 0 skipped, 60.93s**；原先 3 个 rdflib 解析用例由 SKIP 转 PASS，DoD §7.2 由“有条件通过”转 **完全通过**，Turtle round-trip 已在真实解析器上验证。
3. **working tree 归属澄清**：`ontology-guide/*` 的 6 个修改与 4 个未跟踪文件属于此前已完成的 PDF 下载功能，不是本 P0 产生；P0 的改动范围 = `README.md`、`pyproject.toml`、`bootstrap.py`、`interfaces/api.py`、`interfaces/cli.py` 与全部新增文件（`semantic/`、`application/standards_usecases.py`、7 个测试、4 份文档），未 commit。
4. **文档收尾完成**：`README.md` 新增「标准格式插件（P0）」用法与链接；`docs/standards-plugin-architecture.md` §7 新增「P0 实现状态（已落地）」及计划/报告链接（DoD §7.7 完成）。

---

## 附：C0+C1 收尾（task-13 / D 独立验证）

> 依据 `docs/standards-plugin-completion-plan-v1.md` task D 执行；本节只记录 C0+C1 补全的独立验证结果，追加于上节 P0 验收之后，不改动历史结论。

### D-0 结论

C0+C1 达到计划 §1 的 5 项完成判定：全量 **794 passed / 0 skipped**、覆盖率 **79.94%**（门禁 70%）、独立契约套件 **30 项全绿（新增 17 项）**、golden 逐字节与跨进程确定性成立。**无阻塞项**；剩余缺口均为计划 §6 明确留给 C2/C3 的项（G-08/G-10/G-11/G-13/G-14/G-15，以及 G-16 的治理部分）。

### D-1 验证方式（独立于开发者断言）

| 维度 | 做法 |
|---|---|
| 契约测试 | 仍只在 `tests/test_standards_contract.py` 内自建本体 + 自写断言，不 import 开发者的测试模块或 helper |
| golden | 输入的 `Ontology` 由冻结产物**反向重建**（解析 `tests/golden/standards/*.ttl` 的 OWL/SKOS 三元组），导出结果与产物逐字节比对；数量断言直接数 golden 文本，不复用开发者的计数断言 |
| 跨进程 | `subprocess` + `sys.executable` 启动独立 Python 进程，`PYTHONHASHSEED=1` / `2` 各跑一次，比较 stdout 字节；脚本内重建本体并导出，同时与进程内导出比对 |
| 异常隔离 | 私有 `PluginRegistry` 注入 `StandardsUseCases`，并通过已启动的真实 HTTP socket 验证 502；不依赖开发者用例里的同款假插件 |
| 语言标签 | 正则提取产物中**全部** `@zh` 字面量，断言每个都含 CJK；ASCII 标识符断言 `^^xsd:string` 且无 `@lang` |
| 语义红线 | 沿用原 P0 禁串清单（`owl:hasKey`/基数/`FunctionalProperty`/`someValuesFrom`/`allValuesFrom`/`sh:` 等），对新 golden 产物同样断言为零 |

### D-2 测试矩阵（30 项 = 原有 13 项保留 + 新增 17 项）

| 分组 | 新增数 | 覆盖点 |
|---|---|---|
| h) manifest 新字段/旧键兼容 | 2 | `formats{out,in}`/`requires`/`config_schema`/`aliases`、`manifest_problems==[]`、旧扁平键仍在、行可 JSON 序列化；旧式（pre-D3）manifest 仍可注册并带分组默认值 |
| i) canonical/别名 | 1 | `get("onto-standards-owl"/"onto-standards-skos")` 可用、与 canonical 同一实例；`names()` 只含 canonical；别名与 canonical 导出逐字节一致；未知别名仍 404 分类 |
| j) 能力-方法一致性 | 1 | 声明 `validate` 但无方法 → `adapter_contract_problems` 有诊断；发现列表 `status=invalid`、`get` 抛 `AdapterError`；ready 插件不受影响；手动 `register` 抛 `AdapterError` |
| k) `requires.ontoplatform` | 1 | `>=99.0` → `status=invalid` 且 problems 含 "incompatible"；`get` 抛 `AdapterIncompatible`；`requires_compatible` 返回 False 且 reason 含当前平台版本；手动注册亦拒绝 |
| l) scope 负例 | 2 | `scope="abox"/"both"`、`include_abox=True` → `AdapterError`（文案含 "TBox only"）；`""`/`"tbox"` 正常；`build_context` 拒绝未知 scope |
| m) 语言标签 | 1 | OWL `rdfs:label "Vehicle"^^xsd:string`、`rdfs:comment "车辆"@zh`；SKOS `prefLabel/altLabel` 中文 `@zh`、英文别名 `^^xsd:string`；不变式：所有 `@zh` 字面量都含 CJK |
| n) 插件异常隔离 | 3 | use case → `AdapterInvocationError`，`classify` = `plugin_failure`/502/不可重试；审计恰好一条 `standards.export.failed`（RuntimeError + message），无 success 审计；平台校验错误（`AdapterError`）原样透传且不写失败审计；HTTP 502 结构化错误；`Platform.standards=None` 两条路由 404 |
| o) provenance | 2 | API 响应头 `X-Adapter-Name/-Version`、`X-Ontology-Version`、`X-Schema-Fingerprint` 与已发布版本一致，`Content-Type` 含 `charset=utf-8`、`Content-Disposition` 含 `.ttl`；别名请求头仍报 canonical 且字节相同；use case metadata 含 actor/ontology_version/schema_fingerprint/adapter_version、无时间戳、重复导出完全一致 |
| p) golden 独立核对 | 2 | OWL/SKOS 产物与冻结文件逐字节相等；OWL 2 类/4 数据属性/2 对象属性/2 `owl:inverseOf`；SKOS 1 scheme/3 concept/3 altLabel/4 prefLabel；CJK/ASCII 标签与红线 |
| q) 跨进程确定性 | 1 | `PYTHONHASHSEED=1` 与 `2` 两次子进程 stdout 字节相等，且与进程内导出、与 golden 相等（超时 60s） |
| r) 包导出面 | 1 | `onto_platform.semantic` 暴露 `AdapterInvocationError`/`CAPABILITY_METHODS`/`adapter_contract_problems`/`platform_version`/`requires_compatible`/`requires_problems` |

原有 13 项（发现/确定性/语义红线/依赖边界/真实 socket E2E/rdflib round-trip/坏插件隔离）全部保留；仅将「未知 adapter 404」用例的注释从“计划写 400”更新为已对齐口径，断言未放松。

### D-3 逐项契约验证结果

| # | 契约 | 结果 |
|---|---|---|
| 1 | manifest 新字段 + 旧键兼容 | 通过（`to_dict()` 同时含嵌套 `formats/requires/config_schema/aliases` 与扁平 `formats_out/in`；builtin 行 `status=ready`、`problems=[]`） |
| 2 | 别名解析且 canonical 唯一 | 通过（`names()==["owl-rdf","skos"]`；别名与 canonical 导出逐字节一致） |
| 3 | 能力-方法一致 | 通过（`status=invalid` 或注册 `AdapterError`，两者皆被覆盖；ready 插件隔离） |
| 4 | `requires` 不兼容可见 | 通过（`status=invalid` + problems 含 "incompatible"，`get` → `AdapterIncompatible`） |
| 5 | scope 显式拒绝 | 通过（abox/both/include_abox → `AdapterError`；tbox 正常） |
| 6 | 语言标签 | 通过（CJK → `@zh`；ASCII → `^^xsd:string`；无 `"Vehicle"@zh`） |
| 7 | 异常隔离 + 审计 + 502 | 通过（use case 与 HTTP 双层；审计 `standards.export.failed`；`PluginError` 透传不误报） |
| 8 | provenance 头/metadata | 通过（四个 `X-*` 头 + charset；metadata 无时间戳且可复现） |
| 9 | golden 逐字节 + 数量 | 通过（输出 == 冻结文件；数量断言见 D-2 p） |
| 10 | 跨进程确定性 | 通过（seed 1/2 相等，且与进程内一致） |

### D-4 测量数字（本机：Windows / Python 3.12.10 / rdflib 7.6.0 / pytest-cov 已装）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/test_standards_contract.py -q` | **30 passed in 5.50s** |
| `python -m pytest <tests/test_semantic_*.py tests/test_standards_*.py> -q` | **195 passed in 12.57s** |
| `python -m pytest -q` | **794 passed, 0 skipped in 64.37s** |
| `python -m pytest -q --cov=onto_platform --cov-report=term --cov-fail-under=70` | **794 passed in 82.01s**；TOTAL **80%（精确 79.94%）**，`Required test coverage of 70% reached` |
| 覆盖率（`semantic` 包） | spi 88%、registry 93%、owl 96%、skos 93%、turtle 93%、iri 96%、context 100%、formats 100% |

与 P0 验收基线（715 passed / 0 skipped / 60.93s）相比，新增 79 项（A/B/C/D 及补全测试），无回归、无环境 skip。

### D-5 G-01…G-25 补全状态（C0+C1）

| 缺口 | 状态 | 说明 / 证据 |
|---|---|---|
| G-01 manifest 不完整 | **关闭** | 嵌套 `formats/requires/config_schema/aliases` 已出，旧扁平键保留（D-2 h） |
| G-02 能力-方法不校验 | **关闭** | `adapter_contract_problems` + discover 标 `invalid` + register 拒绝（D-2 j） |
| G-03 异常未隔离 | **关闭** | `AdapterInvocationError`→502/`plugin_failure`/不可重试 + 失败审计（D-2 n） |
| G-04 scope 被静默忽略 | **关闭** | abox/both/include_abox 显式 `AdapterError`（D-2 l） |
| G-05 插件名与架构不一致 | **关闭** | canonical 短名 + `onto-standards-*` 别名，`get` 双写法（D-2 i） |
| G-06 IRI SSOT 文档不一致 | **关闭（Lead 文档）** | 架构 §5 与实现对齐；golden 锁定 IRI 形态（D-2 p） |
| G-07 provenance 未落图 | **P0 范围关闭 / 命名图 P1** | metadata + `X-*` 头已交付；命名图按架构 §6（L191）属 P1 |
| G-08 无类层次 | 未关闭（C3） | 依赖模型层 `ObjectTypeSpec.parent`，非插件可解 |
| G-09 无语言标签 | **关闭** | CJK `@zh` / ASCII `xsd:string`（D-2 m） |
| G-10 `skos:definition` 恒空 | 未关闭（C3） | 依赖 `VocabularyTerm.description` 模型字段 |
| G-11 无 JSON-LD | 降级为 P1（文档已改口径） | P0 明确 Turtle-only（plan D4），能力边界已写入 README |
| G-12 SKOS 横向依赖 OWL | **关闭** | 共享 `semantic/formats.py`；SKOS 不再 import OWL（原有解耦断言保留） |
| G-13 API 路由缺口 | 未关闭（C2） | validate/artifacts/reload 属 P1 |
| G-14 use case 缺口 | 未关闭（C2） | Validate/Query 属 P1 |
| G-15 CLI validate | 未关闭（C2） | 属 P1 |
| G-16 治理/兼容校验 | **部分关闭** | 能力-方法与 `requires` 兼容已补；workspace 启停/reload/health/permissions 运行时强制属 C2 |
| G-17 `Platform.standards` 必填 | **关闭（Lead）** | 可选字段默认 None，API/CLI 显式 404（D-2 n 覆盖） |
| G-18 无 golden/数量断言 | **关闭** | golden 两件 + 数量断言（D-2 p） |
| G-19 CI 不装 interop | **关闭（Lead）** | CI 改 `[dev,interop]` + `--cov` 门禁（`.github/workflows/ci.yml`） |
| G-20 无跨进程确定性 | **关闭** | 双 `PYTHONHASHSEED` 子进程字节比对（D-2 q） |
| G-21 无插件异常路径测试 | **关闭** | C 的用例测试 + D 的独立双层验证（D-2 n） |
| G-22 `[dev]` 缺 pytest-cov | **关闭（Lead）** | `pyproject.toml` dev extra 含 pytest-cov/coverage；CI 门禁可运行 |
| G-23 架构文档不一致 | **关闭（Lead）** | §2.2/§4/§5/§6 已对齐（本报告抽查 L64-82、L111-120、L156-175、L191、L207-214） |
| G-24 路线图/审计注记 | **关闭（Lead）** | `plan-v2.md:196` 已加 O-3 导出侧状态补充；`docs/ontology-implementation-review-v1.md` 已加 P0 部分关闭说明与“B2 被架构 §2.5 取代”注记（本轮抽查 diff 确认） |
| G-25 无能力矩阵 | **关闭（Lead）** | README「P0 能力边界（诚实清单）」+ 架构 §7 插件/阶段清单 |

### D-6 DoD 对照（计划 §1「C0+C1 完成判定」）

| # | 判定 | 结果 |
|---|---|---|
| 1 | 全量全绿、≥715 + 新增、0 环境 skip | **通过**：794 passed / 0 skipped（`[interop]` 已装） |
| 2 | 覆盖率 ≥70% | **通过**：79.94%，门禁命令可运行 |
| 3 | 契约测试新增项齐备 | **通过**：manifest/别名/能力-方法/requires/scope/异常 502+审计/golden/数量/语言标签/provenance/跨进程 全部有断言（D-2） |
| 4 | `git diff` 仅声明文件、`dependencies=[]`、Kernel 未动 | **通过（D 范围）**：D 只改本报告 + `tests/test_standards_contract.py`；`dependencies=[]` 复核未变；未 commit；其余改动属 A/B/C/Lead 各自写作用域 |
| 5 | 文档与实现一致 | **通过（Lead 完成，D 抽查）**：架构 §2.2/§4/§5/§6、README 能力边界、CI、pyproject 已同步；本报告补注 C0+C1 |

### D-7 残余风险与观察（C2/C3）

1. **C2（P1，未做）**：G-08/G-10 依赖模型层（`ObjectTypeSpec.parent`、`VocabularyTerm.description/broader/narrower`）；G-11 JSON-LD；G-13/G-14/G-15 validate/artifacts/reload；G-16 的 workspace 启停/权限强制/健康检查；G-07 的命名图 provenance（架构 §6 L191 已标 P1）。
2. **C3（P2/P3，未做）**：ABox 快照导出、SPARQL 查询、导入、类层次/词表层级、RDFS/OWL-RL 推理。
3. **观察（不构成失败）**：`StandardsUseCases.export` 返回的 `adapter` 字段是**调用方传入的名字**（用别名调用时保留别名），而 `metadata.adapter`/`X-Adapter-Name` 为 canonical。HTTP 导出响应体是 Turtle 字节、不暴露该 JSON，故仅影响直接调用 use case 的宿主；建议 P1 统一为 canonical（本轮未擅改实现）。
4. **测试成本**：跨进程确定性用例会启动 2 个 Python 子进程（约 +1–2s）；脚本用 `sys.stdout.buffer` 写字节，避免 Windows 文本模式换行转换。
5. **golden 维护**：`_golden_ontology()` 从产物反向重建输入，若未来修订 golden 需同步解析器；这是“golden 即契约”的有意设计，换来的是一旦产物漂移测试必失败。

### D-8 交付与工作区

| 项 | 内容 |
|---|---|
| 改动文件（D 写作用域） | `tests/test_standards_contract.py`（30 项；`git diff --numstat`：+770/−3）；本报告本节追加（+123 行） |
| 未改动 | `src/**`、其他 `tests/**`、`tests/conftest.py`、`tests/golden/**`；未 `git commit` |
| 工作区其余改动 | A/B/C/Lead 的既有改动（`semantic/{spi,registry,owl,skos,formats,__init__}.py`、`application/standards_usecases.py`、`errors.py`、`bootstrap.py`、`interfaces/{api,cli}.py`、`tests/test_semantic_registry.py`、`tests/test_standards_{api,cli,export,turtle}.py`、`pyproject.toml`、CI、README、docs）；D 未触碰 |
| 未跟踪 | `docs/standards-plugin-completion-plan-v1.md`、`docs/standards-plugin-completion-review-v1.md`、`src/onto_platform/semantic/formats.py`、`tests/golden/`、`tests/test_semantic_spi.py`、`.coverage`（覆盖率运行产物） |
