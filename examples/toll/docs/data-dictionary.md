# 数据字典（data/toll.db）

样例库是 SQLite 单文件数据库 `data/toll.db`，由 `python data/build_db.py` 生成。
固定随机种子 `20260506`，任何机器上重复执行都得到同一份数据（脚本会先 DROP 再建表）。

## stations —— 收费站

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `id` | TEXT 主键 | 收费站编号 | `ST001` |
| `name` | TEXT 非空 | 收费站名称 | `济南西收费站` |
| `status` | TEXT 非空 | 实时通行状态：`free`（畅通）/ `busy`（繁忙）/ `congested`（拥堵） | `congested` |
| `province` | TEXT 非空 | 所属省份 | `山东` |

## lanes —— 收费车道

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `id` | TEXT 主键 | 车道编号 | `L001` |
| `station_id` | TEXT 非空 | 所属收费站 `stations.id` | `ST001` |
| `direction` | TEXT 非空 | 方向：`entry`（入口）/ `exit`（出口） | `exit` |
| `lane_type` | TEXT 非空 | 收费方式：`ETC` / `MTC`（人工或混合车道） | `ETC` |

## vehicles —— 车辆

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `plate` | TEXT 主键 | 车牌号，业务主键 | `鲁A12345` |
| `type` | TEXT 非空 | 车型：`客车` / `货车` | `客车` |
| `owner_type` | TEXT 非空 | 归属类型：`个人` / `企业` / `物流公司` | `个人` |

## passes —— 通行交易

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `id` | TEXT 主键 | 通行交易编号 | `P0001` |
| `plate` | TEXT 非空 | 车牌号，对应 `vehicles.plate` | `鲁A12345` |
| `entry_station` | TEXT 非空 | 入口收费站 `stations.id` | `ST001` |
| `exit_station` | TEXT 非空 | 出口收费站 `stations.id` | `ST002` |
| `lane_id` | TEXT 非空 | 驶出时通过的出口车道 `lanes.id` | `L006` |
| `fee` | REAL 非空 | 应收通行费（元） | `150.48` |
| `ts` | TEXT 非空 | 通行时间（ISO 8601） | `2026-05-06T07:07:00` |

## 语义映射（数据库 → 本体）

| 表 | 本体对象类型 | 对象 id | 属性映射 |
|----|--------------|---------|----------|
| `stations` | `TollStation` | `id` | `name→name`、`status→status`、`province→province` |
| `lanes` | `Lane` | `id` | `direction→direction`、`lane_type→lane_type`、`station_id→station_id` |
| `vehicles` | `Vehicle` | `plate` | `plate→plate`、`type→type`、`owner_type→owner_type` |
| `passes` | `TollTransaction` | `id` | `plate→plate`、`entry_station→entry_station`、`exit_station→exit_station`、`lane_id→lane_id`、`fee→fee`、`ts→ts` |

关系映射：

| 关系 | 来源资产 | source_field → target_field |
|------|----------|-----------------------------|
| `has_lane`（TollStation → Lane） | `lanes` | `station_id → id` |
| `enters`（Vehicle → TollStation） | `passes` | `plate → entry_station` |
| `exits`（TollTransaction → TollStation） | `passes` | `id → exit_station` |
| `passes`（Vehicle → TollStation） | `passes` | `plate → exit_station` |

## 数据规模

`stations` 6 行、`lanes` 18 行（每站 1 条入口 ETC + 1 条出口 ETC + 1 条出口 MTC）、
`vehicles` 20 行、`passes` 40 行，共 84 行。
