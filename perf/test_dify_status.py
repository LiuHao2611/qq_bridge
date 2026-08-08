"""验证 Txt2Img 页 Dify 状态条与提示文案一致：Dify reachable 但未配置 API Key 时，
状态条应显示黄色「未配置 API Key」，下方提示应引导填写 API Key 而非未启动。"""
import sys, json, time
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"

fake_status = {
    "ollama": {"name": "Ollama", "reachable": True, "detail": "已连接"},
    "comfyui": {"name": "ComfyUI", "reachable": True, "detail": "已连接"},
    "dify": {"name": "Dify", "reachable": True, "configured": False, "base_url": "http://127.0.0.1", "detail": "未配置 API Key"},
    "docker": {"name": "Docker Desktop", "reachable": True, "detail": "已运行"},
}


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))

        def handle_route(route, request):
            u = request.url
            if "/services/status" in u:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"code": 0, "data": fake_status}))
            elif "/services/dify-config" in u:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"code": 0, "data": {"dify_base_url": "http://127.0.0.1"}}))
            else:
                route.continue_()

        page.route("**/*", handle_route)
        page.goto(f"{BASE}/txt2img", wait_until="networkidle")
        page.wait_for_selector(".svc-bar", timeout=20000)
        time.sleep(0.3)

        dify_svc = page.locator('.svc-bar .svc', has_text='Dify')
        assert dify_svc.count() == 1, "未找到 Dify 状态条"
        cls = dify_svc.get_attribute('class') or ''
        assert 'warn' in cls, f"Dify reachable 但未配置 Key 时应为 warn 黄色，实际 class={cls}"
        small = dify_svc.locator('small').inner_text().strip()
        assert small == '未配置 API Key', f"Dify 状态条文案应为「未配置 API Key」，实际={small}"

        # 把 orchestrator 设为 dify（通过 localStorage）后刷新，验证下方提示
        page.evaluate("() => { localStorage.setItem('wn_t2i_state', JSON.stringify({orchestrator:'dify'})) }")
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".hint.warn", timeout=10000)
        hint = page.locator('.hint.warn')
        hint_text = hint.inner_text().strip()
        assert "尚未配置 API Key" in hint_text, f"提示文案错误: {hint_text}"

        print("PASS: Dify status wording consistent (reachable but not configured)")
        browser.close()

    if errors:
        print("PAGE ERRORS:", errors)
        sys.exit(1)


if __name__ == "__main__":
    main()
