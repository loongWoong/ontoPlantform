# SHACL 形状约束

SHACL（Shapes Constraint Language）是 W3C 于 2017 年发布的推荐标准，用来校验 RDF 数据是否满足一组“形状”（shape）。它是 OWL 的天然搭档：OWL 负责表达语义和推理，SHACL 负责回答“这份数据完整吗、格式对吗、值合法吗”。

记住它的世界假设是**封闭世界（CWA）**：没有出现的属性就是缺失，可以直接判为不符合。这正是业务校验需要的行为。

## SHACL 解决什么问题

OWL 的开放世界假设决定了它无法表达“必填”。`owl:someValuesFrom` 在属性缺失时不会报错，只会认为“可能存在但未知”。而业务上“订单必须有客户、金额必须大于零、状态必须在枚举内”都是硬约束。

SHACL 的做法是：给数据图（data graph）配一个形状图（shapes graph），执行校验后返回一份校验报告（validation report），报告以 `sh:conforms true/false` 表示是否全部通过，并列出每个失败的焦点节点、路径、来源形状与消息。

## 形状与目标

形状分两类：

- **节点形状（NodeShape）**：直接约束一个节点；
- **属性形状（PropertyShape）**：约束某个路径上的值，用 `sh:path` 指定。

形状通过 target 决定“校验谁”：

| 目标 | 含义 |
|---|---|
| `sh:targetClass ex:Order` | 所有 `rdf:type ex:Order` 的节点 |
| `sh:targetNode <https://example.com/order/1001>` | 指定节点 |
| `sh:targetSubjectsOf ex:placedBy` | 所有以该属性为主语的节点 |
| `sh:targetObjectsOf ex:placedBy` | 所有以该属性为宾语的节点 |

也可以把形状直接声明为类（`ex:Order a sh:NodeShape, owl:Class`），此时该类的实例自动成为目标，这叫隐式类目标。

## 常用约束

| 约束 | 作用 |
|---|---|
| `sh:datatype` | 字面量必须是某 XSD 类型 |
| `sh:class` | 值必须是某类的实例（取决于是否启用推理） |
| `sh:nodeKind` | `sh:IRI` / `sh:Literal` / `sh:BlankNode` 等 |
| `sh:minCount` / `sh:maxCount` | 值的数量 |
| `sh:minInclusive` / `sh:maxInclusive` | 数值/日期范围 |
| `sh:minLength` / `sh:maxLength` | 字符串长度 |
| `sh:pattern` | 正则匹配（可配 `sh:flags`） |
| `sh:in` | 值必须在给定列表中 |
| `sh:hasValue` | 必须包含指定值 |
| `sh:languageIn` | 语言标签必须在允许集合内 |
| `sh:uniqueLang` | 同一属性下语言标签不得重复 |
| `sh:equals` / `sh:disjoint` / `sh:lessThan` | 两个路径的值相等/不相交/有序 |
| `sh:closed` + `sh:ignoredProperties` | 只允许显式声明的属性 |
| `sh:node` | 值必须满足另一个形状 |

## 示例：订单必填与金额范围

```turtle
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix ex:  <https://example.com/ontology/order#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:OrderShape a sh:NodeShape ;
    sh:targetClass ex:Order ;
    sh:closed true ;
    sh:ignoredProperties ( <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ) ;
    sh:property [
        sh:path     ex:placedBy ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:class    ex:Customer ;
        sh:message  "订单必须且只能有一个下单客户。"@zh
    ] ;
    sh:property [
        sh:path          ex:orderAmount ;
        sh:minCount      1 ;
        sh:datatype      xsd:decimal ;
        sh:minInclusive  0 ;
        sh:message       "订单金额必填且不能为负数。"@zh
    ] ;
    sh:property [
        sh:path     ex:status ;
        sh:minCount 1 ;
        sh:in       ( ex:Pending ex:Paid ex:Shipped ex:Closed ) ;
        sh:message  "订单状态必须是预定义值之一。"@zh
    ] .
```

几个细节：`sh:closed true` 后，除了 `sh:path` 声明的属性和 `sh:ignoredProperties` 里的属性，其他属性一律判失败；`sh:in` 的值是有序列表；`sh:datatype xsd:decimal` 对 `"1280"^^xsd:integer` 也会失败，因为类型不精确匹配。

## 严重级别与消息

每个约束可设 `sh:severity`：`sh:Violation`（默认，失败即不通过）、`sh:Warning`、`sh:Info`。`sh:message` 支持多语言标签，用 `{?var}` 可以引用焦点节点或值。生产系统应把消息写成业务方能理解的话，而不是“Constraint violation”。

对同一批数据，可以组合多份形状图：数据质量形状、发布门禁形状、对外 API 形状各管一段，按场景加载。

## SPARQL 约束

当内置约束表达不了跨字段规则时，用 `sh:sparql` 写 SPARQL 查询。约定是：查询中用 `$this` 指代当前焦点节点，返回的每一行代表一个违规。

```turtle
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <https://example.com/ontology/order#> .

ex:ShipDateShape a sh:NodeShape ;
    sh:targetClass ex:Order ;
    sh:sparql [
        sh:message "发货时间不能早于下单时间。"@zh ;
        sh:select """
            SELECT $this WHERE {
                $this <https://example.com/ontology/order#orderDate> ?d ;
                      <https://example.com/ontology/order#shippedAt> ?s .
                FILTER (?s < ?d)
            }
        """
    ] .
```

这条规则用内置约束写不出来，因为它比较同一节点上两个属性的值。SPARQL 约束的代价是可移植性与性能：不同引擎的优化能力不同，复杂查询应加上目标类缩小范围。

## 与 OWL 推理如何配合

SHACL 校验的是“图里实际有的三元组”。如果先推理再校验，形状能看到推断出的类型和关系；如果直接校验原始数据，`sh:class` 可能因为子类未被推断而不通过。两种顺序都合理，但必须在流水线里固定，否则同一份数据在不同环境下结果不一致。

注意不要在推理后校验“缺失”类规则：推理不会凭空生成 `placedBy`，`minCount` 仍会失败，这是正确行为。反过来，也不要指望 SHACL 推断出新事实——它的输出是校验报告，不是新三元组。

## 运行校验

```bash
# 使用 pyshacl 校验
pyshacl -s shapes.ttl -d data.ttl -f human

# 使用 Apache Jena 的 shacl 命令
shacl validate --shapes shapes.ttl --data data.ttl
```

`pyshacl` 支持 OWL 推理前置（按需开启）与多种报告格式；Jena 的 `shacl` 命令适合在 JVM 构建流水线中做门禁。

## 常见坑

- **忘记 target**：形状写了但没有任何节点被选中，校验永远通过。
- **`sh:class` 不考虑子类**：未开启推理时，`ex:ExpressOrder` 的实例不会被认为满足 `sh:class ex:Order`。
- **`closed` 漏掉 `rdf:type`**：几乎所有数据都会因此失败，记得放进 `ignoredProperties`。
- **把 SHACL 当推理解释器**：它不生成数据、不判断语义蕴含，只判断形状满足性。
- **`uniqueLang` 数据没语言标签**：没有标签的字面量不算语言重复，但也不满足 `languageIn`。
- **只在最后校验**：入库、发布、API 三个环节都应校验，越早发现成本越低。

SHACL 与 OWL 的分工可以概括为：OWL 回答“这个模型意味着什么”，SHACL 回答“这份数据够不够好”。两者都做，且各司其职，是语义层稳定运行的前提。约束如何与业务规则、聚合不变量配合，见 [约束与业务规则](/modeling/elements/constraint) 与 [业务规则与校验](/application/rules-validation)。
