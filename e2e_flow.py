import os, json, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8099"
API = "http://127.0.0.1:7788/api/v1"


def _post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(API + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path):
    with urllib.request.urlopen(API + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) 真实创建项目
res = _post("/projects", {"name": "E2E测试·都市迷局", "project_type": "short_drama", "is_draft": 0})
pid = res["data"]["project"]["project_id"]
print("created project", pid)

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

    # 2) 打开片段编辑器（真实项目）
    page.goto(BASE + "/project/" + pid, wait_until="networkidle", timeout=20000)
    time.sleep(1.5)
    page.screenshot(path=r"D:\AI\manju_agent\shots\e2e_editor.png")

    # 3) 点击「保存」
    try:
        page.get_by_role("button", name="保存").click(timeout=8000)
        time.sleep(1.0)
        print("clicked 保存")
    except Exception as e:
        print("保存 button error:", e)

    # 4) 点击「合成全集」→ 提交真实生成任务
    try:
        page.get_by_role("button", name="合成全集").click(timeout=8000)
        time.sleep(2.0)
        print("clicked 合成全集")
    except Exception as e:
        print("合成全集 button error:", e)
    page.screenshot(path=r"D:\AI\manju_agent\shots\e2e_after_generate.png")

    browser.close()

# 5) 通过真实 API 确认生成任务已落库
tasks = _get("/tasks?project_id=" + pid)
items = tasks.get("data", {}).get("items", [])
print("tasks for project:", len(items))
for t in items:
    print("  task", t["task_id"], "status=", t["status"], "cost=", t.get("cost_credits"))

# 6) 确认积分被预冻结（pre-auth 占用）
profile = _get("/user/profile")
print("points_balance=", profile["data"]["points_balance"])

# 清理
try:
    urllib.request.urlopen(urllib.request.Request(API + "/projects/" + pid, method="DELETE"), timeout=10).read()
except Exception:
    pass

print("TOTAL_CONSOLE", len(errors))
for e in errors[:20]:
    print(e)
print("E2E_PASS" if (len(items) >= 1 and len(errors) == 0) else "E2E_FAIL")
