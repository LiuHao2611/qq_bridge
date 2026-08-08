# -*- coding: utf-8 -*-
"""Phase 5 合成全集：Worker 路径隔离验证（直接调用 worker._serve_one，复现原卡死现场）。

原故障：后端 7788 的 worker 线程在合成全集时卡在 running（FFmpeg subprocess 未重定向 stdin 而阻塞）。
本脚本不走 HTTP，直接：
  1) 造两个真实片段 mp4（各 5s，无音轨）
  2) 用 SINGLE_USER_ID 建 project + 2 个 succeeded segments
  3) pre_auth 一笔积分，插入 generation_tasks(model_id='synthesize', status='queued')
  4) 直接调用 worker._serve_one(task)（与原 worker 消费逻辑完全一致），30s 超时判定卡死
  5) 校验任务 succeeded / result_url / 输出时长≈10s
  6) 清理（删除测试数据并退还冻结金）
"""
import os, sys, time, re, subprocess, threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "server"))
import db
from db import get_conn, uid, row_to_dict, SINGLE_USER_ID
import ai_local
import points_engine
import worker

OUT = ai_local.OUTPUTS
FF = ai_local.FFMPEG_BIN
print("FFMPEG_BIN =", FF, "| SINGLE_USER_ID =", SINGLE_USER_ID)


def make_clip(path, color, dur=5):
    cmd = [FF, "-nostdin", "-y", "-f", "lavfi", "-i",
           "color=c=%s:s=640x360:d=%d:r=25" % (color, dur),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path]
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=60)


def ff_duration(path):
    r = subprocess.run([FF, "-i", path], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "ignore"))
    if not m:
        return None
    h, mm, ss = map(float, m.groups())
    return h * 3600 + mm * 60 + ss


ca = os.path.join(OUT, "task_e2eA.mp4")
cb = os.path.join(OUT, "task_e2eB.mp4")
make_clip(ca, "red", 5)
make_clip(cb, "blue", 5)
print("clips:", os.path.getsize(ca), os.path.getsize(cb))

pid = "e2e_syn_" + str(int(time.time()))
conn = get_conn()
conn.execute("INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
             (pid, SINGLE_USER_ID, "e2e_synth"))
seg_ids = []
for i, c in enumerate((ca, cb)):
    sid = uid()
    seg_ids.append(sid)
    conn.execute(
        'INSERT INTO segments (segment_id, project_id, "index", title, duration, video_url, status) '
        "VALUES (?,?,?,?,?,?,?)",
        (sid, pid, i, "seg%d" % i, 5, "/file?name=" + os.path.basename(c), "succeeded"))
conn.commit()

amount = points_engine.estimate_credits({"duration": 10, "resolution": "720p", "audio": True})["estimated_credits"]
pa = points_engine.pre_auth(amount, "synthesize", "idem_e2e_" + str(int(time.time())))
pre_id = pa["pre_auth_id"]
print("pre_auth_id:", pre_id, "amount:", amount)

tid = uid()
local_id = "syn_e2e" + str(int(time.time()))
conn.execute(
    """INSERT INTO generation_tasks (task_id, segment_id, local_task_id, model_id, params,
       pre_auth_id, cost_credits, status)
       VALUES (?,?,?,?,?,?,?,?)""",
    (tid, seg_ids[0], local_id, "synthesize", "{}", pre_id, amount, "queued"))
conn.commit()
conn.close()

conn = get_conn()
row = conn.execute("SELECT * FROM generation_tasks WHERE task_id=?", (tid,)).fetchone()
task = dict(row_to_dict(row))
conn.close()
print("插入任务:", task["task_id"], task["model_id"], task["status"])

holder = {}

def run():
    try:
        worker._serve_one(task)
        holder["ok"] = True
    except Exception as e:
        holder["exc"] = repr(e)

th = threading.Thread(target=run)
t0 = time.time()
th.start()
th.join(timeout=30)
elapsed = time.time() - t0

if th.is_alive():
    print("HANG: worker._serve_one 未在 30s 内返回 —— 合成全集仍卡死！")
    sys.exit(2)

print("worker 返回耗时: %.2fs | holder=%s" % (elapsed, holder))

conn = get_conn()
row = conn.execute(
    "SELECT status, result_url, error_message FROM generation_tasks WHERE task_id=?",
    (tid,)).fetchone()
res = dict(row_to_dict(row))
conn.close()
print("任务结果 -> status:", res["status"], "| result_url:", res["result_url"], "| err:", res["error_message"])

out = os.path.join(OUT, "synth_%s.mp4" % pid)
print("输出存在:", os.path.isfile(out))
dur_ok = False
if os.path.isfile(out):
    d = ff_duration(out)
    dur_ok = d is not None and abs(d - 10.0) < 0.5
    print("输出时长: %s s -> %s" % (d, "PASS" if dur_ok else "FAIL"))

# ---- 清理 ----
try:
    conn = get_conn()
    conn.execute("DELETE FROM generation_tasks WHERE task_id=?", (tid,))
    conn.execute("DELETE FROM segments WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
    conn.commit()
    conn.close()
    try:
        points_engine.refund(pre_id)  # 退还冻结金，复原余额
    except Exception as e:
        print("refund note:", e)
except Exception as e:
    print("cleanup err:", e)
for f in (out, ca, cb):
    if os.path.isfile(f):
        try:
            os.remove(f)
        except Exception:
            pass
print("DONE")
