# -*- coding: utf-8 -*-
"""taskmgr（任务编排，设计文档 §3.4 / §7.1 / §7.4）。

职责：把 intent 产出的路由提交到平台后端 7788，跟踪进度，产出媒体地址。

输入：Route + QQEvent + 会话
输出：{"ok": bool, "kind": str, "images": [...], "video_url": str, "error": str}

关键接口：run(route, ev, sess) / _poll_task(task_id) / watchdog

设计要点：
  * **只经 HTTP 调用主项目**（单向依赖铁律），不 import 任何 server/ 模块；
  * CLOSE-05：外部调用显式超时（图像 120s / 视频 600s / 其它 30s）；
  * REDUN-02：5xx 与网络异常指数退避 1s/2s/4s + 抖动，最多 3 次；**4xx 不重试**；
  * §7.1：本地 ComfyUI 通道独占 → 全局串行锁；在线通道云端并发，不加锁；
  * OPS-04：任务最长 30min 看门狗，超时判 failed 并回传"生成超时"。
"""
from __future__ import annotations

import asyncio
import random
import time

import httpx

from ..config import Config, get_logger, jlog
from ..dispatcher import QQEvent
from ..intent import Route
from ..session import Session

log = get_logger()

# 任务终态（平台约定：成功是 succeeded，不是 success）
TERMINAL_OK = "succeeded"
TERMINAL_FAIL = ("failed", "canceled")

_RETRY_STATUS = (500, 502, 503, 504)


def _unwrap(j: dict) -> dict:
    """平台统一信封 {code, data, message} → 取业务体。兼容裸字典返回。"""
    if not isinstance(j, dict):
        return {}
    data = j.get("data")
    if isinstance(data, dict):
        return data
    return j


def _pick_error(j: dict, resp: httpx.Response) -> str:
    if isinstance(j, dict):
        d = j.get("data")
        if isinstance(d, dict) and d.get("error"):
            return str(d["error"])[:200]
        if j.get("message"):
            return str(j["message"])[:200]
        if j.get("detail"):
            return str(j["detail"])[:200]
    return "HTTP %s" % resp.status_code


def _abs_url(url: str) -> str:
    """平台返回的 /file?name=xxx 是相对路径，NapCat 侧无法解析 → 拼成绝对地址。"""
    s = (url or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://", "base64://", "file:///")):
        return s
    if s.startswith("/"):
        return Config.MANJU_FILE_BASE + s
    return s


class TaskMgr:
    def __init__(
        self,
        api_base: str | None = None,
        channel: str | None = None,
        transport: "httpx.AsyncBaseTransport | None" = None,
    ) -> None:
        self.api_base = (api_base or Config.MANJU_API_BASE).rstrip("/")
        self.channel = (channel or Config.IMAGE_CHANNEL or "online").strip().lower()
        # 测试注入点：传 httpx.MockTransport 即可免起真实后端做端到端逻辑验证
        self._transport = transport
        # §7.1：本地 ComfyUI 同时只跑 1 个任务 → 全局串行
        self._local_lock = asyncio.Lock()
        # 每用户并发 ≤1（§3.4 验收）
        self._running_users: set = set()
        # 2026-09-07：全局任务簿（key=task_id），给 /status 查询使用。
        # 之前 sess.task_id 字段存在但**全代码 0 处写入**——导致 /status 永远回"闲着呢"，
        # 即使旁观者肉眼可见任务还在跑（trace=... 已发）。簿由 _run_t2i / _run_i2i
        # 本地分支在拿到 task_id 后 add，try/finally 兜底 pop；在线通道同步返回不入簿
        # （任务瞬间结束，无"进行中"语义）。簿独立于 sess.task_id：旁观者查不到别人的
        # sess，但能查到这个簿（get_any_active）。
        self._current_tasks: dict[str, dict] = {}

    def _client(self, read_timeout: float, follow_redirects: bool = False) -> httpx.AsyncClient:
        kw = {
            "timeout": httpx.Timeout(10.0, read=read_timeout),
            "follow_redirects": follow_redirects,
        }
        if self._transport is not None:
            kw["transport"] = self._transport
        return httpx.AsyncClient(**kw)

    # ---------------- HTTP 基础（REDUN-02 / CLOSE-05）----------------
    async def _request(self, method: str, path: str, **kw) -> tuple[dict, str]:
        """返回 (业务体, error)。4xx 不重试；5xx/网络异常退避重试 ≤3 次。"""
        url = self.api_base + path
        timeout = kw.pop("timeout", None) or Config.HTTP_TIMEOUT_DEFAULT
        last_err = ""
        for attempt in range(3):
            try:
                async with self._client(read_timeout=timeout) as cli:
                    resp = await cli.request(method, url, **kw)
                if resp.status_code in _RETRY_STATUS and attempt < 2:
                    last_err = "HTTP %s" % resp.status_code
                    raise httpx.HTTPError(last_err)
                try:
                    j = resp.json()
                except Exception:
                    j = {}
                if resp.status_code >= 400:
                    return {}, _pick_error(j, resp)
                body = _unwrap(j)
                code = j.get("code") if isinstance(j, dict) else 0
                if code not in (0, 200, None) and isinstance(body, dict) and body.get("error"):
                    return {}, str(body["error"])[:200]
                return body, ""
            except httpx.HTTPError as e:
                last_err = "%s: %s" % (type(e).__name__, str(e)[:160])
                if attempt < 2:
                    wait = (2 ** attempt) + random.uniform(0, 0.5)
                    log.warning(jlog(
                        "taskmgr_retry", path=path, attempt=attempt + 1,
                        wait=round(wait, 2), error=last_err,
                    ))
                    await asyncio.sleep(wait)
                else:
                    log.error(jlog("taskmgr_http_failed", path=path, error=last_err))
                    return {}, last_err
            except Exception as e:  # 不吞异常，记录后上抛语义化错误（CLOSE-02）
                last_err = "%s: %s" % (type(e).__name__, str(e)[:160])
                log.error(jlog("taskmgr_request_error", path=path, error=last_err))
                return {}, last_err
        return {}, last_err or "未知错误"

    # ---------------- 源图获取（图生图）----------------
    async def _fetch_image(self, url: str) -> tuple[bytes, str]:
        """下载用户发来的图片。带显式超时（CLOSE-05）。"""
        try:
            async with self._client(read_timeout=30.0, follow_redirects=True) as cli:
                resp = await cli.get(url)
            if resp.status_code >= 400:
                return b"", "源图下载失败：HTTP %s" % resp.status_code
            if not resp.content:
                return b"", "源图为空"
            return resp.content, ""
        except Exception as e:
            return b"", "源图下载异常：%s" % type(e).__name__

    # ---------------- 任务轮询（本地异步通道）----------------
    async def _poll_task(self, task_id: str, trace_id: str = "") -> tuple[dict, str]:
        """轮询 /t2i/tasks/{task_id} 直到终态或看门狗超时（OPS-04）。"""
        deadline = asyncio.get_running_loop().time() + Config.TASK_TIMEOUT
        interval = Config.POLL_INTERVAL
        last_progress = -1
        while True:
            body, err = await self._request("GET", "/t2i/tasks/%s" % task_id)
            if err:
                # 轮询失败不立即判死，继续重试直到看门狗到期
                log.warning(jlog("taskmgr_poll_error", task_id=task_id, error=err, trace_id=trace_id))
            else:
                task = body.get("task") or body
                status = str(task.get("status") or "")
                progress = int(task.get("progress") or 0)
                if progress != last_progress:
                    last_progress = progress
                    log.info(jlog("taskmgr_progress", task_id=task_id, status=status, progress=progress))
                if status == TERMINAL_OK:
                    return task, ""
                if status in TERMINAL_FAIL:
                    return task, str(task.get("error_message") or "任务失败")
            if asyncio.get_running_loop().time() > deadline:
                log.error(jlog("taskmgr_watchdog_timeout", task_id=task_id, trace_id=trace_id))
                return {}, "生成超时（超过 %d 秒）" % Config.TASK_TIMEOUT
            await asyncio.sleep(interval)

    def _images_from(self, task: dict) -> list:
        result = task.get("result") or {}
        images = result.get("images") or []
        out = []
        for it in images:
            if isinstance(it, dict):
                u = it.get("url") or it.get("local_url") or ""
            else:
                u = str(it)
            u = _abs_url(u)
            if u:
                out.append(u)
        return out

    # ---------------- 主入口 ----------------
    async def run(self, route: Route, ev: QQEvent, sess: Session, forced_channel: str | None = None) -> dict:
        """执行路由。返回 {ok, kind, images, video_url, error}。

        forced_channel: 生图通道交互式选择场景下用户显式指定的通道
        （"local"/"online"），覆盖 Config.IMAGE_CHANNEL 默认值。
        """
        kind = route.kind
        out = {"ok": False, "kind": kind, "images": [], "video_url": "", "error": ""}

        if ev.user_id in self._running_users:
            out["error"] = "你有一个任务正在进行中，请等它完成后再提交。"
            return out
        self._running_users.add(ev.user_id)
        try:
            if kind == "t2i":
                return await self._run_t2i(route, ev, sess, out, channel=forced_channel)
            if kind == "i2i":
                return await self._run_i2i(route, ev, sess, out)
            if kind == "video":
                return await self._run_video(route, ev, out)
            out["error"] = "未支持的路由类型：%s" % kind
            return out
        finally:
            self._running_users.discard(ev.user_id)

    # ---------------- 本地通道（§7.1：平台本地 ComfyUI 独占）----------------
    async def _wait_local_idle(self, trace_id: str = "") -> str:
        """提交前等平台上其它本地任务跑完（含前端页面提交的），返回错误串（空=可提交）。

        后端 /t2i/generate 入队时会把其它进行中任务置 cancelled 并中断 ComfyUI
        （routers.py t2i_generate），QQ 侧若不等就会与前端页面互相打断，反之亦然。
        端点不存在/查询失败时不阻塞（降级为不排队），保证老后端也能用。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0, Config.LOCAL_QUEUE_TIMEOUT)
        while True:
            body, err = await self._request("GET", "/t2i/active-task?source=local")
            task = (body or {}).get("task")
            if err or not task:
                return ""
            if loop.time() > deadline:
                log.error(jlog("local_queue_timeout", trace_id=trace_id))
                return "平台本地 ComfyUI 正忙，等了 %d 秒还没空出来，稍后再试。" % Config.LOCAL_QUEUE_TIMEOUT
            log.info(jlog(
                "local_queue_wait", task_id=str(task.get("task_id") or "")[:16],
                status=str(task.get("status") or ""), trace_id=trace_id,
            ))
            await asyncio.sleep(max(0.5, Config.LOCAL_QUEUE_INTERVAL))

    @staticmethod
    def _local_t2i_body(prompt: str) -> dict:
        """本地文生图提交体（与主项目文生图页同端点：POST /api/v1/t2i/generate）。

        关键：配了模板时 steps / cfg / 尺寸**缺省不下发**，由后端取模板自带值
        （routers.py t2i_generate：请求里 steps<=0、cfg<=0 或未给尺寸即用模板值）。
        蒸馏类模板自带 steps=8、cfg=1.0，硬套 SD1.5 的 20/7.0 会采样发散出废图。
        未配模板（走后端内置流程）时才显式下发兜底安全值。
        """
        body = {
            "prompt": prompt,
            "negative": Config.LOCAL_NEGATIVE_PROMPT,
            "count": max(1, int(Config.DEFAULT_COUNT)),
            "seed": random.randint(1, 2 ** 31 - 1),
            "style": Config.LOCAL_STYLE,
            "enhance": bool(Config.LOCAL_ENHANCE),
            "orchestrator": Config.LOCAL_ORCHESTRATOR,
        }
        steps = int(Config.LOCAL_STEPS)
        cfg = float(Config.LOCAL_CFG)
        width = int(Config.LOCAL_WIDTH)
        height = int(Config.LOCAL_HEIGHT)

        if Config.LOCAL_TEMPLATE_ID:
            body["template_id"] = Config.LOCAL_TEMPLATE_ID
            if steps > 0:
                body["steps"] = steps
            if cfg > 0:
                body["cfg"] = cfg
            if width > 0 and height > 0:
                body["width"] = width
                body["height"] = height
            return body

        # 无模板：必须显式给值，否则后端默认 1024x576（6GB 显存有 OOM 风险）
        body["steps"] = steps or 20
        body["cfg"] = cfg or 7.0
        body["width"] = width or 512
        body["height"] = height or 512
        return body

    @staticmethod
    def _local_i2i_form(payload: dict, prompt: str) -> dict:
        """本地图生图表单：与后端 POST /t2i/edit 的 Form 字段一一对应。"""
        # 图生图端点（/t2i/edit）没有「模板优先」逻辑，steps/cfg 一律取表单值，
        # 因此这里必须显式下发；Config 为 0（跟随模板）时按内置流程给安全兜底值。
        form = {
            "mode": payload.get("mode") or "img2img",
            "prompt": prompt,
            "negative": Config.LOCAL_NEGATIVE_PROMPT,
            "steps": str(int(Config.LOCAL_STEPS) or 20),
            "cfg": str(float(Config.LOCAL_CFG) or 7.0),
            "seed": str(random.randint(1, 2 ** 31 - 1)),
            "strength": str(payload.get("strength") or Config.LOCAL_STRENGTH),
        }
        for k in ("left", "top", "right", "bottom", "scale"):
            if payload.get(k) is not None:
                form[k] = str(payload[k])
        if Config.LOCAL_TEMPLATE_ID:
            form["template_id"] = Config.LOCAL_TEMPLATE_ID
        return form

    async def _submit_local_t2i(self, prompt: str, trace_id: str = "") -> tuple[dict, str]:
        """提交本地生图：一律走 QQ 专用端点。

        ⚠️ 不回退到 /t2i/generate（页面端点）：页面端点无「模板缺失即报错」约束，
        无模板时会退化为 SD1.5 内置流程出废图。QQ 通道必须配有效模板，拿不到就直接把
        端点返回的业务错误透传给用户（含 409 忙 / 422 无模板 / 404 模板不存在）。
        """
        payload = self._local_t2i_body(prompt)
        body, err = await self._request(
            "POST", "/qq/image-generate", json=payload,
            timeout=Config.HTTP_TIMEOUT_DEFAULT,
        )
        return body, err

    # ---- 文生图 ----
    async def _run_t2i(self, route: Route, ev: QQEvent, sess: Session, out: dict, channel: str | None = None) -> dict:
        prompt = route.payload.get("prompt") or ""
        ch = (channel or self.channel).strip().lower()
        if ch == "local":
            async with self._local_lock:  # §7.1 串行
                # 先等平台上其它本地任务（含前端页面提交的）跑完，避免互相取消
                qerr = await self._wait_local_idle(ev.trace_id)
                if qerr:
                    out["error"] = qerr
                    return out
                body, err = await self._submit_local_t2i(prompt, ev.trace_id)
                if err:
                    out["error"] = err
                    return out
                task_id = body.get("task_id") or ""
                if not task_id:
                    out["error"] = "平台未返回 task_id"
                    return out
                # 2026-09-07：写全局任务簿 + sess.task_id，让 /status 能查到"在跑"
                # 旁观者查不到别人的 sess.task_id，但能查到 _current_tasks；
                # 簿与 sess 双写，簿是权威（不依赖会话是否还在），sess.task_id 是发起人快捷通道。
                #
                # 注意：直接属性赋值 + touch，**不**用 sess.set(...)：
                # Session 自身没有 set 方法（只有 SessionStore.set），__init__.py 现存
                # 754/758/821 三处 `sess.set(ev.user_id, ...)` 在用户没触发 /mode 时是死代码
                # 但触发会 AttributeError——本次修复不踩这个坑。后续统一时把 Session 加
                # set(patch) 并把这三处迁过去是更好的形态。
                self._current_tasks[task_id] = {
                    "task_id": task_id,
                    "user_id": ev.user_id,
                    "group_id": ev.group_id,
                    "kind": "t2i",
                    "prompt": prompt,
                    "trace_id": ev.trace_id,
                    "channel": "local",
                    "started_at": time.time(),
                }
                sess.task_id = task_id
                sess.last_prompt = prompt
                sess.touch()
                try:
                    task, perr = await self._poll_task(task_id, ev.trace_id)
                    if perr:
                        out["error"] = perr
                        return out
                    out["images"] = self._images_from(task)
                finally:
                    # 成功/失败/超时/cancelled 都清簿、清 sess（避免残留假阳性"在跑"）
                    self._current_tasks.pop(task_id, None)
                    if sess.task_id == task_id:
                        sess.task_id = None
                        sess.touch()
        else:
            body, err = await self._request(
                "POST", "/t2i/online/generate",
                json={
                    "prompt": prompt,
                    "negative": Config.DEFAULT_NEGATIVE_PROMPT,
                    "width": Config.DEFAULT_WIDTH,
                    "height": Config.DEFAULT_HEIGHT,
                    "count": Config.DEFAULT_COUNT,
                    "seed": random.randint(1, 2 ** 31 - 1),  # 随机种子：每次出图不同且可复现
                    "watermark": False,  # 火山/通用平台默认加水印，显式关掉
                },
                timeout=Config.HTTP_TIMEOUT_IMG,
            )
            if err:
                out["error"] = err
                return out
            out["images"] = [_abs_url(x) for x in self._images_of(body)]

        out["ok"] = bool(out["images"])
        if not out["ok"] and not out["error"]:
            out["error"] = "平台未返回图片"
        log.info(jlog("taskmgr_t2i_done", ok=out["ok"], n=len(out["images"]),
                      channel=ch, trace_id=ev.trace_id))
        return out

    # ---- 图生图 ----
    async def _run_i2i(self, route: Route, ev: QQEvent, sess: Session, out: dict) -> dict:
        payload = dict(route.payload or {})
        prompt = payload.get("prompt") or ""

        # 源图优先取本条消息带的图，其次取会话暂存（§3.8）
        src = ev.images[0] if ev.images else (sess.pending_image or "")
        if not src:
            out["error"] = "请先发送一张图片，再告诉我怎么改。"
            return out
        img_bytes, ferr = await self._fetch_image(src)
        if ferr:
            out["error"] = ferr
            return out

        if self.channel == "local":
            async with self._local_lock:
                qerr = await self._wait_local_idle(ev.trace_id)
                if qerr:
                    out["error"] = qerr
                    return out
                form = self._local_i2i_form(payload, prompt)
                files = {"image": ("source.png", img_bytes, "image/png")}
                body, err = await self._request(
                    "POST", "/t2i/edit", data=form, files=files,
                    timeout=Config.HTTP_TIMEOUT_DEFAULT,
                )
                if err:
                    out["error"] = err
                    return out
                task_id = body.get("task_id") or ""
                if not task_id:
                    out["error"] = "平台未返回 task_id"
                    return out
                # 2026-09-07：同 _run_t2i 写簿（见上方注释）
                self._current_tasks[task_id] = {
                    "task_id": task_id,
                    "user_id": ev.user_id,
                    "group_id": ev.group_id,
                    "kind": "i2i",
                    "prompt": prompt,
                    "trace_id": ev.trace_id,
                    "channel": "local",
                    "started_at": time.time(),
                }
                sess.task_id = task_id
                sess.last_prompt = prompt
                sess.touch()
                try:
                    task, perr = await self._poll_task(task_id, ev.trace_id)
                    if perr:
                        out["error"] = perr
                        return out
                    out["images"] = self._images_from(task)
                finally:
                    self._current_tasks.pop(task_id, None)
                    if sess.task_id == task_id:
                        sess.task_id = None
                        sess.touch()
        else:
            files = {"image": ("source.png", img_bytes, "image/png")}
            data = {
                "prompt": prompt,
                "negative": Config.DEFAULT_NEGATIVE_PROMPT,
                "strength": str(payload.get("strength") or Config.DEFAULT_STRENGTH),
                "seed": str(random.randint(1, 2 ** 31 - 1)),
                "watermark": "false",  # 火山等平台去水印（表单字段，字符串）
            }
            body, err = await self._request(
                "POST", "/t2i/online/edit", data=data, files=files,
                timeout=Config.HTTP_TIMEOUT_IMG,
            )
            if err:
                out["error"] = err
                return out
            out["images"] = [_abs_url(x) for x in self._images_of(body)]

        out["ok"] = bool(out["images"])
        if not out["ok"] and not out["error"]:
            out["error"] = "平台未返回图片"
        log.info(jlog("taskmgr_i2i_done", ok=out["ok"], n=len(out["images"]),
                      mode=payload.get("mode") or "img2img", trace_id=ev.trace_id))
        return out

    # ---- 视频 ----
    async def _run_video(self, route: Route, ev: QQEvent, out: dict) -> dict:
        """§7.4 方案 B：POST /api/v1/qq/video-generate（需后端实现）。

        诚实处理：该端点目前**尚未在主项目实现**，404 时明确回传"未开通"，
        不假装能生成、也不绕过积分/安全直调底层（CLOSE-04）。
        """
        payload = dict(route.payload or {})
        model = Config.DEFAULT_VIDEO_MODEL
        if model:
            payload.setdefault("model", model)
        body, err = await self._request(
            "POST", "/qq/video-generate", json=payload,
            timeout=Config.HTTP_TIMEOUT_VIDEO,
        )
        if err:
            if "HTTP 404" in err or "Not Found" in err:
                out["error"] = "视频能力尚未开通：主项目后端需先实现 /api/v1/qq/video-generate（设计文档 §7.4 方案 B）。"
            else:
                out["error"] = err
            return out
        video_url = _abs_url(
            (body.get("video_url") or body.get("url")
             or (body.get("result") or {}).get("video_url") or "")
        )
        if not video_url:
            out["error"] = "平台未返回视频地址"
            return out
        out["ok"] = True
        out["video_url"] = video_url
        log.info(jlog("taskmgr_video_done", ok=True, trace_id=ev.trace_id))
        return out

    # ---------------- 2026-09-07：/status 查询接口 ----------------
    def get_active_for(self, user_id: int) -> dict | None:
        """查询指定用户当前进行中的任务（任意 kind）。无返回 None。

        簿里有多个就返回最早 add 的（按 started_at 升序，先来先查），保证结果稳定。
        """
        if not self._current_tasks:
            return None
        items = sorted(self._current_tasks.values(), key=lambda x: x.get("started_at", 0.0))
        for info in items:
            if info.get("user_id") == user_id:
                return dict(info)  # 拷一份避免被外部改
        return None

    def get_any_active(self, exclude_user_id: int | None = None) -> dict | None:
        """查询任意进行中的任务（最近一个），旁观者用。

        exclude_user_id：传"发起人自己的 user_id"则排除自己后选下一个，避免发起人问进度
        拿到"别人的任务"（语义上"自己的"应优先于"别人的"——但调用方应先调 get_active_for，
        这里 exclude 主要是兜底防呆）。
        """
        if not self._current_tasks:
            return None
        items = sorted(self._current_tasks.values(), key=lambda x: -x.get("started_at", 0.0))
        for info in items:
            if exclude_user_id is None or info.get("user_id") != exclude_user_id:
                return dict(info)
        return None

    @staticmethod
    def _images_of(body: dict) -> list:
        """在线端点返回 {task_id, images:[{url}|str]}。"""
        images = (body or {}).get("images") or []
        out = []
        for it in images:
            if isinstance(it, dict):
                u = it.get("url") or it.get("local_url") or ""
            else:
                u = str(it)
            if u:
                out.append(u)
        return out


task_mgr = TaskMgr()
