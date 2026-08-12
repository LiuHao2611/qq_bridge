# -*- coding: utf-8 -*-
"""角色卡生成接入意图翻译引擎（导演agent优化草稿 #1）单元测试。

验证：
  - character_card task_type 正确路由到 character_card_gen 模板；
  - 由结构化角色属性确定性拼装正向提示词（锚定资产，非自由 Prompt，满足铁律 1.1）；
  - 确定性 seed（同一角色跨次一致，修复「每次随机」；不同角色互异）；
  - 首张卡为纯文生图（不含 IP 节点 13/15/23/20）；
  - 模板占位符全部被填充（无残留 {{x}}）。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intent_engine as it


class TestCharacterCardIntent(unittest.TestCase):
    def _attrs(self):
        return {"name": "林浩", "age": 32, "gender": "男",
                "appearance": ["黑色短发", "单眼皮", "方脸", "中等身材"]}

    def test_task_type_and_deterministic_seed(self):
        a = self._attrs()
        i1 = it.build_character_card_intent(a, character_id="ROLE-001", style_tags=["写实", "电影感"])
        i2 = it.build_character_card_intent(a, character_id="ROLE-001", style_tags=["写实", "电影感"])
        self.assertEqual(i1["task_type"], "character_card")
        # 确定性：同角色两次 seed 必须一致
        self.assertEqual(i1["generation_params"]["seed"], i2["generation_params"]["seed"])
        # 不同角色 seed 必须互异
        s_b = it.build_character_card_intent(a, character_id="ROLE-002")["generation_params"]["seed"]
        self.assertNotEqual(i1["generation_params"]["seed"], s_b)

    def test_seed_is_positive_int31(self):
        seed = it.build_character_card_intent(self._attrs(), character_id="ROLE-X")["generation_params"]["seed"]
        self.assertIsInstance(seed, int)
        self.assertGreaterEqual(seed, 0)
        self.assertLess(seed, 2 ** 31)

    def test_positive_prompt_built_from_attributes(self):
        i = it.build_character_card_intent(self._attrs(), character_id="ROLE-001", style_tags=["写实", "电影感"])
        pos = i["prompt_text"]
        for kw in ("男", "青年", "黑色短发", "单眼皮", "方脸", "角色定妆照", "单人半身肖像"):
            self.assertIn(kw, pos, "正向提示词缺少属性关键词: %s" % kw)

    def test_age_bucket(self):
        # 12 -> 儿童；20 -> 青年；40 -> 中年；70 -> 老年
        self.assertIn("儿童", it._build_character_positive({"age": 10}, None))
        self.assertIn("青年", it._build_character_positive({"age": 20}, None))
        self.assertIn("中年", it._build_character_positive({"age": 40}, None))
        self.assertIn("老年", it._build_character_positive({"age": 70}, None))

    def test_translate_routes_to_character_card_template_no_ip(self):
        i = it.build_character_card_intent(self._attrs(), character_id="ROLE-001")
        wf = it.translate_intent_to_workflow(i)
        self.assertIn("3", wf)
        self.assertEqual(wf["3"]["class_type"], "KSampler")
        # 首张卡纯文生图，不应出现 IP 节点
        for n in ("13", "15", "23", "20"):
            self.assertNotIn(n, wf, "角色卡首图不应含 IP 节点 %s" % n)
        # 占位符全部填充
        raw = " ".join(str(v) for v in wf.values())
        self.assertEqual(re.findall(r"\{\{(\w+)\}\}", raw), [],
                         "模板存在未填充占位符")

    def test_default_portrait_aspect(self):
        gp = it.build_character_card_intent(self._attrs(), character_id="R")["generation_params"]
        self.assertEqual(gp["width"], 512)
        self.assertEqual(gp["height"], 768)

    def test_bound_assets_empty_first_card(self):
        i = it.build_character_card_intent(self._attrs(), character_id="R")
        self.assertEqual(i["bound_assets"], [], "首张卡无参考图（鸡生蛋环节）")


if __name__ == "__main__":
    unittest.main()
