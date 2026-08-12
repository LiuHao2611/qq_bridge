# -*- coding: utf-8 -*-
"""悬浮球建议引擎：统一构造建议对象，去重后推送 WebSocket。

所有阶段的建议都走这里，保证格式一致。设计原则（方案 §2.1）：
  - 悬浮球没有自己的大脑，建议全部来自导演 Agent 层已有的数据聚合；
  - 这里只负责「格式化 + 去重节流 + 推送」，不做任何独立检测。
"""
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any

from .dedup import dedup


@dataclass
class OrbSuggestion:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    phase: str = ""               # P1/P2/P3/P4/P5
    severity: str = ""            # info / suggestion / warning / error
    category: str = ""            # asset_quality / shot_continuity / qc_face / qc_frame / degrade / script_structure / synth_quality
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    title: str = ""               # ≤28字，气泡用
    detail: str = ""              # 面板详情
    action_type: Optional[str] = None   # regenerate / skip / navigate / create_asset / override_recipe / dismiss
    action_payload: Optional[Dict[str, Any]] = None
    target_id: Optional[str] = None     # asset_id / segment_id / shot_id / task_id
    created_at: float = field(default_factory=time.time)


def build_key(s: OrbSuggestion) -> str:
    """去重键：同一项目 + 阶段 + 类别 + 指标 + 目标，5 分钟内只推一次。"""
    return "%s:%s:%s:%s:%s" % (s.project_id, s.phase, s.category, s.metric_name, s.target_id)


def _default_publish(channel: str, event: str, data: Dict[str, Any]):
    """默认推送通道：复用既有 realtime.publish（director:<pid> 频道 + orb_ 事件前缀）。"""
    try:
        from realtime import publish as ws_publish
        ws_publish(channel, event, data)
    except Exception:
        # 推送失败不得影响主链路（建议是非阻断信息）
        pass


def emit(s: OrbSuggestion, publish_fn=None) -> Optional[Dict[str, Any]]:
    """去重后推送一条建议；被去重则返回 None。

    redis/WS 注入点：publish_fn(channel, event, data) 可替换（测试时注入 mock）。
    推送频道固定为 director:<project_id>，事件名 orb_suggestion。
    """
    key = build_key(s)
    if not dedup.should_emit(key):
        return None
    data = asdict(s)
    fn = publish_fn or _default_publish
    fn("director:%s" % s.project_id, "orb_suggestion", data)
    return data


def batch_emit(suggestions: List[OrbSuggestion], publish_fn=None) -> List[Dict[str, Any]]:
    """批量 emit，返回实际推送（未被去重抑制）的列表。"""
    results = []
    for s in suggestions:
        r = emit(s, publish_fn=publish_fn)
        if r:
            results.append(r)
    return results
