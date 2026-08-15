# -*- coding: utf-8 -*-
"""首尾帧双锚定（P2 手册 M2 §4.1-§4.3）。

端到端：分镜生成 → 判断首/尾帧可用性 + 模型声明 → 选锚定模式 → 生成 →
抽尾帧落库 → 供下一分镜。

铁律：
- 模型不支持插值 → 必须诚实降级为「首帧外推」，禁硬造假插值。
- 链式承接仅同 scene_id 复用首帧，跨场景不继承（防漂移）。
- 抽尾帧用 ffmpeg（生产）；单测可注入 extract_fn 离线验证。
"""
import logging
import os

from features.ffmpeg_utils import extract_last_frame

logger = logging.getLogger("features.frame_chain")


def _supports_interpolation(model_capability: dict) -> bool:
    """兼容手册 grading 词汇 与 P0 注册表 interpolation:bool / mode。"""
    fa = (model_capability or {}).get("frame_anchor") or {}
    return bool(
        fa.get("grading") == "first_last_interpolate"
        or fa.get("interpolation")
        or fa.get("mode") == "first_last_interpolate"
    )


def choose_anchor_mode(shot, model_capability) -> str:
    """输入：shot（first_frame_path/last_frame_path 存在性）+ 模型声明（P0 M2）
    处理：三步判断
    输出：frame_anchor_mode（"first_frame_only" | "first_last_interpolate"）

    兼容两种模型能力词汇：
      - 手册：frame_anchor.grading == "first_last_interpolate"
      - P0 注册表：frame_anchor.interpolation == True / mode
    """
    # 处理 1：模型不支持插值 → 外推
    if not _supports_interpolation(model_capability):
        return "first_frame_only"

    # 处理 2：支持插值 且 有首帧 + 有尾帧 → 插值
    if _get(shot, "first_frame_path") and _get(shot, "last_frame_path"):
        return "first_last_interpolate"

    # 处理 3：其余 → 外推（诚实：无双帧不硬造插值）
    return "first_frame_only"


def apply_anchor_mode(intent, model_capability) -> str:
    """便捷：计算并把 frame_anchor_mode 写回 intent（dict 或 dataclass）。

    返回选定的模式；调用方无需关心写回细节。
    """
    mode = choose_anchor_mode(intent, model_capability)
    try:
        if isinstance(intent, dict):
            intent["frame_anchor_mode"] = mode
        else:
            setattr(intent, "frame_anchor_mode", mode)
    except Exception as e:
        logger.warning("写回 frame_anchor_mode 失败：%s", e)
    return mode


def chain_frames(prev_shot, current_shot, extract_fn=None) -> dict:
    """输入：上一分镜产物 + 当前分镜
    处理：两步
      - 上一分镜生成完 → 抽尾帧（ffmpeg extract_last_frame）
      - 同 scene_id → 写入当前分镜首帧（跨场景不继承）
    输出：current_shot（dict，已带 first_frame_path）
    """
    ex = extract_fn or extract_last_frame
    video = _get(prev_shot, "video_path")
    if not video:
        logger.warning("chain_frames: 上一分镜无视频路径，跳过承接")
        return current_shot
    # 生产默认抽帧要求视频真实存在；注入 extract_fn（单测）时跳过存在性检查
    if extract_fn is None and not os.path.exists(video):
        logger.warning("chain_frames: 上一分镜视频文件不存在，跳过承接")
        return current_shot
    try:
        last_frame_path = ex(video)
    except Exception as e:
        logger.warning("chain_frames: 抽尾帧失败（降级不承接）：%s", e)
        return current_shot

    same_scene = (_get(prev_shot, "scene_id") is not None
                  and _get(prev_shot, "scene_id") == _get(current_shot, "scene_id"))
    if same_scene:
        if isinstance(current_shot, dict):
            current_shot["first_frame_path"] = last_frame_path
        else:
            setattr(current_shot, "first_frame_path", last_frame_path)
        logger.info("chain_frames: 同场景承接首帧 %s", last_frame_path)
    else:
        logger.info("chain_frames: 跨场景不继承首帧（防漂移）")
    return current_shot


# ---------------------------------------------------------------------------
# 工具：兼容 dict / dataclass 取字段
# ---------------------------------------------------------------------------
def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
