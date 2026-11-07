# ============================================================================
# napcat_status.ps1 —— 只读健康检查（不修改任何状态、不重启进程）
# ----------------------------------------------------------------------------
# 【2026-10-29 架构重构】原版按「父进程 = NapCatWinBootMain.exe」统计注入实例数，
#   该判定已随注入版一并废弃。现在 NapCat 只有一个形态：Docker 容器 qq-bridge-napcat。
#
# 健康判定（四要素全满足才算 HEALTHY）：
#   1) 容器运行中，且重启次数没有异常增长（RestartCount 每次启动会归零，看 StartedAt 更准）
#   2) bridge 在监听 PORT，且与 NapCat 存在 ESTABLISHED 连接
#   3) /api/qq/status 返回 online:true 且 self_id 非空（WS 连着不代表账号真能用）
#   4) 容器日志近期没有 KickedOffLine / 需要验证码 / 等扫码 这类致命信号
#
# 关键区分（很多人栽在这）：
#   「WS 连接在」≠「账号可用」。NapCat 被踢后 WS 往往还挂着，但底层 QQ 会话已失效，
#   发消息会超时。所以必须额外查 self_id，不能只看端口。
#
# 用法： .\napcat_status.ps1
# 退出码：0 健康 / 1 异常（便于脚本串联）
# ============================================================================
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$container = 'qq-bridge-napcat'

# ---- 解析端口（与 start_bridge.ps1 同源） ----
$port = 8080
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}

# ---- 1. 容器状态 ----
$containerExists = $false
$containerRunning = $false
$startedAt = '-'
try {
    $null = & docker inspect -f '{{.Id}}' $container 2>$null
    $containerExists = ($LASTEXITCODE -eq 0)
    if ($containerExists) {
        $running = & docker inspect -f '{{.State.Running}}' $container 2>$null
        $containerRunning = ("$running".Trim() -eq 'true')
        $startedAt = (& docker inspect -f '{{.State.StartedAt}}' $container 2>$null)
    }
} catch { }

# ---- 2. 端口与连桥 ----
$bridgeListen = $false
$established = $false
try {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { $bridgeListen = $true }
    if (Get-NetTCPConnection -LocalPort $port -State Established -ErrorAction SilentlyContinue) { $established = $true }
} catch { }

# ---- 3. bridge 侧账号态（真在线判定） ----
$apiOnline = $false
$apiSelfId = ''
$apiUptime = ''
$apiDetach = ''
$apiErr = ''
try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/qq/status" -TimeoutSec 5 -ErrorAction Stop
    $apiOnline = [bool]$r.online
    if ($r.self_id) { $apiSelfId = "$($r.self_id)" }
    if ($null -ne $r.uptime) { $apiUptime = ('{0:N2}h' -f ([double]$r.uptime / 3600)) }
    if ($null -ne $r.detach_total) { $apiDetach = "$($r.detach_total)" }
} catch {
    $apiErr = $_.Exception.Message
}

# ---- 4. 容器日志致命信号扫描（最近 300 行） ----
$hits = @()
if ($containerExists) {
    $patterns = @{
        'KickedOffLine 被踢下线' = 'KickedOffLine|下线通知'
        '登录票据失效'           = '登录已失效|用户身份已失效|登录态已失效'
        '正在等扫码'             = '请扫描下面的二维码'
        '密码回退需要验证码'     = '密码回退需要验证码'
        '账号风险/限制(168)'     = 'ErrCode:? *168|存在安全风险'
    }
    try {
        $logs = & docker logs --tail 300 $container 2>&1 | Out-String
        foreach ($k in $patterns.Keys) {
            if ($logs -match $patterns[$k]) { $hits += $k }
        }
    } catch { }
}

# ---- 5. 汇总判定 ----
$healthy = ($containerRunning -and $bridgeListen -and $established -and $apiOnline -and $apiSelfId -and $hits.Count -eq 0)

Write-Host "==================== QQ 机器人健康态 ===================="
Write-Host ("  容器 {0,-18}: {1}" -f $container, $(if (-not $containerExists) { '✗ 不存在' } elseif ($containerRunning) { '✓ running' } else { '✗ stopped' }))
if ($containerRunning) { Write-Host ("  容器启动于            : {0}" -f $startedAt) }
Write-Host ("  bridge 监听 :{0,-6}  : {1}" -f $port, $(if ($bridgeListen) { '✓' } else { '✗ 未监听（bridge 没起来）' }))
Write-Host ("  容器↔bridge ESTABLISHED: {0}" -f $(if ($established) { '✓' } else { '✗ 未连桥（NapCat 没连上或 bridge 刚重启）' }))
Write-Host ("  账号在线(/api/qq)     : {0}" -f $(if ($apiOnline) { "✓ online self_id=$apiSelfId" } elseif ($apiErr) { "✗ 查询失败：$apiErr" } else { '✗ offline' }))
if ($apiUptime) { Write-Host ("  本次已在线            : {0}   （累计掉线 {1} 次）" -f $apiUptime, $apiDetach) }
Write-Host ("  致命信号              : {0}" -f $(if ($hits.Count -eq 0) { '✓ 无' } else { '✗ ' + ($hits -join ' / ') }))
if ($hits.Count -gt 0) {
    Write-Host ""
    Write-Host "  ---- 处置建议 ----"
    if ($hits -contains '正在等扫码' -or $hits -contains '密码回退需要验证码') {
        Write-Host "    票据已被服务端作废 → 需要人工扫码，执行： python scripts/qq_login_qr.py"
    }
    if ($hits -contains 'KickedOffLine 被踢下线') {
        Write-Host "    被踢下线 → 先看 python scripts/qq_conn_report.py 的掉线曲线；"
        Write-Host "    若反复被踢，检查是否另有 NapCat/桌面 QQ 在用同一账号（互踢），"
        Write-Host "    以及容器内 napcat_<QQ号>.json 的 bypass 六项是否全为 true。"
    }
}
Write-Host "======================================================="
Write-Host ("  结论: {0}" -f $(if ($healthy) { 'HEALTHY（容器运行 + 连桥 + 账号在线 + 无致命信号）' } else { 'UNHEALTHY —— 见上' }))
if (-not $healthy) { exit 1 } else { exit 0 }
