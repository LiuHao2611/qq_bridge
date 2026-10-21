# 智能家居模块（M8）源码审计 · 2026-10-21

> 方法：**逐文件通读源码 + 交叉 grep 调用点**（不凭文档/交付记录下结论）。
> 范围：`smarthome/`（ha.py / webhook.py / camera.py）、`/ha` 命令族（`__init__.py:4406-4563`）、
> FC 工具注册（`tools/builtins.py:224-277`、`__init__.py:3832-3842`）、配置 schema（`config_api.py:539-570`）。
> 基线：`tests/test_smarthome.py` **26 passed**；`SMARTHOME_ENABLE` 默认 **false**（即默认整模块关闭）。

---

## 一、可直接用（配好 HA 就能真跑）

前置：配置页开 `SMARTHOME_ENABLE` + 填 `SMARTHOME_HA_URL` / `SMARTHOME_HA_TOKEN` + 填 `SMARTHOME_DEVICE_WHITELIST`。

| # | 能力 | 证据 | 状态 |
|---|---|---|---|
| 1 | `/ha 开/关/切换 <entity_id>` 控设备 | `__init__.py:4482-4503` → `ha.call_service` → POST `/api/services/<domain>/<service>` | ✅ 真 REST，白名单双保险 |
| 2 | `/ha 场景 <中文名>` 触发脚本 | `:4461-4479` → `match_scene` 本地映射 → `script.turn_on` | ✅ 确定性快路径，不等 LLM |
| 3 | `/ha 状态 <entity_id>` | `:4506-4517` → `GET /api/states/<e>` | ✅ |
| 4 | `/ha cam <camera entity>` 静帧 | `:4520-4534` → `camera_proxy` JPEG → 发图 | ✅ 仅主人 |
| 5 | `/ha 说 <自然语言>` | `:4537-4550` → HA Conversation Agent | ✅（**依赖 HA 侧已配对话代理**） |
| 6 | 告警接收器 `/ha webhook 开\|关` + 开机自起 | `:4431-4458`、`:471-487` 自动起 | ✅ token 校验/解析/回执齐全 |
| 7 | AI 自然语言控设备（FC 工具） | `builtins.py:224/245/262` + deps 注入 `__init__.py:3838-3840` | ✅ `ha_control`/`ha_scene`/`ha_state` **确已注册** |
| 8 | 语音控设备闭环 | `_handle_voice_message:5176` ASR → 写回 `ev.raw_text` → 走普通消息流（含 FC） | ✅ 链路通 |
| 9 | 安全与错误处理 | `ha.py:150-175 / 236-267` | ✅ 白名单拒绝、高敏仅主人、超时/401/404/网络失败全部显式报错 |

---

## 二、展示品 / 实现了但没真正用上（不是 bug，是没接线）

| # | 项目 | 证据 | 说明 |
|---|---|---|---|
| B1 | **摄像头短视频 `CameraGrab.clip()`** | `camera.py:60-91` 有实现、有 2 个测试；但 grep **无任何命令/工具调用** | `/ha cam` 只调 `snapshot`。「看看客厅」永远只有静图，ffmpeg 拉流能力空转 |
| B2 | **`HAClient.list_states()`** | `ha.py:203-206`；grep 全仓 **零调用** | 拉全量状态的能力没人用 |
| B3 | **HA 管家 prompt `SYSTEM_PROMPT`** | `ha.py:38-44` 定义、`smarthome/__init__.py` 导出、`__init__.py:127` import 成 `HA_SYSTEM_PROMPT` —— **之后再无引用** | 猫娘管家规范文案，是「搬 HAQBot 规范」的留档；HA 对话代理的人设实际在 HA 侧配 |
| B4 | `/ha/health` 健康检查 | `webhook.py:118-120` | 可用但没在任何帮助/文档里告知 |

---

## 三、功能漏洞（真 bug，会导致功能失效）★ 优先修

### C1（高）告警推送目标恒按「群」处理 → 私聊目标会发丢
```python
# __init__.py:4394 —— 自己的 docstring 写「QQ 号=私聊/群号=群」，代码却只走群
ctx = {"user_id": 0, "group_id": int(target) if target.isdigit() else 0,
       "message_type": "group"}
```
发送层判定是 `group_id = int(ctx.get("group_id") or 0); if group_id: 发群 else: 发私聊`（`media/__init__.py:57-59`）。
所以 `OPS_NOTIFY_TARGET` 若填**私聊 QQ 号**，告警会发往「群号 = 该 QQ 号」→ **告警丢失**。
对照 M9 的正确写法：`_ops_notify_send:4351` 用 `{"user_id": tid, "group_id": 0}`。

### C2（高）webhook 只监听 127.0.0.1 → 局域网 HA 连不上
`WebhookReceiver.__init__(host="127.0.0.1")`（`webhook.py:132`），自动启动处（`:475-480`）**未传 host**，只绑回环。
HA 通常跑在另一台机器（NAS / 树莓派），POST 不进来 → 告警链路实际不可用（除非 HA 与机器人同机）。

### C3（中）模块关闭时 `/ha` 完全静默，无任何提示
```python
# __init__.py:4414
if not Config.SMARTHOME_ENABLE:
    return          # 不回一句话
```
用户发 `/ha` 得到零反馈，不知道要先开总开关（对比 `/pc` 至少回「权限不足」）。

### C4（低）webhook 事件无冷却/去重
`webhook.py` 收到即推，没有 M9 的 `OPS_NOTIFY_COOLDOWN` 机制。温度反复越界 / 门磁抖动会刷屏。

---

## 四、缺失（没做的能力）

| # | 缺口 | 影响 |
|---|---|---|
| D1 | **没有「列出我家可控设备」** —— `list_states` 空转，用户必须手打完整 entity_id（`light.living_room`） | 可用性最大短板：不知道家里有哪些可控设备 |
| D2 | **属性级控制缺失** —— `call_service` 支持 `data` 参数，但命令层与 FC 工具都只暴露 `turn_on/off/toggle` | 「灯调暗到 40%」「空调设 26 度」「窗帘开一半」命令层做不到，只能靠 `/ha 说` 走 HA 对话代理 |
| D3 | 摄像头短视频未接线（见 B1） | 要动图的场景没得用 |
| D4 | **没有给用户的 HA 侧配套产物**：HA automation 怎么 POST 到 `/ha/webhook` 的 YAML 样例、对话代理怎么配 | 用户要自己摸索对接 |
| D5 | 无设备状态变化轮询/订阅（只靠 HA 主动推 webhook） | 合理分工，但 HA 侧没配自动化就完全收不到告警 |
| D6 | 无定时/自动化/场景管理（CRUD） | 场景只能读 `SMARTHOME_SCENE_MAP` 静态映射，不能远程增删 |

---

## 五、结论一句话

**核心控制链路（开关 / 场景 / 状态 / 静帧 / 对话代理 / FC+语音闭环）是真实现、可直接用**；
**反向告警链路有两处真漏洞（C1 私聊发丢、C2 只绑回环）**导致「配了 HA 也收不到告警」；
**最该补的是 D1 设备列表与 D2 属性控制**，其次是 C1/C2 两个 bug。

> 边界说明：本审计基于源码静态分析。真链路（连真实 HA 实例）需你提供 HA 地址与令牌才能验证——
> 与 M7 的 aria2 同，属「单测覆盖 + 真机待你环境」。
