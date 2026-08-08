import json, time, urllib.request, os, sys

API = "http://127.0.0.1:7788/api/v1"
tid = sys.argv[1] if len(sys.argv) > 1 else "b3455866-a125-4a26-b189-2197cc23498c"

for i in range(120):  # up to 10 minutes
    try:
        t = json.loads(urllib.request.urlopen(API + "/tasks/%s" % tid, timeout=15).read().decode())["data"]["task"]
    except Exception as e:
        print("poll", i, "err", e); time.sleep(5); continue
    print("poll", i, "status=", t["status"], "level=", t.get("level"), "result=", t.get("result_url"), "note=", t.get("note"))
    if t["status"] in ("succeeded", "failed", "cancelled"):
        if t.get("result_url"):
            p = t["result_url"].replace("file://", "")
            print("artifact exists:", os.path.isfile(p), p)
        print("DONE", t["status"])
        break
    time.sleep(5)
