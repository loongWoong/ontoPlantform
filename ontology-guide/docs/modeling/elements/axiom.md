# 公理

公理是本体里“能推出新知识的断言”。它和普通数据断言的区别不在写法，而在后果：普通断言只描述事实，公理会参与推理、改变类型归属、产生隐含关系，甚至让整个模型变得不一致。写得少而准，是公理设计的第一原则。

## 公理与普通断言

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix data: <https://example.com/shop/data/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# 普通断言：客户 C-1001 下了订单
data:order-1 shop:placedBy data:customer-1001 .

# 公理：所有订单都必须有一个下单客户
shop:Order rdfs:subClassOf [
  a owl:Restriction ;
  owl:onProperty shop:placedBy ;
  owl:someValuesFrom shop:Customer
] .
```

前者描述一次具体事实，后者描述一类对象的逻辑规律。推理器加载两者后，会推出 `data:customer-1001` 是 `shop:Customer`（借着 domain/range），也可能为缺失的订单推出匿名客户。**推理不校验数据**，这一点必须反复强调。

## 常用公理清单

| 公理 | 作用 | 推理效果 | 主要风险 |
|---|---|---|---|
| `rdfs:subClassOf` | 分类层级 | 推出子类个体也属于父类 | 层次过深、误用 is-a |
| `owl:equivalentClass` | 类等价 | 双向分类，推出大量新类型 | 意外归类、推理变慢 |
| `owl:disjointWith` / `owl:AllDisjointClasses` | 类互斥 | 同时属于两类即不一致 | 数据脏时整库不可用 |
| `owl:equivalentProperty` | 属性等价 | 两边断言合并 | 映射错误污染图 |
| `owl:inverseOf` | 逆关系 | 推出反向边 | 语义不真正互逆 |
| 属性特征 | 传递/对称/函数/反函数等 | 推出新关系或唯一值 | 函数属性被当必填 |
| `rdfs:domain/range` | 属性适用类型 | 推断主语/宾语类型 | 被误当校验 |
| `owl:hasKey` | 关键值标识个体 | 相同 key 推出 `sameAs` | 被误当唯一约束 |
| `owl:propertyChainAxiom` | 属性链 | 组合路径推出新关系 | 链过深、循环 |
| `sh:…`（非 OWL） | 校验 | 不推理，只产出报告 | 与公理混为一谈 |
| 基数限制 | 数量约束 | 一致性检查 | 开放世界下不报缺数据 |

## 公理强度阶梯

同一个业务事实往往有多种表达，强度从弱到强：

1. **注释说明**：`rdfs:comment "订单必须有客户"`——零推理成本，只给人看。
2. **弱公理**：`rdfs:subClassOf`、`rdfs:domain/range`——帮助分类与推断。
3. **中强公理**：不相交、函数属性、`hasKey`——引入不一致风险。
4. **强公理**：等价类、枚举类、完整基数限制——改变分类结果，推理代价高。

从弱往强选。只有当某条公理能直接支撑一条能力问题（CQ）时才加它，否则写注释就够了。等价类尤其要谨慎：`owl:equivalentClass` 会让推理器按定义重新归类所有个体，一个写宽的定义能把半个图卷进来。

## 和 SHACL 的分工

| 维度 | OWL 公理 | SHACL 约束 |
|---|---|---|
| 目的 | 保证逻辑一致性 | 保证数据完整性/合规 |
| 语义 | 开放世界，缺失不等于违反 | 封闭世界，缺失即违反 |
| 失败表现 | 推理器报 inconsistent，整个图不可用 | 校验报告列出具体违规节点 |
| 典型用途 | 状态至多一个、类层次、属性特征 | 必填、范围、格式、条件规则 |

判断口诀：**“逻辑上不可能”用公理，“数据上不允许”用 SHACL**。例如“一个订单至多只能有一个状态”适合公理；而“订单必须有订单号”适合 SHACL——因为缺号只是数据不完整，不是逻辑矛盾。

## 三个典型例子

**状态至多一个**：状态在本指南里建成个体，用函数属性限制“至多一个值”，再用 `owl:AllDifferent` 声明四个状态互不相同：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .

# 一个订单至多有一个状态
shop:orderStatus a owl:ObjectProperty , owl:FunctionalProperty .

# 四个状态个体互不相同
[] a owl:AllDifferent ;
  owl:members ( shop:StatusDraft shop:StatusPaid
                shop:StatusShipped shop:StatusCancelled ) .
```

两条公理合起来才有效果：函数属性给出“至多一个状态”，`AllDifferent` 给出“四个状态互不相同”，于是同一订单若同时断言 `shop:StatusPaid` 与 `shop:StatusCancelled`，推理器会判定不一致。只写函数属性而不声明 `AllDifferent`，开放世界会把两个状态推成 `sameAs` 而不是报错；只写 `owl:AllDisjointClasses` 也不行，它的 `owl:members` 只接受类，而这里的状态是个体。这套组合的价值在于尽早暴露脏数据，代价是“一处脏、全库挂”，所以生产环境通常配合数据修复流程使用。

**员工工号唯一**：`owl:hasKey` 表达“工号相同的个体是同一员工”：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .

shop:Employee owl:hasKey ( shop:employeeNumber ) .
```

注意它不是数据库的唯一约束：两个员工都填 `E-001` 时，推理器推出他们是同一个人，而不是报错。要拒绝重复，用 SHACL 或数据库约束。这也解释了为什么“用 hasKey 做唯一性校验”是个常见坑。

**订单必须包含行**：`someValuesFrom` 表达“至少存在一个订单行”：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

shop:Order rdfs:subClassOf [
  a owl:Restriction ;
  owl:onProperty shop:hasLine ;
  owl:someValuesFrom shop:OrderLine
] .
```

在开放世界里，一个没有任何行断言的订单仍然可以“存在某个未记录的行”，所以不会报错。要做“发布前必须至少一行”的检查，仍然需要 `sh:minCount 1`。

## 基数限制该不该用

`owl:minCardinality`、`owl:maxCardinality`、`owl:exactCardinality` 可以挂在类或属性上，但它们在开放世界下只参与一致性检查：缺少数据不会被判违规，只有“断言了多个值、又声明最多一个”时才触发不一致。因此基数限制适合表达“逻辑上不允许”，例如“一个人最多有一个出生日期”；“录入时必须填出生日期”仍应写成 `sh:minCount 1`。

基数写得越精确，推理代价越高，对数据完整度的要求也越强，通常只在核心概念上使用。多数情况下，用 `sh:minCount`/`sh:maxCount` 表达业务要求、用少量 `owl:maxCardinality` 表达逻辑不可能，是更稳妥的组合。

## 过度公理化的代价

- **推理变慢**：等价类、属性链、大基数限制显著增加推理时间，尤其是全量物化时。
- **物化膨胀**：每个推理结果都要存储，图规模可能数倍增长，见[推理与物化](/impl/arch-pattern/inference)。
- **难以解释**：业务方看到“客户变成了订单”这类推断结果时，信任度会迅速下降。
- **维护困难**：一条公理改动可能影响全图分类，回归测试成本高。

选择推理 profile（EL/QL/RL）并评估推理器能力，是控制成本的工程手段。多数业务场景用 RDFS 加少量 OWL 2 EL 公理就够了。

## 公理评审清单

- 这条公理对应哪条 CQ 或哪个明确的业务事实？
- 能否用 SHACL 校验代替？如果只是“数据要合规”，答案通常是能。
- 引入后，不一致的风险是什么？数据脏时如何定位与修复？
- 是否使用了等价类？定义是否窄到只覆盖目标类？
- 推理器在目标 profile 下能否处理？预计物化规模多大？
- 公理是否有注释和变更记录，半年后还能解释清楚吗？
- 新增公理后推理耗时与物化规模是否仍在可接受范围？记录基线，避免逐步劣化。

公理定完后，把它与校验规则一起组织：见[约束与业务规则](/modeling/elements/constraint)。推理引擎与 profile 的工程细节见[OWL 2 本体语言](/spec/owl)。
