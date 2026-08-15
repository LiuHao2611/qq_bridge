# -*- coding: utf-8 -*-
"""分镜连续性与「生成配方透明度」（P3 摄影指导 / 设计方案 §五 P3）。

设计原则：
  - 不新增任何独立检测模型，纯聚合导演 Agent 已有数据（shots / segment_asset_refs /
    shots.recipe_override）。
  - preview_shot_intent：把「这个分镜导演选了什么底模 / 什么 IP-Adapter / 是否首帧锚定」
    翻译成人话，暴露黑盒决策（方案核心价值点）。
  - check_continuity：镜头语言连续性（同景别/同运镜连击、角色突然消失），纯函数便于测试。

注意：本模块只读，不修改底层调度逻辑（无创性）。
"""
import json
from typing import List, Dict, Any, Optional

# 视频模型兜底：模型无关，从能力注册表 / 设置解析（避免硬编码）。
# 与 sequence_scheduler._preferred_video_model_id 同源（capabilities.preferred_video_model）。
def _default_video_model():
    try:
        from capabilities import preferred_video_model
        return preferred_video_model()
    except Exception:
        return "seedance-2.0-mini"


def _load_json(raw, default):
    if raw is None or raw == "":
        return default
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, (dict, list)) else default
    except Exception:
        return default


def preview_shot_intent(shot_id: str) -> Dict[str, Any]:
    """返回某分镜的「生成配方透明度」对象（P3）。

    数据来源：
      - shots.recipe_override：用户手动覆盖（优先级最高，标记为 user_override）
      - segment_asset_refs(asset_type='role')：是否绑定角色 → 决定 IP-Adapter 身份路
      - segments.return_last_frame：是否开启首尾帧锚定
    不做任何重推理（不调用 ComfyUI），仅聚合已有字段，避免请求期卡顿。
    """
    try:
        import db
        conn = db.get_conn()
    except Exception:
        return {"shot_id": shot_id, "available": False, "reason": "db_unavailable"}

    try:
        shot = db.fetch_one(conn, "SELECT * FROM shots WHERE shot_id=?", (shot_id,))
        if not shot:
            return {"shot_id": shot_id, "available": False, "reason": "shot_not_found"}

        segment_id = shot.get("segment_id")
        seg = db.fetch_one(conn, "SELECT * FROM segments WHERE segment_id=?", (segment_id,)) or {}
        refs = db.fetch_all(
            conn,
            "SELECT sar.asset_type, a.type, a.name FROM segment_asset_refs sar "
            "LEFT JOIN assets a ON a.asset_id=sar.asset_id WHERE sar.segment_id=?",
            (segment_id,),
        )
        role_refs = [r for r in refs if (r.get("asset_type") or r.get("type")) == "role"]
        scene_refs = [r for r in refs if (r.get("asset_type") or r.get("type")) == "scene"]

        override = _load_json(shot.get("recipe_override"), {})
        has_override = bool(override)

        # 配方透明度：底模 / IP-Adapter / 视频模型 / 首帧锚定
        base_model = override.get("base_model") or "通用写实底模"
        ip_adapter = {
            "enabled": bool(role_refs),
            "model": override.get("ip_adapter_model") or ("IP-Adapter 角色一致" if role_refs else "未启用"),
            "weight": float(override.get("ip_adapter_weight") or (0.8 if role_refs else 0.0)),
            "role_count": len(role_refs),
        }
        video_model = override.get("video_model") or _default_video_model()
        first_frame_anchor = bool(override.get("first_frame_anchor"))
        if "first_frame_anchor" not in override:
            first_frame_anchor = bool(seg.get("return_last_frame") or 0)

        # 匹配明细：用户覆盖 > 默认兜底
        if has_override:
            match_source = "user_override"
            notes = ["用户手动覆盖了生成配方，已尊重用户意图"]
        else:
            match_source = "default"
            notes = []
            if not role_refs:
                notes.append("未绑定角色资产，将使用通用模板（建议绑定角色以启用 IP-Adapter 身份一致）")
            if not scene_refs:
                notes.append("未绑定场景资产，场景风格将走默认")

        recipe = {
            "base_model": base_model,
            "ip_adapter": ip_adapter,
            "video_model": video_model,
            "first_frame_anchor": first_frame_anchor,
            "has_override": has_override,
        }
        return {
            "shot_id": shot_id,
            "available": True,
            "recipe": recipe,
            "match_details": {
                "matched": True,
                "source": match_source,
                "notes": notes,
            },
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def check_continuity(shots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """镜头语言连续性检查（纯函数，便于单元测试）。

    shots：按 index 升序的分镜列表，每个元素至少含：
      { index:int, shot_type:str, camera_movement:str, role_refs:List[str] }
    返回问题列表（severity 固定 suggestion，不影响生成，仅汇总提醒）：
      - 连续同景别 / 同运镜（≥2 连击）
      - 角色突然出现 / 消失（首镜有角色、中间镜缺失，或反之）
    """
    issues: List[Dict[str, Any]] = []
    if not shots:
        return issues

    ordered = sorted(shots, key=lambda s: s.get("index", 0))

    # 1) 连续同景别 / 同运镜
    for key, label in (("shot_type", "景别"), ("camera_movement", "运镜")):
        run_start = 0
        for i in range(1, len(ordered) + 1):
            prev = ordered[i - 1].get(key)
            cur = ordered[i].get(key) if i < len(ordered) else None
            if i < len(ordered) and cur == prev and prev:
                continue
            # 区间 [run_start, i-1] 结束
            if i - 1 > run_start and prev:
                issues.append({
                    "type": "consecutive_%s" % key,
                    "severity": "suggestion",
                    "shots": [ordered[j].get("index") for j in range(run_start, i)],
                    "value": prev,
                    "detail": "连续 %d 个分镜%s相同（%s），建议变化镜头语言" % (i - run_start, label, prev),
                })
            run_start = i

    # 2) 角色突然出现 / 消失
    present_sets = [set(s.get("role_refs") or []) for s in ordered]
    all_roles = set()
    for rs in present_sets:
        all_roles |= rs
    for role in all_roles:
        seen = False
        for idx, rs in enumerate(present_sets):
            if role in rs:
                if not seen and idx > 0:
                    issues.append({
                        "type": "role_appears",
                        "severity": "suggestion",
                        "role": role,
                        "shot": ordered[idx].get("index"),
                        "detail": "角色在分镜 %s 突然出现（前序未出现），建议补入场上" % ordered[idx].get("index"),
                    })
                seen = True
            else:
                if seen and idx < len(ordered) - 1:
                    # 中间消失（末尾消失不告警，可能本段结束）
                    issues.append({
                        "type": "role_disappears",
                        "severity": "suggestion",
                        "role": role,
                        "shot": ordered[idx].get("index"),
                        "detail": "角色在分镜 %s 突然消失，建议确认是否为刻意转场" % ordered[idx].get("index"),
                    })
                    seen = False
    return issues
