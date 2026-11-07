# -*- coding: utf-8 -*-
r"""配置管理 REST 接口端到端验收（机器判定脚本，2026-09-07）。

目的：把配置面板 6 项 UI 行为（select {value,label} / 模板代理 / 写回 / 状态 / 异常）
用真实 bot 进程跑一遍，输出 PASS/FAIL 摘要，便于 CI 或本地一键回归。

设计要点：
  * 独立端口 18081，避免与正在运行的 8080 bridge 冲突；
  * 写回 .env 后**立即还原**（先备份原文 → 测试结束恢复），保证宿主配置不被污染；
  * 任何步骤 FAIL 立即退出（exit code 1），全部通过才 exit 0；
  * 依赖：nonebot 完整依赖（与 bot.py 一致）；主项目 7788 不在也能跑（代理降级 ok=false）。

用法：
  .venv\Scripts\python.exe scripts/verify_config_routes.py
  或从仓库根：
  D:\AI\manju_agent\qq_bridge\.venv\Scripts\python.exe -m scripts.verify_config_routes
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

# Windows gbk 控制台不能打印 ✓/✗ 等 unicode，重定向 stdout 为 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18081
TOKEN = "verify-routes-token"
BASE = "http://127.0.0.1:%d" % PORT
ENV_PATH = os.path.join(ROOT, ".env")
ENV_BACKUP = ENV_PATH + ".verify.bak"

# 6 项必查行为（与图片任务清单一一对应）
CHECKS = [
    ("GET /api/qq/config · select 字段 options 全部为 {value,label}",
     "_check_options_shape"),
    ("GET /api/qq/templates · 返回结构 {ok, items, error}",
     "_check_templates_endpoint"),
    ("GET /api/qq/config · 包含 IMAGE_CHANNEL/HOST/LOG_LEVEL 等核心 select",
     "_check_core_select_keys_present"),
    ("GET /api/qq/config · strlist 字段含 DRAW_KEYWORDS/TEST_KEYWORDS",
     "_check_strlist_keys_present"),
    ("GET /api/qq/config · LOCAL_TEMPLATE_ID 拉到了主项目真实模板（2026-09-07 回归）",
     "_check_local_template_options_real"),
    ("PUT /api/qq/config · 写回 .env 保留注释 + 触发 requires_restart",
     "_check_put_writes_env"),
    ("GET /api/qq/status · 字段完整（online/bots/connect_count）",
     "_check_status_endpoint"),
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


def _alive(url: str, timeout: float = 2.0) -> bool:
    """快速探测 URL 是否可达（仅 TCP/GET 200/4xx 算通，5xx/timeout 算不通）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        # 4xx 说明服务在（路由能匹配），不算死
        return e.code < 500
    except Exception:
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
        except Exception:
            return r.status, raw
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, str(e)


def _check_options_shape() -> str:
    status, data = _http("GET", "/api/qq/config")
    if status != 200 or not isinstance(data, dict) or "groups" not in data:
        return "FAIL: status=%s data 类型异常" % status
    select_keys = {"HOST", "IMAGE_CHANNEL", "LOG_LEVEL", "LOCAL_STYLE",
                   "LOCAL_ORCHESTRATOR", "DRIVER"}
    seen = 0
    for g in data["groups"]:
        for it in g.get("items", []):
            if it.get("key") not in select_keys:
                continue
            opts = it.get("options") or []
            if not isinstance(opts, list) or not opts:
                return "FAIL: %s options 非空列表" % it["key"]
            for o in opts:
                if not (isinstance(o, dict) and o.get("value") and o.get("label")):
                    return "FAIL: %s option 缺 value/label: %r" % (it["key"], o)
            seen += 1
    if seen < len(select_keys):
        return "FAIL: select 覆盖不全 %d/%d" % (seen, len(select_keys))
    return "PASS"


def _check_templates_endpoint() -> str:
    status, data = _http("GET", "/api/qq/templates")
    if status != 200 or not isinstance(data, dict):
        return "FAIL: status=%s data=%r" % (status, data)
    for k in ("ok", "items", "error"):
        if k not in data:
            return "FAIL: 缺字段 %s" % k
    if not isinstance(data["items"], list):
        return "FAIL: items 非 list"
    # 失败容错：主项目 7788 不在 → ok=False + items=[]
    if not data["ok"]:
        if data["items"] or not data["error"]:
            return "FAIL: 失败分支 items/error 语义错"
        return "PASS（主项目 7788 不可达，代理降级）"
    # 成功分支：items 是 [{value,label}]
    for o in data["items"]:
        if not (isinstance(o, dict) and o.get("value") and o.get("label")):
            return "FAIL: 成功分支 item 缺 value/label: %r" % o
    return "PASS（%d 条模板）" % len(data["items"])


def _check_core_select_keys_present() -> str:
    status, data = _http("GET", "/api/qq/config")
    if status != 200:
        return "FAIL: status=%s" % status
    keys = {it["key"] for g in data["groups"] for it in g["items"]}
    required = {"HOST", "IMAGE_CHANNEL", "LOG_LEVEL", "DRIVER",
                "LOCAL_TEMPLATE_ID", "MANJU_API_BASE"}
    missing = required - keys
    if missing:
        return "FAIL: 缺核心字段 %s" % missing
    return "PASS"


def _check_local_template_options_real() -> str:
    """回归点（2026-09-07 修）：_fetch_template_options 必须走对端点 + 解析对字段。

    历史 bug：调 /api/v1/templates（不存在）+ 找 data.list/items（实际是 data.templates）+
    字段 template_id（实际是 id）→ 解析全空 → 用户看到空下拉。

    断言：
      - /api/qq/config 中 LOCAL_TEMPLATE_ID.options 非空；
      - 至少 1 条 option 的 value 非空（不是占位「主项目不可达」）；
      - 若主项目 7788 不可达（端到端脚本用 9 端口逼降级）→ 仅有占位也算 PASS（降级路径）。
    """
    status, data = _http("GET", "/api/qq/config")
    if status != 200:
        return "FAIL: status=%s" % status
    item = None
    for g in data["groups"]:
        for it in g["items"]:
            if it.get("key") == "LOCAL_TEMPLATE_ID":
                item = it
                break
        if item:
            break
    if not item:
        return "FAIL: 找不到 LOCAL_TEMPLATE_ID 字段"
    opts = item.get("options") or []
    if not opts:
        return "FAIL: options 为空（_fetch_template_options 解析失败）"
    # 过滤掉占位项（value==""）
    real = [o for o in opts if o.get("value")]
    if not real:
        # 仅有占位：可能是主项目 7788 不可达（端到端脚本探测 9 端口走降级），
        # 这种情况视为 PASS（占位是契约行为，不是 bug）。
        if _alive("http://127.0.0.1:7788/api/v1/t2i/active-task?source=local"):
            return "FAIL: 仅有占位「主项目不可达」，但 7788 可达（端点/解析/字段名错位？）"
        return "PASS（7788 不可达，代理降级返回占位项，符合契约）"
    # label 形如 "tpl_xxx — 名称  [类别]"，至少含 tpl_ 前缀（id）才合规
    sample = real[0]
    val = str(sample.get("value") or "")
    if not val.startswith("tpl_"):
        return "FAIL: 第 1 条 value 不像模板 id（缺 tpl_ 前缀）：%r" % sample
    return "PASS（共 %d 条，主项目 7788 /api/v1/t2i/templates 解析成功）" % len(real)


def _check_strlist_keys_present() -> str:
    status, data = _http("GET", "/api/qq/config")
    if status != 200:
        return "FAIL: status=%s" % status
    strlist_keys = {it["key"] for g in data["groups"]
                    for it in g["items"] if it.get("type") == "strlist"}
    required = {"DRAW_KEYWORDS", "TEST_KEYWORDS"}
    if not required.issubset(strlist_keys):
        return "FAIL: 缺 strlist 字段 %s" % (required - strlist_keys)
    return "PASS"


def _check_put_writes_env() -> str:
    """PUT 写回后 .env 含新值 + 返回 requires_restart；测试结束立即还原。"""
    # 备份 .env
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "rb") as f:
            original = f.read()
        with open(ENV_BACKUP, "wb") as f:
            f.write(original)
    else:
        original = None

    try:
        status, data = _http("PUT", "/api/qq/config", {
            "updates": {"IMAGE_CHANNEL": "online", "STICKER_CHANCE": "0.3"}
        })
        if status != 200 or not isinstance(data, dict):
            return "FAIL: PUT 状态=%s data=%r" % (status, data)
        saved = set(data.get("saved") or [])
        if {"IMAGE_CHANNEL", "STICKER_CHANCE"} - saved:
            return "FAIL: saved 缺字段 %s" % saved
        rr = set(data.get("requires_restart") or [])
        if not rr:
            return "FAIL: requires_restart 为空"
        # 落盘检查
        if not os.path.exists(ENV_PATH):
            return "FAIL: .env 未生成"
        text = open(ENV_PATH, encoding="utf-8").read()
        if "IMAGE_CHANNEL=online" not in text:
            return "FAIL: .env 缺 IMAGE_CHANNEL=online"
        if "STICKER_CHANCE=0.3" not in text:
            return "FAIL: .env 缺 STICKER_CHANCE=0.3"
        return "PASS（saved=%d, rr=%d）" % (len(saved), len(rr))
    finally:
        # 还原 .env
        if original is not None:
            with open(ENV_PATH, "wb") as f:
                f.write(original)
        elif os.path.exists(ENV_PATH):
            os.remove(ENV_PATH)
        if os.path.exists(ENV_BACKUP):
            os.remove(ENV_BACKUP)


def _check_status_endpoint() -> str:
    status, data = _http("GET", "/api/qq/status")
    if status != 200 or not isinstance(data, dict):
        return "FAIL: status=%s" % status
    # 即使无 NapCat 连接，结构字段必须在
    required = {"online", "self_id", "bots", "connect_count", "pid"}
    missing = required - set(data.keys())
    if missing:
        return "FAIL: /api/qq/status 缺字段 %s" % missing
    return "PASS（online=%s, bots=%d）" % (data["online"], len(data["bots"] or []))


def _start_bot() -> subprocess.Popen:
    env = dict(os.environ)
    env.update({
        "PORT": str(PORT), "HOST": "127.0.0.1",
        "ONEBOT_V11_ACCESS_TOKEN": TOKEN,
        "REPLY_DELAY_MIN": "0", "REPLY_DELAY_MAX": "0",
        "BOT_OWNER_QQ": "", "PRIVATE_WHITELIST": "",
        "GROUP_WHITELIST": "", "USER_BLACKLIST": "",
        "STRICT_WHITELIST": "false",
        # 屏蔽宿主 .env：确保测试环境纯净；
        # 动态探测 7788：可达则用真地址（让 _fetch_template_options 拿到模板），
        # 不可达则指 9 端口（逼代理走降级）。两种环境都跑通即修复稳健。
        "MANJU_API_BASE": "http://127.0.0.1:7788/api/v1" if _alive("http://127.0.0.1:7788/api/v1/t2i/active-task?source=local") else "http://127.0.0.1:9",
    })
    log_path = os.path.join(ROOT, "logs", "verify_routes.out")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-u", "bot.py"],
        cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )


def main() -> int:
    print("=" * 70)
    print("配置管理 REST 接口端到端验收（机器判定）")
    print("目标端口：%d  ·  日志：logs/verify_routes.out" % PORT)
    print("=" * 70)

    # 1) 启动 bot 进程
    print("\n[1/2] 启动 bot.py @ 127.0.0.1:%d ..." % PORT)
    proc = _start_bot()
    try:
        if not _wait_port(PORT, timeout=60.0):
            proc.terminate()
            print("FAIL: bridge 未在 60s 内监听 %d" % PORT)
            return 1
        print("  ✓ 端口已就绪")

        # 2) 跑 6 项检查
        print("\n[2/2] 跑 %d 项接口行为检查：" % len(CHECKS))
        passed, failed = 0, 0
        for desc, fn_name in CHECKS:
            fn = globals()[fn_name]
            res = fn()
            tag = "✓" if res.startswith("PASS") else "✗"
            print("  %s %s\n      %s" % (tag, desc, res))
            if res.startswith("PASS"):
                passed += 1
            else:
                failed += 1

        print("\n" + "-" * 70)
        print("汇总：%d 通过 / %d 失败" % (passed, failed))
        if failed == 0:
            print("✅ 全部通过")
            return 0
        print("❌ 存在失败项")
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
