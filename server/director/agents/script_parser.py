# -*- coding: utf-8 -*-
"""N02 Script Parse —— ScriptParserAgent（手册 §3.1 / 设计逻辑「剧本结构化解析 Agent」）。

职责：把自然语言剧本结构化拆为分镜单元（语义拆解 + 粒度切分 + **资产自动绑定**）。
委托现有实现：ai_local.split_story_with_llm（Phase 2，已含本地/在线降级）。

相比首版骨架新增（对齐手册 §3.1.4 + 设计逻辑）：
  - 为未带 segment_id 的分镜补充分镜单元 ID（供下游绑定/回滚定位）
  - **资产自动匹配与绑定**：扫描场次文本，按关键词匹配资产库角色/场景/道具，
    相似度≥0.85（骨架用名称精确/分词包含作代理）写入 bound_assets；低于阈值标
    「待人工确认」（warning），不强制注入（Asset Strong Constraint + 非破坏性）
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..agent_interface import AgentContext, AgentResult
from ..errors import AgentError
from .base import BaseAgent

SIM_THRESHOLD = 0.85  # 手册 §3.1.5：资产匹配相似度阈值≥0.85


class ScriptParserAgent(BaseAgent):
    agent_id = "AGENT-SCRIPT-PARSER"
    agent_name = "ScriptParserAgent"

    def run(self, ctx: AgentContext) -> AgentResult:
        self._set_state("running", 0.0)
        self._log("agent_run", level="info")

        story = ctx.params.get("story")
        if not story or not str(story).strip():
            self._set_state("failed", 0.0)
            raise AgentError("MISSING_SCRIPT", "ctx.params.story 为空，无法解析剧本")

        try:
            import ai_local
            model_cfg = ctx.params.get("model_config")
            res = ai_local.split_story_with_llm(story, model_config=model_cfg)
        except Exception as e:
            self._set_state("failed", 0.0)
            raise AgentError("SCRIPT_PARSE_FAILED", "调用 split_story_with_llm 异常: %s" % e, cause=e)

        segs = res.get("segments")
        meta = res.get("meta") or {}
        if not segs:
            self._set_state("failed", 0.0)
            raise AgentError("SCRIPT_PARSE_FAILED", meta.get("error") or "模型未返回可解析的场次")

        # 分镜单元补 ID（Atomic Split：独立生成/重试/回滚的定位键）
        for seg in segs:
            if not seg.get("segment_id"):
                seg["segment_id"] = "seg_%s" % uuid.uuid4().hex[:12]

        # 资产自动绑定（仅当 project_id 可定位资产库；否则仅返回解析结果）
        bound, bind_warn, lib_size = ({}, [], 0)
        if ctx.project_id:
            asset_lib = self._load_asset_library(ctx.project_id)
            lib_size = len(asset_lib)
            if asset_lib:
                bound, bind_warn = self._auto_bind(segs, asset_lib)

        # ① 空间站位预规划（设计逻辑「空间站位预规划」）：多人/群像生成相对位置/朝向/视线蓝图
        for seg in segs:
            self._plan_stage_positions(seg, bound.get(seg.get("segment_id")) or [])

        # ② 规则引擎二次校验（项⑨ / §3.1.5）：解析必须结构化、资产强约束、相似度≥0.85；
        #    不合格直接阻断（禁止无约束自由描述进入下游）。
        rule_warnings = list(bind_warn)
        for seg in segs:
            seg_id = seg.get("segment_id")
            seg_bounds = bound.get(seg_id) or []
            asset_bindings = {sh.get("shot_id"): seg_bounds for sh in (seg.get("shots") or [])}
            try:
                from director.rule_engine import enforce
                w = enforce(seg.get("shots") or [],
                           max_shot_duration=int(seg.get("duration") or 12),
                           asset_bindings=asset_bindings)
                rule_warnings.extend(w)
            except Exception as e:
                self._set_state("failed", 0.0)
                raise AgentError("RULE_ENGINE_FAILED", "规则引擎二次校验未通过: %s" % e, cause=e)

        self._set_state("done", 100.0, segment_count=len(segs), bound_segments=len(bound))
        self._log("agent_done", level="info", segment_count=len(segs),
                  bound_segments=len(bound), asset_lib_size=lib_size,
                  needs_review=len([w for w in rule_warnings if "待人工确认" in w]))
        return AgentResult(
            status="success",
            data={"segments": segs, "meta": meta, "bound_assets": bound, "asset_lib_size": lib_size},
            warnings=rule_warnings,
        )

    # -- 空间站位预规划（设计逻辑「空间站位预规划」） -------------------------
    def _plan_stage_positions(self, seg, seg_bound):
        """多人/群像场景：生成角色相对位置/朝向/视线关系蓝图，作为生成约束避免穿模/左右混乱。"""
        role_ids = {b.get("asset_id") for b in (seg_bound or []) if b.get("asset_type") == "role"}
        for sh in (seg.get("shots") or []):
            refs = [r for r in (sh.get("refs") or []) if r]
            if len(refs) < 2:
                sh["stage_blueprint"] = None
                continue
            layout = self._layout_positions(len(refs))
            blueprint = []
            for idx, rname in enumerate(refs):
                p = layout[idx]
                blueprint.append({
                    "role": rname,
                    "position": p["position"],
                    "facing": p["facing"],
                    "gaze": "toward_partner" if len(refs) == 2 else "toward_center",
                })
            sh["stage_blueprint"] = {
                "type": "multi_character", "count": len(refs), "positions": blueprint,
            }

    @staticmethod
    def _layout_positions(n):
        """2 人：左右对望；3 人：左/中/右；>3：近似环抱分布。返回 [{position,facing}, ...]。"""
        if n == 2:
            return [{"position": "left", "facing": "right(center)"},
                    {"position": "right", "facing": "left(center)"}]
        if n == 3:
            return [{"position": "left", "facing": "center"},
                    {"position": "center", "facing": "front"},
                    {"position": "right", "facing": "center"}]
        # >3：左右两翼 + 居中若干
        out = []
        for i in range(n):
            if i == 0:
                out.append({"position": "far_left", "facing": "center"})
            elif i == n - 1:
                out.append({"position": "far_right", "facing": "center"})
            else:
                out.append({"position": "center_%d" % i, "facing": "front"})
        return out

    # -- 资产自动绑定（手册 §3.1.4 步骤4 / §3.1.5 阈值） -------------------------
    def _auto_bind(self, segments, asset_lib):
        bound = {}
        warnings = []
        # 资产 → 主阶段（selected=1 优先）映射，便于下游锁定 512 维身份向量与 stage_id
        stage_map = self._primary_stage_map([a["asset_id"] for a in asset_lib])
        for seg in segments:
            seg_id = seg.get("segment_id")
            text = " ".join([
                seg.get("scene_description") or "",
                seg.get("summary") or "",
                " ".join(seg.get("characters") or []),
                " ".join(sh.get("description") or "" for sh in (seg.get("shots") or [])),
            ]).lower()
            seg_bound = []
            for a in asset_lib:
                name = (a.get("name") or "").strip().lower()
                if not name:
                    continue
                if name in text:                      # 精确包含 → 高置信
                    conf, needs_review = 1.0, False
                else:
                    # F11 修复：原 `toks = [t for t in name if len(t) >= 2]` 将字符串当字符列表迭代，
                    # 单字符 len 恒为 1 → toks 恒为空，导致「分词重叠→低置信」分支为死代码（永不触发）。
                    # 现改为相邻二元组（bigram）切分：中文按字邻切分，英文/数字按等长窗口，
                    # 覆盖「部分重叠」语义，使低于阈值的弱匹配进入 needs_review 路径（非破坏性）。
                    toks = [name[i:i + 2] for i in range(len(name) - 1)]
                    if any(tok in text for tok in toks):   # 分词重叠 → 低置信（待人工确认）
                        conf, needs_review = 0.6, True
                    else:
                        continue
                if conf >= SIM_THRESHOLD:
                    seg_bound.append({
                        "asset_id": a["asset_id"], "asset_type": a["type"],
                        "stage_id": stage_map.get(a["asset_id"]),
                        "weight": 0.85, "confidence": conf, "needs_review": False,
                    })
                else:
                    warnings.append({
                        "segment_id": seg_id, "asset": a["name"],
                        "reason": "相似度低于阈值，待人工确认", "confidence": conf,
                    })
            if seg_bound:
                bound[seg_id] = seg_bound
        return bound, warnings

    def _primary_stage_map(self, asset_ids):
        """资产 → 主阶段 stage_id 映射（selected=1 优先，否则首阶段），供下游锁定身份向量。"""
        if not asset_ids:
            return {}
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return {}
        try:
            placeholders = ",".join("?" for _ in asset_ids)
            rows = db.fetch_all(
                conn,
                "SELECT stage_id, asset_id, selected FROM asset_stages "
                "WHERE asset_id IN (%s)" % placeholders,
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
            return {aid: r["stage_id"] for aid, r in best.items()}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _load_asset_library(self, project_id):
        """加载资产库（全局 + 本项目），供关键词匹配。"""
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return []
        try:
            rows = db.fetch_all(
                conn,
                "SELECT asset_id, name, type FROM assets "
                "WHERE is_global=1 OR project_id=?",
                (project_id,),
            )
            return [{"asset_id": r["asset_id"], "name": r["name"], "type": r["type"]} for r in rows]
        finally:
            try:
                conn.close()
            except Exception:
                pass
