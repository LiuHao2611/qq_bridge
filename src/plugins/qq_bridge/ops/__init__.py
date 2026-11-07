# -*- coding: utf-8 -*-
"""ops —— M9 运维面与通知中枢（2026-10-20）。

组成：
  gitinfo.py   开发机 git 仓库状态（只读子命令白名单，禁一切写操作）
  ssh.py       远程主机 SSH 命令白名单执行（paramiko；白名单+显式超时+审计）
  wol.py       Wake-on-LAN 魔术包唤醒（主机白名单 + 标准 WoL 包构造）
  frp.py       frp/nps 内网穿透隧道状态查询（frpc admin API /api/status）
  notify.py    通知中枢（事件统一入口 + LLM 人格化转述 + 限流 + 目标路由）
  watchdog.py  开发机仓库巡检循环（脏改动/未推送/自检命令 → 通知中枢）
  connwatch.py  连接看门狗（60s 探真实账号态 → 状态变化才告警；**只喊不动手**）

安全总原则（比 dsh-qq-remote 的 /exec 更严，dev_plan §五：M9 远程 shell
严格走「仅主人 + 二次确认 + 审计」）：
  - SSH 主机白名单 + 命令白名单 + 危险模式双保险 + 审计日志；
  - 调用方（/ops 命令族）落实仅主人 + 「确认」二次确认；
  - 所有外部调用显式超时（CLOSE-05），失败显式上抛（CLOSE-02）。

依赖方向：本包只依赖 ..config，不 import nonebot / 不 import 主包——
通知发送与人格化转述全部经回调注入（hub.send/hub.rephrase/
watchdog.on_event），可裸单测。
"""
from .gitinfo import GitInfo, GitInfoError, git_info, validate_git_args
from .ssh import (SshError, SshHost, SshRunner, check_command, parse_hosts,
                  ssh_runner)
from .wol import WolError, WolHost, WolSender, build_magic_packet, wol_sender
from .frp import FrpError, FrpStatus, frp_status
from .notify import (LEVEL_ERROR, LEVEL_INFO, LEVEL_WARN, NotificationHub,
                     NotifyEvent, notify_hub)
from .watchdog import RepoWatchdog, repo_watchdog
from .connwatch import (STATE_ACCT_FAIL, STATE_OK, STATE_UNKNOWN, STATE_WS_DOWN,
                        ConnWatchdog, conn_watchdog)

__all__ = [
    "GitInfo", "GitInfoError", "git_info", "validate_git_args",
    "SshRunner", "SshHost", "SshError", "ssh_runner", "check_command",
    "parse_hosts",
    "WolSender", "WolHost", "WolError", "wol_sender", "build_magic_packet",
    "FrpStatus", "FrpError", "frp_status",
    "NotificationHub", "NotifyEvent", "notify_hub",
    "LEVEL_INFO", "LEVEL_WARN", "LEVEL_ERROR",
    "RepoWatchdog", "repo_watchdog",
    "ConnWatchdog", "conn_watchdog",
    "STATE_OK", "STATE_WS_DOWN", "STATE_ACCT_FAIL", "STATE_UNKNOWN",
]
