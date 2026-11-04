# 版本与演进

结论先给：**本体是长期资产，IRI 的稳定性比“当前设计是否优雅”重要得多**。代码可以随时重构、重新部署，本体不能——它的 IRI 一旦被别处引用、被历史数据固化，改动的代价就会随时间指数增长。本页给出可执行的版本策略、兼容性判定表、弃用与迁移流程。评审与治理的组织视角见[运营与生命周期](/application/lifecycle)，模块划分见[模块化与复用](/modeling/elements/module)。

## 为什么本体版本比代码版本难

- **IRI 是全局标识**。别处的数据、查询、映射规则都以它为锚点；改一次，所有引用方一起断。
- **数据是持久的**。代码升级失败可以回滚，历史三元组却会长期带着旧语义。
- **没有编译器帮你发现错误**。改一个 `rdfs:range` 不会让构建失败，只会让推理结果悄悄变错。
- **多处解释同一模型**。同一份本体可能同时被检索、校验、报表、对外交换使用，兼容性要按最保守的消费方判断。

因此版本管理的目标不是“记录版本号”，而是三件事：**语义可追溯、兼容可判定、迁移可回滚**。

## 命名空间与版本 IRI

有两种常见策略，选择时最重要的是别让**数据**随版本整体重写。

| 策略 | 形式 | 优点 | 代价 |
|---|---|---|---|
| 哈希命名空间（推荐默认） | `https://example.com/onto/order#Order` | 类 IRI 长期稳定，升级不影响存量数据 | 版本信息只能靠元数据表达 |
| 路径版本 | `https://example.com/onto/order/2.0/Order` | 多版本可并行在线 | 每次大版本都要重写全部数据与查询 |
| 混合 | 稳定命名空间 + `owl:versionIRI` 指向版本 IRI | 兼顾稳定与可追踪 | 需要纪律：发布时必须正确设置 versionIRI |

推荐默认走混合策略：类与属性的 IRI 保持在稳定的哈希命名空间里，版本信息放在本体元数据上。

```turtle
@prefix : <https://example.com/onto/order#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<https://example.com/onto/order> a owl:Ontology ;
    owl:versionIRI <https://example.com/onto/order/2.3.0> ;
    owl:versionInfo "2.3.0" ;
    dcterms:issued "2025-03-01"^^xsd:date ;
    dcterms:modified "2025-06-10"^^xsd:date ;
    rdfs:comment "订单领域本体" .
```

注意 OWL 2 要求 `owl:versionIRI` 与本体 IRI 不同；同一本体 IRI 下不同 versionIRI 表示同一术语体系的多个版本，这是标准里少见的、但很好用的一个区分。早期 OWL 1 的 `owl:priorVersion`、`owl:backwardCompatibleWith`、`owl:incompatibleWith` 等词汇在 OWL 2 中仍保留，但已标记为弃用（deprecated），新模型建议用 `owl:versionIRI` 表达版本关系，记录历史版本时用注释属性（如 `dcterms:replaces`、`prov:wasRevisionOf`）更稳妥。

## 语义化版本怎么套

用 `MAJOR.MINOR.PATCH` 是可行的，但要知道“兼容”在本体里有四层含义：逻辑兼容（新增公理不使旧结论失效）、数据兼容（旧实例仍可用）、查询兼容（旧 SPARQL 仍返回合理结果）、代码兼容（IRI 常量与生成的类不变）。判定兼容性必须按最弱的一层算。

| 变更 | 版本位 | 影响 | 处理动作 |
|---|---|---|---|
| 新增类、新增可选属性 | MINOR | 旧数据与查询不受影响 | 直接发布，检查与既有词是否语义重叠 |
| 新增注释、标签、`rdfs:isDefinedBy` | PATCH | 无语义影响 | 直接发布 |
| 新增 SHACL 必填/范围约束 | MAJOR | 旧数据可能立即违规 | 先以警告级别上线，给修复期再提为违规 |
| 放宽约束、加可选的 `rdfs:subClassOf` | MINOR | 结论变多，通常可接受 | 评估推理结果变化 |
| 新增等价类、属性链、传递属性 | MAJOR 或至少大 MINOR | 推理结果显著变化，可能引入不一致 | 需回归测试与一致性检查 |
| 修改 `rdfs:domain/range` | MAJOR | 类型推断结果改变，可能推出错误类型 | 视为语义变更，写迁移说明 |
| 删除类或属性 | MAJOR | 旧数据成为“孤儿”，查询返回空 | 先弃用至少一个大版本，再删 |
| 收窄取值范围、改属性基数 | MAJOR | 旧数据可能不满足 | 同约束收紧 |
| 用同一 IRI 改语义 | **禁止** | 新旧含义混在同一标识上，无法区分 | 必须新建 IRI，旧 IRI 弃用 |

最后一条是红线。把 `:status` 从“订单状态”改成“支付状态”，即使拼写没变，也是新概念，必须换 IRI。这类事故往往在半年后才被发现，届时已经无法区分哪些数据属于哪种含义。

## 弃用而不是删除

弃用要给消费方留出迁移窗口，通常至少跨一个 MAJOR。

```turtle
:customerName a owl:DatatypeProperty ;
    owl:deprecated true ;
    rdfs:comment "已弃用：请改用 :legalName；计划在 3.0 移除。" ;
    rdfs:isDefinedBy <https://example.com/onto/order> .

:legalName a owl:DatatypeProperty .
```

过渡期用派生保证旧查询还能跑：新属性为权威，旧属性由规则或一次性的 SPARQL Update 派生。

```sparql
PREFIX : <https://example.com/onto/order#>
INSERT { ?s :customerName ?n }
WHERE  { ?s :legalName ?n .
         FILTER NOT EXISTS { ?s :customerName ?n } }
```

这条更新可以重复执行，结果一致（幂等），便于失败重试。派生期间要监控两边数量差异，差异归零后才可进入下一步。

## 数据迁移

推荐顺序是“先兼容、再切换、后清理”，而不是原地大改：

1. **兼容期**：新词上线、旧词弃用、双向派生或双写；查询仍读旧词。
2. **切换期**：代码与查询改为读新词，观察一个发布周期，确认无回归。
3. **清理期**：删掉旧词与派生逻辑，跑一次全量重建，回收空间。

批量改写 IRI 的更新语句要小步执行并保留快照：

```sparql
PREFIX : <https://example.com/onto/order#>

DELETE { ?s :oldStatus ?v }
INSERT { ?s :status ?v }
WHERE  { ?s :oldStatus ?v }
```

几条纪律：

- **迁移前导出快照**（N-Triples/Turtle 均可），它是唯一可靠的退路。
- **迁移脚本必须幂等**，能被安全重跑；否则中途失败就只能人工对账。
- **优先全量重建**：如果知识库可由源数据 + 映射重建，就重建到新版本命名图，验证通过后切换，比原地改写安全得多。
- **按命名图分区版本**，可以并行保留新旧两个图，出问题一键切回。

## 本体注册表与评审

规模一旦超过一个团队，就需要轻量的注册表机制（可以就是一个 Git 仓库加目录约定）：

- **提案模板**：动机、影响的能力问题（CQ）、兼容性判定、迁移方案、回滚方式。CQ 的写法见[能力问题](/modeling/method/competency-questions)。
- **CI 校验**：语法解析、推理器一致性检查、CQ 回归查询、示例数据的 SHACL 校验、与上一版的结构 diff。
- **发布记录**：versionIRI、变更摘要、弃用清单、依赖的本体版本。
- **消费方登记**：谁在用这份本体、能否接受 MAJOR 升级。没有登记机制就无法评估兼容性。

与代码和 API 版本的关系建议是“解耦但可映射”：API 走自己的版本号，本体走自己的版本号，在文档里明确对应关系（例如 API v3 依赖 ontology 2.x）。不要用本体 MAJOR 强行绑架 API 版本，也不要在代码里散落版本号字符串。

## 模块化降低影响面

把本体按模块拆开，每个模块独立命名空间与版本，只升级受影响的部分。`owl:imports` 应**固定到版本 IRI**，而不是指向“最新版”——否则上游一次发布就可能让下游全部失效。这条与[模块化与复用](/modeling/elements/module)里的实践清单一致。

## 变更清单

发布前逐条确认：

1. 是否新增或修改了公理？是否评估过推理结果变化？
2. 是否删除了被引用的类或属性？删除前是否已弃用一个版本？
3. 是否改了 `domain/range` 或约束强度？有没有通知下游？
4. `owl:versionIRI`、`owl:versionInfo`、日期是否已更新？
5. 迁移脚本是否幂等？是否有快照与回滚步骤？
6. CI 是否通过：解析、一致性、CQ、SHACL、diff？
7. 发布记录是否包含弃用清单与消费方影响？

## 常见坑

- **复用 IRI 改语义**：最严重，且最难事后修复。
- **只改模型不改数据**：模型升级了，历史三元组还带着旧词汇，查询开始返回空集。
- **命名空间里带版本**：每次升级都要重写全部数据，最终没人敢升级。
- **`owl:imports` 指向 latest**：上游变更直接击穿下游，且无法复现历史行为。
- **没有 versionIRI 与变更记录**：出问题时无法回答“哪个版本引入了这条错误推理”。
