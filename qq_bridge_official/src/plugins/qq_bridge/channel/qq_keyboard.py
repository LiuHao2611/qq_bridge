# -*- coding: utf-8 -*-
"""QQ 官方通道「消息按钮键盘」生成器（仅官方副本，2026-11-11 第三次返工）。

## 为什么第三次返工（用户「每张卡只挂对应功能按钮」）

第二次返工做到「零编造 + 全量覆盖」，但 ``buttons_for`` 用 family/domain 挑
邻居、同族不足 3 个就用 ``_POPULAR`` 全局高频词补齐 → 真实群截图里**每张卡都
长成同一组**「赏金/钢路/钢铁/裂隙/主菜单」，是全局导航而非这张卡自己的下钻。
用户明确：「裂隙卡下面有钢铁裂隙/普通裂隙，市价卡下面有满级/0级，举一反三，
每个功能每个卡片都加上对应功能按钮」。

## 本版设计：卡片级档位 + 市价带参 rank，两条 AST 事实源，零手工

* :mod:`command_inventory` —— 160 个真实出卡口令（button data 零编造全集基准）；
* :mod:`card_buttons` —— 从 ``_handle`` 派生的「出卡函数 → 该卡所有档位」+
  市价口令集 + ``_parse_mod_rank`` 的 rank token 白名单（满级/R0）。

``buttons_for(text)`` 规则：
  1. **档位族卡**（如裂隙/赏金/信条/终幕/紫卡趋势…）：按钮 = 该卡的全部档位
     canonical（裂隙卡→裂隙|普通裂隙|钢铁裂隙；赏金卡→赏金|钢铁赏金），primary
     恒为当前口令自己。绝不再用 _POPULAR 塞无关全局词。
  2. **市价卡带物品名**（如 ``wm 绝路``）：primary=当前完整口令 + 派生两个带参
     rank 按钮 ``wm 绝路 满级`` / ``wm 绝路 R0``（data 携带物品名，点了才出对应
     卡）+ 主菜单。rank token 只从 ``_parse_mod_rank`` 真实 return 字面量取。
  3. **单档卡 / 兜底卡**（警报/仲裁表/资料卡…）：按钮 = 自己 + 主菜单（无邻居可
     下钻时不硬凑）。
  4. **非口令输入**（斜杠/闲聊/空）→ GENERIC 通用入口，data 全部 ∈ commands。

**硬不变量**：任何按钮 data 必须满足二者之一——
  (a) ∈ ``inventory.commands``（真实零参口令）；或
  (b) 是合法带参串 ``<市价口令> <物品> <rank>``：首词 ∈ card_buttons.market_cmds、
      末词 ∈ card_buttons.rank_tokens、中间物品非空、且首词 ∈ commands。
由构造方式保证，并由 tests/test_qq_card_buttons.py 的「零编造 + 跨卡错位」守门
反向设防（注入假 rank / 把赏金按钮塞裂隙卡 立即 FAIL）。

腾讯平台约束：action.type=1 回调、permission.type=2 所有人可点、label≤10、
每行≤5、行数≤5；keyboard 走 msg_type=2，作为图片卡片之后追加的独立消息。
"""
from __future__ import annotations

from dataclasses import dataclass

try:  # 正常包内 import
    from .command_inventory import get_inventory
    from .card_buttons import get_card_inventory
except ImportError:  # 单文件直载（离线单测 `_load()`）时，同目录兜底
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from command_inventory import get_inventory  # type: ignore[no-redef]
    from card_buttons import get_card_inventory  # type: ignore[no-redef]


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


def _rank_label(token: str) -> str:
    """rank token → 展示文字。满级→满级、R0→0级（用户口径），其余原样。"""
    return {"满级": "满级", "R0": "0级"}.get(token, token[:10])


def _rank_button_data(head: str, rest: str) -> tuple[list[str], list[str]]:
    """市价卡带物品名时派生带参 rank 按钮的 (data 列表, label 列表)。

    ``head`` 是市价口令（如 ``wm``/``市价``，已归一到 inventory 真实口令），
    ``rest`` 是物品名（可能已带 rank token，先剥离）。返回 ``head <物品> <rank>``
    形式的完整口令串——点击后 ``_match_natural`` 最长前缀切成 cmd=market、arg=
    ``<物品> <rank>`` → ``market_text`` 的 ``_parse_mod_rank`` 正确出满级/0级卡。

    只在确认「物品名非空」时派生；否则返回空（市价裸口令/分类不挂 rank 按钮）。
    注意：满级只对 MOD 有效（market_text 内 ``if not is_mod: return 不是 MOD``），
    普通物品点了会给功能提示，不是静默——这是业务层既有语义，按钮只提供下钻入口。
    """
    inv = get_inventory()
    card = get_card_inventory()
    # 归一到 inventory 里真实口令（大小写），并确认是市价口令
    market = next((c for c in inv.commands if c.lower() == head.lower()), "")
    if not market or market not in card.market_cmds:
        return [], []
    # 剥离物品名里已有的 rank 写法（避免「wm 绝路 满级」/「wm 0级 绝路」再挂
    # 按钮时 rank 重复堆叠）。正则逐字节来自 warframe._MOD_RANK_PATTERNS_*，
    # 取组规则（END→group1 / START→lastgroup）由 card_buttons 按源码列表名派生，
    # 与真实 _parse_mod_rank 完全同源，零手工 token。
    import re as _re
    item = (rest or "").strip()
    for pattern, clean_group in card.rank_strip_rules:
        try:
            m = _re.compile(pattern, _re.I).match(item)
        except _re.error:
            continue
        if not m:
            continue
        idx = 1 if clean_group == "first" else (m.lastindex or 1)
        if 1 <= idx <= m.lastindex:
            cleaned = (m.group(idx) or "").strip()
            if cleaned:
                item = cleaned
                break
    if not item:
        return [], []
    data = ["%s %s %s" % (market, item, tok) for tok in card.rank_tokens]
    labels = [_rank_label(tok) for tok in card.rank_tokens]
    return data, labels


def buttons_for(text: str) -> tuple[Btn, ...]:
    """按当前回复所属卡取「对应功能按钮」；绝不返回空。

    规则见模块 docstring：档位族卡→该卡全部档位；市价卡带物品名→+带参 rank；
    单档/兜底卡→自己+主菜单；非口令→GENERIC。所有 data 满足零编造不变量(a)/(b)。
    """
    inv = get_inventory()
    raw = (text or "").strip()
    head = normalize_cmd(raw)
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
    card = get_card_inventory()

    # 市价卡带物品名优先（wm 绝路 / 市价 延伸 满级 …）：档位族里没有市价卡，
    # 故不会与下面「档位族命中」冲突；但「信条」既是档位口令又在 market_cmds
    # （_wf_dispatch 信条查市价），所以档位族命中时优先按档位卡处理。
    slots = card.slots_for(inv_cmd)
    if len(slots) <= 1 and inv_cmd in card.market_cmds:
        rest = raw
        # 去掉前缀符号 + 口令首词，取剩余物品名
        while rest and rest[0] in "#＃/":
            rest = rest[1:].strip()
        parts = rest.split()
        item_rest = " ".join(parts[1:]) if parts else ""
        rank_data, rank_labels = _rank_button_data(inv_cmd, item_rest)
        if rank_data:
            return (primary,) + tuple(
                Btn(lbl, d, False) for d, lbl in zip(rank_data, rank_labels)
            ) + (_MENU,)

    # 档位族卡：按钮 = 该卡全部档位 canonical（含自己），primary 恒为自己。
    # 单档卡（slots 只有自己）自然退化成「自己 + 主菜单」，不硬凑全局词。
    if len(slots) > 1:
        siblings = tuple(
            Btn(c, c, primary=(c.lower() == inv_cmd.lower()))
            for c in slots if c.lower() != inv_cmd.lower()
        )
        # 平台每行≤5：primary + 最多 3 档位 + 主菜单 = 5
        return (primary,) + siblings[:3] + (_MENU,)

    # 单档 / 兜底卡：只自己 + 主菜单
    return (primary, _MENU)


#: 按钮卡片引导 markdown：QQ 官方 msg_type=2 是唯一支持「消息按钮」的类型，
#: 且要求同时有合法 markdown.content；纯 keyboard 会被平台以
#: 40034011「无效 markdown content」拒绝（2026-11-11 真实群实测）。
#: 用自定义 content（非 template_id）→ 个人主体无需申请 Markdown 模板权限
#: （官方错误码 304036/40034127 仅针对模板方式）。单行标准标题语法，规避
#: 模板参数换行限制，对齐官方文档「Markdown + 键盘消息」请求示例。
BUTTON_MARKDOWN = "## 快捷指令"


def make_button_message(kb_model, QQMessage, QQSegment):
    """把 keyboard 模型包成「markdown + keyboard 同段」的官方消息段。

    * 官方协议：消息按钮 keyboard 只能挂在 msg_type=2 的 markdown 消息上，
      故必须先放一段合法 markdown content，再放 keyboard；
    * 适配器 1.7.2 实测：markdown/keyboard 段**不会**被 extract_content 提取
      成 content（它只取 text/emoji/mention_*），最终 payload =
      {msg_type:2, markdown, keyboard}，content 为空，满足「传 markdown 后
      content/ark 必须全为空」，不会触发 22006；
    * 依赖注入 QQMessage/QQSegment，本函数自身不 import nonebot → 宿主可用
      假段单测「先 markdown 后 keyboard」的调用序列，容器可用真段测最终结构。

    返回构造好的 ``QQMessage``（供 official.py::_build_button_segment 调用）。
    """
    seg = QQMessage()
    seg += QQSegment.markdown(BUTTON_MARKDOWN)
    seg += QQSegment.keyboard(kb_model)
    return seg


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
