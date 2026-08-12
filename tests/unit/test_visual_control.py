# -*- coding: utf-8 -*-
"""VisualControlAgent 纯函数单元测试（手册 §3.2 / 设计逻辑「全局视觉统筹 Agent」）。

覆盖与模型/引擎无关的确定性逻辑：
  - 年龄分层调权（_age_weight，默认 0.85，范围 0.7–0.95）
  - 冷暖色调判定（_decide_warm_cool，强制 style_tone 优先 + 关键词兜底）
  - 风格提示词生成（_style_prompt，cool/warm/neutral 三态）
这些函数无需 insightface / ComfyUI，可直接单测。
"""
from director.agents.visual_control import VisualControlAgent


def test_age_weight_child():
    assert VisualControlAgent._age_weight(5) == 0.75


def test_age_weight_elder():
    assert VisualControlAgent._age_weight(70) == 0.90


def test_age_weight_adult_default():
    adult = VisualControlAgent._age_weight(30)
    bad = VisualControlAgent._age_weight("abc")  # 非整数回退默认
    assert 0.7 <= adult <= 0.95
    assert adult == bad  # 默认成年权重与解析失败回退一致


def test_decide_warm_cool_forced():
    assert VisualControlAgent._decide_warm_cool("任意文本", {"style_tone": "cool"}) == "cool"
    assert VisualControlAgent._decide_warm_cool("任意文本", {"style_tone": "warm"}) == "warm"
    assert VisualControlAgent._decide_warm_cool("任意文本", {"style_tone": "neutral"}) == "neutral"


def test_decide_warm_cool_keywords():
    assert VisualControlAgent._decide_warm_cool("深夜办公室开会", {}) == "cool"
    assert VisualControlAgent._decide_warm_cool("夕阳下的婚礼现场", {}) == "warm"
    assert VisualControlAgent._decide_warm_cool("一段抽象且无意义的描述", {}) == "neutral"


def test_style_prompt_tone():
    assert "cold tone" in VisualControlAgent._style_prompt("cool")
    assert "warm tone" in VisualControlAgent._style_prompt("warm")
    assert "neutral tone" in VisualControlAgent._style_prompt("neutral")
