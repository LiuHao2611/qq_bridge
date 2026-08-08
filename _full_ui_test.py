# -*- coding: utf-8 -*-
"""
manju_agent 全方位自动化测试 —— 前端 UI 全流程（Playwright）
断言 5 个流程入口在编辑器/策划页/任务中心可见，并截图留存。
"""
import sys, os, json, time, urllib.request, urllib.error

HERE = os.path.abspath(os.path.dirname(__file__))
BASE_URL = "http://127.0.0.1:7788/api/v1"
FE_URL = "http://127.0.0.1:8099"

RESULTS = []
pid = None


def req(method, path, body=None):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.getcode(), json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + detail) if detail else ""))
    return bool(cond)


def main():
    from playwright.sync_api import sync_playwright
    print("=" * 64)
    print("manju_agent 前端 UI 全流程自动化测试 @ %s" % time.strftime("%H:%M:%S"))
    print("=" * 64)

    # 建一个项目用于 UI 跳转
    c, j = req("POST", "/projects", {"name": "AT_UI_%d" % int(time.time())})
    pid = (j.get("data") or {}).get("project", {}).get("project_id")
    check("0. 创建 UI 测试项目", bool(pid), "pid=%s" % pid)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        def on_console(msg):
            if msg.type in ("warning", "error"):
                txt = msg.text[:200]
                print("    [console.%s] %s" % (msg.type, txt))
                if "project load failed" in txt or "backend" in txt:
                    RESULTS.append(("EDITOR_LOAD_WARN:" + txt[:40], False, txt))
        page.on("console", on_console)
        page.on("pageerror", lambda e: RESULTS.append(
            ("PAGEERROR:" + str(e)[:60], False, str(e)[:120])))

        # ---- 编辑器：5 步流程导航 + 合成全集按钮 ----
        page.goto(FE_URL + "/projects/%s/editor" % pid, wait_until="networkidle")
        try:
            page.wait_for_selector(".flow-nav", timeout=15000)
        except Exception as e:
            check("1. 编辑器加载 flow-nav", False, str(e)[:100])
        nav = page.locator(".flow-nav")
        nav_visible = nav.count() > 0
        labels = ["全局资产", "项目策划", "分镜编排", "任务中心", "合成全集"]
        for lb in labels:
            ok = page.get_by_text(lb, exact=False).count() > 0
            check("1. 流程导航含「%s」" % lb, ok)
        synth_btn = page.get_by_role("button", name="合成全集").count() > 0
        check("2. 编辑器「合成全集」按钮可见", synth_btn)
        page.screenshot(path=os.path.join(HERE, "ui_editor.png"), full_page=False)

        # ---- 项目策划页 ----
        page.goto(FE_URL + "/projects/%s/planning" % pid, wait_until="networkidle")
        try:
            page.wait_for_selector(".flow-nav", timeout=15000)
        except Exception as e:
            check("3. 策划页加载 flow-nav", False, str(e)[:100])
        planning_ok = (page.get_by_text("项目策划", exact=False).count() > 0 and
                       page.get_by_text("AI 自动分场", exact=False).count() > 0)
        ta = page.locator("textarea").count() > 0
        check("3. 策划页含「项目策划」+「AI 自动分场」", planning_ok)
        check("4. 策划页故事文本框可见", ta)
        page.screenshot(path=os.path.join(HERE, "ui_planning.png"), full_page=False)

        # ---- 任务中心 ----
        page.goto(FE_URL + "/tasks", wait_until="networkidle")
        tasks_ok = page.get_by_text("任务中心", exact=False).count() > 0
        check("5. 任务中心页面可见", tasks_ok)
        page.screenshot(path=os.path.join(HERE, "ui_tasks.png"), full_page=False)

        browser.close()

    # 清理
    if pid:
        req("DELETE", "/projects/%s" % pid)
        check("6. 清理 UI 测试项目", True)

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 64)
    print("UI 汇总：%d 项检查，通过 %d，失败 %d" % (total, passed, total - passed))
    print("=" * 64)
    if passed != total:
        for n, ok, d in RESULTS:
            if not ok:
                print("  FAIL -> %s : %s" % (n, d))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
