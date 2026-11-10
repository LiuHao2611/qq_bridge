# -*- coding: utf-8 -*-
"""官方通道验收测试（docs/qq_official_bot_migration_plan.md §3.3 / §6.4 / §8）。

验收标准：
  1) 注册表：official / napcat 取得到；未知通道名回退 napcat（且只告警一次）
  2) normalize：群 @、群非 @、私聊 → ctx v2 字段正确；无关事件 → None
  3) send：群走 send_to_group、私聊走 send_to_c2c，并带上被动回复的 msg_id/msg_seq
  4) send：无在线 bot → ok=False（记日志、不抛异常，CLOSE-02）
  5) channel_of_bot：官方 bot → official，其它 → napcat

说明：用假类 + monkeypatch 隔离真实适配器，保证单测不依赖网络与真实凭据
（与项目既有 test_dispatcher / test_taskmgr 的 duck typing 风格一致）。
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from src.plugins.qq_bridge.channel import NAP_CAT, OFFICIAL, channel_of_bot, get_channel
from src.plugins.qq_bridge.channel import napcat as napcat_mod
from src.plugins.qq_bridge.channel import official as official_mod


# ---------------------------------------------------------------- 假对象
class FakeQQBot:
    """占位：用于 channel_of_bot 的 isinstance 判定。"""


class FakeAuthor:
    def __init__(self, user_openid: str = "", member_openid: str = ""):
        self.user_openid = user_openid
        self.member_openid = member_openid


class FakeC2CEvent:
    def __init__(self, openid: str = "user-openid-1", eid: str = "msg-1"):
        self.author = FakeAuthor(user_openid=openid)
        self.id = eid

    def get_message(self):
        return []


class FakeGroupEvent:
    def __init__(self, group_openid: str = "grp-openid-1",
                 member_openid: str = "member-openid-1", eid: str = "msg-2"):
        self.author = FakeAuthor(member_openid=member_openid)
        self.group_openid = group_openid
        self.id = eid

    def get_message(self):
        return []


class FakeGroupAtEvent(FakeGroupEvent):
    pass


class FakeMessage(list):
    """支持 msg += seg。

    只把 **list** 展平、其余当单个元素追加：这样 Segment 用 tuple/对象表示时，
    消息里每个段仍是独立元素（与真实 Message 语义一致），便于断言。
    """

    def __iadd__(self, other):
        if isinstance(other, list):
            self.extend(other)
        else:
            self.append(other)
        return self

    def __add__(self, other):
        out = FakeMessage(self)
        out.__iadd__(other)
        return out


class FakeSegment:
    @staticmethod
    def text(content: str):
        return ("text", content)

    @staticmethod
    def mention_user(openid: str, username: str | None = None):
        return ("mention_user", openid)

    @staticmethod
    def file_image(data, file_name: str | None = None):
        # file_name 必须给：适配器据此走本地上传分支（见 official.py 说明）
        return ("file_image", file_name, len(data))

    @staticmethod
    def file_video(data, file_name: str | None = None):
        return ("file_video", file_name, len(data))


class FakeBot(FakeQQBot):
    """记录发送调用的假 Bot。"""

    def __init__(self):
        self.calls: list = []

    async def send_to_group(self, group_openid, message, msg_id=None, msg_seq=None,
                            event_id=None, msg_ref_id=None):
        self.calls.append(("group", group_openid, list(message), msg_id, msg_seq))
        return SimpleNamespace(id="ret-group-1")

    async def send_to_c2c(self, openid, message, msg_id=None, msg_seq=None,
                          event_id=None, msg_ref_id=None):
        self.calls.append(("c2c", openid, list(message), msg_id, msg_seq))
        return SimpleNamespace(id="ret-c2c-1")


@pytest.fixture
def patched(monkeypatch):
    """把 _import_qq 替换成假类，返回 (module, bot_holder)。"""
    monkeypatch.setattr(
        official_mod,
        "_import_qq",
        lambda: (FakeQQBot, FakeMessage, FakeSegment,
                 FakeC2CEvent, FakeGroupEvent, FakeGroupAtEvent),
    )
    holder = {}

    def _get(_cls):
        return holder.get("bot")

    monkeypatch.setattr(official_mod, "_get_official_bot", _get)
    return holder


@pytest.fixture(autouse=True)
def _clean_limiter():
    """每个用例前后清空日额度计数（单例，避免用例互相污染）。"""
    from src.plugins.qq_bridge.channel.limiter import official_limiter

    official_limiter._count.clear()
    official_limiter._warned.clear()
    yield
    official_limiter._count.clear()
    official_limiter._warned.clear()


def _ctx_of(event, self_id: str = "bot-1"):
    ch = get_channel(OFFICIAL)
    return asyncio.run(ch.normalize(event, self_id=self_id, trace_id="t-1"))


# ---------------------------------------------------------------- 1) 注册表
def test_registry_returns_both_channels():
    assert get_channel(OFFICIAL).name == OFFICIAL
    assert get_channel(NAP_CAT).name == NAP_CAT


def test_registry_unknown_falls_back_to_napcat():
    ch = get_channel("no-such-channel")
    assert isinstance(ch, napcat_mod.NapCatChannel)
    # 幂等：重复取仍是同一个实例（缓存生效）
    assert get_channel(NAP_CAT) is get_channel(NAP_CAT)


# ---------------------------------------------------------------- 2) normalize
def test_normalize_group_at(patched):
    ctx = _ctx_of(FakeGroupAtEvent())
    assert ctx is not None
    assert ctx["channel"] == OFFICIAL
    assert ctx["is_group"] is True
    assert ctx["at_bot"] is True
    assert ctx["group_key"] == "official:g:grp-openid-1"
    assert ctx["user_key"] == "official:u:member-openid-1"
    assert ctx["user_id"] == 0 and ctx["group_id"] == 0   # 官方无 int 型 QQ 号
    # ts 用于判断是否还能被动回复（平台硬限 5 分钟）
    assert ctx["reply_ref"]["msg_id"] == "msg-2"
    assert ctx["reply_ref"]["msg_seq"] == 1
    assert isinstance(ctx["reply_ref"].get("ts"), float)


def test_normalize_group_plain_not_at(patched):
    ctx = _ctx_of(FakeGroupEvent())
    assert ctx["at_bot"] is False
    assert ctx["is_group"] is True


def test_normalize_c2c_private(patched):
    ctx = _ctx_of(FakeC2CEvent())
    assert ctx["is_group"] is False
    assert ctx["group_key"] == ""
    assert ctx["user_key"] == "official:u:user-openid-1"
    assert ctx["reply_ref"]["msg_id"] == "msg-1"


def test_normalize_unknown_event_returns_none(patched):
    assert _ctx_of(SimpleNamespace(id="x")) is None


def test_normalize_missing_openid_returns_none(patched, caplog):
    assert _ctx_of(FakeC2CEvent(openid="")) is None


# ---------------------------------------------------------------- 3) send
def test_send_group_uses_send_to_group(patched):
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupAtEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="你好", at_user=True)))
    assert ret["ok"] is True
    assert len(bot.calls) == 1
    kind, target, msg, msg_id, msg_seq = bot.calls[0]
    assert kind == "group" and target == "grp-openid-1"
    assert msg_id == "msg-2" and msg_seq == 1          # 被动回复引用
    assert ("mention_user", "member-openid-1") in msg   # at_user
    assert ("text", "你好") in msg


def test_send_c2c_uses_send_to_c2c(patched):
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeC2CEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is True
    kind, target, msg, msg_id, _seq = bot.calls[0]
    assert kind == "c2c" and target == "user-openid-1"
    assert ("mention_user", "user-openid-1") not in msg  # 私聊不 @


def test_send_without_bot_returns_failed(patched):
    patched["bot"] = None
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is False
    assert "no official bot online" in ret["reason"]


def test_send_empty_message_is_skipped(patched):
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing()))
    assert ret.get("skipped") is True
    assert bot.calls == []


def test_send_exception_returns_reason(patched):
    class BoomBot(FakeQQBot):
        async def send_to_group(self, *a, **k):
            raise RuntimeError("platform 40034105")

    patched["bot"] = BoomBot()
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is False
    assert "40034105" in ret["reason"]


# ---------------------------------------------------------------- P2：富媒体 / 额度 / 被动转主动
def test_send_image_downloads_and_uploads(patched, monkeypatch):
    async def fake_download(url):
        return b"IMGBYTES"

    monkeypatch.setattr(official_mod, "_download", fake_download)
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(image_urls=["http://x/a.png"])))
    assert ret["ok"] is True
    assert len(bot.calls) == 1
    _kind, _target, msg, _mid, _seq = bot.calls[0]
    # file_name 必须非空，否则适配器会走 URL 上传分支
    assert ("file_image", "image.png", len(b"IMGBYTES")) in msg


def test_send_multiple_images_are_separate_messages(patched, monkeypatch):
    async def fake_download(url):
        return b"IMG"

    monkeypatch.setattr(official_mod, "_download", fake_download)
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(image_urls=["http://x/a.png", "http://x/b.png"])))
    assert ret["ok"] is True
    # 官方 msg_type 互斥（0=文本 / 7=富媒体）→ 每张图一条消息
    assert len(bot.calls) == 2
    assert len(ret["msg_ids"]) == 2


def test_passive_reply_inside_window(patched):
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())          # ts=now → 在窗口内
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is True
    assert ret["modes"] == ["passive"]       # 不占日额度
    assert bot.calls[0][3] == "msg-2"        # 带被动 msg_id


def test_expired_ref_switches_to_active_and_counts_quota(patched, monkeypatch):
    from src.plugins.qq_bridge.config import Config

    monkeypatch.setattr(Config, "OFFICIAL_PASSIVE_MAX_AGE", 1)
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    ctx["reply_ref"]["ts"] = time.time() - 3600      # 已过期
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is True
    assert ret["modes"] == ["active"]
    assert bot.calls[0][3] is None                  # 主动消息不带 msg_id
    from src.plugins.qq_bridge.channel.limiter import official_limiter

    assert official_limiter.used(ctx["group_key"]) == 1


def test_daily_quota_blocks_extra_active_messages(patched, monkeypatch):
    from src.plugins.qq_bridge.config import Config

    monkeypatch.setattr(Config, "OFFICIAL_PASSIVE_MAX_AGE", 1)
    monkeypatch.setattr(Config, "OFFICIAL_DAILY_GROUP_LIMIT", 1)

    async def fake_download(url):
        return b"IMG"

    monkeypatch.setattr(official_mod, "_download", fake_download)
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    ctx["reply_ref"]["ts"] = time.time() - 3600
    from src.plugins.qq_bridge.channel.base import Outgoing

    asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(image_urls=["http://x/a.png", "http://x/b.png"])))
    # 额度=1 → 第二张图被拦下，只发出 1 条（不撞平台 40034100）
    assert len(bot.calls) == 1


def test_limiter_resets_on_new_day():
    from src.plugins.qq_bridge.channel.limiter import OfficialLimiter

    lim = OfficialLimiter(limit_getter=lambda: 2)
    lim.add("g1")
    assert lim.used("g1") == 1
    assert lim.allow("g1") is True
    lim.add("g1")
    assert lim.allow("g1") is False
    # 模拟跨天
    lim._day = "2000-01-01"
    assert lim.used("g1") == 0
    assert lim.allow("g1") is True


# ---------------------------------------------------------------- P3：业务接入
class FakeSeg:
    def __init__(self, type_: str, data: dict):
        self.type = type_
        self.data = data


class FakeGroupEventWithText(FakeGroupEvent):
    def get_message(self):
        return [
            FakeSeg("text", {"text": " 生图 一只猫 "}),
            FakeSeg("image", {"url": "http://x/1.png"}),
        ]


class FakeChannelRecorder:
    """记录通道层收到的 Outgoing（用于验证 media 分派）。"""

    name = "official"

    def __init__(self):
        self.sent: list = []

    async def send(self, ctx, out):
        self.sent.append(out)
        return {"ok": True}


def test_to_legacy_event_maps_openid_to_negative_id(patched):
    """官方事件 → QQEvent：伪 ID 必须为负（真实 QQ 号恒为正，永不串号）。"""
    ch = get_channel(OFFICIAL)
    ev = asyncio.run(ch.to_legacy_event(FakeGroupEventWithText(), self_id="bot-1", trace_id="t-1"))
    assert ev is not None
    assert ev.user_id < 0
    assert ev.group_id < 0
    assert ev.message_type == "group"
    assert ev.raw_text == "生图 一只猫"
    assert ev.images == ["http://x/1.png"]
    assert getattr(ev, "channel") == OFFICIAL      # media 层据此分派


def test_pseudo_id_is_stable_and_negative():
    from src.plugins.qq_bridge.channel.official import pseudo_id

    a, b = pseudo_id("official:u:abc"), pseudo_id("official:u:abc")
    assert a == b                      # 稳定：同一 openid 永远同一 ID
    assert a < 0 and b < 0
    assert pseudo_id("official:u:abc") != pseudo_id("official:u:def")


def test_media_dispatches_by_channel(monkeypatch):
    """ctx 带 channel=official → media 交给通道层，不再走 OneBot 消息段。"""
    from src.plugins.qq_bridge import media

    fake = FakeChannelRecorder()
    monkeypatch.setattr("src.plugins.qq_bridge.channel.get_channel", lambda _n: fake)

    ctx = {"user_id": -1, "group_id": -2, "channel": "official"}
    asyncio.run(media.send_text(ctx, "hi"))
    asyncio.run(media.send_pic(ctx, b"PNGDATA"))
    assert len(fake.sent) == 2
    assert fake.sent[0].text == "hi"
    assert fake.sent[1].images == [b"PNGDATA"]


def test_media_without_channel_field_keeps_napcat_path():
    """老调用点没有 channel 字段 → 判定为 NapCat，行为与改造前一致。"""
    from src.plugins.qq_bridge.media import _foreign_channel

    assert _foreign_channel({"user_id": 10001, "group_id": 0}) == ""
    assert _foreign_channel({"user_id": 1, "channel": "napcat"}) == ""
    assert _foreign_channel({"channel": "official"}) == "official"


# ---------------------------------------------------------------- 5) 分发
def test_channel_of_bot_distinguishes(monkeypatch):
    import src.plugins.qq_bridge.channel as channel_pkg

    monkeypatch.setattr(channel_pkg, "_official_bot_cls", lambda: FakeQQBot)
    assert channel_of_bot(FakeBot()) == OFFICIAL
    assert channel_of_bot(SimpleNamespace()) == NAP_CAT
    assert channel_of_bot(None) == NAP_CAT


def test_channel_of_bot_without_adapter(monkeypatch):
    import src.plugins.qq_bridge.channel as channel_pkg

    # 适配器未安装 → 一律判为 NapCat，绝不把官方事件误判成官方通道
    monkeypatch.setattr(channel_pkg, "_official_bot_cls", lambda: None)
    assert channel_of_bot(FakeBot()) == NAP_CAT
