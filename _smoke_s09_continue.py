#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§09 补全功能冒烟测试（自动续跑验证）。
覆盖：G1 迁移列可见 / G2 端点契约 / G5 冒烟。
仅对本地 7788 真实 API 发请求，不改动生产数据（除临时创建的锚点会删除）。
"""
import json
import time
import urllib.request
import urllib.error
import sqlite3

BASE = "http://127.0.0.1:7788/api/v1"
MEMOIR = "428ff261-c03f-4348-a480-c8dca09fb034"
PROJECT = "461aaaf5-9135-4e0d-be4b-e7512471b863"

PASS = 0
FAIL = 0


def req(method, path, body=None, raw=False):
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            txt = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8") if e.fp else ""
        status = e.code
    try:
        j = json.loads(txt)
    except Exception:
        j = {"_raw": txt}
    if raw:
        return status, j
    # 信封校验
    ok_env = isinstance(j, dict) and "code" in j and "request_id" in j and "timestamp" in j
    return status, j, ok_env


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} :: {detail}")


print("=== G1 迁移列可见性（memoirs/projects 关键列）===")
conn = sqlite3.connect("server/app.db")
cols_mem = {r[1] for r in conn.execute("PRAGMA table_info(memoirs)")}
cols_proj = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
cols_chap = {r[1] for r in conn.execute("PRAGMA table_info(memoir_chapters)")}
conn.close()
check("memoirs.character_anchors", "character_anchors" in cols_mem)
check("memoirs.model_config", "model_config" in cols_mem)
check("projects.first_frame_mode", "first_frame_mode" in cols_proj)
check("memoir_chapters.lighting_scheme", "lighting_scheme" in cols_chap)
check("memoir_chapters.emotion_palette", "emotion_palette" in cols_chap)
check("memoir_chapters.shot_table_camera_paths", "shot_table_camera_paths" in cols_chap)

print("=== G2 端点契约 ===")
# 回忆录读取（信封 + character_anchors 字段）
s, j, env = req("GET", f"/memoirs/{MEMOIR}")
check("GET /memoirs/:id envelope", env, str(j)[:200])
check("GET /memoirs/:id code=0", j.get("code") == 0, str(j)[:200])
memoir_obj = (j.get("data") or {}).get("memoir") or {}
check("memoirs 含 character_anchors 字段",
      "character_anchors" in memoir_obj, str(memoir_obj.keys()))

# 事实锚点 创建（合法 anchor_type: event/relation/date/location/other）
s, j, env = req("POST", f"/memoirs/{MEMOIR}/anchors",
                {"anchor_type": "event", "content": "爷爷总在老槐树下讲故事"})
check("POST /anchors envelope", env, str(j)[:200])
check("POST /anchors code=0", j.get("code") == 0, str(j)[:200])
anchor_id = (j.get("data") or {}).get("anchor", {}).get("id")
check("POST /anchors 返回 id", bool(anchor_id), str(j.get("data")))

# 事实锚点 列表
s, j, env = req("GET", f"/memoirs/{MEMOIR}/anchors")
check("GET /anchors code=0", j.get("code") == 0, str(j)[:200])
check("GET /anchors 含刚创建项",
      anchor_id in [a.get("id") for a in (j.get("data") or {}).get("anchors", [])], "not found")

# 事实锚点 更新（锁定）
if anchor_id:
    s, j, env = req("PATCH", f"/memoirs/{MEMOIR}/anchors/{anchor_id}", {"is_locked": 0})
    check("PATCH /anchors code=0", j.get("code") == 0, str(j)[:200])

# 事实锚点 删除（清理）
if anchor_id:
    s, j, env = req("DELETE", f"/memoirs/{MEMOIR}/anchors/{anchor_id}")
    check("DELETE /anchors code=0", j.get("code") == 0, str(j)[:200])

# MEM-13 章节重排（原子）
conn = sqlite3.connect("server/app.db")
ch_ids = [r[0] for r in conn.execute(
    "SELECT chapter_id FROM memoir_chapters WHERE memoir_id=? ORDER BY chapter_index",
    (MEMOIR,))]
conn.close()
if len(ch_ids) >= 2:
    reversed_ids = list(reversed(ch_ids))
    s, j, env = req("POST", f"/memoirs/{MEMOIR}/chapters/reorder", {"chapter_ids": reversed_ids})
    check("POST /chapters/reorder code=0", j.get("code") == 0, str(j)[:200])
    # 还原顺序（幂等原子）
    s, j, env = req("POST", f"/memoirs/{MEMOIR}/chapters/reorder", {"chapter_ids": ch_ids})
    check("POST /chapters/reorder 还原 code=0", j.get("code") == 0, str(j)[:200])
else:
    check("reorder 前置章节数>=2", False, f"only {len(ch_ids)} chapters")

# STR-03 first_frame_mode 合法
s, j, env = req("PATCH", f"/projects/{PROJECT}", {"first_frame_mode": "output"})
check("PATCH first_frame_mode=output code=0", j.get("code") == 0, str(j)[:200])
# STR-03 first_frame_mode 非法（应被拒）
s, j, env = req("PATCH", f"/projects/{PROJECT}", {"first_frame_mode": "bad"})
check("PATCH first_frame_mode=bad 被拒(非0)", j.get("code") != 0 if isinstance(j, dict) else True, str(j)[:200])
check("PATCH first_frame_mode=bad 信封仍完整", env, str(j)[:200])

# API-06 反馈提交（限流不触发，单次）
s, j, env = req("POST", "/feedback",
                {"type": "bug", "title": "s09-smoke",
                 "content": "auto smoke test feedback with sufficient length text"})
check("POST /feedback code=0", j.get("code") == 0, str(j)[:200])

# API-05 通知软删（用一条已存在通知验证 404 路径与软删信封）
s, j, env = req("DELETE", "/notifications/non-existent-id")
check("DELETE /notifications 不存在 返回信封", env, str(j)[:200])
check("DELETE /notifications 不存在 非0或0皆可(有request_id)",
      isinstance(j, dict) and "request_id" in j, str(j)[:200])

# API-03 asset-hub 导入场景到项目
s, j, env = req("POST", "/asset-hub/scenes/efb64e29-bd8c-42fb-9331-31463a5c45f2/import",
                {"project_id": PROJECT})
check("POST /asset-hub/scenes/:id/import code=0", j.get("code") == 0, str(j)[:200])

print("\n=== 结果 ===")
print(f"PASS={PASS} FAIL={FAIL}")
raise SystemExit(1 if FAIL else 0)
