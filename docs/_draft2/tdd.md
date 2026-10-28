# TDD / 测试工程视角：OntoPlatform × OntoRK 测试体系诊断与 Test-First 计划

> **评审对象**：`F:\piAgent\ontoPlantform`（平台，Python + React 控制台）与 `F:\piAgent\ontoRK`（内核）。
> **口径**：只读代码与文档，实际运行 `python -m pytest`（未改源码、未装依赖、未启服务）。**路径约定**：平台路径相对 `F:\piAgent\ontoPlantform`；内核路径以 `ontoRK/` 开头、相对 `F:\piAgent`。数字均来自本次实测；判断性结论标【推断】。
> **与既有文档的关系**：`docs/control-theory-review.md` 已从控制论给出 P0/P1 与三阶段路线图（§4、§5.3）。本文**不重复**控制论论证，只回答："这套测试能不能作为规格、能不能在改代码前先红、能不能守住控制契约"。本文的 D 部分是对该路线图的测试侧排程。

---

## 0. 实测基线（工作树含未提交改动）

| 命令 | 结果 |
|---|---|
| `cd F:\piAgent\ontoPlantform; python -m pytest tests/ -q` | **99 passed in 6.43s** |
| `cd F:\piAgent\ontoRK; python -m pytest tests/ -q` | **78 passed in 1.32s** |
| `python -m pytest tests/ -q --collect-only`（平台） | `99 tests collected in 0.50s` |

- 平台测试 99 个、分布在 **14 个 `test_*.py`** 加 `tests/conftest.py`；另有 3 个 `tests/manual/` 探针脚本（`e2e_probe.py` 11.4KB、`layout_probe.mjs`、`react_layout_probe.mjs`）**不被 pytest 收集**（`pyproject.toml:42-43` 仅 `testpaths=["tests"]`，且文件名不符合 `test_*`）。
- 工作树未提交改动：`git status --short` 显示 8 个 M（含 `interfaces/api.py`、`bootstrap.py`、`frontend/src/api.ts`）、`docs/_draft/`、`examples/toll/`、`application/ontology_draft.py`、`tests/test_ontology_draft.py`、`tests/test_toll_sample.py` 等未跟踪项。也就是说 **99 个通过里有 19 个（draft 15 + toll 4）来自尚未提交的新测试**，属于"新功能自带测试"的良好信号。
- 测试工具链几乎为零：`python -m pip list` 只有 `pytest 9.0.3`、`pytest-asyncio`；两仓 `dev` extras 均只有 `pytest`（平台 `pyproject.toml:19`，内核 `ontoRK/pyproject.toml` 的 `dev`）；**无 coverage、hypothesis、pytest-timeout、mutation 工具，两个仓库均无 CI 配置目录**（`Get-ChildItem .github` 为空，`ontoRK` 根目录无 `.github`）。

---

## A. 现有测试组合分析

### A1. 平台 99 个测试的分布（`--collect-only` 按文件计数）

| 文件 | 数 | 断言的行为 | 级别 | 健康度判定 |
|---|---|---|---|---|
| `test_ontology_draft.py` | 15 | 一段文本 → 本体草稿：预览不落库、apply、非法元素丢弃、LLM 失败映射、HTTP 预览/apply/403 | 集成+HTTP | 强，但 1 处脆弱计数断言（见 C 部分） |
| `test_capability.py` | 12 | python/workflow/agent 三类实现：发布、端到端、失败原子性、set_property、重启自愈 | 集成 | 强（原子性测试是范本），含 2 处私有实现测试 |
| `test_projection.py` | 9 | connector 往返 + 投影 e2e + 重跑幂等 + 未知类型 + 连接器失败 | 单元4/集成5 | 中：只证明"happy path 幂等"，不证明失败无残留 |
| `test_gateway.py` | 8 | 网关契约：未发布报错、读写、delta 校验、幂等 link、重发布保状态、invoke 不抛、变更游标 | 集成 | 强；但只断言 `ok is False`，不分类错误 |
| `test_governance.py` | 8 | 生命周期转换、viewer/editor/outsider 权限、审计 | 集成 | 中：**没有 viewer 触发写**、没有审计 detail 断言 |
| `test_frontend_fixes.py` | 8 | B1 变更日志重启存活、B2 expand 字符串、B3 过滤器类型、B4 health | 集成+HTTP | 强：命名即回归清单，是"修一个 bug 先写一个测试"的既有文化 |
| `test_intelligence.py` | 7 | AskOntology 术语/值/一跳；Agent 工具循环、失败 JSON、步数上限、无 provider | 集成 | **含坏规格**：失败动作后仍断言 `ok is True`（C1） |
| `test_ontology_compiler.py` | 6 | 平台本体 → 内核 payload；**产物被内核 `parse_schema` 接受**；能力编译；错误路径 | 单元+契约 | 强（编译契约的唯一证据） |
| `test_persistence.py` | 6 | memory/sqlite repo 往返与一致性、审计日志、code store、sqlite 整平台 e2e | 单元+集成 | 中：两 store 只比 `list` 结果，未跑同一契约集 |
| `test_api.py` | 5 | HTTP 全链路：health/静态页、读、invoke、错误映射 403/400、agent 端点 | 端到端 | 中：1 条**空洞断言**（C1），1 条把 200 固化为契约 |
| `test_application.py` | 5 | 应用/视图/查询/Agent 引用、goal 校验、委托 Intelligence | 集成 | 中：`pytest.raises(Exception)` 过宽 |
| `test_toll_sample.py` | 4 | 样例文件齐全、`toll.db` 逐字节可复现、整链 bootstrap、映射与本体一致 | 黄金/集成 | 强：样例腐化的自动守门 |
| `test_mcp.py` | 3 | MCP 工具表、describe/invoke、ask/agent | 端到端 | 中：`importorskip` 使缺失依赖时静默跳过 |
| `test_cli.py` | 3 | CLI 主流程（workspace/ontology/data/capability/state/ask）、错误退出码 | 端到端 | 中：仅 1 条错误路径 |

### A2. 内核 78 个测试（`--collect-only` 按文件）

`test_types.py` 18、`test_runtime.py` 14、`test_mcp.py` 9、`test_cli.py` 7、`test_memory_store.py` 7、`test_refs.py` 7、`test_http.py` 6、`test_loader.py` 6、`test_sqlite_store.py` 4 = **78**。
内核的亮点是**执行管线的失败原子性成体系**：`ontoRK/tests/test_runtime.py:178-235` 用注入执行器证明"缓冲 mutation 后崩溃=零提交"，`ontoRK/tests/test_memory_store.py:53-64` 证明 store 级回滚。缺口是**并发、时间、幂等、执行记录**完全没有测试：平台测试中 grep `threading` 只命中 4 处且全部用于启动测试服务器（`tests/test_api.py:22`、`tests/test_frontend_fixes.py:90,152`、`tests/test_ontology_draft.py:239`）；对象级 `version/updated_at` 在内核测试中零命中（`version` 仅出现在 `ontoRK/tests/test_loader.py:10` 的 schema 字段），`execution_id` 只有 `ontoRK/tests/test_runtime.py:83` 断言 `startswith("exec-")`。

### A3. 测试层级比例

| 层级 | 平台（99） | 内核（78） | 说明 |
|---|---|---|---|
| 单元/组件（单模块，无 platform facade） | 16（compiler 6、persistence 6、connector 4） | 42（types 18、refs 7、loader 6、memory 7、sqlite 4） | 内核的单元覆盖其实不错 |
| 集成（use case + memory kernel，跨 2 层以上） | 68 | 14（runtime 管线） | 平台的主体；但几乎所有集成测试都跑 memory store |
| 端到端（HTTP/MCP/CLI） | 15（api 5、mcp 3、draft HTTP 2、frontend_fixes HTTP 2、cli 3） | 22（http 6、mcp 9、cli 7） | 都是"起一个 ThreadingHTTPServer 再打自己"，无真实网络依赖 |

**契约测试尚未独立成层**：当前只有 1 组真正的契约测试——`tests/test_ontology_compiler.py:49-57`（平台产物→内核 `parse_schema`），按上表计入单元层；没有"同一契约在两侧各写一版"的结构（`ports/state_store.py` 的 memory/sqlite 契约就是缺的）。

**关键偏差**：平台 68 个集成测试全部由 `tests/conftest.py:89-96` 的 `platform` fixture 构造，而它硬编码 `store="memory"`（`tests/conftest.py:33-34`）；sqlite 路径仅 `test_persistence.py:63-78` 与 `test_capability.py:229-255` 各 1 条。**memory/sqlite 没有参数化跑同一套**——而控制论 review 的 P0-7（启动 seed 覆盖状态）恰恰只发生在 sqlite。

### A3b. 行为测试 vs 实现细节测试（任务口径统计，判定为人工标注）

- **行为测试（断言对外可观察契约，约占 80/99）**：以 HTTP/MCP/CLI 响应、use case 返回值、内核 `FunctionResult`/异常类型为断言对象。典型：`tests/test_api.py:52-90`、`tests/test_capability.py:19-67`、`ontoRK/tests/test_runtime.py:72-131`、`tests/test_toll_sample.py:66-81`（黄金数据）。
- **实现细节/脆弱测试（约 12-18/99【推断】）**：
  - 私有函数直测：`tests/test_capability.py:120-131`（`_resolve`）、`:134-141`（直接 new `GatewayWorkflowRunner`）；
  - 精确内部结构：`tests/test_gateway.py:83-84` 断言 change 的精确 dict 形状（可接受但升级 schema 即红）、`tests/test_projection.py:69-71` 断言 summary 键、`tests/test_intelligence.py:76` 断言"第 2 步 action.tool == finish"（内部步表示）；
  - 计数/文案：`tests/test_ontology_draft.py:123`（warning 数量）；
  - 白盒注入：`ontoRK/tests/test_runtime.py:151,167,192,214` 直接替换 `runtime.executor`（可接受，因为它验证的是管线契约，但属于白盒）。
- **结论**：层次上"集成偏重、单元尚可、契约缺环"；类型上"以正常路径为主，失败路径集中在原子性（内核做得好）与 draft（平台做得好）两块，其余失败路径基本空白"。

### A4. 关键契约是否有测试（✅=有行为断言，⚠️=只覆盖单侧/弱断言，❌=无）

| # | 契约 | 证据 | 判定 |
|---|---|---|---|
| K1 | 平台编译产物被内核 `parse_schema` 接受 | `tests/test_ontology_compiler.py:49-57` | ✅ |
| K2 | `invoke` 永不 raise（内核/网关/HTTP/MCP 四层） | `ontoRK/tests/test_runtime.py:114-131`；`tests/test_gateway.py:133-152`；`tests/test_api.py:84-87`；`ontoRK/tests/test_mcp.py:117-125` | ✅（但 HTTP 状态语义被固化，见 C1 节） |
| K3 | `changes` 语义（改了什么/失败/无变化三态） | 普通 python 能力 ✅ `tests/test_capability.py:25-28`；复合绑定 ❌；失败不记 ❌ | ⚠️ |
| K4 | 变更流：seq 游标、重启回放、损坏尾部 | `tests/test_gateway.py:154-165`；`tests/test_frontend_fixes.py:114-129` | ⚠️（无界、无失败事件、无游标校验断言） |
| K5 | 投影幂等 + 两阶段提交无残留 | `tests/test_projection.py:65-71` 只测"源未变的重跑"；失败只在 fetch 阶段（`:83-93`） | ⚠️ |
| K6 | 发布失败回滚（本体/能力生命周期） | 全仓 grep `rollback|回滚` 在 tests 零命中 | ❌ |
| K7 | 权限（viewer 只读 / editor 写） | `tests/test_governance.py:41-71` 覆盖设计期；`tests/test_ontology_draft.py:271-274` 覆盖 HTTP 403 | ⚠️（**viewer 通过 `capabilities.invoke` 写世界**无测试） |
| K8 | provider 契约（超时/429/5xx/usage） | 只有 `EchoLLMProvider` 与 1 个自定义 `Boom`（`tests/test_ontology_draft.py:171-178`）；`OpenAICompatProvider` 零测试（grep 零命中） | ❌ |
| K9 | ACL 错误映射到 HTTP | 403 ✅ `tests/test_api.py:107-112`；400 ✅ `:88-90`；`int()` 非法→500 无测试 | ⚠️ |
| K10 | 并发：不丢更新、不双花、死锁/阻塞 | **零并发写测试**；仅有 store 单线程回滚（`ontoRK/tests/test_memory_store.py:53-64`） | ❌ |

### A5. 测试替身质量

- **`EchoLLMProvider` 是 FIFO 脚本，且脚本耗尽后静默返回一个"答案"**：`src/onto_platform/infrastructure/llm/echo.py:17-19` → `{"think": "script exhausted", "answer": "无法继续推演"}`。后果：少写一行脚本的测试会以 `ok=True` 通过，**Agent 循环/收束 bug 被掩盖**。它没有 timeout、429、空响应、非法 JSON、幻觉工具名等模式（仅 1 个测试自建 `Boom`）。
- **sqlite 与 memory 未跑同一套契约**：见 A3；且两 store 测试内容不对称——memory 有回滚测试（`ontoRK/tests/test_memory_store.py:53-64`）、sqlite 没有（`ontoRK/tests/test_sqlite_store.py` 仅 4 条，无失败注入）。
- **connector 有 fake 但覆盖偏**：`tests/test_projection.py:42-54` 注册自定义 connector、`:83-93` 注入 `_Broken`，做法正确；但 `rest_ds.py` 没有任何测试。
- **没有 Clock/IdGenerator 替身**：`ApplicationContext` 虽预留 `clock`/`id_factory` 字段（`src/onto_platform/application/common.py:45-46`），但没有测试使用它们；`PlatformConfig` 没有对应开关（`src/onto_platform/bootstrap.py:72-84`）；内核的 `_now()` 与 `uuid4()` 直接写死（`src/onto_platform/infrastructure/runtime/kernel_gateway.py:93-94`、`ontoRK/src/ontology_runtime/runtime/runtime.py:177`）。

### A6. 零测试 / 仅 happy path 的模块（明确清单）

- **完全零测试**：`src/onto_platform/infrastructure/llm/openai_compat.py` 与 `factory.py`（全仓 `grep 'OpenAICompat|provider_from_env' tests/` 零命中）；`src/onto_platform/infrastructure/datasource/rest_ds.py`（`test_projection.py:16-54` 只覆盖 static/sqlite/自定义 fake/坏源）；`ontoRK/src/ontology_runtime/infrastructure/python_executor.py`（只经 `test_runtime` 间接执行，`_cache` 语义无直接断言）；`ontoRK/src/ontology_runtime/adapters/serialize.py::error_to_dict`（`ontoRK/.../serialize.py:58-59`，测试只断言 `type` 字段的间接结果）。
- **零测试的路由/命令**：`interfaces/api.py` 中 applications 的 `add_view/attach_capability`（`:349-362`）、`/audit`（`:261-262`）、`/state/get`（`:252-253`）、`/state/related` 的 `direction`（`:254-257`）、`/changes?after=`（`:258-260`）、请求体大小上限（`_body` `:125-131`）均无 HTTP 测试；`interfaces/mcp.py` 的 `agent_run` 未接 provider（P2-7）也无断言。
- **仅 happy path**：`tests/test_projection.py` 的失败只发生在 `fetch` 阶段（`:83-93`），apply 阶段失败无覆盖；`tests/test_governance.py` 覆盖角色矩阵但没有"viewer 执行写能力"的负例；`tests/test_persistence.py` 无任何 repo 错误路径；`ontoRK/tests/test_sqlite_store.py` 无失败注入。
- **前端**：15 个 `src/**/*.tsx` 组件（含 10 个 `views/`）零测试；只有 `logic.test.ts` 的 8 个纯逻辑用例。

---

## B. TDD 视角的缺口

### B1. 可测试性（testability）：缺哪些 seam

| 现状（难测点） | 证据 | 需要的 seam |
|---|---|---|
| 全局 `RLock` 包住整次 `invoke`（含 LLM 调用） | `src/onto_platform/infrastructure/runtime/kernel_gateway.py:122,239-262` | 锁只包提交段；暴露 lock-wait 指标；可注入 lock/queue 上限 |
| 时间不可注入 | `kernel_gateway.py:93-94`、`application/common.py:26-27` | `Clock` port（platform + kernel），`PlatformConfig(clock=...)` |
| `execution_id` 随机、不落库 | `ontoRK/src/ontology_runtime/runtime/runtime.py:177` | `IdGenerator` port；executions 表；测试可断言确定 id |
| provider 直接实例化 / 请求体注入 | `src/onto_platform/interfaces/api.py:400-412` | Provider 只从服务端配置装配；测试用 FakeProvider 注入 use case |
| 进程内 Python 执行器，无超时 | `ontoRK/src/ontology_runtime/infrastructure/python_executor.py:50-51,85-98` | 可注入 executor（现有）＋超时/进程隔离开关；`_cache` 可清理 |
| 内核懒加载单例 `_KERNEL` | `kernel_gateway.py:38-90` | 构造函数接受 store/executor 工厂，便于失败注入 |
| `find` 无分页、类型错误静默 | `ontoRK/src/ontology_runtime/infrastructure/filters.py:20-21,40-41`；`src/onto_platform/interfaces/api.py:226-251` | `limit/cursor` 契约；类型错误→400 |
| pytest 无 timeout | `pyproject.toml:42-43` 仅 `testpaths` | `pytest-timeout` 才能写"死循环能力"测试 |

### B2. 缺失的测试类型（按控制论 review 的 P0 反推）

1. **property-based**：任意 mutation 序列后 schema 仍成立；投影重放幂等；`SemanticDelta` 内重复 ref 的收敛（P1-17）。当前零 hypothesis。
2. **contract tests**：内核 1 版（`ports/state_store.py` 的 memory/sqlite 同一套）、平台 1 版（`ports/runtime.py` 的网关协议）。当前两份 store 测试不对称。
3. **并发/失败注入**：双线程 recharge（P0-2）、同幂等键并发（P0-3）、120s LLM 超时（P0-4/P0-9）、429/5xx（P1-12）、投影中途失败（P0-6）、kill -9（P0-1/P0-3）。当前零。
4. **回归测试先于修复**：review 的 P0-1..P0-11 没有任何一条有"先失败的测试"。现有唯一例子是 `test_frontend_fixes.py`（B1-B4），说明团队具备该习惯，只是没用于本次 P0。
5. **黄金文件**：compiler 产物目前用内联 dict 断言（`tests/test_ontology_compiler.py:39-46`），可升级为 `tests/golden/*.json` 快照 + 审阅。
6. **mutation testing**：无。断言有效性无法量化——`tests/test_api.py:134` 这类空洞断言就是"测试全绿但零信息"的证据。
7. **验收测试（领域语言）**：见 B3。

### B3. 测试作为规格：缺可执行的"控制语义"

现有测试名多为实现语言（`test_invoke_error_paths`、`test_apply_delta_creates_updates_and_validates`），而不是业务断言。控制语义的 4 条可执行规格完全缺失：

- **动作-验证-结果**：`test_after_recharge_the_balance_equals_old_plus_amount_and_is_read_back_by_an_independent_read`；
- **幂等**：`test_the_same_recharge_request_twice_changes_the_world_once`；
- **超时=未知**：`test_a_timed_out_invocation_is_reported_as_unknown_not_failed`；
- **失败不留痕**：已有内存侧原子性测试（`tests/test_capability.py:46-67` 是范本），但没有跨进程/跨 store 版本。

建议新增 `tests/acceptance/`，用"运营者/审计员"语言命名，只经 HTTP/CLI 断言（不 import 内部模块）。

### B4. 测试基础设施与确定性

- **fixtures 不可组合**：`platform`/`workspace_id`（`tests/conftest.py:89-96`）是"全量种好的交通世界"，没有 `empty_workspace`、`published_only`、`sqlite_platform`、`http_server` 等半成品 fixture；导致 `test_api.py:17-26`、`test_ontology_draft.py:236-242`、`test_frontend_fixes.py:88-109,147-160` 各自复制一份起服务器代码。
- **Determinism**：HTTP 服务器用端口 0（好）；但 `datetime.now`/`uuid4`/`ThreadingHTTPServer` 无等待策略，一旦引入并发测试必须先有 barrier + fake clock，否则 flaky 不可避免【推断】。
- **CI**：无 workflow、无 marker（slow/concurrency/fault-injection）、无 coverage 阈值。当前 `tests/manual/` 三个探针依赖人工启动的 8100 端口，对回归零贡献。
- **前端**：只有 `frontend/src/logic.test.ts`（8 个 vitest 用例，68 行），覆盖 `friendly`/`buildFilters`/demo script 契约；零组件测试、零交互测试，且 `vitest` 不参与任何门禁（`frontend/package.json` 有 `test` 脚本但仓库无 CI）。

---

## C. 测试缺陷清单

### C1. 必须先改的"固化错误行为"的测试（改代码前先改测试）

| ID | 现象 | 证据 | 后果 |
|---|---|---|---|
| **C1-1** | 失败的动作之后仍断言 `run["ok"] is True` | `tests/test_intelligence.py:62-76`（第 65-67 行注入失败的 `invoke_capability`，第 72 行 `assert run["ok"] is True`） | 把"动作失败=运行成功"写成了规格；P1-9/反馈验证的任何修复都会先撞红它 |
| **C1-2** | 同一内核 `related` 的两份**互相矛盾**的测试 | `ontoRK/tests/test_http.py:61-64` 断言 hydrated 对象 `related[0]["properties"]["name"]`；`ontoRK/tests/test_mcp.py:87-90` 断言 `data == ["TollStation/ST001"]`（纯 ref） | 语义漂移被同时固化（P1-2）；统一哪一边都要先删/改一个"绿色"测试 |
| **C1-3** | 复合能力 `changes == []` 被视为正常 | `tests/test_capability.py:99-101`（注释"observation workflow mutates nothing"） | 该断言无法区分"真的没写"与"写了但 changes 被丢弃"（P0-5），给了假保护 |
| **C1-4** | `invoke` 业务失败仍断言 HTTP 200 | `tests/test_api.py:84-87` | 把 P1-18 固化为契约；调用方无法据状态码判定失败 |
| **C1-5** | 空洞断言（永远通过） | `tests/test_api.py:134`：`assert status in (400, 403, 500, 503) or body.get("ok") is False` | 该用例对任何响应都通过，等于没有测试 |
| **C1-6** | 名为"幂等"的测试弱于其名 | `tests/test_projection.py:65-71` 只跑"源未变"的第二次 | 名字给了虚假保证；重复行/源变化/失败重放均未覆盖（P1-7） |

> 处置原则：C1 的每一项都在同一 PR 里**先重写测试（先红）→ 再改实现**。C1-1/C1-2 属于"规格变更"，需在 PR 描述里显式记录"旧断言为何是错的"。

### C2. 缺陷清单（P0/P1/P2）

| 级别 | 缺陷 | 证据 | 后果 |
|---|---|---|---|
| P0 | 无并发写测试，P0-2 无法 test-first | 见 A4-K10 | loss-update 只能靠 review 发现 |
| P0 | 无时间/版本断言，P0-1 无失败测试可写 | 见 A2 | "状态时间化"无法验收 |
| P0 | 无执行记录/幂等测试，P0-3 无失败测试可写 | `ontoRK/tests/test_runtime.py:83` 只断言前缀 | 超时重试=双花无可回归 |
| P0 | 失败场景 `ok=True`（C1-1） | 同上 | 反馈验证改动被旧测试阻挡 |
| P0 | 矛盾 related 测试（C1-2） | 同上 | 契约统一受阻 |
| P1 | `pytest.raises(Exception)` 过宽 | `tests/test_application.py:32-34` | 无法区分业务校验与程序错误 |
| P1 | 断言 warning 数量 | `tests/test_ontology_draft.py:123` `len(result["warnings"]) == 5` | 文案微调即红；未断言 warning 类别 |
| P1 | 直接测私有函数/内部 runner | `tests/test_capability.py:120-131`（`_resolve`）、`:134-141` | 重构即红，不保护契约 |
| P1 | Echo 脚本耗尽静默"作答" | `src/onto_platform/infrastructure/llm/echo.py:17-19` | 见 A5；掩盖 Agent 循环 bug |
| P1 | `importorskip` 让 MCP 测试可静默消失 | `tests/test_mcp.py:12`；`ontoRK/tests/test_mcp.py:12` | CI 缺依赖时覆盖率无声下降 |
| P2 | HTTP 测试服务器 4 处重复 | A/B4 证据 | 维护成本、行为漂移 |
| P2 | 前端 8 个 vitest 不在门禁 | `frontend/src/logic.test.ts`；无 CI | 前端契约漂移无人发现 |
| P2 | `tests/manual/` 探针不可自动运行 | 见 §0 | 腐化 |
| P2 | 无 coverage/无 mutation/无 timeout | `pip list`、两个 `pyproject.toml` | 断言有效性不可度量 |

---

## D. Test-first 实施顺序与门禁

### D1. 策略（红→绿→重构，按依赖排序）

1. **先补测试基础设施**（1-2 天，属于"为测试写测试"）：`conftest` 增加 `empty_workspace`、`sqlite_platform`、`http_server`、`fake_clock`、`FakeProvider`（可编程 timeout/429/坏 JSON/非法工具名）、`barrier_invoke`；`pyproject.toml` 增 `pytest-cov`、`pytest-timeout`、`hypothesis`、`mutmut` 与 marker（`slow`/`concurrency`/`fault`）；加 `pytest.ini_options`：`timeout=30`、`--strict-markers`。
2. **每个 P0 先写失败测试并确认红是"断言失败"**（不是 import/语法错误），PR 里贴红→绿两段输出；旧测试中与目标相冲突的先按 C1 改写。
3. **实现只在测试红之后**；实现 PR 不允许顺手改测试期望值（除非对应 C1 项，且单独 commit）。
4. **P0 全绿后再进 P1**；P1 每项同样先红。P2 跟随重构按需补。

### D2. 第一批失败测试（Phase 0，按 P0 顺序；先红后实现）

| 测试名 | 文件 | Given / When / Then | 对应 P0 |
|---|---|---|---|
| `test_object_exposes_version_updated_at_source` | `ontoRK/tests/test_state_time.py` | Given 对象已 seed / When `runtime.get` / Then 返回 `version>=1, updated_at` ISO8601 UTC, `source`；且 `set_property` 后 version+1 | P0-1 |
| `test_change_carries_seq_ts_execution_id` | `ontoRK/tests/test_state_time.py` | Given 一次 invoke / When 读 changes / Then 每条有 `seq` 单调、`ts`、`execution_id` 与结果一致 | P0-1 |
| `test_platform_get_returns_version_and_age` | `tests/test_contracts_runtime.py` | Given 投影写入 / When 平台 `get_object` / Then 含 version/updated_at；`/api/.../state/get` 同 | P0-1 |
| `test_two_concurrent_recharges_lose_no_update` | `ontoRK/tests/test_concurrency.py`（store/runtime 侧）+ `tests/test_concurrency.py`（平台侧） | Given balance=100 / When 两线程各 +100（Barrier 同步）/ Then 终值 300，或一方 `ConcurrentModification`→409；不得 200 | P0-2 |
| `test_stale_mutation_rejected_with_conflict` | `ontoRK/tests/test_concurrency.py` | Given `expected_version=1` / When 已到 version 2 时写入 / Then `ConcurrentModification`，无变更 | P0-2 |
| `test_same_idempotency_key_applies_once` | `tests/test_idempotency.py` | Given 同 key 两次 `POST /invoke` / When 第二次 / Then 世界只变一次，第二次返回首次结果 | P0-3 |
| `test_execution_record_queryable_after_timeout` | `tests/test_idempotency.py` | Given 注入超时 provider / When 调用 / Then `executions(status="unknown")` 可查，不重复写 | P0-3 |
| `test_result_is_tristate_succeeded_failed_unknown` | `tests/test_idempotency.py` | Given 成功/业务失败/超时三例 / When 调用 / Then 状态分别为三态，不混用 | P0-3 |
| `test_llm_timeout_is_unknown_not_failed` | `tests/test_timeout_semantics.py` | Given FakeProvider 睡眠 > deadline / When invoke / Then `unknown`，HTTP 层可区分；**前端不再渲染为"失败"** | P0-4 |
| `test_slow_invoke_does_not_block_reads` | `tests/test_concurrency.py` | Given 一次 1s 慢 invoke 在途 / When 并发 20 次读 / Then P95 不劣化 >2×，读不排队 | P0-9 |
| `test_dead_loop_capability_times_out` | `tests/test_failure_injection.py` | Given `while True` 能力 / When invoke / Then 在超时内返回 `timeout` 错误码，进程/线程存活 | P0-9 |
| `test_composite_capability_reports_inner_changes` | `tests/test_contracts_runtime.py` | Given workflow+set_property 能力 / When invoke / Then 外层 `changes` 非空，含 `set_property` 与 execution_id | P0-5 |
| `test_projection_failure_leaves_no_residue` | `tests/test_projection_failure.py` | Given 先 seed 后 link 失败（目标不存在）/ When `run_projection` / Then `ValidationError` 且内核无新对象、无半条边 | P0-6 |
| `test_restart_does_not_reset_state` | `ontoRK/tests/test_sqlite_store.py` | Given sqlite 已改值 / When 重新 `build_runtime`（带 seed）/ Then 累积状态保留，seed 不覆盖 | P0-7 |
| `test_viewer_cannot_invoke_write` | `tests/test_authorization.py` | Given alice=viewer / When `capabilities.invoke(recharge)` / Then 403，世界未变 | P0-8 |
| `test_agent_write_action_is_audited_with_execution_id` | `tests/test_authorization.py` | Given 脚本 Agent 调写能力 / When run / Then 审计含 actor+execution_id+能力+目标 | P0-8 |
| `test_provider_cannot_be_injected_from_request_body` | `tests/test_authorization.py` | Given body `llm.base_url` / When `POST /agent` / Then 400，且不创建 provider | P0-8 |
| `test_set_property_on_missing_object_fails_not_creates` | `tests/test_workflow_safety.py` | Given 拼错 ref / When workflow set_property / Then 错误且不产生幽灵对象 | P0-10 |
| `test_republished_source_takes_effect_without_restart` | `tests/test_capability.py`（追加） | Given v1 能力 invoke=1 / When 改源码重发布 / Then 下一次 invoke=2（同进程） | P0-11 |
| `test_delta_with_duplicate_ref_creates_once` | `tests/test_projection_failure.py` | Given 同一 delta 重复 `Type/Id` / When apply / Then 只 1 个对象 | P1-17（顺手） |

> 另需 3 条**元测试**守住门禁本身：`test_related_returns_refs_on_every_surface`（先统一 C1-2）、`test_failed_invoke_is_not_ok`（先改 C1-1）、`test_invoke_business_failure_maps_to_422`（先改 C1-4）。

### D2b. 测试红之后的实现顺序（一一对应，禁止先写实现）

| 测试（红） | 随后才允许的实现改动 | 先动的契约文件 |
|---|---|---|
| D2 前 3 条 | `Object` 增 `version/updated_at/source`；`Change` 增 `ts/execution_id/seq`；SQLite 增列与同事务 changes 表；`get`/HTTP 回传 | `ontoRK/src/ontology_runtime/core/state.py`、`core/execution.py`、`infrastructure/sqlite.py` |
| 并发/冲突 2 条 | `Mutation.expected_version`、`UPDATE ... WHERE version=?`、`ConcurrentModification`→409；gateway 的读-改-写改 CAS | `core/execution.py`、`infrastructure/memory.py`、`infrastructure/sqlite.py`、`src/onto_platform/infrastructure/runtime/kernel_gateway.py` |
| 幂等/三态 3 条 | `Idempotency-Key` 入参、`executions` 表、`FunctionResult.status` 三态、接口层保留 unknown | `ports/runtime.py`、`ports/execution.py`、`infrastructure/sqlite.py`、`interfaces/api.py` |
| 超时/不阻塞 3 条 | 锁缩到提交段（读→执行→提交）、executor 超时与并发上限、服务端 `max_steps` 上界、`unknown` 的 HTTP 语义 | `kernel_gateway.py:239-262`、`platform_executor.py`、`interfaces/api.py:373-390` |
| 复合 changes 1 条 | `PlatformExecutor` 把内层 `apply_delta` 的 changes 合并回外层结果；内核不再无条件覆盖 | `infrastructure/execution/platform_executor.py:48-67`、`ontoRK/.../runtime.py:205-213` |
| 投影残留 2 条 | `apply_delta` 先校验端点存在、creates+mutations 单事务；重复 ref 去重 | `kernel_gateway.py:287-357`、`ports/runtime.py` |
| 重启不覆盖 1 条 | seed 仅空库执行；`INSERT OR REPLACE`→`INSERT OR IGNORE`/`ON CONFLICT DO UPDATE` | `ontoRK/.../adapters/assembly.py:33-35`、`infrastructure/sqlite.py:53-70` |
| 权限/审计 3 条 | `InvokeCapability` 升 editor、Agent 写走该 use case、user 不信任、body 禁 provider | `application/capability_usecases.py:117`、`application/agent_runner.py:151-156`、`interfaces/api.py:205,285,400-412` |
| 幽灵对象 1 条 | `set_property` 校验目标存在（禁止隐式 create） | `infrastructure/execution/workflow_runner.py:98-117`、`kernel_gateway.py:287-305` |
| 重发布生效 1 条 | 执行器缓存键加入源码 hash；re-publish 强制重写 entrypoint | `ontoRK/.../python_executor.py:79-82`、`capability_usecases.py:74-82` |
| 发布回滚（P1-8） | 生命周期与编译原子化；编译成功后才置 published | `application/ontology_usecases.py:168-172`、`capability_usecases.py:84-94` |

### D3. 第二批失败测试（Phase 1，每项仍先红）

- **P1-1 观测质量**：`tests/test_find_contract.py::test_find_supports_limit_and_cursor`、`::test_filter_type_mismatch_returns_400_not_empty`（内核 `filters.py:20-21,40-41` 现在静默丢行）。
- **P1-2 语义统一**：`ontoRK/tests/test_contracts_related.py::test_related_returns_refs_on_http_mcp_and_platform`（改写 `test_http.py:61-64`）。
- **P1-5 错误模型**：`tests/test_http_semantics.py::test_error_payload_has_code_stage_retryable`、`::test_invalid_int_param_returns_400_not_500`（`src/onto_platform/interfaces/api.py:86-92`）。
- **P1-6 变更流**：`tests/test_changes.py::test_failed_and_noop_invocations_are_recorded`、`::test_cursor_resumes_after_restart_without_skips`。
- **P1-7 投影升级**：`tests/test_projection.py::test_source_deletion_propagates`、`::test_watermark_skips_unchanged_rows`、`::test_reconcile_reports_count_mismatch`。
- **P1-8 发布回滚**：`tests/test_publish_rollback.py::test_failed_publish_keeps_ontology_validated`（`src/onto_platform/application/ontology_usecases.py:168-172`）、`::test_failed_capability_publish_keeps_draft`（`capability_usecases.py:84-94`）。
- **P1-9 独立验证**：`tests/test_agent_verification.py::test_finish_without_observation_is_not_ok`、`::test_run_ok_false_when_any_action_failed`（先改 C1-1）。
- **P1-10 Saga**：`tests/test_workflow_saga.py::test_step_k_failure_compensates_prior_steps`、`::test_workflow_has_per_step_timeout`。
- **P1-12 provider**：`tests/test_provider.py::test_429_retries_with_backoff_then_definitely_failed`、`::test_5xx_is_classified_and_not_retried_forever`、`::test_usage_and_latency_recorded`。
- **P1-16 发布临界区**：`tests/test_concurrency.py::test_publish_during_invoke_is_consistent`。
- **P1-18 HTTP 语义**：`tests/test_http_semantics.py::test_body_size_limit`、`::test_unknown_route_is_404_json`。

### D4. 每个 Phase 的测试门禁（全部满足才可进下一 Phase）

| 门禁 | Phase 0 | Phase 1 | Phase 2 |
|---|---|---|---|
| **契约覆盖** | K1/K2/K3/K6/K7/K10 各有≥1 平台 + 1 内核行为测试；`tests/contracts_registry.py` + 元测试断言矩阵无空项 | K3/K4/K5/K9 补齐三态与错误分类 | 目标/漂移类契约（新增）全覆盖 |
| **失败注入全绿** | 控制论 review §6.2 的 8 个场景中，除"kill -9"用子进程模拟外全部有测试 | 全部 8 场景 + 429/5xx/部分提交 | 保留并纳入 nightly |
| **并发** | 至少 3 条并发测试（双写、同幂等键、慢调用不阻塞读），Barrier 同步、无 `sleep>50ms` | + 发布 vs 执行、workflow 并发 | + 多工作区隔离 |
| **红→绿证据** | 每个 P0 测试在旧实现上失败（记录在 PR/CI artifact），且失败原因是断言 | 同左 | 同左 |
| **mutation score**（改动文件） | ≥60%【建议值】 | ≥75% | ≥85% |
| **flaky** | 0 容忍：无重试插件；两次失败即 quarantine；测试耗时 P95 < 5s | 同左；CI 跑 3 次全绿才合并 | 同左 + nightly 稳定性报告 |
| **前端** | 0 新增 | `logic.test.ts` 进 CI + "unknown 不渲染为失败"的组件测试 | 关键视图冒烟（playwright 可选） |

### D5. flaky 与 CI 控制（当前为零，必须新建）

1. 两个仓库各加一个 CI workflow：`pytest -q --timeout=30 -p no:cacheprovider`，Python 3.10/3.12 矩阵；`tests/manual/` 永不执行。
2. 固定随机性：测试内禁用未播种随机；hypothesis 用 `derandomize=True`（property 测试）。
3. 时间/ID：一律通过 `fake_clock`/`FakeIdGenerator`；禁止在测试里 `time.sleep` 等待（用 `threading.Event`）。
4. 慢测试分离：`-m "not slow and not concurrency"` 作为 PR 门禁，`slow`/`concurrency`/`fault` 在合并队列与 nightly 跑全量。
5. 失败注入 provider 必须**确定**（脚本化响应+可控延迟），绝不访问公网；`OpenAICompatProvider` 用本地 `http.server` 假端点测试。

### D6. 工作量与验收（测试侧）

| 工作 | 产出 | 估时【推断】 |
|---|---|---|
| 测试基础设施（fixtures + FakeProvider/Clock/Id + CI + marker/timeout/coverage） | 前置条件，不产出业务测试 | 1.5–2 人日 |
| Phase 0 的 20 条失败测试 + 3 条元测试 + 6 处 C1 改写 | 99→约 130 个测试，全绿 | 4–5 人日 |
| mutation 基线（domain/ + application/）与空洞断言清理 | mutation 报告 + C2 清零 | 1–2 人日 |
| Phase 1 测试（D3 的 20 余条） | 约 160 个测试 | 5–7 人日 |

**Phase 0 测试侧验收（可机械判定）**：① §6.2 失败注入 8 场景全绿；② 3 条并发测试 100 次循环 0 失败；③ 每个 P0 有"旧实现红 / 新实现绿"的 CI artifact；④ C1 的 6 项旧测试已改写且有 commit 说明；⑤ `pytest --strict-markers --timeout=30` 全绿且总时长 < 60s。


---

## E. 结论

现有测试体系在"**正常路径 + 内存实现 + 单进程原子性**"上是健康的（177 绿、样例可复现、编译器契约有内核侧验证、修 bug 先补测试的文化已存在），但它**不是控制语义的规格**：时间/版本/幂等/并发/超时/回滚/权限这些 P0 恰好全部没有失败测试，而 5 个现有测试（C1-1..C1-5）还把错误行为钉成了"正确"。

**最先写的 10 个测试**（全部先红）：`test_object_exposes_version_updated_at_source`、`test_change_carries_seq_ts_execution_id`、`test_two_concurrent_recharges_lose_no_update`、`test_stale_mutation_rejected_with_conflict`、`test_same_idempotency_key_applies_once`、`test_result_is_tristate_succeeded_failed_unknown`、`test_llm_timeout_is_unknown_not_failed`、`test_composite_capability_reports_inner_changes`、`test_projection_failure_leaves_no_residue`、`test_viewer_cannot_invoke_write`。配套先改的 6 个旧测试见 C1——**测试先于实现，旧断言先于新断言**，这是本计划与 §5.3 路线图在工程上的唯一区别。

### 本次评审的边界（未验证项）

- 未运行 `vitest`/前端构建、未起 API 服务、未安装任何测试工具：mutation score、coverage 数字均为**门槛建议**而非实测。
- 未做真实压测与 kill -9 实验：并发/失败注入类结论均来自代码路径与现有测试的缺失，属【推断】。
- 未修改任何源码或既有测试；C1 的 6 项"应先改"仅给出改写方向与证据，具体改写留待实施 PR。
