# Python 技术栈

Python 在本体工作里的优势很明确：解析、校验、数据管道、LLM 生态都在同一门语言里，试错成本低，几百行代码就能把“解析 → 查询 → 校验 → 服务”的闭环跑起来。短板同样明确：内存图不适合大规模数据，并发服务需要额外设计，DL 推理依赖本机 Java。

结论是分工使用：数据工程、原型验证、离线校验、语义检索与 LLM 集成放在 Python；生产级大图、高并发查询、重推理放到 JVM 或专用三元组库。两边用文件、SPARQL 端点或消息交换，不要把整个知识图谱塞进一个 Python 进程。

## 库的分工

| 库 | 定位 | 关键 API | 注意 |
|---|---|---|---|
| rdflib | 纯 Python 的 RDF 模型、解析与 SPARQL | `Graph`、`Namespace`、`parse`、`query`、`update` | 内存图，规模上千万三元组后吃力 |
| pyoxigraph | Rust 内核的图存储与 SPARQL | `Store`、`load`、`query` | 性能好于纯内存图，生态较新 |
| pyshacl | SHACL 校验 | `validate()` | 支持先跑 RDFS/OWL RL 推理再校验 |
| owlready2 | OWL 2 本体操作与 DL 推理 | `get_ontology`、`sync_reasoner` | 推理依赖本机 Java |
| owlrl | RDFS 与 OWL RL 规则推理 | `DeductiveClosure` | 物化后数据会膨胀 |
| SPARQLWrapper | 调用远程 SPARQL 端点 | `SPARQLWrapper`、`query` | 注意超时、认证与查询注入 |

## 解析与 SPARQL

```python
from rdflib import Graph, Namespace, URIRef

COMMERCE = Namespace("https://example.com/onto/commerce#")

graph = Graph()
graph.parse("ontologies/commerce-core.ttl", format="turtle")
graph.parse("data/orders.ttl", format="turtle")

rows = graph.query(
    """
    PREFIX c: <https://example.com/onto/commerce#>
    SELECT ?order ?amount
    WHERE {
      ?order a c:Order ;
             c:amount ?amount ;
             c:customer ?customer .
      FILTER (?customer = ?customerIri)
    }
    """,
    initBindings={
        "customerIri": URIRef("https://example.com/data/customer/42")
    },
)

for row in rows:
    print(row.order, row.amount)
```

`initBindings` 是 rdflib 的参数化机制，等价于把变量预先绑定到 IRI 或字面量，比字符串拼接安全。查询结果按变量名取列，取到的仍是 RDF 术语对象，需要 `str()` 转成业务类型。

`Graph.parse` 按扩展名或 `format` 参数选择解析器；一次加载多个文件时注意基础 IRI，否则相对 IRI 会按错误基准解析。序列化用 `graph.serialize(format="turtle")`，输出前建议统一排序，便于版本对比。

## SHACL 校验

pyshacl 的调用很直接，返回三元组：是否合规、结果图、文本报告。

```python
from pyshacl import validate

conforms, report_graph, report_text = validate(
    graph,
    shacl_graph="shapes/order-shape.ttl",
    inference="rdfs",
    abort_on_first=False,
)

if not conforms:
    raise ValueError(report_text)
```

`inference` 参数决定校验前是否先做推理：`"rdfs"` 补子类与子属性，`"owlrl"` 跑 OWL RL 规则，`"both"` 两者都跑。很多“形状明明写了却不生效”的问题，根源是数据里只有子类实例、没有显式类型，而校验前没开推理。反过来，推理也会让本来因缺类型而不合规的数据变得合规，是否开启取决于业务语义，需要写进校验规范。

`report_graph` 是标准 RDF 结果图，可以入库、做看板或与 CI 集成；`report_text` 适合直接进日志。生产校验建议输出结构化的字段（严重级别、焦点节点、约束、路径），而不是只抛一个布尔值。

## pyoxigraph：比 rdflib 更快的内存与磁盘图

```python
from pyoxigraph import RdfFormat, Store

store = Store("target/oxigraph")
store.load(path="ontologies/commerce-core.ttl", format=RdfFormat.TURTLE)
store.load(path="data/orders.ttl", format=RdfFormat.TURTLE)

for solution in store.query(
    """
    PREFIX c: <https://example.com/onto/commerce#>
    SELECT ?order ?amount WHERE {
      ?order a c:Order ; c:amount ?amount .
    }
    LIMIT 50
    """
):
    print(solution["order"], solution["amount"])
```

pyoxigraph 的核心由 Rust 实现，适合需要比 rdflib 更快加载与查询、又不想引入 JVM 的场景。它的 API 面积比 rdflib 小，缺少部分 Python 侧的便利工具，团队要接受这一点。

## 推理：owlready2 与它的前提

```python
from owlready2 import get_ontology, sync_reasoner

onto = get_ontology("file://ontologies/commerce-core.owl").load()
sync_reasoner()  # 推理器由 Java 实现，需要本机安装 JRE
```

owlready2 提供接近面向对象的本体操作方式，用 Python 类映射 OWL 类，适合做本体探索与规则验证。它的推理走 Java 的 HermiT 或 Pellet，部署时必须装 JRE，容器镜像要相应变大；没有 Java 时推理会直接失败。

如果只需要 RDFS 或 OWL RL 级别的规则推断，`owlrl` 加 `DeductiveClosure` 可以纯 Python 完成，代价是物化结果与原图混在一起，查询时需要区分“显式”与“推断”数据。OWL DL 的完整推理不在纯 Python 能力范围内，见[JVM 技术栈](/impl/tech/jvm)。

## 用 FastAPI 暴露查询服务

```python
from fastapi import FastAPI, Query
from rdflib import Graph, URIRef

app = FastAPI()

graph = Graph()
graph.parse("data/commerce.ttl", format="turtle")

QUERY = """
PREFIX c: <https://example.com/onto/commerce#>
SELECT ?order ?amount
WHERE {
  ?order a c:Order ;
         c:customer ?customer ;
         c:amount ?amount .
  FILTER (?customer = ?customerIri)
}
"""


@app.get("/customers/{customer_id}/orders")
def list_orders(customer_id: str, limit: int = Query(default=20, le=100)):
    rows = graph.query(
        QUERY,
        initBindings={
            "customerIri": URIRef(
                f"https://example.com/data/customer/{customer_id}"
            )
        },
    )
    return [
        {"order": str(row.order), "amount": str(row.amount)}
        for row in rows
    ][:limit]
```

这是一个最小可用的语义查询接口，生产化还要补四件事：图加载放到启动阶段并做只读保护；多进程部署时每个 worker 各自持有图，更新要用版本化文件或外部存储；查询加超时与结果上限；把 SPARQL 文本放在资源文件里而不是函数内。

FastAPI 的同步端点会在线程池里执行，rdflib 的并发读取通常没问题，但并发写同一个 `Graph` 不安全。需要更新时就换成“重新加载并原子替换”，或把写入交给独立服务。

## pandas 与批量导入

```python
import pandas as pd
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

df = pd.read_csv("data/orders.csv")
graph = Graph()
COMMERCE = Namespace("https://example.com/onto/commerce#")

for row in df.itertuples(index=False):
    order = URIRef(f"https://example.com/data/order/{row.order_id}")
    graph.add((order, RDF.type, COMMERCE.Order))
    graph.add((order, COMMERCE.amount,
               Literal(str(row.amount), datatype=XSD.decimal)))
```

两个坑。第一，数值不要直接用浮点数构造字面量，`str(row.amount)` 或 `Decimal` 才能保住十进制语义。第二，业务 ID 进 IRI 前要校验字符集，含空格或斜杠的 ID 必须编码，否则生成非法 IRI。

大批量导入时逐条 `graph.add` 很慢，可以先构建 N-Triples 流再一次性 `parse`，或用 pyoxigraph 的批量加载接口。

## 与 LLM 集成：本体站在哪一层

本体在检索增强里通常承担三件事：术语与实体的规范化、结构化召回、答案约束。一个务实的做法是把“检索单元”从纯文本块换成“本体术语加子图”：

```python
def retrieve_context(question: str, graph, top_k: int = 5):
    terms = search_ontology_terms(question, top_k)  # 向量或关键词检索
    triples = []
    for term in terms:
        iri = term["iri"]
        for predicate, obj in graph.predicate_objects(iri):
            triples.append((iri, predicate, obj))
    return terms, triples
```

其中 `search_ontology_terms` 可以用你现有的向量库实现，索引对象是类名、标签、注释、同义词（`skos:altLabel`），而不是整篇文档。取回结果后，把“命中术语 + 邻居三元组”作为上下文交给模型，并要求答案引用 IRI，便于审计。

接 LangChain 或 LlamaIndex 时，实现一个自定义 retriever 即可：把上面的函数包装成 `BaseRetriever` 的 `_get_relevant_documents`，返回 `Document(page_content=..., metadata={"iri": ...})`。框架 API 会变，关键是不要把“检索到一段文本”当成目标，而是“检索到本体里的概念与关系”。

LLM 生成 SPARQL 要极其谨慎：只允许在只读端点、限定前缀、限制超时与结果条数，生成的查询先做语法校验，再由模板兜底。不要让模型直接生成 `INSERT` 或 `DELETE`。架构讨论见[语义检索与 RAG](/application/semantic-search)。

## 实践注意

- **规模**：rdflib 适合几十万到百万级三元组；再往上优先 pyoxigraph 或远程端点，别硬扛。
- **并发**：`Graph` 的写操作非线程安全；多 worker 部署要明确“每个进程一份只读图 + 版本化更新”的模型。
- **Java 依赖**：owlready2 推理需要 JRE，CI 与容器镜像要显式安装，否则只在生产环境暴露问题。
- **推理边界**：owlrl 与 pyshacl 的预推理都是规则子集，不等价于 OWL DL；不要用它们判断本体的一致性。
- **SPARQL 支持度**：rdflib 的查询引擎覆盖常用语法，但与三元组库相比在联邦查询、部分函数和扩展上有限，复杂查询放端点执行。
- **安全**：所有对外查询走参数绑定，端点加认证与限流；LLM 生成的查询必须经过白名单与语法检查。

## 选型建议

做数据管道、离线校验、RAG 原型，用 rdflib 加 pyshacl 起步，需要更快时换 pyoxigraph；本体需要 DL 推理时，接受 Java 依赖或用远程 JVM 服务。对外服务不要直接用 FastAPI 包一个内存图面向公网，至少加一层缓存与查询约束。服务端选型的整体比较见[技术栈与平台总览](/impl/tech/)。
