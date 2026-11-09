# P1 实施状态（P1 Implementation Status）

> 日期：2026-11-06（P1）；P2 增量见 §6.1–§6.4（2026-11-09）
> 范围：P0-A 架构治理文档（ADR-0001 + 契约索引）+ P1 五个新增契约（Rule / Event / ApplicationSpec / ViewSpec / InteractionSpec）及其 REST 接线；P2.1–P2.6 与 Digital Twin 的增量在本文件 §6 记录
> 关联：[ADR-0001](../decisions/0001-ontology-driven-application-architecture.md)、[契约索引](contract-index.md)、[架构分析](ontology-driven-application-architecture.md)

## 1. 总览

P1 已全部完成，且未突破 ADR-0001 §2.3 的三条边界：

* 5 个新增契约全部落地：`RuleContract`、`EventContract`、`ApplicationSpec`、`ViewSpec`、`InteractionSpec`。
* 每个契约均含：domain 声明（纯函数/纯数据）→ application 用例 → bootstrap 接线 → REST 端点 → 测试。
* 新增 REST 端点 14 个，全部挂载在 `/api/workspaces/{wid}` 下。
* 新增测试 227 个（pytest 收集数）；全量 `python -m pytest -q` → **1033 passed**。
* 未改动：Kernel 五原语、`OntologyCompiler`、Smart UI L0–L5、`Release`/`Deployment`、PDP、outbox 目录、标准适配 SPI、`dependencies = []`。

### 1.1 面级变化速览

| 面 | P1 变化 | 说明 |
|---|---|---|
| 语义面（声明） | +RuleContract、+EventContract | 业务规则与业务事件成为一等声明，带生命周期与闭集校验 |
| 运行面（执行） | 无变化 | 求值/发事件仍复用对象查询与 outbox，未新增执行通道 |
| 应用编排面 | +ApplicationSpec | 只读投影 + diagnostics，不取代既有 `Application` 语义 |
| 体验面（看/做） | +ViewSpec、+InteractionSpec | 视图/交互统一声明，interactions 追加式 |
| 生成面（设计时） | 无变化 | AI 规划、Smart UI 管线未动（Boundary-3 保持） |
| 治理面 | 无变化 | Release/Deployment/PDP 未动 |
| 数据面 | outbox 扩展 | envelope 增 `contract`，订阅匹配复用同一 EventLog |
| 扩展面 | 无变化 | 组件 manifest 与标准适配 SPI 未动 |

一句话结论：**P1 在“声明与执行分离”的边界内完成了五个契约的闭环；P2 已完成低风险闭环、生产边界（P2.2）、子进程沙箱（P2.3）、可回放评测集（P2.4）、规则索引/聚合规则（P2.5）、事件桥/Schema Registry（P2.6）与只读 Digital Twin 快照/传播/反事实仿真（状态见 §6.1–§6.4）。**

## 2. 交付矩阵

| 任务 | 交付物 | 测试文件（用例数） | 状态 |
|---|---|---|---|
| task-17 P0-A | `docs/decisions/0001-*.md`、`docs/analysis/contract-index.md` | 文档链接/表格校验 | 已完成 |
| task-18 Rule | `domain/rule/model.py`、`domain/rule/engine.py`、`application/rule_usecases.py` | `test_rule_engine.py`（85）、`test_rule_usecases.py`（43） | 已完成 |
| task-19 Event | `domain/event/model.py`、`application/event_usecases.py`、`application/events.py` 扩展 | `test_event_contract.py`（46）、`test_event_subscriptions.py`（17） | 已完成 |
| task-20 ApplicationSpec | `domain/application/spec.py`、`domain/application/model.py`（`interactions`）、`application/application_usecases.py` | `test_application_spec.py`（26） | 已完成 |
| task-22 bootstrap 接线 | `bootstrap.py`（`Platform.rules`、`Platform.business_events`、共享 `EventLog`、App spec 用例） | `test_platform_wiring.py`（6） | 已完成 |
| task-21 REST | `interfaces/api.py`（14 个端点） | `test_architecture_api.py`（4） | 已完成 |
| task-23 文档回填 | 契约索引状态回填、ADR §6、本文 | 链接/表格/编码校验 | 已完成 |

## 3. REST 端点（14 个）

> 均挂载在 `/api/workspaces/{wid}`；`user` 由 API 鉴权注入；权限在 use case 内检查（viewer/editor）；成功统一 200，错误沿用既有 `_error` 映射（400/403/404/409/502 等）。

| # | Method | Path | 作用 | 权限 |
|---|---|---|---|---|
| 1 | GET | `/rules?lifecycle=` | 列出规则（draft/published/archived 过滤） | viewer |
| 2 | POST | `/rules` | 创建 draft 规则（name/object_type/condition/severity/priority…） | editor |
| 3 | POST | `/rules/{rid}/publish` | 发布规则（重复发布为 no-op，幂等） | editor |
| 4 | POST | `/rules/evaluate` | 对对象求值（`object_type` 或 `refs`，可 `include_drafts`） | viewer |
| 5 | GET | `/event-contracts?lifecycle=` | 列出事件契约 | viewer |
| 6 | POST | `/event-contracts` | 声明 draft 契约（name/aggregate_kind/payload_schema/version…） | editor |
| 7 | POST | `/event-contracts/{name}/publish` | 发布契约（已发布再发布报错，需显式新版本） | editor |
| 8 | GET | `/event-subscriptions?event_type=` | 列出订阅 | viewer |
| 9 | POST | `/event-subscriptions` | 创建订阅（name/event_type/filter/handler_key…） | editor |
| 10 | DELETE | `/event-subscriptions/{sid}` | 删除订阅 | editor |
| 11 | POST | `/events/emit` | 经契约校验后写 outbox，返回事件 + `matched_subscriptions` | editor |
| 12 | GET | `/applications/{aid}/spec` | 返回 `{spec, diagnostics, valid}` | viewer |
| 13 | POST | `/applications/{aid}/interactions` | 在 draft 应用上追加 InteractionSpec（能力须已发布） | editor |
| 14 | DELETE | `/applications/{aid}/interactions/{iid}` | 从 draft 应用移除 interaction | editor |

路由消歧（`test_architecture_api.py` 有专门负例）：`/rules/evaluate` 是字面路由，优先于 `/rules/{rid}/publish`；`event-contracts/{name}/publish` 支持带点号事件名（如 `traffic.vehicle_ping`）；`events/emit` 与 `event-subscriptions` 分属“事件发布/订阅管理”两类资源。

> 本表只列 P1 的 14 个端点；P2.1–P2.6 增量（含 webhook 账本、持久 handler、沙箱、评测集、Schema Registry、事件桥、Digital Twin）见 [`contract-index.md`](contract-index.md) §4 与本文 §6.2–§6.3。

## 4. 关键设计与边界遵守

### 4.1 Boundary-1：声明 vs 执行

* `domain/rule`、`domain/event`、`domain/application/spec.py` 只依赖标准库与 `domain` 层，无仓储/网关/内核引用；规则求值是纯函数，事件只写 outbox，不存在第二条执行通道。
* 求值语义确定性：按 `(priority, name, id)` 排序；只有 `severity="error"` 的 violation 使 `passed=False`；缺失字段 = MISSING（不抛异常）。14 个操作符 + `all/any/not` 逻辑闭集，`MAX_CONDITION_DEPTH=32`，AST 不可表达任意代码。

### 4.2 Boundary-2：应用组合 vs 表达面

* `ApplicationSpec` 是 `Application` 聚合的**只读投影**；`GetApplicationSpec` 返回 `{spec, diagnostics, valid}`，诊断是数据不是异常（发布仍是硬门禁）。
* `InteractionSpec` 只引用 `capability_id`，不携带执行逻辑；追删只允许 draft 应用、重复 id 拒绝、能力必须已发布；旧应用行 `interactions` 默认 `[]`，round-trip 稳定。
* `ViewSpec` 统一视图声明（`table/card/chart/map/graph/dashboard/timeline/spatial`），`from_view()` 无损提升 P0 `View`。

### 4.3 Boundary-3：生成 vs 运行/发布

* 生成面（Smart UI / AI）未在 P1 改动；任何生成产物仍须经裁决 → 确定性编译（`program_hash`）→ `Release`/`Deployment` 发布，不能直接调用 `RuntimeGateway` 或移动 deployment 指针。
* `interfaces/api.py` 不出现规则求值、事件投递、编译逻辑；只做参数解析 + use case 调用。

### 4.4 其它约束

* **outbox 复用**：`Platform.events` 与 `Platform.business_events` 共享同一个 `EventLog`（bootstrap 单次构建后注入两者），保证业务事件与平台生命周期事件进同一条 outbox 流、被同一个 Dispatcher 消费，不产生并行流。
* **向后兼容**：`EVENT_CATALOG` 平台事件目录未改；业务事件 envelope 仅新增 `contract` 字段；`Application` 旧行可读；API 旧端点未变。
* **无新依赖**：`pyproject.toml` 的 `dependencies = []` 保持为空。

### 4.5 边界判据复核（2026-11-06 实测）

| 判据 | 检查 | 结果 |
|---|---|---|
| domain 不依赖外层 | `domain/**` 中 `from/import ..application/interfaces/infrastructure` | 0 匹配 |
| Kernel 唯一适配点 | `onto_platform` 内 import 内核包 `ontology_runtime` | 仅 `infrastructure/runtime/kernel_gateway.py`（11 处） |
| smartui 不碰 infrastructure | `application/smartui/**` 中 import `infrastructure` | 0 import（仅 1 条注释） |
| API 不做编排逻辑 | `interfaces/api.py` 中 `publish_schema` | 0 匹配 |
| 零依赖 | `pyproject.toml` dependencies | `[]` 不变 |

## 5. 测试证据

* 定向运行（7 个 P1 测试文件）：
  `python -m pytest tests/test_rule_engine.py tests/test_rule_usecases.py tests/test_event_contract.py tests/test_event_subscriptions.py tests/test_application_spec.py tests/test_architecture_api.py tests/test_platform_wiring.py -q` → **227 passed**（3.00s）。
* 全量运行：`python -m pytest -q` → **1033 passed**（68.71s，0 failed），2026-11-06 由 Lead 最终复核；此前 writer-arch 复核同为 1033 passed。
* P2 首轮后全量复核（2026-11-09）：`python -m pytest -q` → **1082 passed**（68.89s，0 failed）；新增 P2 测试文件见 §6.1。
* P2.2 后全量复核（2026-11-09）：`python -m pytest -q` → **1116 passed**（73.52s，0 failed）；新增 P2.2 测试文件见 §6.2，定向 `tests/test_p2_production_api.py` → 3 passed。
* P2.3–P2.6 + Digital Twin 后全量复核（2026-11-09，Lead 执行）：`python -m pytest -q` → **1299 passed**（76.57s，0 failed）；新增阶段测试 178 个（沙箱 15、评测集 11、规则索引/聚合 69、Schema Registry/事件桥 38、Digital Twin 45），P2 REST 接线 5 个（`tests/test_p2_advanced_api.py`）。
* 新增测试分布：Rule 85 + 43、Event 46 + 17、ApplicationSpec 26、REST 4、bootstrap 接线 6。覆盖方向包括：AST 校验与深度上限、MISSING 语义、发布/归档生命周期、payload 契约校验、订阅过滤匹配、emit 只认 published 契约、spec diagnostics、draft-only 编辑、capability 必须已发布、路由消歧。

## 6. P2 遗留风险

下表是 P1 收尾时的判断；P2 首轮的实际处理结果见 §6.1。

| 缺口 | 影响 | 触发条件 | 建议动作 |
|---|---|---|---|
| 租户/工作空间隔离的进程级强制 | 当前是 `workspace_id` + `TenantContext` 的逻辑边界，非进程隔离 | 多租户生产部署前 | API/仓储层强制 tenant 过滤 + 越权审计 |
| 外部事件投递（webhook/事件桥/Schema Registry） | 业务事件只能进程内分发，跨系统需自建桥 | 需要与外部系统集成时 | 先冻结投递契约（签名/重试/配额/死信），复用 outbox 重试 |
| 插件沙箱 / 进程隔离 / SBOM | 插件在进程内执行，权限靠 manifest 白名单 | 引入第三方远程插件 | 独立进程 host + 资源限额 + 签名校验 |
| AI 生成评估闭环 | 有 `program_hash` 与回放，但缺成本/延迟/质量指标与基线 | 生成面进入生产调优 | 定义 eval 契约与指标，接入可回放评测集 |
| Digital Twin 仿真 | 当前 Spatial 仅空间场景渲染，无实时状态/仿真/反事实 | 有遥测接入与仿真模型 | 保持 Spatial/3D Scene 命名，单独立项 |
| 跨 worker durable handler | `Dispatcher` 订阅注册在进程内，重启/多实例会漏投 | 水平扩展 dispatcher | handler 注册持久化 + 租约/认领 + 消费幂等 |
| 规则求值规模 | `EvaluateRules` 拉取对象集逐条求值，大数据量未分页/索引 | 对象数上万 | 分页求值 + 对条件字段建索引 |
| 规则语言/跨对象推理 | 仅单对象声明式 AST（14 操作符），无跨对象/聚合约束 | 业务出现跨对象规则 | 评估 `BehaviorContract` 化或受控规则 DSL |

### 6.1 P2 首轮处理结果（2026-11-09）

> 原则：只做低风险、可测试、可回滚的增量；不把“已有基础设施”包装成“完整生产方案”。

| 缺口 | 本轮结果 | 仍未做 |
|---|---|---|
| `PublishApplication` 未校验 interactions / clone 不复制 `ui_artifact` | 发布时校验全部 interaction（`InteractionSpec`、重复 id、能力存在且已发布）；`CloneApplication` 深拷贝 `ui_artifact` 与 interactions | 历史应用的全量回填/迁移 |
| 租户/工作空间隔离仅为逻辑边界 | `GovernedRepository` 已安装为 `ctx.repo`：激活请求上下文时跨 workspace 读写拒绝、写入强制覆盖 tenant/workspace；新增真实边界回归 | 进程级租户隔离、独立数据库/进程 |
| 外部事件投递（webhook/事件桥） | 已存在签名 webhook；webhook 允许列表现在接受**已发布业务事件契约**，新增业务事件→签名投递→幂等标记回归 | 事件桥、Schema Registry、配额/死信治理 |
| 插件沙箱/SBOM/进程隔离 | `PluginHost.sbom()` + `GET /smart-ui/sbom`：确定性 manifest 摘要，显式 `processIsolation=false`、`trustBoundary=in-process` | 沙箱、资源限额、进程隔离、签名校验强化 |
| AI 生成评估闭环 | `generation_eval` + `GET /smart-ui/eval`：成功率、P50/P95 延迟、token/成本、浏览量、`program_hash` 数、基线 delta | 可回放评测集、质量评分模型、自动回归门禁 |
| Digital Twin 仿真 | 仅规则级 what-if：`POST /rules/simulate` 对单对象应用顶层属性假设值，返回 resolved/introduced；无副作用 | 实时状态、关系传播、完整仿真/反事实 |
| `handler_key` 进程内注册，多 worker 事件 pending | `EventLog.claim` + 租约 CAS；`Dispatcher.dispatch(worker_id=)` 先认领；scheduler/HTTP drain 使用稳定 `Platform.worker_id`；租约过期可重认领 | handler 注册持久化、跨进程 handler 路由 |
| 规则求值未分页、仅扁平字段 | `EvaluateRules` 稳定分页（`limit`/`offset`，`object_count` 保持总数）、嵌套 `properties` 点路径（缺失/标量中段 = MISSING） | 条件字段索引、跨对象/聚合规则 |

新增测试（全部通过）：`test_p2_application_publish.py`、`test_p2_rule_scale.py`、`test_p2_tenant_webhook.py`、`test_p2_event_claim.py`、`test_p2_ai_eval.py`、`test_p2_plugin_sbom.py`、`test_p2_rule_simulate.py`、`test_p2_smartui_endpoints.py`。全量 `python -m pytest -q` → **1082 passed**（2026-11-09，0 failed）。

边界口径：本轮不承诺进程沙箱、完整 Digital Twin、跨进程 handler 注册或生产级评测集；`SBOM` 是清单不是隔离证明，`webhook` 是投递通道不是事件桥。

### 6.2 P2.2 生产边界增量（2026-11-09）

> 原则不变：只把“可验证的硬边界”写进代码；隔离强度按 L0→L3 如实标注，不把账本/租约包装成分布式事务或进程沙箱。

| 项 | 本轮结果 | 强度/边界 |
|---|---|---|
| 租户仓储硬边界（P2.2-A） | [`scoped_repo.py`](../../src/onto_platform/infrastructure/persistence/scoped_repo.py)：`GovernedRepository` 增加可注入 `tenant_resolver`；业务文档 `_pin` 校验 workspace 归属租户；workspace 行（None key）校验行 `tenant_id`（缺失回退 id）与 `entity_id`；`get/list/query/get_versioned/put/put_if_version/delete` 对伪造“租户 B scope + 租户 A workspace”统一 `AccessDeniedError`；写入 payload 的 `tenant_id/workspace_id` 强制覆盖为 scope；workspace 重挂租户后旧 scope 立即失效；无授权上下文（系统/迁移）保持原行为。负向回归见 `tests/test_p2_tenant_hardening.py`（9） | **L1 仓储级**；不是进程/容器隔离，不是 SQL RLS；`ctx.repo.raw` 仍是 fixtures/migrations 显式逃生口 |
| Webhook 投递账本/配额/死信（P2.2-B） | 新增 `webhook_delivery` kind（`id=whd_sha256(event_id|webhook_id)`）：per-(event,webhook) 唯一行、CAS 租约认领、固定窗口 `rate_limit_per_minute`（默认 60）、`max_attempts`（默认 5）后 dead、指数退避、`list_deliveries`/`dead_letters`/`replay_delivery`；事件 `webhook_delivered_at` 仅在所有匹配 delivery 终态时写；URL 拒绝非 http(s)/userinfo/字面私网（`ONTO_WEBHOOK_ALLOW_PRIVATE=1` 仅测试放行）。回归见 `tests/test_p2_webhook_delivery.py`（7） | 投递 at-least-once；DNS 不解析，域名 rebinding 属部署层；REST 已接线，删 hook 不级联删账本 |
| 持久化 handler 注册与跨进程投递（P2.2-C） | 新增 `event_handler` 注册表（key/workspace/event_types/kind/endpoint/secret/active/version/heartbeat）与 `event_delivery` 账本（`sha256(event_id|handler_key)`）；`Dispatcher` 解析 registry ∪ subscription 的 key 并集，逐 key CAS 认领投递，全部终态才 `mark_dispatched`；无本地回调保持 pending 并释放事件 claim；http handler 走 HMAC-SHA256 签名 POST（可注入 transport）。回归见 `tests/test_p2_event_handlers.py`（14） | at-least-once，handler 必须按 `event_id` 幂等；DB 队列不是 Kafka/NATS；跨 worker 只保证“同一投递不被同时执行”，不承诺 exactly-once |
| 应用发布/克隆补口（P2.2-D） | `PublishApplication` 校验全部 interaction（`InteractionSpec`、重复 id、能力存在且已发布，问题一次收集）；`CloneApplication` 深拷贝 `ui_artifact` 与 interactions。回归见 `tests/test_p2_application_publish.py`（5） | 历史应用不做全量回填/迁移 |

P2.2 新增 REST（均 `security.manage`，secret 统一脱敏为 `***`）：

* `GET /security/webhooks/deliveries?webhook=&status=&limit=`
* `GET /security/webhooks/dead-letters?limit=`
* `POST /security/webhooks/deliveries/{id}/replay`
* `GET /event-handlers`、`GET /event-handlers/{key}`
* `POST /event-handlers`（`handler_key/event_types/kind/endpoint/secret/active/lease_seconds`）
* `POST /event-handlers/{key}/heartbeat|pause|activate`

P2.2 全量复核（2026-11-09，Lead 执行）：`python -m pytest -q` → **1116 passed**（73.52s，0 failed）；新增/更新测试文件 `test_p2_tenant_hardening.py`、`test_p2_webhook_delivery.py`、`test_p2_event_handlers.py`、`test_p2_application_publish.py`、`test_p2_production_api.py`。

仍未做（部署层边界）：网络隔离/chroot/cgroup 级沙箱（当前 `network=not-enforced`，Windows 无 rlimit）、进程级租户隔离/独立数据库、外部 broker（Kafka/NATS）与独立 Schema Registry 服务、持久化规则索引与跨对象事件传播、流式 Digital Twin 遥测（当前是调用时刻快照）、bridge 的 `field_map` 尚未接入 webhook transport 出站体（见 §6.3–§6.4）。

### 6.3 P2.3–P2.6 生产能力增量（2026-11-09）

> 原则不变：隔离强度、实时性、兼容性都按实际实现标注；不把子进程沙箱说成容器安全边界，不把内存索引说成数据库索引，不把配置桥说成消息中间件。

| 项 | 本轮结果 | 强度/边界 |
|---|---|---|
| 插件子进程沙箱（P2.3） | [`application/smartui/sandbox.py`](../../src/onto_platform/application/smartui/sandbox.py)：`run_python_plugin` 用 `sys.executable -I` 独立进程 + 临时目录 + 环境白名单 + 墙钟超时/进程树 kill + POSIX `setrlimit` + 有界 I/O + 保存 fd 写 `SANDBOX_RESULT:` 防 print 伪造；`SandboxUseCases.run_package` 复用 `external.read_package` 验签、`editor` 权限与 `smartui.plugin_sandbox` 审计，结果不注册进 PluginHost。回归 `tests/test_p2_plugin_sandbox.py`（15） | **L2 进程级执行边界**；`isolation.network="not-enforced"`、无 chroot、Windows `resource_limits.enforced=false`（仅超时+进程树），不是恶意原生代码的安全边界；host.py 仍声明内置插件进程内执行 |
| 可回放 AI 评测集（P2.4） | [`application/smartui/eval_set.py`](../../src/onto_platform/application/smartui/eval_set.py)：`generation_eval_set` kind，case 支持 `generation_id`/`question`、roles/tags、`expected_program_hash`、延迟/成本/浏览量阈值；`replay` 复用 `studio.replay_generation` 逐 case 检查并算分（success 0.35 / replay 0.25 / latency 0.15 / cost 0.15 / views 0.10，A–F 等级、baseline delta），错误隔离不中断；同名 upsert。回归 `tests/test_p2_eval_set.py`（11） | 只读回放，不调用新 LLM、不落 replay 历史；baseline 是另一评测集而非历史 run 快照；viewer 回放仍受蓝图编译权限约束 |
| 规则字段索引 + 跨对象/聚合规则（P2.5） | [`application/rule_index.py`](../../src/onto_platform/application/rule_index.py)：单次求值内存 posting list，`eq/in/contains/starts_with/ends_with` 正向叶子可裁剪，not/any/is_null/不可哈希/aggregate 回退全扫描；`EvaluateRules(index=True)` 返回 `index_stats`，旧返回键不变。RuleSpec 新增 `kind="aggregate"` + `aggregate{relation,object_type,where,metric,field,op,value}`，支持 count/sum/avg/min/max/any/all 与 `aggregate.<relation>` violation；非法定义被拒。回归 `tests/test_p2_rule_index.py`（18）+ `tests/test_p2_rule_aggregate.py`（51） | per-evaluation 内存索引，无持久化/增量更新，SQLite/kernel 无二级索引；`SimulateRuleChanges` 尚未支持 aggregate；aggregate 运行期 child/relation 缺失按空集合处理 |
| 事件桥 + Schema Registry（P2.6） | [`application/event_schema_registry.py`](../../src/onto_platform/application/event_schema_registry.py)：`event_schema` 版本不可变，backward/forward/full/none 兼容性检查，`compatibility()` 纯干跑；[`application/event_bridge.py`](../../src/onto_platform/application/event_bridge.py)：1 bridge ⇔ 1 内部 webhook（`source_bridge_id`），复用 `webhook_delivery` 账本/租约/退避/配额/死信/replay，`transform` 支持 envelope 与 `field_map` 点路径。回归 `tests/test_p2_event_schema_registry.py`（18）+ `tests/test_p2_event_bridge.py`（20） | 共享仓储中的 kind，不是独立 Registry 服务或 Kafka/NATS；兼容性只对比紧邻前一版本；`field_map` 尚未接入 webhook transport 实际出站体；`http` 是 webhook 别名 |
| Digital Twin 只读仿真 | [`application/digital_twin.py`](../../src/onto_platform/application/digital_twin.py)：`snapshot` 按已发布本体关系确定性 BFS（depth 0–4/out/in/both/max_nodes/cycle-safe，`stateHash`）；`twin_policy` 支持 copy/scale/boolean/delta 同名 upsert；`simulate` 在副本上应用 changes + 关系传播，对 before/after 跑 published+enabled 规则，返回 changed/resolved/introduced/stateHash/noSideEffects；`scenarios` 1–20 个方案按 (introduced,-resolved,changedCount,name) 排序。回归 `tests/test_p2_digital_twin.py`（45） | 快照是**调用时刻**的 kernel 状态，不是流式实时/订阅/遥测；仿真不写 kernel、不发事件、不持久化；无物理模型、无进程隔离；与既有 twin3d 空间渲染命名分离 |

P2.3–P2.6 新增 REST（`tests/test_p2_advanced_api.py` 5 个接线回归）：

* `POST /plugins/sandbox/run`（`package_dir/signature/entrypoint/payload/timeout_seconds/memory_limit_mb`）
* `GET/POST /eval-sets`、`GET /eval-sets/{id}`、`POST /eval-sets/{id}/replay`、`DELETE /eval-sets/{id}`
* `GET/POST /event-schemas`、`GET /event-schemas/{event_type}?version=`、`POST /event-schemas/compatibility`
* `GET/POST /event-bridges`、`GET /event-bridges/{id}`、`POST /event-bridges/{id}`、`POST /event-bridges/{id}/pause|activate|dispatch`、`DELETE /event-bridges/{id}`
* `GET /twin/snapshot?ref=&depth=&direction=&max_nodes=`、`GET/POST /twin/policies`、`DELETE /twin/policies/{id}`、`POST /twin/simulate`、`POST /twin/scenarios`
* `POST /rules` 支持 `kind/aggregate`；`POST /rules/evaluate` 支持 `index`。

新增/更新测试文件：`test_p2_plugin_sandbox.py`、`test_p2_eval_set.py`、`test_p2_rule_index.py`、`test_p2_rule_aggregate.py`、`test_p2_event_schema_registry.py`、`test_p2_event_bridge.py`、`test_p2_digital_twin.py`、`test_p2_advanced_api.py`。全量 `python -m pytest -q` → **1299 passed**（2026-11-09，0 failed）。

### 6.4 剩余边界与未做项（2026-11-09）

| 项 | 当前真实状态 | 生产化前置 |
|---|---|---|
| 沙箱网络/文件系统隔离 | 独立进程 + 临时目录 + 环境白名单 + 超时/进程树；无 network namespace、seccomp、chroot、容器，插件仍可出网并读取同用户可读文件 | 外层容器/VM 或 OS 级 sandbox，按租户隔离网络与挂载 |
| Windows 资源限额 | `resource_limits.enforced=false`，只有墙钟超时和进程树 kill，无 Job Object 内存/CPU 硬限 | Windows Job Object/容器；恶意插件在超时前仍可吃满资源 |
| 进程级租户隔离 | 仍是 **L1 仓储级** `GovernedRepository`，不是每租户独立进程/数据库；`ctx.repo.raw` 是迁移逃生口 | 每租户独立 schema/DB + 进程或连接池级隔离 |
| 规则索引 | per-evaluation 内存索引，翻页每页重建；无持久二级索引、无增量失效；aggregate 规则不裁剪 | 数据库表达式索引/物化视图 + 变更失效；跨对象事件传播 |
| 事件桥/Schema Registry | 共享仓储、DB 队列；无 broker、无独立 Registry 服务；兼容性只对比紧邻版本；`field_map` 尚未接入 transport 出站体 | 引入 Kafka/NATS 或独立 Registry 服务；在 webhook transport 前加 bridge body hook |
| 评测集 | 只读回放；无 CI 门禁/阈值阻断、无 replay 历史、无前端 | 接入 CI 门禁与历史基线快照 |
| Digital Twin | 调用时快照 + 副本仿真；非流式遥测、无状态订阅、不持久化、无物理模型 | 遥测流接入、状态存储/订阅、仿真模型与持久化方案 |

## 7. 维护约定

* **字段以代码为准**：契约索引与本文的字段摘要若与代码不一致，以代码为准并同一次变更内修正文档。
* **契约变更必须同步**：字段/语义/默认值变更时，更新 `contract-index.md` §3（字段摘要）与本文 §2 交付矩阵；破坏性变更按索引 §6.2 追加 ADR。
* **端点变更必须同步**：新增/调整 REST 时，同步 `contract-index.md` §4 与本文 §3。
* **测试数字可追溯**：本文 §5 的数字须注明命令与日期，不得引用未执行的运行结果。
* **不主动 commit**：本轮文档与代码改动由 Lead 统一复核后提交。

## 8. 参考

* [ADR-0001：本体驱动应用架构](../decisions/0001-ontology-driven-application-architecture.md)
* [平台契约索引](contract-index.md)
* [架构分析](ontology-driven-application-architecture.md)
* [v2 技术计划](../plan-v2.md)
