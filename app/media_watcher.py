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
        self.import_list_sync_interval = self._load_sync_interval()
        self.watched_media_action = self._load_watched_action()
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

    def are_containers_running(self):
        """Quickly checks if any PodMedia containers are currently running."""
        try:
            res = subprocess.run(['podman', 'ps', '-q', '--filter', 'name=media_center_'], capture_output=True, text=True, timeout=3, **subproc_kwargs())
            return res.returncode == 0 and bool(res.stdout.strip())
        except Exception:
            return False

    def get_fast_signature(self, path):
        """Lightweight non-recursive signature based on root and immediate child directory mtimes.
        Takes < 1ms and avoids keeping mechanical external disks spinning with constant recursive walks."""
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
            self.fast_signatures[cat] = self.get_fast_signature(path)
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

            # Periodic ImportListSync & optional watched-media cleanup
            now_ts = time.time()
            if now_ts - self.last_import_list_sync >= self.import_list_sync_interval:
                self.last_import_list_sync = now_ts
                threading.Thread(target=sync_trakt_and_import_lists, daemon=True).start()
                if self.watched_media_action in ("delete_after_rating", "delete_after_24h", "delete_immediate"):
                    threading.Thread(target=cleanup_watched_media, args=(self.watched_media_action,), daemon=True).start()

            # Dynamically update external disk paths (only when external-disk containers are active)
            try:
                import podman_manager
                c_states, _ = podman_manager.get_container_states()
                ext_svcs_active = any(c_states.get(c, False) for c in podman_manager.EXTERNAL_DISK_CONTAINERS)
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

                # Ultra-fast non-recursive signature check (< 1ms, zero deep disk traversal)
                current_fast_sig = self.get_fast_signature(path)
                last_fast_sig = self.fast_signatures.get(cat)

                if current_fast_sig == last_fast_sig and current_fast_sig is not None:
                    # Nothing changed at all; skip without touching deeper files!
                    continue

                self.fast_signatures[cat] = current_fast_sig
                current_snapshot = self.get_folder_snapshot(path)
                last_snapshot = self.snapshots.get(cat, (0, 0, 0))
                
                if current_snapshot != last_snapshot:
                    settled_snapshot = self.wait_for_copy_completion(cat, path)
                    self.snapshots[cat] = settled_snapshot
                    self.fast_signatures[cat] = self.get_fast_signature(path)
                    plex_cat = cat.replace('_ext', '')
                    
                    # Refresh Plex library and clean up trash so deleted media disappears instantly
                    self.trigger_plex_refresh(plex_cat)
                    self.trigger_plex_empty_trash(plex_cat)

                    # Synchronize Radarr and Sonarr so deleted items are unmonitored and never re-downloaded
                    try:
                        import radarr_sonarr_sync
                        radarr_sonarr_sync.sync_all()
                    except Exception as e:
                        print("Sync error during watcher event:", e)

    def stop(self):
        self.running = False

def trigger_plex_full_scan_on_startup():
    """Waits asynchronously for Plex Web server startup, then triggers a full scan of all libraries."""
    def _worker():
        for _ in range(15):
            time.sleep(2)
            try:
                url = "http://localhost:32400/library/sections/all/refresh"
                req = urllib.request.Request(url, headers=_plex_headers())
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        print("🚀 [Plex Startup] Triggered full library scan for all sections on Plex startup.")
                        return
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

_trakt_sync_lock = threading.Lock()

def _push_item_to_radarr_or_sonarr(kind, obj):
    """Pushes a Trakt Watchlist movie/show directly to Radarr/Sonarr if online and not already present/excluded."""
    import radarr_sonarr_sync
    ids = obj.get("ids") or {}

    if kind == "movie" and ids.get("tmdb"):
        tmdb_id = int(ids["tmdb"])
        api_key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.RADARR_DIR, "config.xml"))
        if not api_key or not radarr_sonarr_sync._is_service_online("http://127.0.0.1:7878", api_key):
            return
        try:
            existing = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, f"/api/v3/movie?tmdbId={tmdb_id}")
            if existing:
                return
            exclusions = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", api_key, "/api/v3/exclusions") or []
            if any(e.get("tmdbId") == tmdb_id for e in exclusions):
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
            print(f"🎬 [Trakt->Radarr] Added & searching: {obj.get('title')} ({obj.get('year')})")
        except Exception as e:
            print(f"⚠️ [Trakt->Radarr] Could not add {obj.get('title')}: {e}")

    elif kind == "show" and ids.get("tvdb"):
        tvdb_id = int(ids["tvdb"])
        api_key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.SONARR_DIR, "config.xml"))
        if not api_key or not radarr_sonarr_sync._is_service_online("http://127.0.0.1:8989", api_key):
            return
        try:
            existing = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", api_key, f"/api/v3/series?tvdbId={tvdb_id}")
            if existing:
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
            print(f"📺 [Trakt->Sonarr] Added & searching: {obj.get('title')} ({obj.get('year')})")
        except Exception as e:
            print(f"⚠️ [Trakt->Sonarr] Could not add {obj.get('title')}: {e}")

def sync_trakt_watchlist():
    """Directly syncs Trakt Watchlist -> Plex Watchlist AND pushes new items straight to Radarr/Sonarr.
    Fast (~0.5s), works even when Plex container is stopped, and bypasses Radarr/Sonarr 6h/12h refresh limits."""
    import json
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pytrakt_path = os.path.join(project_root, "services", "tools", "plextraktsync", "config", ".pytrakt.json")
    plex_token = _get_plex_token()
    if not (os.path.exists(pytrakt_path) and plex_token):
        return False
    if not _trakt_sync_lock.acquire(blocking=False):
        return False
    try:
        with open(pytrakt_path, "r", encoding="utf-8") as f:
            trakt_cfg = json.load(f)
        client_id = trakt_cfg.get("CLIENT_ID")
        oauth_token = trakt_cfg.get("OAUTH_TOKEN")
        if not (client_id and oauth_token):
            return False

        # 1. Fetch fresh Trakt Watchlist (no local HTTP cache, no username-slug bug)
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
            trakt_wl = json.loads(resp.read().decode("utf-8"))

        # 2. Fetch full Plex Watchlist ratingKeys/guids using pagination headers
        plex_guids = set()
        start = 0
        page_size = 50
        while start < 500:
            plex_req = urllib.request.Request(
                "https://discover.provider.plex.tv/library/sections/watchlist/all",
                headers={
                    "X-Plex-Token": plex_token,
                    "Accept": "application/json",
                    "X-Plex-Client-Identifier": "podmedia-sync",
                    "X-Plex-Container-Start": str(start),
                    "X-Plex-Container-Size": str(page_size),
                    "User-Agent": "PodMedia/1.0",
                },
            )
            with urllib.request.urlopen(plex_req, timeout=10) as resp:
                plex_data = json.loads(resp.read().decode("utf-8"))
            mc = plex_data.get("MediaContainer", {})
            plex_meta = mc.get("Metadata", []) or []
            for m in plex_meta:
                if m.get("ratingKey"):
                    plex_guids.add(str(m["ratingKey"]))
                if m.get("guid"):
                    plex_guids.add(str(m["guid"]).rsplit("/", 1)[-1])
            total_size = int(mc.get("totalSize", len(plex_meta)))
            start += len(plex_meta)
            if not plex_meta or start >= total_size:
                break

        # 3. Add any missing Trakt Watchlist item directly to Plex Watchlist & Radarr/Sonarr
        for item in trakt_wl:
            kind = item.get("type")
            obj = item.get(kind) or {}
            pg = (obj.get("ids") or {}).get("plex", {}).get("guid") if isinstance((obj.get("ids") or {}).get("plex"), dict) else None
            if pg and pg not in plex_guids:
                put_req = urllib.request.Request(
                    f"https://discover.provider.plex.tv/actions/addToWatchlist?ratingKey={pg}",
                    headers={"X-Plex-Token": plex_token, "X-Plex-Client-Identifier": "podmedia-sync", "User-Agent": "PodMedia/1.0"},
                    method="PUT",
                )
                try:
                    with urllib.request.urlopen(put_req, timeout=10) as pr:
                        if pr.status in (200, 204):
                            plex_guids.add(pg)
                            print(f"🔄 [Trakt->Plex] Added to Plex Watchlist: {obj.get('title')} ({obj.get('year')})")
                except Exception as e:
                    print(f"⚠️ [Trakt->Plex] Failed to add {obj.get('title')}: {e}")
            _push_item_to_radarr_or_sonarr(kind, obj)
        return True
    except Exception as e:
        print(f"⚠️ [Trakt->Plex] Watchlist sync skipped: {e}")
    finally:
        _trakt_sync_lock.release()
    return False

def sync_trakt_and_import_lists(target='both'):
    """Syncs Trakt -> Plex Watchlist & Radarr/Sonarr (if configured), then triggers ImportListSync."""
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
    """Waits asynchronously for Radarr/Sonarr API startup, then syncs Trakt Watchlist & ImportLists immediately."""
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
    """Safely removes watched movies from Watchlists (Plex & Trakt) and disk via Radarr.
    - 'delete_after_rating': detects ratings from EITHER Plex or Trakt (even if watched/rated
      solely on Trakt after adding to library), ensures both watched state and rating are
      synced to Plex & Trakt first, then deletes safely.
    - 'delete_after_24h': deletes 24 hours after watching (Plex or Trakt).
    - 'delete_immediate': deletes ~10 minutes after watching (Plex or Trakt).
    Always skips anything currently being streamed in Plex to prevent file handle locks."""
    if mode not in ("delete_after_rating", "delete_after_24h", "delete_immediate"):
        return
    if not _watched_cleanup_lock.acquire(blocking=False):
        return
    try:
        import json
        from datetime import datetime, timezone
        import radarr_sonarr_sync
        plex_token = _get_plex_token()
        if not plex_token:
            return
        headers = {"X-Plex-Token": plex_token, "Accept": "application/json"}
        min_age = 86400 if mode == "delete_after_24h" else (60 if mode == "delete_after_rating" else 600)
        now_ts = time.time()

        # 1. Check active Plex streaming sessions so we NEVER touch a file currently being played
        active_keys = set()
        try:
            sreq = urllib.request.Request("http://127.0.0.1:32400/status/sessions", headers=headers)
            with urllib.request.urlopen(sreq, timeout=4) as sresp:
                sdata = json.loads(sresp.read().decode("utf-8"))
                for m in sdata.get("MediaContainer", {}).get("Metadata", []) or []:
                    if m.get("ratingKey"):
                        active_keys.add(str(m["ratingKey"]))
        except Exception:
            return  # Plex offline -> skip safely

        # Load Trakt credentials if available
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pytrakt_path = os.path.join(project_root, "services", "tools", "plextraktsync", "config", ".pytrakt.json")
        trakt_creds = None
        if os.path.exists(pytrakt_path):
            try:
                with open(pytrakt_path, "r", encoding="utf-8") as f:
                    trakt_creds = json.load(f)
            except Exception:
                pass

        trakt_headers = None
        if trakt_creds and trakt_creds.get("CLIENT_ID") and trakt_creds.get("OAUTH_TOKEN"):
            trakt_headers = {
                "Content-Type": "application/json",
                "trakt-api-version": "2",
                "trakt-api-key": trakt_creds["CLIENT_ID"],
                "Authorization": f"Bearer {trakt_creds['OAUTH_TOKEN']}",
                "User-Agent": "PodMedia/1.0",
            }

        # Fetch Trakt watched movies & movie ratings once so we can detect Trakt-originated watches/ratings
        # and guarantee two-way sync before deleting anything.
        trakt_watched_tmdb = {}   # tmdb_id -> last_watched_ts
        trakt_watched_imdb = {}   # imdb_id -> last_watched_ts
        trakt_ratings_tmdb = {}   # tmdb_id -> (rating_int, rated_at_ts)
        trakt_ratings_imdb = {}   # imdb_id -> (rating_int, rated_at_ts)
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
            except Exception:
                return  # Trakt is configured but unreachable right now -> wait safely instead of risking unsynced data

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

        cleaned_any_movie = False
        for sec in sections:
            if sec.get("type") != "movie":
                continue
            sec_key = sec.get("key")
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

                trakt_watched_ts = trakt_watched_tmdb.get(tmdb_id) or trakt_watched_imdb.get(imdb_id) or 0
                trakt_rating_pair = trakt_ratings_tmdb.get(tmdb_id) or trakt_ratings_imdb.get(imdb_id)
                trakt_rating = trakt_rating_pair[0] if trakt_rating_pair else None
                trakt_rated_ts = trakt_rating_pair[1] if trakt_rating_pair else 0

                plex_watched = view_count > 0
                trakt_watched = trakt_watched_ts > 0

                # Determine whether this library copy qualifies as watched/rated (from Plex OR Trakt),
                # while protecting re-downloaded old favorites whose Trakt activity predates addedAt.
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

                if trakt_headers and not (tmdb_id or imdb_id):
                    continue  # Unmatched item while Trakt is active — keep safely

                ids_obj = {}
                if tmdb_id:
                    ids_obj["tmdb"] = tmdb_id
                if imdb_id:
                    ids_obj["imdb"] = imdb_id

                # Rating gate for 'delete_after_rating' mode
                if mode == "delete_after_rating":
                    if not plex_rating and not trakt_rating:
                        continue  # Not rated in Plex or Trakt yet — keep file safely

                # Two-way sync BEFORE deletion:
                # 1) Ensure Plex watched status & rating reach Trakt
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
                                if h_resp.status not in (200, 201):
                                    continue
                        except Exception:
                            continue

                    if mode == "delete_after_rating" and plex_rating and not trakt_rating:
                        try:
                            score_int = max(1, min(10, int(round(float(plex_rating)))))
                            tr_rate = urllib.request.Request(
                                "https://api.trakt.tv/sync/ratings",
                                data=json.dumps({"movies": [{"rating": score_int, "ids": ids_obj}]}).encode("utf-8"),
                                headers=trakt_headers,
                                method="POST",
                            )
                            with urllib.request.urlopen(tr_rate, timeout=8) as r_resp:
                                if r_resp.status not in (200, 201):
                                    continue
                        except Exception:
                            continue  # Failed to push rating to Trakt -> skip deletion for now

                # 2) Ensure Trakt watched status & rating reach Plex and persist before deleting
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
                        print(f"✓ [WatchedCleanup] Synced Trakt watched status to Plex: {item.get('title')}")
                    except Exception:
                        continue  # Could not mark watched in Plex -> skip deletion

                if mode == "delete_after_rating" and trakt_rating and not plex_rating:
                    try:
                        pr_req = urllib.request.Request(
                            f"http://127.0.0.1:32400/:/rate?identifier=com.plexapp.plugins.library&key={rk}&rating={trakt_rating}",
                            headers=headers,
                            method="PUT",
                        )
                        urllib.request.urlopen(pr_req, timeout=5).close()
                        synced_to_plex_now = True
                        print(f"★ [WatchedCleanup] Synced Trakt rating ({trakt_rating}/10) to Plex: {item.get('title')} (waiting 1 cycle before deletion)")
                    except Exception:
                        continue  # Could not save rating in Plex -> skip deletion

                if synced_to_plex_now:
                    continue  # Wait for next cycle so Plex DB & Plex.tv cloud confirm watched+rating before file removal

                # Remove from Plex Watchlist
                if plex_guid:
                    try:
                        rm_req = urllib.request.Request(
                            f"https://discover.provider.plex.tv/actions/removeFromWatchlist?ratingKey={plex_guid}",
                            headers={"X-Plex-Token": plex_token, "X-Plex-Client-Identifier": "podmedia-sync"},
                            method="PUT",
                        )
                        urllib.request.urlopen(rm_req, timeout=6).close()
                    except Exception:
                        pass

                # Remove from Trakt Watchlist
                if trakt_headers and ids_obj:
                    try:
                        tr_rm = urllib.request.Request(
                            "https://api.trakt.tv/sync/watchlist/remove",
                            data=json.dumps({"movies": [{"ids": ids_obj}]}).encode("utf-8"),
                            headers=trakt_headers,
                            method="POST",
                        )
                        urllib.request.urlopen(tr_rm, timeout=6).close()
                    except Exception:
                        pass

                # Unmonitor + delete file via Radarr (keeps DB in sync and prevents re-download)
                if radarr_online and radarr_movies is not None and tmdb_id:
                    for rm in radarr_movies:
                        if rm.get("tmdbId") == tmdb_id:
                            mf_id = (rm.get("movieFile") or {}).get("id")
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
                                    print(f"🧹 [WatchedCleanup] Deleted watched movie file & unmonitored: {item.get('title')}")
                                except Exception as e:
                                    print(f"⚠️ [WatchedCleanup] Could not delete {item.get('title')}: {e}")
                            break

            if cleaned_any_movie:
                try:
                    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:32400/library/sections/{sec_key}/refresh", headers=headers), timeout=4).close()
                    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:32400/library/sections/{sec_key}/emptyTrash", headers=headers, method="PUT"), timeout=4).close()
                except Exception:
                    pass
    except Exception as e:
        print(f"⚠️ [WatchedCleanup] Skipped: {e}")
    finally:
        _watched_cleanup_lock.release()


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
