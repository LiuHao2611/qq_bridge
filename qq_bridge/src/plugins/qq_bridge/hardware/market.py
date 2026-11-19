# -*- coding: utf-8 -*-
"""hardware.market —— 行情口径与检索（只读 dataset，不碰 nonebot / 文件写）。

为什么单独一层：``dataset.py`` 负责「上游 CSV → Instrument」这种机械事实，
本模块负责**口径**——最新一期对比上一期、涨跌家数、榜首、模糊检索、卡面 payload。
两者拆开是为了：口径改动只动这里；上游换版只动 dataset；两边都能裸单测。
"""
from __future__ import annotations

import re

from .dataset import (
    FLAT_EPS, Dataset, DatasetError, Instrument, load, month_key, month_label,
)


# ---------------------------------------------------------------------------
# 汇总（卡面数据）
# ---------------------------------------------------------------------------
def _fmt_mover(i: Instrument) -> dict:
    return {"label": i.label, "sub": i.sub, "unit": i.unit,
            "prev": i.prev[1], "latest": i.latest, "prev_month": i.prev[0],
            "latest_month": i.latest_month, "pct": i.pct, "delta": i.delta}


def _bucket_stats(ds: Dataset, bucket: str, top: int = 3) -> tuple[dict, list]:
    """一个分类的最新期涨跌统计（只统计两期都有报价的标的）。返回 (summary, matched)。"""
    insts = ds.by_bucket(bucket)
    if not insts:
        raise DatasetError("分类 %s 没有任何标的" % bucket)
    months = sorted({m for i in insts for m, _ in i.points}, key=month_key)
    if len(months) < 2:
        raise DatasetError("分类 %s 只有一期数据，无法算涨跌" % bucket)
    latest, prev = months[-1], months[-2]
    matched = [i for i in insts if i.latest_month == latest and i.prev and i.prev[0] == prev]
    if not matched:
        raise DatasetError("分类 %s 在 %s / %s 没有可配对的标的" % (bucket, prev, latest))
    up, down, flat = [], [], []
    for i in matched:
        if i.pct is None:
            flat.append(i)
        elif i.pct > FLAT_EPS:
            up.append(i)
        elif i.pct < -FLAT_EPS:
            down.append(i)
        else:
            flat.append(i)
    gain = sorted(up, key=lambda i: i.pct, reverse=True)[:top]
    lose = sorted(down, key=lambda i: i.pct)[:top]
    summary = {
        "key": bucket,
        "name": insts[0].bucket_name,
        "latest": latest, "prev": prev,
        "tracked": len(insts),
        "active": sum(1 for i in insts if i.latest_month == latest),
        "matched": len(matched),
        "up": len(up), "down": len(down), "flat": len(flat),
        "gainers": [_fmt_mover(i) for i in gain],
        "losers": [_fmt_mover(i) for i in lose],
    }
    return summary, matched


def bucket_summary(ds: Dataset, bucket: str, top: int = 3) -> dict:
    return _bucket_stats(ds, bucket, top)[0]


def overview(ds: Dataset | None = None) -> dict:
    ds = ds or load()
    buckets, matched = [], []
    for key in ("gpu", "cpu", "mem"):
        s, m = _bucket_stats(ds, key)
        buckets.append(s)
        matched += m
    ups = sorted([i for i in matched if i.pct is not None and i.pct > FLAT_EPS],
                 key=lambda i: i.pct, reverse=True)[:3]
    downs = sorted([i for i in matched if i.pct is not None and i.pct < -FLAT_EPS],
                   key=lambda i: i.pct)[:3]
    # 各表截止月可以不同（上游 README 明说），卡面按分类各写各的月份，不强行对齐。
    notes = []
    for b in buckets:
        if b["up"] and not b["down"]:
            notes.append("%s %d 款两期可比，全部上涨" % (b["name"], b["matched"]))
        elif b["down"] and not b["up"]:
            notes.append("%s %d 款两期可比，全部下跌" % (b["name"], b["matched"]))
    return {
        "view": "overview",
        "asof": ds.data_asof,
        "days_old": ds.days_old,
        "commit": str(ds.meta.get("commit", ""))[:12],
        "buckets": buckets,
        "head_month": max((b["latest"] for b in buckets), key=month_key),
        "gainers": [_fmt_mover(i) for i in ups],
        "losers": [_fmt_mover(i) for i in downs],
        "tracked": sum(b["tracked"] for b in buckets),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
_ALIAS_PREFIX = ("RTX", "GTX", "RX", "ARC", "AMD", "NVIDIA", "INTEL")


def normalize(q: str) -> str:
    """统一大小写/空白/乘号/间隔点，并剥掉厂商前缀（用户常写 RTX 5090 / RX 9070XT）。"""
    s = str(q or "").upper().replace("×", "X").replace("*", "X").replace("·", "")
    s = re.sub(r"[\s\-_/]+", "", s)
    for p in _ALIAS_PREFIX:
        if s.startswith(p) and len(s) > len(p):
            s = s[len(p):]
            break
    return s


def _aliases(i: Instrument) -> list[str]:
    """一个标的的可检索写法。内存/存储额外给「整条容量」写法（DDR5 32G = 16G×2 的总量）。"""
    out = [i.label]
    if i.bucket == "mem":
        head = i.label.split("·")[0].strip()
        if i.sub:
            out.append(i.sub)                      # 内存 DDR5（整类）
            out.append("%s %s" % (i.sub.split()[0], i.spec))  # 内存 32G×2 / 固态 2TB
        if head:
            out.append(head)                       # DDR5（整类）
        if i.kit_gb:
            total = int(i.kit_gb) if float(i.kit_gb).is_integer() else i.kit_gb
            out.append("%s %sG" % (head, total))   # DDR5 32G（整条容量口径）
    return [a for a in out if a]


def search(q: str, ds: Dataset | None = None) -> list[Instrument]:
    """模糊查型号；按「完全相等 → 前缀 → 包含」打分，返回同分命中（调用方判歧义）。

    有一个 score=0（完全相等）的命中时只返回完全相等的那些 —— 用户写「5070」
    不应该被「5070Ti」抢走；写「5070Ti」也不会被「5070」污染。
    """
    ds = ds or load()
    nq = normalize(q)
    if not nq:
        return []
    scored = []
    for i in ds.instruments:
        best = None
        for a in _aliases(i):
            nl = normalize(a)
            if nl == nq:
                s = 0
            elif nl.startswith(nq):
                s = 1
            elif nq in nl:
                s = 2
            elif nl in nq:
                s = 3
            else:
                continue
            best = s if best is None else min(best, s)
        if best is not None:
            scored.append((best, len(i.label), i))
    if not scored:
        return []
    scored.sort(key=lambda x: (x[0], x[1], x[2].label))
    top = scored[0][0]
    if top <= 1:
        scored = [x for x in scored if x[0] == top]
    return [x[2] for x in scored]


def _chart_points(points: tuple, limit: int = 36) -> list[dict]:
    """走势图数据：t∈[0,1] 按**真实月份间距**映射（缺月不会被均分掩盖）。

    limit=36 覆盖上游全部历史（当前 22~24 期）——图内高低点必须等于「档案区间」，
    否则用户会看到图顶标注 ¥2,289 却被告知历史最高 ¥3,199，两张口径打架。
    """
    pts = list(points)[-limit:]
    if not pts:
        return []
    k0, k1 = month_key(pts[0][0]), month_key(pts[-1][0])
    span = (k1 - k0) or 1.0
    return [{"t": (month_key(m) - k0) / span, "p": v, "m": month_label(m)}
            for m, v in pts]


def detail(i: Instrument, ds: Dataset | None = None) -> dict:
    ds = ds or load()
    tier = ds.tiers.get("%s:%s" % (i.bucket, i.label))
    lo_m, lo_v = i.low
    hi_m, hi_v = i.high
    return {
        "view": "model",
        "asof": ds.data_asof,
        "days_old": ds.days_old,
        "bucket": i.bucket,
        "bucket_name": i.bucket_name,
        "label": i.label,
        # 内存/存储的 sub 是原始分类（与 label 重复），卡面副标题不再复述；价格行已标 元/GB
        "sub": "" if i.bucket == "mem" else i.sub,
        "unit": i.unit,
        "latest": i.latest,
        "latest_month": i.latest_month,
        "prev": i.prev[1] if i.prev else None,
        "prev_month": i.prev[0] if i.prev else "",
        "delta": i.delta,
        "pct": i.pct,
        "low": lo_v, "low_month": lo_m,
        "high": hi_v, "high_month": hi_m,
        "vs_low_pct": i.vs_low_pct,
        "kit_gb": i.kit_gb,
        "kit_price": (i.latest * i.kit_gb) if i.kit_gb else None,
        "chart": _chart_points(i.points),
        "tier": ({"tier": tier.tier, "vendor": tier.vendor, "extra": tier.extra,
                  "score_main": tier.score_main, "score_sub": tier.score_sub,
                  "note": tier.note} if tier else None),
    }
