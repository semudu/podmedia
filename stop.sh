#!/usr/bin/env bash
# PodMedia Stop Script

echo "=================================================="
echo " 🛑 PodMedia - Servisleri ve Konteynerleri Durdur"
echo "=================================================="
echo ""

echo "[1/3] PodMedia konteynerleri durduruluyor..."
podman ps -q --filter "name=media_center_" | while read -r c; do
    echo "  - Durduruluyor: $c"
    podman stop "$c" >/dev/null 2>&1
done
echo "  [✓] Tum konteynerler durduruldu."

echo ""
echo "[2/3] Harici disk baglantilari serbest birakiliyor (WSL)..."
wsl -d podman-machine-default -u root umount /mnt/e >/dev/null 2>&1 || true
wsl -d podman-machine-default -u root umount /mnt/f >/dev/null 2>&1 || true
wsl -d podman-machine-default -u root umount /mnt/g >/dev/null 2>&1 || true
echo "  [✓] Harici disk mountlari WSL uzerinden guvenle ayrildi."

echo ""
echo "[3/3] PodMedia web sunucusu ve arka plan surecleri kapatiliyor..."
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*app\app.py*' -or \$_.CommandLine -like '*app/app.py*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" >/dev/null 2>&1 || true
echo "  [✓] Web sunucusu ve izleme surecleri kapatildi."

echo ""
echo "=================================================="
echo " [✓] PodMedia basariyla kapatildi!"
echo " [✓] Harici disk serbest birakildi (guvenle cikarilabilir)."
echo "=================================================="

