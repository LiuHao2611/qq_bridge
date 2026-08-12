# -*- coding: utf-8 -*-
"""悬浮球建议引擎单元测试（方案 §2.1）。

覆盖：
  - build_key 格式：project:phase:category:metric:target
  - emit 经模块单例 dedup 节流（同键 5 分钟内只推一次）
  - emit 调用 publish_fn(channel,event,data)，频道固定 director:<pid>、事件 orb_suggestion
  - emit 被去重时返回 None
  - batch_emit 返回实际推送列表（去重项不计入）
不依赖 DB / 网络 / 外部模型；publish_fn 注入 mock。
"""
import os
import sys
import time
import unittest

_SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from orb.dedup import dedup
from orb.suggestion_engine import OrbSuggestion, build_key, emit, batch_emit


def _sug(**kw):
    base = dict(project_id="p1", phase="P1", category="asset_quality",
                metric_name="ready", target_id="a1", severity="warning",
                title="t", detail="d")
    base.update(kw)
    return OrbSuggestion(**base)


class TestBuildKey(unittest.TestCase):
    def test_key_format(self):
        s = _sug(project_id="p1", phase="P2", category="script_structure",
                 metric_name=None, target_id="s2")
        self.assertEqual(build_key(s), "p1:P2:script_structure:None:s2")

    def test_key_metric_none_normalized(self):
        s = _sug(metric_name=None, target_id="x")
        self.assertIn("None:x", build_key(s))


class TestEmit(unittest.TestCase):
    def setUp(self):
        dedup.reset()

    def test_emit_publishes_and_returns_data(self):
        captured = []
        s = _sug()
        data = emit(s, publish_fn=lambda ch, ev, d: captured.append((ch, ev, d)))
        self.assertIsNotNone(data)
        self.assertEqual(len(captured), 1)
        ch, ev, d = captured[0]
        self.assertEqual(ch, "director:p1")
        self.assertEqual(ev, "orb_suggestion")
        self.assertEqual(d["title"], "t")

    def test_emit_dedup_suppresses_second(self):
        captured = []
        fn = lambda ch, ev, d: captured.append((ch, ev, d))
        self.assertIsNotNone(emit(_sug(), publish_fn=fn))
        self.assertIsNone(emit(_sug(), publish_fn=fn))  # 同键 5 分钟内抑制
        self.assertEqual(len(captured), 1)

    def test_different_target_not_suppressed(self):
        captured = []
        fn = lambda ch, ev, d: captured.append((ch, ev, d))
        emit(_sug(target_id="a1"), publish_fn=fn)
        emit(_sug(target_id="a2"), publish_fn=fn)
        self.assertEqual(len(captured), 2)

    def test_emit_default_publish_is_safe(self):
        # 不传 publish_fn 时走 _default_publish（内部 try/except 吞异常，不应抛）
        s = _sug()
        try:
            result = emit(s)  # 首次放行，会尝试 import realtime（不存在）并被吞掉
        except Exception as e:  # pragma: no cover
            self.fail("emit 默认推送不应抛出: %r" % e)
        self.assertIsNotNone(result)


class TestBatchEmit(unittest.TestCase):
    def setUp(self):
        dedup.reset()

    def test_batch_skips_duplicates(self):
        captured = []
        fn = lambda ch, ev, d: captured.append(d)
        out = batch_emit([
            _sug(target_id="a1"),
            _sug(target_id="a1"),  # 重复
            _sug(target_id="a2"),
        ], publish_fn=fn)
        self.assertEqual(len(out), 2)
        self.assertEqual(len(captured), 2)

    def test_batch_order_preserved(self):
        dedup.reset()
        out = batch_emit([_sug(target_id="a1"), _sug(target_id="a2")], publish_fn=lambda *a: None)
        self.assertEqual([o["target_id"] for o in out], ["a1", "a2"])


if __name__ == "__main__":
    unittest.main()
