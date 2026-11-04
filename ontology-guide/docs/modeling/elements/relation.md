# 对象属性与关系

对象属性连接两个个体，是本体的骨架：类只说明“有哪些类型”，关系才把图和语义连起来。建关系时真正要做的决策只有三个——方向怎么定、语义多强、什么时候需要中间个体。

## 命名与方向

对象属性用一个能读成句子的动词或介词命名，局部名用小写驼峰：`shop:placedBy`（订单由客户下单）、`shop:hasLine`（订单含订单行）、`shop:reportsTo`（员工向经理汇报）。方向选“更自然、更常被查询”的那一侧，另一侧用逆属性补齐。

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

shop:placedBy a owl:ObjectProperty ;
  rdfs:domain shop:Order ;
  rdfs:range shop:Customer ;
  owl:inverseOf shop:placedOrder ;
  rdfs:label "下单客户"@zh .

shop:hasLine a owl:ObjectProperty ;
  rdfs:domain shop:Order ;
  rdfs:range shop:OrderLine ;
  owl:inverseOf shop:lineOfOrder ;
  rdfs:label "包含订单行"@zh .
```

`owl:inverseOf` 不复制数据，它让推理器能从“订单由客户下单”推出“客户下了订单”，因此更适合把两头都定义清楚，而不是让数据里同时写两份方向、结果还可能不一致。

## domain 与 range 是推理公理，不是校验约束

这是本体工程中最常见的误解。`rdfs:domain` 与 `rdfs:range` 的作用是**类型推断**：如果某个三元组使用了该属性，推理器就推断主语属于 domain、宾语属于 range。它不会检查、也不会报错。

```turtle
# 数据方向写反了：客户 placedBy 订单
@prefix data: <https://example.com/shop/data/> .
@prefix shop: <https://example.com/shop/ontology#> .

data:customer-1001 shop:placedBy data:order-1 .
```

配合上面的定义，推理器不会报“类型错误”，而是推出两个结论：`data:customer-1001` 是订单，`data:order-1` 是客户。开放世界假设下，这不一定造成不一致（除非再声明这两个类不相交），于是错误数据被“合法化”了。

所以：**需要校验就写 SHACL**。同一个含义，在 SHACL 里是明确的检查：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .

shop:OrderShape a sh:NodeShape ;
  sh:targetClass shop:Order ;
  sh:property [
    sh:path shop:placedBy ;
    sh:class shop:Customer ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
    sh:message "订单必须且只能有一个下单客户，且客户类型必须为 Customer" ;
  ] .
```

同理，`rdfs:range` 写的 `xsd:decimal` 也不阻止你填入字符串——那是 SHACL 的 `sh:datatype` 的职责。把 domain/range 当校验用，会让团队误以为数据已经受控，问题往往在上线后才暴露。

## 常用属性特征

| 特征 | 语义 | 典型用途 | 注意 |
|---|---|---|---|
| `owl:inverseOf` | 逆关系 | 双向查询 | 两个属性语义必须真正互逆 |
| `owl:TransitiveProperty` | 传递 | 上级组织、包含关系、祖先类目 | 成环会推出自反关系，配合非自反声明才会不一致 |
| `owl:SymmetricProperty` | 对称 | 合作、相邻 | 不要用于有方向的业务关系 |
| `owl:FunctionalProperty` | 至多一个值 | 身份证明号、主联系方式 | 缺少值不报错；不能当“必填” |
| `owl:InverseFunctionalProperty` | 值唯一标识主语 | 邮箱反查账号 | 与唯一性约束不是一回事 |
| `owl:propertyChainAxiom` | 属性链推出新关系 | 部门负责人 = 部门-上级-负责人 | 链过深会增加推理成本 |
| `owl:hasKey` | 关键值标识个体同一性 | 员工工号 | 推出 `sameAs`，不拒绝重复数据 |

以组织汇报链为例：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

shop:reportsTo a owl:ObjectProperty , owl:TransitiveProperty ;
  rdfs:domain shop:Employee ;
  rdfs:range shop:Employee ;
  rdfs:comment "传递的汇报关系；不要与组织架构中的虚线汇报混用。"@zh .

shop:departmentHead a owl:ObjectProperty ;
  owl:propertyChainAxiom ( shop:belongsToDepartment shop:departmentManager ) .
```

## n 元关系：不要硬塞进二元属性

订单、商品、数量、成交价构成一个典型的四元关系。用二元属性只能表达“订单买了商品”，数量、单价无处安放——挂到订单上会丢失行级信息，挂到商品上则完全错误。正确做法是具体化（qualified relation）：引入“订单行”个体承载关系，每个订单行指向订单、商品，并自带数量与金额。

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

shop:OrderLine a owl:Class ;
  rdfs:label "订单行"@zh ;
  rdfs:comment "订单与商品之间的限定关系，承载数量、成交价等行级属性。"@zh .

shop:lineProduct a owl:ObjectProperty ;
  rdfs:domain shop:OrderLine ;
  rdfs:range shop:Product .

shop:quantity a owl:DatatypeProperty ;
  rdfs:domain shop:OrderLine ;
  rdfs:range <http://www.w3.org/2001/XMLSchema#integer> .
```

这类结构在本体设计模式里叫“限定关系/情形”模式，更多变体见[本体设计模式](/modeling/patterns)。

## 关系 vs 外键、关联表

| 维度 | 对象属性 | 外键/关联表 |
|---|---|---|
| 本质 | 语义关系，可被推理 | 存储与引用完整性机制 |
| 方向 | 可有逆属性 | 单向，反向靠索引 |
| 校验 | 需 SHACL/RDFS 配合 | 数据库约束 |
| 跨库 | 用 IRI 全局指代 | 依赖本地 ID |
| 多值 | 天然多值 | 关联表或 JSON |

做语义集成时，外键只说明“物理上关联”，对象属性还要说明“在业务上是什么关系”。两者不是替代关系：关系库里照常用外键，本体层负责跨系统的语义表达。

## 基数放在哪

- **一致性用 OWL**：`owl:minCardinality`、`owl:maxCardinality`、`owl:exactCardinality` 是公理，配合推理器判断模型是否自相矛盾，但开放世界下“数据暂缺”不会触发错误。
- **业务校验用 SHACL**：`sh:minCount`、`sh:maxCount` 在封闭世界语义下直接报错，适合“订单必须有且仅有一个客户”这类规则。

两者可以并存：OWL 里表达“逻辑上不可能有两个主客户”，SHACL 里表达“发布数据时必须有一个客户”。分工原则见[约束与业务规则](/modeling/elements/constraint)。

## 关系建模的四条经验

- **先问“这条关系是否需要独立存在”**：能被其他属性描述、有生命周期、需要被单独引用时，具体化成个体；否则保持二元属性。
- **一个语义只建一个属性**：“购买”“下单”“下单于”不要各建一个，用逆属性与查询方向解决。
- **关系方向服从业务主语**：优先让“主角”当主语，查询时用逆属性或反向模式。
- **命名要能被业务复述**：把属性名读成一句完整的话，业务方听不懂，说明语义还没定义清楚。

## 什么时候用属性链

属性链适合表达“可由路径推出”的关系，例如“部门负责人 = 部门所属上级部门的负责人”。它能减少冗余数据，但链过深或引入循环会让推理变慢、结果难解释，因此只用在稳定且高频的 CQ 上。若某个关系需要频繁查询却没有稳定推导路径，宁可显式存储。

## 常见坑

1. **双向属性没有用 `owl:inverseOf` 对齐**，数据写两份，长期不一致。
2. **用 `owl:FunctionalProperty` 当必填**：它只限制“至多一个”，不限制“至少一个”。
3. **把取值放到关系上**：“含税价”是数据属性，不是对象属性；关系只连接个体。
4. **方向随查询方便而改**：方向应服从业务语义，查询方向用逆属性或 SPARQL 反向模式解决。
5. **属性链过深**：三层以上属性链会让推理变慢，也难以向业务解释。

关系建好后，接着处理[数据属性与取值](/modeling/elements/attribute)：关系连接个体，数据属性给个体挂上具体值。
