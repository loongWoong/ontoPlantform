# 运营场景与查询示例

样例装载完成后，平台里有两个世界：

- **设计世界**：本体（4 个对象类型、4 个关系、15 条业务术语），已发布；
- **运行世界**：84 个对象（收费站 6、车道 18、车辆 20、通行交易 40）与 138 条关系边。

下面的命令把 `<wid>` 换成 `python bootstrap.py` 打印的工作空间 id（也可取
`GET /api/workspaces` 中的 id）。

## 场景 1：看哪些收费站当前拥堵

```bash
# 全部收费站
curl "http://127.0.0.1:8100/api/workspaces/<wid>/state/find?type=TollStation"

# 只看拥堵（状态是属性过滤，字段名直接用本体属性名 status）
curl "http://127.0.0.1:8100/api/workspaces/<wid>/state/find?type=TollStation&status=congested"
```

实际返回：`ST001 济南西收费站`、`ST005 德州收费站`。

## 场景 2：看某个收费站下的车道

```bash
curl "http://127.0.0.1:8100/api/workspaces/<wid>/state/related?ref=TollStation/ST001&relation=has_lane"
# -> ["Lane/L001", "Lane/L002", "Lane/L003"]
```

再取一条车道的详情：`GET .../state/get?ref=Lane/L001`。

## 场景 3：查某辆车的通行交易与费用

```bash
# 属性过滤（车牌含中文，curl 里建议先 urlencode；PowerShell 用 [uri]::EscapeDataString）
curl -G "http://127.0.0.1:8100/api/workspaces/<wid>/state/find" \
     --data-urlencode "type=TollTransaction" \
     --data-urlencode "plate=鲁A12345"

# 该车最近一次通行（第一条）里就有 fee / entry_station / exit_station / ts
```

车与站点的关系：

```bash
# 该车在哪些出口站驶出（passes）
curl "http://127.0.0.1:8100/api/workspaces/<wid>/state/related?ref=Vehicle/%E9%B2%81A12345&relation=passes"
```

## 场景 4：自然语言问数（对应本体术语表）

| 运营会怎么问 | 平台应落到 |
|--------------|------------|
| 「济南西收费站为什么拥堵？」 | `TollStation.name=济南西收费站` → `.status` |
| 「鲁A12345 交了多少通行费？」 | `Vehicle.plate=鲁A12345` → `TollTransaction.fee` |
| 「淄博收费站有几条车道？」 | `TollStation` → `has_lane` → `Lane` |
| 「今天各站收费总额排名」 | 按 `TollTransaction.exit_station` 聚合 `fee` |

术语表把「收费站」「拥堵」「通行费」「车牌」「驶出」等口语词映射到
`TollStation`、`TollStation.status`、`TollTransaction.fee`、`Vehicle.plate`、`exits`，
这就是后续 `POST /api/workspaces/{wid}/ask` 与 Agent 不猜表名的基础。

## 场景 5：重跑投影（数据源变化后同步）

```bash
curl -X POST "http://127.0.0.1:8100/api/workspaces/<wid>/projections/<pid>/run"
```

投影是幂等的 upsert：重复运行只会按行 id 更新对象属性、补关系边，
不会产生重复对象（第二次运行的 `changes` 通常为 0）。

## 场景 6：人工核对本体

```bash
curl "http://127.0.0.1:8100/api/workspaces/<wid>/ontology"     # 对象/关系/术语
curl "http://127.0.0.1:8100/api/workspaces/<wid>/runtime"      # Kernel 编译后的 Schema
curl "http://127.0.0.1:8100/api/workspaces/<wid>/datasources"  # 数据源与资产
curl "http://127.0.0.1:8100/api/workspaces/<wid>/projections"  # 投影定义
```

## 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| `bootstrap.py` 报「无法连接平台 API」 | 8100 服务未启动；先起服务，或用 `--memory` 离线验证 |
| 报「找不到样例数据库」 | 先执行 `python data/build_db.py` |
| 数据源连接失败 | 服务端读不到 `toll.db` 路径；确认脚本传的是绝对路径且服务与样例在同一台机器 |
| 重复运行产生很多工作空间 | 预期行为：平台没有按名查找，每次新建并打印新 wid；旧工作空间不会被改动 |
