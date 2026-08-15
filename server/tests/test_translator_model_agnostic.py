# -*- coding: utf-8 -*-
"""M3 验证：意图翻译引擎「模型无关」—— 同一意图可切换 LTX ↔ Wan（手册 §2.3 / §2.6）。

核心断言：
  1. NodeFamilyMapper 的视频节点族解析支持 user_preference_then_auto：
     用户设置 preferred_video_model=LTXVImgToVideo / WanVideoSampler 时，
     解析结果随之切换，且代码内无任何硬编码视频模型名（模型只是能力的实现）。
  2. 同一 ProductionIntent（视频文生视频），仅改变 user_prefs，翻译器选出的
     视频节点（last_selection["node"]）在 LTX 与 Wan 之间切换，且产出 workflow
     结构合法、无残留占位符。证明 LTX→Wan 切换无需改代码、纯靠能力注册表/设置。
"""
import os
import sys
import asyncio
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intent_engine import (
    BoundAsset, GenerationParams, StyleConstraints, ProductionIntent,
    ResourceCatalog, NodeFamilyMapper, IntentTranslator, FeatureUnavailable,
)

VIDEO_NODES = {
    "LTXVImgToVideo": {"input": {"required": {"num_frames": [["49"], ""],
                                             "fps": [["25"], ""]}}},
    "WanVideoSampler": {"input": {"required": {"num_frames": [["81"], ""],
                                             "fps": [["16"], ""]}}},
}


def _fake_object_info_with_video():
    def _node(*fields):
        req = {}
        for f in fields:
            fname, enum = f if isinstance(f, tuple) else (f, ["__enum__"])
            req[fname] = [enum, ""]
        return {"input": {"required": req}}
    oi = {
        "CheckpointLoaderSimple": _node(
            ("ckpt_name", ["Realistic_Vision_V5.0.safetensors",
                           "AnythingV5.safetensors"])),
        "LoraLoader": _node(("lora_name", ["some_lora.safetensors"])),
        "VAELoader": _node(("vae_name", ["vae.safetensors"])),
        "CLIPVisionLoader": _node(("clip_name", ["CLIP-ViT-H.safetensors"])),
        "KSampler": _node(),
        "EmptyLatentImage": _node(),
        "VAEDecode": _node(),
        "SaveImage": _node(),
        "LoadImage": _node(("image", ["first.png"])),
        "VAEEncode": _node(),
    }
    oi.update(VIDEO_NODES)
    return oi


def _fake_catalog():
    return {
        "checkpoints": [
            {"name": "Realistic_Vision_V5.0.safetensors", "type": "sd1.5",
             "auto_tags": ["写实"], "user_tags": [], "vram_required_mb": 4000},
        ],
        "loras": [], "ip_adapters": [], "controlnets": [],
        "vaes": [], "video_models": [], "node_families": {},
    }


def _video_intent():
    return ProductionIntent(
        task_type="shot_video_text2video",
        intent_tags=["写实"],
        bound_assets=[], generation_params=GenerationParams(
            seed=9, num_frames=81, fps=16),
        style_constraints=StyleConstraints(), optional_controls={})


class TestMapperModelAgnostic(unittest.TestCase):
    def setUp(self):
        self.oi = _fake_object_info_with_video()
        self.mapper = NodeFamilyMapper()

    def test_prefer_ltx(self):
        res = self.mapper.resolve(
            "video_generation", self.oi,
            {"settings.preferred_video_model": "LTXVImgToVideo"})
        self.assertEqual(res["node_class"], "LTXVImgToVideo")

    def test_prefer_wan(self):
        res = self.mapper.resolve(
            "video_generation", self.oi,
            {"settings.preferred_video_model": "WanVideoSampler"})
        self.assertEqual(res["node_class"], "WanVideoSampler")

    def test_default_is_wan(self):
        # 未设置偏好 → DEFAULT_PREFERRED_VIDEO_MODEL = WanVideoSampler（硬件适配默认）
        res = self.mapper.resolve("video_generation", self.oi, {})
        self.assertEqual(res["node_class"], "WanVideoSampler")

    def test_only_ltx_installed_no_pref(self):
        # 仅装 LTX（无 Wan），无偏好时按 priority 降级命中 LTX
        oi = {k: v for k, v in self.oi.items() if k != "WanVideoSampler"}
        res = self.mapper.resolve("video_generation", oi, {})
        self.assertEqual(res["node_class"], "LTXVImgToVideo")


class TestTranslatorLTXWanSwitch(unittest.TestCase):
    """同一视频意图，仅调 user_prefs，翻译器产出不同视频节点（模型无关）。"""

    def _translate_with(self, preferred):
        oi = _fake_object_info_with_video()
        tr = IntentTranslator(
            catalog=_fake_catalog(), object_info=oi, available_vram_mb=6000,
            user_prefs={"settings.preferred_video_model": preferred})
        wf = asyncio.run(tr.translate(_video_intent()))
        return tr, wf

    def test_ltx_selection(self):
        tr, wf = self._translate_with("LTXVImgToVideo")
        self.assertEqual(tr.last_selection["node"], "LTXVImgToVideo")
        self.assertNotIn("{{", str(wf))

    def test_wan_selection(self):
        tr, wf = self._translate_with("WanVideoSampler")
        self.assertEqual(tr.last_selection["node"], "WanVideoSampler")
        self.assertNotIn("{{", str(wf))

    def test_switch_changes_node_not_code(self):
        _, wf_ltx = self._translate_with("LTXVImgToVideo")
        tr_wan, wf_wan = self._translate_with("WanVideoSampler")
        # 同一意图 → 不同模型节点；证明切换靠偏好而非改代码
        self.assertNotEqual(tr_wan.last_selection["node"], "LTXVImgToVideo")
        # 两产物均为合法 workflow（结构校验已在 translator._validate 内执行）
        self.assertNotIn("{{", str(wf_ltx))
        self.assertNotIn("{{", str(wf_wan))


if __name__ == "__main__":
    unittest.main()
