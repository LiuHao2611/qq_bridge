# -*- coding: utf-8 -*-
"""queue —— QQ 侧持久有界任务队列（2026-09-08 新增）。

为什么排队在 bridge 侧而不是主项目侧
------------------------------------
红线要求「QQ 服务崩溃/重启绝不影响主项目 7788/8099」。若把排队做进主项目的
`image_tasks` + `worker.py`，就要动主项目核心状态机，QQ 的异常会传导过去。
因此：主项目保持原样（一次跑一个、忙时 409），**队列由 bridge 持有**。

行业参考（2026 检索）：
  * nonoo/stable-diffusion-telegram-bot —— 请求排队、一次处理一个（与本地
    ComfyUI 独占同构）；
  * scristobal/botatobot —— **bounded** worker queue：满了明确拒绝，不无限堆积；
  * Duxon/FLUX.1-Kontext-Bot —— 队列持久化 + 空闲回收。
  三者 License 均未声明 → **只借鉴思路，代码全部重写**。

能力
----
  * 有界：`QUEUE_MAX`（默认 20）满则拒绝并告知排队数；
  * 持久：落 `qq_bridge/data/queue.json`，bridge 重启后可恢复；
  * 单消费者：同一时刻只向平台提交 1 个任务（本地 ComfyUI 独占）；
  * 平台忙退避：队首提交遇 409 IMG_BUSY → 退避 `QUEUE_BUSY_BACKOFF`（默认 300s）
    后重试，**不弹出队**；重试 `QUEUE_BUSY_RETRY`（默认 3）次仍忙 →
    通知用户「平台持续繁忙」并弹出队列（标记 failed）。用户拍板值：3 × 5 分钟。
  * 幂等：`QUEUE_IDEMPOTENT_TTL` 窗口内同 (user, prompt, 参数) 重复下发 → 返回原任务。

铁律
----
  * 只经 HTTP 调主项目（单向依赖），**不 import 主项目任何模块**；
  * 不 import nonebot（本模块可独立单测，注入 MockTransport 即可）；
  * 通知靠**注入的 notifier 回调**（async callable），不直接依赖 connector，
    避免循环导入，也让单测能捕获所有通知；
  * CLOSE-02：任何异常都要落到 job["error"] 并通知用户，**禁止静默吞异常**；
  * OPS-01：文件句柄 try/finally 释放。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from pathlib import Path

import httpx

log = None  # 延迟绑定（见 _log()），避免模块加载时依赖 ..config 的日志初始化


def _log():
    """惰性拿 logger：模块可被单测直接加载，此时不一定有 qq_bridge 日志体系。"""
    global log
    if log is None:
        try:
            from ..config import get_logger
            log = get_logger()
        except Exception:  # pragma: no cover - 单测无日志体系时退化
            import logging
            log = logging.getLogger("qq_bridge.queue")
    return log


# qq_bridge/ 根目录（与 config_api.ROOT_DIR 算法一致：parents[3]）
ROOT_DIR = Path(__file__).resolve().parents[3]
# 数据目录：默认 qq_bridge/data。可用 QQ_DATA_DIR 覆盖 —— e2e 测试起独立 bridge 进程时
# 指向临时目录，避免把测试任务写进用户真实队列/计划（2026-09-13 为定时执行 e2e 加）。
DATA_DIR = (Path(os.environ["QQ_DATA_DIR"]).resolve()
            if os.environ.get("QQ_DATA_DIR") else (ROOT_DIR / "data"))
QUEUE_PATH = DATA_DIR / "queue.json"

# 任务状态
ST_QUEUED = "queued"
ST_RUNNING = "running"
ST_DONE = "done"
ST_FAILED = "failed"
ST_CANCELED = "canceled"

# 终态（不再被消费者处理）
TERMINAL = (ST_DONE, ST_FAILED, ST_CANCELED)

# 单次批量导入的硬上限（防坏文件把内存/队列撑爆；正常计划远小于此）
BATCH_HARD_CAP = 500


def _cfg(name: str, default):
    """**运行时**读 Config 类属性（热生效：改配置无需重启 bridge）。

    每次调用都读——不能缓存，否则用户在管理页改了 QUEUE_MAX 要重启才生效
    （2026-09-07 已修过同类热生效问题）。
    """
    try:
        from ..config import Config
        return getattr(Config, name, default)
    except Exception:
        return default


def _now() -> float:
    return time.time()


# 主项目 /t2i/edit 的 timeout 字段上限（server/routers.py：>18000 会被静默回落成 1800）。
# QQ 端允许把 TASK_TIMEOUT 设得很大（本端愿意一直等），但下发给平台时必须夹进
# [60, 18000]，否则用户填 999999999 会被平台悄悄改成 1800 —— 表现为"设置没生效、
# 任务还是 30 分钟就超时失败"。2026-09-13 修复（用户反馈"不想任务总因超时失败"时发现）。
_PLATFORM_TASK_TIMEOUT_MAX = 18000
_PLATFORM_TASK_TIMEOUT_MIN = 60


def _platform_timeout() -> int:
    """下发给主项目 /t2i/edit 的任务超时（秒），夹在平台允许区间内。"""
    try:
        v = int(float(_cfg("TASK_TIMEOUT", 600) or 600))
    except Exception:  # noqa: BLE001
        v = 600
    return max(_PLATFORM_TASK_TIMEOUT_MIN, min(_PLATFORM_TASK_TIMEOUT_MAX, v))


def make_idem_key(user_id: int, prompt: str, params: dict) -> str:
    """幂等键：同一用户 + 同一提示词 + 同一参数 → 同一 key。"""
    raw = "%s|%s|%s" % (
        user_id,
        (prompt or "").strip(),
        json.dumps(params or {}, sort_keys=True, ensure_ascii=False),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class TaskQueue:
    """持久有界队列 + 单消费者。

    :param path: 持久化文件路径（单测可传 tmp_path）
    :param api_base: 主项目 API 根（形如 http://127.0.0.1:7788/api/v1）
    :param transport: httpx.AsyncBaseTransport（单测注入 MockTransport 免起后端）
    :param notifier: async callable(event: str, job: dict) —— 关键事件通知出口
    """

    def __init__(
        self,
        path=None,
        api_base: str | None = None,
        transport=None,
        notifier=None,
    ) -> None:
        self.path = Path(path) if path else QUEUE_PATH
        self._api_base = api_base  # None → 运行时读 Config.MANJU_API_BASE
        self._transport = transport
        self.notifier = notifier
        self._jobs: list[dict] = []
        self._seq = 0
        self._lock = asyncio.Lock()
        self._running = False
        self._task: asyncio.Task | None = None
        # 已完成/失败的任务保留最近 N 条供 `任务` / `最近` 查询与「再看一遍」
        # 50 偏小：一个 10 条批次就可能被后续任务挤出，导致「已执行」标记点开后
        # batch_detail 报「已超历史保留上限被清理」。提到 300 给批次成品更久的存活期。
        self._history: list[dict] = []
        self._history_max = 300
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self) -> None:
        """从磁盘恢复。文件损坏/不存在 → 空队列（不能让坏文件拖垮启动）。"""
        try:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            self._jobs = [j for j in (raw.get("jobs") or []) if isinstance(j, dict)]
            self._history = [j for j in (raw.get("history") or []) if isinstance(j, dict)]
            self._seq = int(raw.get("seq") or 0)
            # 重启恢复：中断的 running 任务平台侧可能还在跑，
            # 但本进程已无轮询上下文 → 标记为 failed 让用户知情（不静默丢失）
            for j in self._jobs:
                if j.get("state") == ST_RUNNING:
                    j["state"] = ST_FAILED
                    j["error"] = "bridge 重启，任务中断（平台侧可能仍在运行）"
        except Exception as e:
            _log().warning("queue_load_failed error=%s detail=%s"
                           % (type(e).__name__, str(e)[:200]))
            self._jobs, self._history, self._seq = [], [], 0

    def _save(self) -> None:
        """落盘。失败只记日志——**不能因为存盘失败就让机器人不回话**（CLOSE-02）。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {"seq": self._seq, "jobs": self._jobs, "history": self._history},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(self.path))  # 原子替换，防写一半崩
        except Exception as e:
            _log().warning("queue_save_failed error=%s detail=%s"
                           % (type(e).__name__, str(e)[:200]))

    # ---------------- 属性 ----------------
    @property
    def api_base(self) -> str:
        base = self._api_base
        if not base:
            base = _cfg("MANJU_API_BASE", "http://127.0.0.1:7788/api/v1")
        return str(base).rstrip("/")

    @property
    def max_size(self) -> int:
        return max(1, int(_cfg("QUEUE_MAX", 20) or 20))

    @property
    def busy_backoff(self) -> float:
        return max(1.0, float(_cfg("QUEUE_BUSY_BACKOFF", 300.0) or 300.0))

    @property
    def busy_retry(self) -> int:
        return max(0, int(_cfg("QUEUE_BUSY_RETRY", 3) or 3))

    # ---------------- 入队 / 取消 / 插队 ----------------
    async def enqueue(self, user_id: int, group_id: int, scene: str,
                      prompt: str, params: dict | None = None) -> dict:
        """入队。返回 {ok, job, position, error}。

        position 为 1-based 排队位次（供「已入队 · 第 N 位」文案使用）。
        """
        params = dict(params or {})
        await self.ensure_consumer()
        async with self._lock:
            # ---- 幂等：窗口内同 user+prompt+params → 返回原任务 ----
            ttl = int(_cfg("QUEUE_IDEMPOTENT_TTL", 60) or 0)
            if ttl > 0:
                key = make_idem_key(user_id, prompt, params)
                for j in self._jobs + self._history:
                    if (j.get("idempotent_key") == key
                            and _now() - float(j.get("enqueued_at") or 0) < ttl
                            and j.get("state") not in (ST_FAILED, ST_CANCELED)):
                        return {"ok": True, "job": j, "position": self._position(j),
                                "duplicate": True, "error": ""}

            # ---- 有界：满了明确拒绝 ----
            pending = [j for j in self._jobs if j.get("state") in (ST_QUEUED, ST_RUNNING)]
            if len(pending) >= self.max_size:
                return {
                    "ok": False, "job": None, "position": 0, "duplicate": False,
                    "error": "队列已满（%d/%d），先等一会儿或让主人「清空队列」" % (len(pending), self.max_size),
                }

            self._seq += 1
            job = {
                "seq": self._seq,
                "job_id": uuid.uuid4().hex[:12],
                "user_id": int(user_id),
                "group_id": int(group_id or 0),
                "scene": scene or ("group" if group_id else "private"),
                "prompt": prompt,
                "params": params,
                "enqueued_at": _now(),
                "started_at": None,
                "finished_at": None,
                "state": ST_QUEUED,
                "task_id": "",
                "error": "",
                "busy_retries": 0,
                "idempotent_key": make_idem_key(user_id, prompt, params) if ttl > 0 else "",
                "images": [],
            }
            self._jobs.append(job)
            self._save()

        await self._notify("enqueued", job)
        return {"ok": True, "job": job, "position": self._position(job),
                "duplicate": False, "error": ""}

    def _position(self, job: dict) -> int:
        """1-based 排队位次（含 running 的算第 1 位）。"""
        pending = [j for j in self._jobs if j.get("state") in (ST_QUEUED, ST_RUNNING)]
        pending.sort(key=lambda j: (j.get("state") != ST_RUNNING, int(j.get("seq") or 0)))
        for i, j in enumerate(pending, 1):
            if j.get("job_id") == job.get("job_id"):
                return i
        return 0

    async def cancel(self, target: str, user_id: int, is_owner: bool = False) -> dict:
        """取消任务。target 可以是 seq / job_id / 平台 task_id 前 8 位。

        权限：普通用户只能取消**自己**的；主人可取消任意。
        """
        job = self._find(target)
        if not job:
            return {"ok": False, "error": "没找到任务 %s（用「队列」看看有哪些）" % target}
        if not is_owner and int(job.get("user_id") or 0) != int(user_id):
            return {"ok": False, "error": "那不是你的任务，取消不了（只有主人能动别人的）"}
        if job.get("state") in TERMINAL:
            return {"ok": False, "error": "任务已经是终态（%s），不用取消" % job.get("state")}

        # 已提交到平台 → 调 /t2i/tasks/{id}/stop 真正中断 ComfyUI 并释放显存
        tid = str(job.get("task_id") or "")
        if tid:
            try:
                await self._post("/t2i/tasks/%s/stop" % tid, {})
            except Exception as e:
                _log().warning("queue_stop_failed task_id=%s error=%s"
                               % (tid[:8], type(e).__name__))

        job["state"] = ST_CANCELED
        job["finished_at"] = _now()
        await self._archive(job)
        self._save()
        await self._notify("canceled", job)
        return {"ok": True, "job": job, "error": ""}

    async def promote(self, target: str, is_owner: bool = False) -> dict:
        """插队（仅主人）：把任务提到队首（running 的不能动）。"""
        if not is_owner:
            return {"ok": False, "error": "插队只有主人能用"}
        job = self._find(target)
        if not job:
            return {"ok": False, "error": "没找到任务 %s" % target}
        if job.get("state") != ST_QUEUED:
            return {"ok": False, "error": "只有排队中的任务能插队（当前：%s）" % job.get("state")}
        # 提到所有 queued 之前：给一个比现有最小 seq 更小的 seq
        async with self._lock:
            queued = [j for j in self._jobs if j.get("state") == ST_QUEUED and j is not job]
            min_seq = min([int(j.get("seq") or 0) for j in queued], default=0)
            job["seq"] = min_seq - 1
            self._save()
        await self._notify("promoted", job)
        return {"ok": True, "job": job, "error": ""}

    async def clear(self, is_owner: bool = False) -> dict:
        """清空队列（仅主人）：取消所有 queued（running 的不动，避免打断正在跑的）。"""
        if not is_owner:
            return {"ok": False, "error": "清空队列只有主人能用"}
        n = 0
        async with self._lock:
            for j in self._jobs:
                if j.get("state") == ST_QUEUED:
                    j["state"] = ST_CANCELED
                    j["finished_at"] = _now()
                    await self._archive(j)
                    n += 1
            self._save()
        return {"ok": True, "error": "", "cleared": n}

    # ---------------- 批量生图计划（2026-09-11 新增）----------------
    async def enqueue_batch(self, items, channel, user_id, group_id=0, scene="web",
                            batch_id=None, name="", plan_id="", scheduled_at=0) -> dict:
        """批量导入生图计划：逐条校验 → 入队（跳过幂等/有界上限，但受 BATCH_HARD_CAP 限制）。

        每条计划项是一个独立队列任务，复用单消费者循环 → 计划内任务**依次执行**。
        channel: "local"（本地 ComfyUI）/ "online"（在线云端），决定提交路径。
        name: 计划名称（Web 面板展示「计划名称」列）；plan_id: 来源草稿计划 id；
        scheduled_at: 定时触发时间（0=手动下发，非定时）。

        返回 {ok, batch_id, total, accepted, rejected, errors, jobs:[{seq,index,name,prompt}]}。
        """
        if not isinstance(items, list):
            return {"ok": False, "error": "计划必须是 JSON 数组", "batch_id": "",
                    "total": 0, "accepted": 0, "rejected": 0, "errors": ["计划必须是 JSON 数组"], "jobs": []}
        await self.ensure_consumer()
        channel = str(channel or "local").strip().lower()
        if channel not in ("local", "online"):
            channel = "local"
        batch_id = batch_id or uuid.uuid4().hex[:12]

        accepted_jobs, rejected, errors, jobs = [], [], [], []
        async with self._lock:
            for i, raw in enumerate(items):
                norm, why = self._normalize_plan_item(raw, channel)
                if norm is None:
                    rejected.append(i)
                    errors.append("第 %d 条：%s" % (i + 1, why))
                    continue
                if len(self._jobs) >= BATCH_HARD_CAP:
                    rejected.append(i)
                    errors.append("第 %d 条：超出单次批量上限 %d，未入队" % (i + 1, BATCH_HARD_CAP))
                    continue
                self._seq += 1
                job = {
                    "seq": self._seq,
                    "job_id": uuid.uuid4().hex[:12],
                    "user_id": int(user_id),
                    "group_id": int(group_id or 0),
                    "scene": scene or ("group" if group_id else "private"),
                    "prompt": norm["prompt"],
                    "params": norm["params"],
                    "batch_id": batch_id,
                    "batch_name": str(name or "")[:80],
                    "plan_id": str(plan_id or ""),
                    "scheduled_at": float(scheduled_at or 0),
                    "channel": channel,
                    "plan_index": len(accepted_jobs) + 1,
                    "enqueued_at": _now(),
                    "started_at": None,
                    "finished_at": None,
                    "state": ST_QUEUED,
                    "task_id": "",
                    "error": "",
                    "busy_retries": 0,
                    "idempotent_key": "",
                    "images": [],
                }
                self._jobs.append(job)
                accepted_jobs.append(job)
                jobs.append({
                    "seq": job["seq"], "index": norm.get("index"),
                    "name": norm.get("name"), "prompt": norm["prompt"],
                })
            self._save()
        return {
            "ok": True, "batch_id": batch_id, "total": len(items),
            "accepted": len(accepted_jobs), "rejected": len(rejected),
            "errors": errors[:50], "jobs": jobs,
        }

    # ---------------- 批次计划管理（2026-09-12 新增：Web 面板「计划」Tab）----------------
    def batches_summary(self) -> list:
        """所有「计划/任务组」聚合（2026-09-15 扩展：5 状态页签 + 计划列表日期列）。

        合并活跃队列 + 历史；按 batch_id 分组，**非批次的单条任务各自成一组**（kind="single"），
        保证「无论什么任务，只要执行过，都能在页签里查到」。

        返回字段：
          key/kind/batch_id/name/channel/plan_id      —— 行标识与「计划名称」
          created_at                                  —— 计划日期（最早入队时间）
          scheduled_at                                —— 定时日期（定时触发时间，0=手动）
          started_at / finished_at                    —— 计划开始日期 / 最近完成日期
          total/done/failed/running/queued/canceled   —— 各状态条数
          executed/finished/unfinished                —— 已执行过 / 已结束 / 未结束
          progress/paused                             —— 真实进度百分比 / 是否暂停
          bucket                                      —— 页签归类（单一真源在后端）
          job                                         —— 仅单条任务组：该任务详情（前端免二次请求）
        """
        pool = self._jobs + self._history
        groups: dict[str, list] = {}
        for j in pool:
            bid = str(j.get("batch_id") or "")
            key = ("b:" + bid) if bid else ("s:" + str(j.get("job_id") or j.get("seq") or ""))
            groups.setdefault(key, []).append(j)
        out = []
        for key, jobs in groups.items():
            bid = str(jobs[0].get("batch_id") or "")
            total = len(jobs)
            c = {"queued": 0, "running": 0, "done": 0, "failed": 0, "canceled": 0}
            for j in jobs:
                st = j.get("state")
                if st in c:
                    c[st] += 1
                else:
                    c["canceled"] += 1
            started = [float(j.get("started_at") or 0) for j in jobs]
            finished = [float(j.get("finished_at") or 0) for j in jobs]
            scheduled = [float(j.get("scheduled_at") or 0) for j in jobs]
            executed = sum(
                1 for j in jobs
                if float(j.get("started_at") or 0) > 0
                or j.get("state") in (ST_RUNNING, ST_DONE, ST_FAILED)
            )
            fin = c["done"] + c["failed"]
            out.append({
                "key": key,
                "kind": "batch" if bid else "single",
                "batch_id": bid,
                "name": jobs[0].get("batch_name") or "",
                "channel": jobs[0].get("channel") or "local",
                "plan_id": jobs[0].get("plan_id") or "",
                "created_at": min((float(j.get("enqueued_at") or 0) for j in jobs), default=0),
                "scheduled_at": max(scheduled) if scheduled else 0,
                "started_at": min([t for t in started if t > 0], default=0),
                "finished_at": max(finished) if finished else 0,
                "total": total,
                "done": c["done"],
                "failed": c["failed"],
                "running": c["running"],
                "queued": c["queued"],
                "canceled": c["canceled"],
                "executed": executed,
                "finished": fin,
                "unfinished": total - fin,
                "progress": round(c["done"] / total * 100, 1) if total else 0.0,
                "paused": any(bool(j.get("_paused")) for j in jobs),
                "bucket": self._bucket_of(c, total),
                "job": self._public(jobs[0]) if not bid else None,
            })
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return out

    @staticmethod
    def _bucket_of(c: dict, total: int) -> str:
        """页签归类。优先级：

        1) 有运行中            → running（运行中）
        2) 一条都没结束且还有排队 → queued（排队中）
        3) 还有排队/运行中没结束 → partial（已执行未完成）
           例：一个计划 30 条只执行了 4 条，还有 26 条排队中 → 已执行未完成
        4) 含「已取消」条目      → partial（已执行未完成）
           —— 取消 ≠ 执行完：有计划内条目被取消（用户主动停/计划暂停），即"未跑完"。
              用户举例：30 条只执行 4 条（4 条失败、其余取消）→ 已执行未完成，而非失败。
              与量化口径一致：canceled 计入 unfinished（未结束），bucket 必须同为 partial。
              优先于 failed 判定——"失败"页签语义 = 计划内条目全部执行完且有失败。
        5) 全部结束（done/failed）且有失败 → failed（失败）
        6) 其余（全部条目成功、无失败、无取消）→ done（已完成）

        注意：canceled 不算「成功」，任何含 canceled 的已结束批次都不会落入 已完成。
        """
        fin = c["done"] + c["failed"] + c.get("canceled", 0)
        if c["running"] > 0:
            return "running"
        if fin == 0 and c["queued"] > 0:
            return "queued"
        # 还有未结束的（queued/running 且不算 canceled）→ partial
        pending = c["queued"] + c["running"]
        if pending > 0:
            return "partial"
        # 含已取消条目 → 已执行未完成（计划未跑完；先于 failed 判定，见契约 4）
        if c["canceled"] > 0:
            return "partial"
        # 全部结束，看有没有失败
        if c["failed"] > 0:
            return "failed"
        return "done"

    def batch_detail(self, batch_id: str) -> dict:
        """某批次内每条任务的脱敏详情（按 plan_index 排序 = JSON 顺序）。"""
        pool = self._jobs + self._history
        jobs = [j for j in pool if j.get("batch_id") == batch_id]
        if not jobs:
            return {"ok": False, "error": "计划不存在（可能已超历史保留上限被清理）"}
        jobs.sort(key=lambda j: int(j.get("plan_index") or j.get("seq") or 0))
        return {"ok": True, "batch_id": batch_id, "total": len(jobs),
                "items": [self._public(j) for j in jobs]}

    async def batch_cancel(self, batch_id: str) -> dict:
        """计划级暂停：剩余 queued → canceled（标记 _paused，便于恢复）。
        running 不动（不打断正在跑的）；queued 无 task_id 不需调平台 stop。"""
        n = 0
        async with self._lock:
            for j in list(self._jobs):
                if j.get("batch_id") == batch_id and j.get("state") == ST_QUEUED:
                    j["state"] = ST_CANCELED
                    j["finished_at"] = _now()
                    j["_paused"] = True
                    await self._archive(j)
                    n += 1
            self._save()
        return {"ok": True, "batch_id": batch_id, "canceled": n}

    async def batch_resume(self, batch_id: str) -> dict:
        """计划级恢复：只复活「因暂停被取消」(_paused) 的条目，不误捞手动取消的。
        幂等：重复调用 0 行受影响仍返回 ok。"""
        n = 0
        async with self._lock:
            restored = []
            for j in list(self._history):
                if j.get("batch_id") == batch_id and j.get("_paused"):
                    j["state"] = ST_QUEUED
                    j["finished_at"] = None
                    j["_paused"] = False
                    self._seq += 1
                    j["seq"] = self._seq
                    restored.append(j)
                    n += 1
            if restored:
                paused_ids = {j.get("job_id") for j in restored}
                self._history = [j for j in self._history if j.get("job_id") not in paused_ids]
                self._jobs.extend(restored)
                self._save()
        await self.ensure_consumer()
        return {"ok": True, "batch_id": batch_id, "resumed": n}

    async def batch_retry_failed(self, batch_id: str) -> dict:
        """计划级重试失败：本批次内 failed 任务重置回 queued 重新执行（移回活跃队列队尾）。
        幂等：无失败返回 retried=0 仍 ok。"""
        n = 0
        async with self._lock:
            restored = []
            for j in list(self._history):
                if j.get("batch_id") == batch_id and j.get("state") == ST_FAILED:
                    j["state"] = ST_QUEUED
                    j["finished_at"] = None
                    j["error"] = None
                    j["task_id"] = None
                    j["_paused"] = False
                    self._seq += 1
                    j["seq"] = self._seq
                    restored.append(j)
                    n += 1
            if restored:
                ids = {j.get("job_id") for j in restored}
                self._history = [j for j in self._history if j.get("job_id") not in ids]
                self._jobs.extend(restored)
                self._save()
        if n:
            await self.ensure_consumer()
        return {"ok": True, "batch_id": batch_id, "retried": n}

    async def retry(self, target: str, user_id: int, is_owner: bool = False) -> dict:
        """重试单个失败任务：failed → queued 重新提交。权限同 cancel。"""
        job = self._find(target)
        if not job:
            return {"ok": False, "error": "没找到任务 %s" % target}
        if not is_owner and int(job.get("user_id") or 0) != int(user_id):
            return {"ok": False, "error": "那不是你的任务，重试不了（只有主人能动别人的）"}
        if job.get("state") != ST_FAILED:
            return {"ok": False, "error": "只有失败的任务能重试（当前：%s）" % job.get("state")}
        async with self._lock:
            job["state"] = ST_QUEUED
            job["finished_at"] = None
            job["error"] = None
            job["task_id"] = None
            job["_paused"] = False
            self._history = [j for j in self._history if j.get("job_id") != job.get("job_id")]
            self._seq += 1
            job["seq"] = self._seq
            self._jobs.append(job)
            self._save()
        await self.ensure_consumer()
        return {"ok": True, "job": self._public(job), "error": ""}

    @staticmethod
    def _normalize_plan_item(it, channel: str) -> tuple:
        """把一条计划项规范化成 {prompt, params}。失败返回 (None, 原因)。

        字段兼容中文键与英文别名：
          类型/kind（缺省 = 文生图；"upscale"/"放大" = 图片批量放大 · 2026-09-12 新增）、
          正面提示词/prompt（文生图必填）、负面提示词/negative、图片名称/name、
          宽/width、高/height、比例/ratio、尺寸/size、序号/index；
          放大类专用：图片/image（必填，/file?name=… 或完整 URL）、倍数/scale（2/4）、
          模板/template_id（可选，走 ComfyUI 模板放大工作流）。
        """
        if not isinstance(it, dict):
            return None, "不是对象"
        kind = str(it.get("类型") or it.get("kind") or it.get("task_type") or "").strip().lower()
        if kind in ("upscale", "放大", "图片放大", "批量放大", "i2i_upscale"):
            return TaskQueue._normalize_upscale_item(it)
        prompt = str(it.get("正面提示词") or it.get("prompt") or "").strip()
        if not prompt:
            return None, "缺少 正面提示词"
        neg = str(it.get("负面提示词") or it.get("negative") or it.get("negative_prompt") or "").strip()
        name = str(it.get("图片名称") or it.get("name") or "").strip()
        ratio = str(it.get("比例") or it.get("ratio") or "").strip()
        size = str(it.get("尺寸") or it.get("size") or "").strip()
        idx = it.get("序号") or it.get("index")
        w = it.get("宽") or it.get("width")
        h = it.get("高") or it.get("height")
        try:
            w = int(w) if w not in (None, "") else None
            h = int(h) if h not in (None, "") else None
        except (TypeError, ValueError):
            w, h = None, None
        params = {"channel": channel, "negative": neg, "name": name, "ratio": ratio, "size": size}
        if w:
            params["width"] = w
        if h:
            params["height"] = h
        # 模板（2026-09-13 新增）：与放大项对齐——计划里可指定文生图模板。
        # 仅本地通道有意义（在线平台没有模板概念），由主项目 /qq/image-generate
        # 的 template_id 入参消费（该端点已支持，见 server/routers.py）。
        tpl = str(it.get("模板") or it.get("template_id") or "").strip()
        if tpl:
            if str(channel or "").strip().lower() == "local":
                params["template_id"] = tpl
            else:
                # 在线通道忽略模板，但明确记一条，避免用户以为没生效
                _log().warning(
                    "event=queue_plan_template_ignored channel=%s template_id=%s "
                    "reason=online channel has no template" % (channel, tpl[:48]))
        return {"prompt": prompt, "params": params, "index": idx, "name": name}, None

    @staticmethod
    def _normalize_upscale_item(it: dict) -> tuple:
        """放大计划项（2026-09-12）：图片 必填；倍数只认 2/4（其他值就近收敛）。

        放大只支持本地 ComfyUI（/t2i/edit 是本地通道端点）→ channel 强制 "local"。
        prompt 非必填：缺省用 图片名称 或源图文件名占位（仅作队列展示）。
        """
        image = str(it.get("图片") or it.get("image") or it.get("源图")
                    or it.get("source") or it.get("src") or "").strip()
        if not image:
            return None, "放大计划缺少 图片（/file?name=… 或完整 URL）"
        fname = image.rsplit("/", 1)[-1].split("?")[0]
        name = str(it.get("图片名称") or it.get("name") or "").strip() \
            or ("放大 " + fname[:32])
        try:
            scale = int(it.get("倍数") or it.get("scale") or 2)
        except (TypeError, ValueError):
            scale = 2
        scale = 4 if scale >= 4 else 2
        prompt = str(it.get("正面提示词") or it.get("prompt") or name).strip()
        idx = it.get("序号") or it.get("index")
        params = {
            "channel": "local", "kind": "upscale",
            "image": image, "scale": scale, "name": name,
        }
        tpl = str(it.get("模板") or it.get("template_id") or "").strip()
        if tpl:
            params["template_id"] = tpl
        return {"prompt": prompt, "params": params, "index": idx, "name": name}, None

    def _find(self, target: str) -> dict | None:
        """按 seq / job_id / task_id 前 8 位 / 提示词片段 查找（优先活跃队列，再历史）。"""
        t = str(target or "").strip().lower().lstrip("#")
        if not t:
            return None
        pool = self._jobs + self._history
        # 1) seq 精确
        for j in pool:
            if str(j.get("seq")) == t:
                return j
        # 2) job_id / task_id 前缀
        for j in pool:
            for k in ("job_id", "task_id"):
                v = str(j.get(k) or "").lower()
                if v and (v == t or v.startswith(t)):
                    return j
        # 3) 提示词片段兜底
        for j in pool:
            if t in str(j.get("prompt") or "").lower():
                return j
        return None

    async def _archive(self, job: dict) -> None:
        """终态任务从活跃队列移入历史（保留最近 N 条，供「任务」/「再看」查询）。"""
        self._jobs = [j for j in self._jobs if j.get("job_id") != job.get("job_id")]
        self._history.insert(0, job)
        self._history = self._history[: self._history_max]

    # ---------------- 查询（给命令 + Web 面板）----------------
    def snapshot(self) -> dict:
        """队列快照：`GET /api/qq/queue` 与 QQ「队列」命令共用。"""
        queued = sorted(
            [j for j in self._jobs if j.get("state") == ST_QUEUED],
            key=lambda j: int(j.get("seq") or 0),
        )
        running = next((j for j in self._jobs if j.get("state") == ST_RUNNING), None)
        return {
            "ok": True,
            "max": self.max_size,
            "queued": [self._public(j) for j in queued],
            "running": self._public(running) if running else None,
            "history": [self._public(j) for j in self._history[:20]],
            "queued_count": len(queued),
            "running_count": 1 if running else 0,
            "consumer_alive": self._task is not None and not self._task.done(),
            "consumer_running_flag": self._running,
        }

    def running_status(self) -> dict | None:
        """给 /status 兜底：返回运行中任务（与 taskmgr 任务簿同构），无则 None。

        2026-09-11：本地生图全走队列通道（queue_t2i）后不经 taskmgr，
        全局任务簿永远空 → /status 误报「闲着呢」（后台明明有任务在跑）。
        队列才是任务真相源：running job 即「在跑」。字段与 taskmgr._current_tasks
        条目对齐（kind/prompt/trace_id/started_at/user_id），另带 queued_behind
        （后面还排几条）供文案展示。
        """
        running = next((j for j in self._jobs if j.get("state") == ST_RUNNING), None)
        if running is None:
            return None
        tid = (running.get("task_id") or "")[:8]
        return {
            "task_id": tid,
            "user_id": running.get("user_id"),
            "group_id": running.get("group_id"),
            "kind": ((running.get("params") or {}).get("kind") or "t2i"),
            "prompt": running.get("prompt") or "",
            "trace_id": tid,
            "channel": "queue",
            # 刚提交还没轮询首轮时 started_at 可能为空 → 用入队时间兜底
            "started_at": running.get("started_at")
            or running.get("enqueued_at")
            or _now(),
            "queued_behind": sum(
                1 for j in self._jobs if j.get("state") == ST_QUEUED
            ),
        }

    def pending_status(self) -> dict | None:
        """无 running 但有排队：返回最早排队任务 + 排队数（给 /status 用）。

        2026-09-11：主项目本地 ComfyUI 串行一次只跑 1 个，队列提交被判 busy 时
        消费者会退避等待（此时队列里**没有 running，只有 queued**）。只查 running
        会让 /status 在「后台明明堆着几十条排队」时回「闲着呢」，属假阴性。
        """
        queued = sorted(
            [j for j in self._jobs if j.get("state") == ST_QUEUED],
            key=lambda j: int(j.get("seq") or 0),
        )
        if not queued:
            return None
        first = queued[0]
        return {
            "seq": first.get("seq"),
            "task_id": "",
            "user_id": first.get("user_id"),
            "group_id": first.get("group_id"),
            "kind": ((first.get("params") or {}).get("kind") or "t2i"),
            "prompt": first.get("prompt") or "",
            "trace_id": "",
            "channel": "queue",
            "state": ST_QUEUED,
            "started_at": first.get("enqueued_at") or _now(),
            "queued_behind": len(queued),
        }

    @staticmethod
    def _public(j: dict) -> dict:
        """脱敏后对外（不泄露内部字段）。"""
        return {
            "seq": j.get("seq"),
            "job_id": j.get("job_id"),
            "task_id": (j.get("task_id") or "")[:8],
            "user_id": j.get("user_id"),
            "group_id": j.get("group_id"),
            "scene": j.get("scene"),
            "prompt": j.get("prompt"),
            "params": j.get("params"),
            "state": j.get("state"),
            "batch_id": j.get("batch_id"),
            "batch_name": j.get("batch_name") or "",
            "plan_id": j.get("plan_id") or "",
            "scheduled_at": j.get("scheduled_at") or 0,
            "plan_index": j.get("plan_index"),
            "channel": j.get("channel"),
            "enqueued_at": j.get("enqueued_at"),
            "started_at": j.get("started_at"),
            "finished_at": j.get("finished_at"),
            "error": j.get("error"),
            "busy_retries": j.get("busy_retries"),
            "images": j.get("images") or [],
        }

    def recent(self, user_id: int | None = None, limit: int = 10) -> list[dict]:
        """最近任务（含终态）。传 user_id 则只看该用户的。"""
        pool = self._jobs + self._history
        if user_id is not None:
            pool = [j for j in pool if int(j.get("user_id") or 0) == int(user_id)]
        pool.sort(key=lambda j: float(j.get("enqueued_at") or 0), reverse=True)
        return [self._public(j) for j in pool[:limit]]

    def get(self, target: str) -> dict | None:
        j = self._find(target)
        return self._public(j) if j else None

    # ---------------- 消费者 ----------------
    async def start(self) -> None:
        """启动消费者（幂等：重复调用无害）。"""
        if self._running and self._task and not self._task.done():
            return
        # 若旧任务已死亡，先清理再重建，避免僵尸状态
        if self._task and self._task.done():
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._running = True
        self._task = asyncio.create_task(self._consume_loop(), name="qq-queue-consumer")
        _log().info("queue_consumer_started max=%d backoff=%.0fs retry=%d"
                    % (self.max_size, self.busy_backoff, self.busy_retry))

    async def ensure_consumer(self) -> bool:
        """自检：消费者任务死亡时自动重启。返回是否执行了重启。"""
        if self._task is None or self._task.done():
            _log().warning("queue_consumer_dead_restarting")
            await self.start()
            return True
        return False

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _consume_loop(self) -> None:
        """单消费者：取队首 → 提交 → 轮询 → 归档。任何异常都不能杀死循环。"""
        _log().info("queue_consume_loop_enter")
        while self._running:
            try:
                job = self._peek()
                _log().debug("queue_consume_tick queued=%s job=%s"
                             % (len([j for j in self._jobs if j.get("state") == ST_QUEUED]),
                                job.get("seq") if job else None))
                if job is None:
                    await asyncio.sleep(max(0.5, float(_cfg("QUEUE_POLL_INTERVAL", 2.0) or 2.0)))
                    continue
                _log().info("queue_consume_process seq=%s channel=%s"
                            % (job.get("seq"), (job.get("params") or {}).get("channel", "local")))
                await self._process(job)
                _log().info("queue_consume_done seq=%s state=%s"
                            % (job.get("seq"), job.get("state")))
            except asyncio.CancelledError:
                _log().info("queue_consume_cancelled")
                raise
            except Exception as e:  # noqa: BLE001 — 消费者永不因单任务异常退出
                _log().error("queue_consume_error error=%s detail=%s"
                             % (type(e).__name__, str(e)[:300]))
                await asyncio.sleep(3.0)
        _log().info("queue_consume_loop_exit")

    def _peek(self) -> dict | None:
        queued = [j for j in self._jobs if j.get("state") == ST_QUEUED]
        if not queued:
            return None
        queued.sort(key=lambda j: int(j.get("seq") or 0))
        return queued[0]

    async def _process(self, job: dict) -> None:
        """处理队首任务：按 kind / channel 分派（批量计划共用）。

        kind="upscale"（图片批量放大 · 2026-09-12）→ _process_upscale；
        其余按 channel 分派到本地/在线生图通道（向后兼容：无 kind = t2i）。
        """
        params = job.get("params") or {}
        kind = str(params.get("kind") or "").strip().lower()
        if kind == "upscale":
            await self._process_upscale(job)
            return
        channel = str(params.get("channel") or "").strip().lower()
        if channel == "online":
            await self._process_online(job)
        else:
            await self._process_local(job)

    async def _process_local(self, job: dict) -> None:
        """本地 ComfyUI 通道：提交 → 遇忙退避 → 轮询 → 归档。"""
        # ---- 1. 提交到平台 /qq/image-generate ----
        payload = {"prompt": job.get("prompt") or ""}
        payload.update(dict(job.get("params") or {}))
        data, err, busy = await self._submit(payload)

        if busy:
            job["busy_retries"] = int(job.get("busy_retries") or 0) + 1
            # 用户拍板：退避 QUEUE_BUSY_RETRY 次 × QUEUE_BUSY_BACKOFF 秒；
            # 用完后仍忙 → 通知用户并弹出队列。
            if job["busy_retries"] <= self.busy_retry:
                self._save()
                await self._notify("busy", job)
                _log().info("queue_busy_retry seq=%s attempt=%d/%d sleep=%.0fs"
                            % (job.get("seq"), job["busy_retries"],
                               self.busy_retry, self.busy_backoff))
                await asyncio.sleep(self.busy_backoff)
                return  # 保持队首，下一轮循环再试
            # 用完重试次数 → 失败出队
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = ("平台持续繁忙（本地 ComfyUI 一直被占用），已重试 %d 次共 %d 分钟后放弃。"
                            "多半是网页端正在跑图，等它跑完再投。"
                            % (job["busy_retries"], int(job["busy_retries"] * self.busy_backoff / 60)))
            await self._archive(job)
            self._save()
            await self._notify("failed", job)
            return

        if err:
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = err
            await self._archive(job)
            self._save()
            await self._notify("failed", job)
            return

        # ---- 2. 拿到 task_id → 标记 running，轮询直到终态 ----
        task_id = str((data or {}).get("task_id") or "")
        if not task_id:
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = "平台未返回 task_id（响应：%s）" % str(data)[:120]
            await self._archive(job)
            self._save()
            await self._notify("failed", job)
            return

        job["task_id"] = task_id
        job["state"] = ST_RUNNING
        job["started_at"] = _now()
        self._save()
        await self._notify("started", job)

        await self._wait_task(job)

    async def _submit(self, payload: dict) -> tuple[dict, str, bool]:
        """提交到主项目。返回 (data, error, is_busy)。

        is_busy=True 表示平台忙（409 IMG_BUSY），调用方应退避重试而非判失败。
        """
        try:
            data, err, status = await self._post("/qq/image-generate", payload)
        except Exception as e:  # noqa: BLE001
            return {}, "%s: %s" % (type(e).__name__, str(e)[:160]), False
        if err:
            # 忙闲判定：HTTP 409 或 错误码/文案含 IMG_BUSY 都算忙
            if status == 409 or "IMG_BUSY" in str(err).upper():
                return {}, err, True
            return {}, err, False
        return data or {}, "", False

    async def _wait_task(self, job: dict) -> None:
        """轮询平台任务直到终态（succeeded / failed / canceled）。"""
        tid = str(job.get("task_id") or "")
        timeout = float(_cfg("TASK_TIMEOUT", 600) or 600)
        interval = max(1.0, float(_cfg("POLL_INTERVAL", 3.0) or 3.0))
        deadline = _now() + timeout
        last_notify = 0.0

        while self._running and _now() < deadline:
            await asyncio.sleep(interval)
            try:
                data, err, _ = await self._get("/t2i/tasks/%s" % tid)
            except Exception as e:  # noqa: BLE001
                _log().warning("queue_poll_error task_id=%s error=%s" % (tid[:8], type(e).__name__))
                continue
            if err:
                continue
            # 兼容两种响应形态：主项目 /t2i/tasks/{id} 经 _unwrap 后是 {"task": {...}}
            # （routers.py t2i_task_detail → ok({"task": ...})），而 /qq/image-generate
            # 是 {"task_id": ...} 扁平体。此处统一下钻到真正的任务对象再读 status。
            # 历史 BUG：直接读 data["status"] 恒为空 → 轮询永远判不出终态 → 明明已出图
            # 也被拖满 TASK_TIMEOUT(7200s) 判「任务超时」（2026-09-15 排查）。
            td = data.get("task") if isinstance((data or {}).get("task"), dict) else (data or {})
            status = str(td.get("status") or "").lower()
            if status in ("succeeded", "success"):
                job["state"] = ST_DONE
                job["finished_at"] = _now()
                job["images"] = self._extract_images(td)
                await self._archive(job)
                self._save()
                await self._notify("done", job)
                return
            if status in ("failed", "canceled"):
                job["state"] = ST_FAILED
                job["finished_at"] = _now()
                job["error"] = str(td.get("error")
                                   or td.get("error_message")
                                   or ("任务被取消" if status == "canceled" else "生成失败"))[:200]
                await self._archive(job)
                self._save()
                await self._notify("failed", job)
                return
            # 进度通知节流：最多每 30s 一次，避免刷屏
            if _now() - last_notify > 30.0:
                last_notify = _now()
                pct = td.get("progress")
                if pct is not None:
                    await self._notify("progress", job)

        # 看门狗超时
        if self._running:
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = "任务超时（超过 %d 秒未出图）" % int(timeout)
            await self._archive(job)
            self._save()
            await self._notify("failed", job)

    @staticmethod
    def _abs_url(url: str) -> str:
        """相对路径 /file?name=xxx → 绝对 URL。

        主项目返回的图片地址常是 /file?name=t2i_xxx_0.png 这种相对路径，
        NapCat 收到后无法识别（retcode=1200「识别URL失败」），必须拼上
        MANJU_FILE_BASE。与 taskmgr._abs_url 同逻辑（2026-09-20 修复队列侧遗漏）。
        """
        s = (url or "").strip()
        if not s:
            return ""
        if s.startswith(("http://", "https://", "base64://", "file:///")):
            return s
        if s.startswith("/"):
            base = str(_cfg("MANJU_FILE_BASE", "http://127.0.0.1:7788")
                       or "http://127.0.0.1:7788").rstrip("/")
            return base + s
        return s

    @staticmethod
    def _img_url(x) -> str:
        """图片条目 → URL。兼容 dict 形态（本地 t2i 结果是 {url,width,height,...}）与纯字符串。"""
        if isinstance(x, dict):
            u = str(x.get("url") or x.get("src") or "")
        else:
            u = str(x)
        return TaskQueue._abs_url(u)

    @staticmethod
    def _extract_images(data: dict) -> list[str]:
        """从任务详情里抽图片 URL（兼容 result.images / images / url 三种形态）。"""
        out: list[str] = []
        res = (data or {}).get("result")
        if isinstance(res, dict):
            for k in ("images", "urls"):
                v = res.get(k)
                if isinstance(v, list):
                    out.extend([TaskQueue._img_url(x) for x in v if x])
            if res.get("url"):
                out.append(TaskQueue._abs_url(str(res["url"])))
        for k in ("images", "urls"):
            v = (data or {}).get(k)
            if isinstance(v, list):
                out.extend([TaskQueue._img_url(x) for x in v if x])
        if (data or {}).get("url"):
            out.append(TaskQueue._abs_url(str((data or {}).get("url"))))
        # 去重保序
        seen, uniq = set(), []
        for u in out:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    # ---------------- 在线云端通道（批量计划可走此通道）----------------
    async def _process_online(self, job: dict) -> None:
        """在线云端通道：POST /t2i/online/generate（同步返回图片，无需轮询任务）。

        与本地通道共用同一单消费者循环 → 批量计划内任务**依次执行**，互不并发。
        在线端点返回 {task_id, images, ...}，直接取到图即标 done。
        """
        job["state"] = ST_RUNNING
        job["started_at"] = _now()
        self._save()
        await self._notify("started", job)

        payload = self._build_online_payload(job)
        # 在线生图是同步长耗时接口，给足超时（CLOSE-05）
        data, err, _ = await self._post(
            "/t2i/online/generate", payload,
            timeout=float(_cfg("HTTP_TIMEOUT_IMG", 120) or 120),
        )
        if err:
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = err
            await self._archive(job)
            self._save()
            await self._notify("failed", job)
            return
        images = self._extract_images(data)
        job["state"] = ST_DONE
        job["finished_at"] = _now()
        job["images"] = images
        await self._archive(job)
        self._save()
        await self._notify("done", job)

    @staticmethod
    def _build_online_payload(job: dict) -> dict:
        """在线通道提交体（与 taskmgr._run_t2i 在线分支同字段）。"""
        params = job.get("params") or {}
        try:
            w = int(params.get("width") or _cfg("DEFAULT_WIDTH", 1024) or 1024)
        except (TypeError, ValueError):
            w = 1024
        try:
            h = int(params.get("height") or _cfg("DEFAULT_HEIGHT", 1024) or 1024)
        except (TypeError, ValueError):
            h = 1024
        try:
            cnt = int(params.get("count") or _cfg("DEFAULT_COUNT", 1) or 1)
        except (TypeError, ValueError):
            cnt = 1
        return {
            "prompt": job.get("prompt") or "",
            "negative": params.get("negative") or _cfg("DEFAULT_NEGATIVE_PROMPT", ""),
            "width": w,
            "height": h,
            "count": cnt,
            "seed": -1,
            "watermark": False,
        }

    # ---------------- 图片批量放大（2026-09-12 新增）----------------
    async def _process_upscale(self, job: dict) -> None:
        """放大任务：下载原图 → multipart POST /t2i/edit(mode=upscale) → 复用 _wait_task 轮询。

        与生图通道共用同一单消费者循环 → 放大任务依次执行，不与生图并发挤显存。
        /t2i/edit 异步入队立即返回 task_id（无 409 忙语义），本地独占性由本队列保证。
        失败路径全部落到 job["error"] 并通知（CLOSE-02 禁止静默吞）。
        """
        params = job.get("params") or {}
        src = str(params.get("image") or "").strip()
        if not src:
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = "放大任务缺少源图地址（params.image）"
            await self._archive(job)
            self._save()
            await self._notify("failed", job)
            return

        job["state"] = ST_RUNNING
        job["started_at"] = _now()
        self._save()
        await self._notify("started", job)

        # 1) 下载原图（/file?name=… 相对路径 → 主项目站点根；也可能是外部 URL）
        img_bytes, filename, err = await self._download_image(src)
        if err:
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = "下载原图失败：%s" % err
            await self._archive(job)
            self._save()
            await self._notify("failed", job)
            return

        # 2) multipart 提交放大（CLOSE-05 显式超时）
        fields = {
            "mode": "upscale",
            "scale": str(params.get("scale") or 2),
            "prompt": job.get("prompt") or "",
            "denoise": "0.0",
            "timeout": str(_platform_timeout()),
        }
        tpl = str(params.get("template_id") or "").strip()
        if tpl:
            fields["template_id"] = tpl
        data, err, _status = await self._post_multipart(
            "/t2i/edit", fields,
            files={"image": (filename, img_bytes)},
            timeout=max(60.0, float(_cfg("HTTP_TIMEOUT_IMG", 120) or 120)),
        )
        if err:
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = "提交放大失败：%s" % err
            await self._archive(job)
            self._save()
            await self._notify("failed", job)
            return

        task_id = str((data or {}).get("task_id") or "")
        if not task_id:
            job["state"] = ST_FAILED
            job["finished_at"] = _now()
            job["error"] = "平台未返回 task_id（响应：%s）" % str(data)[:120]
            await self._archive(job)
            self._save()
            await self._notify("failed", job)
            return

        job["task_id"] = task_id
        self._save()
        await self._wait_task(job)

    def _origin(self) -> str:
        """主项目站点根（api_base 去掉 /api/… 后缀）：/file?name=… 相对路径拼在这里。"""
        base = self.api_base
        cut = base.find("/api/")
        return base[:cut] if cut > 0 else base.rstrip("/")

    async def _download_image(self, src: str) -> tuple:
        """下载原图。src 支持 完整 URL / 相对路径（/file?name=…）/ 纯文件名。

        返回 (bytes, filename, error)；error 非空时 bytes 为空。
        """
        if src.startswith("http://") or src.startswith("https://"):
            url = src
        elif src.startswith("/"):
            url = self._origin() + src
        else:
            url = self._origin() + "/file?name=" + src
        filename = src.rsplit("/", 1)[-1].split("?")[0] or "image.png"
        if "." not in filename:
            filename += ".png"
        timeout = max(30.0, float(_cfg("HTTP_TIMEOUT_IMG", 120) or 120))
        try:
            async with self._client(timeout) as cli:
                resp = await cli.get(url)
        except Exception as e:  # noqa: BLE001
            return b"", filename, "%s: %s" % (type(e).__name__, str(e)[:160])
        if resp.status_code != 200 or not resp.content:
            return b"", filename, "HTTP %s（%s）" % (resp.status_code, url[:120])
        return resp.content, filename, ""

    async def _post_multipart(self, path: str, data: dict, files, timeout: float = 120.0):
        """multipart 上传提交（放大任务用）。返回 (data, error, status)，语义同 _request。

        上传体大，不做 3 次退避重试（重传整图得不偿失）；5xx 直接报错给调用方。
        """
        url = self.api_base + path
        try:
            async with self._client(timeout) as cli:
                resp = await cli.post(url, data=data, files=files)
        except Exception as e:  # noqa: BLE001
            return {}, "%s: %s" % (type(e).__name__, str(e)[:160]), 0
        try:
            j = resp.json()
        except Exception:
            j = {}
        if resp.status_code >= 400:
            return {}, self._err_of(j, resp.status_code), resp.status_code
        return self._unwrap(j), "", resp.status_code

    # ---------------- HTTP（CLOSE-05 显式超时 / REDUN-02 退避）----------------
    def _client(self, read_timeout: float):
        kw = {"timeout": httpx.Timeout(10.0, read=read_timeout)}
        if self._transport is not None:
            kw["transport"] = self._transport
        return httpx.AsyncClient(**kw)

    @staticmethod
    def _unwrap(j: dict) -> dict:
        """主项目统一信封 {code, data, message} → 取业务体。"""
        if not isinstance(j, dict):
            return {}
        d = j.get("data")
        return d if isinstance(d, dict) else j

    @staticmethod
    def _err_of(j: dict, status: int) -> str:
        if isinstance(j, dict):
            d = j.get("data")
            if isinstance(d, dict) and d.get("error"):
                return str(d["error"])[:200]
            for k in ("message", "detail", "error"):
                if j.get(k):
                    return str(j[k])[:200]
        return "HTTP %s" % status

    async def _request(self, method: str, path: str, timeout: float = 30.0, json_body: dict | None = None):
        url = self.api_base + path
        last = ({}, "", 0)
        for attempt in range(3):
            try:
                async with self._client(timeout) as cli:
                    resp = await cli.request(method, url, json=json_body)
                if resp.status_code in (500, 502, 503, 504) and attempt < 2:
                    raise httpx.HTTPError("HTTP %s" % resp.status_code)
                try:
                    j = resp.json()
                except Exception:
                    j = {}
                if 400 <= resp.status_code < 500:
                    return {}, self._err_of(j, resp.status_code), resp.status_code
                return self._unwrap(j), "", resp.status_code
            except httpx.HTTPError as e:
                last = ({}, str(e)[:160], 0)
                # 指数退避 1s/2s/4s + 抖动（REDUN-02）
                await asyncio.sleep(min(2 ** attempt, 4) + random.uniform(0, 0.4))
            except Exception as e:  # noqa: BLE001
                last = ({}, "%s: %s" % (type(e).__name__, str(e)[:160]), 0)
                break
        return last

    async def _post(self, path: str, payload: dict | None = None, timeout: float = 30.0):
        return await self._request("POST", path, timeout=timeout, json_body=payload or {})

    async def _get(self, path: str, timeout: float = 15.0):
        return await self._request("GET", path, timeout=timeout)

    # ---------------- 通知 ----------------
    async def _notify(self, event: str, job: dict) -> None:
        if not self.notifier:
            return
        try:
            await self.notifier(event, self._public(job))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 通知失败不能影响队列推进
            _log().warning("queue_notify_failed event=%s error=%s detail=%s"
                           % (event, type(e).__name__, str(e)[:160]))


# 模块级单例（插件入口直接用 queue_mgr；单测各自 new TaskQueue 隔离）
queue_mgr = TaskQueue()
