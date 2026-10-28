# OntoPlatform v0.2 改进计划（v2）：本体论 · 领域建模 · TDD 三视角复核后的修订版

> **本版依据**：`docs/control-theory-review.md`（v1：工程控制论诊断，11 条 P0 / 18 条 P1）+ 三份二轮复核
> —— `docs/_draft2/ontology.md`（本体论）、`docs/_draft2/ddd.md`（领域建模）、`docs/_draft2/tdd.md`（TDD/测试工程）。
> **修订原则**：v1 回答"回路为什么断"，v2 回答"先改什么、按什么顺序改、每步用什么测试守住"。
> **一句话**：v1 的计划是"按控制论维度排的工程任务清单"；v2 把它重排为**六条有依赖关系的工作流**，并加上三块 v1 缺失的地基：**语义地基（身份/约束）**、**领域地基（聚合版本/不变量/Release）**、**测试地基（契约测试与失败注入）**。

---

## 0. 修订摘要：v2 相对 v1 到底变了什么

| # | v1 | v2 | 为什么必须变 |
|---|---|---|---|
| 1 | 直接修"时间/幂等/回执"三个 P0 | 先在**语义层与领域层建立落点**（身份、聚合版本、不变量、Release），再修时间/幂等 | 没有聚合版本，`version` 加在 `Object` 上是"无主字段"；没有 `CapabilityInvocation` 聚合，幂等键无处安放；没有能力契约，后置条件无从声明 |
| 2 | 计划按 9 个技术主题并行 | 重组为 **6 条工作流 + 1 条测试护栏（W0）**，并给出依赖 DAG 与关键路径 | 技术主题之间存在硬依赖（见 §2.2），并行会互相踩脚 |
| 3 | 新增项 | **W1 语义内核**：身份声明、类型约束、约束分层（发布时/运行时/规则） | 当前"轻量对象图"只声明结构，不声明可判定性；控制回路的第一性原理是"模型可判定" |
| 4 | 新增项 | **W2 领域契约**：聚合版本 + 乐观锁、声明式不变量、Release/Deployment、领域事件目录 | DDD 复核指出：真正的差距是"没有版本化聚合与工作单元""不变量全在代码里""生命周期只建模到发布" |
| 5 | 新增项 | **W0 测试护栏**：seams（Clock/Id/Provider/Executor）、FakeProvider、契约注册表、失败注入 harness、CI 门禁 | TDD 复核实测：177 个测试全绿，但 P0 没有一条有"先失败的测试"；5 个现有测试还把错误行为固化成"正确" |
| 6 | Phase 0（2–3 周）：时间化+幂等+锁+指标+5 个正确性修复 | Phase 0（3 周）：测试基础设施 → 聚合版本 → 身份/约束最小集 → 时间化/幂等 → 执行边界 → 授权最小 | 顺序由依赖决定：测试先行、语义先行、版本先行 |
| 7 | 回滚只有一句"发布失败回滚" | 显式建模 `Release`（不可变制品）+ `Deployment`（指针），发布=创建 Release，部署=切指针，回滚=指回旧 Release | 没有制品概念，就没有可回滚对象；"不可变"不能只靠 `if` |
| 8 | 领域事件未展开 | 事件目录（6–10 个 V1 事件）+ envelope + 同事务 outbox；**不做事件溯源** | Change 只是 state diff，不是领域事件；缺事件语义则验证/审计/对账都无法消费 |
| 9 | 失败注入"建议做" | Phase 0 起 8 个场景必须全绿（含 kill -9 子进程模拟） | 控制语义无法用 happy path 证明 |
| 10 | 未界定"不做什么" | 明确 ADR：不引入 OWL 推理机、不做自动实体解析、不做全量 ES、不先拆微服务 | 防止"更完善"退化成"更庞大" |

**保持不变的部分**：v1 的四条主断点（时间/门限/回执/验证）、11 条 P0 的问题判定、以及"LLM 只做慢回路提议者"的定位全部成立；v2 只是给它们补上语义与领域的落点，并重排执行顺序。

---

## 1. 三视角复核结论

### 1.1 本体论：当前是"轻量对象图"，不是可判定的本体

判定依据（详见 `_draft2/ontology.md`）：没有身份准则、没有约束公理、没有时间与来源、没有版本兼容规则、TBox（世界定义）与 ABox（实例状态）混用、开放世界与封闭世界语义冲突。

> 编号说明：下表 O1–O10 是**判定项**（问题）；§3.1 的 **O-1..O-12** 是**工作项**（修法），两者编号不同，工作项原文见 `docs/_draft2/ontology.md` §C。

| # | 判定 | 证据 | 后果 |
|---|---|---|---|
| O1 | **身份不可判定**：`ObjectRef=(type,id)`，`id` 完全来自源行主键（`data_usecases.py:168-183`）；同一实体来自两个源/两个投影时无法合并，也没有 `external_ids` 映射 | `ontoRK/.../core/refs.py`；`application/data_usecases.py:163-183` | 跨源去重只能靠命名约定；实体解析（哪两行是同一辆车）无模型可依 |
| O2 | **类型系统过窄**：只有 7 种标量/结构类型（`string/number/integer/boolean/object/array/datetime`，`core/types.py:15-23`），没有 required、enum、range、unique、pattern、unit、precision；`array/object` 无元素 schema | `core/types.py:38-61`；`domain/ontology/model.py:22-30` | 业务约束只能写进函数；`balance` 可为负、`status` 可为任意字符串 |
| O3 | **关系语义不完整**：只有 `source/target/cardinality`，无逆关系、无关系属性、无重ification；`cardinality` 声明了但从不执行 | `core/schema.py:33-45`；`runtime/runtime.py:246-263` | 模型承诺 1:1，现实允许 N 条边——model-reality 不一致 |
| O4 | **没有不变量/公理层**：世界层与实例层未分离；无完整性约束、无状态机、无推理 | `domain/ontology/validation.py`（仅引用完整性）；`examples/toll/*` | 规则不可查询、不可证明、换函数即绕过 |
| O5 | **时间与来源缺失**：无 valid time / transaction time / 事件时间；无 provenance | `core/state.py:14-27`；`core/execution.py:37-49` | 无法判新鲜度、无法对账、无法归因（与 v1 P0-1 同源，此处是本体侧解释） |
| O6 | **版本无兼容性规则**：`version` 字段闲置，无新增/删除/收窄/放宽/重命名的兼容性判定，无迁移与弃用 | `domain/ontology/model.py:147`；`compiler.py:28-63` | 重发布不校验存量状态（v1 P1-8/P2-2），幽灵字段与类型漂移 |
| O7 | **词表只服务 LLM**：`Vocabulary` 是字符串匹配（最长术语优先），不参与发布校验/授权/查询；无同义层级/多语言/消歧 | `domain/intelligence/model.py:18-49`；`intelligence_usecases.py:38-53` | "业务语言"停留在提示词层，术语漂移无人察觉 |
| O8 | **事件与对象混用**：`Transaction` 作为对象建模，但它本质是"发生过的事件"；无事件/过程/角色的元类型区分 | `examples/toll/business.md`；`domain/*/model.py` | 统计口径（对象 vs 事件）会随建模者变化，无法统一 |
| O9 | **CWA/OWA 冲突**：`find` 未命中返回空集（隐含封闭世界），但语义上未声明的属性应视为"未知"而非"不存在" | `filters.py:20-21`；`runtime/runtime.py:140-143` | "没有数据"与"数据未知"不可区分，控制上误判为"目标达成" |
| O10 | **发布时不检查 TBox 一致性之外的约束**：`validate_ontology` 只查引用完整性，不查类型可满足性、不查约束冲突、不查上限 | `domain/ontology/validation.py:15-47` | 坏模型可以发布，直到运行时才炸 |

**本体侧结论**：v0.2 不需要（也不应该）上 OWL/DL 推理机。需要的是**一个"可判定的轻量约束层"**：身份准则、类型约束、关系基数、状态机、版本兼容规则、明确的 CWA 声明（"未设置 = 未知，不是空值"），并把这些约束**编译到 Kernel 的校验路径**（发布时 + 运行时两级）。

### 1.2 领域建模：战术骨架是对的，三处结构性差距

DDD 复核的必答结论（详见 `_draft2/ddd.md`）：

| 问题 | 结论 |
|---|---|
| 真正的限界上下文 | 3 个：**语义运行时（Kernel）**、**世界定义（平台核心）**、**执行与治理（平台运行时侧）**；七个 `domain/*` 是模块，不是上下文 |
| Kernel 是独立上下文还是共享内核 | 目标独立、实现上是 **Shared Kernel + Conformist**：平台类型系统与内核 1:1 同构、编译产物就是 kernel schema、网关 DTO 与内核类型同构、平台还直接使用内核 `store.seed` 绕过写模型 |
| 缺的聚合 | `Release`、`Deployment`、`ProjectionRun`、`CapabilityInvocation`、`Membership`；Kernel 侧 `Object` 无版本 |
| invoke 的一致性边界 | **命令粒度合理**（一次 invoke 原子提交，是内核最好的决策），但**隔离级别不合理**（无版本/乐观锁）且**边界内塞了长副作用**（LLM/工作流/任意 Python） |
| 哪些规则应上移为不变量 | 关系基数、对象状态枚举/状态机、属性范围/唯一/必填、发布前置条件、聚合版本前置条件 |
| Change 能否算领域事件 | **不能**：无 id/type/version/时间/因果/契约；只是 state diff + 平台自造信封 |
| application 层是否贫血 | 不是贫血而是**失血 + 泄漏**：发布决策在用例、生命周期可绕过、裸 dict 穿过层边界 |
| Capability 是什么 | 定义侧是聚合根；执行侧应是领域服务，但当前 `InvokeCapability` 只是壳，Agent 还绕过它 |
| 两套持久化是否违背 UoW | **是**：一次发布有 3 个独立提交点，没有 UoW/Saga/Outbox，是"假单事务" |
| 发布不可变是否破坏生命周期 | 不可变是好建模；坏的是把"状态迁移 + 制品生成 + 运行时安装"揉成一个 `publish`，且没有 Release 可回指 |

**三处结构性差距（v2 的主对象）**：
1. **没有版本化的聚合与工作单元**（聚合整文档覆盖、无 CAS、无 UoW）→ 并发丢更新、发布半成品；
2. **不变量仍在代码里**（世界结构声明与运行时约束脱节）→ 规则不可验证、换函数即绕过；
3. **生命周期只建模到"发布"**（无 Release/Deployment、无领域事件契约）→ 回滚无对象、演进只能新建工作空间、审计/验证无法消费。

### 1.3 TDD：177 个测试全绿，但它不是"控制语义的规格"

实测基线：平台 **99 passed**（14 个文件）、内核 **78 passed**；工具链只有 pytest（无 coverage / hypothesis / pytest-timeout / mutation / CI）。

测试组合的关键结论：
- 层次上"集成偏重、单元尚可、**契约缺环**"：68/99 个平台集成测试都用 `store="memory"` 的同一个 fixture（`tests/conftest.py:33-34,89-96`），sqlite 只被 2 条测试触达——而 v1 的 P0-7（启动 seed 覆盖状态）**只发生在 sqlite**。
- 关键契约 K1–K10 里，**K6（发布回滚）、K8（provider 超时/429）、K10（并发写）完全没有测试**；K3/K4/K5/K7/K9 只有单侧或弱断言。
- 6 个现有测试**固化了错误行为**，改代码前必须先改它们：失败动作后仍断言 `ok is True`（`tests/test_intelligence.py:62-76`）、HTTP 内部 `related` 与 MCP `related` 两份互相矛盾的测试（`ontoRK/tests/test_http.py:61-64` vs `test_mcp.py:87-90`）、复合能力 `changes == []` 被当作正常（`tests/test_capability.py:99-101`）、业务失败仍断言 HTTP 200（`tests/test_api.py:84-87`）、空洞断言（`tests/test_api.py:134`）、名为"幂等"却只跑"源未变"重放（`tests/test_projection.py:65-71`）。
- 可测性缺口：没有 `Clock`/`IdGenerator` 注入、`execution_id` 随机且不落库、provider 在请求体里直接实例化、全局锁不可观测、`_KERNEL` 懒加载单例、pytest 无 timeout。

**TDD 侧结论**：先把测试当规格——**每个 P0 先写失败测试（红），再写实现（绿）**；先改 6 个旧断言，再谈新契约；测试基础设施（seams + FakeProvider + 失败注入 + CI 门禁）是 Phase 0 的第一件事。

### 1.4 三视角交集：一条因果链，决定了实施顺序

```text
本体层：TBox 无约束（只有结构，没有身份/基数/状态机/时间）
   │
   ▼
实例层：ABox 无校验（写入只查类型；find 静默丢行；无版本）
   │
   ▼
领域层：不变量只能写进 Python 函数（不可查询、不可证明、换函数即绕过）
   │
   ▼
执行层：动作效果不可声明 → 无法验证 → 无法补偿 → 无法闭环
   │
   ▼
控制层：没有独立反馈信号 → "完成"由执行者自报
   │
   ▼
测试层：没有契约可断言 → 只能测 happy path → P0 无失败测试
```

**这条链说明：v1 的"时间/门限/回执"不是三个孤立工程项，而是"可判定模型"的下游。** 因此 v2 的顺序是：**语义地基（身份/约束）→ 领域地基（版本/不变量/Release/事件）→ 执行与观测（时间/幂等/超时/指标）→ 验证与对账 → 自适应与 AI 控制**，测试护栏贯穿始终。

---

## 2. 工作分解与依赖

### 2.1 六条工作流 + 一条护栏

| 工作流 | 负责什么 | 主要产出 | 对应 v1 问题 |
|---|---|---|---|
| **W0 测试护栏**（横向） | 可测性 seams、契约测试、失败注入、CI 门禁 | FakeProvider/FakeClock/FakeId、契约注册表、8 个注入场景、门禁规则 | 让所有 P0 可 test-first |
| **W1 语义内核** | 身份、类型约束、约束分层、版本兼容规则 | 本体 v0.2 元模型 + 发布时校验 + 运行时校验 | 新增（原 v1 未覆盖） |
| **W2 领域契约** | 聚合版本/乐观锁、声明式不变量、Release/Deployment、领域事件 | DM-1..DM-8 | P0-1/P0-2/P0-6/P1-8/P1-9 |
| **W3 执行与观测** | 状态时间化、幂等、三态结果、锁外执行、超时/预算、指标 | P0-1/P0-3/P0-4/P0-9/P1-6/P1-18 | 原 v1 Phase 0 主体 |
| **W4 数据、验证与对账** | 投影水位/删除/ProjectionRun、能力后置条件、独立 verifier、对账任务 | P0-6/P1-7 + v1 Phase 1 | 原 v1 Phase 1 |
| **W5 治理与安全** | actor 认证、读写权限、ActionAuthorization、审批、审计关联 | P0-8/P1-18 | 原 v1 Phase 1 |
| **W6 控制与自适应** | Goal/SLO、分层控制器、漂移检测、影子/canary、大系统协调 | v1 Phase 2 | 原 v1 Phase 2 |

### 2.2 依赖 DAG（决定并行度与关键路径）

```text
W0 测试护栏 ──────────────────────────────┐（可立即开始，横向支撑全部）
                                          │
W1 语义内核 ──▶ W2 领域契约 ──▶ W3 执行与观测 ──▶ W4 验证与对账 ──▶ W6 控制与自适应
   │              │                                    ▲
   │              └──────────────▶ W5 治理与安全 ───────┘
   │                                   （可与 W2 并行，W6 依赖）
   └──▶（类型约束供 W3 校验、W4 后置条件）
```

**关键路径**：`W1 身份/约束 → W2 聚合版本 → W3 时间化/幂等 → W4 能力契约/验证 → W6 闭环控制`。
**可并行**：W0 全程；W5 在 W2 之后即可独立推进；W4 的投影部分可与 W3 并行（两者只共享"变更流/时间"契约）。
**禁止并行**：W2 的聚合版本与 W3 的时间化（同一批表/迁移）；W4 的 verifier 与 W2 的能力契约（后置条件格式未定则 verifier 无输入）。

### 2.3 写作用域（多人/多 agent 并行时的文件边界）

| 工作流 | 独占写作用域 |
|---|---|
| W0 | `tests/`（新增目录与 conftest）、CI 配置、`pyproject.toml` |
| W1 | `domain/ontology/**`、`ontoRK/core/{schema,types}.py`、`ontoRK/loader/**` |
| W2 | `domain/governance/**`、`application/{ontology_usecases,capability_usecases,common}.py`、`ports/storage.py`、`infrastructure/persistence/**` |
| W3 | `ontoRK/core/{state,execution}.py`、`ontoRK/runtime/**`、`ontoRK/infrastructure/{sqlite,memory}.py`、`infrastructure/runtime/kernel_gateway.py`、`interfaces/api.py` |
| W4 | `application/data_usecases.py`、`infrastructure/execution/**`、`application/agent_runner.py`、`infrastructure/datasource/**` |
| W5 | `interfaces/api.py`（鉴权段）、`domain/governance/model.py`（策略）、`application/workspace_usecases.py` |
| W6 | `application/application_usecases.py`、`domain/intelligence/**`、`domain/application/**`、前端 |

> 重叠区（`interfaces/api.py`、`kernel_gateway.py`）必须由 Lead 排他分配并在合并前跑全量测试。

---

## 3. 目标模型（v0.2 蓝图）

### 3.1 本体最小形式化（W1 交付）

**原则**：不追求 DL 表达力，只追求"可判定"。三层约束，各归其位：

| 层 | 约束类型 | 在哪里检查 | 例子 |
|---|---|---|---|
| **发布时（TBox）** | 引用完整性、类型合法性、身份声明、约束可满足性、版本兼容性 | `Ontology.assert_publishable()` + 编译 | 关系端点存在、`cardinality` 是合法枚举、`enum` 非空、删除属性必须有迁移计划 |
| **运行时（ABox）** | required、类型、enum、range、unique、关系基数、状态机、聚合版本 | Kernel 校验 + 聚合不变量 | `balance >= 0`、`status ∈ {open,closed}`、1:1 关系第二条边被拒 |
| **规则（不进本体）** | 跨对象业务规则、审批流程、时序约束 | 领域服务 / workflow / 策略 | "欠费超过 3 天的车禁止上高速" |

**逻辑 profile（必须显式声明，写进发布产物）**：`object_existence: closed`（`find` 未命中=不存在，但缺属性要单列 `excluded_missing` 计数）、`property_completeness: open`（未声明属性=未知，不是空）、`unknown_value: absent`（null 不可表示，缺失与未知必须区分）。这不是学术洁癖：`find` 空集到底是"确实没有"还是"过滤写错/属性缺失"，直接决定控制回路把"目标未达成"判成什么。

**元模型扩展（最小集）**：

```yaml
Ontology:
  profile: {object_existence: closed, property_completeness: open, unknown_value: absent}
ObjectType:
  identity: [plate]              # 身份准则（业务键，可组合；投影 id_field 必须映射到它）
  category: object               # object | event | value（事件必须有 occurred_at）
  properties:
    plate:  {type: string, required: true, unique: true}
    status: {type: string, enum: [open, closed], default: open, temporal: snapshot}
    balance:{type: number, minimum: 0, unit: CNY, precision: 2}
  state_machine:                 # 可选：对象生命周期
    field: status
    transitions: {open: [closed], closed: [open]}
Relation:
  source: Vehicle
  target: TollStation
  cardinality: many-to-one       # 枚举，不是自由文本，运行时执行
  inverse: passed_by             # 可选，用于导航一致性
  derived_from: TollTransaction  # 可选：由哪个事件派生（消除端点双重表示）
```

**本体工作项（O-1..O-12，详见 `docs/_draft2/ontology.md` §C）与阶段映射**：O-1 profile/CWA、O-2 身份进 TBox、O-3 命名空间/ID 冲突/撤回、O-10 映射校验前移 → **Phase 0**（与 W2 聚合版本同批）；O-4 属性 facets、O-5 词表契约、O-6 事件/对象区分与 lint、O-7 时间最小模型、O-8 发布时 ABox 合规+兼容性矩阵、O-11 语义上下文机读 → **Phase 1**；O-9 修订与指纹、O-12 上部区分固化为 lint（**不引入 BFO/DOLCE/SOSA**）→ **Phase 2**。

**CWA 声明**：统一语义为"未设置 = 未知"；`find` 在属性缺失时**不返回**该对象，但 `get` 必须能区分 `missing` 与 `null`（返回值中带 `unknown_fields` 或 `null` 语义），前端/Agent 观察据此显示"未知"。

**版本与兼容**：`schema_version` 单调递增并落库；兼容性规则表（新增属性=兼容、放宽约束=兼容、收窄/删除/重命名=不兼容，需 migration 或 deprecate 期）；发布时打印 drift 报告。

### 3.2 领域模型（W2 交付）

**上下文映射（3+1）**：

```text
┌──────────────────────┐   编译产物（Published Language）   ┌──────────────────────┐
│ 世界定义（Platform）   │ ───────────────────────────────▶ │ 语义运行时（Kernel）   │
│ Ontology/Data/        │   显式 ACL：SchemaPayload/DTO     │ Schema/State/Function │
│ Capability/Application│ ◀─────────────────────────────── │ Change/Invoke          │
└──────────┬───────────┘      InvocationResult/Change       └──────────────────────┘
           │ 领域事件（Outbox）
           ▼
┌──────────────────────┐
│ 执行与治理（Platform） │  Invoke/Agent/Workflow/Audit/Authz
└──────────────────────┘
```
- Kernel 与平台之间：**显式 Shared Kernel 契约**（版本化 schema + 双方 CI 契约测试）或真 ACL；二者选一，写进 ADR，不能含糊。
- 上下文之间禁止共享数据库文档：改为端口 + Published Language（架构测试守门：`domain/**` 不 import 其他上下文的仓储实现）。

**聚合（v0.2）**：

| 聚合根 | 内部 | 不变量 | 版本化 |
|---|---|---|---|
| `Ontology` | ObjectType/Relation/Vocabulary | 引用完整、身份唯一、发布前置条件 | ✅ |
| `Release`（新） | schema 快照 + bindings + hash + 作者/时间 | 不可变、可比较 | 不可变 |
| `Deployment`（新） | workspace → release_id, activated_at | 同一 workspace 只有一个 active | ✅ |
| `Capability` | Implementation/Inputs/**Contract**（新） | key 唯一、目标类型存在、契约可执行 | ✅ |
| `CapabilityInvocation`（新） | idempotency_key/三态/actor/execution_id | 同 key 只生效一次 | 不可变 |
| `Projection` | Mapping + **Run**（新） | 映射引用完整 | ✅ |
| `ProjectionRun`（新） | watermark/rows/upserts/deletes/errors | 一次运行一条 | 不可变 |
| `Application` | View/Query/**Ref**（强类型） | 引用存在、发布前置条件 | ✅ |
| `Membership`（新） | role | 至少一个 owner | ✅ |
| Kernel `Object` | properties + **version/updated_at/source** | 属性约束、关系基数 | ✅ |

**领域事件目录（V1，6–10 个）**：`OntologyPublishedV1`、`CapabilityPublishedV1`、`ProjectionRunCompletedV1`、`ObjectPropertyChangedV1`、`RelationLinkedV1`、`CapabilityInvocationSucceededV1`、`CapabilityInvocationFailedV1`、`VerificationViolatedV1`。
envelope 固定：`{event_id, type, version, occurred_at, workspace, aggregate, payload, execution_id, actor, correlation_id}`；与状态变更**同事务写入 outbox**；失败与零变更也要有事件。

**生命周期三层**：

```text
Asset（draft/validated，可编辑）
  └── Release（不可变制品；可多份、可比较、可回指）
        └── Deployment（workspace 指针；切换=部署，指回=回滚）
```

### 3.3 测试架构（W0 交付）

```text
tests/
├── unit/            # domain、值对象、规约（纯函数，无 IO）
├── contracts/       # 契约注册表：Kernel 侧、Platform 侧各一版；memory/sqlite 同套
├── integration/     # use case + sqlite（不是 memory）+ 真实编解码
├── acceptance/      # 运营者/审计员语言，只经 HTTP/CLI（Given/When/Then）
├── failure/         # 超时/429/5xx/部分提交/死循环/损坏日志
├── concurrency/     # 双写、同幂等键、慢调用不阻塞读、发布 vs 执行
└── golden/          # compiler 产物快照、示例数据可复现
```

**契约注册表**（`tests/contracts/registry.py`）：把 K1–K10 每项登记为"必须被两侧至少一条测试覆盖"的条目，加一条元测试断言矩阵无空项；新增 C1–C10 语义契约（三态结果、幂等、事件 envelope、后置条件、权限、发布原子性、本体 profile、身份一致性、约束可执行、版本兼容性）。

**seams**：`Clock`、`IdGenerator`、`LLMProvider`、`FunctionExecutor`、`Metrics` 五个可注入端口；`PlatformConfig` 与 `KernelRuntime` 构造器接收它们；测试禁止 `time.sleep` 等待（用 `Event`/Barrier）。

**门禁**（每 Phase 结束必须全绿）：见 §5.3。

---

## 4. 修订后的路线图

### Phase 0｜语义地基与测试护栏（3 周）

> 目标：让"可判定"成立——身份/约束能声明、聚合能版本化、动作能重放、测试能先红。

| # | 工作项 | 内容 | test-first 验收（先红后绿） |
|---|---|---|---|
| 0.1 | **W0 测试基础设施** | `fake_clock/fake_id/FakeProvider`（可编程 timeout/429/坏 JSON/空 content/非法工具）；`empty_workspace`/`sqlite_platform`/`http_server` fixtures；pytest-timeout、coverage、hypothesis、mutation、markers、CI | 失败注入 8 场景各 1 条红；`pytest --timeout=30 --strict-markers` 全绿；C1 六项旧断言改写 |
| 0.2 | **W2 聚合版本 + CAS** | `documents` 加 `version`；`Repository.get_versioned/put_if_version`；聚合基类 `version`；冲突异常→409 | `test_two_editors_second_gets_conflict`；`test_version_monotonic` |
| 0.3 | **W1 身份与类型约束最小集** | `identity` 声明（投影 `id_field` 必须一致）、`required/enum/minimum/maximum/unique`、`cardinality` 枚举化、`schema_version` 落库、逻辑 profile 声明；发布时校验 + Kernel 运行时校验（O-1/O-2/O-3/O-10） | `test_identity_declared_and_enforced`；`test_projection_id_field_must_match_identity`；`test_cross_source_id_collision_detected`；`test_property_enum_violation_rejected`；`test_cardinality_executed`；`test_find_reports_excluded_missing` |
| 0.4 | **W2 发布前置条件合并** | `Ontology.assert_publishable()`；用例只调用一次；删除四处重复校验 | `test_publish_rejects_dangling_reference`；`test_publish_failure_keeps_validated` |
| 0.5 | **W3 状态时间化** | `Object.version/updated_at/source`；`Change.ts/execution_id/seq`；同事务 `changes` 表 | `test_object_exposes_version_updated_at_source`；`test_change_carries_seq_ts_execution_id` |
| 0.6 | **W3 幂等与三态** | `Idempotency-Key`；`executions` 表；`succeeded/failed/unknown`；HTTP 语义 | `test_same_key_applies_once`；`test_result_tristate` |
| 0.7 | **W3 执行边界** | 锁缩到提交段；executor 超时/并发上限；`max_steps` 上界；`unknown` 不渲染为失败 | `test_slow_invoke_does_not_block_reads`；`test_dead_loop_times_out`；`test_llm_timeout_is_unknown` |
| 0.8 | **W5 授权最小集** | actor 认证（token）、`user` 不自报、写需 editor、body 禁 provider、审计带 `execution_id` | `test_viewer_cannot_invoke_write`；`test_agent_write_audited`；`test_provider_not_injectable` |
| 0.9 | **W2/W3 六个正确性修复** | 启动 seed 仅空库；`apply_delta` 单事务；`set_property` 目标必须存在；`publish_schema` 持锁；能力源码 hash 进缓存键；复合绑定合并 `changes` | 对应 6 条红测试（tdd D2 表） |

**Phase 0 退出条件（缺一不可）**：
- 20 条 Phase 0 失败测试全部"旧实现红 → 新实现绿"，PR 附两段输出；
- 8 个失败注入场景全绿；3 条并发测试 100 次循环 0 失败；
- `tests/contracts/registry.py` 中 K1–K10 无空项；
- 全量：平台 ≥130 tests、内核 ≥95 tests，`pytest --strict-markers --timeout=30` 全绿且总时长 < 90s。

### Phase 1｜契约与闭环（5–7 周）

| # | 工作项 | 内容 | test-first 验收 |
|---|---|---|---|
| 1.1 | **W2 Release/Deployment** | 三层生命周期；publish=创建 Release、deploy=切指针、rollback=指回 | `test_failed_publish_leaves_no_release`；`test_rollback_points_to_previous_release` |
| 1.2 | **W2 发布 Saga/UoW** | `UnitOfWork`（begin/commit/rollback + 聚合/事件收集）；publish 本地事务 + outbox + dispatcher 重试 | `test_publish_survives_crash_and_retries`；`test_no_half_installed_runtime` |
| 1.3 | **W2 领域事件 + Outbox** | 事件目录 V1、envelope、同事务写入、失败/零变更事件 | `test_failed_invocation_emits_event`；`test_events_same_tx_as_state` |
| 1.4 | **W4 能力契约** | `preconditions/postconditions/effects/idempotent/compensate`；发布时校验；调用后 verifier（含 O-4 属性 facets：enum/range/unit/required 写入校验） | `test_postcondition_violation_flagged`；`test_recharge_balance_equals_old_plus_amount`；`test_projection_rejects_value_outside_enum`；`test_unit_mismatch_rejected` |
| 1.5 | **W4 独立验证器 + 对账** | `Verifier`（只读、独立）；`Reconciler`（源 vs 语义 vs 外部）；差异清单进变更流 | `test_verification_violation_observed`；`test_reconcile_reports_mismatch` |
| 1.6 | **W4 投影 v2** | 水位/增量、删除传播、`ProjectionRun`、对账、幂等运行键 | `test_source_deletion_propagates`；`test_watermark_skips_unchanged`；`test_projection_run_recorded` |
| 1.7 | **W4 workflow Saga** | 补偿动作、失败逆序补偿、每步超时、幂等贯穿 | `test_step_k_failure_compensates_prior` |
| 1.8 | **W5 完整治理** | `ActionAuthorization` 策略；Agent 走 `InvokeCapability`；成员管理/最后 owner；审批 | `test_viewer_write_denied`；`test_last_owner_cannot_be_removed`；`test_illegal_transition_rejected` |
| 1.9 | **W3 观测完整化** | `/metrics`（10 指标）、变更流游标保留/压缩/失败事件、分页/聚合接口、前端"数据时刻 + 回读" | `test_changes_cursor_resumes`；`test_find_limit_cursor`；`test_console_shows_asof`（组件） |
| 1.10 | **W2 值对象与错误分类** | `ObjectRef/Amount/TimeRange/Cardinality/WorkflowStep`；领域异常（InvariantViolation/ConcurrencyConflict/DanglingReference） | `test_invalid_ref_rejected_at_construction`；`test_error_codes_stable` |
| 1.11 | **W1 本体深化（Phase 1 部分）** | O-5 词表契约（value 概念/alias 唯一/弃用）、O-6 事件/对象区分 + lint、O-7 时间最小模型、O-8 发布 ABox 合规 + 兼容性矩阵、O-11 语义上下文机读 | `test_vocabulary_alias_must_be_unique`；`test_lint_flags_duplicated_endpoint`；`test_publish_rejects_narrowing_when_state_violates`；`test_agent_context_contains_enum_values` |

### Phase 2｜自适应与大系统（6–10 周）

| # | 工作项 | 内容 | 验收 |
|---|---|---|---|
| 2.1 | **W6 Goal/SLO** | `Application.objective{metric,target,band,window}`；视图显示"当前/目标/偏差/趋势"；越界生成待办 | 业务页面显示偏差；越界自动生成处理项 |
| 2.2 | **W6 分层控制器** | 快回路规则（阈值/限流/重试预算）；慢回路对账/漂移；LLM 仅提议；提议→裁决→验证三段 | 快回路不依赖 LLM；提议拒绝率可观测 |
| 2.3 | **W6 漂移与质量** | schema drift、数据源新鲜度、投影失败率、LLM 质量抽检；影子模式/canary/降级 | drift 发现 <1 同步周期；灰度可对比 |
| 2.4 | **大系统协调** | 工作区配额/优先级/限流；跨工作区只读汇总；多实例租约或明确单实例契约 | 单工作区打满不影响其它；写冲突有明确语义 |
| 2.5 | **W1 本体深化（Phase 2）** | O-9 修订与指纹（`POST /ontology/revisions`、`parent_version`、`schema_fingerprint`、自愈时检测指纹不一致）、O-12 建模规范 lint（value 不得有 identity、不得作关系端点；书面声明不引入 BFO/DOLCE/SOSA）；关系属性/重ification 仅当业务出现需求 | `test_new_revision_increments_version_and_links_parent`；`test_publish_writes_schema_fingerprint`；`test_value_category_must_not_have_identity`；对应业务场景的验收测试 + 迁移计划 |

---

## 5. 测试与门禁（贯穿三阶段）

### 5.1 测试分层与责任

| 层 | 数量目标（v0.2） | 谁写 | 跑多久 |
|---|---|---|---|
| unit（domain/值对象/规约） | 60+ | W1/W2 | < 5s |
| contracts（K1–K10 + C1–C10） | 25+ | W0 定义、各流补 | < 15s |
| integration（sqlite，不是 memory） | 40+ | W2–W4 | < 40s |
| acceptance（HTTP/CLI，业务语言） | 15+ | W4/W6 | < 60s |
| failure injection | 8 场景 × 2 | W0/W3 | nightly |
| concurrency（Barrier 同步） | 6+ | W3/W4 | nightly |
| golden（compiler/示例） | 5+ | W1/W4 | < 5s |

### 5.2 契约注册表（新增/修订）

| ID | 契约 | 现状 | v0.2 目标 |
|---|---|---|---|
| K1 | 编译产物被 Kernel `parse_schema` 接受 | ✅ 有 | 保留 + 语义断言（约束被执行） |
| K2 | `invoke` 永不 raise | ✅ 有 | 保留；错误分类在结果中可判定 |
| K3 | `changes` 语义 | ⚠️ 只测单函数 | 补复合绑定、失败、零变更、三态 |
| K4 | 变更流 seq/回放 | ⚠️ | 补游标校验、失败事件、重启续传 |
| K5 | 投影幂等/原子 | ⚠️ | 补失败无残留、删除传播、重复 ref |
| K6 | 发布回滚 | ❌ | Release/Deployment + 失败不残留 |
| K7 | 权限 | ⚠️ 无 viewer 写负例 | 补 viewer 写、Agent 写、provider 注入 |
| K8 | provider（超时/429/usage） | ❌ | FakeProvider + 本地假端点 |
| K9 | ACL 错误映射 | ⚠️ | 错误码/阶段/可重试性；`int()` → 400 |
| K10 | 并发（不丢更新/不双花） | ❌ | Barrier 双写、同 key、慢调用 |
| C1 | 三态结果 | ❌ | succeeded/failed/unknown |
| C2 | 幂等 | ❌ | 同 key 一次 |
| C3 | 事件 envelope | ❌ | event_id/type/version/ts/execution_id |
| C4 | 后置条件验证 | ❌ | verifier 独立判定 |
| C5 | 授权策略 | ❌ | 动作/目标/角色矩阵 |
| C6 | 发布原子性 | ❌ | Release 不残留 |
| C7 | 本体 profile | ❌ | `find` 返回 `excluded_missing`；类型错误→400 |
| C8 | 身份一致性 | ❌ | 投影 `id_field` = 声明的 identity；跨源冲突显式化 |
| C9 | 约束可执行 | ❌ | enum/range/unit/cardinality 在写入时被拒 |
| C10 | 版本兼容性 | ❌ | 收窄/删除被发布校验拒绝并给出建议 |

### 5.3 每 Phase 的测试门禁

| 门禁 | Phase 0 | Phase 1 | Phase 2 |
|---|---|---|---|
| 契约覆盖 | K1/K2/K3/K6/K7/K10 各有双侧测试；注册表无空项 | C1–C6 全部有测试 | 目标/漂移契约全覆盖 |
| 失败注入 | 8 场景全绿（kill -9 用子进程） | + 429/5xx/部分提交/损坏日志 | 保留并 nightly |
| 并发 | ≥3 条（双写/同 key/慢调用不阻塞） | + 发布 vs 执行、workflow 并发 | + 多工作区隔离 |
| 红→绿证据 | 每个 P0 有旧实现失败的 CI artifact | 同左 | 同左 |
| mutation score（改动文件） | ≥60% | ≥75% | ≥85% |
| flaky | 0 容忍；2 次失败即 quarantine；P95 < 5s | 同左；CI 跑 3 次全绿 | 同左 + nightly |
| 前端 | 0 新增 | `logic.test.ts` 进 CI + "unknown 不渲染为失败" | 关键视图冒烟 |

### 5.4 必须先改的 6 个旧断言（改代码前）

见 §1.3 列出的 C1-1..C1-6。处置规则：同一 PR 内"先重写测试（红）→ 再改实现"；PR 描述必须写明"旧断言为何是错的"，并单独 commit。

### 5.5 首批 20 条失败测试

直接采用 `docs/_draft2/tdd.md` D2 表（测试名、给定-当-则、对应 P0 一一对应）。它们是 Phase 0 的验收清单，不允许先写实现。

---

## 6. 关键设计决策（ADR 摘要）

| ADR | 决策 | 取舍理由 |
|---|---|---|
| ADR-1 | 轻量本体 + 两级校验（发布时/运行时），**不引入 OWL/DL 推理机** | 业务需要的是"可判定约束"而非"可推理分类"；推理机带来复杂度与不可解释性，且 v0.1 的约束规模用规则+代码即可覆盖 |
| ADR-2 | 身份 = 显式 `identity` 声明 + `external_ids` 映射；**不做自动实体解析（ER）** | 自动 ER 的误合并比不合并更危险；先让人声明"这就是同一实体"，需要时再引入匹配规则 |
| ADR-3 | 版本化 = 聚合 `version`（CAS）+ `Release`（不可变制品）+ `Deployment`（指针） | 版本是并发正确性的最小前提；Release/Deployment 让"回滚"从"没有"变成"改指针" |
| ADR-4 | 事件 = 领域事件目录 + envelope + 同事务 outbox；**不做事件溯源** | 当前状态+变更日志足够；缺的是语义与契约，不是重放装置 |
| ADR-5 | 跨上下文一致性 = 本地事务 + Outbox + Saga/Process Manager；**不做分布式事务** | 两套持久化是架构选择；"假单事务"必须换成显式最终一致 |
| ADR-6 | 测试 = 契约测试独立层 + 失败注入 + test-first 门禁 | 控制语义无法用 happy path 证明；先红后绿是唯一能防止"改完才发现规格错"的办法 |
| ADR-7 | LLM 只做慢回路提议者；快回路交给规则/优化器 | 带噪声、带时滞的控制器不能进高频闭环；这是 v1 已定的边界，v2 保留并写进架构约束 |
| ADR-8 | 不把本体当控制模型；三层分工（本体/动态因果/控制策略） | 本体回答"世界如何表达"，控制回答"如何持续达成目标"，混用会导致两边都做不好 |
| ADR-9 | 本体 = 类型化属性图 + 显式 profile + 两级约束 + lint；**不引入 BFO/DOLCE/SOSA 等上层本体，不引入推理机** | 业务需要"可判定"，不需要"可推理分类"；上层本体只在出现"对象/事件/角色"歧义时用 `category` 元字段 + lint 规则解决 |

---

## 7. 与 v1 计划的对照（变更矩阵）

| v1 项 | v2 处理 | 原因 |
|---|---|---|
| P0-1 状态时间化 | 保留，纳入 W3；前置为 W2 聚合版本 | 没有聚合版本，`version` 无法参与并发控制 |
| P0-2 无条件写/CAS | 升级为"聚合版本 + 乐观锁"（W2） | DDD：原子≠隔离；缺的是聚合版本 |
| P0-3 幂等/三态 | 升级为 `CapabilityInvocation` 聚合（W2/W3） | 幂等键需要宿主聚合与执行记录 |
| P0-4 超时语义 | 保留（W3），并与 W0 FakeProvider 一起验 | 无注入就无法证明 |
| P0-5 复合 changes | 保留（W2/W3 交界） | 需要领域事件语义才能定义"改了什么" |
| P0-6/P1-7 投影 | 保留并升级为 `ProjectionRun` + 水位/删除/对账（W4） | v1 只修原子性，没建模"运行" |
| P0-7 seed 回滚 | 保留（W3.9） | 独立小修，但需 sqlite 契约测试 |
| P0-8 权限 | 保留并升级为 `ActionAuthorization` + Agent 归位（W5） | 权限不是"角色比较"，是策略对象 |
| P0-9 全局锁 | 保留（W3.7） | 需先有"执行不持锁"的提交段边界 |
| P0-10 幽灵对象 | 保留（W2/W3 交界） | 需要"写必须经聚合"的契约 |
| P0-11 源码不生效 | 保留（W3.9） | 执行器缓存键含 hash |
| P1-8 发布回滚 | 升级为 Release/Deployment + Saga（W2） | 没有制品就没有回滚对象 |
| P1-9 Agent 自报 | 升级为"能力后置条件 + 独立 verifier"（W4） | 需要先有契约格式 |
| 新增 | 身份/类型约束（W1）、领域事件（W2）、测试护栏（W0） | 三视角复核的核心增量 |
| 顺序 | 语义地基 → 领域地基 → 执行观测 → 验证 → 自适应 | 由 §1.4 的因果链决定 |
| Phase 0 周期 | 2–3 周 → 3 周（8 项） | 新增测试基础设施与语义地基，范围变大但风险下降 |

---

## 8. 风险与取舍

1. **语义形式化过度**：加约束会拖慢建模体验。对策：约束分级（required/enum 必填，state_machine 可选），并提供"宽松模式"（只告警不阻塞）用于探索期。
2. **身份合并的复杂性**：`external_ids` 只能解决已知的同一实体；真正的实体解析留到业务需要时，且必须人工确认。
3. **Release/Deployment 增加一次部署动作**：v0.1 的"发布即生效"要拆成两步。对策：默认 `publish` = Release + 自动 deploy（保持一键体验），需要灰度时再显式 deploy。
4. **TDD 门禁拖慢交付**：只对 P0 与契约强制；P2 允许先实现后补测试，但必须登记欠账。
5. **测试基础设施是"为测试写测试"**：Phase 0 前 2 天看不到业务功能。这是必要投入——没有 seams，后面的并发/超时测试写不出来。
6. **多实例仍不在 Phase 0/1 承诺范围**：当前单实例 + 持久化是明确契约；多实例要等 W2 的 UoW/Outbox 稳定后再做。
7. **不推翻 v0.1 边界**：Review/Test 折入 publish、Mapping 折入 Projection、单 workspace 单 ontology 继续保留；v2 改的是"语义与契约"，不是"模块数量"。

---

## 9. 验收清单（v0.2 DoD）

**语义层**
- [ ] 每个对象类型声明了身份准则；`identity` 冲突被拒绝
- [ ] `enum/required/minimum/maximum/unique` 在运行时被执行
- [ ] `cardinality` 被编译并执行；第二条 1:1 边被拒
- [ ] `schema_version` 落库；不兼容变更被发布校验拒绝并给出迁移建议

**领域层**
- [ ] 两个编辑器并发保存，第二个收到 409，库中版本单调
- [ ] 发布失败不残留 Release/Deployment，生命周期不变
- [ ] 回滚 = 指回旧 Release，运行时 schema 随之切换
- [ ] 每个写动作产生领域事件，事件与状态同事务；失败动作也有事件
- [ ] 每个已发布能力有可执行的后置条件；verifier 独立判定

**执行与观测**
- [ ] 任意对象可回答"何时、由谁、通过什么动作改的"
- [ ] 同幂等键重复调用，世界只变一次
- [ ] 超时请求返回 `unknown` 且可按 `execution_id` 查真实结果
- [ ] 慢调用不阻塞其它请求；死循环能力被超时终止
- [ ] `/metrics` 有 10 个指标；控制台显示数据时刻；写后有回读

**测试**
- [ ] 每个 P0 有"旧实现红 / 新实现绿"的 CI artifact
- [ ] 契约注册表 K1–K10 + C1–C6 无空项
- [ ] 8 个失败注入场景全绿；3 条并发测试 100 次循环 0 失败
- [ ] mutation score：改动文件 ≥60%（Phase 0）/ ≥75%（Phase 1）
- [ ] 6 个固化错误行为的旧测试已改写并记录原因

---

## 附录 A：三视角缺陷索引

| 视角 | 缺陷清单位置 | Top 判定 |
|---|---|---|
| 本体论 | `docs/_draft2/ontology.md` §B（P0×5、P1×8、P2×6） | 身份不可判定、ABox 无校验出生通道、无删除/撤回语义、CWA/OWA 未声明、属性无值域/单位/必填；另有 12 项本体工作项 O-1..O-12 |
| 领域建模 | `docs/_draft2/ddd.md` §C（P0×5、P1×6、P2×3） | 无版本化聚合/UoW、不变量在代码里、生命周期缺 Release/事件 |
| TDD | `docs/_draft2/tdd.md` §C（C1×6、P0×5） | 契约缺环、6 个测试固化错误行为、可测性 seam 缺失 |
| 控制论（v1） | `docs/control-theory-review.md` §4 | 时间/门限/回执/验证四断点 |

## 附录 B：术语对照（v2 新增）

| 概念 | 定义 | 落点 |
|---|---|---|
| 身份准则（identity） | 判断"两个引用是否是同一实体"的业务键 | Ontology 元模型 + Kernel 校验 |
| 聚合版本（aggregate version） | 聚合级乐观锁版本号 | 平台 documents + Kernel objects |
| Release | 不可变制品（schema 快照 + bindings + hash） | 平台，发布产物 |
| Deployment | workspace → Release 的指针 | 平台，部署状态 |
| 领域事件 envelope | 事件的标准信封（id/type/version/时间/因果） | 平台，outbox |
| 后置条件（postcondition） | 动作执行后必须成立的可检查断言 | Capability 契约 + verifier |
| 规约（Specification） | 可命名、可组合的业务判定对象 | 领域层 |
| 契约注册表 | 必须被测试覆盖的接口契约清单 | tests/contracts |
| 端口 seam | 可注入的时间/ID/Provider/Executor/Metrics 接口 | ports/ + 构造器 |

---

> **v2 一句话总结**：v1 证明了"回路是断的"；v2 的回答是——**先用测试把"断在哪"变成可执行的红灯，再用语义约束让"什么算对"可判定，用聚合版本与 Release 让"改坏了能退"，用领域事件与 verifier 让"做完了能验"，最后才让 LLM 回到它该在的慢回路。**
