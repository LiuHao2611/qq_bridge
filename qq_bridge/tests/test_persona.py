# -*- coding: utf-8 -*-
"""多人设库验收（2026-10-21，persona.py）。

覆盖：
  1. 默认人设 = benmiao，且正文与 Config 内置默认**逐字一致**（换库不换默认行为）
  2. 人设库能加载抓取来的 10 个外部人设（data/personas/*.json）
  3. 切换：set_scope 后 prompt_for 跟着变；clear_scope 回全局
  4. custom id → 沿用 Config.AI_SYSTEM_PROMPT（向后兼容老用法）
  5. 未知 id → 给默认人设（记日志），不崩、不伪装成请求的人设
  6. 写入/删除：内置不可覆盖不可删；导入的可存可删（用临时目录，不碰真实库）
  7. sticker_hint 随人设变（不再是硬编码「猫管家」）

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_persona.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# 与 test_tools_fc.py 同款：插 src/plugins 保持单份 qq_bridge 命名空间
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.persona import (  # noqa: E402
    BUILTINS, CUSTOM_ID, DEFAULT_ID, Persona, PersonaStore)


def test_default_persona_matches_config_default():
    """内置 benmiao 正文必须与 Config 内置默认逐字一致（防两份漂移）。"""
    assert BUILTINS[DEFAULT_ID].prompt == Config._AI_SYSTEM_DEFAULT


def test_library_loads_scraped_personas():
    """人设库应能加载抓取来的外部人设（至少 10 个）。"""
    store = PersonaStore()
    ids = {p.id for p in store.all()}
    assert DEFAULT_ID in ids and CUSTOM_ID in ids
    external = ids - set(BUILTINS)
    assert len(external) >= 10, "外部人设少于 10 个：%s" % sorted(external)
    # 抽样：露娜存在且有正文与来源
    luna = store.get("luna")
    assert luna is not None and luna.prompt
    assert luna.source  # 记了来源（可追溯）
    # 适配后的正文不得残留编码 Agent 专属指令
    for kw in ("emotion_sense", "{{", "工作目录"):
        assert kw not in luna.prompt


def test_switch_persona_per_scope(tmp_path):
    store = PersonaStore(tmp_path)
    store.save(Persona(id="tmp_a", name="临时A", prompt="我是A"))
    # 全局默认
    Config.AI_PERSONA = DEFAULT_ID
    assert store.current_id("g:1") == DEFAULT_ID
    assert store.prompt_for("g:1") == BUILTINS[DEFAULT_ID].prompt
    # 切换 g:1，其它会话不受影响
    assert store.set_scope("g:1", "tmp_a") is True
    assert store.prompt_for("g:1") == "我是A"
    assert store.prompt_for("g:2") == BUILTINS[DEFAULT_ID].prompt
    # 清覆盖 → 回全局
    assert store.clear_scope("g:1") is True
    assert store.prompt_for("g:1") == BUILTINS[DEFAULT_ID].prompt
    # 未知 id 不能切
    assert store.set_scope("g:1", "不存在的人设") is False


def test_custom_id_uses_config_prompt(tmp_path):
    """custom = 老用法逃生口：直接用 Config.AI_SYSTEM_PROMPT。"""
    store = PersonaStore(tmp_path)
    Config.AI_PERSONA = CUSTOM_ID
    old = Config.AI_SYSTEM_PROMPT
    try:
        Config.AI_SYSTEM_PROMPT = "手搓人设文本"
        assert store.prompt_for("g:9") == "手搓人设文本"
    finally:
        Config.AI_SYSTEM_PROMPT = old
        Config.AI_PERSONA = DEFAULT_ID


def test_unknown_persona_falls_back_to_default(tmp_path):
    """配了不存在的 id → 给默认人设（不崩、不装成请求的人设）。"""
    store = PersonaStore(tmp_path)
    Config.AI_PERSONA = "压根没这个人设"
    try:
        assert store.current("g:1").id == DEFAULT_ID
    finally:
        Config.AI_PERSONA = DEFAULT_ID


def test_builtin_protected_and_imported_removable(tmp_path):
    store = PersonaStore(tmp_path)
    # 内置不可覆盖（save 抛错）、不可删
    with pytest.raises(ValueError):
        store.save(Persona(id=DEFAULT_ID, name="冒名", prompt="x"))
    assert store.remove(DEFAULT_ID) is False
    # 导入的可存可删
    store.save(Persona(id="tmp_b", name="临时B", prompt="我是B"))
    assert store.get("tmp_b").prompt == "我是B"
    assert store.remove("tmp_b") is True
    assert store.get("tmp_b") is None


def test_sticker_hint_follows_persona(tmp_path):
    """表情人设句跟着人设走（不再是硬编码猫管家）。"""
    store = PersonaStore(tmp_path)
    store.save(Persona(id="tmp_c", name="临时C", tagline="测试标语",
                       prompt="我是C", sticker_hint="人设一致性：我是C。"))
    Config.AI_PERSONA = DEFAULT_ID
    assert "猫管家" in store.hint_for("g:1")  # 默认人设自己的句子
    store.set_scope("g:1", "tmp_c")
    assert store.hint_for("g:1") == "人设一致性：我是C。"
    assert "猫管家" not in store.hint_for("g:1")
    store.clear_scope("g:1")
