# -*- coding: utf-8 -*-
"""
manju_agent 全方位自动化测试 —— 后端 API 全流程
覆盖：heartbeat / pre-auth / projects CRUD / segments / split-story /
      synthesize(含 FFmpeg 卡死回归) / tasks / assets
断言失败不中断，整体输出汇总表 + 退出码。
"""
import sys, os, json, time, subprocess, shutil, urllib.request, urllib.error

HERE = os.path.abspath(os.path.dirname(__file__))
SERVER = os.path.join(HERE, "server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

BASE_URL = "http://127.0.0.1:7788/api/v1"
ROOT_URL = "http://127.0.0.1:7788"
FFMPEG = r"D:\AI\tools\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.EXE"
OUTPUTS = os.path.join(HERE, "outputs")

RESULTS = []          # (name, ok, detail)
_CREATED = {"project_id": None, "seg_ids": [], "task_id": None, "asset_id": None,
            "seed_clips": [], "pre_auth_ids": [], "synth_name": None}


def req(method, path, body=None, raw=False):
    url = BASE_URL + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            payload = resp.read().decode("utf-8", "replace")
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", "replace")
        code = e.code
    if raw:
        return code, payload
    try:
        return code, json.loads(payload)
    except Exception:
        return code, {"raw": payload}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print("  [%s] %s%s" % (mark, name, ("  -> " + detail) if detail else ""))
    return bool(cond)


def seed_clip(name, seconds=5, color="color=c=blue"):
    path = os.path.join(OUTPUTS, name)
    cmd = [FFMPEG, "-y", "-f", "lavfi", "-i", "%s:s=320x180:d=%d" % (color, seconds),
           "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    return path if os.path.isfile(path) else None


def probe_duration(path):
    try:
        out = subprocess.run([FFMPEG, "-i", path], stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=30).stderr.decode("utf-8", "replace")
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
        if m:
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def main():
    print("=" * 64)
    print("manju_agent 后端 API 全流程自动化测试  @ %s" % time.strftime("%H:%M:%S"))
    print("=" * 64)

    # 1) heartbeat
    c, j = req("GET", "/auth/heartbeat")
    check("1. heartbeat 健康检查", c == 200 and j.get("code") == 0,
          "code=%s msg=%s" % (j.get("code"), j.get("message")))

    # 2) pre-auth（积分冻结）— 用于 synthesize 的 BR-004
    idem = "idem_at_%d" % int(time.time() * 1000)
    c, j = req("POST", "/points/pre-auth",
               {"amount": 30, "segment_id": None, "idempotency_key": idem})
    pa_id = (j.get("data") or {}).get("pre_auth_id") if c == 200 else None
    if pa_id:
        _CREATED["pre_auth_ids"].append(pa_id)
    check("2. points/pre-auth 预冻结积分", c == 200 and pa_id,
          "pre_auth_id=%s" % pa_id)

    # 3) create project
    pname = "AT自动化测试_%d" % int(time.time())
    c, j = req("POST", "/projects", {"name": pname, "project_type": "short_drama",
                                     "ratio": "16:9", "resolution": "720p"})
    pid = (j.get("data") or {}).get("project", {}).get("project_id") if c == 200 else None
    _CREATED["project_id"] = pid
    check("3. POST /projects 创建项目", c == 200 and pid,
          "project_id=%s" % pid)

    # 4) list projects 包含新建项目
    c, j = req("GET", "/projects")
    items = (j.get("data") or {}).get("items") or []
    ok = c == 200 and any((it.get("project_id") == pid) for it in items)
    check("4. GET /projects 列表含新建项目", ok, "total=%d" % len(items))

    # 5) create 2 segments（BR-003 时长 5/8/10/12/15）
    seg_ids = []
    for i in range(2):
        c, j = req("POST", "/projects/%s/segments" % pid,
                   {"title": "测试片段%d" % (i + 1), "duration": 5, "shot_count": 1})
        sid = (j.get("data") or {}).get("segment", {}).get("segment_id") if c == 200 else None
        if sid:
            seg_ids.append(sid)
    _CREATED["seg_ids"] = seg_ids
    check("5. POST segments 创建2个片段(时长=5)", len(seg_ids) == 2,
          "seg_ids=%s" % seg_ids)

    # 5b) BR-003 负向：非法时长应被拒
    c, j = req("POST", "/projects/%s/segments" % pid, {"duration": 7})
    check("5b. BR-003 非法时长(7)被拒", c != 200,
          "code=%s" % c)

    # 6) list segments
    c, j = req("GET", "/projects/%s/segments" % pid)
    segs = (j.get("data") or {}).get("items") or (j.get("data") or {}).get("segments") or []
    check("6. GET segments 列表", c == 200 and len(segs) >= 2, "count=%d" % len(segs))

    # 7) Phase 2 split-story
    story = ("小明说：今天天气真好。我们一起去公园散步吧。\n\n"
             "小红道：好呀，我带点吃的。阳光洒在湖面上波光粼粼。\n\n"
             "他们问路边的老人：这边怎么走？老人笑答：一直往前就到了。")
    c, j = req("POST", "/projects/%s/split-story" % pid, {"story": story})
    ssegs = (j.get("data") or {}).get("segments") or []
    check("7. POST split-story 分场拆解(Phase2)", c == 200 and len(ssegs) >= 2,
          "场次=%d" % len(ssegs))

    # 8) 准备合成：用真实 ffmpeg 片段种子 + 置片段 succeeded/video_url
    import db
    clip0 = seed_clip("at_seed0.mp4", 5, "color=c=blue")
    clip1 = seed_clip("at_seed1.mp4", 5, "color=c=red")
    _CREATED["seed_clips"] = [clip0, clip1]
    if clip0 and clip1 and seg_ids:
        conn = db.get_conn()
        try:
            for sid, clip in zip(seg_ids, (clip0, clip1)):
                nm = os.path.basename(clip)
                conn.execute('UPDATE segments SET status=?, video_url=? WHERE segment_id=?',
                             ("succeeded", "/file?name=%s" % nm, sid))
            conn.commit()
        finally:
            conn.close()
    check("8. 种子片段就绪(FFmpeg生成+置succeeded)",
          bool(clip0 and clip1 and seg_ids),
          "clip0=%s clip1=%s" % (bool(clip0), bool(clip1)))

    # 9) synthesize（Phase5）—— 关键回归：不得卡死
    c, j = req("POST", "/synthesize", {"project_id": pid, "pre_auth_id": pa_id, "options": {}})
    tid = (j.get("data") or {}).get("task_id") if c == 200 else None
    _CREATED["task_id"] = tid
    check("9. POST /synthesize 提交合成全集", c == 200 and tid,
          "task_id=%s" % tid)

    # 10) 轮询任务（CLOSE-01 轮询，非 WS），带超时护栏
    final = None
    t0 = time.time()
    while time.time() - t0 < 60:
        c, j = req("GET", "/tasks/%s" % tid)
        task = (j.get("data") or {}).get("task") or {}
        st = task.get("status")
        if st in ("succeeded", "failed"):
            final = st
            break
        time.sleep(1.5)
    check("10. 合成任务到达终态(无卡死回归)", final == "succeeded",
          "status=%s 耗时=%.1fs" % (final, time.time() - t0))

    # 11) 下载合成视频并校验时长 ≈ 两段之和
    # 注意：/file 是顶层路由（非 /api/v1 前缀）
    synth_name = "synth_%s.mp4" % pid
    _CREATED["synth_name"] = synth_name
    url = ROOT_URL + "/file?name=" + synth_name
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = resp.read()
            c = resp.getcode()
    except urllib.error.HTTPError as e:
        payload = e.read()
        c = e.code
    dpath = os.path.join(OUTPUTS, "at_downloaded.mp4")
    dur = 0.0
    if c == 200 and isinstance(payload, bytes) and len(payload) > 0:
        with open(dpath, "wb") as f:
            f.write(payload)
        dur = probe_duration(dpath)
    check("11. 合成视频可下载且时长≈10s", c == 200 and dur >= 9.0,
          "http=%s bytes=%s duration=%.1fs" % (c, (len(payload) if isinstance(payload, bytes) else 0), dur))

    # 12) tasks 列表含该任务
    c, j = req("GET", "/tasks")
    titems = (j.get("data") or {}).get("items") or []
    check("12. GET /tasks 列表含合成任务", c == 200 and any(
        (t.get("task_id") == tid) for t in titems), "total=%d" % len(titems))

    # 13) assets 列表 + 创建 + 删除
    c, j = req("GET", "/assets")
    check("13a. GET /assets 素材库列表", c == 200,
          "items=%d" % len((j.get("data") or {}).get("items") or []))
    c, j = req("POST", "/assets",
               {"name": "AT测试资产", "type": "role"})
    aid = (j.get("data") or {}).get("asset", {}).get("asset_id") if c == 200 else None
    _CREATED["asset_id"] = aid
    check("13b. POST /assets 创建资产", c == 200 and aid, "asset_id=%s" % aid)
    if aid:
        c, j = req("DELETE", "/assets/%s" % aid)
        check("13c. DELETE /assets 删除资产", c == 200, "code=%s" % c)

    # 14) 清理：删除测试项目（软删）
    if pid:
        c, j = req("DELETE", "/projects/%s" % pid)
        check("14. DELETE /projects 清理测试项目", c == 200, "code=%s" % c)

    # 15) 清理临时文件
    for f in ("at_seed0.mp4", "at_seed1.mp4", "at_downloaded.mp4"):
        p = os.path.join(OUTPUTS, f)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass
    check("15. 清理临时种子/下载文件", True, "已移除 at_seed*/at_downloaded")

    # 汇总
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 64)
    print("汇总：%d 项检查，通过 %d，失败 %d" % (total, passed, total - passed))
    print("=" * 64)
    if passed != total:
        for n, ok, d in RESULTS:
            if not ok:
                print("  FAIL -> %s : %s" % (n, d))
    return 0 if passed == total else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("测试被中断")
        sys.exit(2)
