# ============================================================================
# NapCat + bridge 看门狗（watchdog）—— 增强版（2026-09-15）
#
# 作用：定期检查 NapCat 是否真的健康在线；异常时【仅记录 + 告警】，绝不自动重启。
#   2026-10-27 修正：原"自动 stop_all+start_all"会强杀已登录的 QQ 会话并紧接着重登，
#   形成「频繁重登」→ 腾讯判定「登录过风险设备/网络环境」→ 全场景社交功能保护
#   （3858741376 即因此于 2026-09-16 被永久封）。项目铁律（start_all.ps1 头部）明确要求
#   watchdog 只做「检测 + 日志告警」、绝不自动重新登录。故本脚本现在只告警，不重启。
#   覆盖两种掉线形态（旧版只覆盖第 1 种，导致今天午间"假连接真卡死"漏检）：
#     ① 离线：8080 无 ESTABLISHED 连接（被踢/崩溃/未启动）→ 直接重启。
#     ② 假连接真卡死：8080 仍连着，但 QQ NT 内核 IPC 超时（NapCat 日志密集
#        "NTEvent Timeout" / bridge 日志密集 "ActionFailed"）→ 视为卡死，重启。
#       这种形态 WS 重连机制救不了，只能重启 QQ 进程（NapCat 注入模式必现）。
#
# 设计要点：
#   - 只做"检查 + 重启"，不主动登录；快速登录票据有效时重启后全自动恢复。
#   - 冷却机制：避免反复重启把"请扫码"界面刷掉。
#       · 普通卡死/离线：冷却 15 分钟。
#       · 检测到需扫码信号（日志出现"二维码已保存"/"请扫描"）：冷却 30 分钟，给用户扫码留窗。
#   - 幂等：重复执行安全。
#   - 每次判定都写 watchdog.log（含原因），便于事后复盘。
#
# 典型用法（配合 install_watchdog_task.ps1 注册为每 5 分钟计划任务）：
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\AI\manju_agent\qq_bridge\watchdog.ps1"
# ============================================================================

$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$wdLog       = Join-Path $logDir 'watchdog.log'
$cooldownFile = Join-Path $logDir '.napcat_last_reboot'
$napcatLog   = Join-Path $logDir 'napcat_boot.log'
$botErr      = Join-Path $logDir 'bot.err'

$cooldownNormalMin = 15   # 卡死/离线冷却
$cooldownScanMin   = 30   # 需扫码冷却
$errWindowMin      = 2    # 统计错误的时间窗口
$errThreshold      = 8    # 窗口内错误条数 >= 此值 判定卡死

function Stamp { Get-Date -Format 'yyyy-MM-dd HH:mm:ss' }

# ---- 判定 NapCat 是否健康 ----
function Test-NapCatHealthy {
    # 1) WS 连接是否存在（bridge 监听侧 8080 的 ESTABLISHED，不误匹配 NapCat 出站连腾讯）
    $connected = $false
    try {
        $c = Get-NetTCPConnection -LocalPort 8080 -State Established -ErrorAction SilentlyContinue
        if ($c) { $connected = $true }
    } catch { }

    # 没有连接 = 离线
    if (-not $connected) {
        Add-Content -Path $wdLog -Value "$(Stamp) [watchdog] 判定=离线（8080 无 ESTABLISHED）"
        return $false
    }

    # 2) 卡死检测：统计最近 $errWindowMin 分钟内 NapCat/bridge 错误密集度
    $since = (Get-Date).AddMinutes(-$errWindowMin)
    $errCount = 0
    $needScan = $false

    # bridge 侧错误（完整时间戳 yyyy-MM-dd HH:mm:ss，含 ActionFailed）
    if (Test-Path $botErr) {
        Get-Content $botErr -Tail 800 -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_ -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
                try { $ts = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null) } catch { return }
                if ($ts -ge $since) {
                    if ($_ -match 'ActionFailed|NTEvent|Timeout') { $errCount++ }
                }
            }
        }
    }

    # NapCat 侧错误（时间戳格式 MM-dd HH:mm:ss，补 2026 年；含 NTEvent Timeout / 二维码保存=需扫码）
    if (Test-Path $napcatLog) {
        Get-Content $napcatLog -Tail 1000 -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_ -match '^(\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
                try { $ts = [datetime]::ParseExact("2026-$($Matches[1])", 'yyyy-MM-dd HH:mm:ss', $null) } catch { return }
                if ($ts -ge $since) {
                    if ($_ -match 'NTEvent|发生错误 Error: Timeout') { $errCount++ }
                    if ($_ -match '二维码已保存|请扫描下面的二维码') { $needScan = $true }
                }
            }
        }
    }

    if ($errCount -ge $errThreshold) {
        Add-Content -Path $wdLog -Value "$(Stamp) [watchdog] 判定=卡死（连接存在但最近 $errWindowMin 分钟内错误 $errCount 条 >= $errThreshold）"
        return $false
    }

    if ($needScan) {
        # 连着但要求扫码 = 内核已掉，正等扫码。算离线（需人工扫码），交由下方冷却处理。
        Add-Content -Path $wdLog -Value "$(Stamp) [watchdog] 判定=需扫码（连接存在但日志要求扫码）"
        return $false
    }

    Add-Content -Path $wdLog -Value "$(Stamp) [watchdog] 判定=健康（连接存在且最近 $errWindowMin 分钟内错误 $errCount 条 < $errThreshold）"
    return $true
}

# ---- 主流程 ----
if (Test-NapCatHealthy) {
    exit 0
}

# ---- 异常：检查冷却 ----
$last = $null
if (Test-Path $cooldownFile) {
    $s = (Get-Content $cooldownFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($s -match '^\d+$') {
        try { $last = [datetime]::FromFileTimeUtc([long]$s) } catch { }
    }
}
$now = Get-Date
$sinceMin = if ($last) { [math]::Round(($now - $last).TotalMinutes, 1) } else { 9999 }

# 冷却时长：若最近日志出现需扫码信号，给更长的扫码窗口
$needScanNow = $false
if (Test-Path $napcatLog) {
    $since5 = $now.AddMinutes(-5)
    Get-Content $napcatLog -Tail 1000 -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_ -match '^(\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
            try { $ts = [datetime]::ParseExact("2026-$($Matches[1])", 'yyyy-MM-dd HH:mm:ss', $null) } catch { return }
            if ($ts -ge $since5 -and ($_ -match '二维码已保存|请扫描下面的二维码')) { $needScanNow = $true }
        }
    }
}
$cooldownMin = if ($needScanNow) { $cooldownScanMin } else { $cooldownNormalMin }

if ($last -and $sinceMin -lt $cooldownMin) {
    Add-Content -Path $wdLog -Value "$(Stamp) [watchdog] 异常但距上次自动恢复仅 $sinceMin 分钟（冷却 $cooldownMin 分钟），跳过，等待人工扫码/重连"
    exit 0
}

# ---- 检测到异常：只告警、绝不自动重启（账号安全第一）----
# 2026-10-27 修正：自动 stop_all+start_all 会强杀已登录的 QQ 会话、紧接着重登，
# 形成「频繁重登」→ 腾讯判定「登录过风险设备/网络环境」→ 全场景社交功能保护
# （3858741376 即因此于 2026-09-16 被永久封）。项目铁律明确：watchdog 只做检测+告警、绝不自动重登。
# 故此处仅记录 + 写冷却时间戳，交由人工处理（扫码 / 或显式 stop_all -Hard 后再 start_all）。
Add-Content -Path $wdLog -Value "$(Stamp) [watchdog] ⚠️ NapCat 异常（距上次 $sinceMin 分钟），已熔断：仅告警不自动重启。请人工处理（扫码，或显式 stop_all -Hard 后 start_all）。"
try { [long](Get-Date).ToFileTimeUtc() | Out-File -FilePath $cooldownFile -Encoding ascii } catch { }
Add-Content -Path $wdLog -Value "$(Stamp) [watchdog] 已写入冷却时间戳（冷却 $cooldownMin 分钟），未执行任何停止/启动操作。"
