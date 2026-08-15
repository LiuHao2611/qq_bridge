# -*- coding: utf-8 -*-
"""音画同步维度（手册 M1 §2.1 / 维度 4）。

职责：声音与画面对齐（旁白 / 对白 / BGM 与镜头时间轴对齐）。
实现：由后期合成阶段（N07）分段 apad 精确对齐，误差硬约束 ≤100ms；
build() 产出音画同步开关与对齐精度，并在 wf 模式真实插入 VHS_VideoCombine 节点
（手册 M1：audio_sync 维度 build 调 VHS_VideoCombine + 音频混流）。
"""
from typing import Any, Dict, Optional

from .base import Capability
from .workflow_builder import WorkflowBuilder


class AudioSyncCapability(Capability):
    key = "audio_sync"
    name = "音画同步"
    implementer = "后期分段对齐（apad，误差≤100ms，后处理）"
    description = "将旁白 / 对白 / BGM 与镜头时间轴精确对齐。"

    ALIGN_MAX_MS = 100  # 手册 §3.4.4：音画同步误差≤100ms

    def declare(self) -> Dict[str, Any]:
        return {
            "mode": "post_process",
            "align_ms": self.ALIGN_MAX_MS,
            "tts": True,
            "bgm": True,
        }

    def build(self, intent, wf: Optional[Any] = None) -> Any:
        # 音频同步默认开启；意图可经 optional_controls 关闭
        opts = getattr(intent, "optional_controls", {}) or {}
        audio_on = bool(opts.get("audio", True))
        result: Dict[str, Any] = {
            "audio_sync": audio_on,
            "align_max_ms": self.ALIGN_MAX_MS,
            "tts": audio_on,
            "bgm": audio_on,
            "strategy": "segment_apad",
        }
        if wf is None:
            return result
        # ---- wf 模式：真实插入 VHS_VideoCombine 节点（手册 M1 audio_sync 维度）----
        # 音画同步开启时拼接音频；关闭时仅合成视频（音频输入留空由下游处理）。
        wf.add_node(
            "VHS_VideoCombine",
            {
                "audio": "audio.wav" if audio_on else None,
                "fps": getattr(getattr(intent, "generation_params", None), "fps", 8),
                "frame_rate": 0,
            },
            title="音画合成·VHS_VideoCombine",
        )
        return wf
