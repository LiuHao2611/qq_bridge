# -*- coding: utf-8 -*-
"""设备算力分级单元测试（用户诉求：多设备兼容，不止适配 1660Ti）。

机器判定：通过 monkeypatch 模拟不同 GPU 显存场景，断言 detect_device() 的
图像/视频生成可行性分级与诚实原因文案，覆盖：
  - 无 GPU（纯 CPU）：视频生成禁用 + 诚实原因
  - 1660Ti 6GB：图像可行、视频不可行（用户实测场景，与诉求一致）
  - RTX 3060 12GB / 4090 24GB：图像与视频均可行
  - nvidia-smi 返回异常（显存解析失败）：保守禁用视频
探测函数异常时必须不抛错（诚实，不谎报可用算力）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capabilities import device as dev


class TestDetectDeviceNoGpu(unittest.TestCase):
    def test_no_gpu_disables_video(self):
        import unittest.mock as mock
        with (
            mock.patch.object(dev, "_probe_nvidia_smi", return_value=None),
            mock.patch.object(dev, "_probe_torch", return_value=None),
        ):
            d = dev.detect_device()
        self.assertFalse(d["cuda_available"])
        self.assertFalse(d["capabilities"]["image_generation"])
        self.assertFalse(d["capabilities"]["video_generation"])
        self.assertTrue(d["video_gen_reason"], "无 GPU 时应给出原因文案")
        self.assertIn("CUDA", d["video_gen_reason"])


class TestDetectDevice1660Ti(unittest.TestCase):
    """用户实测机型：6GB 显存 → 图像可行，视频不可行。"""

    def test_1660ti_image_ok_video_no(self):
        import unittest.mock as mock
        with (
            mock.patch.object(dev, "_probe_nvidia_smi",
                             return_value=("NVIDIA GeForce GTX 1660 Ti", 6144)),
            mock.patch.object(dev, "_probe_torch", return_value=None),
        ):
            d = dev.detect_device()
        self.assertTrue(d["cuda_available"])
        self.assertEqual(d["gpu_name"], "NVIDIA GeForce GTX 1660 Ti")
        self.assertEqual(d["vram_total_mb"], 6144)
        self.assertEqual(d["vram_total_gb"], 6.0)
        # 6144 >= 2048 → 图像可行
        self.assertTrue(d["capabilities"]["image_generation"])
        # 6144 < 8192 → 视频不可行（核心断言，对应用户诉求）
        self.assertFalse(d["capabilities"]["video_generation"])
        self.assertIn("8192", d["video_gen_reason"])
        self.assertIn("≥8GB", d["recommendation"])  # 建议诚实告知显存门槛


class TestDetectDeviceCapable(unittest.TestCase):
    def test_rtx3060_12gb_both_ok(self):
        import unittest.mock as mock
        with (
            mock.patch.object(dev, "_probe_nvidia_smi",
                             return_value=("NVIDIA GeForce RTX 3060", 12288)),
            mock.patch.object(dev, "_probe_torch", return_value=None),
        ):
            d = dev.detect_device()
        self.assertTrue(d["capabilities"]["image_generation"])
        self.assertTrue(d["capabilities"]["video_generation"])
        self.assertEqual(d["video_gen_reason"], "")

    def test_rtx4090_24gb_both_ok(self):
        import unittest.mock as mock
        with (
            mock.patch.object(dev, "_probe_nvidia_smi",
                             return_value=("NVIDIA GeForce RTX 4090", 24564)),
            mock.patch.object(dev, "_probe_torch", return_value=None),
        ):
            d = dev.detect_device()
        self.assertTrue(d["capabilities"]["image_generation"])
        self.assertTrue(d["capabilities"]["video_generation"])


class TestDetectDeviceMalformed(unittest.TestCase):
    """nvidia-smi 能拿到名称但显存解析失败（vram=None）→ 保守禁用视频。"""

    def test_name_ok_vram_none(self):
        import unittest.mock as mock
        with (
            mock.patch.object(dev, "_probe_nvidia_smi",
                             return_value=("Some GPU", None)),
            mock.patch.object(dev, "_probe_torch", return_value=None),
        ):
            d = dev.detect_device()
        self.assertTrue(d["cuda_available"])
        self.assertIsNone(d["vram_total_mb"])
        self.assertFalse(d["capabilities"]["video_generation"])
        self.assertTrue(d["video_gen_reason"])


class TestProbeRobustness(unittest.TestCase):
    """探测函数自身异常（如 nvidia-smi 超时 / 命令缺失）必须返回 None，不抛错。"""

    def test_nvidia_smi_subprocess_raises_returns_none(self):
        import unittest.mock as mock
        # 真实 _probe_nvidia_smi 内部 try/except 必须吞掉 subprocess 异常
        with mock.patch.object(dev.subprocess, "run", side_effect=RuntimeError("boom")):
            self.assertIsNone(dev._probe_nvidia_smi())

    def test_detect_device_never_raises(self):
        import unittest.mock as mock
        # nvidia-smi 命令炸（subprocess 异常）→ 探测返回 None；torch 也不可用 → 仍结构化返回
        with (
            mock.patch.object(dev.subprocess, "run", side_effect=RuntimeError("boom")),
            mock.patch.object(dev, "_probe_torch", return_value=None),
        ):
            d = dev.detect_device()
        self.assertIn("capabilities", d)
        self.assertFalse(d["capabilities"]["video_generation"])
        self.assertIn("video_gen_reason", d)
        self.assertFalse(d["cuda_available"])


class TestVideoFeasibleHelper(unittest.TestCase):
    def test_feasible_false_on_1660ti(self):
        import unittest.mock as mock
        with (
            mock.patch.object(dev, "_probe_nvidia_smi",
                             return_value=("GTX 1660 Ti", 6144)),
            mock.patch.object(dev, "_probe_torch", return_value=None),
        ):
            self.assertFalse(dev.video_generation_feasible())

    def test_feasible_true_on_4090(self):
        import unittest.mock as mock
        with (
            mock.patch.object(dev, "_probe_nvidia_smi",
                             return_value=("RTX 4090", 24564)),
            mock.patch.object(dev, "_probe_torch", return_value=None),
        ):
            self.assertTrue(dev.video_generation_feasible())


if __name__ == "__main__":
    unittest.main()
