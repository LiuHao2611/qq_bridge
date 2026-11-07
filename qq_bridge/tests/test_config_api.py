# -*- coding: utf-8 -*-
"""qq_bridge 配置管理接口验收测试（机器判定）。

覆盖：
  - GET /api/qq/config 结构完整、常规/核心标记正确、缺省值取 default
  - PUT /api/qq/config 写回 .env 保留注释与顺序、返回 requires_restart
  - 类型校验（bool / intlist 非法拒绝、合法写入）
不依赖 nonebot / 主项目，单测直接加载 config_api 模块（已去掉相对 import）。
"""
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent  # qq_bridge/
_PLUGIN = _REPO / "src" / "plugins" / "qq_bridge"
sys.path.insert(0, str(_PLUGIN))


def _load():
    import config_api
    return config_api


def test_config_get_structure_and_core_flag(tmp_path, monkeypatch):
    config_api = _load()
    env = tmp_path / ".env"
    env.write_text("# header\nIMAGE_CHANNEL=local\nLOCAL_STEPS=0\nBOT_OWNER_QQ=1743124119\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    view = config_api.get_config_view()
    keys = [it["key"] for g in view["groups"] for it in g["items"]]
    assert "IMAGE_CHANNEL" in keys
    assert "ONEBOT_V11_ACCESS_TOKEN" in keys
    assert "LOCAL_STEPS" in keys

    core = [it["key"] for g in view["groups"] for it in g["items"] if it["core"]]
    assert "IMAGE_CHANNEL" in core          # 核心
    assert "LOCAL_STEPS" not in core        # 常规

    vals = {it["key"]: it["value"] for g in view["groups"] for it in g["items"]}
    # 已配置项取 .env 值
    assert vals["IMAGE_CHANNEL"] == "local"
    # 缺省参数取 schema default（STRICT_WHITELIST 默认 false → bool False）
    assert vals["STRICT_WHITELIST"] in (False, "false")


def test_config_put_preserves_comments_and_split_core_hot(tmp_path, monkeypatch):
    """2026-09-07：拆分 core / hot 后：
      - IMAGE_CHANNEL（core=true）→ 仍写 .env + 在 requires_restart（保持兼容）
      - LOCAL_STEPS（core=false）→ 写 .env + 在 hot_applied（新增字段，不再要求重启）
      - BOT_OWNER_QQ（core=false，新策）→ 写 .env + 在 hot_applied
      - 注释、未改 key、追加不存在 key 行为不变。
    """
    config_api = _load()
    env = tmp_path / ".env"
    env.write_text("# keep\nIMAGE_CHANNEL=local\nLOCAL_STEPS=0\nBOT_OWNER_QQ=1743124119\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    res = config_api.apply_config({"IMAGE_CHANNEL": "online", "LOCAL_STEPS": "20"})
    assert res["errors"] == {}
    # saved 只含本次 PUT 的 key（env 里原 BOT_OWNER_QQ 未在 updates，不算 saved）
    assert set(res["saved"]) == {"IMAGE_CHANNEL", "LOCAL_STEPS"}
    # 关键拆分断言（2026-09-07：core 拆分）
    assert set(res["requires_restart"]) == {"IMAGE_CHANNEL"}  # 仅 core=true
    assert set(res["hot_applied"]) == {"LOCAL_STEPS"}         # 仅 core=false

    text = env.read_text(encoding="utf-8")
    assert "# keep" in text                      # 注释保留
    assert "IMAGE_CHANNEL=online" in text        # 替换
    assert "LOCAL_STEPS=20" in text
    assert "BOT_OWNER_QQ=1743124119" in text     # 未改保留（env 原行不在 formatted 里）

    # 追加 .env 原本不存在、但属于 schema 的 key（PRIVATE_COOLDOWN，core=false）
    res2 = config_api.apply_config({"PRIVATE_COOLDOWN": "15"})
    assert res2["errors"] == {}
    assert "PRIVATE_COOLDOWN" in res2["hot_applied"]
    assert "PRIVATE_COOLDOWN" not in res2["requires_restart"]
    assert "PRIVATE_COOLDOWN=15" in env.read_text(encoding="utf-8")


def test_config_put_validates_bool_and_intlist(tmp_path, monkeypatch):
    config_api = _load()
    env = tmp_path / ".env"
    env.write_text("STRICT_WHITELIST=true\nBOT_OWNER_QQ=1\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    # 非法 bool：整体不写，返回 errors
    r = config_api.apply_config({"STRICT_WHITELIST": "maybe"})
    assert "STRICT_WHITELIST" in r["errors"]
    assert r["saved"] == []

    # 非法 intlist（含非数字）
    r2 = config_api.apply_config({"BOT_OWNER_QQ": "12a,3"})
    assert "BOT_OWNER_QQ" in r2["errors"]

    # 合法：写入
    r3 = config_api.apply_config({"STRICT_WHITELIST": "false", "BOT_OWNER_QQ": "1,2,3"})
    assert r3["errors"] == {}
    text = env.read_text(encoding="utf-8")
    assert "STRICT_WHITELIST=false" in text
    assert "BOT_OWNER_QQ=1,2,3" in text


def test_select_options_are_value_label_objects(tmp_path, monkeypatch):
    """所有 select 字段的 options 必须是 {value,label} 形式（前端 select 渲染依赖此契约）。

    覆盖：HOST/IMAGE_CHANNEL/LOG_LEVEL/LOCAL_STYLE/LOCAL_ORCHESTRATOR/DRIVER。
    校验：
      - 元素是 dict（含 value/label 字段）
      - value 非空字符串
      - label 非空字符串
    """
    config_api = _load()
    env = tmp_path / ".env"
    env.write_text("# empty\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    view = config_api.get_config_view()
    select_keys = {"HOST", "IMAGE_CHANNEL", "LOG_LEVEL", "LOCAL_STYLE",
                   "LOCAL_ORCHESTRATOR", "DRIVER"}
    seen = 0
    for g in view["groups"]:
        for it in g["items"]:
            if it["key"] not in select_keys:
                continue
            opts = it["options"]
            assert isinstance(opts, list) and opts, f"{it['key']} options 应非空列表"
            for o in opts:
                assert isinstance(o, dict), f"{it['key']} option 应为 dict: {o!r}"
                assert isinstance(o.get("value"), str) and o["value"], f"{it['key']} option 缺 value"
                assert isinstance(o.get("label"), str) and o["label"], f"{it['key']} option 缺 label"
            seen += 1
    assert seen == len(select_keys), f"应覆盖 {len(select_keys)} 个 select 字段，实覆盖 {seen}"


def test_proxy_templates_returns_value_label_list(monkeypatch):
    """/api/qq/templates 代理：正常路径返回 ok=True + items[{value,label}]。

    通过 monkeypatch 替换 _fetch_template_options 为假实现，验证：
      - 返回结构为 {ok, items, error}
      - items 透传为 [{value,label}]，与前端 select 契约一致
    """
    import asyncio
    config_api = _load()
    fake = [
        {"value": "tpl_aaa", "label": "tpl_aaa — 基础文生图unet  [基础]"},
        {"value": "tpl_bbb", "label": "tpl_bbb — 二次元  [动漫]"},
    ]
    monkeypatch.setattr(config_api, "_fetch_template_options", lambda: fake)
    res = asyncio.run(config_api.proxy_templates())
    assert res["ok"] is True
    assert res["error"] == ""
    assert res["items"] == fake


def test_proxy_templates_handles_failure(monkeypatch):
    """/api/qq/templates 代理：主项目不可达时返回 ok=False + items=[] + error 非空。"""
    import asyncio
    config_api = _load()
    def boom():
        raise RuntimeError("Connection refused")
    monkeypatch.setattr(config_api, "_fetch_template_options", boom)
    res = asyncio.run(config_api.proxy_templates())
    assert res["ok"] is False
    assert res["items"] == []
    assert "RuntimeError" in res["error"]
    assert "Connection refused" in res["error"]


def test_menu_config_group_and_hot_apply(tmp_path, monkeypatch):
    """2026-09-13：菜单与帮助分组存在、全部 core=false、可热生效。

    覆盖「菜单分主人/一般 + 文案/条目可自定义」的配置面：
      - SCHEMA 里「常规·菜单与帮助」组含标题/页脚/开关/条目共 8 项，且均为常规(core=False)；
      - apply_config 写 .env 后进 hot_applied（改完立即生效，无需重启）。
    """
    config_api = _load()
    env = tmp_path / ".env"
    env.write_text("# empty\n")
    monkeypatch.setattr(config_api, "ENV_PATH", env)

    view = config_api.get_config_view()
    group = [g for g in view["groups"] if g["name"] == "常规·菜单与帮助"]
    assert group, "缺少「常规·菜单与帮助」配置分组"
    items = {it["key"]: it for it in group[0]["items"]}
    for k in ("PIC_MENU_TITLE", "PIC_MENU_SUBTITLE", "PIC_MENU_FOOTER",
              "PIC_MENU_OWNER_SUBTITLE", "PIC_MENU_OWNER_FOOTER",
              "PIC_MENU_SHOW_OWNER", "PIC_MENU_ITEMS", "PIC_MENU_OWNER_ITEMS"):
        assert k in items, f"分组缺少配置项 {k}"
        assert items[k]["core"] is False, f"{k} 必须为常规项（热生效）"

    res = config_api.apply_config({
        "PIC_MENU_TITLE": "本喵的菜单",
        "PIC_MENU_ITEMS": "wand|生图|发一句话;#任务;clock|我的任务|查进度",
        "PIC_MENU_SHOW_OWNER": "false",
    })
    assert res["errors"] == {}
    assert set(res["hot_applied"]) >= {"PIC_MENU_TITLE", "PIC_MENU_ITEMS", "PIC_MENU_SHOW_OWNER"}
    text = env.read_text(encoding="utf-8")
    assert "PIC_MENU_TITLE=本喵的菜单" in text
    assert "PIC_MENU_SHOW_OWNER=false" in text


def test_proxy_templates_drops_placeholder(monkeypatch):
    """/api/qq/templates 代理：主项目不可达时 _fetch_template_options 会塞占位项
    （value=""），代理应过滤掉占位、items 返回空 list。
    """
    import asyncio
    config_api = _load()
    placeholder = [{"value": "", "label": "(主项目不可达，请确认 7788 已启动)"}]
    monkeypatch.setattr(config_api, "_fetch_template_options", lambda: placeholder)
    res = asyncio.run(config_api.proxy_templates())
    # 占位被过滤 → items=[]，但 ok 仍按惯例为 True（表示「请求成功，只是没数据」）
    assert res["items"] == []
