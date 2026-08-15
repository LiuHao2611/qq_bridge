# -*- coding: utf-8 -*-
"""M5 全局风格锚点验证（手册 §2.6 / M5，遗漏 2 修复）。

证明：
  1. VisualControlAgent._decide_style_spec 产出结构化 {lighting,color_temperature,mood}
     （确定性，色温映射 warm=3200/cool=6500/neutral=5000）。
  2. 该 style_anchor 能经 intent_bridge 流转到 ProductionIntent.style_anchor。
  3. semantic 维度把全局风格词追加进每分镜 positive_prompt。
  4. consistency 维度在 wf 模式把全局风格参考图作为末端 style IPAdapter 注入
     （每分镜 workflow 均含风格约束 → 跨分镜统一画风）。
  5. db.save_style_anchor / get_project_style_anchor 往返持久化（style_anchors 表）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from director.intent_bridge import agent_outputs_to_intent
from intent_engine import ProductionIntent, GenerationParams
from capabilities import REGISTRY
from capabilities.workflow_builder import WorkflowBuilder


def _visual_data_anchor():
    return {
        "subjects": [{"asset_id": "ASSET-1", "role": "identity", "weight": 0.85}],
        "intent_tags": ["写实"],
        "style_anchor": {
            "lighting": "warm_cinematic",
            "color_temperature": 3200,
            "mood": "都市情感",
            "reference_image": "assets/style_anchor_1.png",
        },
    }


class TestDecideStyleSpec(unittest.TestCase):
    def _seg(self, text):
        return [{"scene_description": text}]

    def test_warm_maps_to_3200(self):
        from director.agents.visual_control import VisualControlAgent
        spec = VisualControlAgent._decide_style_spec(
            self._seg("咖啡馆 夕阳 室内 黄昏"), {}, "cinematic")
        self.assertEqual(spec["color_temperature"], 3200)
        self.assertEqual(spec["lighting"], "warm_cinematic")
        self.assertEqual(spec["mood"], "都市情感")

    def test_cool_maps_to_6500(self):
        from director.agents.visual_control import VisualControlAgent
        spec = VisualControlAgent._decide_style_spec(
            self._seg("深夜 办公室 医院 雨"), {}, "cinematic")
        self.assertEqual(spec["color_temperature"], 6500)
        self.assertEqual(spec["lighting"], "cool_cinematic")

    def test_neutral_maps_to_5000(self):
        from director.agents.visual_control import VisualControlAgent
        spec = VisualControlAgent._decide_style_spec(
            self._seg("一个抽象的空间"), {}, "cinematic")
        self.assertEqual(spec["color_temperature"], 5000)
        self.assertEqual(spec["lighting"], "neutral_balanced")

    def test_mood_override(self):
        from director.agents.visual_control import VisualControlAgent
        spec = VisualControlAgent._decide_style_spec(
            self._seg("咖啡馆"), {"mood": "悬疑"}, "cinematic")
        self.assertEqual(spec["mood"], "悬疑")

    def test_color_temperature_override(self):
        from director.agents.visual_control import VisualControlAgent
        spec = VisualControlAgent._decide_style_spec(
            self._seg("咖啡馆"), {"color_temperature": 4500}, "cinematic")
        self.assertEqual(spec["color_temperature"], 4500)


class TestStyleAnchorFlowsToIntent(unittest.TestCase):
    def test_style_anchor_flows_into_intent(self):
        intent = agent_outputs_to_intent(_visual_data_anchor(),
                                          {"camera": {}, "frame_anchor": {}})
        self.assertEqual(intent.style_anchor.get("lighting"), "warm_cinematic")
        self.assertEqual(intent.style_anchor.get("reference_image"),
                         "assets/style_anchor_1.png")
        self.assertEqual(intent.style_anchor.get("mood"), "都市情感")


class TestSemanticInjectsStyleWords(unittest.TestCase):
    def test_prompt_contains_style_lighting_and_mood(self):
        intent = ProductionIntent(
            task_type="shot_video_text2video",
            intent_tags=["写实"],
            style_anchor={"lighting": "warm_cinematic", "mood": "都市情感"},
            generation_params=GenerationParams(seed=1, width=512, height=512, steps=20),
        )
        d = REGISTRY["semantic"].build(intent)   # wf=None → dict
        prompt = d["positive_prompt"]
        self.assertIn("warm cinematic lighting", prompt)
        self.assertIn("都市情感", prompt)
        self.assertIn("cinematic", prompt)

    def test_no_anchor_no_extra_words(self):
        intent = ProductionIntent(
            task_type="shot_video_text2video",
            intent_tags=["写实"],
            generation_params=GenerationParams(seed=1, width=512, height=512, steps=20),
        )
        d = REGISTRY["semantic"].build(intent)
        self.assertNotIn("lighting", d["positive_prompt"].lower())


class TestConsistencyInjectsGlobalStyleIPAdapter(unittest.TestCase):
    def _intent(self):
        return ProductionIntent(
            task_type="shot_video_text2video",
            intent_tags=["写实"],
            style_anchor={"reference_image": "assets/style_anchor_1.png"},
            generation_params=GenerationParams(seed=1, width=512, height=512, steps=20),
        )

    def test_global_style_ipadapter_inserted(self):
        wf = WorkflowBuilder()
        wf.add_node("CheckpointLoaderSimple", {"ckpt_name": "rv.safetensors"},
                    title="底模")
        REGISTRY["consistency"].build(self._intent(), wf)
        # 含至少一个 IPAdapter 节点，且其中一个 title 标记「全局风格锚点」
        self.assertTrue(wf.has_node("IPAdapter"), "必须插入 IPAdapter（全局风格）")
        titles = [n.get("_meta", {}).get("title") for n in wf.nodes.values()]
        self.assertTrue(any("全局风格锚点" in (t or "") for t in titles))

    def test_dict_reports_style_anchor_applied(self):
        d = REGISTRY["consistency"].build(self._intent())
        self.assertTrue(d["style_anchor_applied"])

    def test_no_reference_image_no_global_injection(self):
        intent = ProductionIntent(
            task_type="shot_video_text2video",
            intent_tags=["写实"],
            style_anchor={},
            generation_params=GenerationParams(seed=1, width=512, height=512, steps=20),
        )
        d = REGISTRY["consistency"].build(intent)
        self.assertFalse(d["style_anchor_applied"])


class TestStyleAnchorDbRoundTrip(unittest.TestCase):
    def test_save_and_get(self):
        import db as _db
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        old_path = _db.DB_PATH
        _db.DB_PATH = tmp.name
        try:
            _db.init_db()
            aid = _db.save_style_anchor(
                "PROJ-X", "assets/style_anchor_1.png",
                "warm_cinematic", 3200, "都市情感")
            self.assertTrue(aid)
            row = _db.get_project_style_anchor("PROJ-X")
            self.assertIsNotNone(row)
            self.assertEqual(row["lighting"], "warm_cinematic")
            self.assertEqual(row["color_temperature"], 3200)
            self.assertEqual(row["mood"], "都市情感")
            self.assertEqual(row["reference_image"], "assets/style_anchor_1.png")
        finally:
            _db.DB_PATH = old_path
            try:
                os.remove(tmp.name)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
