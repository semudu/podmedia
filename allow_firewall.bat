@echo off
title PodMedia Firewall Configurator
echo ==================================================
echo   PodMedia Hub - Windows Firewall Configurator
echo ==================================================
echo.
echo Requesting Administrator privileges to allow local network (LAN) access to ports...
echo.
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -Command New-NetFirewallRule -DisplayName \"PodMedia Hub Services\" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000,32400,7878,8989,8080,9696,8191,5030,8337 -ErrorAction SilentlyContinue'"
echo [OK] Firewall rule command sent to Administrator PowerShell prompt.
echo.
echo Local IP Address:
ipconfig | findstr /i "IPv4"
echo.
pause
