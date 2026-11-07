# 停止 qq_bridge
#
# 优先按【监听端口】反查 PID；pid 文件仅作兜底。
#
# 2026-11-05 修：容器路线下 docker 后端也会绑同一个 8080，Get-NetTCPConnection
# 的第一个结果可能是它，随后「命令行不含 bot.py → 拒绝终止」会让本脚本直接失效
# （实测：双绑状态下 stop_bridge.ps1 根本停不掉宿主实例）。因此这里遍历全部
# 监听者，只认命令行含 bot.py 的那一个。

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $root 'logs\bridge.pid'

$port = 8080
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}

$target = $null
foreach ($c in (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" -ErrorAction SilentlyContinue
    if ($p -and $p.CommandLine -like '*bot.py*') { $target = $c.OwningProcess; break }
}

if ($target -eq $null) {
    if (Test-Path $pidFile) {
        $raw = (Get-Content $pidFile -Raw).Trim()
        if ($raw -match '^\d+$') { $target = [int]$raw }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

if ($target -eq $null) {
    Write-Host "端口 $port 上没有被本项目的 bot.py 监听，qq_bridge 未运行"
    exit 0
}

# 安全校验：确认目标进程确实是本项目的 bot.py，绝不误杀其它进程（含主项目 7788/8099）
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = $target" -ErrorAction SilentlyContinue
if ($proc -and $proc.CommandLine -notlike '*bot.py*') {
    Write-Error "PID=$target 的命令行不含 bot.py，拒绝终止（避免误杀其它服务）"
    exit 1
}

try {
    Stop-Process -Id $target -Force
    Write-Host "已停止 qq_bridge PID=$target"
} catch {
    Write-Warning "PID=$target 不存在（可能已退出）"
}

# 兜底：NoneBot + uvicorn 是双进程结构（主 NoneBot + uvicorn worker），端口监听者
# 只是 worker，杀 worker 后主进程有时因 asyncio 资源未释放而残留，导致下次 start
# 撞端口失败。这里用 taskkill /F /T 终结整棵树，仍只认 cmdline 含 bot.py 的 python。
Start-Sleep -Milliseconds 300
foreach ($p in (Get-CimInstance Win32_Process -Filter "CommandLine Like '%bot.py%'" -ErrorAction SilentlyContinue)) {
    if ($p.ProcessId -ne $target) {
        try {
            $null = & taskkill.exe /PID $p.ProcessId /F /T 2>&1
            Write-Host "兜底 taskkill 残留 PID=$($p.ProcessId) (cmdline 含 bot.py)"
        } catch { }
    }
}

Start-Sleep -Milliseconds 500
foreach ($p in (Get-CimInstance Win32_Process -Filter "CommandLine Like '%bot.py%'" -ErrorAction SilentlyContinue)) {
    try { $null = & taskkill.exe /PID $p.ProcessId /F /T 2>&1 } catch { }
}

Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
