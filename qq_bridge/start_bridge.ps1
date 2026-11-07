# 启动 qq_bridge（独立进程，日志仅落在 qq_bridge/logs/）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Error 'venv 不存在，请先: python -m venv .venv ; .venv\Scripts\python -m pip install -r requirements.txt'
    exit 1
}

$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'bridge.log'
$pidFile = Join-Path $logDir 'bridge.pid'

# 从 .env 读监听端口（默认 8080）
$port = 8080
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}

# ---- 与容器路线互斥（2026-11-05 加）------------------------------------------
# docker-compose.bridge.yml 已经把 qq-bridge 跑在同一个 8080 上。两条路线
# **只能二选一**，否则 Windows 的 SO_REUSEADDR 会让两个进程都绑成功，流量
# 归谁不确定。实测后果：开机时本脚本先起（13:15），Docker 稍后起容器（16:36）
# 抢走端口，宿主实例从此永久 ws_down 却一直占着 8080，日志里还夹着它的
# HTTP 400 噪音。
# 端口检查挡不住这个竞态（容器可能比脚本晚起），所以这里显式查容器。
$dockerExe = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerExe) {
    $running = & docker ps --filter 'name=^qq-bridge$' --filter 'status=running' --format '{{.Names}}' 2>$null
    if ($running -match 'qq-bridge') {
        Write-Error ("容器 qq-bridge 正在运行并占用 $port —— 本脚本与容器路线互斥，已放弃启动。`n" +
                     "确实要回退到 Windows 原生运行：先 docker compose -f docker-compose.bridge.yml down")
        exit 1
    }
}

# 端口已被占用则直接放弃，避免起第二个实例抢不到端口
$exist = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($exist) {
    Write-Error "端口 $port 已被 PID=$($exist.OwningProcess) 占用，先执行 .\stop_bridge.ps1"
    exit 1
}

# 用单行参数启动，避免跨行反引号+空行被 PowerShell 解析为新命令
# 修复 PowerShell Start-Process 在“脏环境块”下的报错：
# 若进程同时继承了 HTTPS_PROXY 与 https_proxy（及 HTTP_/ALL_/NO_ 的大小写副本），
# Start-Process 内部按大小写不敏感字典合并环境时会抛
# “已添加项。字典中的关键字:"HTTPS_PROXY"所添加的关键字:"https_proxy"”。
# 这里用 .NET API 显式删掉小写副本（不触发 PowerShell $env: 的大小写冲突），
# 让子进程继承干净的环境块。
foreach ($base in @('HTTP', 'HTTPS', 'ALL', 'NO')) {
    $upName = $base + '_PROXY'
    $loName = $base.ToLower() + '_proxy'
    $up = [Environment]::GetEnvironmentVariable($upName, 'Process')
    $lo = [Environment]::GetEnvironmentVariable($loName, 'Process')
    if ($up -and $lo) {
        [Environment]::SetEnvironmentVariable($loName, $null, 'Process')
    }
}

# 2026-10-30 修乱码（根因）：bot 进程 stdout/stderr 经 -RedirectStandardOutput/-Error
# 重定向写入 bridge.log / bridge.err，Windows 下 Python 重定向到文件时默认用 GBK（cp936）
# 输出，导致日志中文是 GBK 字节、前端误读成乱码。加 -X utf8 开启 Python UTF-8 模式，
# 让 stdio 强制 UTF-8，以后这两个文件也是 UTF-8，从源头杜绝乱码（读取侧 routers.py 的
# _detect_log_encoding 已兼容旧 GBK 日志，无需立即重启即可正确显示）。
Start-Process -FilePath $py -ArgumentList @('-X', 'utf8', (Join-Path $root 'bot.py')) -WorkingDirectory $root -RedirectStandardOutput $log -RedirectStandardError "$logDir\bridge.err" -WindowStyle Hidden

# 按【监听端口】反查 PID —— 不能用命令行模糊匹配：
# 机器上可能同时存在其它 python 进程（含残留僵尸进程），按名字/命令行取第一条
# 会把无关 PID 写进 bridge.pid，导致 stop_bridge.ps1 杀错进程、真正的 bridge 杀不掉。
$procId = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { $procId = $conn.OwningProcess; break }
}

if ($procId) {
    $procId | Out-File -FilePath $pidFile -Encoding ascii
    Write-Host "qq_bridge 已启动 PID=$procId（监听 $port），日志见 $log"
} else {
    Write-Warning "30s 内未检测到端口 $port 监听，请查看 $log 与 $logDir\bridge.err"
    exit 1
}
