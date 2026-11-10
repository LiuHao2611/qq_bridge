# -*- coding: utf-8 -*-
"""赋能（Arcane）等级列二值化回归测试（2026-11-10「赋能满级展示满级，否则 0级」）。

赋能与 MOD 交易语义不同：只有「满级成品(R5)」和「未满级原料(R0~R4)」两类有独立
价值，R1~R4 是合成中间态。故赋能等级列只出现两种标记：满级 / 0级，绝不显示 R1~R4。
独立成文件（新文件 <20KB），不挤占存量 test_warframe_market_rank.py 的棘轮余量。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _sell(uid, rank, plat):
    o = {"type": "sell", "platinum": plat, "quantity": 1,
         "user": {"id": uid, "ingameName": uid, "status": "ingame", "avatar": ""}}
    if rank is not None:
        o["rank"] = rank
    return o


def _mock(monkeypatch, orders, tags, max_rank, url_name="arcane_energize"):
    """把 market_text 外呼收敛为离线假数据；tags/maxRank 决定赋能 vs 普通物品。

    真实 _market_en_name 会把命中的 set 成员写进 _MARKET_META_CACHE（含 tags/maxRank），
    market_text 再从该缓存取 item_detail —— mock 必须复刻这条缓存链路。
    """
    from src.plugins.qq_bridge.game_tools import warframe

    hit = {"slug": url_name, "tags": tags, "maxRank": max_rank,
           "i18n": {"en": {"name": "Arcane Energize"}}}

    async def fake_market_get(url, params=""):
        if "/v2/orders/item/" in url:
            return {"data": orders}
        if "/statistics" in url:
            return {"payload": {}}
        if "/v2/item/" in url and "/set" in url:
            return {"data": {"items": [hit]}}
        return None

    async def fake_en_name(url):
        warframe._MARKET_META_CACHE[url] = hit  # 复刻真实实现的缓存填充
        return "Arcane Energize"

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    monkeypatch.setattr(warframe, "_market_en_name", fake_en_name)
    warframe._MARKET_META_CACHE.clear()
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})
    return warframe


ARCANE_TAGS = ["legendary", "arcane_enhancement"]
HELMET_TAGS = ["arcane_helmet", "skin"]


def test_arcane_binarizes_rank_full_vs_zero(monkeypatch):
    """赋能 R5→满级，R0~R4 一律 0级；表头 6 列含等级；绝不出现 R1~R4。"""
    wf = _mock(monkeypatch, [
        _sell("A", 0, 7), _sell("B", 1, 15), _sell("C", 3, 90),
        _sell("D", 4, 200), _sell("E", 5, 900), _sell("F", 5, 950),
    ], ARCANE_TAGS, 5)
    r = asyncio.run(wf.market_text("赋能充沛"))
    lines = r.split("\n")
    assert next(ln for ln in lines if ln.startswith("@hdr|")) == \
        "@hdr|卖家|等级|状态|声望|数量|白金价格"
    marks = [ln.split("|")[2] for ln in lines if ln.startswith("@row|")]
    assert marks == ["0级", "0级", "0级", "0级", "满级", "满级"], \
        "赋能须二值化：R0~R4→0级、R5→满级，实得 %s" % marks
    assert not any(m.startswith("R") for m in marks), "赋能不得出现 R1~R4 中间态"


def test_arcane_full_rank_filter_still_works(monkeypatch):
    """显式「满级」仍过滤到 R5，且每行标满级（过滤态口径一致）。"""
    wf = _mock(monkeypatch, [
        _sell("A", 0, 7), _sell("B", 4, 200), _sell("C", 5, 900), _sell("D", 5, 950),
    ], ARCANE_TAGS, 5)
    r = asyncio.run(wf.market_text("赋能充沛 满级"))
    rows = [ln.split("|") for ln in r.split("\n") if ln.startswith("@row|")]
    assert len(rows) == 2, "满级应只过滤 R5"
    assert {c[2] for c in rows} == {"满级"}
    assert "满级" in next(ln for ln in r.split("\n") if ln.startswith("@wmhdr|"))


def test_arcane_helmet_not_mistaken_for_arcane(monkeypatch):
    """赋能头盔（arcane_helmet+skin，无 maxRank）不得被赋能二值化逻辑误伤。"""
    wf = _mock(monkeypatch, [_sell("A", None, 55), _sell("B", None, 60)], HELMET_TAGS, None)
    r = asyncio.run(wf.market_text("赋能头盔"))
    hdr = next(ln for ln in r.split("\n") if ln.startswith("@hdr|"))
    assert hdr == "@hdr|卖家|状态|声望|数量|白金价格", "头盔无等级维度，保持 5 列"


def test_plain_mod_keeps_per_rank_labels(monkeypatch):
    """普通 MOD 不受赋能改动影响，仍零级/R1/满级逐档显示（零回归）。"""
    wf = _mock(monkeypatch, [
        _sell("A", 0, 101), _sell("B", 1, 110), _sell("C", 3, 134),
    ], ["mod"], 3)
    r = asyncio.run(wf.market_text("重锤射击"))
    marks = [ln.split("|")[2] for ln in r.split("\n") if ln.startswith("@row|")]
    assert marks == ["零级", "R1", "满级"], "MOD 仍逐档，实得 %s" % marks


def test_arcane_fallback_when_item_detail_missing(monkeypatch):
    """详情被风控（_MARKET_META_CACHE 空）时，赋能仍靠 url 前缀二值化；头盔不误伤。"""
    wf = _mock(monkeypatch, [_sell("A", 1, 15), _sell("B", 5, 900)], [], None)
    # 抹掉 en_name 对缓存的填充，模拟详情接口失败
    warframe = wf
    async def fake_en_name_nocache(url):
        return "Arcane Energize"
    warframe._market_en_name = fake_en_name_nocache
    warframe._MARKET_META_CACHE.clear()
    r = asyncio.run(warframe.market_text("赋能充沛"))
    marks = [ln.split("|")[2] for ln in r.split("\n") if ln.startswith("@row|")]
    assert marks == ["0级", "满级"], "详情缺失时赋能仍须二值化，实得 %s" % marks

    # 头盔：url 含 _helmet，即使详情空也不能被当赋能
    wf2 = _mock(monkeypatch, [_sell("A", None, 55)], [], None, "arcane_chorus_helmet")
    async def fake_en_name2(url):
        return "Arcane Chorus Helmet"
    wf2._market_en_name = fake_en_name2
    wf2._MARKET_META_CACHE.clear()
    r2 = asyncio.run(wf2.market_text("赋能头盔"))
    hdr = next(ln for ln in r2.split("\n") if ln.startswith("@hdr|"))
    assert hdr == "@hdr|卖家|状态|声望|数量|白金价格", "头盔即便详情缺失也须保持 5 列"
