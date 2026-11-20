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
    server_port = cfg.getint("server", "port", fallback=3000)
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

    # PlexTraktSync Config (Optional Plex <-> Trakt Sync)
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "plextraktsync", "config.yml.example"),
        os.path.join(base_dir, "services", "tools", "plextraktsync", "config", "config.yml"),
        "PlexTraktSync (services/tools/plextraktsync/config/config.yml)"
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
        os.path.join(base_dir, "services", "tools", "prowlarr", "config"),
        os.path.join(base_dir, "services", "tools", "plextraktsync", "config"),
    ]

    for d in directories:
        os.makedirs(d, exist_ok=True)
        print(f" [✓] Verified directory: {d}")

    def subproc_kwargs():
        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        return kwargs

    # 4. Podman Health / VM Check
    print("\n[4/5] Checking Podman Health...")
    system_os = platform.system()
    res = subprocess.run(["podman", "info"], capture_output=True, text=True, **subproc_kwargs())
    if res.returncode != 0:
        if system_os == "Linux":
            print(" [!] Podman is not ready. Attempting to start user podman.socket...")
            subprocess.run(["systemctl", "--user", "start", "podman.socket"], capture_output=True, text=True)
        else:
            print(" [!] Podman machine is offline. Attempting start...")
            subprocess.run(["podman", "machine", "start"], **subproc_kwargs())
    else:
        print(" [✓] Podman is active and running.")

    # 5. OS-Specific Launcher & Stopper Script Generation
    print("\n[5/5] Generating OS-Specific Launcher & Stopper Scripts...")
    if system_os == "Windows":
        pythonw_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw_exe):
            pythonw_exe = sys.executable
        app_py_path = os.path.join(base_dir, "app", "app.py")

        # Register user-session scheduled tasks so Windows Services (e.g. Homebridge LocalSystem)
        # can trigger PodMedia inside the interactive user's WSL/Podman session without UAC prompts.
        try:
            subprocess.run(
                ["schtasks", "/Create", "/TN", "PodMedia_Start", "/TR", f'"{pythonw_exe}" "{app_py_path}"', "/SC", "ONCE", "/ST", "00:00", "/F"],
                capture_output=True, text=True, **subproc_kwargs()
            )
            subprocess.run(
                ["schtasks", "/Create", "/TN", "PodMedia_StartPlex", "/TR", f'"{pythonw_exe}" "{app_py_path}" --start-plex', "/SC", "ONCE", "/ST", "00:00", "/F"],
                capture_output=True, text=True, **subproc_kwargs()
            )
            subprocess.run(
                ["schtasks", "/Create", "/TN", "PodMedia_StopPlex", "/TR", f'"{pythonw_exe}" "{app_py_path}" --stop-plex', "/SC", "ONCE", "/ST", "00:00", "/F"],
                capture_output=True, text=True, **subproc_kwargs()
            )
            subprocess.run(
                ["schtasks", "/Create", "/TN", "PodMedia_StartDownloads", "/TR", f'"{pythonw_exe}" "{app_py_path}" --start-downloads', "/SC", "ONCE", "/ST", "00:00", "/F"],
                capture_output=True, text=True, **subproc_kwargs()
            )
            subprocess.run(
                ["schtasks", "/Create", "/TN", "PodMedia_StopDownloads", "/TR", f'"{pythonw_exe}" "{app_py_path}" --stop-downloads', "/SC", "ONCE", "/ST", "00:00", "/F"],
                capture_output=True, text=True, **subproc_kwargs()
            )
            subprocess.run(
                ["schtasks", "/Create", "/TN", "PodMedia_Stop", "/TR", f'"{pythonw_exe}" "{app_py_path}" --stop', "/SC", "ONCE", "/ST", "00:00", "/F"],
                capture_output=True, text=True, **subproc_kwargs()
            )
            print(" [✓] Registered Windows Scheduled Tasks: 'PodMedia_Start', 'PodMedia_StartPlex', 'PodMedia_StopPlex', 'PodMedia_StartDownloads', 'PodMedia_StopDownloads', 'PodMedia_Stop'")
        except Exception as e:
            print(" [!] Note: Scheduled task registration skipped:", e)

        launcher_path = os.path.join(base_dir, "start.bat")
        start_bat_content = (
            "@echo off\n"
            "if not exist \"%~dp0podmedia.running\" echo {\"status\":\"starting\"} > \"%~dp0podmedia.running\"\n"
            f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/ping >nul 2>&1\n"
            "if not errorlevel 1 (\n"
            f"    start \"\" http://localhost:{server_port}\n"
            "    exit /b 0\n"
            ")\n"
            "schtasks /End /TN \"PodMedia_Start\" >nul 2>&1\n"
            "schtasks /Run /TN \"PodMedia_Start\" >nul 2>&1\n"
            "if errorlevel 1 (\n"
            f"    \"{pythonw_exe}\" \"%~dp0app\\app.py\"\n"
            ")\n"
            "exit /b 0\n"
        )
        with open(launcher_path, "w", encoding="utf-8") as f:
            f.write(start_bat_content)
        print(" [✓] Created Windows launcher: 'start.bat'")

        plex_launcher_path = os.path.join(base_dir, "start_plex.bat")
        start_plex_bat_content = (
            "@echo off\n"
            "if not exist \"%~dp0podmedia.running\" echo {\"status\":\"starting\"} > \"%~dp0podmedia.running\"\n"
            "if not exist \"%~dp0plex.running\" echo {\"status\":\"starting\"} > \"%~dp0plex.running\"\n"
            f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/autostart/plex >nul 2>&1\n"
            "if not errorlevel 1 (\n"
            "    exit /b 0\n"
            ")\n"
            "schtasks /End /TN \"PodMedia_StartPlex\" >nul 2>&1\n"
            "schtasks /Run /TN \"PodMedia_StartPlex\" >nul 2>&1\n"
            "if errorlevel 1 (\n"
            f"    \"{pythonw_exe}\" \"%~dp0app\\app.py\" --start-plex\n"
            ")\n"
            "exit /b 0\n"
        )
        with open(plex_launcher_path, "w", encoding="utf-8") as f:
            f.write(start_plex_bat_content)
        print(" [✓] Created Windows Plex-only launcher: 'start_plex.bat'")

        plex_stopper_path = os.path.join(base_dir, "stop_plex.bat")
        stop_plex_bat_content = (
            "@echo off\n"
            "if exist \"%~dp0plex.running\" del /f /q \"%~dp0plex.running\" >nul 2>&1\n"
            "if not exist \"%~dp0downloads.running\" (\n"
            "    if exist \"%~dp0podmedia.running\" del /f /q \"%~dp0podmedia.running\" >nul 2>&1\n"
            ")\n"
            f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/autostop/plex >nul 2>&1\n"
            "if not errorlevel 1 (\n"
            "    exit /b 0\n"
            ")\n"
            "schtasks /End /TN \"PodMedia_StopPlex\" >nul 2>&1\n"
            "schtasks /Run /TN \"PodMedia_StopPlex\" >nul 2>&1\n"
            "if errorlevel 1 (\n"
            f"    \"{pythonw_exe}\" \"%~dp0app\\app.py\" --stop-plex\n"
            ")\n"
            "exit /b 0\n"
        )
        with open(plex_stopper_path, "w", encoding="utf-8") as f:
            f.write(stop_plex_bat_content)
        print(" [✓] Created Windows Plex stopper: 'stop_plex.bat'")

        downloads_launcher_path = os.path.join(base_dir, "start_downloads.bat")
        start_downloads_bat_content = (
            "@echo off\n"
            "if not exist \"%~dp0podmedia.running\" echo {\"status\":\"starting\"} > \"%~dp0podmedia.running\"\n"
            "if not exist \"%~dp0downloads.running\" echo {\"status\":\"starting\"} > \"%~dp0downloads.running\"\n"
            f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/autostart/downloads >nul 2>&1\n"
            "if not errorlevel 1 (\n"
            "    exit /b 0\n"
            ")\n"
            "schtasks /End /TN \"PodMedia_StartDownloads\" >nul 2>&1\n"
            "schtasks /Run /TN \"PodMedia_StartDownloads\" >nul 2>&1\n"
            "if errorlevel 1 (\n"
            f"    \"{pythonw_exe}\" \"%~dp0app\\app.py\" --start-downloads\n"
            ")\n"
            "exit /b 0\n"
        )
        with open(downloads_launcher_path, "w", encoding="utf-8") as f:
            f.write(start_downloads_bat_content)
        print(" [✓] Created Windows Downloads launcher: 'start_downloads.bat'")

        downloads_stopper_path = os.path.join(base_dir, "stop_downloads.bat")
        stop_downloads_bat_content = (
            "@echo off\n"
            "if exist \"%~dp0downloads.running\" del /f /q \"%~dp0downloads.running\" >nul 2>&1\n"
            "if not exist \"%~dp0plex.running\" (\n"
            "    if exist \"%~dp0podmedia.running\" del /f /q \"%~dp0podmedia.running\" >nul 2>&1\n"
            ")\n"
            f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/autostop/downloads >nul 2>&1\n"
            "if not errorlevel 1 (\n"
            "    exit /b 0\n"
            ")\n"
            "schtasks /End /TN \"PodMedia_StopDownloads\" >nul 2>&1\n"
            "schtasks /Run /TN \"PodMedia_StopDownloads\" >nul 2>&1\n"
            "if errorlevel 1 (\n"
            f"    \"{pythonw_exe}\" \"%~dp0app\\app.py\" --stop-downloads\n"
            ")\n"
            "exit /b 0\n"
        )
        with open(downloads_stopper_path, "w", encoding="utf-8") as f:
            f.write(stop_downloads_bat_content)
        print(" [✓] Created Windows Downloads stopper: 'stop_downloads.bat'")

        status_path = os.path.join(base_dir, "status.bat")
        status_content = (
            "@echo off\n"
            f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/ping >nul 2>&1\n"
            "if not errorlevel 1 (\n"
            "    echo 0\n"
            "    exit /b 0\n"
            ") else (\n"
            "    if exist \"%~dp0podmedia.running\" del /f /q \"%~dp0podmedia.running\" >nul 2>&1\n"
            "    if exist \"%~dp0plex.running\" del /f /q \"%~dp0plex.running\" >nul 2>&1\n"
            "    if exist \"%~dp0downloads.running\" del /f /q \"%~dp0downloads.running\" >nul 2>&1\n"
            "    echo 1\n"
            "    exit /b 1\n"
            ")\n"
        )
        with open(status_path, "w", encoding="utf-8") as f:
            f.write(status_content)
        print(" [✓] Created Windows status checker: 'status.bat'")

        stopper_path = os.path.join(base_dir, "stop.bat")
        stop_content = (
            "@echo off\n"
            "title PodMedia - Durduruluyor...\n\n"
            "echo ==================================================\n"
            "echo   PodMedia - Servisleri ve Konteynerleri Durdur\n"
            "echo ==================================================\n"
            "echo.\n\n"
            "if exist \"%~dp0podmedia.running\" del /f /q \"%~dp0podmedia.running\" >nul 2>&1\n"
            "if exist \"%~dp0plex.running\" del /f /q \"%~dp0plex.running\" >nul 2>&1\n"
            "if exist \"%~dp0downloads.running\" del /f /q \"%~dp0downloads.running\" >nul 2>&1\n"
            f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/system/quit >nul 2>&1\n"
            "if not errorlevel 1 (\n"
            "    echo [OK] PodMedia kapatma ve disk ayirma islemi baslatildi.\n"
            "    exit /b 0\n"
            ")\n\n"
            "echo [1/3] PodMedia konteynerleri durduruluyor...\n"
            "for /f \"tokens=*\" %%i in ('podman ps -q --filter \"name=media_center_\"') do (\n"
            "    echo   - Durduruluyor: %%i\n"
            "    podman stop %%i >nul 2>&1\n"
            ")\n"
            "echo   [OK] Tum PodMedia konteynerleri durduruldu.\n\n"
            "echo.\n"
            "echo [2/3] Harici disk baglantilari serbest birakiliyor...\n"
            "wsl -d podman-machine-default -u root umount /mnt/e >nul 2>&1\n"
            "wsl -d podman-machine-default -u root umount /mnt/f >nul 2>&1\n"
            "wsl -d podman-machine-default -u root umount /mnt/g >nul 2>&1\n"
            "schtasks /Run /TN \"PodMedia_Stop\" >nul 2>&1\n"
            f"\"{sys.executable}\" -c \"import sys; sys.path.append('app'); from disk_manager import disk_manager; disk_manager.release_all_external_drives()\" >nul 2>&1\n"
            "echo   [OK] Harici disk baglantilari ve kilitleri guvenle ayrildi.\n\n"
            "echo.\n"
            "echo [3/3] PodMedia web sunucusu ve arka plan surecleri kapatiliyor...\n"
            f"for /f \"tokens=5\" %%a in ('netstat -aon ^| findstr :{server_port} ^| findstr LISTENING') do (\n"
            "    taskkill /F /PID %%a >nul 2>&1\n"
            ")\n"
            "powershell -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app\\app.py*' -or $_.CommandLine -like '*app/app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\" >nul 2>&1\n"
            "echo   [OK] Web sunucusu ve izleme surecleri kapatildi.\n\n"
            "echo.\n"
            "echo ==================================================\n"
            "echo   [OK] PodMedia basariyla kapatildi!\n"
            "echo   [OK] Harici disk serbest birakildi (guvenle cikarilabilir).\n"
            "echo ==================================================\n"
            "exit /b 0\n"
        )
        with open(stopper_path, "w", encoding="utf-8") as f:
            f.write(stop_content)
        print(" [✓] Created Windows stopper: 'stop.bat'")
        launch_msg = (
            "Run '.\\start.bat' or double-click 'start.bat' to launch PodMedia.\n"
            " Run '.\\start_plex.bat' / '.\\stop_plex.bat' to control Plex mode.\n"
            " Run '.\\start_downloads.bat' / '.\\stop_downloads.bat' to control Downloads mode.\n"
            " Run '.\\stop.bat' or double-click 'stop.bat' to stop PodMedia & release external drives."
        )
    else:
        python_exe = sys.executable or "python3"

        def write_sh(filename, content):
            s_path = os.path.join(base_dir, filename)
            with open(s_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            try:
                os.chmod(s_path, 0o755)
            except Exception:
                pass
            return s_path

        start_sh_content = (
            "#!/usr/bin/env bash\n"
            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            "[ ! -f \"$SCRIPT_DIR/podmedia.running\" ] && echo '{\"status\":\"starting\"}' > \"$SCRIPT_DIR/podmedia.running\"\n"
            f"if curl -s -o /dev/null -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/ping >/dev/null 2>&1; then\n"
            "    exit 0\n"
            "fi\n"
            f"nohup \"{python_exe}\" \"$SCRIPT_DIR/app/app.py\" >/dev/null 2>&1 &\n"
            "exit 0\n"
        )
        write_sh("start.sh", start_sh_content)
        print(" [✓] Created Unix launcher: 'start.sh' (chmod +x)")

        for mode in ("plex", "downloads"):
            other_mode = "downloads" if mode == "plex" else "plex"
            start_mode_sh = (
                "#!/usr/bin/env bash\n"
                "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
                "[ ! -f \"$SCRIPT_DIR/podmedia.running\" ] && echo '{\"status\":\"starting\"}' > \"$SCRIPT_DIR/podmedia.running\"\n"
                f"[ ! -f \"$SCRIPT_DIR/{mode}.running\" ] && echo '{{\"status\":\"starting\"}}' > \"$SCRIPT_DIR/{mode}.running\"\n"
                f"if curl -s -o /dev/null -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/autostart/{mode} >/dev/null 2>&1; then\n"
                "    exit 0\n"
                "fi\n"
                f"nohup \"{python_exe}\" \"$SCRIPT_DIR/app/app.py\" --start-{mode} >/dev/null 2>&1 &\n"
                "exit 0\n"
            )
            write_sh(f"start_{mode}.sh", start_mode_sh)
            print(f" [✓] Created Unix script: 'start_{mode}.sh' (chmod +x)")

            stop_mode_sh = (
                "#!/usr/bin/env bash\n"
                "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
                f"rm -f \"$SCRIPT_DIR/{mode}.running\"\n"
                f"if [ ! -f \"$SCRIPT_DIR/{other_mode}.running\" ]; then\n"
                "    rm -f \"$SCRIPT_DIR/podmedia.running\"\n"
                "fi\n"
                f"if curl -s -o /dev/null -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/autostop/{mode} >/dev/null 2>&1; then\n"
                "    exit 0\n"
                "fi\n"
                f"nohup \"{python_exe}\" \"$SCRIPT_DIR/app/app.py\" --stop-{mode} >/dev/null 2>&1 &\n"
                "exit 0\n"
            )
            write_sh(f"stop_{mode}.sh", stop_mode_sh)
            print(f" [✓] Created Unix script: 'stop_{mode}.sh' (chmod +x)")

        status_sh_content = (
            "#!/usr/bin/env bash\n"
            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            f"if curl -s -o /dev/null -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/ping >/dev/null 2>&1; then\n"
            "    echo 0\n"
            "    exit 0\n"
            "else\n"
            "    rm -f \"$SCRIPT_DIR/podmedia.running\" \"$SCRIPT_DIR/plex.running\" \"$SCRIPT_DIR/downloads.running\"\n"
            "    echo 1\n"
            "    exit 1\n"
            "fi\n"
        )
        write_sh("status.sh", status_sh_content)
        print(" [✓] Created Unix status checker: 'status.sh' (chmod +x)")

        stop_sh_content = (
            "#!/usr/bin/env bash\n"
            "# PodMedia Stop Script\n"
            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            "rm -f \"$SCRIPT_DIR/podmedia.running\" \"$SCRIPT_DIR/plex.running\" \"$SCRIPT_DIR/downloads.running\"\n\n"
            f"if curl -s -o /dev/null -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/system/quit >/dev/null 2>&1; then\n"
            "    echo \"[OK] PodMedia shutdown initiated.\"\n"
            "    exit 0\n"
            "fi\n\n"
            "echo \"[1/3] Stopping PodMedia containers...\"\n"
            "podman ps -q --filter \"name=media_center_\" | while read -r c; do\n"
            "    [ -n \"$c\" ] && podman stop \"$c\" >/dev/null 2>&1\n"
            "done\n"
            "echo \"[2/3] Releasing external drive mounts (if any)...\"\n"
            f"\"{python_exe}\" -c \"import sys; sys.path.append('$SCRIPT_DIR/app'); from disk_manager import disk_manager; disk_manager.release_all_external_drives()\" >/dev/null 2>&1 || true\n"
            "echo \"[3/3] Stopping PodMedia background server...\"\n"
            "pkill -f \"app/app.py\" >/dev/null 2>&1 || true\n"
            "echo \"[OK] PodMedia stopped.\"\n"
            "exit 0\n"
        )
        write_sh("stop.sh", stop_sh_content)
        print(" [✓] Created Unix stopper: 'stop.sh' (chmod +x)")
        launch_msg = (
            "Run './start.sh' to launch PodMedia.\n"
            " Run './start_plex.sh' / './stop_plex.sh' to control Plex mode.\n"
            " Run './start_downloads.sh' / './stop_downloads.sh' to control Downloads mode.\n"
            " Run './stop.sh' to stop PodMedia & release external drives."
        )

    print("\n==================================================")
    print(" 🎉 PodMedia Setup Completed Successfully!")
    print(f" {launch_msg}")
    print("==================================================")

if __name__ == "__main__":
    run_setup()
