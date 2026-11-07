# ============================================================================
# setup_docker_napcat.ps1 —— 用 Docker 起 NapCat（严格对齐 mlikiowa/napcat-docker）
#
# 前置：Docker Desktop 已安装（Hyper-V 后端）且 daemon 已就绪。
#   若刚装完尚未重启，本脚本会提示先重启（Docker Desktop + Hyper-V 需要一次重启）。
#
# 做的事（全部幂等）：
#   1) 确认 docker / docker compose 可用、daemon 在线
#   2) 建持久化数据目录 ./data/qq, ./data/config, ./data/plugins
#   3) docker compose -f docker-compose.napcat.yml up -d
#   4) 等容器起来 + 打印 WebUI 地址/Token + 二维码位置
#   5) 健康检查（容器状态 + 端口）
#
# 用法：在 qq_bridge 目录运行  .\setup_docker_napcat.ps1
# ============================================================================
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$compose = Join-Path $root 'docker-compose.napcat.yml'

Write-Host "===== [0] 检查 WSL2（Docker Desktop 4.97 的 Linux 引擎只认 WSL2） ====="
$vmOk = $false
$of = Get-CimInstance Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform'" -ErrorAction SilentlyContinue
if ($of -and $of.InstallState -eq 2) { $vmOk = $true }
if (-not $vmOk) {
    Write-Host "[!] VirtualMachinePlatform 未启用。请先【以管理员运行 .\enable_wsl2_docker.ps1】并重启电脑，再回来跑本脚本。"
    exit 1
}
Write-Host "WSL2 特性已启用，尝试更新 WSL 内核（可跳过）..."
try { wsl --update 2>&1 | Out-Host } catch { Write-Host "[i] wsl --update 未执行（可稍后手动运行）" }

Write-Host "`n===== [1/5] 检查 Docker ====="
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Host "[!] 未找到 docker 命令。若刚安装 Docker Desktop，请先【重启电脑】让 WSL2/Docker 生效，再运行本脚本。"
    exit 1
}
docker --version
$composeOk = $false
try { docker compose version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $composeOk = $true } } catch { }
if (-not $composeOk) { Write-Host "[!] docker compose 不可用，请更新 Docker Desktop。"; exit 1 }

$daemonOk = $false
try { docker info --format '{{.ServerVersion}}' 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $daemonOk = $true } } catch { }
if (-not $daemonOk) {
    Write-Host "[!] Docker daemon 未就绪。若刚装完请【重启电脑】；或手动启动 Docker Desktop 后重试。"
    Write-Host "    （Hyper-V 后端需要 hypervisor 运行：Hyper-V 功能已启用，重启后 HypervisorPresent 才为 True）"
    exit 1
}
Write-Host "Docker daemon OK."

Write-Host "`n===== [2/5] 建持久化数据目录 ====="
foreach ($d in @('data\qq','data\config','data\plugins')) {
    $p = Join-Path $root $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    Write-Host "  ok: $p"
}

Write-Host "`n===== [3/5] docker compose up -d ====="
if (-not (Test-Path $compose)) { Write-Host "[!] 缺少 $compose"; exit 1 }
docker compose -f $compose up -d
if ($LASTEXITCODE -ne 0) { Write-Host "[!] compose up 失败，见上方输出。"; exit 1 }

Write-Host "`n===== [4/5] 等待容器 =====（首次会拉镜像，可能几分钟）"
$ok = $false
for ($i=0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 3
    $st = (docker ps --filter "name=qq-bridge-napcat" --format '{{.Status}}' 2>$null)
    if ($st) { Write-Host ("  容器: " + $st); $ok = $true; break }
}
if (-not $ok) { Write-Host "[!] 容器未起来，查看日志：docker compose -f docker-compose.napcat.yml logs -f napcat"; exit 1 }

Write-Host "`n===== [5/5] 健康检查 & 输出 ====="
try {
    $webuiToken = (Select-String -Path (Join-Path $root 'data\config\webui.json') -Pattern '"token"\s*:\s*"([^"]+)"' -ErrorAction SilentlyContinue | Select-Object -First 1).Matches[0].Groups[1].Value
} catch { $webuiToken = '' }
Write-Host "  NapCat WebUI : http://127.0.0.1:6099/webui"
if ($webuiToken) { Write-Host "  WebUI Token  : $webuiToken" } else { Write-Host "  WebUI Token  : 见 docker compose logs napcat（首次生成后写入 data/config/webui.json）" }
Write-Host "  二维码/登录   : docker compose -f docker-compose.napcat.yml logs -f napcat   （或 WebUI 内置二维码）"
Write-Host "  反向 WS       : NapCat 将连回 ws://host.docker.internal:8080/onebot/v11/ws"
Write-Host ""
Write-Host "  首登后确认 bridge 收到事件：bridge.log 末尾应出现 connector_attached ... online=1"
Write-Host "  健康复查：docker ps | findstr qq-bridge-napcat  ;  netstat -ano | findstr :8080"
