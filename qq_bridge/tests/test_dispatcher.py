# -*- coding: utf-8 -*-
"""dispatcher 验收测试（设计文档 §3.2）。

验收标准：
  1) 群消息未 @机器人 且无命令前缀 → 直接丢弃（不消耗额度）
  2) 私聊黑名单用户 → 静默拒绝
  3) 标准化事件字段正确（user_id/group_id/raw_text/at_bot/images/scope_key）
"""
import pytest

from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.dispatcher import allow, is_owner, normalize


class Seg:
    """模拟 onebot 消息段（duck typing：只需 type + data）。"""

    def __init__(self, type_: str, data: dict):
        self.type = type_
        self.data = data


class FakeEvent:
    def __init__(self, message_type="private", user_id=10001, group_id=0, message=None, self_id="90001"):
        self.message_type = message_type
        self.user_id = user_id
        self.group_id = group_id
        self.message = message if message is not None else []
        self.self_id = self_id


def text(s):
    return Seg("text", {"text": s})


def at(qq):
    return Seg("at", {"qq": str(qq)})


def image(url):
    return Seg("image", {"url": url, "file": url})


# ---------------- Config 隔离（防用例间状态污染）----------------
# 本文件多个用例直接改 Config 全局属性（BOT_OWNER_QQ/白名单/黑名单），
# 若不还原会污染同文件后续用例（如 owner 用例遗留 {10001} 使白名单用例
# 误判放行）乃至其它测试文件。autouse fixture 快照/还原。
import copy  # noqa: E402

_CONFIG_ATTRS = ("BOT_OWNER_QQ", "USER_BLACKLIST", "PRIVATE_WHITELIST",
                 "GROUP_WHITELIST", "STRICT_WHITELIST")


@pytest.fixture(autouse=True)
def _isolate_config():
    saved = {a: copy.deepcopy(getattr(Config, a, None)) for a in _CONFIG_ATTRS}
    yield
    for a, v in saved.items():
        setattr(Config, a, v)


# ---------------- §3.2 事件标准化 ----------------

def test_group_message_without_at_is_dropped():
    """验收 1：群消息未 @机器人、无命令前缀 → 丢弃。"""
    ev = FakeEvent("group", group_id=20001, message=[text("大家好")], self_id="90001")
    assert normalize(ev, self_id="90001") is None


def test_group_message_with_at_is_accepted():
    ev = FakeEvent("group", group_id=20001, message=[at("90001"), text("画个城堡")], self_id="90001")
    got = normalize(ev, self_id="90001")
    assert got is not None
    assert got.at_bot is True
    assert got.raw_text == "画个城堡"
    assert got.is_group is True
    assert got.scope_key == "g:20001"


def test_group_message_with_command_prefix_is_accepted():
    """命令前缀**不**再视为显式唤起（2026-09-04 收紧后需真 @机器人）。

    历史行为：`/help` 等前缀无 @ 也受理。2026-09-04 用户决策去掉该口子
    （群里别人随手发 /help 或暗号就误触发刷屏），改为仅真 @ / 主人 /
    pending choice / AI 模式群 / 画廊上下文放行。本测试锁定新口径。
    """
    ev = FakeEvent("group", group_id=20001, message=[text("/help")], self_id="90001")
    got = normalize(ev, self_id="90001")
    assert got is None


def test_group_at_other_qq_is_not_at_bot():
    """@别人不算 @机器人 → 丢弃。"""
    ev = FakeEvent("group", group_id=20001, message=[at("10002"), text("在吗")], self_id="90001")
    assert normalize(ev, self_id="90001") is None


def test_private_message_is_accepted_and_scoped_by_user():
    ev = FakeEvent("private", user_id=10001, message=[text("一只赛博朋克猫")])
    got = normalize(ev, self_id="90001")
    assert got is not None
    assert got.is_group is False
    assert got.group_id == 0
    assert got.raw_text == "一只赛博朋克猫"
    assert got.scope_key == "u:10001"


def test_non_message_type_is_dropped():
    ev = FakeEvent("notice", user_id=10001, message=[text("x")])
    assert normalize(ev, self_id="90001") is None


def test_images_are_extracted():
    ev = FakeEvent("private", user_id=10001,
                   message=[image("http://cdn/a.png"), text("改成水彩风")])
    got = normalize(ev, self_id="90001")
    assert got is not None
    assert got.images == ["http://cdn/a.png"]
    assert got.raw_text == "改成水彩风"


# ---------------- §8 权限过滤 ----------------

def test_blacklist_user_is_silently_denied():
    """验收 2：黑名单静默（reason='silent'）。"""
    Config.USER_BLACKLIST = {10001}
    ev = normalize(FakeEvent("private", user_id=10001, message=[text("hi")]), self_id="90001")
    ok, reason = allow(ev)
    assert ok is False
    assert reason == "silent"


def test_owner_bypasses_blacklist():
    Config.BOT_OWNER_QQ = {10001}
    Config.USER_BLACKLIST = {10001}
    ev = normalize(FakeEvent("private", user_id=10001, message=[text("hi")]), self_id="90001")
    ok, reason = allow(ev)
    assert ok is True
    assert reason == "owner"
    assert is_owner(10001) is True


def test_empty_whitelist_allows_by_default():
    """STRICT_WHITELIST=False（默认）时，名单为空 = 放行所有人，方便自己小号先跑通。"""
    Config.STRICT_WHITELIST = False
    Config.PRIVATE_WHITELIST = set()
    ev = normalize(FakeEvent("private", user_id=10001, message=[text("hi")]), self_id="90001")
    ok, _ = allow(ev)
    assert ok is True


def test_strict_whitelist_blocks_unlisted_user():
    Config.STRICT_WHITELIST = True
    Config.PRIVATE_WHITELIST = {2}
    ev = normalize(FakeEvent("private", user_id=10001, message=[text("hi")]), self_id="90001")
    ok, reason = allow(ev)
    assert ok is False
    assert reason == "silent"


def test_strict_whitelist_allows_listed_user():
    Config.STRICT_WHITELIST = True
    Config.PRIVATE_WHITELIST = {10001}
    ev = normalize(FakeEvent("private", user_id=10001, message=[text("hi")]), self_id="90001")
    assert allow(ev)[0] is True


def test_group_whitelist_filters_group_id():
    Config.GROUP_WHITELIST = {20002}
    ev = normalize(
        FakeEvent("group", group_id=20001, message=[at("90001"), text("画")], self_id="90001"),
        self_id="90001",
    )
    assert allow(ev)[0] is False

    ev2 = normalize(
        FakeEvent("group", group_id=20002, message=[at("90001"), text("画")], self_id="90001"),
        self_id="90001",
    )
    assert allow(ev2)[0] is True
