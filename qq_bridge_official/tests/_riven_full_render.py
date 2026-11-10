# -*- coding: utf-8 -*-
"""本地渲染夹具：隔离装配（桩掉上级包/外呼层），真实加载 riven_full 数据+模板并出图。

用途：像素级对照参考卡、版式门禁；也可被 check_riven_full_layout.py / test_riven_full.py 复用。
"""
import importlib.util
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # .../master-e6caec37
PKG_ROOT = os.path.join(REPO, "qq_bridge", "src", "plugins")


def _stub_pkg(name):
    m = types.ModuleType(name)
    m.__path__ = []
    sys.modules[name] = m
    return m


def _load_real(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def setup():
    """返回 (数据模块, 模板模块, theme)。"""
    for nm in ("qq_bridge", "qq_bridge.game_tools", "qq_bridge.pic",
               "qq_bridge.pic.templates"):
        if nm not in sys.modules:
            _stub_pkg(nm)

    # 桩 warframe（riven_card / riven_full 只用到少数符号）
    wf = sys.modules.get("qq_bridge.game_tools.warframe")
    if wf is None:
        wf = types.ModuleType("qq_bridge.game_tools.warframe")
        sys.modules["qq_bridge.game_tools.warframe"] = wf
    if not hasattr(wf, "MARKET_BASE"):
        wf.MARKET_BASE = "https://api.warframe.market"
        wf._resolve_riven_weapon = lambda q: ""
        async def _market_get(url, **kw):
            import httpx
            headers = {"User-Agent": "Mozilla/5.0 (qq_bridge-test)",
                       "Accept": "application/json"}
            async with httpx.AsyncClient(headers=headers, follow_redirects=True,
                                        timeout=12.0) as cl:
                r = await cl.get(url)
                r.raise_for_status()
                return r.json()
        wf._market_get = _market_get

    def L(name, rel):
        full = "qq_bridge." + name
        if full in sys.modules:
            return sys.modules[full]
        return _load_real(full, os.path.join(PKG_ROOT, "qq_bridge", rel))

    L("pic.theme", "pic/theme.py")
    L("pic.fonts", "pic/fonts.py")
    L("pic.canvas", "pic/canvas.py")
    rc = L("game_tools.riven_card", "game_tools/riven_card.py")
    L("game_tools.riven_price", "game_tools/riven_price.py")  # riven_full 的相对导入依赖
    # 数据目录：生产布局在插件根上一级（rc 默认 _DATA_DIR 已正确），worktree 布局在
    # 仓库根。部署仓库里可能还残留一份陈旧的 REPO/data 影子目录（实测 2026-11-06：
    # D:\AI\manju_agent\data 是 9-30 的旧快照，缺 riven_disp.json），因此**优先选真正
    # 含 riven_disp.json 的那份**，绝不盲信 REPO/data。仅测试夹具重定向，不改生产代码。
    from pathlib import Path as _P
    _cands = [rc._DATA_DIR,
              _P(REPO) / "qq_bridge" / "data" / "warframe_items",
              _P(REPO) / "data" / "warframe_items"]
    real_data = next((p for p in _cands if (p / "riven_disp.json").is_file()), None)
    if real_data is not None:
        rc._DATA_DIR = _P(real_data)
        rc._RAW.update({"disp": None, "base": None, "zh": None, "weapons": None})
    data_mod = L("game_tools.riven_full", "game_tools/riven_full.py")
    tpl = L("pic.templates.riven_full", "pic/templates/riven_full.py")

    theme = types.SimpleNamespace(width=655, bg="#FFFFFF", name="test")
    return data_mod, tpl, theme


def render(data, width=655, boxes=None):
    data_mod, tpl, theme = setup()
    c = tpl.build(theme, data=data, width=width, boxes=boxes)
    return c.to_bytes()
