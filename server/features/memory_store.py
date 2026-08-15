# -*- coding: utf-8 -*-
"""P3 M3 长期记忆 / 资产沉淀（跨项目复用，编排层可插拔）。

衔接（手册 §0.3）：
  - 角色卡复用 P1 M4 import_role_card（深拷贝，含 512 维向量 + 参考图集 + 身份卡）
  - 风格锚点复用 P0 M5 style_anchors 表；LoRA 复用 lora_models 表
实现逻辑铁律（§0.2）：remember_project 三步收集；recommend 两步；import_to_project 按类型分派。
红线（§5）：禁浅拷贝引用（角色卡必须 import_role_card 深拷贝）；禁 hash()（用 db.uid）。
"""
import json
import logging
from collections import Counter

from db import get_conn, fetch_all, fetch_one, insert_row, uid, SINGLE_USER_ID

LOG = logging.getLogger("memory_store")


# ----------------------------------------------------------------------
# 收集（输入→处理→输出）
# ----------------------------------------------------------------------
def get_project_role_cards(project_id):
    """输入：project_id；处理：查 role 资产；输出：角色卡列表。"""
    conn = get_conn()
    try:
        return fetch_all(conn,
            "SELECT asset_id, name, role_type, description, outfit, hairstyle, body_type, "
            "consistency_sentence, cover_url FROM assets "
            "WHERE project_id=? AND type='role' AND is_global=0",
            (project_id,))
    finally:
        conn.close()


def get_style_anchor(project_id):
    """输入：project_id；处理：查风格锚点；输出：锚点 dict 或 None。"""
    conn = get_conn()
    try:
        return fetch_one(conn,
            "SELECT anchor_id, project_id, reference_image, lighting, "
            "color_temperature, mood FROM style_anchors WHERE project_id=?",
            (project_id,))
    finally:
        conn.close()


def get_project_loras(project_id):
    """输入：project_id；处理：LoRA 经 associated_asset_id → assets.project_id 关联；
       输出：项目用过的 LoRA 列表。"""
    conn = get_conn()
    try:
        return fetch_all(conn,
            "SELECT l.id, l.name, l.lora_type, l.file_path, l.trigger_word, "
            "l.semantic_tags, l.associated_asset_id, l.base_model, l.status "
            "FROM lora_models l JOIN assets a ON l.associated_asset_id=a.asset_id "
            "WHERE a.project_id=?", (project_id,))
    finally:
        conn.close()


def insert_memory_records(project_id, role_cards, style_anchor, loras, user_id=None):
    """输入：四类收集结果；处理：逐条写 memory 表（type: character/style/lora）；
       输出：写入记录数。"""
    uid_ = user_id or SINGLE_USER_ID
    conn = get_conn()
    try:
        count = 0
        for rc in (role_cards or []):
            insert_row(conn, "memory", {
                "memory_id": uid(),
                "type": "character",
                "source_project_id": project_id,
                "payload": json.dumps({
                    "asset_id": rc.get("asset_id"), "name": rc.get("name"),
                    "role_type": rc.get("role_type"),
                    "outfit": rc.get("outfit"), "hairstyle": rc.get("hairstyle"),
                    "body_type": rc.get("body_type"),
                    "consistency_sentence": rc.get("consistency_sentence"),
                    "cover_url": rc.get("cover_url"),
                }, ensure_ascii=False),
                "user_id": uid_,
            })
            count += 1
        if style_anchor:
            insert_row(conn, "memory", {
                "memory_id": uid(),
                "type": "style",
                "source_project_id": project_id,
                "payload": json.dumps({
                    "anchor_id": style_anchor.get("anchor_id"),
                    "reference_image": style_anchor.get("reference_image"),
                    "lighting": style_anchor.get("lighting"),
                    "color_temperature": style_anchor.get("color_temperature"),
                    "mood": style_anchor.get("mood"),
                }, ensure_ascii=False),
                "user_id": uid_,
            })
            count += 1
        for lo in (loras or []):
            insert_row(conn, "memory", {
                "memory_id": uid(),
                "type": "lora",
                "source_project_id": project_id,
                "payload": json.dumps({
                    "id": lo.get("id"), "name": lo.get("name"),
                    "lora_type": lo.get("lora_type"), "file_path": lo.get("file_path"),
                    "trigger_word": lo.get("trigger_word"),
                    "semantic_tags": lo.get("semantic_tags"),
                    "associated_asset_id": lo.get("associated_asset_id"),
                    "base_model": lo.get("base_model"), "status": lo.get("status"),
                }, ensure_ascii=False),
                "user_id": uid_,
            })
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def remember_project(project_id, user_id=None):
    """输入：project_id；处理：收集角色卡 + 风格锚点 + LoRA；
       输出：写入 memory 表的记录数。"""
    role_cards = get_project_role_cards(project_id)
    style_anchor = get_style_anchor(project_id)
    loras = get_project_loras(project_id)
    return insert_memory_records(project_id, role_cards, style_anchor, loras, user_id)


# ----------------------------------------------------------------------
# 推荐（输入→处理→输出）
# ----------------------------------------------------------------------
def query_memory(user_id):
    conn = get_conn()
    try:
        return fetch_all(conn,
            "SELECT * FROM memory WHERE user_id=? ORDER BY rowid DESC",
            (user_id or SINGLE_USER_ID,))
    finally:
        conn.close()


def recommend_for_new_project(user_id=None, limit=50):
    """输入：user_id；处理：查 memory + 按使用频次降序 + 时间权重；
       输出：推荐列表（含 usage_count / score）。"""
    rows = query_memory(user_id)
    items = []
    for r in rows:
        try:
            payload = json.loads(r.get("payload") or "{}")
        except Exception:
            payload = {}
        items.append({
            "memory_id": r["memory_id"], "type": r["type"],
            "source_project_id": r["source_project_id"], "payload": payload,
        })
    freq = Counter((it["type"], it["source_project_id"]) for it in items)
    for it in items:
        it["usage_count"] = freq[(it["type"], it["source_project_id"])]
        # score = 使用次数×1 + 最近使用时间权重×0.5（此处以频次为主，时间权重占位）
        it["score"] = it["usage_count"] * 1 + 0.5
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


# ----------------------------------------------------------------------
# 导入（输入→处理→输出，复用 P1 深拷贝）
# ----------------------------------------------------------------------
def copy_style_anchor(payload, target_project_id):
    """输入：风格锚点 payload + 目标项目；处理：实体复制一行到 style_anchors；
       输出：新 anchor_id（深拷贝，新主键，非引用）。"""
    conn = get_conn()
    try:
        new_id = uid()
        insert_row(conn, "style_anchors", {
            "anchor_id": new_id,
            "project_id": target_project_id,
            "reference_image": payload.get("reference_image"),
            "lighting": payload.get("lighting"),
            "color_temperature": payload.get("color_temperature"),
            "mood": payload.get("mood"),
        })
        conn.commit()
        return new_id
    finally:
        conn.close()


def register_lora_to_project(payload, target_project_id):
    """输入：LoRA payload + 目标项目；处理：实体复制一行到 lora_models（新主键）；
       输出：新 LoRA id（跨项目复用注册，非浅引用）。"""
    conn = get_conn()
    try:
        new_id = uid()
        insert_row(conn, "lora_models", {
            "id": new_id,
            "name": payload.get("name") or "导入LoRA",
            "lora_type": payload.get("lora_type") or "style",
            "file_path": payload.get("file_path") or "",
            "trigger_word": payload.get("trigger_word"),
            "semantic_tags": payload.get("semantic_tags") or "[]",
            "associated_asset_id": payload.get("associated_asset_id"),
            "base_model": payload.get("base_model"),
            "status": payload.get("status") or "active",
        })
        conn.commit()
        return new_id
    finally:
        conn.close()


def import_to_project(target_project_id, memory_ids):
    """输入：目标项目 + 选中的记忆项 id 列表；处理：按类型分派；
       输出：{imported, details}。"""
    details = []
    imported = 0
    read_conn = get_conn()
    try:
        for mid in (memory_ids or []):
            r = fetch_one(read_conn,
                "SELECT * FROM memory WHERE memory_id=?", (mid,))
            if not r:
                details.append({"memory_id": mid, "status": "not_found"})
                continue
            mtype = r["type"]
            try:
                payload = json.loads(r.get("payload") or "{}")
            except Exception:
                payload = {}
            if mtype == "character":
                # 复用 P1 M4 import_role_card（深拷贝，含向量+参考图+身份卡）—— 禁浅拷贝
                from routers import import_role_card
                src_asset = payload.get("asset_id")
                if not src_asset:
                    details.append({"memory_id": mid, "status": "no_source"})
                    continue
                res = import_role_card(src_asset, target_project_id)
                imported += 1
                details.append({
                    "memory_id": mid,
                    "status": "imported" if res.get("imported") else "skipped",
                    "asset_id": res.get("asset_id"),
                })
            elif mtype == "style":
                new_id = copy_style_anchor(payload, target_project_id)
                imported += 1
                details.append({"memory_id": mid, "status": "imported", "anchor_id": new_id})
            elif mtype == "lora":
                new_id = register_lora_to_project(payload, target_project_id)
                imported += 1
                details.append({"memory_id": mid, "status": "imported", "lora_id": new_id})
            else:
                details.append({"memory_id": mid, "status": "unknown_type"})
    finally:
        read_conn.close()
    return {"imported": imported, "details": details}
