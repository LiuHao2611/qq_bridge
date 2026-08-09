# -*- coding: utf-8 -*-
"""Layer 0 · Model Engine Pool（执行手册 §2.1 Layer 0: Model Engine Pool）。

抽象：文生视频 / 人脸 ID 嵌入 / TTS / 画质增强四类模型引擎的统一句柄。

本地实现（LocalModelPool）：**本次完善（2026-08-08）** —— 不再只是静态 URL 占位，
而是真正把引擎句柄接到本机已验证的 ComfyUI 客户端（ai_local 的 comfy_available /
_comfy_text2img / _comfy_text2img_with_ipadapter / _comfy_text2video），使 Layer0 在
ComfyUI 可用时直接出图 / 注入真人特征，不可用时优雅降级（comfy_health()=False，
generate() 抛清晰错误由上层回退），保持接口与手册一致。

引擎分类与手册 §5.2 资产注入协议对应：
  文生视频    → ComfyUI 视频工作流（Wan2.1-T2V 已接；detect_comfy_video 为 True 时生成真实运动视频，失败回退幻灯片）
  人脸 ID 嵌入 → ComfyUI IP-Adapter（参考图 → 身份特征注入，等价于 512 维特征注入的本地落地）
  TTS         → 本地 / 在线 TTS（合成阶段调用，Layer0 仅登记）
  画质增强    → 本地 ffmpeg 后处理（ai_local.synthesize_project_video 承担，Layer0 仅登记）

纪律：LOG-01 结构化日志（logging，事件 snake_case）；REDUN-01 降级（ComfyUI 不可用时
generate 抛清晰错误而非静默）；不破坏 ModelEngine / ModelEnginePool 抽象（OPS / AGENTS §08）。
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional

from ai_local import (
    OLLAMA_URL,
    comfy_available,
    _get_comfy_url,
    _comfy_text2img,
    _comfy_text2img_with_ipadapter,
    _comfy_text2video,
)

logger = logging.getLogger("infra.model_pool")


def _dims(inputs: Dict) -> Dict:
    """从调用方参数中抽取维度/随机种子/超时等可选字段。"""
    out = {}
    for k in ("width", "height", "frames", "seed", "steps", "cfg", "timeout", "weight"):
        if k in inputs and inputs[k] is not None:
            out[k] = inputs[k]
    return out


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


class _ComfyAwareEngine(ModelEngine):
    """ComfyUI 感知的引擎句柄：持有池引用，generate() 真正调用 ai_local ComfyUI 客户端。"""

    def __init__(self, engine_type: str, spec: Dict, pool: "LocalModelPool"):
        self.engine_type = engine_type
        self.name = spec.get("name")
        self.spec = spec
        self._pool = pool

    def handle(self) -> Dict:
        return {
            "engine_type": self.engine_type,
            "name": self.name,
            "kind": self.spec.get("kind"),
            "url": self._pool._comfy_url if self.spec.get("kind") == "comfy" else self._pool._ollama_url,
            "workflow": self.spec.get("workflow"),
            "note": self.spec.get("note"),
            "comfy_healthy": self._pool.comfy_health() if self.spec.get("kind") == "comfy" else None,
        }

    def _require_comfy(self):
        if not self._pool.comfy_health():
            raise RuntimeError(
                "ComfyUI 未启动（%s），无法执行 %s 生成；请先启动 ComfyUI 或回退本地降级路径"
                % (self._pool._comfy_url, self.engine_type)
            )

    def generate(self, prompt: Optional[str] = None, **inputs) -> str:
        """执行一次生成，返回输出产物本地路径（图片）。未配置/不可用时抛清晰错误。"""
        if self.engine_type == "face_id":
            self._require_comfy()
            ref = inputs.get("ref_comfy_name") or inputs.get("reference_image")
            if not ref:
                raise ValueError("face_id 引擎需要 ref_comfy_name（ComfyUI 侧参考图文件名）")
            logger.info("engine_generate", extra={"engine": "face_id", "ref": ref})
            return _comfy_text2img_with_ipadapter(prompt or "", ref, **_dims(inputs))
        if self.engine_type == "text2video":
            self._require_comfy()
            logger.info("engine_generate", extra={"engine": "text2video"})
            out = _comfy_text2video(prompt or "", **_dims(inputs))
            if out is None:
                raise RuntimeError(
                    "ComfyUI 视频工作流未配置（需 Seedance/Wan 等工作流），请回退图片轮播降级"
                )
            return out
        if self.engine_type == "enhance":
            raise RuntimeError(
                "enhance 由合成阶段本地 ffmpeg 后处理承担（ai_local.synthesize_project_video），"
                "Layer0 不单独调用 ComfyUI"
            )
        # tts
        raise RuntimeError(
            "tts 由合成阶段调用（XTTS/本地），Layer0 仅登记引擎，不单独生成"
        )


class LocalModelPool(ModelEnginePool):
    """本地实现：直接指向本机 Ollama / ComfyUI，引擎句柄真正可调 ComfyUI 客户端。

    注册表 engine_type -> [ {name, kind, callable?, workflow, note} ]。
    kind='comfy' 的引擎经 _ComfyAwareEngine 调 ai_local 的 ComfyUI 客户端；
    kind='local' 的引擎（tts/enhance）由合成阶段本地实现，Layer0 仅登记。
    """

    def __init__(self, ollama_url: str = OLLAMA_URL, comfy_url: Optional[str] = None):
        self._ollama_url = ollama_url
        self._comfy_url = comfy_url or _get_comfy_url()
        self._engines = {
            "text2video": [
                {"name": "comfyui_video", "kind": "comfy",
                 "workflow": "seedance_equiv / wan（需视频工作流）",
                 "note": "ComfyUI 视频生成，当前需显式工作流"},
            ],
            "face_id": [
                {"name": "ip_adapter_local", "kind": "comfy",
                 "workflow": "ip_adapter_sd15",
                 "note": "IP-Adapter 参考图特征注入（等价于 512 维真人特征注入本地落地）"},
            ],
            "tts": [
                {"name": "xtts_local", "kind": "local",
                 "note": "本地 XTTS / 等价 TTS 引擎（合成阶段调用）"},
            ],
            "enhance": [
                {"name": "ffmpeg_post", "kind": "local",
                 "note": "人脸修复/降噪/锐化/色彩统一（合成后处理，本地 ffmpeg）"},
            ],
        }
        logger.info("pool_init", extra={"comfy_url": self._comfy_url,
                                         "ollama_url": self._ollama_url,
                                         "comfy_healthy": self.comfy_health()})

    def comfy_health(self) -> bool:
        """REDUN-01：ComfyUI 可用性探测（ai_local.comfy_available 查 /system_stats）。"""
        try:
            return bool(comfy_available(self._comfy_url))
        except Exception as e:
            logger.warning("comfy_health_fail", extra={"url": self._comfy_url, "error": str(e)})
            return False

    def get(self, engine_type: str, name: Optional[str] = None) -> ModelEngine:
        group = self._engines.get(engine_type)
        if not group:
            raise KeyError("未注册的引擎类型: %s" % engine_type)
        spec = None
        if name:
            spec = next((g for g in group if g["name"] == name), None)
        if spec is None:
            spec = group[0]
        return _ComfyAwareEngine(engine_type, spec, self)

    def list_engines(self) -> Dict[str, list]:
        return {k: [g["name"] for g in v] for k, v in self._engines.items()}


default_pool = LocalModelPool()
