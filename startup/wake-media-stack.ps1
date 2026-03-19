# Wake Media Stack - starts Plex, Sonarr, Radarr, NZBGet, and Dashboard on boot
# Registered as a scheduled task to run at user logon
# Dashboard is exposed via Cloudflare tunnel (cloudflared runs as a Windows service)

$services = @(
    @{
        Name = "Plex Media Server"
        Path = "C:\Program Files\Plex\Plex Media Server\Plex Media Server.exe"
        Process = "Plex Media Server"
        Port = 32400
    },
    @{
        Name = "Sonarr"
        Path = "C:\ProgramData\Sonarr\bin\Sonarr.exe"
        Process = "Sonarr"
        Port = 8989
    },
    @{
        Name = "Radarr"
        Path = "C:\ProgramData\Radarr\bin\Radarr.exe"
        Process = "Radarr"
        Port = 7878
    },
    @{
        Name = "NZBGet"
        Path = "C:\Program Files\NZBGet\nzbget.exe"
        Args = "-D"
        Process = "nzbget"
        Port = 6789
    }
)

# Python-based service (Plex Health Dashboard) - tunneled via Cloudflare
$pythonService = @{
    Name = "Plex Dashboard"
    Script = "O:\plex\dashboard.py"
    Process = "python"
    Port = 5050
}

$logFile = Join-Path $PSScriptRoot "startup.log"

function Log($msg) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$timestamp  $msg" | Tee-Object -FilePath $logFile -Append
}

Log "=== Media stack startup beginning ==="

foreach ($svc in $services) {
    $running = Get-Process -Name $svc.Process -ErrorAction SilentlyContinue
    if ($running) {
        Log "$($svc.Name) already running (PID $($running.Id -join ', '))"
        continue
    }

    if (-not (Test-Path $svc.Path)) {
        Log "WARNING: $($svc.Name) not found at $($svc.Path)"
        continue
    }

    Log "Starting $($svc.Name)..."
    if ($svc.Args) {
        Start-Process -FilePath $svc.Path -ArgumentList $svc.Args -WindowStyle Hidden
    } else {
        Start-Process -FilePath $svc.Path -WindowStyle Hidden
    }
    Start-Sleep -Seconds 2
}

# Start Plex Dashboard (Python) if port 5050 is not in use
$portCheck = Get-NetTCPConnection -LocalPort $pythonService.Port -ErrorAction SilentlyContinue
if ($portCheck) {
    Log "$($pythonService.Name) already running on port $($pythonService.Port)"
} elseif (Test-Path $pythonService.Script) {
    Log "Starting $($pythonService.Name)..."
    Start-Process -FilePath "C:\Python312\python.exe" `
        -ArgumentList $pythonService.Script `
        -WorkingDirectory "O:\plex" `
        -WindowStyle Hidden
} else {
    Log "WARNING: $($pythonService.Name) script not found at $($pythonService.Script)"
}

# Brief wait then verify
Start-Sleep -Seconds 5

Log "--- Verification ---"
foreach ($svc in $services) {
    $running = Get-Process -Name $svc.Process -ErrorAction SilentlyContinue
    if ($running) {
        Log "$($svc.Name) is UP (port $($svc.Port))"
    } else {
        Log "FAILED: $($svc.Name) is NOT running"
    }
}

# Verify dashboard by port
$dashPort = Get-NetTCPConnection -LocalPort $pythonService.Port -ErrorAction SilentlyContinue
if ($dashPort) {
    Log "$($pythonService.Name) is UP (port $($pythonService.Port))"
} else {
    Log "FAILED: $($pythonService.Name) is NOT running on port $($pythonService.Port)"
}

# Verify Cloudflare tunnel
$cfd = Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue
if ($cfd -and $cfd.Status -eq "Running") {
    Log "Cloudflare tunnel is UP (plex.ninefingerwoodshop.com)"
} else {
    Log "WARNING: Cloudflare tunnel service is not running"
}

Log "=== Startup complete ==="
