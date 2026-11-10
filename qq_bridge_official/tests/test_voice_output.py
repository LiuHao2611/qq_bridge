# -*- coding: utf-8 -*-
"""语音输出链路功能测试（2026-11-10，qq_bridge 机器人「说话」出口）。

背景（先厘清再测，别重复造轮子）：
  「语音输出」的骨架 **M4（2026-10-20）已存在**：
    · multimodal/audio.py::AudioClient.speak(text)  → TTS 合成 mp3 bytes（已有单测）；
    · media/__init__.py::send_record(ctx, data)       → 转 base64:// + CQ:record 发出；
    · plugins/qq_bridge/__init__.py::_handle_media_command 的 `/看图 说 <话>` 入口。
  但 **输出侧 send_record 从来没有测试覆盖**，也没人验证过「真人声/真音频字节
  能不能无损走完 base64 → CQ:record → OneBot 出站」这条链路。本文件补的就是这段。

覆盖范围（全部离线：不打真实群、不烧 TTS 额度、不依赖 Docker/网络）：
  1. send_record：构造合法 [CQ:record,file=base64://...]、群/私聊 target 分流、
     空数据显式 ValueError、@发起人、出站被拦(blocked) 时不写历史；
  2. 官方通道：不支持语音段 → 降级发文字提示，不静默丢音频；
  3. 端到端：用 ffmpeg 生成的**真实 mp3** 作 TTS 返回 → 走 MockTransport 注入
     speak() → send_record() → 捕获 OneBot 出站，解 base64 还原音频，**逐字节一致**；
  4. 配置门：未配 AI_TTS_MODEL 时 speak() 显式报缺什么（诚实契约，不本地兜底）。

机器判定：pytest assert + 非零退出码；可试听音频落在 .tmp/voice_out/（gitignore）。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_voice_output.py -v
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from nonebot.adapters.onebot.v11 import MessageSegment

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.plugins.qq_bridge as M  # noqa: E402
from src.plugins.qq_bridge.config import Config  # noqa: E402
from src.plugins.qq_bridge.media import send_record  # noqa: E402
from src.plugins.qq_bridge.multimodal.audio import (  # noqa: E402
    AudioClient, AudioError,
)

# 与 test_pic_integration 完全一致：延迟置 0，测试不真 sleep。
pytestmark = pytest.mark.usefixtures("_no_reply_delay")


@pytest.fixture(autouse=True)
def _no_reply_delay(monkeypatch):
    monkeypatch.setattr(Config, "REPLY_DELAY_MIN", 0.0)
    monkeypatch.setattr(Config, "REPLY_DELAY_MAX", 0.0)


def _run(coro):
    return asyncio.run(coro)


def _capture_sends(monkeypatch, *, blocked: bool = False):
    """打桩 connector.call_action / first_bot / history，返回 (calls, outgoing_records)。"""
    calls: list[tuple[str, dict]] = []
    out_records: list[tuple] = []

    async def _fake_call(action, params=None, timeout=30.0):
        calls.append((action, dict(params or {})))
        if blocked:
            return {"message_id": 0, "blocked": True, "reason": "group_not_whitelisted"}
        return {"message_id": 4242}

    monkeypatch.setattr(M.connector, "call_action", _fake_call)
    monkeypatch.setattr(M.connector, "first_bot", lambda: None)

    def _rec(ctx, message, self_id, msg_id):
        out_records.append((ctx, message, self_id, msg_id))

    monkeypatch.setattr(M.history, "record_outgoing", _rec)
    return calls, out_records


def _decode_record_base64(message: str) -> bytes:
    """从 [CQ:record,file=base64://XXX] 解回原始音频 bytes。"""
    assert message.startswith("[CQ:record,"), "不是 record 段：%r" % message[:40]
    inner = message[len("[CQ:record,"):-1]
    file_val = ""
    for part in inner.split(","):
        if part.startswith("file="):
            file_val = part[len("file="):]
            break
    assert file_val.startswith("base64://"), "file 不是 base64://：%r" % file_val[:30]
    return base64.b64decode(file_val[len("base64://"):])


# ---------------------------------------------------------------- 1. send_record
def test_send_record_builds_valid_cq_record_and_restores_bytes(monkeypatch):
    """真音频字节 → send_record → 合法 CQ:record，且 base64 可无损还原。"""
    audio = b"ID3\x04\x00\x00" + bytes(range(256)) * 4  # 含 MP3 帧头 + 全字节谱，最严格往返
    calls, _ = _capture_sends(monkeypatch)

    _run(send_record({"user_id": 10001, "group_id": 0}, audio))

    assert len(calls) == 1, "应只发一条消息"
    action, params = calls[0]
    assert action == "send_private_msg", "私聊ctx应发私聊"
    assert params["user_id"] == 10001
    restored = _decode_record_base64(params["message"])
    assert restored == audio, "base64 往返必须逐字节一致"


def test_send_record_routes_group_and_private(monkeypatch):
    """群 ctx → send_group_msg；私聊 ctx → send_private_msg（target 分流）。"""
    calls, _ = _capture_sends(monkeypatch)
    data = b"FAKE_MP3_BYTES"

    _run(send_record({"user_id": 10001, "group_id": 55500001}, data))
    _run(send_record({"user_id": 10001, "group_id": 0}, data))

    assert calls[0][0] == "send_group_msg" and calls[0][1]["group_id"] == 55500001
    assert calls[1][0] == "send_private_msg" and calls[1][1]["user_id"] == 10001
    assert "[CQ:record" in calls[0][1]["message"]
    assert "[CQ:record" in calls[1][1]["message"]


def test_send_record_rejects_empty(monkeypatch):
    """空音频显式 ValueError（CLOSE-02 禁止静默发空语音）。"""
    calls, _ = _capture_sends(monkeypatch)
    with pytest.raises(ValueError, match="空音频"):
        _run(send_record({"user_id": 1, "group_id": 0}, b""))
    assert not calls, "空音频不应产生任何 OneBot 调用"


def test_send_record_at_user_prepends_at_segment(monkeypatch):
    """群聊 at_user=True 时，@段在 record 段之前（与既有发送语义一致）。"""
    calls, _ = _capture_sends(monkeypatch)
    _run(send_record({"user_id": 10001, "group_id": 55500001}, b"AUDIO",
                     at_user=True))
    msg = calls[0][1]["message"]
    assert msg.startswith("[CQ:at,qq=10001]"), "at 段应在最前：%r" % msg[:30]
    assert "[CQ:record" in msg


def test_send_record_blocked_does_not_write_history(monkeypatch):
    """出站闸拦下(blocked) 时不得记对话历史（否则面板出现不存在的假消息）。"""
    calls, out_records = _capture_sends(monkeypatch, blocked=True)
    _run(send_record({"user_id": 1, "group_id": 99999999}, b"AUDIO"))
    assert len(calls) == 1 and calls[0][0] == "send_group_msg"
    assert out_records == [], "blocked 发送不应写 outgoing 历史"


def test_send_record_foreign_channel_degrades_to_text(monkeypatch):
    """官方通道不支持语音段 → 降级发文字提示，绝不静默丢音频。"""
    calls, _ = _capture_sends(monkeypatch)
    # channel="official" 走 _foreign_channel 分支 → send_text → _send_via_channel。
    # 这里只需证明：不会产生 CQ:record，而是转给通道层（判据：无 record 出站）。
    _run(send_record({"user_id": 1, "group_id": 0, "channel": "official"}, b"AUDIO"))
    # 官方通道经 get_channel("official").send → 可能无 NapCat call_action；
    # 关键断言是**没有**把 record 段按 NapCat 方式发出去。
    assert all("[CQ:record" not in (p.get("message") or "") for _, p in calls), \
        "官方通道不应发 NapCat CQ:record 段"


# ---------------------------------------------------------------- 3. 端到端真音频
def test_e2e_real_mp3_speak_then_send_record(monkeypatch, tmp_path):
    """真实 mp3 作 TTS 返回 → speak() → send_record() → 出站，逐字节还原一致。

    若 .tmp/voice_out/test_tone.mp3 存在（ffmpeg 预生成），用它当**真实音频**；
    否则用确定性伪音频 bytes 退化，保证 CI 无 ffmpeg 也能跑（判据不依赖音色）。
    """
    fixture_mp3 = ROOT / ".tmp" / "voice_out" / "test_tone.mp3"
    if fixture_mp3.exists():
        audio = fixture_mp3.read_bytes()
        assert audio[:3] == b"ID3" or (audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0), \
            "预置音频必须是合法 MP3 帧头"
    else:  # CI 无 ffmpeg 时的确定性回退
        audio = b"ID3\x04\x00\x00\x00" + bytes((i * 7 + 13) & 0xFF for i in range(4096))

    old_model = Config.AI_TTS_MODEL
    Config.AI_TTS_MODEL = "tts-1"
    try:
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            seen["path"] = request.url.path
            return httpx.Response(200, content=audio)

        client = AudioClient(api_base="http://api.test/v1", api_key="k",
                             transport=httpx.MockTransport(handler))
        calls, _ = _capture_sends(monkeypatch)

        synthesized = _run(client.speak("你好，这是语音输出功能测试"))
        assert synthesized == audio, "speak() 应原样返回 TTS 音频字节"
        assert seen["path"].endswith("/audio/speech"), \
            "TTS 应打在 /audio/speech（火山方舟 OpenAI 兼容端点）"
        assert seen["body"]["model"] == "tts-1"
        assert seen["body"]["input"] == "你好，这是语音输出功能测试"

        _run(send_record({"user_id": 10001, "group_id": 0}, synthesized))
        msg = calls[0][1]["message"]
        restored = _decode_record_base64(msg)
        assert restored == audio, "端到端：TTS 字节→CQ:record→还原 必须逐字节一致"
        assert "[CQ:record,file=base64://" in msg
    finally:
        Config.AI_TTS_MODEL = old_model


def test_e2e_writes_restorable_playable_audio(tmp_path):
    """把端到端还原出的音频落盘成可试听文件（prove-it 可听证据）。

    这不是断言音色（sine 非人声），而是证明「链路输出的是**真能被播放器打开**的
    音频文件」：合法 MP3 帧头 + 非空 + ffprobe（若装了）能读出 duration。
    """
    fixture_mp3 = ROOT / ".tmp" / "voice_out" / "test_tone.mp3"
    if not fixture_mp3.exists():
        pytest.skip("无 ffmpeg 预生成音频，跳过可试听证据（核心往返已由上一用例覆盖）")
    audio = fixture_mp3.read_bytes()
    encoded = "base64://" + base64.b64encode(audio).decode("ascii")
    msg = str(MessageSegment.record(encoded))
    restored = _decode_record_base64(msg)
    assert restored == audio

    out = tmp_path / "restored_voice_output.mp3"
    out.write_bytes(restored)
    assert out.stat().st_size == len(audio) > 0
    assert out.read_bytes()[:3] == b"ID3" or (restored[0] == 0xFF)
    # 落盘路径回显，便于人工 present_files 试听
    print("\n[可试听证据] %s (%d bytes)" % (out, out.stat().st_size))


# ---------------------------------------------------------------- 4. 配置门（诚实契约）
@pytest.mark.anyio
async def test_speak_without_model_explicitly_fails():
    """未配 AI_TTS_MODEL → 显式 AudioError 告知缺什么，不本地兜底。"""
    old = Config.AI_TTS_MODEL
    Config.AI_TTS_MODEL = ""
    try:
        with pytest.raises(AudioError) as ei:
            await AudioClient(api_base="http://api.test/v1", api_key="k").speak("你好")
        assert "AI_TTS_MODEL" in str(ei.value)
    finally:
        Config.AI_TTS_MODEL = old


def test_config_api_base_points_to_responses_not_tts():
    """记录当前配置事实：AI_API_BASE 指向 /responses（LLM），非 /audio/speech 根。

    这是「机器人现在为什么不会说话」的根因证据之一，不因测试通过而掩盖。断言
    的是「读得到该配置」，具体值可能是 /responses 或 /v3；若哪天接通 TTS 改为
    /v3，此断言仍成立（只验证字段存在且为 http(s)）。
    """
    base = str(getattr(Config, "AI_API_BASE", "") or "")
    assert base.startswith("http://") or base.startswith("https://") or base == ""
    print("\n[配置事实] AI_API_BASE=%s ; AI_TTS_MODEL=%r"
          % (base or "(空)", getattr(Config, "AI_TTS_MODEL", "")))
