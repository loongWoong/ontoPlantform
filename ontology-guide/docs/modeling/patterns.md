# 本体设计模式

本体建模里有大量重复出现的问题：关系带属性怎么表达、事件怎么建、角色怎么处理、带单位的数量怎么存、历史区间怎么表示。每次从零设计，既慢又容易踩同样的坑。本体设计模式（Ontology Design Pattern，ODP）就是把这些成熟解法沉淀下来复用。

本页给出最常用的九种模式、适用场景与简短 Turtle 示例。原则是：**优先复用模式，其次组合模式，最后才自造**。

## 什么是本体设计模式

ODP 是针对某类建模问题的可复用方案，通常包含涉及的类、属性、公理与使用说明。社区有公开的模式目录（如 ontologydesignpatterns.org），也有按内容模式、逻辑模式、架构模式分类的整理。

它的价值不在“好看”，而在三点：减少重复错误、统一命名与结构、让评审有共同语言。一个团队长期使用同一套模式，模型的可读性会显著高于各自发挥。

## 使用模式的前提

模式服务于能力问题（CQ），不是反过来。选模式前先问：这条 CQ 需要什么查询？查询需要什么结构？如果简单结构就能回答，不要为了“标准”引入复杂模式。

另外要区分三种东西：**模式**是可复用方案，**标准**是规范（如 OWL、SHACL），**风格约定**是团队习惯（如命名）。不要把它们混为一谈。

## 1. n 元关系与限定关系

RDF 的三元组天然是二元的，但“客户在某个时间以某个角色参与订单”是三元甚至更多元的关系。常见解法是把它具体化为一个关系节点：

```turtle
@prefix ex:  <https://example.com/ontology/order#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Order-1001 ex:hasParticipation [
    a             ex:CustomerParticipation ;
    ex:participant ex:Customer-C42 ;
    ex:role        ex:Buyer ;
    ex:since       "2024-01-01"^^xsd:date
] .
```

这就是限定关系（qualified relation）的简化形式：关系节点承载限定信息，两端实体通过属性指向它。当关系只有“谁和谁”时用普通对象属性；一旦关系需要时间、角色、金额、来源等属性，就升级为关系节点。

## 2. 情形与事件

事件模式把“发生了什么”建成个体，而不是属性值。事件节点连接参与者、时间、地点与结果，天然支持多参与者与多时间点：

```turtle
@prefix ex:  <https://example.com/ontology/order#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Shipment-9001 a ex:ShipmentEvent ;
    ex:eventFor      ex:Order-1001 ;
    ex:fromWarehouse ex:Warehouse-WH2 ;
    ex:eventTime     "2024-05-01T10:00:00+08:00"^^xsd:dateTime .
```

情形（situation）模式则用一个节点把一组相关事实打包，表达“在某个上下文下成立”，例如某次报价适用的客户等级、渠道与有效期。它适合上下文相关的规则与快照，不适合替代普通关系。

## 3. 角色

角色是非刚性概念：同一个人可以同时是员工、客户、审批人。直接建 `Employee subClassOf Person` 在简单场景可用，但角色会变，类型不该随之永久改变。角色模式用角色节点表达：

```turtle
@prefix ex:  <https://example.com/ontology/hr#> .

ex:Person-E1 ex:playsRole [
    a           ex:EmployeeRole ;
    ex:roleAt   ex:Organization-O1 ;
    ex:roleFrom "2023-03-01"^^<http://www.w3.org/2001/XMLSchema#date>
] .
```

角色节点承载“在哪个组织、从何时起、什么职责”。如果角色属性很少且稳定，也可以用子类简化；判断标准是“这个身份会不会随时间变化、是否需要保留历史”。

## 4. 部分-整体

部分-整体是最常用的结构关系，但“部分”至少有三种：组件（发动机之于汽车）、成员（员工之于部门）、物质（水之于湖）。它们的传递性与约束不同，不要用一个万能 `partOf` 打天下。

```turtle
@prefix ex:  <https://example.com/ontology/manufacturing#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

ex:partOf a owl:TransitiveProperty ;
    owl:inverseOf ex:hasPart .

ex:Engine-SN88 ex:partOf ex:Car-VIN001 .
ex:Car-VIN001 ex:partOf ex:Fleet-FL1 .
```

BOM 场景建议同时保留具体关系（`ex:hasEngine`、`ex:hasWheel`）与通用关系（`ex:partOf`），前者便于精确查询与校验，后者便于递归展开。要小心 `partOf` 的传递闭包：跨层级查询很方便，但在大图上代价高。

## 5. 计量与数量

金额、重量、长度、时长都不应该以裸数字存储，否则单位丢失、比较出错。数量模式把数值与单位绑定：

```turtle
@prefix ex:  <https://example.com/ontology/order#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Order-1001 ex:hasWeight [
    a               ex:Quantity ;
    ex:numericValue "12.5"^^xsd:decimal ;
    ex:unit         ex:Kilogram
] .
```

成熟做法是复用 QUDT 或 OM 这类单位本体，而不是自建单位表；至少要把单位建成受控个体，而不是字符串 `"kg"`。货币金额同理：金额 + 币种，或直接用带币种语义的金额类型。

## 6. 时间区间

只存一个时间点无法表达“价格在某个区间有效”“状态在某段时间内成立”。时间区间模式用区间节点承载起止时间，涉及历史数据时还要区分**有效时间**（业务上何时成立）与**记录时间**（系统何时写入）。

```turtle
@prefix ex:  <https://example.com/ontology/pricing#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Price-P1 ex:validDuring [
    a            ex:TimeInterval ;
    ex:startTime "2024-01-01T00:00:00+08:00"^^xsd:dateTime ;
    ex:endTime   "2024-12-31T23:59:59+08:00"^^xsd:dateTime
] .
```

也可以用 OWL-Time 的 `time:Interval` 与 `time:hasBeginning` / `time:hasEnd`。无论用哪种，不要用字符串存时间，也不要只存一个时间点。

## 7. 来源与溯源

数据来自哪个系统、经过什么加工、由谁负责，属于溯源信息。PROV-O 是 W3C 的溯源本体，核心是 Entity（实体）、Activity（活动）、Agent（代理）三要素：

```turtle
@prefix ex:   <https://example.com/ontology/order#> .
@prefix prov: <http://www.w3.org/ns/prov#> .

ex:Order-1001 prov:wasGeneratedBy [
    a                     prov:Activity ;
    prov:wasAssociatedWith ex:SAPSystem ;
    prov:endedAtTime      "2024-04-30T09:00:00+08:00"^^<http://www.w3.org/2001/XMLSchema#dateTime>
] .
```

溯源信息不要等到“上线后再补”。数据治理、审计与问题定位都依赖它，而且后期补录成本极高。轻量做法是至少记录来源系统、导入批次与生成时间。

## 8. 值划分

值划分（value partition）把取值空间限定为一组互斥且穷尽的取值，既可以用互斥子类表达，也可以把取值建成个体枚举。典型如订单状态、支付方式、风险等级。它适合“取值有限、每个值有明确语义”的场景：

```turtle
@prefix ex:  <https://example.com/ontology/order#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

ex:OrderStatus a owl:Class ;
    owl:equivalentClass [ a owl:Class ;
                          owl:oneOf ( ex:Open ex:Paid ex:Shipped ex:Closed ) ] .

ex:Open    a ex:OrderStatus .
ex:Paid    a ex:OrderStatus .
ex:Shipped a ex:OrderStatus .
ex:Closed  a ex:OrderStatus .

ex:Open  owl:differentFrom ex:Paid , ex:Shipped , ex:Closed .
```

如果每个值还需要中文名、定义、同义词，用 [SKOS](/spec/skos) 把每个值建成概念个体更合适；如果只需要表达“取值集合”，`owl:oneOf` 足够。值划分的校验（必须在集合内）交给 SHACL 的 `sh:in`，不要指望 OWL 报错。

## 9. 分类与本体

“分类”和“类型”经常被混用。类型是本体意义上的类，回答“它本质上是什么”；分类是给对象贴标签，回答“它属于哪个类目”。商品可以同时属于多个类目，类目体系会随运营调整，因此更适合用 SKOS 词表：

```turtle
@prefix ex:   <https://example.com/ontology/product#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

ex:Product-P7 ex:inCategory ex:Category-Electronics .

ex:Category-Electronics a skos:Concept ;
    skos:prefLabel "消费电子"@zh ;
    skos:broader   ex:Category-All .
```

如果每个类目都建成 OWL 类，会得到成百上千个类，推理变慢且没有语义收益。判断标准：类目需要逻辑定义与推理时用类，只是组织与检索时用 SKOS。

## 模式选用表

| 需求 | 推荐模式 | 关键结构 |
|---|---|---|
| 关系本身有属性 | 限定关系 / n 元关系 | 关系节点 + 参与方 |
| 记录发生了什么 | 事件 | 事件个体 + 参与者 + 时间 |
| 身份随时间变化 | 角色 | 角色节点 + 组织/时间 |
| 层次结构、BOM | 部分-整体 | `partOf` + 具体关系 |
| 金额、重量、时长 | 计量/数量 | 数量节点 + 单位 |
| 有效期、历史快照 | 时间区间 | 区间节点 + 起止 |
| 数据来自哪里 | 溯源 | 活动节点 + 来源系统 |
| 有限取值集合 | 值划分 | 枚举类或 SKOS |
| 运营类目、标签 | 分类 | SKOS 概念 + 归类关系 |

## 什么时候自造模式

现有模式目录不可能覆盖所有业务。出现以下情况可以自造：已有模式语义不匹配、组合后结构过于笨重、领域问题确实独特。自造时必须做三件事：写清适用场景与不适用场景、给出正反例、在团队内评审并纳入内部模式库。否则半年后没人知道这个结构为什么长这样。

## 与 DDD 模式的对照

熟悉 [DDD 建模](https://ddd-fans.github.io/ddd-guideline/modeling/) 的读者可以参考下表，但不要机械对齐：本体模式描述“数据与概念怎么表达”，DDD 模式描述“系统怎么组织与行为”。

| DDD 概念 | 本体对应 | 注意 |
|---|---|---|
| 值对象 | 数量、时间区间、地址等结构化字面量或节点 | 本体里通常表示成有 IRI 或空白节点的结构 |
| 领域事件 | 事件模式 | 事件要不要持久化为个体，取决于是否需要历史查询 |
| 聚合 | 部分-整体 + 约束 | 聚合边界不等于本体层次，不要强行对齐 |
| 限界上下文 | 模块与命名空间 | 见 [模块化与复用](/modeling/elements/module) |

## 常见坑

- **为模式而模式**：简单关系硬套限定关系，查询复杂度翻倍。
- **模式节点命名不统一**：`Participation`、`Relation`、`RoleLink` 混用，等于没有模式。
- **传递闭包滥用**：`partOf+` 在大图上很贵，要限定起点与深度。
- **单位/时间用字符串**：失去可比较、可推理的能力。
- **溯源后补**：上线后才发现无法回答数据来源问题。
- **把 ODP 当标准**：模式是经验总结，不是规范，仍需文档与评审。

模式的下一步是把它放进真实系统：先看 [实现总览](/impl/)，再看 [架构模式](/impl/arch-pattern/)。
