# -*- coding: utf-8 -*-
"""Layer 1 —— 资产管理层（手册 §2.2 / 设计逻辑「资产管理层」）。

包含：
  - Feature Extraction Engine：真人资产人脸特征提取（512 维身份向量，§5.1/§5.2）、
    场景风格特征提取；
  - Asset Validation Service：资产合法性 / 可用性 / 权限校验（§5.3）。

所有能力缺失均以 FeatureUnavailable 清晰上报（诚实标注，禁止静默降级）。
"""
from .face_features import (
    FeatureUnavailable,
    extract_face_vector,
    compare_face_vector,
    store_face_vector,
    load_face_vector,
    load_face_vector_by_stage,
    find_ipadapter_weight,
    FACE_VECTOR_DIM,
)
from .scene_features import extract_scene_features, SCENE_VECTOR_DIM
from .asset_validation import check_asset_permission, AssetPermissionError
# P1 M1 角色身份系统（身份卡存取 + 一致句锚点 + 组装编排）
from .identity_card import (
    get_identity_card,
    save_identity_card,
    build_consistency_prompt,
    default_consistency_sentence,
    assemble_identity_card,
    ollama_extract_attributes,
    build_consistency_sentence,
)
# P1 M2 Lock 锁定阶段（标准肖像 + 参考图集；REF_KINDS/SET_KINDS 以此处为准）
from .reference_set import (
    REF_KINDS,
    SET_KINDS,
    generate_standard_portrait,
    generate_reference_set,
    get_reference_set,
    regenerate_one,
    load_reference_image,
    get_style_anchor_for_asset,
    get_standard_portrait_url,
)

__all__ = [
    "FeatureUnavailable",
    "extract_face_vector",
    "compare_face_vector",
    "store_face_vector",
    "load_face_vector",
    "load_face_vector_by_stage",
    "find_ipadapter_weight",
    "FACE_VECTOR_DIM",
    "extract_scene_features",
    "SCENE_VECTOR_DIM",
    "check_asset_permission",
    "AssetPermissionError",
    # P1
    "get_identity_card",
    "save_identity_card",
    "build_consistency_prompt",
    "default_consistency_sentence",
    "assemble_identity_card",
    "ollama_extract_attributes",
    "build_consistency_sentence",
    "REF_KINDS",
    "SET_KINDS",
    "generate_standard_portrait",
    "generate_reference_set",
    "get_reference_set",
    "regenerate_one",
    "load_reference_image",
    "get_style_anchor_for_asset",
    "get_standard_portrait_url",
]
