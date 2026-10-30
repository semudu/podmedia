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

class MediaWatcher(threading.Thread):
    def __init__(self, media_dir, poll_interval=3, quiet_seconds=6):
        super().__init__(daemon=True)
        self.media_dir = os.path.abspath(media_dir)
        self.poll_interval = poll_interval
        self.quiet_seconds = quiet_seconds
        self.running = True
        self.snapshots = {}
        self.fast_signatures = {}
        
        self.target_folders = {
            'movies': os.path.join(self.media_dir, 'movies'),
            'series': os.path.join(self.media_dir, 'series'),
            'music': os.path.join(self.media_dir, 'music')
        }

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
            req = urllib.request.Request(url)
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
            req = urllib.request.Request(url)
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
            req = urllib.request.Request(url, method='PUT')
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

            # Dynamically update external disk paths
            try:
                active_disk = disk_manager.get_active_disk()
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
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        print("🚀 [Plex Startup] Triggered full library scan for all sections on Plex startup.")
                        return
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

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
