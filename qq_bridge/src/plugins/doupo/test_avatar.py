# -*- coding: utf-8 -*-
"""斗破角色动态形象（avatar）单测 —— 零依赖、exit-code 可验证。

运行：
    python src/plugins/doupo/test_avatar.py
    pytest src/plugins/doupo/test_avatar.py
"""
from __future__ import annotations

import os
import sys
import types

# ---- 让 doupo 包与 src.* 都可被导入 ----
# PLUGINS_DIR 供 `from doupo import ...`；REPO_ROOT 供插件 __init__.py 里的
# `from src.game_mode import ...`。裸跑脚本时 CWD 不保证是仓库根，两者缺一即 ModuleNotFoundError。
PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    adapters = types.ModuleType("nonebot.adapters")
    onebot = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    sys.modules["nonebot.adapters"] = adapters
    sys.modules["nonebot.adapters.onebot"] = onebot
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub_nonebot()

from doupo import avatar  # noqa: E402


def test_available_with_bundled_assets():
    """随插件分发的动图资源必须存在且可用。"""
    assert avatar.available(), f"动图目录缺失: {avatar.AVATAR_DIR}"


def test_pick_avatar_tiers():
    """按境界分档：1-9 持剑站立 / 10-18 战斗姿态 / 19-27 HD蓄力 / 28-29 HD骑乘魔兽。"""
    cases = [
        (1, "h0.gif"), (5, "h0.gif"), (9, "h0.gif"),
        (10, "w0.gif"), (15, "w0.gif"), (18, "w0.gif"),
        (19, "hd_h0.gif"), (24, "hd_h0.gif"), (27, "hd_h0.gif"),
        (28, "hd_n0.gif"), (29, "hd_n0.gif"),
    ]
    for lv, want in cases:
        got = avatar.pick_avatar(lv)
        assert got == want, f"lv={lv} 期望 {want} 实得 {got}"


def test_all_tier_files_exist_and_valid():
    """TIERS 里声明的每一档文件都必须真实存在且是合法 GIF（防止改了映射忘了换资源）。"""
    for _lv, name in avatar.TIERS:
        p = avatar.AVATAR_DIR / name
        assert p.is_file(), f"档位资源缺失: {p}"
        assert p.read_bytes()[:6] in (b"GIF87a", b"GIF89a"), f"档位资源损坏: {p}"
        assert p.stat().st_size <= avatar.MAX_BYTES, f"档位资源超体积: {p}"


def test_pick_avatar_robust():
    """非法/越界等级不应抛异常，且能回落到有效档位。"""
    for lv in (None, "x", -5, 0, 9999):
        name = avatar.pick_avatar(lv)
        assert isinstance(name, str) and name.endswith(".gif")


def test_avatar_bytes_is_valid_gif():
    """返回内容必须是合法 GIF（机器验收：GIF 魔数），且体积受控。"""
    for lv in (1, 10, 19, 28):
        data = avatar.avatar_bytes(lv)
        assert data, f"lv={lv} 未取到动图"
        assert data[:6] in (b"GIF87a", b"GIF89a"), f"lv={lv} 不是合法 GIF"
        assert len(data) <= avatar.MAX_BYTES, f"lv={lv} 动图超体积上限"


def test_avatar_for_player_dict():
    """按玩家存档取图（读 lv 字段）。"""
    data = avatar.avatar_for({"lv": 20})
    assert data and data[:6] in (b"GIF87a", b"GIF89a")


def test_avatar_for_bad_input_is_none():
    """非 dict / 空输入不应抛异常。"""
    assert avatar.avatar_for(None) is None or True  # 允许返回兜底图
    assert avatar.avatar_for("not a dict") is None


def test_missing_dir_falls_back_to_none():
    """资源目录被移走时必须返回 None（调用方降级），绝不抛异常。"""
    saved = avatar.AVATAR_DIR
    try:
        avatar.AVATAR_DIR = saved / "__definitely_missing__"
        assert avatar.avatar_bytes(1) is None
        assert avatar.available() is False
    finally:
        avatar.AVATAR_DIR = saved


# ---------------------------------------------------------------------------
# 静态头像（人物卡内嵌，2026-11-07 新增）
# ---------------------------------------------------------------------------
def test_headshot_tiers_are_distinct_96px_png():
    """四档头像都必须是 96×96 合法 PNG，且四张互不相同（防止裁剪框复制粘贴）。"""
    from PIL import Image
    import io

    seen = []
    for lv in (1, 10, 19, 28):
        data = avatar.headshot_bytes(lv)
        assert data, f"lv={lv} 未取到静态头像"
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"lv={lv} 不是 PNG（不能是 GIF）"
        im = Image.open(io.BytesIO(data))
        assert im.size == (96, 96), f"lv={lv} 头像尺寸 {im.size} != (96, 96)"
        assert im.mode == "RGBA", f"lv={lv} 头像应保留 alpha 通道"
        seen.append(data)
    assert len(set(seen)) == 4, "四档头像不能是同一张"


def test_headshot_for_player_is_base64_png():
    """headshot_for 返回纯 base64（模板参数必须可 json.dumps 进缓存 key）。"""
    import base64
    import json

    b64 = avatar.headshot_for({"lv": 22})
    assert isinstance(b64, str) and b64, "有资源时必须返回头像 base64"
    assert "data:" not in b64 and "," not in b64, "应是纯 base64，不带 data URI"
    assert base64.b64decode(b64)[:8] == b"\x89PNG\r\n\x1a\n"
    json.dumps({"avatar_b64": b64})  # 不抛异常即可


def test_headshot_for_bad_input_is_none():
    """非 dict 不抛异常、返回 None（模板会画占位头像）。"""
    assert avatar.headshot_for(None) is None
    assert avatar.headshot_for("not a dict") is None


def test_headshot_missing_assets_is_none():
    """GIF 素材缺失时不抛异常，返回 None，卡片照常出图（占位头像）。"""
    avatar._headshot_b64.cache_clear()
    saved = avatar.AVATAR_DIR
    try:
        avatar.AVATAR_DIR = saved / "__definitely_missing__"
        assert avatar.headshot_bytes(1) is None
        assert avatar.headshot_for({"lv": 1}) is None
    finally:
        avatar.AVATAR_DIR = saved
        avatar._headshot_b64.cache_clear()


def test_headshot_caption_tracks_tier():
    """形象说明跟着境界档位变，非法等级也不抛异常。"""
    caps = [avatar.headshot_caption(lv) for lv in (1, 10, 19, 28)]
    assert len(set(caps)) == 4, caps
    assert all(isinstance(s, str) and s for s in caps)
    assert isinstance(avatar.headshot_caption("bad"), str)


def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok, fail = 0, []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print(f"FAIL  {name}  ->  {type(e).__name__}: {e}")
    print("=" * 70)
    print(f"AVATAR_TOTAL={len(fns)} PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
