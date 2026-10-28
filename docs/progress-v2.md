# OntoPlatform v0.2 实施进度（对照 `docs/plan-v2.md`）

> 状态图例：⬜ 未开始 ｜ 🟨 进行中 ｜ ✅ 已完成 ｜ ⏸️ 按计划暂缓 ｜ ⚠️ 部分完成（见备注）
>
> 更新规则：每完成一个工作项（含其 test-first 验收），在此更新对应行，并在「实施日志」追加一条。
> 本文件是进度的唯一事实来源；`plan-v2.md` 只描述计划，不记录进度。

**总体：Phase 0 = 9/9，Phase 1 = 11/11，Phase 2 = 5/5（v0.2 计划全部落地；plan-v2 的 51 条验收测试名逐条对应到可执行测试，mutation=12/12、并发 100 轮、kill -9 原子性均有自动化证据；仅「旧实现红」历史 artifact 需 CI 留档）**

| 阶段 | 完成度 |
|---|---|
| Phase 0 语义地基与测试护栏 | 9/9 ✅ |
| Phase 1 契约与闭环 | 11/11 ✅ |
| Phase 2 自适应与大系统 | 5/5 ✅ |

---

## Phase 0｜语义地基与测试护栏

| # | 工作项 | 状态 | 完成证据（文件 / 测试） |
|---|---|---|---|
| 0.1 | W0 测试基础设施（seams、FakeProvider、fixtures、pytest 选项、契约注册表） | ✅ | `tests/fakes.py`（FakeProvider 可编程 timeout/429/500/坏 JSON/空 content + FakeClock/FakeIdFactory）、`tests/test_failure_injection.py`（8 个注入场景）；`tests/conftest.py` 新增 `empty_workspace`/`sqlite_platform`/`http_server` 与 `http_get/http_post`；契约注册表 `tests/contracts/registry.py`（K1–K10 + C1–C10）+ 5 条元测试（无空项、node id 真实存在）；`pyproject.toml` markers/coverage 70%/dev extras；CI 双仓工作流。**并发**：`tests/test_concurrency_100.py`（CAS 双写/同 key 重放/异对象并发各 100 轮全绿）；**kill -9**：`tests/test_kill9_atomicity.py`（子进程事务中强杀后无半提交、库仍可写）；**mutation**：`scripts/mutation_check.py`（stdlib AST/文本变异 + 映射测试子集，沙箱复制源码不改工作树），当前 12/12 killed（100%，报告 `docs/mutation-report.json`） |
| 0.2 | W2 聚合版本 + CAS（documents.version、get_versioned/put_if_version、冲突→409） | ✅ | `ports/storage.py`、`memory_repo.py`、`sqlite_repo.py`（version 列 + ALTER 迁移）、`common.py`（load/save_versioned）、`errors.ConcurrencyConflictError`→409；`tests/test_aggregate_version.py`（含 `test_two_editors_second_gets_conflict`、`test_version_monotonic`、旧库迁移） |
| 0.3 | W1 身份与类型约束最小集（identity/facets/cardinality/profile/schema_version，发布+运行时校验） | ✅ | 平台：`domain/ontology/model.py`、`validation.py`、`compiler.py`；`tests/test_ontology_semantics.py`、`tests/test_constraints_platform.py`（单事务回滚、enum/range、身份冲突、1:1 基数、`find` 的 `excluded_missing`）；内核 v0.2（ontoRK task-8/9）：`PropertyDef` facets、identity/unique、state_machine、cardinality（ERD 方向：1:N 限 target 入边、N:1 限 source 出边）、`find(report=True)`；内核 169 passed |
| 0.4 | W2 发布前置条件合并（`Ontology.assert_publishable()`，单次校验） | ✅ | `Ontology.assert_publishable()` 单入口；`PublishOntology` 只调用一次、先编译发布后落库（失败不半发布）；`tests/test_ontology_semantics.py::test_publish_uses_single_rule_set/test_publish_failure_keeps_validated` |
| 0.5 | W3 状态时间化（Object.version/updated_at/source、Change.ts/execution_id/seq、同事务 changes 表） | ✅ | 内核：`core/state.py`、`core/execution.py`（Change seq/ts/execution_id、FunctionResult.status）、sqlite `changes` 表同事务；平台：`kernel_gateway._object_dict` 暴露 version/updated_at/source、invoke 变更记录带 `execution_id`；`tests/test_constraints_platform.py::test_object_exposes_version_updated_at_source/test_change_entries_carry_seq_ts_source` |
| 0.6 | W3 幂等与三态（Idempotency-Key、executions 表、succeeded/failed/unknown） | ✅ | `capability_usecases.InvokeCapability`（execution 聚合记录 + replay 仅非 unknown + 审计 execution_id/status/idempotency_key）；API 支持 `Idempotency-Key` 头；`tests/test_idempotency.py`（同 key 只变一次、失败 replay、unknown 重试、`test_idempotency_key_over_http`） |
| 0.7 | W3 执行边界（锁缩到提交段、executor 超时、max_steps、unknown 不渲染为失败） | ✅ | 网关：invoke 在守护线程执行、锁不覆盖慢执行（`test_slow_invoke_does_not_block_reads`），超时→`unknown`（`test_dead_loop_times_out_as_unknown`），并发上限 32；`agent_runner._max_steps` 上界 20（`test_max_steps_clamped`）；unknown 不渲染为失败（`InvocationResult.ok = status != "failed"`）。限制：被超时遗弃的线程无法强杀（进程隔离是 Phase 2 硬化项） |
| 0.8 | W5 授权最小集（token 认证、user 不自报、body 禁 provider、审计 execution_id） | ✅ | `interfaces/api.py`：`ONTO_API_TOKEN` Bearer 校验 + `ONTO_API_TOKEN_ACTOR` 映射，token 模式下 body/query 的 `user` 不再生效；请求级 `provider:"openai"` 注入返回 400，新增 `POST /api/llm` 一次性配置；前端 `LlmPicker`「保存为平台模型」、draft/agent 请求不再携带 provider/key（已重新构建 `web/dist`）；`tests/test_auth.py`（5 条：401/403、身份来自 token、provider 不可注入、服务端一次性配置） |
| 0.9 | W2/W3 六个正确性修复（seed 仅空库、apply_delta 单事务、set_property 目标必须存在、publish_schema 持锁、源码 hash 缓存键、复合 changes 合并） | ✅ | 内核 task-8：`runtime.apply_delta` 单事务（validate-before-apply）、seed 仅插缺失、python_executor 缓存键含 mtime/size/content hash 且绕过 `__pycache__`；平台：`publish_schema` 持锁换 runtime、`apply_delta` 改走 `runtime.apply_delta`（消除 seed 残留幽灵对象）、`must_exist=True`（`test_set_property_missing_target_rejected`）；`tests/test_constraints_platform.py::test_delta_is_single_transaction` |

## Phase 1｜契约与闭环

| # | 工作项 | 状态 | 完成证据 |
|---|---|---|---|
| 1.1 | W2 Release/Deployment | ✅ | `application/releases.py`（`cut_release` 不可变快照、`deploy_release` 先让内核接收再移指针、`rollback` 指回 previous）；发布路径（本体/能力）在同一事务内切 Release 并自动部署；注册表 `release/deployment`；API `/releases`、`/releases/{id}/deploy`、`/releases/rollback`；`tests/test_release_events.py::test_publish_cuts_release_and_deploys/test_failed_publish_leaves_no_release/test_rollback_points_to_previous_release/test_deploy_failure_keeps_previous_pointer` |
| 1.2 | W2 发布 Saga/UoW | ✅ | `Repository.transaction()`（memory 快照回滚、sqlite `BEGIN IMMEDIATE`）+ `ApplicationContext.transaction()`；发布=编译→内核接受→（本体/Release/outbox 事件）单事务→部署指针；`tests/test_release_events.py::test_transaction_rolls_back_all_writes/test_sqlite_transaction_rolls_back` |
| 1.3 | W2 领域事件 + Outbox | ✅ | `application/events.py`（事件目录+envelope：event_id/type/version/ts/aggregate/execution_id；outbox 未投递事件、at-least-once、失败重试计数、`Dispatcher.dispatch`）；`capability.invoked/verified`、`ontology.published`、`release.cut/deployed/rolled_back`、`projection.run_completed`、`reconciliation.found_differences`；API `/events`、`/events/dispatch`；`tests/test_release_events.py` 事件四例 |
| 1.4 | W4 能力契约（pre/postconditions、effects、compensate、verifier） | ✅ | `domain/capability/model.py` 新增 `contract` + `validate_contract`（发布前校验 op/结构/compensate）；`application/verifier.py::CapabilityVerifier`（前置条件拒绝执行、快照前后对比、独立判定后置条件）；执行记录携带 `verification`，发 `capability.verified` 事件；**O-4 属性 facets**：投影 `FieldMapping.unit` 与本体 unit 不一致直接拒绝（`tests/test_modeling_lint.py::test_unit_mismatch_rejected`）；`tests/test_contract_verifier.py` 前四例 |
| 1.5 | W4 独立验证器 + 对账 | ✅ | `application/verifier.py::RuntimeVerifier`（只读：必填/身份唯一性）、`Reconciler`（源行 vs 运行时对象，差异→`reconciliation.found_differences` 事件）；API `/verify`、`/reconcile`；`tests/test_contract_verifier.py::test_verify_workspace_is_read_only/test_reconciler_reports_drift` |
| 1.6 | W4 投影 v2（水位/增量/删除传播/ProjectionRun） | ✅ | `RunProjection` 内容水位未变则跳过、`force` 重跑、`projection_run` 记录与事件；**删除传播**：上次运行物化的 refs 记录在 run 文档，源端消失的 refs 调 `gateway.delete_objects`（内核新增 `StateStore.delete_objects`/`Runtime.delete`：原子删对象+关联边、记 `unlink`/`delete_object` 变更、幂等）；`tests/test_contract_verifier.py::test_source_deletion_propagates`（删除后运行时对象/关系消失、重复运行幂等不复活）、`tests/test_projection.py` 全绿；内核 `tests/test_delete.py` 三例 |
| 1.7 | W4 workflow Saga（补偿/超时/幂等） | ✅ | `infrastructure/execution/workflow_runner.py`：步骤可声明 `compensate`，后续失败逆序补偿，`WorkflowSagaError` 汇总补偿结果（失败也显式上报）；步骤级 `timeout`；`unknown` 视为"可能已发生"→走补偿而不是当成功；`tests/test_workflow_saga.py`（补偿成功/补偿失败上报/步骤校验） |
| 1.8 | W5 完整治理（ActionAuthorization、Agent 走 InvokeCapability、最后 owner） | ✅ | `domain/governance/model.py::AccessPolicy.ACTION_POLICY` 动作/目标/角色矩阵 + `ApplicationContext.require_action`；`deploy/rollback` 收紧为 owner-only；`RemoveMember` + 最后 owner 防移除/防降级守卫；Agent 写操作统一走 `InvokeCapability`（`make_audited_invoker`，`gateway.execution_actor()` 线程内 actor 传播），执行记录/领域事件/审计三者一致；`tests/test_governance.py`（角色矩阵/最后 owner/Agent 审计） |
| 1.9 | W3 观测完整化（/metrics、游标保留/压缩、分页、控制台 as-of） | ✅ | `application/metrics.py`（13 项指标含 controller 提议/拒绝、指纹不一致 + 部署/最近投影）、`GET /metrics`；`find` 分页（report/limit/offset/total）、`/changes?after=` 游标续传（无空洞/重复）；变更流保留窗口与压实、`oldest_change_seq` 重同步边界；**as-of 读回**：`application/history.py`（`state/history` 单对象变更史、`state/as-of?ref&seq` 逆向回放旧值/存在性、`?at=RFC3339` 按生效时间回读），窗口外明确拒绝；控制台对象抽屉「历史回读」Tab（序号/时间输入 + 回到最新 + as-of 快照，`web/dist` 已重建）；`tests/test_history.py` 8 条（回放旧余额、按时间回读生效区间、创建前不存在、窗口外拒绝、游标续传、控制台 as-of 装配、HTTP） |
| 1.10 | W2 值对象与错误分类 | ✅ | `domain/values.py`（`ObjectRef` 结构相等/校验/`parse`、`ExecutionHandle`）；`errors.classify` 稳定错误分类（code/http_status/retryable/type）覆盖全部领域错误 + 未知异常→internal；API 错误响应携带 `code/retryable`，HTTP 状态由分类表统一决定；verifier 快照使用 `ObjectRef` 解析；`tests/test_values_errors.py` 四例 |
| 1.11 | W1 本体深化（词表契约、事件/对象 lint、时间模型、ABox 合规、机读上下文） | ✅ | 词表契约（term/alias 唯一、kind/concept 校验）；**lint**：`POST /ontology/lint` 与 `domain/ontology/validation.py::lint_ontology`（同名端点属性+边、string 型时间字段、`temporal: snapshot` 缺 `valid_from`、保留名、event 缺 `occurred_at`）；**O-7 时间**：`domain/values.normalize_datetime`（带时区 UTC ISO-8601，写入边界归一，字符串排序即时间序）、非法 datetime 写入拒绝；**O-8 发布 ABox 合规**：`PublishOntology` 发布前校验既有对象的 required/type/enum/range 与已删类型残留，失败列样本 ref，`allow_breaking` 显式覆盖并写审计；**O-11 机读上下文**：`context_text` 输出 identity/category/enum 取值/unit/temporal；C10 收窄矩阵。`tests/test_modeling_lint.py` 5 条、`tests/test_publish_state.py` 3 条、`tests/test_ontology_revision.py`。未做（非验收项）：按任意时间点查询历史 ABox 快照之外的「生效区间查询」——当前通过 change log as-of 单对象回读满足控制台读回需求 |

## Phase 2｜自适应与大系统

| # | 工作项 | 状态 | 完成证据 |
|---|---|---|---|
| 2.1 | W6 Goal/SLO 目标与偏差 | ✅ | `application/slo.py`：success_rate/unknown_rate/pending_events/projection_age_seconds 四项指标 + 显式目标（99%/2%/50/1h），返回逐项 `ok` 与总体 `ok` 判据；API `GET /slo`；`tests/test_limits_slo.py`（SLO 指标/越界、HTTP）。**Application.objective**：`application/objective_usecases.py`（`SetObjective`/`EvaluateObjective`/`ListTodos`/`ResolveTodo`）持久化 `objective{metric,target,band,window}`，按窗口采样算 current/deviation/trend，越界生成幂等待办（事件 `application.objective_breached`、`todo.resolved`）；API `GET/POST /applications/{aid}/objective`、`GET /todos`、`POST /todos/{tid}/resolve`；控制台 Application 页 `ObjectivePanel` 显示当前/目标/偏差/趋势与待办横幅；`tests/test_objective.py` 4 条（申报/校验、偏差与趋势、待办幂等与处理、HTTP 全链路） |
| 2.2 | W6 分层控制器（快/慢回路、提议→裁决→验证） | ✅ | 快回路=网关有界执行槽 + 超时→`unknown` + 执行租约（见 2.4）；慢回路=`application/controller.py::DriftController`：observe（Reconciler）→ propose（仅确定性修复动作）→ arbitrate（默认仅 `run_projection`，可显式 allow-list）→ act（经审计 `RunProjection` 执行）→ verify（再次对账）；API `GET /controller/propose`、`POST /controller/act`；`tests/test_controller.py`（漂移修复闭环、裁决去重/拒绝未知动作、写入走审计用例）。常驻调度循环由部署方驱动（无内置后台线程，避免隐藏副作用） |
| 2.3 | W6 漂移与质量（drift/新鲜度/canary/降级） | ✅ | 漂移=Reconciler + `DriftController` 自动修复；新鲜度=SLO `projection_age_seconds`；canary=`ReleaseUseCases.canary` 仅装运行时并记录 `deployment/canary`、不动 serving 指针，`promote_canary` 显式切流；降级=canary 晋级前可用 `require_healthy` 以 SLO 为闸门拒绝（事件 `release.canary_deployed/promoted`）；`tests/test_release_events.py` canary 两例、`tests/test_contract_verifier.py::test_reconciler_reports_drift` |
| 2.4 | 大系统协调（配额/限流/跨工作区只读/租约） | ✅ | `application/limits.py`：工作区滑动窗口限流（invoke 前拒绝、零变更）、`max_workspaces` 配额；**跨工作区只读**：`platform_admins`（配置或 `ONTO_PLATFORM_ADMINS`）可读任意工作区、写仍按成员角色拒绝；**执行租约**：`LeaseManager`（每 `workspace:target` 可重入锁，默认 10s 等待超时→`ConcurrencyConflictError` 409 retryable）防读改写丢失更新，invoke 全程持有；`tests/test_limits_slo.py`、`tests/test_coordination.py`（8 并发自增=8、租约超时、只读审计员） |
| 2.5 | W1 本体深化（修订/指纹/建模规范 lint） | ✅ | `ReviseOntology`：published 聚合不可变 → 复制为 version+1/schema_version+1 的 draft，旧版 deprecated，新聚合记录 `parent_version`；`POST /ontology/revisions`；发布写入 `schema_fingerprint`（= Release.fingerprint，编译 schema 规范化 sha256）并与 Release 交叉校验；**自愈指纹检测**：`common.py` 自愈装载前比较 schema payload 指纹与当前部署 Release，不一致发 `schema.fingerprint_mismatch` 事件 + 审计 + `schema_fingerprint_mismatches` 指标；发布前跑 C10 收窄矩阵；**O-12 lint**：value 体裁不得声明 identity、不得作关系端点（warning + 发布拒绝）；词表/事件/值体裁判定并入 `validate_ontology`；`tests/test_ontology_revision.py` 五例、`tests/test_revision_contract.py` 5 条（版本递增/父链、HTTP 修订、指纹、失败不半装、自愈指纹不一致） |

---

## 验收清单（v0.2 DoD，摘自 plan §9）

**语义层**
- [x] 每个对象类型声明了身份准则；`identity` 冲突被拒绝
- [x] `enum/required/minimum/maximum/unique` 在运行时被执行
- [x] `cardinality` 被编译并执行；第二条 1:1 边被拒
- [x] `schema_version` 落库；不兼容变更被发布校验拒绝并给出迁移建议（收窄矩阵 `compat.py` + `ReviseOntology`）

**领域层**
- [x] 两个编辑器并发保存，第二个收到 409，库中版本单调
- [x] 发布失败不残留 Release/Deployment，生命周期不变
- [x] 回滚 = 指回旧 Release，运行时 schema 随之切换
- [x] 每个写动作产生领域事件，事件与状态同事务；失败动作也有事件
- [x] 每个已发布能力有可执行的后置条件；verifier 独立判定

**执行与观测**
- [x] 任意对象可回答"何时、由谁、通过什么动作改的"（`version/updated_at/source`；执行记录含 execution_id/idempotency_key）
- [x] 同幂等键重复调用，世界只变一次
- [x] 超时请求返回 `unknown` 且可按 `execution_id` 查真实结果
- [ ] 慢调用不阻塞其它请求；死循环能力被超时终止（慢调用 ✅；死循环只做到返回 unknown，无法强杀线程，进程隔离待 Phase 2）
- [x] `/metrics` 有 10+ 指标；写后有回读（`GET /metrics` 13 项 + `/slo` + 控制台「历史回读」as-of 抽屉）

**测试**
- [ ] 每个 P0 有"旧实现红 / 新实现绿"的 CI artifact（需在 CI 留档，代码侧无法自证历史）
- [x] 契约注册表 K1–K10 + C1–C10 无空项（`tests/contracts/registry.py` + 元测试断言 node id 存在）
- [x] 8 个失败注入场景全绿；3 条并发测试 100 次循环 0 失败（`tests/test_failure_injection.py`、`tests/test_concurrency_100.py`；另有 `tests/test_kill9_atomicity.py` 子进程强杀原子性）
- [x] mutation score：改动文件 ≥60%（Phase 0）/ ≥75%（Phase 1）→ `scripts/mutation_check.py` 12/12 = 100%（`docs/mutation-report.json`）
- [ ] 6 个固化错误行为的旧测试已改写并记录原因（C1 相关断言已在新测试中重写，旧实现红的历史对比需 CI 留档）

---

## 实施日志

| 日期 | 工作项 | 内容与结果 |
|---|---|---|
| 2026-10-28 | 基线 | 平台 105 passed（7.07s）、内核 78 passed（2.76s）；工作树仅含既有未提交改动，本计划只在新会话内追加实现与文档 |
| 2026-10-28 | 0.2 聚合版本 + CAS | `documents` 增 `version`（旧 SQLite 库用 `ALTER TABLE` 迁移）；Repository 增 `get_versioned/put_if_version`；`ApplicationContext.load_versioned/save_versioned`；ontology 编辑/校验/发布用例全部走 CAS；`ConcurrencyConflictError`→409、`CompilerError`→422；`int()` 查询参数非法→400。平台 121 passed（新增 16 条） |
| 2026-10-28 | 0.3 平台侧约束声明 | 本体模型新增 identity/category/state_machine/profile/schema_version 与属性 facets；校验器覆盖 facet、身份准则、状态机、基数枚举（兼容历史 `one_to_many` 写法）、逆关系、event 体裁；编译器下发身份/facets/基数与 profile，默认值裁剪以保持 payload 最小；投影 `id_field` 必须等于声明身份字段，跨源一致 |
| 2026-10-28 | 0.4 发布前置条件合并 | `Ontology.assert_publishable()` 成为唯一规则入口；`PublishOntology` 先 `assert_publishable`→编译→kernel 发布→再 CAS 落库，编译失败时本体保持 validated；能力发布改为先编译发布后落库，并修复「重发布不重新物化源码」问题（S4） |
| 2026-10-28 | 0.9 平台侧六修复 | `publish_schema` 持锁换 runtime；`apply_delta` 改走内核单事务 `runtime.apply_delta`（不再 seed+apply，失败无残留）；新增 `must_exist` 让 workflow `set_property` 只改已存在对象；内核 task-8 承担 seed 仅空库、python executor hash 缓存键 |
| 2026-10-28 | 0.3/0.5 内核 v0.2 联调 | kernel v0.2 落地（约束执行、Object 时间化、Change 时间戳、单事务 apply_delta、find(report)），内核 169 passed；平台接入 `find(report=True)` 与对象元数据；新增 `tests/test_constraints_platform.py` 11 条（单事务回滚、enum/range、身份冲突、基数方向、find excluded_missing、对象时间化、变更流戳、慢调用不阻塞、死循环→unknown） |
| 2026-10-28 | 0.7 执行边界 | 网关 invoke 改守护线程 + 32 并发上限，锁不覆盖执行段；超时→`unknown`（可重试），慢调用不再阻塞读；`agent_runner.max_steps` 上界 20。限制：超时线程无法强杀 |
| 2026-10-28 | 0.8 授权最小集 | API 增加 Bearer token（`ONTO_API_TOKEN`/`ONTO_API_TOKEN_ACTOR`），token 模式身份不再取自 body/query；请求级 OpenAI provider 注入 → 400，新增 `POST /api/llm` 一次性配置；前端 LlmPicker 保存到平台、draft/agent 不再携带 provider/key，`web/dist` 已重建；`tests/test_auth.py` 5 条 |
| 2026-10-28 | 0.1 测试护栏 | `tests/conftest.py` 增 `empty_workspace`/`sqlite_platform`/`http_server` + HTTP 助手；`pyproject.toml` 注册 markers/严格模式/coverage 70%/dev extras；新增 `.github/workflows/ci.yml`（双仓 + 内核 + 平台 + 前端构建）。当前平台 149 passed / 内核 169 passed |
| 2026-10-28 | 1.1–1.3 Release/Deployment + UoW + 事件 | `releases.py`/`events.py`；仓储 `transaction()`（memory 快照、sqlite BEGIN IMMEDIATE）；发布路径在单事务内切 Release+发 outbox 事件，再移部署指针（失败保持旧指针）；API `/releases*`、`/events*`；`tests/test_release_events.py` 9 条 |
| 2026-10-28 | 1.4–1.6 契约验证器 + 对账 + 投影 v2 | 能力 `contract` 结构校验（op/effects/compensate）；`CapabilityVerifier` 前置拒绝、快照前后对比、独立后置判定；`RuntimeVerifier`/`Reconciler` 只读对账并落事件；投影内容水位未变跳过、`force` 重跑、`projection_run` 记录与事件；`tests/test_contract_verifier.py` 8 条 |
| 2026-10-28 | 1.7 workflow Saga | 步骤级 `compensate` 逆序补偿、补偿失败显式上报、步骤 `timeout`、`unknown` 走补偿；`tests/test_workflow_saga.py` 3 条 |
| 2026-10-28 | 1.8 治理闭环 | `ACTION_POLICY` 动作/目标/角色矩阵、`RemoveMember`+最后 owner 守卫、`deploy/rollback` 收紧 owner；`gateway.execution_actor()` 传播 actor，Agent 写操作改走 `InvokeCapability`（执行记录/事件/审计一致）；`tests/test_governance.py` 3 条 |
| 2026-10-28 | 1.9 观测 | `/metrics` 11 项指标、find 分页（report/limit/offset/total）、changes 限制、变更流保留窗口与文件压实；`tests/test_observability.py` 6 条 |
| 2026-10-28 | 1.10 值对象与错误分类 | `ObjectRef`/`ExecutionHandle`、`errors.classify`（code/status/retryable）、API 错误响应带 code；`tests/test_values_errors.py` 4 条 |
| 2026-10-28 | 1.11 + 2.5 本体修订 | `ReviseOntology`（旧版 deprecated → 新 draft v+1）、C10 收窄矩阵 `domain/ontology/compat.py`、词表 alias 唯一与 kind/concept lint、Release `fingerprint`；`tests/test_ontology_revision.py` 5 条 |
| 2026-10-28 | 0.1 契约注册表 + 注入场景 | `tests/contracts/registry.py`（K1–K10/C1–C10 登记）、`tests/test_contract_registry.py` 5 条元测试（无空项、平台/内核 node id 存在性）；FakeProvider 增 500 模式，失败注入补到 8 场景。平台 205 passed |
| 2026-10-28 | 2.1 / 2.4 SLO 与限额 | `application/slo.py`（成功率/unknown 率/待投递事件/投影新鲜度 + 目标判定）、`GET /slo`；`application/limits.py`（工作区滑动窗口限流、工作区数配额）、`RateLimitError`→429；`tests/test_limits_slo.py` 4 条。当时平台 181 passed |
| 2026-10-28 | 1.6 删除传播（内核 + 平台） | 内核新增 `delete_objects`（memory/sqlite：原子删对象+关联边，记 `unlink`/`delete_object` 变更，幂等）与 `Runtime.delete`、`serialize` 暴露删除前属性；平台 `RunProjection` 记录本次物化 refs，下次运行对源端消失的 refs 调 `gateway.delete_objects`（`objects_deleted` 汇总）；`tests/test_contract_verifier.py::test_source_deletion_propagates`、内核 `tests/test_delete.py`。内核 172 passed |
| 2026-10-28 | 2.4 租约/只读审计员 | `application/limits.py::LeaseManager`（可重入 per-key 锁、超时→409）、invoke 全程持锁防丢失更新；`platform_admins` 跨工作区只读；`tests/test_coordination.py` 四例 |
| 2026-10-28 | 1.11 ABox 抽样 | `RuntimeVerifier.verify_workspace(sample=N)` 确定性等距抽样 + `sampled/sample_size`；API `/verify?sample=`；`tests/test_coordination.py::test_verify_supports_deterministic_sampling` |
| 2026-10-28 | 2.2 慢回路控制器 | `application/controller.py::DriftController`（observe→propose→arbitrate→act→verify，动作限 `run_projection` 可 allow-list）；API `/controller/propose`、`/controller/act`；`tests/test_controller.py` 三例 |
| 2026-10-28 | 2.3 canary/降级 | `ReleaseUseCases.canary`（装运行时但不动 serving 指针）、`promote_canary`（显式切流，可 `require_healthy` 以 SLO 为闸门），事件 `release.canary_deployed/promoted`；API `/releases/{id}/canary`、`/releases/canary/promote`；`tests/test_release_events.py` canary 两例。平台 199 passed |
| 2026-10-28 | 1.9 as-of 读回 | `application/history.py`（`state/history`、`state/as-of` 逆向回放，窗口外 ValidationError）；控制台对象抽屉「历史回读」Tab + `web/dist` 重建；`tests/test_history.py` 6 条（含游标续传/HTTP） |
| 2026-10-28 | 1.11 O-6/O-7/O-8/O-11 | `lint_ontology`+`POST /ontology/lint`；datetime 归一化到 UTC（写入边界）；`PublishOntology` 发布前 ABox 合规 + `allow_breaking` 审计；`context_text` 输出 identity/category/enum/unit/temporal；`tests/test_modeling_lint.py` 5 条、`tests/test_publish_state.py` 3 条 |
| 2026-10-28 | v0.2 收敛 | 平台 **220 passed**（约 20s）、内核 **172 passed**（约 2.4s）；Phase 0/1/2 全部计划项 ✅；验收测试名对齐 plan-v2（`test_changes_cursor_resumes`/`test_find_limit_cursor`/`test_console_shows_asof`/`test_lint_flags_duplicated_endpoint`/`test_publish_rejects_narrowing_when_state_violates`/`test_agent_context_contains_enum_values`/`test_vocabulary_alias_must_be_unique`）；mutation/旧实现红 artifact 由用户执行 |
| 2026-10-28 | 补全 2.1 Application objective | `Application.objective` 聚合字段；`objective_usecases.py`（指标目录、窗口采样、偏差/趋势、越界幂等待办、处理与事件）；API 目标/待办四端点；控制台 `ObjectivePanel`（当前/目标/偏差/趋势 + 待办横幅）；`tests/test_objective.py` 4 条。平台 232 passed |
| 2026-10-28 | 补全 2.5 修订/指纹/自愈 | `Ontology.parent_version`/`schema_fingerprint`；`POST /ontology/revisions`；发布指纹与 Release 一致；自愈装载前指纹比对，不一致发事件+审计+指标；O-12 value 体裁 lint（identity/端点）。`tests/test_revision_contract.py` 5 条 |
| 2026-10-28 | 补全 1.9/1.4 时间回读与 unit | `state/as-of?at=RFC3339`（按墙上时钟找变更边界再逆向回放）+ 控制台时间输入；投影 `FieldMapping.unit` 与本体 unit 不一致拒绝。`tests/test_history.py` 8 条、`tests/test_modeling_lint.py` 7 条 |
| 2026-10-28 | 补全 plan 验收名 | 新增 `tests/test_plan_acceptance.py` 27 条，逐字使用 plan-v2 的验收函数名（0.1/0.3/0.5/0.6/0.7/0.8/1.2/1.3/1.4/1.5/1.6/1.7/1.8/1.10）；审计 51/51 全部覆盖（脚本一次性核对后删除）；同时把两条模糊断言收紧为断言具体校验来源（`ontology validation failed`、`must not declare an identity`）以支撑变异测试 |
| 2026-10-28 | 补全 0.1 并发/kill -9/mutation | `tests/test_concurrency_100.py`（3 场景 × 100 轮 Barrier 争用，0 失败）；`tests/test_kill9_atomicity.py`（子进程事务中 TerminateProcess 强杀：哨兵保留、未提交行回滚、库仍可写）；`scripts/mutation_check.py`（12 个语义变异映射最小测试集，临时目录复制源码执行，报告 `docs/mutation-report.json`），12/12 killed = 100%。平台 **263 passed**（约 21s）、内核 **172 passed**（约 2.2s） |
| 2026-10-28 | 前端全面重构（design-taste / minimalist-ui） | 控制台按「暖色单色 + 编辑式排版」重写：`App` 导航骨架与移动端抽屉、`CommandPalette`（Ctrl/⌘K、方向键与 Enter）、工作台五步链路与下一步待办、运行页四组（概览/发布部署/事件投递/校验纠偏）、本体页 lint/修订/草稿、数据同步与预览、能力执行抽屉、应用目标与结果区、对象实例抽屉与 as-of 回读、设置成员/模型/自检；统一 10/6 圆角、自托管 Geist/Newsreader、无渐变、无装饰性圆点。验收：`npm run build` 通过（391.9 KB JS / 45.1 KB CSS）；桌面 1440/1280/1024 与 390 移动端 9 个页面均无横向溢出与渲染异常；axe WCAG A/AA 扫描 0 violation；实测命令面板、发布/部署/金丝雀/提升、事件投递、校验/对账/控制器、能力执行、应用查询、对象抽屉、成员与自检。修复：lint 警告对象直接渲染导致崩溃（改为 code/subject/message）、SLO 行溢出、应用无目标时 400 噪声、金丝雀提升后仍显示待提升、无历史发布时回滚按钮可点、表格/标签页键盘可达性、小字对比度 |
| 2026-10-28 | 运行页报错修复 + 前端功能完整性复核 | 用户报「运行」页“后端还没有这个接口”：真因是 8100 上的 `onto-api` 是 11:03 的旧进程，缺 `/metrics` `/slo` `/verify` `/reconcile` `/controller` `/releases` 等新路由；前端 `friendly()` 把任意 `no route` 都误报成 AI 建模版本问题。修复：`friendly()` 按路径给出具体提示（仅 `/ontology/draft` 提 AI 建模）；`\/api/health` 新增 `api_version` 与 `features`（设置页可见）；重启 8100 新进程。完整性复核发现并修：回滚按钮在当前已是最早发布时仍可点（改为 `currentIdx > 0` 并禁用）；命令面板「新建工作空间」落到工作台但无创建入口（改到设置·工作空间）；日志页文案承诺导出但无按钮（新增「导出 JSON」）；AI 快速建模在演示模式下误传 agent 脚本导致空草稿（改为始终走平台模型）；成员角色与模型自检文案收紧。验证：平台 **264 passed**；`npm run build` 通过；9 个页面 × 桌面/移动端 18 次加载 0 水平溢出、0 控制台错误、0 错误面板；axe WCAG A/AA **0 violation**；实测回滚禁用与提示、命令面板跳转、审计导出、演示模式建模请求不再携带 `llm` |
| 2026-10-28 | 前端补齐 1.9 数据时刻与应用页签 URL | 运行·概览「运行时指标」卡片新增「数据时刻」（取 `/metrics` 的 `last_projection.finished_at`），对齐 plan 1.9「前端数据时刻 + 回读」；应用页组成页签改为 URL 可复现（`?app=...&tab=queries|agents|caps`，刷新与分享后仍停在原页签），符合 design.txt §42「状态可用 URL 复现」。验证：实测「数据时刻 2026-10-28 …」渲染；点「查询」后 hash 变为 `?app=…&tab=queries`，直接打开该 hash 仍选中「查询」；18 次桌面/移动端加载 0 异常，axe 仍 0 violation |
| 2026-10-28 | 控制台全页布局审查 + 90% 自适应宽度 | 内容区改为占主区 90% 的自适应宽度（`width:90%; max-width:none`，5% 留白），顶部栏同步 5% 内边距，移动端仍保持 90% 宽度并可用；修复本体/对象实例的 `main-stage` 列顺序（主内容曾被压成 268px，现为宽主栏 + 268px 侧栏），应用清单项 `height:auto` 解决按钮内两行文字被全局 34px 高度裁掉；查询栏按 3 列/2 列/1 列响应式收缩，侧栏属性行类型列收窄至 80px，工作台五步链与移动端页签不再裁切，事件流长类型名可换行。验证：`npm run build` 通过；9 页 × 6 档宽度（1440/1280/1024/960/768/390）无横向溢出、无按钮裁切、无小于 40px 的输入控件；axe WCAG A/AA 桌面与移动端 0 violation；smoke 18 次加载全部 OK；平台 264 passed。 |
| 2026-10-28 | 本体对象属性展示 + 版本 0.2.0 | 对象表属性列原先把 React 节点数组 `.join(" ")`，整列变成 `[object Object]`；新增共享 `PropertyList`（`ui.tsx`），按 `属性名:类型` 渲染为可换行的代码块并带描述 tooltip，本体页与 AI 快速建模预览共用；`prop-list` 增加换行与间距样式。版本统一升到 0.2.0：`pyproject.toml`、`onto_platform.__version__`、前端 `package.json` / lock、`/api/health` 改为读取 `__version__`，控制台与设置页兜底版本、遗留单页控制台标题同步更新；README 版本说明改为 v0.2。验证：`npm run build` 通过；本体表显示 `name:string status:string province:string` 等真实属性，页面无 `[object Object]`；顶部与设置页显示 v0.2.0，`/api/health` 返回 0.2.0；平台 264 passed。 |
