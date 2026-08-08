import time
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True,
                          args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    pg = b.new_page()
    pg.on("pageerror", lambda e: errors.append("PAGEERROR: " + str(e)))
    pg.on("console", lambda m: errors.append("[%s]%s" % (m.type, m.text))
          if m.type == "error" else None)
    pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
    pg.wait_for_timeout(1500)

    def go(label):
        try:
            pg.click('.nav-item:has(.label:text-is("%s"))' % label, timeout=8000)
            pg.wait_for_timeout(600)
        except Exception as e:
            errors.append("CLICK_FAIL %s: %s" % (label, e))

    for lab in ["文生图", "首页", "项目中心", "创意工坊", "图生图",
                "设置", "操作日志", "历史记录", "会员中心", "帮助与反馈"]:
        go(lab)
    # 再回到文生图停留，确认轮询/渲染无错
    go("文生图")
    pg.wait_for_timeout(2500)
    pg.wait_for_function("() => location.pathname === '/txt2img'", timeout=8000)
    pg.wait_for_timeout(500)

    print("ERRORS_COUNT:", len(errors))
    for e in errors[:40]:
        print(e)
    b.close()
