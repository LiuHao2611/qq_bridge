#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""禁止 NapCat 自动重试登录（容器内运行，改的是运行时解出来的 napcat.mjs）。

## 为什么必须做这件事

2026-10-29 实测：容器起来后没扫码，日志每 2 分钟吐一次

    13:31:34 [Core] [Login] Login Error,ErrType: 1  ErrCode: 3
    13:33:36 同上
    13:35:38 同上

根因在 napcat.mjs 的 `onQRCodeSessionFailed`：二维码会话超时（QQ 侧约 2 分钟）
→ 立刻 `getQRCodePicture()` 重新生成 → 新码再超时 → 再生成，无限循环。
从腾讯服务器看，这就是「这个号在不停发起登录请求」，正是项目铁律里
「频繁重登 = 封号直接根因」的那一类行为（3858741376 就是死在这上面）。

还有第二条更危险的路径：`onLoginConnected`（登录服务重连）里，
只要 `!isLogined` 就自动 `getQRCodePicture()` ——
意思是被踢下线 / 内核重连之后，它会**自己重新发起登录**。

两条都不做人工确认，全部干掉。

## 改哪两处（只动这两行，其余原样）

1. onQRCodeSessionFailed 里 `... , t.getQRCodePicture()` → 只写错误信息 + 告警日志。
   效果：二维码过期后就地停住，不再自动换新码。

2. onLoginConnected 的 `if (!o.isLogined) { ... const u = t.getQRCodePicture(); ... }`
   → 只记「自动重新取码已禁用」，不再取码。
   效果：被踢/重连之后不会自动重新登录，必须人工决定。

## 刻意不动的地方

- `setRefreshQRCodeCallback`（WebUI 手动刷新二维码 / scripts/qq_login_qr.py）——保留，
  否则人工都没法登录了。
- 首次启动流程 `t2e`（含 `-q` 快速登录、密码回退、各类失败后回落到二维码）——保留。
  那是「一次启动一次尝试」，不是循环；禁掉会导致根本没法扫码登录。

## 失败策略

找不到预期代码 = NapCat 版本变了、补丁失效 = 自动重登会悄悄复活。
按项目铁律「账号安全 >> 服务可用性」，此时**直接退出非 0，让容器起不来**，
而不是静默跳过。
"""
from __future__ import annotations

import os
import sys

# 允许用环境变量覆盖，是为了能在宿主机上对导出的 napcat.mjs 副本做同样的补丁、
# 跑同一套断言（scripts/verify_no_autologin.py 会这么用），而不是另写一份逻辑。
TARGET = os.environ.get("NAPCAT_MJS", "/app/napcat/napcat.mjs")
CALL = "getQRCodePicture()"

# ---- 补丁 1：二维码过期后不再自动换新码 ----
OLD_1 = (
    'u === 1 && l === 3 && (pe.setQQLoginError("二维码已过期，请刷新"), '
    't.getQRCodePicture())'
)
NEW_1 = (
    'u === 1 && l === 3 && (pe.setQQLoginError("二维码已过期（自动刷新已禁用，请手动刷新）"), '
    'e.logWarn("[qq_bridge] 二维码已过期：自动刷新已禁用，等待人工刷新"))'
)

# ---- 补丁 2：登录服务重连后不再自动重新取码 ----
OLD_2 = (
    '        pe.setQQLoginPhase("generating_qrcode");\n'
    '        const u = t.getQRCodePicture();\n'
    '        e.logWarn("[Core] [Login] 登录服务重连完成，正在获取二维码", '
    '{ requested: u, msfStatus: t.getMsfStatus() });'
)
NEW_2 = (
    '        pe.setQQLoginError("登录服务已重连，自动重新取码已禁用，请手动刷新二维码");\n'
    '        e.logWarn("[qq_bridge] 登录服务重连完成：自动重新取码已禁用，等待人工刷新");'
)

PATCHES = [("二维码过期不再自动换码", OLD_1, NEW_1), ("重连后不再自动重新取码", OLD_2, NEW_2)]


def main() -> int:
    try:
        with open(TARGET, "r", encoding="utf-8") as f:
            src = f.read()
    except FileNotFoundError:
        # 还没解压出来：不是失败，静默放过（正常流程里不会发生）
        print("[no-autologin] 跳过：%s 不存在" % TARGET, flush=True)
        return 0

    before = src.count(CALL)
    out = src
    applied, skipped = [], []

    for name, old, new in PATCHES:
        if new in out:
            skipped.append(name)
            continue
        if old not in out:
            print(
                "[no-autologin][FAIL] 找不到补丁目标：%s\n"
                "  NapCat 版本可能变了，补丁失效 = 自动重登会复活。\n"
                "  拒绝启动，请人工核对该版本 napcat.mjs 的登录流程。" % name,
                flush=True,
            )
            return 1
        out = out.replace(old, new, 1)
        applied.append(name)

    after = out.count(CALL)

    if applied:
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(out)
        print(
            "[no-autologin] 已禁用自动重试登录：%s（getQRCodePicture 调用 %d → %d）"
            % ("、".join(applied), before, after),
            flush=True,
        )
    else:
        print(
            "[no-autologin] 补丁已存在，跳过（%s）；getQRCodePicture 调用 %d 处（均为人工/首次路径）"
            % ("、".join(skipped), after),
            flush=True,
        )

    # 收尾复核：两条自动路径上都不许再出现取码调用
    for name, _old, new in PATCHES:
        if new not in out:
            print("[no-autologin][FAIL] 复核未通过：%s" % name, flush=True)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
