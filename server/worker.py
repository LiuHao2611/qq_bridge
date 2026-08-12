# -*- coding: utf-8 -*-
"""生成任务 Worker：后台线程消费 generation_tasks 队列。

状态机：queued → running → succeeded / failed（failed 可 retrying）。
成功：settle 积分、写 result_url、推送消息；失败：refund 积分、写 error_code。

CLOSE-01（闭环）：
  - 启动时复位崩溃遗留的 running 任务为 queued（崩溃恢复）；
  - 看门狗将长时间无进展的 running 任务判定 failed（避免永久卡死阻塞队列）；
  - _serve_one 外包 try，意外异常也标记 failed，绝不遗留永久 running。
"""
import os
import threading
import time
import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from db import get_conn, fetch_one, fetch_all, row_to_dict, uid, log_operation, push_message
from common import ApiError
from points_engine import settle, refund
from ai_local import generate_segment_video, txt2img_generate, synthesize_project_video
from realtime import publish as ws_publish
import metrics  # 手册 §7.2 Prometheus 监控指标

logger = logging.getLogger("manju.worker")

POLL_INTERVAL = 3.0
STALE_RUNNING_SECS = 30 * 60  # 运行超过 30 分钟无进展 → 判定卡死（OPS-04 看门狗兜底）
MAX_AUTO_RETRY = 2            # CLOSE-03 失败自动重试上限（指数退避，超过则最终失败退冻结金）
AUTO_RETRY_BASE_DELAY = 30    # 第 n 次重试等待 30 * 2^n 秒
# CLOSE-05：单片段/合成生成的硬超时（KB 视频 600s）。与 30min 看门狗互为兜底：
# 看门狗防永久卡死，此处给单次调用明确上限，超时即按失败退避/退款。
VIDEO_TIMEOUT_SECS = int(os.environ.get("MANJU_VIDEO_TIMEOUT", "600"))
_watchdog_counter = 0
_watchdog_every = 10  # 每 10 次轮询执行一次看门狗扫描
_worker_thread = None
_stop_event = threading.Event()
_exec = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gen-exec")  # CLOSE-05 超时执行池


def _trace(task):
    return (task or {}).get("trace_id") or "-"


def _emit(task, level, event, **kw):
    """LOG-01/03：关键位置输出含 trace_id 的结构化 JSON 日志，便于链路排查。"""
    rec = {"ts": int(time.time()), "level": level, "trace_id": _trace(task),
           "event": event, "task_id": (task or {}).get("task_id")}
    rec.update(kw)
    msg = json.dumps(rec, ensure_ascii=False)
    if level == "error":
        logger.error(msg)
    elif level == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)


def _task_log(conn, task, level, message):
    """写库 + 实时推送该任务日志行（STR-01/02）。"""
    conn.execute(
        "INSERT INTO generation_logs (log_id, task_id, level, message, trace_id) VALUES (?,?,?,?,?)",
        (uid(), task["task_id"], level, message, _trace(task)))
    ws_publish("task:%s" % task["task_id"], "log", {"level": level, "message": message})


def _task_status(conn, task, status, progress=None, result_url=None, error_message=None):
    """更新任务状态并通过 WebSocket 实时推送 status_update（KB §18 通道 task:{id}）。"""
    sets, params = [], []
    if status:
        sets.append("status=?"); params.append(status)
    if progress is not None:
        sets.append("progress=?"); params.append(progress)
    if result_url:
        sets.append("result_url=?"); params.append(result_url)
        sets.append("thumbnail_url=?"); params.append(result_url)
    if error_message:
        sets.append("error_message=?"); params.append(error_message[:200])
    if status in ("succeeded", "failed", "cancelled"):
        sets.append("finished_at=?"); params.append(int(time.time()))
    if sets:
        conn.execute("UPDATE generation_tasks SET %s WHERE task_id=?" % ", ".join(sets),
                     params + [task["task_id"]])
    evt = {k: v for k, v in {"status": status, "progress": progress,
                             "result_url": result_url, "error_message": error_message}.items()
           if v is not None}
    evt["status"] = status or task.get("status")
    ws_publish("task:%s" % task["task_id"],
               "completed" if status == "succeeded" else ("failed" if status == "failed" else "status_update"),
               evt)


def _run_with_timeout(fn, *a, timeout=None, **kw):
    """CLOSE-05：在独立线程中执行生成调用并施加硬超时，超时即抛 RuntimeError（触发退避/退款）。

    timeout 缺省用 VIDEO_TIMEOUT_SECS；N04/N05 可按算力队列覆盖（高速 60s / 普通 120s，手册 §3.3.5）。
    """
    fut = _exec.submit(fn, *a, **kw)
    try:
        return fut.result(timeout=timeout or VIDEO_TIMEOUT_SECS)
    except FuturesTimeout:
        raise RuntimeError("生成调用超时（CLOSE-05，%ds）" % (timeout or VIDEO_TIMEOUT_SECS))


def _mark_failed(conn, task, note):
    """统一将任务置为 failed 并退还冻结金（闭环收尾）。"""
    try:
        refund(task["pre_auth_id"], reason=str(note)[:120])
    except Exception:
        pass
    conn.execute(
        """UPDATE generation_tasks SET status='failed', error_code='50001',
           error_message=?, finished_at=? WHERE task_id=?""",
        (str(note)[:200], int(time.time()), task["task_id"]))
    conn.execute("INSERT INTO generation_logs (log_id, task_id, level, message, trace_id) VALUES (?,?,?,?,?)",
                 (uid(), task["task_id"], "error", "生成失败：" + str(note)[:200], _trace(task)))
    _emit(task, "error", "task_failed", reason=str(note)[:200])
    # STR-01/02：实时推送失败
    ws_publish("task:%s" % task["task_id"], "failed", {"error_message": str(note)[:200]})
    seg = fetch_one(conn, "SELECT * FROM segments WHERE segment_id=?", (task["segment_id"],))
    if seg:
        conn.execute("UPDATE segments SET status='failed' WHERE segment_id=?", (task["segment_id"],))
    push_message(conn, "task", "片段生成失败", "原因：%s" % str(note)[:120], action_url="/tasks")


def _serve_one(task):
    if task.get("model_id") == "synthesize":
        return _serve_synthesize(task)
    conn = get_conn()
    try:
        conn.execute("UPDATE generation_tasks SET status='running', started_at=?, progress=0 WHERE task_id=?",
                     (int(time.time()), task["task_id"]))
        conn.commit()
        # STR-01/02：实时推送「进入推理」状态
        ws_publish("task:%s" % task["task_id"], "status_update", {"status": "processing", "progress": 0})

        segment = fetch_one(conn, "SELECT * FROM segments WHERE segment_id=?",
                            (task["segment_id"],))
        shots = fetch_all(conn, 'SELECT * FROM shots WHERE segment_id=? ORDER BY "index" ASC',
                          (task["segment_id"],))
        asset_refs = fetch_all(conn, "SELECT * FROM segment_asset_refs WHERE segment_id=?",
                               (task["segment_id"],))

        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "task_%s.mp4" % task["local_task_id"])

        gen_params = {
            "model_id": task.get("model_id") or "seedance-2.0-mini",
            "resolution": (segment or {}).get("resolution") or "720p",

            "seed": int(task.get("seed") if task.get("seed") is not None
                       else (segment or {}).get("seed", -1) or -1),
        }
        # 透传 Director 入队时附加的镜头参数（first_frame/enable_tail_frame/priority_level 等）；
        # 当前引擎仅消费 resolution/seed，其余为 full 版兼容预留（不破坏既有 dispatch 路径）。
        gen_timeout = None
        try:
            _tp = json.loads(task.get("params") or "{}") if task.get("params") else {}
        except Exception:
            _tp = {}
        for _k in ("first_frame", "enable_tail_frame", "priority_level",
                   "asset_inject_config", "global_style_ref"):
            if _k in _tp and _tp[_k] is not None:
                gen_params[_k] = _tp[_k]
        # N04/N05 算力队列超时（手册 §3.3.5）：高速队列 60s / 普通队列 120s
        if isinstance(_tp.get("gen_timeout"), (int, float)) and _tp["gen_timeout"] > 0:
            gen_timeout = int(_tp["gen_timeout"])
        level, note, tail_frame = _run_with_timeout(
            generate_segment_video,
            row_to_dict(segment) if segment else {"segment_id": task["segment_id"]},
            [row_to_dict(s) for s in shots],
            [row_to_dict(a) for a in asset_refs],
            out_path,
            gen_params,
            timeout=gen_timeout,
        )

        if level == "fail":
            raise RuntimeError(note)

        # 成功：实扣积分（翻转冻结状态）+ 写结果
        cost = task.get("cost_credits") or 0
        try:
            settle(task["pre_auth_id"])
        except ApiError:
            pass
        rel_url = "/file?name=task_%s.mp4" % task["local_task_id"]
        if segment:
            conn.execute("UPDATE segments SET status='succeeded', video_url=?, thumbnail_url=? WHERE segment_id=?",
                         (rel_url, rel_url, task["segment_id"]))
        # N04/N05：尾帧路径落库，供同场景下一分镜作首帧参考（首尾帧锚定）
        if tail_frame:
            try:
                conn.execute("UPDATE generation_tasks SET last_frame_path=? WHERE task_id=?",
                             (tail_frame, task["task_id"]))
            except Exception:
                pass
        # DB 落地 + WebSocket 实时推送 completed（STR-01/02）
        _task_status(conn, task, "succeeded", progress=100, result_url=rel_url)
        _task_log(conn, task, "info", "生成完成：" + note)
        push_message(conn, "task", "片段生成完成",
                     "片段《%s》已生成（%s）" % ((segment or {}).get("title", ""), note),
                     action_url="/projects/%s/production" % ((segment or {}).get("project_id") or ""))
        ws_publish("credits", "balance_update", {"task_id": task["task_id"], "cost": cost})
        log_operation(conn, "segment_generate", "segment", task["segment_id"],
                      cost_credits=cost)
        _emit(task, "info", "task_succeeded", cost_credits=cost, note=note)
        conn.commit()
    except Exception as e:
        # 失败：按 CLOSE-03 退避重试；超过上限才最终失败并退还冻结金
        rc = task.get("retry_count") or 0
        if rc < MAX_AUTO_RETRY:
            backoff = AUTO_RETRY_BASE_DELAY * (2 ** rc)
            next_at = int(time.time()) + backoff
            try:
                conn.execute(
                    "UPDATE generation_tasks SET status='queued', retry_count=?, next_attempt_at=? WHERE task_id=?",
                    (rc + 1, next_at, task["task_id"]))
                _task_log(conn, task, "warning",
                          "生成失败，%ds 后自动重试（第 %d/%d 次）：%s" % (backoff, rc + 1, MAX_AUTO_RETRY, str(e)[:120]))
                ws_publish("task:%s" % task["task_id"], "status_update", {"status": "retrying", "progress": 0})
                _emit(task, "warning", "task_retry", backoff=backoff, attempt=rc + 1, error=str(e)[:120])
                # 冻结金保留供重试使用，不在此退还
            except Exception:
                pass
        else:
            try:
                _mark_failed(conn, task, e)
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


def _serve_synthesize(task):
    """Phase 5（合成全集）：把项目下所有已生成成功的片段视频拼接为一个 MP4。

    与 _serve_one 对称：pre-auth 结算、写结果、失败退款/重试（CLOSE-01/03/04）。
    """
    conn = get_conn()
    try:
        conn.execute("UPDATE generation_tasks SET status='running', started_at=?, progress=0 WHERE task_id=?",
                     (int(time.time()), task["task_id"]))
        conn.commit()
        # STR-01/02：实时推送「进入推理」状态
        ws_publish("task:%s" % task["task_id"], "status_update", {"status": "processing", "progress": 0})

        seg = fetch_one(conn, "SELECT project_id FROM segments WHERE segment_id=?",
                        (task["segment_id"],))
        project_id = seg["project_id"] if seg else None
        if not project_id:
            raise RuntimeError("无法定位合成全集所属项目")

        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "synth_%s.mp4" % project_id)

        # Phase 5：合成参数（本期仅视频拼接，TTS/BGM/字幕留扩展点）
        try:
            synth_options = json.loads(task.get("params") or "{}") if task.get("params") else {}
        except Exception:
            synth_options = {}
        synth_options.setdefault("mode", "video_only")
        synth_options.setdefault("audio", False)
        synth_options.setdefault("subtitle", False)

        level, note = _run_with_timeout(synthesize_project_video, project_id, out_path, synth_options)
        if level == "fail":
            raise RuntimeError(note)

        cost = task.get("cost_credits") or 0
        try:
            settle(task["pre_auth_id"])
        except ApiError:
            pass
        rel_url = "/file?name=synth_%s.mp4" % project_id
        conn.execute(
            """UPDATE generation_tasks SET status='succeeded', result_url=?, thumbnail_url=?,
               cost_credits=?, finished_at=?, params=? WHERE task_id=?""",
            (rel_url, rel_url, cost, int(time.time()),
             json.dumps(synth_options, ensure_ascii=False), task["task_id"]))
        _task_log(conn, task, "info", "合成全集完成：" + note)
        push_message(conn, "task", "合成全集完成",
                     "项目《%s》已合成为完整视频" % (project_id,), action_url="/projects/%s/production" % (project_id or ""))
        ws_publish("task:%s" % task["task_id"], "completed",
                   {"status": "succeeded", "progress": 100, "result_url": rel_url})
        ws_publish("credits", "balance_update", {"task_id": task["task_id"], "cost": cost})
        log_operation(conn, "project_synthesize", "project", project_id, cost_credits=cost)
        _emit(task, "info", "synthesize_succeeded", cost_credits=cost, note=note)
        conn.commit()
    except Exception as e:
        rc = task.get("retry_count") or 0
        if rc < MAX_AUTO_RETRY:
            backoff = AUTO_RETRY_BASE_DELAY * (2 ** rc)
            next_at = int(time.time()) + backoff
            try:
                conn.execute(
                    "UPDATE generation_tasks SET status='queued', retry_count=?, next_attempt_at=? WHERE task_id=?",
                    (rc + 1, next_at, task["task_id"]))
                _task_log(conn, task, "warning",
                          "合成失败，%ds 后自动重试（第 %d/%d 次）：%s" % (backoff, rc + 1, MAX_AUTO_RETRY, str(e)[:120]))
                ws_publish("task:%s" % task["task_id"], "status_update", {"status": "retrying", "progress": 0})
                _emit(task, "warning", "synthesize_retry", backoff=backoff, attempt=rc + 1, error=str(e)[:120])
            except Exception:
                pass
        else:
            try:
                _mark_failed(conn, task, e)
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


def _watchdog(conn):
    """CLOSE-01：将卡死的 running 任务判定 failed。"""
    cutoff = int(time.time()) - STALE_RUNNING_SECS
    stuck = fetch_all(conn,
        "SELECT * FROM generation_tasks WHERE status='running' AND started_at IS NOT NULL AND started_at < ?",
        (cutoff,))
    for t in stuck:
        try:
            _mark_failed(conn, row_to_dict(t), "任务运行超时（>%d 分钟）被看门狗回收" % (STALE_RUNNING_SECS // 60))
        except Exception:
            pass
    if stuck:
        conn.commit()


def _watchdog_images(conn):
    """CLOSE-01：文生图任务同样需要卡死回收。"""
    cutoff = int(time.time()) - STALE_RUNNING_SECS
    stuck = fetch_all(conn,
        "SELECT * FROM image_tasks WHERE status='running' AND started_at IS NOT NULL AND started_at < ?",
        (cutoff,))
    for t in stuck:
        try:
            conn.execute("UPDATE image_tasks SET status='failed', error_message=? WHERE task_id=?",
                         ("任务运行超时（>%d 分钟）被看门狗回收" % (STALE_RUNNING_SECS // 60), t["task_id"]))
        except Exception:
            pass
    if stuck:
        conn.commit()


def _mark_image_failed(conn, task, note):
    """文生图任务失败收尾（无积分冻结，仅记录并通知）。"""
    try:
        conn.execute(
            """UPDATE image_tasks SET status='failed', error_message=?, finished_at=? WHERE task_id=?""",
            (str(note)[:200], int(time.time()), task["task_id"]))
        push_message(conn, "task", "文生图失败", "原因：%s" % str(note)[:120], action_url="/text-to-image")
        ws_publish("task:%s" % task["task_id"], "failed", {"error_message": str(note)[:200]})
        conn.commit()
    except Exception:
        pass


class _CancelledByUser(Exception):
    """用户手动取消任务时抛出，用于中止 txt2img_generate 循环。"""
    pass


def _serve_image_one(task):
    """文生图任务执行：调用 ai_local.txt2img_generate，逐张增量回写进度。"""
    conn = get_conn()
    try:
        # 取消检查 1：开始前检查是否已被用户取消
        cur_status = (task.get("status") or "").strip()
        if cur_status == "cancelled":
            logger.info("t2i task already cancelled, skip: %s", task["task_id"])
            return

        conn.execute("UPDATE image_tasks SET status='running', started_at=? WHERE task_id=?",
                     (int(time.time()), task["task_id"]))
        conn.commit()

        params = {}
        try:
            params = json.loads(task.get("params") or "{}")
        except Exception:
            pass

        width = int(params.get("width") or 1024)
        height = int(params.get("height") or 576)
        steps = int(params.get("steps") or 20)
        cfg = float(params.get("cfg") or 7.0)
        count = int(params.get("count") or 4)
        seed = int(params.get("seed") or -1)
        enhance = bool(params.get("enhance", True))
        orchestrator = params.get("orchestrator") or "ollama"
        style = params.get("style") or "写实"
        task_id = task["task_id"]

        def on_progress(done, total, img):
            c2 = get_conn()
            try:
                # 取消检查 2：每张图完成后检查是否已被用户取消
                row = fetch_one(c2, "SELECT status, result FROM image_tasks WHERE task_id=?", (task_id,))
                if row and (row.get("status") or "") == "cancelled":
                    raise _CancelledByUser()
                prev = []
                if row and row.get("result"):
                    try:
                        prev = json.loads(row["result"]).get("images") or []
                    except Exception:
                        prev = []
                prev.append(img)
                result = json.dumps({"images": prev, "progress": done, "total": total},
                                    ensure_ascii=False)
                c2.execute("UPDATE image_tasks SET result=?, progress=? WHERE task_id=?",
                           (result, done, task_id))
                c2.commit()
                ws_publish("task:%s" % task_id, "progress", {"progress": done, "total": total})
            finally:
                c2.close()

        out = txt2img_generate(
            task["prompt"], task.get("negative_prompt") or "", width, height,
            steps, cfg, count, seed, enhance, orchestrator, style, on_progress=on_progress)

        # 取消检查 3：生成完成后、写 succeeded 前再检查一次
        chk = fetch_one(conn, "SELECT status FROM image_tasks WHERE task_id=?", (task_id,))
        if chk and (chk.get("status") or "") == "cancelled":
            logger.info("t2i task cancelled during generation, skip success: %s", task_id)
            return

        result = json.dumps({
            "images": out["images"],
            "prompt_used": out["prompt_used"],
            "negative_used": out["negative_used"],
            "orchestrator_used": out["orchestrator_used"],
            "model": out["model"],
            "note": out["note"],
        }, ensure_ascii=False)
        conn.execute(
            """UPDATE image_tasks SET status='succeeded', result=?, progress=?, error_message=?, finished_at=?
               WHERE task_id=?""",
            (result, len(out["images"]), "", int(time.time()), task_id))
        push_message(conn, "task", "文生图完成",
                     "已生成 %d 张图像（%s）" % (len(out["images"]), out.get("orchestrator_used") or "原始提示词"),
                     action_url="/text-to-image")
        ws_publish("task:%s" % task_id, "completed", {"status": "succeeded", "progress": len(out["images"])})
        log_operation(conn, "t2i_generate", "image_task", task_id, cost_credits=0)
        conn.commit()
    except _CancelledByUser:
        logger.info("t2i task cancelled in on_progress: %s", task["task_id"])
        # 不重试，保持 cancelled 状态
        try:
            conn.execute(
                "UPDATE image_tasks SET status='cancelled', error_message='用户手动取消', finished_at=? WHERE task_id=?",
                (int(time.time()), task["task_id"]))
            conn.commit()
        except Exception:
            pass
    except Exception as e:
        # CLOSE-03：失败自动退避重试；超过上限才最终失败
        # 但如果是已取消的任务，不重试
        chk2 = fetch_one(conn, "SELECT status FROM image_tasks WHERE task_id=?", (task.get("task_id"),))
        if chk2 and (chk2.get("status") or "") == "cancelled":
            logger.info("t2i task was cancelled, skip retry: %s", task["task_id"])
            return
        rc = task.get("retry_count") or 0
        if rc < MAX_AUTO_RETRY:
            backoff = AUTO_RETRY_BASE_DELAY * (2 ** rc)
            next_at = int(time.time()) + backoff
            try:
                conn.execute(
                    "UPDATE image_tasks SET status='queued', retry_count=?, next_attempt_at=?, error_message=? WHERE task_id=?",
                    (rc + 1, next_at, str(e)[:200], task["task_id"]))
            except Exception:
                pass
        else:
            try:
                _mark_image_failed(conn, task, e)
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


def _loop():
    global _watchdog_counter
    while not _stop_event.is_set():
        try:
            conn = get_conn()
            try:
                _watchdog_counter += 1
                if _watchdog_counter >= _watchdog_every:
                    _watchdog_counter = 0
                    _watchdog(conn)
                    _watchdog_images(conn)
                task = fetch_one(conn,
                    "SELECT * FROM generation_tasks WHERE status IN ('queued','retrying') "
                    "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                    "ORDER BY priority DESC, created_at ASC LIMIT 1",
                    (int(time.time()),))
            finally:
                conn.close()
            if task:
                try:
                    _serve_one(row_to_dict(task))
                except Exception as e:
                    try:
                        c2 = get_conn()
                        try:
                            _mark_failed(c2, row_to_dict(task), "Worker 异常：" + str(e)[:160])
                            c2.commit()
                        finally:
                            c2.close()
                    except Exception:
                        pass
                continue
            # 文生图任务队列
            iconn = get_conn()
            try:
                itask = fetch_one(iconn,
                    "SELECT * FROM image_tasks WHERE status IN ('queued','retrying') "
                    "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                    "ORDER BY priority DESC, created_at ASC LIMIT 1",
                    (int(time.time()),))
            finally:
                iconn.close()
            if itask:
                try:
                    _serve_image_one(row_to_dict(itask))
                except Exception as e:
                    try:
                        c3 = get_conn()
                        try:
                            c3.execute("UPDATE image_tasks SET status='failed', error_message=? WHERE task_id=?",
                                       ("Worker 异常：" + str(e)[:160], itask["task_id"]))
                            c3.commit()
                        finally:
                            c3.close()
                    except Exception:
                        pass
        except Exception:
            # 队列读取异常：短暂退避后继续，不退出线程
            pass
        # 手册 §7.2：刷新队列深度 / 活跃运行数指标
        try:
            mconn = get_conn()
            try:
                pending = fetch_one(mconn,
                    "SELECT (SELECT COUNT(*) FROM generation_tasks WHERE status IN ('queued','retrying'))"
                    " + (SELECT COUNT(*) FROM image_tasks WHERE status IN ('queued','retrying')) AS c")
                running = fetch_one(mconn,
                    "SELECT (SELECT COUNT(*) FROM generation_tasks WHERE status='running')"
                    " + (SELECT COUNT(*) FROM image_tasks WHERE status='running') AS c")
                metrics.set_queue_depth((pending or {}).get("c", 0) or 0)
                metrics.set_active_runs((running or {}).get("c", 0) or 0)
            finally:
                mconn.close()
        except Exception:
            pass
        _stop_event.wait(POLL_INTERVAL)

def recover():
    """CLOSE-01 崩溃恢复：把上次进程退出遗留的 running 任务复位为 queued。"""
    conn = get_conn()
    try:
        n = conn.execute(
            "UPDATE generation_tasks SET status='queued' WHERE status='running'").rowcount
        n2 = conn.execute(
            "UPDATE image_tasks SET status='queued' WHERE status='running'").rowcount
        conn.commit()
        if n or n2:
            log_operation(conn, "worker_recover", "system", None,
                          "复位 %d 个崩溃遗留 running 任务为 queued（含文生图 %d）" % (n, n2))
            conn.commit()
    finally:
        conn.close()


def start():
    global _worker_thread
    _stop_event.clear()
    recover()
    _worker_thread = threading.Thread(target=_loop, name="gen-worker", daemon=True)
    _worker_thread.start()


def stop():
    _stop_event.set()
