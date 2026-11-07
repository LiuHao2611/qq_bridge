# -*- coding: utf-8 -*-
"""对话页「快捷操作」验收测试（机器判定，2026-09-07 新增）。

背景：对话页从"只能看"升级为可操作——对 QQ 好友/群成员/群做禁言、踢人、
改群名片、拉黑、全员禁言、私聊、撤回等快捷操作。其中：

  * 禁言/踢人/改名片/全员禁言/私聊/撤回 依赖 NapCat 在线（OneBot 转发），
    其 HTTP 端点是 register() 内的闭包，必须真实 bridge 进程才能测，
    故本文件只覆盖**不依赖在线**且能被纯单测判定的核心逻辑；
  * 用户黑名单是**热生效**功能（用户明确要求改完不重启就生效），
    dispatcher.allow() 直接读 Config.USER_BLACKLIST 这个 set，
    因此"改 set → 下一条消息立即生效"完全可单测判定，是本文件重点。

覆盖：
  1. 拉黑 / 解除 热生效：改 Config.USER_BLACKLIST 后 allow() 立即生效，无需重启
  2. 黑名单持久化：写回 .env（apply_config），重启后仍在黑名单里
  3. message_id 落盘：入站消息存 OneBot message_id，撤回才能定位（delete_msg 依赖）
"""
import sys
from pathlib import Path

import pytest

from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.dispatcher import QQEvent, allow

_REPO = Path(__file__).resolve().parent.parent          # qq_bridge/
_PLUGIN = _REPO / "src" / "plugins" / "qq_bridge"
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))


def _ev(user_id=88888, group_id=0):
    """构造一条最小可判定事件（私聊，便于验证黑名单分支）。"""
    return QQEvent(
        user_id=user_id,
        group_id=group_id,
        message_type="group" if group_id else "private",
        raw_text="hi",
        at_bot=False,
        images=[],
        self_id="90001",
        trace_id="t-hot",
        raw=None,
    )


def test_blacklist_hot_reload():
    """核心：拉黑/解除**热生效**——改 set 后下一条消息即生效，无需重启 bridge。

    这是用户明确要求的能力（"热生效（不重启，下次收到消息时立即生效）"）。
    dispatcher.allow() 直接读 Config.USER_BLACKLIST，端点里 add/discard 改的是
    同一个 set 对象，因此不存在"改了要重启才生效"的问题。
    """
    uid = 88888
    ev = _ev(uid)

    # 基线：未拉黑时放行
    ok, reason = allow(ev)
    assert ok is True, "基线：未拉黑应放行，实际 %s" % (reason,)

    # 拉黑（等价于端点 Config.USER_BLACKLIST.add）
    Config.USER_BLACKLIST.add(uid)
    ok, reason = allow(ev)
    assert ok is False, "拉黑后必须立即拒绝（热生效）"
    assert reason == "silent", "黑名单必须是静默拒绝（不回任何提示），实际 %s" % reason

    # 解除（等价于端点 discard）
    Config.USER_BLACKLIST.discard(uid)
    ok, reason = allow(ev)
    assert ok is True, "移出黑名单后必须立即恢复响应（热生效），实际 %s" % (reason,)


def test_blacklist_hot_reload_group_member():
    """群成员场景同样热生效（群消息走 is_group 分支，黑名单检查在其之前）。"""
    uid = 77777
    ev = _ev(uid, group_id=123456)

    Config.USER_BLACKLIST.add(uid)
    try:
        ok, reason = allow(ev)
        assert ok is False and reason == "silent", "群成员被拉黑后必须立即静默"
    finally:
        Config.USER_BLACKLIST.discard(uid)

    ok, _ = allow(ev)
    assert ok is True, "解除后群消息应恢复放行"


def test_blacklist_persist_to_env(tmp_path, monkeypatch):
    """拉黑结果写回 .env：重启 bridge 后仍在黑名单里（持久化）。"""
    import config_api

    env = tmp_path / ".env"
    env.write_text("# header\nIMAGE_CHANNEL=local\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"USER_BLACKLIST": "111,222"})
    assert "USER_BLACKLIST" in res["saved"], res
    assert not res["errors"], res

    text = env.read_text(encoding="utf-8")
    assert "USER_BLACKLIST=111,222" in text, text
    assert "IMAGE_CHANNEL=local" in text, "写回必须保留原有配置行，不能整份覆盖"


def test_message_id_persisted_for_recall(tmp_path, monkeypatch):
    """撤回前置条件：入站消息落盘 OneBot message_id，/api/qq/recall_msg 才能定位。

    背景：delete_msg 需要的是 QQ 的 message_id，不是本地自增 id。此前 history
    根本没存这个字段，撤回功能等于不可用，故在此加断言防止回归。
    """
    from src.plugins.qq_bridge import history

    monkeypatch.setattr(history, "DB_PATH", str(tmp_path / "qq_history.db"))
    history.init_db()

    class _Seg:
        type = "text"
        data = {"text": "hello"}

    class _Sender:
        nickname = "nick"
        card = ""
        role = "member"

    class _Ev:
        message_type = "group"
        user_id = 12345
        group_id = 999111
        message_id = 777777            # OneBot 消息 ID（撤回靠它）
        original_message = [_Seg()]
        sender = _Sender()
        group_name = "测试群"

    history.record_incoming(_Ev(), "trace-1", "90001")

    page = history.list_messages_page("group", 999111, 60)
    assert page["messages"], "消息必须落盘"
    got = page["messages"][-1]
    assert got["message_id"] == 777777, "message_id 必须落盘，否则撤回无法定位：%s" % got
    assert got["text"] == "hello", got


def test_message_id_default_zero_when_absent(tmp_path, monkeypatch):
    """旧数据/无 message_id 的事件：字段存在且为 0，前端据此提示"无法撤回"而非崩溃。"""
    from src.plugins.qq_bridge import history

    monkeypatch.setattr(history, "DB_PATH", str(tmp_path / "qq_history2.db"))
    history.init_db()

    class _Sender:
        nickname = ""
        card = ""
        role = ""

    class _Ev:
        message_type = "private"
        user_id = 555
        group_id = 0
        original_message = []          # 没有 message_id 属性
        sender = _Sender()

    history.record_incoming(_Ev(), "trace-2", "90001")

    page = history.list_messages_page("private", 555, 60)
    assert page["messages"], "消息必须落盘"
    assert page["messages"][-1]["message_id"] == 0, "缺 message_id 时应落 0（前端提示无法撤回）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
