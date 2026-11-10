# -*- coding: utf-8 -*-
"""豆包 seed-audio 生成式 TTS 客户端 + provider 路由验收（2026-11-10）。

覆盖：
  1. 成功：POST 私有协议（X-Api-Key / text_prompt / audio_config），解析 JSON 内嵌
     base64 MP3 → bytes；mock 一个真 MP3 帧头；
  2. 失败路径全显式 AudioError（CLOSE-02，绝不静默/绝不本地兜底）：
     缺 key / 空文本 / 超字数 / HTTP 4xx / 非 JSON / 顶层非对象 / 缺 audio /
     非法 base64 / 空音频 / 非 MP3 帧头 / 超时；
  3. decode_audio_field 反例（data: URI 容错、空白折行、坏帧头）；
  4. build_text_prompt 用「」包裹用户文本 + 音色描述；
  5. MediaManager provider 路由：auto(seed优先/openai兜底/都没有报错) /
     seed 强制 / openai 强制；capabilities 反映 seed/route 但不泄 key；
  6. 端到端：seed 音频 → send_record → CQ:record base64 往返逐字节一致。

全离线：httpx.MockTransport 注入，不打真网络、不烧额度、不依赖 Docker/key。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_audio_seed.py -v
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugins.qq_bridge.config import Config  # noqa: E402
from src.plugins.qq_bridge.multimodal.audio import AudioError  # noqa: E402
from src.plugins.qq_bridge.multimodal.audio_seed import (  # noqa: E402
    DEFAULT_VOICE_DESC, SeedAudioClient, build_text_prompt, decode_audio_field,
)

# 一个「长得像真 MP3」的 mock 音频：ID3 帧头 + 全字节谱（测 base64 往返最严格）
_FAKE_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(range(256)) * 4
_FAKE_MP3_B64 = base64.b64encode(_FAKE_MP3).decode("ascii")


def _client(handler, *, key: str = "test-key"):
    """注入 MockTransport 的客户端；显式传 key 隔离 Config。"""
    return SeedAudioClient(api_key=key,
                           endpoint="http://seed.test/api/v3/tts/create",
                           transport=httpx.MockTransport(handler))


def _ok_handler(seen: dict):
    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(200, json={
            "audio": _FAKE_MP3_B64, "duration": 2.0, "original_duration": 2.0,
            "url": "https://cdn.test/audio.mp3?x-signature=fake",
        })
    return handler


def _run(coro):
    return asyncio.run(coro)


# ---------------- 1. 成功路径 ----------------
def test_speak_success_parses_private_protocol():
    """成功：验鉴权头/请求体契约 + 解析 base64 MP3。"""
    seen = {}
    data = _run(_client(_ok_handler(seen)).speak("你好，小喵"))
    assert data == _FAKE_MP3, "应原样返回解码后的 MP3 bytes"
    assert seen["path"] == "/api/v3/tts/create"
    assert seen["headers"].get("x-api-key") == "test-key", "必须走 X-Api-Key 头"
    body = seen["body"]
    assert body["model"] == "seed-audio-1.0"
    assert "小喵" in body["text_prompt"] and body["text_prompt"].startswith(DEFAULT_VOICE_DESC)
    assert "「你好，小喵」" in body["text_prompt"], "用户文本须用「」包裹防带偏音色"
    assert body["audio_config"]["format"] == "mp3"
    assert "watermark" in body


def test_build_text_prompt_custom_voice_desc():
    assert build_text_prompt("你好", "台湾口音沉稳男声") == "台湾口音沉稳男声：「你好」"
    assert build_text_prompt("  你好  ", "") == "「你好」"


# ---------------- 2. decode_audio_field 反例 ----------------
def test_decode_audio_field_variants_and_counterexamples():
    assert decode_audio_field(_FAKE_MP3_B64) == _FAKE_MP3
    # data: URI 容错
    assert decode_audio_field("data:audio/mpeg;base64," + _FAKE_MP3_B64) == _FAKE_MP3
    # base64 折行空白容错
    spaced = _FAKE_MP3_B64[:40] + "\n" + _FAKE_MP3_B64[40:]
    assert decode_audio_field(spaced) == _FAKE_MP3
    # 反例
    for bad, kw in [("", "空音频"), ("!!!not-base64!!!", "合法 base64"),
                    (base64.b64encode(b"not audio at all").decode(), "不是 MP3")]:
        with pytest.raises(AudioError) as ei:
            decode_audio_field(bad)
        assert kw in str(ei.value)


# ---------------- 3. 失败路径全显式 ----------------
def test_speak_requires_key():
    with pytest.raises(AudioError) as ei:
        _run(SeedAudioClient(api_key="").speak("你好"))
    assert "SEED_TTS_API_KEY" in str(ei.value)


def test_speak_rejects_empty_and_too_long(monkeypatch):
    monkeypatch.setattr(Config, "SEED_TTS_MAX_CHARS", 10)
    with pytest.raises(AudioError, match="不能为空"):
        _run(_client(lambda r: httpx.Response(200)).speak("   "))
    with pytest.raises(AudioError) as ei:
        _run(_client(lambda r: httpx.Response(200)).speak("字" * 11))
    assert "太长" in str(ei.value) and "10" in str(ei.value)


@pytest.mark.parametrize("resp,mk", [
    (httpx.Response(401, text="unauthorized"), "HTTP 401"),
    (httpx.Response(503, text="busy"), "HTTP 503"),
    (httpx.Response(200, text="<html>gateway</html>"), "不是 JSON"),
    (httpx.Response(200, json=["not", "object"]), "顶层不是对象"),
    (httpx.Response(200, json={"code": 3001, "message": "bad"}), "缺 audio"),
    (httpx.Response(200, json={"audio": "!!!bad!!!"}), "合法 base64"),
    (httpx.Response(200, json={"audio": ""}), "空音频"),
    (httpx.Response(200, json={"audio": base64.b64encode(b"xyz").decode()}), "不是 MP3"),
])
def test_speak_explicit_errors(resp, mk):
    with pytest.raises(AudioError) as ei:
        _run(_client(lambda r, resp=resp: resp).speak("你好"))
    assert mk in str(ei.value)


def test_speak_timeout_explicit():
    def slow(request):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(AudioError) as ei:
        _run(_client(slow).speak("你好"))
    assert "超时" in str(ei.value)


# ---------------- 4. provider 路由 ----------------
def _manager(seed_client, openai_client):
    from src.plugins.qq_bridge.multimodal.manager import MediaManager
    from src.plugins.qq_bridge.multimodal.album import MediaAlbum, media_album
    from src.plugins.qq_bridge.multimodal.vision import vision_client
    return MediaManager(vision=vision_client, audio=openai_client,
                        seed_audio=seed_client, album=media_album)


class _StubAudio:
    def __init__(self, out=b"OPENAI_AUDIO"):
        self.out = out
        self.called = 0

    async def speak(self, text):
        self.called += 1
        return self.out


def test_route_auto_prefers_seed_then_openai_then_errors(monkeypatch):
    seed, openai = _StubAudio(b"SEED"), _StubAudio(b"OPENAI")
    mgr = _manager(seed, openai)

    # auto + 两者都配 → seed 优先
    monkeypatch.setattr(Config, "TTS_PROVIDER", "auto")
    monkeypatch.setattr(Config, "SEED_TTS_API_KEY", "k")
    monkeypatch.setattr(Config, "AI_TTS_MODEL", "tts-1")
    assert _run(mgr.speak("hi")) == b"SEED" and seed.called == 1

    # auto + 只有 openai → openai 兜底
    monkeypatch.setattr(Config, "SEED_TTS_API_KEY", "")
    assert _run(mgr.speak("hi")) == b"OPENAI" and openai.called == 1

    # auto + 都没有 → 显式报错（诚实契约，不兜底）
    monkeypatch.setattr(Config, "AI_TTS_MODEL", "")
    with pytest.raises(AudioError) as ei:
        _run(mgr.speak("hi"))
    assert "没配任何语音合成" in str(ei.value)


def test_route_force_seed_or_openai(monkeypatch):
    seed, openai = _StubAudio(b"SEED"), _StubAudio(b"OPENAI")
    mgr = _manager(seed, openai)
    monkeypatch.setattr(Config, "SEED_TTS_API_KEY", "k")
    monkeypatch.setattr(Config, "AI_TTS_MODEL", "tts-1")

    monkeypatch.setattr(Config, "TTS_PROVIDER", "seed")
    assert _run(mgr.speak("hi")) == b"SEED"
    monkeypatch.setattr(Config, "TTS_PROVIDER", "openai")
    assert _run(mgr.speak("hi")) == b"OPENAI"


def test_capabilities_reflects_seed_without_leaking_key(monkeypatch):
    mgr = _manager(_StubAudio(), _StubAudio())
    monkeypatch.setattr(Config, "TTS_PROVIDER", "auto")
    monkeypatch.setattr(Config, "SEED_TTS_API_KEY", "SUPER_SECRET_VALUE")
    monkeypatch.setattr(Config, "AI_TTS_MODEL", "")
    caps = mgr.capabilities()
    assert caps["tts"] is True and caps["seed_tts"] is True
    assert caps["openai_tts"] is False and caps["tts_route"] == "seed"
    assert "SUPER_SECRET_VALUE" not in json.dumps(caps), "capabilities 绝不暴露 key"


# ---------------- 5. 端到端 send_record ----------------
def test_e2e_seed_audio_to_send_record(monkeypatch):
    """seed 客户端输出 → send_record → CQ:record base64 → 还原逐字节一致。"""
    import src.plugins.qq_bridge as M

    monkeypatch.setattr(Config, "REPLY_DELAY_MIN", 0.0)
    monkeypatch.setattr(Config, "REPLY_DELAY_MAX", 0.0)
    calls = []

    async def fake_call(action, params=None, timeout=30.0):
        calls.append((action, dict(params or {})))
        return {"message_id": 1}

    monkeypatch.setattr(M.connector, "call_action", fake_call)
    monkeypatch.setattr(M.connector, "first_bot", lambda: None)
    monkeypatch.setattr(M.history, "record_outgoing", lambda *a, **k: None)

    seen = {}
    client = _client(_ok_handler(seen))
    audio = _run(client.speak("主人你好"))
    _run(M.send_record({"user_id": 1, "group_id": 0}, audio))

    assert calls and calls[0][0] == "send_private_msg"
    msg = calls[0][1]["message"]
    assert "[CQ:record,file=base64://SUQz" in msg
    restored = base64.b64decode(msg.split("file=base64://")[1].split(",")[0].rstrip("]"))
    assert restored == audio == _FAKE_MP3
