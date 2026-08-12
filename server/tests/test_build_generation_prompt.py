# -*- coding: utf-8 -*-
"""M2 结构化提示词 / 能力匹配 / 五要素提取 离线单元测试（可在无 GPU/无模型环境跑）。"""
import os
import sys
import unittest

_SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)

import ai_local as al
from intent_engine.errors import FeatureUnavailable


class TestBuildGenerationPrompt(unittest.TestCase):

    def test_contains_trigger_words_dedup_and_order(self):
        intent = {
            "task_type": "shot_character_img",
            "intent_tags": ["写实", "写实", "电影感"],  # 重复应去重
            "style_constraints": {"color_temperature": 2800, "lighting": "soft light"},
        }
        subj = "林浩，25岁男性"
        prompt = al.build_generation_prompt(intent, subject=subj)
        self.assertIn("photorealistic", prompt)        # 写实触发词
        self.assertIn("cinematic lighting", prompt)     # 电影感触发词
        self.assertIn("warm color tone", prompt)        # 色温 <3000 → 暖
        self.assertIn("soft light", prompt)             # 光影
        self.assertIn("character reference sheet", prompt)  # 角色取景词
        self.assertEqual(prompt.count("photorealistic"), 1)  # 去重
        self.assertTrue(prompt.startswith(subj))        # 主体保序在首位

    def test_scene_task_type_words(self):
        intent = {"task_type": "shot_scene_img", "intent_tags": ["水彩绘本"]}
        prompt = al.build_generation_prompt(intent)
        self.assertIn("watercolor illustration", prompt)
        self.assertIn("environment concept art", prompt)

    def test_color_temp_neutral_and_cool_and_invalid(self):
        self.assertEqual(al._color_temp_word(4000), "neutral color tone")
        self.assertEqual(al._color_temp_word(6500), "cool color tone")
        self.assertIsNone(al._color_temp_word(None))
        self.assertIsNone(al._color_temp_word("abc"))

    def test_negative_prompt_keywords(self):
        neg = al.build_character_negative_prompt()
        for kw in ("deformed hands", "extra fingers", "bad anatomy", "low quality", "multiple characters"):
            self.assertIn(kw, neg)

    def test_build_prompt_accepts_dataclass_intent(self):
        # 用 dict 模拟 to_dict 行为即可；ProductionIntent 含 to_dict → hasattr 分支
        class _Fake:
            def to_dict(self):
                return {"task_type": "shot_character_img", "intent_tags": ["写实"]}
        prompt = al.build_generation_prompt(_Fake())
        self.assertIn("photorealistic", prompt)


class TestMatchCapability(unittest.TestCase):

    def _catalog(self, era_lora_name):
        return {
            "checkpoints": [
                {"name": "SDXL写实", "available": True, "auto_tags": ["写实"],
                 "user_tags": [], "vram_required_mb": 4000},
            ],
            "loras": [
                {"name": era_lora_name, "available": True, "auto_tags": ["民国"], "user_tags": []},
            ],
        }

    def test_era_match_selects_lora(self):
        cap = al.match_capability("民国", ["写实"], self._catalog("minguo_lora"),
                                  available_vram_mb=6000)
        self.assertEqual(cap["checkpoint"]["name"], "SDXL写实")
        self.assertEqual(cap["loras"][0]["name"], "minguo_lora")

    def test_style_tag_overlap_scored(self):
        # 两个底模，标签命中更高者胜
        cat = {
            "checkpoints": [
                {"name": "CP-A", "available": True, "auto_tags": ["动漫"], "user_tags": []},
                {"name": "CP-B", "available": True, "auto_tags": ["写实", "动漫"], "user_tags": []},
            ],
            "loras": [],
        }
        cap = al.match_capability("现代", ["动漫", "写实"], cat)
        self.assertEqual(cap["checkpoint"]["name"], "CP-B")

    def test_no_checkpoint_raises_feature_unavailable(self):
        cat = {"checkpoints": [], "loras": []}
        with self.assertRaises(FeatureUnavailable):
            al.match_capability("现代", ["写实"], cat)

    def test_vram_filter_excludes_too_large(self):
        cat = {
            "checkpoints": [
                {"name": "BIG", "available": True, "auto_tags": ["写实"], "user_tags": [], "vram_required_mb": 8000},
                {"name": "SMALL", "available": True, "auto_tags": ["写实"], "user_tags": [], "vram_required_mb": 2000},
            ],
            "loras": [],
        }
        cap = al.match_capability("现代", ["写实"], cat, available_vram_mb=6000)
        self.assertEqual(cap["checkpoint"]["name"], "SMALL")  # BIG 超显存被排除


class TestExtractFiveElements(unittest.TestCase):

    def test_extract_five_elements_ok(self):
        fake = ('```json\n'
                '{"age":"25","appearance":"黑发剑眉","clothing":"长衫",'
                '"temperament":"沉稳","era":"民国"}\n```')
        data = al.extract_five_elements("林浩是个沉稳的青年", lambda p: fake)
        self.assertEqual(data["age"], "25")
        self.assertEqual(data["era"], "民国")
        self.assertEqual(data["clothing"], "长衫")

    def test_extract_missing_key_raises(self):
        fake = '{"age":"25","appearance":"x","clothing":"y","temperament":"z"}'  # 缺 era
        with self.assertRaises(ValueError):
            al.extract_five_elements("设定", lambda p: fake)

    def test_extract_empty_llm_raises(self):
        with self.assertRaises(ValueError):
            al.extract_five_elements("设定", lambda p: "")

    def test_parse_llm_json_tolerates_noise(self):
        raw = "以下为结果：{\"age\":\"30\"} 完毕"
        self.assertEqual(al._parse_llm_json(raw)["age"], "30")


if __name__ == "__main__":
    unittest.main()
