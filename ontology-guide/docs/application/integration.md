# 语义集成与互操作

系统集成的主流做法是点对点接口或企业服务总线（ESB），两者都能跑通流程，但都不解决**含义**问题：字段叫 `cust_name` 还是 `customerName`、金额单位是元还是分、状态码 `02` 到底表示什么，只能靠文档和口头约定。语义集成把这些问题前移到模型层，用统一 IRI 和显式映射替代逐对谈判。

## 语义集成解决什么

| 集成方式 | 耦合点 | 新增一个系统 | 语义一致性 |
|---|---|---|---|
| 点对点接口 | 每个调用方与被调方 | 需要 n 条新映射 | 每对单独约定 |
| ESB / 消息总线 | 消息格式与路由 | 需接入总线并转换格式 | 转换靠映射组件，语义不显式 |
| 语义层（本体 + 映射） | 共享词表与 IRI | 只需提供到共享模型的映射 | 由模型统一保证 |

结论：**点对点适合少量系统、稳定契约；本体适合系统多、语义易变、需要跨组织交换的场景。** 如果只有两三个系统且接口稳定，不要为了“先进”引入语义层，成本不划算。

语义集成有三个层次，投入依次上升：

1. **词表对齐**：统一术语与编码（国家、货币、单位、状态），先解决“词不同”；
2. **数据映射**：把源结构映射到共享本体，产出 RDF，解决“结构不同”；
3. **联邦与虚拟化**：不搬数据，查询时跨源组合，解决“数据不能动”。

多数项目做到第 2 层就有明显收益，第 3 层按需。

## 映射方式选型

| 方式 | 标准状态 | 适用 | 注意 |
|---|---|---|---|
| R2RML | W3C 推荐标准 | 关系表 → RDF，映射清晰稳定 | 只覆盖关系数据库 |
| RML | 社区规范 | 多格式源（CSV/JSON/XML），扩展 R2RML 思路 | 工具支持不如 R2RML 普及 |
| SPARQL CONSTRUCT | W3C 标准查询语言的一部分 | 复杂转换、需调用外部逻辑 | 映射逻辑写在查询里，可读性要控制 |
| 程序映射（代码） | 无标准 | 大规模、需调服务、需复杂清洗 | 必须配测试与来源记录 |

示例：把源表映射到共享本体的最小 `CONSTRUCT`：

```sparql
PREFIX ex: <https://example.com/shared#>
PREFIX src: <https://example.com/source/>

CONSTRUCT {
  ?customer a ex:Customer ;
            ex:legalName ?name ;
            ex:taxId ?taxId ;
            ex:registeredIn ?country .
  ?country a ex:Country .
}
WHERE {
  ?row src:cust_name ?name ;
       src:tax_id ?taxId ;
       src:country_code ?cc .
  BIND(IRI(CONCAT("https://example.com/customer/", ENCODE_FOR_URI(?taxId))) AS ?customer)
  BIND(IRI(CONCAT("https://example.com/country/", ENCODE_FOR_URI(?cc))) AS ?country)
}
```

映射的三条纪律：**IRI 由业务主键派生且稳定**、**映射文档进版本库并有测试**、**每条三元组保留来源**（`prov:wasDerivedFrom`）。缺了第三条，数据出错时无法回溯。

## 联邦查询与数据虚拟化

数据不能搬（合规、时效、体量）时，用 SPARQL 联邦查询：`SERVICE` 把子查询发给远端端点，本地组合结果。

```sparql
PREFIX ex: <https://example.com/shared#>

SELECT ?customer ?orderCount WHERE {
  SERVICE <https://partner.example.com/sparql> {
    ?customer a ex:Customer ; ex:taxId ?taxId .
  }
  {
    SELECT ?taxId (COUNT(?order) AS ?orderCount) WHERE {
      ?order ex:hasCustomer/ex:taxId ?taxId .
    } GROUP BY ?taxId
  }
}
```

联邦查询的现实约束必须提前说清楚：

- **性能不可控**：受远端端点延迟与限流影响，`SERVICE` 嵌套或无界模式可能超时；
- **端点能力不一**：对方可能只支持部分 SPARQL 1.1，甚至只提供 REST；
- **可用性耦合**：远端挂掉，查询整体失败。关键路径要有降级（缓存上次结果、改走批量同步）；
- **权限与合规**：联邦不搬数据不等于没有数据出境问题，需法务确认。

因此**联邦适合探索性查询与少量关键查询，不适合高频、低延迟、强一致的业务调用**。后者建议做受控同步 + 本地图谱。

## API 语义化

对外接口同样可以带语义：

- **JSON-LD**：在现有 JSON 中加 `@context`，把字段映射到 IRI，前端无需改动即可获得可扩语义。适合渐进改造；
- **内容协商**：同一资源同时提供 JSON 与 Turtle，按 `Accept` 头返回，见 [RDF 数据模型与序列化](/spec/rdf)；
- **超媒体与链接**：Hydra 等思路用词汇描述可用操作与参数，目前是社区方向，采用前先确认客户端生态支持；
- **版本策略**：语义化 API 的字段变更遵循本体版本策略，见 [版本与演进](/impl/arch-pattern/versioning)。

一个 JSON-LD 片段：

```json
{
  "@context": {
    "ex": "https://example.com/shared#",
    "legalName": "ex:legalName",
    "taxId": { "@id": "ex:taxId", "@type": "@id" }
  },
  "@id": "https://example.com/customer/91310000XXXXXXXX",
  "@type": "ex:Customer",
  "legalName": "示例科技有限公司",
  "taxId": "91310000XXXXXXXX"
}
```

## 行业标准：能复用就别自造

按准确度择用，采用前核对版本与许可：

| 标准 | 维护方 | 覆盖领域 | 用法建议 |
|---|---|---|---|
| schema.org | 社区词表（Google、Microsoft、Yahoo、Yandex 发起） | 通用实体（产品、组织、事件、文章） | 面向公开 Web 数据的轻量标注，可直接复用 |
| FHIR | HL7 | 医疗健康 | 数据交换格式强，RDF 表示需借助映射；以官方发布的 RDF 定义为依据 |
| GS1 | GS1 组织 | 商品、条码、供应链（GTIN、GLN、EPCIS） | 商品与物流标识优先复用，不要自造编码 |
| IFC | buildingSMART（ISO 16739） | 建筑与工程 | 与 BIM 工具交换时使用，配合其数据模型 |
| DCAT | W3C | 数据目录 | 描述数据集与服务，数据治理必备 |

采用标准的判断顺序：**先看行业是否已有事实标准 → 再看能否覆盖 80% 需求 → 最后才扩展自定义属性**。扩展时用独立命名空间，不要修改标准词表的语义。

## ESB 与语义层的分工

两者不是替代关系：

- **ESB 管流程与路由**：消息可靠投递、协议转换、事务补偿；
- **语义层管含义**：字段与概念对齐、编码统一、跨源实体合并。

现实中常见组合是“ESB 传输 + 语义层解释”：消息体用 JSON-LD 或 RDF 承载，ESB 负责可靠送达，语义服务负责把消息映射进共享模型并校验。若已有 ESB，不必推倒重来，只需在消息体中补上语义标识。

## 实践建议与坑

1. **先对齐编码，再谈模型**。国家、货币、单位、状态码不统一，映射写得再好也没用。
2. **映射是长期资产**。为每个源系统维护映射文档与测试样例，随源变更同步更新。
3. **不要试图统一所有系统的内部结构**。共享模型只覆盖互操作必需的部分，各系统内部模型保持独立。这与 DDD 的限界上下文思想一致，见 [领域驱动设计（DDD）是什么](https://ddd-fans.github.io/ddd-guideline/main)。
4. **实体主键要稳定**。源系统主键变更会让外部引用全部失效，必要时引入代理键。
5. **区分“同一事物”与“相似事物”**。用 `owl:sameAs` 前先确认，错并会顺着推理扩散。
6. **联邦查询设超时与降级**，并在监控里单独统计远端失败率。
7. **标准不是越新越好**。对接方支持哪个版本，往往比标准本身先进与否更重要。

延伸阅读：[数据治理与主数据](/application/data-governance)（共享术语基础）、[存储选型](/impl/arch-pattern/storage)（本地图谱与混合架构）、[RDF 数据模型与序列化](/spec/rdf)（JSON-LD 上下文与序列化）。
