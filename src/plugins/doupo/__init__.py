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

⚠️ 命名：除「斗破菜单」外全部口令带 dp 前缀。
   修仙插件已占用 商店 / 坊市 / 突破 / 排行榜 / 任务 / 炼丹，
   同为 priority=5 的 matcher 会互相抢答，统一前缀是最干净的隔离方式。
"""

from __future__ import annotations

import asyncio
import logging
import re

from nonebot import on_regex, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment

from . import commands
from . import card
from . import avatar
from . import core
from . import store as store_mod
from . import _throttle

logger = logging.getLogger("qq_bridge.doupo")

store = store_mod.Store()

# 出图频率护栏（见 _throttle.py 顶部：为什么只拦出图、为什么静默丢弃）
throttle = _throttle.Throttle()

# priority=5（高于聊天处理器的 10） + block=True（命中即阻断 LLM 闲聊）
PRI = 5


def _pat(kw: str) -> str:
    """允许前导 CQ 码 / 空白任意交替 + 可选 # 前缀。

    ⚠️ nonebot 的 RegexRule 用 re.search(pattern, **str(event.get_message())**) 匹配，
    即匹配对象是 CQ 码字符串（如 '[CQ:at,qq=10002]dp抢'），不是纯文本。
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


async def _heavy(event: MessageEvent, matcher):
    """出图前必须过的闸口：超频则静默丢弃，放行则随机抖动后再渲染。

    ⚠️ 为什么每个出图 handler 都必须在 `_need_group` 之后调用它：
       `matcher.finish()` 不传参 = 不发任何消息 + 抛出 FinishedException 掐断
       传播链。这样被限流时群里看不到任何回应，不会暴露「这个号在限流」。
       反过来说，任何新增的出图指令如果漏了这一行，就等于给风控留了一个
       可以无限刷的入口 —— 改代码时请一并检查。
    """
    gid = _gid(event)
    ok, why = throttle.check(user=str(event.user_id), group=str(gid) if gid else None)
    if not ok:
        logger.debug(
            "斗破出图限流 user=%s group=%s reason=%s", event.user_id, gid, why
        )
        await matcher.finish()
    # 打散毫秒级规律：出图前随机等一小段，去掉「触发即回图」的机器节奏
    await asyncio.sleep(throttle.jitter_delay())


def _num(event: MessageEvent, default=None):
    m = re.search(r"(\d+)", _plain(event))
    return int(m.group(1)) if m else default


# ---------------------------------------------------------------------------
# 菜单 / 帮助
# ---------------------------------------------------------------------------
menu = on_regex(_pat(r"(?:斗破菜单|dp菜单|斗破口令)"), priority=PRI, block=True)


@menu.handle()
async def _(event: MessageEvent):
    await _need_group(event, menu)
    await _heavy(event, menu)
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


helper = on_regex(_pat(r"dp(?:帮助|玩法|攻略|规则)"), priority=PRI, block=True)


@helper.handle()
async def _(event: MessageEvent):
    await _need_group(event, helper)
    await helper.finish(commands.cmd_help())


# ---------------------------------------------------------------------------
# 入道 / 人物
# ---------------------------------------------------------------------------
enter = on_regex(_pat(r"dp(?:入道|开始|创建角色)"), priority=PRI, block=True)


@enter.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, enter)
    await _heavy(event, enter)
    msg = await commands.cmd_enter(store, str(event.user_id), _name(event), gid)
    # 建号即展示角色动态形象（1 级 = 主角持剑站姿），失败不影响文字
    gif = None
    try:
        gif = avatar.avatar_bytes(1)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破入道动图读取异常，只发文字：%s: %s", type(e).__name__, e)
    if gif:
        await enter.finish(Message([MessageSegment.image(gif), MessageSegment.text(msg)]))
    await enter.finish(msg)


panel = on_regex(_pat(r"dp(?:人物|属性|面板)"), priority=PRI, block=True)


@panel.handle()
async def _(event: MessageEvent):
    await _need_group(event, panel)
    await _heavy(event, panel)
    qq = str(event.user_id)
    # 纯文本兜底（commands.cmd_panel 内部已结算离线修为并存档）
    msg = await commands.cmd_panel(store, qq)
    # 优先发「角色动态形象 + 自绘人物卡」；任何异常都降级为上面的纯文本（绝不丢消息）
    p = None
    png = None
    gif = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_panel(p)
            # 角色动态待机动图（按境界分档），失败不影响卡片
            try:
                gif = avatar.avatar_for(p)
            except Exception as e:  # noqa: BLE001
                logger.warning("斗破角色动图读取异常，只发卡片：%s: %s", type(e).__name__, e)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破人物卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png or gif:
        segs = []
        if gif:
            segs.append(MessageSegment.image(gif))
        if png:
            segs.append(MessageSegment.image(png))
        if len(segs) == 1:
            await panel.finish(segs[0])
        await panel.finish(Message(segs))
    await panel.finish(msg)


# ---------------------------------------------------------------------------
# 修炼 / 突破 / 战斗
# ---------------------------------------------------------------------------
cult = on_regex(_pat(r"dp(?:修炼|静修|打坐)"), priority=PRI, block=True)


@cult.handle()
async def _(event: MessageEvent):
    await _need_group(event, cult)
    await cult.finish(await commands.cmd_cultivate(store, str(event.user_id)))


brk = on_regex(_pat(r"dp(?:突破|晋升|升星)"), priority=PRI, block=True)


@brk.handle()
async def _(event: MessageEvent):
    await _need_group(event, brk)
    await brk.finish(await commands.cmd_break(store, str(event.user_id)))


fight = on_regex(_pat(r"dp(?:战斗|打怪|历练|出战)"), priority=PRI, block=True)


@fight.handle()
async def _(event: MessageEvent):
    await _need_group(event, fight)
    await fight.finish(await commands.cmd_fight(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 强化 / 斗技 / 异火
# ---------------------------------------------------------------------------
enh = on_regex(_pat(r"dp强化"), priority=PRI, block=True)


@enh.handle()
async def _(event: MessageEvent):
    await _need_group(event, enh)
    await enh.finish(await commands.cmd_enhance(store, str(event.user_id)))


skills = on_regex(_pat(r"dp(?:斗技|技能)"), priority=PRI, block=True)


@skills.handle()
async def _(event: MessageEvent):
    await _need_group(event, skills)
    await _heavy(event, skills)
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


sup = on_regex(_pat(r"dp(?:升级|升技)") + r"\s*(.*)", priority=PRI, block=True)


@sup.handle()
async def _(event: MessageEvent):
    await _need_group(event, sup)
    arg = _arg(event, "dp升级") if "dp升级" in _plain(event) else _arg(event, "dp升技")
    await sup.finish(await commands.cmd_skill_up(store, str(event.user_id), arg))


flames = on_regex(_pat(r"dp异火"), priority=PRI, block=True)


@flames.handle()
async def _(event: MessageEvent):
    await _need_group(event, flames)
    await _heavy(event, flames)
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


feed = on_regex(_pat(r"dp(?:养火|喂火)") + r"\s*(.*)", priority=PRI, block=True)


@feed.handle()
async def _(event: MessageEvent):
    await _need_group(event, feed)
    arg = _arg(event, "dp养火") if "dp养火" in _plain(event) else _arg(event, "dp喂火")
    await feed.finish(await commands.cmd_feed(store, str(event.user_id), arg))


# ---------------------------------------------------------------------------
# 宝库 / 炼药 / 背包
# ---------------------------------------------------------------------------
shop = on_regex(_pat(r"dp(?:宝库|商店|坊市)"), priority=PRI, block=True)


@shop.handle()
async def _(event: MessageEvent):
    await _need_group(event, shop)
    await _heavy(event, shop)
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


buy = on_regex(_pat(r"dp(?:买|购买)") + r"\s*(.*)", priority=PRI, block=True)


@buy.handle()
async def _(event: MessageEvent):
    await _need_group(event, buy)
    arg = _arg(event, "dp购买") if "dp购买" in _plain(event) else _arg(event, "dp买")
    await buy.finish(await commands.cmd_buy(store, str(event.user_id), arg))


recipes = on_regex(_pat(r"dp(?:炼药|丹方)"), priority=PRI, block=True)


@recipes.handle()
async def _(event: MessageEvent):
    await _need_group(event, recipes)
    await recipes.finish(await commands.cmd_recipes(store, str(event.user_id)))


craft = on_regex(_pat(r"dp(?:炼制|炼丹)") + r"\s*(.*)", priority=PRI, block=True)


@craft.handle()
async def _(event: MessageEvent):
    await _need_group(event, craft)
    arg = _arg(event, "dp炼制") if "dp炼制" in _plain(event) else _arg(event, "dp炼丹")
    await craft.finish(await commands.cmd_craft(store, str(event.user_id), arg))


bag = on_regex(_pat(r"dp(?:背包|包裹|行囊)"), priority=PRI, block=True)


@bag.handle()
async def _(event: MessageEvent):
    await _need_group(event, bag)
    await _heavy(event, bag)
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


use = on_regex(_pat(r"dp(?:服丹|用丹|吃药)") + r"\s*(.*)", priority=PRI, block=True)


@use.handle()
async def _(event: MessageEvent):
    await _need_group(event, use)
    txt = _plain(event)
    kw = "dp服丹" if "dp服丹" in txt else ("dp用丹" if "dp用丹" in txt else "dp吃药")
    await use.finish(await commands.cmd_use(store, str(event.user_id), _arg(event, kw)))


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
tasks = on_regex(_pat(r"dp任务") + r"\s*$", priority=PRI, block=True)


@tasks.handle()
async def _(event: MessageEvent):
    await _need_group(event, tasks)
    await tasks.finish(await commands.cmd_tasks(store, str(event.user_id)))


taccept = on_regex(_pat(r"dp接任务"), priority=PRI, block=True)


@taccept.handle()
async def _(event: MessageEvent):
    await _need_group(event, taccept)
    await taccept.finish(await commands.cmd_task_accept(store, str(event.user_id)))


tsubmit = on_regex(_pat(r"dp交任务"), priority=PRI, block=True)


@tsubmit.handle()
async def _(event: MessageEvent):
    await _need_group(event, tsubmit)
    await tsubmit.finish(await commands.cmd_task_submit(store, str(event.user_id)))


msubmit = on_regex(_pat(r"dp(?:领主线|交主线)"), priority=PRI, block=True)


@msubmit.handle()
async def _(event: MessageEvent):
    await _need_group(event, msubmit)
    await msubmit.finish(await commands.cmd_main_submit(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 排行 / 抢 / 送
# ---------------------------------------------------------------------------
rank = on_regex(_pat(r"dp(?:排行|排行榜|战力榜)"), priority=PRI, block=True)


@rank.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, rank)
    await _heavy(event, rank)
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


rob = on_regex(_pat(r"dp(?:抢|抢夺|掠夺)"), priority=PRI, block=True)


@rob.handle()
async def _(event: MessageEvent):
    await _need_group(event, rob)
    await rob.finish(await commands.cmd_rob(store, str(event.user_id), _extract_at(event)))


give = on_regex(_pat(r"dp(?:送|赠送)"), priority=PRI, block=True)


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
