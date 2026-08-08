# -*- coding: utf-8 -*-
"""业务模块版本管理器 + 服务探针（服务管理器后端）。

职责：
1. 模块版本：以「源文件快照」方式，对指定业务页面/组件做 版本发布(snapshot) 与
   版本回退(rollback)；回退后自动重建前端产物 frontend/dist（闭环 CLOSE-01）。
2. 灰度开关：module_flags 控制某模块是否启用；前端导航守卫据此拦截已停用模块，
   实现「停用 / 上线」这类轻量回退。
3. 服务探针：探测 backend / frontend / comfyui / ollama 健康状态。
4. 服务控制：经 launcher.py 对受管服务(start/stop/restart)做进程级管理。

§08 纪律：所有异常显式抛出（不静默吞错），关键动作写日志。
"""
import os
import sys
import time
import shutil
import subprocess
import logging
import urllib.request
import asyncio

from db import get_conn, fetch_all, fetch_one, uid

logger = logging.getLogger("module_manager")

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SERVER_DIR)
VERSIONS_DIR = os.environ.get("MANJU_VERSIONS_DIR") or os.path.join(REPO_ROOT, "versions")
RUN_DIR = os.environ.get("MANJU_RUN_DIR") or os.path.join(REPO_ROOT, "run")
os.makedirs(VERSIONS_DIR, exist_ok=True)
os.makedirs(RUN_DIR, exist_ok=True)

NODE = r"C:/Users/L/.workbuddy/binaries/node/versions/22.22.2/node.exe"
VITE = os.path.join(REPO_ROOT, "node_modules", "vite", "bin", "vite.js")
DETACH = getattr(os, "DETACHED_PROCESS", 0) | getattr(os, "CREATE_NEW_PROCESS_GROUP", 0)

# 业务模块注册表：key -> { 名称, 源文件(相对 REPO_ROOT), 路由标记 }
MODULE_REGISTRY = {
    "fragment-editor": {
        "name": "分镜编排编辑器",
        "files": ["src/components/ProjectFragmentEditor.vue"],
    },
    "planning": {
        "name": "项目策划拆解",
        "files": ["src/components/ProjectPlanning.vue"],
    },
    "production": {
        "name": "生产生成 / 推理监控",
        "files": ["src/components/ProductionStage.vue", "src/components/TaskCenter.vue"],
    },
    "assets": {
        "name": "资产库（项目 / 全局）",
        "files": ["src/components/Assets.vue", "src/components/GlobalAssets.vue"],
    },
    "creative": {
        "name": "AI 创作（文生图 / 图生图 / 反推）",
        "files": ["src/components/Txt2Img.vue", "src/components/Img2Img.vue",
                  "src/components/Img2Prompt.vue"],
    },
    "canvas": {
        "name": "工作流画布",
        "files": ["src/components/Canvas.vue"],
    },
    "memoir": {
        "name": "人生回忆录",
        "files": ["src/components/MemoirDirectorBoard.vue", "src/components/MemoirList.vue"],
    },
}

# 服务探针定义（managed=True 表示可由本管理器启停）
SERVICE_PROBES = [
    {"name": "backend", "url": "http://127.0.0.1:7788/api/v1/health", "managed": True},
    {"name": "frontend", "url": "http://127.0.0.1:8099/", "managed": True},
    {"name": "comfyui", "url": "http://127.0.0.1:8188/", "managed": False},
    {"name": "ollama", "url": "http://127.0.0.1:11434/api/tags", "managed": False},
]


def _copy_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


# ----------------------------- 模块版本：快照 / 回退 -----------------------------
def snapshot_module(key, note=""):
    if key not in MODULE_REGISTRY:
        raise ValueError("UNKNOWN_MODULE:%s" % key)
    conn = get_conn()
    try:
        version = "v%s" % int(time.time())
        dest_dir = os.path.join(VERSIONS_DIR, key, version)
        saved = []
        for rel in MODULE_REGISTRY[key]["files"]:
            src = os.path.join(REPO_ROOT, rel)
            if not os.path.isfile(src):
                raise ValueError("MISSING_SOURCE:%s" % rel)
            _copy_file(src, os.path.join(dest_dir, rel))
            saved.append(rel)
        conn.execute("UPDATE module_versions SET is_current=0 WHERE module_key=?", (key,))
        conn.execute(
            "INSERT INTO module_versions (id, module_key, version, note, snapshot_path, is_current, created_at) "
            "VALUES (?,?,?,?,?,1,?)",
            (uid(), key, version, note or "", dest_dir, int(time.time())))
        # 发布即视为启用
        conn.execute(
            "INSERT INTO module_flags (module_key, enabled, updated_at) VALUES (?,1,?) "
            "ON CONFLICT(module_key) DO UPDATE SET enabled=1, updated_at=?",
            (key, int(time.time()), int(time.time())))
        conn.commit()
        logger.info("[MODULE] snapshot %s %s files=%s", key, version, saved)
        return {"module_key": key, "version": version, "note": note, "files": saved,
                "snapshot_path": dest_dir}
    finally:
        conn.close()


async def _run_frontend_build():
    """重建前端产物（回退后需重新打包才可能生效）。

    本机工作区 frontend/dist 受 WorkBuddy safe-delete 钩子限制，vite 无法清空/覆盖旧产物；
    若运行环境设置了 MANJU_FRONTEND_DIR，则构建到该目录，与 web_app 服务目录保持一致，
    实现回退→重建→即时生效的闭环（CLOSE-01）。未设置时回退到默认 frontend/dist。

    注意：vite build 耗时十余秒，必须用 asyncio.to_thread 避免阻塞 FastAPI 事件循环。
    """
    if not os.path.isfile(VITE):
        return {"ok": False, "reason": "vite 未安装（node_modules 缺失）"}
    out_dir = os.environ.get("MANJU_FRONTEND_DIR")
    cmd = [NODE, VITE, "build"]
    if out_dir:
        cmd += ["--outDir", out_dir]
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd, cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=420)
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "out_dir": out_dir or os.path.join(REPO_ROOT, "frontend", "dist"),
                "stdout_tail": (proc.stdout or "")[-1500:],
                "stderr_tail": (proc.stderr or "")[-1500:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "前端构建超时(>420s)"}
    except Exception as e:  # 构建失败不应吞掉文件回退结果
        return {"ok": False, "reason": str(e)}


async def rollback_module(key, version):
    if key not in MODULE_REGISTRY:
        raise ValueError("UNKNOWN_MODULE:%s" % key)
    conn = get_conn()
    try:
        row = fetch_one(conn,
            "SELECT * FROM module_versions WHERE module_key=? AND version=?",
            (key, version))
        if not row:
            raise ValueError("VERSION_NOT_FOUND:%s@%s" % (key, version))
        snap = row["snapshot_path"]
        if not os.path.isdir(snap):
            raise ValueError("SNAPSHOT_MISSING:%s" % snap)
        restored = []
        for rel in MODULE_REGISTRY[key]["files"]:
            src = os.path.join(snap, rel)
            if not os.path.isfile(src):
                raise ValueError("SNAPSHOT_FILE_MISSING:%s" % rel)
            _copy_file(src, os.path.join(REPO_ROOT, rel))
            restored.append(rel)
        build = await _run_frontend_build()
        conn.execute("UPDATE module_versions SET is_current=0 WHERE module_key=?", (key,))
        conn.execute("UPDATE module_versions SET is_current=1 WHERE id=?", (row["id"],))
        conn.execute(
            "INSERT INTO module_flags (module_key, enabled, updated_at) VALUES (?,1,?) "
            "ON CONFLICT(module_key) DO UPDATE SET enabled=1, updated_at=?",
            (key, int(time.time()), int(time.time())))
        conn.commit()
        logger.info("[MODULE] rollback %s -> %s files=%s build_ok=%s", key, version, restored, build.get("ok"))
        return {"module_key": key, "version": version, "restored": restored,
                "build": build, "enabled": True}
    finally:
        conn.close()


def list_modules():
    conn = get_conn()
    try:
        rows = fetch_all(conn, "SELECT * FROM module_versions ORDER BY created_at DESC")
        flags = {r["module_key"]: bool(r["enabled"]) for r in fetch_all(conn, "SELECT * FROM module_flags")}
        out = []
        for key, meta in MODULE_REGISTRY.items():
            mrows = [r for r in rows if r["module_key"] == key]
            current = next((r for r in mrows if r["is_current"]), None)
            history = [{"version": r["version"], "note": r["note"], "created_at": r["created_at"],
                        "is_current": bool(r["is_current"])} for r in mrows]
            out.append({
                "key": key,
                "name": meta["name"],
                "files": meta["files"],
                "enabled": flags.get(key, True),
                "current_version": current["version"] if current else None,
                "current_note": current["note"] if current else None,
                "history": history,
            })
        return out
    finally:
        conn.close()


def set_enabled(key, enabled):
    if key not in MODULE_REGISTRY:
        raise ValueError("UNKNOWN_MODULE:%s" % key)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO module_flags (module_key, enabled, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(module_key) DO UPDATE SET enabled=?, updated_at=?",
            (key, 1 if enabled else 0, int(time.time()), 1 if enabled else 0, int(time.time())))
        conn.commit()
        return {"module_key": key, "enabled": bool(enabled)}
    finally:
        conn.close()


def get_flags():
    conn = get_conn()
    try:
        rows = fetch_all(conn, "SELECT * FROM module_flags")
        return {r["module_key"]: bool(r["enabled"]) for r in rows}
    finally:
        conn.close()


# ----------------------------- 服务探针 / 控制 -----------------------------
def probe_services():
    result = []
    for svc in SERVICE_PROBES:
        t0 = time.time()
        status = "down"
        latency = None
        try:
            req = urllib.request.Request(svc["url"], method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status < 500:
                    status = "up"
            latency = round((time.time() - t0) * 1000, 1)
        except Exception:
            status = "down"
        result.append({
            "name": svc["name"],
            "url": svc["url"],
            "managed": svc["managed"],
            "status": status,
            "latency_ms": latency,
            "checked_at": int(time.time() * 1000),
        })
    return result


def control_service(name, action):
    """经 launcher.py 对受管服务做 start / stop / restart。"""
    if name not in ("backend", "frontend"):
        raise ValueError("BAD_SERVICE:%s" % name)
    if action not in ("start", "stop", "restart"):
        raise ValueError("BAD_ACTION:%s" % action)
    launcher = os.path.join(SERVER_DIR, "launcher.py")
    if not os.path.isfile(launcher):
        raise ValueError("LAUNCHER_MISSING")
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, launcher, action, name],
        cwd=SERVER_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=DETACH, close_fds=True)
    return {"name": name, "action": action, "triggered": True, "launcher_pid": proc.pid}
