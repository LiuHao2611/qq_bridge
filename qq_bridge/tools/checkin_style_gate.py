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
