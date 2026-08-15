# -*- coding: utf-8 -*-
"""能力维度抽象层 · 统一出口（手册 M1 / §2.1）。

视频生成 = 能力维度组合；模型只是能力的实现，可插拔、可替换。
本包定义 6 个能力维度的统一接口（``Capability``）与「当前实现」。

导出：
- ``Capability``：维度基类；
- 6 个维度实现类：``SemanticCapability`` / ``TemporalCapability`` /
  ``ConsistencyCapability`` / ``AudioSyncCapability`` / ``CameraCapability`` /
  ``FrameAnchorCapability``；
- ``REGISTRY``：维度标识 → 实现实例；
- ``all_dimensions()`` / ``declare_all()`` / ``build_all()``：批量导出；
- ``preferred_video_model()``：按能力维度解析的优选视频模型（模型无关）；
- ``declared_implementations()``：系统「已声明支持」的实现清单（离线也可展示）。
"""
from .base import Capability
from .semantic import SemanticCapability
from .temporal import TemporalCapability
from .consistency import ConsistencyCapability
from .audio_sync import AudioSyncCapability
from .camera import CameraCapability
from .frame_anchor import FrameAnchorCapability

# 维度标识（与资源注册表 capabilities 键、前端展示顺序严格对齐）
CAPABILITY_KEYS = ["semantic", "temporal", "consistency", "audio_sync", "camera", "frame_anchor"]

REGISTRY = {
    "semantic": SemanticCapability(),
    "temporal": TemporalCapability(),
    "consistency": ConsistencyCapability(),
    "audio_sync": AudioSyncCapability(),
    "camera": CameraCapability(),
    "frame_anchor": FrameAnchorCapability(),
}

# 兼容默认：本生产管线 worker 当前默认识别 seedance-2.0-mini；
# 用户可在设置页切换 preferred_video_model，或从能力注册表解析（模型无关）。
_COMPAT_DEFAULT_VIDEO_MODEL = "seedance-2.0-mini"


def all_dimensions():
    """返回 6 个能力维度的元信息 + 声明（供 Settings / 资源注册表展示）。"""
    return [c.to_dict() for c in REGISTRY.values()]


def declare_all():
    """返回 {维度标识: declare()} 声明映射。"""
    return {k: c.declare() for k, c in REGISTRY.items()}


def build_all(intent, wf=None):
    """对结构化意图，按 6 个维度分别 build 生成条件。

    - ``wf is None``：返回 {维度标识: 条件 dict}（兼容聚合 / 锁脸计划）；
    - ``wf`` 给定：逐维度调用 ``capability.build(intent, wf)`` 真实插节点，
      返回构建后的 wf（手册 M1：6 维度能真实构建 ComfyUI 节点）。
    """
    if wf is None:
        return {k: c.build(intent) for k, c in REGISTRY.items()}
    for c in REGISTRY.values():
        c.build(intent, wf)
    return wf


def preferred_video_model():
    """按能力维度解析的优选视频模型（模型无关）。

    优先级：DB settings.preferred_video_model（用户设置 / 能力注册表解析）
            → 兼容默认 seedance-2.0-mini（保持现状，无创性）。
    任何异常都回退到兼容默认，绝不抛错（避免阻断调度）。
    """
    try:
        from db import get_conn
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='preferred_video_model'").fetchone()
            if row and row.get("value"):
                return str(row["value"])
        finally:
            conn.close()
    except Exception:
        pass
    return _COMPAT_DEFAULT_VIDEO_MODEL


def declared_implementations():
    """系统「已声明支持」的能力实现清单（离线也可展示，供 Settings 资源清单区）。

    来源：节点族注册表（node_family_mapper）的已知视频/图像实现 + 一致性/风格实现。
    标记为 declared_only=True，与「本地已检测权重」区分（诚实，不谎报已安装文件）。

    能力维度声明由标注算法（resource_catalog._video_capabilities_for_node /
    _ip_capabilities_for_node）程序生成，禁止手写死 chip（手册 §5）。
    """
    try:
        from intent_engine.node_family_mapper import NODE_FAMILY_REGISTRY
    except Exception:
        NODE_FAMILY_REGISTRY = {}
    try:
        from intent_engine.resource_catalog import (
            _video_capabilities_for_node, _ip_capabilities_for_node)
    except Exception:
        def _video_capabilities_for_node(n):  # 兜底：保守声明
            return {"frame_anchor": {"mode": "first_frame_only",
                                     "extrapolation": True,
                                     "interpolation": n == "WanVideoSampler"}}
        def _ip_capabilities_for_node(n):
            return {"consistency": {"mode": "ip_adapter", "face_lock": "ip-adapter"}}
    video_fams = NODE_FAMILY_REGISTRY.get("video_generation", {}).get("priority", [])
    ip_fams = NODE_FAMILY_REGISTRY.get("face_lock", {}).get("priority", [])
    out = []
    for node in video_fams:
        out.append({
            "name": node,
            "type": "video_model",
            "declared_only": True,
            "capabilities": _video_capabilities_for_node(node),
        })
    for node in ip_fams:
        out.append({
            "name": node,
            "type": "ip_adapter",
            "declared_only": True,
            "capabilities": _ip_capabilities_for_node(node),
        })
    return out
