# -*- coding: utf-8 -*-
"""P3 M1 Skills 技能库（编排层可插拔）。

衔接（手册 §0.3）：
  - 技能执行 = fill_template(意图占位符) → 组装 P0 M4 ProductionIntent → 调 P0 M3 translator.translate
  - 技能定义（intent_template）必须严格符合 ProductionIntent 结构（含 subjects[].role），禁用空壳。

实现逻辑铁律（§0.2）：每个能力函数都遵循"输入→处理→输出"。
红线（§5）：禁空壳（run_skill 必须真调 translator，非返回固定文本）；
          禁 intent_template 不符 ProductionIntent 结构；禁 hash() 生成 ID（用 db.uid）。
"""
import os
import re
import json
import logging

from db import (
    get_conn, fetch_one, fetch_all, insert_row, update_row, delete_row,
    uid, SINGLE_USER_ID,
)
from intent_engine.production_intent import ProductionIntent

LOG = logging.getLogger("skills")

_BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin")

# 手册示例用 "shot_video"（非法），归一为合法任务类型（无创性：不改 P0 VALID_TASK_TYPES）。
_TASK_TYPE_ALIASES = {
    "shot_video": "shot_video_text2video",
}

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def normalize_task_type(task_type: str) -> str:
    """输入：原始 task_type；处理：别名归一；输出：合法 task_type。"""
    return _TASK_TYPE_ALIASES.get(task_type, task_type)


# ----------------------------------------------------------------------
# 占位符填充（输入→处理→输出）
# ----------------------------------------------------------------------
def _fill_placeholders(obj, values):
    """递归把 {{key}} 替换为 values[key]；整串单占位符缺失时落空串（保持字符串类型）。"""
    if isinstance(obj, dict):
        return {k: _fill_placeholders(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill_placeholders(v, values) for v in obj]
    if isinstance(obj, str):
        m = _PLACEHOLDER_RE.fullmatch(obj.strip())
        if m:
            key = m.group(1)
            val = values.get(key)
            return val if val is not None else ""
        return _PLACEHOLDER_RE.sub(
            lambda mm: str(values.get(mm.group(1), mm.group(0))), obj)
    return obj


def fill_template(intent_template: dict, user_input, context: dict = None) -> dict:
    """输入：技能意图模板（含 {{user_input}}/{{subject_asset_id}}/{{style_lighting}} 等占位符）
             + 用户输入 + 上下文（项目/分镜/资产 id 等）
       处理：递归替换占位符 → 归一 task_type
       输出：可直接 ProductionIntent.from_dict 的字典"""
    values = dict(context or {})
    values["user_input"] = user_input if user_input is not None else ""
    filled = _fill_placeholders(intent_template, values)
    filled["task_type"] = normalize_task_type(filled.get("task_type", ""))
    return filled


# ----------------------------------------------------------------------
# 技能加载（输入→处理→输出）
# ----------------------------------------------------------------------
def load_builtin_skills() -> list:
    """输入：无（扫描内置目录）
       处理：读 skills/builtin/*.json
       输出：内置技能列表（每个标记 is_builtin=1）"""
    out = []
    if not os.path.isdir(_BUILTIN_DIR):
        return out
    for fn in sorted(os.listdir(_BUILTIN_DIR)):
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(_BUILTIN_DIR, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["is_builtin"] = 1
            out.append(data)
        except Exception as e:  # 单个坏文件不影响其它内置技能
            LOG.warning("[skills] 内置技能加载失败 %s: %s", fp, e)
    return out


def _row_to_skill(r) -> dict:
    return {
        "skill_id": r["skill_id"],
        "name": r["name"],
        "intent_template": json.loads(r["intent_template"]),
        "params": json.loads(r.get("params") or "{}"),
        "is_builtin": r["is_builtin"],
    }


def query_skills_by_user(user_id) -> list:
    """输入：user_id；处理：查 skills 表；输出：用户技能列表。"""
    conn = get_conn()
    try:
        rows = fetch_all(conn, "SELECT * FROM skills WHERE user_id=?", (user_id,))
        return [_row_to_skill(r) for r in rows]
    finally:
        conn.close()


def load_skills(user_id=None) -> list:
    """输入：user_id；处理：内置 + 用户；输出：技能列表（内置在前，用户在后）。"""
    uid_ = user_id or SINGLE_USER_ID
    return load_builtin_skills() + query_skills_by_user(uid_)


def get_skill(skill_id: str):
    """输入：skill_id；处理：先查内置再查用户表；输出：技能 dict 或 None。"""
    for s in load_builtin_skills():
        if s["skill_id"] == skill_id:
            return s
    conn = get_conn()
    try:
        r = fetch_one(conn, "SELECT * FROM skills WHERE skill_id=?", (skill_id,))
    finally:
        conn.close()
    return _row_to_skill(r) if r else None


# ----------------------------------------------------------------------
# 技能执行（核心，输入→处理→输出）—— 真调 P0 translator
# ----------------------------------------------------------------------
async def run_skill(skill_id: str, user_input, context: dict = None, translator=None) -> dict:
    """输入：技能 id + 用户输入 + 上下文（项目/分镜/资产）
       处理：
         1. 加载技能定义（get_skill）
         2. fill_template → 组装完整 ProductionIntent（严格符合 P0 M4 结构）
         3. 调 P0 意图翻译引擎（translator.translate）—— 真翻译，非返回固定文本
       输出：翻译后的 workflow dict"""
    skill = get_skill(skill_id)
    if not skill:
        raise ValueError("技能不存在: %s" % skill_id)

    # 处理 2：填占位符 → ProductionIntent
    filled = fill_template(skill["intent_template"], user_input, context or {})
    intent = ProductionIntent.from_dict(filled)

    # 处理 3：调 P0 意图翻译引擎（必须走这里）
    if translator is None:
        # 惰性导入，避免模块加载期重依赖翻译引擎（翻译引擎 import 有一次性开销）
        from intent_engine.translator import IntentTranslator
        translator = IntentTranslator()
    workflow = await translator.translate(intent)
    return workflow


# ----------------------------------------------------------------------
# 技能增删改（用户自定义技能持久化）
# ----------------------------------------------------------------------
def create_skill(name: str, intent_template: dict, params: dict = None, user_id=None) -> str:
    """输入：名称 + 意图模板（ProductionIntent 结构）+ 参数；处理：落 skills 表；输出：skill_id。"""
    sid = uid()  # 禁 hash()/random，统一用 db.uid（uuid4）
    conn = get_conn()
    try:
        insert_row(conn, "skills", {
            "skill_id": sid,
            "name": name,
            "intent_template": json.dumps(intent_template, ensure_ascii=False),
            "params": json.dumps(params or {}, ensure_ascii=False),
            "is_builtin": 0,
            "user_id": user_id or SINGLE_USER_ID,
        })
        conn.commit()
        return sid
    finally:
        conn.close()


def update_skill(skill_id: str, name=None, intent_template=None, params=None) -> bool:
    """输入：skill_id + 可选字段；处理：更新 skills 表；输出：是否命中。"""
    data = {}
    if name is not None:
        data["name"] = name
    if intent_template is not None:
        data["intent_template"] = json.dumps(intent_template, ensure_ascii=False)
    if params is not None:
        data["params"] = json.dumps(params, ensure_ascii=False)
    if not data:
        return False
    conn = get_conn()
    try:
        n = update_row(conn, "skills", skill_id, data, pk="skill_id")
        conn.commit()
        return n > 0
    finally:
        conn.close()


def delete_skill(skill_id: str) -> bool:
    """输入：skill_id；处理：按主键删除；输出：是否命中。"""
    conn = get_conn()
    try:
        n = delete_row(conn, "skills", skill_id, pk="skill_id")
        conn.commit()
        return n > 0
    finally:
        conn.close()
