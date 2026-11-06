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

---

## 3. P1 新增契约（已落地）

> 字段级摘要已按最终代码核对（2026-11-06）；测试与 REST 证据见 §4 与 [`p1-implementation-status.md`](p1-implementation-status.md)。后续若调整字段，必须在同一次变更里同步本节。

### 3.1 `RuleContract`（task-18）

落点：`domain/rule/model.py`、`domain/rule/engine.py`、`application/rule_usecases.py`；状态：◆ P1 已落地（`test_rule_engine.py` 85、`test_rule_usecases.py` 43、REST 见 §4）。

```
RuleSpec: id, workspace_id, name, description, object_type, kind="constraint",
          severity(error|warning|info), condition{AST}, when{AST, 默认 {}}, message,
          priority(int=100), enabled(bool=True), lifecycle(draft|published|archived),
          version(int), created_at, updated_at
condition AST: {"field","op","value"} | {"all":[...]} | {"any":[...]} | {"not":{...}}
               field 支持 $id / $type / properties 名；op 支持 eq ne gt gte lt lte in not_in
               contains not_contains is_null not_null starts_with ends_with
RuleViolation: rule_id, rule_name, object_type, object_id, severity, message, field, detail(dict)
RuleEvaluation: passed, evaluated_rules(int), violations[dict], skipped(int)
```

* 常量（闭集）：`RULE_KINDS=("constraint",)`、`SEVERITIES`、`RULE_LIFECYCLES=("draft","published","archived")`、`OPERATORS`（14 个）、`LOGICAL_KEYS=("all","any","not")`、`SPECIAL_FIELDS=("$id","$type")`、`MAX_CONDITION_DEPTH=32`；AST 不可表达任意代码。
* 求值语义：纯函数、零依赖；按 `(priority, name, id)` 排序；只有 `severity="error"` 的 violation 让 `passed=False`；**缺失字段 = MISSING**（`is_null`/`not_null` 可判定，其它比较一律 False，不抛异常）。
* 校验入口：`validate_condition` / `validate_rule_definition(rule, object_spec)`；引擎侧 `ensure_valid_condition` 在非法 AST 时抛 `ValidationError`。
* 用例（`rule_usecases.py`）：Create/Update（draft only + 乐观锁）/Publish（重复发布幂等）/Disable/Delete（draft 删除、published 归档）/List/Evaluate；Evaluate 默认只取 published+enabled，返回 `passed/object_count/rule_count/violations/missing/by_severity/evaluated_rules/skipped`，审计 `rule.evaluate`。
* REST（已实现）：`GET/POST /rules`、`POST /rules/{rid}/publish`、`POST /rules/evaluate`（详见 §4）。

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

## 4. REST 端点（P1 已实现，14 个）

> 路径均挂载在 `/api/workspaces/{wid}` 下，`user` 由 API 鉴权注入；权限在 use case 内检查（viewer/editor）。成功统一返回 200；错误沿用既有 `_error` 映射（400/403/404/409/502 等）。实现见 [`interfaces/api.py`](../../src/onto_platform/interfaces/api.py)，契约测试见 `tests/test_architecture_api.py`。

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

路由消歧：`/rules/evaluate` 是字面路由，优先于 `/rules/{rid}/publish`；`event-contracts/{name}/publish` 支持带点号的事件名（如 `traffic.vehicle_ping`）；`events/emit` 与 `event-subscriptions` 分属“事件发布”和“订阅管理”两类资源，互不捕获。

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
