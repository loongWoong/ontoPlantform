# 领域建模（DDD）评审：OntoPlatform v0.1

> **评审对象**：`F:\piAgent\ontoPlantform`（平台，Python ~6.8k 行 + React 控制台）与 `F:\piAgent\ontoRK`（语义运行时内核，~2.5k 行），当前工作树。
> **评审视角**：Eric Evans / Vernon 的 DDD 工具箱——限界上下文与上下文映射、通用语言、子域、聚合与一致性边界、不变量与规约、实体/值对象、领域事件、领域服务、仓储与工作单元、生命周期建模、防腐层。
> **路径缩写**：`[P]` = `ontoPlantform/src/onto_platform/`，`[K]` = `ontoRK/src/ontology_runtime/`，`[T]` = `ontoPlantform/tests/`，`[D]` = `ontoPlantform/docs/`。行号对应当前工作树。
> **与既有文档的分工**：`[D]control-theory-review.md` 已从控制回路角度诊断"时间/门限/回执/验证"四断点（§2.2, §3.2, §3.4, §5.3）。本文**不复述**这些结论，只补充领域建模维度的判断；凡与控制论结论重合处只做引用。`[D]completeness-review.md` 度量"design.txt 契约覆盖度"，也不是本文口径。
> **判定口径**：每条结论标注【v0.1 有意简化】或【领域建模缺陷】；推断标注【推断】。

---

## 0. 必答问题速览

| # | 问题 | 结论 | 详见 |
|---|---|---|---|
| 1 | 真正的限界上下文边界在哪 | 3 个：语义运行时（Kernel）、世界定义（平台，含 ontology/data/capability/application 四模块）、执行与治理（含智能体适配）。七个 `domain/*` 目录是**模块**不是上下文 | A1 |
| 2 | Kernel 是独立上下文还是共享内核 | **语义上是独立上下文，工程上是共享内核**：平台编译产物就是 kernel schema 格式，类型 1:1 同构；ACL 只管 import 不管模型 | A1、B8 |
| 3 | 哪些对象应成为聚合/聚合根 | 已有正确的：Ontology、Workspace、DataSource、Projection、Capability、Application。缺的聚合：`Release`、`Deployment`、`ProjectionRun`、`CapabilityInvocation`、`Membership`；Kernel 侧缺 `Object` 聚合版本 | B1 |
| 4 | 一次 invoke 的一致性边界是否合理 | **命令粒度合理、隔离级别不合理**：单批原子提交是对的，但跨对象读-改-写无版本保护、事件不同事务、长副作用在事务内 | B1 |
| 5 | 哪些业务规则应上移为声明式不变量 | `cardinality`、对象状态枚举/状态机、属性范围/唯一/必填、发布前置条件、聚合版本前置条件。现状全部在 Python 函数或应用用例里 | B2 |
| 6 | Change 能否称领域事件 | **不能**。它是"状态差分记录"，无 id/type/version/时间/因果/契约；只能算事件通知的原料 | B4 |
| 7 | application 层是否贫血 | **不是贫血而是"失血 + 泄漏"**：Ontology/AccessPolicy 有行为，但发布/版本/跨上下文校验等决策落在用例，且用裸 dict 穿过层边界 | B5 |
| 8 | Capability 是领域服务还是应用服务 | **是聚合根**；"执行"才是领域服务，而该领域服务当前缺位——`InvokeCapability` 只是应用服务壳，Agent 还绕过了它 | B5 |
| 9 | 平台与内核两套持久化是否违背 UoW | **是**。同一次发布有 3 个独立提交点，没有跨库事务也没有显式 Saga/Outbox；UoW 只能在单上下文内成立，跨上下文必须建模为最终一致 | B6 |
| 10 | 发布不可变/不可回滚是否破坏生命周期建模 | 不可变是**好**建模；坏的是把"状态迁移 + 制品生成 + 运行时安装"揉成一个 `publish`，且没有 Release 可回指，于是"版本演进"而不是"回滚"先被破坏 | B7 |

---

## A. 战略设计

### A1 限界上下文：七个目录不是七个上下文

**证据（跨"上下文"直接读别的上下文的仓储文档）**：
- capability 创建时直接读 ontology 文档校验目标类型：`[P]application/capability_usecases.py:48-52`；
- projection 创建时读 ontology 文档校验映射：`[P]application/data_usecases.py:86-98`；
- application 建视图/查询时读 ontology 文档：`[P]application/application_usecases.py:38-44`；
- 所有用例用 `ontologies[0]` 取"这个世界"：`[P]application/ontology_usecases.py:36`、`[P]application/common.py:115`、`[P]application/data_usecases.py:119-120`、`[P]application/intelligence_usecases.py:13-16`；
- 领域层内部也互引：`[P]domain/ontology/compiler.py:19,74-78` 直接 import `Capability` 并做目标类型校验；`[P]domain/intelligence/model.py:15` 依赖 Ontology 模型。

这四处形态说明：**ontology/data/capability/application/intelligence 共享同一份数据库、同一个进程、同一个"世界"聚合图**，它们是同一上下文内的模块（module），不是 DDD 意义上"有自己的语言、自己的模型、可独立演进"的上下文。仓储实现印证这一点：所有"上下文"的资产都存在同一张 `documents(kind, workspace_id, entity_id)` 表里，`kind` 是字符串：`[P]infrastructure/persistence/sqlite_repo.py:14-31,41-47`。

**建议的上下文边界（3+1）**：

| 边界 | 拥有什么 | 语言 | 判定依据 |
|---|---|---|---|
| **语义运行时（Kernel）** | 运行时 schema、对象实例、关系实例、函数与绑定、变更提交 | describe/get/find/related/invoke/Change | 独立包、独立 HTTP/MCP/CLI 适配器、独立 store：`[K]adapters/http.py`、`[K]infrastructure/sqlite.py:25-40`；design.txt:1745-1793 声明 Kernel 不认识 Platform |
| **世界定义（Platform 核心）** | Ontology + Vocabulary、DataSource/Mapping/Projection、Capability 定义、Application 组装、SchemaPublisher/Compiler | 对象类型/关系/术语/投影/能力/应用/发布 | 编译产物是唯一跨边界物；design.txt:1265-1317（§35）本身就是一张单世界模型图 |
| **执行与治理（Platform 运行时侧）** | 调用（Invoke/Agent/Workflow）、审计、变更消费、授权 | 动作、执行记录、审计、变更流 | 与控制论"执行器/反馈"同构；`[P]application/capability_usecases.py:106-131`、`[P]application/agent_runner.py` |
| （适配）智能体/人机接口 | LLM 循环、控制台、CLI/MCP | 提示词、工具、视图 | 只应经由应用服务访问世界；现状 Agent 直连 gateway（见 B5） |

**Workspace 是边界还是权宜？** Workspace 作为隔离容器是**真边界**（design.txt:149-180 §4；`[P]infrastructure/runtime/provider.py:16-42` 一空间一 runtime）。但"一个 workspace 一个 ontology"是**权宜**，且用 `list[0]` 隐式表达（`[P]application/ontology_usecases.py:26-36`，注释自称 v0.1；`CreateOntology` 拒绝第二个：`:46-51`）。隐患：一旦引入多本体/版本并存，五处 `ontologies[0]` 会**静默选第一个**而不报错【推断：基于代码路径判断，未做多本体实验】（`[P]application/capability_usecases.py:48`、`data_usecases.py:119`、`application_usecases.py:40`、`intelligence_usecases.py:13`、`common.py:115`）。修法：在 `Workspace` 上显式持有 `ontology_id`（一处），而不是"查列表取第一条"（五处）。

**Kernel 是独立上下文还是共享内核？** 结论：**目标上独立（design 原则二），实现上是 Shared Kernel，平台是 Conformist**。证据：
1. 平台类型系统与内核 1:1 对齐，注释明说 "aligned 1:1 with the kernel's TypeDef kinds"：`[P]domain/ontology/model.py:19-30`；
2. 编译产物不是平台自己的语言，而是 kernel `parse_schema` 的 dict 形状（`[P]domain/ontology/compiler.py:28-63`，`ontology/compiler.py:41-56` 把属性类型原样透传）；
3. 网关 DTO 与内核类型同构：`ObjectUpsert`=kernel `Object`、`SemanticDelta`=Mutation 批、`InvocationResult`=FunctionResult、`SchemaPayload`=schema+bindings：`[P]ports/runtime.py:29-97` 对照 `[K]core/schema.py`、`[K]core/execution.py`；
4. 网关直接调用内核 **store 的 bootstrap 通道** 创建对象、绕过 Runtime 的写模型：`[P]infrastructure/runtime/kernel_gateway.py:14-19,341-353`（`store.seed`）；
5. 校验规则复制了一份：平台 `_validate_edge`（`kernel_gateway.py:359-377`）与内核 `_validate_mutation`（`[K]runtime/runtime.py:246-263`）各写一遍关系端点规则。

**为什么这是缺陷而不是"实现细节"**：Shared Kernel 的代价是**所有权与变更流程必须双方共管**。现在 Kernel 有独立 README、独立版本（`[K]__init__.py`）、独立发布节奏，而平台把 kernel schema 当自己的编译目标——任何一方改 schema 语义都会无声破坏另一方，却没有契约测试（唯一的"契约测试"是编译产物能被 `parse_schema` 接受：`[T]test_ontology_compiler.py:49-59`，只验形状不验语义）。选择只有两个：显式承认 Shared Kernel（版本化契约 + 双方 CI 测试 + 变更评审），或做真 ACL（平台自有语义模型 + 显式映射 + 失败语义翻译，见 B8）。

### A2 通用语言：术语与代码命名

| 业务概念 | 代码里的名字 | 不一致之处 |
|---|---|---|
| 对象类型 | 平台 `ObjectTypeSpec`（`[P]domain/ontology/model.py:54`）/ 内核 `ObjectType`（`[K]core/schema.py:25`）/ 已发布进运行时 | 一个概念三个模型、三处名字，且 `Ontology.objects` 与 kernel `schema.objects` 同名不同义 |
| 对象实例 | `Object`（`[K]core/state.py:14`）；平台用裸 dict `{"type","id","properties"}`（`kernel_gateway.py:213-214`） | 平台侧没有"对象实例"模型，只有 dict + `"Type/Id"` 字符串 |
| 能力 | `Capability`（定义）/ kernel 函数名 `{Type}.{key}`（`[P]domain/capability/model.py:105-108`）/ Workflow / MCP Tool | 用户 CLI 直接 `capability invoke Vehicle.recharge`（README:94），把"能力"和"函数名"混称 |
| 变更 | `Mutation`（待提交）/ `Change`（已应用差分）/ 平台 `_changes` 批次（`kernel_gateway.py:112-119,401-406`）/ 审计 `AuditEntry` | 四种"变化"概念，没有一个有业务语义类型 |
| 应用 | `domain/application/`（产品显示为 user-facing App）与 `application/`（DDD 应用层） | **两个同名目录表达两个完全不同的东西**（`[P]domain/application/model.py` vs `[P]application/`），对人、对 AI 都是歧义 |
| 投影 | `Projection`（配方）/ `Mapping`（VO）/ UI 的"同步/运行" | 没有 `ProjectionRun` 概念（`[P]application/data_usecases.py:105-155` 只返回计数） |

**Vocabulary 只服务 LLM，没有进入领域层**：`VocabularyTerm` 只被 `SemanticIndex` 消费（`[P]domain/intelligence/model.py:18-49`），用于 Ask 的术语解析（`[P]application/intelligence_usecases.py:38-53`）与 Agent 提示词；它不参与发布校验（`[P]domain/ontology/validation.py:42-46` 只校验 concept 存在）、不参与授权、不参与查询。也就是说"业务语言"仍停留在提示词层，没有成为领域语言。【领域建模缺陷：通用语言被降级为 LLM 语料】

### A3 子域划分与投入匹配

| 子域类型 | 内容 | 代码体量证据 | 判断 |
|---|---|---|---|
| 核心域 | 本体建模 + 语义执行（ObjectType/Relation/Compiler/Kernel runtime） | `[P]domain/ontology/` 263+134+48 行；`[K]runtime/runtime.py` 327 行 | 质量最好，但"发布"这一核心动作在应用层（`[P]application/ontology_usecases.py:154-249`） |
| 支撑域 | 数据接入（Projection/Mapping）、能力编排（Capability/Workflow/Agent） | `[P]domain/data/model.py` 235 行；`[P]domain/capability/model.py` 183 行 | 领域模型偏"数据结构"，行为很少 |
| 通用域 | 治理（Access/Lifecycle/Audit）、持久化、接口 | `[P]domain/governance/model.py` 119 行；`errors.py` 41 行 | 过度简化：四态生命周期 + 成员角色就是全部治理，无法承载演进与授权 |

投入错位的具体表现：治理（通用域）占 119 行，却要承载"版本、发布、回滚、权限、审计"五个关键概念；核心域最关键的"发布/版本"没有领域模型，被写成应用层的一个 75 行类（`SchemaPublisher`：`[P]application/ontology_usecases.py:176-249`）。【领域建模缺陷：核心域逻辑外溢到应用层】

---

## B. 战术设计

### B1 聚合与一致性边界

**应有的聚合**（√=已有且形态基本正确；△=已有但边界错；×=缺失）：

| 聚合根 | 内部实体/VO | 现状 | 证据 |
|---|---|---|---|
| Ontology（世界概念图） | ObjectTypeSpec、RelationSpec、PropertySpec(VO)、VocabularyTerm(VO) | √ 聚合内行为齐（唯一性/引用完整性/发布后不可编辑），但持久化整文档覆盖、无版本 | `[P]domain/ontology/model.py:139-223`；保存 `ontology_usecases.py:96-98` |
| Workspace | Membership(VO) | △ Membership 在聚合外（独立文档），且没有"至少一个 owner""不可移除最后 owner"不变量，也没有移除动作 | `[P]domain/workspace/model.py:12-20`；`workspace_usecases.py:26-35,71-85` |
| DataSource | DataAsset(VO) | △ `config/assets` 全是裸 dict，无按 kind 的多态 VO | `[P]domain/data/model.py:55-88` |
| Projection | ObjectMapping/RelationMapping/FieldMapping(VO) | √ 设计上明确 Mapping 是聚合内 VO（`model.py:8-10`），但运行结果（Run）未建模 | `[P]domain/data/model.py:178-219` |
| Capability | Implementation(VO)、CapabilityInput(VO) | △ Implementation 里带 `entrypoint` 文件路径（基础设施泄漏），steps 是裸 dict | `[P]domain/capability/model.py:42-69` |
| Application | View、QuerySpec、AgentRef、capabilityIds | △ Agent 是 `list[dict]`、能力是 `list[str]`，无完整性/存在性约束 | `[P]domain/application/model.py:102-117` |
| Kernel Object（语义聚合） | properties、version（缺）、关系边（现状全局表） | × 没有聚合边界：Object 只有 `ref+properties`，关系是全局两表；没有版本，无法做聚合级并发 | `[K]core/state.py:14-27`；`[K]infrastructure/sqlite.py:25-40` |
| Release / Deployment | Release(schema 快照+Bindings+hash)、Deployment(指针) | × 完全缺失，发布即安装（见 B7） | `[P]application/ontology_usecases.py:200-214` |
| ProjectionRun | watermark/rows/upserts/deletes/errors | × 只返回计数并写一条审计 | `[P]application/data_usecases.py:140-155` |
| CapabilityInvocation | execution_id/idempotency_key/三态结果/actor | × 内核 `execution_id` 不落库即弃 | `[K]runtime/runtime.py:177`；`[P]application/capability_usecases.py:115-131` |

**一次 invoke 的一致性边界是否合理？分三层回答：**

1. **命令粒度：合理。** "一个函数产生的所有 mutation 在函数返回后一次性提交"是正确的设计，也是内核最值得保留的决策：`[K]core/execution.py:1-8,77-86`；`[K]runtime/runtime.py:200-213,222-227`（先整批校验再 apply）；`[K]infrastructure/sqlite.py:119-130`（单事务）。测试也固化了"失败不留痕"：`[T]test_capability.py:46-67`、`[K]tests/test_runtime.py:178-222`。
2. **聚合/隔离级别：不合理。** 一次 invoke 会写多个对象与全局关系表（例：`[K]examples/vehicle/functions/pass_station.py:26-28` 同时改 Vehicle 属性并 link 关系；平台 workflow 的 `set_property` 也走独立 delta：`[P]infrastructure/execution/workflow_runner.py:98-117`），而对象没有版本号（`[K]core/state.py:14-27`），函数读到的是提交前快照（`execution.py:99-106`），提交是无条件覆盖（`[K]infrastructure/sqlite.py:150-172`）。**"原子"只保证全有或全无，不保证读集未被并发修改**——这是控制论 P0-2 的 DDD 侧面：缺的不是事务，是**聚合版本 + 乐观锁**。
3. **边界内的副作用：不合理。** 平台把"LLM 调用（最长 120s）/工作流（N 步，每步一个内核事务）/任意 Python"放在同一个调用边界里：`[P]infrastructure/execution/platform_executor.py:42-67`、`[P]application/agent_runner.py:81-119`；同时一把网关锁覆盖整个调用（`kernel_gateway.py:239-262`）。DDD 语言：这是**长事务 + 进程内锁**，不是聚合事务。

**跨聚合写入清单（应当被建模为流程而不是"顺便事务"）**：
- **工作流跨步**：每步 `invoke` 各自提交（`workflow_runner.py:59-69`），第 k 步失败前 k−1 步已生效，无补偿——这不是聚合事务，是 **Process Manager/Saga** 的缺位（与控制论 P1-10 同源，此处只给 DDD 定性）。
- **投影批量写入**：一个 delta 可跨多个对象类型、跨多个对象（`[P]application/data_usecases.py:163-201`），且网关用两次 store 调用（先 `seed` 建对象，再 `apply` 变更：`kernel_gateway.py:340-353`）——**契约声称原子（`[P]ports/runtime.py:144-145` "Apply one SemanticDelta atomically"），实现是两个独立提交**（`[K]infrastructure/sqlite.py:53-70` 与 `:119-130` 各自 `with self._conn`）。创建成功、变更失败即留下残对象（控制论 P0-6 的 DDD 表述：跨聚合批量写入 + 假 UoW）。

### B2 不变量：全部埋在代码里

**现状证据**：
- 关系基数只被"解析 + describe"，从不执行：`[K]core/schema.py:45`、`[K]loader/__init__.py:90-95`、`[K]runtime/runtime.py:78,118`；链接校验只看端点类型与存在性（`runtime.py:246-263`），没有基数检查。
- 对象状态没有枚举/状态机：示例本体里 `TollStation.status` 只是 `type: string`（`[K]examples/vehicle/ontology.yaml:17-23`），允许值 `"open"` 只出现在一个函数的 if 里（`pass_station.py:19-20`）。
- 业务规则只存在于可执行源码：`pass_station.py:19-24`（站点必须开、余额必须够）、`[P]examples/traffic/demo.py:31-37`（recharge 无任何校验，余额可为负）。
- 平台侧唯一"不变量"是编辑期的引用完整性（`[P]domain/ontology/model.py:157-214`）与发布期 `validate_ontology`（`[P]domain/ontology/validation.py:15-47`），且发布时只做一次、之后不再复检。
- `cardinality` 在平台上也只是字段：`[P]domain/ontology/model.py:87`，编译时透传（`[P]domain/ontology/compiler.py:50-56`）。

**应上移为声明式不变量的清单**：

| 规则 | 归属层级 | 现状 | 为什么必须上移 |
|---|---|---|---|
| 关系基数 `1:1/1:N/N:M` | Kernel schema 约束 + `_validate_mutation` 执行 | 只声明不执行 | 这是"世界结构"的约束，不是某个函数的私事；换一个函数就能绕过 |
| 对象属性枚举/状态机（`status ∈ {open,closed}`，迁移表） | Ontology 约束 → 编译进 schema → Kernel 执行 | 不存在 | 对象状态是领域本质属性；现在只活在 `pass_station.py` 的 if 里 |
| 属性范围/唯一/必填（`balance >= 0`、`plate` 唯一） | Ontology 约束 + 聚合不变量 | 不存在 | 业务规则，应该可查询、可校验、可测试 |
| 聚合版本前置条件（`expected_version`） | 聚合不变量 | 不存在 | 并发正确性的最小前提（B1） |
| 发布前置条件（无悬空引用、capability 目标存在、映射完整） | `Ontology.assert_publishable()` / `Release` 构造前置 | 散在 4 个用例 + compiler（`capability_usecases.py:44-52`、`data_usecases.py:82-98`、`application_usecases.py:38-44`） | 同一条规则四处重写，必有一处漏 |
| 关系端点存在性 | Kernel（已执行）| ✅ `[K]runtime/runtime.py:260-263` | 保留，但删除平台重复实现（`kernel_gateway.py:359-377`） |

**规约（Specification）候选**：`StationOpenSpec`、`SufficientBalanceSpec`、`RelationCardinalitySpec`、`PublishableOntologySpec`、`ActorMayWriteSpec`。它们应是**可命名、可组合、可单测**的一等对象，而不是散落的 `if` 与 `raise`。

### B3 实体 vs 值对象：所有属性都是裸标量/裸 dict

| 应该是值对象 | 现状 | 证据 | 后果 |
|---|---|---|---|
| `ObjectRef` | 平台侧是 `"Type/Id"` 字符串，解析推迟到网关 | `[P]ports/runtime.py:11-13`；`kernel_gateway.py:205-210` | 非法引用要到网关才报错；跨层反复分割字符串（`workflow_runner.py:103-105`、`application_usecases.py:195`） |
| 金额/数量（`Amount`） | `number` 裸值，无精度/币种/非负 | `demo.py:34-35` | 业务不变量无处安放（`balance` 可为负） |
| 时间/时间区间 | `datetime` 也是字符串，比较按字符串 | `[P]domain/ontology/model.py:22-30`；`[K]core/types.py:53-59` | 时间语义丢失（控制论 P2-1 的建模侧面） |
| 基数 `Cardinality` | `str` 自由文本 | `[P]domain/ontology/model.py:87`；`[K]core/schema.py:45` | 无法校验、无法执行（B2） |
| 数据源/资产配置 | `config: dict` | `[P]domain/data/model.py:35-37,59-63` | 连接器契约无法表达，只能靠运行时异常兜底 |
| 工作流步骤 | `steps: tuple[dict,...]` + `STEP_KINDS` 字符串常量 | `[P]domain/capability/model.py:29,49` | 步骤不是类型，模板/参数/别名全靠 dict 约定 |
| 实现绑定 | `Implementation.entrypoint: str | None`（文件路径） | `[P]domain/capability/model.py:48`；`capability_usecases.py:75-82` | **基础设施泄漏进领域层**：领域对象序列化后带文件系统路径，跨进程/跨机器即失效 |

【领域建模缺陷】这不是"以后再补类型"的洁癖问题：值对象是不变量的宿主。没有 `Amount` 就没有 `amount > 0` 的家；没有 `ObjectRef` 就没有"引用必须合法"的家；于是这些规则只能继续写在函数里（B2）。

### B4 Change 能否称领域事件？——不能

对照领域事件的最小契约：

| 领域事件要件 | `Change` 现状 | 证据 |
|---|---|---|
| 事件标识（id） | 无 | `[K]core/execution.py:37-49` |
| 事件类型/名称（语义） | 只有 `kind`（`set_property/link/unlink`），是**机制**不是语义（"充值成功"才是语义） | 同上 |
| 事件版本（schema 演进） | 无 | 同上 |
| 发生时刻 occurred_at | 无 | 同上 |
| 因果/关联（execution_id、command_id、actor） | 无；`execution_id` 在内核生成后不落库、不进 Change | `[K]runtime/runtime.py:177`；`[P]application/capability_usecases.py:120-124` 审计也不含 execution_id |
| 载荷契约 | `old/new` 裸值，无 DTO 定义、无 Publish/Subscribe 契约 | `[K]core/execution.py:41-48`；平台批次 `{seq, ts, source, changes}` 是自造信封：`kernel_gateway.py:401-406` |
| 与状态同事务 | 否：平台 `_record` 在锁外、仅当 `changes` 非空 | `kernel_gateway.py:255-256` |
| 失败/零变更也应有事件 | 否：失败不记，执行了但没变也不记 | 同上；`[K]core/execution.py:60-74` 的 `error` 不产生事件 |

**结论**：`Change` 是**状态差分记录（state diff）**，平台在其外包一层 `seq/ts/source` 批次日志，是"变更通知（event notification）的原料"，不是领域事件。可保留其作为底层事实，但要在平台层定义**有语义、有版本、有契约**的领域事件（如 `ObjectPropertyChangedV1`、`RelationLinkedV1`、`CapabilityPublishedV1`、`ProjectionRunCompletedV1`、`CapabilityInvocationFailedV1`），配事件目录 + envelope + 契约测试。**不建议上事件溯源**——与控制论 §5.5 一致：当前状态 + 变更日志足够，缺的是语义与契约，不是重放。

### B5 领域服务与应用服务：谁在做业务决策？

**application 层判定：失血 + 泄漏，而非典型贫血。**
- 有行为的部分：`Ontology` 聚合（`[P]domain/ontology/model.py:157-223`）、`AccessPolicy`（`[P]domain/governance/model.py:70-94`，纯决策对象，模式正确）、`transition` 状态机（`:24-43`）。
- 失血的部分：
  1. **发布领域服务错放在应用层**：`SchemaPublisher`（`[P]application/ontology_usecases.py:176-249`）做编译、发布、注册 Agent runner、持久化 payload、审计——这是"设计时→运行时"的核心领域动作，不是用例编排；
  2. **生命周期规则可绕过**：两次 `transition` 调用散在用例（`ontology_usecases.py:168-171`、`capability_usecases.py:84-88`、`application_usecases.py:165-169`），聚合没有 `publish()` 方法；任何新用例只要直接 `save({"lifecycle":"published"})` 就能跳过状态机【推断：`save` 不做任何状态校验，见 `[P]application/common.py:78-79`】；
  3. **跨上下文校验写在用例里**（A1 已列证据）；
  4. **裸 dict 穿过所有层边界**：用例返回 `to_dict()`，接口层直接当传输模型（`[P]interfaces/api.py:211-262` 全篇），领域对象没有"出域即 DTO"的边界。
- 结论：应用层既编排又决策，领域层承载数据结构与局部校验；这是**失血模型**（anemic-ish），不是纯贫血。

**Capability 是领域服务还是应用服务？** 定义侧是**聚合根**（有身份、生命周期、实现 VO）；执行侧本应是**领域服务**（`CapabilityInvocationService`：授权、幂等、前后置条件、事件），但现在：
- `InvokeCapability` 只是应用服务壳：`require(viewer)` + `gateway.invoke` + 写审计（`[P]application/capability_usecases.py:106-131`），没有前置/后置条件、没有幂等、没有验证；
- Agent 路径绕过它直接调 gateway（`[P]application/agent_runner.py:151-156`），因此 Agent 的写不经授权与统一审计；
- 应用的 Agent 入口以 `viewer` 权限运行（`[P]application/application_usecases.py:144`）——**Agent 事实上成了控制器，而不是"提议者"**（与控制论 §2.1 原则 1 的落点相反）。

### B6 仓储与工作单元：两套持久化，没有 UoW

**仓储不是按聚合组织的**：`Repository` 只有 `put/get/delete/list(kind, workspace_id, id)`（`[P]ports/storage.py:14-26`），实现是通用文档表 + `INSERT OR REPLACE`（`[P]infrastructure/persistence/sqlite_repo.py:14-31,41-47`），内存版同理（`memory_repo.py:15-17`）。所以：
- **没有聚合边界意识**：同一事务里想改 Ontology 与 Capability，就是两次独立的 `put`；
- **没有版本/CAS**：整文档覆盖，两个并发编辑器后写覆盖先写（`ontology_usecases.py:84-98` 的 读-改-写 序列 + `sqlite_repo.py:44`）；这是控制论 P0-2 的平台侧同构问题；
- **没有 UoW 抽象**：`ApplicationContext.save` 就是一次 `repo.put`（`[P]application/common.py:78-79`），没有 begin/commit/rollback，也没有"脏检查/事件收集"。

**平台与内核两套持久化违背 UoW 吗？——是，且是"假单事务"。**
- 两个独立数据库：平台定义库 `.onto/platform.db`（`[P]bootstrap.py:170-179`）与每 workspace 的内核状态库 `.onto/runtime/runtime_<wid>.db`（`[P]infrastructure/runtime/provider.py:28-37`；`[P]bootstrap.py:181-197` 让 runtime store 跟随平台 store）。
- **一次发布的提交点有 3 个**：① 改 lifecycle 并 `save` 平台库（`ontology_usecases.py:168-171`）→ ② `gateway.publish_schema`（只换内存中的 runtime，不落任何库：`kernel_gateway.py:136-171`）→ ③ 保存 `schema_payload` 到平台库（`ontology_usecases.py:208-214`）。任一环节失败都留下不一致（控制论 P1-8 已列），DDD 的解释是：**这是跨上下文的分布式一致性，被当成了本地 UoW 来写**。
- 正确建模：跨上下文一致性只能是"一个上下文内本地事务 + 出站事件/Outbox + 幂等消费"；发布应是一个 Process Manager（Saga）：创建 Release（本地事务）→ 调 Kernel publish（可重试）→ 记录 Deployment（本地事务）→ 失败补偿。而不是"先改状态再祈祷"。

### B7 生命周期与状态机：四态复用一切，发布不可回滚

**现状**：一套四态 `draft/validated/published/deprecated` + 四条迁移被**复用于 5 类资产**（Ontology/Capability/Application/Projection，及隐式的 Workspace）：`[P]domain/governance/model.py:21-29`；资产字段各带 `lifecycle: str = "draft"`（`domain/ontology/model.py:151`、`capability/model.py:101`、`application/model.py:115`、`data/model.py:188`）。`deprecate/revise` 在生产代码里从未被调用（只在测试 `[T]test_governance.py:19-33` 与错误文案 `ontology/model.py:222` 出现），`completeness-review` A2 也指出未暴露。【v0.1 有意简化：Review 折入 publish、deprecate/revise 未接 API（README:278-284）】

**但有三处不是"简化"，是建模缺陷**：

1. **一套状态机表达多种生命周期**。Ontology 需要"版本演进"（version 字段闲置：`[P]domain/ontology/model.py:147`，`completeness-review.md:78` 已承认）；Capability 需要"部署/生效"（design.txt:702-714 的 `Draft→Validate→Test→Publish→Executable`，Test 步骤未建属简化，但 "Executable" 状态在代码里不存在）；Application 只需要发布；Projection 需要"运行态"（没有 Run 状态）。四态一刀切的结果是：`published` 同时表示"冻结了定义""已编译""已安装进运行时"三件事。
2. **publish 把三件事揉在一起**：状态迁移 + 编译 + 运行时安装（`ontology_usecases.py:159-173`；`capability_usecases.py:64-94`）。所以"发布失败不回滚"不是事务写法问题，而是**没有 Release/Deployment 这两个领域概念**——没有"不可变制品"可命名，就没有"当前部署的是哪份制品"，也就没有真正的回滚对象。
3. **不可变用 if 实现**：`_ensure_editable` 只在 `upsert_*` 里抛错（`[P]domain/ontology/model.py:218-223`），对象本身仍是同一个可变实体；`schema_payload` 被覆盖写（`ontology_usecases.py:208-214`），没有历史，因此"回滚"无指向。

**建议的三层建模**：
```
Asset（Ontology/Capability/Projection/Application）  —— 设计态聚合，draft/validated 可编辑
   └── Release（不可变制品：schema 快照 + bindings + hash + 作者/时间）——可多份、可比较
          └── Deployment（指针：workspace → release_id，activated_at）——切换即部署，指回旧 Release 即回滚
```
这样"不可变"从 if 变成类型（Release 无 setter），"回滚"从"没有"变成"改指针"，"版本演进"从"新建工作空间"变成"新建 Release"。

### B8 防腐层：位置对，语义漏

`KernelRuntimeGateway` 确实是唯一 import Kernel 的模块（懒加载 `[P]infrastructure/runtime/kernel_gateway.py:38-90`；`[P]bootstrap.py` 不 import kernel），位置与 design.txt:1096-1130（§31）一致。但它**不是完整 ACL**：
- **模型同构而非映射**：DTO 与内核类型 1:1（A1 证据 2-3），映射代码退化为 `dict()`/`to_jsonable`（`kernel_gateway.py:213-214,254`）；
- **失败语义没有翻译**：错误被压成 `f"{type(result.error).__name__}: {result.error}"`（`kernel_gateway.py:251-253`），平台无法区分"业务拒绝/参数错/并发冲突/未知"；领域层也没有对应异常（`[P]errors.py:11-41` 只有 8 类泛化错误）；
- **泄漏方向双向**：内核概念（`Type/Id`、`ObjectUpsert`、`SemanticDelta`、`Mutation` 三原语）直接出现在平台端口与数据用例（`[P]ports/runtime.py:29-66`、`[P]application/data_usecases.py:140-143`、`workflow_runner.py:112-116`）；反向地，平台把内核 store 当 bootstrap 通道直接用（`kernel_gateway.py:341-353`），越过内核自己的 Runtime 写模型。

---

## C. 领域建模缺陷清单（P0/P1/P2）

> 定级口径：P0 = 会让聚合状态/事实出错，或让演进路径不可建模；P1 = 显著削弱可演进性/可验证性；P2 = 规模与卫生问题。

| ID | 现象与证据 | DDD 解释 | 后果 | 修法 |
|---|---|---|---|---|
| **P0-1** | 聚合持久化整文档覆盖、无版本：`[P]application/ontology_usecases.py:84-98`、`[P]infrastructure/persistence/sqlite_repo.py:44`、`memory_repo.py:15-17` | 缺聚合版本与乐观锁；仓储不是聚合仓储 | 并发编辑丢更新；无法判断"我改的是哪一版" | 聚合加 `version` + `put_if_version` + `ConcurrencyConflict`（DM-2） |
| **P0-2** | 发布路径 3 个提交点、无 UoW/Saga：`ontology_usecases.py:168-171,204-214`；`capability_usecases.py:84-94` | 跨上下文一致性被当成本地事务 | 半发布（定义已发布、运行时未装；或反之）；重启后自愈掩盖 | Release/Deployment + Outbox/Saga（DM-7、DM-8） |
| **P0-3** | 不变量在 Python 里：`[K]examples/vehicle/functions/pass_station.py:19-24`；`cardinality` 不执行（`[K]core/schema.py:45` vs `[K]runtime/runtime.py:246-263`）；对象状态无枚举（`ontology.yaml:17-23`） | 缺声明式不变量/规约 | 规则不可查询、不可证明、换函数即绕过；模型声明与现实不一致 | 约束语言 + 聚合不变量 + Specification（DM-3、DM-4） |
| **P0-4** | Change 无 id/type/version/时间/execution_id，失败与零变更不记录：`[K]core/execution.py:37-49`；`kernel_gateway.py:255-256,401-406` | 状态差分 ≠ 领域事件；无事件契约 | 无法消费语义事件；审计与变更流割裂；"完成"不可归因 | 事件目录 + envelope + 同事务 outbox（DM-5） |
| **P0-5** | 写授权缺失、Agent 成控制器：`capability_usecases.py:117`（viewer 可写）、`agent_runner.py:151-156`（绕过用例）、`application_usecases.py:144`（Agent 以 viewer 运行） | 授权策略不是领域对象，只是"成员等级检查"；角色/动作/目标未建模 | 越权写、不可追溯（控制论 P0-8 的建模根因） | `ActionAuthorization` 策略 + Agent 继承发起者（DM-10） |
| **P1-1** | "一 workspace 一 ontology" 用 `list[0]` 隐式表达：`ontology_usecases.py:26-36` + 5 处 `ontologies[0]` | 边界内的"单例聚合"没有显式引用 | 多本体/多版本一到，静默选第一个 | `Workspace.ontology_id` 显式化（DM-1 附带） |
| **P1-2** | 上下文间靠共享库文档互读：`capability_usecases.py:48-52`、`data_usecases.py:86-98`、`application_usecases.py:38-44` | 共享数据库集成，不是上下文映射 | 规则重复、耦合不可见、无法独立演进 | Published Language/契约端口 + 架构测试（DM-1） |
| **P1-3** | Capability 聚合混入基础设施：`Implementation.entrypoint` 路径（`capability/model.py:48`）、steps 裸 dict（`:49`） | 实体/值对象边界错；领域对象不可移植 | 序列化后跨机器失效；步骤无类型安全 | `ReleaseArtifact` 承载 entrypoint；`WorkflowStep` VO（DM-6、DM-9） |
| **P1-4** | 生命周期单表四态、版本闲置、deprecate/revise 死码：`governance/model.py:21-29`、`domain/ontology/model.py:147`、`completeness-review.md:41,78` | 生命周期与部署未分离，缺 Release | 只能新建工作空间演进；回滚无对象 | 三层建模（DM-7） |
| **P1-5** | Membership 独立文档、无 owner 不变量、无移除/转移：`workspace_usecases.py:71-85` | 聚合边界错（成员应是 Workspace 聚合内 VO/实体，或独立聚合 + 规约） | 无法保证"至少一个 owner"；成员治理不可操作（completeness A1） | Membership 聚合 + `LastOwnerSpec`（DM-10 附带） |
| **P1-6** | 平台侧引用/配置全是裸字符串与 dict：`[P]ports/runtime.py:11-13`、`domain/data/model.py:35-63` | 值对象缺失 | 校验推迟、错误信息远离源头 | `ObjectRef/Amount/TimeRange/Cardinality`（DM-9） |
| **P2-1** | 错误体系不表达领域概念：`[P]errors.py:11-41`（无 InvariantViolation/ConcurrencyConflict/Conflict） | 领域异常未建模 | 接口层无法按语义映射（只能 400/403/404/409 猜） | 错误分类学 + 领域异常（随 DM-2 落地） |
| **P2-2** | 术语表只服务 LLM：`[P]domain/intelligence/model.py:18-49`；不参与校验/授权/查询 | 通用语言未进入领域层 | 业务语言与代码语言持续漂移 | 术语表纳入发布校验与文档生成（DM-12） |
| **P2-3** | Application 对 Agent/Capability 是弱引用（`list[dict]`/`list[str]`）：`domain/application/model.py:110-114` | 引用完整性未建模 | 悬空引用在运行时才暴露 | `AgentRef/CapabilityRef` VO + 发布校验（DM-6） |

---

## D. v2 计划必须新增/修改的领域建模工作项

> 每项给出 test-first 形式的验收断言（给定…当…则…）。标 **前置** 的项是控制论 Phase 0 工作项（时间/幂等/限界）的建模前提，应优先合入。

| ID | 工作项 | 具体内容 | 验收断言（test-first） | 依赖 |
|---|---|---|---|---|
| **DM-1** | 上下文契约显式化（**前置**） | 写 `docs/architecture/context-map.md`（3 上下文 + Shared Kernel/Conformist 判定）；`OntologyCompiler` 移出 domain（它 import `ports.runtime` 与 `Capability`：`compiler.py:19,22`）；新增架构测试 | 给定平台源码树，当运行 `tests/test_architecture.py`，则除 `infrastructure/runtime/**` 外无模块 import `ontology_runtime`，且 `domain/**` 不 import `ports/**`；给定一份 Kernel schema 变更，则契约测试（编译产物 → `parse_schema` → 语义断言）在平台 CI 与 Kernel CI 同时跑 | — |
| **DM-2** | 聚合版本与聚合仓储（**前置**） | `Repository` 增 `get_versioned/put_if_version`；`documents` 表加 `version`；聚合基类带 `version`；保存即 CAS | 给定两个编辑器都基于 `version=3` 修改同一 Ontology，当 A 先保存、B 后保存，则 B 收到 `ConcurrencyConflict`（409），库中 version=4 且 A 的修改保留；给定一次成功保存，则 version 单调 +1 且旧版本号不可再写 | — |
| **DM-3** | 聚合不变量：发布前置条件 | `Ontology.assert_publishable()` 合并 `validate_ontology` + 引用完整性 + 映射完整性；`PublishOntology/PublishCapability` 只调用一次；删除用例里散落的重复校验 | 给定含悬空引用或空对象集的 Ontology，当调用 `publish`，则抛 `PublishableViolation` 且 `lifecycle` 仍为 `validated`；给定同一不变量，则单元测试只存在于聚合测试一处（用例测试通过 mock 断言委托） | DM-2 |
| **DM-4** | 声明式约束：关系基数与对象状态 | Ontology 支持 `cardinality`、属性 `enum/range/unique/required`、对象 `state_machine`；编译进 Kernel schema；`_validate_mutation`/`store` 执行 | 给定 `cardinality="1:1"` 且已有一条边，当 link 第二条，则返回 `CardinalityViolation` 且边未写入；给定 `TollStation.status ∈ {open,closed}`，当 `set_property(status="busy")`，则 `SchemaViolation` 且状态不变；给定示例 `pass_station` 改为声明式表达，删除函数内 if 后原测试仍通过 | DM-3 |
| **DM-5** | 领域事件与同事务 Outbox | `Change` 增 `event_id/type/version/occurred_at/execution_id/actor`；平台定义事件目录（5-8 个 V1 事件）+ envelope；变更日志与状态提交同事务；失败/零变更发 `*Failed/NoOp` 事件 | 给定一次成功 invoke，当读 `observe_changes`，则每条含 `event_id`、`type`、`version`、`occurred_at`、`execution_id`；给定一次失败 invoke，则变更流含 `CapabilityInvocationFailedV1` 且 `execution_id` 可回查审计；给定内核提交成功但写事件失败，则整个提交回滚（同事务） | DM-2 |
| **DM-6** | Capability 拆分与强引用 | `CapabilityDefinition`（聚合根）+ `ImplementationBinding`（VO）+ `ReleaseArtifact`（entrypoint/hash 移出领域 payload）+ `CapabilityInvocation`（幂等键/三态/actor）；Application 引用改为 `CapabilityRef/AgentRef` | 给定已发布 Capability，当序列化聚合，则 payload 不含文件系统路径，仅含 `artifact_id`；给定同一 `idempotency_key` 两次 invoke，则第二次返回首次结果且世界只变一次；给定删除被 Application 引用的 Capability，则被拒绝（`DanglingReference`） | DM-2、DM-7 |
| **DM-7** | Release / Deployment 三层生命周期 | 新增 `Release`（不可变：schema 快照+bindings+hash+作者+创建时间）与 `Deployment`（workspace→release_id, activated_at）；`publish`=创建 Release，`deploy`=切指针，`rollback`=指回调旧 Release | 给定 Release A 已部署、B 部署失败，当 `rollback`，则 Deployment 指回 A 且 `gateway.describe()` 返回 A 的 schema version；给定发布失败，则无 Release、lifecycle 不变、无半安装；给定同一 Release 并发 deploy，则只有一个成功 | DM-2、DM-8 |
| **DM-8** | 发布 UoW / Saga | `UnitOfWork` 端口（begin/commit/rollback + 聚合收集 + 事件收集）；发布路径：一个 UoW 内写 lifecycle+Release+outbox，commit 后 dispatcher 调 `publish_schema`，失败可重试 | 给定 `publish_schema` 抛错，当执行 `PublishOntology`，则平台库无 Release、无 lifecycle 变更、outbox 无已投递事件；给定重试成功，则恰好一条 Release 且 Deployment 指向它；给定进程在 dispatch 后崩溃，则重启后 outbox 重投且幂等 | DM-6、DM-7 |
| **DM-9** | 值对象落地 | `ObjectRef`（平台侧 VO）、`Amount`（精度/币种/非负）、`TimeRange`、`Cardinality`、`WorkflowStep`、`DataSourceConfig`；替换裸字符串/裸 dict | 给定非法 ref `"Vehicle"`，当构造平台 `ObjectRef`，则抛 `InvalidReference`（不再推迟到网关）；给定 `amount=1.005` 且声明精度 2，则按声明舍入/拒绝；给定含 `WorkflowStep` 的 Capability，当 `to_dict()`，则步骤字段经过步骤类型校验（非法 kind 在构造期报错） | DM-2 |
| **DM-10** | 授权策略领域化与 Agent 归位 | `ActionAuthorization` 领域服务/策略（actor, action, target, workspace）；写动作要求 `editor+`；Agent 以发起者身份走 `InvokeCapability`（写动作逐条审计 actor+execution_id）；Membership 聚合 + `LastOwnerSpec` | 给定 `viewer`，当调用写能力，则 403 且世界不变；给定 Agent 发起写，则审计含 `actor=发起者` 且经过 `InvokeCapability`（绕路路径无入口）；给定移除唯一 owner，则拒绝 | DM-6 |
| **DM-11** | ProjectionRun 与投影原子性 | 新增 `ProjectionRun`（watermark/rows/upserts/deletes/errors/耗时）；`SemanticDelta` 真正单事务（合并 `seed`+`apply` 为一次 store 调用）；source 读与 kernel 写之间显式分段 | 给定 delta 中一条 link 端点不存在，则同一 delta 的 create 也不落库（当前先 seed 提交：`kernel_gateway.py:341-353` + `sqlite.py:53-70`）；给定一次运行，则存在可查询的 ProjectionRun（当前只有 count+audit：`data_usecases.py:147-155`） | DM-2 |
| **DM-12** | 通用语言对齐 | 术语表（12 个核心词）与代码命名对齐；`domain/application` 改名 `domain/product`（消除与 application 层同名）；Vocabulary 纳入发布校验与查询提示 | 给定术语表，当运行 `tests/test_ubiquitous_language.py`，则公共模型类名/字段名与术语表一一对应、无别名冲突；给定 Vocabulary 缺失关键术语，则 `validate` 产出 warning 而非静默通过 | DM-1 |

---

## 附：与"v0.1 有意简化"的区分

| 项 | 定性 | 理由 |
|---|---|---|
| Review 折入 publish（README:278-280） | 简化，可接受 | 状态机预留了四态，升级路径是加一步，不是重建模型 |
| Test 步骤未建（README:281） | 简化，可接受 | pytest 承担；但"Test"缺失使 `published` 少了一个有意义的前置状态，应随 DM-7 的 Release 前置检查补回 |
| CreateMapping 折入 Projection（README:282） | 简化，可接受 | Mapping 作为 Projection 聚合内 VO 本来就是正确 DDD 选择（`[P]domain/data/model.py:8-10`） |
| 一个 workspace 一个 ontology（`ontology_usecases.py:28`） | **权宜，但有隐患** | 边界判断正确，表达方式错误（`list[0]`）；应先显式化引用（DM-1），再谈多本体 |
| 平台/内核两套持久化（design.txt:1745-1773） | 架构选择，不是简化 | 两个上下文本就该有两套存储；缺陷不在"两套"，而在**没有跨上下文一致性契约**（B6/DM-8） |
| 无事件溯源 | 简化，且正确 | 与控制论 §5.5 判断一致；缺的是事件语义与契约（DM-5），不是重放装置 |
| 无 Solution/Project 层级（design.txt:170-180） | 简化，可接受 | Workspace 作为唯一边界在 v0.1 成立；但 Membership 不变量必须补（P1-5） |

> **最终判断**：v0.1 的**战术骨架是对的**——按用例组织、有端口、有单一防腐层入口、Ontology 是真正的聚合、发布是唯一大门。真正的领域建模差距在三处：**(1) 没有"版本化的聚合 + 工作单元"**（P0-1/P0-2，是控制论 Phase 0 的建模前提）；**(2) 不变量仍在代码里，世界结构声明与运行时约束脱节**（P0-3）；**(3) 生命周期只建模到"发布"，没有 Release/Deployment，也没有领域事件契约**（P0-4/P1-4）。补上这三处，v0.2 才能在不推翻 v0.1 的前提下支持版本演进、回滚与可验证的动作闭环；不补，任何"补时间/补幂等"的工程改动都会因为没有聚合边界而无处安放。
