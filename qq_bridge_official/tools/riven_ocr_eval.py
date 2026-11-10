# -*- coding: utf-8 -*-
"""tools/riven_ocr_eval.py —— 紫卡 OCR 准确率评测（对着带标准答案的真实截图跑）。

语料来源（第三方维护，非本项目自造）：
    github.com/CloudeaSoft/koishi-plugin-warframe
    tests/assets/riven-ocr/community-2026-10-21/
      ├─ rivens.json    ← 标准答案：武器中文名 / 紫卡名 / 每条词条「名称 + 数值 + 单位」
      └─ images/riven_XXX.webp|jpg|png

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\riven_ocr_eval.py          # 跑全部
    .venv\\Scripts\\python.exe tools\\riven_ocr_eval.py -v       # 打印每张的原图 OCR 文本

判定口径：
  * 武器：中文名 normalize 后必须一致（去空格/大小写；「基础伤害」↔「伤害」这类
    卡面文案差异不计入错误，但会单独标注）。
  * 词条：按名称配对；数值误差 ≤0.05（%）视为一致（卡面只显示 1 位小数）。
  * 词条条数必须一致——少数一条等于漏识别，比数值偏差更严重。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("LOGURU_LEVEL", "CRITICAL")  # nonebot 用 loguru，必须靠环境变量压日志
ROOT = Path(__file__).resolve().parents[1]
PKG = "qq_bridge"
if PKG not in sys.modules:  # 允许脱离 nonebot 直接跑
    _pkg = types.ModuleType(PKG)
    _pkg.__path__ = [str(ROOT / "src" / "plugins" / "qq_bridge")]
    _pkg.__package__ = PKG
    sys.modules[PKG] = _pkg

CORPUS = ROOT / "tools" / "ocr_fixtures"   # 第三方语料（koishi 仓库），随工具存档
TOL_PCT = 0.05

#: 卡面文案 vs 官方中文名的等价写法（只做归一，不算错误）
_ZH_EQUIV = {
    "伤害": "基础伤害",
    "近战伤害": "基础伤害",
    "火焰": "火焰伤害",
    "冰冻": "冰冻伤害",
    "电击": "电击伤害",
    "毒素": "毒素伤害",
}
#: 卡面文案 ↔ 我们的词条名（同义改写，不是错误）——与产品侧 _CN_SYNONYM 对齐
_ZH_SYN = (("最大值", "上限"), ("几率", "率"))


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").strip()


def _norm_stat(name: str) -> str:
    n = _norm(name)
    n = _ZH_EQUIV.get(n, n)
    for a, b in _ZH_SYN:
        n = n.replace(a, b)
    return n


def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= TOL_PCT


def _eval_one(entry: dict, verbose: bool = False) -> dict:
    from qq_bridge.game_tools import riven

    path = CORPUS / "images" / entry["file_name"]
    res = {"file": entry["file_name"], "ok": False, "weapon_gt": entry.get("weapon_name", ""),
           "weapon_our": "", "riven_gt": entry.get("riven_name", ""), "riven_our": "",
           "n_gt": len(entry.get("attributes") or []), "n_our": 0, "rows": [],
           "error": ""}
    if not path.exists():
        res["error"] = "图片缺失"
        return res
    try:
        import asyncio
        _md, data = asyncio.run(riven.riven_img_analyze_full(path.read_bytes()))
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    if not data:
        res["error"] = "未解析出卡片"
        return res

    res["weapon_our"] = data.get("weapon_zh", "")
    res["riven_our"] = data.get("riven_name", "")
    ours = {}
    for st in data.get("stats") or []:
        ours[_norm_stat(st["name"])] = st
    res["n_our"] = len(ours)

    all_ok = _norm(res["weapon_gt"]) == _norm(res["weapon_our"])
    for attr in entry.get("attributes") or []:
        key = _norm_stat(attr["name"])
        st = ours.get(key)
        if st is None:
            res["rows"].append({"name": attr["name"], "gt": attr["value"], "our": None,
                                "ok": False, "note": "未识别"})
            all_ok = False
            continue
        try:
            val = float(st.get("num", st.get("value")))
        except (TypeError, ValueError):
            val = None
        # GT 存的是**带符号**数值（负词条为负）；我们存「幅度 + sign 标记」，
        # 所以幅度比 magnitude、符号比 sign，两者都过才算对。
        gt = float(attr["value"])
        ok_mag = val is not None and _close(val, abs(gt))
        gt_neg = gt < 0
        our_neg = (st.get("sign") == "-")
        ok = ok_mag and (gt_neg == our_neg)
        note = "" if ok else ("符号错" if ok_mag else "数值偏差")
        res["rows"].append({"name": attr["name"], "gt": gt, "our": val,
                            "ok": ok, "note": note})
        all_ok = all_ok and ok
    if res["n_our"] != res["n_gt"]:
        all_ok = False
    res["ok"] = all_ok
    if verbose:
        print(f"    [原图 OCR] {_md[:0]}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    # nonebot 用的是 loguru，logging.disable 管不到它 —— 必须 logger.remove()
    logging.disable(logging.CRITICAL)
    try:
        from loguru import logger as _lg
        _lg.remove()
    except Exception:  # noqa: BLE001
        pass

    answers = json.loads((CORPUS / "rivens.json").read_text(encoding="utf-8"))
    results = []
    for e in answers:
        r = _eval_one(e, args.verbose)
        results.append(r)
        flag = "PASS" if r["ok"] else "FAIL"
        print(f"[{flag}] {r['file']:<16s} 武器 {r['weapon_our'] or '—':<12s}"
              f"(GT {r['weapon_gt']})  词条 {r['n_our']}/{r['n_gt']}"
              f"{'  ' + r['error'] if r['error'] else ''}")
        for row in r["rows"]:
            mark = "  " if row["ok"] else "!!"
            our = "—" if row["our"] is None else f"{row['our']:g}"
            print(f"      {mark} {row['name']:<10s} GT {row['gt']:>8g}   我们 {our:>8s}"
                  f"  {row['note']}")

    skipped = [r for r in results if r["error"] == "图片缺失"]
    results = [r for r in results if r["error"] != "图片缺失"]
    n = len(results)
    n_ok = sum(1 for r in results if r["ok"])
    if skipped:
        print(f"（跳过 {len(skipped)} 张：图片未下载成功）")
    n_weapon = sum(1 for r in results
                   if _norm(r["weapon_gt"]) == _norm(r["weapon_our"]))
    n_attr = sum(len(r["rows"]) for r in results)
    n_attr_ok = sum(1 for r in results for row in r["rows"] if row["ok"])
    print("\n" + "=" * 62)
    print(f"整卡全对   {n_ok}/{n}")
    print(f"武器命中   {n_weapon}/{n}")
    print(f"词条数值   {n_attr_ok}/{n_attr} ({n_attr_ok / max(1, n_attr) * 100:.1f}%)")
    return 0 if n_ok == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
