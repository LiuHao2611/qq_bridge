# -*- coding: utf-8 -*-
"""pic/templates/menu.py —— 帮助菜单图（2026-09-13 重构：主人菜单 / 一般菜单）。

布局（8pt 网格 + 卡片）：标题 → 副标题 → （可选）分组标题 + N 条菜单行 → 页脚。

内容分两套：
  * GENERAL_ITEMS —— 一般菜单（所有人可见：生图/改图/放大/扩图/任务/历史/队列…）；
  * OWNER_ITEMS   —— 主人专属（主项目任务 / 生图监听 / 监控告警 / 插队 / 清空队列）。

两套都可由机器人配置页（常规·菜单与帮助）自定义：
  * 标题/副标题/页脚：PIC_MENU_* / PIC_MENU_OWNER_*；
  * 条目高级自定义：PIC_MENU_ITEMS / PIC_MENU_OWNER_ITEMS，
    格式 `icon|标题|描述`，多条用 `;` 分隔，分组用 `#分组名`。
"""
from __future__ import annotations

from ..canvas import Canvas
from ..theme import Theme
from .. import decor, widgets as w

#: 一般菜单（所有人可见）—— (icon, 标题, 描述) 或 {"section": "分组名"}。
#: 口径与 intent.HELP_TEXT 保持一致（人话，别打斜杠）。
GENERAL_ITEMS = (
    ("wand", "生图", "发一句话，本喵给你画出来"),
    ("image", "改图", "先发张图，再说怎么改"),
    ("arrow_up", "高清放大", "把糊图放大变清晰"),
    ("square", "扩图", "往左/右/上/下扩一块"),
    ("triangle", "视频", "给句描述，本喵给你生成"),
    {"section": "游戏工具箱"},
    ("question", "角色图鉴", "角色 卡卡罗 鸣潮 —— 五款二游图鉴"),
    ("round_square", "配队推荐", "配队 忌炎 —— 五款二游主流队"),
    ("bolt", "养成路线", "养成 胡桃 —— 毕业武器与词条"),
    ("triangle", "当期副本", "副本 鸣潮 —— 深渊/混沌/危局/深塔"),
    {"section": "摸鱼小游戏"},
    ("circle", "猜数字", "猜数字 —— 1-100 猜大小"),
    ("star", "仓鼠运势", "运势/抽签 —— 每日仓鼠运势"),
    ("clock", "仓鼠黄历", "黄历/黄道吉日 —— 宜忌+紫卡+任务"),
    ("diamond", "模拟开卡", "模拟开卡 —— 纯娱乐开一张紫卡"),
    ("arrow_down", "掉落查询", "#wf 掉落 <物品> —— 官方掉落表"),
    ("diamond", "遗物查询", "#wf 遗物 <核桃/部件> —— 掉落+入库"),
    ("wand", "配卡建议", "#wf 配卡 <武器/战甲> —— 配卡思路+链接"),
    ("star", "金垃圾", "金垃圾 —— 杜卡德价值分档"),
    ("question", "结合目标", "#wf 结合目标 <敌人> —— 大黄脸扫描"),
    ("square", "成语接龙", "接龙 一心一意 —— 接下去"),
    ("diamond", "24点", "24点 —— 四则运算凑 24"),
    ("check", "答题", "答题 —— 二游知识问答"),
    {"section": "任务与作品"},
    ("clock", "我的任务", "查进度结果，回复编号重发"),
    ("star", "历史图库", "翻出图历史，回编号看原图"),
    ("pause", "队列", "看排队情况（本地独占一张）"),
    ("cross", "取消任务", "撤掉你投的（取消 + 编号）"),
    {"section": "其它"},
    ("diamond", "换主题", "深色 / 浅色 / 按时段自动"),
    ("question", "帮助", "把这份清单再发一遍"),
)

#: 主人专属菜单（仅主人可见）—— 管理类命令。
OWNER_ITEMS = (
    {"section": "主人专属"},
    ("bolt", "主项目任务", "看后台在跑啥 / 刚生成的图"),
    ("arrow_right", "生图监听", "开·关：主项目生图私聊提醒"),
    ("check", "监控告警", "开·关：任务失败/超时提醒"),
    ("round_square", "插队", "把任务提到最前（插队 + 编号）"),
    ("circle", "清空队列", "一键清空排队任务"),
)

#: 向后兼容别名（旧调用/旧测试用 DEFAULT_ITEMS 指代一般菜单）
DEFAULT_ITEMS = GENERAL_ITEMS

#: 标题 / 副标题 / 页脚默认文案
DEFAULT_TITLE = "本喵能干啥"
DEFAULT_SUBTITLE = "直接说人话就行，别老打斜杠"
DEFAULT_FOOTER = "闲聊免开尊口，要图直说就完事儿喵~"
DEFAULT_OWNER_SUBTITLE = "主人菜单 · 说人话就行，别老打斜杠"
DEFAULT_OWNER_FOOTER = "这些都是主人专属活儿，别人使唤不动本喵~"


def parse_items(spec: str):
    """解析配置项（字符串）→ items 列表；空/非法返回 None（调用方回退默认）。

    spec 形态：`wand|生图|发一句话;image|改图|发张图;#任务;clock|我的任务|查进度`
      * 条目：`icon|标题|描述`（描述可省，标题必填）；
      * 分组：以 `#` 开头 → {"section": 名称}；
      * icon 非法（不在 icons.ICONS）→ 回退为 circle（不因配错而崩）。
    """
    s = (spec or "").strip()
    if not s:
        return None
    from .. import icons as _icons

    out = []
    for raw in s.split(";"):
        seg = raw.strip()
        if not seg:
            continue
        if seg.startswith("#"):
            name = seg[1:].strip()
            if name:
                out.append({"section": name})
            continue
        parts = [p.strip() for p in seg.split("|")]
        title = parts[1] if len(parts) > 1 else parts[0]
        icon = parts[0] if len(parts) > 0 else "circle"
        desc = parts[2] if len(parts) > 2 else ""
        if not title:
            continue
        if not _icons.exists(icon):
            icon = "circle"
        out.append({"icon": icon, "title": title, "desc": desc})
    return out or None


def _item_h(t: Theme, item) -> int:
    """单条目占高：分组标题 40（含上方留白），普通行 row_h。"""
    if isinstance(item, dict) and item.get("section") is not None:
        return 40
    return w.row_h(t)


def build(
    t: Theme,
    items=None,
    title: str = DEFAULT_TITLE,
    subtitle: str = DEFAULT_SUBTITLE,
    footer: str = DEFAULT_FOOTER,
    owner: bool = False,
    width: int | None = None,
) -> Canvas:
    """渲染菜单图。

    :param items: 条目列表（None=按 owner 取默认：主人=一般+主人两段）。
    :param owner: 是否主人（仅影响默认 items 与右侧分组标记）。
    """
    if items is None:
        items = list(GENERAL_ITEMS) + (list(OWNER_ITEMS) if owner else [])
    rows = list(items)

    width = int(width or getattr(t, "width", 800) or 800)
    pad = int(getattr(t, "pad", 24) or 24)
    pad_in = pad * 2
    inner_w = width - pad_in * 2  # 卡片内可用宽（左右各 pad_in）

    n_entries = sum(1 for it in rows
                    if not (isinstance(it, dict) and it.get("section") is not None))
    body_h = sum(_item_h(t, it) for it in rows)
    h = pad + 96 + body_h + 64 + pad
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="menu")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    right = ("%d 项" % n_entries) + (" · 主人" if owner else "")
    y = w.header(c, t, pad_in, 64, title, subtitle=subtitle, right=right)
    y += 8
    for item in rows:
        if isinstance(item, dict) and item.get("section") is not None:
            y = w.section_label(c, t, pad_in, y + 6, str(item.get("section")))
            continue
        if isinstance(item, dict):
            icon = item.get("icon", "circle")
            title_ = item.get("title", "")
            desc = item.get("desc", "")
            badge = item.get("badge")
        else:
            icon, title_, desc = (list(item) + ["", ""])[:3]
            badge = None
        y = w.menu_row(c, t, pad_in, y, inner_w, icon, title_, desc, badge=badge)

    w.footer(c, t, pad_in, h - pad - 28, footer)
    decor.corner(c, t)
    return c
