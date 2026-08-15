# -*- coding: utf-8 -*-
"""语义理解维度（手册 M1 §2.1 / 维度 1）。

职责：文本 / 分镜 → 生成条件（正向 / 负向提示词 + 风格约束 + 标签）。
实现：由本地 Ollama（gemma 系编码）或 ComfyUI CLIP 编码器承担文本理解；
build() 结构化聚合意图标签与风格约束，并在 wf 模式真实插入 CLIPTextEncode 节点
（手册 M1：semantic 维度 build 调 CLIPTextEncode 节点编码）。
"""
import logging
from typing import Any, Dict, List, Optional

from .base import Capability
from .workflow_builder import WorkflowBuilder


class SemanticCapability(Capability):
    key = "semantic"
    name = "语义理解"
    implementer = "本地 CLIP / Ollama(gemma) 文本编码"
    description = "将文本剧本、分镜描述、风格标签翻译为生成条件（提示词与语义约束）。"

    NEGATIVE_DEFAULT = (
        "low quality, blurry, watermark, text, extra limbs, deformed hands, "
        "mutated fingers, bad anatomy, asymmetric eyes, deformed face, "
        "extra fingers, missing limbs, distorted proportions, ugly, disfigured"
    )

    def declare(self) -> Dict[str, Any]:
        return {
            "encoder": "gemma-7b / CLIP",
            "lang": "zh",
            "max_tokens": 512,
            "negative_supported": True,
        }

    def build(self, intent, wf: Optional[Any] = None) -> Any:
        tags: List[str] = list(getattr(intent, "intent_tags", []) or [])
        gp = getattr(intent, "generation_params", None)
        width = getattr(gp, "width", 512) if gp else 512
        height = getattr(gp, "height", 768) if gp else 768
        sc = getattr(intent, "style_constraints", None)
        lighting = getattr(sc, "lighting", None) if sc else None
        positive = list(tags)
        if lighting:
            positive.append(lighting)
        positive += ["high quality", "detailed", "cinematic"]
        # 角色资产锚定（铁律 1.1：锚定资产，禁止无约束自由创意）
        bound = getattr(intent, "bound_assets", []) or []
        if any(getattr(a, "asset_type", None) == "character" for a in bound):
            positive.append("character focus")
        # M5 全局风格锚点（遗漏 2 修复）：把全局风格词追加进 prompt（输入→处理→输出）
        # 输入：intent.style_anchor.{lighting, mood}
        # 处理：lighting 下划线转空格 → "warm cafe lighting"；mood 原样追加；
        #       "cinematic" 已由上文统一追入，不重复。
        # 输出：每分镜 positive_prompt 均含全局风格约束（跨分镜统一画风）。
        style_anchor = getattr(intent, "style_anchor", None) or {}
        if style_anchor:
            sa_lighting = (style_anchor.get("lighting") or "").replace("_", " ")
            sa_mood = style_anchor.get("mood") or ""
            if sa_lighting and ("%s lighting" % sa_lighting) not in positive:
                positive.append("%s lighting" % sa_lighting)
            if sa_mood and sa_mood not in positive:
                positive.append(sa_mood)
        positive_prompt = ", ".join(positive)
        result: Dict[str, Any] = {
            "positive_prompt": positive_prompt,
            "negative_prompt": self.NEGATIVE_DEFAULT,
            "intent_tags": tags,
            "style_constraints": {
                "color_temperature": getattr(sc, "color_temperature", None) if sc else None,
                "lighting": lighting,
            },
            "style_anchor": dict(style_anchor) if style_anchor else {},
            "resolution": {"width": width, "height": height},
        }
        # ---- P2 M1：视频 prompt 组装（增量注入，不破 P0 v3 既有 tag 聚合）----
        # 仅当意图具备视频上下文（运镜 / 空间站位 / 首帧锚定）时触发；纯文生图不进入。
        # 运镜片段由 camera 维度在 wf 模式注入，避免在此重复；此处增量并入
        # 动作 / 空间站位片段，并把完整六片段 prompt 存于 result["video_prompt"]
        # （build_shot_video_prompt 内部即调用 build_video_prompt，满足 §0.3 接口衔接）。
        try:
            oc = getattr(intent, "optional_controls", None) or {}
            has_video_ctx = bool(
                (isinstance(oc, dict) and oc.get("camera_motion"))
                or getattr(intent, "spatial", None)
                or getattr(intent, "first_frame_path", None)
            )
            if has_video_ctx:
                from features.video_prompt import (
                    build_shot_video_prompt, expand_action, build_spatial_fragment,
                )
                video_prompt = build_shot_video_prompt(intent)
                if video_prompt and video_prompt != "cinematic scene":
                    result["video_prompt"] = video_prompt
                    action = expand_action(intent)
                    spatial_frag = build_spatial_fragment(
                        getattr(intent, "spatial", None) or {})
                    extra = [x for x in (action, spatial_frag) if x]
                    if extra:
                        positive_prompt = (positive_prompt + "，" + "，".join(extra)).strip("，").strip()
                        result["positive_prompt"] = positive_prompt
        except Exception as e:
            logging.getLogger("capabilities.semantic").warning(
                "[SEMANTIC] P2 视频 prompt 组装失败（降级跳过）：%s", e)
        if wf is None:
            return result
        # ---- wf 模式：真实插入 CLIPTextEncode 节点（手册 M1 硬要求）----
        wf.add_node(
            "CLIPTextEncode",
            {"text": positive_prompt},
            title="语义编码·CLIPTextEncode",
        )
        return wf
