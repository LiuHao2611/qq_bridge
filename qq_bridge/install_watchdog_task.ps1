# ============================================================================
# 注册 Windows 计划任务：让 watchdog.ps1 每 5 分钟自动运行一次。
# 效果：NapCat 一旦掉线（被踢/崩溃/假连接卡死），5 分钟内写入一条告警记录。
# ⚠️ 重要：watchdog 只做「检测 + 日志告警」，**绝不自动重启、绝不自动重登**。
#    原因见 watchdog.ps1 头部注释：2026-10-27 之前的自动 stop_all+start_all 会强杀
#    已登录的 QQ 会话并紧接着重登，形成「频繁重登」→ 腾讯判定风险设备 → 全场景社交保护，
#    2026-09-16 已有账号（3858741376）因此被永久封禁。此项为项目铁律。
#    告警落在 logs/watchdog.log，人工处置方式见该文件内的提示行。
# 运行方式（管理员 PowerShell）：
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\AI\manju_agent\qq_bridge\install_watchdog_task.ps1"
# 卸载方式：
#   Unregister-ScheduledTask -TaskName 'NapCatQQBridgeWatchdog' -Confirm:$false
# ============================================================================

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$wd = Join-Path $root 'watchdog.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wd`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration ([TimeSpan]::FromDays(248))
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 3)

Register-ScheduledTask -TaskName 'NapCatQQBridgeWatchdog' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "已注册计划任务 NapCatQQBridgeWatchdog（每 5 分钟检查 NapCat 在线状态，掉线自动恢复）"
Write-Host "查看：taskschd.msc -> 任务计划程序库 -> NapCatQQBridgeWatchdog"
Write-Host "卸载：Unregister-ScheduledTask -TaskName 'NapCatQQBridgeWatchdog' -Confirm:`$false"
