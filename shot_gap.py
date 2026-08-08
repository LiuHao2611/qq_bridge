from playwright.sync_api import sync_playwright
import pathlib
base="http://localhost:8099"
pages=[("/membership","gap-membership.png"),("/settings","gap-settings.png"),("/global-assets","gap-global-assets.png")]
with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    pg=b.new_page(viewport={"width":1440,"height":900})
    errs=[]; fails=[]
    pg.on("console", lambda m: errs.append(m.type+": "+m.text) if m.type in ("error","warning") else None)
    pg.on("requestfailed", lambda r: fails.append(r.url))
    pg.on("pageerror", lambda e: errs.append("PAGEERROR: "+str(e)))
    for path,fn in pages:
        pg.goto(base+path, wait_until="networkidle"); pg.wait_for_timeout(1000)
        out=pathlib.Path(r"D:/AI/manju_agent/shots")/fn
        pg.screenshot(path=str(out))
        print("SHOT",fn,out.exists())
    b.close()
    print("CONSOLE_ERR/WARN",len(errs))
    for e in errs: print("  ",e)
    print("REQ_FAILED",len(fails))
    for f in fails: print("  ",f)
