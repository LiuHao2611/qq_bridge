# -*- coding: utf-8 -*-
"""斗破苍穹·双帝之战 · NoneBot2 口令入口（QQ 群聊 / 私聊文字版）。

纯口令、零 LLM。业务逻辑全在 commands.py / core.py（无 nonebot，可单测）；
本文件只做三件事：
  1) 解析群消息里的口令（允许可选 # 前缀 / 前导空白 / @机器人）
  2) 从 event 取 qq / 群 / @对象 / 数字参数
  3) 调 commands.cmd_* 把结果文字回群

⚠️ 优先级：主插件聊天处理器是 on_message(priority=10, block=False)。
本插件全部 matcher 用 priority=5 + block=True —— 先于聊天处理器运行，
且命中后阻断后续处理器，避免"口令被 LLM 抢答/双重回复"。

⚠️ 模式隔离：斗破 / 修仙 现在都用纯中文口令，靠「按用户记录的当前模式」分流。
   谁发「斗破菜单」→ 他后续口令走斗破；发「修仙菜单」→ 走修仙（菜单是唯一切换口）。
   没有模式或打错游戏的口令，由 game_router 兜底给友好提示，两游戏互不抢答。
"""

from __future__ import annotations

import logging
import re

from nonebot import on_regex, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment

from . import commands
from . import card
from . import avatar
from . import core
from . import store as store_mod

logger = logging.getLogger("qq_bridge.doupo")

store = store_mod.Store()


# priority=5（高于聊天处理器的 10） + block=True（命中即阻断 LLM 闲聊）
from src.game_mode import get_mode, set_mode, register_keywords


def _mode_is(game: str):
    # 官方副本 adapter 隔离（2026-11-11）：event 必须注解为 NapCat/OneBot V11
    # 具体类型。用户给机器人开通「获取群内全部消息」后，官方全量群消息
    # (GroupMessageCreateEvent，无 user_id 字段) 会进入本 rule；若 event 无注解，
    # NoneBot 依赖注入放行 → 执行 event.user_id → AttributeError → 每条官方消息
    # 刷一条 "Rule check failed" ERROR 噪音。注解具体 MessageEvent 后，官方事件
    # 在依赖注入阶段抛 TypeMisMatch(SkippedException)，被 Rule.__call__ 静默置
    # False，绝不再碰 event.user_id；NapCat 的 MessageEvent 实例照常通过，行为不变。
    async def _r(event: MessageEvent) -> bool:
        return (await get_mode(str(event.user_id))) == game
    return _r

PRI = 5


def _pat(kw: str) -> str:
    """允许前导 CQ 码 / 空白任意交替 + 可选 # 前缀。

    ⚠️ nonebot 的 RegexRule 用 re.search(pattern, **str(event.get_message())**) 匹配，
    即匹配对象是 CQ 码字符串（如 '[CQ:at,qq=10002]抢'），不是纯文本。
    因此 ^ 之后必须先吃掉前置 CQ 码，否则所有「@某人 + 口令」的玩法全部失效。
    """
    return r"^(?:\[CQ:[^\]]*\]|\s)*#?\s*" + kw


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _gid(event: MessageEvent):
    return getattr(event, "group_id", None)


def _extract_at(event: MessageEvent):
    """取第一个"他人" @ 对象：跳过机器人自己与发送者本人。"""
    me = str(getattr(event, "self_id", ""))
    uid = str(event.user_id)
    for seg in event.message:
        if getattr(seg, "type", None) == "at":
            qq = str(seg.data.get("qq"))
            if qq and qq not in (me, "all") and qq != uid:
                return qq
    return None


def _plain(event: MessageEvent) -> str:
    return event.get_plaintext()


def _arg(event: MessageEvent, kw: str) -> str:
    """取口令后面的参数（先剥前导 CQ 残留/空白/#，再剥关键词）。"""
    t = re.sub(r"^\s*#?\s*", "", _plain(event))
    return t.replace(kw, "", 1).strip()


def _name(event: MessageEvent) -> str:
    sender = getattr(event, "sender", None)
    if sender and getattr(sender, "card", None):
        return sender.card.strip()
    if sender and getattr(sender, "nickname", None):
        return sender.nickname.strip()
    return f"道友{str(event.user_id)[-4:]}"


# 私聊虚拟作用域前缀：每个用户一个「虚拟群」，隔离单人存档。
# 这样斗破既能在群聊玩（真实 group_id），也能在私聊单人玩（pv:<user_id>）。
# 已知限制：私聊里没有「其他成员」，故 排行/抢/送 在私聊只涉及自己。
PRIVATE_SCOPE_PREFIX = "pv:"


def _scope(event: MessageEvent) -> str:
    """当前会话作用域：群聊=真实 group_id；私聊=pv:<user_id>。"""
    gid = _gid(event)
    if gid:
        return str(gid)
    return f"{PRIVATE_SCOPE_PREFIX}{event.user_id}"


async def _need_group(event: MessageEvent, matcher):
    """取当前会话作用域。

    历史版本：私聊一律拒绝（"请在群聊中使用斗破功能～"）。
    现按需求放开私聊——私聊用 pv:<user_id> 作虚拟作用域，单人可玩；
    matcher 参数保留仅为兼容既有调用点，不再用于阻断。
    """
    return _scope(event)


def _num(event: MessageEvent, default=None):
    m = re.search(r"(\d+)", _plain(event))
    return int(m.group(1)) if m else default


# ---------------------------------------------------------------------------
# 菜单 / 帮助
# ---------------------------------------------------------------------------
menu = on_regex(_pat(r"(?:斗破菜单|斗破口令)"), priority=PRI, block=True)


@menu.handle()
async def _(event: MessageEvent):
    await set_mode(str(event.user_id), "doupo")
    await _need_group(event, menu)
    # 纯文本兜底（commands.cmd_menu 即菜单唯一源，与图片同源）
    msg = commands.cmd_menu()
    # 优先发自绘菜单卡；任何异常都降级为上面的纯文本（绝不丢消息）
    png = None
    try:
        png = await card.render_menu()
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破菜单卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await menu.finish(MessageSegment.image(png))
    await menu.finish(msg)


helper = on_regex(_pat(r"(?:帮助|玩法|攻略|规则)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@helper.handle()
async def _(event: MessageEvent):
    await _need_group(event, helper)
    await helper.finish(commands.cmd_help())


# ---------------------------------------------------------------------------
# 入道 / 人物
# ---------------------------------------------------------------------------
enter = on_regex(_pat(r"(?:入道|开始|创建角色)"), priority=PRI, block=True)


@enter.handle()
async def _(event: MessageEvent):
    await set_mode(str(event.user_id), "doupo")
    gid = await _need_group(event, enter)
    msg = await commands.cmd_enter(store, str(event.user_id), _name(event), gid)
    # 建号欢迎动图（1 级 = 主角持剑站姿），失败不影响文字。
    # 注：这是全流程里唯一剩下的动态形象；「人物 / 属性 / 面板」已按用户要求
    # 改成只发一张内嵌静态头像的卡片，不再夹带动图（见下方 panel）。
    gif = None
    try:
        gif = avatar.avatar_bytes(1)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破入道动图读取异常，只发文字：%s: %s", type(e).__name__, e)
    if gif:
        await enter.finish(Message([MessageSegment.image(gif), MessageSegment.text(msg)]))
    await enter.finish(msg)


panel = on_regex(_pat(r"(?:人物|属性|面板)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@panel.handle()
async def _(event: MessageEvent):
    await _need_group(event, panel)
    qq = str(event.user_id)
    # 纯文本兜底（commands.cmd_panel 内部已结算离线修为并存档）
    msg = await commands.cmd_panel(store, qq)
    # 2026-11-07 用户要求：人物卡里不再放动态小人，改为卡片内嵌静态头像。
    # 因此这里**只发一张 PNG 卡片**，绝不额外拼 GIF 段；出图口令必须出图，
    # 但也不许用「卡片 + 动图」两张图刷屏。渲染异常才回落到上面的纯文本。
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_panel(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破人物卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await panel.finish(MessageSegment.image(png))
    await panel.finish(msg)


# ---------------------------------------------------------------------------
# 修炼 / 突破 / 战斗
# ---------------------------------------------------------------------------
cult = on_regex(_pat(r"(?:修炼|静修|打坐)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@cult.handle()
async def _(event: MessageEvent):
    await _need_group(event, cult)
    await cult.finish(await commands.cmd_cultivate(store, str(event.user_id)))


brk = on_regex(_pat(r"(?:突破|晋升|升星)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@brk.handle()
async def _(event: MessageEvent):
    await _need_group(event, brk)
    await brk.finish(await commands.cmd_break(store, str(event.user_id)))


fight = on_regex(_pat(r"(?:战斗|打怪|历练|出战)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@fight.handle()
async def _(event: MessageEvent):
    await _need_group(event, fight)
    await fight.finish(await commands.cmd_fight(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 强化 / 斗技 / 异火
# ---------------------------------------------------------------------------
enh = on_regex(_pat(r"强化"), priority=PRI, block=True, rule=_mode_is("doupo"))


@enh.handle()
async def _(event: MessageEvent):
    await _need_group(event, enh)
    await enh.finish(await commands.cmd_enhance(store, str(event.user_id)))


skills = on_regex(_pat(r"(?:斗技|技能)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@skills.handle()
async def _(event: MessageEvent):
    await _need_group(event, skills)
    qq = str(event.user_id)
    # 纯文本兜底（commands.cmd_skills 即数据源，与图片同源）
    msg = await commands.cmd_skills(store, qq)
    # 优先发自绘列表卡；任何异常都降级为上面的纯文本（绝不丢消息）
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_skills(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破斗技卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await skills.finish(MessageSegment.image(png))
    await skills.finish(msg)


sup = on_regex(_pat(r"(?:升级|升技)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@sup.handle()
async def _(event: MessageEvent):
    await _need_group(event, sup)
    arg = _arg(event, "升级") if "升级" in _plain(event) else _arg(event, "升技")
    await sup.finish(await commands.cmd_skill_up(store, str(event.user_id), arg))


flames = on_regex(_pat(r"异火"), priority=PRI, block=True, rule=_mode_is("doupo"))


@flames.handle()
async def _(event: MessageEvent):
    await _need_group(event, flames)
    qq = str(event.user_id)
    # 纯文本兜底（与图片同源）
    msg = await commands.cmd_flames(store, qq)
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_flames(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破异火卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await flames.finish(MessageSegment.image(png))
    await flames.finish(msg)


feed = on_regex(_pat(r"(?:养火|喂火)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@feed.handle()
async def _(event: MessageEvent):
    await _need_group(event, feed)
    arg = _arg(event, "养火") if "养火" in _plain(event) else _arg(event, "喂火")
    await feed.finish(await commands.cmd_feed(store, str(event.user_id), arg))


# ---------------------------------------------------------------------------
# 宝库 / 炼药 / 背包
# ---------------------------------------------------------------------------
shop = on_regex(_pat(r"(?:宝库|商店|坊市)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@shop.handle()
async def _(event: MessageEvent):
    await _need_group(event, shop)
    qq = str(event.user_id)
    # 纯文本兜底（与图片同源）
    msg = await commands.cmd_shop(store, qq)
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_shop(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破宝库卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await shop.finish(MessageSegment.image(png))
    await shop.finish(msg)


buy = on_regex(_pat(r"(?:买|购买)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@buy.handle()
async def _(event: MessageEvent):
    await _need_group(event, buy)
    arg = _arg(event, "购买") if "购买" in _plain(event) else _arg(event, "买")
    await buy.finish(await commands.cmd_buy(store, str(event.user_id), arg))


recipes = on_regex(_pat(r"(?:炼药|丹方)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@recipes.handle()
async def _(event: MessageEvent):
    await _need_group(event, recipes)
    await recipes.finish(await commands.cmd_recipes(store, str(event.user_id)))


craft = on_regex(_pat(r"(?:炼制|炼丹)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@craft.handle()
async def _(event: MessageEvent):
    await _need_group(event, craft)
    arg = _arg(event, "炼制") if "炼制" in _plain(event) else _arg(event, "炼丹")
    await craft.finish(await commands.cmd_craft(store, str(event.user_id), arg))


bag = on_regex(_pat(r"(?:背包|包裹|行囊)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@bag.handle()
async def _(event: MessageEvent):
    await _need_group(event, bag)
    qq = str(event.user_id)
    # 纯文本兜底（与图片同源）
    msg = await commands.cmd_bag(store, qq)
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_bag(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破背包卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await bag.finish(MessageSegment.image(png))
    await bag.finish(msg)


use = on_regex(_pat(r"(?:服丹|用丹|吃药)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@use.handle()
async def _(event: MessageEvent):
    await _need_group(event, use)
    txt = _plain(event)
    kw = "服丹" if "服丹" in txt else ("用丹" if "用丹" in txt else "吃药")
    await use.finish(await commands.cmd_use(store, str(event.user_id), _arg(event, kw)))


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
tasks = on_regex(_pat(r"任务") + r"\s*$", priority=PRI, block=True, rule=_mode_is("doupo"))


@tasks.handle()
async def _(event: MessageEvent):
    await _need_group(event, tasks)
    await tasks.finish(await commands.cmd_tasks(store, str(event.user_id)))


taccept = on_regex(_pat(r"接任务"), priority=PRI, block=True, rule=_mode_is("doupo"))


@taccept.handle()
async def _(event: MessageEvent):
    await _need_group(event, taccept)
    await taccept.finish(await commands.cmd_task_accept(store, str(event.user_id)))


tsubmit = on_regex(_pat(r"交任务"), priority=PRI, block=True, rule=_mode_is("doupo"))


@tsubmit.handle()
async def _(event: MessageEvent):
    await _need_group(event, tsubmit)
    await tsubmit.finish(await commands.cmd_task_submit(store, str(event.user_id)))


msubmit = on_regex(_pat(r"(?:领主线|交主线)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@msubmit.handle()
async def _(event: MessageEvent):
    await _need_group(event, msubmit)
    await msubmit.finish(await commands.cmd_main_submit(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 排行 / 抢 / 送
# ---------------------------------------------------------------------------
rank = on_regex(_pat(r"(?:排行|排行榜|战力榜)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@rank.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, rank)
    # 纯文本兜底（与图片同源）
    msg = await commands.cmd_rank(store, gid)
    png = None
    try:
        ps = await store.all_players(gid)
        top = core.rank(ps)[:10]
        png = await card.render_rank(top)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破排行卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await rank.finish(MessageSegment.image(png))
    await rank.finish(msg)


rob = on_regex(_pat(r"(?:抢|抢夺|掠夺)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@rob.handle()
async def _(event: MessageEvent):
    await _need_group(event, rob)
    await rob.finish(await commands.cmd_rob(store, str(event.user_id), _extract_at(event)))


give = on_regex(_pat(r"(?:送|赠送)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@give.handle()
async def _(event: MessageEvent):
    await _need_group(event, give)
    await give.finish(await commands.cmd_give(
        store, str(event.user_id), _extract_at(event), _num(event)))


# ---------------------------------------------------------------------------
# 启动：载入存档
# ---------------------------------------------------------------------------
@get_driver().on_startup
async def _startup():
    await store.load()


# 口令关键字登记（供 game_router 兜底探测）
register_keywords("doupo", [
    "斗破菜单",
    "斗破口令",
    "帮助",
    "玩法",
    "攻略",
    "规则",
    "入道",
    "开始",
    "创建角色",
    "人物",
    "属性",
    "面板",
    "修炼",
    "静修",
    "打坐",
    "突破",
    "晋升",
    "升星",
    "战斗",
    "打怪",
    "历练",
    "出战",
    "强化",
    "斗技",
    "技能",
    "升级",
    "升技",
    "异火",
    "养火",
    "喂火",
    "宝库",
    "商店",
    "坊市",
    "买",
    "购买",
    "炼药",
    "丹方",
    "炼制",
    "炼丹",
    "背包",
    "包裹",
    "行囊",
    "服丹",
    "用丹",
    "吃药",
    "任务",
    "接任务",
    "交任务",
    "领主线",
    "交主线",
    "排行",
    "排行榜",
    "战力榜",
    "抢",
    "抢夺",
    "掠夺",
    "送",
    "赠送",
])
