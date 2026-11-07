"""仲裁时间表（#wf 仲裁表 / 仲裁表）回归测试。

数据层：arbitration_table_data(arg) → {title, rows:[{time,task,node,eff,tier}], footer, page, hint}
- 长图模式（无分页）：默认与「所有」/「-N」等价，统一返回全量轮换班次
  （_ARB_TABLE_CYCLES × 7 = 21 行），且 page == ""、hint == ""（卡片不再画页码徽章）。
- 行字段：time(「X日Y时」) / task(「<星球><任务><派系字母>系」) / node(英文节点名) /
          eff(生息效率) / tier(评级 S+/A+/B+/C+)
- 逐时循环：当前 7 节点轮换池按小时 idx 偏移。

本测试不依赖网络：monkeypatch 掉 _official_arbitration 与 _solnodes，并锚定 _now。
（项目未启用 pytest-asyncio，故以 asyncio.run 包裹异步函数，与既有同步测试风格一致）
"""
import asyncio

import pytest

import src.plugins.qq_bridge.game_tools.warframe as wf

# 当前国际服仲裁 7 节点轮换池（与官方世界状态一致；Rabbit 旧截图的更大池已不再出现）
_OFFICIAL = {
    "nodes": ["SolNode132", "SolNode11", "SolNode123", "SolNode10",
              "SolNode122", "SolNode62", "SettlementNode3"],
    "activation": 1700000000,   # 锚点：令 _now()==activation → idx=0，首页=节点 0..6
    "expiry": 9999999999,
}
_SN = {
    "SolNode132": {"value": "Bode (Ceres)", "enemy": "Grineer", "type": "Spy"},
    "SolNode11": {"value": "Thebe (Jupiter)", "enemy": "Corpus", "type": "Exterminate"},
    "SolNode123": {"value": "Herculina (Ceres)", "enemy": "Grineer", "type": "Defense"},
    "SolNode10": {"value": "Cameria (Jupiter)", "enemy": "Corpus", "type": "Interception"},
    "SolNode122": {"value": "Pangaea (Eris)", "enemy": "Infested", "type": "Survival"},
    "SolNode62": {"value": "Sechura (Mercury)", "enemy": "Corpus", "type": "Excavation"},
    "SettlementNode3": {"value": "Larzac (Earth)", "enemy": "Grineer", "type": "Extermination"},
}


@pytest.fixture
def patched(monkeypatch):
    async def _official():
        return dict(_OFFICIAL)
    async def _sn():
        return dict(_SN)
    monkeypatch.setattr(wf, "_official_arbitration", _official)
    monkeypatch.setattr(wf, "_solnodes", _sn)
    monkeypatch.setattr(wf, "_now", lambda: _OFFICIAL["activation"])
    return


def test_table_default_is_long_image_21_rows(patched):
    """默认「仲裁表」即为整张长图：7 节点 × 3 循环 = 21 行，无页码/翻页。"""
    d = asyncio.run(wf.arbitration_table_data(""))
    assert d["title"] == "仲裁时间表"
    assert len(d["rows"]) == 21, "长图：默认即为 7 节点 × 3 循环 = 21 行（无分页）"
    assert d["page"] == "" and d["hint"] == "", "长图无页码/翻页提示"
    keys = {"time", "task", "node", "eff", "tier"}
    assert set(d["rows"][0].keys()) == keys


def test_table_columns_efficiency_and_tier(patched):
    """行字段与评级/效率口径（沿用旧首页断言，验证首行与最高效行）。"""
    d = asyncio.run(wf.arbitration_table_data(""))
    # 第一行 = nodes[0] = Bode (Ceres) / Grineer / Spy
    r0 = d["rows"][0]
    assert r0["node"] == "Bode"
    assert "G系" in r0["task"] and "间谍" in r0["task"]
    assert r0["eff"] == 450 and r0["tier"] == "B+", "Spy → (450, B+)"
    # 第四行 = nodes[3] = Cameria / Corpus / Interception → S+ 最高效
    r3 = d["rows"][3]
    assert r3["node"] == "Cameria"
    assert "C系" in r3["task"] and "拦截" in r3["task"]
    assert r3["eff"] == 850 and r3["tier"] == "S+"


def test_table_arg_all_and_dashN_equivalent(patched):
    """「所有」/「-2」等参数均与默认等价：同样 21 行、无页码（长图，不分页）。"""
    for arg in ("所有", "all", "全部", "-2", "-1", "99"):
        d = asyncio.run(wf.arbitration_table_data(arg))
        assert len(d["rows"]) == 21, "arg=%r 也应返回全量长图 21 行" % arg
        assert d["page"] == "" and d["hint"] == ""
    # 纯循环：第二循环首行（第 8 行）应回到第一个节点
    d_all = asyncio.run(wf.arbitration_table_data("所有"))
    assert d_all["rows"][7]["node"] == "Bode"
    assert d_all["rows"][0]["node"] == d_all["rows"][14]["node"] == "Bode"


def test_time_label_format(patched):
    d = asyncio.run(wf.arbitration_table_data(""))
    t = d["rows"][0]["time"]
    assert "日" in t and t.endswith("时"), "时间标签应形如「26日09时」"


def test_faction_letter_and_mission_zh(patched):
    d = asyncio.run(wf.arbitration_table_data(""))
    # 索引 5 = Sechura / Corpus / Excavation → C系挖掘 / A
    r5 = d["rows"][5]
    assert r5["node"] == "Sechura"
    assert "C系" in r5["task"] and "挖掘" in r5["task"]
    assert r5["eff"] == 550 and r5["tier"] == "A"
    # 索引 6 = Larzac / Grineer / Extermination → G系歼灭 / C+
    r6 = d["rows"][6]
    assert r6["node"] == "Larzac"
    assert "G系" in r6["task"] and "歼灭" in r6["task"]
    assert r6["eff"] == 300 and r6["tier"] == "C+"


def test_empty_data_graceful(monkeypatch):
    async def _none():
        return None
    monkeypatch.setattr(wf, "_official_arbitration", _none)
    d = asyncio.run(wf.arbitration_table_data(""))
    assert d["rows"] == []
    assert d["footer"], "无数据时 footer 应给出友好提示而非崩溃"


def test_text_fallback_source_aligned(patched):
    """arbitration_table_text 与 arbitration_table_data 同源（降级文本）。"""
    txt = asyncio.run(wf.arbitration_table_text(""))
    assert "【仲裁时间表】" in txt
    assert "Bode" in txt and "S+" in txt
    # 长图：文本表同样输出全部 21 行班次
    txt_all = asyncio.run(wf.arbitration_table_text("所有"))
    assert txt_all.count("· ") == 21
