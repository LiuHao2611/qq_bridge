# ============================================================================
# 停止 QQ 机器人服务：bridge（宿主机 8080）+ 可选 NapCat 容器
# ----------------------------------------------------------------------------
# 【2026-10-29 架构重构】原 NapCat 注入版相关逻辑已全部移除。
#   NapCat 现在只以 Docker 容器形式存在（qq-bridge-napcat），
#   所以「停止」只有两个层级：停 bridge / 再停容器，不再有「杀注入 QQ 进程树」这回事。
#   原 stop_all_hard.ps1 已合并为本脚本的 -Hard 开关（那个文件已删除）。
#
# 两种停止语义（默认是温和的，这是刻意的）：
#
#   默认（温和）：只停 bridge，**容器与 QQ 登录态保持在线**
#     → 之后 start_all 起来，NapCat 的 reverse_ws 自动重连，全程不用扫码。
#     → 日常改 bridge 代码、调配置，一律用这个。
#
#   加 -Hard：停 bridge 之后再 `docker stop` 容器
#     → 容器停掉后 QQ-NT 进程退出，即使登录态还在 ./data/qq 卷里，
#       quick-login 票据也常被服务端判失效 → 下次启动大概率要重新扫码。
#     → 仅在「换号 / 确实要重启 NapCat / 改容器配置 / 彻底关机」时使用。
#
# 为什么默认不杀 QQ（2026-10-26 实测教训，勿删）：
#   旧版 stop_all 无条件 taskkill /F 掉已登录 QQ。强杀后旧会话在服务端仍挂在线
#   （心跳未超时），紧接着用同一份票据重登，QQ 判定会话冲突并返回
#   「你的用户身份已失效，为保证账号安全，请你重新登录」→ 免密失效，只能重新扫码。
#   实测：16:15 该账号仍正常在线（nt_msg.db / profile_info.db 同秒写入），
#         16:16:32 重启后立即报身份已失效。而旧版 restart = stop_all + start_all，
#         等于「每次重启都要扫码」。
#
# 用法：
#   .\stop_all.ps1          温和停止：只停 bridge，QQ 保持在线
#   .\stop_all.ps1 -Hard    彻底停止：连 NapCat 容器一起停（下次多半要扫码）
# ============================================================================
param(
    [switch]$Hard
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$container = 'qq-bridge-napcat'

# ---------- 1. 停 bridge（8080） ----------
$stopBridge = Join-Path $root 'stop_bridge.ps1'
if (Test-Path $stopBridge) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopBridge
} else {
    Write-Host "[stop_all] 未找到 stop_bridge.ps1，跳过停止 bridge"
}

if (-not $Hard) {
    Write-Host "[stop_all] 温和停止：bridge 已停；NapCat 容器【保持运行】，下次启动自动重连，无需扫码。"
    Write-Host "[stop_all] 如需连容器一起停（换号 / 彻底关机，下次需重新扫码）：.\stop_all.ps1 -Hard"
    exit 0
}

# ---------- 2. 停 NapCat 容器（-Hard） ----------
Write-Host "[stop_all] -Hard：将停止 NapCat 容器（下次启动大概率需要重新扫码登录）"
$exists = $false
try {
    $null = & docker inspect -f '{{.Id}}' $container 2>$null
    $exists = ($LASTEXITCODE -eq 0)
} catch { }

if (-not $exists) {
    Write-Host "[stop_all] 容器 $container 不存在，跳过。"
    exit 0
}

$running = & docker inspect -f '{{.State.Running}}' $container 2>$null
if ("$running".Trim() -ne 'true') {
    Write-Host "[stop_all] 容器 $container 已是停止状态，跳过。"
    exit 0
}

& docker stop $container | Out-Null
Write-Host "[stop_all] 容器 $container 已停止（登录态仍保留在 ./data/qq 卷里）。"
Write-Host "[stop_all] 完成：QQ 机器人全套服务已停止"
