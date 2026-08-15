# -*- coding: utf-8 -*-
"""LoRA 训练任务执行器（手册 §5.2 / 设计思路 §3 ④）。

由 worker 消费 lora_train_tasks 队列时调用。流程：
  queued → running →（caption_status!=done 则打标）→ build_train_config → run_training
         → 成功 register_lora + succeeded；失败（≤2 重试）→ failed；取消 → 杀子进程+清理+cancelled。
CLOSE-01 状态机 / CLOSE-03 失败重试 / trace_id 贯穿 / 取消清理（手册 §11）。
进度经 WS 频道 lora:{task_id} 实时推送（手册 §9）。
"""
import os
import json
import time
import logging

from db import get_conn, uid, transaction
from realtime import publish as ws_publish

from . import engine, captioner, register
from .engine import FeatureUnavailable

logger = logging.getLogger("manju.lora.task_runner")

MAX_AUTO_RETRY = 2
TRAIN_LOCK_FILE = ".lora_training_active"  # 训练期间存在的互斥标记（与生成任务显存互斥）


def _ws(task_id, event, data):
    try:
        ws_publish("lora:%s" % task_id, event, data)
    except Exception:
        pass


def _set_status(conn, task, status, **fields):
    sets, params = ["status=?"], [status]
    if status in ("succeeded", "failed", "cancelled"):
        sets.append("updated_at=strftime('%s','now')")
    for k, v in fields.items():
        sets.append("%s=?" % k)
        params.append(v)
    conn.execute("UPDATE lora_train_tasks SET %s WHERE id=?" % ", ".join(sets), params + [task["id"]])
    _ws(task["id"], "status", {"status": status, **fields})


def _progress_cb(task_id, cur, tot, progress, loss, raw):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE lora_train_tasks SET current_step=?, total_steps=?, progress=? WHERE id=?",
            (cur, tot, progress, task_id))
        conn.commit()
    finally:
        conn.close()
    _ws(task_id, "progress", {
        "current_step": cur, "total_steps": tot, "progress": progress,
        "loss": loss, "raw": (raw or "")[:200],
    })


def serve_lora_training(task):
    """训练任务执行主体（worker 后台线程调用）。"""
    conn = get_conn()
    try:
        conn.execute("UPDATE lora_train_tasks SET status='running', updated_at=strftime('%s','now') WHERE id=?",
                     (task["id"],))
        # 训练互斥标记：训练期间暂停并发生成（手册 §5.2 / §11.2）
        _touch_lock()
        conn.commit()
        _ws(task["id"], "status", {"status": "running"})
        trace_id = task.get("trace_id") or "-"
        logger.info("[LORA-RUN] 开始 task=%s type=%s trace=%s", task["id"], task.get("lora_type"), trace_id)

        # 1. 引擎诚实探测（缺失 → 直接失败，明确提示安装路径，不静默）
        eng = engine.detect_engine()
        if not eng["available"]:
            raise FeatureUnavailable("kohya_ss", eng["reason"])

        # 2. 打标（若尚未完成）
        if (task.get("caption_status") or "none") != "done":
            conn.execute("UPDATE lora_train_tasks SET caption_status='pending' WHERE id=?", (task["id"],))
            conn.commit()
            captioner.caption_dataset(
                task["dataset_dir"], task.get("lora_type"), task.get("trigger_word"))
            conn.execute("UPDATE lora_train_tasks SET caption_status='done' WHERE id=?", (task["id"],))
            conn.commit()
            logger.info("[LORA-RUN] 打标完成 task=%s", task["id"])

        # 3. 训练（run_training 内部再次 detect_engine + build_train_config）
        preset_key = (json.loads(task.get("params") or "{}") if task.get("params") else {}).get("preset")
        result = engine.run_training(task, preset_key=preset_key, on_progress=_progress_cb)
        if not result["ok"]:
            raise RuntimeError(result.get("error") or "训练失败")

        # 4. 注册（定位 .safetensors → 写 lora_models → catalog）
        model_path = result["output_path"]
        semantic_tags = _derive_semantic_tags(task)
        row = register.register_lora_from_task(
            task["id"], model_path,
            name=task.get("name") or task["id"],
            lora_type=task.get("lora_type"),
            trigger_word=task.get("trigger_word"),
            semantic_tags=semantic_tags,
            associated_asset_id=task.get("associated_asset_id"),
            base_model=task.get("base_model"),
        )
        _set_status(conn, task, "succeeded", progress=1.0)
        _ws(task["id"], "registered", {"model_id": row["id"], "name": row["name"]})
        logger.info("[LORA-RUN] 完成并注册 task=%s model=%s", task["id"], row["id"])
        # 新训练 LoRA 已落库，立即使翻译侧 LoRA 匹配缓存失效（即时可见）
        try:
            from intent_engine.resource_catalog import invalidate_lora_cache
            invalidate_lora_cache()
        except Exception:
            pass
    except FeatureUnavailable as e:
        # 引擎/打标不可用：诚实失败，明确原因
        _mark_failed(conn, task, "训练引擎不可用：%s" % e.reason, code="engine_unavailable")
    except Exception as e:
        _mark_failed(conn, task, "训练失败：%s" % str(e)[:200])
    finally:
        _remove_lock()
        conn.close()


def _mark_failed(conn, task, note, code=None):
    rc = task.get("retry_count") or 0
    try:
        if rc < MAX_AUTO_RETRY:
            backoff = 30 * (2 ** rc)
            next_at = int(time.time()) + backoff
            conn.execute(
                "UPDATE lora_train_tasks SET status='queued', retry_count=?, next_attempt_at=?, "
                "error_message=? WHERE id=?",
                (rc + 1, next_at, (code + ": " if code else "") + note, task["id"]))
            _ws(task["id"], "status", {"status": "queued", "error_message": note, "retry": rc + 1})
            logger.warning("[LORA-RUN] 失败，%ds 后重试（第 %d/%d 次）task=%s",
                           backoff, rc + 1, MAX_AUTO_RETRY, task["id"])
        else:
            conn.execute(
                "UPDATE lora_train_tasks SET status='failed', error_message=?, updated_at=strftime('%s','now') "
                "WHERE id=?", ((code + ": " if code else "") + note, task["id"]))
            _ws(task["id"], "status", {"status": "failed", "error_message": note})
            logger.error("[LORA-RUN] 最终失败 task=%s note=%s", task["id"], note)
    except Exception:
        pass


def _derive_semantic_tags(task):
    """由任务推导语义标签（供 find_best_lora 匹配）。角色 LoRA 关联资产名，风格 LoRA 取名称关键词。"""
    tags = []
    lora_type = task.get("lora_type")
    tags.append("角色" if lora_type == "character" else "风格")
    name = (task.get("name") or "").strip()
    if name:
        tags.append(name)
    asset_id = task.get("associated_asset_id")
    if asset_id:
        try:
            c2 = get_conn()
            try:
                ar = c2.execute("SELECT name FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
                if ar and ar["name"]:
                    tags.append(ar["name"])
            finally:
                c2.close()
        except Exception:
            pass
    # 去重保序
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


# ---- 训练/生成 显存互斥标记 ----
def _touch_lock():
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "data", TRAIN_LOCK_FILE), "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


def _remove_lock():
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(base, "data", TRAIN_LOCK_FILE)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def training_active():
    """是否有 LoRA 训练正在进行（worker 据此暂停并发生成派发，手册 §5.2 / §11.2）。"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.exists(os.path.join(base, "data", TRAIN_LOCK_FILE))
    except Exception:
        return False


def cancel_training(task_id):
    """取消训练：杀子进程 + 清理互斥标记 + 置 cancelled（手册 §5.2 / CLOSE-01）。"""
    engine.cancel_training(task_id)
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE lora_train_tasks SET status='cancelled', error_message='用户手动取消', "
            "updated_at=strftime('%s','now') WHERE id=? AND status IN ('queued','running')",
            (task_id,))
        conn.commit()
        _ws(task_id, "status", {"status": "cancelled"})
    finally:
        conn.close()
    _remove_lock()
    return True


# ---- 验证对比图（手册 §9 / 设计思路 §3 ⑤）----
def _outputs_root():
    proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(proj_root, "outputs")


def generate_validation_pair(lora_row):
    """生成「有 LoRA vs 无 LoRA」验证对比图（手册 §9 验证区）。

    依赖本地 ComfyUI 在线 + LoRA 文件可被 ComfyUI 加载（lora 名需在 ComfyUI models/loras 下）。
    任一不满足 → 诚实抛 FeatureUnavailable（禁止静默失败）。
    :param lora_row: lora_models 行（dict）。
    :return: {"baseline": "/file?name=...", "with_lora": "/file?name=..."}
    """
    try:
        from layer0.comfyui_service import get_service
        from intent_engine.translator import load_template, _fill_placeholders, _inject_lora_node
    except Exception as e:
        raise FeatureUnavailable("comfyui", "无法加载 ComfyUI 服务模块：%s" % e)

    svc = get_service()
    if not svc.ping():
        raise FeatureUnavailable(
            "comfyui",
            "本地 ComfyUI 未运行，无法生成验证对比图（请先在设置中启动 ComfyUI）")

    tpl = load_template("shot_image_gen")["prompt"]
    tw = (lora_row.get("trigger_word") or "").strip()
    prompt = "masterpiece, best quality, " + (tw + ", " if tw else "") + "test subject"
    ckpt = lora_row.get("base_model") or "v1-5-pruned.safetensors"
    values = {
        "checkpoint": ckpt,
        "positive_prompt": prompt,
        "negative_prompt": "low quality, blurry, watermark, text",
        "seed": 42, "steps": 28, "cfg": 8.5, "width": 512, "height": 768,
    }
    # 基线（无 LoRA）
    base_wf = _fill_placeholders({k: v for k, v in tpl.items()}, values)
    # 注入 LoRA
    with_wf = _fill_placeholders({k: v for k, v in tpl.items()}, values)
    with_wf = _inject_lora_node(with_wf, lora_row.get("name"))

    out_dir = os.path.join(_outputs_root(), "lora_validate")
    os.makedirs(out_dir, exist_ok=True)
    base_path = svc.generate(base_wf, out_dir, timeout=240)
    with_path = svc.generate(with_wf, out_dir, timeout=240)
    return {
        "baseline": "/file?name=" + os.path.basename(base_path),
        "with_lora": "/file?name=" + os.path.basename(with_path),
    }
