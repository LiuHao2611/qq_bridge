# -*- coding: utf-8 -*-
"""Phase 5 合成全集：真实 HTTP 端到端验证（走 /api/v1 + 真实 worker 线程）。

流程：造片段 → 建 project+2 succeeded segments → POST /points/pre-auth → POST /synthesize
→ 轮询 GET /tasks/:id → 下载 result_url → 校验时长 ≈10s → 清理。
"""
import os, sys, time, re, json, subprocess, urllib.request

sys.path.insert(0, os.path.join(os.getcwd(), "server"))
import db
from db import get_conn, uid, SINGLE_USER_ID
import ai_local

OUT = ai_local.OUTPUTS
FF = ai_local.FFMPEG_BIN
BASE = "http://127.0.0.1:7788/api/v1"


def make_clip(path, color, dur=5):
    subprocess.run([FF, "-nostdin", "-y", "-f", "lavfi", "-i",
                    "color=c=%s:s=640x360:d=%d:r=25" % (color, dur),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=60)


def post(path, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def getj(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())


def ff_duration(path):
    r = subprocess.run([FF, "-i", path], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "ignore"))
    if not m:
        return None
    h, mm, ss = map(float, m.groups())
    return h * 3600 + mm * 60 + ss


ca = os.path.join(OUT, "task_httpA.mp4")
cb = os.path.join(OUT, "task_httpB.mp4")
make_clip(ca, "red", 5)
make_clip(cb, "green", 5)
print("clips:", os.path.getsize(ca), os.path.getsize(cb))

pid = "http_syn_" + str(int(time.time()))
conn = get_conn()
conn.execute("INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
             (pid, SINGLE_USER_ID, "http_e2e"))
sids = []
for i, c in enumerate((ca, cb)):
    sid = uid()
    sids.append(sid)
    conn.execute(
        'INSERT INTO segments (segment_id, project_id, "index", title, duration, video_url, status) '
        "VALUES (?,?,?,?,?,?,?)",
        (sid, pid, i, "seg%d" % i, 5, "/file?name=" + os.path.basename(c), "succeeded"))
conn.commit()
conn.close()

pa = post("/points/pre-auth",
          {"amount": 2160, "segment_id": "synthesize", "idempotency_key": "idem_http_" + str(int(time.time()))})
print("pre-auth resp code:", pa.get("code"), "| pre_auth_id:", pa.get("data", {}).get("pre_auth_id"))
pre_id = pa["data"]["pre_auth_id"]

syn = post("/synthesize", {"project_id": pid, "pre_auth_id": pre_id, "options": {}})
print("synthesize resp code:", syn.get("code"), "| task_id:", syn.get("data", {}).get("task_id"))
task_id = syn["data"]["task_id"]

status = "queued"
task = None
deadline = time.time() + 90
while time.time() < deadline:
    res = getj("/tasks/%s" % task_id)
    task = res["data"]["task"]
    status = task["status"]
    print("poll status:", status)
    if status in ("succeeded", "failed"):
        break
    time.sleep(2)

print("FINAL status:", status, "| result_url:", task.get("result_url"), "| err:", task.get("error_message"))
ok = False
if status == "succeeded":
    ru = task["result_url"]
    with urllib.request.urlopen("http://127.0.0.1:7788" + ru, timeout=30) as r:
        blob = r.read()
    out = os.path.join(OUT, "http_download.mp4")
    with open(out, "wb") as f:
        f.write(blob)
    d = ff_duration(out)
    ok = d is not None and abs(d - 10.0) < 0.5
    print("downloaded bytes:", len(blob), "| duration:", d, "->", "PASS" if ok else "FAIL")
    os.remove(out)

# ---- 清理 ----
try:
    conn = get_conn()
    conn.execute("DELETE FROM generation_logs WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM generation_tasks WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM segments WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM point_transactions WHERE idempotency_key LIKE 'idem_http%'")
    conn.execute("DELETE FROM point_transactions WHERE idempotency_key LIKE 'refund-%'")
    conn.commit()
    conn.close()
except Exception as e:
    print("cleanup err:", e)
for f in (ca, cb):
    if os.path.isfile(f):
        try:
            os.remove(f)
        except Exception:
            pass
print("HTTP_E2E", "PASS" if (status == "succeeded" and ok) else "FAIL", "| DONE")
