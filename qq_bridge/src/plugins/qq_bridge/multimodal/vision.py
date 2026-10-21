# -*- coding: utf-8 -*-
"""media.vision —— 视觉理解（M4 多模态，2026-10-20）。

需求（dev_plan M4）：图理解 / 历史图召回 / 主人私聊语音。范式参考
personification（图片输入 / GIF 采样理解 / 视觉 fallback / 可选视频理解）。

设计：
  - 视觉消息走 OpenAI 兼容多模态协议：content 数组 [{type:text},
    {type:image_url, image_url:{url: data:image/...;base64,...}}]；
  - **vision 模型必须显式配置**（AI_VISION_MODEL）——不配就如实告知，
    **不回落到文本模型**（文本模型看不了图，回落 = 降级，违反纪律）；
  - 图片来源：本地文件 / http(s) URL / 已落的 bytes；统一转 data URI
    （服务端无需回连本机，最稳）；
  - 视频理解：抽帧（ffmpeg）后按图片理解——本机 ffmpeg 8.1.2 实测可用，
    抽 1~N 帧（AI_VISION_FRAMES 可配，默认 3 帧）。

纪律：CLOSE-05 显式超时；失败显式 VisionError（含缺什么、怎么补）；
不 import nonebot / 不 import 主包：可裸单测（LLM 客户端可注入）。
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any

from ..config import Config, get_logger, jlog

log = get_logger()

#: 支持的图片后缀（其余显式拒绝，不猜类型）
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
#: 单张图片上限（MB）：超过不送模型（base64 会撑爆请求体）
MAX_IMAGE_MB = 8


class VisionError(Exception):
    """显式化的视觉理解失败。"""


def _resolve_ffmpeg() -> str:
    """ffmpeg 可执行文件（build/ffmpeg.exe 优先，回落 PATH）。"""
    here = Path(__file__).resolve().parents[3]  # qq_bridge 根
    cand = here / "build" / "ffmpeg.exe"
    if cand.exists():
        return str(cand)
    return "ffmpeg"


def image_to_data_uri(path_or_bytes, mime: str | None = None) -> str:
    """本地文件/bytes → data URI（模型侧无需回连本机）。

    超限（>MAX_IMAGE_MB）显式 VisionError；非图片后缀显式拒绝。
    """
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
        mt = mime or "image/jpeg"
    else:
        p = Path(path_or_bytes)
        if not p.exists():
            raise VisionError("图片不存在：%s" % p)
        if p.suffix.lower() not in IMAGE_EXTS:
            raise VisionError("不是支持的图片格式（%s）——支持 %s"
                              % (p.suffix, "/".join(IMAGE_EXTS)))
        raw = p.read_bytes()
        mt = mime or (mimetypes.guess_type(p.name)[0] or "image/jpeg")
    if len(raw) > MAX_IMAGE_MB * 1048576:
        raise VisionError("图片太大（%.1f MB > %d MB），本喵送不动"
                          % (len(raw) / 1048576, MAX_IMAGE_MB))
    return "data:%s;base64,%s" % (mt, base64.b64encode(raw).decode("ascii"))


class VisionClient:
    """视觉理解客户端（视觉模型 + 多模态消息）。"""

    def __init__(self, client: Any = None, model: str | None = None) -> None:
        self._client = client
        self._model = model

    def _resolve_model(self) -> str:
        m = self._model if self._model is not None else str(
            getattr(Config, "AI_VISION_MODEL", "") or "").strip()
        if not m:
            raise VisionError(
                "没配视觉模型喵——去「机器人配置 → 常规·AI 模式 → AI_VISION_MODEL」"
                "填一个支持图片的模型（图理解必须走视觉模型，不能拿文本模型凑）")
        return m

    def _resolve_client(self):
        if self._client is not None:
            return self._client
        from ..tools.llm import LLMClient
        self._client = LLMClient()
        return self._client

    async def describe(self, image, prompt: str = "描述这张图里有什么",
                       model: str | None = None) -> str:
        """看图说话。image = 本地路径 / bytes / http(s) URL。返回描述文本。"""
        text = (prompt or "描述这张图里有什么").strip()[:500]
        m = model or self._resolve_model()
        if isinstance(image, str) and image.lower().startswith(("http://", "https://")):
            img_url = image  # 公网直链直接用（省 base64 体积）
        else:
            img_url = image_to_data_uri(image)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": img_url}},
            ],
        }]
        client = self._resolve_client()
        try:
            msg = await client.chat(messages, model=m, temperature=0.3)
        except Exception as e:  # noqa: BLE001 — LLMError 等显式上抛
            from ..tools.llm import LLMError
            detail = str(e) if isinstance(e, LLMError) else "%s" % type(e).__name__
            raise VisionError("看图失败：%s" % detail)
        content = str(msg.get("content") or "").strip()
        if not content:
            raise VisionError("视觉模型没给出描述（返回空）")
        log.info(jlog("vision_described", model=m, out_len=len(content)))
        return content

    async def extract_frames(self, video_path: str, frames: int | None = None,
                             timeout: float = 30.0) -> list[bytes]:
        """视频抽帧（ffmpeg）→ 图片 bytes 列表。失败显式 VisionError。"""
        raw = getattr(Config, "AI_VISION_FRAMES", 3)
        n = 3 if raw is None else int(frames if frames is not None else raw)
        n = max(1, min(6, n))
        p = Path(video_path)
        if not p.exists():
            raise VisionError("视频不存在：%s" % p)
        ff = _resolve_ffmpeg()
        # 均匀抽 n 帧：fps 按 1/n 秒取（短视频也能拿到帧）
        cmd = [ff, "-y", "-i", str(p), "-vf", "fps=1/%d" % max(1, n),
               "-frames:v", str(n), "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(),
                                              timeout=float(timeout))
        except asyncio.TimeoutError:
            raise VisionError("抽帧超时（%ss）" % int(timeout))
        except FileNotFoundError:
            raise VisionError("找不到 ffmpeg（%s）——抽帧需要它" % ff)
        except Exception as e:  # noqa: BLE001
            raise VisionError("抽帧失败（%s）" % type(e).__name__)
        if proc.returncode != 0 and not out:
            raise VisionError("ffmpeg 抽帧报错：%s"
                              % (err or b"").decode("utf-8", "replace")[:200])
        # image2pipe mjpeg 多帧按 JPEG SOI/EOI 切分
        shots: list[bytes] = []
        buf = out or b""
        pos = 0
        while True:
            start = buf.find(b"\xff\xd8\xff", pos)
            if start < 0:
                break
            end = buf.find(b"\xff\xd9", start)
            if end < 0:
                break
            shots.append(buf[start:end + 2])
            pos = end + 2
            if len(shots) >= n:
                break
        if not shots:
            raise VisionError("没抽出帧（视频可能损坏或 ffmpeg 不支持该编码）")
        log.info(jlog("vision_frames_extracted", path=str(p), frames=len(shots)))
        return shots

    async def describe_video(self, video_path: str,
                             prompt: str = "描述这段视频的画面内容",
                             model: str | None = None) -> str:
        """视频理解（抽帧 + 逐帧描述合并）。"""
        shots = await self.extract_frames(video_path)
        m = model or self._resolve_model()
        texts = []
        for i, shot in enumerate(shots, 1):
            try:
                t = await self.describe(shot, "（第%d/%d帧）%s"
                                        % (i, len(shots), prompt), model=m)
                texts.append(t)
            except VisionError as e:
                texts.append("（第 %d 帧看不了：%s）" % (i, str(e)[:80]))
        return "\n".join(texts)


#: 模块级单例（单测各自 new 隔离）
vision_client = VisionClient()
