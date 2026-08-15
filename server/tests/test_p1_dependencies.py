#!/usr/bin/env python3
"""P1 依赖打通 + 编排函数单测（手册 §0.3 / §5）。

覆盖（全部离线，不依赖 ComfyUI / InsightFace / Ollama 真实服务）：
  §0.3 P0 三依赖真实可用：
    ① load_face_vector(asset_id) 按资产返回主阶段 512 向量；load_face_vector_by_stage 不回归；
    ② load_reference_image(asset_id) 返回标准肖像本地路径（无则诚实 None）；
    ③ find_ipadapter_weight() 缺失抛 FeatureUnavailable（红线，禁 None/静默）。
  §5 编排函数真实代码 + 断言：
    ④ assemble_identity_card：三步入库（抽向量 + Ollama 属性 + 一致句），返回含向量状态；
    ⑤ inject_consistency：四杠杆落到 intent（subjects[role=identity] + first_frame + 一致句）；
    ⑥ import_role_card：深拷贝（新主键 + 向量实体复制）+ 同名幂等；
  M2 红线（§6）：参考图集光线统一来自 P0 M5 风格锚点（注入 warm_cinematic，非写死）。

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
from capabilities.consistency import inject_consistency


def _real_vec_b64():
    v = np.random.RandomState(0).rand(512).astype(np.float32)
    return base64.b64encode(v.tobytes()).decode("ascii")


class P1DependencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.DB_PATH = _DB
        db.init_db()
        c = db.get_conn()
        try:
            c.execute("INSERT INTO projects (project_id, user_id, name) VALUES (?,?,?)",
                      ("proj1", db.SINGLE_USER_ID, "P1项目"))
            # 光线测试资产（挂在 proj1，有风格锚点）
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global, "
                "outfit, hairstyle, body_type, consistency_sentence) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("lit_asset", db.SINGLE_USER_ID, "林默", "role", "proj1", 0,
                 "黑风衣", "短发", "高瘦", "林默，黑风衣，短发"))
            # M2 红线：proj1 风格锚点（warm_cinematic，同连接插入，避免并发锁）
            c.execute(
                "INSERT INTO style_anchors (anchor_id, project_id, reference_image, lighting, color_temperature, mood) "
                "VALUES (?,?,?,?,?,?)",
                (db.uid(), "proj1", "/ref.png", "warm_cinematic", 3200, "都市情感"))
            # inject 测试资产
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global, "
                "outfit, hairstyle, body_type, consistency_sentence) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("inj_asset", db.SINGLE_USER_ID, "周岩", "role", "proj1", 0,
                 "黑色校服", "齐肩短发", "瘦削", "周岩，黑色校服，齐肩短发"))
            c.execute(
                "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at) "
                "VALUES (?,?,?,?,?,strftime('now'))",
                ("R-SP", "inj_asset", "standard_portrait", "/file?name=inj-sp.png", "VEC",))
            # assemble 测试资产（无身份卡）
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global) "
                "VALUES (?,?,?,?,?,?)",
                ("asm_asset", db.SINGLE_USER_ID, "苏沐", "role", "proj1", 0))
            # import 测试源角色（含向量 + 参考图集）
            c.execute(
                "INSERT INTO assets (asset_id, user_id, name, type, project_id, is_global, "
                "outfit, hairstyle, body_type, consistency_sentence) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("src_r", db.SINGLE_USER_ID, "可复用", "role", "srcp", 0,
                 "红裙", "长发", "丰满", "可复用，红裙，长发"))
            c.execute(
                "INSERT INTO asset_stages (stage_id, asset_id, stage_name, age, face_vector, selected, created_at) "
                "VALUES (?,?,?,?,?,?,strftime('now'))",
                ("ST1", "src_r", "默认", 20, _real_vec_b64(), 1))
            c.execute(
                "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at) "
                "VALUES (?,?,?,?,?,strftime('now'))",
                ("RR1", "src_r", "standard_portrait", "/file?name=src-sp.png", "VEC_SP"))
            c.commit()
        finally:
            c.close()

    def setUp(self):
        # 每测试强制本模块独占 temp 库：db.DB_PATH 是模块级全局，
        # pytest-randomly 跨文件重排时其他模块的 setUpClass 可能改写它，
        # 故每个测试前重新钉死，确保无论如何重排都命中本模块 _DB。
        db.DB_PATH = _DB

    def test_load_face_vector_by_asset(self):
        # 独立资产，避免与 test_assemble_identity_card 共享 asm_asset 造成交叉耦合
        aid = db.uid()
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO assets (asset_id, user_id, name, type) VALUES (?,?,?,?)",
                (aid, db.SINGLE_USER_ID, "载人资产", "role"))
            sid = db.uid()
            conn.execute(
                "INSERT INTO asset_stages (stage_id, asset_id, stage_name, age, face_vector, selected, created_at) "
                "VALUES (?,?,?,?,?,?,strftime('now'))",
                (sid, aid, "默认", 18, _real_vec_b64(), 1))
            conn.commit()
        finally:
            conn.close()
        vec = features.load_face_vector(aid)
        self.assertIsNotNone(vec, "load_face_vector(asset_id) 应返回主阶段 512 向量（P0 依赖打通）")
        self.assertEqual(getattr(vec, "shape", (0,))[0], 512)

    def test_load_face_vector_by_stage_no_regression(self):
        vec = features.load_face_vector_by_stage("ST1")
        self.assertIsNotNone(vec, "阶段级 load_face_vector_by_stage 不应因签名重构而回归")
        self.assertEqual(getattr(vec, "shape", (0,))[0], 512)

    # ---------------------------------------------------------------- §0.3 ②
    def test_load_reference_image(self):
        # 无标准肖像 → None（诚实）
        self.assertIsNone(features.load_reference_image("lit_asset"))
        # 有记录但本地文件不存在 → None（诚实，不静默返回无效路径）
        self.assertIsNone(features.load_reference_image("inj_asset"))
        # 创建本地文件后 → 返回可服务的本地路径
        fname = "ref-inj_asset-standard_portrait.png"
        os.makedirs(features.reference_set.REF_OUT, exist_ok=True)
        target = os.path.join(features.reference_set.REF_OUT, fname)
        with open(target, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        try:
            conn = db.get_conn()
            conn.execute("UPDATE asset_reference_set SET image_url=? WHERE ref_id=?",
                         ("/file?name=" + fname, "R-SP"))
            conn.commit()
            conn.close()
            p = features.load_reference_image("inj_asset")
            self.assertIsNotNone(p, "load_reference_image 应解析 /file?name= → 本地路径")
            self.assertTrue(os.path.isfile(p))
        finally:
            if os.path.exists(target):
                try:
                    os.remove(target)
                except OSError:
                    # 安全删除垫片在回收站不可用时 fail-closed 拦截 os.remove；
                    # 该临时文件位于 outputs/references，下一轮会被同名覆盖，无害。
                    pass

    # ---------------------------------------------------------------- §0.3 ③ §6 红线
    def test_find_ipadapter_weight_raises_when_missing(self):
        with mock.patch("comfy_env.discover_weight", side_effect=RuntimeError("missing")):
            with self.assertRaises(features.FeatureUnavailable):
                features.find_ipadapter_weight()

    def test_find_ipadapter_weight_returns_path(self):
        with mock.patch("comfy_env.discover_weight", return_value="ip-adapter_test.safetensors"):
            self.assertEqual(features.find_ipadapter_weight(), "ip-adapter_test.safetensors")

    # ---------------------------------------------------------------- §5 ④ M1
    def test_assemble_identity_card(self):
        fake_vec = np.zeros(512, dtype=np.float32)
        with mock.patch.object(features.identity_card, "extract_face_vector", return_value=fake_vec), \
             mock.patch("ai_local.ollama_chat",
                        return_value='{"outfit":"白衬衫","hairstyle":"寸头","body_type":"健壮"}'):
            card = features.assemble_identity_card("asm_asset", "/some/ref.png", "健壮寸头男")
        self.assertIsNotNone(card)
        self.assertTrue(card["has_face_vector"], "assemble_identity_card 后应含 512 维向量")
        self.assertEqual(card["outfit"], "白衬衫")
        self.assertIn("白衬衫", card["consistency_sentence"])
        self.assertIn("寸头", card["consistency_sentence"])

    # ---------------------------------------------------------------- §5 ⑤ M3
    def test_inject_consistency_four_levers(self):
        intent = {"task_type": "shot", "subjects": []}
        out = inject_consistency(intent, "inj_asset")
        self.assertEqual(out["subjects"][0]["role"], "identity", "杠杆1：role=identity")
        self.assertEqual(out["subjects"][0]["asset_id"], "inj_asset")
        self.assertEqual(out["first_frame_path"], "/file?name=inj-sp.png", "杠杆2：首帧=标准肖像")
        self.assertIn("周岩", out["consistency_sentence"], "杠杆4：一致句进 prompt")

    # ---------------------------------------------------------------- §5 ⑥ M4
    def test_import_role_card_deep_copy_and_idempotent(self):
        import routers
        res = routers.import_role_card("src_r", "dstp")
        self.assertTrue(res["imported"])
        self.assertNotEqual(res["asset_id"], "src_r", "深拷贝必须生成新主键")
        conn = db.get_conn()
        try:
            real = conn.execute("SELECT face_vector FROM asset_stages WHERE asset_id=? AND selected=1",
                                (res["asset_id"],)).fetchone()
            self.assertEqual(real["face_vector"], _real_vec_b64(), "512 维向量必须实体复制")
            ref = conn.execute("SELECT face_vector FROM asset_reference_set WHERE asset_id=? AND ref_kind='standard_portrait'",
                               (res["asset_id"],)).fetchone()
            self.assertEqual(ref["face_vector"], "VEC_SP")
        finally:
            conn.close()
        # 同名幂等
        res2 = routers.import_role_card("src_r", "dstp")
        self.assertTrue(res2["skipped"], "同名应幂等跳过")
        self.assertEqual(res2["asset_id"], res["asset_id"])

    # ---------------------------------------------------------------- M2 红线 §6
    def test_lighting_from_style_anchor_not_hardcoded(self):
        anchor = features.get_style_anchor_for_asset("lit_asset")
        self.assertEqual(anchor.get("lighting"), "warm_cinematic", "光线必须来自 P0 M5 风格锚点")
        prompt = features.reference_set._build_prompt("lit_asset", "front", anchor)
        self.assertIn("warm cinematic lighting", prompt, "4 张光线应注入同一锚点值")
        # 无锚点 → 中性兜底（证明无写死）
        prompt2 = features.reference_set._build_prompt("lit_asset", "front", None)
        self.assertIn("neutral balanced lighting", prompt2)


if __name__ == "__main__":
    unittest.main()
