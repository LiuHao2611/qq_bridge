# -*- coding: utf-8 -*-
"""运行时 WS 交付验证（M3 悬浮球·P1-P5 导演事件契约）。

不与离线单测重复：单测只验证「函数生成了正确的事件对象」；本脚本验证
「事件确实通过生产 realtime.py 的 /ws 网关，经真实 TCP WebSocket 抵达订阅者」——
即前端（同为 WS 订阅者）在对应阶段真实收到发言/决策卡/选型/相似度。

使用与生产完全一致的 server/realtime.py 与 server/director/decision_engine.py，
仅将 DB 指向临时文件（不污染真实库）。用真实 uvicorn（ws=websockets-sansio，
与生产 :7788 同款协商）+ 真实 websockets 客户端，单进程线程化启动。
"""
import asyncio
import json
import os
import socket
import sys
import threading
import time
import urllib.request

# 必须最先设置 DB 路径（db 模块加载时解析 MANJU_DB_PATH），指向项目内临时文件。
_TMP_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       ".tmp", "rt_ws_orb.sqlite")
os.environ["MANJU_DB_PATH"] = _TMP_DB
os.environ.setdefault("MANJU_SINGLE_USER", "1")

from fastapi import FastAPI, WebSocket
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import realtime  # 生产模块（与 :7788 运行的同一份）
from director import decision_engine  # 生产模块

PORT = 18002
PID = "RTORB"
CH_DIR = "director:%s" % PID
CH_TRA = "translator:%s" % PID

app = FastAPI()


@app.websocket("/ws")  # 与 server/main.py 完全一致：装饰器注册 WebSocket
async def ws_endpoint(websocket: WebSocket):
    await realtime.handle_ws(websocket)


@app.post("/trigger/{pid}")
def trigger(pid: str):
    """复刻后端四处真实发射点（与 ai_local / routers / scheduler / post_production 一致）。"""
    # P1 悬浮球发言（ai_local.generate_character_card_v2 顶部）
    realtime.publish_director_notice(pid, {
        "message": "正在提取角色 X 的五要素（年龄/外貌/服装/气质/时代）",
        "phase": "P1",
    })
    # P1 决策卡（routers.extract_assets / generate_character_card_v2_endpoint 调 preflight_check）
    decision_engine.preflight_check(pid, "P1")
    # P3 选型推送（sequence_scheduler 经 publish_translator_selection）
    realtime.publish_translator_selection(pid, "trace-1", "shot_gen", {
        "checkpoint": "sd15写实底模",
        "node": "IP-Adapter",
        "ip_adapter_weight": 0.85,
        "loras": ["民国LoRA"],
        "seed": 123,
    })
    # P5 品控结论（post_production 合成成功后）
    realtime.publish_director_insight(pid, {
        "message": "合成完成，人脸相似度 0.82，达标",
        "phase": "P5",
        "face_similarity": 0.82,
        "pass": True,
    })
    return {"ok": True}


def _wait_port(host, port, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


async def _trigger_http():
    url = "http://127.0.0.1:%d/trigger/%s" % (PORT, PID)

    def _post():
        req = urllib.request.Request(url, data=b"", method="POST")
        return urllib.request.urlopen(req, timeout=5).read()

    await asyncio.to_thread(_post)


async def _client():
    import websockets
    uri = "ws://127.0.0.1:%d/ws" % PORT
    received = []
    async with websockets.connect(uri, open_timeout=5) as ws:
        await ws.send(json.dumps({
            "action": "subscribe",
            "payload": {"channels": [CH_DIR, CH_TRA]},
        }))
        while True:
            m = json.loads(await ws.recv())
            if m.get("event") == "subscribed":
                break
        await _trigger_http()
        try:
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.5))
                ch = m.get("channel")
                ev = m.get("event")
                if ch in (CH_DIR, CH_TRA) and ev in ("notice", "decision_request", "selection", "insight"):
                    received.append(m)
        except asyncio.TimeoutError:
            pass
    return received


def main():
    import db
    db.init_db()  # 隔离 DB（与生产库无关），并建表

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT,
                            ws="websockets-sansio", log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    assert _wait_port("127.0.0.1", PORT, 10.0), "测试服务未起"

    received = asyncio.run(_client())
    server.should_exit = True

    # ---- 断言 ----
    by = {(m["channel"], m["event"]): m for m in received}
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    n = by.get((CH_DIR, "notice"))
    check("P1 悬浮球发言 notice 抵达", n is not None,
          ("message=%s" % n["data"].get("message")) if n else "未收到")
    if n:
        check("  └ 文案含「五要素」", "五要素" in (n["data"].get("message") or ""),
              n["data"].get("message"))

    d = by.get((CH_DIR, "decision_request"))
    check("P1 决策卡 decision_request 抵达（真实 preflight_check 发射）", d is not None,
          "未收到" if not d else "")
    if d:
        titles = [x.get("title") for x in d["data"].get("decisions", [])]
        check("  └ 含「整体视觉风格定调」", "整体视觉风格定调" in titles, str(titles))
        check("  └ 含「角色是否使用真人参考」", "角色是否使用真人参考" in titles, str(titles))
        check("  └ 决策带 decision_id 且 pending",
              all(x.get("decision_id") and x.get("status") == "pending"
                  for x in d["data"].get("decisions", [])), "")

    s = by.get((CH_TRA, "selection"))
    check("P3 选型 selection 抵达 translator 频道", s is not None, "未收到" if not s else "")
    if s:
        sel = s["data"].get("selected") or {}
        check("  └ 含 checkpoint（写实底模）", bool(sel.get("checkpoint")), str(sel.get("checkpoint")))
        check("  └ 含 loras（民国LoRA）", "民国LoRA" in (sel.get("loras") or []), str(sel.get("loras")))

    i = by.get((CH_DIR, "insight"))
    check("P5 品控结论 insight 抵达", i is not None, "未收到" if not i else "")
    if i:
        check("  └ 人脸相似度 0.82 且 pass=True",
              i["data"].get("face_similarity") == 0.82 and i["data"].get("pass") is True,
              "face_similarity=%s pass=%s" % (i["data"].get("face_similarity"), i["data"].get("pass")))

    print("=" * 64)
    print("M3 悬浮球·P1-P5 导演事件 · 运行时 WS 交付验证")
    print("=" * 64)
    for name, ok, detail in results:
        print("%s %s%s" % ("[PASS]" if ok else "[FAIL]", name, ("  (%s)" % detail) if detail else ""))
    npass = sum(1 for _, ok, _ in results if ok)
    print("-" * 64)
    print("合计 %d/%d 通过" % (npass, len(results)))
    print("收到事件:", [(m["channel"], m["event"]) for m in received])
    print("=" * 64)
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
