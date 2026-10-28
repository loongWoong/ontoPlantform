# OntoRK 内核层诊断：以工程控制论为镜

> 对象：`F:\piAgent\ontoRK`（Ontology Runtime Kernel v0.1.1，src 约 2.5k 行）
> 方法：全量静态阅读（runtime / core / ports / infrastructure / loader / adapters / tests），未修改源码、未启动服务、未安装依赖。
> 路径约定：内核路径相对 `ontoRK/src/ontology_runtime/`（如 `runtime/runtime.py:222`）；测试相对 `ontoRK/`；平台路径相对 `F:\piAgent\`。
> 标签约定：**【设计取舍】**= v0.1 有意简化；**【实现缺陷】**= 与自身声明的契约或控制目标不符；**【设计取舍但代价被低估】**；【推断】= 静态推理、未运行验证。

## 1. 内核在控制回路中的位置

内核自我定义为"运行时底座"：Schema 描述世界、State 保存世界、Function 改变世界、Change 描述世界发生了什么（README:10-16）。从控制论看，它是一个**单拍、无时间基准、执行器外置的指令执行机构**：闭环所需的"测量—比较—校正"三件事被整体推给上游（Agent / 平台 / 人），内核本身既不提供可信测量（无版本、时间戳、新鲜度），也不提供可安全重试的执行（无幂等、无提交回执、结果未知态缺位），更不提供误差信号（Schema 是描述性的，不含任何状态不变量）。典型本体控制回路在这里被切成两段：

```text
              ┌──────────────── 控制器：Agent / 平台 / 人 ────────────────┐
              │ 目标：Schema 声明的对象·属性·能力（+ 函数里的隐式业务规则）│
              └───┬───────────────────────────────────────────▲──────────┘
   invoke(能力,目标,参数)                                        │ FunctionResult
                  │                                              │ {output, changes, error}
                  ▼                                              │
  ┌──────────────────────────── 内核 ─────────────────────────────┴────┐
  │ 执行器 PythonFunctionExecutor   任意 Python、无沙箱/无超时/无配额   │
  │      │ ctx.set / ctx.link / ctx.unlink → Mutation 缓冲（唯一写入口）│
  │      ▼                                                             │
  │ 提交点 OntologyRuntime._apply_mutations：validate → store.apply   │
  │      │                                                             │
  │      ▼                       观测器 get / find / related           │
  │ 被控对象 StateStore：objects(otype,oid,properties) + relations      │
  └──────────────────────────────┬──────────────────────────────────────┘
                                 │ seed（每次启动）+ apply（每次提交）
                                 ▼
                持久层：Memory（进程内，HTTP/MCP 默认） / SQLite 文件
```

### 1.1 控制论角色 → 内核对应物

| 角色 | 内核对应物 | 证据 | 缺什么 |
|---|---|---|---|
| 被控对象（Plant） | StateStore 两张表：objects / relations | `infrastructure/sqlite.py:25-40`、`infrastructure/memory.py:27-30` | 无状态版本、无时间、无历史 |
| 执行器（Actuator） | `ctx.set/link/unlink` → Mutation → `store.apply` | `core/execution.py:128-146`、`runtime/runtime.py:222-227` | 无条件写、无 CAS、无幂等、无 create/delete |
| 观测器（Sensor） | `get/find/related` + `describe` + `FunctionResult.changes` | `runtime/runtime.py:62-155` | 无时间戳/版本/计数/分页/一致性快照 |
| 提交点（Commit） | `_apply_mutations`（validate-then-apply） | `runtime/runtime.py:222-227` | 只保证"本次缓冲不半途落盘"，不保证与读一致 |
| 目标与模型（Setpoint） | OntologySchema + CapabilityCatalog | `core/schema.py:48-83` | 无状态不变量、无期望态、版本不落库 |
| 反馈（Feedback） | 仅 `FunctionResult`，返回调用方后即丢弃 | `core/execution.py:60-74`、`runtime/runtime.py:208` | 无变更日志、无 seq、无回放 |

### 1.2 十个控制论维度上的落点

| 维度 | 内核现状 | 断点证据 |
|---|---|---|
| 目标（setpoint） | 只有"结构合法"，没有"状态应满足什么" | `core/types.py:38-60` 只做写入时类型检查；无 `balance >= 0` 类不变量 |
| 模型（model） | 模型与实际可执行能力不对账 | catalog 与 bindings 分别加载、无交叉校验：`loader/__init__.py:97-120` vs `adapters/assembly.py:37-38`；`cardinality` 只声明不执行：`core/schema.py:45` |
| 可观 | 只能看"当前值"，看不到"何时/多新/来自谁" | `core/state.py:14-27`、`core/execution.py:37-49` |
| 可控 | 只能改属性/加边/删边，不能造/删对象；写入无条件 | `core/execution.py:128-146`、`ports/state_store.py:16-36` |
| 反馈 | 单次调用的返回值即全部反馈，内核不留痕 | `runtime/runtime.py:208-213` |
| 稳定 | 无速率限制、无饱和、无幂等，重试可正反馈放大 | `runtime/runtime.py:161-177`（无准入控制）、`python_executor.py:50-51` |
| 时滞 | 无法测量：无时间戳、无耗时统计 | `infrastructure/sqlite.py:25-40`（表无时间列） |
| 噪声 | 过滤静默丢行、适配器语义漂移 | `infrastructure/filters.py:20-21,40-41`、`adapters/http.py:112-118` vs `adapters/mcp.py:68-71` |
| 自适应 | schema 换代不对账既有状态，只能重启+seed 复位 | `adapters/assembly.py:22,33-35` |
| 容错 | 默认 Memory 进程内、崩溃即失忆；无在飞执行记录 | `adapters/http.py:159`、`adapters/mcp.py:24` |

### 1.3 回路的三处断点

1. **观测端无时间基准** → 无法计算新鲜度/时滞，也无法区分"世界没变"和"我没看见"（见 2.2、2.3）。
2. **执行端无回执语义** → 超时/崩溃后"是否已生效"不可判定，重试即重复激励（见 2.1、2.6）。
3. **模型端无不变量、无版本对账** → 不存在"设定值 − 测量值"的误差项，所谓闭环只能由外部脚本拼装（平台正是在内核之外自建 change log：`ontoPlantform/src/onto_platform/infrastructure/runtime/kernel_gateway.py:397-429`）。

## 2. 逐项诊断

### 2.0 总览：26 项诊断 × 控制论维度

| # | 维度 | 一句话 | 级别 |
|---|---|---|---|
| 2.1.A | 稳定/原子性 | 读—算—写三段无锁无版本，并发覆盖（lost update） | P0 |
| 2.1.B | 可观/稳定 | Memory 存储零同步，并发观测可崩 | P0 |
| 2.1.C | 容错 | 原子性靠前置校验，Memory 无回滚 | P2 |
| 2.1.D | 可控 | `store.apply/seed` 公开且不校验 schema | P1 |
| 2.2.A | 可观/时滞 | 状态无版本/时间戳/来源，Change 无归属 | P0 |
| 2.2.B | 可观 | `output` 与 `changes` 双真相，内核不仲裁 | P1 |
| 2.2.C | 时滞/容错 | 启动即 seed（`INSERT OR REPLACE`），重启回滚状态 | P0 |
| 2.3.A | 可观 | 无计数/聚合/分页/投影，描述也不给实例数 | P1 |
| 2.3.B | 可观/时滞 | find 全表扫描 + 全量反序列化，成本随 N 线性 | P1 |
| 2.3.C | 噪声 | 过滤静默丢行（缺属性/类型不符都返回空） | P1 |
| 2.3.D | 可观/噪声 | related N+1 水合 + 撕裂快照 | P1 |
| 2.3.E | 噪声 | HTTP 返回对象、MCP/CLI 返回引用，语义漂移 | P1 |
| 2.3.F | 可观 | 无日志/无指标/无调用记录（HTTP 还关了日志） | P1 |
| 2.4.A | 可控 | 无 create/delete，对象总体由 seed 冻结 | P0 |
| 2.4.B | 可控 | 写入无条件原语，无门限/无 CAS | P0 |
| 2.4.C | 可控/容错 | 执行器任意 Python，无超时/沙箱/配额 | P1 |
| 2.4.D | 模型 | catalog 与 bindings 不对账，"能做什么"不可信 | P1 |
| 2.4.E | 可观 | 函数内读路径绕过 runtime 校验 | P2 |
| 2.5.A | 模型/容错 | Memory 浅拷贝/写别名，与 SQLite 行为不等价 | P1 |
| 2.5.B | 容错 | 默认 Memory，多适配器 = 多个世界 | P0 |
| 2.5.C | 容错/噪声 | 跨进程无协调，争用被报成 422 | P1 |
| 2.5.D | 自适应 | schema 重发布不对账既有状态，store 无版本 | P2 |
| 2.5.E | 反馈 | 内核无 changelog/seq，回放能力在内核外 | P1 |
| 2.6.A | 故障分类 | `invoke` 永不 raise，内核 bug 伪装成业务错误 | P1 |
| 2.6.B | 故障分类 | 无错误码/阶段/可重试性，因果链丢在边界 | P1 |
| 2.6.C | 容错 | 只有"成功/失败"两态，缺"结果未知" | P1 |

### 2.1 执行与原子性

#### 2.1.A 原子单元只覆盖写，不覆盖读（lost update）

**现象**：`invoke` 的路径是构造 `ExecutionContext`（`runtime/runtime.py:190-198`）→ 执行函数（`:200-203`）→ 取缓冲、全部校验、一次 `store.apply`（`:207-213`、`:222-227`）；SQLite 侧在 `with self._conn` 事务内逐条写入（`infrastructure/sqlite.py:119-130`），Memory 侧"先全量 `_check` 再逐条 `_apply_one`"（`infrastructure/memory.py:68-78`），测试也覆盖了"buffer 后崩溃不落盘"（`tests/test_runtime.py:178-236`）。但函数读到的 `Object` 是提交前某一时刻的快照（`core/execution.py:99-106` → `infrastructure/memory.py:42-46` / `sqlite.py:74-82`），函数用它算新值、提交时直接覆盖旧值：教科书式 read-modify-write，无版本、无条件写。**证据**：`runtime/runtime.py:190-208`（读—算—写分三段，中间无锁/无版本）、`infrastructure/sqlite.py:150-172`（`props[m.property] = new` 无条件覆盖）、`adapters/http.py:172`（`ThreadingHTTPServer`，每请求一线程）、`runtime/runtime.py` 全文无 `threading` 导入、`infrastructure/memory.py:27-30` 无锁字段。

**控制论解释**：执行器是无记忆的比例环节，被控对象却有多拍动态（并发写）；缺少"测量—执行"之间的原子性（无 CAS），回路在并发下出现正反馈式偏差放大。**后果（可量化）**：`pass_station` 中 `balance=100, fee=60` 的两笔并发调用都读到 100、都通过 `balance < fee` 检查（`examples/vehicle/functions/pass_station.py:22-24`），最终余额 40：本应收 120 实收 60，且建立两条 `passes` 边——资损 50% + 重复执行；该窗口在 HTTP 下必然存在。【推断：未做并发压测，但代码路径确定】标签**【实现缺陷】**。

#### 2.1.B Memory 存储完全无同步，且有"边迭代边改"崩溃路径

**现象**：`related` 对底层 set 排序返回（`infrastructure/memory.py:63,65`），另一线程 `_link` 正在 `set.add`（`:117`），并发时可能抛 `RuntimeError: Set changed size during iteration`；`apply` 里的 `_check` 与 `_apply_one` 也是无锁的两段（`:71-78`）。**证据**：`infrastructure/memory.py` 全文无 `threading`/`Lock`；`:63`、`:117`。

**控制论解释**：观测环节本身不稳定——同一次测量可能直接异常，控制器会把"观测器崩了"当成"世界不存在"。**后果**：默认 store 下并发读关系列表可能 500；平台若在运行期调用 `store.seed` 造对象（`kernel_gateway.py:342`），并发 `find` 遍历 `self._objects.items()`（`memory.py:52`）还会触发另一类 `dictionary changed size`。标签**【实现缺陷】**。

#### 2.1.C 原子性靠前置校验而非事务回滚，Memory 无 undo

**现象**：Memory 的 `apply` 无回滚——若第 2 条 mutation 在 `_apply_one` 抛错，第 1 条已写入且不会撤销（`infrastructure/memory.py:68-78`）。**证据**：契约只写在 `ports/state_store.py:34`（"Apply mutations atomically"），实现层无事务原语。

**控制论解释**：原子性从"保证"降级为"当前这些输入组合下恰好成立"，一旦新增 mutation 种类或并发介入即失效。**后果**：今天可达路径被 runtime 的 schema 校验 + store 的 `_check` 挡住，尚未复现，但这是无兜底的结构性隐患。标签**【设计取舍但代价被低估】**。

#### 2.1.D `store.apply` / `store.seed` 公开，且 store 层不校验 schema

**现象**：`StateStore` Port 的 docstring 假设"apply 收到的 mutation 已通过 schema 校验"（`ports/state_store.py:4`），实现层只查对象存在性（`infrastructure/sqlite.py:134-148`、`memory.py:82-89`）；`seed` 完全不在 Port 里（`sqlite.py:53`、`memory.py:34`），也不做类型/属性校验，`parse_seed` 同样不校验（`loader/__init__.py:139-154`）。**证据**：平台为了让投影能"造对象"，直接调用 `self._store.seed(creates, [])` 与 `self._store.apply(mutations)`（`ontoPlantform/.../kernel_gateway.py:341-353`）——证明"写必须经 Runtime"这条原则在缺少 create 原语时**必然被绕过**。

**控制论解释**：执行通道有两条，其中一条没有限幅器（schema 校验），控制器无法保证"所有动作都受模型约束"。**后果**：任何持有 store 的代码都能写入非法状态（未知属性、错类型、未定义对象类型）；这类对象随后出现在 `get` 结果里，`find` 又因类型未定义而报错（`runtime/runtime.py:141,271-275`），形成"可见但不可查询"的幽灵状态。标签**【实现缺陷】**（原语缺失），后果延伸见 2.4.A。

### 2.2 状态的时间性与来源

#### 2.2.A 状态里没有一个时间、版本或来源字段

**现象**：`Object` 只有 `ref + properties`（`core/state.py:14-27`）；`Change` 只有 `kind/old/new`，没有时间戳、没有 `execution_id`、没有序号（`core/execution.py:37-49`）；SQLite 只有 `objects(otype,oid,properties)` 与 `relations` 五列（`infrastructure/sqlite.py:25-40`）；`execution_id` 只活在返回对象上，是随机串（`runtime/runtime.py:177`），不落库、不进 `Change`（`core/execution.py:71-74`）。**证据**：`grep updated_at\|version` 在 state/execution/sqlite 零命中，`version` 仅作 ontology 元数据（`core/schema.py:52-56`）。

**控制论解释**：观测器输出是"稳态标量"、没有时间轴 ⇒ 无法估计时滞（dead time）、无法判断读数是否过期、无法做"预测—校正"，也无法把两次观测之差归因于自己的动作（动作与效果之间没有因果键）。**后果（可量化）**：对一笔余额，无法回答"何时、由哪个能力、哪次调用改成 200"，审计/对账必须重新推导；Agent 在 T2 读到余额 200，无法知道这是 10ms 前还是 3 天前（无 freshness 字段），若底层数据源停更，内核表现为"沉默的常量"而非"过期信号"；平台只能在内核之外自造 `seq/ts/source`（`kernel_gateway.py:401-406`），等于承认观测通道的时间基准不在内核内。标签**【设计取舍但代价被低估】**——v0.1 明确不做 Data Lineage / Event Sourcing（README:229-233），但连版本号都没有，使"至少能检测并发覆盖"这件最低要求也无法满足。

#### 2.2.B `output` 与 `changes` 是两个真相，内核不做交叉校验

**现象**：`FunctionResult` 同时携带函数自报的 `output` 与内核实际落盘的 `changes`（`core/execution.py:60-74`、`runtime/runtime.py:208-213`），函数可以返回 `{"balance": 999}` 而实际只写 200，内核不比对。**证据**：示例 Agent 之所以正确，是因为它自己又 `get` 了一次（`examples/agent/simple_agent.py:70-73`）——这是约定，不是契约。

**控制论解释**：控制器同时收到"执行器自述"和"被控对象实测"，却没有仲裁规则；当二者冲突时，闭环会按错误的反馈量收敛。**后果**：函数 bug（或幻觉）会以"成功 + 漂亮数字"进入上层，直到下一次真实测量才暴露，期间所有基于 `output` 的决策都已做出。标签**【实现缺陷】**（debug 模式至少应校验 `output` 中声明的对象/属性与 `changes` 一致）。

#### 2.2.C 每次启动都重新 seed，对象是 `INSERT OR REPLACE`

**现象**：`build_runtime` 只要给了 `seed_path` 就无条件 `state_store.seed(...)`（`adapters/assembly.py:33-35`）；SQLite 侧对象用 `INSERT OR REPLACE`（`infrastructure/sqlite.py:56-59`，关系却是 `INSERT OR IGNORE`，`:61`）——重启 = 把累积状态静默回滚到 seed 值，且没有任何版本号变化可供察觉。**证据**：`adapters/assembly.py:33-35`、`infrastructure/sqlite.py:53-70`；HTTP/MCP 启动参数直接暴露 `--seed`（`adapters/http.py:189-197`、`adapters/mcp.py:101-109`），"带 seed 启动"是文档推荐路径（README:88-90）。

**控制论解释**：控制器以为自己面对连续演化的对象，实际每次重启都被注入一个阶跃扰动（回到初值），且该扰动不可观测。**后果（可量化）**：充到 200 的车重启后回到 100，差额凭空消失；若两次重启间有流水对账，100% 对不上。标签**【实现缺陷】**。

### 2.3 可观测性

**能观测的**：读接口只有四种形态——单对象（`runtime/runtime.py:134-138`）、按类型过滤的列表（`:140-143`）、一跳关系（`:145-155`）、静态 schema（`:62-128`）；写路径唯一观测物是 `FunctionResult.changes`（`:208`），返回后内核不再保留副本。

#### 2.3.A 没有计数/聚合/分页/投影

**现象**：`find` 签名没有 `limit/offset/order/count`（`runtime/runtime.py:140`）；`GET /objects/{type}` 永远整表返回（`adapters/http.py:107-108`）；`describe()` 不给每种类型的实例数（`:62-90`）。**证据**：`runtime/runtime.py:140-143`、`adapters/http.py:105-108`、`runtime/runtime.py:62-90`。

**控制论解释**：控制器无法"先估规模再采样"，只能全量测量；在上下文有限的 Agent 场景里，全量即等量于失败。**后果**：想知道"共有多少车"必须把全部对象拉回数一遍；对象数超过上下文预算时，观测动作本身就会失败。标签**【设计取舍】**（v0.1 不做 Query DSL，README:233），但 `count`/`limit` 不属于 DSL，属基本量测能力。

#### 2.3.B find 是全表扫描 + 全量反序列化

**现象**：SQLite 侧 `SELECT oid, properties FROM objects WHERE otype=?` 取回该类型全部行，逐行 `json.loads` 后在 Python 里过滤（`infrastructure/sqlite.py:84-97`），属性上无索引（只有 `(otype,oid)` 主键，`:25-31`）；Memory 侧扫全部对象再按类型筛（`infrastructure/memory.py:48-57`）。**证据**：`filters.py` 是纯 Python 比较器（`infrastructure/filters.py:17-42`），没有任何下推。

**控制论解释**：测量成本随世界规模线性增长，采样周期被世界大小绑架——控制回路的带宽不恒定，规模一涨增益就掉。**后果（可量化）**：1 万对象、每对象 ~300B，单次查询约 3MB 内存 + 1 万次 JSON 解析（量级 10~50ms CPU），且无上限【推断：量级估算，非基准测试】。标签**【设计取舍但代价被低估】**（审查报告已记 G3，留给 v0.2）。

#### 2.3.C 过滤静默丢数据

**现象**：`match_object` 在属性缺失时直接 `return False`（`infrastructure/filters.py:20-21`），比较抛 `TypeError` 时也 `return False`（`:40-41`）；`_check_filters` 只校验属性名与运算符名，不校验值类型（`runtime/runtime.py:316-327`）；HTTP 查询串被 `json.loads` 尽力猜类型（`adapters/http.py:47-52`）。**证据**：`balance__gt=abc` → `int > str` 抛 `TypeError` → 被吞 → 返回空列表。

**控制论解释**：量测丢点而无告警——控制器会把"观测缺失"误判为"目标已达成"，这比报错更危险（报错会停，静默空结果会让策略继续）。**后果**：一次拼写错误/类型传错，返回的是"世界确实没有符合条件的对象"，下游据此下结论。标签**【实现缺陷】**。

#### 2.3.D related 的 N+1 与撕裂快照

**现象**：HTTP 适配器拿到 refs 后逐个 `runtime.get` 水合（`adapters/http.py:112-118`）——1 次关系查询 + N 次单对象查询、N 次加锁、N 个独立时刻；若期间有并发 invoke，返回的 N 个对象来自不同快照，而对象上没有版本号，无法检测撕裂。**证据**：`adapters/http.py:109-118`。

**控制论解释**：一次"导航观测"被拆成 N 次异步测量，观测结果在时间上不一致；控制器把由自身并发动作造成的差异当成世界的结构特征，形成虚假的模型修正。**后果（可量化）**：N=100 时存储往返 101 次，延迟约线性放大 100 倍【推断：按单次查询 ~0.1ms 估】，且可能读到"车已扣费但边还没建"的中间世界。标签**【实现缺陷】**。

#### 2.3.E 协议间语义漂移

**现象**：同一 `related`，HTTP 返回"对象数组"（`adapters/http.py:112-118`），MCP 与 CLI 返回"引用数组"（`adapters/mcp.py:68-71`、`adapters/cli.py:103-106`）；测试把漂移固化下来：`tests/test_http.py:61-64` 断言 `related[0]["properties"]["name"]`，`tests/test_mcp.py:87-90` 断言 `data == ["TollStation/ST001"]`。**证据**：两份测试互相矛盾而同时通过；README 声称"语义同源"（README:48）。

**控制论解释**：同一被控对象经不同观测通道得到不同量纲，控制器在不同接入方式下得到不可比的反馈。**后果**：HTTP 前端能做"对象卡片"，MCP 客户端拿到引用还得再 `get`；两边对"关系"的理解不同，跨协议编排必然出错。标签**【实现缺陷】**。

#### 2.3.F 没有运行时可观测性设施

**现象**：内核无日志、无计数器、无耗时、无调用记录；HTTP 适配器甚至主动关闭请求日志（`adapters/http.py:57-58`）。**证据**：`adapters/http.py:57-58`；`runtime/runtime.py` 无 logging/metrics 引用。

**控制论解释**：没有增益、误差积分、执行失败率，就没有整定依据——"控制器性能"本身不可测。**后果**：错误率、P99、失败分布一概不可测；出了事故只能靠外部 APM 或重放。标签**【设计取舍】**（v0.1 精简），但"日志"不属于平台化膨胀。

### 2.4 可控性与执行器边界

#### 2.4.A 写原语只有三个，没有对象生命周期

**现象**：`set_property / link / unlink`（`core/execution.py:19-21,128-146`）能改属性、加边、删边，不能创建/删除对象；`StateStore` Port 也没有 create/delete（`ports/state_store.py:16-36`），对象总体由 seed 决定（`adapters/assembly.py:33-35`）。**证据**：`ports/state_store.py:16-36`、`core/execution.py:128-146`；平台绕过：`kernel_gateway.py:341-352`。

**控制论解释**：执行器行程（range）被人为限幅，且限幅位置既未声明也未告警；控制器规划"新增实体"这类动作时必然失败，只能借道后门。**后果**：控制器无法加入新实体（新车、新站），只能改 seed 并重启——同时触发 2.2.C 的状态回滚；平台绕到 store 内部造对象，使"写必经 Runtime"失效。标签**【设计取舍】**（有意不给 Agent 写 API，README:126-128）+ **【实现缺陷】**（缺少受控的 create 通道，边界被外部绕过）。

#### 2.4.B 写入无条件覆盖，没有条件/比较原语

**现象**：`set` 一律 `props[property] = new`（`infrastructure/memory.py:98`、`sqlite.py:161-165`），不校验旧值、不带期望版本；所有业务前置条件只能写在 Python 函数里做"先读后判"（`examples/vehicle/functions/pass_station.py:19-28`）。**证据**：同上；`core/execution.py:128-136` 的 `set` 无 expected 参数。

**控制论解释**：执行器缺少"带门限的比例环节"，无法实现"仅在误差为正时才动作"的最小抑制策略；所有门限都外置到不受内核约束的脚本里。**后果**：与 2.1.A 叠加，50% 资损算例成立；且门限逻辑不可见、不可审计、不可被平台统一检查。标签**【实现缺陷】**。

#### 2.4.C 执行器是"任意 Python、全权限、无时限"

**现象**：`PythonFunctionExecutor.execute` 直接 `fn(context)`（`infrastructure/python_executor.py:50-51`）；首次调用时 `spec.loader.exec_module(module)` 会执行目标文件模块顶层代码（`:85-98`，`:92`）；没有超时、没有信号/线程隔离、没有 CPU/内存上限、没有能力白名单、没有速率限制；`ThreadingHTTPServer` 也无请求超时（`adapters/http.py:172`）。**证据**：上述行；`python_executor.py` 无 `signal`/`threading`/`timeout`/`resource`/`subprocess`。

**控制论解释**：执行器开环无界，没有饱和/限幅、没有抗饱和、没有执行时间抖动上界 ⇒ 回路可被单个坏动作拖死（单点失效放大为全局停摆）。**后果（可量化）**：一个 `while True:` 永久占死一个工作线程，并发堆积成线程泄漏（每线程 ~8MB 栈）；`_cache` 以路径为键且永不失效（`:79-82`），意味着"改了文件不重载"（v0.1 声明不做 hot reload，README:232），同时没有版本/哈希可判断"在跑的是哪版代码"。标签**【设计取舍但代价被低估】**。

#### 2.4.D Schema 宣布的能力与实际 bindings 不对账

**现象**：catalog 从 ontology 构建（`loader/__init__.py:97-120`），bindings 单独加载（`adapters/assembly.py:37-38`），二者无交叉校验；缺 binding 的函数在 `describe()` 里正常出现（`runtime/runtime.py:82-89`），直到 `invoke` 才抛 `FunctionExecutionError`（`infrastructure/python_executor.py:39-48`），反向的"有 binding 无定义"则永久静默。**证据**：`adapters/assembly.py:37-41`、`python_executor.py:39-48`。

**控制论解释**：模型（能做什么）与实际执行器（实际能做什么）不一致，控制器据模型规划、执行时才发现能力缺失——model-reality mismatch 发生在最坏的时刻（动作已下达）。**后果**：Agent 的"Discover→Act"可能凭空失败；缺 binding 的示例函数目前恰好都配齐（`examples/vehicle/bindings.yaml`），问题只在扩展时暴露。标签**【实现缺陷】**。

#### 2.4.E 函数内部读路径绕过 runtime 校验

**现象**：`ExecutionContext.related` 直接调 `store.related`（`runtime/runtime.py:197`），跳过 `Runtime.related` 的关系存在性与方向校验（`:145-155`）。**证据**：`runtime/runtime.py:197` vs `:145-155`；方向写错时 SQLite 抛 `ValueError`（`infrastructure/sqlite.py:116`），被 executor 兜成 `FunctionExecutionError`（`python_executor.py:52-57`）。

**控制论解释**：第二条未校验的读路径——执行器的"感知"与观测器的"感知"不是同一个函数，前者可以对模型撒谎而不被发觉。**后果**：函数里写错关系名得不到 `RelationNotFound`，只会静默返回 `[]`，于是"没查到"与"关系不存在"再次不可区分。标签**【实现缺陷】**。

### 2.5 一致性与并发

#### 2.5.A 一个 Port，两种语义；Memory 与 SQLite 行为不等价

**现象**：`get_object` 在 Memory 返回 `dict(...)` 浅拷贝（`infrastructure/memory.py:46`），嵌套 dict/list 与库内状态共享引用；在 SQLite 返回 `json.loads` 深拷贝（`infrastructure/sqlite.py:82`）；写入同理，Memory 把函数传入的对象值直接挂进状态（`:98`），SQLite 立刻序列化（`:161-165`）。于是 `obj.properties["meta"]["x"] = 1` 或 `ctx.set(ref,"tags",lst); lst.append(...)` 在 Memory 上静默改状态（绕过 Mutation 校验与原子性），在 SQLite 上毫无效果。**证据**：`memory.py:46,98` vs `sqlite.py:82,161-165`；测试只测顶层拷贝（`tests/test_memory_store.py:24-27`）。

**控制论解释**：同一个控制器面对两个传递函数不同的被控对象；在 A 上整定好的策略到 B 上失效，而测试台（Memory）恰好是最宽容的那个。**后果（可量化）**：2 个 store 只有 1 个行为正确；单测在 Memory 上通过、生产在 SQLite 上不生效（或反之）。标签**【实现缺陷】**。

#### 2.5.B 默认持久层是进程内 Memory，多适配器 = 多个世界

**现象**：`--store` 默认 `memory`（`adapters/http.py:159,183`、`adapters/mcp.py:99`）；HTTP 与 MCP 各自 `build_runtime`（`adapters/http.py:165-171`、`adapters/mcp.py:34-40`），同一套本体在两条通道里有两份互不可见的状态。**证据**：上述行。

**控制论解释**：被控对象不唯一，观测与执行不在同一物理系统上，闭环无意义。**后果**：Agent 通过 MCP 改的世界，人在 HTTP 控制台看不见；"kiosk 演示"没问题，作为平台底座是 P0 级事故源。标签**【设计取舍但代价被低估】**。

#### 2.5.C SQLite 单连接 + 进程内非重入锁，跨进程无协调

**现象**：`sqlite3.connect(path, check_same_thread=False)`（`infrastructure/sqlite.py:47`）配一把 `threading.Lock`（`:46`）；进程内所有方法串行、`apply` 在锁内完成整笔事务（`:119-130`），进程外两把锁互不可见，只能靠 SQLite 文件锁，默认 5s 超时后抛 `sqlite3.OperationalError: database is locked`。**证据**：`sqlite.py:46-47,119-130`；该异常不是 `KernelError`：写路径被 `runtime/invoke` 的 `except Exception` 收进 `result.error`（`runtime/runtime.py:209-212`），HTTP 按默认值映射 422（`adapters/http.py:144-147`）；读路径根本不捕获（`adapters/http.py:120` 只接 `KernelError`），异常直接冒出 handler。

**控制论解释**：数据库争用被报告为"业务参数不可处理"（422），控制器的重试/降级策略会按错误的类别动作；读路径则直接断连，观测中断而无结构化信号。**后果**：跨进程争用 → 写请求 422（客户端不会重试）、读请求连接错误；`apply` 内部再次 `self._conn.execute`（`sqlite.py:123-124` → `:143-148`），未来任何在持锁状态下再进锁的改动都会自锁死（非可重入 Lock）。标签**【实现缺陷】**。

#### 2.5.D schema 重发布与既有状态之间没有任何对账

**现象**：schema 每进程从 YAML 重建（`adapters/assembly.py:22`），store 被保留；内核从不校验既有对象是否符合新 schema——`get` 原样返回属性字典（`core/state.py:14-23` 无校验逻辑），`find` 对已删除属性直接 `InvalidArgument`（`runtime/runtime.py:316-323`），函数读到被改类型的旧值则在运行期崩成 `FunctionExecutionError`（`python_executor.py:52-57`）；`schema.version` 只是元数据（`core/schema.py:52-56`、`loader/__init__.py:60-64`），既不落库也不比对（store 里没有 schema 版本表，`infrastructure/sqlite.py:25-40`）。**证据**：上述行；平台侧 `publish_schema` 明确"state store is preserved across republish"（`kernel_gateway.py:169-171`），但没有任何漂移检查。

**控制论解释**：这是自适应缺失——模型变更（republish）没有带来任何状态一致性检查或迁移信号，只能靠重启+seed 把状态打回原形（见 2.2.C），即"用开环复位代替闭环校正"。**后果**：属性删除后旧值继续可读、继续进入 Agent 上下文（幽灵字段）；类型收窄后旧值成为"读到就崩"的定时炸弹；无 schema 指纹 ⇒ 无法判断某条观测属于哪个模型版本。标签**【设计取舍但代价被低估】**。

#### 2.5.E 变更日志在内核里不存在，回放能力在内核外

**现象**：内核没有 changelog 表/文件，`Change` 不带 seq（`core/execution.py:37-49`）；平台自建 `_changes + _seq + 追加写 jsonl`（`kernel_gateway.py:397-429`），并承认这是"UI/Agent 的观测通道"（`:114-117`）。**证据**：内核 `infrastructure/sqlite.py:25-40` 只有两张业务表；平台实现见上述行（写日志失败不阻断写路径、崩溃截断行可跳过）。

**控制论解释**：观测通道与写路径没有共同事务边界（先 apply 再 record，两步之间崩溃则日志与状态不一致），且"是否已落地"的判断只能靠消费方自己拼。**后果**：换一个消费方就得重新实现一遍；内核作为底座却把"可观测"这一核心职责外置。标签**【实现缺陷】**（职责错位）。

### 2.6 错误模型

#### 2.6.A `invoke` 永不 raise，代价是"内核自己的 bug 伪装成业务错误"

**现象**：`invoke` 在三个位置吞异常：前置校验（`runtime/runtime.py:183-188`）、执行器调用（`:200-203`）、提交阶段（`:207-213`）；执行器再把函数抛出的非 `KernelError` 包装成 `FunctionExecutionError`（`python_executor.py:52-57`）。**证据**：上述行；收益是真实的——调用方只看一个字段，三个适配器因此简化（`adapters/http.py:144-147`、`adapters/cli.py:113-116`、`adapters/mcp.py:80-88`）。

**控制论解释**：故障分类被压平——控制器的"故障检测→分类→处置"三环节中，第一环之后全部失去信息。**后果**：属性名拼错、`None` 参与算术、内核 helper 抛 `TypeError`，最终都是 `FunctionExecutionError` + 一段字符串（`pass_station.py:22-26` 若 `balance` 缺失即此类），HTTP 统一 422；监控看到的"业务拒绝率"里混着"代码 bug 率"，故障检测被噪声污染。标签**【设计取舍但代价被低估】**。

#### 2.6.B 没有错误码、阶段、可重试性、因果链

**现象**：序列化只留 `type + message`（`adapters/serialize.py:58-59`）；`raise ... from exc` 的原始异常（`python_executor.py:55-57`）越过边界即丢弃；调用方无法区分"参数错（可修正重试）""业务拒绝（不可重试）""存储瞬时失败（应重试）""未知（不可重试）"。**证据**：`adapters/serialize.py:40-43,58-59`；HTTP 映射表只有 4 类（`adapters/http.py:30-35`），其余一律 422/500。

**控制论解释**：重试策略的正确性取决于错误分类；分类缺失 + 无幂等（2.1/2.4）组合起来，重试不是纠错而是二次激励。**后果（可量化）**：一次 422 超时后重试，可能二次扣费——3 次重试 × fee=30 ≈ 90 元过路费对 1 次过闸。标签**【设计取舍但代价被低估】**。

#### 2.6.C "未生效"与"未知"不可区分

**现象**：提交阶段抛错时内核返回 `error`；若异常发生在 commit 之后（连接层错误、进程崩溃），调用方拿到的是失败，但状态可能已改——语义上需要三态（成功 / 确定失败 / 结果未知），内核只有两态。**证据**：`runtime/runtime.py:207-213` 把任何 `Exception` 都变 `result.error`；`FunctionResult`（`core/execution.py:60-74`）无 `phase`/`applied` 字段。

**控制论解释**：这是控制论意义上的"不可判定的执行"——它使任何重试/补偿策略失去前提。**后果**：正确做法（保守不重试）会漏动作，激进做法（重试）会重动作，二者都错；生产上只能靠人工对账。标签**【实现缺陷】**。

## 3. 测试断言了什么、没断言什么

| 已断言（78 个测试，审查文档口径） | 证据 | 未断言 / 无载体 |
|---|---|---|
| 五 API 正常路径与参数/目标/关系校验 | `tests/test_runtime.py:31-64` | 任何并发（无多线程 invoke、无 lost update 用例） |
| 单次 invoke 的原子性（注入崩溃、中途 fail、executor 返回错误） | `tests/test_runtime.py:99-112,178-236` | 跨进程/多实例一致性 |
| Mutation 的 schema 校验（未知属性 → `SchemaViolation` 且不落盘） | `tests/test_runtime.py:134-157` | store 层 `seed/apply` 被直接调用时的一致性（无测试，也无防护） |
| link/unlink 幂等；Memory 顶层防御性拷贝 | `tests/test_memory_store.py:24-27,67-84`、`tests/test_sqlite_store.py:39-48` | 嵌套属性/写入别名的两 store 行为等价性（现测试恰好测不到差异） |
| SQLite 重开进程后状态仍在 | `tests/test_sqlite_store.py:22-30` | 重启 + seed 的状态回滚（无测试，现象必然） |
| HTTP 404/422 映射、MCP 七工具与 invoke 端到端、"invoke 永不 raise" | `tests/test_http.py:78-90`、`tests/test_mcp.py:43-125` | related 在 HTTP（对象）与 MCP（引用）语义一致——两测试各自断言矛盾期望且都通过（`test_http.py:61-64` vs `test_mcp.py:87-90`） |
| — | — | find 的规模行为、分页、聚合（API 本身没有） |
| — | — | 函数超时/死循环/资源上限 |
| — | — | 重试幂等、execution_id 去重 |
| — | — | 时间戳/版本/provenance（字段不存在，无从断言） |
| — | — | schema 重发布 vs 既有状态 |
| — | — | 过滤器类型不匹配应报错而非静默返回空（`filters.py:40-41` 的行为未被测试固定） |

结论：现有测试证明的是**单线程、单进程、无演进、无故障注入**下的语义正确性——"理想实验台"；而控制回路真正关心的四件事（并发下的观测一致性、故障后的状态可判定性、时间维度、模型演进）全部在覆盖之外，其中三件连 API 载体都没有。这也解释了为什么 `docs/v0.1-completeness-review.md` 能给出 95% 完成度：它度量的正是这台实验台。

## 4. 内核层 Top 问题排序

| # | 级别 | 问题（一句话） | 证据 | 后果 | 修法（最小改动） |
|---|---|---|---|---|---|
| K1 | **P0** | 启动即 seed 且对象 `INSERT OR REPLACE`，重启静默回滚状态 | `adapters/assembly.py:33-35`、`infrastructure/sqlite.py:56-59` | 累积状态丢失，不可观测 | seed 仅在 store 为空时执行，或独立 `init` 命令 + 记录 `seeded_at` |
| K2 | **P0** | 无条件写 + read-modify-write，无版本/CAS，并发覆盖 | `runtime/runtime.py:190-208`、`sqlite.py:150-172`、`memory.py:92-105` | 并发扣费 50% 资损 + 重复建边（算例见 2.1.A） | Mutation 增 `expected_version`；state 增 version 列，`UPDATE ... WHERE version=?` |
| K3 | **P0** | 无幂等键/无执行记录；超时或崩溃后重试即重复执行 | `runtime/runtime.py:177`（execution_id 不落库）、`core/execution.py:37-49` | 一次过闸可能收 2~3 次费 | `executions(exec_id UNIQUE, ts, function, target, status)`，重复 exec_id 返回原结果 |
| K4 | **P0** | 状态无版本/时间戳/来源；Change 无时间无归属 | `core/state.py:14-27`、`core/execution.py:37-49`、`sqlite.py:25-40` | 无法判新鲜度/对账/回放；平台被迫自建日志 | Object 增 `version/updated_at`；Change 增 `ts/execution_id/seq` 并落库 |
| K5 | **P0** | Memory 为 HTTP/MCP 默认且无锁；两进程两份世界 | `adapters/http.py:159,172`、`adapters/mcp.py:24`、`memory.py:27-46` | 观测与执行不在同一对象上；并发 `RuntimeError` | 默认改 sqlite；memory 加锁并声明单进程；或强制单 runtime 单入口 |
| K6 | **P1** | `find` 全表扫描 + 全量反序列化，无分页/计数/投影；过滤静默丢行 | `sqlite.py:84-97`、`runtime/runtime.py:140`、`filters.py:20-21,40-41` | 万级对象 3MB/10~50ms 每查；漏检被误判为"无数据" | 加 `limit/offset`、`count`；操作符下推 SQL；类型不匹配报 `InvalidArgument` |
| K7 | **P1** | related 的 N+1 + 撕裂快照；HTTP/MCP 语义漂移 | `adapters/http.py:112-118`、`adapters/mcp.py:68-71`、两份测试互相矛盾 | 100 关系 ≈ 101 次往返；观测混合两个时刻 | 统一返回引用；提供批量 `get_many(refs)` 或 snapshot token |
| K8 | **P1** | 执行器任意 Python、无超时/沙箱/配额 | `python_executor.py:50-51,85-98`、`http.py:172` | 一个死循环拖死线程池；审计面=整机 | 线程/进程级超时 + 资源上限 + 导入白名单；至少 `--timeout` |
| K9 | **P1** | `store.apply/seed` 公开且不校验 schema；无受控 create | `ports/state_store.py:34`、`sqlite.py:53,134-148`、`kernel_gateway.py:342,352` | 平台已绕过"写必经 Runtime"，可写入非法状态 | 增 `create_object` mutation 并经 runtime 校验；store 内部方法降级为私有 |
| K10 | **P1** | 错误模型不可判定：无码/无阶段/无重试性，缺陷=业务错误，两态而非三态 | `runtime/runtime.py:200-213`、`python_executor.py:52-57`、`serialize.py:58-59`、`http.py:144-147` | 重试决策只能靠猜；故障率统计失真 | `FunctionResult` 增 `code/phase/retryable`；`KernelError` 带稳定码 |
| K11 | **P1** | 内核无 changelog/seq/observe 接口 | `core/execution.py:37-49`、`sqlite.py:25-40`、平台自建 `kernel_gateway.py:397-429` | 每个消费方重复实现，且与状态无事务边界 | 提交点同事务写 `changes(seq,ts,exec_id,...)`；暴露 `observe_changes(after_seq)` |
| K12 | **P2** | schema 重发布不校验既有状态，store 无 schema 版本 | `adapters/assembly.py:22`、`state.py:14-23`、`sqlite.py:25-40` | 幽灵字段、类型漂移、版本错配无人察觉 | store 记 `schema_version`；启动时做一次 drift 扫描并报告 |
| K13 | **P2** | Memory 浅拷贝/写入别名，Port 行为不可替换 | `memory.py:46,98` vs `sqlite.py:82,161-165` | 同一函数两 store 语义不同（嵌套写入只在 Memory 生效） | 读深拷贝或声明"只能传标量"；补两 store 参数化测试 |
| K14 | **P2** | 声明了但从未执行的模型要素：`cardinality` 全库仅出现在 schema/loader/describe | `core/schema.py:45`、`loader/__init__.py:94`、`runtime/runtime.py:78,118` | 模型承诺 1:1，状态允许 N 条边——model-reality 不一致 | 要么落地约束（link 时查重），要么删字段并标注"保留未实现" |
| K15 | **P2** | `datetime` 以字符串存储与比较（过滤器用 Python 原生比较），时区格式不同即错序 | `core/types.py:53-59`、`filters.py:28-37` | 时间过滤静默返回错序/错集 | 归一化 UTC ISO 存储；比较前解析 |

## 5. 内核层优化路线（供最终方案取舍）

按"先让回路可闭、再谈性能"排序，前三项即可把内核从"指令执行器"升级为"可观测、可重试的执行机构"：

1. **状态时间化（K4/K3）**：`Object` 增 `version/updated_at`，`Change` 增 `ts/execution_id/seq`，提交时同事务写 `executions` 与 `changes` 两表。收益：可判新鲜度、可幂等重试、可回放；成本约 +80 行（v0.1 无迁移机制，可"建表即用"）。
2. **写入门限化（K2/K9）**：Mutation 增可选 `expected_version`，`set` 走 `UPDATE ... WHERE version=?`，冲突返回专门的 `ConcurrentModification`；同时补 `create_object` mutation，把 `seed` 收回 Runtime。收益：消灭 read-modify-write 资损，堵住平台绕过。
3. **执行边界化（K8/K10）**：给 executor 加超时与并发上限（线程 + 计时器即可起步），错误带 `code/phase/retryable`。收益：单点坏函数不再拖死整机；上层可据 `retryable` 做补偿而非盲重试。
4. **观测可扩展（K6/K7/K11）**：`find` 增 `limit/offset/count`，操作符下推；`related` 统一返回引用并提供批量水合；暴露 `observe_changes(after_seq)`。
5. **一致性收敛（K1/K5/K12/K13）**：默认 store 改 SQLite；seed 幂等；store 记录 schema version 并在启动时做 drift 报告；Memory 深拷贝或降级为纯测试实现。

**一句话结论**：OntoRK 的执行语义（validate-then-apply、函数不可提交）是干净的，作为"语义执行机构"合格；但它对控制回路缺少三件最低限度的工程承诺——**时间（何时/多新）、回执（是否生效/能否重放）、门限（未变时不动）**。补上这三件，平台层的许多"控制失效"问题会自动消失；不补，则任何上层策略都只能在"看不见、说不准、退不回"的世界里开环运行。
