# -*- coding: utf-8 -*-
"""悬浮球建议去重 / 节流单元测试（设计方案 §2.3）。

覆盖 DedupLRU：
  - 窗口内重复 should_emit 返回 False（抑制）
  - 窗口外 / 首次返回 True（放行）
  - reset 单键 / 全量
  - __len__ 反映存储键数
不依赖 DB / 网络 / 外部模型。
"""
import os
import sys
import time
import unittest

_SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from orb.dedup import DedupLRU, dedup, DEDUP_TTL_SECONDS


class TestDedupLRU(unittest.TestCase):
    def test_first_emit_allowed(self):
        d = DedupLRU(ttl=10)
        self.assertTrue(d.should_emit("k1"))
        self.assertEqual(len(d), 1)

    def test_repeat_within_window_suppressed(self):
        d = DedupLRU(ttl=10)
        self.assertTrue(d.should_emit("k1"))
        self.assertFalse(d.should_emit("k1"))
        self.assertFalse(d.should_emit("k1"))

    def test_different_keys_independent(self):
        d = DedupLRU(ttl=10)
        self.assertTrue(d.should_emit("a"))
        self.assertTrue(d.should_emit("b"))
        self.assertFalse(d.should_emit("a"))
        self.assertFalse(d.should_emit("b"))
        self.assertEqual(len(d), 2)

    def test_window_expiry_allows_again(self):
        d = DedupLRU(ttl=1)
        self.assertTrue(d.should_emit("k"))
        self.assertFalse(d.should_emit("k"))
        time.sleep(1.05)
        self.assertTrue(d.should_emit("k"))

    def test_reset_single_key(self):
        d = DedupLRU(ttl=100)
        self.assertTrue(d.should_emit("k"))
        d.reset("k")
        self.assertTrue(d.should_emit("k"))  # 重置后窗口清空，再次放行

    def test_reset_all(self):
        d = DedupLRU(ttl=100)
        d.should_emit("a")
        d.should_emit("b")
        d.reset()
        self.assertEqual(len(d), 0)
        self.assertTrue(d.should_emit("a"))

    def test_singleton_ttl_constant(self):
        self.assertEqual(DEDUP_TTL_SECONDS, 300)
        self.assertIsInstance(dedup, DedupLRU)
        self.assertEqual(dedup.ttl, DEDUP_TTL_SECONDS)


if __name__ == "__main__":
    unittest.main()
