# -*- coding: utf-8 -*-
"""QQ 官方通道主动消息日额度保护（docs/qq_official_bot_migration_plan.md §6.7）。

为什么需要：平台对**主动消息**有硬限制——单群 20 条/分钟、**每天最多 1000 条**
（[官方文档](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)）。
撞上会返回 `40034100`，用户侧表现为"机器人突然不说话了"。与其撞平台硬限，
不如自己先限（默认 900，留 10% 余量），超限主动降级为文本提示。

设计：
  * 按 (group_key, 日期) 计数，**跨天自动重置**（不依赖定时任务，读时比对日期）；
  * 只统计**主动消息**，被动回复不计数（平台口径：日上限写在"主动消息"频控节）；
  * 内存态，bridge 重启清零——宁可少限也不能误限（重启当天额度本来也没跑满）；
  * 单测可直接注入 Config.OFFICIAL_DAILY_GROUP_LIMIT（OPS-02：有清理者，按天清理）。
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any

from ..config import get_logger, jlog

log = get_logger()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


class OfficialLimiter:
    """主动消息日额度计数器。"""

    def __init__(self, limit_getter=None) -> None:
        self._count: dict[tuple, int] = {}
        self._day: str = _today()
        self._lock = Lock()
        self._limit_getter = limit_getter
        #: 已告警过的 key，避免每满一次刷一条日志
        self._warned: set = set()

    # ---------- 配置 ----------
    def _limit(self) -> int:
        if self._limit_getter is not None:
            return int(self._limit_getter() or 0)
        try:
            from ..config import Config

            return int(getattr(Config, "OFFICIAL_DAILY_GROUP_LIMIT", 900) or 0)
        except Exception:  # pragma: no cover
            return 900

    # ---------- 核心 ----------
    def _gc(self) -> None:
        """跨天重置（OPS-02/03：批量清理，带锁）。"""
        today = _today()
        if today != self._day:
            with self._lock:
                if today != self._day:  # 双检，避免并发重复清
                    self._count.clear()
                    self._warned.clear()
                    self._day = today
                    log.info(jlog("official_quota_reset", day=today))

    def used(self, group_key: str) -> int:
        self._gc()
        return self._count.get((str(group_key), self._day), 0)

    def remaining(self, group_key: str) -> int:
        return max(0, self._limit() - self.used(group_key))

    def allow(self, group_key: str, n: int = 1) -> bool:
        """是否还允许发 n 条主动消息。"""
        self._gc()
        if self._limit() <= 0:      # 0 = 不限制
            return True
        return self.used(group_key) + n <= self._limit()

    def add(self, group_key: str, n: int = 1) -> int:
        """记 n 条主动消息，返回已用数。"""
        self._gc()
        key = (str(group_key), self._day)
        with self._lock:
            cur = self._count.get(key, 0) + n
            self._count[key] = cur
        if not self.allow(group_key):
            if key not in self._warned:
                self._warned.add(key)
                log.warning(jlog(
                    "official_daily_limit_reached",
                    group_key=str(group_key), used=cur, limit=self._limit(),
                ))
        return cur


#: 全局单例（官方通道共用）
official_limiter = OfficialLimiter()
