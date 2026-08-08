import json, os, time, sqlite3, subprocess, urllib.request, urllib.error

BASE = "http://127.0.0.1:7788/api/v1"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUT, exist_ok=True)

def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def ff_path(name):
    return os.path.join(OUT, name)

# 1) 建项目
s, p = req("POST", "/projects", {"name": "合成全集测试", "type": "short", "resolution": "720p", "ratio": "16:9"})
assert s == 200, p
pid = p["data"]["project"]["project_id"]
print("project:", pid)

# 2) 建 2 个片段
seg_ids = []
for i in range(2):
    s, seg = req("POST", "/projects/%s/segments" % pid,
                 {"title": "片段%d" % (i+1), "duration": 5, "resolution": "720p", "ratio": "16:9"})
    assert s == 200, seg
    seg_ids.append(seg["data"]["segment"]["segment_id"])
print("segments:", seg_ids)

# 3) 用 ffmpeg 造 2 个 5s 测试卡，写入 outputs/ 并标记片段 succeeded
for i, sid in enumerate(seg_ids):
    name = "task_test_%s.mp4" % sid
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x10131a:s=1280x720:d=5",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25", ff_path(name)]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "app.db"))
    conn.execute("UPDATE segments SET status='succeeded', video_url=? WHERE segment_id=?",
                 ("/file?name=" + name, sid))
    conn.commit(); conn.close()
print("seeded 2 segment videos")

# 4) 预授权
s, pre = req("POST", "/points/pre-auth", {"amount": 10, "operation_type": "synthesize",
            "idempotency_key": "idem_test_%d" % int(time.time())})
assert s == 200, pre
paid = pre["data"].get("pre_auth_id") or pre["data"].get("txn_id")
print("pre_auth_id:", paid)

# 5) 提交合成全集
s, syn = req("POST", "/synthesize", {"project_id": pid, "pre_auth_id": paid})
assert s == 200, syn
tid = syn["data"]["task_id"]
print("synthesize task:", tid)

# 6) 轮询
for _ in range(60):
    s, t = req("GET", "/tasks/%s" % tid)
    st = t.get("data", {}).get("status") if s == 200 else None
    if st in ("succeeded", "failed"):
        print("task status:", st, t.get("data", {}).get("result_url"))
        break
    time.sleep(2)

# 7) 校验产物
synth_name = "synth_%s.mp4" % pid
synth_path = ff_path(synth_name)
exists = os.path.isfile(synth_path)
dur = 0
if exists:
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", synth_path],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    dur = float(probe.stdout.decode().strip() or 0)
print("synth mp4 exists:", exists, "duration:", round(dur, 1), "s")

# 8) 校验任务终态 + 任务中心可回溯（generation_tasks/generation_logs 已落库）
s, tf = req("GET", "/tasks/%s" % tid)
print("final task status:", tf.get("data", {}).get("task", {}).get("status"),
      "result_url:", tf.get("data", {}).get("task", {}).get("result_url"))

# 9) 清理
conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "app.db"))
conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
conn.execute("DELETE FROM segments WHERE project_id=?", (pid,))
conn.execute("DELETE FROM generation_tasks WHERE task_id=?", (tid,))
conn.commit(); conn.close()
for f in [synth_path] + [ff_path("task_test_%s.mp4" % s) for s in seg_ids] + [ff_path("synth_concat_%s.txt" % pid)]:
    if os.path.exists(f):
        try: os.remove(f)
        except: pass
print("cleaned up")
assert exists and dur >= 9, "合成全集失败"
print("PASS")
