# -*- coding: utf-8 -*-
"""签到卡（浅色 Rabbit 风）回归测试 —— 2026-11-10「参考 Rabbit 图优化 + 改浅色」。

守四类契约：
  * 主题绑定：checkin 显式 > 配置 > 内置默认，三层都解析为 light（浅色交付不依赖 data/ 配置）；
  * 渲染：三态（ok / already / 未签到）+ 里程碑态 + 无下一里程碑，都真实出 PNG、无豆腐框；
  * 数据契约：只展示 manager 真实字段（连签/累计/喵币/名次/月历/奖励拆分），
    **绝不出现** Rabbit 专属编造字段（群段位/经验值/群萝卜/抽中/礼品盒）；
  * 门禁：check_checkin_layout 的 --selftest 9 类反例全部被拦，25 组渲染 0 问题。

不联网、不碰 Docker：全程 render_sync / build 离线渲染 + 离线夹具。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

from src.plugins.qq_bridge.pic import _resolve_card_theme_name  # noqa: E402
from src.plugins.qq_bridge.pic import card_binds, theme  # noqa: E402
from src.plugins.qq_bridge.pic.templates import checkin as C  # noqa: E402
from src.plugins.qq_bridge.pic.templates import checkin_parts as P  # noqa: E402

# ---- 真实 manager 契约夹具 ----
PROF = {"streak": 12, "total_days": 12, "points": 720, "last_day": "2026-11-10",
        "today": "2026-11-10", "already": False, "next_milestone": (15, 200, 3)}
CAL = {"year": 2026, "month": 11, "today": "2026-11-10",
       "days": {"2026-11-01": 6, "2026-11-02": 5, "2026-11-09": 5, "2026-11-10": 7}}
RES_OK = {"status": "ok", "gain": 7, "daily": 5, "streak_bonus": 2,
          "milestone_bonus": 0, "today": "2026-11-10"}


def _build(res=None, prof=None, cal=None, tname="light", boxes=None, name="林浩", rank=3):
    t = theme.resolve(tname)
    return C.build(t, name=name, res=res or RES_OK, prof=prof or PROF,
                   cal=cal or CAL, rank=rank, boxes=boxes)


# ---------------------------------------------------------------- 主题绑定
def test_builtin_default_binds_checkin_to_light():
    """内置默认（随代码交付、绕过 data/ gitignore）必须把签到卡绑到 light。"""
    assert card_binds.default_card_theme("checkin") == "light"
    assert card_binds.resolve_card_theme(None, "checkin", lambda c: "") == "light"


def test_config_bind_precedence_and_explicit_override():
    """配置层（若存在）优先于内置默认；显式主题又优先于配置层。

    注意 data/pic_card_themes.json 被 gitignore、不入库，fresh clone 里可能不存在，
    故不断言配置层必为 light（内置默认才是交付保证）；只守「优先级顺序」契约。
    """
    cfg = theme.card_theme("checkin")           # 运行时配置值（可能为空）
    final = _resolve_card_theme_name(None, "checkin")
    assert final == (cfg or card_binds.default_card_theme("checkin")) == "light"
    # 显式主题优先：无论配置/内置为何，显式传入一律生效（编辑器可临时预览深色）
    assert _resolve_card_theme_name("brutalist", "checkin") == "brutalist"


def test_resolved_checkin_theme_is_pale_light():
    name = _resolve_card_theme_name(None, "checkin")
    t = theme.resolve(name, "checkin")
    assert t.name == "light"
    assert int(t.bg.lstrip("#")[0:2], 16) >= 200      # 页面底色浅
    assert t.radius > 0                                # 圆角（非 Brutalism 直角）
    assert t.bg_pattern != "none"                      # 贴纸背景替代参考图插画


# ---------------------------------------------------------------- 渲染三态
def test_ok_state_renders_png_with_boxes():
    boxes = []
    c = _build(boxes=boxes)
    png = c.to_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.img.size[0] == 800 and c.img.size[1] > 800
    labels = {b[0] for b in boxes}
    assert {"hdr.title", "banner", "status.title", "cal.grid", "reward.title", "footer"} <= labels
    assert sum(1 for x in labels
               if x.startswith("status.pill.") and not x.endswith(".value")) == 4
    assert sum(1 for x in labels if x.endswith(".value")) == 4   # 数值贴边判据登记


def test_already_state_uses_real_wording():
    prof = dict(PROF, already=True)
    res = {"status": "already", "today": "2026-11-10"}
    c = _build(res=res, prof=prof)
    assert c.to_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_not_signed_state_renders():
    prof = {"streak": 0, "total_days": 0, "points": 0, "last_day": "",
            "today": "2026-11-10", "already": False, "next_milestone": (3, 30, 3)}
    c = _build(res={"status": "none", "today": "2026-11-10"}, prof=prof)
    assert c.to_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_milestone_bonus_shows_three_reward_pills():
    res = dict(RES_OK, gain=237, daily=5, streak_bonus=2, milestone_bonus=230)
    boxes = []
    _build(res=res, boxes=boxes)
    n = sum(1 for x in boxes if x[0].startswith("reward.pill."))
    assert n == 3, "里程碑态应出 每日/连签/里程碑 三枚奖励胶囊"


def test_no_next_milestone_state_renders():
    """累计满 30 天（next_milestone=None）时第四枚胶囊降级为名次/已圆满，不崩。"""
    prof = dict(PROF, total_days=30, streak=30, next_milestone=None)
    boxes = []
    c = _build(prof=prof, boxes=boxes)
    assert c.to_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert sum(1 for x in boxes
               if x[0].startswith("status.pill.") and not x[0].endswith(".value")) == 4


def test_brutalist_theme_still_renders_backward_compat():
    """显式 brutalist（编辑器预览/历史配置）仍可出图，不因浅色重写而崩。"""
    c = _build(tname="brutalist")
    assert c.to_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- 数据契约：无编造字段
@pytest.mark.parametrize("word", ["群段位", "经验值", "群萝卜", "0段", "抽中", "礼品盒",
                                   "路边捡", "100点经验", "本月补签"])
def test_no_rabbit_invented_field_in_template_source(word):
    """模板两个源文件的**字符串字面量**不得出现 Rabbit 专属编造字段。"""
    for fn in ("checkin.py", "checkin_parts.py"):
        src = (ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates" / fn).read_text(
            encoding="utf-8")
        for ln in src.splitlines():
            s = ln.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("*"):
                continue
            assert not (("'" + word) in ln or ('"' + word) in ln
                        or (word + "'") in ln or (word + '"') in ln), \
                "%s 卡面字符串出现编造字段 %r：%s" % (fn, word, s)


def test_only_real_manager_fields_in_capsules():
    """胶囊只用 manager 真实字段；月历来自 cal.days。"""
    boxes = []
    _build(boxes=boxes)
    labels = [b[0] for b in boxes]
    assert "cal.grid" in labels
    assert "mile.bar" in labels            # 项目强项：里程碑进度条保留
    assert "status.pill.0" in labels and "status.pill.3" in labels


# ---------------------------------------------------------------- 跨月鲁棒
@pytest.mark.parametrize("year,month,today", [
    (2026, 2, "2026-02-28"),   # 平年 28
    (2024, 2, "2024-02-29"),   # 闰年 29
    (2026, 4, "2026-04-30"),   # 30
    (2026, 1, "2026-01-31"),   # 31
    (2026, 12, "2026-12-01"),  # 跨年
])
def test_month_calendar_rows_all_render(year, month, today):
    off, dim = P._month_matrix(year, month)
    rows = (off + dim + 6) // 7
    cal = {"year": year, "month": month, "today": today, "days": {today: 7}}
    boxes = []
    c = _build(cal=cal, boxes=boxes)
    assert c.to_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    grid = next(b for b in boxes if b[0] == "cal.grid")
    # 月历网格高度 = 星期表头 + rows×日高
    assert abs((grid[4] - grid[2]) - (P.WD_H + rows * P.DAY_H)) <= 1


# ---------------------------------------------------------------- 门禁
def test_gate_selftest_catches_all_injected_counterexamples():
    r = subprocess.run([sys.executable, str(TOOLS / "check_checkin_layout.py"), "--selftest"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_gate_check_all_states_months_zero_problems():
    r = subprocess.run([sys.executable, str(TOOLS / "check_checkin_layout.py"), "--check"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "溢出 0 / 重叠 0 / 间距 0" in r.stdout


def test_template_under_20kb_ratchet():
    """主模板必须 <20480B（HEAD 14KB 非存量超大文件，无棘轮豁免）。"""
    sha = subprocess.check_output(
        ["git", "-C", str(ROOT.parent), "hash-object", "-w",
         "qq_bridge/src/plugins/qq_bridge/pic/templates/checkin.py"], text=True).strip()
    size = int(subprocess.check_output(
        ["git", "-C", str(ROOT.parent), "cat-file", "-s", sha], text=True).strip())
    assert size < 20480, "checkin.py git blob %d B 超 20KB 硬上限" % size
