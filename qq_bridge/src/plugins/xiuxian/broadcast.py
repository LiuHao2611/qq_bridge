# -*- coding: utf-8 -*-
"""修仙「后台随机事件循环（群活跃引擎）」子系统。

从 xiuxian/__init__.py 抽出为独立模块，以满足 20KB 代码文件上限门禁。
本模块负责：世界Boss / 秘境 / 奇遇的定时群广播、限时秘境自动探索引擎，
以及启动期的 on_startup 注册。

⚠️ 2026-11-09 事故修复（见 _broadcast_enabled）：
GROUP_ENABLED 原本被本模块的 _event_loop / _broadcast 直接复用，导致「开启群聊」
顺带把修仙后台「炸群」（定时往群里发）也放了出来，触发腾讯风控把机器人 QQ 封禁。
现已把「群聊（响应 @）」与「修仙群广播」解耦——后者必须显式
XIIXIAN_BROADCAST_ENABLE=true 才开，默认关闭（安全态）。

注意：store 实例在包 __init__ 中创建并共享；本模块仅在函数体内惰性
`from . import store`，避免包加载期的循环依赖。
"""

from __future__ import annotations

import asyncio
import os
import random
import time

import nonebot
from nonebot import get_driver
from nonebot.log import logger

from . import config as cfg
from . import core
from src.group_switch import group_enabled


def _broadcast_enabled() -> bool:
    """修仙「群广播」独立总闸（2026-11-09 事故修复）。

    GROUP_ENABLED 原本被 xiuxian 的 _event_loop / _broadcast 直接复用，导致
    「开启群聊」顺带把修仙后台「炸群」（世界Boss / 秘境 / 奇遇定时往群里发）也放了出来，
    触发腾讯风控把机器人 QQ 封禁。

    本函数把「群聊（响应 @）」与「修仙群广播」解耦：前者由 GROUP_ENABLED 控制；
    后者必须显式 XIIXIAN_BROADCAST_ENABLE=true 才开，默认关闭（安全态）。
    """
    return os.environ.get("XIIXIAN_BROADCAST_ENABLE", "false").strip().lower() in (
        "1", "true", "yes", "on", "y", "是",
    )


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
    if not group_enabled() or not _broadcast_enabled():
        logger.debug(f"[xiuxian] 群聊/修仙广播未启用，广播丢弃 gid={gid} "
                     f"(group_enabled={group_enabled()} broadcast={_broadcast_enabled()})")
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
            # store 实例在包 __init__ 中创建并共享；此处惰性导入避免循环依赖。
            from . import store
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
            # store 实例在包 __init__ 中创建并共享；此处惰性导入避免循环依赖。
            from . import store
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
    # store 实例在包 __init__ 中创建并共享；此处惰性导入避免循环依赖。
    from . import store
    await store.load()
    # 世界事件循环的唯一职责就是「往群里炸一条」，群聊关了它就没有存在意义，
    # 直接不启动（不是启动了再丢弃）：少一条循环就少一份误发风险，
    # 也省得它继续改 world 状态、留下半截开启中的秘境。
    # 世界事件循环（炸群）需要「群聊响应」+「修仙广播」双闸都开才启动；
    # 任一关即不启动 —— 即使 GROUP_ENABLED=true，只要 XIIXIAN_BROADCAST_ENABLE=false
    # 就不会有任何自动群广播（2026-11-09 事故修复）。
    if group_enabled() and _broadcast_enabled():
        asyncio.create_task(_event_loop())
    else:
        logger.info("[xiuxian] 修仙群广播未启用（group_enabled=%s broadcast=%s），"
                    "世界事件循环不启动" % (group_enabled(), _broadcast_enabled()))
    # 秘境巡检保留：它同时负责秘境到点关闭与收获结算，
    # 停了会让「开着但没人能进」的秘境永远关不掉。播报由 _broadcast 自己拦。
    asyncio.create_task(_secret_tick())
