# -*- coding: utf-8 -*-
"""QQ 官方「被动回复 msg_seq」跨 send 分配器（2026-11-11 修 40054005）。

## 真实生产事故（用户「看看日志，紫卡分析没出结果」）

紫卡分析图**已成功渲染**（日志 ``pic_render template=riven_analysis bytes=176090``、
``gt_render_done kind=riven_analysis``），但发送被平台拒：

    channel_send_failed ... code=40054005, message=消息被去重，请检查请求msgseq

## 根因（nonebot-adapter-qq 1.7.2 源码 + 生产日志双证）

1. 适配器 ``Bot.send`` 对群/C2C 事件维护 ``event._reply_seq``（``event.py:373``
   初始 0），**每次 send 先 ``_reply_seq += 1`` 再透传 msg_seq**（``bot.py:691/706``）。
   即：同一条入站消息（同一 ``msg_id``）下，第 N 次被动回复的 ``msg_seq``
   必须严格等于 N（1、2、3…）；**重复 seq 会被平台按去重拒收 40054005**。
2. 我们的 ``OfficialChannel.send()`` 为发图片/按钮/合并段，**绕过 ``Bot.send``
   直调 ``send_to_group(msg_seq=...)``**，适配器不会替我们自增。旧实现用
   ``msg_seq=(i + 1)``，``i`` 只是**单次 send 内** segments 的下标 → 每次 send
   都从 1 起。
3. 紫卡链路在一次事件里 **send 两次**：
   * 排队话术 ``bot.send(event, "已加入分析队列…")`` → ``OfficialLikeBot.send``
     → ``OfficialChannel.send`` → 一段 text，seq=1；
   * 结果图 ``send_pic(ctx, png)`` → ``media._send_via_channel`` → ``OfficialChannel.send``
     → 一段 image，seq 又=1。
   同一 ``msg_id`` 下 seq 重复 → 第二条形被去重 → 用户侧「没出结果」。

## 修法：按 reply_ref 跨 send 递增（本模块）

``normalize()`` 初始化 ``reply_ref={"msg_id":…, "msg_seq":1, "ts":…}``——这个
``msg_seq`` 字段本就是**预留但未接上**的跨 send 计数器。一次事件内所有 send
共享**同一个** ``reply_ref`` dict（``send()`` 里 ``merged = dict(full)`` 是浅拷贝；
``send_pic`` 的 3 字段 ctx 无 reply_ref，``merged.update(ctx)`` 不覆盖它），因此
在此 dict 上原子递增即可让跨 send 的 seq 严格 1、2、3…，与适配器
``event._reply_seq`` 语义完全对齐。不同事件 reply_ref 是不同 dict，天然并发隔离
（ContextVar 每事件独立 task）。

## 契约（供 test_qq_msg_seq.py 独立基准 + 注入验证）

* ``next_msg_seq(ref)``：返回本次 send 应带的 seq（首次 1），并把 ref 内计数 +1；
  超过 ``max_seq``（平台硬限每条消息最多 5 次被动回复）返回 ``None``，调用方必须
  转主动/跳过，**绝不能再发重复 seq**。
* 缺字段 / 非 dict / 非法值一律安全降级为「从 1 起」，不抛异常拖垮发送。
* 顶层**零依赖**（不 import nonebot / httpx / warframe）：
  宿主纯标准库可直载真跑，容器真 nonebot 亦可复用。
"""
from __future__ import annotations

#: 平台硬限：群聊每条消息最多被动回复 5 次（official.py 头部契约）。
#: 与适配器 event._reply_seq 的可用区间一致（1..5）。
MAX_PASSIVE_SEQ = 5


def _coerce(value) -> int:
    """把 reply_ref["msg_seq"] 安全转成 int；非法/缺失回退到 1（首条）。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def next_msg_seq(reply_ref, *, max_seq: int = MAX_PASSIVE_SEQ):
    """分配本次被动回复的 ``msg_seq``，并把 reply_ref 内计数原子 +1。

    :param reply_ref: 一次事件内共享的 ``ctx["reply_ref"]`` dict。
    :param max_seq: 平台每条消息被动回复次数上限（默认 5）。
    :return: 本次应透传给 ``send_to_group/send_to_c2c(msg_seq=...)`` 的正整数；
             计数已超上限时返回 ``None``（调用方必须转主动或跳过，不得重复发 seq）。

    语义对齐适配器 ``Bot.send`` 的 ``event._reply_seq += 1``：调用一次消耗一个
    序号，同一 reply_ref 上连续调用产出 1、2、3…，跨多次 ``OfficialChannel.send``
    调用亦然（因为共享同一 dict）。
    """
    if not isinstance(reply_ref, dict):
        # 无凭据场景：给 1 也不影响（调用方本就 passive_ok=False 不会透传），
        # 但返回 None 更明确地让调用方转主动，杜绝任何重复 seq=1 的可能。
        return None
    try:
        cur = _coerce(reply_ref.get("msg_seq", 1))
        if cur > max_seq:
            return None
        reply_ref["msg_seq"] = cur + 1
        return cur
    except Exception:  # noqa: BLE001 — reply_ref 异常绝不能拖垮发送主链路
        return None


def peek_msg_seq(reply_ref) -> int:
    """只读当前待用 seq（不推进计数），供日志/守门诊断。"""
    if not isinstance(reply_ref, dict):
        return 1
    return _coerce(reply_ref.get("msg_seq", 1))
