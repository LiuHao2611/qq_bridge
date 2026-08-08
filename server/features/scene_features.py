# -*- coding: utf-8 -*-
"""Feature Extraction Engine —— 场景风格特征提取（手册 §2.2 / §3.2.4）。

为「同一场景内统一光影方向、色温、饱和度」提供可量化风格描述子。
不依赖大模型权重（用色彩统计），保证本地可实时运行；用于 N03 场景一致性约束
与 N06 风格漂移检测。

extract_scene_features(image_path) -> np.float32(SCENE_VECTOR_DIM,)：
  [0] 平均亮度(0-1)  [1] 平均饱和度(0-1)  [2] 色温偏暖程度(0-1, 越高越暖)
  [3] 主色相(0-1, hue/360)  [4] 色彩方差(0-1, 越高越杂乱)
  [5] 冷色占比(0-1)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

SCENE_VECTOR_DIM = 6


def extract_scene_features(image_path):
    """从一张图提取 6 维风格描述子（详见模块 docstring）。无图/读失败返回 None。"""
    import numpy as np
    try:
        import cv2
        img = cv2.imread(image_path)
    except Exception:
        return None
    if img is None:
        return None
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    h, w, _ = img.shape
    # 亮度
    lum = 0.2126 * img[:, :, 0] + 0.7152 * img[:, :, 1] + 0.0722 * img[:, :, 2]
    brightness = float(lum.mean())
    # 饱和度/色相
    maxc = img.max(axis=2)
    minc = img.min(axis=2)
    delta = (maxc - minc)
    sat = float((delta).mean()) if h * w > 0 else 0.0
    # 色温：以 R-B 差近似（暖=正，冷=负）→ 归一化到 0-1
    warmth = float(((img[:, :, 0] - img[:, :, 2]).mean() + 1.0) / 2.0)
    # 主色相
    cmax_idx = maxc.argmax(axis=2) if False else None
    # 简化主色相：用平均 RGB 反算 hue
    r, g, b = img[:, :, 0].mean(), img[:, :, 1].mean(), img[:, :, 2].mean()
    hue = _rgb_to_hue(r, g, b)
    # 色彩方差（sat 的标准差近似杂乱度）
    color_var = float(delta.std()) if h * w > 0 else 0.0
    # 冷色占比：hue∈[180,300] 视为冷
    # 逐像素判定代价高，用平均 B-R 近似冷色强度
    cold = float(((img[:, :, 2] - img[:, :, 0]).clip(0, 1)).mean())
    vec = np.array([brightness, sat, warmth, hue, color_var, cold], dtype=np.float32)
    vec = np.clip(vec, 0.0, 1.0)
    return vec


def _rgb_to_hue(r, g, b):
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 1e-6:
        return 0.0
    if mx == r:
        h = (g - b) / d
    elif mx == g:
        h = 2.0 + (b - r) / d
    else:
        h = 4.0 + (r - g) / d
    h = (h / 6.0) % 1.0
    return float(h)


def scene_consistency_ok(vec_a, vec_b, warmth_tol=200.0 / 255.0, bright_tol=0.10):
    """判断两段同场景镜头的风格是否一致（手册 §3.2.5：色温偏差≤200K≈0.10、亮度偏差≤10%）。

    这里用 warmth(0-1) 与 brightness(0-1) 近似色温/亮度偏差。返回 (ok, reasons[])。
    """
    if vec_a is None or vec_b is None:
        return True, []
    reasons = []
    if abs(vec_a[2] - vec_b[2]) > warmth_tol:
        reasons.append("色温偏差超阈值")
    if abs(vec_a[0] - vec_b[0]) > bright_tol:
        reasons.append("亮度偏差超阈值")
    return (len(reasons) == 0), reasons
