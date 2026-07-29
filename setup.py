import os
import sys
import subprocess
import shutil

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

    # 1. Dependency Checks
    print("[1/5] Checking Dependencies...")
    if not check_command("podman"):
        print(" [X] ERROR: 'podman' CLI is not installed or not in PATH.")
        print("     Please install Podman from https://podman.io")
        sys.exit(1)
    else:
        print(" [✓] Podman CLI detected.")

    if not check_command("podman-compose"):
        print(" [!] WARNING: 'podman-compose' CLI not found on PATH.")
        print("     You can install it via: pip install podman-compose")
    else:
        print(" [✓] podman-compose detected.")

    # 2. Directory Creation (Downloads -> Beets -> Media Pipeline)
    print("\n[2/5] Creating Directory & Pipeline Structure...")
    directories = [
        os.path.join(base_dir, "downloads", "incomplete"),
        os.path.join(base_dir, "downloads", "music"),
        os.path.join(base_dir, "media", "movies"),
        os.path.join(base_dir, "media", "tv"),
        os.path.join(base_dir, "media", "music"),
        os.path.join(base_dir, "services", "tools", "slskd", "config"),
        os.path.join(base_dir, "services", "tools", "beets", "config"),
        os.path.join(base_dir, "services", "tools", "qbittorrent", "config", "qBittorrent"),
    ]

    for d in directories:
        os.makedirs(d, exist_ok=True)
        print(f" [✓] Verified directory: {os.path.relpath(d, base_dir)}")

    # 3. Personalized Configuration Initialization
    print("\n[3/5] Initializing Personalized Service Configurations...")

    # Root config.properties
    copy_template_if_missing(
        os.path.join(base_dir, "config.properties.example"),
        os.path.join(base_dir, "config.properties"),
        "Root Server Properties (config.properties)"
    )

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

    # 4. Podman Machine Health
    print("\n[4/5] Checking Podman VM Health...")
    res = subprocess.run(["podman", "info"], capture_output=True, text=True)
    if res.returncode != 0:
        print(" [!] Podman machine is offline. Attempting start...")
        subprocess.run(["podman", "machine", "start"])
    else:
        print(" [✓] Podman VM is active and running.")

    print("\n[5/5] Setup Finished!")
    print("==================================================")
    print(" 🎉 PodMedia Personalized Setup is Complete!")
    print(" Run 'python app.py' to launch the web dashboard.")
    print("==================================================")

if __name__ == "__main__":
    run_setup()
