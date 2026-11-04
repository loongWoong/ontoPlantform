# 分层与六边形架构

结论先给：把语义能力当成系统里独立的一层，上层通过**端口**使用它，三元组库、推理器、外部数据源都放到**适配器**里。这样本体可以独立演进，业务代码也不必知道底下是 Jena 还是 GraphDB。这套思路与 DDD 的[端口适配器](https://ddd-fans.github.io/ddd-guideline/impl/arch-pattern/ports-and-adapters)同源，只是端口的内容从“聚合读写”换成了“语义查询与校验”。存储怎么选见[存储选型](/impl/arch-pattern/storage)，推理怎么开见[推理与物化](/impl/arch-pattern/inference)。

## 语义层在系统中的位置

```text
              ┌──────────────┐
   外部调用 ──▶│  接口层      │  REST/GraphQL/RPC
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │  应用层      │  用例编排、事务、权限
              └──────┬───────┘
        ┌────────────┴─────────────┐
        ▼                          ▼
 ┌──────────────┐          ┌──────────────┐
 │  领域层      │          │  语义层      │
 │ 聚合/行为    │◀─事件/接口─▶ 查询/校验/推理 │
 └──────┬───────┘          └──────┬───────┘
        ▼                          ▼
 ┌──────────────┐   ┌──────────┬──────────┬──────────┐
 │ 业务库适配器 │   │ 三元组库 │ 推理器   │ 外部源   │
 └──────────────┘   └──────────┴──────────┴──────────┘
```

横向上，语义层和领域层是**并列的两个能力中心**，不是上下级。领域层回答“业务该怎么变”，语义层回答“这些名词共同指什么、跨系统怎么对上、隐含结论是什么”。把语义层塞进领域层内部，会让领域模型被 RDF API 与 SPARQL 字符串污染。

## 各层职责与禁止事项

| 层 | 职责 | 不应该做 |
|---|---|---|
| 接口层 | 协议转换、鉴权、参数校验 | 拼 SPARQL、直接访问三元组库 |
| 应用层 | 用例编排、调用语义与领域能力、事务与补偿 | 把模型语义判断写成 if-else |
| 语义层 | 暴露按用例命名的查询/校验接口、屏蔽推理与存储细节 | 承载业务行为、直接返回裸 `Model` |
| 适配器 | 三元组库读写、推理器调用、外部源导入、缓存 | 包含业务规则 |

## 六边形：端口和适配器怎么切

端口是**以问题语言命名**的接口，而不是对底层 API 的包装。下面这个端口只描述“系统需要知道什么”，不暴露任何 RDF 概念：

```java
public interface OrderSemanticPort {
    List<OrderSummary> findOrdersByCustomer(CustomerId customerId);

    ValidationResult validateOrder(OrderId orderId);
}
```

适配器负责把它翻译成具体实现。同一个端口可以有两个适配器，测试时用内存实现，生产时用三元组库：

```java
public final class SparqlOrderAdapter implements OrderSemanticPort {
    private final QueryExecutor queries;   // 封装 SPARQL 执行与超时
    private final ShapeValidator validator;

    @Override
    public List<OrderSummary> findOrdersByCustomer(CustomerId id) {
        var bindings = queries.select("order/by-customer.rq", Map.of("customer", id.iri()));
        return bindings.stream().map(OrderSummary::from).toList();
    }

    @Override
    public ValidationResult validateOrder(OrderId orderId) {
        return validator.validate(orderId.iri(), "order-shapes.ttl");
    }
}
```

`QueryExecutor` 这类东西本身也是端口，它的适配器可以是 Fuseki HTTP 客户端、Jena 本地 `QueryExecution`、RDF4J `RepositoryConnection`。查询文本放在 `queries/*.rq` 资源文件里，而不是散落在 Java 字符串里——这一点对可测试性和优化都很关键。

## 依赖方向的四条规则

1. 上层依赖语义层的接口，语义层不依赖上层。
2. 语义层不 `import` 任何具体图库或 HTTP 客户端（Jena、RDF4J、rdflib 都只出现在适配器里）。
3. 适配器之间互不依赖，交换数据走端口或显式 DTO。
4. 语义层返回的值对象是**为消费方设计的**，不是 RDF 的 `Model`、`Resource`、`Statement`。

第 4 条常被忽略。直接把 `Model` 抛给上层，等于让调用方自己写 SPARQL，过半年就没人说得清系统里到底有多少条查询、哪些是热点、哪些已经失效。

## 语义服务 API 怎么设计

按用例封装，一条用例对应一个方法或一个命名查询，避免“通用 SPARQL 端点”直接暴露给内部调用方。

- **方法名用业务语言**：`findOverdueOrders`，而不是 `runQuery("q-017")`。
- **输入是业务标识**，不是 IRI 字符串拼接；IRI 生成集中在 IRI 常量类里，见[实现本体对象](/impl/objects/)。
- **输出是视图对象**，字段明确、可序列化，顺带完成从 IRI 到业务 ID 的映射。
- **校验类接口返回结构化结果**（是否通过、违规项、严重级别、涉及节点），而不是一句布尔值。
- **读接口可以缓存**，写接口一律走数据管道，不要让业务代码直接发 SPARQL Update 改知识库。

如果消费方只有 BI 或数据科学团队，也可以保留一个受控的 SPARQL 端点，但要限制超时、并发、结果行数和可用图，必要时做查询白名单。开放端点的风险见[SPARQL 查询与更新](/spec/sparql)。

## 与 DDD 四层架构的对照

| DDD 四层 | 本体侧对应 | 差异 |
|---|---|---|
| 用户接口层 | 不变 | 语义层通常只被内部调用 |
| 应用层 | 不变，负责编排两侧 | 一个用例可能既改聚合又查语义 |
| 领域层 | 领域层保持原样，另设语义层 | 语义层不是领域层的子集，两者并列 |
| 基础设施层 | 三元组库、推理器、导入任务作为适配器 | 语义层自身不含基础设施代码 |

六边形架构里的“核心”在 DDD 是领域模型，在本体语境里要拆成两个：**领域模型**（行为）和**本体**（语义）。二者都需要被保护，但保护方式不同——领域模型靠不依赖外部技术，本体靠版本化、可校验、可推理。

## 小型项目简化版

只有一个应用、数据量不大时，不必真的画出两个中心：

- 语义层做成一个包或一个模块，对外只有 3–5 个方法；
- 直接使用嵌入式三元组库（Jena TDB2、Oxigraph），省掉独立服务；
- SHACL 校验放在导入任务末尾，而不是每次查询都跑；
- 仍然坚持“查询文本外置 + 不返回裸 Model”这两条，它们是低成本高回报的纪律。

等到第二个消费方出现、或需要独立发布节奏时，再把模块提成服务，改动量并不大——前提是端口划分一开始就按用例做对了。

## 常见坑

- **语义层变成 SPARQL 转发器**。所有方法都只是 `select(sparql)`，等于没有抽象，只是多了一层文件。
- **把推理放进查询路径**。每条查询都触发推理，延迟不可控；应当把推理结果物化到独立命名图，见[推理与物化](/impl/arch-pattern/inference)。
- **事务边界混乱**。业务库与知识库各自开事务，缺补偿方案；应当明确知识库更新是最终一致。
- **适配器里写业务规则**。“这个客户算不算 VIP”出现在 SPARQL 里，等于法律条款写进了 SQL。
