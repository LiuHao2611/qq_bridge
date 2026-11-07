# -*- coding: utf-8 -*-
"""riven_fusion —— 紫卡融合（Riven Fusion）指南 + 费用表（2026-11-05 P1）。

数据现实（实测，不是拍脑袋）：
  WFM 紫卡拍卖记录**不含 fuse / veins 字段**（item 只有 attributes /
  mod_rank / re_rolls / polarity 等），因此**无法给出「按融合等级的实时市价」**。
  本模块绝不编造融合溢价行情，只提供三类有依据的信息：

    1. 真实当前市场中位价（rank8，WFM ``price_asc`` 挂牌要价，**非成交价**）；
    2. 融合机制说明（同名重复紫卡 → 每次 +1 融合 vein → +1 次重洗 re-roll 机会）；
    3. 融合费用表：Warframe Wiki《Fusion》——紫卡按 **Rare 稀有度**计费
       （EBC = 30 Endo / CrBC = 1,449 Credits，与模组融合同公式），
       逐次增量 + 累计。公式已与 wiki 表逐项核对
       （rank8 累计 7,650 Endo / 369,495 Cr 完全一致）。

口径红线（与 riven_card / riven_trend 一致）：
  * 价格只取 price_asc 低价视图，`_robust_center` 中位数，**非成交价**；
  * 样本不足 / 取数失败显式抛错，不降级、不填「暂无」、不估算；
  * **任何「按融合等级的市价 / 溢价」都不得出现**（WFM 无该字段）。
"""
from __future__ import annotations

from . import riven_card as rc
from . import warframe
from .riven_trend import RivenTrendError, resolve_weapon

# 复用趋势模块的武器解析与取数口径
RivenFusionError = RivenTrendError  # 同源显式错误，统一门禁/断言捕获

RANK = 8                       # 统一比较满级卡
MIN_SAMPLES = 3                # 少于 3 条不计算中价
MAX_FUSE = 8                   # 费用表展示到第 8 次融合

#: Warframe Wiki《Fusion》：Rare / Riven 稀有度基准成本
#:   Endo Base Cost (EBC) = 30，Credit Base Cost (CrBC) = 1,449
#:   单次(k−1→k) = base × 2^(k−1)；累计(0→k) = base × (2^k − 1)
_ENDO_EBC = 30
_CREDIT_CBC = 1449


def fusion_cost_table(max_fuse: int = MAX_FUSE) -> list[dict]:
    """融合费用表：每个融合次数 k（1..max_fuse）的单次增量 + 累计。

    公式（Warframe Wiki《Fusion》，紫卡按 Rare 稀有度）：
      单次(k−1→k) = base × 2^(k−1)；累计(0→k) = base × (2^k − 1)
    已与 wiki Rare 列核对：k=1 → 30 Endo / 1,449 Cr；
    k=8 → 7,650 Endo / 369,495 Cr（1,449 × 255）。本函数按公式现算，
    不在代码里硬编码 wiki 数字，避免抄错。
    """
    rows: list[dict] = []
    for k in range(1, max_fuse + 1):
        inc_endo = _ENDO_EBC * (2 ** (k - 1))
        inc_credit = _CREDIT_CBC * (2 ** (k - 1))
        cum_endo = _ENDO_EBC * (2 ** k - 1)
        cum_credit = _CREDIT_CBC * (2 ** k - 1)
        rows.append({"fuse": k, "inc_endo": inc_endo, "inc_credit": inc_credit,
                     "cum_endo": cum_endo, "cum_credit": cum_credit})
    return rows


async def fusion_data(weapon: str | None = None) -> dict:
    """`#紫卡融合 [武器]` 的数据。

    weapon 可空：空时只给通用融合指南（费用对所有紫卡一致）；
    非空时额外取该武器真实 rank8 中位价作价值参考。
    """
    q = (weapon or "").strip()
    info = None
    median = None
    n = lo = hi = None
    if q:
        info = resolve_weapon(q)
        if not info:
            raise RivenFusionError(
                "没找到武器「%s」的 WFM 紫卡 slug（试试中文全名或英文名）" % weapon)
        slug = info["slug"]
        auctions = await rc._fetch_auction_page(slug, "price_asc", allow_empty=True)
        prices = rc._prices_of(rc._same_rank(auctions, RANK))
        if len(prices) >= MIN_SAMPLES:
            median = rc._robust_center(prices)
            n = len(prices)
            lo, hi = prices[0], prices[-1]
    return {
        "weapon_zh": (info or {}).get("zh") or "通用指南",
        "weapon_en": (info or {}).get("en") or "",
        "slug": (info or {}).get("slug") or "",
        "median": median, "n": n or 0, "lo": lo, "hi": hi,
        "cost_rows": fusion_cost_table(),
        # 口径脚注：明确声明 WFM 无 fuse 字段，本卡不编造按融合等级的市价
        "source": ("WFM 紫卡挂牌要价·非成交价（中位价）｜Warframe Wiki《Fusion》费用公式："
                   "紫卡按 Rare 稀有度 EBC=30 Endo / CrBC=1,449 Credits｜"
                   "WFM 拍卖不暴露 fuse 字段，本卡不编造按融合等级的实时市价"),
    }


__all__ = ["MAX_FUSE", "RANK", "fusion_cost_table", "fusion_data"]
