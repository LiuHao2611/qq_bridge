# -*- coding: utf-8 -*-
"""紫卡「市场 / 推荐词条」真实 WFM 联调门禁（2026-11-04 22:25 新增）。

跑在容器里（需要 qq_bridge 包 + 出网）：

    python /app/tools/check_riven_market.py --out /app/tmp

与 ``check_riven_layout.py`` 分工：那个用离线夹具守**排版**，这个用真联网守
**数据真实性**（prove-it：被测的那部分不许被替身顶掉）。检查内容：

  A 真实 WFM ``/v1/auctions/search`` 按武器聚合（glaxion / laetum / torid）
  B 反例：Chrome-like UA 会拿到空样本，必须显式 ``RivenMarketError``
  C 反例：纯中文武器名不得被当成 WFM slug 发出去
  D 反例：``build_card_data`` 漏传 market 必须 TypeError（用户截图缺陷的根因）
  E 真实快照 → ``build_card_data`` → ``recs`` 四行 + PNG 样张
  F 文字卡同样拿到真实推荐词条，且与图片卡共用同一份快照
  G/G2/G3 最贵视图价格不计价、中位数抗离群、口径标注齐全
  G4 2026-11-04 23:25 同分位对称对比：把「不对称分布对比」放回去必须能识别

绝不降级：任何一项失败 ``exit 1``；不使用静态权重表、不写「暂无」冒充成功。
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import os
import sys
import time
from urllib.parse import quote as _quote

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /app
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from qq_bridge.pic import render_sync  # noqa: E402
from qq_bridge.game_tools import riven_card  # noqa: E402

#: 复刻 2026-11-04 用户截图：冷冻光束步枪 / GLAXION / 3 正 1 负 / Lv8 / 倾向 1.20
GLAXION = {
    "weapon": "GLAXION", "weapon_zh": "冷冻光束步枪", "riven_name": "",
    "type": "Rifle", "disp": 1.20, "mr": 14, "rank": 8,
    "stats": [
        {"en": "Heat Damage", "zh": "火焰伤害", "value": 99.1, "unit": "%", "sign": "+"},
        {"en": "Viral Damage", "zh": "病毒伤害", "value": 110.2, "unit": "%", "sign": "+"},
        {"en": "Multishot", "zh": "多重射击", "value": 110.1, "unit": "%", "sign": "+"},
        {"en": "Zoom", "zh": "变焦", "value": 59.0, "unit": "%", "sign": "-"},
    ],
}

_CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


async def run(out_dir: str, wait: float = 0.0) -> int:
    checks: list[tuple[str, bool, str]] = []

    def ck(name: str, cond, detail: str = "") -> None:
        checks.append((name, bool(cond), detail))
        print("%s %-48s %s" % ("PASS" if cond else "FAIL", name, detail))

    if wait:
        # 连续跑本脚本会触发 WFM 429；等待限流窗口是同一实现的退避，不是降级。
        print("WAIT %.0fs（WFM 429 退避）" % wait)
        await asyncio.sleep(wait)

    async def fetch_with_backoff(parsed: dict) -> dict:
        """429 / 超时时退避重试**同一实现**（不是降级、不是换备选接口）。"""
        for attempt in range(4):
            try:
                return await riven_card.fetch_market_bundle(parsed)
            except riven_card.RivenMarketError:
                if attempt == 3:
                    raise
                print("  retry %d/3 after WFM 429/timeout ..." % (attempt + 1))
                await asyncio.sleep(15.0)
        raise AssertionError("unreachable")

    # ---- A 真实拍卖聚合 ----
    live: dict[str, dict] = {}
    weapons = (("GLAXION", "Rifle"), ("Laetum", "Pistol"), ("Torid", "Rifle"))
    for i, (weapon, kind) in enumerate(weapons):
        if i:
            await asyncio.sleep(1.0)       # 节流：别把 WFM 打到 429
        parsed = {"weapon": weapon, "type": kind, "stats": []}
        try:
            m = await fetch_with_backoff(parsed)
            live[weapon] = m
            good = (m["status"] == "ok" and m["sample_n"] >= 3 and m["n"] >= 3
                    and len(m["rec_pos"]) >= 1 and bool(m["rec_neg"])
                    and m["rec_neg"]["n"] >= riven_card._WFM_MIN_SAMPLES
                    and abs(m["rec_neg"]["pct"]) < 500   # 旧版 +3949% 必须被拦住
                    and m["desc_price_used"] is False)
            ck("A 真实拍卖 " + weapon, good,
               "slug=%s rank=%d 基线=%.0fP 推荐负=%s 中位=%.0fP(%+.0f%%) 匹配=%s"
               % (m["slug"], m["rank"], m["base"], m["rec_neg"]["zh"],
                  m["rec_neg"]["median"], m["rec_neg"]["pct"], m["match_tier"]))
        except Exception as e:  # noqa: BLE001 — 如实记为失败，不跳过
            ck("A 真实拍卖 " + weapon, False, "%s: %s" % (type(e).__name__, e))

    # ---- G 口径反例：最贵视图的价格绝不参与计算，聚合必须抗离群 ----
    try:
        desc = await riven_card._fetch_auction_page("laetum", "price_desc")
        desc_max = max(p for p in (riven_card._auction_price(a) for a in desc) if p)
        lm = live.get("Laetum")
        ck("G 溢价未被最贵挂单拉爆",
           bool(lm) and lm["status"] == "ok"
           and lm["rec_neg"]["median"] < desc_max / 10
           and abs(lm["rec_neg"]["pct"]) < 500,
           "同负词条中位 %.0fP < 最贵挂单 %.0fP/10；pct=%.0f%%（旧版 mean=8098P/+3949%%）"
           % ((lm or {}).get("rec_neg", {}).get("median", 0), desc_max,
              (lm or {}).get("rec_neg", {}).get("pct", 0)))
        outlier = [200.0, 200.0, 200.0, 200.0, 66666.0]
        center = riven_card._robust_center(outlier)
        ck("G2 聚合抗离群（中位数）", center == 200.0,
           "离群样本 → 中心 %.1f；改回 mean 会得到 %.1f"
           % (center, sum(outlier) / len(outlier)))
        ck("G3 口径标注齐全",
           bool(lm) and "非成交" in lm["price_kind"]
           and "匹配" in lm["source"] and bool(lm["exact"]),
           "price_kind=%r match_tier=%r exact=%r"
           % ((lm or {}).get("price_kind"), (lm or {}).get("match_tier"),
              (lm or {}).get("exact", {}).get("status")))
    except Exception as e:  # noqa: BLE001 — 如实记为失败，不跳过
        ck("G 口径反例组", False, "%s: %s" % (type(e).__name__, e))

    # ---- G4 同分位对称对比：把 2026-11-04 23:22 真实犯过的「不对称对比」放回去 ----
    # 未过滤 price_asc 页 rank8 只有 13 条（全在 90–100P），-zoom 池 rank8 有 84 条
    # （150–1700P）。拿全量负词条中位数比通用池最便宜 13 条 → +700% 伪溢价。
    # 这里用同一份真实快照同时算两种口径，证明门禁能区分「同分位」与「不对称」。
    try:
        gm = live.get("GLAXION")
        if gm and gm["status"] == "ok":
            asc = await riven_card._fetch_auction_page("glaxion", "price_asc")
            base = riven_card._prices_of(riven_card._same_rank(asc, 8))
            neg_all_raw = await riven_card._fetch_auction_page(
                "glaxion", "price_asc",
                "&negative_stats=" + _quote(gm["rec_neg"]["slug"], safe=""))
            neg_all = riven_card._prices_of(riven_card._same_rank(neg_all_raw, 8))
            k = min(len(base), len(neg_all))
            paired = (riven_card._robust_center(neg_all[:k])
                      / riven_card._robust_center(base[:k]) - 1) * 100
            unpaired = (riven_card._robust_center(neg_all)
                        / riven_card._robust_center(base) - 1) * 100
            ck("G4 同分位对称对比（不对称口径会翻几倍）",
               isinstance(gm["pair_k"], int)
               and gm["pair_k"] == k and gm["rec_neg"]["n"] == k
               and gm["rec_neg"]["pool_n"] == len(neg_all)
               and abs(gm["rec_neg"]["pct"] - paired) < 1e-6
               and paired < 150 and unpaired > paired * 2,
               "k=%d 同分位=%+.0f%% 不对称=%+.0f%%；bundle pct=%+.0f%%"
               % (k, paired, unpaired, gm["rec_neg"]["pct"]))
        else:
            ck("G4 同分位对称对比", False, "GLAXION 未拿到真实快照，无法核验")
    except Exception as e:  # noqa: BLE001 — 如实记为失败，不跳过
        ck("G4 同分位对称对比", False, "%s: %s" % (type(e).__name__, e))

    # ---- B 反例：Chrome-like UA 会导致空样本，必须显式报错 ----
    saved_headers = dict(riven_card._WFM_AUCTION_HEADERS)
    saved_cache = dict(riven_card._AUCTION_CACHE)   # A 阶段真实快照留着给 E 复用
    riven_card._AUCTION_CACHE.clear()
    riven_card._WFM_AUCTION_HEADERS.clear()
    riven_card._WFM_AUCTION_HEADERS.update({
        "User-Agent": _CHROME_UA, "accept": "application/json",
        "Platform": "pc", "Language": "en"})
    try:
        raised = False
        err = ""
        try:
            await riven_card.fetch_market_bundle({"weapon": "GLAXION", "stats": []})
        except riven_card.RivenMarketError as e:
            raised, err = True, str(e)
        ck("B Chrome UA 空样本必须显式报错", raised, err[:110])
    finally:
        riven_card._WFM_AUCTION_HEADERS.clear()
        riven_card._WFM_AUCTION_HEADERS.update(saved_headers)
        riven_card._AUCTION_CACHE.clear()
        riven_card._AUCTION_CACHE.update(saved_cache)  # 恢复真实快照，E 阶段零请求

    # ---- C 反例：纯中文武器名不得当 slug ----
    try:
        slug = riven_card._weapon_slug(
            {"weapon": "冷冻光束步枪", "weapon_zh": "冷冻光束步枪"})
        ck("C 纯中文武器名不得当 slug", False, "竟解析成 %r" % slug)
    except riven_card.RivenMarketError as e:
        ck("C 纯中文武器名不得当 slug", True, str(e)[:110])

    # ---- D 反例：图片卡漏传市场数据必须立刻报错 ----
    try:
        riven_card.build_card_data(copy.deepcopy(GLAXION))  # 故意漏 market
        ck("D 图片卡漏传 market 必须 TypeError", False, "旧版签名允许省略，缺陷会复发")
    except TypeError as e:
        ck("D 图片卡漏传 market 必须 TypeError", True, str(e)[:110])

    # ---- E 真实快照接进图片卡 + 渲染样张 ----
    # 用**带 stats 的 GLAXION** 走唯一入口，才会真正算「正负面相同」精确匹配
    # （A 阶段只传武器名，exact=unknown）。asc/desc/neg_pool 命中 A 阶段缓存，
    # 只多一个 positive_stats 请求。
    try:
        market = await fetch_with_backoff(copy.deepcopy(GLAXION))
    except Exception as e:  # noqa: BLE001 — 显式失败态，不伪造成功
        market = riven_card.market_error_bundle(e)
    # ⛔ 2026-11-04 23:29 真实事故：带 stats 的精确匹配分支抛 TypeError，
    # 旧脚本把「渲染出失败态卡」也判 PASS，把真异常盖了过去。
    # 真联网门禁里，成功态是**必须**的；失败态排版由离线夹具脚本负责验证。
    ck("E0 全量快照（含精确匹配）必须成功",
       market.get("status") == "ok",
       "status=%s error=%r" % (market.get("status"), market.get("error", ""))[:150])
    data = riven_card.build_card_data(
        copy.deepcopy(GLAXION), market, weapon_image=None,
        now=time.strftime("%Y-%m-%d %H:%M"))
    recs = data.get("recs") or {}
    ck("E 图片卡 recs 状态与快照一致",
       bool(recs) and recs.get("status") == market.get("status") and
       all(recs.get(k) for k in ("market", "pos", "neg", "exact", "source")),
       "status=%s market=%r pos=%r neg=%r exact=%r"
       % (recs.get("status"), recs.get("market"), recs.get("pos"),
          recs.get("neg"), recs.get("exact")))
    if market.get("status") == "ok":
        ck("E2 卡面含样本数/口径/时间戳",
           "样本" in recs.get("market", "") and "同等级" in recs.get("market", "")
           and "低价视图" in recs.get("market", "")
           and "非成交" in recs.get("source", "") and "匹配" in recs.get("source", "")
           and "同分位" in recs.get("source", "")
           and "中位数" in recs.get("neg", "") and "不计价" in recs.get("pos", "")
           and "同分位" in recs.get("neg", "")
           and market["ts"] in recs.get("source", ""),
           recs.get("source", ""))
        ex = market["exact"]
        ck("E3 精确匹配层级如实",
           ex["status"] in ("ok", "few", "unknown")
           and ((ex["status"] == "ok" and ex["n"] >= 3 and ex["median"] > 0)
                or (ex["status"] != "ok" and bool(ex["reason"]))),
           "status=%s n=%d median=%s reason=%r"
           % (ex["status"], ex["n"], ex["median"], ex["reason"][:70]))
    else:
        ck("E2 失败态写明原因（不写暂无）",
           "获取失败" in recs.get("market", "") and
           "不可用" in recs.get("pos", "") and
           "不可用" in recs.get("exact", "") and
           "暂无" not in recs.get("market", ""),
           recs.get("market", "")[:110])

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        png = render_sync("riven_analysis", theme_name=None,
                          card="紫卡分析", data=data, boxes=[])
        path = os.path.join(out_dir, "glaxion_live_market.png")
        with open(path, "wb") as f:
            f.write(png)
        print("PNG: %s (%d bytes)" % (path, len(png)))

    # ---- F 文字卡同快照 ----
    md = await riven_card.build_card_async(copy.deepcopy(GLAXION), market=market)
    # 旧版文案：静态热度分 / 估价 / 「样本不足，暂无」。出现任意一个说明又降级了。
    stale = ("WM 拍卖样本不足，暂无", "市场样本不足，可发", "按市场热度权重，非实价",
             "词条价差预估价", "基于市场价差 - 暂无",
             "（WM 高价挂牌", "算术均值", "挂牌价均价",
             "同负词条普通挂牌")     # 23:22 不对称口径的旧文案
    pos_txt = "、".join(market.get("rec_pos") or [])
    neg_zh = (market.get("rec_neg") or {}).get("zh") or ""
    if market.get("status") == "ok":
        good = ("基于市场价差 -" in md and "不参与计价" in md
                and "挂牌价非成交价" in md and "类似紫卡" in md
                and "非成交" in md and "同分位" in md and "低价视图" in md
                and pos_txt in md and neg_zh in md
                and not any(x in md for x in stale))
        detail = "推荐正=%r 推荐负=%r 口径标注齐全" % (pos_txt, neg_zh)
    else:
        good = ("获取失败" in md and "不可用" in md
                and not any(x in md for x in stale))
        detail = "失败态原因可见"
    ck("F 文字卡含真实市场价与推荐词条", good,
       "%s；残留旧文案=%s；md=%d 字符"
       % (detail, [x for x in stale if x in md], len(md)))

    failed = sum(1 for _, ok, _ in checks if not ok)
    print("SUMMARY: %d checks, %d passed, %d failed"
          % (len(checks), len(checks) - failed, failed))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="真实数据样张输出目录")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="开跑前等待秒数（WFM 429 退避；同一实现的退避，不是降级）")
    args = ap.parse_args()
    return asyncio.run(run(args.out, args.wait))


if __name__ == "__main__":
    sys.exit(main())
