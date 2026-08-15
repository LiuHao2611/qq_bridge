# -*- coding: utf-8 -*-
"""节点族映射器（手册 §2.3 / 设计思路 §4.4）。

职责：业务意图（锁脸/姿势/深度/视频）→ 具体 ComfyUI 节点类，按优先级自动降级；
全部不可用诚实抛 FeatureUnavailable（不静默跳过，CLOSE-03）。
"""
import logging
import re

from .errors import FeatureUnavailable

LOG = logging.getLogger("intent_engine")


def _infer_arch(name: str) -> str:
    """从底模名推断架构（sd1.5 / sdxl），供组件兼容过滤。"""
    n = (name or "").lower()
    if re.search(r"sdxl|\b xl\b", n):
        return "sdxl"
    return "sd1.5"  # 未知默认按 SD1.5 兼容（最宽松）



# 节点族注册表（优先级顺序即降级顺序，手册 §2.3.1 / §4.4）
NODE_FAMILY_REGISTRY = {
    "face_lock": {
        "priority": ["IPAdapterFaceID", "IPAdapterPlus", "InstantID", "ReActorFaceSwap"],
        "selection_logic": "auto_fallback",
        "required_inputs": {
            "IPAdapterFaceID": {"weight": 0.85, "face_embeds": "asset.face_vector"},
            "InstantID": {"insightface": "buffalo_l", "weight": 0.8},
        },
        # 组件兼容（手册 M3 §3）：候选 IP 节点兼容的底模架构。
        # 切换底模时排除不兼容候选，而非 if-else 硬编码。
        "compatible_checkpoints": {
            "IPAdapterFaceID": ["sd1.5", "sdxl"],
            "IPAdapterPlus": ["sd1.5", "sdxl"],
            "InstantID": ["sdxl"],
            "ReActorFaceSwap": ["sd1.5", "sdxl"],
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
        # 每节点的帧/帧率默认值（手册 M3 帧参数，由注册表驱动，禁止 if-else 硬编码）
        "required_inputs": {
            "CogVideoSampler": {"num_frames": 49, "fps": 8},
            "WanVideoSampler": {"num_frames": 81, "fps": 16},
            "LTXVImgToVideo": {"num_frames": 49, "fps": 8},
            "HunyuanVideoSampler": {"num_frames": 61, "fps": 12},
        },
        # 首尾帧分级声明（手册 M2 §2.2.6：按节点输入字段判定，禁止 if-else 硬编码模型名）。
        # 含 first_frame + last_frame → 插值（first_last_interpolate）；仅 first_frame → 外推。
        # 切换模型 = 改这里的数据，而非改匹配/标注代码（模型无关）。
        "frame_anchor_inputs": {
            "CogVideoSampler": ["first_frame"],
            "WanVideoSampler": ["first_frame", "last_frame"],
            "LTXVImgToVideo": ["first_frame"],
            "HunyuanVideoSampler": ["first_frame"],
        },
        # 每节点的能力标签（手册 M3 匹配算法：intent_tags 与候选标签打分）。
        # user_tags：人工标注（权重 2）；auto_tags：文件名推断（权重 1）。
        # 模型无关：切换模型 = 改这里的数据，而非改匹配代码。
        "user_tags": {
            "CogVideoSampler": ["电影感"],
            "WanVideoSampler": ["电影感", "写实", "插值", "first_last_interpolate"],
            "LTXVImgToVideo": ["动漫", "轻量", "外推", "first_frame_only"],
            "HunyuanVideoSampler": ["写实", "外推", "first_frame_only"],
        },
        "auto_tags": {
            "CogVideoSampler": ["视频"],
            "WanVideoSampler": ["视频"],
            "LTXVImgToVideo": ["视频"],
            "HunyuanVideoSampler": ["视频"],
        },
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


# 手册 §10.4 待确认项：preferred_video_model 默认值（按本机 1660Ti 实测后调优）。
# 用户未设置时回退到此值；若不在 ComfyUI object_info 中则继续按 priority 自动降级。
DEFAULT_PREFERRED_VIDEO_MODEL = "WanVideoSampler"


class NodeFamilyMapper:
    def __init__(self, registry=None):
        self.registry = registry or NODE_FAMILY_REGISTRY

    def resolve(self, family_name, object_info, user_prefs=None, intent_tags=None,
                 checkpoint=None):
        """解析节点族，返回可用节点类（手册 §2.3.2 / M3 匹配算法）。

        auto_fallback：按 priority 找第一个存在于 object_info 的节点。
        user_preference_then_auto：先查 user_prefs 指定项是否可用，再按 priority 降级。
        全部不可用 → 抛 FeatureUnavailable（附已安装节点清单）。

        匹配算法（手册 M3 §2.6 第 2/3 步）：
          - 兼容过滤：若传入已选 checkpoint，排除 compatible_checkpoints 不含该
            底模架构的候选（防止 IP-Adapter 与底模不匹配）；
          - 打分：score = user_tags命中数×2 + auto_tags命中数×1（intent_tags 与候选标签）；
          - 排序：score 降序；同分取节点族优先级顺序。
        日志含 filter/score/rank，证明「自动匹配、非硬编码」。
        """
        family = self.registry.get(family_name)
        if family is None:
            raise FeatureUnavailable(family_name, reason="未知节点族")
        logic = family.get("selection_logic", "auto_fallback")
        installed = sorted(object_info.keys()) if object_info else []

        # 候选打分（M3 匹配算法）：user_tags 命中权重 2 + auto_tags 命中权重 1
        user_tags_map = family.get("user_tags") or {}
        auto_tags_map = family.get("auto_tags") or {}

        def _score(candidate):
            if not intent_tags:
                return 0
            s = 0
            ut = set(user_tags_map.get(candidate, []))
            at = set(auto_tags_map.get(candidate, []))
            for t in intent_tags:
                if t in ut:
                    s += 2
                elif t in at:
                    s += 1
            return s

        # ---- 兼容过滤（手册 M3 §3）----
        compat_map = family.get("compatible_checkpoints")
        filtered_out = []
        if checkpoint and compat_map:
            want_arch = _infer_arch(checkpoint)
            def _compat(c):
                allowed = compat_map.get(c)
                if allowed is None:
                    return True   # 未声明兼容集 → 不强制排除
                return want_arch in allowed
            present_raw = [c for c in family["priority"] if c in (object_info or {})]
            present = [c for c in present_raw if _compat(c)]
            filtered_out = [c for c in present_raw if c not in present]
            if filtered_out:
                LOG.info("[MATCH] 兼容过滤：checkpoint=%s arch=%s 排除不兼容候选=%s",
                         checkpoint, want_arch, filtered_out)
        else:
            present = [c for c in family["priority"] if c in (object_info or {})]

        # 用户显式偏好优先（仅 user_preference_then_auto 族）
        if logic == "user_preference_then_auto":
            key = family.get("user_preference_key")
            explicit_pref = (user_prefs or {}).get(key) if key else None
            if explicit_pref and explicit_pref in object_info and explicit_pref in present:
                LOG.info("[MATCH] family=%s 用户显式偏好=%s 命中", family_name, explicit_pref)
                return self._result(family, explicit_pref, object_info)

        if not present:
            raise FeatureUnavailable(
                family_name,
                reason="节点族无可用实现（按优先级 %s 均未安装/不兼容）" % family["priority"],
                installed=installed)
        # 打分排序：分数高优先，同分保持 priority 顺序（稳定）
        ranked = sorted(present, key=lambda c: (-_score(c), family["priority"].index(c)))
        best = ranked[0]
        LOG.info("[MATCH] family=%s intent_tags=%s checkpoint=%s selected=%s "
                 "scores=%s filtered_out=%s",
                 family_name, intent_tags, checkpoint, best,
                 {c: _score(c) for c in ranked}, filtered_out)
        return self._result(family, best, object_info)

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
