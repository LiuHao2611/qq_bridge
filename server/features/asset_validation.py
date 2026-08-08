# -*- coding: utf-8 -*-
"""Asset Validation Service —— 资产合法性 / 可用性 / 权限校验（手册 §5.3）。

所有资产调用前必须执行权限校验，校验不通过禁止注入，返回权限错误码（§5.3）。
本地个人版：资产归属于单用户 SINGLE_USER_ID；全局资产(is_global=1)对所有项目可读，
项目私有资产仅限其 project_id 使用；删除/禁用态资产禁止注入。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

# 默认单用户（与 AGENTS.md person_local_only 一致）
SINGLE_USER_ID = "local_user"


class AssetPermissionError(Exception):
    def __init__(self, asset_id, message, code="ASSET_PERMISSION_DENIED"):
        self.asset_id = asset_id
        self.code = code
        self.message = message
        super().__init__(message)


def check_asset_permission(asset_id, project_id=None, user_id=SINGLE_USER_ID):
    """校验资产是否可被当前项目/用户注入（§5.3）。

    返回 dict: {"ok": bool, "reason": str, "asset": {...}}。
      - 资产不存在 → ok=False（ASSET_NOT_FOUND）
      - 资产为项目私有且 project_id 不匹配 → ok=False（ASSET_PERMISSION_DENIED）
      - 资产被禁用（type 为空或 name 为空等非法态）→ ok=False（ASSET_INVALID）
    通过则 ok=True。
    """
    if not asset_id:
        return {"ok": False, "reason": "asset_id 为空", "asset": None}
    try:
        import db
        conn = db.get_conn()
    except Exception:
        return {"ok": False, "reason": "数据库不可用", "asset": None}
    try:
        row = conn.execute(
            "SELECT asset_id, name, type, is_global, project_id, role_type, description "
            "FROM assets WHERE asset_id=?",
            (asset_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "资产不存在", "asset": None}
        d = dict(row)
        if not d.get("name") or not d.get("type"):
            return {"ok": False, "reason": "资产状态非法（缺少名称/类型）", "asset": d}
        if d.get("is_global") != 1 and project_id is not None:
            if d.get("project_id") != project_id:
                return {"ok": False, "reason": "项目私有资产，禁止跨项目注入",
                        "asset": d}
        return {"ok": True, "reason": "", "asset": d}
    except Exception as e:
        return {"ok": False, "reason": "权限校验异常：%s" % e, "asset": None}
    finally:
        try:
            conn.close()
        except Exception:
            pass
