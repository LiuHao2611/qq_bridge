# -*- coding: utf-8 -*-
"""生图通道交互式选择状态机单测（§新增：反问「在线/本地」+ 超时回收）。

验证 session/choice.py 的状态机逻辑（ask/answer/consume/has + 选择词解析）。
超时 timer 由入口用 asyncio 注册，此处以 timer=None / 无 cancel 方法的对象模拟，
断言 consume 取消 timer 时容错不抛（OPS 稳健性）。

设计借鉴（检索 CSDN/官方「拿来用」）：
  - nonebot-plugin-waiter：等待用户输入 + 超时即取消
  - NoneBot2 官方 session-control：每用户独立会话作用域
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.plugins.qq_bridge.session.choice import (  # noqa: E402
    ChoiceSessionStore,
    _parse_choice,
    choice_sessions,
)


def test_parse_choice_local():
    assert _parse_choice("1") == "local"
    assert _parse_choice("本地") == "local"
    assert _parse_choice("本地生图") == "local"
    assert _parse_choice("local") == "local"
    assert _parse_choice("我要本地的") == "local"  # 模糊包含容错


def test_parse_choice_online():
    assert _parse_choice("2") == "online"
    assert _parse_choice("在线") == "online"
    assert _parse_choice("云端") == "online"
    assert _parse_choice("online") == "online"
    assert _parse_choice("云端的") == "online"


def test_parse_choice_none():
    assert _parse_choice("") is None
    assert _parse_choice("随便聊聊") is None
    assert _parse_choice("生图 一本书") is None  # 非选择词，应走正常生图反问流


def test_ask_answer_consume():
    s = ChoiceSessionStore()
    assert not s.has(1001)
    cs = s.ask(1001, 2001, "a cat")
    assert s.has(1001)
    assert cs.prompt == "a cat"
    # 回答命中
    assert s.answer(1001, "本地") == "local"
    # 取出并取消 timer（timer=None 不报错）
    got = s.consume(1001)
    assert got is not None and got.prompt == "a cat"
    assert not s.has(1001)
    # 取出后 answer 返回 None
    assert s.answer(1001, "在线") is None


def test_answer_without_pending_is_none():
    s = ChoiceSessionStore()
    assert s.answer(9999, "1") is None  # 无 pending


def test_ask_overwrites_old_pending_and_cancels_timer():
    s = ChoiceSessionStore()
    cs1 = s.ask(2002, 1, "first")
    cs1.timer = object()  # 无 cancel 方法，验证容错
    cs2 = s.ask(2002, 1, "second")
    assert cs2.prompt == "second"  # 新选择覆盖旧的
    assert s.has(2002)
    got = s.consume(2002)  # 取消旧 timer（cs1.timer 无 cancel 方法，应容错）
    assert got.prompt == "second"


def test_global_store_singleton_smoke():
    assert isinstance(choice_sessions, ChoiceSessionStore)
    choice_sessions.consume(12345)  # 清理（无则返回 None，不抛）
