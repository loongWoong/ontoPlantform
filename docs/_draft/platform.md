# 平台层（ontoPlantform v0.1）工程控制论诊断

> 范围：`application/`、`infrastructure/`、`interfaces/api.py`、`ports/`、`tests/`。只读代码，未启动服务、未改源码。
> 路径约定：平台源码路径相对 `src/onto_platform/`，测试路径相对仓库根 `ontoPlantform/`。行号对应当前工作树。
> 记号：`[有意]` = v0.1 明确的有意简化；`[缺陷]` = 与设计意图不符或会在规模/并发/故障下产生错误；`[推断]` = 未运行验证，由代码路径推出。

---

## 1. 平台在控制回路中的位置

```
        ┌────────── 设计时：控制器（编译 + 发布） ──────────┐
用户/控制台 ──► interfaces/api.py ──► application/*_usecases.py
                                      │ domain/ontology/compiler.py
                                      ▼
                         SchemaPublisher.publish ──► gateway.publish_schema
                                                      │（替换 schema，保留 state）
 外部数据源 ──► Connector.fetch ──► RunProjection ──► SemanticDelta
                                                      │
                                                      ▼
                                            gateway.apply_delta ──► Kernel StateStore
                                                                    （被控对象 = 语义状态）
 观测器 ◄── gateway.describe/find/get/related ◄────────────────────────┘
        ◄── gateway.observe_changes（内核 Change 流 + 落盘 jsonl）
        ◄── audit log（仅成功动作）
 执行器 ◄── gateway.invoke ──► PlatformExecutor ──► python / workflow / agent（含 LLM）
```

回路元素与实现位置：

| 回路元素 | 实现位置 | 缺什么 |
|---|---|---|
| 被控对象 | kernel StateStore（`infrastructure/runtime/kernel_gateway.py:106-108`） | 平台不持有状态，只经网关触碰 |
| 控制器（设计时） | `application/ontology_usecases.py:176-223` SchemaPublisher | 无版本校验、无迁移、发布无回滚 |
| 控制器（运行时编排） | `application/capability_usecases.py:106-131`、`infrastructure/execution/*` | 无超时、无重试、无背压 |
| 观测器 | `kernel_gateway.py:185-229`、`:397-399`、audit | 无误差信号（对账）、无水位、无健康度、无执行 ID 关联 |
| 执行器 | `kernel_gateway.py:235-262`、`platform_executor.py:34-71` | 执行期间独占 workspace 锁，无隔离 |
| 反馈（误差） | —— | **不存在**：源数据与语义状态从不比对；失败不入审计 |

五条链路的闭环度（本次诊断的总体判断）：

| 链路 | 控制量 | 是否闭环 | 依据 |
|---|---|---|---|
| 链一 设计时→运行时 | schema/发布 | **半开环**：发布无校验、失败无回滚、无版本观测 | `ontology_usecases.py:168-172,204-214` |
| 链二 数据→语义状态 | 全量快照 delta | **开环**：无水位、无删除、无对账、无运行键 | `data_usecases.py:111-155,185-201` |
| 执行 用户/Agent→状态 | invoke | **闭环但饱和**：变化被记录，但执行期独占锁、失败不可见 | `kernel_gateway.py:239-262` |
| 观测 | change 流/状态读 | **只有单向流**：有 seq 无游标保留、无执行 ID、无误差量 | `kernel_gateway.py:397-413` |
| 治理 | 审计/成员 | **只记录成功**：拒绝与失败不可观测 | `data_usecases.py:153`；`ontology_usecases.py:215` |

结论先说：这个系统的**控制器（发布/投影）是开环的**——动作发出去之后，没有任何一个量被送回比较。三处"回路"实际上都是单向的：设计时→运行时、源数据→状态、执行→变更流。下面的诊断按这个主线展开。

---

## 2. 逐项诊断

### 2.1 数据投影链路（链二：外部数据 → 语义状态）

**P1 全量快照，无增量水位** `[有意简化为 v0.1，但后果是缺陷]`
- 现象：每次 `RunProjection` 都调用 `connector.fetch(asset.config)` 取全部行，没有任何 `since`/`updated_at`/游标参数。
- 证据：`application/data_usecases.py:124-138`（对每个 asset 全量 fetch）；`infrastructure/datasource/sqlite_ds.py:18-22`（`SELECT` + `fetchall()`）；`infrastructure/datasource/rest_ds.py:20-36`（整页 JSON 读入内存）。投影对象里没有任何水位字段（`domain/data/model.py:178-203`），运行结果只写审计（`data_usecases.py:153-154`），不落"上次运行到哪"。
- 控制论解释：这是**采样而非状态估计**。没有水位 = 没有积分项，控制器不知道"上次以来变化了多少"，只能每次重算全世界；系统对源侧的增量扰动无响应能力，只能靠周期性全量扫描（且系统自己也不调度扫描，靠人点按钮）。
- 后果：N 行源数据 → N 个 upsert 全量传输、全量校验；N=10⁵ 时单次运行的内存与锁持有时间线性膨胀（见 P2/G1）。源侧删除永远不生效（P2）。

**P2 无删除语义 → 幽灵对象** `[缺陷]`
- 现象：投影只产生 `upserts` 与 `links`，从不产生 `unlinks`。
- 证据：`data_usecases.py:140-143` 构造 `SemanticDelta(upserts=..., links=...)`，`unlinks` 缺省为空（`ports/runtime.py:64-66`）；`data_usecases.py:185-201` 的 `_links` 只 `yield LinkAssert`。全仓搜索：`UnlinkAssert` 只在 `ports/runtime.py:47-53` 定义、在 `kernel_gateway.py:329-338` 被消费，**没有任何用例构造它**。
- 控制论解释：被控对象的状态空间只能增不能减，映射到控制里就是**没有负反馈项**：源侧"撤掉一个实体"这个扰动无法被状态跟踪，误差只会累积。
- 后果：源库删行/删关系后，内核里对象与边永久残留；下游查询、Agent 观察、报表全部基于过期集合；且这种偏差**没有任何入口能修正**（除手工写 delta）。

**P3 无对账、无幂等运行键** `[缺陷]`
- 现象：一次投影运行没有 run id、没有幂等键、没有"运行中"状态，也没有事后核对（kernel 对象数 vs 源行数）。
- 证据：`data_usecases.py:111-155`：整个 `__call__` 无运行记录写入 repo（只有成功后的审计 `:153`）；`SemanticDelta`（`ports/runtime.py:56-66`）无 `run_id`/`source_snapshot_ts`；`Projection` 无 `last_run_at`（`domain/data/model.py:178-203`）。
- 控制论解释：没有**可观测的控制器内部状态**（Is the loop running? Did it converge?），也没有对"同一次动作重复施加"的鲁棒性设计。控制论上这是"无记忆的纯比例控制"：每次输出只依赖当前输入，无法判断自己是否已经做过。
- 后果：双击两次 `/projections/{pid}/run` 或两个进程同时跑 = 两份全量 delta 交错写入，后写覆盖先写；审计里出现两条无法区分"重复/两次真实运行"的记录。

**P4 两阶段写入非原子：seed 先提交、apply 后失败会残留** `[缺陷，最严重之一]`
- 现象：`apply_delta` 先 `self._store.seed(creates, [])` 落库，再 `self._store.apply(mutations)`；两步是两个独立事务。第二步失败时第一步已提交，而且**变更流里不会出现这批 create**（`_record` 在最后）。
- 证据：`kernel_gateway.py:340-353`（`seed` 在 `:342`，`apply` 在 `:352`，`_record` 在 `:355-356`）；内核侧 `seed` 是自己的一次提交（`ontoRK/src/ontology_runtime/infrastructure/sqlite.py:53-54` 的 `with self._lock, self._conn:`），`apply` 是另一次。
- 关键放大器：关系端点是否存在**不在网关里校验**。`_validate_edge` 只比对类型（`kernel_gateway.py:359-377`），存在性由内核 `apply` 时才抛 `ObjectNotFound`（`ontoRK/.../memory.py:86-89`），而这是内核异常、不是 `PlatformError`，会**穿透 `apply_delta` 的契约**（docstring 声称 validate-before-apply，`kernel_gateway.py:286`）。
- 控制论解释：执行器的一次"动作"应当是原子的（离散事件系统的最小可控步）。现在动作被拆成两个可观测/不可观测的半动作：**状态已变、观测通道没记录**——观测器与被控对象失步（observer desync）。这是控制回路里最危险的一类故障：控制器以为自己没做，实际做了一半。
- 后果：投影映射里有一条关系指向不存在的对象（真实数据里非常常见，比如交易表里的 `vid` 引用了未同步的车辆），则该次运行：①新车对象已创建；②`apply_delta` 抛 `ObjectNotFound`；③`RunProjection` 不写审计、返回 500；④change log 无记录。用户重试会再次尝试创建（幂等，勉强可恢复），但**首次部分写入从未被审计，也无法被任何消费者发现**。

**P5 缺失字段保留旧值 → 陈旧** `[缺陷]`
- 现象：upsert 的 `properties` 只包含"源行里存在且在 mapping 里声明"的字段；缺字段时不做任何清空/置空。
- 证据：`data_usecases.py:176-180`：`if fm.source_field in row` 才加入 properties；网关侧对既有对象只对"值不同"的属性生成 mutation（`kernel_gateway.py:307-317`），没出现的属性既不置空也不报错；`ports/runtime.py:29-35` 的 `ObjectUpsert` 语义是补丁而非全量替换。
- 控制论解释：语义是"增量补丁"，但被当"全量快照"来用。当源侧字段消失（列删除、NULL、API 换版），状态保留旧值 = 控制器输出停在旧设定点，误差永不归零。
- 后果：属性陈旧无告警；典型的"车辆余额停留在三天前"式数据事故，且从 change log 完全看不出来（没有 change 发生）。

**P6 源侧快照不一致** `[推断]` `[缺陷]`
- 现象：多个 asset 串行 fetch，没有共享快照/事务时间点。
- 证据：`data_usecases.py:126-138` 顺序 `for asset_name in {...}` 逐个 `connector.fetch`；`sqlite_ds.py:18-24` 每次新建连接、各自只读一次。
- 控制论解释：采样不同步 = 观测向量各分量取自不同时刻，控制器基于一个**不存在的系统状态**做动作。
- 后果：`vehicles` 与 `transactions` 两次读取之间的写入，会导致本轮 delta 内部自相矛盾（关系指向刚被删的车），进一步触发 P4 的半写入路径。

**P7 运行历史不可重放/不可补偿** `[缺陷]`
- 现象：一次投影运行的产物（delta、changes、失败原因）不落任何可查询的"运行记录"，只留下成功后的一条审计摘要。
- 证据：`data_usecases.py:147-155`（summary 只含计数，写完审计即丢）；`RunProjection` 不写 repo；`Projection` 无 `last_run_*`（`domain/data/model.py:178-203`）；失败路径（`:133-138` 抛 `ConnectorError`）连计数都没有。
- 控制论解释：这等于**不保存控制器上一拍的状态**（无 memory 的纯组合逻辑）。没有记忆，就无法做"上次运行失败 → 本次补偿"的差动控制，也无法判断投影是否收敛。
- 后果：数据错了只能全量重跑或人工造 delta；无法回答"这个对象最后一次被哪个投影、哪次运行写成这样"，与 G2 的 execution_id 缺口叠加后完全不可追溯。

### 2.2 编译与发布（链一：设计时 → 运行时）

**S1 重发布不校验既有状态是否满足新 schema** `[缺陷]`
- 现象：`publish_schema` 只解析新 payload，直接 `OntologyRuntime(schema=..., store=self._store, ...)` 换掉 runtime；store 原样保留，**不校验存量对象是否仍满足新定义**。
- 证据：`kernel_gateway.py:136-171`（`:169-171` 换 runtime，注释明说 "definitions change, semantic state does not"）；测试甚至把这条写成断言：`tests/test_gateway.py:121-130`（version 1→2 后旧对象仍在）。
- 控制论解释：模型（schema）是被控对象的**约束集**。运行时更换约束而不做可行性检查，等价于在闭环中改变约束面却不检查当前状态是否仍在可行域内——从控制论上这是失去稳定性裕度的典型来源。
- 后果：删属性/改类型（如 `balance: number → integer`）后，存量对象成为"schema 非法状态"；此后任何一次 `apply_delta` 触碰该对象，`pdef.type.check(value)`（`kernel_gateway.py:297`）按新类型判旧值，可能直接抛 ValidationError，正常投影被卡死，而且**没有迁移入口**。

**S2 无 schema 版本校验/迁移** `[有意简化]`
- 证据：`domain/ontology/model.py:147` 有 `version: int = 1`，`compiler.py:32` 写入 payload；但全仓没有任何自增点（`grep "version"` 只命中读取/to_dict），`compile` 不比较新旧版本，网关也不记录 runtime 当前版本。`SchemaPayload`（`ports/runtime.py:69-84`）无 `schema_version`/`min_reader` 字段。
- 控制论解释：版本号存在但不参与反馈，是"装饰性观测"——系统无法回答"运行时用的哪一版模型"。
- 后果：多实例部署时（P/C 段）实例 A 发布 v2、实例 B 自愈发布已存的 v1 payload，两个实例对同一 store 施加不同约束集，行为不可复现。

**S3 发布-持久化时序：先改生命周期，后发布，失败无回滚** `[缺陷]`
- 现象：`PublishOntology` 先把 ontology 生命周期推到 `published` 并 `save`，然后才 `publisher.publish`；`PublishCapability` 同理；`SchemaPublisher` 先 `publish_schema`，再 `save("schema_payload")`。
- 证据：`application/ontology_usecases.py:168-172`（save 在 `:171`，publish 在 `:172`）；`capability_usecases.py:84-90`（save 在 `:88`，publisher 在 `:90`）；`ontology_usecases.py:204-214`（publish 在 `:204`，落 payload 在 `:208-214`）。
- 控制论解释：三次非事务提交 = 控制器动作被切成三拍，任一拍被打断都会留下**设计时与运行时的不一致中间态**，而且系统没有"重放/回滚"机制把它拉回一致。
- 后果（三种崩溃窗口，全部无告警）：①save 后 publish 失败 → 仓库显示已发布、运行时还是旧 schema（或没有 runtime）；②publish 成功但进程在存 payload 前死 → 重启后 `gateway_for` 用**旧 payload** 自愈（`application/common.py:92-104`），运行时悄悄退回上一版；③`PublishCapability` 失败后 capability 标记 published，前端认为可用，invoke 报 FunctionNotFound。

**S4 python 源码重发布静默不生效** `[缺陷]`（新发现，影响面大）
- 现象：`PublishCapability` 只在"没有 entrypoint"时才把 `impl.source` 写盘；首次发布后 `entrypoint` 已存在，**后续编辑源码再发布会跳过写文件**。即使写了，同一路径覆盖，内核执行器按 `(path, attr)` 缓存函数对象，不重新加载。
- 证据：`capability_usecases.py:74-82`（条件 `impl.source and not impl.entrypoint`，`:75`）；`infrastructure/persistence/file_code_store.py:13-19`（路径由 `ws{wid}/{function}` 决定，覆盖写）；`ontoRK/src/ontology_runtime/infrastructure/python_executor.py:79-82`（`key = (str(path), attr)`，命中缓存直接返回旧函数）。
- 控制论解释：控制器以为修改了执行器，实际执行器没变——典型的**执行器失配（actuator mismatch）**，闭环增益变成 0，人机回路里人永远等不到效果，且没有任何反馈说"没生效"。
- 后果：改一个充值逻辑、重新发布、测试通过（测试用新 platform 实例），线上进程仍跑旧代码；直到进程重启才"突然改变行为"。

**S5 发布与执行/读取无并发保护** `[缺陷]`
- 现象：`publish_schema` 不持 `self._lock`，而 `describe/invoke/apply_delta` 都持锁。
- 证据：`kernel_gateway.py:136-171`（无 `with self._lock`）对比 `:186-187`、`:239`、`:274`。
- 控制论解释：控制器换模型与执行器读模型不在同一临界区 = 同一时刻不同线程看到不同版本的"世界模型"，闭环失去一致性假设。
- 后果：线程 A 在 `invoke` 里用旧 runtime（`:250`），线程 B 中途发布换掉 `self._runtime`；A 的 mutation 按旧 schema 校验、写入新 schema 的 store；`seed/apply` 与 schema 校验之间可能出现类型不匹配。并发发布 + 投影运行时还会出现"影子写入"。

**S6 编译产物无回滚/无 dry-run** `[缺陷]`
- 证据：`ontology_usecases.py:196-198` 直接 `compile` → `gateway.publish_schema`；`compiler.py:28-63` 只做静态检查；失败只抛 `CompilerError`（`errors.py:20-22`），已 save 的生命周期不回滚（S3）。
- 后果：坏 payload 一次发布即造成运行时不可用（`_require_runtime` 抛 `RuntimeUnavailableError`，`kernel_gateway.py:177-183`），所有 invoke 立刻返回 error（`api.py` 层仍 200），系统进入"控制器饱和"但没有降级预案。

### 2.3 运行时网关（控制器 ↔ 执行器的交界）

**G1 一把 workspace 级 RLock 覆盖整个执行期（含 LLM / 用户 python）** `[缺陷，P0]`
- 现象：`invoke` 在 `with self._lock:` 内调 `runtime.invoke`；agent/workflow 能力在调用栈深处再重入网关，LLM 的 HTTP 往返与用户代码执行全在锁内。
- 证据：`kernel_gateway.py:235-250`（锁在 `:239`，`runtime.invoke` 在 `:250`）→ `infrastructure/execution/platform_executor.py:56-67`（agent 直接 `agent(...)`）→ `application/agent_runner.py:81-84`（`self._provider.complete(messages)`，最多 8 步）→ 工具调用再进网关（`agent_runner.py:134,138-150`，靠 RLock 重入不死锁）；workflow 的 `set_property` 同样重入 `apply_delta`（`workflow_runner.py:112-117`）。锁注释自证："one lock for every runtime touch"（`kernel_gateway.py:120-122`）。
- 控制论解释：把"观测 + 决策（LLM）+ 执行"全部塞进一个串行临界区 = 系统的**带宽被最慢环节决定**。这不是并发控制，是"单线程中央控制器"：一次外部 LLM 超时（数十秒）会让该 workspace 的所有读请求排队，包括 `/changes` 与 `/state/get`——**观测器被控制器堵死**，操作员看不到出了什么事。
- 后果：`ThreadingHTTPServer` 下线程堆积（`api.py:415-416`，无上限、无超时）；`/api/health` 仍然 `ok`（`api.py:148-158`），对外表现为"健康但全站超时"；锁不可中断，进程无法优雅退出。

**G2 `_record` 无 execution_id，审计/变更/执行三者无法对齐** `[缺陷]`
- 现象：change 日志条目只有 `seq/ts/source/changes/function`；`InvocationResult.execution_id` 在内核里生成，没有被写进任何持久记录。
- 证据：`kernel_gateway.py:255-256` + `:401-406`；`capability_usecases.py:120-124`（审计 detail 有 target/ok/error/changes，**无 execution_id**）；`interfaces/api.py:125-130` 返回体里有 execution_id，但那是给客户端的，服务端不落库。
- 控制论解释：三个观测通道（审计、变更流、调用返回值）各自编号，无法做**因果闭合**——出了事只能靠时间戳近似对齐（且两侧都是秒级 ISO 字符串，`kernel_gateway.py:93-94` 与 `common.py:26-27` 同格式同分辨率）。
- 后果：用户报"余额不对"时，无法从 execution_id 追到哪条 change、哪条审计、哪次投影；事故复盘只能猜。

**G3 change 流无上限、无压缩、启动全量重放** `[缺陷]`
- 现象：`self._changes` 是只增 list；持久化 jsonl 每次 `_record` 追加，启动时整文件读回内存；`observe_changes(after_seq)` 返回**所有** `seq > after` 的条目。
- 证据：`kernel_gateway.py:112`、`:118-119`（启动重放）、`:397-399`（无 limit）、`:407-413`（追加写）、`:415-429`（逐行读回）；API `api.py:258-260` 直接透出，无 `limit`。
- 控制论解释：这是**无限记忆的观测器**——没有遗忘因子，没有滑动窗口，观测延迟与内存随历史线性增长，最终把"观测"变成"归档"。
- 后果：长时间运行的 workspace 里 `GET /changes?after=0` 可能返回几十万条（单响应几十~几百 MB，`_send` 一次性 `json.dumps` 全部内存化，`api.py:112-119`）；重启的启动时间与内存随历史增长；消费者第一次接入必须从 0 开始拉全量。

**G4 `apply_delta` 校验缺口（3 处）** `[缺陷]`
- 证据与缺口：
  1. 关系端点存在性不校验（`kernel_gateway.py:359-377` 只查类型；真实检查在 `:352` 的内核 `apply` 里，且抛的是非 `PlatformError`）——见 P4。
  2. 同一次 delta 内重复 id：`existing = self._store.get_object(ref)`（`:303`）只看已落库状态，看不到本批次尚未提交的 creates，于是同一 `Type/Id` 的两条 upsert 会生成**两个 `Object`** 进 `creates`；`seed` 是"最后一次覆盖"（`ontoRK/.../memory.py:35-36`），`applied` 里却报两条 `create_object`。
  3. `unlinks` 对不存在的边静默成功（`:329-338` 只校验类型），"删除成功"与"本来就没有"不可区分。
- 控制论解释：校验器（模型验证）与执行器（状态突变）覆盖面不一致，导致**动作的预期效果与真实效果可观测地不同**；控制律里混入了不可辨识的静默成功。
- 后果：投影数据里常见的重复行（同一 `vid` 出现两次）会产生自相矛盾的变更记录；对账时 counts 对不上。

**G5 `observe_changes` 只有轮询语义，无服务端游标** `[缺陷]`
- 证据：`kernel_gateway.py:397-399`（纯函数式 `after_seq`）；`api.py:258-260`；无 `next_cursor`、无 `wait`/long-poll、无 `has_more`、无 ack。`ports/runtime.py:150-152` 的契约也只说"返回 seq > after"。
- 控制论解释：反馈通道要求**至少一次/恰好一次**语义。现在把"读到哪"这个状态放在消费者内存里，且没有保留期/压缩策略（G3），一旦消费者进程重启就退化成全量重读。
- 后果：UI（`frontend/`）与 Agent 各自维护 `last_seq`，彼此不一致；多副本消费者无法协同；`seq` 本身是进程内计数器（`kernel_gateway.py:113,403`），跨进程会重复（C4）。

**G6 失败动作不进入变更流，`invoke` 的 error 与 changes 语义不一致** `[缺陷]`
- 证据：`kernel_gateway.py:251-256`（有 error 时 `changes` 通常为空，`if changes:` 才记录 → 失败无记录）；`InvocationResult` 同时携带 error 与 changes（`ports/runtime.py:86-97`），但没有"已回滚"标记。
- 后果：失败调用在变更观测里完全隐形；而审计里（`capability_usecases.py:120-124`）有失败记录，只有走 `InvokeCapability` 的路径才有——Agent 工具循环里直接 `gateway.invoke`（`agent_runner.py:151-156`）绕过了审计。

**G7 `find_objects` / `related` 无 limit，投影式 N+1** `[缺陷]`
- 证据：`kernel_gateway.py:221-229`（全量返回、无分页）；`api.py:226-251`（无 limit 参数）；应用查询对每个对象再 `related` + `get_object`（`application_usecases.py:190-201`），`AskOntology` 对候选再扩 1 跳（`intelligence_usecases.py:55-83`）。

**G8 两个写入口语义分叉（`source` 标签的伪区分）** `[缺陷]`
- 现象：语义状态有两个写入口——`invoke`（内核函数内 mutation）与 `apply_delta`（投影/工作流 `set_property`）。两者都用 `_record` 写同一条 change 流，只靠一个自由字符串 `source` 区分（`kernel_gateway.py:256` 与 `:356`，`workflow_runner.py:114-116` 传 `source="invoke"`）。
- 证据：`kernel_gateway.py:268-273`（docstring 明说 source 是标签）；`_record(source=..., **extra)`（`:401-405`）；`api.py:258-260` 无按 source 过滤能力。
- 控制论解释：观测通道里区分"谁改的"靠标签而非结构化的 actor/execution 引用，属于**不可校验的自述**；一旦某处忘了传，来源就永久错标。
- 后果：审计与变更流无法按"人类操作 / Agent / 投影 / 工作流"切分做过速率限制或回滚策略；`tests/test_capability.py:171-172` 只能断言"存在 source=invoke 的条目"，无法断言"这条是哪个 Agent 干的"。

### 2.4 并发与多实例

**C1 `for_workspace` 的 check-then-set 竞态** `[缺陷]`
- 现象：`if workspace_id not in self._gateways:` 之后构造（含 sqlite 打开、change log 全量重放）再赋值，无锁。
- 证据：`infrastructure/runtime/provider.py:23-42`（判断 `:24`，赋值 `:34`；字典在 `:21` 无保护）。
- 控制论解释：两个线程可能各自造出一个执行器实例，短暂存在**两个控制器连着同一个被控对象**；被丢弃的那个实例已经打开 sqlite 连接并写文件。
- 后果：`sqlite` runtime 下同一 workspace 出现两条独立连接写同一 db 文件（`kernel_gateway.py:100-110`；内核 store 的锁是实例内的，`ontoRK/.../sqlite.py:46-47`），并发替换/投影会撞 `database is locked` 或丢更新；泄漏连接与重复重放的开销。

**C2 `gateway_for` 自愈发布的竞态与开销** `[缺陷]`
- 现象：`if not getattr(gateway, "published", True):` 再读 payload 再 `publish_schema` + 重挂 agent runner，整个 check-then-act 无锁。
- 证据：`application/common.py:83-105`（判断 `:92`，发布 `:97-103`）；`:107-125` 每次都 `repo.list("ontology")` + `list("capability")` + 构建 `SemanticIndex`。
- 后果：并发首请求会重复发布（S5 的竞态叠加）；自愈路径本身是 O(ontology+capabilities) 的，而它是**每个用例的入口**（`common.py:91` 每次调用都先 `repo.get("workspace")`），冷启动 storms 时放大为每请求一次全表扫描。

**C3 SQLite 单连接 + 两套连接** `[缺陷/有意]`
- 证据：`infrastructure/persistence/sqlite_repo.py:37`（一个 `sqlite3.connect` + 模块级 `threading.Lock`，`check_same_thread=False`）、`:86`（audit 用**另一个**连接）；`:41-47` 每个 put 自带隐式事务。
- 控制论解释：仓库层把所有写串行化到一把进程内锁，进程内是安全的，但**跨进程没有协调**；两个连接（documents / audit_log）不在同一事务里。
- 后果：①状态变更与审计写入之间存在崩溃窗口（S3/V3）；②多进程部署（C4）时 SQLite 写锁冲突会以异常形式冒到 API 层（映射成 500，`api.py:86-92`）。

**C4 多进程/多实例部署会发生什么** `[推断]` `[缺陷]`
- 部署形态：`api.py main()` 用 `ThreadingHTTPServer` 单进程（`api.py:415-416, 430-443`）；`bootstrap.py:170-197` 的 runtime 与 platform 都落到同一个 sqlite 文件（`runtime_{wid}.db`、`platform.db`）。
- 推演：两个进程同时服务同一批 workspace 时——①各自缓存自己的 gateway（provider.py:21），互不感知；②各自维护 `_seq`（kernel_gateway.py:113）并向**同一个** `changes_{wid}.jsonl` 追加（`:407-413`），seq 会重复/乱序，"after_seq" 语义彻底失效；③2024-partial 文件尾被 `_replay_change_log` 静默跳过（`:422-425`），跨进程写还会产生交错行；④`FileCodeStore` 同路径覆盖写（file_code_store.py:13-19）可能出现截断文件被内核 `exec_module` 读到半截源码（`:85-92`）；⑤自愈发布用各自读到的 payload（S2/S3），集群内 schema 版本可能不同。
- 结论："多实例"在当前实现里不是扩容，而是**控制回路多重化**：同一被控对象上并行跑多个无协调的控制器，属于典型的失稳配置。

### 2.5 接口层（interfaces/api.py）

**A1 无鉴权，且默认身份是 admin** `[有意简化 + 缺陷]`
- 证据：`api.py:205`（`user = query.get("user", "admin")`）、`api.py:285`（`body.pop("user", "admin")`）、`api.py:299,302,305` 等直接把该字符串当 actor 传给用例；权限模型只有 membership 查表（`domain/governance/model.py:70-88`）。CORS 全开 `api.py:107-110`。
- 控制论解释：控制器输入（谁在操作）来自被控系统外部的**不可信量**，没有任何独立校验环节 → 治理回路的设定点由请求方任意指定。
- 后果：任何人只要知道 workspace id 就能以任意成员身份读写（`tests/test_api.py:107-112` 的 403 只是因为传了一个不存在的 `mallory`，不是因为没有伪造空间）；默认 `admin` 是"默认放行"的错误安全侧；审计里 actor 字段不可信。**注意这是 v0.1 明确简化，但默认值是不安全的方向，不属于"合理简化"。**

**A2 无分页 / 无 limit** `[缺陷]`
- 证据：`api.py:208-225`（workspaces/ontology/datasources/projections/capabilities/applications 全量）、`api.py:251`（`find` 全量）；只有 audit 有 `limit`（`:262`）。
- 后果：单响应无界（配合 G3 的 `/changes`），内存放大加序列化停顿；前端没有游标可分页。

**A3 无超时 / 无请求体上限 / 无并发上限** `[缺陷]`
- 证据：`api.py:415-416`（`ThreadingHTTPServer`，请求线程无上限、无 `timeout`）；`api.py:125-131`（`Content-Length` 直接 `read(length)`，无最大值、无 `413`）；调用链上没有 deadline（`kernel_gateway.py:235-250`）。
- 后果：一次超大 body 或 LLM 卡死即可耗尽线程；G1 的锁等待没有上界。

**A4 错误映射与状态码语义失效** `[缺陷]`
- 证据：`_STATUS_BY_ERROR`（`api.py:54-61`）与 `_status_for`（`:86-92`）：未列类型一律 500；`_error` 把 `str(exc)` 与异常类名直接回显（`:121-123`，`:166-167` 的兜底）。
- 具体漏洞：`int(query.get("after", 0))`（`:259-260`）与 `int(query.get("limit", 50))`（`:262`）遇非数字抛 `ValueError` → 500 而非 400；`int(body.get("max_steps", 8))`（`:373,379,390`）同理；`_body` 缺必填键抛 `KeyError` 虽被 `:279-280` catch 成 400，但 `TypeError`/`AttributeError` 走 500。
- 控制论解释：控制回路的**故障信号被压平**。执行器业务失败返回 `200 {ok:false}`（`api.py:340-343`，`tests/test_api.py:84-87` 明确断言），网关不可用也是 200；监控只能靠 body 内容，标准 HTTP 层（状态码、错误率、熔断器）全部失明。

**A5 `/changes` 消费语义与 `/health` 观测盲区** `[缺陷]`
- `/changes`：`api.py:258-260` 直接透出 `observe_changes(after)`，响应是裸数组，无 `next_cursor`/`has_more`/`server_ts`/`limit`；进程重启后 seq 可能回退或重复（C4），消费者无法自检。
- `/health`：`api.py:148-158` 只报 `status/version/llm_configured/backend/runtime_store/console`。**不检查**：runtime 是否已发布、sqlite 是否可写、change log 是否在涨、投影最后成功时间、锁是否被长期持有、审计写入是否成功。G1 的"健康但全站卡死"正是这个盲区。

**A6 Web 静态服务路径处理** `[规模无关]`
- 证据：`api.py:169-188`：`candidate.is_file() and str(candidate).startswith(str(root))` —— 字符串前缀比较而非路径包含判断，`root` 若为 `.../dist`，`.../distX` 也能通过 [推断]；`_send_file` 全量 `read_bytes`（`:190-201`）。当前只服务打包产物，风险低，但比较方式应改 `is_relative_to`。

**A7 客户端可自带 LLM 端点 → SSRF + 可控长阻塞** `[缺陷]`
- 现象：请求体里的 `llm` 对象允许调用方指定 `base_url`/`model`/`api_key`，服务端直接构造 provider 并用于执行；默认 provider timeout 120s，Agent 循环最多 8 步。
- 证据：`api.py:400-412`（`OpenAICompatProvider(base_url=llm.get("base_url", ...))`）；`:372,378,387,390`（把 body 的 provider 传进用例）；`openai_compat.py:20`（`timeout: float = 120.0`）；`agent_runner.py:81-84`（最多 `max_steps` 次 complete，默认 8，见 `api.py:390`）。
- 控制论解释：执行器的外部依赖（LLM）由请求方提供并控制其响应时间，等于**把回路的时滞交给外部**；而 G1 的锁把这部分时滞全局化。
- 后果：①单请求可让工作区串行化最长 ~16 分钟（8×120s，且可被调用方调慢/调大 `max_steps`，`api.py:390` 无上限）；②服务端会代调用方请求任意 URL（内网地址、元数据服务），并把响应内容当工具观察回灌 Agent；③api_key 出现在请求体里（无日志脱敏约定，`api.py:104` 还关闭了访问日志，出事后无痕）。

### 2.6 治理与审计

**V1 失败与拒绝不进审计（开环）** `[缺陷，P0 级]`
- 现象：所有用例的模式是"先 `require`、再动作、最后 `audit_event`"，异常路径没有审计；只有 `InvokeCapability` 显式记录 `ok/error`。
- 证据：`data_usecases.py:112`（require）→ `:146`（apply）→ `:153`（audit）；`ontology_usecases.py:160-172, 215-222`；`capability_usecases.py:117-124`（唯一例外）。
- 控制论解释：审计是治理回路的观测器。只记成功动作 = **只能观测设定点，不能观测误差与饱和**；拒绝（AccessDenied）和失败（ConnectorError/ValidationError/内核错误）恰恰是安全控制最需要的信号。
- 后果：`data.run_projection` 失败后，审计里**没有这条运行**（连"尝试过"都没有）；无法回答"昨天有多少次投影失败""谁在反复尝试越权"；`tests/test_governance.py:79-90` 只断言成功动作存在，未断言失败可见。

**V2 审计失败无人知（`audit_event` 无错误路径、无 outbox）** `[缺陷]`
- 证据：`common.py:57-68` 直接 `self.audit.append(...)`，异常向上抛；但多数调用点在动作成功后（V1），一旦 audit 写失败（磁盘满、sqlite locked）→ 已生效的动作 + 500 响应 + 无审计。`sqlite_repo.py:90-103` 与 `:41-47` 是不同连接，不同事务。
- 后果：审计不可信性；与 V1 叠加后，"动作存在而记录不存在"有两个独立来源。

**V3 审计无强一致序号、双时钟** `[缺陷]`
- 证据：`sqlite_repo.py:22-30` 有 `seq INTEGER PRIMARY KEY AUTOINCREMENT`，但 `list()` 只 SELECT `ts,actor,...,detail`（`:108-118`），**不返回 seq**；内存实现根本没有 seq（`memory_repo.py:37-52`）。时间戳来自两个不同函数（`common.py:26-27` vs `kernel_gateway.py:93-94`），同为秒级 ISO 字符串。
- 后果：消费者无法做"审计 seq 单调游标"式增量拉取；审计与变更流只能靠秒级 ts 对齐，同一秒内多动作顺序不可判定（`/audit` 只给了 `limit`，`api.py:262`）。

**V4 成员校验每请求全表解析** `[缺陷/P2]`
- 证据：`common.py:50-55`：每次 `require` 都 `repo.list("membership", workspace_id)` 并 `Membership.from_dict` 全量解析；`workspace_usecases.py:59-68` 的 `ListWorkspaces` 是 O(工作区 × 成员) 的组合。
- 后果：M 成员、W 工作区时每次列工作区解析 O(W·M) 条 JSON（`sqlite_repo.py:67-80` 返回全表行再解析）；没有缓存、没有成员变更版本号。

**V5 生命周期与运行时状态无一致性校验** `[缺陷]`
- 证据：`domain/ontology/model.py:218-223` 只拦"已发布则不可编辑"；`UpsertObjectType`（`ontology_usecases.py:96-101`）改完即 `save`，**不自动重新编译/发布**，也不标记 runtime "stale"。
- 后果：设计时改了本体（如新增属性）但没点发布，`describe_runtime` 与 `/ontology` 不一致，前端两份数据源；没有任何 `stale` 位点可供 UI 提示——又一个"状态存在但观测不到"的例子。

**有意简化 vs 缺陷 归纳**：`[有意]` 一 workspace 一本体（`ontology_usecases.py:28,46-51`）、memory 后端、静态/rest/sqlite 三种连接器、同步单进程、审计本地 append-only、无 auth。除此之外，P2/P4/P5/S1/S3/S4/G1/G3/V1 都不是"简化"，而是会在真实数据量与故障下**产生错误状态或错误观测**的实现缺陷。

---

## 3. 测试断言了什么、没断言什么

已有断言（语义覆盖得不错，工程属性几乎空白）：

| 断言内容 | 位置 |
|---|---|
| 未发布时 describe 抛错、invoke 返回 error 不抛 | `tests/test_gateway.py:41-47` |
| delta 的 create/update 语义、只产生变化属性 | `tests/test_gateway.py:70-84` |
| 四类非法 delta（未知类型/类型错/未知属性/反向边） | `tests/test_gateway.py:86-98` |
| link/unlink 幂等 | `tests/test_gateway.py:101-118` |
| 重发布后状态保留（version 1→2，结构不变） | `tests/test_gateway.py:121-130` |
| invoke 永不抛（缺文件、未知函数、参数错） | `tests/test_gateway.py:133-151` |
| change seq 单调 | `tests/test_gateway.py:154-165` |
| 链二端到端、重跑幂等（changes==0）、连接器失败上抛、filter/缺 id | `tests/test_projection.py:57-62,65-71,83-93,107-125` |
| HTTP 全流程（设计→发布→投影→invoke→ask）、403、invoke 失败 200 | `tests/test_api.py:45-134` |
| memory/sqlite 仓库与审计行为一致、两实例读同一 db | `tests/test_persistence.py:17-78` |
| 函数失败后状态不变（内核原子性） | `tests/test_capability.py:46-67` |
| agent 能力跨进程重启自愈 | `tests/test_capability.py:229-255` |
| 审计记录四类成功动作 + invoke | `tests/test_governance.py:79-90` |
| 已发布本体不可编辑 | `tests/test_governance.py:93-97` |

没断言的部分（每条都对应上文一个 P 级问题）：

1. **并发**：没有任何 `threading` 压测；`test_api.py` 的 server fixture 虽是 `ThreadingHTTPServer`（`:21-26`），但所有请求串行执行（`:52-90`）。C1/C2/S5/G1 全部无覆盖。
2. **失败注入在"两次写入之间"**：没有 monkeypatch `seed` 或 `apply` 使第二步失败，因此 P4 的半写入从未被触发；`test_projection.py:83-93` 只在 fetch 阶段失败（尚未写状态）。
3. **删除/对账**：没有任何"源行消失后状态应消失"的测试；`unlinks` 无测试经业务路径触发（只有 `test_gateway.py:114-118` 直接构造）。
4. **schema 收窄/迁移**：`test_gateway.py:121-130` 只改 `version` 数字，不删属性不改类型，所以 S1 从未被触发。
5. **重复 id**：没有任何 delta 内同 id 两条 upsert 的用例（G4-2）。
6. **悬挂关系端点**：`test_gateway.py:95-98` 测的是端点**类型反向**，不是端点对象不存在——后者会走到内核 `ObjectNotFound`（P4）。
7. **规模**：全仓最大数据集是 3 行（`tests/conftest.py:18-30`）；无 10³/10⁴ 级投影、无 change log 增长测试；G3 的 `/changes` 全量重放无测试。
8. **延迟/超时/吞吐**：无 benchmark、无 `timeout` 断言、无慢 LLM provider 注入；G1 的锁覆盖 LLM 无测试。
9. **多实例并发写**：`test_persistence.py:63-78` 的第二实例**只读**；`test_capability.py:229-255` 的 platform2 写入时 platform1 已不再写。C4 的 seq 重复、jsonl 交错、同路径代码覆盖均无覆盖。
10. **接口安全与边界**：所有 API 测试都显式带 `user` 参数（`test_api.py:56,62,72,80`），**未测默认 admin 路径**（A1）；无超长 body、无非法 `after`（A3/A4）、无分页（A2）。
11. **失败可见性**：无"失败后审计应有记录"的断言（V1）；无 audit 写入失败注入（V2）；无 seq 暴露断言（V3）。

结论：测试验证的是**功能语义（happy path + 少量非法输入）**，没有验证**控制回路属性**——并发下的稳定性、故障后的可观测性、规模下的时间/内存界。这与本次诊断的结论一致：系统的功能层是闭环的，运行时控制层是开环的。

---

## 4. 平台层 Top 问题排序

### P0（导致语义状态错误或不可恢复的观测缺口，必须先修）

| # | 一句话 | 证据 |
|---|---|---|
| 1 | 投影"动作"被拆成 `seed` + `apply` 两次提交，第二步失败会留下**已提交的 create 且无变更记录**；关系端点存在性只在内核 `apply` 才检查、抛非平台异常 | `kernel_gateway.py:340-357`、`:359-377`；`ontoRK/.../memory.py:86-89`；`sqlite.py:53-54` |
| 2 | 无删除/无对账：投影只产生 upsert+link，`unlinks` 全仓零调用 → 源侧删除永久留在语义状态里 | `data_usecases.py:140-143,185-201`；`ports/runtime.py:47-53` |
| 3 | 发布链路先落库后发布、失败无回滚；且 **python 源码重发布不重写文件 + 内核按路径缓存** → 重发布静默失效 | `ontology_usecases.py:168-172,204-214`；`capability_usecases.py:75-82,88-90`；`python_executor.py:79-82` |
| 4 | 一把 workspace RLock 覆盖 invoke 全栈（含 LLM 8 步循环与用户代码），单次慢调用冻结整个 workspace 的读写与观测，健康检查无感 | `kernel_gateway.py:120-122,239-250`；`platform_executor.py:56-67`；`agent_runner.py:81-84`；`api.py:148-158` |
| 5 | 无鉴权且无身份时默认 `admin`，CORS 全开：任意调用方可以任意成员身份读写任意 workspace，审计 actor 不可信 | `api.py:205,285,107-110`；`domain/governance/model.py:70-88` |

### P1（会造成错误/陈旧状态或长时间不可用）

| # | 一句话 | 证据 |
|---|---|---|
| 6 | 全量快照 + 无水位 + 无幂等运行键 + 多 asset 无一致采样：重跑/并发跑互相覆盖，delta 可能基于不存在的快照 | `data_usecases.py:124-155`；`domain/data/model.py:178-203` |
| 7 | change 流无上限、无压缩、启动全量重放，`/changes?after=0` 可返回全部历史；重启时间与内存随历史线性增长 | `kernel_gateway.py:112,118-119,397-399,415-429`；`api.py:258-260` |
| 8 | `publish_schema` 不持锁，与读/执行并发替换 world model；失败后设计时=已发布、运行时=旧/无 | `kernel_gateway.py:136-171` 对比 `:186-187,:239`；`ontology_usecases.py:168-172` |
| 9 | `for_workspace` check-then-set 无锁 → 同 workspace 两个 runtime/两条 sqlite 连接；`gateway_for` 自愈发布同样无锁且每请求走 O(本体+能力) 路径 | `provider.py:23-42`；`common.py:83-105` |
| 10 | 审计/变更流/调用返回值无 `execution_id` 关联；失败与拒绝不入审计（invoke 是唯一例外，且 Agent 工具循环绕过它） | `kernel_gateway.py:255-256,401-406`；`capability_usecases.py:120-124`；`data_usecases.py:153`；`agent_runner.py:151-156` |
| 11 | 接口无分页/limit、无超时、无请求体上限；业务失败与运行时不可用都返回 200，HTTP 层失明 | `api.py:125-131,251,340-343,415-416` |
| 12 | 同一 delta 内重复 `Type/Id` 会产生两条 create 与两条变更记录（校验只看已落库状态） | `kernel_gateway.py:302-305,340-350` |

### P2（治理/工程性，规模上来后变 P1）

| # | 一句话 | 证据 |
|---|---|---|
| 13 | `health` 只报 `ok`，不检查运行时是否发布、仓库可写、change 积压、投影最后成功时间 | `api.py:148-158` |
| 14 | 每请求全量解析成员 + 列工作区 O(W·M)；无缓存、无成员版本号 | `common.py:50-55`；`workspace_usecases.py:59-68`；`sqlite_repo.py:67-80` |
| 15 | 审计无对外 seq、`/audit` 只有 limit；审计与变更流双时钟、秒级分辨率，无法判定同秒顺序 | `sqlite_repo.py:105-118`；`memory_repo.py:37-52`；`common.py:26-27` |
| 16 | 连接器全量 `fetchall()` 进内存、无大小上限；rest 仅单请求 timeout，无重试/退避 | `sqlite_ds.py:18-24`；`rest_ds.py:18,27-36` |
| 17 | `find` 无 limit；应用查询/问答是 N+1 展开（每对象再 related+get） | `api.py:251`；`application_usecases.py:190-201`；`intelligence_usecases.py:55-83` |
| 18 | 错误映射：非数字参数变 500，`str(exc)` 与异常类名直接回显 | `api.py:54-61,86-92,121-123,259-262` |
| 19 | 静态资源路径用字符串前缀判断目录包含；`_send_file` 全量读入 | `api.py:182,190-201` |
| 20 | 生命周期变更不标记 runtime stale、不触发重编译，`/ontology` 与 `/runtime` 可长期不一致 | `domain/ontology/model.py:218-223`；`ontology_usecases.py:96-101` |

### 修复方向（按控制回路改造，非逐条打补丁）

1. **把"动作"变成真正的最小原子步**：`apply_delta` 统一走一次存储事务（存在性校验前移进网关；或让内核提供 `seed+apply` 的原子入口），失败即零写入；变更记录与写入同事务（outbox）。
2. **补上误差信号**：投影记录 `run_id/source_watermark/last_success_at`，支持 `since` 增量与 `unlinks`（删除/对账），并暴露"源计数 vs 状态计数"的对账接口——这才是闭环。
3. **把锁降级为状态锁**：锁只保护 `_runtime`/`_store` 的读写，LLM 与用户代码在锁外执行（或改为 per-invocation 的租约 + 快照）；`publish_schema` 与读者共用同一临界区。
4. **统一执行身份**：`execution_id` 贯穿 change log / 审计 / 响应；失败与拒绝也写审计（含 reason）；`/changes` 支持 `limit` + `next_cursor` + 保留期。
5. **接口硬化**：默认拒绝（无身份即 403）、CORS 白名单、`limit`/超时/请求体上限、业务失败区分 4xx/5xx 或至少在 body 加 `ok` 并上报指标；`health` 增加真实依赖探针。
