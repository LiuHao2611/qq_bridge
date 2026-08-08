# -*- coding: utf-8 -*-
"""规则引擎（手册 §3.1.5 / 设计逻辑「可控性优先」/ 项⑨）。

核心调度逻辑由确定性规则引擎执行，LLM 仅负责语义解析与创意补全，
禁止自由推理修改生产流程（手册 §1.2 Rule First）。

两次强制校验点：
  - N02 解析后：`validate_script_parse` —— 分镜时长、结构化字段、镜头参数枚举、
    资产强约束（角色必须以资产 ID 标识、相似度≥0.85 才自动绑定）。不合格直接报错，
    禁止无约束的自由描述进入下游。
  - N06 品控：`validate_quality_report` —— 文件存在、时长一致、人脸相似度≥0.8。

镜头参数枚举单一来源对齐 AGENTS.md §3 不变量（后端自持一份，前端以 src/mock.js 为准）。
"""
import re

# ---- 镜头参数枚举（对齐 AGENTS.md §3 不变量，后端权威副本） -------------------
SHOT_TYPES = {
    "extreme_close_up", "close_up", "medium_close", "medium",
    "medium_full", "full", "long", "extreme_long",
}
CAMERA_MOVEMENTS = {
    "static", "pan", "tilt", "dolly", "zoom", "handheld", "aerial", "follow",
}
CAMERA_ANGLES = {
    "eye_level", "low_angle", "high_angle", "dutch", "overhead", "shoulder", "pov",
}
FOCAL_LENGTHS = {"24mm", "35mm", "50mm", "85mm", "135mm"}

# 阈值（手册）
ASSET_SIM_THRESHOLD = 0.85      # §3.1.5
DURATION_TOL = 0.5              # §3.1.5 单分镜时长误差≤0.5s
FACE_SIM_THRESHOLD = 0.8        # §3.4.5


class RuleViolation(Exception):
    """规则引擎校验不通过时抛出（携带结构化 errors/warnings）。"""

    def __init__(self, errors, warnings=None):
        self.errors = errors or []
        self.warnings = warnings or []
        msg = "规则引擎校验未通过：" + "；".join(self.errors[:5])
        super().__init__(msg)


def _enum_ok(value, allowed):
    return value in allowed


def validate_script_parse(shot_list, max_shot_duration=12, asset_bindings=None):
    """N02 解析后确定性二次校验（§3.1.5）。

    shot_list: List[dict]，每个含 shot_id/duration/shot_type/camera_params/
               scene_desc/action_desc/audio_desc/bound_assets 等。
    asset_bindings: {seg_or_shot_id: [ {asset_id, asset_type, weight, confidence} ]}
                    用于资产强约束与相似度阈值校验。

    返回 (ok, errors, warnings)。ok=False 时调用方应抛 RuleViolation 阻断下游。
    """
    errors = []
    warnings = []
    if not shot_list:
        errors.append("分镜列表为空：LLM 未返回可解析分镜")
        return False, errors, warnings

    for i, shot in enumerate(shot_list):
        tag = shot.get("shot_id") or ("shot#%d" % (i + 1))
        # 1) 结构化字段：禁止无约束自由描述（§1.2 Rule First）
        for f in ("scene_desc", "action_desc"):
            v = shot.get(f)
            if not v or not str(v).strip():
                errors.append("[%s] 缺少结构化字段 %s（禁止无约束自由描述）" % (tag, f))
        # 2) 时长：单分镜≤max + 容差（§3.1.5）
        try:
            dur = float(shot.get("duration") or 0)
        except Exception:
            dur = 0.0
        if dur <= 0:
            errors.append("[%s] 时长非法（%r）" % (tag, shot.get("duration")))
        elif dur > max_shot_duration + DURATION_TOL:
            errors.append("[%s] 时长 %.1fs 超过套餐上限 %.0fs（容差%.1fs）"
                          % (tag, dur, max_shot_duration, DURATION_TOL))
        # 3) 镜头参数枚举（AGENTS.md §3 不变量）
        cp = shot.get("camera_params") or {}
        st = shot.get("shot_type") or cp.get("shot_type")
        if st and not _enum_ok(st, SHOT_TYPES):
            errors.append("[%s] shot_type 非法枚举：%s" % (tag, st))
        mv = shot.get("camera_movement") or cp.get("camera_movement")
        if mv and not _enum_ok(mv, CAMERA_MOVEMENTS):
            errors.append("[%s] camera_movement 非法枚举：%s" % (tag, mv))
        ang = shot.get("camera_angle") or cp.get("camera_angle")
        if ang and not _enum_ok(ang, CAMERA_ANGLES):
            errors.append("[%s] camera_angle 非法枚举：%s" % (tag, ang))
        fl = shot.get("focal_length") or cp.get("focal_length")
        if fl and not _enum_ok(str(fl), FOCAL_LENGTHS):
            errors.append("[%s] focal_length 非法枚举：%s" % (tag, fl))
        # 4) 资产强约束：角色必须以资产 ID 标识，且相似度≥0.85（§3.1.5 / §1.2）
        binds = (asset_bindings or {}).get(tag) or shot.get("bound_assets") or []
        for b in binds:
            conf = b.get("confidence") if b.get("confidence") is not None else b.get("weight")
            if conf is not None and float(conf) < ASSET_SIM_THRESHOLD:
                warnings.append("[%s] 资产 %s 绑定相似度 %.2f<%.2f，标记待人工确认"
                                % (tag, b.get("asset_id"), float(conf), ASSET_SIM_THRESHOLD))
            if not b.get("asset_id"):
                errors.append("[%s] 资产绑定缺少 asset_id（角色必须以资产 ID 标识）" % tag)
    return (len(errors) == 0), errors, warnings


def validate_quality_report(report):
    """N06 品控确定性校验（§3.4.5）。report: dict（post_production._quality_check 产出）。

    返回 (passed, reasons)。人脸相似度存在且<0.8 → 不合格（触发重生成）。
    """
    reasons = []
    if not report.get("file_ok"):
        reasons.append("文件缺失/未生成")
    if not report.get("duration_ok"):
        reasons.append("时长不一致（误差%.2fs）" % (report.get("duration_error") or 0))
    face_sim = report.get("face_sim")
    if face_sim is not None and float(face_sim) < FACE_SIM_THRESHOLD:
        reasons.append("人脸相似度 %.2f<%.2f" % (float(face_sim), FACE_SIM_THRESHOLD))
    return (len(reasons) == 0), reasons


def enforce(shot_list, max_shot_duration=12, asset_bindings=None):
    """便捷：校验不通过直接抛 RuleViolation（N02 调用，阻断劣质分镜进入下游）。"""
    ok, errors, warnings = validate_script_parse(
        shot_list, max_shot_duration=max_shot_duration, asset_bindings=asset_bindings)
    if not ok:
        raise RuleViolation(errors, warnings)
    return warnings
