"""机器验收：群聊关闭时，机器人不会往群里发任何消息。

2026-10-29 事故复盘：`GROUP_ENABLED=false` 只拦了入站（群消息进不来），
但 xiuxian 的世界事件循环直接调 `bots[0].send_group_msg(...)`，
13:53 / 14:33 / 15:26 照样往群里广播。所以「关群聊」必须验两个方向。

八项：
  A 组·判定逻辑（纯函数，直接调）
    1. send_group_msg            → 拦
    2. send_group_forward_msg    → 拦
    3. send_msg + group_id       → 拦
    4. send_msg + message_type=group → 拦
    5. send_private_msg          → 放行
    6. send_msg + user_id        → 放行
  B 组·出站闸真的挂上了
    7. 装上闸后，群发送不触达底层 API；私聊照常透传
  C 组·源头
    8. xiuxian._broadcast 在发送之前就有 group_enabled() 早退；
       _startup 里 _event_loop 受 group_enabled() 保护

用法：.venv\\Scripts\\python.exe scripts\\verify_no_group_push.py
退出码：0 = 全 PASS；1 = 有 FAIL。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.group_switch import (  # noqa: E402
    group_enabled,
    install_outbound_guard,
    is_blocked_group_send,
)


def _check_xiuxian_source() -> tuple[bool, bool]:
    p = os.path.join(ROOT, "src", "plugins", "xiuxian", "__init__.py")
    src = open(p, encoding="utf-8").read()

    # 1) _broadcast 里，group_enabled() 必须出现在 send_group_msg 之前
    i_fn = src.index("async def _broadcast(")
    i_send = src.index("send_group_msg", i_fn)
    seg = src[i_fn:i_send]
    guard_before_send = "group_enabled()" in seg and "return" in seg

    # 2) _startup 里 _event_loop 必须被 group_enabled() 包住
    i_start = src.index("async def _startup(")
    i_loop = src.index("_event_loop()", i_start)
    startup_seg = src[i_start:i_loop]
    loop_guarded = "group_enabled()" in startup_seg

    return guard_before_send, loop_guarded


async def _check_wrapper() -> tuple[bool, bool]:
    from nonebot.adapters.onebot.v11 import Bot as V11Bot

    hits: list[str] = []

    async def fake_call_api(self, api: str, **data):
        hits.append(api)
        return {"message_id": 999}

    # 先换成探针，模拟真实 call_api；再装闸（装闸会包住当前实现）
    V11Bot.call_api = fake_call_api
    V11Bot._qqbridge_outbound_guard = False
    install_outbound_guard()

    fake_self = object()
    # 假号：必须用不可能进白名单的 9xxxxxxx（AGENTS.md §51），禁止真实 QQ / 群号。
    r1 = await V11Bot.call_api(fake_self, "send_group_msg", group_id=900000001, message="hi")
    group_blocked = r1.get("blocked") is True and "send_group_msg" not in hits

    r2 = await V11Bot.call_api(fake_self, "send_private_msg", user_id=900000002, message="hi")
    private_passthrough = r2.get("message_id") == 999 and "send_private_msg" in hits

    return group_blocked, private_passthrough


def main() -> int:
    logic = [
        ("send_group_msg", "send_group_msg", {"group_id": 1}, True),
        ("send_group_forward_msg", "send_group_forward_msg", {"group_id": 1}, True),
        ("send_msg + group_id", "send_msg", {"group_id": 1}, True),
        ("send_msg + message_type=group", "send_msg", {"message_type": "group"}, True),
        ("send_private_msg", "send_private_msg", {"user_id": 1}, False),
        ("send_msg + user_id", "send_msg", {"user_id": 1}, False),
    ]

    rows = []
    for label, api, data, expect in logic:
        rows.append(("判定 " + label, is_blocked_group_send(api, data), expect))

    group_blocked, private_ok = asyncio.run(_check_wrapper())
    rows.append(("出站闸拦住群发送", group_blocked, True))
    rows.append(("出站闸放行私聊", private_ok, True))

    guard_before_send, loop_guarded = _check_xiuxian_source()
    rows.append(("xiuxian._broadcast 发送前有闸", guard_before_send, True))
    rows.append(("xiuxian 世界事件循环受闸控制", loop_guarded, True))

    # 单测语义：这脚本默认在「群关闭」语境下跑，所以开关本身也要对
    print("=" * 70)
    print("  当前 GROUP_ENABLED=%r → group_enabled()=%r"
          % (os.environ.get("GROUP_ENABLED"), group_enabled()))
    fail = 0
    for label, actual, expect in rows:
        ok = actual == expect
        fail += 0 if ok else 1
        print("  %-5s %-34s actual=%-6r expect=%r"
              % ("PASS" if ok else "FAIL", label, actual, expect))
    print("=" * 70)
    print("%d PASS / %d FAIL" % (len(rows) - fail, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
