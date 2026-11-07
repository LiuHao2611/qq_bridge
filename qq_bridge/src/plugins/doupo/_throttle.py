# -*- coding: utf-8 -*-
"""【已停用 · 禁止重新接入斗破链路】2026-11-07 21:17

用户第 3 次纠正后明确要求：「原本能输出图片的口令，不要降级为纯文字，记住了」。
本模块过去的 25s USER_GAP 会让连续发出的出图口令只回文字（更早一版甚至零回复），
两次线上被用户抓到。现在 `doupo/__init__.py` **完全不 import 本模块**，
8 个出图 handler 无条件渲染并发图。

本文件只作为独立限流工具类保留（算法单测见 `test_throttle.py`），
**禁止任何人在游戏口令链路里调用 `Throttle.check()`，或加回任何冷却 /
降级 / 静默逻辑**。若担心账号风控，必须先与用户确认，不得擅自改回。

历史背景（仅存档，不代表当前行为）：当初设计它是因为 `人物` 一句口令连出
两张图且无冷却，担心高频图片触发 QQ 风控；社区经验是「不要滥用发消息」。
滑动窗口 / 抖动等实现保留在下方，供其他非口令场景按需复用。
"""

from __future__ import annotations

import random
import time
from collections import deque
from typing import Callable, Deque

# ---------------------------------------------------------------------------
# 策略常量 —— 调参只改这一段
# ---------------------------------------------------------------------------
USER_GAP = 25.0        # 同一用户两次出图的最小间隔（秒）
GROUP_LIMIT = 6        # 单群在 WINDOW 秒内最多出图次数
GLOBAL_LIMIT = 15      # 全局在 WINDOW 秒内最多出图次数
WINDOW = 60.0          # 滑动窗口长度（秒）
JITTER = (0.35, 1.20)  # 出图前的随机延迟区间（秒）

_USER_PRUNE_AT = 512   # 用户上次响应时间表超过这个规模就清一次过期项


class Throttle:
    """滑动窗口限流器。时钟与随机源可注入，便于单测不依赖真实时间。"""

    def __init__(
        self,
        *,
        user_gap: float = USER_GAP,
        group_limit: int = GROUP_LIMIT,
        global_limit: int = GLOBAL_LIMIT,
        window: float = WINDOW,
        jitter: tuple[float, float] = JITTER,
        clock: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._user_gap = user_gap
        self._group_limit = group_limit
        self._global_limit = global_limit
        self._window = window
        self._jitter = jitter
        self._clock = clock
        self._rand = rand

        self._user_last: dict[str, float] = {}
        self._group_hits: dict[str, Deque[float]] = {}
        self._global_hits: Deque[float] = deque()

    # -- 内部 ---------------------------------------------------------------
    def _trim(self, dq: Deque[float], now: float) -> None:
        """丢弃窗口外的历史打点。"""
        edge = now - self._window
        while dq and dq[0] <= edge:
            dq.popleft()

    def _prune_users(self, now: float) -> None:
        if len(self._user_last) <= _USER_PRUNE_AT:
            return
        stale = [u for u, t in self._user_last.items() if now - t >= self._user_gap]
        for u in stale:
            del self._user_last[u]

    # -- 对外 ---------------------------------------------------------------
    def check(self, *, user: str, group: str | None) -> tuple[bool, str]:
        """判断本次出图是否放行。

        返回 (allow, reason)。reason 仅用于日志与单测断言，不回给用户。
        放行时会记账；被拒时不记账（否则重试会永久卡死）。
        """
        now = self._clock()

        last = self._user_last.get(user)
        if last is not None and now - last < self._user_gap:
            return False, "user_gap"

        if group is not None:
            gq = self._group_hits.get(group)
            if gq is not None:
                self._trim(gq, now)
                if len(gq) >= self._group_limit:
                    return False, "group_window"

        self._trim(self._global_hits, now)
        if len(self._global_hits) >= self._global_limit:
            return False, "global_window"

        # 通过 → 记账
        self._user_last[user] = now
        if group is not None:
            self._group_hits.setdefault(group, deque()).append(now)
        self._global_hits.append(now)
        self._prune_users(now)
        return True, "ok"

    def jitter_delay(self) -> float:
        """出图前应等待的随机秒数。"""
        lo, hi = self._jitter
        return lo + (hi - lo) * self._rand()
