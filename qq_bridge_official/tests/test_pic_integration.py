# -*- coding: utf-8 -*-
"""P4 验收：自绘图片接入真实消息链路（/help、/status、/quota、主题切换）。

核心断言：**能出图时出图，出不了图必须回落纯文本**（CLOSE-02 禁止静默失败）。

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_pic_integration.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.plugins.qq_bridge as M  # noqa: E402
from src.plugins.qq_bridge.config import Config  # noqa: E402
from src.plugins.qq_bridge.dispatcher import QQEvent  # noqa: E402
from src.plugins.qq_bridge.intent import HELP_TEXT  # noqa: E402
from src.plugins.qq_bridge.session import Session  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _ev(user_id: int = 10001, group_id: int = 0) -> QQEvent:
    return QQEvent(user_id=user_id, group_id=group_id,
                   message_type="group" if group_id else "private", raw_text="")


class _Rec:
    """记录 send_pic / send_text 的调用。"""

    def __init__(self) -> None:
        self.pics: list[bytes] = []
        self.texts: list[str] = []

    async def pic(self, ctx, data, at_user=False):
        self.pics.append(data)

    async def text(self, ctx, text, at_user=False):
        self.texts.append(text)


@pytest.fixture
def rec(monkeypatch):
    r = _Rec()
    monkeypatch.setattr(M, "send_pic", r.pic)
    monkeypatch.setattr(M, "send_text", r.text)
    monkeypatch.setattr(M, "maybe_send_sticker", lambda ctx: asyncio.sleep(0))
    return r


# ---------------------------------------------------------------- _try_send_pic
def test_try_send_pic_success(rec):
    ok = _run(M._try_send_pic({"user_id": 1, "group_id": 0}, "menu"))
    assert ok is True
    assert len(rec.pics) == 1
    assert rec.pics[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_try_send_pic_failure_falls_back(rec, monkeypatch):
    """渲染失败必须返回 False 且不发图，让调用方回落纯文本。"""
    async def _boom(*a, **kw):
        raise RuntimeError("Pillow 炸了")

    monkeypatch.setattr(M.pic_mod, "render", _boom)
    ok = _run(M._try_send_pic({"user_id": 1, "group_id": 0}, "menu"))
    assert ok is False
    assert rec.pics == []


def test_try_send_pic_disabled(rec, monkeypatch):
    """PIC_ENABLE=false 时不发图（一键止血开关）。"""
    monkeypatch.setattr(Config, "PIC_ENABLE", False)
    ok = _run(M._try_send_pic({"user_id": 1, "group_id": 0}, "menu"))
    assert ok is False
    assert rec.pics == []


# ---------------------------------------------------------------- 三个入口
def test_help_sends_menu_pic(rec):
    _run(M._handle_reply(_ev(), Session(10001), HELP_TEXT, 0, 50))
    assert len(rec.pics) == 1 and rec.pics[0][:8] == b"\x89PNG\r\n\x1a\n"
    assert rec.texts == [], "出图成功就不该再发纯文本"


def test_help_falls_back_to_text(rec, monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("渲染失败")

    monkeypatch.setattr(M.pic_mod, "render", _boom)
    _run(M._handle_reply(_ev(), Session(10001), HELP_TEXT, 0, 50))
    assert rec.pics == []
    assert len(rec.texts) == 1 and "本喵能干的活儿" in rec.texts[0]


def test_status_with_task_sends_progress(rec, monkeypatch):
    monkeypatch.setattr(
        M.task_mgr, "get_active_for",
        lambda uid: {"task_id": "t1", "user_id": 10001, "kind": "t2i",
                     "prompt": "一只猫", "trace_id": "abcdef1234567890",
                     "started_at": __import__("time").time() - 90},
    )
    _run(M._handle_reply(_ev(), Session(10001), "__STATUS__", 0, 50))
    assert len(rec.pics) == 1 and rec.pics[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_status_idle_sends_text_pic(rec, monkeypatch):
    """空闲状态也走 text 卡图（用户决策：@机器人 返回一律出图）。"""
    monkeypatch.setattr(M.task_mgr, "get_active_for", lambda uid: None)
    monkeypatch.setattr(M.task_mgr, "get_any_active", lambda **kw: None)
    # 2026-09-11：/status 加了队列兜底，真正"全闲"还须队列无 running、无排队
    monkeypatch.setattr(M.queue_mgr, "running_status", lambda: None)
    monkeypatch.setattr(M.queue_mgr, "pending_status", lambda: None)
    _run(M._handle_reply(_ev(), Session(10001), "__STATUS__", 0, 50))
    assert len(rec.pics) == 1 and rec.pics[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_quota_sends_quota_pic(rec):
    _run(M._handle_reply(_ev(), Session(10001), "__QUOTA__", 12, 50))
    assert len(rec.pics) == 1 and rec.pics[0][:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- 主题切换
def test_theme_switch_sets_session(rec):
    sess = Session(10001)
    _run(M._handle_reply(_ev(), sess, "__THEME_LIGHT__", 0, 50))
    assert sess.pic_theme == "light"
    _run(M._handle_reply(_ev(), sess, "__THEME_NEON__", 0, 50))
    assert sess.pic_theme == "neon"
    _run(M._handle_reply(_ev(), sess, "__THEME_AUTO__", 0, 50))
    assert sess.pic_theme is None


def test_session_theme_used_by_render(rec, monkeypatch):
    """会话主题必须真的传给 render（否则切换是假的）。"""
    seen = {}

    async def _spy(template, theme_name=None, use_cache=True, **params):
        seen["theme"] = theme_name
        return (await M.pic_mod.render_sync.__wrapped__(template, theme_name, **params)
                if hasattr(M.pic_mod.render_sync, "__wrapped__")
                else M.pic_mod.render_sync(template, theme_name, **params))

    monkeypatch.setattr(M.pic_mod, "render", _spy)
    sess = Session(10001)
    sess.pic_theme = "light"
    _run(M._handle_reply(_ev(), sess, "__QUOTA__", 3, 50))
    assert seen.get("theme") == "light"


def test_maid_alias_for_theme():
    from src.plugins.qq_bridge.intent import route as route_intent

    ev = _ev()
    ev.raw_text = "换深色"
    assert route_intent(ev, Session(10001)).text == "__THEME_NEON__"
    ev.raw_text = "换浅色"
    assert route_intent(ev, Session(10001)).text == "__THEME_LIGHT__"
    ev.raw_text = "自动主题"
    assert route_intent(ev, Session(10001)).text == "__THEME_AUTO__"


# ---------------------------------------------------------------- media.send_pic
def test_send_pic_uses_base64(monkeypatch):
    """自绘图必须走 base64:// 直发（NapCat 拉本地 URL 有踩坑史）。"""
    calls = []

    async def _fake_call(action, params=None, timeout=30.0):
        calls.append((action, params))
        return {"message_id": 1}

    monkeypatch.setattr(M.connector, "call_action", _fake_call)
    monkeypatch.setattr(M.connector, "first_bot", lambda: None)
    _run(M.send_pic({"user_id": 10001, "group_id": 0}, b"\x89PNG\r\n\x1a\nDATA"))
    assert calls, "未调用 OneBot 发送"
    msg = calls[0][1]["message"]
    assert "base64://" in msg and "[CQ:image" in msg or "base64" in msg


def test_send_pic_rejects_empty():
    with pytest.raises(ValueError):
        _run(M.send_pic({"user_id": 1, "group_id": 0}, b""))


# ---------------------------------------------------------------- _reply 统一出口
def test_mode_switch_sends_text_pic(rec):
    _run(M._handle_reply(_ev(), Session(10001), "__MODE_VIDEO__", 0, 50))
    assert len(rec.pics) == 1 and rec.pics[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_theme_switch_sends_text_pic_and_updates_session(rec):
    sess = Session(10001)
    _run(M._handle_reply(_ev(), sess, "__THEME_LIGHT__", 0, 50))
    assert len(rec.pics) == 1
    assert sess.pic_theme == "light", "出图与更新会话态要分离：图发了，会话主题也得改"


def test_reply_helper_falls_back_on_render_fail(rec, monkeypatch):
    """_reply 在 text 渲染失败时回落纯文本（CLOSE-02 禁止静默失败）。"""

    async def _boom(*a, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(M.pic_mod, "render", _boom)
    _run(M._reply({"user_id": 1, "group_id": 0}, Session(1), "hello", title="t"))
    assert rec.pics == []
    assert rec.texts == ["hello"]


# ---------------------------------------------------------------- 主题编辑器入口触发
def test_theme_editor_hint_sends_text_pic_with_url(rec, monkeypatch):
    """用户自然语言「主题编辑器」应触发 _reply 发图卡（带 URL 指引）。

    场景：用户在 NapCat WebUI 里找不到入口 → 给机器人发一句「主题编辑器」
    → 机器人回一张带 URL 的图卡（直白告诉浏览器开哪个地址）。
    """
    from types import SimpleNamespace

    # 屏蔽前置副作用，让 handle_message 一路走到我们的触发器就 return
    monkeypatch.setattr(M.history, "record_incoming", lambda *a, **kw: None)
    cs = SimpleNamespace(
        answer=lambda *a: None, has=lambda *a: False, consume=lambda *a: None,
    )
    monkeypatch.setattr(M, "choice_sessions", cs)

    # 构造一个 NoneBot-like 事件（normalize 会从 message 段提取 raw_text）
    text_seg = SimpleNamespace(type="text", data={"text": "主题编辑器"})
    fake_event = SimpleNamespace(
        message_type="private", user_id=10001, group_id=0, self_id="90001",
        message=[text_seg], original_message=None,
    )
    bot = SimpleNamespace(self_id="90001")
    _run(M.handle_message(bot, fake_event))

    assert len(rec.pics) == 1, "触发器应立刻发一张图卡"
    assert rec.pics[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_theme_editor_hint_also_triggers_on_aliases(rec, monkeypatch):
    """同义词也要命中：编辑主题 / 自定义主题 / 配色编辑器 / theme editor。"""
    from types import SimpleNamespace

    monkeypatch.setattr(M.history, "record_incoming", lambda *a, **kw2: None)
    cs = SimpleNamespace(
        answer=lambda *a: None, has=lambda *a: False, consume=lambda *a: None,
    )
    monkeypatch.setattr(M, "choice_sessions", cs)
    bot = SimpleNamespace(self_id="90001")

    for kw in ("编辑主题", "自定义主题", "配色编辑器", "theme editor"):
        rec.pics.clear(); rec.texts.clear()
        text_seg = SimpleNamespace(type="text", data={"text": kw})
        fake_event = SimpleNamespace(
            message_type="private", user_id=10001, group_id=0, self_id="90001",
            message=[text_seg], original_message=None,
        )
        _run(M.handle_message(bot, fake_event))
        assert len(rec.pics) == 1, f"关键词「{kw}」应触发图卡"


# ---------------------------------------------------------------- 帮助菜单：主人 / 一般分离
def _titles(items) -> list:
    """从菜单条目里取标题（兼容 tuple 与 dict 两种形态，跳过分组行）。"""
    out = []
    for x in items or []:
        if isinstance(x, dict):
            if x.get("section") is None and x.get("title"):
                out.append(x["title"])
        elif isinstance(x, (list, tuple)) and len(x) >= 2:
            out.append(x[1])
    return out


def test_menu_params_owner_sees_owner_section(monkeypatch):
    """_menu_params：主人看到「主人专属」条目，一般人看不到（2026-09-13 菜单分层）。"""
    monkeypatch.setattr(Config, "BOT_OWNER_QQ", ("10001",))
    monkeypatch.setattr(Config, "PIC_MENU_SHOW_OWNER", True)
    monkeypatch.setattr(Config, "PIC_MENU_ITEMS", "")
    monkeypatch.setattr(Config, "PIC_MENU_OWNER_ITEMS", "")

    owner = M._menu_params(10001)
    other = M._menu_params(20002)
    assert owner["owner"] is True and other["owner"] is False

    ot, gt = _titles(owner["items"]), _titles(other["items"])
    assert "主项目任务" in ot and "生图监听" in ot, "主人应看到专属条目"
    assert "主项目任务" not in gt and "生图监听" not in gt, "一般人不得看到主人专属"
    assert "生图" in gt, "一般菜单仍须含基础命令"


def test_menu_params_show_owner_can_be_disabled(monkeypatch):
    """PIC_MENU_SHOW_OWNER=false → 连主人也只看到一般菜单。"""
    monkeypatch.setattr(Config, "BOT_OWNER_QQ", ("10001",))
    monkeypatch.setattr(Config, "PIC_MENU_SHOW_OWNER", False)
    monkeypatch.setattr(Config, "PIC_MENU_ITEMS", "")
    p = M._menu_params(10001)
    assert p["owner"] is False
    assert "主项目任务" not in _titles(p["items"])


def test_help_text_sends_menu_with_owner_items(rec, monkeypatch):
    """真实 /help 链路：主人收到的菜单图参数带 owner=True 且含主人专属条目。"""
    monkeypatch.setattr(Config, "BOT_OWNER_QQ", ("10001",))
    monkeypatch.setattr(Config, "PIC_MENU_SHOW_OWNER", True)
    monkeypatch.setattr(Config, "PIC_MENU_ITEMS", "")
    monkeypatch.setattr(Config, "PIC_MENU_OWNER_ITEMS", "")

    seen = {}

    async def _spy(template, theme_name=None, **params):
        seen["template"] = template
        seen["params"] = params
        return b"\x89PNG\r\n\x1a\n"

    monkeypatch.setattr(M.pic_mod, "render", _spy)
    _run(M._handle_reply(_ev(10001), Session(10001), HELP_TEXT, 0, 50))
    assert seen.get("template") == "menu", "帮助应出菜单图"
    assert seen["params"].get("owner") is True
    assert "主项目任务" in _titles(seen["params"].get("items"))
