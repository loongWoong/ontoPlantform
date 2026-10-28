#!/usr/bin/env python3
"""生成高速收费站样例数据库 data/toll.db（纯标准库 sqlite3）。

    python data/build_db.py

- 固定随机种子，任何机器上重复执行都得到同一份数据；
- 脚本可重复执行：先 DROP 再 CREATE，不残留旧表；
- 数据规模很小（84 行），足够跑通「数据源 → 投影 → 本体状态」全链路。

生成后会打印每张表的行数和一条样例，便于人工核对。
"""

from __future__ import annotations

import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Windows 控制台默认可能是 GBK，统一按 UTF-8 输出，避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "toll.db"
SEED = 20260506

# ---------------------------------------------------------------------------
# 固定数据集
# ---------------------------------------------------------------------------

# (id, name, status, province)
STATIONS = [
    ("ST001", "济南西收费站", "congested", "山东"),
    ("ST002", "青岛东收费站", "free", "山东"),
    ("ST003", "淄博收费站", "busy", "山东"),
    ("ST004", "潍坊收费站", "free", "山东"),
    ("ST005", "德州收费站", "congested", "山东"),
    ("ST006", "临沂收费站", "free", "山东"),
]

# 入口站 -> 出口站 固定里程（公里），与方向无关（对称）
DISTANCES = {
    ("ST001", "ST002"): 352,
    ("ST001", "ST003"): 101,
    ("ST001", "ST004"): 214,
    ("ST001", "ST005"): 122,
    ("ST001", "ST006"): 262,
    ("ST002", "ST003"): 253,
    ("ST002", "ST004"): 158,
    ("ST002", "ST005"): 474,
    ("ST002", "ST006"): 213,
    ("ST003", "ST004"): 113,
    ("ST003", "ST005"): 223,
    ("ST003", "ST006"): 161,
    ("ST004", "ST005"): 336,
    ("ST004", "ST006"): 274,
    ("ST005", "ST006"): 384,
}

PLATES = [
    "鲁A12345", "鲁A23456", "鲁A34567", "鲁A45678", "鲁A56789",
    "鲁B12345", "鲁B23456", "鲁B34567", "鲁B45678", "鲁B56789",
    "鲁C12345", "鲁C23456", "鲁C34567", "鲁C45678", "鲁C56789",
    "鲁D12345", "鲁D23456", "鲁D34567", "鲁D45678", "鲁D56789",
]

# 车型费率（元/公里）、最低收费（元），与 docs/business-rules.md 一致
RATE_PER_KM = {"客车": 0.45, "货车": 0.90}
MIN_FEE = {"客车": 5.0, "货车": 8.0}
ETC_DISCOUNT = 0.95
PASS_COUNT = 40
START_TS = datetime(2026, 5, 6, 7, 0, 0)


def distance(a: str, b: str) -> int:
    """入口站 a -> 出口站 b 的里程（对称查表）。"""
    if a == b:
        raise ValueError(f"入口站与出口站相同: {a}")
    return DISTANCES.get((a, b)) or DISTANCES[(b, a)]


def build_lanes() -> list[tuple[str, str, str, str]]:
    """每个收费站 3 条车道：入口 ETC / 出口 ETC / 出口 MTC。"""
    lanes: list[tuple[str, str, str, str]] = []
    for index, (station_id, _name, _status, _province) in enumerate(STATIONS):
        lanes.append((f"L{index * 3 + 1:03d}", station_id, "entry", "ETC"))
        lanes.append((f"L{index * 3 + 2:03d}", station_id, "exit", "ETC"))
        lanes.append((f"L{index * 3 + 3:03d}", station_id, "exit", "MTC"))
    return lanes


def build_vehicles() -> list[tuple[str, str, str]]:
    """20 辆车：货车约占 1/4，货车归物流公司，其余为个人/企业。"""
    vehicles: list[tuple[str, str, str]] = []
    for index, plate in enumerate(PLATES):
        vehicle_type = "货车" if index % 4 == 3 else "客车"
        if vehicle_type == "货车":
            owner_type = "物流公司"
        else:
            owner_type = "企业" if index % 3 == 0 else "个人"
        vehicles.append((plate, vehicle_type, owner_type))
    return vehicles


def build_passes(lanes: list[tuple[str, str, str, str]],
                 vehicles: list[tuple[str, str, str]]
                 ) -> list[tuple[str, str, str, str, str, float, str]]:
    """40 条通行交易：入口/出口不同站，出口车道取出口站的出口车道。"""
    rng = random.Random(SEED)
    exit_lanes: dict[str, list[tuple[str, str, str, str]]] = {}
    for lane in lanes:
        if lane[2] == "exit":
            exit_lanes.setdefault(lane[1], []).append(lane)

    station_ids = [s[0] for s in STATIONS]
    passes: list[tuple[str, str, str, str, str, float, str]] = []
    for index in range(1, PASS_COUNT + 1):
        entry = rng.choice(station_ids)
        exit_station = rng.choice([sid for sid in station_ids if sid != entry])
        plate, vehicle_type, _owner = rng.choice(vehicles)
        lane = rng.choice(exit_lanes[exit_station])

        km = distance(entry, exit_station)
        fee = max(MIN_FEE[vehicle_type], round(km * RATE_PER_KM[vehicle_type], 2))
        if lane[3] == "ETC":
            fee = round(fee * ETC_DISCOUNT, 2)
        else:
            fee = round(fee, 2)

        ts = START_TS + timedelta(minutes=7 * index + rng.randint(0, 3),
                                  seconds=rng.randint(0, 59))
        passes.append((
            f"P{index:04d}", plate, entry, exit_station, lane[0], fee,
            ts.isoformat(timespec="seconds"),
        ))
    return passes


SCHEMA = """
DROP TABLE IF EXISTS passes;
DROP TABLE IF EXISTS lanes;
DROP TABLE IF EXISTS vehicles;
DROP TABLE IF EXISTS stations;

CREATE TABLE stations (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    status   TEXT NOT NULL CHECK (status IN ('free', 'busy', 'congested')),
    province TEXT NOT NULL
);

CREATE TABLE lanes (
    id         TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id),
    direction  TEXT NOT NULL CHECK (direction IN ('entry', 'exit')),
    lane_type  TEXT NOT NULL CHECK (lane_type IN ('ETC', 'MTC'))
);

CREATE TABLE vehicles (
    plate      TEXT PRIMARY KEY,
    type       TEXT NOT NULL CHECK (type IN ('客车', '货车')),
    owner_type TEXT NOT NULL
);

CREATE TABLE passes (
    id            TEXT PRIMARY KEY,
    plate         TEXT NOT NULL REFERENCES vehicles(plate),
    entry_station TEXT NOT NULL REFERENCES stations(id),
    exit_station  TEXT NOT NULL REFERENCES stations(id),
    lane_id       TEXT NOT NULL REFERENCES lanes(id),
    fee           REAL NOT NULL,
    ts            TEXT NOT NULL
);

CREATE INDEX idx_lanes_station ON lanes(station_id);
CREATE INDEX idx_passes_plate ON passes(plate);
CREATE INDEX idx_passes_exit ON passes(exit_station);
"""


def main() -> int:
    lanes = build_lanes()
    vehicles = build_vehicles()
    passes = build_passes(lanes, vehicles)

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO stations VALUES (?, ?, ?, ?)", STATIONS)
        conn.executemany("INSERT INTO lanes VALUES (?, ?, ?, ?)", lanes)
        conn.executemany("INSERT INTO vehicles VALUES (?, ?, ?)", vehicles)
        conn.executemany("INSERT INTO passes VALUES (?, ?, ?, ?, ?, ?, ?)", passes)
        conn.commit()

        print(f"已生成 {DB_PATH}")
        for table in ("stations", "lanes", "vehicles", "passes"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:<9} {count:>3} 行")
        total = conn.execute(
            "SELECT (SELECT COUNT(*) FROM stations) + (SELECT COUNT(*) FROM lanes) "
            "+ (SELECT COUNT(*) FROM vehicles) + (SELECT COUNT(*) FROM passes)"
        ).fetchone()[0]
        print(f"  合计      {total:>3} 行")

        print("\n样例数据：")
        for row in conn.execute(
            "SELECT id, name, status FROM stations ORDER BY id LIMIT 2"
        ):
            print(f"  station {row[0]} | {row[1]} | {row[2]}")
        for row in conn.execute(
            "SELECT id, plate, entry_station, exit_station, lane_id, fee, ts "
            "FROM passes ORDER BY id LIMIT 2"
        ):
            print("  pass    " + " | ".join(str(v) for v in row))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
