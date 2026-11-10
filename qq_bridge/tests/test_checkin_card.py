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
    """主模板与门禁脚本都必须 <20480B（HEAD 均非存量超大文件，无棘轮豁免）。"""
    for rel in ("src/plugins/qq_bridge/pic/templates/checkin.py",
                "src/plugins/qq_bridge/pic/templates/checkin_parts.py",
                "tools/check_checkin_layout.py", "tools/checkin_style_gate.py"):
        sha = subprocess.check_output(
            ["git", "-C", str(ROOT.parent), "hash-object", "-w", "qq_bridge/" + rel],
            text=True).strip()
        size = int(subprocess.check_output(
            ["git", "-C", str(ROOT.parent), "cat-file", "-s", sha], text=True).strip())
        assert size < 20480, "%s git blob %d B 超 20KB 硬上限" % (rel, size)


# ---------------------------------------------------------------- 视觉精修契约（2026-11-10 15:0x）
# 用户「注意每个边框内外边距、字体、文字大小、加粗程度、颜色」。以下三条把主观
# 视觉规范变成机器可拦：字号层级 / 间距 token / 分区标题同色同字重。
sys.path.insert(0, str(TOOLS))
import checkin_style_gate as SG  # noqa: E402


def test_type_scale_is_layered():
    """字号层级：标题>核心数值>分区标题>正文>弱文字，逐级差达标。"""
    assert P.type_scale_problems() == [], P.type_scale_problems()


def test_spacing_are_four_pixel_tokens():
    """所有间距类常量须为 4 的倍数 token（杜绝 10/14/18/22 混用）。"""
    assert P.spacing_token_problems() == [], P.spacing_token_problems()


def test_section_titles_share_accent_and_bold():
    """4 个分区标题同层同色同字重：accent + bold，无奖励标题单独用绿。"""
    assert SG.section_style_problems() == [], SG.section_style_problems()


def test_visual_gate_catches_real_counterexamples():
    """三条注入反例（字号倒挂 / 非 token 间距 / 同层异色+去粗）必须各自被拦。"""
    assert SG.scale_probe(), "字号层级倒挂未拦住"
    assert SG.spacing_probe(), "非 4 倍数间距未拦住"
    assert SG.style_probe(), "分区标题同层异色/去 bold 未拦住"


def test_visual_contract_problems_bundles_parts():
    """视觉契约打包函数 = parts 的字号+间距判据，且当前源码无问题。"""
    assert SG.visual_contract_problems() == (
        P.type_scale_problems() + P.spacing_token_problems())


def test_zero_progress_no_isolated_current_line():
    """0 进度里程碑不得画孤立当前位置竖线（曾肉眼抓到，机器补拦）。

    守语义不锁字面：当前源码干净；等价守卫 if frac / if px>cx0 放行；
    无条件 if True、删整段守卫、找不到竖线都必须被拦。"""
    assert SG.zero_progress_line_problems() == [], SG.zero_progress_line_problems()
    src = open(SG.SOURCE, encoding="utf-8").read()
    # 等价进度守卫写法放行人（不 implementation-coupled）
    for repl in ("if frac:", "if px > cx0:"):
        assert SG.zero_progress_line_problems(src.replace("if total > 0:", repl, 1)) == [], repl
    # 无条件画 / 删守卫 / 删竖线本身，三种回退都必须被拦
    assert SG.zero_progress_line_problems(src.replace("if total > 0:", "if True:", 1))
    import re
    assert SG.zero_progress_line_problems(
        re.sub(r"    if total > 0:\n        px = .*\n        c\.line\(\(px,.*\n", "", src))
    assert SG.zero_progress_line_problems(src.replace("c.line((px,", "c.line((qx,", 1))
    # 反例探针：注入 if True 必须命中，还原后干净
    assert SG.zero_progress_probe(), "0 进度孤立竖线守卫回退未被拦住"


# ---------------------------------------------------------------- 里程碑图例 token 粘连（2026-11-10 肉眼抓到）
def test_legend_tokens_registered_as_real_boxes_with_gap():
    """图例 4 档 + 右侧「还差」须逐枚登记真实盒，相邻水平间距 >=8px（防连读）。"""
    from src.plugins.qq_bridge.pic.templates import checkin_font as CF
    boxes = []
    _build(boxes=boxes)                          # ok 态有 next_milestone=(15,200,3)
    toks = sorted((b for b in boxes if b[0].startswith("mile.legend.")
                   and b[0] != "mile.legend"), key=lambda b: b[1])
    assert [b[0] for b in toks] == ["mile.legend.0", "mile.legend.1",
                                    "mile.legend.2", "mile.legend.3", "mile.legend.next"]
    for a, b in zip(toks, toks[1:]):
        assert b[1] - a[3] >= 8, "图例 %s <-> %s 间距不足（连读粘连）" % (a[0], b[0])
    # 间距常量须为 4px token，且度量同源（token 宽走 checkin_font）
    assert P.MILE_LEGEND_GAP % 4 == 0 and P.MILE_LEGEND_GAP >= 16
    assert CF.width("已达成 3天 +30", P.FS_LEGEND, "bold") > 0


def test_legend_gate_catches_sticky_and_missing_counterexamples():
    """反例注入：相邻 token 压到 gap=0 / 删一枚 / 换行，判据须各自命中。"""
    assert SG.mile_legend_problems([
        ("mile.legend", 40, 518, 760, 546),
        ("mile.legend.0", 44, 525, 150, 539),
        ("mile.legend.1", 170, 525, 275, 539),
        ("mile.legend.2", 295, 525, 416, 539),
        ("mile.legend.3", 436, 525, 512, 539),
        ("mile.legend.next", 662, 525, 756, 539),
    ], has_next=True) == [], "正常 20px 样本被误报"
    good = [("mile.legend", 40, 518, 760, 546),
            ("mile.legend.0", 44, 525, 150, 539),
            ("mile.legend.1", 170, 525, 275, 539),
            ("mile.legend.2", 295, 525, 416, 539),
            ("mile.legend.3", 436, 525, 512, 539),
            ("mile.legend.next", 662, 525, 756, 539)]
    assert any("间距" in x or "重叠" in x for x in SG.mile_legend_problems(
        [good[0], good[1], ("mile.legend.1", 150, 525, 255, 539)] + good[3:], True))
    assert any("缺里程碑 token" in x for x in SG.mile_legend_problems(good[:4], True))
    assert SG.mile_legend_probe(), "图例粘连反例探针未命中"


# ---------------------------------------------------------------- 黄金图标 + 分层字体资产（2026-11-10）
def test_gold_icons_and_font_assets_are_real():
    """7 枚黄金 PNG + 4 款字体必须真实到位且非空（禁几何兜底/豆腐块交付）。"""
    assert SG.checkin_asset_contract_problems() == [], SG.checkin_asset_contract_problems()
    from src.plugins.qq_bridge.pic.templates import checkin_gold_icons as GI
    assert GI.missing() == [], "黄金图标缺真实 PNG：%s" % GI.missing()
    for k in SG.GOLD_KINDS:
        assert GI.available(k), "图标 %s 未到位" % k


def test_font_layer_routes_and_same_source_measure():
    """分层字体按字号/字重路由，绘制与度量同源（防「测雅黑、画风格字」错位）。"""
    from src.plugins.qq_bridge.pic.templates import checkin_font as CF
    assert CF.route(P.FS_TITLE, "bold") == "display"       # 40px 毛笔标题
    assert CF.route(P.FS_SEC, "bold") == "section"          # 20px 行楷分区
    assert CF.route(P.FS_PILL_VAL, "bold") == "heavy"       # 24px 圆润粗体数值
    assert CF.route(P.FS_NOTE, "regular") == "body"         # 14px 圆润正文
    for s, sz, role in (("签到", 40, "bold"), ("还差3天 +200", 14, "bold"),
                        ("本月签到记录", 20, "bold")):
        assert CF.width(s, sz, role) == CF.measure(s, sz, role)[0], "绘制度量不同源：%r" % s


def test_asset_wiring_gate_catches_real_regressions():
    """反例注入：撤 gate strict / 撤同源度量接线，资产契约判据须各自命中。"""
    assert SG.checkin_asset_probe(), "撤 gate/同源度量接线未被拦下"
    src = open(SG.SOURCE, encoding="utf-8").read()
    psrc = open(SG._PARTS, encoding="utf-8").read()
    assert SG.checkin_asset_contract_problems(src, psrc) == []
    bad = SG.checkin_asset_contract_problems(
        src.replace("_cf.wrap(Canvas", "_cf.wrap_none(Canvas", 1), psrc)
    assert any("wrap" in x for x in bad)
    bad2 = SG.checkin_asset_contract_problems(
        src, psrc.replace("_cf.size(text, size, role)", "c.text_size(text, size, role)", 1))
    assert any("同源度量" in x for x in bad2)


def test_missing_gold_png_hard_fails(monkeypatch, tmp_path):
    """prove-it 注入：真实 PNG 缺失时 gate(strict=True) 必须 SystemExit（不静默兜底）。"""
    from src.plugins.qq_bridge.pic.templates import checkin_gold_icons as GI
    monkeypatch.setattr(GI, "_ASSETS", tmp_path)            # 指向空目录 => 全缺
    with pytest.raises(SystemExit, match="黄金图标缺失"):
        GI.gate(strict=True)
    assert GI.missing() == list(GI.KINDS)


def test_missing_style_font_hard_fails(monkeypatch, tmp_path):
    """prove-it 注入：风格字体缺失时 gate(strict=True) 必须 SystemExit。"""
    from src.plugins.qq_bridge.pic.templates import checkin_font as CF
    monkeypatch.setattr(CF, "_ASSETS", tmp_path)
    with pytest.raises(SystemExit, match="风格字体缺失"):
        CF.gate(strict=True)
