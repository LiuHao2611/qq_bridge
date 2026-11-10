# ============================================================================
# 一键启动 QQ 机器人全套服务：NapCat（Docker 容器）+ bridge（宿主机 8080）
# ----------------------------------------------------------------------------
# 【2026-10-29 架构重构】本脚本原为「Windows 注入版 NapCat」启动器，已彻底废弃。
#
# 为什么废弃注入版（这条务必读完再改代码）：
#   同一账号绝不允许两份 NapCat 同时在线。注入版与容器版各持一份登录票据，
#   服务端判定会话冲突后互相踢下线，并把设备标记为「风险设备」，表现为
#   【数小时被踢一次 → 间隔越来越短 → 最终登录票据被作废、只能重新扫码】。
#
#   2026-10-28 实测曲线（数据来自 logs/qq_bridge.log 的 connector_attached/detached）：
#     10-27 16:18 → 10-28 09:34  在线 17.25h（当时只跑容器版，无冲突）
#     10-28 14:31 重登后          仅 2.9h
#     之后 17:23 / 17:47 / 18:34 / 20:21 / 21:07 / 02:05 连续断开 6 次
#     单段在线时长从 17h 一路掉到 4.9 分钟
#   原 qq_bridge/napcat/ 下 120MB 注入版文件已于 2026-10-29 删除，请勿从任何来源恢复。
#
# 现在的唯一拓扑：
#   NapCat → docker 容器 qq-bridge-napcat（由 docker-compose.napcat.yml 管理）
#            · 镜像是自建的 qq-bridge-napcat:launcher-v4.18.41，不是上游 latest
#              构建源见 docker/Dockerfile.napcat-launcher：base 钉版本 + 把上游
#              用 sed 改过的 QQ package.json 还原成腾讯原值，改由 LD_PRELOAD
#              在运行时用 memfd 伪造 —— 磁盘零篡改，见该文件顶部完整说明
#            · 登录态持久化在 ./data/qq 卷，容器重启不丢
#            · 设备身份四要素（hostname / MAC / machine-id / 内网 IP）已在 compose 内钉死
#            · 日常只 `docker restart`，绝不 `up -d` 重建 —— 重建会被判「新设备」，要求重新扫码
#   bridge → 容器 qq-bridge，发布 8080（由 docker-compose.bridge.yml 管理）
#            ★ 2026-11-05 收口：宿主 python 路线已废。两条路线同绑 8080 会被
#              Windows 的 SO_REUSEADDR 双绑成功，流量归属不确定；实测宿主实例
#              会永久 ws_down 却一直占着端口。start_bridge.ps1 现在遇到容器在跑
#              就拒绝启动，只作为回退通道保留。
#
# 【铁律】账号安全 >> 服务可用性（2026-09-16 封号事故后确立）
#   1. 登录账号唯一来源 = 容器环境变量 ACCOUNT（见 docker-compose.napcat.yml），本脚本不碰。
#   2. 严禁硬编码 QQ 明文密码 / MD5。
#   3. 严禁自动重登：出现 需扫码 / KickedOffline / 被封 等信号必须立即熔断，只告警不重试。
#      频繁重登是 QQ 风控最高危行为，也是那次永久封号的直接根因。
#   4. 账号 3858741376 已于 2026-09-16 被封，永久禁止再登录。
#
# 用法： .\start_all.ps1
# 幂等：容器已在跑就跳过（不打断 QQ-NT 会话）；bridge 若在跑则先停后起（加载最新代码）。
# ============================================================================
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$container = 'qq-bridge-napcat'
$composeFile = Join-Path $root 'docker-compose.napcat.yml'
$composeEnv = Join-Path $root 'data\compose.env'

# 监听端口（默认 8080，与 start_bridge.ps1 / stop_bridge.ps1 同源解析）
$port = 8080
$envFile = Join-Path $root '.env'

# ----------------------------------------------------------------------------
# 账号单一来源：从 .env 读 BOT_UIN 并导出到 compose 进程环境
# ----------------------------------------------------------------------------
# docker-compose.napcat.yml 的 ACCOUNT 用 ${BOT_UIN:?} 插值。compose 传 --env-file
# data\compose.env 时会【遮蔽】项目 .env（实测），导致 ${BOT_UIN} 取空 —— 那样
# NapCat 会拿空号登录，是高频重登/封号的诱因。所以这里显式读 .env 的 BOT_UIN
# 导出到进程环境（优先级高于 --env-file），缺值立即报错，绝不静默往下走。
if (-not (Test-Path $envFile)) {
    Write-Host "[登录号][错误] 缺少 $envFile，无法读取 BOT_UIN。请先复制 .env.example 为 .env 并填写。"
    exit 1
}
$botUinMatch = Select-String -Path $envFile -Pattern '^\s*BOT_UIN\s*=\s*(\d+)' | Select-Object -First 1
if (-not $botUinMatch) {
    Write-Host "[登录号][错误] .env 缺少 BOT_UIN=<机器人QQ号>。换号只改这一行，禁止写死在代码/compose。"
    exit 1
}
$env:BOT_UIN = $botUinMatch.Matches[0].Groups[1].Value
Write-Host "[登录号] BOT_UIN=$env:BOT_UIN（唯一来源：.env；换号只改那里）"

if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}

# ----------------------------------------------------------------------------
# 1. NapCat 容器
# ----------------------------------------------------------------------------
# 关键：已在运行就绝对不碰。重启容器会杀掉 QQ-NT 进程，即使登录态仍在卷里，
#       quick-login 票据也常被服务端判失效 → 又要扫码。能不动就不动。
$containerExists = $false
$containerRunning = $false
try {
    $id = & docker inspect -f '{{.Id}}' $container 2>$null
    $containerExists = ($LASTEXITCODE -eq 0 -and $id)
    if ($containerExists) {
        $running = & docker inspect -f '{{.State.Running}}' $container 2>$null
        $containerRunning = ("$running".Trim() -eq 'true')
    }
} catch { }

if ($containerRunning) {
    Write-Host "[NapCat] 容器 $container 已在运行，跳过（不重启，避免打断 QQ-NT 会话与登录态）。"
} elseif ($containerExists) {
    Write-Host "[NapCat] 容器存在但已停止 → docker start（沿用卷里的登录态，不重建、不换设备身份）。"
    & docker start $container | Out-Null
    Start-Sleep -Seconds 5
} else {
    Write-Host "[NapCat] 容器不存在 → 用 compose 首次创建。"
    if (-not (Test-Path $composeFile)) {
        Write-Host "[NapCat][错误] 缺少 $composeFile，无法创建容器。"
        exit 1
    }
    $composeArgs = @('compose', '-f', $composeFile, 'up', '-d', '--build')
    if (Test-Path $composeEnv) { $composeArgs += @('--env-file', $composeEnv) }
    & docker @composeArgs
    Start-Sleep -Seconds 8
}

# 容器状态复核（失败即停，不再往下假装成功）
try {
    $running = & docker inspect -f '{{.State.Running}}' $container 2>$null
    if ("$running".Trim() -ne 'true') {
        Write-Host "[NapCat][错误] 容器未能进入运行状态，请查看：docker logs --tail 50 $container"
        Write-Host "[NapCat][提示] 若日志出现 '需要验证码'，需在 WebUI 完成验证或扫码："
        Write-Host "                python scripts/qq_login_qr.py   # 刷新并导出二维码"
        exit 1
    }
    Write-Host "[NapCat] 容器运行中。"
} catch {
    Write-Host "[NapCat][错误] 无法读取容器状态：$($_.Exception.Message)"
    exit 1
}

# ----------------------------------------------------------------------------
# 2. bridge（容器 qq-bridge，唯一路线）
# ----------------------------------------------------------------------------
# ★ 2026-11-05 架构收口：bridge 只剩容器一条路线 ★
# 历史上这里拉的是「宿主机 python 进程（start_bridge.ps1）」，与
# docker-compose.bridge.yml 的容器路线并存。开机时两条都起 → Windows 的
# SO_REUSEADDR 允许两边都绑 0.0.0.0:8080，流量归谁不确定。实测后果：
# 宿主实例 13:15 起（一路 ws_down），容器 16:36 抢走端口，宿主从此永久
# ws_down 却一直占着 8080（bridge.err 里 conn_state_still_down 连续 3h47m），
# 还每 7 秒刷一条 aria2 HTTP 400。所以这里不再拉宿主进程。
# start_bridge.ps1 现在是「回退通道」：检测到容器在跑会直接拒绝启动。
# 改代码后要生效需 rebuild：docker compose -f docker-compose.bridge.yml up -d --build
$bridgeCompose = Join-Path $root 'docker-compose.bridge.yml'
if (-not (Test-Path $bridgeCompose)) {
    Write-Host "[bridge][错误] 缺少 $bridgeCompose，无法启动容器。"
    exit 1
}
Write-Host "[bridge] 用 compose 启动容器（幂等：配置没变就不会重建）..."
& docker compose -f $bridgeCompose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "[bridge][错误] docker compose up 失败，见上方输出。"
    exit 1
}

Write-Host ""
Write-Host "启动完成。验证方式："
Write-Host "  1) .\napcat_status.ps1          查看容器 / 连桥 / 账号登录态是否健康"
Write-Host "  2) 打开 http://127.0.0.1:8080/api/qq/status ，应返回 online:true 且 self_id 为机器人号"
Write-Host "  3) python scripts/qq_conn_report.py   查看在线时长曲线与掉线次数"
Write-Host ""
Write-Host "若 NapCat 停在等扫码状态（票据被服务端作废时会发生）："
Write-Host "  4) python scripts/qq_login_qr.py      刷新并导出可直接扫的二维码"
