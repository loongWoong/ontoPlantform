#!/usr/bin/env python3
"""装载金融租赁样例，并可选地跑一次智能视图编译自检。

    python -m examples.finance_lease.bootstrap
    python -m examples.finance_lease.bootstrap --memory
    python -m examples.finance_lease.bootstrap --db .onto/platform.db --verify

默认写入运行中 API 使用的 ``.onto/platform.db``；脚本完成后刷新控制台即可。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from onto_platform.bootstrap import PlatformConfig, build_platform  # noqa: E402
from onto_platform.interfaces.smart_ui import (  # noqa: E402
    compile_smart_ui,
    invoke_smart_action,
    smart_ui_meta,
)

from .scenario import seed_finance_lease  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="finance-lease-bootstrap")
    parser.add_argument("--db", default=str(ROOT / ".onto" / "platform.db"))
    parser.add_argument("--memory", action="store_true",
                        help="build an in-memory platform instead of sqlite")
    parser.add_argument("--verify", action="store_true",
                        help="compile the smart UI and exercise the risk_review action")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.memory:
        platform = build_platform(PlatformConfig(store="memory"))
    else:
        db_path = Path(args.db).resolve()
        platform = build_platform(PlatformConfig(
            store="sqlite", db_path=str(db_path)))
        print(f"db          {db_path}")

    wid = seed_finance_lease(platform)
    runtime = platform.ontologies.describe_runtime(wid, "admin")
    gateway = platform.ctx.gateway_for(wid)
    counts = {
        item.get("name"): len(gateway.find_objects(item["name"]))
        for item in (runtime.get("objects") or [])
        if isinstance(item, dict) and item.get("name")
    }
    print(f"workspace   {wid}")
    print(f"objects     {json.dumps(counts, ensure_ascii=False)}")

    if args.verify:
        meta = smart_ui_meta(platform, wid, "admin")
        print(f"ready       {meta.get('ready')}")
        print(f"focus       {meta['bridge'].get('focusObject')} / "
              f"{meta['bridge'].get('focusValue')}")
        compiled = compile_smart_ui(
            platform, wid, "admin", meta.get("defaultQuestion", ""), ["owner"])
        views = [
            (view.get("perspective"), view.get("title"))
            for view in compiled["compiled"].get("viewMap") or []
        ]
        print(f"views       {json.dumps(views, ensure_ascii=False)}")
        action = next(
            (item for item in meta.get("actions") or []
             if item.get("key") == "risk_review"),
            None,
        )
        if action:
            result = invoke_smart_action(
                platform, wid, "admin", "risk_review",
                {"focusValue": meta["bridge"].get("focusValue"), "role": "owner"},
            )
            print(f"action      risk_review -> "
                  f"{result.get('status') or result.get('ok')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
