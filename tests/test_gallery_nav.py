# -*- coding: utf-8 -*-
"""画廊翻页口令「下一页」全链路单测（2026-09-13 线上事故修复）。

事故：用户在群里看完画廊（提示写着「翻页说「历史 2」或「下一页」」）后发「下一页」，
机器人毫无反应。日志实证（logs/qq_bridge.log）：

    event=dispatcher_drop reason=group_not_at user_id=3287416492 raw_message=下一页

根因是**三道门都没给翻页口令开门**，缺一道都仍然不通：

  1. ``dispatcher``：群消息无 @ → ``group_not_at`` 直接丢；它只给「1~2 位纯数字」
     开了画廊免 @ 口子（2026-09-11），翻页词没份；
  2. ``KEYWORD_GATE``：``match_keyword("下一页")`` 返回空 → ``keyword_miss`` 静默丢；
  3. ``intent.MAID_ALIASES``：根本没有「下一页」暗号 → 到不了 ``__GALLERY__``；
     即便到了，``gallery.NAV_*`` 也只写在 ``_cmd_gallery`` 的正则里
     （``下一|上[一页]|翻[下上]一?页?``），「下页」还会漏判。

修复：口令词表收敛成 ``gallery.NAV_WORDS`` / ``gallery.nav_delta`` **一份**，
dispatcher 免 @、intent 暗号、``_cmd_gallery`` 增量三处统一引用。

本文件覆盖第 2、3 道门 + ``_cmd_gallery`` 页码语义；
第 1 道门见 ``test_dispatcher_gallery_bypass.py::test_gallery_nav_*``。
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.plugins.qq_bridge as M  # noqa: E402
from src.plugins.qq_bridge import gallery  # noqa: E402
from src.plugins.qq_bridge.dispatcher import normalize  # noqa: E402
from src.plugins.qq_bridge.intent import match_keyword, route as route_intent  # noqa: E402
from src.plugins.qq_bridge.session import Session  # noqa: E402

SELF_ID = "3858741376"


# ---------------------------------------------------------------- helpers
class _Seg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


def _mk_event(uid, gid, text, with_at=True):
    segs = []
    if with_at:
        segs.append(_Seg("at", {"qq": SELF_ID}))
    segs.append(_Seg("text", {"text": text}))
    return type(
        "E", (),
        {
            "message_type": "group",
            "user_id": uid,
            "group_id": gid,
            "self_id": SELF_ID,
            "original_message": segs,
            "message": segs,
            "raw_message": text,
        },
    )()


def _norm(uid, text, gid=79099, with_at=True):
    """走真实 dispatcher 标准化（@ 机器人版），拿到 QQEvent。"""
    ev = normalize(_mk_event(uid, gid, text, with_at=with_at),
                   self_id=SELF_ID, trace_id="t-nav")
    assert ev is not None, "「%s」应通过 dispatcher（带了 @）" % text
    return ev


def _items(n=9):
    return [{"task_id": "t%d" % i, "image_url": "/file?name=a%d.png" % i,
             "kind": "生图", "prompt": "p%d" % i, "created_at": ""} for i in range(n)]


# ------------------------------------------------- 第 2 道门：KEYWORD_GATE
@pytest.mark.parametrize("word", ["下一页", "上一页", "翻页", "下页", "上页", "next", "prev"])
def test_match_keyword_passes_nav(word):
    """翻页口令必须判为 cmd —— 否则 KEYWORD_GATE 会走 keyword_miss 静默丢弃。"""
    kind, payload = match_keyword(word)
    assert kind == "cmd", "「%s」被关键词门禁丢了（事故现场）" % word
    assert payload == word


def test_match_keyword_still_drops_gibberish():
    """对照：非口令的群里闲聊仍判空（保证放行口子没被放大）。"""
    for word in ("下面给你看看", "下一页要画猫", "再下一页吧", "页"):
        kind, _ = match_keyword(word)
        assert kind == "", "「%s」不应被当成翻页口令放行" % word


# ------------------------------------------------------ 第 3 道门：路由
def test_route_next_page_keeps_nav_word_as_arg():
    """「下一页」→ ``__GALLERY__@@下一页``。

    关键：口令词**必须作为参数传下去**（``_translate_maid`` 会把命中的暗号前缀吃掉，
    若暗号只映射成 ``/gallery``，参数会变空 → 永远停在第 1 页）。
    """
    r = route_intent(_norm(88901, "下一页"), Session(88901))
    assert r.kind == "reply"
    assert r.text == "__GALLERY__@@下一页", r.text


def test_route_prev_page_maps_to_gallery():
    r = route_intent(_norm(88902, "上一页"), Session(88902))
    assert r.text == "__GALLERY__@@上一页", r.text


def test_route_page_number_still_works():
    """老口令「历史 2」不受影响。"""
    r = route_intent(_norm(88903, "历史 2"), Session(88903))
    assert r.text == "__GALLERY__@@2", r.text


def test_nav_alias_is_exact_not_prefix():
    """口令是 exact 匹配：含「下一页」的正常句子必须原样走生图，不能被吞。"""
    r = route_intent(_norm(88904, "下一页要画猫"), Session(88904))
    assert "_GALLERY_" not in r.text, "正常句子被翻页口令吞了"
    assert r.kind in ("t2i", "queue_t2i"), r.kind
    assert "下一页要画猫" in (r.payload.get("prompt") or "")


# ------------------------------------------- nav_delta 语义（词表唯一来源）
@pytest.mark.parametrize("word,expect", [
    ("下一页", 1), ("下页", 1), ("翻页", 1), ("翻下页", 1), ("next", 1),
    ("上一页", -1), ("上页", -1), ("翻上页", -1), ("prev", -1), ("previous", -1),
    ("下一页。", 1), (" 下一页 ", 1), ("下一页？", 1),   # 尾标点/空格
    ("上面画的猫", 0), ("下一页要画猫", 0), ("页", 0), ("", 0), (None, 0),
])
def test_nav_delta(word, expect):
    assert gallery.nav_delta(word) == expect
    assert gallery.is_nav(word) == (expect != 0)


# ---------------------------------------- _cmd_gallery：页码真的动了吗
class _Rec:
    """记录 _cmd_gallery 对外动作（说/回/发图）+ 桩掉网络。"""

    def __init__(self, items, total, err=None):
        self.items = items
        self.total = total
        self.err = err
        self.fetched = []
        self.said = []
        self.replied = []
        self.pics = []


def _patch_gallery(monkeypatch, M, rec):
    async def _fetch_recent(page=1, type_filter=""):
        rec.fetched.append({"page": page, "type_filter": type_filter})
        return rec.items, rec.err, rec.total

    async def _build_page(items):
        return b"\x89PNG\r\n\x1a\nFAKE"

    async def _say(ctx, text):
        rec.said.append(text)

    async def _reply(ctx, sess, text, title=None, **_kw):
        rec.replied.append(text)

    async def _send_pic(ctx, data, **_kw):
        rec.pics.append(data)

    monkeypatch.setattr(M.gallery, "fetch_recent", _fetch_recent)
    monkeypatch.setattr(M.gallery, "build_page", _build_page)
    monkeypatch.setattr(M, "_say", _say)
    monkeypatch.setattr(M, "_reply", _reply)
    monkeypatch.setattr(M, "send_pic", _send_pic)


def _run_cmd(monkeypatch, uid, arg, cls="i2i", page=3, items=None, total=None):
    """同步跑一次 _cmd_gallery（桩掉网络与对外发送），返回动作记录。

    ``arg`` 传的是**生产形态**的参数（路由已 strip 掉 /gallery 前缀后的部分），
    如「下一页」「2」。
    """
    import asyncio

    import src.plugins.qq_bridge as M

    gallery.reset()
    if cls is not None:
        gallery.remember(uid, _items(gallery.PAGE_SIZE), cls=cls, page=page)
    rec = _Rec(_items(gallery.PAGE_SIZE) if items is None else items,
               (page + 1) * gallery.PAGE_SIZE if total is None else total)
    _patch_gallery(monkeypatch, M, rec)
    try:
        ev = _norm(uid, arg)
        asyncio.run(M._cmd_gallery(ev, Session(uid), arg))
    finally:
        gallery.reset()
    return rec


def test_cmd_gallery_next_page_advances_and_keeps_class(monkeypatch):
    """「下一页」→ 沿用上次分类，页码 +1（事故前永远停在第 1 页）。"""
    rec = _run_cmd(monkeypatch, 88911, "下一页", cls="i2i", page=3)
    assert rec.fetched == [{"page": 4, "type_filter": "i2i"}], rec.fetched
    assert rec.pics, "应重新拼一页九宫格发出去"
    assert any("第 4" in t for t in rec.replied), rec.replied


def test_cmd_gallery_prev_page_decrements(monkeypatch):
    rec = _run_cmd(monkeypatch, 88912, "上一页", cls="i2i", page=3)
    assert rec.fetched == [{"page": 2, "type_filter": "i2i"}], rec.fetched


def test_cmd_gallery_prev_page_clamps_at_one(monkeypatch):
    """第 1 页再「上一页」→ 仍停在第 1 页（不能变 0 页）。"""
    rec = _run_cmd(monkeypatch, 88913, "上一页", cls="i2i", page=1)
    assert rec.fetched[0]["page"] == 1, rec.fetched


def test_cmd_gallery_no_active_view_starts_at_page_one(monkeypatch):
    """没翻过相册就发「下一页」→ 当第 1 页处理（不崩、也不瞎跳）。"""
    rec = _run_cmd(monkeypatch, 88914, "下一页", cls=None)
    assert rec.fetched == [{"page": 1, "type_filter": ""}], rec.fetched


def test_cmd_gallery_explicit_page_ignores_nav(monkeypatch):
    """「历史 2」的显式页码优先，不受上次页码（7）影响。"""
    rec = _run_cmd(monkeypatch, 88915, "2", cls="i2i", page=7)
    assert rec.fetched == [{"page": 2, "type_filter": ""}], rec.fetched


def test_cmd_gallery_overflow_says_bottom_with_page_count(monkeypatch):
    """翻过头：据实报"已到底 + 共几页"并给回退口令（不再含糊说"这页没记录"）。"""
    rec = _run_cmd(monkeypatch, 88916, "下一页", cls="i2i", page=2,
                   items=[], total=2 * gallery.PAGE_SIZE)
    assert rec.replied, "应有回复"
    msg = rec.replied[0]
    assert "翻到底" in msg and "共 2 页" in msg, msg
    assert "历史 1" in msg, msg
    assert not rec.pics, "到底了不该再发图"


def test_cmd_gallery_first_page_empty_is_unchanged(monkeypatch):
    """对照：第 1 页本来就空 → 保留原提示（"相册还是空的"），不被"到底了"覆盖。"""
    rec = _run_cmd(monkeypatch, 88917, "历史", cls=None, items=[], total=0)
    assert rec.replied and "相册还是空的" in rec.replied[0], rec.replied


# --------------------------------------- 端到端：走真实 handle_message 全链路
def _patch_handle_message_core(monkeypatch):
    """把 handle_message 的前置非本测试关注的依赖桩掉（用真实 normalize / 真实路由）。"""
    monkeypatch.setattr(M.history, "record_incoming", lambda event, trace_id, self_id: None)
    monkeypatch.setattr(M, "allow", lambda ev: (True, ""))
    monkeypatch.setattr(M, "choice_sessions",
                        SimpleNamespace(answer=lambda *a, **k: None,
                                        has=lambda *a, **k: False,
                                        consume=lambda *a, **k: None))
    monkeypatch.setattr(M, "session_store",
                        SimpleNamespace(get=staticmethod(lambda u: None),
                                        ttl_cleanup=staticmethod(lambda: None)))
    monkeypatch.setattr(M, "_ctx", lambda ev: {"user_id": ev.user_id, "group_id": ev.group_id})
    monkeypatch.setattr(M.safety, "check_prompt", lambda text: (True, None))
    monkeypatch.setattr(M, "rate_limiter",
                        SimpleNamespace(allow=lambda *a, **k: (True, None),
                                        quota_allow=lambda *a, **k: (True, 0, 99),
                                        quota_refund=lambda *a, **k: None,
                                        peek_quota=lambda *a, **k: (0, 99)))


def _onebot_event(uid, gid, text, at_qq=None):
    """构造一个 nonebot v11 群消息事件（SimpleNamespace 鸭子类型）。"""
    segs = []
    if at_qq:
        segs.append(SimpleNamespace(type="at", data={"qq": str(at_qq)}))
    segs.append(SimpleNamespace(type="text", data={"text": text}))
    return SimpleNamespace(
        message_type="group", user_id=uid, group_id=gid, self_id=SELF_ID,
        message=segs, original_message=None, raw_message=text,
    )


def test_e2e_next_page_group_no_at_advances_pages(monkeypatch):
    """端到端复现用户场景：群聊没 @机器人 直接发「下一页」→ 画廊真的翻到第 2 页。

    全链路走真实代码：dispatcher 标准化 → KEYWORD_GATE → route → _cmd_gallery；
    只桩掉网络（fetch_recent）与出图（build_page/send_pic）。
    这是 2026-09-13 线上「发了下一页没反应」事故的精确反例。
    """
    uid, gid = 88921, 79221
    gallery.reset()
    # 模拟用户此前已看过第 1 页（画廊上下文建立 → 群门禁才给翻页口令放行）
    gallery.remember(uid, _items(gallery.PAGE_SIZE), cls="i2i", page=1)

    rec = _Rec(_items(gallery.PAGE_SIZE), (2) * gallery.PAGE_SIZE)
    _patch_gallery(monkeypatch, M, rec)
    _patch_handle_message_core(monkeypatch)

    bot = SimpleNamespace(self_id=SELF_ID)
    # 关键：不带 @（at_qq=None）——这正是事故现场（raw_message=下一页、群里没 @）
    ev = _onebot_event(uid, gid, "下一页")

    async def _run():
        await M.handle_message(bot, ev)
    import asyncio
    try:
        asyncio.run(_run())
    finally:
        gallery.reset()

    # 三道门全部放行 + 页码真的 +1：fetch_recent 必须被请求 page=2（沿用上次 i2i 分类）
    assert rec.fetched == [{"page": 2, "type_filter": "i2i"}], rec.fetched
    assert rec.pics, "应重新拼出一页九宫格并发送（事故前这里什么都没发）"
    assert any("第 2" in t for t in rec.replied), rec.replied


def test_e2e_no_gallery_next_page_still_silently_dropped(monkeypatch):
    """对照：没有画廊上下文时，群里没 @发「下一页」仍被门禁静默丢弃（不破口）。

    证明免 @ 放行口子只对画廊上下文开，普通群聊里的"下一页"不会误触发机器人。
    """
    uid, gid = 88922, 79222
    gallery.reset()  # 确保无活跃画廊
    rec = _Rec(_items(gallery.PAGE_SIZE), gallery.PAGE_SIZE * 2)
    _patch_gallery(monkeypatch, M, rec)
    _patch_handle_message_core(monkeypatch)

    bot = SimpleNamespace(self_id=SELF_ID)
    ev = _onebot_event(uid, gid, "下一页")

    import asyncio
    asyncio.run(M.handle_message(bot, ev))

    # 门禁静默丢弃 → handle_message 根本没走到画廊拉取，fetch_recent 一次都没被调
    assert rec.fetched == [], "无画廊上下文时「下一页」不应进入画廊处理（被门禁静默丢弃）"
