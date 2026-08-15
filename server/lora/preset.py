# -*- coding: utf-8 -*-
"""LoRA 训练参数预设（手册 §6 / 设计思路 §5）。

所有预设强制开启 gradient_checkpointing + mixed_precision=fp16（6GB 显存必需）；
xformers 关闭——经典 sd-scripts 的 requirements 未含 xformers，1660Ti 小显存 LoRA 改用 PyTorch 原生 SDPA。
6GB 预设分辨率禁超 512 / batch 禁超 1（OOM 红线，手册 §12）。
"""
import os

# KOHYA_SS_PATH 默认落 D:\AI（铁律：绝不写 C 盘）。
DEFAULT_KOHYA_SS_PATH = os.environ.get("KOHYA_SS_PATH") or "D:/AI/kohya_ss"

PRESETS = {
    "6gb_fast": {
        "label": "6GB 快速",
        "resolution": "512,512",
        "batch_size": 1,
        "optimizer": "AdamW",
        "max_train_steps": 1500,
        "learning_rate": 1e-4,
        "note": "快速验证（约 20-40 分钟）",
    },
    "6gb_quality": {
        "label": "6GB 质量",
        "resolution": "512,768",
        "batch_size": 1,
        "optimizer": "AdamW",
        "max_train_steps": 3000,
        "learning_rate": 1e-4,
        "note": "正式训练（约 1-3 小时）",
    },
    "standard": {
        "label": "标准（预留）",
        "resolution": "768,768",
        "batch_size": 2,
        "optimizer": "AdamW",
        "max_train_steps": 3000,
        "learning_rate": 1e-4,
        "note": "更高显存机型（>8GB）",
    },
}

DEFAULT_PRESET = "6gb_quality"

# 所有预设强制开启的优化开关（手册 §6 必须）。
# 注：xformers 设为 false——经典 sd-scripts 的 requirements 未包含 xformers，
# 且 1660Ti 小显存 LoRA 用 PyTorch 原生 SDPA 即可，避免额外依赖与版本风险。
FORCED_FLAGS = {
    "xformers": "false",
    "gradient_checkpointing": "true",
    "mixed_precision": "fp16",
}

# LoRA 网络结构默认值（rank/dropout/alpha）。
NETWORK_DIM = 32
NETWORK_ALPHA = 16
NETWORK_DROPOUT = 0.0

# 数据量阈值（设计思路 §2 / 手册 §4.1）：不足则提示，不静默放行。
MIN_IMAGES_STYLE = 10
MIN_IMAGES_CHARACTER = 15


def get_preset(key):
    """按 key 取预设；无效 key 回退默认并补强制开关。"""
    p = PRESETS.get(key)
    if not p:
        p = PRESETS[DEFAULT_PRESET]
    merged = dict(p)
    merged.update(FORCED_FLAGS)
    return merged


def list_presets():
    """前端下拉用的预设清单（含分辨率/步数/批大小）。"""
    out = []
    for key, p in PRESETS.items():
        merged = dict(p)
        merged.update(FORCED_FLAGS)
        out.append({
            "key": key,
            "label": p.get("label", key),
            "resolution": p.get("resolution"),
            "batch_size": p.get("batch_size"),
            "max_train_steps": p.get("max_train_steps"),
            "learning_rate": p.get("learning_rate"),
            "optimizer": p.get("optimizer"),
            "note": p.get("note", ""),
            "is_default": key == DEFAULT_PRESET,
            "forced": FORCED_FLAGS,
        })
    return out
