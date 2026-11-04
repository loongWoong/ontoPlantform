# JVM 技术栈

JVM 是本体工程中生态最完整的技术栈之一。解析、SPARQL、推理、SHACL 校验都有可直接用于生产的库，商业图数据库也大多以 Java 客户端为一等公民。代价是组件多、职责容易重叠，我们需要先分清“哪个库解决哪一段”，否则会出现 Jena 与 RDF4J 混用、两套模型互相转换的维护负担。

一条实用结论：需要做服务端、需要完整推理器、需要企业级运维时选 JVM；具体到库，通用场景优先 Apache Jena，需要 OWL 2 公理级操作与 DL 推理时用 OWL API 加 HermiT 或 ELK，已有 RDF4J 仓储抽象习惯的团队继续用 RDF4J 即可，不必迁移。

## 生态分工

| 库 | 定位 | 关键 API | 典型用途 |
|---|---|---|---|
| Apache Jena | RDF 模型、解析、SPARQL、存储、服务 | `Model`、`RDFDataMgr`、`QueryExecution`、`TDB2Factory`、`Fuseki` | 应用内建模与服务端主力 |
| Eclipse RDF4J | 仓储抽象、SPARQL、Sail 存储 | `Repository`、`RepositoryConnection`、`SailRepository` | 已有 Java 仓储风格的项目 |
| OWL API | OWL 2 对象模型与公理操作 | `OWLOntologyManager`、`OWLOntology`、`OWLReasoner` | 程序化修改本体、DL 推理 |
| ELK | OWL 2 EL 推理器 | `ElkReasonerFactory` | 大规模 EL profile 本体分类 |
| HermiT | OWL 2 DL 推理器 | OWL API 的 `OWLReasonerFactory` 实现 | 完整 DL 一致性检查 |
| Jena SHACL | SHACL 校验 | `ShaclValidator`、`Shapes`、`ValidationReport` | 数据与本体发布前校验 |
| RDF4J SHACL | 事务内 SHACL 校验 | `ShaclSail` | 写入即校验 |

要特别注意：Jena 自带的 OWL 推理器是规则型实现，覆盖 RDFS 与 OWL 的一部分语义，不等价于完整的 OWL 2 DL 推理。需要严格的 DL 推理时，用 OWL API 加 HermiT 或 ELK，而不是指望 Jena 的 `OntModel`。

## Maven 依赖

版本统一由属性或 BOM 管理，不要在业务模块里散落具体版本号。

```xml
<!-- ${jena.version} 等版本属性由父 POM 的 properties 统一管理 -->
<dependency>
  <groupId>org.apache.jena</groupId>
  <artifactId>apache-jena-libs</artifactId>
  <version>${jena.version}</version>
  <type>pom</type>
</dependency>

<dependency>
  <groupId>org.apache.jena</groupId>
  <artifactId>jena-shacl</artifactId>
  <version>${jena.version}</version>
</dependency>
```

`apache-jena-libs` 是聚合 POM，一次引入 Jena 的主要模块。RDF4J 用 BOM 管理版本：

```xml
<dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>org.eclipse.rdf4j</groupId>
      <artifactId>rdf4j-bom</artifactId>
      <version>${rdf4j.version}</version>
      <type>pom</type>
      <scope>import</scope>
    </dependency>
  </dependencies>
</dependencyManagement>
```

OWL API 的坐标为 `net.sourceforge.owlapi:owlapi-distribution`；ELK、HermiT 按所选推理器引入对应的 OWL API 绑定，具体 artifact 以各自项目文档为准。

## 读 Turtle、建模型、跑查询

```java
import org.apache.jena.query.*;
import org.apache.jena.rdf.model.*;
import org.apache.jena.riot.RDFDataMgr;

Model model = ModelFactory.createDefaultModel();
RDFDataMgr.read(model, "ontologies/commerce-core.ttl");
RDFDataMgr.read(model, "data/orders.ttl");

String cq = """
        PREFIX c: <https://example.com/onto/commerce#>
        SELECT ?order ?amount
        WHERE {
          ?order a c:Order ;
                 c:amount ?amount ;
                 c:customer ?customer .
          FILTER (?customer = ?customerIri)
        }
        LIMIT 50
        """;

QuerySolutionMap binding = new QuerySolutionMap();
binding.add("customerIri",
        ResourceFactory.createResource("https://example.com/data/customer/42"));

try (QueryExecution qe = QueryExecutionFactory.create(
        QueryFactory.create(cq), model, binding)) {
    ResultSet rs = qe.execSelect();
    while (rs.hasNext()) {
        QuerySolution row = rs.next();
        System.out.println(row.getResource("order").getURI()
                + " " + row.getLiteral("amount").getLexicalForm());
    }
}
```

三个细节。`QueryExecution` 必须关闭，否则游标与连接会泄漏，用 try-with-resources。参数用 `QuerySolutionMap` 绑定，避免把外部输入拼进查询造成注入。文本块需要 Java 15 及以上，低版本改用普通字符串拼接。

## TDB2 与事务

生产环境不要把大图全量读进 `Model`。TDB2 提供事务与磁盘存储：

```java
import org.apache.jena.query.Dataset;
import org.apache.jena.query.ReadWrite;
import org.apache.jena.riot.RDFDataMgr;
import org.apache.jena.tdb2.TDB2Factory;

Dataset dataset = TDB2Factory.connectDataset("target/tdb2");

dataset.begin(ReadWrite.WRITE);
try {
    RDFDataMgr.read(dataset.getDefaultModel(), "data/orders.ttl");
    dataset.commit();
} catch (RuntimeException e) {
    dataset.abort();
    throw e;
} finally {
    dataset.end();
}
```

读事务同理，用 `ReadWrite.READ`。TDB2 的数据目录是运行时状态，不要提交到仓库；本体的 TBox 更新应当作为一次受控迁移执行，而不是每次启动都覆盖写入。

## 推理：先选对引擎

只需要 RDFS 级别的类型与子属性推断，Jena 的规则推理器足够：

```java
import org.apache.jena.reasoner.Reasoner;
import org.apache.jena.reasoner.ReasonerRegistry;

Reasoner reasoner = ReasonerRegistry.getOWLReasoner();
InfModel inf = ModelFactory.createInfModel(reasoner, model);
```

这类推理是规则匹配，遇到复杂类表达式或大图时可能变慢甚至不终止，必须做规模测试。需要 OWL DL 时用 OWL API：

```java
import java.io.File;
import org.semanticweb.elk.owlapi.ElkReasonerFactory;
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.model.OWLOntology;
import org.semanticweb.owlapi.model.OWLOntologyManager;
import org.semanticweb.owlapi.reasoner.InferenceType;
import org.semanticweb.owlapi.reasoner.OWLReasoner;

OWLOntologyManager manager = OWLManager.createOWLOntologyManager();
OWLOntology ontology = manager.loadOntologyFromOntologyDocument(
        new File("ontologies/commerce-core.owl"));

OWLReasoner reasoner = new ElkReasonerFactory().createReasoner(ontology);
boolean consistent = reasoner.isConsistent();
reasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY);
reasoner.dispose();
```

ELK 面向 OWL 2 EL profile，超出 profile 的公理不在其保证范围内；需要完整 DL 时换成 HermiT。两者都适合“发布前一致性检查 + 分类结果物化”，不适合放在每个 HTTP 请求里实时跑。推理时机与物化策略见[推理与物化](/impl/arch-pattern/inference)。

## SHACL 校验

Jena 的 SHACL 模块用法直接：

```java
import org.apache.jena.graph.Graph;
import org.apache.jena.riot.RDFDataMgr;
import org.apache.jena.shacl.ShaclValidator;
import org.apache.jena.shacl.Shapes;
import org.apache.jena.shacl.ValidationReport;

Graph shapesGraph = RDFDataMgr.loadGraph("shapes/order-shape.ttl");
Graph dataGraph = RDFDataMgr.loadGraph("data/orders.ttl");

Shapes shapes = Shapes.parse(shapesGraph);
ValidationReport report = ShaclValidator.get().validate(shapes, dataGraph);

if (!report.conforms()) {
    report.getEntries().forEach(entry ->
            System.err.println(entry.focusNode() + " " + entry.resultMessage()));
}
```

如果需要“写入即校验”，RDF4J 的 `ShaclSail` 把校验放进事务：不合规的提交直接失败，省掉应用层的事后检查。代价是每次写入都付出校验开销，形状复杂时要评估吞吐。

```java
import org.eclipse.rdf4j.repository.Repository;
import org.eclipse.rdf4j.repository.sail.SailRepository;
import org.eclipse.rdf4j.sail.memory.MemoryStore;
import org.eclipse.rdf4j.sail.shacl.ShaclSail;

ShaclSail shaclSail = new ShaclSail(new MemoryStore());
Repository repository = new SailRepository(shaclSail);
repository.init();
```

## Fuseki 的边界

Fuseki 是快速暴露 SPARQL 端点的方式，但默认不带用户认证，SPARQL Update 端点一旦开放，任何能访问端口的人都能改数据。生产部署至少要做四件事：启用认证与按数据集的权限、只读查询与更新分离、配置查询超时与结果集上限、放在内网或网关之后。用 Fuseki 做公网开放端点，需要额外的限流与审计。

## 常见问题

**要不要用 `OntModel`？** `OntModel` 在 `Model` 之上加了本体词汇与可选的推理封装，读写 `owl:Class`、`rdfs:subClassOf` 时更方便。代价是隐式推理开销与额外的类型转换，规模变大后不易控制。简单场景可以用，复杂链路建议直接用 `Model` 加显式推理器，把推理时机握在自己手里。

**为什么推理出来的三元组查不到？** 推理结果存在于 `InfModel` 或 `OntModel` 这个视图上，不会自动写回底层 `Model`。查询必须对推理视图执行；如果希望持久化，需要把推断结果物化写回存储，并接受随之而来的膨胀与增量更新问题。

**Jena 与 RDF4J 能只选一个吗？** 能，而且推荐。两者都提供模型、解析、SPARQL 与存储，能力高度重叠，混用只会增加依赖与转换成本。已有 RDF4J 仓储体系就继续用 RDF4J，新项目从 Jena 起步更常见。

## 实践注意

- **`Model` 可变且非线程安全**。每个请求或线程使用独立模型；共享数据用 TDB2 数据集的事务，不要在多线程间传同一个 `Model`。
- **推理器与 `InfModel` 非线程安全**。需要并发时每线程实例化，或把推理结果物化后只共享只读结果。
- **别把大图读进内存**。`RDFDataMgr.loadGraph` 适合样例与小文件；生产用 TDB2 或远端端点，并给查询设上限。
- **Jena 与 RDF4J 不要混用模型类型**。两套 `Model`/`Graph` 抽象不同，同一模块里混用会带来无谓的转换代码；选一套作为主模型，边界处显式转换。
- **序列化用标准格式**。`RDFDataMgr.write(out, model, RDFFormat.TURTLE)` 便于人工审阅与跨语言交换；N-Triples 更适合流式与差分。
- **依赖冲突要检查**。Jena、RDF4J、OWL API 都可能传递引入不同版本的 RDF 与 JSON 库，使用 `mvn dependency:tree` 定期排查。

## 与其他语言的协作

JVM 服务不必独占整个系统。常见形态是 JVM 侧持有图存储与推理，Python 侧做数据管道与 LLM 集成，前端通过 BFF 取子图。跨语言边界用 SPARQL 端点、RDF 文件或 JSON-LD 消息，不要在进程间共享 `Model` 对象。这样团队可以按技能分工，代价是多一层网络与序列化开销，需要在接口粒度上控制。

## 选型建议

先跑通“读 Turtle、一条 CQ 查询、一次 SHACL 校验”，再决定要不要推理以及用哪个引擎。通用服务端用 Jena；需要 OWL 公理操作与 DL 推理加 OWL API；已有 RDF4J 体系则保持不动。存储与端点的取舍见[存储选型](/impl/arch-pattern/storage)，与其他语言的协作见[Python 技术栈](/impl/tech/python)。
