# -*- coding: utf-8 -*-
"""Layer 0 · Model Engine Pool（执行手册 §2.1 Layer 0: Model Engine Pool）。

抽象：文生视频 / 人脸 ID 嵌入 / TTS / 画质增强四类模型引擎的统一句柄。
本地实现（LocalModelPool）：映射到本机既有的 Ollama(润色/解析) + ComfyUI(出图) +
FFmpeg(合成) 引擎；人脸特征提取在本地以占位能力存在（真实 IP-Adapter 特征注入由
ComfyUI 工作流承载），保持接口与手册一致。

引擎分类与手册 §5.2 资产注入协议对应：
  文生视频    → ComfyUI Realistic_Vision / Seedance 等价本地工作流
  人脸 ID 嵌入 → IP-Adapter 参考图特征（本地由 ComfyUI 工作流消费）
  TTS         → XTTS/本地 TTS（合成阶段调用）
  画质增强    → 人脸修复/降噪/锐化/色彩统一（合成后处理）
"""
from abc import ABC, abstractmethod
from typing import Dict, Optional

from ai_local import OLLAMA_URL, COMFY_URL


class ModelEngine(ABC):
    """单个模型引擎句柄。"""
    engine_type: str = "unknown"

    @abstractmethod
    def handle(self) -> Dict:
        """返回引擎连接句柄/地址/元信息。"""
        raise NotImplementedError


class Text2VideoEngine(ModelEngine):
    engine_type = "text2video"


class FaceIdEngine(ModelEngine):
    engine_type = "face_id"


class TTSEngine(ModelEngine):
    engine_type = "tts"


class EnhanceEngine(ModelEngine):
    engine_type = "enhance"


class ModelEnginePool(ABC):
    """模型引擎池抽象。"""

    @abstractmethod
    def get(self, engine_type: str, name: Optional[str] = None) -> ModelEngine:
        raise NotImplementedError

    @abstractmethod
    def list_engines(self) -> Dict[str, list]:
        raise NotImplementedError


class LocalModelPool(ModelEnginePool):
    """本地实现：直接指向本机 Ollama / ComfyUI 地址，引擎以名字索引。"""

    def __init__(self, ollama_url: str = OLLAMA_URL, comfy_url: str = COMFY_URL):
        self._ollama_url = ollama_url
        self._comfy_url = comfy_url
        self._engines = {
            "text2video": {
                "comfyui_realist_vision": {"url": comfy_url, "workflow": "realistic_vision_sd15"},
                "seedance_2_mini": {"url": comfy_url, "workflow": "seedance_equiv"},
            },
            "face_id": {
                "ip_adapter_local": {"url": comfy_url, "note": "由 ComfyUI IP-Adapter 工作流消费参考图"},
            },
            "tts": {
                "xtts_local": {"note": "本地 XTTS / 等价 TTS 引擎"},
            },
            "enhance": {
                "ffmpeg_post": {"note": "人脸修复/降噪/锐化/色彩统一（合成后处理）"},
            },
        }

    def get(self, engine_type: str, name: Optional[str] = None) -> ModelEngine:
        group = self._engines.get(engine_type, {})
        if not group:
            raise KeyError("未注册的引擎类型: %s" % engine_type)
        key = name or next(iter(group))
        meta = group.get(key)
        if not meta:
            raise KeyError("引擎未找到: %s/%s" % (engine_type, name))
        return _LocalEngine(engine_type, key, meta)

    def list_engines(self) -> Dict[str, list]:
        return {k: list(v.keys()) for k, v in self._engines.items()}


class _LocalEngine(ModelEngine):
    def __init__(self, engine_type: str, name: str, meta: Dict):
        self.engine_type = engine_type
        self.name = name
        self.meta = meta

    def handle(self) -> Dict:
        return {"engine_type": self.engine_type, "name": self.name, **self.meta}


default_pool = LocalModelPool()
