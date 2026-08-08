import os, sys, time, json, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8099"
API = "http://127.0.0.1:7788/api/v1"
OUT = r"D:\AI\manju_agent\shots"
os.makedirs(OUT, exist_ok=True)


def _post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(API + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def create_project(name, ptype):
    """真实创建项目（对齐后端 /api/v1/projects），返回 project_id。"""
    res = _post("/projects", {"name": name, "project_type": ptype, "is_draft": 0})
    return res["data"]["project"]["project_id"]


def delete_project(pid):
    try:
        req = urllib.request.Request(API + "/projects/" + pid, method="DELETE")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


# 先建两个真实项目，供片段编辑 / 回忆录 / 素材库 / 画布页加载真实数据
short_id = create_project("演示短剧·都市迷局", "short_drama")
memoir_id = create_project("演示回忆录·岁月的余温", "memoir")

routes = [
    ("01-home", "/"),
    ("02-projects", "/projects"),
    ("03-tasks", "/tasks"),
    ("04-txt2img", "/txt2img"),
    ("05-img2img", "/img2img"),
    ("06-creative", "/creative"),
    ("07-global-assets", "/global-assets"),
    ("08-img2prompt", "/img2prompt"),
    ("09-history", "/history"),
    ("10-membership", "/membership"),
    ("11-messages", "/messages"),
    ("12-help", "/help"),
    ("13-logs", "/logs"),
    ("14-settings", "/settings"),
    ("15-fragment-editor", "/project/" + short_id),
    ("16-memoir-board", "/project/" + memoir_id + "/memoir"),
    ("17-assets", "/project/" + short_id + "/assets"),
    ("18-canvas", "/project/" + short_id + "/canvas"),
]

errors = []
failed_reqs = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
    page.on("requestfailed", lambda r: failed_reqs.append(f"{r.url} -> {r.failure.get('errorText','?')}"))
    page.on("response", lambda r: failed_reqs.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
    for name, route in routes:
        try:
            page.goto(BASE + route, wait_until="networkidle", timeout=20000)
        except Exception as e:
            errors.append(f"[{name}] goto error: {e}")
        time.sleep(2.0)
        page.mouse.wheel(0, 400)
        time.sleep(0.4)
        page.mouse.wheel(0, -400)
        try:
            page.screenshot(path=os.path.join(OUT, name + ".png"), full_page=False)
        except Exception as e:
            errors.append(f"[{name}] screenshot error: {e}")
        print("shot", name, route)
    browser.close()

# 清理演示项目，避免污染项目列表
delete_project(short_id)
delete_project(memoir_id)

with open(os.path.join(OUT, "console_errors.txt"), "w", encoding="utf-8") as f:
    f.write("--- Console errors/warnings ---\n")
    f.write("\n".join(errors) if errors else "NONE")
    f.write("\n\n--- Failed requests ---\n")
    f.write("\n".join(failed_reqs) if failed_reqs else "NONE")
print("TOTAL_CONSOLE", len(errors), "TOTAL_FAILED_REQ", len(failed_reqs))
for e in errors[:40]:
    print(e)
for r in failed_reqs[:20]:
    print(r)
