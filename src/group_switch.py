"""群聊总闸的单一事实来源（入站 + 出站）。

## 为什么要有这个模块

2026-10-29 的事故：`.env` 里设了 `GROUP_ENABLED=false`，入站方向确实被
`bot.py` 的 `event_preprocessor` 拦死了（群消息一条都进不来），
**但机器人还在往群里发消息** —— 13:53 / 14:33 / 15:26 三条主动广播，
来自 `xiuxian` 的 `_event_loop`（世界 Boss / 秘境 / 奇遇），
走的是 `bots[0].send_group_msg(...)`，完全不经过入站链路。

结论：**「关群聊」必须同时关两个方向。**
  · 入站：budget 在 `bot.py` 的 `event_preprocessor`（已实现）；
  · 出站：本模块的 `install_outbound_guard()`，挂在 `Bot.call_api` 上，
    任何走 OneBot API 的群操作都从这里过，不依赖各插件自己写 if。

把开关和出站判定放一个模块里，是为了让 bot.py（入口）和 xiuxian（插件）
用同一份逻辑，避免「各处各写一个 os.environ.get」然后慢慢漂移。

## 出站拦截的范围

只拦**发消息**类 API（send_group_msg / send_group_forward_msg / send_group_notice，
以及带 group_id 或 message_type=group 的 send_msg）。
踢人、禁言这类管理操作不在拦截范围——它们不是「往群里发消息」，
误拦会让私聊里的管理指令也失效。
"""
from __future__ import annotations

import os

_TRUE = ("1", "true", "yes", "on")

# 群消息类 API：一旦命中直接丢弃
GROUP_SEND_APIS = frozenset(
    {
        "send_group_msg",
        "send_group_forward_msg",
        "send_group_notice",
        "_send_group_notice",
    }
)


def group_enabled(raw: str | None = None) -> bool:
    """群聊是否开启。默认 True（群聊正常）。

    raw 只为便于单测传入；正常运行读环境变量。
    """
    if raw is None:
        raw = os.environ.get("GROUP_ENABLED")
    if raw is None:
        return True
    return str(raw).strip().lower() in _TRUE


def is_blocked_group_send(api: str, data: dict) -> bool:
    """这次 API 调用是否属于「往群里发消息」。"""
    if api in GROUP_SEND_APIS:
        return True
    if api == "send_msg":
        # send_msg 是通用发送：靠 message_type 或 group_id 判断是不是发到群
        if str(data.get("message_type") or "").lower() == "group":
            return True
        if data.get("group_id"):
            return True
    return False


def install_outbound_guard() -> bool:
    """给 OneBot V11 的 Bot.call_api 套一层出站闸。返回是否已安装。

    幂等：重复调用不会套两层。
    """
    from nonebot.adapters.onebot.v11 import Bot as V11Bot

    if getattr(V11Bot, "_qqbridge_outbound_guard", False):
        return True

    original = V11Bot.call_api

    async def call_api(self, api: str, **data):  # type: ignore[override]
        if is_blocked_group_send(api, data):
            # 静默丢弃并返回一个「成功但无实际消息」的结果：
            # 调用方大多只看有没有抛异常，抛异常会让循环崩掉/重试，
            # 反而制造更多发送尝试。
            print(
                "[qq_bridge] GROUP_ENABLED=false 已拦截出站群消息 api=%s group=%s"
                % (api, data.get("group_id") or data.get("message_type")),
                flush=True,
            )
            return {"message_id": 0, "blocked": True}

        return await original(self, api, **data)

    V11Bot.call_api = call_api  # type: ignore[method-assign]
    V11Bot._qqbridge_outbound_guard = True  # type: ignore[attr-defined]
    return True
