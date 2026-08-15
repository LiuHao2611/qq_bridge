#!/usr/bin/env python3
"""P1 D1 修正验收 · 独立身份卡 / 标准肖像表（手册 §4 落地）。

验证（全部离线，机器判定，非零退出）：
  ① 迁移后 identity_card / standard_portrait 两张独立表存在（PRAGMA table_info）；
  ② assemble_identity_card 落库进 identity_card 表（含 outfit/hairstyle/body_type/
     consistency_sentence + 512 维 face_vector），且 assets 列同步（非侵入镜像）；
  ③ get_identity_card 优先读 identity_card 表（sentinel 证明：assets 列清空仍返回表值）；
  ④ generate_standard_portrait 落库进 standard_portrait 表；get_standard_portrait_url /
     load_reference_image 主源为该表（sentinel 证明）；
  ⑤ import_role_card 深拷贝 identity_card + standard_portrait（实体复制，非共享引用）。

全部走自建临时 sqlite（D 盘 .tmp），不污染全局 MANJU_DB_PATH。
"""
import os
import sys
import base64
import tempfile
import unittest
from unittest import mock

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER)

_TMP = os.path.join(_SERVER, ".tmp")
os.makedirs(_TMP, exist_ok=True)
_FD, _DB = tempfile.mkstemp(suffix=".db", dir=_TMP)
os.close(_FD)
os.environ["MANJU_DB_PATH"] = _DB

import db
import numpy as np
import features


def _real_vec_b64(seed=0):
    v = np.random.RandomState(seed).rand(512).astype(np.float32)
    return base64.b64encode(v.tobytes()).decode("ascii")


def _real_vec(seed=0):
    return np.random.RandomState(seed).rand(512).astype(np.float32)


class P1IdentityTablesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.DB_PATH = _DB
        db.init_db()  # 含 §4 独立表迁移 + 回填
        c = db.get_conn()
        try:
            c.execute("INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
                      ("p_d1", db.SINGLE_USER_ID, "D1项目"))
        finally:
            c.close()

    def setUp(self):
        db.DB_PATH = _DB  # 钉死本模块独占 temp 库

    # ---------------------------------------------------------------- ①
    def test_tables_exist_after_migration(self):
        for tbl in ("identity_card", "standard_portrait"):
            cols = [r[1] for r in db.get_conn().execute(
                "PRAGMA table_info(%s)" % tbl).fetchall()]
            self.assertIn("asset_id", cols, "%s 表必须存在且含 asset_id 主键" % tbl)
        # identity_card 五字段齐全
        ic_cols = [r[1] for r in db.get_conn().execute(
            "PRAGMA table_info(identity_card)").fetchall()]
        for f in ("face_vector", "outfit", "hairstyle", "body_type", "consistency_sentence"):
            self.assertIn(f, ic_cols, "identity_card 缺字段 %s" % f)
        # standard_portrait 三字段齐全
        sp_cols = [r[1] for r in db.get_conn().execute(
            "PRAGMA table_info(standard_portrait)").fetchall()]
        for f in ("image_path", "created_at"):
            self.assertIn(f, sp_cols, "standard_portrait 缺字段 %s" % f)

    # ---------------------------------------------------------------- ②
    def test_assemble_writes_independent_identity_card_table(self):
        aid = "asm_d1_" + db.uid()[:8]
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)", (aid, db.SINGLE_USER_ID, "苏沐", "role", "p_d1", 0))
            conn.commit()
        finally:
            conn.close()

        fake_vec = _real_vec(seed=11)
        with mock.patch.object(features.identity_card, "extract_face_vector", return_value=fake_vec), \
             mock.patch("ai_local.ollama_chat",
                        return_value='{"outfit":"白衬衫","hairstyle":"寸头","body_type":"健壮"}'):
            card = features.assemble_identity_card(aid, "/some/ref.png", "健壮寸头男")

        # 主存储：identity_card 表必须有行且四字段正确
        conn = db.get_conn()
        try:
            ic = conn.execute(
                "SELECT outfit, hairstyle, body_type, consistency_sentence, face_vector "
                "FROM identity_card WHERE asset_id=?", (aid,)).fetchone()
            self.assertIsNotNone(ic, "assemble 后 identity_card 表必须有行")
            self.assertEqual(ic["outfit"], "白衬衫")
            self.assertEqual(ic["hairstyle"], "寸头")
            self.assertEqual(ic["body_type"], "健壮")
            self.assertIn("白衬衫", ic["consistency_sentence"])
            self.assertIn("寸头", ic["consistency_sentence"])
            # face_vector 已同步进独立表
            self.assertEqual(ic["face_vector"], _real_vec_b64(11),
                             "512 维向量必须同步进 identity_card.face_vector")
            # 非侵入镜像：assets 四列同步
            a = conn.execute(
                "SELECT outfit, hairstyle, body_type, consistency_sentence FROM assets WHERE asset_id=?",
                (aid,)).fetchone()
            self.assertEqual(a["outfit"], "白衬衫", "assets 列应被镜像（memory_store/import 依赖）")
        finally:
            conn.close()

        self.assertTrue(card["has_face_vector"], "assemble 后应含 512 维向量")
        self.assertEqual(card["outfit"], "白衬衫")

    # ---------------------------------------------------------------- ③ sentinel
    def test_get_identity_card_reads_from_identity_card_table(self):
        aid = "sentinel_" + db.uid()[:8]
        conn = db.get_conn()
        try:
            # assets 四列留空（默认），仅 identity_card 表有值 → 若仍读对，证明表是主源
            conn.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)", (aid, db.SINGLE_USER_ID, "探员", "role", "p_d1", 0))
            conn.execute(
                "INSERT INTO identity_card (asset_id, outfit, hairstyle, body_type, consistency_sentence) "
                "VALUES (?,?,?,?,?)", (aid, "IC_服装", "IC_发型", "IC_体型", "IC_一致句"))
            conn.commit()
        finally:
            conn.close()
        card = features.get_identity_card(db.get_conn(), aid)
        self.assertEqual(card["outfit"], "IC_服装", "get_identity_card 必须优先读 identity_card 表")
        self.assertEqual(card["hairstyle"], "IC_发型")
        self.assertEqual(card["body_type"], "IC_体型")
        self.assertEqual(card["consistency_sentence"], "IC_一致句")

    # ---------------------------------------------------------------- ④
    def test_standard_portrait_table_is_primary_store(self):
        aid = "sp_d1_" + db.uid()[:8]
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)", (aid, db.SINGLE_USER_ID, "林默", "role", "p_d1", 0))
            conn.commit()
        finally:
            conn.close()

        # 造一张真实图片供 _persist_reference_image 复制
        fake_png = os.path.join(_TMP, "fake_sp_%s.png" % aid)
        with open(fake_png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        with mock.patch("ai_local._comfy_text2img", return_value=fake_png), \
             mock.patch.object(features, "extract_face_vector", return_value=_real_vec(seed=22)):
            features.generate_standard_portrait(aid)

        # 标准肖像主存储：standard_portrait 表必须有行
        conn = db.get_conn()
        try:
            sp = conn.execute(
                "SELECT image_path FROM standard_portrait WHERE asset_id=?", (aid,)).fetchone()
            self.assertIsNotNone(sp, "generate_standard_portrait 后 standard_portrait 表必须有行")
            self.assertTrue(sp["image_path"].startswith("/file?name="),
                             "standard_portrait.image_path 应为可服务 URL")
        finally:
            conn.close()

        # get_standard_portrait_url 主源为 standard_portrait 表
        url = features.get_standard_portrait_url(aid)
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith("/file?name="))

        # load_reference_image 主源为 standard_portrait 表（落真实文件可解析）
        local = features.load_reference_image(aid)
        self.assertIsNotNone(local, "load_reference_image 应解析 standard_portrait → 本地路径")
        self.assertTrue(os.path.isfile(local))

    # ---------------------------------------------------------------- ④ sentinel
    def test_standard_portrait_read_primary_even_when_reference_set_cleared(self):
        aid = "sp_sentinel_" + db.uid()[:8]
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)", (aid, db.SINGLE_USER_ID, "确认", "role", "p_d1", 0))
            # 仅 standard_portrait 表有值，asset_reference_set 无该行 → 若读对，证明表是主源
            conn.execute(
                "INSERT INTO standard_portrait (asset_id, image_path, created_at) "
                "VALUES (?,?,strftime('now'))", (aid, "/file?name=sp_sentinel_only.png"))
            conn.commit()
        finally:
            conn.close()
        # 造真实文件，证明 load_reference_image 走 standard_portrait 而非 asset_reference_set
        target = os.path.join(features.reference_set.REF_OUT, "sp_sentinel_only.png")
        os.makedirs(features.reference_set.REF_OUT, exist_ok=True)
        with open(target, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        try:
            self.assertEqual(features.get_standard_portrait_url(aid),
                             "/file?name=sp_sentinel_only.png",
                             "get_standard_portrait_url 必须优先读 standard_portrait 表")
            self.assertTrue(os.path.isfile(features.load_reference_image(aid)),
                            "load_reference_image 必须优先读 standard_portrait 表")
        finally:
            if os.path.exists(target):
                try:
                    os.remove(target)
                except OSError:
                    pass

    # ---------------------------------------------------------------- ⑤
    def test_import_deep_copies_identity_and_standard_portrait(self):
        import routers
        src = "src_d1_" + db.uid()[:8]
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global, "
                "outfit, hairstyle, body_type, consistency_sentence) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (src, db.SINGLE_USER_ID, "可复用D1", "role", "srcp_d1", 0,
                 "红裙", "长发", "丰满", "可复用D1，红裙，长发"))
            conn.execute(
                "INSERT INTO asset_stages (stage_id, asset_id, stage_name, age, face_vector, selected, created_at) "
                "VALUES (?,?,?,?,?,?,strftime('now'))",
                ("ST_D1", src, "默认", 20, _real_vec_b64(33), 1))
            conn.execute(
                "INSERT INTO identity_card (asset_id, face_vector, outfit, hairstyle, body_type, consistency_sentence) "
                "VALUES (?,?,?,?,?,?)",
                (src, _real_vec_b64(33), "红裙", "长发", "丰满", "可复用D1，红裙，长发"))
            conn.execute(
                "INSERT INTO standard_portrait (asset_id, image_path, created_at) "
                "VALUES (?,?,strftime('now'))", (src, "/file?name=src_d1_sp.png"))
            conn.commit()
        finally:
            conn.close()

        res = routers.import_role_card(src, "dstp_d1")
        self.assertTrue(res["imported"])
        new_id = res["asset_id"]
        self.assertNotEqual(new_id, src, "深拷贝必须生成新主键")

        conn = db.get_conn()
        try:
            ic = conn.execute(
                "SELECT outfit, hairstyle, body_type, consistency_sentence, face_vector "
                "FROM identity_card WHERE asset_id=?", (new_id,)).fetchone()
            self.assertIsNotNone(ic, "深拷贝必须复制 identity_card 表行")
            self.assertEqual(ic["outfit"], "红裙")
            self.assertEqual(ic["hairstyle"], "长发")
            self.assertEqual(ic["body_type"], "丰满")
            self.assertEqual(ic["consistency_sentence"], "可复用D1，红裙，长发")
            self.assertEqual(ic["face_vector"], _real_vec_b64(33), "identity_card.face_vector 必须实体复制")

            sp = conn.execute(
                "SELECT image_path FROM standard_portrait WHERE asset_id=?", (new_id,)).fetchone()
            self.assertIsNotNone(sp, "深拷贝必须复制 standard_portrait 表行")
            self.assertEqual(sp["image_path"], "/file?name=src_d1_sp.png")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
