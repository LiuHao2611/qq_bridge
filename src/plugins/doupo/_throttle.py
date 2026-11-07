# -*- coding: utf-8 -*-
"""斗破出图口令的频率护栏 —— 少发一张图，就少一分「像机器人」的证据。

【为什么需要这个文件】

在 linux.do / locdd 那些「挂了半年、几个月没掉线」的分享里，被反复强调的
不是某个更隐蔽的注入技巧，而是行为层面的克制。原话：

  ·「不要滥用发消息的功能，一般发消息多了符合机器人特征，小号很快就会风控」
  ·「我的机器人只做处理加群请求和踢人自动拉黑功能，像群发消息、@后回复的
     功能都被我删掉了」
  ·「就算是大号随便来两个人举报也会很快风控」

本插件里 `dp人物` 是典型的高特征行为：一句口令 → 连出两张图（境界动图 +
自绘人物卡），而且没有任何冷却，可以无限刷。`dp入道` / `斗破菜单` 同理。

本模块只约束「出图」这一类口令，理由：
  · 文本口令（dp修炼/突破/战斗）本身受游戏内冷却约束，且单条文本的信息量
    远小于一张图，风控权重不同；
  · 如果连文本也拦，会打断游戏机制，得不偿失；
  · 出图是唯一「零成本可无限重复」的高带宽行为，也是最像机器人的那一种。

三道闸：
  1) 同一用户冷却 —— 防止单人连刷
  2) 每群滑动窗口 —— 群是风控的观测单元，一个群一分钟刷 20 张图，
     和 20 个群各刷 1 张，在服务端是两个完全不同的信号
  3) 出图前随机抖动 —— 打散毫秒级规律，去掉机器节奏

【为什么不用 nonebot 的 run_preprocessor】

本机 nonebot 是 2.5.0，`Matcher.run_preprocessor` 在 2.5 里还不存在
（已实测 `hasattr(Matcher, "run_preprocessor") == False`）。因此改用
「priority 更小 + block=False 的 on_message 前置匹配器」作为唯一闸口：
放行时不 finish（后续 matcher 照跑），被限流时 finish() 直接掐断传播链。
这样 10 多个 handler 一行都不用改。

【为什么不复用 qq_bridge/ratelimit 里已有的 RateLimiter】

不是重复实现，是它根本不覆盖这条链路：

  · `qq_bridge.ratelimit.rate_limiter` 只被 `qq_bridge/__init__.py` 的主聊天处理器
    调用，而那套 matcher 是 `priority=10`。本插件的全部口令 matcher 是
    `priority=5 + block=True` —— 命中即阻断传播，消息永远走不到 priority=10。
    换句话说 doupo 的口令从来没有、也不会经过 `rate_limiter`。
  · 语义也不同：`RateLimiter.allow()` 是「单个 key 多久能再来一次」（默认私聊
    10s / 群 5s），粒度是 user 或 group 二选一。这里需要的是三层同时生效
    （同一用户跨所有群 / 单个群 / 全局）+ 出图前抖动，`RateLimiter` 表达不了。

若将来把 doupo 并进主链路，应改成复用 `RateLimiter` 而不是留两份。

【为什么被限流时静默不回话】

回一句「你太频繁了」本身就是一条消息，等于把限流行为变成了新的机器人特征，
而且会告诉群里所有人「这个号在限流」。所以这里选择静默丢弃。
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
