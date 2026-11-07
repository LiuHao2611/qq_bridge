# -*- coding: utf-8 -*-
"""平原状态卡片（#平原）回归测试。

覆盖：
  * 倒计时解析（_parse_timestr / _fmt_dur_full）带秒中文格式；
  * _wfwiki_cycles 解析 Cambion(Fass/Vome) 与 Duviri(情绪) 为中文 + 下一阶段
    （数据源 worldstate.wf.wiki 的 cycles 数组，expiry 毫秒）；
  * daynight_data 四行结构（地球&夜灵平野 / 福尔图娜 / 魔胎之境 / 双衍王境）；
  * 魔胎/双衍取不到时优雅降级为「暂无 / 进游戏看」，版式不崩；
  * plain 模板对正常与降级数据都能成功渲染出合法 PNG。
"""
import asyncio
import time
from unittest.mock import AsyncMock, patch

from src.plugins.qq_bridge.game_tools.warframe import (
    _fmt_dur_full,
    _parse_timestr,
    _wfwiki_cycles,
    daynight_data,
)
from src.plugins.qq_bridge.pic import render_sync


def _iso(future_sec: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(future_sec, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _mock_cycles_html(cam_key: str, dv_key: str,
                      cam_expiry_ms: int, dv_expiry_ms: int) -> str:
    """构造 worldstate.wf.wiki 的 flight 片段（cycles 数组，escaped 引号）。"""
    cam_state = "Fass" if cam_key == "fass" else "Vome"
    dv_state = {"joy": "欢愉", "anger": "愤怒", "envy": "嫉妒",
                "sorrow": "悲伤", "fear": "恐惧"}[dv_key]
    cycles = (
        '{\\"id\\":\\"cambion\\",\\"label\\":\\"魔胎之境\\",'
        '\\"state\\":\\"' + cam_state + '\\",\\"stateKey\\":\\"' + cam_key + '\\",'
        '\\"totalMs\\":6000000,\\"dialPhases\\":[{\\"key\\":\\"fass\\",\\"ms\\":6000000},'
        '{\\"key\\":\\"vome\\",\\"ms\\":3000000}],\\"expiry\\":' + str(cam_expiry_ms) + '},'
        '{\\"id\\":\\"duviri\\",\\"label\\":\\"双衍王境\\",'
        '\\"state\\":\\"' + dv_state + '\\",\\"stateKey\\":\\"' + dv_key + '\\",'
        '\\"totalMs\\":7200000,\\"dialPhases\\":\\"$undefined\\",'
        '\\"expiry\\":' + str(dv_expiry_ms) + '}'
    )
    return 'self.__next_f.push([1,"xxx\\"cycles\\":[' + cycles + ']yyy"])'
    # 注：\" 在运行时即代表一个被转义的引号，_extract_flight_json 据此定位 cycles。


def _reset_cyc_cache():
    import src.plugins.qq_bridge.game_tools.warframe as wf
    wf._WFWIKI_CYC_CACHE.update({"ts": 0.0, "data": None, "fail_ts": 0.0})


def test_parse_timestr():
    assert _parse_timestr("1h 4m 8s") == 3848
    assert _parse_timestr("4m 5s") == 245
    assert _parse_timestr("43m 57s") == 2637
    assert _parse_timestr("") == 0


def test_fmt_dur_full():
    assert _fmt_dur_full(3848) == "1小时4分8秒"
    assert _fmt_dur_full(245) == "4分5秒"
    assert _fmt_dur_full(2637) == "43分57秒"
    assert _fmt_dur_full(None) == "进游戏看"


def test_wfwiki_cycles_parses():
    _reset_cyc_cache()
    html = _mock_cycles_html("fass", "sorrow",
                             int((time.time() + 3700) * 1000),
                             int((time.time() + 2600) * 1000))
    with patch(
        "src.plugins.qq_bridge.game_tools.warframe._wfwiki_html",
        new=AsyncMock(return_value=html),
    ):
        cyc = asyncio.run(_wfwiki_cycles())
    assert cyc["cambion"]["state"] == "Fass"
    assert cyc["cambion"]["next"] == "Vome"
    assert cyc["duviri"]["state"] == "悲伤"
    assert cyc["duviri"]["next"] == "恐惧"
    assert cyc["cambion"]["remain"] > 3600
    assert 2500 < cyc["duviri"]["remain"] < 2700


def test_daynight_data_full():
    _reset_cyc_cache()
    now = time.time()
    dn = {
        "time": 0,
        "data": [
            {"id": "cetus", "start": now - 100, "length": 9000, "dayStart": 0, "dayEnd": 4500},
            {"id": "fortuna", "start": now - 100, "length": 1600, "dayStart": 800, "dayEnd": 1200},
        ],
    }
    html = _mock_cycles_html("vome", "fear",
                             int((time.time() + 3700) * 1000),
                             int((time.time() + 100) * 1000))
    with patch(
        "src.plugins.qq_bridge.game_tools.warframe._worldstate",
        new=AsyncMock(return_value={"daynight": dn}),
    ), patch(
        "src.plugins.qq_bridge.game_tools.warframe._wfwiki_html",
        new=AsyncMock(return_value=html),
    ):
        data = asyncio.run(daynight_data())
    rows = data["rows"]
    assert len(rows) == 4
    assert rows[0]["name"] == "地球&夜灵平野"
    assert rows[0]["state"] == "白天" and rows[0]["kind"] == "blue"
    assert rows[0]["next"] == "夜晚"
    # fortuna：t=0 不在 [800,1200) → 寒冷（紫）
    assert rows[1]["name"] == "福尔图娜"
    assert rows[1]["state"] == "寒冷" and rows[1]["kind"] == "purple"
    assert rows[1]["next"] == "温暖"
    # cambion：vome → 下一阶段 Fass
    assert rows[2]["name"] == "魔胎之境"
    assert rows[2]["state"] == "Vome" and rows[2]["next"] == "Fass"
    # duviri：fear → 下一阶段 欢愉（循环回首位）
    assert rows[3]["name"] == "双衍王境"
    assert rows[3]["state"] == "恐惧" and rows[3]["next"] == "欢愉"
    assert "秒" in rows[3]["remain"]
    assert data["source"] == "Tenno.Tools + wf.wiki 世界状态"


def test_daynight_data_fallback():
    """魔胎/双衍取不到 → 暂无 / 进游戏看，版式不崩。"""
    _reset_cyc_cache()
    dn = {
        "time": 0,
        "data": [
            {"id": "cetus", "start": 0, "length": 9000, "dayStart": 0, "dayEnd": 4500},
        ],
    }
    with patch(
        "src.plugins.qq_bridge.game_tools.warframe._worldstate",
        new=AsyncMock(return_value={"daynight": dn}),
    ), patch(
        "src.plugins.qq_bridge.game_tools.warframe._wfwiki_html",
        new=AsyncMock(return_value=None),
    ):
        data = asyncio.run(daynight_data())
    rows = data["rows"]
    assert len(rows) == 4
    assert rows[2]["state"] == "暂无" and rows[2]["remain"] == "进游戏看"
    assert rows[3]["state"] == "暂无" and rows[3]["remain"] == "进游戏看"
    assert "进游戏看" in data["source"]


def test_template_renders_normal():
    rows = [
        {"name": "地球&夜灵平野", "icon": "#3FA34D", "state": "白天", "kind": "blue",
         "next": "夜晚", "remain": "1小时4分8秒"},
        {"name": "福尔图娜", "icon": "#E67E22", "state": "寒冷", "kind": "purple",
         "next": "温暖", "remain": "4分5秒"},
        {"name": "魔胎之境", "icon": "#B3412E", "state": "Fass", "kind": "blue",
         "next": "Vome", "remain": "1小时4分8秒"},
        {"name": "双衍王境", "icon": "#16A085", "state": "悲伤", "kind": "blue",
         "next": "恐惧", "remain": "43分57秒"},
    ]
    png = render_sync("plain", None, "x", rows=rows, title="平原状态和时间",
                      footer_name="曼珠")
    assert isinstance(png, bytes) and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_template_renders_fallback():
    rows = [
        {"name": "地球&夜灵平野", "icon": "#3FA34D", "state": "白天", "kind": "blue",
         "next": "夜晚", "remain": "1小时4分8秒"},
        {"name": "福尔图娜", "icon": "#E67E22", "state": "暂无", "kind": "blue",
         "next": "温暖", "remain": "进游戏看"},
        {"name": "魔胎之境", "icon": "#B3412E", "state": "暂无", "kind": "blue",
         "next": "Vome", "remain": "进游戏看"},
        {"name": "双衍王境", "icon": "#16A085", "state": "暂无", "kind": "blue",
         "next": "恐惧", "remain": "进游戏看"},
    ]
    png = render_sync("plain", None, "x", rows=rows, title="平原状态和时间",
                      footer_name="曼珠")
    assert isinstance(png, bytes) and png[:8] == b"\x89PNG\r\n\x1a\n"
