# -*- coding: utf-8 -*-
"""tests/check_riven_full_layout.py —— 紫卡全面分析卡版式门禁（card-design 契约）。

渲染全部样本 → 收集真实文字包围盒 → 检查 溢出 / 重叠 / 间距。
任一违规打印明细并 sys.exit(1)，拦在交差前；并带**反证**（注入已知错误必须被拦截）。

运行：python qq_bridge/tests/check_riven_full_layout.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _riven_full_render as R  # noqa: E402


def _selftest(tpl):
    """反证：门禁对「真错误」必须报错（否则检查等于没加）。"""
    checks = [
        ("overlap", [(28, 100, 160, 130, "A"), (100, 100, 240, 130, "B")], "overlap"),
        ("overflow", [(28, 100, 700, 130, "wide")], "overflow"),
        ("spacing", [(28, 100, 160, 130, "A"), (28, 130.5, 160, 160, "B")], "spacing"),
    ]
    ok = True
    for name, boxes, key in checks:
        res = tpl._check_boxes(boxes, min_gap=2.0, pad=28, width=655, height=1920)
        caught = bool(res[key])
        print("  反证[%s]：%s" % (name, "已拦截" if caught else "漏检!"))
        ok = ok and caught
    return ok


def main():
    print("== 紫卡全面分析卡版式门禁 ==")
    dm, tpl, theme = R.setup()
    if not _selftest(tpl):
        print("== 反证失败：门禁未拦截已知错误 ==")
        return 1

    sample = dm.reference_sample()
    ts = time.strftime("%Y-%m-%d %H:%M")
    # 2026-11-10：左盒 big 现在有两态（价格数字 / 中文「暂无匹配样本」），
    # 两态都必须过版式门禁；旧夹具传的 `wfm=` 参数早已不存在，会直接 TypeError。
    matched = {"status": "ok", "n": 27, "med": 380.0, "lo": 330.0, "hi": 400.0,
               "zero": 369.0, "match_tier": "同负词条",
               "rec_neg": {"delta": 17.0}, "exact": {"status": "few", "n": 1},
               "ts": ts}
    exact = dict(matched, exact={"status": "ok", "n": 9, "median": 520.0})
    few = dict(matched, status="error", error="样本不足")
    samples = {
        "reference_sample": sample,
        "no_screenshot": dm.riven_full_data(weapon="绝路"),
        "wfm_real_median": dm.riven_full_data(weapon="绝路", market=matched),
        "price_big_exact_match": dm.riven_full_data(weapon="绝路", market=exact),
        "price_big_no_match": dm.riven_full_data(weapon="绝路", market=few),
        "long_weapon": dm.riven_full_data(weapon="一个非常非常非常长的武器中文名称测试溢出"),
        "ocr_real": dm.riven_full_data(parsed={
            "weapon": "rubico", "weapon_zh": "绝路", "riven_name": "Acri-igniata",
            "rank": 8, "disp": 1.2, "type": "Rifle",
            "stats": [{"en": "criticalChance", "zh": "触发几率", "value": 135.4,
                       "unit": "%", "sign": "+"},
                      {"en": "magazineCapacity", "zh": "弹匣容量", "value": 79.1,
                       "unit": "%", "sign": "+"}]}),
        "empty_stats": dict(sample, stats=[], fusion_rows=[]),
    }
    out_dir = os.path.join(R.REPO, "outputs", "riven_full_test")
    os.makedirs(out_dir, exist_ok=True)
    total = 0
    for name, data in samples.items():
        boxes = []
        try:
            c = tpl.build(theme, data=data, width=655, boxes=boxes)
            png = c.to_bytes()
        except Exception as e:
            print("  [%s] 渲染异常：%r" % (name, e))
            total += 1
            continue
        res = tpl._check_boxes(boxes, min_gap=2.0, pad=28, width=655, height=1920)
        n_bad = len(res["overflow"]) + len(res["overlap"]) + len(res["spacing"])
        total += n_bad
        print("  [%s] 溢出 %d / 重叠 %d / 间距 %d ｜ PNG %d bytes ｜ 文字盒 %d"
              % (name, len(res["overflow"]), len(res["overlap"]),
                 len(res["spacing"]), len(png), len(boxes)))
        for k in ("overflow", "overlap", "spacing"):
            for msg in res[k]:
                print("      - %s: %s" % (k, msg))
        with open(os.path.join(out_dir, "%s.png" % name), "wb") as f:
            f.write(png)
    if total:
        print("== 门禁失败：共 %d 处违规 ==" % total)
        return 1
    print("== 门禁通过：溢出 0 / 重叠 0 / 间距 0（样本 %d 张，图存 %s）=="
          % (len(samples), out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
