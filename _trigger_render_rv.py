import json, urllib.request

PID = "1785942734524"
BASE = "http://localhost:8099"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def post_render(pid, scenes):
    boundary = "----manjuboundary12345"
    parts = []
    def field(name, val):
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode())
        parts.append(val.encode("utf-8") if isinstance(val, str) else val)
        parts.append(b"\r\n")
    field("scenes", json.dumps(scenes, ensure_ascii=False))
    field("ckpt", "Realistic_Vision_V6.0_NV_B1_fp16.safetensors")
    field("animated", "0")
    field("use_ip", "0")
    body = b"".join(parts) + ("--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(
        BASE + "/api/projects/" + pid + "/render",
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

prj = get("/api/projects/" + PID)
scenes = prj.get("scenes", [])
print("project:", prj.get("title"), "scenes:", len(scenes))
resp = post_render(PID, scenes)
print("render resp:", resp)
