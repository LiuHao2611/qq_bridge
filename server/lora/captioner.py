# -*- coding: utf-8 -*-
"""数据集自动打标（手册 §4.2 / 设计思路 §3 ②）。

引擎：kohya_ss 内置 WD14 tagger（tag_images_by_wd14_tagger.py）。
- 角色 LoRA：每张 caption 前缀统一为 {trigger_word}, （确保触发词被学习，手册 §12 红线）。
- 打标失败显式报错，不留半成品 caption（可整体重跑，幂等）。
【禁止】静默失败：引擎 / 打标脚本缺失必须显式抛 FeatureUnavailable。
"""
import os
import re
import subprocess
import logging

try:
    from common import ApiError
except Exception:
    ApiError = Exception

from . import engine

logger = logging.getLogger("manju.lora.captioner")

# WD14 原始输出形如 "1girl, smile, ... (0.98)"，需清洗括号与置信度。
_RE_BRACKET = re.compile(r"\s*\([^)]*\)")
_RE_COMMA_SPACE = re.compile(r",\s*,")
_RE_LEAD_COMMA = re.compile(r"^\s*,\s*")


def _clean_wd14(raw):
    """清洗 WD14 输出：去掉 (0.98) 置信度括号，规整逗号。"""
    s = _RE_BRACKET.sub("", raw)
    s = _RE_COMMA_SPACE.sub(",", s)
    s = _RE_LEAD_COMMA.sub("", s)
    return s.strip().strip(",").strip()


def _prefix_for(lora_type, trigger_word):
    """角色 LoRA 强制前缀 trigger_word；风格 LoRA 不加（避免污染风格语义）。"""
    if lora_type == "character" and trigger_word:
        return "%s, " % trigger_word.strip()
    return ""


def _write_captions_for_images(img_dir, cap_dir, lora_type, trigger_word, raw_tags_fn):
    """为 img 目录下每张图写一一对应的 caption（.txt）。返回写入数量。

    :param raw_tags_fn: 给定图片路径，返回该图原始标签字符串（可能为空）。
    """
    os.makedirs(cap_dir, exist_ok=True)
    count = 0
    prefix = _prefix_for(lora_type, trigger_word)
    for fn in sorted(os.listdir(img_dir)):
        base, _ = os.path.splitext(fn)
        img_path = os.path.join(img_dir, fn)
        if not os.path.isfile(img_path):
            continue
        raw = raw_tags_fn(img_path) or ""
        cleaned = _clean_wd14(raw)
        caption = (prefix + cleaned).strip().strip(",").strip()
        cap_path = os.path.join(cap_dir, base + ".txt")
        with open(cap_path, "w", encoding="utf-8") as f:
            f.write(caption + "\n")
        count += 1
    return count


def caption_dataset(dataset_dir, lora_type, trigger_word):
    """对数据集自动打标（手册 §4.2）。

    :param dataset_dir: 数据集根目录，含 img/ 子目录。
    :param lora_type: character | style。
    :param trigger_word: 触发词（角色 LoRA 必填）。
    :return: {"count": int, "caption_dir": str}
    :raises FeatureUnavailable: 引擎 / 打标脚本缺失（诚实降级，不静默失败）。
    """
    img_dir = os.path.join(dataset_dir, "img")
    cap_dir = os.path.join(dataset_dir, "captions")
    if not os.path.isdir(img_dir):
        raise FeatureUnavailable("dataset", "数据集 img 目录不存在：%s" % img_dir)

    eng = engine.detect_engine()
    if not eng["available"]:
        raise FeatureUnavailable("kohya_ss", eng["reason"])
    if not eng["caption_script"]:
        raise FeatureUnavailable(
            "wd14_tagger",
            "未找到打标脚本 tag_images_by_wd14_tagger.py（应在 %s）。请确认 kohya_ss 安装完整"
            % os.path.join(engine._kohya_root(), "finetune", "tag_images_by_wd14_tagger.py"))

    # 调用 kohya_ss WD14 打标（输出到 caption_dir）。
    # 注：WD14 第一次运行会下载模型权重，需联网；失败则此处显式抛出。
    cmd = [
        engine._python_for_kohya(),
        eng["caption_script"],
        img_dir,
        "--caption_extension", ".txt",
        "--caption_output_dir", cap_dir,
        "--max_data_loader_n_workers", "1",
        "--batch_size", "4",
        "--thresh", "0.35",
    ]
    logger.info("[LORA-CAPTION] 启动 WD14 打标 dataset=%s cmd=%s", dataset_dir, " ".join(cmd))
    proc = subprocess.run(cmd, cwd=engine._kohya_root(),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        out = (proc.stdout or "")[-800:]
        raise FeatureUnavailable("wd14_tagger", "WD14 打标失败（退出码 %s）：%s" % (proc.returncode, out))

    # WD14 已写出 caption；此处按角色 LoRA 规则补 trigger_word 前缀（幂等重写）。
    count = _write_captions_for_images(
        img_dir, cap_dir, lora_type, trigger_word,
        raw_tags_fn=lambda p: _read_caption(cap_dir, p))
    if count == 0:
        raise FeatureUnavailable("dataset", "数据集中没有任何图片可打标：%s" % img_dir)
    return {"count": count, "caption_dir": cap_dir}


def _read_caption(cap_dir, img_path):
    base, _ = os.path.splitext(os.path.basename(img_path))
    cp = os.path.join(cap_dir, base + ".txt")
    if os.path.isfile(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""


def prefix_caption(lora_type, trigger_word, raw):
    """纯函数：给定原始标签与类型，返回加好前缀的 caption（供单测，离线可用）。"""
    prefix = _prefix_for(lora_type, trigger_word)
    cleaned = _clean_wd14(raw or "")
    return (prefix + cleaned).strip().strip(",").strip()
