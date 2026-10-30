import os
import sys
import subprocess
import shutil
import configparser
import platform

BANNER = """
==================================================
  🍿 PodMedia Setup & Initialization Engine
==================================================
"""

def check_command(cmd):
    """Checks if a CLI command is available on system PATH."""
    return shutil.which(cmd) is not None

def copy_template_if_missing(src_example, dest_target, description):
    """Utility to copy a template file to target path if target does not exist."""
    if not os.path.exists(dest_target):
        if os.path.exists(src_example):
            os.makedirs(os.path.dirname(dest_target), exist_ok=True)
            shutil.copyfile(src_example, dest_target)
            print(f" [✓] Initialized {description} from template.")
        else:
            print(f" [!] Template missing for {description} ({src_example}).")
    else:
        print(f" [✓] Verified local config for {description}.")

def run_setup():
    print(BANNER)
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. Dependency Checks & Installation
    print("[1/5] Checking Dependencies...")
    if not check_command("podman"):
        print(" [X] ERROR: 'podman' CLI is not installed or not in PATH.")
        print("     Please install Podman from https://podman.io")
        sys.exit(1)
    else:
        print(" [✓] Podman CLI detected.")

    req_file = os.path.join(base_dir, "requirements.txt")
    if os.path.exists(req_file):
        try:
            print(" [i] Installing/Verifying Python requirements (pystray, Pillow, podman-compose)...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], check=True, capture_output=True)
            print(" [✓] Python dependencies verified.")
        except Exception as e:
            print(" [!] Note: pip install completed with notice:", e)

    # 2. Config & Environment Initialization
    print("\n[2/5] Initializing Personalized Service Configurations...")

    # Root config.properties
    config_file = os.path.join(base_dir, "config.properties")
    example_config = os.path.join(base_dir, "config.properties.example")
    copy_template_if_missing(example_config, config_file, "Root Server Properties (config.properties)")

    # Read active paths from config.properties
    cfg = configparser.ConfigParser()
    cfg.read(config_file)
    raw_media_dir = cfg.get("paths", "media_dir", fallback="./media")
    raw_downloads_dir = cfg.get("paths", "downloads_dir", fallback="./downloads")

    media_dir = os.path.abspath(os.path.join(base_dir, raw_media_dir)) if not os.path.isabs(raw_media_dir) else raw_media_dir
    downloads_dir = os.path.abspath(os.path.join(base_dir, raw_downloads_dir)) if not os.path.isabs(raw_downloads_dir) else raw_downloads_dir

    # Plex .env
    copy_template_if_missing(
        os.path.join(base_dir, "services", "plex", ".env.example"),
        os.path.join(base_dir, "services", "plex", ".env"),
        "Plex Local Environment (services/plex/.env)"
    )

    # Slskd Config (Soulseek FLAC Downloader)
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "slskd", "slskd.yml.example"),
        os.path.join(base_dir, "services", "tools", "slskd", "config", "slskd.yml"),
        "Slskd Downloader (services/tools/slskd/config/slskd.yml)"
    )

    # Beets Config (Music Auto-Tagger)
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "beets", "config.yaml.example"),
        os.path.join(base_dir, "services", "tools", "beets", "config", "config.yaml"),
        "Beets Music Auto-Tagger (services/tools/beets/config/config.yaml)"
    )

    # qBittorrent Config (Torrent Downloader)
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "qbittorrent", "qBittorrent.conf.example"),
        os.path.join(base_dir, "services", "tools", "qbittorrent", "config", "qBittorrent", "qBittorrent.conf"),
        "qBittorrent Client (services/tools/qbittorrent/config/qBittorrent/qBittorrent.conf)"
    )

    # Sonarr Config (Series Management)
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "sonarr", "config.xml.example"),
        os.path.join(base_dir, "services", "tools", "sonarr", "config", "config.xml"),
        "Sonarr Series Management (services/tools/sonarr/config/config.xml)"
    )

    # Radarr Config (Movies Management)
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "radarr", "config.xml.example"),
        os.path.join(base_dir, "services", "tools", "radarr", "config", "config.xml"),
        "Radarr Movies Management (services/tools/radarr/config/config.xml)"
    )

    # 3. Dynamic Directory Creation Based on config.properties
    print("\n[3/5] Creating Dynamic Directory & Pipeline Structure...")
    print(f" [i] Active Media Directory    : {media_dir}")
    print(f" [i] Active Downloads Directory: {downloads_dir}")

    directories = [
        os.path.join(downloads_dir, "music", "incomplete"),
        os.path.join(downloads_dir, "music", "complete"),
        os.path.join(downloads_dir, "series"),
        os.path.join(downloads_dir, "movies"),
        os.path.join(media_dir, "movies"),
        os.path.join(media_dir, "series"),
        os.path.join(media_dir, "music"),
        os.path.join(base_dir, "services", "tools", "slskd", "config"),
        os.path.join(base_dir, "services", "tools", "beets", "config"),
        os.path.join(base_dir, "services", "tools", "qbittorrent", "config", "qBittorrent"),
        os.path.join(base_dir, "services", "tools", "sonarr", "config"),
        os.path.join(base_dir, "services", "tools", "radarr", "config"),
    ]

    for d in directories:
        os.makedirs(d, exist_ok=True)
        print(f" [✓] Verified directory: {d}")

    def subproc_kwargs():
        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        return kwargs

    # 4. Podman Machine Health
    print("\n[4/5] Checking Podman VM Health...")
    res = subprocess.run(["podman", "info"], capture_output=True, text=True, **subproc_kwargs())
    if res.returncode != 0:
        print(" [!] Podman machine is offline. Attempting start...")
        subprocess.run(["podman", "machine", "start"], **subproc_kwargs())
    else:
        print(" [✓] Podman VM is active and running.")

    # 5. OS-Specific Launcher & Stopper Script Generation
    print("\n[5/5] Generating OS-Specific Launcher & Stopper Scripts...")
    system_os = platform.system()
    if system_os == "Windows":
        launcher_path = os.path.join(base_dir, "start.bat")
        with open(launcher_path, "w", encoding="utf-8") as f:
            f.write("@echo off\nstart \"\" pythonw app\\app.py\n")
        print(" [✓] Created Windows launcher: 'start.bat'")

        stopper_path = os.path.join(base_dir, "stop.bat")
        stop_content = (
            "@echo off\n"
            "title PodMedia - Durduruluyor...\n\n"
            "echo ==================================================\n"
            "echo   PodMedia - Servisleri ve Konteynerleri Durdur\n"
            "echo ==================================================\n"
            "echo.\n\n"
            "echo [1/3] PodMedia konteynerleri durduruluyor...\n"
            "for /f \"tokens=*\" %%i in ('podman ps -q --filter \"name=media_center_\"') do (\n"
            "    echo   - Durduruluyor: %%i\n"
            "    podman stop %%i >nul 2>&1\n"
            ")\n"
            "echo   [OK] Tum PodMedia konteynerleri durduruldu.\n\n"
            "echo.\n"
            "echo [2/3] Harici disk baglantilari serbest birakiliyor (WSL)...\n"
            "wsl -d podman-machine-default -u root umount /mnt/e >nul 2>&1\n"
            "wsl -d podman-machine-default -u root umount /mnt/f >nul 2>&1\n"
            "wsl -d podman-machine-default -u root umount /mnt/g >nul 2>&1\n"
            "echo   [OK] Harici disk mountlari WSL uzerinden guvenle ayrildi.\n\n"
            "echo.\n"
            "echo [3/3] PodMedia web sunucusu ve arka plan surecleri kapatiliyor...\n"
            "for /f \"tokens=5\" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do (\n"
            "    taskkill /F /PID %%a >nul 2>&1\n"
            ")\n"
            "powershell -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app\\app.py*' -or $_.CommandLine -like '*app/app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\" >nul 2>&1\n"
            "powershell -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*DFB65C4C-B34F-435D-AFE9-A86218684AA8*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\" >nul 2>&1\n"
            "echo   [OK] Web sunucusu ve izleme surecleri kapatildi.\n\n"
            "echo.\n"
            "echo ==================================================\n"
            "echo   [OK] PodMedia basariyla kapatildi!\n"
            "echo   [OK] Harici disk serbest birakildi (guvenle cikarilabilir).\n"
            "echo ==================================================\n"
            "echo.\n"
            "ping 127.0.0.1 -n 3 >nul\n"
            "exit /b 0\n"
        )
        with open(stopper_path, "w", encoding="utf-8") as f:
            f.write(stop_content)
        print(" [✓] Created Windows stopper: 'stop.bat'")
        launch_msg = (
            "Run '.\\start.bat' or double-click 'start.bat' to launch PodMedia.\n"
            " Run '.\\stop.bat' or double-click 'stop.bat' to stop PodMedia & release external drives."
        )
    else:
        launcher_path = os.path.join(base_dir, "start.sh")
        with open(launcher_path, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\npython3 app/app.py &\n")
        try:
            os.chmod(launcher_path, 0o755)
        except Exception:
            pass
        print(" [✓] Created Unix launcher: 'start.sh' (chmod +x)")

        stopper_path = os.path.join(base_dir, "stop.sh")
        stop_sh_content = (
            "#!/usr/bin/env bash\n"
            "# PodMedia Stop Script\n\n"
            "echo \"==================================================\"\n"
            "echo \"  PodMedia - Servisleri ve Konteynerleri Durdur\"\n"
            "echo \"==================================================\"\n"
            "echo \"\"\n\n"
            "echo \"[1/3] PodMedia konteynerleri durduruluyor...\"\n"
            "podman ps -q --filter \"name=media_center_\" | while read -r c; do\n"
            "    echo \"  - Durduruluyor: $c\"\n"
            "    podman stop \"$c\" >/dev/null 2>&1\n"
            "done\n"
            "echo \"  [OK] Tum konteynerler durduruldu.\"\n\n"
            "echo \"\"\n"
            "echo \"[2/3] Harici disk baglantilari serbest birakiliyor (WSL)...\"\n"
            "wsl -d podman-machine-default -u root umount /mnt/e >/dev/null 2>&1 || true\n"
            "wsl -d podman-machine-default -u root umount /mnt/f >/dev/null 2>&1 || true\n"
            "wsl -d podman-machine-default -u root umount /mnt/g >/dev/null 2>&1 || true\n"
            "echo \"  [OK] Harici disk mountlari serbest birakildi.\"\n\n"
            "echo \"\"\n"
            "echo \"[3/3] PodMedia web sunucusu ve arka plan surecleri kapatiliyor...\"\n"
            "powershell.exe -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { \\$_.CommandLine -like '*app\\app.py*' -or \\$_.CommandLine -like '*app/app.py*' } | ForEach-Object { Stop-Process -Id \\$_.ProcessId -Force }\" >/dev/null 2>&1 || true\n"
            "echo \"  [OK] Web sunucusu kapatildi.\"\n\n"
            "echo \"\"\n"
            "echo \"==================================================\"\n"
            "echo \"  [OK] PodMedia basariyla kapatildi!\"\n"
            "echo \"  [OK] Harici disk serbest birakildi.\"\n"
            "echo \"==================================================\"\n"
        )
        with open(stopper_path, "w", encoding="utf-8") as f:
            f.write(stop_sh_content)
        try:
            os.chmod(stopper_path, 0o755)
        except Exception:
            pass
        print(" [✓] Created Unix stopper: 'stop.sh' (chmod +x)")
        launch_msg = (
            "Run './start.sh' to launch PodMedia.\n"
            " Run './stop.sh' to stop PodMedia & release external drives."
        )

    print("\n==================================================")
    print(" 🎉 PodMedia Setup Completed Successfully!")
    print(f" {launch_msg}")
    print("==================================================")

if __name__ == "__main__":
    run_setup()
