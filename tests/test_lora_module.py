# -*- coding: utf-8 -*-
"""LoRA 训练模块功能测试（离线、可独立运行）。

覆盖手册 §3 / §4 / §7 / §8 / §9 关键契约：
  1. 迁移建表（lora_train_tasks / lora_models）
  2. 训练引擎缺失诚实降级（detect_engine 返回 available=False + reason）
  3. build_train_config 生成合法 TOML（含强制开关 fp16/xformers/gradient_checkpointing）
  4. caption 前缀规则（角色 LoRA 强制触发词前缀，风格 LoRA 不加）
  5. find_best_lora 语义匹配 + 底模架构兼容过滤 + 角色资产强绑定
  6. 翻译器模板无关注入 LoraLoader 节点（合成 workflow + 真实模板）

运行：python tests/test_lora_module.py
（不依赖 pytest；优先使用 MANJU_DB_PATH 指向 D 盘 .tmp，绝不写 C 盘。）
"""
import os
import sys
import json
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

# ---- 隔离测试库（D 盘 .tmp，避免污染生产 DB / 触碰 C 盘）----
_TMP = os.path.join(ROOT, ".tmp", "lora_test")
os.makedirs(_TMP, exist_ok=True)
_TEST_DB = os.path.join(_TMP, "lora_test_%d.db" % (abs(hash(os.getpid())) % 100000))
os.environ["MANJU_DB_PATH"] = _TEST_DB

import tomllib  # py3.11+

import db
from lora import engine, captioner, preset
from intent_engine.resource_catalog import find_best_lora_static, _infer_arch
from intent_engine.translator import _inject_lora_node, load_template


# =====================================================================
def test_migration_creates_lora_tables():
    db.init_db()
    conn = db.get_conn()
    try:
        cols_t = {r[1] for r in conn.execute("PRAGMA table_info(lora_train_tasks)")}
        assert {"id", "status", "loss_log", "current_step", "total_steps", "trigger_word",
                "lora_type", "dataset_dir", "caption_status"}.issubset(cols_t), cols_t
        cols_m = {r[1] for r in conn.execute("PRAGMA table_info(lora_models)")}
        assert {"id", "name", "lora_type", "file_path", "trigger_word",
                "semantic_tags", "status", "base_model", "associated_asset_id"}.issubset(cols_m), cols_m
    finally:
        conn.close()


def test_detect_engine_missing_honest():
    # 指向不存在的目录，隔离真实安装状态，稳定验证「诚实降级」分支。
    orig = preset.DEFAULT_KOHYA_SS_PATH
    preset.DEFAULT_KOHYA_SS_PATH = "D:/AI/.tmp/__nonexistent_kohya__"
    try:
        info = engine.detect_engine()
        assert info["available"] is False
        assert info["reason"], "引擎缺失时必须给出安装指引 reason"
    finally:
        preset.DEFAULT_KOHYA_SS_PATH = orig


def test_build_train_config_toml():
    fake = {
        "available": True, "path": "D:/fake", "train_script": "D:/fake/train_network.py",
        "caption_script": "D:/fake/tag_images_by_wd14_tagger.py", "reason": "",
    }
    ds = os.path.join(_TMP, "ds_toml")
    os.makedirs(os.path.join(ds, "img"), exist_ok=True)
    task = {
        "id": "t1", "name": "林浩", "base_model": "v1-5-pruned.safetensors",
        "dataset_dir": ds, "trigger_word": "linhao",
        "params": json.dumps({"preset": "6gb_quality", "seed": 7}),
    }
    orig = engine.detect_engine
    engine.detect_engine = lambda: fake
    try:
        path = engine.build_train_config(task, "6gb_quality")
    finally:
        engine.detect_engine = orig
    assert os.path.isfile(path), "应生成 config.toml"
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    ta = cfg["training_arguments"]
    assert ta["network_module"] == "networks.lora"
    assert ta["network_dim"] == preset.NETWORK_DIM
    assert ta["network_alpha"] == preset.NETWORK_ALPHA
    assert ta["mixed_precision"] == "fp16"
    # xformers 现为 false（经典 sd-scripts requirements 未含 xformers，1660Ti 用 PyTorch SDPA）。
    assert ta["xformers"] is False
    assert ta["gradient_checkpointing"] is True
    assert ta["max_train_steps"] == 3000
    assert ta["resolution"] == "512,768"
    assert ta["caption_extension"] == ".txt"


def test_caption_prefix_character_adds_trigger():
    out = captioner.prefix_caption("character", "linhao", "1girl, smile (0.98)")
    assert out.startswith("linhao, "), out
    assert "1girl, smile" in out
    assert "(0.98)" not in out, "应清洗置信度括号"
    out2 = captioner.prefix_caption("style", "", "oil painting, texture")
    assert out2 == "oil painting, texture", "风格 LoRA 不加触发词前缀"


def _seed_lora(conn, row):
    conn.execute("DELETE FROM lora_models")
    conn.execute(
        "INSERT INTO lora_models (id,name,lora_type,file_path,trigger_word,"
        "semantic_tags,associated_asset_id,base_model,status) VALUES (?,?,?,?,?,?,?,?,?)",
        (row["id"], row["name"], row["lora_type"], row["file_path"], row["trigger_word"],
         row["semantic_tags"], row["associated_asset_id"], row["base_model"], row["status"]))
    conn.commit()


def test_find_best_lora_match_and_compat():
    db.init_db()
    conn = db.get_conn()
    try:
        _seed_lora(conn, {
            "id": "m1", "name": "民国风LoRA", "lora_type": "style",
            "file_path": "/x/m.safetensors", "trigger_word": "minguo_film",
            "semantic_tags": json.dumps(["民国风", "写实"]), "associated_asset_id": None,
            "base_model": "v1-5-pruned.safetensors", "status": "active"})
        # 语义命中 + 同架构
        hit = find_best_lora_static(["民国风", "写实"], None, "v1-5-pruned.safetensors")
        assert hit and hit["name"] == "民国风LoRA"
        assert hit["trigger_word"] == "minguo_film"
        # 不相关意图 → None（回落通用生成）
        assert find_best_lora_static(["赛博朋克"], None, "v1-5-pruned.safetensors") is None
        # 底模架构不兼容（sd1.5 LoRA vs sdxl 底模）→ 过滤掉 → None
        assert find_best_lora_static(["民国风"], None, "sdxl_base_1.0.safetensors") is None
        # 角色资产强绑定：associated_asset_id 命中权重 +5
        _seed_lora(conn, {
            "id": "m2", "name": "林浩LoRA", "lora_type": "character",
            "file_path": "/x/l.safetensors", "trigger_word": "linhao",
            "semantic_tags": json.dumps(["林浩", "角色"]), "associated_asset_id": "asset_9",
            "base_model": "v1-5-pruned.safetensors", "status": "active"})
        hit2 = find_best_lora_static(["林浩"], "asset_9", "v1-5-pruned.safetensors")
        assert hit2 and hit2["name"] == "林浩LoRA"
    finally:
        conn.close()

    # 架构推断规则自洽校验
    assert _infer_arch("v1-5-pruned.safetensors") == "sd1.5"
    assert _infer_arch("sdxl_base_1.0.safetensors") == "sdxl"


def test_inject_lora_node_synthetic():
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "v1-5-pruned.safetensors"}},
        "3": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "clip": ["4", 1], "seed": 42}},
    }
    out = _inject_lora_node(wf, "民国风LoRA")
    new_id = out["3"]["inputs"]["model"][0]
    assert out[new_id]["class_type"] == "LoraLoader"
    assert out[new_id]["inputs"]["lora_name"] == "民国风LoRA"
    assert out[new_id]["inputs"]["model"] == ["4", 0]
    assert out[new_id]["inputs"]["clip"] == ["4", 1]
    assert out["3"]["inputs"]["model"] == [new_id, 0]


def test_inject_lora_node_real_template():
    try:
        tpl = load_template("shot_image_gen")
    except Exception:
        print("  [skip] 真实模板 shot_image_gen.json 缺失")
        return
    prompt = tpl.get("prompt") if isinstance(tpl, dict) and "prompt" in tpl else tpl
    ks_id = next(k for k, n in prompt.items()
                 if isinstance(n, dict) and (n.get("class_type") or "").startswith("KSampler"))
    out = _inject_lora_node(prompt, "民国风LoRA")
    new_id = out[ks_id]["inputs"]["model"][0]
    assert out[new_id]["class_type"] == "LoraLoader"
    assert out[new_id]["inputs"]["lora_name"] == "民国风LoRA"


# =====================================================================
def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            print("  PASS  %s" % fn.__name__)
            passed += 1
        except Exception as e:
            failed += 1
            print("  FAIL  %s" % fn.__name__)
            traceback.print_exc()
    print("\n==== LoRA 模块测试：%d 通过 / %d 失败 ====" % (passed, failed))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
