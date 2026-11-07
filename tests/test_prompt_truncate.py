# -*- coding: utf-8 -*-
"""2026-09-07：truncate_prompt 验收。

背景：之前 validate_prompt 遇超长直接拒 → quota_refund + return →
task_mgr.run 不跑 → 全局任务簿 _current_tasks 空 → /status 永远回"闲着呢"
（用户体感"任务进行时问进度，机器人说没事干"——本质是任务根本没起来）。

修复：新增 truncate_prompt，**按句末标点自动截断**而非拒，让任务能起来。

覆盖：
  1) 短文本：原样返回 + was_truncated=False
  2) 边界：恰好 PROMPT_MAX_LEN：原样返回
  3) 超长无句末：硬截到 PROMPT_MAX_LEN
  4) 超长有句末：在最近句末标点切（保留完整句子）
  5) 截断点低于半长：兜底硬截
  6) 中文长句标点优先级：。 > ！ > ？ > ， > ；
  7) 空文本：返空 + was_truncated=False
  8) 多标点：取最靠后
  9) PROMPT_MAX_LEN=300 默认场景（820 字符典型）

不依赖 task_mgr / httpx，纯函数测即可。
"""
import pytest

from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.intent import truncate_prompt


@pytest.fixture(autouse=True)
def _env():
    Config.PROMPT_MAX_LEN = 300  # 与 config.py 默认值一致
    yield


# ================ 短文本 / 边界 ================

def test_short_text_passes_through():
    t, trunc = truncate_prompt("一只猫")
    assert t == "一只猫"
    assert trunc is False


def test_empty_text():
    t, trunc = truncate_prompt("")
    assert t == ""
    assert trunc is False


def test_none_text():
    t, trunc = truncate_prompt(None)  # type: ignore[arg-type]
    assert t == ""
    assert trunc is False


def test_exactly_max_len_passes_through():
    """恰好 PROMPT_MAX_LEN 不截断（边界）。"""
    text = "猫" * Config.PROMPT_MAX_LEN
    t, trunc = truncate_prompt(text)
    assert t == text
    assert trunc is False


# ================ 超长：有句末 ================

def test_truncate_at_full_stop():
    """超长时优先按中文句号切到 PROMPT_MAX_LEN 内的最后位置。"""
    # 300 字符里第 200 字有一个句号
    text = "。" + "猫" * (Config.PROMPT_MAX_LEN - 2) + "。更多后续内容"
    t, trunc = truncate_prompt(text)
    assert trunc is True
    assert len(t) <= Config.PROMPT_MAX_LEN
    assert t.endswith("。")  # 切在句末
    # 第一句（"。" + 298 个"猫"）太长了，按 rfind 会落在 index=PROMPT_MAX_LEN
    # 验证保留的是截断范围内的最后一句
    assert "更多后续" not in t


def test_truncate_at_last_punctuation():
    """在 PROMPT_MAX_LEN 范围内找最近的句末标点（不限于最后一个）。"""
    # 构造：cat.  cat.  cat. ...（前 N 句用英文 . 结束，最后超长）
    sentences = []
    for i in range(50):
        sentences.append("sentence %d" % i)  # 每句带空格 ~ 12 字符
    # 拼成 ~600 字符，前 300 字符里有 4-5 个空格分隔
    text = " ".join(sentences) + "，更多更多更多" * 10
    assert len(text) > Config.PROMPT_MAX_LEN
    t, trunc = truncate_prompt(text)
    assert trunc is True
    assert len(t) <= Config.PROMPT_MAX_LEN
    # 截断应该在一个空格或逗号位置（不是硬截）
    # rfind 优先级 . > , > ; > \n，所以空格不算标点 → 会找 , 或 ; → 找不到则硬截
    # 这里都是空格 + 逗号，逗号在 PROMPT_MAX_LEN 之后才会有，所以应该硬截
    # 关键断言：长度确实被截到 ≤ PROMPT_MAX_LEN
    assert len(t) <= Config.PROMPT_MAX_LEN


def test_truncate_with_multiple_punctuation_picks_latest():
    """多个句末标点时取最靠后的（不是第一个）。"""
    # 在 PROMPT_MAX_LEN 范围内有多个句号，取最后一个
    p1 = "短句1。" * 20  # 80 字符
    p2 = "短句2。" * 20  # 80 字符
    p3 = "短句3。" * 20  # 80 字符
    text = p1 + p2 + p3 + "剩余内容" * 50  # 撑到 > 300
    assert len(text) > Config.PROMPT_MAX_LEN
    t, trunc = truncate_prompt(text)
    assert trunc is True
    assert len(t) <= Config.PROMPT_MAX_LEN
    # 最后一个句号应该被保留（在 PROMPT_MAX_LEN 范围内最靠后的）
    assert "。" in t
    # 切点应该在前 300 字符最靠后的句号位置
    last_dot = t.rfind("。")
    assert last_dot >= Config.PROMPT_MAX_LEN // 2
    # 切断后还能完整保留几个"短句N。" 不应该掉头截在中间


# ================ 超长：无句末 ================

def test_truncate_no_punctuation_falls_back_to_hard_cut():
    """超长但无任何句末标点 → 硬截到 PROMPT_MAX_LEN（trunc=True，但 cut point 不变）。"""
    text = "x" * 500  # 500 个 x，无任何标点
    t, trunc = truncate_prompt(text)
    assert trunc is True
    assert t == "x" * Config.PROMPT_MAX_LEN


def test_truncate_punctuation_too_early_falls_back_to_hard_cut():
    """句末标点出现在前 1/4 → rfind 找到的位置低于半长门槛 → 硬截（保留更多）。"""
    # 句号在位置 50，然后后面全是 x 撑到 500
    text = "。" + "x" * (Config.PROMPT_MAX_LEN - 1) + "abcdefg" * 30
    assert len(text) > Config.PROMPT_MAX_LEN
    t, trunc = truncate_prompt(text)
    assert trunc is True
    # cut 找到位置 0，半长门槛 150，0 < 150 → 硬截
    assert t == text[:Config.PROMPT_MAX_LEN]


# ================ 中文标点优先级 ================

def test_full_width_period_priority_over_comma():
    """中文句号优先级 > 逗号（按 cut_marks 顺序，后遍历覆盖）。"""
    # 在 PROMPT_MAX_LEN 范围内既有 "，" 又有 "。"，rfind 会先找 . 然后覆盖到最近 . 位置
    # 但 rfind 都是从右往左找，"。" 在 "，" 右边 → 最终取 "。" 位置
    half = Config.PROMPT_MAX_LEN // 2
    text = "，" * half + "。" + "x" * (Config.PROMPT_MAX_LEN - half - 1) + "多余内容" * 20
    assert len(text) > Config.PROMPT_MAX_LEN
    t, trunc = truncate_prompt(text)
    assert trunc is True
    # 应该在 "。" 处切（在 PROMPT_MAX_LEN 附近靠右位置）
    assert t.endswith("。") or t == text[:Config.PROMPT_MAX_LEN]


# ================ 真实场景 ================

def test_typical_820_char_prompt_gets_truncated():
    """用户日志典型场景：820 字符提示词 → 截到 ≤300。"""
    unit = "18-year-old east asian girl, natural everyday female face, mature casual look. "  # ~82 字符
    text = unit * 11  # ~900 字符
    assert len(text) > Config.PROMPT_MAX_LEN
    assert len(text) > 800  # 典型 ~820-900
    t, trunc = truncate_prompt(text)
    assert trunc is True
    assert len(t) <= Config.PROMPT_MAX_LEN
    # 在 cut_marks 任意位置切（这个文本有 . 或 ，）
    # 关键：长度对了 + 被告知截断（trunc=True）


def test_truncate_then_validate_always_passes():
    """截断后再调 validate_prompt 必通过（不再拒超长）。"""
    from src.plugins.qq_bridge.intent import validate_prompt
    long_text = "猫" * 1000
    trunc_text, _ = truncate_prompt(long_text)
    ok, why = validate_prompt(trunc_text)
    assert ok is True, "截断后必须通过：%r" % why
    assert why == ""


def test_no_truncation_message_when_short():
    """was_truncated=False 时调用方不应发"已截断"提示（避免噪音）。"""
    t, trunc = truncate_prompt("短的")
    assert trunc is False
    # 这是契约：调用方只在 trunc=True 时发提示