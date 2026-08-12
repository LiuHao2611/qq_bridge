# -*- coding: utf-8 -*-
"""铁律 1.2 覆盖测试：face_restore / ControlNet 文生图 改为模板优先 + 内联手搓兜底。

运行方式（项目根）：
    python -m unittest server/tests/test_typed_templates.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ai_local
import intent_translator


class TestTypedTemplateFill(unittest.TestCase):
    """验证 workflow_templates 占位符填充：数值占位符须为真实数字（非字符串）。"""

    def test_controlnet_numeric_types(self):
        wf = intent_translator.translate_typed_workflow("shot_image_controlnet", {
            "checkpoint": "v1-5.safetensors",
            "positive_prompt": "a rainy night",
            "negative_prompt": "low quality",
            "seed": 42, "steps": 28, "cfg": 8.5, "width": 768, "height": 1024,
            "ref_image": "ref.png", "preprocess_node": "DepthPreprocessor",
            "control_net_name": "control_v11p_sd15_depth.pth", "control_weight": 0.7,
        })
        self.assertEqual(wf["3"]["inputs"]["seed"], 42)
        self.assertEqual(wf["3"]["inputs"]["steps"], 28)
        self.assertIsInstance(wf["3"]["inputs"]["cfg"], float)
        self.assertEqual(wf["3"]["inputs"]["cfg"], 8.5)
        self.assertEqual(wf["5"]["inputs"]["width"], 768)
        self.assertEqual(wf["5"]["inputs"]["height"], 1024)
        self.assertIsInstance(wf["13"]["inputs"]["strength"], float)
        self.assertEqual(wf["13"]["inputs"]["strength"], 0.7)
        # 字符串占位符保留
        self.assertEqual(wf["10"]["inputs"]["image"], "ref.png")
        self.assertEqual(wf["11"]["class_type"], "DepthPreprocessor")
        self.assertEqual(wf["12"]["inputs"]["control_net_name"], "control_v11p_sd15_depth.pth")

    def test_face_restore_gfpgan_fill(self):
        wf = intent_translator.translate_typed_workflow("face_restore_gfpgan", {
            "input_image": "up.png", "ckpt_name": "GFPGANv1.4.pth", "restore_model": "GFPGANv1.4",
        })
        self.assertEqual(wf["1"]["inputs"]["image"], "up.png")
        self.assertEqual(wf["2"]["class_type"], "GFPGAN")
        self.assertEqual(wf["2"]["inputs"]["ckpt_name"], "GFPGANv1.4.pth")
        self.assertEqual(wf["2"]["inputs"]["restore_model"], "GFPGANv1.4")

    def test_face_restore_codeformer_fill(self):
        wf = intent_translator.translate_typed_workflow("face_restore_codeformer", {
            "input_image": "up.png", "upscale": 2, "codeformer_weight": 0.5,
            "fidelity": 0.5, "seed": 0, "blur_amount": 0, "blur_kernel": 5,
        })
        self.assertEqual(wf["2"]["class_type"], "CodeFormer")
        self.assertEqual(wf["2"]["inputs"]["upscale"], 2)
        self.assertEqual(wf["2"]["inputs"]["codeformer_weight"], 0.5)
        self.assertEqual(wf["2"]["inputs"]["fidelity"], 0.5)


class TestFaceRestoreTemplateFirst(unittest.TestCase):
    """face_restore 优先走模板；模板异常时诚实回退内联手搓。"""

    def _patch_env(self, avail=True):
        pm = mock.patch.multiple(
            ai_local,
            comfy_available=lambda: avail,
            _comfy_has_node=lambda n: True,
            _upload_to_comfy=lambda p: "up.png",
        )
        pm.start()
        self.addCleanup(pm.stop)

    def test_template_path_success(self):
        self._patch_env()
        with mock.patch.object(ai_local, "_comfy_via_typed_template", return_value="/fake/facefix.png") as m_tpl, \
             mock.patch.object(ai_local, "_comfy_run") as m_run:
            rp = ai_local.face_restore("x.png", "GFPGAN")
            self.assertEqual(rp, "/fake/facefix.png")
            m_tpl.assert_called_once()
            # 模板成功时不应再手搓
            m_run.assert_not_called()
            # 模板名须为 GFPGAN 模板
            self.assertEqual(m_tpl.call_args[0][0], "face_restore_gfpgan")

    def test_template_fallback_to_inline(self):
        self._patch_env()
        with mock.patch.object(ai_local, "_comfy_via_typed_template", side_effect=RuntimeError("no tpl")), \
             mock.patch.object(ai_local, "_comfy_run", return_value="/fake/inline.png") as m_run:
            rp = ai_local.face_restore("x.png", "CodeFormer")
            self.assertEqual(rp, "/fake/inline.png")
            m_run.assert_called_once()

    def test_comfy_offline_raises(self):
        self._patch_env(avail=False)
        with self.assertRaises(RuntimeError):
            ai_local.face_restore("x.png", "GFPGAN")


class TestControlNetTemplateFirst(unittest.TestCase):
    """_comfy_text2img_with_controlnet 优先走模板；模板异常时诚实回退内联手搓。"""

    def _patch_env(self):
        pm = mock.patch.multiple(
            ai_local,
            comfy_available=lambda: True,
            _get_comfy_checkpoint=lambda *a, **k: "v1-5.safetensors",
            _get_comfy_controlnet=lambda t: "cn.pth",
        )
        pm.start()
        self.addCleanup(pm.stop)

    def test_template_path_success(self):
        self._patch_env()
        with mock.patch.object(ai_local, "_comfy_via_typed_template", return_value="/fake/cn.png") as m_tpl, \
             mock.patch.object(ai_local, "_comfy_run") as m_run:
            rp = ai_local._comfy_text2img_with_controlnet("prompt", "ref.png", "depth")
            self.assertEqual(rp, "/fake/cn.png")
            m_tpl.assert_called_once()
            self.assertEqual(m_tpl.call_args[0][0], "shot_image_controlnet")
            m_run.assert_not_called()

    def test_template_fallback_to_inline(self):
        self._patch_env()
        with mock.patch.object(ai_local, "_comfy_via_typed_template", side_effect=RuntimeError("no tpl")), \
             mock.patch.object(ai_local, "_comfy_run", return_value="/fake/cn_inline.png") as m_run:
            rp = ai_local._comfy_text2img_with_controlnet("prompt", "ref.png", "canny")
            self.assertEqual(rp, "/fake/cn_inline.png")
            m_run.assert_called_once()

    def test_comfy_offline_raises(self):
        pm = mock.patch.multiple(
            ai_local,
            comfy_available=lambda: False,
            _get_comfy_checkpoint=lambda *a, **k: "v1-5.safetensors",
            _get_comfy_controlnet=lambda t: "cn.pth",
        )
        pm.start()
        self.addCleanup(pm.stop)
        with self.assertRaises(RuntimeError):
            ai_local._comfy_text2img_with_controlnet("prompt", "ref.png", "depth")


if __name__ == "__main__":
    unittest.main()
