#!/usr/bin/env python3
"""P1 M4 复用 · 角色卡导入实体复制图片文件测试（不依赖 ComfyUI / insightface）。

验证 import_role_card 在深拷贝角色卡时，把参考图 / 标准肖像 / 阶段参考图
的真实 PNG 文件也复制到新资产名（REF_OUT/ref-<new>-<kind>.png 等），
而非仅指向源文件造成「共享引用」——这是跨项目 / 跨机（兼容多个电脑）可用性的硬要求。

全部走自建临时 sqlite（D 盘 .tmp）+ 临时 REF_OUT，不污染全局 MANJU_DB_PATH 与真实 outputs。
"""
import os
import sys
import tempfile
import unittest

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER)

_TMP = os.path.join(_SERVER, ".tmp")
os.makedirs(_TMP, exist_ok=True)
_fd, _DB = tempfile.mkstemp(suffix=".db", dir=_TMP)
os.close(_fd)
os.environ["MANJU_DB_PATH"] = _DB


class _FakeReq:
    def __init__(self, data):
        self._data = data
        self.headers = {"content-length": str(len(str(data)))}

    async def body(self):
        import json as _json
        return _json.dumps(self._data).encode("utf-8")

    async def json(self):
        return self._data


def _conn():
    import db
    return db.get_conn()


class RoleImportFilesM4Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db
        import features
        cls.db = db
        cls.features = features
        db.DB_PATH = _DB
        db.init_db()
        # 临时 REF_OUT：落真实源文件，验证导入后实体复制
        cls.ref_out = _DB + ".refout"
        os.makedirs(cls.ref_out, exist_ok=True)
        features.reference_set.REF_OUT = cls.ref_out

        cls.ref_dir = cls.ref_out
        c = db.get_conn()
        try:
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)",
                ("src_role_zt42", db.SINGLE_USER_ID, "周岩", "role", "src_proj", 0))
            # 阶段：带真实参考图文件
            c.execute(
                "INSERT INTO asset_stages (stage_id, asset_id, stage_name, age, "
                "reference_image_url, face_vector, selected, created_at) "
                "VALUES (?,?,?,?,?,?,?,strftime('now'))",
                ("STAGE_SRC", "src_role_zt42", "默认", 17, "/file?name=stage-src_zt42.png", "VEC_STAGE", 1))
            # 参考图集：标准肖像 + front（带真实文件）
            refs = [
                ("standard_portrait", "ref-src_role_zt42-standard_portrait.png", "VEC_SP"),
                ("front", "ref-src_role_zt42-front.png", "VEC_FRONT"),
                ("three_quarter", "ref-src_role_zt42-three_quarter.png", None),
            ]
            for kind, fname, fv in refs:
                # 仅给「有向量」的参考图落真实源文件；three_quarter 故意不落文件，
                # 用于验证「源无图 → 导入后 image_url 为空（诚实无图，不指向源）」。
                if fv:
                    with open(os.path.join(cls.ref_dir, fname), "wb") as f:
                        f.write(b"\x89PNG\r\n\x1a\n")
                c.execute(
                    "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at) "
                    "VALUES (?,?,?,?,?,strftime('now'))",
                    ("REF_" + kind, "src_role_zt42", kind, "/file?name=" + fname, fv))
            # 标准肖像独立表
            with open(os.path.join(cls.ref_dir, "ref-src_role_zt42-standard_portrait.png"), "ab") as f:
                f.write(b"SP")
            c.execute(
                "INSERT INTO standard_portrait (asset_id, image_path, created_at) VALUES (?,?,strftime('now'))",
                ("src_role_zt42", "/file?name=ref-src_role_zt42-standard_portrait.png"))
            # 阶段参考图文件
            with open(os.path.join(cls.ref_dir, "stage-src_zt42.png"), "wb") as f:
                f.write(b"STAGE")
            c.commit()
        finally:
            c.close()

    @classmethod
    def tearDownClass(cls):
        cls.features.reference_set.REF_OUT = os.path.join(_SERVER, "outputs", "references")

    def setUp(self):
        import db
        import routers
        import features
        db.DB_PATH = _DB
        features.reference_set.REF_OUT = self.ref_out
        self.routers = routers

    def _call_import(self, project_id, asset_id):
        import asyncio
        resp = asyncio.run(self.routers.import_role_asset_endpoint(
            project_id, _FakeReq({"asset_id": asset_id})))
        return resp["data"]

    def test_import_copies_reference_and_stage_images(self):
        out = self._call_import("dst_proj", "src_role_zt42")
        self.assertTrue(out["imported"])
        new_id = out["asset"]["asset_id"]

        c = _conn()
        try:
            # 参考图集：front 应有新资产名文件且 image_url 指向它
            refs = c.execute(
                "SELECT ref_kind, image_url, face_vector, image_url FROM asset_reference_set "
                "WHERE asset_id=? ORDER BY ref_kind", (new_id,)).fetchall()
            by_kind = {r["ref_kind"]: r for r in refs}
            front = by_kind["front"]
            self.assertEqual(front["image_url"], "/file?name=ref-%s-front.png" % new_id,
                             "参考图 image_url 必须实体复制到新资产名")
            self.assertTrue(os.path.isfile(os.path.join(self.ref_out, "ref-%s-front.png" % new_id)),
                            "参考图 PNG 必须被实体复制（跨机导入可显示）")
            # three_quarter 源无文件 → 导入后 image_url 应为 ''（诚实无图）
            self.assertEqual(by_kind["three_quarter"]["image_url"], "",
                             "源无图的参考图导入后 image_url 必须为空（不指向源）")

            # 标准肖像：新资产名文件 + image_path 指向它
            sp = c.execute(
                "SELECT image_path FROM standard_portrait WHERE asset_id=?", (new_id,)).fetchone()
            self.assertIsNotNone(sp, "标准肖像应深拷贝")
            self.assertEqual(sp["image_path"], "/file?name=ref-%s-standard_portrait.png" % new_id)
            self.assertTrue(os.path.isfile(
                os.path.join(self.ref_out, "ref-%s-standard_portrait.png" % new_id)),
                "标准肖像 PNG 必须被实体复制")

            # 阶段：reference_image_url 实体复制到 stage-<new>-0.png
            st = c.execute(
                "SELECT reference_image_url FROM asset_stages WHERE asset_id=?", (new_id,)).fetchone()
            self.assertIsNotNone(st)
            self.assertEqual(st["reference_image_url"], "/file?name=stage-%s-0.png" % new_id)
            self.assertTrue(os.path.isfile(
                os.path.join(self.ref_out, "stage-%s-0.png" % new_id)),
                "阶段参考图 PNG 必须被实体复制")
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
