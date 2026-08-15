# -*- coding: utf-8 -*-
"""Lock 锁定阶段（P1 M2 · 标准肖像 + 参考图集）。

一键为角色生成：标准肖像（正面中性表情）+ 4 张参考图
（front / three_quarter / expression / full_body），光线一致；
每张生成后提取 512 维身份向量落库，供后续 Lock-Then-Animate 一致性注入。

实现要点：
- 复用现有 ComfyUI 文生图（ai_local._comfy_text2img）与 insightface 向量提取
  （features.extract_face_vector）；不手搓节点（铁律 1.2）。
- 所有外部依赖（ComfyUI / insightface）缺失时**诚实抛错**，绝不静默降级成
  「无锁脸」空图（P1 红线的核心要求之一）。
- 图像落 server/outputs/references/，经 /file?name= 服务（与角色卡一致）。
"""
import os
import base64
import uuid

from db import get_conn

# 参考图集五种类型（与数据库 CHECK 约束 + 前端严格对齐）
REF_KINDS = ["standard_portrait", "front", "three_quarter", "expression", "full_body"]
# M2 参考图集的 4 张（不含标准肖像）
SET_KINDS = ["front", "three_quarter", "expression", "full_body"]

# 生成输出目录（server/outputs/references，D 盘，无创性写盘）
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = os.path.join(_BASE, "outputs")
REF_OUT = os.path.join(OUTPUTS, "references")

# 无风格锚点时的中性兜底（仅兜底用，主键来自 P0 M5 风格锚点，绝不写死）。
_LIGHTING_DEFAULT = "neutral balanced lighting, soft even illumination, consistent lighting, high detail"
# 风格锚点 lighting token → 自然语言光线描述（与 P0 M5 _decide_style_spec 取值对齐）。
_LIGHTING_MAP = {
    "warm_cinematic": "warm cinematic lighting, golden hour tone, soft key light",
    "cool_cinematic": "cool cinematic lighting, blue hour tone, cool fill light",
    "neutral_balanced": "neutral balanced lighting, soft even illumination",
}


def get_style_anchor_for_asset(asset_id):
    """按 asset → project 解析 P0 M5 全局风格锚点（光线/色调/情绪）。

    输入：asset_id → 处理：查 assets.project_id → db.get_project_style_anchor → 输出：
    style_anchor dict（含 lighting/color_temperature/mood/reference_image）或空 dict
    （项目尚未生成风格锚点时）。缺失即空（由调用方中性兜底），绝不抛错。
    """
    try:
        import db
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT project_id FROM assets WHERE asset_id=?", (asset_id,)
            ).fetchone()
            pid = row["project_id"] if row else None
        finally:
            conn.close()
        if pid:
            return db.get_project_style_anchor(pid) or {}
    except Exception:
        pass
    return {}


def _lighting_phrase(anchor):
    """从风格锚点推导光线描述（与 P0 M5 同一来源，杜绝写死）。

    输出：(phrase, src)。src 用于日志证明光线来自风格锚点而非硬编码。
    """
    if not anchor:
        return _LIGHTING_DEFAULT, "fallback_neutral"
    lighting = anchor.get("lighting") or "neutral_balanced"
    phrase = _LIGHTING_MAP.get(lighting, lighting.replace("_", " ") + " lighting")
    return phrase, lighting


# 各类型的构图/表情指令（与一致句叠加，逐字锁定角色外观）
_KIND_PROMPTS = {
    "standard_portrait": "front facing, neutral expression, half-body portrait, looking at camera",
    "front": "front facing, calm expression, half-body portrait, looking at camera",
    "three_quarter": "three quarter view, slight head turn, half-body portrait",
    "expression": "front facing, gentle smile, vivid expression, half-body portrait",
    "full_body": "full body shot, standing, entire figure visible, front facing",
}


def _persist_reference_image(asset_id, ref_kind, local_path):
    """把生成图落到 outputs/references/ref-<asset_id>-<kind>.png，返回可服务 URL。"""
    os.makedirs(REF_OUT, exist_ok=True)
    fname = "ref-%s-%s.png" % (asset_id, ref_kind)
    dest = os.path.join(REF_OUT, fname)
    if os.path.abspath(local_path) != os.path.abspath(dest):
        import shutil
        shutil.copyfile(local_path, dest)
    return "/file?name=" + fname


def _encode_vector(vec):
    """把 numpy 512 维向量编码为 base64 文本（与 asset_stages 一致）。"""
    import numpy as np
    return base64.b64encode(np.asarray(vec, dtype="float32").reshape(-1).tobytes()).decode("ascii")


def _store_reference(conn, asset_id, ref_kind, image_url, face_vector_b64):
    """upsert 一条参考图集记录（按 asset_id + ref_kind 幂等）。"""
    existing = conn.execute(
        "SELECT ref_id FROM asset_reference_set WHERE asset_id=? AND ref_kind=?",
        (asset_id, ref_kind),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE asset_reference_set SET image_url=?, face_vector=?, created_at=strftime('now') WHERE ref_id=?",
            (image_url, face_vector_b64, existing["ref_id"]),
        )
        ref_id = existing["ref_id"]
    else:
        ref_id = "REF-" + uuid.uuid4().hex[:20]
        conn.execute(
            "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at) "
            "VALUES (?,?,?,?,?,strftime('now'))",
            (ref_id, asset_id, ref_kind, image_url, face_vector_b64),
        )
    # 标准肖像：同步进独立 standard_portrait 表（手册 §4，主存储）；
    # asset_reference_set 仍保留该行作参考图集展示（非侵入）。
    if ref_kind == "standard_portrait":
        conn.execute(
            "INSERT INTO standard_portrait (asset_id, image_path, created_at) VALUES (?,?,strftime('now')) "
            "ON CONFLICT(asset_id) DO UPDATE SET image_path=excluded.image_path, created_at=excluded.created_at",
            (asset_id, image_url),
        )
    return ref_id


def _build_prompt(asset_id, ref_kind, anchor=None):
    """基于身份卡拼装该类型的生成提示词（服装/发型/体型逐字锚定，光线来自风格锚点）。

    输入：asset_id + ref_kind + style_anchor(可选，缺则现取) → 处理：拼装 →
    输出：自然语言提示词（光线一致词统一来自 P0 M5 风格锚点）。
    """
    import features
    conn = get_conn()
    try:
        card = features.get_identity_card(conn, asset_id) or {}
    finally:
        conn.close()
    name = card.get("name") or "该角色"
    parts = ["single character portrait of %s" % name]
    if card.get("outfit"):
        parts.append(card["outfit"])
    if card.get("hairstyle"):
        parts.append(card["hairstyle"])
    if card.get("body_type"):
        parts.append(card["body_type"])
    parts.append(_KIND_PROMPTS.get(ref_kind, "front facing portrait"))
    # 光线一致：统一来自 P0 M5 风格锚点（同一锚点 → 4 张 + 标准肖像光线一致）
    lighting_phrase, _ = _lighting_phrase(anchor)
    parts.append(lighting_phrase)
    ct = (anchor or {}).get("color_temperature")
    if ct:
        parts.append("color temperature %dK" % int(ct))
    parts.append("character consistency, same appearance")
    return ", ".join(parts)


def _generate_one(asset_id, ref_kind, anchor=None, timeout=240):
    """生成单张参考图 + 提取 512 维向量 + 落库。返回 (ref_id, image_url, has_vector)。

    依赖缺失时抛错（ComfyUI 不可用 → RuntimeError；insightface 缺失 → FeatureUnavailable）。
    光线来自 P0 M5 风格锚点（anchor 由调用方统一传入，保证 4 张 + 标准肖像一致）。
    """
    import ai_local
    import features
    if anchor is None:
        anchor = get_style_anchor_for_asset(asset_id)
    lighting_src = (anchor or {}).get("lighting") or "fallback_neutral"
    logger = __import__("logging").getLogger("reference_set")
    logger.info("[REF-LIGHTING] asset=%s kind=%s style_anchor_lighting=%s",
                asset_id, ref_kind, lighting_src)
    prompt = _build_prompt(asset_id, ref_kind, anchor)
    local = ai_local._comfy_text2img(prompt, width=768, height=1024, steps=28, cfg=8.5, timeout=timeout)
    image_url = _persist_reference_image(asset_id, ref_kind, local)
    # 提取 512 维身份向量：缺 insightface / 无脸 → 诚实上浮（绝不落库假向量）
    vec = features.extract_face_vector(local)
    fv_b64 = _encode_vector(vec)
    conn = get_conn()
    try:
        ref_id = _store_reference(conn, asset_id, ref_kind, image_url, fv_b64)
        conn.commit()
    finally:
        conn.close()
    return ref_id, image_url, True


def generate_standard_portrait(asset_id, timeout=240):
    """生成标准肖像（正面中性表情）。返回 (ref_id, image_url, has_vector)。光线来自风格锚点。"""
    anchor = get_style_anchor_for_asset(asset_id)
    return _generate_one(asset_id, "standard_portrait", anchor=anchor, timeout=timeout)


def generate_reference_set(asset_id, timeout=240):
    """生成 4 张参考图（front/three_quarter/expression/full_body，光线一致）。

    返回 {asset_id, items:[{ref_kind, image_url, has_vector}], failed:[...]}。
    任一图失败（ComfyUI 离线/权重缺失）时记录到 failed 并继续其余，最终若有失败则抛
    RuntimeError 汇总（诚实告知，不假装全成功）。

    光线一致：一次解析风格锚点并下传 4 张 + 标准肖像，杜绝各自写死。
    """
    anchor = get_style_anchor_for_asset(asset_id)
    items = []
    failed = []
    for kind in SET_KINDS:
        try:
            ref_id, image_url, has_vector = _generate_one(asset_id, kind, anchor=anchor, timeout=timeout)
            items.append({"ref_kind": kind, "image_url": image_url, "has_vector": has_vector})
        except Exception as e:
            failed.append({"ref_kind": kind, "error": str(e)})
    if failed:
        raise RuntimeError(
            "参考图集部分生成失败（%d/%d）：%s"
            % (len(failed), len(SET_KINDS), "; ".join("%s:%s" % (f["ref_kind"], f["error"]) for f in failed))
        )
    return {"asset_id": asset_id, "items": items, "failed": failed}


def regenerate_one(asset_id, ref_kind, timeout=240):
    """重生成单张参考图 / 标准肖像（按 ref_kind，幂等覆盖）。

    返回 {ref_id, image_url, has_vector}。依赖缺失时诚实上浮（ComfyUI 离线 /
    insightface 缺失 / 未知 ref_kind），绝不落库假向量。
    """
    if ref_kind not in REF_KINDS:
        raise ValueError(
            "未知参考图类型: %s（可选：%s）" % (ref_kind, ", ".join(REF_KINDS))
        )
    anchor = get_style_anchor_for_asset(asset_id)
    return _generate_one(asset_id, ref_kind, anchor=anchor, timeout=timeout)


def resolve_ref_local_path(image_url):
    """把 /file?name=xxx 解析为本地真实存在的图片绝对路径（镜像 main.py /file 落盘解析）。

    候选根目录（与 /file 端点一致）：
      - REF_OUT（server/outputs/references，参考图主落盘）
      - server/server/outputs（历史兼容）
      - server/.uploads（上传兼容）
    文件真实存在 → 返回绝对路径；URL 非法 / 文件缺失 → None。
    用途：get_reference_set 据此做「最基本的校验」——有向量但图不存在时不得谎报 512维✓。
    """
    if not image_url:
        return None
    name = image_url.split("name=", 1)[-1] if "name=" in image_url else image_url
    name = os.path.basename(name.split("&", 1)[0])
    if not name:
        return None
    # 候选根目录必须与 /file 端点（main.py + web_app.py）完全一致，
    # 否则「后端判定有图」与「浏览器实际加载」会不一致。
    repo_root = os.path.dirname(_BASE)  # manju_agent 仓库根
    candidates = [
        REF_OUT,                                        # server/outputs/references（参考图主落盘）
        os.path.join(repo_root, "outputs"),             # 仓库根 outputs
        os.path.join(repo_root, ".uploads"),            # 仓库根 .uploads
        os.path.join(repo_root, "server", "outputs"),   # server/outputs
        os.path.join(repo_root, "server", ".uploads"),  # server/.uploads
    ]
    for d in candidates:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def copy_ref_image_named(src_image_url, new_name):
    """通用实体复制：把源参考图文件落到 REF_OUT/<new_name>（跨项目 / 跨机导入用）。

    源文件缺失（跨机未带文件 / 半落库）→ 返回 ''（诚实：无图，待新机重新生成），
    绝不指向源文件造成「共享引用」破坏实体复制红线。复制失败（权限等）→ 同样返回 ''。
    """
    src = resolve_ref_local_path(src_image_url)
    if not src:
        return ""
    os.makedirs(REF_OUT, exist_ok=True)
    fname = os.path.basename(new_name)
    dest = os.path.join(REF_OUT, fname)
    try:
        import shutil
        shutil.copyfile(src, dest)
    except OSError:
        return ""
    return "/file?name=" + fname


def copy_ref_image_to_asset(src_image_url, new_asset_id, ref_kind):
    """跨项目 / 跨机导入时实体复制参考图：源 → REF_OUT/ref-<new_asset_id>-<ref_kind>.png。"""
    return copy_ref_image_named(src_image_url, "ref-%s-%s.png" % (new_asset_id, ref_kind))


def get_reference_set(conn, asset_id):
    """读取角色参考图集（含标准肖像共 5 张 + 每张向量状态）。返回按 ref_kind 排序的列表。

    基本校验（用户红线：最基本的校验）：has_vector 仅在「存在 512 维向量 base64 且
    对应图片文件真实存在于磁盘」时为真。任何「向量在库但图片缺失」的情形
    （跨机 / 跨项目导入未带文件、生成半落库、文件被误删）一律：
      - has_vector=False（不谎报 512维✓）
      - image_missing=True（前端明确提示「图片缺失，需重新生成」）
    仅判定图片存在性，不触碰 ComfyUI / insightface，零外部依赖。
    """
    rows = conn.execute(
        "SELECT ref_id, ref_kind, image_url, face_vector, created_at "
        "FROM asset_reference_set WHERE asset_id=?", (asset_id,)
    ).fetchall()
    out = []
    for r in rows:
        fv = r["face_vector"]
        image_url = r["image_url"] or ""
        image_exists = resolve_ref_local_path(image_url) is not None
        has_vector = bool(fv) and image_exists
        out.append({
            "ref_id": r["ref_id"],
            "ref_kind": r["ref_kind"],
            "image_url": image_url,
            "has_vector": has_vector,
            "image_exists": image_exists,
            "image_missing": bool(fv) and not image_exists,
            "created_at": r["created_at"],
        })
    order = {k: i for i, k in enumerate(REF_KINDS)}
    out.sort(key=lambda x: order.get(x["ref_kind"], 99))
    return out


def load_reference_image(asset_id):
    """P0 依赖（手册 §0.3）：返回角色标准肖像的本地文件路径，供 IP-Adapter 作 image 输入。

    主源：独立 standard_portrait 表（手册 §4）；缺记录回退 asset_reference_set
    (ref_kind='standard_portrait')（向后兼容 / 未回填数据）。
    输入：asset_id → 处理：解析 image_path(/file?name=) → server/outputs/references/ 本地文件 →
    输出：本地绝对路径；无标准肖像 / 文件不存在返回 None（诚实，不静默降级）。
    """
    try:
        import db
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT image_path FROM standard_portrait WHERE asset_id=? LIMIT 1",
                (asset_id,)).fetchone()
            if row and row["image_path"]:
                url = row["image_path"]
            else:
                row2 = conn.execute(
                    "SELECT image_url FROM asset_reference_set "
                    "WHERE asset_id=? AND ref_kind='standard_portrait' LIMIT 1", (asset_id,)
                ).fetchone()
                url = row2["image_url"] if row2 else None
        finally:
            conn.close()
        if not url:
            return None
        fname = url.split("name=", 1)[-1] if "name=" in url else url
        local = os.path.join(REF_OUT, os.path.basename(fname))
        if os.path.isfile(local):
            return local
        return None
    except Exception:
        return None


def get_standard_portrait_url(asset_id):
    """读取已生成的标准肖像 image_url（供 inject_consistency 作 i2v 首帧）。

    主源：独立 standard_portrait 表（手册 §4）；缺记录回退 asset_reference_set
    （向后兼容 / 未回填数据）。尚未生成标准肖像 → 诚实返回 None。
    """
    try:
        import db
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT image_path FROM standard_portrait WHERE asset_id=? LIMIT 1",
                (asset_id,)).fetchone()
            if row and row["image_path"]:
                return row["image_path"]
            row2 = conn.execute(
                "SELECT image_url FROM asset_reference_set "
                "WHERE asset_id=? AND ref_kind='standard_portrait' LIMIT 1", (asset_id,)
            ).fetchone()
            return row2["image_url"] if row2 else None
        finally:
            conn.close()
    except Exception:
        return None
