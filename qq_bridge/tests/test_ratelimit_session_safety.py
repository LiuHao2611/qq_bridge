# -*- coding: utf-8 -*-
"""ratelimit / session / safety 验收测试（设计文档 §3.5、§3.6、§3.8）。"""
import time

from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.ratelimit import RateLimiter
from src.plugins.qq_bridge.safety import Safety
from src.plugins.qq_bridge.session import SessionStore


# ---------------- §3.6 限流 ----------------

def test_cooldown_blocks_second_call():
    """验收：超频返回拒绝，并给出剩余冷却秒数。"""
    rl = RateLimiter(private_cooldown=10, group_cooldown=5)
    ok, _ = rl.allow("u:10001")
    assert ok is True
    ok, retry_after = rl.allow("u:10001")
    assert ok is False
    assert retry_after > 0


def test_cooldown_expires():
    rl = RateLimiter(private_cooldown=1, group_cooldown=1)
    assert rl.allow("u:10001")[0] is True
    time.sleep(1.05)
    assert rl.allow("u:10001")[0] is True


def test_group_cooldown_is_shorter_than_private():
    rl = RateLimiter(private_cooldown=10, group_cooldown=5)
    assert rl.cooldown_for("g:20001") == 5
    assert rl.cooldown_for("u:10001") == 10


def test_different_users_do_not_share_cooldown():
    rl = RateLimiter(private_cooldown=10)
    assert rl.allow("u:1")[0] is True
    assert rl.allow("u:2")[0] is True


def test_daily_quota_exhausts_and_refunds():
    """CLOSE-04：任务失败必须退还配额占用。"""
    rl = RateLimiter(daily_quota=2)
    assert rl.quota_allow(10001) == (True, 1, 2)
    assert rl.quota_allow(10001) == (True, 2, 2)
    ok, used, quota = rl.quota_allow(10001)
    assert ok is False and used == 2 and quota == 2

    rl.quota_refund(10001)      # 模拟一次任务失败退还
    assert rl.quota_allow(10001)[0] is True


def test_quota_disabled_when_zero():
    rl = RateLimiter(daily_quota=0)
    for _ in range(5):
        assert rl.quota_allow(10001)[0] is True


# ---------------- §3.8 会话态 ----------------

def test_session_pending_image_persists_for_followup():
    """验收：先发图、再发描述，能关联源图。"""
    store = SessionStore(ttl=600)
    store.set(10001, {"pending_image": "http://cdn/a.png"})
    assert store.get(10001).pending_image == "http://cdn/a.png"


def test_session_mode_switch():
    store = SessionStore(ttl=600)
    store.set(10001, {"mode": "video"})
    assert store.get(10001).mode == "video"


def test_session_ttl_cleanup():
    """验收：TTL 过期自动清理（OPS-02/03，且幂等）。"""
    store = SessionStore(ttl=1)
    store.get(10001)
    store.get(10002)
    assert len(store) == 2
    time.sleep(1.05)
    assert store.ttl_cleanup() == 2
    assert len(store) == 0
    assert store.ttl_cleanup() == 0     # 幂等：重复清理不报错


def test_session_expired_on_get():
    store = SessionStore(ttl=1)
    store.set(10001, {"mode": "video"})
    time.sleep(1.05)
    assert store.get(10001).mode == "image"   # 过期后返回全新会话


# ---------------- §3.5 内容安全 ----------------

def test_safety_blocks_sensitive_prompt():
    """验收：违规 prompt 被拒，且不回显命中词。"""
    s = Safety(enable=True)
    ok, reason = s.check_prompt("帮我画一张色情图片")
    assert ok is False
    assert reason == "内容不合规"
    assert "色情" not in reason


def test_safety_passes_normal_prompt():
    s = Safety(enable=True)
    assert s.check_prompt("一只赛博朋克猫") == (True, "")


def test_safety_can_be_disabled():
    s = Safety(enable=False)
    assert s.check_prompt("色情")[0] is True


def test_safety_extra_words_merge():
    Config.SAFETY_EXTRA_WORDS = "违禁词A,违禁词B"
    s = Safety(enable=True)
    assert s.check_prompt("包含违禁词A的内容")[0] is False
    assert s.check_prompt("一只猫")[0] is True


def test_safety_media_scheme_whitelist():
    s = Safety(enable=True)
    assert s.check_media("https://x/a.png", kind="image")[0] is True
    assert s.check_media("file:///d:/a.png", kind="image")[0] is True
    assert s.check_media("ftp://x/a.png", kind="image")[0] is False
    assert s.check_media("base64://AAAA", kind="image")[0] is True
    assert s.check_media("", kind="image")[0] is False


def test_safety_media_extension_check():
    s = Safety(enable=True)
    assert s.check_media("d:/out/a.png", kind="image")[0] is True
    assert s.check_media("d:/out/a.exe", kind="image")[0] is False
    assert s.check_media("d:/out/a.mp4", kind="video")[0] is True
    assert s.check_media("d:/out/a.png", kind="video")[0] is False
