#!/usr/bin/env python3
"""环境健康探测：检查导演 Agent 能力增强依赖的四项服务端口。

端口约定：
  7788 = 后端 API（manju_agent）
  8099 = 前端（web_app）
  8188 = ComfyUI（出图/出视频引擎）
  11434 = Ollama（LLM 本地推理）

服务未启动时诚实报告 DOWN，不伪造可用状态。返回码：全 UP=0，否则=1。
"""
import os
import sys
import json
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # manju_agent/

PORTS = {
    "backend_api": (7788, "/"),
    "frontend": (8099, "/"),
    "comfyui": (8188, "/"),
    "ollama": (11434, "/api/tags"),
}


def probe(name, port, path, timeout=2.0):
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"name": name, "port": port, "status": "UP", "code": r.status}
    except urllib.error.HTTPError as e:
        # 能被 TCP 连上但返回非 2xx，仍说明服务在运行
        return {"name": name, "port": port, "status": "UP", "code": e.code}
    except Exception as e:  # 连接拒绝 / 超时等
        return {"name": name, "port": port, "status": "DOWN", "reason": type(e).__name__}


def main():
    results = [probe(n, p, path) for n, (p, path) in PORTS.items()]
    up = sum(1 for r in results if r["status"] == "UP")
    print("=== 环境健康探测（导演 Agent 能力增强）===")
    for r in results:
        if r["status"] == "UP":
            print("  [UP  ] %-12s :%-5d (HTTP %s)" % (r["name"], r["port"], r.get("code")))
        else:
            print("  [DOWN] %-12s :%-5d (%s)" % (r["name"], r["port"], r.get("reason", "")))
    print("=== %d/%d 服务可用 ===" % (up, len(results)))
    if up != len(results):
        print("提示：DOWN 项为未启动的外部依赖（如 ComfyUI/Ollama），属正常；真实端到端生成需其运行。")
    return 0 if up == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
