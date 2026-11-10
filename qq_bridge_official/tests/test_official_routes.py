# -*- coding: utf-8 -*-
"""官方机器人独立接口 /api/qqo/* 的端到端验证（2026-09-13）。

不重启现网 bridge：本模块起一个独立端口 18080 的 bridge 子进程（复用
test_e2e_ws 的范式），只验证路由注册与返回结构 —— 不动现网 8080，也不改
真实 .env（OFFICIAL_* 显式置空，避免宿主 .env 泄漏）。

覆盖「两个机器人两个页面」需求：
  GET /api/qqo/status   —— 官方通道三态（enabled/configured/online）+ 沙箱 + bots + 日额度
  GET /api/qqo/config   —— 只暴露「官方机器人」分组（与 NapCat 参数物理隔离）
PUT 的 rejected 过滤（只写官方分组 key）由 config_api 的 SCHEMA 单测间接覆盖，
此处不测 PUT 以免跨进程写真实 .env。

运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_official_routes.py -m e2e -q
"""
import json
import os
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18080
TOKEN = "e2e-test-token"


def _wait_port(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        finally:
            s.close()
        time.sleep(0.3)
    return False


def _kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


@pytest.fixture(scope="module")
def bridge():
    env = dict(os.environ)
    env.update({
        "PORT": str(PORT),
        "HOST": "127.0.0.1",
        "ONEBOT_V11_ACCESS_TOKEN": TOKEN,
        "REPLY_DELAY_MIN": "0",
        "REPLY_DELAY_MAX": "0",
        "BOT_OWNER_QQ": "",
        "PRIVATE_WHITELIST": "",
        "GROUP_WHITELIST": "",
        "USER_BLACKLIST": "",
        "STRICT_WHITELIST": "false",
        # 显式关闭官方通道，避免宿主 .env 的 OFFICIAL_* 泄漏进测试进程
        "OFFICIAL_ENABLE": "false",
        "OFFICIAL_APPID": "",
        "OFFICIAL_SECRET": "",
    })
    log_path = os.path.join(ROOT, "logs", "e2e_official.out")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-u", "bot.py"],
        cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    if not _wait_port(PORT):
        _kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        logf.close()
        pytest.fail("bridge 未在 40s 内监听 %d" % PORT)
    try:
        yield proc
    finally:
        _kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        logf.flush()
        logf.close()


def _get(path: str) -> dict:
    import urllib.request

    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def test_official_status_contract(bridge):
    """/api/qqo/status 必须有完整三态 + 额度结构（页面据此渲染徽标）。"""
    d = _get("/api/qqo/status")
    assert d.get("ok") is True, "ok 字段缺失"
    assert "enabled" in d and isinstance(d["enabled"], bool)
    assert "configured" in d and isinstance(d["configured"], bool)
    assert "online" in d and isinstance(d["online"], bool)
    assert "sandbox" in d and isinstance(d["sandbox"], bool)
    assert isinstance(d.get("bots", []), list)
    assert isinstance(d.get("daily_limit", 0), int) and d["daily_limit"] > 0
    assert isinstance(d.get("quota_used", {}), dict)


def test_official_config_exposes_only_official_group(bridge):
    """/api/qqo/config 只返回「官方机器人」分组 —— 与 NapCat 参数物理隔离。"""
    d = _get("/api/qqo/config")
    assert d.get("ok") is True
    groups = d.get("groups", [])
    assert isinstance(groups, list)
    names = [g.get("name") for g in groups]
    assert "官方机器人" in names, "必须暴露官方机器人分组"
    # 隔离保证：不应把 NapCat 的常规分组（如「常规·消息」「常规·任务队列」）泄漏进来
    leaked = [n for n in names if n and n != "官方机器人"]
    assert not leaked, "官方配置接口不应含 NapCat 分组：%s" % leaked
