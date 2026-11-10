# -*- coding: utf-8 -*-
"""multimodal —— M4 多模态 + 语音（2026-10-20）。

需求（dev_plan M4）：图理解 / 历史图召回 / 主人私聊语音。范式参考
personification（图片输入 / GIF 采样理解 / 视觉 fallback / 可选视频
理解 / 语音）。

**为什么独立成包不放 media/ 下**：既有 `media/` 是媒体**发送**层
（send_text/send_image/send_file…），其 `__init__.py` import nonebot 与
connector；本包是媒体**理解**层，必须保持「不 import nonebot、可裸单测」
的约定（与 tools/download/ops/memory/emotion 同款）。同域不同层，分家。

组成：
  vision.py   视觉理解：多模态消息（text + image_url data URI）、
              视频抽帧理解（本机 ffmpeg 8.1.2 实测可用）；视觉模型
              **必须显式配置**——不回落文本模型（看不了图，回落即降级）
  audio.py    语音：ASR（ffmpeg 转 mp3 → OpenAI 兼容 /audio/transcriptions）
              + TTS（/audio/speech）；未配置显式告知缺什么，不本地兜底
              （本机未装 faster-whisper / edge-tts，硬凑就是次品路径）
  audio_seed.py 豆包 seed-audio 生成式 TTS（openspeech 私有协议 /api/v3/tts/create，
              X-Api-Key + text_prompt 描述情绪/口音/BGM → JSON 内嵌 base64 MP3）；
              与 audio.py 两条独立产品线，复用 AudioError，由 manager 按 provider 路由
  album.py    会话媒体档案（收发过的图/视频/语音 + 视觉描述回填），
              历史图召回（与 gallery.py 的 t2i 相册分工不重叠）
  manager.py  门面：look / look_video / listen / speak / recall /
              capabilities（能力探测诚实：没配就是没配）

纪律：CLOSE-05 显式超时；OPS-01 临时文件 try/finally 释放；全失败路径
显式（VisionError / AudioError / AlbumError）；不 import nonebot /
不 import 主包：可裸单测。
"""
from .vision import VisionClient, VisionError, image_to_data_uri, vision_client
from .audio import AudioClient, AudioError, audio_client
from .audio_seed import (DEFAULT_VOICE_DESC as SEED_DEFAULT_VOICE_DESC,
                         SeedAudioClient, decode_audio_field, seed_audio_client)
from .album import AlbumError, MediaAlbum, media_album
from .manager import MediaManager, media_manager

__all__ = [
    "VisionClient", "VisionError", "vision_client", "image_to_data_uri",
    "AudioClient", "AudioError", "audio_client",
    "SeedAudioClient", "seed_audio_client", "decode_audio_field",
    "SEED_DEFAULT_VOICE_DESC",
    "MediaAlbum", "AlbumError", "media_album",
    "MediaManager", "media_manager",
]
