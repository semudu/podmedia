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
DOWNLOADS_DIR = config.get('paths', 'downloads_dir', fallback='./downloads')

ALL_CONTAINERS = [
    'media_center_plex',
    'media_center_radarr',
    'media_center_sonarr',
    'media_center_prowlarr',
    'media_center_qbittorrent',
    'media_center_flaresolverr',
    'media_center_slskd',
    'media_center_beets'
]

TOOL_MAP = {
    'media_center_radarr': 'radarr',
    'media_center_sonarr': 'sonarr',
    'media_center_prowlarr': 'prowlarr',
    'media_center_qbittorrent': 'qbittorrent',
    'media_center_flaresolverr': 'flaresolverr',
    'media_center_slskd': 'slskd',
    'media_center_beets': 'beets'
}

def subproc_kwargs():
    """Returns platform-specific subprocess arguments to prevent terminal window flashing on Windows."""
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs

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

    yield "\n[✓] ACTION COMPLETED SUCCESSFULLY!\n"

def stop_all_containers_sync():
    """Synchronously stops all running PodMedia containers (used on application exit)."""
    try:
        res = subprocess.run(['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'], capture_output=True, text=True, **subproc_kwargs())
        if res.returncode == 0:
            containers = [c.strip() for c in res.stdout.strip().splitlines() if c.strip()]
            for c in containers:
                subprocess.run(['podman', 'stop', c], capture_output=True, text=True, **subproc_kwargs())
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
    yield "\n[✓] ALL SERVICES STOPPED!\n"

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

    complete_dir = os.path.abspath(os.path.join(project_root, DOWNLOADS_DIR, "music", "complete")) if not os.path.isabs(DOWNLOADS_DIR) else os.path.join(DOWNLOADS_DIR, "music", "complete")
    incomplete_dir = os.path.abspath(os.path.join(project_root, DOWNLOADS_DIR, "music", "incomplete")) if not os.path.isabs(DOWNLOADS_DIR) else os.path.join(DOWNLOADS_DIR, "music", "incomplete")
    cleaned_c = cleanup_empty_dirs(complete_dir)
    cleaned_i = cleanup_empty_dirs(incomplete_dir)
    total_cleaned = cleaned_c + cleaned_i
    if total_cleaned > 0:
        yield f"[✓] Cleaned up {total_cleaned} empty leftover user folder(s).\n"

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
        'beets': ('tools', 'beets')
    }

    if app_name in app_map:
        folder, service = app_map[app_name]
        yield f"[+] Starting '{app_name.upper()}' service...\n"
        work_dir = os.path.join(project_root, 'services', folder)
        for log in run_command_generator(f'podman-compose up -d {service}', cwd=work_dir):
            yield log
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
    yield f"\n[✓] '{app_name.upper()}' STOPPED SUCCESSFULLY!\n"
