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
    FACE_VECTOR_DIM,
)
from .scene_features import extract_scene_features, SCENE_VECTOR_DIM
from .asset_validation import check_asset_permission, AssetPermissionError

__all__ = [
    "FeatureUnavailable",
    "extract_face_vector",
    "compare_face_vector",
    "store_face_vector",
    "load_face_vector",
    "FACE_VECTOR_DIM",
    "extract_scene_features",
    "SCENE_VECTOR_DIM",
    "check_asset_permission",
    "AssetPermissionError",
]
