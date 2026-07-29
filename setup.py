import os
import sys
import subprocess
import shutil
import configparser

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

    # 3. Dynamic Directory Creation Based on config.properties
    print("\n[3/5] Creating Dynamic Directory & Pipeline Structure...")
    print(f" [i] Active Media Directory    : {media_dir}")
    print(f" [i] Active Downloads Directory: {downloads_dir}")

    directories = [
        os.path.join(downloads_dir, "incomplete"),
        os.path.join(downloads_dir, "music"),
        os.path.join(media_dir, "movies"),
        os.path.join(media_dir, "tv"),
        os.path.join(media_dir, "music"),
        os.path.join(base_dir, "services", "tools", "slskd", "config"),
        os.path.join(base_dir, "services", "tools", "beets", "config"),
        os.path.join(base_dir, "services", "tools", "qbittorrent", "config", "qBittorrent"),
    ]

    for d in directories:
        os.makedirs(d, exist_ok=True)
        print(f" [✓] Verified directory: {d}")

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
    print(" 🎉 PodMedia Dynamic Setup is Complete!")
    print(" Run 'python app.py' to launch the System Tray App.")
    print("==================================================")

if __name__ == "__main__":
    run_setup()
