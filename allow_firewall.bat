@echo off
title PodMedia LAN Access Configurator
echo ==================================================
echo   PodMedia Hub - Windows LAN Access Configurator
echo ==================================================
echo.
echo Requesting Administrator privileges to enable LAN access and port forwarding...
echo.
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -Command New-NetFirewallRule -DisplayName \"PodMedia Hub Services\" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000,32400,7878,8989,8080,9696,8191,5030,8337 -ErrorAction SilentlyContinue; foreach ($p in @(32400,7878,8989,8080,9696,8191,5030,8337)) { netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=127.0.0.1 }'"
echo [OK] Firewall and Podman Port Forwarding rules configured!
echo.
echo Local IP Address:
ipconfig | findstr /i "IPv4"
echo.
pause
