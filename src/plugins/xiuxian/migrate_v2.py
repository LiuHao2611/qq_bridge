# -*- coding: utf-8 -*-
"""一次性存档迁移：旧 12 级境界表 → 开源 38 级表（schema_version 1 → 2）。

为什么需要
----------
2026-10-26 把成长数值表整体换成开源表（见 config_open.py），境界从 12 级变 38 级，
存档里 `realm` 存的是**索引**，不迁移的话旧 `realm=3`（旧金丹）会被新表读成
「筑基境初期」，玩家境界凭空空降。

映射规则（按大境界对齐，不重算修为）：
  旧索引 0-11 = 凡人/练气/筑基/金丹/元婴/化神/炼虚/合体/大乘/渡劫/真仙/金仙
  新索引     = 江湖好手(0) / 练气初期(1) / 筑基初期(4) / 结丹初期(7) / 元婴初期(10)
               / 化神初期(13) / 炼虚初期(16) / 合体初期(19) / 大乘初期(22) / 渡劫初期(25)
               / 真仙初期(29) / 金仙初期(32)
修为（exp）原样保留；补 `level_up_cd=0`、`bt_bonus=0.0` 两个新字段。

幂等：顶层写 `schema_version=2`，重复执行直接跳过。
备份：写盘前把原文件复制为 `xiuxian.json.bak-<时间戳>`。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

OLD_TO_NEW = [0, 1, 4, 7, 10, 13, 16, 19, 22, 25, 29, 32]
SCHEMA = 2


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
    for qq, p in players.items():
        old = int(p.get("realm") or 0)
        new = OLD_TO_NEW[old] if 0 <= old < len(OLD_TO_NEW) else 0
        p["realm"] = new
        p.setdefault("level_up_cd", 0)
        p.setdefault("bt_bonus", 0.0)
        print(f"[migrate] {qq} {p.get('name')}: realm {old} → {new}, exp={p.get('exp')}")
    data["schema_version"] = SCHEMA
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[migrate] 完成，{len(players)} 个玩家已迁移")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parent / "data" / "xiuxian.json"))
