import os
import time
import threading
import xml.etree.ElementTree as ET
import urllib.request

class MediaWatcher(threading.Thread):
    def __init__(self, media_dir, poll_interval=3):
        super().__init__(daemon=True)
        self.media_dir = os.path.abspath(media_dir)
        self.poll_interval = poll_interval
        self.running = True
        self.snapshots = {}
        
        self.target_folders = {
            'movies': os.path.join(self.media_dir, 'movies'),
            'series': os.path.join(self.media_dir, 'series'),
            'music': os.path.join(self.media_dir, 'music')
        }
        
    def get_folder_snapshot(self, path):
        if not os.path.exists(path):
            return 0, 0
        total_files = 0
        latest_mtime = 0
        try:
            for root, dirs, files in os.walk(path):
                for f in files:
                    total_files += 1
                    fp = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(fp)
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                    except Exception:
                        pass
        except Exception:
            pass
        return total_files, latest_mtime

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
        for cat, path in self.target_folders.items():
            self.snapshots[cat] = self.get_folder_snapshot(path)

        while self.running:
            time.sleep(self.poll_interval)
            for cat, path in self.target_folders.items():
                current_snapshot = self.get_folder_snapshot(path)
                last_snapshot = self.snapshots.get(cat, (0, 0))
                
                if current_snapshot != last_snapshot:
                    time.sleep(2)
                    self.snapshots[cat] = self.get_folder_snapshot(path)
                    print(f"[MediaWatcher] Change detected in '{cat}' library ({last_snapshot} -> {self.snapshots[cat]}). Refreshing Plex section...")
                    self.trigger_plex_refresh(cat)

    def stop(self):
        self.running = False

watcher_instance = None

def start_media_watcher(media_dir):
    global watcher_instance
    if watcher_instance is None or not watcher_instance.is_alive():
        watcher_instance = MediaWatcher(media_dir)
        watcher_instance.start()
        print(f"[MediaWatcher] Started background watcher for {media_dir} (Movies, Series, Music).")

def stop_media_watcher():
    global watcher_instance
    if watcher_instance and watcher_instance.is_alive():
        watcher_instance.stop()
