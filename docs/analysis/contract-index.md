# 平台契约索引（Contract Index）

> 用途：回答“平台有哪些契约、归哪个面、落在哪、现在什么状态”。
> 架构决策见 [`docs/decisions/0001-ontology-driven-application-architecture.md`](../decisions/0001-ontology-driven-application-architecture.md)（五层叙事 + 五面 + 三边界）；问题论证见 [`docs/analysis/ontology-driven-application-architecture.md`](ontology-driven-application-architecture.md)；P1 交付与测试证据见 [`p1-implementation-status.md`](p1-implementation-status.md)。
>
> 口径：本索引服务 P0-A（边界冻结）与 P1（契约落地）两个窗口。**字段摘要已按最终代码核对（2026-11-06）**：P1 五个新增契约全部落地（domain + use case + bootstrap 接线 + REST + 测试），全量测试 `python -m pytest -q` → **1033 passed**。字段仍以代码为最终事实；新增或修改契约必须同步本文件。
>
> 状态图例：**● 已存在**（P0 及以前，可作为依赖，改动需评审） / **◆ P1 已落地**（P1 窗口新增，含测试与 REST） / **○ 后续**（有计划，未在本轮） / **◐ 部分存在**（有落点但表达力不足）

---

## 1. 索引总表

### 1.1 按面速查

| 面 | canonical 契约 | 单一 owner |
|---|---|---|
| 语义面（声明） | `OntologySchema`、`CapabilityContract`、`RuleContract`◆、`EventContract`◆ | 语义/本体 owner |
| 运行面（执行） | `RuntimeGateway`、`SchemaPayload`、`SemanticDelta`、`InvocationResult`、执行账本 | Runtime owner |
| 应用编排面 | `ApplicationSpec`◆（引用 views/queries/interactions/agents/capabilities） | Application owner |
| 体验面（看/做） | `ViewSpec`◆、`InteractionSpec`◆、`Blueprint`/`CompiledProgram`、`ComponentManifest` | Smart UI owner |
| 生成面（设计时） | `Blueprint` / `CompiledProgram` / `BuildManifest`、Studio 草稿 | Smart UI / AI owner |
| 治理面 | `Release` / `Deployment`、`PolicyDecision`、审计、SLO/controller | 治理 owner |
| 数据面 | 事件 outbox envelope、`SemanticDelta`、投影/对账 | 数据 owner |
| 扩展面 | `ComponentManifest`（插件 manifest）、`AdapterManifest`/`AdapterArtifact`（标准适配 SPI） | 扩展 owner |

### 1.2 主索引

| 契约 / 协议名 | 层 / 面 | 作用 | 代码落点（现有 / 新增） | 状态 |
|---|---|---|---|---|
| `OntologySchema` | 语义面 → 运行面 | 结构语义的确定性编译产物（objects/relations/functions），Release 指纹与 Kernel 冻结 schema 的载体 | 现有：[`domain/ontology/compiler.py`](../../src/onto_platform/domain/ontology/compiler.py) `OntologyCompiler.compile`；[`ports/runtime.py`](../../src/onto_platform/ports/runtime.py) `SchemaPayload` | ● |
| `CapabilityContract` | 语义面（行为）/ 运行面 | 能力的输入、实现方式（python/workflow/agent）与可执行契约（前置/后置/副作用/幂等/补偿） | 现有：[`domain/capability/model.py`](../../src/onto_platform/domain/capability/model.py) `Capability`、`CapabilityInput`、`Implementation`、`validate_contract` | ● |
| `RuleContract` | 语义面（声明）→ 运行面（求值） | 业务规则的一等声明：条件 AST、严重级别、优先级、发布/归档状态；运行面只做确定性求值 + 审计 | 新增：[`domain/rule/model.py`](../../src/onto_platform/domain/rule/model.py)、[`domain/rule/engine.py`](../../src/onto_platform/domain/rule/engine.py)、[`application/rule_usecases.py`](../../src/onto_platform/application/rule_usecases.py)；[`bootstrap.py`](../../src/onto_platform/bootstrap.py) `Platform.rules` | ◆ P1 已落地（含测试与 REST） |
| `EventContract` | 语义面（声明）→ 数据面（订阅） | 业务事件 schema（名称/版本/聚合/payload 字段）+ 订阅（类型/过滤）；复用 outbox 投递 | 新增：[`domain/event/model.py`](../../src/onto_platform/domain/event/model.py)、[`application/event_usecases.py`](../../src/onto_platform/application/event_usecases.py)；扩展既有 [`application/events.py`](../../src/onto_platform/application/events.py)；[`bootstrap.py`](../../src/onto_platform/bootstrap.py) `Platform.business_events` | ◆ P1 已落地（含测试与 REST） |
| `RuntimeGateway` | 运行面 | 平台访问 Kernel 的唯一出口：publish_schema / describe* / get_object / find_objects / related / invoke / apply_delta / delete_objects / observe_changes | 现有：[`ports/runtime.py`](../../src/onto_platform/ports/runtime.py)；实现 [`infrastructure/runtime/kernel_gateway.py`](../../src/onto_platform/infrastructure/runtime/kernel_gateway.py) | ● |
| `ApplicationSpec` | 应用编排面 | 应用作为一份声明式组合契约：views + queries + interactions + agents + capabilities + ui_artifact，带诊断与 valid 标记 | 新增：[`domain/application/spec.py`](../../src/onto_platform/domain/application/spec.py)；[`application/application_usecases.py`](../../src/onto_platform/application/application_usecases.py) `GetApplicationSpec`（返回 `{spec, diagnostics, valid}`） | ◆ P1 已落地（含测试与 REST） |
| `ViewSpec` | 体验面（观察） | 视图契约：数据绑定、查询引用、渲染配置、布局、权限裁剪；兼容 P0 `View` | 新增：[`domain/application/spec.py`](../../src/onto_platform/domain/application/spec.py) `ViewSpec`；`from_view()` 提升既有 [`domain/application/model.py`](../../src/onto_platform/domain/application/model.py) `View` | ◆ P1 已落地（含测试与 REST） |
| `InteractionSpec` | 体验面（交互） | 交互契约：动作/流程/表单/Agent 入口，只声明引用（`capability_id`），不执行 | 新增：[`domain/application/spec.py`](../../src/onto_platform/domain/application/spec.py) `InteractionSpec`；`Application.interactions`（追加式字段）；`AddInteraction`/`RemoveInteraction` 用例 | ◆ P1 已落地（含测试与 REST） |
| `Blueprint` / `CompiledProgram` | 生成面 → 体验面 | AI 生成产物：Blueprint（候选结构）+ 确定性编译程序（`program_hash`）+ 发布产物；可回放 | 现有：[`application/smartui/bridge.py`](../../src/onto_platform/application/smartui/bridge.py)、[`adjudication.py`](../../src/onto_platform/application/smartui/adjudication.py)、[`publisher.py`](../../src/onto_platform/application/smartui/publisher.py)、[`studio.py`](../../src/onto_platform/application/smartui/studio.py) | ● |
| `Release` / `Deployment` | 治理面 | 不可变发布制品（schema+bindings+workflows+能力集+指纹）与部署指针（current/canary，pending→active/failed） | 现有：[`application/releases.py`](../../src/onto_platform/application/releases.py)（cut/deploy/canary/promote/rollback）；repo kind `release`/`deployment` | ● |
| `PolicyDecision` | 治理面 | PDP 输出：`allow` + 原因码 + 义务/作用域；PEP 只允许对 `allow` 分支 | 现有：[`domain/security/model.py`](../../src/onto_platform/domain/security/model.py) `Decision`；[`application/security/pdp.py`](../../src/onto_platform/application/security/pdp.py) `PolicyEvaluator.evaluate` | ● |
| `ComponentManifest` | 扩展面 / 体验面 | 组件/插件注册契约：id、kind、版本、API 版本、权限、视图类型→前端组件映射 | 现有：[`application/smartui/contracts.py`](../../src/onto_platform/application/smartui/contracts.py) `PluginManifest`；[`builtin.py`](../../src/onto_platform/application/smartui/builtin.py) `BUILTIN_MANIFESTS`；[`renderers.py`](../../src/onto_platform/application/smartui/renderers.py) `RendererDescriptor` | ● |
| `AdapterManifest` / `AdapterArtifact` | 扩展面 | 标准格式适配插件 SPI（Turtle TBox 导出/导入），注册表 + 兼容性与依赖校验 | 现有（P0 已落地）：[`semantic/spi.py`](../../src/onto_platform/semantic/spi.py)、[`semantic/registry.py`](../../src/onto_platform/semantic/registry.py) | ● |
| 平台事件 envelope（outbox） | 数据面 / 治理面 | 平台领域事件目录 + envelope + 同事务 outbox + 重试/死信 + 进程内订阅 | 现有：[`application/events.py`](../../src/onto_platform/application/events.py) `EventLog` / `Dispatcher` / `EVENT_CATALOG` | ● |
| `SemanticDelta` / `SchemaPayload` / `InvocationResult` | 数据面 → 运行面 | 投影增量（upserts/links/unlinks/deletes）、编译载荷、三值调用结果（succeeded/failed/unknown） | 现有：[`ports/runtime.py`](../../src/onto_platform/ports/runtime.py) | ● |
| 执行账本（ExecutionLedger） | 运行面 / 治理面 | 能力调用的幂等键 claim / 结果记录 / 对账，`execution_id` 可追溯 | 现有：[`application/execution_ledger.py`](../../src/onto_platform/application/execution_ledger.py) | ● |

### 1.3 本次窗口（P0-A + P1）结论

* **P0-A（已完成）**：ADR-0001 落盘（五层叙事 + 五面 + 三边界），本索引建立并锁定 canonical 集合与 owner。
* **P1（已完成，2026-11-06）**：5 个新增契约全部落地 —— `RuleContract`、`EventContract`、`ApplicationSpec`、`ViewSpec`、`InteractionSpec`，均含 domain 契约 + use case + bootstrap 接线 + REST（14 个端点，见 §4）+ 测试；全量 `python -m pytest -q` → **1033 passed**。
* **本次明确不落地（P2）**：AI 生成评估闭环、外部事件投递、插件沙箱等（详见 §5），避免半成品接口。
* **不动**：Kernel 五原语、`OntologyCompiler`、Smart UI L0–L5 管线、`Release`/`Deployment`、PDP、事件 outbox、标准适配 SPI —— P1 复核确认边界未被破坏（证据见 [`p1-implementation-status.md`](p1-implementation-status.md) §4）。

---

## 2. 已存在契约（字段级摘要）

### 2.1 `OntologySchema`（编译产物，canonical）

落点：[`domain/ontology/compiler.py`](../../src/onto_platform/domain/ontology/compiler.py)；传输载体 `SchemaPayload`（[`ports/runtime.py`](../../src/onto_platform/ports/runtime.py)）。

```
schema: { ontology: {name, version, schema_version, profile},
          objects:   {<ObjectType>: {description, identity[], category, properties{}},
                      + state_machine?: {field, transitions{}}},
          relations: {<Relation>: {source, target, cardinality?, inverse?}},
          functions: {<Type.key>: {target, description, input{<arg>: {type}}} } }
bindings:  {<Type.key>: {executor: python|workflow|agent, entrypoint}}
workflows: {<workflow://capability-id>: [step, ...]}   # 平台执行器消费，Kernel 不解释
```

* 校验/不变式：纯函数、确定性输出；`profile`（closed/open）随本体声明；发布时由 [`application/releases.py`](../../src/onto_platform/application/releases.py) 计算 `schema_fingerprint`，同指纹 = 同语义。
* 兼容约定：只增不删字段；`functions` 由 `Capability` 编译而来，平台不得手写。

### 2.2 `CapabilityContract`（canonical）

落点：[`domain/capability/model.py`](../../src/onto_platform/domain/capability/model.py)。

```
Capability: id, workspace_id, key, name, target_type, description,
            inputs[CapabilityInput{name, type, description}],
            implementation{kind: python|workflow|agent, source?, entrypoint?, steps?, goal?},
            contract{preconditions[], postconditions[], effects[], idempotent?, compensate?},
            lifecycle: draft|published, created_at, updated_at
contract 条件: {property, op ∈ {eq,ne,gte,lte,gt,lt,in,changed,unchanged,eq_prev_plus}, value}
```

* 校验入口：`validate_contract()`、`validate_capability()`、`validate_steps()`；发布后编译进 `OntologySchema.functions` 与 `bindings`。
* 执行：只能经 `RuntimeGateway.invoke`；结果三值（`succeeded` / `failed` / `unknown`），`unknown` 不得当成功或失败，需幂等重试或按 `execution_id` 对账。

### 2.3 `RuntimeGateway`（运行面唯一出口，canonical）

落点：[`ports/runtime.py`](../../src/onto_platform/ports/runtime.py)；实现 [`infrastructure/runtime/kernel_gateway.py`](../../src/onto_platform/infrastructure/runtime/kernel_gateway.py)（全仓库唯一 `import kernel` 位置）。

```
协议方法: publish_schema(SchemaPayload)
          describe() / describe_object(name) / describe_relation(name) / describe_function(name)
          get_object("Type/Id") / find_objects(type, filters, report) / related(ref, relation, direction)
          invoke(function, target, args, timeout, actor) -> InvocationResult
          apply_delta(SemanticDelta, source, must_exist) / delete_objects(refs, source)
          observe_changes(after_seq) -> [change]
状态语义: InvocationResult.status ∈ {succeeded, failed, unknown}；only succeeded ⇒ ok
```

* 判据：`domain/` 不得触及该协议；`interfaces/` 不直接调用 gateway；执行必须经 use case。

### 2.4 `Blueprint` / `CompiledProgram` / `BuildManifest`

落点：[`application/smartui/{bridge,adjudication,publisher,studio}.py`](../../src/onto_platform/application/smartui/)；产物挂在 `Application.ui_artifact`。

```
Blueprint（LLM 候选，经裁决后归一）:
  question, roles[], context{object, focus?},
  views[{alias, title, kind, object_type, properties[], perspective, config, layout}],
  actions[{key, capability, label, ...}], security{...}
CompiledProgram: program_hash + 编译后的 OpenUI 程序（视图/动作/工具闭包）
BuildManifest: 版本、prompt_hash、program_hash、插件锁（execution lock）、published_at/by
```

* 不变式（Boundary-3）：LLM 永不直接进编译器；必须先经 adjudicator/sanitizer 与确定性编译；发布产物可回放（历史 Blueprint 重编译）；任何进入 `ui_artifact` 的东西都带 hash。

### 2.5 `Release` / `Deployment`（canonical）

落点：[`application/releases.py`](../../src/onto_platform/application/releases.py)。

```
Release: id, workspace_id, schema_version, fingerprint, schema, bindings, workflows,
         capability_ids[], previous_release_id, created_at, actor, note
Deployment: id(current|canary), workspace_id, release_id, schema_version, fingerprint,
            base_release_id, status: pending|active|failed, checked_at, deployed_at,
            actor, state_mode, activated_at, (canary) promoted
```

* 流程：`cut_release` → `deploy_release`（先写 pending 再 publish，Kernel 失败则回滚指针与运行时）→ `check_canary`/`promote_canary`/`rollback`。
* 不变式：只有本模块与 bootstrap 发布链路可移动 deployment 指针；Release 不可变；能力集缺失时拒绝部署。

### 2.6 `PolicyDecision`（canonical）

落点：[`domain/security/model.py`](../../src/onto_platform/domain/security/model.py) `Decision`；[`application/security/pdp.py`](../../src/onto_platform/application/security/pdp.py) `PolicyEvaluator.evaluate`。

```
Decision: allow, reason, action, principal_id, tenant_id, workspace_id,
          decision_id, resource_type, resource_id, policy_id, policy_version,
          obligations[Obligation], scope, created_at, duration_ms, ttl_seconds
property: effect = allow | deny
```

* 语义：`allow` 是 PEP 唯一可分支字段；`obligations` 未满足时决策必须为 deny（`unresolved_obligation`）；reason 与设计文档 reason_code 双拼写兼容。
* 缓存：`DecisionCache` 按 TTL 复用；决策可审计（append-only）。

### 2.7 `ComponentManifest`（组件/插件注册）

落点：[`application/smartui/contracts.py`](../../src/onto_platform/application/smartui/contracts.py)；内置清单在 [`builtin.py`](../../src/onto_platform/application/smartui/builtin.py)。

```
PluginManifest: id, kind ∈ PLUGIN_KINDS, version, api_version(smartui/v1),
                summary, perspectives[], view_type, component, order,
                builtin, permissions[] ∈ PLUGIN_PERMISSIONS, metadata, api_versions[]
RendererDescriptor: view_type → component（+ plugin_id, order, format）
```

* 校验：id/version 正则、kind 白名单、permissions 白名单、api_version 兼容；插件锁（`PluginLockEntry`）保证回放时映射不漂移。
* 边界（Boundary-2）：manifest 只声明“谁能画/生成/裁决”，不携带业务语义；前端渲染器是消费者。

### 2.8 `AdapterManifest` / `AdapterArtifact`（扩展面 SPI，P0 已冻结）

落点：[`semantic/spi.py`](../../src/onto_platform/semantic/spi.py)、[`semantic/registry.py`](../../src/onto_platform/semantic/registry.py)。

```
AdapterManifest: name, version, api_version="1.0", standards[], capabilities[],
                 formats_out[], formats_in[], description, permissions{},
                 requires{ontoplatform, extras?}, config_schema{}, aliases[]
AdapterArtifact: content(bytes), media_type, filename, format, scope="tbox", metadata{}
```

* 注册：`PluginRegistry`（builtin `onto_platform.semantic.owl:OwlRdfAdapter` / `...:SkosAdapter`）；别名 `onto-standards-owl` / `onto-standards-skos`。
* 失败语义：`AdapterInvocationError` → `("plugin_failure", 502, False)`；manifest 不兼容/缺依赖时 `status=invalid` 而非崩溃。

### 2.9 平台事件 envelope（outbox）

落点：[`application/events.py`](../../src/onto_platform/application/events.py)。

```
envelope: id/event_id, type ∈ EVENT_CATALOG, event_version, ts, workspace_id, actor,
          aggregate{kind, id, version?}, execution_id?, payload{}, dispatched_at,
          attempts, last_error, next_attempt_at?, dead_lettered_at?, delivery?, consumer?
投递: at-least-once；失败退避重试（base 5s，指数），5 次后进死信；订阅者按 event_id 幂等
```

* 不变式：`type` 不在闭集 `EVENT_CATALOG` 时拒绝（`ValidationError`）；`workspace_id` 必须存在；写入与业务状态同事务（outbox）。
* 扩展（P1 已落地）：`EventLog.emit(..., contract=EventContract|None)` 先校验 `event_type` 与契约名一致、契约已发布、payload 匹配 `payload_schema`，再写 outbox；envelope 增加 `contract` 字段（业务事件名，平台生命周期事件不带）；`Dispatcher.subscribe_key(handler_key, handler)` + `subscriptions_for_event()` 支持声明式订阅的进程内分发。
* 多 worker 认领（P2 已落地）：`EventLog.claim(event, worker_id, lease_seconds)` 用 `get_versioned` + `put_if_version` 做 CAS，写 `claimed_by/claimed_at/lease_expires_at`；`Dispatcher.dispatch(..., worker_id=)` 先认领再投递；`pending()` 跳过未过期租约。租约到期可被其它 worker 重认领（at-least-once）。
* 持久 handler 与 per-key 投递（P2.2-C 已落地）：`event_handler` 注册表把 `handler_key` 从进程内 dict 提升为 workspace 级持久文档；`event_delivery` 账本按 `(event_id, handler_key)` 建唯一行并 CAS 认领；`Dispatcher` 取 registry ∪ subscription 的 key 并集，只有全部 delivery 终态（delivered/dead）才 `mark_dispatched`，无本地回调的 key 保持 pending 并释放事件 claim。投递仍是 at-least-once，handler 必须按 `event_id` 幂等；数据库队列不是 Kafka/NATS。

### 2.10 `PluginSBOM` / `GenerationEval`（P2 观测面）

落点：[`application/smartui/host.py`](../../src/onto_platform/application/smartui/host.py) `PluginHost.sbom`、[`application/smartui/eval.py`](../../src/onto_platform/application/smartui/eval.py) `generation_eval`。

```
PluginSBOM: bomFormat="OntoPlatform-SBOM", specVersion, hostApiVersion,
            trustBoundary="in-process", processIsolation=false,
            externalPluginsEnabled, components[], dependencies[]
component: type, name, version, kind, apiVersion, builtin, enabled,
           implemented, permissions[], manifestDigest(sha256 canonical JSON)

GenerationEval: workspace_id, baselineId, current, baseline?, delta?
current: count, byStatus{}, succeeded, failed, successRate,
         duration{p50Ms,p95Ms,avgMs}, usage{promptTokens,completionTokens,totalTokens},
         cost{total,currency}, views{total,average}, programHashCount
delta: successRate, p95Ms, totalCost, avgViewCount
```

* SBOM 只序列化 manifest 摘要，**不序列化实现对象**；`processIsolation=false` 明确声明内置/已安装插件仍是进程内信任边界，SBOM 不是沙箱。P2.3 新增的 `SandboxUseCases.run_package` 是独立的可选子进程执行入口（结果不注册进 host），其强度见 §2.12。
* GenerationEval 是只读聚合（`viewer`），质量指标以成功率代理；成本/延迟/用量来自既有 generation 记录，不改变生成管线。P2.4 的 `generation_eval_set` 在其上增加可回放评测集与加权评分（§2.12）。

### 2.11 `WebhookDelivery` / `EventHandler` / `EventDelivery`（P2.2 生产边界）

落点：[`application/security/webhook_delivery.py`](../../src/onto_platform/application/security/webhook_delivery.py)、[`application/security/distribution.py`](../../src/onto_platform/application/security/distribution.py)、[`application/event_handlers.py`](../../src/onto_platform/application/event_handlers.py)、[`application/events.py`](../../src/onto_platform/application/events.py)。

```
WebhookDelivery: id=whd_sha256(event_id|webhook_id), workspace_id, webhook_id, event_id,
                 event_type, status(pending|delivered|failed|dead), attempts,
                 lease_expires_at, lease_owner, next_attempt_at, last_status_code,
                 last_error, response_excerpt, created_at, updated_at
Webhook: +rate_limit_per_minute(int=60), +max_attempts(int=5),
         +window_start, +window_count（固定窗口，CAS 更新）
EventHandler: id, handler_key(workspace 内唯一), workspace_id, event_types[],
              kind(in_process|http), endpoint, secret, active, version,
              last_heartbeat_at, lease_seconds, created_by, created_at, updated_at,
              last_worker?
EventDelivery: id=sha256(event_id|handler_key), workspace_id, event_id, handler_key,
               status(pending|delivered|failed|dead), attempts, lease_expires_at,
               next_attempt_at, last_error, created_at, updated_at
```

* Webhook 状态机：`pending` → CAS lease → `delivered` | `failed`（退避）→ `dead`（attempts≥max）；事件 `webhook_delivered_at` 只在所有匹配 webhook 的 delivery 终态时写；`replay_delivery` 把 `dead/failed` 重置为 `pending` 并清事件 marker。
* EventHandler：按 `handler_key` upsert（version 递增，不重复插行）；`resolve(event)` 只返回 active 且 `event_types` 匹配的行；`heartbeat` 刷新 `last_heartbeat_at`；http handler 必须 `endpoint`+`secret`，投递为 HMAC-SHA256 签名 POST。
* 隔离与强度：租户边界是 **L1 仓储级**（`GovernedRepository` 拒绝伪造 tenant/workspace），不是进程/容器隔离；两套投递账本都解决“同一事件不被多个 worker 同时投递”，但都是 at-least-once，不提供 exactly-once 或跨表事务；`ctx.repo.raw` 是预留逃生口。

### 2.12 P2.3–P2.6 与 Digital Twin 契约（2026-11-09）

落点：[`application/smartui/sandbox.py`](../../src/onto_platform/application/smartui/sandbox.py)、[`application/smartui/eval_set.py`](../../src/onto_platform/application/smartui/eval_set.py)、[`application/event_schema_registry.py`](../../src/onto_platform/application/event_schema_registry.py)、[`application/event_bridge.py`](../../src/onto_platform/application/event_bridge.py)、[`application/digital_twin.py`](../../src/onto_platform/application/digital_twin.py)、[`application/rule_index.py`](../../src/onto_platform/application/rule_index.py)。

```
SandboxResult: ok, result, error, exit_code, timed_out, duration_ms,
               stdout_excerpt, stderr_excerpt,
               isolation{process, filesystem, environment, network},
               resource_limits{cpuSeconds, addressSpaceMb, enforced, platform}
EvalSet: id, workspace_id, name, description, status(draft|active), baseline_id,
         cases[], created_by, created_at, updated_at, version
EvalCase: id, name, generation_id?, question?, roles[], tags[],
          max_duration_ms?, max_cost?, min_view_count?, expected_program_hash?
GenerationEvalReplay: workspace_id, evalSetId, replayedAt,
          summary{total,passed,failed,errors,passRate,score,grade,scoreDelta?,
                  passRateDelta?}, cases[],
          scoreModel{success:0.35,replay:0.25,latency:0.15,cost:0.15,views:0.10},
          baseline?
EventSchema: id=es_<event_type>_v<version>, workspace_id, event_type, version,
             payload_schema{field:{type,required?,description?}}, compatibility,
             description, created_by, created_at
SchemaCompatibility: {compatible, mode(backward|forward|full|none),
                      breaking[{field,reason}], added[], removed[], typeChanged[]}
EventBridge: id, workspace_id, name, event_types[], url, webhook_id,
             source_bridge_id?（webhook 侧反向引用）, field_map{external:payload.path},
             include_envelope, active, created_by, created_at, updated_at
TwinPolicy: id, workspace_id, name, source_type, relation, target_field,
            source_field, mode(copy|scale|boolean|delta), factor, active, version,
            created_by, created_at, updated_at
TwinSnapshot: workspace_id, ref, depth, direction(out|in|both), nodes[],
              edges[], stateHash, generatedAt, truncated, capabilities
TwinSimulation: ref, object_type, object_id, depth, changes,
                before/after{passed,violations[],total}, total_before/after,
                resolved[], introduced[], changed[], changedCount,
                policiesApplied[], stateHashBefore/After, noSideEffects
TwinScenarios: root, scenarios[{name,score,summary,changedCount,ruleDelta}],
               best, noSideEffects
RuleFieldIndex stats: scanned, fields, values, rules_indexed, rules_scanned
```

* 沙箱 `isolation` 是**如实声明**：`process=true`、`filesystem="temporary-directory"`、`environment="allowlist"`、`network="not-enforced"`；`resource_limits.enforced` 在 Windows 为 false。子进程结果不是插件注册，`PluginHost` 的 `processIsolation=false` 仍描述内置/已安装插件。
* 评测集 `score = 100 × Σ(通过维度权重)`；无约束维度按满分且 `checks[].passed=null`（skipped）；`summary.score` 为 case 均值（2 位小数），A≥90/B≥80/C≥70/D≥60/F<60；baseline 给出 `scoreDelta/passRateDelta`。
* Schema Registry 版本不可变：同一 `(event_type,version)` 重复注册报错；backward 拒绝删字段/改类型/可选→必填/新增 required；forward 对称；full 取并集；none 跳过；`compatibility()` 不写库。
* EventBridge 不实现重试：1 bridge 对应 1 个内部 webhook，投递状态全在 `webhook_delivery` 账本；`field_map` 是服务层 `transform`，尚未接入 webhook transport 的组装点。
* Digital Twin 只读：`snapshot` 是调用时刻的 kernel 状态与关系 BFS（不是流式实时）；`simulate` 只在副本上叠加 changes 与 policy 传播并重算规则，`noSideEffects=true`，不写对象/关系、不持久化仿真；`stateHash` 是 nodes+edges 的 canonical JSON sha256。
* 规则字段索引是 **per-evaluation 内存 posting list**，不持久化；`EvaluateRules(index=True)` 只对可索引的正向叶子裁剪，not/any/is_null/不可哈希/aggregate 回退全扫描，并返回 `index_stats`。

---

## 3. P1 新增契约（已落地）

> 字段级摘要已按最终代码核对（2026-11-06）；测试与 REST 证据见 §4 与 [`p1-implementation-status.md`](p1-implementation-status.md)。后续若调整字段，必须在同一次变更里同步本节。

### 3.1 `RuleContract`（task-18）

落点：`domain/rule/model.py`、`domain/rule/engine.py`、`application/rule_usecases.py`；状态：◆ P1 已落地（`test_rule_engine.py` 85、`test_rule_usecases.py` 43、REST 见 §4）。

```
RuleSpec: id, workspace_id, name, description, object_type, kind(constraint|aggregate),
          severity(error|warning|info), condition{AST}, when{AST, 默认 {}}, message,
          priority(int=100), enabled(bool=True), lifecycle(draft|published|archived),
          version(int), created_at, updated_at, aggregate{...}|None
condition AST: {"field","op","value"} | {"all":[...]} | {"any":[...]} | {"not":{...}}
               field 支持 $id / $type / properties 名（含嵌套点路径，缺失/标量中段 = MISSING）；op 支持 eq ne gt gte lt lte in not_in
               contains not_contains is_null not_null starts_with ends_with
aggregate: {relation, object_type, where?{AST}, metric(count|sum|avg|min|max|any|all),
            field?, op, value}
RuleViolation: rule_id, rule_name, object_type, object_id, severity, message, field, detail(dict)
RuleEvaluation: passed, evaluated_rules(int), violations[dict], skipped(int)
RuleFieldIndex.stats: scanned, fields, values, rules_indexed, rules_scanned
```

* 常量（闭集）：`RULE_KINDS=("constraint","aggregate")`、`SEVERITIES`、`RULE_LIFECYCLES=("draft","published","archived")`、`OPERATORS`（14 个）、`METRICS=("count","sum","avg","min","max","any","all")`、`LOGICAL_KEYS=("all","any","not")`、`SPECIAL_FIELDS=("$id","$type")`、`MAX_CONDITION_DEPTH=32`；AST 不可表达任意代码。
* 求值语义：纯函数、零依赖；按 `(priority, name, id)` 排序；只有 `severity="error"` 的 violation 让 `passed=False`；**缺失字段 = MISSING**（`is_null`/`not_null` 可判定，其它比较一律 False，不抛异常）。
* 聚合语义（P2.5）：`aggregate` 规则的 `condition` 被忽略；对父对象经 `relation` 取 children，`where` 只对 child 求值；count/any/all 不需要 `field`，sum/avg/min/max 取数值字段（缺失/非数字跳过）；空集 count=0、sum=0、avg/min/max=MISSING、any=False、all=True；violation.field=`aggregate.<relation>`；`SimulateRuleChanges` 暂不支持 aggregate。
* 字段索引（P2.5）：`application/rule_index.py` 一次扫描建内存 posting list；仅 eq/in/contains/starts_with/ends_with 正向叶子可裁剪，其余回退全扫描；`EvaluateRules(index=True)` 返回追加的 `index_stats`，旧键不变；索引不持久化、翻页每页重建。
* 校验入口：`validate_condition` / `validate_aggregate` / `validate_rule_definition(rule, object_spec)`；引擎侧 `ensure_valid_condition` 在非法 AST 时抛 `ValidationError`。
* 用例（`rule_usecases.py`）：Create（支持 `kind/aggregate`）/Update（draft only + 乐观锁）/Publish（重复发布幂等）/Disable/Delete（draft 删除、published 归档）/List/Evaluate/Simulate；Evaluate 默认只取 published+enabled，按对象 id 稳定分页（`limit` 默认 100、`offset`），返回 `passed/object_count/total_objects/evaluated_objects/offset/limit/has_more/next_offset/rule_count/violations/missing/by_severity/evaluated_rules/skipped`，审计 `rule.evaluate`；`object_count` 保持“总候选数”以兼容旧调用方，`violations/by_severity/evaluated_rules` 只统计当前页。`Simulate` 对单个对象应用顶层属性假设值并重算同一批规则，返回 `before/after/delta{resolved,introduced,changed}`，审计 `rule.simulate`，无副作用。
* REST（已实现）：`GET/POST /rules`、`POST /rules/{rid}/publish`、`POST /rules/evaluate`（可传 `index`）、`POST /rules/simulate`（详见 §4）。

### 3.2 `EventContract`（task-19）

落点：`domain/event/model.py`、`application/event_usecases.py`，扩展 [`application/events.py`](../../src/onto_platform/application/events.py)；状态：◆ P1 已落地（`test_event_contract.py` 46、`test_event_subscriptions.py` 17、REST 见 §4）。

```
EventContract: name(^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$), version(int>=1),
               description, aggregate_kind, payload_schema{field: {type, required, description}},
               lifecycle(draft|published), created_at, updated_at
EventSubscription: id, workspace_id, name, event_type, filter{field: [values]},
                   handler_key, enabled, created_at
payload 类型闭集: string | number | integer | boolean | object | array
匹配语义: filter 是 payload 的子集匹配（类型/enabled/字段缺失任一不满足即不匹配）；
          只有 published 契约可 emit，emit(contract=...) 校验 event_type 与契约名一致、
          lifecycle=published、payload 通过 schema，再落 outbox 并标记 event.contract
```

* 校验入口：`validate_event_name` / `validate_payload_schema`（归一化字段类型）/ `EventContract.validate_payload(payload)`；`matching_subscriptions(subscriptions, event)`（`domain/event/model.py`）按类型 + enabled + filter 匹配。
* 用例（`event_usecases.py`）：CreateEventContract/PublishEventContract/ListEventContracts/SubscribeEvent/ListEventSubscriptions/DeleteEventSubscription/EmitEvent；`events.py` 新增 `subscriptions_for_event()` 与 `Dispatcher.subscribe_key(handler_key, handler)`。
* 边界（Boundary-1）：只声明与匹配，不新开投递通道；对外投递不在本轮（见 §5）。
* REST（已实现）：`GET/POST /event-contracts`、`POST /event-contracts/{name}/publish`、`GET/POST /event-subscriptions`、`DELETE /event-subscriptions/{sid}`、`POST /events/emit`（详见 §4；emit 返回事件 + `matched_subscriptions`）。

### 3.3 `ViewSpec`（task-20）

落点：[`domain/application/spec.py`](../../src/onto_platform/domain/application/spec.py)；状态：◆ P1 已落地（`test_application_spec.py` 26；经 `GET /applications/{aid}/spec` 暴露）。

```
ViewSpec: alias, title, kind ∈ VIEW_SURFACES, object_type, query_alias,
          properties[], config{}, layout{}, permissions{}
VIEW_SURFACES: table | card | chart | map | graph | dashboard | timeline | spatial
校验: alias 必填；chart 需 config.x/config.y；map 需 config.lat/config.lng；
      graph/dashboard/timeline/spatial 为新增观察面（Digital Twin 不是 kind）
```

* 兼容：`ViewSpec.from_view(View)` 无损提升 P0 视图；P0 `VIEW_KINDS` 仍是合法子集。
* 边界（Boundary-2）：只声明“看什么/怎么画/裁剪什么权限”，不含取数实现；权限裁剪由消费方按 `permissions` 执行。
* 诊断：`ApplicationSpec.diagnostics` 报 `unknown_object_type` / `unknown_query` / `missing_view_config`。

### 3.4 `InteractionSpec`（task-20）

落点：同上 + `Application.interactions`（追加式）；状态：◆ P1 已落地（`test_application_spec.py` + `test_architecture_api.py`；REST 见 §4）。

```
InteractionSpec: id, kind ∈ {action, workflow, form, agent}, title, capability_id,
                 config{}, permissions{}, approval{}
校验: id 必填；action/form 必须有 capability_id；config/permissions/approval 必须 dict
```

* 边界（Boundary-2）：只声明“能做什么/走哪个能力/要不要审批”，不携带执行逻辑；执行仍走 Capability 用例 + `RuntimeGateway`。
* 变更：`AddInteraction` / `RemoveInteraction` 用例；追加/删除只允许 draft 应用，重复 id 拒绝，`capability_id` 必须指向已发布能力（负例测试覆盖）。
* REST（已实现）：`POST /applications/{aid}/interactions`、`DELETE /applications/{aid}/interactions/{iid}`（详见 §4）。

### 3.5 `ApplicationSpec`（task-20）

落点：[`domain/application/spec.py`](../../src/onto_platform/domain/application/spec.py) + `GetApplicationSpec`；状态：◆ P1 已落地（`test_application_spec.py` 26、`test_architecture_api.py`、REST 见 §4）。

```
ApplicationSpec: application_id, workspace_id, name, description, version(int=1),
                 lifecycle(draft|published), views[ViewSpec], queries[dict],
                 interactions[InteractionSpec], agents[dict], capabilities[ids],
                 ui_artifact{dict}
诊断: diagnostics(ontology, capability_ids) -> [{code, message, path}]
codes: ontology_missing | unknown_object_type | unknown_query | missing_view_config | unknown_capability
```

* 语义（Boundary-2）：`Application` 聚合的**只读投影**；视图/查询按 alias 排序保证确定性；诊断是告警不抛异常（发布仍是硬门禁）。
* 返回：`GetApplicationSpec` → `{"spec": <ApplicationSpec dict>, "diagnostics": [{code,message,path}], "valid": bool}`（`valid = not diagnostics`）；能力按**已发布集合**判定。
* 兼容：现有 `Application` 的 API/模型语义不变；`interactions` 旧行默认 `[]`，round-trip 稳定。
* REST（已实现）：`GET /applications/{aid}/spec`（详见 §4）。

### 3.6 新增契约的边界自查（评审用）

| 检查项 | 判据 |
|---|---|
| 声明不含执行 | `domain/rule`、`domain/event`、`domain/application/spec.py` 只依赖标准库 + `domain`；无仓储/网关/内核引用 |
| 执行只有一条路 | 规则求值只读对象、只产出 violation；事件只落 outbox；interaction 只引用 `capability_id` |
| 生成不越权 | 任何 AI/规划产物不得直接调用 `RuntimeGateway` 或移动 deployment 指针 |
| 单一 owner | 5 个新契约各只有一个落点；API 层不出现规则/事件/编译逻辑 |

P1 收尾复核：上表判据全部通过（`grep` 证据与命令见 [`p1-implementation-status.md`](p1-implementation-status.md) §4）。

---

## 4. REST 端点（P1 已实现 14 个 + P2.1 增量 3 个 + P2.2 增量 9 个 + P2.3–P2.6/Twin 增量 24 个 + 2 个扩展）

> 路径均挂载在 `/api/workspaces/{wid}` 下，`user` 由 API 鉴权注入；权限在 use case 内检查（viewer/editor）。成功统一返回 200；错误沿用既有 `_error` 映射（400/403/404/409/502 等）。实现见 [`interfaces/api.py`](../../src/onto_platform/interfaces/api.py)，契约测试见 `tests/test_architecture_api.py` 与 `tests/test_p2_*.py`。

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
| 15 | POST | `/rules/simulate` | 规则级 what-if：`{ref, changes, include_drafts?}`，返回 `before/after/delta` | viewer |
| 16 | GET | `/smart-ui/eval?baseline=` | 生成评分卡聚合；带 baseline 时返回 delta | viewer |
| 17 | GET | `/smart-ui/sbom` | 插件 SBOM（manifest 摘要 + 进程内边界声明） | viewer |
| 18 | GET | `/security/webhooks/deliveries?webhook=&status=&limit=` | 列出 per-(event,webhook) 投递账本 | security.manage |
| 19 | GET | `/security/webhooks/dead-letters?limit=` | 列出 dead delivery | security.manage |
| 20 | POST | `/security/webhooks/deliveries/{id}/replay` | dead/failed → pending，重开事件标记 | security.manage |
| 21 | GET | `/event-handlers` | 列出持久 handler（secret 脱敏） | security.manage |
| 22 | GET | `/event-handlers/{key}` | 读取单个持久 handler | security.manage |
| 23 | POST | `/event-handlers` | 注册/更新 handler（handler_key/event_types/kind/endpoint/secret…） | security.manage |
| 24 | POST | `/event-handlers/{key}/heartbeat` | 刷新 worker 存活时间 | security.manage |
| 25 | POST | `/event-handlers/{key}/pause` | 暂停 handler（不参与 resolve） | security.manage |
| 26 | POST | `/event-handlers/{key}/activate` | 恢复 handler | security.manage |
| 27 | POST | `/plugins/sandbox/run` | 验签后在子进程执行外部插件（返回 SandboxResult） | editor |
| 28 | GET | `/eval-sets` | 列出生成分评测集 | viewer |
| 29 | POST | `/eval-sets` | 创建/同名更新评测集（cases 1–100） | editor |
| 30 | GET | `/eval-sets/{id}` | 读取评测集 | viewer |
| 31 | POST | `/eval-sets/{id}/replay` | 只读回放并返回加权评分/passRate/grade | viewer |
| 32 | DELETE | `/eval-sets/{id}` | 删除评测集 | editor |
| 33 | GET | `/event-schemas?event_type=` | 列出 payload schema 版本 | viewer |
| 34 | GET | `/event-schemas/{event_type}?version=` | 读取指定/最高版本 | viewer |
| 35 | POST | `/event-schemas` | 注册不可变 schema 版本（含兼容性检查） | editor |
| 36 | POST | `/event-schemas/compatibility` | 干跑兼容性检查（不写库） | viewer |
| 37 | GET | `/event-bridges` | 列出事件桥 | security.manage |
| 38 | POST | `/event-bridges` | 创建事件桥 + 内部 webhook | security.manage |
| 39 | GET | `/event-bridges/{id}` | 读取事件桥 | security.manage |
| 40 | POST | `/event-bridges/{id}` | 更新 url/event_types/field_map/active 等 | security.manage |
| 41 | POST | `/event-bridges/{id}/pause`、`/activate`、`/dispatch` | 停用/启用/触发投递（复用 webhook 账本） | security.manage |
| 42 | DELETE | `/event-bridges/{id}` | 停用桥与内部 webhook（保留账本） | security.manage |
| 43 | GET | `/twin/snapshot?ref=&depth=&direction=&max_nodes=` | 调用时刻实时状态 + 关系子图 + stateHash | viewer |
| 44 | GET | `/twin/policies` | 列出传播策略 | viewer |
| 45 | POST | `/twin/policies` | 创建/同名更新 copy/scale/boolean/delta 策略 | editor |
| 46 | DELETE | `/twin/policies/{id}` | 删除传播策略 | editor |
| 47 | POST | `/twin/simulate` | 反事实：changes + 关系传播 + 规则 delta（不写 kernel） | viewer |
| 48 | POST | `/twin/scenarios` | 1–20 方案比较并按违规/改动排序 | viewer |
| 49 | POST | `/rules/evaluate`（扩展） | 新增可选 `index`，返回 `index_stats` | viewer |
| 50 | POST | `/rules`（扩展） | 新增可选 `kind/aggregate` | editor |

路由消歧：`/rules/evaluate`、`/rules/simulate` 是字面路由，优先于 `/rules/{rid}/publish`；`event-contracts/{name}/publish` 支持带点号的事件名（如 `traffic.vehicle_ping`）；`events/emit` 与 `event-subscriptions` 分属“事件发布”和“订阅管理”两类资源，互不捕获；`/security/webhooks/deliveries` 与 `/security/webhooks/{id}` 按 `deliveries` 字面优先，`/{id}/replay` 不被 `/{id}/delete` 捕获；P2.3–P2.6 路由按 `/twin`、`/event-schemas`、`/event-bridges`、`/eval-sets`、`/plugins` 前缀委派，`/event-schemas/compatibility` 为字面路由先于 `/event-schemas/{event_type}` 判定，`/twin/policies` 与 `/twin/snapshot|simulate|scenarios` 同级不冲突。

---

## 5. 后续契约（本轮明确不做）

| 缺口 | 为什么现在不做 | 触发条件 / 前置 | 预计轮次 |
|---|---|---|---|
| **AI 生成评估闭环**（replay/eval 指标、成本/延迟/质量观测） | 现有已保 trace（`program_hash`、回放），缺评估基线与指标定义；先冻结契约再定指标 | Blueprint/Program 契约稳定 + 至少一个可回放的评测集 | P2 |
| **外部事件投递**（webhook / 事件桥 / Schema Registry） | 内部 outbox 契约刚落地；外呼涉及签名、重试、配额、死信治理，提前做会重复实现 | `EventContract` 发布 + 订阅用例稳定；需要跨系统集成时 | P2 |
| **插件沙箱 / 进程隔离 / SBOM** | 现有插件 host + 签名 + opt-in 够用；进程隔离属扩展面系统工程 | 引入第三方远程插件或需要资源限额时 | P2 |
| `BehaviorContract` 独立契约（把 `Capability.contract` 正式化为独立 schema） | P1 已由 `Capability.contract` 承载 pre/post/effects/idempotent/compensate，独立化的收益待评估 | 行为校验/补偿语法需要跨能力复用时 | P2 |
| 视图面扩展（Dashboard/Timeline/Spatial 的通用 schema 与 renderer） | 本轮只统一 `ViewSpec` 声明；前端 renderer 与组合布局在后续轮次 | `ViewSpec` 发布 + 前端按契约接入 | P2 |
| 租户级数据脱敏 / 密钥托管 / Schema 演进迁移 | 生产化范围（治理面），P1 只保持 `workspace_id`/`TenantContext` 边界 | 多租户生产部署前 | P2 |
| Digital Twin 仿真（实时状态 + 仿真 + 反事实） | 现实现是空间场景渲染；命名降级为 Spatial/3D Scene | 有遥测接入与仿真模型时 | 未排期 |
| 完整规则语言 / 推理 / 外部表达式 | 业务需要“可判定约束”，非通用表达式；P1 只做声明式 AST | 高频规则稳定后按需评估 | 未排期 |

> P2 首轮（2026-11-09）已在低风险范围内落地：发布/克隆校验、规则分页与嵌套字段、租户/仓储越权回归、业务事件 webhook、事件认领租约、生成评分卡聚合、插件 SBOM、规则级 what-if。
>
> P2.2（2026-11-09）进一步落地：L1 租户仓储硬边界、webhook per-(event,webhook) 投递账本（配额/重试/死信/重放）、持久 `event_handler` 注册表与 per-key `event_delivery`、`PublishApplication` interactions 校验、`CloneApplication` 深拷贝 `ui_artifact`，并接入 §4 的 9 个 REST 路由。
>
> P2.3–P2.6 + Digital Twin（2026-11-09）已落地：L2 子进程沙箱（网络隔离仍未强制）、可回放生成评测集与加权评分、规则内存字段索引与跨对象/聚合规则、事件 Schema Registry 与事件桥（复用 webhook 账本）、只读 Digital Twin 快照/传播/反事实仿真，并接入 §4 的 24 个新 REST 路由与 2 个扩展。仍未做（部署层）：网络/容器级沙箱、进程级租户隔离、外部 broker/独立 Registry、持久规则索引、流式 Twin 遥测、bridge `field_map` 的 transport 出站接线；对应边界见 [`p1-implementation-status.md`](p1-implementation-status.md) §6.4。

---

## 6. 契约治理

### 6.1 登记与 owner

* 新增契约：先在本索引登记（面、作用、落点、状态、字段摘要），再写代码；不允许“代码里先长出一个契约”。
* 一个契约一个 owner 面；跨面能力通过 Port/契约组合，不允许两个面各实现一套。
* 生成面产物必须带可回放标识（`program_hash` / 版本）；发布只能经 `Release`/`Deployment`。

### 6.2 变更与版本

* 破坏性变更（删字段、改语义、改默认值）必须追加 ADR（`docs/decisions/` 下一个编号）并更新本索引。
* 兼容性优先：新字段给默认值；旧读取方不得因未知字段崩溃（`from_dict` 忽略未知字段 / 追加式字段默认空）。
* 版本字段命名：契约自身用 `version`（int 递增）；能力/插件用 `api_version`（字符串）；事件用 `event_version`；schema 用 `schema_version` + `fingerprint`。

### 6.3 校验入口清单

| 契约 | 校验函数 / 入口 | 测试 |
|---|---|---|
| `OntologySchema` | `OntologyCompiler.compile` + `schema_fingerprint` | `tests/test_ontology_compiler.py`、`test_revision_contract.py` |
| `CapabilityContract` | `validate_contract` / `validate_capability` / `validate_steps` | `tests/test_capability_*`、`test_constraints_platform.py` |
| `RuleContract` | `validate_condition` / `validate_rule_definition`（引擎侧 `ensure_valid_condition` 抛 `ValidationError`） | `tests/test_rule_engine.py`（85）、`tests/test_rule_usecases.py`（43） |
| `EventContract` | `validate_event_name` / `validate_payload_schema` / `EventContract.validate_payload` | `tests/test_event_contract.py`（46）、`test_event_subscriptions.py`（17）、`test_architecture_api.py` |
| `ApplicationSpec` / `ViewSpec` / `InteractionSpec` | `ViewSpec.validate` / `InteractionSpec.validate` / `ApplicationSpec.diagnostics` | `tests/test_application_spec.py`（26）、`test_architecture_api.py` |
| P1 接线（bootstrap） | `Platform.rules` / `Platform.business_events` / `ApplicationUseCases.get_spec` | `tests/test_platform_wiring.py`（6） |
| `RuntimeGateway` | gateway 契约测试 + 三值结果断言 | `tests/test_gateway.py`、`test_truth_contract.py` |
| `Release`/`Deployment` | deploy/canary/rollback 路径与指纹一致性 | `tests/test_release_rings.py`、`test_release_events.py`、`test_release_recovery.py` |
| `PolicyDecision` | `PolicyEvaluator.evaluate` + obligations 用例 | `tests/test_security_*` |
| `ComponentManifest` | manifest 校验 + plugin lock 漂移检测 | `tests/test_smart_ui_plugins.py`、`test_smart_ui_studio.py` |
| `AdapterManifest` | `adapter_contract_problems` / `requires_problems` | `tests/test_semantic_spi.py`、`test_semantic_registry.py` |
| P2.3 `SandboxResult` | `run_python_plugin` 参数/上限校验 + `isolation/resource_limits` 审计 | `tests/test_p2_plugin_sandbox.py`（15） |
| P2.4 `GenerationEvalSet/Replay` | `_normalize_cases` + replay 逐 case checks + `_score` | `tests/test_p2_eval_set.py`（11） |
| P2.5 `RuleSpec.aggregate` / `RuleFieldIndex` | `validate_aggregate` / `validate_rule_definition` / `RuleFieldIndex.build` | `tests/test_p2_rule_index.py`（18）、`test_p2_rule_aggregate.py`（51） |
| P2.6 `EventSchema` / `EventBridge` | `SchemaRegistry.register/compatibility` / `EventBridgeService.create/transform` | `tests/test_p2_event_schema_registry.py`（18）、`test_p2_event_bridge.py`（20） |
| Digital Twin `TwinPolicy/TwinSnapshot/TwinSimulation` | `_validate_depth/direction/policy/changes` + `simulate` 副本语义 | `tests/test_p2_digital_twin.py`（45） |
| P2 REST 接线 | `_get_p2/_post_p2/_delete_p2` 路由与 `_require_service` 守卫 | `tests/test_p2_advanced_api.py`（5） |

### 6.4 架构判据（可 grep 的检查）

* `grep -rn "ontology_runtime" src/onto_platform` → 只允许出现在 `infrastructure/runtime/kernel_gateway.py`（内核包名，Kernel 的唯一适配点）；
* `src/onto_platform/domain/**` 不得 import `application` / `interfaces` / `infrastructure`；
* `src/onto_platform/ports/**` 不得 import `infrastructure`；
* `application/smartui/**` 不得 import `infrastructure.runtime`，不得调用 `publish_schema`；
* `interfaces/api.py` 不出现规则求值、事件投递、编译逻辑（只做参数解析 + use case 调用）；
* `pyproject.toml` 的 `dependencies = []` 不变。

P1 复核（2026-11-06）：上列判据全部通过（`domain/` 无 application/infrastructure import；`ontology_runtime` 只出现在 `kernel_gateway.py`；`smartui/` 无 infrastructure import；`api.py` 无 `publish_schema`；`dependencies = []`）；`python -m pytest -q` → 1033 passed。证据与命令见 [`p1-implementation-status.md`](p1-implementation-status.md) §4–§5。

---

## 7. 参考

* 架构决策：[ADR-0001](../decisions/0001-ontology-driven-application-architecture.md)
* 架构分析：[`ontology-driven-application-architecture.md`](ontology-driven-application-architecture.md)
* v2 技术计划：[`docs/plan-v2.md`](../plan-v2.md)
* 标准插件架构：[`docs/standards-plugin-architecture.md`](../standards-plugin-architecture.md)
* Smart UI 插件化：[`docs/design/llm-smart-ui/pluginization.md`](../design/llm-smart-ui/pluginization.md)
* 权限模型：[`docs/design/security/permission-model.md`](../design/security/permission-model.md)
