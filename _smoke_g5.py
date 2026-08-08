#!/usr/bin/env python3
# §09 补全功能 - G5 冒烟测试：验证 §09 新增/扩展端点契约与信封
import json, sqlite3, os, urllib.request, urllib.error

BASE = "http://localhost:7788/api/v1"

def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw}

def data_of(resp):
    return resp.get("data", {}) if isinstance(resp, dict) else {}

def ok(label, status, resp, expect_code=0):
    code = resp.get("code") if isinstance(resp, dict) else None
    good = (status < 400) and (code == expect_code)
    print(f"[{'PASS' if good else 'FAIL'}] {label}: HTTP {status} code={code}")
    return good

results = []

# 取一个真实 project_id / memoir_id
_, rp = call("GET", "/projects?page=1&size=1")
pid = (data_of(rp).get("items") or [{}])[0].get("project_id")
dbp = os.path.join(os.getcwd(), "app.db")
conn = sqlite3.connect(dbp); cur = conn.cursor()
cur.execute("SELECT memoir_id FROM memoirs LIMIT 1"); row = cur.fetchone(); conn.close()
mid = row[0] if row else None

# 1. 健康检查：项目列表
results.append(ok("GET /projects (envelope)", 200, rp))

# 2. API-06 反馈提交 + 列表
s, r = call("POST", "/feedback", {"type":"bug","title":"smoke-feedback","content":"this is a smoke test feedback with enough length","contact_email":""})
results.append(ok("POST /feedback (API-06)", s, r))
_, r = call("GET", "/feedback?page=1&size=5")
results.append(ok("GET /feedback (API-06 list)", 200, r))

# 3. API-05 通知列表 + 删除（存在则删，不存在则验证 404 信封）
_, r = call("GET", "/notifications")
results.append(ok("GET /notifications (API-05 list)", 200, r))
nid = None
nlist = data_of(r).get("notifications") or data_of(r).get("items") or []
if nlist:
    nid = nlist[0].get("message_id") or nlist[0].get("id")
if nid:
    s2, r2 = call("DELETE", f"/notifications/{nid}")
    results.append(ok("DELETE /notifications/{id} (API-05)", s2, r2))
else:
    # 无通知数据：验证错误码映射（NOT_FOUND 40404 正确返回信封）
    s2, r2 = call("DELETE", "/notifications/smoke-fake-id")
    good = (s2 == 404) and (r2.get("code") == 40404)
    print(f"[{'PASS' if good else 'FAIL'}] DELETE /notifications/{{fake}} 正确返回 40404 信封: HTTP {s2} code={r2.get('code')}")
    results.append(good)

# 4. API-04 模板列表
_, r = call("GET", "/templates?page=1&size=5")
results.append(ok("GET /templates (API-04)", 200, r))

# 5. API-03 全局资产库：场景列表 + 导入到项目
_, r = call("GET", "/asset-hub/scenes")
results.append(ok("GET /asset-hub/scenes (API-03 list)", 200, r))
sid = (data_of(r).get("scenes") or data_of(r).get("items") or data_of(r).get("assets") or [{}])[0].get("asset_id") or (data_of(r).get("scenes") or [{}])[0].get("id")
if sid and pid:
    s3, r3 = call("POST", f"/asset-hub/scenes/{sid}/import", {"project_id": pid})
    results.append(ok("POST /asset-hub/scenes/{id}/import (API-03)", s3, r3))
else:
    print("[SKIP] API-03 import: 缺少 scene 或 project 数据")

# 6. STR-03 first_frame_mode PATCH（合法值 + 非法值拒绝）
if pid:
    s4, r4 = call("PATCH", f"/projects/{pid}", {"first_frame_mode": "output"})
    results.append(ok("PATCH /projects/{id} first_frame_mode=output (STR-03)", s4, r4))
    s5, r5 = call("PATCH", f"/projects/{pid}", {"first_frame_mode": "no_output"})
    results.append(ok("PATCH /projects/{id} first_frame_mode=no_output (STR-03 revert)", s5, r5))
    s6, r6 = call("PATCH", f"/projects/{pid}", {"first_frame_mode": "bad"})
    bad_ok = (s6 >= 400) and (isinstance(r6, dict) and r6.get("code") not in (0, None))
    print(f"[{'PASS' if bad_ok else 'FAIL'}] PATCH first_frame_mode=bad 应被拒: HTTP {s6} code={r6.get('code') if isinstance(r6,dict) else None}")
    results.append(bad_ok)
else:
    print("[SKIP] STR-03 patch: 缺少 project 数据")

# 7. MEM 回忆录端点（有 memoir 才测）
if mid:
    s7, r7 = call("GET", f"/memoirs/{mid}")
    results.append(ok("GET /memoirs/{id} (MEM detail)", s7, r7))
    s8, r8 = call("POST", f"/memoirs/{mid}/anchors", {"anchor_type":"event","content":"smoke anchor content","is_locked":0})
    results.append(ok("POST /memoirs/{id}/anchors (MEM-03)", s8, r8))
    aid = (r8.get("anchor") or {}).get("anchor_id") if isinstance(r8, dict) else None
    if aid:
        s9, r9 = call("DELETE", f"/memoirs/{mid}/anchors/{aid}")
        results.append(ok("DELETE /memoirs/{id}/anchors/{aid} (MEM-03 cleanup)", s9, r9))
    # 章节重排（确保至少 2 章）
    s10, r10 = call("GET", f"/memoirs/{mid}")
    chs = (data_of(r10).get("memoir") or {}).get("chapters") or data_of(r10).get("chapters") or []
    while len(chs) < 2:
        sc, rc = call("POST", f"/memoirs/{mid}/chapters", {"title": f"测试章{len(chs)+1}"})
        if sc >= 400:
            break
        newid = (rc.get("chapter") or {}).get("chapter_id") if isinstance(rc, dict) else None
        if newid:
            chs.append({"chapter_id": newid})
        else:
            break
    if len(chs) >= 2:
        ids = [c.get("chapter_id") or c.get("id") for c in chs]
        s11, r11 = call("POST", f"/memoirs/{mid}/chapters/reorder", {"chapter_ids": ids[::-1]})
        results.append(ok("POST /memoirs/{id}/chapters/reorder (MEM-13)", s11, r11))
    else:
        print("[SKIP] MEM-13 reorder: 无法创建足够章节")
    # 旁白生成
    if chs:
        cid = chs[0].get("chapter_id") or chs[0].get("id")
        s12, r12 = call("POST", f"/memoirs/{mid}/chapters/{cid}/narrations", {"text":"smoke narration","voice":"zh-CN-XiaoxiaoNeural"})
        results.append(ok("POST /memoirs/{id}/chapters/{cid}/narrations (MEM-09)", s12, r12))
        # patch_chapter 结构化字段（MEM-06/07/08）
        s13, r13 = call("PATCH", f"/memoirs/{mid}/chapters/{cid}", {
            "shot_table_camera_paths": [{"shot_index":0,"camera_path":"push_in","camera_speed":3}],
            "lighting_scheme": {"key_light":"front","fill_ratio":0.4,"back_light":True,"color_temp":5600,"description":"soft"},
            "emotion_palette": [{"color":"#ff4d8d","weight":0.6}]
        })
        results.append(ok("PATCH /memoirs/{id}/chapters/{cid} MEM-06/07/08", s13, r13))
else:
    print("[SKIP] MEM tests: 无回忆录数据")

# 8. STR-01 任务日志
_, r = call("GET", "/tasks?page=1&size=3")
results.append(ok("GET /tasks (STR-01 list)", 200, r))
tid = (data_of(r).get("tasks") or data_of(r).get("items") or [{}])[0].get("task_id") or (data_of(r).get("tasks") or [{}])[0].get("id")
if tid:
    s14, r14 = call("GET", f"/tasks/{tid}")
    results.append(ok("GET /tasks/{id} (STR-01 logs)", s14, r14))
else:
    print("[SKIP] STR-01 task logs: 无任务数据")

print("\n==== 汇总 ====")
passed = sum(1 for x in results if x)
print(f"PASS {passed}/{len(results)}")
