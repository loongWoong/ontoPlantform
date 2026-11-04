# 约束与业务规则

本体只保证“说得通”，不保证“数据齐”。约束要解决的问题是：数据是否完整、是否符合业务规则、是否在发布前被拦截。这件事不能交给 OWL 公理，而应该交给 SHACL、应用层和数据库，各自分担不同层次的规则。

## 三层分工

| 维度 | OWL 公理 | SHACL 约束 | 应用层/规则引擎 |
|---|---|---|---|
| 语义 | 开放世界 | 封闭世界 | 命令执行时 |
| 目的 | 逻辑一致性 | 数据完整性与合规 | 流程、权限、跨系统 |
| 失败表现 | 推理器报不一致 | 校验报告列出违规节点 | 抛出异常、拒绝请求 |
| 典型规则 | 状态至多一个、类层次 | 必填、范围、格式、条件 | 审批、扣减库存、调用支付 |
| 执行时机 | 加载/推理 | 入库、发布、巡检 | 每次业务操作 |

判断口诀：**逻辑上不可能 → OWL；数据上不允许 → SHACL；流程上不允许 → 应用层**。把三者混在一起，最常见的结果是规则既没在入口拦住，也无法向业务解释。把三层各自的规则列成清单，比争论“该用 OWL 还是 SHACL”更有价值。

## 规则分类与实现手段

| 规则类型 | 例子 | 首选实现 |
|---|---|---|
| 必填 | 订单必须有下单客户 | `sh:minCount 1` |
| 唯一 | 工号不重复 | SHACL-SPARQL 或数据库唯一索引 |
| 取值范围 | 金额 ≥ 0 | `sh:minInclusive` |
| 数据类型 | 日期必须是 dateTime | `sh:datatype` |
| 格式 | 订单号形如 ORD-20250301 | `sh:pattern` |
| 枚举 | 状态只能取四个值之一 | `sh:in` |
| 长度 | 备注不超过 500 字 | `sh:minLength` / `sh:maxLength` |
| 跨字段 | 折后价不高于原价 | `sh:lessThan` 或 SHACL-SPARQL |
| 条件 | 已支付必须有支付时间 | SHACL-SPARQL 或 `sh:or` |
| 引用完整性 | 订单行必须指向存在的商品 | `sh:class` / `sh:node` |
| 时序 | 发货时间晚于支付时间 | 应用层或查询巡检 |

SHACL 擅长结构、取值和局部条件；跨记录、跨时间窗口、涉及外部系统的规则，放在应用层更实际。

## 约束放在哪：三个执行点

1. **数据入口**：ETL 写入、API 提交、主数据发布时先校验，问题数据不落库。成本最低、收益最大。
2. **发布前**：本体新版本、类目、参考数据发布前跑全量校验，避免“模式升级、数据不合规”。
3. **定期巡检**：对历史数据做周期性扫描，发现存量问题并进入修复队列。

约束本身是声明式的，必须有人执行：离线用 pyshacl/Jena 的 SHACL 校验，在线用服务层封装。执行点与架构位置见[分层与六边形架构](/impl/arch-pattern/layered)。

## 严重级别与错误消息

SHACL 允许给形状和约束设置严重级别与消息，这直接决定运维体验：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .

shop:OrderShape a sh:NodeShape ;
  sh:targetClass shop:Order ;
  sh:severity sh:Violation ;
  sh:property [
    sh:path shop:orderNumber ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
    sh:pattern "^ORD-[0-9]{8}$" ;
    sh:name "订单号" ;
    sh:message "订单号缺失、重复或格式不符（应形如 ORD-20250301）" ;
  ] .
```

`sh:Violation` 表示必须修复，`sh:Warning` 表示可疑但不阻塞，`sh:Info` 仅提示。消息用业务语言写，不要写“constraint violation”；否则业务方看不懂报告，规则就失去了治理价值。

## 完整示例：订单校验

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

shop:OrderShape a sh:NodeShape ;
  sh:targetClass shop:Order ;
  sh:property [
    sh:path shop:placedBy ;
    sh:class shop:Customer ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
  ] ;
  sh:property [
    sh:path shop:totalAmount ;
    sh:datatype xsd:decimal ;
    sh:minInclusive 0 ;
  ] ;
  sh:property [
    sh:path shop:hasLine ;
    sh:minCount 1 ;
    sh:node shop:OrderLineShape ;
  ] ;
  sh:property [
    sh:path shop:orderStatus ;
    sh:maxCount 1 ;
    sh:in ( shop:StatusDraft shop:StatusPaid
            shop:StatusShipped shop:StatusCancelled ) ;
  ] .

shop:OrderLineShape a sh:NodeShape ;
  sh:targetClass shop:OrderLine ;
  sh:property [
    sh:path shop:quantity ;
    sh:datatype xsd:integer ;
    sh:minInclusive 1 ;
  ] ;
  sh:property [
    sh:path shop:lineProduct ;
    sh:minCount 1 ;
    sh:class shop:Product ;
  ] .
```

条件规则用 SHACL-SPARQL 表达更直接：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .

shop:PaidOrderRule a sh:NodeShape ;
  sh:targetClass shop:Order ;
  sh:sparql [
    a sh:SPARQLConstraint ;
    sh:severity sh:Violation ;
    sh:message "已支付订单必须有支付时间" ;
    sh:select """
      PREFIX shop: <https://example.com/shop/ontology#>
      SELECT $this ?status WHERE {
        $this shop:orderStatus ?status .
        FILTER (?status = shop:StatusPaid)
        FILTER NOT EXISTS { $this shop:paidAt ?t }
      }
    """ ;
  ] .
```

`$this` 是 SHACL 注入的焦点节点变量。SPARQL 约束灵活但可读性差，能用原生约束组合表达时优先用原生约束。

## 与 DDD 聚合不变量配合

DDD 的不变量在聚合根内由代码保证，事务提交即生效；SHACL 校验的是数据层的“发布口径”。两者不是替代关系：

- **聚合内不变量**（订单总额等于行金额之和）必须在代码里强一致地维护，不能依赖事后校验。
- **聚合间规则**（客户信用额度、库存可用量）通常用最终一致或校验兜底。
- **跨系统共享的合规规则**（字段格式、受控取值）用 SHACL 统一表达，所有数据源共用一套规则。

避免把同一条规则在代码、数据库、SHACL 里各写一遍且口径不同。做法是维护一份规则清单，标注权威实现位置，其余位置只做防御性检查。

## 从业务规则到 SHACL 的翻译步骤

业务方给出的往往是自然语言规则，直接写形状容易漏项。推荐按四步翻译：

1. **拆对象**：规则约束的是哪类节点？确定 `sh:targetClass` 或 `sh:targetNode`。
2. **定属性**：规则检查哪个属性路径？必要时用 `sh:path` 的多段路径或逆路径。
3. **选组件**：取值类规则优先用原生约束（`sh:in`、`sh:pattern`），跨字段与条件规则用 `sh:sparql`。
4. **配消息与级别**：写业务语言消息、设严重级别，并定义违规后的处理动作（拒收、告警、进修复队列）。

翻译完用样例数据回归：至少一条合规数据、一条违规数据，确保形状真的会报错，而不是永远通过。

## 规则不是写完就结束

规则会随业务变化，必须有版本与测试：每条规则有编号、负责人和生效时间；修改规则前先在历史数据上跑一遍，评估影响面；废弃规则要保留记录并说明原因。规则清单本身也要有检索入口，方便业务方按“订单”“商品”找到对应规则。没有治理的规则集合，半年后会比没有规则更难维护。

## 失败模式

1. **把 SHACL 当推理**：以为校验能补出缺失的客户，实际上 SHACL 只报告问题。
2. **只在批处理跑校验**：入口不拦，脏数据先落库，越积越多。
3. **规则写死在代码里**：业务方看不见、改不动，也没有统一报告。
4. **一个形状塞几十条约束**：报告无法定位，修复责任不清。
5. **不分严重级别**：全设成 Violation，告警疲劳后没人再看。
6. **条件规则硬塞进 SHACL**：跨实体、跨时间的复杂规则应上移到应用层或规则引擎。
7. **规则文档与实现脱节**：文档写“金额必须大于 0”，形状里却只校验了类型，上线后才发现。

规则与应用的完整落地方式见[业务规则与校验](/application/rules-validation)；SHACL 语法细节见[SHACL 形状约束](/spec/shacl)。
