# -*- coding: utf-8 -*-
"""M2 worker 提交策略纯函数单元测试（非破坏性分支验证）。

select_submission_strategy 决定：
  - workflow_json 非空 → 本地 ComfyUI 提交（local_comfy）
  - 否则 → 既有 RunningHub 路径（runninghub，非破坏性）
"""
import os
import sys
import unittest

_SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)

import worker


class TestSelectSubmissionStrategy(unittest.TestCase):

    def test_local_comfy_when_workflow_json_present(self):
        task = {"task_id": "t1", "workflow_json": '{"nodes": []}'}
        self.assertEqual(worker.select_submission_strategy(task), "local_comfy")

    def test_runninghub_when_workflow_json_absent(self):
        task = {"task_id": "t2"}
        self.assertEqual(worker.select_submission_strategy(task), "runninghub")

    def test_runninghub_when_workflow_json_empty(self):
        task = {"task_id": "t3", "workflow_json": ""}
        self.assertEqual(worker.select_submission_strategy(task), "runninghub")

    def test_runninghub_when_workflow_json_null(self):
        task = {"task_id": "t4", "workflow_json": None}
        self.assertEqual(worker.select_submission_strategy(task), "runninghub")

    def test_local_comfy_whitespace_truthy(self):
        # 纯真值判定：非空（含纯空白）字符串视为「有 workflow_json」→ local_comfy；
        # 真正的 JSON 合法性校验发生在 _serve_local_workflow 的 json.loads 处。
        task = {"task_id": "t5", "workflow_json": "   "}
        self.assertEqual(worker.select_submission_strategy(task), "local_comfy")


if __name__ == "__main__":
    unittest.main()
