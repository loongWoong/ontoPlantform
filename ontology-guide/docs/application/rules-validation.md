# 业务规则与校验

业务规则散落在代码、存储过程、Excel 和人的记忆里，是系统最脆弱的部分。本页讲如何把规则拆成三类载体——**OWL 一致性、SHACL 校验、规则引擎**——各自承担什么、怎么治理，以及和 DDD 聚合不变量如何配合。

## 先分清三类规则载体

| 载体 | 语义 | 适合的规则 | 结果 |
|---|---|---|---|
| OWL 公理 | 开放世界，声明必然为真 | 类不相交、基数、属性链、key | 推理出一致性冲突，或推出隐含事实 |
| SHACL | 封闭世界，校验数据是否满足 | 必填、类型、范围、格式、跨字段、计数 | 输出违规报告，指出哪个节点哪个属性 |
| 规则引擎 | 任意前件后件，过程化 | 需要执行动作、跨系统调用、复杂计算 | 触发动作、变更状态 |

**最常见的错误是把 `rdfs:domain` / `rdfs:range` 当校验规则**。它们只产生类型推断：写 `ex:orderDate rdfs:range xsd:dateTime`，数据里放字符串"2024-13-01"不会报错，反而会推出“该值是 xsd:dateTime”的结论。要校验就用 SHACL。

另一条原则：**能用 SHACL 就不用规则引擎**。SHACL 声明式、可测试、可版本化；规则引擎强在“做什么”，但规则一多就变成不可维护的逻辑泥潭。

## 规则分层：从数据入口到业务动作

按执行时机分三层，避免所有规则堆在一个点：

```text
入口层（写入/导入）
  → SHACL：结构、类型、必填、格式  ← 快速失败，成本最低
    → 发布层（数据合并/主数据发布）
      → SHACL-SPARQL + SPARQL 计数：跨实体一致性、唯一性
        → 决策层（业务流程）
          → 规则引擎/领域服务：需要动作、跨系统、时效判断的规则
```

入口层规则要严，发布层规则要全，决策层规则要少。决策层规则越少，越容易测试和解释。

## SHACL 示例：订单校验

```turtle
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix ex:   <https://example.com/onto#> .

ex:OrderShape a sh:NodeShape ;
    sh:targetClass ex:Order ;
    sh:property [
        sh:path ex:orderNumber ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:datatype xsd:string ;
        sh:pattern "^ORD-[0-9]{8}$" ;
        sh:message "订单号必填且格式为 ORD-8 位数字"@zh
    ] ;
    sh:property [
        sh:path ex:hasCustomer ;
        sh:minCount 1 ;
        sh:class ex:Customer ;
        sh:message "订单必须关联客户"@zh
    ] ;
    sh:property [
        sh:path ex:orderTotal ;
        sh:minCount 1 ;
        sh:datatype xsd:decimal ;
        sh:minInclusive 0 ;
        sh:message "订单金额必填且不小于 0"@zh
    ] ;
    sh:property [
        sh:path ex:hasOrderLine ;
        sh:minCount 1 ;
        sh:node ex:OrderLineShape ;
        sh:message "订单至少一条明细"@zh
    ] .
```

跨实体规则（明细金额求和等于订单总额）超出基本约束，用 SPARQL 约束表达：

```turtle
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex:  <https://example.com/onto#> .

# SHACL-SPARQL 中的前缀需显式声明
ex: a owl:Ontology ;
    sh:declare [
        sh:prefix "ex" ;
        sh:namespace "https://example.com/onto#"^^xsd:anyURI
    ] .

ex:OrderTotalShape a sh:NodeShape ;
    sh:targetClass ex:Order ;
    sh:sparql [
        sh:message "订单总额必须等于明细金额之和"@zh ;
        sh:severity sh:Violation ;
        sh:prefixes ex: ;
        sh:select """
            SELECT $this (ex:orderTotal AS ?path) (?total AS ?value)
            WHERE {
              $this ex:orderTotal ?declared .
              { SELECT $this (SUM(?amount) AS ?total) WHERE {
                  $this ex:hasOrderLine/ex:lineAmount ?amount .
                } GROUP BY $this }
              FILTER(ABS(?declared - ?total) > 0.001)
            }
        """
    ] .
```

注意浮点容差：金额比较不要用等号。

## 规则分类清单

建模时逐类核对，避免遗漏：

- **结构规则**：必填、唯一、类型、格式、基数；
- **取值规则**：范围、枚举、单位、精度、编码规范（如统一社会信用代码校验位）；
- **跨实体规则**：引用完整性、金额合计、状态机前置条件、层级深度限制；
- **时序规则**：生效日期不重叠、结束时间晚于开始、状态变更顺序、有效期覆盖；
- **统计规则**：比例阈值、异常波动，一般不放 SHACL，交给监控平台。

时序规则容易被忽略。用时间区间建模（`ex:validFrom` / `ex:validTo`）配合 SPARQL 重叠检测，比在代码里写一堆日期比较可靠。

## 行业例子

### 金融合规

典型规则：客户风险等级与产品风险等级匹配、投资冷静期、关联交易识别、披露文件完整性。做法上，把监管条款拆成“适用对象 + 条件 + 结论 + 依据条款号”，用 SHACL 或规则表实现，并保留条款号用于审计追溯。关键是**规则可追溯到监管原文**，否则监管检查时无法说明依据。

### 制造 BOM

典型规则：物料替代关系的有效性、BOM 层级与用量校验、工程变更（ECN）影响范围、同一装配件下位物料版本一致性。BOM 天然是图结构，用本体表达“装配-部件”的传递关系与版本约束，比关系表递归查询更直观。注意：**部分-整体关系要区分“组成”与“分类”**，混用会导致层级校验误判，参见 [本体设计模式](/modeling/patterns)。

## 规则治理

规则是资产，需要和代码一样管理：

1. **规则有唯一 ID、版本、责任人、生效日期、失效日期**；
2. **变更走审批**，影响范围要评估（哪些数据集、哪些下游）；
3. **每条规则有测试用例**：至少一个通过样例、一个违规样例，进 CI；
4. **例外要显式**：业务允许的例外用白名单记录（含原因、期限、批准人），不要靠关闭规则解决；
5. **违规报告可操作**：包含节点 IRI、属性、实际值、期望值、规则 ID、严重级别；
6. **定期回归**：数据量变化、模型升级后重跑全量校验。

严重级别分三档：`sh:Violation` 阻断、`sh:Warning` 记录、`sh:Info` 提示。全部设成 Violation 的结果是报告没人看。

## 与 DDD 聚合不变量配合

边界要划清楚：

- **聚合内部不变量**（如订单明细数量上限、状态迁移合法性）由聚合根在事务内保证，不要靠事后 SHACL 检查；
- **跨聚合/跨系统的规则**（订单金额与发票金额一致、客户信用额度）不适合放在单个聚合里，用 SHACL 或独立校验服务；
- **规则本体中的约束是“数据契约”**，聚合是“行为契约”，两者互补。

实现上，领域对象在持久化前可以调用语义校验服务（端口-适配器，见 [分层与六边形架构](/impl/arch-pattern/layered)），但不要让业务事务依赖外部推理器的可用性。校验失败的策略要明确：阻断、降级记录、还是异步告警。

## 失败模式

1. **规则写在三个地方**：数据库约束、代码 if-else、SHACL 各一套，彼此矛盾。
2. **OWL 公理当校验**：期待推理器报错，结果只推出类型，脏数据照样入库。
3. **规则无版本**：业务说“这条规则早改了”，却查不到历史。
4. **校验太晚**：只在月末跑一次，问题数据已经流向下游。
5. **违规不可定位**：报告只写“有 372 条违规”，没有节点和属性，无法修复。
6. **例外靠关规则**：关掉一次就再也开不回来。
7. **规则过度形式化**：简单格式校验也写成 SPARQL，维护成本高于收益。能用 `sh:pattern` 就不要写查询。
