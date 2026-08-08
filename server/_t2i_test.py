# -*- coding: utf-8 -*-
"""临时实测：提交文生图任务并轮询，验证 ComfyUI 真实出图 + /file 可访问。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:7788/api/v1"
ROOT = "http://127.0.0.1:7788"  # /file 路由在根路径，不在 /api/v1 下


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def head_file(name):
    try:
        with urllib.request.urlopen(ROOT + "/file?name=" + name, timeout=10) as r:
            return r.status, r.headers.get("Content-Type")
    except Exception as e:
        return None, str(e)


payload = {
    "prompt": "雨夜霓虹下的赛博朋克城市街景，湿润的街道反射彩色灯光，电影感",
    "negative": "low quality, blurry, watermark",
    "resolution": "1024",
    "ratio": "16:9",
    "style": "电影感",
    "steps": 20,
    "count": 1,
    "seed": 42,
    "enhance": True,
    "orchestrator": "ollama",
}
print("[1] submit t2i ...")
r = post("/t2i/generate", payload)
print("   ->", r)
tid = r["data"]["task_id"]
print("   task_id =", tid)

print("[2] poll ...")
for i in range(60):
    t = get("/t2i/tasks/" + tid)["data"]["task"]
    st = t["status"]
    prog = t.get("progress") or 0
    print("   [%ds] status=%s progress=%s err=%s" % (i * 5, st, prog, t.get("error_message")))
    if st in ("succeeded", "failed"):
        break
    time.sleep(5)

res = t.get("result") or {}
imgs = res.get("images") or []
print("[3] result images:", len(imgs))
for im in imgs:
    name = im["url"].split("name=")[-1]
    code, ctype = head_file(name)
    print("   %s -> http=%s ctype=%s seed=%s" % (name, code, ctype, im.get("seed")))
print("[4] orchestrator_used:", res.get("orchestrator_used"))
print("    prompt_used:", (res.get("prompt_used") or "")[:120])
print("DONE status=%s images=%d" % (st, len(imgs)))
