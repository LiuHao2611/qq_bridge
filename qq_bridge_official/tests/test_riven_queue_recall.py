# -*- coding: utf-8 -*-
"""紫卡分析「排队占位消息」自动撤回机制回归测试。

覆盖：
  * 分析结果（成功或失败）发出后，排队占位消息被自动撤回（delete_msg）。
  * 按「群+用户」scope 归并，不跨会话误撤回。
  * 无待撤回消息时绝不调用撤回 API（避免无谓外呼）。
  * 撤回失败（如超 2 分钟时限）只记日志、不抛异常、不阻断出卡流程。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.plugins.qq_bridge.game_tools as gt


class _FakeEvent:
    def __init__(self, group_id=0, user_id=1):
        self.group_id = group_id
        self.user_id = user_id


def _fake_bot():
    bot = MagicMock()
    bot.call_api = AsyncMock()
    return bot


def test_scope_key_private():
    assert gt._riven_scope_key(_FakeEvent(group_id=0, user_id=7)) == "u7"


def test_scope_key_group():
    assert gt._riven_scope_key(_FakeEvent(group_id=123, user_id=7)) == "g123_u7"


def test_recall_deletes_pending_and_clears():
    ev = _FakeEvent(group_id=100, user_id=9)
    bot = _fake_bot()
    key = gt._riven_scope_key(ev)
    gt._riven_queue_ids.setdefault(key, []).append(111)
    gt._riven_queue_ids.setdefault(key, []).append(222)
    asyncio.run(gt._riven_recall_queue(bot, ev))
    calls = [(c.args[0], c.kwargs) for c in bot.call_api.call_args_list]
    assert ("delete_msg", {"message_id": 111}) in calls
    assert ("delete_msg", {"message_id": 222}) in calls
    # 撤回后该 scope 的待撤回列表应被清空
    assert gt._riven_queue_ids.get(key) in (None, [])


def test_recall_noop_when_empty():
    ev = _FakeEvent(group_id=100, user_id=9)
    bot = _fake_bot()
    asyncio.run(gt._riven_recall_queue(bot, ev))
    assert bot.call_api.call_args_list == []


def test_recall_isolation_between_scopes():
    a = _FakeEvent(group_id=1, user_id=1)
    b = _FakeEvent(group_id=2, user_id=2)
    ka, kb = gt._riven_scope_key(a), gt._riven_scope_key(b)
    gt._riven_queue_ids.setdefault(ka, []).append(10)
    gt._riven_queue_ids.setdefault(kb, []).append(20)
    bot = _fake_bot()
    asyncio.run(gt._riven_recall_queue(bot, a))  # 只撤回 a
    calls = [(c.args[0], c.kwargs) for c in bot.call_api.call_args_list]
    assert ("delete_msg", {"message_id": 10}) in calls
    assert ("delete_msg", {"message_id": 20}) not in calls
    # b 的待撤回仍在，不会被 a 的调用误清
    assert gt._riven_queue_ids.get(kb) == [20]


def test_recall_failure_is_swallowed():
    ev = _FakeEvent(group_id=5, user_id=5)
    key = gt._riven_scope_key(ev)
    gt._riven_queue_ids.setdefault(key, []).append(777)
    bot = _fake_bot()
    bot.call_api.side_effect = RuntimeError("撤回超时")
    # 不应抛异常
    asyncio.run(gt._riven_recall_queue(bot, ev))
    c = bot.call_api.call_args_list[0]
    assert c.args == ("delete_msg",)
    assert c.kwargs == {"message_id": 777}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
