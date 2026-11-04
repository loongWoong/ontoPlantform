# 标准与语言总览

本体不是一张画在 PPT 里的概念图。它要被工具读取、被推理器检查、被程序查询、被校验器验证，就必须落到一组公共的语法与语义标准上。W3C 的语义网标准栈就是为此存在的：不同团队、不同产品只要遵守同一套标准，本体的交换和复用才不需要每次重写。

本页给出标准全景、它们之间的组合方式，以及按场景选型的思路。各标准的细节分别见 [RDF](/spec/rdf)、[OWL 2](/spec/owl)、[SKOS](/spec/skos)、[SHACL](/spec/shacl)、[SPARQL](/spec/sparql)。

## 为什么本体需要标准

没有标准时，团队通常用三种方式表达概念体系：数据库表与注释、私有 JSON Schema、图数据库里约定俗成的节点结构。它们都能在一个系统内工作，但一旦要跨系统交换，就会出现三个问题：

- **语法不统一**：A 团队用 `parent_id` 表示上下级，B 团队用 `belongsTo`，映射代码越写越多。
- **语义不共享**：字段名叫 `status`，但一方指订单状态，另一方指客户状态，机器无法判断。
- **工具不通用**：换一个图数据库或校验工具，模型和规则就得重写一遍。

标准解决的是这三个层面的问题：RDF 统一数据表示，RDFS/OWL 统一语义表达，SHACL 统一校验方式，SPARQL 统一查询语言。代价是学习曲线，收益是长期可交换、可推理、可治理。

## 标准全景

| 标准 | 版本年份 | 解决什么问题 | 在本指南中的位置 |
|---|---|---|---|
| RDF 1.1 | 2014 | 数据模型：三元组、IRI、字面量、命名图 | [RDF 数据模型与序列化](/spec/rdf) |
| RDFS | 随 RDF 1.1 更新 | 轻量模式：类层次、属性层次、domain/range | [RDF 数据模型与序列化](/spec/rdf) |
| OWL 2 | 2009（2012 第二版） | 形式化本体：类表达式、属性特征、公理、推理 | [OWL 2 本体语言](/spec/owl) |
| SKOS | 2009 | 受控词表、分类法、叙词表 | [SKOS 词表与分类法](/spec/skos) |
| SHACL | 2017 | 封闭世界的数据形状校验 | [SHACL 形状约束](/spec/shacl) |
| SPARQL 1.1 | 2013 | 查询与更新 RDF 数据 | [SPARQL 查询与更新](/spec/sparql) |
| JSON-LD 1.1 | 2020 | 用 JSON 表达 RDF，便于 Web 与前端使用 | [RDF 数据模型与序列化](/spec/rdf) |

此外还要知道两个正在演进的方向：RDF-star（把语句当作节点来引用）目前是社区规范，尚未成为 W3C 正式推荐标准；RDF 1.2 正在标准化过程中。生产系统不要把未定稿的特性当作基础依赖。

## 它们如何组合

标准不是一个替代另一个，而是一条流水线：

```text
概念体系 ──SKOS/OWL──▶ 模式（TBox）
                          │
业务数据 ──映射──▶ 实例（ABox）──RDF──▶ 存储
                          │
        SPARQL 查询 ◀──────┤──────▶ SHACL 校验
                          │
                  JSON-LD 交换与展示
```

一个典型的最小组合是：用 RDF/Turtle 写模式和样例数据，用 OWL 表达需要推理的公理，用 SHACL 表达必填、范围、格式等业务约束，用 SPARQL 实现查询与验收，用 JSON-LD 与前端或其他系统交换。SKOS 只在需要受控词表时加入，不必每页都用。

```turtle
@prefix ex:   <https://example.com/ontology/order#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

ex:Order a owl:Class ;
    rdfs:label "订单"@zh .

ex:placedBy a owl:ObjectProperty ;
    rdfs:domain ex:Order ;
    rdfs:range  ex:Customer .

ex:orderAmount a owl:DatatypeProperty ;
    rdfs:domain ex:Order ;
    rdfs:range  xsd:decimal .
```

这段 Turtle 已经用到了 RDF 的语法、RDFS 的词汇、OWL 的声明。注意 `rdfs:domain` 的含义是“推理出类型”，不是“校验报错”，这一点在 [OWL 页](/spec/owl)会重点解释。

## 怎么选：按问题选标准

不要问“哪个标准更先进”，要问“我现在要解决什么问题”。

| 你的主要诉求 | 优先使用 | 说明 |
|---|---|---|
| 术语统一、分类导航、同义词 | SKOS | 轻量、工具支持好，不引入推理负担 |
| 共享概念模型、需要推理或一致性检查 | OWL 2 | 从 EL/RL 等 profile 起步，避免全量 DL 公理 |
| 数据完整性、业务规则校验 | SHACL | 封闭世界，天然适合“必填、范围、格式” |
| 查询、报表、验收测试 | SPARQL 1.1 | 与 OWL 解耦，查询不依赖推理器也能跑 |
| Web API、前端消费 | JSON-LD 1.1 | 用 `@context` 把 JSON 映射为 RDF |
| 只是内部图结构、不需要语义互操作 | 属性图（LPG）也可 | 但要清楚它不解决语义共享问题 |

一个务实的判断顺序是：先确认是否真的需要跨系统语义互操作；需要则从 RDF + SPARQL + SHACL 起步，再按需要加 OWL；如果只是词表管理，SKOS 就够了。

## 与属性图的关系

行业里常把“图数据库”和“本体”混为一谈。RDF 与属性图（Labeled Property Graph，LPG）是两种图范式：RDF 以全局 IRI 标识资源、边不带属性、强调标准与推理；属性图允许边上带属性、工程上手快、生态偏应用。选型取决于你是否需要语义互操作、标准查询与推理，而不是取决于“图”这个字。详细对比见 [RDF 页](/spec/rdf)。

## 常见误区

- **标准越重越好**：一上来就全量 OWL DL + 复杂公理，推理慢、维护贵，业务方还看不懂。先轻后重。
- **用 OWL 做校验**：OWL 是开放世界，缺数据不等于错误；必填、范围、格式应交给 SHACL。
- **把 SKOS 当简化版 OWL**：SKOS 的 `broader` 不是 `subClassOf`，不能当类层次推理用。
- **只学语法不学语义**：会写 Turtle 不等于会建模；语法只是载体，取舍在语义层。
- **以为 JSON-LD 是另一套体系**：它只是 RDF 的一种序列化，`@context` 决定映射，不是新模型。

## 小结

标准栈的价值在于分工：RDF 管表示，OWL/SKOS 管语义，SHACL 管校验，SPARQL 管访问，JSON-LD 管交换。多数项目不需要一次用全，先从最小组合开始，随着问题复杂度增加再引入更重的标准，这个顺序比反过来容易成功得多。
