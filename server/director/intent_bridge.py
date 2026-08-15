# -*- coding: utf-8 -*-
"""M4 导演 Agent 意图化·意图桥接（手册 §2.6 / M4）。

把 4 个子 Agent 的结构化输出（VisualControlAgent 的 subjects/intent_tags、
SequenceSchedulerAgent 的 camera/frame_anchor）规整为统一的 ProductionIntent，
并流转到意图翻译引擎 IntentTranslator.translate(intent)（走 M3 匹配算法），
最终产出真实 workflow。全链路日志可证（验收 M4：意图能从 Agent 流转到 translator）。

设计要点（无创性）：本桥接为「增量旁路」——
- 子 Agent 的既有输出（enhanced / clips / asset_inject_config）完全不变；
- 新增 subjects/intent_tags/camera/frame_anchor 仅为附加结构化字段；
- 生产链路（worker → translate_intent 模板同步路径）不受影响，本桥接供
  导演编排 / 测试 / 离线推演使用，证明「意图 → 翻译 → workflow」闭环成立。
"""
import asyncio
import logging

from intent_engine import (
    ProductionIntent, BoundAsset, GenerationParams, StyleConstraints, Subject, IntentTranslator,
)

LOG = logging.getLogger("director.intent_bridge")


def agent_outputs_to_intent(visual_data: dict, seq_data: dict, params: dict = None) -> ProductionIntent:
    """把视觉控制 / 时序调度两个 Agent 的结构化输出合并为一个 ProductionIntent。

    :param visual_data: VisualControlAgent 的 data（含 subjects / intent_tags）
    :param seq_data: SequenceSchedulerAgent 的 data（含 camera / frame_anchor）
    :param params: 生成参数覆盖（seed/steps/cfg/width/height/num_frames/fps）
    :return: 符合 ProductionIntent 契约的结构化意图
    """
    params = params or {}
    visual_data = visual_data or {}
    seq_data = seq_data or {}

    subjects = visual_data.get("subjects") or []
    intent_tags = list(visual_data.get("intent_tags") or [])
    camera = seq_data.get("camera") or {}
    frame_anchor = seq_data.get("frame_anchor") or {}
    spatial = seq_data.get("spatial") or {}
    # M5 全局风格锚点（遗漏 2 修复）：从 VisualControl 输出流转到意图翻译引擎，
    # 供 semantic 维度追加风格词 + consistency 维度追加全局 style IPAdapter。
    style_anchor = visual_data.get("style_anchor") or {}

    # subjects → bound_assets（一致性维度消费角色锁定，保持向后兼容）
    bound_assets = [
        BoundAsset(
            asset_id=s.get("asset_id"),
            asset_type="character",
            inject_type="face",
            weight=float(s.get("weight", 0.85)),
        )
        for s in subjects if s.get("asset_id")
    ]
    # subjects（含 role）直接透传为 ProductionIntent.subjects（手册 M4 遗漏 1 修复）
    subject_objs = [
        Subject(
            asset_id=s.get("asset_id"),
            role=s.get("role", "identity"),
            weight=float(s.get("weight", 0.85)),
        )
        for s in subjects if s.get("asset_id")
    ]

    def _int(v, d, t=int):
        try:
            return t(v)
        except Exception:
            return d

    has_frames = bool(frame_anchor.get("first_frame_path") or frame_anchor.get("last_frame_path"))
    task_type = "shot_video_with_anchor" if has_frames else "shot_video_text2video"

    gp = GenerationParams(
        seed=_int(params.get("seed"), 42),
        steps=_int(params.get("steps"), 30),
        cfg_scale=_int(params.get("cfg"), 7.0),
        width=_int(params.get("width"), 1024),
        height=_int(params.get("height"), 576),
        num_frames=_int(params.get("num_frames"), 81) if params.get("num_frames") else None,
        fps=_int(params.get("fps"), 16) if params.get("fps") else None,
    )

    intent = ProductionIntent(
        task_type=task_type,
        intent_tags=intent_tags or ["写实"],
        bound_assets=bound_assets,
        generation_params=gp,
        style_constraints=StyleConstraints(),
        optional_controls={
            "camera_motion": camera.get("movement", "static"),
            "shot_type": camera.get("shot_type", "medium"),
        },
        first_frame_path=frame_anchor.get("first_frame_path"),
        last_frame_path=frame_anchor.get("last_frame_path"),
        subjects=subject_objs,
        spatial=spatial,
        style_anchor=style_anchor,
    )

    # ---- P2 M2：根据模型能力声明选择首尾帧锚定模式（诚实降级，禁硬造假插值）----
    # 模型能力来自 SequenceSchedulerAgent 声明的 frame_anchor.mode；兼容手册 grading
    # 词汇与 P0 注册表 interpolation:bool。落库到 intent.frame_anchor_mode，
    # 供 P0 frame_anchor.build() 消费。失败降级不阻断主链路。
    try:
        from features.frame_chain import apply_anchor_mode
        fa_mode = frame_anchor.get("mode")
        model_capability = {
            "frame_anchor": {
                "mode": fa_mode or "first_frame_only",
                "interpolation": fa_mode == "first_last_interpolate",
                "grading": fa_mode,
            }
        }
        apply_anchor_mode(intent, model_capability)
    except Exception as e:
        LOG.warning("[BRIDGE] apply_anchor_mode 失败（降级不阻断）：%s", e)

    LOG.info("[BRIDGE] 子Agent输出 → ProductionIntent：task_type=%s subjects=%s "
             "intent_tags=%s camera=%s frame_anchor=%s spatial=%s style_anchor=%s",
             task_type, [(s.get("asset_id"), s.get("role")) for s in subjects],
             intent_tags, camera, frame_anchor.get("mode"), spatial.get("composition"),
             style_anchor.get("lighting"))
    return intent


def build_workflow_from_intent(intent: ProductionIntent, catalog=None,
                                object_info=None, user_prefs=None):
    """把 ProductionIntent 流转到意图翻译引擎，产出真实 workflow（手册 M4 / M3）。

    :param intent: agent_outputs_to_intent 产出的结构化意图
    :param catalog / object_info: 离线注入时可传静态目录与 /object_info（零网络）；
        不传则走 IntentTranslator 在线 refresh（需 ComfyUI 可达）。
    :return: (workflow, last_selection)
    """
    tr = IntentTranslator(catalog=catalog, object_info=object_info, user_prefs=user_prefs)
    LOG.info("[BRIDGE] 意图流转到 translator.translate（M3 匹配算法）task_type=%s",
             intent.task_type)
    wf = asyncio.run(tr.translate(intent))
    LOG.info("[BRIDGE] translator 产出 workflow：selected_node=%s checkpoint=%s",
             tr.last_selection.get("node"), tr.last_selection.get("checkpoint"))
    return wf, tr.last_selection
