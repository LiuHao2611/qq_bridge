# -*- coding: utf-8 -*-
"""ratelimit（限流，设计文档 §3.6 / §7.1 / §8）。

职责：按 QQ 号 / 群做冷却与每日配额。

关键接口：allow(key, action) -> (ok, retry_after)
参数（参考文档 [REF-6]）：私聊冷却 10s、群冷却 5s、prompt ≤300、每日配额可配。

验收（§3.6）：超频返回"稍后再试"；不绕过平台资源独占（§7.1）。

设计取舍：用「单调时间戳 + 每 key 上次通过时间」实现冷却，比令牌桶更易验证
且不会因长时间空闲积累大量令牌导致突发放行（对外服务反而不希望突发）。
每日配额按自然日切分，跨天自动重置。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import Config, get_logger, jlog

log = get_logger()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


@dataclass
class _Bucket:
    last_pass: float = 0.0


@dataclass
class _Quota:
    day: str = field(default_factory=_today)
    used: int = 0


class RateLimiter:
    """冷却 + 每日配额。

    2026-09-07：cooldown/quota 从「实例属性」改为「property 透传 Config」，
    这样 config_api.apply_config() 对 core=false 的 GROUP_COOLDOWN / DAILY_QUOTA
    / PRIVATE_COOLDOWN 调用 setattr(Config, ...) 后立即生效，无需重启 bridge。
    构造参数仍保留作为单测 override（传 None 时回退到 Config，运行时修改的 Config 也生效）。
    """

    def __init__(
        self,
        private_cooldown: int | None = None,
        group_cooldown: int | None = None,
        daily_quota: int | None = None,
    ) -> None:
        self._private_cooldown_override = private_cooldown
        self._group_cooldown_override = group_cooldown
        self._daily_quota_override = daily_quota
        self._buckets: dict[str, _Bucket] = {}
        self._quotas: dict[int, _Quota] = {}

    @property
    def private_cooldown(self) -> int:
        # override=None 时读 Config，热改生效
        return self._private_cooldown_override if self._private_cooldown_override is not None else Config.PRIVATE_COOLDOWN

    @property
    def group_cooldown(self) -> int:
        return self._group_cooldown_override if self._group_cooldown_override is not None else Config.GROUP_COOLDOWN

    @property
    def daily_quota(self) -> int:
        return self._daily_quota_override if self._daily_quota_override is not None else Config.DAILY_QUOTA

    def cooldown_for(self, action_key: str) -> int:
        return self.group_cooldown if action_key.startswith("g:") else self.private_cooldown

    def allow(self, key: str, action: str = "draw") -> tuple[bool, float]:
        """冷却检查。key 形如 'u:10001'（私聊）或 'g:20001'（群）。

        返回 (ok, retry_after)。ok=False 时 retry_after 为还需等待的秒数。
        """
        bucket_key = "%s:%s" % (key, action)
        bucket = self._buckets.setdefault(bucket_key, _Bucket())
        cd = self.cooldown_for(key)
        now = time.time()
        elapsed = now - bucket.last_pass
        if elapsed < cd:
            retry_after = round(cd - elapsed, 1)
            log.info(jlog("ratelimit_blocked", key=key, action=action, retry_after=retry_after))
            return False, retry_after
        bucket.last_pass = now
        return True, 0.0

    def quota_allow(self, user_id: int) -> tuple[bool, int, int]:
        """每日配额。返回 (ok, used, quota)。配额<=0 表示不限。"""
        if self.daily_quota <= 0:
            return True, 0, 0
        q = self._quotas.get(user_id)
        today = _today()
        if q is None or q.day != today:
            q = _Quota(day=today, used=0)
            self._quotas[user_id] = q
        if q.used >= self.daily_quota:
            log.info(jlog("ratelimit_quota_exceeded", user_id=user_id, used=q.used, quota=self.daily_quota))
            return False, q.used, self.daily_quota
        q.used += 1
        return True, q.used, self.daily_quota

    def quota_refund(self, user_id: int) -> None:
        """任务失败时退还本次配额占用（对应 CLOSE-04 失败路径必退）。"""
        q = self._quotas.get(user_id)
        if q and q.used > 0:
            q.used -= 1

    def peek_quota(self, user_id: int) -> tuple[int, int]:
        q = self._quotas.get(user_id)
        if q is None or q.day != _today():
            return 0, self.daily_quota
        return q.used, self.daily_quota


rate_limiter = RateLimiter()
