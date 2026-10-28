# 工程控制论视角下的 OntoPlatform v0.1：系统诊断与优化方案

> **分析对象**：`F:\piAgent\ontoPlantform`（本体智能平台 v0.1，Python 约 6.8k 行 + React 控制台）与 `F:\piAgent\ontoRK`（Ontology Runtime Kernel v0.1，约 2.5k 行）。
> **分析视角**：钱学森《工程控制论》的回路语言——设定值、模型、观测、执行、反馈、稳定性、时滞、噪声、自适应、冗余与大系统。
> **证据口径**：静态通读源码 + 运行测试（`python -m pytest tests/ -q` → **99 passed in 7.14s**，当前工作树含未提交改动；初测为 95 passed）。本文所有"文件:行"均对应当前工作树；数量级估算一律标注【推断】，未做压测与故障注入实验。
> **与既有文档的关系**：`docs/completeness-review.md` 度量的是"design.txt 的架构契约实现度"（结论 92%）；本文度量的是**控制回路的闭合度**。两个口径都成立，但后者才是"系统能不能在扰动中持续达成目标"的判据。
> **分工**：四层详细证据分别见 `docs/_draft/kernel.md`、`docs/_draft/platform.md`、`docs/_draft/intelligence.md`、`docs/_draft/console.md`；本文是复核后的总报告。

---

## 摘要（结论先行）

**一句话**：OntoPlatform 已经是一个合格的"本体驱动的动作平台"（能建模、能接数据、能执行能力、能让 AI 调用），但还不是一个"控制系统"——它具备回路的**骨架**，缺少回路的**三件底层工程承诺：时间、门限、回执**；再加上**验证与权限没有接进回路**，所以当前系统在扰动、并发、超时与模型演进面前，本质上仍是**开环**的。

四条主断点：

| # | 断点 | 表现 | 后果 |
|---|---|---|---|
| 1 | **观测没有时间基准** | 对象状态无 `version/updated_at/source`；Change 无 `ts/execution_id/seq`；全前端无"数据时刻" | 无法判断读数是否过期、无法对账、无法把"动作"与"效果"归因 |
| 2 | **执行没有门限与回执** | 写入是无条件覆盖（无 CAS/版本）；调用无幂等键、无超时、无并发上限；结果只有"成功/失败"两态 | 并发丢失更新、超时重试=重复施加、崩溃后"是否已生效"不可判定 |
| 3 | **反馈没有独立验证** | `finish` 由 LLM 自报；投影无对账；UI 写后不回读；变更流只记成功且有 `changes` 的动作 | 幻觉/空转/写错对象都无法被系统发现，"完成"是声明不是观测 |
| 4 | **权限与边界没有闭合** | `viewer` 可驱动 LLM 写世界；Agent 路径绕过 `InvokeCapability` 的 actor 与审计；HTTP `user` 自报、请求体可注入 provider | 不可追溯的写、数据外带面、越权动作 |

**优先级判断**：P0 不是"再加功能"，而是**补三条底层契约**——状态时间化（版本/时间/来源）、执行幂等化（幂等键/三态结果/超时）、动作验证化（后置条件/对账/回读）。这三条不补，上层的任何"智能控制"都只能建立在"看不见、说不准、退不回"的世界之上。

---

## 一、分析方法：先把系统画成回路，再逐点检查

### 1.1 控制论的最小闭环

《工程控制论》讨论的不是"某个装置如何自动运行"，而是**如何用有限的观测与控制手段，让一个受扰系统持续按预定目标运行**。最小闭环含六个要素：

```text
    r（目标/设定值） ──▶ 比较器 e=r−y ──▶ 控制器 ──▶ 执行器 u ──▶ 被控对象 ──▶ y（输出）
                              ▲                                              │
                              └──────────── 观测器/传感器（含噪声与时滞） ◀──┘
```

控制论对每个要素都提出工程判据，而不是只要求"有":

| 要素 | 判据 | 本系统检查点 |
|---|---|---|
| 目标 r | 可量化、可判定达成 | 有无目标/阈值/SLO 实体 |
| 模型 | 能预测"输入→动态→输出"，并声明假设 | 本体是否含行为/不变量，能力是否声明效果 |
| 观测 y | 有量纲、有采样时刻、有精度、可分辨"没变"与"没看到" | 状态版本/时间/来源、分页、聚合、指标 |
| 执行器 u | 有行程限幅、有速率限制、有回执、可重试 | 超时/配额/幂等/CAS/补偿 |
| 反馈 | 由**独立测量**给出误差，而非执行者自报 | 动作后验证、对账、变更流 |
| 稳定性 | 扰动下有界、可恢复；环路时滞小于响应时间 | 超时、熔断、背压、锁粒度 |

### 1.2 本系统的回路映射

把代码映射到回路，得到下面这张图（实线为已接线的数据流，虚线为**设计里有、实现里没有**的通道）：

```text
 业务目标（人）                      ← 断点 1：没有目标/阈值/SLO 实体（r 缺失）
      │
      ▼
 ┌────────────┐  提议(JSON)   ┌─────────────┐  执行   ┌──────────────┐  作用  ┌────────────┐
 │ LLM/规则    │ ───────────▶ │ 平台裁决/审计 │ ─────▶ │ Capability   │ ─────▶ │ 业务世界    │
 │ 控制器提议者 │ ◀─────────── │ （无逐动作   │        │ workflow/agent│        │ Kernel 状态 │
 └────────────┘  观测(≤4000字)│  授权、无验证）│        └──────────────┘        └─────┬──────┘
      ▲                       └─────────────┘                                       │
      │                                                                             │
      └╌╌╌╌╌╌╌╌╌╌ 设计声称的 Change→AI 回边：实现未接线（observe_changes 只服务 UI/CLI）╌╌╘
                                                                                    │
   外部数据源 ──▶ Projection（全量快照/无水位/无删除/无对账）──────────────────────────┘
                                                                                    │
   观测通道：get / find / related / describe（无时间、无版本、无分页、无指标） ◀──────┘
```

对应关系与"缺什么"：

| 控制论角色 | 代码对应物 | 证据 | 缺什么 |
|---|---|---|---|
| 设定值 r | `AppAgent.goal`（自然语言）、能力入参 | `frontend/src/api.ts:63`；`domain/capability/model.py` | 可计算的**目标值/阈值/误差带**；成功判据 |
| 被控对象 | Kernel `objects`/`relations` 两表 + 外部业务系统 | `ontoRK/.../sqlite.py:25-40` | 版本、时间、来源；状态不变量 |
| 观测器 | `get/find/related/describe`；change log；audit | `ontoRK/.../runtime/runtime.py:62-155`；`kernel_gateway.py:397-429` | 采样时刻、分页/聚合、质量指标 |
| 估计器 | Projection（映射） | `application/data_usecases.py:105-155` | 增量、水位、去重、对账 |
| 控制器 | 能力编排 + Agent + LLM | `agent_runner.py:66-119` | 结构化目标、预算、独立裁决 |
| 执行器 | `ctx.set/link/unlink` → Mutation；`invoke` | `ontoRK/.../execution.py:128-146`；`runtime.py:161-213` | 限幅、速率、幂等、CAS、超时 |
| 反馈 | `FunctionResult.changes`、change log | `execution.py:60-74`；`kernel_gateway.py:401-413` | 独立验证、失败事件、`execution_id` 关联 |

### 1.3 十条判据的评分

| 维度 | 控制论要求 | 本系统现状 | 判定 |
|---|---|---|---|
| D1 目标 | 设定值可量化、可判定 | 只有自然语言 goal 与表单参数 | ✗ 无 |
| D2 模型 | 结构 + 动态 + 约束 | 有结构本体（类型/属性/关系）；无行为模型、无状态不变量；`cardinality` 声明未执行 | ◐ 半 |
| D3 观测 | 时间、精度、可分页、可分辨缺失 | 有值；无版本/时间/来源；`find` 全表返回；过滤器类型错误静默返回空 | ◐ 半 |
| D4 可控 | 行程/速率/回执/可重试 | 能改属性与关系；无 CAS、无幂等、无超时、无配额；无受控 create/delete | ◐ 半 |
| D5 反馈 | 独立测量误差 | `finish` 自报；投影无对账；UI 不回读；变更流只记成功 | ✗ 无 |
| D6 稳定 | 扰动有界、可恢复 | 无超时/熔断/背压；单把全局锁；`_changes` 无界 | ✗ 无 |
| D7 时滞 | 采样周期与回路时滞匹配 | 同步 HTTP；LLM 120s vs 前端 15s；轮询 2.5s；无水位/新鲜度 | ✗ 无 |
| D8 噪声 | 估计/滤波/置信 | LLM 输出无校验；多源写 last-write-wins；无置信/来源 | ✗ 无 |
| D9 自适应 | 模型变化可检测、可迁移 | 发布即全量替换，无版本/迁移/漂移检测 | ✗ 无 |
| D10 容错/大系统 | 冗余、补偿、分层协调 | 单实例单锁；无补偿；工作区隔离好但无配额与协调 | ✗ 无 |

**结论：0 个维度完整通过，3 个半通过，7 个不通过。** 这与"design.txt 覆盖 92%"并不矛盾——架构契约完成度衡量的是"该有的零件有没有"，控制论衡量的是"零件之间有没有闭合的因果链"。

### 1.4 一个关键区分：本体模型 ≠ 控制模型

用户原文中的三分法在代码里有精确对应，值得单独强调：

| 模型类型 | 描述什么 | 本系统对应物 | 状态 |
|---|---|---|---|
| 本体模型 | 业务对象、属性、关系、约束 | `Ontology` / `ObjectType` / `Relation`（`domain/ontology/model.py`） | **有**，且质量不错 |
| 动态/因果模型 | 状态如何随时间演化，动作产生什么后果 | 能力的 Python 源码/工作流步骤（隐式规则） | **弱**：规则埋在可执行代码里，不可查询、不可验证、不可声明 |
| 控制策略 | 根据状态与目标选择动作 | 人的判断 + Agent 的 LLM 提议 | **缺**：没有可判定的策略对象，没有效果验证 |

这正是当前系统"看起来像控制、实际上没有控制"的根因：**本体提供了 `y` 的词汇表，但没有提供 `dy/dt` 和 `u→y` 的关系**。没有后者，稳定性、超调、时滞、增益这些概念就无从谈起。

---

## 二、总体判断：能跑，但还没有闭环

### 2.1 做对的三件事（必须保留）

1. **提议与执行分离**。LLM 只能通过 `invoke_capability` 提出动作，动作必须落到已发布能力，最终由 Kernel 提交（`agent_runner.py:151-164`；`design.txt:946-989`）。这是全项目最正确的控制论决策：**控制器输出必须经过执行器才作用于被控对象**。
2. **语义状态单一事实源**。Platform 不持有第二份业务状态；`publish_schema()` 只换定义不换状态；Projection 是唯一的数据入口（`kernel_gateway.py:8-20,136-171,268-357`）。这避免了"双状态同步地狱"，是可靠观测的前提。
3. **防腐层与领域边界**。Platform 只有 `KernelRuntimeGateway` 认识 Kernel；Domain 零基础设施依赖（`bootstrap.py`；`completeness-review.md` 已验证）。这让后续加"时间/版本/指标"时，改动可以收敛在少数文件。

### 2.2 四条主断点（本报告的主线）

**断点 A：状态没有时间。**
`Object` 只有 `ref + properties`（`ontoRK/.../core/state.py:14-27`），没有 `version/updated_at/source`；`Change` 没有时间戳、没有 `execution_id`、没有 `seq`（`core/execution.py:37-49`）；SQLite 只有两张业务表（`infrastructure/sqlite.py:25-40`）。平台为了做 UI 观测，只能在内核之外自建 `_changes + _seq + jsonl`（`kernel_gateway.py:112-119,401-429`）——**观测通道的时间基准不在被观测系统内**，这本身就说明内核缺了底座职责。

**断点 B：执行没有门限。**
`set` 是无条件覆盖（`memory.py:98`、`sqlite.py:161-165`），没有版本/条件写；`execution_id` 是随机串、不落库（`runtime/runtime.py:177`），因此没有幂等；Python 函数在进程内直接执行，无超时无沙箱（`python_executor.py:50-51,85-98`）；平台网关用一把 `RLock` 把整个 `invoke`（含 LLM 调用）包住（`kernel_gateway.py:122,239-262`）。

**断点 C：反馈没有独立验证。**
Agent 的 `finish` 一出现即采纳（`agent_runner.py:100-106`），`AgentRun.ok` 默认 `True`（`domain/intelligence/model.py:75`）；投影只返回计数，不与源对账（`data_usecases.py:147-155`）；控制台只显示后端自报的 `changes`（`frontend/src/views/Capability.tsx:216-219`）；变更流只在 `changes` 非空时记录（`kernel_gateway.py:255-256,355-357`），"失败"与"执行了但没变化"都不可见。

**断点 D：权限与边界没有闭合。**
HTTP 的 `user` 来自查询串/请求体，默认 `"admin"`（`interfaces/api.py:205,285`）；应用 Agent 只需 `viewer` 即可让 LLM 调用写能力（`application_usecases.py:144`），且 Agent 直接调 `gateway.invoke`，绕过 `InvokeCapability` 的 `require` 与审计（`agent_runner.py:151-156` vs `capability_usecases.py:115-131`）；请求体还能指定任意 `base_url/api_key/script` 作为 LLM provider（`api.py:399-411`）。

---

## 三、分层诊断

> 每条格式：**现象 → 证据 → 控制论解释 → 后果**。P0/P1/P2 定级见第四节。

### 3.1 目标层：没有设定值

**现象**：系统里没有任何"目标/阈值/期望值"实体。`Application` 只有 `View/Query/Agent`（`domain/application/model.py`）；`QuerySpec` 只有 `object_type/filters/expand`（`frontend/src/api.ts:61-63`）；`AppAgent.goal` 是自然语言字符串，只用于提示词；后端全仓检索 `kpi|metric|threshold|setpoint|target_value` 零命中。

**控制论解释**：没有参考输入 `r`，就没有误差 `e = r − y`。系统能回答"现在是什么"，不能回答"差多少、是否越界、该不该动作"。控制律的第一件事（何时动作）被完全推回给人。

**后果**：所谓"应用"退化为"更好看的表查询"；告警与处置只能靠人肉巡表；无人能证明"控制是有效的"——因为没有可判定的目标。

### 3.2 模型层：只有结构，没有动态与不变量

**现象与证据**：
- 本体只声明"有什么"（类型/属性/关系），不声明"什么状态组合是允许的"。`RelationDef.cardinality` 被解析、被 describe（`core/schema.py:45`；`loader/__init__.py:94`；`runtime.py:78,118`），但链接操作从不检查基数（`runtime.py:246-263`）。
- 业务规则全部埋在能力实现里：Python 源码（`examples/vehicle/functions/pass_station.py:19-28`）或 workflow 步骤；平台无法查询、无法校验、无法在发布时证明其正确性。
- 发布不做状态迁移：`publish_schema` 保留 store、换掉 runtime（`kernel_gateway.py:168-171`），但从不检查既有对象是否满足新 schema；`schema.version` 只是元数据（`core/schema.py:52-56`），不落库、不比对。

**控制论解释**：控制论意义上的"模型"必须能预测"动作 → 下一状态"。当前模型只能做"结构匹配"，不能做"行为预测"，因此没有根据做稳定性分析，也无法在动作前评估风险。更严重的是 model-reality mismatch：**模型声明了 `cardinality`，现实允许 N 条边**；模型换版了，现实仍是旧状态。

**后果**：幽灵字段（删除属性后旧值仍可读、仍进入 Agent 上下文）、类型漂移（收窄类型后旧值成为"读到就崩"的定时炸弹）、没有 schema 指纹导致"这条观测属于哪个模型版本"不可判定。

### 3.3 观测层：有值，但没有质量

这是全系统最需要优先修的一层：**观测的质量决定控制的极限**。

**(a) 没有时间与来源。** 见断点 A。后果：无法判断新鲜度；无法对账；无法把"动作"与"效果"用因果键连起来（`execution_id` 不落库、不进 Change、也不进审计 detail——`capability_usecases.py:120-124`）。

**(b) `find` 是"全表扫描 + 全量反序列化 + 全量返回"。** SQLite 侧 `SELECT oid, properties FROM objects WHERE otype=?` 取回该类型全部行，逐行 `json.loads` 后在 Python 里过滤（`sqlite.py:84-97`），属性上无索引；Memory 侧扫全部对象（`memory.py:48-57`）；`runtime.find` 没有 `limit/offset/count`（`runtime.py:140-143`）；平台 API 原样透传（`api.py:226-251`）。**量级**：1 万对象、每对象约 300B，单次查询约 3MB 内存 + 1 万次 JSON 解析（约 10–50ms CPU）【推断：量级估算，非基准】。

**(c) 过滤会静默丢行。** `match_object` 在属性缺失时返回 `False`（`filters.py:20-21`），比较抛 `TypeError` 时也返回 `False`（`filters.py:40-41`）；而 `_check_filters` 只校验属性名与运算符名，不校验值类型（`runtime.py:316-327`）。于是 `balance__gt=abc` 返回空列表，与"确实没有对象"不可区分——**量测丢点而无告警**，控制器会把"观测缺失"误判为"目标达成"。

**(d) `related` 是 N+1 与撕裂快照。** HTTP 适配器拿到 refs 后逐个 `runtime.get` 水合（`adapters/http.py:112-118`）：1 次关系查询 + N 次单对象查询、N 次加锁、N 个独立时刻；并发写时返回的对象来自不同快照，而对象无版本号，无法检测撕裂。N=100 时约 101 次往返。

**(e) 变更流是"成功的尽力而为日志"。** 进程内 `_changes` 列表无上限，启动时全量回放 jsonl（`kernel_gateway.py:112-119,415-429`）；只在 `changes` 非空时记录（`:255-256,355-357`）；记录在锁外执行（`:255-256`），并发时事件的 `seq` 可能与提交顺序不一致；`observe_changes(after_seq)` 只支持轮询，没有保留/压缩/游标校验（`:397-399`）。

**(f) 没有任何运行指标。** 没有 `/metrics`、没有耗时、没有错误率、没有队列深度、没有投影运行历史、没有 LLM 调用成本（`api.py:148-158` 的 health 只有版本/存储/provider）。内核甚至主动关闭请求日志（`ontoRK/.../adapters/http.py:57-58`）。**控制器的增益与阻尼无从整定。**

**(g) 平台侧观测缺失。** 投影运行的"结果"只有一次 toast 与一条审计（`data_usecases.py:147-155`）；没有运行历史表、没有上次同步时刻、没有对象总量趋势。控制台因此无法显示"数据截止时间"（见 3.9）。

### 3.4 执行层：无界、无门限、无回执

**(a) 无 CAS 的 read-modify-write。** 函数读到的 `Object` 是提交前的快照（`execution.py:99-106`），算完直接覆盖（`memory.py:98`、`sqlite.py:161-165`）。并发两笔 `pass_station(balance=100, fee=60)` 都读到 100、都通过余额检查，最终余额 40、建立两条边：**本应收 120 实收 60，资损 50% + 重复执行**（`examples/vehicle/functions/pass_station.py:19-28`）。HTTP 每请求一线程（`adapters/http.py:172`），Memory store 无锁（`memory.py:27-46`）——该窗口在默认配置下必然存在。【推断：未做并发压测，但代码路径确定】

**(b) 无幂等。** `execution_id` 随机生成、不落库（`runtime.py:177`）；没有 `executions` 表、没有去重键；平台 `invoke` 也没有幂等入参（`capability_usecases.py:115-131`）。超时/崩溃后重试 = 二次扣费。

**(c) 部分提交。** 三条路径：
- `apply_delta` 先 `seed` 创建对象（立即提交，`kernel_gateway.py:341-350`），再 `apply` 变更（`:351-353`）；若后者因端点不存在失败，**创建已落库**。
- `_validate_edge` 只校验类型，不校验端点存在（`kernel_gateway.py:359-377`）——存在性检查被推迟到 store（`sqlite.py:134-148`）。
- workflow 多步骤：每步单独 `invoke` 并提交，第 k 步失败时前 k−1 步已永久生效，无补偿（`workflow_runner.py:34-54`）；外层 `FunctionResult.changes` 还被内核 `take_mutations()` 覆盖为空（`platform_executor.py:48-67`；`ontoRK/.../runtime.py:205-213`），调用方**连"改了什么"都拿不到**。

**(d) 执行器无界。** 任意 Python 在进程内执行（`python_executor.py:50-51`），首次调用还会执行模块顶层代码（`:85-98`）；无超时、无 CPU/内存上限、无导入白名单、无速率限制；`ThreadingHTTPServer` 无请求超时（`adapters/http.py:172`）。一个 `while True:` 即可永久占死工作线程。

**(e) 一把全局锁把长执行串行化。** `KernelRuntimeGateway.invoke` 在 `with self._lock` 内调用 `runtime.invoke`（`kernel_gateway.py:239-262`），而 agent 型能力会在这个锁内跑完整轮 LLM（`platform_executor.py:56-67` → `agent_runner.py:81-119`）。**一次 120 秒的 LLM 调用会让所有读写排队 120 秒**，且没有排队上限与超时。

**(f) 写原语不完整。** 只有 `set_property/link/unlink`（`execution.py:19-21,128-146`），没有 create/delete；平台为了投影能造对象，直接用 `store.seed()` 绕过 Runtime（`kernel_gateway.py:341-352`），使"写必须经 Runtime 校验"这条原则失效；workflow 的 `set_property` 对不存在的 `Type/Id` 会**创建**一个只有该属性的幽灵对象（`workflow_runner.py:98-117` → `kernel_gateway.py:287-305`），与 README"写回已存在对象"的承诺相反。

**(g) 发布失败不回滚。** `PublishOntology` 先把生命周期置为 `published` 并保存（`ontology_usecases.py:168-172`），再调 `SchemaPublisher.publish`；若编译/发布失败，本体已"发布"但运行时未发布。`SchemaPublisher` 先 `publish_schema`、后保存 payload（`ontology_usecases.py:204-214`），若保存失败，重启后运行时与仓库不一致。`PublishCapability` 同样先改生命周期再发布（`capability_usecases.py:84-94`）。

**(h) 执行器失配：改了代码，执行器没变。** 首次发布把 `source` 写盘并记录 `entrypoint`；再次发布时因 `entrypoint` 已存在而**跳过写盘**（`capability_usecases.py:74-82`），同一 function 名的路径还会互相覆盖（`file_code_store.py:13-19`）；内核执行器按 `(path, attr)` 缓存函数对象、命中即返回旧函数（`ontoRK/.../python_executor.py:79-82`）。结果是"控制动作看起来施加了，被控对象没变"——闭环增益为 0，且没有任何反馈说明"没生效"。

**(i) 发布与执行不在同一临界区。** `publish_schema` 不持 `self._lock`（`kernel_gateway.py:136-171`），而 `invoke`/`apply_delta`/`describe` 都持锁；发布瞬间可能出现"旧 runtime 校验、按新 schema 写 store"的错配，且并发发布本身没有版本互斥。

### 3.5 反馈层：有信号，没有验证

**(a) "完成"由执行者自报。** `AgentRunner` 收到 `answer` 即返回（`agent_runner.py:100-106`）；不核对动作是否成功、不核对答案是否有 observation 依据、不读回状态。`AgentRun.ok` 默认 `True`，只有超步或 provider 异常才置 `False`（`domain/intelligence/model.py:75`）。现有测试恰好演示了反例：一次失败的 `invoke_capability` 之后 `run["ok"]` 仍为 `True`（`tests/test_intelligence.py:62-76`）。

**(b) 复合绑定的 `changes` 恒空。** workflow/agent 绑定经 `PlatformExecutor` 返回 `make_result(execution_id, output)`（`platform_executor.py:48-67`），内核随后用 `take_mutations()` 的结果覆盖 `result.changes`（`runtime.py:205-213`），而复合执行的真实写入发生在内层 `gateway.invoke`（已各自记录 change log）。**调用方看到的 `changes` 是 `[]`，无法据此验证写操作。**

**(c) 投影不对账。** `RunProjection` 每次全量 fetch 全部资产 → 全量 upsert（`data_usecases.py:124-146`），没有：
- 增量游标/水位（每次都从源拉全量）；
- 删除语义（源中已删除的行，内核对象永久残留；`_links` 只加不删）；
- 源内去重/排序（同一对象多行时 last-write-wins）；
- 对账（不比较源计数与内核计数、不比较字段级差异）；
- 幂等运行键（连点两次"同步"会重复执行，虽然 upsert 本身幂等，但耗时与副作用不可控）。

字段缺失时保留旧值（`kernel_gateway.py:307-317` 只对出现的属性生成 mutation），于是**源字段停更后，内核显示的是"沉默的常量"而不是"过期信号"**。

**(d) 变更流丢掉误差项。** 失败不写 change log；"执行了但状态未变"也不写（`kernel_gateway.py:255-256`）。操作者无法区分"没点"、"点了没反应"、"点了，系统说无事可做"。审计里虽有 `ok:false + error`（`capability_usecases.py:120-124`），但审计与变更流是两个互不相连的页面，且审计 detail 不含 `execution_id`。

**(e) 控制台不回读。** 能力执行后只贴出后端返回的 `changes`（`Capability.tsx:216-219`），不调 `api.get` 读回目标对象；`api.get` 在整个前端只用于详情抽屉（`StateBrowser.tsx:82`）。反馈是**声明**而非**观测**。

### 3.6 稳定性、时滞与噪声

**时滞（D7）的四个具体错配：**
1. LLM provider 默认 120s 超时（`openai_compat.py:20,42`），而 React 控制台统一 15s 超时（`frontend/src/api.ts:114-119`），且 abort 只断 HTTP、不取消服务端线程。**动作可能已生效，界面报"失败"**，用户重试 → 重复施加。这是当前最危险的控制语义错误：把"未知结果"渲染成"失败"。
2. 投影同步是同步 HTTP 请求（`data_usecases.py:124-146`），大源上必然超过 15s，同样落入上面的陷阱。
3. 执行中持锁（见 3.4e），把"单次慢"放大成"全局慢"。
4. 变更流是 2.5s 轮询（`StateBrowser.tsx:198-202`）且默认关闭；没有推送、没有"数据截止时间"，操作者无法区分"没有变化"与"回路卡住"。

**稳定性（D6）：**
- 无超时/熔断/背压/排队上限；`max_steps` 由请求体任意指定且无上界（`api.py:373-390`），推断最坏 8×120s≈16 分钟静默执行。
- `_changes` 与 jsonl 无轮转，启动全量回放：长时间运行后内存与查询成本单调上升。
- 重启即 `seed` 且对象 `INSERT OR REPLACE`（`ontoRK/.../adapters/assembly.py:33-35`；`sqlite.py:56-59`）——**重启 = 把累积状态静默回滚到 seed 值**，且无版本号变化可供察觉。
- 默认 `memory` store + HTTP/MCP 各自 `build_runtime`（`ontoRK/.../adapters/http.py:159,172`；`mcp.py:24`），同一本体在不同进程里是**两份互不可见的世界**——观测与执行不在同一对象上，闭环无意义。
- `provider.for_workspace` 是 check-then-set，无锁（`infrastructure/runtime/provider.py:23-41`）；`ApplicationContext.gateway_for` 的"未发布则自愈发布"也是无锁的读-判-写（`application/common.py:83-105`）。并发首次访问可能创建两个网关/重复发布。

**噪声（D8）：**
- LLM 输出没有平台侧 schema 校验：未知 tool、缺参数、参数类型错误都只变成一句 `"tool error: ..."` 文本回灌给模型自纠（`agent_runner.py:129-168`）；`TOOLS_SPEC` 只是提示词（`:24-37`）。
- 观测以裸文本拼进 user 消息（`agent_runner.py:113-116`），业务数据里的指令性文本可以改变后续动作（提示注入）。
- `find` 静默只取前 20 条（`agent_runner.py:141`），`_dump` 在 JSON 中间截断 4000 字符（`:171-175`）——控制器拿到的是**坏帧与有偏采样**，且不知道丢失了多少。
- 多源写同一属性时无优先级、无置信、无冲突检测：last-write-wins。
- `cardinality` 未执行、`datetime` 以字符串比较（`core/types.py:53-59`；`filters.py:28-37`）——时间过滤会因格式/时区差异静默错序。

### 3.7 自适应与演进（D9）

- **模型变更无迁移**：重发布换 schema、保留 state，但从不校验旧状态（见 3.2）。没有 schema 指纹写入 store，无法判断"这条观测属于哪个模型版本"。
- **发布不可回滚**：生命周期先行、发布后行（3.4g）；`deprecate/revise` 状态机存在但未暴露到 API/CLI（`completeness-review.md` §A2），所以演进只能"新建工作空间"。
- **无漂移检测**：没有"数据源停更/投影失败率上升/schema 漂移/LLM 质量下降"的检测与告警；没有影子模式与 canary。
- **LLM 无自适应**：只有 `max_steps` 一个旋钮；没有根据历史成功率调整提示/工具/预算。

### 3.8 大系统与协调（D10）

**做对的**：Workspace 作为隔离容器，每个工作区独立 Kernel 实例（`provider.py:16-42`），资源与状态边界清晰；这为"分层协调"提供了正确的第一刀。

**不足**：
- 协调手段只有"隔离"，没有"协调"：无跨工作区配额、优先级、限流；无局部目标与全局目标的一致性检查。
- 单实例单锁 + SQLite 单连接（`sqlite.py:46-47`）决定了吞吐上限；多实例部署没有租约/leader 选举，两个进程同时写同一 db 会以 `database is locked` 结束，且该异常被映射成 422（写）或连接错误（读）（`sqlite.py:119-130`；`runtime.py:209-212`；`http.py:144-147`）。
- `_changes` 的序号是进程内内存值（`kernel_gateway.py:401-406`），多实例下无法作为全局游标。

### 3.9 人机接口（控制台）：状态展示器，不是 HMI

判据是 HMI 必须闭合四件事：**看得见状态、比得出偏差、发得出动作、验得回效果**。

| 回路要素 | 现状 | 判定 |
|---|---|---|
| 参考输入 r | 无目标/阈值实体 | ✗ |
| 比较器 e=r−y | 人脑 | ✗ |
| 执行 u | 能发（能力/投影/发布） | ◐ |
| 测量 y | 有值，无采样时刻 | ◐ |
| 反馈确认 | 仅 toast + 后端自报 changes | ✗ |

具体证据：
- **无新鲜度**：全前端无 `Date.now/new Date/updatedAt/asOf` 逻辑；各页只在挂载时一次性拉取（`ui.tsx:19-27`）；唯一周期观测是 20s 探活（`store.tsx:159-164,220`），只证明 HTTP 入口活着。
- **超时语义错误**（见 3.6）。
- **无回读**（见 3.5e）。
- **"系统自检"含伪造测点**：`Settings.tsx:105-107` 的"版本/定义存储"两行恒为 `yes(true)`；"自检模型连通性"只读 `/api/health` 的 `llm_configured`，不真连模型（`LlmPicker.tsx:47-56`）。
- **变更流可能静默失效**：游标存组件 ref、只增不减；memory 运行时重启后 `seq` 归零，而客户端仍持旧高位 → 永久显示"暂无变更"，不报错（`StateBrowser.tsx:180,197`；`provider.py:27-32`；`kernel_gateway.py:397-399`）。
- **错误不可分类**：`ApiError` 保留 `status/type`，但全前端无一处读取，`friendly()` 压成一句业务话术（`api.ts:82-112`；`store.tsx:302`）。
- **五步主流程是"上线清单"不是"控制环"**（`Workbench.tsx:58-85`）：上线之后（真正需要控制的时候）没有视图承接。

---

## 四、问题清单与优先级

定级口径：**P0 = 会让调用方/审计得到错误事实，或并发/超时下产生不可逆的错误写入**；P1 = 显著削弱可观测性/可运维性/可演进性；P2 = 演进与规模问题。

### P0（建议立即修，按依赖排序）

| ID | 问题 | 证据 | 后果 |
|---|---|---|---|
| P0-1 | 状态无版本/时间/来源，Change 无 `ts/execution_id/seq` | `core/state.py:14-27`；`core/execution.py:37-49`；`sqlite.py:25-40` | 无法判新鲜度/对账/归因；平台被迫自建日志 |
| P0-2 | 无条件写 + read-modify-write，无 CAS | `runtime.py:190-208`；`sqlite.py:150-172`；`memory.py:92-105` | 并发丢失更新；示例业务资损 50% + 重复建边 |
| P0-3 | 无幂等键/无执行记录，`execution_id` 不落库 | `runtime.py:177`；`execution.py:37-49`；`capability_usecases.py:115-131` | 超时/崩溃重试 = 重复扣费 |
| P0-4 | 15s 客户端超时 vs 120s LLM；abort 不取消服务端 | `frontend/src/api.ts:114-119`；`openai_compat.py:20,42`；`api.py:416` | 动作已生效却报失败 → 重试 → 重复施加 |
| P0-5 | 混合绑定的 `changes` 恒空 | `platform_executor.py:48-67`；`runtime.py:205-213` | 调用方无法验证写操作 |
| P0-6 | 投影两阶段提交（先 seed 后 apply），失败留残 | `kernel_gateway.py:340-353`；`sqlite.py:134-148` | 部分写入，状态与源不一致 |
| P0-7 | 启动即 seed + `INSERT OR REPLACE` | `ontoRK/.../assembly.py:33-35`；`sqlite.py:56-59` | 重启静默回滚累积状态 |
| P0-8 | 权限/审计边界：viewer 可写、Agent 绕过 InvokeCapability、user 自报、provider 注入 | `application_usecases.py:144`；`agent_runner.py:151-156`；`api.py:205,285,399-411` | 越权写、不可追溯、数据外带/SSRF |
| P0-9 | 全局锁覆盖 LLM/任意 Python 执行 | `kernel_gateway.py:122,239-262`；`platform_executor.py:56-67` | 单次慢调用阻塞全工作区；死循环拖死进程 |
| P0-10 | `set_property` 目标不存在时创建幽灵对象 | `workflow_runner.py:98-117`；`kernel_gateway.py:287-305` | 拼错 ref 污染状态与后续查询 |
| P0-11 | 能力源码重发布静默不生效：`entrypoint` 已存在即跳过写盘，内核按路径缓存函数 | `capability_usecases.py:74-82`；`file_code_store.py:13-19`；`ontoRK/.../python_executor.py:79-82` | 执行器没变而平台认为已更新，改逻辑后线上行为不变，直到重启 |

### P1

| ID | 问题 | 证据 |
|---|---|---|
| P1-1 | `find` 全表扫描/全量返回；无分页/计数/聚合；过滤类型错误静默返回空 | `runtime.py:140-143`；`sqlite.py:84-97`；`filters.py:20-21,40-41` |
| P1-2 | `related` N+1 + 撕裂读；HTTP/MCP 语义漂移（两份测试互相矛盾） | `http.py:112-118` vs `mcp.py:68-71`；`test_http.py:61-64` vs `test_mcp.py:87-90` |
| P1-3 | 执行器任意 Python、无超时/沙箱/配额 | `python_executor.py:50-51,85-98` |
| P1-4 | `store.apply/seed` 公开且只查存在性；平台已绕过 | `ports/state_store.py:34`；`kernel_gateway.py:341-352` |
| P1-5 | 错误不可判定：无码/阶段/重试性；成功/失败两态（缺"未知"） | `runtime.py:200-213`；`serialize.py:58-59`；`http.py:144-147` |
| P1-6 | 变更流无界、无压缩、无游标校验；只在有 changes 时记录；锁外记录 | `kernel_gateway.py:255-256,355-357,397-429` |
| P1-7 | 投影无增量/水位/删除/对账/运行历史；字段停更显示旧值 | `data_usecases.py:124-155`；`kernel_gateway.py:307-317` |
| P1-8 | 发布失败不回滚（生命周期先行；payload 后存） | `ontology_usecases.py:168-172,204-214`；`capability_usecases.py:84-94` |
| P1-9 | Agent `finish` 自报；行动后不复查；`ok` 是存活标志 | `agent_runner.py:100-106`；`model.py:75` |
| P1-10 | workflow 无跨步事务/补偿/幂等/每步超时 | `workflow_runner.py:34-54`；`ports/execution.py:23-24` |
| P1-11 | Agent 观测有损无优先级（4000 字符、前 20 条、无 token 计量） | `agent_runner.py:22,141,171-175` |
| P1-12 | provider 无重试/错误分类/usage/max_tokens；有 key 无 URL 静默回退公网 | `openai_compat.py:13-45`；`factory.py:13-35` |
| P1-13 | 默认 memory + 多入口多世界；无跨进程协调 | `http.py:159,172`；`mcp.py:24`；`sqlite.py:46-47` |
| P1-14 | gateway/provider 初始化竞态 | `provider.py:23-41`；`common.py:83-105` |
| P1-15 | 控制台无数据时刻；自检伪造测点；变更流游标失效；无回读 | `ui.tsx:19-27`；`Settings.tsx:105-107`；`StateBrowser.tsx:180,197`；`Capability.tsx:216-219` |
| P1-16 | `publish_schema` 不持锁，发布与执行/读取不在同一临界区 | `kernel_gateway.py:136-171` vs `:186-187,239,274` |
| P1-17 | 同一 delta 内重复 `Type/Id` 会生成两个 `Object`（校验只看已落库状态） | `kernel_gateway.py:303,341-350` |
| P1-18 | 接口层错误语义压平：`int()` 参数非法 → 500；`invoke` 业务失败仍 200；`body` 无大小上限 | `api.py:86-92,125-131,259-262,340-343` |

### P2

| ID | 问题 |
|---|---|
| P2-1 | `cardinality` 声明未执行；`datetime` 字符串比较 |
| P2-2 | 平台/内核 schema `version` 闲置，无指纹与 drift 报告 |
| P2-3 | `_cache` 按路径缓存 Python 函数，无版本/哈希 |
| P2-4 | AskOntology 无匹配时回退前 10 个对象；仅一跳；property 命中被丢弃 |
| P2-5 | `RunAppQuery` 无分页/N+1；`AddQuery` 只校验 object_type |
| P2-6 | 审计无分页/筛选；无 `execution_id` 关联；变更流与审计割裂 |
| P2-7 | MCP `agent_run` 未接 provider；`workspace_ask` 固定 `admin` |
| P2-8 | 静态文件路径用字符串前缀比较（应 `is_relative_to`），`_send_file` 全量读入内存 |
| P2-9 | `C2 自愈发布`每次入口先 `repo.get("workspace")`，冷启动时放大为每请求一次读 |

---

## 五、优化方案

> **v2 修订说明**：本节的控制论方向（先可观测、再受限执行、再独立验证、最后自适应）仍然成立；但具体排期、依赖关系、工作分解与验收已由 **`docs/plan-v2.md`** 重写——三视角复核（本体论/领域建模/TDD）后调整为"语义地基 → 领域契约 → 执行与观测 → 验证与对账 → 自适应"六条工作流，并加入测试先行门禁。**执行以 v2 为准，本节保留为控制论论证。**

### 5.1 设计四原则

1. **先可观测，再可控**：没有"时间 + 质量 + 可分辨缺失"的观测，任何控制律都是盲调。
2. **执行器必须受限**：行程（能改什么）、速率（多快）、回执（是否生效）、可逆（能否补偿）四项必须在契约里声明并被检查。
3. **反馈必须独立**：验收信号不能由执行者自报；由独立的 verifier/对账任务给出。
4. **模型演进必须可检测**：schema/阈值/代码的每次变更都要有版本、指纹与漂移检查，禁止"静默换模型"。

### 5.2 目标架构：四个回路 + 五个层

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 治理层  actor 认证 · 读写权限 · 审批 · 审计（execution_id 全链路）      │
├──────────────────────────────────────────────────────────────────────┤
│ 决策层  规则/优化器（快回路） · LLM（提议） · 裁决器（批准/拒绝）        │
├──────────────────────────────────────────────────────────────────────┤
│ 验证层  后置条件 · 效果回读 · 对账 · 误差预算 · 漂移检测                │
├──────────────────────────────────────────────────────────────────────┤
│ 执行层  受限执行器（超时/配额/沙箱） · 幂等键 · CAS · Saga/补偿         │
├──────────────────────────────────────────────────────────────────────┤
│ 观测层  状态版本/时间/来源 · 指标 · 游标化变更流 · 执行记录 · 新鲜度     │
├──────────────────────────────────────────────────────────────────────┤
│ 语义层  本体 + 状态不变量 + 能力契约（前置/后置/效果/补偿）              │
└──────────────────────────────────────────────────────────────────────┘

四个回路（按时间尺度分工）：
  快回路（ms~s）    阈值/规则/限流/熔断 —— 自动纠偏，不经过 LLM
  慢回路（min~h）   对账/漂移/容量/新鲜度 —— 发现"模型与现实"的偏差
  学习回路（d~w）   阈值整定/本体修订/能力演进 —— 人审后生效
  人工回路（例外）   审批/回滚/复盘 —— 高风险或不可逆动作
```

**关键取舍：LLM 不进快回路。** LLM 是"开放式诊断/提议器"，适合慢回路与例外处理；对延迟、吞吐、资源、重试这类有明确量化指标的问题，规则/优化器更快、更稳、更可验证。这与用户原文的判断一致：*"对于延迟、流量、资源调度等有明确量化指标的问题，经典控制算法或其他经过验证的优化策略，往往更适合作为执行层的控制机制。"*

### 5.3 三阶段路线图

#### Phase 0：补三条底层契约（2–3 周）

| 工作项 | 具体改动 | 验收（可量化） |
|---|---|---|
| 0.1 状态时间化 | `objects` 增 `version/updated_at/source`；`Change` 增 `ts/execution_id/seq`；提交时同事务写 `changes` 与 `executions` 两表 | 任意 `get` 返回版本与时间；任意变更可按 `seq` 回放；并发写冲突返回 409（`ConcurrentModification`） |
| 0.2 执行幂等化 | `POST /invoke` 接受 `Idempotency-Key`；`executions(id UNIQUE, key, function, target, status, result)`；重复键直接返回原结果；结果三态 `succeeded/definitely_failed/unknown` | 同 key 并发两次，状态只变一次；超时后可按 `execution_id` 查询真实结果 |
| 0.3 执行边界化 | 锁只保护提交（读→执行→提交三段，执行不持锁）；executor 超时（线程/进程级）与并发上限；`/agent`、`/invoke`、投影设置请求预算；`max_steps` 服务端上界 | 一个 120s LLM 调用期间读请求 P95 不劣化 >2×；死循环函数被超时终止并返回 `timeout` 错误码 |
| 0.4 观测基线 | `/metrics`（QPS、错误率、P50/P95、锁等待、投影耗时、数据新鲜度 lag、变更积压、活跃执行数）；变更流保留策略（窗口/压缩）+ 游标校验 + 失败事件 | 控制台显示"数据时刻/最后同步时刻"；看板 10 个指标可查；失败与 0 变更都能在过程流看到 |
| 0.5 修复 P0 数据正确性 | 启动 seed 仅空库执行；`apply_delta` 先校验端点存在、单事务写；`set_property` 目标必须存在；发布失败回滚生命周期；`publish_schema` 成功后才改 lifecycle | 重启不丢状态；投影失败无残留；拼错 ref 报错而非创建；发布失败后本体仍是 validated |

#### Phase 1：契约与对账（4–6 周）

| 工作项 | 具体改动 | 验收 |
|---|---|---|
| 1.1 能力契约 | `Capability` 增 `preconditions/postconditions/effects/idempotent/compensate`；发布时校验契约与实现一致；调用前检查前置条件，调用后由 verifier 执行后置条件；源码指纹（hash）进入执行器缓存键 | 每个已发布能力都有可执行的后置条件；改代码发布后下一次调用即生效（无需重启）；验证失败率可观测 |
| 1.2 投影升级 | 增量拉取（源游标/`updated_at` 水位）；删除传播（tombstone/软删）；对象级去重与冲突策略；对账任务（源计数/校验和 vs 内核）；运行历史表 | 对账差异 <0.1%；源删除后内核对象在下一次同步内消失；每次运行有耗时/行数/失败原因 |
| 1.3 workflow Saga | 步骤声明补偿动作；失败按逆序补偿；幂等键贯穿整条 workflow；每步超时 | 第 k 步失败时前 k−1 步被补偿（或明确标记为"部分成功"并告警） |
| 1.4 权限与认证 | actor 认证（至少 token）；`viewer` 不能触发写；Agent 写动作走 `InvokeCapability`（逐动作 actor 审计）；provider 仅服务端配置，请求体禁 `base_url/api_key/script`；审计含 `execution_id/actor/影响对象` | 未认证请求 401；viewer 写请求 403；每条写动作可追到 actor 与 execution |
| 1.5 执行器沙箱 | 资源/时间上限、导入白名单、每能力并发与速率配额、熔断与降级 | 死循环/内存炸弹被隔离；单能力故障不影响其它能力 |

#### Phase 2：目标与自适应（6–12 周）

| 工作项 | 具体改动 | 验收 |
|---|---|---|
| 2.1 Goal/SLO 实体 | `Application` 增 `objective{metric, target, band, window}`；视图增加"当前值/目标值/偏差/趋势"；越界产生告警/工单 | 业务页面能显示"差多少"；越界自动生成待办 |
| 2.2 分层控制器 | 快回路规则引擎（阈值/限流/重试预算）；慢回路对账/漂移任务；LLM 仅做开放式诊断与提议；提议→裁决→验证三段式 | 快回路动作不依赖 LLM；LLM 提议被拒绝率可观测 |
| 2.3 漂移与质量 | schema drift 扫描、数据源新鲜度、投影失败率、LLM 质量抽检；影子模式与 canary；自动降级 | drift 发现 <1 个同步周期；灰度期间新旧策略结果可对比 |
| 2.4 大系统协调 | 工作区配额/优先级/限流；跨工作区只读汇总；多实例租约与 leader 选举（或明确"单实例"部署契约） | 单工作区打满不影响其它工作区；多实例写冲突有明确语义 |

### 5.4 关键设计要点（实现指引）

**A. 状态时间化（内核表结构）**

```sql
-- 建议新增（SQLite）
ALTER TABLE objects ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE objects ADD COLUMN updated_at TEXT;      -- UTC ISO8601
ALTER TABLE objects ADD COLUMN source TEXT;          -- "projection:<id>" / "capability:<fn>"
CREATE TABLE IF NOT EXISTS changes (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, execution_id TEXT, source TEXT,
  kind TEXT, object_ref TEXT, property TEXT, old_value TEXT, new_value TEXT
);
CREATE TABLE IF NOT EXISTS executions (
  execution_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE,
  function TEXT, target TEXT, status TEXT,            -- succeeded|failed|unknown
  started_at TEXT, finished_at TEXT, result TEXT, error TEXT
);
```

配套：`Mutation` 增 `expected_version`；`set` 走 `UPDATE ... WHERE version=?`；冲突返回 `ConcurrentModification`（可重试错误）。`get` 返回 `{ref, properties, version, updated_at, source}`。

**B. 幂等与三态结果**

- 客户端生成/传入 `Idempotency-Key`（或复用业务幂等键，如"交易号 + 动作"）；服务端落库并去重。
- `FunctionResult` 从"成功/失败"扩为三态：`succeeded` / `definitely_failed`（可重试）/ `unknown`（超时/连接中断，需按 key 查询）。
- 前端把 `unknown` 渲染为"结果未知，正在核对"，**禁止**渲染为"失败"并允许直接重试。

**C. 观测接口契约**

```text
GET /api/{wid}/state/find?type=X&limit=50&after=<cursor>&order=<field>
  → {items:[...], total: N, next_cursor: "..."}       # 分页 + 总数
GET /api/{wid}/objects/{ref}?fields=a,b,c             # 字段裁剪
GET /api/{wid}/changes?after_seq=S&limit=500          # 游标 + 上限
GET /api/{wid}/metrics                                # 指标快照（或 /metrics Prometheus）
GET /api/{wid}/executions?status=failed&since=...     # 执行记录（排障入口）
```

`find` 的过滤操作符应下推到 SQL/索引；类型不匹配返回 `400 InvalidArgument`，**不得**静默返回空集。

**D. 投影升级（源→内核）**

```text
源表 ──(updated_at 水位 / CDC 游标)──▶ 增量批次
     ──▶ 去重(主键) + 冲突策略(源优先/时间优先)
     ──▶ upsert + tombstone(删除) + 关联边重建
     ──▶ 单事务应用（或 Saga：批次可重放，幂等）
     ──▶ 对账：源计数/校验和 vs 内核；差异>阈值告警
```

每次运行写 `projection_runs(id, projection_id, started_at, finished_at, rows_read, upserts, deletes, errors, watermark)`；控制台数据页展示"上次同步时刻 / 结果 / 差异"。

**E. 能力契约（把规则从代码里提出来）**

```yaml
capability: Vehicle.recharge
inputs: {amount: number}
preconditions:
  - amount > 0
  - Vehicle.balance + amount <= Vehicle.credit_limit
postconditions:
  - Vehicle.balance == old.balance + amount
effects: [Vehicle.balance]
idempotent: true
compensate: Vehicle.recharge_reverse    # 可选
```

发布时校验契约可执行；调用后由平台 verifier 读回对象，比较后置条件；失败则标记 `verification_failed` 并触发告警/补偿。**这是把"工程控制论"的"效果验证"落到代码里的最小机制。**

**F. 验证器与对账（独立反馈通道）**

- `Verifier` 只读、独立于执行路径；输入 `pre_state + action + post_state`，输出 `met/violated/unknown`。
- `Reconciler` 周期性比对"源世界 / 语义世界 / 外部系统"，输出差异清单（不是立即修数，而是产生待办）。
- 关键：验证信号写入 `verification` 表并进入变更流，**让"验证通过/失败"成为一等观测**。

**G. 控制分层（LLM 的正确位置）**

```text
快回路：规则/阈值/限流/重试预算 → 直接执行（不经 LLM）
慢回路：对账/漂移/容量         → 生成待办/告警（可让 LLM 归因）
提议：  LLM 诊断/候选方案       → 裁决器（规则/权限/风险）→ 执行器
人工：  不可逆/高风险动作        → 审批 → 执行 → 验证 → 复盘
```

**H. 治理闭环**

- 认证：所有写路径必须绑定真实 actor；`user` 不再从请求体自报。
- 授权：读（viewer）/写（editor）/治理（owner）分离；Agent 继承发起者的权限，且写动作必须经 `InvokeCapability`。
- 审计：每条写动作 = `actor + execution_id + 能力 + 目标 + 前后值 + 验证结果`，可按 `execution_id` 一键回查。
- 发布：生命周期与编译原子化（失败回滚），`schema_version` 落库并写入每条变更。

### 5.5 不建议做的事（避免过度工程）

1. **不要为"控制"引入完整事件溯源（Event Sourcing）**：v0.1 的"当前状态 + 变更日志"足够；把 `changes` 表做好（有 seq/ts/execution_id）即可，不必重放整个世界。
2. **不要让 LLM 进快回路**：延迟、吞吐、资源类问题用规则/优化器；LLM 只做诊断与提议。
3. **不要先拆微服务**：当前瓶颈是契约缺失而非部署形态；先补时间/幂等/验证，再谈多实例。
4. **不要把本体当控制模型**：本体解决"世界如何结构化表达"，控制需要"动态 + 因果 + 策略"三层；三者分工，不能互相替代。

---

## 六、度量与验收：从"能不能跑"到"控制得好不好"

### 6.1 控制质量指标

| 指标 | 定义 | 目标（建议） |
|---|---|---|
| 状态新鲜度 lag | `now − source_updated_at`（按对象/源） | 投影类 < 1 个同步周期；超阈值告警 |
| 观测完整率 | 有 `source/updated_at/version` 的对象占比 | 100% |
| 闭环率 | 有后置条件验证的动作 / 全部写动作 | 逐步到 100% |
| 动作验证通过率 | `met / (met+violated)` | ≥ 99%（业务可定义） |
| 未知结果率 | `unknown` 调用 / 全部调用 | < 0.1%，且 100% 可查 |
| 幂等重试率 | 命中幂等键的请求占比 | 可观测即可（反映重试健康度） |
| 投影对账差 | `|源计数 − 内核计数| / 源计数` | < 0.1% |
| 变更积压 | `seq_now − seq_consumed` | < 1000，持续上升告警 |
| 控制时延 P95 | 事件发生 → 动作完成 | 按场景定义（快回路秒级） |
| 振荡/超调 | 同一目标在窗口内被反复修正的次数 | 伸缩/重试场景 < 3 次/小时 |
| 控制代价 | 每次闭环的 token/调用/资源消耗 | 有预算、可归因 |

### 6.2 故障注入与实验（Phase 0 起就应具备）

| 场景 | 注入方式 | 期望行为 |
|---|---|---|
| 提交中崩溃 | kill -9 于 `apply` 前后 | 重启后状态一致；`executions` 可判定 |
| 并发双花 | 两线程同时 `pass_station` | 一个成功、一个 `ConcurrentModification`；余额正确 |
| LLM 超时 | provider 延迟 120s | 请求返回 `unknown`；服务端可查结果；不阻塞其它请求 |
| provider 429/5xx | 注入错误 | 有界重试 + 退避；最终 `definitely_failed`；不重复写 |
| 源数据重复/乱序/删除 | 构造源表 | 投影幂等；删除传播；对账差异=0 |
| schema 收窄 | 发布删除属性 | 发布前 drift 报告；旧值不被静默读取 |
| 死循环能力 | 注入 `while True` | 超时终止；线程/进程不被拖死 |
| 变更流滞后 | 停止消费再恢复 | 游标可续；积压可见；无静默丢事件 |

### 6.3 一页验收清单（Phase 0 结束时应全部为"是"）

- [ ] 任意对象可回答"这个值是什么时候、由谁、通过什么动作改的"
- [ ] 同一幂等键重复调用，世界只变一次
- [ ] 超时请求可区分"失败/未知"，并能按 `execution_id` 查到真实结果
- [ ] 并发写冲突被显式拒绝（409），而不是静默覆盖
- [ ] 投影失败不留残；源删除会传播；每次同步有运行记录与对账结果
- [ ] 变更流包含失败事件；游标可续；有上限与压缩
- [ ] 控制台每个数据区显示"数据时刻"；写后有回读对比
- [ ] `viewer` 无法触发写；每条写动作可追到 actor 与执行
- [ ] 一个慢/坏能力不会阻塞其它工作区请求
- [ ] 改一个能力的源码并发布后，下一次调用执行的是新代码（无需重启进程）

---

## 七、与《工程控制论》的对应

| 书中主题 | 控制论概念 | 本系统对应问题 | 优化落点 |
|---|---|---|---|
| 上册：系统建模、传递函数、状态空间 | 模型必须能预测动态 | 只有结构本体，无动态/不变量 | 能力契约、状态不变量（Phase 1.1） |
| 上册：稳定性分析 | 扰动后有界/收敛 | 无超时/熔断/背压；锁内长执行 | Phase 0.3、1.5 |
| 上册：静态精度分析 | 稳态误差 | 无对账、无新鲜度 | Phase 1.2、6.1 |
| 上册：能观测性/能控性 | 能否看见、能否改变 | `find` 无分页、无时间；写原语不完整 | Phase 0.1/0.4、1.1 |
| 上册：参数设计与协调控制 | 多变量耦合下的整定 | 无协调层，单锁串行 | Phase 2.2/2.4 |
| 上册：非线性、最速控制 | 饱和/限幅/时间最优 | 执行器无界；无预算 | Phase 0.3、1.5 |
| 下册：随机输入与噪声过滤 | 估计与滤波 | LLM 噪声、多源写冲突、观测截断 | Phase 2.3；观测升级 |
| 下册：最优控制 | 在约束下最优 | 无目标函数、无代价模型 | Phase 2.1 |
| 下册：自适应控制 | 模型变化后仍达标 | 无漂移检测、无迁移 | Phase 2.3 |
| 下册：冗余与容错 | 失效后维持功能 | 无补偿、单点、重启回滚 | Phase 0.5、1.3 |
| 下册：大系统 | 分层/协调/信息结构 | 有隔离无协调；单实例 | Phase 2.4 |

---

## 八、风险与取舍

1. **加版本/幂等会增加写路径成本**：每次写多一次版本检查与一行执行记录。对当前规模可忽略；若未来写吞吐成为瓶颈，再考虑批量提交与异步日志，但**不能**回到"无版本"。
2. **对账与验证会暴露大量既有不一致**：这是好事（先看见再修），但要给人一个"差异待办"的处置流程，否则会变成噪声。
3. **LLM 的角色被压缩**：把快回路交给规则会让"AI 原生"的叙事变弱，但换来可验证性；LLM 的价值在开放式诊断与提议，不在高频闭环。
4. **多实例是"以后再说"**：在契约补齐前强行多实例，只会把竞态从进程内搬到进程间。建议先明确"单实例 + 持久化"的部署契约，再引入租约/队列。
5. **不要破坏 v0.1 的边界声明**：README 中声明的有意简化（Review 折入 publish、Test 由 pytest 承担、Mapping 折入 Projection）应保留；本方案新增的是"控制契约"，不是"更多功能模块"。

---

## 附录 A：证据索引（按文件）

| 文件 | 关键结论 |
|---|---|
| `ontoRK/src/ontology_runtime/runtime/runtime.py` | `invoke` 管线；validate-then-apply（:222-227）；`execution_id` 不落库（:177）；`changes` 被覆盖（:205-213）；错误吞并（:183-213）；`find` 无分页（:140-143） |
| `ontoRK/.../core/execution.py` | Mutation/Change/FunctionResult 无时间/seq/execution_id（:37-74）；写原语仅三种（:19-21,128-146） |
| `ontoRK/.../core/state.py` | Object 只有 ref+properties（:14-27） |
| `ontoRK/.../infrastructure/sqlite.py` | 两表无版本/时间（:25-40）；单连接+锁（:46-47）；`seed` INSERT OR REPLACE（:53-70）；apply 事务（:119-130）；find 全表（:84-97） |
| `ontoRK/.../infrastructure/memory.py` | 无锁、浅拷贝/写入别名（:27-46,98）；非原子 apply（:68-78） |
| `ontoRK/.../infrastructure/python_executor.py` | 任意 Python、无沙箱/超时（:50-51,85-98） |
| `ontoRK/.../infrastructure/filters.py` | 缺属性/类型错误静默 False（:20-21,40-41） |
| `ontoRK/.../adapters/assembly.py` | 启动无条件 seed（:33-35） |
| `ontoRK/.../adapters/http.py` | related N+1 水合（:112-118）；ThreadingHTTPServer（:172）；默认 memory（:159,183） |
| `ontoPlantform/.../infrastructure/runtime/kernel_gateway.py` | 全局 RLock 覆盖 invoke（:122,239-262）；apply_delta 两阶段（:268-357）；变更流（:397-429）；`_changes` 无界回放（:112-119,415-429） |
| `ontoPlantform/.../infrastructure/runtime/provider.py` | check-then-set 竞态（:23-41） |
| `ontoPlantform/.../application/common.py` | 自愈发布竞态（:83-105） |
| `ontoPlantform/.../application/data_usecases.py` | 全量投影、无水位/删除/对账（:105-155） |
| `ontoPlantform/.../application/ontology_usecases.py` | 生命周期先行（:159-173）；发布-保存时序（:182-223） |
| `ontoPlantform/.../application/capability_usecases.py` | 发布先置生命周期（:84-94）；invoke 审计无 execution_id（:115-131） |
| `ontoPlantform/.../application/agent_runner.py` | finish 自报（:100-106）；观测截断（:22,171-175）；find 前 20（:141）；工具无 schema（:129-168） |
| `ontoPlantform/.../infrastructure/execution/platform_executor.py` | 三类绑定；changes 丢弃（:48-67） |
| `ontoPlantform/.../infrastructure/execution/workflow_runner.py` | 无跨步事务（:34-54）；set_property 直写（:98-117） |
| `ontoPlantform/.../infrastructure/llm/openai_compat.py` | 120s 单次 urlopen；无重试/分类/usage（:13-45） |
| `ontoPlantform/.../interfaces/api.py` | user 自报（:205,285）；provider 注入（:399-411）；无分页/超时（:226-263）；health 单薄（:148-158） |
| `ontoPlantform/.../interfaces/mcp.py` | 固定 admin（:100-103）；agent_run 未接 provider（:117-121） |
| `frontend/src/api.ts` | 15s 统一超时（:114-119） |
| `frontend/src/store.tsx` | 20s 探活（:220）；toast 4s/9s（:152-157） |
| `frontend/src/views/StateBrowser.tsx` | 2.5s 轮询（:198-202）；游标只增（:180,197） |
| `frontend/src/views/Settings.tsx` | 伪造自检行（:105-107） |
| `frontend/src/views/Capability.tsx` | 无回读（:216-219） |

## 附录 B：术语对照

| 控制论 | 本系统 |
|---|---|
| 设定值 r / 参考输入 | 目标值、SLO、期望状态（**当前缺失**） |
| 输出 y / 被控量 | Kernel 对象属性、关系、外部业务状态 |
| 误差 e = r − y | 目标值与当前值的差（**当前靠人脑计算**） |
| 控制器 | 规则引擎 / 优化器 / LLM 提议器 |
| 执行器 u | `invoke` 能力 / `apply_delta` / workflow |
| 传感器 | `get/find/related/describe` + change log + audit |
| 扰动 | 源数据变化、并发、超时、模型漂移、LLM 噪声 |
| 时滞 | 投影同步延迟、LLM 响应、锁等待、采样周期 |
| 反馈 | 后置条件验证、对账、效果回读（**当前未接线**） |
| 稳定性 | 超时/熔断/背压/幂等（**当前缺失**） |
| 自适应 | drift 检测、schema 迁移、策略整定（**当前缺失**） |
| 冗余/容错 | 补偿动作、降级、多副本（**当前缺失**） |

---

> **最终判断**：OntoPlatform 的架构骨架是对的，且已经具备"本体驱动动作"的完整链路；它现在缺的不是更多功能，而是控制论意义上的**三件底层承诺（时间、门限、回执）与一条独立验证通道**。补上这三件，平台才能从"能执行动作"走向"能持续达成目标"；不补，则任何上层智能都只能在开环世界里运行。
