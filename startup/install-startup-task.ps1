# Run this once (as admin) to register the startup scheduled task
# Usage: Right-click > Run with PowerShell as Administrator

$scriptPath = Join-Path $PSScriptRoot "wake-media-stack.ps1"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -AtLogon

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

$existing = Get-ScheduledTask -TaskName "WakeMediaStack" -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName "WakeMediaStack" -Confirm:$false
    Write-Host "Removed existing task."
}

Register-ScheduledTask `
    -TaskName "WakeMediaStack" `
    -Description "Starts Plex, Sonarr, Radarr, and NZBGet at logon" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host ""
Write-Host "Scheduled task 'WakeMediaStack' registered successfully!" -ForegroundColor Green
Write-Host "All four services will start automatically at logon."
Write-Host ""
Write-Host "To test now, run:"
Write-Host "  schtasks /Run /TN WakeMediaStack"
