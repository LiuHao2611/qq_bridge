# -*- coding: utf-8 -*-
"""性能测试（手册 §5.3）。

在 mock 掉真实 ComfyUI/Ollama 的前提下，对纯 Python 关键路径做时序基准：
  - 意图翻译 + 模板填充延迟（P95）
  - 单格出图管线（mock ComfyUI Layer0）端到端耗时
不依赖真实模型推理，可在任意机器跑通；阈值留足余量，仅用于回归防劣化。
"""
import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intent_engine
import ai_local


def _make_dummy_png(path):
    from PIL import Image
    Image.new("RGB", (64, 64), (120, 90, 200)).save(path)


class TestPerfIntentTranslation(unittest.TestCase):
    """意图翻译 + 模板填充应在毫秒级完成。"""

    def _once(self):
        intent = intent_engine.build_shot_image_intent(
            "test prompt", style_tags=["日漫"],
            identity_image="char.png", style_image="style.png",
            generation_params={"seed": 7, "width": 768, "height": 1024})
        return intent_engine.translate_intent_to_workflow(intent)

    def test_translate_p95(self):
        samples = []
        for _ in range(30):
            t0 = time.perf_counter()
            self._once()
            samples.append(time.perf_counter() - t0)
        samples.sort()
        p95 = samples[int(len(samples) * 0.95)]
        # 纯字典操作，30 次采样 P95 应远低于 50ms
        self.assertLess(p95, 0.05, "意图翻译 P95=%.4fs 超出预期" % p95)

    def test_img2img_translate(self):
        t0 = time.perf_counter()
        intent = intent_engine.build_shot_image_intent(
            "anchor", init_image="frame.png", denoise=0.55,
            generation_params={"seed": 1})
        wf = intent_engine.translate_intent_to_workflow(intent)
        dt = time.perf_counter() - t0
        self.assertIn("10", wf)  # LoadImage 节点
        self.assertLess(dt, 0.02)


class TestPerfComicPanel(unittest.TestCase):
    """单格出图管线（mock ComfyUI Layer0）应在亚秒级完成。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = ai_local._comfy_text2img_via_template

        def fake_via_template(intent, timeout=240):
            p = os.path.join(self._tmp, "p_%s.png" % int(time.time() * 1e6))
            _make_dummy_png(p)
            return p

        ai_local._comfy_text2img_via_template = fake_via_template

    def tearDown(self):
        ai_local._comfy_text2img_via_template = self._orig

    def test_panel_batch_latency(self):
        n = 12
        t0 = time.perf_counter()
        for idx in range(n):
            out = ai_local._comic_panel_generate(
                "prompt %d" % idx, 512, 512, 20, 8.0, idx, "",
                char_comfy=["char.png"], style_ref_comfy=None, ip_available=True,
                first_local=None, idx=idx, style_arg="日漫", enforce_asset=False)
            self.assertTrue(os.path.isfile(out))
        dt = time.perf_counter() - t0
        per = dt / n
        # mock 下每格应远小于 50ms（纯 Python + 写小 PNG）
        self.assertLess(per, 0.05, "单格平均 %.4fs 超出预期" % per)


if __name__ == "__main__":
    unittest.main()
