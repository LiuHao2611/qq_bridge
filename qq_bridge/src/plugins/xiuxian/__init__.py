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

import asyncio
import random
import re
import time

import nonebot
from nonebot import on_regex, get_driver
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger

from . import card
from . import config as cfg
from . import commands
from . import core
from . import store as store_mod

from src.group_switch import group_enabled

store = store_mod.Store()

# priority=5（高于聊天处理器的 10） + block=True（命中即阻断 LLM 闲聊）
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
    gid = await _need_group(event, reg)
    qq = str(event.user_id)
    first = await store.get_player(qq) is None
    msg = await commands.cmd_enter(store, qq, _name(event), gid)
    await _finish_status(reg, event, msg, tip=TIP_FIRST if first else None)


status = on_regex(_pat("我的修仙"), priority=PRI, block=True)


@status.handle()
async def _(event: MessageEvent):
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
    await _need_group(event, menu)
    await _finish_card(menu, event, card.render_menu, commands.cmd_menu(), "修仙菜单卡")


helper = on_regex(_pat("修仙(?:帮助|玩法|攻略)"), priority=PRI, block=True)


@helper.handle()
async def _(event: MessageEvent):
    await _need_group(event, helper)
    await _finish_card(helper, event, card.render_help, commands.cmd_help(), "修仙玩法卡")


# ---------------------------------------------------------------------------
# 闭关 / 出关 / 突破
# ---------------------------------------------------------------------------
secl = on_regex(_pat("闭关"), priority=PRI, block=True)


@secl.handle()
async def _(event: MessageEvent):
    await _need_group(event, secl)
    await secl.finish(await commands.cmd_seclude(store, str(event.user_id)))


unsecl = on_regex(_pat("出关"), priority=PRI, block=True)


@unsecl.handle()
async def _(event: MessageEvent):
    await _need_group(event, unsecl)
    await unsecl.finish(await commands.cmd_unseclude(store, str(event.user_id)))


bt = on_regex(_pat("突破"), priority=PRI, block=True)


@bt.handle()
async def _(event: MessageEvent):
    await _need_group(event, bt)
    await bt.finish(await commands.cmd_breakthrough(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 机缘 / 秘境
# ---------------------------------------------------------------------------
claim = on_regex(_pat("领取机缘"), priority=PRI, block=True)


@claim.handle()
async def _(event: MessageEvent):
    await _need_group(event, claim)
    await claim.finish(await commands.cmd_claim(store, str(event.user_id)))


exp = on_regex(_pat("秘境"), priority=PRI, block=True)


@exp.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, exp)
    await exp.finish(await commands.cmd_explore(store, str(event.user_id), gid))


# 仅主人：立即开启一场限时秘境（复核在 commands.cmd_open_secret 内）
openSec = on_regex(_pat("开启秘境") + r"\s*$", priority=PRI, block=True)


@openSec.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, openSec)
    await openSec.finish(await commands.cmd_open_secret(store, gid, str(event.user_id)))


# ---------------------------------------------------------------------------
# 悬赏：看榜 / 接取 / 结算（数据搬自开源 悬赏令.json，见 config_open）
# ---------------------------------------------------------------------------
bounty = on_regex(_pat("悬赏") + r"\s*$", priority=PRI, block=True)


@bounty.handle()
async def _(event: MessageEvent):
    await _need_group(event, bounty)
    await bounty.finish(await commands.cmd_bounty(store, str(event.user_id)))


takebounty = on_regex(_pat("接悬赏") + r"\s*(.*)", priority=PRI, block=True)


@takebounty.handle()
async def _(event: MessageEvent):
    await _need_group(event, takebounty)
    # 参数从纯文本里取（CQ 码已在 _pat 前缀里被吃掉，这里不能再拿 CQ 串去正则）
    m = re.search(r"接悬赏\s*(.*)", _plain(event))
    name = (m.group(1) if m else "").strip()
    await takebounty.finish(await commands.cmd_take_bounty(store, str(event.user_id), name))


finbounty = on_regex(_pat("交悬赏") + r"\s*$", priority=PRI, block=True)


@finbounty.handle()
async def _(event: MessageEvent):
    await _need_group(event, finbounty)
    await finbounty.finish(await commands.cmd_finish_bounty(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 商店 / 炼丹 / 用丹（数据搬自开源 炼丹丹药.json / 药材.json / 炼丹炉.json）
# ---------------------------------------------------------------------------
shop = on_regex(_pat("商店|坊市") + r"\s*$", priority=PRI, block=True)


@shop.handle()
async def _(event: MessageEvent):
    await _need_group(event, shop)
    await shop.finish(await commands.cmd_shop(store, str(event.user_id)))


buy = on_regex(_pat("买") + r"\s*(.+)", priority=PRI, block=True)


@buy.handle()
async def _(event: MessageEvent):
    await _need_group(event, buy)
    m = re.search(r"买\s*(.*)", _plain(event))
    key = (m.group(1) if m else "").strip()
    await buy.finish(await commands.cmd_buy(store, str(event.user_id), key))


mix = on_regex(_pat("炼丹") + r"\s*(.*)", priority=PRI, block=True)


@mix.handle()
async def _(event: MessageEvent):
    await _need_group(event, mix)
    m = re.search(r"炼丹\s*(.*)", _plain(event))
    name = (m.group(1) if m else "").strip()
    await mix.finish(await commands.cmd_mix(store, str(event.user_id), name))


collect = on_regex(_pat("收取丹药") + r"\s*$", priority=PRI, block=True)


@collect.handle()
async def _(event: MessageEvent):
    await _need_group(event, collect)
    await collect.finish(await commands.cmd_mix_collect(store, str(event.user_id)))


useelixir = on_regex(_pat("用丹") + r"\s*(.*)", priority=PRI, block=True)


@useelixir.handle()
async def _(event: MessageEvent):
    await _need_group(event, useelixir)
    m = re.search(r"用丹\s*(.*)", _plain(event))
    name = (m.group(1) if m else "").strip()
    await useelixir.finish(await commands.cmd_use_elixir(store, str(event.user_id), name))


# ---------------------------------------------------------------------------
# 对抗：切磋 / 抢 / 偷 / 送
# ---------------------------------------------------------------------------
pk = on_regex(_pat("切磋"), priority=PRI, block=True)


@pk.handle()
async def _(event: MessageEvent):
    await _need_group(event, pk)
    await pk.finish(await commands.cmd_pvp(store, str(event.user_id), _target(event)))


rob = on_regex(_pat("抢灵石"), priority=PRI, block=True)


@rob.handle()
async def _(event: MessageEvent):
    await _need_group(event, rob)
    await rob.finish(await commands.cmd_rob(store, str(event.user_id), _target(event)))


steal = on_regex(_pat("偷灵石"), priority=PRI, block=True)


@steal.handle()
async def _(event: MessageEvent):
    await _need_group(event, steal)
    await steal.finish(await commands.cmd_steal(store, str(event.user_id), _target(event)))


give = on_regex(_pat("送灵石"), priority=PRI, block=True)


@give.handle()
async def _(event: MessageEvent):
    await _need_group(event, give)
    m = re.search(r"(\d+)", _plain(event))
    amount = int(m.group(1)) if m else None
    await give.finish(await commands.cmd_give(store, str(event.user_id), _target(event), amount))


# ---------------------------------------------------------------------------
# 排行榜
# ---------------------------------------------------------------------------
rank = on_regex(_pat("排行榜"), priority=PRI, block=True)


@rank.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, rank)
    await rank.finish(await commands.cmd_rank(store, gid))


# ---------------------------------------------------------------------------
# 宗门
# ---------------------------------------------------------------------------
mksect = on_regex(_pat("创建宗门") + r"\s*(.*)", priority=PRI, block=True)


@mksect.handle()
async def _(event: MessageEvent):
    await _need_group(event, mksect)
    await mksect.finish(await commands.cmd_create_sect(store, str(event.user_id), _arg(event, "创建宗门")))


joinsect = on_regex(_pat("加入宗门") + r"\s*(.*)", priority=PRI, block=True)


@joinsect.handle()
async def _(event: MessageEvent):
    await _need_group(event, joinsect)
    await joinsect.finish(await commands.cmd_join_sect(store, str(event.user_id), _arg(event, "加入宗门")))


secinfo = on_regex(_pat("宗门") + r"\s*$", priority=PRI, block=True)


@secinfo.handle()
async def _(event: MessageEvent):
    await _need_group(event, secinfo)
    await secinfo.finish(await commands.cmd_sect_info(store, str(event.user_id)))


sectwar = on_regex(_pat("宗门战") + r"\s*(.*)", priority=PRI, block=True)


@sectwar.handle()
async def _(event: MessageEvent):
    await _need_group(event, sectwar)
    await sectwar.finish(await commands.cmd_sect_war(store, str(event.user_id), _arg(event, "宗门战")))


# ---------------------------------------------------------------------------
# 世界Boss
# ---------------------------------------------------------------------------
wb = on_regex(_pat("世界[Bb]oss"), priority=PRI, block=True)


@wb.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, wb)
    await wb.finish(await commands.cmd_boss(store, gid))


atk = on_regex(_pat("讨伐[Bb]oss"), priority=PRI, block=True)


@atk.handle()
async def _(event: MessageEvent):
    gid = await _need_group(event, atk)
    await atk.finish(await commands.cmd_hit_boss(store, str(event.user_id), gid))


# ---------------------------------------------------------------------------
# 后台随机事件循环（群活跃引擎）
# ---------------------------------------------------------------------------
# 全局广播频率护栏：防止后台循环（秘境/奇遇/Boss）向同一群刷屏，
# 触发腾讯社交风控。宁可静默丢弃一条非关键广播，也绝不灌屏。
_BC_GAP_SEC = 8          # 同一群两条广播的最小间隔（秒）
_BC_MAX_PER_MIN = 6      # 同一群每分钟最多广播条数（安全网）
_bc_log: dict[str, list[float]] = {}   # gid -> 近 60s 内的发送时间戳


def _bc_allowed(gid: str) -> bool:
    now = time.time()
    hist = _bc_log.get(gid)
    if not hist:
        return True
    if now - hist[-1] < _BC_GAP_SEC:          # 间隔太密
        return False
    recent = [t for t in hist if now - t < 60]
    if len(recent) >= _BC_MAX_PER_MIN:        # 每分钟超限
        return False
    return True


async def _broadcast(gid: str, msg: str):
    # 群聊总闸（GROUP_ENABLED=false）下的第一道关。
    # 这里必须拦，因为这是**出站**：入站闸只让群消息进不来，拦不住我们主动发。
    # 2026-10-29 就是漏了这里，世界 Boss / 秘境 / 奇遇 照样往两个群里广播。
    if not group_enabled():
        logger.debug(f"[xiuxian] 群聊已关闭，广播丢弃 gid={gid}")
        return
    try:
        if not _bc_allowed(gid):
            # 命中护栏：静默丢弃，避免刷屏触发风控
            logger.debug(f"[xiuxian] 广播被频率护栏丢弃 gid={gid}")
            return
        bots = list(get_driver().bots.values())
        if bots:
            await bots[0].send_group_msg(group_id=int(gid), message=msg)
            now = time.time()
            hist = _bc_log.setdefault(gid, [])
            hist.append(now)
            _bc_log[gid] = [t for t in hist if now - t < 60]  # 仅保留 60s 内
    except Exception:  # noqa: BLE001 — 广播失败不该拖垮循环
        pass


async def _event_loop():
    await asyncio.sleep(8)
    while True:
        await asyncio.sleep(random.randint(cfg.EVENT_INTERVAL_MIN, cfg.EVENT_INTERVAL_MAX))
        try:
            groups = await store.enabled_groups()
            if not groups:
                continue
            g = random.choice(groups)
            ev = core.roll_world_event()
            world = await store.get_world(g)
            if ev == "boss":
                players = await store.all_players(g)
                world["boss"] = core.spawn_boss(max(1, len(players)))
                msg = (f"⚠️【世界Boss 降临】气血 {world['boss']['max_hp']}！"
                       f"发送「讨伐boss」出力，击杀瓜分 {cfg.BOSS_REWARD_POOL} 灵石！")
            elif ev == "secret":
                cur = world.get("secret")
                if cur and core.secret_is_open(cur):
                    continue  # 已有开启中的限时秘境，不重复开
                world["secret"] = core.secret_new()
                mm = cfg.SECRET_DURATION_SEC // 60
                msg = (f"🌟【限时秘境开启】限时 {mm} 分钟！\n"
                       "发送一次「秘境」即可入内、自动探索；途中机缘按倒计时自动触发，"
                       "结束自动播报总览。")
            else:
                msg = "🍀【奇遇】" + random.choice(cfg.QIYU_POOL) + " 速去「闭关」领悟！"
            await store.set_world(g, world)
            await _broadcast(g, msg)
        except Exception:  # noqa: BLE001 — 单次事件异常重启循环
            pass


async def _secret_tick():
    """限时秘境自动探索引擎：按倒计时触发随机事件；到时关闭并播报总览。

    这是"报名后自动探索"的核心——玩家无需再发口令，事件由本循环驱动：
      * 每个已报名探索者按 next_event_at 到点触发一次随机事件（真实加灵石/修为/道具）；
      * 秘境 ends_at 到点 → 置 open=False，向群里播报 secret_summary_text 总览。
    """
    await asyncio.sleep(6)
    while True:
        await asyncio.sleep(cfg.SECRET_TICK_SEC)
        try:
            now_ts = time.time()
            for g in await store.enabled_groups():
                world = await store.get_world(g)
                secret = world.get("secret")
                if not secret or not secret.get("open"):
                    continue
                try:
                    ends_at = float(secret.get("ends_at", 0) or 0)
                except (TypeError, ValueError):
                    ends_at = 0.0
                changed = False
                for qq, part in list((secret.get("participants") or {}).items()):
                    guard = 0
                    while part.get("next_event_at", 0) <= now_ts and now_ts < ends_at and guard < 30:
                        pl = await store.get_player(qq)
                        if not pl:
                            break
                        core.secret_roll_event(pl, part, now_ts)
                        await store.put_player(pl)
                        changed = True
                        guard += 1
                if now_ts >= ends_at:
                    secret["open"] = False
                    await store.set_world(g, world)
                    await _broadcast(g, core.secret_summary_text(secret))
                elif changed:
                    await store.set_world(g, world)
        except Exception:  # noqa: BLE001 — 巡检异常不得拖垮循环
            pass


@get_driver().on_startup
async def _startup():
    await store.load()
    # 世界事件循环的唯一职责就是「往群里炸一条」，群聊关了它就没有存在意义，
    # 直接不启动（不是启动了再丢弃）：少一条循环就少一份误发风险，
    # 也省得它继续改 world 状态、留下半截开启中的秘境。
    if group_enabled():
        asyncio.create_task(_event_loop())
    else:
        logger.info("[xiuxian] 群聊已关闭（GROUP_ENABLED=false），世界事件循环不启动")
    # 秘境巡检保留：它同时负责秘境到点关闭与收获结算，
    # 停了会让「开着但没人能进」的秘境永远关不掉。播报由 _broadcast 自己拦。
    asyncio.create_task(_secret_tick())
