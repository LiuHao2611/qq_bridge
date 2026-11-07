# -*- coding: utf-8 -*-
"""信条 / 终幕武器卡回归测试（2026-11-06）。

守的是用户看得见的行为：
  * 正式 ``pic.render_sync("tenet" / "coda")`` 都能出 800px 宽的正确图；
  * **信条和终幕是两个口令、两套独立版式**：信条单列粉紫气泡，
    终幕 800×998 参考图两列网格；不得共用 build/_plan，互传 kind 必须 raise；
  * 加成值带 ``+``、元素图标是 Wiki 真实伤害类型图标（不是色圆点）、
    武器图标缺一即报错（用户最高声明：禁止兜底 / 禁止降级）；
  * ``tools/check_tenet_layout.py`` 门禁通过，且把真实错误放回去时必须被抓到。

离线可复现：``tests/fixtures/tenet_coda_flight.json`` 是 2026-11-06 从
worldstate.wf.wiki 抓取的**真实快照**（不是编造的样本），只用于固定测试输入。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from io import BytesIO

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge.game_tools import warframe  # noqa: E402
from src.plugins.qq_bridge.pic import element_icons as ei  # noqa: E402
from src.plugins.qq_bridge.pic import render_sync  # noqa: E402
from src.plugins.qq_bridge.pic.templates import coda, tenet  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "tenet_coda_flight.json")
GATE = os.path.join(ROOT, "tools", "check_tenet_layout.py")


@pytest.fixture
def flight(monkeypatch):
    """用真实抓取快照替换联网取数，保证测试离线、可复现。"""
    with open(FIXTURE, encoding="utf-8") as f:
        snap = json.load(f)

    async def _fake():
        return snap

    monkeypatch.setattr(warframe, "_wfwiki_flight_data", _fake)
    return snap


def _data(kind: str) -> dict:
    d = asyncio.run(warframe.tenet_card_data(kind))
    assert d, "tenet_card_data 不应返回空"
    return d


def test_tenet_data_has_plus_bonus_and_canonical_elements(flight):
    for kind, n in (("tenet", 5), ("coda", 7)):
        d = _data(kind)
        assert len(d["weapons"]) == n
        assert d["kind"] == kind
        for w in d["weapons"]:
            assert w["bonus_str"].startswith("+"), w
            assert w["bonus_str"].endswith("%"), w
            assert w["element_key"] in ei.ELEMENT_FILES
            assert os.path.isfile(w["icon"]) and os.path.getsize(w["icon"]) > 600
            # 真实 Wiki 伤害类型图标，缺图 icon_path 会直接 raise
            assert ei.icon_path(w["element_key"]).is_file()


def test_tenet_both_kinds_render_through_render_sync(flight):
    """走机器人真正的出图入口，不是直接调 build。"""
    # 信条 5 把 → 两列 3 行 → 835px；终幕 7 把 → 两列 4 行 → 998px。
    for kind, h in (("tenet", 835), ("coda", 998)):
        d = _data(kind)
        card = ("星际战甲·终幕信条武器" if kind == "coda"
                else "星际战甲·佩兰数列信条武器")
        png = render_sync(kind, theme_name=None, card=card, data=d)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert Image.open(BytesIO(png)).size == (800, h)


def test_tenet_and_coda_are_independent_templates():
    """用户纠正「信条和终幕是两个口令」：两个模板必须独立，禁止再合并回去。"""
    assert coda.build is not tenet.build
    assert coda._plan is not tenet._plan


def test_tenet_and_coda_share_two_column_grid(flight):
    """看得出来的版式：信条与终幕同为两列网格（2026-11-06 用户要求对齐），
    但仍是两套独立模板、武器数不同导致高度不同。"""
    from src.plugins.qq_bridge.pic import resolve_theme

    tb: list = []
    tc = tenet.build(resolve_theme(None, "x"), data=_data("tenet"), width=800, boxes=tb)
    cb: list = []
    cc = coda.build(resolve_theme(None, "x"), data=_data("coda"), width=800, boxes=cb)
    # 同为两列网格：两套模板都渲染出 cell*-bar / cell*-under / top-div / foot-div / pc-pill
    for boxes in (tb, cb):
        labels = [b[0] for b in boxes]
        assert "top-div" in labels and "foot-div" in labels and "pc-pill" in labels
        assert sum(1 for b in boxes if b[0].startswith("cell") and b[0].endswith("-bar")) >= 2
        assert not any(b[0].startswith("row") for b in boxes)  # 不再有单列气泡
    # 武器数不同 → 高度不同（信条 5 把 / 终幕 7 把）
    assert tc.height != cc.height
    assert tc.height == 835 and cc.height == 998
    # 信条 5 把 → 5 张卡；终幕 7 把 → 7 张卡
    assert sum(1 for b in tb if b[0].startswith("cell") and b[0].endswith("-bar")) == 5
    assert sum(1 for b in cb if b[0].startswith("cell") and b[0].endswith("-bar")) == 7


def test_tenet_templates_reject_wrong_kind(flight):
    """防止又把两个口令混用：互相传错 kind 必须直接 raise。"""
    from src.plugins.qq_bridge.pic import resolve_theme

    with pytest.raises(ValueError, match="只接受 kind"):
        tenet.build(resolve_theme(None, "x"), data=_data("coda"), width=800)
    with pytest.raises(ValueError, match="只接受 kind"):
        coda.build(resolve_theme(None, "x"), data=_data("tenet"), width=800)


def test_tenet_missing_weapon_icon_raises(flight):
    """两列信条卡也必须禁止 sword 几何兜底（用户最高声明禁止降级）。"""
    from src.plugins.qq_bridge.pic import resolve_theme

    d = _data("tenet")
    d["weapons"][0]["icon"] = ""
    with pytest.raises(FileNotFoundError, match="真实武器图标"):
        tenet.build(resolve_theme(None, "x"), data=d, width=800)


def test_tenet_all_14_element_assets_are_real():
    assert len(ei.ELEMENT_FILES) >= 14
    for key, fname in ei.ELEMENT_FILES.items():
        p = ei.icon_path(key)
        assert p.name == fname
        with Image.open(p) as im:
            assert im.size == (64, 64), (key, im.size)
            assert im.mode in ("RGBA", "RGB", "P")


def test_tenet_reference_bonus_and_element_colors_locked():
    """参考卡 7 个加成值 / 4 种元素名的实测配色，不许悄悄改。"""
    assert [ei.bonus_color(v) for v in (33.1, 49.0, 38.3, 25.7, 25.6, 27.7, 26.4)] == [
        "#215694", "#328B51", "#215694", "#51676B", "#51676B", "#51676B", "#51676B"]
    assert ei.elem_color("magnetic") == "#657B82"
    assert ei.elem_color("electricity") == "#8B62A1"
    assert ei.elem_color("toxin") == "#7C8D46"
    assert ei.elem_color("heat") == "#D1701E"


def test_tenet_unknown_element_raises():
    with pytest.raises(KeyError, match="未知元素"):
        ei.canonical("不存在的元素")


def test_tenet_missing_weapon_icon_raises(flight, monkeypatch):
    """武器图标下载失败必须显式报错，不允许留空让模板画几何 sword。"""
    async def _none(_url):
        return None

    monkeypatch.setattr(warframe, "_bounty_download_icon", _none)
    with pytest.raises(FileNotFoundError, match="真实武器图标缺失"):
        _data("coda")


def test_tenet_missing_element_icon_raises(flight, monkeypatch):
    """元素图标缺失必须显式报错，不允许画元素色圆点。"""
    d = _data("coda")
    missing = dict(ei.ELEMENT_FILES)
    missing["magnetic"] = "__absent_damage_icon__.png"
    monkeypatch.setattr(ei, "ELEMENT_FILES", missing)
    from src.plugins.qq_bridge.pic import resolve_theme

    with pytest.raises(FileNotFoundError, match="真实元素图标"):
        coda.build(resolve_theme(None, "星际战甲·终幕信条武器"),
                   data=d, width=800)


def test_tenet_width_must_be_800(flight):
    d = _data("coda")
    from src.plugins.qq_bridge.pic import resolve_theme

    with pytest.raises(ValueError, match="width=900"):
        coda.build(resolve_theme(None, "星际战甲·终幕信条武器"),
                   data=d, width=900)


def test_tenet_layout_gate_passes():
    r = subprocess.run([sys.executable, GATE, "--check", "--ref-geom", "--fixture"],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "溢出 0 / 重叠 0 / 间距 0 / 坐标 0 / 资产 0" in r.stdout
    assert "14/14 种" in r.stdout
    assert "全元素压力检查（14 种 × +59.9% × 最长倒计时）：0 项问题" in r.stdout


def test_tenet_gate_selftest_catches_real_errors():
    # 显式 --fixture：测试用真实快照离线复现，不依赖 worldstate 当前是否在线。
    r = subprocess.run([sys.executable, GATE, "--selftest", "--fixture"],
                       cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELFTEST PASS：8 类真实错误全部被拦住" in r.stdout


def test_tenet_gate_selftest_without_fixture_uses_live_data():
    """容器场景：不传 --fixture 必须走实时数据，不得悄悄去读 tests/ 目录。

    把夹具文件临时改名，--selftest 仍要 PASS；若实现又偷偷写死读夹具，
    这里就会因 FileNotFoundError 失败。改完立刻恢复（try/finally 保证）。
    """
    moved = FIXTURE + ".off"
    os.rename(FIXTURE, moved)
    try:
        r = subprocess.run([sys.executable, GATE, "--selftest"],
                           cwd=ROOT, capture_output=True, text=True, timeout=240)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "SELFTEST PASS：8 类真实错误全部被拦住" in r.stdout
        assert "No such file or directory" not in r.stderr
    finally:
        os.rename(moved, FIXTURE)


def test_tenet_gate_fixture_missing_fails_loudly():
    """显式指定 --fixture 但夹具不存在时必须报错，禁止自动回退在线数据。"""
    moved = FIXTURE + ".off"
    os.rename(FIXTURE, moved)
    try:
        r = subprocess.run([sys.executable, GATE, "--check", "--fixture"],
                           cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert r.returncode != 0, "夹具缺失却静默成功了（禁止降级）"
        assert "FileNotFoundError" in r.stderr, r.stdout + r.stderr
    finally:
        os.rename(moved, FIXTURE)
