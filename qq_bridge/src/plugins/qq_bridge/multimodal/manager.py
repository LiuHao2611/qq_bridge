# -*- coding: utf-8 -*-
"""media.manager —— 多模态门面（M4，2026-10-20）。

三件事：
  1. look(path_or_url, prompt) → 看图说话（视觉模型），并把描述回填到
     媒体档案（album）供后续历史召回；
  2. listen(audio_path) → 语音听写（ASR）→ 文本；
  3. say(text) → 语音合成（TTS）→ 音频 bytes（调用方发语音消息）。

能力口径（诚实，禁止兜底降级）：
  - 视觉/ASR/TTS 各自**独立配置**；没配哪一项 → 该项显式告知缺什么，
    **不拿别的模型凑**（文本模型看不了图，本地没装 ASR/TTS 库也不能
    凭空听/说——硬凑就是次品路径）。

本模块不 import nonebot / 不 import 主包：可裸单测（组件可注入）。
"""
from __future__ import annotations

from typing import Any

from ..config import Config, get_logger, jlog
from .album import AlbumError, MediaAlbum, media_album as _default_album
from .audio import AudioClient, AudioError, audio_client as _default_audio
from .audio_seed import SeedAudioClient, seed_audio_client as _default_seed_audio
from .vision import VisionClient, VisionError, vision_client as _default_vision

log = get_logger()


class MediaManager:
    """多模态门面（单例见 media_manager）。"""

    def __init__(self, vision: VisionClient | None = None,
                 audio: AudioClient | None = None,
                 album: MediaAlbum | None = None,
                 seed_audio: SeedAudioClient | None = None) -> None:
        self.vision = vision or _default_vision
        self.audio = audio or _default_audio
        self.seed_audio = seed_audio or _default_seed_audio
        self.album = album or _default_album

    # ---------------- 看图 ----------------
    async def look(self, image, prompt: str = "描述这张图里有什么",
                   scope: str = "", user_id: int = 0,
                   remember: bool = True, ref: str = "") -> str:
        """看图说话（失败显式上抛，由调用方转成用户可见文案）。"""
        desc = await self.vision.describe(image, prompt)
        if remember and scope:
            try:
                if ref:
                    item = self.album.add(scope, ref, kind="image",
                                          user_id=user_id, caption=desc)
                else:
                    # 没给 ref（如 bytes）→ 只记描述不记引用
                    item = self.album.add(scope, "(inline)", kind="image",
                                          user_id=user_id, caption=desc)
                log.info(jlog("media_looked", scope=scope, aid=item["id"],
                              out_len=len(desc)))
            except AlbumError as e:
                log.warning(jlog("media_album_add_failed", detail=str(e)[:160]))
        return desc

    async def look_video(self, video_path: str,
                         prompt: str = "描述这段视频的画面内容",
                         scope: str = "", user_id: int = 0) -> str:
        desc = await self.vision.describe_video(video_path, prompt)
        if scope:
            try:
                self.album.add(scope, video_path, kind="video",
                               user_id=user_id, caption=desc)
            except AlbumError as e:
                log.warning(jlog("media_album_add_failed", detail=str(e)[:160]))
        return desc

    # ---------------- 听 / 说 ----------------
    async def listen(self, audio_path: str) -> str:
        """语音 → 文字（ASR）。"""
        return await self.audio.transcribe(audio_path)

    def _tts_route(self) -> str:
        """按配置决定本次 TTS 走哪家，返回 "seed" / "openai"。

        provider 三态（config.Config.TTS_PROVIDER）：
          · "seed"   —— 强制豆包生成式；没配 SEED_TTS_API_KEY 时由其显式报错；
          · "openai" —— 强制火山方舟 OpenAI 兼容（旧行为）；
          · "auto"   —— 谁配齐用谁：有 seed key 优先 seed，否则 openai 有模型
                        就用 openai，都没有则显式报错告知两项怎么配（诚实契约，
                        不静默回落、更不本地兜底）。
        """
        provider = str(getattr(Config, "TTS_PROVIDER", "") or "auto").strip().lower()
        has_seed = bool(str(getattr(Config, "SEED_TTS_API_KEY", "") or "").strip())
        has_openai = bool(str(getattr(Config, "AI_TTS_MODEL", "") or "").strip())
        if provider == "seed":
            return "seed"
        if provider == "openai":
            return "openai"
        # auto：seed 优先（生成式音质更好、且用户新接入），否则 openai
        if has_seed:
            return "seed"
        if has_openai:
            return "openai"
        raise AudioError(
            "没配任何语音合成：填 SEED_TTS_API_KEY 用豆包 seed-audio（生成式，"
            "推荐），或填 AI_TTS_MODEL 走火山方舟 OpenAI 兼容 /audio/speech；"
            "两者都不配就不会说话（不本地兜底，硬凑是次品路径）")

    async def speak(self, text: str) -> bytes:
        """文字 → 语音 bytes（TTS，按 TTS_PROVIDER 路由）。"""
        route = self._tts_route()
        if route == "seed":
            return await self.seed_audio.speak(text)
        return await self.audio.speak(text)

    # ---------------- 历史召回 ----------------
    def recall(self, scope: str, n: int | None = None,
               user_id: int | None = None) -> Any:
        """历史图：n 为空 → 列表文本；给 n → 取第 n 条（1=最新）。"""
        if n is None:
            return self.album.format_list(scope, limit=10, user_id=user_id)
        item = self.album.pick(scope, int(n), user_id=user_id)
        if item is None:
            raise AlbumError("没有第 %s 张图（/看图 列表 看看有几张）" % n)
        return item

    # ---------------- 能力探测（给 /看图 状态用） ----------------
    def capabilities(self) -> dict:
        """当前可用能力（诚实：没配就是没配，不假装能）。"""
        def _flag(val: str) -> bool:
            return bool(str(val or "").strip())

        has_seed = _flag(getattr(Config, "SEED_TTS_API_KEY", ""))
        has_openai = _flag(getattr(Config, "AI_TTS_MODEL", ""))
        # tts=True 表示至少有一家可用；route 反映 auto 实际会走哪家（诚实可观测）
        try:
            route = self._tts_route()
        except AudioError:
            route = "none"
        return {
            "vision": _flag(getattr(Config, "AI_VISION_MODEL", "")),
            "asr": _flag(getattr(Config, "AI_ASR_MODEL", "")),
            "tts": has_seed or has_openai,
            "api_key": _flag(getattr(Config, "AI_API_KEY", "")),
            # 新增：seed 能力 + 路由结果，供 /看图 状态 展示，不暴露 key 本身
            "seed_tts": has_seed,
            "openai_tts": has_openai,
            "tts_route": route,
        }


#: 模块级单例（插件入口直接用；单测各自 new 隔离）
media_manager = MediaManager()
