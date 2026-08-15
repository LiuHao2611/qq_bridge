# -*- coding: utf-8 -*-
"""时序运动维度（手册 M1 §2.1 / 维度 2）。

职责：生成连续、连贯的运动（视频专用）。
实现：由 LTX / Wan 单 Transformer（3D 时空注意力）承担时序建模；
build() 从意图的 generation_params 推导帧数 / 帧率 / 运动连贯等级，并在 wf 模式
真实插入采样器节点（KSampler，手册 M1 时序维度 build 调采样器迭代去噪）。
"""
from typing import Any, Dict, Optional

from .base import Capability
from .workflow_builder import WorkflowBuilder


class TemporalCapability(Capability):
    key = "temporal"
    name = "时序运动"
    implementer = "LTX / Wan 单 Transformer（3D 时空注意力）"
    description = "为视频生成提供连续、连贯的时序运动建模。"

    def declare(self) -> Dict[str, Any]:
        return {
            "arch": "3d_transformer",
            "min_frames": 16,
            "max_frames": 241,
            "coherence": "high",
        }

    def build(self, intent, wf: Optional[Any] = None) -> Any:
        gp = getattr(intent, "generation_params", None)
        num_frames = getattr(gp, "num_frames", None) if gp else None
        fps = getattr(gp, "fps", None) if gp else None
        num_frames = int(num_frames) if num_frames else 49
        fps = int(fps) if fps else 8
        # 运动连贯等级：帧数越多、帧率越稳 → 连贯度越高
        coherence = "high" if num_frames >= 49 else ("medium" if num_frames >= 25 else "low")
        result: Dict[str, Any] = {
            "arch": "3d_transformer",
            "num_frames": num_frames,
            "fps": fps,
            "motion_coherence": coherence,
            "temporal_window": "full_sequence",
        }
        if wf is None:
            return result
        # ---- wf 模式：真实插入采样器节点（手册 M1 时序维度 build 调采样器）----
        wf.add_node(
            "KSampler",
            {
                "seed": getattr(gp, "seed", 0) if gp else 0,
                "steps": getattr(gp, "steps", 30) if gp else 30,
                "cfg": getattr(gp, "cfg_scale", 7.0) if gp else 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
            title="时序去噪·KSampler",
        )
        return wf
