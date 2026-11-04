# SPARQL 查询与更新

SPARQL 1.1 是 W3C 于 2013 年发布的 RDF 查询语言标准，包含查询（Query）与更新（Update）两部分。它相当于 RDF 世界的 SQL：数据存成三元组后，靠 SPARQL 取数、统计、验收和对外提供接口。

与 SQL 最大的不同在于，SPARQL 匹配的是图模式而不是表。你写的是“子图长什么样”，引擎去找所有匹配该子图的绑定组合。

## 基本图模式

```sparql
PREFIX ex: <https://example.com/ontology/order#>

SELECT ?order ?customerName
WHERE {
  ?order a ex:Order ;
         ex:placedBy ?customer ;
         ex:orderAmount ?amount .
  ?customer ex:name ?customerName .
}
```

`WHERE` 里的三元组模式用变量连接：`?order` 是订单节点，`a` 是 `rdf:type` 的缩写。这条查询等价于 SQL 的两表连接，但连接条件由共享变量隐式表达。

## 常用能力

| 构造 | 作用 |
|---|---|
| `FILTER` | 过滤表达式，如数值比较、正则、`BOUND`、`EXISTS` |
| `OPTIONAL` | 左连接；匹配失败时变量为空 |
| `UNION` | 并集 |
| `MINUS` | 差集 |
| `BIND (... AS ?x)` | 在查询中计算新变量 |
| `VALUES ?x { ... }` | 内联数据，常用于参数化 |
| `GROUP BY` / `HAVING` / 聚合 | `COUNT`、`SUM`、`AVG`、`MIN`、`MAX`、`GROUP_CONCAT` |
| 子查询 | 嵌套 `SELECT`，先聚合再连接 |
| 属性路径 | `ex:partOf+`、`^ex:placedBy`、`ex:a/ex:b`，以及用竖线表示的多条路径并集 |
| `GRAPH` / `FROM NAMED` | 访问命名图，见 [RDF 数据集](/spec/rdf) |
| `SERVICE <endpoint>` | 联邦查询，跨端点取数 |
| `ORDER BY` / `LIMIT` / `OFFSET` | 排序与分页 |

属性路径是一个高频能力。`ex:partOf+` 表示沿 `partOf` 传递闭包向上找，适合组织架构、BOM 展开、类目祖先这类层次查询；`^ex:placedBy` 表示反向沿边查找。注意无界路径（`*`、`+`）在大图上代价很高，务必配合 `LIMIT` 与起点约束。

## 从能力问题到验收查询

[能力问题（CQ）](/modeling/method/competency-questions)的落地方式之一，就是把每个 CQ 写成一条 SPARQL 查询。例如 CQ：“列出 2024 年以来下单、尚未发货且总额超过 1000 元的订单，按金额倒序”。

```sparql
PREFIX ex:  <https://example.com/ontology/order#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?order ?customerName (SUM(?amount) AS ?total)
WHERE {
  ?order a ex:Order ;
         ex:placedBy   ?customer ;
         ex:orderAmount ?amount ;
         ex:orderDate   ?date .
  ?customer ex:name ?customerName .
  OPTIONAL { ?order ex:shippedAt ?shipped }
  FILTER (?date >= "2024-01-01"^^xsd:date && !BOUND(?shipped))
}
GROUP BY ?order ?customerName
HAVING (SUM(?amount) > 1000)
ORDER BY DESC(?total)
LIMIT 20
```

这条查询同时用到了图模式、`OPTIONAL`、`FILTER`、聚合、`HAVING` 与排序。它能跑通，就说明模型里有对应的类和属性，且数据填得进去——这比“模型看起来不错”可靠得多。

## 其他查询形式

- **ASK**：返回布尔值，适合存在性检查；
- **CONSTRUCT**：返回新三元组，适合生成派生图或做格式转换；
- **DESCRIBE**：返回某资源的描述，具体内容由实现决定，不宜依赖其精确语义。

```sparql
PREFIX ex: <https://example.com/ontology/order#>

CONSTRUCT {
  ?customer ex:hasHighValueOrder ?order .
}
WHERE {
  ?order a ex:Order ;
         ex:placedBy    ?customer ;
         ex:orderAmount ?amount .
  FILTER (?amount > 10000)
}
```

## SPARQL Update

Update 用于写入、修改与删除，语法上独立于 Query：

```sparql
PREFIX ex:  <https://example.com/ontology/order#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

DELETE { ?order ex:status ex:Pending . }
INSERT { ?order ex:status ex:Closed . }
WHERE  {
  ?order a ex:Order ;
         ex:status    ex:Pending ;
         ex:orderDate ?date .
  FILTER (?date < "2024-01-01"^^xsd:date)
}
```

常用操作包括 `INSERT DATA`、`DELETE DATA`、`DELETE WHERE`、`DELETE/INSERT ... WHERE`、`LOAD <file>`、`CLEAR GRAPH`。批量导入优先用 `LOAD` 或批量 `INSERT DATA`，逐条更新在远程端点上很慢。事务语义由具体存储决定，跨多个图的更新不保证原子，写操作前要评估回滚策略。

## 与 SQL 的对照

| 维度 | SPARQL | SQL |
|---|---|---|
| 数据模型 | 图（三元组） | 表（行与列） |
| 模式 | 可选，运行时匹配 | 强 schema，建表时定义 |
| 连接 | 共享变量 | `JOIN ... ON` |
| 可空/缺失 | 天然允许，`OPTIONAL` 表达 | `NULL` |
| 路径查询 | 属性路径原生支持 | 需要递归 CTE |
| 演进 | 加属性不影响已有数据 | 通常要改表结构 |
| 生态 | 三元组库、推理器 | 关系数据库占绝对主流 |

两者不是替代关系。常见架构是业务数据在关系库，语义层用 RDF/SPARQL 做集成与查询，必要时通过联邦或物化同步。

## 命名图与推理边界

RDF 数据可以组织成数据集：默认图加若干命名图。SPARQL 用 `FROM` 指定默认图、`FROM NAMED` 声明可被访问的命名图，再用 `GRAPH` 在查询里切换；不写 `FROM` 时，查询作用于端点配置的默认数据集。按来源分图（ERP、CRM、人工补录）既保留出处，也能在一个查询里合并。

```sparql
PREFIX ex: <https://example.com/ontology/order#>

SELECT ?order ?status
FROM <https://example.com/graph/orders>
FROM NAMED <https://example.com/graph/erp>
WHERE {
  ?order a ex:Order .
  GRAPH <https://example.com/graph/erp> { ?order ex:erpStatus ?status }
}
```

推理不属于 SPARQL 规范：一条查询能否看到推断出的三元组，取决于端点是否启用了 RDFS/OWL 推理、是物化还是查询时推理。同一端点改配置，结果就可能变化。接口契约要写明推理级别；需要稳定结果时，宁可在查询里显式写出所需的连接，也不要把语义押在端点的隐藏推理上。

## 性能与安全注意

- **无界属性路径**：`ex:partOf*` 在百万级图上有爆炸风险，尽量限定起点类型与深度。
- **`SELECT *`**：返回所有变量，网络与序列化开销大，生产查询应显式列出变量。
- **无法索引的过滤**：`FILTER(regex(...))`、函数计算通常无法走索引，先缩小图模式再过滤。
- **联邦查询**：`SERVICE` 依赖远端可用性与延迟，且会把数据暴露给对端，需评估信任边界。
- **开放端点风险**：可写的 SPARQL 端点是高危面。生产环境应默认只读、加认证与限流、设查询超时与结果上限；公开端点尤其要防止 `LOAD` 与大批量 `DELETE`。
- **注入风险**：不要把用户输入直接拼接进查询字符串。用 `VALUES` 绑定参数，或在应用层做 IRI 校验与转义。
- **推理与查询分离**：查询是否看到推断出的三元组，取决于端点是否开启推理。接口契约里要写清楚。

## 小结

SPARQL 是本体从“模型文件”变成“可用数据服务”的关键一环。写模型时就该同步写查询：查询写不出来，往往说明模型缺少必要的属性或方向；查询性能差，往往说明图结构或索引设计有问题。把 CQ 查询纳入自动化测试，是最划算的本体质量保障手段之一。
