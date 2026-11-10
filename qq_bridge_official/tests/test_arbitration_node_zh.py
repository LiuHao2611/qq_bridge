"""仲裁节点解析回归测试。

背景：wf.wiki 世界状态页已重构，不再内嵌 ArbitersSyndicate 节点池，旧
_extract_arb_pool 恒返回 None，导致仲裁卡片退化成 SolNode132 / ?系 / （300|C+）。
修复后改用 WFCD solNodes.json 解析 SolNode ID → 节点名/星球/派系/任务类型。

本测试不依赖网络：直接读取本地 data/solNodes.json（与运行时同源）。
"""
import json
from pathlib import Path

import pytest

import src.plugins.qq_bridge.game_tools.warframe as wf

_DATA = Path(__file__).resolve().parents[1] / "data" / "solNodes.json"


@pytest.fixture(scope="module")
def sn():
    return json.loads(_DATA.read_text(encoding="utf-8"))


def test_parse_solnode_value():
    assert wf._parse_solnode_value("Bode (Ceres)") == ("Bode", "Ceres")
    assert wf._parse_solnode_value("Stickney (Phobos)") == ("Stickney", "Phobos")
    # 无括号时系统名为空，节点名取原值
    assert wf._parse_solnode_value("SomeNode") == ("SomeNode", "")


def test_arb_info_resolves_real_names(sn):
    """关键回归：不再退回原始 SolNode ID 与 ? 派系。"""
    info = wf._arb_info_from_solnodes("SolNode132", sn)  # 谷神星 Bode / Grineer / Spy
    assert info["node"] == "Bode"
    assert info["system"] == "谷神星"
    assert info["faction"] == "Grineer"
    assert info["mission"] == "Spy"
    # 不允许出现原始 ID 作为节点名
    assert "SolNode" not in info["node"]


def test_arb_info_unknown_graceful(sn):
    """未知 SolNode 时优雅降级：节点名退回 ID，系统/派系/任务空（不崩溃）。"""
    info = wf._arb_info_from_solnodes("SolNode999999", sn)
    assert info["node"] == "SolNode999999"
    assert info["system"] == ""
    assert info["faction"] == ""
    assert info["mission"] == ""


def test_current_line_format():
    line = wf._arb_current_line("谷神星", "Bode", "Grineer", "Spy")
    assert line == "谷神星Bode | G系间谍"


def test_future_line_format():
    line = wf._arb_future_line("Corpus", "海王星", "Extermination", 300, "C+")
    assert line == "C系海王星歼灭（300|C+）"


def test_extermination_translated_and_efficiency():
    # Extermination 应翻译为 歼灭 且效率命中 (300, C+)
    assert wf._zh_mission("Extermination") == "歼灭"
    val, tier = wf._arb_efficiency("Extermination", "")
    assert (val, tier) == (300, "C+")


def test_faction_letter():
    assert wf._arb_faction_letter("Grineer") == "G"
    assert wf._arb_faction_letter("Corpus") == "C"
    assert wf._arb_faction_letter("Infested") == "I"
    assert wf._arb_faction_letter("") == "?"
