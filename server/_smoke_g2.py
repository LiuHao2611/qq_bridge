# -*- coding: utf-8 -*-
"""G2 端点冒烟测试：覆盖 §09 新增后端端点，验证信封/状态码，并确保清理测试数据。"""
import json, sqlite3, urllib.request, urllib.error, time

BASE = "http://127.0.0.1:7788/api/v1"
MID = "428ff261-c03f-4348-a480-c8dca09fb034"   # 测试回忆录
PID = "461aaaf5-9135-4e0d-be4b-e7512471b863"   # 关联项目
SCENE = "2802ec40-34ff-4cf7-a4a0-d34d120b8c47" # 全局场景资产
MSG = "b8bb2307-736d-4707-bf92-09ff8dba6def"   # 真实通知(测试后还原)

results = []
created = {"feedback": None, "anchor": None, "chapters": [], "asset": None,
           "memoir_ca": None, "project_ffm": None, "template": None}

def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            code = resp.status; raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        code = e.code; raw = e.read().decode("utf-8", "ignore")
    try:
        js = json.loads(raw)
    except Exception:
        js = {"_raw": raw[:200], "code": None}
    return code, js

def check(name, cond, detail=""):
    results.append((name, "PASS" if cond else "FAIL", detail))
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))

def env_ok(js):
    return js.get("code") == 0 and "data" in js and "request_id" in js and "timestamp" in js

def db():
    c = sqlite3.connect("app.db", timeout=15)
    c.row_factory = sqlite3.Row
    return c

def cleanup():
    print("\n--- CLEANUP ---")
    try:
        conn = db(); cur = conn.cursor()
        if created["feedback"]:
            cur.execute("DELETE FROM feedbacks WHERE id=?", (created["feedback"],))
        if created["anchor"]:
            cur.execute("DELETE FROM memoir_fact_anchors WHERE id=?", (created["anchor"],))
        if created["chapters"]:
            cur.execute("DELETE FROM memoir_chapters WHERE chapter_id IN (%s)" % ",".join("?" * len(created["chapters"])), created["chapters"])
        if created["asset"]:
            cur.execute("DELETE FROM assets WHERE asset_id=?", (created["asset"],))
        if created["memoir_ca"] is not None:
            cur.execute("UPDATE memoirs SET character_anchors=? WHERE memoir_id=?", (created["memoir_ca"], MID))
        if created["project_ffm"] is not None:
            cur.execute("UPDATE projects SET first_frame_mode=? WHERE project_id=?", (created["project_ffm"], PID))
        cur.execute("UPDATE messages SET deleted_at=NULL WHERE message_id=?", (MSG,))
        if created["template"]:
            cur.execute("DELETE FROM templates WHERE template_id=?", (created["template"],))
        conn.commit(); print("cleanup committed")
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        print("cleanup error:", e)
    finally:
        try: conn.close()
        except Exception: pass

def pre_clean():
    print("--- PRE-CLEAN (残留清理) ---")
    try:
        conn = db(); cur = conn.cursor()
        cur.execute("DELETE FROM memoir_chapters WHERE memoir_id=?", (MID,))
        cur.execute("DELETE FROM feedbacks WHERE title='冒烟测试反馈'")
        cur.execute("DELETE FROM templates WHERE template_id LIKE 'smoke_tpl_%'")
        cur.execute("DELETE FROM assets WHERE is_global=0 AND name='客厅' AND asset_id NOT IN (SELECT asset_id FROM asset_stages) AND created_at>?", (int(time.time()) - 7200,))
        cur.execute("UPDATE memoirs SET character_anchors='{}' WHERE memoir_id=?", (MID,))
        cur.execute("UPDATE projects SET first_frame_mode='no_output' WHERE project_id=?", (PID,))
        cur.execute("UPDATE messages SET deleted_at=NULL WHERE message_id=?", (MSG,))
        conn.commit(); print("pre-clean committed")
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        print("pre-clean error:", e)
    finally:
        try: conn.close()
        except Exception: pass

try:
    pre_clean()
    # ---- 1. 只读端点 ----
    c, js = req("GET", "/templates")
    check("GET /templates 信封", env_ok(js) and c == 200, "code=%s list=%s" % (js.get("code"), len((js.get("data") or {}).get("list", []))))
    c, js = req("GET", "/asset-hub/scenes")
    check("GET /asset-hub/scenes 信封", env_ok(js) and c == 200, "total=%s" % ((js.get("data") or {}).get("total")))
    c, js = req("GET", "/asset-hub/props")
    check("GET /asset-hub/props 信封", env_ok(js) and c == 200, "total=%s" % ((js.get("data") or {}).get("total")))
    c, js = req("GET", "/feedback")
    check("GET /feedback 信封", env_ok(js) and c == 200, "total=%s" % ((js.get("data") or {}).get("total")))

    # ---- 2. 负向校验（证明校验/错误信封，不 500）----
    c, js = req("POST", "/feedback", {"type": "bug", "title": "x", "content": "short"})
    check("POST /feedback 短内容拦截", c == 400 and js.get("code") == 10001, "http=%s code=%s" % (c, js.get("code")))
    c, js = req("POST", "/memoirs/%s/anchors" % MID, {"anchor_type": "bad", "content": "测试内容足够长"})
    check("POST /anchors 非法类型拦截", c == 400 and js.get("code") == 10001, "http=%s code=%s" % (c, js.get("code")))

    # ---- 3. 反馈创建 ----
    c, js = req("POST", "/feedback", {"type": "bug", "title": "冒烟测试反馈", "content": "这是一段用于G2冒烟测试的反馈内容，长度足够。", "contact_email": "test@example.com"})
    check("POST /feedback 创建", env_ok(js) and c == 200, "code=%s" % js.get("code"))
    if env_ok(js): created["feedback"] = js["data"]["feedback"]["id"]

    # ---- 4. 事实锚点 CRUD ----
    c, js = req("GET", "/memoirs/%s/anchors" % MID)
    check("GET /anchors 初始", env_ok(js) and c == 200, "count=%s" % len((js.get("data") or {}).get("anchors", [])))
    c, js = req("POST", "/memoirs/%s/anchors" % MID, {"anchor_type": "event", "content": "测试事实锚点内容", "is_locked": True})
    check("POST /anchors 创建", env_ok(js) and c == 200, "code=%s" % js.get("code"))
    aid = (js.get("data") or {}).get("anchor", {}).get("id") if env_ok(js) else None
    created["anchor"] = aid
    if aid:
        c, js = req("DELETE", "/memoirs/%s/anchors/%s" % (MID, aid))
        check("DELETE /anchors 软删", env_ok(js) and c == 200 and (js.get("data") or {}).get("deleted") is True, "code=%s" % js.get("code"))

    # ---- 5. 章节创建 + 重排 + 旁白 ----
    c1, j1 = req("POST", "/memoirs/%s/chapters" % MID, {"title": "冒烟章一"})
    c2, j2 = req("POST", "/memoirs/%s/chapters" % MID, {"title": "冒烟章二"})
    jd1 = j1.get("data") or {}; jd2 = j2.get("data") or {}
    cid1 = c1 == 200 and (jd1.get("chapter") or {}).get("chapter_id")
    cid2 = c2 == 200 and (jd2.get("chapter") or {}).get("chapter_id")
    check("POST /chapters 创建x2", c1 == 200 and c2 == 200 and cid1 and cid2, "c1=%s c2=%s" % (c1, c2))
    if cid1: created["chapters"].append(cid1)
    if cid2: created["chapters"].append(cid2)
    if cid1 and cid2:
        c, js = req("POST", "/memoirs/%s/chapters/reorder" % MID, {"chapter_ids": [cid2, cid1]})
        ok = env_ok(js) and c == 200 and [x["chapter_id"] for x in ((js.get("data") or {}).get("chapters") or [])] == [cid2, cid1]
        check("POST /chapters/reorder 原子重排", ok, "code=%s" % js.get("code"))
        c, js = req("POST", "/memoirs/%s/chapters/reorder" % MID, {"chapter_ids": [cid1]})
        check("reorder 集合不一致拦截", c == 400 and js.get("code") == 10001, "http=%s code=%s" % (c, js.get("code")))
        c, js = req("POST", "/memoirs/%s/chapters/%s/narrations" % (MID, cid1), {"text": "这是一段用于测试声音旁白生成的文本内容。", "voice_id": "zh-CN-XiaoxiaoNeural"})
        narr = (js.get("data") or {}).get("narration") or {}
        check("POST /chapters/{id}/narrations", env_ok(js) and c == 200, "code=%s fallback=%s" % (js.get("code"), narr.get("local_fallback")))

    # ---- 6. 全局资产导入 ----
    c, js = req("POST", "/asset-hub/scenes/%s/import" % SCENE, {"project_id": PID})
    check("POST /asset-hub/scenes/{id}/import", env_ok(js) and c == 200, "code=%s" % js.get("code"))
    if env_ok(js): created["asset"] = (js.get("data") or {}).get("asset", {}).get("asset_id")

    # ---- 7. MEM-04 人物锚点 patch ----
    conn = db()
    created["memoir_ca"] = conn.execute("SELECT character_anchors FROM memoirs WHERE memoir_id=?", (MID,)).fetchone()[0]
    conn.close()
    c, js = req("PATCH", "/memoirs/%s" % MID, {"character_anchors": {"appearance_keywords": ["圆脸", "戴眼镜", "中等身材"], "personality_keywords": ["温和", "固执"], "core_relationship": "与叙述者为父子关系，贯穿全片。"}})
    check("PATCH /memoirs 人物锚点", env_ok(js) and c == 200, "code=%s" % js.get("code"))

    # ---- 8. STR-03 first_frame_mode patch ----
    conn = db()
    created["project_ffm"] = conn.execute("SELECT first_frame_mode FROM projects WHERE project_id=?", (PID,)).fetchone()[0]
    conn.close()
    c, js = req("PATCH", "/projects/%s" % PID, {"first_frame_mode": "output"})
    check("PATCH /projects first_frame_mode=output", env_ok(js) and c == 200, "code=%s" % js.get("code"))
    c, js = req("PATCH", "/projects/%s" % PID, {"first_frame_mode": "invalid"})
    check("PATCH /projects first_frame_mode 非法拦截", c == 400 and js.get("code") == 10001, "http=%s code=%s" % (c, js.get("code")))

    # ---- 9. 通知删除（真实删除后还原）----
    c, js = req("DELETE", "/notifications/%s" % MSG)
    check("DELETE /notifications 软删", env_ok(js) and c == 200, "code=%s" % js.get("code"))
    c, js = req("DELETE", "/notifications/does-not-exist-id")
    check("DELETE /notifications 不存在 404", c == 404 and js.get("code") == 40404, "http=%s code=%s" % (c, js.get("code")))

    # ---- 10. 模板深度测试（验证 CONFLICT 规则）----
    conn = db()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(templates)")]
        ins = {}
        if "name" in cols: ins["name"] = "冒烟测试模板"
        if "category" in cols: ins["category"] = "vlog"
        if "preset" in cols: ins["preset"] = "smoke-preset"
        if "use_count" in cols: ins["use_count"] = 0
        if "created_at" in cols: ins["created_at"] = int(time.time())
        if "updated_at" in cols: ins["updated_at"] = int(time.time())
        tid = "smoke_tpl_%d" % int(time.time())
        keys = list(ins.keys())
        conn.execute("INSERT INTO templates (template_id,%s) VALUES (?,%s)" % (",".join(keys), ",".join("?" * len(keys))), [tid] + list(ins.values()))
        conn.commit()
        created["template"] = tid
    finally:
        conn.close()
    c, js = req("GET", "/templates/%s" % tid)
    check("GET /templates/{id} 测试模板", env_ok(js) and c == 200, "code=%s" % js.get("code"))
    c, js = req("PATCH", "/templates/%s" % tid, {"name": "冒烟模板改名", "tags": ["a", "b"]})
    check("PATCH /templates 改名", env_ok(js) and c == 200, "code=%s" % js.get("code"))
    c, js = req("POST", "/templates/%s/apply" % tid, {"project_id": PID})
    check("POST /templates/{id}/apply 计数+1", env_ok(js) and c == 200, "code=%s" % js.get("code"))
    c, js = req("DELETE", "/templates/%s" % tid)
    check("DELETE /templates use_count>0 冲突", c == 409 and js.get("code") == 40909, "http=%s code=%s" % (c, js.get("code")))

finally:
    cleanup()

passed = sum(1 for _, s, _ in results if s == "PASS")
failed = [n for n, s, _ in results if s == "FAIL"]
print("\n==== SUMMARY ====")
print("TOTAL=%d PASS=%d FAIL=%d" % (len(results), passed, len(failed)))
print("FAILED:", failed if failed else "NONE")
print("ALL_G2_SMOKE_PASS" if not failed else "G2_SMOKE_HAS_FAILURES")
