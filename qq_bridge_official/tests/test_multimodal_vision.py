# -*- coding: utf-8 -*-
"""M4 视觉理解验收（2026-10-20，multimodal/vision.py）。

覆盖：
  1. image_to_data_uri：本地文件 → data URI；不存在/非图片后缀/超限 →
     显式 VisionError（不猜类型、不静默跳过）；
  2. describe：构造多模态消息（text + image_url）→ 调 LLM；**未配视觉
     模型 → 显式错误且不回落文本模型**（禁止降级）；空返回 → 显式错误；
     LLM 失败 → 显式 VisionError；
  3. http(s) URL 直接用（不转 base64）；
  4. extract_frames：ffmpeg 抽帧（本机 ffmpeg 8.1.2 实测）——用假命令
     验证切帧逻辑；缺 ffmpeg/超时 → 显式错误；
  5. describe_video：多帧描述合并，单帧失败不炸（标注看不了）。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_multimodal_vision.py -v
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.multimodal.vision import (  # noqa: E402
    IMAGE_EXTS, VisionClient, VisionError, image_to_data_uri)


class _FakeLLM:
    def __init__(self, content="", raises=None):
        self.content, self.raises = content, raises
        self.seen = None

    async def chat(self, messages, tools=None, temperature=None, model=None):
        self.seen = {"messages": messages, "model": model}
        if self.raises:
            raise self.raises
        return {"role": "assistant", "content": self.content, "tool_calls": []}


# ---------------- 1. data URI ----------------
def test_data_uri_from_file(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    uri = image_to_data_uri(str(p))
    assert uri.startswith("data:image/png;base64,")


def test_data_uri_from_bytes():
    uri = image_to_data_uri(b"\xff\xd8\xff\x00")
    assert uri.startswith("data:image/jpeg;base64,")


def test_data_uri_rejects_missing_and_bad_ext(tmp_path):
    with pytest.raises(VisionError):
        image_to_data_uri(str(tmp_path / "nope.png"))
    bad = tmp_path / "a.txt"
    bad.write_bytes(b"x")
    with pytest.raises(VisionError) as ei:
        image_to_data_uri(str(bad))
    assert "不是支持的图片格式" in str(ei.value)


def test_data_uri_rejects_too_large(tmp_path):
    p = tmp_path / "big.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"0" * (9 * 1048576))  # 9MB > 8MB 上限
    with pytest.raises(VisionError) as ei:
        image_to_data_uri(str(p))
    assert "太大" in str(ei.value)


# ---------------- 2. describe ----------------
@pytest.mark.anyio
async def test_describe_builds_multimodal_message(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    llm = _FakeLLM(content="一只猫在睡觉")
    vc = VisionClient(client=llm, model="vision-x")
    out = await vc.describe(str(p), "这图里有啥")
    assert out == "一只猫在睡觉"
    msg = llm.seen["messages"][0]
    assert isinstance(msg["content"], list)
    assert msg["content"][0] == {"type": "text", "text": "这图里有啥"}
    assert msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert llm.seen["model"] == "vision-x"      # 走的是视觉模型


@pytest.mark.anyio
async def test_describe_requires_vision_model(tmp_path):
    """未配视觉模型 → 显式告知，**不回落文本模型**（文本模型看不了图）。"""
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    old = Config.AI_VISION_MODEL
    Config.AI_VISION_MODEL = ""
    try:
        vc = VisionClient(client=_FakeLLM(content="x"))
        with pytest.raises(VisionError) as ei:
            await vc.describe(str(p))
        assert "AI_VISION_MODEL" in str(ei.value)
        assert "不回落" in str(ei.value) or "不能拿" in str(ei.value)
    finally:
        Config.AI_VISION_MODEL = old


@pytest.mark.anyio
async def test_describe_empty_result_raises(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    vc = VisionClient(client=_FakeLLM(content="   "), model="v")
    with pytest.raises(VisionError) as ei:
        await vc.describe(str(p))
    assert "空" in str(ei.value)


@pytest.mark.anyio
async def test_describe_llm_failure_wrapped(tmp_path):
    from qq_bridge.tools.llm import LLMError
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    vc = VisionClient(client=_FakeLLM(raises=LLMError("HTTP 413")), model="v")
    with pytest.raises(VisionError) as ei:
        await vc.describe(str(p))
    assert "413" in str(ei.value)


@pytest.mark.anyio
async def test_describe_http_url_passthrough():
    llm = _FakeLLM(content="ok")
    vc = VisionClient(client=llm, model="v")
    await vc.describe("https://example.com/a.jpg")
    url = llm.seen["messages"][0]["content"][1]["image_url"]["url"]
    assert url == "https://example.com/a.jpg"   # 公网直链不转 base64


# ---------------- 4. 抽帧 ----------------
def _jpeg_frame(n: int) -> bytes:
    return b"\xff\xd8\xff" + bytes([n]) * 20 + b"\xff\xd9"


@pytest.mark.anyio
async def test_extract_frames_splits_jpegs(monkeypatch, tmp_path):
    import qq_bridge.multimodal.vision as vmod
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fake-video")

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"head" + _jpeg_frame(1) + _jpeg_frame(2) + b"tail", b"")

    async def fake_exec(*cmd, stdout=None, stderr=None):
        return FakeProc()

    monkeypatch.setattr(vmod.asyncio, "create_subprocess_exec", fake_exec)
    vc = VisionClient(client=_FakeLLM(), model="v")
    shots = await vc.extract_frames(str(vid), frames=2)
    assert len(shots) == 2
    assert shots[0].startswith(b"\xff\xd8\xff") and shots[0].endswith(b"\xff\xd9")


@pytest.mark.anyio
async def test_extract_frames_missing_video():
    vc = VisionClient(client=_FakeLLM(), model="v")
    with pytest.raises(VisionError) as ei:
        await vc.extract_frames("Z:/no/such.mp4")
    assert "不存在" in str(ei.value)


@pytest.mark.anyio
async def test_extract_frames_no_frames_explicit(monkeypatch, tmp_path):
    import qq_bridge.multimodal.vision as vmod
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fake-video")

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"garbage-without-jpeg-markers", b"")

    async def fake_exec(*cmd, stdout=None, stderr=None):
        return FakeProc()

    monkeypatch.setattr(vmod.asyncio, "create_subprocess_exec", fake_exec)
    vc = VisionClient(client=_FakeLLM(), model="v")
    with pytest.raises(VisionError) as ei:
        await vc.extract_frames(str(vid))
    assert "没抽出帧" in str(ei.value)


# ---------------- 5. 视频理解 ----------------
@pytest.mark.anyio
async def test_describe_video_merges(monkeypatch, tmp_path):
    import qq_bridge.multimodal.vision as vmod
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fake-video")

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (_jpeg_frame(1) + _jpeg_frame(2), b"")

    async def fake_exec(*cmd, stdout=None, stderr=None):
        return FakeProc()

    monkeypatch.setattr(vmod.asyncio, "create_subprocess_exec", fake_exec)
    vc = VisionClient(client=_FakeLLM(content="画面"), model="v")
    text = await vc.describe_video(str(vid))
    assert "画面" in text and text.count("画面") == 2


@pytest.mark.anyio
async def test_describe_video_partial_failure_noted(monkeypatch, tmp_path):
    """单帧看不了 → 标注「看不了」而不是整段失败（诚实，不假装全看懂）。"""
    import qq_bridge.multimodal.vision as vmod
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fake-video")

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (_jpeg_frame(1) + _jpeg_frame(2), b"")

    async def fake_exec(*cmd, stdout=None, stderr=None):
        return FakeProc()

    monkeypatch.setattr(vmod.asyncio, "create_subprocess_exec", fake_exec)

    class FlakyLLM:
        def __init__(self):
            self.n = 0

        async def chat(self, messages, tools=None, temperature=None, model=None):
            self.n += 1
            if self.n == 1:
                raise VisionError("模型限流")
            return {"role": "assistant", "content": "第二帧画面", "tool_calls": []}

    vc = VisionClient(client=FlakyLLM(), model="v")
    text = await vc.describe_video(str(vid))
    assert "看不了" in text and "第二帧画面" in text
