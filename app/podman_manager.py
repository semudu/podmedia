import os
import subprocess
import configparser

# Project Root directory (parent of app/)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load properties configuration
config = configparser.ConfigParser()
config_file = os.path.join(project_root, 'config.properties')
if os.path.exists(config_file):
    config.read(config_file)

PORT = config.getint('server', 'port', fallback=3000)
NETWORK_NAME = config.get('podman', 'network', fallback='media_center_net')
MEDIA_DIR = config.get('paths', 'media_dir', fallback='./media')
MEDIA_STUB_DIR = './media_external_stub'
DOWNLOADS_DIR = config.get('paths', 'downloads_dir', fallback='./downloads')

ALL_CONTAINERS = [
    'media_center_plex',
    'media_center_radarr',
    'media_center_sonarr',
    'media_center_prowlarr',
    'media_center_qbittorrent',
    'media_center_flaresolverr',
    'media_center_slskd',
    'media_center_beets',
    'media_center_metube'
]

TOOL_MAP = {
    'media_center_radarr': 'radarr',
    'media_center_sonarr': 'sonarr',
    'media_center_prowlarr': 'prowlarr',
    'media_center_qbittorrent': 'qbittorrent',
    'media_center_flaresolverr': 'flaresolverr',
    'media_center_slskd': 'slskd',
    'media_center_beets': 'beets',
    'media_center_metube': 'metube'
}

def subproc_kwargs():
    """Returns platform-specific subprocess arguments to prevent terminal window flashing on Windows."""
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs

from disk_manager import disk_manager

def get_external_media_dir():
    """Returns the dynamic media directory path of the active external disk, or local stub fallback."""
    active = disk_manager.get_active_disk()
    if active and active.get("connected") and active.get("media_path"):
        return active["media_path"]
    return MEDIA_STUB_DIR

def get_external_disk_info():
    """Returns dynamic status and metrics for the currently attached target storage drive."""
    active = disk_manager.get_active_disk()
    
    info = {
        "connected": bool(active and active.get("connected")),
        "drive_letter": active.get("letter", "") if active else "",
        "label": active.get("label", "Harici Disk") if active else "Harici Disk",
        "serial": active.get("serial", "") if active else "",
        "path": active.get("media_path", "") if active else "",
        "free_gb": active.get("free_gb", 0.0) if active else 0.0,
        "total_gb": active.get("total_gb", 0.0) if active else 0.0,
        "pending_movies_gb": 0.0,
        "pending_series_gb": 0.0,
        "total_pending_gb": 0.0
    }

    # Calculate pending files in local D:\media_center\media\movies and series
    local_media = os.path.abspath(os.path.join(project_root, MEDIA_DIR)) if not os.path.isabs(MEDIA_DIR) else MEDIA_DIR
    local_movies = os.path.join(local_media, 'movies')
    local_series = os.path.join(local_media, 'series')

    def _calc_size(dir_path):
        if not os.path.exists(dir_path):
            return 0.0
        total = 0
        for root, dirs, files in os.walk(dir_path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        return round(total / (1024**3), 2)

    info["pending_movies_gb"] = _calc_size(local_movies)
    info["pending_series_gb"] = _calc_size(local_series)
    info["total_pending_gb"] = round(info["pending_movies_gb"] + info["pending_series_gb"], 2)
    return info

def resolve_compose_path(path_str, cwd):
    """Converts media/downloads path into a clean POSIX path relative to cwd for podman-compose."""
    target_dir = cwd if cwd else project_root
    abs_path = os.path.abspath(os.path.join(project_root, path_str)) if not os.path.isabs(path_str) else path_str
    try:
        rel_path = os.path.relpath(abs_path, target_dir)
        return rel_path.replace('\\', '/')
    except Exception:
        return abs_path.replace('\\', '/')

def run_command_generator(cmd, cwd=None):
    """Executes a command and yields stdout/stderr line by line in real-time."""
    yield f"$ {cmd}\n"
    try:
        env = os.environ.copy()
        env['MEDIA_DIR'] = resolve_compose_path(MEDIA_DIR, cwd)
        env['DOWNLOADS_DIR'] = resolve_compose_path(DOWNLOADS_DIR, cwd)

        # Dynamic external drive resolution: find registered drive regardless of current drive letter (E:, F:, Z:)
        # Fallback to local stub if unmounted so containers start cleanly without error!
        active_disk = disk_manager.get_active_disk()
        if active_disk and active_disk["connected"] and os.path.exists(active_disk["media_path"]):
            env['EXTERNAL_MEDIA_DIR'] = resolve_compose_path(active_disk["media_path"], cwd)
            env['EXTERNAL_MOVIES_DIR'] = resolve_compose_path(active_disk["movies_path"], cwd)
            env['EXTERNAL_SERIES_DIR'] = resolve_compose_path(active_disk["series_path"], cwd)
        else:
            stub_abs = os.path.abspath(os.path.join(project_root, MEDIA_STUB_DIR))
            os.makedirs(os.path.join(stub_abs, 'movies'), exist_ok=True)
            os.makedirs(os.path.join(stub_abs, 'series'), exist_ok=True)
            env['EXTERNAL_MEDIA_DIR'] = resolve_compose_path(MEDIA_STUB_DIR, cwd)
            env['EXTERNAL_MOVIES_DIR'] = resolve_compose_path(os.path.join(MEDIA_STUB_DIR, 'movies'), cwd)
            env['EXTERNAL_SERIES_DIR'] = resolve_compose_path(os.path.join(MEDIA_STUB_DIR, 'series'), cwd)

        process = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **subproc_kwargs()
        )
        for line in iter(process.stdout.readline, ''):
            yield line
        process.stdout.close()
        process.wait()
    except Exception as e:
        yield f"Error: {str(e)}\n"

def ensure_podman_machine():
    """Returns True if podman machine is running, else attempts to start it."""
    res = subprocess.run(['podman', 'info'], capture_output=True, text=True, **subproc_kwargs())
    if res.returncode != 0:
        yield "[!] Podman machine is offline, starting (podman machine start)...\n"
        for line in run_command_generator('podman machine start'):
            yield line
    else:
        yield "[✓] Podman machine is active.\n"

def ensure_network():
    """Ensures that the dedicated Podman network exists."""
    res = subprocess.run(['podman', 'network', 'ls', '--format', '{{.Name}}'], capture_output=True, text=True, **subproc_kwargs())
    if NETWORK_NAME not in res.stdout.splitlines():
        yield f"[+] Creating network '{NETWORK_NAME}'...\n"
        for line in run_command_generator(f'podman network create {NETWORK_NAME}'):
            yield line

def get_container_states():
    """Queries running containers and computes group states."""
    running_set = set()
    try:
        res = subprocess.run(['podman', 'ps', '--format', '{{.Names}}'], capture_output=True, text=True, timeout=5, **subproc_kwargs())
        if res.returncode == 0:
            for line in res.stdout.strip().splitlines():
                if line.strip():
                    running_set.add(line.strip())
    except Exception as e:
        print("Error reading Podman status:", e)

    states = {c: (c in running_set) for c in ALL_CONTAINERS}
    groups = {
        "plex": states['media_center_plex'],
        "downloads": (states['media_center_radarr'] and states['media_center_sonarr'] and states['media_center_qbittorrent']),
        "music": (states['media_center_slskd'] and states['media_center_plex'])
    }
    return states, groups

def apply_target_state(target_containers):
    """Main engine to start target containers and stop unneeded ones with live logging."""
    for log in ensure_podman_machine():
        yield log
    for log in ensure_network():
        yield log

    services_dir = os.path.join(project_root, 'services')

    # Start Plex if targeted
    if 'media_center_plex' in target_containers:
        yield "[+] Starting Plex Media Server...\n"
        plex_dir = os.path.join(services_dir, 'plex')
        for log in run_command_generator('podman-compose up -d plex', cwd=plex_dir):
            yield log

    # Start Tools if targeted
    tools_to_start = []
    for container_name, service_name in TOOL_MAP.items():
        if container_name in target_containers:
            tools_to_start.append(service_name)

    if tools_to_start:
        tools_str = " ".join(tools_to_start)
        yield f"[+] Starting tools: {tools_str}...\n"
        tools_dir = os.path.join(services_dir, 'tools')
        for log in run_command_generator(f'podman-compose up -d {tools_str}', cwd=tools_dir):
            yield log

    # Stop unneeded containers
    res = subprocess.run(['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'], capture_output=True, text=True, **subproc_kwargs())
    if res.returncode == 0:
        for c in res.stdout.strip().splitlines():
            c = c.strip()
            if c and c not in target_containers:
                yield f"[-] Stopping untargeted container: {c}...\n"
                for log in run_command_generator(f'podman stop {c}'):
                    yield log

    release_external_mounts()
    yield "\n[✓] ACTION COMPLETED SUCCESSFULLY!\n"

def release_external_mounts():
    """Safely unmounts external drive mounts in WSL when no media_center containers are running.
    This frees Windows file locks so the external disk can be safely ejected or placed in sleep mode."""
    try:
        res = subprocess.run(['podman', 'ps', '-q', '--filter', 'name=media_center_'], capture_output=True, text=True, **subproc_kwargs())
        if res.returncode == 0 and not res.stdout.strip():
            letters = set()
            active_disk = disk_manager.get_active_disk()
            if active_disk and active_disk.get('letter'):
                letters.add(active_disk['letter'][0].lower())
            for d in disk_manager.list_available_drives():
                if d.get('letter'):
                    letters.add(d['letter'][0].lower())
            letters.update(['e', 'f', 'g'])

            for l in letters:
                subprocess.run(['wsl', '-d', 'podman-machine-default', '-u', 'root', 'umount', f'/mnt/{l}'], capture_output=True, text=True, **subproc_kwargs())

            # Also clear any lingering Windows COM Surrogate / DllHost processes that lock external drives after file transfers
            if os.name == 'nt':
                subprocess.run(
                    ['powershell', '-NoProfile', '-Command', 
                     "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*DFB65C4C-B34F-435D-AFE9-A86218684AA8*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                    capture_output=True, text=True, **subproc_kwargs()
                )
    except Exception:
        pass

def stop_all_containers_sync():
    """Synchronously stops all running PodMedia containers (used on application exit)."""
    try:
        res = subprocess.run(['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'], capture_output=True, text=True, **subproc_kwargs())
        if res.returncode == 0:
            containers = [c.strip() for c in res.stdout.strip().splitlines() if c.strip()]
            for c in containers:
                subprocess.run(['podman', 'stop', c], capture_output=True, text=True, **subproc_kwargs())
        release_external_mounts()
    except Exception as e:
        print("Error during sync container cleanup:", e)

def stop_all_containers():
    """Stops all running PodMedia containers."""
    for log in ensure_podman_machine():
        yield log
    
    yield "[!] Stopping all PodMedia services...\n"
    res = subprocess.run(['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'], capture_output=True, text=True, **subproc_kwargs())
    if res.returncode == 0:
        for c in res.stdout.strip().splitlines():
            c = c.strip()
            if c:
                yield f"[-] Stopping: {c}...\n"
                for log in run_command_generator(f'podman stop {c}'):
                    yield log
    release_external_mounts()
    yield "\n[✓] ALL SERVICES STOPPED (External disk released)!\n"

def cleanup_empty_dirs(target_dir):
    """Recursively removes empty folders in target_dir."""
    if not os.path.exists(target_dir):
        return 0
    removed_count = 0
    for root, dirs, files in os.walk(target_dir, topdown=False):
        for d in dirs:
            dir_path = os.path.join(root, d)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    removed_count += 1
            except Exception:
                pass
    return removed_count

def organize_music():
    """Triggers Beets music auto-tagging process from /downloads/music/complete -> /music."""
    for log in ensure_podman_machine():
        yield log

    states, _ = get_container_states()
    if not states.get('media_center_beets', False):
        yield "[+] Starting Beets service...\n"
        tools_dir = os.path.join(project_root, 'services', 'tools')
        for log in run_command_generator('podman-compose up -d beets', cwd=tools_dir):
            yield log

    yield "[+] Triggering Beets music tagging automation (/downloads/music/complete -> /music)...\n"
    for log in run_command_generator('podman exec media_center_beets beet import -m -q /downloads/music/complete'):
        yield log
    # Singleton pass for individual songs
    for log in run_command_generator('podman exec media_center_beets beet import -m -s -q /downloads/music/complete'):
        yield log
    # Fallback pass for non-standard folder names or unindexed releases
    for log in run_command_generator('podman exec media_center_beets beet import -m -A -q /downloads/music/complete'):
        yield log

    complete_dir = os.path.abspath(os.path.join(project_root, DOWNLOADS_DIR, "music", "complete")) if not os.path.isabs(DOWNLOADS_DIR) else os.path.join(DOWNLOADS_DIR, "music", "complete")
    incomplete_dir = os.path.abspath(os.path.join(project_root, DOWNLOADS_DIR, "music", "incomplete")) if not os.path.isabs(DOWNLOADS_DIR) else os.path.join(DOWNLOADS_DIR, "music", "incomplete")
    cleaned_c = cleanup_empty_dirs(complete_dir)
    cleaned_i = cleanup_empty_dirs(incomplete_dir)
    total_cleaned = cleaned_c + cleaned_i
    if total_cleaned > 0:
        yield f"[✓] Cleaned up {total_cleaned} empty leftover user folder(s).\n"

    # Trigger Plex library refresh if Plex is running
    try:
        req = urllib.request.Request("http://localhost:32400/library/sections/all/refresh")
        urllib.request.urlopen(req, timeout=2)
        yield "[✓] Triggered automatic Plex Media Library scan.\n"
    except Exception:
        pass

    yield "\n[✓] MUSIC TAGGING COMPLETED!\n"

def start_single_app(app_name):
    """Selectively starts a single application container."""
    for log in ensure_podman_machine():
        yield log
    for log in ensure_network():
        yield log

    app_map = {
        'plex': ('plex', 'plex'),
        'radarr': ('tools', 'radarr'),
        'sonarr': ('tools', 'sonarr'),
        'prowlarr': ('tools', 'prowlarr'),
        'qbittorrent': ('tools', 'qbittorrent'),
        'flaresolverr': ('tools', 'flaresolverr'),
        'slskd': ('tools', 'slskd'),
        'beets': ('tools', 'beets'),
        'metube': ('tools', 'metube')
    }

    if app_name in app_map:
        folder, service = app_map[app_name]
        yield f"[+] Starting '{app_name.upper()}' service...\n"
        work_dir = os.path.join(project_root, 'services', folder)
        for log in run_command_generator(f'podman-compose up -d {service}', cwd=work_dir):
            yield log
        if app_name == 'plex':
            try:
                import media_watcher
                media_watcher.trigger_plex_full_scan_on_startup()
            except Exception:
                pass
        yield f"\n[✓] '{app_name.upper()}' STARTED SUCCESSFULLY!\n"
    else:
        yield f"[X] Unknown service: {app_name}\n"

def stop_single_app(app_name):
    """Selectively stops a single application container."""
    for log in ensure_podman_machine():
        yield log

    container_name = f"media_center_{app_name}"
    yield f"[-] Stopping '{app_name.upper()}' service ({container_name})...\n"
    for log in run_command_generator(f'podman stop {container_name}'):
        yield log
    release_external_mounts()
    yield f"\n[✓] '{app_name.upper()}' STOPPED SUCCESSFULLY!\n"
