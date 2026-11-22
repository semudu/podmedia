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
    scripts_dir = os.path.join(base_dir, "scripts")
    run_dir = os.path.join(base_dir, "run")

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

    config_file = os.path.join(base_dir, "config.properties")
    example_config = os.path.join(base_dir, "config.properties.example")
    copy_template_if_missing(example_config, config_file, "Root Server Properties (config.properties)")

    cfg = configparser.ConfigParser()
    cfg.read(config_file)
    server_port = cfg.getint("server", "port", fallback=3000)
    raw_media_dir = cfg.get("paths", "media_dir", fallback="./media")
    raw_downloads_dir = cfg.get("paths", "downloads_dir", fallback="./downloads")

    media_dir = os.path.abspath(os.path.join(base_dir, raw_media_dir)) if not os.path.isabs(raw_media_dir) else raw_media_dir
    downloads_dir = os.path.abspath(os.path.join(base_dir, raw_downloads_dir)) if not os.path.isabs(raw_downloads_dir) else raw_downloads_dir

    copy_template_if_missing(
        os.path.join(base_dir, "services", "plex", ".env.example"),
        os.path.join(base_dir, "services", "plex", ".env"),
        "Plex Local Environment (services/plex/.env)"
    )
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "slskd", "slskd.yml.example"),
        os.path.join(base_dir, "services", "tools", "slskd", "config", "slskd.yml"),
        "Slskd Downloader (services/tools/slskd/config/slskd.yml)"
    )
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "qbittorrent", "qBittorrent.conf.example"),
        os.path.join(base_dir, "services", "tools", "qbittorrent", "config", "qBittorrent", "qBittorrent.conf"),
        "qBittorrent Client (services/tools/qbittorrent/config/qBittorrent/qBittorrent.conf)"
    )
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "sonarr", "config.xml.example"),
        os.path.join(base_dir, "services", "tools", "sonarr", "config", "config.xml"),
        "Sonarr Series Management (services/tools/sonarr/config/config.xml)"
    )
    copy_template_if_missing(
        os.path.join(base_dir, "services", "tools", "radarr", "config.xml.example"),
        os.path.join(base_dir, "services", "tools", "radarr", "config", "config.xml"),
        "Radarr Movies Management (services/tools/radarr/config/config.xml)"
    )
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
        scripts_dir,
        run_dir,
        os.path.join(downloads_dir, "music", "incomplete"),
        os.path.join(downloads_dir, "music", "complete"),
        os.path.join(downloads_dir, "series"),
        os.path.join(downloads_dir, "movies"),
        os.path.join(media_dir, "movies"),
        os.path.join(media_dir, "series"),
        os.path.join(media_dir, "music"),
        os.path.join(base_dir, "services", "tools", "slskd", "config"),
        os.path.join(base_dir, "services", "tools", "qbittorrent", "config", "qBittorrent"),
        os.path.join(base_dir, "services", "tools", "sonarr", "config"),
        os.path.join(base_dir, "services", "tools", "radarr", "config"),
        os.path.join(base_dir, "services", "tools", "prowlarr", "config"),
        os.path.join(base_dir, "services", "tools", "aurral", "config"),
        os.path.join(base_dir, "services", "tools", "plextraktsync", "config"),
    ]

    for d in directories:
        os.makedirs(d, exist_ok=True)
        print(f" [✓] Verified directory: {d}")

    # Clean up legacy root-level generated scripts & state files from older layouts
    legacy_root_files = [
        "start.bat", "stop.bat", "status.bat",
        "start_plex.bat", "stop_plex.bat",
        "start_video.bat", "stop_video.bat",
        "start_downloads.bat", "stop_downloads.bat",
        "start_music.bat", "stop_music.bat",
        "start.sh", "stop.sh", "status.sh",
        "start_plex.sh", "stop_plex.sh",
        "start_video.sh", "stop_video.sh",
        "start_downloads.sh", "stop_downloads.sh",
        "start_music.sh", "stop_music.sh",
        "podmedia.running", "plex.running", "video.running", "downloads.running", "music.running",
    ]
    for lf in legacy_root_files:
        lp = os.path.join(base_dir, lf)
        if os.path.exists(lp):
            try:
                os.remove(lp)
                print(f" [i] Cleaned legacy root file: {lf}")
            except Exception:
                pass

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

    # 5. OS-Specific Launcher & Stopper Script Generation (under scripts/, state under run/)
    print("\n[5/5] Generating OS-Specific Launcher & Stopper Scripts (scripts/ & run/)...")
    all_modes = ("video", "downloads", "music")

    if system_os == "Windows":
        pythonw_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw_exe):
            pythonw_exe = sys.executable
        app_py_path = os.path.join(base_dir, "app", "app.py")

        # Register user-session scheduled tasks so Windows Services (e.g. Homebridge LocalSystem)
        # can trigger PodMedia inside the interactive user's WSL/Podman session without UAC prompts.
        try:
            tasks = [
                ("PodMedia_Start", f'"{pythonw_exe}" "{app_py_path}"'),
                ("PodMedia_StartVideo", f'"{pythonw_exe}" "{app_py_path}" --start-video'),
                ("PodMedia_StopVideo", f'"{pythonw_exe}" "{app_py_path}" --stop-video'),
                ("PodMedia_StartDownloads", f'"{pythonw_exe}" "{app_py_path}" --start-downloads'),
                ("PodMedia_StopDownloads", f'"{pythonw_exe}" "{app_py_path}" --stop-downloads'),
                ("PodMedia_StartMusic", f'"{pythonw_exe}" "{app_py_path}" --start-music'),
                ("PodMedia_StopMusic", f'"{pythonw_exe}" "{app_py_path}" --stop-music'),
                ("PodMedia_Stop", f'"{pythonw_exe}" "{app_py_path}" --stop'),
            ]
            for tn, tr in tasks:
                subprocess.run(
                    ["schtasks", "/Create", "/TN", tn, "/TR", tr, "/SC", "ONCE", "/ST", "00:00", "/F"],
                    capture_output=True, text=True, **subproc_kwargs()
                )
            for old_tn in ("PodMedia_StartPlex", "PodMedia_StopPlex"):
                subprocess.run(["schtasks", "/Delete", "/TN", old_tn, "/F"], capture_output=True, text=True, **subproc_kwargs())
            print(" [✓] Registered Windows Scheduled Tasks:", ", ".join(f"'{t[0]}'" for t in tasks))
        except Exception as e:
            print(" [!] Note: Scheduled task registration skipped:", e)

        def write_bat(filename, content):
            p = os.path.join(scripts_dir, filename)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            print(f" [✓] Created Windows script: 'scripts/{filename}'")

        root_header = (
            "@echo off\n"
            "for %%I in (\"%~dp0..\") do set \"ROOT=%%~fI\"\n"
            "set \"RUN_DIR=%ROOT%\\run\"\n"
            "if not exist \"%RUN_DIR%\" mkdir \"%RUN_DIR%\" >nul 2>&1\n"
        )

        write_bat("start.bat", (
            root_header
            + "if not exist \"%RUN_DIR%\\podmedia.running\" echo {\"status\":\"starting\"} > \"%RUN_DIR%\\podmedia.running\"\n"
            + f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/ping >nul 2>&1\n"
            + "if not errorlevel 1 (\n"
            + f"    start \"\" http://localhost:{server_port}\n"
            + "    exit /b 0\n"
            + ")\n"
            + "schtasks /End /TN \"PodMedia_Start\" >nul 2>&1\n"
            + "schtasks /Run /TN \"PodMedia_Start\" >nul 2>&1\n"
            + "if errorlevel 1 (\n"
            + f"    \"{pythonw_exe}\" \"%ROOT%\\app\\app.py\"\n"
            + ")\n"
            + "exit /b 0\n"
        ))

        task_cap = {"video": "Video", "downloads": "Downloads", "music": "Music"}
        for mode in all_modes:
            cap = task_cap[mode]
            other_modes = [m for m in all_modes if m != mode]
            other_checks = " ".join([f"if not exist \"%RUN_DIR%\\{m}.running\"" for m in other_modes])

            write_bat(f"start_{mode}.bat", (
                root_header
                + "if not exist \"%RUN_DIR%\\podmedia.running\" echo {\"status\":\"starting\"} > \"%RUN_DIR%\\podmedia.running\"\n"
                + f"if not exist \"%RUN_DIR%\\{mode}.running\" echo {{\"status\":\"starting\"}} > \"%RUN_DIR%\\{mode}.running\"\n"
                + f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/autostart/{mode} >nul 2>&1\n"
                + "if not errorlevel 1 (\n"
                + "    exit /b 0\n"
                + ")\n"
                + f"schtasks /End /TN \"PodMedia_Start{cap}\" >nul 2>&1\n"
                + f"schtasks /Run /TN \"PodMedia_Start{cap}\" >nul 2>&1\n"
                + "if errorlevel 1 (\n"
                + f"    \"{pythonw_exe}\" \"%ROOT%\\app\\app.py\" --start-{mode}\n"
                + ")\n"
                + "exit /b 0\n"
            ))

            write_bat(f"stop_{mode}.bat", (
                root_header
                + f"if exist \"%RUN_DIR%\\{mode}.running\" del /f /q \"%RUN_DIR%\\{mode}.running\" >nul 2>&1\n"
                + f"{other_checks} (\n"
                + "    if exist \"%RUN_DIR%\\podmedia.running\" del /f /q \"%RUN_DIR%\\podmedia.running\" >nul 2>&1\n"
                + ")\n"
                + f"curl.exe -s -o nul -f --connect-timeout 2 --max-time 5 http://127.0.0.1:{server_port}/api/autostop/{mode} >nul 2>&1\n"
                + "if not errorlevel 1 (\n"
                + "    exit /b 0\n"
                + ")\n"
                + f"schtasks /End /TN \"PodMedia_Stop{cap}\" >nul 2>&1\n"
                + f"schtasks /Run /TN \"PodMedia_Stop{cap}\" >nul 2>&1\n"
                + "if errorlevel 1 (\n"
                + f"    \"{pythonw_exe}\" \"%ROOT%\\app\\app.py\" --stop-{mode}\n"
                + ")\n"
                + "exit /b 0\n"
            ))

        write_bat("status.bat", (
            root_header
            + f"curl.exe -s -o nul -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/ping >nul 2>&1\n"
            + "if errorlevel 1 (\n"
            + "    if exist \"%RUN_DIR%\\podmedia.running\" del /f /q \"%RUN_DIR%\\podmedia.running\" >nul 2>&1\n"
            + "    if exist \"%RUN_DIR%\\video.running\" del /f /q \"%RUN_DIR%\\video.running\" >nul 2>&1\n"
            + "    if exist \"%RUN_DIR%\\downloads.running\" del /f /q \"%RUN_DIR%\\downloads.running\" >nul 2>&1\n"
            + "    if exist \"%RUN_DIR%\\music.running\" del /f /q \"%RUN_DIR%\\music.running\" >nul 2>&1\n"
            + "    echo 1\n"
            + "    exit /b 1\n"
            + ")\n"
            + "if \"%~1\"==\"\" (\n"
            + "    echo 0\n"
            + "    exit /b 0\n"
            + ")\n"
            + "if exist \"%RUN_DIR%\\%~1.running\" (\n"
            + "    echo 0\n"
            + "    exit /b 0\n"
            + ") else (\n"
            + "    echo 1\n"
            + "    exit /b 1\n"
            + ")\n"
        ))

        write_bat("stop.bat", (
            root_header
            + "title PodMedia - Durduruluyor...\n\n"
            + "echo ==================================================\n"
            + "echo   PodMedia - Servisleri ve Konteynerleri Durdur\n"
            + "echo ==================================================\n"
            + "echo.\n\n"
            + "if exist \"%RUN_DIR%\\podmedia.running\" del /f /q \"%RUN_DIR%\\podmedia.running\" >nul 2>&1\n"
            + "if exist \"%RUN_DIR%\\video.running\" del /f /q \"%RUN_DIR%\\video.running\" >nul 2>&1\n"
            + "if exist \"%RUN_DIR%\\downloads.running\" del /f /q \"%RUN_DIR%\\downloads.running\" >nul 2>&1\n"
            + "if exist \"%RUN_DIR%\\music.running\" del /f /q \"%RUN_DIR%\\music.running\" >nul 2>&1\n"
            + f"curl.exe -s -o nul -f --connect-timeout 2 --max-time 5 http://127.0.0.1:{server_port}/api/system/quit >nul 2>&1\n"
            + "if not errorlevel 1 (\n"
            + "    echo [OK] PodMedia kapatma ve disk ayirma islemi baslatildi.\n"
            + "    exit /b 0\n"
            + ")\n\n"
            + "echo [1/3] PodMedia konteynerleri durduruluyor...\n"
            + "for /f \"tokens=*\" %%i in ('podman ps -q --filter \"name=media_center_\"') do (\n"
            + "    echo   - Durduruluyor: %%i\n"
            + "    podman stop %%i >nul 2>&1\n"
            + ")\n"
            + "echo   [OK] Tum PodMedia konteynerleri durduruldu.\n\n"
            + "echo.\n"
            + "echo [2/3] Harici disk baglantilari serbest birakiliyor...\n"
            + "wsl -d podman-machine-default -u root umount -l /mnt/e >nul 2>&1\n"
            + "wsl -d podman-machine-default -u root umount -l /mnt/f >nul 2>&1\n"
            + "wsl -d podman-machine-default -u root umount -l /mnt/g >nul 2>&1\n"
            + "schtasks /Run /TN \"PodMedia_Stop\" >nul 2>&1\n"
            + f"\"{sys.executable}\" -c \"import sys; sys.path.append(r'%ROOT%\\app'); from disk_manager import disk_manager; disk_manager.release_all_external_drives()\" >nul 2>&1\n"
            + "echo   [OK] Harici disk baglantilari ve kilitleri guvenle ayrildi.\n\n"
            + "echo.\n"
            + "echo [3/3] PodMedia web sunucusu ve arka plan surecleri kapatiliyor...\n"
            + f"for /f \"tokens=5\" %%a in ('netstat -aon ^| findstr :{server_port} ^| findstr LISTENING') do (\n"
            + "    taskkill /F /PID %%a >nul 2>&1\n"
            + ")\n"
            + "powershell -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app\\app.py*' -or $_.CommandLine -like '*app/app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\" >nul 2>&1\n"
            + "echo   [OK] Web sunucusu ve izleme surecleri kapatildi.\n\n"
            + "echo.\n"
            + "echo ==================================================\n"
            + "echo   [OK] PodMedia basariyla kapatildi!\n"
            + "echo   [OK] Harici disk uyku moduna (0 RPM) alindi.\n"
            + "echo ==================================================\n"
            + "exit /b 0\n"
        ))

        launch_msg = (
            "Run 'scripts\\start.bat' to launch PodMedia.\n"
            " Run 'scripts\\start_video.bat' / 'scripts\\stop_video.bat' to control Video (Cinema) mode.\n"
            " Run 'scripts\\start_downloads.bat' / 'scripts\\stop_downloads.bat' to control Downloads mode.\n"
            " Run 'scripts\\start_music.bat' / 'scripts\\stop_music.bat' to control Music (Plexamp) mode.\n"
            " Run 'scripts\\stop.bat' to stop PodMedia & sleep external drives."
        )
    else:
        python_exe = sys.executable or "python3"

        def write_sh(filename, content):
            s_path = os.path.join(scripts_dir, filename)
            with open(s_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            try:
                os.chmod(s_path, 0o755)
            except Exception:
                pass
            print(f" [✓] Created Unix script: 'scripts/{filename}' (chmod +x)")
            return s_path

        sh_header = (
            "#!/usr/bin/env bash\n"
            "ROOT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/..\" && pwd)\"\n"
            "RUN_DIR=\"$ROOT_DIR/run\"\n"
            "mkdir -p \"$RUN_DIR\"\n"
        )

        write_sh("start.sh", (
            sh_header
            + "[ ! -f \"$RUN_DIR/podmedia.running\" ] && echo '{\"status\":\"starting\"}' > \"$RUN_DIR/podmedia.running\"\n"
            + f"if curl -s -o /dev/null -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/ping >/dev/null 2>&1; then\n"
            + "    exit 0\n"
            + "fi\n"
            + f"nohup \"{python_exe}\" \"$ROOT_DIR/app/app.py\" >/dev/null 2>&1 &\n"
            + "exit 0\n"
        ))

        for mode in all_modes:
            other_modes = [m for m in all_modes if m != mode]
            other_cond = " && ".join([f"[ ! -f \"$RUN_DIR/{m}.running\" ]" for m in other_modes])

            write_sh(f"start_{mode}.sh", (
                sh_header
                + "[ ! -f \"$RUN_DIR/podmedia.running\" ] && echo '{\"status\":\"starting\"}' > \"$RUN_DIR/podmedia.running\"\n"
                + f"[ ! -f \"$RUN_DIR/{mode}.running\" ] && echo '{{\"status\":\"starting\"}}' > \"$RUN_DIR/{mode}.running\"\n"
                + f"if curl -s -o /dev/null -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/autostart/{mode} >/dev/null 2>&1; then\n"
                + "    exit 0\n"
                + "fi\n"
                + f"nohup \"{python_exe}\" \"$ROOT_DIR/app/app.py\" --start-{mode} >/dev/null 2>&1 &\n"
                + "exit 0\n"
            ))

            write_sh(f"stop_{mode}.sh", (
                sh_header
                + f"rm -f \"$RUN_DIR/{mode}.running\"\n"
                + f"if {other_cond}; then\n"
                + "    rm -f \"$RUN_DIR/podmedia.running\"\n"
                + "fi\n"
                + f"if curl -s -o /dev/null -f --connect-timeout 2 --max-time 5 http://127.0.0.1:{server_port}/api/autostop/{mode} >/dev/null 2>&1; then\n"
                + "    exit 0\n"
                + "fi\n"
                + f"nohup \"{python_exe}\" \"$ROOT_DIR/app/app.py\" --stop-{mode} >/dev/null 2>&1 &\n"
                + "exit 0\n"
            ))

        write_sh("status.sh", (
            sh_header
            + f"if ! curl -s -o /dev/null -f --connect-timeout 1 http://127.0.0.1:{server_port}/api/ping >/dev/null 2>&1; then\n"
            + "    rm -f \"$RUN_DIR/podmedia.running\" \"$RUN_DIR/video.running\" \"$RUN_DIR/downloads.running\" \"$RUN_DIR/music.running\"\n"
            + "    echo 1\n"
            + "    exit 1\n"
            + "fi\n"
            + "if [ -z \"$1\" ]; then\n"
            + "    echo 0\n"
            + "    exit 0\n"
            + "fi\n"
            + "if [ -f \"$RUN_DIR/$1.running\" ]; then\n"
            + "    echo 0\n"
            + "    exit 0\n"
            + "else\n"
            + "    echo 1\n"
            + "    exit 1\n"
            + "fi\n"
        ))

        write_sh("stop.sh", (
            sh_header
            + "# PodMedia Stop Script\n"
            + "rm -f \"$RUN_DIR/podmedia.running\" \"$RUN_DIR/video.running\" \"$RUN_DIR/downloads.running\" \"$RUN_DIR/music.running\"\n\n"
            + f"if curl -s -o /dev/null -f --connect-timeout 2 --max-time 5 http://127.0.0.1:{server_port}/api/system/quit >/dev/null 2>&1; then\n"
            + "    echo \"[OK] PodMedia shutdown initiated.\"\n"
            + "    exit 0\n"
            + "fi\n\n"
            + "echo \"[1/3] Stopping PodMedia containers...\"\n"
            + "podman ps -q --filter \"name=media_center_\" | while read -r c; do\n"
            + "    [ -n \"$c\" ] && podman stop \"$c\" >/dev/null 2>&1\n"
            + "done\n"
            + "echo \"[2/3] Releasing external drive mounts (if any)...\"\n"
            + f"\"{python_exe}\" -c \"import sys; sys.path.append('$ROOT_DIR/app'); from disk_manager import disk_manager; disk_manager.release_all_external_drives()\" >/dev/null 2>&1 || true\n"
            + "echo \"[3/3] Stopping PodMedia background server...\"\n"
            + "pkill -f \"app/app.py\" >/dev/null 2>&1 || true\n"
            + "echo \"[OK] PodMedia stopped.\"\n"
            + "exit 0\n"
        ))

        launch_msg = (
            "Run './scripts/start.sh' to launch PodMedia.\n"
            " Run './scripts/start_video.sh' / './scripts/stop_video.sh' to control Video (Cinema) mode.\n"
            " Run './scripts/start_downloads.sh' / './scripts/stop_downloads.sh' to control Downloads mode.\n"
            " Run './scripts/start_music.sh' / './scripts/stop_music.sh' to control Music (Plexamp) mode.\n"
            " Run './scripts/stop.sh' to stop PodMedia & release external drives."
        )

    print("\n==================================================")
    print(" 🎉 PodMedia Setup Completed Successfully!")
    print(f" {launch_msg}")
    print("==================================================")


if __name__ == "__main__":
    run_setup()
