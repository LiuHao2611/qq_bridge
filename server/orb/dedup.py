# -*- coding: utf-8 -*-
"""悬浮球建议去重与节流（设计方案 §2.3）。

同一类问题 5 分钟内只提醒一次；用户明确「忽略」后由前端 localStorage 处理（不在此层）。
本模块只负责「时间维度」的去重，与具体业务无关。
"""
import time


# 同一类建议的最小重复间隔（秒）= 5 分钟
DEDUP_TTL_SECONDS = 300


class DedupLRU:
    """极简去重器：key -> 下次允许的过期时间戳。

    should_emit(key)：
      - 若 key 在窗口内（未过期）→ 返回 False（抑制本次推送）
      - 否则刷新窗口并返回 True（允许推送）
    线程不安全（单进程 FastAPI + GIL 足够；如需多进程可换 Redis）。
    """

    def __init__(self, ttl: int = DEDUP_TTL_SECONDS):
        self.ttl = ttl
        self._store: dict = {}

    def should_emit(self, key: str) -> bool:
        now = time.time()
        exp = self._store.get(key)
        if exp is not None and now < exp:
            return False
        self._store[key] = now + self.ttl
        return True

    def reset(self, key: str = None):
        """清除某个 key（或全部）的抑制窗口，便于测试或手动刷新。"""
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)

    def __len__(self):
        return len(self._store)


# 进程级单例（与导演 Agent 同进程）
dedup = DedupLRU()
