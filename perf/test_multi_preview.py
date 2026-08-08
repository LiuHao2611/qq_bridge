"""验证 Txt2Img 历史记录多图预览：拦截 /t2i/tasks 返回 2 张图，
点击卡片后弹窗应显示 2 张缩略图和 1/2 计数，并支持左右切换。"""
import sys, os, json, time
from playwright.sync_api import sync_playwright

BASE = os.environ.get('APP_URL', 'http://127.0.0.1:8099')

fake_history = {
    "items": [
        {
            "task_id": "test-task-001",
            "prompt": "测试多图预览",
            "status": "succeeded",
            "images": [
                {"url": "https://picsum.photos/id/10/400/400", "seed": 1, "width": 400, "height": 400},
                {"url": "https://picsum.photos/id/20/400/400", "seed": 2, "width": 400, "height": 400},
            ],
        }
    ],
    "total": 1,
    "page": 1,
    "size": 20,
}


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))

        def handle_route(route, request):
            if "/t2i/tasks?" in request.url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(fake_history))
            else:
                route.continue_()

        page.route("**/*", handle_route)
        page.goto(f"{BASE}/txt2img", wait_until="networkidle")
        page.wait_for_selector(".res-tabs", timeout=20000)

        # 切换到历史记录 tab，触发 loadHistory()
        page.locator('.res-tabs span', has_text='历史记录').click()
        page.wait_for_selector('.asset-card', timeout=10000)

        # 验证卡片显示 2 张角标
        badge = page.locator('.img-count')
        assert badge.count() == 1 and badge.inner_text().strip() == "2 张", "历史卡片未显示 2 张角标"

        # 点击卡片打开多图预览
        page.click('.asset-card')
        page.wait_for_selector('.preview-wrap', timeout=10000)

        thumbs = page.locator('.preview-thumbs img')
        counter = page.locator('.preview-meta .counter')
        time.sleep(0.3)
        assert thumbs.count() == 2, f"期望 2 张缩略图，实际 {thumbs.count()}"
        assert counter.inner_text().strip() == "1 / 2", f"计数器错误: {counter.inner_text()}"

        # 点击下一张
        page.click('.nav-btn.next')
        time.sleep(0.3)
        assert counter.inner_text().strip() == "2 / 2", f"切换后计数器错误: {counter.inner_text()}"

        # 点击第二张缩略图
        page.locator('.preview-thumbs img').nth(1).click()
        time.sleep(0.3)
        assert counter.inner_text().strip() == "2 / 2", f"缩略图切换后计数器错误"

        print("PASS: multi-image preview works (2 images, navigation + thumbs)")
        browser.close()

    if errors:
        print("PAGE ERRORS:", errors)
        sys.exit(1)


if __name__ == "__main__":
    main()
