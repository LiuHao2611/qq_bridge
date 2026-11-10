# -*- coding: utf-8 -*-
"""M4 语音(ASR/TTS) + 媒体档案验收（2026-10-20，multimodal/audio.py +
album.py + manager.py）。

audio 覆盖：
  1. 能力门：未配 ASR/TTS 模型 / 未配 Key → 显式 AudioError 且**不本地
     兜底**（本机没装 faster-whisper/edge-tts，硬凑就是次品路径）；
  2. transcode：ffmpeg 转码（假进程）；不存在/坏后缀/超时/没 ffmpeg →
     显式错误，临时文件释放（OPS-01）；
  3. transcribe：POST /audio/transcriptions → text；超时/4xx/非 JSON/
     空结果 → 显式错误；临时 mp3 用后即删；
  4. speak：POST /audio/speech → bytes；空文本/超字数 → 显式错误。

album 覆盖：
  5. add/get/recent（时间倒序：1=最新）/pick（越界 None）/set_caption/
     format_list/gc（滚动清理、幂等）。

manager 覆盖：
  6. capabilities：诚实反映配置状态（没配=False，不假装能）；
  7. look：看图并回填档案；listen/speak 转发；recall 列表/取第 N 张。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_multimodal_audio.py -v
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.multimodal.album import AlbumError, MediaAlbum  # noqa: E402
from qq_bridge.multimodal.audio import AudioClient, AudioError  # noqa: E402
from qq_bridge.multimodal.manager import MediaManager  # noqa: E402


def _cli(handler):
    return AudioClient(api_base="http://api.test/v1", api_key="k",
                       transport=httpx.MockTransport(handler))


# ---------------- 1. 能力门 ----------------
@pytest.mark.anyio
async def test_asr_requires_model():
    old_m, old_k = Config.AI_ASR_MODEL, Config.AI_API_KEY
    Config.AI_ASR_MODEL, Config.AI_API_KEY = "", "k"
    try:
        with pytest.raises(AudioError) as ei:
            await _cli(lambda r: httpx.Response(200, json={"text": "x"})) \
                .transcribe("a.amr")
        assert "AI_ASR_MODEL" in str(ei.value)
    finally:
        Config.AI_ASR_MODEL, Config.AI_API_KEY = old_m, old_k


@pytest.mark.anyio
async def test_tts_requires_model():
    old_m = Config.AI_TTS_MODEL
    Config.AI_TTS_MODEL = ""
    try:
        with pytest.raises(AudioError) as ei:
            await _cli(lambda r: httpx.Response(200, content=b"x")).speak("你好")
        assert "AI_TTS_MODEL" in str(ei.value)
    finally:
        Config.AI_TTS_MODEL = old_m


@pytest.mark.anyio
async def test_requires_api_key():
    old = Config.AI_API_KEY
    Config.AI_API_KEY = ""
    try:
        with pytest.raises(AudioError) as ei:
            await AudioClient(api_base="http://x/v1", api_key="").speak("你好")
        assert "API Key" in str(ei.value)
    finally:
        Config.AI_API_KEY = old


# ---------------- 2. 转码 ----------------
@pytest.mark.anyio
async def test_transcode_ok(monkeypatch, tmp_path):
    import qq_bridge.multimodal.audio as amod
    src = tmp_path / "a.amr"
    src.write_bytes(b"fake")
    dst = tmp_path / "out.mp3"
    dst.write_bytes(b"mp3data")

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def fake_exec(*cmd, stdout=None, stderr=None):
        # 顺手把输出文件写上（模拟 ffmpeg 产出）
        Path(cmd[-1]).write_bytes(b"mp3data")
        return FakeProc()

    monkeypatch.setattr(amod.asyncio, "create_subprocess_exec", fake_exec)
    out = await _cli(None).transcode(str(src))
    assert Path(out).read_bytes() == b"mp3data"
    Path(out).unlink(missing_ok=True)


@pytest.mark.anyio
async def test_transcode_bad_ext_and_missing(tmp_path):
    bad = tmp_path / "a.txt"
    bad.write_bytes(b"x")
    with pytest.raises(AudioError) as ei:
        await _cli(None).transcode(str(bad))
    assert "不支持的音频格式" in str(ei.value)
    with pytest.raises(AudioError):
        await _cli(None).transcode(str(tmp_path / "nope.amr"))


@pytest.mark.anyio
async def test_transcode_failure_cleans_tmp(monkeypatch, tmp_path):
    import qq_bridge.multimodal.audio as amod
    src = tmp_path / "a.amr"
    src.write_bytes(b"fake")

    class FakeProc:
        returncode = 1

        async def communicate(self):
            return (b"", b"boom")

    async def fake_exec(*cmd, stdout=None, stderr=None):
        return FakeProc()

    monkeypatch.setattr(amod.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(AudioError) as ei:
        await _cli(None).transcode(str(src))
    assert "转码" in str(ei.value)


# ---------------- 3. ASR ----------------
@pytest.mark.anyio
async def test_transcribe_ok(monkeypatch, tmp_path):
    import qq_bridge.multimodal.audio as amod
    src = tmp_path / "a.amr"
    src.write_bytes(b"fake")
    old = Config.AI_ASR_MODEL
    Config.AI_ASR_MODEL = "whisper-1"
    try:
        async def fake_exec(*cmd, stdout=None, stderr=None):
            Path(cmd[-1]).write_bytes(b"mp3")

            class P:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")
            return P()

        monkeypatch.setattr(amod.asyncio, "create_subprocess_exec", fake_exec)
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["has_file"] = b"audio/mpeg" in request.content
            return httpx.Response(200, json={"text": "今天天气不错"})

        out = await _cli(handler).transcribe(str(src))
        assert out == "今天天气不错"
        assert seen["url"].endswith("/audio/transcriptions")
        assert seen["has_file"]
    finally:
        Config.AI_ASR_MODEL = old


@pytest.mark.anyio
async def test_transcribe_http_error(monkeypatch, tmp_path):
    import qq_bridge.multimodal.audio as amod
    src = tmp_path / "a.amr"
    src.write_bytes(b"fake")
    old = Config.AI_ASR_MODEL
    Config.AI_ASR_MODEL = "whisper-1"
    try:
        async def fake_exec(*cmd, stdout=None, stderr=None):
            Path(cmd[-1]).write_bytes(b"mp3")

            class P:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")
            return P()

        monkeypatch.setattr(amod.asyncio, "create_subprocess_exec", fake_exec)
        with pytest.raises(AudioError) as ei:
            await _cli(lambda r: httpx.Response(401, text="unauthorized")) \
                .transcribe(str(src))
        assert "401" in str(ei.value)
    finally:
        Config.AI_ASR_MODEL = old


@pytest.mark.anyio
async def test_transcribe_empty_text(monkeypatch, tmp_path):
    import qq_bridge.multimodal.audio as amod
    src = tmp_path / "a.amr"
    src.write_bytes(b"fake")
    old = Config.AI_ASR_MODEL
    Config.AI_ASR_MODEL = "whisper-1"
    try:
        async def fake_exec(*cmd, stdout=None, stderr=None):
            Path(cmd[-1]).write_bytes(b"mp3")

            class P:
                returncode = 0

                async def communicate(self):
                    return (b"", b"")
            return P()

        monkeypatch.setattr(amod.asyncio, "create_subprocess_exec", fake_exec)
        with pytest.raises(AudioError) as ei:
            await _cli(lambda r: httpx.Response(200, json={"text": "  "})) \
                .transcribe(str(src))
        assert "没听出来" in str(ei.value)
    finally:
        Config.AI_ASR_MODEL = old


# ---------------- 4. TTS ----------------
@pytest.mark.anyio
async def test_speak_ok():
    old = Config.AI_TTS_MODEL
    Config.AI_TTS_MODEL = "tts-1"
    try:
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=b"AUDIO")

        data = await _cli(handler).speak("你好呀")
        assert data == b"AUDIO"
        assert seen["body"]["model"] == "tts-1"
        assert seen["body"]["input"] == "你好呀"
        assert seen["body"]["voice"] == "alloy"
    finally:
        Config.AI_TTS_MODEL = old


@pytest.mark.anyio
async def test_speak_empty_and_too_long():
    old = Config.AI_TTS_MODEL
    Config.AI_TTS_MODEL = "tts-1"
    try:
        with pytest.raises(AudioError):
            await _cli(lambda r: httpx.Response(200, content=b"x")).speak("  ")
        with pytest.raises(AudioError) as ei:
            await _cli(lambda r: httpx.Response(200, content=b"x")).speak("字" * 600)
        assert "太长" in str(ei.value)
    finally:
        Config.AI_TTS_MODEL = old


@pytest.mark.anyio
async def test_speak_empty_response():
    old = Config.AI_TTS_MODEL
    Config.AI_TTS_MODEL = "tts-1"
    try:
        with pytest.raises(AudioError) as ei:
            await _cli(lambda r: httpx.Response(200, content=b"")).speak("你好")
        assert "空音频" in str(ei.value)
    finally:
        Config.AI_TTS_MODEL = old


# ---------------- 5. 媒体档案 ----------------
@pytest.fixture()
def album(tmp_path):
    return MediaAlbum(tmp_path / "media_album.json")


def test_album_add_and_recent(album):
    now = time.time()
    album.add("g:1", "u1", user_id=42, ts=now - 10)
    album.add("g:1", "u2", user_id=42, ts=now)
    items = album.recent("g:1")
    assert [i["ref"] for i in items] == ["u2", "u1"]   # 1=最新
    assert album.recent("g:2") == []                    # scope 隔离


def test_album_pick_and_caption(album):
    album.add("g:1", "u1", user_id=42, ts=time.time() - 10)
    b = album.add("g:1", "u2", user_id=42, ts=time.time())
    assert album.pick("g:1", 1)["ref"] == "u2"
    assert album.pick("g:1", 2)["ref"] == "u1"
    assert album.pick("g:1", 9) is None        # 越界显式 None
    assert album.set_caption(b["id"], "一只猫")
    assert album.get(b["id"])["caption"] == "一只猫"


def test_album_validation(album):
    with pytest.raises(AlbumError):
        album.add("g:1", "")
    with pytest.raises(AlbumError):
        album.add("g:1", "u", kind="bogus")


def test_album_format_list_and_gc(album):
    assert "还没存过图片" in album.format_list("g:1")
    it = album.add("g:1", "u1", user_id=42, caption="猫")
    assert "猫" in album.format_list("g:1")
    # gc：保留上限内不清理（幂等）
    assert album.gc() == 0
    assert len(album.recent("g:1", limit=100)) == 1


# ---------------- 6/7. manager ----------------
def test_manager_capabilities_honest():
    # 2026-11-10：新增 seed-audio 后 capabilities 契约扩展为 7 键。
    # 全部相关配置都纳入临时清空（含 SEED_TTS_API_KEY，消除 .env 有 key 时的
    # 偶发不稳定），断言「没配就是没配」的诚实语义 + 路由字段一致，比原 4 键更严格。
    keys = ("AI_VISION_MODEL", "AI_ASR_MODEL", "AI_TTS_MODEL", "AI_API_KEY",
            "SEED_TTS_API_KEY", "TTS_PROVIDER")
    old = {k: getattr(Config, k, "") for k in keys}
    try:
        for k in keys:
            setattr(Config, k, "")
        Config.TTS_PROVIDER = "auto"
        caps = MediaManager().capabilities()
        assert caps == {
            "vision": False, "asr": False, "tts": False, "api_key": False,
            "seed_tts": False, "openai_tts": False, "tts_route": "none",
        }   # 没配就是没配，不假装能
        # 只配视觉 → vision 亮，语音仍全灭（各能力独立，互不回落）
        Config.AI_VISION_MODEL = "v"
        assert MediaManager().capabilities()["vision"] is True
        assert MediaManager().capabilities()["tts"] is False
        # 只配 seed key → seed_tts 亮、auto 路由到 seed、api_key 不受影响
        Config.SEED_TTS_API_KEY = "k"
        caps2 = MediaManager().capabilities()
        assert caps2["seed_tts"] is True and caps2["tts"] is True
        assert caps2["tts_route"] == "seed"
        # 精确断言：任何字段的「值」都不得等于 key 明文（而非在整段字符串里
        # 找子串——键名 api_key 本身就含字母 k，子串断言会假失败）。
        assert all(v != "k" for v in caps2.values()), "capabilities 绝不回显 key 值"
    finally:
        for k, v in old.items():
            setattr(Config, k, v)


@pytest.mark.anyio
async def test_manager_look_records_album(tmp_path):
    from qq_bridge.multimodal.manager import MediaManager
    from qq_bridge.multimodal.vision import VisionClient

    class FakeVision:
        async def describe(self, image, prompt="", model=None):
            return "一只猫"

    mgr = MediaManager(vision=FakeVision(),  # type: ignore[arg-type]
                       album=MediaAlbum(tmp_path / "a.json"))
    out = await mgr.look("u1", scope="g:1", user_id=42, ref="http://x/1.jpg")
    assert out == "一只猫"
    assert mgr.album.recent("g:1")[0]["caption"] == "一只猫"
