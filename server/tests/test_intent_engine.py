# -*- coding: utf-8 -*-
"""意图翻译引擎 M1 单元测试 + 集成测试（手册 §7.1 / §7.2）。

覆盖：
  - production_intent：JSON 序列化/反序列化字段一致；
  - resource_catalog：find_best_checkpoint 双重过滤（显存+组件兼容）+ 语义匹配；
  - node_family_mapper：resolve 自动降级 + FeatureUnavailable；
  - template_engine：节点 ID 动态映射无残留语义名；
  - parameter_injector：占位符全替换 + 类型/范围校验；derive_seed 同入同出异入异出；
  - 集成：ProductionIntent → translate() → workflow 结构合法 + last_selection 追溯。
"""
import os
import sys
import asyncio
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intent_engine as ie
from intent_engine import (
    BoundAsset, GenerationParams, StyleConstraints, ProductionIntent,
    ResourceCatalog, NodeFamilyMapper, TemplateEngine, ParameterInjector,
    derive_seed, IntentTranslator, FeatureUnavailable, InjectError,
)


def _fake_object_info():
    """构造一个最小但自洽的 ComfyUI /object_info 返回（用于离线测试）。"""
    def _node(*fields):
        req = {}
        for f in fields:
            if isinstance(f, tuple):
                fname, enum = f
            else:
                fname, enum = f, ["__enum__"]
            req[fname] = [enum, ""]
        return {"input": {"required": req}}
    return {
        "CheckpointLoaderSimple": _node(
            ("ckpt_name", ["Realistic_Vision_V5.0.safetensors",
                           "AnythingV5.safetensors", "SDXL_写实.safetensors"])),
        "LoraLoader": _node(("lora_name", ["some_lora.safetensors"])),
        "IPAdapterFaceID": _node(
            ("ipadapter_file", ["ip-adapter-faceid_sd15.safetensors"]),
            ("clip_vision", ["CLIP-ViT-H-14.safetensors"]),
            ("insightface", ["buffalo_l"])),
        "ControlNetLoader": _node(
            ("control_net_name", ["control_v11p_sd15_openpose.safetensors"])),
        "VAELoader": _node(("vae_name", ["vae-ft-mse-840000-ema.safetensors"])),
        "CLIPVisionLoader": _node(("clip_name", ["CLIP-ViT-H-14.safetensors"])),
        "KSampler": _node(),
        "EmptyLatentImage": _node(),
        "VAEDecode": _node(),
        "SaveImage": _node(),
        "LoadImage": _node(("image", ["first.png"])),
        "VAEEncode": _node(),
    }


def _fake_catalog():
    return {
        "checkpoints": [
            {"name": "Realistic_Vision_V5.0.safetensors", "type": "sd1.5",
             "auto_tags": ["写实", "真人"], "user_tags": [], "vram_required_mb": 4000},
            {"name": "AnythingV5.safetensors", "type": "sd1.5",
             "auto_tags": ["动漫"], "user_tags": [], "vram_required_mb": 4000},
            {"name": "SDXL_写实.safetensors", "type": "sdxl",
             "auto_tags": ["写实"], "user_tags": [], "vram_required_mb": 8000},
        ],
        "loras": [],
        "ip_adapters": [
            {"name": "ip-adapter-faceid_sd15.safetensors", "type": "ipadapter",
             "compatible_checkpoints": ["sd1.5"]},
        ],
        "controlnets": [],
        "vaes": [],
        "video_models": [],
        "node_families": {},
    }


class TestProductionIntent(unittest.TestCase):
    def test_json_roundtrip(self):
        intent = ProductionIntent(
            task_type="character_genesis",
            intent_tags=["写实", "电影感"],
            bound_assets=[BoundAsset("ASSET-1", "character", "face", 0.85, "VEC")],
            generation_params=GenerationParams(pose="front", seed=424242,
                                               width=512, height=768, steps=30, cfg_scale=7.0),
            style_constraints=StyleConstraints(color_temperature=3200, lighting="low_key_neon"),
            optional_controls={"pose_control": False},
            first_frame_path=None, last_frame_path=None)
        d = intent.to_dict()
        self.assertEqual(d["task_type"], "character_genesis")
        self.assertEqual(d["intent_tags"], ["写实", "电影感"])
        self.assertEqual(d["bound_assets"][0]["asset_id"], "ASSET-1")
        back = ProductionIntent.from_dict(d)
        self.assertEqual(back.to_dict(), d)

    def test_invalid_task_type(self):
        with self.assertRaises(ValueError):
            ProductionIntent(task_type="bogus", intent_tags=[],
                             bound_assets=[], generation_params=GenerationParams())


class TestResourceCatalog(unittest.TestCase):
    def _cat(self, vram=6000):
        return ResourceCatalog(catalog=_fake_catalog(),
                               object_info=_fake_object_info(),
                               available_vram_mb=vram)

    def test_semantic_match(self):
        cat = self._cat()
        cp = cat.find_best_checkpoint(["写实"])
        self.assertEqual(cp["name"], "Realistic_Vision_V5.0.safetensors")

    def test_semantic_match_anime(self):
        cat = self._cat()
        cp = cat.find_best_checkpoint(["动漫"])
        self.assertEqual(cp["name"], "AnythingV5.safetensors")

    def test_vram_hardware_filter(self):
        # 6GB 显存下 SDXL(8000MB) 必须被排除，写实匹配落到 SD1.5
        cat = self._cat(vram=6000)
        cp = cat.find_best_checkpoint(["写实"])
        self.assertNotEqual(cp["name"], "SDXL_写实.safetensors")
        self.assertEqual(cp["name"], "Realistic_Vision_V5.0.safetensors")

    def test_no_match_raises(self):
        cat = self._cat()
        with self.assertRaises(FeatureUnavailable):
            cat.find_best_checkpoint(["水彩"])

    def test_require_ipadapter_compat(self):
        cat = self._cat()
        # require_ipadapter=True 时仍能在 sd1.5 兼容集中选中（不会因缺 sdxl ip 而炸）
        cp = cat.find_best_checkpoint(["写实"], require_ipadapter=True)
        self.assertEqual(cp["type"], "sd1.5")

    def test_find_ip_adapter_and_clip(self):
        cat = self._cat()
        ip = cat.find_best_ip_adapter("Realistic_Vision_V5.0.safetensors")
        self.assertEqual(ip["name"], "ip-adapter-faceid_sd15.safetensors")
        self.assertEqual(cat.find_clip_vision(), "CLIP-ViT-H-14.safetensors")


class TestNodeFamilyMapper(unittest.TestCase):
    def setUp(self):
        self.oi = _fake_object_info()
        self.mapper = NodeFamilyMapper()

    def test_resolve_autofallback(self):
        # 优先级 IPAdapterFaceID 缺失 → 降级到 IPAdapterPlus（若装）
        oi = dict(self.oi)
        oi.pop("IPAdapterFaceID")
        oi["IPAdapterPlus"] = {"input": {"required": {}}}
        res = self.mapper.resolve("face_lock", oi)
        self.assertEqual(res["node_class"], "IPAdapterPlus")

    def test_resolve_image_generation(self):
        res = self.mapper.resolve("image_generation", self.oi)
        self.assertEqual(res["node_class"], "KSampler")

    def test_resolve_none_available_raises(self):
        oi = {"KSampler": {}}
        with self.assertRaises(FeatureUnavailable):
            self.mapper.resolve("face_lock", oi)

    def test_detect_node_families(self):
        oi = dict(self.oi)
        oi["WanVideoSampler"] = {"input": {"required": {}}}
        detected = self.mapper.detect_node_families(oi)
        self.assertEqual(detected["video_generation"], "WanVideoSampler")


class TestTemplateEngine(unittest.TestCase):
    def setUp(self):
        self.te = TemplateEngine()

    def test_no_residual_semantic(self):
        wf, sem, schema = self.te.load_template("character_genesis")
        ids = set(wf.keys())
        # 所有 [引用, 槽位] 的首元素必须是已存在的节点键（合法映射），
        # 不能是未解析的语义名。
        for nid, node in wf.items():
            for k, v in node.get("inputs", {}).items():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                    self.assertIn(v[0], ids,
                                  "残留语义引用 %s @ %s.%s" % (v, nid, k))
        # ksampler.model 应指向 ip_adapter 的节点 ID（字符串，与 ComfyUI 一致）
        self.assertIsInstance(wf["5"]["inputs"]["model"], list)
        self.assertEqual(len(wf["5"]["inputs"]["model"]), 2)
        self.assertIn(wf["5"]["inputs"]["model"][0], ids)

    def test_discover_includes_all(self):
        ids = self.te.get_template_ids()
        for tid in ("character_genesis", "scene_genesis", "shot_txt2img",
                    "shot_img2img", "shot_video_text2video", "shot_video_with_anchor"):
            self.assertIn(tid, ids)

    def test_user_custom_override(self):
        # user_custom 同名覆盖系统模板（目录存在即验证发现机制）
        ids = self.te.get_template_ids()
        self.assertNotIn("__override_test__", ids)  # 无覆盖时无此 id


class TestParameterInjector(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "seed": {"type": "int", "required": True},
            "cfg_scale": {"type": "float", "range": [1.0, 15.0]},
            "width": {"type": "int", "default": 512},
            "positive_prompt": {"type": "string"},
        }

    def test_type_and_range(self):
        inj = ParameterInjector(self.schema)
        inj.set("seed", 424242)
        inj.set("width", "512")  # 字符串转 int
        with self.assertRaises(InjectError):
            inj.set("cfg_scale", 20)  # 越界

    def test_inject_no_residual(self):
        inj = ParameterInjector(self.schema)
        inj.set("seed", 42).set("cfg_scale", 7.0).set("positive_prompt", "a, b")
        inj.set_defaults()
        wf = {"1": {"class_type": "X", "inputs": {
            "seed": "{{seed}}", "cfg": "{{cfg_scale}}",
            "text": "pic {{positive_prompt}} end", "w": "{{width}}"}}}
        out = inj.inject(wf)
        self.assertNotIn("{{", str(out))
        self.assertEqual(out["1"]["inputs"]["seed"], 42)       # 数值类型
        self.assertEqual(out["1"]["inputs"]["w"], 512)
        self.assertEqual(out["1"]["inputs"]["text"], "pic a, b end")

    def test_residual_raises(self):
        inj = ParameterInjector(self.schema)
        inj.set("seed", 1)
        wf = {"1": {"class_type": "X", "inputs": {"s": "{{missing}}"}}}
        with self.assertRaises(InjectError):
            inj.inject(wf)


class TestDeriveSeed(unittest.TestCase):
    def test_deterministic(self):
        a = derive_seed("林默", "front")
        b = derive_seed("林默", "front")
        self.assertEqual(a, b)
        self.assertGreaterEqual(a, 0)
        self.assertLess(a, 2 ** 31)

    def test_distinct(self):
        self.assertNotEqual(derive_seed("林默", "front"), derive_seed("林默", "side"))
        self.assertNotEqual(derive_seed("林默", "front"), derive_seed("林浩", "front"))


class TestTranslateIntegration(unittest.TestCase):
    def _translator(self):
        return IntentTranslator(catalog=_fake_catalog(),
                                object_info=_fake_object_info(),
                                available_vram_mb=6000)

    def _character_intent(self, with_vector=True):
        return ProductionIntent(
            task_type="character_genesis",
            intent_tags=["写实"],
            bound_assets=[BoundAsset("ASSET-1", "character", "face", 0.85,
                                     "BASE64VEC" if with_vector else None)],
            generation_params=GenerationParams(pose="front", seed=424242,
                                               width=512, height=768, steps=30, cfg_scale=7.0),
            style_constraints=StyleConstraints(),
            optional_controls={}, first_frame_path=None, last_frame_path=None)

    def test_translate_character_genesis(self):
        tr = self._translator()
        wf = asyncio.run(tr.translate(self._character_intent()))
        self.assertNotIn("{{", str(wf))
        # 结构合法：每个节点有 class_type，所有引用指向存在节点
        ids = set(wf.keys())
        for nid, node in wf.items():
            self.assertIn("class_type", node)
            for k, v in node.get("inputs", {}).items():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
                    self.assertIn(str(v[0]), ids)
        # selected 追溯
        self.assertEqual(tr.last_selection["node"], "IPAdapterFaceID")
        self.assertEqual(tr.last_selection["checkpoint"],
                         "Realistic_Vision_V5.0.safetensors")
        self.assertEqual(tr.last_selection["ip_adapter"],
                         "ip-adapter-faceid_sd15.safetensors")

    def test_translate_character_missing_vector_raises(self):
        tr = self._translator()
        with self.assertRaises(InjectError):
            asyncio.run(tr.translate(self._character_intent(with_vector=False)))

    def test_translate_shot_txt2img(self):
        tr = self._translator()
        intent = ProductionIntent(
            task_type="shot_txt2img", intent_tags=["写实"],
            bound_assets=[], generation_params=GenerationParams(seed=123, steps=28),
            style_constraints=StyleConstraints(), optional_controls={})
        wf = asyncio.run(tr.translate(intent))
        self.assertNotIn("{{", str(wf))
        self.assertEqual(tr.last_selection["node"], "KSampler")

    def test_translate_shot_img2img_with_anchor(self):
        tr = self._translator()
        intent = ProductionIntent(
            task_type="shot_img2img", intent_tags=["写实"],
            bound_assets=[], generation_params=GenerationParams(seed=7),
            style_constraints=StyleConstraints(), optional_controls={},
            first_frame_path="D:/ai/first.png")
        wf = asyncio.run(tr.translate(intent))
        self.assertNotIn("{{", str(wf))
        # init_image 占位符被填充
        flat = str(wf)
        self.assertIn("D:/ai/first.png", flat)

    def test_translate_video_text2video(self):
        oi = _fake_object_info()
        oi["WanVideoSampler"] = {"input": {"required": {}}}
        tr = IntentTranslator(catalog=_fake_catalog(), object_info=oi, available_vram_mb=6000)
        intent = ProductionIntent(
            task_type="shot_video_text2video", intent_tags=["写实"],
            bound_assets=[], generation_params=GenerationParams(seed=9, num_frames=81, fps=16),
            style_constraints=StyleConstraints(), optional_controls={})
        wf = asyncio.run(tr.translate(intent))
        self.assertNotIn("{{", str(wf))
        self.assertEqual(tr.last_selection["node"], "WanVideoSampler")


if __name__ == "__main__":
    unittest.main()
