import os
import sys
import subprocess
import shutil

BANNER = """
==================================================
  🍿 PodMedia Setup & Initialization
==================================================
"""

def check_command(cmd):
    """Checks if a CLI command is available on system PATH."""
    return shutil.which(cmd) is not None

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

    # 2. Directory Creation
    print("\n[2/5] Creating Directory Structure...")
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

    # 3. Config Initialization
    print("\n[3/5] Initializing Configuration Files...")
    config_file = os.path.join(base_dir, "config.properties")
    example_config = os.path.join(base_dir, "config.properties.example")

    if not os.path.exists(config_file) and os.path.exists(example_config):
        shutil.copyfile(example_config, config_file)
        print(" [✓] Created 'config.properties' from example template.")
    else:
        print(" [✓] 'config.properties' exists.")

    # 4. Slskd Default Config Setup
    slskd_config = os.path.join(base_dir, "services", "tools", "slskd", "config", "slskd.yml")
    if not os.path.exists(slskd_config):
        slskd_content = """web:
  port: 5030
  authentication:
    enabled: false

soulseek:
  username: "slsk_user"
  password: "slsk_password"
  listen_port: 50300

shares:
  directories:
    - /music

directories:
  incomplete: /downloads/incomplete
  downloads: /downloads/music
"""
        with open(slskd_config, "w", encoding="utf-8") as f:
            f.write(slskd_content)
        print(" [✓] Created default 'slskd.yml' configuration.")

    # 5. Podman Machine Health
    print("\n[4/5] Checking Podman VM Health...")
    res = subprocess.run(["podman", "info"], capture_output=True, text=True)
    if res.returncode != 0:
        print(" [!] Podman machine is offline. Attempting start...")
        subprocess.run(["podman", "machine", "start"])
    else:
        print(" [✓] Podman VM is active and running.")

    print("\n[5/5] Setup Finished!")
    print("==================================================")
    print(" 🎉 PodMedia is ready!")
    print(" Run 'python app.py' to launch the web dashboard.")
    print("==================================================")

if __name__ == "__main__":
    run_setup()
