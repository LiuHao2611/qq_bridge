# -*- coding: utf-8 -*-
"""宿主离线生成签到卡「黄金铸造图标 + 风格字体子集」资产（运行时不依赖本脚本依赖）。

一次运行，产出随代码交付的源码资产（唯一生成入口，可复现）：
  * ``pic/templates/assets/checkin_gold/<kind>.png`` —— 7 枚黄金浮雕图标（128x128 RGBA）
    来源 Font Awesome Free 6.5.0 Solid SVG（Icons CC BY 4.0）；下载 -> svgpathtools 解析 ->
    aggdraw 复合 Path（nonzero winding，正确挖孔/抗锯齿）-> Pillow 金色渐变 + 顶高光 +
    深铜描边 + 内阴影 + 柔和投影。运行时只读 PNG，不依赖 svgpathtools/aggdraw/cairo。
  * ``pic/templates/assets/checkin_fonts/<name>.ttf`` —— 4 款按「卡面固定文案字符」子集
    的字体（Google Fonts，SIL OFL 1.1）。动态昵称由 vendored 雅黑兜底（已随 COPY src
    进镜像），故固定字集即可，体积 <700KB / 4 款。
  * ``.tmp/checkin_assets_preview.png`` —— 肉眼核对预览（图标排 + 字体样例）。

设计取舍（真实数据，见 .tmp/probe_gold_feasibility.py / probe_font_subset.py）：
  * GB2312 全字子集体积 95%~99%（字体主字集本就是 GB2312），4 款全量约 20MB，
    绝不入库；只留卡面固定字符（从模板字符串字面量 ast 抽取）。
  * 风格字体缺 ``✓▶``（马/龙/志还缺 ``·``）→ 卡面这些符号改几何绘制 / 换字。
  * wand 正确 slug = ``wand-magic-sparkles``（``wand`` 是 404）。

用法:
    python tools/build_checkin_assets.py            # 生成/覆盖全部资产
    python tools/build_checkin_assets.py --check     # 交付闸门：只校验本地资产齐全且为真
"""
from __future__ import annotations

import argparse
import ast
import re
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
# 宿主离线「生成」专用依赖（aggdraw/svgpathtools/fontTools）改为惰性 import：
# 运行时与容器只消费已生成的 PNG/TTF，--check 只依赖 Pillow，不该被生成链依赖拖累
# （2026-11-10 容器 --check 报 ModuleNotFoundError: aggdraw 的真实回归）。

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates"
GOLD = TPL / "assets" / "checkin_gold"
FONTF = TPL / "assets" / "checkin_fonts"
CACHE = ROOT / ".tmp" / "gold_src"
FONTRAW = ROOT / ".tmp" / "fontprobe"
PREVIEW = ROOT / ".tmp" / "checkin_assets_preview.png"

SS = 256                       # 光栅化 / 镀金工作尺寸
OUT_PNG = 128                  # 入库图标尺寸（运行时最多画 30px，足够）
PAD = 26                       # 图标四周留白（投影/描边不裁切）

# kind -> FA 6.5.0 solid slug
ICONS = {
    "clock": "clock",                             # 顶部标题闹钟
    "bolt": "bolt",                               # 连签次数
    "check": "check",                             # 累计签到
    "coins": "coins",                             # 当前喵币（黄金金币，替换旧 diamond）
    "arrow-up": "arrow-up",                       # 距里程碑
    "star": "star",                               # 群内名次
    "wand-magic-sparkles": "wand-magic-sparkles",  # 奖励胶囊
}
FA_BASE = "https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.5.0/svgs/solid/%s.svg"

# 逻辑 role -> (原始字体文件名 stem, Google Fonts CSS2 family 查询串, 用途)
# display/section/heavy/body 里 heavy 与 body 同为 kuaile（分别生成便于将来独立换字体）
FONT_SPECS = {
    "display": ("mashan", "Ma+Shan+Zheng", "毛笔板书大字（签到）"),
    "section": ("longcang", "Long+Cang", "硬笔行楷分区标题"),
    "heavy": ("kuaile", "ZCOOL+KuaiLe", "圆润粗体数值/强调"),
    "body": ("kuaile", "ZCOOL+KuaiLe", "圆润正文字体"),
}
CSS2 = "https://fonts.googleapis.com/css2?family=%s"
# Chrome 58 / Win7 UA 让 CSS2 返回 ttf（默认返回 woff2）
FONT_UA = ("Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36")


# ---------------------------------------------------------------- 下载（带缓存）
def _fetch(url: str, cache: Path) -> bytes:
    if cache.is_file() and cache.stat().st_size > 0:
        return cache.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": FONT_UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if not data:
        raise SystemExit("[build] 下载为空：%s" % url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    return data


def _font_raw(stem: str, spec: str) -> bytes:
    """取原始 ttf：优先复用 .tmp/fontprobe 已下载文件；否则走 CSS2 直链下载。"""
    local = FONTRAW / (stem + ".ttf")
    if local.is_file() and local.stat().st_size > 0:
        return local.read_bytes()
    cache = CACHE / ("raw_" + stem + ".ttf")
    if cache.is_file() and cache.stat().st_size > 0:
        return cache.read_bytes()
    req = urllib.request.Request(CSS2 % spec, headers={"User-Agent": FONT_UA})
    try:
        css = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")
    except Exception as e:
        raise SystemExit("[build] Google Fonts CSS2 下载失败 family=%s：%s；"
                         "可手动把 ttf 放到 %s 后重跑" % (spec, e, local))
    m = re.findall(r"url\((https://[^)]+\.ttf)\)", css)
    if not m:
        raise SystemExit("[build] CSS2 未返回 ttf 直链 family=%s（UA 可能失效）；"
                         "可手动放 ttf 到 %s" % (spec, local))
    return _fetch(m[0], cache)


# ---------------------------------------------------------------- SVG -> 抗锯齿 mask
def rasterize(svg: str, size: int = SS, pad: int = PAD) -> Image.Image:
    """FA Solid SVG -> L 模式抗锯齿形状（复合 Path + nonzero winding，正确挖孔）。"""
    try:
        from aggdraw import Draw as ADraw, Brush, Path as APath  # noqa: PLC0415
        from svgpathtools import parse_path  # noqa: PLC0415
    except ImportError as e:
        raise SystemExit("[build] 生成资产需宿主安装 aggdraw/svgpathtools（--check 只读 PNG/TTF 不需）：%s" % e)
    ds = re.findall(r'<path[^>]*\bd="([^"]+)"', svg)
    if not ds:
        raise SystemExit("[build] SVG 无 <path d=...>")
    vb = re.search(r'viewBox="\s*-?[\d.]+\s+-?[\d.]+\s+([\d.]+)\s+([\d.]+)"', svg)
    vw, vh = (float(vb.group(1)), float(vb.group(2))) if vb else (512.0, 512.0)
    sc = (size - 2 * pad) / max(vw, vh)
    ox = (size - vw * sc) / 2
    oy = (size - vh * sc) / 2
    im = Image.new("L", (size, size), 0)
    ad = ADraw(im)
    comp = APath()
    for d in ds:
        for sub in parse_path(d).continuous_subpaths():
            segs = list(sub)
            pts: list[tuple[float, float]] = []
            for i, seg in enumerate(segs):
                ln = seg.length()
                if ln <= 0:
                    continue
                n = max(6, int(ln / 3))
                last = n + 1 if i == len(segs) - 1 else n
                for k in range(last):
                    q = seg.point(k / n)
                    pts.append((ox + q.real * sc, oy + q.imag * sc))
            if len(pts) >= 3:
                comp.moveto(*pts[0])
                for q in pts[1:]:
                    comp.lineto(*q)
                comp.lineto(*pts[0])
    ad.path(comp, None, Brush(255))
    ad.flush()
    return im


def _fit(mask: Image.Image, size: int = SS, pad: int = PAD) -> Image.Image:
    """裁透明边 -> 等比 contain 到 (size-2pad) 画布（按真实 AR，不拉伸）。"""
    bb = mask.getbbox()
    if not bb:
        raise SystemExit("[build] SVG 光栅化后形状为空（winding/采样问题）")
    shape = mask.crop(bb)
    inner = size - 2 * pad
    sc = min(inner / shape.width, inner / shape.height)
    shape = shape.resize((max(1, int(shape.width * sc)), max(1, int(shape.height * sc))),
                         Image.LANCZOS)
    out = Image.new("L", (size, size), 0)
    out.paste(shape, ((size - shape.width) // 2, (size - shape.height) // 2))
    return out


# ---------------------------------------------------------------- 黄金铸造合成
# 金色纵向色标：v=0（顶）亮香槟金 -> v=255（底）暗赤金
_GOLD_STOPS = ((0, (255, 246, 200)), (64, (245, 197, 66)),
               (150, (200, 134, 12)), (255, (138, 82, 8)))


def _gold_palette() -> list[tuple[int, int, int, int]]:
    pal = []
    for v in range(256):
        for i in range(len(_GOLD_STOPS) - 1):
            a, ca = _GOLD_STOPS[i]
            b, cb = _GOLD_STOPS[i + 1]
            if a <= v <= b:
                t = (v - a) / max(1, b - a)
                pal.append(tuple(int(ca[c] + (cb[c] - ca[c]) * t) for c in range(3)) + (255,))
                break
        else:
            pal.append(_GOLD_STOPS[-1][1] + (255,))
    return pal


def gild(mask: Image.Image) -> Image.Image:
    """白色 alpha 形状 -> 黄金浮雕：投影 + 深铜描边 + 金色渐变 + 顶高光 + 底内阴影。"""
    m = mask
    out = Image.new("RGBA", m.size, (0, 0, 0, 0))

    # 1) 柔和投影（右下偏移 + 模糊，深棕半透明）
    sh_a = m.filter(ImageFilter.GaussianBlur(5)).point(lambda v: int(v * 0.42))
    sh_layer = Image.new("L", m.size, 0)
    sh_layer.paste(sh_a, (5, 7))
    shadow = Image.new("RGBA", m.size, (40, 26, 6, 0))
    shadow.putalpha(sh_layer)
    out.alpha_composite(shadow)

    # 2) 深铜外描边：膨胀 mask 减原 mask 得到边环
    edge = ImageChops.subtract(m.filter(ImageFilter.MaxFilter(9)), m)
    border = Image.new("RGBA", m.size, (74, 46, 8, 255))
    border.putalpha(edge.point(lambda v: int(v * 0.95)))
    out.alpha_composite(border)

    # 3) 金色主体：Image.linear_gradient 顶 0(亮) -> 底 255(暗)，逐通道映射色标
    grad = Image.linear_gradient("L").resize(m.size)   # 顶 0、底 255
    pal = _gold_palette()
    r = grad.point(lambda v: pal[v][0])
    g = grad.point(lambda v: pal[v][1])
    b = grad.point(lambda v: pal[v][2])
    gold = Image.merge("RGB", (r, g, b)).convert("RGBA")
    gold.putalpha(m)
    out.alpha_composite(gold)

    # 4) 顶部高光：mask ∩ 上方椭圆，半透明白
    hl = Image.new("L", m.size, 0)
    ImageDraw.Draw(hl).ellipse((-m.size[0] * 0.1, -m.size[1] * 0.55,
                                m.size[0] * 1.1, m.size[1] * 0.48), fill=140)
    hl = ImageChops.multiply(m, hl).filter(ImageFilter.GaussianBlur(3))
    hi = Image.new("RGBA", m.size, (255, 255, 240, 0))
    hi.putalpha(hl)
    out.alpha_composite(hi)

    # 5) 底部内阴影：内边缘 ∩ 下半渐强，增强浮雕立体感
    inner_edge = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(5)))
    low = Image.linear_gradient("L").resize(m.size).point(
        lambda v: int(120 * max(0.0, (v / 255.0 - 0.45) / 0.55)))
    inner_edge = ImageChops.multiply(inner_edge, low)
    shade = Image.new("RGBA", m.size, (92, 52, 6, 0))
    shade.putalpha(inner_edge)
    out.alpha_composite(shade)
    return out


# ---------------------------------------------------------------- 固定文案字符集
#: 真正会出现在卡面的标点白名单。docstring / 报错文案里的 § ≥ ≤ < > = 等符号
#: 绝不能混进字集（曾因 ast 全量抽取把 `§31`/`≥200` 塞进子集，自检正确报错）。
CARD_PUNCT = set("～「」*+%#/:：，。；？！（）—…【】、·， ")
#: 风格字体确实没有的字形：卡面一律改几何绘制 / 纯文本，不写字形。
GLYPHLESS = set("✓▶✔❯★☆◆◇●○✦✧→⇒√§≥≤<>=")
#: 只有站酷快乐体(heavy/body)具备、马善政/龙藏(display/section)缺失的字形。
KUALE_ONLY = {"·"}


def fixed_charset() -> str:
    """卡面固定文案字符全集（heavy/body 用）：模板字符串字面量里、白名单内的标点
    + 全部汉字 + 数字 + 拉丁。注释/docstring 走的标点不在白名单，天然被过滤。"""
    raw: set[str] = set()
    for fn in ("checkin.py", "checkin_parts.py"):
        tree = ast.parse((TPL / fn).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                raw.update(node.value)
    chars = {c for c in raw if not c.isspace() and c not in GLYPHLESS
             and (c.isalnum() or c in CARD_PUNCT)}
    chars.update("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return "".join(sorted(chars))


def font_charset(name: str) -> str:
    """按字体角色定子集：display(马善政)/section(龙藏) 没有 `·`，剔除；
    heavy/body(站酷快乐体) 保留。运行时这两个角色也绝不画 `·`（横幅正文走 body）。"""
    chars = set(fixed_charset())
    if name in ("display", "section"):
        chars -= KUALE_ONLY
    return "".join(sorted(chars))


def _subset(raw: bytes, chars: str, out: Path) -> None:
    try:
        from fontTools import subset  # noqa: PLC0415
    except ImportError as e:
        raise SystemExit("[build] 生成字体需宿主安装 fontTools（--check 不需）：%s" % e)
    opts = subset.Options()
    font = subset.load_font(BytesIO(raw), opts)
    s = subset.Subsetter(options=opts)
    s.populate(text=chars)
    s.subset(font)
    with open(out, "wb") as f:
        subset.save_font(font, f, opts)


# ---------------------------------------------------------------- 校验
def check() -> int:
    errs: list[str] = []
    for kind in ICONS:
        p = GOLD / (kind + ".png")
        if not p.is_file():
            errs.append("缺图标 %s" % p)
            continue
        try:
            im = Image.open(p).convert("RGBA")
        except Exception as e:
            errs.append("图标损坏 %s: %s" % (p, e))
            continue
        if im.size != (OUT_PNG, OUT_PNG):
            errs.append("图标尺寸 %s 应为 %dx%d，实得 %s" % (kind, OUT_PNG, OUT_PNG, im.size))
        a = im.getchannel("A")
        n = sum(1 for v in a.tobytes() if v > 16)
        frac = n / (im.width * im.height)
        if not (0.04 < frac < 0.80):
            errs.append("图标 %s 非透明像素占比 %.1f%% 异常（疑似空/糊成饼）" % (kind, frac * 100))
    for name in FONT_SPECS:
        p = FONTF / (name + ".ttf")
        if not p.is_file():
            errs.append("缺字体 %s" % p)
            continue
        try:
            f = ImageFont.truetype(str(p), 20)
            probe = bytes(f.getmask("\u0378"))
            need = font_charset(name)
            miss = [c for c in set(need) if bytes(f.getmask(c)) == probe]
            if miss:
                errs.append("字体 %s 缺其角色字集的字 %s" % (name, "".join(miss)))
        except Exception as e:
            errs.append("字体损坏 %s: %s" % (p, e))
    if errs:
        print("[check] FAIL（%d）：" % len(errs))
        for e in errs[:50]:
            print("  " + e)
        return 1
    print("[check] PASS：%d 枚黄金图标（%dx%d RGBA，占比正常） + %d 款风格字体"
          "（按角色字集覆盖，零缺字；display/section 不含 ·）"
          % (len(ICONS), OUT_PNG, OUT_PNG, len(FONT_SPECS)))
    return 0


# ---------------------------------------------------------------- 生成
def build() -> int:
    GOLD.mkdir(parents=True, exist_ok=True)
    FONTF.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    chars = fixed_charset()
    print("[build] 卡面固定字符集 %d 字" % len(set(chars)))

    gold_imgs: list[tuple[str, Image.Image]] = []
    for kind, slug in ICONS.items():
        svg = _fetch(FA_BASE % slug, CACHE / (slug + ".svg")).decode("utf-8", "ignore")
        g = gild(_fit(rasterize(svg))).resize((OUT_PNG, OUT_PNG), Image.LANCZOS)
        p = GOLD / (kind + ".png")
        g.save(p, "PNG", optimize=True)
        gold_imgs.append((kind, g))
        print("  icon %-22s -> %s (%dB)" % (kind, p.name, p.stat().st_size))

    for name, (stem, spec, why) in FONT_SPECS.items():
        raw = _font_raw(stem, spec)
        out = FONTF / (name + ".ttf")
        need = font_charset(name)
        _subset(raw, need, out)
        print("  font %-9s %-22s -> %s (%dB / %d 字)"
              % (name, why, out.name, out.stat().st_size, len(set(need))))

    # 预览：上排图标 + 下排三款字体样例（高度须容得下字体，曾因 170px 把字体裁掉，肉眼核对抓到）
    PW = OUT_PNG * len(ICONS) + 40
    prev = Image.new("RGB", (PW, 330), (245, 247, 250))
    d = ImageDraw.Draw(prev)
    for i, (kind, g) in enumerate(gold_imgs):
        x = 20 + i * OUT_PNG
        prev.paste(g, (x, 10), g)
        d.text((x + 4, OUT_PNG + 16), kind, fill=(40, 50, 70))
    d.line((20, OUT_PNG + 40, PW - 20, OUT_PNG + 40), fill=(210, 216, 226), width=1)
    fdisp = ImageFont.truetype(str(FONTF / "display.ttf"), 40)
    fsec = ImageFont.truetype(str(FONTF / "section.ttf"), 26)
    fkua = ImageFont.truetype(str(FONTF / "heavy.ttf"), 28)
    ly = OUT_PNG + 52
    d.text((24, ly), "签到", font=fdisp, fill=(176, 118, 16))
    d.text((150, ly + 8), "毛笔板书 · 主标题 40px", fill=(90, 96, 110))
    ly += 52
    d.text((24, ly), "签到状态  里程碑 12/30  本月签到记录",
           font=fsec, fill=(40, 60, 90))
    ly += 42
    d.text((24, ly), "喵币 720 +237  ·  连签 12天  #03",
           font=fkua, fill=(30, 40, 60))
    PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    prev.save(PREVIEW, "PNG")
    print("[build] 预览 -> %s" % PREVIEW)
    return check()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="签到卡黄金图标 + 风格字体资产生成器")
    ap.add_argument("--check", action="store_true", help="只校验本地资产齐全且为真")
    args = ap.parse_args()
    raise SystemExit(check() if args.check else build())
