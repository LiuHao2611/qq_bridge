# -*- coding: utf-8 -*-
"""ProductionIntent 契约（手册 §2.1 / 设计思路 §4.2）。

导演 Agent（业务层 / LLM）与意图翻译引擎之间**唯一契约**。
导演 Agent 只产出这个结构化对象，**绝不触碰 workflow JSON**（铁律 2.2 Rule First）。

字段与设计思路 §4.2 完全一致，额外提供：
- `to_dict()` / `from_dict()`：JSON 序列化 / 反序列化（验收要求字段名对齐）；
- `task_type` 合法枚举校验。
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional


# 合法 task_type（与设计思路 §4.2 / 手册 §1.3 模板清单一致）
VALID_TASK_TYPES = (
    "character_genesis",
    "scene_genesis",
    "shot_txt2img",
    "shot_img2img",
    "shot_video_text2video",
    "shot_video_with_anchor",
)


@dataclass
class BoundAsset:
    asset_id: str                                   # 资产 ID
    asset_type: str                                 # character|scene|prop
    inject_type: str                                # face|style|pose|none
    weight: float                                   # 注入权重
    face_vector: Optional[str] = None               # base64 512 维向量


@dataclass
class Subject:
    """参考按作用绑定（手册 M4 / 遗漏 1 修复）。

    role 决定这张参考「绑什么」：
      - "identity"：锁人脸（一致性维度走 IPAdapterFaceID + 512 维向量）；
      - "outfit" ：锁服装（一致性维度走普通 IPAdapter，权重更低 ≤0.6）。
    weight 为该 subject 的注入权重。
    """
    asset_id: str
    role: str = "identity"                          # identity | outfit
    weight: float = 0.85


@dataclass
class GenerationParams:
    pose: Optional[str] = None
    width: int = 512
    height: int = 768
    seed: int = 0
    steps: int = 30
    cfg_scale: float = 7.0
    num_frames: Optional[int] = None                # 视频专用
    fps: Optional[int] = None                       # 视频专用


@dataclass
class StyleConstraints:
    color_temperature: Optional[int] = None
    lighting: Optional[str] = None


@dataclass
class ProductionIntent:
    task_type: str                                  # 见 VALID_TASK_TYPES
    intent_tags: List[str]                          # 风格标签
    bound_assets: List[BoundAsset] = field(default_factory=list)
    generation_params: GenerationParams = field(default_factory=GenerationParams)
    style_constraints: StyleConstraints = field(default_factory=StyleConstraints)
    optional_controls: dict = field(default_factory=dict)   # {pose_control, depth_control}
    first_frame_path: Optional[str] = None          # 首帧（视频锚定用）
    last_frame_path: Optional[str] = None           # 尾帧（视频锚定用）
    # ---- 手册 M4 新增（遗漏 1 / 遗漏 3 修复）----
    subjects: List[Subject] = field(default_factory=list)   # 参考按作用绑定（含 role）
    spatial: dict = field(default_factory=dict)             # 空间站位蓝图（多人同框）
    style_anchor: dict = field(default_factory=dict)        # 全局风格锚点（M5）
    # ---- P2 M2 新增：首尾帧锚定模式（由 choose_anchor_mode 写入，frame_anchor.build 消费）----
    frame_anchor_mode: Optional[str] = None                 # "first_frame_only" | "first_last_interpolate"

    def __post_init__(self):
        if self.task_type not in VALID_TASK_TYPES:
            raise ValueError(
                "非法 task_type=%r，合法值：%s" % (self.task_type, VALID_TASK_TYPES))

    # ---- JSON 序列化 / 反序列化（验收 §2.1）----

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "intent_tags": list(self.intent_tags),
            "bound_assets": [asdict(a) for a in self.bound_assets],
            "generation_params": asdict(self.generation_params),
            "style_constraints": asdict(self.style_constraints),
            "optional_controls": dict(self.optional_controls),
            "first_frame_path": self.first_frame_path,
            "last_frame_path": self.last_frame_path,
            "subjects": [asdict(s) for s in self.subjects],
            "spatial": dict(self.spatial),
            "style_anchor": dict(self.style_anchor),
            "frame_anchor_mode": self.frame_anchor_mode,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProductionIntent":
        ba = [BoundAsset(**a) for a in (d.get("bound_assets") or [])]
        gp = GenerationParams(**(d.get("generation_params") or {}))
        sc = StyleConstraints(**(d.get("style_constraints") or {}))
        subs = [Subject(**s) for s in (d.get("subjects") or [])]
        return cls(
            task_type=d["task_type"],
            intent_tags=list(d.get("intent_tags") or []),
            bound_assets=ba,
            generation_params=gp,
            style_constraints=sc,
            optional_controls=dict(d.get("optional_controls") or {}),
            first_frame_path=d.get("first_frame_path"),
            last_frame_path=d.get("last_frame_path"),
            subjects=subs,
            spatial=dict(d.get("spatial") or {}),
            style_anchor=dict(d.get("style_anchor") or {}),
            frame_anchor_mode=d.get("frame_anchor_mode"),
        )
