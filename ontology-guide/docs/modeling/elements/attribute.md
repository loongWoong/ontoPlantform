# 数据属性与取值

数据属性把个体连接到字面量：价格、日期、编号、名称。它看起来像“建个字段”这么简单，但实际上类型选择、语言标签和计量结构决定了数据能否被正确查询、比较和校验。一个常见的坏味道是：模型结构很漂亮，金额是字符串、日期没有时区、单位散落在注释里。

## 三种“属性”先分清

| 类型 | 连接什么 | 是否参与推理 | 典型用途 |
|---|---|---|---|
| 对象属性 | 个体与个体 | 是 | 订单属于客户、员工属于部门 |
| 数据属性 | 个体与字面量 | 是 | 金额、日期、编号 |
| 注释属性 | 个体/类与说明文字 | 否 | `rdfs:label`、`rdfs:comment`、来源出处 |

判断方法：值是另一个对象就用对象属性；值需要被比较、聚合并可用 SHACL 校验，用数据属性；只是给人看的说明，用注释属性。不要把业务数据塞进注释属性——它不会参与一致性推理，语义上也不对。

## 选对 XSD 类型

| 场景 | 推荐类型 | 说明 |
|---|---|---|
| 名称、编号 | `xsd:string` | 编号保留前导零，不要转数字 |
| 数量、件数 | `xsd:integer` / `xsd:decimal` | 计数用整数 |
| 金额、价格 | `xsd:decimal` | 不用 `xsd:double`，避免二进制浮点误差 |
| 比例、评分 | `xsd:decimal` | 需要精度时显式声明 |
| 是否 | `xsd:boolean` | 不要用字符串 `"true"` |
| 日期 | `xsd:date` | 只有日期无时间 |
| 时间点 | `xsd:dateTime` | 带时区偏移，避免长期歧义 |
| 时长 | `xsd:duration` / `xsd:dayTimeDuration` | 用于计算而非展示 |
| URI | `xsd:anyURI` | 链接、外部标识 |

类型选窄不选宽：能确定是整数就不要用 `xsd:decimal`，能确定是日期就不要用字符串。窄类型让校验和查询都更可靠，也能在数据加载时尽早暴露格式错误。

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .

shop:orderNumber a owl:DatatypeProperty ;
  rdfs:domain shop:Order ;
  rdfs:range xsd:string ;
  rdfs:label "订单号"@zh ;
  rdfs:comment "对外可见的订单编号，形如 ORD-20250301，保留前导零。"@zh .

shop:placedAt a owl:DatatypeProperty ;
  rdfs:domain shop:Order ;
  rdfs:range xsd:dateTime ;
  rdfs:label "下单时间"@zh .
```

## 字面量与语言标签

文本值有几种常见写法，注意 RDF 1.1 中 `"foo"` 与 `"foo"^^xsd:string` 是**同一个字面量**，其余写法语义不同：

```turtle
@prefix data: <https://example.com/shop/data/> .
@prefix shop: <https://example.com/shop/ontology#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

data:product-1 shop:productName "无线键盘" .                 # 纯字符串，无语言
data:product-2 shop:productName "无线键盘"@zh .              # 中文语言标签
data:product-3 shop:productName "Wireless Keyboard"@en .     # 英文语言标签
data:product-4 shop:productName "无线键盘"^^xsd:string .     # 显式 xsd:string，与纯字符串等价，一般不必手写
```

需要多语言展示时一定要用语言标签，并用 `skos:prefLabel` 或 `rdfs:label` 区分展示名；`"foo"` 与 `"foo"@zh` 在 RDF 中是不相等的字面量，混用会导致查询漏数据、SHACL 的 `sh:uniqueLang` 也失去意义。

## 数值、货币、计量：不要留裸数字

`shop:price "199"` 的问题不是类型，而是“199 什么单位、什么币种”。两种处理方式，按复杂度取舍：

**扁平双字段**适合单一币种、单一单位系统的小型项目：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

shop:listPrice a owl:DatatypeProperty ;
  rdfs:range xsd:decimal .

shop:priceCurrency a owl:DatatypeProperty ;
  rdfs:range xsd:string .
```

**计量对象（Quantity/Money 模式）**适合多币种、多单位、需要换算或参与规则计算的场景：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix data: <https://example.com/shop/data/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

shop:MonetaryAmount a owl:Class ;
  rdfs:label "金额"@zh .

shop:amountValue a owl:DatatypeProperty ;
  rdfs:domain shop:MonetaryAmount ;
  rdfs:range xsd:decimal .

shop:amountCurrency a owl:ObjectProperty ;
  rdfs:domain shop:MonetaryAmount ;
  rdfs:range shop:Currency .

data:order-total a shop:MonetaryAmount ;
  shop:amountValue "1288.00"^^xsd:decimal ;
  shop:amountCurrency data:CNY .

shop:Currency a owl:Class ;
  rdfs:label "货币"@zh .

data:CNY a shop:Currency .
```

后者多一个中间个体，但换来了可校验（币种必须是受控值）、可换算（汇率规则作用于金额对象）、可扩展（加税、折扣、精度来源）。判断标准很简单：如果未来会有汇率、单位换算或多币种报表，就用计量对象；否则扁平字段更省事。

## 把校验交给 SHACL

数据属性的 `rdfs:range` 只影响推理，不做检查。完整性与取值范围用 SHACL：

```turtle
@prefix shop: <https://example.com/shop/ontology#> .
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

shop:ProductShape a sh:NodeShape ;
  sh:targetClass shop:Product ;
  sh:property [
    sh:path shop:listPrice ;
    sh:datatype xsd:decimal ;
    sh:minInclusive 0 ;
    sh:message "商品标价必须是非负 decimal" ;
  ] ;
  sh:property [
    sh:path shop:priceCurrency ;
    sh:in ( "CNY" "USD" "EUR" ) ;
    sh:message "币种必须是受支持的 ISO 货币" ;
  ] .
```

`sh:datatype` 检查字面量类型，`sh:minInclusive` 检查数值下界，`sh:pattern` 检查格式，`sh:languageIn` / `sh:uniqueLang` 检查语言标签。这些在 OWL 里都没有等价物，不要试图用公理模拟。

## 多值、默认值与更新语义

RDF 里没有“默认值”“空值”和“字段”的概念，只有三元组，这带来四个与关系库不同的约束：

- **多值**：同一主语、同一属性可以出现多个宾语；需要唯一时靠 SHACL 的 `sh:maxCount` 限定。
- **缺失**：不写该三元组就是缺失，不存在 `null`；要区分“不知道”和“没有”，用不同的建模方式（例如用独立状态个体表达“未登记”）。
- **更新**：修改是删除旧三元组、插入新三元组，没有原地赋值，写入方必须保证幂等。
- **默认值**：不要在模型里隐式补默认值，把默认值写进数据或在应用层填充，避免“看起来有、查出来没有”。

## 注释属性与业务数据的边界

`rdfs:label`、`rdfs:comment`、`dcterms:source` 这类注释属性只用于说明，不参与推理和一致性检查。可以用它们记录术语来源、生效时间和负责人，但不要把状态、金额等业务取值放进去——那样既难以被 SHACL 的 `sh:in` 校验，也无法参与聚合查询。

## 与 DDD 值对象的关系

值对象是“没有身份、由属性值定义相等性”的概念，映射到本体通常有两种做法：拆成多个数据属性（简单、查询方便），或建一个类加数据属性（可复用、可约束、可与其他概念关联）。`Money`、`Address`、`DateRange`、`Contact` 这类值对象，建议建成类；`color`、`note` 这类单字段值，直接数据属性。

区别在于：代码里的值对象承载行为（格式化、比较），本体里的值只承载语义。行为留在应用层，不要试图在 RDF 里表达“金额相加”的方法。

## 常见坑

1. **金额用 `xsd:double`**：出现 `199.00000000000003` 这类精度问题，报表对不上。
2. **日期存字符串**：无法做时间区间比较与排序，`FILTER` 里只能做字典序。
3. **单位/币种全局约定**：模型里看不出来，换一个数据源就错。
4. **同一属性多值且无约束**：一个订单挂三个 `totalAmount`，SHACL 没写 `maxCount` 就发现不了。
5. **用空字符串表示“没有值”**：应直接不写该三元组，用 `sh:minCount` 区分“必须填”和“可以不填”。
6. **丢掉时区**：跨时区业务里 `2025-03-01T10:15:00` 与带偏移的时间含义不同。
7. **同一属性混用单位**：有的数据存“厘米”、有的存“米”，没有单位建模就无从发现。

属性与取值定好后，就轮到[个体与实例数据](/modeling/elements/individual)：模式只是蓝图，真正被查询的是个体。
