# -*- coding: utf-8 -*-
"""dispatcher 画廊上下文「回复纯数字编号选原图」免 @ 放行单测（2026-09-11）。

背景：用户在画廊（九宫格）里回「3」想看第 3 张原图，但群消息若没 @机器人，
会被 group_not_at 门禁静默丢弃 → 体感「发了编号没反应」。
修：normalize 群消息 not at_bot 分支，若 gallery.active_count(uid) 且消息是 1~2 位纯数字
→ at_bot=True 放行（与 pending choice 同理）。非数字闲聊仍按原门禁丢，避免破口。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.plugins.qq_bridge.dispatcher import normalize  # noqa: E402
from src.plugins.qq_bridge import gallery  # noqa: E402


class _Seg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


def _mk_event(uid, gid, text, with_at=False, self_id="900000009"):
    """构造最简 onebot MessageEvent（duck typing，normalize 用 getattr 读字段）。"""
    segs = []
    if with_at:
        segs.append(_Seg("at", {"qq": self_id}))
    segs.append(_Seg("text", {"text": text}))
    return type(
        "E", (),
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


def _activate(uid, n=1):
    """造一个活跃画廊（够 active_count>0 即可）。"""
    items = [{"task_id": "t%d" % i, "image_url": "/file?name=a%d.png" % i,
              "kind": "生图", "prompt": "p", "created_at": ""} for i in range(n)]
    gallery.remember(uid, items)


def test_gallery_pick_bypasses_at_gate():
    """画廊上下文 + 群消息无 @ + 纯数字 → 放行，raw_text 正确。"""
    uid, gid = 89001, 79001
    _activate(uid)
    try:
        ev = _mk_event(uid, gid, "3")
        r = normalize(ev, self_id="900000009", trace_id="t-g")
        assert r is not None, "画廊上下文里回纯数字应免@放行"
        assert r.at_bot is True
        assert r.raw_text == "3"
    finally:
        gallery.reset()


def test_gallery_active_but_non_numeric_still_dropped():
    """画廊上下文里非数字闲聊（无 @）仍按门禁丢，避免误触发。"""
    uid, gid = 89002, 79002
    _activate(uid)
    try:
        ev = _mk_event(uid, gid, "你好喵")
        r = normalize(ev, self_id="900000009", trace_id="t-g2")
        assert r is None, "画廊上下文里非数字闲聊仍应被@门禁丢"
    finally:
        gallery.reset()


def test_gallery_pick_out_of_range_digits_dropped():
    """3 位以上数字（非编号）不触发画廊放行，仍丢。"""
    uid, gid = 89003, 79003
    _activate(uid)
    try:
        ev = _mk_event(uid, gid, "12345")
        r = normalize(ev, self_id="900000009", trace_id="t-g3")
        assert r is None, "非编号的长数字不应走画廊放行"
    finally:
        gallery.reset()


def test_no_gallery_pure_digit_still_dropped():
    """无活跃画廊 + 纯数字无 @ → 仍按原门禁丢（保证不破口）。"""
    uid, gid = 89004, 79004
    gallery.reset()
    ev = _mk_event(uid, gid, "3")
    r = normalize(ev, self_id="900000009", trace_id="t-g4")
    assert r is None, "无画廊时纯数字无@应被丢"


# ------------------------- 翻页口令免 @（2026-09-13 线上事故，用户号已脱敏）-------
# 事故现场日志：event=dispatcher_drop reason=group_not_at raw_message=下一页
# 用户在画廊里发「下一页」没 @机器人 → 被 group_not_at 静默丢弃 → 体感"发了没反应"。
# 修：与「回复编号」同理，画廊上下文内的**整句翻页口令**一并免 @ 放行。
def test_gallery_nav_bypasses_at_gate():
    """画廊上下文 + 无 @ + 翻页口令 → 放行（下一页/上一页/翻页/下页）。"""
    uid, gid = 89005, 79005
    _activate(uid)
    try:
        for w in ("下一页", "上一页", "翻页", "下页", "next", "上一页。"):
            ev = _mk_event(uid, gid, w)
            r = normalize(ev, self_id="900000009", trace_id="t-nav-%s" % w)
            assert r is not None, "画廊上下文里发「%s」应免@放行（事故现场词）" % w
            assert r.at_bot is True
            assert r.raw_text == w
    finally:
        gallery.reset()


def test_gallery_nav_without_gallery_still_dropped():
    """无活跃画廊时「下一页」无 @ → 仍丢（放行口子只对画廊上下文开）。"""
    uid, gid = 89006, 79006
    gallery.reset()
    ev = _mk_event(uid, gid, "下一页")
    r = normalize(ev, self_id="900000009", trace_id="t-nav-x")
    assert r is None, "没有活跃画廊时翻页口令无@应被丢"


def test_gallery_context_similar_chatter_still_dropped():
    """画廊上下文里的相似闲聊（非整句口令）仍丢 —— 证明免@没被放大成破口。"""
    uid, gid = 89007, 79007
    _activate(uid)
    try:
        for w in ("下面给你看看", "下一页要画猫", "再下一页吧", "这页不错"):
            ev = _mk_event(uid, gid, w)
            r = normalize(ev, self_id="900000009", trace_id="t-nav-y")
            assert r is None, "「%s」不该被翻页免@放行" % w
    finally:
        gallery.reset()
