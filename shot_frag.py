from playwright.sync_api import sync_playwright
import pathlib
url = "http://localhost:8099/project/demo-short"
out = pathlib.Path(r"D:/AI/manju_agent/shots/15-fragment-editor.png")
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1440,"height":900})
    errs=[]; fails=[]
    pg.on("console", lambda m: errs.append(m.type+": "+m.text) if m.type in ("error","warning") else None)
    pg.on("requestfailed", lambda r: fails.append(r.url))
    pg.goto(url, wait_until="networkidle"); pg.wait_for_timeout(1200)
    pg.screenshot(path=str(out), full_page=False)
    b.close()
    print("SHOT", out, out.exists())
    print("CONSOLE_ERR/WARN", len(errs)); [print("  ",e) for e in errs]
    print("REQ_FAILED", len(fails)); [print("  ",f) for f in fails]
