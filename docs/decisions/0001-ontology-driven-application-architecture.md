# ADR-0001：本体驱动应用架构 —— 保留五层叙事，按“五面 + 三横切”实施

> 状态：**Accepted（P0）** · 日期：2026-11-06 · 决策者：平台架构（Lead） · 实施方：Platform Core / Smart UI / 前端 / 文档
>
> 关联分析：[`docs/analysis/ontology-driven-application-architecture.md`](../analysis/ontology-driven-application-architecture.md)（问题论证、逐项映射、路线与风险的证据基线）
>
> 关联契约索引：[`docs/analysis/contract-index.md`](../analysis/contract-index.md)（每个契约的作用、代码落点、状态与字段级摘要）
>
> 上游技术取舍：[`docs/plan-v2.md` §6 ADR 摘要](../plan-v2.md)（ADR-1…ADR-9，本 ADR 不推翻其中任何一条）
>
> 配套图：[`docs/analysis/ontology-driven-application-architecture.svg`](../analysis/ontology-driven-application-architecture.svg)（只表达现状成熟度，不作为优先级依据）
>
> 实施进展：见 §6（P1，2026-11-06；P2 首轮，2026-11-09；全量测试 1082 passed）

---

## 1. 背景

### 1.1 产品叙事：五层目标架构

对外叙事把平台描述为五层：

| 层 | 目标文本的表述 |
|---|---|
| 业务语义模型层 | Object / Attribute / Relation / State / Behavior / Rule / Event —— 定义业务世界 |
| 业务运行时层 | Object Runtime / Relation Engine / Query Engine / State Engine / Action Engine / Event Engine / Permission Engine —— 让语义可执行 |
| 应用表达与交互层 | Graph / Dashboard / Timeline / Digital Twin / Workflow / Form / Agent UI —— 定义业务如何被理解、观察和操作 |
| AI 应用规划层 | Intent Understanding / Application Planner / View Blueprint / Interaction Planning —— 把自然语言意图转换成应用结构 |
| 横向支撑能力 | 数据连接 / 语义查询 / 权限治理 / 可观测性 / 事件订阅 / 审计 / 组件协议 / 运行时扩展 |

这套叙事解决了“平台到底在做什么”的沟通问题，方向与现有实现同源（分析文档 §2、§5.1）。

### 1.2 实现现实：Kernel 是引擎，Platform 是整车

* **Kernel** 只回答五个问题：`describe`（世界长什么样）、`get`（对象是什么）、`find`（找出对象）、`related`（对象之间有什么关系）、`invoke`（做一件事）。
* **Platform** 承担其余一切：建模、发布、数据接入、能力沉淀、应用组装、语义检索、Agent 编排与治理，并通过**防腐败层**把这一切编译成 Kernel 的语言。
* 代码分层为 `interfaces → application → domain → ports → infrastructure`；运行时内核只能经 [`RuntimeGateway`](../../src/onto_platform/ports/runtime.py) 访问，且只有 [`kernel_gateway.py`](../../src/onto_platform/infrastructure/runtime/kernel_gateway.py) 一处 import 内核包 `ontology_runtime`（Kernel）。
* v0.3 已具备的实现深度（见分析文档 §4）：本体编译、能力契约与执行账本、确定性 Smart UI 编译与回放、Release/Deployment/Canary/Rollback、PDP/PEP、outbox/重试/死信、投影与对账、SLO/controller、插件化标准适配（P0 已落地）。

### 1.3 分析结论（作为本 ADR 的输入）

分析文档 [`ontology-driven-application-architecture.md`](../analysis/ontology-driven-application-architecture.md) 的三条总体判断（§0.2）是：

1. **五层叙事合理，但实施规格不足**：层内混合了“声明”与“执行”、“编译”与“查询”、“策略”与“决策”，照抄会得到两套实现。
2. **现有实现比目标描述更深**：确定性编译、裁决、发布治理、可观测性这些“生产分水岭”能力，目标文本反而没有写。
3. **正确做法是“叙事 + 缺口清单”，不是“上位替代”**（§5.3）：保留 Kernel/Platform 边界，用目标架构补齐语义层、表达层与生成面的缺口；按目标架构重写会丢掉真正的护城河。

### 1.4 本 ADR 要解决的问题

P1 即将新增 Rule / Event / ApplicationSpec 等一等契约。如果不先把边界写死，会出现三类典型错误：

* **声明被当成执行**：规则、事件、应用被写成“运行时的另一个引擎”，与 Kernel 或既有 use case 重复；
* **表达面被当成运行时**：视图/交互对象开始持有执行逻辑与业务语义，前端成为第二事实来源；
* **生成面被当成请求链路**：规划结果直接写状态或发动作，绕过裁决、编译与发布治理。

因此本 ADR 只做一件事：**把“五层叙事”翻译成可检查的实施边界，并声明本轮增量契约的归属。**

---

## 2. 决策

### 2.1 决策一：保留五层叙事（对外），实施按“五面 + 三横切”组织（对内）

五层叙事继续用于产品沟通、文档首页与对外介绍；代码与任务卡一律使用“面”的划分，避免一个能力挂在两层。

| 目标层 | 实施面 | 该面回答的问题 | 主要代码落点 |
|---|---|---|---|
| 业务语义模型层 | **语义面** | 世界里有什么、允许什么、发生什么 | [`domain/ontology`](../../src/onto_platform/domain/ontology)、[`domain/capability`](../../src/onto_platform/domain/capability)、`domain/rule`（本轮新增）、`domain/event`（本轮新增） |
| 业务运行时层 | **运行面** | 如何执行：Kernel 原语 + 平台引擎 | [`ports/runtime.py`](../../src/onto_platform/ports/runtime.py)、[`infrastructure/runtime`](../../src/onto_platform/infrastructure/runtime)、`application/*_usecases.py` |
| 应用表达与交互层 | **应用编排面** + **体验面** | 应用由什么组成；“看”与“做”的契约长什么样 | [`domain/application/model.py`](../../src/onto_platform/domain/application/model.py)、[`domain/application/spec.py`](../../src/onto_platform/domain/application/spec.py)、[`application/smartui`](../../src/onto_platform/application/smartui)、`frontend/` |
| AI 应用规划层 | **生成面（设计时）** | 如何把意图变成候选应用结构 | [`application/smartui/bridge.py`](../../src/onto_platform/application/smartui/bridge.py)、[`generators.py`](../../src/onto_platform/application/smartui/generators.py)、[`adjudication.py`](../../src/onto_platform/application/smartui/adjudication.py)、[`studio.py`](../../src/onto_platform/application/smartui/studio.py) |
| 横向支撑能力 | **治理面 / 数据面 / 扩展面** | 生产化：租户、发布、权限、审计、可观测；数据接入与投影；组件与插件扩展 | [`application/security`](../../src/onto_platform/application/security)、[`application/releases.py`](../../src/onto_platform/application/releases.py)、[`domain/data`](../../src/onto_platform/domain/data)、[`semantic`](../../src/onto_platform/semantic) |

**“五面”**：语义面 / 运行面 / 应用编排面 / 体验面 / 生成面。
**“三横切”**：治理面 / 数据面 / 扩展面。横切面不是“杂物层”，每项能力必须有 owner、Port、生命周期与失败模式（分析文档 §3.5）。

**单一 owner 规则**：一个契约只能有一个 owner 面和一个实现落点；跨面能力通过 Port/契约组合，不允许两个面各实现一套。本轮 owner 分配见 §2.4 与契约索引。

### 2.2 决策二：生成面是设计时管线，不在请求链路上

生成面回答“用户提出需求 → 生成/变更应用”这一设计时问题，**不参与每一次业务请求**。

**生成面链路（design-time）**

```text
Intent（意图/澄清）→ Context（L0–L2 + focus）→ Plan（候选 Blueprint / ApplicationSpec 草案）
→ Adjudicate（语义/权限/能力/布局裁决）→ Compile（确定性编译）→ Preview（沙箱预览）
→ Publish（人工确认 + Release/Deployment）→ Replay/Eval（回放、评估、回退）
```

**请求链路（run-time）**

```text
UI / Agent → REST API（interfaces/api.py）→ application use case → RuntimeGateway → Kernel
```

两条链路只有“已发布产物”这一个交接点：生成面产出候选契约与编译产物，经发布治理后进入运行面；运行面不依赖生成面是否可用。约束条目：

* **R1** 模型输出只能作为候选（Blueprint / spec 草案），不得直接调用 `RuntimeGateway`、不得写语义状态、不得绕过 `Publish`。
* **R2** 生成面必须保留“确定性约束优先、模型提议其次”：确定性规则/关键词规划始终可用，LLM 只是可选提议器；模型不可用时有兜底（现有规则模式）。
* **R3** 每次生成必须可回放：产物携带 `program_hash` 与输入快照，支持用历史 Blueprint 重编译（Smart UI 已实现，后续契约沿用）。
* **R4** 生成面不得成为业务请求的必需依赖；`dependencies = []` 与“不 import Kernel”约束不变。
* **R5** Application Planner 的范围以 `ApplicationSpec` 契约为边界（视图/查询/交互/Agent/能力引用），但不要求 P1 一步到位实现全量规划。

### 2.3 决策三：三个边界（本 ADR 的核心约束）

三个边界是本 ADR 的验收对象；每个边界给出“允许 / 禁止 / 代码判据”。

#### Boundary-1：声明 vs 执行

* **定义**：语义面只**声明**世界结构与行为契约（Ontology / Capability / Rule / Event），运行面负责**执行**；执行只能经 `RuntimeGateway` 或既有平台 use case。
* **允许**：`domain/` 里的 dataclass + 校验函数 + 纯函数求值器（如规则 AST 求值，只读对象、只产出 violation）。
* **禁止**：在声明对象里直接持有仓储/网关/内核引用；在 `domain/` 内写状态；把“引擎”写成可绕过 use case 的第二条执行通道。
* **代码判据**：`src/onto_platform/domain/**` 不得 import `application` / `interfaces` / `infrastructure` / kernel；写语义状态只能出现在 use case 或 gateway 实现中。
* **落地**：Rule/Event 声明放语义面，规则求值是纯函数；事件发布复用平台 outbox（`EventLog`），不新开投递通道。

#### Boundary-2：应用组合 vs 表达面

* **定义**：`ApplicationSpec` 是**组合契约**（引用 views / queries / interactions / agents / capabilities），`ViewSpec` / `InteractionSpec` 是**表达契约**（怎么观察、能做什么）；前端与渲染器只是消费者，不持有业务语义。
* **允许**：`ApplicationSpec` 引用 `InteractionSpec.capability_id`；`ViewSpec` 声明数据绑定、布局、权限裁剪；扩展面通过组件协议（`PluginManifest` / renderer descriptor）注册新视图类型。
* **禁止**：在 `ViewSpec` / `InteractionSpec` 中内联执行逻辑或复制能力实现；让生成出的 UI 程序成为业务事实来源；前端绕过 API 直接访问 Kernel。
* **代码判据**：`domain/application/spec.py` 只依赖 `domain.application.model` 与标准库；`InteractionSpec` 只有引用（`capability_id`）没有执行；interactions 是追加式字段，`Application` 旧行默认 `[]`。
* **落地**：本轮 `GetApplicationSpec` / `AddInteraction` / `RemoveInteraction` 都在 use case 层，REST 只做薄投影（接线见任务卡 task-21）。

#### Boundary-3：生成 vs 运行/发布

* **定义**：生成面产出“候选”，运行/发布面持有“真值”；候选必须经裁决 → 编译 → 预览 → 发布才能生效。
* **允许**：生成面读取快照、产出 Blueprint / spec 草案 / 编译程序、写 Studio 草稿与审计。
* **禁止**：生成代码直接 `publish_schema` 或调用 `invoke`；把草稿当成已发布产物；发布与回退绕过 `Release` / `Deployment` 指针。
* **代码判据**：只有 [`application/releases.py`](../../src/onto_platform/application/releases.py) 与 bootstrap 的发布链路可以移动 `deployment` 指针；`application/smartui/**` 不 import `infrastructure.runtime`；`interfaces/api.py` 不出现业务规则求值/编译逻辑。
* **落地**：Smart UI 现有 L4 编译 + `program_hash` + 回放是这条边界的既有实现，新增契约沿用同一模式。

### 2.4 决策四：增量实施，不重写 Kernel

**保留清单**（本轮明确“不动”，理由见分析文档 §5.2、§9.2）：

| 既有能力 | 决策 | 说明 |
|---|---|---|
| Kernel 五原语与 `RuntimeGateway` 防腐败层 | **保留，不碰** | 平台不 import Kernel；只有 gateway 一处适配 |
| `OntologyCompiler` 确定性编译 + `schema_fingerprint` | **保留** | Release 指纹与回滚的基础 |
| Smart UI L0–L5 管线（裁决 / 编译 / 沙箱 / 规则兜底 / 回放） | **保留** | 现有实现比目标描述更完整，只增契约不替换 |
| `Release` / `Deployment` / Canary / Rollback | **保留** | 生成面产物进入运行面的唯一通道 |
| 审计 / outbox / 幂等 / 执行账本 / 对账 / controller / SLO | **保留** | 可靠性护城河，新契约必须复用 |
| PDP/PEP + 角色 + ReBAC/ABAC | **保留** | `PolicyDecision` 是权限唯一判据 |
| 插件化标准适配（`semantic/spi.py` + `PluginRegistry`，P0 已完成） | **保留，扩展不改协议** | 扩展面范例：manifest + registry + 502 失败类 |
| 多租户 / 工作空间边界 | **保留** | 新契约一律带 `workspace_id` |

**本轮新增契约（P1）**：只做“声明 + 确定性求值/投影 + 审计”，不做通用规则语言、不做外部投递、不做前端重写。

| 新契约 | owner 面 | 实现落点（以代码为准） | 与既有契约的关系 |
|---|---|---|---|
| `RuleContract` | 语义面（声明）→ 运行面（求值） | `domain/rule/{model,engine}.py`、`application/rule_usecases.py` | 复用 `$id`/`$type` 与对象 properties；审计复用 outbox |
| `EventContract` | 语义面（声明）→ 数据面（订阅） | `domain/event/model.py`、`application/event_usecases.py`、扩展 [`application/events.py`](../../src/onto_platform/application/events.py) | 复用 `EventLog`/`Dispatcher`/`EVENT_CATALOG`，不新开 outbox |
| `ApplicationSpec` | 应用编排面 | [`domain/application/spec.py`](../../src/onto_platform/domain/application/spec.py)、`GetApplicationSpec` | 是 `Application` 聚合的**只读投影**，不替换现有 API |
| `ViewSpec` | 体验面（观察） | 同上 | 兼容 P0 `VIEW_KINDS`；新增 graph/dashboard/timeline/spatial 面 |
| `InteractionSpec` | 体验面（交互） | 同上 + `Application.interactions`（追加式） | 只引用 `capability_id`/workflow/agent，不执行 |

字段级摘要、校验入口与状态标记统一登记在 [`contract-index.md`](../analysis/contract-index.md)，本 ADR 不重复字段清单。

**实施节奏**：先声明（domain 契约 + 校验）→ 后执行（纯函数引擎 / use case）→ 再治理（审计、发布、API 投影）。任何一步都不允许倒置为“先做执行、再补声明”。

### 2.5 canonical 契约集合

以下契约是平台级 canonical（canonical = 唯一落点 + 单一 owner + 可校验 + 有测试），其余契约与之组合：

`OntologySchema`（编译产物）、`CapabilityContract`、`RuleContract`、`EventContract`、`RuntimeGateway`、`ApplicationSpec` / `ViewSpec` / `InteractionSpec`、`Blueprint / CompiledProgram / BuildManifest`、`Release / Deployment`、`PolicyDecision`、`ComponentManifest`（及标准适配 SPI 的 `AdapterManifest`）。

变更规则：

* 新增契约必须先登记契约索引，再写代码；
* 破坏性变更需要新 ADR（追加编号），不得静默修改 canonical 字段语义；
* 生成面产物必须携带可回放标识（`program_hash` / 版本），否则不得发布。

### 2.6 与 plan-v2 ADR-1…9 的关系

`docs/plan-v2.md` §6 的 ADR-1…ADR-9 是**技术取舍**记录（不引入推理机、不做实体解析、事件不做溯源、LLM 只做慢回路提议者等）。本 ADR 是**架构叙事与边界**记录，两者互补、不冲突：

* 本 ADR 的 R2（确定性优先、模型提议其次）与 ADR-7 一致；
* 本 ADR 的 Event 契约只做声明 + outbox 复用，与 ADR-4（不做事件溯源）一致；
* 若未来出现冲突：安全/边界条款以本 ADR §2.3 为准，技术选型以 plan-v2 为准，裁决记录追加到 `docs/decisions/`（下一个编号）。

---

## 3. 后果

### 3.1 正面

* 对外五层叙事不变，对内边界可检查：新能力不会再“挂两层”或重复实现；
* Rule / Event / ApplicationSpec 第一次成为可声明、可校验、可审计的一等契约，补齐分析文档 §6.1 的三个 P1 缺口；
* 生成面与发布面职责清晰：AI 只能产出候选，发布只能走 Release/Deployment，回放与回退保持现有强度；
* 增量实施不动 Kernel、不新增依赖，v0.3 的护城河（确定性编译、发布治理、可靠性、权限）全部保留。

### 3.2 成本与风险

| 风险 | 表现 | 缓解 | 验证 |
|---|---|---|---|
| 过度抽象 | 为每个名词造 Engine，没人用 | P1 只做 5 个契约、只做 `constraint` 规则类；先高频后抽象 | 任务卡验收 + 契约索引“后续”栏 |
| 契约与实现漂移 | 文档字段与代码字段不一致 | 字段摘要以代码为准；契约索引与任务卡同源；Lead 复核 diff | D/Lead 对照 [contract-index.md](../analysis/contract-index.md) |
| 双写/重复实现 | Rule 求值既在 use case 又在 API；事件出现第二套投递 | Boundary-1/3 的代码判据；单一 owner；审计只经 `EventLog` | `grep` 判据 + 架构测试 |
| 生成面越权 | 规划器直接写状态/发动作 | Boundary-3 判据；发布只在 releases/bootstrap 链路 | API 用例测试 + 架构测试 |
| `ApplicationSpec` 取代现有 API | 前端/CLI 大改 | spec 是只读投影，`Application` API 不变；interactions 追加式 | 既有测试全绿 |

### 3.3 兼容与迁移

* `ApplicationSpec` / `ViewSpec` / `InteractionSpec` 是**新增只读契约**：现有 `Application` 的 views/queries/capabilities/agents 语义不变，`interactions` 为追加式字段（旧行读取默认 `[]`，round-trip 稳定）。
* `Rule` / `Event` 是新增 `domain` 包；`application/events.py` 仅做向后兼容扩展（新增可选的 `contract` 校验参数与订阅键），既有 `EVENT_CATALOG` 与调阅行为不变。
* 不新增运行时依赖（`dependencies = []`）、不改 Kernel 契约、不重写前端；新契约通过现有 REST/CLI 面暴露（接线见 task-21）。

---

## 4. 非目标（P1 明确不做）

| 非目标 | 原因 |
|---|---|
| 完整规则语言 / 通用表达式语言 / OWL 推理机 | 业务需要“可判定约束”，推理带来不可解释性；P1 只做声明式条件 AST（无代码执行） |
| 外部事件投递（webhook / 事件桥 / Schema Registry） | 只做内部 outbox 契约与进程内订阅，外呼治理（签名/重试/配额）未就绪，避免半成品接口 |
| Digital Twin 仿真（实时孪生、遥测、反事实） | 现实现是空间场景渲染；命名降级为 Spatial/3D Scene，只有“模型 + 实时状态 + 仿真”三者齐备才启用 Twin 一词 |
| 重写前端 / 远程组件加载 | 前端是渲染器；生成面契约先行，前端在后续轮次按契约接入 |
| 插件沙箱 / 进程隔离 / SBOM | 已有插件 host + 签名 + opt-in；进程隔离属扩展面 P2 |
| 多租户级数据脱敏、密钥托管 | P2 生产化范围，P1 只保持 `workspace_id`/`TenantContext` 边界 |
| 把 Capability 正式化为独立 `BehaviorContract` | P1 已用 `Capability.contract`（pre/post/effects/idempotent/compensate）承载；独立契约的收益在 P2 评估 |

---

## 5. 验收

### 5.1 本 ADR 交付验收（P0-A，本次）

- [x] `docs/decisions/0001-ontology-driven-application-architecture.md` 落盘，状态、日期、决策者、关联链接齐全；
- [x] 五层叙事 ↔ 五面映射表逐项可点到代码落点（分析文档 §4 已提供证据）；
- [x] §2.3 的三个边界都有“允许/禁止/代码判据”，可作为评审检查表；
- [x] 本轮 5 个新增契约在契约索引中登记（状态、落点、字段级摘要、验收入口）；
- [x] 与 plan-v2 ADR-1…9 的关系已说明，无冲突；
- [x] 文档内相对链接可解析、UTF-8、无未决占位。

### 5.2 P1 实施验收（Lead 已复核，2026-11-06）

- [x] `RuleContract`：创建/发布/求值/审计闭环，引擎为纯函数，缺失字段语义有测试（`domain/rule` + `rule_usecases`）；
- [x] `EventContract`：契约 + 订阅匹配 + `emit(contract=...)` 校验，`EVENT_CATALOG` 回归绿；
- [x] `ApplicationSpec` / `ViewSpec` / `InteractionSpec`：`from_application` 只读投影 + `diagnostics` 告警 + interactions 追加式；
- [x] 5 个契约均有唯一落点与 owner，契约索引无“第二实现”；
- [x] 模型输出不能绕过裁决/编译/发布（Boundary-3 检查表通过）；
- [x] 架构判据：`domain/` 无 application/infrastructure import；只有 `kernel_gateway.py` import `ontology_runtime`；`dependencies = []` 不变；
- [x] 全量测试通过且既有测试不改语义。

---

## 6. 实施进展（P1，2026-11-06）

P1 在本 ADR 的 §2.3 边界与 §2.4 保留清单内完成：只新增契约与接线，未改动 Kernel 五原语、`OntologyCompiler`、Smart UI L0–L5、`Release`/`Deployment`、PDP、outbox 与标准适配 SPI。

### 6.1 已落地模块

| 交付 | 模块 / 文件 | 说明 |
|---|---|---|
| Rule 契约与求值 | `domain/rule/model.py`、`domain/rule/engine.py` | `RuleSpec`/`RuleViolation`/`RuleEvaluation`；14 操作符闭集 AST；纯函数求值（缺失字段 = MISSING） |
| Rule 用例 | `application/rule_usecases.py` | Create/Update/Publish/Disable/Delete/List/Evaluate；审计 `rule.*` |
| Event 契约与订阅 | `domain/event/model.py`、`application/event_usecases.py` | `EventContract`/`EventSubscription`；契约校验、订阅匹配、emit |
| Outbox 扩展 | `application/events.py` | `emit(contract=...)` 契约校验、envelope 增 `contract`、`Dispatcher.subscribe_key`、`subscriptions_for_event` |
| ApplicationSpec / ViewSpec / InteractionSpec | `domain/application/spec.py`、`domain/application/model.py`（interactions 追加式）、`application/application_usecases.py` | 只读投影 + `diagnostics` + `{spec, diagnostics, valid}`；interactions 增删 |
| 组合根接线 | `bootstrap.py` | `Platform.rules`、`Platform.business_events`（与 `Platform.events` 共享同一 `EventLog`）、`ApplicationUseCases.get_spec/add_interaction/remove_interaction` |
| REST | `interfaces/api.py` | 14 个端点（见 6.2；明细见契约索引 §4） |

### 6.2 新增 REST 端点（14 个）

> 均挂载在 `/api/workspaces/{wid}`；权限在 use case 内检查（viewer/editor）；成功返回 200。

| 组 | 端点 |
|---|---|
| Rule（4） | GET/POST `/rules`；POST `/rules/{rid}/publish`；POST `/rules/evaluate` |
| Event 契约（3） | GET/POST `/event-contracts`；POST `/event-contracts/{name}/publish` |
| Event 订阅（3） | GET/POST `/event-subscriptions`；DELETE `/event-subscriptions/{sid}` |
| Event 发射（1） | POST `/events/emit` |
| Application（3） | GET `/applications/{aid}/spec`；POST `/applications/{aid}/interactions`；DELETE `/applications/{aid}/interactions/{iid}` |

### 6.3 测试证据（2026-11-06）

* 新增测试 227 个（pytest 收集数，含参数化展开）：`test_rule_engine.py`（85）、`test_rule_usecases.py`（43）、`test_event_contract.py`（46）、`test_event_subscriptions.py`（17）、`test_application_spec.py`（26）、`test_architecture_api.py`（4）、`test_platform_wiring.py`（6）；定向运行 → **227 passed**。
* 全量：`python -m pytest -q` → **1033 passed**（68.71s，0 failed，Lead 最终复核）。
* 边界复核（§2.3 判据）：`domain/` 无 application/infrastructure import；`ontology_runtime` 只在 `kernel_gateway.py`；`smartui/` 无 infrastructure import；`api.py` 无 `publish_schema`；`dependencies = []` 不变。

### 6.4 P2 未做（明确不在 P1）

* 生产化：租户/工作空间隔离的进程级强制、外部 webhook 投递、插件沙箱/进程隔离/SBOM、AI 生成评估闭环（成本/延迟/质量）、Digital Twin 仿真、跨 worker durable handler（当前 `Dispatcher` 订阅为进程内）。
* 上述各项保持 §4 非目标口径；技术取舍仍以 plan-v2 ADR-1…9 为准。

### 6.5 P2 首轮进展（2026-11-09，不改变 §2.3 边界）

| 能力 | 本轮落地 | 明确不做 |
|---|---|---|
| 应用发布/克隆 | 发布校验全部 interactions；clone 复制 `ui_artifact` 与 interactions | 历史数据回填 |
| 租户/工作空间 | `GovernedRepository` 跨 workspace 拒绝 + 真实回归；webhook 接受已发布业务事件 | 进程/数据库级隔离 |
| 事件多 worker | `EventLog.claim` CAS + 租约，scheduler/HTTP drain 使用稳定 worker id | handler 注册持久化、跨进程路由 |
| 规则规模 | 稳定分页 + 嵌套属性点路径 | 条件字段索引、跨对象规则 |
| 生成评估 | `/smart-ui/eval` 聚合（成功率/延迟/token/成本/浏览/基线 delta） | 可回放评测集与质量模型 |
| 插件 SBOM | `/smart-ui/sbom` 确定性 manifest 摘要，声明进程内边界 | 沙箱/进程隔离 |
| Digital Twin | 仅 `/rules/simulate` 单对象规则级 what-if | 实时状态/传播/完整仿真 |
| 外部投递 | 业务事件 webhook 签名投递回归 | 事件桥/Schema Registry |

测试：`python -m pytest -q` → **1082 passed**（2026-11-09，0 failed）。SBOM 是清单不是隔离证明，webhook 是投递通道不是事件桥；不得把本轮能力表述为生产级沙箱或完整 Digital Twin。

---

## 7. 参考

* 架构分析：[`docs/analysis/ontology-driven-application-architecture.md`](../analysis/ontology-driven-application-architecture.md)
* 契约索引：[`docs/analysis/contract-index.md`](../analysis/contract-index.md)
* v2 技术计划：[`docs/plan-v2.md`](../plan-v2.md)（§6 ADR 摘要）
* 标准插件架构：[`docs/standards-plugin-architecture.md`](../standards-plugin-architecture.md)
* Smart UI 规则驱动生成：[`docs/design/llm-smart-ui/rule-driven-generation.md`](../design/llm-smart-ui/rule-driven-generation.md)
* Smart UI 插件化：[`docs/design/llm-smart-ui/pluginization.md`](../design/llm-smart-ui/pluginization.md)
* 权限模型：[`docs/design/security/permission-model.md`](../design/security/permission-model.md)
* 对照表：README 的“Kernel 是引擎，Platform 是整车”与五条约束 [README.md](../../README.md)
