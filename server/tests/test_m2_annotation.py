# -*- coding: utf-8 -*-
"""M2 能力注册表·标注算法验证（手册 §2.2.6 / §5 禁止手写 chip）。

证明：
  1. detect_capability_dimensions 由 /object_info 节点名自动推导能力维度
     （IPAdapterFaceID 节点存在 → 自动标注 consistency，非手写死）；
  2. 首尾帧分级：LTXVImgToVideo → first_frame_only；WanImgToVideo → first_last_interpolate；
  3. declared_implementations 的能力声明由算法生成（维度齐全，且 Wan 插值 / LTX 外推正确）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intent_engine.resource_catalog import (
    detect_capability_dimensions, NODE_DIMENSION_MAP,
)
from capabilities import declared_implementations


class TestDetectCapabilityDimensions(unittest.TestCase):
    def _oi(self, nodes):
        return {n: {"input": {"required": {}}} for n in nodes}

    def _oi_inputs(self, nodes_fields):
        """构造带 required 输入字段的 object_info：{node: [field, ...]}。"""
        return {n: {"input": {"required": {f: ["x"] for f in fields}}}
                for n, fields in nodes_fields.items()}

    def test_ipadapter_node_yields_consistency(self):
        oi = self._oi(["IPAdapterFaceID", "KSampler", "CLIPTextEncode"])
        det = detect_capability_dimensions(oi)
        self.assertIn("IPAdapterFaceID", det.get("consistency", []))
        self.assertIn("KSampler", det.get("temporal", []))
        self.assertIn("CLIPTextEncode", det.get("semantic", []))

    def test_only_present_nodes_annotated(self):
        oi = self._oi(["WanImgToVideo"])
        det = detect_capability_dimensions(oi)
        # 未安装的节点不应出现在标注中
        self.assertNotIn("consistency", det)
        self.assertIn("frame_anchor", det)

    def test_ltx_frame_anchor_extrapolate_only(self):
        oi = self._oi(["LTXVImgToVideo"])
        det = detect_capability_dimensions(oi)
        fa = det["frame_anchor"][0]
        self.assertEqual(fa["node"], "LTXVImgToVideo")
        self.assertEqual(fa["mode"], "first_frame_only")
        self.assertTrue(fa["extrapolation"])
        self.assertFalse(fa["interpolation"])

    def test_wan_frame_anchor_interpolate(self):
        # 手册 M2 §2.2.6：Wan 插值由「输入字段含 first_frame + last_frame」判定，非硬编码模型名
        oi = self._oi_inputs({"WanImgToVideo": ["first_frame", "last_frame"]})
        det = detect_capability_dimensions(oi)
        fa = det["frame_anchor"][0]
        self.assertEqual(fa["node"], "WanImgToVideo")
        self.assertEqual(fa["mode"], "first_last_interpolate")
        self.assertTrue(fa["interpolation"])

    def test_frame_anchor_grading_by_input_fields(self):
        # 输入字段驱动分级：仅 first_frame → 外推；first+last → 插值
        oi_ext = self._oi_inputs({"SomeNovelI2V": ["first_frame"]})
        det = detect_capability_dimensions(oi_ext)
        self.assertEqual(det["frame_anchor"][0]["mode"], "first_frame_only")
        oi_int = self._oi_inputs({"SomeNovelI2V": ["first_frame", "last_frame"]})
        det2 = detect_capability_dimensions(oi_int)
        self.assertEqual(det2["frame_anchor"][0]["mode"], "first_last_interpolate")

    def test_layer2_signature_infers_consistency(self):
        # 层2：未知节点名但输入含 face_embeds → 标 consistency（source=signature）
        oi = self._oi_inputs({"MyNovelFaceNode": ["face_embeds", "model"]})
        det = detect_capability_dimensions(oi)
        self.assertIn("MyNovelFaceNode", det.get("consistency", []))
        self.assertEqual(det["sources"]["consistency"]["MyNovelFaceNode"], "signature")

    def test_layer2_signature_infers_temporal(self):
        # 层2：输入含 conditioning + latent → temporal（source=signature）
        oi = self._oi_inputs({"NovelSamplerX": ["conditioning", "latent", "model"]})
        det = detect_capability_dimensions(oi)
        self.assertIn("NovelSamplerX", det.get("temporal", []))
        self.assertEqual(det["sources"]["temporal"]["NovelSamplerX"], "signature")

    def test_layer3_unclassified_for_unknown_node(self):
        # 层3：层1/层2 均未命中的未知节点 → 进 unclassified_nodes（status=pending）
        oi = self._oi(["TotallyUnknownNodeXYZ"])
        det = detect_capability_dimensions(oi)
        unc = det.get("unclassified_nodes", [])
        self.assertTrue(any(u["node"] == "TotallyUnknownNodeXYZ" for u in unc))
        self.assertEqual(unc[0]["status"], "pending")

    def test_source_field_present(self):
        # 每个能力维度 chip 带 source 字段（name_semantic / signature）
        oi = self._oi(["IPAdapterFaceID", "KSampler", "CLIPTextEncode"])
        oi.update(self._oi_inputs({"SigNode": ["face_embeds"]}))
        det = detect_capability_dimensions(oi)
        self.assertEqual(det["sources"]["consistency"]["IPAdapterFaceID"], "name_semantic")
        self.assertEqual(det["sources"]["consistency"]["SigNode"], "signature")
        self.assertIn("name_semantic", det["sources"]["temporal"].values())

    def test_vhs_yields_audio_sync(self):
        oi = self._oi(["VHS_VideoCombine"])
        det = detect_capability_dimensions(oi)
        self.assertIn("VHS_VideoCombine", det.get("audio_sync", []))

    def test_mapping_table_nonempty(self):
        self.assertTrue(NODE_DIMENSION_MAP)


class TestDeclaredImplementationsAlgorithmic(unittest.TestCase):
    def test_all_video_nodes_have_six_dims(self):
        impls = declared_implementations()
        video = [it for it in impls if it["type"] == "video_model"]
        self.assertTrue(video)
        six = {"semantic", "temporal", "consistency", "audio_sync", "camera", "frame_anchor"}
        for it in video:
            self.assertEqual(set(it["capabilities"].keys()), six,
                             "能力维度声明必须覆盖 6 维度（算法生成）")

    def test_wan_interpolate_ltx_extrapolate(self):
        impls = {it["name"]: it for it in declared_implementations()}
        wan = impls["WanVideoSampler"]["capabilities"]["frame_anchor"]
        ltx = impls["LTXVImgToVideo"]["capabilities"]["frame_anchor"]
        self.assertTrue(wan["interpolation"])
        self.assertFalse(ltx["interpolation"])

    def test_ip_family_declares_consistency(self):
        impls = [it for it in declared_implementations() if it["type"] == "ip_adapter"]
        self.assertTrue(impls)
        for it in impls:
            self.assertIn("consistency", it["capabilities"])


if __name__ == "__main__":
    unittest.main()
