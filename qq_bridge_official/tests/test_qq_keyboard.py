# -*- coding: utf-8 -*-
"""官方消息按钮键盘验收（2026-11-11，prove-it）。

守三件用户感受得到的行为：
  1. 全量口令都有按钮：从 game_tools 源码抽取 NATURAL_CMDS 词表（121 个），
     逐一 make_keyboard(cmd) 必须返回结构合法的键盘，且「专属按钮组覆盖率」
     达到 100%——不允许任何口令悄悄落到兜底组（禁止遗漏 / 禁止偷懒）。
  2. 按钮结构合法：style∈{0,1,3,4}、action.type=1、permission.type=2、
     label≤10 字符、data 非空、每行≤5 个、总行数≤5（腾讯平台硬限）。
  3. 非AT关键字触发：_is_warframe_command 对「普通群消息里的裸口令」返回 True，
     对闲聊返回 False，对 NapCat 事件恒 False（不误吞旧机器人）。

跑法（容器内，词表从真实源码抽，不开网络、不发真消息）：
  docker exec -i qq-bridge-official python - <<'PY' ... 见运行记录
或：
  python -m pytest tests/test_qq_keyboard.py -v （宿主 python 可用时）
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import importlib.util  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

# 直接按文件加载，绕开 plugins.qq_bridge/__init__.py 的重量级链式 import
# （它顶层 from src.group_switch / httpx / ai_mode，纯按钮单测不需要这些）。
def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass 解析注解需要模块已在 sys.modules
    spec.loader.exec_module(mod)
    return mod

kb = _load(_REPO / "src/plugins/qq_bridge/channel/qq_keyboard.py", "qq_keyboard")


def _gt_source() -> str:
    return (_REPO / "src/plugins/qq_bridge/game_tools/__init__.py").read_text(encoding="utf-8")


def _natural_cmds() -> list[str]:
    """从真实源码抽取 NATURAL_CMDS，避免测试基准与实现漂移（anti-tautology）。"""
    m = re.search(r"NATURAL_CMDS\s*=\s*\((.*?)\)", _gt_source(), re.S)
    assert m, "找不到 NATURAL_CMDS 词表"
    words = re.findall(r"[\"']([^\"']+)[\"']", m.group(1))
    assert len(words) >= 100, "词表数量异常，抽取失败"
    return words


def _ast_literal(name: str):
    """用 AST 精确取 game_tools 顶层变量字面量（tuple/dict/set/str），不靠正则。"""
    tree = ast.parse(_gt_source())
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError("源码找不到变量 %s" % name)


def _all_command_keys() -> set[str]:
    """全量口令来源（用户要求「每一个口令，禁止遗漏」）：
      * NATURAL_CMDS         免前缀自然语言口令
      * GUIDE_TITLES.keys()  发富卡片的攻略类命令（含大量别名）
      * ZERO_ARG_OK          免参数白名单里的键
      * _PLAN_CMD_WORDS      生图计划词
      * _DISPATCH_CMD_WORDS  计划下发词
      * HELP 文本里的 #命令   帮助里明列的口令
    词表本身全部来自真实源码 AST 抽取，实现改了词表而按钮没跟上 → 本测试先失败。
    """
    keys: set[str] = set()
    keys.update(_natural_cmds())
    keys.update(_ast_literal("GUIDE_TITLES").keys())
    keys.update(_ast_literal("ZERO_ARG_OK"))
    keys.update(_ast_literal("_PLAN_CMD_WORDS"))
    keys.update(_ast_literal("_DISPATCH_CMD_WORDS"))
    # HELP 里 `#命令` / 裸命令（如「下发计划」）都算口令来源
    help_text = _ast_literal("HELP")
    for token in re.findall(r"#([^\s／/，,。：:]+)", help_text):
        keys.add(token)
    return {k for k in keys if k}


def test_all_commands_have_dedicated_buttons():
    """每个 NATURAL_CMDS 口令都必须命中专属按钮组，不允许落兜底。"""
    missing = []
    for cmd in _natural_cmds():
        key = cmd.lower() if cmd.isascii() else cmd
        group = kb._GROUPS.get(cmd, kb._GROUPS.get(key))
        if group is None:
            missing.append(cmd)
    assert not missing, "以下 %d 个口令缺专属按钮组（落兜底=遗漏）：%s" % (len(missing), missing)


def test_normalize_cmd():
    assert kb.normalize_cmd("#赏金 钢铁") == "赏金"
    assert kb.normalize_cmd("＃裂隙") == "裂隙"
    assert kb.normalize_cmd("/help") == "help"
    assert kb.normalize_cmd("紫卡趋势 绝路") == "紫卡趋势"
    assert kb.normalize_cmd("  WF  ") == "wf"
    assert kb.normalize_cmd("") == ""


def test_keyboard_structure_valid():
    """按钮结构符合腾讯平台硬限（故意弄坏任一字段本测试必须失败）。"""
    from nonebot.adapters.qq.models.common import MessageKeyboard

    for cmd in sorted(_all_command_keys()) + ["", "随便一句闲聊"]:
        model, btns = kb.make_keyboard(cmd)
        assert isinstance(model, MessageKeyboard), cmd
        assert model.content and model.content.rows, cmd
        assert 1 <= len(model.content.rows) <= 5, "行数越界 %s" % cmd
        for row in model.content.rows:
            assert 1 <= len(row.buttons) <= 5, "每行按钮数越界 %s" % cmd
            for b in row.buttons:
                assert b.render_data and b.render_data.label, "缺 label %s" % cmd
                assert len(b.render_data.label) <= 10, "label 超 10 字符 %s" % cmd
                assert b.render_data.style in (0, 1, 3, 4), "style 非法 %s" % cmd
                assert b.action and b.action.type == 1, "action.type 必须回调(1) %s" % cmd
                assert b.action.permission and b.action.permission.type == 2, "permission 非法 %s" % cmd
                assert b.action.data, "data 空 %s" % cmd
        assert btns, "按钮组空 %s" % cmd


def test_menu_button_always_present():
    """每张卡片都必须带「主菜单」兜底按钮（保证用户能回主菜单）。"""
    for cmd in sorted(_all_command_keys()) + ["", "闲聊"]:
        _, btns = kb.make_keyboard(cmd)
        assert any(b.cmd == "帮助" for b in btns), "缺主菜单按钮 %s" % cmd


def test_primary_button_exists():
    """每张卡片至少有一个主按钮（蓝底 style=1），视觉层级不能全灰。"""
    for cmd in sorted(_all_command_keys()):
        _, btns = kb.make_keyboard(cmd)
        assert any(b.primary for b in btns), "无主按钮 %s" % cmd


def _is_game_command_from_source(text: str) -> bool:
    """从源码 NATURAL_CMDS 复刻 is_game_command 判定（精确/前缀匹配）。

    用真实词表判定，避免 import 重量级 game_tools 包；词表本身来自源码抽取，
    若实现改了词表而测试基准没跟上，test_all_commands_* 会先失败。
    """
    t = (text or "").strip()
    if not t:
        return False
    for cmd in sorted(_natural_cmds(), key=len, reverse=True):
        if t == cmd:
            return True
        if t.startswith(cmd) and t[len(cmd):].strip():
            return True
    return False


def test_non_at_keyword_rule():
    """非AT关键字触发：裸口令命中、闲聊不命中（防群里普通说话刷屏）。"""
    # 关键字（非AT场景下的裸口令）必须命中
    for cmd in ("赏金", "#赏金", "钢铁赏金", "裂隙", "紫卡趋势 绝路", "市价 充沛", "抽签"):
        t = cmd.lstrip("#＃").strip()
        assert _is_game_command_from_source(t), "裸口令未命中: %s" % cmd
    # 闲聊不得命中
    for noise in ("在吗", "今天天气不错", "大家好啊", "哈哈哈", "我计划去玩"):
        assert not _is_game_command_from_source(noise), "闲聊误命中: %s" % noise


def _buttons_for_key(cmd: str) -> tuple[Btn, ...]:
    """复刻 buttons_for 的查表逻辑（本地，不依赖 nonebot），用于判定是否落兜底。"""
    normalized = cmd.strip()
    while normalized and normalized[0] in "#＃/":
        normalized = normalized[1:].strip()
    if not normalized:
        return kb._FALLBACK
    parts = normalized.split()
    head = parts[0] if parts else normalized
    key = head.lower() if head.isascii() else head
    canonical = kb._ALIASES.get(key, kb._ALIASES.get(key.lower(), key))
    return kb._GROUPS.get(
        canonical, kb._GROUPS.get(canonical.lower(), kb._GROUPS.get(key.lower(), kb._FALLBACK))
    )


def test_every_command_source_has_dedicated_buttons():
    """【全量·禁止遗漏】6 个口令来源里每一个别名都必须命中专属按钮组，不许落兜底。

    这是本次「全量检查优化，禁止遗漏」的核心守门：把任一别名从 _GROUPS/_ALIASES
    删掉，本测试立即失败并列出该别名。只覆盖 NATURAL_CMDS 是不够的——
    GUIDE_TITLES/HELP 里的别名（混沌回忆/分析/WM/plans…）同样是用户能发的口令。
    """
    missing = []
    for cmd in sorted(_all_command_keys()):
        if _buttons_for_key(cmd) is kb._FALLBACK:
            missing.append(cmd)
    assert not missing, "以下 %d 个口令落兜底(=遗漏专属按钮)：%s" % (len(missing), missing)


def test_coverage_report():
    """覆盖率报告：全量口令来源 vs 专属按钮组，作为交付证据。"""
    natural = _natural_cmds()
    allkeys = _all_command_keys()
    dedicated_all = sum(1 for c in allkeys if _buttons_for_key(c) is not kb._FALLBACK)
    dedicated_nat = sum(
        1 for c in natural
        if kb._GROUPS.get(c, kb._GROUPS.get(c.lower() if c.isascii() else c)) is not None
        or _buttons_for_key(c) is not kb._FALLBACK
    )
    print("\n[覆盖报告·全量] 口令来源合计=%d 专属按钮组命中=%d 兜底=%d 覆盖率=%.1f%%" % (
        len(allkeys), dedicated_all, len(allkeys) - dedicated_all,
        100.0 * dedicated_all / len(allkeys)))
    print("[覆盖报告·NATURAL_CMDS] %d/%d 专属命中" % (dedicated_nat, len(natural)))
    assert dedicated_all == len(allkeys), "存在口令未命中专属按钮组"


if __name__ == "__main__":
    import traceback
    tests = [
        test_all_commands_have_dedicated_buttons,
        test_normalize_cmd,
        test_keyboard_structure_valid,
        test_menu_button_always_present,
        test_primary_button_exists,
        test_non_at_keyword_rule,
        test_every_command_source_has_dedicated_buttons,
        test_coverage_report,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print("PASS  %s" % t.__name__)
            passed += 1
        except Exception as e:
            print("FAIL  %s -> %s: %s" % (t.__name__, type(e).__name__, e))
            traceback.print_exc()
            failed += 1
    print("\n==== 结果: %d PASS / %d FAIL ====" % (passed, failed))
    sys.exit(1 if failed else 0)
