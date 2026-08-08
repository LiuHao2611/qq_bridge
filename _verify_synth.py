# -*- coding: utf-8 -*-
"""Phase 5 合成全集：隔离验证 synthesize_project_video 不再卡死（FFmpeg stdin 修复）。

流程：
  1) 用 FFmpeg 现造两个真实 mp4 片段（各 5s，无音轨，模拟真实「片段生成」产物）
  2) 插入临时 user/project/2 segments（status=succeeded, duration=5 合法）
  3) 在线程中调用 synthesize_project_video，30s 超时做「卡死」判定
  4) 校验输出 mp4 存在且时长≈10s
  5) 清理临时数据与文件
"""
import os, sys, time, re, subprocess, threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "server"))
import ai_local
from db import get_conn, uid

OUT = ai_local.OUTPUTS
FF = ai_local.FFMPEG_BIN
print("FFMPEG_BIN =", FF)

def make_clip(path, color, dur=5):
    cmd = [FF, "-nostdin", "-y", "-f", "lavfi", "-i",
           "color=c=%s:s=640x360:d=%d:r=25" % (color, dur),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path]
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60)
    if r.returncode != 0 or not os.path.isfile(path):
        raise RuntimeError("造片段失败: %s" % color)

def ff_duration(path):
    r = subprocess.run([FF, "-i", path], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "ignore"))
    if not m:
        return None
    h, mm, ss = map(float, m.groups())
    return h * 3600 + mm * 60 + ss

ca = os.path.join(OUT, "task_dbgA.mp4")
cb = os.path.join(OUT, "task_dbgB.mp4")
make_clip(ca, "red", 5)
make_clip(cb, "blue", 5)
print("clips:", os.path.getsize(ca), os.path.getsize(cb))

pid = "verify_synth_" + str(int(time.time()))
uid_user = "verify_user_" + str(int(time.time()))
conn = get_conn()
conn.execute("INSERT INTO users (user_id, nickname) VALUES (?,?)", (uid_user, "verify"))
conn.execute("INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
             (pid, uid_user, "verify_project"))
for i, c in enumerate((ca, cb)):
    conn.execute(
        'INSERT INTO segments (segment_id, project_id, "index", title, duration, video_url, status) '
        "VALUES (?,?,?,?,?,?,?)",
        (uid(), pid, i, "seg%d" % i, 5, "/file?name=" + os.path.basename(c), "succeeded"))
conn.commit()

out = os.path.join(OUT, "synth_%s.mp4" % pid)
if os.path.isfile(out):
    os.remove(out)

res = {}
def run():
    try:
        res["val"] = ai_local.synthesize_project_video(pid, out)
    except Exception as e:
        res["val"] = ("EXC", repr(e))

t = threading.Thread(target=run)
t0 = time.time()
t.start()
t.join(timeout=30)
elapsed = time.time() - t0

if t.is_alive():
    print("HANG detected: synthesize_project_video 未在 30s 内返回（已运行 %.1fs）" % elapsed)
    # 不能强制杀子进程，直接标记失败退出
    sys.exit(2)

print("返回耗时: %.2fs" % elapsed)
print("返回结果:", res.get("val"))
print("输出存在:", os.path.isfile(out))
if os.path.isfile(out):
    d = ff_duration(out)
    print("输出时长: %s s" % d)
    ok = d is not None and abs(d - 10.0) < 0.5
    print("时长校验:", "PASS" if ok else "FAIL")

# ---- 清理 ----
try:
    conn.execute("DELETE FROM segments WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM users WHERE user_id=?", (uid_user,))
    conn.commit()
finally:
    conn.close()
for f in (out, ca, cb):
    if os.path.isfile(f):
        try:
            os.remove(f)
        except Exception:
            pass
print("cleanup done")
