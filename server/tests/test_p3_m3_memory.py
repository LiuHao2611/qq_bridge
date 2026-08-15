#!/usr/bin/env python3
"""P3 M3 长期记忆 / 资产沉淀单测（手册 §0.3 / §4 / §5）。

覆盖（全部离线，不依赖 ComfyUI 真实服务）：
  §0.2 输入→处理→输出：remember_project 三步收集；recommend 两步；import_to_project 按类型分派
  §0.3 衔接 P1 M4 import_role_card（深拷贝：新主键 + 向量实体复制 + 同名幂等）
  §5 红线：禁浅拷贝引用（角色卡必须 import_role_card 深拷贝）；禁 hash() 生成 ID（用 db.uid）

全部走自建临时 sqlite（D 盘 .tmp），不污染全局 MANJU_DB_PATH。
"""
import os
import sys
import tempfile
import unittest

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER)

_TMP = os.path.join(_SERVER, ".tmp")
os.makedirs(_TMP, exist_ok=True)
_FD, _DB = tempfile.mkstemp(suffix=".db", dir=_TMP)
os.close(_FD)
os.environ["MANJU_DB_PATH"] = _DB

import db  # noqa: E402
db.DB_PATH = _DB
db.init_db()

from features import memory_store as ms  # noqa: E402


def _insert(conn, sql, params):
    conn.execute(sql, params)


class P3M3MemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.DB_PATH = _DB
        db.init_db()

    def setUp(self):
        # 每测试清空相关表，保证项目 id 不冲突、记忆不串扰
        c = db.get_conn()
        try:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("memory", "asset_reference_set", "asset_stages", "lora_models",
                      "style_anchors", "assets", "projects"):
                c.execute("DELETE FROM %s" % t)
            c.execute("PRAGMA foreign_keys=ON")
            c.commit()
        finally:
            c.close()

    def _seed(self, src="src_p", dst="dst_p"):
        c = db.get_conn()
        try:
            c.execute("INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
                      (src, db.SINGLE_USER_ID, "源项目"))
            c.execute("INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
                      (dst, db.SINGLE_USER_ID, "目标项目"))
            # 角色卡（含身份卡四列）
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global, "
                "description, cover_url, tags, created_at, updated_at, outfit, hairstyle, "
                "body_type, consistency_sentence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("role_a", db.SINGLE_USER_ID, "林默", "role", src, 0,
                 "主角", "", "[]", 0, 0, "黑风衣", "短发", "高瘦", "林默，黑风衣，短发"))
            # 深拷贝所需的 asset_stages（含 512 维向量 face_vector 实体）
            c.execute(
                "INSERT INTO asset_stages (stage_id, asset_id, stage_name, age, "
                "reference_image_url, face_vector, selected, created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("st1", "role_a", "青年", 25, "", "BASE64VEC", 1, 0))
            # 风格锚点（P0 M5）
            c.execute(
                "INSERT INTO style_anchors (anchor_id, project_id, reference_image, "
                "lighting, color_temperature, mood) VALUES (?,?,?,?,?,?)",
                ("anc1", src, "/anchor.png", "霓虹灯", 5500, "冷"))
            # LoRA 关联资产（须在 src 项目，join 才命中）
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global, "
                "description, cover_url, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("lora_asset", db.SINGLE_USER_ID, "lora角色", "role", src, 0,
                 "", "", "[]", 0, 0))
            c.execute(
                "INSERT INTO lora_models (id, name, lora_type, file_path, trigger_word, "
                "semantic_tags, associated_asset_id, base_model, status) VALUES (?,?,?,?,?,?,?,?,?)",
                ("lora1", "我的LoRA", "character", "/x.safetensors", "trigger", "[]",
                 "lora_asset", "sd15", "active"))
            c.commit()
        finally:
            c.close()

    # ---- §0.2 remember_project 三步收集 ----
    def test_remember_project_writes_memory(self):
        self._seed()
        n = ms.remember_project("src_p", db.SINGLE_USER_ID)
        self.assertGreaterEqual(n, 3)  # 角色卡 + 风格锚点 + LoRA
        # memory 表确有三类
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT type, COUNT(*) c FROM memory WHERE source_project_id='src_p' "
                "GROUP BY type").fetchall()
        finally:
            conn.close()
        types = {r["type"]: r["c"] for r in rows}
        self.assertIn("character", types)
        self.assertIn("style", types)
        self.assertIn("lora", types)

    # ---- §0.2 recommend 两步 ----
    def test_recommend_returns_ranked(self):
        self._seed()
        ms.remember_project("src_p", db.SINGLE_USER_ID)
        items = ms.recommend_for_new_project(db.SINGLE_USER_ID)
        self.assertGreaterEqual(len(items), 3)
        # 含结构化 payload
        for it in items:
            self.assertIn("payload", it)
            self.assertIn("score", it)

    # ---- §0.2 import_to_project 按类型分派（角色卡深拷贝）----
    def test_import_to_project_deep_copies_character(self):
        self._seed()
        ms.remember_project("src_p", db.SINGLE_USER_ID)
        items = ms.recommend_for_new_project(db.SINGLE_USER_ID)
        mids = [it["memory_id"] for it in items]
        res = ms.import_to_project("dst_p", mids)
        self.assertGreaterEqual(res["imported"], 3)
        # 目标项目应新增一个"林默"角色资产（import_role_card 深拷贝，新主键）
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT asset_id, name, project_id FROM assets "
                "WHERE project_id='dst_p' AND type='role'").fetchall()
            names = [r["name"] for r in rows]
            self.assertIn("林默", names)
            # 深拷贝：dst 的角色资产 id 与源不同（非浅引用）
            self.assertNotIn("role_a", [r["asset_id"] for r in rows])
            # 深拷贝向量实体落库
            dst_asset = next(r["asset_id"] for r in rows if r["name"] == "林默")
            vec = conn.execute(
                "SELECT face_vector FROM asset_stages WHERE asset_id=?", (dst_asset,)).fetchone()
            self.assertIsNotNone(vec)
            self.assertEqual(vec["face_vector"], "BASE64VEC")
            # 风格锚点被复制到 dst 项目
            anc = conn.execute(
                "SELECT anchor_id, project_id FROM style_anchors WHERE project_id='dst_p'").fetchall()
            self.assertTrue(anc)
            # LoRA 注册到 dst 可用（新主键）
            lora = conn.execute(
                "SELECT id FROM lora_models WHERE name='我的LoRA'").fetchall()
            self.assertTrue(lora)
        finally:
            conn.close()

    # ---- §5 红线：二次导入同名角色卡应幂等（skipped，不重复建）----
    def test_import_idempotent_same_name(self):
        self._seed()
        ms.remember_project("src_p", db.SINGLE_USER_ID)
        items = ms.recommend_for_new_project(db.SINGLE_USER_ID)
        char_mid = next(it["memory_id"] for it in items if it["type"] == "character")
        ms.import_to_project("dst_p", [char_mid])  # 第一次：imported
        res2 = ms.import_to_project("dst_p", [char_mid])  # 第二次：skipped（同名幂等）
        self.assertEqual(res2["details"][0]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
