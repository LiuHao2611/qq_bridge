# -*- coding: utf-8 -*-
"""ffmpeg 抽帧工具（P2 M2/M3 共用，纯 ffmpeg，无 PIL 依赖于生产路径）。

- 生产路径用 ffmpeg 抽帧 / 抽尾帧；
- 单测通过注入 extract_fn 走离线（PIL 生成 fixture），不依赖本机 ffmpeg。
- 通过环境变量 FFMPEG_BIN 指定可执行文件，默认 "ffmpeg"。
"""
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger("features.ffmpeg_utils")

# C 盘零写入：临时帧目录固定落到项目 .tmp，不依赖 TMPDIR 环境变量
# （否则未设 TMPDIR 时会落到系统 temp，违反沙箱铁律）。
# 本文件位于 server/features/，故向上三级到达项目根（manju_agent）。
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP_ROOT = os.path.join(_BASE, ".tmp")
os.makedirs(_TMP_ROOT, exist_ok=True)


def _ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


def _ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN") or "ffprobe"


def run_ffmpeg(args, timeout: int = 120):
    """运行 ffmpeg 子进程；失败抛 RuntimeError（含 stderr 尾部，禁静默）。"""
    exe = _ffmpeg_bin()
    cmd = [exe] + [str(a) for a in args]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg 未找到：%s（请安装或设置 FFMPEG_BIN）" % exe)
    if proc.returncode != 0:
        tail = proc.stderr[-800:].decode(errors="ignore")
        raise RuntimeError("ffmpeg 失败(code=%d): %s" % (proc.returncode, tail))
    return proc


def count_frames(video_path: str):
    """用 ffprobe 取总帧数；失败返回 None（调用方走保守回退）。"""
    exe = _ffprobe_bin()
    if shutil.which(exe) is None and not os.path.exists(exe):
        return None
    cmd = [exe, "-v", "error", "-count_frames", "-select_streams", "v:0",
           "-show_entries", "stream=nb_read_frames", "-of",
           "default=nokey=1:noprint_wrappers=1", video_path]
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=60).stdout.decode(errors="ignore").strip()
        return int(out) if out.isdigit() else None
    except Exception as e:
        logger.warning("ffprobe 取帧数失败：%s", e)
        return None


def extract_frames(video_path: str, indices, out_dir: str = None) -> list:
    """按索引抽帧为 PNG，返回路径列表（与 indices 顺序一致）。"""
    out_dir = out_dir or tempfile.mkdtemp(prefix="mj_frames_", dir=_TMP_ROOT)
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i in indices:
        p = os.path.join(out_dir, "frame_%05d.png" % i)
        run_ffmpeg(["-y", "-ss", "0", "-i", video_path,
                    "-vf", "select='eq(n\\,%d)'" % int(i),
                    "-vsync", "0", "-frames:v", "1", p], timeout=60)
        paths.append(p)
    return paths


def extract_last_frame(video_path: str, out_path: str = None) -> str:
    """抽尾帧（手册 M2 §4.2：ffmpeg -sseof -0.1）。返回 PNG 路径。"""
    if out_path is None:
        d = tempfile.mkdtemp(prefix="mj_lastframe_", dir=_TMP_ROOT)
        out_path = os.path.join(d, "last_frame.png")
    run_ffmpeg(["-y", "-sseof", "-0.1", "-i", video_path,
                "-frames:v", "1", out_path], timeout=60)
    return out_path


def video_frame_indices(video_path: str, n: int = 3) -> list:
    """取前/中/后三帧索引（手册 M3 §4.1）。无法探帧时保守回退 [0,1,2]。"""
    total = count_frames(video_path)
    if not total or total < n:
        return list(range(min(n, max(1, total or n))))
    last = total - 1
    mid = total // 2
    return [0, mid, last]
