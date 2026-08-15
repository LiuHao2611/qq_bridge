#!/usr/bin/env python3
"""P1 M4 复用 · 角色卡跨项目深拷贝离线测试（不依赖 ComfyUI / insightface）。

通过真实端点 import_role_asset_endpoint 验证：
  ① 深拷贝：身份卡四列 + asset_stages(含 face_vector 实体) + asset_reference_set(含 face_vector 实体)
     全部实体复制，主键用 uid() 重新生成（杜绝浅拷贝 / 共享引用）；
  ② 同名幂等：同一 project 下已有同名 role 资产时返回 skipped=true，不重复创建；
  ③ 红线：源资产非 role 类型时诚实报 NOT_ROLE_ASSET；源不存在报 ASSET_NOT_FOUND。

全部走自建临时 sqlite（D 盘 .tmp），不污染全局 MANJU_DB_PATH。
"""
import os
import sys
import tempfile
import unittest
from typing import Any

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER)

_TMP = os.path.join(_SERVER, ".tmp")
os.makedirs(_TMP, exist_ok=True)
_fd, _DB = tempfile.mkstemp(suffix=".db", dir=_TMP)
os.close(_fd)
os.environ["MANJU_DB_PATH"] = _DB


class _FakeReq:
    """最小 Request 替身，覆盖 _body 所需的 headers / body() / json()。"""
    def __init__(self, data: dict):
        self._data = data
        self.headers = {"content-length": str(len(str(data)))}

    async def body(self) -> bytes:
        import json as _json
        return _json.dumps(self._data).encode("utf-8")

    async def json(self) -> dict:
        return self._data


def _conn():
    import db
    return db.get_conn()


class RoleImportM4Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db
        cls.db = db
        db.DB_PATH = _DB  # 强制本模块独占 temp 库，避免与 M2/M3 串库（DB_PATH 在导入期冻结）
        db.init_db()
        c = db.get_conn()
        try:
            # 源角色卡：挂在另一个项目 src_proj，带完整身份卡四列 + role_type + 参考集
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global, "
                "role_type, description, cover_url, outfit, hairstyle, body_type, consistency_sentence) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("src_role", db.SINGLE_USER_ID, "周岩", "role", "src_proj", 0,
                 "protagonist", "源角色", "/cover.png",
                 "黑色校服外套", "齐肩短发", "瘦削170cm", "周岩，黑色校服外套，齐肩短发"),
            )
            # 源资产阶段（含 512 维 face_vector 实体）
            c.execute(
                "INSERT INTO asset_stages (stage_id, asset_id, stage_name, age, "
                "reference_image_url, face_vector, selected, created_at) "
                "VALUES (?,?,?,?,?,?,?,strftime('now'))",
                ("STAGE_SRC", "src_role", "默认", 17, "/stage.png", "BASE64VEC512_SRC", 1),
            )
            # 源参考图集（含向量实体；标准肖像 + 4 参考）
            refs = [
                ("standard_portrait", "/sp.png", "VEC_SP"),
                ("front", "/f.png", "VEC_FRONT"),
                ("three_quarter", "/tq.png", None),
                ("expression", "/e.png", "VEC_EXP"),
                ("full_body", "/fb.png", "VEC_FB"),
            ]
            for kind, url, fv in refs:
                c.execute(
                    "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at) "
                    "VALUES (?,?,?,?,?,strftime('now'))",
                    ("REF_" + kind, "src_role", kind, url, fv),
                )
            # 一个非 role 资产（用于红线测试）
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)",
                ("src_scene", db.SINGLE_USER_ID, "街景", "scene", "src_proj", 0),
            )
            c.commit()
        finally:
            c.close()

    def setUp(self):
        import routers
        self.routers = routers
        # 每测试钉死本模块独占 temp 库：db.DB_PATH 是模块级全局，
        # pytest-randomly 跨文件重排时其他模块 setUpClass 可能改写它。
        import db
        db.DB_PATH = _DB

    def _call_import(self, project_id, asset_id):
        import asyncio
        resp = asyncio.run(self.routers.import_role_asset_endpoint(
            project_id, _FakeReq({"asset_id": asset_id})))
        return resp["data"]  # ok() 包裹在 data 下

    def test_deep_copy_carries_vectors_and_identity(self):
        out = self._call_import("dst_proj", "src_role")
        self.assertTrue(out["imported"], "应成功深拷贝")
        self.assertFalse(out["skipped"])
        new_id = out["asset"]["asset_id"]
        self.assertNotEqual(new_id, "src_role", "必须生成新主键（深拷贝）")

        c = _conn()
        try:
            row = c.execute(
                "SELECT name, type, project_id, is_global, outfit, hairstyle, body_type, "
                "consistency_sentence, role_type FROM assets WHERE asset_id=?",
                (new_id,)).fetchone()
            self.assertIsNotNone(row, "目标角色卡应落库")
            self.assertEqual(row["name"], "周岩")
            self.assertEqual(row["type"], "role")
            self.assertEqual(row["project_id"], "dst_proj")
            self.assertEqual(row["is_global"], 0)
            # 身份卡四列逐字复制
            self.assertEqual(row["outfit"], "黑色校服外套")
            self.assertEqual(row["hairstyle"], "齐肩短发")
            self.assertEqual(row["body_type"], "瘦削170cm")
            self.assertEqual(row["consistency_sentence"], "周岩，黑色校服外套，齐肩短发")
            self.assertEqual(row["role_type"], "protagonist")

            # 阶段深拷贝：face_vector 实体复制，stage_id 重新生成
            st = c.execute(
                "SELECT stage_id, asset_id, face_vector, selected FROM asset_stages "
                "WHERE asset_id=?", (new_id,)).fetchone()
            self.assertIsNotNone(st, "阶段应深拷贝")
            self.assertNotEqual(st["stage_id"], "STAGE_SRC")
            self.assertEqual(st["asset_id"], new_id)
            self.assertEqual(st["face_vector"], "BASE64VEC512_SRC", "512 维向量必须实体复制（非共享引用）")
            self.assertEqual(st["selected"], 1)

            # 参考图集深拷贝：5 张全复制，face_vector 实体复制，ref_id 重新生成
            refs = c.execute(
                "SELECT ref_id, asset_id, ref_kind, image_url, face_vector FROM asset_reference_set "
                "WHERE asset_id=? ORDER BY ref_kind", (new_id,)).fetchall()
            self.assertEqual(len(refs), 5, "参考图集 5 张应全量深拷贝")
            by_kind = {r["ref_kind"]: r for r in refs}
            self.assertEqual(by_kind["standard_portrait"]["face_vector"], "VEC_SP")
            self.assertEqual(by_kind["front"]["face_vector"], "VEC_FRONT")
            self.assertIsNone(by_kind["three_quarter"]["face_vector"])
            self.assertEqual(by_kind["expression"]["face_vector"], "VEC_EXP")
            self.assertEqual(by_kind["full_body"]["face_vector"], "VEC_FB")
            for r in refs:
                self.assertNotEqual(r["ref_id"], "REF_" + r["ref_kind"], "ref_id 必须重新生成")
                self.assertEqual(r["asset_id"], new_id)

            # 源数据未被改动（深拷贝不污染源）
            src_refs = c.execute(
                "SELECT COUNT(*) AS c FROM asset_reference_set WHERE asset_id=?",
                ("src_role",)).fetchone()["c"]
            self.assertEqual(src_refs, 5)
        finally:
            c.close()

    def test_same_name_idempotent_skip(self):
        # 第一次导入
        out1 = self._call_import("dst2", "src_role")
        self.assertTrue(out1["imported"])
        first_id = out1["asset"]["asset_id"]
        # 第二次同名导入（dst2 已有「周岩」）→ 幂等跳过
        out2 = self._call_import("dst2", "src_role")
        self.assertTrue(out2["skipped"], "同名应幂等跳过")
        self.assertFalse(out2["imported"])
        self.assertEqual(out2["asset"]["asset_id"], first_id, "应返回既有资产 id")
        c = _conn()
        try:
            cnt = c.execute(
                "SELECT COUNT(*) AS c FROM assets WHERE project_id=? AND type='role' AND name=?",
                ("dst2", "周岩")).fetchone()["c"]
            self.assertEqual(cnt, 1, "不应重复创建同名角色卡")
        finally:
            c.close()

    def test_not_role_asset_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self._call_import("dst3", "src_scene")
        # 诚实报错，不允许把 scene 当 role 导入（ApiError 仅保留 message）
        self.assertIn("仅支持角色卡导入", ctx.exception.message)

    def test_unknown_asset_not_found(self):
        with self.assertRaises(Exception) as ctx:
            self._call_import("dst4", "no_such")
        self.assertIn("源资产不存在", ctx.exception.message)

    def test_importable_roles_excludes_own_project(self):
        import asyncio
        # 在 probe_proj 自身放一个角色（应被排除），再在 other_proj 放一个（应被纳入）
        c = _conn()
        try:
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)",
                ("own_role", self.db.SINGLE_USER_ID, "本项角色X", "role", "probe_proj", 0))
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)",
                ("other_role", self.db.SINGLE_USER_ID, "跨项角色Y", "role", "other_proj", 0))
            c.commit()
        finally:
            c.close()
        resp = asyncio.run(
            self.routers.importable_roles_endpoint("probe_proj"))
        items = resp["data"]["items"]
        names = [it["name"] for it in items]
        self.assertNotIn("本项角色X", names, "导入列表应排除查询项目自身的角色卡")
        self.assertIn("跨项角色Y", names, "导入列表应纳入其他项目的角色卡")


if __name__ == "__main__":
    unittest.main()
