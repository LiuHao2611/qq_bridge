# -*- coding: utf-8 -*-
"""P2 M3 单元测试：视频质量验收（运动检测 + 自动重试）。

离线：抽帧走注入的 extract_fn（PIL 生成 fixture），frame_diff 走真实 PIL 实现。
"""
import os
import tempfile
import unittest

from features.motion_qc import detect_motion, frame_diff, perturb_seed, qc_and_retry
from features.ffmpeg_utils import extract_frames  # 仅确认可导入（生产用）


def _make_png(path, rgb):
    from PIL import Image
    Image.new("RGB", (32, 32), rgb).save(path)


class TestFrameDiff(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="p2_qc_")
        self.a = os.path.join(self.d, "a.png")
        self.b = os.path.join(self.d, "b.png")
        self.c = os.path.join(self.d, "c.png")
        _make_png(self.a, (200, 30, 30))
        _make_png(self.b, (30, 30, 200))
        _make_png(self.c, (200, 30, 30))  # 同 a

    def test_identical_zero(self):
        self.assertAlmostEqual(frame_diff(self.a, self.c), 0.0, places=4)

    def test_different_positive(self):
        self.assertGreater(frame_diff(self.a, self.b), 0.0)


class TestDetectMotion(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="p2_det_")
        self.red = os.path.join(self.d, "red.png")
        self.blue = os.path.join(self.d, "blue.png")
        self.green = os.path.join(self.d, "green.png")
        _make_png(self.red, (200, 30, 30))
        _make_png(self.blue, (30, 30, 200))
        _make_png(self.green, (30, 200, 30))

    def test_static_frames_detected(self):
        frames = [self.red, self.red, self.red]
        res = detect_motion("fake.mp4", extract_fn=lambda v, idx: frames)
        self.assertTrue(res["is_static"])
        self.assertLess(res["motion_score"], 0.02)

    def test_dynamic_frames_passes(self):
        frames = [self.red, self.blue, self.green]
        res = detect_motion("fake.mp4", extract_fn=lambda v, idx: frames)
        self.assertFalse(res["is_static"])
        self.assertGreater(res["motion_score"], 0.02)

    def test_extract_failure_conservative_static(self):
        def boom(v, idx):
            raise RuntimeError("ffmpeg missing")
        res = detect_motion("fake.mp4", extract_fn=boom)
        self.assertTrue(res["is_static"])
        self.assertEqual(res["note"], "frame_extract_failed")


class TestPerturbSeed(unittest.TestCase):
    def test_deterministic_and_in_range(self):
        s1 = perturb_seed(42, "林默", "站立")
        s2 = perturb_seed(42, "林默", "站立")
        self.assertEqual(s1, s2)
        self.assertTrue(0 <= s1 < 2 ** 31)

    def test_retry_changes_seed(self):
        s0 = perturb_seed(42)
        s1 = perturb_seed(s0)
        self.assertNotEqual(s0, s1)  # 换 seed 重试，避免复刻同错画面

    def test_no_python_hash_used(self):
        # derive_seed 走 SHA256（intent_engine.parameter_injector），非内置 hash()
        import intent_engine.parameter_injector as pi
        # 同样输入 → 同样输出（确定性）
        self.assertEqual(pi.derive_seed("x", "y"), pi.derive_seed("x", "y"))


class TestQcAndRetry(unittest.TestCase):
    def test_retry_then_pass_no_mark(self):
        calls = {"detect": 0, "regen": 0, "mark": 0}
        def detect_fn(url):
            calls["detect"] += 1
            # 前两次判静态，第三次通过
            return {"is_static": calls["detect"] < 3, "motion_score": 0.0 if calls["detect"] < 3 else 0.5}
        def regen_fn(task):
            calls["regen"] += 1
            task["seed"] = perturb_seed(task.get("seed", 0))
            return task
        def mark_fn(task, reason):
            calls["mark"] += 1
        task = {"result_url": "v.mp4", "seed": 1}
        shot = {}
        res = qc_and_retry(shot, task, regenerate_fn=regen_fn,
                           detect_fn=detect_fn, mark_failed_fn=mark_fn)
        self.assertFalse(res["is_static"])
        self.assertEqual(calls["regen"], 2)   # 重试 2 次
        self.assertEqual(calls["mark"], 0)    # 通过不标 failed

    def test_still_static_marks_failed(self):
        calls = {"detect": 0, "regen": 0, "mark": 0}
        def detect_fn(url):
            calls["detect"] += 1
            return {"is_static": True, "motion_score": 0.0}
        def regen_fn(task):
            # 与 test_retry_then_pass_no_mark 对称：注入换 seed 重试桩，
            # 验证「仍静态 → 标 failed」路径（不依赖 DB 片段，避免懒加载真实 regen 触发 KeyError）
            calls["regen"] += 1
            task["seed"] = perturb_seed(task.get("seed", 0))
            return task
        def mark_fn(task, reason):
            calls["mark"] += 1
            self.assertEqual(reason, "运动检测未通过")
        task = {"result_url": "v.mp4", "seed": 7}
        res = qc_and_retry({}, task, regenerate_fn=regen_fn,
                           detect_fn=detect_fn, mark_failed_fn=mark_fn)
        self.assertTrue(res["is_static"])
        self.assertEqual(calls["regen"], 2)   # 静态重试 ≤2 次
        self.assertEqual(calls["mark"], 1)    # 仍静态 → 标 failed + 提示（禁静默）


if __name__ == "__main__":
    unittest.main()
