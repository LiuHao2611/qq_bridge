# -*- coding: utf-8 -*-
"""dispatcher 群消息 pending choice 放行单测（§修复 2026-09-05）。

背景：群里回"1"/"本地"（无 @bot）被 group_not_at 门禁丢，
     体感"机器人突然发一张大帮助文字（HELP_TEXT 渲染成消息卡片，看着像表情包）"。
修：normalize 群消息 not at_bot 分支，若 choice_sessions.has(uid) → at_bot=True 放行。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.plugins.qq_bridge.dispatcher import normalize  # noqa: E402
from src.plugins.qq_bridge.session.choice import choice_sessions  # noqa: E402


class _Seg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


def _mk_event(uid, gid, text, with_at=False, self_id="900000009"):
    """构造一个最简 onebot MessageEvent（duck typing，normalize 用 getattr 读字段）。"""
    segs = []
    if with_at:
        segs.append(_Seg("at", {"qq": self_id}))
    segs.append(_Seg("text", {"text": text}))
    return type(
        "E",
        (),
        {
            "message_type": "group",
            "user_id": uid,
            "group_id": gid,
            "self_id": self_id,
            "original_message": segs,
            "message": segs,
            "raw_message": text,
        },
    )()


def test_no_pending_is_dropped():
    """无 pending 时群消息无 @ → 仍按原规则丢（保证群 @ 门禁不破口）。"""
    uid, gid = 88001, 77001
    choice_sessions.consume(uid)  # 清理
    ev = _mk_event(uid, gid, "1")
    r = normalize(ev, self_id="900000009", trace_id="t-no")
    assert r is None, f"无 pending 群消息无 @ 应被丢，实际={r}"


def test_pending_bypass_text():
    """有 pending 时群消息无 @ → 放行（at_bot=True），raw_text 正确。"""
    uid, gid = 88002, 77002
    choice_sessions.consume(uid)
    choice_sessions.ask(uid, gid, "一个背着书包的小女孩")
    try:
        ev = _mk_event(uid, gid, "1")
        r = normalize(ev, self_id="900000009", trace_id="t-yes")
        assert r is not None, "有 pending 群消息无 @ 应被放行"
        assert r.at_bot is True
        assert r.raw_text == "1"
        assert r.user_id == uid and r.group_id == gid
    finally:
        choice_sessions.consume(uid)


def test_pending_bypass_with_at_still_works():
    """有 pending + 真 @（at 段）→ 仍 at_bot=True（不破原行为）。"""
    uid, gid = 88003, 77003
    choice_sessions.consume(uid)
    choice_sessions.ask(uid, gid, "a cat")
    try:
        ev = _mk_event(uid, gid, "1", with_at=True)
        r = normalize(ev, self_id="900000009", trace_id="t-at")
        assert r is not None and r.at_bot is True and r.raw_text == "1"
    finally:
        choice_sessions.consume(uid)
