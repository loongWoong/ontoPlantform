# SKOS 词表与分类法

SKOS（Simple Knowledge Organization System）是 W3C 于 2009 年发布的推荐标准，用来在 RDF 上表达受控词表、分类法、叙词表和术语表。它和 OWL 长得像（都用 RDF 三元组、都定义类与属性），但目标完全不同：**SKOS 关心的是一组概念如何被命名、组织和互相映射，而不是概念之间的逻辑蕴含**。

如果你的问题只是“把术语统一起来、给它们分类、支持多语言与同义词”，用 SKOS 比 OWL 更轻、更合适。

## 三件容易混淆的东西

- **受控词表（controlled vocabulary）**：一组经过批准的术语，比如订单状态、产品类别、风险等级。
- **分类法（taxonomy）**：带层次结构的词表，每个条目通常只有一个父节点，比如商品类目树。
- **叙词表（thesaurus）**：更正式的词表，含同义词、相关词、上下位关系与范围注释，常用于图书馆与知识组织。

SKOS 对三者都给出一套统一建模方式，区别只在于你用了多少特性。

## SKOS 核心词汇

**概念体系与概念**：`skos:ConceptScheme` 是词表容器，`skos:Concept` 是词表里的一个条目，用 `skos:inScheme` 归属，用 `skos:topConceptOf` / `skos:hasTopConcept` 标记顶层。

**标签**（都带语言标签）：

| 属性 | 用途 | 约束 |
|---|---|---|
| `skos:prefLabel` | 首选标签 | 同一语言下每个概念至多一个 |
| `skos:altLabel` | 同义词、缩写、别名 | 可多个，用于检索扩展 |
| `skos:hiddenLabel` | 拼写变体、错拼 | 不展示，只用于匹配 |

**语义关系**：

- `skos:broader` / `skos:narrower`：上下位关系，二者互为逆关系，`broader` 具有传递性；
- `skos:related`：相关但不构成层次的关系，是对称的。

**其他常用属性**：`skos:notation`（编码，如 `"PAID"`）、`skos:definition`（定义）、`skos:scopeNote`（使用范围说明）、`skos:example`、`skos:changeNote`；集合用 `skos:Collection`、有序集合用 `skos:OrderedCollection`。

```turtle
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix ex:   <https://example.com/vocab/order-status#> .

ex:StatusScheme a skos:ConceptScheme ;
    skos:prefLabel "订单状态词表"@zh ;
    skos:hasTopConcept ex:Status .

ex:Status a skos:Concept ;
    skos:prefLabel "订单状态"@zh ;
    skos:topConceptOf ex:StatusScheme .

ex:Paid a skos:Concept ;
    skos:inScheme ex:StatusScheme ;
    skos:prefLabel "已支付"@zh ;
    skos:altLabel "付款完成"@zh , "paid"@en ;
    skos:notation "PAID" ;
    skos:broader ex:Status ;
    skos:definition "订单款项已收到，等待发货。"@zh .

ex:Shipped a skos:Concept ;
    skos:inScheme ex:StatusScheme ;
    skos:prefLabel "已发货"@zh ;
    skos:notation "SHIPPED" ;
    skos:broader ex:Status ;
    skos:related ex:Paid .
```

**映射关系**用于跨词表对齐：`skos:exactMatch`（可互换）、`skos:closeMatch`（近似）、`skos:broadMatch` / `skos:narrowMatch`（宽/窄于对方）、`skos:relatedMatch`。它们是对称或可逆的，但都只是“词表层面”的断言，不能推出实例层面的等价。

## SKOS 不是 OWL 的子集替代品

这是最常见的误解。SKOS 本身是用 OWL 定义的词汇表，但它的语义很轻：`skos:Concept` 的实例之间只有 `broader`、`related` 这些关系，**`skos:broader` 不是 `rdfs:subClassOf`**。

差别在于：

- OWL 的 `subClassOf` 作用在**类**上，会影响实例的类型推断；
- SKOS 的 `broader` 作用在**概念个体**上，只表达“这两个词条有上下位关系”；
- 因此不能通过 `ex:Paid skos:broader ex:Status` 推出“任何已支付订单都是订单状态”，也不能把 `skos:Concept` 当业务实体的类型。

SKOS 里唯一的“推理”是少量内建语义：`broader` 传递、`broader`/`narrower` 互逆、`related` 对称、映射关系对称等。它不会替你做类分类，也不会检查一致性。

## 适用场景与反例

**适合用 SKOS 的**：

- 企业术语表、数据字典的分类层、指标口径词表；
- 商品类目、行业代码、地区代码等受控取值；
- 多语言标签与同义词检索扩展；
- 跨标准词表的映射（如内部状态码与行业标准码）。

**不适合用 SKOS 的**：

- 需要表达“订单必须由客户下达”这类结构约束；
- 需要按属性关系做推理（如组织汇报链、BOM 展开）；
- 需要类型层次与实例类型推断；
- 需要数据校验（那是 [SHACL](/spec/shacl) 的职责）。

判断方法很简单：如果你主要在管理“词”和“词的归属”，用 SKOS；如果开始描述“实体有哪些属性、彼此什么关系”，就该上 [OWL](/spec/owl)。

## 与本体配合的三种方式

1. **词表作为本体的标签体系**：业务类用 OWL 定义，属性值用 SKOS 概念表示；对需要多语言、同义词的枚举，用 `skos:Concept` 个体比裸字符串更可控。
2. **用注释属性建立关联**：在概念和类之间加自定义注释属性（例如 `ex:representsClass`），或用 `rdfs:seeAlso` 指向类。不要用 `skos:exactMatch` 直接连“概念”和“类”，那会混淆两个层次。
3. **值划分（value partition）**：把 SKOS 词表当作枚举值的来源，实例通过对象属性指向 `skos:Concept` 个体，而不是用字符串存状态码。这样状态的含义、同义词、多语言标签都能复用。

## 常见坑

- **把 `broader` 当 `subClassOf`**：模型看起来对，推理结果全错；这是 SKOS 与 OWL 混用时的头号错误。
- **同一语言多个 `prefLabel`**：SKOS 要求每种语言至多一个首选标签，多了会导致展示与检索歧义。
- **用标签当标识**：概念必须有稳定 IRI；靠 `prefLabel` 做匹配，改名就断链。
- **`altLabel` 乱塞**：同义词、错拼、旧名要区分 `altLabel` 与 `hiddenLabel`，否则检索噪音大。
- **映射关系用得太重**：`exactMatch` 表示可互换，跨系统对齐时优先 `closeMatch`，把审核结论留在流程里而不是模型里。
- **缺少 `skos:definition` 与 `scopeNote`**：词表最终要给人用，没有定义和适用范围说明，词表会退化成一张标签表。

## 小结

SKOS 的定位是“把词组织好”，不是“把世界建模好”。它是本体项目里极好的第一步：成本低、业务方能参与、能快速统一术语；等到需要结构和推理时，再让 OWL 接管语义层，SKOS 继续负责标签与词表治理。两者分工清楚，比强行用一套语言解决所有问题更可持续。
