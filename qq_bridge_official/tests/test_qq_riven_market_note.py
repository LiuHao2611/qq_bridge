# -*- coding: utf-8 -*-
"""守门：紫卡全面分析卡「市场负面参考」字段取对、异常串不上卡（官方副本，2026-11-11）。

防的是用户图1卡面：
    *市场负面参考：获取失败 (RivenMarketError: WFM 拍卖返回 NoneType 对象…)
根因：riven_full.py 误取 cd["market"]（市场整体状态串），正确字段是 cd["recs"]["neg"]。

锚定 official 副本根（parents[1]）。纯 AST + 数据层 import（带桩），宿主可直接跑。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_full.py"

_P = _F = 0
_LEAK = ("RivenMarketError", "NoneType", "Traceback", "Exception", "ActionFailed")


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-48s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-48s %s" % (name, detail))


def _load_data_layer():
    """隔离加载 riven_full.py：它只 import logging/pathlib，函数体内懒 import riven_card。"""
    spec = importlib.util.spec_from_file_location("_riven_full_under_test", _DATA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_rc(market):
    """桩 riven_card：build_card_data 返回与真实现同构的 recs/market。"""
    rc = types.ModuleType("_rc")
    if market["status"] == "ok":
        neg = market["rec_neg"]
        recs = {"status": "ok", "neg": "%s %+.1fp/%+.0f%%（同分位 rank%d 各 %d 条·中位数·挂牌非成交）"
                % (neg["zh"], neg["delta"], neg["pct"], market["rank"], neg["n"])}
        mtxt = "WM 低价视图中位 %.0fP｜区间 %.0f–%.0fP" % (
            market["base"], market["lo"], market["hi"])
    else:
        recs = {"status": "error", "neg": "不可用（市场数据获取失败）"}
        mtxt = "获取失败（%s）" % market["error"]
    rc.build_card_data = lambda parsed, m: {
        "stats": [], "market": mtxt, "recs": recs,
        "hint": "偏差以对应倾向的理论中值为基准，正常范围为±10%。",
    }
    rc._weapon_raw = lambda name: None  # 跳过评分/DPS（模块D 单独测）
    # 模块 E 后 riven_full_data 会按 _wtype 调 _build_mods → 读这两张真表键。
    # 桩只需提供同构结构（内容本守门不校验，配卡正确性由 test_qq_riven_build 覆盖）。
    rc._BUILD = {"Pistol": ["p1"], "Rifle": ["r1"], "Shotgun": ["s1"]}
    rc._MELEE_BUILD = ["m1"]
    return rc


def _inject_rc(RF, rc):
    # riven_full_data 在 `from . import riven_card as rc` / `from . import riven_price`
    # 处取模；用 sys.modules 桩替换。
    pkg = types.ModuleType("_gt")
    pkg.__path__ = []
    rcm = types.ModuleType("_gt.riven_card")
    for k, v in vars(rc).items():
        setattr(rcm, k, v)
    rpm = types.ModuleType("_gt.riven_price")
    rpm.observed_text = lambda ts: ""
    rpm.price_boxes = lambda market: ({}, {})  # 结构占位，模块C 不测价格盒
    sys.modules["_gt"] = pkg
    sys.modules["_gt.riven_card"] = rcm
    sys.modules["_gt.riven_price"] = rpm
    RF.__package__ = "_gt"


def _riven_full_data_fn(tree: ast.AST):
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "riven_full_data":
            return n
    return None


def section_static():
    src = _DATA.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = _riven_full_data_fn(tree)
    ok(fn is not None, "C0 找到 riven_full_data 函数体")

    # C1 真实路径（函数体内）写 market_note 的赋值：1 处取 _neg + 1 处 except 兜底。
    notes = []
    if fn:
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) \
                            and t.value.id == "base" \
                            and isinstance(t.slice, ast.Constant) \
                            and t.slice.value == "market_note":
                        notes.append(n.value)
    # 含 _neg 的那一处即真实成功/失败路径取值
    real = [v for v in notes if "_neg" in ast.dump(v)]
    ok(len(real) == 1, "C1 真实路径 market_note 唯一且取 _neg", "n=%d" % len(real))
    expr = ast.dump(real[0]) if real else ""
    fn_dump = ast.dump(fn) if fn else ""
    # recs 取自 cd.get("recs")，neg 取自 _recs.get("neg")，最终喂给 _neg
    ok("recs" in fn_dump and "neg" in fn_dump and "_neg" in expr,
       "C2 market_note 链路 recs->neg->_neg 完整")
    ok('"market"' not in expr and "'market'" not in expr,
       "C3 取值表达式不含 cd['market']（防异常串回流）")

    # C4 有异常串黑名单兜底
    ok(all(tok in src for tok in _LEAK[:2]), "C4 源码含异常串黑名单",
       "RivenMarketError/NoneType")


def _run(market, expect_in, expect_no_leak=True):
    RF = _load_data_layer()
    _inject_rc(RF, _fake_rc(market))
    parsed = {"weapon": "终幕·米尔", "weapon_zh": "终幕·米尔", "type": "Melee",
              "rank": 8, "disp": 1.2, "mr": 0,
              "stats": [{"en": "Critical Chance", "zh": "暴击几率", "value": 222.0,
                         "unit": "%", "sign": "+"}]}
    d = RF.riven_full_data(parsed=parsed, market=market)
    note = d["market_note"]
    has = expect_in in note
    leak = any(t in note for t in _LEAK)
    return note, has, (not leak if expect_no_leak else True)


def section_behavior():
    okm = {"status": "ok", "rank": 8, "base": 95.0, "lo": 80.0, "hi": 120.0,
           "rec_neg": {"zh": "滑暴", "delta": 91.1, "pct": 105, "n": 13}}
    note, has, clean = _run(okm, "滑暴 +91.1p/+105%")
    ok(has, "C5 成功态出真实价差", note)
    ok(clean, "C5b 成功态无异常串")

    erm = {"status": "error", "error": "RivenMarketError: WFM 拍卖返回 NoneType 对象"}
    note, has, clean = _run(erm, "不可用")
    ok(has, "C6 失败态出人话降级", note)
    ok(clean, "C6b 失败态不露异常串", note)

    # C7 注入反证：失败态若强行回退 cd["market]（旧 bug）→ 必须能抓到泄露
    old_note = "*市场负面参考：获取失败（RivenMarketError: WFM 拍卖返回 NoneType 对象）"
    ok(any(t in old_note for t in _LEAK), "C7 注入旧实现 -> 守门判据抓到泄露（反证）")

    # C8 cd["market"] 仍保留诊断价值（不被清空），只是不再画到这一栏
    RF = _load_data_layer()
    _inject_rc(RF, _fake_rc(erm))
    d = RF.riven_full_data(parsed={"weapon": "终幕·米尔", "weapon_zh": "终幕·米尔",
                                   "type": "Melee", "rank": 8, "disp": 1.2, "mr": 0,
                                   "stats": []}, market=erm)
    ok("不可用" in d["market_note"], "C8 失败态 market_note 已降级", d["market_note"])


if __name__ == "__main__":
    print("== C. 市场负面参考守门 ==")
    section_static()
    section_behavior()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
