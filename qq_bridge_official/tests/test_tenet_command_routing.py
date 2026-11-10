# -*- coding: utf-8 -*-
"""信条 / 终幕裸口令命令路由回归测试（2026-11-06）。

背景：用户发纯文本「信条」机器人没回。根因是「信条」在 NATURAL_CMDS 词表、
却不在 ZERO_ARG_OK 零参数白名单，导致 ``_match_natural("信条")`` 返回 ``None``、
``_handle`` 在裸口令分支静默 ``return``。修复后两者都登记，纯文本口令能路由到
``_send_tenet`` 出专属卡。

守的是用户看得见的行为：
  * 裸口令「信条」→ 走 ``tenet`` 单列模板出卡（不是静默无响应，也不是市价）；
  * 裸口令「终幕」→ 走 ``coda`` 两列模板出卡；
  * ZERO_ARG_OK 必须含「信条 / 终幕」，否则会被静默吞掉——这是本次 bug 的回归护栏。

不依赖网络：mock ``warframe.tenet_card_data``（喂门禁脚本的离线 fixture 真实结构）、
``pic.render``、``media.send_pic``。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge import game_tools as gt  # noqa: E402


def _load_data(kind: str) -> dict:
    """用门禁脚本的离线 fixture 拿真实完整 data 结构（不触网）。"""
    spec = importlib.util.spec_from_file_location(
        "gate_routing", os.path.join(ROOT, "tools", "check_tenet_layout.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return asyncio.run(m._load(kind, True))


_TENET_DATA = _load_data("tenet")
_CODA_DATA = _load_data("coda")


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send(self, event, msg=None, **kw):
        self.sent.append((msg, kw))
        return {"message_id": 1}


class _Seg:
    def __init__(self, t, data):
        self.type = t
        self.data = data


def _make_event(text: str) -> SimpleNamespace:
    segs = [_Seg("text", {"text": text})]
    return SimpleNamespace(
        message_type="group", group_id=123, user_id=456, channel="napcat",
        original_message=segs, message=segs,
        get_matcher=lambda: SimpleNamespace(stop_propagation=lambda: None),
    )


async def _run_handle(bot, text, data):
    ev = _make_event(text)
    with patch.object(gt.warframe, "tenet_card_data", new=AsyncMock(return_value=data)):
        with patch("src.plugins.qq_bridge.pic.render",
                   new=AsyncMock(return_value="tmp/_tenet_route_test.png")) as rnd:
            with patch("src.plugins.qq_bridge.media.send_pic", new=AsyncMock()) as sp:
                await gt._handle(bot, ev)
                tpl = rnd.call_args.args[0] if rnd.called else None
                return rnd.called, tpl, sp.called


def test_bare_tenet_routes_to_tenet_card():
    """裸口令「信条」必须出 tenet 卡（修复前静默无响应）。"""
    bot = _FakeBot()
    render_called, tpl, send_pic_called = asyncio.run(
        _run_handle(bot, "信条", _TENET_DATA))
    assert render_called, "裸口令「信条」没触发渲染（被静默吞掉）"
    assert tpl == "tenet", "裸口令「信条」应走 tenet 单列模板，实际 tpl=%r" % tpl
    assert send_pic_called, "裸口令「信条」应调用 send_pic 出图"
    assert bot.sent == [], "裸口令「信条」不应回纯文本（应出卡）"


def test_bare_coda_routes_to_coda_card():
    """裸口令「终幕」必须出 coda 卡（修复前连词表都没登记）。"""
    bot = _FakeBot()
    render_called, tpl, send_pic_called = asyncio.run(
        _run_handle(bot, "终幕", _CODA_DATA))
    assert render_called, "裸口令「终幕」没触发渲染"
    assert tpl == "coda", "裸口令「终幕」应走 coda 两列模板，实际 tpl=%r" % tpl
    assert send_pic_called


def test_zero_arg_allowlist_protects_bare_command(monkeypatch):
    """护栏：ZERO_ARG_OK 必须含「信条」，否则会被 _match_natural 静默返回 None。

    把「信条」从白名单临时移除应复现本次 bug（返回 None），恢复后必须返回 ("信条","")。
    """
    assert "信条" in gt.ZERO_ARG_OK and "终幕" in gt.ZERO_ARG_OK

    # 复现修复前：白名单不含信条 → 裸口令被静默丢弃
    slim = set(gt.ZERO_ARG_OK) - {"信条"}
    monkeypatch.setattr(gt, "ZERO_ARG_OK", slim)
    assert gt._match_natural("信条") is None, "白名单缺失时应复现静默吞掉 bug"

    # 恢复：白名单含信条 → 正常解析为零参数口令
    monkeypatch.setattr(gt, "ZERO_ARG_OK", set(slim) | {"信条"})
    assert gt._match_natural("信条") == ("信条", ""), "修复后裸口令「信条」应解析为零参数"


def test_bare_tenet_does_not_trigger_market_price():
    """裸口令「信条」（无参数）不应误走市价分支（市价仅带武器名时生效）。

    通过断言：render 的 tpl 是 tenet（专属卡），而非 guide/市价文本回流。
    """
    bot = _FakeBot()
    _, tpl, _ = asyncio.run(_run_handle(bot, "信条", _TENET_DATA))
    assert tpl == "tenet"
