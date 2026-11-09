# -*- coding: utf-8 -*-
"""紫卡全面分析卡「市场价格预计 / 词条价差预估」的真实估价层。

为什么要独立成模块（2026-11-10）：旧 ``riven_full._price_boxes`` 把左盒
``big`` 与 ``foot`` **写死**成「暂无匹配样本」，导致卡片明明有 27 条真实挂牌
样本、右盒也算出了价格，左盒仍宣称「无匹配样本」；且左右两框填的是同一个
``med``，根本没有两套口径。用户要求「搞清楚别人是怎么做的，全量对齐」。

别人（Warframe Rabbit 原卡，用户 2026-11-06 08:59 提供的 655×1920 实卡，
逐字基准见 ``tests/fixtures/rabbit_riven_price_reference.json``）的做法：

  * 左盒「市场价格预计」= **匹配样本估价**：有同词条组合的可比挂牌就给价，
    没有才诚实写「暂无匹配样本」；同时 sub 给出「零洗中位价」作为独立基准。
    原卡那张恰好无精确匹配样本（369P 基准 / 386P 模型估值，两个数不同）。
  * 右盒「词条价差预估」= 基于 WFM 挂牌的**模型估值**，脚注「模型预估 谨慎参考」；
    sub 写「来源：wm市场|置信度80%」，不写死置信度、不自造「样本 n=」字段。
  * 底部「最近观测：13小时内」是**相对时间**，不是绝对时间戳。

外部同类做法（2026-11-10 调研）：RivenRadar 的 comparable-bucket 模型按
「同武器 + 正词条组合 + 负词条类别」分桶，用 15 分位锚 + 25~75 分区间并给
confidence；桶内样本不足时降级到更宽的桶并降低置信度，而不是一律报「无」。

本模块按同一语义做**层级降级**，全部取 ``riven_card.fetch_market_bundle`` 的
真实字段，绝不编造价格：

  L1 ``exact.status == "ok"``  → 正负面相同、同等级精确匹配挂牌中位价；
  L2 ``n >= 3``                → 同负词条/同等级低价视图中位价（标注匹配层级）；
  L3 以上都不满足              → 才写「暂无匹配样本」，并保留零洗基准。

零洗中位价只取 ``market["zero"]``（rank0 未洗挂牌），绝不拿 rank8 的 ``med``
冒充；词条价差预估用「同等级中位 + 推荐负词条同分位溢价」推导，与零洗基准
天然不同口径。置信度按样本数推导，观测时间按参考卡写相对值。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

__all__ = ["price_boxes", "observed_text", "confidence", "price_box_problems"]

_MIN_SAMPLES = 3
_NO_MATCH = "暂无匹配样本"
_NO_ACTUAL = "暂无实盘样本"
_PRICE_RE = re.compile(r"^\d+P$")
_SUB_RE = re.compile(r"^来源：wm市场\|置信度\d+%$")
_OBS_RE = re.compile(r"^最近观测：\d+(小时|天)内$")


def confidence(n: int) -> int:
    """样本数 → 置信度%（与 ``riven_recommend._confidence`` 同口径：n=20 约满、封顶 99）。"""
    return min(99, round(max(0, int(n or 0)) / 20 * 100))


def _as_price(v: Any) -> str | None:
    """只认真实数字；None / 字符串 / 负数一律不当价格（不编数）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return None
    return "%dP" % int(round(float(v)))


def observed_text(ts: str | None, now: datetime | None = None) -> str:
    """参考卡口径的「最近观测：X小时内」；无时间戳不伪造。"""
    if not ts:
        return "最近观测：暂无"
    try:
        t = datetime.strptime(str(ts), "%Y-%m-%d %H:%M")
        secs = ((now or datetime.now()) - t).total_seconds()
        if secs < 0 or secs < 3600:
            return "最近观测：1小时内"
        hours = int(secs // 3600)
        if hours < 24:
            return "最近观测：%d小时内" % hours
        return "最近观测：%d天内" % (hours // 24)
    except (ValueError, TypeError):
        return "最近观测：%s" % ts


def price_boxes(market: dict | None) -> tuple[dict, dict]:
    """返回 (左盒, 右盒)；所有数字只来自真实市场快照。"""
    m = market or {}
    ok = m.get("status") == "ok"
    n = int(m.get("n") or 0)
    med = m.get("med")
    zero = m.get("zero")
    exact = m.get("exact") or {}
    rec = m.get("rec_neg") or {}
    tier = str(m.get("match_tier") or "").strip()
    zero_txt = _as_price(zero) or "暂无"

    if not ok or _as_price(med) is None:
        # 失败态：诚实写原因，不回退静态表、不写「暂无」冒充成功
        err = str(m.get("error") or "")
        return (
            {"title": "市场价格预计", "big": _NO_MATCH,
             "sub": "零洗中位价：", "sub_val": "暂无",
             "foot": "窗口样本不足" if not err else "数据获取失败"},
            {"title": "词条价差预估", "big": _NO_ACTUAL,
             "sub": "来源：wm市场|窗口样本不足", "foot": "模型预估 谨慎参考"},
        )

    med_i = int(round(float(med)))
    # 词条价差模型估值 = 同等级同分位中位 + 推荐负词条同分位溢价（都是真实推导值）
    delta = rec.get("delta")
    model = med_i + (int(round(float(delta)))
                     if isinstance(delta, (int, float)) and not isinstance(delta, bool)
                     else 0)
    model = max(1, model)

    # 匹配样本分层：L1 精确组合 → L2 同负词条层级 → L3 才「暂无匹配样本」
    if exact.get("status") == "ok" and _as_price(exact.get("median")):
        big = _as_price(exact["median"])
        ex_n = int(exact.get("n") or 0)
        foot = "匹配层级：正负面相同｜样本 %d 条" % ex_n
    elif n >= _MIN_SAMPLES:
        big = "%dP" % med_i
        foot = "匹配层级：%s｜样本 %d 条" % (tier or "同负词条", n)
    else:
        big = _NO_MATCH
        foot = _NO_MATCH

    left = {"title": "市场价格预计", "big": big,
            "sub": "零洗中位价：", "sub_val": zero_txt, "foot": foot}
    right = {"title": "词条价差预估", "big": "%dP" % model,
             "sub": "来源：wm市场|置信度%d%%" % confidence(n),
             "foot": "模型预估 谨慎参考"}
    return left, right


def price_box_problems(data: dict, market: dict | None) -> list[str]:
    """机器判定：把本次真实犯过的错放进来，任何一条命中即 FAIL。

    由 ``tools/check_riven_price_boxes.py`` 与单测共用，避免两处口径漂移。
    """
    p: list[str] = []
    m = market or {}
    left = data.get("price_left") or {}
    right = data.get("price_right") or {}
    ok = m.get("status") == "ok"
    n = int(m.get("n") or 0)
    med = m.get("med")
    zero = m.get("zero")
    exact = m.get("exact") or {}
    big = str(left.get("big") or "")
    sub = str(right.get("sub") or "")

    # ① 有真实匹配样本却写死「暂无匹配样本」（用户 2026-11-10 反馈的核心问题）
    has_match = ok and (exact.get("status") == "ok" or n >= _MIN_SAMPLES)
    if has_match and big == _NO_MATCH:
        p.append("[写死] 有 %d 条匹配样本，左盒 big 仍是「暂无匹配样本」" % n)
    if has_match and not _PRICE_RE.match(big):
        p.append("[格式] 有匹配样本时 big 必须是价格数字（如 380P），实得 %r" % big)
    if not has_match and big != _NO_MATCH:
        p.append("[编数] 无匹配样本却给出价格 %r" % big)

    # ② 零洗中位价必须来自 rank0 挂牌，不能拿 rank8 的 med 冒充；
    #    失败态一律「暂无」，不允许残留快照里的旧价格冒充成功。
    want_zero = (_as_price(zero) or "暂无") if ok else "暂无"
    if str(left.get("sub_val") or "") != want_zero:
        p.append("[口径] 零洗中位价应取 market.zero=%r → %s，实得 %r"
                 % (zero, want_zero, left.get("sub_val")))

    # ③ 左右两框必须是两套独立口径（参考卡 369P / 386P），不能填同一个数
    if ok and _as_price(med) and str(left.get("sub_val") or "") == str(right.get("big") or ""):
        p.append("[同值] 零洗中位价与词条价差预估是同一个数，未做独立口径")

    # ④ 置信度必须由样本数推导，且 sub 逐字对齐参考卡格式
    if ok:
        if not _SUB_RE.match(sub):
            p.append("[逐字] 右盒 sub 应形如「来源：wm市场|置信度80%%」，实得 %r" % sub)
        if "置信度%d%%" % confidence(n) not in sub:
            p.append("[写死] 置信度应由 n=%d 推导为 %d%%，实得 %r"
                     % (n, confidence(n), sub))
        if "样本 n=" in sub or "样本：" in sub:
            p.append("[逐字] 右盒 sub 不得自造「样本 n=」字段")

    # ⑤ 匹配层级必须显式标注（别人卡上有「匹配层级」字样）
    if has_match and "匹配层级" not in str(left.get("foot") or ""):
        p.append("[层级] 有匹配样本时 foot 必须标注匹配层级，实得 %r" % left.get("foot"))

    # ⑥ 最近观测是相对时间，不是绝对时间戳
    obs = str(data.get("observed") or "")
    if not _OBS_RE.match(obs) and obs != "最近观测：暂无":
        p.append("[逐字] observed 应为「最近观测：X小时内」，实得 %r" % obs)
    return p
