# 概念与类

类是本体里最基础的元素，代表“一组可能成员的集合”。建类就是做分类：把领域里稳定、可命名、需要被共享引用的类型提取出来。类建错了，后面的属性、公理、约束都会跟着错，所以这是最值得慢下来的一步。

## 类的语义：集合与分类器

在 OWL 里，类不是“表”也不是“代码里的 class”，而是一个集合。个体通过类型断言成为类的成员：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

shop:Order a owl:Class ;
  rdfs:label "订单"@zh , "Order"@en ;
  rdfs:comment "客户提交的购买请求，一个订单至少包含一个订单行。"@zh ;
  rdfs:subClassOf shop:BusinessDocument .

shop:BusinessDocument a owl:Class ;
  rdfs:label "业务单据"@zh .
```

记住两个后果：其一，`shop:Order` 的成员由类型断言和推理共同决定，不靠“字段列表”；其二，OWL 默认开放世界，推理器不会因为“没见到某个个体”就断定它不属于某个类。这决定了类不能用来表达“必须是这些值”的封闭约束，那属于 SHACL 的职责。

## 命名与注释

命名规范直接影响本体的可维护性，建议至少遵守四条：

- **IRI 稳定**：类 IRI 一旦发布就不要改，改名用 `owl:deprecated` 过渡，见[版本与演进](/impl/arch-pattern/versioning)。
- **局部名用英文单数 CamelCase**：`shop:Order`、`shop:OrderLine`，不要用中文或复数。
- **中文靠语言标签**：`rdfs:label "订单"@zh`，不要把它塞进 IRI。
- **写清边界**：`rdfs:comment` 一句话说明“这个类包含什么、不包含什么”，比长篇定义有用。

## 分类层次与 subClassOf

`rdfs:subClassOf` 表达子集关系，并且是传递的：`shop:RetailOrder rdfs:subClassOf shop:Order`，`shop:Order rdfs:subClassOf shop:BusinessDocument`，则零售订单也是业务单据。这会影响查询（用属性路径可以一次取到所有子类）和推理（引入新的类型断言）。

判断一条 `is-a` 是否成立，用最简单的测试：**“每一个 A 都是 B 吗？”** 零售订单是订单，成立；订单是客户吗？不成立。常见误用是把角色或状态建成子类，例如把“已支付订单”建成 `shop:Order` 的子类——这会让同一个订单在不同时间属于不同类，分类语义被破坏。状态应建成个体加属性，或用角色模式处理，见[本体设计模式](/modeling/patterns)。

多继承在 OWL 里是允许的，也别害怕用；但层次过深（超过 4–5 层）通常说明划分维度混乱，不如拆成多个扁平维度。层次是否“自然”可以借助 OntoClean 的刚性、身份、统一性等元属性评估，见[质量评估与反模式](/modeling/method/quality)。

## 类、个体、属性值、角色怎么选

这是建模时问得最多的问题。一个实用判定流程：

1. **它是稳定类型，还是具体对象？** 类型建类（`shop:Customer`），具体对象建个体（`data:customer-1001`）。
2. **它是固定的小集合，且需要被其他对象引用吗？** 是则建成“类 + 个体”的受控取值，例如订单状态、货币、计量单位。只在文本里出现、不需要被引用的取值，用数据属性即可。
3. **它的身份是否随情境变化？** 同一个人在不同场景下是买家、卖家、客服，这是角色而非类型，适合用角色模式或“参与者 + 角色类型”结构。
4. **它是否只是某个类的取值集合？** 可以用 `owl:oneOf` 或 SKOS 受控词表表达，不必建完整类层次。

订单状态就是一个典型例子：它需要被比较、被校验、被查询，所以建成个体而不是字符串：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

shop:OrderStatus a owl:Class ;
  rdfs:label "订单状态"@zh .

shop:StatusDraft     a owl:NamedIndividual , shop:OrderStatus .
shop:StatusPaid      a owl:NamedIndividual , shop:OrderStatus .
shop:StatusShipped   a owl:NamedIndividual , shop:OrderStatus .
shop:StatusCancelled a owl:NamedIndividual , shop:OrderStatus .
```

如果四个状态必须互不相同，用 `owl:AllDifferent` 声明；如果订单只能取其中一个，用函数属性（`owl:FunctionalProperty`）或 `sh:maxCount 1` 限制“至多一个”；如果状态必须来自这四项，用 SHACL 的 `sh:in` 做封闭校验。注意“个体互不相同”与“枚举类（`owl:oneOf`）”是不同层面的语义：前者约束个体，后者定义类，详见[公理](/modeling/elements/axiom)。

## 类划分的四条原则

- **按本质属性划分**：按“它是什么”而不是“它当前处于什么状态”“谁在使用它”划分。
- **避免交叉分类**：同一个层次里不要同时按来源和支付方式分，否则组合爆炸，新增维度就要改模型。
- **同义词不建类**：“客户”“顾客”“买方”指向同一概念时用一个类，其余写 `skos:altLabel` 或 `rdfs:label` 的多语言/别名，不要建三个类再对齐。
- **不留兜底类**：`OtherThing`、`Misc` 一类会把分类问题藏起来，宁可暂时不分类。

## 元类与 punning

元类是“类的类”，例如把“产品类目”本身当作可操作对象。OWL 2 允许 punning：同一个 IRI 既可以出现在类的位置，也可以出现在个体的位置，两者的解释互不干扰。它能解决“类目既是分类维度又是业务对象”的表达问题，但会让模型对非专业读者变难。

多数情况下更简单的做法是二选一：要么类目作为类层次（`shop:ElectronicsProduct rdfs:subClassOf shop:Product`），要么类目作为个体并用 `skos:broader` 组织层级。只有确实需要同时具备两种身份时才用 punning，并在注释里写清意图。

## 常见错误与修复

| 错误 | 症状 | 修复 |
|---|---|---|
| 把具体对象建成类 | `shop:Shanghai` 是类，查询要按城市枚举 | 改为个体，或建“城市”类并用个体表示 |
| 同义词各建一类 | 客户/顾客/买方三个类，数据分散 | 合并为一类，别名写注释 |
| 状态建成子类 | 订单随状态改变类型，推理结果混乱 | 状态建个体，用属性关联 |
| 层次过深 | 五六层 is-a，没人能说清区别 | 拆成多个扁平维度 |
| 类没有注释 | 新人无法判断该放哪个类 | 每类至少一句 `rdfs:comment` |

## 两个常见疑问

**类必须有实例吗？** 不必。类可以先建、后补数据；空类在开放世界下完全合法。但空类长期存在且无人使用，通常说明模型过度设计。

**子类之间必须互斥吗？** 不必，除非业务确实要求。互斥是公理，一旦声明，违反就会导致推理器判定不一致；如果只是“数据里不该同时出现”，用 SHACL 校验更合适，报错也更友好。

类定好之后，下一步是[对象属性与关系](/modeling/elements/relation)：类只说明“有哪些类型”，关系才把图连起来。
