# -*- coding: utf-8 -*-
"""运镜控制维度（手册 M1 §2.1 / 维度 5）。

职责：按分镜移动镜头（推 / 拉 / 摇 / 移 / 跟 / 固定）。
实现：以提示词驱动的运镜（prompt_driven），由语义模板注入运镜描述；
build() 从分镜文本 / 可选控制推导运镜类型与强度，并在 wf 模式把运镜片段拼入
CLIPTextEncode 的 prompt 文本（手册 M1：camera 维度 build 把 prompt 拼进 semantic
维度的编码节点，随 CLIPTextEncode 一起编码）。无独立节点，真实「构建」体现在文本注入。
"""
from typing import Any, Dict, Optional

from .base import Capability
from .workflow_builder import WorkflowBuilder
# P2 M1：运镜片段统一走 features.camera_mapping（超集表 + 未知回退 static+warning）
from features.camera_mapping import (
    CAMERA_MOVEMENT_PROMPT,
    resolve_camera_fragment,
)


# 运镜关键词 → 运镜类型（用于从分镜文本推断）
_CAMERA_KEYWORDS = {
    "推": "push_in", "推进": "push_in", "拉近": "push_in",
    "拉": "pull_out", "拉远": "pull_out", "退": "pull_out",
    "摇": "pan_lr", "横移": "pan_lr", "平移": "pan_lr",
    "移": "track_lr", "跟": "track_lr", "跟随": "track_lr",
    "升": "crane_up", "降": "crane_down", "俯": "top_down", "仰": "tilt_up",
    "环绕": "orbit", "旋转": "orbit", "固定": "static", "静止": "static",
}


class CameraCapability(Capability):
    key = "camera"
    name = "运镜控制"
    implementer = "提示词驱动运镜（prompt_driven）"
    description = "按分镜语义控制镜头运动（推拉摇移跟等）。"

    # 对齐手册 P2 v2 §4.1 CAMERA_MOVEMENT_PROMPT（9 个官方运镜语义）+ 前端真实枚举别名
    # + P0 推断产生值。SUPPORTED 必须覆盖手册全部 9 值（static/push_in/pull_back/
    # pan_left/pan_right/tilt_up/tilt_down/tracking/handheld），否则 capability 声明
    # 与手册运镜语义脱节；前端别名（pan_lr/track_lr）与 P0 推断值（crane_up/crane_down/
    # follow）一并保留，确保 resolve_camera_fragment 超集全覆盖、无未知值回退 static。
    # 注：编辑器下拉源来自 src/mock.js（手册 §4 禁止#342：枚举只读不改），其键名
    # 为 pan_lr/track_lr/pull_out 等别名；二者语义经 camera_mapping 超集对齐，不丢运镜。
    SUPPORTED = [
        "static", "push_in", "pull_back", "pan_left", "pan_right",
        "tilt_up", "tilt_down", "tracking", "handheld",
        "pan_lr", "track_lr", "crane_up", "crane_down", "follow",
    ]

    def declare(self) -> Dict[str, Any]:
        return {
            "mode": "prompt_driven",
            "supported": self.SUPPORTED,
        }

    def build(self, intent, wf: Optional[Any] = None) -> Any:
        # 优先读可选控制显式指定
        opts = getattr(intent, "optional_controls", {}) or {}
        explicit = opts.get("camera_motion")
        if explicit:
            motion = explicit
        else:
            text = " ".join(getattr(intent, "intent_tags", []) or [])
            sc = getattr(intent, "style_constraints", None)
            if sc and getattr(sc, "lighting", None):
                text += " " + getattr(sc, "lighting")
            motion = "static"
            for kw, cam in _CAMERA_KEYWORDS.items():
                if kw in text:
                    motion = cam
                    break
        # 运动强度：跟随类更强，固定为 0
        strength = 0.0 if motion == "static" else (0.8 if motion in ("tracking", "track_lr", "orbit") else 0.5)
        result: Dict[str, Any] = {
            "camera_motion": motion,
            "motion_strength": strength,
            "supported": self.SUPPORTED,
        }
        if wf is None:
            return result
        # ---- wf 模式：把运镜片段拼入 CLIPTextEncode 的 prompt 文本 ----
        # P2 M1：统一用 CAMERA_MOVEMENT_PROMPT 超集（含前端枚举别名），未知→static
        fragment = resolve_camera_fragment(motion)
        if isinstance(wf, WorkflowBuilder):
            clip_id, clip_node = wf.find_node("CLIPTextEncode")
            if clip_node is not None:
                old = clip_node["inputs"].get("text") or ""
                sep = ", " if old else ""
                clip_node["inputs"]["text"] = (old + sep + fragment).strip(", ")
                return wf
        # 无语义节点时补一个 CLIPTextEncode 承载运镜文本
        wf.add_node("CLIPTextEncode", {"text": fragment},
                    title="运镜编码·CLIPTextEncode")
        return wf
