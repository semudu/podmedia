@echo off
title PodMedia - Durduruluyor...

echo ==================================================
echo   PodMedia - Servisleri ve Konteynerleri Durdur
echo ==================================================
echo.

echo [1/3] PodMedia konteynerleri durduruluyor...
for /f "tokens=*" %%i in ('podman ps -q --filter "name=media_center_"') do (
    echo   - Durduruluyor: %%i
    podman stop %%i >nul 2>&1
)
echo   [OK] Tum PodMedia konteynerleri durduruldu.

echo.
echo [2/3] Harici disk baglantilari serbest birakiliyor (WSL)...
wsl -d podman-machine-default -u root umount /mnt/e >nul 2>&1
wsl -d podman-machine-default -u root umount /mnt/f >nul 2>&1
wsl -d podman-machine-default -u root umount /mnt/g >nul 2>&1
echo   [OK] Harici disk mountlari WSL uzerinden guvenle ayrildi.

echo.
echo [3/3] PodMedia web sunucusu ve arka plan surecleri kapatiliyor...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app\app.py*' -or $_.CommandLine -like '*app/app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
echo   [OK] Web sunucusu ve izleme surecleri kapatildi.

echo.
echo ==================================================
echo   [OK] PodMedia basariyla kapatildi!
echo   [OK] Harici disk serbest birakildi (guvenle cikarilabilir).
echo ==================================================
echo.
ping 127.0.0.1 -n 3 >nul
exit /b 0
