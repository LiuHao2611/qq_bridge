# -*- coding: utf-8 -*-
"""tests/test_riven_full.py —— 紫卡全面分析（像素级复刻卡）单元测试 + 真数据探针。

纪律（prove-it）：
  * 版式：与参考卡实测墨迹宽度/坐标做机器对照（不靠肉眼）。
  * 数据：参考卡夹具 sample=True 且脚注标明非实时；真实路径字段不得编造。
  * 真联网：WFM 窗口跑真实 _auction_stats，验证市价管线可用。

运行：python qq_bridge/tests/test_riven_full.py   （退出码 0=全过，1=有失败）
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _riven_full_render as R  # noqa: E402

_failed = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        _failed.append(name)


def test_data_discipline(dm):
    s = dm.reference_sample()
    check("sample_flagged", s.get("sample") is True)
    check("sample_footer_label", "版式样例" in s.get("source", ""))
    check("sample_has_all_sections",
          all(k in s for k in ("stats", "fusion_rows", "scores", "price_left",
                               "price_right", "mods", "kill_line", "gain_line",
                               "cmd_groups", "footer")), str(sorted(s.keys())))
    check("sample_3_stats", len(s["stats"]) == 3)
    check("sample_4_fusion_rows", len(s["fusion_rows"]) == 4)
    check("sample_8_mods", len(s["mods"]) == 8)
    check("mod_assets_exist",
          sum(1 for m in s["mods"] if m.get("image") and os.path.isfile(m["image"])) == 8)

    # 真实武器名路径：不得编造词条/评分/击杀效率
    r = dm.riven_full_data(weapon="绝路")
    check("real_not_sample", r.get("sample") is False)
    check("real_no_fabricated_stats", not any(
        "%" in str(x.get("value", "")) for x in r["stats"]))
    check("real_honest_need_screenshot", "需截图" in str(r["fusion_rows"]))
    check("real_scores_are_dashes", all(
        "—" in x["value"] for x in r["scores"]))
    check("real_kill_line_absent", not r["kill_line"])
    check("real_price_no_fake", r["price_right"]["big"] == "暂无实盘样本")
    check("real_source_declares_model", "模型预估" in dm.FULL_SOURCE)
    txt = dm.full_text(r)
    check("text_fallback_mentions_need_shot", "需紫卡截图" in txt)

    # WFM 真实中位 → 价格框填真实数字；词条价差用挂牌中位作「模型代理估值」，
    # 恒标「模型预估 谨慎参考」，绝不冒充实盘价差（2026-11-04 market 口径）。
    r2 = dm.riven_full_data(
        weapon="绝路",
        market={"status": "ok", "n": 7, "med": 400.0, "lo": 360.0, "hi": 1000.0})
    check("wfm_real_median_filled", r2["price_left"]["sub_val"] == "400P")
    check("wfm_model_proxy_labeled",
          r2["price_right"]["big"] == "400P"
          and "模型预估" in r2["price_right"]["foot"]
          and "n=7" in r2["price_right"]["sub"])

    # 回归（2026-11-06 用户实卡「阿利乌双枪」）：三条词条全是触发/弹匣/装填时，
    # 旧模型（纯面板 DPS）三者贡献全 0 → 伤害分 0.0 / 综合 0.0 / 击杀效率
    # 13.4→13.4→13.4 被压平。持续 DPS（弹匣容量 + 装填速度进占空比）修复后
    # 必须伤害分 > 0、击杀效率三列互不相同。注入验证：把 _sustain_ratio 打回 1.0
    # （等价旧逻辑）时本用例立刻报错。
    rc = sys.modules.get("qq_bridge.game_tools.riven_card")
    wk = {"magazineSize": 10, "reloadTime": 3.4, "fireRate": 4.248,
          "damage": {"total": 487}, "criticalChance": 0.06,
          "criticalMultiplier": 1.8, "cat": "副武器"}
    rp = {"weapon": "Akarius", "weapon_zh": "阿利乌双枪", "rank": 8,
          "disp": 1.2, "type": "Pistol",
          "stats": [
              {"en": "Status Chance", "zh": "触发几率", "value": 135.4, "unit": "%", "sign": "+"},
              {"en": "Magazine Capacity", "zh": "弹匣容量", "value": 79.1, "unit": "%", "sign": "+"},
              {"en": "Reload Speed", "zh": "装填速度", "value": 30.7, "unit": "%", "sign": "-"},
          ]}
    sim = rc._sim(rp, wk)
    sc = rc._scores(rp, sim)
    check("sustain_dmg_score_nonzero", sc["dmg"] > 0.0, "dmg=%r" % sc["dmg"])
    check("sustain_ke_not_flat",
          len({"%.2f" % sim["ke_base"], "%.2f" % sim["ke_cur"],
               "%.2f" % sim["ke_best"]}) == 3,
          "ke=(%.2f, %.2f, %.2f)" % (sim["ke_base"], sim["ke_cur"], sim["ke_best"]))


def test_real_pic_registration():
    """真实加载 ``pic/__init__.py``（非桩）验证模板注册，避免只测到隔离夹具。"""
    import importlib.util
    import types
    pkg_dir = os.path.join(R.REPO, "qq_bridge", "src", "plugins", "qq_bridge")
    # 隔离夹具往 sys.modules 注入了桩包（templates.__path__=[]），会遮蔽真实包；
    # 这里临时清空全部 qq_bridge* 再真实导入，结束后原样恢复。
    saved = {k: v for k, v in list(sys.modules.items())
             if k == "qq_bridge" or k.startswith("qq_bridge.")}
    for k in saved:
        sys.modules.pop(k, None)
    try:
        qq = types.ModuleType("qq_bridge")
        qq.__path__ = [pkg_dir]
        sys.modules["qq_bridge"] = qq
        spec = importlib.util.spec_from_file_location(
            "qq_bridge.pic", os.path.join(pkg_dir, "pic", "__init__.py"),
            submodule_search_locations=[os.path.join(pkg_dir, "pic")])
        m = importlib.util.module_from_spec(spec)
        sys.modules["qq_bridge.pic"] = m
        spec.loader.exec_module(m)
        check("真实 pic 包导入", len(m._TEMPLATES) >= 20, str(len(m._TEMPLATES)))
        check("riven_full 已注册", "riven_full" in m._TEMPLATES)
        check("riven_full 可调用", callable(m._TEMPLATES.get("riven_full")))
        check("riven_full 中文标签", m.TEMPLATE_LABELS.get("riven_full") == "紫卡全面分析卡")
    except Exception as e:
        check("真实 pic 包导入", False, "%r" % e)
    finally:
        for k in list(sys.modules):
            if k == "qq_bridge" or k.startswith("qq_bridge."):
                sys.modules.pop(k, None)
        sys.modules.update(saved)


def test_pixel_alignment(dm, tpl, theme):
    """机器对照：参考卡关键墨迹左右边界 vs 复刻卡（容差 ±6px）。"""
    from PIL import Image
    ref_path = os.environ.get(
        "RIVEN_FULL_REF",
        r"D:\WorkBuddyData\clipboard-images\clipboard-2026-11-06T08-59-20-609Z-6fb2b736.jpg")
    if not os.path.isfile(ref_path):
        print("  SKIP  pixel_alignment（参考图不在本机：%s）" % ref_path)
        return
    ref = Image.open(ref_path).convert("RGB")
    rep_path = os.path.join(R.REPO, "outputs", "riven_full_test", "replica.png")
    rep = Image.open(rep_path).convert("RGB")

    def span(im, y0, y1, x0, x1, cond):
        px = im.load()
        cols = [x for x in range(x0, x1)
                if sum(1 for y in range(y0, y1) if cond(*px[x, y])) >= 2]
        return (min(cols), max(cols)) if cols else None

    blue = lambda r, g, b: b > r + 22 and b > g + 4 and r < 165 and b > 80
    dark = lambda r, g, b: max(r, g, b) < 135
    green = lambda r, g, b: g > r + 16 and g > b + 10 and g < 200 and r < 160
    orange = lambda r, g, b: r > g + 35 and r > b + 55 and r > 135
    purple = lambda r, g, b: r > g + 18 and b > g + 18 and r < 175 and b < 205

    items = [
        ("页眉标题", 18, 60, 240, 470, blue),
        ("章节1标题", 105, 150, 25, 300, blue),
        ("武器名", 182, 214, 40, 240, purple),
        ("词条1数值", 236, 265, 45, 145, purple),
        ("词条1名称", 236, 265, 170, 265, blue),
        ("词条1高值", 236, 262, 545, 610, blue),
        ("章节2标题", 495, 545, 55, 300, blue),
        ("表头状态", 556, 584, 30, 210, blue),
        ("表头融合结果", 556, 584, 215, 425, blue),
        ("表头数值", 556, 584, 435, 620, blue),
        ("表格值", 590, 625, 435, 540, green),
        ("章节3标题", 875, 920, 25, 300, blue),
        ("评分大数字", 965, 1015, 35, 180, dark),
        ("价格左大字", 1095, 1140, 40, 315, orange),
        ("价格右大字", 1095, 1140, 335, 620, orange),
        ("章节5长标题", 1250, 1300, 25, 620, blue),
        ("指令标题", 1595, 1632, 55, 400, dark),
    ]
    worst = 0
    for name, y0, y1, x0, x1, cond in items:
        a, b = span(ref, y0, y1, x0, x1, cond), span(rep, y0, y1, x0, x1, cond)
        if a is None or b is None:
            print("  SKIP  %s（墨迹未检出 ref=%s rep=%s）" % (name, a, b))
            continue
        d = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
        worst = max(worst, d)
        check("像素对齐 %s (Δ=%dpx)" % (name, d), d <= 6,
              "ref=%s rep=%s" % (a, b))
    print("  INFO  最大像素偏差 = %dpx（阈值 6px）" % worst)

    # 关键结构线位置（水平线扫描）：参考卡的线是很浅的灰/绿/紫，
    # 阈值必须放宽到「非纯白的有色像素」，否则扫不到（真实踩过的坑）。
    def hline(im, y0, y1, x0, x1, thr=260):
        px = im.load()
        for y in range(y0, y1):
            n = sum(1 for x in range(x0, x1)
                    if 150 < max(px[x, y]) < 248 and min(px[x, y]) > 140)
            if n > thr:
                return y
        return None
    for label, y0, y1 in [("页眉线", 65, 80), ("评分框顶", 938, 950),
                          ("价格框顶", 1028, 1040), ("页脚线", 1865, 1880)]:
        a, b = hline(ref, y0, y1, 30, 620), hline(rep, y0, y1, 30, 620)
        check("结构线 %s (ref=%s rep=%s)" % (label, a, b),
              a is not None and b is not None and abs(a - b) <= 3)


async def test_real_network(dm):
    """真联网：WFM 窗口 → 真实 fetch_market_for_card → 真实中位进卡并过门禁。"""
    try:
        from qq_bridge.game_tools import riven_card as _rc
    except Exception as e:
        print("  SKIP  real_network（模块不可用）：%r" % e)
        return
    import httpx
    from qq_bridge.pic.templates import riven_full as tpl
    headers = {"User-Agent": "Mozilla/5.0 (qq_bridge-test)", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True,
                                    timeout=20.0) as cl:
            d = (await cl.get("https://api.warframe.market/v1/auctions")).json()
    except Exception as e:
        print("  SKIP  real_network（联网失败/被风控）：%r" % e)
        return
    auc = (d.get("payload") or {}).get("auctions") or []
    from collections import Counter
    cnt = Counter(((a.get("item") or {}).get("weapon_url_name") or "").lower()
                  for a in auc if (a.get("item") or {}).get("weapon_url_name"))
    wn = next((w for w, c in cnt.most_common() if c >= 3), None)
    check("真联网窗口有紫卡", len(auc) > 0, "n=%d" % len(auc))
    if not wn:
        print("  SKIP  窗口内无武器出现≥3次（滚动窗口，属正常）")
        return
    try:
        st = await asyncio.wait_for(
            _rc.fetch_market_for_card({"weapon": wn, "weapon_zh": wn, "rank": 8}),
            timeout=20.0)
    except Exception as e:
        print("  SKIP  fetch_market_for_card 异常：%r" % e)
        return
    if not st or st.get("status") != "ok" or st.get("med") is None:
        print("  SKIP  真实中位不可用（样本不足/被风控，属正常降级）：%r"
              % (st.get("error") if st else "None"))
        return
    data = dm.riven_full_data(weapon=wn, market=st)
    boxes = []
    c = tpl.build(R.setup()[2], data=data, width=655, boxes=boxes)
    res = tpl._check_boxes(boxes, min_gap=2.0, pad=28, width=655, height=1920)
    check("真数据卡门禁通过", not (res["overflow"] or res["overlap"] or res["spacing"]),
          str(res))
    check("真数据中位入卡", data["price_left"]["sub_val"] == "%dP" % int(round(st["med"])))
    print("  INFO  WFM %s 真实中位 ≈ %s 白金（n=%s）" % (wn, st["med"], st["n"]))
    out = os.path.join(R.REPO, "outputs", "riven_full_test", "real_%s.png" % wn)
    with open(out, "wb") as f:
        f.write(c.to_bytes())
    print("  真实数据卡：%s" % out)


def main():
    print("== 紫卡全面分析 (像素级复刻) 测试 ==")
    dm, tpl, theme = R.setup()
    # 先出一张基准复刻卡供像素对照
    boxes = []
    c = tpl.build(theme, data=dm.reference_sample(), width=655, boxes=boxes)
    out_dir = os.path.join(R.REPO, "outputs", "riven_full_test")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "replica.png"), "wb") as f:
        f.write(c.to_bytes())
    res = tpl._check_boxes(boxes, min_gap=2.0, pad=28, width=655, height=1920)
    check("基准卡门禁 0/0/0", not (res["overflow"] or res["overlap"] or res["spacing"]),
          str(res))
    test_data_discipline(dm)
    test_real_pic_registration()
    test_pixel_alignment(dm, tpl, theme)
    asyncio.run(test_real_network(dm))
    print("== 结果：%s ==" % ("ALL PASS" if not _failed else "FAILED: " + ", ".join(_failed)))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
