# -*- coding: utf-8 -*-
"""game_tools/ocr_rapid.py —— RapidOCR（PaddleOCR ONNX）后端，**跨平台**。

为什么需要它：
  * 现有 ``ocr.py`` 依赖 ``winrt``（Windows.Media.Ocr），只能在 Windows 跑；
    机器人要整体进 Docker（Linux 容器）时直接不可用。
  * 实测（2026-11-02，三宝聚紫卡）Windows OCR 把中文拆成单字、且「三」误读成「二」；
    RapidOCR 整行输出且一次读对（``三宝聚 Laci-visitis`` + 4 条词条全对）。
    即：换它不只是为了部署，识别质量也更好。

实现要点：
  * 引擎懒加载、进程内复用（加载 ~1s）；推理加锁，避免并发闸门。
  * **一次推理同时产出 text 与 boxes**，按图片字节 sha1 缓存 ——
    ``riven_img_analyze_full`` 会先后调 ``ocr_image`` / ``ocr_image_boxes``，
    不缓存就会对同一张图跑两遍（CPU 上约 1.1s/次）。
  * 不做额外放大：RapidOCR 内部检测模型自带缩放，实测 252×328 原图即可整行读对。
  * 只依赖 ``rapidocr`` + ``onnxruntime``（纯 CPU），Linux/Windows 通用。
"""
from __future__ import annotations

import hashlib
import io
import logging
import threading

from PIL import Image

_ENGINE = None
_LOCK = threading.Lock()        # 引擎加载锁
_INFER_LOCK = threading.Lock()  # 推理锁（RapidOCR 内部预处理/后处理非严格线程安全）
_CACHE: dict[str, tuple[str, list]] = {}
_CACHE_ORDER: list[str] = []
_CACHE_MAX = 24

_QUIETED = False


def _quiet_logs() -> None:
    """压掉 RapidOCR 每次推理的 INFO 噪音（模型加载/引擎名）。"""
    global _QUIETED
    if _QUIETED:
        return
    for name in ("RapidOCR", "rapidocr"):
        try:
            logging.getLogger(name).setLevel(logging.WARNING)
        except Exception:  # noqa: BLE001
            pass
    _QUIETED = True


def available() -> bool:
    """rapidocr 是否可导入（模型随 wheel 内置，无需运行时下载）。"""
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _engine():
    global _ENGINE
    if _ENGINE is None:
        with _LOCK:
            if _ENGINE is None:
                _quiet_logs()
                from rapidocr import RapidOCR
                _ENGINE = RapidOCR()
    return _ENGINE


def _poly_to_box(poly) -> tuple[int, int, int, int]:
    """四点（或更多点）多边形 → (x, y, w, h) 行包围盒，坐标取原图像素。"""
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    x0, y0 = min(xs), min(ys)
    x1, y1 = max(xs), max(ys)
    return int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0))


def _infer(data: bytes) -> tuple[str, list]:
    """对**原始图片字节**跑一次 RapidOCR，返回 (text, boxes)，按 sha1 缓存。"""
    key = hashlib.sha1(data).hexdigest()
    if key in _CACHE:
        return _CACHE[key]
    img = Image.open(io.BytesIO(data)).convert("RGB")
    import numpy as np
    arr = np.asarray(img)
    with _INFER_LOCK:
        out = _engine()(arr)
    txts = tuple(getattr(out, "txts", None) or ())
    boxes = getattr(out, "boxes", None)
    lines: list[str] = []
    rects: list[tuple[str, int, int, int, int]] = []
    if boxes is not None and len(txts):
        for poly, txt in zip(boxes, txts):
            t = (txt or "").strip()
            if not t:
                continue
            x, y, w, h = _poly_to_box(poly)
            lines.append(t)
            rects.append((t, x, y, w, h))
    text = "\n".join(lines)
    result = (text, rects)
    _CACHE[key] = result
    _CACHE_ORDER.append(key)
    if len(_CACHE_ORDER) > _CACHE_MAX:
        _CACHE.pop(_CACHE_ORDER.pop(0), None)
    return result


def ocr_image(data: bytes) -> str:
    """图片字节 → OCR 文本（多行，按检测顺序）。失败返回空串。"""
    if not available():
        return ""
    try:
        return _infer(data)[0]
    except Exception:  # noqa: BLE001 — 单次失败不炸调用方
        return ""


def ocr_image_boxes(data: bytes) -> list:
    """图片字节 → 行级包围盒 [(text, x, y, w, h)]，坐标原始图像素。"""
    if not available():
        return []
    try:
        return _infer(data)[1]
    except Exception:  # noqa: BLE001
        return []
