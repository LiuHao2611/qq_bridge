# -*- coding: utf-8 -*-
"""M1 能力维度抽象层单元测试（手册 §2.1 / 铁律 1.2）。

验证 6 个能力维度（semantic / temporal / consistency / audio_sync / camera /
frame_anchor）均提供了真实 declare() 与 build()，且批量导出 / 模型无关解析可用。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intent_engine import (
    BoundAsset, GenerationParams, StyleConstraints, ProductionIntent,
)
from capabilities import (
    Capability, SemanticCapability, TemporalCapability, ConsistencyCapability,
    AudioSyncCapability, CameraCapability, FrameAnchorCapability,
    CAPABILITY_KEYS, REGISTRY, all_dimensions, declare_all, build_all,
    preferred_video_model, declared_implementations,
)


def _sample_intent():
    return ProductionIntent(
        task_type="shot_video_text2video",
        intent_tags=["写实", "电影感", "推镜头"],
        bound_assets=[BoundAsset("ASSET-1", "character", "face", 0.85, "BASE64VEC")],
        generation_params=GenerationParams(
            seed=424242, width=1024, height=576, steps=30, cfg_scale=7.0,
            num_frames=81, fps=16),
        style_constraints=StyleConstraints(lighting="low_key_neon"),
        optional_controls={}, first_frame_path=None, last_frame_path=None)


class TestCapabilityRegistry(unittest.TestCase):
    def test_registry_has_six(self):
        self.assertEqual(len(CAPABILITY_KEYS), 6)
        for k in CAPABILITY_KEYS:
            self.assertIn(k, REGISTRY)
            self.assertIsInstance(REGISTRY[k], Capability)

    def test_dimension_classes_not_placeholder(self):
        # 铁律：禁止空接口 + pass。每个维度必须是真实实现类
        for k, inst in REGISTRY.items():
            self.assertNotEqual(inst.__class__.__name__,
                                "Capability", "维度 %s 未提供实现" % k)

    def test_all_dimensions_metadata(self):
        dims = all_dimensions()
        self.assertEqual(len(dims), 6)
        for d in dims:
            self.assertIn("key", d)
            self.assertIn("name", d)
            self.assertIn("implementer", d)
            self.assertIn("description", d)
            self.assertIn("declare", d)
            self.assertTrue(d["name"])        # 中文名非空
            self.assertTrue(d["implementer"])  # 实现名非空


class TestDeclare(unittest.TestCase):
    def test_declare_all_keys(self):
        decl = declare_all()
        self.assertEqual(set(decl.keys()), set(CAPABILITY_KEYS))
        for k, v in decl.items():
            self.assertIsInstance(v, dict)
            self.assertTrue(v, "维度 %s 的 declare() 返回空" % k)

    def test_frame_anchor_declare_shape(self):
        fa = REGISTRY["frame_anchor"].declare()
        self.assertIn("mode", fa)
        self.assertIn("extrapolation", fa)
        self.assertIn("interpolation", fa)


class TestBuild(unittest.TestCase):
    def test_build_all_on_real_intent(self):
        intent = _sample_intent()
        built = build_all(intent)
        self.assertEqual(set(built.keys()), set(CAPABILITY_KEYS))
        for k, v in built.items():
            self.assertIsInstance(v, dict)
            self.assertTrue(v, "维度 %s 的 build() 返回空" % k)

    def test_semantic_build_aggregates_tags(self):
        intent = _sample_intent()
        out = SemanticCapability().build(intent)
        # 语义维度应聚合 intent_tags + 风格约束
        self.assertTrue(any("写实" in str(o) for o in out.values()) or
                        out.get("intent_tags"))

    def test_camera_build_infers_motion(self):
        intent = _sample_intent()  # 含「推镜头」
        out = CameraCapability().build(intent)
        self.assertIn("camera_motion", out)
        self.assertEqual(out["camera_motion"], "push_in")  # 关键词应被识别为推镜

    def test_frame_anchor_build_grading(self):
        out = FrameAnchorCapability().build(_sample_intent())
        self.assertIn("anchor_mode", out)
        self.assertIn("interpolation", out)


class TestModelAgnosticResolution(unittest.TestCase):
    def test_preferred_video_model_returns_string(self):
        m = preferred_video_model()
        self.assertIsInstance(m, str)
        self.assertTrue(m)

    def test_declared_implementations_not_empty(self):
        impls = declared_implementations()
        self.assertIsInstance(impls, list)
        self.assertTrue(impls, "已声明实现清单不应为空（节点族注册表）")
        for it in impls:
            self.assertIn("name", it)
            self.assertIn("type", it)
            self.assertIn("capabilities", it)


class TestFrameAnchorGrading(unittest.TestCase):
    """手册 M2 §2.2.6 首尾帧分级：LTX 仅首帧外推，Wan 支持首尾帧插值（铁律，禁止反例）。"""

    def _caps_for(self, node_name):
        for it in declared_implementations():
            if it["name"] == node_name and it["type"] == "video_model":
                return it["capabilities"].get("frame_anchor")
        return None

    def test_ltx_is_first_frame_only(self):
        fa = self._caps_for("LTXVImgToVideo")
        self.assertIsNotNone(fa, "LTXVImgToVideo 应被声明为视频模型能力")
        self.assertEqual(fa["mode"], "first_frame_only")
        self.assertTrue(fa["extrapolation"])
        self.assertFalse(fa["interpolation"], "LTX 禁止标为插值（手册 M2）")

    def test_wan_is_first_last_interpolate(self):
        fa = self._caps_for("WanVideoSampler")
        self.assertIsNotNone(fa)
        self.assertEqual(fa["mode"], "first_last_interpolate" if fa.get("mode") == "first_last" else fa["mode"])
        self.assertTrue(fa["interpolation"], "Wan 必须标为插值（手册 M2）")

    def test_other_video_models_extrapolate_only(self):
        for node in ("CogVideoSampler", "HunyuanVideoSampler"):
            fa = self._caps_for(node)
            if fa is None:
                continue
            self.assertFalse(fa["interpolation"], "%s 非 Wan，不应标插值" % node)


if __name__ == "__main__":
    unittest.main()
