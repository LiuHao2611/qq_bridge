# -*- coding: utf-8 -*-
"""M8 告警推送链路测试（C1 目标路由 / C4 同类告警冷却去重）。

与 tests/test_smarthome.py 分工：那边只测 smarthome 包（纯函数、免 nonebot）；
这里测**推送出口接线**——`_notify_target_ctx` 与 `_ha_webhook_send` 定义在
主包 __init__.py，必须 import 主包（nonebot 已在 .venv 装好，import 不联网、
不起服务、不发消息）。

回归防线（2026-10-21 修的两个真 bug）：
  C1 修前：告警目标一律按**群**发（OPS_NOTIFY_TARGET 填私聊 QQ 号 → 发丢）；
  C4 修前：门磁/人体感应连发会刷屏（无冷却）；
  顺带修 M9：`_ops_notify_send` 一律按**私聊**发（负群号会被当负数 QQ 号发丢）。

运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_smarthome_alert.py -v
"""
from __future__ import annotations

import asyncio
import time

import pytest

import src.plugins.qq_bridge as bridge
from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.smarthome import HaEvent


def _ev(etype="motion", entity="sensor.living", msg="客厅有人") -> HaEvent:
    return HaEvent(event_type=etype, entity_id=entity, message=msg)


def _patch_send(monkeypatch, sent: list) -> None:
    """把 _reply 换成记录器（不真发消息）。"""

    async def fake_reply(ctx, sess, text, **kw):
        sent.append((ctx, text))

    monkeypatch.setattr(bridge, "_reply", fake_reply)


# ---------------- 1) 目标路由（C1） ----------------

@pytest.mark.parametrize("target,uid,gid", [
    ("123456", 123456, 0),      # 纯数字 = 私聊（QQ 号）
    ("-987654", 0, 987654),     # 负号前缀 = 群
    ("g987654", 0, 987654),     # g 前缀 = 群
])
def test_notify_target_ctx_forms(target, uid, gid):
    ctx = bridge._notify_target_ctx(target)
    assert ctx is not None
    assert (ctx["user_id"], ctx["group_id"]) == (uid, gid)


@pytest.mark.parametrize("target", ["", "   ", "不是号", "12a34"])
def test_notify_target_ctx_rejects_garbage(target):
    """解析不了 → None（调用方只记日志，绝不假装发出）。"""
    assert bridge._notify_target_ctx(target) is None


def test_ha_webhook_send_private_target(monkeypatch):
    """填私聊 QQ 号 → 必须发私聊（C1 回归：修前恒按群发，私聊收不到告警）。"""
    sent: list = []
    _patch_send(monkeypatch, sent)
    monkeypatch.setattr(Config, "OPS_NOTIFY_TARGET", "123456")
    monkeypatch.setattr(Config, "SMARTHOME_ALERT_COOLDOWN", 0.0)
    bridge._ha_alert_last.clear()

    asyncio.run(bridge._ha_webhook_send(_ev()))
    assert len(sent) == 1
    assert sent[0][0]["user_id"] == 123456 and sent[0][0]["group_id"] == 0


def test_ha_webhook_send_group_target(monkeypatch):
    sent: list = []
    _patch_send(monkeypatch, sent)
    monkeypatch.setattr(Config, "OPS_NOTIFY_TARGET", "-987654")
    monkeypatch.setattr(Config, "SMARTHOME_ALERT_COOLDOWN", 0.0)
    bridge._ha_alert_last.clear()

    asyncio.run(bridge._ha_webhook_send(_ev()))
    assert len(sent) == 1
    assert sent[0][0]["group_id"] == 987654 and sent[0][0]["user_id"] == 0


def test_ha_webhook_send_no_target_is_silent_but_logged(monkeypatch):
    """没配目标：不推送（也不报错）——hub 口径一致，只记日志。"""
    sent: list = []
    _patch_send(monkeypatch, sent)
    monkeypatch.setattr(Config, "OPS_NOTIFY_TARGET", "")
    asyncio.run(bridge._ha_webhook_send(_ev()))
    assert sent == []


# ---------------- 2) 同类告警冷却（C4） ----------------

def test_ha_webhook_send_cooldown(monkeypatch):
    sent: list = []
    _patch_send(monkeypatch, sent)
    monkeypatch.setattr(Config, "OPS_NOTIFY_TARGET", "123456")
    monkeypatch.setattr(Config, "SMARTHOME_ALERT_COOLDOWN", 60.0)
    bridge._ha_alert_last.clear()

    ev = _ev()
    asyncio.run(bridge._ha_webhook_send(ev))
    assert len(sent) == 1

    # 冷却内同设备同类型 → 压掉（合并计数，不刷屏）
    asyncio.run(bridge._ha_webhook_send(ev))
    assert len(sent) == 1

    # 不同设备不受影响（门磁和厨房人体感应不该互相压掉）
    asyncio.run(bridge._ha_webhook_send(_ev(entity="sensor.kitchen")))
    assert len(sent) == 2

    # 冷却过期 → 带「共 N 次」补发
    bridge._ha_alert_last["motion::sensor.living"] = (time.time() - 61.0, 3)
    asyncio.run(bridge._ha_webhook_send(ev))
    assert len(sent) == 3
    assert "共 3 次" in sent[2][1]


def test_ha_webhook_send_cooldown_disabled(monkeypatch):
    """冷却配 0 = 每条都推（用户明确要全量时不擅自节流）。"""
    sent: list = []
    _patch_send(monkeypatch, sent)
    monkeypatch.setattr(Config, "OPS_NOTIFY_TARGET", "123456")
    monkeypatch.setattr(Config, "SMARTHOME_ALERT_COOLDOWN", 0.0)
    bridge._ha_alert_last.clear()

    ev = _ev()
    asyncio.run(bridge._ha_webhook_send(ev))
    asyncio.run(bridge._ha_webhook_send(ev))
    assert len(sent) == 2
