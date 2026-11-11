# -*- coding: utf-8 -*-
"""修仙文字游戏 · NoneBot2 口令入口。

纯口令、无 LLM。业务逻辑全在 commands.py（无 nonebot，可单测）；
本文件只做三件事：
  1) 解析群消息里的口令（允许可选 # 前缀 / 前导空白）
  2) 从 event 取 qq / 群 / @对象 / 数字参数
  3) 调 commands.cmd_* 把结果文字回群

⚠️ 优先级：主插件聊天处理器是 on_message(priority=10, block=False)。
本插件全部 matcher 用 priority=5 + block=True —— 先于聊天处理器运行，
且命中后阻断后续处理器，避免"口令被 LLM 抢答/双重回复"。

设计目标（用户要求）：让人上瘾但不是上班 ——
  * 闭关 = 被动成长（离线也涨），不强制每日任务
  * 抢/偷/切磋 = 社交摩擦，是每日群活跃的真正引擎
  * 世界Boss/秘境/奇遇 = 后台不定时炸群，制造好奇而非考核
"""

from __future__ import annotations

import re

import nonebot
from nonebot import on_regex, get_driver
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger

from . import card
from . import config as cfg
from . import commands
from . import core
from . import broadcast
from . import store as store_mod

from src.group_switch import group_enabled

store = store_mod.Store()


# priority=5（高于聊天处理器的 10） + block=True（命中即阻断 LLM 闲聊）
from src.game_mode import get_mode, set_mode, register_keywords


def _mode_is(game: str):
    # 官方副本 adapter 隔离（2026-11-11）：event 必须注解为 NapCat/OneBot V11
    # 具体类型，否则官方全量群消息(GroupMessageCreateEvent，无 user_id)会进入本
    # rule 并执行 event.user_id → AttributeError → 每条官方消息刷 Rule check
    # failed ERROR。注解后官方事件在依赖注入阶段 TypeMisMatch → Rule.__call__
    # 静默置 False；NapCat 的 MessageEvent 实例照常通过，行为不变。
    async def _r(event: MessageEvent) -> bool:
        return (await get_mode(str(event.user_id))) == game
    return _r

PRI = 5


def _pat(kw: str) -> str:
    """允许前导 CQ 码 / 空白任意交替 + 可选 # 前缀。

    ⚠️ nonebot 的 RegexRule 用 re.search(pattern, **str(event.get_message())**) 匹配，
    即匹配对象是 CQ 码字符串（如 '[CQ:at,qq=10002]切磋'），不是纯文本。
    因此 ^ 之后必须先吃掉前置 CQ 码，否则所有"@某人 + 口令"的玩法全部失效。
    注意 '@机器人 @某人 抢灵石' 实际是 CQ 码与空格交替出现，需循环吃掉。
    """
    return r"^(?:\[CQ:[^\]]*\]|\s)*#?\s*" + kw


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _gid(event: MessageEvent):
    return getattr(event, "group_id", None)


def _extract_at(event: MessageEvent):
    """取第一个"他人" @ 对象：跳过机器人自己与发送者本人。

    「@机器人 抢灵石@张三」是群里最常见的用法，若直接取第一个 at 会拿到机器人 QQ，
    导致把 bot 当成对手（对方尚未入道）。这里一并过滤掉。
    """
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


def _target(event: MessageEvent):
    """取被 @ 的对象；无可用的 @ 返回 None。"""
    return _extract_at(event)


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


async def _need_group(event: MessageEvent, matcher):
    """群聊外直接拒绝并结束；群内返回 gid。"""
    gid = _gid(event)
    if not gid:
        await matcher.finish("请在群聊中使用修仙功能～")
    return str(gid)


# 首次入道时的卡片底部提示（之后用默认提示）
TIP_FIRST = "初次入道 · 发送「闭关」开始修炼，或「抢灵石」找人切磋"


async def _finish_status(matcher, event: MessageEvent, text: str,
                         tip: str | None = None) -> None:
    """状态类回复：优先发自绘图片卡，任何异常都降级为纯文本（绝不丢消息）。

    文案仍由 commands 层生成 —— 图片只是"更好看的呈现"，两者信息等价；
    这样即使渲染挂了、字体缺失、主题损坏，用户照样能拿到完整状态。
    """
    png = None
    try:
        p = await store.get_player(str(event.user_id))
        if p:
            png = await card.render_status(p, tip=tip)
    except Exception as e:  # noqa: BLE001
        logger.warning("修仙状态卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await matcher.finish(MessageSegment.image(png))
    await matcher.finish(text)


# ---------------------------------------------------------------------------
# 入道 / 状态
# ---------------------------------------------------------------------------
reg = on_regex(_pat("我要修仙"), priority=PRI, block=True)


@reg.handle()
async def _(event: MessageEvent):
    await set_mode(str(event.user_id), "xiuxian")
    gid = await _need_group(event, reg)
    qq = str(event.user_id)
    first = await store.get_player(qq) is None
    msg = await commands.cmd_enter(store, qq, _name(event), gid)
    await _finish_status(reg, event, msg, tip=TIP_FIRST if first else None)


status = on_regex(_pat("我的修仙"), priority=PRI, block=True)


@status.handle()
async def _(event: MessageEvent):
    await set_mode(str(event.user_id), "xiuxian")
    await _need_group(event, status)
    msg = await commands.cmd_status(store, str(event.user_id))
    await _finish_status(status, event, msg)


# 菜单/帮助：两张卡分工，别名覆盖口语。
#   「修仙菜单」= 有哪些口令（索引）；「修仙帮助」= 规则怎么算（细则，带数值）。
# 与「我要修仙」「我的修仙」不冲突：_pat() 是 ^ 锚定的前缀匹配，二者要求以「我」开头。
async def _finish_card(matcher, event: MessageEvent, render, text: str, what: str) -> None:
    """卡片类回复：优先发图，渲染失败降级纯文本（两者信息等价，绝不丢消息）。"""
    png = None
    try:
        png = await render()
    except Exception as e:  # noqa: BLE001
        logger.warning("%s 渲染异常，降级纯文本：%s: %s", what, type(e).__name__, e)
    if png:
        await matcher.finish(MessageSegment.image(png))
    await matcher.finish(text)


menu = on_regex(_pat("修仙(?:菜单|口令)"), priority=PRI, block=True)


@menu.handle()
async def _(event: MessageEvent):
    await set_mode(str(event.user_id), "xiuxian")
    await _need_group(event, menu)
    await _finish_card(menu, event, card.render_menu, commands.cmd_menu(), "修仙菜单卡")


helper = on_regex(_pat("(?:帮助|玩法|攻略|规则)"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@helper.handle()
async def _(event: MessageEvent):
    await _need_group(event, helper)
    await _finish_card(helper, event, card.render_help, commands.cmd_help(), "修仙玩法卡")


# ---------------------------------------------------------------------------
# 闭关 / 出关 / 突破
# ---------------------------------------------------------------------------
secl = on_regex(_pat("闭关"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@secl.handle()
async def _(event: MessageEvent):
    await _need_group(event, secl)
    await secl.finish(await commands.cmd_seclude(store, str(event.user_id)))


unsecl = on_regex(_pat("出关"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@unsecl.handle()
async def _(event: MessageEvent):
    await _need_group(event, unsecl)
    await unsecl.finish(await commands.cmd_unseclude(store, str(event.user_id)))


bt = on_regex(_pat("突破"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@bt.handle()
async def _(event: MessageEvent):
    await _need_group(event, bt)
    await bt.finish(await commands.cmd_breakthrough(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 机缘 / 秘境
# ---------------------------------------------------------------------------
claim = on_regex(_pat("领取机缘"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@claim.handle()
async def _(event: MessageEvent):
    await _need_group(event, claim)
    await claim.finish(await commands.cmd_claim(store, str(event.user_id)))


exp = on_regex(_pat("秘境"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@exp.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, exp)
    await exp.finish(await commands.cmd_explore(store, str(event.user_id), gid))


# 仅主人：立即开启一场限时秘境（复核在 commands.cmd_open_secret 内）
openSec = on_regex(_pat("开启秘境") + r"\s*$", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@openSec.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, openSec)
    await openSec.finish(await commands.cmd_open_secret(store, gid, str(event.user_id)))


# ---------------------------------------------------------------------------
# 悬赏：看榜 / 接取 / 结算（数据搬自开源 悬赏令.json，见 config_open）
# ---------------------------------------------------------------------------
bounty = on_regex(_pat("悬赏") + r"\s*$", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@bounty.handle()
async def _(event: MessageEvent):
    await _need_group(event, bounty)
    await bounty.finish(await commands.cmd_bounty(store, str(event.user_id)))


takebounty = on_regex(_pat("接悬赏") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@takebounty.handle()
async def _(event: MessageEvent):
    await _need_group(event, takebounty)
    # 参数从纯文本里取（CQ 码已在 _pat 前缀里被吃掉，这里不能再拿 CQ 串去正则）
    m = re.search(r"接悬赏\s*(.*)", _plain(event))
    name = (m.group(1) if m else "").strip()
    await takebounty.finish(await commands.cmd_take_bounty(store, str(event.user_id), name))


finbounty = on_regex(_pat("交悬赏") + r"\s*$", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@finbounty.handle()
async def _(event: MessageEvent):
    await _need_group(event, finbounty)
    await finbounty.finish(await commands.cmd_finish_bounty(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 商店 / 炼丹 / 用丹（数据搬自开源 炼丹丹药.json / 药材.json / 炼丹炉.json）
# ---------------------------------------------------------------------------
shop = on_regex(_pat("商店|坊市") + r"\s*$", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@shop.handle()
async def _(event: MessageEvent):
    await _need_group(event, shop)
    await shop.finish(await commands.cmd_shop(store, str(event.user_id)))


buy = on_regex(_pat("买") + r"\s*(.+)", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@buy.handle()
async def _(event: MessageEvent):
    await _need_group(event, buy)
    m = re.search(r"买\s*(.*)", _plain(event))
    key = (m.group(1) if m else "").strip()
    await buy.finish(await commands.cmd_buy(store, str(event.user_id), key))


mix = on_regex(_pat("炼丹") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@mix.handle()
async def _(event: MessageEvent):
    await _need_group(event, mix)
    m = re.search(r"炼丹\s*(.*)", _plain(event))
    name = (m.group(1) if m else "").strip()
    await mix.finish(await commands.cmd_mix(store, str(event.user_id), name))


collect = on_regex(_pat("收取丹药") + r"\s*$", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@collect.handle()
async def _(event: MessageEvent):
    await _need_group(event, collect)
    await collect.finish(await commands.cmd_mix_collect(store, str(event.user_id)))


useelixir = on_regex(_pat("用丹") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@useelixir.handle()
async def _(event: MessageEvent):
    await _need_group(event, useelixir)
    m = re.search(r"用丹\s*(.*)", _plain(event))
    name = (m.group(1) if m else "").strip()
    await useelixir.finish(await commands.cmd_use_elixir(store, str(event.user_id), name))


# ---------------------------------------------------------------------------
# 对抗：切磋 / 抢 / 偷 / 送
# ---------------------------------------------------------------------------
pk = on_regex(_pat("切磋"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@pk.handle()
async def _(event: MessageEvent):
    await _need_group(event, pk)
    await pk.finish(await commands.cmd_pvp(store, str(event.user_id), _target(event)))


rob = on_regex(_pat("抢灵石"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@rob.handle()
async def _(event: MessageEvent):
    await _need_group(event, rob)
    await rob.finish(await commands.cmd_rob(store, str(event.user_id), _target(event)))


steal = on_regex(_pat("偷灵石"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@steal.handle()
async def _(event: MessageEvent):
    await _need_group(event, steal)
    await steal.finish(await commands.cmd_steal(store, str(event.user_id), _target(event)))


give = on_regex(_pat("送灵石"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@give.handle()
async def _(event: MessageEvent):
    await _need_group(event, give)
    m = re.search(r"(\d+)", _plain(event))
    amount = int(m.group(1)) if m else None
    await give.finish(await commands.cmd_give(store, str(event.user_id), _target(event), amount))


# ---------------------------------------------------------------------------
# 排行榜
# ---------------------------------------------------------------------------
rank = on_regex(_pat("排行榜"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@rank.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, rank)
    await rank.finish(await commands.cmd_rank(store, gid))


# ---------------------------------------------------------------------------
# 宗门
# ---------------------------------------------------------------------------
mksect = on_regex(_pat("创建宗门") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@mksect.handle()
async def _(event: MessageEvent):
    await _need_group(event, mksect)
    await mksect.finish(await commands.cmd_create_sect(store, str(event.user_id), _arg(event, "创建宗门")))


joinsect = on_regex(_pat("加入宗门") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@joinsect.handle()
async def _(event: MessageEvent):
    await _need_group(event, joinsect)
    await joinsect.finish(await commands.cmd_join_sect(store, str(event.user_id), _arg(event, "加入宗门")))


secinfo = on_regex(_pat("宗门") + r"\s*$", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@secinfo.handle()
async def _(event: MessageEvent):
    await _need_group(event, secinfo)
    await secinfo.finish(await commands.cmd_sect_info(store, str(event.user_id)))


sectwar = on_regex(_pat("宗门战") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("xiuxian"))


@sectwar.handle()
async def _(event: MessageEvent):
    await _need_group(event, sectwar)
    await sectwar.finish(await commands.cmd_sect_war(store, str(event.user_id), _arg(event, "宗门战")))


# ---------------------------------------------------------------------------
# 世界Boss
# ---------------------------------------------------------------------------
wb = on_regex(_pat("世界[Bb]oss"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@wb.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, wb)
    await wb.finish(await commands.cmd_boss(store, gid))


atk = on_regex(_pat("讨伐[Bb]oss"), priority=PRI, block=True, rule=_mode_is("xiuxian"))


@atk.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, atk)
    await atk.finish(await commands.cmd_hit_boss(store, str(event.user_id), gid))




# 口令关键字登记（供 game_router 兜底探测）
register_keywords("xiuxian", [
    "我要修仙",
    "我的修仙",
    "修仙菜单",
    "修仙口令",
    "帮助",
    "玩法",
    "攻略",
    "规则",
    "闭关",
    "出关",
    "突破",
    "领取机缘",
    "秘境",
    "开启秘境",
    "悬赏",
    "接悬赏",
    "交悬赏",
    "商店",
    "坊市",
    "买",
    "炼丹",
    "收取丹药",
    "用丹",
    "切磋",
    "抢灵石",
    "偷灵石",
    "送灵石",
    "排行榜",
    "创建宗门",
    "加入宗门",
    "宗门",
    "宗门战",
    "世界Boss",
    "讨伐boss",
])
