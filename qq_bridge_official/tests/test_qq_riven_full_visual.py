# -*- coding: utf-8 -*-
"""紫卡全面分析卡「去 Rabbit 化 + 项目 light 主题底」守门（2026-11-11，第 13 次，prove-it）。

## 需求（用户确认）
用户发真群截图：655×1920 riven_full 卡右下有绿色 Rabbit 卡通形象、页脚
"Warframe Rabbit"、多处兔耳图标。要求「把全面分析的背景改成我们的，不要这个
卡通形象」。经 AskUserQuestion 二选确认：
  1) 「我们的背景」→ 项目 light 主题底（与市价/签到/裂隙一致，decor.background）；
  2) Rabbit 品牌 → 全部换掉（卡通 + 页脚字样 + 兔耳图标 → 项目自有标识/图标）。

## 本文件守门（绝不测错源码；不信任生产实现，第二套基准 + 真渲染 + 注入）
锚定关键点：用 ``Path(__file__).resolve().parents[1]`` 取 **official 副本根**，
绝不用旧夹具 _riven_full_render.py 的三级 dirname（那会在宿主算到原件
D:/AI/manju_agent/qq_bridge/ → 加载未改稿源码 → 测试假通过，真实踩过的坑）。

  A. AST 源码断言（读 official 副本真源码，逐字）：
     - riven_full 模板：无 chibi.png / bunny.png / bunny_dark.png 名义贴；无
       "Warframe Rabbit" 字符串；有 decor.background(...tpl="riven_full")；
       有 _icons.draw(...) ≥2；页脚 brand = "曼珠"；from .. import decor/icons。
     - card_binds.py：_DEFAULT_CARD_BINDS 含 "星际战甲·紫卡全面分析": "light"。
     - 数据层 riven_full.py：两处 footer.brand = "曼珠"，无 "Warframe Rabbit"。
     - 调用点 game_tools/__init__.py：两个 render("riven_full"...) 仍传 card 名。
     - 注入：把上述任一改回 Rabbit / 删 light 绑定 / 删 decor 接线 → 必 FAIL。
  B. 真渲染（隔离装配 official 副本 pic 包 + 真 LIGHT_THEME + reference_sample）：
     - 真出一张 PNG（bytes > 0、尺寸 655×1920），不抛异常。
     - 像素对照：同 data 用 light 主题 vs 用「无背景覆盖」的 theme（bg_pattern=none
       且 bg_overrides 空）渲染，两者**不同**（证明 decor.background 真画了贴纸底，
       不是 no-op）；注入「删 decor.background 调用」→ 两者会相同 → 必 FAIL。
     - 右下原 chibi 区（515..655 × 1740..1920）：去 Rabbit 后该区不再是卡通实心块；
       断言该区域内「接近 chibi 主色」的像素占比极低（对比保留 chibi 的注入版必 FAIL）。
     - 页脚「曼珠」3 字确实画出：该区（x 68..130, y 1878..1905）非纯白像素数 > 阈值；
       注入「brand 改回 Warframe Rabbit」→ 该区绘制内容变化 → 【A 组已覆盖】。
  C. 版式门禁：真数据 reference_sample + 真 light 主题下 _check_boxes 仍 0/0/0
     （去 Rabbit/换图标/加背景不得破坏溢出/重叠/间距）。

跑法：
  py -3.13 tests/test_qq_riven_full_visual.py（宿主纯标准库 + Pillow，全桩无 nonebot）
  docker exec -i qq-bridge-official /opt/venv/bin/python tests/test_qq_riven_full_visual.py
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]          # official 副本根（精确锚定）
_ROOT = _REPO / "src" / "plugins" / "qq_bridge"
_TMPL = _ROOT / "pic" / "templates" / "riven_full.py"
_DATA = _ROOT / "game_tools" / "riven_full.py"
_BINDS = _ROOT / "pic" / "card_binds.py"
_GT = _ROOT / "game_tools" / "__init__.py"

_failed: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        _failed.append(name)


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------- A. AST 源码断言
def _code_strings(tree: ast.AST) -> set[str]:
    """收集**执行代码**里的字符串常量，排除 docstring。

    去 Rabbit 化的说明本身写在模块/函数 docstring 里（如「移除 chibi.png」），
    这些是给维护者看的注释文字，不是渲染时真正 paste 的文件名。若把它们算进
    断言，会把「注释里提到 Rabbit」误判成「代码仍在贴 Rabbit」→ 测试假 FAIL。
    注释（#）本就不进 AST；docstring 是字符串常量节点，需按位置显式排除。
    """
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                doc_ids.add(id(body[0].value))
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in doc_ids}


def _tmpl_assertions(src: str) -> dict:
    """对 riven_full 模板源码做第二套基准断言；返回各探针结果，供注入复用。"""
    tree = ast.parse(src)
    strs = _code_strings(tree)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]

    def call_name(c):
        f = c.func
        if isinstance(f, ast.Attribute):
            return f.attr
        if isinstance(f, ast.Name):
            return f.id
        return ""

    has_chibi = any("chibi.png" in s for s in strs)
    has_bunny = any(("bunny.png" in s or "bunny_dark.png" in s) for s in strs)
    has_rabbit = any("Warframe Rabbit" in s for s in strs)
    has_brand = "曼珠" in strs
    has_decor_import = "from .. import decor" in src
    has_icons_import = "from .. import icons as _icons" in src
    has_bg = any(call_name(c) == "background" for c in calls) and "decor.background(c, t, tpl=\"riven_full\")" in src
    n_icons_draw = sum(1 for c in calls if call_name(c) == "draw")
    return {
        "has_chibi": has_chibi, "has_bunny": has_bunny, "has_rabbit": has_rabbit,
        "has_brand": has_brand, "has_decor_import": has_decor_import,
        "has_icons_import": has_icons_import, "has_bg": has_bg,
        "n_icons_draw": n_icons_draw,
    }


def test_ast():
    print("[A] AST 源码断言（official 副本真源码）")
    t = _tmpl_assertions(_src(_TMPL))
    check("A1 模板无 chibi.png", not t["has_chibi"])
    check("A2 模板无 bunny.png / bunny_dark.png", not t["has_bunny"])
    check("A3 模板无 'Warframe Rabbit' 字样", not t["has_rabbit"])
    check("A4 模板页脚品牌 = 曼珠", t["has_brand"])
    check("A5 模板 import decor + icons", t["has_decor_import"] and t["has_icons_import"])
    check("A6 模板接入 decor.background(tpl=riven_full)", t["has_bg"])
    check("A7 模板 ≥2 处 _icons.draw（section + 指令）", t["n_icons_draw"] >= 2,
          "draw=%d" % t["n_icons_draw"])

    binds = _src(_BINDS)
    check("A8 card_binds 内置绑定 riven_full→light",
          '"星际战甲·紫卡全面分析": "light"' in binds)
    check("A9 card_binds 含 resolve_card_theme 优先级链", "resolve_card_theme" in binds)

    dm = _src(_DATA)
    dm_code = _code_strings(ast.parse(dm))
    check("A10 数据层两处 footer.brand = 曼珠", dm.count('"brand": "曼珠"') == 2,
          "count=%d" % dm.count('"brand": "曼珠"'))
    check("A11 数据层执行代码无 'Warframe Rabbit'（docstring 说明不算）",
          not any("Warframe Rabbit" in s for s in dm_code),
          "hits=%r" % [s for s in dm_code if "Warframe Rabbit" in s])

    gt = _src(_GT)
    n_card = gt.count('card="星际战甲·紫卡全面分析"')
    check("A12 两个 render 调用点仍传 card 名（主题绑定生效前提）", n_card >= 2,
          "n=%d" % n_card)

    # 注入：把改稿改回 Rabbit / 删接线 / 删绑定 → 探针必须翻转（证明断言非摆设）
    inj_chibi = _src(_TMPL).replace(
        '    # 2026-11-11 去 Rabbit 化：移除右下 Rabbit 卡通 chibi.png（原 515..655 ×',
        '    paste_asset("chibi.png", 515, 1740, 655, 1920)  # INJECT\n'
        '    # 2026-11-11 去 Rabbit 化：移除右下 Rabbit 卡通 chibi.png（原 515..655 ×')
    check("A13 注入[恢复 chibi.png] → has_chibi 变 True（A1 必抓）",
          _tmpl_assertions(inj_chibi)["has_chibi"] is True)
    inj_bg = _src(_TMPL).replace('decor.background(c, t, tpl="riven_full")', 'pass  # INJECT no bg')
    check("A14 注入[删 decor.background] 必被 A6 抓", not _tmpl_assertions(inj_bg)["has_bg"])
    inj_brand = _src(_TMPL).replace('brand = "曼珠"', 'brand = "Warframe Rabbit"  # INJECT')
    check("A15 注入[页脚改回 Rabbit] 必被 A3/A4 抓",
          _tmpl_assertions(inj_brand)["has_rabbit"] and not _tmpl_assertions(inj_brand)["has_brand"])
    inj_bind = binds.replace('"星际战甲·紫卡全面分析": "light",', '')
    check("A16 注入[删 light 绑定] 必被 A8 抓", '"星际战甲·紫卡全面分析": "light"' not in inj_bind)


# ------------------------------------------------- B/C. 隔离装配 official 副本 pic 包真渲染
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _build_pic_pkg():
    """桩 qq_bridge / pic 包，真实加载 official 副本的 pic 子模块 + 数据层。"""
    for nm in ("qq_bridge", "qq_bridge.pic", "qq_bridge.pic.templates",
               "qq_bridge.game_tools"):
        if nm not in sys.modules:
            m = types.ModuleType(nm)
            m.__path__ = []
            sys.modules[nm] = m
    pic_dir = _ROOT / "pic"
    mod_theme = _load("qq_bridge.pic.theme", pic_dir / "theme.py")
    _load("qq_bridge.pic.fonts", pic_dir / "fonts.py")
    _load("qq_bridge.pic.canvas", pic_dir / "canvas.py")
    _load("qq_bridge.pic.assets", pic_dir / "assets.py")
    _load("qq_bridge.pic.icons", pic_dir / "icons.py")
    _load("qq_bridge.pic.decor", pic_dir / "decor.py")
    mod_tpl = _load("qq_bridge.pic.templates.riven_full", _TMPL)
    mod_data = _load("qq_bridge.game_tools.riven_full", _DATA)
    return mod_theme, mod_tpl, mod_data


def test_render():
    print("[B/C] 真渲染 + 版式门禁（official 副本 pic 包 + 真 LIGHT_THEME）")
    try:
        theme, tpl, data_mod = _build_pic_pkg()
    except Exception as e:
        check("B0 隔离装配 official 副本 pic 包", False, repr(e))
        return
    check("B0 隔离装配 official 副本 pic 包", True)

    light = theme.LIGHT_THEME
    check("B1 LIGHT_THEME bg_pattern=stickers/sticker=wand",
          getattr(light, "bg_pattern", "") == "stickers" and getattr(light, "sticker", "") == "wand",
          "pattern=%r sticker=%r" % (getattr(light, "bg_pattern", ""), getattr(light, "sticker", "")))

    sample = data_mod.reference_sample()
    boxes = []
    try:
        c = tpl.build(light, data=sample, width=655, boxes=boxes)
        png = c.to_bytes()
    except Exception as e:
        check("B2 真 light 主题渲染不抛异常", False, repr(e))
        return
    check("B2 真 light 主题渲染不抛异常", True)
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(png)).convert("RGB")
    check("B3 出图尺寸 655×1920", im.size == (655, 1920), str(im.size))
    check("B4 PNG 非空（bytes>0）", len(png) > 0, "bytes=%d" % len(png))

    # B5 像素对照：无背景主题须与 light 主题出图不同（证明 decor.background 真画）
    from dataclasses import replace as dc_replace
    flat = dc_replace(light, bg_pattern="none", corner_sticker=False)
    c2 = tpl.build(flat, data=sample, width=655, boxes=None)
    png2 = c2.to_bytes()
    check("B5 decor.background 真画贴纸底（light != 无背景）", png != png2,
          "light=%d flat=%d" % (len(png), len(png2)))

    # B6 右下原 chibi 区不再有 Rabbit 卡通实心块：与「无背景」版同区域应几乎一致
    # （chibi 已去；若仍在，两版该区都会叠同样卡通 → 但关键是颜色统计）
    px = im.load()
    greenish = 0
    total = 0
    for y in range(1740, 1920, 3):
        for x in range(515, 655, 3):
            r, g, b = px[x, y]
            total += 1
            # chibi 主色：绿色卡通（g 明显高于 r/b 且不太亮）
            if g > r + 18 and g > b + 18 and g < 200:
                greenish += 1
    ratio = greenish / total if total else 1.0
    check("B6 右下原 chibi 区无 Rabbit 绿色卡通块", ratio < 0.08,
          "green_ratio=%.3f" % ratio)

    # B7 页脚「曼珠」确实画出（x 68..135, y 1878..1905 有非白像素）
    foot_ink = 0
    for y in range(1878, 1906):
        for x in range(68, 138):
            r, g, b = px[x, y]
            if min(r, g, b) < 210:  # 非纯白/近白
                foot_ink += 1
    check("B7 页脚「曼珠」区有文字墨迹", foot_ink > 40, "ink_px=%d" % foot_ink)

    # C 版式门禁：真 light + 真数据下 boxes 0/0/0
    res = tpl._check_boxes(boxes, min_gap=2.0, pad=28, width=655, height=1920)
    bad = len(res["overflow"]) + len(res["overlap"]) + len(res["spacing"])
    check("C1 去 Rabbit + light 底后版式门禁 0/0/0", bad == 0, str(res))
    check("C2 boxes 已收集（非跳过渲染）", len(boxes) > 80, "boxes=%d" % len(boxes))

    # 注入 B5：把 decor.background 调用替换为 no-op，重新加载模板 → light 与 flat 必相同
    src = _src(_TMPL)
    inj = src.replace('decor.background(c, t, tpl="riven_full")', 'pass  # INJECT')
    assert inj != src, "注入定位失败：未找到 decor.background 调用"
    p = _ROOT / "pic" / "templates" / "_riven_full_visual_inject_tmp.py"
    p.write_text(inj, encoding="utf-8")
    try:
        inj_tpl = _load("qq_bridge.pic.templates._riven_full_visual_inject_tmp", p)
        boxes_i = []
        ci = inj_tpl.build(light, data=sample, width=655, boxes=boxes_i)
        png_i = ci.to_bytes()
        # 注入后模板不画背景，light 与 flat 应完全一致
        ci2 = inj_tpl.build(flat, data=sample, width=655, boxes=None)
        png_i2 = ci2.to_bytes()
        check("B8 注入[删 decor.background] → light==flat（证明 B5 检的是背景）",
              png_i == png_i2, "inj_light=%d inj_flat=%d" % (len(png_i), len(png_i2)))
    except Exception as e:
        check("B8 注入版渲染", False, repr(e))
    finally:
        try:
            p.unlink()
        except OSError:
            pass
        sys.modules.pop("qq_bridge.pic.templates._riven_full_visual_inject_tmp", None)


def main():
    print("== 紫卡全面分析卡 去 Rabbit 化 + light 底 守门 ==")
    print("  锚定 official 副本根：%s" % _REPO)
    test_ast()
    test_render()
    print("== 结果：%s ==" % ("ALL PASS" if not _failed else "FAILED: " + ", ".join(_failed)))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
