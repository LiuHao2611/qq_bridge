#!/usr/bin/env python3
"""角色卡六步管线离线可验证测试（T8 / 清单 #1）。

目标：在不依赖 ComfyUI(:8188) / InsightFace 真实运行的前提下，证明 §3.3 角色卡
六步确定性管线的「逻辑闭环」：
  ① 五要素（无 ollama_fn 时跳过 LLM 抽取，走确定性分支）
  ② 能力匹配（mock match_capability → 时代+风格决定底模+LoRA）
  ③ 结构化 prompt 构建（build_generation_prompt）
  ④ derive_seed 确定性（同角色同姿势 seed 恒定）
  ⑤/⑥ 生成与品控（mock ComfyUI 提交 + 向量/品控，验证管线贯通）

外部依赖全部 mock，纯离线；断言聚焦「管线组装正确 + 选型推送正确」。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # server/

import ai_local
from intent_engine.parameter_injector import derive_seed


class CharacterCardV2OfflineTest(unittest.TestCase):
    def setUp(self):
        # 隔离全部外部依赖，使管线可离线跑通
        self._patchers = [
            mock.patch.object(ai_local, "generate_character_card",
                              return_value="/tmp/fake_card.png"),
            mock.patch.object(ai_local, "_extract_face_vector_from_image",
                              return_value=[0.1] * 512),
            mock.patch.object(ai_local, "_qc_character_card",
                              return_value={"passed": True, "score": 0.9}),
            mock.patch.object(ai_local, "_load_asset_attrs_for_card",
                              return_value={"name": "唐朝少女"}),
            mock.patch.object(ai_local, "match_capability",
                              return_value={
                                  "checkpoint": {"name": "sd15.safetensors"},
                                  "loras": [{"name": "tang_style.safetensors"}],
                              }),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def test_pipeline_offline_builds_prompt_and_publishes_selection(self):
        import realtime
        published = []
        with mock.patch.object(realtime, "publish_translator_selection",
                               side_effect=lambda *a, **k: published.append(a)):
            res = ai_local.generate_character_card_v2(
                asset_id="a1",
                setting_text="唐朝少女，红衣，端庄",
                style_tags=["古风"],
                era="唐",
                catalog=mock.MagicMock(),
                project_id="p_test",
            )

        # ③ prompt 非空
        self.assertTrue(res["positive_prompt"], "正向 prompt 不应为空")
        # ② capability 已解析
        self.assertIsNotNone(res["capability"])
        self.assertEqual(res["capability"]["checkpoint"]["name"], "sd15.safetensors")
        # ④ derive_seed 确定性：同角色同姿势 seed 恒定
        self.assertEqual(res["poses"][0]["seed"], derive_seed("唐朝少女", "front"))
        # ⑤/⑥ 管线贯通： poses 含 card_path / qc
        self.assertEqual(res["poses"][0]["card_path"], "/tmp/fake_card.png")
        self.assertTrue(res["poses"][0]["qc"]["passed"])
        # M3 选型推送： character_genesis 任务把「选了哪个底模/节点/seed」推给悬浮球
        self.assertEqual(len(published), 1)
        pid, trace_id, task_type, selected = published[0]
        self.assertEqual(pid, "p_test")
        self.assertEqual(task_type, "character_genesis")
        self.assertEqual(selected["checkpoint"], "sd15.safetensors")
        self.assertEqual(selected["seed"], derive_seed("唐朝少女", "front"))

    def test_pipeline_offline_no_project_id_skips_publish(self):
        import realtime
        published = []
        with mock.patch.object(realtime, "publish_translator_selection",
                               side_effect=lambda *a, **k: published.append(a)):
            res = ai_local.generate_character_card_v2(
                asset_id="a2",
                style_tags=["古风"],
                era="唐",
                catalog=mock.MagicMock(),
                project_id=None,  # 缺省跳过导演推送
            )
        self.assertEqual(len(published), 0, "无 project_id 不应推送选型")
        self.assertTrue(res["positive_prompt"])


if __name__ == "__main__":
    unittest.main()
