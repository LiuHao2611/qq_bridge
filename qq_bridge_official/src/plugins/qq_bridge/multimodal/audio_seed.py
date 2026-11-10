# -*- coding: utf-8 -*-
"""audio_seed —— 豆包语音「种子」生成式 TTS（openspeech 私有协议，2026-11-10）。

与 audio.py（火山方舟 OpenAI 兼容 `/audio/speech`）是**两条独立产品线**，不可混用：
  · 域名/鉴权：POST https://openspeech.bytedance.com/api/v3/tts/create，头 `X-Api-Key`；
  · 请求体：`{model, text_prompt, audio_config{...}, watermark{}}`——`text_prompt`
    是**生成式描述**（可写音色/情绪/音效/BGM/方言/多角色），不是纯文本；
  · 返回：**同步** JSON（不是 create→query 异步），`audio` 字段是 base64 内嵌 MP3，
    另带 `duration`/`original_duration`/`url`（带签名直链，有 x-expires 有效期）。

为什么独立成模块而不是塞进 audio.py：协议/鉴权/返回解析全不同，硬塞会让
AudioClient 同时背两套契约、单测互相污染。独立模块 + 复用 AudioError 异常类，
让调用方（media_manager.speak）统一按 provider 路由，上层 `/看图 说` 无需改动。

纪律（与 audio.py 一致）：
  - CLOSE-05 显式超时（SEED_TTS_TIMEOUT，默认 90s：生成式 TTS 实测 7~22s）；
  - CLOSE-02 全失败路径显式 AudioError（缺 key / 缺文本 / 超字数 / HTTP 错误 /
    非法 JSON / 缺 audio / 非法 base64 / 空音频），绝不静默吞、绝不本地兜底；
  - 不 import nonebot / 不 import 主包：httpx transport 可注入，可裸单测；
  - 敏感信息：api_key 只从 Config.SEED_TTS_API_KEY（.env）读，绝不打印/log/落盘。

实测基线（2026-11-10 真 key）：单句 7.4s/59KB/24kHz，剧本 20.2s/324KB/48kHz；
返回 mp3 md5 与 CDN Etag 一致（base64 解码无损）。生成式按字符计费，故设字数上限。
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any

import httpx

from ..config import Config, get_logger, jlog
from .audio import AudioError  # 复用同一异常类：/看图 说 的 except AudioError 自动接住

log = get_logger()

#: 默认端点 / 模型（可用 Config 覆盖，便于自部署或换模型）
DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/create"
DEFAULT_MODEL = "seed-audio-1.0"
#: 默认音色描述：机器人「小喵」温柔女声。用户可在 .env 里改 SEED_TTS_VOICE_DESC。
#: 拼装口径 = voice_desc + 「」包裹的用户文本 —— 引号能降低用户文本把音色描述带偏的风险。
DEFAULT_VOICE_DESC = (
    "一名温柔亲切的年轻女性助手「小喵」，用轻快自然、略带俏皮的语气，"
    "清晰标准地普通话念出下面这句话"
)


def _endpoint() -> str:
    return str(getattr(Config, "SEED_TTS_ENDPOINT", "") or DEFAULT_ENDPOINT).rstrip("/")


def _model() -> str:
    return str(getattr(Config, "SEED_TTS_MODEL", "") or DEFAULT_MODEL).strip()


def _voice_desc() -> str:
    return str(getattr(Config, "SEED_TTS_VOICE_DESC", "") or DEFAULT_VOICE_DESC).strip()


def _max_chars() -> int:
    try:
        return max(1, int(getattr(Config, "SEED_TTS_MAX_CHARS", 300) or 300))
    except (TypeError, ValueError):
        return 300


def _timeout() -> float:
    try:
        return max(1.0, float(getattr(Config, "SEED_TTS_TIMEOUT", 90.0) or 90.0))
    except (TypeError, ValueError):
        return 90.0


def _sample_rate() -> int:
    """采样率：仅允许常见值，非法值回退 24000（生成式 TTS 24k 足够，48k 更贵）。"""
    try:
        v = int(getattr(Config, "SEED_TTS_SAMPLE_RATE", 24000) or 24000)
        return v if v in (16000, 24000, 32000, 48000) else 24000
    except (TypeError, ValueError):
        return 24000


def build_text_prompt(text: str, voice_desc: str | None = None) -> str:
    """把音色描述与用户文本拼成 seed-audio 的 text_prompt。"""
    desc = (voice_desc if voice_desc is not None else _voice_desc()).strip()
    body = (text or "").strip()
    if not desc:
        return "「%s」" % body
    return "%s：「%s」" % (desc, body)


def decode_audio_field(audio_b64: str) -> bytes:
    """解析返回的 `audio` 字段（base64 内嵌 MP3），校验 ID3/MPEG 帧头。

    独立成函数便于单测直接注反例（非法 base64 / 空 / 非音频）。
    """
    raw = str(audio_b64 or "").strip()
    if not raw:
        raise AudioError("语音合成返回空音频（audio 字段为空）")
    if raw.startswith("data:"):
        # 容错：兼容 `data:audio/mpeg;base64,XXXX` 形态
        raw = raw.split(",", 1)[-1]
    # 去掉可能的空白/换行（base64 里不允许，但接口偶有折行）
    compact = "".join(raw.split())
    try:
        data = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        raise AudioError("语音合成的 audio 字段不是合法 base64")
    if not data:
        raise AudioError("语音合成返回空音频（解码后为 0 字节）")
    is_mp3 = data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF
                                     and (data[1] & 0xE0) == 0xE0)
    if not is_mp3:
        raise AudioError("语音合成返回的不是 MP3 音频（帧头异常：%s）"
                         % data[:4].hex())
    return data


class SeedAudioClient:
    """豆包 seed-audio 生成式 TTS 客户端（与 AudioClient 同构，可注入 transport）。"""

    def __init__(self, api_key: str | None = None, endpoint: str | None = None,
                 transport: Any = None) -> None:
        # None→读 Config（生产）；显式传值→用于单测隔离。空串是合法「未配置」。
        self.api_key = (api_key if api_key is not None else
                        str(getattr(Config, "SEED_TTS_API_KEY", "") or "")).strip()
        self.endpoint = (endpoint if endpoint is not None else _endpoint()).rstrip("/")
        self._transport = transport

    def _require(self, text: str) -> tuple[str, dict, dict]:
        """校验前置条件，返回 (endpoint, payload, headers)；缺什么显式报什么。"""
        if not self.api_key:
            raise AudioError(
                "没配豆包语音密钥（SEED_TTS_API_KEY）——seed-audio 是私有协议，"
                "需要 openspeech 的 X-Api-Key；或把 TTS_PROVIDER 切回 openai 走火山方舟")
        t = (text or "").strip()
        if not t:
            raise AudioError("要说的文字不能为空")
        limit = _max_chars()
        if len(t) > limit:
            raise AudioError("文字太长（%d 字 > 上限 %d）——seed-audio 按字符计费，"
                             "发文字更合适（可调 SEED_TTS_MAX_CHARS）" % (len(t), limit))
        payload = {
            "model": _model(),
            "text_prompt": build_text_prompt(t),
            "audio_config": {
                "format": "mp3",
                "sample_rate": _sample_rate(),
                "pitch_rate": 0,
                "speech_rate": 0,
                "loudness_rate": 0,
            },
            "watermark": {},
        }
        headers = {"Content-Type": "application/json", "X-Api-Key": self.api_key}
        return self.endpoint, payload, headers

    async def speak(self, text: str, timeout: float | None = None) -> bytes:
        """文字 → 生成式语音 mp3 bytes。失败显式 AudioError（不本地兜底）。"""
        endpoint, payload, headers = self._require(text)
        tmo = _timeout() if timeout is None else max(1.0, float(timeout))
        try:
            async with httpx.AsyncClient(
                    timeout=httpx.Timeout(tmo), trust_env=False,
                    transport=self._transport) as cli:
                resp = await cli.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise AudioError("豆包语音合成超时（%ss）——生成式 TTS 较慢，"
                             "可调 SEED_TTS_TIMEOUT" % int(tmo))
        except Exception as e:  # noqa: BLE001 — CLOSE-02 显式化，不静默吞
            raise AudioError("豆包语音请求失败（%s）" % type(e).__name__)

        if resp.status_code >= 400:
            # 只回状态码 + 截断正文：正文可能含签名 URL/密钥片段，绝不整段外泄
            raise AudioError("豆包语音接口报错 HTTP %d：%s"
                             % (resp.status_code, resp.text[:120]))
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            raise AudioError("豆包语音返回不是 JSON（可能是网关拦截页）")
        if not isinstance(body, dict):
            raise AudioError("豆包语音返回 JSON 结构异常（顶层不是对象）")
        if "audio" not in body:
            raise AudioError("豆包语音返回缺 audio 字段：%s"
                             % json.dumps(body, ensure_ascii=False)[:120])
        audio = decode_audio_field(body.get("audio"))
        duration = body.get("duration")
        log.info(jlog("seed_audio_synthesized", model=payload["model"],
                      bytes=len(audio), duration=duration))
        return audio


#: 模块级单例（单测各自 new 隔离）
seed_audio_client = SeedAudioClient()
