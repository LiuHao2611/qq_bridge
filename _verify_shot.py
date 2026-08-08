# -*- coding: utf-8 -*-
"""造一个「合成全集」任务并截图任务中心页面（展示 succeeded 的合成任务）。"""
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


ca = os.path.join(OUT, "task_shotA.mp4")
cb = os.path.join(OUT, "task_shotB.mp4")
make_clip(ca, "red", 5)
make_clip(cb, "teal", 5)

pid = "shot_syn_" + str(int(time.time()))
conn = get_conn()
conn.execute("INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
             (pid, SINGLE_USER_ID, "shot_e2e"))
for i, c in enumerate((ca, cb)):
    sid = uid()
    conn.execute(
        'INSERT INTO segments (segment_id, project_id, "index", title, duration, video_url, status) '
        "VALUES (?,?,?,?,?,?,?)",
        (sid, pid, i, "seg%d" % i, 5, "/file?name=" + os.path.basename(c), "succeeded"))
conn.commit()
conn.close()

pa = post("/points/pre-auth",
          {"amount": 2160, "segment_id": "synthesize", "idempotency_key": "idem_shot_" + str(int(time.time()))})
pre_id = pa["data"]["pre_auth_id"]
syn = post("/synthesize", {"project_id": pid, "pre_auth_id": pre_id, "options": {}})
task_id = syn["data"]["task_id"]
print("created synthesize task:", task_id)

status = "queued"
deadline = time.time() + 90
while time.time() < deadline:
    status = getj("/tasks/%s" % task_id)["data"]["task"]["status"]
    print("poll:", status)
    if status in ("succeeded", "failed"):
        break
    time.sleep(2)
print("FINAL:", status)

# ---- Playwright 截图任务中心 ----
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto("http://127.0.0.1:8099/tasks", wait_until="networkidle", timeout=30000)
        pg.wait_for_timeout(2500)
        pg.screenshot(path="synth_tasks_page.png")
        print("screenshot -> synth_tasks_page.png")
        b.close()
except Exception as e:
    print("screenshot skipped:", repr(e))

# ---- 清理 ----
try:
    conn = get_conn()
    conn.execute("DELETE FROM generation_logs WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM generation_tasks WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM segments WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM point_transactions WHERE idempotency_key LIKE 'idem_shot%'")
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
print("DONE")
