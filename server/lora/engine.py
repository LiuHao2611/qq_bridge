# -*- coding: utf-8 -*-
"""LoRA 训练引擎封装（手册 §3 / 设计思路 §6）。

主引擎：kohya_ss（sd-scripts），经 subprocess 调 train_network.py + tag_images_by_wd14_tagger.py。
路径：环境变量 KOHYA_SS_PATH（默认 D:/AI/kohya_ss）。

【禁止】静默失败：引擎缺失 / 脚本缺失必须显式抛 FeatureUnavailable（手册 §3.1 / §11.4）。
【禁止】硬编码训练配置：全部由 preset.py 驱动（手册 §3.2）。
【必须】训练配置合法 TOML，含所有必需字段（手册 §3.2）。
【必须】逐行解析 stdout 的 loss / step，回写进度（手册 §3.3 + CLOSE-01 状态机）。
"""
import os
import re
import sys
import json
import time
import subprocess
import logging

try:
    from common import ApiError
except Exception:  # 独立运行/测试回退
    ApiError = Exception

from . import preset

logger = logging.getLogger("manju.lora.engine")

# 激活的训练子进程注册表（供取消时精准 kill）。
_ACTIVE = {}

# loss / step 解析正则（kohya_ss 输出形如 "step: 10/1500  loss: 0.1234 ..."）
_RE_LOSS = re.compile(r"loss[=:\s]+([0-9]+\.[0-9]+)", re.IGNORECASE)
_RE_STEP = re.compile(r"step[=:\s]+(\d+)\s*/\s*(\d+)", re.IGNORECASE)


class FeatureUnavailable(Exception):
    """训练引擎 / 脚本不可用（诚实降级，禁止静默失败）。"""

    def __init__(self, feature, reason=""):
        self.feature = feature
        self.reason = reason
        super().__init__("LoRA 训练功能不可用（%s）：%s" % (feature, reason))


def _kohya_root():
    return preset.DEFAULT_KOHYA_SS_PATH


def detect_engine():
    """探测 kohya_ss 训练引擎（手册 §3.1）。

    返回 {"available": bool, "path": str, "train_script": str|None, "caption_script": str|None, "reason": str}。
    引擎缺失时 available=False 且 reason 给出安装指引（绝不以异常替代，方便调用方优雅报错）。
    """
    root = _kohya_root()
    train_script = os.path.join(root, "train_network.py")
    # 经典 sd-scripts 把 WD14 打标脚本放在 finetune/ 子目录（其 import 依赖 sd-scripts 可编辑安装，见 README）。
    caption_script = os.path.join(root, "finetune", "tag_images_by_wd14_tagger.py")
    if not os.path.isdir(root):
        return {
            "available": False, "path": root,
            "train_script": None, "caption_script": None,
            "reason": "kohya_ss 未安装：请将 kohya_ss（sd-scripts）克隆到 %s，并设置环境变量 KOHYA_SS_PATH 指向该目录" % root,
        }
    if not os.path.isfile(train_script):
        return {
            "available": False, "path": root,
            "train_script": None, "caption_script": (caption_script if os.path.isfile(caption_script) else None),
            "reason": "未找到训练脚本 train_network.py（应在 %s）。请确认 kohya_ss 安装完整" % train_script,
        }
    return {
        "available": True, "path": root,
        "train_script": train_script,
        "caption_script": (caption_script if os.path.isfile(caption_script) else None),
        "reason": "",
    }


def _python_for_kohya():
    """kohya_ss 自带 venv 优先；否则回退当前解释器。"""
    root = _kohya_root()
    cand = os.path.join(root, "venv", "Scripts", "python.exe")  # Windows 布局
    if os.path.isfile(cand):
        return cand
    cand = os.path.join(root, "venv", "bin", "python")
    if os.path.isfile(cand):
        return cand
    return sys.executable


def _slug(s):
    s = (s or "lora").strip().replace(" ", "_")
    return re.sub(r"[^\w\-]", "", s) or "lora"


def _params_of(task):
    """安全解析任务的 params 字段：DB 中存为 JSON 字符串，需兼容 dict / str / None。"""
    p = task.get("params")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            p = {}
    if not isinstance(p, dict):
        p = {}
    return p


def build_train_config(task, preset_key=None):
    """生成 kohya_ss sd-scripts TOML 配置（手册 §3.2）。

    :param task: lora_train_tasks 行（dict），至少含 dataset_dir / base_model / name / trigger_word。
    :param preset_key: 预设 key；缺省用 DEFAULT_PRESET。
    :return: toml 文件路径（落 dataset_dir/output/config.toml）。
    :raises FeatureUnavailable: 引擎缺失时（调用方应已 detect_engine 前置，这里二次保险）。
    """
    eng = detect_engine()
    if not eng["available"]:
        raise FeatureUnavailable("kohya_ss", eng["reason"])

    pp = _params_of(task)
    p = preset.get_preset(preset_key or pp.get("preset"))
    dataset_dir = task["dataset_dir"]
    img_dir = os.path.join(dataset_dir, "img")
    cap_dir = os.path.join(dataset_dir, "captions")
    out_dir = os.path.join(dataset_dir, "output")
    os.makedirs(out_dir, exist_ok=True)

    resolution = p["resolution"]
    batch_size = int(p["batch_size"])
    max_train_steps = int(p["max_train_steps"])
    learning_rate = float(p["learning_rate"])
    optimizer = p["optimizer"]

    output_name = _slug(task.get("name") or task.get("id") or "lora")
    # 保底日志/保存间隔：便于进度解析与断点。
    save_every = max(100, max_train_steps // 20)
    log_every = max(10, max_train_steps // 100)

    lines = []
    lines.append("# 由 manju_agent LoRA 训练模块自动生成（preset=%s）" % (preset_key or "default"))
    lines.append("[training_arguments]")
    lines.append('pretrained_model_name_or_path = "%s"' % (task.get("base_model") or "v1-5-pruned.safetensors"))
    lines.append('train_data_dir = "%s"' % img_dir.replace("\\", "/"))
    lines.append('output_dir = "%s"' % out_dir.replace("\\", "/"))
    lines.append('output_name = "%s"' % output_name)
    lines.append('logging_dir = "%s"' % os.path.join(out_dir, "logs").replace("\\", "/"))
    lines.append('network_module = "networks.lora"')
    lines.append('network_dim = %d' % preset.NETWORK_DIM)
    lines.append('network_alpha = %d' % preset.NETWORK_ALPHA)
    if preset.NETWORK_DROPOUT:
        lines.append('network_dropout = %s' % preset.NETWORK_DROPOUT)
    lines.append('resolution = "%s"' % resolution)
    lines.append('batch_size = %d' % batch_size)
    lines.append('optimizer_type = "%s"' % optimizer)
    lines.append('learning_rate = %s' % learning_rate)
    lines.append('max_train_steps = %d' % max_train_steps)
    lines.append('lr_scheduler = "cosine"')
    lines.append('lr_warmup_steps = %d' % max(0, max_train_steps // 20))
    lines.append('caption_extension = ".txt"')
    lines.append('shuffle_caption = true')
    lines.append('keep_tokens = 1')
    lines.append('mixed_precision = "%s"' % p["mixed_precision"])
    lines.append('gradient_checkpointing = %s' % p["gradient_checkpointing"])
    lines.append('xformers = %s' % p["xformers"])
    lines.append('save_every_n_steps = %d' % save_every)
    lines.append('logging_dir_per_step = false')
    lines.append('log_with = "tensorboard"')
    lines.append('save_precision = "fp16"')
    lines.append('seed = %d' % int(pp.get("seed", 42) or 42))

    toml_path = os.path.join(out_dir, "config.toml")
    with open(toml_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return toml_path


def run_training(task, preset_key=None, on_progress=None):
    """启动 kohya_ss 训练子进程并实时解析进度（手册 §3.3 + CLOSE-01）。

    :param on_progress: 回调 (task_id, current_step, total_steps, progress, loss) ；返回 False 表示被取消。
    :return: {"ok": bool, "output_path": str|None, "error": str, "last_loss": float|None}
    :raises FeatureUnavailable: 引擎缺失。
    """
    eng = detect_engine()
    if not eng["available"]:
        raise FeatureUnavailable("kohya_ss", eng["reason"])

    toml_path = build_train_config(task, preset_key)
    pp = _params_of(task)
    p = preset.get_preset(preset_key or pp.get("preset"))
    total_steps = int(p["max_train_steps"])

    task_id = task.get("id")
    python = _python_for_kohya()
    cmd = [python, eng["train_script"], "--config_file", toml_path]

    logger.info("[LORA-TRAIN] 启动训练 task=%s cmd=%s", task_id, " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        cwd=_kohya_root(),
    )
    if task_id:
        _ACTIVE[task_id] = proc

    last_loss = None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            m_step = _RE_STEP.search(line)
            m_loss = _RE_LOSS.search(line)
            if m_loss:
                try:
                    last_loss = float(m_loss.group(1))
                except Exception:
                    pass
            cur = None
            if m_step:
                try:
                    cur = int(m_step.group(1))
                    tot = int(m_step.group(2)) or total_steps
                except Exception:
                    cur, tot = None, total_steps
            else:
                cur = None
                tot = total_steps
            if cur is None:
                # 没有 step 标记时，用 loss 行粗略推进进度（不阻塞）
                continue
            progress = min(1.0, cur / float(tot)) if tot else 0.0
            if on_progress:
                try:
                    stop = on_progress(task_id, cur, tot, progress, last_loss, line)
                except Exception as e:
                    logger.warning("[LORA-TRAIN] on_progress 异常：%s", e)
                    stop = False
                if stop is False:
                    logger.info("[LORA-TRAIN] 收到取消信号，终止子进程 task=%s", task_id)
                    _terminate(proc)
                    break
        proc.wait()
    finally:
        if task_id:
            _ACTIVE.pop(task_id, None)

    if proc.returncode != 0:
        return {
            "ok": False,
            "output_path": None,
            "error": "训练进程退出码 %s（详见后端日志）" % proc.returncode,
            "last_loss": last_loss,
        }

    # 定位产出 .safetensors
    out_dir = os.path.join(task["dataset_dir"], "output")
    cands = []
    if os.path.isdir(out_dir):
        for fn in os.listdir(out_dir):
            if fn.lower().endswith(".safetensors"):
                cands.append(os.path.join(out_dir, fn))
    if not cands:
        return {
            "ok": False,
            "output_path": None,
            "error": "训练结束但未在 output 目录发现 .safetensors 产出",
            "last_loss": last_loss,
        }
    # 取最新修改者
    cands.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return {"ok": True, "output_path": cands[0], "error": "", "last_loss": last_loss}


def _terminate(proc):
    try:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
    except Exception:
        pass


def cancel_training(task_id):
    """取消正在进行的训练：终止对应子进程。"""
    proc = _ACTIVE.get(task_id)
    if proc and proc.poll() is None:
        _terminate(proc)
        return True
    return False


def locate_output(task):
    """从数据集 output 目录定位最新 .safetensors（供 register 调用）。"""
    out_dir = os.path.join(task["dataset_dir"], "output")
    if not os.path.isdir(out_dir):
        return None
    cands = [os.path.join(out_dir, f) for f in os.listdir(out_dir)
             if f.lower().endswith(".safetensors")]
    if not cands:
        return None
    cands.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return cands[0]
