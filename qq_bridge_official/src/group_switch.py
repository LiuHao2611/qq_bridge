"""群聊治理的单一事实来源：入站总闸 / 出站总闸 / 出站单群白名单。

## 为什么要有这个模块

2026-10-29 的事故：`.env` 里设了 `GROUP_ENABLED=false`，入站方向确实被
`bot.py` 的 `event_preprocessor` 拦死了（群消息一条都进不来），
**但机器人还在往群里发消息** —— 13:53 / 14:33 / 15:26 三条主动广播，
来自 `xiuxian` 的 `_event_loop`（世界 Boss / 秘境 / 奇遇），
走的是 `bots[0].send_group_msg(...)`，完全不经过入站链路。

结论：**「关群聊」必须同时关两个方向。**
  · 入站：总闸在 `bot.py` 的 `event_preprocessor`（已实现）；
  · 出站：本模块的 `install_outbound_guard()`，挂在 `Bot.call_api` 上，
    任何走 OneBot API 的群发送都从这里过，不依赖各插件自己写 if。

2026-11-10 的第二起事故：`GROUP_WHITELIST` 只作用于 dispatcher（priority=10），
而 doupo / xiuxian 是 priority=5 + block=True，抢在 dispatcher 之前处理群消息。
日志实证：17:49:07 非白名单群 1042928787 的 `dj` 口令 → 17:49:11
`media_sent action=send_group_msg msg_len=288795`（一张 288KB 图）发了出去。
因此新增**常驻出站白名单闸**：`GROUP_ENABLED=true` 时同样生效，
只允许往 `.env::GROUP_WHITELIST` 里的群发消息，文字/图片/转发/公告一视同仁。

把开关和出站判定放一个模块里，是为了让 bot.py（入口）和 xiuxian（插件）
用同一份逻辑，避免「各处各写一个 os.environ.get」然后慢慢漂移。

## 出站拦截的范围

只拦**发消息**类 API（send_group_msg / send_group_forward_msg / send_group_notice，
以及带 group_id 或 message_type=group 的 send_msg）。
踢人、禁言、拉群列表这类管理操作不在拦截范围——它们不是「往群里发消息」，
误拦会让私聊里的管理指令也失效。

## 安全默认值（fail-closed）

`GROUP_WHITELIST` 为空 / 缺失 / 无法解析 → 一个群都不许发。
群发送拿不到明确 group_id（例如 send_msg 只带 message_type=group）→ 同样拦。
宁可少发一条，也不能把消息发到没被允许的群。
"""
from __future__ import annotations

import logging
import os

_TRUE = ("1", "true", "yes", "on")

# 群消息类 API：命中后必须过白名单判定
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


def group_send_whitelist(raw: str | None = None) -> set[int]:
    """允许**出站发消息**的群集合，唯一来源 = `.env::GROUP_WHITELIST`。

    与 `config.Config.GROUP_WHITELIST`（入站监听群）同源同语义，
    每次调用都重读环境变量，避免进程内缓存与配置漂移。
    空集合 = 一个群都不许发（fail-closed）。
    """
    if raw is None:
        raw = os.environ.get("GROUP_WHITELIST")
    out: set[int] = set()
    for part in str(raw or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def group_send_target(api: str, data: dict) -> int | None:
    """取这次调用的目标群号；无法确认时返回 None（调用方按 fail-closed 处理）。"""
    gid = data.get("group_id")
    if gid is None or gid == "":
        return None
    try:
        return int(gid)
    except (TypeError, ValueError):
        return None


def is_blocked_group_send(api: str, data: dict) -> bool:
    """这次 API 调用是否属于「往群里发消息」（受本闸管辖）。"""
    if api in GROUP_SEND_APIS:
        return True
    if api == "send_msg":
        # send_msg 是通用发送：靠 message_type 或 group_id 判断是不是发到群
        if str(data.get("message_type") or "").lower() == "group":
            return True
        if data.get("group_id"):
            return True
    return False


def is_group_send_allowed(api: str, data: dict, raw_whitelist: str | None = None) -> bool:
    """这次调用是否允许真正发出去（只看白名单，不看 GROUP_ENABLED）。

    · 非群发送        → True（私聊 / 管理操作不受本闸管辖）
    · 群发送但无群号  → False（无法证明目标群被允许）
    · 群号在白名单    → True
    """
    if not is_blocked_group_send(api, data):
        return True
    gid = group_send_target(api, data)
    if gid is None:
        return False
    return gid in group_send_whitelist(raw_whitelist)


def blocked_reason(api: str, data: dict) -> str:
    """返回拦截原因；空串表示放行。优先级：总闸 > 白名单。"""
    if not is_blocked_group_send(api, data):
        return ""
    if not group_enabled():
        return "group_disabled"
    if not is_group_send_allowed(api, data):
        return "group_not_whitelisted"
    return ""


def _log_blocked(api: str, data: dict, reason: str) -> None:
    """拦截必须留痕：容器 stdout 与 qq_bridge.log 各一条（CLOSE-02 禁止静默）。"""
    gid = group_send_target(api, data)
    allowed = sorted(group_send_whitelist())
    print(
        "[qq_bridge] 出站群消息已拦截 reason=%s api=%s group_id=%s allowed=%s"
        % (reason, api, gid, ",".join(str(g) for g in allowed) or "(none)"),
        flush=True,
    )
    try:
        logging.getLogger("qq_bridge").warning(
            "event=group_send_blocked reason=%s api=%s group_id=%s allowed_count=%d",
            reason, api, gid, len(allowed),
        )
    except Exception as e:  # noqa: BLE001 — 日志失败也要显式留痕，不静默吞
        print(
            "[qq_bridge] group_send_blocked 日志写入失败 %s: %s" % (type(e).__name__, e),
            flush=True,
        )


def install_outbound_guard() -> bool:
    """给 OneBot V11 的 Bot.call_api 套一层常驻出站闸。返回是否已安装。

    ⚠️ 必须**无条件**安装（bot.py 在 GROUP_ENABLED=true 时也要调）。
    只在关群聊时装闸，等于群聊开启时所有非白名单群发送全部放行 ——
    正是 2026-11-10 那次乱发群消息的直接原因。

    幂等：重复调用不会套两层。
    """
    from nonebot.adapters.onebot.v11 import Bot as V11Bot

    if getattr(V11Bot, "_qqbridge_outbound_guard", False):
        return True

    original = V11Bot.call_api

    async def call_api(self, api: str, **data):  # type: ignore[override]
        reason = blocked_reason(api, data)
        if reason:
            # 静默丢弃并返回一个「成功但无实际消息」的结果：
            # 调用方大多只看有没有抛异常，抛异常会让循环崩掉/重试，
            # 反而制造更多发送尝试。返回值带 blocked/reason 供上层显式识别。
            _log_blocked(api, data, reason)
            return {"message_id": 0, "blocked": True, "reason": reason}

        return await original(self, api, **data)

    V11Bot.call_api = call_api  # type: ignore[method-assign]
    V11Bot._qqbridge_outbound_guard = True  # type: ignore[attr-defined]
    return True
