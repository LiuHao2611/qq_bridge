# -*- coding: utf-8 -*-
"""角色身份系统（P1 M1 · Lock-Then-Animate 地基）。

身份卡 = 人脸缩略图(封面) + 512 维向量(主阶段) + 服装/发型/体型/一致句。
本模块只负责身份卡的「存取」与「一致句默认生成 / 提示词锚点拼装」，
不触碰图像生成（那是 M2 reference_set 的职责）。

存储形态（手册 §4 落地）：主存储为独立 identity_card 表（asset_id 主键，
含 face_vector/outfit/hairstyle/body_type/consistency_sentence）；同时镜像回写
assets 四列（outfit/hairstyle/body_type/consistency_sentence）——因 memory_store /
import_role_card 仍直接读 assets 列，镜像是为不破坏既有业务（非侵入）。
读取优先取 identity_card 表，缺行则回退 assets 列（向后兼容旧库）。
512 维向量以 asset_stages 主阶段为准（P0 load_face_vector 主源），并同步进
identity_card.face_vector 供手册 §4 表形态完整。
"""
import re
import base64

from .face_features import extract_face_vector, store_face_vector, FeatureUnavailable

# 参考图集五种类型（与数据库 CHECK 约束严格对齐）
REF_KINDS = ["standard_portrait", "front", "three_quarter", "expression", "full_body"]


def default_consistency_sentence(name, outfit="", hairstyle="", body_type=""):
    """默认一致句：锚定角色视觉一致性，供生成时逐字写进 prompt（杠杆④）。

    用户可改；此处仅在未填写时生成兜底版本。
    """
    name = (name or "该角色").strip()
    parts = ["同一角色「%s」" % name]
    if outfit:
        parts.append("服装保持一致（%s）" % outfit)
    else:
        parts.append("服装保持一致")
    if hairstyle:
        parts.append("发型不变（%s）" % hairstyle)
    else:
        parts.append("发型不变")
    if body_type:
        parts.append("体型一致（%s）" % body_type)
    else:
        parts.append("体型一致")
    parts.append("保持样貌与五官一致，跨镜头不跳脸")
    return "，".join(parts) + "。"


def _primary_stage_face_vector(conn, asset_id):
    """取主阶段（selected=1 优先，否则首个）的 face_vector 与状态。

    返回 (face_vector_b64_or_None, has_vector)。face_vector 在库中以 base64 文本存储
    （见 features.face_features.store_face_vector）。
    """
    row = conn.execute(
        "SELECT face_vector FROM asset_stages WHERE asset_id=? "
        "ORDER BY selected DESC, stage_id LIMIT 1", (asset_id,)
    ).fetchone()
    fv = row["face_vector"] if row else None
    if fv is None:
        return None, False
    # 兼容历史 raw float32 bytes（旧数据）：能解码即视为有向量
    try:
        import base64
        if isinstance(fv, str):
            base64.b64decode(fv)
        else:
            base64.b64decode(fv)
    except Exception:
        return None, False
    return fv, True


def get_identity_card(conn, asset_id):
    """读取角色身份卡（含默认一致句兜底 + 主阶段向量状态）。

    数据优先取自独立 identity_card 表（手册 §4）；该行缺失时回退 assets 四列
    （向后兼容旧库 / 未回填数据）。512 维向量始终以 asset_stages 主阶段为准
    （P0 load_face_vector 主源，保持不破坏既有一致性链路）。

    返回 dict：asset_id / name / face_vector(主阶段 base64 或 None) / has_face_vector
              / outfit / hairstyle / body_type / consistency_sentence(已兜底默认)。
    """
    row = conn.execute(
        "SELECT asset_id, name, outfit, hairstyle, body_type, consistency_sentence "
        "FROM assets WHERE asset_id=?", (asset_id,)
    ).fetchone()
    if not row:
        return None
    # 优先独立 identity_card 表；缺行回退 assets 列
    ic = conn.execute(
        "SELECT outfit, hairstyle, body_type, consistency_sentence "
        "FROM identity_card WHERE asset_id=?", (asset_id,)
    ).fetchone()
    if ic is not None:
        outfit = ic["outfit"] or ""
        hairstyle = ic["hairstyle"] or ""
        body_type = ic["body_type"] or ""
        consistency_raw = ic["consistency_sentence"] or ""
    else:
        outfit = row["outfit"] or ""
        hairstyle = row["hairstyle"] or ""
        body_type = row["body_type"] or ""
        consistency_raw = row["consistency_sentence"] or ""
    fv, has_fv = _primary_stage_face_vector(conn, asset_id)
    name = row["name"] or asset_id
    consistency = consistency_raw.strip()
    if not consistency:
        consistency = default_consistency_sentence(name, outfit, hairstyle, body_type)
    return {
        "asset_id": asset_id,
        "name": name,
        "face_vector": fv,
        "has_face_vector": bool(has_fv),
        "outfit": outfit,
        "hairstyle": hairstyle,
        "body_type": body_type,
        "consistency_sentence": consistency,
    }


def save_identity_card(conn, asset_id, data):
    """写入身份卡 4 字段（服装/发型/体型/一致句）。

    主存储：独立 identity_card 表（手册 §4，幂等 upsert）。
    镜像：同步回写 assets 四列（非侵入——memory_store / import_role_card 仍读 assets 列）。
    data: {outfit?, hairstyle?, body_type?, consistency_sentence?}（缺省字段不更新）。
    一致句若为空字符串，则落库为空（读取时再兜底默认）；显式传入 None 则保留原值。
    返回更新行数（identity_card 与 assets 同进，恒为 1 或 0）。
    """
    allowed = ("outfit", "hairstyle", "body_type", "consistency_sentence")
    sets = {}
    for k in allowed:
        if k in data and data[k] is not None:
            sets[k] = str(data[k])
    if not sets:
        return 0
    cols = list(sets.keys())
    placeholders = ",".join(["?"] * len(cols))
    upd = ",".join("%s=excluded.%s" % (c, c) for c in cols)
    # 1) 独立 identity_card 表（主存储）
    conn.execute(
        "INSERT INTO identity_card (asset_id, %s) VALUES (?, %s) "
        "ON CONFLICT(asset_id) DO UPDATE SET %s"
        % (",".join(cols), placeholders, upd),
        (asset_id,) + tuple(sets.values()))
    # 2) 镜像回 assets 四列（非侵入）
    acols = ["%s=?" % c for c in cols]
    conn.execute(
        "UPDATE assets SET %s, updated_at=strftime('%%s','now') WHERE asset_id=?"
        % ", ".join(acols),
        tuple(list(sets.values()) + [asset_id]))
    conn.commit()
    return 1


def build_consistency_prompt(card):
    """杠杆④：把一致句 + 服装/发型/体型逐字拼为提示词锚点片段（顺序稳定、无随机）。

    返回字符串；供 translator / ai_local 直接拼接进正向 prompt 末尾，
    强制模型锁定角色视觉一致性。空卡返回空串（不污染 prompt）。
    """
    if not card:
        return ""
    chunks = []
    cs = (card.get("consistency_sentence") or "").strip()
    if cs:
        chunks.append(cs)
    # 显式维度兜底（即便一致句未涵盖，也逐字强调）
    if card.get("outfit"):
        chunks.append("服装：%s" % card["outfit"])
    if card.get("hairstyle"):
        chunks.append("发型：%s" % card["hairstyle"])
    if card.get("body_type"):
        chunks.append("体型：%s" % card["body_type"])
    return "；".join(chunks) + ("。" if chunks else "")


# ----------------------------------------------------------------------
# M1 身份卡组装（手册 M1 §4 · 输入→处理→输出，三步入库，真实落地不静默）
# ----------------------------------------------------------------------

def ollama_extract_attributes(role_text):
    """用 Ollama 从角色设定文本结构化提取形象属性（服装/发型/体型）。

    输入：role_text（如"17岁高中生，瘦削，眼神坚定"） → 处理：ollama_chat 结构化 JSON →
    输出：{outfit, hairstyle, body_type}（缺字段以空串兜底）。
    Ollama 不可用 / 解析失败 → 抛 FeatureUnavailable（诚实，不假装提取成功）。
    """
    from ai_local import ollama_chat
    prompt = (
        "你是角色形象分析助手。请从下面角色设定中提取三项属性，"
        "严格只输出 JSON：{\"outfit\":\"服装描述\",\"hairstyle\":\"发型描述\","
        "\"body_type\":\"体型描述\"}。不要任何解释。\n角色设定：%s"
        % (role_text or "")
    )
    try:
        raw = ollama_chat(prompt, think=False, purpose="extract")
    except Exception as e:
        raise FeatureUnavailable(
            "ollama", "提取形象属性失败：%s" % e, code="OLLAMA_ATTR_FAILED")
    # 容错解析：截取首个 {...} 块
    import json
    import re as _re
    text = raw or ""
    m = _re.search(r"\{.*\}", text, _re.DOTALL)
    data = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:
            data = {}
    return {
        "outfit": str(data.get("outfit", "") or "").strip(),
        "hairstyle": str(data.get("hairstyle", "") or "").strip(),
        "body_type": str(data.get("body_type", "") or "").strip(),
    }


def build_consistency_sentence(attrs):
    """把提取的形象属性拼为一致句（杠杆④锚点，手册 M1 §4 模板）。"""
    outfit = (attrs or {}).get("outfit", "") or ""
    hairstyle = (attrs or {}).get("hairstyle", "") or ""
    return "同一角色，服装%s，发型%s，保持人物样貌与服装一致" % (outfit, hairstyle)


def _ensure_primary_stage(conn, asset_id):
    """返回资产主阶段 stage_id（selected 优先）；无则新建默认阶段并返回其 id。"""
    import db as _db
    row = conn.execute(
        "SELECT stage_id FROM asset_stages WHERE asset_id=? "
        "ORDER BY selected DESC, stage_id LIMIT 1", (asset_id,)
    ).fetchone()
    if row:
        return row["stage_id"]
    stage_id = _db.uid()
    conn.execute(
        "INSERT INTO asset_stages (stage_id, asset_id, stage_name, age, selected, created_at) "
        "VALUES (?,?,?,?,?,strftime('now'))",
        (stage_id, asset_id, "默认", 0, 1))
    return stage_id


def assemble_identity_card(asset_id, reference_image_path, role_text):
    """M1 身份卡组装（输入→处理→输出），真实落地，绝不静默。

    处理1：extract_face_vector(reference_image_path) → 512 维向量（无脸 → FeatureUnavailable）
    处理2：ollama_extract_attributes(role_text) → outfit/hairstyle/body_type
    处理3：build_consistency_sentence(attrs) → 一致句
    输出：写入 assets 四列 + 主阶段 face_vector；返回 identity_card dict（含向量状态）
    """
    face_vector = extract_face_vector(reference_image_path)  # 512 维 numpy
    attrs = ollama_extract_attributes(role_text)
    consistency_sentence = build_consistency_sentence(attrs)

    import db as _db
    conn = _db.get_conn()
    try:
        save_identity_card(conn, asset_id, {
            "outfit": attrs.get("outfit", ""),
            "hairstyle": attrs.get("hairstyle", ""),
            "body_type": attrs.get("body_type", ""),
            "consistency_sentence": consistency_sentence,
        })
        stage_id = _ensure_primary_stage(conn, asset_id)
        # 关键：_ensure_primary_stage 在 conn 的当前事务内插入新阶段（未提交）；
        # store_face_vector 会另开独立连接做 UPDATE，必须先把新阶段提交，
        # 否则独立连接看不到该行 → UPDATE 命中 0 行 → 512 维向量静默丢失。
        conn.commit()
        store_face_vector(stage_id, face_vector)
        # 同步 512 维向量到独立 identity_card 表（手册 §4 face_vector 列）；
        # asset_stages 仍作 P0 load_face_vector 主源，不被破坏。
        import numpy as np
        fv_b64 = base64.b64encode(
            np.asarray(face_vector, dtype="float32").reshape(-1).tobytes()
        ).decode("ascii")
        conn.execute(
            "UPDATE identity_card SET face_vector=? WHERE asset_id=?",
            (fv_b64, asset_id))
        conn.commit()
    finally:
        conn.close()

    # 返回组装后的身份卡（含向量状态）
    c2 = _db.get_conn()
    try:
        return get_identity_card(c2, asset_id)
    finally:
        c2.close()
