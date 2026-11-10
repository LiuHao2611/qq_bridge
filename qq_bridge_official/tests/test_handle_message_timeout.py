# -*- coding: utf-8 -*-
"""handle_message 60s 超时保护单测（用户要求 2026-09-11）。

验证：_timed_handle_message 包裹 handle_message，当内部处理超过 REPLY_TIMEOUT 仍
未完成时，捕获 asyncio.TimeoutError 并尽力给用户一条「超时保护」提示，事件循环
不再被卡死（NapCat 抖动 / 模型卡住都能兜住）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

import src.plugins.qq_bridge as M  # noqa: E402
from src.plugins.qq_bridge.config import Config  # noqa: E402


class _Seg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


def test_timed_handle_message_timeout_protection(monkeypatch):
    """内部 handle_message 卡住 → 超时后捕获并回「超时」提示。"""
    # 1) 让真正的 handle_message 卡住（超过 REPLY_TIMEOUT）
    async def _slow(bot, event):
        await asyncio.sleep(10)

    monkeypatch.setattr(M, "handle_message", _slow)

    # 2) 缩短超时，避免测试真等 60s
    monkeypatch.setattr(Config, "REPLY_TIMEOUT", 0.3)

    # 3) 超时分支里 normalize 返回有效 ev（private，免 @ 门禁），让 _reply 能发出
    class _Ev:
        user_id = 1
        group_id = 0
        message_type = "private"

    monkeypatch.setattr(M, "normalize", lambda event, self_id="", trace_id="": _Ev())

    # 4) 捕获 _reply 发出的文本
    captured = {}

    async def _fake_reply(ctx, sess, text, title=None, **kwargs):
        captured["text"] = text
        captured["title"] = title

    monkeypatch.setattr(M, "_reply", _fake_reply)

    bot = type("B", (), {"self_id": "1"})()

    async def _run():
        await M._timed_handle_message(bot, object())

    asyncio.run(_run())

    assert "超时" in captured.get("text", ""), "超时后必须给用户兜底提示，实际=%r" % captured
    assert captured.get("title") == "超时"


def test_timed_handle_message_normal_path_no_timeout(monkeypatch):
    """内部正常返回 → 不触发超时提示（冒烟，确认不误伤正常链路）。"""
    async def _ok(bot, event):
        return None

    monkeypatch.setattr(M, "handle_message", _ok)
    monkeypatch.setattr(Config, "REPLY_TIMEOUT", 0.3)

    captured = {}

    async def _fake_reply(ctx, sess, text, title=None, **kwargs):
        captured["text"] = text

    monkeypatch.setattr(M, "_reply", _fake_reply)

    bot = type("B", (), {"self_id": "1"})()

    async def _run():
        await M._timed_handle_message(bot, object())

    asyncio.run(_run())

    assert "text" not in captured, "正常路径不应发超时提示"
