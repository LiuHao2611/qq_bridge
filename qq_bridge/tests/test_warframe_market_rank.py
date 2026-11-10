# -*- coding: utf-8 -*-
"""MOD「等级」列回归测试（2026-11-10「遇到有等级的物品时应该展示物品等级」）。

从 test_warframe_market_card.py 按功能拆出：该文件是存量 >20KB，等级列的 6 个测试
若留在里面会撞 20KB 上限；拆到本文件（<20KB）后两边都遵守项目棘轮纪律。

守用户看得见的行为（prove-it）：
  * MOD 表格 6 列含「等级」，每行胶囊标 零级/R1/满级，且真实混排；
  * 非 MOD（5 列）不混入等级列 —— 旧卡 / editor 预览 / 夹具向后兼容；
  * 等级门禁自身的 4 类反例注入会被拦；
  * market_text 默认全等级混排、显式等级仍过滤且逐行标级、非 MOD 保持 5 列。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import market_rank_gate as _rg  # noqa: E402
import check_market_currency_layout as gate  # noqa: E402

_render = gate._render
check_boxes = gate.check_boxes
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _ranked_boxes():
    return _rg._render_ranked("light")


def test_mod_card_has_rank_column_every_row_and_mixed_ranks():
    """MOD 6 列卡：表头恰好 1 个「等级」、每行 1 枚等级胶囊、零级/R1/满级确实混排。"""
    c, boxes = _ranked_boxes()
    assert _rg.rank_problems(boxes, 10, {"零级", "R1", "满级"}) == []
    labels = {b[0].split(":", 1)[1] for b in boxes if b[0].startswith("tbl.row.rank:")}
    assert labels == {"零级", "R1", "满级"}, "等级应真实混排，实得 %s" % labels
    assert check_boxes((16, 16, c.width - 16, c.height - 16), boxes) == []
    assert c.to_bytes()[:8] == PNG_MAGIC


def test_non_mod_five_col_card_has_no_rank_column():
    """非 MOD（5 列）卡不得混入等级列 —— 向后兼容旧卡 / editor 预览 / 夹具。"""
    _, boxes = _render("light", "100")
    assert _rg.rank_problems(boxes, 0, set()) == []
    assert not any(b[0].startswith("tbl.row.rank") or b[0] == "tbl.hdr.rank"
                   for b in boxes)


def test_rank_gate_selftest_catches_injected_regressions():
    """等级门禁自己也要被测：漏表头/漏行/单一等级/非MOD混入 4 类反例必须全被拦。"""
    _, plain = _render("light", "100")
    assert _rg.selftest_rank(plain) == []


def _mock_wm_orders(monkeypatch, orders):
    """把 market_text 的三个外呼收敛成离线假数据（订单/统计/英文名/详情）。"""
    from src.plugins.qq_bridge.game_tools import warframe

    async def fake_market_get(url, params=""):
        if "/v2/orders/item/" in url:
            return {"data": orders}
        if "/v1/items/" in url and "/statistics" in url:
            # 带 mod_rank 的真实档位：验证混排统计不按单一 rank 过滤 + 「全等级/零级」前缀
            closed = [{"min_price": 100, "max_price": 150, "median": 134, "mod_rank": 0},
                      {"min_price": 110, "max_price": 160, "median": 140, "mod_rank": 3}]
            return {"payload": {
                "statistics_closed": {"48hours": closed, "90days": closed},
                "statistics_live": {"48hours": [], "90days": []},
            }}
        if "/v2/item/" in url:
            return {"data": {"items": [{"tags": ["mod"], "icon": "",
                                        "i18n": {"en": {"name": "Hammer Shot"}}}]}}
        return None

    async def fake_en_name(url):
        return "Hammer Shot"

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    monkeypatch.setattr(warframe, "_market_en_name", fake_en_name)
    warframe._MARKET_META_CACHE.clear()
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})
    return warframe


def _sell(uid, rank, plat):
    o = {"type": "sell", "platinum": plat, "quantity": 1,
         "user": {"id": uid, "ingameName": uid, "status": "ingame", "avatar": ""}}
    if rank is not None:  # 真实 WM：非 MOD 订单不带 rank 键
        o["rank"] = rank
    return o


def test_market_text_default_mixes_all_ranks_and_marks_each_row(monkeypatch):
    """用户「全部等级混排」：未指定等级时 R0/R1/R3 全进表，每行带等级标记，
    表头 6 列含「等级」；统计前缀「全等级」且不按单一 rank 过滤。"""
    wf = _mock_wm_orders(monkeypatch, [
        _sell("A", 0, 101), _sell("B", 1, 110), _sell("C", 3, 134), _sell("D", 3, 140),
    ])
    r = asyncio.run(wf.market_text("重锤射击"))
    lines = r.split("\n")
    hdr = next(ln for ln in lines if ln.startswith("@hdr|"))
    assert hdr == "@hdr|卖家|等级|状态|声望|数量|白金价格"
    rows = [ln for ln in lines if ln.startswith("@row|")]
    assert len(rows) == 4, "未指定等级不应只显示零级，应全等级混排"
    fields = [ln.split("|") for ln in rows]
    assert all(f[0] == "@row" for f in fields)
    marks = [f[2] for f in fields]
    assert set(marks) == {"零级", "R1", "满级"}, "每行应标真实等级，实得 %s" % marks
    # @row 前缀 + 6 可见列 + uid + 头像 = 9 段；末两段 uid/头像固定
    assert all(len(f) == 9 for f in fields), "MOD 行应 6 可见列+uid+头像"
    assert all(f[-2] in {"A", "B", "C", "D"} and f[-1] == "" for f in fields), \
        "uid/头像须固定在末两列（5/6 列通用）"
    mstat = [ln for ln in lines if ln.startswith("@mstat|")]
    assert mstat and all("全等级" in ln for ln in mstat), "混排统计前缀应标「全等级」"


def test_market_text_explicit_rank_still_filters_but_marks_rank(monkeypatch):
    """显式「满级/R0」仍按等级过滤，但过滤态每行也照标等级（口径一致）。"""
    wf = _mock_wm_orders(monkeypatch, [
        _sell("A", 0, 101), _sell("B", 1, 110), _sell("C", 3, 134), _sell("D", 3, 140),
    ])
    r = asyncio.run(wf.market_text("重锤射击 满级"))
    rows = [ln.split("|") for ln in r.split("\n") if ln.startswith("@row|")]
    assert len(rows) == 2, "满级应只过滤出 R3 的两条"
    assert {c[2] for c in rows} == {"满级"}, "过滤态也应逐行标等级"
    assert "满级" in next(ln for ln in r.split("\n") if ln.startswith("@wmhdr|"))


def test_market_text_non_mod_stays_five_columns(monkeypatch):
    """非 MOD（武器/部件）无 rank 维度，@hdr 仍 5 列、@row 不插等级字段。"""
    wf = _mock_wm_orders(monkeypatch, [_sell("A", None, 55), _sell("B", None, 60)])
    r = asyncio.run(wf.market_text("绝路 Prime"))
    hdr = next(ln for ln in r.split("\n") if ln.startswith("@hdr|"))
    assert hdr == "@hdr|卖家|状态|声望|数量|白金价格"
    rows = [ln for ln in r.split("\n") if ln.startswith("@row|")]
    assert rows and all(len(ln.split("|")) == 8 for ln in rows), "非 MOD 应 5 可见列+uid+头像"
    assert "零级" in next(ln for ln in r.split("\n") if ln.startswith("@mstat|"))
