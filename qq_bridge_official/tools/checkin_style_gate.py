# -*- coding: utf-8 -*-
"""签到卡「视觉精修」语义门禁（2026-11-10 15:0x，按功能从 check_checkin_layout 拆出）。

守用户点名的三个维度，判据逻辑**只有这一份**（主门禁 import 调用，反例探针也在本模块）：
  * 字号层级 —— checkin_parts.type_scale_problems（本模块打包兜底）
  * 间距 token —— checkin_parts.spacing_token_problems（本模块打包兜底）
  * 分区标题同色同字重 —— section_style_problems（颜色 accent + 加粗 bold，同层统一）

从主门禁拆出的理由：主门禁 script 是存量 <20KB 文件，无棘轮豁免资格；本轮新增
section_style + 3 个反例探针后主脚本涨到 23KB 触 20KB 硬红线，故按项目「按功能拆分」
纪律独立成模块。本模块无业务判断、不 import 上级包之外的东西，纯判据 + 反例注入。
"""
from __future__ import annotations

import os
import re
import sys

# 模块自治：任意 cwd / 宿主 pytest 导入本模块时，都能找到 qq_bridge 包（与主门禁同口径）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /app
for _p in (_ROOT, os.path.join(_ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from qq_bridge.pic.templates import checkin_parts as _parts  # noqa: E402

SOURCE = os.path.join(_ROOT, "src", "plugins", "qq_bridge", "pic", "templates", "checkin.py")

_TITLES = ("签到状态", "本次签到奖励", "本月签到记录")


def section_style_problems(src: str | None = None) -> list[str]:
    """分区标题语义样式：4 个分区标题必须同层同色同字重 = accent + bold。

    不得出现奖励标题单独用 ok 绿、或某个标题去掉 bold 这类同层异样回退。
    ``src`` 可选：None=读当前源码；注入反例时传改造后的文本，判据只有这一份。
    ``role``/``color`` 常写在标题字符串续行，故按「括号平衡」合句后判定。"""
    if src is None:
        try:
            with open(SOURCE, encoding="utf-8") as f:
                src = f.read()
        except OSError as e:
            return ["[样式] 读 checkin.py 失败：%s" % e]
    p: list[str] = []
    lines = src.splitlines()

    def statement(start: int) -> str:
        buf = lines[start]
        depth = buf.count("(") - buf.count(")")
        j = start
        while depth > 0 and j + 1 < len(lines):
            j += 1
            buf += " " + lines[j]
            depth += lines[j].count("(") - lines[j].count(")")
        return buf

    for title in _TITLES:
        for i, ln in enumerate(lines):
            if ('"%s"' % title) in ln and "FS_SEC" in ln:
                stmt = statement(i)
                if "color=accent" not in stmt:
                    p.append("[样式] 分区标题 %r 非 accent 同层色：%s" % (title, stmt.strip()[:88]))
                if 'role="bold"' not in stmt:
                    p.append("[样式] 分区标题 %r 非 bold：%s" % (title, stmt.strip()[:88]))
                break
    for i, ln in enumerate(lines):
        if '"里程碑  %d/%d"' in ln and "FS_SEC" in ln:
            stmt = statement(i)
            if "color=accent" not in stmt or 'role="bold"' not in stmt:
                p.append("[样式] 里程碑分区标题非 accent/bold：%s" % stmt.strip()[:88])
            break
    return p


def visual_contract_problems() -> list[str]:
    """字号层级 + 间距 token + 0 进度孤立竖线三维度（前两者实现在 checkin_parts）。"""
    return (_parts.type_scale_problems() + _parts.spacing_token_problems()
            + zero_progress_line_problems())


def value_padding_problems(boxes) -> list[str]:
    """数值三面不贴边：右≥8px，上下各≥6px（防 24px bold 压底边，肉眼核对曾抓到）。

    boxes 结构 = (label, x0, y0, x1, y1)。父子嵌套里 .value 是 capsule 的子盒。"""
    p: list[str] = []
    for label, x0, y0, x1, y1 in boxes:
        if not label.startswith("status.pill.") or label.endswith(".value"):
            continue
        val = next((b for b in boxes if b[0] == label + ".value"), None)
        if val is None:
            p.append("[贴边] %s 缺 .value 包围盒登记" % label)
            continue
        if x1 - val[3] < 8:                     # 右留白
            p.append("[贴边] %s 数值距右边框仅 %dpx < 8px" % (label, x1 - val[3]))
        pad_t, pad_b = val[2] - y0, y1 - val[4]  # 上/下留白用 y0=val[2] / y1=val[4]
        if pad_t < 6 or pad_b < 6:
            p.append("[贴边] %s 数值垂直贴边：上 %dpx / 下 %dpx < 6px"
                     % (label, pad_t, pad_b))
    return p


# ---------------------------------------------------------------- 反例注入探针（selftest 用）
def scale_probe() -> bool:
    """核心数值压到与分区标题同级（差 0<4）须被拦，还原后须干净。"""
    orig = _parts.FS_PILL_VAL
    try:
        _parts.FS_PILL_VAL = _parts.FS_SEC
        bad = _parts.type_scale_problems()
        _parts.FS_PILL_VAL = orig
        good = _parts.type_scale_problems()
    finally:
        _parts.FS_PILL_VAL = orig
    return bool(bad) and not good


def spacing_probe() -> bool:
    """GAP=14（非 4 倍数）须被拦，还原后须干净。"""
    orig = _parts.GAP
    try:
        _parts.GAP = 14
        bad = _parts.spacing_token_problems()
        _parts.GAP = orig
        good = _parts.spacing_token_problems()
    finally:
        _parts.GAP = orig
    return bool(bad) and not good


def style_probe() -> bool:
    """注入奖励标题同层异色 + 去 bold 两处真实回退，判据须各自命中。"""
    try:
        with open(SOURCE, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return False
    if section_style_problems(src):
        return False                             # 当前源码本就不该有问题
    p1 = section_style_problems(src.replace(
        "本次签到奖励\", size=FS_SEC, color=accent",
        "本次签到奖励\", size=FS_SEC, color=ok"))
    pat = re.compile(r'("本月签到记录", size=FS_SEC, color=accent,.*?role=")bold(")',
                     re.DOTALL)
    injected, n = pat.subn(r"\1regular\2", src, count=1)
    p2 = section_style_problems(injected) if n else []
    return bool(p1) and bool(p2) and n == 1


# ---------------------------------------------------------------- 0 进度孤立竖线（肉眼核对抓到、机器补拦）
_ZERO_LINE_TOKEN = "c.line((px,"


def zero_progress_line_problems(src: str | None = None) -> list[str]:
    """里程碑「当前位置竖线」必须受进度守卫：total=0 不画，杜绝孤立蓝线悬在
    轨道最左外缘。2026-11-10 肉眼核对抓到该回归（机器初漏），故补此判据。

    守的是**语义**（竖线绘制须受一个引用进度量 total/frac/px 的条件守卫），
    不锁死字面写法——``if total > 0`` / ``if frac`` / ``if px > cx0`` 都放行；
    只拦「无条件画竖线」或 ``if True`` 这类让 0 进度也画的回退。"""
    if src is None:
        try:
            with open(SOURCE, encoding="utf-8") as f:
                src = f.read()
        except OSError as e:
            return ["[进度条] 读 checkin.py 失败：%s" % e]
    lines = src.splitlines()
    idx = next((i for i, ln in enumerate(lines) if _ZERO_LINE_TOKEN in ln), None)
    if idx is None:
        return ["[进度条] 找不到当前位置竖线 c.line((px,...)，契约缺失（0 进度守卫无从谈起）"]
    indent = len(lines[idx]) - len(lines[idx].lstrip())
    guard = ""
    for j in range(idx - 1, -1, -1):
        s = lines[j].strip()
        if not s or s.startswith("#"):
            continue
        if len(lines[j]) - len(lines[j].lstrip()) < indent:
            guard = s                      # 最近的、缩进更浅的上层控制语句
            break
    if not re.search(r"\bif\b.*\b(total|frac|px)\b.*:", guard):
        return ["[进度条] 当前位置竖线无进度守卫（0 进度会画孤立蓝线，曾肉眼抓到）：守卫=%r" % guard]
    return []


def zero_progress_probe() -> bool:
    """注入真实回退：把进度守卫换成 ``if True:``（0 进度也画竖线），判据须命中；
    还原后须干净。从来没报过错的检查等于没加。"""
    try:
        with open(SOURCE, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return False
    if zero_progress_line_problems(src):
        return False                         # 当前源码本就不该有问题
    injected = src.replace("if total > 0:", "if True:", 1)
    bad = zero_progress_line_problems(injected)
    return bool(bad) and not zero_progress_line_problems(src)


# ---------------------------------------------------------------- 里程碑图例 token 间距（肉眼核对抓到粘连，机器补拦）
LEGEND_TOKENS = ("mile.legend.0", "mile.legend.1", "mile.legend.2", "mile.legend.3")
LEGEND_MIN_GAP = 8          # 相邻图例 token 最小水平间距（实测恒 20px；<8 观感即连读）
LEGEND_PARENT = "mile.legend"
LEGEND_NEXT = "mile.legend.next"


def _legend_box(boxes, label):
    return next((b for b in boxes if b[0] == label), None)


def mile_legend_problems(boxes, has_next: bool | None = None) -> list[str]:
    """图例 4 枚 token 须各自登记真实盒 + 相邻水平间距 ≥8px + 不重叠 + 不越父盒 + 同行。

    2026-11-10 肉眼核对 zoom_mile.png 抓到四档图例挤成一行（+30 与后一个「已达成」
    连读），根因是旧版只登记整条 ``mile.legend`` 父盒、check_boxes 看不到 token 间水平
    间距。故模板逐 token 登记叶子盒，本判据量化「整条父盒查不出」的粘连。
    ``has_next`` 此轮夹具都有下一里程碑（PROF_ZERO 也是 (3,30,3)）；None=按 .next 盒
    是否在位自动判断，避免把「无下一档」误判为缺盒。
    """
    p: list[str] = []
    toks = [_legend_box(boxes, k) for k in LEGEND_TOKENS]
    absent = [LEGEND_TOKENS[i] for i, b in enumerate(toks) if b is None]
    if absent:
        p.append("[图例] 缺里程碑 token 包围盒：%s（整条父盒不算，须逐枚登记）" % absent)
        return p                              # 缺盒时后续两两比对无意义
    parent = _legend_box(boxes, LEGEND_PARENT)
    if parent is None:
        p.append("[图例] 缺 %s 父盒（无法校验越界）" % LEGEND_PARENT)
    # 同行：4 枚 token 的 y 中心须一致（同一图例行，防某枚被挤到下一行）
    yc = {(b[2] + b[4]) / 2 for b in toks}
    if len(yc) != 1:
        p.append("[图例] token 未在同行：y 中心=%s" % sorted(yc))
    ordered = sorted(toks, key=lambda b: b[1])   # 4 枚 token 按 x 排序
    nxt = _legend_box(boxes, LEGEND_NEXT)
    expect_next = (nxt is not None) if has_next is None else bool(has_next)
    if expect_next and nxt is None:
        p.append("[图例] 有下一里程碑时缺 %s 盒（右侧「还差」未登记）" % LEGEND_NEXT)
    chain = ordered + ([nxt] if nxt is not None else [])
    for a, b in zip(chain, chain[1:]):
        gap = b[1] - a[3]
        if gap < 0:
            p.append("[图例] %s <-> %s 重叠 %dpx" % (a[0], b[0], -gap))
        elif gap < LEGEND_MIN_GAP:
            p.append("[图例] %s <-> %s 水平间距 %dpx < %dpx（连读粘连）"
                     % (a[0], b[0], gap, LEGEND_MIN_GAP))
        if abs(((a[2] + a[4]) / 2) - ((b[2] + b[4]) / 2)) > 1:
            p.append("[图例] %s 与 %s y 中心不齐（换行/漂移）" % (a[0], b[0]))
    if parent is not None:
        for b in chain:
            if b[1] < parent[1] - 1 or b[3] > parent[3] + 1:
                p.append("[图例] %s 水平越出 %s 父盒" % (b[0], LEGEND_PARENT))
    return p


def mile_legend_probe() -> bool:
    """反例：把 token2 紧贴 token1（gap 0）须被拦；正常 20px 样本须干净。"""
    good = [
        (LEGEND_PARENT, 40, 518, 760, 546),
        ("mile.legend.0", 44, 525, 150, 539),
        ("mile.legend.1", 170, 525, 275, 539),
        ("mile.legend.2", 295, 525, 416, 539),
        ("mile.legend.3", 436, 525, 512, 539),
        (LEGEND_NEXT, 662, 525, 756, 539),
    ]
    if mile_legend_problems(good, has_next=True):
        return False                          # 正常样本不得误报
    tight = [list(b) for b in good]
    tight[2] = ("mile.legend.1", 150, 525, 255, 539)   # 紧贴 token0：gap=0
    bad = mile_legend_problems([tuple(b) for b in tight], has_next=True)
    missing = mile_legend_problems([b for b in good if b[0] != "mile.legend.3"],
                                   has_next=True)
    return any("间距" in x or "重叠" in x for x in bad) and any("缺里程碑 token" in x for x in missing)


# ---------------------------------------------------------------- 黄金图标/分层字体资产接线契约
ASSET_DIR = os.path.join(_ROOT, "src", "plugins", "qq_bridge", "pic", "templates",
                         "assets")
GOLD_DIR = os.path.join(ASSET_DIR, "checkin_gold")
FONTS_DIR = os.path.join(ASSET_DIR, "checkin_fonts")
GOLD_KINDS = ("clock", "bolt", "check", "coins", "arrow-up", "star",
              "wand-magic-sparkles")
FONT_ROLES = ("display", "section", "heavy", "body")


_PARTS = os.path.join(_ROOT, "src", "plugins", "qq_bridge", "pic", "templates",
                      "checkin_parts.py")
_WIRING = (
    ("_gold.gate(strict=True)", "缺真实黄金 PNG 未硬拦"),
    ("_cf.gate(strict=True)", "缺风格字体未硬拦"),
    ("_cf.wrap(Canvas", "分层字体绘制代理未接线"),
    ("_gold.draw(c,", "黄金图标未走真实 PNG 通道"),
)


def checkin_asset_contract_problems(src: str | None = None,
                                    psrc: str | None = None) -> list[str]:
    """真实资产/接线契约：7 枚黄金 PNG + 4 款字体 + 同源度量三处接线都在位。

    守「禁止几何兜底当交付」（AGENTS §18/§67）：PNG 缺一张、字体缺一款，或
    checkin.py 撤掉 gate/wrap、checkin_parts 撤掉 _cf 同源度量，全都必须被拦。
    只查**契约接线与文件存在**，PNG/TTF 内容真实性由 build_checkin_assets --check
    与三层 md5 部署闸门负责，避免重复实现图像处理。
    ``src``/``psrc`` 可注入改造后源码文本（反例探针用），None=读当前磁盘。
    """
    p: list[str] = []
    for k in GOLD_KINDS:
        f = os.path.join(GOLD_DIR, k + ".png")
        if not os.path.isfile(f) or os.path.getsize(f) < 1024:
            p.append("[资产] 黄金图标缺真实 PNG 或过小：%s" % f)
    for r in FONT_ROLES:
        f = os.path.join(FONTS_DIR, r + ".ttf")
        if not os.path.isfile(f) or os.path.getsize(f) < 1024:
            p.append("[资产] 分层字体缺失或过小：%s" % f)
    if src is None:
        try:
            with open(SOURCE, encoding="utf-8") as f:
                src = f.read()
        except OSError as e:
            return p + ["[资产] 读 checkin.py 失败：%s" % e]
    for tok, why in _WIRING:
        if tok not in src:
            p.append("[资产] checkin.py 缺接线 %r：%s" % (tok, why))
    if psrc is None:
        try:
            with open(_PARTS, encoding="utf-8") as f:
                psrc = f.read()
        except OSError as e:
            return p + ["[资产] 读 checkin_parts.py 失败：%s" % e]
    if "_cf.width(s, size, role)" not in psrc or "_cf.size(text, size, role)" not in psrc:
        p.append("[资产] checkin_parts._w/_tb 未走 checkin_font 同源度量（绘制度量分裂）")
    return p


def checkin_asset_probe() -> bool:
    """注入真实回退（不写磁盘）：撤 gate strict / 撤同源度量接线，判据须各自命中。"""
    try:
        with open(SOURCE, encoding="utf-8") as f:
            src = f.read()
        with open(_PARTS, encoding="utf-8") as f:
            psrc = f.read()
    except OSError:
        return False
    if checkin_asset_contract_problems(src, psrc):
        return False                          # 当前源码本就不该有问题
    bad_gate = checkin_asset_contract_problems(
        src.replace("_gold.gate(strict=True)", "_gold.gate(strict=False)", 1), psrc)
    bad_same = checkin_asset_contract_problems(
        src, psrc.replace("_cf.width(s, size, role)", "c.text_width(s, size, role)", 1))
    return (any("gate" in x for x in bad_gate)
            and any("同源度量" in x for x in bad_same))
