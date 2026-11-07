# -*- coding: utf-8 -*-
"""handle_message 关键词门禁「画廊数字特例」单测（2026-09-11）。

背景：dispatcher 已对「画廊活跃 + 纯数字」放行（group_not_at 门禁），但 handle_message
内部还有第二道 KEYWORD_GATE：match_keyword("5") 返回 ("","")（数字不是关键词）→
走到 keyword_miss 静默 return，根本到不了 route() 的纯数字→__PICK__ 逻辑。
表现为"发了编号没反应"。

修复：handle_message 关键词门禁的 kw_kind=="" 分支里，若 gallery.active_count(uid)
且 raw_text 是 1~2 位纯数字，放行到路由层走 __PICK__（与 dispatcher 同口径）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

import src.plugins.qq_bridge as M  # noqa: E402
from src.plugins.qq_bridge import gallery  # noqa: E402
from src.plugins.qq_bridge.config import Config  # noqa: E402
from src.plugins.qq_bridge.dispatcher import QQEvent  # noqa: E402


def _Ev(uid, gid, raw, at_bot=True):
    """用真实 QQEvent 构造假事件（M4 起 handle_message 读取 is_group/voices/files
    等字段，手写假类易漏属性 → 直接用 dataclass，一劳永逸）。"""
    return QQEvent(user_id=uid, group_id=gid, message_type="group",
                   raw_text=raw, at_bot=at_bot)


def _patch_core(monkeypatch, uid, gid, raw):
    """把 handle_message 的关键依赖 mock 成「已放行到 handle_message」的状态。"""
    monkeypatch.setattr(Config, "KEYWORD_GATE", True)
    monkeypatch.setattr(M, "normalize",
                        lambda event, self_id="", trace_id="": _Ev(uid, gid, raw))
    monkeypatch.setattr(M, "allow", lambda ev: (True, ""))
    # 模拟 KEYWORD_GATE 未命中关键词（纯数字"5"本身不是关键词）
    monkeypatch.setattr(M, "match_keyword", lambda text: ("", ""))
    monkeypatch.setattr(M, "session_store",
                        type("S", (), {"get": staticmethod(lambda u: None),
                                       "ttl_cleanup": staticmethod(lambda: None)})())
    monkeypatch.setattr(M, "_ctx", lambda ev: {"user_id": ev.user_id, "group_id": ev.group_id})


def test_gallery_digit_bypasses_keyword_gate(monkeypatch):
    """画廊活跃 + 纯数字 → 放行到路由层（_execute_route_flow）走 PICK。"""
    uid, gid = 89010, 79010
    gallery.remember(uid, [{"task_id": "t1", "image_url": "/file?name=a.png",
                            "kind": "生图", "prompt": "p", "created_at": ""}])
    try:
        _patch_core(monkeypatch, uid, gid, "5")
        called = {}

        async def _spy(ev, sess, forced_channel=None, trace_id=""):
            called["yes"] = True

        monkeypatch.setattr(M, "_execute_route_flow", _spy)
        bot = type("B", (), {"self_id": "1"})()

        async def _run():
            await M.handle_message(bot, object())

        asyncio.run(_run())
        assert called.get("yes"), "画廊数字应绕过 keyword_miss 放行到路由层走 __PICK__"
    finally:
        gallery.reset()


def test_non_gallery_digit_still_keyword_miss(monkeypatch):
    """无画廊活跃 + 纯数字 → 仍被 keyword_miss 静默丢弃（不破口、不误当生图提示词）。"""
    uid, gid = 89011, 79011
    gallery.reset()
    _patch_core(monkeypatch, uid, gid, "5")
    called = {}

    async def _spy(ev, sess, forced_channel=None, trace_id=""):
        called["yes"] = True

    monkeypatch.setattr(M, "_execute_route_flow", _spy)
    bot = type("B", (), {"self_id": "1"})()

    async def _run():
        await M.handle_message(bot, object())

    asyncio.run(_run())
    assert not called.get("yes"), "无画廊时纯数字不应放行到路由层（保持 keyword_miss 静默）"
