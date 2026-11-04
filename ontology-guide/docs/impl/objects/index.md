# 实现本体对象

建模交付的是文件，实现交付的是代码。本体进入系统之后，就不再是 Protégé 里的一张图，而是一组需要在代码中被引用、加载、查询、校验和推理的资产。与 DDD 不同，本体没有“聚合、实体、值对象”式的代码范式可以直接照搬：我们不需要为每个 OWL 类生成一个业务类，也不应该把业务规则塞进 SPARQL。

本页的结论有三条。第一，TBox 与 ABox 必须在代码和部署上分开：本体文件当源码管理，实例数据当数据处理。第二，所有 IRI 必须集中管理，不允许字符串散落在业务代码里。第三，把查询和校验封装成用例级接口，SPARQL 与 Shape 作为受版本控制的资源文件，而不是内联字符串。

## 本体对象由哪几类代码承载

本体元素在代码里的形态，与它们在模型里的位置有关，而不是与面向对象的类层次一一对应。常见分工如下。

| 建模产物 | 代码或文件形态 | 归属 | 说明 |
|---|---|---|---|
| 类、属性、命名空间 | IRI 常量类、枚举、命名空间对象 | 语义层 | 全项目唯一的 IRI 事实来源 |
| TBox | `ontologies/**/*.ttl` | 语义层 | 随应用发布，走代码评审 |
| ABox | 三元组库、数据文件、导入任务 | 数据层 | 不进应用包，按数据治理流程走 |
| SHACL Shape | `shapes/**/*.ttl` | 语义层 | 校验规则的唯一来源 |
| 能力问题查询 | `queries/**/*.rq` 加参数化封装 | 语义层 | 查询是资产，不是临时字符串 |
| 推理配置 | profile、规则集、物化任务 | 基础设施 | 决定推理由谁在何时执行 |
| 映射代码 | Translator、Assembler、DAO | 应用层/适配器 | 负责 RDF 与领域对象互转 |

其中“类与属性”这一类最容易被写坏。很多项目在业务代码里到处出现 `"https://example.com/onto/commerce#Order"` 这样的字面量，改命名空间时只能全局搜索替换，拼写错误又只有运行期才暴露。

## 目录组织：把 TBox 当源码管

推荐把本体文件放在源码仓库里，与 Java、Python 代码一起评审、一起发布。一个可维护的组织方式如下：

```text
src/main/resources/
  ontologies/
    commerce/
      core.ttl          # 复用与对齐：schema.org、上层本体
      order.ttl         # 订单相关类与属性
      party.ttl         # 客户、组织
  shapes/
    order-shape.ttl
  queries/
    find-orders-by-customer.rq
```

这样做的直接收益是：本体的变更可以走代码评审与 CI（解析是否通过、是否一致、CQ 查询是否仍能返回结果），而不是靠人记得更新某台服务器上的文件。实例数据则相反，它由导入管道或图数据库持有，应用启动时只加载 TBox 和必要的参考数据。两者的生命周期不同：TBox 按季度演化，ABox 按小时或按天更新，混在一起发布会让每次数据变更都触发应用重新部署。

模块边界建议与限界上下文对齐：一个模块一个命名空间、一个目录、一组 `owl:imports`。边界划分与复用策略见[模块化与复用](/modeling/elements/module)。

## IRI 常量：把字符串关进类里

集中管理 IRI 是所有后续工作的前提。Java 里可以这样写：

```java
import org.apache.jena.rdf.model.Property;
import org.apache.jena.rdf.model.Resource;
import org.apache.jena.rdf.model.ResourceFactory;

public final class CommerceVocab {
    public static final String NS = "https://example.com/onto/commerce#";

    public static final Resource ORDER = ResourceFactory.createResource(NS + "Order");
    public static final Property CUSTOMER = ResourceFactory.createProperty(NS + "customer");
    public static final Property AMOUNT = ResourceFactory.createProperty(NS + "amount");

    private CommerceVocab() {
    }
}
```

`Resource` 与 `Property` 是 Jena 的全局对象，不绑定具体模型，可以安全地作为常量复用。Python 的写法更轻：

```python
from rdflib import Namespace

COMMERCE = Namespace("https://example.com/onto/commerce#")
ORDER = COMMERCE.Order
AMOUNT = COMMERCE.amount
```

`COMMERCE.Order` 返回的是 `URIRef`，拼接、比较、作为查询参数都合法，比手写字符串安全得多。若由本体文件生成本地代码（codegen），请把生成物标记为“勿手改”，并在 CI 中校验生成物与本体文件一致；否则生成代码会慢慢偏离模型，成为第二套事实标准。

## 本体类不等于业务实体类

这是实现阶段最容易走偏的地方。OWL 类表达的是共享概念、分类与推理约束；业务实体类表达的是行为、生命周期和聚合不变量。DDD 侧可对照[领域对象实现](https://ddd-fans.github.io/ddd-guideline/impl/domain-objects/)，两者的关注点不同，可以对应，但不必一一对应。

常见的两种极端都要避免。其一是“为本体每个类生成一个实体类”：生成代码没有行为，字段跟着模型变，业务逻辑无处安放。其二是“把本体当唯一模型”：用 OWL 表达订单状态机和聚合不变量，结果既难写又难测，还会被开放世界假设坑到。

推荐的做法是让领域模型与本体并行存在，中间加一层显式映射。本体承担“对外语义视图”和“跨系统契约”的角色，领域模型承担“系统内行为模型”的角色。映射关系大致如下。

| 本体元素 | 代码对应物 | 注意 |
|---|---|---|
| 类 | 值对象类型、枚举、实体类型标记 | 一个类可能对应多个实体类型 |
| 个体 | 聚合根或实体实例 | 身份由业务 ID 生成 IRI 承载 |
| 对象属性 | 关联与引用 | 不要退化成外键字段 |
| 数据属性 | 值对象字段 | 取值校验交给 SHACL |
| 公理 | 不变量的一部分 | 一致性交推理器，完整性交 SHACL |
| Shape | 服务层的校验器 | 输出结构化错误报告 |
| 模块 | 包或限界上下文 | 与命名空间一一对应 |

一个最小映射器长这样：

```java
import org.apache.jena.rdf.model.Model;
import org.apache.jena.rdf.model.Resource;
import org.apache.jena.rdf.model.ResourceFactory;
import org.apache.jena.vocabulary.RDF;

public final class OrderTranslator {

    public static Resource toRdf(Order order, Model model) {
        Resource r = model.createResource(
                "https://example.com/data/order/" + order.id());
        r.addProperty(RDF.type, CommerceVocab.ORDER);
        r.addProperty(CommerceVocab.CUSTOMER,
                ResourceFactory.createResource(
                        "https://example.com/data/customer/" + order.customerId()));
        r.addProperty(CommerceVocab.AMOUNT,
                ResourceFactory.createTypedLiteral(order.amount()));
        return r;
    }
}
```

注意两点。第一，业务 ID 拼进 IRI 前要做转义或编码，否则含空格、斜杠、中文的 ID 会生成非法 IRI。第二，`order.amount()` 应当是 `BigDecimal` 而不是 `double`，否则金额的十进制语义会在序列化时丢失。

## 服务封装：仓储、查询服务、校验服务

上层代码不应该直接接触 `Model`、`Graph`、`QueryExecution`，更不应该自己拼 SPARQL。按用例封装成窄接口，是让本体可维护的关键一步。

```java
public interface OrderSemanticRepository {
    void save(Order order);
    Optional<Order> findById(String orderId);
}

public interface OrderCompetencyQuery {
    List<OrderSummary> findOrdersByCustomer(String customerId, int limit);
}

public interface ValidationService {
    ValidationReport validate(Resource resource);
}
```

`OrderCompetencyQuery` 对应一条能力问题，而不是一张表。它的实现读取 `queries/find-orders-by-customer.rq`，通过参数绑定传入客户 IRI，再把结果集映射成 DTO 返回。

```sparql
PREFIX c: <https://example.com/onto/commerce#>

SELECT ?order ?amount
WHERE {
  ?order a c:Order ;
         c:customer ?customer ;
         c:amount ?amount .
  FILTER (?customer = ?customerIri)
}
LIMIT 50
```

```java
import java.nio.file.Files;
import java.nio.file.Path;
import org.apache.jena.query.*;

String queryText = Files.readString(
        Path.of("queries/find-orders-by-customer.rq"));

QuerySolutionMap binding = new QuerySolutionMap();
binding.add("customerIri", ResourceFactory.createResource(customerIri));

try (QueryExecution qe = QueryExecutionFactory.create(
        QueryFactory.create(queryText), model, binding)) {
    ResultSet rs = qe.execSelect();
    while (rs.hasNext()) {
        QuerySolution row = rs.next();
        // 映射为 OrderSummary，而不是把 ResultSet 抛给上层
    }
}
```

参数绑定除了可读性，更重要的是防注入：不要把用户输入拼进查询字符串。`LIMIT` 这类整型参数不能用变量占位，需要先校验为合法整数再拼入，或固定上限后由应用层截断。

Python 侧的查询服务同理，用 `initBindings` 传参：

```python
from rdflib import Graph, URIRef

graph = Graph().parse("data/commerce.ttl", format="turtle")
rows = graph.query(query_text, initBindings={
    "customerIri": URIRef(f"https://example.com/data/customer/{customer_id}")
})
```

## 推理与校验的代码位置

推理不要在每次请求里全量执行。常见做法是启动时加载 TBox 并预热，或在批处理任务中物化推断结果；请求路径只做查询。推理器与规则集的选择见[推理与物化](/impl/arch-pattern/inference)。

校验则相反，应该出现在写入口和发布流程里：数据导入前、本体发布前、关键写入前各跑一次，结果要落到可追溯的报告中（哪个 Shape、哪个节点、什么严重级别）。OWL 管一致性、SHACL 管完整性的分工见[SHACL 形状约束](/spec/shacl)与[约束与业务规则](/modeling/elements/constraint)。

## 反模式清单

- **IRI 字符串满天飞**：没有常量类，改命名空间时全局替换，错误只在运行期暴露。
- **业务逻辑写进 SPARQL**：查询会变成不可测、不可复用、无法评审的“第二套代码”；规则应放在 SHACL 或领域服务里。
- **把 `Model` 传遍全系统**：上层被迫理解 RDF 细节，换存储或换语言时全部重写。
- **每次请求重新解析 TTL**：本体文件读盘加解析的开销被忽略，延迟随文件变大而上升。
- **用 `rdfs:domain/range` 当校验**：开放世界假设下缺类型只会推出更多结论，不会报错。
- **图库与关系库双写**：没有补偿或事件机制时必然长期不一致。
- **把 TBox 塞进 ABox 图**：版本回滚与数据重建互相牵连，备份策略也无法分开。

## 落地顺序

1. 定命名空间与 IRI 规范，落成常量类或命名空间对象。
2. 建立 `ontologies/`、`shapes/`、`queries/` 三个受版本控制的目录。
3. 先封装一个查询服务和一次 SHACL 校验，跑通端到端。
4. 再加映射器与仓储，把 RDF 与领域对象隔开。
5. 最后接推理与治理流程，见[分层与六边形架构](/impl/arch-pattern/layered)。

技术栈具体到 JVM、Python、JavaScript 的库与依赖，见[JVM 技术栈](/impl/tech/jvm)、[Python 技术栈](/impl/tech/python)、[JavaScript/TypeScript 技术栈](/impl/tech/js)。
