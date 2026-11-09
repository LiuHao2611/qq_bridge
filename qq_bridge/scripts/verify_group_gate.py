"""机器验收：GROUP_ENABLED 开关是否真的能在任何 matcher 之前丢弃群消息。

为什么要单独写这个：
  这道闸的价值全在「群消息一条都到不了插件」。人工发一条群消息去试，既要有
  在线 QQ，又只能证明「这一次」没反应 —— 无法证明私聊没被误伤，也无法证明
  闸口关闭时群消息还能正常进来。所以直接跑框架的那条真实路径。

做法（不 mock 判定逻辑，只 mock 事件对象）：
  1. 用 runpy 以 run_name != "__main__" 执行 bot.py —— 会走完 nonebot.init()、
     插件加载、总闸注册，但跳过末尾的 nonebot.run()，所以不监听端口。
  2. 从 nonebot.message._event_preprocessors 里精确取出 bot.py 注册的那个
     Dependent（按函数名定位），这正是线上被调用的同一个对象。
  3. 构造真实的 OneBot V11 GroupMessageEvent / PrivateMessageEvent，按框架的
     调用方式 proc(bot=..., event=..., state=..., stack=..., dependency_cache=...)
     调用它，看是否抛 IgnoredException。
  4. 两种挡位（false / true）各起一个子进程跑，父进程断言结果。

用法：
    .venv\\Scripts\\python.exe scripts\\verify_group_gate.py
退出码：0 = 全部 PASS；1 = 有 FAIL。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC_NAME = "_block_group_messages"


def _payload(group: bool) -> dict:
    base = {
        "time": 1730000000,
        "self_id": 2149466287,
        "post_type": "message",
        "sub_type": "normal",
        "user_id": 1743124119,
        "message_type": "group" if group else "private",
        "message_id": 1001,
        "message": "测试",
        "original_message": "测试",
        "raw_message": "测试",
        "font": 0,
        "sender": {"user_id": 1743124119, "nickname": "tester"},
        "to_me": True,
    }
    if group:
        base["group_id"] = 1075851417
    return base


async def _child_main() -> None:
    import runpy

    os.chdir(ROOT)
    # runpy 不会像 `python bot.py` 那样把脚本目录塞进 sys.path[0]，
    # bot.py 里 `import src.plugins...` 会 ImportError，必须手动补。
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    # run_name 不能是 "__main__"，否则会执行到 nonebot.run() 把端口占住
    runpy.run_path(os.path.join(ROOT, "bot.py"), run_name="gate_probe")

    from nonebot.adapters.onebot.v11.event import (  # noqa: E402
        GroupMessageEvent,
        PrivateMessageEvent,
    )
    from nonebot.exception import IgnoredException  # noqa: E402
    from nonebot.message import _event_preprocessors  # noqa: E402

    deps = [d for d in _event_preprocessors if getattr(d.call, "__name__", "") == PROC_NAME]

    result: dict = {
        "gate_registered": len(deps) == 1,
        "dep_count": len(deps),
        "gate_env": os.environ.get("GROUP_ENABLED"),
        "group_dropped": None,
        "private_dropped": None,
    }

    if result["gate_registered"]:
        dep = deps[0]

        async def _probe(ev) -> bool:
            """返回 True 表示「被丢弃」。"""
            try:
                await dep(bot=None, event=ev, state={}, stack=None, dependency_cache=None)
            except IgnoredException:
                return True
            return False

        result["group_dropped"] = await _probe(GroupMessageEvent(**_payload(True)))
        result["private_dropped"] = await _probe(PrivateMessageEvent(**_payload(False)))

    print("GATE_RESULT " + json.dumps(result, ensure_ascii=False), flush=True)


def _run_child(mode: str) -> dict:
    env = dict(os.environ)
    env["GROUP_ENABLED"] = mode
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--child"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("GATE_RESULT "):
            return json.loads(line[len("GATE_RESULT "):])
    raise RuntimeError(
        "子进程没吐结果（mode=%s, rc=%s）\n--- stdout ---\n%s\n--- stderr ---\n%s"
        % (mode, proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:])
    )


def main() -> int:
    if "--child" in sys.argv:
        import asyncio

        asyncio.run(_child_main())
        return 0

    offline = _run_child("false")
    online = _run_child("true")

    rows = [
        ("GROUP_ENABLED=false 时闸已注册", offline["gate_registered"], True),
        ("GROUP_ENABLED=false 时群消息被丢弃", offline["group_dropped"], True),
        ("GROUP_ENABLED=false 时私聊放行", offline["private_dropped"], False),
        ("GROUP_ENABLED=true 时闸未注册", online["gate_registered"], False),
        ("GROUP_ENABLED=true 时不干预（无闸可比）", online["group_dropped"], None),
    ]

    fail = 0
    print("=" * 68)
    for name, actual, expect in rows:
        if expect is None:
            print("  SKIP  %-42s actual=%r" % (name, actual))
            continue
        ok = actual == expect
        fail += 0 if ok else 1
        print("  %-5s %-42s actual=%-5r expect=%r"
              % ("PASS" if ok else "FAIL", name, actual, expect))
    print("=" * 68)
    print("%d PASS / %d FAIL" % (len(rows) - 1 - fail, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
