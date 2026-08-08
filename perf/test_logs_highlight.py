"""验证操作日志页：行号、error 红色、warning 黄色、单搜索框即时过滤+高亮。"""
import sys, os, json, time
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"

fake_content = (
    "2026-08-06 23:00:01 INFO 服务启动完成\n"
    "2026-08-06 23:00:02 DEBUG 加载配置 config.json\n"
    "2026-08-06 23:00:03 WARN 磁盘剩余空间较低\n"
    "2026-08-06 23:00:04 WARNING 即将进入低内存模式\n"
    "2026-08-06 23:00:05 ERROR 数据库连接失败\n"
    "2026-08-06 23:00:06 Exception traceback: null pointer\n"
    "2026-08-06 23:00:07 INFO 用户点击生成按钮\n"
)
fake_list = {
    "logs": [
        {"name": "backend.log", "size": 1234, "modified_at": 1754492400},
        {"name": "web_app.log", "size": 567, "modified_at": 1754492400},
    ]
}


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))

        def handle_route(route, request):
            u = request.url
            if "/api/v1/logs?" in u or u.endswith("/api/v1/logs"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps(fake_list))
            elif "/api/v1/logs/" in u:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"name": "x", "total_lines": 7, "returned_lines": 7, "text": fake_content}))
            else:
                route.continue_()

        page.route("**/*", handle_route)
        page.goto(f"{BASE}/logs", wait_until="networkidle")
        try:
            page.wait_for_selector(".lv-body", timeout=20000)
        except Exception as e:
            print("DEBUG title:", page.title())
            print("DEBUG url:", page.url)
            print("DEBUG body text:", page.locator('body').inner_text()[:400])
            print("DEBUG errors:", errors)
            raise
        time.sleep(0.3)

        # 行号
        lines = page.locator(".lv-body .line")
        assert lines.count() == 7, f"期望 7 行，实际 {lines.count()}"
        first_ln = lines.first.locator(".ln").inner_text().strip()
        assert first_ln == "1", f"首行行号应为 1，实际 {first_ln}"

        # error 红色
        err_line = page.locator(".line.error").first
        assert err_line.count() == 1, "应有 1 个 error 行(ERROR)"
        exc_line = page.locator(".line.error").nth(1)
        assert exc_line.count() == 1, "Exception 行也应判为 error"
        err_color = err_line.locator(".txt").evaluate("el => getComputedStyle(el).color")
        assert err_color in ("rgb(255, 107, 107)",), f"error 应为红色，实际 {err_color}"

        # warning 黄色（WARN 与 WARNING）
        warn_lines = page.locator(".line.warn")
        assert warn_lines.count() == 2, f"应有 2 个 warn 行，实际 {warn_lines.count()}"
        warn_color = warn_lines.first.locator(".txt").evaluate("el => getComputedStyle(el).color")
        assert warn_color in ("rgb(245, 197, 66)",), f"warning 应为黄色，实际 {warn_color}"

        # 搜索框即时过滤 + 高亮
        search = page.locator(".lv-tools input").first
        search.fill("ERROR")
        time.sleep(0.3)
        assert page.locator(".lv-body .line").count() == 1, "搜索 ERROR 后应只剩 1 行"
        marks = page.locator(".lv-body mark")
        assert marks.count() >= 1, "搜索关键词应被高亮 <mark>"
        # 清空恢复
        search.fill("")
        time.sleep(0.3)
        assert page.locator(".lv-body .line").count() == 7, "清空搜索后应恢复 7 行"

        print("PASS: logs highlight + line number + search work")
        browser.close()

    if errors:
        print("PAGE ERRORS:", errors)
        sys.exit(1)


if __name__ == "__main__":
    main()
