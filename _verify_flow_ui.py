import sys
import subprocess
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "playwright"])
    from playwright.sync_api import sync_playwright

PID = "4a975451-ccb9-446a-9b6e-8ededf7e4807"
BASE = "http://127.0.0.1:8099"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})

    # 1) 项目编辑页：验证 5 步流程导航 + 合成全集按钮
    page.goto(f"{BASE}/projects/{PID}/editor")
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    page.screenshot(path="flow_editor.png", full_page=False)
    print("flow_editor.png saved")

    # 2) 项目策划页：验证流程导航 + 分场拆解界面
    page.goto(f"{BASE}/projects/{PID}/planning")
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    page.screenshot(path="flow_planning.png", full_page=False)
    print("flow_planning.png saved")

    browser.close()
