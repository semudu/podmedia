# PodMedia Fix Plex & LAN Access
$ErrorActionPreference = "SilentlyContinue"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  PodMedia Hub - Fixing Local & Network Access" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Add Windows Firewall Rule
Write-Host "[1/2] Setting Windows Firewall Rules..." -ForegroundColor Yellow
New-NetFirewallRule -DisplayName "PodMedia Hub Services" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000,32400,7878,8989,8080,9696,8191,5030,8337 -ErrorAction SilentlyContinue | Out-Null
Write-Host " [✓] Firewall rule configured!" -ForegroundColor Green

# 2. Get Podman WSL2 VM IP & Set Netsh Portproxy
Write-Host "[2/2] Detecting Podman WSL2 VM IP & Forwarding Ports..." -ForegroundColor Yellow
$wsl_raw = wsl.exe -d podman-machine-default -u root ip route
$wsl_ip = ""
foreach ($line in $wsl_raw) {
    if ($line -match 'src\s+(172\.\d+\.\d+\.\d+)') {
        $wsl_ip = $Matches[1]
    }
}

if ($wsl_ip) {
    Write-Host " [✓] Podman WSL2 VM IP detected: $wsl_ip" -ForegroundColor Green
    netsh interface portproxy reset | Out-Null
    $ports = @(32400, 7878, 8989, 8080, 9696, 8191, 5030, 8337)
    foreach ($p in $ports) {
        netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$wsl_ip | Out-Null
    }
    Write-Host " [✓] Port forwarding active! Plex and services mapped to $wsl_ip" -ForegroundColor Green
} else {
    Write-Host " [!] Podman WSL2 VM IP could not be detected." -ForegroundColor Red
}

Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Gray
$null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
