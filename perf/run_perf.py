import time, json, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
OUT = r"D:\AI\manju_agent\perf"
os.makedirs(OUT, exist_ok=True)

# ---- 跨导航累积统计（Python 侧事件回调）----
inflight = 0
inflight_peak = 0
api_count = 0

def on_request(req):
    global inflight, inflight_peak, api_count
    if "/api" in req.url:
        inflight += 1
        api_count += 1
        if inflight > inflight_peak:
            inflight_peak = inflight

def on_response(resp):
    global inflight
    if "/api" in resp.url:
        inflight = max(0, inflight - 1)

routes = [
    ("/", "首页"),
    ("/projects", "项目"),
    ("/tasks", "任务"),
    ("/txt2img", "文生图"),
    ("/img2img", "图生图"),
    ("/creative", "创意"),
    ("/global-assets", "素材库"),
    ("/img2prompt", "图生提示词"),
    ("/history", "历史"),
    ("/membership", "会员"),
    ("/messages", "消息"),
    ("/help", "帮助"),
    ("/logs", "日志"),
    ("/settings", "设置"),
]

def longtasks(page):
    try:
        return page.evaluate("() => performance.getEntriesByType('longtask').length")
    except Exception:
        return 0

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,
                                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.on("request", on_request)
    page.on("response", on_response)
    errors = []
    page.on("console", lambda m: errors.append("[%s] %s" % (m.type, m.text))
            if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append("[pageerror] %s" % e))

    # 基线
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
    time.sleep(1)
    base_lt = longtasks(page)

    # 第一轮：逐页独立加载耗时（模拟刷新进入）
    nav_results = {}
    page.goto(BASE + "/", wait_until="domcontentloaded")
    time.sleep(0.5)
    for route, name in routes:
        t0 = time.time()
        try:
            page.goto(BASE + route, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("main > *", timeout=8000)
        except Exception as e:
            nav_results[name] = {"error": str(e)}
            continue
        # 等网络平静（keep-alive 页面每 10s 一次轮询，最多等 6s）
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        t1 = time.time()
        nav_results[name] = {"load_time": round(t1 - t0, 3)}
        time.sleep(0.6)

    # 第二轮：复现用户操作——打开文生图，然后在侧边栏页签间反复点击切换
    page.goto(BASE + "/txt2img", wait_until="domcontentloaded", timeout=20000)
    time.sleep(2)
    switch_report = []
    seq = ["/", "/projects", "/creative", "/", "/txt2img", "/img2img",
           "/txt2img", "/settings", "/txt2img", "/logs", "/txt2img"]
    for target in seq:
        t0 = time.time()
        try:
            page.click('a[href="%s"]' % target, timeout=10000)
        except Exception:
            try:
                page.goto(BASE + target, wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                switch_report.append({"to": target, "error": str(e)})
                continue
        try:
            page.wait_for_selector("main > *", timeout=8000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        t1 = time.time()
        # 切后停留 3s，期间后台请求由事件回调累积 peak
        time.sleep(3)
        lt = longtasks(page)
        switch_report.append({
            "to": target,
            "switch_time": round(t1 - t0, 3),
            "inflight_peak_so_far": inflight_peak,
            "longtasks_so_far": lt - base_lt,
        })

    end_lt = longtasks(page)
    browser.close()

summary = {
    "api_requests_total": api_count,
    "inflight_peak_overall": inflight_peak,
    "longtasks_total": end_lt - base_lt,
    "nav_results": nav_results,
    "switch_report": switch_report,
    "console_errors": errors[:50],
}
with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(json.dumps(summary, ensure_ascii=False, indent=2))
