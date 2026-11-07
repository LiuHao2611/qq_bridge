# M8 智能家居 真设备验收清单

> 目标：用**真实设备**把 M8 从「逻辑已测」钉到「成品」。
> 原则（对应用户最高声明）：**禁止降级/兜底、诚实报错、反对半成品**。
> 边界：qq_bridge 只负责到「给 HA 发对 HTTP」为止；HA→真硬件的协议翻译由 HA 集成负责，不在本清单。
> 用法：设备/HA 到位后逐项跑；**A–H 全绿 = M8 成品**，任一红按「失败意味」列定位。

---

## 〇 前置条件（缺一项，后面全红）

| 条件 | 配置项 | 怎么设 |
|---|---|---|
| 总开关打开 | `SMARTHOME_ENABLE` | `true` |
| HA 地址 | `SMARTHOME_HA_URL` | `http://<你的HA_IP>:8123`（不带 /api） |
| 长效令牌 | `SMARTHOME_HA_TOKEN` | HA → 个人资料 → 长效访问令牌（仅走环境变量/.env，勿入仓库） |
| 设备白名单 | `SMARTHOME_DEVICE_WHITELIST` | 分号分隔，如 `light.test;switch.test`（测试设备必须加进来） |
| 高敏设备（可选） | `SMARTHOME_SENSITIVE` | 如 `lock.front;camera.living` |
| 场景映射（可选） | `SMARTHOME_SCENE_MAP` | 如 `洗澡模式=script.bath` |
| HTTP 超时 | `SMARTHOME_HTTP_TIMEOUT` | 默认 `10` |
| 短视频依赖 | 本机 `ffmpeg` | 装进 PATH（不装则 clip 显式报错，不回退静图） |
| 告警接收器 | `SMARTHOME_WEBHOOK_HOST/PORT/TOKEN` | 默认 host `0.0.0.0`；令牌建议配（不配则局域网任何人可触发） |

**前置自检**：设好环境变量后跑 live 测试，连通+令牌必须过——
```
set HA_TEST_URL=http://<你的HA_IP>:8123
set HA_TEST_TOKEN=<长效令牌>
set HA_TEST_ENTITY=light.test
.venv/Scripts/python -m pytest -m live tests/test_smarthome_live.py::test_live_connectivity_and_token -v
```

---

## A 真实设备控制（核心，必须真亮真灭）

| # | QQ 指令 | 期望 | 失败意味 | 对应 live 测试 |
|---|---|---|---|---|
| A1 | `/ha 开 light.test` | QQ 回「已打开」+ **灯真亮** | 灯不亮=HA 侧设备/集成问题；QQ 不回=桥接层问题 | `test_live_turn_on_and_read_back` |
| A2 | `/ha 关 light.test` | QQ 回「已关闭」+ **灯真灭** | 同上 | `test_live_turn_off_and_read_back` |
| A3 | `/ha 切换 switch.test` | 状态翻转一次 | 翻转两次/不变=服务映射问题 | —（人眼确认） |
| A4 | 白名单外 `/ha 开 light.secret` | QQ 明说「不在可控白名单」 | 若真发了请求=双保险失效（严重） | `test_live_whitelist_blocks_real` |

> A1/A2 的判据是**灯物理状态真的变**，不是「QQ 回了成功」就算过——回复成功但灯不动 = 假装成功，按 bug 处理。

---

## B 属性级控制（真验证过的 11 组属性）

| # | QQ 指令 | 期望 | 失败意味 |
|---|---|---|---|
| B1 | `/ha set light.test brightness=128` | 灯变到 128 亮度 | 亮度不变=该域服务没接上 |
| B2 | `/ha set light.test brightness=300` | QQ 明说「超范围 0-255，不替你改数值」，**灯不变** | 若偷偷变 255 = 夹取降级，严重违规 |
| B3 | `/ha set light.test 不存在的属性=1` | QQ 明说「不支持该属性」，不猜服务名 | 若发了个 guessed 服务 = 猜服务名，违规 |
| B4 | `/ha set climate.ac temperature=26`（有空调才测） | 空调目标温度变 26 | — |

对应 live：`test_live_set_attribute`（设 `HA_TEST_ATTR_EXPR=brightness=128`）。

---

## C 场景脚本（本地映射，确定性快路径）

| # | QQ 指令 | 期望 | 失败意味 |
|---|---|---|---|
| C1 | `/ha 场景 洗澡模式` | 执行 `script.bath`，QQ 回「已执行」 | 场景没配=`SMARTHOME_SCENE_MAP` 没填 |
| C2 | `/ha 场景 不存在的模式` | QQ 明说「没有这个场景」+ 列出已配场景 | 静默不回=失败 |

---

## D 查询

| # | QQ 指令 | 期望 | 失败意味 |
|---|---|---|---|
| D1 | `/ha 状态 light.test` | 回当前 `state`（on/off…） | — |
| D2 | `/ha 设备`（群聊） | 只列白名单内设备（防枚举） | 露了白名单外=越权 |
| D3 | `/ha 设备 全部`（仅主人） | 列全量；群友发则被拒 | 群友能看全量=权限洞 |

---

## E 摄像头（可选；高敏，仅主人；短视频需 ffmpeg + stream_source）

| # | QQ 指令 | 期望 | 失败意味 |
|---|---|---|---|
| E1 | `/ha 看看 camera.living`（主人） | QQ 收到当前静帧 | 拿不到=entity 离线/不在 HA |
| E2 | `/ha clip camera.living 5`（主人） | 收到 5 秒 mp4，临时文件用完释放 | ffmpeg 没装→明说，**不回退成静图** |
| E3 | 群友 `/ha 看看 camera.living` | 拒绝「只有主人能看」 | 群友能看=高敏失守 |

---

## F 告警上行（webhook：HA 主动推 → QQ 通知你）

| # | 操作 | 期望 | 失败意味 |
|---|---|---|---|
| F1 | 主人 `/ha webhook 开` | 接收器在 `host:port` 起服务 | 端口占用/起不来=显式报错 |
| F2 | HA 自动化 POST 一个事件 | QQ 收到告警文字（+可选摄像头附图） | 收不到=webhook 路由/token 问题 |
| F3 | 同类告警连发多条 | 冷却去重（`SMARTHOME_ALERT_COOLDOWN` 默认 60s） | 刷屏=冷却失效 |
| F4 | POST 坏 body | 显式 400，服务不崩 | 崩/静默=失败 |

curl 自测步骤见 `docs/smarthome_ha_setup.md`。

---

## G 权限分层（群友 vs 主人）

| # | 场景 | 期望 |
|---|---|---|
| G1 | 群友控白名单内普通设备 | 允许 |
| G2 | 群友控高敏（门锁/摄像头） | 拒绝「只有主人」 |
| G3 | 群友 `/ha 设备 全部` | 拒绝 |
| G4 | 群友 `/ha webhook 开` | 拒绝 |
| G5 | 私聊普通用户控普通设备 | 允许（白名单内） |

---

## H 错误显式化（红线：禁止静默失败/假装成功）

| # | 场景 | 期望 |
|---|---|---|
| H1 | 坏令牌 | QQ 明说「令牌无效或过期」，不假装成功 |
| H2 | 白名单外 entity | 明说「不在可控白名单」 |
| H3 | 不存在的 entity / 404 | 明说「没有这个接口/设备」 |
| H4 | HA 不可达 | 明说「连不上 Home Assistant」 |
| H5 | 属性越界 | 明说超范围，不夹取（同 B2） |

对应 live：`test_live_bad_token_explicit_error`。

---

## 通过判据（机器可判 + 人眼）

1. **机器**：`.venv/Scripts/python -m pytest -m live -v` **全绿**（真实 HTTP 往返，非 Mock）。
2. **人眼**：A–H 每项 QQ 端实际结果符合「期望」列。
3. **判定**：两项都过 = M8 成品；任一红 = 按「失败意味」列分层定位——是**配置/网络/entity 问题**（你在 HA 侧修），还是**代码 bug**（我修）。

---

## 附：最小设备采购参考（怎么把链路凑齐）

| 想要的能力 | 最小硬件 | HA 侧集成 |
|---|---|---|
| 先验满桥接层（免硬件） | 无（HA demo 虚拟灯） | `demo` / `input_boolean` |
| 真开真关一盏灯 | 一个 WiFi 智能插座/灯（涂鸦/小米，直连路由器，免网关） | Tuya/LocalTuya、Xiaomi Miio |
| Zigbee 设备 | USB 协调器（Sonoff Zigbee 3.0 / SkyConnect） | ZHA 或 Zigbee2MQTT |
| 老式空调/电视 | 红外转发器（博联 RM4 等） | Broadlink |
| 摄像头静帧/短视频 | 支持 RTSP/ONVIF 或已接入 HA 的 camera 实体 | camera / stream / ONVIF |

---

## 源码对照（trust but verify）

- 命令分发：`src/plugins/qq_bridge/__init__.py` `_handle_ha_command`（4490–4762）
- FC 工具：同文件 `_ha_control/_ha_scene/_ha_state/_ha_set`（3814–3866）
- HA 客户端：`src/plugins/qq_bridge/smarthome/ha.py`（`call_service`/`set_attribute`/`get_state`/`list_states`/`converse`）
- 摄像头：`src/plugins/qq_bridge/smarthome/camera.py`（`snapshot`/`clip`，无降级回退）
- 真机测试：`tests/test_smarthome_live.py`（`pytest -m live`，默认不跑，缺环境变量整模块 skip）
- 逻辑单测：`tests/test_smarthome.py` + `tests/test_smarthome_alert.py`（Mock，59 条）
