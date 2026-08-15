#!/usr/bin/env python3
"""P1 M2 Lock 锁定阶段 · 参考图集逻辑离线测试（不依赖 ComfyUI / InsightFace）。

覆盖：
  ① get_reference_set 排序（标准肖像优先 + 4 张参考图按 REF_KINDS 顺序）+ has_vector 标志正确；
  ② regenerate_one 对未知 ref_kind 诚实抛 ValueError（不触碰 DB / ComfyUI）；
  ③ _store_reference 幂等 upsert（同 asset_id+ref_kind 覆盖而非重复）。

全部走自建临时 sqlite（D 盘 .tmp），不污染全局 MANJU_DB_PATH。
"""
import os
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # server/

import features  # reference_set 模块（features 包入口）

# 保存原 REF_OUT，测试内 monkeypatch 落真实文件后还原（避免污染真实 outputs 目录）
_ORIG_REF_OUT = features.reference_set.REF_OUT

_TMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp")
os.makedirs(_TMP, exist_ok=True)

_REF_KINDS = [
    "standard_portrait", "front", "three_quarter", "expression", "full_body",
]


def _make_conn():
    fd, path = tempfile.mkstemp(suffix=".db", dir=_TMP)
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE asset_reference_set ("
        " ref_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL,"
        " ref_kind TEXT NOT NULL CHECK(ref_kind IN"
        " ('standard_portrait','front','three_quarter','expression','full_body')),"
        " image_url TEXT, face_vector TEXT, created_at TEXT)"
    )
    conn.commit()
    return conn, path


class ReferenceSetM2Test(unittest.TestCase):
    def setUp(self):
        self.conn, self._tmp_path = _make_conn()

    def tearDown(self):
        try:
            self.conn.close()
        finally:
            # temp 文件落在 D:\AI 下，可能被 safe-delete 垫片拦截（环境性，非逻辑缺陷）；
            # 删除被拦截时静默忽略，避免污染测试结果。文件名由 mkstemp 保证唯一，无残留风险。
            if self._tmp_path and os.path.exists(self._tmp_path):
                try:
                    os.remove(self._tmp_path)
                except OSError:
                    pass

    def test_get_reference_set_ordering_and_vector_flag(self):
        # 用临时 REF_OUT 落真实图片，验证「有图 + 有向量 → has_vector=True」且顺序正确
        tmp_ref = self._tmp_path + ".refdir"
        os.makedirs(tmp_ref, exist_ok=True)
        features.reference_set.REF_OUT = tmp_ref
        try:
            rows = [
                ("standard_portrait", "ref-a-standard_portrait.png", "VEC_SP"),
                ("full_body", "ref-a-full_body.png", None),          # 无向量
                ("front", "ref-a-front.png", "VEC_FRONT"),
                ("three_quarter", "ref-a-three_quarter.png", None),  # 无向量
                ("expression", "ref-a-expression.png", "VEC_EXP"),
            ]
            for i, (kind, fname, fv) in enumerate(rows):
                if fv:  # 仅给有向量的行落真实文件，无向量行不落（验证 image_exists 取决于文件）
                    with open(os.path.join(tmp_ref, fname), "wb") as f:
                        f.write(b"\x89PNG\r\n\x1a\n")  # 伪 PNG 头，仅验证文件存在性
                self.conn.execute(
                    "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at)"
                    " VALUES (?,?,?,?,?,strftime('now'))",
                    ("REF-%d" % i, "asset_a", kind, "/file?name=" + fname, fv),
                )
            self.conn.commit()

            out = features.get_reference_set(self.conn, "asset_a")
            self.assertEqual([r["ref_kind"] for r in out], _REF_KINDS,
                             "参考图集必须按 REF_KINDS 顺序返回（标准肖像优先）")
            by_kind = {r["ref_kind"]: r for r in out}
            self.assertTrue(by_kind["standard_portrait"]["has_vector"])
            self.assertTrue(by_kind["front"]["has_vector"])
            self.assertTrue(by_kind["expression"]["has_vector"])
            self.assertFalse(by_kind["full_body"]["has_vector"], "无向量必须 has_vector=False")
            self.assertFalse(by_kind["three_quarter"]["has_vector"])
            self.assertEqual(by_kind["front"]["image_url"], "/file?name=ref-a-front.png")
            # 全部有图 → 不应标记 image_missing
            self.assertFalse(any(r["image_missing"] for r in out),
                             "有图有向量不应误报 image_missing")
        finally:
            features.reference_set.REF_OUT = _ORIG_REF_OUT

    def test_get_reference_set_vector_without_image_flagged_missing(self):
        # 红线：有向量但对应图片缺失（跨机/跨项目导入未带文件、半落库）→ 不得谎报 512维✓
        tmp_ref = self._tmp_path + ".refdir2"
        os.makedirs(tmp_ref, exist_ok=True)
        features.reference_set.REF_OUT = tmp_ref
        try:
            self.conn.execute(
                "INSERT INTO asset_reference_set (ref_id, asset_id, ref_kind, image_url, face_vector, created_at)"
                " VALUES (?,?,?,?,?,strftime('now'))",
                ("REF_X", "asset_x", "front", "/file?name=ref-x-front.png", "VEC_X"))
            self.conn.commit()
            out = features.get_reference_set(self.conn, "asset_x")
            self.assertEqual(len(out), 1)
            r = out[0]
            self.assertFalse(r["has_vector"],
                             "有向量但图缺失 → has_vector 必须为 False（不谎报512维）")
            self.assertTrue(r["image_missing"],
                            "有向量但图缺失 → image_missing 必须为 True")
            self.assertFalse(r["image_exists"])
        finally:
            features.reference_set.REF_OUT = _ORIG_REF_OUT

    def test_resolve_ref_local_path_and_copy(self):
        # resolve_ref_local_path 命中 / 缺失；copy_ref_image_to_asset 实体复制 + 源缺失返回 ''
        tmp_ref = self._tmp_path + ".refdir3"
        os.makedirs(tmp_ref, exist_ok=True)
        features.reference_set.REF_OUT = tmp_ref
        try:
            src = os.path.join(tmp_ref, "ref-src-front.png")
            with open(src, "wb") as f:
                f.write(b"IMG")
            self.assertEqual(
                features.reference_set.resolve_ref_local_path("/file?name=ref-src-front.png"), src)
            self.assertIsNone(
                features.reference_set.resolve_ref_local_path("/file?name=nope.png"))

            new_url = features.reference_set.copy_ref_image_to_asset(
                "/file?name=ref-src-front.png", "asset_new", "front")
            self.assertEqual(new_url, "/file?name=ref-asset_new-front.png")
            self.assertTrue(os.path.isfile(os.path.join(tmp_ref, "ref-asset_new-front.png")),
                            "实体复制必须产生新文件（跨机/跨项目导入带图）")
            self.assertEqual(
                features.reference_set.copy_ref_image_to_asset("/file?name=ghost.png", "asset_new", "full_body"),
                "", "源缺失必须返回 ''（诚实无图，待新机重新生成）")
        finally:
            features.reference_set.REF_OUT = _ORIG_REF_OUT

    def test_get_reference_set_empty(self):
        out = features.get_reference_set(self.conn, "asset_empty")
        self.assertEqual(out, [])

    def test_regenerate_one_bad_kind_raises_valueerror(self):
        # 未知 ref_kind 必须在触碰 DB / ComfyUI 前诚实抛错
        with self.assertRaises(ValueError):
            features.regenerate_one("asset_a", "side_profile")
        with self.assertRaises(ValueError):
            features.regenerate_one("asset_a", "")

    def test_store_reference_upsert_idempotent(self):
        rid1 = features.reference_set._store_reference(self.conn, "asset_b", "front",
                                         "/file?name=ref-b-front.png", "VEC1")
        self.conn.commit()
        rid2 = features.reference_set._store_reference(self.conn, "asset_b", "front",
                                         "/file?name=ref-b-front.png", "VEC2")
        self.conn.commit()
        self.assertEqual(rid1, rid2, "同 asset_id+ref_kind 应幂等覆盖，ref_id 不变")
        cnt = self.conn.execute(
            "SELECT COUNT(*) AS c FROM asset_reference_set WHERE asset_id=? AND ref_kind=?",
            ("asset_b", "front"),
        ).fetchone()["c"]
        self.assertEqual(cnt, 1, "不应产生重复记录")
        fv = self.conn.execute(
            "SELECT face_vector FROM asset_reference_set WHERE ref_id=?", (rid1,)
        ).fetchone()["face_vector"]
        self.assertEqual(fv, "VEC2", "覆盖后应为最新向量")


if __name__ == "__main__":
    unittest.main()
