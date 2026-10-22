# 贾维斯管家 · 每个口令的测试结果与验收矩阵

> 生成于 2026-10-22。口径说明（务必先看）：
> - **自动化单测**：跑 `pytest`（默认 `not e2e`），本次实跑 **1040 passed / 0 failed / 10 deselected**。
> - **e2e 真机烟测**：`test_e2e_ws.py`（6 条，需 `pytest -m e2e` 显式跑，默认排除）只覆盖 `/ping` `/help` 与群 @ 分发、Token 鉴权。
> - **命令分发 handler**：经全仓 grep，**没有任何 `handle_*_command` 的直接单测**——各 `/口令` 的分发/权限/参数解析目前靠「底层模块单测 + 真机验收」间接保证（详见各簇「缺口」栏）。底层逻辑（设备控制、记忆、提醒…）都有单测。
> - **🔌 需真机验收**：依赖真实外部环境的口令，单测用 MockTransport/假数据覆盖逻辑，但真链路（真 HA / 真 aria2 / 真 Windows 机 / 真 QQ 号 / 真 GPU 截图）必须真机点一遍。
>
> 列含义：✅ 自动化单测覆盖；🔌 需真机验收；⚠️ 命令分发 handler 无直接单测（建议补）。

---

## 总览（按功能族）

| 功能族 | 口令数 | 自动化单测条数 | 真机验收项 | 缺口 |
|---|---|---|---|---|
| 全局 `/ping` `/help` | 2 | 6（e2e） | — | e2e 默认不收 |
| M1 记忆 `/记忆` | 6+口语 | 33 | — | handler 未直测 |
| M2 提醒 `/提醒` | 4 | 25 | 时间解析 LLM | handler 未直测 |
| M3 情感 `/情感` | 7 | 40 | — | handler 未直测 |
| M4 多模态 `/看图` | 6+口语 | 32 | VLM/ASR/TTS 真引擎 | handler 未直测 |
| M5 群风格/预算 `/风格` `/预算` | 9 | 26 | — | handler 未直测 |
| M6 宿主机 `/pc` | 50+ | 13（monitor/pic_hw） | **真 Windows 机 + GPU/DXGI + ffmpeg** | ⚠️ 命令全族无直测 |
| M7 下载 `/dl` | 10 | 55 | 真 aria2 RPC | handler 未直测 |
| M8 智能家居 `/ha` | 11 | 50（含 FC 注册） | **真 HA 实例** | handler 未直测 |
| M9 运维 `/ops` | 9 | 45 | 真 ssh/WoL/frp 主机 | handler 未直测 |
| 多人格 `/人设` | 5 | 7 | — | handler 未直测 |
| AI 模式 `/ai` | 7 | 107（dispatch/intent/ratelimit） | — | ai_mode 切换无专测 |

---

## 逐口令明细

### 全局
| 口令 | 测试状态 | 证据 / 真机项 |
|---|---|---|
| `/ping` | ✅ e2e | `test_e2e_ws.py::test_ping_returns_pong`（默认 deselected，需 `-m e2e`） |
| `/help` | ✅ e2e | `test_e2e_ws.py::test_help_returns_command_list`（回文生图等） |
| 群消息未 @ | ✅ e2e | `test_group_message_without_at_is_ignored`（不回任何包） |
| 群消息 @ | ✅ e2e | `test_group_at_bot_without_text_replies_help` |
| Token 错配 | ✅ e2e | `test_wrong_token_is_rejected`（403，不静默） |

### M1 记忆 `/记忆`  👥
| 口令 | 测试状态 | 证据 / 说明 |
|---|---|---|
| `/记忆`（看画像） | ✅ 逻辑 | `test_memory_manager.py`(18) `test_memory_store.py`(15) |
| `/记忆 我 职业=…` 记属性 | ✅ 逻辑 | 同上，属性读写单测全覆盖 |
| `/记忆 更正 / 忘记 / 事件 / 清理` | ✅ 逻辑 | 同上 |
| 口语自动提取 | 🔌 需真机 | 走 AI 闲聊后的 `memory_manager.ingest`，逻辑有单测，真实抽取需真 LLM |
| **分发 handler** | ⚠️ 缺口 | `handle_memory` 无直接单测（解析子命令/权限靠底层间接保证） |

### M2 提醒 `/提醒`  👥
| 口令 | 测试状态 | 证据 / 说明 |
|---|---|---|
| 建/列表/取消/改 | ✅ 逻辑 | `test_reminders.py`(25) 全分支 |
| 自然语言建（FC） | ✅ 逻辑 | `remind_create` 工具注册于 `test_tools_registry.py`(15) |
| 时间解析失败诚实报错 | ✅ 逻辑 | `test_reminders.py` 解析失败分支 |
| **分发 handler** | ⚠️ 缺口 | `handle_remind` 无直接单测 |

### M3 情感 `/情感`  👥
| 口令 | 测试状态 | 证据 |
|---|---|---|
| 总览/心情/好感/日程/加/完成/删 | ✅ 逻辑 | `emotion_manager`(7) `emotion_mood`(14) `emotion_store`(19) |
| marker 自动落心情 | ✅ 逻辑 | `test_emotion_mood.py` |
| **分发 handler** | ⚠️ 缺口 | `handle_emotion` 无直接单测 |

### M4 多模态 `/看图`  👥（TTS 🔒）
| 口令 | 测试状态 | 证据 / 真机项 |
|---|---|---|
| 图史/描述/能力/重述 | ✅ 逻辑 | `test_multimodal_vision.py`(14) |
| TTS 语音 | 🔌 需真机 | `test_multimodal_audio.py`(18) 覆盖转写/引擎接入；真 TTS 引擎需真机 |
| 自然语言识图/转写 | 🔌 需真机 | 真实 VLM/ASR 模型 |
| **分发 handler** | ⚠️ 缺口 | `handle_vision`/`_handle_multimodal` 无直接单测 |

### M5 群风格 / 预算  `/风格` `/预算`
| 口令 | 测试状态 | 证据 |
|---|---|---|
| `/风格 学/看/加梗/删梗/忌讳/重置` | ✅ 逻辑 | `test_group_style.py`(16) |
| `/预算` 查看 / `/预算 重置` | ✅ 逻辑 | `test_budget.py`(10) |
| **分发 handler** | ⚠️ 缺口 | `handle_style`/`handle_budget` 无直接单测 |

### M6 宿主机管家 `/pc`  🔒 私聊+主人（**全族最缺单测**）
| 口令 | 测试状态 | 证据 / 真机项 |
|---|---|---|
| 监控/温度/CPU/GPU（底层） | ✅ 逻辑 | `test_monitor.py`(8) 覆盖采集计算 |
| 截图卡片渲染 | ✅ 逻辑 | `test_pic_hw.py`(5) + `test_pic_canvas/templates/integration`(~115) |
| `/pc` 全部子命令（查询/操作/高危/互传/直通车） | 🔌 需真机 | 依赖真实 Windows + DXGI/GPU + 键鼠 + 文件白名单 |
| **命令分发 handler** | ⚠️ 缺口 | **`handle_pc_command` 无任何单测**（高风险项，建议优先补 dispatcher 级用例） |

### M7 下载媒体 `/dl`  👥查 / 🔒写
| 口令 | 测试状态 | 证据 / 真机项 |
|---|---|---|
| 加/列表/暂停/继续/取消/发我/订阅/退订 | ✅ 逻辑 | `download_manager`(21) `download_subscribe`(11) `download_btselect`(10) `download_aria2`(13) |
| 连真实 aria2 RPC | 🔌 需真机 | 真 aria2c `--enable-rpc` |
| **分发 handler** | ⚠️ 缺口 | `handle_dl_command` 无直接单测 |

### M8 智能家居 `/ha`
| 口令 | 测试状态 | 证据 / 真机项 |
|---|---|---|
| 开/关/切换/场景/状态/设备/属性/摄像头/说/webhook | ✅ 逻辑 | `test_smarthome.py`(35：白名单/场景/对话/错误码/相机) `test_smarthome_alert.py`(7：告警路由+冷却) `test_tools_fc.py`(8：ha_control/scene/state/set 注册) |
| 连真实 HA REST / 对话代理 / 摄像头流 | 🔌 需真机 | 真 HA 实例 + 长效令牌；curl 三步自测见 `smarthome_ha_setup.md` |
| **分发 handler** | ⚠️ 缺口 | `handle_ha_command` 无直接单测（权限分层/参数解析靠底层间接保证） |

### M9 运维 `/ops`  🔒
| 口令 | 测试状态 | 证据 / 真机项 |
|---|---|---|
| git/gitlog/gitdiff | ✅ 逻辑 | `test_ops_gitinfo.py`(10) |
| 通知中枢/测试/历史 | ✅ 逻辑 | `test_ops_notify.py`(15) |
| ssh/wol/frp | ✅ 逻辑 | `test_ops_ssh.py`(10) `test_ops_wol_frp.py`(10) |
| 连真实 ssh/WoL/frp 主机 | 🔌 需真机 | 真实白名单主机 |
| **分发 handler** | ⚠️ 缺口 | `handle_ops_command` 无直接单测 |

### 多人格 `/人设`  👥看 / 🔒切
| 口令 | 测试状态 | 证据 |
|---|---|---|
| 列表/当前/看/用/重置 + 切换/未知回落/内置保护 | ✅ 逻辑 | `test_persona.py`(7) |
| **分发 handler** | ⚠️ 缺口 | `handle_persona_command` 无直接单测 |

### AI 模式 `/ai`  👥群管
| 口令 | 测试状态 | 证据 |
|---|---|---|
| 分发/群 @ 规则/限速/意图/任务 | ✅ 逻辑 | `test_dispatcher`(13) `dispatcher_gallery_pending_bypass`(10) `keyword_gate`(6) `intent`(14) `intent_task_cmds`(28) `ratelimit_session_safety`(16) `prompt_truncate`(13) `quick_actions`(5) |
| `/ai` 各子命令（on/off/game/sticker/proactive）切换 | ⚠️ 缺口 | ai_mode 模块无专测，dispatch 间接覆盖 |

---

## 口径总结

1. **全量单测 1040 条全绿**，覆盖各功能族的**底层业务逻辑**（设备控制、记忆、提醒、情感、下载、运维、人设、渲染、意图分发…）。
2. **命令分发 handler 全族无直接单测**——这是当前最大测试缺口：各 `/口令` 的子命令解析、权限门、参数校验、回复文案拼装，目前只能靠真机点测或读源码复核。建议优先给 **M6 `/pc`**（全族 50+ 子命令、副作用最多）和 **M8 `/ha`、M9 `/ops`** 补 dispatcher 级用例。
3. **真链路必须真机验收**：HA / aria2 / Windows 宿主机 / 真 QQ / GPU 截图 / TTS·ASR·VLM 模型，单测用 Mock/假数据验证逻辑，不等于真能跑通。智能家居已给 `curl` 三步自测（无需等真事件）。

> 验收红线不变：任何**假成功 / 静默失败 / 黑屏截图 / 该报错却装没事**都算 bug。
