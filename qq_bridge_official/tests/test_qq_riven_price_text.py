# -*- coding: utf-8 -*-
"""守门：市场两盒失败态文案不像「系统故障」、成功态真出数（官方副本，2026-11-12）。

用户投诉「市场估价/词条差价没做好」：图1 两盒显示「暂无匹配样本 / 数据获取失败 /
接口样本不足」，读起来像机器人坏了。真根因是 WFM slug 连字符→400（由
test_qq_riven_slug.py 修复、使这把武器 status=ok 真出数）；本守门钉死剩下两件事：
  1. 仍真无数据的武器（status=error）走**诚实但不像故障**的降级文案；
  2. 成功态两盒真出数、口径独立（不编数、不写「暂无」冒充成功）。

锚定 official 副本根（parents[1]）。riven_price.py 只 import 标准库，宿主可直跑。
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PRICE = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_price.py"
_P = _F = 0
# 「像系统故障」的旧措辞：失败态不得再出现
_FAULTY = ("数据获取失败", "接口样本不足", "窗口样本不足", "获取失败")


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-52s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-52s %s" % (name, detail))


def _load():
    spec = importlib.util.spec_from_file_location("_rp_under_test", _PRICE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    rp = _load()

    # A 失败态：诚实降级、零故障措辞、零异常串
    print("== A. 失败态降级文案 ==")
    for mkt in [
        {"status": "error", "error": "RivenMarketError: WFM 拍卖返回 NoneType 对象"},
        {"status": "error", "error": "连接超时"},
        {"status": "ok", "med": None, "n": 0},  # ok 但无有效中位价 → 同走降级
        {}, None,
    ]:
        left, right = rp.price_boxes(mkt)
        blob = str(left) + str(right)
        ok(not any(t in blob for t in _FAULTY),
           "A 失败态无故障措辞", "mkt=%r" % (mkt,))
        ok(not any(t in blob for t in ("RivenMarketError", "NoneType", "Traceback")),
           "A 失败态无异常串")
        ok("暂无紫卡挂牌样本" in str(left.get("foot")),
           "A 失败态诚实说明无挂牌样本", "foot=%r" % left.get("foot"))
        ok(left.get("big") == "暂无匹配样本" and right.get("big") == "暂无实盘样本",
           "A 失败态 big 不编数")

    # B 成功态（图2 真出数快照）：两盒真出、口径独立
    print("== B. 成功态真出数 ==")
    mkt = {"status": "ok", "n": 5, "med": 70.0, "zero": 1.0,
           "match_tier": "同负词条", "rec_neg": {"delta": 230.0},
           "exact": {"status": "unknown", "n": 0}}
    left, right = rp.price_boxes(mkt)
    ok(left.get("big") == "70P", "B1 左盒出真中位价", "big=%r" % left.get("big"))
    ok(left.get("sub_val") == "1P", "B2 零洗中位价取 rank0", "sub=%r" % left.get("sub_val"))
    ok("匹配层级" in str(left.get("foot")), "B3 左盒标注匹配层级")
    ok(right.get("big") not in ("暂无匹配样本", "暂无实盘样本", None),
       "B4 右盒出模型估值", "big=%r" % right.get("big"))
    ok(left.get("sub_val") != right.get("big"), "B5 两盒口径独立（非同值）")
    ok("置信度%d%%" % rp.confidence(5) in str(right.get("sub")),
       "B6 右盒置信度由样本数推导")

    # C 注入反证：把失败态改回旧故障措辞 → price_boxes 真输出必须被 A 判据抓到
    print("== C. 注入反证 ==")
    src = _PRICE.read_text(encoding="utf-8")
    ok("该武器 WFM 暂无紫卡挂牌样本" in src,
       "C1 当前真源码含诚实降级文案")
    # C2 用 AST 取 price_boxes 执行代码里的字符串常量（排除注释/docstring，
    #     否则本文件第 93 行「不用『数据获取失败』」的注释会被子串误命中）
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "price_boxes"), None)
    doc_ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                doc_ids.add(id(b[0].value))
    code_strs = {n.value for n in ast.walk(fn)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and id(n) not in doc_ids} if fn else set()
    ok(not any(t in "".join(code_strs) for t in _FAULTY),
       "C2 price_boxes 执行代码不含旧故障措辞",
       "hit=%r" % [t for t in _FAULTY if t in "".join(code_strs)])
    inj = src.replace("该武器 WFM 暂无紫卡挂牌样本", "数据获取失败")
    ok("数据获取失败" in inj and inj != src,
       "C3 注入旧故障措辞被 A 判据抓到（反证样本构造）")

    print("\n%d PASS / %d FAIL" % (_P, _F))
    raise SystemExit(1 if _F else 0)


if __name__ == "__main__":
    main()
