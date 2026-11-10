# -*- coding: utf-8 -*-
"""M8 真机验收测试（live marker，默认不跑，需显式 `pytest -m live`）。

⚠️ 这是**真连真实 Home Assistant** 的测试，不是 Mock。
跑前必须提供环境变量（令牌仅存于环境变量 / .env，绝不进仓库或日志）：

    set HA_TEST_URL=http://192.168.x.x:8123      # HA 基地址（不含 /api）
    set HA_TEST_TOKEN=eyJ...                       # 长效访问令牌
    set HA_TEST_ENTITY=light.test_lamp             # 一个测试用灯/开关（会真实开关一次）

可选：
    set HA_TEST_ATTR_ENTITY=light.test_lamp        # 属性控制目标（缺省=HA_TEST_ENTITY）
    set HA_TEST_ATTR_EXPR=brightness=128           # 属性表达式
    set HA_TEST_CONVERSE=1                          # 1=额外测对话代理（需 HA 配 conversation）

安全约定（红线）：
  - 本文件任何地方都不打印 / 记录 token 值；断言信息只含错误类型，不含令牌。
  - token 只来自环境变量；请勿把令牌写进任何源码、配置或日志。
副作用：会真实开关 HA_TEST_ENTITY 一次（开→读→关→读），结束时设备为关。
        请确保该 entity 是无碍的测试设备（别拿主灯/门锁测）。
运行：
    .venv/Scripts/python -m pytest -m live -v
默认 `pytest`（addopts 已含 `not e2e and not live`）不会跑本文件。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.smarthome import HAClient, HaError  # noqa: E402

pytestmark = pytest.mark.live


def _run(coro):
    """同步跑一个协程（HAClient 的方法都是 async）。"""
    return asyncio.run(coro)


# ---- 环境变量（缺则整个模块跳过，绝不连真机） ----
HA_URL = (os.environ.get("HA_TEST_URL", "") or "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TEST_TOKEN", "") or ""
HA_ENTITY = os.environ.get("HA_TEST_ENTITY", "") or ""
HA_ATTR_ENTITY = (os.environ.get("HA_TEST_ATTR_ENTITY", "") or HA_ENTITY)
HA_ATTR_EXPR = os.environ.get("HA_TEST_ATTR_EXPR", "") or ""
HA_CONVERSE = (os.environ.get("HA_TEST_CONVERSE", "") or "") == "1"

if not (HA_URL and HA_TOKEN and HA_ENTITY):
    pytest.skip(
        "真机验收跳过：需设 HA_TEST_URL / HA_TEST_TOKEN / HA_TEST_ENTITY 三个环境变量"
        "（令牌仅走环境变量，勿入仓库）",
        allow_module_level=True,
    )


def _client(**kw) -> HAClient:
    """真实 HAClient（不注入 _client → 走真实 httpx 网络栈）。"""
    wl = {HA_ENTITY, HA_ATTR_ENTITY} - {""}
    return HAClient(base_url=HA_URL, token=HA_TOKEN, whitelist=wl, **kw)


def test_live_connectivity_and_token():
    """地址可达 + 令牌有效：能拉到 states 列表（不读取任何设备状态以外的私密）。"""
    c = _client()
    states = _run(c.list_states())
    assert isinstance(states, list) and len(states) > 0, \
        "list_states 没返回非空列表——HA 地址/令牌可能不对"
    assert any(s.get("entity_id") == HA_ENTITY for s in states), \
        "白名单测试设备 %s 不在 HA 状态列表里——确认 entity_id 拼写/存在" % HA_ENTITY


def test_live_get_state_readable():
    """只读查询：能读回测试设备当前状态（不改动设备）。"""
    c = _client()
    st = _run(c.get_state(HA_ENTITY))
    assert st.get("entity_id") == HA_ENTITY
    assert "state" in st, "返回结构缺少 state 字段：%r" % st


def test_live_turn_on_and_read_back():
    """真实开灯 → 读回 state==on（验证指令真生效，不是假装成功）。"""
    c = _client()
    _run(c.call_service(HA_ENTITY, "turn_on"))
    st = _run(c.get_state(HA_ENTITY))
    assert st.get("state") == "on", "开灯后状态不是 on（读到 %r）" % st.get("state")


def test_live_turn_off_and_read_back():
    """真实关灯 → 读回 state==off（成对恢复，结束时设备为关）。"""
    c = _client()
    _run(c.call_service(HA_ENTITY, "turn_off"))
    st = _run(c.get_state(HA_ENTITY))
    assert st.get("state") == "off", "关灯后状态不是 off（读到 %r）" % st.get("state")


def test_live_whitelist_blocks_real():
    """双保险在真机也生效：白名单不含该设备 → 调服务前即被拒（不发真实请求）。"""
    # 注意 HAClient._check：空 whitelist 视为「全放行」，必须用非空但不含目标的名单
    c = HAClient(base_url=HA_URL, token=HA_TOKEN,
                 whitelist={"light.placeholder_not_real"})
    with pytest.raises(HaError) as ei:
        _run(c.call_service(HA_ENTITY, "turn_on"))
    assert "白名单" in str(ei.value)


def test_live_bad_token_explicit_error():
    """坏令牌在真机也显式报错（不静默）：应 HaError 含「令牌无效」。"""
    c = HAClient(base_url=HA_URL, token="__definitely_not_a_valid_token__",
                 whitelist={HA_ENTITY})
    with pytest.raises(HaError) as ei:
        _run(c.get_state(HA_ENTITY))
    assert "令牌无效" in str(ei.value)


@pytest.mark.skipif(not (HA_ATTR_ENTITY and HA_ATTR_EXPR),
                    reason="未设 HA_TEST_ATTR_ENTITY / HA_TEST_ATTR_EXPR，跳过属性控制真机测试")
def test_live_set_attribute():
    """属性级控制真机验证（如 brightness=128 / temperature=26）。"""
    c = _client()
    _run(c.set_attribute(HA_ATTR_ENTITY, HA_ATTR_EXPR))
    st = _run(c.get_state(HA_ATTR_ENTITY))
    assert st.get("entity_id") == HA_ATTR_ENTITY


@pytest.mark.skipif(not HA_CONVERSE, reason="未设 HA_TEST_CONVERSE=1，跳过对话代理真机测试")
def test_live_converse():
    """对话代理真机验证：发一句自然语言能回话术（需 HA 配 conversation agent）。"""
    c = _client()
    out = _run(c.converse("把测试设备状态告诉我"))
    assert out.get("ok") is True and out.get("speech"), \
        "converse 没回话术——HA 对话代理可能未配好或离线"
