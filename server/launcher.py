# -*- coding: utf-8 -*-
"""受管服务启动器（服务管理器后端）。

对 backend / frontend 两个受管进程做 start / stop / restart / status，
用 PID 文件（run/<name>.pid）跟踪生命周期，避免重复拉起。

用法（由 module_manager.control_service 调用，或手动）：
    python launcher.py start backend
    python launcher.py stop frontend
    python launcher.py restart backend
    python launcher.py status frontend
"""
import os
import sys
import time
import json
import subprocess

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SERVER_DIR)
# 运行期 PID/日志目录：默认项目内 run/；若本机 run/ 被宿主权限/锁限制，可设 MANJU_RUN_DIR 指向工作区外。
RUN_DIR = os.environ.get("MANJU_RUN_DIR") or os.path.join(REPO_ROOT, "run")
os.makedirs(RUN_DIR, exist_ok=True)

PY = sys.executable

# 受管服务定义：backend(7788) / frontend(8099)
SERVICES = {
    "backend": {
        "cwd": SERVER_DIR,
        "cmd": [PY, "main.py"],
        # 沿用运行环境的 DB/LOG 覆盖（工作区 .db 只读锁 → 指向可写副本）
        "env_extra": {
            # 运行时库默认指向文档化的可写快照副本（PROJECT_MAP §3：MANJU_DB_PATH=
            # .../manju_app_snapshot.db）。工作区 server/app.db 在沙箱/安全删除钩子下
            # 为只读且为陈旧种子库，作为默认会落错库；显式默认到快照可保证无论被谁
            # （看门狗/手动/service_console）拉起都使用含最新数据的可写库。
            "MANJU_DB_PATH": os.environ.get("MANJU_DB_PATH") or r"C:/Users/L/AppData/Local/Temp/manju_app_snapshot.db",
            "MANJU_LOG_DIR": os.environ.get("MANJU_LOG_DIR") or os.path.join(REPO_ROOT, "logs"),
        },
    },
    "frontend": {
        "cwd": REPO_ROOT,
        "cmd": [PY, "web_app.py"],
        # 默认服务 frontend/dist；若本机 frontend/dist 受 safe-delete 钩子限制无法被 vite 覆盖，
        # 可在运行环境中设置 MANJU_FRONTEND_DIR 指向工作区外构建目录（如 C:/Users/L/AppData/Local/Temp/manju_dist_fixed），
        # launcher 会透传给 web_app，同时 module_manager._run_frontend_build 也会构建到同一目录，保持闭环。
        # 日志目录覆盖：D:\AI\logs 可能无写权限 → 指向项目内 logs/（已验证可写）。
        "env_extra": {
            "MANJU_LOG_DIR": os.environ.get("MANJU_LOG_DIR") or os.path.join(REPO_ROOT, "logs"),
            "MANJU_FRONTEND_DIR": os.environ.get("MANJU_FRONTEND_DIR") or "",
        },
    },
}

DETACH = getattr(os, "DETACHED_PROCESS", 0) | getattr(os, "CREATE_NEW_PROCESS_GROUP", 0)


def _pid_file(name):
    return os.path.join(RUN_DIR, "%s.pid" % name)


def _read_pid(name):
    p = _pid_file(name)
    if not os.path.isfile(p):
        return None
    try:
        return int(open(p, "r").read().strip())
    except Exception:
        return None


def _is_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)  # Windows：信号 0 仅做存在性探测
        return True
    except Exception:
        return False


def _write_pid(name, pid):
    try:
        open(_pid_file(name), "w").write(str(pid))
    except Exception:
        pass


def _start(name):
    cfg = SERVICES[name]
    pid = _read_pid(name)
    if _is_alive(pid):
        return {"name": name, "action": "start", "status": "already_running", "pid": pid}
    env = os.environ.copy()
    env.update(cfg["env_extra"])
    # 日志目录与 run/ 分离：run/ 只放 PID 文件；日志写到 MANJU_LOG_DIR，避免工作区 run/ 被锁定。
    log_dir = os.environ.get("MANJU_LOG_DIR") or os.path.join(REPO_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "%s.out.log" % name)
    with open(log_path, "ab") as lf:
        proc = subprocess.Popen(
            cfg["cmd"], cwd=cfg["cwd"], env=env, stdout=lf, stderr=lf,
            creationflags=DETACH, close_fds=True)
    _write_pid(name, proc.pid)
    time.sleep(1.5)
    alive = _is_alive(proc.pid)
    return {"name": name, "action": "start",
            "status": "started" if alive else "failed", "pid": proc.pid}


def _stop(name):
    pid = _read_pid(name)
    if not _is_alive(pid):
        return {"name": name, "action": "stop", "status": "not_running"}
    try:
        # Windows 可靠终止：taskkill /F /T（含子进程）
        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        try:
            os.kill(pid, 9)
        except Exception:
            pass
    try:
        if os.path.isfile(_pid_file(name)):
            os.remove(_pid_file(name))
    except Exception:
        pass
    return {"name": name, "action": "stop", "status": "stopped", "pid": pid}


def _restart(name):
    _stop(name)
    time.sleep(1)
    return _start(name)


def _status(name):
    pid = _read_pid(name)
    return {"name": name, "status": "running" if _is_alive(pid) else "stopped", "pid": pid}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: launcher.py <start|stop|restart|status> <backend|frontend>"}))
        return
    action, name = sys.argv[1], sys.argv[2]
    if name not in SERVICES:
        print(json.dumps({"error": "unknown_service", "name": name}))
        return
    if action == "start":
        print(json.dumps(_start(name)))
    elif action == "stop":
        print(json.dumps(_stop(name)))
    elif action == "restart":
        print(json.dumps(_restart(name)))
    elif action == "status":
        print(json.dumps(_status(name)))
    else:
        print(json.dumps({"error": "unknown_action", "action": action}))


if __name__ == "__main__":
    main()
