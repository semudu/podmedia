import os
import subprocess
import configparser
import json
import time

# Project Root directory (parent of app/)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Runtime state & PID directory
RUN_DIR = os.path.join(project_root, 'run')
STATE_FILE = os.path.join(RUN_DIR, 'podmedia.running')
VIDEO_STATE_FILE = os.path.join(RUN_DIR, 'video.running')
DOWNLOADS_STATE_FILE = os.path.join(RUN_DIR, 'downloads.running')
MUSIC_STATE_FILE = os.path.join(RUN_DIR, 'music.running')

VALID_MODES = ('video', 'downloads', 'music')
MODE_STATE_FILES = {
    'video': VIDEO_STATE_FILE,
    'downloads': DOWNLOADS_STATE_FILE,
    'music': MUSIC_STATE_FILE,
}

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
    'media_center_plextraktsync',
    'media_center_radarr',
    'media_center_sonarr',
    'media_center_prowlarr',
    'media_center_qbittorrent',
    'media_center_flaresolverr',
    'media_center_aurral',
    'media_center_slskd'
]

TOOL_MAP = {
    'media_center_plextraktsync': 'plextraktsync',
    'media_center_radarr': 'radarr',
    'media_center_sonarr': 'sonarr',
    'media_center_prowlarr': 'prowlarr',
    'media_center_qbittorrent': 'qbittorrent',
    'media_center_flaresolverr': 'flaresolverr',
    'media_center_aurral': 'aurral',
    'media_center_slskd': 'slskd'
}

EXTERNAL_DISK_CONTAINERS = {
    'media_center_plex',
    'media_center_radarr',
    'media_center_sonarr',
}


def normalize_mode(mode):
    """Normalizes mode names, mapping legacy 'plex' alias to 'video'."""
    m = str(mode or '').strip().lower()
    if m == 'plex':
        return 'video'
    return m


def get_disk_wake_policy():
    """Returns 'media_only' (default), 'manual', or 'always' from config.properties."""
    try:
        cfg = configparser.ConfigParser()
        if os.path.exists(config_file):
            cfg.read(config_file, encoding='utf-8')
            val = cfg.get('automation', 'disk_wake_policy', fallback='media_only').strip().lower()
            if val in ('media_only', 'manual', 'always'):
                return val
    except Exception:
        pass
    return 'media_only'


def get_music_start_slskd():
    """Returns True if Music Mode should also auto-start Slskd (default True)."""
    try:
        cfg = configparser.ConfigParser()
        if os.path.exists(config_file):
            cfg.read(config_file, encoding='utf-8')
            return cfg.getboolean('automation', 'music_start_slskd', fallback=True)
    except Exception:
        pass
    return True


def is_plextraktsync_configured():
    """Returns True if PlexTraktSync has completed initial Plex & Trakt login."""
    cfg_dir = os.path.join(project_root, 'services', 'tools', 'plextraktsync', 'config')
    return (
        os.path.exists(os.path.join(cfg_dir, '.pytrakt.json'))
        and os.path.exists(os.path.join(cfg_dir, 'servers.yml'))
    )


def subproc_kwargs():
    """Returns platform-specific subprocess arguments to prevent terminal window flashing on Windows."""
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


from disk_manager import disk_manager


def ensure_run_dir():
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
    except Exception:
        pass


def write_state_file():
    """Creates run/podmedia.running state file."""
    try:
        ensure_run_dir()
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({"status": "running", "pid": os.getpid(), "port": PORT}, f)
    except Exception:
        pass


def remove_state_file():
    """Removes run/podmedia.running state file."""
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except Exception:
        pass


def write_mode_state_file(mode):
    """Creates run/<mode>.running state file representing user mode intent."""
    m = normalize_mode(mode)
    fpath = MODE_STATE_FILES.get(m)
    if not fpath:
        return
    try:
        ensure_run_dir()
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump({"status": "running", "mode": m}, f)
    except Exception:
        pass


def remove_mode_state_file(mode):
    """Removes run/<mode>.running state file."""
    m = normalize_mode(mode)
    fpath = MODE_STATE_FILES.get(m)
    if not fpath:
        return
    try:
        if os.path.exists(fpath):
            os.remove(fpath)
    except Exception:
        pass


def is_mode_state_active(mode):
    """Returns True if run/<mode>.running currently exists."""
    m = normalize_mode(mode)
    fpath = MODE_STATE_FILES.get(m)
    return bool(fpath and os.path.exists(fpath))


def any_mode_state_file_exists():
    """Returns True if any mode state file exists under run/."""
    return any(os.path.exists(p) for p in MODE_STATE_FILES.values())


def get_mode_containers(mode):
    """Returns the set of containers required by an activity mode."""
    m = normalize_mode(mode)
    if m == 'video':
        c = {'media_center_plex'}
        if is_plextraktsync_configured():
            c.add('media_center_plextraktsync')
        return c
    if m == 'downloads':
        return {
            'media_center_radarr',
            'media_center_sonarr',
            'media_center_prowlarr',
            'media_center_qbittorrent',
            'media_center_flaresolverr',
        }
    if m == 'music':
        c = {'media_center_aurral', 'media_center_plex'}
        if get_music_start_slskd():
            c.add('media_center_slskd')
        return c
    return set()


def mode_needs_external_disk(mode):
    """Returns True if the given activity mode requires the external archive HDD to be awake."""
    return normalize_mode(mode) in ('video', 'downloads')


def compute_target_for_modes(active_modes):
    """Computes the union of required containers and whether the external HDD is needed."""
    containers = set()
    needs_disk = False
    for m in active_modes:
        nm = normalize_mode(m)
        if nm in VALID_MODES:
            containers |= get_mode_containers(nm)
            if mode_needs_external_disk(nm):
                needs_disk = True
    return containers, needs_disk


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


def run_command_generator(cmd, cwd=None, force_stub=False):
    """Executes a command and yields stdout/stderr line by line in real-time."""
    yield f"$ {cmd}\n"
    try:
        env = os.environ.copy()
        env['MEDIA_DIR'] = resolve_compose_path(MEDIA_DIR, cwd)
        env['DOWNLOADS_DIR'] = resolve_compose_path(DOWNLOADS_DIR, cwd)

        # Dynamic external drive resolution: when force_stub=True (e.g. Music-only Plexamp mode),
        # always bind media_external_stub so the external HDD can stay asleep at 0 RPM.
        active_disk = None if force_stub else disk_manager.get_active_disk()
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


def is_podman_machine_running():
    """Checks if the podman machine VM is currently running."""
    try:
        list_res = subprocess.run(['podman', 'machine', 'list', '--format', 'json'], capture_output=True, text=True, **subproc_kwargs())
        if list_res.returncode == 0 and list_res.stdout.strip():
            machines = json.loads(list_res.stdout)
            return any(m.get('Running') for m in machines)
    except Exception:
        pass
    return False


def ensure_podman_machine():
    """Ensures that the Podman machine is running and responsive."""
    for attempt in range(3):
        res = subprocess.run(['podman', 'info'], capture_output=True, text=True, **subproc_kwargs())
        if res.returncode == 0:
            yield "[✓] Podman machine is active.\n"
            return
        time.sleep(1)

    if is_podman_machine_running():
        for _ in range(5):
            time.sleep(1)
            check = subprocess.run(['podman', 'info'], capture_output=True, text=True, **subproc_kwargs())
            if check.returncode == 0:
                yield "[✓] Podman socket is ready.\n"
                return
        yield "[✓] Podman machine is running.\n"
        return

    if os.name == 'nt':
        subprocess.run(['wsl', '--terminate', 'podman-machine-default'], capture_output=True, **subproc_kwargs())

    yield "[!] Podman machine is offline, starting (podman machine start)...\n"
    for line in run_command_generator('podman machine start'):
        if "already running" not in line.lower():
            yield line

    for _ in range(8):
        time.sleep(1)
        check = subprocess.run(['podman', 'info'], capture_output=True, text=True, **subproc_kwargs())
        if check.returncode == 0:
            yield "[✓] Podman machine started and ready.\n"
            return


def ensure_network():
    """Ensures that the dedicated Podman network exists."""
    res = subprocess.run(['podman', 'network', 'ls', '--format', '{{.Name}}'], capture_output=True, text=True, **subproc_kwargs())
    if NETWORK_NAME not in res.stdout.splitlines():
        yield f"[+] Creating network '{NETWORK_NAME}'...\n"
        for line in run_command_generator(f'podman network create {NETWORK_NAME}'):
            yield line


def get_container_mount_mode(cname):
    """Returns 'stub' if container binds media_external_stub, 'external' if it binds an external drive, or None."""
    try:
        insp = subprocess.run(
            ['podman', 'inspect', '--format', '{{range .Mounts}}{{.Source}} {{end}}', cname],
            capture_output=True, text=True, timeout=5, **subproc_kwargs()
        )
        if insp.returncode == 0 and insp.stdout.strip():
            mounts_str = insp.stdout.strip()
            if 'media_external_stub' in mounts_str:
                return 'stub'
            return 'external'
    except Exception:
        pass
    return None


def get_containers_using_external_disk(running_names=None):
    """Returns list of currently running containers that actually hold an external-disk mount (excluding stub mounts)."""
    if running_names is None:
        running_names = set()
        try:
            res = subprocess.run(['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'], capture_output=True, text=True, timeout=5, **subproc_kwargs())
            if res.returncode == 0:
                running_names = {c.strip() for c in res.stdout.strip().splitlines() if c.strip()}
        except Exception:
            pass
    else:
        running_names = set(running_names)

    using_ext = []
    for cname in (running_names & EXTERNAL_DISK_CONTAINERS):
        if get_container_mount_mode(cname) != 'stub':
            using_ext.append(cname)
    return using_ext


def get_plex_disk_mode(plex_running=None):
    """Returns 'external', 'stub', or 'off' describing how Plex is currently mounted."""
    if plex_running is None:
        states, _ = get_container_states()
        plex_running = states.get('media_center_plex', False)
    if not plex_running:
        return 'off'
    return get_container_mount_mode('media_center_plex') or 'external'


def get_container_states():
    """Queries running containers and computes activity mode states (intent-aware for shared Plex)."""
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
    music_on = states['media_center_aurral'] or states['media_center_slskd']
    downloads_on = (
        states['media_center_radarr']
        and states['media_center_sonarr']
        and states['media_center_qbittorrent']
    )

    # Plex is shared between Video (Cinema) and Music (Plexamp).
    # If Music mode is active and video.running does not exist, Plex is running solely for Music mode.
    if not states['media_center_plex']:
        video_on = False
    elif is_mode_state_active('video'):
        video_on = True
    elif music_on or is_mode_state_active('music'):
        video_on = False
    else:
        video_on = True

    groups = {
        "video": video_on,
        "downloads": downloads_on,
        "music": music_on,
    }
    return states, groups


def ensure_external_mounts(use_external_disk=True, target_containers=None):
    """Ensures external drive mounts match requested mode (real external disk vs disk-free stub).
    Removes and forces recreation of target containers whose existing mount binding differs."""
    try:
        policy = get_disk_wake_policy()
        stub_abs = os.path.abspath(os.path.join(project_root, MEDIA_STUB_DIR))
        os.makedirs(os.path.join(stub_abs, 'movies'), exist_ok=True)
        os.makedirs(os.path.join(stub_abs, 'series'), exist_ok=True)

        if use_external_disk:
            if os.name == 'nt' and policy != 'manual':
                disk_manager.wake_all_external_drives()

            active = disk_manager.get_active_disk()
            if active and active.get("connected") and active.get("letter"):
                letter = active["letter"][0].lower()
                drive_root = f"{letter.upper()}:\\"
                if os.name == 'nt':
                    try:
                        os.listdir(drive_root)
                    except Exception:
                        pass
                    check_cmd = f"stat /mnt/{letter}/media >/dev/null 2>&1"
                    chk = subprocess.run(['podman', 'machine', 'ssh', check_cmd], capture_output=True, text=True, **subproc_kwargs())
                    if chk.returncode != 0:
                        yield f"[+] Mounting external disk ({letter.upper()}:) into Podman VM...\n"
                        mount_cmd = f"sudo umount -l /mnt/{letter} 2>/dev/null; sudo mkdir -p /mnt/{letter} && sudo mount -t drvfs '{letter.upper()}:\\' /mnt/{letter}"
                        subprocess.run(['podman', 'machine', 'ssh', mount_cmd], capture_output=True, text=True, **subproc_kwargs())
                expected_mount_token = f"/mnt/{letter}/media"
            else:
                yield "[i] No external disk attached — running in local storage mode.\n"
                expected_mount_token = "media_external_stub"
        else:
            yield "[i] Using disk-free stub mount (external HDD remains asleep for Music/Plexamp).\n"
            expected_mount_token = "media_external_stub"

        check_list = (set(target_containers) & EXTERNAL_DISK_CONTAINERS) if target_containers is not None else EXTERNAL_DISK_CONTAINERS
        for cname in check_list:
            insp = subprocess.run(
                ['podman', 'inspect', '--format', '{{range .Mounts}}{{.Source}} {{end}}', cname],
                capture_output=True, text=True, **subproc_kwargs()
            )
            if insp.returncode == 0 and insp.stdout.strip():
                mounts_str = insp.stdout.strip()
                if expected_mount_token not in mounts_str:
                    yield f"[i] Updating storage mount binding for {cname}...\n"
                    subprocess.run(['podman', 'rm', '-f', cname], capture_output=True, text=True, **subproc_kwargs())
    except Exception as e:
        print("Error ensuring external mounts:", e)


def reconcile_modes(active_modes):
    """Declarative union orchestrator: reconciles running containers & external disk state
    to exactly match the requested set of active activity modes (video / downloads / music)."""
    normalized = {normalize_mode(m) for m in (active_modes or set()) if normalize_mode(m) in VALID_MODES}
    target_containers, needs_disk = compute_target_for_modes(normalized)
    policy = get_disk_wake_policy()
    use_external_disk = needs_disk or (policy == 'always' and bool(target_containers))

    for m in VALID_MODES:
        if m in normalized:
            write_mode_state_file(m)
        else:
            remove_mode_state_file(m)

    if not target_containers:
        for log in stop_all_containers(power_down=None):
            yield log
        return

    for log in ensure_podman_machine():
        yield log

    # Stop untargeted containers FIRST so removed disk-dependent containers release mounts immediately
    res = subprocess.run(['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'], capture_output=True, text=True, **subproc_kwargs())
    if res.returncode == 0:
        for c in res.stdout.strip().splitlines():
            c = c.strip()
            if c and c not in target_containers:
                yield f"[-] Stopping: {c}...\n"
                for log in run_command_generator(f'podman stop {c}'):
                    yield log

    # Ensure external disk / stub mounts match the active modes (recreates Plex if swapping stub <-> external)
    if target_containers & EXTERNAL_DISK_CONTAINERS:
        for log in ensure_external_mounts(use_external_disk=use_external_disk, target_containers=target_containers):
            yield log

    for log in ensure_network():
        yield log

    running_now, _ = get_container_states()
    services_dir = os.path.join(project_root, 'services')
    force_stub = not use_external_disk

    if 'media_center_plex' in target_containers and not running_now.get('media_center_plex', False):
        mode_note = "disk-free stub mode for Plexamp" if force_stub else "external archive mode"
        yield f"[+] Starting Plex Media Server ({mode_note})...\n"
        plex_dir = os.path.join(services_dir, 'plex')
        for log in run_command_generator('podman-compose up -d plex', cwd=plex_dir, force_stub=force_stub):
            yield log
        if not force_stub:
            try:
                import media_watcher
                media_watcher.trigger_plex_full_scan_on_startup()
            except Exception:
                pass

    tools_to_start = [
        svc for cname, svc in TOOL_MAP.items()
        if cname in target_containers and not running_now.get(cname, False)
    ]
    if tools_to_start:
        tools_str = " ".join(tools_to_start)
        yield f"[+] Starting tools: {tools_str}...\n"
        tools_dir = os.path.join(services_dir, 'tools')
        for log in run_command_generator(f'podman-compose up -d {tools_str}', cwd=tools_dir, force_stub=force_stub):
            yield log
        if 'radarr' in tools_to_start or 'sonarr' in tools_to_start:
            try:
                import media_watcher
                if 'radarr' in tools_to_start and 'sonarr' in tools_to_start:
                    media_watcher.trigger_import_list_sync_on_startup('both')
                elif 'radarr' in tools_to_start:
                    media_watcher.trigger_import_list_sync_on_startup('radarr')
                else:
                    media_watcher.trigger_import_list_sync_on_startup('sonarr')
            except Exception:
                pass

    for log in release_external_mounts_with_logs():
        yield log
    yield "\n[✓] ACTION COMPLETED SUCCESSFULLY!\n"


def apply_target_state(target_containers):
    """Legacy wrapper retained for Start All; activates all matching modes."""
    target_set = set(target_containers or [])
    if target_set >= set(ALL_CONTAINERS) - {'media_center_plextraktsync'}:
        for log in reconcile_modes(set(VALID_MODES)):
            yield log
        return

    active_modes = set()
    if 'media_center_plex' in target_set:
        active_modes.add('video')
    if 'media_center_radarr' in target_set and 'media_center_sonarr' in target_set and 'media_center_qbittorrent' in target_set:
        active_modes.add('downloads')
    if 'media_center_aurral' in target_set or 'media_center_slskd' in target_set:
        active_modes.add('music')
    for log in reconcile_modes(active_modes):
        yield log


def release_external_mounts_with_logs(power_down=None):
    """Generator that unmounts external drive mounts in WSL/Podman VM, flushes Windows volume buffers,
    and puts the drive offline & spins down motor (0 RPM) whenever no container actually mounts the external drive."""
    try:
        if power_down is None:
            power_down = (get_disk_wake_policy() in ('media_only', 'manual'))
        machine_running = is_podman_machine_running()
        if machine_running:
            using_ext = get_containers_using_external_disk()
            if using_ext:
                # One or more running containers actively hold an external-disk mount; do not unmount
                return

        letters = set()
        active_disk = disk_manager.get_active_disk()
        if active_disk and active_disk.get('letter'):
            letters.add(active_disk['letter'][0].lower())
        for d in disk_manager.list_available_drives():
            if d.get('letter'):
                letters.add(d['letter'][0].lower())
        letters.update(['e', 'f', 'g'])

        drive_label = f"{active_disk.get('label', 'External Disk')} ({active_disk.get('letter')})" if active_disk and active_disk.get('letter') else "External Disk"

        if machine_running:
            yield f"[-] Unmounting {drive_label} from Podman VM & WSL (/mnt/...)...\n"
            unmount_cmds = " ; ".join([f"sudo umount -l /mnt/{l} 2>/dev/null" for l in letters])
            subprocess.run(['podman', 'machine', 'ssh', unmount_cmds], capture_output=True, text=True, **subproc_kwargs())
            for l in letters:
                subprocess.run(['wsl', '-d', 'podman-machine-default', '-u', 'root', 'umount', '-l', f'/mnt/{l}'], capture_output=True, text=True, **subproc_kwargs())

        if power_down:
            yield f"[-] Putting {drive_label} into Offline & Standby (Motor Spin-Down) mode...\n"
        else:
            yield f"[-] Flushing volume buffers & releasing Windows locks for {drive_label}...\n"
        any_released = False
        for l in letters:
            if disk_manager.safely_release_drive(l, power_down=power_down):
                any_released = True
        if any_released:
            if power_down:
                yield f"[✓] {drive_label} powered down (Offline / 0 RPM) and protected.\n"
            else:
                yield f"[✓] {drive_label} safely unmounted and ready to eject.\n"
    except Exception as e:
        print("Error releasing external mounts:", e)


def release_external_mounts(power_down=None):
    """Safely unmounts external drive mounts in WSL and flushes/sleeps drive when no container uses the external disk."""
    for _ in release_external_mounts_with_logs(power_down=power_down):
        pass


def stop_all_containers_sync(power_down=True):
    """Synchronously stops all running PodMedia containers and powers down external disks (used on application exit)."""
    remove_state_file()
    for m in VALID_MODES:
        remove_mode_state_file(m)
    try:
        if is_podman_machine_running():
            res = subprocess.run(['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'], capture_output=True, text=True, **subproc_kwargs())
            if res.returncode == 0:
                containers = [c.strip() for c in res.stdout.strip().splitlines() if c.strip()]
                if containers:
                    subprocess.run(['podman', 'stop', '-t', '2'] + containers, capture_output=True, text=True, **subproc_kwargs())
        release_external_mounts(power_down=power_down)
    except Exception as e:
        print("Error during sync container cleanup:", e)


def stop_all_containers(power_down=None):
    """Stops all running PodMedia containers and releases/sleeps external disk according to policy."""
    for m in VALID_MODES:
        remove_mode_state_file(m)
    if is_podman_machine_running():
        yield "[!] Stopping all PodMedia services...\n"
        res = subprocess.run(['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'], capture_output=True, text=True, **subproc_kwargs())
        if res.returncode == 0:
            running = [c.strip() for c in res.stdout.strip().splitlines() if c.strip()]
            if not running:
                yield "[i] No running PodMedia containers found.\n"
            for c in running:
                yield f"[-] Stopping: {c}...\n"
                for log in run_command_generator(f'podman stop {c}'):
                    yield log
    else:
        yield "[i] Podman machine is already offline.\n"
    for log in release_external_mounts_with_logs(power_down=power_down):
        yield log
    yield "\n[✓] ALL SERVICES STOPPED (External disk released)!\n"


def start_single_app(app_name):
    """Selectively starts a single application container."""
    for log in ensure_podman_machine():
        yield log
    cname = f"media_center_{app_name}"
    if cname in EXTERNAL_DISK_CONTAINERS:
        for log in ensure_external_mounts(use_external_disk=True, target_containers={cname}):
            yield log
    for log in ensure_network():
        yield log

    app_map = {
        'plex': ('plex', 'plex'),
        'plextraktsync': ('tools', 'plextraktsync'),
        'radarr': ('tools', 'radarr'),
        'sonarr': ('tools', 'sonarr'),
        'prowlarr': ('tools', 'prowlarr'),
        'qbittorrent': ('tools', 'qbittorrent'),
        'flaresolverr': ('tools', 'flaresolverr'),
        'aurral': ('tools', 'aurral'),
        'slskd': ('tools', 'slskd')
    }

    if app_name in app_map:
        folder, service = app_map[app_name]
        yield f"[+] Starting '{app_name.upper()}' service...\n"
        work_dir = os.path.join(project_root, 'services', folder)
        for log in run_command_generator(f'podman-compose up -d {service}', cwd=work_dir):
            yield log
        if app_name == 'plex':
            write_mode_state_file('video')
            if is_plextraktsync_configured():
                yield "[+] Starting PlexTraktSync companion service...\n"
                tools_dir = os.path.join(project_root, 'services', 'tools')
                for log in run_command_generator('podman-compose up -d plextraktsync', cwd=tools_dir):
                    yield log
            try:
                import media_watcher
                media_watcher.trigger_plex_full_scan_on_startup()
            except Exception:
                pass
        elif app_name in ('radarr', 'sonarr'):
            try:
                import media_watcher
                media_watcher.trigger_import_list_sync_on_startup(app_name)
            except Exception:
                pass
        yield f"\n[✓] '{app_name.upper()}' STARTED SUCCESSFULLY!\n"
    else:
        yield f"[X] Unknown service: {app_name}\n"


def stop_single_app(app_name):
    """Selectively stops a single application container."""
    if app_name == 'plex':
        remove_mode_state_file('video')
    for log in ensure_podman_machine():
        yield log

    container_name = f"media_center_{app_name}"
    yield f"[-] Stopping '{app_name.upper()}' service ({container_name})...\n"
    for log in run_command_generator(f'podman stop {container_name}'):
        yield log
    if app_name == 'plex':
        chk = subprocess.run(
            ['podman', 'ps', '-q', '--filter', 'name=^media_center_plextraktsync$'],
            capture_output=True, text=True, **subproc_kwargs()
        )
        if chk.returncode == 0 and chk.stdout.strip():
            yield "[-] Stopping PlexTraktSync companion service (media_center_plextraktsync)...\n"
            for log in run_command_generator('podman stop media_center_plextraktsync'):
                yield log
    for log in release_external_mounts_with_logs():
        yield log
    yield f"\n[✓] '{app_name.upper()}' STOPPED SUCCESSFULLY!\n"
