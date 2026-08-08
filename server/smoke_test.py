import json, urllib.request, urllib.error, sys, time

BASE = "http://127.0.0.1:7788/api/v1"

def req(method, path, body=None):
    url = BASE + path
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_error": True, "status": e.code, "body": e.read().decode("utf-8")[:500]}

def check(label, resp):
    if resp.get("_error"):
        print(f"FAIL {label}: HTTP {resp['status']} {resp['body']}")
        sys.exit(1)
    if resp.get("code") not in (0, None):
        print(f"FAIL {label}: code={resp.get('code')} msg={resp.get('message')}")
        sys.exit(1)
    print(f"OK   {label}")
    return resp.get("data", {})

print("=== health ===")
resp = req("GET", "/health")
check("health", resp)

print("=== profile ===")
data = check("profile", req("GET", "/user/profile"))
print("   points:", data.get("points_balance"))
assert data.get("points_balance") == 1999995162

print("=== create project ===")
data = check("create project", req("POST", "/projects", {"name": "冒烟短剧-2", "project_type": "short_drama", "ratio": "16:9", "resolution": "720p"}))
print("   project:", data)
pid = data["project"]["project_id"]

print("=== create segment ===")
data = check("create segment", req("POST", f"/projects/{pid}/segments", {"title": "第一幕", "duration": 12, "resolution": "720p", "ratio": "16:9", "audio_enabled": 1, "asset_humanize": 0, "return_last_frame": 0}))
print("   segment:", data)
sid = data["segment"]["segment_id"]
assert data["segment"]["index"] == 1

shots = [
    {"shot_id": "s1", "index": 1, "duration": 3, "content": "主角走进房间", "image_prompt": "a room", "location": "室内", "time": "白天", "shot_type": "full", "camera_movement": "static", "camera_angle": "eye_level", "focal_length": "35mm", "action": "走路", "character": "阿杰", "props": "钥匙"},
    {"shot_id": "s2", "index": 2, "duration": 3, "content": "特写钥匙", "image_prompt": "key", "location": "室内", "time": "白天", "shot_type": "close_up", "camera_movement": "static", "camera_angle": "eye_level", "focal_length": "50mm", "action": "拿取", "character": "阿杰", "props": "钥匙"},
    {"shot_id": "s3", "index": 3, "duration": 3, "content": "门外黑影", "image_prompt": "shadow", "location": "门口", "time": "夜晚", "shot_type": "medium", "camera_movement": "pan", "camera_angle": "low_angle", "focal_length": "35mm", "action": "出现", "character": "黑影", "props": "无"},
    {"shot_id": "s4", "index": 4, "duration": 3, "content": "主角回头", "image_prompt": "turn back", "location": "室内", "time": "夜晚", "shot_type": "medium_close", "camera_movement": "static", "camera_angle": "eye_level", "focal_length": "85mm", "action": "回头", "character": "阿杰", "props": "无"},
]
print("=== put shots ===")
data = check("put shots", req("PUT", f"/projects/{pid}/segments/{sid}/shots", {"shots": shots}))
print("   shots count:", len(data.get("shots", [])))
assert len(data["shots"]) == 4

print("=== create assets ===")
role_asset = check("create role asset", req("POST", "/assets", {"name": "阿杰", "type": "role", "tags": ["主角", "现代"]}))
scene_asset = check("create scene asset", req("POST", "/assets", {"name": "客厅", "type": "scene", "tags": ["室内"]}))
print("   role asset:", role_asset.get("asset", {}).get("asset_id"))
print("   scene asset:", scene_asset.get("asset", {}).get("asset_id"))

print("=== put asset_refs ===")
data = check("put asset_refs", req("PUT", f"/projects/{pid}/segments/{sid}/asset-refs", {"asset_refs": [
    {"asset_id": role_asset["asset"]["asset_id"], "asset_type": "role", "stage_id": "latest", "used_in": "shot"},
    {"asset_id": scene_asset["asset"]["asset_id"], "asset_type": "scene", "stage_id": "latest", "used_in": "shot"},
]}))
print("   refs count:", len(data.get("asset_refs", [])))

print("=== points estimate ===")
data = check("estimate", req("GET", f"/user/points?operation=estimate&duration=12&resolution=720p&audio=1&model_id=seedance-2.0-mini"))
print("   estimated:", data.get("estimated_credits"))

print("=== pre-auth points ===")
import uuid
idata = check("preauth", req("POST", "/points/pre-auth", {"amount": data["estimated_credits"], "operation_type": "segment_generate", "idempotency_key": str(uuid.uuid4())}))
print("   pre_auth:", idata)
pre_auth_id = idata["pre_auth_id"]

print("=== generate ===")
data = check("generate", req("POST", f"/projects/{pid}/segments/{sid}/generate", {"pre_auth_id": pre_auth_id, "params": {"duration": 12, "resolution": "720p", "audio": 1, "model_id": "seedance-2.0-mini", "asset_humanize": 0, "return_last_frame": 0, "seed": -1}}))
print("   task:", data)
tid = data["task_id"]

print("=== get task ===")
data = check("get task", req("GET", f"/tasks/{tid}"))
print("   task status:", data.get("status"))

print("=== list tasks ===")
data = check("list tasks", req("GET", "/tasks?limit=10"))
print("   total tasks:", data.get("total"))

print("\nALL SMOKE TESTS PASSED")
