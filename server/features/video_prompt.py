# -*- coding: utf-8 -*-
"""视频 prompt 组装（P2 手册 M1 §4.2-§4.4 / P0 semantic.build() 依赖）。

端到端：分镜静态描述 → 动作扩写（Ollama，15s 超时+回退）→ spatial 站位描述 →
运镜片段（camera_mapping）→ 风格词（风格锚点）→ 一致句（身份卡）→ build_video_prompt
六片段拼接 → 注入生成（禁裸 shot["description"]）。

铁律：
- 动作扩写（Ollama）必须 15s 超时 + 失败回退，绝不阻塞生成主线程。
- build_video_prompt 是唯一正向视频 prompt 组装入口；任何视频生成路径都走它。
- 不依赖 PIL；Ollama 调用惰加载，离线单测可注入 ollama_fn 替换。
"""
import concurrent.futures
import logging

from features.camera_mapping import (
    CAMERA_MOVEMENT_PROMPT,
    resolve_camera_fragment,
    resolve_camera_movement,
)

logger = logging.getLogger("features.video_prompt")


# ---------------------------------------------------------------------------
# 工具：兼容 dict / dataclass 取字段
# ---------------------------------------------------------------------------
def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _subjects_of(spatial):
    if not spatial:
        return []
    if isinstance(spatial, dict):
        return spatial.get("subjects") or []
    return getattr(spatial, "subjects", None) or []


# ---------------------------------------------------------------------------
# §4.2 动作扩写（输入→处理→输出，15s 超时 + 回退）
# ---------------------------------------------------------------------------
def _default_ollama_expand(description: str) -> str:
    """默认 Ollama 扩写实现（懒加载 import，避免离线测试拉起 ai_local）。"""
    from ai_local import ollama_chat
    prompt = (
        "把以下静态画面描述扩写为'主体+动作'短语（20字内），"
        "只输出动作短语，不要解释：%s" % description
    )
    return ollama_chat(prompt, think=False, purpose="create")


def expand_action(shot, ollama_fn=None, timeout: float = 15.0) -> str:
    """输入：shot（含 description / 可选 action 字段）
    处理：三步
      1. 有显式动作字段 → 直接用
      2. 无动作 → 调 Ollama 扩写（默认真实 ollama_chat，可注入 ollama_fn 离线测）
      3. 超时(默认15s)/异常 → 退回原描述（宁可无动作，不阻塞主线程）
    输出：动作短语
    """
    # 处理 1：显式动作字段 → 直接用
    action = _get(shot, "action")
    if action:
        return action

    description = _get(shot, "description") or _get(shot, "prompt_text") or ""
    # 无描述也无动作 → 空动作（交由 build_video_prompt 兜底）
    if not description:
        return ""

    fn = ollama_fn or _default_ollama_expand
    try:
        # 处理 2：Ollama 扩写，线程池强制 15s 墙钟超时（禁主线程阻塞）
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(fn, description)
            result = fut.result(timeout=timeout)
        expanded = (result or "").strip()
        if not expanded:
            return description
        return expanded
    except (TimeoutError, concurrent.futures.TimeoutError):
        # 处理 3：超时 → 退回静态描述
        logger.warning("动作扩写超时(%.1fs)，退回静态描述", timeout)
        return description
    except Exception as e:  # 其它异常同样诚实退回，不静默
        logger.warning("动作扩写失败(%s)，退回静态描述", e)
        return description


# ---------------------------------------------------------------------------
# §4.3 spatial 站位描述（P0 v3 新增字段，输入→处理→输出）
# ---------------------------------------------------------------------------
def build_spatial_fragment(spatial) -> str:
    """输入：intent.spatial（composition + subjects[position/orientation/gaze]）
    处理：模板拼接
    输出：站位描述 prompt fragment（如 "A在画面左，facing_right，视线to_B"）
    """
    subjects = _subjects_of(spatial)
    if not subjects:
        return ""
    parts = []
    for s in subjects:
        if isinstance(s, dict):
            aid = s.get("asset_id", "")
            pos = s.get("position", "")
            ori = s.get("orientation", "")
            gaze = s.get("gaze", "")
        else:
            aid = getattr(s, "asset_id", "")
            pos = getattr(s, "position", "")
            ori = getattr(s, "orientation", "")
            gaze = getattr(s, "gaze", "")
        parts.append("%s在画面%s，%s，视线%s" % (aid, pos, ori, gaze))
    return "，".join(parts)


# ---------------------------------------------------------------------------
# §4.4 视频 prompt 组装（P0 semantic.build() 依赖，核心：六片段拼接）
# ---------------------------------------------------------------------------
def build_video_prompt(scene, action="", camera_frag="", style=None,
                       spatial_frag="", consistency_sentence="",
                       style_anchor=None) -> str:
    """输入：六个片段（scene 必须，其余可选）
    处理：拼接模板
    输出：完整视频 prompt 文本
    """
    # 处理 1：组装各片段（顺序：动作→场景→站位→运镜→风格→一致句）
    segs = []
    if action:                 segs.append(action)               # 动作
    if scene:                  segs.append(scene)               # 场景
    if spatial_frag:           segs.append(spatial_frag)        # 空间站位
    if camera_frag:            segs.append(camera_frag)         # 运镜
    if style_anchor:           # 风格（来自 P0 M5 全局风格锚点）
        sa = style_anchor if isinstance(style_anchor, dict) else {
            "lighting": getattr(style_anchor, "lighting", None),
            "mood": getattr(style_anchor, "mood", None),
        }
        sa_lighting = (sa.get("lighting") or "").replace("_", " ")
        if sa_lighting:
            segs.append(sa_lighting)
    if consistency_sentence:   segs.append(consistency_sentence)  # 一致句（来自 P1）

    # 处理 2：拼接（逗号分隔，保持顺序）
    prompt = "，".join(segs)

    # 处理 3：兜底（全空时给最低限度）
    if not prompt:
        prompt = "cinematic scene"
    return prompt


# ---------------------------------------------------------------------------
# 主链路（M1 完整调用）：编排 4.1-4.4
# ---------------------------------------------------------------------------
def build_shot_video_prompt(intent, identity_card=None, style_anchor=None,
                            ollama_fn=None, timeout: float = 15.0) -> str:
    """输入：分镜意图 + 身份卡（P1）+ 风格锚点（P0 M5）
    处理：调 4.1-4.4
    输出：prompt 文本

    兼容 dict（mock.js 直传：camera_movement / description / spatial / style_anchor）
    与 ProductionIntent dataclass（optional_controls.camera_motion / spatial / style_anchor）。
    ollama_fn / timeout 透传给 expand_action（离线单测可注入）。
    """
    # 4.1 运镜映射（超集查表）
    movement = resolve_camera_movement(intent)
    camera_frag = resolve_camera_fragment(movement)

    # 4.2 动作扩写
    action = expand_action(intent, ollama_fn=ollama_fn, timeout=timeout)

    # 4.3 站位描述
    spatial_frag = build_spatial_fragment(_get(intent, "spatial") or {})

    # 场景（分镜描述）
    scene = _get(intent, "description") or _get(intent, "prompt_text") or ""

    # 一致句（来自身份卡）
    consistency_sentence = ""
    if identity_card is not None:
        consistency_sentence = _get(identity_card, "consistency_sentence") or ""

    # 风格锚点（P0 M5）：未显式传入则从 intent 取
    if style_anchor is None:
        style_anchor = _get(intent, "style_anchor") or {}

    # 4.4 组装
    return build_video_prompt(
        scene=scene,
        action=action,
        camera_frag=camera_frag,
        style=_get(intent, "intent_tags") or [],
        spatial_frag=spatial_frag,
        consistency_sentence=consistency_sentence,
        style_anchor=style_anchor,
    )
