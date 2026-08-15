# -*- coding: utf-8 -*-
"""M1 能力维度 build() 真实插节点验证（手册 §2.1 / 铁律 §5 禁止 pass/None）。

核心硬要求：6 维度 build(intent, wf) 真实往 workflow 插入对应 ComfyUI 节点，
其中 consistency.build() 产出的 workflow **必须含 IPAdapterFaceID 节点**。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intent_engine import (
    BoundAsset, GenerationParams, StyleConstraints, ProductionIntent, Subject,
)
from capabilities import (
    REGISTRY, CAPABILITY_KEYS, build_all,
)
from capabilities.workflow_builder import WorkflowBuilder


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


def _subjects_intent():
    """含 role 的 subjects（遗漏 1 修复：参考按作用绑定）。"""
    return ProductionIntent(
        task_type="shot_video_text2video",
        intent_tags=["写实", "电影感"],
        subjects=[
            Subject("role_001", "identity", 0.85),
            Subject("role_002", "outfit", 0.5),
        ],
        generation_params=GenerationParams(seed=7, width=1024, height=576, steps=30),
        optional_controls={},
    )


# 每个维度在 wf 模式应插入的节点类（手册 M1 §2.1 映射）
_EXPECTED_NODE = {
    "semantic": "CLIPTextEncode",
    "temporal": "KSampler",
    "consistency": "IPAdapterFaceID",
    "audio_sync": "VHS_VideoCombine",
    "camera": "CLIPTextEncode",        # 运镜折叠进语义编码节点
    "frame_anchor": "LTXVImgToVideo",
}


class TestBuildInsertsNodes(unittest.TestCase):
    def test_consistency_build_inserts_ipadapter_node(self):
        """手册 M1 验收硬要求：consistency.build() 产出 workflow 含 IPAdapterFaceID 节点。"""
        wf = WorkflowBuilder()
        out = REGISTRY["consistency"].build(_sample_intent(), wf)
        self.assertIs(out, wf)  # 返回 wf 本身
        self.assertTrue(wf.has_node("IPAdapterFaceID"),
                        "consistency.build 必须插入 IPAdapterFaceID 节点")

    def test_each_dimension_inserts_expected_node(self):
        for key in CAPABILITY_KEYS:
            wf = WorkflowBuilder()
            REGISTRY[key].build(_sample_intent(), wf)
            self.assertTrue(
                wf.has_node(_EXPECTED_NODE[key]),
                "维度 %s.build 应插入 %s 节点，实际节点：%s"
                % (key, _EXPECTED_NODE[key], list(wf.nodes.values())))

    def test_build_all_inserts_all_six(self):
        wf = WorkflowBuilder()
        out = build_all(_sample_intent(), wf)
        self.assertIs(out, wf)
        present = {n["class_type"] for n in wf.nodes.values()}
        for key, node in _EXPECTED_NODE.items():
            self.assertIn(node, present, "build_all 应插入 %s（维度 %s）" % (node, key))
        # IPAdapterFaceID 必在（一致性硬要求）
        self.assertIn("IPAdapterFaceID", present)

    def test_build_none_returns_dict_backward_compat(self):
        """wf=None 时仍返回结构化 dict（兼容 P1 锁脸计划 / M4 聚合 / build_all 旧调用）。"""
        d = REGISTRY["consistency"].build(_sample_intent())
        self.assertIsInstance(d, dict)
        self.assertIn("lock_mode", d)
        all_dicts = build_all(_sample_intent())
        self.assertIsInstance(all_dicts, dict)
        self.assertEqual(set(all_dicts.keys()), set(CAPABILITY_KEYS))

    def test_camera_folds_into_existing_clip(self):
        """camera 维度应把运镜片段并入已存在的 CLIPTextEncode（prompt_driven）。"""
        wf = WorkflowBuilder()
        REGISTRY["semantic"].build(_sample_intent(), wf)
        REGISTRY["camera"].build(_sample_intent(), wf)
        clip_id, clip = wf.find_node("CLIPTextEncode")
        self.assertIsNotNone(clip)
        self.assertIn("camera", (clip["inputs"].get("text") or "").lower())

    # ---- M1 角色分支（遗漏 1 修复）----
    def test_identity_role_inserts_faceid(self):
        """role=identity 必须插入 IPAdapterFaceID（锁人脸）。"""
        wf = WorkflowBuilder()
        REGISTRY["consistency"].build(_subjects_intent(), wf)
        self.assertTrue(wf.has_node("IPAdapterFaceID"))
        _, node = wf.find_node("IPAdapterFaceID")
        self.assertEqual(node["inputs"]["weight"], 0.85)

    def test_outfit_role_inserts_plain_ipadapter_not_faceid(self):
        """role=outfit 必须插入 IPAdapter（非 FaceID），且权重 ≤0.6（手册 M1 验收硬要求）。

        用「仅 outfit」的意图隔离验证：该 subject 插普通 IPAdapter，且不插 FaceID。
        """
        intent = ProductionIntent(
            task_type="shot_video_text2video",
            intent_tags=["写实"],
            subjects=[Subject("role_002", "outfit", 0.5)],
            generation_params=GenerationParams(seed=7, width=512, height=512, steps=20),
            optional_controls={},
        )
        wf = WorkflowBuilder()
        REGISTRY["consistency"].build(intent, wf)
        self.assertTrue(wf.has_node("IPAdapter"), "outfit role 必须插入普通 IPAdapter 节点")
        self.assertFalse(wf.has_node("IPAdapterFaceID"),
                         "outfit role 不应插 IPAdapterFaceID（仅 identity 锁脸）")
        _, node = wf.find_node("IPAdapter")
        self.assertLessEqual(node["inputs"]["weight"], 0.6,
                             "服装锁权重必须 ≤0.6，实际=%s" % node["inputs"]["weight"])
        # 第二 subject（outfit, weight=0.5）→ weight 保持 0.5（<0.6 不被截断）
        self.assertEqual(node["inputs"]["weight"], 0.5)

    def test_mixed_subjects_insert_both_node_types(self):
        """混合 subjects（identity + outfit）→ 同时插入 FaceID 与 IPAdapter（各自角色分支）。"""
        wf = WorkflowBuilder()
        REGISTRY["consistency"].build(_subjects_intent(), wf)
        self.assertTrue(wf.has_node("IPAdapterFaceID"), "identity subject 必须插 FaceID")
        self.assertTrue(wf.has_node("IPAdapter"), "outfit subject 必须插 IPAdapter")

    def test_outfit_weight_cap_at_0_6(self):
        """outfit 权重超 0.6 时强制截断到 0.6（不污染人脸锁权重区间）。"""
        intent = ProductionIntent(
            task_type="shot_video_text2video",
            intent_tags=["写实"],
            subjects=[Subject("role_x", "outfit", 0.95)],
            generation_params=GenerationParams(seed=1, width=512, height=512, steps=20),
            optional_controls={},
        )
        wf = WorkflowBuilder()
        REGISTRY["consistency"].build(intent, wf)
        _, node = wf.find_node("IPAdapter")
        self.assertEqual(node["inputs"]["weight"], 0.6)

    def test_subjects_field_present_and_serializable(self):
        """ProductionIntent.subjects 含 role 字段，且 to_dict/from_dict 往返一致（M4 契约）。"""
        intent = _subjects_intent()
        self.assertEqual(len(intent.subjects), 2)
        self.assertEqual(intent.subjects[0].role, "identity")
        self.assertEqual(intent.subjects[1].role, "outfit")
        d = intent.to_dict()
        self.assertEqual(d["subjects"][1]["role"], "outfit")
        back = ProductionIntent.from_dict(d)
        self.assertEqual([s.role for s in back.subjects], ["identity", "outfit"])
        self.assertEqual(back.spatial, {})
        self.assertEqual(back.style_anchor, {})


if __name__ == "__main__":
    unittest.main()
