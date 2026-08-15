# -*- coding: utf-8 -*-
"""增强②：Phase2 分场提示词注入 P1 角色摘要的纯函数验证（机器判定，不依赖 LLM/网络）。

对齐用户要求：把 P1 已锁定角色（身份卡）作为上下文给拆分 AI，提升 characters/refs 抽取准确度。
无创性：cast_summary=None 时输出与历史完全一致（不破坏现有握手 / 已验证行为）。
运行：server/.venv/Scripts/python.exe -m pytest tests/test_p2_cast_summary.py -q
"""
import unittest

from ai_local import _split_story_prompt


class TestSplitStoryCastSummary(unittest.TestCase):
    def test_prompt_excludes_cast_when_none(self):
        # 无创性：历史行为——不含「已知角色」段，故事原文照常出现
        p = _split_story_prompt("小明和小红去公园散步。")
        self.assertNotIn("已知角色", p)
        self.assertIn("小明和小红去公园散步", p)

    def test_prompt_includes_cast_when_provided(self):
        cast = [{"name": "周岩", "desc": "男主"}, {"name": "林夏", "desc": "女主"}]
        p = _split_story_prompt("周岩和林夏在雨中相遇。", cast_summary=cast)
        self.assertIn("已知角色", p)
        self.assertIn("周岩", p)
        self.assertIn("林夏", p)
        # 提示语应鼓励优先使用已知角色，且不覆盖「不新增未出现角色」约束
        self.assertIn("优先", p)

    def test_prompt_signature_backward_compatible(self):
        # 老调用方（script_parser 等）仅传 story 不应报错（签名兼容）
        p = _split_story_prompt("故事内容")
        self.assertTrue(isinstance(p, str) and p)


if __name__ == "__main__":
    unittest.main()
