# -*- coding: utf-8 -*-
"""session（会话态，设计文档 §3.8）。

职责：每个 QQ 号一份轻量会话——默认模式、待编辑源图、当前任务 id。

关键接口：get(user_id) / set(user_id, patch) / ttl_cleanup()
验收（§3.8）：
  - 用户先发图、再发"改成赛博朋克"能关联源图（pending_image 生效）；
  - TTL 过期自动清理（OPS-02：持久数据声明 TTL + 清理者）。

实现要点：内存字典 + 惰性 TTL 清理（每次 get/set 顺带扫描，OPS-03 幂等分批）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Config, get_logger, jlog

log = get_logger()


@dataclass
class Session:
    user_id: int
    mode: str = "image"            # image | video
    pending_image: str | None = None  # 待编辑源图 url（图生图用）
    task_id: str | None = None     # 当前进行中的任务
    last_prompt: str = ""
    # 自绘图片主题（None=跟随全局 Config.PIC_THEME；用户可用「换深色/换浅色」临时改）
    pic_theme: str | None = None
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    def expired(self, ttl: int) -> bool:
        return (time.time() - self.updated_at) > ttl


class SessionStore:
    def __init__(self, ttl: int | None = None) -> None:
        self._ttl = ttl if ttl is not None else Config.SESSION_TTL
        self._data: dict[int, Session] = {}

    def get(self, user_id: int) -> Session:
        sess = self._data.get(user_id)
        if sess is None or sess.expired(self._ttl):
            if sess is not None:
                self._data.pop(user_id, None)
                log.info(jlog("session_expired", user_id=user_id))
            sess = Session(user_id=user_id)
            self._data[user_id] = sess
        return sess

    def set(self, user_id: int, patch: dict[str, Any]) -> Session:
        sess = self.get(user_id)
        for k, v in patch.items():
            if hasattr(sess, k):
                setattr(sess, k, v)
        sess.touch()
        return sess

    def clear(self, user_id: int) -> None:
        self._data.pop(user_id, None)

    def ttl_cleanup(self) -> int:
        """清理过期会话，返回清理数量。幂等，可重复调用（OPS-03）。"""
        ttl = self._ttl
        dead = [uid for uid, s in self._data.items() if s.expired(ttl)]
        for uid in dead:
            self._data.pop(uid, None)
        if dead:
            log.info(jlog("session_ttl_cleanup", cleaned=len(dead), remain=len(self._data)))
        return len(dead)

    def __len__(self) -> int:
        return len(self._data)


session_store = SessionStore()

# 生图通道交互式选择会话（§新增：反问「在线/本地」+ 超时）
from .choice import choice_sessions  # noqa: E402
