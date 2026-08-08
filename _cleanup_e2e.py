# -*- coding: utf-8 -*-
"""清理 _verify_worker.py 遗留的测试数据（按 FK 顺序删：logs→tasks→segments→projects，以及预授权积分）。"""
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "server"))
from db import get_conn

conn = get_conn()
rows = conn.execute(
    "SELECT txn_id, idempotency_key FROM point_transactions WHERE idempotency_key LIKE 'idem_e2e%'"
).fetchall()
for r in rows:
    conn.execute("DELETE FROM point_transactions WHERE idempotency_key=?", (r["idempotency_key"],))
    conn.execute("DELETE FROM point_transactions WHERE idempotency_key=?", ("refund-" + r["txn_id"],))
pre_n = len(rows)

tids = [r["task_id"] for r in conn.execute(
    "SELECT task_id FROM generation_tasks WHERE task_id LIKE 'e2e%' OR local_task_id LIKE 'syn_e2e%'").fetchall()]
for tid in tids:
    conn.execute("DELETE FROM generation_logs WHERE task_id=?", (tid,))
for tid in tids:
    conn.execute("DELETE FROM generation_tasks WHERE task_id=?", (tid,))

pids = [r["project_id"] for r in conn.execute(
    "SELECT project_id FROM projects WHERE project_id LIKE 'e2e_syn%'").fetchall()]
for pid in pids:
    conn.execute("DELETE FROM segments WHERE project_id=?", (pid,))
for pid in pids:
    conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))

conn.commit()
conn.close()
print("cleaned: pre_auths=%d, tasks=%d, projects=%d" % (pre_n, len(tids), len(pids)))
