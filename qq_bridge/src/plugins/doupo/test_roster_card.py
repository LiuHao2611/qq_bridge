# -*- coding: utf-8 -*-
"""本宗斗技名录卡验收（零依赖、exit-code 可验证）。

守用户感受得到的行为：
  * 「斗技 全部」必须出**卡片**，不能拿纯文本糊弄；
  * 卡片画的是本宗**全部**斗技（含未解锁、已收服异火的主动技），
    不是只看四槽上阵项——用户上次只上阵 1 门就以为「斗技只剩一个」；
  * 玩家只上阵 1 门时，名录卡仍应列出全部门技；
  * 渲染出的 PNG 合法且尺寸在可用范围内。

运行：
    python src/plugins/doupo/test_roster_card.py
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for mod in ("nonebot.adapters", "nonebot.adapters.onebot",
                "nonebot.adapters.onebot.v11"):
        sys.modules[mod] = types.ModuleType(mod)
    for attr in ("Message", "MessageEvent", "MessageSegment"):
        setattr(sys.modules["nonebot.adapters.onebot.v11"], attr, object)


_stub_nonebot()

from doupo import card, config as cfg, core  # noqa: E402


def _p(lv=48, faction=None):
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    if faction:
        p["faction"] = faction
        p["faction_name"] = faction
    return p


def test_roster_covers_every_faction_skill():
    p = _p()
    data = card.build_roster_data(p)
    pool = core.skill_pool(p)
    assert len(data["rows"]) == len(pool) > 1, "名录卡没画出本宗全部斗技"
    assert {r["title"] for r in data["rows"]} == {e["name"] for e in pool}
    assert f"共 {len(pool)} 门" in data["subtitle"]


def test_only_one_equipped_still_lists_all():
    """真实事故：玩家四槽只上阵 1 门，卡片不能因此只剩 1 行。

    2026-11-15 梳理后新增 ``ensure_loadout`` 自动补满四槽（旧版只在空列表时补，
    导致「斗技就剩一个」）。这里同时锁住两个契约：四槽必须补满、
    名录卡仍画全部斗技（而不是只画那 4 个上阵项）。
    """
    p = _p()
    usable = [e["name"] for e in core.skill_pool(p) if e["unlocked"]]
    assert usable, "测试前提：本宗至少有 1 门已解锁斗技"
    core.set_loadout(p, usable[:1])
    lo = core.loadout_entries(p)
    assert len(lo) == 4, "四槽未自动补满：%s" % [e["name"] for e in lo]
    data = card.build_roster_data(p)
    assert len(data["rows"]) == len(core.skill_pool(p)), "名录卡退化成只画上阵项"
    assert len(data["rows"]) > len(lo), "名录卡至少要比四槽多列未上阵斗技"


def test_locked_and_flame_skills_are_marked():
    low = _p(lv=1)
    rights = [str(r["right"]) for r in card.build_roster_data(low)["rows"]]
    assert any("未解锁" in r for r in rights), "未解锁斗技没标注"

    p = _p()
    p["flames"]["青莲地心火"] = 1
    subs = [str(r["sub"]) for r in card.build_roster_data(p)["rows"]]
    assert any("异火技" in s for s in subs), "已收服异火的主动技没进名录卡"


def test_roster_rows_carry_multidim_tags():
    """2026-11-15：名录每行必须带主定位 + 多维效果标签 + 门控，
    而不是只有「技能名 + 一句 brief」——否则玩家只能比数字大小。"""
    from doupo import factions as fac

    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    rows = card.build_roster_data(p)["rows"]
    pool_n = len(core.skill_pool(p))
    assert len(rows) == pool_n, "名录卡行数应等于运行池门数：%d vs %d" % (
        len(rows), pool_n)
    assert pool_n >= 10, "天冥宗运行池异常偏少：%d" % pool_n
    assert any("〔" in r["sub"] and "〕" in r["sub"] for r in rows), "没画主定位标签"
    assert any(r["note"] for r in rows), "没画多维效果 / 门控行"
    assert all("note" in r for r in rows), "note 字段必须存在（可为空串）"
    # 每门至少有一条可见维度信息：主定位、效果 tag、或门控代价
    for r in rows:
        assert ("〔" in r["sub"]) or r["note"], "「%s」没有任何维度信息" % r["title"]
    # 复合技能必须拆成多个可见维度，而不是一个数字
    by_name = {r["title"]: r for r in rows}
    assert "血毒" in by_name["血煞掌"]["note"] and "减疗" in by_name["血煞掌"]["note"], (
        "复合技能血煞掌没拆成多维标签：%s" % by_name["血煞掌"]["note"])
    assert "限：" in by_name["嗜血化骨手"]["note"], "门控代价没展示"


def _check_boxes(bounds, boxes, min_gap: int = 2) -> list[str]:
    """与 card-design skill 同规则：溢出 / 两方向重叠 / 同行间距过小。"""
    bx0, by0, bx1, by1 = bounds
    problems: list[str] = []
    items = list(boxes)
    for label, x0, y0, x1, y1 in items:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越界: (%d,%d,%d,%d) bounds=%s"
                            % (label, x0, y0, x1, y1, bounds))
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            la, a = items[i][0], items[i][1:]
            lb, b = items[j][0], items[j][1:]
            if not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]):
                problems.append("[重叠] %s <-> %s: %s vs %s" % (la, lb, a, b))
            if a[1] < b[3] and b[1] < a[3]:
                gap_x = b[0] - a[2] if a[0] <= b[0] else a[0] - b[2]
                if 0 <= gap_x < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx" % (la, lb, gap_x))
    return problems


def test_roster_passes_check_boxes():
    """card-design 门禁：真实天冥宗 15 行名录卡（doupo_roster 专用模板），
    溢出 0 / 重叠 0 / 间距 0。"""
    import importlib

    from doupo import factions as fac

    pic = card._load_pic()
    assert pic is not None
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_roster")
    theme = importlib.import_module(card._PKG_NAME + ".theme").get("light")

    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    data = card.build_roster_data(p)
    boxes: list[tuple] = []
    c = tpl.build(theme, data=data, width=720, boxes=boxes)
    assert len(boxes) > len(data["rows"]) * 5, (
        "收集到的包围盒太少，自检会假通过：%d" % len(boxes))
    problems = _check_boxes((12, 12, 720 - 12, c.height - 12), boxes)
    assert not problems, "名录卡触线：\n" + "\n".join(problems[:10])


def test_roster_shows_full_desc():
    """用户 2026-11-15：「技能描述加上，别偷懒」——每门必须展示**完整**描述。

    名录卡不许只放 brief 一句。剥离末尾「限制：…」段是允许的（代价由 gate 行
    单独展示），但剥离后仍须保留机制段，且被剥掉的内容必须能在 gate 里找到，
    不能凭空空掉。
    """
    import importlib

    from doupo import factions as fac

    pic = card._load_pic()
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_roster")
    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    data = card.build_roster_data(p)
    rows = data["rows"]
    inner_w = tpl._cols(720)[2]
    for r in rows:
        desc = str(r.get("desc") or "")
        assert len(desc) >= 40, "%s 描述过短（%d 字），疑似退回 brief" % (r["name"], len(desc))
        assert "限制：" not in desc, "%s 描述没剥离代价段，会和「限：」行重复" % r["name"]
        assert r.get("gate"), "%s 剥掉了描述里的代价却没有 gate，信息丢了" % r["name"]
        lines = tpl._desc_lines(r, inner_w)
        assert len(lines) >= 2, "%s 描述只折出 %d 行，可能没渲染" % (r["name"], len(lines))
        assert "".join(lines) == desc, "%s 描述折行丢字" % r["name"]


def test_roster_is_two_columns():
    """用户 2026-11-15：「做成两列」——分区内每行两块、同 y、左右列起点固定。"""
    import importlib

    from doupo import factions as fac

    pic = card._load_pic()
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_roster")
    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    data = card.build_roster_data(p)
    L = tpl._plan(data, 720)
    pairs = [e for e in L["entries"] if e["kind"] == "pair"]
    assert pairs, "没有任何成对的行，不是两列布局"
    full = [e for e in pairs if len(e["cells"]) == 2]
    # 按阶位分组后，每个奇数门分区各允许 1 个单块（15 门 = 3 个分区可各留 1 块）
    groups_n = len(data["groups"])
    singles = [e for e in pairs if len(e["cells"]) == 1]
    assert len(singles) <= groups_n, (
        "每个分区最多 1 个单块，实测 %d 个单块 / %d 个分区"
        % (len(singles), groups_n))
    assert len(full) >= len(pairs) - groups_n, (
        "并排两块的行数过少：%d/%d" % (len(full), len(pairs)))

    x_left = full[0]["cells"][0]["x"]
    x_right = full[0]["cells"][1]["x"]
    assert x_left < x_right, "两列 x 顺序错"
    for e in full:
        a, b = e["cells"]
        assert a["x"] == x_left, "左列起点漂移：%d vs %d" % (a["x"], x_left)
        assert b["x"] == x_right, "右列起点漂移：%d vs %d" % (b["x"], x_right)
        assert a["w"] == b["w"], "两列宽度不一致"
        assert e["h"] >= max(c["h"] for c in e["cells"]), "等高计算小于内容高（会裁切）"
        # 右列必须落在卡片内容区内
        assert b["x"] + b["w"] <= 720 - tpl.PAD + 1, "右列越出内容区"
    # 单数门的分区允许只有 1 块（如特殊斗技 1 门）
    assert any(len(e["cells"]) == 1 for e in pairs), "单数分区应允许单块"


def test_roster_uses_dedicated_template():
    """名录卡必须走专用模板（按阶位分组 + 卡片块），不许退回通用列表卡。

    2026-11-15 用户第二次说「优化这个卡片，怎么没优化」——上一版只是把多维
    信息塞进 doupo_list 的行里，视觉上仍是一张无分组无分隔的表格。
    """
    pic = card._load_pic()
    assert pic is not None
    assert "doupo_roster" in pic.list_templates(), "专用名录卡模板未注册"
    assert pic.TEMPLATE_LABELS.get("doupo_roster") == "斗破斗技名录卡"
    # render_roster 必须走 doupo_roster（源码契约，防止被顺手改回去）
    import inspect
    src = inspect.getsource(card.render_roster)
    assert '"doupo_roster"' in src, "render_roster 没有使用专用模板"
    # 通用列表卡必须保留给异火/宝库/背包/排行
    assert "doupo_list" in pic.list_templates()


def test_roster_groups_by_tier():
    """按阶位分组：普通 / 高阶 / 特殊，顺序与 SKILL_TIERS 一致，门数不丢。"""
    from doupo import factions as fac
    from doupo import config as cfg

    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    d = card.build_roster_data(p)
    groups = d["groups"]
    assert groups, "没有分组，又退回一张平铺表格"
    tiers = [g["tier"] for g in groups]
    order = [t for t in cfg.SKILL_TIERS if t in tiers]
    assert tiers == order, "分组顺序与 SKILL_TIERS 不一致：%s vs %s" % (tiers, order)
    assert sum(g["count"] for g in groups) == len(d["rows"])
    assert len(d["rows"]) >= 10, "名录卡门数异常偏少：%d" % len(d["rows"])
    for g in groups:
        assert g["rows"] and g["count"] == len(g["rows"])
        assert g.get("icon"), "%s 分区缺图标" % g["tier"]


def test_row_meta_has_no_duplicated_word():
    """副行不许出现「秘法 · 秘法」这种 rank 与 kind 同名的重复（真实踩过）。"""
    from doupo import factions as fac

    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    rows = card.build_roster_data(p)["rows"]
    by_name = {r["title"]: r for r in rows}
    # 九幽魂手：rank=秘法 / kind=秘法
    assert "秘法 · 秘法" not in by_name["九幽魂手"]["meta"], (
        "副行把阶位与类型重复拼了两次：%s" % by_name["九幽魂手"]["meta"])
    for r in rows:
        assert " ·  · " not in r["meta"], "副行有连续分隔符：%s" % r["meta"]
        assert not r["meta"].startswith(" · "), "副行以分隔符开头：%s" % r["meta"]


def test_lock_state_pill_fits_for_low_level():
    """低等级玩家的「未解锁 lv50」是最长状态标签，不能撑爆块或压住技能名。"""
    import importlib

    from doupo import factions as fac

    pic = card._load_pic()
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_roster")
    theme = importlib.import_module(card._PKG_NAME + ".theme").get("light")

    p = _p(lv=1, faction="tianmingzong")   # lv1：绝大多数高阶/特殊技未解锁
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    data = card.build_roster_data(p)
    locked = [r for r in data["rows"] if r["state_kind"] == "lock"]
    assert locked, "测试前提：lv1 应有未解锁斗技"
    boxes: list[tuple] = []
    c = tpl.build(theme, data=data, width=720, boxes=boxes)
    problems = _check_boxes((12, 12, 720 - 12, c.height - 12), boxes)
    assert not problems, "未解锁长标签把布局撑坏：\n" + "\n".join(problems[:10])
    # 状态标签文字必须真的出现（不是被 _fit 截成空）
    labels = [b[0] for b in boxes]
    assert any(".state" in x for x in labels), "状态 pill 没被绘制/登记"


def test_desc_first_sentence_is_skill_source():
    """用户 2026-11-15 纠正：「不要写原著多少章这种描述，你见过哪个游戏的
    技能描述这样写的？」

    技能说明首句 = 「<传承来源>，<施展流程>…」：来源只取 skill_book.source，
    用逗号并入描述，不许写章节号 / 「出处：」标题 / 来源单独成句；
    没有来源的技能不加任何前缀。
    """
    from doupo import factions as fac
    from doupo import skill_effects as fx

    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    pool = {e["name"]: e for e in core.skill_pool(p)}
    rows = card.build_roster_data(p)["rows"]
    with_source = 0
    for r in rows:
        phrase = str(r.get("source_phrase") or "")
        desc = str(r.get("desc") or "")
        assert bool(phrase) == bool(r.get("has_source")), (
            "%s 的 has_source 与 source_phrase 不一致" % r["name"])
        assert phrase == fx.source_phrase(pool[r["name"]]), (
            "%s 的来源不是 skill_book.source 原文" % r["name"])
        assert not fx.CITATION_RE.search(desc), (
            "%s 的描述写成小说注脚了：%s" % (r["name"], desc[:36]))
        for tag in ("出处：", "来源：", "原著："):
            assert tag not in desc[:24], (
                "%s 把来源写成了独立标题「%s」" % (r["name"], tag))
        if phrase:
            with_source += 1
            assert desc.startswith(phrase + "，"), (
                "%s 的来源没并进描述第一句：%s" % (r["name"], desc[:36]))
            assert not desc.startswith(phrase + "。"), (
                "%s 的来源单独成句，不像技能描述" % r["name"])
            assert len(desc) > len(phrase) + 8, (
                "%s 来源后面没有施展流程" % r["name"])
        else:
            assert not desc.startswith("，"), "%s 没有来源却留了逗号前缀" % r["name"]
        assert len(desc) >= 40, "%s 描述过短" % r["name"]
    # 2026-11-16 新口径：来源必须写「怎么来的」，查不到就留空。
    # 不再要求「绝大多数都有来源」——那会逼着写「某某所使」冒充来源。
    assert with_source >= 1, "名录卡一门来源都没有，来源层可能没接上"
    # 检测本身必须认得出这次真实犯过的旧写法（否则门禁会形同虚设）
    assert fx.CITATION_RE.search(
        "原著第一千一百九十章「承让」载，天冥宗易尘所使。斗气凝血成掌……"), (
        "旧章节引用写法必须被识别")


def test_state_badge_is_prominent():
    """用户 2026-11-15：「右上角提示要更明显更显眼」。

    旧版三态共用 cell_bg 淡底 + 12px，肉眼几乎看不见。守高对比实底 /
    字号高度 / 三态区分，并用真错注入证明这道检查真的会报错。
    """
    import importlib

    card._load_pic()
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_roster")
    assert not tpl.state_badge_problems(), "当前状态徽章不合格：" + str(
        tpl.state_badge_problems())
    assert tpl.state_badge_problems(fs=12, h=18), "退回 12px/18px 小胶囊必须报错"
    pale = {"on": ("#FBEEDD", "#EBC79A", "#9E1B32"),
            "learn": ("#F7F1E9", "#E6DCCB", "#8A7C6B"),
            "lock": ("#F7F1E9", "#E6DCCB", "#8A7C6B")}
    assert tpl.state_badge_problems(style=pale), "退回淡底且三态同色必须报错"
    for kind, (fill, _ring, ink) in tpl.STATE_COLORS.items():
        assert tpl._luminance(fill) < 170, "%s 底色太浅" % kind
        assert tpl._luminance(ink) - tpl._luminance(fill) >= 80, (
            "%s 底色与文字对比不足，浅字会糊" % kind)


def test_skill_names_not_truncated_by_wider_badge():
    """徽章放大后名字可用宽度变小，技能名不能被 _fit 截成「…」。"""
    import importlib

    from doupo import factions as fac

    card._load_pic()
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_roster")
    theme = importlib.import_module(card._PKG_NAME + ".theme").get("light")
    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    data = card.build_roster_data(p)
    boxes: list[tuple] = []
    tpl.build(theme, data=data, width=720, boxes=boxes)
    by_label = {b[0]: b for b in boxes}
    for r in data["rows"]:
        key = "%s.name" % r["name"]
        assert key in by_label, "%s 的技能名没被绘制/登记" % r["name"]
        w_box = by_label[key][3] - by_label[key][1]
        w_full = tpl._w(r["name"], tpl.FS_NAME, "bold")
        assert w_box == w_full, "%s 的技能名被截断（登记宽 %d < 实测 %d）" % (
            r["name"], w_box, w_full)


def test_meta_wrap_keeps_unlock_level_intact():
    """肉眼核对抓到的真实缺陷：低等级玩家副行「lv20 解锁」被截成「lv…」，
    改成逐字折行后又会把数字拆成「lv2」/「0 解锁」。折行必须按语义段整段搬，
    且每行都不越界、不与描述重叠。
    """
    import importlib
    import re

    from doupo import factions as fac

    card._load_pic()
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_roster")
    theme = importlib.import_module(card._PKG_NAME + ".theme").get("light")
    p = _p(lv=1, faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    data = card.build_roster_data(p)
    inner_w = tpl._cols(720)[2]
    checked = 0
    for r in data["rows"]:
        meta = str(r["meta"])
        lines = tpl._meta_lines(r, inner_w)
        assert lines, "%s 的副行没折出任何行" % r["name"]
        for ln in lines:
            assert tpl._w(ln, tpl.FS_META, "regular") <= inner_w + 1, (
                "%s 副行越界：%s" % (r["name"], ln))
        m = re.search(r"lv\d+", meta)
        if m:
            checked += 1
            assert any(m.group(0) in ln and "解锁" in ln for ln in lines), (
                "%s 的解锁等级被截断或拆字：%s" % (r["name"], lines))
    assert checked >= 4, "lv1 应有多门未解锁斗技，实测 %d" % checked

    boxes: list[tuple] = []
    c = tpl.build(theme, data=data, width=720, boxes=boxes)
    problems = _check_boxes((12, 12, 720 - 12, c.height - 12), boxes)
    assert not problems, "副行折行后布局触线：\n" + "\n".join(problems[:8])


def test_roster_renders_valid_png():
    from PIL import Image

    p = _p()
    png = asyncio.run(card.render_roster(p))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", "没出图 / 不是 PNG"
    im = Image.open(io.BytesIO(png))
    im.verify()
    w, h = im.size
    assert 400 <= w <= 1200 and 200 <= h <= 4000, f"卡片尺寸异常 {w}x{h}"
    assert h > 400, "名录卡太矮，可能没画出多行"


def test_roster_data_is_json_friendly():
    import json
    p = _p()
    json.dumps(card.build_roster_data(p), ensure_ascii=False)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok, fail = 0, []
    for name, fn in fns:
        try:
            fn()
            print("PASS ", name)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("FAIL ", name, "->", type(e).__name__, str(e)[:150])
            fail.append(name)
    print("\nROSTER_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
