# -*- coding: utf-8 -*-
"""生图通道交互式选择会话（设计文档 §新增）。

场景：用户发「生图 一本书」→ 机器人反问「在线还是本地」→ 用户回复「1/本地」或
「2/在线」→ 按所选通道提交。超时未选自动取消并清理（避免半开对话堆积，OPS-02）。

设计借鉴（2026-09-05 检索 CSDN / 官方文档「拿来用」）：
  * nonebot-plugin-waiter（RF-Tar-Railt）：提供 prompt() 等待用户输入、可配超时；
    本模块借鉴其「等待用户输入 + 超时即取消 + 会话作用域隔离」模型。
    https://github.com/RF-Tar-Railt/nonebot-plugin-waiter
  * NoneBot2 官方「会话控制」：每会话主体（用户）独立状态，互不串扰；
    本模块对每个 user_id 维护独立 ChoiceSession，避免群里多人互相覆盖。
    https://nonebot.dev/docs/appendices/session-control

与 waiter 的区别：waiter 基于 nonebot Matcher 的「阻塞式 prompt() 协程」，
本项目的入口是「每条消息独立 handler」的事件驱动模型，故自实现轻量状态机 +
asyncio.TimerHandle 超时，更贴合现有架构、易单测、不引入重依赖（符合 qq 与主项目
物理隔离的纪律）。核心思想（超时取消 / 作用域隔离）一致。

状态机：
  ask()        进入 pending（覆盖旧 pending 并取消其 timer）
  answer()     若用户处于 pending 且本条是选择词 → 返回 'local'/'online'，否则 None
  consume()    取出并取消 timer（回答命中或超时清理时调用）
  has()        是否处于 pending
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Config, get_logger, jlog

log = get_logger()

# 选择词 → 通道。数字 1/2 与中文/英文都支持，兼容 OneBot V11 无按钮的纯文字交互。
_LOCAL_WORDS = {"1", "本地", "本地生图", "本地的", "local", "本地通道"}
_ONLINE_WORDS = {"2", "在线", "云端", "云端的", "online", "在线通道", "云端生图"}


@dataclass
class ChoiceSession:
    user_id: int
    group_id: int
    prompt: str                       # 用户原始生图描述（剥离触发词后）
    created_at: float = field(default_factory=time.time)
    timer: Any | None = None          # asyncio.TimerHandle，超时取消用

    @property
    def expires_at(self) -> float:
        return self.created_at + Config.CHOICE_TIMEOUT

    def cancel_timer(self) -> None:
        if self.timer is not None:
            try:
                self.timer.cancel()
            except Exception:  # noqa: BLE001
                pass
            self.timer = None


def _parse_choice(text: str) -> str | None:
    """把用户回复解析为通道。纯数字/精确词优先，其次模糊包含。"""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in _LOCAL_WORDS:
        return "local"
    if t in _ONLINE_WORDS:
        return "online"
    # 模糊：含关键字即判定（容错用户多打几个字，如「我要本地的」）
    if "本地" in t or "local" in t:
        return "local"
    if "在线" in t or "云端" in t or "online" in t:
        return "online"
    return None


class ChoiceSessionStore:
    def __init__(self) -> None:
        self._data: dict[int, ChoiceSession] = {}

    def ask(self, user_id: int, group_id: int, prompt: str) -> ChoiceSession:
        """进入 pending；若已有旧 pending 先取消其 timer（新选择覆盖旧的）。"""
        old = self._data.get(user_id)
        if old is not None:
            old.cancel_timer()
        cs = ChoiceSession(user_id=user_id, group_id=group_id, prompt=prompt)
        self._data[user_id] = cs
        return cs

    def answer(self, user_id: int, text: str) -> str | None:
        """用户处于 pending 且本条是选择词 → 'local'/'online'；否则 None。"""
        cs = self._data.get(user_id)
        if cs is None:
            return None
        return _parse_choice(text)

    def consume(self, user_id: int) -> ChoiceSession | None:
        """取出并取消超时 timer（回答命中或超时清理时调用）。"""
        cs = self._data.pop(user_id, None)
        if cs is not None:
            cs.cancel_timer()
        return cs

    def has(self, user_id: int) -> bool:
        return user_id in self._data

    def __len__(self) -> int:
        return len(self._data)


choice_sessions = ChoiceSessionStore()
