#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 漫剧工坊 v2 —— 参考 updream / 小云雀 的项目式 AI 视频工作台
零依赖(仅 Python 标准库)。底层调用 manju_agent 的两阶段接口(plan / render)。

页面:
  /projects             项目列表(小云雀式卡片网格)
  /project/{id}         项目工作台(updream 式三栏: 镜头列表 / 剧本分镜编辑 / 预览)
  /project/{id}/assets  素材库(角色/场景图网格 + 提示词浮层)
  /project/{id}/canvas  工作流画布(ComfyUI 同款 LiteGraph 节点编辑器)

启动:  python web_app.py
访问:  http://localhost:8099
前置: Ollama(11434) + 秋叶 ComfyUI(8188) + ffmpeg(on PATH) 已运行。
"""
import os, json, time, threading, urllib.parse, urllib.request, email, shutil
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging

# 统一日志目录：D:\AI\logs（所有应用日志都落在这里，绝不放 C 盘）
# 可用 MANJU_LOG_DIR 覆盖（与后端一致，指向工作区外可写目录）。
_AI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # D:\AI
LOG_DIR = os.environ.get("MANJU_LOG_DIR") or os.path.join(_AI_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
_log_path = os.path.join(LOG_DIR, "web_app.log")
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")
_fileh = logging.FileHandler(_log_path, encoding="utf-8"); _fileh.setFormatter(_fmt)
_streamh = logging.StreamHandler(); _streamh.setFormatter(_fmt)
_root = logging.getLogger(); _root.setLevel(logging.INFO)
if not _root.handlers:
    _root.addHandler(_fileh); _root.addHandler(_streamh)
logger = logging.getLogger("web_app")

API_V1_HOST = "http://127.0.0.1:7788"
from manju_agent import (BASE, extract_docx, parse_scenes_from_tables,
                         parse_scenes_from_text, _extract_title, plan_script,
                         render_video, list_checkpoints, list_motion_models,
                         list_ipadapter_models, list_clip_vision, CKPT, MOTION_MODEL)

HOST, PORT = "0.0.0.0", 8099
OUTPUTS = os.path.join(BASE, "outputs")
PROJECTS_DIR = os.path.join(BASE, "projects")
UPLOADS_DIR = os.path.join(BASE, ".uploads")
# 前端产物目录：默认 frontend/dist；可用 MANJU_FRONTEND_DIR 覆盖
# （工作区 dist 被宿主锁定不可覆盖时，构建到工作区外再指向此处）。
FRONTEND_DIR = os.environ.get("MANJU_FRONTEND_DIR") or os.path.join(BASE, "frontend", "dist")

os.makedirs(OUTPUTS, exist_ok=True)
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

_lock = threading.Lock()
_tasks = {}  # task_id -> state dict


# ----------------------------- 项目数据存储 -----------------------------
class ProjectStore:
    """本地 JSON 文件存储项目。"""
    @staticmethod
    def _path(pid):
        return os.path.join(PROJECTS_DIR, "%s.json" % pid)

    @staticmethod
    def _now():
        return int(time.time() * 1000)

    @classmethod
    def list(cls):
        items = []
        for fn in sorted(os.listdir(PROJECTS_DIR)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(PROJECTS_DIR, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    prj = json.load(f)
                if prj.get("deleted"):
                    continue
                items.append(cls._summary(prj))
            except Exception:
                pass
        return sorted(items, key=lambda x: x.get("updated_at", 0), reverse=True)

    @classmethod
    def _summary(cls, prj):
        scenes = prj.get("scenes") or []
        cover = None
        run_dir = prj.get("run_dir")
        if run_dir:
            for name in ("final.mp4", "scene_01.mp4", "scene_01.png"):
                cp = os.path.join(OUTPUTS, run_dir, name)
                if os.path.isfile(cp):
                    cover = "/file?run=%s&name=%s" % (run_dir, name)
                    break
        return {
            "id": prj["id"],
            "title": prj.get("title", "未命名项目"),
            "status": prj.get("status", "draft"),
            "scene_count": len(scenes),
            "cover": cover,
            "created_at": prj.get("created_at"),
            "updated_at": prj.get("updated_at"),
        }

    @classmethod
    def create(cls, title="未命名项目", script_text="", mode="simple"):
        pid = "%d" % cls._now()
        prj = {
            "id": pid,
            "title": title,
            "script_text": script_text,
            "script_path": "",
            "mode": mode,
            "scenes": [],
            "refs": [],
            "assets": {"characters": [], "scenes": [], "props": []},
            "status": "draft",
            "run_dir": "",
            "video_url": "",
            "ckpt": CKPT,
            "animated": True,
            "use_ip": False,
            "canvas_state": None,
            "characters": [
                {"id": "c1", "name": "主角", "relation": "主人公", "age": "青年", "notes": "", "refs": []},
            ],
            "chapters": [
                {"id": "ch1", "title": "片段 01", "active": True},
            ],
            "segments": [],
            "story": {"setting": "", "timeline": "", "notes": ""},
            "model_config": {
                "model": "Seedance 2.0 Mini",
                "resolution": "720p",
                "ratio": "16:9",
                "duration": 12,
                "audio": True,
                "seed": -1,
            },
            "output_params": {
                "format": "mp4",
                "fps": 24,
                "codec": "H.264",
                "quality": "标准",
            },
            "palette": [
                {"name": "主色", "color": "#3a5bd9"},
                {"name": "辅色1", "color": "#d9a23a"},
                {"name": "辅色2", "color": "#c23ad9"},
                {"name": "肤色", "color": "#e8c4a2"},
            ],
            "anchors": [],
            "checks": {
                "character_photo": False,
                "storyboard": False,
                "director_guide": False,
                "video_clip": False,
            },
            "created_at": cls._now(),
            "updated_at": cls._now(),
        }
        cls.save(prj)
        return prj

    @classmethod
    def get(cls, pid):
        p = cls._path(pid)
        if not os.path.isfile(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            prj = json.load(f)
        if prj.get("deleted"):
            return None
        return prj

    @classmethod
    def save(cls, prj):
        prj["updated_at"] = cls._now()
        with open(cls._path(prj["id"]), "w", encoding="utf-8") as f:
            json.dump(prj, f, ensure_ascii=False, indent=2)

    @classmethod
    def delete(cls, pid):
        prj = cls.get(pid)
        if prj:
            prj["deleted"] = True
            cls.save(prj)

    @classmethod
    def update(cls, pid, **fields):
        prj = cls.get(pid)
        if not prj:
            return None
        allowed = {
            "title", "script_text", "mode", "scenes", "refs", "assets",
            "status", "run_dir", "video_url", "ckpt", "animated", "use_ip",
            "canvas_state", "characters", "chapters", "segments", "story",
            "model_config", "output_params", "checks", "palette", "anchors",
            "cover"
        }
        for k, v in fields.items():
            if k in allowed:
                prj[k] = v
        cls.save(prj)
        return prj


# ----------------------------- 任务系统 -----------------------------
def _new_task(project_id=None):
    tid = "%010d" % int(time.time() * 1000)
    with _lock:
        _tasks[tid] = {
            "stage": "queued", "message": "任务已排队，即将开始…",
            "current": 0, "total": 0, "images": [], "video": None,
            "run_dir": None, "done": False, "error": None, "animated": False,
            "title": None, "scenes": None, "project_id": project_id,
        }
    return tid


def _progress(tid, stage, msg, cur, tot):
    with _lock:
        t = _tasks.get(tid)
        if not t:
            return
        t["stage"] = stage
        t["message"] = msg
        t["current"] = cur
        t["total"] = tot
        if stage == "image" and cur > 0 and t["run_dir"]:
            if t.get("animated"):
                url = None
            else:
                name = "scene_%02d.png" % cur
                url = "/file?run=%s&name=%s" % (t["run_dir"], name)
            card = {"url": url, "caption": msg}
            if not any(i["caption"] == card["caption"] for i in t["images"]):
                t["images"].append(card)
        if stage == "done":
            if t["run_dir"]:
                # 实际成片可能是 mp4 或 mov，按存在性选择
                for cand in ("final.mp4", "final.mov"):
                    fp = os.path.join(OUTPUTS, t["run_dir"], cand)
                    if os.path.isfile(fp):
                        t["video"] = "/file?run=%s&name=%s" % (t["run_dir"], cand)
                        break
            t["done"] = True
            # 同步写回项目状态
            if t.get("project_id"):
                prj = ProjectStore.get(t["project_id"])
                if prj:
                    prj["status"] = "done" if t["run_dir"] else "draft"
                    prj["run_dir"] = t["run_dir"] or prj.get("run_dir", "")
                    if t.get("video"):
                        prj["video_url"] = t["video"]
                        if not prj.get("cover"):
                            prj["cover"] = t["video"]
                    ProjectStore.save(prj)
        if stage == "error":
            t["done"] = True
            if t.get("project_id"):
                prj = ProjectStore.get(t["project_id"])
                if prj:
                    prj["status"] = "error"
                    ProjectStore.save(prj)


def _resolve_size(model_config):
    """根据 分辨率+比例 计算 ComfyUI 出图尺寸(向下取整到 64 倍数)。"""
    mc = model_config or {}
    res = str(mc.get("resolution", "720p")).replace("p", "")
    ratio = str(mc.get("ratio", "16:9"))
    try:
        base = int(res)
    except Exception:
        base = 720
    table = {
        "16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1),
    }
    rw, rh = table.get(ratio, (16, 9))
    if rw == rh:
        w = h = base
    elif rw > rh:
        w = base
        h = int(round(base * rh / rw))
    else:
        h = base
        w = int(round(base * rw / rh))
    w = max(64, (w // 64) * 64)
    h = max(64, (h // 64) * 64)
    return w, h


def _resolve_codec_fmt(output_params):
    op = output_params or {}
    fmt = str(op.get("format", "mp4")).lower()
    codec = str(op.get("codec", "H.264")).upper().replace(".", "")
    codec_map = {"H264": "libx264", "H265": "libx265", "HEVC": "libx265",
                 "H.264": "libx264", "H.265": "libx265"}
    return codec_map.get(codec, "libx264"), (fmt if fmt in ("mp4", "mov") else "mp4")


def _start_plan(tid, project_id, text, mode, script_text, script_path):
    try:
        _progress(tid, "plan", "正在解析剧本 / 拆分镜头（AI 推理中，请稍候）…", 0, 1)

        def _on_chunk(partial):
            # 把 Ollama 流式输出实时透传给前端，避免「卡住」错觉
            _progress(tid, "plan", "AI 正在生成分镜与提示词…\n" + partial[-500:], 0, 1)

        title, scenes, meta = plan_script(
            text, mode, script_text or None, script_path or None, on_chunk=_on_chunk)
        with _lock:
            if tid in _tasks:
                _tasks[tid]["title"] = title
                _tasks[tid]["scenes"] = scenes
        # 保存到项目
        prj = ProjectStore.get(project_id)
        if prj:
            prj["title"] = title or prj.get("title", "未命名项目")
            # 镜头归属到第一个章节
            ch_id = (prj.get("chapters") or [{"id": "ch1"}])[0]["id"]
            for i, sc in enumerate(scenes, 1):
                sc["chapter_id"] = ch_id
                sc.setdefault("shot_type", "中景")
                sc.setdefault("camera_move", "固定")
                sc.setdefault("thumb", "")
            prj["scenes"] = scenes
            if meta.get("characters"):
                prj["characters"] = meta["characters"]
            if meta.get("palette"):
                prj["palette"] = meta["palette"]
            if meta.get("story"):
                prj["story"] = meta["story"]
            if meta.get("anchors"):
                prj["anchors"] = meta["anchors"]
            prj["status"] = "draft"
            ProjectStore.save(prj)
        _progress(tid, "done", "分镜与提示词已生成，共 %d 镜" % len(scenes), 1, 1)
    except Exception as e:
        _progress(tid, "error", str(e), 0, 0)


def _start_render(tid, project_id, title, scenes, ckpt, animated, use_ip, ref_files):
    prj = ProjectStore.get(project_id) or {}
    mc = prj.get("model_config", {}) or {}
    op = prj.get("output_params", {}) or {}
    width, height = _resolve_size(mc)
    fps = int(op.get("fps", 24) or 24)
    codec, fmt = _resolve_codec_fmt(op)
    duration_total = sum(int(s.get("duration", 4)) for s in scenes)

    def on_start(outdir):
        with _lock:
            if tid in _tasks:
                _tasks[tid]["run_dir"] = os.path.basename(outdir)
        p = ProjectStore.get(project_id)
        if p:
            p["status"] = "rendering"
            ProjectStore.save(p)
    try:
        with _lock:
            if tid in _tasks:
                _tasks[tid]["animated"] = bool(animated)
        outdir, manifest = render_video(
            title, scenes, ref_files=ref_files, ckpt=ckpt,
            animated=animated, use_ip=use_ip,
            width=width, height=height, fps=fps, codec=codec, fmt=fmt,
            on_progress=lambda s, m, c, t: _progress(tid, s, m, c, t),
            on_start=on_start)
        # 渲染完成后：生成图回流素材库 + 场景缩略图 + 封面
        run = os.path.basename(outdir)
        assets = prj.get("assets", {"characters": [], "scenes": [], "props": []})
        if "scenes" not in assets:
            assets["scenes"] = []
        new_assets = []
        scene_index = 1
        for sc, m in zip(scenes, manifest.get("scenes", [])):
            media = m.get("media", "")
            if not media:
                continue
            url = "/file?run=%s&name=%s" % (run, media)
            sc["thumb"] = url
            kind = m.get("kind", "image")
            new_assets.append({
                "name": "镜头%d" % scene_index,
                "url": url,
                "prompt": sc.get("prompt", ""),
                "caption": sc.get("caption", ""),
                "kind": kind,
                "created_at": int(time.time() * 1000),
            })
            scene_index += 1
        # 合并：替换旧的场景素材，保留角色/道具
        assets["scenes"] = new_assets
        p = ProjectStore.get(project_id)
        if p:
            p["assets"] = assets
            p["scenes"] = scenes
            # 用首镜作为封面
            if new_assets:
                p["cover"] = new_assets[0]["url"]
            ProjectStore.save(p)
    except Exception as e:
        _progress(tid, "error", str(e), 0, 0)


# ----------------------------- HTTP Handler -----------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _parse_multipart(self):
        ctype = self.headers.get("Content-Type", "")
        out = {"fields": {}, "files": {}}
        if not ctype.startswith("multipart/form-data"):
            return out
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        head = ("Content-Type: " + ctype + "\r\nMIME-Version: 1.0\r\n").encode("utf-8")
        msg = BytesParser(policy=default).parsebytes(head + raw)
        for p in msg.walk():
            if p.is_multipart():
                continue
            cd = p.get("Content-Disposition", "") or ""
            name = None
            fn = p.get_filename()
            import re as _re
            m = _re.search(r'name="([^"]+)"', cd)
            if m:
                name = m.group(1)
            if not name:
                continue
            data = p.get_payload(decode=True) or b""
            if fn:
                out["files"].setdefault(name, []).append((fn, data))
            else:
                out["fields"][name] = data.decode("utf-8", "ignore").strip()
        return out

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            return {}

    def _proxy_v1(self):
        if not self.path.startswith("/api/v1"):
            return False
        url = API_V1_HOST + self.path
        try:
            body = None
            cl = self.headers.get("Content-Length")
            if cl:
                body = self.rfile.read(int(cl))
            req = urllib.request.Request(url, data=body, method=self.command,
                                         headers={h: self.headers[h] for h in self.headers if h.lower() not in ("host", "content-length")})
            with urllib.request.urlopen(req, timeout=600) as resp:
                self.send_response(resp.status)
                for h, v in resp.headers.items():
                    if h.lower() not in ("transfer-encoding", "content-length", "connection"):
                        self.send_header(h, v)
                data = resp.read()
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            data = e.read()
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send(502, json.dumps({"error": "api_v1 proxy error", "detail": str(e)}, ensure_ascii=False))
        return True

    def do_GET(self):
        if self._proxy_v1(): return
        u = urllib.parse.urlparse(self.path)
        p = u.path
        # 静态资源优先（Vite 构建产物在 /assets/）
        if p.startswith("/static/"):
            self._serve_static(p[len("/static/"):])
            return
        if p.startswith("/assets/"):
            self._serve_static("assets/" + p[len("/assets/"):])
            return
        if p.startswith("/uploads/"):
            self._serve_uploads(p[len("/uploads/"):])
            return
        # API
        if p == "/api/projects":
            self._send(200, json.dumps(ProjectStore.list(), ensure_ascii=False))
        elif p.startswith("/api/projects/"):
            self._api_projects_get(u.path)
        elif p == "/api/models":
            self._send(200, json.dumps({
                "checkpoints": list_checkpoints(),
                "default": CKPT if CKPT in list_checkpoints() else (list_checkpoints()[0] if list_checkpoints() else ""),
                "animatediff": len(list_motion_models()) > 0,
                "ipadapter": (len(list_ipadapter_models()) > 0 and len(list_clip_vision()) > 0),
                "motion_model": MOTION_MODEL,
            }, ensure_ascii=False))
        elif p == "/api/status":
            q = urllib.parse.parse_qs(u.query)
            tid = (q.get("task_id") or [""])[0]
            with _lock:
                t = _tasks.get(tid)
                if not t:
                    self._send(404, json.dumps({"error": "未知任务"}))
                    return
                data = dict(t)
            self._send(200, json.dumps(data, ensure_ascii=False))
        elif p == "/file":
            self._serve_file(u.query)
        else:
            # SPA history 回退: 一律返回 index.html
            self._serve_index()

    def _api_projects_get(self, path):
        parts = path.split("/")
        pid = parts[3]
        prj = ProjectStore.get(pid)
        if not prj:
            self._send(404, json.dumps({"error": "项目不存在"}))
            return
        self._send(200, json.dumps(prj, ensure_ascii=False))

    def do_POST(self):
        if self._proxy_v1(): return
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/projects":
            body = self._read_json()
            prj = ProjectStore.create(title=body.get("title", "未命名项目"),
                                      script_text=body.get("script_text", ""),
                                      mode=body.get("mode", "simple"))
            self._send(200, json.dumps(prj, ensure_ascii=False))
            return

        if not u.path.startswith("/api/projects/"):
            if u.path == "/api/dify/run":
                self._dify_run()
                return
            self._send(404, json.dumps({"error": "not found"}))
            return

        parts = u.path.split("/")
        if len(parts) < 4:
            self._send(404, json.dumps({"error": "not found"}))
            return
        pid = parts[3]
        action = parts[4] if len(parts) > 4 else ""

        if action == "parse_script":
            self._parse_script(pid)
        elif action == "plan":
            self._plan(pid)
        elif action == "render":
            self._render(pid)
        elif action == "refs":
            self._upload_refs(pid)
        elif action == "assets":
            self._upload_assets(pid)
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_PUT(self):
        if self._proxy_v1(): return
        u = urllib.parse.urlparse(self.path)
        if not u.path.startswith("/api/projects/"):
            self._send(404, json.dumps({"error": "not found"}))
            return
        parts = u.path.split("/")
        if len(parts) < 4:
            self._send(404, json.dumps({"error": "not found"}))
            return
        pid = parts[3]
        body = self._read_json()
        prj = ProjectStore.update(pid, **body)
        if not prj:
            self._send(404, json.dumps({"error": "项目不存在"}))
            return
        self._send(200, json.dumps(prj, ensure_ascii=False))

    def do_PATCH(self):
        if self._proxy_v1(): return
        self._send(404, json.dumps({"error": "not found"}))

    def do_DELETE(self):
        if self._proxy_v1(): return
        u = urllib.parse.urlparse(self.path)
        if not u.path.startswith("/api/projects/"):
            self._send(404, json.dumps({"error": "not found"}))
            return
        parts = u.path.split("/")
        if len(parts) < 4:
            self._send(404, json.dumps({"error": "not found"}))
            return
        pid = parts[3]
        if len(parts) >= 5 and parts[4] == "refs":
            idx = int(parts[5]) if len(parts) > 5 else -1
            self._delete_ref(pid, idx)
            return
        ProjectStore.delete(pid)
        self._send(200, json.dumps({"ok": True}, ensure_ascii=False))

    # ---------------- 业务方法 ----------------
    def _parse_script(self, pid):
        mp = self._parse_multipart()
        files = mp["files"]
        prj = ProjectStore.get(pid)
        if not prj:
            self._send(404, json.dumps({"error": "项目不存在"}))
            return

        script_text = ""
        title = prj.get("title", "")
        scenes = []
        for fn, data in files.get("file", []):
            safe = "".join(c for c in fn if c.isalnum() or c in "._-") or "script.txt"
            p = os.path.join(UPLOADS_DIR, "%d_%s" % (int(time.time() * 1000), safe))
            with open(p, "wb") as fh:
                fh.write(data)
            prj["script_path"] = p
            if safe.lower().endswith(".docx"):
                try:
                    script_text, scenes = extract_docx(p)
                    title = _extract_title(script_text) or title
                except Exception as e:
                    self._send(500, json.dumps({"error": "docx 解析失败: %s" % e}))
                    return
            else:
                script_text = data.decode("utf-8", "ignore")
                title = _extract_title(script_text) or title
                scenes = parse_scenes_from_text(script_text) or []
            break

        prj["script_text"] = script_text
        if title:
            prj["title"] = title
        if scenes:
            prj["scenes"] = scenes
        ProjectStore.save(prj)
        self._send(200, json.dumps({
            "title": title, "script_text": script_text, "scenes": scenes
        }, ensure_ascii=False))

    def _plan(self, pid):
        mp = self._parse_multipart()
        fld = mp["fields"]
        prj = ProjectStore.get(pid)
        if not prj:
            self._send(404, json.dumps({"error": "项目不存在"}))
            return
        text = fld.get("text", "").strip()
        mode = fld.get("mode", prj.get("mode", "simple"))
        script_path = prj.get("script_path", "")
        script_text = prj.get("script_text", "")
        if not text and not script_text and not script_path:
            self._send(400, json.dumps({"error": "请提供主题/剧本"}))
            return
        prj["status"] = "planning"
        ProjectStore.save(prj)
        tid = _new_task(project_id=pid)
        th = threading.Thread(target=_start_plan, args=(tid, pid, text, mode, script_text, script_path), daemon=True)
        th.start()
        self._send(200, json.dumps({"task_id": tid}, ensure_ascii=False))

    def _render(self, pid):
        mp = self._parse_multipart()
        fld = mp["fields"]
        prj = ProjectStore.get(pid)
        if not prj:
            self._send(404, json.dumps({"error": "项目不存在"}))
            return
        try:
            scenes = json.loads(fld.get("scenes", "[]"))
        except Exception:
            scenes = []
        if not isinstance(scenes, list) or not scenes:
            self._send(400, json.dumps({"error": "缺少分镜数据"}))
            return
        ckpt = fld.get("ckpt", prj.get("ckpt", CKPT)) or CKPT
        animated = fld.get("animated", "0") == "1"
        use_ip = fld.get("use_ip", "0") == "1"
        title = prj.get("title", "漫剧")
        # 参考图转绝对路径
        ref_files = []
        for url in prj.get("refs", []):
            # /file?run=...&name=... 或 /uploads/...
            if url.startswith("/uploads/"):
                ref_files.append(os.path.join(BASE, url[1:]))
            elif url.startswith("/file?"):
                q = urllib.parse.parse_qs(url[6:])
                run = (q.get("run") or [""])[0]
                name = (q.get("name") or [""])[0]
                if run and name:
                    ref_files.append(os.path.join(OUTPUTS, run, name))
        prj["status"] = "rendering"
        prj["ckpt"] = ckpt
        prj["animated"] = animated
        prj["use_ip"] = use_ip
        ProjectStore.save(prj)
        tid = _new_task(project_id=pid)
        th = threading.Thread(target=_start_render, args=(tid, pid, title, scenes, ckpt, animated, use_ip, ref_files), daemon=True)
        th.start()
        self._send(200, json.dumps({"task_id": tid}))

    def _upload_refs(self, pid):
        mp = self._parse_multipart()
        prj = ProjectStore.get(pid)
        if not prj:
            self._send(404, json.dumps({"error": "项目不存在"}))
            return
        refs = prj.get("refs", [])
        for fn, data in mp["files"].get("images", []):
            safe = "".join(c for c in fn if c.isalnum() or c in "._-") or "ref.png"
            name = "%d_%s" % (int(time.time() * 1000), safe)
            p = os.path.join(UPLOADS_DIR, name)
            with open(p, "wb") as fh:
                fh.write(data)
            refs.append("/uploads/%s" % name)
        prj["refs"] = refs
        ProjectStore.save(prj)
        self._send(200, json.dumps({"refs": refs}, ensure_ascii=False))

    def _delete_ref(self, pid, idx):
        prj = ProjectStore.get(pid)
        if not prj:
            self._send(404, json.dumps({"error": "项目不存在"}))
            return
        refs = prj.get("refs", [])
        if 0 <= idx < len(refs):
            refs.pop(idx)
        prj["refs"] = refs
        ProjectStore.save(prj)
        self._send(200, json.dumps({"refs": refs}, ensure_ascii=False))

    def _upload_assets(self, pid):
        mp = self._parse_multipart()
        fld = mp["fields"]
        prj = ProjectStore.get(pid)
        if not prj:
            self._send(404, json.dumps({"error": "项目不存在"}))
            return
        asset_type = fld.get("type", "scenes")
        assets = prj.get("assets", {"characters": [], "scenes": []})
        if asset_type not in assets:
            asset_type = "scenes"
        for fn, data in mp["files"].get("images", []):
            safe = "".join(c for c in fn if c.isalnum() or c in "._-") or "asset.png"
            name = "%d_%s" % (int(time.time() * 1000), safe)
            p = os.path.join(UPLOADS_DIR, name)
            with open(p, "wb") as fh:
                fh.write(data)
            assets[asset_type].append({
                "name": safe.rsplit(".", 1)[0],
                "url": "/uploads/%s" % name,
                "prompt": "",
                "created_at": int(time.time() * 1000)
            })
        prj["assets"] = assets
        ProjectStore.save(prj)
        self._send(200, json.dumps({"assets": assets}, ensure_ascii=False))

    # ---------------- 文件服务 ----------------
    def _serve_file(self, query):
        q = urllib.parse.parse_qs(query)
        run = (q.get("run") or [""])[0]
        name = (q.get("name") or [""])[0]
        client = self.headers.get("User-Agent", "-")
        if not name or ".." in name or "/" in name or "\\" in name:
            logger.warning("[FILE] 400 bad name=%s run=%s ua=%s" % (name, run, client))
            self._send(400, json.dumps({"error": "非法路径"}))
            return
        if run:
            if ".." in run or "/" in run or "\\" in run:
                logger.warning("[FILE] 400 bad run=%s name=%s ua=%s" % (run, name, client))
                self._send(400, json.dumps({"error": "非法路径"}))
                return
            path = os.path.join(OUTPUTS, run, name)
        else:
            # 文生图 /file?name=t2i_xxx.png 直接落在 outputs/ 根目录
            path = os.path.join(OUTPUTS, name)
            if not os.path.isfile(path):
                path = os.path.join(UPLOADS_DIR, name)
            # 后端 upload-image 等端点以 server/ 为 BASE 写入 server/outputs/（或 server/.uploads/），
            # 与 web_app 的 BASE（项目根）不一致，兜底兼容这些路径。
            if not os.path.isfile(path):
                path = os.path.join(BASE, "server", "outputs", name)
            if not os.path.isfile(path):
                path = os.path.join(BASE, "server", ".uploads", name)
        if not os.path.isfile(path):
            logger.warning("[FILE] 404 name=%s run=%s tried=%s ua=%s" % (name, run, path, client))
            self._send(404, json.dumps({"error": "文件不存在"}))
            return
        logger.info("[FILE] 200 name=%s run=%s path=%s bytes=%d" % (
            name, run, path, os.path.getsize(path)))
        ctype = "video/mp4" if name.lower().endswith(".mp4") else (
                "image/png" if name.lower().endswith(".png") else (
                "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "application/octet-stream"))
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                part = rng[6:].split(",")[0].strip()
                start_s, end_s = part.split("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
                length = end - start + 1
                with open(path, "rb") as f:
                    f.seek(start)
                    chunk = f.read(length)
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(chunk)
                return
            except Exception:
                pass
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    # ---------------- SPA 静态服务 ----------------
    _CT = {
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".map": "application/json",
    }

    def _serve_index(self):
        idx = os.path.join(FRONTEND_DIR, "index.html")
        if not os.path.isfile(idx):
            self._send(500, json.dumps({"error": "index.html 不存在, 请检查 frontend 目录"}))
            return
        with open(idx, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, rel):
        rel = rel.replace("\\", "/")
        if ".." in rel or rel.startswith("/"):
            self._send(400, json.dumps({"error": "非法路径"}))
            return
        fp = os.path.normpath(os.path.join(FRONTEND_DIR, rel))
        base = os.path.normpath(FRONTEND_DIR)
        if not fp.startswith(base) or not os.path.isfile(fp):
            self._send(404, json.dumps({"error": "文件不存在"}))
            return
        ext = os.path.splitext(fp)[1].lower()
        ctype = self._CT.get(ext, "application/octet-stream")
        try:
            with open(fp, "rb") as f:
                data = f.read()
        except Exception:
            self._send(404, json.dumps({"error": "文件不存在"}))
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _serve_uploads(self, rel):
        """服务用户上传的参考图/素材（.uploads/）。"""
        rel = rel.replace("\\", "/")
        if ".." in rel or rel.startswith("/"):
            self._send(400, json.dumps({"error": "非法路径"}))
            return
        fp = os.path.normpath(os.path.join(UPLOADS_DIR, rel))
        base = os.path.normpath(UPLOADS_DIR)
        if not fp.startswith(base) or not os.path.isfile(fp):
            self._send(404, json.dumps({"error": "文件不存在"}))
            return
        ext = os.path.splitext(fp)[1].lower()
        ctype = self._CT.get(ext, "application/octet-stream")
        try:
            with open(fp, "rb") as f:
                data = f.read()
        except Exception:
            self._send(404, json.dumps({"error": "文件不存在"}))
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _dify_run(self):
        """兼容旧版 Dify 入口。"""
        try:
            body = self._read_json()
            script = (body.get("script") or "").strip()
            mode = body.get("mode", "simple")
            if mode not in ("simple", "full"):
                mode = "simple"
            scenes_in = body.get("scenes")
            ckpt = body.get("ckpt") or CKPT
            animated = bool(body.get("animated", False))
            use_ip = bool(body.get("use_ip", False))
            title = body.get("title") or "漫剧"
            if isinstance(scenes_in, list) and scenes_in:
                scenes = scenes_in
            else:
                if not script:
                    self._send(400, json.dumps({"error": "请提供 script 或 scenes"}))
                    return
                title, scenes, meta = plan_script(script, mode, script, None)
            if not scenes:
                self._send(400, json.dumps({"error": "未生成分镜"}))
                return
            outdir = os.path.join(BASE, "outputs", "dify_%s" % time.strftime("%Y%m%d_%H%M%S"))
            render_video(title, scenes, outdir=outdir, ref_files=None,
                         ckpt=ckpt, animated=animated, use_ip=use_ip,
                         on_progress=lambda *a: None, on_start=None)
            final = os.path.join(outdir, "final.mp4")
            if not os.path.isfile(final):
                self._send(500, json.dumps({"error": "视频文件未生成"}))
                return
            video_url = "http://localhost:8099/file?run=%s&name=final.mp4" % os.path.basename(outdir)
            self._send(200, json.dumps({
                "title": title, "scenes": scenes,
                "video_url": video_url, "run_dir": os.path.basename(outdir)
            }, ensure_ascii=False))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    logger.info(">> AI 漫剧工坊 v2 已启动: http://localhost:%d (logs -> %s)" % (PORT, _log_path))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        logger.info(">> 已停止")


if __name__ == "__main__":
    main()
