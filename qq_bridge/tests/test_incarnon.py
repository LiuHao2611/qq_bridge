# -*- coding: utf-8 -*-
"""灵化卡全量对齐参考图的回归测试（2026-11-05）。

守的是用户看得见的行为：
  * 正式 ``pic.render_sync(\"incarnon\")`` 能出 800×1793 的图，且用上全部真实资产；
  * 参考卡那一周的数据顺序 / 下周战甲 / 版本胶囊文案与截图一致；
  * ``tools/check_incarnon_layout.py`` 自检通过，且把错误放回去时能被抓到；
  * 行内图标缺图必须报错，不允许静默降级成占位方块（用户最高声明：禁止兜底）。

运行：
    .venv\\Scripts\\python.exe -m pytest tests/test_incarnon.py -v
"""
from __future__ import annotations

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
from src.plugins.qq_bridge.pic import render_sync  # noqa: E402


@pytest.fixture
def ref_week(monkeypatch):
    """锁到参考卡那一周（锚点后第 7 周：灵化 A 组 + 回廊 Week8）。"""
    monkeypatch.setattr(warframe, "_week_offset", lambda: 7)
    return warframe.incarnon_card_data()


def test_incarnon_data_matches_reference(ref_week):
    assert [f["name"] for f in ref_week["frames"]] == ["Inaros", "Ivara", "Titania"]
    assert [f["recommend"] for f in ref_week["frames"]] == [True, False, False]
    assert ref_week["next_frames"] == [
        {"name": "Nidus", "rec": True},
        {"name": "Octavia", "rec": False},
        {"name": "Harrow", "rec": False},
    ]
    assert [w["name"] for w in ref_week["weapons"]] == [
        "布莱顿", "苦无", "拉托", "帕里斯", "空刃"]
    assert [v["name"] for v in ref_week["weapons"][0]["variants"]] == [
        "布莱顿 Prime", "MK1- 布莱顿", "布莱顿 破坏者"]
    assert ref_week["weapons"][0]["comment_parts"][1]["icon"].endswith(".png")
    assert len(ref_week["rows"]) == 9 and all(len(r) == 5 for r in ref_week["rows"])


def test_incarnon_render_uses_real_assets_and_reference_size(ref_week):
    """走真正的 render 入口（不是直接调 build），保证机器人出图路径可用。"""
    png = render_sync("incarnon", theme_name=None, card="星际战甲·灵化",
                      data=ref_week)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    im = Image.open(BytesIO(png)).convert("RGB")
    assert im.size == (800, 1793)

    icons = [f["icon"] for f in ref_week["frames"]]
    icons += [w["icon"] for w in ref_week["weapons"]]
    icons += [p["icon"] for w in ref_week["weapons"]
              for p in (w.get("comment_parts") or []) if p.get("icon")]
    assert len(icons) == 9
    for path in icons:
        assert path and os.path.isfile(path) and os.path.getsize(path) > 600


def test_incarnon_layout_gate_passes():
    """把错误放回去会被门禁抓到，正常数据必须 PASS（card-design 红线）。"""
    gate = os.path.join(ROOT, "tools", "check_incarnon_layout.py")
    r = subprocess.run([sys.executable, gate, "--check", "--week", "7", "--ref-geom"],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "自检 PASS（溢出 0 / 重叠 0 / 间距 0）" in r.stdout

    s = subprocess.run([sys.executable, gate, "--selftest"],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert s.returncode == 0, s.stdout + s.stderr
    assert "selftest: PASS" in s.stdout


def test_incarnon_missing_inline_icon_raises(monkeypatch, ref_week):
    """行内图标缺失必须报错，不能画占位方块 / 原样输出标记。"""
    cards = warframe._incarnon_cards()
    cards["weapons"]["布莱顿"]["comment"] = "打不过 [[icon:no_such_weapon_xyz]] 伯斯顿"
    monkeypatch.setattr(warframe, "_incarnon_cards", lambda: cards)
    with pytest.raises(FileNotFoundError, match="灵化卡行内图标缺失"):
        warframe.incarnon_card_data()


def test_incarnon_all_weeks_gate_passes():
    """9 组 x 11 周 = 99 组合全量验收：别等轮换到下一周才发现问题。"""
    gate = os.path.join(ROOT, "tools", "check_incarnon_layout.py")
    r = subprocess.run([sys.executable, gate, "--all-weeks"],
                       cwd=ROOT, capture_output=True, text=True, timeout=420)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "99/99 组合渲染成功" in r.stdout
    assert "全周自检 PASS" in r.stdout


def test_incarnon_curated_covers_all_rotations():
    """9 组 / 11 周顺序与推荐都显式列出；45 武器 + 33 战甲评价、真实资产不缺。"""
    cards = warframe._incarnon_cards()
    circuit = warframe._incarnon_rotation()["circuit"]
    assert set(cards["card_order"]) == set(cards["rotation"]) == set("ABCDEFGHI")
    weeks = {str(i) for i in range(1, 12)}
    assert set(cards["frame_order"]) == weeks
    assert set(cards["frame_recommend"]) == weeks
    for g, names in cards["rotation"].items():
        assert set(cards["card_order"][g]) == set(names), g
        for zh in names:
            w = cards["weapons"][zh]
            assert w.get("comment") and w.get("variants"), zh
            assert warframe._incarnon_icon(w.get("en") or zh), zh
    for wk, items in circuit.items():
        en_names = [str(i).split(" ")[0] for i in items]
        assert set(cards["frame_order"][wk]) == set(en_names), wk
        for en in en_names:
            assert cards["frames"][en].get("comment"), en
            assert warframe._frame_icon(en), en


def test_incarnon_missing_curated_key_raises(monkeypatch):
    """漏填某周推荐必须报错，不能静默当成「本周无推荐」。"""
    cards = warframe._incarnon_cards()
    cards["frame_recommend"].pop("8", None)
    monkeypatch.setattr(warframe, "_incarnon_cards", lambda: cards)
    monkeypatch.setattr(warframe, "_week_offset", lambda: 7)
    with pytest.raises(KeyError, match="frame_recommend"):
        warframe.incarnon_card_data()


@pytest.mark.parametrize("week", [0, 3, 8, 10, 43])
def test_incarnon_other_weeks_render(monkeypatch, week):
    """非参考周（含 C/F 组、W11 缺过立绘的周）走正式 render 入口也能出图。"""
    monkeypatch.setattr(warframe, "_week_offset", lambda: week)
    data = warframe.incarnon_card_data()
    png = render_sync("incarnon", theme_name=None, card="星际战甲·灵化", data=data)
    im = Image.open(BytesIO(png)).convert("RGB")
    assert im.size[0] == 800 and im.size[1] in (1793, 1830)
    icons = ([f["icon"] for f in data["frames"]]
             + [w["icon"] for w in data["weapons"]]
             + [p["icon"] for w in data["weapons"]
                for p in (w.get("comment_parts") or []) if p.get("icon")])
    assert icons and all(p and os.path.isfile(p) for p in icons)
