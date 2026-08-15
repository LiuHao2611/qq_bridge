# -*- coding: utf-8 -*-
"""视频质量验收：运动检测 + 自动重试（P2 手册 M3 §4）。

端到端：视频生成 → 抽帧 → 帧间差异 → 判定静态/动态 → 静态则换 seed 重试 ≤2 →
仍静态标 failed + 提示（WS 推送 + 站内信由 mark_failed_fn 负责）。

铁律：
- 静态 PPT 禁静默通过：必须 detect + 重试 + 提示。
- 换 seed 用 parameter_injector.derive_seed（SHA256），禁 Python 内置 hash()。
- 生产抽帧走 ffmpeg；单测注入 extract_fn / frame_diff_fn / regenerate_fn 离线验证。
"""
import logging

from features.ffmpeg_utils import extract_frames, video_frame_indices
from intent_engine.parameter_injector import derive_seed

logger = logging.getLogger("features.motion_qc")

# 运动分数阈值（归一化 [0,1]，= 帧间灰度平均绝对差 / 255）；< 0.02 判静态
STATIC_THRESHOLD = 0.02


# ---------------------------------------------------------------------------
# 帧间差异（PIL：生产/测试均可用；生产抽帧走 ffmpeg）
# ---------------------------------------------------------------------------
def frame_diff(path_a: str, path_b: str) -> float:
    """输入：两帧 PNG 路径
    处理：灰度化 → 像素差均值（归一化到 [0,1]）
    输出：差异分数（0=完全相同，越高越不同）
    """
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        raise RuntimeError("frame_diff 需要 PIL（生产环境已安装）")
    a = Image.open(path_a).convert("L")
    b = Image.open(path_b).convert("L")
    # 尺寸不一致 → resize 到较小者，保证可比
    if a.size != b.size:
        w = min(a.size[0], b.size[0])
        h = min(a.size[1], b.size[1])
        a = a.resize((w, h))
        b = b.resize((w, h))
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    mean = stat.mean[0] if stat.mean else 0.0
    return float(mean) / 255.0


# ---------------------------------------------------------------------------
# 运动检测（输入→处理→输出）
# ---------------------------------------------------------------------------
def detect_motion(video_path, frame_diff_fn=None, extract_fn=None) -> dict:
    """输入：生成的视频文件
    处理：抽 3 帧（前/中/后）→ 帧间差异 → 阈值判定
    输出：{is_static: bool, motion_score: float}
    """
    ex = extract_fn or extract_frames
    fd = frame_diff_fn or frame_diff
    indices = video_frame_indices(video_path, 3)
    try:
        frames = ex(video_path, indices)
    except Exception as e:
        logger.warning("detect_motion: 抽帧失败（保守判静态待重试）：%s", e)
        return {"is_static": True, "motion_score": 0.0, "note": "frame_extract_failed"}
    if len(frames) < 2:
        logger.warning("detect_motion: 帧数不足，保守判静态")
        return {"is_static": True, "motion_score": 0.0, "note": "too_few_frames"}

    d1 = fd(frames[0], frames[1])                       # 前→中
    d2 = fd(frames[1], frames[2]) if len(frames) >= 3 else d1  # 中→后
    motion_score = (d1 + d2) / 2.0
    is_static = motion_score < STATIC_THRESHOLD          # 阈值判定
    return {"is_static": bool(is_static), "motion_score": float(motion_score)}


# ---------------------------------------------------------------------------
# seed 扰动（复用 P0 derive_seed：SHA256，禁 Python hash()）
# ---------------------------------------------------------------------------
def perturb_seed(seed, character_name: str = "", pose: str = "") -> int:
    """输入：当前 seed（+ 角色/姿势，用于确定性派生）
    处理：以 SHA256 派生偏移量，叠加到原 seed（每次 pose=str(seed) 变化 → 重试不同）
    输出：新 seed（[0, 2^31)）
    """
    eff_name = character_name or "p2_perturb"
    eff_pose = pose or str(seed)
    offset = derive_seed(eff_name, eff_pose)
    return (int(seed) + offset) % (2 ** 31)


# ---------------------------------------------------------------------------
# 重试流程（输入→处理→输出）
# ---------------------------------------------------------------------------
def qc_and_retry(shot, task, regenerate_fn=None, detect_fn=None,
                 mark_failed_fn=None, max_retry: int = 2) -> dict:
    """输入：分镜 + 生成任务（含 result_url / seed）
    处理：检测 → 换 seed 重生成 ≤2 → 仍静态标 failed
    输出：最终检测结果 {is_static, motion_score}

    生产装配：未注入 regenerate_fn / mark_failed_fn 时，懒加载 worker 真实实现；
    懒加载失败则记录错误并返回当前结果（禁静默吞，但也不无限空转）。
    """
    det = detect_fn or detect_motion
    regen = regenerate_fn
    mark = mark_failed_fn

    result = det(task.get("result_url") or task.get("video_path"))
    retry = 0
    while result["is_static"] and retry < max_retry:
        # 换 seed 重试（seed 扰动，避免复刻同错画面）
        task["seed"] = perturb_seed(task.get("seed", 0))
        if regen is None:
            try:
                from worker import regenerate_video_task
                regen = regenerate_video_task
            except Exception as e:
                logger.error("qc_and_retry: 未注入 regenerate_fn 且懒加载失败：%s", e)
                break
        try:
            result = det(regen(task))
        except Exception as e:
            # 换 seed 重生成失败（如 ComfyUI 不可达 / 生成管线异常）：
            # 保守判静态并停止重试，交由下方 mark_failed 标 failed + 提示（禁静默吞、不无限空转）
            logger.error("qc_and_retry: regenerate_fn 执行失败，保守判静态待标记：%s", e)
            break
        retry += 1

    if result["is_static"]:
        # 仍静态 → 标记 failed + 提示（WS 推送 / 站内信由 mark_failed_fn 负责）
        if mark is None:
            try:
                from worker import mark_video_task_failed
                mark = mark_video_task_failed
            except Exception:
                mark = None
        if mark:
            try:
                mark(task, "运动检测未通过")
            except Exception as e:
                logger.error("qc_and_retry: mark_failed 失败：%s", e)
        else:
            logger.error("qc_and_retry: 视频判定静态且无 mark_failed_fn，需人工介入")
    return result
