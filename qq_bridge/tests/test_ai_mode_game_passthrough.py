# -*- coding: utf-8 -*-
"""AI 模式下「裸词游戏口令」路由回归测试（2026-10-23 修复）。

背景（bug）：某群开启 AI 陪聊（/ai on）后，群友直接发「钢铁裂隙 / 灵化」等
裸词游戏口令，主流程 _route 把它们当闲聊送进大模型，game_tools 独立 matcher
在 AI 开启时又对裸词整类跳过 —— 结果只有 AI 陪聊回复、游戏卡片消失。

修复：
  * game_tools._handle —— AI 开启时仅跳过「非游戏指令」裸词（is_game_command）；
  * 主流程 _route —— AI 开启时识别到游戏指令裸词直接 passthrough 交还 game_tools，
    不再送进 _handle_ai_chat，杜绝双重回复。

本测试锁定两条规则：
  1) is_game_command 词表判定（游戏词 True / 闲聊 False / WF 实体名兜底 False）；
  2) _route 在 AI 开启群里：游戏裸词不进 AI 陪聊、闲聊仍进 AI 陪聊。
"""
from types import SimpleNamespace

from src.plugins.qq_bridge.game_tools import is_game_command


# ---------------- 规则 1：纯函数词表判定 ----------------

def test_game_bare_words_recognized():
    for t in ["钢铁裂隙", "普通裂隙", "裂隙", "灵化", "紫卡", "赏金", "突击", "奸商"]:
        assert is_game_command(t) is True, t


def test_chitchat_not_game_command():
    for t in ["今天天气真好", "在吗", "你是谁", "你好呀", "讲个笑话"]:
        assert is_game_command(t) is False, t


def test_wf_entity_name_fallback_excluded():
    """WF 实体名（毒妈/咖喱棒/充沛）靠 _match_natural 兜底，不应被 is_game_command 认，
    否则会把闲聊里提到角色名的句子误判成指令。"""
    for t in ["毒妈", "咖喱棒", "充沛", "rhino"]:
        assert is_game_command(t) is False, t


def test_game_prefix_with_arg_recognized():
    # 粘连/带参写法仍算游戏指令（交给 game_tools 解析参数）
    assert is_game_command("钢铁裂隙怎么打") is True
    assert is_game_command("灵化武器有哪些") is True


def test_empty_text_not_game_command():
    assert is_game_command("") is False
    assert is_game_command("   ") is False


# ---------------- 规则 2：_route 在 AI 开启群里的分流 ----------------

def test_route_passthrough_game_cmd_in_ai_mode(monkeypatch):
    import asyncio

    import src.plugins.qq_bridge as mod

    monkeypatch.setattr(mod, "allow", lambda ev: (True, ""))
    monkeypatch.setattr(mod.Config, "MM_ENABLE", False)
    monkeypatch.setattr(mod.Config, "MM_ASR_PRIVATE", False)
    monkeypatch.setattr(mod.Config, "PC_ENABLE", False)
    monkeypatch.setattr(mod, "_try_pc_natural",
                        lambda ev, tid: asyncio.sleep(0, result=False))
    monkeypatch.setattr(mod, "_parse_ai_cmd", lambda t: None)

    ai_chat_calls = []

    async def _fake_ai_chat(ev, trace_id):
        ai_chat_calls.append(ev.raw_text)

    monkeypatch.setattr(mod, "_handle_ai_chat", _fake_ai_chat)
    monkeypatch.setattr(mod.ai_mode_store, "is_on", lambda scope: True)

    def ev(text):
        return SimpleNamespace(
            raw_text=text, scope_key="g:900000001", is_group=True,
            group_id=900000001, user_id=900000002, images=None, voices=None,
        )

    async def run():
        # 游戏裸词：不应进 AI 陪聊（交给 game_tools 渲染卡片）
        for t in ["钢铁裂隙", "灵化", "普通裂隙"]:
            ai_chat_calls.clear()
            await mod._route(ev(t), "tid-" + t)
            assert len(ai_chat_calls) == 0, f"{t} 不应进 AI 陪聊"
        # 闲聊：仍应进 AI 陪聊
        for t in ["今天天气真好", "在吗"]:
            ai_chat_calls.clear()
            await mod._route(ev(t), "tid-" + t)
            assert len(ai_chat_calls) == 1, f"{t} 应进 AI 陪聊"

    asyncio.run(run())
