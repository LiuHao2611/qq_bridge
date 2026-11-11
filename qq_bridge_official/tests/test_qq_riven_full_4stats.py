# -*- coding: utf-8 -*-
"""守门：紫卡全面分析卡第 4 词条不再被截断（官方副本，2026-11-11）。

防的是用户图1 vs 图2：图1 只有 3 条词条，第 4 条（通常是诅咒负词条）
被数据层 ``[:3]`` 与模板 ``range(3)`` 双重截断整条丢失。

红线：
  * 3 词条路径必须保持原行距 / 原版式（参考卡像素级回归零影响）；
  * 4 词条走紧凑行距，塞进 stats 卡片且不与市场参考重叠；
  * 融合表第 4 行 pair 必须有宽度约束，不越列重叠；
  * 4 条局数由常量 _MAX_STATS 统一，不许再散落 [:3]。

锚定 official 副本根（parents[1]）。纯 AST + 数据层 import，宿主可直接跑。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_full.py"
_TMPL = _REPO / "src" / "plugins" / "qq_bridge" / "pic" / "templates" / "riven_full.py"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-46s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-46s %s" % (name, detail))


def _slice_vals(tree: ast.AST, target: str) -> list:
    """收集对 target 名字做的所有切片上界常量（如 x[:3] 的 3）。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                and n.value.id == target and isinstance(n.slice, ast.Slice):
            s = n.slice.upper
            if isinstance(s, ast.Constant):
                out.append(s.value)
    return out


def section_static():
    ds = _DATA.read_text(encoding="utf-8")
    ts = _TMPL.read_text(encoding="utf-8")
    dt = ast.parse(ds)
    tt = ast.parse(ts)

    # B1 数据层定义 _MAX_STATS=4 且无 [:3] 硬截断
    cap = None
    for n in ast.walk(dt):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id == "_MAX_STATS" for t in tgts):
                cap = n.value.value if isinstance(n.value, ast.Constant) else None
    ok(cap == 4, "B1 数据层 _MAX_STATS==4", "cap=%r" % cap)
    # stats 与 fusion 两处循环都应改用 [:_MAX_STATS]，不得再有 [:3]
    hard3 = []
    for n in ast.walk(dt):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
            u = n.slice.upper
            if isinstance(u, ast.Constant) and u.value == 3:
                hard3.append(ast.dump(n.value)[:40])
    ok(hard3 == [], "B2 数据层无 [:3] 硬截断", str(hard3))

    # B3 模板 stats 区动态行距：4 条用紧凑 row_base（4 个值），3 条保持原（3 个值）
    ok("len(stats) >= 4" in ts, "B3a 模板按 4 条切紧凑行距")
    compact = "row_base = [250.0, 292.0, 334.0, 376.0]" in ts
    legacy = "row_base = [258.0, 307.0, 357.0]" in ts
    ok(compact and legacy, "B3b 4条紧凑/3条原行距并存",
       "compact=%s legacy=%s" % (compact, legacy))
    ok("range(n_rows)" in ts and "n_rows = 4" in ts and "n_rows = 3" in ts,
       "B3c 模板按 n_rows 循环（非固定 range(3)）")

    # B4 融合表第 4 行 pair 有 fit 宽度约束（防越列重叠）
    ok("fit(pair," in ts, "B4 融合表 pair 用 fit 约束宽度",
       "防长词条名越第二列")

    # B5 注入反证：把数据层 _MAX_STATS 改成 3 → 守门应判失败
    poisoned = ds.replace("_MAX_STATS = 4", "_MAX_STATS = 3")
    ok(poisoned != ds, "B5a 注入构造成功（_MAX_STATS 3）")
    poisoned_cap = None
    for n in ast.walk(ast.parse(poisoned)):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id == "_MAX_STATS" for t in tgts):
                poisoned_cap = n.value.value
    ok(poisoned_cap != 4, "B5b 注入后守门判据能抓到（反证）",
       "poisoned_cap=%r" % poisoned_cap)


if __name__ == "__main__":
    print("== B. 第4词条守门 ==")
    section_static()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
