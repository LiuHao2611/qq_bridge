# -*- coding: utf-8 -*-
r"""对话页「快捷操作」7 个写端点端到端验收（机器判定，2026-09-07）。

覆盖第一期 7 项：禁言/解禁、踢人、改群名片、拉黑（热生效）、全员禁言、私聊、撤回。

设计要点（与 verify_config_routes.py 同一模式，避免重复造轮子）：
  * 独立端口 18082，避免与生产 8080 bridge 及 18081 其它脚本冲突；
  * 本实例**没有 NapCat 连接**（NapCat 只连 8080），connector.online=False，
    正好用来验证**离线降级**——6 个 OneBot 端点必须返回 503 + code=offline，
    绝不能让前端误以为操作成功（写操作最重要的防护）；
  * 黑名单端点不依赖在线，真实验证**热生效**：拉黑后 GET /api/qq/groups 立即
    能看到该 uid（无需重启 bridge），并写回 .env 持久化；
  * .env 先备份、结束还原，绝不污染宿主配置；
  * 任一项 FAIL 即 exit 1，全过 exit 0（机器判定，禁止人工读日志自判）。

用法：
  python scripts/verify_quick_actions.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Windows gbk 控制台不能打印 ✓/✗ 等 unicode，重定向 stdout 为 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18082
TOKEN = "verify-actions-token"
BASE = "http://127.0.0.1:%d" % PORT
ENV_PATH = os.path.join(ROOT, ".env")
ENV_BACKUP = ENV_PATH + ".verify_actions.bak"

# 测试用 QQ：故意用一个不存在的号，任何真实路径都不会伤及真实用户
TEST_UID = 99119911

# 6 个依赖 NapCat 在线的端点（离线时必须 503）
ONEBOT_ENDPOINTS = [
    ("/api/qq/group_member/ban", {"group_id": 1, "user_id": 1, "duration": 60}),
    ("/api/qq/group_member/kick", {"group_id": 1, "user_id": 1}),
    ("/api/qq/group_member/card", {"group_id": 1, "user_id": 1, "card": "x"}),
    ("/api/qq/group/whole_ban", {"group_id": 1, "enable": False}),
    ("/api/qq/send_msg", {"scene": "private", "user_id": 1, "message": "x"}),
    ("/api/qq/recall_msg", {"message_id": 1}),
]

CHECKS = [
    ("参数校验 · 缺必填参数被拒绝（在线=400，离线=503 离线优先）", "_check_missing_params"),
    ("离线降级 · 6 个 OneBot 端点返回 503 + code=offline（不让前端误判成功）", "_check_offline_503"),
    ("黑名单热生效 · 拉黑后 GET /api/qq/groups 立即可见（无需重启）", "_check_blacklist_hot_reload"),
    ("黑名单持久化 + 解除 · 写回 .env 且移出后立即消失", "_check_blacklist_persist_and_remove"),
]


def _wait_port(port: int, timeout: float = 60.0) -> bool:
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


def _http(method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        raw = r.read().decode("utf-8")
        try:
            return r.status, json.loads(raw)
        except Exception:  # noqa: BLE001
            return r.status, raw
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, str(e)


def _check_missing_params() -> str:
    """缺必填参数必须被拒绝，绝不能 200 静默成功。

    状态码取决于在线与否（**离线优先**设计）：
      * 在线 → 400（纯参数错误，准确语义）；
      * 离线 → 503（此时参数对也执行不了，优先告知"机器人离线"比"参数错误"
        更有指导性，避免用户对着参数排查半天）。
    两种都必须是 ok=false + 带 error 文案，绝不返回成功。
    """
    _st, st = _http("GET", "/api/qq/status")
    online = isinstance(st, dict) and bool(st.get("online"))
    expect = 400 if online else 503

    # 6 个转发给 NapCat 的端点：受在线状态影响（离线优先 → 503）
    onebot_cases = [
        "/api/qq/group_member/ban",
        "/api/qq/group_member/kick",
        "/api/qq/group_member/card",
        "/api/qq/group/whole_ban",
        "/api/qq/send_msg",
        "/api/qq/recall_msg",
    ]
    # 黑名单是本地配置（改 Config + 写 .env），不依赖在线，缺参数恒为 400
    local_cases = ["/api/qq/group_member/blacklist"]

    for path in onebot_cases:
        status, data = _http("POST", path, {})
        if status != expect:
            return "FAIL: %s 缺参数应 %d（online=%s），实际 %s (%r)" % (
                path, expect, online, status, data)
        if not isinstance(data, dict) or data.get("ok") is not False:
            return "FAIL: %s 缺参数应返回 ok=false，实际 %r" % (path, data)
        if not data.get("error"):
            return "FAIL: %s 缺参数应带 error 文案，实际 %r" % (path, data)

    for path in local_cases:
        status, data = _http("POST", path, {})
        if status != 400:
            return "FAIL: %s 不依赖在线，缺参数应恒 400，实际 %s (%r)" % (path, status, data)
        if not isinstance(data, dict) or data.get("ok") is not False or not data.get("error"):
            return "FAIL: %s 缺参数应返回 ok=false + error，实际 %r" % (path, data)

    return "PASS（7/7 端点拒绝缺参数：6 个在线端点→%d，黑名单本地端点→400）" % expect


def _check_offline_503() -> str:
    """无 NapCat 连接时，依赖在线的 6 个端点必须 503 + code=offline。"""
    # 先确认本实例确实离线（否则后面的 503 断言不成立）
    _st, st = _http("GET", "/api/qq/status")
    if isinstance(st, dict) and st.get("online"):
        return "SKIP（本实例 NapCat 已连接，无法验证离线降级；请在无连接实例上跑）"
    for path, body in ONEBOT_ENDPOINTS:
        status, data = _http("POST", path, body)
        if status != 503:
            return "FAIL: %s 离线应 503，实际 %s (%r)" % (path, status, data)
        if not isinstance(data, dict) or data.get("code") != "offline":
            return "FAIL: %s 离线应 code=offline，实际 %r" % (path, data)
        if not data.get("error"):
            return "FAIL: %s 离线应给出可执行的 error 文案，实际 %r" % (path, data)
    return "PASS（6/6 端点离线降级 503）"


def _check_blacklist_hot_reload() -> str:
    """核心：拉黑后无需重启，GET /api/qq/groups 立即能读到（热生效）。"""
    status, data = _http("POST", "/api/qq/group_member/blacklist",
                         {"user_id": TEST_UID, "blacklisted": True})
    if status != 200 or not isinstance(data, dict) or not data.get("ok"):
        return "FAIL: 拉黑请求失败 status=%s data=%r" % (status, data)
    if data.get("requires_restart") is not False:
        return "FAIL: 黑名单应热生效（requires_restart=false），实际 %r" % data

    # 不重启 bridge，直接读：必须立刻看到
    _s2, groups = _http("GET", "/api/qq/groups")
    if not isinstance(groups, dict):
        return "FAIL: /api/qq/groups 返回异常 %r" % (groups,)
    bl = [int(x) for x in (groups.get("blacklist") or [])]
    if TEST_UID not in bl:
        return "FAIL: 拉黑后 /api/qq/groups 未立即生效（热生效失败），blacklist=%r" % (bl,)
    return "PASS（拉黑立即生效，未重启 bridge）"


def _check_blacklist_persist_and_remove() -> str:
    """拉黑写回 .env（重启后仍在），解除后立即从视图消失。"""
    # 持久化：.env 里应出现该 uid
    try:
        env_text = open(ENV_PATH, encoding="utf-8").read()
    except Exception as e:  # noqa: BLE001
        return "FAIL: 读 .env 失败 %s" % e
    if str(TEST_UID) not in env_text:
        return "FAIL: 拉黑未写回 .env（重启后会丢），.env 片段=%r" % env_text[-200:]

    # 解除：立即消失（热生效双向）
    status, data = _http("POST", "/api/qq/group_member/blacklist",
                         {"user_id": TEST_UID, "blacklisted": False})
    if status != 200 or not isinstance(data, dict) or not data.get("ok"):
        return "FAIL: 解除黑名单失败 status=%s data=%r" % (status, data)

    _s, groups = _http("GET", "/api/qq/groups")
    bl = [int(x) for x in ((groups or {}).get("blacklist") or [])]
    if TEST_UID in bl:
        return "FAIL: 解除后仍在黑名单中 %r" % (bl,)
    return "PASS（持久化 + 解除均生效）"


def _start_bot() -> subprocess.Popen:
    env = dict(os.environ)
    env.update({
        "PORT": str(PORT), "HOST": "127.0.0.1",
        "ONEBOT_V11_ACCESS_TOKEN": TOKEN,
    })
    log_path = os.path.join(ROOT, "logs", "verify_actions.out")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-u", "bot.py"],
        cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )


def main() -> int:
    print("=" * 70)
    print("对话页快捷操作 7 端点端到端验收（机器判定）")
    print("目标端口：%d  ·  日志：logs/verify_actions.out" % PORT)
    print("=" * 70)

    # 备份 .env（黑名单端点会写回 .env，结束后必须还原）
    original = None
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "rb") as f:
            original = f.read()
        with open(ENV_BACKUP, "wb") as f:
            f.write(original)

    print("\n[1/2] 启动 bot.py @ 127.0.0.1:%d ..." % PORT)
    proc = _start_bot()
    ok = True
    try:
        if not _wait_port(PORT, timeout=60.0):
            proc.terminate()
            print("FAIL: bridge 未在 60s 内监听 %d" % PORT)
            return 1
        print("  ✓ 端口已就绪")

        print("\n[2/2] 执行 %d 项检查：" % len(CHECKS))
        for desc, fn in CHECKS:
            res = globals()[fn]()
            mark = "✓" if res.startswith("PASS") else ("○" if res.startswith("SKIP") else "✗")
            print("  %s %s\n      → %s" % (mark, desc, res))
            if not (res.startswith("PASS") or res.startswith("SKIP")):
                ok = False

        print("\n" + "=" * 70)
        if ok:
            print("✅ 全部通过（exit 0）")
            return 0
        print("❌ 存在失败项（exit 1）")
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        # 还原 .env
        if original is not None:
            with open(ENV_PATH, "wb") as f:
                f.write(original)
        if os.path.exists(ENV_BACKUP):
            os.remove(ENV_BACKUP)
        print("  · 已还原 .env，临时实例已停止")


if __name__ == "__main__":
    sys.exit(main())
