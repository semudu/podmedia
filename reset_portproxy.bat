@echo off
title Reset Windows PortProxy
echo Resetting Windows netsh portproxy...
netsh interface portproxy reset
echo [OK] All netsh portproxy rules removed successfully!
pause
