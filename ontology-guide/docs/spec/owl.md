# OWL 2 本体语言

OWL 2 是 W3C 于 2009 年发布的正式推荐标准（2012 年出版第二版），用于在 RDF 之上表达比 RDFS 更丰富的本体公理。它的理论根基是描述逻辑（Description Logic），所以既能表达“订单必须由客户下达”这类结构知识，也能让推理器据此推导出新事实或发现矛盾。

但要先记住一句话：**OWL 是用来推理和检查一致性的，不是用来校验业务数据的**。理解这一点，比记住所有构造子更重要。

## OWL 2 的定位

OWL 2 有三种“方言”层面的选择：

- **OWL 2 DL**：表达力强，有可判定的推理，主流推理器支持；
- **OWL 2 Full**：在 RDF 上不加限制，表达力最强但没有推理保证，实践中很少作为目标；
- **OWL 2 Profiles（EL / QL / RL）**：牺牲部分表达力换取可计算性与工程性能，是落地时的重点。

OWL 2 常用的序列化是 Turtle（RDF 语法），也可以用 OWL/XML、Manchester 语法。无论哪种语法，模型语义相同。

## 类表达式：把概念组合起来

类表达式让“类”不再只是一个名字，而是可以由其他类、属性和值定义：

```turtle
@prefix ex:  <https://example.com/ontology/order#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:PaidOrder a owl:Class ;
    owl:equivalentClass [
        a owl:Class ;
        owl:intersectionOf (
            ex:Order
            [ a owl:Restriction ;
              owl:onProperty ex:paymentStatus ;
              owl:hasValue ex:Paid ]
        )
    ] .

ex:ExpressOrder a owl:Class ;
    owl:equivalentClass [
        a owl:Restriction ;
        owl:onProperty ex:deliveryOption ;
        owl:someValuesFrom ex:ExpressDelivery
    ] .
```

常用构造子包括：

| 构造 | 含义 | 典型用途 |
|---|---|---|
| `owl:intersectionOf` | 交集（and） | 组合特征定义子类 |
| `owl:unionOf` | 并集（or） | 多个来源合并为一个概念 |
| `owl:complementOf` | 补集（not） | 互斥分类 |
| `owl:oneOf` | 枚举 | 状态、等级等有限取值 |
| `owl:someValuesFrom` | 至少有一个值属于某类 | 存在性要求 |
| `owl:allValuesFrom` | 所有值都属于某类 | 值域限制（仅在属性出现时生效） |
| `owl:hasValue` | 必须取某个具体值 | 固定状态或标志 |
| `owl:minCardinality` / `maxCardinality` | 基数限制 | 数量约束 |
| `owl:minQualifiedCardinality` + `owl:onClass` | 限定基数 | “至少两个不同商品” |

## 属性特征

对象属性的特征决定了推理能力：

| 特征 | 推理效果 |
|---|---|
| `owl:inverseOf` | 双向关系可互推 |
| `owl:TransitiveProperty` | 传递闭包，如“属于上级组织” |
| `owl:SymmetricProperty` | 对称关系，如“与…合作” |
| `owl:FunctionalProperty` | 至多一个值，常表达唯一标识 |
| `owl:InverseFunctionalProperty` | 值唯一确定主语 |
| `owl:ReflexiveProperty` / `owl:IrreflexiveProperty` | 自反/非自反 |
| `owl:AsymmetricProperty` | 非对称 |
| `owl:propertyDisjointWith` | 两个属性不能同时成立 |
| `owl:propertyChainAxiom` | 属性链，如“父之兄为伯父” |
| `owl:hasKey` | 用一组属性作为键，键相同则个体相同 |

```turtle
@prefix ex:   <https://example.com/ontology/order#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

ex:hasEmployeeNo a owl:DatatypeProperty , owl:FunctionalProperty ;
    rdfs:domain ex:Employee ;
    rdfs:range  xsd:string .

ex:Employee owl:hasKey ( ex:hasEmployeeNo ) .

ex:managerOf a owl:ObjectProperty ;
    owl:inverseOf ex:reportsTo .
```

注意 `owl:hasKey` 与 `owl:FunctionalProperty` 的差别：前者用于“键相同则同一个人”，后者用于“一个主语最多一个值”，二者不能互相替代。

## 个体同一性：sameAs 与不同个体

OWL 默认**没有唯一名称假设（no UNA）**：两个不同的 IRI 完全可能指同一个体。要表达“这是同一个人”，写 `ex:A owl:sameAs ex:B`；要表达“这是两个人”，必须显式写 `owl:differentFrom` 或 `owl:AllDifferent`。

这带来两个实践后果：

- 不写 `differentFrom` 不代表不同，推理器不会因为你用了两个 IRI 就认为它们是两个人；
- `sameAs` 会合并两个个体的所有断言，误用会污染数据。实体链接得到的 `sameAs` 必须经过审核，且最好保留来源与置信度。

## 开放世界假设的后果

OWL 采用开放世界假设（OWA）：**没有说的不等于假的**。因此：

- 订单没有 `placedBy`，推理器不会报错，只会认为“尚未知道”；
- 要表达“订单必须由客户下达”，`someValuesFrom` 只能推出“存在一个客户”，推不出“缺失即错误”；
- 若两个类声明为 `owl:disjointWith`，而数据迫使某个个体同时属于两者，推理器会判定本体或数据**不一致**（inconsistent），这是唯一会“报错”的机制。

```turtle
ex:Customer a owl:Class .
ex:Supplier a owl:Class ;
    owl:disjointWith ex:Customer .
```

如果一条数据同时断言某个主体既属于 `Customer` 又属于 `Supplier`，推理器会发现矛盾。但如果只是“某个属性没有填写”，它什么也不会说——这正是需要 [SHACL](/spec/shacl) 的地方。

## Profile 与推理复杂度

| Profile | 表达力取舍 | 适合场景 | 代表推理器 |
|---|---|---|---|
| OWL 2 EL | 支持存在量词、交、类/属性层次、部分属性链，不支持全称、析取、否定 | 大规模术语分类、SNOMED 类本体 | ELK |
| OWL 2 QL | 面向查询重写，可把 SPARQL 改写到关系数据库 | OBDA、数据虚拟化 | 查询重写引擎（如 Ontop） |
| OWL 2 RL | 可用规则引擎/Datalog 实现，适合流式与增量推理 | 规则密集型系统、生产环境 | 规则引擎 |
| OWL 2 DL | 表达力最强，复杂度高 | 复杂约束建模、学术与深度推理 | HermiT、Pellet |

实践建议：能用 EL 表达就用 EL，需要规则就用 RL，只有确实需要复杂否定/析取时才上完整 DL。全量 DL 公理在大数据上很容易推理爆炸。

## OWL、RDFS 与 SHACL 的边界

| 维度 | RDFS | OWL 2 | SHACL |
|---|---|---|---|
| 主要用途 | 轻量类层次 | 形式化语义与推理 | 数据校验 |
| 世界假设 | 开放 | 开放 | 封闭 |
| 缺数据 | 不报错 | 不报错 | 可报错 |
| 典型问题 | 这是子类吗 | 这个概念能推出什么、是否矛盾 | 这条数据合法吗 |

三者可以共存：RDFS/OWL 定义语义，SHACL 定义完整性要求。不要在 OWL 里硬造校验，也不要用 SHACL 表达推理规则。

## 过度公理化的坑

- **用 `allValuesFrom` 当必填**：属性不出现时约束自动成立，永远校验不出缺失。
- **用不相交公理做业务规则**：一旦数据有噪声，整个本体被判定不一致，难以定位。
- **把唯一性寄托在 `InverseFunctionalProperty`**：OWL 推的是“相等”，不是“重复数据报错”。
- **属性链、传递属性滥用**：推理闭包膨胀，查询变慢。
- **用 `sameAs` 做 ETL 对齐**：合并后难以撤销，应由受控的实体链接流程负责。

建议：公理只保留真正有推理价值的少数几条，其余约束交给 SHACL；每次变更后跑推理器做一致性检查，并保留解释（explanation）；用样例实例回归测试验证推理结果。进一步的质量方法见 [质量评估与反模式](/modeling/method/quality)。
