# -*- coding: utf-8 -*-
"""守门：紫卡全面分析卡评分字母口径自洽（官方副本，2026-11-11）。

防的是用户图1：伤害分/市场分/综合评分「全 S」——根因是把 0–100 分制喂给
``riven_card._grade``（偏差百分比口径 p=(10+d)/20，d>9.2 即 S）。正确应 ``_letter``。

本守门只管「_letter vs _grade」字母口径（D1–D6），不动模型。
``_scores.total=max(dmg,mkt)``（综合口径）与 ``gain_max``（最强紫卡基准）2026-11-11
全量对齐 Rabbit，由独立守门 ``test_qq_riven_gainmax.py`` 覆盖，本守门不重复。

锚定 official 副本根（parents[1]）。纯 AST + 真 riven_card._letter，宿主可跑。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_full.py"
_CARD = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-46s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-46s %s" % (name, detail))


def _load_card_isolated():
    """riven_card 只 import nonebot.log / warframe，桩掉后加载真 _letter/_grade。"""
    nb = types.ModuleType("nonebot"); nbl = types.ModuleType("nonebot.log")
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    nbl.logger = _L(); nb.log = nbl
    sys.modules.setdefault("nonebot", nb); sys.modules.setdefault("nonebot.log", nbl)
    pkg = types.ModuleType("_gt"); pkg.__path__ = []
    wf = types.ModuleType("_gt.warframe")
    wf._load_riven_tables = lambda: ({}, {}, {})
    sys.modules["_gt"] = pkg; sys.modules["_gt.warframe"] = wf
    spec = importlib.util.spec_from_file_location("_gt.riven_card", _CARD)
    m = importlib.util.module_from_spec(spec); sys.modules["_gt.riven_card"] = m
    spec.loader.exec_module(m)
    return m


def _scores_assignment(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) \
                        and t.value.id == "base" and isinstance(t.slice, ast.Constant) \
                        and t.slice.value == "scores":
                    return n.value
    return None


def section():
    src = _DATA.read_text(encoding="utf-8")
    tree = ast.parse(src)
    rc = _load_card_isolated()

    # D1 scores 装配里只用 _letter，绝不出现 _grade
    scores = _scores_assignment(tree)
    dump = ast.dump(scores) if scores else ""
    ok("scores" in dump or "value" in dump, "D0 找到 scores 装配", dump[:60])
    ok(dump.count("_letter") == 3, "D1 三个分数都用 _letter",
       "count=%d" % dump.count("_letter"))
    ok("_grade" not in dump, "D2 scores 装配不含 _grade（防全S回归）")

    # D3 _letter 分档正确：低分绝不 S
    cases = [(11.0, "E"), (44.6, "C"), (62.1, "B"), (81.4, "A"), (100.0, "S")]
    for v, expect in cases:
        got = rc._letter(v)
        ok(got == expect, "D3 _letter(%s)==%s" % (v, expect), "got=%s" % got)

    # D4 反证：同样分数喂 _grade 会全 S（证明修复必要 + 守门判据有效）
    all_s = all(rc._grade(v) == "S" for v, _ in cases)
    ok(all_s, "D4 反证 _grade 对这些分数全 S（旧 bug 复现）")

    # D5 图1原分数：新口径下市场分 81.4 不再是 S
    ok(rc._letter(81.4) != "S" and rc._grade(81.4) == "S",
       "D5 市场分81.4 从 S(错) 纠为 %s(对)" % rc._letter(81.4))

    # D6 注记口径分离：_grade 仍在 build_card_data 用于 dev（偏差），不许从项目删掉
    ok(callable(getattr(rc, "_grade", None)) and callable(getattr(rc, "_letter", None)),
       "D6 _grade/_letter 两套口径并存（各司其职）")


if __name__ == "__main__":
    print("== D. 评分字母口径守门 ==")
    section()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
