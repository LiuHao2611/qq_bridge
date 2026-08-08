#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能短剧 AI 创作平台 - 服务启动控制台
自包含 Python 脚本，仅依赖标准库。

启动：python service_console.py
访问：http://localhost:8098

功能：
  - 实时检测后端(7788)、前端(8099)、ComfyUI(8188)、Ollama(11434)、ffmpeg 状态
  - 一键启动/停止后端和前端服务
  - 健康检查（前端页面 / 后端API / 代理链路）
  - 重建前端构建产物
  - 配置信息展示与命令复制
  - 常见问题排查表
"""

import http.server
import json
import socket
import subprocess
import os
import sys
import threading
import time
import webbrowser
from urllib.parse import urlparse
from urllib.request import urlopen
from urllib.error import URLError

# ============================================================
# 配置
# ============================================================

PORT = 8098
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 优先使用受管 venv（已装 fastapi/uvicorn），回退到受管 python
VENV_PY = r"C:\Users\L\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
MANAGED_PY = r"C:\Users\L\.workbuddy\binaries\python\versions\3.13.12\python.exe"
PYTHON_EXE = VENV_PY if os.path.exists(VENV_PY) else MANAGED_PY

NODE_EXE = r"C:\Users\L\.workbuddy\binaries\node\versions\22.22.2\node.exe"
NPM_CLI = r"C:\Users\L\.workbuddy\binaries\node\versions\22.22.2\node_modules\npm\bin\npm-cli.js"
VITE_JS = None  # 动态检测

FRONTEND_DIR = r"C:\Users\L\AppData\Local\Temp\manju_dist_fixed"
LOG_DIR = r"C:\Users\L\AppData\Local\Temp\manju_logs"
DB_PATH = r"C:\Users\L\AppData\Local\Temp\manju_app_snapshot.db"
RUN_DIR = r"C:\Users\L\AppData\Local\Temp\manju_run"

SERVICES = [
    {
        "id": "backend",
        "name": "后端 API",
        "desc": "server/main.py (uvicorn :7788)",
        "port": 7788,
        "health_url": "http://127.0.0.1:7788/docs",
        "type": "core",
        "start_cmd": 'cd /d/AI/manju_agent/server && MANJU_DB_PATH="{db}" MANJU_LOG_DIR="{ld}" MANJU_RUN_DIR="{rd}" "{py}" main.py',
        "start_cwd": os.path.join(PROJECT_ROOT, "server"),
        "start_args": ["main.py"],
        "env": {"MANJU_DB_PATH": DB_PATH, "MANJU_LOG_DIR": LOG_DIR, "MANJU_RUN_DIR": RUN_DIR},
        "win_title": "manju-backend",
    },
    {
        "id": "frontend",
        "name": "前端网关",
        "desc": "web_app.py (:8099, 反代 /api/v1 -> 7788)",
        "port": 8099,
        "health_url": "http://127.0.0.1:8099/",
        "type": "core",
        "start_cmd": 'cd /d/AI/manju_agent && MANJU_FRONTEND_DIR="{fd}" MANJU_LOG_DIR="{ld}" "{py}" web_app.py',
        "start_cwd": PROJECT_ROOT,
        "start_args": ["web_app.py"],
        "env": {"MANJU_FRONTEND_DIR": FRONTEND_DIR, "MANJU_LOG_DIR": LOG_DIR},
        "win_title": "manju-frontend",
    },
    {
        "id": "comfyui",
        "name": "ComfyUI",
        "desc": "外部依赖 - 图像渲染 (:8188)",
        "port": 8188,
        "health_url": "http://127.0.0.1:8188/",
        "type": "dependency",
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "desc": "外部依赖 - 剧本推理 (qwen2.5:7b, :11434)",
        "port": 11434,
        "health_url": "http://127.0.0.1:11434/api/tags",
        "type": "dependency",
    },
    {
        "id": "ffmpeg",
        "name": "ffmpeg",
        "desc": "外部依赖 - 视频合成 (需在 PATH)",
        "port": None,
        "health_url": None,
        "type": "dependency",
    },
]

HEALTH_CHECKS = [
    {"id": "frontend", "label": "前端页面", "url": "http://127.0.0.1:8099/", "expect": 200},
    {"id": "backend", "label": "后端 API", "url": "http://127.0.0.1:7788/docs", "expect": 200},
    {"id": "proxy", "label": "代理链路", "url": "http://127.0.0.1:8099/api/v1/health", "expect": 200},
]

TROUBLESHOOTING = [
    {"symptom": "启动 web_app.py 报 PermissionError: D:\\AI\\logs\\web_app.log", "cause": "日志文件被其他进程锁住", "solution": "启动时带 MANJU_LOG_DIR 指向 Temp 私有目录"},
    {"symptom": "访问 8099 报 500 index.html 不存在", "cause": "Temp 前端产物被系统清理", "solution": "点击「重建前端」按钮，然后刷新浏览器"},
    {"symptom": "页面能打开但数据加载失败 / 一直转圈", "cause": "后端 7788 没起来，或代理不通", "solution": "确认后端进程在跑；点击「健康检查」探测"},
    {"symptom": "前端改了代码不生效", "cause": "没重建，或浏览器缓存", "solution": "重建前端 + 浏览器硬刷新 Ctrl+Shift+R"},
    {"symptom": "vite build 报找不到 node / vite", "cause": "用了系统 node 而非受管 node", "solution": "必须用受管 node: C:/Users/L/.workbuddy/binaries/node/versions/22.22.2/node.exe"},
    {"symptom": "端口被占用 (Address already in use)", "cause": "上次进程没退干净", "solution": "点击对应服务的「停止」按钮，或用 netstat + taskkill"},
    {"symptom": "出图/出视频失败", "cause": "ComfyUI(8188) 或 Ollama(11434) 没开", "solution": "先启动外部服务，确认模型 qwen2.5:7b 已拉取"},
]

CONFIG_INFO = [
    {"label": "Python 运行时", "value": PYTHON_EXE, "copyable": True},
    {"label": "Node.js 运行时", "value": NODE_EXE, "copyable": True},
    {"label": "前端产物目录", "value": FRONTEND_DIR, "copyable": True},
    {"label": "日志目录", "value": LOG_DIR, "copyable": True},
    {"label": "项目根目录", "value": PROJECT_ROOT, "copyable": True},
    {"label": "访问地址", "value": "http://localhost:8099", "copyable": True},
]


# ============================================================
# 工具函数
# ============================================================

def check_port(port, host="127.0.0.1", timeout=0.5):
    """检测端口是否被占用（服务是否在运行）"""
    if port is None:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            result = s.connect_ex((host, port))
            return result == 0
    except Exception:
        return False


def check_ffmpeg():
    """检测 ffmpeg 是否在 PATH 中"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=5,
            shell=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_pid_by_port(port):
    """通过端口号查找进程 PID"""
    try:
        result = subprocess.run(
            f'netstat -ano | findstr ":{port} "',
            capture_output=True, text=True, timeout=5,
            shell=True,
        )
        lines = result.stdout.strip().split("\n")
        for line in lines:
            parts = line.split()
            if len(parts) >= 5 and f":{port}" in parts[1] and "LISTENING" in line.upper():
                return parts[-1]
    except Exception:
        pass
    return None


def kill_pid(pid):
    """终止指定 PID 的进程"""
    try:
        subprocess.run(f'taskkill /PID {pid} /F', capture_output=True, timeout=5, shell=True)
        return True
    except Exception:
        return False


def http_check(url, timeout=3):
    """发送 HTTP 请求检测状态码"""
    try:
        resp = urlopen(url, timeout=timeout)
        return resp.getcode()
    except URLError:
        return None
    except Exception:
        return None


# ============================================================
# API 处理
# ============================================================

def api_status():
    """获取所有服务状态"""
    statuses = []
    for svc in SERVICES:
        if svc["id"] == "ffmpeg":
            running = check_ffmpeg()
        else:
            running = check_port(svc["port"])
        pid = get_pid_by_port(svc["port"]) if (svc["port"] and running) else None
        statuses.append({
            "id": svc["id"],
            "name": svc["name"],
            "desc": svc["desc"],
            "port": svc["port"],
            "type": svc["type"],
            "running": running,
            "pid": pid,
        })
    return {"code": 0, "data": statuses}


def api_start(service_id):
    """启动指定服务"""
    svc = next((s for s in SERVICES if s["id"] == service_id), None)
    if not svc:
        return {"code": 1, "message": f"未知服务: {service_id}"}
    if svc["type"] == "dependency":
        return {"code": 1, "message": f"{svc['name']} 是外部依赖，请手动启动"}
    if "start_cwd" not in svc:
        return {"code": 1, "message": f"{svc['name']} 无法自动启动"}

    if check_port(svc["port"]):
        return {"code": 0, "message": f"{svc['name']} 已在运行 (端口 {svc['port']})"}

    env = os.environ.copy()
    if "env" in svc:
        env.update(svc["env"])

    try:
        # Windows: 用 start 在新窗口启动
        title = svc.get("win_title", service_id)
        cmd = f'start "{title}" cmd /k "cd /d {svc["start_cwd"]} && "{PYTHON_EXE}" {" ".join(svc["start_args"])}"'
        subprocess.Popen(cmd, shell=True, env=env)
        # 等待片刻检测是否启动成功
        time.sleep(2)
        running = check_port(svc["port"])
        if running:
            return {"code": 0, "message": f"{svc['name']} 启动成功 (端口 {svc['port']})"}
        else:
            return {"code": 0, "message": f"{svc['name']} 启动命令已发送，请稍候检测状态"}
    except Exception as e:
        return {"code": 1, "message": f"启动失败: {str(e)}"}


def api_stop(service_id):
    """停止指定服务"""
    svc = next((s for s in SERVICES if s["id"] == service_id), None)
    if not svc:
        return {"code": 1, "message": f"未知服务: {service_id}"}
    if not svc["port"]:
        return {"code": 1, "message": f"{svc['name']} 无端口，无法停止"}

    pid = get_pid_by_port(svc["port"])
    if not pid:
        return {"code": 0, "message": f"{svc['name']} 未在运行"}

    if kill_pid(pid):
        return {"code": 0, "message": f"{svc['name']} 已停止 (PID {pid})"}
    else:
        return {"code": 1, "message": f"停止失败 (PID {pid})"}


def api_health():
    """执行健康检查"""
    results = []
    for check in HEALTH_CHECKS:
        code = http_check(check["url"])
        results.append({
            "id": check["id"],
            "label": check["label"],
            "url": check["url"],
            "status_code": code,
            "ok": code == check["expect"],
        })
    return {"code": 0, "data": results}


def api_rebuild():
    """重建前端构建产物"""
    # 检测 vite 路径
    vite_path = os.path.join(PROJECT_ROOT, "node_modules", "vite", "bin", "vite.js")
    if not os.path.exists(vite_path):
        return {"code": 1, "message": "vite 未安装，请先在项目根目录运行 npm install"}

    try:
        cmd = f'cd /d "{PROJECT_ROOT}" && "{NODE_EXE}" "{vite_path}" build --outDir "{FRONTEND_DIR}"'
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, shell=True,
        )
        if result.returncode == 0:
            return {"code": 0, "message": "前端构建成功，刷新浏览器即可生效"}
        else:
            return {"code": 1, "message": f"构建失败: {result.stderr[-500:]}"}
    except subprocess.TimeoutExpired:
        return {"code": 1, "message": "构建超时 (120s)"}
    except Exception as e:
        return {"code": 1, "message": f"构建异常: {str(e)}"}


def api_config():
    """返回配置信息"""
    return {"code": 0, "data": CONFIG_INFO}


def api_troubleshooting():
    """返回故障排查表"""
    return {"code": 0, "data": TROUBLESHOOTING}


# ============================================================
# HTML 前端
# ============================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Manju Agent - 服务启动控制台</title>
<style>
:root {
  --bg: #0a0b0e; --bg-2: #0e1016; --panel: #14161c; --card: #1a1d25; --card-2: #20242e;
  --line: #262a35; --txt: #eef0f5; --sub: #8b93a7;
  --pink: #ff4d8d; --pur: #8a5cff; --blue: #4d9fff; --green: #3ddc84; --warn: #ffb020;
  --grad: linear-gradient(90deg, #ff4d8d, #8a5cff);
  --danger: #ff5c5c; --ok: #3ddc84;
  --ff: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
  --r: 10px; --r-s: 8px;
  --sh: 0 2px 8px rgba(0,0,0,.4); --sh2: 0 10px 30px rgba(0,0,0,.5);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--ff);
  background: var(--bg);
  color: var(--txt);
  min-height: 100vh;
}
.grad-txt { background: var(--grad); -webkit-background-clip: text; background-clip: text; color: transparent; font-weight: 800; }

/* Header */
.header {
  display: flex; align-items: center; justify-content: space-between;
  height: 56px; padding: 0 24px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 100;
}
.header-left { display: flex; align-items: center; gap: 10px; }
.logo-icon {
  width: 32px; height: 32px; border-radius: 8px;
  background: var(--grad);
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
}
.header-title { font-size: 16px; font-weight: 600; }
.header-right { display: flex; align-items: center; gap: 12px; }

/* Status Badge */
.status-badge {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 999px;
  font-size: 12px; font-weight: 500;
}
.status-badge.all-ok {
  background: rgba(61, 220, 132, 0.15);
  border: 1px solid rgba(61, 220, 132, 0.4);
  color: var(--green);
}
.status-badge.has-down {
  background: rgba(255, 92, 92, 0.15);
  border: 1px solid rgba(255, 92, 92, 0.4);
  color: var(--danger);
}
.status-dot { width: 8px; height: 8px; border-radius: 50%; }

/* Buttons */
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 16px; border-radius: var(--r-s);
  font-size: 13px; font-weight: 600; cursor: pointer;
  border: none; transition: all 0.15s;
  font-family: var(--ff);
}
.btn-primary {
  background: var(--grad); color: #fff;
  box-shadow: 0 4px 14px rgba(138, 92, 255, .35);
}
.btn-primary:hover { opacity: 0.9; }
.btn-secondary {
  background: var(--card); border: 1px solid var(--line); color: var(--txt);
}
.btn-secondary:hover { background: var(--card-2); border-color: var(--pur); }
.btn-danger {
  background: var(--card); border: 1px solid var(--danger); color: var(--danger);
}
.btn-danger:hover { background: rgba(255, 92, 92, 0.1); }
.btn-sm { padding: 5px 10px; font-size: 12px; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }

/* Main layout */
.main { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }

/* Section title */
.section-title {
  font-size: 13px; font-weight: 600; color: var(--sub);
  margin: 18px 0 10px; letter-spacing: 0.5px;
  display: flex; align-items: center; gap: 8px;
}
.section-title::before {
  content: ''; width: 3px; height: 14px; border-radius: 2px;
  background: var(--grad);
}

/* Service cards */
.svc-grid {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px;
}
.svc-card {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--r); padding: 14px;
  display: flex; flex-direction: column; gap: 8px;
  position: relative; overflow: hidden;
}
.svc-card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: var(--line);
}
.svc-card.running::before { background: var(--green); }
.svc-card.stopped::before { background: var(--danger); }
.svc-card.dep::before { background: var(--warn); }
.svc-name { font-size: 14px; font-weight: 700; }
.svc-desc { font-size: 11px; color: var(--sub); line-height: 1.4; }
.svc-port {
  font-size: 11px; font-family: monospace;
  background: var(--card-2); padding: 2px 8px; border-radius: 4px;
  color: var(--pur); display: inline-block; width: fit-content;
}
.svc-status {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 500;
}
.svc-status .dot { width: 8px; height: 8px; border-radius: 50%; }
.svc-status.running { color: var(--green); }
.svc-status.running .dot { background: var(--green); box-shadow: 0 0 8px var(--green); }
.svc-status.stopped { color: var(--danger); }
.svc-status.stopped .dot { background: var(--danger); }
.svc-actions { display: flex; gap: 6px; margin-top: 4px; }

/* Two column layout */
.two-col { display: grid; grid-template-columns: 1fr 380px; gap: 14px; margin-top: 14px; }

/* Card container */
.card {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--r); padding: 16px;
}
.card-title {
  font-size: 14px; font-weight: 700; margin-bottom: 12px;
  display: flex; align-items: center; justify-content: space-between;
}

/* Command block */
.cmd-block {
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-s); padding: 10px 12px;
  font-family: 'Consolas', 'Monaco', monospace; font-size: 12px;
  color: var(--txt); line-height: 1.6;
  word-break: break-all; position: relative;
  margin-bottom: 8px;
}
.cmd-block .copy-btn {
  position: absolute; top: 6px; right: 6px;
  background: var(--card-2); border: 1px solid var(--line);
  border-radius: 4px; padding: 3px 8px;
  font-size: 11px; color: var(--sub); cursor: pointer;
  transition: all 0.15s; font-family: var(--ff);
}
.cmd-block .copy-btn:hover { color: var(--pur); border-color: var(--pur); }
.cmd-label { font-size: 12px; color: var(--sub); margin-bottom: 4px; font-weight: 500; }

/* Step item */
.step-item { margin-bottom: 14px; }
.step-header {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px;
}
.step-badge {
  width: 22px; height: 22px; border-radius: 50%;
  background: var(--grad); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
}
.step-name { font-size: 13px; font-weight: 600; }
.step-status { font-size: 12px; margin-left: auto; }

/* Health check */
.health-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 0; border-bottom: 1px solid var(--line);
  font-size: 13px;
}
.health-item:last-child { border-bottom: none; }
.health-item .label { flex: 1; }
.health-item .url { font-family: monospace; font-size: 11px; color: var(--sub); }
.health-item .result {
  font-size: 12px; font-weight: 600; padding: 2px 8px;
  border-radius: 4px;
}
.health-item .result.ok { background: rgba(61,220,132,.15); color: var(--green); }
.health-item .result.fail { background: rgba(255,92,92,.15); color: var(--danger); }
.health-item .result.pending { background: var(--card-2); color: var(--sub); }

/* Config info */
.config-item {
  display: flex; flex-direction: column; gap: 4px;
  padding: 10px 0; border-bottom: 1px solid var(--line);
}
.config-item:last-child { border-bottom: none; }
.config-label { font-size: 11px; color: var(--sub); }
.config-value {
  font-size: 12px; font-family: monospace; color: var(--txt);
  word-break: break-all; display: flex; align-items: center; gap: 6px;
}
.config-value .copy-btn {
  background: var(--card-2); border: 1px solid var(--line);
  border-radius: 3px; padding: 2px 6px;
  font-size: 10px; color: var(--sub); cursor: pointer;
  transition: all 0.15s; font-family: var(--ff); flex-shrink: 0;
}
.config-value .copy-btn:hover { color: var(--pur); border-color: var(--pur); }

/* Quick actions */
.quick-actions { display: flex; flex-direction: column; gap: 8px; }

/* Troubleshooting table */
.trouble-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.trouble-table th {
  text-align: left; padding: 8px 12px;
  background: var(--card-2); color: var(--sub);
  font-weight: 500; font-size: 11px;
  border-bottom: 1px solid var(--line);
}
.trouble-table td {
  padding: 8px 12px; border-bottom: 1px solid var(--line);
  vertical-align: top; line-height: 1.5;
}
.trouble-table td:first-child { color: var(--danger); font-weight: 500; white-space: nowrap; }

/* Toast */
.toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: var(--card-2); border: 1px solid var(--pur);
  border-radius: var(--r-s); padding: 10px 20px;
  font-size: 13px; color: var(--txt);
  box-shadow: var(--sh2); z-index: 200;
  opacity: 0; transition: opacity 0.2s;
  pointer-events: none;
}
.toast.show { opacity: 1; }

/* Loading spinner */
.spinner {
  display: inline-block; width: 14px; height: 14px;
  border: 2px solid var(--line); border-top-color: var(--pur);
  border-radius: 50%; animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Responsive */
@media (max-width: 1100px) {
  .svc-grid { grid-template-columns: repeat(3, 1fr); }
  .two-col { grid-template-columns: 1fr; }
}
@media (max-width: 700px) {
  .svc-grid { grid-template-columns: repeat(2, 1fr); }
  .header-title { font-size: 14px; }
}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="logo-icon">🎬</div>
    <div class="header-title">Manju Agent · <span class="grad-txt">服务启动控制台</span></div>
  </div>
  <div class="header-right">
    <div class="status-badge has-down" id="globalStatus">
      <div class="status-dot" style="background: var(--warn)"></div>
      <span id="globalStatusText">检测中...</span>
    </div>
    <button class="btn btn-primary" onclick="window.open('http://localhost:8099','_blank')">
      🌐 打开 8099
    </button>
  </div>
</div>

<div class="main">
  <!-- Service Status Cards -->
  <div class="section-title">服务状态</div>
  <div class="svc-grid" id="svcGrid">
    <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--sub);">
      <div class="spinner"></div> 正在检测服务状态...
    </div>
  </div>

  <!-- Main Content -->
  <div class="two-col">
    <!-- Left Column: Startup Steps + Health Checks -->
    <div style="display: flex; flex-direction: column; gap: 14px;">
      <!-- Startup Steps -->
      <div class="card">
        <div class="card-title">
          <span>启动步骤</span>
          <button class="btn btn-primary btn-sm" onclick="startAll()">▶ 全部启动</button>
        </div>

        <div class="step-item">
          <div class="step-header">
            <div class="step-badge">A</div>
            <div class="step-name">启动后端 (7788)</div>
            <div class="step-status" id="stepA-status">—</div>
          </div>
          <div class="cmd-label">Git Bash:</div>
          <div class="cmd-block">
            cd /d/AI/manju_agent/server && "C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe" main.py
            <button class="copy-btn" onclick="copyText(this.parentElement.textContent.trim().split('C:/Users')[0] + 'C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe main.py')">复制</button>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="btn btn-primary btn-sm" onclick="startService('backend')">▶ 启动</button>
            <button class="btn btn-danger btn-sm" onclick="stopService('backend')">■ 停止</button>
          </div>
        </div>

        <div class="step-item">
          <div class="step-header">
            <div class="step-badge">B</div>
            <div class="step-name">启动前端 (8099)</div>
            <div class="step-status" id="stepB-status">—</div>
          </div>
          <div class="cmd-label">Git Bash (带环境变量):</div>
          <div class="cmd-block">
            cd /d/AI/manju_agent && MANJU_FRONTEND_DIR="C:/Users/L/AppData/Local/Temp/manju_dist_fixed" MANJU_LOG_DIR="C:/Users/L/AppData/Local/Temp/manju_logs" "C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe" web_app.py
            <button class="copy-btn" onclick="copyText('cd /d/AI/manju_agent && MANJU_FRONTEND_DIR=&quot;C:/Users/L/AppData/Local/Temp/manju_dist_fixed&quot; MANJU_LOG_DIR=&quot;C:/Users/L/AppData/Local/Temp/manju_logs&quot; &quot;C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe&quot; web_app.py')">复制</button>
          </div>
          <div class="cmd-label">PowerShell:</div>
          <div class="cmd-block">
            $env:MANJU_FRONTEND_DIR="C:/Users/L/AppData/Local/Temp/manju_dist_fixed"
            $env:MANJU_LOG_DIR="C:/Users/L/AppData/Local/Temp/manju_logs"
            cd D:\AI\manju_agent
            & "C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe" web_app.py
            <button class="copy-btn" onclick="copyText('$env:MANJU_FRONTEND_DIR=&quot;C:/Users/L/AppData/Local/Temp/manju_dist_fixed&quot;\n$env:MANJU_LOG_DIR=&quot;C:/Users/L/AppData/Local/Temp/manju_logs&quot;\ncd D:\\AI\\manju_agent\n&amp; &quot;C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe&quot; web_app.py')">复制</button>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="btn btn-primary btn-sm" onclick="startService('frontend')">▶ 启动</button>
            <button class="btn btn-danger btn-sm" onclick="stopService('frontend')">■ 停止</button>
          </div>
        </div>
      </div>

      <!-- Health Checks -->
      <div class="card">
        <div class="card-title">
          <span>健康检查</span>
          <button class="btn btn-secondary btn-sm" onclick="runHealthCheck()">
            🔄 重新检测
          </button>
        </div>
        <div id="healthList">
          <div class="health-item">
            <span class="label">前端页面</span>
            <span class="url">GET http://127.0.0.1:8099/</span>
            <span class="result pending">待检测</span>
          </div>
          <div class="health-item">
            <span class="label">后端 API</span>
            <span class="url">GET http://127.0.0.1:7788/docs</span>
            <span class="result pending">待检测</span>
          </div>
          <div class="health-item">
            <span class="label">代理链路</span>
            <span class="url">GET http://127.0.0.1:8099/api/v1/health</span>
            <span class="result pending">待检测</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Right Column: Config + Quick Actions -->
    <div style="display: flex; flex-direction: column; gap: 14px;">
      <!-- Quick Actions -->
      <div class="card">
        <div class="card-title">快捷操作</div>
        <div class="quick-actions">
          <button class="btn btn-secondary" style="width: 100%; justify-content: center;" onclick="rebuildFrontend()">
            🔄 重建前端
          </button>
          <button class="btn btn-secondary" style="width: 100%; justify-content: center;" onclick="window.open('http://localhost:8099','_blank')">
            🌐 打开浏览器
          </button>
          <button class="btn btn-secondary" style="width: 100%; justify-content: center;" onclick="window.open('http://127.0.0.1:7788/docs','_blank')">
            📖 后端 API 文档
          </button>
          <button class="btn btn-secondary" style="width: 100%; justify-content: center;" onclick="window.open('http://127.0.0.1:8188','_blank')">
            🎨 ComfyUI
          </button>
        </div>
      </div>

      <!-- Config Info -->
      <div class="card">
        <div class="card-title">配置信息</div>
        <div id="configList">
          <div style="text-align: center; color: var(--sub); padding: 20px;">
            <div class="spinner"></div> 加载中...
          </div>
        </div>
      </div>

      <!-- Rebuild Command -->
      <div class="card">
        <div class="card-title">重建前端命令</div>
        <div class="cmd-label">Git Bash:</div>
        <div class="cmd-block">
          cd /d/AI/manju_agent && "C:/Users/L/.workbuddy/binaries/node/versions/22.22.2/node.exe" node_modules/vite/bin/vite.js build --outDir C:/Users/L/AppData/Local/Temp/manju_dist_fixed
          <button class="copy-btn" onclick="copyText('cd /d/AI/manju_agent &amp;&amp; &quot;C:/Users/L/.workbuddy/binaries/node/versions/22.22.2/node.exe&quot; node_modules/vite/bin/vite.js build --outDir C:/Users/L/AppData/Local/Temp/manju_dist_fixed')">复制</button>
        </div>
        <div style="font-size: 11px; color: var(--sub); margin-top: 6px;">
          ⚠️ 构建完直接刷新浏览器即可，无需重启前端进程（web_app.py 按磁盘实时读取）
        </div>
      </div>
    </div>
  </div>

  <!-- Troubleshooting -->
  <div class="section-title" style="margin-top: 18px;">常见问题排查</div>
  <div class="card" style="padding: 0; overflow: hidden;">
    <table class="trouble-table" id="troubleTable">
      <thead>
        <tr>
          <th style="width: 30%;">现象</th>
          <th style="width: 25%;">原因</th>
          <th style="width: 45%;">解决方案</th>
        </tr>
      </thead>
      <tbody id="troubleBody">
        <tr><td colspan="3" style="text-align: center; color: var(--sub);"><div class="spinner"></div> 加载中...</td></tr>
      </tbody>
    </table>
  </div>

  <div style="text-align: center; padding: 20px; color: var(--sub); font-size: 12px;">
    Manju Agent 服务启动控制台 · 端口 8098 · 仅依赖 Python 标准库
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ============================================================
// API 调用
// ============================================================
async function apiCall(path, method = 'GET') {
  try {
    const resp = await fetch(path, { method });
    return await resp.json();
  } catch (e) {
    console.error('API error:', e);
    return { code: 1, message: '请求失败: ' + e.message };
  }
}

// ============================================================
// 状态更新
// ============================================================
async function updateStatus() {
  const result = await apiCall('/api/status');
  if (result.code !== 0) return;

  const grid = document.getElementById('svcGrid');
  const statuses = result.data;
  let allRunning = true;

  grid.innerHTML = statuses.map(s => {
    const isRunning = s.running;
    if (!isRunning && s.type === 'core') allRunning = false;
    const cardClass = isRunning ? 'running' : (s.type === 'dependency' ? 'dep' : 'stopped');
    const statusClass = isRunning ? 'running' : 'stopped';
    const statusText = isRunning ? '运行中' : '已停止';
    const portText = s.port ? ':' + s.port : 'PATH';

    let actions = '';
    if (s.type === 'core') {
      actions = isRunning
        ? '<button class="btn btn-danger btn-sm" onclick="stopService(\'' + s.id + '\')">停止</button>'
        : '<button class="btn btn-primary btn-sm" onclick="startService(\'' + s.id + '\')">启动</button>';
    } else {
      actions = '<span style="font-size: 11px; color: var(--sub);">外部依赖</span>';
    }

    return '<div class="svc-card ' + cardClass + '">' +
      '<div class="svc-name">' + s.name + '</div>' +
      '<div class="svc-desc">' + s.desc + '</div>' +
      '<div class="svc-port">' + portText + '</div>' +
      '<div class="svc-status ' + statusClass + '">' +
        '<div class="dot"></div>' + statusText +
        (s.pid ? ' (PID ' + s.pid + ')' : '') +
      '</div>' +
      '<div class="svc-actions">' + actions + '</div>' +
    '</div>';
  }).join('');

  // Update global status badge
  const badge = document.getElementById('globalStatus');
  const text = document.getElementById('globalStatusText');
  const dot = badge.querySelector('.status-dot');
  if (allRunning) {
    badge.className = 'status-badge all-ok';
    dot.style.background = 'var(--green)';
    text.textContent = '核心服务运行中';
  } else {
    badge.className = 'status-badge has-down';
    dot.style.background = 'var(--danger)';
    const downCount = statuses.filter(s => !s.running && s.type === 'core').length;
    text.textContent = downCount + ' 个核心服务未运行';
  }

  // Update step statuses
  const backend = statuses.find(s => s.id === 'backend');
  const frontend = statuses.find(s => s.id === 'frontend');
  document.getElementById('stepA-status').innerHTML = backend && backend.running
    ? '<span style="color: var(--green)">● 运行中 (PID ' + backend.pid + ')</span>'
    : '<span style="color: var(--danger)">○ 未运行</span>';
  document.getElementById('stepB-status').innerHTML = frontend && frontend.running
    ? '<span style="color: var(--green)">● 运行中 (PID ' + frontend.pid + ')</span>'
    : '<span style="color: var(--danger)">○ 未运行</span>';
}

// ============================================================
// 服务操作
// ============================================================
async function startService(id) {
  showToast('正在启动 ' + id + '...');
  const result = await apiCall('/api/start/' + id, 'POST');
  showToast(result.message || '操作完成');
  setTimeout(updateStatus, 1000);
}

async function stopService(id) {
  showToast('正在停止 ' + id + '...');
  const result = await apiCall('/api/stop/' + id, 'POST');
  showToast(result.message || '操作完成');
  setTimeout(updateStatus, 500);
}

async function startAll() {
  showToast('正在启动所有服务...');
  const r1 = await apiCall('/api/start/backend', 'POST');
  showToast(r1.message);
  await new Promise(r => setTimeout(r, 2000));
  const r2 = await apiCall('/api/start/frontend', 'POST');
  showToast(r2.message);
  setTimeout(updateStatus, 1000);
}

// ============================================================
// 健康检查
// ============================================================
async function runHealthCheck() {
  const list = document.getElementById('healthList');
  list.innerHTML = [
    {label: '前端页面', url: 'GET http://127.0.0.1:8099/', id: 'frontend'},
    {label: '后端 API', url: 'GET http://127.0.0.1:7788/docs', id: 'backend'},
    {label: '代理链路', url: 'GET http://127.0.0.1:8099/api/v1/health', id: 'proxy'},
  ].map(h => '<div class="health-item">' +
    '<span class="label">' + h.label + '</span>' +
    '<span class="url">' + h.url + '</span>' +
    '<span class="result pending"><div class="spinner" style="width:12px;height:12px;"></div></span>' +
  '</div>').join('');

  const result = await apiCall('/api/health');
  if (result.code !== 0) {
    showToast('健康检查失败');
    return;
  }

  list.innerHTML = result.data.map(h => {
    const resultClass = h.ok ? 'ok' : 'fail';
    const resultText = h.status_code ? 'HTTP ' + h.status_code : '连接失败';
    return '<div class="health-item">' +
      '<span class="label">' + h.label + '</span>' +
      '<span class="url">GET ' + h.url + '</span>' +
      '<span class="result ' + resultClass + '">' + resultText + '</span>' +
    '</div>';
  }).join('');
}

// ============================================================
// 重建前端
// ============================================================
async function rebuildFrontend() {
  if (!confirm('确认重建前端构建产物？这可能需要 30-60 秒。')) return;
  showToast('正在构建前端...');
  const result = await apiCall('/api/rebuild', 'POST');
  showToast(result.message || '构建完成');
}

// ============================================================
// 配置信息
// ============================================================
async function loadConfig() {
  const result = await apiCall('/api/config');
  if (result.code !== 0) return;
  const list = document.getElementById('configList');
  list.innerHTML = result.data.map(c => 
    '<div class="config-item">' +
      '<div class="config-label">' + c.label + '</div>' +
      '<div class="config-value">' +
        '<span>' + c.value + '</span>' +
        (c.copyable ? '<button class="copy-btn" onclick="copyText(\'' + c.value.replace(/\\/g, '\\\\').replace(/'/g, "\\'") + '\')">复制</button>' : '') +
      '</div>' +
    '</div>'
  ).join('');
}

// ============================================================
// 故障排查表
// ============================================================
async function loadTroubleshooting() {
  const result = await apiCall('/api/troubleshooting');
  if (result.code !== 0) return;
  const body = document.getElementById('troubleBody');
  body.innerHTML = result.data.map(t =>
    '<tr><td>' + t.symptom + '</td><td>' + t.cause + '</td><td>' + t.solution + '</td></tr>'
  ).join('');
}

// ============================================================
// 工具函数
// ============================================================
function showToast(msg) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove('show'), 3000);
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => {
    showToast('已复制到剪贴板');
  }).catch(() => {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); showToast('已复制到剪贴板'); }
    catch(e) { showToast('复制失败'); }
    document.body.removeChild(ta);
  });
}

// ============================================================
// 初始化
// ============================================================
async function init() {
  await Promise.all([updateStatus(), loadConfig(), loadTroubleshooting(), runHealthCheck()]);
  // 每 5 秒刷新状态
  setInterval(updateStatus, 5000);
}

init();
</script>
</body>
</html>
"""


# ============================================================
# HTTP 请求处理
# ============================================================

class ConsoleHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_html(HTML_PAGE)
        elif path == "/api/status":
            self._send_json(api_status())
        elif path == "/api/health":
            self._send_json(api_health())
        elif path == "/api/config":
            self._send_json(api_config())
        elif path == "/api/troubleshooting":
            self._send_json(api_troubleshooting())
        else:
            self._send_json({"code": 404, "message": "Not Found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/start/"):
            svc_id = path.split("/api/start/")[-1].strip("/")
            self._send_json(api_start(svc_id))
        elif path.startswith("/api/stop/"):
            svc_id = path.split("/api/stop/")[-1].strip("/")
            self._send_json(api_stop(svc_id))
        elif path == "/api/rebuild":
            self._send_json(api_rebuild())
        else:
            self._send_json({"code": 404, "message": "Not Found"}, 404)

    def _send_html(self, content):
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ============================================================
# 主入口
# ============================================================

def main():
    # 检测 vite 路径
    global VITE_JS
    vite_candidate = os.path.join(PROJECT_ROOT, "node_modules", "vite", "bin", "vite.js")
    if os.path.exists(vite_candidate):
        VITE_JS = vite_candidate

    server = http.server.HTTPServer(("0.0.0.0", PORT), ConsoleHandler)
    print(f"\n  ┌──────────────────────────────────────────────────┐")
    print(f"  │  Manju Agent 服务启动控制台                       │")
    print(f"  │  访问地址: http://localhost:{PORT}                  │")
    print(f"  │  按 Ctrl+C 停止                                   │")
    print(f"  └──────────────────────────────────────────────────┘\n")

    # 自动打开浏览器
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务控制台已停止。")
        server.shutdown()


if __name__ == "__main__":
    main()
