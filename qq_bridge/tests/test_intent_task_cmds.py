# -*- coding: utf-8 -*-
"""
test_intent_task_cmds.py —— 任务中心命令层验收（2026-09-08）

覆盖：
  1. `parse_draw_args` 生图全参解析（与 /txt2img 页面参数对齐）
     - 中文/英文 key 都能解析；
     - **未知 `--xxx` 留在提示词里**（提示词可能含任意字符，不能误吞）；
     - 数量/比例越界 → 明确报错（不静默忽略）；
     - `--key` 后缺值 → 报用法错误。
  2. 路由：本地通道走队列（queue_t2i），**在线通道行为不变**（零回归）。
  3. 女仆暗号顺序：**「任务进度」不被「任务」prefix 吞掉**（exact 必须排在 prefix 前）。
  4. 任务中心命令 → 占位符 + `@@` 参数 的正确产出。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_intent_task_cmds.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge import config as bridge_config  # noqa: E402
from src.plugins.qq_bridge import intent  # noqa: E402

CFG = bridge_config.Config


def _ev(text: str, images=None, force_t2i: bool = False):
    return SimpleNamespace(raw_text=text, images=images or [], force_t2i=force_t2i)


def _sess(mode: str = "image", pending_image=None):
    return SimpleNamespace(mode=mode, pending_image=pending_image)


def _route(text: str, channel: str = "local", mode: str = "image"):
    CFG.IMAGE_CHANNEL = channel
    return intent.route(_ev(text), _sess(mode=mode))


# ===========================================================================
# 1) parse_draw_args
# ===========================================================================
def test_parse_chinese_args():
    p, params, err = intent.parse_draw_args("一只在雨中撑伞的猫 --尺寸 1024 --比例 16:9 --数量 4")
    assert err == "", err
    assert p == "一只在雨中撑伞的猫", "提示词应去掉参数部分，实际 %r" % p
    assert params == {"resolution": "1024", "ratio": "16:9", "count": 4}


def test_parse_english_alias():
    p, params, err = intent.parse_draw_args("a cat --size 1024 --count 2 --steps 8")
    assert err == "", err
    assert params == {"resolution": "1024", "count": 2, "steps": 8}


def test_parse_full_params_align_with_txt2img():
    """全部 8 个参数都要能解析（与 /txt2img 页面字段对齐）。"""
    p, params, err = intent.parse_draw_args(
        "赛博朋克城市 --尺寸 1024 --比例 16:9 --步数 20 --数量 4 "
        "--种子 -1 --模板 tpl_abc --负向 模糊 --风格 电影感")
    assert err == "", err
    assert params == {
        "resolution": "1024", "ratio": "16:9", "steps": 20, "count": 4,
        "seed": -1, "template_id": "tpl_abc", "negative": "模糊", "style": "电影感",
    }, params


def test_unknown_flag_stays_in_prompt():
    """未知 `--xxx` 必须留在提示词里——提示词可能含任意字符，不能误吞。"""
    p, params, err = intent.parse_draw_args("机甲少女 --装甲 重装 --尺寸 768")
    assert err == "", err
    assert params == {"resolution": "768"}, "只应解析已知 key"
    assert "--装甲 重装" in p, "未知参数应留在提示词里，实际 %r" % p


def test_count_out_of_range():
    _, _, err = intent.parse_draw_args("一只猫 --数量 99")
    assert err, "数量越界必须报错"
    assert "1~8" in err, "报错文案应提示合法范围：%s" % err


def test_steps_out_of_range():
    _, _, err = intent.parse_draw_args("一只猫 --步数 999")
    assert err and "1~150" in err


def test_invalid_ratio():
    _, _, err = intent.parse_draw_args("一只猫 --比例 2:7")
    assert err, "非法比例必须报错"
    assert "16:9" in err, "报错应给出合法值：%s" % err


def test_non_numeric_count():
    _, _, err = intent.parse_draw_args("一只猫 --数量 四张")
    assert err and "数字" in err


def test_missing_value_reports_usage():
    """`--尺寸` 后面没值 → 报用法错误（不能静默忽略，否则用户以为改了尺寸）。"""
    _, _, err = intent.parse_draw_args("一只猫 --尺寸")
    assert err, "缺值必须报错"
    assert "尺寸" in err


def test_empty_prompt():
    _, _, err = intent.parse_draw_args("")
    assert err, "空提示词必须报错"


def test_only_params_no_prompt():
    _, _, err = intent.parse_draw_args("--尺寸 1024")
    assert err, "只有参数没有提示词必须报错"


# ===========================================================================
# 2) 路由：本地走队列 / 在线保持不变
# ===========================================================================
def test_local_channel_routes_to_queue():
    """本地通道（ComfyUI 独占）→ 走持久队列。"""
    r = _route("生图 一只猫 --数量 2", channel="local")
    assert r.kind == "queue_t2i", "本地通道应走队列，实际 kind=%s" % r.kind
    assert r.payload["prompt"] == "一只猫"
    assert r.payload["params"] == {"count": 2}


def test_online_channel_unchanged():
    """在线通道**不走队列**（云端并发，排队无意义）——零回归保护。"""
    r = _route("一只猫 --数量 2", channel="online")
    assert r.kind == "t2i", "在线通道应保持原样，实际 kind=%s" % r.kind
    assert r.api == "/t2i/online/generate", "在线通道端点不能变"
    assert r.payload == {"prompt": "一只猫 --数量 2"}, "在线通道不做参数解析（保持原样）"


def test_draw_keyword_stripped_from_prompt():
    """触发词「生图」不能混进提示词（否则图里凭空多个「生图」语义）。

    KEYWORD_GATE 开启后用户必须说「生图 xxx」才能触发，而 route() 收到的是
    原话 → 必须在这里剥掉。
    """
    r = _route("生图 一只猫", channel="online")
    assert r.payload["prompt"] == "一只猫", "触发词应被剥离，实际 %r" % r.payload["prompt"]
    r2 = _route("生图 一只猫 --数量 2", channel="local")
    assert r2.payload["prompt"] == "一只猫", "本地通道同样剥离，实际 %r" % r2.payload["prompt"]
    # 用户真想画「生图」主题（剥离后为空）→ 保留原文，不吞字
    assert intent._strip_draw_keyword("生图") == "生图"


def test_local_bad_args_reply_usage_not_queue():
    """参数写错 → 回用法错误，**不占队列名额**。"""
    r = _route("生图 一只猫 --数量 99", channel="local")
    assert r.kind == "reply", "参数错误应直接回复，实际 %s" % r.kind
    assert "1~8" in r.text


def test_video_mode_still_video():
    """会话切到视频模式时，纯文本仍走视频（不受队列改造影响）。"""
    r = _route("一只猫在跑", channel="local", mode="video")
    assert r.kind == "video", "视频模式应保持，实际 %s" % r.kind


def test_force_t2i_local_goes_queue():
    """显式生图词（force_t2i）在本地通道也走队列。"""
    r = intent.route(_ev("生图 一只猫", force_t2i=True), _sess(mode="video"))
    CFG.IMAGE_CHANNEL = "local"
    r2 = intent.route(_ev("生图 一只猫", force_t2i=True), _sess(mode="video"))
    assert r2.kind == "queue_t2i", "force_t2i 本地应走队列，实际 %s" % r2.kind


# ===========================================================================
# 3) 女仆暗号顺序（exact 必须先于 prefix 匹配）
# ===========================================================================
def test_maid_task_progress_not_swallowed():
    """「任务进度」必须命中 /status，**不能被「任务」prefix 吞掉**。

    这是 MAID_ALIASES 顺序的回归保护：一旦有人把 prefix 型「任务」排到
    exact 型「任务进度」前面，用户的「任务进度」就会变成「/task 进度」而查不到东西。
    """
    assert intent._translate_maid("任务进度") == "/status"
    assert intent._translate_maid("我的任务") == "/task"


def test_maid_task_with_arg():
    assert intent._translate_maid("任务 abc123") == "/task abc123"
    assert intent._translate_maid("队列") == "/queue"
    assert intent._translate_maid("取消 abc123") == "/cancel abc123"
    assert intent._translate_maid("再看 abc123") == "/again abc123"


def test_maid_monitor():
    assert intent._translate_maid("监控打开") == "/monitor on"
    assert intent._translate_maid("监控关闭") == "/monitor off"


# ===========================================================================
# 4) 任务中心命令 → 占位符 + @@ 参数
# ===========================================================================
def _placeholder(r) -> str:
    return r.text


def test_cmd_task():
    assert _placeholder(_route("/task")) == "__TASK__@@"
    assert _placeholder(_route("/task abc123")) == "__TASK__@@abc123"


def test_cmd_recent_queue_clear():
    assert _placeholder(_route("/recent")) == "__RECENT__"
    assert _placeholder(_route("/queue")) == "__QUEUE__"
    assert _placeholder(_route("/clear")) == "__CLEAR__"


def test_cmd_cancel_requires_target():
    r = _route("/cancel")
    assert r.kind == "reply" and "用法" in r.text, "缺任务号应回用法"
    assert _placeholder(_route("/cancel abc123")) == "__CANCEL__@@abc123"


def test_cmd_promote_requires_target():
    r = _route("/promote")
    assert "用法" in r.text
    assert _placeholder(_route("/promote 5")) == "__PROMOTE__@@5"


def test_cmd_again_requires_target():
    r = _route("/again")
    assert "用法" in r.text
    assert _placeholder(_route("/again abc123")) == "__AGAIN__@@abc123"


def test_cmd_monitor_on_off():
    assert _placeholder(_route("/monitor on")) == "__MONITOR_ON__"
    assert _placeholder(_route("/monitor 开")) == "__MONITOR_ON__"
    assert _placeholder(_route("/monitor off")) == "__MONITOR_OFF__"
    assert _placeholder(_route("/monitor 关")) == "__MONITOR_OFF__"
    r = _route("/monitor 随便")
    assert "用法" in r.text


def test_cmd_via_maid_alias_end_to_end():
    """自然语言暗号 → 翻译 → 路由，端到端串一遍。"""
    for spoken, expect in (
        ("我的任务", "__TASK__@@"),
        ("看看队列", "__QUEUE__"),
        ("最近任务", "__RECENT__"),
    ):
        r = _route(spoken)
        assert _placeholder(r) == expect, "「%s」应路由到 %s，实际 %s" % (spoken, expect, r.text)


def test_help_mentions_task_cmds():
    """帮助文案必须包含新增的任务中心命令（否则用户不知道有这功能）。"""
    for kw in ("队列", "取消", "任务"):
        assert kw in intent.HELP_TEXT, "帮助文案缺少「%s」相关说明" % kw
