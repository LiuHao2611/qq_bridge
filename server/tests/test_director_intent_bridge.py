# -*- coding: utf-8 -*-
"""M4 导演 Agent 意图化·意图流转验证（手册 §2.6 / M4）。

证明：
  1. 子 Agent 的结构化输出（subjects/intent_tags/camera/frame_anchor）能经
     agent_outputs_to_intent 规整为合法 ProductionIntent；
  2. 该意图能流转到 IntentTranslator.translate（M3 匹配算法），离线（catalog +
     object_info 注入，零网络）产出合法 workflow；
  3. 同一意图仅改 object_info 即可在 LTX ↔ Wan 间切换（模型无关，证明意图流转
     而非硬编码）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from director.intent_bridge import agent_outputs_to_intent, build_workflow_from_intent


def _object_info():
    def _node(*fields):
        req = {}
        for f in fields:
            fname, enum = f if isinstance(f, tuple) else (f, ["__enum__"])
            req[fname] = [enum, ""]
        return {"input": {"required": req}}
    return {
        "CheckpointLoaderSimple": _node(
            ("ckpt_name", ["Realistic_Vision_V5.0.safetensors"])),
        "CLIPVisionLoader": _node(("clip_name", ["CLIP-ViT-H.safetensors"])),
        "KSampler": _node(), "EmptyLatentImage": _node(), "VAEDecode": _node(),
        "SaveImage": _node(), "LoadImage": _node(("image", ["first.png"])),
        "VAEEncode": _node(), "VAELoader": _node(("vae_name", ["vae.safetensors"])),
        "LoraLoader": _node(("lora_name", ["some.safetensors"])),
        "IPAdapterFaceID": _node(("ipadapter_file", ["ip.safetensors"])),
        "CLIPTextEncode": _node(("text", ["__enum__"])),
        "LTXVImgToVideo": _node(("num_frames", [["49"], ""]), ("fps", [["25"], ""])),
        "WanVideoSampler": _node(("num_frames", [["81"], ""]), ("fps", [["16"], ""])),
        "VHS_VideoCombine": _node(("audio", ["a.wav"])),
    }


def _catalog():
    return {
        "checkpoints": [
            {"name": "Realistic_Vision_V5.0.safetensors", "type": "sd1.5",
             "auto_tags": ["写实"], "user_tags": [], "vram_required_mb": 4000},
        ],
        "loras": [], "ip_adapters": [], "controlnets": [],
        "vaes": [], "video_models": [], "node_families": {},
    }


def _visual_data():
    return {
        "subjects": [{"asset_id": "ASSET-1", "weight": 0.85}],
        "intent_tags": ["写实", "电影感"],
    }


def _visual_data_multi():
    return {
        "subjects": [
            {"asset_id": "ASSET-1", "role": "identity", "weight": 0.85},
            {"asset_id": "ASSET-2", "role": "outfit", "weight": 0.5},
        ],
        "intent_tags": ["写实"],
    }


def _seq_data(enable_tail=False):
    return {
        "camera": {"movement": "push_in", "shot_type": "medium"},
        "frame_anchor": {"mode": "first_frame_only" if enable_tail else "none",
                         "first_frame_path": None, "last_frame_path": None,
                         "enable_tail_frame": enable_tail},
    }


def _seq_data_spatial():
    return {
        "camera": {"movement": "static", "shot_type": "medium"},
        "frame_anchor": {"mode": "none", "first_frame_path": None, "last_frame_path": None,
                         "enable_tail_frame": False},
        "spatial": {"composition": "two_shot", "subjects": [
            {"asset_id": "ASSET-1", "position": "left", "orientation": "facing_right",
             "gaze": "to_ASSET-2"},
            {"asset_id": "ASSET-2", "position": "right", "orientation": "facing_left",
             "gaze": "to_ASSET-1"},
        ]},
    }


class TestAgentOutputsToIntent(unittest.TestCase):
    def test_intent_built_from_agent_outputs(self):
        intent = agent_outputs_to_intent(_visual_data(), _seq_data(),
                                          {"seed": 7, "num_frames": 81, "fps": 16})
        self.assertEqual(intent.task_type, "shot_video_text2video")
        self.assertEqual([a.asset_id for a in intent.bound_assets], ["ASSET-1"])
        self.assertEqual(intent.optional_controls["camera_motion"], "push_in")
        self.assertIn("写实", intent.intent_tags)


class TestM4RoleAndSpatial(unittest.TestCase):
    def test_subjects_carry_role(self):
        # 手册 M4 遗漏 1：subjects 透传 role（参考按作用绑定）
        intent = agent_outputs_to_intent(_visual_data_multi(), _seq_data_spatial())
        roles = {s.asset_id: s.role for s in intent.subjects}
        self.assertEqual(roles["ASSET-1"], "identity")
        self.assertEqual(roles["ASSET-2"], "outfit")

    def test_spatial_flows_into_intent(self):
        # 手册 M4 遗漏 3：空间站位蓝图透传
        intent = agent_outputs_to_intent(_visual_data_multi(), _seq_data_spatial())
        self.assertEqual(intent.spatial["composition"], "two_shot")
        self.assertEqual(len(intent.spatial["subjects"]), 2)
        self.assertEqual(intent.spatial["subjects"][0]["position"], "left")
        self.assertEqual(intent.spatial["subjects"][1]["position"], "right")

    def test_anchor_intent_when_frames_present(self):
        sd = _seq_data(enable_tail=True)
        sd["frame_anchor"]["first_frame_path"] = "first.png"
        intent = agent_outputs_to_intent(_visual_data(), sd)
        self.assertEqual(intent.task_type, "shot_video_with_anchor")
        self.assertEqual(intent.first_frame_path, "first.png")


class TestIntentFlowToTranslator(unittest.TestCase):
    def _flow(self, preferred):
        intent = agent_outputs_to_intent(_visual_data(), _seq_data(),
                                          {"seed": 7, "num_frames": 81, "fps": 16})
        wf, sel = build_workflow_from_intent(
            intent, catalog=_catalog(), object_info=_object_info(),
            user_prefs={"settings.preferred_video_model": preferred})
        return wf, sel

    def test_intent_flows_to_translator_ltx(self):
        wf, sel = self._flow("LTXVImgToVideo")
        self.assertEqual(sel["node"], "LTXVImgToVideo")
        self.assertNotIn("{{", str(wf))

    def test_intent_flows_to_translator_wan(self):
        wf, sel = self._flow("WanVideoSampler")
        self.assertEqual(sel["node"], "WanVideoSampler")
        self.assertNotIn("{{", str(wf))

    def test_same_intent_switches_model_not_code(self):
        _, sel_ltx = self._flow("LTXVImgToVideo")
        _, sel_wan = self._flow("WanVideoSampler")
        self.assertNotEqual(sel_ltx["node"], sel_wan["node"])
        # 同一意图 → 不同模型节点：证明走 M3 匹配算法，而非改代码


if __name__ == "__main__":
    unittest.main()
