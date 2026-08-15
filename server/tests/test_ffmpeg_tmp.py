# -*- coding: utf-8 -*-
"""D3 修复验证：ffmpeg 抽帧临时目录必须固定落在项目 .tmp（C 盘零写入）。

此前 extract_frames / extract_last_frame 用 tempfile.mkdtemp() 未指定 dir，
依赖 TMPDIR 环境变量；部署未设时落到系统 temp（如 C:/Users/.../Temp），违反沙箱铁律。

本测试用 monkeypatch 替换 run_ffmpeg，避免依赖本机 ffmpeg，纯粹断言临时目录位置：
- _TMP_ROOT 必须指向 <项目根>/.tmp；
- 未显式传 out_dir 时，生成的帧/尾帧临时目录必须位于 _TMP_ROOT 之下。
"""
import os
import sys
import tempfile

_TMP = os.path.join(tempfile.mkdtemp(prefix="ffmptmp_"), "test.db")
os.environ["MANJU_DB_PATH"] = _TMP

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import pytest  # noqa: E402
from features import ffmpeg_utils as fu  # noqa: E402


def test_tmp_root_is_project_dot_tmp():
    """_TMP_ROOT 必须位于项目根下的 .tmp，而非系统 temp。"""
    assert fu._TMP_ROOT.replace("\\", "/").endswith("manju_agent/.tmp")


def test_extract_frames_uses_project_tmp(monkeypatch):
    """未传 out_dir 时，抽帧临时目录必须落在 _TMP_ROOT 之下。"""
    captured = {}

    def _fake_run(args, timeout=120):
        # args[-1] 是输出 png 路径，其父目录即临时目录
        captured["dir"] = os.path.dirname(args[-1])

        class _P:
            returncode = 0
            stderr = b""

        return _P()

    monkeypatch.setattr(fu, "run_ffmpeg", _fake_run)
    # run_ffmpeg 被 mock，不会真正调用 ffmpeg；即使抛错也不影响对目录位置的断言
    try:
        fu.extract_frames("fake.mp4", [0, 1, 2])
    except Exception:
        pass
    assert captured.get("dir"), "未观察到抽帧临时目录"
    assert captured["dir"].startswith(fu._TMP_ROOT), (
        "抽帧临时目录 %s 不在项目 .tmp(%s) 之下" % (captured["dir"], fu._TMP_ROOT))


def test_extract_last_frame_uses_project_tmp(monkeypatch):
    """未传 out_path 时，尾帧临时目录必须落在 _TMP_ROOT 之下。"""
    captured = {}

    def _fake_run(args, timeout=120):
        captured["dir"] = os.path.dirname(args[-1])

        class _P:
            returncode = 0
            stderr = b""

        return _P()

    monkeypatch.setattr(fu, "run_ffmpeg", _fake_run)
    try:
        fu.extract_last_frame("fake.mp4")
    except Exception:
        pass
    assert captured.get("dir"), "未观察到尾帧临时目录"
    assert captured["dir"].startswith(fu._TMP_ROOT)


if __name__ == "__main__":
    class _M:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    m = _M()
    test_tmp_root_is_project_dot_tmp()
    test_extract_frames_uses_project_tmp(m)
    test_extract_last_frame_uses_project_tmp(m)
    print("ffmpeg 临时目录断言通过 ✅")
