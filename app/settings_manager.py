import os
import re
import json
import socket
import configparser
import subprocess
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import podman_manager
import radarr_sonarr_sync

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PROPERTIES_PATH = os.path.join(PROJECT_ROOT, "config.properties")
PLEX_ENV_PATH = os.path.join(PROJECT_ROOT, "services", "plex", ".env")
TRAKT_YML_PATH = os.path.join(PROJECT_ROOT, "services", "tools", "plextraktsync", "config", "config.yml")
SLSKD_YML_PATH = os.path.join(PROJECT_ROOT, "services", "tools", "slskd", "config", "slskd.yml")
ARR_CONFIGS = [
    ("radarr", os.path.join(PROJECT_ROOT, "services", "tools", "radarr", "config", "config.xml")),
    ("sonarr", os.path.join(PROJECT_ROOT, "services", "tools", "sonarr", "config", "config.xml")),
    ("prowlarr", os.path.join(PROJECT_ROOT, "services", "tools", "prowlarr", "config", "config.xml")),
]


def _detect_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _read_env_file(path):
    data = {}
    if not os.path.exists(path):
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                data[k.strip()] = v.strip()
    except Exception:
        pass
    return data


def _write_env_file(path, updates):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    written = set()
    new_lines = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}")
                written.add(k)
                continue
        new_lines.append(line)
    for k, v in updates.items():
        if k not in written:
            new_lines.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")


def _read_trakt_yml():
    res = {"available": os.path.exists(TRAKT_YML_PATH), "sync_ratings": True, "sync_watched": True, "scrobble_threshold": 80}
    if not res["available"]:
        return res
    try:
        with open(TRAKT_YML_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        m_rat = re.search(r"^\s*ratings:\s*(true|false)", text, re.MULTILINE | re.IGNORECASE)
        if m_rat:
            res["sync_ratings"] = m_rat.group(1).lower() == "true"
        m_wat = re.search(r"^\s*watched_status:\s*(true|false)", text, re.MULTILINE | re.IGNORECASE)
        if m_wat:
            res["sync_watched"] = m_wat.group(1).lower() == "true"
        m_thr = re.search(r"^\s*scrobble_threshold:\s*(\d+)", text, re.MULTILINE)
        if m_thr:
            res["scrobble_threshold"] = int(m_thr.group(1))
    except Exception:
        pass
    return res


def _write_trakt_yml(sync_ratings, sync_watched, scrobble_threshold):
    if not os.path.exists(TRAKT_YML_PATH):
        return False
    try:
        with open(TRAKT_YML_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        r_val = "true" if sync_ratings else "false"
        w_val = "true" if sync_watched else "false"
        thr = max(50, min(99, int(scrobble_threshold)))
        new_text = re.sub(r"(^\s*ratings:\s*)(?:true|false)", rf"\g<1>{r_val}", text, flags=re.MULTILINE | re.IGNORECASE)
        new_text = re.sub(r"(^\s*watched_status:\s*)(?:true|false)", rf"\g<1>{w_val}", new_text, flags=re.MULTILINE | re.IGNORECASE)
        new_text = re.sub(r"(^\s*scrobble_threshold:\s*)\d+", rf"\g<1>{thr}", new_text, flags=re.MULTILINE)
        if new_text != text:
            with open(TRAKT_YML_PATH, "w", encoding="utf-8") as f:
                f.write(new_text)
            return True
    except Exception:
        pass
    return False


def _read_slskd_yml():
    res = {"available": os.path.exists(SLSKD_YML_PATH), "username": "", "listen_port": 50300, "share_music": True}
    if not res["available"]:
        return res
    try:
        with open(SLSKD_YML_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        m_user = re.search(r'^\s*username:\s*["\']?([^"\'\r\n#]+)["\']?', text, re.MULTILINE)
        if m_user:
            res["username"] = m_user.group(1).strip()
        m_port = re.search(r"^\s*listen_port:\s*(\d+)", text, re.MULTILINE)
        if m_port:
            res["listen_port"] = int(m_port.group(1))
        res["share_music"] = bool(re.search(r"^\s*-\s*/music\b", text, re.MULTILINE))
    except Exception:
        pass
    return res


def _write_slskd_yml(username, listen_port, share_music):
    if not os.path.exists(SLSKD_YML_PATH):
        return False
    try:
        with open(SLSKD_YML_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        new_text = text
        clean_user = str(username or "").strip().replace('"', "")
        if clean_user:
            new_text = re.sub(
                r'(^\s*username:\s*)[^\r\n]+',
                rf'\g<1>"{clean_user}"',
                new_text,
                count=1,
                flags=re.MULTILINE,
            )
        port_val = max(1024, min(65535, int(listen_port or 50300)))
        new_text = re.sub(
            r"(^\s*listen_port:\s*)\d+",
            rf"\g<1>{port_val}",
            new_text,
            count=1,
            flags=re.MULTILINE,
        )
        has_music = bool(re.search(r"^\s*-\s*/music\b", new_text, re.MULTILINE))
        if share_music and not has_music:
            if re.search(r"^\s*directories:\s*\[\s*\]", new_text, re.MULTILINE):
                new_text = re.sub(r"(^\s*directories:)\s*\[\s*\]", r"\g<1>\n    - /music", new_text, count=1, flags=re.MULTILINE)
            elif re.search(r"^shares:\s*\n\s*directories:", new_text, re.MULTILINE):
                new_text = re.sub(r"(^shares:\s*\n\s*directories:)", r"\g<1>\n    - /music", new_text, count=1, flags=re.MULTILINE)
        elif not share_music and has_music:
            new_text = re.sub(r"^\s*-\s*/music\s*\r?\n?", "", new_text, flags=re.MULTILINE)
            new_text = re.sub(r"(^shares:\s*\n\s*directories:)(\s*\n\s*\n|\s*$)", r"\g<1> []\n\n", new_text, count=1, flags=re.MULTILINE)
        if new_text != text:
            with open(SLSKD_YML_PATH, "w", encoding="utf-8") as f:
                f.write(new_text)
            return True
    except Exception:
        pass
    return False


def _read_qbit_limits():
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/api/v2/app/preferences")
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        dl_mb = round((d.get("dl_limit", 0) or 0) / (1024 * 1024), 1)
        up_kb = int(round((d.get("up_limit", 0) or 0) / 1024))
        return {"online": True, "dl_limit_mbps": dl_mb, "up_limit_kbps": up_kb}
    except Exception:
        return {"online": False, "dl_limit_mbps": 0, "up_limit_kbps": 0}


def _write_qbit_limits(dl_limit_mbps, up_limit_kbps):
    try:
        dl_bytes = max(0, int(round(float(dl_limit_mbps or 0) * 1024 * 1024)))
        up_bytes = max(0, int(round(float(up_limit_kbps or 0) * 1024)))
        body = urllib.parse.urlencode({"json": json.dumps({"dl_limit": dl_bytes, "up_limit": up_bytes})}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8080/api/v2/app/setPreferences",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False


def _read_arr_rename():
    val = None
    online = False
    for port, cfg_dir, field in [
        (7878, radarr_sonarr_sync.RADARR_DIR, "renameMovies"),
        (8989, radarr_sonarr_sync.SONARR_DIR, "renameEpisodes"),
    ]:
        key = radarr_sonarr_sync._get_api_key(os.path.join(cfg_dir, "config.xml"))
        if not key:
            continue
        try:
            d = radarr_sonarr_sync._api_request(f"http://127.0.0.1:{port}", key, "/api/v3/config/naming", timeout=2.5)
            if d is not None:
                online = True
                val = bool(d.get(field, True)) if val is None else (val and bool(d.get(field, True)))
        except Exception:
            pass
    return {"online": online, "auto_rename": True if val is None else val}


def _write_arr_rename(enabled):
    changed = False
    for port, cfg_dir, field in [
        (7878, radarr_sonarr_sync.RADARR_DIR, "renameMovies"),
        (8989, radarr_sonarr_sync.SONARR_DIR, "renameEpisodes"),
    ]:
        key = radarr_sonarr_sync._get_api_key(os.path.join(cfg_dir, "config.xml"))
        if not key:
            continue
        try:
            d = radarr_sonarr_sync._api_request(f"http://127.0.0.1:{port}", key, "/api/v3/config/naming", timeout=3)
            if d is not None and bool(d.get(field)) != bool(enabled):
                d[field] = bool(enabled)
                radarr_sonarr_sync._api_request(f"http://127.0.0.1:{port}", key, "/api/v3/config/naming", method="PUT", payload=d, timeout=4)
                changed = True
        except Exception:
            pass
    return changed


def _read_allowed_hosts():
    # Read shared user-facing hosts (excluding internal media_center_* tokens)
    for _, path in ARR_CONFIGS:
        if os.path.exists(path):
            try:
                root = ET.parse(path).getroot()
                el = root.find("AllowedHosts")
                if el is not None and el.text:
                    tokens = [t.strip() for t in el.text.split(",") if t.strip() and not t.strip().startswith("media_center_")]
                    if tokens:
                        return ",".join(tokens)
            except Exception:
                pass
    local_ip = _detect_local_ip()
    return f"localhost,127.0.0.1,*.local,{local_ip}"


def _write_allowed_hosts(hosts_csv):
    base_tokens = []
    for tok in (hosts_csv or "").split(","):
        t = tok.strip()
        if t and not t.startswith("media_center_") and t not in base_tokens:
            base_tokens.append(t)
    if not base_tokens:
        base_tokens = ["localhost", "127.0.0.1", "*.local"]

    changed_services = []
    for svc, path in ARR_CONFIGS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            full_val = ",".join(base_tokens + [f"media_center_{svc}"])
            if "<AllowedHosts>" in text:
                new_text = re.sub(r"<AllowedHosts>.*?</AllowedHosts>", f"<AllowedHosts>{full_val}</AllowedHosts>", text, flags=re.DOTALL)
            else:
                new_text = text.replace("</Config>", f"  <AllowedHosts>{full_val}</AllowedHosts>\n</Config>")
            if new_text != text:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_text)
                changed_services.append(svc)
        except Exception:
            pass
    return changed_services


def get_settings():
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_PROPERTIES_PATH):
        cfg.read(CONFIG_PROPERTIES_PATH, encoding="utf-8")

    plex_env = _read_env_file(PLEX_ENV_PATH)
    trakt_cfg = _read_trakt_yml()
    slskd_cfg = _read_slskd_yml()
    qbit = _read_qbit_limits()
    arr_ren = _read_arr_rename()

    return {
        "server_port": cfg.getint("server", "port", fallback=3000),
        "watchlist_sync_minutes": cfg.getint("automation", "watchlist_sync_minutes", fallback=5),
        "auto_update_enabled": cfg.getboolean("automation", "auto_update_enabled", fallback=True),
        "watched_media_action": cfg.get("automation", "watched_media_action", fallback="keep"),
        "disk_wake_policy": cfg.get("automation", "disk_wake_policy", fallback="media_only"),
        "music_start_slskd": cfg.getboolean("automation", "music_start_slskd", fallback=True),
        "delete_disliked_music": cfg.getboolean("automation", "delete_disliked_music", fallback=True),
        "sync_music_ratings": cfg.getboolean("automation", "sync_music_ratings", fallback=True),
        "music_discovery_schedule": cfg.get("automation", "music_discovery_schedule", fallback="weekly"),
        "music_discovery_size": cfg.getint("automation", "music_discovery_size", fallback=20),
        "aurral_user_api_key_set": bool(cfg.get("automation", "aurral_user_api_key", fallback="").strip()),
        "listenbrainz_token_set": bool(cfg.get("automation", "listenbrainz_token", fallback="").strip()),
        "slskd_available": slskd_cfg["available"],
        "slskd_username": slskd_cfg["username"],
        "slskd_listen_port": slskd_cfg["listen_port"],
        "slskd_share_music": slskd_cfg["share_music"],
        "qbit_online": qbit["online"],
        "dl_limit_mbps": qbit["dl_limit_mbps"],
        "up_limit_kbps": qbit["up_limit_kbps"],
        "arr_online": arr_ren["online"],
        "auto_rename_media": arr_ren["auto_rename"],
        "allowed_hosts": _read_allowed_hosts(),
        "plex_advertise_ip": plex_env.get("PLEX_ADVERTISE_IP", f"http://{_detect_local_ip()}:32400"),
        "plex_hostname": plex_env.get("PLEX_HOSTNAME", "podmedia"),
        "timezone": plex_env.get("TZ", "Europe/Istanbul"),
        "trakt_available": trakt_cfg["available"],
        "trakt_sync_ratings": trakt_cfg["sync_ratings"],
        "trakt_sync_watched": trakt_cfg["sync_watched"],
        "trakt_scrobble_threshold": trakt_cfg["scrobble_threshold"],
    }


def save_settings(payload):
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_PROPERTIES_PATH):
        cfg.read(CONFIG_PROPERTIES_PATH, encoding="utf-8")

    for section in ("server", "paths", "podman", "automation"):
        if not cfg.has_section(section):
            cfg.add_section(section)

    old_port = cfg.getint("server", "port", fallback=3000)
    old_env = _read_env_file(PLEX_ENV_PATH)
    restart_targets = []

    port = max(1024, min(65535, int(payload.get("server_port", old_port))))
    sync_min = max(1, min(180, int(payload.get("watchlist_sync_minutes", 5))))
    auto_update = bool(payload.get("auto_update_enabled", True))
    watched_action = str(payload.get("watched_media_action", "keep")).strip()
    if watched_action not in ("keep", "delete_after_rating", "delete_after_24h", "delete_immediate"):
        watched_action = "keep"
    disk_wake = str(payload.get("disk_wake_policy", "media_only")).strip().lower()
    if disk_wake not in ("media_only", "manual", "always"):
        disk_wake = "media_only"
    music_start_slskd = bool(payload.get("music_start_slskd", True))
    delete_disliked_music = bool(payload.get("delete_disliked_music", True))
    sync_music_ratings = bool(payload.get("sync_music_ratings", True))
    disc_sched = str(payload.get("music_discovery_schedule", "weekly")).strip().lower()
    if disc_sched not in ("off", "daily", "weekly", "biweekly", "monthly"):
        disc_sched = "weekly"
    disc_size = max(5, min(50, int(payload.get("music_discovery_size", 20) or 20)))

    cfg.set("server", "port", str(port))
    if not cfg.has_option("paths", "media_dir"):
        cfg.set("paths", "media_dir", "./media")
    if not cfg.has_option("paths", "downloads_dir"):
        cfg.set("paths", "downloads_dir", "./downloads")
    if not cfg.has_option("podman", "network"):
        cfg.set("podman", "network", "media_center_net")
    cfg.set("automation", "watchlist_sync_minutes", str(sync_min))
    cfg.set("automation", "auto_update_enabled", "true" if auto_update else "false")
    cfg.set("automation", "watched_media_action", watched_action)
    cfg.set("automation", "disk_wake_policy", disk_wake)
    cfg.set("automation", "music_start_slskd", "true" if music_start_slskd else "false")
    cfg.set("automation", "delete_disliked_music", "true" if delete_disliked_music else "false")
    cfg.set("automation", "sync_music_ratings", "true" if sync_music_ratings else "false")
    cfg.set("automation", "music_discovery_schedule", disc_sched)
    cfg.set("automation", "music_discovery_size", str(disc_size))

    # Preserve existing tokens unless a new non-empty token is entered
    new_aurral_key = str(payload.get("aurral_user_api_key", "") or "").strip()
    if new_aurral_key:
        cfg.set("automation", "aurral_user_api_key", new_aurral_key)
    new_lb_token = str(payload.get("listenbrainz_token", "") or "").strip()
    if new_lb_token:
        cfg.set("automation", "listenbrainz_token", new_lb_token)

    with open(CONFIG_PROPERTIES_PATH, "w", encoding="utf-8") as f:
        cfg.write(f)

    if port != old_port:
        restart_targets.append("podmedia")

    # Apply live Watchlist interval, watched-media policy & music rating sync immediately
    try:
        import media_watcher
        if media_watcher.watcher_instance:
            media_watcher.watcher_instance.import_list_sync_interval = sync_min * 60
            media_watcher.watcher_instance.watched_media_action = watched_action
            media_watcher.watcher_instance.delete_disliked_music = delete_disliked_music
            media_watcher.watcher_instance.sync_music_ratings = sync_music_ratings
            media_watcher.watcher_instance.music_discovery_schedule = disc_sched
            media_watcher.watcher_instance.music_discovery_size = disc_size
        if new_aurral_key or new_lb_token or sync_music_ratings:
            import threading
            threading.Thread(
                target=media_watcher.sync_music_ratings_and_recommendations,
                kwargs={"delete_disliked": delete_disliked_music, "sync_ratings": sync_music_ratings},
                daemon=True,
            ).start()
    except Exception:
        pass
    if disk_wake in ("media_only", "manual"):
        try:
            if not podman_manager.get_containers_using_external_disk():
                podman_manager.release_external_mounts(power_down=True)
        except Exception:
            pass

    # Apply live qBittorrent speed limits
    if "dl_limit_mbps" in payload or "up_limit_kbps" in payload:
        _write_qbit_limits(payload.get("dl_limit_mbps", 0), payload.get("up_limit_kbps", 0))

    # Apply live Radarr/Sonarr auto-rename
    if "auto_rename_media" in payload:
        _write_arr_rename(bool(payload.get("auto_rename_media", True)))

    # Update AllowedHosts across Radarr, Sonarr, Prowlarr
    if "allowed_hosts" in payload:
        changed_arr = _write_allowed_hosts(payload.get("allowed_hosts", ""))
        restart_targets.extend(changed_arr)

    # Update Plex .env
    plex_ip = str(payload.get("plex_advertise_ip", old_env.get("PLEX_ADVERTISE_IP", ""))).strip()
    plex_host = str(payload.get("plex_hostname", old_env.get("PLEX_HOSTNAME", "podmedia"))).strip() or "podmedia"
    tz = str(payload.get("timezone", old_env.get("TZ", "Europe/Istanbul"))).strip() or "UTC"
    if (
        plex_ip != old_env.get("PLEX_ADVERTISE_IP", "")
        or plex_host != old_env.get("PLEX_HOSTNAME", "")
        or tz != old_env.get("TZ", "")
    ):
        _write_env_file(PLEX_ENV_PATH, {
            "PLEX_ADVERTISE_IP": plex_ip,
            "PLEX_HOSTNAME": plex_host,
            "TZ": tz,
        })
        restart_targets.append("plex")

    # Update PlexTraktSync config.yml
    if os.path.exists(TRAKT_YML_PATH):
        trakt_changed = _write_trakt_yml(
            bool(payload.get("trakt_sync_ratings", True)),
            bool(payload.get("trakt_sync_watched", True)),
            int(payload.get("trakt_scrobble_threshold", 80)),
        )
        if trakt_changed:
            restart_targets.append("plextraktsync")

    # Update Slskd config/slskd.yml
    if os.path.exists(SLSKD_YML_PATH) and (
        "slskd_username" in payload or "slskd_listen_port" in payload or "slskd_share_music" in payload
    ):
        slskd_cur = _read_slskd_yml()
        slskd_changed = _write_slskd_yml(
            payload.get("slskd_username", slskd_cur["username"]),
            payload.get("slskd_listen_port", slskd_cur["listen_port"]),
            bool(payload.get("slskd_share_music", slskd_cur["share_music"])),
        )
        if slskd_changed:
            restart_targets.append("slskd")

    # Deduplicate while preserving order
    seen = set()
    restart_targets = [x for x in restart_targets if not (x in seen or seen.add(x))]

    return {
        "status": "ok",
        "restart_needed": len(restart_targets) > 0,
        "restart_targets": restart_targets,
        "settings": get_settings(),
    }


def reset_to_defaults():
    local_ip = _detect_local_ip()
    defaults = {
        "server_port": 3000,
        "watchlist_sync_minutes": 5,
        "auto_update_enabled": True,
        "watched_media_action": "keep",
        "disk_wake_policy": "media_only",
        "music_start_slskd": True,
        "delete_disliked_music": True,
        "sync_music_ratings": True,
        "music_discovery_schedule": "weekly",
        "music_discovery_size": 20,
        "slskd_listen_port": 50300,
        "slskd_share_music": True,
        "dl_limit_mbps": 0,
        "up_limit_kbps": 0,
        "auto_rename_media": True,
        "allowed_hosts": f"localhost,127.0.0.1,*.local,{local_ip}",
        "plex_advertise_ip": f"http://{local_ip}:32400",
        "plex_hostname": "podmedia",
        "timezone": "Europe/Istanbul",
        "trakt_sync_ratings": True,
        "trakt_sync_watched": True,
        "trakt_scrobble_threshold": 80,
    }
    res = save_settings(defaults)
    res["reset"] = True
    return res


def restart_services_generator(target_csv=""):
    """Restarts requested (or all currently running) services to apply changed config/env settings."""
    states, _ = podman_manager.get_container_states()
    requested = [t.strip().lower() for t in (target_csv or "").split(",") if t.strip()]
    if not requested:
        # Restart all currently running PodMedia containers
        requested = [name.replace("media_center_", "") for name, running in states.items() if running]

    if not requested:
        yield "[i] Çalışan servis bulunamadı.\n"
        return

    services_dir = os.path.join(PROJECT_ROOT, "services")
    for svc in requested:
        if svc == "podmedia":
            continue
        cname = f"media_center_{svc}"
        if not states.get(cname, False):
            yield f"[i] {svc.upper()} şu an kapalı (yeniden başlatma atlandı).\n"
            continue
        if svc == "plex":
            # Force recreate so updated .env (PLEX_ADVERTISE_IP, TZ, PLEX_HOSTNAME) takes effect
            yield "[+] Recreating Plex container with updated environment...\n"
            for log in podman_manager.run_command_generator("podman-compose up -d --force-recreate plex", cwd=os.path.join(services_dir, "plex")):
                yield log
        else:
            yield f"[+] Restarting {svc.upper()} ({cname})...\n"
            for log in podman_manager.run_command_generator(f"podman restart {cname}"):
                yield log

    yield "\n[✓] SERVİSLER YENİDEN BAŞLATILDI — Ayarlar aktif!\n"
