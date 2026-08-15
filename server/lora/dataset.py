# -*- coding: utf-8 -*-
"""数据集管理（手册 §4.1 / 设计思路 §3 ①）。

- import_from_assets：从 P1 资产库复制角色多角度参考图到训练集（front/side/back/full_body/custom）。
- upload_dataset：接收上传图片，校验格式（png/jpg/webp）。
- validate_dataset：校验数量（风格≥10、角色≥15，不足提示，不静默放行）。
数据集目录结构：{dataset_dir}/img/*.png + {dataset_dir}/captions/*.txt。
"""
import os
import re
import shutil
import logging

from . import preset
from . import engine

logger = logging.getLogger("manju.lora.dataset")

_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_RE_FILE_URL = re.compile(r"/file\?name=([^&]+)")


def _resolve_outputs_roots():
    """解析本地图片可能的落盘根目录（与 main.py /file 端点一致）。"""
    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.dirname(server_dir)
    roots = [
        os.path.join(base, "outputs"),
        os.path.join(server_dir, "outputs"),
        os.path.join(server_dir, ".uploads"),
    ]
    return [r for r in roots if os.path.isdir(r)]


def _resolve_source(ref_url):
    """把资产库 reference_image_url / cover_url 解析为真实文件路径（可能多个候选）。"""
    if not ref_url:
        return None
    # 绝对路径直接可用
    if os.path.isabs(ref_url) and os.path.isfile(ref_url):
        return ref_url
    if os.path.isfile(ref_url):
        return ref_url
    # /file?name=xxx → 解析到 outputs 各根
    m = _RE_FILE_URL.search(ref_url)
    if m:
        name = m.group(1)
        for root in _resolve_outputs_roots():
            p = os.path.join(root, name)
            if os.path.isfile(p):
                return p
    return None


def import_from_assets(asset_id, target_dir, conn=None):
    """从 P1 资产库导入角色多角度参考图到 target_dir/img（手册 §4.1）。

    :param asset_id: 角色资产 id（assets.type='role'）。
    :param target_dir: 数据集根目录。
    :param conn: 可选 DB 连接（调用方管理）；缺省自开。
    :return: {"count": int, "images": [绝对路径], "asset_id": str}
    :raises FeatureUnavailable / ValueError: 资产不存在或无可用参考图。
    """
    own = conn is None
    if own:
        from db import get_conn
        conn = get_conn()
    try:
        row = conn.execute(
            "SELECT asset_id, type, name FROM assets WHERE asset_id=?", (asset_id,)
        ).fetchone()
        if not row:
            raise ValueError("资产不存在：%s" % asset_id)
        if row["type"] != "role":
            raise ValueError("仅角色资产支持导入多角度参考图（当前类型：%s）" % row["type"])

        # 收集多角度参考图：asset_stages.reference_image_url + asset_poses.reference_image_url
        refs = []
        for r in conn.execute(
            "SELECT reference_image_url FROM asset_stages WHERE asset_id=?", (asset_id,)
        ).fetchall():
            refs.append(r["reference_image_url"])
        for r in conn.execute(
            "SELECT reference_image_url FROM asset_poses WHERE asset_id=?", (asset_id,)
        ).fetchall():
            refs.append(r["reference_image_url"])
        # 也兜底资产封面
        ar = conn.execute(
            "SELECT cover_url, cover_path, asset_path FROM assets WHERE asset_id=?", (asset_id,)
        ).fetchone()
        if ar:
            for k in ("cover_url", "cover_path", "asset_path"):
                if ar[k]:
                    refs.append(ar[k])

        sources = []
        seen = set()
        for ref in refs:
            src = _resolve_source(ref)
            if src and src not in seen:
                seen.add(src)
                sources.append(src)
        if not sources:
            raise ValueError(
                "资产《%s》无可解析的参考图文件（请先在 P1 资产库上传多角度参考图）" % (row["name"] or asset_id))
    finally:
        if own:
            conn.close()

    img_dir = os.path.join(target_dir, "img")
    os.makedirs(img_dir, exist_ok=True)
    copied = []
    for i, src in enumerate(sources):
        ext = os.path.splitext(src)[1].lower() or ".png"
        dst = os.path.join(img_dir, "ref_%03d%s" % (i + 1, ext))
        try:
            shutil.copyfile(src, dst)
            copied.append(dst)
        except Exception as e:
            logger.warning("[LORA-DATASET] 复制失败 src=%s err=%s", src, e)
    if not copied:
        raise ValueError("参考图复制失败（源文件不可读）")
    return {"count": len(copied), "images": copied, "asset_id": asset_id}


def upload_dataset(files, target_dir):
    """接收上传图片写入 target_dir/img（手册 §4.1）。

    :param files: [(filename, bytes), ...]。
    :return: {"count": int, "images": [绝对路径], "rejected": [filename]}
    :raises ValueError: 无有效图片。
    """
    if not files:
        raise ValueError("未收到任何图片文件")
    img_dir = os.path.join(target_dir, "img")
    os.makedirs(img_dir, exist_ok=True)
    saved, rejected = [], []
    for filename, content in files:
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _ALLOWED_EXT:
            rejected.append(filename)
            continue
        safe = re.sub(r"[^\w\-.]", "_", os.path.basename(filename))
        dst = os.path.join(img_dir, safe)
        try:
            if isinstance(content, str):
                content = content.encode("utf-8")
            with open(dst, "wb") as f:
                f.write(content)
            saved.append(dst)
        except Exception as e:
            logger.warning("[LORA-DATASET] 写上传失败 %s err=%s", filename, e)
            rejected.append(filename)
    if not saved:
        raise ValueError("没有合法图片（支持 png/jpg/jpeg/webp）：%s" % (", ".join(rejected[:5]) or "无"))
    return {"count": len(saved), "images": saved, "rejected": rejected}


def new_dataset_dir(base=None):
    """创建新的数据集根目录（落 D:/AI 内，绝不写 C 盘）。返回绝对路径。"""
    if not base:
        server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base = os.path.join(os.path.dirname(server_dir), "data", "loras")
    os.makedirs(base, exist_ok=True)
    import time
    stamp = int(time.time() * 1000)
    d = os.path.join(base, "ds_%d" % stamp)
    os.makedirs(os.path.join(d, "img"), exist_ok=True)
    os.makedirs(os.path.join(d, "captions"), exist_ok=True)
    return d


def validate_dataset(dataset_dir, lora_type):
    """校验数据集图片数量（手册 §4.1 / 设计思路 §2）。不足则 ok=False 并给出提示。"""
    img_dir = os.path.join(dataset_dir, "img")
    count = 0
    if os.path.isdir(img_dir):
        for fn in os.listdir(img_dir):
            if os.path.splitext(fn)[1].lower() in _ALLOWED_EXT and os.path.isfile(os.path.join(img_dir, fn)):
                count += 1
    threshold = preset.MIN_IMAGES_CHARACTER if lora_type == "character" else preset.MIN_IMAGES_STYLE
    ok = count >= threshold
    msg = ("数据集校验通过" if ok else
           "图片数量不足：当前 %d 张，%s LoRA 至少需要 %d 张（请补充后重试）"
           % (count, "角色" if lora_type == "character" else "风格", threshold))
    return {"ok": ok, "count": count, "min": threshold, "message": msg}
