# -*- coding: utf-8 -*-
"""训练产物注册 resource_catalog（手册 §7 / 设计思路 §3 ⑥ + §4）。

- 定位产出 .safetensors（校验合法性：存在 / 非空 / 头部可解析）；
- 写入 lora_models 表；
- 调 resource_catalog.register_lora 注入 catalog（供导演 Agent 匹配）。
【必须】注册前校验 .safetensors 文件合法（手册 §12）。
【必须】触发词必须写入模型元数据（semantic_tags 之外，trigger_word 独立落库），否则无法被 prompt 激活。
"""
import os
import json
import struct
import logging

from db import get_conn, uid, insert_row

logger = logging.getLogger("manju.lora.register")


def _validate_safetensors(path):
    """轻量校验 .safetensors：存在 / 非空 / 头部长度字段可解析（防截断/损坏）。"""
    if not path or not os.path.isfile(path):
        raise ValueError(".safetensors 文件不存在：%s" % path)
    size = os.path.getsize(path)
    if size < 1024:
        raise ValueError(".safetensors 文件过小（%d 字节），疑似损坏或未训练完成" % size)
    try:
        with open(path, "rb") as f:
            header_len = struct.unpack("<Q", f.read(8))[0]
            if header_len <= 0 or header_len > size - 8:
                raise ValueError("safetensors 头部长度异常（%d），文件可能损坏" % header_len)
            header = json.loads(f.read(header_len).decode("utf-8"))
            if "__metadata__" not in header and not any(
                k != "__metadata__" for k in header.keys()
            ):
                raise ValueError("safetensors 头部无张量，文件可能损坏")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("safetensors 校验失败：%s" % e)
    return size


def register_lora_from_task(task_id, model_path, *, name, lora_type, trigger_word,
                            semantic_tags, associated_asset_id=None, base_model=None):
    """训练完成后注册（手册 §7.1）。

    :return: lora_models 行（dict）。
    :raises ValueError: 文件校验失败。
    """
    size = _validate_safetensors(model_path)
    if isinstance(semantic_tags, (list, tuple)):
        semantic_tags = json.dumps(list(semantic_tags), ensure_ascii=False)
    elif semantic_tags is None:
        semantic_tags = "[]"

    conn = get_conn()
    try:
        row = {
            "id": uid(),
            "name": name,
            "lora_type": lora_type,
            "file_path": model_path,
            "trigger_word": trigger_word or "",
            "semantic_tags": semantic_tags,
            "associated_asset_id": associated_asset_id,
            "base_model": base_model or "",
            "status": "active",
        }
        insert_row(conn, "lora_models", row)
        conn.commit()
        # 注入 catalog（导演 Agent 匹配入口）；失败不阻断落库（DB 为真理源，find_best_lora 直查 DB）。
        try:
            from intent_engine.resource_catalog import ResourceCatalog
            rc = ResourceCatalog(catalog={})  # 离线空 catalog，仅用于触发内存注册逻辑
            rc.register_lora(row)
        except Exception as e:
            logger.warning("[LORA-REGISTER] catalog 内存注册跳过（不影响 DB）：%s", e)
        logger.info("[LORA-REGISTER] 已注册 LoRA id=%s name=%s size=%d", row["id"], name, size)
        return row
    finally:
        conn.close()


def manual_register(model_id):
    """手动（重）注册：从 lora_models 读取并重新注入 catalog（手册 §8 /lora/models/{id}/register）。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM lora_models WHERE id=?", (model_id,)).fetchone()
        if not row:
            raise ValueError("LoRA 模型不存在：%s" % model_id)
        row = dict(row)
        try:
            from intent_engine.resource_catalog import ResourceCatalog
            rc = ResourceCatalog(catalog={})
            rc.register_lora(row)
        except Exception as e:
            logger.warning("[LORA-REGISTER] catalog 内存注册跳过：%s", e)
        return row
    finally:
        conn.close()


def set_model_status(model_id, status):
    """启用 / 停用 / 删除（手册 §9 模型管理）。status ∈ active|disabled。"""
    if status not in ("active", "disabled"):
        raise ValueError("非法状态：%s" % status)
    conn = get_conn()
    try:
        n = conn.execute("UPDATE lora_models SET status=? WHERE id=?", (status, model_id)).rowcount
        conn.commit()
        if n == 0:
            raise ValueError("LoRA 模型不存在：%s" % model_id)
        return n
    finally:
        conn.close()


def delete_model(model_id):
    """删除 LoRA 模型记录（保留磁盘文件，避免误删训练成果；如需清理由用户手动操作）。"""
    conn = get_conn()
    try:
        n = conn.execute("DELETE FROM lora_models WHERE id=?", (model_id)).rowcount
        conn.commit()
        if n == 0:
            raise ValueError("LoRA 模型不存在：%s" % model_id)
        return n
    finally:
        conn.close()
