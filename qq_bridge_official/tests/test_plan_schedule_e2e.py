# -*- coding: utf-8 -*-
"""计划「定时执行」端到端验收（2026-09-13）：真实起 bridge 子进程（独立端口 18080）。

验证用户可点的闭环：
  1. POST /api/qq/plans 存草稿 → 返回完整定时字段
  2. POST /api/qq/plans/{id}/schedule 设定时（几秒后）→ 返回 enabled / at / repeat
  3. 等后台调度器（15s 周期）到点自动下发 → 计划自动转为「已触发」（enabled=false、
     last_fired_at>0、last_batch 非空），且 /api/qq/queue/batches 能看到该批次
  4. 过去的时刻被拒（400）；enabled=false 取消定时 → 字段复位

测试隔离：QQ_DATA_DIR 指向临时目录 —— 不碰用户真实 queue.json / plans.json。
安全：计划内容用「放大」项且图片不存在 → 消费者下载原图 404 → job failed，
绝不会向主项目提交真实生图任务（不扣额度、不产生主项目任务）。

运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_plan_schedule_e2e.py -m e2e -v --timeout=240
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.e2e

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18080
TOKEN = "e2e-test-token"
# 调度器轮询间隔 15s；等到点 + 下发 + 落盘，最多等 75s
FIRE_WAIT = 75


def _wait_port(port: int, timeout: float = 45.0) -> bool:
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
    data_dir = tempfile.mkdtemp(prefix="qq_bridge_e2e_plan_")
    env = dict(os.environ)
    env.update({
        "PORT": str(PORT),
        "HOST": "127.0.0.1",
        "ONEBOT_V11_ACCESS_TOKEN": TOKEN,
        "QQ_DATA_DIR": data_dir,          # 隔离：不写用户真实队列/计划
        "REPLY_DELAY_MIN": "0",
        "REPLY_DELAY_MAX": "0",
        "BOT_OWNER_QQ": "",
        "PRIVATE_WHITELIST": "",
        "GROUP_WHITELIST": "",
        "USER_BLACKLIST": "",
        "STRICT_WHITELIST": "false",
        "OFFICIAL_ENABLE": "false",
        "OFFICIAL_APPID": "",
        "OFFICIAL_SECRET": "",
    })
    log_path = os.path.join(ROOT, "logs", "e2e_plan_schedule.out")
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
        pytest.fail("bridge 未在 45s 内监听 %d（看 logs/e2e_plan_schedule.out）" % PORT)
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


def _req(method: str, path: str, payload=None):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read().decode("utf-8") or "{}"
        return json.loads(body)


def _plan_items_for_schedule():
    """放大项 + 不存在的图片：能入队（产生批次），但执行时下载失败 → 不会调到主项目生图。"""
    return json.dumps([{
        "序号": 1, "类型": "放大", "图片名称": "__e2e_missing__",
        "图片": "/file?name=__e2e_missing__.png", "倍数": 2,
    }], ensure_ascii=False)


def test_schedule_fires_automatically(bridge):
    """核心闭环：设定时 → 到点由调度器自动下发 → 计划转已触发 + 批次进队列看板。"""
    plan = _req("POST", "/api/qq/plans", {
        "name": "e2e 定时计划", "channel": "local", "json_text": _plan_items_for_schedule(),
    })
    assert plan.get("ok") is True, plan
    pid = plan["plan_id"]
    assert plan["plan"]["schedule_enabled"] is False, "新建计划不应自带定时"

    at = int(time.time()) + 4
    r = _req("POST", "/api/qq/plans/%s/schedule" % pid,
             {"schedule_at": at, "repeat": "none", "enabled": True})
    assert r.get("ok") is True, r
    assert r["plan"]["schedule_enabled"] is True
    assert abs(float(r["plan"]["schedule_at"]) - at) <= 1

    # 等调度器到点自动下发
    fired = None
    deadline = time.time() + FIRE_WAIT
    while time.time() < deadline:
        items = _req("GET", "/api/qq/plans")["items"]
        cur = next((x for x in items if x.get("plan_id") == pid), None)
        if cur and not cur.get("schedule_enabled") and cur.get("schedule_last_fired_at"):
            fired = cur
            break
        time.sleep(2)
    assert fired is not None, "调度器未在 %ds 内自动下发定时计划（查 logs/e2e_plan_schedule.out）" % FIRE_WAIT

    bid = str(fired.get("schedule_last_batch") or "")
    assert bid, "计划未记录定时下发的批次号"
    batches = _req("GET", "/api/qq/queue/batches")["items"]
    assert any(str(b.get("batch_id")) == bid for b in batches), \
        "定时下发的批次未出现在队列看板：%s" % [b.get("batch_id") for b in batches]

    _req("DELETE", "/api/qq/plans/%s" % pid)


def test_schedule_validation_and_cancel(bridge):
    """过去的时刻必须被拒（400）；取消定时后字段复位。"""
    plan = _req("POST", "/api/qq/plans",
                {"name": "e2e 取消定时", "channel": "local", "json_text": "[]"})
    pid = plan["plan_id"]

    with pytest.raises(urllib.error.HTTPError) as ei:
        _req("POST", "/api/qq/plans/%s/schedule" % pid,
             {"schedule_at": int(time.time()) - 10, "enabled": True})
    assert ei.value.code == 400, "过去的定时时间应返回 400"

    r = _req("POST", "/api/qq/plans/%s/schedule" % pid,
             {"schedule_at": int(time.time()) + 3600, "repeat": "daily", "enabled": True})
    assert r.get("ok") is True and r["plan"]["schedule_repeat"] == "daily"

    r2 = _req("POST", "/api/qq/plans/%s/schedule" % pid, {"enabled": False})
    assert r2.get("ok") is True
    cur = next(x for x in _req("GET", "/api/qq/plans")["items"] if x["plan_id"] == pid)
    assert cur["schedule_enabled"] is False
    assert int(cur["schedule_at"]) == 0

    _req("DELETE", "/api/qq/plans/%s" % pid)
