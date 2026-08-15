# -*- coding: utf-8 -*-
"""P4 M1 新模型零代码接入（手册 §2 M1）。

铁律（手册 §0 三条铁律 + §5 红线）：
- 零代码真实边界：放模型 → 自动探测 → 节点自动发现（**复用 P0 M2 三层推导**）
  → 模板自动匹配（**查 P0 M3 task_type→template 映射**）→ 用户补声明 → 写 catalog 热加载。
- 禁假零代码：必须「节点自动发现 + 模板自动匹配」二者缺一不可，不得只填声明不自动发现/匹配。
- 禁全新族假装已接入：全新族（无对应模板/节点族）诚实返回 need_template 引导
  （走 P3 M2 导模板），不伪造接入成功。
- 禁破坏 P0–P3：仅增量新增 model_declarations 表 + 端点，不改动既有表/端点/翻译逻辑。

复用点（不得新写一套推导）：
- P0 M2 三层推导：resource_catalog.detect_capability_dimensions / _frame_anchor_grade /
  _dimension_for_node / _annotate_entry（节点名→维度映射、输入签名、首尾帧分级、能力声明）。
- P0 M2 节点族注册表：node_family_mapper.NODE_FAMILY_REGISTRY（video_generation 四节点族 + 帧输入）。
- P0 M3 模板映射：template_engine.TemplateEngine.discover_templates()（user_custom 同名覆盖系统）。
"""
import os
import re
import json
import logging

from db import get_conn, uid

try:
    from intent_engine.resource_catalog import (
        detect_capability_dimensions,
        _frame_anchor_grade,
        _dimension_for_node,
        _annotate_entry,
        _infer_auto_tags,
        _is_video_model,
    )
except Exception:  # 独立运行/测试时回退
    from ..intent_engine.resource_catalog import (
        detect_capability_dimensions,
        _frame_anchor_grade,
        _dimension_for_node,
        _annotate_entry,
        _infer_auto_tags,
        _is_video_model,
    )

from intent_engine.node_family_mapper import NODE_FAMILY_REGISTRY
from intent_engine.template_engine import TemplateEngine

LOG = logging.getLogger("model_plug")

# ---- 数据驱动映射（与 P0 M2 node_family_mapper.NODE_FAMILY_REGISTRY 一致，禁止 if-else 硬编码行为）----

# 家族 → 节点类（复用 P0 M2 _detect_node_families 的探测映射，反向）。
NODECLASS_TO_FAMILY = {
    "CogVideoSampler": "cogvideox",
    "WanVideoSampler": "wan",
    "LTXVImgToVideo": "ltx",
    "HunyuanVideoSampler": "hunyuan",
    "MochiPreview": "mochi",
}
FAMILY_TO_NODECLASS = {v: k for k, v in NODECLASS_TO_FAMILY.items()}

# 模型名关键字 → 视频家族（小写子串匹配，数据驱动）。
_MODEL_FAMILY_KEYWORDS = {
    "cogvideox": ["cogvideo"],
    "wan": ["wan"],
    "ltx": ["ltx"],
    "hunyuan": ["hunyuan"],
    "mochi": ["mochi"],
}

# 非视频模型的默认建议模板（P0 M3 模板清单）。
_TYPE_DEFAULT_TEMPLATE = {
    "checkpoint": "shot_txt2img",
    "lora": "shot_txt2img",
    "ipadapter": "shot_image_gen_ip",
    "controlnet": "shot_image_controlnet",
    "video": "shot_video_gen",
}


# ---------------------------------------------------------------------------
# 底层复用：模型类型 / 家族推断
# ---------------------------------------------------------------------------

def _infer_model_type(name):
    """按文件名关键词推断模型类型（与 resource_catalog._is_video_model 同源）。"""
    n = (name or "").lower()
    if re.search(r"video|wan|cogvideo|ltx|hunyuan|mochi", n):
        return "video"
    if re.search(r"lora", n):
        return "lora"
    if re.search(r"ipadapter|ip-adapter|insightface", n):
        return "ipadapter"
    if re.search(r"control", n):
        return "controlnet"
    return "checkpoint"


def _match_family_keyword(model_name):
    """按模型名关键字匹配视频家族（数据驱动）。命中返回家族名，否则 None。"""
    n = (model_name or "").lower()
    for fam, kws in _MODEL_FAMILY_KEYWORDS.items():
        for kw in kws:
            if kw in n:
                return fam
    return None


# ---------------------------------------------------------------------------
# 核心函数 1：detect_new_models —— 放模型后扫描磁盘差集 catalog
# ---------------------------------------------------------------------------

def _comfy_models_root():
    """解析 ComfyUI 模型根目录（离线/不可达返回 None，静默回落）。"""
    try:
        from comfy_env import get_comfy_root
        return get_comfy_root()
    except Exception:
        return None


def _scan_disk_weights(root):
    """扫描 models/<subdir> 下权重文件，返回 [(name, bucket)]。"""
    buckets = [
        ("checkpoints", ["*.safetensors", "*.ckpt"]),
        ("loras", ["*.safetensors", "*.ckpt"]),
        ("ipadapter", ["*.safetensors", "*.bin", "*.pt"]),
        ("controlnet", ["*.safetensors", "*.pth"]),
        ("videos", ["*.safetensors", "*.ckpt", "*.pt"]),
    ]
    out = []
    for subdir, pats in buckets:
        scan_dir = os.path.join(root, "models", subdir)
        if not os.path.isdir(scan_dir):
            continue
        import glob
        for pat in pats:
            for fp in glob.glob(os.path.join(scan_dir, pat)):
                if os.path.isfile(fp):
                    out.append((os.path.basename(fp), subdir))
    return out


def _known_names(conn):
    """已登记（model_declarations）的模型名集合。"""
    try:
        rows = conn.execute("SELECT name FROM model_declarations").fetchall()
        return {r["name"] for r in rows}
    except Exception:
        return set()


def detect_new_models(comfy_root=None):
    """扫描 ComfyUI 模型目录，返回尚未声明的「新模型」清单（零代码接入第一步）。

    - 离线/ComfyUI 未安装：返回 []（不静默吞错，记日志告警，诚实返回空，由前端提示启动 ComfyUI）。
    - 对每个磁盘权重：推断类型 + 家族 + auto_tags，标记为 status='new'（待用户确认声明）。
    - 与 model_declarations 差集：已声明的不重复列出。

    返回结构：[{"name","type","node_family","auto_tags","status":"new"}, ...]
    """
    root = comfy_root or _comfy_models_root()
    if not root or not os.path.isdir(root):
        LOG.warning("[M1] ComfyUI 模型根不可达（root=%s），返回空清单（需先启动 ComfyUI）", root)
        return []
    files = _scan_disk_weights(root)
    if not files:
        return []
    conn = get_conn()
    try:
        known = _known_names(conn)
    finally:
        conn.close()
    new = []
    for name, bucket in files:
        if name in known:
            continue
        mtype = _infer_model_type(name)
        fam = _match_family_keyword(name)
        node_class = FAMILY_TO_NODECLASS.get(fam) if fam else None
        tags = _infer_auto_tags(name)
        new.append({
            "name": name,
            "type": mtype,
            "node_family": fam,
            "node_class": node_class,
            "auto_tags": tags,
            "status": "new",
        })
    LOG.info("[M1] 探测到 %d 个未声明模型", len(new))
    return new


# ---------------------------------------------------------------------------
# 核心函数 2：discover_node_family —— 节点自动发现（复用 P0 M2 三层推导）
# ---------------------------------------------------------------------------

def discover_node_family(model_name, object_info=None):
    """自动发现某模型对应的 ComfyUI 节点族 + 能力维度（P0 M2 三层推导复用）。

    :param model_name: 模型文件名（如 "wan-3.0" / "CogVideoX-5b"）。
    :param object_info: 可选，ComfyUI /object_info（在线探测结果）。
                       传入时**真正运行 P0 M2 三层推导** detect_capability_dimensions(object_info)，
                       并从 detected 结果中提取该模型的节点族分级（证明自动推导、非手写死）；
                       缺省走离线注册表兜底（仍复用 _frame_anchor_grade 层2 签名推导）。
    :return: {
        "model_name","type","node_family","node_class",
        "frame_anchor_grade":{mode,extrapolation,interpolation,source,node} | None,
        "capabilities":{...}(P0 M2 _annotate_entry 生成),
        "detected_dimensions":[...](仅在线时由三层推导给出),
        "source":"three_layer_derivation" | "registry_fallback"
    }
    """
    mtype = _infer_model_type(model_name)
    fam = _match_family_keyword(model_name)
    node_class = FAMILY_TO_NODECLASS.get(fam) if fam else None

    # ---- 能力维度：复用 P0 M2 _annotate_entry（模型无关，数据驱动）----
    caps, frame_anchor = _annotate_entry(model_name, mtype, node_family=node_class)
    fa_grade = None
    detected_dims = None
    source = "registry_fallback"

    if object_info:
        # ---- 在线：真正跑 P0 M2 三层推导，提取该模型的节点族分级 ----
        detected = detect_capability_dimensions(object_info)
        detected_dims = {k: v for k, v in detected.items()
                         if k not in ("sources", "unclassified_nodes")}
        # frame_anchor 分级：优先用 object_info 实际输入字段做层2 签名推导
        if node_class:
            spec = (object_info or {}).get(node_class, {})
            fields = None
            if spec:
                from intent_engine.resource_catalog import _collect_input_fields
                fields = _collect_input_fields(spec)
            g = _frame_anchor_grade(node_class, fields)
            g["node"] = node_class
            g["source"] = detected.get("sources", {}).get("frame_anchor", {}).get(node_class, "signature")
            fa_grade = g
            source = "three_layer_derivation"
        elif "frame_anchor" in detected:
            # 模型名未命中家族关键字，但 object_info 中存在 frame_anchor 节点 → 取首个
            graded = detected["frame_anchor"]
            if graded:
                fa_grade = graded[0]
                node_class = fa_grade.get("node")
                fam = NODECLASS_TO_FAMILY.get(node_class)
                source = "three_layer_derivation"
    else:
        # ---- 离线兜底：用注册表 frame_anchor_inputs 做层2 签名推导（仍是 P0 M2，非硬编码）----
        if node_class:
            fam_inputs = (NODE_FAMILY_REGISTRY.get("video_generation", {})
                          .get("frame_anchor_inputs", {}).get(node_class, []))
            g = _frame_anchor_grade(node_class, set(fam_inputs))
            g["node"] = node_class
            g["source"] = "signature"
            fa_grade = g

    return {
        "model_name": model_name,
        "type": mtype,
        "node_family": fam,
        "node_class": node_class,
        "frame_anchor_grade": fa_grade,
        "capabilities": caps,
        "detected_dimensions": detected_dims,
        "source": source,
    }


# ---------------------------------------------------------------------------
# 核心函数 3：match_template —— 模板自动匹配（查 P0 M3 task_type→template 映射）
# ---------------------------------------------------------------------------

def _template_candidate_for(model_type, node_family):
    """由模型类型/节点族推断候选模板 id（P0 M3 命名约定：task_type==template_id）。"""
    if model_type == "video" and node_family:
        return "shot_video_gen_%s" % node_family
    return _TYPE_DEFAULT_TEMPLATE.get(model_type, "shot_txt2img")


def match_template(node_family, model_type="video"):
    """为该节点族自动匹配 P0 M3 模板（user_custom 同名覆盖系统模板）。

    - 命中：返回 {"status":"matched","template_id","path","task_type"}。
    - 全新族（无对应模板）：诚实返回 {"status":"need_template","guide","suggested_template_id"}，
      引导用户走 P3 M2 导入（禁假装已接入）。

    复用：TemplateEngine.discover_templates()（P0 M3 唯一映射事实源）。
    """
    candidate = _template_candidate_for(model_type, node_family)
    engine = TemplateEngine()
    templates = engine.discover_templates()
    if candidate in templates:
        return {
            "status": "matched",
            "template_id": candidate,
            "path": templates[candidate],
            "task_type": candidate,
        }
    # 软匹配：任一已注册模板名含该家族关键字
    if node_family:
        for tid in templates:
            if node_family in tid:
                return {
                    "status": "matched",
                    "template_id": tid,
                    "path": templates[tid],
                    "task_type": tid,
                }
    # 全新族：诚实引导
    return {
        "status": "need_template",
        "suggested_template_id": candidate,
        "guide": ("当前节点族 %s 暂无内置模板。请到「模板导入」页（P3 M2）上传该模型的 "
                  "ComfyUI workflow，系统将自动识别占位符并注册为模板 %s，"
                  "注册后即可被导演 Agent 自动选用（task_type→template 映射自动生效）。"
                  % (node_family, candidate)),
    }


# ---------------------------------------------------------------------------
# 核心函数 4：register_model —— 合并自动发现 + 用户补声明 → 写 catalog 热加载
# ---------------------------------------------------------------------------

def register_model(declaration):
    """登记一个模型声明（合并自动发现 + 用户补充），持久化到 model_declarations（热加载）。

    :param declaration: {
        "name":必填, "type":可选(默认推断), "node_family":可选(默认自动发现),
        "semantic_tags":可选(list), "user_notes":可选(str), "status":可选(默认 active)
    }
    :return: 持久化后的完整记录 dict（含 model_id / capabilities / 自动发现结果）。
    """
    name = str((declaration or {}).get("name") or "").strip()
    if not name:
        raise ValueError("name 必填")
    mtype = declaration.get("type") or _infer_model_type(name)
    fam = declaration.get("node_family") or _match_family_keyword(name)

    # 合并自动发现（节点族 + 能力维度 + 首尾帧分级），用户字段优先覆盖
    discovered = discover_node_family(name)
    node_class = discovered.get("node_class")
    capabilities = discovered.get("capabilities") or {}
    # 用户可补充语义标签
    user_tags = declaration.get("semantic_tags") or []
    if isinstance(user_tags, str):
        user_tags = [t.strip() for t in user_tags.split(",") if t.strip()]
    # 合并 auto_tags（自动发现）与 user_tags（用户补充），去重保序
    merged_tags = list(user_tags)
    for t in _infer_auto_tags(name):
        if t not in merged_tags:
            merged_tags.append(t)

    status = declaration.get("status") or "active"
    user_notes = declaration.get("user_notes") or ""

    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT model_id FROM model_declarations WHERE name=?", (name,)
        ).fetchone()
        if existing:
            model_id = existing["model_id"]
            conn.execute(
                """UPDATE model_declarations
                   SET type=?, node_family=?, node_class=?, capabilities=?,
                       semantic_tags=?, user_notes=?, status=?, updated_at=strftime('%s','now')
                   WHERE model_id=?""",
                (mtype, fam, node_class, json.dumps(capabilities, ensure_ascii=False),
                 json.dumps(merged_tags, ensure_ascii=False), user_notes, status, model_id))
        else:
            model_id = uid()
            conn.execute(
                """INSERT INTO model_declarations
                   (model_id, name, type, node_family, node_class, capabilities,
                    semantic_tags, user_notes, status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (model_id, name, mtype, fam, node_class,
                 json.dumps(capabilities, ensure_ascii=False),
                 json.dumps(merged_tags, ensure_ascii=False), user_notes, status))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM model_declarations WHERE model_id=?", (model_id,)
        ).fetchone()
        rec = dict(row)
        rec["capabilities"] = json.loads(rec["capabilities"]) if rec["capabilities"] else {}
        rec["semantic_tags"] = json.loads(rec["semantic_tags"]) if rec["semantic_tags"] else []
        # 附自动发现结果（供前端展示节点族/首尾帧分级）
        rec["discovery"] = discovered
    finally:
        conn.close()
    LOG.info("[M1] 注册模型 %s → family=%s status=%s（热加载生效，无需重启）", name, fam, status)
    return rec


# ---------------------------------------------------------------------------
# 辅助：列出已声明模型（GET /models，热加载视图）
# ---------------------------------------------------------------------------

def list_model_declarations(status=None):
    """列出 model_declarations（按 updated_at 倒序）。status 过滤可选。"""
    conn = get_conn()
    try:
        sql = "SELECT * FROM model_declarations"
        params = ()
        if status:
            sql += " WHERE status=?"
            params = (status,)
        sql += " ORDER BY updated_at DESC, created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["capabilities"] = json.loads(d["capabilities"]) if d["capabilities"] else {}
            d["semantic_tags"] = json.loads(d["semantic_tags"]) if d["semantic_tags"] else []
            out.append(d)
        return out
    finally:
        conn.close()
