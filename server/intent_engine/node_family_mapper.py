# -*- coding: utf-8 -*-
"""节点族映射器（手册 §2.3 / 设计思路 §4.4）。

职责：业务意图（锁脸/姿势/深度/视频）→ 具体 ComfyUI 节点类，按优先级自动降级；
全部不可用诚实抛 FeatureUnavailable（不静默跳过，CLOSE-03）。
"""
import logging

from .errors import FeatureUnavailable

LOG = logging.getLogger("intent_engine")


# 节点族注册表（优先级顺序即降级顺序，手册 §2.3.1 / §4.4）
NODE_FAMILY_REGISTRY = {
    "face_lock": {
        "priority": ["IPAdapterFaceID", "IPAdapterPlus", "InstantID", "ReActorFaceSwap"],
        "selection_logic": "auto_fallback",
        "required_inputs": {
            "IPAdapterFaceID": {"weight": 0.85, "face_embeds": "asset.face_vector"},
            "InstantID": {"insightface": "buffalo_l", "weight": 0.8},
        },
    },
    "pose_control": {
        "priority": ["ControlNetApply", "ControlNetApplyAdvanced"],
        "preprocessor": ["OpenPosePreprocessor", "DWPreprocessor"],
        "selection_logic": "auto_fallback",
    },
    "depth_control": {
        "priority": ["ControlNetApply"],
        "preprocessor": ["MiDaS-DepthMapPreprocessor", "Zoe-DepthPreprocessor"],
        "selection_logic": "auto_fallback",
    },
    "video_generation": {
        "priority": ["CogVideoSampler", "WanVideoSampler", "LTXVImgToVideo", "HunyuanVideoSampler"],
        "selection_logic": "user_preference_then_auto",
        "user_preference_key": "settings.preferred_video_model",
    },
    "image_generation": {
        "priority": ["KSampler", "KSamplerAdvanced"],
        "selection_logic": "auto_fallback",
    },
    "upscale": {
        "priority": ["UpscaleModelLoader", "ImageScaleBy", "UltimateSDUpscale"],
        "selection_logic": "auto_fallback",
    },
    "frame_anchor": {
        "priority": ["ImageBlend", "VHS_VideoCombine"],
        "selection_logic": "auto_fallback",
    },
}


class NodeFamilyMapper:
    def __init__(self, registry=None):
        self.registry = registry or NODE_FAMILY_REGISTRY

    def resolve(self, family_name, object_info, user_prefs=None):
        """解析节点族，返回可用节点类（手册 §2.3.2）。

        auto_fallback：按 priority 找第一个存在于 object_info 的节点。
        user_preference_then_auto：先查 user_prefs 指定项是否可用，再按 priority 降级。
        全部不可用 → 抛 FeatureUnavailable（附已安装节点清单）。
        """
        family = self.registry.get(family_name)
        if family is None:
            raise FeatureUnavailable(family_name, reason="未知节点族")
        logic = family.get("selection_logic", "auto_fallback")
        installed = sorted(object_info.keys()) if object_info else []

        if logic == "user_preference_then_auto":
            key = family.get("user_preference_key")
            preferred = (user_prefs or {}).get(key) if key else None
            if preferred and preferred in object_info:
                return self._result(family, preferred, object_info)

        for candidate in family["priority"]:
            if candidate in (object_info or {}):
                return self._result(family, candidate, object_info)

        raise FeatureUnavailable(
            family_name,
            reason="节点族无可用实现（按优先级 %s 均未安装）" % family["priority"],
            installed=installed)

    @staticmethod
    def _result(family, node_class, object_info):
        available_inputs = {}
        try:
            available_inputs = object_info[node_class]["input"]["required"]
        except Exception:
            pass
        return {
            "node_class": node_class,
            "available_inputs": available_inputs,
            "config": family.get("required_inputs", {}).get(node_class, {}),
        }

    def detect_node_families(self, object_info):
        """标记哪些节点族当前可用（基于 object_info 中是否存在 priority 任一节点）。"""
        result = {}
        for fam_name, fam in self.registry.items():
            for cand in fam["priority"]:
                if cand in (object_info or {}):
                    result[fam_name] = cand
                    break
        return result
