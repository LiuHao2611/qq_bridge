# -*- coding: utf-8 -*-
"""DirectorOrb v2 接口单元测试（设计方案 §3.3 / 开发手册 §4）。

通过 sys.modules 注入轻量 mock（db / common / module_manager / points_engine），
用 FastAPI TestClient 直接驱动真实路由逻辑，不依赖业务服务进程 / 真实 SQLite 文件 / 外部模型。

覆盖：
  - 灰度开关：director_orb_v2 关闭 → 全部接口返回 ORB_DISABLED
  - 接口1 资产就绪度字段形状 + 主角未就绪扣分
  - 接口4 品控摘要字段形状 + failed_segments
  - 接口6 成片质检字段形状
  - 接口5 批量操作（CLOSE-04 资金安全）：regenerate 重排队列 + 审计落库、
    skip 置 cancelled、积分不足 INSUFFICIENT_POINTS、非法 action INVALID_ACTION
"""
import os
import sys
import types
import unittest
import sqlite3
import uuid

_SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)


# ----------------------------------------------------------------------------
# 轻量 mock 注入（必须在 import orb.routes 之前完成）
# ----------------------------------------------------------------------------
_FLAGS = {"director_orb_v2": True}
_POINTS_BALANCE = 999999


def _build_fake_db():
    m = types.ModuleType("db")
    # 共享缓存的纯内存库（跨线程/连接共享同一份数据；check_same_thread=False 允许线程池调用）
    _URI = "file:orbtest?mode=memory&cache=shared"
    keepalive = sqlite3.connect(_URI, uri=True, check_same_thread=False)
    keepalive.row_factory = sqlite3.Row
    _create_schema(keepalive)
    keepalive.commit()
    m._keepalive = keepalive
    m._uri = _URI

    def get_conn():
        c = sqlite3.connect(_URI, uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def fetch_all(conn, sql, params=()):
        # 与真实 db.fetch_all 一致：返回 dict 列表（sqlite3.Row 可转 dict）
        return [dict(r) for r in conn.execute(sql, params or ()).fetchall()]

    def fetch_one(conn, sql, params=()):
        r = conn.execute(sql, params or ()).fetchone()
        return dict(r) if r is not None else None

    def clear():
        k = m._keepalive
        for t in ("operation_logs", "generation_tasks", "segment_asset_refs",
                  "segments", "assets"):
            k.execute("DELETE FROM %s" % t)
        k.commit()

    m.get_conn = get_conn
    m.fetch_all = fetch_all
    m.fetch_one = fetch_one
    m.clear = clear
    m.uid = lambda: uuid.uuid4().hex
    m.SINGLE_USER_ID = "u1"
    return m


def _create_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS assets (
      asset_id TEXT, name TEXT, quality_flags TEXT, role_type TEXT,
      project_id TEXT, type TEXT, is_global INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS segments (
      segment_id TEXT, project_id TEXT, "index" INTEGER, status TEXT,
      scene_description TEXT, duration REAL
    );
    CREATE TABLE IF NOT EXISTS segment_asset_refs (
      segment_id TEXT, asset_id TEXT, asset_type TEXT
    );
    CREATE TABLE IF NOT EXISTS generation_tasks (
      task_id TEXT, segment_id TEXT, status TEXT, face_similarity REAL,
      first_frame_match REAL, degrade_reason TEXT, cost_credits INTEGER,
      params TEXT, retry_count INTEGER
    );
    CREATE TABLE IF NOT EXISTS operation_logs (
      log_id TEXT, user_id TEXT, action TEXT, object_type TEXT, object_id TEXT,
      diff TEXT, status TEXT, cost_credits INTEGER, created_at INTEGER
    );
    """)


def _build_fake_common():
    m = types.ModuleType("common")

    def envelope(code=0, data=None, message="ok", request_id=None):
        return {"code": code, "data": data, "message": message}

    # 与真实 common.ok 一致：第一个位置参数映射到 data（code 固定 0）
    m.ok = lambda data=None, message="ok": envelope(0, data, message)

    class ApiError(Exception):
        def __init__(self, name, detail=None, http_status=None):
            self.code = name
            self.http_status = http_status or 400
            self.message = detail or name

    m.ApiError = ApiError
    return m


def _build_fake_module_manager():
    m = types.ModuleType("module_manager")

    def get_flags():
        return dict(_FLAGS)

    m.get_flags = get_flags
    return m


def _build_fake_points():
    m = types.ModuleType("points_engine")
    m.pre_auth = lambda amount, object_id, idem_key: None
    m.get_points_balance = lambda: _POINTS_BALANCE
    return m


_fake_db = _build_fake_db()
_fake_common = _build_fake_common()
sys.modules["db"] = _fake_db
sys.modules["common"] = _fake_common
sys.modules["module_manager"] = _build_fake_module_manager()
sys.modules["points_engine"] = _build_fake_points()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from orb.routes import register_orb_routes  # noqa: E402


def _make_app():
    api = __import__("fastapi").APIRouter()
    register_orb_routes(api)
    app = FastAPI()
    app.add_exception_handler(_fake_common.ApiError, _handle_api_error)
    app.include_router(api, prefix="/api/v1")
    return app


def _handle_api_error(request: Request, exc):
    return JSONResponse(
        status_code=exc.http_status or 400,
        content={"code": exc.code, "message": exc.message},
    )


def _data(resp):
    """统一解包 ok 信封：优先取 data，否则返回整体。"""
    j = resp.json()
    return j.get("data") if isinstance(j, dict) and "data" in j else j


PID = "proj-1"


class _Base(unittest.TestCase):
    def setUp(self):
        _fake_db.clear()
        _FLAGS["director_orb_v2"] = True
        global _POINTS_BALANCE
        _POINTS_BALANCE = 999999
        self.client = TestClient(_make_app())

    def _seed_assets(self):
        c = _fake_db.get_conn()
        c.executemany(
            "INSERT INTO assets (asset_id,name,quality_flags,role_type,project_id,type,is_global) "
            "VALUES (?,?,?,?,?,?,0)",
            [
                # 主角带质量错误 → 未就绪 → 触发主角未就绪扣分（总分 ≤50）
                ("a1", "主角", '{"blur":{"severity":"error"}}', "protagonist", PID, "role"),
                ("a2", "配角", "{}", "supporting", PID, "role"),
                ("s1", "客厅", "{}", "", PID, "scene"),
                ("p1", "魔法杖", "{}", "", PID, "prop"),
            ],
        )
        # 一个场次没有绑定任何角色 → 触发 missing_role_segments
        c.execute(
            'INSERT INTO segments (segment_id,project_id,"index",status,scene_description,duration) '
            "VALUES (?,?,?,?,?,?)",
            ("seg-miss", PID, 3, "draft", "夜", 5),
        )
        c.commit()

    def _seed_tasks(self):
        c = _fake_db.get_conn()
        c.execute(
            'INSERT INTO segments (segment_id,project_id,"index",status,scene_description,duration) '
            "VALUES (?,?,?,?,?,?)",
            ("s1", PID, 1, "draft", "日", 4),
        )
        c.execute(
            'INSERT INTO segments (segment_id,project_id,"index",status,scene_description,duration) '
            "VALUES (?,?,?,?,?,?)",
            ("s2", PID, 2, "draft", "夜", 6),
        )
        c.executemany(
            "INSERT INTO generation_tasks (task_id,segment_id,status,face_similarity,first_frame_match,degrade_reason,cost_credits,params,retry_count) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("t1", "s1", "succeeded", 0.92, 0.95, None, 100, "{}", 0),
                ("t2", "s1", "failed", 0.55, 0.40, "model_timeout", 0, "{}", 1),
                ("t3", "s2", "queued", None, None, None, 0, "{}", 0),
            ],
        )
        c.commit()


class TestGrayFlag(_Base):
    def test_all_disabled_returns_orb_disabled(self):
        _FLAGS["director_orb_v2"] = False
        for path in ("asset-readiness", "script-analysis", "run-summary", "synthesize-report"):
            r = self.client.get("/api/v1/orb/projects/%s/%s" % (PID, path))
            self.assertEqual(r.status_code, 400, path)
            self.assertEqual(r.json()["code"], "ORB_DISABLED")
        r = self.client.post(
            "/api/v1/orb/projects/%s/batch-action" % PID,
            json={"action": "skip", "segment_ids": ["s1"]},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "ORB_DISABLED")


class TestAssetReadiness(_Base):
    def test_shape_and_primary_penalty(self):
        self._seed_assets()
        r = self.client.get("/api/v1/orb/projects/%s/asset-readiness" % PID)
        self.assertEqual(r.status_code, 200)
        d = _data(r)
        self.assertIn("readiness_score", d)
        self.assertIn("characters", d)
        self.assertIn("scenes", d)
        self.assertIn("props", d)
        self.assertIn("missing_role_segments", d)
        # 主角 a1 带质量错误未就绪、配角 a2 就绪
        chars = {c["id"]: c for c in d["characters"]["items"]}
        self.assertFalse(chars["a1"]["ready"])
        self.assertTrue(chars["a2"]["ready"])
        self.assertFalse(chars["a2"]["is_primary"])
        self.assertTrue(chars["a1"]["is_primary"])
        # 主角未就绪 → 总分扣到 ≤50
        self.assertLessEqual(d["readiness_score"], 50)
        # seg-miss 无角色绑定 → 出现在缺口
        miss_ids = {m["segment_id"] for m in d["missing_role_segments"]}
        self.assertIn("seg-miss", miss_ids)


class TestRunSummary(_Base):
    def test_shape_and_failed_segments(self):
        self._seed_tasks()
        r = self.client.get("/api/v1/orb/projects/%s/run-summary" % PID)
        self.assertEqual(r.status_code, 200)
        d = _data(r)
        self.assertEqual(d["total"], 3)
        self.assertEqual(d["completed"], 1)
        self.assertEqual(d["failed"], 1)
        self.assertEqual(d["running"], 1)
        failed_seg_ids = {f["segment_id"] for f in d["failed_segments"]}
        self.assertIn("s1", failed_seg_ids)
        self.assertGreaterEqual(d["face_similarity_pass_rate"], 0)


class TestSynthesizeReport(_Base):
    def test_degrade_summary(self):
        self._seed_tasks()
        r = self.client.get("/api/v1/orb/projects/%s/synthesize-report" % PID)
        self.assertEqual(r.status_code, 200)
        d = _data(r)
        self.assertEqual(d["generated"], 3)
        self.assertIn("degrade_summary", d)
        self.assertIn("face_similarity_pass_rate", d)
        self.assertIn("model_timeout", d["degrade_summary"])


class TestBatchAction(_Base):
    def test_regenerate_requeues_failed_and_audits(self):
        self._seed_tasks()
        r = self.client.post(
            "/api/v1/orb/projects/%s/batch-action" % PID,
            json={"action": "regenerate", "segment_ids": ["s1"], "params": {}},
        )
        self.assertEqual(r.status_code, 200)
        d = _data(r)
        self.assertGreaterEqual(d["queued"], 1)
        # 失败任务 t2 已被重置为 queued
        c = _fake_db.get_conn()
        status = c.execute("SELECT status FROM generation_tasks WHERE task_id='t2'").fetchone()[0]
        self.assertEqual(status, "queued")
        # 审计落库
        n = c.execute("SELECT COUNT(*) FROM operation_logs").fetchone()[0]
        self.assertGreaterEqual(n, 1)

    def test_skip_cancels(self):
        self._seed_tasks()
        r = self.client.post(
            "/api/v1/orb/projects/%s/batch-action" % PID,
            json={"action": "skip", "segment_ids": ["s1", "s2"], "params": {}},
        )
        self.assertEqual(r.status_code, 200)
        c = _fake_db.get_conn()
        rows = {row["segment_id"]: row["status"] for row in
                c.execute("SELECT segment_id,status FROM generation_tasks").fetchall()}
        self.assertEqual(rows["s1"], "cancelled")
        self.assertEqual(rows["s2"], "cancelled")

    def test_insufficient_points(self):
        global _POINTS_BALANCE
        _POINTS_BALANCE = 0  # regenerate 需 100/分镜，余额不足
        self._seed_tasks()
        r = self.client.post(
            "/api/v1/orb/projects/%s/batch-action" % PID,
            json={"action": "regenerate", "segment_ids": ["s1"], "params": {}},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "INSUFFICIENT_POINTS")
        # 未改动任务状态
        c = _fake_db.get_conn()
        status = c.execute("SELECT status FROM generation_tasks WHERE task_id='t2'").fetchone()[0]
        self.assertEqual(status, "failed")

    def test_invalid_action(self):
        r = self.client.post(
            "/api/v1/orb/projects/%s/batch-action" % PID,
            json={"action": "bogus", "segment_ids": ["s1"], "params": {}},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "INVALID_ACTION")

    def test_empty_segment_ids(self):
        r = self.client.post(
            "/api/v1/orb/projects/%s/batch-action" % PID,
            json={"action": "skip", "segment_ids": [], "params": {}},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "INVALID_PARAMS")


if __name__ == "__main__":
    unittest.main()
