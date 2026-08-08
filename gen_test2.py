import json, time, urllib.request, sys

API = "http://127.0.0.1:7788/api/v1"
sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None


def _post(p, b):
    req = urllib.request.Request(API + p, data=json.dumps(b).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


def _put(p, b):
    req = urllib.request.Request(API + p, data=json.dumps(b).encode(),
                                 headers={"Content-Type": "application/json"}, method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


def _get(p):
    return json.loads(urllib.request.urlopen(API + p, timeout=15).read().decode())


pr = _post("/projects", {"name": "GenTest2", "project_type": "short_drama", "is_draft": 0})
pid = pr["data"]["project"]["project_id"]
seg = _post("/projects/%s/segments" % pid, {"title": "片段一", "duration": 12})
sid = seg["data"]["segment"]["segment_id"]
_put("/projects/%s/segments/%s/shots" % (pid, sid),
     {"shots": [{"duration": 4, "description": "主角走入房间"},
                {"duration": 4, "description": "窗外雷雨"},
                {"duration": 4, "description": "两人对视"}]})
est = _get("/user/points?operation=estimate&duration=12&resolution=720p&audio=1")
cost = est["data"]["estimated_credits"]
idem = "idem2_" + str(int(time.time()))
pre = _post("/points/pre-auth", {"amount": cost, "operation_type": "segment_generate", "idempotency_key": idem})
paid = pre["data"]["pre_auth_id"]
gen = _post("/projects/%s/segments/%s/generate" % (pid, sid),
            {"pre_auth_id": paid, "params": {"duration": 12, "resolution": "720p", "ratio": "16:9",
                                             "audio": 1, "model_id": "seedance-2.0-mini"}})
tid = gen["data"]["task_id"]
print("TID=" + tid + " cost=" + str(cost), flush=True)
