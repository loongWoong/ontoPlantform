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
