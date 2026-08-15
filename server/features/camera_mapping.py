# -*- coding: utf-8 -*-
"""运镜映射表（P2 手册 M1 §4.1 / P0 camera.build() 依赖）。

输入：camera_movement 枚举值（来自前端 src/mock.js 与 P0 推断，只读不改）
处理：查表
输出：英文运镜描述（prompt fragment）

铁律：
- 映射表是「超集」——手册 §4.1 的 9 个官方值 + 前端真实枚举别名（dolly/zoom/aerial/
  follow/pull_out/pan_lr/pan/tilt）+ P0 camera.py 推断产生的 crane_up/crane_down/
  top_down/orbit/pan，全部覆盖，避免未知值回退 static 时丢失运镜语义。
- 严禁修改 src/mock.js 枚举；只在此处新增映射。
- 未知值 → 回退 "static" 并记 warning（禁静默）。
"""
import logging

logger = logging.getLogger("features.camera_mapping")

# 手册 §4.1 官方 9 项（原文保留，作为语义基准）
_CAMERA_MOVEMENT_PROMPT_OFFICIAL = {
    "static":    "fixed camera, no camera movement",
    "push_in":   "camera slowly pushing in toward the subject",
    "pull_back": "camera slowly pulling back, revealing the scene",
    "pan_left":  "camera panning left",
    "pan_right": "camera panning right",
    "tilt_up":   "camera tilting up",
    "tilt_down": "camera tilting down",
    "tracking":  "camera tracking alongside the moving subject",
    "handheld":  "handheld camera with subtle natural shake",
}

# 前端真实枚举 / P0 推断别名 → 复用或扩展为等价英文片段（超集，补齐手册未列值）
_CAMERA_MOVEMENT_PROMPT_ALIAS = {
    # 前端 CAMERA_PATH_OPTIONS / SHOT_TYPE_OPTIONS / LEGACY_MOVE_MAP 实际会出现的值
    "pull_out":  "camera slowly pulling back, revealing the scene",   # 别名 → pull_back
    "dolly":     "camera dolly movement, smooth push-in on tracks",   # 推拉镜头（含推/拉）
    "zoom":      "camera zoom, focal length change",                  # 变焦（光学，区别于 dolly）
    "aerial":    "aerial view from above, drone shot",                # 航拍
    "follow":    "camera tracking alongside the moving subject",      # 跟拍 → tracking
    "track_lr":  "camera tracking left to right alongside the moving subject",  # 移（左右跟）
    "pan_lr":    "camera panning left then right",                    # 摇（左右）
    "pan":       "camera panning horizontally",                       # 摇（横向，LEGACY）
    "tilt":      "camera tilting, vertical movement",                 # 升降/俯仰（LEGACY）
    # P0 camera.py _CAMERA_KEYWORDS 推断会产生的值（保留原片段，避免推断回归）
    "crane_up":  "camera crane up",
    "crane_down": "camera crane down",
    "top_down":  "top down aerial view",
    "orbit":     "camera orbit around subject",
}

# 合并超集（官方优先，别名补齐）
CAMERA_MOVEMENT_PROMPT = dict(_CAMERA_MOVEMENT_PROMPT_ALIAS)
CAMERA_MOVEMENT_PROMPT.update(_CAMERA_MOVEMENT_PROMPT_OFFICIAL)


def resolve_camera_movement(intent) -> str:
    """从意图对象（dict 或 ProductionIntent dataclass）解析运镜枚举。

    解析顺序（兼容多种来源）：
      1. intent["camera"]["movement"]      （手册 dict 形态）
      2. intent["optional_controls"]["camera_motion"]  （ProductionIntent 形态）
      3. intent["camera_movement"]         （src/mock.js 直传形态）
    都没有 → "static"。
    """
    if isinstance(intent, dict):
        cam = intent.get("camera") or {}
        if isinstance(cam, dict) and cam.get("movement"):
            return cam["movement"]
        oc = intent.get("optional_controls") or {}
        if isinstance(oc, dict) and oc.get("camera_motion"):
            return oc["camera_motion"]
        if intent.get("camera_movement"):
            return intent["camera_movement"]
        return "static"
    # dataclass（ProductionIntent）
    oc = getattr(intent, "optional_controls", None) or {}
    if isinstance(oc, dict) and oc.get("camera_motion"):
        return oc["camera_motion"]
    return "static"


def resolve_camera_fragment(movement: str) -> str:
    """输入：运镜枚举
    处理：查 CAMERA_MOVEMENT_PROMPT（超集）
    输出：英文运镜描述 fragment；未知 → "static" + warning（禁静默）。
    """
    if movement in CAMERA_MOVEMENT_PROMPT:
        return CAMERA_MOVEMENT_PROMPT[movement]
    logger.warning("未知运镜枚举=%r，回退 static", movement)
    return CAMERA_MOVEMENT_PROMPT["static"]
