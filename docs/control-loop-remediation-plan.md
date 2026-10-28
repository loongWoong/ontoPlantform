# OntoPlatform v0.3 控制闭环修复计划

- 依据：`docs/control-loop-report-v2.md` 的 15 项问题（P0-1 到 P2-15）与 39 项功能矩阵。
- 目标版本：`v0.3.0`，API 版本从 `2` 升到 `3`。
- 计划性质：修复计划，不是实现记录；本文件不声称任何代码已经修改。
- 估算口径：约 45 到 55 人日，含后端、前端、测试、迁移与联调，不含 20% 以上缺陷缓冲；1 人约 10 到 12 周，2 人约 6 到 7 周。
- 完成定义：报告中的 15 个问题全部有代码、测试、界面和文档证据；报告判定为设计态或只读的功能不强行加自动控制，但必须把“未闭环”如实显示出来。

## 1. 结论

v0.2 的主要风险不是“没有自动化”，而是“看起来闭环，实际没有闭环”。修复顺序必须先修真相，再修执行安全，再修发布安全，最后打开时钟与反馈回路。否则自动调度只会更快地产生假成功和假修复。

本计划把修复拆成 6 个工作包：

| 工作包 | 主题 | 覆盖问题 | 估算 |
|---|---|---|---|
| WP0 | 契约、测试基线、版本与迁移入口 | 全部问题的验收骨架 | 2 人日 |
| WP1 | 真相层与执行账本 | P0-1、P0-2、P0-4 | 10 人日 |
| WP2 | 发布环隔离与崩溃恢复 | P0-3、P1-5 | 8 人日 |
| WP3 | 时钟、投递与纠偏回路 | P1-6、P1-7、P1-8、P1-9、P1-10 | 12 人日 |
| WP4 | 身份、模型、Agent 与观测边界 | P1-11、P2-12、P2-13、P2-14、P2-15 | 9 人日 |
| WP5 | 集成、迁移、全量验收与发布 | 全部问题的最终证据 | 4 人日 |
| 缓冲 | 联调、返工、构建与演示准备 | 不分配给具体问题 | 8 人日 |

这不是“全部自动化”的计划。对象查询、问业务世界、审计查看、健康查看、五步引导、数据源连接测试、映射编辑属于设计态或只读功能，报告判定为开环是正确的。对这些功能只修三件事：显示真实状态、标注快照时间、拒绝把未执行说成已执行。

## 2. 修复原则

1. 先真相后自动化。任何 `ok`、绿色、已投递、已闭环都必须能从执行记录、发布记录或观测记录中复算出来。
2. 成功只有一个定义：`status == "succeeded"`。`unknown` 不是成功，`failed` 不是成功，契约后置条件不成立也不是成功。
3. 结果未知不自动重试。超时、进程崩溃、执行线程失联都进入 `unknown`，由人或外部证据解析，不允许同一幂等键再次执行。
4. 发布指针是运行时的唯一真相。`schema_payload` 只是发布过程中的暂存物，进程重启必须从 `deployment/current` 指向的 Release 恢复运行时。
5. 金丝雀必须是隔离环。v0.3 的金丝雀是“影子验证环”，不接真实流量；不能宣传成流量金丝雀，也不能让金丝雀写当前运行时。
6. 对账必须有归属。只对某个投影声明拥有的对象和关系做对账；过滤条件、缺失 ID 策略和 authoritative/additive 模式必须显式。
7. 调度只做可重入的短作业。单进程调度，作业幂等，失败隔离；多实例不在 v0.3 范围内。
8. 人可以推翻系统，但必须留下证据。解析 `unknown`、关闭待办、提升金丝雀、回滚都需要操作者、理由和时间。
9. 报告中的限制要保留在文档和界面上。不能用一个新名词把未实现的能力包装成已实现。

## 3. 目标状态

### 3.1 执行状态机

```text
requested
  -> claimed(running)
       -> succeeded
       -> failed
       -> unknown
            -> resolved_applied
            -> resolved_not_applied
```

规则：

- `claimed` 由执行账本原子写入，`put_if_version(expected_version=0)` 成功才算拿到执行权。
- 同一幂等键在 `running` 时返回冲突或进行中，不启动第二次执行。
- 同一幂等键在 `succeeded` 或 `failed` 时返回原执行记录，`idempotent_replay=true`。
- 同一幂等键在 `unknown` 时返回 `retryable=true` 但拒绝自动重试，必须走解析入口。
- 进程重启时，属于旧实例的 `running` 记录全部转为 `unknown`，原因写 `process_restart`。
- 执行记录追加尝试历史，不再用同一个 `key_<key>` 文档覆盖前一次结果。

### 3.2 发布状态机

```text
Release: cut -> immutable
Deployment(current): pending -> active -> superseded
Deployment(canary): staged -> validated -> promoted | rejected | expired
```

规则：

- Release 创建后不可修改。
- 当前部署指针写入前可以处于 `pending`，但 `gateway_for` 只认 `deployment/current` 指向的 Release。
- 运行时发布成功但数据库事务失败时，下一次读或启动恢复必须把运行时拉回当前 Release，不能继续服务未提交的 schema。
- 金丝雀使用独立网关实例和独立数据目录，永不调用当前网关。
- 提升前必须有新鲜的 canary check；没有 check 或 check 过期时提升按钮不可用。

### 3.3 投递状态机

```text
pending -> delivered
pending -> retrying -> delivered | dead
pending -> unrouted
```

规则：

- 没有消费者时，事件进入 `unrouted`，不能计为已投递。
- 消费者失败时保留 `pending`，记录 `attempts`、`last_error`、`next_attempt_at`。
- 达到最大尝试次数后进入 `dead`，保留原文和错误，供人工重放。
- 所有投递必须有至少一个真实消费者，不能靠空消费者把数字刷绿。

### 3.4 目标与待办状态机

```text
Objective: no_data | insufficient | ok | breach | recovered
Todo: open -> acknowledged -> resolved | overridden
```

规则：

- 没有样本时是 `no_data`，不是 `ok`。
- 样本数不足时是 `insufficient`，不是 `ok`。
- 同一个目标在同一评估窗口只产生一个未关闭待办。
- 关闭待办前必须重新评估；如果仍然越界，默认拒绝关闭，owner 只能带理由强制关闭。
- 待办必须带证据：指标、窗口、样本数、越界值、目标值、评估时间、建议动作、验收条件。

### 3.5 新增模块

| 模块 | 作用 |
|---|---|
| `application/execution_ledger.py` | 执行认领、幂等、未知解析、重启恢复 |
| `application/execution_runner.py` | 有界执行器，超时只影响等待，不放弃执行所有权 |
| `application/release_health.py` | 金丝雀探针、提升门禁、部署一致性检查 |
| `application/scheduler.py` | 单进程周期调度，作业注册、隔离、状态 |
| `application/consumers.py` | 事件消费者：审计、信号、控制器信号、可选 webhook |
| `application/control_signals.py` | 把执行失败、对账差异、目标越界收敛为待办或控制器输入 |
| `application/projection_ownership.py` | 投影对象归属索引与对账范围 |
| `application/identity.py` | 本地模式、令牌模式、actor 来源和读权限 |
| `application/llm_config.py` | 工作空间级模型配置、加密、版本和审计 |
| `application/goal_verifier.py` | Agent 目标验收谓词和证据 |
| `frontend/src/components/ValueView.tsx` | 对象/数组/时间/空值的统一渲染 |
| `frontend/src/lib/time.ts` | 本地时间到 UTC ISO 的显式转换 |
| `frontend/src/lib/status.ts` | 状态到文案/颜色/图标的唯一映射 |

## 4. 问题追踪矩阵

### 4.1 P0 问题

| ID | 问题 | 根因落点 | 修复包 | 必须通过的验收 |
|---|---|---|---|---|
| P0-1 | 成功判定失真：`unknown` 和契约后置条件失败仍返回 `ok=true` | `ports/runtime.py` 的 `InvocationResult.ok` 只判断 `status != "failed"`；`application/capability_usecases.py` 将后置条件只放进 `verification`，不回写 `status/ok`；前端只 toast `r.ok` | WP1 | 后置条件失败返回 `status=failed, ok=false`，执行记录和事件一致；超时返回 `status=unknown, ok=false, retryable=true`；前端显示状态而不是布尔成功 |
| P0-2 | 幂等与超时语义不成立：并发同键执行两次，超时后重试再执行，执行记录被覆盖 | 幂等检查在租约之前；没有原子认领；执行记录 ID 固定为 `key_<key>`；网关超时后放弃线程但仍返回可重试未知；没有未知解析入口 | WP1 | 100 并发的同一幂等键只产生一次世界变化和一条执行记录；超时后同键不再执行；解析前记录为 `unknown`；进程重启后旧 `running` 变 `unknown` |
| P0-3 | 金丝雀不隔离，回滚重启后 schema 漂移 | `deploy_release` 无论 ring 都调用 `ctx.gateway_for(workspace_id)`；`gateway_for` 从最新 `schema_payload` 自愈，而不是从 `deployment/current` 的 Release；提升门禁只看全局 SLO | WP2 | 金丝雀部署不改变当前运行时的函数/类型；提升必须有新鲜 canary check；回滚后重启运行时指纹等于当前 Release 指纹；不再出现 `schema.fingerprint_mismatch` |
| P0-4 | SLO 零样本显示 100%，窗口是全时段 | `slo.py` 零样本默认 `success_rate=1.0`；`metrics.py` 使用全时段计数；没有最小样本和窗口；投影新鲜度无运行时取 `0.0` | WP1 | 空工作空间返回 `no_data` 且 `ok=false`；1 小时窗口外的失败不进入当前值；样本不足返回 `insufficient`；投影未运行时返回 `no_data` 而不是 0 秒 |

### 4.2 P1 问题

| ID | 问题 | 根因落点 | 修复包 | 必须通过的验收 |
|---|---|---|---|---|
| P1-5 | 发布/部署非原子，崩溃或失败留下半状态 | 先发布运行时再写指针；没有发布意图或恢复记录；`schema_payload` 可被覆盖；`gateway_for` 从最新载荷自愈 | WP2 | 在运行时发布后、指针提交前注入失败或重启，恢复后指针和运行时一致；未提交的 schema 不会继续服务；审计有恢复记录 |
| P1-6 | 对账假阳性，控制器不收敛 | `Reconciler` 比较某类型所有运行时对象与所有源 ID，忽略 `mapping.filter`、`exclude_missing_id` 和其他投影；控制器每轮都重新提议，没有收敛/停止条件；零差异也发事件 | WP3 | 过滤投影的未拥有对象不再产生 `missing_in_source`；authoritative 模式的孤儿对象可被识别；控制器连续 N 轮未收敛后打开待办并停止；零差异不发 `found_differences` |
| P1-7 | Outbox 空转：没有消费者也显示已投递 | `Dispatcher.dispatch` 在 `handlers` 为空时仍 `delivered += 1` 并 `mark_dispatched`；生产没有订阅者；没有重试、退避、死信 | WP3 | 无消费者时事件为 `unrouted` 且不计入已投递；消费者失败重试并退避；超过上限进死信；至少一个内部消费者真实处理事件 |
| P1-8 | 目标只在读取时评估，待办只标记已解决 | `EvaluateObjective` 由 `GET /objective` 触发并在读时追加样本；`window` 只裁剪样本；`ResolveTodo` 不重新评估 | WP3 | `GET` 不再产生样本；调度周期评估；同一窗口只开一个待办；关闭待办时仍越界则拒绝；应用级范围可选 |
| P1-9 | 没有调度器和时钟 | 平台只有手动 use case，没有周期作业、作业状态、失败隔离和优雅停止 | WP3 | 健康接口显示作业、上次运行、下次计划、错误；`run_once` 可测试；作业失败不拖垮其他作业；停止后不再执行 |
| P1-10 | 数据同步完整性、饱和和新鲜度都不足 | watermark 跳过不写运行记录；upsert 与 delete 分两次网关调用；连接器无行数上限；同步无后台运行 ID；`last_check` 与 `last_change` 不分 | WP3 | 删除失败时不会留下已提交的 upsert；跳过运行也记录检查时间；超过行数上限明确失败；同步返回运行 ID 并可查状态；新鲜度使用最近检查 |
| P1-11 | 身份和权限边界不真实 | 无 token 时默认 `admin`；有 token 时固定 actor；前端固定 `user=admin`；成员角色没有读范围说明 | WP4 | 令牌模式下匿名被拒绝；令牌映射到真实 actor；本地模式 actor 不是默认 admin；工作空间读需要成员；viewer 不能写；审计记录 actor 来源 |

### 4.3 P2 问题

| ID | 问题 | 根因落点 | 修复包 | 必须通过的验收 |
|---|---|---|---|---|
| P2-12 | Agent 目标自证，步骤不留痕 | `AgentRunner` 自行 `finish`；`run.ok` 只表示有答案；最大步数强制给答案；只审计目标/步数，不持久化步骤 | WP4 | 目标谓词不成立时 `ok=false`；运行记录包含目标、证据、步骤、工具调用、token/耗时；最大步数返回失败或未知，不伪造答案 |
| P2-13 | 观测显示失真 | 对象详情和 as-of 用 `String(v)`；本地时间字符串被当 UTC；无快照时间/刷新 | WP4 | 对象/数组显示为结构化视图；时间以本地输入转 UTC；详情显示拉取时间；Vitest 覆盖格式化和时区 |
| P2-14 | 模型配置全局且密钥可被前端读取 | 全局 LLM 配置；`api_key` 存 localStorage；无工作空间隔离、版本、审计 | WP4 | 每个工作空间独立配置；密钥只写不读，返回掩码；保存/更新有审计；前端不再持久化密钥 |
| P2-15 | 界面承诺超过实现 | 工作台 SLO 绿色、运行页“实时快照”“已投递”、数据页“失败不会留下半写状态”、助手“每一步都留痕”、契约示例字段不匹配 | WP4 | 所有状态文案由真实状态派生；无数据不绿色；未知不写成功；契约示例能被后端接受；静态文案测试覆盖 |

### 4.4 设计态与只读功能的处置

报告矩阵中有一部分功能的正确判定就是“开环”或“不适用”。v0.3 不为它们强行加自动控制，但必须让界面和文档说清楚“这里没有闭环，靠人或只读”。

| 功能 | 报告判定 | 计划处置 |
|---|---|---|
| 五步上线引导 | 开环，设计态 | 保留人工清单；每步显示“已完成/待完成/下一步”，不画成自动闭环 |
| 本体编辑、检查、发布 | 开环到发布动作，发布由 WP2 接管 | 编辑期只做校验；发布走 Release 与恢复；不自动改本体 |
| 数据源连接、预览 | 开环点检 | 连接测试是显式动作；显示 `checked_at` 和结果，不承诺持续监控 |
| 映射、投影定义 | 设计态 | 增加预览、校验、归属声明；不自动改映射 |
| 对象查询、应用查询 | 读请求闭环 | 只保证查询结果和快照时间，不纳入纠偏 |
| 对象详情、as-of | 只读 | P2-13 结构化显示、时区转换、快照时间 |
| 问业务世界 | 只读 | 回答不声称已执行动作；有执行意图时引导到能力调用 |
| 审计、健康查看 | 只读观测 | 增加调度器、作业、投递、执行状态；不自动修复 |
| 成员、角色 | 边界功能 | P1-11 身份与工作空间读权限；明确不做对象级 ACL |

这些条目不计入“15 个问题的修复”，但必须出现在发布验收中，否则报告矩阵的“开环/不适用”会被误读成“已闭环”。

## 5. 工作包

### WP0 契约、基线与测试骨架（2 人日）

目标：先让“错误行为”变成失败测试，再开始改代码。

改动：

1. 新建分支 `v0.3-control-loop`，冻结 `v0.2.0` 基线。
2. 在 `docs/control-loop-remediation-plan.md` 记录每个问题的验收命令和负责人。
3. 在 `tests/conftest.py` 增加可控时钟、可控执行器、慢能力、失败消费者、发布失败注入器。
4. 新增测试文件骨架，先写断言，允许在实现前失败：
   - `tests/test_truth_contract.py`
   - `tests/test_execution_ledger.py`
   - `tests/test_release_rings.py`
   - `tests/test_release_recovery.py`
   - `tests/test_outbox_delivery.py`
   - `tests/test_scheduler.py`
   - `tests/test_objective_loop.py`
   - `tests/test_reconciler_ownership.py`
   - `tests/test_projection_atomic.py`
   - `tests/test_identity_scope.py`
   - `tests/test_agent_goal.py`
   - `tests/test_llm_config.py`
   - `frontend/src/lib/format.test.ts`
   - `frontend/src/lib/time.test.ts`
5. `interfaces/api.py` 增加 `/api/health` 的 `api_version`、`scheduler`、`execution_ledger` 能力声明；健康检查只报告，不暴露密钥。
6. 更新 `pyproject.toml` / 前端 `package.json` 到 `0.3.0` 只放在最后发布提交，避免过早混入版本噪声。

验收：

```powershell
cd F:\piAgent\ontoPlantform
python -m pytest -q
```

基线必须是 264 passed。新增测试在实现前失败，失败原因必须对应具体问题，不能是语法错误。

### WP1 真相层与执行账本（10 人日）

覆盖 P0-1、P0-2、P0-4。这是关键路径，必须先于调度和自动纠偏。

#### 5.1 统一结果语义（P0-1）

改动：

- `ports/runtime.py`
  - 增加 `ExecutionStatus = Literal["succeeded", "failed", "unknown"]`。
  - `InvocationResult.ok` 改为属性：`self.status == "succeeded"`。
  - 增加 `retryable` 属性：`self.status == "unknown"`。
  - `verification` 保持原结构，新增 `contract_failed: bool`。
- `application/capability_usecases.py`
  - `_execute` 在得到运行时结果后，先做后置条件校验，再写执行记录。
  - 如果运行时 `succeeded` 但后置条件 `ok=False`：把结果改为 `status="failed"`、`ok=False`、`error="postcondition failed: ..."`、`contract_failed=True`。
  - 如果运行时 `unknown`：`ok=False`、`retryable=True`，保留 `unknown` 原因和 `execution_id`。
  - 发出 `capability.contract_failed` 事件；保留 `capability.verified` 的 `ok=False` 语义不变。
- `application/events.py`
  - 事件目录增加 `capability.contract_failed`。
- `interfaces/api.py`
  - `/invoke` 对 `succeeded` 和 `failed` 返回 200，对 `unknown` 返回 202，响应体始终带 `status`、`ok`、`retryable`、`execution_id`、`verification`。
  - 增加 `GET /api/workspaces/{wid}/executions/{execution_id}` 和 `GET /api/workspaces/{wid}/executions/by-key/{key}`，只读。
- `frontend/src/api.ts`
  - `InvokeResult` 增加 `status`、`retryable`、`contract_failed`、`verification`。
  - 删除“有 `ok` 就成功”的类型假设。
- `frontend/src/components/StatusBadge.tsx` 新增统一状态组件。
- `frontend/src/views/Capability.tsx`、`RunDrawer`、`Application.tsx`、`Assistant.tsx`
  - 成功、失败、未知、契约失败分别显示，不再只 toast `r.ok`。
- 契约示例修正：
  - 当前 UI 示例使用 `{"required": ["amount"], ...}`，后端 `validate_contract` 期望 `preconditions/postconditions/effects/idempotent/compensate`。改为后端可接受且语义完整的示例，并在保存时拒绝未知顶层键。

测试：

- `test_postcondition_violation_is_failure`：`status=failed`、`ok=False`、`contract_failed=True`、执行记录一致。
- `test_unknown_is_not_success`：`status=unknown`、`ok=False`、`retryable=True`。
- `test_invoke_http_status`：未知返回 202，失败返回 200 但 `ok=False`。
- `test_contract_example_is_accepted`：前端示例直接作为后端 contract 输入，`create` 不报错。
- 修改 `tests/test_contract_verifier.py::test_postcondition_violation_reported` 的旧断言 `result["ok"] is True`。

验收：

```powershell
python -m pytest -q tests/test_truth_contract.py tests/test_contract_verifier.py
```

#### 5.2 原子认领与未知解析（P0-2）

改动：

- 新增 `application/execution_ledger.py`：
  - `try_claim(workspace_id, key, actor, function, target, args_hash) -> ClaimResult`。
  - 在 `ctx.transaction()` 内用 `repo.put_if_version("execution", wid, f"key_{key}", record, expected_version=0)` 认领。
  - 记录字段：`execution_id`、`idempotency_key`、`status`、`attempts`、`owner_instance`、`started_at`、`lease_expires_at`、`function`、`target`、`args_hash`、`result`、`verification`、`resolution`。
  - `ClaimResult` 区分 `claimed`、`replay`、`in_progress`、`unknown`。
  - 认领文档固定键为 `key_<key>`，只允许从 `running` 迁移到终态一次；认领文档保存 `execution_id`、`attempts` 历史和当前结果，**不再表现成“同一个 ID 覆盖前一次结果”**。
  - 每次真实执行使用独立的 `exec_<random>` 执行 ID；幂等键是唯一索引，执行结果写入认领文档，不写第二份可被覆盖的 `key_<key>` 文档。
- 新增 `application/execution_runner.py`：
  - 有界 `ThreadPoolExecutor`，默认 8 个执行槽，队列满时返回 429/503，不静默丢弃。
  - 作业内部获取目标租约 `ctx.leases.hold(f"{workspace}:{target}:{function}")`，执行能力，做后置条件，最后 `finalize`。
  - HTTP 等待超时只返回 `unknown`，不取消作业，不释放租约；作业完成后更新同一条执行记录。
  - 作业异常统一转 `failed` 或 `unknown`，写 `last_error`，绝不抛出到调度线程。
- `capability_usecases.py`
  - 删除“幂等查询在租约之前”的旧流程，改为先 `try_claim`，再提交执行作业。
  - `replay` 直接返回原结果；`in_progress` 返回 409 `execution_in_progress`，带现有 `execution_id` 和 `Retry-After`；`unknown` 返回 409 `outcome_unknown`，带解析入口。
  - 同一目标的其他键仍由租约串行化；租约只保护世界状态，不替代执行认领。
- 新增 `application/execution_resolver.py` 或并入 ledger：
  - `resolve(execution_id, decision, evidence, actor)`。
  - 只允许 `applied` 或 `not_applied`。
  - `applied` 将状态改为 `succeeded` 或 `resolved_applied`，`not_applied` 改为 `failed` 或 `resolved_not_applied`；两者都保留原 `unknown` 原因和操作者证据。
  - 解析后同一幂等键仍返回解析后的终态，不重新执行。
- 启动恢复：
  - `ExecutionLedger.recover_stale(instance_id)` 把属于旧实例且仍 `running` 的记录改为 `unknown`，原因 `process_restart`。
  - `build_platform` 或 API 启动时调用一次；测试可显式调用。
- API：
  - `GET /executions/{id}`。
  - `POST /executions/{id}/resolve`，body `{decision, evidence, reason}`，需要 editor 或 owner。
  - `POST /invoke` 支持 `timeout_seconds`，默认 30，最大 120；超时只影响等待。
- 前端：
  - `RunDrawer` 每次用户动作生成一次 `crypto.randomUUID()` 幂等键，超时重试复用原键。
  - 未知状态显示“结果未知，已停止自动重试”，提供“查证已生效/未生效”，不提供“再试一次”。
  - 显示执行 ID、开始时间、最后错误、契约校验。

测试：

- `test_concurrent_same_key_executes_once`：100 个并发同键请求，余额只增加一次，执行记录一条。
- `test_timeout_unknown_does_not_retry`：慢能力超时后同键再请求不执行，余额不变；作业完成后记录为终态。
- `test_unknown_requires_resolution`：解析前同键 409；解析后终态；新键才允许新执行。
- `test_execution_record_is_append_only`：两次不同尝试不覆盖 `attempts` 历史。
- `test_restart_marks_running_unknown`：模拟旧实例运行中，恢复后状态 unknown。
- 修改 `tests/test_idempotency.py::test_timeout_is_unknown_and_retried`，原测试断言“unknown ok true 且重试再执行”，必须改成新语义。

验收：

```powershell
python -m pytest -q tests/test_execution_ledger.py tests/test_idempotency.py
```

#### 5.3 窗口化 SLO 与数据质量（P0-4）

改动：

- `application/slo.py`
  - `ServiceLevels.evaluate(workspace_id, actor, window_seconds=3600, min_samples=20)`。
  - 只统计 `finished_at` 落在窗口内的执行记录。
  - `success_rate` 分母为 `succeeded + failed`，`unknown_rate` 分母为全部执行；两者分开，不能把 unknown 算进成功。
  - 返回指标结构：`value`、`samples`、`min_samples`、`window_seconds`、`status`、`target`、`ok`、`reason`。
  - 状态为 `ok | breach | no_data | insufficient | not_applicable`。
  - 总 `ok` 只有在所有适用指标 `status=ok` 且至少有一个有效成功样本时为 true。
  - 投影新鲜度：没有投影时 `not_applicable`；有投影但没有运行/检查时 `no_data`，不是 0 秒。
  - 新鲜度使用 `last_check_at`，不是仅使用成功写入时间。
- `application/metrics.py`
  - 增加 `window_seconds`、`data_quality`、`last_check_at`、`last_change_at`、`unrouted_events`、`dead_events`。
  - 指标返回 `window` 元数据；没有窗口的全时段计数只能通过 `window=all` 显式请求，界面默认不使用。
- `interfaces/api.py`
  - `/slo?window=5m|1h|24h|7d`，默认 1h。
  - `/metrics?window=5m|1h|24h|7d`，默认 1h。
  - `window=all` 保留给诊断，响应标记 `window_mode=all`。
- 前端：
  - `Workbench.tsx` 的 SLO 卡片显示窗口、样本数、数据质量。
  - 无数据时显示“样本不足”，不显示绿色 100%。
  - `Operations.tsx` 的指标区显示窗口与数据质量。
  - `frontend/src/components/DataQuality.tsx` 统一处理 `no_data/insufficient`。

测试：

- `test_empty_workspace_slo_is_no_data`：`success_rate.status=no_data`、`samples=0`、总 `ok=False`。
- `test_slo_window_excludes_old_failure`：窗口外失败不影响当前成功率。
- `test_slo_insufficient_samples`：少于 20 个样本为 insufficient。
- `test_unknown_rate_separate_from_success`：unknown 不计入成功，单独显示。
- `test_projection_freshness_uses_last_check`：跳过运行也刷新检查时间。
- 修改 `tests/test_limits_slo.py`：保留 2 个样本的断言，但明确 `insufficient` 时总 `ok=False`；新增空工作空间测试。

验收：

```powershell
python -m pytest -q tests/test_limits_slo.py tests/test_truth_contract.py
```

### WP2 发布环隔离与崩溃恢复（8 人日）

覆盖 P0-3、P1-5。

#### 5.4 运行时环与金丝雀（P0-3）

改动：

- `infrastructure/runtime/provider.py`
  - `for_workspace(workspace_id, runtime_config, ring="current")`。
  - 缓存键改为 `(workspace_id, ring)`。
  - SQLite 路径改为 `runtime_{workspace_id}_{ring}.db`，变更日志同理。
  - `ring="canary"` 时，如果当前是 SQLite 且允许克隆，用 `sqlite3.Connection.backup` 复制当前库到 canary 库；如果当前是 memory，canary 为空状态并标记 `state_mode="schema_only"`。
- `application/common.py`
  - `gateway_for(workspace_id, ring="current")`。
  - 自愈来源从 `schema_payload` 改为当前 ring 的 `deployment` 指向的 Release；没有 Release 的旧工作空间才回退到 `schema_payload`。
  - 发布 Release 时重新挂接 agent runner，不能把 agent 实现写进 Release。
  - 指纹不一致时先尝试用当前 Release 重发，再审计；不要把不一致静默吞掉。
- `application/releases.py`
  - `deploy_release(..., ring)` 使用 `ctx.gateway_for(workspace_id, ring)`。
  - canary 部署不得读写 `deployment/current`。
  - 新增 `deployment` 字段：`status`、`release_id`、`fingerprint`、`base_release_id`、`state_mode`、`checked_at`。
  - `promote_canary` 先调用 `ReleaseHealth.require_fresh_check`，再发布当前 ring，再移动指针；没有 check 时拒绝，除非 owner 带理由 override 并审计。
- 新增 `application/release_health.py`
  - `ReleaseHealth.check(workspace_id, actor)`：
    - 读取 canary Release 的 `describe`，比对函数、对象类型、指纹。
    - 在 canary 状态上执行只读探针：抽样对象查询、关系查询、契约描述。
    - SQLite 克隆模式下比较当前/canary 的样本对象数量；memory 模式只做 schema 探针。
    - 写入 `canary_check` 文档，包含 `ok`、`checks`、`checked_at`、`base_release_id`、`state_mode`。
  - 提升门禁要求 check 在 10 分钟内且 `ok=True`。
- API：
  - `POST /releases/{rid}/canary` 仍返回部署结果，但响应增加 `isolated=True`、`state_mode`。
  - `POST /releases/canary/check` 触发探针。
  - `POST /releases/canary/promote` 的 `require_healthy` 默认改为 true；没有 check 返回 409。
- 前端 `Operations.tsx`：
  - 文案改为“隔离验证环（不接真实流量）”。
  - 显示 check 状态、检查时间、检查项、state_mode。
  - 未检查/检查过期时禁用提升，提供“运行验证”。
  - 不再把 canary 说成“金丝雀流量”。

测试：

- `test_canary_does_not_touch_current_runtime`：canary 旧 Release 部署后，当前 `describe` 函数不变。
- `test_canary_state_mode`：SQLite 克隆包含基线对象；memory 模式标记 schema_only。
- `test_promote_requires_fresh_check`：无 check 409；失败 check 409；通过后当前指针移动。
- `test_canary_old_release_still_serves_current`：报告中的探针场景复现并断言当前可用。
- `test_rollback_restart_matches_release`：回滚后新进程 `gateway_for` 从 Release 恢复，指纹一致。
- 修改 `tests/test_release_events.py` 中依赖“canary 写入当前”的断言。

验收：

```powershell
python -m pytest -q tests/test_release_rings.py tests/test_release_recovery.py
```

#### 5.5 发布意图与恢复（P1-5）

改动：

- `application/releases.py`
  - 新增 `publish_intent` 文档：`id`、`workspace_id`、`payload_hash`、`release_id`、`state`（`prepared|installing|committed|failed`）、`actor`、`created_at`、`last_error`。
  - `cut_release` 接受内存 payload 或 Release payload，不再依赖可能被覆盖的 `schema_payload`。
  - `deploy_release` 先把 `deployment` 写为 `pending`（包含 release 和 fingerprint），再发布运行时，再标记 `active`；发布失败时立即用旧 Release 重发当前运行时，把 deployment 恢复为旧值并审计。不能只恢复指针而把新 schema 留在当前进程里。
  - `promote_canary` 在发布当前运行时失败时立即用旧 Release 重发当前运行时，保留旧指针，金丝雀状态改为 `failed`。
- `application/recovery.py` 或 `bootstrap` 启动钩子
  - `RecoveryService.recover(workspace)`：
    - 如果有 `pending` deployment：用其 Release 重发；成功后标记 active；失败则回退上一个 active deployment。
    - 如果有 `installing` publish_intent：按 intent 的 Release 重发并提交，或标记 failed 并重发当前 Release。
    - 运行时指纹和当前 Release 不一致：重发当前 Release，审计 `schema.recovered`。
  - API 启动和 `Platform` 测试可显式调用。
- `application/ontology_usecases.py`、`capability_usecases.py`
  - 发布路径统一走 `ReleasePublisher.publish`，不再各自维护“先运行时后事务”的顺序。
  - 事务提交内容包括 ontology/capability、Release、指针、outbox；运行时安装是事务外的可重放步骤。
- `application/events.py`
  - 增加 `schema.recovered`、`deployment.pending`、`deployment.failed` 事件。
- 前端 `Operations.tsx`
  - 显示 deployment `pending/failed` 状态和恢复记录。
  - 发布失败时提示“未切换”，不显示成功。

测试：

- `test_publish_failure_before_commit_keeps_runtime`：注入指针提交失败，恢复后当前 Release 不变。
- `test_publish_crash_after_runtime_before_commit_recovers`：模拟重启，运行时回到当前 Release。
- `test_pending_deployment_recovery`：pending 记录被修复为 active。
- `test_rollback_after_failed_promote`：提升失败保留旧指针。
- `test_no_fingerprint_mismatch_after_restart`。

验收：

```powershell
python -m pytest -q tests/test_release_recovery.py
```

### WP3 时钟、投递与纠偏回路（12 人日）

覆盖 P1-6、P1-7、P1-8、P1-9、P1-10。

#### 5.6 调度器（P1-9）

改动：

- 新增 `application/scheduler.py`
  - `Job` 数据类：`name`、`interval_seconds`、`run(ctx)`、`enabled`、`last_run_at`、`last_error`、`next_run_at`。
  - `Scheduler.start()`、`stop()`、`run_once()`、`status()`。
  - 单线程调度器加有界 `ThreadPoolExecutor`；每个作业失败只记录，不终止其他作业。
  - 作业级超时；超时标记失败并允许下一轮。
  - 停止时等待当前作业结束，最多 5 秒；不强行杀线程。
- `bootstrap.py`
  - `Platform` 增加 `scheduler` 字段和 `start_background()` / `stop_background()`。
  - `PlatformConfig.extra["scheduler"]` 支持 `enabled`、`outbox_interval`、`objective_interval`、`controller_interval`、`canary_interval`。
  - 测试和 `build_platform` 默认不启动，`serve()` 默认启动；显式配置才在测试启动。
- `interfaces/api.py`
  - `serve()` 启动调度器，`finally` 优雅停止。
  - `GET /api/health` 增加 `scheduler` 摘要。
  - `GET /api/workspaces/{wid}/jobs` 只读作业状态。
- 前端：
  - `Operations.tsx` 增加“后台作业”区：作业名、上次运行、下次计划、最近错误。
  - 不再说“实时快照”，改为“最近一次采样 + 时间”。

作业清单：

| 作业 | 默认间隔 | 作用 |
|---|---|---|
| `outbox.dispatch` | 5 秒 | 投递待处理事件 |
| `execution.recover` | 启动一次 | 旧实例运行中转 unknown |
| `objective.evaluate` | 60 秒 | 评估所有启用目标 |
| `projection.sync` | 按投影配置 | 拉取到期投影 |
| `controller.observe` | 300 秒 | 只观察、只提议，不自动执行 |
| `canary.check` | 30 秒 | 对未提升的金丝雀跑健康检查 |
| `slo.snapshot` | 60 秒 | 可选，记录趋势样本 |

测试：

- `test_scheduler_run_once`：手动触发，记录 last_run。
- `test_scheduler_job_failure_isolated`：一个作业抛错，其他继续。
- `test_scheduler_stop_is_graceful`。
- `test_health_reports_jobs`。
- `test_server_starts_and_stops_scheduler`：构建 handler 后启动/停止，线程不泄漏。

验收：

```powershell
python -m pytest -q tests/test_scheduler.py
```

#### 5.7 事件消费者、重试与死信（P1-7）

改动：

- `application/events.py`
  - `EventLog` 增加字段：`delivery_status`（`pending|retrying|delivered|unrouted|dead`）、`attempts`、`next_attempt_at`、`dead_lettered_at`、`consumer`、`last_error`。
  - `pending` 只取 `delivery_status in (pending, retrying)` 且 `next_attempt_at` 到期的事件。
  - `mark_unrouted`、`mark_retry`、`mark_dead`、`mark_delivered`。
  - `dispatch` 返回 `delivered`、`failed`、`retrying`、`unrouted`、`dead`、`errors`。
  - 没有 handler 时不能 `delivered += 1`。
- 新增 `application/consumers.py`
  - `AuditConsumer`：把事件写入审计或 `event_delivery` 文档，证明投递发生。
  - `SignalConsumer`：
    - `capability.contract_failed`、`capability.verified` 失败 -> 生成待办/信号。
    - `reconciliation.found_differences` -> 生成控制器输入，不自动执行。
    - `application.objective_breached` -> 生成/更新待办。
    - 按 `event_id` 幂等。
  - `WebhookConsumer`：可选配置，POST JSON，带签名；失败进入重试。
- `bootstrap.py`
  - 注册 AuditConsumer 和 SignalConsumer；生产可追加 webhook。
  - 没有外部消费者时，事件仍可由内部消费者处理，不再空转；`consumer` 字段记录消费者名。
- 重试策略：
  - 最大 5 次，指数退避 2^n 秒，上限 5 分钟。
  - 达到上限写 `dead`，保留原始 payload。
  - 提供 `POST /events/{id}/replay`，仅 owner。
- API：
  - `GET /events?delivery_status=` 过滤。
  - `GET /events/summary` 返回 pending/unrouted/dead 数量。
- 前端：
  - `Workbench` 的“已投递”改为“已投递 / 待投递 / 未路由 / 死信”。
  - `Operations` 事件表显示消费者、尝试次数、最后错误；没有消费者显示“未配置消费者”。

测试：

- `test_no_consumer_is_unrouted`：空订阅者时 pending 不变，`unrouted` 增加，`delivered=0`。
- `test_consumer_success_marks_delivered`。
- `test_consumer_failure_retries_with_backoff`：时钟推进后到期；attempts 增加。
- `test_consumer_dead_after_max_attempts`。
- `test_consumer_idempotent_by_event_id`。
- `test_signal_consumer_opens_todo`。
- 修改旧的“无订阅者仍投递 7 条”测试或删除该错误断言。

验收：

```powershell
python -m pytest -q tests/test_outbox_delivery.py
```

#### 5.8 目标评估与待办生命周期（P1-8）

改动：

- `domain/application/model.py` 或 `domain/objective.py`
  - 目标字段：`metric`、`target`、`band`、`window_seconds`、`evaluation_interval_seconds`、`scope`（`workspace|application`）、`min_samples`、`status`、`last_evaluated_at`、`last_sample_at`、`todo_id`。
  - `scope=application` 时，只统计该应用关联能力的执行记录；`scope=workspace` 时统计工作空间。界面显示范围。
- `application/objective_usecases.py`
  - `EvaluateObjective` 只由调度器或 `POST /applications/{aid}/objective/evaluate` 调用，`GET` 只读。
  - 评估调用 `ServiceLevels.evaluate(window, min_samples)`；没有数据写 `no_data`，不追加假样本。
  - 同一评估窗口只追加一个趋势样本，避免读一次加一次。
  - 越界时创建或更新一个 `open` 待办，payload 包含证据和建议动作。
  - 恢复时状态 `recovered`，发 `application.objective_recovered`；默认不自动关闭待办。
  - `ResolveTodo(actor, todo_id, evidence, reason, override=False)`：
    - 先重新评估。
    - 仍越界且 `override=False` -> 409。
    - `override=True` 只允许 owner，记录理由。
    - 通过后状态 `resolved`，写审计和事件。
- `interfaces/api.py`
  - `GET /applications/{aid}/objective` 只读。
  - `POST /applications/{aid}/objective/evaluate` 显式评估。
  - `POST /todos/{id}/resolve`。
  - 调度器每 60 秒评估到期目标。
- 前端 `Application.tsx`
  - 目标面板显示范围、窗口、样本数、状态、上次评估。
  - 无数据不显示绿色；越界显示待办和证据。
  - 增加“立即评估”按钮；读取页面不再顺带评估。

测试：

- `test_get_objective_has_no_side_effect`：连续 GET，样本数不变。
- `test_evaluation_no_data_creates_no_todo`。
- `test_evaluation_breach_creates_one_todo`：重复评估不重复创建。
- `test_evaluation_recovery_marks_recovered`。
- `test_resolve_blocked_while_breached`；`test_owner_override_with_reason`。
- `test_application_scope_uses_only_app_capabilities`。

验收：

```powershell
python -m pytest -q tests/test_objective_loop.py
```

#### 5.9 投影归属、对账与控制器收敛（P1-6）

改动：

- `domain/data/model.py`
  - `Projection` 增加 `mode`（`additive|authoritative`，默认 `additive`）、`managed_types`、`sync_interval_seconds`、`last_check_at`、`last_change_at`。
  - `validate_projection` 拒绝同一类型被多个 authoritative 投影声明。
- 新增 `application/projection_ownership.py`
  - 记录 `workspace_id`、`projection_id`、`object_type`、`object_id`、`last_seen_run_id`、`updated_at`。
  - `RunProjection` 成功写入/删除对象后同步更新归属索引；全量同步清理过期归属。
  - 旧工作空间首次运行时回填；无法判断的历史对象在 additive 模式下忽略，在 authoritative 模式下报告并要求一次全量同步。
- `application/verifier.py`
  - `Reconciler` 按投影分别计算：
    - 期望 ID：源行经过 `mapping.filter`，缺失 ID 按 `exclude_missing_id` 处理。
    - 拥有 ID：`projection_ownership` 中该投影的记录。
    - `missing_in_runtime`：期望但未拥有/不存在。
    - `missing_in_source`：拥有但期望集合中没有。
    - authoritative 额外检查 `managed_types` 下未被任何投影拥有的运行时对象，作为 `orphan` 发现。
    - 属性/关系差异按 mapping 字段比较，不只比 ID。
  - 只有存在发现时才发 `reconciliation.found_differences`，payload 带发现类型和数量。
  - 返回 `ok`、`findings`、`checked_at`、`mode`、`scope`。
- `application/controller.py`
  - 提议动作扩展：`run_projection`（missing_in_runtime）、`delete_orphan`（authoritative orphan，默认需确认）、`reconcile_metadata`（归属缺失）。
  - `act(accept=False)` 默认不自动接受；接受后重新观察。
  - 连续 3 轮仍不收敛：打开 `controller.unresolved` 待办，记录每轮动作和结果，停止继续提议同一问题。
  - 同一问题已有一轮未结束时不重复提议。
  - 每次动作写 `controller.cycle` 事件，包含 `cycle`、`proposed`、`accepted`、`verified`、`reason`。
- `interfaces/api.py`
  - `POST /reconcile`、`POST /controller/propose`、`POST /controller/act`，避免 GET 触发副作用；旧 GET 保留只读或返回 405，并在 UI 改调用。
  - `GET /controller/state` 只读。
- 前端 `Operations.tsx`
  - 对账显示模式、范围、发现类型、归属来源。
  - 控制器显示轮次、是否收敛、待办入口；不再显示“已验证”而没有重新观察。

测试：

- `test_additive_filtered_projection_ignores_unowned_object`：报告中的 `EXTRA` 场景在 additive 下不再报 missing。
- `test_authoritative_mode_reports_orphan`：显式声明 authoritative 后报 orphan。
- `test_filter_applied_to_expected_set`。
- `test_missing_id_policy`：`exclude_missing_id=True` 跳过；False 报 invalid row。
- `test_controller_converges_or_opens_todo`：持续漂移 3 轮后待办并停止。
- `test_no_event_when_no_findings`。
- 修改 `tests/test_contract_verifier.py::test_reconciler_reports_drift`：明确该类型是 authoritative，或改为 additive 下不报告；新增两种模式测试。

验收：

```powershell
python -m pytest -q tests/test_reconciler_ownership.py tests/test_controller.py
```

#### 5.10 数据同步完整性与新鲜度（P1-10）

改动：

- `ports/runtime.py`
  - `SemanticDelta` 增加 `deletes: tuple[ObjectRef, ...]`。
  - `RuntimeGateway.apply_delta` 定义：upserts、relations、deletes 在一次调用内原子完成。
- `infrastructure/runtime/kernel_gateway.py`
  - `apply_delta` 在同一个内核锁/事务内处理 deletes；失败整体不提交。
  - `delete_objects` 保留给兼容，但数据同步不再分两次调用。
- `application/data_usecases.py`
  - `RunProjection` 构造一个 delta，包含 upserts + relations + deletes。
  - 每次运行（包括 watermark 跳过）写 `projection_run`，`status=skipped` 时也写 `checked_at`；更新 projection 的 `last_check_at`，仅在数据变化时更新 `last_change_at`。
  - 连接器读取增加 `max_rows`（默认 5000）和 `max_seconds`（默认 30）；超限明确失败，不静默截断。
  - REST 连接器支持分页参数；SQLite 连接器支持 limit/offset；静态连接器直接计数。
  - 后台同步：`POST /projections/{id}/sync` 返回 `run_id` 202；`GET /projection-runs/{id}` 查状态；小型同步保留同步路径。
- `application/slo.py`
  - 投影新鲜度用 `last_check_at`；跳过运行也算新鲜。
- 前端 `Data.tsx`
  - toast 显示 `run_id`、状态、行数、跳过原因、检查时间。
  - 删除“失败不会留下半写状态”的绝对文案，改为“同步失败不会提交部分写入，运行记录会保留失败原因”。
  - 显示 `last_check_at` 与 `last_change_at` 两个时间，不再混用“最近同步”。

测试：

- `test_projection_delete_failure_leaves_no_upsert`：注入 delete 失败，对象和关系保持原样。
- `test_skip_records_check_and_freshness`：第二次跳过运行有记录，freshness 更新，`last_change` 不变。
- `test_connector_row_limit_fails_loudly`。
- `test_async_sync_run_status`。
- `test_projection_run_failure_recorded`。
- 修改 `tests/test_contract_verifier.py::test_projection_watermark_skips_unchanged`，原断言 runs 只有 2 条，改为包含 skip 记录。

验收：

```powershell
python -m pytest -q tests/test_projection_atomic.py tests/test_contract_verifier.py
```

### WP4 身份、模型、Agent 与观测边界（9 人日）

覆盖 P1-11、P2-12、P2-13、P2-14、P2-15。

#### 5.11 身份与读权限（P1-11）

改动：

- 新增 `application/identity.py`
  - `AuthConfig`：`mode=local|token|oidc`。
  - 本地模式：actor 来自 `ONTO_LOCAL_ACTOR`，默认 `local-owner`；不再默认 `admin`。为了兼容现有本地开发，显式 `user` 参数只在 `local` 模式接受，并标记 `actor_source=local_claimed`。本地模式只允许绑定 `127.0.0.1` 的单用户部署，对外服务必须使用 token 模式，不能把 `local_claimed` 当成生产身份。
  - 令牌模式：`ONTO_API_TOKENS` JSON 或文件，token -> actor；兼容单 token 环境变量但不再固定 actor。
  - OIDC 模式只留接口，不在 v0.3 实现完整 IdP。
  - 无凭据访问返回 401；令牌无效返回 401 或 403。
- `interfaces/api.py`
  - `actor_from_request` 使用 `AuthConfig`；删除 `default "admin"`。
  - 所有写接口要求 editor/owner；读接口要求 viewer 且是成员。
  - `ListWorkspaces` 已按成员过滤，补测试，不扩大到跨工作空间对象读取。
  - 审计记录 `actor` 和 `actor_source`。
- `application/common.py`
  - `require` 保持工作空间级；成员角色只决定动作，不承诺对象级可见性。
  - 文档和 UI 明确“工作空间内成员可见全部对象；v0.3 不做对象级 ACL”。
- `frontend/src/api.ts`
  - 删除固定 `USER="admin"` 和 URL `user` 参数。
  - 增加 token 输入或从同源会话读取；本地模式显示“本地操作者”。
- `frontend/src/views/Settings.tsx`
  - 成员角色文案改为“角色决定可执行动作；工作空间内读范围相同”，删除误导性承诺。
- `interfaces/cli.py`（如有）同步 actor 来源，不默认 admin。

测试：

- `test_token_mode_rejects_anonymous`。
- `test_token_maps_to_configured_actor`：两个 token 两个 actor，审计可区分。
- `test_local_mode_default_actor_is_not_admin`。
- `test_viewer_can_read_but_not_write`。
- `test_workspace_list_filters_non_members`。
- `test_actor_source_in_audit`。
- 更新大量测试：`conftest` 中仍可用 `admin` 显式创建；HTTP 测试不再依赖默认 admin，改为发送本地 actor/token。

验收：

```powershell
python -m pytest -q tests/test_identity_scope.py tests/test_api.py
```

#### 5.12 Agent 目标验收与轨迹（P2-12）

改动：

- 新增 `application/goal_verifier.py`
  - `GoalSpec`：`statement`、`predicates`（对象存在、属性比较、关系存在、查询结果约束）、`max_steps`、`max_seconds`、`max_tokens`。
  - `GoalVerifier.verify(workspace_id, spec, answer, trace)` 返回 `ok`、`evidence`、`failed_predicates`。
  - 默认谓词必须是可读、可复算的；不允许只检查“答案非空”。
- `application/agent_runner.py`
  - 运行前记录 `agent_run`；每步记录 `agent_step`：序号、工具、参数摘要、结果摘要、耗时、token；错误也记录。
  - `finish` 只是候选答案，必须经过 `GoalVerifier`；不通过则 `ok=False`，保留答案和失败谓词。
  - 达到最大步数/时返回 `failed` 或 `unknown`，不再强制生成成功答案。
  - 运行记录持久化完整 trace，不只在内存。
- `application/intelligence_usecases.py`
  - `RunAgent` 返回 `verification`、`trace_id`、`budget_used`。
  - 审计包含目标谓词、验证结果、步数、耗时。
- API：
  - `POST /agents/{id}/run` 返回 202 和 `run_id`；`GET /agent-runs/{id}` 返回状态、trace、验证。
  - 旧同步接口保留但内部仍写 run。
- 前端 `Assistant.tsx`
  - 显示目标是否通过、失败谓词、证据、步骤列表。
  - “每一步都留痕”改为“本次运行步骤已保存”，并以 `trace_id` 为准。

测试：

- `test_agent_finish_without_predicate_is_not_success`：假 LLM 直接 finish，谓词不成立，`ok=False`。
- `test_agent_goal_verified_with_evidence`。
- `test_agent_trace_persisted`：步骤数、工具、结果可查。
- `test_agent_max_steps_returns_failure_not_fake_answer`。
- `test_agent_budget_exceeded`。

验收：

```powershell
python -m pytest -q tests/test_agent_goal.py tests/test_agent_recursion.py
```

#### 5.13 观测渲染与时区（P2-13）

改动：

- 新增 `frontend/src/components/ValueView.tsx`
  - 原始类型：字符串、数字、布尔、null 直接显示。
  - 对象/数组：折叠 JSON 视图，保留键顺序，长文本截断可展开。
  - 时间字段：按字段名 `*_at`、`ts` 识别，显示本地时间与原始 ISO。
  - 不再出现 `String(v)` 导致 `[object Object]`。
- 新增 `frontend/src/lib/time.ts`
  - `toUtcIso(localValue)`：把 `<input type="datetime-local">` 的本地时间按浏览器时区转成带 offset 的 ISO。
  - `formatLocal(iso)`：显示本地时间 + 时区。
  - 后端只接受 ISO 8601；不猜本地时间。
- `StateBrowser.tsx`
  - 对象详情、变更详情、as-of 结果统一用 `ValueView`。
  - 详情头部显示 `fetched_at`；增加可选“每 5 秒刷新”开关，默认关闭。
  - as-of 面板显示解释：“查询的是该时刻的快照，不是实时值”。
- `api.ts` 对 as-of 参数做 ISO 校验，失败给明确错误。

测试：

- `frontend/src/lib/format.test.ts`：对象、数组、null、长文本。
- `frontend/src/lib/time.test.ts`：东八区本地时间转 UTC，跨日、夏令时边界（用固定 TZ 配置）。
- `npm run test` 通过；`npm run build` 通过。

验收：

```powershell
cd F:\piAgent\ontoPlantform\frontend
npm run test
npm run build
```

#### 5.14 工作空间级模型配置（P2-14）

改动：

- 依赖：后端增加 `cryptography`（Fernet）或使用已有安全依赖；不新增前端加密。
- 新增 `application/llm_config.py`
  - `LLMConfig` 文档：`workspace_id`、`provider`、`model`、`base_url`、`api_key_ciphertext`、`key_last4`、`version`、`updated_at`、`updated_by`。
  - `ONTO_SECRET_KEY` 作为主密钥；未配置时拒绝保存密钥，返回明确错误，不回退明文。
  - `get` 只返回 `provider/model/base_url/key_last4/version`，永不返回明文。
  - `set` 需要 owner，写审计 `llm_config.updated`，不把密钥写日志。
- `infrastructure/llm/factory.py`
  - `for_workspace(workspace_id)` 解析配置；无配置回退到环境默认，但健康接口显示 `source=environment`。
  - agent capability runner 重挂载时使用工作空间配置，不再用全局 provider。
- `application/common.py`
  - `llm_for(workspace_id)`；`_reattach_agent_runners` 使用。
- API：
  - `GET /workspaces/{wid}/llm-config` 掩码。
  - `PUT /workspaces/{wid}/llm-config` 需要 owner。
  - `POST /workspaces/{wid}/llm-config/test` 只测试连接，不回显密钥。
- 前端 `LlmPicker.tsx`、`store.tsx`
  - 删除 `api_key` localStorage 持久化。
  - 保存时只提交一次，成功后清空输入，显示 `last4/version`。
  - 显示配置来源（工作空间/环境）和更新时间。
- 迁移：
  - 现有 localStorage 密钥不自动上传；提示用户重新输入；文档说明清理方法。

测试：

- `test_llm_key_never_returned`：GET 不含明文。
- `test_llm_config_per_workspace`：两个工作空间互不影响。
- `test_llm_config_requires_owner`。
- `test_llm_config_audit`。
- `test_missing_secret_key_fails_closed`。
- 前端源码测试或 Vitest：localStorage 不出现 `api_key`。

验收：

```powershell
python -m pytest -q tests/test_llm_config.py
```

#### 5.15 界面真实性（P2-15）

改动：

- 新增 `frontend/src/lib/status.ts`
  - 唯一状态映射：`succeeded/failed/unknown/no_data/insufficient/unrouted/retryable/contract_failed`。
  - 颜色：只有 `succeeded` 用绿色；`unknown` 用琥珀色；`failed` 用红色；`no_data/insufficient` 用灰色。
  - 禁止在组件里手写“成功”文案。
- `Workbench.tsx`
  - SLO 卡片改为“窗口 + 样本 + 状态”，无数据灰色。
  - 回路图标注“当前为人工触发/只读/开环”的环节，不把 5 步引导画成自动闭环。
  - 最近事件显示投递状态，不用“已投递”一概而论。
- `Operations.tsx`
  - “实时快照”改为“最近一次快照 + 时间”。
  - 控制器文案说明默认 observe，不自动修复。
  - 事件表显示消费者/尝试/错误。
  - 金丝雀文案改为“隔离验证环”。
- `Data.tsx`
  - 删除“失败不会留下半写状态”绝对承诺，改为具体保证。
  - 同步 toast 显示 run_id、状态、检查时间。
- `Capability.tsx`
  - 契约示例与后端一致；保存前客户端的顶层键校验与后端一致。
  - toast 使用 `status` 而不是 `r.ok`。
- `Assistant.tsx`
  - “每一步都留痕”改为“本次运行 trace 已保存”，并链接 `trace_id`。
- `Settings.tsx`
  - 成员角色文案修正。
- 新增静态文案检查测试或脚本：
  - `frontend/src/copy.audit.test.ts` 扫描关键组件，禁止出现“实时快照”“失败不会留下半写状态”“每一步都留痕”等未实现承诺。
  - 允许列表与真实状态绑定。

测试：

- `frontend/src/lib/status.test.ts`：状态映射和颜色。
- `frontend/src/copy.audit.test.ts`：禁止文案。
- 手工 e2e：空工作空间、未知执行、契约失败、无消费者、金丝雀未验证、目标无数据六个场景截图核对。

验收：

```powershell
cd F:\piAgent\ontoPlantform\frontend
npm run test
npm run build
```

### WP5 集成、迁移与发布（4 人日）

改动：

1. 版本与 API：
   - `pyproject.toml`、`__version__`、前端 `package.json` 升到 `0.3.0`。
   - `api_version` 升到 `3`；健康接口返回 `features`，包含 `execution_status`、`release_rings`、`scheduler`、`outbox_retry`、`identity_modes`。
2. 迁移：
   - `scripts/migrate_v03.py`：
     - 回填 `deployment` 的 `status/fingerprint`。
     - 把已有 `event` 的 `delivered` 映射为 `delivery_status=delivered`，未投递映射 `pending`；历史“空消费者已投递”标记 `legacy_unverified`，不重新投递。
     - 清理/标记旧执行记录：缺少 `attempts` 的补 1；旧 `key_<key>` 文档保留，新执行不再覆盖。
     - 对已有投影写 `mode=additive`，首次成功运行回填归属。
     - 对已有目标写 `window_seconds` 和 `scope=workspace`。
   - 迁移脚本默认 dry-run，打印将变更数量；`--apply` 才写入。
3. 文档：
   - 更新 `docs/progress-v2.md` 或新增 `docs/progress-v0.3.md`，记录每项问题的证据。
   - 更新 `docs/plan-v2.md` 的相关章节或新增 ADR：
     - ADR：`ok` 只表示 succeeded。
     - ADR：unknown 不自动重试。
     - ADR：部署指针是运行时真相。
     - ADR：金丝雀是隔离验证环，不是流量环。
     - ADR：投影归属与 authoritative/additive。
     - ADR：单进程调度与作业幂等。
4. 验收脚本：
   - 启动服务，空工作空间 SLO、未知执行、契约失败、canary 隔离、回滚重启、无消费者、目标调度、权限拒绝、Agent 目标失败、时区显示。
   - 前端构建、Vitest、axe 检查。
5. 发布前检查：
   - 全量 `pytest`、`npm run test`、`npm run build`。
   - 手工 smoke 记录命令和输出。
   - 回滚方案：保留 `v0.2.0` 分支和构建产物；数据库迁移可逆字段只增不改，旧版本可读新文档的未知字段并忽略。

验收：

```powershell
cd F:\piAgent\ontoPlantform
python -m pytest -q
python scripts/migrate_v03.py
python scripts/migrate_v03.py --apply
cd frontend
npm run test
npm run build
```

## 6. 顺序、依赖与并行

### 6.1 依赖关系

```text
WP0
  -> WP1 (P0-1, P0-2, P0-4)
       -> WP3 (P1-6, P1-7, P1-8, P1-9, P1-10)
WP0
  -> WP2 (P0-3, P1-5)
WP0
  -> WP4 (P1-11, P2-12, P2-13, P2-14, P2-15)
WP1 + WP2 + WP3 + WP4
  -> WP5
```

硬依赖：

- P0-2 依赖 P0-1 的状态语义。
- P1-7 的事件消费者依赖 P0-1 的失败事件和 P1-9 的调度器。
- P1-8 的目标评估依赖 P0-4 的窗口指标。
- P1-6 的控制器依赖 P1-9 的调度器才能周期观察，但可以在调度器之前先做手动收敛。
- P0-3 的 canary check 依赖 Release 恢复语义。
- P2-12 的 Agent 验证依赖 P1-11 的 actor 和 P2-14 的模型配置，避免用全局模型和默认 admin 做验收。

### 6.2 并行策略

2 人配置：

- 开发 A：WP1 全量，然后 WP3 的 scheduler/outbox/objective。
- 开发 B：WP2 全量，然后 WP3 的 reconciler/projection/data。
- 前端：WP1 开始后介入状态组件和 API 类型；WP4 集中处理观测/模型/文案。
- 测试：WP0 建骨架，WP1 起每个工作包随代码提交测试，不把测试留到最后。

文件写作用域建议：

| 范围 | 主要文件 | 协调点 |
|---|---|---|
| 执行真相 | `ports/runtime.py`、`capability_usecases.py`、`execution_ledger.py`、`execution_runner.py` | 先定 `ok/status` 契约 |
| 发布恢复 | `releases.py`、`common.py`、`provider.py`、`release_health.py` | 先定 `gateway_for(ring)` 签名 |
| 调度投递 | `scheduler.py`、`events.py`、`consumers.py`、`bootstrap.py` | `bootstrap` 合并窗口 |
| 数据对账 | `data_usecases.py`、`verifier.py`、`controller.py`、`projection_ownership.py` | `SemanticDelta` 先合并 |
| 身份模型 | `identity.py`、`llm_config.py`、`api.py`、`factory.py` | `api.py` 是所有路径汇合点，安排单人收口 |
| 前端 | `api.ts`、`views/*`、`components/*`、`lib/*` | 按视图拆，避免多人改 `api.ts` |

`interfaces/api.py`、`bootstrap.py`、`tests/conftest.py` 是高风险共享文件。每个工作包只加自己的路由/装配，合并前由 Lead 审一遍，避免互相覆盖。

### 6.3 里程碑

| 里程碑 | 内容 | 门禁 |
|---|---|---|
| M0 契约冻结 | WP0 完成，红测试就位 | 新测试失败原因正确 |
| M1 真相可用 | P0-1、P0-2、P0-4 完成 | 无假成功、无同键重复、空 SLO 不绿 |
| M2 发布可用 | P0-3、P1-5 完成 | canary 隔离、回滚重启一致、失败恢复 |
| M3 时钟可用 | P1-9、P1-7 完成 | 作业运行、事件真实投递/未路由/死信 |
| M4 纠偏可用 | P1-6、P1-8、P1-10 完成 | 对账不假阳、目标周期评估、同步原子 |
| M5 边界可用 | P1-11、P2-12 到 P2-15 完成 | 身份、模型、Agent、显示、文案 |
| R0 发布 | WP5 完成，v0.3.0 | 全量测试、构建、迁移、演示 |

## 7. API、数据与界面变更

### 7.1 API 变更

| 端点/字段 | 变更 | 兼容策略 |
|---|---|---|
| `POST /invoke` | 增加 `status/retryable/verification`，unknown 返回 202 | 旧客户端看 `ok` 会得到更保守的 false；版本号提升 |
| `GET /executions/{id}` | 新增 | 只读 |
| `POST /executions/{id}/resolve` | 新增 | owner/editor，必须证据 |
| `GET /slo?window=` | 增加窗口和数据质量 | 默认 1h，旧调用仍可用 |
| `GET /metrics?window=` | 增加窗口 | 默认 1h |
| `POST /releases/canary/check` | 新增 | 运行隔离探针 |
| `POST /releases/canary/promote` | 默认要求 check | 旧 `require_healthy=false` 仍可显式覆盖，但审计 |
| `GET /events` | 增加 `delivery_status` 和摘要 | 旧字段保留 |
| `POST /events/{id}/replay` | 新增 | owner |
| `GET /applications/{aid}/objective` | 只读，不再评估 | 新增 POST evaluate |
| `POST /applications/{aid}/objective/evaluate` | 新增 | 手动触发 |
| `POST /todos/{id}/resolve` | 新增/增强 | 重新评估后决定 |
| `POST /reconcile` | GET 改为 POST | UI 同步；旧 GET 返回只读状态或 405 |
| `POST /controller/propose` | GET 改为 POST | 同上 |
| `POST /controller/act` | 默认 `accept=false` | 显式接受 |
| `POST /projections/{id}/sync` | 返回 202 + run_id | 小数据仍可同步 |
| `GET /projection-runs/{id}` | 新增 | 只读 |
| `GET/PUT /workspaces/{wid}/llm-config` | 新增 | 密钥只写不读 |
| `GET /api/health` | 增加 `api_version`、`scheduler`、能力声明 | 不暴露密钥 |

### 7.2 数据字段与迁移

新增/变更文档字段：

- `execution`：`execution_id`、`status`、`attempts`、`owner_instance`、`lease_expires_at`、`args_hash`、`result`、`verification`、`resolution`、`finished_at`。
- `deployment`：`status`、`fingerprint`、`base_release_id`、`state_mode`、`checked_at`。
- `release`：保持不可变，`previous_release_id` 已有。
- `publish_intent`：新增。
- `canary_check`：新增。
- `event`：`delivery_status`、`next_attempt_at`、`dead_lettered_at`、`consumer`。
- `projection`：`mode`、`managed_types`、`sync_interval_seconds`、`last_check_at`、`last_change_at`。
- `projection_ownership`：新增。
- `objective`：`window_seconds`、`scope`、`min_samples`、`status`、`last_evaluated_at`、`last_sample_at`、`todo_id`。
- `llm_config`：新增。
- `agent_run`、`agent_step`：新增。

存储是文档型，不需要 DDL。迁移只做字段回填和旧记录标记；不删除旧字段。迁移脚本必须可重复执行。

### 7.3 界面变更

| 视图 | 变更 |
|---|---|
| Workbench | SLO 显示窗口/样本/数据质量；无数据灰色；回路图标注人工与只读环节；事件显示投递状态 |
| Operations | 指标窗口；金丝雀为隔离验证环；canary check 门禁；控制器轮次/收敛；事件消费者/重试/死信；后台作业状态 |
| Data | 同步 run_id、状态、行数、检查时间；删除绝对承诺；投影模式与自动同步配置 |
| Capability | 状态徽标；未知不显示成功；契约示例修正；解析入口 |
| Application | 目标范围/窗口/样本/状态；立即评估；待办证据与关闭 |
| StateBrowser | 结构化值；as-of 快照说明；拉取时间；可选刷新 |
| Assistant | 目标通过/失败谓词/证据；trace_id；步骤列表 |
| Settings | 身份模式、token 配置、模型配置来源；成员角色文案修正 |
| 全局 | `StatusBadge`、`DataQuality`、`ValueView`、`status.ts` 统一状态与颜色 |

设计语言保持不变：暖色单色、Geist/Newsreader、无渐变、一个墨色强调、无长破折号。新增状态色只用于状态语义，不做装饰。

## 8. 测试与门禁

### 8.1 基线

```powershell
cd F:\piAgent\ontoPlantform
python -m pytest -q
```

基线：264 passed。任何实现提交不得让基线下降；旧测试如果断言的正是错误行为，必须改写并在提交说明里写清，不允许删除断言逃避。

### 8.2 新增测试矩阵

| 问题 | 新测试文件 | 关键断言 |
|---|---|---|
| P0-1 | `test_truth_contract.py` | 后置条件失败 = failed；unknown != success；HTTP 202 |
| P0-2 | `test_execution_ledger.py` | 并发同键一次；超时不重试；解析；重启 |
| P0-3 | `test_release_rings.py` | canary 不碰 current；check 门禁；回滚重启 |
| P0-4 | `test_truth_contract.py` | 空 SLO no_data；窗口；最小样本；新鲜度 |
| P1-5 | `test_release_recovery.py` | 崩溃恢复；pending；无指纹漂移 |
| P1-6 | `test_reconciler_ownership.py` | 过滤/归属；authoritative/additive；收敛 |
| P1-7 | `test_outbox_delivery.py` | unrouted；重试；死信；消费者幂等 |
| P1-8 | `test_objective_loop.py` | GET 无副作用；周期；单待办；关闭门禁 |
| P1-9 | `test_scheduler.py` | run_once；隔离；停止；健康 |
| P1-10 | `test_projection_atomic.py` | 删除原子；跳过记录；行数上限；异步状态 |
| P1-11 | `test_identity_scope.py` | 匿名拒绝；token 映射；viewer 只读 |
| P2-12 | `test_agent_goal.py` | 目标谓词；trace；最大步数 |
| P2-13 | `frontend/src/lib/*.test.ts` | 值渲染；时区转换 |
| P2-14 | `test_llm_config.py` | 密钥不回显；工作空间隔离；审计 |
| P2-15 | `frontend/src/copy.audit.test.ts` | 禁止未实现承诺 |

### 8.3 旧测试修改清单

| 测试 | 原因 | 修改方式 |
|---|---|---|
| `test_idempotency.py::test_timeout_is_unknown_and_retried` | 断言 unknown 成功且重试执行，正是 P0-2 | 拆成 unknown 不重试、解析后终态两个测试 |
| `test_contract_verifier.py::test_postcondition_violation_reported` | 断言 `ok is True`，正是 P0-1 | 改为 `ok is False`、`status=failed` |
| `test_contract_verifier.py::test_reconciler_reports_drift` | 默认对账对未归属对象报 drift，正是 P1-6 | 显式 authoritative 或改 additive 场景 |
| `test_contract_verifier.py::test_projection_watermark_skips_unchanged` | 期望 skip 不写 run，正是 P1-10 | 期望 skip 记录且检查时间更新 |
| `test_release_events.py` 中 canary 断言 | 旧实现写 current | 断言 current 不变、canary 独立 |
| `test_objective.py` / `test_application.py` 中 GET 触发评估 | 旧读接口有副作用 | 改为显式 evaluate 或调度器 |
| `test_limits_slo.py` | 零样本/样本不足语义变化 | 保留有样本断言，新增 no_data |
| `test_api.py` | 默认 admin 和 GET 副作用 | 改为显式 actor/token；POST 触发动作 |
| `test_agent_recursion.py` | Agent 成功判定变化 | 增加目标谓词，断言 evidence |

### 8.4 端到端验收场景

1. 空工作空间：SLO 显示 `no_data`，不绿；投影新鲜度 `no_data`。
2. 后置条件失败：执行显示 `failed`，契约失败可见，世界未被误报成功。
3. 超时：返回 `unknown`，同键不再执行，解析后终态。
4. 并发同键：只执行一次，余额只变一次。
5. 金丝雀：部署旧 Release 不影响当前函数；无 check 不能提升；check 后提升成功。
6. 回滚重启：重启后当前 Release 的 schema 指纹一致，无 `schema.fingerprint_mismatch`。
7. 无消费者：事件显示 `unrouted`，不显示“已投递”。
8. 消费者失败：重试、退避、死信可见。
9. 目标越界：调度器开一个待办，重复评估不重复开；关闭被拒绝。
10. 数据同步：删除失败不留半写；跳过运行刷新检查时间。
11. 对账：additive 过滤投影不报未拥有对象；authoritative 报孤儿；控制器 3 轮不收敛开待办。
12. 权限：匿名 token 模式 401；viewer 可读不可写。
13. Agent：自证答案不通过；目标通过有证据；trace 可查。
14. 观测：对象详情不出现 `[object Object]`；as-of 时间按本地时区转换。
15. 模型：GET 不含密钥；两个工作空间配置隔离。

### 8.5 发布门禁

- `python -m pytest -q` 全绿。
- `cd frontend; npm run test` 全绿。
- `cd frontend; npm run build` 成功，产物进入 Python 包。
- 手工 smoke 15 个场景全部通过，记录命令和输出。
- `docs/control-loop-report-v2.md` 的每个 P0/P1/P2 在 `docs/progress-v0.3.md` 有“修复证据”链接。
- 没有新增未标注的 `TODO`、`FIXME` 或 `NotImplemented` 在发布路径上。
- 健康接口版本为 `0.3.0`，API 版本 `3`，能力声明与实现一致。

## 9. 发布与回滚

发布步骤：

1. 合并 WP0 到 WP4，跑全量测试和构建。
2. 在 staging 工作空间跑 15 个 e2e 场景。
3. dry-run 迁移，核对将变更的文档数量。
4. 备份数据库和运行时目录。
5. `--apply` 迁移。
6. 启动 `v0.3.0`，检查健康、调度器、SLO 数据质量、事件投递。
7. 观察 24 小时：错误率、unknown 率、unrouted 数、dead 数、scheduler 失败数。

回滚：

- 代码回滚到 `v0.2.0` 标签；数据库字段只增不改，旧代码忽略新字段。
- 如果迁移把旧事件标记为 `legacy_unverified`，回滚后保持只读，不重新投递。
- 如果 v0.3 调度器写入了新格式执行记录，旧版本读取时忽略新字段；旧版本不会覆盖新执行记录，避免破坏证据。
- 如果发布环故障，优先回滚部署指针到上一个 Release，再回滚代码。
- 回滚后必须重新跑 `schema` 指纹检查和一次空 SLO 检查，确认没有假绿。

## 10. 风险与未解决边界

| 风险/边界 | 影响 | 处理 |
|---|---|---|
| Python 线程无法被强制终止 | 超时后作业可能继续运行，占用租约和执行槽 | 认领 + 未知 + 不重试 + 解析；有界执行器拒绝新任务；文档写明残余风险 |
| 单进程调度 | 多实例会重复执行作业 | v0.3 明确单实例；作业幂等；多实例需数据库租约，作为后续版本 |
| 金丝雀不是真实流量环 | 无法验证真实请求分布和真实状态演化 | 明确定义为隔离验证环；SQLite 克隆状态探针；UI 不宣传流量金丝雀；真实流量分环留待后续 |
| 工作空间内无对象级 ACL | 成员能看到工作空间全部对象 | 明确边界；角色只控制动作；对象级 ACL 单独立项 |
| 内存运行时无法克隆 canary 状态 | canary 探针只能验证 schema | 标记 `state_mode=schema_only`；生产使用 SQLite 时克隆 |
| 事件消费者失败可能重复处理 | 下游重复写入 | 消费者按 `event_id` 幂等；webhook 带幂等键；死信人工重放 |
| `ok` 语义变更破坏旧客户端 | 旧客户端把 unknown 当成功 | API 版本提升；迁移说明；旧客户端至少不会误报成功 |
| 迁移误判归属 | 对账范围错误 | 默认 additive；先 dry-run；首次全量同步回填；authoritative 需显式声明 |
| 测试改写被误认为放宽 | 质量下降 | 每个旧断言变更加提交说明和报告链接；基线测试数量只能增不能减 |
| 调度器带来资源竞争 | 指标/投影/目标同时跑 | 作业有界、超时、租约；默认间隔保守；可在配置中关闭 |

明确的非目标：

- 不做微服务拆分、消息队列、分布式事务。
- 不做多实例高可用。
- 不做对象级 ACL。
- 不做真实流量金丝雀。
- 不把设计态编辑器强行自动化。
- 不为了“看起来闭环”增加无消费者的订阅者或空动作。

## 11. 可直接拆分的执行清单

### WP0

- [ ] 建分支、冻结 v0.2.0、记录基线 264 passed。
- [ ] 增加可控时钟、慢能力、失败消费者、发布失败注入 fixture。
- [ ] 建 15 个测试文件骨架，红测试原因对应问题。
- [ ] 健康接口增加能力声明和 API 版本占位。

### WP1

- [ ] `InvocationResult.ok` 改为仅 succeeded。
- [ ] 后置条件失败回写 status/ok，发 `capability.contract_failed`。
- [ ] unknown 返回 202，ok=false，retryable=true。
- [ ] 新增执行账本，`put_if_version(0)` 原子认领。
- [ ] 新增有界执行器，超时只停止等待。
- [ ] 同键 running/in_progress/unknown 分支。
- [ ] 新增解析接口和解析状态。
- [ ] 启动恢复旧 running 为 unknown。
- [ ] SLO 窗口、最小样本、no_data/insufficient。
- [ ] 前端状态组件和未知解析入口。
- [ ] 修改旧幂等/契约测试。

### WP2

- [ ] provider 支持 ring，SQLite canary 克隆。
- [ ] `gateway_for(ring)` 从 Release 恢复。
- [ ] canary 部署隔离，不写 current。
- [ ] canary check 探针与提升门禁。
- [ ] deployment pending/active/failed 状态。
- [ ] publish intent 和启动恢复。
- [ ] 回滚重启指纹一致。
- [ ] 前端金丝雀文案和 check 展示。

### WP3

- [ ] 调度器、作业注册、健康、优雅停止。
- [ ] outbox 投递状态、重试、死信、未路由。
- [ ] 审计/信号消费者，事件幂等。
- [ ] 目标评估去 GET 副作用，周期评估。
- [ ] 待办证据和关闭门禁。
- [ ] 投影归属索引和 additive/authoritative。
- [ ] 对账按过滤/归属/属性/关系比较。
- [ ] 控制器动作、收敛计数、未收敛待办。
- [ ] `SemanticDelta.deletes` 原子应用。
- [ ] 投影检查记录、行数上限、异步 run。
- [ ] 前端作业/事件/目标/对账展示。

### WP4

- [ ] 身份模式 local/token，移除默认 admin 和前端固定 user。
- [ ] 工作空间读权限测试和角色文案。
- [ ] GoalSpec、GoalVerifier、agent_run/agent_step 持久化。
- [ ] 最大步数返回失败，不伪造答案。
- [ ] ValueView、time.ts、as-of 说明和快照时间。
- [ ] 工作空间级加密模型配置，密钥不回显。
- [ ] 删除 localStorage 密钥。
- [ ] 状态/文案唯一映射和静态文案检查。
- [ ] 修改客服/助手/数据/契约示例。

### WP5

- [ ] 版本 0.3.0、API 3、能力声明。
- [ ] 迁移脚本 dry-run/apply。
- [ ] 15 个 e2e 场景证据。
- [ ] 全量测试、Vitest、构建、axe。
- [ ] 更新进度文档和 ADR。
- [ ] 发布与回滚演练。

## 12. 附：报告问题到验收证据索引

| 问题 | 主要证据文件/测试 | 证据形式 |
|---|---|---|
| P0-1 | `tests/test_truth_contract.py`、`docs/progress-v0.3.md` | 断言 + 截图/响应 |
| P0-2 | `tests/test_execution_ledger.py` | 并发/超时/解析记录 |
| P0-3 | `tests/test_release_rings.py` | describe 对比、重启指纹 |
| P0-4 | `tests/test_truth_contract.py`、`test_limits_slo.py` | 空/少样本响应 |
| P1-5 | `tests/test_release_recovery.py` | 失败注入与恢复审计 |
| P1-6 | `tests/test_reconciler_ownership.py`、`test_controller.py` | 发现列表、收敛轮次 |
| P1-7 | `tests/test_outbox_delivery.py` | 投递状态、重试/死信 |
| P1-8 | `tests/test_objective_loop.py` | GET 无副作用、单待办 |
| P1-9 | `tests/test_scheduler.py`、健康接口 | 作业状态 |
| P1-10 | `tests/test_projection_atomic.py` | 删除原子、skip 记录 |
| P1-11 | `tests/test_identity_scope.py` | 401/403、actor 审计 |
| P2-12 | `tests/test_agent_goal.py` | 失败谓词、trace |
| P2-13 | `frontend/src/lib/*.test.ts` | 值/时区 |
| P2-14 | `tests/test_llm_config.py` | 掩码、隔离、审计 |
| P2-15 | `frontend/src/copy.audit.test.ts`、构建 | 文案和截图 |

本计划不把“已经写了计划”当作问题已解决。只有对应的红测试变绿、界面不再过度承诺、进度文档留下证据，问题才关闭。
