# JavaScript/TypeScript 技术栈

JS/TS 在本体系统里的定位是“接入与呈现”，不是“存储与推理”。前端和 Node 能解析 RDF、调用 SPARQL、渲染子图、做轻量校验，但全量知识图谱和 OWL DL 推理不应放在浏览器里。选型时先把这条边界划清楚，后面的库选择会简单很多。

一个务实的分工：SPARQL 查询与推理留在服务端，前端只请求按用例裁剪过的子图；需要离线或边缘能力时，用 Oxigraph 的 WASM 版本在本地装一小份图；Node 侧适合做语义 BFF，把多个端点和多个数据源聚合成前端友好的 JSON。

## 生态分工

| 库/工具 | 运行环境 | 能做什么 | 不适合 |
|---|---|---|---|
| N3.js | Node 与浏览器 | Turtle/TriG/N-Triples 解析、RDF/JS 数据集、序列化 | 不提供 SPARQL 引擎 |
| rdflib.js | 浏览器为主 | 前端图存储、解析、增量同步、SPARQL 客户端 | 大数据量与复杂查询 |
| oxigraph（WASM） | 浏览器与 Node | 嵌入式 SPARQL 1.1 查询与存储 | 全量图、高并发 |
| rdf-validate-shacl 等 | Node 与浏览器 | 基于 RDF/JS 的 SHACL 校验 | 缺少预推理时语义不完整 |
| jsonld | Node 与浏览器 | JSON-LD 的 expand、compact、转 RDF | 不做语义校验 |
| Cytoscape.js、vis-network、AntV G6 | 浏览器 | 图可视化、布局、交互 | 不做数据建模 |

## N3.js：解析、模式查询与序列化

N3.js 是 RDF/JS 规范的常用实现，适合在 Node 或浏览器里做解析与轻量数据处理。

```ts
import { DataFactory, Parser, Store, Writer } from 'n3';

const turtle = `
@prefix c: <https://example.com/onto/commerce#> .
<https://example.com/data/order/1001> a c:Order ;
    c:amount "99.00"^^<http://www.w3.org/2001/XMLSchema#decimal> .
`;

const parser = new Parser({ format: 'text/turtle' });
const quads = parser.parse(turtle);

const store = new Store(quads);
const orderType = DataFactory.namedNode(
  'https://example.com/onto/commerce#Order',
);
const rdfType = DataFactory.namedNode(
  'http://www.w3.org/1999/02/22-rdf-syntax-ns#type',
);

const orders = store.getQuads(null, rdfType, orderType, null);
console.log(`订单数：${orders.length}`);

const writer = new Writer({ format: 'text/turtle' });
writer.addQuads(store.getQuads(null, null, null, null));
writer.end((error, result) => {
  if (error) throw error;
  console.log(result);
});
```

`Store.getQuads` 按四个位置做模式匹配，`null` 表示通配，这是前端做局部查询最常用的方式。注意 N3.js 做的是“按模式找三元组”，不是 SPARQL；需要联表、聚合、属性路径时，要么写多步遍历，要么把查询交给端点。

解析大文件时不要在主线程一次性处理，用 `Parser` 的流式写法配 Web Worker，避免页面卡顿。

## 调用 SPARQL 端点

最常见的接入方式是通过 HTTP 调服务端端点，拿 SPARQL 结果 JSON 再渲染。

```ts
const endpoint = 'https://example.com/sparql';

async function findOrders(customerIri: string) {
  const query = `
    PREFIX c: <https://example.com/onto/commerce#>
    SELECT ?order ?amount
    WHERE {
      ?order a c:Order ;
             c:customer ?customer ;
             c:amount ?amount .
      FILTER (?customer = <${customerIri}>)
    }
    LIMIT 50
  `;

  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/sparql-query',
      Accept: 'application/sparql-results+json',
    },
    body: query,
  });

  if (!response.ok) {
    throw new Error(`SPARQL 请求失败：${response.status}`);
  }

  const { results } = await response.json();
  return results.bindings.map((row: any) => ({
    order: row.order.value,
    amount: row.amount.value,
  }));
}
```

两个安全点。第一，不要把 IRI 直接拼进查询字符串：这里用模板插值只为示例，生产代码应当由后端接收业务 ID 并完成绑定，前端只传 ID，不传任意 IRI。第二，浏览器不应直连生产端点：端点需要处理 CORS 与认证，通常是前端调自家 BFF，由 BFF 携带凭据访问端点并限制查询模板。

## 浏览器内的图存储：Oxigraph WASM 与 rdflib.js

Oxigraph 的 JS/WASM 包把 Rust 实现的 SPARQL 引擎带进浏览器或 Node，可以在本地装载术语表或子图并执行查询。它适合离线演示、文档站内嵌查询、以及“数据量不大但要真 SPARQL”的场景。代价是 WASM 包体积与初始化时间，需要按需加载，且不要指望它承载全量知识图谱。

rdflib.js 面向浏览器场景，提供前端图存储、解析、与远端文档的增量同步，以及 SPARQL 客户端能力。它适合“页面里维护一小份 RDF 状态”的应用，例如表单草稿、实体卡片、离线编辑。选它之前先确认版本与 API，前端库的迭代速度通常快于 JVM 生态。

无论用哪个，原则一致：浏览器只持有当前用例需要的子图。全量图放在服务端，按需拉取邻居，用缓存与分页控制内存。

## SHACL 与 JSON-LD

SHACL 校验在前端通常只做即时反馈：用户填表时对已加载的形状做局部校验，提示缺字段或格式错误。但局部数据通过不代表全局合规，真正的校验必须由服务端在完整数据上执行。形状文件由服务端统一发布，前端不要各自维护一份，否则容易出现两套规则。SHACL 的语义见[SHACL 形状约束](/spec/shacl)。

JSON-LD 是前端最友好的 RDF 序列化形式。用 `@context` 把 IRI 映射成短名，接口返回的 JSON 既可读又能直接转成三元组。常见做法是：后端用 JSON-LD 输出，前端用 `jsonld` 库做 `expand`/`compact`，需要三元组时转成 N-Quads 交给 N3.js。注意 `@context` 本身也要版本化，改了上下文就等于改了数据契约。

## 可视化：从三元组到图视图

可视化库只负责画图，不负责理解 RDF。渲染前要做一次“视图模型转换”：把三元组聚合成节点与边，隐藏 `rdf:type` 这类结构性谓词，把类型映射成节点形状或颜色，把对象属性映射成边，数据属性放进节点详情面板。

- Cytoscape.js：图数据结构与布局算法完整，交互与扩展生态成熟，适合中等规模的业务图谱。
- vis-network：上手快、配置简单，适合内部工具与演示。
- AntV G6：布局、交互与组件体系较完整，适合产品化的图谱前端。

三条实践原则。按能力问题取子图，一次渲染几十到几百个节点，而不是全量。节点用 IRI 做稳定 key，避免每次刷新后状态丢失。点击节点再按需拉邻居，做渐进展开；布局计算放到 Worker 里，避免大图阻塞主线程。

## 边界与性能

- **内存**：浏览器里几十万条三元组就开始吃紧，WASM 图建议只装子图或术语表。
- **主线程**：解析大文件、跑布局都会阻塞 UI，用 Web Worker 分担。
- **查询**：前端只发模板化查询，不接收用户自由输入的 SPARQL；结果做字段投影与分页。
- **推理**：不在浏览器跑 DL 推理；即使是 RDFS 传递闭包，也要先估算数据量。
- **认证与 CORS**：直连端点需要 CORS 配置与令牌方案，更常见的做法是经自家 BFF 代理。
- **缓存**：按版本或 ETag 缓存图数据与形状文件，避免每次进入页面重新下载。

## 适用场景

适合：知识图谱浏览与探索、语义搜索的实体卡片与关系路径展示、录入表单的即时校验反馈、静态文档站内嵌可查询的小型本体、Node 侧聚合多个语义端点的 BFF。

不适合：全量知识图谱存储、企业级推理、多用户并发治理、需要复杂 SPARQL 的分析任务。这些交给[JVM 技术栈](/impl/tech/jvm)或[Python 技术栈](/impl/tech/python)侧的服务。

## 推荐组合

- **N3.js 加 fetch 加 Cytoscape.js**：解析、查询、渲染三件事各自用最合适的库，是前端知识图谱最常见的组合。
- **rdflib.js 加自家 BFF**：前端维护局部 RDF 状态，BFF 负责认证、模板查询与结果裁剪。
- **Oxigraph WASM 加静态数据**：离线演示与文档站内嵌查询，数据量控制在子图规模。

架构上如何把前端与语义服务隔开，见[分层与六边形架构](/impl/arch-pattern/layered)；知识图谱的整体构建流程见[知识图谱构建](/application/knowledge-graph)。
