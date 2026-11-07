# -*- coding: utf-8 -*-
"""通道抽象层基类（docs/qq_official_bot_migration_plan.md §3.3 / §3.4）。

目的：把「协议相关」与「业务逻辑」彻底解耦，使 NapCat（OneBot V11）与
QQ 官方机器人两条通道可以**并存**，业务逻辑（意图路由 / 任务编排 / 队列 /
限流 / 配额 / 安全 / 自绘图 / 监控 / 历史）完全复用、零重复实现。

通道的契约只有两条：
  1. ``normalize(event) -> ctx``   把协议事件归一化为统一 ctx；
  2. ``send(ctx, Outgoing)``        把与协议无关的待发内容真正发出去。

铁律：
  * 通道内**不得**包含任何业务规则（是否受理、限流、扣额度等一律在业务层）；
  * 发送失败必须抛异常或明确返回失败原因（CLOSE-02 禁止静默吞异常）；
  * 通道不得 import 主项目任何模块（隔离红线）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Outgoing:
    """与协议无关的待发送消息。

    业务层只负责填这个结构，由具体通道自行翻译成对应协议的消息段。
    """

    text: str = ""
    image_urls: list[str] = field(default_factory=list)   # 图片 URL（主项目产出）
    images: list[bytes] = field(default_factory=list)     # 内存图片（自绘图/表情包）
    video: str = ""                                       # 视频 URL / 本地路径
    at_user: bool = False                                 # 是否 @发起人


class Channel(ABC):
    """通道基类。子类通过 ``channel.register()`` 注册到全局注册表。"""

    #: 通道名，写进 ctx["channel"]，用于日志/历史/队列区分
    name: str = ""

    # ---------- 事件归一化 ----------
    @abstractmethod
    async def normalize(
        self, event: Any, self_id: str = "", trace_id: str = ""
    ) -> dict | None:
        """把协议事件归一化为统一 ctx。返回 None 表示该事件应被丢弃。"""

    # ---------- 发送 ----------
    @abstractmethod
    async def send(self, ctx: dict, out: Outgoing) -> dict:
        """发送消息。返回 {"ok": bool, ...}（失败时 ok=False 并带 reason）。"""


def make_ctx(
    channel: str,
    *,
    user_key: str,
    group_key: str = "",
    user_id: int = 0,
    group_id: int = 0,
    self_id: str = "",
    is_group: bool = False,
    at_bot: bool = False,
    reply_ref: dict | None = None,
    trace_id: str = "",
) -> dict:
    """构造统一 ctx（契约 v2）。

    兼容说明：``user_id`` / ``group_id`` 对 NapCat 保持原语义（int 型 QQ 号/群号），
    官方通道填 0；业务层需要稳定唯一键时一律用 ``user_key`` / ``group_key``。
    """
    return {
        "channel": channel,
        "user_id": int(user_id or 0),
        "group_id": int(group_id or 0),
        "user_key": str(user_key),
        "group_key": str(group_key or ""),
        "self_id": str(self_id or ""),
        "is_group": bool(is_group),
        "at_bot": bool(at_bot),
        "reply_ref": dict(reply_ref or {}),
        "trace_id": str(trace_id or ""),
    }
