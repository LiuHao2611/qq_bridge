# -*- coding: utf-8 -*-
"""tools/check_doupo_typography.py —— 斗破卡片「真粗体 + 菜单排版」交付闸门。

用户 2026-11-15：「优化一下菜单，还有所有斗破卡片，这字体加粗不太行，看着模糊，
加粗的统一换个字体 @skill:card-design」。本闸门把这次踩过的三个坑钉死：

1. **伪粗描边糊字**：容器没有 Windows 字体，``bold`` 退到 vendored ``msyh.ttc``
   （两个 face 都是 Regular），画布层用同色细描边假装加粗；后来即使拿到真粗体，
   又再叠一层描边 → 双影 + 笔画糊。修法：项目内置真粗体 + ``_is_real_bold`` 守卫。
2. **换字体不生效**：``data/pic_cache`` 是 bind mount，重建容器不清；缓存 key 原本
   只含 template/theme/params，换字体后仍吐旧糊图。修法：key 纳入字体 size+mtime_ns。
3. **菜单口令被截断 / 列间距靠经验数**：旧版写死 ``CMD_COL=236`` +
   ``max_w=CMD_COL-26``，「上阵 技1 技2 技3 技4」被砍成「上阵 技1 技2 技3…」。
   修法：``doupo_menu.layout()`` 单一布局入口 + 实测最长口令推列宽 + 折行保字。

``check_boxes`` 是 card-design skill ``scripts/pillow_layout.py`` 的同款算法（溢出 /
重叠 / 最小间距），在项目内自带一份，保证**容器内也能跑闸门**，不依赖用户机器上
的 skill 路径。

用法：
    python tools/check_doupo_typography.py            # 报告，不中断
    python tools/check_doupo_typography.py --check    # 违规 exit 1（交付前必跑）
    python tools/check_doupo_typography.py --selftest # 反例注入：每个旧错都必须被拦
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins" / "qq_bridge"))

from PIL import ImageFont  # noqa: E402
from pic import fonts  # noqa: E402
from pic.canvas import Canvas, emoji_safe  # noqa: E402
from pic.templates import doupo_menu as M  # noqa: E402

FONTS = ROOT / "src/plugins/qq_bridge/pic/fonts.py"
CANVAS = ROOT / "src/plugins/qq_bridge/pic/canvas.py"
PIC_INIT = ROOT / "src/plugins/qq_bridge/pic/__init__.py"
MENU = ROOT / "src/plugins/qq_bridge/pic/templates/doupo_menu.py"
LIST = ROOT / "src/plugins/qq_bridge/pic/templates/doupo_list.py"
CARD = ROOT / "src/plugins/doupo/card.py"
COMMANDS = ROOT / "src/plugins/doupo/commands.py"

WIDTHS = (560, 720, 900)
MIN_GAP = 8


# --------------------------------------------------------------- check_boxes ----
def _overlap_2d(a, b) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """card-design skill 同款自检：溢出 / 重叠 / 同行最小间距。空列表 = 通过。"""
    bx0, by0, bx1, by1 = bounds
    problems: list[str] = []
    items = list(boxes)
    for label, x0, y0, x1, y1 in items:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append(f"[溢出] {label} 越界: bbox=({x0},{y0},{x1},{y1}) bounds={bounds}")
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            la, a = items[i][0], items[i][1:]
            lb, b = items[j][0], items[j][1:]
            if _overlap_2d(a, b):
                problems.append(f"[重叠] {la} <-> {lb}: {a} vs {b}")
            if a[1] < b[3] and b[1] < a[3]:
                gap_x = b[0] - a[2] if a[0] <= b[0] else a[0] - b[2]
                if 0 <= gap_x < min_gap:
                    problems.append(f"[间距] {la} <-> {lb} 水平间距 {gap_x}px < {min_gap}px")
    return problems


# ------------------------------------------------------------------ 真实数据 ----
def menu_data() -> dict:
    """菜单唯一源 = commands.MENU_GROUPS；用 ast 取值，不 import nonebot 插件层。"""
    src = COMMANDS.read_text(encoding="utf-8")
    groups = None
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "MENU_GROUPS":
            groups = ast.literal_eval(node.value)
            break
    if groups is None:
        raise SystemExit("[数据] commands.py 里找不到 MENU_GROUPS")
    total = sum(len(rows) for _, rows in groups)
    return {
        "title": "斗破菜单",
        "groups": [{"name": name, "rows": [list(r) for r in rows]} for name, rows in groups],
        "tip": f"共 {total} 条口令 · 纯中文，菜单是唯一切换口",
    }


# ---------------------------------------------------------------- 字体链路 ----
def font_chain_problems() -> list[str]:
    out: list[str] = []
    bp = fonts.font_path("bold")
    if not bp or not Path(bp).is_file():
        out.append(f"[字体] bold 字体文件缺失: {bp!r}")
    else:
        f = ImageFont.truetype(str(bp), 24)
        style = (f.getname()[1] or "").lower()
        if not any(k in style for k in ("bold", "black", "heavy", "semibold", "粗")):
            out.append(f"[字体] bold 不是真粗体 face: {f.getname()}")
        if Path(bp).name != "doupo_bold.ttf":
            out.append(f"[字体] bold 未走项目内置真粗体 doupo_bold.ttf: {bp}")
        if Path(bp).stat().st_size < 100_000:
            out.append("[字体] 内置粗体小于 100KB，疑似子集缺失或占位文件")
    out += source_problems(
        MENU.read_text(encoding="utf-8"),
        CANVAS.read_text(encoding="utf-8"),
        PIC_INIT.read_text(encoding="utf-8"),
        FONTS.read_text(encoding="utf-8"),
    )
    out += list_note_problems(
        CARD.read_text(encoding="utf-8"),
        LIST.read_text(encoding="utf-8"),
    )
    return out


def list_note_problems(d_src: str, l_src: str) -> list[str]:
    """异火卡「下级需 N 药渣」必须独立成 note 行，不许全角空格塞进 sub。"""
    out: list[str] = []
    if "　下级需" in d_src:
        out.append("[列表] 异火副行用全角空格凑间距（card-design 红线 5，长描述折行会连读）")
    if '"note": note' not in d_src:
        out.append("[列表] build_flames_data 未把升级需求放进独立 note 字段")
    if "NOTE_GAP" not in l_src or 'item.get("note")' not in l_src:
        out.append("[列表] doupo_list 未实现 note 行 + 显式 gap（高度与绘制会漂移）")
    return out


def _has_cmd_col_truncation(m_src: str) -> bool:
    """AST 判定「口令按写死列宽截断」是否真的出现在代码里。

    不能简单 grep ``max_w=CMD_COL``：菜单 docstring 里会引用旧写法当反例说明，
    字符串匹配会误报。只看真实 keyword 实参才拦得住代码、放得过注释。
    """
    try:
        tree = ast.parse(m_src)
    except SyntaxError:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "max_w":
            if any(isinstance(n, ast.Name) and n.id == "CMD_COL"
                   for n in ast.walk(node.value)):
                return True
    return False


def source_problems(m_src: str, c_src: str, p_src: str, f_src: str) -> list[str]:
    """源码契约：三个修复点不许被「顺手改回去」。"""
    out: list[str] = []
    if "not _is_real_bold(font)" not in c_src or "def _is_real_bold" not in c_src:
        out.append("[画布] 真粗体不叠伪粗描边的守卫丢了（会回到双影糊字）")
    if "_MIN_BOLD_FS" not in c_src or "size >= _MIN_BOLD_FS" not in c_src:
        out.append("[画布] <22px 小字禁止粗体字形的护栏丢了（小字会被真粗体糊化）")
    if '"|f=" + fver' not in p_src or "st_mtime_ns" not in p_src:
        out.append("[缓存] 缓存 key 未纳入字体 size+mtime（换字体仍会吐旧糊图）")
    if '"|v=" + _code_sig()' not in p_src or "def _code_sig" not in p_src:
        out.append("[缓存] 缓存 key 未纳入渲染代码签名（改模板版式仍会吐旧图）")
    if "_BUNDLED_BOLD" not in f_src or 'role == "bold" and _BUNDLED_BOLD.is_file()' not in f_src:
        out.append("[字体] font_path 未优先返回项目内置真粗体")
    if _has_cmd_col_truncation(m_src):
        out.append("[菜单] 口令列又出现按写死列宽截断（会砍掉长口令）")
    if "def layout(" not in m_src or "def layout_boxes(" not in m_src:
        out.append("[菜单] 单一布局入口 layout()/layout_boxes() 被拆掉（高度会与渲染漂移）")
    return out


# ---------------------------------------------------------------- 菜单排版 ----
def menu_layout_problems() -> list[str]:
    out: list[str] = []
    if M.COL_GAP < 16:
        out.append(f"[网格] COL_GAP={M.COL_GAP} <16px：口令列与说明列会贴在一起")
    if M.ROW_GAP < 4:
        out.append(f"[网格] ROW_GAP={M.ROW_GAP} <4px：行与行没有呼吸区")
    if M.CMD_LH < M.FS_CMD + 4 or M.DESC_LH < M.FS_DESC + 4:
        out.append("[网格] 行高低于字号的 1.2 倍：折行会挤成一团")

    data = menu_data()
    for w in WIDTHS:
        L = M.layout(data, w)
        boxes = M.layout_boxes(L)
        bounds = (M.PAD, M.PAD, w - M.PAD, L["h"] - M.PAD)
        out += [f"[w={w}] {p}" for p in check_boxes(bounds, boxes, min_gap=MIN_GAP)]
        if L["h"] < 360 or L["h"] > 4000:
            out.append(f"[w={w}] 卡片高度异常 {L['h']}")
        for g in data["groups"]:
            for row in g["rows"]:
                part = M._row_parts(row, L["content_w"], L["cmd_col"])
                if "".join(part["cmd"]) != emoji_safe(row[0]):
                    out.append(f"[w={w}] 口令被截断/丢字: {row[0]!r} -> {part['cmd']!r}")
                if "".join(part["desc"]) != emoji_safe(row[1]):
                    out.append(f"[w={w}] 说明被截断/丢字: {row[1][:18]!r}")
                if any("…" in ln for ln in part["cmd"] + part["desc"]):
                    out.append(f"[w={w}] 出现省略号（截断回归）: {row[0]!r}")
    return out


# ------------------------------------------------------------------ 反例注入 ----
def selftest() -> list[tuple[str, bool]]:
    """把这次真实犯过的错放回去，检查必须报错；全 True 才算闸门有效。"""
    m_src = MENU.read_text(encoding="utf-8")
    c_src = CANVAS.read_text(encoding="utf-8")
    p_src = PIC_INIT.read_text(encoding="utf-8")
    f_src = FONTS.read_text(encoding="utf-8")
    d_src = CARD.read_text(encoding="utf-8")
    l_src = LIST.read_text(encoding="utf-8")
    res: list[tuple[str, bool]] = []

    res.append(("撤掉真粗体不伪粗守卫", bool(source_problems(
        m_src, c_src.replace("and not _is_real_bold(font)", ""), p_src, f_src))))
    res.append(("撤掉小字粗体护栏", bool(source_problems(
        m_src, c_src.replace("size >= _MIN_BOLD_FS", "True"), p_src, f_src))))
    res.append(("撤掉缓存字体签名", bool(source_problems(
        m_src, c_src, p_src.replace('"|f=" + fver', '"|" + fver'), f_src))))
    res.append(("撤掉渲染代码签名", bool(source_problems(
        m_src, c_src, p_src.replace('"|v=" + _code_sig()', '""'), f_src))))
    res.append(("font_path 不再优先内置粗体", bool(source_problems(
        m_src, c_src, p_src,
        f_src.replace('if role == "bold" and _BUNDLED_BOLD.is_file():', 'if False:')))))
    res.append(("菜单恢复写死列宽截断", bool(source_problems(
        m_src + "\nS.key(c, (x0 + 14, cy), cmd, size=FS_CMD, max_w=CMD_COL - 26)\n",
        c_src, p_src, f_src))))

    res.append(("异火卡重新用全角空格凑间距", bool(list_note_problems(
        d_src.replace('note = f"下级需 {commands._fmt(need)} 药渣"',
                      'sub = sub + "　下级需 60 药渣"'), l_src))))
    res.append(("列表卡撤掉 note 行实现", bool(list_note_problems(
        d_src, l_src.replace("NOTE_GAP", "")))))

    saved = M.COL_GAP
    M.COL_GAP = 0
    res.append(("列间距归零", bool(menu_layout_problems())))
    M.COL_GAP = saved
    saved = M.ROW_GAP
    M.ROW_GAP = 0
    res.append(("行间距归零", bool(menu_layout_problems())))
    M.ROW_GAP = saved
    return res


def main() -> int:
    fc = font_chain_problems()
    ml = menu_layout_problems()
    st = selftest()
    problems = fc + ml
    for name, caught in st:
        if not caught:
            problems.append(f"[selftest] 反例没被拦住: {name}")
    print("字体链路 + 菜单排版：", "PASS" if not problems else f"FAIL（{len(problems)} 项）")
    print("菜单盒自检 widths=%s → %s" % (
        WIDTHS,
        "溢出 0 / 重叠 0 / 间距 0" if not ml else f"{len(ml)} 项违规（见明细）"))
    print("反例注入：%d/%d 被拦" % (sum(1 for _, ok in st if ok), len(st)))
    for name, ok in st:
        print("  %s %s" % ("OK  " if ok else "MISS", name))
    for p in problems:
        print("  " + p)
    if "--selftest" in sys.argv:
        return 0 if all(ok for _, ok in st) else 1
    if "--check" in sys.argv and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
