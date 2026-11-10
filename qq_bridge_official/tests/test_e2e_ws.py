# -*- coding: utf-8 -*-
"""端到端连通测试：模拟 onebot 协议端（NapCat/LLOneBot）连上 qq_bridge 收发消息。

覆盖设计文档：
  §3.1 connector  —— 协议端接入、Token 鉴权（错配必须明确报错，不静默）
  §3.2 dispatcher —— 群消息未 @机器人 直接丢弃（不回任何包 = 不消耗额度）
  §11 渐进式上线第 1 步 —— 连通：/ping 回 pong

与真实部署的差异（仅为可重复运行而设，业务逻辑完全一致）：
  * 用独立端口 18080 起 bot.py，避免占用/干扰真实 bridge 的 8080；
  * Token 用固定测试值，回传随机延迟置 0。
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import pytest
import websockets

# 整个模块标记为 e2e：默认 `pytest` 不收集（pyproject 里 addopts=-m "not e2e"），
# 避免启动真实 bot.py 子进程阻塞/卡死。需端到端时显式：pytest -m e2e
pytestmark = pytest.mark.e2e

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18080
TOKEN = "e2e-test-token"
WS_URL = "ws://127.0.0.1:%d/onebot/v11/ws" % PORT
SELF_ID = "10001"
TEST_USER = 20002


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
    """杀掉进程及其子树（Windows 上 terminate 只杀主进程，事件循环/线程可能残留，
    导致子进程挂起需手动关闭——这正是之前"卡住要手动关"的根因）。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            import os as _os
            import signal as _sig
            try:
                _os.killpg(_os.getpgid(proc.pid), _sig.SIGTERM)
            except Exception:
                proc.terminate()
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


@pytest.fixture(scope="module")
def bridge():
    """起一个独立端口的 bridge 进程，供本模块全部用例复用。"""
    env = dict(os.environ)
    env.update({
        "PORT": str(PORT),
        "HOST": "127.0.0.1",
        "ONEBOT_V11_ACCESS_TOKEN": TOKEN,
        "REPLY_DELAY_MIN": "0",
        "REPLY_DELAY_MAX": "0",
        # 屏蔽宿主 .env 的权限配置（bot.py 的 load_dotenv 不覆盖已存在键），
        # 否则真实 .env 的 BOT_OWNER_QQ/STRICT_WHITELIST/GROUP_WHITELIST 会泄漏进来，
        # 导致测试虚拟用户被当作陌生人静默、e2e 用例在开发者机器上不可重复。
        "BOT_OWNER_QQ": "",
        "PRIVATE_WHITELIST": "",
        "GROUP_WHITELIST": "",
        "USER_BLACKLIST": "",
        "STRICT_WHITELIST": "false",
    })
    log_path = os.path.join(ROOT, "logs", "e2e_bridge.out")
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
        pytest.fail("bridge 未在 40s 内监听 %d，日志见 %s" % (PORT, log_path))
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


def _event(text="", user_id=TEST_USER, group_id=0, at_qq=None, self_id=SELF_ID):
    """构造 onebot v11 私聊/群聊 message 事件（Array 格式，文档 §5.1）。"""
    segs = []
    if at_qq:
        segs.append({"type": "at", "data": {"qq": str(at_qq)}})
    if text:
        segs.append({"type": "text", "data": {"text": text}})
    return {
        "time": int(time.time()),
        "self_id": int(self_id),
        "post_type": "message",
        "message_type": "group" if group_id else "private",
        "sub_type": "normal" if group_id else "friend",
        "message_id": int(time.time() * 1000) % 1000000,
        "user_id": user_id,
        "group_id": group_id,
        "message": segs,
        "raw_message": "".join(
            "[CQ:at,qq=%s]" % at_qq if at_qq else "" for _ in range(0)
        ) + text,
        "font": 0,
        "sender": {"user_id": user_id, "nickname": "e2e-tester"},
    }


async def _talk(events, token=TOKEN, wait: float = 12.0, self_id=SELF_ID):
    """连上 bridge 发事件，收集 bridge 主动发来的 action（并应答 API 调用）。

    self_id 必须每个用例各不相同：nonebot 对重复 X-Self-ID 会直接
    close(1008, "Duplicate X-Self-ID")，导致后续用例握手失败。
    """
    headers = {"X-Self-ID": str(self_id)}
    if token:
        headers["Authorization"] = "Bearer " + token

    actions = []
    async with websockets.connect(WS_URL, additional_headers=headers) as ws:
        for ev in events:
            await ws.send(json.dumps(ev))
            await asyncio.sleep(0.2)   # 让事件循环有机会处理

        loop = asyncio.get_event_loop()
        deadline = loop.time() + wait
        while loop.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
            except asyncio.TimeoutError:
                if actions:
                    break
                continue
            msg = json.loads(raw)
            if "action" in msg:
                actions.append(msg)
                # 必须应答，否则 nonebot 的 call_api 会一直等到超时
                await ws.send(json.dumps({
                    "status": "ok", "retcode": 0,
                    "data": {"message_id": 1}, "echo": msg.get("echo"),
                }))
    return actions


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _disconnect_gap():
    """用例之间留一点时间，确保上一个 bot 已从 bridge 注销。"""
    yield
    time.sleep(1.0)


# ---------------- §11 第 1 步：连通 ----------------

def test_ping_returns_pong(bridge):
    """私聊发 /ping，bridge 必须回一条含 pong 的私聊消息。"""
    sid = "10001"
    actions = run(_talk([_event("/ping", self_id=sid)], self_id=sid))
    sent = [a for a in actions if a.get("action") == "send_private_msg"]
    assert sent, "未收到 send_private_msg，实际收到：%s" % [a.get("action") for a in actions]
    content = json.dumps(sent[0].get("params", {}), ensure_ascii=False)
    assert "pong" in content


def test_help_returns_command_list(bridge):
    sid = "10002"
    actions = run(_talk([_event("/help", self_id=sid)], self_id=sid))
    sent = [a for a in actions if a.get("action") == "send_private_msg"]
    assert sent
    content = json.dumps(sent[0].get("params", {}), ensure_ascii=False)
    assert "文生图" in content


# ---------------- §3.2：群消息未 @机器人 必须静默丢弃 ----------------

def test_group_message_without_at_is_ignored(bridge):
    """群消息未 @机器人、且无命令前缀 → 一个包都不回（不消耗额度）。"""
    sid = "10003"
    actions = run(_talk([_event("大家好", group_id=30001, self_id=sid)], wait=6.0, self_id=sid))
    assert actions == [], "未 @机器人的群消息不应触发任何回包，实际：%s" % actions


def test_group_message_with_at_is_handled(bridge):
    """群消息 @机器人 → 正常受理。"""
    sid = "10004"
    actions = run(_talk([_event("/ping", group_id=30001, at_qq=sid, self_id=sid)], self_id=sid))
    sent = [a for a in actions if a.get("action") == "send_group_msg"]
    assert sent, "未收到 send_group_msg，实际：%s" % [a.get("action") for a in actions]


def test_group_at_bot_without_text_replies_help(bridge):
    """纯 @机器人（无文字）→ 回帮助提示（2026-09-04 用户决策：不再静默）。"""
    sid = "10006"
    actions = run(_talk([_event("", group_id=30001, at_qq=sid, self_id=sid)], self_id=sid))
    sent = [a for a in actions if a.get("action") == "send_group_msg"]
    assert sent, "未收到 send_group_msg，实际：%s" % [a.get("action") for a in actions]
    content = json.dumps(sent[0].get("params", {}), ensure_ascii=False)
    assert "文生图" in content


# ---------------- §3.1：Token 错配必须明确报错，不静默 ----------------

def test_wrong_token_is_rejected(bridge):
    sid = "10005"
    with pytest.raises(Exception) as exc:
        run(_talk([_event("/ping", self_id=sid)], token="wrong-token", self_id=sid))
    msg = str(exc.value)
    assert "403" in msg or "Invalid" in msg or "invalid" in msg, \
        "Token 错配应被明确拒绝（403），实际异常：%s" % msg
