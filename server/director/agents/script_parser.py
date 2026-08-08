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

        self._set_state("done", 100.0, segment_count=len(segs), bound_segments=len(bound))
        self._log("agent_done", level="info", segment_count=len(segs),
                  bound_segments=len(bound), asset_lib_size=lib_size,
                  needs_review=len(bind_warn))
        return AgentResult(
            status="success",
            data={"segments": segs, "meta": meta, "bound_assets": bound, "asset_lib_size": lib_size},
            warnings=bind_warn,
        )

    # -- 资产自动绑定（手册 §3.1.4 步骤4 / §3.1.5 阈值） -------------------------
    def _auto_bind(self, segments, asset_lib):
        bound = {}
        warnings = []
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
                    toks = [t for t in name if len(t) >= 2]
                    if any(tok in text for tok in toks):   # 分词重叠 → 低置信
                        conf, needs_review = 0.6, True
                    else:
                        continue
                if conf >= SIM_THRESHOLD:
                    seg_bound.append({
                        "asset_id": a["asset_id"], "asset_type": a["type"],
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
