# -*- coding: utf-8 -*-
"""M3 意图翻译引擎·匹配算法验证（手册 §2.6 / §5 禁止 if-else 硬编码模型名）。

证明：节点选择由「注册表候选 tags ∩ intent_tags」打分驱动，而非 if-else 硬编码
模型名。同一份已安装节点集合，仅改 intent_tags 即可改变选型，且不需要改代码。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intent_engine.node_family_mapper import NodeFamilyMapper


def _oi_with_ltx_and_wan():
    nodes = ["KSampler", "CLIPTextEncode", "LTXVImgToVideo", "WanVideoSampler"]
    return {n: {"input": {"required": {}}} for n in nodes}


class TestTagDrivenNodeSelection(unittest.TestCase):
    def setUp(self):
        self.mapper = NodeFamilyMapper()
        self.oi = _oi_with_ltx_and_wan()

    def test_default_no_tags_prefers_wan(self):
        # 无意图标签 → 走硬件适配默认（WanVideoSampler）
        res = self.mapper.resolve("video_generation", self.oi, {})
        self.assertEqual(res["node_class"], "WanVideoSampler")

    def test_interp_tag_selects_wan(self):
        # intent_tags 命中 Wan 的「插值 / first_last_interpolate」→ 选 Wan
        res = self.mapper.resolve("video_generation", self.oi, {},
                                  intent_tags=["插值", "first_last_interpolate"])
        self.assertEqual(res["node_class"], "WanVideoSampler")

    def test_extrapolate_tag_selects_ltx(self):
        # intent_tags 命中 LTX 的「外推 / first_frame_only」→ 覆写默认选 LTX
        res = self.mapper.resolve("video_generation", self.oi, {},
                                  intent_tags=["外推", "first_frame_only"])
        self.assertEqual(res["node_class"], "LTXVImgToVideo")

    def test_user_pref_still_wins(self):
        # 用户显式偏好优先于标签打分
        res = self.mapper.resolve(
            "video_generation", self.oi,
            {"settings.preferred_video_model": "LTXVImgToVideo"},
            intent_tags=["插值"])
        self.assertEqual(res["node_class"], "LTXVImgToVideo")


class TestCompatAndWeighting(unittest.TestCase):
    def _mapper(self, registry):
        return NodeFamilyMapper(registry=registry)

    def test_user_tags_weight_two_beats_auto_one(self):
        # 手册 M3：user_tags 命中权重 2 > auto_tags 命中权重 1。
        # CandA 仅 auto 命中，CandB 仅 user 命中 → 应选 CandB。
        reg = {
            "video_generation": {
                "priority": ["CandA", "CandB"],
                "selection_logic": "auto_fallback",
                "user_tags": {"CandA": [], "CandB": ["电影感"]},
                "auto_tags": {"CandA": ["电影感"], "CandB": []},
            }
        }
        oi = {n: {"input": {"required": {}}} for n in ["CandA", "CandB"]}
        res = self._mapper(reg).resolve("video_generation", oi, intent_tags=["电影感"])
        self.assertEqual(res["node_class"], "CandB")

    def test_compat_filter_excludes_incompatible_ip(self):
        # 手册 M3 §3：checkpoint 为 sd1.5 架构时，InstantID（仅 sdxl）被排除。
        oi = {"IPAdapterFaceID": {"input": {}}, "InstantID": {"input": {}}}
        res = NodeFamilyMapper().resolve(
            "face_lock", oi, intent_tags=[], checkpoint="someSD15model.safetensors")
        self.assertEqual(res["node_class"], "IPAdapterFaceID")
        self.assertNotEqual(res["node_class"], "InstantID")

    def test_compat_filter_keeps_compatible(self):
        # sdxl checkpoint：InstantID（sdxl）与 IPAdapterFaceID（sdxl/sd1.5）均保留。
        oi = {"IPAdapterFaceID": {"input": {}}, "InstantID": {"input": {}}}
        res = NodeFamilyMapper().resolve(
            "face_lock", oi, intent_tags=[], checkpoint="someSDXLmodel.safetensors")
        self.assertIn(res["node_class"], ("IPAdapterFaceID", "InstantID"))


if __name__ == "__main__":
    unittest.main()
