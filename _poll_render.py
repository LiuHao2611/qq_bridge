import urllib.request, json, time, sys

TID = "1785945756182"
PID = "1785942734524"
BASE = "http://localhost:8099"

def get(path):
    with urllib.request.urlopen(BASE+path, timeout=20) as r:
        return json.loads(r.read().decode())

def download(url, out):
    with urllib.request.urlopen(BASE+url, timeout=30) as r:
        with open(out, "wb") as f:
            f.write(r.read())

for i in range(120):
    s = get("/api/status?task_id="+TID)
    stage = s.get("stage")
    print(f"[{i}] stage={stage} cur={s.get('current')}/{s.get('total')} msg={s.get('message','')[:80]}")
    if stage == "done":
        break
    if stage == "error":
        print("ERROR:", s.get("message"))
        sys.exit(1)
    time.sleep(5)
else:
    print("timeout")
    sys.exit(1)

p = get("/api/projects/"+PID)
print("title=", p.get("title"), "status=", p.get("status"))
print("cover=", p.get("cover"))
print("assets.scenes=", len((p.get("assets") or {}).get("scenes", [])))
for i, sc in enumerate(p.get("scenes", [])):
    print(f" scene {i+1} thumb={sc.get('thumb')}")

if p.get("cover"):
    download(p["cover"], "D:/AI/manju_agent/_check_fp32.png")
    print("downloaded cover to _check_fp32.png")
