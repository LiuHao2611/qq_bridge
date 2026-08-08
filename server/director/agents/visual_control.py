# -*- coding: utf-8 -*-
"""N03 Visual Enhancement —— VisualControlAgent（手册 §3.2 / 设计逻辑「全局视觉统筹 Agent」）。

职责：全局视觉参数下发 + 真人资产特征/一致性约束注入。
委托现有实现：ai_local.enhance_with_ollama / build_segment_prompt（资产名注入不变量）。

相比首版骨架新增（对齐手册 §3.2 + 设计逻辑）：
  - 消费上游 bound_assets：把自动绑定的资产名注入提示词不变量（Asset Strong Constraint）
  - 全局参数下发：resolution / aspect_ratio / seed_strategy 透传到下游生成（§3.2.2）
  - seed_strategy=inherit：跨分镜继承 seed 并做微扰（§3.2.5）
  - 产出 global_style_ref / asset_inject_config（weight 默认 0.85，§5.2 / §3.2.5）
  - 真人资产 512 维特征注入、风格 LoRA、全局风格参考图在 full 版接入 ComfyUI 时补全
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..agent_interface import AgentContext, AgentResult
from ..errors import AgentError
from .base import BaseAgent

INJECT_WEIGHT_DEFAULT = 0.85  # 手册 §3.2.5：真人资产特征注入权重默认 0.85


class VisualControlAgent(BaseAgent):
    agent_id = "AGENT-VISUAL-CONTROL"
    agent_name = "VisualControlAgent"

    def run(self, ctx: AgentContext) -> AgentResult:
        self._set_state("running", 0.0)
        self._log("agent_run", level="info")

        segments = ctx.upstream.get("segments") or []
        if not segments:
            self._set_state("failed", 0.0)
            raise AgentError("VISUAL_ENHANCE_FAILED", "上游缺少 segments，无法注入视觉参数")

        bound = ctx.upstream.get("bound_assets") or {}
        gparams = ctx.params.get("global_params") or {}
        aspect_ratio = gparams.get("aspect_ratio") or ctx.params.get("aspect_ratio") or "16:9"
        seed_strategy = gparams.get("seed_strategy") or ctx.params.get("seed_strategy") or "fixed"
        style = ctx.params.get("visual_style", "cinematic")

        # seed_strategy=inherit：跨分镜继承并微扰（§3.2.5）
        running_seed = None
        if seed_strategy == "inherit":
            running_seed = int((segments[0] or {}).get("seed") or 1234)

        enhanced = []
        db_names = self._load_asset_infos(ctx.project_id)  # DB 兜底资产名
        try:
            import ai_local
            total = len(segments)
            for i, seg in enumerate(segments):
                seg_id = seg.get("segment_id")
                shots = seg.get("shots") or []
                # 资产名集合 = 上游自动绑定 + DB 兜底
                bound_names = [self._asset_name(ctx.project_id, b["asset_id"])
                               for b in (bound.get(seg_id) or [])]
                asset_infos = list(db_names)
                for n in bound_names:
                    if n and n not in [d["name"] for d in asset_infos]:
                        asset_infos.append({"name": n, "type": "role"})

                scene = seg.get("scene_description") or ""
                scene_enh = ai_local.enhance_with_ollama(scene, style=style) if scene else scene
                shot_prompts = []
                for sh in shots:
                    prompt = ai_local.build_segment_prompt(seg, [sh], asset_infos)
                    shot_prompts.append({"shot_id": sh.get("shot_id") or sh.get("caption"),
                                         "prompt": prompt})
                # inherit seed 微扰（+i）
                if running_seed is not None:
                    running_seed = running_seed + i
                enhanced.append({
                    "segment_id": seg_id,
                    "scene_description_enhanced": scene_enh,
                    "shot_prompts": shot_prompts,
                    "aspect_ratio": aspect_ratio,
                    "seed": running_seed if seed_strategy == "inherit" else seg.get("seed"),
                })
                self._set_state("running", (i + 1) / total * 100.0)
        except Exception as e:
            self._set_state("failed", 0.0)
            raise AgentError("VISUAL_ENHANCE_FAILED", "视觉参数注入异常: %s" % e, cause=e)

        asset_inject_config = {
            "enable_real_asset": bool(gparams.get("enable_real_asset", False)),
            "base_weight": INJECT_WEIGHT_DEFAULT,
            "lora_weight_range": [0.3, 0.6],   # 手册 §5.2 风格/年龄 LoRA 权重
        }
        self._set_state("done", 100.0, segment_count=len(enhanced))
        self._log("agent_done", level="info", segment_count=len(enhanced),
                  aspect_ratio=aspect_ratio, seed_strategy=seed_strategy)
        return AgentResult(
            status="success",
            data={
                "enhanced": enhanced,
                "global_style_ref": "",   # full 版：生成全局风格参考图并写入
                "asset_inject_config": asset_inject_config,
            },
        )

    def _load_asset_infos(self, project_id):
        if not project_id:
            return []
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return []
        try:
            rows = db.fetch_all(
                conn,
                "SELECT a.name, a.type FROM segment_asset_refs r "
                "JOIN assets a ON a.asset_id=r.asset_id "
                "WHERE r.segment_id IN (SELECT segment_id FROM segments WHERE project_id=?)",
                (project_id,),
            )
            return [{"name": r["name"], "type": r["type"]} for r in rows]
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _asset_name(self, project_id, asset_id):
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return None
        try:
            row = db.fetch_one(conn, "SELECT name FROM assets WHERE asset_id=?", (asset_id,))
            return row.get("name") if row else None
        finally:
            try:
                conn.close()
            except Exception:
                pass
