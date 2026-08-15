# -*- coding: utf-8 -*-
"""一键重构前端端点回归测试。

修复背景：原日志目录 server/.srvlogs 在部分 Windows 环境下被安全策略/钩子拦截，
导致 `Permission denied` 而构建尚未开始即失败。本测试断言：
  - 日志目录已迁移到 D 盘 .tmp/logs
  - subprocess 被正确调用时返回 success=True
  - 输出中不再出现 Permission denied / .srvlogs
"""
import asyncio
import os
import sys
import unittest

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import routers  # noqa: E402


class FakeProc:
    returncode = 0

    def wait(self, timeout=None):
        return 0


class TestSystemRebuildFrontend(unittest.TestCase):
    def test_log_dir_moved_to_tmp_logs_and_succeeds(self):
        import unittest.mock as mock

        script_exists = lambda p: p.endswith("build_frontend.sh") or p.endswith("bash.exe")

        with (
            mock.patch("os.path.isfile", side_effect=script_exists),
            mock.patch("subprocess.Popen", return_value=FakeProc()) as mock_popen,
            mock.patch("os.makedirs") as mock_makedirs,
        ):
            result = asyncio.run(routers.system_rebuild_frontend())

        self.assertEqual(result["code"], 0)
        data = result["data"]
        self.assertTrue(data["success"], data.get("output"))
        self.assertEqual(data["exit_code"], 0)
        self.assertIn(".tmp\\logs\\rebuild_frontend.log", data["log_path"])
        self.assertNotIn(".srvlogs", data["log_path"])
        self.assertNotIn("Permission denied", data.get("output", ""))
        mock_makedirs.assert_called_once()
        call_args = mock_makedirs.call_args[0]
        self.assertIn(".tmp", call_args[0].replace("/", "\\"))
        self.assertIn("logs", call_args[0].replace("/", "\\"))
        mock_popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
