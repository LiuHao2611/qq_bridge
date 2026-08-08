import time, json
from collections import Counter
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
records = []
def on_req(req):
    if "/api" in req.url:
        records.append((time.time(), req.url))

NAV = [
    ("首页", "/"), ("项目中心", "/projects"), ("任务中心", "/tasks"),
    ("文生图", "/txt2img"), ("图生图", "/img2img"), ("创意工坊", "/creative"),
    ("人生回忆录", "/memoir"), ("全局资产库", "/global-assets"),
    ("图片反推", "/img2prompt"), ("历史记录", "/history"),
    ("会员中心", "/membership"), ("消息中心", "/messages"),
    ("帮助与反馈", "/help"), ("操作日志", "/logs"), ("设置", "/settings"),
]

with sync_playwright() as p:
    b = p.chromium.launch(headless=True,
                          args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    pg = b.new_page()
    pg.on("request", on_req)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append("[%s]%s" % (m.type, m.text))
          if m.type == "error" else None)

    def click_nav(label):
        pg.click('.nav-item:has(.label:text-is("%s"))' % label, timeout=8000)

    def switch(label, path):
        t0 = time.time()
        click_nav(label)
        try:
            pg.wait_for_function("() => location.pathname === '%s'" % path, timeout=8000)
        except Exception as e:
            return {"label": label, "error": "route not changed: %s" % e}
        pg.wait_for_timeout(400)
        return {"label": label, "path": path, "switch_ms": round((time.time() - t0) * 1000)}

    # 首页起步
    pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
    pg.wait_for_timeout(1500)

    # ---- A. 全页签真实切换耗时 ----
    switch_results = []
    switch_results.append(switch("文生图", "/txt2img"))
    for label, path in NAV:
        if path == "/txt2img":
            continue
        switch_results.append(switch(label, path))
    print("=== 全页签 SPA 切换耗时 ===")
    for r in switch_results:
        print(json.dumps(r, ensure_ascii=False))

    # ---- B. 后台安静度专项：进文生图停留 → 切走 → 监控 12s ----
    def probe_after(label, path, window=12):
        pg.click('.nav-item:has(.label:text-is("文生图"))', timeout=8000)
        pg.wait_for_function("() => location.pathname === '/txt2img'", timeout=8000)
        pg.wait_for_timeout(3000)  # 让 services 轮询跑几轮
        # 切走
        pg.click('.nav-item:has(.label:text-is("%s"))' % label, timeout=8000)
        pg.wait_for_function("() => location.pathname === '%s'" % path, timeout=8000)
        pg.wait_for_timeout(400)
        records.clear()
        pg.wait_for_timeout(window * 1000)
        api_in = [(round(t - records[0][0], 2) if records else 0, u.split("?")[0]) for t, u in records]
        c = Counter(u for _, u in api_in)
        return {"switched_to": label, "api_count_in_%ds" % window: len(api_in),
                "breakdown": dict(c), "detail": api_in[:20]}

    print("\n=== 切走文生图后后台请求安静度 ===")
    print(json.dumps(probe_after("首页", "/"), ensure_ascii=False, indent=2))
    print(json.dumps(probe_after("创意工坊", "/creative"), ensure_ascii=False, indent=2))
    print(json.dumps(probe_after("图生图", "/img2img"), ensure_ascii=False, indent=2))

    print("\n=== pageerrors / console errors ===")
    for e in errors[:30]:
        print(e)
    b.close()
