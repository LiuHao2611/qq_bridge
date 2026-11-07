# -*- coding: utf-8 -*-
"""本周执刑官猎杀卡（2026-11-05）回归：官方数据解析 / 像素对齐 / 门禁 / 指令。

prove-it 口径：守用户感受得到的行为——发口令能拿到正确的 BOSS、三阶段任务和
倒计时；出图与参考图的静态部分逐像素一致；超长数据不撑破版式。
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.plugins.qq_bridge.game_tools import warframe
from src.plugins.qq_bridge.pic.templates import archon as _archon

ROOT = Path(__file__).resolve().parents[1]

#: 线上真实抓取的官方 worldState.php LiteSorties 结构（不是手写臆造）
REAL_LITE = {
    "LiteSorties": [{
        "_id": {"$oid": "6ae7cefede137f9303d8e537"},
        "Activation": {"$date": {"$numberLong": "1793577600000"}},
        "Expiry": {"$date": {"$numberLong": "1794182400000"}},
        "Reward": "/Lotus/Types/Game/MissionDecks/ArchonSortieRewards",
        "Seed": 39264,
        "Boss": "SORTIE_BOSS_BOREAL",
        "Missions": [
            {"missionType": "MT_MOBILE_DEFENSE", "node": "SolNode79"},
            {"missionType": "MT_TERRITORY", "node": "SolNode39"},
            {"missionType": "MT_ASSASSINATION", "node": "SolNode24"},
        ],
    }],
}
REAL_NODES = {
    "SolNode79": {"value": "Cambria (Earth)", "enemy": "Grineer", "type": "Spy"},
    "SolNode39": {"value": "Everest (Earth)", "enemy": "Grineer", "type": "Excavation"},
    "SolNode24": {"value": "Oro (Earth)", "enemy": "Grineer", "type": "Assassination"},
}


def test_archon_data_parses_real_litestorties():
    """真实官方结构 → BOSS / 三阶段任务 / 节点 / 奖励必须与参考图一致。"""
    with patch.object(warframe, "_official_worldstate", new=AsyncMock(return_value=REAL_LITE)), \
         patch.object(warframe, "_solnodes", new=AsyncMock(return_value=REAL_NODES)), \
         patch.object(warframe, "_now", return_value=1794182400 - 3 * 86400 - 8 * 3600):
        d = asyncio.run(warframe.archon_hunt_data())
    assert d["boss_name"] == "BOSS-诡文枭主"
    assert d["shard"] == "蔚蓝源力石"
    assert d["quote"] == "如水千变化！万物皆利器！吃我三叉戟！"
    assert d["remain"] == "3天8小时"
    assert [m["mission"] for m in d["missions"]] == ["移动防御", "拦截", "刺杀"]
    assert [m["lv"] for m in d["missions"]] == ["Lv130-135", "Lv135-140", "Lv145-150"]
    assert [m["node"] for m in d["missions"]] == ["地球-Cambria", "地球-Everest", "地球-Oro"]
    assert [m["planet_en"] for m in d["missions"]] == ["Earth"] * 3


def test_archon_text_fallback_is_explicit():
    """取不到数据时给明确的兜底文本，不静默、不假装成功。"""
    with patch.object(warframe, "_official_worldstate", new=AsyncMock(return_value=None)):
        text = asyncio.run(warframe.archon_hunt_text())
    assert "暂时取不到" in text


def test_archon_card_fixed_size_and_no_overlap():
    """超长/空数据都不撑破固定 800x562 版式，且元素互不重叠。"""
    from src.plugins.qq_bridge.pic import resolve_theme
    long_data = {
        "boss_name": "BOSS-非常长的执刑官名字用来验证截断机制",
        "quote": "超长台词" * 12, "shard": "超长奖励名称" * 6,
        "remain": "6天23小时",
        "missions": [
            {"mission_key": "MT_SURVIVAL", "mission": "超长任务类型名称" * 3,
             "lv": "Lv130-135", "node": "扎里曼号-超长节点名称" * 3},
        ] * 3,
    }
    for data in (long_data, {}, None):
        boxes: list = []
        c = _archon.build(resolve_theme(None, "星际战甲·执刑官猎杀"),
                          data=data, boxes=boxes)
        assert c.img.size == (800, 562)
        # 所有内容包围盒都在画布内
        for label, x0, y0, x1, y1 in boxes:
            assert 0 <= x0 < x1 <= 800, (label, x0, x1)
            assert 0 <= y0 < y1 <= 562, (label, y0, y1)


def test_archon_gate_includes_pixel_diff():
    """门禁必须真的跑、且像素基准存在（防止换机器后静默跳过）。"""
    assert (ROOT / "tools" / "fixtures" / "ref_archon.png").is_file()
    script = ROOT / "tools" / "check_archon_layout.py"
    r = subprocess.run([sys.executable, str(script), "--selftest"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selftest: PASS" in r.stdout
    r = subprocess.run([sys.executable, str(script), "--check"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "像素差 0" in r.stdout


def test_archon_commands_reachable():
    """口令必须在游戏指令词表里（AI 模式下不会被陪聊吞掉）。"""
    from src.plugins.qq_bridge.game_tools import (NATURAL_CMDS, ZERO_ARG_OK,
                                                  is_game_command, _task_sub_intent)
    for word in ("执政官", "执刑官", "执行官", "猎杀", "archon"):
        assert word in NATURAL_CMDS and word in ZERO_ARG_OK
    assert is_game_command("执政官") and is_game_command("执刑官")
    assert _task_sub_intent("执政官", "") is None   # 不能被订阅功能抢走
