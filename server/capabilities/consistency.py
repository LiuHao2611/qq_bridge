# -*- coding: utf-8 -*-
"""一致性锚定维度（手册 M1 §2.1 / 维度 3）。

职责：锁定角色 / 风格，保证跨分镜一致（人脸锁、风格锁）。
实现：由 IP-Adapter 家族（IPAdapterFaceID / InstantID）承担身份与风格锚定；
build() 从绑定资产推导锁定的模式、权重与参考向量来源，并在 wf 模式真实插入
IPAdapterFaceID 节点（手册 M1 验收硬要求：consistency.build 产出 workflow 含
IPAdapterFaceID 节点）。
"""
import os
from typing import Any, Dict, List, Optional

from .base import Capability
from .workflow_builder import WorkflowBuilder


class ConsistencyCapability(Capability):
    key = "consistency"
    name = "一致性锚定"
    implementer = "IP-Adapter 家族（IPAdapterFaceID / InstantID）"
    description = "锁定角色身份与视觉风格，保证跨分镜一致性。"

    DEFAULT_FACE_WEIGHT = 0.85
    DEFAULT_STYLE_WEIGHT = 0.4

    def declare(self) -> Dict[str, Any]:
        return {
            "mode": "i2v_first_frame",
            "face_lock": "ip-adapter",
            "style_lock": "ip-adapter",
            "face_vector_dim": 512,
        }

    def build(self, intent, wf: Optional[Any] = None) -> Any:
        """输入：subjects（含 role 字段）→ 处理 → 输出：注入后的 model。

        实现逻辑（手册 M1 §2.1 / 维度 3，遗漏 1 修复）：
          - 输入：intent.subjects（List[Subject]，含 role）；缺失则回退 intent.bound_assets
                 合成 subjects（role 默认 identity）。
          - 处理：逐 subject 分支——
              role=="identity" → 调 IPAdapterFaceID（锁人脸，weight=subject.weight）；
              role=="outfit"   → 调 IPAdapter（普通版锁服装，weight=min(subject.weight,0.6)）。
              每个 subject 链路：上一个节点输出作为下一个节点的 model 输入（串链）。
          - 输出：
              wf=None → 返回结构化 dict（lock_mode / 各 subject 注入摘要）；
              wf 给定 → 真实插入对应 IP-Adapter 节点，返回 wf。
        """
        subjects = self._resolve_subjects(intent)

        # M5 全局风格锚点（遗漏 2 修复）：全局项目级风格参考图（跨分镜统一画风）。
        # 在 wf 模式作为末端 style IPAdapter 注入；dict 模式仅作状态上报。
        style_ref = (getattr(intent, "style_anchor", None) or {}).get("reference_image")

        # 提取人脸向量（用于 dict 模式摘要 + wf 模式 face_embeds 探测）
        face_refs: List[str] = []
        for s in subjects:
            fv = s.get("face_vector")
            if fv and s.get("role") == "identity":
                face_refs.append(str(fv))

        result: Dict[str, Any] = {
            "lock_mode": "i2v_first_frame" if face_refs else "style_only",
            "ip_adapter_weight": self.DEFAULT_FACE_WEIGHT if face_refs else self.DEFAULT_STYLE_WEIGHT,
            "face_refs": face_refs,
            "face_vector_dim": 512 if face_refs else 0,
            "injected_subjects": [
                {"asset_id": s.get("asset_id"), "role": s.get("role"),
                 "weight": s.get("weight")}
                for s in subjects
            ],
            "style_anchor_applied": bool(style_ref),
        }
        if wf is None:
            return result

        # ---- wf 模式：真实插入 IP-Adapter 节点（手册 M1 硬要求）----
        # 模型源链：首节点接 CheckpointLoaderSimple，后续接上一节点输出（串链）
        model_link = None
        if isinstance(wf, WorkflowBuilder):
            cp_id, _ = wf.find_node("CheckpointLoaderSimple")
            if cp_id is not None:
                model_link = [cp_id, 0]

        for s in subjects:
            role = s.get("role", "identity")
            asset_id = s.get("asset_id")
            if role == "outfit":
                # 普通 IPAdapter 锁服装，权重更低（≤0.6）
                w = min(float(s.get("weight", self.DEFAULT_STYLE_WEIGHT)), 0.6)
                node_id = wf.add_node(
                    "IPAdapter",
                    {
                        "model": model_link,
                        "ipadapter_file": self._resolve_ipadapter_file(),
                        "weight": w,
                        "image": self._load_ref_image(asset_id),
                    },
                    title="一致性锚定·IPAdapter(服装)",
                )
            else:
                # 默认 identity：IPAdapterFaceID 锁人脸
                w = float(s.get("weight", self.DEFAULT_FACE_WEIGHT))
                node_id = wf.add_node(
                    "IPAdapterFaceID",
                    {
                        "model": model_link,
                        "ipadapter_file": self._resolve_ipadapter_file(),
                        "weight": w,
                        "face_embeds": self._load_face_vector(asset_id) or s.get("face_vector"),
                    },
                    title="一致性锚定·IPAdapterFaceID",
                )
            # 串链：本节点输出作为下一 subject 的 model 输入
            model_link = [node_id, 0]

        # ---- M5 全局风格锚点注入（遗漏 2 修复）----
        # 输入：intent.style_anchor.reference_image（全局项目级风格参考图）
        # 处理：在 subject 串链末端追加一个普通 IPAdapter（style 路径，
        #       权重=DEFAULT_STYLE_WEIGHT），把全局风格条件接到 model 链尾。
        # 输出：每个分镜 workflow 末端均含全局风格条件（跨分镜统一画风）。
        if style_ref and isinstance(wf, WorkflowBuilder) and model_link is not None:
            wf.add_node(
                "IPAdapter",
                {
                    "model": model_link,
                    "ipadapter_file": "ip-adapter_sd15.safetensors",
                    "weight": self.DEFAULT_STYLE_WEIGHT,
                    "image": style_ref,
                },
                title="全局风格锚点·IPAdapter",
            )
        return wf

    # ------------------------------------------------------------------
    # M1 角色分支辅助（输入解析 + 资产加载）
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_subjects(intent) -> List[Dict[str, Any]]:
        """从 intent 解析 subjects 列表（统一为 dict）。

        输入：intent.subjects（List[Subject]）→ 转 dict；若为空则回退 intent.bound_assets
        合成 subjects（asset_type=character / inject_type∈{face,style} 视为 identity 锁脸）。
        """
        subs = getattr(intent, "subjects", None) or []
        out: List[Dict[str, Any]] = []
        for s in subs:
            if isinstance(s, dict):
                out.append({
                    "asset_id": s.get("asset_id"),
                    "role": s.get("role", "identity"),
                    "weight": s.get("weight", 0.85),
                    "face_vector": s.get("face_vector"),
                })
            else:  # Subject dataclass
                out.append({
                    "asset_id": getattr(s, "asset_id", None),
                    "role": getattr(s, "role", "identity"),
                    "weight": getattr(s, "weight", 0.85),
                    "face_vector": getattr(s, "face_vector", None),
                })
        if out:
            return out
        # 回退：从 bound_assets 合成 identity 锁脸 subjects
        for a in (getattr(intent, "bound_assets", None) or []):
            at = getattr(a, "asset_type", None)
            inj = getattr(a, "inject_type", None)
            if at == "character" or inj in ("face", "style"):
                out.append({
                    "asset_id": getattr(a, "asset_id", None),
                    "role": "identity",
                    "weight": getattr(a, "weight", 0.85),
                    "face_vector": getattr(a, "face_vector", None),
                })
        return out

    def _load_face_vector(self, asset_id):
        """加载角色 512 维身份向量（来自资产主阶段）。缺失返回 None 并告警（诚实，不静默）。"""
        if not asset_id:
            return None
        try:
            from features import load_face_vector
            vec = load_face_vector(asset_id)
            if vec:
                return vec
        except Exception as e:
            logger = __import__("logging").getLogger("consistency")
            logger.warning("[LOCK] identity 向量加载失败 asset=%s: %s", asset_id, e)
        return None

    def _resolve_ipadapter_file(self):
        """解析 IP-Adapter 权重文件名（P0 依赖 find_ipadapter_weight，手册 §3）。

        缺失时诚实告警并回退默认文件名（graph 仍可构建，便于离线单测），
        真正生成时由探针/编排层（find_ipadapter_weight）诚实阻断，杜绝静默「无锁脸」。
        """
        try:
            from features import find_ipadapter_weight
            return find_ipadapter_weight()
        except Exception as e:
            logger = __import__("logging").getLogger("consistency")
            logger.warning("[IPADAPTER] 权重探测失败，回退默认文件名：%s", e)
            return "ip-adapter_sd15.safetensors"

    def _load_ref_image(self, asset_id):
        """加载角色参考图（服装锁用 IPAdapter 的 image 输入）。缺失返回 None 并告警。

        优先用 P0 依赖 load_reference_image（标准肖像本地路径，手册 §0.3）；
        缺失回退既有 stage 查询。均缺失返回 None 并告警（诚实，不静默）。
        """
        if not asset_id:
            return None
        try:
            from features import load_reference_image
            p = load_reference_image(asset_id)
            if p:
                return p
        except Exception:
            pass
        # 回退：既有 stage 查询逻辑
        try:
            import db
            conn = db.get_conn()
            try:
                row = conn.execute(
                    "SELECT reference_image_url, asset_path, cover_path FROM asset_stages s "
                    "JOIN assets a ON a.asset_id=s.asset_id WHERE s.asset_id=? "
                    "ORDER BY selected DESC, stage_id LIMIT 1", (asset_id,)
                ).fetchone()
                if row:
                    for c in (row["reference_image_url"], row["asset_path"], row["cover_path"]):
                        if c and (os.path.isfile(c) if "http" not in str(c) else False):
                            return c
            finally:
                conn.close()
        except Exception as e:
            logger = __import__("logging").getLogger("consistency")
            logger.warning("[LOCK] 服装参考图加载失败 asset=%s: %s", asset_id, e)
        return None


    # ======================================================================
    # P1 M3 Lock-Then-Animate · 四杠杆（一致性注入核心，增量，不破 P0 契约）
    # 四杠杆缺一不可（红线）：
    #   ① 参考图像：标准肖像 + 参考图集喂给 IP-Adapter（需权重）
    #   ② 起始帧锚定：标准肖像做 i2v 首帧
    #   ③ 主体特征：512 维身份向量注入
    #   ④ 提示词锚点：一致句 + 服装/发型/体型逐字写进 prompt
    # ======================================================================
    FOUR_LEVERS = (
        ("reference_image", "标准肖像 + 参考图集喂给 IP-Adapter（需权重）"),
        ("first_frame", "标准肖像做 i2v 首帧锚定"),
        ("subject_vector", "512 维身份向量注入（主体特征）"),
        ("prompt_anchor", "一致句 + 服装/发型/体型逐字写进 prompt"),
    )

    @staticmethod
    def _field(a: Any, key: str) -> Optional[Any]:
        """兼容 dict / BoundAsset / 任意对象读取字段。"""
        if isinstance(a, dict):
            return a.get(key)
        return getattr(a, key, None)

    def build_lock_plan(self, intent: Any) -> Dict[str, Any]:
        """四杠杆 Lock-Then-Animate 计划（增量；不破 P0 build() 契约）。

        从绑定角色资产推导四杠杆状态；参考图集（杠杆①②）与一致句锚点（杠杆④）
        按 asset_id 从 DB 实时加载（features.get_reference_set / get_identity_card），
        保证数据真实、不硬编码。缺失即标记为 missing（诚实，不假装全锁）。

        返回：
          levers: 四杠杆逐项 {present, ...}
          reference_images: [标准肖像 URL, 4 张参考图 URL]（IP-Adapter 输入）
          first_frame: 标准肖像 URL（i2v 首帧）
          face_vector: 首个角色 512 维向量（base64 或 None）
          consistency_prompt: 杠杆④ 逐字锚点（一致句 + 服装 + 发型 + 体型）
          lock_mode: i2v_first_frame / style_only
        """
        bound = self._field(intent, "bound_assets") or []
        reference_images: List[str] = []
        first_frame: Optional[str] = None
        face_vector: Optional[str] = None
        consistency_prompt = ""
        character_name = ""
        has_character = False
        for a in bound:
            at = self._field(a, "asset_type")
            inj = self._field(a, "inject_type")
            if at == "character" or inj in ("face", "style"):
                has_character = True
                if not face_vector and self._field(a, "face_vector"):
                    face_vector = str(self._field(a, "face_vector"))
                # 杠杆①②④：按 asset_id 从 DB 加载参考图集 + 身份卡
                aid = self._field(a, "asset_id")
                if aid:
                    ref_imgs, sp, cp, fv_db, cname = self._load_lock_sources(aid)
                    for u in ref_imgs:
                        if u and u not in reference_images:
                            reference_images.append(u)
                    if sp and not first_frame:
                        first_frame = sp
                    if cp and not consistency_prompt:
                        consistency_prompt = cp
                    if fv_db and not face_vector:
                        face_vector = fv_db
                    if cname and not character_name:
                        character_name = cname
        lock_mode = (
            "i2v_first_frame"
            if (has_character and (first_frame or reference_images or face_vector))
            else "style_only"
        )
        levers = {
            "reference_image": {"present": bool(reference_images), "count": len(reference_images)},
            "first_frame": {"present": bool(first_frame)},
            "subject_vector": {"present": bool(face_vector)},
            "prompt_anchor": {"present": bool(consistency_prompt)},
        }
        return {
            "levers": levers,
            "reference_images": reference_images,
            "first_frame": first_frame,
            "face_vector": face_vector,
            "consistency_prompt": consistency_prompt,
            "character_name": character_name,
            "lock_mode": lock_mode,
            "face_vector_dim": 512 if face_vector else 0,
        }

    def _load_lock_sources(self, asset_id: str):
        """从 DB 加载角色参考图集（杠杆①②）+ 一致句锚点（杠杆④）。无则空。"""
        import features
        reference_images: List[str] = []
        first_frame: Optional[str] = None
        consistency_prompt = ""
        character_name = ""
        primary_vector: Optional[str] = None
        try:
            from db import get_conn
            conn = get_conn()
            try:
                items = features.get_reference_set(conn, asset_id)
                row = conn.execute(
                    "SELECT name FROM assets WHERE asset_id=?", (asset_id,)
                ).fetchone()
                character_name = row["name"] if row else ""
                fv = conn.execute(
                    "SELECT face_vector FROM asset_stages WHERE asset_id=? "
                    "ORDER BY selected DESC, stage_id LIMIT 1", (asset_id,)
                ).fetchone()
                if fv and fv["face_vector"]:
                    primary_vector = str(fv["face_vector"])
            finally:
                conn.close()
            for it in items:
                url = it.get("image_url")
                if it.get("ref_kind") == "standard_portrait":
                    first_frame = url
                    if url:
                        reference_images.append(url)
                elif url:
                    reference_images.append(url)
        except Exception as e:  # 无参考图集不致命，标记缺失即可
            logger = __import__("logging").getLogger("consistency")
            logger.warning("[LOCK] 加载参考图集失败 asset=%s: %s", asset_id, e)
        try:
            from db import get_conn
            conn = get_conn()
            try:
                card = features.get_identity_card(conn, asset_id) or {}
            finally:
                conn.close()
            consistency_prompt = features.build_consistency_prompt(card)
        except Exception as e:
            logger = __import__("logging").getLogger("consistency")
            logger.warning("[LOCK] 加载身份卡失败 asset=%s: %s", asset_id, e)
        return reference_images, first_frame, consistency_prompt, primary_vector, character_name

    def probe_readiness(self) -> Dict[str, Any]:
        """权重前置诚实探测（M3 红线 + M5 设置页）：四杠杆所需权重是否就绪。

        返回 {weights:{ipadapter,clip_vision,controlnet,insightface},
              all_ready, missing:[{name,error}]}。
        """
        from comfy_env import discover_weight
        from features.face_features import _insightface_root

        weights: Dict[str, Optional[str]] = {}
        missing: List[Dict[str, str]] = []

        def _probe(name, fn):
            try:
                weights[name] = fn() or "present"
            except Exception as e:
                weights[name] = None
                missing.append({"name": name, "error": str(e)})

        _probe("ipadapter", lambda: discover_weight(
            "ipadapter", ["*.safetensors", "*.bin"], what="IP-Adapter 权重"))
        _probe("clip_vision", lambda: discover_weight(
            "clip_vision", ["*.safetensors", "*.bin"], what="CLIP Vision 权重"))
        _probe("controlnet", lambda: discover_weight(
            "controlnet", ["*.safetensors", "*.bin"], what="ControlNet 权重"))
        _probe("insightface", lambda: _insightface_root())

        return {"weights": weights, "all_ready": not missing, "missing": missing}


# ======================================================================
# P1 M3 Lock-Then-Animate · inject_consistency（四杠杆注入核心编排）
# 与 build_lock_plan（计划层）配合：本函数把四杠杆真正落到 intent，交给 translator。
# ======================================================================

def inject_consistency(intent, asset_id):
    """P1 M3 四杠杆注入（输入→处理→输出）。

    输入：分镜意图（dict 或对象）+ asset_id（角色） → 处理：四杠杆落到 P0 三个能力维度 →
    输出：带四杠杆的 intent（交给 translator）。
      杠杆1+3：subjects[role=identity] + face_vector（consistency 维度，IPAdapterFaceID 锁脸）
      杠杆2：first_frame_path = 标准肖像（frame_anchor 维度，i2v 首帧）
      杠杆4：consistency_sentence 进 prompt（semantic 维度）
    支持 dict 与对象两种 intent；dict 返回新副本（不污染原对象），对象 setattr 注入。
    """
    import features
    import db as _db
    conn = _db.get_conn()
    try:
        card = features.get_identity_card(conn, asset_id) or {}
    finally:
        conn.close()
    portrait = features.get_standard_portrait_url(asset_id)

    subjects = [{"asset_id": asset_id, "role": "identity", "weight": 0.85}]
    fv = card.get("face_vector")
    if fv:
        subjects[0]["face_vector"] = fv

    if isinstance(intent, dict):
        out = dict(intent)
        out["subjects"] = subjects
        out["first_frame_path"] = portrait
        out["consistency_sentence"] = card.get("consistency_sentence", "")
        return out

    # 对象 intent：setattr 注入（不要求特定 dataclass）
    for key, val in (
        ("subjects", subjects),
        ("first_frame_path", portrait),
        ("consistency_sentence", card.get("consistency_sentence", "")),
    ):
        if hasattr(intent, key) or hasattr(type(intent), key):
            setattr(intent, key, val)
    return intent
