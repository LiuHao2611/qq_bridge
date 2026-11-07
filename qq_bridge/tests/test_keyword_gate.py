# -*- coding: utf-8 -*-
"""关键词门禁测试（KEYWORD_GATE）。

背景：好友申请的系统验证消息（如「请求添加你为好友」）会被当作生图提示词，
白烧一次配额与费用。开启门禁后，只有命中关键词的消息才受理，其余一律静默。
"""
from src.plugins.qq_bridge.intent import match_keyword


def test_hi_is_test_keyword():
    """hi 作为测试对话关键词，大小写不敏感，不生图。"""
    assert match_keyword("hi") == ("test", "")
    assert match_keyword("HI") == ("test", "")
    assert match_keyword("  hi  ") == ("test", "")
    assert match_keyword("你好") == ("test", "")


def test_draw_prefix_strips_keyword():
    """生图触发词作前缀，其后内容作为提示词。"""
    kind, prompt = match_keyword("生图 一只在雨中的猫")
    assert kind == "draw"
    assert prompt == "一只在雨中的猫"


def test_draw_english_case_insensitive():
    kind, prompt = match_keyword("Draw a red apple")
    assert kind == "draw"
    assert prompt == "a red apple"


def test_draw_without_prompt_gives_empty():
    """只发生图、不带描述时提示词为空，由下游提示补描述。"""
    assert match_keyword("生图") == ("draw", "")


def test_slash_command_always_passes():
    """斜杠指令是零成本自检命令，不受关键词限制。"""
    assert match_keyword("/help")[0] == "cmd"
    assert match_keyword("/quota")[0] == "cmd"


def test_friend_request_and_chat_are_missed():
    """好友申请与闲聊不得命中，避免误触发生图。"""
    assert match_keyword("请求添加你为好友") == ("", "")
    assert match_keyword("今天天气不错") == ("", "")
    assert match_keyword("") == ("", "")
    assert match_keyword("   ") == ("", "")
