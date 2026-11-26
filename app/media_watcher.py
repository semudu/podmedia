import os
import time
import threading
import subprocess
import xml.etree.ElementTree as ET
import urllib.request
from disk_manager import disk_manager

def subproc_kwargs():
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs

def _get_plex_token():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prefs_path = os.path.join(project_root, "services", "plex", "config", "Library", "Application Support", "Plex Media Server", "Preferences.xml")
    if os.path.exists(prefs_path):
        try:
            root = ET.parse(prefs_path).getroot()
            return root.attrib.get("PlexOnlineToken", "")
        except Exception:
            pass
    return ""

def _plex_headers():
    tok = _get_plex_token()
    return {"X-Plex-Token": tok} if tok else {}

class MediaWatcher(threading.Thread):
    def __init__(self, media_dir, poll_interval=3, quiet_seconds=6):
        super().__init__(daemon=True)
        self.media_dir = os.path.abspath(media_dir)
        self.poll_interval = poll_interval
        self.quiet_seconds = quiet_seconds
        self.running = True
        self.snapshots = {}
        self.fast_signatures = {}
        self.last_import_list_sync = 0
        self.last_music_cleanup = 0
        self.import_list_sync_interval = self._load_sync_interval()
        self.watched_media_action = self._load_watched_action()
        self.delete_disliked_music = self._load_bool_automation("delete_disliked_music", True)
        self.sync_music_ratings = self._load_bool_automation("sync_music_ratings", True)
        self.music_discovery_schedule = self._load_str_automation("music_discovery_schedule", "weekly")
        self.music_discovery_size = self._load_int_automation("music_discovery_size", 20, 5, 50)
        self.target_folders = {
            'movies': os.path.join(self.media_dir, 'movies'),
            'series': os.path.join(self.media_dir, 'series'),
            'music': os.path.join(self.media_dir, 'music')
        }

    def _load_sync_interval(self):
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.properties")
            if os.path.exists(cfg_path):
                cfg.read(cfg_path, encoding="utf-8")
                mins = cfg.getint("automation", "watchlist_sync_minutes", fallback=5)
                return max(60, mins * 60)
        except Exception:
            pass
        return 300

    def _load_watched_action(self):
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.properties")
            if os.path.exists(cfg_path):
                cfg.read(cfg_path, encoding="utf-8")
                act = cfg.get("automation", "watched_media_action", fallback="keep").strip()
                if act in ("keep", "delete_after_rating", "delete_after_24h", "delete_immediate"):
                    return act
        except Exception:
            pass
        return "keep"

    def _load_bool_automation(self, key, default=True):
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.properties")
            if os.path.exists(cfg_path):
                cfg.read(cfg_path, encoding="utf-8")
                return cfg.getboolean("automation", key, fallback=default)
        except Exception:
            pass
        return default

    def _load_str_automation(self, key, default=""):
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.properties")
            if os.path.exists(cfg_path):
                cfg.read(cfg_path, encoding="utf-8")
                val = cfg.get("automation", key, fallback=default).strip().lower()
                if val:
                    return val
        except Exception:
            pass
        return default

    def _load_int_automation(self, key, default=20, min_val=5, max_val=50):
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.properties")
            if os.path.exists(cfg_path):
                cfg.read(cfg_path, encoding="utf-8")
                v = cfg.getint("automation", key, fallback=default)
                return max(min_val, min(max_val, v))
        except Exception:
            pass
        return default

    def are_containers_running(self):
        """Quickly checks if any PodMedia containers are currently running."""
        try:
            res = subprocess.run(['podman', 'ps', '-q', '--filter', 'name=media_center_'], capture_output=True, text=True, timeout=3, **subproc_kwargs())
            return res.returncode == 0 and bool(res.stdout.strip())
        except Exception:
            return False

    def get_fast_signature(self, path, depth=1):
        """Lightweight signature based on root and child directory mtimes.
        depth=1 avoids spinning mechanical external disks; depth=2 for local SSD music catches Artist/Album upgrades."""
        if not os.path.exists(path):
            return None
        try:
            stat = os.stat(path)
            sig = [stat.st_mtime]
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        s = entry.stat()
                        sig.append((entry.name, s.st_mtime, s.st_size if entry.is_file() else 0))
                        if depth >= 2 and entry.is_dir():
                            with os.scandir(entry.path) as sub_it:
                                for sub in sub_it:
                                    try:
                                        ss = sub.stat()
                                        sig.append((entry.name + "/" + sub.name, ss.st_mtime, ss.st_size if sub.is_file() else 0))
                                    except Exception:
                                        pass
                    except Exception:
                        pass
            return tuple(sig)
        except Exception:
            return None
        
    def get_folder_snapshot(self, path):
        """Returns (total_files, total_bytes, latest_mtime) for a directory tree."""
        if not os.path.exists(path):
            return 0, 0, 0
        total_files = 0
        total_bytes = 0
        latest_mtime = 0
        try:
            for root, dirs, files in os.walk(path):
                for f in files:
                    total_files += 1
                    fp = os.path.join(root, f)
                    try:
                        stat = os.stat(fp)
                        total_bytes += stat.st_size
                        if stat.st_mtime > latest_mtime:
                            latest_mtime = stat.st_mtime
                    except Exception:
                        pass
        except Exception:
            pass
        return total_files, total_bytes, latest_mtime

    def is_any_file_locked(self, path):
        """Checks if any file modified in the last 60 seconds is still open/locked for writing."""
        if not os.path.exists(path):
            return False
        now = time.time()
        try:
            for root, dirs, files in os.walk(path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(fp)
                        if now - mtime < 60:
                            # Try opening file to verify it is no longer locked by Windows Explorer/Downloader
                            with open(fp, 'r+b'):
                                pass
                    except (IOError, OSError):
                        return True  # File is currently locked / being written to
        except Exception:
            pass
        return False

    def wait_for_copy_completion(self, cat, path):
        """Waits until folder file count, total size, and mtime remain 100% stable with no locks."""
        print(f"⏳ [MediaWatcher] Change detected in '{cat}' ({path}). Waiting for file copy/write to complete...")
        last_snap = self.get_folder_snapshot(path)
        stable_start = time.time()
        
        while self.running:
            time.sleep(2)
            current_snap = self.get_folder_snapshot(path)
            locked = self.is_any_file_locked(path)
            
            if current_snap != last_snap or locked:
                # Active file copy or write still in progress! Reset quiet timer
                last_snap = current_snap
                stable_start = time.time()
            else:
                # No new file writes or size changes detected
                elapsed = time.time() - stable_start
                if elapsed >= self.quiet_seconds:
                    print(f"✓ [MediaWatcher] Copy/write settled for '{cat}' library ({current_snap[0]} files, {current_snap[1]} bytes).")
                    return current_snap

    def get_plex_section_mapping(self):
        mapping = {}
        try:
            url = "http://localhost:32400/library/sections"
            req = urllib.request.Request(url, headers=_plex_headers())
            with urllib.request.urlopen(req, timeout=2) as resp:
                xml_data = resp.read()
                root = ET.fromstring(xml_data)
                for directory in root.findall('Directory'):
                    sec_id = directory.attrib.get('key')
                    sec_type = directory.attrib.get('type')  # 'movie', 'show', 'artist'
                    
                    if sec_type == 'movie':
                        mapping['movies'] = sec_id
                    elif sec_type in ('show', 'series'):
                        mapping['series'] = sec_id
                    elif sec_type in ('artist', 'music'):
                        mapping['music'] = sec_id
        except Exception:
            pass
        return mapping

    def trigger_plex_refresh(self, category):
        mapping = self.get_plex_section_mapping()
        sec_id = mapping.get(category)
        if sec_id:
            url = f"http://localhost:32400/library/sections/{sec_id}/refresh"
        else:
            url = "http://localhost:32400/library/sections/all/refresh"
            
        try:
            req = urllib.request.Request(url, headers=_plex_headers())
            urllib.request.urlopen(req, timeout=3)
            print(f"[MediaWatcher] Triggered Plex section refresh for category '{category}' (section {sec_id})")
        except Exception as e:
            pass

    def trigger_plex_empty_trash(self, category):
        """Purges deleted items from Plex library when deliberate file deletion is detected."""
        mapping = self.get_plex_section_mapping()
        sec_id = mapping.get(category)
        if sec_id:
            url = f"http://localhost:32400/library/sections/{sec_id}/emptyTrash"
        else:
            url = "http://localhost:32400/library/sections/all/emptyTrash"
        try:
            req = urllib.request.Request(url, headers=_plex_headers(), method='PUT')
            urllib.request.urlopen(req, timeout=3)
            print(f"[MediaWatcher] Triggered Plex section emptyTrash for category '{category}' (section {sec_id})")
        except Exception:
            pass

    def run(self):
        # Trigger a full scan of all Plex libraries on startup
        trigger_plex_full_scan_on_startup()

        for cat, path in self.target_folders.items():
            self.fast_signatures[cat] = self.get_fast_signature(path, depth=2 if cat == 'music' else 1)
            self.snapshots[cat] = self.get_folder_snapshot(path)

        last_ext_connected = False
        containers_active = True
        loop_counter = 0

        while self.running:
            time.sleep(self.poll_interval)
            loop_counter += 1

            # Check container status every 3 iterations (~9 seconds)
            if loop_counter % 3 == 0:
                containers_active = self.are_containers_running()

            # If NO media containers are running, do NOT touch external drives!
            # Keeps mechanical drives asleep and frees handles so drives can be safely ejected.
            if not containers_active:
                continue

            # Periodic ImportListSync & watched-media watchlist/disk hygiene
            now_ts = time.time()
            if now_ts - self.last_import_list_sync >= self.import_list_sync_interval:
                self.last_import_list_sync = now_ts
                threading.Thread(target=sync_trakt_and_import_lists, daemon=True).start()
                threading.Thread(target=cleanup_watched_media, args=(self.watched_media_action,), daemon=True).start()

            # Periodic Plexamp music rating sync (Aurral + ListenBrainz), <2★ disliked cleanup & scheduled discovery downloads
            if now_ts - self.last_music_cleanup >= 60:
                self.last_music_cleanup = now_ts
                if self.delete_disliked_music or self.sync_music_ratings:
                    threading.Thread(
                        target=sync_music_ratings_and_recommendations,
                        kwargs={
                            "delete_disliked": self.delete_disliked_music,
                            "sync_ratings": self.sync_music_ratings,
                            "music_dir": self.target_folders.get("music"),
                        },
                        daemon=True,
                    ).start()
                if self.music_discovery_schedule and self.music_discovery_schedule != "off":
                    threading.Thread(
                        target=run_scheduled_music_discovery,
                        kwargs={"force": False},
                        daemon=True,
                    ).start()

            # Dynamically update external disk paths (only when a container actually mounts the external disk)
            try:
                import podman_manager
                ext_svcs_active = bool(podman_manager.get_containers_using_external_disk())
                active_disk = disk_manager.get_active_disk() if ext_svcs_active else None
                is_ext_connected = bool(active_disk and active_disk.get('connected'))
                if is_ext_connected:
                    self.target_folders['movies_ext'] = active_disk['movies_path']
                    self.target_folders['series_ext'] = active_disk['series_path']
                    if not last_ext_connected:
                        print(f"🔌 [MediaWatcher] External disk connected ({active_disk.get('label')}, {active_disk.get('letter')}). Refreshing Plex & syncing Radarr/Sonarr...")
                        try:
                            import radarr_sonarr_sync
                            radarr_sonarr_sync.sync_all()
                        except Exception:
                            pass
                        self.trigger_plex_refresh('movies')
                        self.trigger_plex_refresh('series')
                else:
                    self.target_folders.pop('movies_ext', None)
                    self.target_folders.pop('series_ext', None)
                last_ext_connected = is_ext_connected
            except Exception:
                pass

            for cat, path in list(self.target_folders.items()):
                if not os.path.exists(path):
                    # Directory is offline / unmounted (e.g. external disk detached)
                    # Skip without triggering false deletion scan in Plex!
                    continue

                # Fast signature check (depth=2 for local SSD music to catch Artist/Album upgrades)
                sig_depth = 2 if cat == 'music' else 1
                current_fast_sig = self.get_fast_signature(path, depth=sig_depth)
                last_fast_sig = self.fast_signatures.get(cat)

                if current_fast_sig == last_fast_sig and current_fast_sig is not None:
                    # Nothing changed at all; skip without touching deeper files!
                    continue

                self.fast_signatures[cat] = current_fast_sig
                current_snapshot = self.get_folder_snapshot(path)
                last_snapshot = self.snapshots.get(cat, (0, 0, 0))
                
                if current_snapshot != last_snapshot:
                    settled_snapshot = self.wait_for_copy_completion(cat, path)
                    if cat == 'music':
                        cleanup_music_hygiene(path)
                        settled_snapshot = self.get_folder_snapshot(path)
                    self.snapshots[cat] = settled_snapshot
                    self.fast_signatures[cat] = self.get_fast_signature(path, depth=sig_depth)
                    plex_cat = cat.replace('_ext', '')
                    
                    # Refresh Plex library; for Music (local SSD), run delayed emptyTrash after scan settles
                    # so upgraded/deleted tracks never linger as ghost duplicates in Plexamp.
                    self.trigger_plex_refresh(plex_cat)
                    if plex_cat == 'music':
                        schedule_plex_empty_trash('music', delay=8)
                    else:
                        # Only empty trash for movies/series when external disk is actually mounted & online
                        try:
                            import podman_manager
                            if podman_manager.get_plex_disk_mode() != 'stub':
                                schedule_plex_empty_trash(plex_cat, delay=8)
                        except Exception:
                            pass

                    # Synchronize Radarr and Sonarr so deleted items are unmonitored and never re-downloaded
                    if plex_cat in ('movies', 'series'):
                        try:
                            import radarr_sonarr_sync
                            radarr_sonarr_sync.sync_all()
                        except Exception as e:
                            print("Sync error during watcher event:", e)

    def stop(self):
        self.running = False

def schedule_plex_empty_trash(category, delay=8):
    """Empties Plex trash for a single section AFTER scan finishes so upgraded/deleted tracks disappear cleanly."""
    def _worker():
        time.sleep(max(1, delay))
        try:
            sec_id = _get_plex_section_id(category)
            if not sec_id:
                return
            url = f"http://127.0.0.1:32400/library/sections/{sec_id}/emptyTrash"
            req = urllib.request.Request(url, headers=_plex_headers(), method='PUT')
            urllib.request.urlopen(req, timeout=5).close()
            print(f"🧹 [MediaWatcher] Emptied Plex '{category}' trash (section {sec_id}).")
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()


def _get_plex_section_id(category):
    try:
        req = urllib.request.Request("http://127.0.0.1:32400/library/sections", headers=_plex_headers())
        with urllib.request.urlopen(req, timeout=3) as resp:
            root = ET.fromstring(resp.read())
            for directory in root.findall('Directory'):
                sec_id = directory.attrib.get('key')
                sec_type = directory.attrib.get('type')
                if category == 'movies' and sec_type == 'movie':
                    return sec_id
                if category == 'series' and sec_type in ('show', 'series'):
                    return sec_id
                if category == 'music' and sec_type in ('artist', 'music'):
                    return sec_id
    except Exception:
        pass
    return None


def trigger_plex_full_scan_on_startup():
    """Waits asynchronously for Plex Web server startup, then scans active libraries.
    When Plex runs in disk-free stub mode (Music/Plexamp), scans & purges trash ONLY for Music."""
    def _worker():
        for _ in range(15):
            time.sleep(2)
            try:
                stub_mode = False
                try:
                    import podman_manager
                    stub_mode = (podman_manager.get_plex_disk_mode() == 'stub')
                except Exception:
                    pass
                if stub_mode:
                    sec_id = _get_plex_section_id('music')
                    if not sec_id:
                        continue
                    url = f"http://127.0.0.1:32400/library/sections/{sec_id}/refresh"
                    req = urllib.request.Request(url, headers=_plex_headers())
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        if resp.status == 200:
                            print("🚀 [Plex Startup] Triggered Music section scan (stub mode).")
                            schedule_plex_empty_trash('music', delay=10)
                            return
                else:
                    url = "http://127.0.0.1:32400/library/sections/all/refresh"
                    req = urllib.request.Request(url, headers=_plex_headers())
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        if resp.status == 200:
                            print("🚀 [Plex Startup] Triggered full library scan for all sections on Plex startup.")
                            schedule_plex_empty_trash('music', delay=10)
                            return
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

_trakt_sync_lock = threading.Lock()

def _push_item_to_radarr_or_sonarr(kind, obj):
    """Pushes a Watchlist movie/show directly to Radarr/Sonarr if online and not already present or excluded."""
    import radarr_sonarr_sync
    ids = obj.get("ids") or {}

    if kind == "movie" and ids.get("tmdb"):
        tmdb_id = int(ids["tmdb"])
        imdb_id = ids.get("imdb") or ""
        if radarr_sonarr_sync.is_media_excluded("movie", tmdb_id=tmdb_id, imdb_id=imdb_id):
            return
        api_key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.RADARR_DIR, "config.xml"))
        if not api_key or not radarr_sonarr_sync._is_service_online("http://127.0.0.1:7878", api_key):
            return
        try:
            existing = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, f"/api/v3/movie?tmdbId={tmdb_id}")
            if existing:
                return
            exclusions = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, "/api/v3/exclusions") or []
            if any(int(e.get("tmdbId") or 0) == tmdb_id for e in exclusions):
                return
            lists = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, "/api/v3/importlist") or []
            q_id = lists[0].get("qualityProfileId") if lists else None
            root_path = lists[0].get("rootFolderPath") if lists else None
            if not q_id:
                profs = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, "/api/v3/qualityprofile") or []
                q_id = profs[0]["id"] if profs else 1
            if not root_path:
                roots = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, "/api/v3/rootfolder") or []
                root_path = roots[0]["path"] if roots else "/movies"
            lookup = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, f"/api/v3/movie/lookup/tmdb?tmdbId={tmdb_id}", timeout=15)
            if not lookup:
                return
            lookup["qualityProfileId"] = q_id
            lookup["rootFolderPath"] = root_path
            lookup["monitored"] = True
            lookup["addOptions"] = {"monitor": "movieOnly", "searchForMovie": True}
            radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, "/api/v3/movie", method="POST", payload=lookup, timeout=15)
            radarr_sonarr_sync.reconcile_arr_deletions()
            print(f"🎬 [Trakt->Radarr] Added & searching: {obj.get('title')} ({obj.get('year')})")
        except Exception as e:
            print(f"⚠️ [Trakt->Radarr] Could not add {obj.get('title')}: {e}")

    elif kind == "show" and ids.get("tvdb"):
        tvdb_id = int(ids["tvdb"])
        imdb_id = ids.get("imdb") or ""
        if radarr_sonarr_sync.is_media_excluded("show", tvdb_id=tvdb_id, imdb_id=imdb_id):
            return
        api_key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.SONARR_DIR, "config.xml"))
        if not api_key or not radarr_sonarr_sync._is_service_online("http://127.0.0.1:8989", api_key):
            return
        try:
            existing = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", api_key, f"/api/v3/series?tvdbId={tvdb_id}")
            if existing:
                return
            exclusions = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", api_key, "/api/v3/importlistexclusion") or []
            if any(int(e.get("tvdbId") or 0) == tvdb_id for e in exclusions):
                return
            lists = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", api_key, "/api/v3/importlist") or []
            q_id = lists[0].get("qualityProfileId") if lists else None
            root_path = lists[0].get("rootFolderPath") if lists else None
            if not q_id:
                profs = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", api_key, "/api/v3/qualityprofile") or []
                q_id = profs[0]["id"] if profs else 1
            if not root_path:
                roots = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", api_key, "/api/v3/rootfolder") or []
                root_path = roots[0]["path"] if roots else "/tv"
            lookup_list = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", api_key, f"/api/v3/series/lookup?term=tvdb:{tvdb_id}", timeout=15)
            if not lookup_list:
                return
            lookup = lookup_list[0]
            lookup["qualityProfileId"] = q_id
            lookup["rootFolderPath"] = root_path
            lookup["monitored"] = True
            lookup["addOptions"] = {"searchForMissingEpisodes": True}
            radarr_sonarr_sync._api_request("http://127.0.0.1:8989", api_key, "/api/v3/series", method="POST", payload=lookup, timeout=15)
            radarr_sonarr_sync.reconcile_arr_deletions()
            print(f"📺 [Trakt->Sonarr] Added & searching: {obj.get('title')} ({obj.get('year')})")
        except Exception as e:
            print(f"⚠️ [Trakt->Sonarr] Could not add {obj.get('title')}: {e}")


def _make_watchlist_key(kind, tmdb=None, tvdb=None, imdb=None, plex_guid=None):
    if kind == "movie":
        if tmdb:
            return f"movie:tmdb:{int(tmdb)}"
        if imdb:
            return f"movie:imdb:{str(imdb)}"
    else:
        if tvdb:
            return f"show:tvdb:{int(tvdb)}"
        if tmdb:
            return f"show:tmdb:{int(tmdb)}"
        if imdb:
            return f"show:imdb:{str(imdb)}"
    if plex_guid:
        return f"{kind}:plex:{plex_guid}"
    return None


def sync_trakt_watchlist():
    """Two-way exclusion-aware Watchlist sync between Trakt Watchlist, Plex Watchlist, Radarr, and Sonarr.
    - Detects Radarr/Sonarr UI deletions first so deleted items never get re-added.
    - Propagates Watchlist removals across both Plex & Trakt instead of resurrecting removed items.
    - Blocks excluded items unless freshly re-added to Trakt Watchlist after exclusion timestamp."""
    import json
    import radarr_sonarr_sync

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pytrakt_path = os.path.join(project_root, "services", "tools", "plextraktsync", "config", ".pytrakt.json")
    plex_token = _get_plex_token()
    if not plex_token:
        return False
    if not _trakt_sync_lock.acquire(blocking=False):
        return False
    try:
        # Step 0: Catch any movie/series deleted in Radarr/Sonarr UI BEFORE touching Watchlists
        radarr_sonarr_sync.reconcile_arr_deletions()

        trakt_configured = False
        trakt_wl = []
        if os.path.exists(pytrakt_path):
            with open(pytrakt_path, "r", encoding="utf-8") as f:
                trakt_cfg = json.load(f)
            client_id = trakt_cfg.get("CLIENT_ID")
            oauth_token = trakt_cfg.get("OAUTH_TOKEN")
            if client_id and oauth_token:
                trakt_req = urllib.request.Request(
                    "https://api.trakt.tv/sync/watchlist",
                    headers={
                        "Content-Type": "application/json",
                        "trakt-api-version": "2",
                        "trakt-api-key": client_id,
                        "Authorization": f"Bearer {oauth_token}",
                        "User-Agent": "PodMedia/1.0",
                    },
                )
                with urllib.request.urlopen(trakt_req, timeout=10) as resp:
                    trakt_wl = json.loads(resp.read().decode("utf-8")) or []
                trakt_configured = True

        # Step 1: Fetch full Plex Watchlist with cached external GUIDs (tmdb, tvdb, imdb)
        plex_items = radarr_sonarr_sync.resolve_plex_watchlist_items(plex_token)

        # Build keyed maps & purge any already-excluded items lingering on either Watchlist
        plex_map = {}
        plex_rks = set()
        for p in plex_items:
            rk = p.get("ratingKey")
            if rk:
                plex_rks.add(str(rk))
            kind = p.get("kind", "movie")
            tmdb = p.get("tmdb")
            tvdb = p.get("tvdb")
            imdb = p.get("imdb")

            excl = radarr_sonarr_sync.is_media_excluded(kind, tmdb_id=tmdb, tvdb_id=tvdb, imdb_id=imdb)
            if excl:
                # Stale/cached excluded item on Plex Watchlist -> purge it unless freshly re-added on Trakt below
                radarr_sonarr_sync.remove_from_watchlists(
                    kind, tmdb_id=tmdb, tvdb_id=tvdb, imdb_id=imdb, plex_guid=rk, title=p.get("title", ""), year=p.get("year")
                )
                plex_rks.discard(str(rk))
                continue

            wkey = _make_watchlist_key(kind, tmdb=tmdb, tvdb=tvdb, imdb=imdb, plex_guid=rk)
            if wkey:
                plex_map[wkey] = p

        trakt_map = {}
        for item in trakt_wl:
            raw_kind = item.get("type")
            if raw_kind not in ("movie", "show"):
                continue
            obj = item.get(raw_kind) or {}
            ids = obj.get("ids") or {}
            tmdb = int(ids["tmdb"]) if ids.get("tmdb") else None
            tvdb = int(ids["tvdb"]) if ids.get("tvdb") else None
            imdb = str(ids["imdb"]) if ids.get("imdb") else None
            pg = (ids.get("plex") or {}).get("guid") if isinstance(ids.get("plex"), dict) else None
            listed_ts = _parse_iso_ts(item.get("listed_at"))

            excl = radarr_sonarr_sync.is_media_excluded(raw_kind, tmdb_id=tmdb, tvdb_id=tvdb, imdb_id=imdb)
            if excl:
                excl_ts = int(excl.get("ts") or 0)
                if listed_ts > 0 and listed_ts > (excl_ts + 120):
                    # User intentionally re-added this title to Trakt Watchlist AFTER deleting/excluding it!
                    radarr_sonarr_sync.unexclude_media(raw_kind, tmdb if raw_kind == "movie" else tvdb)
                    print(f"♻️ [Watchlist] Lifted exclusion for freshly re-watchlisted {raw_kind}: {obj.get('title')}")
                else:
                    radarr_sonarr_sync.remove_from_watchlists(
                        raw_kind, tmdb_id=tmdb, tvdb_id=tvdb, imdb_id=imdb, plex_guid=pg, title=obj.get("title", ""), year=obj.get("year")
                    )
                    continue

            wkey = _make_watchlist_key(raw_kind, tmdb=tmdb, tvdb=tvdb, imdb=imdb, plex_guid=pg)
            if wkey:
                trakt_map[wkey] = {
                    "kind": raw_kind,
                    "obj": obj,
                    "tmdb": tmdb,
                    "tvdb": tvdb,
                    "imdb": imdb,
                    "plex_guid": pg,
                    "title": obj.get("title", ""),
                    "year": obj.get("year"),
                    "listed_ts": listed_ts,
                }

        # Step 2: Two-way removal propagation using previous synced_watchlist snapshot
        state = radarr_sonarr_sync.load_media_sync_state()
        prev_wl = state.get("synced_watchlist") or {}

        for wkey, prev_entry in list(prev_wl.items()):
            was_trakt = bool(prev_entry.get("in_trakt"))
            was_plex = bool(prev_entry.get("in_plex"))
            now_trakt = wkey in trakt_map
            now_plex = wkey in plex_map
            p_kind = prev_entry.get("kind", "movie")

            if trakt_configured and was_trakt and not now_trakt:
                # User (or Trakt scrobble) removed item from Trakt Watchlist -> remove from Plex Watchlist too!
                if now_plex:
                    p_item = plex_map.pop(wkey, {})
                    radarr_sonarr_sync.remove_from_watchlists(
                        p_kind,
                        tmdb_id=prev_entry.get("tmdb") or p_item.get("tmdb"),
                        tvdb_id=prev_entry.get("tvdb") or p_item.get("tvdb"),
                        imdb_id=prev_entry.get("imdb") or p_item.get("imdb"),
                        plex_guid=p_item.get("ratingKey") or prev_entry.get("plex_guid"),
                        title=prev_entry.get("title", ""),
                        year=prev_entry.get("year"),
                    )
                    if p_item.get("ratingKey"):
                        plex_rks.discard(str(p_item["ratingKey"]))
                    print(f"🧹 [Trakt->Plex] Propagated Trakt Watchlist removal to Plex: {prev_entry.get('title')}")
            elif was_plex and not now_plex:
                # User removed item from Plex Watchlist -> remove from Trakt Watchlist instead of resurrecting!
                if trakt_configured and now_trakt:
                    t_item = trakt_map.pop(wkey, {})
                    radarr_sonarr_sync.remove_from_watchlists(
                        p_kind,
                        tmdb_id=prev_entry.get("tmdb") or t_item.get("tmdb"),
                        tvdb_id=prev_entry.get("tvdb") or t_item.get("tvdb"),
                        imdb_id=prev_entry.get("imdb") or t_item.get("imdb"),
                        plex_guid=prev_entry.get("plex_guid") or t_item.get("plex_guid"),
                        title=prev_entry.get("title", ""),
                        year=prev_entry.get("year"),
                    )
                    print(f"🧹 [Plex->Trakt] Propagated Plex Watchlist removal to Trakt: {prev_entry.get('title')}")

        # Step 3: Push active Trakt Watchlist items to Plex Watchlist & Radarr/Sonarr
        for wkey, t_entry in trakt_map.items():
            pg = t_entry.get("plex_guid")
            if pg and pg not in plex_rks and wkey not in plex_map:
                put_req = urllib.request.Request(
                    f"https://discover.provider.plex.tv/actions/addToWatchlist?ratingKey={pg}",
                    headers={
                        "X-Plex-Token": plex_token,
                        "X-Plex-Client-Identifier": "podmedia-sync",
                        "User-Agent": "PodMedia/1.0",
                    },
                    method="PUT",
                )
                try:
                    with urllib.request.urlopen(put_req, timeout=10) as pr:
                        if pr.status in (200, 204):
                            plex_rks.add(pg)
                            plex_map[wkey] = {
                                "ratingKey": pg,
                                "kind": t_entry["kind"],
                                "tmdb": t_entry["tmdb"],
                                "tvdb": t_entry["tvdb"],
                                "imdb": t_entry["imdb"],
                                "title": t_entry["title"],
                                "year": t_entry["year"],
                            }
                            print(f"🔄 [Trakt->Plex] Added to Plex Watchlist: {t_entry.get('title')} ({t_entry.get('year')})")
                except Exception as e:
                    print(f"⚠️ [Trakt->Plex] Failed to add {t_entry.get('title')}: {e}")

            _push_item_to_radarr_or_sonarr(t_entry["kind"], t_entry["obj"])

        # Also push any Plex-only Watchlist items straight to Radarr/Sonarr (unless excluded)
        for wkey, p_entry in plex_map.items():
            if wkey in trakt_map:
                continue
            ids_dict = {}
            if p_entry.get("tmdb"):
                ids_dict["tmdb"] = p_entry["tmdb"]
            if p_entry.get("tvdb"):
                ids_dict["tvdb"] = p_entry["tvdb"]
            if p_entry.get("imdb"):
                ids_dict["imdb"] = p_entry["imdb"]
            if ids_dict:
                _push_item_to_radarr_or_sonarr(
                    p_entry.get("kind", "movie"),
                    {"title": p_entry.get("title", ""), "year": p_entry.get("year"), "ids": ids_dict},
                )

        # Step 4: Persist unified Watchlist snapshot
        new_wl = {}
        all_keys = set(trakt_map.keys()) | set(plex_map.keys())
        for wkey in all_keys:
            te = trakt_map.get(wkey) or {}
            pe = plex_map.get(wkey) or {}
            new_wl[wkey] = {
                "kind": te.get("kind") or pe.get("kind") or "movie",
                "tmdb": te.get("tmdb") or pe.get("tmdb"),
                "tvdb": te.get("tvdb") or pe.get("tvdb"),
                "imdb": te.get("imdb") or pe.get("imdb"),
                "plex_guid": pe.get("ratingKey") or te.get("plex_guid"),
                "title": te.get("title") or pe.get("title") or "",
                "year": te.get("year") or pe.get("year"),
                "in_trakt": wkey in trakt_map,
                "in_plex": wkey in plex_map,
            }
        state = radarr_sonarr_sync.load_media_sync_state()
        state["synced_watchlist"] = new_wl
        radarr_sonarr_sync.save_media_sync_state(state)
        return True
    except Exception as e:
        print(f"⚠️ [WatchlistSync] Watchlist sync skipped: {e}")
    finally:
        _trakt_sync_lock.release()
    return False


def sync_trakt_and_import_lists(target='both'):
    """Reconciles Radarr/Sonarr deletions, syncs Trakt <-> Plex Watchlists, then triggers ImportListSync."""
    try:
        import radarr_sonarr_sync
        radarr_sonarr_sync.reconcile_arr_deletions()
    except Exception:
        pass
    sync_trakt_watchlist()
    try:
        import radarr_sonarr_sync
        if target in ('both', 'radarr'):
            radarr_sonarr_sync.trigger_radarr_command("ImportListSync")
        if target in ('both', 'sonarr'):
            radarr_sonarr_sync.trigger_sonarr_command("ImportListSync")
    except Exception:
        pass


def trigger_import_list_sync_on_startup(target='both'):
    """Waits asynchronously for Radarr/Sonarr API startup, then reconciles deletions & syncs Watchlists."""
    def _worker():
        import radarr_sonarr_sync
        targets = []
        if target in ('both', 'radarr'):
            targets.append(('radarr', 'http://127.0.0.1:7878', os.path.join(radarr_sonarr_sync.RADARR_DIR, 'config.xml'), radarr_sonarr_sync.trigger_radarr_command))
        if target in ('both', 'sonarr'):
            targets.append(('sonarr', 'http://127.0.0.1:8989', os.path.join(radarr_sonarr_sync.SONARR_DIR, 'config.xml'), radarr_sonarr_sync.trigger_sonarr_command))

        pending = list(targets)
        synced_trakt = False
        for _ in range(15):
            if not pending:
                return
            time.sleep(2)
            still_pending = []
            for name, base_url, xml_path, trigger_fn in pending:
                key = radarr_sonarr_sync._get_api_key(xml_path)
                if key and radarr_sonarr_sync._is_service_online(base_url, key):
                    radarr_sonarr_sync.reconcile_arr_deletions()
                    if not synced_trakt:
                        sync_trakt_watchlist()
                        synced_trakt = True
                    trigger_fn("ImportListSync")
                    print(f"🚀 [{name.capitalize()} Startup] Triggered immediate ImportListSync (Plex/Trakt Watchlists).")
                else:
                    still_pending.append((name, base_url, xml_path, trigger_fn))
            pending = still_pending

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


_watched_cleanup_lock = threading.Lock()


def _parse_iso_ts(iso_str):
    if not iso_str:
        return 0
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def cleanup_watched_media(mode="keep"):
    """Manages watched/rated movies (Radarr) and TV shows (Sonarr) across Plex & Trakt:
    - 'keep': keeps media files on disk, but removes watched items from Plex & Trakt Watchlists
      and adds Import Exclusions so if manually deleted later they NEVER re-download.
    - 'delete_after_rating': after watching/rating on Plex or Trakt (two-way synced first),
      removes from Watchlists, adds Import Exclusion, unmonitors, and deletes media file(s).
    - 'delete_after_24h': deletes 24 hours after watching (Plex or Trakt).
    - 'delete_immediate': deletes ~10 minutes after watching (Plex or Trakt).
    Always skips anything currently being streamed in Plex and skips when Plex runs in music-only stub mode."""
    if mode not in ("keep", "delete_after_rating", "delete_after_24h", "delete_immediate"):
        return
    if not _watched_cleanup_lock.acquire(blocking=False):
        return
    try:
        import json
        from datetime import datetime, timezone
        import radarr_sonarr_sync
        import podman_manager

        plex_stub_mode = (podman_manager.get_plex_disk_mode() == "stub")
        plex_token = _get_plex_token()
        if not plex_token:
            return
        headers = {"X-Plex-Token": plex_token, "Accept": "application/json"}
        min_age = 86400 if mode == "delete_after_24h" else (60 if mode in ("keep", "delete_after_rating") else 600)
        delete_files = mode in ("delete_after_rating", "delete_after_24h", "delete_immediate")
        now_ts = time.time()

        # 1. Check active Plex streaming sessions so we NEVER touch a file or series currently being played
        active_keys = set()
        try:
            sreq = urllib.request.Request("http://127.0.0.1:32400/status/sessions", headers=headers)
            with urllib.request.urlopen(sreq, timeout=4) as sresp:
                sdata = json.loads(sresp.read().decode("utf-8"))
                for m in sdata.get("MediaContainer", {}).get("Metadata", []) or []:
                    for kname in ("ratingKey", "parentRatingKey", "grandparentRatingKey"):
                        if m.get(kname):
                            active_keys.add(str(m[kname]))
        except Exception:
            return  # Plex offline -> skip safely

        # Load Trakt credentials if available
        trakt_headers = radarr_sonarr_sync._get_trakt_headers()

        trakt_watched_tmdb = {}
        trakt_watched_imdb = {}
        trakt_ratings_tmdb = {}
        trakt_ratings_imdb = {}
        trakt_show_ratings_tvdb = {}
        trakt_show_ratings_imdb = {}
        if trakt_headers:
            try:
                tw_req = urllib.request.Request("https://api.trakt.tv/sync/watched/movies", headers=trakt_headers)
                with urllib.request.urlopen(tw_req, timeout=8) as tw_resp:
                    for entry in json.loads(tw_resp.read().decode("utf-8")) or []:
                        w_ts = _parse_iso_ts(entry.get("last_watched_at"))
                        m_ids = (entry.get("movie") or {}).get("ids") or {}
                        if m_ids.get("tmdb"):
                            trakt_watched_tmdb[int(m_ids["tmdb"])] = w_ts
                        if m_ids.get("imdb"):
                            trakt_watched_imdb[str(m_ids["imdb"])] = w_ts

                tr_req = urllib.request.Request("https://api.trakt.tv/sync/ratings/movies", headers=trakt_headers)
                with urllib.request.urlopen(tr_req, timeout=8) as tr_resp:
                    for entry in json.loads(tr_resp.read().decode("utf-8")) or []:
                        r_score = entry.get("rating")
                        r_ts = _parse_iso_ts(entry.get("rated_at"))
                        m_ids = (entry.get("movie") or {}).get("ids") or {}
                        if r_score:
                            pair = (int(r_score), r_ts)
                            if m_ids.get("tmdb"):
                                trakt_ratings_tmdb[int(m_ids["tmdb"])] = pair
                            if m_ids.get("imdb"):
                                trakt_ratings_imdb[str(m_ids["imdb"])] = pair

                trs_req = urllib.request.Request("https://api.trakt.tv/sync/ratings/shows", headers=trakt_headers)
                with urllib.request.urlopen(trs_req, timeout=8) as trs_resp:
                    for entry in json.loads(trs_resp.read().decode("utf-8")) or []:
                        r_score = entry.get("rating")
                        r_ts = _parse_iso_ts(entry.get("rated_at"))
                        s_ids = (entry.get("show") or {}).get("ids") or {}
                        if r_score:
                            pair = (int(r_score), r_ts)
                            if s_ids.get("tvdb"):
                                trakt_show_ratings_tvdb[int(s_ids["tvdb"])] = pair
                            if s_ids.get("imdb"):
                                trakt_show_ratings_imdb[str(s_ids["imdb"])] = pair
            except Exception:
                if delete_files:
                    return  # Trakt configured but unreachable -> skip file deletion safely

        # 2. Fetch Plex library sections
        sec_req = urllib.request.Request("http://127.0.0.1:32400/library/sections", headers=headers)
        with urllib.request.urlopen(sec_req, timeout=5) as resp:
            sections = json.loads(resp.read().decode("utf-8")).get("MediaContainer", {}).get("Directory", []) or []

        radarr_key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.RADARR_DIR, "config.xml"))
        radarr_online = bool(radarr_key and radarr_sonarr_sync._is_service_online("http://127.0.0.1:7878", radarr_key))
        radarr_movies = None
        if radarr_online:
            try:
                radarr_movies = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", radarr_key, "/api/v3/movie", timeout=5) or []
            except Exception:
                radarr_movies = []

        sonarr_key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.SONARR_DIR, "config.xml"))
        sonarr_online = bool(sonarr_key and radarr_sonarr_sync._is_service_online("http://127.0.0.1:8989", sonarr_key))
        sonarr_series = None
        if sonarr_online:
            try:
                sonarr_series = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", sonarr_key, "/api/v3/series", timeout=5) or []
            except Exception:
                sonarr_series = []

        for sec in sections:
            sec_type = sec.get("type")
            sec_key = sec.get("key")

            # =========================================================
            # MOVIES (Radarr + Plex + Trakt)
            # =========================================================
            if sec_type == "movie":
                cleaned_any_movie = False
                m_req = urllib.request.Request(f"http://127.0.0.1:32400/library/sections/{sec_key}/all?includeGuids=1", headers=headers)
                with urllib.request.urlopen(m_req, timeout=8) as mresp:
                    items = json.loads(mresp.read().decode("utf-8")).get("MediaContainer", {}).get("Metadata", []) or []

                for item in items:
                    rk = str(item.get("ratingKey", ""))
                    if not rk or rk in active_keys:
                        continue

                    added_at = int(item.get("addedAt") or 0)
                    view_count = int(item.get("viewCount") or 0)
                    last_viewed = int(item.get("lastViewedAt") or 0)
                    last_rated = int(item.get("lastRatedAt") or 0)
                    plex_rating = item.get("userRating")

                    tmdb_id = None
                    imdb_id = None
                    plex_guid = str(item.get("guid") or "").rsplit("/", 1)[-1]
                    for g in item.get("Guid") or []:
                        gid = g.get("id", "")
                        if gid.startswith("tmdb://"):
                            try:
                                tmdb_id = int(gid.replace("tmdb://", ""))
                            except Exception:
                                pass
                        elif gid.startswith("imdb://"):
                            imdb_id = gid.replace("imdb://", "")

                    # In 'keep' mode, if already excluded as watched, skip in O(1) without extra API calls
                    if not delete_files and tmdb_id and radarr_sonarr_sync.is_media_excluded("movie", tmdb_id=tmdb_id, imdb_id=imdb_id):
                        continue

                    trakt_watched_ts = trakt_watched_tmdb.get(tmdb_id) or trakt_watched_imdb.get(imdb_id) or 0
                    trakt_rating_pair = trakt_ratings_tmdb.get(tmdb_id) or trakt_ratings_imdb.get(imdb_id)
                    trakt_rating = trakt_rating_pair[0] if trakt_rating_pair else None
                    trakt_rated_ts = trakt_rating_pair[1] if trakt_rating_pair else 0

                    plex_watched = view_count > 0
                    trakt_watched = trakt_watched_ts > 0

                    plex_eligible = plex_watched and (now_ts - max(last_viewed, last_rated)) >= min_age
                    trakt_watched_eligible = (
                        trakt_watched
                        and trakt_watched_ts >= (added_at - 86400)
                        and (now_ts - trakt_watched_ts) >= min_age
                    )
                    trakt_rated_eligible = (
                        mode == "delete_after_rating"
                        and bool(trakt_rating)
                        and trakt_rated_ts >= (added_at - 86400)
                        and (now_ts - trakt_rated_ts) >= min_age
                    )

                    if not (plex_eligible or trakt_watched_eligible or trakt_rated_eligible):
                        continue

                    if delete_files and trakt_headers and not (tmdb_id or imdb_id):
                        continue

                    ids_obj = {}
                    if tmdb_id:
                        ids_obj["tmdb"] = tmdb_id
                    if imdb_id:
                        ids_obj["imdb"] = imdb_id

                    if mode == "delete_after_rating" and not plex_rating and not trakt_rating:
                        continue

                    # Two-way sync BEFORE Watchlist removal / file deletion
                    if trakt_headers and ids_obj:
                        if plex_watched and not trakt_watched:
                            try:
                                w_iso = datetime.fromtimestamp(last_viewed or now_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                                tr_hist = urllib.request.Request(
                                    "https://api.trakt.tv/sync/history",
                                    data=json.dumps({"movies": [{"watched_at": w_iso, "ids": ids_obj}]}).encode("utf-8"),
                                    headers=trakt_headers,
                                    method="POST",
                                )
                                with urllib.request.urlopen(tr_hist, timeout=8) as h_resp:
                                    if h_resp.status not in (200, 201) and delete_files:
                                        continue
                            except Exception:
                                if delete_files:
                                    continue

                        if plex_rating and not trakt_rating:
                            try:
                                score_int = max(1, min(10, int(round(float(plex_rating)))))
                                tr_rate = urllib.request.Request(
                                    "https://api.trakt.tv/sync/ratings",
                                    data=json.dumps({"movies": [{"rating": score_int, "ids": ids_obj}]}).encode("utf-8"),
                                    headers=trakt_headers,
                                    method="POST",
                                )
                                with urllib.request.urlopen(tr_rate, timeout=8) as r_resp:
                                    if r_resp.status not in (200, 201) and delete_files:
                                        continue
                            except Exception:
                                if delete_files:
                                    continue

                    synced_to_plex_now = False
                    if (trakt_watched or trakt_rating) and not plex_watched:
                        try:
                            sc_req = urllib.request.Request(
                                f"http://127.0.0.1:32400/:/scrobble?identifier=com.plexapp.plugins.library&key={rk}",
                                headers=headers,
                                method="GET",
                            )
                            urllib.request.urlopen(sc_req, timeout=5).close()
                            synced_to_plex_now = True
                        except Exception:
                            if delete_files:
                                continue

                    if trakt_rating and not plex_rating:
                        try:
                            pr_req = urllib.request.Request(
                                f"http://127.0.0.1:32400/:/rate?identifier=com.plexapp.plugins.library&key={rk}&rating={trakt_rating}",
                                headers=headers,
                                method="PUT",
                            )
                            urllib.request.urlopen(pr_req, timeout=5).close()
                            synced_to_plex_now = True
                        except Exception:
                            if delete_files:
                                continue

                    if delete_files and synced_to_plex_now:
                        continue  # Wait 1 cycle before deleting file so Plex persists watched+rating

                    # Always remove watched movie from BOTH Watchlists & add Radarr Import Exclusion
                    if tmdb_id:
                        radarr_sonarr_sync.exclude_and_purge_media(
                            "movie",
                            tmdb_id=tmdb_id,
                            imdb_id=imdb_id,
                            plex_guid=plex_guid,
                            title=item.get("title", ""),
                            year=item.get("year", 0),
                            reason=f"watched_{mode}",
                            cancel_queue=delete_files,
                        )
                    else:
                        radarr_sonarr_sync.remove_from_watchlists(
                            "movie", imdb_id=imdb_id, plex_guid=plex_guid, title=item.get("title", ""), year=item.get("year")
                        )

                    if not delete_files:
                        continue  # 'keep' mode: file stays safely on disk, Watchlist + Exclusion are now clean!

                    # Delete file & unmonitor via Radarr (skip external-disk files if Plex/Radarr is in stub mode)
                    if radarr_online and radarr_movies is not None and tmdb_id:
                        for rm in radarr_movies:
                            if rm.get("tmdbId") == tmdb_id:
                                rm_path = str(rm.get("path") or "")
                                if plex_stub_mode and rm_path.startswith("/movies_external/"):
                                    break
                                mf_id = (rm.get("movieFile") or {}).get("id") or rm.get("movieFileId")
                                if rm.get("monitored"):
                                    rm["monitored"] = False
                                    try:
                                        radarr_sonarr_sync._api_request("http://127.0.0.1:7878", radarr_key, f"/api/v3/movie/{rm['id']}", method="PUT", payload=rm, timeout=6)
                                    except Exception:
                                        pass
                                if mf_id:
                                    try:
                                        radarr_sonarr_sync._api_request("http://127.0.0.1:7878", radarr_key, f"/api/v3/moviefile/{mf_id}", method="DELETE", timeout=8)
                                        cleaned_any_movie = True
                                        print(f"🧹 [WatchedCleanup] Deleted watched movie file, unmonitored & excluded: {item.get('title')}")
                                    except Exception as e:
                                        print(f"⚠️ [WatchedCleanup] Could not delete {item.get('title')}: {e}")
                                break

                if cleaned_any_movie and not plex_stub_mode:
                    try:
                        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:32400/library/sections/{sec_key}/refresh", headers=headers), timeout=4).close()
                        schedule_plex_empty_trash("movies", delay=8)
                    except Exception:
                        pass

            # =========================================================
            # TV SHOWS (Sonarr + Plex + Trakt)
            # =========================================================
            elif sec_type in ("show", "series"):
                cleaned_any_show = False
                s_req = urllib.request.Request(f"http://127.0.0.1:32400/library/sections/{sec_key}/all?includeGuids=1", headers=headers)
                with urllib.request.urlopen(s_req, timeout=8) as sresp:
                    shows = json.loads(sresp.read().decode("utf-8")).get("MediaContainer", {}).get("Metadata", []) or []

                sonarr_by_tvdb = {int(s["tvdbId"]): s for s in (sonarr_series or []) if s.get("tvdbId")}

                for show in shows:
                    show_rk = str(show.get("ratingKey", ""))
                    if not show_rk or show_rk in active_keys:
                        continue

                    tvdb_id = None
                    imdb_id = None
                    plex_guid = str(show.get("guid") or "").rsplit("/", 1)[-1]
                    for g in show.get("Guid") or []:
                        gid = g.get("id", "")
                        if gid.startswith("tvdb://"):
                            try:
                                tvdb_id = int(gid.replace("tvdb://", ""))
                            except Exception:
                                pass
                        elif gid.startswith("imdb://"):
                            imdb_id = gid.replace("imdb://", "")

                    leaf_count = int(show.get("leafCount") or 0)
                    viewed_leaf_count = int(show.get("viewedLeafCount") or 0)
                    all_present_watched = leaf_count > 0 and viewed_leaf_count >= leaf_count

                    s_obj = sonarr_by_tvdb.get(tvdb_id) if tvdb_id else None
                    series_ended = bool(s_obj and str(s_obj.get("status") or "").lower() == "ended")

                    plex_show_rating = show.get("userRating")
                    trakt_s_pair = trakt_show_ratings_tvdb.get(tvdb_id) or trakt_show_ratings_imdb.get(imdb_id)
                    trakt_show_rating = trakt_s_pair[0] if trakt_s_pair else None

                    # 1) Whole-show Watchlist & Exclusion hygiene when completed & ended (or rated in delete_after_rating)
                    if all_present_watched and (series_ended or (mode == "delete_after_rating" and (plex_show_rating or trakt_show_rating))):
                        if tvdb_id and not radarr_sonarr_sync.is_media_excluded("show", tvdb_id=tvdb_id, imdb_id=imdb_id):
                            if series_ended:
                                radarr_sonarr_sync.exclude_and_purge_media(
                                    "show",
                                    tvdb_id=tvdb_id,
                                    imdb_id=imdb_id,
                                    plex_guid=plex_guid,
                                    title=show.get("title", ""),
                                    year=show.get("year", 0),
                                    reason=f"watched_{mode}",
                                    cancel_queue=False,
                                )

                    if not delete_files or not sonarr_online or not s_obj:
                        continue
                    s_path_str = str(s_obj.get("path") or "")
                    if plex_stub_mode and (s_path_str.startswith("/tv_external/") or s_path_str.startswith("/series_external/")):
                        continue

                    if mode == "delete_after_rating" and not (plex_show_rating or trakt_show_rating):
                        continue

                    # 2) Episode-level cleanup in Sonarr for watched episodes exceeding min_age
                    if viewed_leaf_count <= 0:
                        continue
                    try:
                        ep_req = urllib.request.Request(f"http://127.0.0.1:32400/library/metadata/{show_rk}/allLeaves", headers=headers)
                        with urllib.request.urlopen(ep_req, timeout=8) as ep_resp:
                            plex_eps = json.loads(ep_resp.read().decode("utf-8")).get("MediaContainer", {}).get("Metadata", []) or []

                        eligible_se_ep = set()
                        for pep in plex_eps:
                            ep_rk = str(pep.get("ratingKey", ""))
                            if not ep_rk or ep_rk in active_keys:
                                continue
                            if int(pep.get("viewCount") or 0) <= 0:
                                continue
                            lv = int(pep.get("lastViewedAt") or show.get("lastRatedAt") or 0)
                            if lv and (now_ts - lv) < min_age:
                                continue
                            s_num = pep.get("parentIndex")
                            e_num = pep.get("index")
                            if s_num is not None and e_num is not None:
                                eligible_se_ep.add((int(s_num), int(e_num)))

                        if not eligible_se_ep:
                            continue

                        sonarr_eps = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", sonarr_key, f"/api/v3/episode?seriesId={s_obj['id']}", timeout=6) or []
                        unmon_ids = []
                        del_ef_ids = set()
                        for sep in sonarr_eps:
                            key_se = (int(sep.get("seasonNumber") or -1), int(sep.get("episodeNumber") or -1))
                            if key_se in eligible_se_ep:
                                if sep.get("monitored") and sep.get("id"):
                                    unmon_ids.append(int(sep["id"]))
                                if sep.get("episodeFileId"):
                                    del_ef_ids.add(int(sep["episodeFileId"]))

                        if unmon_ids:
                            radarr_sonarr_sync._api_request(
                                "http://127.0.0.1:8989",
                                sonarr_key,
                                "/api/v3/episode/monitor",
                                method="PUT",
                                payload={"episodeIds": unmon_ids, "monitored": False},
                                timeout=6,
                            )
                        for ef_id in del_ef_ids:
                            try:
                                radarr_sonarr_sync._api_request("http://127.0.0.1:8989", sonarr_key, f"/api/v3/episodefile/{ef_id}", method="DELETE", timeout=6)
                                cleaned_any_show = True
                            except Exception:
                                pass
                        if del_ef_ids:
                            print(f"🧹 [WatchedCleanup] Deleted {len(del_ef_ids)} watched episode file(s) & unmonitored in Sonarr: {show.get('title')}")
                    except Exception:
                        pass

                if cleaned_any_show and not plex_stub_mode:
                    try:
                        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:32400/library/sections/{sec_key}/refresh", headers=headers), timeout=4).close()
                        schedule_plex_empty_trash("series", delay=8)
                    except Exception:
                        pass
    except Exception as e:
        print(f"⚠️ [WatchedCleanup] Skipped: {e}")
    finally:
        _watched_cleanup_lock.release()


_music_cleanup_lock = threading.Lock()
_AUDIO_EXTS = {'.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.aac', '.wma', '.alac', '.aiff', '.webm'}
_JUNK_FILES = {'cover.jpg', 'cover.png', 'cover.webp', 'folder.jpg', 'folder.png', 'thumbs.db', '.ds_store', 'desktop.ini'}


def cleanup_music_hygiene(music_dir=None):
    """Removes stale Aurral .tmp import leftovers, orphaned cover-only 'Unknown Album' folders,
    and completely empty folders under media/music (never touches _playlists or audio files)."""
    if not music_dir:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        music_dir = os.path.join(project_root, "media", "music")
    music_dir = os.path.abspath(music_dir)
    if not os.path.isdir(music_dir):
        return {"tmp_removed": 0, "dirs_removed": 0}

    now_ts = time.time()
    tmp_removed = 0
    dirs_removed = 0

    for root, dirs, files in os.walk(music_dir, topdown=False):
        rel = os.path.relpath(root, music_dir)
        if rel == '.' or rel.split(os.sep)[0].lower() == '_playlists':
            continue

        # 1. Remove stale .aurral-import-*.tmp files older than 10 minutes
        for f in list(files):
            if f.startswith('.aurral-import-') and f.endswith('.tmp'):
                fp = os.path.join(root, f)
                try:
                    if now_ts - os.path.getmtime(fp) > 600:
                        os.remove(fp)
                        tmp_removed += 1
                except Exception:
                    pass

        # 2. Check current directory contents
        try:
            remaining = os.listdir(root)
        except Exception:
            continue

        base_lower = os.path.basename(root).lower()
        if not remaining:
            try:
                os.rmdir(root)
                dirs_removed += 1
            except Exception:
                pass
        elif base_lower == 'unknown album':
            # Only delete Unknown Album folder if it contains NO subdirectories and NO audio files
            has_audio_or_subdir = False
            for item in remaining:
                ip = os.path.join(root, item)
                if os.path.isdir(ip):
                    has_audio_or_subdir = True
                    break
                ext = os.path.splitext(item)[1].lower()
                if ext in _AUDIO_EXTS or item.lower() not in _JUNK_FILES:
                    has_audio_or_subdir = True
                    break
            if not has_audio_or_subdir:
                try:
                    for item in remaining:
                        os.remove(os.path.join(root, item))
                    os.rmdir(root)
                    dirs_removed += 1
                except Exception:
                    pass

    if tmp_removed or dirs_removed:
        print(f"🧹 [MusicHygiene] Cleaned {tmp_removed} stale .tmp file(s) and {dirs_removed} empty/orphan folder(s).")
    return {"tmp_removed": tmp_removed, "dirs_removed": dirs_removed}


def _load_music_automation_config():
    """Loads music rating/cleanup preferences & optional user tokens from config.properties."""
    import configparser
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.join(project_root, "config.properties")
    res = {
        "delete_disliked_music": True,
        "sync_music_ratings": True,
        "music_discovery_schedule": "weekly",
        "music_discovery_size": 20,
        "aurral_user_api_key": "",
        "listenbrainz_token": "",
    }
    try:
        cfg = configparser.ConfigParser()
        if os.path.exists(cfg_path):
            cfg.read(cfg_path, encoding="utf-8")
            res["delete_disliked_music"] = cfg.getboolean("automation", "delete_disliked_music", fallback=True)
            res["sync_music_ratings"] = cfg.getboolean("automation", "sync_music_ratings", fallback=True)
            sched = cfg.get("automation", "music_discovery_schedule", fallback="weekly").strip().lower()
            res["music_discovery_schedule"] = sched if sched in ("off", "daily", "weekly", "biweekly", "monthly") else "weekly"
            res["music_discovery_size"] = max(5, min(50, cfg.getint("automation", "music_discovery_size", fallback=20)))
            res["aurral_user_api_key"] = cfg.get("automation", "aurral_user_api_key", fallback="").strip()
            res["listenbrainz_token"] = cfg.get("automation", "listenbrainz_token", fallback="").strip()
    except Exception:
        pass
    return res


def _music_ratings_state_path():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run_dir = os.path.join(project_root, "run")
    os.makedirs(run_dir, exist_ok=True)
    return os.path.join(run_dir, "music_ratings_state.json")


def _load_music_ratings_state():
    import json
    p = _music_ratings_state_path()
    default_state = {"tracks": {}, "artist_feedback": {}, "disliked_artists": {}, "mbid_cache": {}}
    if not os.path.exists(p):
        return default_state
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        for k, v in default_state.items():
            if not isinstance(d.get(k), dict):
                d[k] = dict(v)
        return d
    except Exception:
        return default_state


def _save_music_ratings_state(state):
    import json
    p = _music_ratings_state_path()
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def _read_aurral_music_catalog():
    """Reads read-only lookup maps from aurral.db: general API key, path->track info, artist track counts, exclusions."""
    import sqlite3
    import json
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    adb = os.path.join(project_root, "services", "tools", "aurral", "config", "aurral.db")
    out = {
        "general_api_key": "",
        "by_path": {},            # path -> {track_id, identity_key, mbid, title, artist_name, artist_mbid}
        "artist_counts": {},      # lower(artist_name) -> int available tracks
        "artist_mbids": {},       # lower(artist_name) -> (artist_name, mbid)
        "excluded_artists": {},   # lower(artist_name) -> (artist_name, artist_mbid, count)
    }
    if not os.path.exists(adb):
        return out
    con = None
    try:
        con = sqlite3.connect(f"file:{adb}?mode=ro", uri=True, timeout=2.0)
        cur = con.cursor()
        row = cur.execute("SELECT value FROM settings WHERE key='integrations'").fetchone()
        if row and row[0]:
            integ = json.loads(row[0])
            ak = (integ.get("general") or {}).get("apiKey", "")
            if ak and not str(ak).startswith("AURRAL_ENC:"):
                out["general_api_key"] = str(ak).strip()

        for aname, ambid in cur.execute("SELECT name, mbid FROM library_artists WHERE name IS NOT NULL").fetchall():
            k = str(aname).strip().lower()
            if k:
                out["artist_mbids"][k] = (str(aname).strip(), str(ambid or "").strip())

        for path, tid, ikey, tmbid, ttitle, tartist, aartist, ambid in cur.execute("""
            SELECT mf.path, mf.track_id, lt.identity_key, lt.mbid, lt.title, lt.artist_name, la.name, la.mbid
            FROM library_media_files mf
            LEFT JOIN library_tracks lt ON mf.track_id = lt.id
            LEFT JOIN library_albums alb ON mf.album_id = alb.id
            LEFT JOIN library_artists la ON alb.artist_id = la.id
            WHERE mf.available = 1
        """).fetchall():
            aname = str(aartist or tartist or "").strip()
            akey = aname.lower()
            if akey:
                out["artist_counts"][akey] = out["artist_counts"].get(akey, 0) + 1
                if ambid and akey not in out["artist_mbids"]:
                    out["artist_mbids"][akey] = (aname, str(ambid).strip())
            out["by_path"][path] = {
                "track_id": tid,
                "identity_key": str(ikey or "").strip(),
                "mbid": str(tmbid or "").strip(),
                "title": str(ttitle or "").strip(),
                "artist_name": aname,
                "artist_mbid": str(ambid or "").strip(),
            }

        for aname, ambid in cur.execute(
            "SELECT artist_name, artist_mbid FROM library_monitoring_exclusions WHERE artist_name IS NOT NULL AND artist_name != ''"
        ).fetchall():
            clean_a = str(aname).strip()
            k = clean_a.lower()
            prev = out["excluded_artists"].get(k)
            cnt = (prev[2] + 1) if prev else 1
            best_mbid = str(ambid or "").strip() or (prev[1] if prev else "")
            out["excluded_artists"][k] = (clean_a, best_mbid, cnt)
    except Exception:
        pass
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    return out


def _lookup_listenbrainz_mbid(artist_name, track_title, state):
    """Best-effort public ListenBrainz metadata lookup for tracks without an embedded MBID (cached)."""
    import urllib.parse
    import json
    cache_key = f"{artist_name.strip().lower()}::{track_title.strip().lower()}"
    if cache_key in state["mbid_cache"]:
        return state["mbid_cache"][cache_key]
    mbid = ""
    try:
        qs = urllib.parse.urlencode({"artist_name": artist_name, "recording_name": track_title})
        req = urllib.request.Request(
            f"https://api.listenbrainz.org/1/metadata/lookup/?{qs}",
            headers={"User-Agent": "PodMedia/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            mbid = str(d.get("recording_mbid") or "").strip()
    except Exception:
        mbid = ""
    state["mbid_cache"][cache_key] = mbid
    return mbid


def _send_listenbrainz_feedback(token, recording_mbid, score):
    """Sends official ListenBrainz recording feedback: score in {1 (Love), 0 (Neutral), -1 (Hate)}."""
    import json
    if not token or not recording_mbid:
        return False
    try:
        payload = json.dumps({"recording_mbid": recording_mbid, "score": int(score)}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.listenbrainz.org/1/feedback/recording-feedback",
            data=payload,
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
                "User-Agent": "PodMedia/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f"⚠️ [MusicRatings] ListenBrainz feedback failed ({recording_mbid}, score={score}): {e}")
        return False


def _set_aurral_favorite(user_api_key, song_identity_key, starred):
    """Stars or unstars a track in Aurral Favorites (natural 1.4x recommendation weight) as the user."""
    import json
    import urllib.parse
    if not user_api_key or not song_identity_key:
        return False
    try:
        song_id = "song:" + urllib.parse.quote(song_identity_key, safe="")
        payload = json.dumps({"ids": [song_id], "starred": bool(starred)}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:3001/api/library/favorites",
            data=payload,
            headers={"X-Api-Key": user_api_key, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        print(f"⚠️ [MusicRatings] Aurral favorite ({starred}) failed for {song_identity_key}: {e}")
        return False


def _add_aurral_artist_less_like_this(user_api_key, artist_name, artist_mbid=""):
    """Sends surgical artist-only 'less_like_this' (empty tagContext so genres stay natural & undistorted)."""
    import json
    if not user_api_key or not artist_name:
        return None
    try:
        body = {
            "action": "less_like_this",
            "artistName": artist_name,
            "sourceContext": "podmedia_plexamp",
            "tagContext": [],
            "seedContext": [],
        }
        if artist_mbid:
            body["artistId"] = artist_mbid
        req = urllib.request.Request(
            "http://127.0.0.1:3001/api/discovery/feedback",
            data=json.dumps(body).encode("utf-8"),
            headers={"X-Api-Key": user_api_key, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            fb = data.get("feedback") or {}
            return fb.get("id") or "synced"
    except Exception as e:
        print(f"⚠️ [MusicRatings] Aurral less_like_this failed for {artist_name}: {e}")
        return None


def _remove_aurral_discovery_feedback(user_api_key, feedback_id):
    """Removes a previously added Aurral discovery feedback entry when the user later likes that artist."""
    if not user_api_key or not feedback_id or feedback_id == "synced":
        return False
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:3001/api/discovery/feedback/{feedback_id}",
            headers={"X-Api-Key": user_api_key, "Accept": "application/json"},
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


def _is_aurral_online():
    try:
        req = urllib.request.Request("http://127.0.0.1:3001/api/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status < 500
    except Exception:
        try:
            req = urllib.request.Request("http://127.0.0.1:3001/")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status < 500
        except Exception:
            return False


def sync_music_ratings_and_recommendations(
    delete_disliked=None,
    sync_ratings=None,
    delete_below_rating=4.0,
    music_dir=None,
    force_empty_trash=False,
):
    """Natural, algorithm-friendly Plexamp -> Aurral + ListenBrainz rating & recommendation engine:
    - >= 4★ (8.0-10.0): ListenBrainz Love (+1) + Aurral Favorites star (natural 1.4x boost); clears prior artist less_like_this.
    - 2★ - 3.5★ (4.0-7.0): Kept on disk; ListenBrainz Neutral (0) if previously non-zero; unstarred in Aurral.
    - < 2★ (0.5★, 1★, 1.5★ / userRating < 4.0): ListenBrainz Hate (-1), deleted via Aurral API (+monitoring exclusion),
      Plex Music trash purged, and artist-only 'less_like_this' (tagContext=[], preserving genres!) ONLY if the artist
      has no remaining library tracks and no loved tracks.
    """
    if not _music_cleanup_lock.acquire(blocking=False):
        return {"status": "busy", "deleted": [], "lb_synced": 0, "aurral_starred": 0, "artist_feedback": 0}

    deleted_items = []
    lb_synced = 0
    aurral_fav_synced = 0
    artist_fb_synced = 0
    skipped_aurral_offline = 0

    try:
        import json
        cfg = _load_music_automation_config()
        if delete_disliked is None:
            delete_disliked = cfg["delete_disliked_music"]
        if sync_ratings is None:
            sync_ratings = cfg["sync_music_ratings"]
        user_api_key = cfg["aurral_user_api_key"]
        lb_token = cfg["listenbrainz_token"]

        plex_token = _get_plex_token()
        if not plex_token:
            return {"status": "no_plex_token", "deleted": []}
        headers = {"X-Plex-Token": plex_token, "Accept": "application/json"}

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not music_dir:
            music_dir = os.path.join(project_root, "media", "music")
        music_dir = os.path.abspath(music_dir)

        # 1. Active Plex sessions (never delete a track currently playing)
        active_keys = set()
        try:
            s_req = urllib.request.Request("http://127.0.0.1:32400/status/sessions", headers=headers)
            with urllib.request.urlopen(s_req, timeout=4) as sresp:
                sdata = json.loads(sresp.read().decode("utf-8"))
                for m in sdata.get("MediaContainer", {}).get("Metadata", []) or []:
                    if m.get("ratingKey"):
                        active_keys.add(str(m["ratingKey"]))
        except Exception:
            return {"status": "plex_offline", "deleted": []}

        music_sec = _get_plex_section_id("music")
        if not music_sec:
            return {"status": "no_music_section", "deleted": []}

        t_req = urllib.request.Request(
            f"http://127.0.0.1:32400/library/sections/{music_sec}/all?type=10",
            headers=headers,
        )
        with urllib.request.urlopen(t_req, timeout=10) as tresp:
            tracks = json.loads(tresp.read().decode("utf-8")).get("MediaContainer", {}).get("Metadata", []) or []

        catalog = _read_aurral_music_catalog()
        state = _load_music_ratings_state()
        state_dirty = False
        aurral_online = None
        delete_api_key = user_api_key or catalog["general_api_key"]

        # Track per-artist loved counts from current Plex library
        artist_loved_counts = {}

        for item in tracks:
            rk = str(item.get("ratingKey", ""))
            if not rk:
                continue
            rating = item.get("userRating")
            if rating is None:
                continue
            try:
                rating_val = float(rating)
            except Exception:
                continue
            if rating_val <= 0.0:
                continue

            part_files = []
            for media_obj in item.get("Media") or []:
                for part in media_obj.get("Part") or []:
                    fp = part.get("file")
                    if fp and fp.startswith("/media/music/"):
                        part_files.append(fp)
            if not part_files:
                continue

            artist_name = str(item.get("grandparentTitle") or item.get("originalTitle") or "").strip()
            track_title = str(item.get("title") or "").strip()
            label = f"{artist_name} - {track_title}".strip(" -")
            artist_key = artist_name.lower()

            # Classify rating naturally
            is_loved = rating_val >= 8.0                      # >= 4★ (4★, 4.5★, 5★)
            is_disliked = rating_val < float(delete_below_rating)  # < 2★ (0.5★, 1★, 1.5★)
            desired_lb = 1 if is_loved else (-1 if is_disliked else 0)
            desired_star = True if is_loved else False

            if is_loved and artist_key:
                artist_loved_counts[artist_key] = artist_loved_counts.get(artist_key, 0) + 1

            for container_path in part_files:
                rel_path = container_path[len("/media/music/"):].replace("/", os.sep)
                host_path = os.path.abspath(os.path.join(music_dir, rel_path))
                if not host_path.startswith(music_dir + os.sep):
                    continue

                aurral_meta = catalog["by_path"].get(container_path) or {}
                aurral_track_id = aurral_meta.get("track_id")
                song_ikey = aurral_meta.get("identity_key", "")
                mbid = aurral_meta.get("mbid", "")
                eff_artist = aurral_meta.get("artist_name") or artist_name
                eff_artist_mbid = aurral_meta.get("artist_mbid") or ""
                eff_artist_key = eff_artist.lower()

                prev = state["tracks"].get(container_path) or {}

                # A) ListenBrainz Recording Feedback (+1 Love, 0 Neutral if previously non-zero, -1 Hate)
                if sync_ratings and lb_token:
                    prev_lb = prev.get("lb_score")
                    should_send_lb = (desired_lb != 0 and prev_lb != desired_lb) or (desired_lb == 0 and prev_lb in (1, -1))
                    if should_send_lb:
                        if not mbid and eff_artist and track_title:
                            mbid = _lookup_listenbrainz_mbid(eff_artist, track_title, state)
                            state_dirty = True
                        if mbid and _send_listenbrainz_feedback(lb_token, mbid, desired_lb):
                            prev["lb_score"] = desired_lb
                            prev["mbid"] = mbid
                            lb_synced += 1
                            state_dirty = True
                            print(f"🎵 [MusicRatings] ListenBrainz feedback ({desired_lb:+d}): {label}")

                # B) Aurral Favorites Star (natural 1.4x weight for >= 4★; unstar if downgraded)
                if sync_ratings and user_api_key and song_ikey and not is_disliked:
                    prev_star = prev.get("aurral_starred")
                    should_send_star = (desired_star and prev_star is not True) or (not desired_star and prev_star is True)
                    if should_send_star:
                        if aurral_online is None:
                            aurral_online = _is_aurral_online()
                        if aurral_online and _set_aurral_favorite(user_api_key, song_ikey, desired_star):
                            prev["aurral_starred"] = desired_star
                            aurral_fav_synced += 1
                            state_dirty = True
                            print(f"⭐ [MusicRatings] Aurral favorite ({'starred' if desired_star else 'unstarred'}): {label}")

                # Persist rating snapshot for kept tracks
                if not is_disliked:
                    if prev.get("rating") != rating_val:
                        prev["rating"] = rating_val
                        prev["artist_name"] = eff_artist
                        prev["track_title"] = track_title
                        state["tracks"][container_path] = prev
                        state_dirty = True
                    continue

                # C) Disliked (< 2★): unstar if starred, then delete & exclude if enabled and not playing
                if rk in active_keys:
                    continue

                if user_api_key and song_ikey and prev.get("aurral_starred") is True:
                    if aurral_online is None:
                        aurral_online = _is_aurral_online()
                    if aurral_online:
                        _set_aurral_favorite(user_api_key, song_ikey, False)

                if not delete_disliked:
                    prev["rating"] = rating_val
                    state["tracks"][container_path] = prev
                    state_dirty = True
                    continue

                deleted_ok = False
                if aurral_track_id is not None and delete_api_key:
                    if aurral_online is None:
                        aurral_online = _is_aurral_online()
                    if not aurral_online:
                        skipped_aurral_offline += 1
                        continue
                    try:
                        del_req = urllib.request.Request(
                            f"http://127.0.0.1:3001/api/library/tracks/{aurral_track_id}",
                            headers={"X-Api-Key": delete_api_key, "Accept": "application/json"},
                            method="DELETE",
                        )
                        with urllib.request.urlopen(del_req, timeout=15) as dresp:
                            if dresp.status in (200, 204):
                                deleted_ok = True
                                print(f"👎 [MusicCleanup] Deleted <2★ track via Aurral (+excluded, rating={rating_val}): {label}")
                    except Exception as e:
                        print(f"⚠️ [MusicCleanup] Aurral delete failed for {label} (id={aurral_track_id}): {e}")
                elif os.path.exists(host_path):
                    try:
                        os.remove(host_path)
                        deleted_ok = True
                        print(f"👎 [MusicCleanup] Deleted <2★ untracked file (rating={rating_val}): {label}")
                    except Exception as e:
                        print(f"⚠️ [MusicCleanup] File delete failed for {label}: {e}")

                if deleted_ok:
                    deleted_items.append({"label": label, "rating": rating_val, "path": container_path})
                    state["tracks"].pop(container_path, None)
                    if eff_artist_key:
                        if eff_artist_key in catalog["artist_counts"]:
                            catalog["artist_counts"][eff_artist_key] = max(0, catalog["artist_counts"][eff_artist_key] - 1)
                        d_entry = state["disliked_artists"].get(eff_artist_key) or {
                            "artist_name": eff_artist,
                            "artist_mbid": eff_artist_mbid,
                            "count": 0,
                        }
                        d_entry["count"] = int(d_entry.get("count", 0)) + 1
                        if eff_artist_mbid:
                            d_entry["artist_mbid"] = eff_artist_mbid
                        state["disliked_artists"][eff_artist_key] = d_entry
                    state_dirty = True

        # D) Surgical artist-only Aurral Discovery 'less_like_this' (tagContext=[] so genres are never distorted!)
        #    - Applied ONLY when an artist has disliked/excluded track(s), 0 remaining library tracks, and 0 loved tracks.
        #    - Automatically removed if the user later loves (>= 4★) any track by that artist.
        if sync_ratings and user_api_key:
            # Merge already-excluded artists (e.g. John Coltrane) with state['disliked_artists']
            candidate_disliked = dict(state["disliked_artists"])
            for akey, (aname, ambid, cnt) in catalog["excluded_artists"].items():
                if akey not in candidate_disliked:
                    candidate_disliked[akey] = {"artist_name": aname, "artist_mbid": ambid, "count": cnt}

            all_artist_keys = set(candidate_disliked.keys()) | set(state["artist_feedback"].keys()) | set(artist_loved_counts.keys())
            for akey in all_artist_keys:
                loved_n = artist_loved_counts.get(akey, 0)
                remaining_n = catalog["artist_counts"].get(akey, 0)
                is_disliked_artist = akey in candidate_disliked
                existing_fb = state["artist_feedback"].get(akey)

                want_less = bool(is_disliked_artist and loved_n == 0 and remaining_n == 0)
                if want_less and not existing_fb:
                    if aurral_online is None:
                        aurral_online = _is_aurral_online()
                    if aurral_online:
                        info = candidate_disliked[akey]
                        aname = info.get("artist_name") or catalog["artist_mbids"].get(akey, (akey, ""))[0]
                        ambid = info.get("artist_mbid") or catalog["artist_mbids"].get(akey, ("", ""))[1]
                        fb_id = _add_aurral_artist_less_like_this(user_api_key, aname, ambid)
                        if fb_id:
                            state["artist_feedback"][akey] = {"action": "less_like_this", "feedback_id": fb_id, "artist_name": aname}
                            artist_fb_synced += 1
                            state_dirty = True
                            print(f"🎯 [MusicRatings] Aurral discovery 'less_like_this' (artist-only): {aname}")
                elif not want_less and existing_fb and loved_n > 0:
                    if aurral_online is None:
                        aurral_online = _is_aurral_online()
                    if aurral_online:
                        _remove_aurral_discovery_feedback(user_api_key, existing_fb.get("feedback_id"))
                        state["artist_feedback"].pop(akey, None)
                        artist_fb_synced += 1
                        state_dirty = True
                        print(f"🎯 [MusicRatings] Cleared Aurral 'less_like_this' after >=4★ rating: {existing_fb.get('artist_name', akey)}")

        if state_dirty:
            _save_music_ratings_state(state)

        hygiene = cleanup_music_hygiene(music_dir)
        if deleted_items or force_empty_trash or hygiene["dirs_removed"] or hygiene["tmp_removed"]:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"http://127.0.0.1:32400/library/sections/{music_sec}/refresh", headers=_plex_headers()),
                    timeout=5,
                ).close()
            except Exception:
                pass
            schedule_plex_empty_trash("music", delay=6)

        return {
            "status": "ok",
            "deleted": deleted_items,
            "lb_synced": lb_synced,
            "aurral_favorites_synced": aurral_fav_synced,
            "artist_feedback_synced": artist_fb_synced,
            "skipped_aurral_offline": skipped_aurral_offline,
            "has_listenbrainz_token": bool(lb_token),
            "has_aurral_user_key": bool(user_api_key),
            "hygiene": hygiene,
        }
    except Exception as e:
        print(f"⚠️ [MusicRatings] Skipped: {e}")
        return {"status": "error", "error": str(e), "deleted": deleted_items}
    finally:
        _music_cleanup_lock.release()


def cleanup_disliked_music_tracks(max_rating=3.9, music_dir=None, force_empty_trash=False):
    """Backward-compatible wrapper invoking the unified music rating sync & <2★ cleanup engine."""
    return sync_music_ratings_and_recommendations(
        delete_below_rating=float(max_rating) + 0.1 if float(max_rating) < 4.0 else float(max_rating),
        music_dir=music_dir,
        force_empty_trash=force_empty_trash,
    )


_music_discovery_lock = threading.Lock()
_DISCOVERY_INTERVALS = {
    "daily": 86400,
    "weekly": 7 * 86400,
    "biweekly": 14 * 86400,
    "monthly": 30 * 86400,
}


def run_scheduled_music_discovery(force=False):
    """Automatically refreshes & downloads Aurral recommendation Flows and auto-sync playlists
    on a configurable schedule (default: weekly), catching up cleanly even when Music Mode is not 24/7."""
    if not _music_discovery_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        import json
        cfg = _load_music_automation_config()
        schedule = cfg["music_discovery_schedule"]
        size = cfg["music_discovery_size"]
        if not force and schedule == "off":
            return {"status": "disabled"}

        interval_sec = _DISCOVERY_INTERVALS.get(schedule, 7 * 86400)
        state = _load_music_ratings_state()
        last_ts = float(state.get("last_discovery_run_ts") or 0)
        now_ts = time.time()

        if not force and last_ts > 0 and (now_ts - last_ts) < interval_sec:
            return {"status": "not_due", "next_in_hours": round((interval_sec - (now_ts - last_ts)) / 3600, 1)}

        if not _is_aurral_online():
            return {"status": "aurral_offline"}

        catalog = _read_aurral_music_catalog()
        user_key = cfg["aurral_user_api_key"]
        api_key = user_key or catalog["general_api_key"]
        if not api_key:
            return {"status": "no_api_key"}

        headers = {"X-Api-Key": api_key, "Content-Type": "application/json", "Accept": "application/json"}

        # 1. Inspect existing Aurral Flows & static playlists
        st_req = urllib.request.Request("http://127.0.0.1:3001/api/playlists/status", headers=headers)
        with urllib.request.urlopen(st_req, timeout=10) as sresp:
            st_data = json.loads(sresp.read().decode("utf-8"))

        flows = st_data.get("flows") or []
        static_pls = st_data.get("staticPlaylists") or []
        triggered_flows = []
        created_flow = None
        synced_playlists = []
        note = ""

        # 2. Trigger existing enabled Flows; if none exist and user API key is set, create Discover Weekly Flow
        enabled_flows = [f for f in flows if f.get("enabled") is not False]
        if not flows and user_key:
            try:
                c_body = json.dumps({
                    "templateId": "discover-weekly",
                    "name": "Discover Weekly",
                    "autoDownload": True,
                    "size": size,
                }).encode("utf-8")
                c_req = urllib.request.Request("http://127.0.0.1:3001/api/playlists/flows", data=c_body, headers=headers, method="POST")
                with urllib.request.urlopen(c_req, timeout=15) as cresp:
                    cdata = json.loads(cresp.read().decode("utf-8"))
                    flow_obj = cdata.get("flow") or {}
                    if flow_obj.get("id"):
                        created_flow = flow_obj.get("name") or "Discover Weekly"
                        enabled_flows = [flow_obj]
                        # Ensure enabled & target size applied
                        try:
                            en_req = urllib.request.Request(
                                f"http://127.0.0.1:3001/api/playlists/flows/{flow_obj['id']}/enabled",
                                data=json.dumps({"enabled": True}).encode("utf-8"),
                                headers=headers,
                                method="PUT",
                            )
                            urllib.request.urlopen(en_req, timeout=8).close()
                        except Exception:
                            pass
            except Exception as e:
                print(f"⚠️ [MusicDiscovery] Could not create Discover Weekly flow: {e}")
        elif not flows and not user_key:
            note = "Discover Weekly Flow oluşturmak için Aurral Kullanıcı API Anahtarı girin (şimdilik mevcut playlistler eşitlendi)."

        for fl in enabled_flows:
            fid = fl.get("id")
            if not fid:
                continue
            try:
                # Ensure autoDownload & updated size are respected
                if fl.get("autoDownload") is False or int(fl.get("size") or 0) != size:
                    try:
                        u_req = urllib.request.Request(
                            f"http://127.0.0.1:3001/api/playlists/flows/{fid}",
                            data=json.dumps({"autoDownload": True, "size": size}).encode("utf-8"),
                            headers=headers,
                            method="PUT",
                        )
                        urllib.request.urlopen(u_req, timeout=8).close()
                    except Exception:
                        pass
                run_req = urllib.request.Request(
                    f"http://127.0.0.1:3001/api/playlists/start/{fid}",
                    data=json.dumps({"limit": size}).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(run_req, timeout=15) as rresp:
                    if rresp.status in (200, 202):
                        triggered_flows.append(fl.get("name") or fid)
                        print(f"✨ [MusicDiscovery] Started scheduled flow download ({size} tracks): {fl.get('name') or fid}")
            except Exception as e:
                print(f"⚠️ [MusicDiscovery] Flow start failed for {fid}: {e}")

        # 3. Also refresh any sync-enabled static playlists (e.g. Last.fm Mix)
        for pl in static_pls:
            pid = pl.get("id")
            imp = pl.get("importSource") or {}
            if pid and imp.get("syncEnabled"):
                try:
                    p_req = urllib.request.Request(
                        f"http://127.0.0.1:3001/api/playlists/{pid}/sync",
                        data=b"{}",
                        headers=headers,
                        method="POST",
                    )
                    with urllib.request.urlopen(p_req, timeout=15) as presp:
                        if presp.status in (200, 202):
                            synced_playlists.append(pl.get("name") or pid)
                            print(f"✨ [MusicDiscovery] Synced static playlist: {pl.get('name') or pid}")
                except Exception as e:
                    print(f"⚠️ [MusicDiscovery] Static playlist sync failed for {pid}: {e}")

        if triggered_flows or synced_playlists:
            state["last_discovery_run_ts"] = now_ts
            state["last_discovery_summary"] = {
                "ts": int(now_ts),
                "schedule": schedule,
                "flows": triggered_flows,
                "playlists": synced_playlists,
            }
            _save_music_ratings_state(state)

        return {
            "status": "ok",
            "schedule": schedule,
            "size": size,
            "created_flow": created_flow,
            "triggered_flows": triggered_flows,
            "synced_playlists": synced_playlists,
            "note": note,
        }
    except Exception as e:
        print(f"⚠️ [MusicDiscovery] Skipped: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        _music_discovery_lock.release()


watcher_instance = None

def start_media_watcher(media_dir):
    global watcher_instance
    if watcher_instance is None or not watcher_instance.is_alive():
        watcher_instance = MediaWatcher(media_dir)
        watcher_instance.start()
        print(f"[MediaWatcher] Started copy-completion background watcher for {media_dir} (Movies, Series, Music).")

def stop_media_watcher():
    global watcher_instance
    if watcher_instance and watcher_instance.is_alive():
        watcher_instance.stop()
