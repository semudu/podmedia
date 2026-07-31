@echo off
title PodMedia Firewall Configurator
echo ==================================================
echo   🍿 PodMedia Hub - Windows Firewall Configurator
echo ==================================================
echo.
echo Requesting Administrator privileges to allow local network (LAN) access to ports:
echo 3000 (Hub), 32400 (Plex), 8080 (qBittorrent), 7878 (Radarr), 8989 (Sonarr), 9696 (Prowlarr), 5030 (Slskd)
echo.
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-Command New-NetFirewallRule -DisplayName \"PodMedia Hub Services\" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000,32400,7878,8989,8080,9696,8191,5030,8337 -ErrorAction SilentlyContinue'"
echo [✓] Firewall rule added successfully!
echo.
echo Local IP Address:
ipconfig | findstr /i "IPv4"
echo.
pause
