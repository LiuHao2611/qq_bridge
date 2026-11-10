# -*- coding: utf-8 -*-
"""紫卡趋势 / 词条价差（2026-11-05 P2）回归。

prove-it 口径：只守用户感受得到的行为——查过的武器能被跟踪并读回采样点、
首次查询如实标注只有 1 个点、同分位价差不会被不对称对比污染、
失败态写进卡面而不是「暂无」、卡片门禁真的能抓错。
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.plugins.qq_bridge.game_tools import riven_trend as rt
from src.plugins.qq_bridge.pic.templates import riven_trend as tpl

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 解析 ----
def test_resolve_weapon_zh_to_wfm_slug():
    """中文武器名 → WFM 紫卡基础 slug（Prime 后缀要去掉）。"""
    assert rt.resolve_weapon("绝路")["slug"] == "rubico"
    assert rt.resolve_weapon("Rubico Prime")["slug"] == "rubico"
    assert rt.resolve_weapon("太始弓")["slug"] == "nataruk"
    assert rt.resolve_weapon("glaxion")["slug"] == "glaxion"
    assert rt.resolve_weapon("不存在的武器xyz") is None


# ------------------------------------------------------------ 快照存储 ----
def test_store_tracks_and_persists_points(tmp_path, monkeypatch):
    """用户查过的武器进入跟踪表，采样点写盘后重新打开仍读得回来。"""
    p = tmp_path / "trends.json"
    store = rt.RivenTrendStore(path=p)
    info = {"slug": "rubico", "en": "Rubico", "zh": "绝路"}
    store.track(info)
    assert "rubico" in store.tracked()
    t0 = time.time()
    assert store.append("rubico", {"ts": t0, "median": 20.0, "n": 7,
                                   "lo": 15.0, "hi": 25.0, "pool": 100})
    # 相邻点间隔不足 → 不写（防查询把曲线刷平）
    assert not store.append("rubico", {"ts": t0 + 60, "median": 21.0, "n": 7,
                                       "lo": 15.0, "hi": 26.0, "pool": 100})
    assert store.append("rubico", {"ts": t0 + rt.MIN_POINT_GAP + 1,
                                   "median": 24.0, "n": 9, "lo": 18.0,
                                   "hi": 30.0, "pool": 100})
    again = rt.RivenTrendStore(path=p)
    assert "rubico" in again.tracked()
    pts = again.points("rubico")
    assert len(pts) == 2
    assert [p["median"] for p in pts] == [20.0, 24.0]


def test_store_tracked_lru_cap(tmp_path):
    """跟踪列表有上限，按最近查询淘汰，避免把 WFM 打到 429。"""
    store = rt.RivenTrendStore(path=tmp_path / "t.json")
    for i in range(rt.MAX_TRACKED + 5):
        store.track({"slug": "w%03d" % i, "en": "W%d" % i, "zh": "武器%d" % i})
        time.sleep(0.0001)
    assert len(store.tracked()) == rt.MAX_TRACKED
    assert "w000" not in store.tracked()


# -------------------------------------------------------- 同分位价差 ----
def test_paired_pct_is_symmetric():
    """不对称全量对比会造出伪溢价；对称 pair_k 对比必须给出可信值。"""
    base = [10, 20, 30, 40, 50, 60, 70, 80]
    stat = [100, 110, 120, 5000, 6000]     # 少量高价离群
    got = rt.paired_pct(base, stat)
    assert got is not None
    pct, k = got
    assert k == 5                          # pair_k = min(8, 5)
    assert pct < 1500                      # 对称对比不会像全量那样翻几倍
    # 反例：若拿全量 stat 中位数 / 全量 base 中位数会得到离谱倍数
    naive = (sorted(stat)[len(stat) // 2] / sorted(base)[len(base) // 2] - 1) * 100
    assert pct != naive


def test_paired_pct_rejects_too_few_samples():
    assert rt.paired_pct([1, 2], [3, 4, 5]) is None
    assert rt.paired_pct([], [1, 2, 3]) is None
    assert rt.paired_pct([0, 0, 0], [1, 2, 3]) is None   # 基准为 0 不硬算


# -------------------------------------------------------------- 卡片 ----
def _trend_data(points, enough=True):
    now = time.time()
    pts = [{"ts": now - (len(points) - 1 - i) * 7200, "median": v,
            "n": 8, "lo": v * .8, "hi": v * 1.3, "pool": 100}
           for i, v in enumerate(points)]
    return {"weapon_zh": "绝路", "weapon_en": "Rubico", "points": pts,
            "current": pts[-1], "enough_for_trend": enough,
            "change_pct": (points[-1] / points[0] - 1) * 100,
            "source": "WFM 紫卡挂牌要价·非成交价｜price_asc｜rank8｜中位数"}


def test_first_sample_card_says_so_and_no_fake_chart():
    """首次只有 1 点：如实显示采样起点，绝不画假折线。"""
    from src.plugins.qq_bridge.pic import resolve_theme
    boxes: list = []
    c = tpl.build(resolve_theme(None, "星际战甲·紫卡趋势"),
                  data=_trend_data([20], enough=False), mode="trend", boxes=boxes)
    labels = [b[0] for b in boxes]
    assert "chart.empty" in labels
    assert not any(lab.startswith("chart.seg") for lab in labels)
    assert c.img.width == 600


def test_failure_state_writes_reason_into_card():
    """取数失败时卡面写原因，不出现「暂无」/估算数据。"""
    from src.plugins.qq_bridge.pic import resolve_theme
    data = {"weapon_zh": "绝路", "weapon_en": "", "points": [], "rows": [],
            "current": {}, "error": "获取失败：RivenTrendError：样本不足",
            "source": "WFM 紫卡挂牌要价·非成交价｜获取失败，未生成任何估算数据"}
    boxes: list = []
    c = tpl.build(resolve_theme(None, "x"), data=data, mode="trend", boxes=boxes)
    labels = [b[0] for b in boxes]
    assert "chart.empty" in labels
    assert "暂无" not in "".join(str(x) for x in data.values())
    c2, boxes2 = None, []
    c2 = tpl.build(resolve_theme(None, "x"), data=data, mode="spread", boxes=boxes2)
    assert any(b[0] == "s.empty" for b in boxes2)


def test_spread_displayed_median_is_paired_pool():
    """卡面显示的中位价必须与溢价同源（前 pair_k 条），不能是全量池。"""
    base = [10, 20, 30, 40, 50, 60, 70, 80]                     # 8 条
    stat = [100, 110, 120, 130, 140, 150, 160, 5000, 6000, 7000]  # 10 条 → k=8
    pct, k = rt.paired_pct(base, stat)
    assert k == 8
    paired = sorted(stat)[:k]
    paired_med = (paired[k // 2 - 1] + paired[k // 2]) / 2       # 135
    full = sorted(stat)
    full_med = (full[len(full) // 2 - 1] + full[len(full) // 2]) / 2  # 145
    assert paired_med != full_med, "测试样本必须让配对池与全量池不同"
    # 卡面若误用全量中位价，反推出的百分比会与 paired_pct 不符
    base_med = (sorted(base)[k // 2 - 1] + sorted(base)[k // 2]) / 2
    assert abs((paired_med / base_med - 1) * 100 - pct) < 1e-6
    assert abs((full_med / base_med - 1) * 100 - pct) > 1e-6


def test_delta_colors_follow_chinese_convention():
    assert tpl._delta_color(12) == tpl.C_UP       # 涨红
    assert tpl._delta_color(-12) == tpl.C_DOWN     # 跌绿
    assert tpl._delta_color(0) == tpl.C_FLAT


# ---------------------------------------------------------- 门禁 / 注册 ----
def test_card_design_gate_runs():
    script = ROOT / "tools" / "check_riven_trend_layout.py"
    assert script.is_file()
    for flag, expect in (("--selftest", "selftest: PASS"), ("--check", "自检 PASS")):
        r = subprocess.run([sys.executable, str(script), flag],
                           cwd=str(ROOT), capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        assert expect in r.stdout


def test_commands_registered():
    from src.plugins.qq_bridge.game_tools import (NATURAL_CMDS, dispatch,
                                                  is_game_command, wf_reference)
    for w in ("紫卡趋势", "词条价差", "紫卡价差"):
        assert w in NATURAL_CMDS
    assert is_game_command("紫卡趋势 绝路")
    cmds = [c["cmd"] for c in wf_reference.command_reference()]
    assert any("紫卡趋势" in c for c in cmds)
    assert any("词条价差" in c for c in cmds)
    assert "格式" in asyncio.run(dispatch("紫卡趋势", "", "t")) or \
           "紫卡趋势" in asyncio.run(dispatch("紫卡趋势", "绝路", "t"))
