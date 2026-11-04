# 个体与实例数据

模式（TBox）回答“领域里有什么”，个体（ABox）回答“具体有哪些”。本体能不能用起来，最终取决于个体数据是否准确、可追溯、可更新。很多项目模式写得很规范，实例数据却靠手工维护、IRI 随意生成，几个月后图里全是重复和悬空引用。

## TBox 与 ABox 的边界

- **TBox**：类、对象属性、数据属性、公理，跨系统共享，变化慢。
- **ABox**：个体、类型断言、关系断言、取值断言，来自业务系统，变化快。

边界不是绝对的。订单状态集合既可以是 TBox 里的枚举类，也可以是 ABox 里的参考数据；判断标准是“由谁维护、以什么频率变化”。模式与实例通常放在不同的文件或命名图中，便于独立版本管理。

## 个体与数据库行的区别

数据库行由表结构定义，靠主键识别，一行的列值是固定的。个体则由 IRI 标识、靠断言描述，可以：

- 同时属于多个类（一个订单既是销售订单又是促销订单）；
- 在任意时刻被追加新的断言（第二个月补上发票信息）；
- 被多个系统用同一个 IRI 引用，无需数据复制；
- 与另一系统的对象建立 `owl:sameAs` 关联。

一个数据库行可以映射成多个个体（订单行、明细、地址），也可能多个行合并成一个个体（一个客户在三个渠道的记录合并）。

## 个体 IRI 命名策略

| 策略 | 形式 | 优点 | 风险 |
|---|---|---|---|
| 业务 ID | `data:order-ORD20250301` | 可读、可对账、幂等 | ID 规则变更会导致 IRI 变化 |
| 代理键 | `data:order-8f3c9a2e` | 稳定、不暴露业务 | 可读性差，排查困难 |
| 内容哈希 | 地址、联系人 | 自然去重 | 内容一改就是新个体 |
| 空白节点 | `[ ... ]` | 方便表达中间结构 | 不能跨系统引用，不可追踪 |

实践建议：**业务实体用稳定业务 ID，纯内部结构用空白节点，绝不拿会变的字段（邮箱、手机号、地址）当 IRI**。发布后的个体 IRI 视为不可变，需要重命名时保留 `owl:sameAs` 或 `dcterms:replaces` 指向，避免引用断裂。

```turtle
@prefix data: <https://example.com/shop/data/> .
@prefix shop: <https://example.com/shop/ontology#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

data:order-ORD20250301 a shop:Order ;
  shop:orderNumber "ORD-20250301" ;
  shop:placedAt "2025-03-01T10:15:00+08:00"^^xsd:dateTime ;
  shop:placedBy data:customer-1001 ;
  shop:orderStatus shop:StatusPaid ;
  shop:hasLine data:line-1 , data:line-2 .

data:line-1 a shop:OrderLine ;
  shop:lineOfOrder data:order-ORD20250301 ;
  shop:lineProduct data:product-1 ;
  shop:quantity 2 ;
  shop:lineAmount "398.00"^^xsd:decimal .

data:customer-1001 a shop:Customer ;
  shop:customerId "C-1001" .
```

注意显式写出 `a shop:Order`。没有类型断言的个体仍然存在，但 `sh:targetClass` 找不到它，按类查询也查不到——“数据在，但看不见”是排查成本很高的故障。

## 什么时候必须个体化

这些对象不适合压成一个属性值，而应建成个体：订单、订单行、地址、事件、评估结果、审批记录，以及任何需要独立时间戳或来源的关系。判断标准是“是否需要被单独引用、追溯或关联”。

反过来，不要把每条业务流水都手写进本体文件。个体应由 ETL、API 同步或数据管道生成，本体只保留关键参考数据（状态、币种、单位、类目）作为受控取值。

## sameAs 的风险与实体链接

`owl:sameAs` 是等价公理：声明两个 IRI 指同一个体后，推理器会把两边的所有断言合并。它能解决“同一客户在 CRM 和 ERP 有两个 ID”的问题，但一旦链接错误，错误属性会污染整个图，而且很难定位。

```turtle
@prefix data: <https://example.com/shop/data/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .

data:customer-1001 owl:sameAs <https://crm.example.com/customer/C-1001> .
```

使用原则：

1. **只在人工或高置信度规则确认后使用**，批量匹配结果先进“候选表”复核。
2. **不确定的映射用 `skos:exactMatch`、`skos:closeMatch`**，它们只是映射标注，不会触发合并推理。
3. **避免链式 sameAs 成环**，必要时做去重与传递闭包检查。
4. 反向操作也要考虑：开放世界、无唯一名称假设意味着两个不同 IRI 完全可能指同一对象，不要默认“不同 IRI 就是不同对象”。

实体链接的工程流程与质量指标见[知识图谱构建](/application/knowledge-graph)。

## 实例数据从哪来

| 来源 | 常用方式 | 注意 |
|---|---|---|
| 关系库 | R2RML/RML 映射、SQL 导出后转 Turtle | 主键映射为稳定 IRI |
| 业务 API | 定时拉取后生成 JSON-LD | 分页与增量游标 |
| CSV/Excel | 模板映射脚本 | 编码、日期格式、空值 |
| JSON 文档 | JSON-LD `@context` 直接表达 | 上下文版本要固定 |

无论哪种方式，都要保证**幂等**：同一份源数据重复导入不产生重复个体；更新用 SPARQL Update 的 `DELETE`/`INSERT` 或整批替换，删除源数据时同步删除断言。否则图会越跑越脏。

另外，导入时保留来源信息（`dcterms:source` 或 `prov:wasDerivedFrom`），出现争议时能追到源记录；批量导入建议用命名图区分批次或来源，便于按批校验与回滚。

## 实例数据质量

导入后至少跑四类检查：重复个体（同一业务 ID 出现在多个 IRI）、缺失必填断言（SHACL `minCount`）、悬空引用（`shop:placedBy` 指向不存在的客户）、类型冲突（同时是互不相交的两个类）。前两类用 SHACL，后两类用一致性检查加查询。

质量规则与治理流程见[约束与业务规则](/modeling/elements/constraint)和[数据治理与主数据](/application/data-governance)。

## 常见坑

- 用空白节点表示订单、客户等业务实体，跨系统引用时无法对齐。
- 拿邮箱、手机号做 IRI，用户改一次号码，历史数据就断链。
- 个体没写 `rdf:type`，按类查询和 SHACL 校验双双失效。
- 滥用 `owl:sameAs` 做去重，错误合并后无法回滚。
- 只导入不删除，源系统已作废的数据长期留在图里。
- 用可变字段拼接 IRI，字段一改就断链；应使用稳定业务 ID 或代理键。
