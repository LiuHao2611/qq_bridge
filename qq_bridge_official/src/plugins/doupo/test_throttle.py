# -*- coding: utf-8 -*-
"""斗破出图限流器单测（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_throttle.py     # 自带 runner，不依赖 pytest
    pytest src/plugins/doupo/test_throttle.py

_throttle.py 不 import nonebot，所以这里不需要打桩，直接导入即可。
时钟与随机源全部注入，测试不依赖真实时间、不会 flaky。
"""

from __future__ import annotations

import os
import sys

# _throttle.py 自身零依赖（不 import nonebot），所以这里不走 `import doupo._throttle`
# ——那会先执行 doupo/__init__.py，进而拉起 nonebot 的 matcher 注册，在无 QQ 环境
# 下必然失败。直接把本目录加进 sys.path，按文件名导入。
DOUPO_DIR = os.path.dirname(os.path.abspath(__file__))
if DOUPO_DIR not in sys.path:
    sys.path.insert(0, DOUPO_DIR)

import _throttle  # noqa: E402

Throttle = _throttle.Throttle


class Clock:
    """可手动推进的单调时钟。"""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _mk(**kw) -> tuple[Throttle, Clock]:
    clock = Clock()
    kw.setdefault("clock", clock)
    kw.setdefault("rand", lambda: 0.5)
    return Throttle(**kw), clock


# ---------------------------------------------------------------------------
# 基础放行 / 用户冷却
# ---------------------------------------------------------------------------
def test_first_hit_allowed():
    th, _ = _mk()
    ok, why = th.check(user="u1", group="g1")
    assert ok and why == "ok", (ok, why)


def test_user_gap_blocks_repeat():
    th, clock = _mk(user_gap=10.0, group_limit=999, global_limit=999)
    assert th.check(user="u1", group="g1")[0]
    clock.advance(1.0)
    ok, why = th.check(user="u1", group="g1")
    assert not ok and why == "user_gap", (ok, why)


def test_user_gap_expires():
    th, clock = _mk(user_gap=10.0, group_limit=999, global_limit=999)
    assert th.check(user="u1", group="g1")[0]
    clock.advance(10.0)  # 恰好等于冷却
    ok, why = th.check(user="u1", group="g1")
    assert ok and why == "ok", (ok, why)


def test_denial_does_not_record():
    """被拒时不更新 last，否则用户越重试越被卡死。"""
    th, clock = _mk(user_gap=10.0, group_limit=999, global_limit=999)
    assert th.check(user="u1", group="g1")[0]   # t=0 记账
    clock.advance(1.0)
    assert not th.check(user="u1", group="g1")[0]  # t=1 被拒，不应记账
    clock.advance(9.0)                          # t=10，距 t=0 已满 10s
    ok, why = th.check(user="u1", group="g1")
    assert ok and why == "ok", (ok, why)


# ---------------------------------------------------------------------------
# 群窗口 / 全局窗口
# ---------------------------------------------------------------------------
def test_group_window_limits():
    th, clock = _mk(user_gap=0.0, group_limit=3, global_limit=999, window=60.0)
    for i in range(3):
        ok, why = th.check(user=f"u{i}", group="g1")
        assert ok, (i, why)
        clock.advance(0.1)
    ok, why = th.check(user="u3", group="g1")
    assert not ok and why == "group_window", (ok, why)


def test_group_window_does_not_cross_groups():
    th, clock = _mk(user_gap=0.0, group_limit=2, global_limit=999, window=60.0)
    for i in range(2):
        assert th.check(user=f"u{i}", group="g1")[0]
        clock.advance(0.1)
    assert not th.check(user="u9", group="g1")[0]
    # 另一个群不受影响
    ok, why = th.check(user="u9", group="g2")
    assert ok and why == "ok", (ok, why)


def test_global_window_limits():
    th, clock = _mk(user_gap=0.0, group_limit=999, global_limit=2, window=60.0)
    assert th.check(user="u1", group="g1")[0]
    clock.advance(0.1)
    assert th.check(user="u2", group="g2")[0]
    clock.advance(0.1)
    ok, why = th.check(user="u3", group="g3")
    assert not ok and why == "global_window", (ok, why)


def test_window_slides():
    th, clock = _mk(user_gap=0.0, group_limit=2, global_limit=999, window=60.0)
    assert th.check(user="u1", group="g1")[0]   # t=0
    clock.advance(0.1)
    assert th.check(user="u2", group="g1")[0]   # t=0.1
    clock.advance(30.0)
    assert not th.check(user="u3", group="g1")[0]  # t=30.1 仍在窗口内
    clock.advance(30.0)                            # t=60.1，两条历史都过期
    ok, why = th.check(user="u3", group="g1")
    assert ok and why == "ok", (ok, why)


# ---------------------------------------------------------------------------
# 私聊（group=None）
# ---------------------------------------------------------------------------
def test_no_group_only_user_and_global():
    th, clock = _mk(user_gap=0.0, group_limit=1, global_limit=999, window=60.0)
    # group=None 时不受「每群」上限约束，只受用户/全局约束
    for i in range(3):
        ok, why = th.check(user=f"u{i}", group=None)
        assert ok, (i, why)
        clock.advance(0.1)


# ---------------------------------------------------------------------------
# 抖动
# ---------------------------------------------------------------------------
def test_jitter_within_range():
    th, _ = _mk(jitter=(0.35, 1.20), rand=lambda: 0.0)
    assert abs(th.jitter_delay() - 0.35) < 1e-9
    th2, _ = _mk(jitter=(0.35, 1.20), rand=lambda: 1.0)
    assert abs(th2.jitter_delay() - 1.20) < 1e-9
    th3, _ = _mk(jitter=(0.35, 1.20), rand=lambda: 0.5)
    d = th3.jitter_delay()
    assert 0.35 <= d <= 1.20, d


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    fail = []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print(f"FAIL  {name}  ->  {type(e).__name__}: {e}")
    print("=" * 70)
    print(f"THROTTLE_TOTAL={len(fns)} PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
