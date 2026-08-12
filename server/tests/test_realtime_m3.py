# -*- coding: utf-8 -*-
"""M3 悬浮球前端化 · 导演事件推送封装单元测试（手册 §4.1 / 设计 §7.3）。

离线可跑：仅校验 server/realtime.py 新增的三个薄包装
publish_director_notice / publish_director_insight / publish_translator_selection
是否以正确的 channel / event / payload 调用既有 publish()（非破坏性复用）。

不依赖任何外部服务（Ollama/ComfyUI/WebSocket），mock 掉 publish 即可。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)

import realtime  # noqa: E402


class TestRealtimeM3DirectorEvents(unittest.TestCase):
    """三类导演事件封装 → 复用 publish(channel, event, data)。"""

    def _patch_publish(self):
        mock = MagicMock()
        patcher = patch.object(realtime, "publish", mock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def test_notice_channel_event_and_default_data(self):
        mock = self._patch_publish()
        # 显式 data
        realtime.publish_director_notice("p1", {"msg": "脚本节奏偏慢"})
        mock.assert_called_once_with("director:p1", "notice", {"msg": "脚本节奏偏慢"})
        # None → 默认 {}
        mock.reset_mock()
        realtime.publish_director_notice("p2")
        mock.assert_called_once_with("director:p2", "notice", {})

    def test_insight_channel_event_and_default_data(self):
        mock = self._patch_publish()
        realtime.publish_director_insight("p1", {"passed": 5, "total": 5})
        mock.assert_called_once_with("director:p1", "insight", {"passed": 5, "total": 5})
        mock.reset_mock()
        realtime.publish_director_insight("p9")
        mock.assert_called_once_with("director:p9", "insight", {})

    def test_translator_selection_payload_shape(self):
        mock = self._patch_publish()
        selected = {
            "checkpoint": "majicmixRealistic_v7",
            "node": "IPAdapterFaceID",
            "loras": ["char_lin_0.7", "style_film_0.3"],
            "seed": 123456789,
        }
        realtime.publish_translator_selection(
            "p1", "trace-abcd", "character_genesis", selected
        )
        self.assertEqual(mock.call_count, 1)
        args = mock.call_args
        channel, event, data = args[0]
        self.assertEqual(channel, "translator:p1")
        self.assertEqual(event, "selection")
        # payload 必须含四元组，且不依赖 hash/random（trace_id 由调用方传 uuid4）
        self.assertEqual(data["trace_id"], "trace-abcd")
        self.assertEqual(data["task_type"], "character_genesis")
        self.assertEqual(data["selected"], selected)
        self.assertIn("timestamp", data)
        self.assertIsInstance(data["timestamp"], int)

    def test_translator_selection_empty_selected_defaults(self):
        mock = self._patch_publish()
        realtime.publish_translator_selection("p1", "t", "character_genesis", None)
        _, _, data = mock.call_args[0]
        self.assertEqual(data["selected"], {})


if __name__ == "__main__":
    unittest.main()
