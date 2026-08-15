# -*- coding: utf-8 -*-
"""设备算力探测与能力分级（用户诉求：多设备兼容，不止适配 1660Ti）。

职责：
- 探测本机 NVIDIA GPU 名称与总显存（优先 nvidia-smi，回退 torch.cuda）；
- 基于显存门槛做「图像生成 / 视频生成」可行性分级（模型无关）；
- 任何探测失败都回退到「未知 / 不可用」而非抛错（诚实，绝不谎报可用算力）。

为什么需要它（用户原话）：
「这是要兼容多个电脑的，你光兼容我这个1660ti干啥，难道你还觉得他能做出来视频吗」。
→ 1660Ti 仅 6GB 显存，跑 SD1.5 图像生成可行，但视频模型（AnimateDiff / LTX / Wan 等）
  在 6GB 下极易 OOM，不应让用户傻等 30 分钟后才失败。本模块让前端 / 调度层能
  提前诚实告知「当前设备显存不足，无法生视频」。

设计准则（无创性 / 铁律）：
- 纯探测，不改动任何现有生成链路；现有 /generate 等端点行为不受影响；
- 提供独立的 /device/capabilities 与 /video/readiness 闸门接口，供前端前置校验；
- 不引入新第三方依赖（nvidia-smi 为系统命令；torch 为可选回退）。
"""
import datetime
import shutil
import subprocess

# ---- 显存门槛（单位 MB）----
# 图像生成：SD1.5 入门门槛 2GB（1660Ti 6GB 足够，图像生成可行）。
IMAGE_GEN_MIN_VRAM_MB = 2 * 1024
# 视频生成：视频模型对显存极敏感，8GB 为稳定生视频的下限（AnimateDiff/LTX/Wan 轻量）。
# 1660Ti 6GB < 8GB → 视频生成判定为不可行，与用户实测一致。
VIDEO_GEN_MIN_VRAM_MB = 8 * 1024


def _probe_nvidia_smi():
    """返回 (gpu_name, vram_total_mb)；不可得则 None。优先 nvidia-smi。"""
    try:
        smi = shutil.which("nvidia-smi")
        if not smi:
            return None
        out = subprocess.run(
            [smi, "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        line = out.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return None
        name = parts[0]
        try:
            vram = int(float(parts[1]))
        except ValueError:
            vram = None
        return (name, vram)
    except Exception:
        return None


def _probe_torch():
    """回退探测：torch.cuda（若已安装）。不可得则 None。"""
    try:
        import torch  # 可选依赖，未安装即跳过
        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            idx = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(idx)
            return (props.name, int(props.total_memory // (1024 * 1024)))
    except Exception:
        pass
    return None


def _now_iso():
    try:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
    except Exception:
        return ""


def _recommendation(vram_total_mb, video_gen):
    """给出可操作建议（中文，供前端直接展示）。"""
    if vram_total_mb is None:
        return ("未检测到 NVIDIA GPU（CUDA 不可用）。图像生成将极慢或不可用，"
                "视频生成不可用。建议在具备独立显卡的机器上运行。")
    if not video_gen:
        return ("当前显存不足以稳定生视频。可继续图像生成（SD1.5），"
                "视频生成请使用 ≥8GB 显存的显卡，或降低分辨率 / 帧数后谨慎尝试。")
    return "显存充足，图像与视频生成均可正常进行。"


def detect_device():
    """探测本机算力能力，返回结构化字典。

    返回字段：
      cuda_available    : bool 是否探测到 CUDA GPU
      gpu_name          : str|None GPU 名称
      vram_total_mb     : int|None 总显存（MB）
      vram_total_gb     : float|None 总显存（GB，保留两位小数）
      thresholds_mb     : {image_gen_min, video_gen_min}
      capabilities      : {image_generation, video_generation}
      video_gen_reason  : str 视频不可行时的原因（可行时为空）
      recommendation    : str 可操作建议
      detected_at       : str ISO 时间
    """
    info = _probe_nvidia_smi() or _probe_torch()
    cuda_available = info is not None
    gpu_name = info[0] if info else None
    vram_total_mb = info[1] if info else None

    if vram_total_mb is None:
        # 有 CUDA 但拿不到显存，或完全无 GPU：保守禁用视频，图像按 CUDA 有无判定
        image_generation = bool(cuda_available)
        video_generation = False
        if cuda_available:
            video_gen_reason = ("已检测到 CUDA 设备但无法读取显存总量，为安全起见"
                                "视频生成已禁用（视频模型对显存敏感）。")
        else:
            video_gen_reason = ("未检测到 NVIDIA GPU（CUDA 不可用）。图像生成将极慢或"
                                "不可用，视频生成不可用。建议在具备独立显卡的机器上运行。")
    else:
        image_generation = vram_total_mb >= IMAGE_GEN_MIN_VRAM_MB
        video_generation = vram_total_mb >= VIDEO_GEN_MIN_VRAM_MB
        if not video_generation:
            video_gen_reason = (
                "当前 GPU 显存 %dMB（%.1fGB）低于视频生成门槛 %dMB（%dGB）。"
                "视频模型（AnimateDiff / LTX / Wan 等）在此显存下极易 OOM，"
                "不建议生视频；可使用更强算力的显卡，或仅做图像生成。"
                % (vram_total_mb, vram_total_mb / 1024.0,
                   VIDEO_GEN_MIN_VRAM_MB, VIDEO_GEN_MIN_VRAM_MB / 1024.0)
            )
        else:
            video_gen_reason = ""

    return {
        "cuda_available": cuda_available,
        "gpu_name": gpu_name,
        "vram_total_mb": vram_total_mb,
        "vram_total_gb": round(vram_total_mb / 1024.0, 2) if vram_total_mb else None,
        "thresholds_mb": {
            "image_gen_min": IMAGE_GEN_MIN_VRAM_MB,
            "video_gen_min": VIDEO_GEN_MIN_VRAM_MB,
        },
        "capabilities": {
            "image_generation": image_generation,
            "video_generation": video_generation,
        },
        "video_gen_reason": video_gen_reason,
        "recommendation": _recommendation(vram_total_mb, video_generation),
        "detected_at": _now_iso(),
    }


def video_generation_feasible():
    """便捷判断：当前设备是否适合生视频。供调度层 / 前置闸门调用。"""
    return bool(detect_device()["capabilities"]["video_generation"])
