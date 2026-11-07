# -*- coding: utf-8 -*-
"""intent 验收测试（设计文档 §3.3 + §6 指令集）。

验收标准：
  纯文本→文生图；图片+文本→图生图；/video→视频；未知指令→/help。
"""
from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.dispatcher import QQEvent
from src.plugins.qq_bridge.intent import route, validate_prompt
from src.plugins.qq_bridge.session import Session


def ev(text="", images=None, group=False):
    return QQEvent(
        user_id=10001,
        group_id=20001 if group else 0,
        message_type="group" if group else "private",
        raw_text=text,
        images=images or [],
    )


def sess(mode="image", pending=None):
    s = Session(user_id=10001)
    s.mode = mode
    s.pending_image = pending
    return s


# ---------------- §6 指令集 ----------------

def test_plain_text_routes_to_t2i():
    r = route(ev("一只赛博朋克猫"), sess())
    assert r.kind == "t2i"
    assert r.api == "/t2i/online/generate"
    assert r.payload["prompt"] == "一只赛博朋克猫"


def test_image_with_text_routes_to_i2i():
    """验收：图片+文本→图生图。"""
    r = route(ev("改成水彩风", images=["http://cdn/a.png"]), sess())
    assert r.kind == "i2i"
    assert r.payload["prompt"] == "改成水彩风"
    assert r.consume_image is True


def test_pending_image_with_text_continues_editing():
    """§3.8 验收场景：先发图 → 再发文字，能关联源图。"""
    r = route(ev("改成赛博朋克"), sess(pending="http://cdn/a.png"))
    assert r.kind == "i2i"
    assert r.consume_image is True


def test_upscale_command():
    r = route(ev("/upscale"), sess(pending="http://cdn/a.png"))
    assert r.kind == "i2i"
    assert r.payload["mode"] == "upscale"


def test_outpaint_command_with_direction():
    r = route(ev("/outpaint 左 加宽背景"), sess(pending="http://cdn/a.png"))
    assert r.kind == "i2i"
    assert r.payload["mode"] == "outpaint"
    assert r.payload["left"] == 128
    assert r.payload["top"] == 0
    assert r.payload["prompt"] == "加宽背景"


def test_outpaint_without_direction_returns_usage():
    r = route(ev("/outpaint"), sess(pending="http://cdn/a.png"))
    assert r.kind == "reply"
    assert "用法" in r.text


def test_video_command():
    r = route(ev("/video 一个夕阳下的城市航拍"), sess())
    assert r.kind == "video"
    assert r.api == "/qq/video-generate"
    assert r.payload["prompt"] == "一个夕阳下的城市航拍"


def test_video_command_without_prompt_returns_usage():
    r = route(ev("/video"), sess())
    assert r.kind == "reply"
    assert "用法" in r.text


def test_mode_switch_commands():
    assert route(ev("/mode video"), sess()).text == "__MODE_VIDEO__"
    assert route(ev("/mode image"), sess()).text == "__MODE_IMAGE__"


def test_session_video_mode_makes_plain_text_a_video():
    r = route(ev("航拍城市"), sess(mode="video"))
    assert r.kind == "video"


def test_help_command():
    r = route(ev("/help"), sess())
    assert r.kind == "reply"
    assert "文生图" in r.text


def test_empty_text_returns_guidance():
    r = route(ev(""), sess())
    assert r.kind == "reply"
    assert "help" in r.text


def test_status_and_quota_placeholders():
    assert route(ev("/status"), sess()).text == "__STATUS__"
    assert route(ev("/quota"), sess()).text == "__QUOTA__"


# ---------------- §3.6 prompt 长度 ----------------

def test_prompt_length_limit():
    Config.PROMPT_MAX_LEN = 10
    ok, why = validate_prompt("短")
    assert ok is True
    ok, why = validate_prompt("这是一段明显超过十个字的超长提示词内容")
    assert ok is False
    assert "300" not in why  # 提示应反映当前配置值
    assert "10" in why
