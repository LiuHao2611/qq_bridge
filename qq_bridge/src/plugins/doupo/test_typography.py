# -*- coding: utf-8 -*-
"""斗破四张卡的统一排版验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_typography.py     # 自带 runner
    pytest src/plugins/doupo/test_typography.py

用户 2026-11-14：「字体问题不只是斗技这一个卡片，所有的斗破游戏卡片都要进行优化」。
本测试锁定：四张卡共用同一份字号/字重规范、重点文字真的加粗、小字真的不糊、
四张卡都能渲染出合法 PNG，并导出截图供人工视觉核对。
产物：tmp/doupo_typography/*.png
"""
from __future__ import annotations

import importlib
import io
import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = os.path.join(REPO_ROOT, "tmp", "doupo_typography")
os.makedirs(OUT_DIR, exist_ok=True)


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
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import card, core  # noqa: E402
from PIL import Image  # noqa: E402

TEMPLATES = ("doupo", "doupo_menu", "doupo_list", "doupo_skills")


def _tpl(name):
    # 模板挂在 card 的独立 pic 包下，必须先触发加载（runner 按函数名排序，
    # 第一个用例就可能还没调过 _pic()）
    _pic()
    return importlib.import_module(card._PKG_NAME + ".templates." + name)


def _pic():
    pic = card._load_pic()
    assert pic is not None
    return pic


def _ink(c) -> int:
    raw = c.img.convert("L").tobytes()
    return sum(1 for v in raw if v < 128)


# ---------------------------------------------------------------------------
# 1. 规范契约：四张卡同源，重点字号达标
# ---------------------------------------------------------------------------
def test_all_four_cards_use_shared_typography():
    for name in TEMPLATES:
        m = _tpl(name)
        src = open(m.__file__, encoding="utf-8").read()
        assert "doupo_style" in src, "%s 没有引用共用排版规范" % name
    S = importlib.import_module(card._PKG_NAME + ".templates.doupo_style")
    assert S.MIN_BOLD_FS == 22
    assert S.FS_HERO > S.FS_KEY > S.FS_BODY > S.FS_SUB > S.FS_LABEL


def test_key_font_sizes_meet_the_spec():
    S = importlib.import_module(card._PKG_NAME + ".templates.doupo_style")
    # 一级重点：>=24（放大加粗）
    strong = {
        "doupo": ("FS_NAME", "FS_STAT"),                   # 角色名 / 六维数值
        "doupo_menu": ("FS_CMD",),                         # 口令
        "doupo_list": ("FS_ITEM",),                        # 商品名 / 价格 / 战力
        "doupo_skills": ("FS_LV", "FS_NAME", "FS_VALUE", "FS_ITEM"),
    }
    # 二级重点：>=22（刚好达到可加粗阈值）
    boldable = {
        "doupo": ("FS_BODY",),                             # 武器名 / 炼药师
        "doupo_menu": ("FS_GROUP",),                       # 分组名
        "doupo_list": (),
        "doupo_skills": ("FS_RULE",),
    }
    for name, fields in strong.items():
        for f in fields:
            v = getattr(_tpl(name), f)
            assert v >= S.FS_KEY, "%s.%s=%s 小于一级重点字号 %s" % (
                name, f, v, S.FS_KEY)
    for name, fields in boldable.items():
        for f in fields:
            v = getattr(_tpl(name), f)
            assert v >= S.MIN_BOLD_FS, "%s.%s=%s 达不到可加粗阈值" % (name, f, v)
    # 说明文字清晰下限：不能比规范小，也不能被拿去加粗
    for name, field in (("doupo", "FS_SMALL"), ("doupo_menu", "FS_DESC"),
                        ("doupo_list", "FS_SMALL"), ("doupo_skills", "FS_DESC")):
        assert getattr(_tpl(name), field) >= S.FS_SUB


def test_emphasis_is_really_bold_and_small_text_is_not():
    """S.key 与 S.small 画同一串字：重点应更粗；<22px 两者必须逐像素一致。"""
    S = importlib.import_module(card._PKG_NAME + ".templates.doupo_style")
    Canvas = _pic().Canvas

    def draw(fn, size):
        c = Canvas(360, 70, "#FFFFFF")
        fn(c, (10, 10), "战力 12345", size=size, color="#000000")
        return _ink(c), c.to_bytes()

    ink_k, b_k = draw(S.key, 24)
    ink_s, b_s = draw(S.small, 24)
    assert b_k != b_s and ink_k > ink_s, "重点文字没有真的加粗"
    # 小字：key 自动降级为不加粗，与 small 完全一致（防小字糊化回归）
    ink_k2, b_k2 = draw(S.key, 17)
    ink_s2, b_s2 = draw(S.small, 17)
    assert b_k2 == b_s2 and ink_k2 == ink_s2, "小字被加粗了，会糊成一团"


# ---------------------------------------------------------------------------
# 2. 四张卡真实出图（render_sync 绕开磁盘缓存，杜绝旧图假通过）
# ---------------------------------------------------------------------------
def _render(template: str, data: dict, card_name: str) -> bytes:
    pic = _pic()
    return pic.render_sync(template, "light", card_name, width=720, data=data)


def _player():
    p = core.create_player("10001", "萧炎", "900000001")
    p["lv"] = 40
    p["weapon"] = 3
    p["skills"] = {"龙吟碎": 3, "天火三玄变": 1, "八极崩": 5, "五轮离火法": 2}
    p["bag"] = {"回灵丹": 3, "血灵草": 2, "炎星石": 1, "焚血散": 2}
    p["gold"] = 5200
    return p


def test_four_cards_render_valid_png_and_export():
    p = _player()
    cases = [
        ("01_panel.png", "doupo", card.build_data(p), card.CARD_NAME),
        ("02_menu.png", "doupo_menu", card.build_menu_data(), card.CARD_MENU_NAME),
        ("03_list.png", "doupo_list", card.build_shop_data(p), "斗破宝库"),
        ("04_skills.png", "doupo_skills", card.build_skills_data(p), "斗破斗技"),
    ]
    for fn, tpl, data, cname in cases:
        png = _render(tpl, data, cname)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000, tpl
        img = Image.open(io.BytesIO(png)).convert("RGB")
        assert img.width == 720 and 400 < img.height < 2200, (
            "%s 尺寸异常 %s" % (tpl, img.size))
        raw = img.resize((80, 80)).tobytes()
        colors = len({raw[i:i + 3] for i in range(0, len(raw), 3)})
        assert colors > 20, "%s 疑似空白卡" % tpl
        with open(os.path.join(OUT_DIR, fn), "wb") as f:
            f.write(png)


def test_other_list_cards_render_and_export():
    """通用列表卡还有异火/背包/排行三种形态，字段不同，也要出图核对。"""
    p = _player()
    other = core.create_player("10002", "药老", "900000001")
    other["lv"] = 62
    cases = [
        ("05_flames.png", card.build_flames_data(p), "斗破异火"),
        ("06_bag.png", card.build_bag_data(p), "斗破背包"),
        ("07_rank.png", card.build_rank_data(core.rank([p, other])[:10]), "斗破排行"),
    ]
    for fn, data, cname in cases:
        png = _render("doupo_list", data, cname)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1500, cname
        img = Image.open(io.BytesIO(png))
        assert img.width == 720 and 300 < img.height < 2400, cname
        with open(os.path.join(OUT_DIR, fn), "wb") as f:
            f.write(png)


def test_avatar_box_did_not_drift():
    """人物卡字号变大不能把头像框挤走（历史像素断言依赖 (28,96)-(124,192)）。"""
    png = _render("doupo", card.build_data(_player()), card.CARD_NAME)
    img = Image.open(io.BytesIO(png)).convert("RGB").crop((28, 96, 124, 192))
    raw = img.tobytes()
    dark = sum(1 for i in range(0, len(raw), 3)
               if raw[i] < 120 and raw[i + 1] < 120 and raw[i + 2] < 140)
    assert dark > 1500, "头像框像素漂移或头像没画进去：%d" % dark


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    fail = []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print("PASS  %s" % name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print("FAIL  %s  ->  %s: %s" % (name, type(e).__name__, e))
    print("=" * 70)
    print("TYPOGRAPHY_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
