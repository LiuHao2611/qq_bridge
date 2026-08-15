# -*- coding: utf-8 -*-
"""P2 M2 单元测试：首尾帧双锚定（choose_anchor_mode / chain_frames / frame_anchor.build 消费）。

含 P0 依赖打通证据：frame_anchor.build 消费 frame_anchor_mode。
"""
import os
import tempfile
import unittest

from intent_engine import ProductionIntent
from capabilities.frame_anchor import FrameAnchorCapability
from features.frame_chain import choose_anchor_mode, apply_anchor_mode, chain_frames


# 模型能力声明（两种词汇）
LTX_CAP = {"frame_anchor": {"mode": "first_frame_only", "interpolation": False}}
WAN_CAP = {"frame_anchor": {"mode": "first_last_interpolate", "interpolation": True}}
MANUAL_INTERP_CAP = {"frame_anchor": {"grading": "first_last_interpolate"}}
MANUAL_EXTRAPOL_CAP = {"frame_anchor": {"grading": "first_frame_only"}}


class TestChooseAnchorMode(unittest.TestCase):
    def test_ltx_no_interp_returns_extrapolation(self):
        shot = {"first_frame_path": "f.png", "last_frame_path": "l.png"}
        self.assertEqual(choose_anchor_mode(shot, LTX_CAP), "first_frame_only")

    def test_wan_interp_with_both_frames(self):
        shot = {"first_frame_path": "f.png", "last_frame_path": "l.png"}
        self.assertEqual(choose_anchor_mode(shot, WAN_CAP), "first_last_interpolate")

    def test_wan_interp_only_first_frame(self):
        shot = {"first_frame_path": "f.png"}
        self.assertEqual(choose_anchor_mode(shot, WAN_CAP), "first_frame_only")

    def test_manual_grading_vocab(self):
        shot = {"first_frame_path": "f.png", "last_frame_path": "l.png"}
        self.assertEqual(choose_anchor_mode(shot, MANUAL_INTERP_CAP), "first_last_interpolate")
        self.assertEqual(choose_anchor_mode(shot, MANUAL_EXTRAPOL_CAP), "first_frame_only")

    def test_no_frames_always_extrapolation(self):
        shot = {}
        self.assertEqual(choose_anchor_mode(shot, WAN_CAP), "first_frame_only")


class TestChainFrames(unittest.TestCase):
    def test_same_scene_reuses_last_frame(self):
        calls = {"n": 0}
        def fake_extract(video):
            calls["n"] += 1
            return "/tmp/last.png"
        prev = {"video_path": "/v/sc1.mp4", "scene_id": "sc1"}
        cur = {"scene_id": "sc1"}
        out = chain_frames(prev, cur, extract_fn=fake_extract)
        self.assertEqual(out["first_frame_path"], "/tmp/last.png")
        self.assertEqual(calls["n"], 1)

    def test_cross_scene_does_not_reuse(self):
        prev = {"video_path": "/v/sc1.mp4", "scene_id": "sc1"}
        cur = {"scene_id": "sc2"}
        out = chain_frames(prev, cur, extract_fn=lambda v: "/tmp/last.png")
        self.assertNotIn("first_frame_path", out)

    def test_missing_video_skips(self):
        prev = {"scene_id": "sc1"}
        cur = {"scene_id": "sc1"}
        out = chain_frames(prev, cur, extract_fn=lambda v: "/tmp/last.png")
        self.assertNotIn("first_frame_path", out)


class TestFrameAnchorBuildConsumesMode(unittest.TestCase):
    def test_interpolation_mode_consumed(self):
        intent = ProductionIntent(
            task_type="shot_video_with_anchor", intent_tags=[],
            first_frame_path="f.png", last_frame_path="l.png",
            frame_anchor_mode="first_last_interpolate")
        res = FrameAnchorCapability().build(intent)
        self.assertEqual(res["anchor_mode"], "interpolation")
        self.assertTrue(res["interpolation"])
        self.assertEqual(res["grading"], "A")

    def test_extrapolation_mode_consumed(self):
        intent = ProductionIntent(
            task_type="shot_video_with_anchor", intent_tags=[],
            first_frame_path="f.png", frame_anchor_mode="first_frame_only")
        res = FrameAnchorCapability().build(intent)
        self.assertEqual(res["anchor_mode"], "first_frame")
        self.assertFalse(res["interpolation"])
        self.assertEqual(res["anchor_note"], "当前模型仅首帧外推，建议缩短单镜时长")

    def test_interpolation_declared_but_no_last_frame_honest_downgrade(self):
        intent = ProductionIntent(
            task_type="shot_video_with_anchor", intent_tags=[],
            first_frame_path="f.png", frame_anchor_mode="first_last_interpolate")
        with self.assertLogs("capabilities.frame_anchor", level="WARNING"):
            res = FrameAnchorCapability().build(intent)
        self.assertEqual(res["anchor_mode"], "first_frame")  # 诚实降级，不硬造假插值
        self.assertFalse(res["interpolation"])

    def test_backward_compat_when_mode_unset(self):
        # P0 行为：未写 frame_anchor_mode 时按首/尾帧存在性推导
        intent = ProductionIntent(
            task_type="shot_video_with_anchor", intent_tags=[],
            first_frame_path="f.png", last_frame_path="l.png")
        self.assertIsNone(intent.frame_anchor_mode)
        res = FrameAnchorCapability().build(intent)
        self.assertEqual(res["anchor_mode"], "interpolation")


class TestApplyAnchorModeViaBridge(unittest.TestCase):
    def test_bridge_writes_frame_anchor_mode(self):
        # 复用 intent_bridge（避免重复逻辑）：声明插值 + 双帧 → 写入 first_last_interpolate
        from director.intent_bridge import agent_outputs_to_intent
        seq = {"frame_anchor": {"mode": "first_last_interpolate",
                                "first_frame_path": "f.png", "last_frame_path": "l.png"}}
        intent = agent_outputs_to_intent({"subjects": [], "intent_tags": []}, seq)
        self.assertEqual(intent.frame_anchor_mode, "first_last_interpolate")
        # 仅声明插值但无尾帧 → 诚实外推
        seq2 = {"frame_anchor": {"mode": "first_last_interpolate",
                                 "first_frame_path": "f.png"}}
        intent2 = agent_outputs_to_intent({"subjects": [], "intent_tags": []}, seq2)
        self.assertEqual(intent2.frame_anchor_mode, "first_frame_only")


if __name__ == "__main__":
    unittest.main()
