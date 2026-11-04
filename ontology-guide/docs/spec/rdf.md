# RDF 数据模型与序列化

RDF（Resource Description Framework）是整套语义网标准的地基。它只有一句话：任何知识都可以拆成“主语—谓语—宾语”的三元组。理解这句话背后的 IRI、字面量、空白节点和命名图，就理解了为什么本体可以跨系统合并、推理和查询。

## 三元组：RDF 的全部

一个三元组（triple）描述一个事实：

```text
<https://example.com/order/1001>  <https://example.com/ontology/order#placedBy>  <https://example.com/customer/C-42> .
```

- **主语**必须是 IRI 或空白节点，不能是字面量；
- **谓语**必须是 IRI；
- **宾语**可以是 IRI、空白节点或字面量。

RDF 没有“表”“行”“字段”的概念，只有一堆三元组。把它们放在一起就是一个图（graph）：主语和宾语是节点，谓语是边。这也是为什么 RDF 数据天然可以合并——两个图取并集即可，不需要对齐 schema。

## IRI、字面量与空白节点

**IRI**（Internationalized Resource Identifier）是全局标识。RDF 用完整 IRI 保证不同来源的数据不会撞名。日常写 Turtle 用前缀缩写，但展开后必须是绝对 IRI。注意 IRI 区分大小写，`ex:Order` 和 `ex:order` 是两个东西。

**字面量**（literal）是值，分三种：

```turtle
@prefix ex:    <https://example.com/ontology/order#> .
@prefix order: <https://example.com/order/> .
@prefix xsd:   <http://www.w3.org/2001/XMLSchema#> .

order:1001 ex:orderNo   "SO-1001" .
order:1001 ex:note      "加急"@zh .
order:1001 ex:itemCount "3"^^xsd:integer .
```

在 RDF 1.1 中，`"abc"` 等价于 `"abc"^^xsd:string`。数字、日期、布尔值都应该显式标注 XSD 类型，否则查询里的数值比较会得到意外结果。

**空白节点**（blank node，写作 `_:b1`）表示“存在某个东西，但我不知道也不关心它的 IRI”。它适合表示没有独立身份的结构性节点，比如一个内嵌地址。但空白节点不能被外部引用，标签只在当前文档内有效，跨文档合并时会重新分配。需要稳定标识的实体，一律用 IRI。

## 用 Turtle 写数据

Turtle 是手写 RDF 最常用的语法，可读性最好：

```turtle
@prefix ex:   <https://example.com/ontology/order#> .
@prefix order: <https://example.com/order/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

order:1001
    a               ex:Order ;
    rdfs:label      "订单 1001"@zh ;
    ex:orderNo      "SO-1001" ;
    ex:orderAmount  "1280.00"^^xsd:decimal ;
    ex:placedBy     order:C-42 ;
    ex:contains     [ a ex:OrderLine ; ex:product order:P-7 ; ex:quantity 2 ] .
```

要点：`a` 是 `rdf:type` 的缩写；`;` 续写同一主语，`,` 续写同一谓语的多个宾语；`[...]` 是空白节点属性列表。这些缩写只是语法糖，展开后仍是普通三元组。

## 其他序列化

同一份数据可以写成多种格式，语义完全等价。

**N-Triples**：一行一个三元组，无前缀，适合流式处理和 diff：

```text
<https://example.com/order/1001> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <https://example.com/ontology/order#Order> .
<https://example.com/order/1001> <https://example.com/ontology/order#orderAmount> "1280.00"^^<http://www.w3.org/2001/XMLSchema#decimal> .
```

**JSON-LD**：用 JSON 表达 RDF，通过 `@context` 建立映射，适合 Web API：

```json
{
  "@context": {
    "ex": "https://example.com/ontology/order#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "orderNo": "ex:orderNo",
    "orderAmount": { "@id": "ex:orderAmount", "@type": "xsd:decimal" }
  },
  "@id": "https://example.com/order/1001",
  "@type": "ex:Order",
  "orderNo": "SO-1001",
  "orderAmount": "1280.00"
}
```

选择依据：人工编辑与代码审查用 Turtle；大数据交换与流处理用 N-Triples/N-Quads；前后端 API 用 JSON-LD；带命名图的场景用 TriG 或 N-Quads。

## RDFS：最轻的模式词汇

RDFS 提供了一小组建模词汇，它们不是“注释”，而是带推理语义的公理：

| 词汇 | 含义 | 推理后果 |
|---|---|---|
| `rdfs:subClassOf` | 子类关系 | 传递；子类实例可被推断为父类实例 |
| `rdfs:subPropertyOf` | 子属性关系 | 子属性断言可推断出父属性断言 |
| `rdfs:domain` | 定义域 | 若 `x p y` 且 `p` 的 domain 是 `C`，则推出 `x a C` |
| `rdfs:range` | 值域 | 若 `x p y` 且 `p` 的 range 是 `C`，则推出 `y a C` |
| `rdfs:label` / `rdfs:comment` | 人类可读标签与说明 | 无逻辑后果，供展示和检索 |

```turtle
@prefix ex:   <https://example.com/ontology/order#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:PaidOrder rdfs:subClassOf ex:Order .
ex:Order     rdfs:subClassOf ex:BusinessDocument .

# 由传递性可推出：ex:PaidOrder rdfs:subClassOf ex:BusinessDocument
```

最常见的坑是把 `domain`/`range` 当校验规则。它们**只产生类型推断，不会报错**：如果一条订单没有 `placedBy`，推理器不会说“订单缺少客户”；如果 `placedBy` 的对象是商品，推理器会把它推断成客户，而不是判定数据非法。校验要用 [SHACL](/spec/shacl)。

## RDF 数据集与命名图

单个图是最常见的单位，但 RDF 数据集（dataset）可以包含一个默认图和若干命名图（named graph）。命名图让同一份数据集里可以按来源、版本或用途隔离三元组，SPARQL 里用 `GRAPH` 访问，见 [SPARQL 页](/spec/sparql)。典型用法是按数据来源分图（如 `<https://example.com/graph/sap>`、`<https://example.com/graph/crm>`），既保留来源，又能在查询时合并。

## RDF 与属性图怎么选

| 维度 | RDF | 属性图（LPG） |
|---|---|---|
| 标识 | 全局 IRI，天然可合并 | 通常为内部 ID，跨库需对齐 |
| 边属性 | 不支持，需要具体化为节点 | 原生支持 |
| 模式 | RDFS/OWL/SHACL 标准化 | 多为应用层约定，标准较弱 |
| 推理 | 有成熟标准与推理器 | 一般依赖应用逻辑或规则引擎 |
| 查询 | SPARQL（标准） | Cypher/Gremlin 等（各产品不同） |
| 工程体验 | 学习曲线较陡，工具链成熟 | 上手快，生态偏应用 |
| 适合 | 跨系统语义互操作、知识图谱、数据治理 | 路径查询、推荐、图算法密集场景 |

判断标准不是“哪个技术更先进”，而是“我的问题里，语义共享与标准化占多大比重”。只是做社交关系查询，属性图更省事；要做跨部门主数据统一并保留可推理的语义层，RDF 更合适。也可以组合：RDF 做语义层，属性图或搜索引擎做服务层。

## 入门命令

用 Apache Jena 的 `riot` 可以完成语法校验与格式转换：

```bash
# 校验 Turtle 语法
riot --validate data.ttl

# 把 N-Triples 转为 Turtle
riot --output=TURTLE data.nt
```

用 `rapper`（raptor 工具集）也可以互转：

```bash
rapper -i turtle -o ntriples data.ttl > data.nt
```

## 常见坑

- **把字符串当实体**：`ex:customer "C-42"` 与 `ex:customer <https://example.com/order/C-42>` 语义完全不同，前者无法参与实体链接。
- **类型标注不一致**：一处写 `"3"`，一处写 `"3"^^xsd:integer`，聚合和比较会出问题。约定好每个数据属性用哪种 XSD 类型。
- **语言标签缺失**：多语言站点要统一用 `@zh`、`@en`，不要一半带标签一半不带。
- **前缀冲突**：合并多个文件时同一前缀指向不同命名空间，展开后才知道撞了。评审时检查前缀表。
- **空白节点充当实体**：需要被查询、被引用、被更新的对象必须有稳定 IRI。
