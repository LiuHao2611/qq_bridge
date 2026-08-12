# -*- coding: utf-8 -*-
"""M2 决策中枢离线单元测试（手册 §3 / 设计 §5）。

离线可跑：通过 MANJU_DB_PATH 指向临时 SQLite 引导迁移，
不依赖任何外部服务（Ollama/ComfyUI/WebSocket）。
"""
import os
import sys
import tempfile
import unittest

# 必须在导入任何 server 模块之前设置；同时会在 setUpClass 中强制覆盖 db.DB_PATH。
_TMP_DB = os.path.join(tempfile.gettempdir(), "manju_m2_decision_test.db")
for _suf in ("", "-wal", "-shm"):
    _p = _TMP_DB + _suf
    if os.path.exists(_p):
        os.remove(_p)
os.environ["MANJU_DB_PATH"] = _TMP_DB

_SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)

import db
import director.decision_engine as de

# 强制覆盖（防止 db 已被其他测试模块先行导入导致 DB_PATH 指向默认库）。
db.DB_PATH = _TMP_DB


class TestDecisionEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db.init_db()  # 在临时库上重建 schema + 执行 M2 迁移

    def setUp(self):
        # 用例隔离：清空相关表（先子后父，满足外键约束）。
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM decisions")
            conn.execute("DELETE FROM operation_logs")
            conn.execute("DELETE FROM generation_tasks")
            conn.execute("DELETE FROM shots")
            conn.execute("DELETE FROM segments")
            conn.execute("DELETE FROM assets")
            conn.execute("DELETE FROM projects")
            conn.execute("DELETE FROM users")
            conn.commit()
        finally:
            conn.close()
        self.pid = "proj-test-001"

    def _seed_project(self, with_roles=False):
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO users (user_id, nickname) VALUES (?,?)",
                (db.SINGLE_USER_ID, "tester"))
            conn.execute(
                "INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
                (self.pid, db.SINGLE_USER_ID, "测试项目"))
            if with_roles:
                conn.execute(
                    "INSERT INTO assets (asset_id, user_id, project_id, name, type, role_type, tags) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("role-1", db.SINGLE_USER_ID, self.pid, "林浩", "role", "protagonist", "[]"))
            conn.commit()
        finally:
            conn.close()

    def test_migration_workflow_json_column_and_decisions_table(self):
        conn = db.get_conn()
        try:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(generation_tasks)").fetchall()]
            self.assertIn("workflow_json", cols)
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
            ).fetchone()
            self.assertIsNotNone(tbl)
            # 索引存在
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_decisions_project_status'"
            ).fetchone()
            self.assertIsNotNone(idx)
        finally:
            conn.close()

    def test_get_project_snapshot_phase_p2_with_roles_no_shots(self):
        # 角色已建、分镜未拆 → P2（启发式：有角色但无 shots）
        self._seed_project(with_roles=True)
        snap = de.get_project_snapshot(self.pid)
        self.assertEqual(snap["project_id"], self.pid)
        self.assertEqual(snap["phase"], "P2")
        self.assertEqual(len(snap["assets"]["roles"]), 1)
        self.assertEqual(snap["assets"]["roles"][0]["name"], "林浩")
        self.assertEqual(snap["shots"], [])
        self.assertEqual(snap["pending_decisions"], [])

    def test_get_project_snapshot_shot_quality_score_mapping(self):
        # 段/镜存在且生成任务带 face_similarity → 映射到 quality_score（status 取任务状态）
        self._seed_project(with_roles=True)
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO segments (segment_id, project_id, \"index\", status) VALUES (?,?,?,?)",
                ("seg-1", self.pid, 1, "succeeded"))
            conn.execute(
                "INSERT INTO shots (shot_id, segment_id, \"index\") VALUES (?,?,?)",
                ("shot-1", "seg-1", 1))
            conn.execute(
                "INSERT INTO generation_tasks "
                "(task_id, segment_id, local_task_id, pre_auth_id, status, face_similarity) "
                "VALUES (?,?,?,?,?,?)",
                ("task-1", "seg-1", "lt-1", "pa-1", "queued", 0.92))
            conn.commit()
        finally:
            conn.close()
        snap = de.get_project_snapshot(self.pid)
        self.assertEqual(snap["phase"], "P3")  # 有镜但无成功生成 → P3
        self.assertEqual(len(snap["shots"]), 1)
        self.assertEqual(snap["shots"][0]["status"], "queued")
        self.assertEqual(snap["shots"][0]["quality_score"], 0.92)  # face_similarity 映射

    def test_get_project_snapshot_phase_p1_when_empty(self):
        self._seed_project(with_roles=False)
        snap = de.get_project_snapshot(self.pid)
        self.assertEqual(snap["phase"], "P1")

    def test_get_project_snapshot_phase_p3_p4_p5(self):
        self._seed_project(with_roles=True)
        conn = db.get_conn()
        try:
            for i, st in enumerate(["queued", "succeeded"]):  # 1 成功 1 未 → P4
                sid = "seg-%d" % (i + 1)
                conn.execute(
                    "INSERT INTO segments (segment_id, project_id, \"index\", status) VALUES (?,?,?,?)",
                    (sid, self.pid, i + 1, "succeeded"))
                conn.execute(
                    "INSERT INTO shots (shot_id, segment_id, \"index\") VALUES (?,?,?)",
                    ("shot-%d" % (i + 1), sid, 1))
                conn.execute(
                    "INSERT INTO generation_tasks "
                    "(task_id, segment_id, local_task_id, pre_auth_id, status) "
                    "VALUES (?,?,?,?,?)",
                    ("task-%d" % (i + 1), sid, "lt-%d" % (i + 1), "pa", st))
            conn.commit()
        finally:
            conn.close()
        snap = de.get_project_snapshot(self.pid)
        self.assertEqual(snap["phase"], "P4")  # 部分成功 → P4

    def test_preflight_returns_a_class_batch_for_p1(self):
        self._seed_project()
        decisions = de.preflight_check(self.pid, "P1")
        self.assertEqual(len(decisions), 2)  # P1 阶段有 2 条 A 类默认
        titles = {d.title for d in decisions}
        self.assertIn("整体视觉风格定调", titles)
        self.assertIn("角色是否使用真人参考", titles)
        for d in decisions:
            self.assertEqual(d.category, "A")
            self.assertEqual(d.status, "pending")
            self.assertTrue(d.decision_id)  # uuid4 非空

    def test_preflight_idempotent(self):
        self._seed_project()
        de.preflight_check(self.pid, "P1")
        de.preflight_check(self.pid, "P1")  # 二次调用应复用，不重复插入
        rows = de.list_decisions(self.pid)
        self.assertEqual(len(rows), 2)

    def test_resolve_decision_updates_status_and_skips_next_preflight(self):
        self._seed_project()
        decisions = de.preflight_check(self.pid, "P1")
        dp = decisions[0]
        updated = de.resolve_decision(self.pid, dp.decision_id, "写实")
        self.assertEqual(updated.status, "resolved")
        self.assertEqual(updated.answer, "写实")

        # resolved 后 preflight 不再重复推送该项（仅剩 1 条 pending）
        again = de.preflight_check(self.pid, "P1")
        pending_titles = {d.title for d in again}
        self.assertNotIn(dp.title, pending_titles)
        self.assertIn("角色是否使用真人参考", pending_titles)

    def test_list_decisions_filter(self):
        self._seed_project()
        de.preflight_check(self.pid, "P1")
        self.assertEqual(len(de.list_decisions(self.pid, status="pending")), 2)
        self.assertEqual(len(de.list_decisions(self.pid, status="resolved")), 0)
        self.assertEqual(len(de.list_decisions(self.pid)), 2)

    def test_log_b_decision_writes_operation_logs(self):
        self._seed_project()
        de.log_b_decision(self.pid, "自动选择底模", "SDXL写实", note="无用户干预")
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM operation_logs WHERE action='director_auto_decision'"
            ).fetchall()
            self.assertEqual(len(rows), 1)
            import json
            diff = json.loads(rows[0]["diff"])
            self.assertEqual(diff["project_id"], self.pid)
            self.assertEqual(diff["choice"], "SDXL写实")
            self.assertEqual(diff["note"], "无用户干预")
        finally:
            conn.close()

    def test_preflight_unknown_phase_raises(self):
        self._seed_project()
        with self.assertRaises(ValueError):
            de.preflight_check(self.pid, "PX")

    def test_resolve_missing_decision_raises(self):
        self._seed_project()
        with self.assertRaises(ValueError):
            de.resolve_decision(self.pid, "no-such-id", "x")


if __name__ == "__main__":
    unittest.main()
