# P1 实施状态（P1 Implementation Status）

> 日期：2026-11-06
> 范围：P0-A 架构治理文档（ADR-0001 + 契约索引）+ P1 五个新增契约（Rule / Event / ApplicationSpec / ViewSpec / InteractionSpec）及其 REST 接线
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

一句话结论：**P1 在“声明与执行分离”的边界内完成了五个契约的闭环，P2 的生产化项（外部投递、沙箱、评估闭环等）尚未开始。**

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
* 新增测试分布：Rule 85 + 43、Event 46 + 17、ApplicationSpec 26、REST 4、bootstrap 接线 6。覆盖方向包括：AST 校验与深度上限、MISSING 语义、发布/归档生命周期、payload 契约校验、订阅过滤匹配、emit 只认 published 契约、spec diagnostics、draft-only 编辑、capability 必须已发布、路由消歧。

## 6. P2 遗留风险

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
