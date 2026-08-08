"""拖拽引用后端闭环（REDUN-03）端到端验证：针对真实运行的 7788 后端 + 临时 DB。

不依赖前端：直接 PUT shots（description 含 @[资产名]，asset_refs 为空），
验证服务端能从文本抽取并落库 asset_refs；并验证 BR-009 段级上限。
"""
import json, time, urllib.request, sqlite3, sys, os

BASE = "http://127.0.0.1:7788/api/v1"
DB = "C:/Users/L/AppData/Local/Temp/manju_app_snapshot.db"

PID = "a44e9709-1d8a-4d0f-9e5b-200e82910ee0"
SID = "bda4b3e9-ce42-40dc-81a8-4accfdb10ee2"
ASSET_NAME = "阿杰"  # 全局资产


def req(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def wait_health():
    for _ in range(40):
        try:
            st, _ = req("GET", "/health")
            if st == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def get_asset_id():
    c = sqlite3.connect(DB)
    row = c.execute("SELECT asset_id FROM assets WHERE name=? AND is_global=1 LIMIT 1",
                    (ASSET_NAME,)).fetchone()
    return row[0] if row else None


def get_seg_duration():
    c = sqlite3.connect(DB)
    row = c.execute("SELECT duration FROM segments WHERE segment_id=?", (SID,)).fetchone()
    return int(row[0]) if row else 12


def main():
    assert wait_health(), "后端不可用"
    aid = get_asset_id()
    assert aid, "未找到全局资产 %s" % ASSET_NAME
    dur = get_seg_duration()

    # Case 1：description 含 @[阿杰]，client 未提交 asset_refs → 服务端应解析落库
    shots = [{
        "duration": dur,
        "shot_type": "medium", "camera_angle": "eye_level", "camera_movement": "static",
        "focal_length": "35mm", "description": "主角 @[%s] 推门而入" % ASSET_NAME,
        "voiceover": "", "dialogue": "", "image_prompt": "", "negative_prompt": "",
        "setting": "", "asset_refs": [],
    }]
    st, resp = req("PUT", "/projects/%s/segments/%s/shots" % (PID, SID), {"shots": shots})
    assert st == 200, ("PUT shots 失败 %s %s" % (st, resp))
    _, seg = req("GET", "/projects/%s/segments/%s" % (PID, SID))
    got = seg["data"]["shots"][0]["asset_refs"]
    ids = {r["asset_id"] for r in (got or [])}
    assert aid in ids, "REDUN-03 失败：服务端未从 @[%s] 解析出 %s，实际=%s" % (ASSET_NAME, aid, got)
    print("PASS REDUN-03：文本 @[%s] → 落库 asset_refs=%s" % (ASSET_NAME, ids))

    # Case 2：BR-009 段级上限——10 个不同全局资产名 → 应返回 MAX_REFS_EXCEEDED
    names = ["角色%d" % i for i in range(1, 11)]
    # 确保这些名字在 assets 里存在（用已有全局资产名凑数：取 10 个真实资产名）
    c = sqlite3.connect(DB)
    real = [r[0] for r in c.execute(
        "SELECT name FROM assets WHERE is_global=1 GROUP BY name").fetchall()]
    if len(real) >= 10:
        big = [{
            "duration": 1, "description": " ".join("@[%s]" % n for n in real[:10]),
            "asset_refs": [],
        }]
        st2, resp2 = req("PUT", "/projects/%s/segments/%s/shots" % (PID, SID),
                         {"shots": big})
        assert st2 == 400 and resp2.get("code") == "MAX_REFS_EXCEEDED", \
            ("BR-009 未触发：%s %s" % (st2, resp2))
        print("PASS BR-009：10 个唯一引用被服务端拒绝 (MAX_REFS_EXCEEDED)")
    else:
        print("SKIP BR-009：全局资产名不足 10 个（实际 %d）" % len(real))

    print("E2E_OK")


if __name__ == "__main__":
    main()
