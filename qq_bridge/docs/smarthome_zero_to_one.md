# M8 智能家居 从 0 到 1 完全实操教程

> 从「手上什么都没有」开始，一步步把设备准备起来、装上 Home Assistant、接上第一个设备、配好 qq_bridge，直到**能用 QQ 控制真设备**。
> 每一步都给可复制命令 + 验证方法 + 不通怎么排。跟着走完，M8 就是从 demo 到真设备的成品。

---

## 0. 先决定你要投入到哪一档

| 档位 | 你要花 | 能得到什么 | 适合谁 |
|---|---|---|---|
| **A. 零成本试跑** | 一台常开的电脑 + Docker，**不买任何硬件** | 用 HA 虚拟灯把「QQ→HA→设备」整条链路调通 | 先验证软件、再决定买不买设备 |
| **B. 最小真设备** | A + 一个 WiFi 智能插座/灯（几十元） | **真控一盏真灯/插座**，M8 核心成品 | 想尽快看到真效果的 |
| **C. 完整体验** | B + Zigbee 协调器 / 红外转发器 / 摄像头 | 灯/传感器/空调/摄像头全打通 | 要把智能家居玩全的 |

> 强烈建议**先走 A**：不花钱就能确认整条链路和你的代码没问题，再上真设备。真设备出问题时你才知道是"设备/集成"的锅还是代码的锅。

---

## 1. 第 0 步：把 Home Assistant 跑起来

HA 是智能家居的中枢（前面两张图里的第 3 层）。三种装法选一：

### 方式 A：Docker（最快，推荐先用这个）

**Linux：**
```bash
mkdir -p ~/ha_config
docker run -d --name homeassistant --restart=unless-stopped \
  -e TZ=Asia/Shanghai \
  -v ~/ha_config:/config \
  --network=host \
  ghcr.io/home-assistant/home-assistant:stable
```

**Windows（Docker Desktop，不支持 host 网络，改用端口映射）：**
```powershell
docker run -d --name homeassistant --restart=unless-stopped `
  -e TZ=Asia/Shanghai `
  -v D:\ha_config:/config `
  -p 8123:8123 `
  ghcr.io/home-assistant/home-assistant:stable
```

首次启动要等 1–3 分钟（拉镜像 + 初始化）。

### 方式 B：虚拟机跑 HA OS（想长期 7×24 用）
下载 Home Assistant OS 镜像 → VMware/VirtualBox 新建虚拟机装入 → 启动后访问 `http://<虚拟机IP>:8123`。

### 方式 C：树莓派
用 Raspberry Pi Imager 刷 Home Assistant OS 到 TF 卡 → 插电启动 → 访问 `http://<树莓派IP>:8123`。

**验证**：浏览器打开 `http://<装HA那台机的IP>:8123`，能进注册页 = 成功。先注册账号、设时区 `Asia/Shanghai`。

---

## 2. 第 1 步：生成长效令牌（HA 给外人的"钥匙"）

qq_bridge 靠这个令牌调 HA，没它一切免谈：

1. HA 网页左下角点你的**头像**（个人资料）
2. 翻到最底 **「长效访问令牌」**
3. **创建令牌** → 起个名（如 `qq_bridge`）→ 复制那串 `eyJ...`

> ⚠️ 这串就是钥匙，**只走环境变量/.env，别贴聊天、别进 git**。qq_bridge 的 `.env` 已被 `.gitignore` 屏蔽，令牌不会进仓库。

---

## 3. 第 2 步：造第一个"设备"

### 3.A 零成本：加一个虚拟灯（input_boolean，UI 就能加，免硬件）

1. HA → **设置 → 设备与服务 → 帮助程序（Helpers）→ 创建帮助程序 → 「开关（Toggle）」**
2. 名字填 `测试灯`，entity_id 改成 `test_lamp`（得到 `input_boolean.test_lamp`）
3. 保存后去 **开发者工具 → 状态**，搜 `input_boolean.test_lamp`，能看到它、状态 `on`/`off`

这个虚拟灯**完全满足** M8 的开 / 关 / 切换 / 查状态（它有 `turn_on`/`turn_off`/`toggle` 服务和 `state`）。
> 诚实说明：`input_boolean` 没有亮度/色温这些属性，也没有画面——**属性控制、场景、摄像头要等真设备才测**（见验收清单 B/C/E）。A/D/G/H 这些虚拟灯就能验满。

### 3.B 真设备：一个 WiFi 智能插座 / 灯（以涂鸦系为例）

1. 先用厂商 App（如智能生活）让设备连上你家 WiFi（**先让硬件自己能上网**）
2. HA → **设置 → 设备与服务 → 添加集成 → 搜 `Tuya`（或 `LocalTuya`）**
3. 按提示填涂鸦 IoT 平台的 `Access ID` / `Access Secret`（去 iot.tuya.com 申请个开发者项目，选"智能生活"生态）
4. 添加后设备自动出现在 HA，**开发者工具 → 状态**里能看到它，记下 `entity_id`（如 `switch.room_plug`）

> 小米生态同理：添加 **`Xiaomi Miio`** 集成。核心是「设备先自己上网 → HA 加对应集成 → 记下 entity_id」。

---

## 4. 第 3 步：把 HA 接进 qq_bridge（填配置）

配置**全部从环境变量读**（`src/plugins/qq_bridge/config.py` 实证）。两种填法二选一：

### 填法 1：`.env` 文件（推荐，.gitignore 已屏蔽）

在 `qq_bridge/` 目录建/改 `.env`，加：
```env
SMARTHOME_ENABLE=true
SMARTHOME_HA_URL=http://<装HA那台机的IP>:8123
SMARTHOME_HA_TOKEN=eyJ...你的长效令牌
SMARTHOME_DEVICE_WHITELIST=input_boolean.test_lamp
```
> 多个设备用分号分隔：`SMARTHOME_DEVICE_WHITELIST=input_boolean.test_lamp;switch.room_plug`

### 填法 2：系统环境变量（内容同上，`set` 设）

关键项说明（默认值来自 config.py）：

| 配置项 | 作用 | 默认 |
|---|---|---|
| `SMARTHOME_ENABLE` | 总开关，**不开整族没反应** | `false` |
| `SMARTHOME_HA_URL` | HA 地址（不带 /api） | 空 |
| `SMARTHOME_HA_TOKEN` | 长效令牌 | 空 |
| `SMARTHOME_DEVICE_WHITELIST` | 能控的设备，分号分隔 | 空 |
| `SMARTHOME_SENSITIVE` | 高敏（门锁/摄像头），仅主人 | 空 |
| `SMARTHOME_SCENE_MAP` | 场景映射，如 `洗澡模式=script.bath` | 空 |
| `SMARTHOME_WEBHOOK_PORT` | 告警接收端口 | `8090` |

填完**重启 qq_bridge**（配置在启动时读入）。

---

## 5. 第 4 步：跑 live 测试（先证明"QQ↔HA"这段真的通）

这是区分「真连」和「Mock 假连」的一步，**必须真发 HTTP**：
```bash
cd /d/AI/manju_agent/qq_bridge
set HA_TEST_URL=http://<HA_IP>:8123
set HA_TEST_TOKEN=eyJ...你的长效令牌
set HA_TEST_ENTITY=input_boolean.test_lamp
./.venv/Scripts/python.exe -m pytest -m live -v
```
**期望**：`test_live_connectivity_and_token` / `turn_on` / `turn_off` / 读回 / 白名单 / 坏令牌 全绿。
全绿 = 你的 HA 地址、令牌、entity、网络、代码**全部没问题**，只剩 QQ 端确认。

---

## 6. 第 5 步：QQ 端实测（真正"用起来"）

给机器人发（私聊或已配白名单的群）：

```
/ha 开 input_boolean.test_lamp      → 回「已打开」+ HA 里虚拟灯变 on
/ha 状态 input_boolean.test_lamp    → 回「现在是：on」
/ha 切换 input_boolean.test_lamp    → 状态翻转
/ha 关 input_boolean.test_lamp      → 回「已关闭」+ 变 off
```

去 HA **开发者工具 → 状态**刷新，看虚拟灯的 `state` 是不是真的跟着变——**真的变 = M8 成品跑通**。有真设备时把 `input_boolean.test_lamp` 换成 `switch.room_plug` 等，就是控真灯。

更多指令（`/ha 设备`、`/ha set ... brightness=128`、`/ha 场景 ...`、`/ha 看看 <摄像头>`、`/ha clip <摄像头> 5`、`/ha webhook 开`）见 `docs/smarthome_acceptance.md`，逐项验。

---

## 7. 逐步上真设备（从易到难）

链路跑通后，按这个顺序加设备，每加一个就把它的 `entity_id` 补进 `SMARTHOME_DEVICE_WHITELIST`：

| 顺序 | 设备 | 买什么 | HA 集成 | 能验的 M8 能力 |
|---|---|---|---|---|
| 1 | WiFi 插座/灯 | 涂鸦/小米 WiFi 插座（几十元，免网关） | Tuya / LocalTuya / Xiaomi Miio | 真开/真关/切换/状态 |
| 2 | 可调光灯 | 支持亮度的 WiFi 灯 | 同上 | 属性控制 `brightness=128`、越界显式 |
| 3 | 温湿度/门磁/人体 | Zigbee 传感器 + USB 协调器（Sonoff Zigbee 3.0 / SkyConnect） | ZHA 或 Zigbee2MQTT | 查状态、告警上行 |
| 4 | 老式空调/电视 | 红外转发器（博联 RM4 等） | Broadlink | climate 控温、场景 |
| 5 | 摄像头 | 支持 RTSP/ONVIF 的摄像头 | camera / ONVIF / stream | `看看`静帧、`clip`短视频（需 ffmpeg） |

> 场景脚本：HA 里建 **脚本（Script）** 或**自动化（Automation）**，把多个动作打包成一个 `script.xxx`，再填进 `SMARTHOME_SCENE_MAP=洗澡模式=script.bath`，就能 `/ha 场景 洗澡模式` 一键跑。

---

## 8. 排障表（不通先查这）

| 现象 | 最可能原因 | 解法 |
|---|---|---|
| live 测试「连不上」 | HA 地址错 / 防火墙 / 不在同一网段 | 确认 `http://<IP>:8123` 浏览器能开；查 Windows 防火墙放行 8123 |
| HTTP 401/403「令牌无效」 | 令牌错/过期/复制少了 | 回 HA 重新生成长效令牌，整串复制 |
| HTTP 404「没有这个接口/设备」 | entity_id 拼错 / 集成没加 / 设备离线 | 开发者工具→状态里搜到的 entity_id 才是准的 |
| 「不在可控白名单」 | 没加进 `SMARTHOME_DEVICE_WHITELIST` | 分号分隔补进去，重启 qq_bridge |
| QQ 发 `/ha` 没反应 | `SMARTHOME_ENABLE` 还是 false / 没重启 | 设 `true` 并重启；私聊会明确提示"没开" |
| 虚拟灯 turn_on 报错 | 用的 entity 不支持（如 sensor 只能读） | 控制类要用 `input_boolean`/`light`/`switch` 等可控域 |
| clip 报「没装 ffmpeg」 | 本机无 ffmpeg | 装 ffmpeg 加进 PATH；或先用 `看看` 拿静帧 |
| 告警收不到 | webhook 没开 / HA 没配自动化 POST | `/ha webhook 开`；HA 自动化用 REST 命令 POST 到 `http://<qq_bridgeIP>:8090`，curl 自测见 `docs/smarthome_ha_setup.md` |

---

## 9. 一页速查（最小可用配置）

```env
# qq_bridge/.env —— 最小能跑
SMARTHOME_ENABLE=true
SMARTHOME_HA_URL=http://192.168.x.x:8123
SMARTHOME_HA_TOKEN=eyJ...长效令牌
SMARTHOME_DEVICE_WHITELIST=input_boolean.test_lamp
```
```bash
# 验证（真实 HTTP，非 Mock）
set HA_TEST_URL=http://192.168.x.x:8123
set HA_TEST_TOKEN=eyJ...长效令牌
set HA_TEST_ENTITY=input_boolean.test_lamp
./.venv/Scripts/python.exe -m pytest -m live -v
```
```
# QQ 里
/ha 开 input_boolean.test_lamp
/ha 状态 input_boolean.test_lamp
/ha 关 input_boolean.test_lamp
```

---

## 相关文档

- 逐项验收标准：`docs/smarthome_acceptance.md`
- HA 对接/curl 自测/告警自动化：`docs/smarthome_ha_setup.md`
- 全部指令说明：`docs/feature_command_index.md`（M8 段）
- 源码位置：`src/plugins/qq_bridge/__init__.py`（`_handle_ha_command`）、`smarthome/ha.py`、`smarthome/camera.py`
- 真机测试：`tests/test_smarthome_live.py`（`pytest -m live`，默认不跑）
