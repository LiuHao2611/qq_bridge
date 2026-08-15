# -*- coding: utf-8 -*-
"""首尾帧维度（手册 M1 §2.1 / 维度 6）—— 外推 / 插值分级。

职责：首尾帧锚定。两种能力分级：
- 外推（extrapolation）：给定首帧 → 生成后续帧（首帧外推）；
- 插值（interpolation）：给定首帧 + 尾帧 → 在中间补帧（首尾帧插值）。

实现：视频模型（LTX / Wan）支持首帧外推；首尾帧插值需模型原生支持。
build() 从意图的首尾帧路径推导锚定模式与分级，并在 wf 模式真实插入帧锚定节点
（LTXVImgToVideo，手册 M1：frame_anchor 维度 build 调 LTXVImgToVideo 做首帧外推）。
"""
import logging
from typing import Any, Dict, Optional

from .base import Capability
from .workflow_builder import WorkflowBuilder

logger = logging.getLogger("capabilities.frame_anchor")


class FrameAnchorCapability(Capability):
    key = "frame_anchor"
    name = "首尾帧"
    implementer = "视频模型首帧外推（LTX / Wan），插值预留"
    description = "首尾帧锚定：首帧外推 / 首尾帧插值（分级声明）。"

    def declare(self) -> Dict[str, Any]:
        # 手册 M2 示例：首尾帧外推 / 插值分级
        return {
            "mode": "first_frame_only",  # 当前仅支持外推
            "extrapolation": True,       # 首帧外推：支持
            "interpolation": False,      # 首尾帧插值：预留（需模型原生支持）
        }

    def build(self, intent, wf: Optional[Any] = None) -> Any:
        first = getattr(intent, "first_frame_path", None)
        last = getattr(intent, "last_frame_path", None)
        # ---- P2 M2：消费 choose_anchor_mode 写入的 frame_anchor_mode（若存在）----
        # 未写入（None）时退回 P0 原有「首/尾帧存在性」推导，保持向后兼容。
        forced = getattr(intent, "frame_anchor_mode", None)
        if forced in ("first_last_interpolate", "first_frame_only"):
            if forced == "first_last_interpolate" and last:
                anchor_mode = "interpolation"
                interp = True
            elif forced == "first_last_interpolate" and not last:
                # 诚实降级：声明插值却无尾帧 → 外推，不硬造假插值
                logger.warning("frame_anchor_mode=interpolation 但无尾帧，诚实降级为首帧外推")
                anchor_mode = "first_frame"
                interp = False
            elif first:
                anchor_mode = "first_frame"
                interp = False
            else:
                anchor_mode = "none"
                interp = False
        elif first and last:
            anchor_mode = "interpolation"
            interp = True
        elif first:
            anchor_mode = "first_frame"
            interp = False
        else:
            anchor_mode = "none"
            interp = False
        result: Dict[str, Any] = {
            "anchor_mode": anchor_mode,
            "extrapolation": anchor_mode in ("first_frame", "interpolation"),
            "interpolation": interp,
            "tail_reuse": anchor_mode == "interpolation",
            "grading": "A" if interp else ("B" if anchor_mode == "first_frame" else "C"),
        }
        # 诚实边界提示：供 DirectorOrb 气泡展示
        if not interp and anchor_mode == "first_frame":
            result["anchor_note"] = "当前模型仅首帧外推，建议缩短单镜时长"
        elif anchor_mode == "none":
            result["anchor_note"] = "未提供首帧，无法锚定"
        if wf is None:
            return result
        # ---- wf 模式：真实插入帧锚定节点（手册 M1 frame_anchor 维度）----
        # 首帧外推用 LTXVImgToVideo；插值（首尾帧）用 WanImgToVideo（如可用）。
        node_class = "WanImgToVideo" if interp else "LTXVImgToVideo"
        wf.add_node(
            node_class,
            {
                "image": first,
                "last_image": last if interp else None,
            },
            title="首尾帧锚定·%s" % node_class,
        )
        return wf
