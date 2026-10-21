# -*- coding: utf-8 -*-
"""media.audio —— 语音听写(ASR) 与 语音合成(TTS)（M4，2026-10-20）。

需求（dev_plan M4）：主人私聊语音 —— 语音消息听懂 + （可选）用语音回复。

实现取舍（个人单机、本机实测 ffmpeg 8.1.2 可用、本地无 ASR/TTS 库）：
  - ASR：QQ 语音是 silk/amr 编码 → ffmpeg 转 mp3 → POST 到 OpenAI 兼容
    `/audio/transcriptions`（Whisper 兼容端点，模型名 AI_ASR_MODEL）；
  - TTS：POST `/audio/speech`（OpenAI TTS 兼容，模型 AI_TTS_MODEL +
    音色 AI_TTS_VOICE）→ 音频 bytes → QQ 语音消息；
  - **未配置对应模型时诚实告知缺什么**，不本地兜底（本地没装
    faster-whisper/edge-tts，硬凑就是次品路径——禁止降级）。

纪律：CLOSE-05 显式超时（AI_ASR_TIMEOUT / AI_TTS_TIMEOUT）；
失败显式 AudioError（含排查指引）；临时文件 try/finally 释放（OPS-01）；
不 import nonebot / 不 import 主包：可裸单测（httpx transport 可注入）。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from ..config import Config, get_logger, jlog

log = get_logger()

#: QQ 语音常见编码 → ffmpeg 输入格式提示（转码统一输出 mp3）
SUPPORTED_AUDIO_EXTS = (".amr", ".silk", ".mp3", ".wav", ".m4a", ".ogg",
                        ".opus", ".flac", ".aac")


class AudioError(Exception):
    """显式化的语音处理失败。"""


def _resolve_ffmpeg() -> str:
    here = Path(__file__).resolve().parents[3]  # qq_bridge 根
    cand = here / "build" / "ffmpeg.exe"
    if cand.exists():
        return str(cand)
    return "ffmpeg"


def _base() -> str:
    return str(getattr(Config, "AI_API_BASE", "") or "").rstrip("/")


class AudioClient:
    """ASR/TTS 客户端（OpenAI 兼容端点）。"""

    def __init__(self, api_base: str | None = None, api_key: str | None = None,
                 transport: Any = None) -> None:
        self.api_base = (api_base if api_base is not None else _base()).rstrip("/")
        self.api_key = api_key if api_key is not None else str(
            getattr(Config, "AI_API_KEY", "") or "")
        self._transport = transport

    def _require(self, kind: str) -> tuple[str, str]:
        """返回 (endpoint, model)；未配置显式报错（缺什么说什么）。"""
        if not self.api_key:
            raise AudioError("没配 AI 的 API Key（AI_API_KEY）——语音功能也要走它")
        if not self.api_base:
            raise AudioError("没配 API 地址（AI_API_BASE）")
        if kind == "asr":
            m = str(getattr(Config, "AI_ASR_MODEL", "") or "").strip()
            if not m:
                raise AudioError(
                    "没配语音识别模型（AI_ASR_MODEL）——填个 Whisper 兼容模型名"
                    "（如 whisper-1）。本机没装本地 ASR，不能凭空听懂语音")
            return self.api_base + "/audio/transcriptions", m
        m = str(getattr(Config, "AI_TTS_MODEL", "") or "").strip()
        if not m:
            raise AudioError(
                "没配语音合成模型（AI_TTS_MODEL）——填个 TTS 兼容模型名"
                "（如 tts-1）。本机没装本地 TTS，不能凭空说话")
        return self.api_base + "/audio/speech", m

    async def transcode(self, src: str, timeout: float = 30.0) -> str:
        """任意音频 → mp3（临时文件，调用方用完删）。失败显式 AudioError。"""
        p = Path(src)
        if not p.exists():
            raise AudioError("音频不存在：%s" % p)
        if p.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
            raise AudioError("不支持的音频格式（%s）——支持 %s"
                             % (p.suffix, "/".join(SUPPORTED_AUDIO_EXTS)))
        fd, dst = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        ff = _resolve_ffmpeg()
        cmd = [ff, "-y", "-i", str(p), "-ar", "16000", "-ac", "1",
               "-f", "mp3", dst]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            _out, err = await asyncio.wait_for(proc.communicate(),
                                               timeout=float(timeout))
        except asyncio.TimeoutError:
            Path(dst).unlink(missing_ok=True)
            raise AudioError("音频转码超时（%ss）" % int(timeout))
        except FileNotFoundError:
            Path(dst).unlink(missing_ok=True)
            raise AudioError("找不到 ffmpeg（%s）——语音转码需要它" % ff)
        except Exception as e:  # noqa: BLE001
            Path(dst).unlink(missing_ok=True)
            raise AudioError("音频转码失败（%s）" % type(e).__name__)
        if proc.returncode != 0 or not Path(dst).stat().st_size:
            Path(dst).unlink(missing_ok=True)
            raise AudioError("ffmpeg 转码报错：%s"
                             % (err or b"").decode("utf-8", "replace")[:200])
        log.info(jlog("audio_transcoded", src=str(p), dst=dst))
        return dst

    async def transcribe(self, audio_path: str, timeout: float | None = None) -> str:
        """语音 → 文字（先转码 mp3 再 POST）。返回转写文本。"""
        raw = getattr(Config, "AI_ASR_TIMEOUT", 60.0)
        tmo = 60.0 if raw is None else float(timeout if timeout is not None else raw)
        endpoint, model = self._require("asr")
        mp3 = await self.transcode(audio_path)
        try:
            files = {"file": (os.path.basename(mp3),
                              Path(mp3).read_bytes(), "audio/mpeg")}
            data = {"model": model}
            headers = {"Authorization": "Bearer %s" % self.api_key}
            async with httpx.AsyncClient(
                    timeout=httpx.Timeout(tmo), trust_env=False,
                    transport=self._transport) as cli:
                resp = await cli.post(endpoint, files=files, data=data,
                                      headers=headers)
        except httpx.TimeoutException:
            raise AudioError("语音识别超时（%ss）" % int(tmo))
        except Exception as e:  # noqa: BLE001 — CLOSE-02
            raise AudioError("语音识别请求失败（%s）" % type(e).__name__)
        finally:
            Path(mp3).unlink(missing_ok=True)  # OPS-01 临时文件必释放
        if resp.status_code >= 400:
            raise AudioError("语音识别接口报错 HTTP %d：%s"
                             % (resp.status_code, resp.text[:160]))
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            raise AudioError("语音识别返回不是 JSON")
        text = str((body or {}).get("text") or "").strip()
        if not text:
            raise AudioError("没听出来（识别结果为空）")
        log.info(jlog("audio_transcribed", model=model, text_len=len(text)))
        return text

    async def speak(self, text: str, timeout: float | None = None) -> bytes:
        """文字 → 语音 bytes（mp3）。失败显式 AudioError。"""
        t = (text or "").strip()
        if not t:
            raise AudioError("要说的文字不能为空")
        if len(t) > int(getattr(Config, "AI_TTS_MAX_CHARS", 500) or 500):
            raise AudioError("文字太长（%d 字）——语音合成有长度上限，"
                             "发文字更合适" % len(t))
        raw = getattr(Config, "AI_TTS_TIMEOUT", 60.0)
        tmo = 60.0 if raw is None else float(timeout if timeout is not None else raw)
        endpoint, model = self._require("tts")
        payload = {
            "model": model,
            "input": t,
            "voice": str(getattr(Config, "AI_TTS_VOICE", "") or "alloy"),
        }
        headers = {"Authorization": "Bearer %s" % self.api_key,
                   "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(
                    timeout=httpx.Timeout(tmo), trust_env=False,
                    transport=self._transport) as cli:
                resp = await cli.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise AudioError("语音合成超时（%ss）" % int(tmo))
        except Exception as e:  # noqa: BLE001
            raise AudioError("语音合成请求失败（%s）" % type(e).__name__)
        if resp.status_code >= 400:
            raise AudioError("语音合成接口报错 HTTP %d：%s"
                             % (resp.status_code, resp.text[:160]))
        if not resp.content:
            raise AudioError("语音合成返回空音频")
        log.info(jlog("audio_synthesized", model=model, bytes=len(resp.content)))
        return resp.content


#: 模块级单例（单测各自 new 隔离）
audio_client = AudioClient()
