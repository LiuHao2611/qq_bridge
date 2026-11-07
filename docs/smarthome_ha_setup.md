# 智能家居（M8）对接手册 · HA 侧怎么配 · 2026-10-21

> 配套文档：`docs/smarthome_audit.md`（能力审计与修复记录）。
> 本文解决审计里的 **D4 缺口**：此前只有机器人侧配置，没有 HA 侧怎么对接的样例，
> 用户要自己摸索。下面全部是**可直接抄改**的配置与自测命令。

---

## 一、机器人侧（设置页 → 智能家居）

| 配置项 | 必填 | 说明 |
|---|---|---|
| `SMARTHOME_ENABLE` | ✅ | 总开关，默认 false（不开 `/ha` 全部静默，私聊会给提示） |
| `SMARTHOME_HA_URL` | ✅ | HA 地址，如 `http://192.168.1.10:8123`（**不要**带末尾斜杠也行，代码会 rstrip） |
| `SMARTHOME_HA_TOKEN` | ✅ | HA 长效令牌：HA → 左下角个人资料 → 安全 → 创建令牌 |
| `SMARTHOME_DEVICE_WHITELIST` | ✅ | 可控件白名单，分号分隔：`light.living_room;switch.fan;climate.bed;cover.curtain`。**空=全禁** |
| `SMARTHOME_SENSITIVE` | 建议 | 高敏设备（门锁/摄像头），命中后仅主人可操控：`lock.front_door;camera.living` |
| `SMARTHOME_SCENE_MAP` | 可选 | 每行 `中文口语=script.xxx`，命中即直接执行不等 LLM |
| `SMARTHOME_HA_AGENT_ID` | 可选 | 对话代理实体 ID（`/ha 说` 走它），默认 `conversation.home_assistant` |
| `SMARTHOME_WEBHOOK_HOST` | 建议 | 接收器监听地址，**默认 `0.0.0.0`**（HA 在另一台机器必须这样）；HA 与机器人同机可收紧 `127.0.0.1` |
| `SMARTHOME_WEBHOOK_PORT` | 可选 | 默认 `8090`（重启生效） |
| `SMARTHOME_WEBHOOK_TOKEN` | ✅（监听 0.0.0.0 时） | 局域网可达却没令牌 = 任何人都能触发告警 |
| `SMARTHOME_ALERT_COOLDOWN` | 可选 | 同类告警冷却秒数，默认 60（门磁/人体感应连发不刷屏）；0=每条都推 |

**推送目标**复用 M9 的 `OPS_NOTIFY_TARGET`（运维面）：
- 纯数字 `123456` → **私聊**这个 QQ 号；
- 负号或 `g` 前缀 `-987654` / `g987654` → **群**号 987654。

> 历史 bug（已修，2026-10-21）：修前告警一律按群发，填私聊 QQ 号会**发丢**。

---

## 二、HA 侧：反向告警（自动化 → 机器人 → QQ）

### 1) 定义 rest_command（`configuration.yaml`）

```yaml
rest_command:
  qq_bridge_alert:
    url: "http://192.168.1.100:8090/ha/webhook?token=你的告警令牌"
    method: POST
    content_type: "application/json"
    payload: >-
      {"event_type":"{{ event_type }}","entity_id":"{{ entity_id }}",
       "message":"{{ message }}","level":"{{ level }}",
       "camera_entity":"{{ camera_entity }}"}
```

- `192.168.1.100` 换成**跑机器人的那台机器**的局域网 IP（不要填 127.0.0.1，那是 HA 自己）；
- 令牌与 `SMARTHOME_WEBHOOK_TOKEN` **必须一致**（也可改用请求头 `x-webhook-token`）；
- 机器人侧的可选字段：`event_type`/`event`、`entity_id`、`message`、`camera_entity`、`level(info|warn|error)`。
  `message` 有值就原样透传，没值机器人按 `event_type` 转述。

### 2) 写自动化（新版语法，2024.8+）

```yaml
automation:
  - alias: 厨房漏水 → 推 QQ
    triggers:
      - trigger: state
        entity_id: binary_sensor.kitchen_leak
        to: "on"
    actions:
      - action: rest_command.qq_bridge_alert
        data:
          event_type: leak
          entity_id: binary_sensor.kitchen_leak
          message: "厨房漏水了，快去看一眼！"
          level: error

  - alias: 门口有人 → 推 QQ 并附画面
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_motion
        to: "on"
    actions:
      - action: rest_command.qq_bridge_alert
        data:
          event_type: motion
          entity_id: binary_sensor.front_motion
          camera_entity: camera.front
          level: warn
```

旧版 HA（< 2024.8）把 `triggers/actions` 换成 `trigger/action`，
`- trigger: state` 写成 `- platform: state`，`action:` 写成 `service:`。

### 3) 先手工验链路（不用等真事件）

```bash
# 在机器人那台机器上（应回 {"ok":true,...}）
curl -X POST "http://127.0.0.1:8090/ha/webhook?token=你的告警令牌" ^
     -H "Content-Type: application/json" ^
     -d "{\"event_type\":\"leak\",\"message\":\"这是一条测试告警\",\"level\":\"warn\"}"

# 健康检查（应回 {"ok":true,"service":"smarthome-webhook"}）
curl http://127.0.0.1:8090/ha/health

# 再从 HA 那台机器打一次（跨机连通性 + 防火墙）
curl -X POST "http://192.168.1.100:8090/ha/webhook?token=你的告警令牌" -H "Content-Type: application/json" -d "{\"event_type\":\"ping\",\"message\":\"跨机测试\"}"
```
> Windows 的 cmd 用 `^` 换行，PowerShell 用 `` ` ``；Git Bash 用 `\`。
> 跨机打不通 = Windows 防火墙拦了 8090，去「高级安全 Windows Defender 防火墙 → 入站规则」放行该端口。

---

## 三、HA 侧：对话代理（`/ha 说` 走它）

机器人**不自带 LLM**（省钱且与 HA 生态一致）：意图理解与设备匹配全在 HA 侧完成。

1. HA → 设置 → 语音助手（Voice assistants）→ 添加助手；
2. 对话引擎选你已经配好的本地模型（Ollama 集成 / 其他本地对话集成）；
3. 建好后去 **开发者工具 → 状态** 里搜 `conversation.`，找到它的实体 ID
   （形如 `conversation.ollama_zh`、`conversation.home_assistant`）；
4. 把这个 ID 填进 `SMARTHOME_HA_AGENT_ID`。

> `agent_id` 因 HA 版本/集成而异，**以你自己状态页里看到的实体 ID 为准**；
> 填错的表现是 `/ha 说` 报「Home Assistant 没回话」。

---

## 四、摄像头（静帧 / 短视频）

| 能力 | 命令 | HA 侧前提 | 本机前提 |
|---|---|---|---|
| 静帧 | `/ha cam camera.living`（仅主人） | 摄像头实体在线 | 无 |
| 短视频 | `/ha clip camera.living 5`（仅主人，1-30 秒） | 实体属性里要有 `stream_source`：启用 `stream:` 集成并在摄像头集成上开「预加载流」 | 装 **ffmpeg** 并加进 PATH |

ffmpeg（Windows）：`winget install ffmpeg` 或去 gyan.dev 下载 essentials 构建，
解压后把 `bin` 目录加进 PATH，命令行 `ffmpeg -version` 能出版本号即可。
没装 ffmpeg 时 `/ha clip` 会**显式报错**（不会偷偷降级成静图——要视频就给视频，给不了就明说）。

---

## 五、命令速查（2026-10-21 补齐后）

```
/ha 设备                     列可控设备（白名单内，群聊可用）
/ha 设备 全部                 全量清单（仅主人，白名单外的看得到控不了）
/ha set light.living_room brightness=128       属性级控制
/ha set climate.bed temperature=26
/ha set cover.curtain position=50
/ha set climate.bed hvac_mode=cool
/ha set light.living rgb_color=255,120,0
/ha clip camera.living 5     摄像头短视频（仅主人）
/ha cam camera.living        静帧（仅主人）
/ha 开|关|切换 <entity>
/ha 场景 洗澡模式
/ha 状态 light.living_room
/ha 说 把客厅灯打开
/ha webhook 开|关            告警接收器（仅主人）
```

属性白名单（只做真验证过的组合，**不猜服务名**）：
`light.brightness(0-255)`、`light.brightness_pct(0-100)`、`light.color_temp`、
`light.rgb_color`、`climate.temperature(5-35)`、`climate.hvac_mode`、
`cover.position(0-100)`、`fan.percentage(0-100)`、`media_player.volume_level(0-1)`、
`humidifier.humidity(0-100)`、`water_heater.temperature(20-80)`。
**越界会直说，不偷偷改数值**（比如 brightness=300 会报超范围，不会给你设成 255）。

---

## 六、排障表

| 现象 | 原因 | 处理 |
|---|---|---|
| 群聊发 `/ha` 完全没反应 | 模块没开时**群聊刻意静默**（不暴露功能存在） | 私聊发 `/ha`，会明确提示要去开总开关 |
| 「连不上 Home Assistant（ConnectError）」 | URL 错 / HA 没开 / 跨机网络 | 在机器人机器上 `curl <HA_URL>/api/` |
| 「令牌无效或过期（HTTP 401/403）」 | 令牌失效 | HA 重新生成长效令牌 |
| 「不在可控白名单里」 | 没填白名单或填错 entity_id | `/ha 设备 全部`（仅主人）看真实 entity_id 再填 |
| `/ha 设备` 说「没有在线设备」 | 白名单空或 entity 不在线 | 填 `SMARTHOME_DEVICE_WHITELIST` |
| 告警收不到 | 监听地址/端口/防火墙/令牌四选一 | 按第二节第 3 步三步 curl 定位 |
| `/ha 说` 报「没回话」 | 对话代理没配或 `SMARTHOME_HA_AGENT_ID` 填错 | 状态页核对 `conversation.*` 实体 |
| `/ha clip` 报没装 ffmpeg | PATH 没配 | 装 ffmpeg 后**重启机器人进程** |
| `/ha clip` 报「没有 stream_source」 | HA 没启用流 | 加 `stream:` 并给摄像头开预加载流 |
