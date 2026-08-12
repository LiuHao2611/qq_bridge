# -*- coding: utf-8 -*-
"""ScriptParserAgent 单元测试（手册 §3.1 / 设计逻辑「剧本结构化解析 Agent」）。

覆盖：
  - 空间站位预规划（_layout_positions / _plan_stage_positions）
  - 资产自动绑定（_auto_bind，相似度阈值 ≥0.85）
  - run() 主流程：happy path（无 project_id 跳过绑定）+ 缺脚本异常（MISSING_SCRIPT）
ai_local 与 rule_engine 通过 sys.modules 注入，避免触发真实 LLM / 校验依赖。
"""
import sys
import types

import pytest

from director.agents.script_parser import ScriptParserAgent
from director.agent_interface import AgentContext
from director.errors import AgentError


def _ctx(project_id="", story="", **params):
    p = {"story": story}
    p.update(params)
    return AgentContext(trace_id="test-trace", project_id=project_id, params=p)


def _agent():
    return ScriptParserAgent()


# ---- 空间站位预规划 -------------------------------------------------------
def test_layout_positions_two():
    out = ScriptParserAgent._layout_positions(2)
    assert len(out) == 2
    assert out[0]["position"] == "left" and out[1]["position"] == "right"


def test_layout_positions_three():
    out = ScriptParserAgent._layout_positions(3)
    assert [o["position"] for o in out] == ["left", "center", "right"]


def test_layout_positions_many():
    out = ScriptParserAgent._layout_positions(5)
    assert out[0]["position"] == "far_left"
    assert out[-1]["position"] == "far_right"
    assert all(o["position"].startswith("center_") for o in out[1:-1])


def test_plan_stage_positions_single_no_blueprint():
    seg = {"shots": [{"refs": ["A"]}]}
    _agent()._plan_stage_positions(seg, [])
    assert seg["shots"][0].get("stage_blueprint") is None


def test_plan_stage_positions_two_blueprint():
    seg = {"shots": [{"refs": ["A", "B"]}]}
    _agent()._plan_stage_positions(seg, [])
    bp = seg["shots"][0]["stage_blueprint"]
    assert bp["type"] == "multi_character"
    assert bp["count"] == 2
    assert bp["positions"][0]["position"] == "left"
    assert bp["positions"][1]["facing"].startswith("left")


# ---- 资产自动绑定（§3.1.5 阈值 ≥0.85） -----------------------------------
def test_auto_bind_exact_match():
    segments = [{
        "segment_id": "s1",
        "scene_description": "周岩在公园散步",
        "shots": [{"description": "周岩走路"}],
    }]
    asset_lib = [{"asset_id": "a1", "name": "周岩", "type": "role"}]
    bound, warnings = _agent()._auto_bind(segments, asset_lib)
    assert "s1" in bound
    ref = bound["s1"][0]
    assert ref["asset_id"] == "a1"
    assert ref["weight"] == 0.85
    assert ref["confidence"] == 1.0
    assert ref["needs_review"] is False
    assert warnings == []


def test_auto_bind_no_match():
    segments = [{
        "segment_id": "s2",
        "scene_description": "林浩在房间",
        "shots": [{"description": "林浩坐着"}],
    }]
    asset_lib = [{"asset_id": "a2", "name": "周岩", "type": "role"}]
    bound, warnings = _agent()._auto_bind(segments, asset_lib)
    assert bound == {}
    assert warnings == []


# ---- run() 主流程 --------------------------------------------------------
def _inject_ai_local():
    fake = types.ModuleType("ai_local")
    fake.split_story_with_llm = lambda story, model_config=None: {
        "segments": [{"segment_id": "", "shots": [{"description": "x"}]}],
        "meta": {},
    }
    saved = sys.modules.get("ai_local")
    sys.modules["ai_local"] = fake
    return saved


def _inject_rule_engine():
    fake = types.ModuleType("director.rule_engine")
    fake.enforce = lambda *a, **k: []
    saved = sys.modules.get("director.rule_engine")
    sys.modules["director.rule_engine"] = fake
    return saved


def test_run_happy_path_no_project():
    saved_ai = _inject_ai_local()
    saved_re = _inject_rule_engine()
    try:
        res = _agent().run(_ctx(story="一段剧本"))
    finally:
        if saved_ai is None:
            sys.modules.pop("ai_local", None)
        else:
            sys.modules["ai_local"] = saved_ai
        if saved_re is None:
            sys.modules.pop("director.rule_engine", None)
        else:
            sys.modules["director.rule_engine"] = saved_re
    assert res.status == "success"
    # 分镜单元缺 segment_id 时应被补全（Atomic Split 定位键）
    assert res.data["segments"][0]["segment_id"]
    # 无 project_id 时不触发资产绑定
    assert res.data["bound_assets"] == {}


def test_run_missing_story_raises():
    with pytest.raises(AgentError):
        _agent().run(_ctx(story=""))
