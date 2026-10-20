# -*- coding: utf-8 -*-
"""ops.notify —— 通知中枢（M9 运维面，2026-10-20）。

需求（roadmap §M9）：「宿主机所有事件（下载完成/渲染完成/温度告警/CI 失败）
统一走 AI 人格化转述推送到指定群（『你的视频下好了喵』而非干巴日志）」。

设计（内部设计，无外部范式——monitor/ 的直发告警是反面对照：机器日志直接
甩给用户，没人话）：
  - **单一入口**：所有机器事件经 `hub.push(event)`（kind/title/detail/level）；
  - **人格化转述**：rephrase 回调（调用方注入 ai_mode.say 包装）把事件
    改写成机器人语气；转述失败**不静默直发原文**——log 显式记
    rephrase_failed 并附注「（人格化转述失败，原文照发）」（诚实优先，
    通知必须送达；这不算功能降级——通知内容没丢）；
  - **限流防刷屏**：同类事件（同 kind+title）冷却期内合并计数
    （OPS_NOTIFY_COOLDOWN，默认 300s；温度类告警 5 分钟一条才是人话）；
  - **目标**：OPS_NOTIFY_TARGET（QQ 号=私聊 / 负群号=群；空=只记日志不发），
    发送经 send 回调注入（本模块不碰 nonebot）；
  - **级别过滤**：level(info/warn/error) < OPS_NOTIFY_MIN_LEVEL 的只记日志；
  - **落账**：最近 50 条进内存环（/ops 通知 可看），不落盘（事件流无持久化
    价值，OPS-02 显式声明）。

事件接入策略（分期，如实记录）：一期服务 M9 自有事件（仓库巡检脏改动/
CI/测试失败、SSH 执行结果不推——那是命令响应）；M6 告警/M7 下载完成保持
既有直发链路（它们本就是人话，无需转述），二期按「机器事件才转述」原则
逐个收编。hub 的 push API 即统一契约，收编是接入问题不是重构。

本模块不 import nonebot / 不 import 主包：可裸单测。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..config import Config, get_logger, jlog

log = get_logger()

LEVEL_DEBUG = "debug"
LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"
_LEVELS = (LEVEL_DEBUG, LEVEL_INFO, LEVEL_WARN, LEVEL_ERROR)
_LEVEL_RANK = {LEVEL_DEBUG: 0, LEVEL_INFO: 1, LEVEL_WARN: 2, LEVEL_ERROR: 3}


@dataclass
class NotifyEvent:
    kind: str            # 事件类型（repo_dirty / ci_failed / ssh_done …）
    title: str           # 一句话标题（转述的素材）
    detail: str = ""     # 详情（转述可选参考）
    level: str = LEVEL_INFO
    scope: str = ""      # 来源会话（空=系统级，推给 OPS_NOTIFY_TARGET）
    ts: float = field(default_factory=time.time)

    def key(self) -> str:
        """限流键：同类事件 = 同 kind+title。"""
        return "%s::%s" % (self.kind, self.title[:40])


class NotificationHub:
    """通知中枢：push → 过滤/限流 → 转述 → 发送。回调全部注入。"""

    #: 内存环容量（/ops 通知 查看）
    HISTORY = 50

    def __init__(self, rephrase: Callable[[NotifyEvent], Awaitable[str]] | None = None,
                 send: Callable[[str, NotifyEvent], Awaitable[None]] | None = None,
                 target: str | None = None, min_level: str | None = None,
                 cooldown: float | None = None, enabled: bool | None = None) -> None:
        self.rephrase = rephrase
        self.send = send
        self.target = (target if target is not None
                       else str(getattr(Config, "OPS_NOTIFY_TARGET", "") or ""))
        self.min_level = min_level or str(
            getattr(Config, "OPS_NOTIFY_MIN_LEVEL", "") or LEVEL_INFO)
        self.cooldown = float(cooldown if cooldown is not None
                              else getattr(Config, "OPS_NOTIFY_COOLDOWN", 300.0))
        self.enabled = bool(enabled if enabled is not None
                            else getattr(Config, "OPS_NOTIFY_ENABLE", True))
        #: 限流键 → 上次推送时间
        self._last_sent: dict[str, float] = {}
        #: 限流键 → 冷却期内累计次数
        self._pending_count: dict[str, int] = {}
        #: 最近事件环（含未推送的，供查询）
        self._history: deque[NotifyEvent] = deque(maxlen=self.HISTORY)

    # ---------------- 事件入口 ----------------
    async def push(self, event: NotifyEvent) -> bool:
        """推一个事件。返回是否实际推送（False=被过滤/限流/未配目标）。

        任何失败路径都显式记日志（CLOSE-02），不静默吞事件。
        """
        if event.level not in _LEVELS:
            log.warning(jlog("notify_bad_level", kind=event.kind,
                             level=event.level))
            event.level = LEVEL_INFO
        self._history.append(event)

        if not self.enabled:
            log.info(jlog("notify_suppressed_disabled", kind=event.kind))
            return False
        if _LEVEL_RANK[event.level] < _LEVEL_RANK.get(self.min_level, 1):
            log.info(jlog("notify_suppressed_level", kind=event.kind,
                          level=event.level))
            return False
        if not self.target or self.send is None:
            log.info(jlog("notify_no_target", kind=event.kind,
                          target=self.target or "(空)"))
            return False

        # 限流：同类事件冷却期内累计，到点带计数补发
        now = time.time()
        key = event.key()
        last = self._last_sent.get(key, 0.0)
        if last and now - last < self.cooldown:
            self._pending_count[key] = self._pending_count.get(key, 0) + 1
            log.info(jlog("notify_throttled", kind=event.kind,
                          pending=self._pending_count[key]))
            return False
        self._last_sent[key] = now
        burst = self._pending_count.pop(key, 0)
        if burst:
            event.detail = (event.detail + "\n（冷却期内另有 %d 次同类事件未逐条推送）"
                            % burst)[:600]

        text = await self._rephrase(event)
        try:
            await self.send(text, event)
        except Exception as e:  # noqa: BLE001 — 发送失败显式记，不重试（防重复打扰）
            log.warning(jlog("notify_send_failed", kind=event.kind,
                             error=type(e).__name__, detail=str(e)[:160]))
            return False
        log.info(jlog("notify_sent", kind=event.kind, level=event.level,
                      text_len=len(text)))
        return True

    async def _rephrase(self, event: NotifyEvent) -> str:
        """人格化转述；失败 → 原文 + 显式附注（不静默、不假装转述成功）。"""
        if self.rephrase is None:
            return "[%s] %s\n%s" % (event.kind, event.title, event.detail)
        try:
            text = await self.rephrase(event)
            text = (text or "").strip()
            if text:
                return text
            log.warning(jlog("notify_rephrase_empty", kind=event.kind))
        except Exception as e:  # noqa: BLE001 — 转述失败：原文照发+附注（诚实）
            log.warning(jlog("notify_rephrase_failed", kind=event.kind,
                             error=type(e).__name__, detail=str(e)[:160]))
        return ("[%s] %s\n%s\n（人格化转述失败，原文照发）"
                % (event.kind, event.title, event.detail))

    # ---------------- 查询 ----------------
    def history(self, limit: int = 10) -> list[NotifyEvent]:
        return list(self._history)[-limit:]


#: 模块级单例（插件入口注入 rephrase/send；单测各自 new 隔离）
notify_hub = NotificationHub()
