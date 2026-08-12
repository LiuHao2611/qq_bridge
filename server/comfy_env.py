# -*- coding: utf-8 -*-
"""ComfyUI 安装根目录定位 + 权重自动发现（手册 §5.1 / §5.2 权重引用）。

用户已授权修改节点参数与权重配置（2026-08-09）。本模块把「权重文件名」与
「权重落盘位置」解耦：代码不再硬编码猜测的权重名，而是

  1) 优先使用显式指定（settings / 环境变量，即「用户修改权重」的入口）；
  2) 否则扫描秋葉 ComfyUI 的 models/<subdir> 自动挑首个匹配文件；
  3) 都没有 → 抛清晰 RuntimeError，指明去哪个目录放什么权重。

所有返回均为 ComfyUI 节点所需的「文件名」（ckpt_name / ipadapter_file /
clip_name / control_net_name ...），而非绝对路径。
"""
import os
import glob

_DEFAULT_COMFY_ROOT = r"D:\AI\ComfyUI-aki-v3.2\ComfyUI"

# 占位文件（aki 整合包自带，非真实权重），扫描时跳过
_PLACEHOLDER_NAMES = {
    "put_checkpoints_here",
    "put_clip_vision_models_here",
    "put_controlnets_and_t2i_here",
    "_output_images_will_be_put_here",
    "请在此处放置放大模型",
}


def get_comfy_root():
    """定位秋葉 ComfyUI 安装根（含 models/ 与 output/）。

    覆盖优先级：环境变量 MANJU_COMFY_ROOT > settings(comfy.root) > 默认 aki-v3.2。
    """
    env = os.environ.get("MANJU_COMFY_ROOT")
    if env and os.path.isdir(env):
        return env
    try:
        import db
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", ("comfy.root",)).fetchone()
            if row and row["value"]:
                p = str(row["value"]).strip().strip('"').strip("'")
                if p and os.path.isdir(p):
                    return p
        finally:
            conn.close()
    except Exception:
        pass
    return _DEFAULT_COMFY_ROOT


def get_comfy_output_dir():
    """ComfyUI 写出图像的目录（SaveImage 默认落到 <root>/output）。"""
    return os.path.join(get_comfy_root(), "output")


def discover_weight(subdir, patterns, explicit=None, what="权重文件", prefer=None):
    """返回 ComfyUI 节点应填的权重文件名；缺失则抛清晰错误。

    subdir: 相对 models/ 的子目录，如 'checkpoints' / 'ipadapter' / 'clip_vision' / 'controlnet'
    patterns: 通配符列表，如 ['*.safetensors', '*.ckpt']
    explicit: 用户/设置显式指定的文件名（无需扫描，直接信任）
    what: 报错时对人友好的权重名称
    prefer: 可选 glob 通配符列表（如 ['*anime*','*manga*']）。命中 prefer 的文件
            优先排序在前，用于「自动挑选更合适的底模」（如漫画优先动漫底模）。
            未命中 prefer 时回退到稳定排序的首个文件。
    """
    if explicit and isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    root = get_comfy_root()
    scan_dir = os.path.join(root, "models", subdir)
    found = []
    if os.path.isdir(scan_dir):
        for pat in patterns:
            found.extend(glob.glob(os.path.join(scan_dir, pat)))
    found = [
        f for f in found
        if os.path.isfile(f)
        and os.path.basename(f).lower() not in _PLACEHOLDER_NAMES
    ]
    if found:
        # 稳定排序后取首个，避免随机抖动；若指定 prefer 则把命中项排到最前
        ordered = sorted(found)
        if prefer:
            preferred = []
            rest = []
            for f in ordered:
                name = os.path.basename(f).lower()
                if any(glob.fnmatch.fnmatch(name, p.lower()) for p in prefer):
                    preferred.append(f)
                else:
                    rest.append(f)
            if preferred:
                ordered = preferred + rest
        return os.path.basename(ordered[0])
    raise RuntimeError(
        "缺少%s：请在秋葉 ComfyUI 的 models/%s 目录放入对应权重文件"
        "（如 %s）。扫描目录: %s"
        % (what, subdir, " / ".join(patterns), scan_dir))
