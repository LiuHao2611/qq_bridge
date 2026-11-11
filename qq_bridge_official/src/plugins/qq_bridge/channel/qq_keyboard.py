# -*- coding: utf-8 -*-
"""QQ 官方通道「消息按钮键盘」生成器（仅官方副本，2026-11-11 全部重做）。

## 为什么全部重做（第二次返工）

上一版手工维护 ``_GROUPS``(130+ 键) + ``_ALIASES``，测试又从同一批词表抽词去
验这张手工表 —— 「自己考自己」。结果：
  * 编造别名 ``fhelp`` / ``w M`` 混进按钮 data，源码里根本不存在，点了静默；
  * 错误映射 ``"终幕": "信条"``，与 _handle:1960/1963 两个独立分支冲突；
  * 15 个只在分发函数体出现、词表/HELP 都没登记的真实口令 100% 漏检。

## 本版设计：零手工口令/别名表，唯一事实源来自源码 AST

按钮依据全部由 :mod:`command_inventory` 从 game_tools 分发源码 AST 派生：
  * ``inventory.commands``  —— 160 个真实出卡口令（按钮 data 的合法全集）
  * ``inventory.families``  —— 69 个别名族（每个 ``if cmd in (...)`` 最小分支）
  * ``inventory.domain_of`` —— 每个口令的出卡域（同分支的 _send_*/warframe.* 调用）

``buttons_for(P)`` 规则（机械、无人工分类，天然不遗漏不编造）：
  1. primary = P 自己（点了回到自己 → 终幕≠信条天然成立，无需任何别名映射）；
  2. 邻居 = 同 family 别名（取 canonical）+ 同 domain 其它 family 的 canonical，
     补足到 3-4 个，全部是真实出卡口令；
  3. 末位 = 主菜单「帮助」（真实口令）；
  4. P 不在 inventory.commands（主流程斜杠 / AI 闲聊 / 空）→ GENERIC 通用入口，
     data 仍全部取自 inventory.commands。

**硬不变量**：任何按钮的 ``cmd`` 都 ∈ ``inventory.commands``。这条由构造方式
保证（primary 来自归一化后校验通过的口令，邻居来自 family/domain，GENERIC 是
写死的真实口令且测试会校验它们都在全集里），并由 tests/test_qq_keyboard.py
的「零编造」守门反向设防（注入一个假 data 立即 FAIL）。

腾讯平台约束：action.type=1 回调、permission.type=2 所有人可点、label≤10、
每行≤5、行数≤5；keyboard 走 msg_type=2，作为图片卡片之后追加的独立消息。
"""
from __future__ import annotations

from dataclasses import dataclass

try:  # 正常包内 import
    from .command_inventory import get_inventory
except ImportError:  # 单文件直载（离线单测 `_load()`）时，同目录兜底
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from command_inventory import get_inventory  # type: ignore[no-redef]


@dataclass(frozen=True)
class Btn:
    label: str        # 按钮显示文字（≤10 字符，平台限制）
    cmd: str          # 点击后触发的口令（回调 button_data），必须 ∈ inventory.commands
    primary: bool = False   # 主按钮蓝底(style=1)，其余灰线框(style=0)


# 主菜单按钮：「帮助」是 game_tools 真实一级口令（dispatch:2078），点了出工具箱卡。
_MENU_CMD = "帮助"
_MENU = Btn("主菜单", _MENU_CMD)

# GENERIC：主流程斜杠命令 / AI 闲聊 / 空口令的通用入口。每项都必须真实出卡，
# 测试会断言 GENERIC 里每个 cmd ∈ inventory.commands（防编造）。
_GENERIC_CMDS = ("赏金", "裂隙", "市价", "抽签")

# 展示优先级：同 domain 邻居里，优先挑这些「高频入口」，让按钮更实用；
# 它们全部是真实口令，仅影响排序，不影响「是否覆盖/是否编造」。
_POPULAR = (
    "帮助", "赏金", "裂隙", "市价", "紫卡", "仲裁", "突击", "入侵", "警报",
    "抽签", "黄历", "wiki", "战甲", "配队", "计划", "订阅列表", "签到",
)


def normalize_cmd(text: str) -> str:
    """归一化到口令首词：去 #/＃// 前缀、去空白、取首词、ascii 转小写。

    只用于「选哪组按钮」，不改业务分发。例 ``#赏金 钢铁``→``赏金``、
    ``紫卡趋势 绝路``→``紫卡趋势``、``/help``→``help``（后者不命中 inventory
    → GENERIC，符合边界）。
    """
    t = (text or "").strip()
    while t and t[0] in "#＃/":
        t = t[1:].strip()
    if not t:
        return ""
    parts = t.split()
    head = parts[0] if parts else t
    return head.lower() if head.isascii() else head


def _is_real(cmd: str) -> bool:
    return bool(cmd) and cmd in get_inventory().commands


def _display(cmd: str) -> str:
    """按钮文案：用口令本身（所见即所得，点击=发送该口令）；过长截断。"""
    return cmd[:10]


def buttons_for(text: str) -> tuple[Btn, ...]:
    """按当前回复所属口令取按钮组；绝不返回空，任何输入都有按钮。

    派生逻辑见模块 docstring。返回元组第一个恒为 primary（当前口令自己，或
    GENERIC 的第一个入口），末位恒为主菜单。所有 ``Btn.cmd`` ∈ inventory.commands。
    """
    inv = get_inventory()
    head = normalize_cmd(text)
    # 精确命中优先；英文大小写不敏感兜底（如 WF/wf、WM/wm）
    cmd = head if _is_real(head) else next(
        (c for c in inv.commands if c.lower() == head.lower()), ""
    ) if head else ""

    # 主流程斜杠 / 闲聊 / 空 → GENERIC（data 全部是真实口令）
    if not cmd:
        return tuple(
            Btn(_display(c), c, primary=(i == 0))
            for i, c in enumerate(_GENERIC_CMDS)
        ) + (_MENU,)

    inv_cmd = next((c for c in inv.commands if c.lower() == cmd.lower()), cmd)
    primary = Btn(_display(inv_cmd), inv_cmd, True)

    # 候选邻居：同 family 别名（归一到 canonical）+ 同 domain 其它 family canonical
    family_of = inv.family_of
    canon = family_of.get(inv_cmd, inv_cmd)
    own = next((f for f in inv.families if f.canonical == canon), None)
    # 用专属 family.domain（如 _send_tenet），不用 domain_of（可能被 GUIDE_TITLES
    # broad 兜底污染成 _send_guide，导致「信条」错配到养成/副本/功能）。
    dom = own.domain if own else ""
    candidates: list[str] = []
    for fam in inv.families:
        if fam.canonical == canon or inv_cmd in fam.aliases:
            continue
        if fam.canonical in candidates:
            continue
        same_family = inv_cmd in fam.aliases
        same_domain = bool(dom) and fam.domain == dom and dom not in ("guide_fallback", "")
        if same_family or same_domain:
            candidates.append(fam.canonical)

    # 排序： Popular 优先 → 短词优先，保证同输入结果稳定（纯函数、并发安全）
    candidates.sort(key=lambda c: (
        0 if c in _POPULAR else 1,
        len(c), c,
    ))
    # 补足：若同族/同域不足 3 个，用全站高频真实口令补齐（仍 ∈ commands）
    for c in _POPULAR:
        if len(candidates) >= 3:
            break
        if c != inv_cmd and _is_real(c) and c not in candidates and c != _MENU_CMD:
            candidates.append(c)

    neighbors = tuple(
        Btn(_display(c), c, False) for c in candidates[:3]
    )
    return (primary,) + neighbors + (_MENU,)


def make_keyboard(text: str):
    """根据当前口令文本构造 ``MessageKeyboard``（供 official.py::send 调用）。

    延迟 import nonebot：本模块可在无依赖环境单测按钮表（command_inventory 纯
    标准库）。返回 ``(model, btns)``；btns 供调用方记日志（取 label）。
    纯函数——只依赖入参 + 缓存的 inventory，无全局可变态。
    """
    from nonebot.adapters.qq.models.common import (  # noqa: PLC0415
        Action, Button, InlineKeyboard, InlineKeyboardRow, MessageKeyboard,
        Permission, RenderData,
    )

    btns = buttons_for(text)
    row_buttons = []
    for b in btns[:5]:  # 平台每行≤5；当前最多 1+3+1=5，正好一行
        row_buttons.append(Button(
            id="qb_%s" % (b.cmd or "menu"),
            render_data=RenderData(
                label=b.label[:10],
                visited_label=b.label[:10],
                style=1 if b.primary else 0,
            ),
            action=Action(
                type=1,
                permission=Permission(type=2),
                data=b.cmd,
                reply=False,
                enter=False,
            ),
        ))
    kb = MessageKeyboard(
        content=InlineKeyboard(rows=[InlineKeyboardRow(buttons=row_buttons)])
    )
    return kb, btns
