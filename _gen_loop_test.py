import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:7788/api/v1"

def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

print("=== health ===")
print(req("GET", "/health")[1]["data"]["status"])

# 1) 建项目
s, p = req("POST", "/projects", {"name": "闭环验证", "preset_id": "flash-long", "ratio": "16:9", "resolution": "720p"})
pid = p["data"]["project"]["project_id"]
# 2) 建片段(12s)
s, seg = req("POST", "/projects/%s/segments" % pid, {"title": "片段一", "duration": 12, "resolution": "720p", "ratio": "16:9"})
sid = seg["data"]["segment"]["segment_id"]
# 3) 放 3 个分镜(duration 和=12) 带 prompt/negative/setting
shots = [
    {"shot_id": "g1", "index": 1, "duration": 4, "shot_type": "medium", "camera_movement": "static", "camera_angle": "eye_level", "focal_length": "35mm", "description": "雨夜咖啡馆", "voiceover": "", "dialogue": "", "image_prompt": "rainy night cafe", "negative_prompt": "blur", "setting": "深夜", "asset_refs": []},
    {"shot_id": "g2", "index": 2, "duration": 4, "shot_type": "close_up", "camera_movement": "dolly", "camera_angle": "eye_level", "focal_length": "50mm", "description": "人物特写", "voiceover": "", "dialogue": "", "image_prompt": "portrait", "negative_prompt": "", "setting": "卡座", "asset_refs": []},
    {"shot_id": "g3", "index": 3, "duration": 4, "shot_type": "long", "camera_movement": "static", "camera_angle": "high_angle", "focal_length": "24mm", "description": "窗外人影", "voiceover": "", "dialogue": "", "image_prompt": "silhouette window", "negative_prompt": "", "setting": "窗边", "asset_refs": []},
]
s, r = req("PUT", "/projects/%s/segments/%s/shots" % (pid, sid), {"shots": shots})
print("put_shots:", s, "count", len(r["data"]["shots"]))

# 4) 预授权 + 提交
s, pa = req("POST", "/points/pre-auth", {"amount": 3000, "segment_id": sid, "idempotency_key": "loop_%d" % int(time.time())})
pre = pa["data"]["pre_auth_id"]
s, g = req("POST", "/projects/%s/segments/%s/generate" % (pid, sid),
           {"pre_auth_id": pre, "params": {"duration": 12, "resolution": "720p", "ratio": "16:9", "audio": 0, "model_id": "seedance-2.0-mini", "asset_humanize": 0, "return_last_frame": 0, "seed": -1}})
tid = g["data"]["task_id"]
print("task_id =", tid, "status =", g["data"]["status"])

# 5) 轮询(最长 ~6 分钟)
final = None
for i in range(120):
    s, t = req("GET", "/tasks/%s" % tid)
    tk = t["data"]["task"]
    st = tk["status"]
    if i % 6 == 0 or st in ("succeeded", "failed", "cancelled"):
        print("  [%3ds] status=%s" % (i * 3, st))
    if st in ("succeeded", "failed", "cancelled"):
        final = st
        print("FINAL:", st, "result_url=", tk.get("result_url"), "note=", tk.get("error_message"))
        break
    time.sleep(3)

print("RESULT:", final)
# 6) 清理
req("DELETE", "/projects/%s" % pid)
print("cleanup done")
