# -*- coding: utf-8 -*-
"""qq_bridge 主插件：串联 connector / dispatcher / safety / ratelimit /
intent / taskmgr / media / session 八个模块（设计文档 §3）。

链路（文档附录 B）：
  QQ → NapCat →(onebot)→ dispatcher →(normalized)→ safety → ratelimit
     → intent →(route)→ taskmgr →(HTTP 7788)→ media →(回传 CQ 码)→ 用户

铁律：
  * 能力层只经 HTTP 调主项目 7788，**不 import 任何 server/ 模块**；
  * 失败必须有明确回传与日志（CLOSE-02 禁止静默吞异常）；
  * 任务失败退还当日配额占用（CLOSE-04 失败路径必退）。
"""
from __future__ import annotations

import os
print(f"[qq_bridge] module loaded: __init__.py mtime={os.path.getmtime(__file__)}", flush=True)

import asyncio
import base64
import json
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx

from nonebot import get_driver, on_message, on_notice
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupIncreaseNoticeEvent

# QQ 官方通道类型（可选依赖，双轨并行用）。
# 必须是**具体类型**而不是 Any：NoneBot 依赖注入靠参数注解挑 handler，
# 写 Any 会直接 ValueError("Unknown parameter bot")（2026-09-12 实测踩坑）。
# 适配器缺失时置 None → 不注册官方 matcher → NapCat 完全不受影响。
try:  # noqa: SIM105
    from nonebot.adapters.qq import Bot as QQBot  # noqa: E402
    from nonebot.adapters.qq import Event as QQEvent  # noqa: E402
except Exception:  # pragma: no cover — 未安装适配器时的降级路径
    QQBot = None  # type: ignore[assignment,misc]
    QQEvent = None  # type: ignore[assignment,misc]
from nonebot.matcher import Matcher

# 每日与官方同步 Warframe 静态数据（武器/战甲/倾向倍率），见 wf_sync.py。
from . import wf_sync  # noqa: E402

# 2026-09-07：Request **必须在模块顶层导入**。本文件顶部有
# `from __future__ import annotations`，函数注解会被延迟成字符串，FastAPI 解析
# "req: Request" 时只认模块命名空间里的名字；若只在路由函数内部 import，
# FastAPI 找不到 Request 就会把它当成"必需的查询参数"，请求一律 422。
# （该坑同样影响 /api/qq/config 的 PUT——保存配置会一直 422，一并修复。）
from fastapi import Request  # noqa: E402
# JSONResponse 必须从 fastapi.responses 导入：部分 fastapi 版本不在顶层导出它，
# 写成 `from fastapi import Request, JSONResponse` 会直接 ImportError 导致插件加载失败。
from fastapi.responses import JSONResponse, FileResponse  # noqa: E402

from .config import Config, get_logger, jlog, new_trace_id
from .connector import connector
from .dispatcher import allow, normalize
from . import history  # 包本身（history/ 目录下是一组函数，无单例对象）
from .intent import HELP_TEXT, match_keyword, _translate_maid
from .intent import all_intents as _intents_all
from .intent import all_intents as _intents_all
from .intent import route as route_intent
from .intent import validate_prompt, truncate_prompt
from .media import (send_image, send_images, send_pic, send_record, send_text, send_video,
                     send_video_link, send_file)
from .ratelimit import rate_limiter
from .safety import safety
from .session import session_store, choice_sessions
from .taskmgr import task_mgr
# 任务中心（2026-09-08）：QQ 侧持久有界队列，主项目后端零改动
from .queue import queue_mgr
from .planstore import plan_mgr
from .monitor import monitor_mgr
# 监听主项目生图任务（2026-09-13）：从推理监控页 /tasks 获取，QQ 直接看后台在画啥
from .monitor import infer_listener
# 历史画廊（2026-09-11）：群里翻历史缩略图 + 回复编号看原图
from . import gallery
# AI 陪聊模式（2026-10-19）：/ai on 后群里所有消息走在线大模型（OpenAI 兼容），
# 默认纯文字回复；文字游戏带每次对话倒计时 + 超时 AI 催更/收局。
from .ai_mode import (AI_GAMES, ai_chatter, ai_mode_store,  # noqa: F401
                      calculate_typing_time, event_scope, game_sessions,
                      parse_sticker_marker, process_llm_response)
# AI 主动发言（2026-10-19）：群里安静下来自己起话头/跟进话题 + 早午晚安
from .ai_proactive import compute_activity, proactive_engine, scope_of
# M2 工具总线（2026-10-20）：Function Calling（registry/llm/fc）+ 提醒定时任务
# （scheduler）+ 首批工具（builtins）。tools 包不 import 主包，无循环 import 风险。
from .tools import (ReminderError, ReminderParser,  # noqa: F401
                    default_registry, register_builtin_tools,
                    reminder_scheduler, reminder_store)
# M7 下载媒体中心（2026-10-20）：aria2 RPC 桥 + BT 智能选 + yt-dlp + 订阅监控。
# download 包同样不 import 主包，通知/发送经回调注入（on_complete/on_new）。
from .download import (Aria2Error, DownloadError, DownloadManager,  # noqa: F401
                       SubscriptionError, YtDlpError, classify,
                       download_manager, subscription_monitor, subscription_store)
# M9 运维面与通知中枢（2026-10-20）：git 状态 / SSH 白名单执行 / WoL / frp 状态 +
# 通知中枢（事件统一人格化转述）。ops 包同款零主包依赖，回调注入。
from .ops import (FrpError, GitInfo, GitInfoError,  # noqa: F401
                  NotificationHub, NotifyEvent, RepoWatchdog, SshError,
                  SshRunner, WolError, WolSender, frp_status, git_info,
                  notify_hub, repo_watchdog, ssh_runner, wol_sender)
# M1 记忆系统（2026-10-20）：三态记忆（长期 events/核心画像+群档案）+ LLM 抽取。
# memory 包同款零主包依赖（仅延迟 import tools.llm）。
from .memory import (ExtractError, MemoryError, MemoryManager,  # noqa: F401
                     MemoryStore, memory_manager, memory_store)
# M3 情感 + 日程 + 关系（2026-10-20）：二维情感（MaiBot valence-arousal）
# + 好感度/关系阶段 + 日程（到期关心）。emotion 包同款零主包依赖。
from .emotion import (EmotionError, EmotionManager, EmotionStore,  # noqa: F401
                      ScheduleError, emotion_manager, emotion_store)
# M4 多模态 + 语音（2026-10-20）：图理解/视频抽帧/历史图召回 + ASR/TTS。
# 独立成包不挂 media/ 下（media 是发送层且 import nonebot）。
from .multimodal import (AlbumError, AudioError, MediaManager,  # noqa: F401
                         VisionError, media_album, media_manager)
# 智能表情包（2026-10-19）：自动收集 + OCR/VLM 分类 + 人设语境选用
from .stickers import (DEFAULT_PERSONA_MAP, STICKER_CATS,  # noqa: F401
                       collect_group_stickers, parse_persona_map, sticker_lib)
# 宿主机管家（2026-10-20 M6）：/pc 命令族 + 高危确认流；**仅私聊+主人**（roadmap 10.1）
from . import pc as pc_mod
# 自绘图片（2026-09-07）：pic 包只依赖 Pillow，不 import nonebot，可在插件上下文安全加载
from . import pic as pic_mod
from .pic import PicRenderError  # noqa: F401  （对外暴露，便于调用方捕获降级）
from .pic.templates import progress as progress_tpl

log = get_logger()

# 注意：本模块**不在顶层调用 get_driver()/on_message()**。
# bot.py 需要先 `from src.plugins.qq_bridge.config import setup_logging`，
# 而 import 任何一个子模块都会先执行本包的 __init__；若此时 nonebot.init() 还没跑，
# 顶层的 get_driver() 会抛 "NoneBot has not been initialized."。
# 因此注册动作收敛到 register()，由 bot.py 在 nonebot.init() 之后显式调用。
msg_matcher: Matcher | None = None
notice_matcher: Matcher | None = None
#: QQ 官方通道独立 matcher（双轨并行：与 NapCat 的 matcher 互不干扰）
official_matcher: Matcher | None = None
_registered = False


async def _on_connect(bot: Bot) -> None:
    connector.attach_bot(bot)


async def _on_disconnect(bot: Bot) -> None:
    connector.detach_bot(bot)


def register() -> Matcher:
    """注册生命周期钩子与消息匹配器。幂等，可重复调用。"""
    global msg_matcher, notice_matcher, official_matcher, _registered
    if _registered and msg_matcher is not None:
        return msg_matcher

    driver = get_driver()
    driver.on_bot_connect(_on_connect)
    driver.on_bot_disconnect(_on_disconnect)
    # 任务队列：启动时拉起消费者，关闭时优雅停下（bridge 崩/停都不影响主项目）
    driver.on_startup(_on_startup)
    driver.on_shutdown(_on_shutdown)

    _load_stickers()

    _register_config_routes()
    _register_queue_routes()
    _register_history_routes()
    # 官方机器人独立接口命名空间（两个机器人、两个页面）
    _register_official_routes()
    # 2026-09-07：自定义主题编辑器（HTML 页面 + REST + 实时预览）
    try:
        from .pic.editor import register as _register_theme_editor
        from nonebot import get_app
        _register_theme_editor(get_app())
        log.info(jlog("theme_editor_registered", path="/pic/theme-editor"))
    except Exception as e:  # 编辑器挂载失败绝不应拖垮消息处理
        log.error(jlog("theme_editor_register_failed", error=str(e)))

    msg_matcher = on_message(priority=10, block=False)
    msg_matcher.handle()(_timed_handle_message)

    # QQ 官方通道：**独立 matcher + 官方类型注解**（不能用 Any，见文件头说明）。
    # 只在「适配器已安装 且 AppID/Secret 配齐」时注册：没配就完全不注册，
    # NapCat 链路一行都不受影响（双轨并行的关键）。
    if QQBot is not None and Config.official_configured():
        try:
            official_matcher = on_message(priority=10, block=False)
            official_matcher.handle()(_handle_official_message)
            log.info(jlog("official_matcher_registered", sandbox=Config.OFFICIAL_SANDBOX))
        except Exception as e:  # noqa: BLE001 — 注册失败绝不能拖垮 NapCat
            official_matcher = None
            log.error(jlog(
                "official_matcher_register_failed",
                error=type(e).__name__, detail=str(e)[:200],
            ))

    # 进群自动发言：机器人自己被拉进群时发一句腹黑阴阳怪气的开场白
    notice_matcher = on_notice(priority=10, block=False)
    notice_matcher.handle()(_on_group_increase)

    _registered = True

    # 视频链接自动解析（独立 matcher，不 block，异常自吞，绝不拖垮消息处理）
    try:
        from .video_parse import register as _vp_register
        _vp_register()
    except Exception as e:  # noqa: BLE001
        log.error(jlog("video_parse_register_failed", error=str(e)[:200]))

    # 游戏工具箱：二游查询（角色/配队/养成/副本）+ 群聊小游戏（# 前缀命令）
    try:
        from .game_tools import register as _gt_register
        _gt_register()
    except Exception as e:  # noqa: BLE001
        log.error(jlog("game_tools_register_failed", error=str(e)[:200]))

    # M2 工具总线（2026-10-20）：注册首批 FC 工具（remind_*/queue_status/
    # pc_overview）。失败只记日志——纯对话 AI 模式照常工作（注册失败 ≠ 假注册，
    # FC 路径会因 enabled_tools() 为空而自动走纯对话路由，见 _handle_ai_chat）。
    try:
        _register_m2_tools()
    except Exception as e:  # noqa: BLE001
        log.error(jlog("m2_tools_register_failed", error=type(e).__name__,
                       detail=str(e)[:200]))

    log.info(jlog("plugin_registered", channel=Config.IMAGE_CHANNEL, api_base=Config.MANJU_API_BASE))
    return msg_matcher


def _official_message_text(event: Any) -> str:
    """提取官方消息事件里的纯文本。

    官方事件的 @ 段不落在文本里（适配器已剔除），因此只需拼接 text 段。
    提取失败返回空串——宁可少回一句，也不要让异常冒到事件循环（CLOSE-02）。
    """
    try:
        msg = event.get_message()
        return "".join(
            str(seg.data.get("text", "")) for seg in msg if seg.type == "text"
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


async def _handle_official_message(bot: QQBot, event: QQEvent) -> None:  # type: ignore[valid-type]
    """QQ 官方通道消息入口（P3：已接入完整业务链路）。

    归一化成业务层契约对象 ``QQEvent`` 后直接复用 ``_route`` —— 与 NapCat 共用
    意图路由 / 关键词门禁 / 限流 / 队列 / 配额 / 会话，业务规则零重复。

    协议层客观差异（不是业务差异，运维需知悉）：
      * 官方通道**拿不到用户 QQ 号**，只有 openid → 用伪负数 ID
        （见 ``channel.official.pseudo_id``，负数保证不与真实 QQ 号串号）；
      * 因此按 QQ 号配置的白/黑名单对官方通道**无效**，STRICT_WHITELIST 需保持 false，
        访问控制依赖官方平台的「服务范围」与群主邀请；
      * 历史落盘暂不记录：现有 ``history.record_incoming`` 依赖 OneBot 的
        message_type 字段，官方事件没有该字段（改 channel-aware 属 P4 范围）。
    """
    from .channel import OFFICIAL, get_channel

    trace_id = new_trace_id()
    ch = get_channel(OFFICIAL)
    try:
        ev = await ch.to_legacy_event(
            event, self_id=str(getattr(bot, "self_id", "") or ""), trace_id=trace_id
        )
    except Exception as e:  # noqa: BLE001 — 归一化失败只记日志，绝不影响 NapCat
        log.error(jlog("official_normalize_failed", error=type(e).__name__,
                       detail=str(e)[:200], trace_id=trace_id))
        return
    if ev is None:
        return

    log.info(jlog(
        "official_message_in",
        trace_id=trace_id,
        user_id=ev.user_id,
        is_group=bool(ev.group_id),
        at_bot=ev.at_bot,
        text_len=len(ev.raw_text or ""),
    ))

    try:
        await _route(ev, trace_id)
    except Exception as e:  # noqa: BLE001 — CLOSE-02：记全，不静默吞
        log.error(jlog("official_route_failed", error=type(e).__name__,
                       detail=str(e)[:200], trace_id=trace_id))


async def _timed_handle_message(bot: Bot, event: MessageEvent) -> None:
    """handle_message 的 60s 超时包装（用户要求 2026-09-11）：单条消息处理超时被兜底提示，
    不再让用户只看到「没反应」却永远等不到回执（NapCat 抖动 / 模型卡住都能兜住）。

    注意：参数类型**必须**是具体的 OneBot 类型，不能写成 Any——NoneBot 依赖注入
    靠类型注解挑 handler（写 Any 会直接 ValueError: Unknown parameter bot）。
    官方通道走**另一个 matcher**（见 register() 里的 official_matcher），互不干扰。
    """
    try:
        await asyncio.wait_for(
            handle_message(bot, event),
            timeout=float(getattr(Config, "REPLY_TIMEOUT", 60.0)),
        )
    except asyncio.TimeoutError:
        log.warning(jlog("message_timeout", user_id=getattr(event, "user_id", "")))
        # 尽力给个提示；若 NapCat 此刻也超时则发不出，但事件循环不再被卡住
        try:
            ev = normalize(event, self_id=str(bot.self_id), trace_id=new_trace_id())
            if ev:
                await _reply(_ctx(ev), session_store.get(ev.user_id),
                             "本喵想了 60 秒还没搞定这条，喵再试一次？（超时保护兜底）",
                             title="超时")
        except Exception:
            pass


async def _monitor_alert(text: str) -> None:
    """监控告警出口：**私聊**给所有主人（不进群，避免打扰群友）。

    没人配主人 → 只记日志（不能因为没配主人就抛异常，CLOSE-02）。
    """
    try:
        owners = [int(str(x).strip()) for x in (Config.BOT_OWNER_QQ or ())
                  if str(x).strip().isdigit()]
    except Exception:
        owners = []
    if not owners:
        log.warning(jlog("monitor_alert_no_owner", detail="未配置 BOT_OWNER_QQ，告警无处可发"))
        return
    for uid in owners:
        try:
            await send_text({"user_id": uid, "group_id": 0}, "【监控告警】\n" + text)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("monitor_alert_send_failed", user_id=uid,
                             error=type(e).__name__, detail=str(e)[:160]))


async def _infer_alert(text: str, images=None) -> None:
    """监听主项目生图任务的通知出口：**私聊**所有主人（不进群，避免打扰群友）。

    images：可选的结果图 URL 列表（生图完成时补发，让用户直接看到后台生成的图）。
    没配主人 → 只记日志（CLOSE-02：不能因为没有主人就抛异常）。
    """
    try:
        owners = [int(str(x).strip()) for x in (Config.BOT_OWNER_QQ or ())
                  if str(x).strip().isdigit()]
    except Exception:
        owners = []
    if not owners:
        log.warning(jlog("infer_alert_no_owner", detail="未配置 BOT_OWNER_QQ，监听通知无处可发"))
        return
    for uid in owners:
        try:
            ctx = {"user_id": uid, "group_id": 0}
            if images:
                await send_text(ctx, "【主项目生图】\n" + text)
                await send_images(ctx, list(images))
            else:
                await send_text(ctx, "【主项目生图】\n" + text)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("infer_alert_send_failed", user_id=uid,
                             error=type(e).__name__, detail=str(e)[:160]))


async def _on_startup() -> None:
    """启动队列消费者 + 监控器，并绑定通知出口。

    失败只记日志：**绝不因为队列/监控起不来就拖垮 bridge**（CLOSE-02 记而不吞，
    REDUN-01 降级链——队列挂了用户仍可直发生图，只是不排队）。
    """
    try:
        queue_mgr.notifier = _queue_notifier
        await queue_mgr.start()
        log.info(jlog("queue_started", max=queue_mgr.max_size,
                      backoff=queue_mgr.busy_backoff, retry=queue_mgr.busy_retry))
    except Exception as e:  # noqa: BLE001
        log.error(jlog("queue_start_failed", error=type(e).__name__, detail=str(e)[:200]))
    try:
        monitor_mgr.notifier = _monitor_alert
        await monitor_mgr.start()
    except Exception as e:  # noqa: BLE001
        log.error(jlog("monitor_start_failed", error=type(e).__name__, detail=str(e)[:200]))
    # 监听主项目生图任务（2026-09-13）：从推理监控页 /tasks 获取，QQ 直接看后台在画啥
    try:
        infer_listener.notifier = _infer_alert
        await infer_listener.start()
    except Exception as e:  # noqa: BLE001
        log.error(jlog("infer_listen_start_failed", error=type(e).__name__, detail=str(e)[:200]))
    # 计划定时执行调度器（2026-09-13）：与队列一起起停
    try:
        _plan_scheduler_start()
    except Exception as e:  # noqa: BLE001
        log.error(jlog("plan_scheduler_start_failed", error=type(e).__name__, detail=str(e)[:200]))
    # 每日官方数据同步（2026-09-30）：启动即起后台每日循环，首次循环会在
    # 距上次同步 >24h 时自动拉取（长停机后上线即更新，无需手动）。
    try:
        wf_sync.start_sync_scheduler()
    except Exception as e:  # noqa: BLE001
        log.error(jlog("wf_sync_scheduler_start_failed", error=type(e).__name__, detail=str(e)[:200]))
    # AI 主动发言 tick 循环（2026-10-19）：冷场起话头 + 早午晚安，60s 一拍
    try:
        asyncio.ensure_future(_proactive_loop())
        log.info(jlog("proactive_loop_spawned"))
    except Exception as e:  # noqa: BLE001 — 起不来不影响其它功能
        log.error(jlog("proactive_loop_spawn_failed", error=type(e).__name__))
    # 提醒/定时任务调度器（2026-10-20 M2）：1s tick 扫到点提醒，经 _remind_fire
    # 回传到原会话。失败只记日志——用户设的提醒仍在库里，重启后恢复扫描。
    try:
        reminder_scheduler.on_fire = _remind_fire
        await reminder_scheduler.start()
        log.info(jlog("reminder_scheduler_spawned",
                      pending=reminder_store.pending_count("")))
    except Exception as e:  # noqa: BLE001
        log.error(jlog("reminder_scheduler_start_failed", error=type(e).__name__,
                       detail=str(e)[:200]))
    # 下载媒体中心（2026-10-20 M7）：aria2 完成轮询 + 订阅监控。aria2 RPC 未开
    # 不阻塞启动——用户下发 /dl 时会得到显式开启指引（探测在命令里做）。
    try:
        download_manager.on_complete = _dl_notify
        await download_manager.start()
        subscription_monitor.submit = _dl_sub_submit
        subscription_monitor.on_new = _dl_sub_notify
        if Config.DL_SUB_ENABLE:
            await subscription_monitor.start()
        log.info(jlog("download_manager_started",
                      subs=len(subscription_store.list("")) ))
    except Exception as e:  # noqa: BLE001
        log.error(jlog("download_manager_start_failed", error=type(e).__name__,
                       detail=str(e)[:200]))
    # 运维面通知中枢 + 仓库巡检（2026-10-20 M9）：事件 → 人格化转述 → 目标推送。
    # 转述借 ai_chatter.say 人设（say 以 user 身份送模型但不落历史）；发送走
    # _ops_notify_send（OPS_NOTIFY_TARGET）。目标未配 = 只记日志（hub 已挡）。
    try:
        notify_hub.rephrase = _ops_notify_rephrase
        notify_hub.send = _ops_notify_send
        repo_watchdog.on_event = notify_hub.push
        if Config.OPS_WATCH_ENABLE:
            await repo_watchdog.start()
        log.info(jlog("ops_notify_ready",
                      target=Config.OPS_NOTIFY_TARGET or "(未配)",
                      min_level=Config.OPS_NOTIFY_MIN_LEVEL))
    except Exception as e:  # noqa: BLE001
        log.error(jlog("ops_notify_start_failed", error=type(e).__name__,
                       detail=str(e)[:200]))
    # 情感到期关心循环（2026-10-20 M3）：日程到期 → 人设语气主动关心一句
    if Config.EMO_ENABLE and Config.EMO_SCHEDULE_EXTRACT:
        try:
            asyncio.ensure_future(_emotion_care_loop())
            log.info(jlog("emotion_care_loop_started"))
        except Exception as e:  # noqa: BLE001
            log.error(jlog("emotion_care_start_failed", error=type(e).__name__,
                           detail=str(e)[:200]))


async def _on_shutdown() -> None:
    """优雅停消费者与监控（队列已落盘，重启后恢复）。"""
    try:
        await _plan_scheduler_stop()
        log.info(jlog("plan_scheduler_stopped"))
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("plan_scheduler_stop_failed", error=type(e).__name__,
                         detail=str(e)[:160]))
    # 停提醒调度器（M2）：在跑的 tick 取消，库里的 pending 重启后恢复
    try:
        await reminder_scheduler.stop()
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("reminder_scheduler_stop_failed", error=type(e).__name__,
                         detail=str(e)[:160]))
    # 停下载轮询与订阅监控（M7）
    for name, stopper in (("download_loop", download_manager.stop),
                          ("subscription_monitor", subscription_monitor.stop)):
        try:
            await stopper()
            log.info(jlog("%s_stopped" % name))
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("%s_stop_failed" % name, error=type(e).__name__,
                             detail=str(e)[:160]))
    # 停仓库巡检（M9；通知中枢无循环，无需停）
    try:
        await repo_watchdog.stop()
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("repo_watchdog_stop_failed", error=type(e).__name__,
                         detail=str(e)[:160]))
    # 停每日官方数据同步循环
    try:
        wf_sync.stop_sync_scheduler()
    except Exception:  # noqa: BLE001
        pass
    for name, stopper in (("queue", queue_mgr.stop), ("monitor", monitor_mgr.stop),
                           ("infer_listen", infer_listener.stop)):
        try:
            await stopper()
            log.info(jlog("%s_stopped" % name))
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("%s_stop_failed" % name, error=type(e).__name__,
                             detail=str(e)[:160]))


# ==================== 计划定时执行（2026-09-13）====================
# 用户需求：任务中心「计划」Tab 可对草稿计划设定时，到点自动下发（无需人工点「下发」）。
# 设计：单进程内 asyncio 后台循环（与队列消费者同生命周期），每 15s 扫一次到点计划；
#      抢单在真正入队之前完成（plan_mgr.claim_due），避免多轮重复入队。
PLAN_SCHED_INTERVAL = 15.0
_plan_sched_task: "asyncio.Task | None" = None


def _parse_ts(v: Any) -> float:
    """把时间输入解析成 Unix 秒。支持：秒/毫秒时间戳（int/float/数字串）、ISO 字符串。

    毫秒判定用 > 1e12（1e12 秒 ≈ 公元 33658 年，正常业务值绝不会到）。解析失败返回 0.0。
    """
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        f = float(v)
        return f / 1000.0 if f > 1e12 else f
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        f = float(s)
        return f / 1000.0 if f > 1e12 else f
    except Exception:  # noqa: BLE001 — 非数字，按 ISO 时间解析
        pass
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.astimezone()  # 无时区信息 → 按本机时区解释
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


async def _execute_plan(plan_id: str, scheduled_at: float = 0.0) -> dict:
    """下发一份已存计划（「立即下发」与「定时到点」共用入口）。

    scheduled_at > 0 表示定时触发（任务中心「定时日期」列），0 表示人工手动下发。
    返回 enqueue_batch 的结果 {ok, batch_id, total, accepted, ...}。
    """
    plan = plan_mgr.get(plan_id)
    if not plan:
        return {"ok": False, "error": "计划不存在"}
    try:
        items = json.loads(plan["json_text"])
    except Exception as e:  # noqa: BLE001
        log.error(jlog("plan_json_broken", plan_id=plan_id, error=str(e)[:160]))
        return {"ok": False, "error": "计划 JSON 损坏：%s" % str(e)[:200]}
    if not isinstance(items, list):
        return {"ok": False, "error": "计划必须是 JSON 数组"}
    uid = int(next(iter(Config.BOT_OWNER_QQ or (0,))))
    await queue_mgr.ensure_consumer()
    res = await queue_mgr.enqueue_batch(
        items, plan.get("channel", "local"), uid, 0, "web",
        name=str(plan.get("name") or ""), plan_id=plan_id,
        scheduled_at=float(scheduled_at or 0),
    )
    if res.get("ok"):
        plan_mgr.mark_run(plan_id, res["batch_id"])
    return res


async def _plan_scheduler_loop() -> None:
    """每 PLAN_SCHED_INTERVAL 秒扫一次到点计划并自动下发。

    失败只记日志绝不退出循环（一个坏计划不能拖垮整个调度器）。
    """
    log.info(jlog("plan_scheduler_started", interval=PLAN_SCHED_INTERVAL))
    while True:
        try:
            for p in plan_mgr.due_plans():
                pid = str(p.get("plan_id") or "")
                # 抢单：先移出 due 再入队，防同一计划被连续多轮重复下发
                if not plan_mgr.claim_due(pid):
                    continue
                res = await _execute_plan(pid, scheduled_at=time.time())
                bid = str(res.get("batch_id") or "")
                plan_mgr.mark_schedule_result(pid, bid)
                cur = plan_mgr.get(pid) or {}
                log.info(jlog(
                    "plan_scheduled_run", plan_id=pid, name=str(p.get("name") or "")[:40],
                    ok=bool(res.get("ok")), batch_id=bid, total=res.get("total"),
                    accepted=res.get("accepted"), repeat=p.get("schedule_repeat"),
                    next_at=cur.get("schedule_at"), enabled=bool(cur.get("schedule_enabled")),
                ))
                if not res.get("ok"):
                    log.error(jlog("plan_scheduled_run_failed", plan_id=pid,
                                   error=str(res.get("error"))[:200]))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error(jlog("plan_scheduler_error", error=type(e).__name__, detail=str(e)[:200]))
        await asyncio.sleep(PLAN_SCHED_INTERVAL)


def _plan_scheduler_start() -> None:
    """拉起调度器任务（幂等：已在跑则不重复创建）。"""
    global _plan_sched_task
    if _plan_sched_task is not None and not _plan_sched_task.done():
        return
    _plan_sched_task = asyncio.get_running_loop().create_task(_plan_scheduler_loop())


async def _plan_scheduler_stop() -> None:
    """取消调度器任务（关闭时调用，OPS-01 临时资源随请求释放）。"""
    global _plan_sched_task
    t, _plan_sched_task = _plan_sched_task, None
    if t is None or t.done():
        return
    t.cancel()
    try:
        await t
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


def _register_queue_routes() -> None:
    """任务中心 REST（2026-09-08）：给 8099「任务中心」页用。

    与主项目端点**完全分离**：这里读的是 bridge 自己的队列（含尚未提交到平台的
    排队任务），主项目 `GET /t2i/tasks` 只能看到已入 image_tasks 的那些。
    前端把两者合并渲染即可看到完整链路（排队中 → 已提交 → 完成）。
    """
    try:
        from nonebot import get_app
    except Exception:
        return
    app = get_app()

    async def _q_get():
        await queue_mgr.ensure_consumer()
        return queue_mgr.snapshot()

    async def _q_recent(req: Request):
        try:
            limit = int(req.query_params.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        uid_raw = req.query_params.get("user_id") or ""
        uid = int(uid_raw) if uid_raw.strip().isdigit() else None
        return {"ok": True, "items": queue_mgr.recent(user_id=uid, limit=max(1, min(limit, 50)))}

    async def _q_submit(req: Request):
        """下发任务（Web 面板「任务中心」的生图表单）。"""
        try:
            b = await req.json()
        except Exception:
            b = {}
        if not isinstance(b, dict):
            b = {}
        prompt = str(b.get("prompt") or "").strip()
        if not prompt:
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "提示词不能为空"})
        params = b.get("params") if isinstance(b.get("params"), dict) else {}
        # 与 QQ 侧同一套长度规则（超长按句末截断，不直接拒）
        prompt, was_truncated = truncate_prompt(prompt)
        ok, why = validate_prompt(prompt)
        if not ok:
            return JSONResponse(status_code=400, content={"ok": False, "error": why})
        # Web 面板下发：user_id 用主人号（否则任务无法归因/取消）
        await queue_mgr.ensure_consumer()
        uid = b.get("user_id")
        try:
            uid = int(uid) if uid not in (None, "") else int(next(iter(Config.BOT_OWNER_QQ or (0,))))
        except (TypeError, ValueError):
            uid = 0
        res = await queue_mgr.enqueue(uid, 0, "web", prompt, params)
        res["truncated"] = was_truncated
        return res

    async def _q_cancel(req: Request):
        try:
            b = await req.json()
        except Exception:
            b = {}
        target = str((b or {}).get("target") or (b or {}).get("seq") or "").strip()
        if not target:
            return JSONResponse(status_code=400, content={"ok": False, "error": "缺少任务号"})
        # Web 面板走主人权限（页面本身只有主人能进）
        return await queue_mgr.cancel(target, user_id=0, is_owner=True)

    async def _q_promote(req: Request):
        try:
            b = await req.json()
        except Exception:
            b = {}
        target = str((b or {}).get("target") or "").strip()
        if not target:
            return JSONResponse(status_code=400, content={"ok": False, "error": "缺少任务号"})
        return await queue_mgr.promote(target, is_owner=True)

    async def _q_clear(req: Request):
        return await queue_mgr.clear(is_owner=True)

    app.add_api_route("/api/qq/queue", _q_get, methods=["GET"])
    app.add_api_route("/api/qq/queue/recent", _q_recent, methods=["GET"])
    app.add_api_route("/api/qq/queue/submit", _q_submit, methods=["POST"])
    app.add_api_route("/api/qq/queue/cancel", _q_cancel, methods=["POST"])
    app.add_api_route("/api/qq/queue/promote", _q_promote, methods=["POST"])
    app.add_api_route("/api/qq/queue/clear", _q_clear, methods=["POST"])

    async def _q_batch(req: Request):
        """批量生图计划下发（Web 面板「生图计划」）：JSON 文本 + 通道 → 逐条入队。

        通道 channel="local"（本地 ComfyUI）/ "online"（在线云端），决定每条任务的提交路径；
        计划内任务由单消费者循环依次执行。校验/解析失败都返回结构化错误，不静默。
        """
        try:
            b = await req.json()
        except Exception:
            b = {}
        if not isinstance(b, dict):
            b = {}
        json_text = str(b.get("json_text") or "").strip()
        name = str(b.get("name") or "").strip()[:80]
        channel = str(b.get("channel") or "local").strip().lower()
        if channel not in ("local", "online"):
            channel = "local"
        if not json_text:
            return JSONResponse(status_code=400, content={"ok": False, "error": "未提供 JSON 文本"})
        try:
            items = json.loads(json_text)
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "JSON 解析失败：%s" % str(e)[:200]})
        if not isinstance(items, list):
            return JSONResponse(status_code=400, content={"ok": False, "error": "计划必须是 JSON 数组"})
        uid = b.get("user_id")
        try:
            uid = int(uid) if uid not in (None, "") else int(next(iter(Config.BOT_OWNER_QQ or (0,))))
        except (TypeError, ValueError):
            uid = 0
        await queue_mgr.ensure_consumer()
        res = await queue_mgr.enqueue_batch(items, channel, uid, 0, "web", name=name)
        if not res.get("ok"):
            return JSONResponse(status_code=400, content=res)
        return res

    app.add_api_route("/api/qq/queue/batch", _q_batch, methods=["POST"])

    # ===== 计划/任务组聚合（2026-09-12 新增；2026-09-15 扩展为 5 状态页签数据源）=====
    async def _q_batches(req: Request):
        """所有计划/任务组聚合（含非批次单条任务）。

        行字段见 queue_mgr.batches_summary()；此处额外为**老批次**（创建时尚未记录
        计划名）从草稿计划里回填 名称 / 定时日期，保证「计划名称/定时日期」列不空。
        """
        items = queue_mgr.batches_summary()
        try:
            by_batch: dict[str, dict] = {}
            for p in plan_mgr.list():
                for bid in (p.get("last_run_batch"), p.get("schedule_last_batch")):
                    if bid:
                        by_batch[str(bid)] = p
            for it in items:
                bid = str(it.get("batch_id") or "")
                p = by_batch.get(bid) if bid else None
                if not p:
                    continue
                if not it.get("name"):
                    it["name"] = str(p.get("name") or "")
                if not it.get("plan_id"):
                    it["plan_id"] = str(p.get("plan_id") or "")
                if not it.get("scheduled_at"):
                    it["scheduled_at"] = float(p.get("schedule_last_fired_at") or 0)
        except Exception as e:  # noqa: BLE001 — 回填失败不影响列表本身
            log.warning(jlog("batch_plan_name_backfill_failed", error=str(e)[:160]))
        return {"ok": True, "items": items}

    app.add_api_route("/api/qq/queue/batches", _q_batches, methods=["GET"])

    async def _q_batch_detail(req: Request):
        bid = req.path_params.get("batch_id") or ""
        return queue_mgr.batch_detail(bid)

    app.add_api_route("/api/qq/queue/batch/{batch_id}", _q_batch_detail, methods=["GET"])

    async def _q_batch_cancel(req: Request):
        """计划级暂停（剩余 queued 取消）。"""
        bid = req.path_params.get("batch_id") or ""
        return await queue_mgr.batch_cancel(bid)

    app.add_api_route("/api/qq/queue/batch/{batch_id}/cancel", _q_batch_cancel, methods=["POST"])

    async def _q_batch_resume(req: Request):
        """计划级恢复（复活被暂停取消的条目）。"""
        bid = req.path_params.get("batch_id") or ""
        return await queue_mgr.batch_resume(bid)

    app.add_api_route("/api/qq/queue/batch/{batch_id}/resume", _q_batch_resume, methods=["POST"])

    async def _q_batch_retry_failed(req: Request):
        """计划级重试失败：本批次内所有 failed 任务重新入队。"""
        bid = req.path_params.get("batch_id") or ""
        return await queue_mgr.batch_retry_failed(bid)

    app.add_api_route("/api/qq/queue/batch/{batch_id}/retry-failed", _q_batch_retry_failed, methods=["POST"])

    async def _q_retry(req: Request):
        """单任务重试（失败 → 重新排队）。"""
        try:
            b = await req.json()
        except Exception:
            b = {}
        target = str((b or {}).get("target") or "").strip()
        if not target:
            return JSONResponse(status_code=400, content={"ok": False, "error": "缺少任务号"})
        return await queue_mgr.retry(target, user_id=0, is_owner=True)

    app.add_api_route("/api/qq/queue/retry", _q_retry, methods=["POST"])

    # ===== 计划草稿（2026-09-12）：存为模板 / 一键复用下发 =====
    async def _plan_list(req: Request):
        return {"ok": True, "items": plan_mgr.list()}

    app.add_api_route("/api/qq/plans", _plan_list, methods=["GET"])

    async def _plan_save(req: Request):
        try:
            b = await req.json()
        except Exception:
            b = {}
        if not isinstance(b, dict):
            b = {}
        name = str(b.get("name") or "").strip()
        channel = str(b.get("channel") or "local").strip().lower()
        json_text = str(b.get("json_text") or "").strip()
        if not json_text:
            return JSONResponse(status_code=400, content={"ok": False, "error": "计划内容不能为空"})
        try:
            items = json.loads(json_text)
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "JSON 解析失败：%s" % str(e)[:200]})
        if not isinstance(items, list):
            return JSONResponse(status_code=400, content={"ok": False, "error": "计划必须是 JSON 数组"})
        return plan_mgr.save(name, channel, json_text)

    app.add_api_route("/api/qq/plans", _plan_save, methods=["POST"])

    async def _plan_get(req: Request):
        pid = req.path_params.get("plan_id") or ""
        p = plan_mgr.get(pid)
        if not p:
            return JSONResponse(status_code=404, content={"ok": False, "error": "计划不存在"})
        return {"ok": True, "plan": p}

    app.add_api_route("/api/qq/plans/{plan_id}", _plan_get, methods=["GET"])

    async def _plan_delete(req: Request):
        pid = req.path_params.get("plan_id") or ""
        if plan_mgr.delete(pid):
            return {"ok": True}
        return JSONResponse(status_code=404, content={"ok": False, "error": "计划不存在"})

    app.add_api_route("/api/qq/plans/{plan_id}", _plan_delete, methods=["DELETE"])

    async def _plan_run(req: Request):
        """复用已存计划一键下发（立即执行）→ 与定时执行共用 _execute_plan。"""
        pid = req.path_params.get("plan_id") or ""
        if not plan_mgr.get(pid):
            return JSONResponse(status_code=404, content={"ok": False, "error": "计划不存在"})
        return await _execute_plan(pid)

    app.add_api_route("/api/qq/plans/{plan_id}/run", _plan_run, methods=["POST"])

    async def _plan_schedule(req: Request):
        """设置 / 取消草稿计划的「定时执行」，并可同步改写执行模板。

        body: {enabled: bool, schedule_at: 秒或毫秒时间戳（也接受 ISO 字符串）,
               repeat: "none"（一次）| "daily"（每天同一时刻）,
               template_id: 可选，传则改写计划执行模板（空串=恢复默认）}
        enabled=false → 取消定时。
        """
        pid = req.path_params.get("plan_id") or ""
        try:
            b = await req.json()
        except Exception:
            b = {}
        if not isinstance(b, dict):
            b = {}
        enabled = b.get("enabled", True) is not False
        at = _parse_ts(b.get("schedule_at"))
        # template_id 仅在请求显式给出时才下传（含空串）；未给则不动模板
        tid = b.get("template_id")
        res = plan_mgr.set_schedule(pid, at, str(b.get("repeat") or "none"),
                                    enabled=enabled, template_id=tid)
        if not res.get("ok"):
            code = 404 if "不存在" in str(res.get("error")) else 400
            return JSONResponse(status_code=code, content=res)
        pl = res.get("plan") or {}
        log.info(jlog("plan_schedule_set", plan_id=pid, enabled=enabled,
                      schedule_at=pl.get("schedule_at"), repeat=pl.get("schedule_repeat"),
                      template_id=tid))
        return res

    app.add_api_route("/api/qq/plans/{plan_id}/schedule", _plan_schedule, methods=["POST"])

    async def _q_kick(req: Request):
        """诊断：若消费者任务死亡则重启（仅开发/排障用）。"""
        dead = queue_mgr._task is None or queue_mgr._task.done()
        if dead:
            await queue_mgr.stop()
            await queue_mgr.start()
            return {"ok": True, "restarted": True, "previous_dead": True}
        return {"ok": True, "restarted": False, "alive": True}

    app.add_api_route("/api/qq/queue/kick", _q_kick, methods=["POST"])
    log.info(jlog("queue_routes_registered", paths="/api/qq/queue"))


def _register_config_routes() -> None:
    """挂载 QQ 机器人配置管理 REST 接口（GET/PUT /api/qq/config）+ CORS。

    使 8099 前端管理页可经 HTTP 读/写 qq_bridge 配置（单向依赖，不污染主项目后端）。
    配置数据全部存于 qq_bridge/.env（本目录内），红线合规。
    """
    try:
        from nonebot import get_app
        from fastapi.middleware.cors import CORSMiddleware
        from .config_api import get_config_view, apply_config, proxy_templates

        app = get_app()
        # CORS：放行管理页（8099）跨域访问；开发期放宽到 *
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        async def _config_get():
            return get_config_view()

        async def _config_put(req: Request):
            try:
                body = await req.json()
            except Exception:
                body = {}
            updates = body.get("updates", {}) if isinstance(body, dict) else {}
            return apply_config(updates)

        app.add_api_route("/api/qq/config", _config_get, methods=["GET"])
        app.add_api_route("/api/qq/config", _config_put, methods=["PUT"])
        log.info(jlog("config_routes_registered", path="/api/qq/config"))

        # 2026-09-06 新增：真实在线状态（给 8099 管理页展示）。
        # 之前管理页只能探 8080 端口判断"服务运行中"，但 bridge 在跑 ≠ 机器人在
        # QQ 上在线（NapCat 掉线/未登录时 bridge 依然监听，页面却显示正常），
        # 状态严重失真。这里直接暴露 connector 的连接簿：online 为真才代表
        # NapCat 已连上、账号已登录、能收能发。
        async def _status_get():
            st = connector.status()
            off = _official_status()
            return {
                "online": bool(st.get("online")),
                "self_id": (st["bots"][0] if st.get("bots") else None),
                "bots": st.get("bots") or [],
                "connect_count": st.get("connect_count", 0),
                "last_connected_at": st.get("last_connected_at", 0.0),
                "last_disconnected_at": st.get("last_disconnected_at", 0.0),
                "pid": os.getpid(),
                # 双轨并行（2026-09-12）：两条通道各自独立，前端可分开展示。
                # online 沿用 NapCat 语义（向后兼容），官方通道见 official.online。
                "channels": {
                    "napcat": bool(st.get("online")),
                    "official": bool(off.get("online")),
                },
                "official": off,
            }

        app.add_api_route("/api/qq/status", _status_get, methods=["GET"])
        log.info(jlog("status_routes_registered", path="/api/qq/status"))

        # /api/qq/templates 独立代理（2026-09-07 新增）：
        # 与 get_config_view 内联拉模板的实现同源（config_api.proxy_templates），
        # 单独暴露让前端可以在 select 打开时即时拉取，不用依赖整个 config 视图。
        # 失败容错：主项目 7788 未启 / 模板接口异常 → 200 + ok=false + items=[]，
        # 前端拿到 ok=false 时降级为「主项目不可达」提示文案。
        async def _templates_get():
            return await proxy_templates()

        app.add_api_route("/api/qq/templates", _templates_get, methods=["GET"])

        # 2026-09-16：触发口令清单（内置暗号 + 可配关键词 + 自定义），管理页「口令清单」展示
        async def _intents_get():
            return _intents_all()

        app.add_api_route("/api/qq/intents", _intents_get, methods=["GET"])
        log.info(jlog("intents_route_registered", path="/api/qq/intents"))

        # 2026-09-16：触发口令清单（内置暗号 + 可配关键词 + 自定义），管理页「口令清单」展示
        async def _intents_get():
            return _intents_all()

        app.add_api_route("/api/qq/intents", _intents_get, methods=["GET"])
        log.info(jlog("intents_route_registered", path="/api/qq/intents"))
        log.info(jlog("templates_route_registered", path="/api/qq/templates"))

        # 2026-09-21：Warframe 专属接口调试台（管理页新页签「🎮 Warframe」）。
        # 列出全部外部数据源配置 + 当前缓存状态；支持手动点按钮重新调用接口，
        # 返回结果摘要（键/条数/首条样本），方便排查「哪个源挂了、数据对不对」。
        def _wf_cache_info(c, ttl):
            now = None
            try:
                from .game_tools import warframe as _wf
                now = _wf._now()
            except Exception:
                return {"cached": False, "reason": "warframe 模块未加载"}
            if not c.get("ts"):
                return {"cached": False, "age_sec": None, "ttl": ttl, "remaining_sec": ttl}
            age = int(now - c["ts"])
            return {"cached": bool(c.get("data") or c.get("html")),
                    "age_sec": age, "ttl": ttl,
                    "remaining_sec": max(0, int(ttl - age))}

        async def _wf_apis_list():
            try:
                from .game_tools import warframe as wf
            except Exception as e:
                return {"ok": False, "error": f"warframe 模块未加载: {e}"}
            # 卡片链路清单（preview 路由按 key 跑完整 raw→extracted→card 三步）
            # groups：前端树形展示——顶层按数据源分组，叶子是具体卡片链路。
            # 每条链路标注它实际调用的外部 API（URL/方法/超时/缓存 TTL）。
            chains = [
                {"key": "netracell", "name": "科研任务", "cmd": "#wf 科研",
                 "group": "official", "desc": "深层+时光实验室本周任务",
                 "card_template": "netracell",
                 "apis": [{"name": "官方 worldState", "method": "GET",
                           "url": wf._OFFICIAL_URL, "timeout": wf._TIMEOUT, "ttl": wf._OFFICIAL_TTL}]},
                {"key": "fissure_quick", "name": "虚空裂隙·速览", "cmd": "#wf 裂隙",
                 "group": "tennotools", "desc": "全裂隙一页速览", "card_template": "fissure",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL_FAST}]},
                {"key": "fissure_normal", "name": "虚空裂隙·普通", "cmd": "#wf 普通裂隙",
                 "group": "tennotools", "desc": "非钢铁裂隙分组", "card_template": "fissure",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL_FAST}]},
                {"key": "fissure_steel", "name": "虚空裂隙·钢铁", "cmd": "#wf 钢铁裂隙",
                 "group": "tennotools", "desc": "钢铁之路裂隙", "card_template": "fissure",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL_FAST}]},
                {"key": "bounty", "name": "赏金任务", "cmd": "#wf 赏金",
                 "group": "bounty", "desc": "希图斯/索拉里斯赏金轮换", "card_template": "guide",
                 "apis": [
                    {"name": "官方 worldState", "method": "GET", "url": wf._OFFICIAL_URL,
                     "timeout": wf._TIMEOUT, "ttl": wf._OFFICIAL_TTL},
                    {"name": "wf.wiki RSC", "method": "GET", "url": "https://worldstate.wf.wiki/",
                     "timeout": wf._TIMEOUT, "ttl": wf._WFWIKI_TTL}]},
                {"key": "steel_bounty", "name": "钢铁赏金", "cmd": "#wf 钢铁赏金",
                 "group": "bounty", "desc": "钢铁之路赏金", "card_template": "guide",
                 "apis": [
                    {"name": "官方 worldState", "method": "GET", "url": wf._OFFICIAL_URL,
                     "timeout": wf._TIMEOUT, "ttl": wf._OFFICIAL_TTL},
                    {"name": "wf.wiki RSC", "method": "GET", "url": "https://worldstate.wf.wiki/",
                     "timeout": wf._TIMEOUT, "ttl": wf._WFWIKI_TTL}]},
                {"key": "riven", "name": "紫卡模拟开卡", "cmd": "#紫卡模拟",
                 "group": "local", "desc": "纯娱乐模拟出货", "card_template": "riven",
                 "apis": []},
                # 状态查询类（都走 Tenno.Tools worldstate，文本卡片）
                {"key": "sortie", "name": "每日突击", "cmd": "#wf 突击",
                 "group": "tennotools", "desc": "每日三连突击", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "baro", "name": "虚空商人(奸商)", "cmd": "#wf 奸商",
                 "group": "tennotools", "desc": "Baro Ki'Teer 来访/货物", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "daily_deals", "name": "每日特惠", "cmd": "#wf 特惠",
                 "group": "tennotools", "desc": "Darvo 每日折扣", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "invasions", "name": "入侵战况", "cmd": "#wf 入侵",
                 "group": "tennotools", "desc": "阵营入侵战奖励", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "arbitration", "name": "仲裁", "cmd": "#wf 仲裁",
                 "group": "tennotools", "desc": "每日仲裁任务", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "nightwave", "name": "午夜电波", "cmd": "#wf 电波",
                 "group": "tennotools", "desc": "周挑战/每日挑战", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "voidstorms", "name": "九重天虚空风暴", "cmd": "#wf 九重天",
                 "group": "tennotools", "desc": "奥布山谷虚空风暴", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "weekly", "name": "周本周期", "cmd": "#wf 周本",
                 "group": "tennotools", "desc": "周期任务一览", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "rotation", "name": "赤毒信条轮换", "cmd": "#wf 轮换",
                 "group": "tennotools", "desc": "信条/赤毒武器轮换", "card_template": "guide",
                 "apis": [{"name": "Tenno.Tools worldstate", "method": "GET",
                           "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT, "ttl": wf._CACHE_TTL}]},
                {"key": "almanac", "name": "仓鼠黄历", "cmd": "#wf 黄历",
                 "group": "local", "desc": "今日运势+紫卡运势", "card_template": "guide",
                 "apis": []},
                {"key": "market", "name": "市场价格查询", "cmd": "#wf 价格 <物品>",
                 "group": "market", "desc": "WM 挂单列表+48h/90d 价格统计+喊话",
                 "card_template": "guide",
                 "apis": [{"name": "WM orders", "method": "GET",
                           "url": wf.MARKET_BASE + "/v2/orders/item/{url_name}",
                           "timeout": wf._TIMEOUT, "ttl": wf._MARKET_CACHE_TTL},
                          {"name": "WM statistics", "method": "GET",
                           "url": wf.MARKET_BASE + "/v1/items/{url_name}/statistics",
                           "timeout": wf._TIMEOUT, "ttl": wf._MARKET_CACHE_TTL}]},
            ]
            # 分组元信息（前端树顶层）
            chain_groups = [
                {"key": "tennotools", "label": "Tenno.Tools 世界状态",
                 "url": wf.WORLDSTATE_URL, "method": "GET",
                 "note": "裂隙/平原等高频轮换源"},
                {"key": "official", "label": "官方 worldState.php",
                 "url": wf._OFFICIAL_URL, "method": "GET",
                 "note": "科研/赏金 Conquests 源"},
                {"key": "bounty", "label": "赏金（官方 + wf.wiki）",
                 "url": "https://worldstate.wf.wiki/", "method": "GET",
                 "note": "双源合并：官方任务 + wf.wiki RSC 奖励"},
                {"key": "market", "label": "Warframe.Market 交易",
                 "url": wf.MARKET_BASE, "method": "GET",
                 "note": "挂单列表/价格统计（本机可能被风控 403）"},
                {"key": "local", "label": "本地（无外呼）",
                 "url": "—", "method": "—", "note": "本地随机/离线数据"},
            ]
            apis = [
                {"key": "tennotools", "name": "Tenno.Tools 世界状态",
                 "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT,
                 "ttl": wf._CACHE_TTL, "purpose": "裂隙/突击/入侵/仲裁/电波/赏金 主数据源",
                 "kind": "json", "cache": _wf_cache_info(wf._cache, wf._CACHE_TTL)},
                {"key": "tennotools_fast", "name": "Tenno.Tools 世界状态(快缓存)",
                 "url": wf.WORLDSTATE_URL, "timeout": wf._TIMEOUT,
                 "ttl": wf._CACHE_TTL_FAST, "purpose": "裂隙/平原/九重天 高频轮换",
                 "kind": "json", "cache": _wf_cache_info(wf._fast_cache, wf._CACHE_TTL_FAST)},
                {"key": "official", "name": "官方 worldState.php",
                 "url": wf._OFFICIAL_URL, "timeout": wf._TIMEOUT,
                 "ttl": wf._OFFICIAL_TTL, "purpose": "科研 Conquests（深层/时光）",
                 "kind": "json", "cache": _wf_cache_info(wf._official_cache, wf._OFFICIAL_TTL)},
                {"key": "wfwiki", "name": "wf.wiki RSC",
                 "url": "https://worldstate.wf.wiki/", "timeout": wf._TIMEOUT,
                 "ttl": wf._WFWIKI_TTL, "purpose": "赏金任务板（bountyBoard）",
                 "kind": "html", "cache": _wf_cache_info(wf._wfwiki_cache, wf._WFWIKI_TTL)},
                {"key": "market", "name": "warframemarket 市价",
                 "url": wf.MARKET_BASE, "timeout": wf._TIMEOUT,
                 "ttl": wf._MARKET_CACHE_TTL, "purpose": "MOD/紫卡 价格查询",
                 "kind": "json", "cache": {"cached": bool(wf._MARKET_CACHE.get("data")),
                                            "age_sec": int(wf._now() - wf._MARKET_CACHE["ts"]) if wf._MARKET_CACHE.get("ts") else None,
                                            "ttl": wf._MARKET_CACHE_TTL,
                                            "remaining_sec": max(0, int(wf._MARKET_CACHE_TTL - (wf._now() - wf._MARKET_CACHE["ts"]))) if wf._MARKET_CACHE.get("ts") else int(wf._MARKET_CACHE_TTL)}},
            ]
            return {"ok": True, "apis": apis, "chains": chains, "chain_groups": chain_groups,
                    "rate_gate_min_interval": wf._MIN_INTERVAL,
                    "fail_cooldown": wf._FAIL_COOLDOWN}

        async def _wf_api_call(req: Request):
            name = (req.query_params.get("name") or "").strip()
            try:
                from .game_tools import warframe as wf
            except Exception as e:
                return {"ok": False, "error": f"warframe 模块未加载: {e}"}
            import time as _t
            t0 = _t.time()
            try:
                if name == "tennotools":
                    d = await wf._worldstate(fast=False)
                elif name == "tennotools_fast":
                    d = await wf._worldstate(fast=True)
                elif name == "official":
                    d = await wf._official_worldstate()
                elif name == "wfwiki":
                    h = await wf._wfwiki_html()
                    dt = int((_t.time() - t0) * 1000)
                    return {"ok": bool(h), "elapsed_ms": dt,
                            "kind": "html", "size": len(h) if h else 0,
                            "preview": (h or "")[:500]}
                else:
                    return {"ok": False, "error": f"未知接口: {name}"}
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}",
                        "elapsed_ms": int((_t.time() - t0) * 1000)}
            dt = int((_t.time() - t0) * 1000)
            if not isinstance(d, dict):
                return {"ok": False, "error": "返回非 dict", "elapsed_ms": dt}
            summary = {}
            for k, v in d.items():
                if isinstance(v, dict):
                    inner = v.get("data")
                    summary[k] = {"type": "dict", "keys": list(v.keys())[:10],
                                  "items": len(inner) if isinstance(inner, list) else None}
                elif isinstance(v, list):
                    summary[k] = {"type": "list", "items": len(v)}
                else:
                    summary[k] = {"type": type(v).__name__}
            return {"ok": True, "elapsed_ms": dt, "kind": "json",
                    "top_keys": list(d.keys()), "summary": summary}

        app.add_api_route("/api/qq/warframe/apis", _wf_apis_list, methods=["GET"])
        app.add_api_route("/api/qq/warframe/call", _wf_api_call, methods=["POST"])
        log.info(jlog("warframe_routes_registered", path="/api/qq/warframe/*"))

        # 2026-09-24：Warframe 页签「口令对照表」+「战甲外号对照表」。
        # 均为离线数据（curated 文档 / 解析 战甲别名全表.md），不触发任何外部 HTTP。
        async def _wf_commands():
            try:
                from .game_tools import wf_reference
                return {"ok": True, "commands": wf_reference.command_reference()}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"wf_reference 加载失败: {e}"}

        async def _wf_aliases():
            try:
                from .game_tools import wf_reference
                return {"ok": True, "sections": wf_reference.alias_reference()}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"wf_reference 加载失败: {e}"}

        app.add_api_route("/api/qq/warframe/commands", _wf_commands, methods=["GET"])
        app.add_api_route("/api/qq/warframe/aliases", _wf_aliases, methods=["GET"])
        log.info(jlog("warframe_reference_routes_registered", path="/api/qq/warframe/commands|aliases"))

        # 2026-09-21 v2：链路调试台——一条卡片链路完整跑通：
        #   raw（外部源原始数据）→ extracted（提取后的结构化结果）→ card（渲染出的卡片 PNG）。
        # 管理页用 base64 data URI 直接 <img> 预览，等同 QQ 里实际收到的那张卡。
        import base64 as _b64, time as _time
        from . import pic as _pic_mod

        def _raw_preview(obj, limit=60):
            """原始数据截断预览：dict 列前 limit 个顶层键及类型/条数。"""
            if obj is None:
                return None
            if isinstance(obj, str):
                return {"kind": "html/text", "size": len(obj), "head": obj[:300]}
            if isinstance(obj, dict):
                out = {}
                for i, (k, v) in enumerate(obj.items()):
                    if i >= limit: break
                    if isinstance(v, list): out[k] = f"list[{len(v)}]"
                    elif isinstance(v, dict): out[k] = f"dict{{{len(v)}keys}}"
                    else: out[k] = type(v).__name__
                return {"kind": "json", "top_keys": list(obj.keys()), "preview": out,
                        "total_keys": len(obj)}
            return {"kind": type(obj).__name__}

        def _png_data_uri(png):
            if not png: return None
            return "data:image/png;base64," + _b64.b64encode(png).decode("ascii")

        async def _wf_preview(req: Request):
            name = (req.query_params.get("name") or "").strip()
            try:
                from .game_tools import warframe as wf
            except Exception as e:
                return {"ok": False, "error": f"warframe 模块未加载: {e}"}
            t0 = _time.time()
            steps = {"raw": None, "extracted": None, "card": None}
            err = None
            try:
                if name == "netracell":
                    steps["raw"] = {"source": "官方 worldState.php", **_raw_preview(await wf._official_worldstate())}
                    ext = await wf.netracell_data()
                    steps["extracted"] = {"sections": len(ext.get("sections", [])),
                                          "title": ext.get("title"), "remain": ext.get("remain"),
                                          "section_samples": ext.get("sections", [])[:2]}
                    if ext.get("sections"):
                        png = await _pic_mod.render("netracell", theme_name=None,
                            card="星际战甲·科研任务", sections=ext["sections"],
                            title=ext.get("title", "本周科研任务"), remain=ext.get("remain", ""))
                        steps["card"] = _png_data_uri(png)
                elif name in ("fissure_quick", "fissure_normal", "fissure_steel"):
                    mode = name.split("_", 1)[1]  # quick/normal/steel
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    ext = await wf.fissures_data(mode)
                    steps["extracted"] = {"title": ext.get("title"),
                                          "intro": ext.get("intro"),
                                          "groups": len(ext.get("groups", [])),
                                          "footers": ext.get("footers", []),
                                          "group_samples": ext.get("groups", [])[:1]}
                    if ext.get("groups"):
                        png = await _pic_mod.render("fissure", theme_name=None,
                            card={"quick": "星际战甲·速览虚空裂隙", "normal": "星际战甲·普通虚空裂隙",
                                  "steel": "星际战甲·钢铁虚空裂隙"}[mode],
                            groups=ext["groups"], title=ext.get("title"),
                            intro=ext.get("intro", ""), footers=ext.get("footers", []))
                        steps["card"] = _png_data_uri(png)
                elif name in ("bounty", "steel_bounty"):
                    steps["raw"] = {"source": "官方 worldState.php + wf.wiki RSC",
                                    "official": _raw_preview(await wf._official_worldstate()),
                                    "wfwiki_html_size": len(await wf._wfwiki_html() or "")}
                    txt = await (wf.steel_bounties_text() if name == "steel_bounty" else wf.bounties_text())
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·赏金", body=txt[:1200], title="星际战甲·赏金任务")
                    steps["card"] = _png_data_uri(png)
                elif name == "riven":
                    steps["raw"] = {"source": "本地随机（无外呼）", "kind": "offline"}
                    d = wf._sim_riven_data()
                    steps["extracted"] = d
                    steps["card"] = _png_data_uri(await wf.render_sim_riven())
                elif name == "sortie":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.sortie_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·每日突击", body=txt[:1200], title="星际战甲·每日突击")
                    steps["card"] = _png_data_uri(png)
                elif name == "baro":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.void_trader_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·虚空商人", body=txt[:1200], title="星际战甲·虚空商人")
                    steps["card"] = _png_data_uri(png)
                elif name == "daily_deals":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.daily_deals_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·每日特惠", body=txt[:1200], title="星际战甲·每日特惠")
                    steps["card"] = _png_data_uri(png)
                elif name == "invasions":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.invasions_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·入侵战况", body=txt[:1200], title="星际战甲·入侵战况")
                    steps["card"] = _png_data_uri(png)
                elif name == "arbitration":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.arbitration_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·仲裁", body=txt[:1200], title="星际战甲·仲裁")
                    steps["card"] = _png_data_uri(png)
                elif name == "nightwave":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.nightwave_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·午夜电波", body=txt[:1200], title="星际战甲·午夜电波")
                    steps["card"] = _png_data_uri(png)
                elif name == "voidstorms":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.voidstorms_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·九重天虚空风暴", body=txt[:1200], title="星际战甲·九重天虚空风暴")
                    steps["card"] = _png_data_uri(png)
                elif name == "weekly":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.weekly_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·周本周期", body=txt[:1200], title="星际战甲·周本周期")
                    steps["card"] = _png_data_uri(png)
                elif name == "rotation":
                    steps["raw"] = {"source": "Tenno.Tools worldstate (fast)",
                                    **_raw_preview(await wf._worldstate(fast=True))}
                    txt = await wf.rotation_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·赤毒信条轮换", body=txt[:1200], title="星际战甲·赤毒信条轮换")
                    steps["card"] = _png_data_uri(png)
                elif name == "almanac":
                    steps["raw"] = {"source": "本地随机（无外呼）", "kind": "offline"}
                    txt = await wf.almanac_text()
                    steps["extracted"] = {"kind": "text", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="星际战甲·仓鼠黄历", body=txt[:1200], title="星际战甲·仓鼠黄历")
                    steps["card"] = _png_data_uri(png)
                elif name == "market":
                    # 市场查询：示例物品 Saryn Prime Set（本机 market API 可能被风控 403，失败降级）
                    steps["raw"] = {"source": "warframe.market orders + statistics",
                                    "sample_item": "Saryn Prime Set"}
                    txt = await wf.market_text("Saryn Prime Set")
                    steps["extracted"] = {"kind": "text/markup", "chars": len(txt), "text": txt}
                    png = await _pic_mod.render("guide", theme_name=None,
                        card="Warframe.market 物品市场", body=txt, title="Warframe.market 物品市场")
                    steps["card"] = _png_data_uri(png)
                else:
                    return {"ok": False, "error": f"未知链路: {name}"}
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            return {"ok": err is None, "error": err,
                    "elapsed_ms": int((_time.time() - t0) * 1000), "steps": steps}

        app.add_api_route("/api/qq/warframe/preview", _wf_preview, methods=["POST"])
        log.info(jlog("warframe_preview_route_registered"))
    except Exception as e:  # 配置路由注册失败绝不应拖垮消息处理
        log.error(jlog("config_routes_register_failed", error=str(e)))


def _official_status() -> dict:
    """QQ 官方通道状态（供 /api/qq/status 暴露给管理页）。

    三态要分清楚，别再犯「TCP 在就等于在线」的错：
      * enabled   —— 开关打开
      * configured—— AppID/Secret 填全（没填全则适配器不注册）
      * online    —— 官方 Bot 实例真的在线（nonebot 运行时里能取到）
    """
    out = {
        "enabled": bool(getattr(Config, "OFFICIAL_ENABLE", False)),
        "configured": False,
        "online": False,
        "sandbox": bool(getattr(Config, "OFFICIAL_SANDBOX", True)),
        "bots": [],
    }
    try:
        out["configured"] = bool(Config.official_configured())
        from nonebot import get_bots  # noqa: PLC0415

        from .channel import OFFICIAL, channel_of_bot  # noqa: PLC0415

        for bot in get_bots().values():
            if channel_of_bot(bot) == OFFICIAL:
                out["online"] = True
                out["bots"].append(str(getattr(bot, "self_id", "") or ""))
    except Exception as e:  # noqa: BLE001 — 状态查询失败不能拖垮状态接口
        log.warning(jlog("official_status_failed", error=type(e).__name__))
    return out


def _register_official_routes() -> None:
    """官方机器人**独立** REST 命名空间 ``/api/qqo/*``。

    为什么另起一套而不是复用 ``/api/qq/*``：用户要的是**两个机器人、两个页面**。
    两套接口在数据与状态上完全隔离：
      * NapCat 掉线/崩溃不影响官方页面；
      * 官方未启用/配置不全不影响 NapCat 页面；
      * 保存配置时**只接受官方分组的 key**，杜绝误改 NapCat 参数。
    """
    try:
        from nonebot import get_app  # noqa: PLC0415

        app = get_app()

        async def _official_status_api():
            st = _official_status()
            used: dict = {}
            try:
                from .channel.limiter import official_limiter  # noqa: PLC0415

                used = {k[0]: v for k, v in getattr(official_limiter, "_count", {}).items()}
            except Exception:  # noqa: BLE001
                pass
            return {
                "ok": True,
                "pid": os.getpid(),
                "enabled": st.get("enabled", False),
                "configured": st.get("configured", False),
                "online": st.get("online", False),
                "sandbox": st.get("sandbox", True),
                "bots": st.get("bots") or [],
                "daily_limit": int(getattr(Config, "OFFICIAL_DAILY_GROUP_LIMIT", 900)),
                "quota_used": used,
            }

        async def _official_config_get():
            """只返回「官方机器人」分组（配置页不用再看 NapCat 那一堆参数）。"""
            try:
                from .config_api import get_config_view  # noqa: PLC0415

                view = get_config_view()
                gs = [g for g in (view.get("groups") or []) if g.get("name") == "官方机器人"]
                return {"ok": True, "groups": gs}
            except Exception as e:  # noqa: BLE001
                log.error(jlog("official_config_get_failed", error=type(e).__name__,
                               detail=str(e)[:200]))
                return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=200)

        async def _official_config_put(req: Request):
            """只写官方分组的 key；其余一律拒绝并在 rejected 里列出来。"""
            try:
                body = await req.json()
            except Exception:  # noqa: BLE001
                body = {}
            updates = body.get("updates", {}) if isinstance(body, dict) else {}
            try:
                from .config_api import SCHEMA, apply_config  # noqa: PLC0415

                allowed = {item[1] for item in SCHEMA if item[0] == "官方机器人"}
            except Exception as e:  # noqa: BLE001
                log.error(jlog("official_schema_failed", error=type(e).__name__))
                return JSONResponse({"ok": False, "error": "schema unavailable"}, status_code=200)

            filtered = {k: v for k, v in updates.items() if k in allowed}
            rejected = sorted(k for k in updates if k not in allowed)
            res = apply_config(filtered) if filtered else {"saved": [], "errors": {},
                                                           "requires_restart": []}
            return {
                "ok": True,
                "saved": res.get("saved", []),
                "errors": res.get("errors", {}),
                "requires_restart": res.get("requires_restart", []),
                "rejected": rejected,
            }

        app.add_api_route("/api/qqo/status", _official_status_api, methods=["GET"])
        app.add_api_route("/api/qqo/config", _official_config_get, methods=["GET"])
        app.add_api_route("/api/qqo/config", _official_config_put, methods=["PUT"])
        log.info(jlog("official_routes_registered", paths="/api/qqo/{status,config}"))
    except Exception as e:  # noqa: BLE001 — 路由注册失败绝不应拖垮消息处理
        log.error(jlog("official_routes_register_failed", error=str(e)))


def _register_history_routes() -> None:
    """挂载「对话历史 / 群成员」REST 接口（2026-09-07 新增，供 8099 对话页使用）。

    为什么要这些接口：管理页要像 QQ 一样展示会话——谁在哪个群、什么时候说了什么、
    群主和管理员是谁、谁在黑名单。此前项目既不落盘消息，也从未拉过群成员，
    页面根本无从下手，故在此补齐读侧能力（写侧由 handle_message / media 旁路落盘）。

    接口：
      GET /api/qq/conversations           会话列表（群/私聊，含最后一条消息摘要与时间）
      GET /api/qq/history?scene=&peer_id= 某会话消息（时间戳、昵称、名片、角色、头像）
      GET /api/qq/groups                  群列表 + 当前监听群（白名单）+ 用户黑名单
      GET /api/qq/group_members?group_id= 群成员（昵称/名片/角色/头像/黑名单/主人标记）
    """
    try:
        from nonebot import get_app

        app = get_app()
        history.init_db()

        async def _conversations():
            data = await asyncio.to_thread(history.list_conversations, 50)
            return {"ok": True, "conversations": data, "online": connector.online}

        async def _history_list(req: Request):
            q = req.query_params
            scene = str(q.get("scene") or "group")
            try:
                peer_id = int(q.get("peer_id") or 0)
            except (TypeError, ValueError):
                peer_id = 0
            # 分页（2026-09-07）：默认只给最近 60 条 + has_more，前端滚到顶部再取更早的
            try:
                limit = max(1, min(int(q.get("limit") or 60), 200))
            except (TypeError, ValueError):
                limit = 60
            raw_before = q.get("before_ts") or ""
            try:
                before_ts = float(raw_before) if raw_before else None
            except (TypeError, ValueError):
                before_ts = None
            page = await asyncio.to_thread(
                history.list_messages_page, scene, peer_id, limit, before_ts
            )
            profiles = await asyncio.to_thread(history.list_profiles, peer_id)
            return {
                "ok": True,
                "messages": page["messages"],
                "has_more": page["has_more"],
                "next_before_ts": page["next_before_ts"],
                "total": page["total"],
                "profiles": profiles,
            }

        async def _groups():
            """群列表。在线时实时拉 OneBot get_group_list 并补充：
              - bot_mute：机器人在该群是否被禁言
              - all_mute：该群是否全员禁言（NapCat 扩展字段，取不到时为 null）
            离线时回退本地缓存。
            """
            err = ""
            raw = []
            try:
                raw = list(await connector.call_action("get_group_list") or [])
            except Exception as e:  # 离线/接口不支持：降级到缓存，页面照常能看
                err = "%s: %s" % (type(e).__name__, str(e)[:160])

            cached = {g["group_id"]: g for g in await asyncio.to_thread(history.list_groups)}
            wl = set(Config.GROUP_WHITELIST or ())
            items: list = []
            bot_id: int | None = None
            if connector.online:
                try:
                    bot_id = int(connector.status()["bots"][0])
                except Exception:
                    bot_id = None

            now = time.time()
            if raw:
                # 并发获取群信息（全员禁言）与机器人在该群的禁言状态，限制 5 并发防冲
                sem = asyncio.Semaphore(5)

                async def _fetch(g):
                    gid = int(g.get("group_id") or 0)
                    name = str(g.get("group_name") or "")
                    cnt = int(g.get("member_count") or 0)
                    bot_mute = None
                    all_mute = None
                    if gid and connector.online:
                        async with sem:
                            try:
                                info = await connector.call_action("get_group_info", {"group_id": gid})
                                if info and isinstance(info, dict):
                                    # NapCat 可能在 get_group_info 里扩展返回 shut_up_timestamp
                                    ts = info.get("shut_up_timestamp") or info.get("mute_all_timestamp") or 0
                                    if ts:
                                        all_mute = int(ts) > now
                            except Exception:
                                pass
                            if bot_id:
                                try:
                                    mi = await connector.call_action(
                                        "get_group_member_info",
                                        {"group_id": gid, "user_id": bot_id, "no_cache": True}
                                    )
                                    if mi and isinstance(mi, dict):
                                        ts = mi.get("shut_up_timestamp") or 0
                                        if ts:
                                            bot_mute = int(ts) > now
                                except Exception:
                                    pass
                    return gid, name, cnt, bot_mute, all_mute

                results = await asyncio.gather(*(_fetch(g) for g in raw), return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        continue
                    gid, name, cnt, bot_mute, all_mute = r
                    if not gid:
                        continue
                    await asyncio.to_thread(history.upsert_group, gid, name, cnt, 0)
                    items.append({
                        "group_id": gid, "name": name or ("群 %d" % gid),
                        "member_count": cnt, "avatar": history.group_avatar_url(gid),
                        "monitored": (not wl) or (gid in wl),
                        "bot_mute": bot_mute, "all_mute": all_mute,
                    })
            else:
                for gid, g in cached.items():
                    items.append({
                        "group_id": gid, "name": g["name"] or ("群 %d" % gid),
                        "member_count": g["member_count"], "avatar": g["avatar"],
                        "monitored": (not wl) or (gid in wl),
                        "bot_mute": None, "all_mute": None,
                    })

            # 历史里出现过、但机器人已不在（或拉不到）的群也补上
            seen = {i["group_id"] for i in items}
            for gid, g in cached.items():
                if gid not in seen:
                    items.append({
                        "group_id": gid, "name": g["name"] or ("群 %d" % gid),
                        "member_count": g["member_count"], "avatar": g["avatar"],
                        "monitored": (not wl) or (gid in wl),
                        "bot_mute": None, "all_mute": None,
                    })

            return {
                "ok": not err, "error": err, "groups": items, "online": connector.online,
                # 白名单为空 = 所有群都监听（见 dispatcher.allow 的语义）
                "watchlist": sorted(int(x) for x in wl),
                "watch_all": not wl,
                "blacklist": sorted(int(x) for x in (Config.USER_BLACKLIST or ())),
                "owners": sorted(int(x) for x in (Config.BOT_OWNER_QQ or ())),
            }

        async def _members(req: Request):
            """群成员列表（含群主/管理员/黑名单标记）。离线时回退本地档案缓存。"""
            try:
                gid = int(req.query_params.get("group_id") or 0)
            except (TypeError, ValueError):
                gid = 0
            if not gid:
                return {"ok": False, "error": "缺少 group_id", "members": [], "from_cache": False}

            err = ""
            raw = []
            try:
                raw = list(await connector.call_action(
                    "get_group_member_list", {"group_id": gid}) or [])
            except Exception as e:
                err = "%s: %s" % (type(e).__name__, str(e)[:160])

            black = set(int(x) for x in (Config.USER_BLACKLIST or ()))
            owners = set(int(x) for x in (Config.BOT_OWNER_QQ or ()))
            members: list = []
            if raw:
                for m in raw or []:
                    uid = int(m.get("user_id") or 0)
                    if not uid:
                        continue
                    nick = str(m.get("nickname") or "")
                    card = str(m.get("card") or "")
                    role = str(m.get("role") or "member")
                    members.append({
                        "user_id": uid, "nickname": nick, "card": card, "role": role,
                        "avatar": history.avatar_url(uid),
                        "blacklisted": uid in black, "is_owner": uid in owners,
                    })
                    # 群主/管理员一定要缓存下来：离线后页面仍能看到"谁是管理员"
                    if role in ("owner", "admin"):
                        await asyncio.to_thread(history.upsert_profile, gid, uid, nick, card, role)
            else:
                profs = await asyncio.to_thread(history.list_profiles, gid)
                for uid, p in profs.items():
                    members.append({
                        "user_id": uid, "nickname": p.get("nickname") or "",
                        "card": p.get("card") or "", "role": p.get("role") or "member",
                        "avatar": p.get("avatar") or history.avatar_url(uid),
                        "blacklisted": uid in black, "is_owner": uid in owners,
                    })

            # 群主排前、管理员次之，其余按群名片/昵称
            order = {"owner": 0, "admin": 1, "member": 2}
            members.sort(key=lambda x: (order.get(x["role"], 3), x["card"] or x["nickname"]))
            owner = next((m for m in members if m["role"] == "owner"), None)
            return {
                "ok": not err, "error": err, "members": members,
                "from_cache": not raw, "online": connector.online,
                "owner": owner, "admin_count": sum(1 for m in members if m["role"] == "admin"),
            }

        app.add_api_route("/api/qq/conversations", _conversations, methods=["GET"])
        app.add_api_route("/api/qq/history", _history_list, methods=["GET"])
        app.add_api_route("/api/qq/groups", _groups, methods=["GET"])
        app.add_api_route("/api/qq/group_members", _members, methods=["GET"])

        # --- 媒体文件代理：让对话页能显示机器人 base64/本地图片视频 ---
        async def _file(req: Request):
            name = str(req.query_params.get("name") or "").strip()
            if not name or ".." in name or "/" in name or "\\" in name:
                return JSONResponse(status_code=400, content={"ok": False, "error": "非法文件名"})
            path = os.path.join(history.QQ_FILES_DIR, name)
            if not os.path.isfile(path):
                return JSONResponse(status_code=404, content={"ok": False, "error": "文件不存在"})
            real = os.path.realpath(path)
            base = os.path.realpath(history.QQ_FILES_DIR)
            if not (real == base or real.startswith(base + os.sep)):
                return JSONResponse(status_code=403, content={"ok": False, "error": "禁止访问"})
            return FileResponse(path)

        app.add_api_route("/api/qq/file", _file, methods=["GET"])

        # --- 清空所有对话历史（含消息/档案/群缓存）---
        async def _clear_history(req: Request):
            res = await asyncio.to_thread(history.clear_all_history)
            if res.get("ok"):
                return res
            return JSONResponse(status_code=500, content=res)

        app.add_api_route("/api/qq/clear_history", _clear_history, methods=["POST"])

        # ==================== 2026-09-07 快捷操作写侧端点 ====================
        # 设计：对话页（8099）对「群成员 / 群 / 消息」的快捷操作，全部经 NapCat 的 OneBot
        # 协议转发到 QQ。离线（NapCat 未连 / QQ 未登录）时返回 503 + 明确文案，不让前端
        # 误以为成功。危险操作（踢人 / 黑名单）后端统一记审计日志；前端 useConfirm 是 UX
        # 层确认，这里不再二次弹窗，但必须以正确身份（管理员/群主）为前提由 QQ 侧校验。
        # 黑名单端点不依赖 OneBot 在线：直接改 Config.USER_BLACKLIST（dispatcher.allow 实时
        # 读取该 set），并写回 .env 持久化，返回 requires_restart=false（已热生效）。
        from .config_api import apply_config

        def _offline_json(msg: str = (
            "机器人离线：NapCat 未连接或 QQ 未登录，无法执行该操作。"
            "请先在管理页启动 NapCat 并完成扫码登录。"
        )) -> JSONResponse:
            """写操作在机器人离线时统一返回 503 + 结构化错误。"""
            return JSONResponse(status_code=503, content={"ok": False, "error": msg, "code": "offline"})

        async def _read_body(req: Request) -> dict:
            try:
                body = await req.json()
            except Exception:
                body = {}
            return body if isinstance(body, dict) else {}

        # --- 1. 禁言 / 解禁（set_group_ban）---
        async def _member_ban(req: Request):
            if not connector.online:
                return _offline_json()
            b = await _read_body(req)
            try:
                gid = int(b.get("group_id") or 0)
                uid = int(b.get("user_id") or 0)
                duration = int(b.get("duration") or 1800)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"ok": False, "error": "group_id/user_id/duration 必须为整数"})
            if not gid or not uid:
                return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 group_id 或 user_id"})
            if duration < 0:
                duration = 0
            try:
                await connector.call_action("set_group_ban", {"group_id": gid, "user_id": uid, "duration": duration})
                log.info(jlog("action_member_ban", group_id=gid, user_id=uid, duration=duration))
                return {"ok": True, "banned": duration > 0, "duration": duration}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

        app.add_api_route("/api/qq/group_member/ban", _member_ban, methods=["POST"])

        # --- 2. 踢人（set_group_kick）---
        async def _member_kick(req: Request):
            if not connector.online:
                return _offline_json()
            b = await _read_body(req)
            try:
                gid = int(b.get("group_id") or 0)
                uid = int(b.get("user_id") or 0)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"ok": False, "error": "group_id/user_id 必须为整数"})
            if not gid or not uid:
                return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 group_id 或 user_id"})
            try:
                await connector.call_action("set_group_kick", {"group_id": gid, "user_id": uid, "reject_add_request": True})
                log.info(jlog("action_member_kick", group_id=gid, user_id=uid))
                return {"ok": True, "kicked": uid}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

        app.add_api_route("/api/qq/group_member/kick", _member_kick, methods=["POST"])

        # --- 3. 设置群名片（set_group_card）---
        async def _member_card(req: Request):
            if not connector.online:
                return _offline_json()
            b = await _read_body(req)
            try:
                gid = int(b.get("group_id") or 0)
                uid = int(b.get("user_id") or 0)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"ok": False, "error": "group_id/user_id 必须为整数"})
            card = str(b.get("card") or "").strip()
            if not gid or not uid:
                return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 group_id 或 user_id"})
            try:
                await connector.call_action("set_group_card", {"group_id": gid, "user_id": uid, "card": card})
                log.info(jlog("action_member_card", group_id=gid, user_id=uid, card=card))
                return {"ok": True, "card": card}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

        app.add_api_route("/api/qq/group_member/card", _member_card, methods=["POST"])

        # --- 4. 用户黑名单（热生效 + 持久化）---
        # 不依赖 OneBot：直接改 Config.USER_BLACKLIST（dispatcher.allow 实时读该 set），
        # 并写回 .env 持久化；返回 requires_restart=false 告知前端无需重启即可生效。
        async def _member_blacklist(req: Request):
            b = await _read_body(req)
            try:
                uid = int(b.get("user_id") or 0)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"ok": False, "error": "user_id 必须为整数"})
            if not uid:
                return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 user_id"})
            blacklisted = bool(b.get("blacklisted", True))
            try:
                if blacklisted:
                    Config.USER_BLACKLIST.add(uid)
                else:
                    Config.USER_BLACKLIST.discard(uid)
                serialized = ",".join(str(x) for x in sorted(int(x) for x in Config.USER_BLACKLIST))
                apply_config({"USER_BLACKLIST": serialized})
                log.info(jlog("action_user_blacklist", user_id=uid, blacklisted=blacklisted, size=len(Config.USER_BLACKLIST)))
                return {"ok": True, "blacklisted": blacklisted, "requires_restart": False}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

        app.add_api_route("/api/qq/group_member/blacklist", _member_blacklist, methods=["POST"])

        # --- 5. 全员禁言（set_group_whole_ban）---
        async def _whole_ban(req: Request):
            if not connector.online:
                return _offline_json()
            b = await _read_body(req)
            try:
                gid = int(b.get("group_id") or 0)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"ok": False, "error": "group_id 必须为整数"})
            if not gid:
                return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 group_id"})
            enable = bool(b.get("enable", True))
            try:
                await connector.call_action("set_group_whole_ban", {"group_id": gid, "enable": enable})
                log.info(jlog("action_whole_ban", group_id=gid, enable=enable))
                return {"ok": True, "enabled": enable}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

        app.add_api_route("/api/qq/group/whole_ban", _whole_ban, methods=["POST"])

        # --- 6. 主动发消息（send_group_msg / send_private_msg）---
        async def _send_msg(req: Request):
            if not connector.online:
                return _offline_json()
            b = await _read_body(req)
            message = str(b.get("message") or "").strip()
            if not message:
                return JSONResponse(status_code=400, content={"ok": False, "error": "消息内容为空"})
            scene = str(b.get("scene") or "group")
            try:
                if scene == "private":
                    uid = int(b.get("user_id") or 0)
                    if not uid:
                        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 user_id"})
                    params = {"user_id": uid, "message": message}
                    action = "send_private_msg"
                else:
                    gid = int(b.get("group_id") or b.get("peer_id") or 0)
                    if not gid:
                        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 group_id"})
                    params = {"group_id": gid, "message": message}
                    action = "send_group_msg"
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"ok": False, "error": "group_id/user_id 必须为整数"})
            try:
                ret = await connector.call_action(action, params)
                msg_id = (ret or {}).get("message_id") if isinstance(ret, dict) else None
                log.info(jlog("action_send_msg", scene=scene, action=action, message_len=len(message)))
                return {"ok": True, "message_id": msg_id}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

        app.add_api_route("/api/qq/send_msg", _send_msg, methods=["POST"])

        # --- 7. 撤回消息（delete_msg）---
        async def _recall_msg(req: Request):
            if not connector.online:
                return _offline_json()
            b = await _read_body(req)
            try:
                mid = int(b.get("message_id") or 0)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"ok": False, "error": "message_id 必须为整数"})
            if not mid:
                return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 message_id"})
            try:
                await connector.call_action("delete_msg", {"message_id": mid})
                log.info(jlog("action_recall_msg", message_id=mid))
                return {"ok": True, "message_id": mid}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

        app.add_api_route("/api/qq/recall_msg", _recall_msg, methods=["POST"])
        log.info(jlog("history_action_routes_registered", path="/api/qq/{ban,kick,card,blacklist,whole_ban,send_msg,recall_msg}"))

        log.info(jlog("history_routes_registered", path="/api/qq/history"))
    except Exception as e:  # 路由注册失败绝不应拖垮消息处理
        log.error(jlog("history_routes_register_failed", error=str(e)))


async def _on_group_increase(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
    """群成员增加事件：机器人自己进群发开场白；群里有新人加入则腹黑欢迎。

    类型注解必须是 OneBot 具体类型（同 _timed_handle_message 的原因）：
    官方通道的进群事件是另一个类，靠类型注解天然被过滤掉，不会走到这里。
    """
    ctx = {"user_id": event.user_id, "group_id": event.group_id}
    if str(event.user_id) == str(bot.self_id):
        # 机器人自己被拉进群
        log.info(jlog("group_join_greeting", group_id=event.group_id, self_id=bot.self_id))
        await _say(ctx, Config.GROUP_JOIN_REPLY)
    else:
        # 群里有新人加入，腹黑欢迎（群内 @新人）
        log.info(jlog("group_member_join", group_id=event.group_id, user_id=event.user_id))
        await _say(ctx, Config.GROUP_WELCOME_REPLY, at_user=bool(ctx["group_id"]))


def _ctx(ev) -> dict:
    """构造发送上下文。

    带 ``channel`` 字段：media 层据此选择用哪个协议端回传（双轨并行的关键）。
    缺省 "napcat" 保证所有既有调用点行为不变。
    """
    return {
        "user_id": ev.user_id,
        "group_id": ev.group_id,
        "channel": getattr(ev, "channel", "napcat"),
    }


# ---- 表情包（抽象搞笑活人感）：每次对话随机发一张 ----
STICKERS: list[str] = []
# 同用户最近一次发表情包时间戳（秒），用于 cooldown 防连发
_last_sticker_at: dict[int, float] = {}


def _load_stickers() -> None:
    """扫描本地表情包库目录，缓存图片路径列表。"""
    global STICKERS
    d = Config.STICKER_DIR
    if not d or not os.path.isdir(d):
        STICKERS = []
        return
    exts = (".gif", ".png", ".jpg", ".jpeg", ".webp")
    STICKERS = [
        os.path.join(d, f)
        for f in os.listdir(d)
        if f.lower().endswith(exts) and not f.startswith("_")
    ]
    log.info(jlog("stickers_loaded", count=len(STICKERS), dir=d))


async def _send_burst(ctx: dict, text: str, mood: str | None = None) -> None:
    """拟人连发（2026-10-19 起为 **MaiMBot 真源码搬运版**，非仿制）。

    算法来源 github.com/Ruins996/MaiMBot（message_sender.py / utils.py /
    bot.py / typo_generator.py，逻辑逐行不变，仅人设文案适配）：
      ① process_llm_response：>300 字摆烂守卫 → 错别字注入（3% 单字同音 /
         20% 声调 / 2% 整词，MaiMBot 线上值）→ 概率拆条（强度按文本长度
         0.3/0.7/0.9；`！？` 按强度删、`。` 按 1-强度 删；**真正断句只在逗号
         和空格**，各自按强度概率断——句号永不拆条，真人也是一逗一句喘口气）；
      ② 逐条发送，每条前 sleep(calculate_typing_time(本条))：中文 0.2s/字、
         其它 0.1s/字、封顶 AI_TYPING_MAX（叠加媒体层自带的 1-3s 随机延迟，
         合起来就是「先愣一会 + 按字数打字」）；
      ③ 表情包是**文字流末尾独立一条**（MaiMBot bot.py 里 is_emoji 队列
         消息的同款位置），按 AI_BURST_STICKER_CHANCE / marker 心情决定。

    与仿制版的差别：延迟从「拍脑袋 rand(1.2,3.0)」换成按字数计；拆条从
    「标点必断」换成概率强度制；新增错别字注入；表情包从中间夹改流末尾
    （MaiMBot 真实行为）。
    """
    text = (text or "").strip()
    if not text:
        return

    async def _single(body: str) -> None:
        await send_text(ctx, body)
        if Config.AI_STICKER_SMART:
            await maybe_send_sticker(ctx, mood=mood, force=bool(mood))
        else:
            await maybe_send_sticker(ctx)  # 关智能=老行为（纯随机旧库+25%概率）

    # 连发总闸 / 意愿闸（MaiMBot 无意愿闸，此处按「偶尔连发」诉求保留）
    if not Config.AI_BURST_ENABLE or random.random() >= max(
            0.0, min(1.0, float(Config.AI_BURST_CHANCE))):
        await _single(text)
        return
    parts = process_llm_response(text)
    if len(parts) <= 1:
        await _single(parts[0] if parts else text)
        return
    # 连发：逐条 + 按长度打字延迟（叠加媒体层 1-3s 随机延迟）
    cap = max(0.5, float(Config.AI_TYPING_MAX))
    for part in parts:
        await asyncio.sleep(min(cap, calculate_typing_time(
            part, Config.AI_TYPING_CN_TIME, Config.AI_TYPING_EN_TIME)))
        await send_text(ctx, part)
    # 表情包：文字流末尾独立一条（MaiMBot is_emoji 队列消息位置）
    if Config.AI_STICKER_SMART:
        if mood or random.random() < max(0.0, min(1.0, float(Config.AI_BURST_STICKER_CHANCE))):
            await maybe_send_sticker(ctx, mood=mood, force=True)
    else:
        await maybe_send_sticker(ctx)
    log.info(jlog("ai_burst_sent", parts=len(parts), mood=mood or ""))


def _persona_sticker_map() -> dict:
    """当前人设情绪→表情分类映射（配置优先，空/坏则用内置本喵默认）。"""
    try:
        m = parse_persona_map(Config.AI_STICKER_MAP)
        return m or DEFAULT_PERSONA_MAP
    except Exception:  # noqa: BLE001
        return DEFAULT_PERSONA_MAP


def _sticker_system_hint() -> str | None:
    """智能表情开启时注入系统提示的「自主发表情包」协议。

    参考 Funny1Potato/nonebot-plugin-aigf-master 的「AI 自主决定发表情包」：
    让模型自己在回复末尾打 [表情:分类] marker，程序只执行与兜底。
    """
    if not Config.AI_STICKER_SMART:
        return None
    cats = "、".join(STICKER_CATS[:-1])  # 不含"通用"
    return (
        "表情包协议：如果此刻真想配一张表情包，在回复最末尾单独一行写 "
        "[表情:分类]（分类只能从这些选：%s）；不想发就不写，别勉强——"
        "简短闲聊大部分时候不用发。人设一致性：你是腹黑阴阳怪气的猫管家，"
        "生气时发「阴阳怪气」或「无语」，绝不会发怒图；开心才发「开心/搞笑」。" % cats
    )


async def maybe_send_sticker(ctx: dict, mood: str | None = None,
                             force: bool = False) -> None:
    """随对话附一张表情包（活人感）。智能模式按人设语境选图；概率/冷却防刷屏。

    mood:  情绪关键词（AI 模式由大模型 marker 声明；None=不带情绪）；
    force: 调用方明确要发（AI marker）→ 跳过概率判定，冷却仍然生效。
    """
    if not force and random.random() > Config.STICKER_CHANCE:
        return
    # 同用户 cooldown：避免连发刷屏（2026-09-04）
    uid = int(ctx.get("user_id") or 0)
    now = time.time()
    last = _last_sticker_at.get(uid, 0.0)
    if now - last < Config.STICKER_COOLDOWN:
        return
    # 选图：智能库（人设映射 + 频率加权）优先，失败回落旧随机库
    entry = None
    if Config.AI_STICKER_SMART:
        try:
            entry = sticker_lib.pick(mood, _persona_sticker_map())
        except Exception as e:  # noqa: BLE001 — 选图失败绝不挡发送
            log.warning(jlog("sticker_pick_failed", error=type(e).__name__))
    path = entry.file if entry is not None else (random.choice(STICKERS) if STICKERS else "")
    if not path:
        return
    try:
        # 本地文件转 base64:// 直发，彻底不依赖 NapCat 对裸绝对路径的读取
        if os.path.isfile(path):
            with open(path, "rb") as f:
                payload = "base64://" + base64.b64encode(f.read()).decode("ascii")
        else:
            payload = path
        await send_image(ctx, payload, at_user=False)  # 表情包不 @ 人（2026-09-04）
        _last_sticker_at[uid] = now
        if entry is not None:
            try:
                sticker_lib.record_use(entry)  # 使用频率累计（重排加权用）
            except Exception:  # noqa: BLE001
                pass
        log.info(jlog("sticker_sent", mood=mood or "",
                      cat=entry.cat if entry is not None else "random"))
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("sticker_send_failed", detail=str(e)[:160]))


async def _say(ctx: dict, text: str) -> None:
    """发文字 + 随机表情包（统一出口，替代散落的 send_text）。"""
    await send_text(ctx, text)
    await maybe_send_sticker(ctx)


# 注意：此处**不使用** @msg_matcher.handle() 装饰器 —— 注册由 register() 显式完成，
# 否则模块级求值会因 msg_matcher 为 None 而报 AttributeError。
async def handle_message(bot: Bot, event: MessageEvent) -> None:
    trace_id = new_trace_id()
    # 智能表情包收集（2026-10-19）：群消息里的表情包自动入库为己用。
    # 必须在 normalize 之前——未 @机器人 的群消息会被 normalize 静默丢弃，
    # 而那些消息里恰恰最多表情包。fire-and-forget：收集是旁路，绝不拖垮消息处理。
    if str(getattr(event, "message_type", "") or "") == "group" and Config.AI_STICKER_COLLECT:
        try:
            asyncio.ensure_future(collect_group_stickers(event))
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("sticker_collect_spawn_failed", error=type(e).__name__))
    # 对话历史（旁路，2026-09-07）：**先落盘再走门禁**。
    # 必须在 normalize() 之前记——normalize 会把"未 @机器人"的群消息丢掉，
    # 而页面要像 QQ 一样展示群里所有人说了什么，被丢弃的也要看得见。
    # 用 to_thread 包裹 sqlite 写，避免阻塞事件循环；失败只记日志不影响业务。
    try:
        await asyncio.to_thread(history.record_incoming, event, trace_id, str(bot.self_id))
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("history_record_failed", error=type(e).__name__, detail=str(e)[:120]))

    ev = normalize(event, self_id=str(bot.self_id), trace_id=trace_id)
    if ev is None:
        return  # 群消息未 @机器人且无命令前缀 → 静默丢弃（§3.2）
    await _route(ev, trace_id)


def _parse_ai_cmd(raw: str) -> str | None:
    """识别 AI 模式命令，归一化为 on/off/toggle/clear/status/game/help。

    非本命令返回 None（放行给后续路由）。纯函数、不碰 I/O（可单测）。
    支持形式：`/ai on`、`/ai off`、`/ai`、`ai模式开`、`开启ai模式`、`关闭ai模式` 等。
    注意词边界：`/air`、`ai模式党` 之类不是本命令（返回 None），不误吞正常对话。
    """
    t = " ".join((raw or "").strip().lower().replace("　", " ").split())
    if not t:
        return None
    arg: str | None = None
    if t == "/ai" or t.startswith("/ai "):
        arg = t[len("/ai"):].strip()
    elif t == "ai模式" or t.startswith("ai模式"):
        arg = t[len("ai模式"):].strip()
    elif t in ("开启ai模式", "打开ai模式", "启用ai模式"):
        arg = "on"
    elif t in ("关闭ai模式", "关掉ai模式", "禁用ai模式"):
        arg = "off"
    if arg is None:
        return None
    if arg in ("", "状态", "status", "state"):
        return "status"
    if arg in ("on", "开", "开启", "打开", "启用", "enable"):
        return "on"
    if arg in ("off", "关", "关闭", "关上", "关掉", "禁用", "disable"):
        return "off"
    if arg in ("清空", "clear", "重置", "reset"):
        return "clear"
    if arg in ("切换", "toggle", "翻转"):
        return "toggle"
    if arg in ("game", "游戏") or arg.startswith("game ") or arg.startswith("游戏"):
        return "game"
    if arg in ("sticker", "表情") or arg.startswith("sticker ") or arg.startswith("表情"):
        return "sticker"
    if arg in ("proactive", "主动") or arg.startswith("proactive ") or arg.startswith("主动"):
        return "proactive"
    return "help"


def _ai_proactive_arg(ev) -> str:
    """从原始消息取 `/ai proactive` 后的参数（on/off/空）。"""
    t = (ev.raw_text or "").strip()
    low = t.lower()
    for marker in ("/ai proactive", "/ai 主动", "ai模式主动"):
        i = low.find(marker.lower())
        if i >= 0:
            return t[i + len(marker):].strip()
    return ""


async def _handle_ai_proactive_cmd(ev, trace_id: str) -> None:
    """/ai proactive [on|off] —— 主动发言开关/状态（仅主人，随 /ai 门禁）。"""
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key
    arg = _ai_proactive_arg(ev).strip().lower()
    if arg in ("off", "关", "关闭"):
        proactive_engine.set_off(scope, True)
        await _reply(ctx, sess, "这个%s的 AI 主动发言已关——本喵之后只搭话不主动，"
                                "除非你喊「/ai proactive on」。" % ("群" if ev.is_group else "私聊"),
                     title="AI 主动·关")
        return
    if arg in ("on", "开", "开启"):
        proactive_engine.set_off(scope, False)
        await _reply(ctx, sess, "这个%s的 AI 主动发言已开——冷场了本喵会自己找话题，"
                                "早晚安也照旧。" % ("群" if ev.is_group else "私聊"),
                     title="AI 主动·开")
        return
    st = proactive_engine.state(scope)
    lines = [
        "主动发言：%s" % ("关" if proactive_engine.is_off(scope) else "开"),
        "今日主动：%d/%d 次 ｜ 能量：%.0f/100" % (
            st.proactive_today, max(0, int(Config.AI_PROACTIVE_DAILY_MAX)), st.energy),
        "时段：%s ｜ 最小间隔：%ds ｜ 日上限：%d" % (
            Config.AI_PROACTIVE_HOURS, int(Config.AI_PROACTIVE_MIN_INTERVAL),
            int(Config.AI_PROACTIVE_DAILY_MAX)),
        "沉默阈值：平均发言间隔 × %.1f（%ds~%ds）" % (
            float(Config.AI_PROACTIVE_IDLE_FACTOR),
            int(Config.AI_PROACTIVE_IDLE_MIN), int(Config.AI_PROACTIVE_IDLE_MAX)),
        "命令：/ai proactive on|off",
    ]
    await _reply(ctx, sess, "\n".join(lines), title="AI 主动·状态")


def _ai_sticker_arg(ev) -> str:
    """从原始消息取 `/ai sticker` 后的参数（classify / 空）。"""
    t = (ev.raw_text or "").strip()
    low = t.lower()
    for marker in ("/ai sticker", "/ai 表情", "ai模式表情", "ai模式 sticker"):
        i = low.find(marker.lower())
        if i >= 0:
            return t[i + len(marker):].strip()
    return ""


async def _handle_ai_sticker_cmd(ev, trace_id: str) -> None:
    """/ai sticker [classify] —— 表情库状态 / 立即分类（仅主人，随 /ai 门禁）。"""
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    arg = _ai_sticker_arg(ev).strip().lower()
    if arg in ("classify", "分类", "识别"):
        n = await sticker_lib.classify_pending(vlm=True)
        await _reply(ctx, sess,
                     "表情包分类完成：OCR 认字 %d 张，VLM 识图 %d 张，失败 %d 张。\n"
                     "（VLM 默认关闭，只跑本地 OCR；要识图分类去「机器人配置 → "
                     "常规·AI 模式」开 VLM 并填视觉模型）"
                     % (n["ocr"], n["vlm"], n["failed"]), title="AI 表情·分类")
        return
    st = sticker_lib.stats()
    by_cat = "，".join("%s×%d" % (k, v) for k, v in
                       sorted(st["by_cat"].items(), key=lambda x: -x[1]))
    lines = [
        "表情库：共 %d 张（内置 %d + 群内收集 %d）" % (st["total"], st["builtin"], st["collected"]),
        "待分类：%d 张（「/ai sticker 分类」立即分类）" % st["pending"],
        "分布：%s" % (by_cat or "无"),
        "智能选用：%s ｜ 自动收集：%s ｜ VLM 识图：%s" % (
            "开" if Config.AI_STICKER_SMART else "关",
            "开" if Config.AI_STICKER_COLLECT else "关",
            "开" if Config.AI_STICKER_VLM else "关"),
    ]
    await _reply(ctx, sess, "\n".join(lines), title="AI 表情·状态")


def _ai_game_arg(ev) -> str:
    """从原始消息里取 `/ai game` 后面的参数（游戏名 / off / list）。"""
    t = (ev.raw_text or "").strip()
    low = t.lower()
    for marker in ("/ai game", "/ai 游戏", "ai模式游戏", "ai模式 game"):
        i = low.find(marker.lower())
        if i >= 0:
            return t[i + len(marker):].strip()
    return ""


def _detect_game_start(text: str) -> str | None:
    """自然语言开局识别：「玩成语接龙」「来个海龟汤」「接龙开局」→ 游戏名。

    纯函数（可单测）。防误触三原则：
      ① 动词必须与游戏名**相邻**（"海龟汤真好玩"不误判）；
      ② 含否定词（不玩/别玩/结束…）直接放弃；
      ③ 长句（>20 字）不判——那是闲聊不是开局。
    """
    t = (text or "").strip()
    if not t or len(t) > 20:
        return None
    if any(w in t for w in ("不玩", "别玩", "不想玩", "停止", "结束", "不猜", "不会玩", "退出")):
        return None
    for name, g in AI_GAMES.items():
        for alias in (name,) + tuple(g.get("aliases") or ()):
            if alias not in t:
                continue
            if t == alias:  # 整句就是游戏名
                return name
            for v in ("玩", "来个", "来一局", "来局", "来玩", "开始", "整一个", "开局"):
                if (v + alias) in t or (alias + v) in t:
                    return name
    return None


def _is_game_end(text: str) -> bool:
    """自然语言结束游戏识别（仅在游戏进行中调用）。"""
    t = (text or "").strip()
    if not t or len(t) > 12:
        return False
    return t in ("不玩了", "不玩啦", "结束游戏", "停止游戏", "退出游戏", "算了不玩",
                 "玩腻了", "不玩了喵", "exit", "quit") or t.endswith("不玩了")


async def _handle_ai_command(ev, action: str, trace_id: str) -> None:
    """AI 模式开关命令（2026-10-19）。**仅主人可用**；非主人静默（§8：不提示不探测）。"""
    if not _is_owner(ev.user_id):
        log.info(jlog("ai_cmd_denied", user_id=ev.user_id, group_id=ev.group_id,
                      trace_id=trace_id, action=action))
        return
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key
    where = "群" if ev.is_group else "私聊"
    if not Config.AI_MODE_ENABLE:
        await _reply(ctx, sess,
                     "AI 模式功能被总开关关掉了（AI_MODE_ENABLE=false），"
                     "去「机器人配置」页打开再使唤本喵。", title="AI 模式")
        return
    if action == "on":
        ai_mode_store.set(scope, True)
        await _reply(ctx, sess,
                     "AI 模式已开启喵——接下来这个%s里你们说什么本喵都接，"
                     "由在线大模型（%s）陪聊，默认纯文字回复。\n"
                     "· 让它闭嘴：/ai off\n"
                     "· 想看图直接说「生图 + 描述」，照常给你画\n"
                     "· / 和 # 开头的命令仍走原功能（查战甲那些不耽误）\n"
                     "· 说「玩成语接龙」「来个海龟汤」开局文字游戏，每轮有倒计时，"
                     "超时本喵会催喵\n"
                     "· 冷场了本喵会自己起话头，早晚安也一样；嫌吵喊 /ai proactive off\n"
                     "· 换 Key/模型：「机器人配置 → 常规·AI 模式」"
                     % (where, Config.AI_MODEL), title="AI 模式·开")
    elif action == "off":
        ai_mode_store.set(scope, False)
        _cancel_game_timer(scope)  # 游戏会话随之解散（/ai off = 全剧终）
        game_sessions.end(scope)
        await _reply(ctx, sess,
                     "AI 模式已关，恢复老规矩：@本喵或者带命令前缀才理你。",
                     title="AI 模式·关")
    elif action == "toggle":
        on = not ai_mode_store.is_on(scope)
        ai_mode_store.set(scope, on)
        await _reply(ctx, sess, "AI 模式%s。" % ("已开启" if on else "已关闭"), title="AI 模式")
    elif action == "clear":
        ai_chatter.history.clear(scope)
        await _reply(ctx, sess, "这个%s的 AI 聊天记忆已清空，咱们从头聊。"
                     % where, title="AI 模式·清空")
    elif action == "game":
        await _handle_ai_game_cmd(ev, trace_id)
    elif action == "sticker":
        await _handle_ai_sticker_cmd(ev, trace_id)
    elif action == "proactive":
        await _handle_ai_proactive_cmd(ev, trace_id)
    elif action == "status":
        on = ai_mode_store.is_on(scope)
        gs = game_sessions.get(scope)
        lines = [
            "当前%s：AI 模式%s" % (where, "开启中" if on else "关闭中"),
            "模型：%s（%s）" % (Config.AI_MODEL, Config.AI_API_BASE),
            "上下文：最近 %d 轮 · TTL %d 秒" % (Config.AI_HISTORY_TURNS, Config.AI_HISTORY_TTL),
        ]
        if gs:
            lines.append("游戏中：%s（每轮倒计时 %ds，已催更 %d 次）"
                         % (gs.game, int(gs.timeout), gs.nudges))
        lines.append("命令：/ai on 开 · /ai off 关 · /ai 清空 · /ai game 玩游戏 · /ai 看状态")
        await _reply(ctx, sess, "\n".join(lines), title="AI 模式·状态")
    else:  # help
        await _reply(ctx, sess,
                     "AI 模式用法（仅主人）：\n"
                     "/ai on —— 开启，这个%s里所有消息交给大模型回复（纯文字）\n"
                     "/ai off —— 关闭，恢复 @/命令 才理你\n"
                     "/ai 清空 —— 清掉这个%s的聊天记忆\n"
                     "/ai game —— 文字游戏列表（成语接龙/数字炸弹/海龟汤…）\n"
                     "/ai —— 看当前状态" % (where, where), title="AI 模式·帮助")


async def _handle_ai_game_cmd(ev, trace_id: str) -> None:
    """/ai game <游戏名>|off|list —— 文字游戏管理（仅主人，随 /ai 门禁）。"""
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key
    arg = _ai_game_arg(ev).strip()
    if not arg or arg.lower() in ("list", "列表", "有哪些", "什么"):
        lines = ["AI 模式文字游戏（直接说「玩+游戏名」也能开局）："]
        for name, g in AI_GAMES.items():
            lines.append("· %s（每轮倒计时 %ds）" % (name, int(g["timeout"])))
        lines.append("用法：/ai game 成语接龙 ｜ /ai game off 强制结束")
        await _reply(ctx, sess, "\n".join(lines), title="AI 游戏·列表")
        return
    if arg.lower() in ("off", "关", "结束", "退出", "停止"):
        if game_sessions.end(scope):
            _cancel_game_timer(scope)
            await _reply(ctx, sess, "游戏已强制收场，AI 陪聊继续。", title="AI 游戏·结束")
        else:
            await _reply(ctx, sess, "当前没有进行中的游戏喵。", title="AI 游戏·结束")
        return
    if arg not in AI_GAMES:
        await _reply(ctx, sess, "没这个游戏喵。现有的：%s" % "、".join(AI_GAMES.keys()),
                     title="AI 游戏·未知")
        return
    await _start_ai_game(ev, arg, trace_id)


async def _start_ai_game(ev, name: str, trace_id: str) -> None:
    """开局：建会话 → AI 说开场白（纯文字）→ 布防每次对话倒计时。"""
    ctx = _ctx(ev)
    scope = ev.scope_key
    g = AI_GAMES[name]
    game_sessions.start(scope, name, g["timeout"])
    reply, err = await ai_chatter.say(scope, g["intro"], ev.user_id,
                                      extra_system=g["system"])
    if err:
        game_sessions.end(scope)
        log.warning(jlog("ai_game_intro_failed", scope=scope, game=name, err=err))
        await _say(ctx, "AI 模式喘不上气了：%s，这局开不了喵。" % err)
        return
    log.info(jlog("ai_game_intro", scope=scope, game=name, reply_len=len(reply)))
    await _say(ctx, reply)
    _arm_game_timer(ev)


async def _end_ai_game(ev, gs, trace_id: str) -> None:
    """用户主动结束游戏：AI 一句告别（LLM 生成，失败降级固定文案）+ 解散会话。"""
    ctx = _ctx(ev)
    scope = ev.scope_key
    g = AI_GAMES.get(gs.game) or {}
    reply, err = await ai_chatter.say(
        scope,
        "（系统事件：群友主动结束了游戏。用你的口吻简短告别或调侃一句，"
        "一句话以内，不要提'系统'二字。）",
        ev.user_id, extra_system=g.get("system"))
    if err or not reply:
        reply = "行吧，这局就到这。想玩了再喊本喵～"
    game_sessions.end(scope)
    _cancel_game_timer(scope)
    await _say(ctx, reply)


async def _handle_ai_chat(ev, trace_id: str) -> None:
    """AI 模式本体：非命令消息 → 在线大模型**纯文字**回复。

    - 默认纯文字（`_say`）：群聊要对话感，不做图片卡绘制；
    - 特殊要求放行：显式生图词命中 → 转生图交互流（`_ask_channel_choice`）；
    - 游戏进行中：注入游戏规则 + 迟到吐槽 note，回复后重布倒计时；
    - CLOSE-02：模型故障显式告知，绝不装死；纯图无文字/冷却中静默跳过。
    """
    text = (ev.raw_text or "").strip()
    if not text:
        return  # 纯图片/表情无文字 → 不调模型
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key

    # ---- 特殊要求：显式生图 → 不走 LLM，转生图交互流（AI 模式默认纯文字）----
    kw_kind, kw_text = match_keyword(text)
    _draw_hit = kw_kind == "draw" or bool(
        re.match(r"^(画一|画张|画个|画幅|来张图|来个图|出图|生成一?张)", text))
    if _draw_hit:
        log.info(jlog("ai_chat_draw_passthrough", scope=scope,
                      user_id=ev.user_id, trace_id=trace_id))
        await _ask_channel_choice(ev, kw_text or text, trace_id)
        return

    # ---- 入站安全（与生图链路同款词表）：违规内容不送模型 ----
    ok, why = safety.check_prompt(text)
    if not ok:
        await _say(ctx, "这内容本喵可不接，换个别的吧（微笑）。")
        return

    # ---- 游戏会话：结束识别 → 开局识别 → 游戏中注入 ----
    gs = game_sessions.get(scope)
    if gs and _is_game_end(text):
        await _end_ai_game(ev, gs, trace_id)
        return
    if not gs:
        name = _detect_game_start(text)
        if name:
            await _start_ai_game(ev, name, trace_id)
            return

    note = None
    extra_system = None
    if gs:
        g = AI_GAMES.get(gs.game) or {}
        extra_system = g.get("system")
        if gs.nudges > 0:
            # 迟到回复：让 AI 先吐槽一句再接话（活人感，不死板）
            note = ("（系统事件：群友超时 %d 秒后才回复，先吐槽一句再接话，"
                    "不要提'系统'二字。）" % int(gs.timeout * gs.nudges))
            gs.nudges = 0
    # 智能表情协议注入（AI 自主决定发不发、发什么情绪；人设语气指导）
    _sticker_sys = _sticker_system_hint()
    if _sticker_sys:
        extra_system = (extra_system + "\n" + _sticker_sys) if extra_system else _sticker_sys
    # ---- M1 记忆系统（2026-10-20）：对话前注入相关记忆（画像/长期事件/群档案）----
    # recall 失败不阻断对话（manager 内部已挡：记日志+本轮无记忆）。
    if Config.MEMORY_ENABLE:
        try:
            _mem_text = memory_manager.recall_text(scope, ev.user_id, query=text)
            if _mem_text:
                extra_system = ((extra_system + "\n\n" + _mem_text)
                                if extra_system else _mem_text)
        except Exception as e:  # noqa: BLE001 — CLOSE-02 记而不吞
            log.warning(jlog("memory_recall_error", scope=scope,
                             error=type(e).__name__, detail=str(e)[:160],
                             trace_id=trace_id))
    # ---- M3 情感/关系/日程（2026-10-20）：注入「此刻心情 + 对这个人的亲疏
    # + 他接下来的安排」。语气由模型决定（personification 原则：代码只给事实）。
    if Config.EMO_ENABLE:
        try:
            _emo_text = emotion_manager.prompt(scope, ev.user_id)
            if _emo_text:
                extra_system = ((extra_system + "\n\n" + _emo_text)
                                if extra_system else _emo_text)
        except Exception as e:  # noqa: BLE001 — CLOSE-02 记而不吞
            log.warning(jlog("emotion_prompt_error", scope=scope,
                             error=type(e).__name__, detail=str(e)[:160],
                             trace_id=trace_id))
    # ---- M2 工具总线（2026-10-20）：开关开且已有工具注册 → FC 多回合路径；
    # 否则（关/无工具/注册失败）走原纯对话路径。这是路由选择不是兜底降级——
    # 两条路径能力差异只在「能不能调工具」，纯对话行为零变化。
    _fc_runner = None
    if Config.AI_TOOLS_ENABLE:
        try:
            from .tools import fc as _tools_fc
            _runner_cand = _tools_fc.get_runner()
            if _runner_cand.registry.enabled_tools():
                _fc_runner = _runner_cand
        except Exception as e:  # noqa: BLE001 — 总线异常不拖垮聊天（CLOSE-02 记而不吞）
            log.warning(jlog("ai_chat_tools_unavailable", scope=scope,
                             error=type(e).__name__, detail=str(e)[:160],
                             trace_id=trace_id))
    _t0 = time.perf_counter()
    if _fc_runner is not None:
        # 当前时间注入（模型把「N 分钟后」算成 fire_at 绝对时间戳用）
        extra_system = ((extra_system + "\n" + _tools_time_hint())
                        if extra_system else _tools_time_hint())
        from .tools import ToolContext as _ToolCtx
        _tctx = _ToolCtx(scope=scope, user_id=ev.user_id, group_id=ev.group_id,
                         is_private=not ev.is_group,
                         is_owner=_is_owner(ev.user_id),
                         trace_id=trace_id,
                         channel=getattr(ev, "channel", "napcat"))
        reply, err = await ai_chatter.chat_tools(
            scope, text, ev.user_id, extra_system=extra_system, note=note,
            converse=_fc_runner.converse, tool_ctx=_tctx)
    else:
        reply, err = await ai_chatter.chat(scope, text, ev.user_id,
                                           extra_system=extra_system, note=note)
    _ms = int((time.perf_counter() - _t0) * 1000)
    if err == "cooldown":
        log.info(jlog("ai_chat_cooldown", scope=scope, user_id=ev.user_id,
                      trace_id=trace_id))
        return  # 冷却中静默：回「等一下」本身就是刷屏
    if err == "no_key":
        await _reply(ctx, sess,
                     "还没配 AI 的 API Key 喵——去「机器人配置 → 常规·AI 模式」"
                     "填上 base_url / key / 模型（OpenAI 兼容协议，DeepSeek、"
                     "Moonshot、百炼、智谱都能用）再使唤本喵。",
                     title="AI 模式·未配置")
        return
    if err:
        log.warning(jlog("ai_chat_failed", scope=scope, user_id=ev.user_id,
                         err=err, trace_id=trace_id))
        await _reply(ctx, sess,
                     "AI 模式喘不上气了：%s\n（去「机器人配置 → 常规·AI 模式」"
                     "检查 API Key 与地址）" % err, title="AI 模式·故障")
        return
    if gs:
        gs.turns += 1
        _arm_game_timer(ev)  # 每回一手 → 重布倒计时（旧任务取消，防叠加）
    # ---- M1 记忆系统（2026-10-20）：对话后异步抽取（不阻塞回复）----
    # 后台 task 跑 ingest（LLM 抽取 facts/events）；失败只在 task 内记日志，
    # 不影响用户已收到的回复。抽取不到东西是正常态（静默）。
    if Config.MEMORY_ENABLE and Config.MEMORY_EXTRACT and not err:
        try:
            asyncio.ensure_future(_memory_ingest_bg(scope, ev.user_id, text, reply))
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("memory_ingest_spawn_failed", scope=scope,
                             error=type(e).__name__))
    # ---- M3 情感/日程（2026-10-20）：对话后异步更新（不阻塞回复）----
    # 后台跑日程抽取（对话里听到的未来安排）。情感/好感度变化不在本处自动判
    # ——要模型判定得再调一次 LLM，成本高且易抖；改由 /情感 命令与到期关心
    # 驱动，或后续接入情绪判定模型时再打开（如实记录：一期不自动判情感）。
    if Config.EMO_ENABLE and Config.EMO_SCHEDULE_EXTRACT and not err:
        try:
            asyncio.ensure_future(_emotion_update_bg(scope, ev.user_id, text))
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("emotion_update_spawn_failed", scope=scope,
                             error=type(e).__name__))
    # reply 已被 chat() 剥掉 [表情:分类] marker；mood 从 last_mood 取
    mood = ai_chatter.last_mood.get(scope)
    # ---- 连发（概率拆 2~3 条 + 条间拟人延迟 + 中间夹表情包）----
    await _send_burst(ctx, reply, mood)
    log.info(jlog("ai_chat_done", scope=scope, user_id=ev.user_id,
                  reply_len=len(reply), elapsed_ms=_ms, trace_id=trace_id,
                  game=gs.game if gs else "", mood=mood or ""))


#: AI 游戏倒计时看门狗（per-scope 一个 asyncio 任务；重启 bridge 即清，可接受）
_ai_game_timers: dict[str, "asyncio.Task"] = {}


def _cancel_game_timer(scope: str) -> None:
    """取消并移除某 scope 的看门狗任务（幂等）。"""
    t = _ai_game_timers.pop(scope, None)
    if t is not None and not t.done():
        t.cancel()


def _arm_game_timer(ev) -> None:
    """给当前 scope 的游戏对话布防倒计时（取消旧任务、启新看门狗）。

    看门狗两段（调研搬运：luojisama/nonebot-plugin-personification 的
    「状态驱动主动表达」+「过期提交拦截」；延迟公式参考 Chaonaoo/QQ_bot）：
      ① timeout 到点 → AI 催更（不耐烦人设，发送前复查会话有效性）；
      ② 再拖 2×timeout → AI 收局（阴阳怪气收官，解散会话）。
    用户任意回复 / 游戏结束都会取消本任务，不会误触发。
    """
    scope = ev.scope_key
    _cancel_game_timer(scope)
    gs = game_sessions.get(scope)
    if gs is None:
        return
    ctx = _ctx(ev)
    uid = ev.user_id

    async def _watchdog() -> None:
        try:
            await asyncio.sleep(gs.timeout)
            if game_sessions.get(scope) is not gs:
                return  # 过期提交拦截：会话已被换/解散 → 放弃
            await _ai_game_nudge(ctx, uid, scope, gs, kind="nudge")
            await asyncio.sleep(gs.timeout * 2)
            if game_sessions.get(scope) is not gs:
                return
            await _ai_game_nudge(ctx, uid, scope, gs, kind="end")
        except asyncio.CancelledError:
            pass  # 用户回了/游戏结束 → 正常取消
        except Exception as e:  # noqa: BLE001 — 看门狗绝不拖垮任何东西
            log.warning(jlog("ai_game_watchdog_error", scope=scope,
                             err=type(e).__name__, detail=str(e)[:160]))

    _ai_game_timers[scope] = asyncio.create_task(_watchdog())


async def _ai_game_nudge(ctx: dict, uid, scope: str, gs, kind: str) -> None:
    """倒计时到点的 AI 特殊回复：催更 / 收局（全程 LLM 生成，拒绝死板文案）。"""
    g = AI_GAMES.get(gs.game) or {}
    if kind == "nudge":
        gs.nudges += 1
        instruction = (
            "（系统事件：群友已经 %d 秒没接话了。用你不耐烦的猫管家口吻催一下，"
            "比如「人呢」「掉线了？」，一句话以内，不要提'系统'，不要重复游戏规则。）"
            % int(gs.timeout))
    else:
        instruction = (
            "（系统事件：群友超时太久，这局游戏作废。用阴阳怪气的口吻宣布游戏结束，"
            "放句狠话收场，一句话以内，不要提'系统'二字。）")
    # 拟人打字延迟（Chaonaoo/QQ_bot 公式改编）：催更前像在"打字"
    try:
        await asyncio.sleep(min(3.0, 0.8 + random.uniform(0.3, 1.2)))
    except Exception:  # noqa: BLE001
        pass
    reply, err = await ai_chatter.say(scope, instruction, uid, extra_system=g.get("system"))
    if err:
        # 模型故障：收局 + 显式告知一次（CLOSE-02），不静默吊着这群
        game_sessions.end(scope)
        _cancel_game_timer(scope)
        log.warning(jlog("ai_game_nudge_failed", scope=scope, kind=kind, err=err))
        await _say(ctx, "AI 模式喘不上气了：%s，这局先算了吧喵。" % err)
        return
    log.info(jlog("ai_game_%s" % kind, scope=scope, game=gs.game, reply_len=len(reply)))
    await _say(ctx, reply)
    if kind == "end":
        game_sessions.end(scope)
        _cancel_game_timer(scope)


# ===========================================================================
# AI 模式主动发言（2026-10-19）：没人说话也自己搭话，活群友
# 调研搬运：nyaturingtest-enhanced（时段/日上限/两成闸门/不自说自话）、
# learning-chat（自适应沉默阈值）、proactive_talk_plugin（LLM 最终判断可回「跳过」）、
# personification（早晚问候）、aigf-master（社交能量周期）。
# ===========================================================================

def _proactive_ctx(scope: str) -> dict:
    """scope key → 发送上下文（群发到群，私聊发到人）。"""
    if scope.startswith("g:"):
        return {"group_id": int(scope[2:]), "user_id": 0, "channel": "napcat"}
    return {"group_id": 0, "user_id": int(scope[2:]), "channel": "napcat"}


def _proactive_candidates() -> list:
    """候选会话：AI 模式开着的 scope × 历史里有动静的会话。"""
    on = set(ai_mode_store.on_scopes())
    if not on:
        return []
    out = []
    try:
        convs = history.list_conversations(limit=50)
    except Exception as e:  # noqa: BLE001 — 枚举失败只记日志，下轮再来
        log.warning(jlog("proactive_conv_list_failed", error=type(e).__name__))
        return []
    for cv in convs:
        scope = scope_of(cv.get("scene") or "", int(cv.get("peer_id") or 0))
        if scope in on:
            out.append(cv)
    return out


async def _proactive_speak(scope: str, act, kind: str) -> None:
    """主动发言：拉群上下文 → LLM 生成（可回「跳过」）→ 发送 → 记账。

    CLOSE-02：生成失败/模型说「跳过」→ 静默不发（主动发言是锦上添花，
    绝不在半夜刷错误）；但会记日志。
    """
    ctx = _proactive_ctx(scope)
    idle_min = int(act.idle_sec // 60)
    ctx_text = "\n".join(act.recent_texts[-8:]) or "（最近没什么实质内容）"
    if kind == "greet":
        slot_name = {"morning": "早上", "noon": "中午", "evening": "晚上"}.get(
            act.greet_slot, "这会儿")
        instruction = (
            "（系统事件：现在是%s，这个群安静好一会了。用你的人格跟大家%s好，"
            "一两句话，自然点别肉麻。如果这个点不适合打招呼就只回：跳过。）"
            % (slot_name, slot_name))
    else:
        instruction = (
            "（系统事件：群里已经安静 %d 分钟了。最近的聊天内容：\n%s\n\n"
            "用你的人格自然起个话头，或者接着刚才的话题聊一句，像群友随口一说，"
            "一两句话就好。如果实在没什么好说的就只回两个字：跳过。"
            "想配一张表情包的话，在末尾单独一行写 [表情:分类]"
            "（分类：开心/搞笑/愤怒/无语/阴阳怪气/安慰/庆祝/疑问/爱心/劳累），"
            "不想发就不写。）"
            % (idle_min, ctx_text))
    _t0 = time.perf_counter()
    reply, err = await ai_chatter.say(scope, instruction, 0)
    if err:
        log.warning(jlog("proactive_gen_failed", scope=scope, kind=kind, err=err))
        return
    if not reply or reply.strip() in ("跳过", "跳过。", "跳過"):
        log.info(jlog("proactive_skipped", scope=scope, kind=kind,
                      elapsed_ms=int((time.perf_counter() - _t0) * 1000)))
        return
    # 主动发言也走连发（真人暖场也是分一两条发的，偶尔带图）
    mood = ai_chatter.last_mood.get(scope)
    await _send_burst(ctx, reply, mood)
    if kind == "greet":
        proactive_engine.mark_greeted(scope, act.greet_slot)
    else:
        proactive_engine.mark_proactive(scope, Config.AI_PROACTIVE_ENERGY_COST)
    log.info(jlog("proactive_sent", scope=scope, kind=kind,
                  reply_len=len(reply),
                  elapsed_ms=int((time.perf_counter() - _t0) * 1000),
                  idle_min=idle_min))


async def _proactive_loop() -> None:
    """主动发言 tick 循环（60s 一拍；启动时由 _on_startup 拉起）。"""
    log.info(jlog("proactive_loop_started", interval=60))
    while True:
        try:
            await asyncio.sleep(60)
            proactive_engine.roll_energy()
            for cv in _proactive_candidates():
                scene = cv.get("scene") or ""
                peer = int(cv.get("peer_id") or 0)
                scope = scope_of(scene, peer)
                if proactive_engine.is_off(scope):
                    continue
                # 活跃度从 history 算（单一事实来源，重启不丢）
                msgs = await asyncio.to_thread(
                    history.list_messages, scene, peer, 50)
                act = compute_activity(scene, peer, msgs)
                act.greet_slot = None
                # ① 到点问候（早/午/晚，每日每段一次）
                slot = proactive_engine.greet_due(act)
                if slot:
                    act.greet_slot = slot
                    await _proactive_speak(scope, act, "greet")
                    continue
                # ② 冷场起话头（全闸门 + 能量 roll）
                ok, reason = proactive_engine.should_consider(act)
                if ok and proactive_engine.willing(scope):
                    await _proactive_speak(scope, act, "topic")
                elif ok:
                    log.info(jlog("proactive_energy_low", scope=scope,
                                  energy=round(proactive_engine.state(scope).energy, 1)))
                await asyncio.sleep(2)  # 错峰：避免连续 LLM 调用
        except asyncio.CancelledError:
            log.info(jlog("proactive_loop_stopped"))
            break
        except Exception as e:  # noqa: BLE001 — 循环绝不能死
            log.warning(jlog("proactive_loop_error", error=type(e).__name__,
                             detail=str(e)[:160]))
            await asyncio.sleep(60)


# ===========================================================================
# 宿主机管家（2026-10-20 M6）：/pc 命令族 + 自然语言直通车 + 高危确认流
# 安全硬规则（roadmap 10.1）：仅私聊 + 仅主人——群聊调用泄露的是主人硬件配置。
# 查询类（体检/top/端口/温度/截图/目录）与操作类（kill）分离：
#   查询类主人私聊即可；操作类额外走「回复确认」二次确认（30s 有效）。
# ===========================================================================

#: 高危操作待确认：user_id → (kind, target, expire_ts)
_pc_pending: dict[int, tuple[str, str, float]] = {}
_PC_CONFIRM_TTL = 30.0
#: 电源操作 5s 倒计时期间用户的取消意图
_pc_power_cancel: dict[int, bool] = {}


def _pc_guard(ev) -> bool:
    """PC 管家鉴权（roadmap 10.1 硬规则）。群聊/非主人一律拒绝并留痕。"""
    ok, reason = pc_mod.allowed(
        is_private=(not ev.is_group), user_id=ev.user_id,
        is_owner=_is_owner(ev.user_id))
    if ok:
        return True
    log.info(jlog("pc_denied", reason=reason, user_id=ev.user_id,
                  group_id=ev.group_id))
    return False


def _pc_disk_allowed(path: str) -> bool:
    """目录浏览白名单校验（PC_DISK_ROOTS，分号分隔；防遍历整盘）。"""
    roots = [r.strip() for r in str(getattr(Config, "PC_DISK_ROOTS", "") or "").split(";") if r.strip()]
    if not roots:
        return False
    try:
        target = Path(path).resolve()
        return any(str(target).lower().startswith(Path(r).resolve().as_posix().lower())
                   or Path(r).resolve() in target.parents or target == Path(r).resolve()
                   for r in roots)
    except Exception:  # noqa: BLE001 — 解析失败一律不放行
        return False


def _is_blackish(png: bytes) -> bool:
    """启发式：PNG 是否基本全黑（DXGI/mss 在黑屏/服务化场景下会截到全黑）。"""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(png)).convert("RGB")
        px = list(im.getdata())
        if not px:
            return True
        return all(r == 0 and g == 0 and b == 0 for (r, g, b) in px[:20000])
    except Exception:  # noqa: BLE001 — 解析失败当非黑，交给上层按大小校验
        return False


async def _pc_shot_bytes() -> tuple[bytes | None, str]:
    """M6 截图方法选择（WGC/DXGI 黑屏修复，2026-10-20）。

    按 Config.PC_SCREENSHOT_METHOD：
      - dxgi（默认）：DXGI Desktop Duplication，捕获桌面合成器输出（含 GPU 全屏内容，不黑屏）；
        失败或截到全黑即显式报错，不做任何降级 / 回退。
      - mss：传统 mss（已知 GPU 全屏内容会黑屏，仅在 DXGI 不可用且明确需要时选用；
        失败如实报错，同样无兜底）。
    设计原则：选中方法失败即如实报错，杜绝「降级路径 / 兜底」。
    DXGI 同步阻塞（最长约 5s 等帧），用 asyncio.to_thread 包住不卡事件循环。
    """
    method = str(getattr(Config, "PC_SCREENSHOT_METHOD", "dxgi")).lower()
    if method == "mss":
        try:
            data, err = await pc_mod.screenshot()
            return data, err
        except Exception as e:  # noqa: BLE001 — CLOSE-02
            return None, "截图失败（%s）" % type(e).__name__
    # 默认 / dxgi：仅 DXGI，失败即报错（无 mss 兜底）
    data, err = await asyncio.to_thread(pc_mod.screenshot_dxgi)
    if err:
        return None, "DXGI 截图失败：%s" % err
    if not data:
        return None, "DXGI 截图失败：未返回图像数据"
    if _is_blackish(data):
        return None, "DXGI 截到全黑图（桌面可能尚未重绘或当前无交互式桌面会话），无法回退"
    return data, ""


async def _pc_send_shot(ctx: dict) -> None:
    """截图并经 base64 回传（含大小上限校验）。"""
    png, err = await _pc_shot_bytes()
    if err:
        await _reply(ctx, None, err, title="截图失败")
        return
    if len(png) > max(1, int(Config.PC_SHOT_MAX_MB)) * 1024 * 1024:
        await _reply(ctx, None, "截屏太大了（%.1f MB），超过 %d MB 上限，"
                                "本喵不敢往 QQ 里塞。" % (len(png) / 1048576,
                                                       int(Config.PC_SHOT_MAX_MB)),
                     title="截图失败")
        return
    await send_image(ctx, "base64://" + base64.b64encode(png).decode("ascii"),
                     at_user=False)


def _pc_inbox_dir() -> str:
    """QQ→宿主 落盘目录：PC_FILE_UPLOAD_DIR 非空用其值，否则项目 data/pc_inbox。

    始终落在项目内（不散到系统盘），与 PC_DISK_ROOTS 无关——收件箱是固定收纳点。
    """
    custom = str(getattr(Config, "PC_FILE_UPLOAD_DIR", "") or "").strip()
    if custom:
        return str(Path(custom).resolve())
    return str(Path(__file__).resolve().parents[3] / "data" / "pc_inbox")


async def _pc_send_file(ctx: dict, sess, path: str) -> None:
    """PC→QQ：把宿主机上的文件发到 QQ（受 PC_DISK_ROOTS 白名单 + PC_FILE_MAX_MB 限制）。"""
    if not path:
        await _reply(ctx, sess, "用法：/pc get <文件完整路径>\n"
                     "例：/pc get D:\\AI\\notes.txt", title="文件互传")
        return
    if not _pc_disk_allowed(path):
        await _reply(ctx, sess, "这个路径不在白名单（%s）里，本喵不敢乱读。"
                     % Config.PC_DISK_ROOTS, title="文件互传")
        return
    info = pc_mod.file_read_info(path)
    if not info.get("exists"):
        await _reply(ctx, sess, "读不到：%s" % info.get("error"), title="文件互传")
        return
    if not info.get("is_file"):
        await _reply(ctx, sess, info.get("error"), title="文件互传")
        return
    max_mb = max(1, int(getattr(Config, "PC_FILE_MAX_MB", 100)))
    if info["size"] > max_mb * 1024 * 1024:
        await _reply(ctx, sess, "「%s」有 %d MB，超过 %d MB 上限，本喵不传这么大。"
                     % (info["name"], info["size"] // 1048576, max_mb), title="文件互传")
        return
    await _reply(ctx, sess, "正在把「%s」（%d KB）发给你…"
                 % (info["name"], max(1, info["size"] // 1024)), title="文件互传")
    await send_file(ctx, path)


async def _pc_receive_files(ev, trace_id: str) -> None:
    """QQ→宿主：把 QQ 发来的文件落盘到收件箱（仅私聊主人，防覆盖，超限制拒）。"""
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    max_mb = max(1, int(getattr(Config, "PC_FILE_MAX_MB", 100)))
    inbox = _pc_inbox_dir()
    files = list(ev.files or [])
    if not files:
        await _reply(ctx, sess, "没收到文件段（端可能没以文件段投递）。", title="文件互传")
        return
    got = 0
    for f in files:
        name = f.get("name") or "unknown"
        size = int(f.get("size") or 0)
        if size and size > max_mb * 1048576:
            await _reply(ctx, sess, "「%s」有 %d MB 超过 %d MB 上限，跳过。"
                         % (name, size // 1048576, max_mb), title="文件互传")
            continue
        # 1) 优先用段里自带的 url（NapCat 常给本地 http 直链）下载；
        # 2) 否则用 get_file API 把文件 id 换成磁盘路径。
        url = str(f.get("url") or "")
        src_path = ""
        if url:
            try:
                async with httpx.AsyncClient(timeout=60.0) as cli:
                    resp = await cli.get(url)
                if resp.status_code == 200 and resp.content:
                    res = pc_mod.inbox_write_bytes(resp.content, inbox, name, max_mb * 1048576)
                    if res.get("ok"):
                        got += 1
                        await _reply(ctx, sess, "收到「%s」（%d KB）→ %s"
                                     % (res.get("name"), max(1, res.get("size", 0) // 1024),
                                        res.get("path")), title="文件互传")
                    else:
                        await _reply(ctx, sess, "「%s」保存失败：%s"
                                     % (name, res.get("error")), title="文件互传")
                else:
                    await _reply(ctx, sess, "「%s」下载失败（HTTP %d）。"
                                 % (name, resp.status_code), title="文件互传")
            except Exception as e:  # noqa: BLE001 — CLOSE-02
                log.warning(jlog("pc_file_url_failed", error=type(e).__name__,
                                 detail=str(e)[:160], trace_id=trace_id))
                await _reply(ctx, sess, "「%s」下载异常（%s）。"
                             % (name, type(e).__name__), title="文件互传")
            continue
        fid = str(f.get("file") or "")
        if fid:
            try:
                ret = await connector.call_action("get_file", {"file_id": fid})
                if isinstance(ret, dict):
                    src_path = ret.get("file") or ret.get("url") or ""
            except Exception as e:  # noqa: BLE001 — CLOSE-02
                log.warning(jlog("pc_file_get_failed", error=type(e).__name__,
                                 detail=str(e)[:160], trace_id=trace_id))
        if not src_path:
            await _reply(ctx, sess, "「%s」本喵取不到本地路径（端不支持？），跳过。"
                         % name, title="文件互传")
            continue
        res = await asyncio.to_thread(pc_mod.inbox_save, src_path, inbox, name, max_mb * 1048576)
        if res.get("ok"):
            got += 1
            await _reply(ctx, sess, "收到「%s」（%d KB）→ %s"
                         % (res.get("name"), max(1, res.get("size", 0) // 1024),
                            res.get("path")), title="文件互传")
        else:
            await _reply(ctx, sess, "「%s」保存失败：%s" % (name, res.get("error")),
                         title="文件互传")
    if got:
        log.info(jlog("pc_file_received", count=got, inbox=inbox, user_id=ev.user_id,
                      trace_id=trace_id))
    else:
        await _reply(ctx, sess, "这次没存成任何文件（都超限或取不到）。", title="文件互传")


async def _handle_pc_command(ev, trace_id: str) -> None:
    """/pc 命令族（仅私聊+主人）：体检 / top / port / temp / shot / kill / disk / host。"""
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    if not _pc_guard(ev):
        # 群聊静默（不暴露功能存在）；私聊非主人明确拒绝
        if not ev.is_group:
            await _reply(ctx, sess, "宿主机管家是主人的私享功能，本喵只认主人喵。",
                         title="权限不足")
        return
    body = (ev.raw_text or "").strip()
    low_body = body.lower()
    parts = body.split(None, 1)
    sub = parts[1].strip() if len(parts) > 1 else ""
    low = sub.lower()

    # ---- 高危确认应答（优先级最高）----
    # 注意：确认消息是「确认」本身（无 /pc 前缀），必须用完整 body 判断，
    # 不能只比 sub——否则 sub=="" 会被下方 status 分支吞掉，确认永远失效。
    if low_body in ("确认", "确认执行", "y", "yes"):
        pend = _pc_pending.get(ev.user_id)
        if pend and pend[2] > time.time():
            _pc_pending.pop(ev.user_id, None)
            kind, target, _ = pend
            try:
                if kind == "kill":
                    msg = pc_mod.kill_process(target)
                elif kind == "power":
                    # 关机/重启不可逆 → 5s 可取消倒计时（CLOSE-02 防误触）
                    await _reply(ctx, sess,
                                 "⚠️ %s将在 5 秒后执行，发「取消」可中止…" % target,
                                 title="高危操作")
                    _pc_power_cancel[ev.user_id] = False
                    for _i in range(5, 0, -1):
                        await asyncio.sleep(1)
                        if _pc_power_cancel.get(ev.user_id):
                            await _reply(ctx, sess, "已取消%s。" % target,
                                         title="高危操作")
                            return
                    msg = pc_mod._do_power(target)
                else:
                    msg = "未知操作"
            except Exception as e:  # noqa: BLE001
                msg = "执行失败（%s）" % type(e).__name__
            await _reply(ctx, sess, msg, title="宿主机管家")
            return
        await _reply(ctx, sess, "没有待确认的操作（或已过期），重新下指令吧。",
                     title="宿主机管家")
        return
    # 倒计时期间撤回：发「取消」中止待执行的电源操作
    if low_body in ("取消", "cancel") and ev.user_id in _pc_power_cancel:
        _pc_power_cancel[ev.user_id] = True
        return
    # 任何新指令都让旧确认失效
    _pc_pending.pop(ev.user_id, None)

    if not sub or low in ("status", "状态", "咋样", "体检", "check"):
        await _reply(ctx, sess, pc_mod.system_overview(), title="宿主机体检")
        return
    if low.startswith("top"):
        tail = sub[3:].strip()
        n = int(tail) if tail.isdigit() else 5
        await _reply(ctx, sess, pc_mod.top_processes(n), title="进程 Top %d" % n)
        return
    if low.startswith("port"):
        tail = sub[4:].strip()
        if not tail.isdigit():
            await _reply(ctx, sess, "用法：/pc port 8080", title="端口查询")
            return
        await _reply(ctx, sess, pc_mod.find_port(int(tail)), title="端口占用")
        return
    if low in ("temp", "温度", "热度"):
        await _reply(ctx, sess, pc_mod.temperatures(), title="硬件温度")
        return
    if low in ("shot", "screenshot", "截图"):
        await _pc_send_shot(ctx)
        return
    if low.startswith("kill"):
        target = sub[4:].strip()
        if not target:
            await _reply(ctx, sess, "用法：/pc kill <PID 或进程名>", title="高危操作")
            return
        if not Config.PC_KILL_CONFIRM:
            await _reply(ctx, sess, pc_mod.kill_process(target), title="高危操作")
            return
        _pc_pending[ev.user_id] = ("kill", target, time.time() + _PC_CONFIRM_TTL)
        await _reply(ctx, sess,
                     "高危确认：结束「%s」？\n回复「确认」执行（30 秒内有效），"
                     "发任何其它指令即取消。" % target, title="高危确认")
        return
    # ---- 窗口管理 ----
    if low in ("win", "wins", "windows", "窗口", "窗口列表"):
        wins = pc_mod.list_windows()
        txt = "可见窗口（%d 个）：\n" % len(wins) + "\n".join(
            "· " + w for w in wins[:40]) if wins else "当前没有可见窗口。"
        await _reply(ctx, sess, txt, title="窗口列表")
        return
    if low.startswith("focus"):
        await _reply(ctx, sess, pc_mod.focus_window(sub[5:].strip()), title="窗口操作")
        return
    if low.startswith("min"):
        await _reply(ctx, sess, pc_mod.minimize_window(sub[3:].strip()), title="窗口操作")
        return
    if low.startswith("close"):
        await _reply(ctx, sess, pc_mod.close_window(sub[5:].strip()), title="窗口操作")
        return
    if low.startswith("winshot"):
        title = sub[7:].strip()
        png, err = await pc_mod.window_screenshot(title)
        if err:
            await _reply(ctx, sess, err, title="窗口截图")
            return
        if len(png) > max(1, int(Config.PC_SHOT_MAX_MB)) * 1024 * 1024:
            await _reply(ctx, sess, "窗口截图太大（%.1f MB），超过 %d MB 上限。"
                                   % (len(png) / 1048576, int(Config.PC_SHOT_MAX_MB)),
                         title="窗口截图")
            return
        await send_image(ctx, "base64://" + base64.b64encode(png).decode("ascii"),
                         at_user=False)
        return
    # ---- 剪贴板 ----
    if low == "clip":
        c = pc_mod.clipboard_get()
        await _reply(ctx, sess, ("剪贴板内容：\n" + c) if c else "剪贴板是空的。",
                     title="剪贴板")
        return
    if low.startswith("clipset"):
        await _reply(ctx, sess, pc_mod.clipboard_set(sub[7:].strip()), title="剪贴板")
        return
    # ---- 电源（关机/重启不可逆 → 二次确认 + 5s 可取消）----
    if low == "lock":
        await _reply(ctx, sess, pc_mod.lock_screen(), title="电源")
        return
    if low == "sleep":
        await _reply(ctx, sess, pc_mod.sleep_pc(), title="电源")
        return
    if low.startswith("power"):
        tail = sub[5:].strip().lower()
        if tail in ("关机", "shutdown", "off"):
            target = "关机"
        elif tail in ("重启", "restart", "reboot"):
            target = "重启"
        else:
            await _reply(ctx, sess, "用法：/pc power 关机 | /pc power 重启",
                         title="电源")
            return
        _pc_pending[ev.user_id] = ("power", target, time.time() + _PC_CONFIRM_TTL)
        await _reply(ctx, sess,
                     "⚠️ 高危确认：%s整台电脑？\n回复「确认」执行（30 秒内有效），"
                     "执行前有 5 秒可发「取消」中止。" % target, title="高危确认")
        return
    # ---- 应用 ----
    if low == "apps":
        installed = pc_mod.list_installed()
        txt = "已装程序（前 %d 个）：\n" % len(installed) + "\n".join(
            "· " + a for a in installed) if installed else "没扫到已装程序。"
        await _reply(ctx, sess, txt, title="已装程序")
        return
    if low == "startup":
        items = pc_mod.startup_items()
        txt = "开机自启项（%d 个）：\n" % len(items) + "\n".join(
            "· " + i for i in items) if items else "没有开机自启项。"
        await _reply(ctx, sess, txt, title="开机自启")
        return
    if low.startswith("app"):
        await _reply(ctx, sess, pc_mod.launch_app(sub[3:].strip()), title="启动应用")
        return
    # ---- 键鼠 / OCR ----
    if low.startswith("type"):
        await _reply(ctx, sess, pc_mod.type_text(sub[4:].strip()), title="键鼠")
        return
    if low.startswith("click"):
        m = sub[5:].strip().split()
        if len(m) == 2 and m[0].isdigit() and m[1].isdigit():
            await _reply(ctx, sess, pc_mod.mouse_click(int(m[0]), int(m[1])),
                         title="键鼠")
        else:
            await _reply(ctx, sess, "用法：/pc click <x> <y>", title="键鼠")
        return
    if low in ("ocr", "识屏", "屏幕文字"):
        ocr_text, err = await asyncio.to_thread(pc_mod.screen_ocr)
        if err:
            await _reply(ctx, sess, err, title="屏幕 OCR")
            return
        await _reply(ctx, sess, ("屏幕上的文字：\n" + ocr_text) if ocr_text.strip()
                     else "屏幕上没识别到文字（可能是纯图像/黑屏）。", title="屏幕 OCR")
        return
    # ---- 文件互传：PC→QQ（把宿主机文件发到 QQ）----
    # 注意：low 是「/pc 」之后的整串（含路径），必须按首 token 判定，
    # 不能 exact match——否则 /pc get D:\x.txt 的 low="get d:\x.txt" 永远匹配不到。
    _cmd0 = low.split()[0] if low else ""
    if _cmd0 in ("get", "dl", "file"):
        _rest = sub.split(None, 1)
        tail = _rest[1].strip() if len(_rest) > 1 else ""
        await _pc_send_file(ctx, sess, tail)
        return
    if low.startswith("disk"):
        tail = sub[4:].strip() or str(Config.PC_DISK_ROOTS or "").split(";")[0].strip()
        if not tail or not _pc_disk_allowed(tail):
            await _reply(ctx, sess,
                         "这个目录不在白名单里。可看的：%s" % Config.PC_DISK_ROOTS,
                         title="目录速览")
            return
        await _reply(ctx, sess, pc_mod.disk_tree(tail), title="目录速览")
        return
    if low in ("host", "主机", "ip"):
        await _reply(ctx, sess, pc_mod.hostname_info(), title="宿主机信息")
        return
    await _reply(ctx, sess,
                 "宿主机管家用法：\n"
                 "· 体检 / 查询：/pc ｜ /pc top [N] ｜ /pc port <端口> ｜ /pc temp ｜ /pc shot ｜ /pc host\n"
                 "· 窗口：/pc wins ｜ /pc focus <标题> ｜ /pc min <标题> ｜ /pc close <标题> ｜ /pc winshot <标题>\n"
                 "· 剪贴板：/pc clip ｜ /pc clipset <文字>\n"
                 "· 应用：/pc apps ｜ /pc startup ｜ /pc app <名|路径>\n"
                 "· 键鼠/OCR：/pc type <文字> ｜ /pc click <x> <y> ｜ /pc ocr\n"
                 "· 电源（高危）：/pc lock ｜ /pc sleep ｜ /pc power 关机 ｜ /pc power 重启（需确认+5s可取消）\n"
                 "· 高危：/pc kill <PID|名>（需确认）\n"
                 "· 文件：/pc disk [路径]（限白名单）｜ /pc get <文件完整路径>（PC→QQ 发文件）\n"
                 "· 传文件给本喵：直接在私聊里发文件，自动存到收件箱（限主人、超 %d MB 拒）\n"
                 "自然语言也能用：电脑咋样 / 截个图 / 硬件温度 / 谁占CPU / 剪贴板 / 锁屏 / 睡眠 / 屏幕上的字" % int(getattr(Config, "PC_FILE_MAX_MB", 100)),
                 title="宿主机管家")


async def _try_pc_natural(ev, trace_id: str) -> bool:
    """自然语言直通车（不走 LLM，零成本；仅私聊主人）。命中返回 True。"""
    if not Config.PC_ENABLE or ev.is_group or not _is_owner(ev.user_id):
        return False
    t = (ev.raw_text or "").strip()
    if not t or t.startswith(("/", "#")):
        return False
    ctx = _ctx(ev)
    if any(k in t for k in ("电脑咋样", "电脑怎么样", "系统咋样", "硬件咋样",
                            "主机状态", "电脑体检")):
        await _reply(ctx, None, pc_mod.system_overview(), title="宿主机体检")
        return True
    if any(k in t for k in ("截个图", "截图看看", "看看屏幕", "看看桌面", "截屏看看")):
        await _pc_send_shot(ctx)
        return True
    if any(k in t for k in ("硬件温度", "cpu 温度", "cpu温度", "显卡温度", "多少度")):
        await _reply(ctx, None, pc_mod.temperatures(), title="硬件温度")
        return True
    if ("谁占" in t or "吃 cpu" in t.lower() or "占内存" in t
            or "哪个进程" in t or "哪些进程" in t):
        await _reply(ctx, None, pc_mod.top_processes(5), title="进程 Top 5")
        return True
    if any(k in t for k in ("剪贴板", "复制了啥", "粘帖板", "粘贴板")):
        c = pc_mod.clipboard_get()
        await _reply(ctx, None, ("剪贴板内容：\n" + c) if c else "剪贴板是空的。",
                     title="剪贴板")
        return True
    if any(k in t for k in ("锁屏", "锁电脑", "锁一下")):
        await _reply(ctx, None, pc_mod.lock_screen(), title="电源")
        return True
    if any(k in t for k in ("睡眠", "休眠", "睡一下")):
        await _reply(ctx, None, pc_mod.sleep_pc(), title="电源")
        return True
    if any(k in t for k in ("开了哪些窗口", "正在运行", "运行中的窗口", "窗口列表")):
        wins = pc_mod.list_windows()
        txt = ("可见窗口（%d 个）：\n" % len(wins) + "\n".join(
            "· " + w for w in wins[:40])) if wins else "当前没有可见窗口。"
        await _reply(ctx, None, txt, title="窗口列表")
        return True
    if any(k in t for k in ("屏幕上的字", "屏幕文字", "识屏", "截图识别", "识别屏幕")):
        ocr_text, err = await asyncio.to_thread(pc_mod.screen_ocr)
        if err:
            await _reply(ctx, None, err, title="屏幕 OCR")
            return True
        await _reply(ctx, None, ("屏幕上的文字：\n" + ocr_text) if ocr_text.strip()
                     else "屏幕上没识别到文字（可能是纯图像/黑屏）。", title="屏幕 OCR")
        return True
    return False


# ==================== M2 工具总线 / 提醒（2026-10-20）====================
# 设计：tools/ 包（registry/llm/fc/scheduler/builtins）不 import 主包，
# 宿主能力（队列/PC）经 deps 回调注入；提醒回传经 on_fire 注入。
# 详见 tools/registry.py 与 tools/scheduler.py 模块 docstring。

#: ReminderParser 单例（/提醒 命令族的 LLM 时间解析；LLMClient 懒构造）
_remind_parser = ReminderParser()


def _tools_time_hint() -> str:
    """注入 FC 系统提示的当前时间注记（模型靠它把「N 分钟后」算成时间戳）。"""
    now = time.time()
    return ("（系统注记：当前 Unix 时间 %d，本地时间 %s。用户说「N 分钟后/明早/"
            "下周一」等相对时间时，请用 remind_create 的 fire_at 参数传算好的"
            "绝对 Unix 时间戳（秒）。）"
            % (now, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))))


def _register_m2_tools() -> list[str]:
    """M2 首批 FC 工具注册（register() 调用）。

    deps 注入宿主能力：tools 包保持零主包依赖（可裸单测、无循环 import）。
    """
    async def _queue_status(tctx) -> str:
        snap = queue_mgr.snapshot()
        lines = ["队列上限 %d · 排队 %d · 运行中 %d"
                 % (snap["max"], snap["queued_count"], snap["running_count"])]
        uid = int(getattr(tctx, "user_id", 0) or 0)
        r = snap.get("running")
        mine_running = bool(r) and int(r.get("user_id") or 0) == uid
        if r:
            lines.append("正在跑：#%s %s%s" % (
                r.get("seq"), str(r.get("prompt") or "")[:30],
                "（是你的）" if mine_running else ""))
        q = snap.get("queued") or []
        my_pos = next((i for i, j in enumerate(q, 1)
                       if int(j.get("user_id") or 0) == uid), None)
        if q:
            lines.append("后面还排着 %d 条" % len(q))
        if my_pos:
            lines.append("你的任务排第 %d" % my_pos)
        elif not mine_running:
            lines.append("你当前没有在排的任务")
        if not snap.get("consumer_alive"):
            lines.append("⚠️ 队列消费者没在跑，任务不会被处理（bridge 可能刚重启）")
        return "\n".join(lines)

    async def _pc_overview() -> str:
        # system_overview 是同步函数（M6 既有用法一致直接调）
        return pc_mod.system_overview()

    async def _download_add(url: str, tctx) -> str:
        item = await download_manager.submit(
            url, scope=tctx.scope, user_id=tctx.user_id, group_id=tctx.group_id,
            channel=getattr(tctx, "channel", "napcat"))
        if item.get("status") == "complete":
            return ("下好了：%s\n文件：%s（%.1f MB）\n"
                    "「/dl 发我 %s」取文件" % (
                        item.get("name") or "未命名",
                        item.get("filepath") or "",
                        float(item.get("size") or 0) / 1048576,
                        item["id"]))
        return ("已加入下载队列：%s\n编号 %s（aria2 gid %s）\n"
                "下完会自动告诉你；「/dl 列表」看进度，「/dl 取消 %s」取消" % (
                    item.get("url") or "", item["id"],
                    item.get("gid") or "-", item["id"]))

    async def _download_list(tctx) -> str:
        items = download_manager.list(tctx.scope)
        if not items:
            return "当前会话没有进行中的下载"
        lines = ["下载任务："]
        for i, d in enumerate(items, 1):
            lines.append("%d. [%s] %s" % (i, d["id"],
                                          (d.get("name") or d.get("url") or "")[:50]))
        return "\n".join(lines)

    async def _download_cancel(ref: str, tctx) -> str:
        item = download_manager.resolve_ref(tctx.scope, ref)
        if item is None:
            return "没找到编号/序号为 %s 的下载任务" % ref
        await download_manager.cancel(item["id"], tctx.scope)
        return "已取消下载 %s（只移除任务，已下的文件还在 %s）" % (
            item["id"], item.get("filepath") or "下载目录")

    async def _ops_git_status() -> str:
        st = await git_info.status()
        return git_info.format_status(st)

    async def _ops_frp_status() -> str:
        data = await frp_status.status()
        return frp_status.format_status(data)

    return register_builtin_tools(default_registry, {
        "queue_status": _queue_status,
        "pc_overview": _pc_overview,
        "download_add": _download_add,
        "download_list": _download_list,
        "download_cancel": _download_cancel,
        "ops_git_status": _ops_git_status,
        "ops_frp_status": _ops_frp_status,
    })


async def _remind_fire(item: dict) -> None:
    """提醒到点回传（ReminderScheduler.on_fire 注入，M2）。

    发送失败抛错——调度器记 failed + last_error，**不自动重试**
    （ai-groupmate 纪律：重发会让用户收到重复提醒）。
    """
    rid = item.get("id", "")
    text = (item.get("text") or "").strip()
    if not text:
        raise ReminderError("提醒内容为空（rid=%s）" % rid)
    ctx = {"user_id": int(item.get("user_id") or 0),
           "group_id": int(item.get("group_id") or 0),
           "channel": item.get("channel") or "napcat"}
    rep = "\n（每天这个时间都会再提醒你）" if item.get("repeat") == "daily" else ""
    await send_text(ctx, "⏰ 提醒：%s%s" % (text, rep))
    log.info(jlog("remind_fired", rid=rid, scope=item.get("scope", ""),
                  group_id=ctx["group_id"], user_id=ctx["user_id"]))


async def _handle_remind_command(ev, trace_id: str) -> None:
    """/提醒 命令族（M2 2026-10-20）：创建 / 列表 / 取消 / 修改。

    用法：
      /提醒 20分钟后 喝水        创建（时间整句交给 LLM 解析，禁正则兜底）
      /提醒 明早八点 叫我起床    创建
      /提醒 列表                 本会话待触发提醒（序号即取消用的编号）
      /提醒 取消 2               取消第 2 条（也接受编号，如 a1b2c3d4）
      /提醒 改 2 明早八点        改第 2 条的触发时间（可顺带改内容）

    时间解析只信 LLM（ReminderParser）：「半小时后/明早/下周一」这类说法
    正则覆盖不全且错了很难看，解析失败显式告知换个说法，绝不兜底。
    """
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key
    body = (ev.raw_text or "").strip()
    parts = body.split(None, 2)
    sub = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""

    # ---- 列表 ----
    if sub in ("列表", "看看", "list", "ls", "所有"):
        items = reminder_store.list(scope)
        if not items:
            await _reply(ctx, sess, "这个会话还没有待触发的提醒喵。", title="提醒列表")
            return
        lines = ["待触发的提醒："]
        for i, r in enumerate(items, 1):
            lines.append("%d. [%s] %s %s%s" % (
                i, r["id"],
                time.strftime("%m-%d %H:%M", time.localtime(r["fire_at"])),
                r["text"][:40],
                "（每天）" if r.get("repeat") == "daily" else ""))
        lines.append("\n取消：/提醒 取消 <序号或编号>")
        await _reply(ctx, sess, "\n".join(lines), title="提醒列表")
        return

    # ---- 取消 ----
    if sub in ("取消", "删", "删除", "cancel", "del"):
        if not rest:
            await _reply(ctx, sess,
                         "用法：/提醒 取消 <序号或编号>（序号看 /提醒 列表）",
                         title="提醒")
            return
        rid = rest.split()[0]
        if rid.isdigit():  # 序号 → 编号
            items = reminder_store.list(scope)
            idx = int(rid)
            if not (1 <= idx <= len(items)):
                await _reply(ctx, sess, "没有第 %d 条提醒（当前 %d 条）。"
                                         % (idx, len(items)), title="提醒")
                return
            rid = items[idx - 1]["id"]
        try:
            item = reminder_store.cancel(rid, scope)
        except ReminderError as e:
            await _reply(ctx, sess, str(e), title="提醒")
            return
        await _reply(ctx, sess, "已取消：%s（编号 %s）" % (item["text"], rid),
                     title="提醒")
        return

    # ---- 修改 ----
    if sub in ("改", "修改", "modify", "mod"):
        if not rest:
            await _reply(ctx, sess,
                         "用法：/提醒 改 <序号> <新时间> [新内容]\n"
                         "例：/提醒 改 1 明早八点｜/提醒 改 1 八点 起床喝水",
                         title="提醒")
            return
        head = rest.split(None, 1)
        rid = head[0]
        tail = head[1].strip() if len(head) > 1 else ""
        items = reminder_store.list(scope)
        if rid.isdigit():
            idx = int(rid)
            if not (1 <= idx <= len(items)):
                await _reply(ctx, sess, "没有第 %d 条提醒。" % idx, title="提醒")
                return
            rid = items[idx - 1]["id"]
        if not tail:
            await _reply(ctx, sess, "要改成啥时候？例：/提醒 改 1 明早八点",
                         title="提醒")
            return
        try:
            plan = await _remind_parser.parse("帮我把一条提醒改到：%s" % tail)
        except Exception as e:  # noqa: BLE001 — ReminderError/LLMError 都显式告知
            await _reply(ctx, sess, _remind_err_text(e), title="提醒")
            return
        kw: dict = {"fire_at": plan["fire_at"]}
        if plan.get("text"):
            kw["text"] = plan["text"]
        try:
            item = reminder_store.modify(rid, scope, **kw)
        except ReminderError as e:
            await _reply(ctx, sess, str(e), title="提醒")
            return
        await _reply(ctx, sess, "改好了：%s（%s，编号 %s）" % (
            item["text"],
            time.strftime("%m-%d %H:%M", time.localtime(item["fire_at"])),
            rid), title="提醒")
        return

    # ---- 创建（默认分支：整句交给 LLM 解析）----
    if not rest and not sub:
        await _reply(ctx, sess,
                     "用法：/提醒 <时间> <内容>，例：\n"
                     "· /提醒 20分钟后 喝水\n"
                     "· /提醒 明早八点 叫我起床\n"
                     "· /提醒 每周一 九点 周会（每天重复说「每天」即可）\n"
                     "· /提醒 列表｜/提醒 取消 <序号>",
                     title="提醒")
        return
    utterance = (sub + (" " + rest if rest else "")).strip()
    try:
        plan = await _remind_parser.parse(utterance)
    except Exception as e:  # noqa: BLE001 — 显式告知，禁正则兜底
        log.warning(jlog("remind_parse_failed", scope=scope, user_id=ev.user_id,
                         error=type(e).__name__, trace_id=trace_id))
        await _reply(ctx, sess, _remind_err_text(e), title="提醒")
        return
    # 用户用自然语言表达了查看/取消/修改意图（没打子命令）——按解析结果办
    if plan["action"] == "list":
        items = reminder_store.list(scope)
        if not items:
            await _reply(ctx, sess, "这个会话还没有待触发的提醒喵。", title="提醒列表")
            return
        lines = ["待触发的提醒："]
        for i, r in enumerate(items, 1):
            lines.append("%d. [%s] %s %s" % (
                i, r["id"],
                time.strftime("%m-%d %H:%M", time.localtime(r["fire_at"])),
                r["text"][:40]))
        await _reply(ctx, sess, "\n".join(lines), title="提醒列表")
        return
    if plan["action"] in ("cancel", "modify"):
        await _reply(ctx, sess,
                     "要%s的话用子命令更稳：/提醒 %s <序号>"
                     % ("取消" if plan["action"] == "cancel" else "修改",
                        "取消" if plan["action"] == "cancel" else "改"),
                     title="提醒")
        return
    try:
        item = reminder_store.create(
            text=plan["text"], fire_at=plan["fire_at"], repeat=plan["repeat"],
            scope=scope, user_id=ev.user_id, group_id=ev.group_id,
            is_private=not ev.is_group,
            channel=getattr(ev, "channel", "napcat"))
    except ReminderError as e:
        await _reply(ctx, sess, str(e), title="提醒")
        return
    await _reply(ctx, sess, "好，%s 提醒你：%s（编号 %s%s）" % (
        time.strftime("%m-%d %H:%M", time.localtime(item["fire_at"])),
        item["text"], item["id"],
        "，每天这时间都提醒" if item["repeat"] == "daily" else ""),
        title="提醒已设定")
    log.info(jlog("remind_cmd_created", scope=scope, user_id=ev.user_id,
                  rid=item["id"], trace_id=trace_id))


def _remind_err_text(e: Exception) -> str:
    """提醒解析失败的对外文案（no_key 转配置指引，其余显式透出）。"""
    msg = str(e)
    if "no_key" in msg:
        return ("提醒解析要调大模型，但还没配 API Key 喵——去「机器人配置 → "
                "常规·AI 模式」填上 base_url / key / 模型。")
    return msg


# ==================== M7 下载媒体中心（2026-10-20）====================
# download 包（aria2/btselect/ytdlp/manager/subscribe）不 import 主包；
# 完成通知/发送/文件回传全部经本区块的回调与命令族落地。


async def _dl_notify(item: dict) -> None:
    """下载完成通知（download_manager.on_complete 注入，M7）。

    小文件（≤ PC_FILE_MAX_MB）直接回传；大文件只报路径+取件命令
    （NapCat WS 单帧 16MB，硬塞会炸——M6 文件互传同款取舍）。
    """
    did = item.get("id", "")
    name = item.get("name") or "未命名文件"
    path = item.get("filepath") or ""
    size = int(item.get("size") or 0)
    ctx = {"user_id": int(item.get("user_id") or 0),
           "group_id": int(item.get("group_id") or 0),
           "channel": item.get("channel") or "napcat"}
    sess = None
    max_mb = max(1, int(getattr(Config, "PC_FILE_MAX_MB", 100)))
    if path and size and size <= max_mb * 1048576:
        try:
            await send_file(ctx, path)
            log.info(jlog("dl_notify_file_sent", did=did, size=size))
        except Exception as e:  # noqa: BLE001 — 直发失败回落文本指引（不静默）
            log.warning(jlog("dl_notify_send_failed", did=did,
                             error=type(e).__name__, detail=str(e)[:160]))
            await _reply(ctx, sess,
                         "「%s」下好了（%.1f MB），但直接回传失败（%s）。\n"
                         "文件在：%s\n发「/dl 发我 %s」再试" % (
                             name, size / 1048576, type(e).__name__, path, did),
                         title="下载完成")
            return
        return
    await _reply(ctx, sess,
                 "「%s」下好了（%.1f MB，超过 %d MB 不回传）。\n"
                 "文件在：%s\n发「/dl 发我 %s」取文件" % (
                     name, size / 1048576, max_mb, path or "下载目录", did),
                 title="下载完成")


async def _dl_sub_notify(sub: dict, new_items: list) -> None:
    """订阅更新通知（subscription_monitor.on_new 注入，M7）。"""
    ctx = {"user_id": int(sub.get("user_id") or 0),
           "group_id": int(sub.get("group_id") or 0),
           "channel": sub.get("channel") or "napcat"}
    titles = "、".join(i.get("title", "")[:30] for i in new_items[:5])
    if len(new_items) > 5:
        titles += " 等 %d 条" % len(new_items)
    auto = "已在自动下载，下完告诉你" if Config.DL_SUB_AUTO_DOWNLOAD else \
        "（自动下载关着，只通知——要下手动 /dl <链接>）"
    await _reply(ctx, None,
                 "订阅更新：%s\n%s\n%s" % (sub.get("title") or sub.get("url", ""),
                                        titles, auto),
                 title="订阅更新")


async def _dl_sub_submit(url: str, scope: str, user_id: int = 0,
                         group_id: int = 0, channel: str = "napcat"):
    """订阅自动下载入口（subscription_monitor.submit 注入）。"""
    return await download_manager.submit(url, scope=scope, user_id=user_id,
                                         group_id=group_id, channel=channel)


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024
    return "%.1f PB" % n


async def _handle_dl_command(ev, trace_id: str) -> None:
    """/dl 命令族（M7 2026-10-20）：下载 / 列表 / 暂停 / 继续 / 取消 / 发我 /
    订阅 / 退订 / 状态。

    下载类（add/cancel）**仅主人**——占主人带宽与磁盘；列表/查询所有会话
    可用（只看自己会话的任务）。「发我」的文件路径受 PC_DISK_ROOTS 白名单
    约束（复用 M6 文件互传安全层）。
    """
    if not Config.DL_ENABLE:
        return
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key
    body = (ev.raw_text or "").strip()
    parts = body.split(None, 2)
    sub = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""
    low = sub.lower()

    def _owner_only() -> bool:
        if ev.is_group or not _is_owner(ev.user_id):
            return False
        return True

    # ---- 状态（aria2 RPC 探测；所有会话可查）----
    if low in ("状态", "status", "咋样"):
        ok, detail = await download_manager.aria2.check()
        if not ok:
            await _reply(ctx, sess, detail, title="下载中心·未就绪")
            return
        try:
            stat = await download_manager.aria2.get_global_stat()
            await _reply(ctx, sess,
                         "下载中心在线（%s）\n下载 %s/s · 上传 %s/s · 活动任务 %s" % (
                             detail, _fmt_size(float(stat.get("downloadSpeed") or 0)),
                             _fmt_size(float(stat.get("uploadSpeed") or 0)),
                             stat.get("numActive", "?")),
                         title="下载中心")
        except Aria2Error as e:
            await _reply(ctx, sess, str(e), title="下载中心")
        return

    # ---- 列表 ----
    if low in ("列表", "list", "ls", "所有", "进度"):
        items = download_manager.list(scope)
        if not items:
            await _reply(ctx, sess, "这个会话没有进行中的下载喵。", title="下载列表")
            return
        lines = ["下载任务（编号 序号）："]
        for i, d in enumerate(items, 1):
            name = (d.get("name") or d.get("url") or "")[:40]
            lines.append("%d. [%s] %s" % (i, d["id"], name))
        await _reply(ctx, sess, "\n".join(lines), title="下载列表")
        return

    # ---- 订阅 ----
    if low in ("订阅", "sub"):
        if not _owner_only():
            await _reply(ctx, sess, "订阅下载是主人的私享功能（会自动下片占机器）喵。",
                         title="权限不足")
            return
        if not Config.DL_SUB_ENABLE:
            await _reply(ctx, sess, "订阅监控关着（配置页「下载媒体 → "
                                    "DL_SUB_ENABLE」打开）", title="订阅")
            return
        if not rest:
            subs = subscription_store.list(scope)
            usage = ("用法：/dl 订阅 <B站收藏夹/UP空间/抖音作品链接>\n"
                     "例：/dl 订阅 https://space.bilibili.com/xxxx/favlist\n"
                     "· /dl 订阅列表 · /dl 退订 <序号>")
            if subs:
                usage += "\n当前订阅：\n" + "\n".join(
                    "%d. %s" % (i, s["url"][:60])
                    for i, s in enumerate(subs, 1))
            await _reply(ctx, sess, usage, title="订阅")
            return
        try:
            sub_item = subscription_store.add(
                rest, scope, user_id=ev.user_id, group_id=ev.group_id,
                channel=getattr(ev, "channel", "napcat"))
            await _reply(ctx, sess,
                         "订阅好了：%s\n编号 %s（每 %d 分钟检查一次更新，"
                         "有新片自动下）" % (
                             sub_item["url"][:80], sub_item["id"],
                             int(Config.DL_SUB_INTERVAL // 60)),
                         title="订阅已添加")
        except SubscriptionError as e:
            await _reply(ctx, sess, str(e), title="订阅")
        return

    if low in ("订阅列表", "sublist"):
        subs = subscription_store.list(scope)
        if not subs:
            await _reply(ctx, sess, "这个会话还没有订阅源。", title="订阅列表")
            return
        lines = ["订阅源："]
        for i, s in enumerate(subs, 1):
            lines.append("%d. [%s] %s（上次新增 %d）" % (
                i, s["id"], s["url"][:60], int(s.get("last_new_count") or 0)))
        await _reply(ctx, sess, "\n".join(lines), title="订阅列表")
        return

    if low in ("退订", "取消订阅", "unsub"):
        if not _owner_only():
            await _reply(ctx, sess, "退订也要主人来喵。", title="权限不足")
            return
        if not rest:
            await _reply(ctx, sess, "用法：/dl 退订 <序号或编号>", title="退订")
            return
        ref = rest.split()[0]
        subs = subscription_store.list(scope)
        if ref.isdigit() and 1 <= int(ref) <= len(subs):
            ref = subs[int(ref) - 1]["id"]
        try:
            s = subscription_store.remove(ref, scope)
            await _reply(ctx, sess, "已退订：%s" % s["url"][:80], title="退订")
        except SubscriptionError as e:
            await _reply(ctx, sess, str(e), title="退订")
        return

    # ---- 发我（文件中转站：下载目录/白名单文件 → QQ）----
    if low in ("发我", "发送", "send", "get"):
        if not rest:
            await _reply(ctx, sess,
                         "用法：/dl 发我 <任务序号/编号> 或 <文件完整路径>\n"
                         "例：/dl 发我 1 · /dl 发我 D:\\Downloads\\a.mkv",
                         title="文件回传")
            return
        ref = rest.split()[0]
        item = download_manager.resolve_ref(scope, ref)
        path = (item or {}).get("filepath") or ref
        await _pc_send_file(ctx, sess, path)
        return

    # ---- 暂停 / 继续 ----
    if low in ("暂停", "pause", "停"):
        item = download_manager.resolve_ref(scope, rest.split()[0] if rest else "")
        if item is None:
            await _reply(ctx, sess, "没找到那个任务（/dl 列表 看编号）", title="下载")
            return
        try:
            await download_manager.pause(item["id"], scope)
            await _reply(ctx, sess, "已暂停 %s" % item["id"], title="下载")
        except (DownloadError, Aria2Error) as e:
            await _reply(ctx, sess, str(e), title="下载")
        return
    if low in ("继续", "恢复", "resume", "unpause"):
        item = download_manager.resolve_ref(scope, rest.split()[0] if rest else "")
        if item is None:
            await _reply(ctx, sess, "没找到那个任务（/dl 列表 看编号）", title="下载")
            return
        try:
            await download_manager.unpause(item["id"], scope)
            await _reply(ctx, sess, "已继续 %s" % item["id"], title="下载")
        except (DownloadError, Aria2Error) as e:
            await _reply(ctx, sess, str(e), title="下载")
        return

    # ---- 取消 ----
    if low in ("取消", "删", "删除", "cancel", "del", "rm"):
        if not _owner_only():
            await _reply(ctx, sess, "取消下载也要主人确认喵（会占你机器资源）。",
                         title="权限不足")
            return
        if not rest:
            await _reply(ctx, sess, "用法：/dl 取消 <序号或编号>", title="取消下载")
            return
        item = download_manager.resolve_ref(scope, rest.split()[0])
        if item is None:
            await _reply(ctx, sess, "没找到那个任务", title="取消下载")
            return
        try:
            await download_manager.cancel(item["id"], scope)
            await _reply(ctx, sess,
                         "已取消 %s（只移除任务；已下的文件还在下载目录）" % item["id"],
                         title="取消下载")
        except (DownloadError, Aria2Error) as e:
            await _reply(ctx, sess, str(e), title="取消下载")
        return

    # ---- 下载（默认分支）----
    if not _owner_only():
        # 群聊/非主人：静默（不暴露功能存在，同 /pc 口径）或明确拒绝
        if not ev.is_group:
            await _reply(ctx, sess, "下载是主人的私享功能（会占你家机器资源）喵。",
                         title="权限不足")
        return
    if not rest and not sub:
        await _reply(ctx, sess,
                     "用法：/dl <链接>\n"
                     "· http/https/ftp 直链 → aria2\n"
                     "· magnet: 磁力 → aria2 + 智能选最大文件\n"
                     "· 本地 .torrent 路径 → aria2\n"
                     "· B站/抖音/YouTube 等视频页 → yt-dlp\n"
                     "· /dl 列表 · /dl 发我 N · /dl 取消 N · /dl 状态 · /dl 订阅 <链接>",
                     title="下载中心")
        return
    url = (sub + (" " + rest if rest else "")).strip()
    try:
        item = await download_manager.submit(
            url, scope=scope, user_id=ev.user_id, group_id=ev.group_id,
            channel=getattr(ev, "channel", "napcat"))
    except (DownloadError, Aria2Error, YtDlpError) as e:
        log.warning(jlog("dl_submit_failed", scope=scope, user_id=ev.user_id,
                         error=type(e).__name__, trace_id=trace_id))
        await _reply(ctx, sess, str(e), title="下载失败")
        return
    if item.get("status") == "complete":
        await _reply(ctx, sess,
                     "下好了：%s\n文件：%s（%s）\n「/dl 发我 %s」取文件" % (
                         item.get("name") or "未命名",
                         item.get("filepath") or "",
                         _fmt_size(float(item.get("size") or 0)), item["id"]),
                     title="下载完成")
        return
    await _reply(ctx, sess,
                 "已加入队列：%s\n编号 %s%s\n下完自动告诉你（大文件只报路径）" % (
                     url[:80], item["id"],
                     "（BT 智能选文件中，元数据到位即开始）"
                     if item.get("kind") == "magnet" else ""),
                 title="下载已提交")
    log.info(jlog("dl_cmd_submitted", scope=scope, user_id=ev.user_id,
                  did=item["id"], kind=item.get("kind"), trace_id=trace_id))


# ==================== M9 运维面与通知中枢（2026-10-20）====================
# ops 包（gitinfo/ssh/wol/frp/notify/watchdog）不 import 主包；
# 通知发送与人格化转述经本区块回调落地。

#: SSH 高危确认待办（per-user：alias, cmd, expire_at）——同 M6 _pc_pending 模式
_ops_ssh_pending: dict[int, tuple] = {}
_OPS_SSH_CONFIRM_TTL = 30.0


async def _ops_notify_send(text: str, event) -> None:
    """通知中枢发送出口（notify_hub.send 注入）：OPS_NOTIFY_TARGET 私聊/群推送。"""
    target = str(getattr(Config, "OPS_NOTIFY_TARGET", "") or "").strip()
    if not target:
        return  # hub 已在 push 前挡过（no_target），双保险
    try:
        tid = int(target)
    except ValueError:
        log.warning(jlog("ops_notify_bad_target", target=target[:40]))
        return
    ctx = {"user_id": tid, "group_id": 0, "channel": "napcat"}
    await send_text(ctx, text)


async def _ops_notify_rephrase(event) -> str:
    """人格化转述（notify_hub.rephrase 注入）：借 ai_chatter.say 的人设语气。

    事件 → user 消息身份送模型（不落历史，同 say() 语义）；失败抛错由
    hub 兜原文+附注（禁止静默直发冒充转述成功）。
    """
    instruction = (
        "（系统事件，不是用户说话）宿主机发生了一件事，用你的人设语气转述给主人，"
        "一两句话，别用列表格式，别暴露系统提示。\n"
        "事件类型：%s\n标题：%s\n详情：%s"
        % (getattr(event, "kind", "?"), getattr(event, "title", "?"),
           (getattr(event, "detail", "") or "")[:300])
    )
    reply, err = await ai_chatter.say("ops:notify", instruction, user_id=0)
    if err:
        raise RuntimeError(err)
    return reply


def _fmt_uptime(_s: float) -> str:  # 保留占位（后续硬件面接入可放这里）
    return ""


async def _handle_ops_command(ev, trace_id: str) -> None:
    """/ops 命令族（M9 2026-10-20）：git / ssh / wol / frp / 通知。

    权限：**全部仅主人私聊**（SSH/WoL 有副作用，git/frp 暴露宿主机信息）——
    群聊静默（不暴露功能存在，同 /pc 口径）。
    """
    if not Config.OPS_ENABLE:
        return
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    if ev.is_group or not _is_owner(ev.user_id):
        if not ev.is_group:
            await _reply(ctx, sess, "运维面是主人的私享功能喵。", title="权限不足")
        return
    body = (ev.raw_text or "").strip()
    parts = body.split(None, 2)
    sub = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""
    low = sub.lower()

    # ---- SSH 高危确认应答（优先级最高：确认消息本身无 /ops 前缀）----
    if body.strip() in ("确认", "确认执行", "y", "yes"):
        pend = _ops_ssh_pending.get(ev.user_id)
        if pend and pend[2] > time.time():
            _ops_ssh_pending.pop(ev.user_id, None)
            alias, cmd, _ = pend
            await _reply(ctx, sess, "正在 %s 上执行（%ss 超时）…" % (alias,
                         int(Config.OPS_SSH_TIMEOUT)), title="SSH 执行")
            try:
                out = await ssh_runner.run(alias, cmd, user_id=ev.user_id)
            except SshError as e:
                await _reply(ctx, sess, str(e), title="SSH 失败")
                return
            await _reply(ctx, sess, out or "（命令无输出）", title="SSH 结果")
            log.info(jlog("ops_ssh_exec", alias=alias, user_id=ev.user_id,
                          cmd=cmd[:80], trace_id=trace_id))
            return
        # 没有待确认的 SSH → 不当普通命令处理，明确告知
        await _reply(ctx, sess, "没有待确认的 SSH 命令（或已过期），重新下指令吧。",
                     title="SSH 确认")
        return
    # 任何新指令都让旧确认失效
    _ops_ssh_pending.pop(ev.user_id, None)

    # ---- git 状态 ----
    if low in ("git", "仓库", "状态", "status"):
        try:
            st = await git_info.status()
        except GitInfoError as e:
            await _reply(ctx, sess, str(e), title="仓库状态")
            return
        await _reply(ctx, sess, git_info.format_status(st), title="开发机仓库")
        return

    # ---- git 原始只读查询 ----
    if low.startswith("gitlog") or low.startswith("gitdiff"):
        arg = sub[len("gitlog"):] if low.startswith("gitlog") else sub[len("gitdiff"):]
        try:
            out = await git_info.raw(
                ["log", "--oneline", arg.strip("- ") or "-5"] if low.startswith("gitlog")
                else ["diff", "--stat"])
        except GitInfoError as e:
            await _reply(ctx, sess, str(e), title="git 查询")
            return
        await _reply(ctx, sess, (out or "（无输出）")[:1500], title="git 查询")
        return

    # ---- SSH 执行（白名单 + 二次确认）----
    if low in ("ssh", "远程", "exec"):
        if not rest:
            hosts = ssh_runner.hosts
            await _reply(ctx, sess,
                         "用法：/ops ssh <别名> <命令>\n"
                         "已配主机：%s\n"
                         "命令白名单：%s…\n"
                         "例：/ops ssh nas df -h" % (
                             "、".join(hosts) or "（无，去配置页加 OPS_SSH_HOSTS）",
                             ssh_runner.whitelist[0]),
                         title="SSH 白名单执行")
            return
        head = rest.split(None, 1)
        alias = head[0]
        cmd = head[1].strip() if len(head) > 1 else ""
        try:
            desc = ssh_runner.describe(alias, cmd)
        except SshError as e:
            await _reply(ctx, sess, str(e), title="SSH 拒绝")
            return
        _ops_ssh_pending[ev.user_id] = (alias, cmd, time.time() + _OPS_SSH_CONFIRM_TTL)
        await _reply(ctx, sess,
                     "高危确认：%s\n回复「确认」执行（30 秒内有效），发任何其它指令即取消。\n"
                     "（审计已记录：谁/何时/主机/命令）" % desc,
                     title="SSH 高危确认")
        log.info(jlog("ops_ssh_confirm_requested", alias=alias,
                      user_id=ev.user_id, cmd=cmd[:80], trace_id=trace_id))
        return

    # ---- WoL 唤醒 ----
    if low in ("wol", "唤醒", "开机"):
        if not rest:
            await _reply(ctx, sess,
                         "用法：/ops wol <别名>\n已配目标：%s\n"
                         "例：/ops wol 客厅" % (
                             "、".join(wol_sender.hosts) or "（无，去配置页加 OPS_WOL_HOSTS）"),
                         title="Wake-on-LAN")
            return
        try:
            msg = await wol_sender.wake(rest.split()[0], user_id=ev.user_id)
        except WolError as e:
            await _reply(ctx, sess, str(e), title="唤醒失败")
            return
        await _reply(ctx, sess, msg, title="已发魔术包")
        return

    # ---- frp 隧道状态 ----
    if low in ("frp", "穿透", "隧道"):
        try:
            data = await frp_status.status()
        except FrpError as e:
            await _reply(ctx, sess, str(e), title="frp 状态")
            return
        await _reply(ctx, sess, frp_status.format_status(data), title="内网穿透")
        return

    # ---- 通知中枢：测试 + 历史 ----
    if low in ("通知", "notify", "测试通知"):
        if rest.lower() in ("历史", "history", "记录"):
            items = notify_hub.history(10)
            if not items:
                await _reply(ctx, sess, "还没有事件记录。", title="通知历史")
                return
            lines = ["最近事件："]
            for e in items:
                lines.append("· [%s] %s（%s）" % (
                    e.level, e.title[:50],
                    time.strftime("%m-%d %H:%M", time.localtime(e.ts))))
            await _reply(ctx, sess, "\n".join(lines), title="通知历史")
            return
        if not Config.OPS_NOTIFY_TARGET:
            await _reply(ctx, sess,
                         "通知中枢没配推送目标（OPS_NOTIFY_TARGET），现在只记日志不发。",
                         title="通知中枢")
            return
        ok = await notify_hub.push(NotifyEvent(
            kind="manual_test", title="通知中枢自测：这是一条测试消息",
            detail="如果你收到这条，说明转述+推送链路通了喵。", level="info"))
        await _reply(ctx, sess,
                     "测试事件已推（%s）" % ("已发送" if ok else "被过滤/限流/无目标"),
                     title="通知中枢")
        return

    # ---- 帮助 ----
    await _reply(ctx, sess,
                 "运维面命令族（仅主人私聊）：\n"
                 "· /ops git —— 开发机仓库状态（只读）\n"
                 "· /ops gitlog [n] —— 最近提交\n"
                 "· /ops ssh <别名> <命令> —— 远程执行（白名单+二次确认）\n"
                 "· /ops wol <别名> —— 魔术包唤醒\n"
                 "· /ops frp —— 内网穿透隧道状态\n"
                 "· /ops 通知 —— 通知中枢自测｜/ops 通知 历史 —— 事件记录",
                 title="运维面")


# ==================== M1 记忆系统（2026-10-20）====================
# memory 包（store/extract/manager）不 import 主包；ingest 的 LLM 抽取经
# tools.llm 延迟导入。


async def _memory_ingest_bg(scope: str, user_id: int, user_text: str,
                            bot_reply: str) -> None:
    """后台记忆抽取（_handle_ai_chat 对话后 spawn；失败只记日志）。"""
    try:
        await memory_manager.ingest(scope, user_id, user_text, bot_reply)
    except (ExtractError, MemoryError) as e:
        # 抽取失败/无 Key：静默记日志（下轮对话还能再抽，不打扰用户）
        log.info(jlog("memory_ingest_skipped", scope=scope, user_id=user_id,
                      detail=str(e)[:160]))
    except Exception as e:  # noqa: BLE001 — 兜底显式化，绝不静默
        log.warning(jlog("memory_ingest_failed", scope=scope, user_id=user_id,
                         error=type(e).__name__, detail=str(e)[:160]))


async def _handle_memory_command(ev, trace_id: str) -> None:
    """/记忆 命令族（M1 2026-10-20）：我 / 忘记 / 更正 / 事件 / 清理。

    权限口径：
      - `/记忆 我`、`/记忆 忘记 <字段>`：任何人可看/删**自己**在本会话的记忆；
      - `/记忆 更正 <字段> <值>`：任何人可更正自己的画像（结构化画像更正，
        override 优先于自动快照——personification 范式）；
      - `/记忆 事件`、`/记忆 清理`：仅主人（看全会话长期记忆/触发滚动清理）。
    """
    if not Config.MEMORY_ENABLE:
        return
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key
    parts = (ev.raw_text or "").strip().split(None, 2)
    sub = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""
    low = sub.lower()

    # ---- 我：我的画像 ----
    if low in ("我", "me", "画像", "profile", ""):
        try:
            text = memory_manager.profile_view(scope, ev.user_id)
        except MemoryError as e:
            await _reply(ctx, sess, str(e), title="记忆")
            return
        await _reply(ctx, sess, text, title="本喵对你的了解")
        return

    # ---- 更正：结构化画像更正（override 优先）----
    if low in ("更正", "纠正", "correct", "set"):
        head = rest.split(None, 1)
        if len(head) < 2:
            await _reply(ctx, sess,
                         "用法：/记忆 更正 <字段> <值>\n"
                         "例：/记忆 更正 喜好 猫猫和代码\n"
                         "（更正后你的话优先——自动抽取的同字段会被覆盖）",
                         title="记忆更正")
            return
        key, value = head[0], head[1].strip()
        try:
            memory_store.set_override(scope, ev.user_id, key, value,
                                      by=ev.user_id)
        except MemoryError as e:
            await _reply(ctx, sess, str(e), title="记忆更正")
            return
        await _reply(ctx, sess, "记住了：%s = %s（你的更正优先）" % (key, value),
                     title="记忆更正")
        return

    # ---- 忘记：删一条自己的记忆 ----
    if low in ("忘记", "忘掉", "forget", "删"):
        if not rest:
            await _reply(ctx, sess,
                         "用法：/记忆 忘记 <字段>\n例：/记忆 忘记 喜好\n"
                         "（先 /记忆 我 看有哪些字段）", title="记忆")
            return
        try:
            msg = memory_manager.forget(scope, ev.user_id, rest.split()[0])
        except MemoryError as e:
            await _reply(ctx, sess, str(e), title="记忆")
            return
        await _reply(ctx, sess, msg, title="记忆")
        return

    # ---- 事件：本会话长期记忆（仅主人）----
    if low in ("事件", "events", "长期"):
        if not _is_owner(ev.user_id):
            await _reply(ctx, sess, "看会话记忆是主人的权限喵。", title="权限不足")
            return
        try:
            hits = memory_store.recent(scope, limit=15)
        except MemoryError as e:
            await _reply(ctx, sess, str(e), title="记忆")
            return
        if not hits:
            await _reply(ctx, sess, "这个会话还没有长期记忆。", title="长期记忆")
            return
        lines = ["本会话长期记忆（最近 15 条）："]
        for h in hits:
            lines.append("· [%s] %s（%s）" % (
                h.get("kind"), str(h.get("text") or "")[:60],
                time.strftime("%m-%d", time.localtime(float(h.get("created_at") or 0)))))
        await _reply(ctx, sess, "\n".join(lines), title="长期记忆")
        return

    # ---- 清理：滚动清理（仅主人）----
    if low in ("清理", "gc", "整理"):
        if not _is_owner(ev.user_id):
            await _reply(ctx, sess, "清理是主人的权限喵。", title="权限不足")
            return
        try:
            n = memory_store.gc()
        except MemoryError as e:
            await _reply(ctx, sess, str(e), title="记忆清理")
            return
        await _reply(ctx, sess, "清理完成：%d 条超期/超额记忆已软删（可恢复）" % n,
                     title="记忆清理")
        return

    # ---- 帮助 ----
    await _reply(ctx, sess,
                 "记忆命令族：\n"
                 "· /记忆 我 —— 本喵对你的了解（画像）\n"
                 "· /记忆 更正 <字段> <值> —— 你说的话优先（覆盖自动抽取）\n"
                 "· /记忆 忘记 <字段> —— 删一条\n"
                 "· /记忆 事件 —— 本会话长期记忆（主人）\n"
                 "· /记忆 清理 —— 滚动清理超期记忆（主人）",
                 title="记忆系统")


# ==================== M3 情感 + 日程 + 关系（2026-10-20）====================
# emotion 包（mood/store/schedule/manager）不 import 主包；LLM 抽取经
# tools.llm 延迟导入。


async def _emotion_update_bg(scope: str, user_id: int, user_text: str) -> None:
    """后台日程抽取（_handle_ai_chat 对话后 spawn；失败只记日志）。"""
    try:
        await emotion_manager.update_from_chat(scope, user_id, user_text)
    except (ScheduleError, EmotionError) as e:
        log.info(jlog("emotion_update_skipped", scope=scope, user_id=user_id,
                      detail=str(e)[:160]))
    except Exception as e:  # noqa: BLE001 — 显式化，绝不静默
        log.warning(jlog("emotion_update_failed", scope=scope, user_id=user_id,
                         error=type(e).__name__, detail=str(e)[:160]))


async def _emotion_care_loop() -> None:
    """到期关心循环（M3）：到点/快到点的日程 → 经通知中枢推一句关心。

    与 M2 提醒循环的区别：提醒回传用户原话；这里是机器人**自己记下的**
    安排到期，主动问一句（情绪 + 日程 + 人设语气）。关心后 mark_notified
    幂等去重（防重复打扰）。
    """
    from .emotion.schedule import format_due_line
    interval = max(60.0, float(getattr(Config, "EMO_SCHEDULE_WINDOW", 3600.0) or 3600.0) / 6)
    while True:
        try:
            await asyncio.sleep(interval)
            if not (Config.EMO_ENABLE and Config.EMO_SCHEDULE_EXTRACT):
                continue
            due = emotion_store.due_schedules(
                "", now=time.time(),
                window=float(getattr(Config, "EMO_SCHEDULE_WINDOW", 3600.0) or 3600.0),
                limit=5)
            for item in due:
                scope = item.get("scope") or ""
                if not scope:
                    continue
                ctx = {"user_id": int(item.get("user_id") or 0), "group_id": 0,
                       "channel": "napcat"}
                if scope.startswith("g:"):
                    try:
                        ctx["group_id"] = int(scope.split(":", 1)[1] or 0)
                    except ValueError:
                        continue
                # 关心文案：事实给全，语气交给模型（personification 原则）
                line = format_due_line(item)
                text = ("（系统事件：你记着对方的一个安排——%s。用人设语气"
                        "主动关心一句，别提'系统'二字。）" % line)
                try:
                    care, perr = await ai_chatter.say("emo:care", text, user_id=0)
                except Exception as e:  # noqa: BLE001
                    log.warning(jlog("emotion_care_llm_failed", sid=item.get("id"),
                                     error=type(e).__name__))
                    continue
                if perr or not care:
                    log.info(jlog("emotion_care_empty", sid=item.get("id"),
                                  detail=str(perr)[:120]))
                    continue
                await send_text(ctx, care)
                emotion_store.mark_notified(item["id"], scope)
                log.info(jlog("emotion_care_sent", sid=item["id"], scope=scope,
                              title=(item.get("title") or "")[:40]))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 单轮失败不杀循环
            log.warning(jlog("emotion_care_loop_error", error=type(e).__name__,
                             detail=str(e)[:160]))


async def _handle_emotion_command(ev, trace_id: str) -> None:
    """/情感 命令族（M3 2026-10-20）：我 / 日程 / 加日程 / 完成 / 删 /
    好感 / 心情。

    权限：查看/管理自己的情感与日程对所有人开放；`好感`（榜单）仅主人
    （亲疏数据敏感）。
    """
    if not Config.EMO_ENABLE:
        return
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key
    parts = (ev.raw_text or "").strip().split(None, 2)
    sub = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""
    low = sub.lower()
    from .emotion.mood import relation_stage
    from .emotion.schedule import format_due_line

    # ---- 我：我的情感状态 ----
    if low in ("我", "me", "状态", "status", ""):
        try:
            text = emotion_manager.status_text(scope, ev.user_id)
        except EmotionError as e:
            await _reply(ctx, sess, str(e), title="情感")
            return
        await _reply(ctx, sess, text, title="情感状态")
        return

    # ---- 日程：列出 ----
    if low in ("日程", "schedule", "安排"):
        try:
            items = emotion_store.list_schedules(scope)
        except EmotionError as e:
            await _reply(ctx, sess, str(e), title="日程")
            return
        if not items:
            await _reply(ctx, sess, "没记着你的安排（聊天里提到面试/考试之类"
                                    "本喵会自己记下）", title="日程")
            return
        lines = ["记着的安排："]
        now = time.time()
        lines.extend(format_due_line(s, now=now) for s in items[:15])
        await _reply(ctx, sess, "\n".join(lines), title="日程")
        return

    # ---- 加日程：手动补一条（时间用「N 分钟后/明天 HH:MM」简写）----
    if low in ("加日程", "记一下", "add"):
        head = rest.split(None, 1)
        if len(head) < 2:
            await _reply(ctx, sess,
                         "用法：/情感 加日程 <时间> <安排>\n"
                         "时间写法：30分钟 / 2小时 / 明天09:00 / 后天14:30\n"
                         "例：/情感 加日程 明天09:00 面试", title="加日程")
            return
        when, title = head[0], head[1].strip()
        try:
            due = _parse_when(when)
        except ValueError as e:
            await _reply(ctx, sess, str(e), title="加日程")
            return
        try:
            item = emotion_store.add_schedule(title, due, scope,
                                              user_id=ev.user_id, source="cmd")
        except EmotionError as e:
            await _reply(ctx, sess, str(e), title="加日程")
            return
        await _reply(ctx, sess,
                     "记下了：%s（%s）" % (title[:40], time.strftime(
                         "%m-%d %H:%M", time.localtime(due))), title="加日程")
        return

    # ---- 完成/删 ----
    if low in ("完成", "done"):
        if not rest:
            await _reply(ctx, sess, "用法：/情感 完成 <编号>", title="日程")
            return
        ok = emotion_store.mark_done(rest.split()[0], scope)
        await _reply(ctx, sess, "标记完成" if ok else "没找到那个日程", title="日程")
        return
    if low in ("删", "删除", "del", "忘掉"):
        if not rest:
            await _reply(ctx, sess, "用法：/情感 删 <编号>", title="日程")
            return
        ok = emotion_store.soft_delete_schedule(rest.split()[0], scope)
        await _reply(ctx, sess, "已删除" if ok else "没找到那个日程", title="日程")
        return

    # ---- 好感：榜单（仅主人）----
    if low in ("好感", "好感度", "关系"):
        if not _is_owner(ev.user_id):
            await _reply(ctx, sess, "看亲疏榜单是主人的权限喵。", title="权限不足")
            return
        try:
            rows = emotion_store.top_favors(scope, limit=10)
        except EmotionError as e:
            await _reply(ctx, sess, str(e), title="好感度")
            return
        if not rows:
            await _reply(ctx, sess, "这个会话还没有好感度记录。", title="好感度")
            return
        lines = ["好感度排行："]
        for i, r in enumerate(rows, 1):
            lines.append("%d. QQ %s — %d/100（%s）" % (
                i, r["user_id"], r["favor"],
                r.get("relation") or relation_stage(int(r["favor"]))))
        await _reply(ctx, sess, "\n".join(lines), title="好感度")
        return

    # ---- 心情：手动调情绪（仅主人，调试/演出用）----
    if low in ("心情", "mood"):
        if not _is_owner(ev.user_id):
            await _reply(ctx, sess, "调心情是主人的权限喵。", title="权限不足")
            return
        if not rest:
            await _reply(ctx, sess,
                         "用法：/情感 心情 <情感名> [强度0-1]\n"
                         "情感名：开心/生气/难过/平静/兴奋/疲倦/焦虑…",
                         title="心情")
            return
        head = rest.split()
        name = head[0]
        try:
            intensity = float(head[1]) if len(head) > 1 else 0.6
        except ValueError:
            intensity = 0.6
        try:
            snap = emotion_manager.bump(scope, ev.user_id, emotion=name,
                                        intensity=intensity)
        except EmotionError as e:
            await _reply(ctx, sess, str(e), title="心情")
            return
        await _reply(ctx, sess, "心情调到：%s（愉悦 %.2f / 兴奋 %.2f）" % (
            snap["mood"]["text"], snap["mood"]["valence"],
            snap["mood"]["arousal"]), title="心情")
        return

    # ---- 帮助 ----
    await _reply(ctx, sess,
                 "情感命令族：\n"
                 "· /情感 我 —— 心情 + 对你的亲疏 + 记着的安排\n"
                 "· /情感 日程 —— 列出安排\n"
                 "· /情感 加日程 <时间> <安排> —— 手动补一条\n"
                 "· /情感 完成/删 <编号> —— 标记完成/删除\n"
                 "· /情感 好感 —— 好感度排行（主人）\n"
                 "· /情感 心情 <情感名> [强度] —— 调心情（主人）",
                 title="情感系统")


def _parse_when(when: str) -> float:
    """简写时间 → Unix 时间戳（30分钟 / 2小时 / 明天09:00 / 后天14:30 / 3天）。

    解析不了显式 ValueError（不猜、不兜底）。
    """
    import re as _re
    w = (when or "").strip()
    now = time.time()
    m = _re.match(r"^(\d+)\s*(分钟|分|m)$", w, _re.I)
    if m:
        return now + int(m.group(1)) * 60
    m = _re.match(r"^(\d+)\s*(小时|时|h)$", w, _re.I)
    if m:
        return now + int(m.group(1)) * 3600
    m = _re.match(r"^(\d+)\s*(天|d)$", w, _re.I)
    if m:
        return now + int(m.group(1)) * 86400
    m = _re.match(r"^(今天|明天|后天)\s*(\d{1,2}):(\d{2})$", w)
    if m:
        base = {"今天": 0, "明天": 1, "后天": 2}[m.group(1)]
        import datetime as _dt
        d = _dt.date.today() + _dt.timedelta(days=base)
        return time.mktime(_dt.datetime.combine(
            d, _dt.time(int(m.group(2)), int(m.group(3)))).timetuple())
    raise ValueError("时间看不懂（%s）——用 30分钟 / 2小时 / 明天09:00 这种写法" % w)


# ==================== M4 多模态 + 语音（2026-10-20）====================
# multimodal 包（vision/audio/album/manager）不 import 主包；视觉/ASR/TTS
# 各能力独立配置，没配哪项就哪项显式告知（不互相回落、不本地兜底）。


async def _mm_image_path(img: str) -> str:
    """图片引用 → 可被视觉模型吃的引用（本地路径 / http URL / base64 data）。

    NapCat 给的可能是本地缓存路径或 http 直链；本地文件不存在时显式报错
    （不静默跳过——看不了图要说清楚）。
    """
    s = str(img or "").strip()
    if s.lower().startswith(("http://", "https://")):
        return s
    if s.startswith("base64://"):
        return s[len("base64://"):]  # 交给 vision 转 data URI
    p = Path(s)
    if p.exists():
        return str(p)
    raise VisionError("图片取不到（%s）——既不是能访问的链接，本机也没这个文件"
                      % s[:80])


async def _handle_image_message(ev, trace_id: str) -> None:
    """纯图片消息 → 视觉模型描述 + 入档案（MM_AUTO_LOOK）。"""
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    imgs = list(getattr(ev, "images") or [])
    if not imgs:
        return
    try:
        ref = await _mm_image_path(imgs[0])
        desc = await media_manager.look(
            ref, "用一句话描述这张图，像跟朋友吐槽一样自然",
            scope=ev.scope_key, user_id=ev.user_id, ref=str(imgs[0]))
    except (VisionError, AlbumError) as e:
        log.info(jlog("mm_look_failed", scope=ev.scope_key,
                      detail=str(e)[:160], trace_id=trace_id))
        await _reply(ctx, sess, str(e), title="看图")
        return
    except Exception as e:  # noqa: BLE001 — 显式化，绝不静默
        log.warning(jlog("mm_look_error", error=type(e).__name__,
                         detail=str(e)[:160], trace_id=trace_id))
        await _reply(ctx, sess, "看图时出岔子了（%s）" % type(e).__name__,
                     title="看图")
        return
    await _reply(ctx, sess, desc, title="本喵看到了")
    log.info(jlog("mm_image_described", scope=ev.scope_key,
                  user_id=ev.user_id, trace_id=trace_id))


async def _handle_voice_message(ev, trace_id: str) -> None:
    """主人私聊语音 → ASR 转写 → 写回 ev.raw_text（后续流程当普通消息）。"""
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    voices = list(getattr(ev, "voices") or [])
    if not voices:
        return
    ref = str(voices[0].get("url") or voices[0].get("file") or "").strip()
    if not ref:
        await _reply(ctx, sess, "收到语音但拿不到音频地址喵。", title="语音")
        return
    tmp = None
    try:
        if ref.lower().startswith(("http://", "https://")):
            # OneBot 常给 http 直链 → 先落盘再转码
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=30.0) as cli:
                resp = await cli.get(ref)
            if resp.status_code != 200 or not resp.content:
                raise AudioError("语音下载失败（HTTP %d）" % resp.status_code)
            import tempfile as _tf
            fd, tmp = _tf.mkstemp(suffix=".amr")
            os.close(fd)
            Path(tmp).write_bytes(resp.content)
            text = await media_manager.listen(tmp)
        else:
            text = await media_manager.listen(ref)
    except AudioError as e:
        log.info(jlog("mm_asr_failed", detail=str(e)[:160], trace_id=trace_id))
        await _reply(ctx, sess, str(e), title="语音")
        return
    except Exception as e:  # noqa: BLE001 — 显式化，绝不静默
        log.warning(jlog("mm_asr_error", error=type(e).__name__,
                         detail=str(e)[:160], trace_id=trace_id))
        await _reply(ctx, sess, "听语音时出岔子了（%s）" % type(e).__name__,
                     title="语音")
        return
    finally:
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)  # OPS-01 临时文件释放
            except Exception:  # noqa: BLE001
                pass
    if not text:
        return
    # 转写文本当作用户打的字继续走后续流程（零递归：本分支只在路由最前跑一次）
    ev.raw_text = text
    log.info(jlog("mm_asr_done", user_id=ev.user_id, text_len=len(text),
                  trace_id=trace_id))
    await _reply(ctx, sess, "（听到）%s" % text[:200], title="语音转写")


async def _handle_media_command(ev, trace_id: str) -> None:
    """/看图 命令族（M4 2026-10-20）：看图 / 列表 / 看第N张 / 说 / 状态。

    权限：看图与历史召回对所有人开放（看的是本会话的图）；`说`（TTS）
    仅主人（会消耗合成额度）。
    """
    if not Config.MM_ENABLE:
        return
    ctx = _ctx(ev)
    sess = session_store.get(ev.user_id)
    scope = ev.scope_key
    parts = (ev.raw_text or "").strip().split(None, 2)
    sub = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""
    low = sub.lower()

    # ---- 状态：能力探测（诚实：没配就是没配）----
    if low in ("状态", "status", "能力"):
        caps = media_manager.capabilities()
        lines = ["多模态能力：",
                 "· 看图：%s" % ("可用" if caps["vision"] else "未配视觉模型"),
                 "· 听语音：%s" % ("可用" if caps["asr"] else "未配 ASR 模型"),
                 "· 说话：%s" % ("可用" if caps["tts"] else "未配 TTS 模型"),
                 "· API Key：%s" % ("已配" if caps["api_key"] else "未配")]
        if not caps["api_key"]:
            lines.append("\n先去「机器人配置 → 常规·AI 模式」把 Key 和地址填上。")
        await _reply(ctx, sess, "\n".join(lines), title="多模态状态")
        return

    # ---- 列表：历史图 ----
    if low in ("列表", "ls", "历史", ""):
        try:
            text = media_manager.recall(scope)
        except AlbumError as e:
            await _reply(ctx, sess, str(e), title="看图")
            return
        await _reply(ctx, sess, text, title="图片档案")
        return

    # ---- 看第 N 张 ----
    if low.isdigit() or (low.startswith("第") and low.rstrip("张个").isdigit()):
        n = int(low.strip("第").rstrip("张个")) if low.startswith("第") else int(low)
        try:
            item = media_manager.recall(scope, n=n)
        except AlbumError as e:
            await _reply(ctx, sess, str(e), title="看图")
            return
        cap = item.get("caption") or "（当时没描述）"
        await _reply(ctx, sess, "第 %d 张：%s" % (n, cap), title="历史图")
        # 原图能发就发（本地路径/URL）
        ref = str(item.get("ref") or "")
        if ref and ref != "(inline)":
            try:
                await send_image(ctx, ref)
            except Exception:  # noqa: BLE001 — 发图失败不影响文字回复
                pass
        return

    # ---- 看图：对本条消息带的图片（或指定 URL/路径）理解 ----
    if low in ("看图", "看", "描述", "look"):
        imgs = list(getattr(ev, "images") or [])
        target = rest.strip() or (imgs[0] if imgs else "")
        if not target:
            await _reply(ctx, sess,
                         "用法：/看图 <图片URL或路径>（或连同图片一起发）\n"
                         "· /看图 列表 —— 历史图\n· /看图 3 —— 看第 3 张",
                         title="看图")
            return
        try:
            ref = await _mm_image_path(target)
            desc = await media_manager.look(
                ref, rest.strip() and ("用户问：%s" % rest) or
                "描述这张图里有什么", scope=scope, user_id=ev.user_id,
                ref=str(target))
        except (VisionError, AlbumError) as e:
            await _reply(ctx, sess, str(e), title="看图")
            return
        await _reply(ctx, sess, desc, title="本喵看到了")
        return

    # ---- 说：TTS（仅主人）----
    if low in ("说", "说话", "tts", "念"):
        if not _is_owner(ev.user_id):
            await _reply(ctx, sess, "语音合成是主人的权限喵（会花额度）。",
                         title="权限不足")
            return
        if not rest:
            await _reply(ctx, sess, "用法：/看图 说 <要念的话>", title="语音合成")
            return
        try:
            data = await media_manager.speak(rest)
        except AudioError as e:
            await _reply(ctx, sess, str(e), title="语音合成")
            return
        try:
            await send_record(ctx, data)
            await _reply(ctx, sess, "说完了（%d 字节）" % len(data), title="语音合成")
        except Exception as e:  # noqa: BLE001 — 发送失败显式告知
            await _reply(ctx, sess, "语音合成好了但发不出去（%s）"
                         % type(e).__name__, title="语音合成")
        return

    # ---- 帮助 ----
    await _reply(ctx, sess,
                 "多模态命令族：\n"
                 "· /看图 —— 发图时一起发，或 /看图 <URL/路径>\n"
                 "· /看图 列表 —— 本会话历史图\n"
                 "· /看图 <序号> —— 看第 N 张（1=最新）\n"
                 "· /看图 说 <话> —— 语音念出来（主人）\n"
                 "· /看图 状态 —— 看哪些能力配好了",
                 title="多模态")


async def _route(ev, trace_id: str) -> None:
    """归一化之后的**统一业务路由**（与协议无关）。

    双轨并行：NapCat 由 handle_message 归一化、官方通道由 _handle_official_message
    归一化，两者都调用本函数 —— 意图/限流/队列/配额/会话逻辑零重复。
    """
    # 惰性 TTL 清理（OPS-02/03），成本极低
    session_store.ttl_cleanup()
    game_sessions.ttl_cleanup()

    allowed, reason = allow(ev)
    if not allowed:
        return  # 黑名单/非白名单 → 静默，不回任何提示（§8）

    # ---- M4 语音（2026-10-20）：主人私聊语音 → ASR 转写 → 当普通文字消息继续 ----
    # 只改 ev.raw_text（dataclass 字段可写），后续命令/关键词/AI 流程零改动，
    # 也不产生递归（本分支在路由最前，只跑一次）。
    if Config.MM_ENABLE and Config.MM_ASR_PRIVATE and not ev.is_group \
            and _is_owner(ev.user_id) and getattr(ev, "voices", None):
        await _handle_voice_message(ev, trace_id)

    # ---- M4 图片（2026-10-20）：纯图片无文字 → 自动看图（MM_AUTO_LOOK）----
    # 有文字时不抢流程（文字可能是指令/提问），只在「光发图」时看图说话。
    if Config.MM_ENABLE and Config.MM_AUTO_LOOK and getattr(ev, "images", None) \
            and not (ev.raw_text or "").strip():
        await _handle_image_message(ev, trace_id)

    # ---- 宿主机管家：接收 QQ 发来的文件（仅私聊主人，M6 文件互传）----
    # 位置说明：必须极靠前（在 AI 模式/关键词门禁之前）——文件消息 raw_text 为空，
    # 若落到 AI 聊天分支会变成「空文本对话」甚至触发生图，毫无意义。
    # 仅私聊 + 主人，群聊文件一律不收（防陌生人往宿主机塞文件）。
    _in_files = getattr(ev, "files", None)
    if Config.PC_ENABLE and _in_files and not ev.is_group and is_owner(ev.user_id):
        await _pc_receive_files(ev, trace_id)
        return

    # ---- §新增：生图通道交互式选择 —— 优先处理对 pending 的回答 ----
    ans = choice_sessions.answer(ev.user_id, ev.raw_text)
    if ans is not None:
        cs = choice_sessions.consume(ev.user_id)  # 取出并取消超时 timer
        if cs is not None:
            log.info(jlog("choice_answered", channel=ans, user_id=ev.user_id,
                          group_id=ev.group_id, trace_id=trace_id))
            ev.raw_text = cs.prompt
            ev.force_t2i = True
            await _execute_route_flow(ev, session_store.get(ev.user_id),
                                     forced_channel=ans, trace_id=trace_id)
            return
    # 有旧 pending 但本条并非选择词（用户转话题）→ 静默取消，避免超时后误提示
    if choice_sessions.has(ev.user_id):
        choice_sessions.consume(ev.user_id)

    # ---- AI 模式（2026-10-19）：/ai 开关命令 + 开启后拦截非命令消息走大模型 ----
    # 位置说明：必须在关键词门禁**之前**——AI 模式群里所有消息都要进大模型，
    # 而 KEYWORD_GATE 会把未命中关键词的消息静默丢弃（那是关闭态的规矩，不适用）。
    # / 与 # 开头的显式命令仍放行给后续路由（/ai off 的退出通道就在其中）。
    _ai_cmd = _parse_ai_cmd(ev.raw_text)
    if _ai_cmd is not None:
        await _handle_ai_command(ev, _ai_cmd, trace_id)
        return
    if ai_mode_store.is_on(ev.scope_key):
        if not (ev.raw_text or "").strip().startswith(("/", "#")):
            # 宿主机管家自然语言直通车优先级最高（仅私聊主人）：开着 AI 模式
            # 也能「电脑咋样」一步体检，不被聊天截走（M6 2026-10-20）。
            if Config.PC_ENABLE and await _try_pc_natural(ev, trace_id):
                return
            await _handle_ai_chat(ev, trace_id)
            return
        log.info(jlog("ai_mode_cmd_passthrough", scope=ev.scope_key,
                      user_id=ev.user_id, trace_id=trace_id))

    # ---- M2 提醒命令族（2026-10-20）：/提醒 创建/列表/取消/修改 ----
    # 位置：AI 模式块之后（开启时 / 开头命令 passthrough 到这里）、/pc 块之前
    # （两条命令族互不依赖）。以 / 开头，不会被关键词门禁误吞。
    if (ev.raw_text or "").strip().startswith("/提醒"):
        await _handle_remind_command(ev, trace_id)
        return

    # ---- M7 下载媒体命令族（2026-10-20）：/dl 下载/列表/取消/发我/订阅 ----
    if (ev.raw_text or "").strip().startswith("/dl"):
        await _handle_dl_command(ev, trace_id)
        return

    # ---- M9 运维面命令族（2026-10-20）：/ops git/ssh/wol/frp/通知 ----
    if (ev.raw_text or "").strip().startswith("/ops"):
        await _handle_ops_command(ev, trace_id)
        return

    # ---- M1 记忆命令族（2026-10-20）：/记忆 我/更正/忘记/事件/清理 ----
    if (ev.raw_text or "").strip().startswith("/记忆"):
        await _handle_memory_command(ev, trace_id)
        return

    # ---- M3 情感命令族（2026-10-20）：/情感 我/日程/加日程/好感/心情 ----
    if (ev.raw_text or "").strip().startswith("/情感"):
        await _handle_emotion_command(ev, trace_id)
        return

    # ---- M4 多模态命令族（2026-10-20）：/看图 看图/列表/序号/说/状态 ----
    if (ev.raw_text or "").strip().startswith("/看图"):
        await _handle_media_command(ev, trace_id)
        return

    # ---- 宿主机管家（M6 2026-10-20）：/pc 命令族 + 自然语言直通车 ----
    # 位置说明：必须在关键词门禁**之前**——「电脑咋样」不是关键词，会被门禁静默
    # 丢弃。自然语言直通车（仅私聊主人）的优先级已在上方 AI 模式块内处理；
    # 这里兜底 AI 模式关闭时的自然语言命中，以及 /pc 显式命令（以 / 开头，
    # 本就不被 AI 模式聊天拦截）。
    _pc_t = (ev.raw_text or "").strip()
    if Config.PC_ENABLE and _pc_t:
        if _pc_t.startswith("/pc"):
            await _handle_pc_command(ev, trace_id)
            return
        if await _try_pc_natural(ev, trace_id):
            return

    # ---- §主题编辑器（自定义主题）入口：自然语言触发，避免用户在 NapCat WebUI 里找不到 ----
    _t = ev.raw_text.strip().lower()
    if any(k in _t for k in ("主题编辑器", "编辑主题", "自定义主题", "配色编辑器", "theme editor")):
        log.info(jlog("theme_editor_hint", user_id=ev.user_id, group_id=ev.group_id, trace_id=trace_id))
        _url = "http://127.0.0.1:%s/pic/theme-editor" % Config.PORT
        await _reply(
            _ctx(ev), session_store.get(ev.user_id),
            "主题编辑器是独立 web 仪表盘：浏览器打开 %s 即可配色\n"
            "· 17 个色板字段拖拽即看效果（实时预览）\n"
            "· WCAG 对比度自动检查（防看不清）\n"
            "· 一键保存 / 应用（不用重启，立刻生效）\n"
            "预设：neon / light；不满意就拖个自己的" % _url,
            title="主题编辑器入口",
        )
        return

    # ---- 关键词门禁：未命中关键词一律静默不回（好友申请/闲聊不误触发、不烧配额）----
    if Config.KEYWORD_GATE:
        kw_kind, kw_text = match_keyword(ev.raw_text)
        if kw_kind == "test":
            log.info(jlog("keyword_hit", kind="test", user_id=ev.user_id, group_id=ev.group_id, trace_id=trace_id))
            await _reply(_ctx(ev), session_store.get(ev.user_id), Config.TEST_REPLY, title="打招呼")
            return
        elif kw_kind == "draw":
            log.info(jlog(
                "keyword_hit", kind="draw", user_id=ev.user_id, group_id=ev.group_id,
                prompt_len=len(kw_text), trace_id=trace_id,
            ))
            # 进入「在线/本地」交互式选择，不直接提交（§新增：避免误用通道）
            await _ask_channel_choice(ev, kw_text, trace_id)
            return
        elif kw_kind == "":
            # 纯 @机器人 且无文字 → 回帮助提示（2026-09-04 用户决策：
            # 避免群友 @ 了却没反应，摸不着头脑）。
            # 但 @机器人 + 仅发图片（无描述）不算"空"，应走到 route 走 i2i（图生图）。
            if ev.at_bot and not ev.raw_text.strip() and not ev.images:
                log.info(jlog("keyword_empty_at_help", user_id=ev.user_id, group_id=ev.group_id, trace_id=trace_id))
                c0 = _ctx(ev)
                s0 = session_store.get(ev.user_id)
                if await _try_send_pic(c0, "menu", sess=s0, **_menu_params(ev.user_id)):
                    return
                await _reply(c0, s0, HELP_TEXT)
                return
            # §修复（2026-09-11）：画廊上下文里回复纯数字编号看原图。
            # KEYWORD_GATE 会把"5"判为「未命中关键词」而静默丢弃，与 dispatcher 的
            # group_not_at 门禁共同造成"发了编号没反应"。dispatcher 已对 gallery.active_count
            # 放行，此处同样特例放行到路由层走 __PICK__（route 已把纯数字映射为 __PICK__）。
            _num = (ev.raw_text or "").strip()
            if gallery.active_count(ev.user_id) and _num.isdigit() and 1 <= len(_num) <= 2:
                log.info(jlog("keyword_gallery_bypass", user_id=ev.user_id,
                              group_id=ev.group_id, trace_id=trace_id, raw=_num))
                await _execute_route_flow(ev, session_store.get(ev.user_id),
                                         forced_channel=None, trace_id=trace_id)
                return
            # 注意："cmd"（斜杠指令）必须落到此处之外，正常放行
            log.info(jlog(
                "keyword_miss", user_id=ev.user_id,
                text_len=len(ev.raw_text), trace_id=trace_id,
            ))
            return  # 静默：不回任何包

    # ---- §6 连通性自检（渐进式上线第 1 步）----
    if ev.raw_text.strip().lower() in ("/ping", "ping") or _translate_maid(ev.raw_text.strip()) == "/ping":
        from .channel import NAP_CAT

        if getattr(ev, "channel", NAP_CAT) != NAP_CAT:
            txt = "pong。QQ 官方通道在线（WebSocket 直连，不依赖挂机客户端），别瞎操心。"
        else:
            st = connector.status()
            txt = ("pong。协议端在线（%s），连了 %d 次，没挂，别瞎操心。"
                   % (st["online"], st["connect_count"]))
        await _reply(_ctx(ev), session_store.get(ev.user_id), txt, title="在线状态")
        return

    # ---- §3.3~§3.7 入站安全/限流/路由/提交/回传：抽到 _execute_route_flow 复用 ----
    await _execute_route_flow(ev, session_store.get(ev.user_id), forced_channel=None, trace_id=trace_id)


async def _send_video_result(ctx: dict, result: dict) -> None:
    """§7.3：优先直发 video 段，失败（超限/不支持）则回退发下载链接。"""
    url = result.get("video_url") or ""
    try:
        await send_video(ctx, url, at_user=False)
    except Exception as e:
        log.warning(jlog("video_direct_failed", error=type(e).__name__, detail=str(e)[:160]))
        await send_video_link(ctx, url)


async def _try_send_pic(ctx: dict, template: str, sess=None, **params) -> bool:
    """尝试渲染并发送自绘图片；**任何失败都返回 False**，由调用方回落纯文本。

    为什么必须能失败：渲染依赖 Pillow 与系统字体，在未知机器上可能不可用。
    宁可回一句朴素文字，也不能让机器人「不回话」（CLOSE-02 禁止静默失败）。
    """
    if not pic_mod.enabled():
        return False
    theme_name = getattr(sess, "pic_theme", None) or None
    try:
        _t0 = time.perf_counter()
        data = await pic_mod.render(template, theme_name=theme_name, **params)
        _render_ms = (time.perf_counter() - _t0) * 1000
        _t1 = time.perf_counter()
        await send_pic(ctx, data)
        _send_ms = (time.perf_counter() - _t1) * 1000
        # 区分「重绘慢」与「发图（经 NapCat→QQ 上传）慢」：render_ms 见 pic 层日志，
        # 这里补 send_ms（图片字节经 OneBot 上传到 QQ 的网络耗时，常 0.5~2s）。
        log.info(jlog(
            "pic_reply", template=template, render_ms=round(_render_ms, 1),
            send_ms=round(_send_ms, 1), bytes=len(data),
        ))
        return True
    except Exception as e:  # noqa: BLE001 — 记录后降级，不吞异常
        log.warning(jlog(
            "pic_send_failed", template=template,
            error=type(e).__name__, detail=str(e)[:160],
        ))
        return False


async def _reply(ctx: dict, sess, text: str, *, template: str = "text",
                 title: str | None = None, **kwargs) -> None:
    """机器人主动回复的**统一出口**：优先文本卡图（text 模板），失败回落 _say。

    用户决策（2026-09-07）：**所有** @机器人 返回都改成图片（更直观）。
    - **永不静默**：图片渲染失败一律回落纯文本（CLOSE-02）；
    - **不破坏既有体验**：保留随机延迟、表情包、撤回、history 旁路。
    - **少侵入**：只是把直接 `await _say(ctx, text)` 换成 `await _reply(ctx, sess, text)`，
      `_say` 仍是基础设施，新代码可继续直接调用。
    """
    if not text:
        return
    if await _try_send_pic(ctx, template, sess=sess, body=text, title=title, **kwargs):
        return
    await _say(ctx, text)


def _split_placeholder(text: str) -> tuple[str, str]:
    """`__TASK__@@abc` → ("__TASK__", "abc")；无 `@@` 时 arg 为空串。"""
    if "@@" in (text or ""):
        a, b = text.split("@@", 1)
        return a, (b or "").strip()
    return (text or ""), ""


def _is_owner(user_id) -> bool:
    """是否主人（插队/清空/监控开关等高危命令的权限闸）。"""
    try:
        owners = {str(x).strip() for x in (Config.BOT_OWNER_QQ or ()) if str(x).strip()}
        return str(user_id).strip() in owners
    except Exception:
        return False


def _menu_params(user_id) -> dict:
    """按身份（主人 / 一般）组装帮助菜单图参数，并套用配置页的「菜单与帮助」自定义。

    - 文案：配置留空 → 回退内置默认（pic/templates/menu.py 的 DEFAULT_*）；
    - 条目：配置留空 → 内置默认；主人额外附「主人专属」段（可用 PIC_MENU_SHOW_OWNER 关掉）。
    纯读 Config（热生效）+ 纯函数解析，不碰 I/O。
    """
    from .pic.templates import menu as menu_tpl
    is_owner = _is_owner(user_id) and bool(getattr(Config, "PIC_MENU_SHOW_OWNER", True))
    title = (getattr(Config, "PIC_MENU_TITLE", "") or "").strip() or menu_tpl.DEFAULT_TITLE
    if is_owner:
        # 2026-09-16：主人菜单标题独立配置（空则跟随一般菜单标题）
        title = ((getattr(Config, "PIC_MENU_OWNER_TITLE", "") or "").strip() or title)
        subtitle = ((getattr(Config, "PIC_MENU_OWNER_SUBTITLE", "") or "").strip()
                    or menu_tpl.DEFAULT_OWNER_SUBTITLE)
        footer = ((getattr(Config, "PIC_MENU_OWNER_FOOTER", "") or "").strip()
                  or menu_tpl.DEFAULT_OWNER_FOOTER)
    else:
        subtitle = ((getattr(Config, "PIC_MENU_SUBTITLE", "") or "").strip()
                    or menu_tpl.DEFAULT_SUBTITLE)
        footer = ((getattr(Config, "PIC_MENU_FOOTER", "") or "").strip()
                  or menu_tpl.DEFAULT_FOOTER)
    items = menu_tpl.parse_items(getattr(Config, "PIC_MENU_ITEMS", "") or "")
    if items is None:
        items = list(menu_tpl.GENERAL_ITEMS)
    else:
        items = list(items)
    if is_owner:
        owner_items = menu_tpl.parse_items(getattr(Config, "PIC_MENU_OWNER_ITEMS", "") or "")
        if owner_items is None:
            owner_items = list(menu_tpl.OWNER_ITEMS)
        items = items + list(owner_items)
    return {"items": items, "title": title, "subtitle": subtitle, "footer": footer, "owner": is_owner}


def _fmt_job_line(j: dict) -> str:
    """任务一行摘要（列表用）。"""
    state_zh = {"queued": "排队中", "running": "生成中", "done": "已完成",
                "failed": "失败", "canceled": "已取消"}.get(j.get("state"), j.get("state"))
    pos = ""
    if j.get("state") == "queued":
        pos = "第%d位 " % (j.get("position") or 0)
    p = str(j.get("prompt") or "")
    p = p if len(p) <= 18 else p[:18] + "…"
    return "· #%s %s%s —— %s" % (j.get("seq"), pos, state_zh, p)


# ---------------------------------------------------------------------------
# 任务中心命令实现（2026-09-08）
# ---------------------------------------------------------------------------
def _fmt_main_tasks_summary(snap: dict) -> str:
    """主项目后台任务摘要（「任务」命令附带；完整详情/结果图走「主项目任务」）。"""
    running = snap.get("running") or []
    queued = snap.get("queued") or []
    recent = snap.get("recent") or []

    def _short(it: dict) -> str:
        return (it.get("prompt") or it.get("name") or "")[:24] or "?"

    lines = ["—— 主项目后台 ——"]
    if running:
        lines.append("【正在生图 %d 张】%s" % (
            len(running), "、".join(_short(x) for x in running[:3])))
    if queued:
        lines.append("【排队中 %d 张】%s" % (
            len(queued), "、".join(_short(x) for x in queued[:3])))
    if recent:
        lines.append("【最近完成 %d 张】最新：%s" % (len(recent), _short(recent[0])))
    if not (running or queued or recent):
        lines.append("暂时没任务，后台闲着")
    lines.append("完整详情/结果图：发「主项目任务」")
    return "\n".join(lines)


async def _cmd_task(ev, sess, arg: str) -> None:
    """`任务 [号]`：不带号列我最近 5 条；带号看单条详情（含结果图）。"""
    ctx = _ctx(ev)
    if not arg:
        items = queue_mgr.recent(user_id=ev.user_id, limit=5)
        if not items:
            await _reply(ctx, sess, "你还没投过任务。发段描述本喵就给你画（本地通道会自动排队）。",
                         title="我的任务")
            return
        body = "\n".join(_fmt_job_line(j) for j in items)
        # 2026-09-16：用户要求「任务」顺带看主项目后台在跑啥（复用 8099 统一任务 API）
        try:
            msnap = await infer_listener.snapshot(limit=5)
            msum = _fmt_main_tasks_summary(msnap)
            if msum:
                body += "\n\n" + msum
        except Exception as e:  # noqa: BLE001 — 主项目挂了不能影响「我的任务」
            log.warning(jlog("task_main_summary_failed", error=type(e).__name__))
        await _reply(ctx, sess, body + "\n\n看某条详情：「任务 <号>」；重发结果图：「再看 <号>」",
                     title="我的任务（最近 %d 条）" % len(items))
        return

    j = queue_mgr.get(arg)
    if not j:
        await _reply(ctx, sess, "没找到任务 %s。说「队列」看看现在都有啥。" % arg, title="查无此任务")
        return
    state_zh = {"queued": "排队中", "running": "生成中", "done": "已完成",
                "failed": "失败", "canceled": "已取消"}.get(j.get("state"), j.get("state"))
    lines = [
        "任务号 #%s（平台号 %s）" % (j.get("seq"), j.get("task_id") or "-"),
        "状态：%s" % state_zh,
        "描述：%s" % str(j.get("prompt") or "")[:80],
    ]
    if j.get("params"):
        lines.append("参数：%s" % json.dumps(j["params"], ensure_ascii=False)[:120])
    if j.get("error"):
        lines.append("错误：%s" % str(j["error"])[:120])
    if j.get("started_at") and j.get("finished_at"):
        lines.append("耗时：%s" % _human_dur(float(j["finished_at"]) - float(j["started_at"])))
    await _reply(ctx, sess, "\n".join(lines), title="任务详情")
    # 已完成 → 顺手把结果图发出来（不用再敲「再看」）
    if j.get("state") == "done" and j.get("images"):
        try:
            await send_images(ctx, j["images"], at_user=bool(ctx.get("group_id")))
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("task_detail_send_failed", error=type(e).__name__))


async def _cmd_recent(ev, sess) -> None:
    """`最近任务`：全局最近 10 条（含别人的，主人排查用）。"""
    ctx = _ctx(ev)
    items = queue_mgr.recent(limit=10)
    if not items:
        await _reply(ctx, sess, "最近没有任何任务。", title="最近任务")
        return
    body = "\n".join(_fmt_job_line(j) for j in items)
    await _reply(ctx, sess, body, title="最近任务（全部 %d 条）" % len(items))


async def _cmd_queue(ev, sess) -> None:
    """`队列`：排队看板。"""
    ctx = _ctx(ev)
    snap = queue_mgr.snapshot()
    lines = ["队列上限 %d · 当前排队 %d · 运行中 %d"
             % (snap["max"], snap["queued_count"], snap["running_count"])]
    if snap.get("running"):
        r = snap["running"]
        lines.append("\n【正在跑】#%s %s（%s）"
                     % (r.get("seq"), _job_prompt_short(r, 20),
                        str(r.get("task_id") or "-")))
    if snap["queued"]:
        lines.append("\n【排队中】")
        for i, j in enumerate(snap["queued"][:10], 1):
            lines.append("%d. #%s %s" % (i, j.get("seq"), _job_prompt_short(j, 20)))
        if snap["queued_count"] > 10:
            lines.append("…还有 %d 条" % (snap["queued_count"] - 10))
    else:
        lines.append("\n（队列是空的，现在投立刻就能跑）")
    if not snap.get("consumer_alive"):
        lines.append("\n⚠️ 队列消费者没在跑，任务不会被处理（bridge 可能刚重启）")
    await _reply(ctx, sess, "\n".join(lines), title="队列看板")


async def _cmd_cancel(ev, sess, arg: str) -> None:
    """`取消 <号>`：本人或主人可取消。"""
    ctx = _ctx(ev)
    res = await queue_mgr.cancel(arg, user_id=ev.user_id, is_owner=_is_owner(ev.user_id))
    if res.get("ok"):
        await _reply(ctx, sess, "已取消 #%s。" % (res.get("job", {}).get("seq") or arg),
                     title="已取消")
    else:
        await _reply(ctx, sess, res.get("error") or "取消失败", title="取消失败")


async def _cmd_promote(ev, sess, arg: str) -> None:
    """`插队 <号>`：仅主人。"""
    ctx = _ctx(ev)
    res = await queue_mgr.promote(arg, is_owner=_is_owner(ev.user_id))
    if res.get("ok"):
        await _reply(ctx, sess, "已把 #%s 提到队首。" % (res.get("job", {}).get("seq") or arg),
                     title="已插队")
    else:
        await _reply(ctx, sess, res.get("error") or "插队失败", title="插队失败")


async def _cmd_clear(ev, sess) -> None:
    """`清空队列`：仅主人，只清排队的（不打断正在跑的）。"""
    ctx = _ctx(ev)
    res = await queue_mgr.clear(is_owner=_is_owner(ev.user_id))
    if res.get("ok"):
        await _reply(ctx, sess, "清空了 %d 条排队中的任务（正在跑的那条没动）。"
                     % res.get("cleared", 0), title="已清空")
    else:
        await _reply(ctx, sess, res.get("error") or "清空失败", title="清空失败")


async def _cmd_again(ev, sess, arg: str) -> None:
    """`再看 <号>`：重发结果图（不重新生成、不占队列）。"""
    ctx = _ctx(ev)
    j = queue_mgr.get(arg)
    if not j:
        await _reply(ctx, sess, "没找到任务 %s。" % arg, title="查无此任务")
        return
    if j.get("state") != "done":
        state_zh = {"queued": "排队中", "running": "生成中",
                    "failed": "失败", "canceled": "已取消"}.get(j.get("state"), j.get("state"))
        await _reply(ctx, sess, "#%s 还没完成（当前：%s），出图后本喵会主动发给你。"
                     % (j.get("seq"), state_zh), title="还没好")
        return
    imgs = j.get("images") or []
    if not imgs:
        await _reply(ctx, sess, "#%s 当时没捞到图片地址，只能重画了。" % j.get("seq"),
                     title="没有结果图")
        return
    try:
        await send_images(ctx, imgs, at_user=bool(ctx.get("group_id")))
    except Exception as e:  # noqa: BLE001
        await _reply(ctx, sess, "重发失败（%s）。" % type(e).__name__, title="发图失败")


# ---------------------------------------------------------------------------
# 历史画廊（2026-09-11）：`历史 [页码]` 看九宫格缩略图 → 回复编号看原图
# ---------------------------------------------------------------------------
async def _cmd_gallery(ev, sess, arg: str) -> None:
    """`历史 [分类] [页码]` / 画廊上下文内「下一页」：把主项目最近出图拼成九宫格。

    分类：文生图 / 图生图 / 留空=全部；页码默认 1。翻页说「历史 2」或「下一页」。
    """
    ctx = _ctx(ev)
    parsed = (arg or "").strip()
    # 画廊上下文内的翻页口令（下一页/上一页/翻页）——沿用上次分类与页码。
    # 词表/方向统一由 gallery.nav_delta 判定（与 dispatcher 免 @ 放行同源，
    # 不再各写一份正则——此前正则只覆盖"下一|上[一页]"，"下页"漏判）。
    delta = gallery.nav_delta(parsed)
    if delta and gallery.peek(ev.user_id):
        last_cls, last_page = gallery.peek(ev.user_id)
        cls, page = last_cls, max(1, last_page + delta)
    else:
        cls, page = gallery.parse_gallery_arg(parsed)
    label = {"t2i_local": "文生图", "i2i": "图生图", "": "全部"}[cls]

    await _say(ctx, "我去翻翻相册…（%s · 第 %d 页）" % (label, page))

    items, err, total = await gallery.fetch_recent(page=page, type_filter=cls)
    if err:
        await _reply(ctx, sess, "翻不动相册：%s（主项目没开？）" % err, title="历史画廊")
        return
    if not items:
        if page > 1:
            # 翻页翻过头（常见的「下一页」一路点到底）——据实报总页数并给出回退口令，
            # 不再含糊地说"这页没记录"让用户以为画廊坏了。
            pages = max(1, (total + gallery.PAGE_SIZE - 1) // gallery.PAGE_SIZE) if total else page
            await _reply(ctx, sess,
                         "已经翻到底啦（共 %d 页）。回到开头说「历史 1」，或换分类说「历史 图生图」。"
                         % pages, title="历史画廊")
            return
        scope = "" if label == "全部" else ("「%s」" % label)
        await _reply(ctx, sess, "相册还是空的——%s这页没有出过图的记录。" % scope,
                     title="历史画廊")
        return
    try:
        data = await gallery.build_page(items)
    except Exception as e:  # noqa: BLE001 — 拼图失败降级为文字清单，绝不静默
        log.warning(jlog("gallery_render_failed", error=type(e).__name__,
                         detail=str(e)[:160]))
        await _reply(ctx, sess, "缩略图拼接失败了（%s）。" % type(e).__name__,
                     title="历史画廊")
        return

    gallery.remember(ev.user_id, items, cls=cls, page=page)
    await send_pic(ctx, data)

    lines = ["%d. [%s] %s" % (i, it["kind"], (it["prompt"] or "无描述")[:20])
             for i, it in enumerate(items, 1)]
    total_pages = max(1, (total + gallery.PAGE_SIZE - 1) // gallery.PAGE_SIZE) if total else page + 1
    await _reply(
        ctx, sess,
        "第 %d/%d 页 · %s · %d 张：\n%s\n\n回复编号看原图（比如「3」）；翻页说「历史 %d」或「下一页」。"
        % (page, total_pages, label, len(items), "\n".join(lines), page + 1),
        title="历史画廊",
    )


async def _cmd_pick(ev, sess, arg: str) -> None:
    """回复编号 → 发该张原图（大图）。"""
    ctx = _ctx(ev)
    it = gallery.pick(ev.user_id, arg)
    if not it:
        await _reply(ctx, sess,
                     "你最近没翻过相册（或编号过期了，%d 分钟有效）。先说「历史」看看缩略图。"
                     % int(gallery.VIEW_TTL // 60), title="选原图")
        return
    url = gallery.abs_url(it.get("image_url") or "")
    if not url:
        await _reply(ctx, sess, "这条记录没有图片地址，换一张吧。", title="选原图")
        return
    try:
        await send_image(ctx, url, at_user=bool(ctx.get("group_id")))
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("gallery_pick_send_failed", error=type(e).__name__, detail=str(e)[:160]))
        _detail = str(e)
        if "Timeout" in _detail or "1200" in _detail:
            # NapCat 发消息超时（retcode=1200）：多半是快速登录 cookie 过期，需重新扫码登录
            hint = ("发原图失败：NapCat 发消息超时（retcode 1200）——多半是登录态过期，"
                    "去 NapCat 重新扫码登录后重试（见 qq_bridge/logs/napcat_boot.log）。")
        else:
            hint = "发原图失败（%s）。" % type(e).__name__
        await _reply(ctx, sess, hint, title="发图失败")
        return
    await _reply(ctx, sess,
                 "第 %s 张 · %s · %s\n任务号 %s"
                 % (arg, it["kind"], (it["prompt"] or "无描述")[:40],
                    str(it.get("task_id") or "")[:8]),
                 title="原图")


async def _cmd_monitor(ev, sess, enable: bool) -> None:
    """`监控 开/关`：仅主人。复用 config_api.apply_config（写 .env + 热生效）。

    不自己写 .env —— 复用既有配置通道，保证与「机器人配置」页一致（铁律 9）。
    """
    ctx = _ctx(ev)
    if not _is_owner(ev.user_id):
        await _reply(ctx, sess, "监控开关只有主人能动。", title="没权限")
        return
    try:
        from .config_api import apply_config
        res = apply_config({"MONITOR_ENABLE": "true" if enable else "false"})
        if res.get("errors"):
            await _reply(ctx, sess, "改配置失败：%s" % json.dumps(res["errors"], ensure_ascii=False),
                         title="设置失败")
            return
    except Exception as e:  # noqa: BLE001
        await _reply(ctx, sess, "改配置出错（%s），去「机器人配置」页手动改吧。" % type(e).__name__,
                     title="设置失败")
        return
    if enable:
        await _reply(ctx, sess,
                     "监控已打开：任务失败 / 超时 / 平台持续繁忙时本喵私聊提醒你。\n"
                     "轮询间隔 %s 秒，可在「机器人配置 → 常规·任务队列」调。"
                     % getattr(Config, "MONITOR_INTERVAL", 30.0), title="监控已开")
    else:
        await _reply(ctx, sess, "监控已关闭，本喵不打扰你了。", title="监控已关")


def _manju_api_base() -> str:
    """主项目 API 基础地址（运行时读 Config，热生效）。"""
    return str(getattr(Config, "MANJU_API_BASE", "http://127.0.0.1:7788/api/v1") or "http://127.0.0.1:7788/api/v1").rstrip("/")


async def _manju_get(path: str, timeout: float = 10.0) -> tuple[dict | None, str]:
    """调用主项目 GET API，返回 (data, error)。

    只经 HTTP，不 import 主项目模块；失败返回明确错误文本。
    """
    url = _manju_api_base() + path
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as cli:
            resp = await cli.get(url)
        try:
            j = resp.json()
        except Exception as e:  # noqa: BLE001
            return None, "主项目返回不是 JSON（%s）" % type(e).__name__
        if resp.status_code >= 400:
            return None, "主项目 HTTP %d" % resp.status_code
        data = j.get("data") if isinstance(j, dict) else None
        return (data if isinstance(data, dict) else j) or {}, ""
    except httpx.ConnectError as e:
        return None, "连不上主项目（%s）" % str(e)[:120]
    except Exception as e:  # noqa: BLE001
        return None, "拉取失败（%s）" % type(e).__name__


def _fmt_bytes(b: int | float | None) -> str:
    """字节 → 人类可读（GB/MB）。"""
    if b is None:
        return "—"
    try:
        n = float(b)
    except (TypeError, ValueError):
        return "—"
    g = n / 1024 / 1024 / 1024
    if g >= 1:
        return "%.1f GB" % g
    return "%.1f MB" % (n / 1024 / 1024)


def _fmt_hw(snap: dict) -> str:
    """把 /system/hardware 快照格式化成 QQ 文本（不用 emoji，避免图片/字体渲染成方块）。"""
    lines: list[str] = []
    cpu = snap.get("cpu") or {}
    if cpu:
        lines.append("CPU：%s%%  ·  %s 核 %s 线程" % (
            cpu.get("percent") or "—",
            cpu.get("count_physical") or "—",
            cpu.get("count_logical") or "—",
        ))
    mem = snap.get("mem") or {}
    if mem:
        lines.append("内存：%s / %s  ·  %s%%" % (
            _fmt_bytes(mem.get("used")),
            _fmt_bytes(mem.get("total")),
            mem.get("percent") or "—",
        ))
    disk = snap.get("disk") or {}
    if disk:
        lines.append("磁盘 %s：%s / %s  ·  %s%%" % (
            disk.get("path") or "",
            _fmt_bytes(disk.get("used")),
            _fmt_bytes(disk.get("total")),
            disk.get("percent") or "—",
        ))
    gpus = snap.get("gpus") or []
    if gpus:
        for g in gpus:
            lines.append("GPU%d %s：%.1f%%  ·  显存 %s / %s  ·  %s°C  ·  %.1f W / %.1f W" % (
                g.get("index", 0),
                g.get("name") or "",
                g.get("utilization") or 0,
                _fmt_bytes(g.get("mem_used")),
                _fmt_bytes(g.get("mem_total")),
                g.get("temperature") or "—",
                g.get("power_draw") or 0,
                g.get("power_limit") or 0,
            ))
    elif cpu or mem or disk:
        lines.append("GPU：未检测到（或没有 NVIDIA 显卡）")
    updated = ""
    ts = snap.get("ts")
    if ts:
        updated = "\n更新于 %s" % time.strftime("%H:%M:%S", time.localtime(ts))
    if not lines:
        return "主项目返回了空的硬件快照。"
    return "\n".join(lines) + updated


async def _cmd_hw(ev, sess) -> None:
    """`资源` / `/hw`：拉主项目 /system/hardware 实时快照发给用户。"""
    ctx = _ctx(ev)
    snap, err = await _manju_get("/system/hardware", timeout=10.0)
    if err:
        await _reply(ctx, sess, "拉硬件占用失败：%s" % err, title="硬件占用")
        return
    if not snap:
        await _reply(ctx, sess, "主项目没返回硬件快照。", title="硬件占用")
        return
    # 优先发环形色块图（绿=空闲/黄=中等/红=高占用）；图片渲染失败回落纯文本（CLOSE-02）
    if await _try_send_pic(ctx, "hw", sess=sess, snap=snap):
        return
    await _reply(ctx, sess, _fmt_hw(snap), title="本地硬件占用")


async def _cmd_mtasks(ev, sess, arg: str) -> None:
    """`主项目任务 [页码]`：从推理监控页拉主项目后台的生图任务，直接看在跑啥、生成啥图。

    数据来源即 8099/tasks 所用的统一任务 API（GET /tasks/unified?type=image）。
    """
    ctx = _ctx(ev)
    limit = int(getattr(Config, "TASK_LISTEN_LIMIT", 10) or 10)
    try:
        snap = await infer_listener.snapshot(limit=limit)
    except Exception as e:  # noqa: BLE001
        await _reply(ctx, sess,
                     "拉主项目生图任务失败了（%s）。主项目或推理监控页可能没开？"
                     % type(e).__name__, title="拉取失败")
        return
    if not (snap.get("running") or snap.get("queued") or snap.get("recent")):
        await _reply(ctx, sess, "主项目这会儿没有生图任务（后台闲着呢）。", title="主项目生图任务")
        return
    text, imgs = infer_listener.format_snapshot(snap)
    # 文本卡（自绘，失败回落纯文本），结果图另发
    await _reply(ctx, sess, text, title="主项目生图任务")
    # 结果图：最近完成的（最多 4 张），让用户直接看到后台生成了啥
    if imgs:
        try:
            await send_images(ctx, imgs, at_user=bool(ctx.get("group_id")))
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("mtasks_send_img_failed", error=type(e).__name__))


async def _cmd_mlisten(ev, sess, enable: bool) -> None:
    """`生图监听 开/关`：仅主人。复用 config_api.apply_config（写 .env + 热生效）。

    与「监控 开/关」同通道，保证与「机器人配置」页一致（铁律 9）。
    """
    ctx = _ctx(ev)
    if not _is_owner(ev.user_id):
        await _reply(ctx, sess, "生图监听开关只有主人能动。", title="没权限")
        return
    try:
        from .config_api import apply_config
        res = apply_config({"TASK_LISTEN_ENABLE": "true" if enable else "false"})
        if res.get("errors"):
            await _reply(ctx, sess, "改配置失败：%s" % json.dumps(res["errors"], ensure_ascii=False),
                         title="设置失败")
            return
    except Exception as e:  # noqa: BLE001
        await _reply(ctx, sess, "改配置出错（%s），去「机器人配置」页手动改吧。" % type(e).__name__,
                     title="设置失败")
        return
    if enable:
        await _reply(ctx, sess,
                     "生图监听已打开：主项目开始生图 / 生图完成，本喵私聊提醒你（完成还补发结果图）。\n"
                     "轮询间隔 %s 秒，可在「机器人配置 → 常规·任务队列」调。"
                     % getattr(Config, "TASK_LISTEN_INTERVAL", 15.0), title="生图监听已开")
    else:
        await _reply(ctx, sess, "生图监听已关闭。", title="生图监听已关")


def _human_dur(seconds: float) -> str:
    """秒 → 中文时长（与 pic/canvas.human_duration 同口径，避免再引一个依赖）。"""
    s = int(max(0, seconds))
    if s < 60:
        return "%d 秒" % s
    m, sec = divmod(s, 60)
    if m < 60:
        return "%d 分 %02d 秒" % (m, sec)
    h, m = divmod(m, 60)
    return "%d 小时 %02d 分" % (h, m)


async def _handle_reply(ev, sess, text: str, used: int, quota: int) -> None:
    """处理 reply 类路由（含入口占位符）。"""
    ctx = _ctx(ev)

    # ---- 任务中心命令（2026-09-08）----
    cmd, arg = _split_placeholder(text)
    if cmd == "__TASK__":
        await _cmd_task(ev, sess, arg)
        return
    if cmd == "__RECENT__":
        await _cmd_recent(ev, sess)
        return
    if cmd == "__QUEUE__":
        await _cmd_queue(ev, sess)
        return
    if cmd == "__CANCEL__":
        await _cmd_cancel(ev, sess, arg)
        return
    if cmd == "__PROMOTE__":
        await _cmd_promote(ev, sess, arg)
        return
    if cmd == "__CLEAR__":
        await _cmd_clear(ev, sess)
        return
    if cmd == "__AGAIN__":
        await _cmd_again(ev, sess, arg)
        return
    if cmd == "__GALLERY__":
        await _cmd_gallery(ev, sess, arg)
        return
    if cmd == "__PICK__":
        await _cmd_pick(ev, sess, arg)
        return
    if cmd in ("__MONITOR_ON__", "__MONITOR_OFF__"):
        await _cmd_monitor(ev, sess, cmd == "__MONITOR_ON__")
        return

    if text == "__HW__":
        await _cmd_hw(ev, sess)
        return

    # ---- 监听主项目生图任务（2026-09-13）：从推理监控页 /tasks 获取 ----
    if cmd == "__MTASKS__":
        await _cmd_mtasks(ev, sess, arg)
        return
    if cmd in ("__MLISTEN_ON__", "__MLISTEN_OFF__"):
        await _cmd_mlisten(ev, sess, cmd == "__MLISTEN_ON__")
        return

    if text == "__QUOTA__":
        # 优先发配额图（环形进度更直观），渲染失败回落原纯文本
        if await _try_send_pic(ctx, "quota", sess=sess, used=used, quota=quota):
            return
        await _reply(ctx, sess, "今天你已经造了 %d / %d，悠着点。" % (used, quota),
                     title="今日配额")
        return
    if text == "__STATUS__":  # noqa: F841 — noqa 是为避免重复提示
        # 先查自己 → 旁观者查全局任务簿 → 队列通道兜底 → 闲。
        # 2026-09-11：本地生图全走队列（queue_t2i）后不经 taskmgr，全局簿永远空，
        # /status 误报「闲着呢」——用户在群里问进度被回"闲着呢"但后台明明有任务。
        info = task_mgr.get_active_for(ev.user_id)
        if info is None:
            info = task_mgr.get_any_active(exclude_user_id=ev.user_id)
        if info is None:
            # 队列通道兜底：正在跑 → 排队等待（busy 退避期间只有 queued）
            info = queue_mgr.running_status() or queue_mgr.pending_status()
        if info is None:
            await _reply(ctx, sess, "闲着呢，没活儿干（你倒是给本喵找点事啊）。",
                         title="任务状态")
            return
        if info.get("state") == "queued":
            # 排队态：没有"正在画"的任务，只有等着画的一堆——不发进度卡
            # （百分比无意义），直接报排队实况（2026-09-11）。
            waited = max(0, int(time.time() - info["started_at"]))
            await _reply(
                ctx, sess,
                "还有 %d 条排队没画呢（最早那条是群友 %d 的，已等 %d 秒）。\n"
                "主项目那边还占着（本地出图串行，一次一张），等它腾出手本喵接着画。"
                % (int(info.get("queued_behind") or 0),
                   int(info.get("user_id") or 0), waited),
                title="队列排队中",
            )
            return
        elapsed = max(0, int(time.time() - info["started_at"]))
        kind_zh = {"t2i": "生图", "i2i": "改图", "video": "视频"}.get(info["kind"], info["kind"])
        trace_short = (info.get("trace_id") or "")[:8]
        behind = int(info.get("queued_behind") or 0)
        behind_txt = ("，前面还排着 %d 条" % behind) if behind > 0 else ""
        # 优先发进度条图（百分比用「已耗时/看门狗超时」诚实估算）
        if await _try_send_pic(
            ctx, "progress", sess=sess,
            percent=progress_tpl.from_elapsed(elapsed, Config.TASK_TIMEOUT),
            elapsed=elapsed, kind=info.get("kind") or "t2i",
            prompt=info.get("prompt") or "", trace_id=info.get("trace_id") or "",
        ):
            return
        if info.get("user_id") == ev.user_id:
            # 发起人问自己：报自己的进度
            await _reply(
                ctx, sess,
                "你那单%s还在画呢（已等 %d 秒，trace=%s%s），急也没用，画完本喵会叫你的。" % (kind_zh, elapsed, trace_short, behind_txt),
                title="任务进度",
            )
        else:
            # 旁观者问：报"群里有谁的任务在跑"（拿不到昵称就用 user_id，诚实）
            await _reply(
                ctx, sess,
                "群友 %d 的%s还在画（已等 %d 秒，trace=%s%s），等他画完本喵才能接你的活。" % (
                    int(info.get("user_id") or 0), kind_zh, elapsed, trace_short, behind_txt,
                ),
                title="任务进度",
            )
        return
    if text == "__MODE_VIDEO__":
        await _reply(ctx, sess, "切视频模式了，直接发描述本喵就给你生成。", title="模式已切换")
        return
    if text == "__MODE_IMAGE__":
        await _reply(ctx, sess, "切回生图模式了。", title="模式已切换")
        return

    # ---- 图片主题切换（会话级，影响该用户后续收到的自绘图片）----
    if text == "__THEME_NEON__":
        sess.pic_theme = "neon"   # 直接赋值 + touch：Session 无 set 方法（sess.set 是死代码，已被删）
        sess.touch()
        await _reply(ctx, sess, "换深色霓虹了，够酷了吧。", title="主题已切换")
        return
    if text == "__THEME_LIGHT__":
        sess.pic_theme = "light"
        sess.touch()
        await _reply(ctx, sess, "换极简白底了，清爽。", title="主题已切换")
        return
    if text == "__THEME_AUTO__":
        sess.pic_theme = None     # None=跟随全局（含 auto 按时段）
        sess.touch()
        await _reply(ctx, sess, "主题交给时间决定：19点到早上7点深色，其余浅色。", title="主题已切换")
        return

    # ---- 帮助：优先发菜单图，失败回落文本卡 ----
    if text == HELP_TEXT:
        if await _try_send_pic(ctx, "menu", sess=sess, **_menu_params(ev.user_id)):
            return

    await _reply(ctx, sess, text)


async def _ask_channel_choice(ev: QQEvent, prompt: str, trace_id: str) -> None:
    """反问生图通道，进入 pending + 注册超时（借鉴 nonebot-plugin-waiter 超时取消）。"""
    cs = choice_sessions.ask(ev.user_id, ev.group_id, prompt)
    try:
        loop = asyncio.get_running_loop()
        cs.timer = loop.call_later(
            Config.CHOICE_TIMEOUT,
            lambda: asyncio.create_task(_choice_timed_out(ev.user_id, ev.group_id)),
        )
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("choice_timer_failed", error=type(e).__name__, trace_id=trace_id))
    if Config.LOCAL_TEMPLATE_ID:
        await _say(
            _ctx(ev),
            "🎨 生图通道选哪个？回复【1】本地(ComfyUI·竖屏) 或【2】在线(云端)。\n"
            "%d 秒内不选自动取消，重发「生图 描述」可再选。" % Config.CHOICE_TIMEOUT,
        )
    else:
        # §修复（2026-09-05）：LOCAL_TEMPLATE_ID 未配置时本地通道不可用（7788 /qq/image-generate
        # 拒回退 SD1.5 废图，强制要求 template_id），不提供【1】避免用户选了又报错，
        # 诚实告知原因引导用户选【2】或联系主人配模板。万一用户执意发"1"/"本地"，
        # 7788 会透传"无 template"业务错误（文案已够清楚），符合 by-design 严格性。
        log.info(jlog("ask_channel_no_local_template", user_id=ev.user_id, group_id=ev.group_id, trace_id=trace_id))
        await _say(
            _ctx(ev),
            "🎨 本地 ComfyUI 通道暂未配置模板（需在 qq_bridge/.env 设 LOCAL_TEMPLATE_ID）。\n"
            "本次只能走在线云端，回复【2】在线(云端) 即可出图。\n"
            "%d 秒内不选自动取消，重发「生图 描述」可再选。" % Config.CHOICE_TIMEOUT,
        )


async def _choice_timed_out(user_id: int, group_id: int) -> None:
    """超时回调：清理 pending 并提示（OPS-02 超时回收，避免半开对话堆积）。"""
    cs = choice_sessions.consume(user_id)
    if cs is None:
        return  # 已被回答消费，无需处理
    log.info(jlog("choice_timeout", user_id=user_id, group_id=group_id))
    await send_text(
        {"user_id": user_id, "group_id": group_id},
        "生图选择超时（%d秒）已取消。直接发「生图 你的描述」可重新选通道。" % Config.CHOICE_TIMEOUT,
    )


# ===========================================================================
# 任务中心（2026-09-08）：队列事件 → QQ 消息
# ---------------------------------------------------------------------------
# notifier 由队列在关键节点回调。**enqueued 不在这里发**——命令侧需要附带
# 排队位次 / 幂等命中信息，那边回得更有用（且能省一条消息）。
# ===========================================================================
def _job_ctx(job: dict) -> dict:
    """队列任务 → 发消息用的 ctx（还原当时的私聊/群场景）。"""
    return {"user_id": job.get("user_id"), "group_id": job.get("group_id") or 0}


def _job_tag(job: dict) -> str:
    return "#%s" % (job.get("seq") or "?")


def _job_prompt_short(job: dict, n: int = 24) -> str:
    p = str(job.get("prompt") or "")
    return p if len(p) <= n else p[:n] + "…"


async def _queue_notifier(event: str, job: dict) -> None:
    """队列事件通知。任何异常都不能影响队列推进（CLOSE-02：记录而非吞掉）。"""
    ctx = _job_ctx(job)
    tag, short = _job_tag(job), _job_prompt_short(job)
    try:
        if event == "started":
            # 提交成功 → 连续繁忙计数归零（监控用）
            monitor_mgr.note_submitted()
            await _reply(ctx, None,
                         "轮到你的活了（%s · %s），本喵开始画。\n平台任务号 %s"
                         % (tag, short, str(job.get("task_id") or "")[:8]),
                         title="开始生成")
        elif event == "busy":
            # 平台忙 → 累加连续繁忙计数（达阈值时监控会告警一次）
            monitor_mgr.note_busy()
            n = int(job.get("busy_retries") or 0)
            total = int(getattr(Config, "QUEUE_BUSY_RETRY", 3) or 3)
            mins = int(float(getattr(Config, "QUEUE_BUSY_BACKOFF", 300.0) or 300.0) / 60)
            await _reply(ctx, None,
                         "本地显卡正忙（多半是网页端在跑图），你的 %s 还在队首等着。\n"
                         "第 %d/%d 次等待，%d 分钟后我再试一次。"
                         % (tag, n, total, mins),
                         title="平台忙 · 排队中")
        elif event == "done":
            imgs = job.get("images") or []
            if imgs:
                try:
                    await send_images(ctx, imgs, at_user=bool(ctx.get("group_id")))
                except Exception as e:  # noqa: BLE001
                    log.warning(jlog("queue_done_send_failed", error=type(e).__name__))
                    await _reply(ctx, None, "图画好了但发图失败（%s），用「再看 %s」重发。"
                                 % (type(e).__name__, tag), title="发图失败")
                return
            await _reply(ctx, None, "任务 %s 完成了，但没捞到图片地址（用「再看 %s」试试）"
                         % (tag, tag), title="完成（无图）")
        elif event == "failed":
            await _reply(ctx, None,
                         "你的 %s（%s）没跑成：\n%s" % (tag, short, job.get("error") or "未知原因"),
                         title="任务失败")
        elif event == "canceled":
            await _reply(ctx, None, "已取消 %s（%s）。" % (tag, short), title="已取消")
        elif event == "promoted":
            await _reply(ctx, None, "主人发话了，%s 给你提到队首。" % tag, title="已插队")
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — 通知失败不能拖垮队列
        # 2026-09-11 修：字段名不能再叫 event——jlog(event, **fields) 第一个
        # 位置参数就是 event，再传 event= 会 TypeError「got multiple values」，
        # 导致**真正的失败原因被丢失**（外层只看到 jlog 自己的报错）。
        log.warning(jlog("queue_notifier_failed", notify_event=event,
                         error=type(e).__name__, detail=str(e)[:200]))


async def _handle_queue_submit(ev, sess, r, used: int, quota: int, trace_id: str) -> None:
    """本地通道生图 → 入持久队列（不直发，避免打断网页端正在跑的图）。"""
    ctx = _ctx(ev)
    prompt = (r.payload.get("prompt") or "").strip()
    params = dict(r.payload.get("params") or {})

    # 长度：与直发生图同一套规则（超长按句末截断，不直接拒）
    prompt, was_truncated = truncate_prompt(prompt)
    if was_truncated:
        try:
            await _say(ctx, "你的描述超了 %d 字上限，本喵按句末截到 %d 字继续排队。"
                       % (Config.PROMPT_MAX_LEN, len(prompt)))
        except Exception:  # noqa: BLE001
            pass
    ok, why = validate_prompt(prompt)
    if not ok:
        rate_limiter.quota_refund(ev.user_id)
        await _say(ctx, why)
        return

    scene = "group" if getattr(ev, "is_group", False) else "private"
    res = await queue_mgr.enqueue(ev.user_id, ev.group_id, scene, prompt, params)
    if not res.get("ok"):
        rate_limiter.quota_refund(ev.user_id)  # CLOSE-04：失败退配额
        await _reply(ctx, sess, res.get("error") or "入队失败", title="投不进去")
        return

    job = res["job"]
    tag, short = _job_tag(job), _job_prompt_short(job, 40)
    if res.get("duplicate"):
        await _reply(ctx, sess,
                     "这条你刚投过（%d 秒内重复不下第二遍），原任务还在第 %d 位。\n%s"
                     % (int(getattr(Config, "QUEUE_IDEMPOTENT_TTL", 60) or 60),
                        res.get("position") or 0, short),
                     title="重复了")
        return

    pos = res.get("position") or 0
    extra = ""
    if params:
        bits = []
        for k, zh in (("resolution", "尺寸"), ("ratio", "比例"), ("steps", "步数"),
                      ("count", "数量"), ("style", "风格")):
            if params.get(k) is not None:
                bits.append("%s %s" % (zh, params[k]))
        if bits:
            extra = "\n参数：" + " · ".join(bits)
    await _reply(ctx, sess,
                 "已入队 · 第 %d 位（队列上限 %d）\n任务号 %s\n%s%s\n\n"
                 "查进度：「任务 %s」；等不及：「取消 %s」"
                 % (pos, queue_mgr.max_size, tag, short, extra, tag, tag),
                 title="已入队")
    log.info(jlog("queue_enqueued", seq=job.get("seq"), position=pos,
                  user_id=ev.user_id, trace_id=trace_id))


async def _execute_route_flow(ev: QQEvent, sess, forced_channel, trace_id: str) -> None:
    """§3.3~§3.7 统一执行流：安全 → 限流/配额 → 路由 → 校验 → 提交 → 回传。

    生图通道交互式选择（forced_channel 非空）与常规场景共用此流，
    仅 task_mgr.run 的通道选择不同。
    """
    # ---- §3.5 入站内容安全 ----
    ok, why = safety.check_prompt(ev.raw_text)
    if not ok:
        await _say(_ctx(ev), "这内容本喵可不接，换个别的吧（微笑）。")
        return

    # 本条带图 → 更新待编辑源图（§3.8：先发图后发文字也能关联）
    if ev.images:
        sess.set(ev.user_id, {"pending_image": ev.images[0]})

    # ---- §3.6 冷却 + 每日配额（/help、/status 等零成本指令不受限）----
    text = ev.raw_text.strip()
    maid_cmd = _translate_maid(text)
    # 2026-09-11：历史画廊与「回复编号看原图」属查看类，不该扣生图额度/限流
    _free_prefixes = ("/help", "/status", "/quota", "/mode", "/ping", "/gallery")
    is_free_cmd = (
        text.lower().startswith(_free_prefixes)
        or text.startswith(("历史", "图库", "相册", "我的图"))
        or text.isdigit()  # 纯数字=选第 N 张原图
        or (maid_cmd is not None and maid_cmd.startswith(_free_prefixes))
    )
    if not is_free_cmd:
        ok, retry_after = rate_limiter.allow(ev.scope_key, action="gen")
        if not ok:
            await _say(_ctx(ev), "急什么，%.0f 秒后再来烦本喵。" % retry_after)
            return
        ok, used, quota = rate_limiter.quota_allow(ev.user_id)
        if not ok:
            await _say(_ctx(ev), "今天额度被你造光了（%d/%d），明天再来吧，本喵下班了。" % (used, quota))
            return
    else:
        used, quota = rate_limiter.peek_quota(ev.user_id)

    # ---- §3.3 意图路由 ----
    r = route_intent(ev, sess)

    # 零成本指令：就地答复
    if r.kind == "reply":
        await _handle_reply(ev, sess, r.text, used, quota)
        return

    # ---- 任务中心：本地通道入队（2026-09-08）----
    # 本地 ComfyUI 独占 + 入队会 cancel 别人 → 必须排队，不能直发。
    if r.kind == "queue_t2i":
        await _handle_queue_submit(ev, sess, r, used, quota, trace_id)
        return

    # ---- §3.6 prompt 长度（2026-09-07：超长自动截断而非拒，让任务能起来）----
    # 之前超长直接 validate_prompt 拒 → task_mgr.run 不跑 → 全局任务簿空 →
    # /status 永远回"闲着呢"，用户体感"看不到进度"。改为：超长按句末标点
    # 截断到 ≤PROMPT_MAX_LEN，写回 r.payload 让 task_mgr 用截断后的，
    # 并 _say 一行提示用户"已自动截断"。
    raw_prompt = r.payload.get("prompt") or ""
    trunc_prompt, was_truncated = truncate_prompt(raw_prompt)
    if was_truncated:
        r.payload["prompt"] = trunc_prompt
        log.info(jlog(
            "prompt_truncated",
            orig_len=len(raw_prompt), new_len=len(trunc_prompt),
            trace_id=trace_id,
        ))
        try:
            await _say(
                _ctx(ev),
                "你的描述 %d 字超了 %d 字上限，本喵按句末截到 %d 字继续画（想完整就精简重发）。"
                % (len(raw_prompt), Config.PROMPT_MAX_LEN, len(trunc_prompt)),
            )
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("trunc_notice_failed", error=type(e).__name__, trace_id=trace_id))
    ok, why = validate_prompt(r.payload.get("prompt") or "")
    if not ok:
        rate_limiter.quota_refund(ev.user_id)
        await _say(_ctx(ev), why)
        return

    # ---- §3.4 提交任务 ----
    ctx = _ctx(ev)
    try:
        await _say(ctx, "行了，本喵这就去画，等着（trace=%s）。" % trace_id[:8])
    except Exception as e:
        log.warning(jlog("ack_send_failed", error=type(e).__name__, trace_id=trace_id))

    try:
        result = await task_mgr.run(r, ev, sess, forced_channel=forced_channel)
    except Exception as e:
        # 不吞异常：记录 + 明确回传（CLOSE-02）
        log.error(jlog("taskmgr_raised", error=type(e).__name__, detail=str(e)[:200], trace_id=trace_id))
        rate_limiter.quota_refund(ev.user_id)
        await _say(ctx, "画崩了（%s），自己再试一次吧。" % type(e).__name__)
        return

    if not result.get("ok"):
        rate_limiter.quota_refund(ev.user_id)  # CLOSE-04
        err = result.get("error") or "未知错误"
        log.warning(jlog("task_failed", kind=result.get("kind"), error=err[:200], trace_id=trace_id))
        await _say(ctx, "画崩了：%s，别赖本喵，重发一次试试。" % err)
        return

    # ---- §3.7 媒体回传 ----
    try:
        if result["kind"] == "video":
            await _send_video_result(ctx, result)
        else:
            await send_images(ctx, result.get("images") or [], at_user=ev.is_group)
    except Exception as e:
        log.error(jlog("media_send_raised", error=type(e).__name__, detail=str(e)[:200], trace_id=trace_id))
        await _say(ctx, "图是画好了，发你那儿时出了岔子（%s），自己查查？" % type(e).__name__)
        return

    log.info(jlog("task_completed", kind=result["kind"], trace_id=trace_id))
