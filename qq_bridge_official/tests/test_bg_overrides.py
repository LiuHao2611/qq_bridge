# -*- coding: utf-8 -*-
"""卡片专属背景（bg_overrides）回归（2026-09-23）。

背景：主题级 bg_image 是所有模板共享的——用户在编辑器给「裂隙卡」上传背景图，
实际写进了全局字段，导致攻略卡背景也跟着变（用户报障）。

修复后契约：
1. 渲染层：``t.bg_overrides[tpl]`` 仅影响对应模板；其它模板渲染结果与无覆盖时
   **逐字节一致**（机器判定"不误伤"）；
2. 固定风格卡（fissure）：无专属覆盖时**完全不吃全局背景**（无创原则——
   内置 neon/light 全局有 dots/stickers 图案，裂隙卡观感不得因此改变）；
   有专属覆盖时背景生效（与无覆盖渲染不一致）；
3. 校验层：validate_layout 清洗 bg_overrides（非法键/非法资产名剥掉，不抛异常）；
4. 存量数据：cs1.json 迁移后（绿图归位 fissure 专属）可正常加载。
"""
import json
import os

from src.plugins.qq_bridge.pic import render_raw
from src.plugins.qq_bridge.pic.theme import NEON_THEME, Theme, resolve
from src.plugins.qq_bridge.pic.themes_store import validate_layout
from src.plugins.qq_bridge.pic.templates import fissure as fissure_tpl
from src.plugins.qq_bridge.pic.templates import netracell as netracell_tpl



def _theme(**kw) -> Theme:
    base = dict(
        name="t", bg="#0B0E17", card="#141A2B", card_border="#232C45",
        card_shadow="#000000", text="#EAF0FF", text_sub="#A7B4CE",
        text_weak="#6E7C99", accent="#00E5FF", accent2="#FF4FD8",
        accent_soft="#0E2A38", track="#1E2740", fill_start="#00E5FF",
        fill_end="#FF4FD8", ok="#3DDC97", warn="#FFB020", err="#FF5C7A",
        divider="#232C45",
    )
    base.update(kw)
    return Theme(**base)


def test_validate_layout_cleans_bg_overrides():
    lay = validate_layout({"bg_overrides": {
        "fissure": {"bg_pattern": "image", "bg_image": "a" * 32 + ".png", "bg_image_opacity": 150},
        "BAD KEY": {"bg_image": "x"},                      # 键非法 → 整条剥掉
        "guide": {"bg_image": "../../etc/passwd", "evil": 1},  # 资产名/键非法 → 剥掉
    }})
    ov = lay["bg_overrides"]
    assert "BAD KEY" not in ov, "非法键整条剥掉"
    assert ov["fissure"]["bg_image_opacity"] == 100, "越界值夹取"
    assert "evil" not in ov.get("guide", {}), "白名单外键剥掉"
    assert ov.get("guide", {}).get("bg_image", "") == "", "非法资产名清空"


def test_fissure_override_does_not_affect_guide():
    """裂隙卡专属背景不得影响攻略卡：两者渲染结果逐字节一致。"""
    t_plain = _theme(bg_pattern="none")
    t_ov = _theme(bg_pattern="none",
                  bg_overrides={"fissure": {"bg_pattern": "dots"}})
    a = render_raw("guide", t_plain, body="预览")
    b = render_raw("guide", t_ov, body="预览")
    assert a == b, "攻略卡渲染不得因 fissure 专属覆盖而变化"


def test_fissure_ignores_global_background():
    """无创回归：内置风格主题全局有背景图案（如 neon 的 dots），裂隙卡不受影响。"""
    t = _theme(bg_pattern="dots")  # 模拟全局图案（neon 内置即 dots）
    no_ov = render_raw("fissure", t, groups=[], title="虚空裂隙", intro="")
    t2 = _theme(bg_pattern="dots", bg_overrides={})
    assert no_ov == render_raw("fissure", t2, groups=[], title="虚空裂隙", intro="")
    # 全局背景改变（dots→grid）裂隙卡渲染仍不变
    t3 = _theme(bg_pattern="grid")
    assert no_ov == render_raw("fissure", t3, groups=[], title="虚空裂隙", intro="")


def test_fissure_override_takes_effect():
    """裂隙卡配了专属背景后渲染必须真的变化（否则用户设了也白设）。"""
    t_plain = _theme(bg_pattern="none")
    t_ov = _theme(bg_pattern="none",
                  bg_overrides={"fissure": {"bg_pattern": "dots"}})
    a = render_raw("fissure", t_plain, groups=[], title="虚空裂隙", intro="")
    b = render_raw("fissure", t_ov, groups=[], title="虚空裂隙", intro="")
    assert a != b, "fissure 专属覆盖必须生效"


def test_global_templates_pass_tpl_and_still_render():
    """六个走全局背景的模板带 tpl 调用后必须正常出图（不崩、PNG 魔数正确）。"""
    t = _theme(bg_overrides={"menu": {"bg_pattern": "grid"}})
    assert render_raw("menu", t)[:8] == b"\x89PNG\r\n\x1a\n"      # menu 命中专属覆盖
    assert render_raw("hw", t)[:8] == b"\x89PNG\r\n\x1a\n"
    assert render_raw("text", t, body="预览")[:8] == b"\x89PNG\r\n\x1a\n"
    assert render_raw("progress", t, percent=62, elapsed=95, kind="t2i",
                      prompt="预览", trace_id="t01")[:8] == b"\x89PNG\r\n\x1a\n"
    assert render_raw("quota", t, used=18, quota=50, user_label="配额")[:8] == b"\x89PNG\r\n\x1a\n"


def test_cs1_theme_loads_with_fissure_override():
    """存量 cs1.json 迁移后：可加载、fissure 专属背景归位、全局背景已清。"""
    t = resolve("cs1")
    assert t.name == "cs1"
    assert t.bg_overrides.get("fissure", {}).get("bg_image"), "绿图应归位 fissure 专属"
    assert t.bg_pattern == "none", "全局背景应已清空（攻略卡恢复原观感）"
    # 渲染冒烟：fissure（带专属背景）与 guide（无背景）都正常出图
    assert render_raw("fissure", t, groups=[], title="虚空裂隙", intro="")[:4]
    assert render_raw("guide", t, body="预览")[:4]


def test_neon_theme_untouched():
    """内置主题 bg_overrides 为空 dict——所有模板行为与旧版一致。"""
    assert NEON_THEME.bg_overrides == {}
    assert resolve("neon").bg_overrides == {}


# ============================================================================
# 固定风格卡专属配色（ov: 语义色键，2026-09-24）
# 契约：bg_overrides["fissure"]/["netracell"] 里的语义色键覆盖硬编码默认；
#   * 不配 → 渲染与旧版逐字节一致（无创、不误伤任何其它模板）；
#   * 配了合法 HEX → 渲染真的变化；非法/白名单外键被剥掉；
#   * 模板必须 emit ov: 热区（前端靠 regions 出可点击浮窗）。
# ============================================================================
def test_ov_color_keys_validated_and_normalized():
    """专属配色键：合法 HEX 保留并大写；非法 HEX / 白名单外键剥掉。"""
    lay = validate_layout({"bg_overrides": {
        "fissure":  {"title_color": "#1f4e79", "red_color": "nothex", "bogus": "#FFFFFF"},
        "netracell": {"bubble_bg": "#d6e6f5", "evil": 1},
    }})
    f = lay["bg_overrides"]["fissure"]
    n = lay["bg_overrides"]["netracell"]
    assert f.get("title_color") == "#1F4E79", "合法 HEX 保留并归一大写"
    assert "red_color" not in f, "非法 HEX 剥掉"
    assert "bogus" not in f, "白名单外键剥掉"
    assert n.get("bubble_bg") == "#D6E6F5", "netracell 合法 HEX 保留"
    assert "evil" not in n, "白名单外键剥掉"


def test_fissure_ov_color_takes_effect():
    """裂隙卡配了专属色键后渲染必须真的变化。"""
    t0 = _theme(bg_overrides={"fissure": {}})
    t1 = _theme(bg_overrides={"fissure": {"title_color": "#FF0000"}})
    a = render_raw("fissure", t0, groups=[], title="虚空裂隙", intro="")
    b = render_raw("fissure", t1, groups=[], title="虚空裂隙", intro="")
    assert a != b, "fissure 专属配色必须生效"


def test_netracell_ov_color_takes_effect():
    """科研卡配了专属色键后渲染必须真的变化。"""
    t0 = _theme(bg_overrides={"netracell": {}})
    t1 = _theme(bg_overrides={"netracell": {"title_color": "#FF0000"}})
    a = render_raw("netracell", t0, sections=[], title="本周科研任务", remain="")
    b = render_raw("netracell", t1, sections=[], title="本周科研任务", remain="")
    assert a != b, "netracell 专属配色必须生效"


def test_no_ov_color_keeps_byte_identical():
    """未配专属色键时， fissure/netracell 渲染与无 bg_overrides 逐字节一致。"""
    t_none = _theme()                                  # 不传 bg_overrides（用 Theme 默认）
    t_empty = _theme(bg_overrides={})                   # 空 dict
    kw_f = dict(groups=[], title="虚空裂隙", intro="")
    kw_n = dict(sections=[], title="本周科研任务", remain="")
    assert render_raw("fissure", t_none, **kw_f) == render_raw("fissure", t_empty, **kw_f)
    assert render_raw("netracell", t_none, **kw_n) == render_raw("netracell", t_empty, **kw_n)


def test_ov_color_no_cross_template():
    """固定风格卡的专属配色不得误伤其它模板（攻略卡 / 另一张固定卡）。"""
    t_f = _theme(bg_overrides={"fissure": {"title_color": "#FF0000"}})
    t_n = _theme(bg_overrides={"netracell": {"title_color": "#FF0000"}})
    base = _theme(bg_overrides={})
    # fissure 配色不影响 guide / netracell
    assert render_raw("guide", base, body="预览") == render_raw("guide", t_f, body="预览")
    assert render_raw("netracell", base, sections=[], title="x", remain="") == \
           render_raw("netracell", t_f, sections=[], title="x", remain="")
    # netracell 配色不影响 guide / fissure
    assert render_raw("guide", base, body="预览") == render_raw("guide", t_n, body="预览")
    assert render_raw("fissure", base, groups=[], title="x", intro="") == \
           render_raw("fissure", t_n, groups=[], title="x", intro="")


def test_fissure_regions_have_ov_fields():
    """fissure 必须 emit ov: 热区（前端可编辑化契约），且每个区域带当前生效色。"""
    t = _theme(bg_overrides={})
    groups = [{"label": "前纪(T2)", "rows": [
        {"bubble": "Grineer · 普通", "node": "Tessellate", "remain": "23分钟"}]}]
    c = fissure_tpl.build(t, groups=groups, title="虚空裂隙",
                          intro="矿工的日常", footers=["* 输入\"裂隙 钢铁\""])
    fields = {r["field"] for r in c.regions}
    for need in ("ov:card_color", "ov:title_color", "ov:text_color",
                 "ov:tier_color", "ov:bubble_bg", "ov:node_color",
                 "ov:remain_color", "ov:dim_color", "ov:line_color"):
        assert need in fields, "fissure 缺少热区 %s" % need
    # 每个 ov: 区域必须带 values（前端 curColorOf 读初始色）
    for r in c.regions:
        if r["field"].startswith("ov:"):
            assert r.get("values"), "区域 %s 缺少 values" % r["field"]


def test_netracell_regions_have_ov_fields():
    """netracell 必须 emit ov: 热区（前端可编辑化契约），且每个区域带当前生效色。

    注意：border_color / bubble_line / bubble_text 不单列热区，而是分别随
    ov:card_color / ov:bubble_bg 的「多色弹窗」（values 携带 + 前端 ELEMENT_CONTROLS
    多行）一起改——断言可达性而非独立 field。
    """
    t = _theme(bg_overrides={})
    sections = [{"highlight": "深层",
                 "missions": [{"type": "刺杀", "name": "Vor", "risks": ["情报减少"]}],
                 "extras": ["额外奖励：真空 Mod"]}]
    c = netracell_tpl.build(t, sections=sections, title="本周科研任务", remain="6天")
    fields = {r["field"] for r in c.regions}
    for need in ("ov:card_color", "ov:title_color", "ov:text_color",
                 "ov:red_color", "ov:type_color", "ov:name_color", "ov:dim_color",
                 "ov:bubble_bg", "ov:remain_color", "ov:line_color"):
        assert need in fields, "netracell 缺少热区 %s" % need
    # border_color / card_alpha 随 ov:card_color 弹窗可达
    card_reg = next(r for r in c.regions if r["field"] == "ov:card_color")
    assert card_reg["values"].get("ov:border_color"), "border_color 应随卡片壳弹窗可达"
    assert card_reg["values"].get("ov:card_alpha") == 100, "card_alpha 应随卡片壳弹窗可达（缺省 100）"
    # bubble_line / bubble_text 随 ov:bubble_bg 弹窗可达
    bub_reg = next(r for r in c.regions if r["field"] == "ov:bubble_bg")
    assert bub_reg["values"].get("ov:bubble_line") and bub_reg["values"].get("ov:bubble_text")
    # 其余独立 ov: 区域必须带 values
    for r in c.regions:
        if r["field"].startswith("ov:") and r["field"] not in ("ov:card_color", "ov:bubble_bg"):
            assert r.get("values"), "区域 %s 缺少 values" % r["field"]


def test_ov_color_pal_fallback_and_override():
    """_pal：未配置回落硬编码默认；配置后返回专属值。"""
    t0 = _theme(bg_overrides={})
    pal0 = fissure_tpl._pal(t0)
    assert pal0["title_color"] == fissure_tpl.C_TITLE, "未配置回落默认"
    assert pal0["bubble_bg"] == fissure_tpl.C_BUB_BG
    t1 = _theme(bg_overrides={"fissure": {"title_color": "#FF0000", "bubble_bg": "#123456"}})
    pal1 = fissure_tpl._pal(t1)
    assert pal1["title_color"] == "#FF0000", "专属覆盖生效"
    assert pal1["bubble_bg"] == "#123456"
    assert pal1["card_color"] == fissure_tpl.C_CARD, "未覆盖键仍走默认"


# ---- 卡片底色透明度（ov:card_alpha，2026-09-24 用户反馈：颜色可改但透明度调不了）----
def test_card_alpha_override_validated():
    """card_alpha：0-100 夹取、非法剥掉。"""
    lay = validate_layout({"bg_overrides": {
        "fissure": {"card_alpha": 60},
        "netracell": {"card_alpha": 150},          # 越界 → 夹到 100
        "guide": {"card_alpha": "abc"},            # 非法 → 剥掉
    }})
    ov = lay["bg_overrides"]
    assert ov["fissure"]["card_alpha"] == 60
    assert ov["netracell"]["card_alpha"] == 100, "越界夹取"
    assert "card_alpha" not in ov.get("guide", {}), "非法值剥掉"


def test_card_alpha_takes_effect_and_default_byte_identical():
    """透明度 <100 必须真的改变渲染；缺省/100 时与旧版逐字节一致（无创）。"""
    kw_f = dict(groups=[], title="虚空裂隙", intro="")
    kw_n = dict(sections=[], title="本周科研任务", remain="")
    base_f = render_raw("fissure", _theme(bg_overrides={}), **kw_f)
    base_n = render_raw("netracell", _theme(bg_overrides={}), **kw_n)
    # 显式 100 == 缺省（逐字节一致）
    assert base_f == render_raw("fissure", _theme(bg_overrides={"fissure": {"card_alpha": 100}}), **kw_f)
    assert base_n == render_raw("netracell", _theme(bg_overrides={"netracell": {"card_alpha": 100}}), **kw_n)
    # <100 生效
    assert base_f != render_raw("fissure", _theme(bg_overrides={"fissure": {"card_alpha": 50}}), **kw_f)
    assert base_n != render_raw("netracell", _theme(bg_overrides={"netracell": {"card_alpha": 50}}), **kw_n)
    # 不误伤另一张固定卡
    assert base_n == render_raw("netracell", _theme(bg_overrides={"fissure": {"card_alpha": 50}}), **kw_n)
    assert base_f == render_raw("fissure", _theme(bg_overrides={"netracell": {"card_alpha": 50}}), **kw_f)


def test_card_alpha_helper_clamps():
    """_card_alpha：缺省 100、越界夹取、非法回落 100。"""
    assert fissure_tpl._card_alpha(_theme(bg_overrides={})) == 100
    assert fissure_tpl._card_alpha(_theme(bg_overrides={"fissure": {"card_alpha": 30}})) == 30
    assert fissure_tpl._card_alpha(_theme(bg_overrides={"fissure": {"card_alpha": 999}})) == 100
    assert fissure_tpl._card_alpha(_theme(bg_overrides={"fissure": {"card_alpha": "x"}})) == 100
    assert netracell_tpl._card_alpha(_theme(bg_overrides={"netracell": {"card_alpha": 0}})) == 0


# ============================================================================
# 元素微调（elem_pos / elem_scl，2026-09-24 全量自由编辑）
# 契约：bg_overrides[tpl].elem_pos {元素键:[dx,dy]} / elem_scl {元素键:百分比}——
#   * 拖动 = 像素偏移（不参与排版流）；缩放 = 字号/行高缩放（参与排版流，卡高自适应）；
#   * 未配置 → 渲染与旧版逐字节一致（无创）；
#   * 清洗层：键白名单、坐标 ±4000 夹取、缩放 30~300 夹取、(0,0)/100 不落盘、非法剥掉；
#   * 每个可编辑元素的热区带 ek 元素键（前端拖拽/缩放写回的依据）。
# ============================================================================
def test_elem_tf_validated():
    """清洗层：elem_pos/elem_scl 白名单 + 夹取 + (0,0)/100 不落盘 + 非法剥掉。"""
    lay = validate_layout({"bg_overrides": {"fissure": {
        "elem_pos": {"ok": [9999, -9999], "zero": [0, 0], "bad key!": [1, 2],
                     "halfway": [12, "x"]},            # 越界夹取 / 0不落盘 / 非法键剥 / 非法值剥
        "elem_scl": {"big": 999, "tiny": 5, "mid": 100, "bad": "z"},  # 300/30 夹取 / 100不落盘 / 非法剥
        "evil": 1,
    }}})
    f = lay["bg_overrides"]["fissure"]
    assert f["elem_pos"] == {"ok": [4000, -4000]}, f
    assert f["elem_scl"] == {"big": 300, "tiny": 30}, f
    assert "evil" not in f


def test_elem_tf_takes_effect_and_shifts_bounds():
    """fissure 标题 偏移+缩放：热区随偏移平移、随缩放变高，卡片高度自适应。"""
    from src.plugins.qq_bridge.pic.canvas import Canvas
    kw = dict(groups=[], title="虚空裂隙", intro="")
    t0 = _theme(bg_overrides={})
    t1 = _theme(bg_overrides={"fissure": {"elem_pos": {"title": [40, 20]},
                                           "elem_scl": {"title": 200}}})
    c0 = fissure_tpl.build(t0, **kw)
    c1 = fissure_tpl.build(t1, **kw)
    r0 = next(r for r in c0.regions if r.get("ek") == "title")
    r1 = next(r for r in c1.regions if r.get("ek") == "title")
    # y 精确 +20（标题从页面顶部开始，无居中补偿）
    assert r1["bounds"][1] - r0["bounds"][1] == 20
    # 缩放 200% → 热区高度约 2 倍；卡片高度参与排版流变大
    assert (r1["bounds"][3] - r1["bounds"][1]) > (r0["bounds"][3] - r0["bounds"][1]) * 1.8
    assert c1.height > c0.height
    # 渲染像素真的变了
    from src.plugins.qq_bridge.pic import render_raw
    assert render_raw("fissure", t0, **kw) != render_raw("fissure", t1, **kw)


def test_elem_tf_default_byte_identical():
    """未配 elem_pos/elem_scl（或配空/配 100）→ 渲染与旧版逐字节一致（无创）。"""
    kw_f = dict(groups=[], title="虚空裂隙", intro="")
    kw_n = dict(sections=[], title="本周科研任务", remain="")
    base_f = render_raw("fissure", _theme(), **kw_f)
    base_n = render_raw("netracell", _theme(), **kw_n)
    assert base_f == render_raw("fissure", _theme(bg_overrides={"fissure": {
        "elem_pos": {}, "elem_scl": {"title": 100}}}), **kw_f)
    assert base_n == render_raw("netracell", _theme(bg_overrides={"netracell": {
        "elem_pos": {"title": [0, 0]}, "elem_scl": {}}}), **kw_n)
    # 不误伤：fissure 的微调不影响 netracell / guide
    t_f = _theme(bg_overrides={"fissure": {"elem_pos": {"title": [30, 30]},
                                            "elem_scl": {"title": 150}}})
    assert base_n == render_raw("netracell", t_f, **kw_n)
    assert render_raw("guide", _theme(), body="预览") == render_raw("guide", t_f, body="预览")


def test_netracell_bubble_tf_takes_effect():
    """科研卡气泡独立缩放：气泡热区变宽、卡片变高。"""
    sections = [{"highlight": "深层",
                 "missions": [{"type": "刺杀", "name": "Vor", "risks": []}],
                 "extras": ["额外奖励：真空 Mod", "额外奖励：虚空风暴加成"]}]
    kw = dict(sections=sections, title="本周科研任务", remain="6天")
    t0 = _theme(bg_overrides={})
    t1 = _theme(bg_overrides={"netracell": {"elem_pos": {"bub-0-0": [10, -6]},
                                             "elem_scl": {"bub-0-0": 200}}})
    c0 = netracell_tpl.build(t0, **kw)
    c1 = netracell_tpl.build(t1, **kw)
    b0 = next(r for r in c0.regions if r.get("ek") == "bub-0-0")
    b1 = next(r for r in c1.regions if r.get("ek") == "bub-0-0")
    assert b1["bounds"][0] - b0["bounds"][0] == 10
    assert b1["bounds"][1] - b0["bounds"][1] == -6
    assert (b1["bounds"][2] - b1["bounds"][0]) > (b0["bounds"][2] - b0["bounds"][0])
    assert c1.height > c0.height


def test_fixed_card_regions_carry_ek():
    """固定风格卡的可编辑元素热区必须带 ek 元素键（前端拖拽/缩放写回依据）。"""
    t = _theme(bg_overrides={})
    cf = fissure_tpl.build(t, groups=[{"label": "G", "rows": [
        {"bubble": "b", "node": "n", "remain": "r"}]}],
        title="虚空裂隙", intro="说明", footers=["* 提示"])
    eks_f = {r.get("ek") for r in cf.regions}
    for need in ("title", "line", "intro", "foot", "tier-0", "bub-0-0",
                 "node-0-0", "rem-0-0"):
        assert need in eks_f, "fissure 缺少元素键 %s" % need
    cn = netracell_tpl.build(t, sections=[{"highlight": "深层",
        "missions": [{"type": "刺杀", "name": "Vor", "risks": []}],
        "extras": ["额外：真空"]}], title="本周科研任务", remain="6天")
    eks_n = {r.get("ek") for r in cn.regions}
    for need in ("title", "line", "sect-0", "miss-0-0", "bub-0-0", "remain"):
        assert need in eks_n, "netracell 缺少元素键 %s" % need
