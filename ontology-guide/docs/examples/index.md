# 端到端案例：电商商品与订单本体

前面几页分别讲了元素与标准，这一页把它们串起来：从能力问题出发，建一个覆盖商品、客户、订单、订单行、地址的最小可用本体，再用样例数据、SPARQL 和 SHACL 证明它真的能用。案例刻意保持小，目的是让整条链路可读、可复现。

## 范围与能力问题

范围限于“商品展示与订单查询”，不覆盖库存、支付、优惠券和物流轨迹。先写 6 条能力问题（CQ）：

| 编号 | 能力问题 | 验收方式 |
|---|---|---|
| CQ1 | 某客户还有哪些未取消的订单？金额多少？ | SPARQL |
| CQ2 | 电子类目下有哪些商品（含子类目）？ | SPARQL 属性路径 |
| CQ3 | 已支付超过 24 小时仍未发货的订单有哪些？ | SPARQL + 时间比较 |
| CQ4 | 哪些订单行的成交价与商品标价不一致？ | SPARQL 对账 |
| CQ5 | 客服视图里，一个订单的客户、地址、商品明细是什么？ | SPARQL |
| CQ6 | 录入的订单数据是否完整、格式是否合规？ | SHACL |

CQ 的价值在于“可判定”：每条都能落到查询或校验上，模型是否有用一目了然。CQ 也不是一次性文档，每轮迭代先复核 CQ 是否仍成立，再决定加不加概念。写法要点见[能力问题（CQ）](/modeling/method/competency-questions)。

## 复用取舍：schema.org 与 GS1

电商概念与 schema.org 高度重合（`schema:Product`、`schema:Offer`、`schema:Order`、`schema:OrderItem`），商品条码领域有 GS1 的 GTIN 标准。本案例的选择是：

- **核心业务类自建**：订单、订单行、客户、地址需要承载内部编码、状态机与校验规则，自建才能控制语义。
- **对齐而非 import**：用 `owl:equivalentClass` 或 `skos:exactMatch` 把 `shop:Product` 对齐到 `schema:Product`，供外部检索与交换使用（下方 TBox 给出了 `skos:exactMatch` 的写法）。
- **GS1 只借用标识**：`shop:gtin` 作为数据属性存储 GTIN，不引入 GS1 全套词表。

不直接 `owl:imports` schema.org 的原因很实际：它面向搜索引擎，类多而浅，导入后既不能替你表达业务规则，还会把大量无关的 domain/range 带进推理。取舍原则见[模块化与复用](/modeling/elements/module)。

## 核心类与关系

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix schema: <https://schema.org/> .

<https://example.com/shop/ontology> a owl:Ontology ;
  rdfs:label "电商商品与订单本体"@zh ;
  owl:versionInfo "1.0.0" .

shop:Product a owl:Class ;
  rdfs:label "商品"@zh ;
  skos:exactMatch schema:Product .
shop:ElectronicsProduct a owl:Class ;
  rdfs:subClassOf shop:Product ;
  rdfs:label "电子商品"@zh .
shop:Customer a owl:Class .
shop:Order a owl:Class .
shop:OrderLine a owl:Class ;
  rdfs:comment "订单与商品之间的限定关系，承载数量与成交价。"@zh .
shop:Address a owl:Class .
shop:OrderStatus a owl:Class .

shop:placedBy a owl:ObjectProperty ;
  rdfs:domain shop:Order ;
  rdfs:range shop:Customer ;
  owl:inverseOf shop:placedOrder .

shop:hasLine a owl:ObjectProperty ;
  rdfs:domain shop:Order ;
  rdfs:range shop:OrderLine ;
  owl:inverseOf shop:lineOfOrder .

shop:lineProduct a owl:ObjectProperty ;
  rdfs:domain shop:OrderLine ;
  rdfs:range shop:Product .

shop:shipsTo a owl:ObjectProperty ;
  rdfs:domain shop:Order ;
  rdfs:range shop:Address .

shop:orderStatus a owl:ObjectProperty ;
  rdfs:domain shop:Order ;
  rdfs:range shop:OrderStatus .

shop:orderNumber a owl:DatatypeProperty ;
  rdfs:domain shop:Order ;
  rdfs:range xsd:string .
shop:placedAt a owl:DatatypeProperty ;
  rdfs:domain shop:Order ;
  rdfs:range xsd:dateTime .
shop:paidAt a owl:DatatypeProperty ;
  rdfs:domain shop:Order ;
  rdfs:range xsd:dateTime .
shop:totalAmount a owl:DatatypeProperty ;
  rdfs:domain shop:Order ;
  rdfs:range xsd:decimal .
shop:currency a owl:DatatypeProperty ;
  rdfs:domain shop:Order ;
  rdfs:range xsd:string .
shop:customerId a owl:DatatypeProperty ;
  rdfs:domain shop:Customer ;
  rdfs:range xsd:string .
shop:customerName a owl:DatatypeProperty ;
  rdfs:domain shop:Customer ;
  rdfs:range xsd:string .
shop:sku a owl:DatatypeProperty ;
  rdfs:domain shop:Product ;
  rdfs:range xsd:string .
shop:productName a owl:DatatypeProperty ;
  rdfs:domain shop:Product ;
  rdfs:range xsd:string .
shop:listPrice a owl:DatatypeProperty ;
  rdfs:domain shop:Product ;
  rdfs:range xsd:decimal .
shop:quantity a owl:DatatypeProperty ;
  rdfs:domain shop:OrderLine ;
  rdfs:range xsd:integer .
shop:unitPrice a owl:DatatypeProperty ;
  rdfs:domain shop:OrderLine ;
  rdfs:range xsd:decimal .
shop:lineAmount a owl:DatatypeProperty ;
  rdfs:domain shop:OrderLine ;
  rdfs:range xsd:decimal .
shop:addressLine a owl:DatatypeProperty ;
  rdfs:domain shop:Address ;
  rdfs:range xsd:string .
shop:city a owl:DatatypeProperty ;
  rdfs:domain shop:Address ;
  rdfs:range xsd:string .
shop:postalCode a owl:DatatypeProperty ;
  rdfs:domain shop:Address ;
  rdfs:range xsd:string .
```

订单行是本案例的**限定关系**：订单与商品是多对多关系，还带数量与成交价，因此必须具体化为 `shop:OrderLine`，而不是在订单上挂一个 `shop:buysProduct`。这个套路可复用到“合同-商品-单价”“项目-人员-工时”等场景，见[对象属性与关系](/modeling/elements/relation)。

## 关键公理

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# 订单至少有一个订单行（存在性，不是必填校验）
shop:Order rdfs:subClassOf [
  a owl:Restriction ;
  owl:onProperty shop:hasLine ;
  owl:someValuesFrom shop:OrderLine
] .

# 四种状态是 OrderStatus 的个体
shop:StatusDraft     a shop:OrderStatus .
shop:StatusPaid      a shop:OrderStatus .
shop:StatusShipped   a shop:OrderStatus .
shop:StatusCancelled a shop:OrderStatus .

# 一个订单至多有一个状态
shop:orderStatus a owl:FunctionalProperty .

# 四种状态互不相同（注意：互斥的“值”用 AllDifferent，
# AllDisjointClasses 用于互斥的“类”）
[] a owl:AllDifferent ;
  owl:members ( shop:StatusDraft shop:StatusPaid
                shop:StatusShipped shop:StatusCancelled ) .

# 业务唯一标识，用于推出个体同一性
shop:Customer owl:hasKey ( shop:customerId ) .
shop:Product owl:hasKey ( shop:sku ) .
```

注意两条边界：`someValuesFrom` 在开放世界下不会因为“没有行数据”而报错；`owl:hasKey` 只推出 `sameAs`，不拒绝重复。状态用“函数属性 + `AllDifferent`”表达：前者限制至多一个，后者保证四个状态个体互异，合起来才能对“一单两个状态”判不一致。真正的必填与唯一校验交给 SHACL 和数据库，见[公理](/modeling/elements/axiom)。

## 样例数据

```turtle
@prefix data: <https://example.com/shop/data/> .
@prefix shop: <https://example.com/shop/ontology#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

data:product-1 a shop:ElectronicsProduct ;
  shop:sku "SKU-KB-001" ;
  shop:productName "无线键盘"@zh ;
  shop:listPrice "199.00"^^xsd:decimal .

data:product-2 a shop:Product ;
  shop:sku "SKU-BK-002" ;
  shop:productName "本体建模实践"@zh ;
  shop:listPrice "445.00"^^xsd:decimal .

data:customer-1001 a shop:Customer ;
  shop:customerId "C-1001" ;
  shop:customerName "张三" .

data:address-1 a shop:Address ;
  shop:addressLine "示例路 100 号" ;
  shop:city "上海" ;
  shop:postalCode "200000" .

data:order-ORD20250301 a shop:Order ;
  shop:orderNumber "ORD-20250301" ;
  shop:placedAt "2025-03-01T10:15:00+08:00"^^xsd:dateTime ;
  shop:paidAt "2025-03-01T10:20:00+08:00"^^xsd:dateTime ;
  shop:placedBy data:customer-1001 ;
  shop:shipsTo data:address-1 ;
  shop:orderStatus shop:StatusPaid ;
  shop:totalAmount "1288.00"^^xsd:decimal ;
  shop:currency "CNY" ;
  shop:hasLine data:line-1 , data:line-2 .

data:line-1 a shop:OrderLine ;
  shop:lineOfOrder data:order-ORD20250301 ;
  shop:lineProduct data:product-1 ;
  shop:quantity 2 ;
  shop:unitPrice "199.00"^^xsd:decimal ;
  shop:lineAmount "398.00"^^xsd:decimal .

data:line-2 a shop:OrderLine ;
  shop:lineOfOrder data:order-ORD20250301 ;
  shop:lineProduct data:product-2 ;
  shop:quantity 2 ;
  shop:unitPrice "445.00"^^xsd:decimal ;
  shop:lineAmount "890.00"^^xsd:decimal .
```

数据里刻意保留了 `shop:unitPrice` 与 `shop:listPrice` 两个价格，用来支撑 CQ4 的对账。

## SHACL 校验

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

shop:OrderShape a sh:NodeShape ;
  sh:targetClass shop:Order ;
  sh:property [
    sh:path shop:orderNumber ;
    sh:minCount 1 ; sh:maxCount 1 ;
    sh:pattern "^ORD-[0-9]{8}$" ;
    sh:message "订单号缺失或格式错误" ;
  ] ;
  sh:property [
    sh:path shop:placedBy ;
    sh:class shop:Customer ;
    sh:minCount 1 ; sh:maxCount 1 ;
  ] ;
  sh:property [
    sh:path shop:totalAmount ;
    sh:datatype xsd:decimal ;
    sh:minInclusive 0 ;
  ] ;
  sh:property [
    sh:path shop:currency ;
    sh:pattern "^[A-Z]{3}$" ;
    sh:message "币种应为三位大写 ISO 代码" ;
  ] ;
  sh:property [
    sh:path shop:hasLine ;
    sh:minCount 1 ;
    sh:node shop:OrderLineShape ;
  ] .

shop:OrderLineShape a sh:NodeShape ;
  sh:targetClass shop:OrderLine ;
  sh:property [
    sh:path shop:quantity ;
    sh:datatype xsd:integer ;
    sh:minInclusive 1 ;
  ] ;
  sh:property [
    sh:path shop:lineProduct ;
    sh:minCount 1 ;
    sh:class shop:Product ;
  ] .
```

这份形状把 CQ6 落成了可执行的检查：必填、格式、引用类型、数值下界都在封闭世界语义下判定，缺数据就报错。语法细节见[SHACL 形状约束](/spec/shacl)，规则治理见[业务规则与校验](/application/rules-validation)。

## CQ 对应的 SPARQL

```sparql
PREFIX shop: <https://example.com/shop/ontology#>
PREFIX data: <https://example.com/shop/data/>

# CQ1：客户 C-1001 未取消的订单
SELECT ?order ?number ?total WHERE {
  ?order a shop:Order ;
         shop:orderNumber ?number ;
         shop:placedBy ?customer ;
         shop:orderStatus ?status ;
         shop:totalAmount ?total .
  ?customer shop:customerId "C-1001" .
  FILTER (?status != shop:StatusCancelled)
}
ORDER BY DESC(?total)
```

```sparql
PREFIX shop: <https://example.com/shop/ontology#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

# CQ2：电子类目（含子类）的商品
SELECT ?sku ?name ?price WHERE {
  ?product rdf:type/rdfs:subClassOf* shop:ElectronicsProduct ;
           shop:sku ?sku ;
           shop:productName ?name ;
           shop:listPrice ?price .
}
```

```sparql
PREFIX shop: <https://example.com/shop/ontology#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>

# CQ3：支付超过 24 小时仍未发货
SELECT ?order ?number ?paidAt WHERE {
  ?order a shop:Order ;
         shop:orderNumber ?number ;
         shop:orderStatus shop:StatusPaid ;
         shop:paidAt ?paidAt .
  FILTER (?paidAt < (NOW() - "PT24H"^^xsd:dayTimeDuration))
}
```

```sparql
PREFIX shop: <https://example.com/shop/ontology#>

# CQ4：成交价与标价不一致的订单行（对账）
SELECT ?number ?sku ?sold ?listed WHERE {
  ?order shop:orderNumber ?number ;
         shop:hasLine ?line .
  ?line shop:lineProduct ?product ;
        shop:unitPrice ?sold .
  ?product shop:sku ?sku ;
           shop:listPrice ?listed .
  FILTER (?sold != ?listed)
}
```

CQ5 的客服视图只是把 `placedBy`、`shipsTo`、`hasLine/lineProduct` 组合起来，不再重复。属性路径与聚合的更多用法见[SPARQL 查询与更新](/spec/sparql)。

## 在系统里怎么用

选择两个落地场景，覆盖“写入”和“读取”两端：

**写入校验**：订单数据进入图之前跑 `OrderShape`，违规订单进入修复队列。实现上可以用服务层封装校验，与聚合内不变量形成双层防护——聚合负责“总额等于行金额之和”，SHACL 负责“字段完整、格式与引用合法”。

**日终对账**：每天用 CQ4 扫描成交价与标价不一致的订单行，输出差异清单给运营。它体现本体的核心价值：跨系统数据用同一套语义对齐后，对账不再靠字段名猜含义。

若继续扩展，下一步通常是商品检索（把类目与属性用于筛选和召回）和个性化推荐（依赖客户历史订单），分别见[语义检索与 RAG](/application/semantic-search)与[知识图谱构建](/application/knowledge-graph)。

## 与 DDD 模型的对齐

把本体映射到代码时，边界要清楚：`shop:Order` 是聚合根，`shop:OrderLine` 是聚合内实体，`shop:Address` 与金额是值对象；聚合内的不变量（总额等于行金额之和、状态迁移合法）仍在代码里维护，本体只负责共享语义、查询与校验。`shop:OrderStatus` 的四个个体对应代码里的状态枚举，SHACL 校验对应应用层的 DTO 校验与发布检查。这样的映射让两边各司其职，不需要把推理器塞进聚合，见[实现本体总览](/impl/)。

## 迭代经验

1. **先跑通一条 CQ 再加概念**：本案例第一版没有地址和币种，CQ5 出现后才补，避免了提前设计。
2. **订单行必须一开始就具体化**：把数量、成交价挂在订单或商品上，后期几乎没有无痛迁移路径。
3. **样例数据是测试用例**：每个 CQ 配一条样例数据，比评审类图有效得多。
4. **公理克制**：只保留状态至多一个、状态个体互异、订单行存在性与 key，其余交给 SHACL，推理规模可控。
5. **命名保持稳定**：`shop:` 命名空间与 local name 一旦发布，只增不改，废弃用 `owl:deprecated` 标记。
6. **把 CQ 查询与形状一起纳入版本库**：本体、形状、验收查询同批评审与回归，避免模型改了查询悄悄失效。
