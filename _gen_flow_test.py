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
print(req("GET", "/health")[1]["data"])

# 1) 建项目
s, p = req("POST", "/projects", {"name": "闭环测试", "preset_id": "flash-long", "ratio": "16:9", "resolution": "720p"})
pid = p["data"]["project"]["project_id"]
# 2) 建片段
s, seg = req("POST", "/projects/%s/segments" % pid, {"title": "片段一", "duration": 12, "resolution": "720p", "ratio": "16:9"})
sid = seg["data"]["segment"]["segment_id"]
# 3) 预授权积分（估算 cost 不易，直接冻结足够额度）
s, pa = req("POST", "/points/pre-auth", {"amount": 1000, "segment_id": sid, "idempotency_key": "test_%d" % int(time.time())})
print("pre_auth:", s, pa.get("data", {}).get("pre_auth_id"))
pre = pa["data"]["pre_auth_id"]
# 4) 提交生成
s, g = req("POST", "/projects/%s/segments/%s/generate" % (pid, sid),
           {"pre_auth_id": pre, "params": {"duration": 12, "resolution": "720p", "ratio": "16:9", "audio": 0, "model_id": "seedance-2.0-mini", "asset_humanize": 0, "return_last_frame": 0, "seed": -1}})
print("generate:", s, g.get("data"))
tid = g["data"]["task_id"]

# 5) 轮询任务状态（最多 ~40s，看是否推进）
print("\n--- polling task %s ---" % tid)
final = None
for i in range(14):
    s, t = req("GET", "/tasks/%s" % tid)
    st = t["data"]["task"]["status"]
    print("  [%2ds] status=%s" % (i * 3, st))
    if st in ("succeeded", "failed", "cancelled"):
        final = st
        break
    time.sleep(3)

print("\nFINAL task status =", final)
if final == "succeeded":
    print("result_url =", t["data"]["task"].get("result_url"))

# 6) 清理
req("DELETE", "/projects/%s" % pid)
print("cleanup deleted project")
