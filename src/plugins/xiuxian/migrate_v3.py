# -*- coding: utf-8 -*-
"""一次性存档迁移：补齐炼丹/用丹新增字段（schema_version 2 → 4）。

2026-10-26 接入「商店 / 炼丹 / 用丹」系统，玩家新增字段：
  furnace(炼丹炉名) / elixir_used(累计服用) / elixir_day(每日服用) /
  mixing(炼制中) / bt_big(待消耗突破率加成) / last_explore(秘境冷却时间戳)。
core.py 对这些字段一律用 .get() 兜底，缺字段也不会崩；本脚本只是把
存档显式规范化，并升 schema_version，保持与 migrate_v2 同风格。

幂等：schema_version >= 4 直接跳过。备份原文件。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

SCHEMA = 4
NEW_FIELDS = {
    "furnace": None,
    "elixir_used": {},
    "elixir_day": {},
    "mixing": None,
    "bt_big": 0.0,
    "last_explore": 0,
}


def main(path: Path) -> int:
    if not path.is_file():
        print(f"[migrate] 存档不存在，无需迁移：{path}")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("schema_version") or 1) >= SCHEMA:
        print(f"[migrate] 已是 schema_version={data.get('schema_version')}，跳过")
        return 0

    bak = path.with_suffix(path.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, bak)
    print(f"[migrate] 已备份 → {bak.name}")

    players = data.get("players") or {}
    n = 0
    for qq, p in players.items():
        for k, v in NEW_FIELDS.items():
            p.setdefault(k, v)
        n += 1
    data["schema_version"] = SCHEMA
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[migrate] 完成，{n} 个玩家已补齐炼丹字段（furnace/elixir_used/elixir_day/mixing/bt_big）")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parent / "data" / "xiuxian.json"))
