# ============================================================================
# enable_wsl2_docker.ps1 —— 一键启用 Docker Desktop 4.97 所需的 WSL2 环境
#
# 背景：Docker Desktop 4.97 已不支持 Hyper-V 跑 Linux 容器，只认 WSL2。
#       本机 VirtualMachinePlatform / Microsoft-Windows-Subsystem-Linux 均为关闭。
# 本脚本：以管理员身份启用这两个特性（dism），完成后提示重启。
#
# 用法（任选其一）：
#   1) 右键本文件 → "使用 PowerShell 运行"（若未提权会自动弹 UAC）
#   2) 管理员 PowerShell： powershell -ExecutionPolicy Bypass -File .\enable_wsl2_docker.ps1
# ============================================================================
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] 需要管理员权限，正在请求提权（请在 UAC 弹窗点『是』）..."
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath)
    } catch {
        Write-Host "[!] 自动提权失败：$($_.Exception.Message)"
        Write-Host "    请手动用管理员 PowerShell 运行： powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    }
    exit
}

Write-Host "===== 启用 VirtualMachinePlatform ====="
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
Write-Host "exit=$LASTEXITCODE"

Write-Host "`n===== 启用 Microsoft-Windows-Subsystem-Linux (WSL) ====="
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
Write-Host "exit=$LASTEXITCODE"

Write-Host "`n===== 复核 InstallState (2=已启用) ====="
foreach ($f in @('VirtualMachinePlatform','Microsoft-Windows-Subsystem-Linux')) {
    $of = Get-CimInstance Win32_OptionalFeature -Filter "Name='$f'" -ErrorAction SilentlyContinue
    if ($of) { Write-Host ("  {0} = {1}" -f $f, $of.InstallState) }
}

Write-Host ""
Write-Host "================================================================"
Write-Host " 完成。接下来："
Write-Host "   1) 【重启电脑】（WSL2 行为需重启生效）"
Write-Host "   2) 重启后打开 Docker Desktop，等右下角引擎变绿"
Write-Host "      （首次可能提示安装 WSL 内核；如未自动装，管理员跑： wsl --update）"
Write-Host "   3) 回到 qq_bridge 目录运行： .\setup_docker_napcat.ps1"
Write-Host "================================================================"
Read-Host "按回车键退出"
