# -*- coding: utf-8 -*-
"""N03 Visual Enhancement —— VisualControlAgent（手册 §3.2 / 设计逻辑「全局视觉统筹 Agent」）。

职责：全局视觉参数下发 + 真人资产特征/一致性约束注入。
委托现有实现：ai_local.enhance_with_ollama / build_segment_prompt（资产名注入不变量）。

完全按手册 §3.2 + §5.2 + §5.3 实现（禁止低配）：
  - **真人资产 512 维特征注入**：enable_real_asset 开启时，从资产库拉取每个绑定角色的
    主阶段（asset_stages），加载已存的 512 维身份向量；若无则当场用 insightface 提取并落库
    （asset_stages.face_vector）。缺人脸引擎/权重时**诚实阻断**（REAL_ASSET_INJECT_FAILED），
    不静默跳过身份锁定。
  - **资产分层权重控制**：基础身份权重默认 0.85，按年龄段在 0.7-0.95 调整；风格/年龄 LoRA
    权重区间 [0.3, 0.6]；交叉注意力层 2-4 注入；前 30 步权重上浮 10%（协议写入 asset_inject_config，
    生成端按 base_weight 实跑，详见 ai_local._comfy_text2img_with_ipadapter）。
  - **全局风格参考图**：根据主场景色温（暖/冷）由 ComfyUI 真实出图（global_style_ref），
    作为风格条件注入所有分镜（IP-Adapter 第二路风格权重）。
  - 全局参数下发：resolution / aspect_ratio / seed_strategy 透传（§3.2.2）；
    seed_strategy=inherit 跨分镜继承 seed 并微扰（§3.2.5）。
  - 产出 global_style_ref / asset_inject_config 经 upstream 透传给 N04/N05 与 worker。
"""
import os
import sys
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..agent_interface import AgentContext, AgentResult
from ..errors import AgentError
from .base import BaseAgent

INJECT_WEIGHT_DEFAULT = 0.85  # 手册 §3.2.5：真人资产特征注入权重默认 0.85
LORA_WEIGHT_RANGE = [0.3, 0.6]  # 手册 §5.2：风格/年龄 LoRA 权重区间
FACE_VECTOR_DIM = 512  # 手册 §5.1：512 维身份特征向量
CROSS_ATTN_LAYERS = "2-4"  # 手册 §5.2：注入位置 交叉注意力层第 2-4 层
FIRST_STEPS_BOOST = 0.10  # 手册 §5.2：前 30 步权重上浮 10%
STYLE_WEIGHT = 0.4  # 全局风格参考图注入权重（风格条件，低于身份权重）

# OUTPUTS = 仓库根目录 /outputs（与 ai_local / worker 一致）
_OUTPUT_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
OUTPUTS = os.path.join(_OUTPUT_BASE, "outputs")


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
        enable_real_asset = bool(gparams.get("enable_real_asset", False))

        # ------------------------------------------------------------------
        # N03-① 真人资产 512 维特征向量拉取 / 提取（手册 §5.1 / §5.2）
        # ------------------------------------------------------------------
        role_specs = self._collect_role_specs(ctx.project_id, bound)
        face_problems = []
        if enable_real_asset:
            for stage_id, spec in role_specs.items():
                vec = None
                try:
                    from features import load_face_vector
                    vec = load_face_vector(stage_id)
                except Exception:
                    vec = None
                if vec is None and spec.get("ref_image"):
                    try:
                        from features import extract_face_vector, store_face_vector
                        vec = extract_face_vector([spec["ref_image"]])  # 多张取均值
                        store_face_vector(stage_id, vec)
                        self._log("face_vector_extracted", level="info",
                                  stage_id=stage_id, role=spec.get("name"))
                    except Exception as e:
                        # 真人资产化已开启但本机缺人脸引擎/权重/无脸 → 诚实阻断（禁止低配）
                        raise AgentError(
                            "REAL_ASSET_INJECT_FAILED",
                            "真人资产化已开启，但角色【%s】无法提取 512 维身份向量：%s"
                            % (spec.get("name"), e),
                        )
                if vec is None:
                    face_problems.append("角色【%s】无可用的 512 维身份向量（参考图缺失或未提取）"
                                         % spec.get("name"))
                spec["has_vector"] = vec is not None

        # 构建 per-role 分层权重配置（基础 ID + 风格 LoRA，年龄调权）
        role_configs = {}
        for stage_id, spec in role_specs.items():
            role_configs[stage_id] = {
                "asset_id": spec.get("asset_id"),
                "name": spec.get("name"),
                "base_weight": self._age_weight(spec.get("age")),
                "lora_weight_range": list(LORA_WEIGHT_RANGE),
                "cross_attn_layers": CROSS_ATTN_LAYERS,
                "first_30_steps_boost": FIRST_STEPS_BOOST,
                "face_vector_dim": FACE_VECTOR_DIM if spec.get("has_vector") else 0,
                "has_vector": bool(spec.get("has_vector")),
                "ref_image": spec.get("ref_image"),
                "age": spec.get("age"),
            }

        # ------------------------------------------------------------------
        # N03-② 全局风格参考图（手册 §3.2.4）：暖/冷由主场景色温决定，ComfyUI 真实出图
        # ------------------------------------------------------------------
        style_ref_local, style_ref_comfy, warm_cool = self._generate_style_ref(segments, gparams)

        asset_inject_config = {
            "enable_real_asset": enable_real_asset,
            "face_vector_dim": FACE_VECTOR_DIM,
            "cross_attn_layers": CROSS_ATTN_LAYERS,
            "first_30_steps_boost": FIRST_STEPS_BOOST,
            "base_weight_default": INJECT_WEIGHT_DEFAULT,
            "lora_weight_range": list(LORA_WEIGHT_RANGE),
            "style_ref_local_path": style_ref_local,
            "style_ref_comfy_name": style_ref_comfy,
            "style_weight": STYLE_WEIGHT,
            "warm_cool": warm_cool,
            "roles": role_configs,
        }

        # ------------------------------------------------------------------
        # 全局参数下发 + 提示词增强（沿用既有资产名注入不变量）
        # ------------------------------------------------------------------
        # seed_strategy=inherit：跨分镜继承并微扰（§3.2.5）
        running_seed = None
        if seed_strategy == "inherit":
            running_seed = int((segments[0] or {}).get("seed") or 1234)

        enhanced = []
        db_names = self._load_asset_infos(ctx.project_id)
        try:
            import ai_local
            total = len(segments)
            for i, seg in enumerate(segments):
                seg_id = seg.get("segment_id")
                shots = seg.get("shots") or []
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

        self._set_state("done", 100.0, segment_count=len(enhanced),
                        real_asset=enable_real_asset, style_ref=bool(style_ref_local),
                        warm_cool=warm_cool)
        self._log("agent_done", level="info", segment_count=len(enhanced),
                  enable_real_asset=enable_real_asset, role_count=len(role_configs),
                  style_ref=bool(style_ref_local), warm_cool=warm_cool,
                  aspect_ratio=aspect_ratio, seed_strategy=seed_strategy)
        return AgentResult(
            status="success",
            data={
                "enhanced": enhanced,
                "global_style_ref": style_ref_local or "",   # 真实风格参考图路径（无则空）
                "asset_inject_config": asset_inject_config,
            },
            warnings=face_problems,
        )

    # -- 资产角色主阶段收集（用于 512 维向量拉取） -------------------------
    def _collect_role_specs(self, project_id, bound):
        """汇总所有片段绑定的角色资产 → 主阶段（selected=1 优先）的局部信息。

        返回 {stage_id: {asset_id, name, age, ref_image}}。ref_image 解析为本地可读文件
        （stage.reference_image_url / asset.asset_path / asset.cover_path），供 insightface 提取。
        """
        asset_ids = set()
        for _seg_id, lst in (bound or {}).items():
            for b in (lst or []):
                if b.get("asset_type") == "role" and b.get("asset_id"):
                    asset_ids.add(b["asset_id"])
        specs = {}
        if not asset_ids:
            return specs
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return specs
        try:
            placeholders = ",".join("?" for _ in asset_ids)
            rows = db.fetch_all(
                conn,
                "SELECT s.stage_id, s.asset_id, s.age, s.reference_image_url, s.selected, "
                "a.name, a.asset_path, a.cover_path "
                "FROM asset_stages s JOIN assets a ON a.asset_id=s.asset_id "
                "WHERE s.asset_id IN (%s)" % placeholders,
                tuple(asset_ids),
            )
            best = {}
            for r in rows:
                aid = r["asset_id"]
                if aid in best and best[aid].get("selected"):
                    continue
                if aid in best and r.get("selected"):
                    best[aid] = r
                    continue
                if aid not in best:
                    best[aid] = r
            for aid, r in best.items():
                ref = self._resolve_local_image(r.get("reference_image_url"),
                                                r.get("asset_path"), r.get("cover_path"))
                specs[r["stage_id"]] = {
                    "asset_id": aid, "name": r.get("name"), "age": r.get("age"),
                    "ref_image": ref,
                }
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return specs

    def _resolve_local_image(self, *cands):
        """把资产参考图（URL 或路径）解析为本地可读文件；找不到返回 None。"""
        for c in cands:
            if not c:
                continue
            if os.path.isfile(c):
                return c
            name = None
            if "name=" in c:  # /file?name=xxx
                name = (parse_qs(urlparse(c).query).get("name") or [None])[0]
            else:
                name = os.path.basename(c)
            if name:
                for base in (OUTPUTS, os.path.join(OUTPUTS, "assets"),
                             os.path.join(OUTPUTS, "assets", "originals")):
                    p = os.path.join(base, os.path.basename(name))
                    if os.path.isfile(p):
                        return p
        return None

    # -- 年龄调权（手册 §3.2.5：默认 0.85，范围 0.7-0.95） ------------------
    @staticmethod
    def _age_weight(age):
        try:
            age = int(age)
        except Exception:
            return INJECT_WEIGHT_DEFAULT
        if age <= 12:
            w = 0.75
        elif age >= 60:
            w = 0.90
        else:
            w = INJECT_WEIGHT_DEFAULT
        return max(0.7, min(0.95, w))

    # -- 全局风格参考图生成（手册 §3.2.4） ----------------------------------
    def _generate_style_ref(self, segments, gparams):
        """据主场景色温决定暖/冷，ComfyUI 真实出图作为 global_style_ref；无 ComfyUI 则诚实留空。

        返回 (local_path, comfy_name, warm_cool)。
        """
        main_scene = " ".join(
            (seg.get("scene_description") or seg.get("summary") or "")
            for seg in (segments or [])
        )[:400]
        warm_cool = self._decide_warm_cool(main_scene, gparams)
        try:
            import ai_local
            if not ai_local.comfy_available():
                self._log("style_ref_skip", level="warning",
                          reason="ComfyUI 未启动，跳过全局风格参考图生成（下游不注入风格条件）",
                          warm_cool=warm_cool)
                return None, None, warm_cool
            prompt = self._style_prompt(warm_cool)
            local = ai_local._comfy_text2img(prompt, width=512, height=288, steps=20, timeout=120)
            comfy_name = None
            if local and os.path.isfile(local):
                try:
                    comfy_name = ai_local._upload_to_comfy(local)
                except Exception:
                    comfy_name = None
                self._log("style_ref_generated", level="info",
                          warm_cool=warm_cool, path=local)
                return local, comfy_name, warm_cool
        except Exception as e:
            self._log("style_ref_failed", level="warning",
                      reason="全局风格参考图生成失败（不阻塞生成）: %s" % str(e)[:160],
                      warm_cool=warm_cool)
        return None, None, warm_cool

    @staticmethod
    def _decide_warm_cool(scene_text, gparams):
        forced = (gparams.get("style_tone") or "").strip().lower()
        if forced in ("warm", "cool", "neutral"):
            return forced
        cold_kw = ["夜", "办公室", "冷", "医院", "雨", "雪", "蓝", "科技", "深夜",
                   "监狱", "海底", "科幻", "阴"]
        warm_kw = ["日", "暖", "民国", "黄", "夕阳", "咖啡", "婚礼", "阳光", "童年",
                   "家", "室内", "黄昏", "橙"]
        if any(k in scene_text for k in cold_kw):
            return "cool"
        if any(k in scene_text for k in warm_kw):
            return "warm"
        return "neutral"

    @staticmethod
    def _style_prompt(warm_cool):
        if warm_cool == "cool":
            return ("cinematic color grading reference still, cold tone, low saturation, "
                    "bluish shadows, nighttime office, muted teal and slate, film still, "
                    "soft volumetric light, high detail")
        if warm_cool == "warm":
            return ("cinematic color grading reference still, warm tone, golden hour, "
                    "amber and sepia, soft sunlight, cozy interior, film still, high detail")
        return ("cinematic color grading reference still, balanced neutral tone, "
                "clean mid contrast, film still, high detail")

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
