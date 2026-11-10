# -*- coding: utf-8 -*-
"""钢铁裂隙卡片节点中文翻译回归测试（2026-10-26 修复）。

背景：裂隙卡片节点名来自 Tenno.Tools 世界状态（英文，如 Void/Mot），
由 _fiss_node 拆成「星球 + 节点」两半——星球走 _LOCATION_ZH（较全），
节点走 _NODE_ZH（此前仅 5 条，导致绝大多数节点英文原样输出）。

修复：把「有官方简中译名」的节点补进 _NODE_ZH（来源：灰机Wiki SolNode 表 +
WFCD solNodes.json，英文↔中文已核对）。无官方译名的节点（Kiste/Dione/Armaros/
Naeglar/Pago/Taveuni …）保留英文，与官方客户端一致。

测试覆盖：截图涉及的节点 + 全部虚空塔 + 无译名节点保持英文。
"""
from src.plugins.qq_bridge.game_tools.warframe import _fiss_node


def test_screenshot_nodes_translated():
    """截图里「有官方中文」的节点应被翻出来。"""
    # 火星(赫拉斯撞击坑) —— 之前漏翻
    assert _fiss_node("Mars/Hellas") == "火星(赫拉斯撞击坑)"
    # 虚空(死灵塔) —— 之前漏翻
    assert _fiss_node("Void/Mot") == "虚空(死灵塔)"
    # 扎里曼号(涂沃主厅) —— 原有
    assert _fiss_node("Zariman/Tuvul Commons") == "扎里曼号(涂沃主厅)"


def test_void_towers_translated():
    """13 座虚空 Orokin 塔（SolNode400-412）全部翻官方中文。"""
    expect = {
        "Void/Teshub": "虚空(神王塔)", "Void/Oxomoco": "虚空(母神塔)",
        "Void/Mot": "虚空(死灵塔)", "Void/Hepit": "虚空(神后塔)",
        "Void/Taranis": "虚空(雷争塔)", "Void/Tiwaz": "虚空(法神塔)",
        "Void/Stribog": "虚空(风王塔)", "Void/Ani": "虚空(阿尼塔)",
        "Void/Ukko": "虚空(乌戈塔)", "Void/Belenus": "虚空(光神塔)",
        "Void/Aten": "虚空(太阳塔)", "Void/Marduk": "虚空(守护神塔)",
        "Void/Mithra": "虚空(光理塔)",
    }
    for loc, zh in expect.items():
        assert _fiss_node(loc) == zh, loc


def test_no_official_zh_nodes_stay_english():
    """无官方中文译名的节点保留英文（与官方客户端一致，不强行音译）。"""
    for loc in ["Ceres/Kiste", "Saturn/Dione", "Europa/Armaros",
                "Eris/Naeglar", "Kuva Fortress/Pago", "Kuva Fortress/Taveuni"]:
        out = _fiss_node(loc)
        # 星球被翻译、节点名仍是英文原文
        assert "(" in out and out.rsplit("(", 1)[-1].rstrip(")") == loc.split("/", 1)[1], loc


def test_unknown_node_graceful():
    """未知节点不应抛错，原样英文返回。"""
    assert _fiss_node("SomePlanet/SomeNode") == "SomePlanet(SomeNode)"
    assert _fiss_node("") == ""
