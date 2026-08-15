# -*- coding: utf-8 -*-
"""P4 M1 新模型零代码接入 —— 单元测试（手册 §2 M1 验收函数）。

断言聚焦：
- discover_node_family 复用 P0 M2 三层推导（detect_capability_dimensions / _frame_anchor_grade），
  在线走 three_layer_derivation、离线走 registry_fallback，但首尾帧分级始终正确；
- match_template 查 P0 M3 TemplateEngine.discover_templates()：同族命中、全新族诚实引导 need_template；
- register_model 写 model_declarations 热加载 + 幂等更新；
- **D2 闭环**：声明的 semantic_tags / 节点族必须回流到实时 catalog（find_best_checkpoint 按声明标签命中、
  提前声明视频族补录 video_models/node_families），注册即生效、无需重启；
- detect_new_models 离线/ComfyUI 不可达安全返回 []。
"""
import os
import sys
import tempfile

# 必须在导入 db / model_plug 之前固定临时库（db.py 在 import 时解析 DB_PATH）。
_TMP = os.path.join(tempfile.mkdtemp(prefix="p4m1_"), "test.db")
os.environ["MANJU_DB_PATH"] = _TMP

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from db import init_db  # noqa: E402

init_db()

from features.model_plug import (  # noqa: E402
    discover_node_family,
    match_template,
    register_model,
    list_model_declarations,
    detect_new_models,
)
from intent_engine.resource_catalog import ResourceCatalog  # noqa: E402


def _video_object_info():
    """最小可驱动 P0 M2 三层推导的 object_info（含四视频节点族 + 图像节点）。"""
    def _node(*inputs):
        return {"input": {"required": {i: ["dummy"] for i in inputs}, "optional": {}}}
    return {
        "WanVideoSampler": _node("first_frame", "last_frame", "model_name"),
        "CogVideoSampler": _node("first_frame", "model_name"),
        "LTXVImgToVideo": _node("first_frame", "model_name"),
        "HunyuanVideoSampler": _node("first_frame", "model_name"),
        "KSampler": _node("seed", "steps"),
        "CLIPTextEncode": _node("text"),
        "IPAdapter": _node("ipadapter_file", "face_embeds"),
    }


def test_discover_node_family_online_three_layer():
    """在线：真正跑 detect_capability_dimensions，WanVideoSampler 首尾帧 = 插值。"""
    oi = _video_object_info()
    r = discover_node_family("wan-3.0", oi)
    assert r["type"] == "video"
    assert r["node_family"] == "wan"
    assert r["node_class"] == "WanVideoSampler"
    # 首尾帧分级由层2 签名推导（first_frame+last_frame → 插值）
    assert r["frame_anchor_grade"]["mode"] == "first_last_interpolate"
    assert r["frame_anchor_grade"]["interpolation"] is True
    assert r["frame_anchor_grade"]["extrapolation"] is True
    assert r["source"] == "three_layer_derivation"
    # 在线三层推导给出了完整 detected 维度（证明自动推导非手写死）
    assert isinstance(r["detected_dimensions"], dict)
    # 能力维度由 _annotate_entry 生成（视频模型含 temporal）
    assert "temporal" in r["capabilities"]


def test_discover_node_family_offline_registry_fallback():
    """离线（无 object_info）：仍用 _frame_anchor_grade 层2 推导，分级正确。"""
    r = discover_node_family("Wan2.2-T2V.safetensors")
    assert r["node_family"] == "wan"
    assert r["node_class"] == "WanVideoSampler"
    assert r["frame_anchor_grade"]["mode"] == "first_last_interpolate"
    assert r["source"] == "registry_fallback"


def test_discover_checkpoint_capabilities():
    """非视频模型：能力维度由 _annotate_entry 生成（checkpoint 含一致性）。"""
    r = discover_node_family("majicMIX.safetensors")
    assert r["type"] == "checkpoint"
    assert r["node_family"] is None
    assert r["frame_anchor_grade"] is None
    assert "consistency" in r["capabilities"]


def test_match_template_known_family_hit():
    """同族命中：wan → shot_video_gen_wan（P0 M3 模板映射）。"""
    r = match_template("wan", "video")
    assert r["status"] == "matched"
    assert r["template_id"] == "shot_video_gen_wan"
    assert r["task_type"] == "shot_video_gen_wan"
    assert r["path"].endswith("shot_video_gen_wan.json")


def test_match_template_new_family_need_template():
    """全新族（无对应模板）：诚实返回 need_template 引导（禁假装已接入）。"""
    r = match_template("sora", "video")
    assert r["status"] == "need_template"
    assert "guide" in r and r["guide"]
    assert r["suggested_template_id"] == "shot_video_gen_sora"


def test_register_and_list_hotload():
    """register_model 写 model_declarations 并热加载可见；重复注册幂等更新。"""
    rec = register_model({
        "name": "wan-test-3.0.safetensors",
        "semantic_tags": ["写实", "电影感"],
        "user_notes": "本地新放的视频底模",
    })
    assert rec["model_id"]
    assert rec["node_family"] == "wan"
    assert rec["status"] == "active"
    assert "写实" in rec["semantic_tags"]

    # 热加载：list 立即可见，无需重启
    models = list_model_declarations()
    names = [m["name"] for m in models]
    assert "wan-test-3.0.safetensors" in names

    # 幂等更新：再次注册同 name 应更新而非新增
    rec2 = register_model({"name": "wan-test-3.0.safetensors", "user_notes": "改了备注"})
    assert rec2["model_id"] == rec["model_id"]
    assert rec2["user_notes"] == "改了备注"
    assert len([m for m in list_model_declarations() if m["name"] == "wan-test-3.0.safetensors"]) == 1


def test_detect_new_models_offline_safe():
    """离线/ComfyUI 不可达：安全返回 []，不抛错、不静默假装成功。"""
    # 不设置 comfy root → _comfy_models_root 返回 None
    result = detect_new_models(comfy_root="/nonexistent_path_xyz")
    assert result == []


def _offline_catalog():
    """构造一个最小离线 catalog（含一个待声明的视频底模 + 一个对照底模）。"""
    return {
        "checkpoints": [
            {"name": "wan-test-3.0.safetensors", "type": "wan",
             "auto_tags": ["视频"], "user_tags": [], "vram_required_mb": 0},
            {"name": "other-ckpt.safetensors", "type": "sd1.5",
             "auto_tags": ["写实"], "user_tags": [], "vram_required_mb": 0},
        ],
        "loras": [], "ip_adapters": [], "controlnets": [], "vaes": [],
        "video_models": [], "node_families": {}, "object_info": {},
    }


def test_hotload_declared_tags_flow_into_selection():
    """D2 闭环：注册模型的 semantic_tags 必须回流到实时选型（find_best_checkpoint 按声明标签命中）。

    这是 P4 M1「零代码接入」是否真正闭环的关键判定——此前声明只落 DB，
    导演仍按旧目录选，用户补的标签不生效。
    """
    register_model({
        "name": "wan-test-3.0.safetensors",
        "semantic_tags": ["写实", "电影感"],
        "user_notes": "本地新放的视频底模",
    })
    cat = ResourceCatalog(catalog=_offline_catalog())  # 离线注入，不触网
    # 合并 model_declarations → 实时 catalog
    cat._merge_model_declarations()

    # 1) 声明标签已并入该 checkpoint 的 user_tags
    entry = next(c for c in cat._catalog["checkpoints"]
                 if c["name"] == "wan-test-3.0.safetensors")
    assert "写实" in entry["user_tags"]
    assert "电影感" in entry["user_tags"]
    assert entry.get("declared") is True

    # 2) 导演按声明标签选型时，该模型被优先选中（打分 写实2+电影感2=4 > 对照 写实1）
    best = cat.find_best_checkpoint(["写实", "电影感"])
    assert best["name"] == "wan-test-3.0.safetensors"


def test_hotload_advance_family_declaration_enriches_matcher():
    """D2 闭环（提前声明）：文件尚未落盘时，声明视频节点族也补录进 video_models/node_families，
    使匹配器在注册后即可知晓该族（导演可选用对应模板），无需等文件、无需重启。"""
    register_model({
        "name": "future-wan-4.0.safetensors",
        "type": "video",
        "node_family": "wan",
        "semantic_tags": ["写实"],
    })
    cat = ResourceCatalog(catalog=_offline_catalog())
    cat._merge_model_declarations()

    # 未在磁盘/checkpoints 命中 → 补录进 video_models + node_families
    fams = cat._catalog["node_families"]
    assert "wan" in fams
    assert fams["wan"] == "WanVideoSampler"
    assert any(v.get("name") == "WanVideoSampler" and v.get("declared")
               for v in cat._catalog["video_models"])


if __name__ == "__main__":
    test_discover_node_family_online_three_layer()
    test_discover_node_family_offline_registry_fallback()
    test_discover_checkpoint_capabilities()
    test_match_template_known_family_hit()
    test_match_template_new_family_need_template()
    test_register_and_list_hotload()
    test_detect_new_models_offline_safe()
    test_hotload_declared_tags_flow_into_selection()
    test_hotload_advance_family_declaration_enriches_matcher()
    print("P4 M1 全部断言通过 ✅")
