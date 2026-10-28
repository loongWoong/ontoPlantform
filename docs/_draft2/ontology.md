# 本体论 / 本体工程视角下的 OntoPlatform v0.1 与 OntoRK v0.1
> **分析对象**：`F:\piAgent\ontoPlantform`（本体智能平台 v0.1）与 `F:\piAgent\ontoRK`（Ontology Runtime Kernel v0.1）。
> **分析视角**：形式本体论（identity / TBox-ABox / CWA-OWA / 关系与事件 / 时间 / 版本）+ 本体工程（建模规范、校验分层、演进契约）。
> **与前一版 review 的分工**：`docs/control-theory-review.md` 已论证 §1.4「本体模型 ≠ 控制模型」、§3.2「只有结构，没有动态与不变量」、`cardinality` 未执行（P2-1）、`version` 闲置（P2-2）。本文不重复这些**工程后果**，只回答更前置的问题：**这套东西在本体论上"是什么"，它的语义承诺在哪里不可判定**。重叠处直接引用，不重新论证。
> **证据口径**：静态通读源码与样例文档；未运行服务、未改源码；行号对应当前工作树。未经代码证实、由结构推得的结论标注【推断】。

---
## 0. 结论先行
| # | 判断 | 结论 | 关键证据 |
|---|---|---|---|
| J1 | 它是"本体"吗 | **不是。是"类型化属性图"（typed property graph），RDF 的一个受限子集**：节点 `(type,id)`、边 `(source,relation,target)`、属性是无命名空间键值；无 IRI、无类层次、无公理、无推理、无 datatype facets | `core/refs.py:13-24`；`core/state.py:14-36`；`core/schema.py:24-56`；`compiler.py:29-37`；`core/schema.py:54` |
| J2 | 实例身份可判定吗 | **引用身份可判定（ref 相等），实体身份不可判定**。身份由 Data 域的 `id_field` 临时决定，TBox 不声明"什么算同一个东西"；无代理键、无 merge/split、无冲突检测 | `domain/data/model.py:117`；`domain/ontology/model.py:53-59`；`data_usecases.py:168,182` |
| J3 | 关系是否只有二元且无属性 | **是**：二元、有向、无属性、无关系实例身份、无逆关系声明、基数不执行；边是集合语义，重复事件被吞 | `core/schema.py:33-45`；`core/state.py:30-36`；`memory.py:112-119`；`ontoRK/README.md:159-160` |
| J4 | TBox/ABox 是否混用 | **概念上未分离**，实现上只有"冻结/可变"；更严重的是 ABox 有一条**零校验出生通道** `store.seed()`，读侧也不校验签名 | `core/schema.py:48-56`；`core/state.py:14-27`；`loader/__init__.py:139-154`；`assembly.py:33-35`；`sqlite.py:53-70` |
| J5 | CWA/OWA 冲突点 | **隐含 CWA 但从未声明**：`find` 未命中=空集（negation as failure）；过滤器遇缺失属性直接判 `False`，把"未知"压成"否"；`null` 不可表示，于是"缺失/未知/空"不可区分 | `runtime/runtime.py:140-143`；`filters.py:20-21,40-41`；`core/types.py:38-60`；`core/state.py:25-27` |
| J6 | 时间 | **只有"当前状态"**：无 valid time / 事件时间 / 区间；业务时间降级为普通属性，且同一概念两种表示（`TollTransaction.ts: datetime` vs `TrafficFlow.hour: string`） | `core/state.py:1-5`；`expected-ontology.json:42`；`demo.py:88`；`business-rules.md:39-40` |
| J7 | 版本演进 | **`version` 是死字段**（无代码递增/比对）；发布即终态；无兼容性规则、无迁移、无弃用 API；`design.txt:1750` 却把"本体版本"列为 Platform 责任 | `model.py:147,218-223`；`governance/model.py:24-29`；`ontology_draft.py:162-166` |
| J8 | 词表层 | **是"别名索引表"，不是术语层**：子串匹配、无分词/边界/消歧/上下位/多语言/状态；并把**取值**当概念（"拥堵"→`TollStation.status`） | `intelligence/model.py:28-35`；`model.py:110-136,214`；`expected-ontology.json:81` |
| J9 | 要不要 upper ontology | **不引入 BFO/DOLCE/SOSA 全量**（无推理器、无 OWL 工具链，引入只会变成注释）；**引入最小上部区分** `category ∈ {object,event,value}`（role 书面暂缓），用 facet 而非继承 | `expected-ontology.json:33-44` vs `:46-75`（事件建成对象、同一事实现两种表示） |
| J10 | 推理边界 | **三层分工必须进 v2**：发布时（TBox 良构性 + 旧 ABox 快照联合校验）／运行时（O(1) 实例级与基数）／**根本不该进本体**（计费公式、阈值、滑动窗口、刷新节奏） | 见 §A.9 分层表 |

**"有意简化" vs "设计缺陷"的分界**（后文定级以此为准）：

| 项 | 定性 | 依据 |
|---|---|---|
| 不做推理引擎/规则引擎、不做 Schema Migration、无 Query DSL | **有意简化**（但"不声明逻辑 profile 与兼容性矩阵"才是缺陷） | `ontoRK/README.md:226-234` |
| 关系属性"预留不实现" | **有意简化** | `core/schema.py:37-40`；`ontoRK/README.md:160` |
| `id` 永不全局、单本体/工作区 | **有意简化** | `core/refs.py:1-4`；`application/ontology_usecases.py:28` |
| 身份策略不在 TBox、ABox 可绕过校验 | **本体设计缺陷**（不是省功能，是语义承诺缺失） | §A.1 / §A.4 |
| CWA 未声明、无值域/枚举/单位 | **本体设计缺陷** | §A.2 / §A.4 |
| 事件与对象混用、端点双重表示、无时间模型 | **本体设计缺陷**（模型与现实不匹配，非"少做"） | §A.3 / §A.6 / §A.8 |

---
## A. 形式语义审查
### A.0 先定位：这是"本体"，还是"带 schema 的属性图"？
| 判据 | 本体（RDFS/OWL 意义） | 本项目 | 判定 |
|---|---|---|---|
| 标识 | 全局 IRI，可跨系统引用 | 无命名空间；`ObjectRef` 仅工作区内唯一（`compiler.py:29-37` 不发 namespace） | ✗ 属性图 |
| 类 | 类表达式、subClassOf、交并补 | 只有具名类型（`model.py:53-59`） | ✗ |
| 属性 | Object/Data property，domain/range，inverse/transitive/functional | 只有名字 + 源/目标类型（`schema.py:33-45`） | ✗ |
| 公理 | 不交、等价、key、基数 | 无（`validation.py:15-48`） | ✗ |
| 个体 | ABox 断言，CWA/OWA 可声明 | 有 ABox，profile 未声明 | ◐ |
| 推理 | 分类、一致性检查、查询重写 | 无（`ontoRK/README.md:229`） | ✗ |
| TBox/ABox | 语义分离 | 实现层"冻结/可变"分离（`schema.py:48-56`） | ✓ |
| datatype | 带 facets、单位、语言标记 | 7 个基元 kind，无 facets（`types.py:15-23`） | ✗ |

**结论**：除"Schema/State 分离"外，本体工程的核心要素一个都不在。实际定位是**"带 schema 的类型化属性图 + 能力调用"**——比文档库强（有类型与引用完整性），比知识图谱弱（无 IRI、无共享术语、无推理）。

这个定位本身不是错误：v0.1 的规模适合它。**错误在于把属性图的语义当成"本体"来承诺**——design.txt 通篇称"本体"，却没有声明逻辑 profile、身份判据与约束（见 A.10）。

### A.1 身份（identity）
**现状**：`ObjectRef(type, id)` 是唯一身份载体，`id` 只是"不含 `/` 的非空字符串"，无规范化（大小写、空白、全半角都不处理），无代理键。

- `core/refs.py:13-24`：约束只有"type/id 非空且不含 `/`"。
- `core/refs.py:26-34`：`"Type/Id"` 按第一个 `/` 切分，id 内禁止 `/`。

**关键缺口：身份声明不在本体里。**

- `domain/ontology/model.py:53-59`：`ObjectTypeSpec` 只有 `name/description/properties`，**没有任何 key/identifier 概念**。
- `domain/data/model.py:117`：`ObjectMapping.id_field` —— "哪个字段标识实例"只存在于投影。
- `data_usecases.py:168,182`：投影取 `str(row[id_field])` 直接当对象 id。

由此产生四个可判定的后果：

1. **同一 ObjectType 的两次投影可用不同 id_field**（一个 `plate`、一个 `vid`），同一现实车辆得到两个 ObjectRef，系统无法判定"这是同一个实体"——`SemanticDelta` 只有 upserts/links（`ports/runtime.py:64-66`），没有 sameAs/merge/split。
2. **跨源 id 相撞会静默合并**：两个源都产 `Vehicle/1` 时，`apply_delta` 用 ref 判存在（`kernel_gateway.py:303-305`），后者覆盖前者，无冲突错误、无来源记录。
3. **业务主键可变而身份不可变**：样例以车牌为业务主键（`examples/toll/docs/data-dictionary.md:28`），换牌/过户后同车变新对象、旧对象永不消失。
4. **ABox 单调只增**：`RunProjection` 只构造 upserts/links（`data_usecases.py:140-143`），`SemanticDelta` 无 delete/tombstone（`ports/runtime.py:56-66`），内核也没有 delete mutation（`runtime.py:229-265` 只识别 set_property/link/unlink）。

**判断**：身份策略**不可判定**（就"现实实体同一性"而言）。注意这与"引用稳定"不矛盾：`Vehicle/V001` 作为字符串完全可判定，缺的是**实体同一性判据**（identity criteria）。→ P0。

### A.2 类型与分类
**现状**：7 个 kind（`string/number/integer/boolean/object/array/datetime`），平台与内核同构（`core/types.py:15-23`；`model.py:22-30`），`check()` 只做 isinstance + `fromisoformat`（`types.py:38-60`）。

- **无 facets**：`PropertySpec` 只有 `name/type/description`（`model.py:34-50`）——无 required、default、enum、min/max、pattern、unit、精度、可空。
- **枚举只存在于中文描述**：`status ∈ {free,busy,congested}` 写在 description 里（`expected-ontology.json:11`；`business-rules.md:29-35`），机器无法校验，LLM 只能猜。
- **无单位**：金额、里程、排队长度、费率全是 `number`，单位只在文字里（`expected-ontology.json:41` "应收通行费（元）"；`demo.py:80` `balance: number`）。量纲不可校验，"元/分"混用无法被发现。
- **金额用 float**（`types.py:43-44`），而计费规则要求"四舍五入保留 2 位"（`business-rules.md:16`）——无 decimal/currency 类型。
- **`object`/`array` 是黑盒**：只验 `isinstance(dict/list)`（`types.py:49-52`），无嵌套 schema，不可被 `find` 查询。
- **无类层次**：ObjectType 之间无 subClassOf，"客车/货车"只能是字符串属性（`expected-ontology.json:29`），无法表达"货车是一种车辆"及其属性继承。
- **属性名不校验标识符**：`upsert_object` 直接以 `p.name` 为键（`ontology_usecases.py:86-95`），`validate_ontology` 只查 type（`validation.py:26-30`），kernel loader 同样不查（`loader/__init__.py:70-71`）；样例 `Vehicle.type`（`expected-ontology.json:29`）与类型系统自身的 "type" 概念撞名。

**判断**：类型层是"弱类型键值 + 7 个标量 kind"，**不足以表达业务语义的最小面**（枚举、单位、必填、精度）。P0/P1 见 §B。

### A.3 关系语义
**现状**：定义 `RelationDef(name, source, target, cardinality)`（`core/schema.py:33-45`）；实例 `Relation(source, relation, target)`（`core/state.py:30-36`）。

- **无关系属性**：README 明示"预留不实现"（`ontoRK/README.md:160`），schema 注释同（`core/schema.py:37-40`）。金额/时间/车道只能挂在被物化的事件对象上（见 A.8）。
- **无关系实例身份**：边没有 id，`(source,relation,target)` 即全部身份（`state.py:30-36` 注释）；重复 link 幂等（`memory.py:112-119`；`sqlite.py:173-180`）。
- **集合语义吞掉重复事件**：同一车同一站通行 N 次 = 1 条边。"某车一共交了多少费/通行几次"在关系层不可数；样例靠 `TollTransaction` 对象计数间接恢复一部分，但边与对象之间没有一致性约束。
- **无逆关系**：反向只能靠读参数 `direction="in"`（`runtime.py:145-155`），TBox 不声明 `passed_by`；"被经过"不是本体概念，词汇表也表达不了（`model.py:116-118` 只有 object/property/relation）。
- **无传递/对称/函数性/自反**声明；`related` 只一跳（`runtime.py:155`），不做闭包。
- **cardinality 声明了但不执行，且 HTTP 路径会丢**：
  - 自由文本 `str`（`core/schema.py:45`），loader 不校验取值（`loader/__init__.py:94`），LLM 起草只截 40 字符（`ontology_draft.py:330`），典型值 `one_to_many/many_to_many/many_to_one` 是自定义词表（`expected-ontology.json:52,59,66,73`）。
  - `POST /ontology/relations` 不接收该字段（`api.py:300-303`），内存后端却接收（`bootstrap.py:321-326`）→ **同一本体经 HTTP 与经进程内装载，TBox 不同**。
  - link 时从不检查基数（`runtime.py:246-263`）。

**判断**：关系层只支持"二元、无属性、集合语义、无逆、基数不生效"的边。对通车计费这类业务，它**恰好把最需要挂在边上的东西（金额、时间、车道）驱逐到了错误位置**。

### A.4 约束、公理与 TBox/ABox
**做对的部分**：Schema 冻结、State 可变（`core/schema.py:48-56` frozen；`state.py:14-27`），是 v0.1 最大的本体工程优点（`ontoRK/README.md:45`）。

**混用点**：

- **没有"类断言/实例断言"、"术语/个体"的概念区分**，也没有 IRI、命名图；两者都是 dataclass。
- **ABox 有三条写入通道，校验强度不同**：

| 通道 | 校验 | 证据 |
|---|---|---|
| `runtime` mutation | 强（类型/存在性/属性存在） | `runtime.py:229-265` |
| `gateway.apply_delta` | 中（类型/属性；无值域） | `kernel_gateway.py:287-317` |
| `store.seed()` | **零校验**（可写未定义类型/属性） | `loader/__init__.py:139-154`；`memory.py:34-38`；`sqlite.py:53-70`；`assembly.py:33-35` |

  平台投影的 create 走的正是 `seed`（`kernel_gateway.py:340-350`）：先补校验，但这是"约定"而非"通道保证"，任何直接调用 store 的代码都能让 ABox 出生即违反 TBox。
- **读侧不校验签名**：`related(Vehicle/V001, "has_lane", "out")` 不报错、只是空集（`runtime.py:145-155`）；`find` 只校验属性名与运算符名，不校验值类型（`runtime.py:316-327`）。
- **无状态不变量**：业务规则里的"入口站 ≠ 出口站"（`business-rules.md:17`）、"`lane_id` 必须是出口站的一条出口车道"（`:48`）、"每条车道隶属唯一收费站"（`:46`）全部无法表达——前两者是实例/跨关系约束，第三者需要基数。
- **无公理**：`validate_ontology` 只做引用完整性 + 类型名合法性（`validation.py:15-48`）；无 disjointness、无 domain/range、无 key axiom。

**CWA/OWA（J5 展开）**：

1. CWA 侧：`find` 未命中返回 `[]`（`runtime.py:140-143`；`sqlite.py:84-97` 全表过滤），调用方必然读作"不存在"——negation-as-failure。
2. 不一致侧：`match_object` 属性缺失直接 `False`（`filters.py:20-21`），比较 `TypeError` 也 `False`（`:40-41`）。于是 `status__ne=free` 对"没有 status"的对象为假：CWA 下应为真，OWA 下应为"未知"，两种逻辑都不满足。
3. `null` 不可表示：`TypeDef.check(None)` 对所有 kind 为假（`types.py:38-60`），"未结算/未采样"只能靠"没有该键"；而读取端 `Object.get(key, default=None)` 又把缺失当默认值（`state.py:25-27`）→ **缺失、未知、空值三者不可区分**。

**判断**：TBox/ABox **未真正分离**（只是"冻结 vs 可变"），且"TBox 校验 ABox"的义务没有收敛到唯一入口。这是设计缺陷，不是简化。

### A.5 时间与来源（provenance）
- 状态定义就是"the world as it is right now"（`core/state.py:1-5`）；`Object` 无 `updated_at/version`；`Change` 无 `ts/execution_id`（`execution.py:37-49`）。工程后果已见 control-theory P0-1，此处只谈本体语义。
- **无 valid time**：`TollStation.status` 是"某一时刻的快照，不随时间自动变化"（`business-rules.md:40`），但模型无有效期；旧状态**永久为真**。"10:05 时该站是否拥堵"不可问，只能问"现在"。
- **事件时间降级为普通属性**：`TollTransaction.ts: datetime`（`expected-ontology.json:42`）；`TrafficFlow.hour` 却是 `string`（`demo.py:88`）——同一"时间"概念两种表示，且都无区间/时区/粒度语义（`types.py:53-60` 只做 `fromisoformat`，naive 与 aware 都接受）。
- **无 transaction time**：平台在外置 change log 补了 `source/ts`（`kernel_gateway.py:401-406`），但那是**内核之外的旁路**，本体层无法表达"这条事实由哪个过程产生"。
- **无 provenance（entity/activity/agent）**：审计只有 actor/action/detail（`common.py:57-68`），且不含 `execution_id`。因此**"源断言的事实"与"能力算出来的事实"在 ABox 里完全同构**——`fee` 是按公式算出来的（`business-rules.md:5-16`），却和 `plate` 一样是普通属性，无 `derived_from` 标记。

**判断**：不能解释、不能重算、不能按来源裁决冲突。P1。

### A.6 版本与演进
- `Ontology.version: int = 1`（`model.py:147`），编译器原样写入内核（`compiler.py:32`），**但全仓找不到任何递增或比对**（检索 `version` 仅命中读取与序列化）。
- 发布后本体不可编辑，报错文案是"create a new version or deprecate it first"（`model.py:218-223`）；AI 起草报错同样是"create a new draft version"（`ontology_draft.py:162-166`）。但**系统没有"新建版本"用例，也没有 deprecate API**：`governance/model.py:24-29` 定义了 `deprecate`/`revise` 转移，无调用方；`design.txt:318-327` 的 `Review` 步骤也未实现。用户被指向一条不存在的路。
- **无兼容性规则**：`validate_ontology` 不比对上一版（`validation.py:15-48`）；`publish_schema` 只换定义不迁移状态（`kernel_gateway.py:167-171`，该行为本身正确），但**从不检查既有 ABox 是否仍满足新 TBox**。
- **无 schema 指纹**：`schema_payload` 只存 `schema/bindings/workflows`（`ontology_usecases.py:208-214`），无 version/fingerprint，重启自愈时无法回答"这份 state 属于哪个 TBox 版本"【推断】。
- `design.txt:1750` 把"本体版本"标为 Platform ✅（`docs/completeness-review.md` 据此记为 ✅）——**契约与实现不一致**。

**判断**：版本演进不是"简化"，是**缺席**；它与"本体是设计时资产"的自我定位直接矛盾。

### A.7 词表与语义对齐
- 结构：`VocabularyTerm(term, concept, kind, aliases)`（`model.py:110-136`）；`add_term` 校验 concept 存在（`:195-213`）；`term` 为唯一键、**同名静默覆盖**（`:214`）；alias 无唯一性校验。
- **解析是子串包含**：`any(n and n in text for n in needles)`，只按 term 长度降序（`intelligence/model.py:31-34`）。无分词、无边界、无打分、无上下文消歧（中文短词"站"会命中一切含"站"的问句）。
- **三个消费面**：
  1. `SemanticIndex.context_text` → Agent 系统提示（`intelligence/model.py:37-49`；`agent_runner.py:44-50`）：只给 `name:type`、关系方向、术语→概念，**不给枚举/单位/身份/事件性**，LLM 不知道 `status` 的合法取值，只能读中文描述猜。
  2. `AskOntology`（`intelligence_usecases.py:38-83`）：object 命中→按类型全量 `find`；property 命中**只用于推导 owner 类型、不参与过滤**（`:45-49`）；relation 命中只取 `rdef.source`，target 丢弃（`:50-53`）；无命中回退"前 10 个对象"（`:66`），用属性值字符串包含判断"是否提到"（`:133-138`）。
  3. `add_term` / draft 校验（`model.py:195-214`；`ontology_draft.py:339-365`）。
- **值概念缺失导致类型混淆**：`{"term":"拥堵","concept":"TollStation.status","kind":"property"}`（`expected-ontology.json:81`）。"拥堵"是一个**取值**（`congested`），不是属性；系统没有 value concept（`TYPE_KINDS` 无 enum，`model.py:22-30`），术语表只能勉强指到属性上。
- **术语漂移不可检出**：term 覆盖写、无 status/definition/scope note（`model.py:116-119` 只有四个字段）、无版本、无审计 diff；而 Agent 提示词是发布时烘死的（`ontology_usecases.py:235`；`common.py:116`）。

**判断**：词表层是**别名索引**，不是术语层（TBox 的 T 部分）。P1。

### A.8 与上层本体的对齐；事件 vs 对象
**现状：没有任何上部区分**——对象类型只有名字与中文描述，"事件/对象/角色/质量"全靠命名习惯。样例给出最直接的证据链：

1. `TollTransaction` 是**一个事件**（一次通行），却建成 `ObjectType`，并把端点复制成字符串属性 `plate/entry_station/exit_station/lane_id`（`expected-ontology.json:33-44`）。
2. 同一条 `passes` 表又投影出三条**关系边** `enters/exits/passes`（`bootstrap.py:124-149`）。
3. 于是"车从入口站进"同时是（a）交易对象的两个属性、（b）一条边。
4. **边会被校验端点存在**（`kernel_gateway.py:359-377` + store `_check`），**属性不会** → 两者可以互相矛盾，且没有一致性约束（`validate_ontology` 不涉及）。

**判断：需要最小上部区分，但不需要 BFO/DOLCE/SOSA。**

- `event`：有发生时间、不可变、更正靠补偿事件——必须与"可更新对象"分开，否则时间/来源语义无处安放。
- `value`：值对象/量（金额、里程、排队长度），带单位，不独立存在、无身份。
- `object`：其余可标识、可更新的实体（车辆、收费站、车道）。
- `role`：暂不实现，但**书面声明不做**（避免"驾驶员/车主"被随手建成对象）。
- SOSA/SSN 只对"观测/采样"有意义，而系统恰恰缺观测实体（无 sensor/observation），留到 v3 与观测层一起做。

**理由**：当前缺的不是"哲学严谨"，而是这三个区分**各自对应一个已暴露的缺陷**（时间无处安放、单位不可校验、事件与边双重表示）；facet（`category` 字段 + lint）比引进 OWL 工具链便宜两个数量级。

### A.9 推理边界：哪些约束放哪里
当前无任何推理（`ontoRK/README.md:229`）。但"没有推理器"不等于"没有可判定约束"，应按判定成本分三层：

| 层 | 内容 | 应放哪 | 现状 |
|---|---|---|---|
| ① 发布时（TBox 内可判定） | 标识符合法/唯一；属性 type ∈ TYPE_KINDS；relation source/target 已定义；cardinality 在受控词表；**每个 ObjectType 有 identity**；enum/unit/range 语法；保留名禁用 | `validate_ontology` | 只有引用完整性 + 类型名（`validation.py:15-48`）；identity/cardinality/枚举/单位全缺 |
| ② 发布时 + ABox 快照 | 新 TBox 是否让既有实例违规（收窄/加必填/删属性）；兼容性判定 | `PublishOntology` | **完全缺失**（`ontology_usecases.py:154-173`） |
| ③ 运行时（O(1)/单实例） | 类型/存在性；字段级 min/max/regex/enum；**基数**；值域 | `runtime._validate_mutation` | 只有类型/存在性（`runtime.py:229-265`） |
| ④ 运行时（跨对象，读快照） | 跨关系不变式（lane ∈ exit station 的车道）、入口站≠出口站 | 提交前校验（需同一临界区） | **无**（平台侧 read→validate→apply 无快照保证，`kernel_gateway.py:287-317`） |
| ⑤ **不该进本体** | 计费公式 `max(最低收费, 里程×费率)×折扣`（`business-rules.md:5-16`）；拥堵阈值 50/200 米、80%/60%（`:33-35`）；5 分钟滑动平均（`:39`）；刷新节奏 | 规则/流程/能力 + 可配置阈值实体 | 公式在能力代码/文档；阈值只在文档；ABox 只存**结果**（`fee`、`status`），无输入、无来源 |
| ⑥ 需要推理（建议不做） | 传递闭包（`belongs_to` 链）、属性继承、sameAs 传递 | 物化/预计算，不上推理器 | 无 |

**分层的本体论理由**：①是 TBox 自身良构性；②是 TBox 与既有 ABox 的一致性；③是"单个断言是否合法"；④是"一次提交后世界是否仍满足不变量"；⑤是**规则**（从事实推出决定），其正确性无法用断言表达，只能靠测试与效果验证；⑥超出轻量层能力边界。

**把⑤写进本体是本项目最容易犯的错**（本体变成不可维护的规则库）；把它留在文档里则是现在的问题（模型无法承载判定输入）。

### A.10 与 design.txt §5–§16 的对照：设计口径本身缺失
| 节 | 设计内容 | 实现了什么 | 本体论上缺什么 |
|---|---|---|---|
| §5 | Ontology = ObjectType + Relation + Vocabulary（`design.txt:184-222`） | 三件都有 | 没有身份/类别/约束/时间这些本体必答项 |
| §6 | ObjectType = properties（`:226-253`） | 有 | 属性只有 type，无 facets |
| §7 | Relation 定义 vs 实例（`:255-285`） | 区分正确 | 关系无属性、无逆、无基数语义 |
| §8 | Vocabulary 连接业务语言（`:289-310`） | 有 | 未定义术语粒度（词/概念/取值）、同义、消歧 |
| §9 | Draft→Validate→Review→Publish（`:314-343`） | 有（Review 未实现） | 无版本、无兼容性、无弃用 |
| §10 | Compiler 是防腐层（`:346-372`） | 有，做得好 | 编译丢弃 namespace（`compiler.py:29-37`） |
| §11–§14 | DataSource/Asset/Mapping/Projection（`:375-483`） | 有 | Mapping 无类型转换/null 策略；Projection 无删除 |
| §15–§16 | Data→Kernel，Kernel 是唯一语义状态（`:487-569`） | 有 | 无 provenance（哪条源、哪次运行） |

更硬的证据：`design.txt` 全文检索，**"约束""不变量""继承""基数""单位""主键""同义""上位""公理""枚举""时间"全部 0 命中**（仅"版本"2 处、"唯一"2 处）。

→ 这些不是"实现没跟上设计"，而是**设计口径本身缺失**。这也解释了 `docs/completeness-review.md` 为何能给出 92%：它度量的"design.txt 契约实现度"里，这些契约根本不在。

---
## B. 本体缺陷清单
**定级口径**：P0 = 让调用方得到"看起来对"的错误事实，或让本体语义承诺不可判定；P1 = 显著削弱可治理性/可演进性；P2 = 演进与规模问题。

### P0（语义承诺缺失）
**P0-1 身份不在 TBox，实体同一性不可判定，跨源 id 冲突静默合并。**
证据：`domain/data/model.py:117`（身份只由投影 `id_field` 决定）；`domain/ontology/model.py:53-59`（TBox 无 identity）；`data_usecases.py:168,182`；`kernel_gateway.py:303-305`。　本体解释：没有 identity criteria / key axiom / sameAs，实例只能"按 ref 存在"，不能"按现实同一"。　后果：跨源汇聚=静默覆盖；车牌变更=身份断裂；无法去重/对账/归因。修法：O2、O3。

**P0-2 ABox 有零校验出生通道，TBox 可被绕过。**
证据：`loader/__init__.py:139-154`；`memory.py:34-38`；`sqlite.py:53-70`；`assembly.py:33-35`；平台的 create 走的正是 `seed`（`kernel_gateway.py:340-350`）。　后果：ABox 可含未定义属性/类型；"TBox 保证"不成立；查询结果不可解释。修法：O8（入库侧同源校验）。

**P0-3 无删除/撤回语义，ABox 单调只增。**
证据：`ports/runtime.py:56-66`；`data_usecases.py:140-143`；`runtime.py:229-265`（无 delete mutation）。　本体解释：无法否定一个断言，就无法表达"实体已不存在"与"两个对象是同一实体"。　后果：源删除不传播（control P1-7 同源）；旧对象永久参与查询与 Agent 上下文。修法：O3。

**P0-4 CWA/OWA 未声明，三值语义被压成二值，缺失/未知/空值不可分。**
证据：`runtime.py:140-143`；`filters.py:20-21,40-41`；`types.py:38-60`（null 不可表示）；`state.py:25-27`（缺失读成默认值）。　后果：`find` 空集既可能"确实没有"也可能"过滤类型错"（control P1-1 的语义根因）；"未结算/未采样"不可表达。修法：O1。

**P0-5 属性无值域/枚举/单位/必填，业务语义只存在于中文描述。**
证据：`model.py:34-50`；`expected-ontology.json:11`（status 取值只在 description）；`data-dictionary.md:41`（"元"只在文字里）；`business-rules.md:29-35`。　后果：非法值（`CONGESTED`/`拥堵`）可入 ABox；单位混用不可检出；LLM 与校验器都不知道合法取值。修法：O4、O5。

### P1
| ID | 现象 | 证据 | 本体解释 → 后果 | 修法 |
|---|---|---|---|---|
| P1-1 | `cardinality` 自由文本、不执行、HTTP 路径丢失 | `core/schema.py:45`；`loader/__init__.py:94`；`runtime.py:246-263`；`api.py:300-303`；`bootstrap.py:321-326` | 声明与行为不一致；同一本体两种装载结果不同（TBox 漂移） | O6/O10：受控词表 + 发布校验 + link 时执行 |
| P1-2 | 事件/对象混用，端点双重表示且无一致性约束 | `expected-ontology.json:33-44` vs `:46-75`；`bootstrap.py:124-149` | 同一事实两条路径，一条受校验一条不受；"哪份是真的"不可判定 | O6：`category` + lint |
| P1-3 | 无 valid time，状态快照永久为真 | `core/state.py:1-5`；`business-rules.md:39-40` | 时点/区间问题不可答；状态判定无法复核 | O7 |
| P1-4 | 版本死字段、无兼容性/迁移/弃用 | `model.py:147,218-223`；`governance/model.py:24-29`；`design.txt:1750` | 无演进契约；发布即终态；错误提示指向不存在用例 | O9 |
| P1-5 | 词表是子串索引，alias 冲突，值概念缺失 | `intelligence/model.py:28-35`；`model.py:214`；`expected-ontology.json:81` | 术语漂移不可检出；歧义命中直接进 Agent 上下文 | O5 |
| P1-6 | 无 provenance：派生事实与断言事实同构 | `execution.py:37-49`；`kernel_gateway.py:401-406`；`common.py:57-68` | `fee`（算出来的）与 `plate`（源断言的）无法区分；冲突不可按来源裁决 | O6/O2 |
| P1-7 | 属性标识符不校验，保留名撞车 | `validation.py:26-30`；`model.py:22-30`；`expected-ontology.json:29` | 非法键进 TBox；`type` 等与类型系统概念混淆 | O4 + lint |
| P1-8 | `related` 不校验关系签名 | `runtime.py:145-155` | 非法查询静默返回空集，被读作"没有这个关系" | 运行时检查 `ref.type` ∈ {source,target} |

### P2
| ID | 现象 | 证据 | 修法 |
|---|---|---|---|
| P2-1 | `object`/`array` 无嵌套 schema，是黑盒属性 | `core/types.py:49-52` | 声明"不支持嵌套查询"或加最小 schema |
| P2-2 | 金额用 float，无 decimal/currency | `types.py:43-44`；`business-rules.md:16` | facet `unit` + 定点数 |
| P2-3 | `datetime` 不做时区/格式规范化，字符串比较 | `types.py:53-60`；`filters.py:28-37`；`data-dictionary.md:42` | 规范化 UTC + 显式 tz 要求 |
| P2-4 | HTTP 查询值类型由字面量推断（`"12345"`→int） | `api.py:144,240-250` | 按 TBox 类型解析 |
| P2-5 | `find` 无排序契约（SQL 无 ORDER BY） | `sqlite.py:88-91`；`intelligence_usecases.py:66` | 显式 `order` 或声明无序 |
| P2-6 | 无类层次，子类语义只能靠字符串属性 | `model.py:53-59` | 声明"无 subClassOf"，或引入单继承 |

---
## C. 对 v2 计划的本体工作项（test-first）
> 每项 = 改动点 + 可执行验收断言（先写测试后实现）；括号内为建议落点（Phase 对齐 control-theory §5.3）。

**O1 声明并实现本体逻辑 profile（CWA/OWA、null、unknown）**〔Phase 0〕— 改动：`Ontology` 增 `profile {object_existence: closed, property_completeness: open, unknown_value: "absent"}`；`find` 响应增 `excluded_missing` 计数；文档明写三条语义。
验收：`test_find_reports_excluded_missing`：对象缺 `status` 时 `find(TollStation, status__ne="free")` 返回 `{"items":[...], "excluded_missing":1}`。；`test_filter_type_mismatch_is_error`：`find(TollStation, name__gt=1)` 返回 InvalidArgument，不再返回空集（与 control P1-1 合并验收）。；`test_profile_declared_in_published_schema`：`describe()` 回显 profile，缺字段则发布失败。

**O2 身份进 TBox（identity 声明 + 与投影一致）**〔Phase 0/1〕— 改动：`ObjectTypeSpec` 增 `identity: list[str]`（属性级键，可复合）；校验 identity ⊆ properties 且类型为 string/integer；`CreateProjection` 要求 `id_field` 映射到 identity 声明的字段（或显式 `identity_strategy="surrogate"`）。
验收：`test_publish_rejects_object_type_without_identity`：无 identity 的 ObjectType 发布失败，错误信息含类型名。；`test_projection_id_field_must_match_declared_identity`：`Vehicle.identity=["plate"]` 时 `id_field="vid"` 的投影创建失败。；`test_two_projections_with_different_identities_conflict`：同一 ObjectType 两个投影声明不同身份字段 → 第二个被拒（或需显式覆盖并记录审计）。

**O3 命名空间、ID 冲突检测、撤回语义**〔Phase 0〕— 改动：`ObjectRef` 增可选 `source`（或 id 前缀 `src:<projection_id>/`）；`apply_delta` 检测"同 ref 由不同 source 断言"→ `IdentityConflict`；`SemanticDelta` 增 `deletes`（软删 tombstone）。
验收：`test_cross_source_id_collision_detected`：两个 datasource 都产 `Vehicle/1`，第二次 `apply_delta` 抛 `IdentityConflict`（或产生 `conflict` 审计；二者择一并在文档声明）。；`test_projection_delete_removes_object_from_find`：源行消失后重跑投影，对象从 `find` 消失且变更流出现 `delete` 事件。；`test_ref_rejects_ambiguous_id`：含 `/` 或空 id 的 ref 仍被拒（回归 `core/refs.py:20-24`）。

**O4 属性 facets（枚举/范围/单位/必填/派生）**〔Phase 1〕— 改动：`PropertySpec` 增 `enum/format/min/max/unit/required/derived_from`；发布时静态校验语法；写入时（`apply_delta`/`_validate_mutation`）执行值域校验；`object/array` 标注"不支持嵌套校验"。
验收：`test_publish_rejects_enum_value_invalid`：`enum` 含非法项（非标量）→ 发布失败。；`test_projection_rejects_value_outside_enum`：`status="CONGESTED"`/`status="拥堵"` 的投影运行失败并报行号（当前会成功入库）。；`test_unit_mismatch_rejected`：`balance{unit:"元"}` 写入 `{"value":100,"unit":"分"}` 被拒；同 unit 通过。；`test_required_property_missing_fails_run`：源行缺 required 属性 → 该行失败并按策略上报（fail-fast 或计数）。

**O5 词表契约：value 概念 + alias 唯一 + 弃用**〔Phase 1〕— 改动：`kind` 增 `"value"`，concept 形如 `TollStation.status#congested`；`add_term` 校验 alias 全局唯一；`VocabularyTerm` 增 `status: active|deprecated` + `definition`；`resolve` 返回 `{term, concept, kind, score}` 并支持 value 命中。
验收：`test_vocabulary_alias_must_be_unique`：两个 term 声明同一 alias → 第二个被拒。；`test_value_term_resolves_to_enum_member`：问句含"拥堵"→ 命中 `TollStation.status#congested`（修正 `expected-ontology.json:81` 的 kind）。；`test_deprecated_term_excluded_from_agent_context`：deprecated term 不出现在 `context_text`，但仍可在 `get_ontology` 看到。

**O6 事件/对象区分 + 关系属性替代方案 + lint**〔Phase 1〕— 改动：`ObjectTypeSpec` 增 `category: object|event|value`（默认 object）；`RelationSpec` 增可选 `derived_from`（从哪个 event 派生）；新增 `POST /ontology/lint`：端点属性与同名边并存、event 缺时间属性、value 有 identity、属性名撞保留名。
验收：`test_lint_flags_duplicated_endpoint`：toll 本体（`TollTransaction.entry_station` 属性 + `enters` 边）产生 warning；修复后归零。；`test_event_category_requires_temporal_property`：`category="event"` 且无 `datetime` 属性 → 发布失败。；`test_relation_marks_derived_from`：`passes` 可声明 `derived_from: "TollTransaction"`；未声明且端点属性重复时 lint 报警。

**O7 时间最小模型**〔Phase 1/2〕— 改动：event 类型必须有 `occurred_at: datetime`；snapshot 类属性（`TollStation.status`）标 `temporal: "snapshot"` 并要求 `valid_from`；`datetime` 规范化为带时区 ISO-8601。
验收：`test_event_requires_occurred_at`：event 类缺时间属性 → 发布失败。；`test_datetime_normalized_to_utc`：写入 `2026-05-06T07:07:00` 与 `…+08:00` 后读回同一规范形式，字符串比较可排序。；`test_trafficflow_hour_must_be_datetime`：`hour: string` 被 lint 标记（修正 `demo.py:88`）。

**O8 发布时 ABox 合规 + 兼容性矩阵**〔Phase 1〕— 改动：`PublishOntology` 增 `validate_state_against_schema(old, new, state)`：对所有已存在对象检查属性存在性/类型/enum，失败输出前 N 个样本；`--allow-breaking` 显式覆盖并写审计；新增 `validate_compatibility(old,new) -> compatible|breaking`（加属性=兼容；删属性/收窄类型/改必填=破坏；重命名=破坏+弃用建议）。
验收：`test_publish_rejects_narrowing_when_state_violates`：`status` 收窄为 enum 后，状态里存在 `"CONGESTED"` → 发布失败并列出 1 个样本 ref。；`test_publish_allows_additive_change`：加一个可选属性 → 成功，状态不变。；`test_allow_breaking_requires_audit`：`--allow-breaking` 发布后审计含 sample refs 与操作者。

**O9 本体修订与指纹**〔Phase 2〕— 改动：新增 `POST /ontology/revisions`（从 published 复制 → draft，`version+1`，记录 `parent_version`）；发布时写 `schema_fingerprint`（canonical schema hash）到 `schema_payload` 与每条 change；`describe()` 回显 fingerprint。
验收：`test_new_revision_increments_version_and_links_parent`：revisions 后 `version==2` 且 `parent_version==1`（当前 `model.py:147` 恒为 1）。；`test_publish_writes_schema_fingerprint`：发布后 `schema_payload.fingerprint` 存在；同内容两次发布相同，改一个属性则不同。；`test_gateway_self_heal_detects_fingerprint_mismatch`：仓库 payload 与本体 fingerprint 不一致时 `gateway_for` 记录告警（而非静默用旧 schema）。

**O10 映射语义校验前移（Mapping 对齐 TBox）**〔Phase 0/1〕— 改动：`CreateProjection` 校验 `FieldMapping.property` 存在且类型可转换、`id_field` 存在、relation mapping 的 source/target 字段非空；运行投影时按 `required`/`unknown` 策略统计 `skipped_missing` 并在 summary 返回。
验收：`test_projection_rejects_unknown_property`：`{"property":"fee2"}` 在创建时即拒（当前只在 run 时炸，`kernel_gateway.py:291-296`）。；`test_projection_reports_skipped_rows`：含空 id 的行被跳过时 summary 含 `skipped_missing: 1`（当前静默跳过，`data_usecases.py:169-175`）。；`test_relation_mapping_requires_endpoint_types_loaded`：`bootstrap.filter_mappings` 的语义（`bootstrap.py:442-455`）下沉为平台校验并锁定。

**O11 语义上下文对 LLM 可机读**〔Phase 1/2〕— 改动：`SemanticIndex.context_text` 输出 facets（enum 取值、unit、identity、category、temporal）；术语按 concept 分组；`AskOntology` 的 property 命中参与过滤（不再只推导 owner）。
验收：`test_agent_context_contains_enum_values`：`context_text` 含 `TollStation.status ∈ {free, busy, congested}`。；`test_agent_context_contains_identity`：含 "identified by plate"。；`test_ask_uses_property_hit_as_filter`：问"拥堵的收费站"时构造 `status=="congested"` 过滤（而非全量 find 后回退前 10）。

**O12 最小上部区分落地为 lint 规则集**〔Phase 2〕— 改动：把 O6 的 `category`/`derived_from` 固化为"建模规范 v1"（文档 + lint）；书面声明**不引入** BFO/DOLCE/SOSA，`role` 暂缓；`category="value"` 不得有 identity、不得被关系指向。
验收：`test_value_category_must_not_have_identity`：`value` 类型声明 identity → 发布失败。；`test_value_type_cannot_be_relation_endpoint`：`has_lane` 指向 `value` 类型 → 校验失败。；`test_docs_declare_no_upper_ontology`：`docs/ontology-modeling-guide.md` 存在且包含"不引入 BFO/DOLCE/SOSA"与 profile 声明（CI 关键词检查）。

---
## 结论
当前系统**有一个正确的骨架**（Schema/State 分离、写路径收编、平台-内核防腐层），**但骨架里装的是"类型化属性图"而不是本体**：没有身份判据、没有逻辑 profile、没有值域与单位、没有时间、没有版本演进，事件与对象混用且事实双重表示。 v2 不需要 OWL 或推理器，**只需要把"语义承诺"补成可判定的契约**：身份与类别进 TBox、CWA/OWA 与空值写进文档、枚举/单位/基数成为显式 facets、时间与来源成为一等属性、发布做兼容性与 ABox 合规检查。 这些恰好也是前一版控制论 review 所需的地基：**没有可判定的世界模型，就没有可验证的闭环**。

---

## 附录：本次审查的证据入口（按文件）
| 文件 | 本体相关内容 |
|---|---|
| `ontoPlantform/.../domain/ontology/{model,compiler,validation}.py` | TBox 全貌：属性无 facets、Relation 仅 4 字段、校验仅引用完整性 |
| `ontoPlantform/.../domain/{data,intelligence}/model.py` | 身份声明位置（`id_field`）、投影只增不删；词表消费（子串匹配、上下文烘死） |
| `ontoRK/.../core/{refs,schema,state,types}.py` | 身份/类型/关系/状态的形式定义 |
| `ontoRK/.../{loader/__init__.py,infrastructure/*.py}` | seed 零校验；cardinality 不校验；三值压二值、集合语义、全表过滤 |
| `ontoRK/README.md` §7/§10 | 有意简化的边界（不做事清单） |
| `examples/toll/{expected-ontology.json,docs/business-rules.md,docs/data-dictionary.md}` | 事件建成对象、端点双重表示、规则与约束无载体；业务主键/单位/时间格式 |
| `examples/traffic/demo.py` | `hour: string`、`set`（局部变量）与 `set_property`（世界）语义混淆 |
