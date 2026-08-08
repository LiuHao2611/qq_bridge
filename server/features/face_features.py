# -*- coding: utf-8 -*-
"""Feature Extraction Engine —— 真人资产人脸特征提取（手册 §5.1 / §5.2）。

真人资产必须包含至少 3 张不同角度参考图与预提取的 **512 维身份特征向量**。
本模块：
  - extract_face_vector(image_path) -> np.ndarray(shape=(512,), dtype=float32)
      经 insightface(arcface, buffalo_l) 提取身份向量；多张参考图取平均以稳健。
  - compare_face_vector(image_path, vector_bytes) -> float
      对生成图抽脸并与已存向量算余弦相似度（N06 品控，阈值≥0.8 由调用方判定）。
  - store_face_vector(stage_id, vector) / load_face_vector(stage_id)
      落库 asset_stages.face_vector(BLOB, float32 little-endian)。

能力探测：insightface / onnxruntime 缺失或 buffalo_l 模型未就绪时，
统一抛 FeatureUnavailable（code="FACE_MODEL_MISSING"），由上层清晰告知用户
「需向秋葉 ComfyUI 的 insightface 模型目录放入 buffalo_l」，而非静默不注入。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

FACE_VECTOR_DIM = 512


class FeatureUnavailable(Exception):
    """特征引擎依赖（模型/运行时）缺失时的统一告警，禁止静默吞。"""

    def __init__(self, capability, message, code="FEATURE_UNAVAILABLE"):
        self.capability = capability
        self.code = code
        self.message = message
        super().__init__(message)


def _require_numpy():
    """numpy 缺失时统一抛 FeatureUnavailable（而非 ImportError 崩溃导入链）。"""
    try:
        import numpy as np
        return np
    except Exception as e:
        raise FeatureUnavailable(
            "face",
            "未安装 numpy（特征提取还需要 insightface + onnxruntime）。请先安装依赖。",
            code="FACE_RUNTIME_MISSING") from e


def _load_insightface():
    """惰性加载 insightface FaceAnalysis；缺失或模型未就绪时抛 FeatureUnavailable。"""
    try:
        from insightface.app import FaceAnalysis
    except Exception as e:
        raise FeatureUnavailable(
            "face",
            "未安装 insightface（需 insightface + onnxruntime）。请先安装后再提取人脸特征。",
            code="FACE_RUNTIME_MISSING",
        ) from e
    # 允许通过环境变量指定模型根目录（默认 ~/.insightface）；秋葉整合包可指向其 models/insightface
    root = os.environ.get("INSIGHTFACE_MODELS")
    try:
        app = FaceAnalysis(name="buffalo_l", root=root) if root else FaceAnalysis(name="buffalo_l")
        app.prepare(ctx_id=0, det_size=(640, 640))
    except Exception as e:
        raise FeatureUnavailable(
            "face",
            "insightface 模型 buffalo_l 未就绪（需在 insightface 模型目录放入 buffalo_l 权重）。"
            "秋葉整合包通常位于 ComfyUI/models/insightface；放入后本能力即生效。",
            code="FACE_MODEL_MISSING",
        ) from e
    return app


def _embed_one(app, image_path):
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        raise FeatureUnavailable("face", "无法读取参考图：%s" % image_path, code="FACE_IMAGE_UNREADABLE")
    faces = app.get(img)
    if not faces:
        raise FeatureUnavailable(
            "face", "参考图中未检测到人脸：%s" % image_path, code="FACE_NOT_DETECTED")
    emb = faces[0].embedding
    return np.asarray(emb, dtype=np.float32).reshape(-1)


def extract_face_vector(image_paths):
    """从一张或多张参考图提取 512 维身份向量（多图取均值，提升跨角度稳健性）。

    image_paths: str 或 List[str]。返回 np.float32(512,)。
    无脸 / 模型缺失 → FeatureUnavailable。
    """
    if isinstance(image_paths, str):
        image_paths = [image_paths]
    if not image_paths:
        raise FeatureUnavailable("face", "未提供参考图", code="FACE_NO_IMAGE")
    np = _require_numpy()
    app = _load_insightface()
    vecs = []
    for p in image_paths:
        vecs.append(_embed_one(app, p))
    mean_vec = np.mean(np.stack(vecs), axis=0).astype(np.float32)
    # L2 归一化（便于余弦相似度直接点积）
    norm = np.linalg.norm(mean_vec)
    if norm > 0:
        mean_vec = mean_vec / norm
    return mean_vec


def compare_face_vector(image_path, vector_bytes):
    """对生成图抽脸，与已存 512 维向量(序列化 bytes)算余弦相似度 → float∈[-1,1]。

    无脸 → 返回 0.0 并由调用方据阈值判不合格（不抛错，便于品控统计）；
    模型缺失 → 抛 FeatureUnavailable。
    """
    if vector_bytes is None:
        raise FeatureUnavailable("face", "资产未提取 512 维特征向量", code="FACE_VECTOR_MISSING")
    np = _require_numpy()
    ref = np.frombuffer(vector_bytes, dtype=np.float32).reshape(-1)
    if ref.shape[0] != FACE_VECTOR_DIM:
        raise FeatureUnavailable("face", "特征向量维度异常：%d" % ref.shape[0], code="FACE_VECTOR_DIM")
    app = _load_insightface()
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        return 0.0
    faces = app.get(img)
    if not faces:
        return 0.0
    emb = np.asarray(faces[0].embedding, dtype=np.float32).reshape(-1)
    n = np.linalg.norm(emb)
    if n > 0:
        emb = emb / n
    sim = float(np.dot(emb, ref))
    return max(-1.0, min(1.0, sim))


# ---- 落库（asset_stages.face_vector，BLOB = float32 little-endian） -------------
def store_face_vector(stage_id, vector):
    """把 512 维向量序列化写入 asset_stages.face_vector。"""
    try:
        import db
        conn = db.get_conn()
    except Exception:
        return False
    try:
        np = _require_numpy()
        blob = np.asarray(vector, dtype=np.float32).reshape(-1).tobytes()
        conn.execute(
            "UPDATE asset_stages SET face_vector=? WHERE stage_id=?",
            (blob, stage_id))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_face_vector(stage_id):
    """读取 asset_stages.face_vector；无则返回 None。"""
    try:
        import db
        conn = db.get_conn()
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT face_vector FROM asset_stages WHERE stage_id=?", (stage_id,)).fetchone()
        if row and row["face_vector"]:
            return np.frombuffer(row["face_vector"], dtype=np.float32).reshape(-1)
        return None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
