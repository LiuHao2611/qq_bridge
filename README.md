# qq_bridge —— manju_agent 的 QQ 接入桥接层

依据：`docs/qq_integration_design.md` v0.2（NapCat/LLOneBot 个人号协议端 + NoneBot2 框架）。

> **最后更新：2026-11-05 17:40**
>
> 本次变更（P0 运维，详见 `AGENTS.md` 变更记录 §17:24）：
>
> 1. **bridge 部署形态收口为容器一条路线**。Windows 的 `SO_REUSEADDR` 允许两个进程
>    **同时 bind `0.0.0.0:8080` 且都成功**，此前开机时宿主 python 与容器各占一份，
>    输的那侧永久 `ws_down` 却一直占着端口（实测连续 3h47m）。`start_bridge.ps1`
>    现降级为**回退通道**，检测到容器在跑会 `exit 1`。
> 2. **aria2 凭据单一来源**：`DL_ARIA2_SECRET` 只放 `.env`（宿主与容器共用），
>    compose 只留端点。此前硬编码在 compose 里，宿主实例读不到 → 每 7 秒刷一条
>    `download_tick_failed`，且 Unauthorized 被显示成「端口报错」。
> 3. **测试不再污染生产日志**：pytest 曾用真实群号伪造群事件写进 `logs/qq_bridge.log`，
>    与真群消息无法区分。`config.LOG_DIR` 改读 `QQ_LOG_DIR`，`tests/conftest.py` 统一改道。
> 4. 新增门禁 `tools/check_bridge_sanity.py`（端口单一所有者 / aria2 凭据 / 测试日志隔离）。

## 隔离约定（最高优先级，不可违背）

| 约束 | 落地 |
|---|---|
| 目录隔离 | 代码/配置/日志/测试**全部在本目录内**，不写主项目 `server/`、`src/`、`frontend/`、`logs/`、`data/` |
| 进程独立 | 本服务独立进程、独立端口（默认 `127.0.0.1:8080`）；主项目后端 `7788`、前端 `8099` 互不干扰 |
| 崩溃不影响主项目 | 主项目启动链路不含、不依赖、不反向拉起本服务；本服务挂掉主项目照常运行 |
| 单向依赖 | 调主项目能力**只能走 HTTP**（`MANJU_API_BASE`），**禁止 import 任何 `server/` 模块** |
| 日志自管 | 只写 `qq_bridge/logs/`（`qq_bridge.log`、`qq_bridge_error.log`），不落主项目 `logs/` |

## 目录结构（设计文档 §4）

```
qq_bridge/
├── bot.py                      入口：nonebot.init + 注册 OneBotV11Adapter + load_plugins
├── pyproject.toml / requirements.txt
├── docker-compose.napcat.yml   NapCat 容器编排（设备身份四要素在此钉死）
├── docker-compose.bridge.yml   ★ bridge 容器编排（**当前唯一部署方式**，含 aria2 sidecar）
├── start_all.ps1               一键拉起（NapCat 不动 → compose 起 bridge）
├── start_bridge.ps1            ⚠️ 回退通道（宿主机 python 跑 bot.py）；检测到容器在跑会 exit 1
├── stop_bridge.ps1             停宿主机 python 实例（只认命令行含 bot.py 的进程）
├── tools/check_bridge_sanity.py  部署形态门禁：端口单一所有者 / aria2 凭据 / 测试日志隔离
├── .env.example                配置模板（§3.6 限流 / §8 权限 / §9 风控）
└── src/plugins/qq_bridge/      唯一 nonebot 插件入口
    ├── config.py               配置中心 + 结构化日志（不 import nonebot，便于单测）
    ├── connector/              §3.1 连接层：bot 生命周期 + call_action
    ├── dispatcher/             §3.2 事件标准化 + 白黑名单
    ├── intent/                 §3.3 意图路由 + §6 指令集
    ├── taskmgr/                §3.4 任务编排（重试/串行队列/看门狗）
    ├── safety/                 §3.5 内容安全
    ├── ratelimit/              §3.6 限流（冷却 + 每日配额）
    ├── media/                  §3.7 媒体回传（image/video 段 + 随机延迟）
    └── session/                §3.8 会话态（源图暂存 + TTL）
tests/                          正式测试（pytest），e2e 用独立端口 18080
```

## 本机部署步骤（Windows，你只需要做第 1~3 步）

### 1. 启动 NapCat（Docker 容器）

> **⚠️ 2026-10-29 架构重构：原生注入版 NapCat 已彻底废弃，不要再使用。**
>
> 历史做法是用 `NapCatWinBootMain.exe` 把 NapCat 注入到 Windows 桌面版 QQ 里跑（原
> `onekey/bootmain/` 与 `napcat/` 两套文件，合计 120MB+，已于同日删除）。它与容器版
> **是同一个账号的两份独立会话**，会互相踢下线，并被腾讯标记为「风险设备」。
>
> 实测后果（2026-10-28，数据见 `logs/qq_bridge.log` 的 connector_attached/detached）：
>
> | 时段 | 在线时长 |
> |---|---|
> | 10-27 16:18 → 10-28 09:34 | **17.25 小时**（只跑容器，无冲突） |
> | 10-28 14:31 重登后 | 仅 2.9 小时 |
> | 之后 17:23 / 17:47 / 18:34 / 20:21 / 21:07 / 02:05 | 连续断 6 次，单段最短 **4.9 分钟** |
>
> 最终 02:05 登录票据被服务端作废，只能重新扫码，而 NapCat 被踢后不会自动出码，
> 白等了 5 个半小时。**结论：一个账号只能有一个 NapCat。**

正确做法（容器化，登录态持久化在 `./data/qq` 卷）：

```powershell
cd D:\AI\manju_agent\qq_bridge
docker compose -f docker-compose.napcat.yml --env-file data\compose.env up -d --build
```

容器名 `qq-bridge-napcat`，镜像 `qq-bridge-napcat:launcher-v4.18.41`（自建，**不是**上游 `latest`），
WebUI 在 `127.0.0.1:6099`。

### 1.1 为什么是自建镜像（Launcher 注入）

上游 `mlikiowa/napcat-docker` 的做法是在**构建镜像时**用 `sed` 把磁盘上 QQ 的
`/opt/QQ/resources/app/package.json` 的 `"main"` 从腾讯原值
`./application.asar/app_launcher/index.js` 改写成 `./loadNapCat.js`。
这是一个可被「读自身 package.json 做完整性校验」直接命中的**磁盘级静态特征**。

`docker/Dockerfile.napcat-launcher` 改成 NapCat 官方 Linux 安装器用的那套做法：

| | 上游镜像 | 本仓库镜像 |
|---|---|---|
| `package.json` 磁盘值 | 被 sed 改成 `./loadNapCat.js` | **腾讯原值，零修改** |
| `loadNapCat.js` 磁盘文件 | 存在 | **不存在** |
| 注入时机 | 构建期写盘 | 运行期 `LD_PRELOAD` 劫持 `open64`/`fopen64`，用 `memfd_create` 返回伪造内容 |

两个容易踩的坑，都已写进 Dockerfile 并在构建期断言：

- **cwd 必须是 `/app/napcat`**。⚠️ 预编译 `.so` 与仓库最新 `launcher.cpp` 源码不是同一版：
  二进制里注入 JS 写的是 `path.join(CurrentPath, './napcat.mjs')`（cwd 同级），
  而仓库源码写的是 `'./napcat/napcat.mjs'`。按二进制来，cwd 必须是 `/app/napcat`。
  若改成 `/app`，main 会指向 `/app/loadNapCat.js` 而它 import 的 `/app/napcat.mjs`
  不存在 —— QQ 正常启动、NapCat 静默不加载，极难排查。
  上游 entrypoint 本来就是 `cd /app/napcat`，本镜像不动它，并加断言守住。
- **`.so` 里的 `ORIGINAL_MAIN` 必须与实际 package.json 精确匹配**。匹配不上的话
  QQ 会按原生入口正常启动、NapCat 静默不加载，表现是「进程在但永远不连桥」，极易
  被误判成网络问题。所以每次换镜像版本后跑一次验收：

```powershell
python scripts/verify_launcher.py   # 退出码 0 = 5 条不变量全部成立
```

### 1.2 版本为什么钉在 v4.18.41

社区流传「最新版一两天必掉」「4.15.0 的 docker 版怎么挂都不掉」，但那些说法没有对照组。
可查证的事实是：

- 本机 QQ 内核 `3.2.34-53644`，NapCat 直到 **v4.18.34** 的 release note 才写明
  「新增 Linux QQ 3.2.34-53644 的 Packet、Napi2Native 和 AppID 映射」——
  退到 4.15.0 等于让 hook 表覆盖不到当前 QQ 构建。
- **v4.18.41** 已实测出镜像：`docker manifest` 与 `:latest` 同 digest
  `sha256:b101b896…`，说明 41 就是当前上游的稳定发布点（而不是漂移中的边缘版）。

所以取「支持当前 QQ 内核 + 上游已在用的发布点 + 不是浮动标签」的 **v4.18.41**。
**钉版本的真实价值是停止自动漂移**（`:latest` 意味着某次 pull 就可能换掉内核），
而不是迷信某个特定版本号。要换版本只改 compose 与 Dockerfile 里的 `NAPCAT_VERSION`。

> 曾计划钉 v4.18.37（它的 release note 明写「docker 镜像在测试设备上启动及配置、
> 插件持久化检查通过」）。实际执行时该 tag 的两个大层（`c2a923760eab`、`1287c987d7f3`）
> 反复卡 `Waiting`，直连 / `docker.m.daocloud.io` / 三次重试共 35 分钟无进展，才改用 41。
> 两者的 QQ 内核映射完全相同（都 > 4.18.34），不影响上面的推理。


### 1.3 自动重试登录：已在三个层面禁死

账号 2503891418 于 2026-10-28 被标记、次日被强制下线后，实测容器会**每 2 分钟自己发起一次登录**。
从腾讯服务器看，这就是「这个号在不停地登」，正是项目铁律里封号的直接根因。三处一起掐：

| # | 位置 | 原始行为 | 处置 |
|---|---|---|---|
| 1 | `napcat.mjs` `onQRCodeSessionFailed` | 二维码会话超时（QQ 侧约 2 分钟）→ 立刻 `getQRCodePicture()` 生成新码 → 新码再超时 → 无限循环 | 改成只写错误提示 + 告警日志，不再取码 |
| 2 | `napcat.mjs` `onLoginConnected` | 登录服务重连（掉线/被踢后）只要未登录就自动 `getQRCodePicture()` = **自己重新登录** | 改成只记「已禁用，等待人工刷新」 |
| 3 | `docker-compose.napcat.yml` `restart` | `unless-stopped`：QQ 进程一退出容器就停，Docker 立刻拉起 → entrypoint 再跑一次 `qq -q $ACCOUNT` = 又一次登录 | 改为 `"no"`，容器停了就停着，必须人工 `docker start` |

第 1、2 项由 **`docker/qqbridge_no_autologin.py`** 在 entrypoint 里、解压出 `napcat.mjs`
之后就地改写（`napcat.mjs` 是运行时产物，不能打进镜像层）。脚本是幂等的，
且**找不到目标代码就 `exit 1` 让容器起不来**——补丁失效时宁可不可用，也不能让自动重登悄悄复活。

**刻意保留的取码路径**（不是漏洞）：

- `setRefreshQRCodeCallback` —— WebUI 手动刷新二维码，`scripts/qq_login_qr.py` 走这里；
- 首次启动流程 `t2e` —— `-q` 快速登录、密码回退、失败后回落到二维码。
  这是「一次启动一次尝试」，不是循环；禁掉就根本没法扫码登录了。

验收：`python scripts/verify_no_autologin.py`（查的是容器内那份运行时 `napcat.mjs`，
不是仓库里的文件），应输出 `8 PASS / 0 FAIL`。

**代价**：二维码过期后不会自动换新，需要手动刷新：

```powershell
.venv\Scripts\python.exe scripts\qq_login_qr.py
```

**三条不能违反的容器纪律**（违反任何一条都会被判「新设备」要求重新扫码）：

1. **不要 `up -d` 重建容器**。改配置走 `docker exec` 写文件 + `docker restart`；
   `up -d` 会换容器 ID → hostname/MAC 变化 → 设备身份漂移。
   （compose 里已把 hostname / MAC / machine-id / 内网 IP 四要素钉死，但能不重建就别重建。）
2. **不要同时跑桌面版 QQ 并登录同一个号**。手机 QQ 不受影响（官方支持一手机一电脑）。
3. **反检测开关必须开**。`data/config/napcat.json` 与 `data/config/napcat_<QQ号>.json`
   两个文件的 `bypass` 六项都要为 `true`、`o3HookMode` 为 `0`。
   只改主配置无效——账号级 `napcat_<QQ号>.json` 会覆盖它。`bypass.container` 的作用就是
   隐藏 Docker/cgroups 标识，关着等于向腾讯自报「我是容器里的第三方客户端」。

### 1.4 群聊总闸是**两个方向**，少关一边等于没关

2026-10-29 事故：`.env` 设了 `GROUP_ENABLED=false`，入站确实被拦死了（群消息一条都进不来），
**但机器人照样在 13:53 / 14:33 / 15:26 往群里发主动广播**：

```
15:26:15 沈韦清 | 发送 -> 群聊 [群名(1075851417)] 🍀【奇遇】你在坊市淘到一本残卷…
```

来源是 `xiuxian` 的世界事件循环（世界 Boss / 限时秘境 / 奇遇），它直接调
`bots[0].send_group_msg(...)`，**完全不走事件链路**。所以入站闸管不到它。

现在四个位置一起关（开关的唯一事实来源是 `src/group_switch.py`）：

| 层 | 位置 | 作用 |
|---|---|---|
| 入站 | `bot.py` `event_preprocessor` | 带 `group_id` 的事件在进任何 matcher 之前丢弃 |
| 出站·总闸 | `src/group_switch.py` `install_outbound_guard()` | 挂在 `Bot.call_api` 上，`send_group_msg` / `send_group_forward_msg` / `send_group_notice` / 带 `group_id` 的 `send_msg` 一律拦截 |
| 源头·xiuxian | `_broadcast()` 早退 + `_event_loop` 不启动 | 世界事件循环的唯一职责就是炸群，群关了它就没有存在意义，直接不启动 |
| 源头·主动发言 | `_proactive_candidates()` / `_emotion_care_loop` 过滤群 scope | 不只是「发了再丢」——`_proactive_speak` 会先花 token 让模型生成，生成完再被丢是纯亏钱 |

HTTP 接口 `/api/qq/send_msg` 在 `scene=group` 时直接返回 403 并说明原因，
不做静默伪成功——面板要能看懂「为什么没发出去」。

验收：`python scripts/verify_no_group_push.py`（10 项，含判定逻辑 + 出站闸真的挂上了 + 源头有闸）。
真机验证看 NapCat 日志：`docker logs qq-bridge-napcat | grep "发送 -> 群"` 应为 0 条。

### 2. 确认 NapCat 已连上 bridge

配置在 `data/config/onebot11_<QQ号>.json`（Docker 挂载卷），走 reverse_ws 主动连回宿主机：

```json
"websocketClients": [{
  "enable": true,
  "name": "rws",
  "url": "ws://host.docker.internal:8080/onebot/v11/ws",
  "token": "8ebfc30d5a726914",
  "messagePostFormat": "array"
}]
```

Token 必须与 `qq_bridge/.env` 的 `ONEBOT_V11_ACCESS_TOKEN` 一致。
**上线前请改成强随机值**：`python -c "import secrets;print(secrets.token_hex(8))"`（§9 #3）。

### 3. 启动 bridge（容器，唯一路线）

```powershell
cd D:\AI\manju_agent\qq_bridge
.\start_all.ps1                                        # 一键：NapCat 不动 → compose 起 bridge
# 只重启 bridge：
docker compose -f docker-compose.bridge.yml up -d      # 改过代码要加 --build
```

就绪判据：

```powershell
docker logs qq-bridge --tail 30      # 看到 Uvicorn running on http://0.0.0.0:8080 且无 ERROR
.\.venv\Scripts\python.exe tools\check_bridge_sanity.py   # 3 项全 PASS 才动手测；exit 1 = 部署形态不对
```

> **⚠️ `start_bridge.ps1` 是回退通道，不是常规入口。**
> 它用宿主机 python 跑 `bot.py`。Windows 的 `SO_REUSEADDR` 允许它与容器**同时**绑定
> `0.0.0.0:8080` 且都成功，流量归谁不确定 —— 输的那侧会永久 `ws_down` 却一直占着端口。
> 所以该脚本检测到 `qq-bridge` 容器在运行时直接 `exit 1`。真要回退：
> 先 `docker compose -f docker-compose.bridge.yml down`，再 `.\start_bridge.ps1`。
> 停止宿主机实例用 `.\stop_bridge.ps1`（它会遍历全部监听者，只结束命令行含 `bot.py` 的那个）。

### 4. 验证（渐进式，文档 §11）

| 步骤 | 操作 | 预期 |
|---|---|---|
| ① 连通 | 私聊机器人发 `/ping` | 回 `pong（协议端在线：True，连接次数：1）` |
| ② 生图 | 私聊发 `一只赛博朋克猫` | 回「收到，正在生成…」→ 回传图片 |
| ③ 改图 | 先发一张图，再发 `改成水彩风` | 关联源图并回传改图 |
| ④ 指令 | `/help` `/status` `/quota` `/mode video` | 见下表 |

## 指令集（文档 §6）

| 输入 | 行为 |
|---|---|
| 直接发文字 | 文生图（默认走**在线通道**，云端并发、不占本地 ComfyUI） |
| 发图 + 文字 | 图生图 |
| `/upscale` | 高清放大（需先发图） |
| `/outpaint 左\|右\|上\|下` | 扩图（需先发图） |
| `/video 描述` | 视频（**需主项目先实现 `/api/v1/qq/video-generate`，见下**） |
| `/mode image\|video` | 切换默认模式 |
| `换深色` `换浅色` `自动主题` | 切换出图配色（仅影响自己） |
| `/status` `/quota` `/help` | 任务进度（进度条图） / 今日配额（环形图） / 帮助（菜单图） |

群消息需 **@机器人** 或 `/` 前缀才会受理（未 @ 直接静默丢弃，不消耗额度）。

## 本地生图通道（ComfyUI，`IMAGE_CHANNEL=local`）

### 调的是主项目哪个接口

主项目文生图页（`/txt2img`）的真实调用是 `POST /api/v1/t2i/generate`
+ 轮询 `GET /api/v1/t2i/tasks/{task_id}`，QQ 侧走同一套，不另起炉灶。
在此之上新增了 **QQ 专用端点** `POST /api/v1/qq/image-generate`（`server/routers.py`）：

| 端点 | 本地通道正忙时 | 用途 |
|---|---|---|
| `/api/v1/qq/image-generate` | **409 拒绝**，绝不动别人正在跑的图 | QQ 专用（优先） |
| `/api/v1/t2i/generate` | 把进行中任务置 `cancelled` 并中断 ComfyUI | 页面端点（仅前端用） |

bridge 提交时**只**调 QQ 专用端点，不再回退到页面端点：QQ 通道要求必须配有效模板
（`LOCAL_TEMPLATE_ID` / settings `qq.imageDefaults.template_id`），拿不到模板（未配或模板不存在）
后端直接报 **422**（IMG_NO_TEMPLATE），不会退化为 SD1.5 内置流程出废图。状态查询统一走
`GET /api/v1/t2i/tasks/{task_id}`。

> 端口澄清：`http://127.0.0.1:8099/txt2img` 是**前端页面路由**，不是接口。
> 8099 只代理 `/api/v1` 前缀（`web_app.py`），可用接口是
> `http://127.0.0.1:7788/api/v1/...` 或 `http://127.0.0.1:8099/api/v1/...`。

### ⚠️ 参数跟随模板，别硬套 SD1.5 的值

配了模板时，bridge **不下发** steps / cfg / 尺寸，由后端取模板自带值
（`routers.py`：请求里 `steps<=0`、`cfg<=0` 或未给尺寸即用模板值）。

原因：本机模板「基础文生图unet」是蒸馏类模型，自带 **steps=8、cfg=1.0**；
硬套 SD1.5 的 20 / 7.0 会直接采样发散出废图（2026-09-04 核对主项目真实任务确认）。

`.env` 里 `LOCAL_STEPS=0` / `LOCAL_CFG=0` / `LOCAL_WIDTH=0` / `LOCAL_HEIGHT=0`
中的 `0` 即「跟随模板」。QQ 通道**必须配有效模板**——未配或模板不存在直接报错
（422 IMG_NO_TEMPLATE），不再退化为 SD1.5 兜底值。

### 前置条件

1. ComfyUI 在跑（默认 `http://127.0.0.1:8188`）；
2. 主项目 settings 的 `comfy.root` 已配置（当前 `D:\AI\ComfyUI-aki-v3.2\ComfyUI`）；
3. `.env` 里 `IMAGE_CHANNEL=local`。

### 默认参数契约（改后端即生效，不必动 bridge）

后端 settings 的 `qq.imageDefaults`（改它对所有 QQ 调用方立即生效，无需改 bridge）：

```json
{"template_id": "tpl_0e334372", "style": "写实", "count": 1,
 "enhance": false, "orchestrator": "none", "width": 720, "height": 1280}
```

> 尺寸锁定 **720×1280 竖屏**：模板自带 1280×720 横屏在当前 6GB 显存下
> 采样中途 OOM（`hostbuf_file_reader_read failed`），竖屏却能跑完（2026-09-04 真实验收）。

`enhance=false` 的原因：后端提示词润色**没有词典兜底**（`ai_local.py`），
Ollama 调不通会直接判失败；本机 Ollama 未运行，且与主项目今天的真实用法一致（英文原文直出）。

### 排队

本地 ComfyUI 同时只跑 1 个任务。bridge 提交前轮询 `/t2i/active-task?source=local`
等平台空闲（`LOCAL_QUEUE_TIMEOUT`，默认 900s），超时明确回话，**不会取消用户正在跑的图**。

## 自绘图片（菜单图 / 进度条图 / 卡片图，2026-09-07 新增）

机器人现在能**自己合成排版好的图片**发出去，比纯文字更直观美观。

### 能力范围（已接入真实消息链路）

- `/help` `你能干啥` → **菜单卡片图**（分区 + 图标 + 命令高亮）
- `/status` `你咋样` → **进度条图**（百分比 + 耗时 + trace + 状态色）
- `/quota` `今天额度` → **配额环形图**（已用占比 + 剩余/已用双数值）
- 失败/异常 → **自动降级回纯文本**（`_try_send_pic` 返回 False → 原文本兜底）
- 总开关 `PIC_ENABLE=false` → 一键回落纯文本（出问题可止血）

进度百分比口径：本地 ComfyUI 拿不到真实进度，用「已耗时 / `TASK_TIMEOUT`」估算，
图上标注为「已等待」而非「已完成 x%」，**不误导用户**。

### 视觉风格（双主题 + 热切换）

| 主题名 | 风格 | 主色 |
|---|---|---|
| `neon` | 深色霓虹 | 青 `#00E5FF` → 洋红 `#FF4FD8` 渐变进度条 |
| `light` | 极简白底 | 蓝 `#2563EB` → 浅蓝 `#60A5FA` |
| `auto` | 19:00~07:00 自动用 neon，其余 light | — |

切换方式：
1. **全局**：`PIC_THEME=neon|light|auto`（管理页可改，立即生效）
2. **临时**（女仆暗号）：`换深色` / `换浅色`（用户级，会话内优先）

### 技术选型

- 渲染：**Pillow 12.3**（Python 图像领域主流，PyPI 累计 88 亿下载；不引入浏览器/NoneBot 插件）
- 字体：Windows 系统自带 `msyh.ttc / msyhbd.ttc / simhei.ttf`，回退链自动找
- 性能：典型图 < 50ms，体积 < 30KB
- 隔离：全部代码/字体/缓存在 `qq_bridge/` 内，不 import 主项目任何模块

### 设计依据（2026 主流调研）

按"研发准则"在动手前检索了 6 个 2025-2026 主流方案（[nonebot-plugin-htmlrender v0.8.1](https://github.com/kexue-z/nonebot-plugin-htmlrender)、[htmlkit 官方](https://nonebot.dev/docs/best-practice/htmlkit-render)、[cardimg 2026-02](https://github.com/youlanan/nonebot-plugin-cardimg)、[zhenxun_bot BuildImage](https://github.com/zhenxun-org/zhenxun_bot)、Yunzai help-plugin、pilmoji），按数据对比与许可合规选定 Pillow，详见 [`docs/pic_render_plan.md`](docs/pic_render_plan.md)。

预览图（在真实 QQ 接入前可肉眼确认观感）：`data/pic_preview/progress_neon.png` / `progress_light.png`。

### 自定义主题（用户可自己配色）

觉得内置 `neon` / `light` 还不够？两种方式打开配色面板：

1. **管理页入口（推荐）**：`http://127.0.0.1:8099/qq-bridge` → 右上角「🎨 主题编辑器」按钮
   （按钮上的小徽标显示当前生效的自定义主题名；bridge 未运行时点击会提示先启动）
2. 直接访问 **`http://127.0.0.1:8080/pic/theme-editor`**（bridge 未启动则为 404）

打开后即可配色（v2，2026-09-07 重构：不再"只能调颜色"）：

**可调项**
- **颜色 17 项**（分组：背景 / 文字 / 主色 / 进度条 / 语义色）——拾色器用 [Coloris](https://github.com/mdbassit/Coloris)（MIT，本地内置不走 CDN），色值框可粘贴、可复制、可 🎲 随机和谐色
- **形状与排版 7 项**：卡片圆角、边距基准、出图宽度、字号缩放、渐变方向、卡片描边、阴影强度

**交互**
- **四模板实时预览**：文本卡 / 菜单 / 进度条 / 配额，切页签即看该模板真实效果
- **从图片取色**：上传一张图 → 提取主色 → 「🪄 智能填充」按亮度/饱和度自动分配整套配色
- **撤销 / 重做**（Ctrl+Z / Ctrl+Shift+Z）、Ctrl+S 保存、参数搜索、分组折叠、未保存标记
- **导入 / 导出 JSON**、预设一键填色、**保存 / 应用 / 删除**（删除走自研确认框，无原生弹窗）
- **WCAG 对比度**：正文/次级/弱文字/主色 vs 卡片底自动算（AA ≥ 4.5 / AAA ≥ 7），低于 3:1 直接告警
- **应用立即生效**——点「应用」后全局（机器人发出的所有图）立刻用新主题，**不用重启**

主题文件落在 `qq_bridge/data/themes/{name}.json`（含 `colors` + `layout` 两段；老文件只有 `colors` 也能正常用，形状参数自动取默认值，零迁移）。

#### v2 重构：案例对比与搬运来源（按研发准则检索，2026-09-07）

| 案例 | 许可 | 规模 | 借鉴/搬运点 |
|---|---|---|---|
| [tweakcn](https://github.com/jnsahaj/tweakcn) | Apache-2.0 | 10.3k stars | **三栏布局 + 颜色与 Radius 同属可编辑项**——直接启发了"不只调颜色"；Next.js 项目无法直接搬，按思路实现 |
| [Coloris](https://github.com/mdbassit/Coloris) | **MIT** | 616 stars，零依赖 | **源码直接搬运**（`pic/static/coloris.min.js/css`），替代土味 `input[type=color]`；本地托管不依赖 CDN |
| [Color Thief](https://github.com/monty-dev/colorthief) | MIT | 主流 | 从图片取主色的 median-cut 思路；实现改用 Pillow 自带 `quantize`，**不新增依赖**（pic 包只准引 Pillow） |
| [DashPalette](https://github.com/OriC28/DashPalette) / [JustColor](https://gitee.com/wanjinyin2016_admin/just-color) | — | 2026 | 色板缩略图卡片、实时预览交互 |
| [Color Toolkit](https://coloraccy.com/) | — | 2026 | WCAG 对比检查 + 多格式导出 |

> 许可合规：Coloris（MIT）可放心分发，已在 `pic/static/` 保留其版权头；tweakcn（Apache-2.0）仅借鉴交互设计，未复制其源码；Color Thief 仅借鉴算法思路（Pillow 原生实现）。

#### 2026-09-24 UI 人性化优化（二次调研 + 搬运）

用户反馈"UI 设计 / 展示 / 操作不够人性化"，二次检索 2026 已落地方案后做了四类增量（**纯前端、零后端改动、不新增依赖**，仍只用本地 Coloris + 原生 JS）：

| 新增能力 | 搬运来源 | 解决什么痛点 |
|---|---|---|
| **快速风格**：一键套用 6 种氛围（霓虹 / 柔光 / 纸感 / 暗夜 / 糖果 / 赛博），每种只给 5 个基色、其余按"暗/亮"语义自动调和整套配色 | [TweakCN](https://github.com/jnsahaj/tweakcn) 精选调色板 + [Palette Forge](https://github.com/tweakyourpc/palette-forge) 语义调和 | 非设计用户面对 17 个孤立滑块无从下手；现在 1 键出好效果 |
| **色觉缺陷模拟**：预览图套 SVG feColorMatrix 滤镜（红/绿/蓝色盲），检查配色对色盲用户是否仍清晰 | [Palette Forge](https://github.com/tweakyourpc/palette-forge) 色觉缺陷模拟 | 配色只看自己眼睛，可能误伤红绿色盲用户 |
| **多格式导出**：主题 JSON（含布局）/ CSS 变量（`:root`）/ 仅配色 JSON 三选一 | [Theme Creator](https://github.com/naes993/theme-creator) 多格式导出 | 原来只有一种 JSON，前端项目想直接拿变量用很麻烦 |
| **视觉打磨**：暗色玻璃质感面板、分组头/预设卡 hover 上浮反馈、暗色滚动条、色块行加可见色板块（点一下即开拾色器） | TweakCN / [Shadcn Studio](https://github.com/jnsahaj/tweakcn) 观感 | 原编辑器偏" utilitarian"，观感与"展示"不够友好 |

> 许可合规：上述均为交互/观感思路搬运，未复制第三方源码；编辑器红线不变——仍仅本地 Coloris + 原生 JS，零新依赖、零 CDN。

#### 验收

- 后端：`tests/test_theme_layout.py`（23 项）+ `tests/test_bg_overrides.py`（18 项）+ `tests/test_pic_custom_theme.py` + `tests/test_theme_colors_backcompat.py`——校验夹取、老主题零迁移、四模板预览、**形状参数真的影响像素**、卡片专属背景、link 兼容
- 前端：`tests/verify_qq_theme_editor.mjs`（Playwright，**61 项断言，需 bridge 在 8080 运行**）——基础结构无 JS 报错、快速风格套用（6 卡 + 柔光落色）、色觉模拟滤镜生效与清除、多格式导出（CSS 下载）、分组折叠、撤销、固定风格卡 ov: 专属配色（浮窗初始色正确 / 覆盖优先 / 写入 bg_overrides / 不误伤其它卡）、**背景适配弹窗**（16 个散装细节字段已收进弹窗、上传入口仍在、无图守卫、打开→平铺禁用缩放/对齐/透明度→裁满启用→九宫格对齐写入→放大缩放写回裁切→重置→关闭全链路无 JS 报错）、**全量自由编辑 + 吸色器**（每处色板有 💧、吸色写回、预览取色降级会话、ek 热区拖拽/手柄渲染、真实鼠标拖拽写回 elem_pos、缩放写回 elem_scl/title_fs、拖回原位清除偏移）、**选择项目图片**（弹窗取主项目成图、缩略图绝对地址、最新/最早排序、文生图/图生图分类、关键词筛选、翻页不重复、点击导入并写回卡片专属背景、验收后清理临时资源）

## 测试

```powershell
cd D:\AI\manju_agent\qq_bridge
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe tools/check_bridge_sanity.py           # 部署形态门禁，exit 1 = 不合格
.\.venv\Scripts\python.exe tools/check_bridge_sanity.py --selftest # 验证门禁本身有效
```

> **测试不会污染生产日志**：`config.LOG_DIR` 在 import 时读 `QQ_LOG_DIR`，
> `tests/conftest.py` 由 pytest 最先加载并把它指向临时目录。独立脚本
> `src/plugins/*/test_e2e.py` 不吃 conftest，自带改道。
> 2026-11-05 之前不是这样：`tests/test_plan_commands.py` 用真实群号 `1075851417`
> 伪造群事件，pytest 一跑就把假消息写进生产 `logs/qq_bridge.log`，排查时与真群消息
> 无法区分，差点被判成「群聊总闸被绕过」。门禁 P3 守这条。

- `tests/test_dispatcher.py`、`test_intent.py`、`test_ratelimit_session_safety.py`、`test_taskmgr.py`
  —— 纯逻辑验收，用 `httpx.MockTransport` 模拟主项目后端，**不需要真实 7788**。
- `tests/test_choice_session.py` —— 生图通道交互式选择状态机（ask/answer/consume + 超时容错），纯逻辑。
- `tests/test_taskmgr_local.py` —— 本地通道契约：提交体字段、模板参数跟随（防蒸馏模型被套 20/7.0）、
  QQ 端点优先与 404 回退、提交前等平台空闲、排队超时不提交。
- `tests/test_e2e_local_t2i.py` —— 真实生图（打真实 7788 + 8188，占 GPU ~1-2 分钟）。
  默认跳过，需显式开启：`$env:MANJU_E2E_LOCAL="1"`。
- `tests/test_e2e_ws.py` —— 端到端：真起 `bot.py`（独立端口 18080）+ 模拟协议端 WS 连上来收发消息。

## 已知未完成项（诚实声明）

1. **视频能力未开通**：`/qq/video-generate` 端点在主项目后端**尚未实现**（设计文档 §7.4 方案 B）。
   当前 `/video` 会明确回复「尚未开通」，不会假装生成，也不会绕过积分/安全直调底层。
2. **出站图像安全审核未接入**：`safety.check_media()` 目前只做协议/扩展名准入校验，
   **不具备 NSFW 判别能力**。对外服务前需接入腾讯云/阿里云内容安全（文档 §7.2）。
3. **积分退还未接线**：失败路径已退当日配额（`quota_refund`，CLOSE-04 精神），
   但若对接真实计费，需在方案 B 端点内统一处理扣减/退还。

## 待你确认（设计文档 §13）

1. 对外范围：仅白名单好友/群（建议），还是公开？
2. 默认生图通道：在线（当前默认，推荐）还是本地 ComfyUI（独占，需串行）？
3. 是否接受新增 `/api/v1/qq/video-generate` 端点（视频能力的前置条件）？
4. 是否需要积分/配额体系？

## 磁盘清理

历史下载残留 / 已解压完的安装包 / 被替代的旧文件，**已改名 `.to-delete`，可自行删除**：

```
napcat_node_dl.to-delete  node20.to-delete  node20_dl.to-delete
node22_dl.to-delete       qq_install.to-delete
_e2e_ws_test.py.to-delete            # 已被 tests/test_e2e_ws.py 取代
src/plugins/echo.to-delete           # 早期连通性测试插件，已与主插件重复
```

**2026-10-29 已彻底删除（不可恢复，也不需要恢复）**：

```
napcat/                  120MB 注入版 NapCat（NapCatWinBootHook.dll / NapCatWinBootMain.exe /
                         napcat.mjs / node_modules 等），与容器版抢同一账号 → 互踢根因
onekey/                  3.3MB 注入版一键安装包（NapCatInstaller.exe / OneKey.zip / 7z.exe）
onekey/bootmain/         注入版启动器 + napcat.bat / napcat.quick.bat / napcat.kill.qq.bat
stop_all_hard.ps1        其 -Hard 语义已并入 stop_all.ps1
_reboot_napcat.ps1       stop_all + start_all 的包装，现改用 docker restart 或 start_all.ps1
start_all - 副本.ps1     历史残留副本
```

NapCat 本体如需获取，请用容器镜像 `mlikiowa/napcat-docker`，**不要再从任何渠道恢复注入版**。

## 卡片专属背景（2026-09-23 新增）

此前 `bg_image` 是**主题级**字段——所有卡片共用一张背景图。用户给「裂隙卡」
上传背景图后，攻略卡背景也跟着变（误伤）。现在支持**按卡片覆盖背景**：

- 主题 JSON 的 `layout.bg_overrides`：`{"fissure": {"bg_pattern": "image", "bg_image": "xxx.png"}}`；
- 编辑器里**在对应卡片页签下**上传/生图背景图，自动写入该卡的专属覆盖（不再动全局），
  面板显示「XX 使用专属背景 [清除]」，一键回落全局；
- 渲染时 `decor.background(c, t, tpl=模板名)`：专属覆盖优先，缺失键回落主题级全局背景；
- **无创原则**：固定风格卡（裂隙卡 fissure）仅当显式配了专属背景才画背景——
  全局背景图案（neon 的点阵/light 的贴纸平铺）**不会**改变裂隙卡现有观感；
- 存量主题零迁移：没有 `bg_overrides` 的主题行为与旧版完全一致。

验收：`tests/test_bg_overrides.py`（18 项）——专属覆盖只影响对应模板（攻略卡渲染逐字节不变）、
裂隙卡不吃全局背景（无创回归）、配了专属背景必须真的生效、校验层清洗非法键/路径穿越、
cs1 存量数据加载冒烟、**固定风格卡专属配色**（fissure / netracell 的 ov: 语义色键：生效/不误伤/
渲染逐字节兼容/热区 regions 与 values 契约，共 8 项）。运行：
`cd qq_bridge && .venv\Scripts\python.exe -m pytest tests/test_bg_overrides.py -v`

## 固定风格卡专属配色（2026-09-24 新增）

两类固定风格卡——**虚空裂隙卡（fissure）**与**科研任务卡（netracell）**——照 Rabbit 样本的
浅色白卡、固定配色（不随主题深/浅切换）。此前在主题编辑器里**只能改背景、其它元素（标题/文字/
气泡/节点/倒计时/分割线）点不下去**，因为这两张卡模板没有注册可编辑热区（add_region），且后端预览
没有示例参数、netracell 还漏接了背景。

修复（**无创增量，固定风格卡观感零回退**）：

- 模板各自新增一份 `_COLOR_MAP`（语义色键 → 硬编码默认），渲染时 `_pal(t)` 解析
  「专属覆盖 > 默认」——`bg_overrides[fissure]/[netracell]` 里没有的键**回落原硬编码色**，
  故不配时渲染与旧版**逐字节一致**；
- 大量 `add_region("ov:xxx", …)` 注册可点击热区（卡片壳/标题/区块标题/高亮词/任务行配色/
  气泡/节点/倒计时/分割线/剩余时间…），每个区域带 `values`（当前生效色，供浮窗显示）；
- 后端 `_parse_preview` 补两类卡的示例参数，预览有真实内容可点；
- 前端 `editor_page.html`：新增 `ovColorOf`/`setOvColor`，`setColor` 的 `ov:` 分支，
  `ELEMENT_CONTROLS` 的 `ov:card_color`/`ov:bubble_bg` 多色弹窗，`controlsFor` 的 `ov:` 回退，
  `OV_LABELS` 中文名——点预览元素即改对应专属色，写入 `bg_overrides[本卡]`，**只影响本卡**；
- `TEMPLATE_LABELS` 补「虚空裂隙卡 / 科研任务卡」中文 tab 名（此前英文 key 直接暴露）。

**生效方式（重要）**：`editor_page.html` 每次 GET 实时读盘 → 前端改动**刷新即生效**；
但模板/`themes_store`/`editor.py` 是 Python，需**重启 bridge** 才会加载。
容器路线：`docker compose -f docker-compose.bridge.yml restart qq-bridge`
（改了代码才需 `up -d --build`）。宿主机回退路线才是 `stop_bridge.ps1` → `start_bridge.ps1`。

验收：

- 后端：`tests/test_bg_overrides.py`（见上，含 fissure/netracell 专属配色 8 项）；
- 前端：`tests/verify_qq_theme_editor.mjs` 的 `[固定风格卡 ov: 专属配色]` 段（5 项，含浮窗初始色
  显示区域生效色而非 #000000 的回归）。

### 卡片底色透明度（2026-09-24 用户反馈修复）

用户反馈：点 fissure / netracell 卡片壳弹窗，「明明能改底色颜色，但调不了透明度，且定位不到卡片底色元素」
（弹窗底部 toast「右侧面板未找到参数：ov:card_color」）。两个问题的根因与修复：

1. **没有透明度滑块**：`card_alpha`（卡片底色透明度 0-100%）是整型布局参数，`bg_overrides` 清洗层此前只认
   颜色键与背景键，没把它纳入白名单；前端 `ELEMENT_CONTROLS['ov:card_color']` 也只有「底色/描边」两色行，
   slider 无来源。
   - `themes_store.py`：新增 `_OVERRIDE_INT_KEYS = frozenset({"card_alpha"})` + `_clean_bg_overrides`
     清洗分支（0-100 夹取，非法剥掉）；
   - `fissure.py` / `netracell.py`：新增 `_card_alpha(t)`（缺省 100、越界/非法回落 100），卡片壳
     `rounded_rect(..., fill_alpha=ca*255//100)`（<100 走 alpha_composite 真半透明，露出背景图案/背景图）；
     `ov:card_color` 区域的 `values` 带 `ov:card_alpha`；
   - `editor_page.html`：新增 `setOvLayout`/`ovLayoutOf`，`ELEMENT_CONTROLS['ov:card_color']` 加
     `['slider','ov:card_alpha','底色透明度']`，`buildPopRow` 的 slider 分支支持 `ov:` 键
     （默认 0-100、写入 `bg_overrides[本卡]`）。
2. **定位 toast 报错**：弹窗底部「看全部参数 →」对 `ov:card_color` 执行 `focusField('ov:card_color')`，
   但右侧面板没有这个字段 → toast「未找到参数」。修复：`openPop` 对 `ov:` 前缀字段**不显示**该按钮
   （浮窗本身就是该元素的完整控制集合）。顺带修了一处重构引入的 JS `ReferenceError`
   （`ft.appendChild(adv)` 引用了块级作用域外的 `adv`），以及一处后端隐患：netracell 卡片壳矩形误用了
   未定义的 `card_x0`/`card_y0`（渲染会 `NameError`），补为 `14, 14`（与 fissure 一致，缺省观感逐字节不变）。

验收（后端）：`tests/test_bg_overrides.py` 现 **18 项**（原 15 + 本次 3：card_alpha 校验夹取 /
生效且缺省逐字节一致且不误伤 / `_card_alpha` 夹取 helper），全部通过；`py_compile` 3 文件 OK。

### 背景适配弹窗（2026-09-24 用户反馈重构）

用户反馈：上传图片后，右栏的「背景适应 / 缩放 / 位置 / 比例 / 裁切 / 透明度 / 叠加底色」是一堆孤立滑块，
"对不上画面、太难用、不够人性化"。把这些细碎参数重构成一个**可视化、可拖拽、可滚轮缩放、可九宫格对齐**的友好弹窗。

**交互（点上传行下方的「🖼️ 背景适配调整…（位置 / 缩放 / 裁切）」按钮打开）**：
- **大图实时预览**：直接**拖动平移取景**、**滚轮缩放**（围绕取景中心，放大=只取图片局部）；
- **左下原图取景小图**：**点哪取哪**（把点击点设为取景中心），所见即所得；
- **右栏**：适应方式分段（平铺 / 裁满 / 完整 / 拉伸）+ 九宫格对齐（↖↑↗←·→↙↓↘）+ 缩放滑杆 + 不透明度 + 叠加底色开关；
- **联动约束**：平铺模式无缩放概念 → 自动禁用 缩放/对齐/透明度；拉伸模式无对齐 → 对齐按钮禁用；
- **一键恢复默认**；**Esc / 点遮罩 / 完成**关闭。

**与后端渲染逐条对齐**（保证"所见即所得"）：
- 裁切 = 原图百分比窗口（`widgets._process_fill_image`）；
- `cover`/`contain`/`fill` 缩放 + 九宫格偏移（`widgets._pos_offset`）；
- `tile` = 整图居中裁正方形后按画布等比贴纸（透明度=贴纸透明度 `decor._alpha`，后端 tile 分支提前 return 无叠加底色）；
- `opacity < 100` 乘到整层；`overlay` = 叠页面/卡片底色（110/255）盖顶（`decor._bg_image`）。

**写回语义（无创）**：走 `setLayout` / `setOvLayout`（卡片专属覆盖 > 全局），每项可 Ctrl+Z 撤销，
实时 `schedulePreview` 同步背后真实预览；右栏 16 个散装字段（`bg_image_*` / `card_image_*` 的细节键）
由 `BF_HIDDEN` 集合拦截、不再散装渲染，但 `bg_image` / `card_image` 上传入口本身永远保留。

**纯前端改动**：`editor_page.html` 6 处（CSS / 弹窗 DOM / `BF_HIDDEN` 隐藏散装字段 / 上传行换入口 /
`attachCropper` 替换为完整弹窗函数集 `openBgFitDialog`·`bfShow`·`bfBindOnce`·`bfBuildOps`·`bfSyncAll`·`bfDraw` /
浮窗内增加入口）。**零后端改动、零新依赖**，刷新即生效，不触碰 P1 资产流水线。

验收（前端）：`tests/verify_qq_theme_editor.mjs` 新增 `[背景适配弹窗]` 段 **15 项**全部通过，
全量 **36/36 通过**（exit 0，bridge 在 8080 运行、HTML GET 即时生效）。

### 全量自由编辑 + 吸色器（2026-09-24 用户反馈）

用户反馈：这类固定尺寸输出卡片希望「全量编辑——拖动任意元素位置、任意元素大小调整」，并给每一处色板加吸色器。

**① 全量自由编辑（固定风格卡：虚空裂隙 / 科研任务）**
- 数据模型（无创增量，沿用 bg_overrides 专属覆盖）：`bg_overrides[tpl].elem_pos = {元素键:[dx,dy]}`（像素平移，
  不参与排版流，元素允许重叠——自由摆放是用户的选择）；`bg_overrides[tpl].elem_scl = {元素键:百分比}`（30~300%，
  **参与排版流**：字号/行高/气泡折行随缩放重算，卡片高度自适应）。
- 渲染端（`decor.elem_tf(t, tpl)` 共用助手）：每个元素（标题/分割线/说明/分组/任务行/气泡/剩余时间/提示行…逐实例）
  独立应用偏移与缩放；**未配置时渲染与旧版逐字节一致**（pytest 锁定）。热区带 `ek` 元素键（如 `title`/`bub-0-1`）。
- 前端交互：预览热区**拖动移动**（网格吸附沿用）、**右下角橙色手柄拖动缩放**、松手写回 `elem_pos/elem_scl`
  （每步可 Ctrl+Z）、**拖回原位 = 清除偏移**；夹取保证元素留在画布内。流程卡（text/quota/progress/guide）的
  标题/来源新增缩放手柄（写回 `title_fs/source_fs`，拖动本就有）。
- 清洗层（`themes_store._clean_elem_pos/_scl`）：键白名单、坐标 ±4000 夹取、缩放 30~300 夹取、
  `(0,0)/100` 不落盘、非法整条剥掉（防任意注入口）。
- 诚实边界：guide/text/quota/progress 等流式模板的正文元素暂无独立定位字段（拖动/缩放当前覆盖
  固定风格卡全部元素 + 流式卡的标题/来源字号）；后端模板改动**需重启 bridge 生效**。

**② 吸色器（每一处色板）**
- 右栏全部颜色行、浮窗颜色行（含 ov: 专属配色）、布局颜色行、文字样式描边色，一律带 **💧 吸色按钮**。
- 优先 **EyeDropper API**（Chromium/Edge/Chrome，全屏幕取色）；不支持时自动降级「**点击预览图取色**」
  （预览 PNG 画进离屏 canvas 采样像素，data-URL 同源不污染画布；Esc 或再点 💧 取消）。
- 顺带修复（Playwright 验出）：拖回原位未真正清除已存偏移（下次渲染会跳回旧位置）。

验收：
- 后端：`tests/test_bg_overrides.py` 现 **23 项**（新增 5：elem_tf 清洗夹取 / fissure 偏移+缩放生效且热区平移 /
  缺省逐字节一致且不误伤 / netracell 气泡独立缩放 / 全元素 ek 契约）；pic 相关回归 **200 passed**
  （唯一失败为预存的菜单图 211KB>200KB 体积预算，与本轮无关）；`py_compile` 4 文件 OK。
- 前端：`tests/verify_qq_theme_editor.mjs` 新增 `[全量自由编辑 + 吸色器]` 段 **10 项**，全量随后 **61/61 通过**（exit 0）。

### 主题持久化修复 + 选择项目图片（2026-09-24 用户反馈）

**① BUG：重启服务后，之前应用好的主题变回默认**

- 根因：激活态只有 `.env` 的 `PIC_CUSTOM_THEME` **一个通道**。它有三类失效场景——① 启动时 dotenv
  没把 `.env` 灌进 `os.environ`（换启动方式 / cwd 变化，`find_dotenv` 找不到）；② 键被别的写
  `.env` 的通道覆盖成空串；③ `.env` 写失败（占用 / 只读）。任一命中 → 主题回落默认。
- 修复：**双写双通道**。编辑器「应用 / 取消」同时写 `.env` 与 `data/pic_active_theme.json`
  （`theme.write_active_theme`，原子替换）；解析侧 `theme._config_theme()` 在 **env 通道完全没有这个键**
  时才回落到文件——键存在但为空 = 用户在配置页显式清空，**不复活**，两个写入口保持同一语义。
- 顺带修掉一个真实污染源：`tests/test_pic_custom_theme.py` 的 activate/deactivate 此前直接写生产
  `.env`，跑一次测试就把用户已应用的主题清成空。现在 `.env` 路径提为模块级 `editor.ENV_PATH`
  可被 monkeypatch，用例一律指向临时目录（同 fixture 也修了 `editor.theme_store` 这个名字绑定，
  否则 PUT/DELETE 会打到真实 `data/themes/`）。
- 实测：删掉 `.env` 里的 `PIC_CUSTOM_THEME` 行后重启 bridge，`GET /api/pic/themes` 的 `active`
  仍为 `cs1`（兜底文件生效）；还原 `.env` 后逐字节一致。

**② 新增：设置背景图处「🖼项目图」——直接选主项目已生成的图**

- 入口：每个图片资源行（背景图 / 卡片背景图 / 贴纸 / 角落贴纸）在「⬆上传」旁新增 **🖼项目图**。
- 后端：复用 `gallery` 的 HTTP 单向契约（绝不 import 主项目模块）。
  - `GET /api/pic/project-images?type=&keyword=&sort=&page=&size=` —— 调主项目 `/t2i/tasks`，
    **过滤回收站图片**（`deleted_at` 非空一律剔除）、无图任务剔除；分类交给服务端（total 才准），
    关键词 / 排序 / 分页在本地做（并发翻页扫描最近 800 张，超出部分响应里带 `capped` 如实提示）。
  - `POST /api/pic/project-images/import {url}` —— 下载并存进主题资源库，**SSRF 闸门只放行主项目
    自己的 host**；大图走 `assets.save_scaled`（先等比缩到 ≤2048px，压不下再转 JPEG，实在不行才报错），
    与用户上传的严格 2MB 上限分开，避免"项目成图本来就 2MB+，一上传就被拒"。
- 前端弹窗：分类（全部 / 文生图 / 图生图）、排序（最新 / 最早优先）、关键词搜索、上一页/下一页、
  缩略图网格（带「生图 / 改图」角标 + 提示词 + 时间）；选中即导入并走与上传完全相同的写回路径
  （`applyAssetFilename`，背景图写当前卡片的 `bg_overrides[tpl]`）。主项目不可达时弹窗内给中文提示，不抛 JS 报错。

验收：
- 后端：新增 `tests/test_pic_project_images.py` **16 项**（回收站过滤、thumb 绝对地址、筛选/排序/分页、
  多页扫描、主项目不可达降级、SSRF 闸门、大图缩放/转 JPEG/超限报错、两条路由的成败路径）；
  `tests/test_pic_custom_theme.py` 新增 **6 项**持久化用例（含 `.env` 写失败时兜底顶上）。
  合计 **40 passed**；pic 相关回归 **233 passed**（2 项失败均为沙箱阻止批量删缓存 / 预存的
  菜单图体积预算，与本轮无关）。
- 前端：`tests/verify_qq_theme_editor.mjs` 新增 `[选择项目图片]` 段 **15 项**，全量 **61/61 通过**（exit 0）。

## 图片批量放大计划（任务中心 · 2026-09-12 新增）

「QQ 任务中心 → 任务计划」新增第二种计划类型 **图片批量放大**：把主项目历史出图
（文生图 / 图生图结果）成批送本地 ComfyUI 高清放大，队列依次执行、不挤占网页端。

### 用法

1. 任务中心 → 任务计划 → 计划类型切到「图片批量放大」；
2. 点「🖼️ 从历史选择要放大的图片」打开选择器：
   - **筛选**：任务类型（全部/文生图/图生图）、日期（今天/近3天/近7天/自定义区间）、
     文件大小（≥/≤ MB）、分辨率（宽≥/高≥）、提示词关键词（类型切换会重新拉取，其余为客户端过滤）；
   - **多选**（Google Photos 范式）：单击勾选/取消、**Shift+单击连选**、**Ctrl+A 全选筛选结果**、
     「全选筛选结果 / 清空选择」按钮、底栏浮动操作栏「已选 N 张 → 加入计划」；
   - 底部「加载更多」按页追加（每页 100 张），计数实时显示「已加载 / 筛选后」；
3. 选 2× / 4× 倍数与**放大模板** → 「开始执行计划」→ bridge 队列逐张执行，进度在队列/历史里看。
   - **放大模板**下拉：默认「内置放大模型」（不传 template_id，主项目自动选 4x-UltraSharp/RealESRGAN）；
     或选导入的 ComfyUI 模板——**只列 `mode=custom` 且工作流含 LoadImage 节点的**
     （按 node_summary.class_type 判定，文生图模板 EmptyLatentImage 无图入会被剔除），
     如「传图的高清放大-ZIT」。选中项失效自动回落默认；模板随计划项 `模板` 字段下发。

### 执行链（不改主项目一行）

计划项 `{"类型":"放大","图片":"/file?name=…","倍数":2}` → `POST /api/qq/queue/batch` →
bridge 队列（`queue/__init__.py` 新 kind=upscale）→ 下载原图（`_download_image`，拼主项目站点根）→
`multipart POST /t2i/edit mode=upscale`（复用主项目现有放大链路：模板优先，无模板走
4x-UltraSharp/RealESRGAN 自动选择）→ 复用 `_wait_task` 轮询至终态。
放大只支持本地通道（`/t2i/edit` 是本地端点），channel 强制 local；失败路径全部落
`job["error"]` 并通知（CLOSE-02），下载/提交/超时各自有明确错误文案。

顺带修复 `_extract_images`：本地 t2i 结果的 images 是 `{url,width,height,...}` dict 形态，
此前会被 `str(dict)` 变成乱串——现取 `im["url"]`。

### 调研来源（2026-09-12 检索）

| 案例 | 许可 | 借鉴/取舍 |
|---|---|---|
| Google Photos 选择范式（2026 相册组件事实标准） | 思路 | hover 勾选圈 + 单击切换 + Shift 范围连选 + 底部浮动操作栏 |
| [DragSelect](https://github.com/ThibaultJanBeyer/DragSelect)（dragselect.com，2026-08 活跃） | MIT | 拖拽框选库。**评估后未引入**：动态分页网格 + 滚动容器下与图片原生拖拽/滚动冲突风险高，Shift 连选 + Ctrl+A 已覆盖批量诉求 |
| Gmail / Windows 资源管理器多选惯例 | 思路 | Ctrl+A 全选筛选结果、锚点+范围语义 |

### 验收

- 后端：`qq_bridge/tests/test_queue_upscale.py`（12 项）——计划解析（中文键/缺图片拒绝/
  倍数收敛 2|4/t2i 回归保护）+ MockTransport 端到端（下载原图→multipart 提交→轮询 done→
  抽结果图）+ 下载 404 失败路径。运行：
  `cd qq_bridge && .venv\Scripts\python.exe -m pytest tests/test_queue_upscale.py -v`
- 前端：`tests/verify_qq_upscale_picker.mjs`（24 项）——类型切换/选择器加载/关键词筛选空态/
  单击+Shift 连选+Ctrl+A/浮动栏/加入计划预览/通道语义/回归。运行：`node tests/verify_qq_upscale_picker.mjs`
- 已知坑（维护必读）：选择器网格**必须固定行高**（`grid-auto-rows:150px`）——
  `aspect-ratio` + `img height:100%` 存在 Grid 轨道尺寸循环依赖，行会坍缩导致卡片相互重叠
  （点击被邻格拦截）。

## 生图通道交互式选择（新增 · 2026-09-05）

群聊发「生图 描述」**不再直接提交**，而是先反问「在线还是本地」，等你选完再提交——避免误用通道。

- 触发：群里 `@机器人 生图 一本书`（或主人号直接发「生图 一本书」）
- 机器人反问：`🎨 生图通道选哪个？回复【1】本地(ComfyUI·竖屏) 或【2】在线(云端)。60秒内不选自动取消`
- 回复 `1`/`本地`/`local` 或 `2`/`在线`/`云端`/`online` → 按所选通道提交（本地走 ComfyUI，在线走云端）
- 超时（默认 60s，`.env` 设 `CHOICE_TIMEOUT` 可调）未选 → 自动取消并提示，**不堆积半开对话**（OPS-02）
- 选择期间再发「生图 描述」会覆盖上一次选择

设计借鉴（检索 CSDN / 官方文档 / gethub「拿来用」）：
- nonebot-plugin-waiter：等待用户输入 + 超时即取消
- NoneBot2 官方 session-control：每用户独立会话作用域
本实现为贴合「每条消息独立 handler」事件驱动模型的轻量自实现（状态机见
`src/plugins/qq_bridge/session/choice.py`），不引入重依赖、易单测。


## 提交门禁（Commit Size Gate）· 2026-11-07

> **生效时间：2026-11-07 17:35（北京时间）** · 维护者：qq_bridge 主题编辑器开发者

仓库现为 **public**。原因：GitHub 把「强制状态检查（required status checks）」这类门禁能力在私有仓上划在 **GitHub Pro 付费档**，只有升 Pro 或把仓库转公开才解锁。为拿到免费的真·硬墙，本仓转为公开。

### 门禁是什么

- **Repository Ruleset** `block-oversized-code`（id `26752150`，enforcement `active`）：作用于 `main` 分支，强制要求 `gate` 状态检查通过，否则**禁止合入、禁止直推**。
- 检查器是 GitHub Actions 工作流 `.github/workflows/code-size-gate.yml` + `scripts/code_size_gate.py`：每次 push / 开 PR 到 `main` 时运行，扫描代码文件，**>20KB 即拦截**；放行 lockfile（package-lock / yarn / pnpm / Cargo.lock / go.sum 等）与自动生成/打包产物（*.min.js、*.bundle.*、*.d.ts、*.map、vendor / node_modules / dist / build / __pycache__）。命中时提示「按功能拆分」。

### 实测（prove-it · 2026-11-07）

| 场景 | gate 结论 | 结果 |
|---|---|---|
| 违规 PR（>20KB `.py`） | failure | merge 返回 **HTTP 405** `Required status check "gate" is failing` → 被拦 |
| 合规 PR（<20KB `.py`） | success | merge 返回 **HTTP 200** merged → 放行 |
| 直推 `main`（>20KB `.py`） | — | ref 更新返回 **HTTP 422** `Required status check "gate" is expected` → 被拦 |

结论：**无论走 PR 还是直推 `main`，超限代码都会被强制拦下**；合规的小文件正常通过。

### 注意事项

- 本硬墙依赖「仓库公开」。若把仓库改回 private 且未订阅 GitHub Pro，该 Ruleset 将无法启用（GitHub 会要求升级 Pro）——届时墙退化为「亮红灯但不拦截」的可见墙。
- 本地已删除的 `.githooks/pre-commit` 不参与此门禁；唯一生效的是上述服务端 Ruleset。
- 公开副作用：`msyh.ttc`（19.7MB）、`aria2c`（14MB）等大文件随之对全网可见。


## 仓库地址与访问凭证

- **仓库地址（public）**：https://github.com/LiuHao2611/qq_bridge
- **克隆地址**：https://github.com/LiuHao2611/qq_bridge.git

### 访问凭证（安全必读）

> ⚠️ 本仓库为 **public**。**切勿把任何真实 Token / 密钥明文写进仓库（含本文件）**——会被全网爬取滥用。
> 凭证只应存在于 **GitHub Actions Secrets** 或 **本地环境变量**，绝不明文落库。

- 本地 CLI：`gh auth login` 登录，或 `export GH_TOKEN=<GITHUB_TOKEN>` 提供写权限；
- CI 门禁：工作流通过 GitHub 自动注入的 `GITHUB_TOKEN` 运行，无需任何额外密钥；
- 写权限 PAT：在 `Settings → Secrets and variables → Actions` 配置，引用时写 `${{ secrets.GH_TOKEN }}`，**明文禁止落库**。

> 本项目使用过的 PAT 已在外部分享环境出现，建议立即到 GitHub `Settings → Developer settings → Personal access tokens` **撤销并重新生成**，旧令牌一律作废。
