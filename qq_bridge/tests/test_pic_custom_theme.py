# -*- coding: utf-8 -*-
"""验收：自定义主题（themes_store / theme.resolve / 编辑器 REST + HTML）。

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_pic_custom_theme.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge.config import Config  # noqa: E402
from src.plugins.qq_bridge.pic import theme as theme_mod  # noqa: E402
from src.plugins.qq_bridge.pic.themes_store import (  # noqa: E402
    PRESETS, THEME_FIELDS, ThemeStoreError, validate_colors, _safe,
)


# ------------------------------------------------------------------ 安全 & 校验
def test_safe_name_normalizes_and_rejects_traversal():
    assert _safe("My-Theme.1") == "my-theme.1"
    assert _safe("  Neon  ") == "neon"
    with pytest.raises(ThemeStoreError):
        _safe("../etc/passwd")
    with pytest.raises(ThemeStoreError):
        _safe("中文名")  # 不在白名单
    with pytest.raises(ThemeStoreError):
        _safe("")


def test_validate_colors_normalizes_to_uppercase():
    colors = {f: "#00e5ff" for f in THEME_FIELDS}
    out = validate_colors(colors)
    assert all(out[f] == "#00E5FF" for f in THEME_FIELDS)


def test_validate_colors_rejects_bad_format_and_defaults_missing():
    # 非法格式仍报错（强校验保底，防止写入无法渲染的色值）
    bad = dict({f: "#000000" for f in THEME_FIELDS})
    bad["bg"] = "blue"
    with pytest.raises(ThemeStoreError, match="bg"):
        validate_colors(bad)
    # 缺失字段回落默认（2026-09-23 link 兼容修复：新增颜色字段不得让老主题报「缺字段」）
    from src.plugins.qq_bridge.pic.theme import NEON_THEME
    bad2 = dict({f: "#000000" for f in THEME_FIELDS})
    del bad2["card"]
    out = validate_colors(bad2)
    assert out["card"] == getattr(NEON_THEME, "card")


# ------------------------------------------------------------------ store CRUD
@pytest.fixture
def store(tmp_path, monkeypatch):
    """临时目录替换 theme_store，避免污染真实数据。"""
    from src.plugins.qq_bridge.pic import themes_store

    s = themes_store.ThemeStore(dir_=str(tmp_path))
    monkeypatch.setattr(themes_store, "theme_store", s)
    return s


def test_store_save_get_list_delete(store):
    colors = {f: "#112233" for f in THEME_FIELDS}
    d = store.save("alpha", colors, "first")
    assert d["name"] == "alpha" and d["description"] == "first"
    assert d["created_at"] > 0 and d["updated_at"] > 0

    fetched = store.get("alpha")
    assert fetched and fetched["colors"] == {f: "#112233" for f in THEME_FIELDS}

    listed = store.list()
    assert [t["name"] for t in listed] == ["alpha"]

    assert store.delete("alpha") is True
    assert store.get("alpha") is None
    assert store.list() == []


def test_store_save_fills_missing_but_rejects_bad_format(store):
    from src.plugins.qq_bridge.pic.theme import NEON_THEME
    # 缺字段：回落默认，保存成功（不再因新增字段让老主题报错）
    d = store.save("partial", {"bg": "#FFFFFF"}, "x")
    assert d["name"] == "partial"
    assert d["colors"]["card"] == getattr(NEON_THEME, "card")
    # 非法格式：仍报错
    with pytest.raises(ThemeStoreError):
        store.save("badfmt", {**{f: "#000000" for f in THEME_FIELDS}, "bg": "blue"}, "x")


def test_store_save_updates_updated_at(store):
    colors = {f: "#000000" for f in THEME_FIELDS}
    d1 = store.save("a", colors)
    d2 = store.save("a", colors)
    assert d2["updated_at"] >= d1["updated_at"]
    assert d2["created_at"] == d1["created_at"], "更新不应改 created_at"


def test_presets_match_builtin_themes():
    """编辑器「预设」按钮一键填色必须与内置主题完全一致。"""
    from src.plugins.qq_bridge.pic.theme import NEON_THEME, LIGHT_THEME

    assert PRESETS["neon"] == {f: getattr(NEON_THEME, f) for f in THEME_FIELDS}
    assert PRESETS["light"] == {f: getattr(LIGHT_THEME, f) for f in THEME_FIELDS}


# ------------------------------------------------------------------ theme.resolve
def test_resolve_picks_custom_theme(store):
    """自定义主题命中后，render 立刻拿到新色板。"""
    colors = {f: "#000000" for f in THEME_FIELDS}
    colors["bg"] = "#FA0001"  # 唯一标识，证明命中
    store.save("weird", colors)
    t = theme_mod.resolve("weird")
    assert t.name == "weird"
    assert t.bg == "#FA0001"


def test_resolve_uses_PIC_CUSTOM_THEME_global(store):
    """Config.PIC_CUSTOM_THEME 非空时，resolve(None) 也走自定义主题。"""
    colors = {f: "#000000" for f in THEME_FIELDS}
    colors["accent"] = "#ABCDEF"
    store.save("brand", colors)

    Config.PIC_CUSTOM_THEME = "brand"
    try:
        t = theme_mod.resolve(None)
        assert t.accent == "#ABCDEF"
    finally:
        Config.PIC_CUSTOM_THEME = ""


def test_resolve_falls_back_to_neon_on_missing():
    """未知名（既非内置也非自定义）一律回落 neon。"""
    t = theme_mod.resolve("does-not-exist-xyz")
    assert t.name == "neon"


def test_resolve_auto_still_works(store):
    """auto 模式不受自定义主题影响。"""
    from datetime import datetime
    from src.plugins.qq_bridge.pic.theme import LIGHT, NEON_THEME, resolve_auto

    assert theme_mod.resolve("auto", now=datetime(2026, 9, 7, 12, 0)).name == LIGHT
    # 确定性：两边用同一注入时间，避免 `resolve_auto()` 取真实系统时间导致
    # 白天跑时返回 light、与注入夜间时间返回的 neon 用 `is` 比较失败（脆弱测试，非逻辑问题）
    assert theme_mod.resolve("auto", now=datetime(2026, 9, 7, 23, 0)) is resolve_auto(now=datetime(2026, 9, 7, 23, 0))


# ------------------------------------------------------------------ editor REST
@pytest.fixture
def client(store, tmp_path, monkeypatch):
    """临时 FastAPI app + TestClient，挂载编辑器全部路由。

    2026-09-24 必修：**必须把「激活态」的两个落盘目标都指向临时目录**。
    此前 activate / deactivate 会直接写生产 `qq_bridge/.env` 的 PIC_CUSTOM_THEME，
    跑一次测试就把用户已应用的主题清成空 —— 这正是「重启服务后主题变回默认」
    的元凶之一（验收脚本污染生产配置，违反 REDUN / FS 纪律）。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.plugins.qq_bridge.pic import editor as editor_mod

    monkeypatch.setattr(editor_mod, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(theme_mod, "_ACTIVE_THEME_PATH",
                        str(tmp_path / "pic_active_theme.json"))
    # 关键：editor.py 是 `from .themes_store import theme_store`（**直接名字绑定**），
    # 只 monkeypatch themes_store.theme_store 替换不了 editor 手里那个对象 ——
    # 用例里的 PUT/DELETE 会打到真实的 data/themes/（实测踩到：凭空多出主题，
    # 主题编辑器首屏加载它，前端验收的「撤销」用例因此误判）。
    monkeypatch.setattr(editor_mod, "theme_store", store)
    app = FastAPI()
    editor_mod.register(app)
    return TestClient(app)


def test_editor_list_includes_presets_and_fields(client):
    r = client.get("/api/pic/themes")
    j = r.json()
    assert r.status_code == 200 and j["ok"]
    assert set(j["presets"]) == {"neon", "light"}
    assert {f["key"] for f in j["fields"]} == set(THEME_FIELDS)


def test_editor_full_crud_cycle(client):
    colors = {f: "#102030" for f in THEME_FIELDS}
    # 创建
    r = client.put("/api/pic/themes/brand1", json={"colors": colors, "description": "测试"})
    assert r.status_code == 200 and r.json()["ok"]
    # 详情
    r = client.get("/api/pic/themes/brand1")
    assert r.json()["theme"]["name"] == "brand1"
    # 激活（写 Config）
    Config.PIC_CUSTOM_THEME = ""
    r = client.post("/api/pic/themes/brand1/activate")
    assert r.status_code == 200 and r.json()["ok"]
    assert Config.PIC_CUSTOM_THEME == "brand1"
    # 列表里能看到激活标记
    r = client.get("/api/pic/themes")
    assert r.json()["active"] == "brand1"
    # 取消激活
    r = client.post("/api/pic/themes/deactivate")
    assert Config.PIC_CUSTOM_THEME == ""
    # 删除
    r = client.delete("/api/pic/themes/brand1")
    assert r.status_code == 200
    r = client.get("/api/pic/themes/brand1")
    assert r.status_code == 404


def test_editor_save_rejects_invalid_colors(client):
    r = client.put("/api/pic/themes/bad", json={"colors": {"bg": "blue"}})
    assert r.status_code == 400
    assert "bg" in r.json()["error"]


def test_editor_activate_rejects_missing_theme(client):
    r = client.post("/api/pic/themes/nonexistent/activate")
    assert r.status_code == 404


def test_editor_preview_returns_png(client):
    colors = {f: "#222222" for f in THEME_FIELDS}
    r = client.post("/api/pic/themes/preview",
                    json={"colors": colors, "template": "text", "body": "预览", "title": "T"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_editor_preview_rejects_bad_colors(client):
    r = client.post("/api/pic/themes/preview", json={"colors": {"bg": "red"}, "body": "x"})
    assert r.status_code == 400


def test_editor_page_renders_html(client):
    r = client.get("/pic/theme-editor")
    assert r.status_code == 200
    assert "QQ 机器人" in r.text and "WCAG" in r.text


# ------------------------- 激活态持久化（2026-09-24：重启后主题不再变回默认）-------------------------
def test_active_theme_file_roundtrip(tmp_path, monkeypatch):
    """兜底文件写入 / 读取 / 清除（清除后不留残值，否则取消激活会被"复活"）。"""
    monkeypatch.setattr(theme_mod, "_ACTIVE_THEME_PATH", str(tmp_path / "a.json"))
    assert theme_mod.read_active_theme() == ""
    assert theme_mod.write_active_theme("CS1") is True
    assert theme_mod.read_active_theme() == "cs1"          # 统一小写，与 Config 侧一致
    assert theme_mod.write_active_theme("") is True
    assert theme_mod.read_active_theme() == ""


def test_active_theme_file_tolerates_corruption(tmp_path, monkeypatch):
    """文件损坏（半截写入 / 手改坏）只能回落空串，绝不抛异常拖垮渲染。"""
    p = tmp_path / "a.json"
    p.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(theme_mod, "_ACTIVE_THEME_PATH", str(p))
    assert theme_mod.read_active_theme() == ""
    p.write_text('{"name": 123}', encoding="utf-8")       # 类型不对
    assert theme_mod.read_active_theme() == ""


def test_config_theme_falls_back_to_file_only_when_env_key_absent(tmp_path, monkeypatch):
    """兜底的触发边界：env 通道**没这个键**才回落；键存在但为空 = 用户主动清空，必须尊重。"""
    monkeypatch.setattr(theme_mod, "_ACTIVE_THEME_PATH", str(tmp_path / "a.json"))
    theme_mod.write_active_theme("brand")
    monkeypatch.setattr(Config, "PIC_CUSTOM_THEME", "")
    monkeypatch.delenv("PIC_CUSTOM_THEME", raising=False)
    assert theme_mod._config_theme() == "brand"

    monkeypatch.setenv("PIC_CUSTOM_THEME", "")
    assert theme_mod._config_theme() == str(getattr(Config, "PIC_THEME", "") or "").strip().lower()


def test_persist_active_theme_writes_both_channels(tmp_path, monkeypatch):
    """双写：.env 原位替换（保留注释与顺序，不重复键）+ 兜底文件同步。"""
    from src.plugins.qq_bridge.pic import editor as editor_mod

    env = tmp_path / ".env"
    env.write_text("# 注释\nPIC_THEME=neon\nOTHER=1\n", encoding="utf-8")
    monkeypatch.setattr(editor_mod, "ENV_PATH", env)
    monkeypatch.setattr(theme_mod, "_ACTIVE_THEME_PATH", str(tmp_path / "a.json"))

    assert editor_mod._persist_active_theme("brand") is True
    txt = env.read_text(encoding="utf-8")
    assert "# 注释" in txt and "OTHER=1" in txt            # 未破坏原有内容
    assert "PIC_CUSTOM_THEME=brand" in txt
    assert theme_mod.read_active_theme() == "brand"

    assert editor_mod._persist_active_theme("other") is True
    txt = env.read_text(encoding="utf-8")
    assert txt.count("PIC_CUSTOM_THEME") == 1              # 原位替换，不新增一行
    assert "PIC_CUSTOM_THEME=other" in txt
    assert theme_mod.read_active_theme() == "other"


def test_persist_active_theme_env_failure_still_saves_file(tmp_path, monkeypatch):
    """.env 写失败（被占用 / 只读）时，兜底文件必须顶上 —— 单通道失败的整条降级链。"""
    from src.plugins.qq_bridge.pic import editor as editor_mod

    monkeypatch.setattr(editor_mod, "ENV_PATH", tmp_path / "no" / "such" / ".env")  # 目录不存在 → 写失败
    monkeypatch.setattr(theme_mod, "_ACTIVE_THEME_PATH", str(tmp_path / "a.json"))
    assert editor_mod._persist_active_theme("brand") is True
    assert theme_mod.read_active_theme() == "brand"


def test_activate_deactivate_keeps_file_in_sync(client):
    """端到端：应用 → 文件有值；取消 → 文件清空（不能让主题在重启后被"复活"）。"""
    client.put("/api/pic/themes/keepme", json={"colors": {f: "#102030" for f in THEME_FIELDS}})
    Config.PIC_CUSTOM_THEME = ""
    r = client.post("/api/pic/themes/keepme/activate")
    assert r.json().get("persisted") is True
    assert theme_mod.read_active_theme() == "keepme"

    client.post("/api/pic/themes/deactivate")
    assert theme_mod.read_active_theme() == ""
    assert theme_mod.active_custom_theme() == ""