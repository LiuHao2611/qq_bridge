# QQ 官方机器人接入方案（双轨并行版）

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 日期 | 2026-09-11 |
| 状态 | 待评审（实施前需用户确认） |
| 范围 | `qq_bridge/`（与主项目物理隔离，不改变隔离红线） |
| 核心决策 | **保留 NapCat 现有链路与页面，新增 QQ 官方机器人通道，双轨并行** |
| 依据 | QQ 机器人官方文档（2026-08/09 版）、NapCatQQ GitHub Issues、本机日志实证 |

---

## 0. TL;DR

1. 现网 NapCat 故障已定位：**不是 WS 断连，是 QQNT 内核卡死**，官方不修（[#869](https://github.com/NapNeko/NapCatQQ/issues/869) Closed、[#929](https://github.com/NapNeko/NapCatQQ/issues/929) not planned）。
2. 官方机器人：**免费**、**支持本地分片上传（不需要公网 URL）**、**支持 WebSocket 单机接入（不需要公网服务器）**、个人认证可公开 + 500 群。
3. 实施策略：**不拆 NapCat**。抽出「通道抽象层」，NapCat 与官方两个通道并存，业务逻辑（意图/队列/限流/配额/自绘图/监控）100% 复用。
4. 主要工作量在**协议适配层 + 富媒体上传**，业务逻辑几乎不动。

---

## 1. 背景

### 1.1 现网故障实证（2026-09-11 本机日志）

| 时间 | 事件 |
|---|---|
| 12:58:47 | bridge 与 QQ 进程启动；`自动快速登录失败：用户身份已失效` → 扫码重登 |
| 12:59:22 | `connector_attached self_id=3858741376` |
| 15:11:24 | 最后一次健康行为（发图成功） |
| 15:14:28 | 起，`get_group_member_info` 开始 `Timeout: NTEvent ... NodeIKernelProfileService/fetchUserDetailInfo` |
| 15:52:00 | 连 `send_private_msg` 也失败：`EventChecker Failed: NodeIKernelMsgService/sendMsg` |
| 15:14→16:05 | 持续失败 **2157 次**（约 16 次/分钟），**无一恢复** |
| 全程 | `/api/qq/status` 仍返回 `online:true, last_disconnected_at:0` |

**三个叠加原因**：

1. **底层**：QQNT 内核长时间运行后消息队列/资源异常（[#869](https://github.com/NapNeko/NapCatQQ/issues/869) 同型，报错字符串逐字一致）。
2. **加速**：`QQChatPanel.vue:500` 每 8 秒 `refreshAll()` → `/api/qq/groups` → 对 2 个群各发 `get_group_info` + `get_group_member_info(no_cache=True)` = 每 8 秒 4 次强制走内核的调用。
3. **不愈**：`watchdog.ps1:113` 只用「8080 是否有 ESTABLISHED」判定在线，TCP 还在就判在线 → 从不触发自动恢复。

### 1.2 官方对根因的定论

- [#869](https://github.com/NapNeko/NapCatQQ/issues/869)（维护者 MliKiowa 领走，已 Closed）：「长时间运行会无法发送消息…**只能重启恢复正常**」，无代码修复。
- [#929](https://github.com/NapNeko/NapCatQQ/issues/929)：同款报错，**Closed as not planned**。
- 实测（本次）：内核已死 1 小时，NapCat WebUI 仍返回 `isLogin:true / online:true` → 登录态类探针**无法**识别该故障。

### 1.3 为什么选官方路线

| 维度 | NapCat（个人号挂机） | 官方开放平台机器人 |
|---|---|---|
| 身份 | 一个普通 QQ 号的登录态 | 独立 APPID，平台身份 |
| 稳定性 | 挂机掉线是命门 | 平台级，无挂机概念 |
| 合规 | 注入/逆向，违反 QQ ToS | 官方接口，合法 |
| 大群案例 | 无同等规模公开案例 | 《一念成仙》数万 DAU、2021 运营至今 |
| 成本 | 免费 | 免费（详见 §4.6） |

### 1.4 决策：双轨并行

**保留** NapCat 通道（含现有一切功能与前端页面），**新增**官方通道。理由：
- 官方通道有硬约束（被动回复 5 分钟、主动消息需群主开开关、不能主动加群），需实测验证体验；
- 两通道能力不完全等价（NapCat 能做的部分官方做不了，反之亦然）；
- 平滑切换，随时可回滚。

---

## 2. 目标与非目标

### 目标
1. 同一进程内同时挂载 OneBot V11（NapCat）与 QQ 官方两个适配器。
2. 业务逻辑零重复：意图路由、任务编排、队列、限流、配额、安全、自绘图、监控、历史画廊全部复用。
3. 官方通道支持：群聊 @、群聊全量消息、私聊、文本、Markdown、图片（本地分片上传）、视频。
4. 前端页面保留，增加通道标识（区分消息来自哪个通道）。
5. 全程可回滚：任一阶段失败不影响 NapCat 现有能力。

### 非目标
1. 不删除/改造 NapCat 链路（本阶段）。
2. 不实现官方特有的 Ark 模板、内联键盘、互动召回（后续可选）。
3. 不迁移历史会话数据到 openid（新旧通道历史分表存储）。
4. 不改动主项目（7788/8099）任何代码。

---

## 3. 架构设计

### 3.1 现状架构

```
NapCat ──(反向 WS:8080)──> nonebot(OneBot V11 Adapter)
                                  │
                                  ├─ dispatcher.normalize() → ctx
                                  ├─ safety → ratelimit → intent.route()
                                  ├─ queue → taskmgr ──HTTP──> 主项目 7788
                                  └─ media.send_*() ──> connector.call_action()
```

耦合点（协议相关，必须抽象）：
- `__init__.py:26` 顶层 `from nonebot.adapters.onebot.v11 import Bot, MessageEvent`
- `media/__init__.py:25` `from nonebot.adapters.onebot.v11 import MessageSegment`
- `media._target()` 硬编码 `send_group_msg` / `send_private_msg`
- `session.Session.user_id: int`
- `dispatcher.normalize()` 读 `event.user_id` / `event.group_id`
- `connector` 是 OneBot 单例

### 3.2 目标架构

```
NapCat ──WS:8080──> OneBot V11 Adapter ─┐
                                        ├─> Channel 抽象层 ─> 统一业务逻辑 ─> 7788
QQ官方 ──WS(出站)──> QQ Adapter ─────────┘        │
                                                 └─> Channel.send_*()（各协议自实现）
```

### 3.3 通道抽象层（新增）

```
src/plugins/qq_bridge/channel/
├── __init__.py      # get_channel(name) / registry
├── base.py          # Channel 抽象基类 + Outgoing 数据类
├── napcat.py        # 现有 OneBot 实现（从 connector/media 迁移）
└── official.py      # QQ 官方实现
```

**基类契约**（`base.py`）：

```python
@dataclass
class Outgoing:
    text: str = ""              # 文本内容
    images: list[bytes] = []    # 内存图片（自绘图/表情包）
    image_urls: list[str] = []  # 图片 URL（主项目产出）
    video: str = ""             # 视频 URL/路径
    at_user: bool = False       # 是否 @发起人

class Channel(ABC):
    name: str                                   # "napcat" | "official"
    async def normalize(self, event) -> dict | None    # 事件 → ctx
    async def send(self, ctx: dict, out: Outgoing) -> dict   # 统一发送
    async def upload_image(self, ctx: dict, data: bytes) -> str  # 返回可发送句柄
```

### 3.4 ctx 契约 v2（向后兼容）

现有 `ctx = {"user_id": int, "group_id": int}` **保留**，新增字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `channel` | str | `napcat` / `official`，**必填** |
| `user_id` | int | NapCat 为 QQ 号；官方通道填 `0` |
| `group_id` | int | NapCat 为群号；官方通道填 `0` |
| `user_key` | str | **统一身份键**，NapCat=`u<QQ号>`，官方=`openid` |
| `group_key` | str | **统一会话键**，NapCat=`g<群号>`，官方=`group_openid` |
| `reply_ref` | dict | 被动回复所需引用（官方：`{"msg_id":..., "msg_seq":1}`） |
| `self_id` | str | 机器人自身标识 |

> 业务层（session/ratelimit/queue/history）统一改用 `user_key` / `group_key`，不再直接依赖 int 型 ID。

---

## 4. 官方平台能力与限制（已核对官方文档）

### 4.1 接入方式

| 方式 | 要求 | 适用 |
|---|---|---|
| **WebSocket**（推荐） | 我们主动连出去，**不依赖公网服务器** | 个人单机 ← 我们选这个 |
| Webhook | 需 HTTPS 公网回调地址 | 公开服务 |

来源：[接入指南 §8.1](https://bot.q.qq.com/wiki/bot_new_product-intro/)

### 4.2 消息类型

| msg_type | 类型 | 内容字段 |
|---|---|---|
| 0 | 文本 | `content` |
| 2 | Markdown | `markdown.content`（需申请模板权限） |
| 7 | 富媒体 | `media.file_info` |

> 已知坑：`msg_type=7` 时 `content` 需填一个空格 `" "`。

### 4.3 频控（群聊）

| 维度 | 限制 |
|---|---|
| 接口层 | 100 QPS（**包括主动、被动等所有消息类型**） |
| Bot 维度（**全局，跨所有群/单聊**） | 60 条/分钟（个人/企业认证），未认证 30 |
| 单群维度 | 20 条/分钟，**每天最多 1000 条** |
| 被动回复 | **5 分钟**有效，每条用户消息最多回 **5 次** |
| 单聊 | 被动 60 分钟/4 次；主动 10 qps、20 qpm、**每个好友每天 1000 条** |

**计数口径**：按「条」计，**不区分文本/图片/语音/视频**，一图 = 一条。

### 4.4 富媒体

- 格式：图片 jpg/png/gif/webp/bmp；视频 mp4；语音 silk/mp3/wav/ogg
- 大小：图片/语音 软 20MB（超了降级为文件卡片）、硬 200MB；视频 软 30MB
- 上传方式：**分片上传（本地文件，推荐，无需公网 CDN）** / URL 上传（需公网）
- `file_info` 有 TTL，过期需重传；**单聊与群聊上传接口不互通**
- `srv_send_msg=true` 可上传即发送，但**占用主动消息频次**

来源：[富媒体消息概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/rich-media.html)

### 4.5 硬约束与坑（实施必须遵守）

1. **群主动消息需群主开开关**：「机器人主动在群聊内发言」，否则 `40034105`。我们出图慢，必然走主动消息 → **硬前提**。
2. **消息里不能带未配置的 URL**：需在后台「开发设置-消息URL配置」预配置，否则 `40054010`。→ **图片必须走 file_info，不能贴 7788 图链**。
3. **被动回复 5 分钟超时**：出图超时必须降级为主动消息。
4. **IP 白名单**：仅支持单个 IPv4（不支持 CIDR），最多 50 条。家用动态公网 IP 变化后会调不通 OpenAPI。
5. **机器人不能主动加群**：需群主拉入。
6. **openid 不是 QQ 号**：用户/群标识全变，历史数据需映射。
7. **服务范围**：未认证=仅管理员；**个人认证=可公开、进群上限 500**；企业认证=全场景。
8. **沙箱环境不受频控**：开发阶段用 `QQ_IS_SANDBOX=true`。

### 4.6 费用

- 官方机器人通道**免费**（官方文档无收费/计费章节；多个 2026-03 来源确认「创建流程完全免费」）。
- 成本在服务器与大模型 API —— 本项目已具备，**零新增成本**。
- 在线生图额度仍走现有火山/硅基 key，与通道无关。

---

## 5. 接入准备（人工操作步骤）

| # | 操作 | 预期结果 |
|---|---|---|
| 1 | 注册 [QQ 开放平台](https://q.qq.com/)，走**个人入口**完成主体认证 | 认证状态=已认证（个人） |
| 2 | 创建机器人（头像/昵称/简介），AI 服务选「稍后连接」 | 拿到 **AppID + AppSecret** |
| 3 | 开发基础设置 → 事件订阅选 **WebSocket** | 连接方式=WebSocket |
| 4 | Intent 勾选 `c2c_group_at_messages`、`group_members` | 能收到群 @ 与群消息 |
| 5 | IP 白名单填本机出口 IPv4 | OpenAPI 可调用 |
| 6 | 在 [QQ 消息列表创建沙箱群](https://q.qq.com/wiki/FAQ/robot/) 并把机器人拉进去 | 沙箱群可对话，**不受频控** |
| 7 | 目标群：群主把机器人拉入，并**打开「机器人主动在群聊内发言」** | 主动消息可发送 |

---

## 6. 详细设计

### 6.1 文件清单

**新增**

| 文件 | 职责 |
|---|---|
| `channel/__init__.py` | 通道注册与获取 |
| `channel/base.py` | `Channel` 抽象基类 + `Outgoing` |
| `channel/napcat.py` | OneBot V11 实现（迁移现有 connector/media 逻辑） |
| `channel/official.py` | QQ 官方实现 |
| `official/__init__.py` | 官方 API 封装（鉴权、发消息、上传） |
| `official/upload.py` | 富媒体分片上传 |
| `official/limiter.py` | 主动消息频控与日额度计数 |
| `tests/test_channel_official.py` | 官方通道单元测试 |
| `tests/test_official_upload.py` | 分片上传测试 |

**修改**

| 文件 | 改动 |
|---|---|
| `bot.py` | 额外 `register_adapter(QQAdapter)`；driver 支持 `~httpx+~websockets` |
| `__init__.py` | 按 bot 类型分发到对应 channel；去掉顶层 OneBot 类型硬依赖（改为 TYPE_CHECKING） |
| `config.py` | 新增官方配置项（§6.2） |
| `config_api.py` | 新增「官方机器人」配置分组 |
| `dispatcher/__init__.py` | `normalize` 委托给 channel |
| `media/__init__.py` | 改为调用 `channel.send(ctx, Outgoing(...))` |
| `session/__init__.py` | key 由 int 改为 str `user_key` |
| `history/__init__.py` | 记录 `channel` 字段 |
| `gallery.py` / `queue` / `ratelimit` | 改用 `user_key`/`group_key` |

**不动**：`intent/`、`taskmgr/`、`safety/`、`pic/`、`monitor/`（纯业务，协议无关）。

### 6.2 配置设计

`.env` 新增：

```ini
# ---- QQ 官方机器人（双轨并行，与 NapCat 互不干扰）----
OFFICIAL_ENABLE=true
OFFICIAL_APPID=1020xxxxx
OFFICIAL_SECRET=xxxxxxxx
OFFICIAL_SANDBOX=true            # 沙箱不受频控，PoC 阶段开
OFFICIAL_USE_WEBSOCKET=true
# Intent（JSON）
OFFICIAL_INTENT={"c2c_group_at_messages":true,"group_members":true}
# 官方通道日额度保护（平台 1000 条/群/天，这里提前自限）
OFFICIAL_DAILY_GROUP_LIMIT=900
# 被动回复策略：超过该秒数未完成 → 转主动消息（平台硬限制 5 分钟）
OFFICIAL_PASSIVE_MAX_AGE=240
```

`config.py` 新增同名类属性（密钥**只从环境变量读，不落日志**，遵守 LOG-04）。

`config_api.py` 的 SCHEMA 增加分组「官方机器人」，secret 字段 `secret=True`（前端掩码）。

### 6.3 通道注册

```python
# bot.py
nonebot.init(onebot_access_token=ACCESS_TOKEN,
             qq_bots=OFFICIAL_BOTS_JSON)          # nonebot-adapter-qq 的配置项
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
driver.register_adapter(QQAdapter)                 # 新增
```

`DRIVER=~httpx+~websockets`（官方适配器需要 HTTPClient + WebSocketClient）。

### 6.4 事件归一化

| 官方事件 | 说明 | ctx 映射 |
|---|---|---|
| `GROUP_AT_MESSAGE_CREATE` | 群内 @机器人 | `group_key=group_openid`，`reply_ref={"msg_id": d.id}` |
| `GROUP_MESSAGE_CREATE` | 群内非 @ 消息（需群主开启） | 同上 |
| `C2C_MESSAGE_CREATE` | 私聊 | `user_key=user_openid`，`reply_ref={"msg_id": d.id}` |

`normalize` 返回 **ctx v2**（§3.4），后续 `safety/ratelimit/intent` 流程完全不变。

### 6.5 发送层重构

`media.send_*()` 不再直接拼 `MessageSegment`，改为构造 `Outgoing` 后交给 `channel.send()`：

```python
async def send_image(ctx, file, at_user=False):
    await ctx_channel(ctx).send(ctx, Outgoing(image_urls=[file], at_user=at_user))
```

NapCat 通道内部保持现有行为（URL 直发 + base64 回退），官方通道走上传。

### 6.6 富媒体上传模块（官方）

```
official/upload.py
  async def upload_image(ctx, data: bytes, scene: str) -> str
      # scene: "group" | "c2c"（接口不互通）
      1. POST /v2/{groups/{gid}|users/{uid}}/files  → upload_prepare
         入参 file_type=1, file_size, md5_10m
         → upload_id, block_size, 每片 presigned_url
      2. for chunk: HTTP PUT → presigned_url
      3. POST .../files/part_finish 声明该片完成
      4. POST .../files 带 upload_id 合并 → file_info（带 TTL）
```

- 缓存：`(md5, scene) → (file_info, expire_at)`，**TTL 前 60 秒视为过期**（避免踩 TTL 失效）。
- 小图（< block_size，默认 5MB）走**整文件上传**（`file_data` base64）更简单，代码里按大小分流。
- 超时：官方建议 ≥5 秒。
- 失败：分片 PUT 重试 3 次（1s/2s/4s + 抖动，遵守 REDUN-02），仍失败则记日志并回退为文本提示（CLOSE-02 不静默）。

### 6.7 被动 / 主动发送策略（关键）

```
T0        用户 @ 生图
T0+~1s    被动回复「收到，正在生成…（预计 1-2 分钟）」  ← 带 msg_id，不占日额度
T0+?      轮询主项目任务（现有 taskmgr 逻辑不变）
          ├─ 完成且 elapsed < OFFICIAL_PASSIVE_MAX_AGE(240s)
          │    → 被动回复图片（msg_seq 递增，最多 5 次）
          └─ 超时/超过 5 次
               → 主动消息（占日额度，需群主已开开关）
                  失败 40034105/40054013 → 文本提示用户去开开关
```

**日额度保护**：`official/limiter.py` 按 `(group_key, date)` 计数，达 `OFFICIAL_DAILY_GROUP_LIMIT` 主动降级为文本提示，避免撞平台 1000 条硬限。

### 6.8 身份与会话映射

| | NapCat | 官方 |
|---|---|---|
| 用户 | `u<QQ号>` | `user_openid` |
| 群 | `g<群号>` | `group_openid` |
| 私聊会话键 | `u<QQ号>` | `c2c:<user_openid>` |

- `Session.user_id: int` → `user_key: str`（兼容层：int 传入会自动 `f"u{id}"`）。
- 历史数据：NapCat 历史保持原样；官方通道历史写入时带 `channel=official`，**不做跨通道合并**。

### 6.9 业务层复用说明

| 模块 | 是否改动 | 说明 |
|---|---|---|
| `intent/` | 不改 | 纯文本意图路由 |
| `taskmgr/` | 不改 | 调 7788，与协议无关 |
| `queue/` | 小改 | 任务记录加 `channel`/`user_key`/`group_key` |
| `ratelimit/` | 小改 | 冷却键改用 `user_key` |
| `safety/` | 不改 | |
| `pic/` | 不改 | 自绘图输出 bytes，两通道都用「内存图片上传/直发」 |
| `monitor/` | 不改 | 私聊告警走 NapCat 主人号（保持稳定通道） |
| `gallery.py` | 小改 | 按 channel 过滤 |

### 6.10 前端页面

- 保留现有 QQ 面板全部功能。
- 会话列表/历史记录增加**通道标签**（NapCat / 官方）。
- 管理页新增「官方机器人」配置分组（AppID / Secret / 沙箱开关 / Intent / 日额度）。
- 状态区展示：两通道各自在线状态（NapCat 显示 `real_online`，官方显示 WS 连接态）。

### 6.11 日志

- 统一 `jlog()` 结构化单行，新增字段 `channel`。
- 官方请求/响应/耗时必须打全（用户铁律：模型与平台调用必须记 request/response/error/duration_ms）。
- **禁止**落 AppSecret、access_token（LOG-04）。

---

## 7. 实施计划

| 阶段 | 内容 | 产出 | 验收 |
|---|---|---|---|
| **P0 文档** | 本方案 | 本文档 | 用户评审通过 |
| **P1 PoC 骨架** | 装 `nonebot-adapter-qq`，双 adapter 启动，官方通道收消息 + 回文本 | 能收能回 | 沙箱群 @ 机器人收到文本回复 |
| **P2 富媒体** | 分片上传 + 发图 | `official/upload.py` | 沙箱群收到**机器人生成的图片** |
| **P3 业务接入** | 官方通道跑通完整生图链路（意图/队列/限流/历史） | 端到端 | 沙箱群走完一次生图全流程 |
| **P4 双轨完善** | 前端通道标签、配置分组、状态展示、限流保护 | 页面 | 两通道消息可区分 |
| **P5 灰度** | 正式群试运行 1~2 周 | 运行报告 | 无 P0 故障 |
| **P6（可选）** | NapCat 下线评估 | 决策 | 用户拍板 |

每阶段结束必须提供「操作步骤 + 预期结果」供用户人工复现验收。

---

## 8. 测试方案（正式 pytest，禁止临时脚本）

| 文件 | 覆盖 |
|---|---|
| `tests/test_channel_registry.py` | 通道注册/获取/未知通道兜底 |
| `tests/test_channel_official.py` | 官方 normalize → ctx v2；主动/被动判定；@ 提取 |
| `tests/test_official_upload.py` | 整文件上传 / 分片流程 / TTL 缓存 / 失败重试 |
| `tests/test_official_limiter.py` | 日额度计数与降级；跨天重置 |
| `tests/test_official_send.py` | 文本/图片/视频发送；`msg_type=7` content 空格 |
| `tests/test_official_dual_channel.py` | 双通道并存互不影响；NapCat 链路回归 |

原则：用 `httpx.MockTransport` 模拟官方 API，不依赖真实网络；E2E 单独文件并默认跳过（环境变量开启）。

---

## 9. 灰度与回滚

- **开关**：`OFFICIAL_ENABLE=false` 一键关闭官方通道，NapCat 不受任何影响。
- **回滚**：删除 `QQAdapter` 注册即可，业务层改动（ctx v2）向后兼容 NapCat。
- **灰度顺序**：沙箱群 → 自建测试群 → 1 个正式群 → 全量。

---

## 10. 风险登记册

| # | 风险 | 影响 | 应对 |
|---|---|---|---|
| R1 | 群主未开主动消息开关 | 出图发不回 | 入群引导文案 + 失败提示引导；检测 `40034105` 主动告知 |
| R2 | 被动回复 5 分钟超时 | 生图结果丢失 | 超时转主动消息 + 日额度保护 |
| R3 | 家用动态 IP 变化 | OpenAPI 全调不通 | 监控 `401/403`，告警提示更新白名单；长期考虑固定出口 |
| R4 | openid 与 QQ 号不互通 | 历史/会话割裂 | 分通道存储，不做强制合并 |
| R5 | 图片上传慢/失败 | 用户体验差 | 分片并发 + 重试 + 缓存 + 失败降级文本 |
| R6 | 日额度 1000 条/群打满 | 群内服务中断 | 自限流 900 + 提示 |
| R7 | 内容审核更严（官方通道） | 违规被封应用 | `safety` 前置 + 遵守运营规范 |
| R8 | 双通道消息串扰 | 重复回复 | 通道级会话隔离 + 幂等键含 channel |

---

## 11. 验收标准（用户可复现）

1. **P1**：沙箱群 @ 机器人 → 收到文本回复；NapCat 群同时正常。
2. **P2**：沙箱群 @ 机器人发一张本地生成的图 → 收到图片，控制台日志含 `channel=official`、`upload_ok`、`duration_ms`。
3. **P3**：沙箱群完整生图流程（含排队、进度、失败重试、配额退还）走通。
4. **P4**：前端页面能看到两通道消息并带标签；关闭 `OFFICIAL_ENABLE` 后 NapCat 功能完全不受影响。
5. **P5**：正式群连续运行 7 天，无因官方通道导致的不可用。

---

## 12. 待实测确认项（PoC 阶段验证）

1. **被动回复是否占用「1000 条/群/天」额度** —— 官方文档将被动与主动分两节描述，1000 条写在主动节；需沙箱实测确认。
2. **官方 heartbeat / 连接态语义** —— 用于状态展示（NapCat 那套判定已证明不可靠，官方需重新定义）。
3. **分片上传实际耗时与成功率**（本地 → 腾讯，取决于上行带宽）。
4. **群内非 @ 消息（GROUP_MESSAGE_CREATE）在本机环境的可用性**与审核要求。

---

## 附录 A：权威参考

- [QQ Bot 介绍与接入指南](https://bot.q.qq.com/wiki/bot_new_product-intro/)（2026-08-13）
- [消息收发概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)（频控/被动/主动）
- [发送群聊消息 API](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)（2026-09-03）
- [富媒体消息概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/rich-media.html)
- [机器人 FAQ](https://q.qq.com/wiki/FAQ/robot/)（沙箱、机器人数量上限）
- [nonebot/adapter-qq](https://github.com/nonebot/adapter-qq)（NoneBot 官方适配器，MIT）
- [NapCatQQ #869](https://github.com/NapNeko/NapCatQQ/issues/869)、[#929](https://github.com/NapNeko/NapCatQQ/issues/929)
- [2026 年 QQ 群机器人两条路线梳理](https://damodev.csdn.net/6a79617810ee7a33f298e71d.html)

## 附录 B：关键错误码（官方）

| 码 | 含义 | 处理 |
|---|---|---|
| 40034100 | 主动消息超频控 | 降速 / 次日恢复 |
| 40034105 | 主动消息无权限 | 提示群主开开关 |
| 40034128 | 被动回复时间或次数超限 | 转主动消息 |
| 40034005 / 304103 | msg_id 已过期 | 转主动消息 |
| 40054010 | 不允许发送 URL | 改走 file_info |
| 40054013 | 用户拒收 | 提示用户开启 |
| 40054007 | 消息长度超限 | 截断 |
| 40034006 | 消息内容违规 | safety 拦截并提示 |
| 304080 | file_info 无效 | 重新上传 |

## 附录 C：术语

| 术语 | 含义 |
|---|---|
| openid | 官方体系下的用户/群唯一标识（≠ QQ 号） |
| file_info | 富媒体上传后返回的句柄，带 TTL，用于 msg_type=7 发送 |
| 被动消息 | 携带 msg_id/event_id 的回复，受时效与次数约束 |
| 主动消息 | 不针对特定消息的推送，受 qpm 与日额度约束 |
| Intent | WebSocket 事件订阅位（决定能收到哪些事件） |
| 沙箱 | 测试环境，不受频控，仅操作测试群 |

---

## 13. P1 实施记录（2026-09-12 完成）

### 13.1 已完成（P1~P4）

| 阶段 | 项 | 内容 |
|---|---|---|
| P1 | 依赖 | `nonebot-adapter-qq==1.7.2` 已装进 `qq_bridge/.venv` |
| P1 | 通道抽象 | 新增 `channel/`：`base.py`（`Channel` ABC + `Outgoing` + `make_ctx`）、`napcat.py`、`official.py`、`__init__.py`（惰性注册表 + `channel_of_bot`） |
| P1 | 双适配器 | `bot.py` 按 `OFFICIAL_ENABLE` 决定是否注册 `QQAdapter`；未配置时**连 import 都不做** |
| P1 | 入口分发 | `__init__.py` 新增 `official_matcher`（独立 matcher + 官方类型注解），NapCat 的 `msg_matcher` 一行未动 |
| P2 | 富媒体 | `official.py` 支持图片（URL 下载→`file_image` 本地上传）与视频；**文本与每张图各一条消息**（官方 msg_type 互斥） |
| P2 | 额度保护 | `channel/limiter.py`：按 (群, 日期) 计主动消息，跨天重置，超限降级（防 40034100） |
| P2 | 被动/主动 | 5 分钟内带 `msg_id` 被动回复（不占额度），超窗或超 5 条自动转主动 |
| P3 | 业务接入 | `OfficialChannel.to_legacy_event()` → `QQEvent`，复用统一路由 `_route()`（意图/门禁/限流/队列/配额/会话全复用） |
| P3 | 身份映射 | `pseudo_id(openid)` 取**负数**（真实 QQ 号恒为正，永不串号） |
| P3 | 发送分发 | `media` 各 `send_*` 按 `ctx["channel"]` 分派；无该字段时行为与改造前完全一致 |
| P4 | 配置分组 | `config_api.SCHEMA` 新增「官方机器人」8 项，管理页**数据驱动自动渲染**，无需改前端 |
| P4 | 状态暴露 | `/api/qq/status` 新增 `channels{napcat,official}` 与 `official{enabled,configured,online,sandbox,bots}` |
| P4 | **第二套接口** | 官方机器人独立命名空间 `/api/qqo/{status,config}`，与 NapCat 的 `/api/qq/*` 完全隔离；保存时**只接受官方分组 key**，其余进 `rejected` |
| P4 | **第二个页面** | 主项目新增 `QQOfficialPanel.vue` + 路由 `/qq-official` + 侧边栏「官方机器人」入口；只调 `/api/qqo/*`，不动 NapCat 页面 |
| — | 测试 | `tests/test_channel_official.py` **24 个用例全过**（含 P2 富媒体/额度、P3 全链路映射与 media 分派） |

### 13.2 实测：适配器 1.7.2 与官方文档/GitHub README 的差异

| 项 | 文档/README | 实际 1.7.2 |
|---|---|---|
| 机器人配置字段 | 只需 `id` + `secret` | `BotInfo` **还要求 `token` 字段**（必填，无默认值）→ 传空串即可，实际鉴权走 appid+secret 换 access token |
| 事件类名 | `GROUP_AT_MESSAGE_CREATE` | 驼峰类名：`GroupAtMessageCreateEvent` / `GroupMessageCreateEvent` / `C2CMessageCreateEvent`（前者是平台事件名） |
| 身份字段 | openid | `event.author.member_openid`（群）/ `event.author.user_openid`（C2C）/ `event.group_openid` |
| 富媒体上传 | 需自己实现分片流程 | **已内置**：`post_group_upload` / `post_c2c_upload` 自动完成 prepare→并发 PUT→part_finish→合并；`send_to_group` 还会自动判定 `msg_type` |

→ 结论：**P2 的"自研分片上传"可大幅简化**，直接用 `MessageSegment.file_image(bytes)` + `bot.send_to_group()` 即可。

### 13.3 踩坑记录（必须记住）

1. **NoneBot handler 参数不能写 `Any`**
   把 `_timed_handle_message(bot: Any, event: Any)` 想当然地泛化 → 启动直接
   `ValueError: Unknown parameter bot ... with type typing.Any`。
   NoneBot 依赖注入靠**具体类型注解**挑 handler。正确做法：**一个通道一个 matcher**，
   各自用本适配器类型（本项目的 `official_matcher` 即如此）。

2. **`jlog()` 第一个形参就叫 `event`**
   `jlog("xxx", event=...)` 会报 `TypeError: jlog() got multiple values for argument 'event'`。
   日志字段名要避开 `event`（改用 `event_type` 等）。

3. **派生配置字段不会随源字段更新**
   `Config.MANJU_FILE_BASE` 在**类定义时**由 `MANJU_API_BASE` 派生一次；运行时改
   `MANJU_API_BASE` 不会联动。（这是 `tests/test_taskmgr.py` 6 个用例失败的既有根因，
   与本次改动无关。）

### 13.4 当前状态与验证结论

- `OFFICIAL_ENABLE=false` → 官方 matcher **不注册**，NapCat 链路零影响（代码路径证明）。
- 全量测试：改动前后均为 **24 failed / 383 passed / 1 skipped**（零新增失败）；
  24 个失败为既有问题（测试间 `Config` 状态污染 + `MANJU_FILE_BASE` 派生问题），非本次引入。
- e2e 启动测试（`pytest -m e2e`，真起 `bot.py`）：修复后 bridge 正常监听 18080，
  3 passed / 3 failed（3 个失败同样是既有：`/help` 已改为发自绘图，测试仍断言文本）。

### 13.5 客观限制（不是缺陷，是官方平台的事实）

1. **拿不到用户 QQ 号**，只有 openid → 按 QQ 号配置的白/黑名单**对官方通道无效**。
   访问控制只能依赖：官方平台「服务范围」+ 群主邀请 + 本项目的每日配额。
   （`STRICT_WHITELIST` 若开启，官方用户会因不在白名单被静默拒绝，需保持 false。）
2. **伪负数 ID**：业务层主键仍是 int，官方用户用 `pseudo_id()` 映射为负数
   （真实 QQ 号恒为正 → 永不串号）。副作用：历史页显示负数 ID。
3. **官方通道暂不落历史**：`history.record_incoming` 依赖 OneBot 的 `message_type`，
   官方事件没有该字段；改为 channel-aware 属后续增强。
4. **消息不能含 URL**（40054010）→ 视频链接回退时不贴链接，只提示去任务中心。
5. **群主动消息需群主开开关**，否则出图结果发不回（40034105）。

### 13.6 两个机器人、两个页面（用户明确要求，2026-09-13）

**不是**在一个页面里加通道标签，而是**两套完全独立的入口**：

| | 机器人 A（已有） | 机器人 B（新增） |
|---|---|---|
| 身份 | NapCat 挂机小号 `3858741376` | QQ 官方 **APPID 应用**（不是 QQ 号） |
| 协议端 | OneBot V11，反向 WS:8080 | 官方 WebSocket（出站，无需公网） |
| 后端接口 | `/api/qq/*` | **`/api/qqo/*`** |
| 前端页面 | `/qq-bridge`（QQBridgePanel） | **`/qq-official`（QQOfficialPanel）** |
| 侧边栏 | 「QQ 机器人」 | 「官方机器人」 |
| 配置 | `.env` 中除「官方机器人」外的全部 | 仅「官方机器人」分组 |
| 故障影响 | 互不影响：一个掉线/没配，另一个照常 | 同左 |

隔离保证（代码层面）：
- `QQOfficialPanel.vue` 只 `fetch(BRIDGE + '/api/qqo/...')`，**不读也不写** NapCat 的任何接口；
- `/api/qqo/config` 的 PUT 会用 `SCHEMA` 过滤，**只接受「官方机器人」分组的 key**，
  误传 NapCat 参数会被列进 `rejected` 而不是写入 `.env`；
- 官方通道未启用时，`official_matcher` 不注册，NapCat 链路一行代码都不执行。

### 13.7 当前状态

- `OFFICIAL_ENABLE=false` → 官方 matcher **不注册**，NapCat 链路零影响。
- 全量测试：`24 failed / 393 passed`（改动前基线 `24 failed / 383 passed`，
  **零新增失败**，新增 10 个用例全过）；24 个失败是既有问题
  （测试间 `Config` 污染 + `MANJU_FILE_BASE` 派生），非本次引入。
- e2e 启动测试（`pytest -m e2e`，真起 `bot.py`）：`3 passed / 3 failed`，
  失败三项同为既有（`/help` 已改为发自绘图，测试仍断言文本）。
- **bridge 进程仍是旧代码**，需重启后新逻辑才生效。

---

## 14. 验收清单（人工，需 AppID/Secret）

### 14.1 准备

1. `.env` 填 `OFFICIAL_APPID` / `OFFICIAL_SECRET`，设 `OFFICIAL_ENABLE=true`
   （沙箱阶段 `OFFICIAL_SANDBOX=true`）；
2. 重启 bridge（`stop_bridge.ps1` → `start_bridge.ps1`）；
3. 看日志必须出现：`QQ official adapter registered` 与 `official_matcher_registered`。

### 14.2 逐项验收

| # | 操作 | 预期结果 |
|---|---|---|
| 1 | 沙箱群 @机器人 `生图 一只赛博朋克猫` | 收到「在线/本地」通道反问；回复「2」→ 云端出图并回传图片 |
| 2 | 私聊机器人 `/ping` | 回「pong。QQ 官方通道在线（WebSocket 直连…）」 |
| 3 | 私聊 `/help` | 收到菜单（自绘图优先，失败回落文本） |
| 4 | 打开主项目侧边栏「官方机器人」（`/qq-official`） | 页面显示：在线徽标、机器人 ID、沙箱、日额度；配置区只有官方参数 |
| 5 | 在「官方机器人」页改配置并保存 | 提示已保存项数；`/api/qqo/config` 的 `rejected` 为空（不会写到 NapCat 参数） |
| 6 | 查看 `GET http://127.0.0.1:8080/api/qqo/status` | `online=true`、`configured=true`、`bots` 非空 |
| 7 | 打开 `/qq-bridge`（NapCat 页面） | 配置仍是 NapCat 那一套，参数**未被官方页改动** |
| 5 | 连续发 3 条生图 | 队列/限流/配额行为与 NapCat 一致，无重复扣额度 |
| 6 | 把 `.env` 改回 `OFFICIAL_ENABLE=false` 并重启 | NapCat 行为完全恢复，官方相关日志不再出现 |

### 14.3 回滚

`OFFICIAL_ENABLE=false` 后重启即可；极端情况删除 `bot.py` 里的 `QQAdapter` 注册。
NapCat 链路在本方案中**从未被修改行为**，回滚零成本。
