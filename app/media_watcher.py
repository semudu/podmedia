import os
import time
import threading
import xml.etree.ElementTree as ET
import urllib.request

class MediaWatcher(threading.Thread):
    def __init__(self, media_dir, poll_interval=3, quiet_seconds=6):
        super().__init__(daemon=True)
        self.media_dir = os.path.abspath(media_dir)
        self.poll_interval = poll_interval
        self.quiet_seconds = quiet_seconds
        self.running = True
        self.snapshots = {}
        
        self.target_folders = {
            'movies': os.path.join(self.media_dir, 'movies'),
            'series': os.path.join(self.media_dir, 'series'),
            'music': os.path.join(self.media_dir, 'music'),
            'movies_ext': 'E:\\media\\movies',
            'series_ext': 'E:\\media\\series'
        }
        
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

    def run(self):
        # Trigger a full scan of all Plex libraries on startup
        trigger_plex_full_scan_on_startup()

        for cat, path in self.target_folders.items():
            self.snapshots[cat] = self.get_folder_snapshot(path)

        while self.running:
            time.sleep(self.poll_interval)
            for cat, path in list(self.target_folders.items()):
                if not os.path.exists(path):
                    # Directory is offline / unmounted (e.g. external disk detached)
                    # Skip without triggering false deletion scan in Plex!
                    continue

                current_snapshot = self.get_folder_snapshot(path)
                last_snapshot = self.snapshots.get(cat, (0, 0, 0))
                
                if current_snapshot != last_snapshot:
                    settled_snapshot = self.wait_for_copy_completion(cat, path)
                    self.snapshots[cat] = settled_snapshot
                    plex_cat = cat.replace('_ext', '')
                    self.trigger_plex_refresh(plex_cat)

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
