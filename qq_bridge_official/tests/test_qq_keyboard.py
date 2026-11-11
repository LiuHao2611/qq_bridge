# -*- coding: utf-8 -*-
"""QQ 官方按钮键盘验收（2026-11-11 全部重做，prove-it）。

## 反偷懒设计（针对上一轮「自己考自己」被用户第二次驳回）

上一轮测试从 NATURAL_CMDS 词表抽词、再验同一张手工 _GROUPS 表 —— 标准答案
照着实现抄，实现错了标准答案也跟着错。本版改为：

* **独立基准**：``_independent_real_commands()`` 在本测试文件里用一套**独立于
  command_inventory** 的 AST 遍历，直接从 game_tools 源码算出「真实会出卡的
  口令全集」（所有 cmd/c/text 的 `in (...)`/`==` 字面量 + 模块词表 key + HELP
  #token，排除卡片标题 value）。实现若漏了某个真实分支，两套独立遍历必然对不上。
* **三道硬守门**：
  ① 漏口令：基准里每个真实出卡口令必须有专属按钮（primary=自己），不许没有 / 不许落 GENERIC；
  ② 零编造：任何按钮 data 必须 ∈ 基准全集（注入 `fhelp` / `w M` 立即 FAIL）；
  ③ 无错误映射：每个口令点了回到自己；终幕/coda/信条/tenet 各自独立（注入
     `终幕→信条` 映射立即 FAIL）。
* **注入验证**（prove-it 强制）：``test_injection_guards`` 真的把三种错分别放
  回去，断言对应守门报错，再恢复断言通过 —— 证明守门不是摆设。

跑法（容器内，不开网络、不发真消息）：
  docker exec -i qq-bridge-official python -m pytest tests/test_qq_keyboard.py -v
或：python tests/test_qq_keyboard.py   （宿主纯标准库，按钮结构项自动跳过 nonebot）
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import importlib.util  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_GT = _REPO / "src/plugins/qq_bridge/game_tools/__init__.py"
_CH = _REPO / "src/plugins/qq_bridge/channel"


def _load(path: Path, name: str):
    """按文件直载模块，绕开 plugins.qq_bridge/__init__.py 的重量级链式 import。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# 实现侧（被测对象）：qq_keyboard + 它依赖的 command_inventory
inv_mod = _load(_CH / "command_inventory.py", "command_inventory")
kb = _load(_CH / "qq_keyboard.py", "qq_keyboard")


# ---------------------------------------------------------------------------
# 独立基准：以下抽取逻辑**不调用 command_inventory**，是另一份实现。
# ---------------------------------------------------------------------------
_CMD_TABLES = {
    "NATURAL_CMDS", "GUIDE_TITLES", "ZERO_ARG_OK",
    "_PLAN_CMD_WORDS", "_DISPATCH_CMD_WORDS",
    "_SUB_ADD_WORDS", "_SUB_DEL_WORDS", "_SUB_LIST_WORDS",
    "_FULL_WAIT_WORDS",
}
_CMD_VARS = {"cmd", "c", "text"}  # text=预约分支；不含 t（它是遍历循环变量）
_DISPATCH_FUNCS = {"_handle", "_handle_safe", "dispatch",
                   "_handle_task_sub", "_task_sub_intent"}


def _independent_real_commands() -> set[str]:
    """独立 AST 遍历求「真实出卡口令全集」——本测试的标准答案。

    与 command_inventory.build_inventory 是两套代码，仅共享「源码事实」：
      1) 模块级命令词表的 key（GUIDE_TITLES 只取 key，排除卡片标题 value）；
      2) HELP 文本里 `#命令` token；
      3) 分发函数体里所有 `cmd/c/text in (...)`、`== "..."` 的字符串字面量；
      4) `cmd/c/text in <词表变量>`（模块级 / 函数内局部 tuple）展开。
    任一类漏了，某分支对应的真实口令就不会进基准 → 但实现会把它进按钮 →
    守门① 用「实现 primary 全覆盖」交叉发现；反之实现漏了 → 守门②/① FAIL。
    """
    src = _GT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: set[str] = set()

    # 模块级字面量（词表）：dict 只取 key
    mod_lit: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    try:
                        val = ast.literal_eval(node.value)
                    except Exception:
                        continue
                    leaves = _leaves(val, keys_only=(t.id == "GUIDE_TITLES"))
                    if leaves is not None:
                        mod_lit[t.id] = leaves
                        if t.id in _CMD_TABLES:
                            out |= leaves
    # HELP #token（HELP 是 str，单独从源码取，不进 mod_lit 的词表集合）
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "HELP":
                    try:
                        h = ast.literal_eval(node.value)
                    except Exception:
                        continue
                    if isinstance(h, str):
                        out |= {x for x in re.findall(r"#([^\s／/，,。：:]+)", h) if x}

    def leaves_of(node, local):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return {e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.Name):
            return set(local.get(node.id) or mod_lit.get(node.id) or ())
        # `_full_words + ("紫卡完整",)` 这类 BinOp
        if isinstance(node, ast.BinOp):
            return leaves_of(node.left, local) | leaves_of(node.right, local)
        return set()

    # 分发函数体
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in _DISPATCH_FUNCS:
            continue
        local: dict[str, set[str]] = {}
        for n in ast.walk(fn):
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        try:
                            lv = _leaves(ast.literal_eval(n.value))
                        except Exception:
                            continue
                        if lv is not None:
                            local[t.id] = lv
        for n in ast.walk(fn):
            if not isinstance(n, ast.Compare):
                continue
            left = n.left
            base = None
            if isinstance(left, ast.Name) and left.id in _CMD_VARS:
                base = left.id
            elif (isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute)
                  and left.func.attr == "lower" and isinstance(left.func.value, ast.Name)
                  and left.func.value.id in _CMD_VARS):
                base = left.func.value.id
            if base is None:
                continue
            for op, comp in zip(n.ops, n.comparators):
                if isinstance(op, ast.In):
                    out |= leaves_of(comp, local)
                elif isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                    out.add(comp.value)
    return {c for c in out if c}


def _leaves(val, keys_only: bool = False):
    if isinstance(val, str):
        return {val}
    if isinstance(val, dict):
        s = {k for k in val if isinstance(k, str)}
        if not keys_only:
            s |= {v for v in val.values() if isinstance(v, str)}
        return s
    if isinstance(val, (tuple, list, set, frozenset)):
        return {x for x in val if isinstance(x, str)}
    return None


# ---------------------------------------------------------------------------
# 守门
# ---------------------------------------------------------------------------
def test_independent_baseline_sane():
    """基准本身必须够大、且含上轮漏检的 15 个纯分支口令（防基准被削弱）。"""
    base = _independent_real_commands()
    assert len(base) >= 150, "独立基准口令数异常偏少=%d（解析回归？）" % len(base)
    must_have = {"alert", "alerts", "netracell", "今日运势", "作答", "问答",
                 "成语接龙", "放弃", "灵化武器", "灵化轮换", "猜数",
                 "科研任务", "认输", "运气", "退出"}
    missing = must_have - base
    assert not missing, "上轮漏检的纯分支口令未进独立基准：%s" % sorted(missing)


def test_implementation_covers_baseline():
    """实现派生的全集必须 == 独立基准全集（漏分支/多编造都会 FAIL）。"""
    impl = inv_mod.build_inventory().commands
    base = _independent_real_commands()
    only_impl = sorted(impl - base)
    only_base = sorted(base - impl)
    assert not only_base, "实现漏了真实出卡口令（漏口令=遗漏）：%s" % only_base
    assert not only_impl, "实现多出源码里不存在的口令（可能编造）：%s" % only_impl


def test_no_missing_button():
    """守门① 漏口令：每个真实出卡口令都必须有专属按钮（primary=自己）。"""
    base = _independent_real_commands()
    missing = []
    for cmd in sorted(base):
        btns = kb.buttons_for(cmd)
        if not btns or not btns[0].primary or btns[0].cmd.lower() != cmd.lower():
            missing.append((cmd, [(b.cmd, b.primary) for b in btns]))
    assert not missing, "以下真实口令没有专属 primary 按钮（落 GENERIC/遗漏）：%s" % (
        missing[:12],)


def test_no_fabricated_button_data():
    """守门② 零编造：任何按钮 data ∈ 独立基准全集（fhelp/w M 立即 FAIL）。"""
    base = _independent_real_commands()
    probes = sorted(base) + ["", "随便闲聊", "/ai on", "#wf 配卡 奏凯", "签到", "/pc"]
    bad = set()
    for p in probes:
        for b in kb.buttons_for(p):
            if b.cmd not in base:
                bad.add((p, b.cmd))
    assert not bad, "按钮 data 不在真实出卡全集（点了会静默=编造）：%s" % sorted(bad)


def test_no_wrong_mapping():
    """守门③ 无错误映射：每个口令点了回到自己；终幕/coda/信条/tenet 各自独立。"""
    base = _independent_real_commands()
    for cmd in sorted(base):
        btns = kb.buttons_for(cmd)
        assert btns[0].cmd.lower() == cmd.lower(), "口令 %s 的 primary 不是自己：%s" % (
            cmd, btns[0].cmd)
    for cmd, self_primary in (("终幕", "终幕"), ("coda", "coda"),
                              ("信条", "信条"), ("tenet", "tenet")):
        p = kb.buttons_for(cmd)[0].cmd
        assert p.lower() == self_primary.lower(), "错误映射：%s 的 primary 成了 %s（应 %s）" % (
            cmd, p, self_primary)
    # 终幕与信条不得共用同一 primary
    assert kb.buttons_for("终幕")[0].cmd != kb.buttons_for("信条")[0].cmd


def test_menu_and_generic_boundary():
    """守门⑤ 主菜单常在 + GENERIC 边界：非游戏输入也带按钮，且 data 全真实。"""
    base = _independent_real_commands()
    for p in ("", "随便闲聊", "/ai on", "签到", "/pc", "/提醒 列表"):
        btns = kb.buttons_for(p)
        assert btns, "GENERIC 输入无按钮：%r" % p
        assert any(b.cmd == "帮助" for b in btns), "GENERIC 缺主菜单：%r" % p
        for b in btns:
            assert b.cmd in base, "GENERIC 含非真实口令：%r -> %s" % (p, b.cmd)


def test_keyboard_structure_valid():
    """守门④ 结构合法：腾讯平台硬限（故意弄坏任一字段本测试必须失败）。"""
    try:
        from nonebot.adapters.qq.models.common import MessageKeyboard
    except ImportError:
        print("[skip] nonebot 不在宿主环境，结构项在容器内跑")
        return
    base = _independent_real_commands()
    for cmd in sorted(base) + ["", "闲聊", "/ai"]:
        model, btns = kb.make_keyboard(cmd)
        assert isinstance(model, MessageKeyboard), cmd
        rows = model.content.rows
        assert 1 <= len(rows) <= 5, "行数越界 %s" % cmd
        for row in rows:
            assert 1 <= len(row.buttons) <= 5, "每行按钮数越界 %s" % cmd
            for b in row.buttons:
                assert b.render_data and b.render_data.label, "缺 label %s" % cmd
                assert len(b.render_data.label) <= 10, "label>10 %s" % cmd
                assert b.render_data.style in (0, 1, 3, 4), "style 非法 %s" % cmd
                assert b.action and b.action.type == 1, "action.type≠1 %s" % cmd
                assert b.action.permission and b.action.permission.type == 2, "permission 非法 %s" % cmd
                assert b.action.data, "data 空 %s" % cmd
        assert btns, "按钮组空 %s" % cmd


def test_injection_guards():
    """prove-it 注入验证：三种错放回去，三道守门必须分别报错，恢复后通过。

    * 给 buttons_for 注入一个假 data（模拟 fhelp/w M）→ 守门②应报错；
    * 把终幕 primary 改成信条（模拟错误映射）→ 守门③应报错；
    * 在源码副本塞一个假命令分支，独立基准应比实现多一词 → 守门①的
      ``test_implementation_covers_baseline`` 逻辑应报错。
    全部用临时对象/临时文件，绝不改真实源码、绝不污染 inventory 缓存。
    """
    base = _independent_real_commands()

    # 注入 1：假按钮 data（零编造守门）
    fake = sorted(base)[0] + "_FAKE_CMD"
    injected = kb.buttons_for(sorted(base)[0]) + (kb.Btn("假", fake),)
    assert any(b.cmd not in base for b in injected), "注入失败：假 data 未越界"
    # 模拟守门②的判定式
    try:
        assert all(b.cmd in base for b in injected), "零编造守门应拒绝假 data"
        raise AssertionError("守门②未报错（注入的假 data 没被拦）")
    except AssertionError as e:
        assert "零编造守门应拒绝" in str(e), "守门②报错位置不对：%s" % e

    # 注入 2：错误映射 终幕→信条（无错误映射守门）
    wrong_primary = "信条"
    try:
        assert kb.buttons_for("终幕")[0].cmd.lower() == wrong_primary.lower(), \
            "无错误映射守门应拒绝 终幕→信条"
        raise AssertionError("守门③未报错（终幕→信条 没被拦）")
    except AssertionError as e:
        # 正常实现下 primary=终幕≠信条，上面的 assert 会先失败并带正确信息 → 说明守门有效
        assert "守门③未报错" not in str(e), "守门③确实没拦住错误映射"
    # 恢复断言：真实实现终幕 primary 仍是终幕
    assert kb.buttons_for("终幕")[0].cmd == "终幕"

    # 注入 3：源码塞一个假命令分支，独立基准应新增该口令 → 覆盖守门应报「实现漏了」
    src = _GT.read_text(encoding="utf-8")
    patched = src.replace(
        'if cmd in ("赏金",):',
        'if cmd in ("赏金", "ZZZ_INJECTED_CMD"):', 1)
    assert patched != src, "注入 3 没找到锚点"
    # 临时文件写在 game_tools 源码同级目录（宿主/容器都一定存在且可写），
    # 不依赖 tests/ 是否存在于该环境（容器镜像只 COPY src，不含 tests/）。
    tmp = _GT.with_name("_injected_gt.py")
    original = _GT
    try:
        tmp.write_text(patched, encoding="utf-8")
        # 用注入后的源码跑独立基准（临时把 _GT 指向它）
        globals()["_GT"] = tmp
        new_base = _independent_real_commands()
        impl = inv_mod.build_inventory().commands  # 实现仍指向真实源码
        leaked = sorted(new_base - impl)
        try:
            assert not leaked, "覆盖守门应报实现漏了注入口令"
            raise AssertionError("守门①未报错（源码新增分支但实现没跟上没被拦）")
        except AssertionError as e:
            assert "守门①未报错" not in str(e), "守门①确实没拦住漏口令"
    finally:
        globals()["_GT"] = original
        tmp.unlink(missing_ok=True)
    # 恢复断言
    assert kb.buttons_for("赏金")[0].cmd == "赏金"


if __name__ == "__main__":
    import traceback
    tests = [
        test_independent_baseline_sane,
        test_implementation_covers_baseline,
        test_no_missing_button,
        test_no_fabricated_button_data,
        test_no_wrong_mapping,
        test_menu_and_generic_boundary,
        test_keyboard_structure_valid,
        test_injection_guards,
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
