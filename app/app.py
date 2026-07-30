import sys
import os

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import time
import urllib.parse
import json

import podman_manager
import atexit

# Ensure all PodMedia containers are stopped cleanly on application exit
atexit.register(podman_manager.stop_all_containers_sync)

PORT = podman_manager.PORT
is_busy = False

def get_action_generator(action, target):
    """Routes UI action requests to corresponding generator functions in podman_manager."""
    states, groups = podman_manager.get_container_states()
    target_containers = set()

    for c_name, is_running in states.items():
        if is_running:
            target_containers.add(c_name)

    if action == 'start-all':
        target_containers = set(podman_manager.ALL_CONTAINERS)
        return podman_manager.apply_target_state(target_containers)

    elif action == 'stop-all':
        return podman_manager.stop_all_containers()

    elif action == 'music-sync':
        return podman_manager.organize_music()

    elif action == 'toggle-group':
        group = target.lower()
        current_group_on = groups.get(group, False)

        group_apps_map = {
            'plex': ['plex'],
            'downloads': ['radarr', 'sonarr', 'prowlarr', 'qbittorrent', 'flaresolverr'],
            'music': ['plex', 'slskd', 'beets']
        }
        app_container_map = {
            'plex': 'media_center_plex',
            'radarr': 'media_center_radarr',
            'sonarr': 'media_center_sonarr',
            'prowlarr': 'media_center_prowlarr',
            'qbittorrent': 'media_center_qbittorrent',
            'flaresolverr': 'media_center_flaresolverr',
            'slskd': 'media_center_slskd',
            'beets': 'media_center_beets'
        }

        group_apps = group_apps_map.get(group, [])
        if not current_group_on:
            apps_to_start = [a for a in group_apps if not states.get(app_container_map[a], False)]
            if not apps_to_start:
                def empty_gen(): yield f"[✓] '{group.upper()}' group is already active.\n"
                return empty_gen()
            
            def group_start_gen():
                for app_name in apps_to_start:
                    for log in podman_manager.start_single_app(app_name):
                        yield log
            return group_start_gen()
        else:
            apps_to_stop = [a for a in group_apps if states.get(app_container_map[a], False)]
            if not apps_to_stop:
                def empty_gen(): yield f"[✓] '{group.upper()}' group is already stopped.\n"
                return empty_gen()

            def group_stop_gen():
                for app_name in apps_to_stop:
                    for log in podman_manager.stop_single_app(app_name):
                        yield log
            return group_stop_gen()

    elif action == 'toggle-app':
        app = target.lower()
        app_container_map = {
            'plex': 'media_center_plex',
            'radarr': 'media_center_radarr',
            'sonarr': 'media_center_sonarr',
            'prowlarr': 'media_center_prowlarr',
            'qbittorrent': 'media_center_qbittorrent',
            'flaresolverr': 'media_center_flaresolverr',
            'slskd': 'media_center_slskd',
            'beets': 'media_center_beets'
        }
        c_name = app_container_map.get(app)
        if c_name and states.get(c_name, False):
            return podman_manager.stop_single_app(app)
        else:
            return podman_manager.start_single_app(app)

    else:
        def empty_gen():
            yield "Invalid action.\n"
        return empty_gen()

class AppHTTPServer(HTTPServer):
    def handle_error(self, request, client_address):
        # Silently ignore client socket disconnections (e.g. closing browser tab during SSE)
        pass

class AppHandler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_GET(self):
        global is_busy
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == '/' or path == '/index.html':
            html_path = os.path.join(os.path.dirname(__file__), 'index.html')
            if os.path.exists(html_path):
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    with open(html_path, 'rb') as f:
                        self.wfile.write(f.read())
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass
            else:
                self.send_response(404)
                self.end_headers()

        elif path == '/api/status':
            containers, groups = podman_manager.get_container_states()
            self.send_json({
                "containers": containers,
                "groups": groups,
                "is_busy": is_busy
            })

        elif path == '/api/stream':
            if is_busy:
                try:
                    self.send_response(429)
                    self.end_headers()
                    msg = "data: System is currently processing another operation.\n\n"
                    self.wfile.write(msg.encode('utf-8'))
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass
                return

            is_busy = True
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

                query = urllib.parse.parse_qs(parsed.query)
                action = query.get('action', [''])[0]
                target = query.get('target', [''])[0]

                generator = get_action_generator(action, target)
                for line in generator:
                    formatted = line.replace('\n', '\ndata: ')
                    event_data = f"data: {formatted}\n\n"
                    self.wfile.write(event_data.encode('utf-8'))
                    self.wfile.flush()

                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            except Exception as e:
                print("SSE Streaming error:", e)
            finally:
                is_busy = False

        else:
            self.send_json({"status": "running", "info": "PodMedia Server"})

    def log_message(self, format, *args):
        return

import threading

def is_port_in_use(port):
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0
    except Exception:
        return False

def start_music_folder_watcher(debounce_seconds=4):
    """Event-driven folder watcher for downloads/music/complete.
    Triggers organize_music() automatically when new files arrive and settle."""
    def _get_dir_state(target_dir):
        if not os.path.exists(target_dir):
            return (0, 0)
        total_files = 0
        latest_mtime = 0
        try:
            for root, _, files in os.walk(target_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    total_files += 1
                    try:
                        mtime = os.path.getmtime(fp)
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                    except Exception:
                        pass
        except Exception:
            pass
        return (total_files, latest_mtime)

    def _worker():
        complete_dir = os.path.abspath(os.path.join(podman_manager.project_root, podman_manager.DOWNLOADS_DIR, "music", "complete")) if not os.path.isabs(podman_manager.DOWNLOADS_DIR) else os.path.join(podman_manager.DOWNLOADS_DIR, "music", "complete")
        
        last_files_count, last_mtime = _get_dir_state(complete_dir)
        pending_trigger = False
        last_change_time = 0

        while True:
            time.sleep(1)
            current_count, current_mtime = _get_dir_state(complete_dir)

            # File arrival or modification event detected
            if current_count > 0 and (current_count != last_files_count or current_mtime != last_mtime):
                last_files_count = current_count
                last_mtime = current_mtime
                last_change_time = time.time()
                pending_trigger = True

            # If an event occurred and directory state has settled (no new writes for debounce_seconds)
            if pending_trigger and (time.time() - last_change_time >= debounce_seconds):
                pending_trigger = False
                global is_busy
                if not is_busy:
                    print(f"⚡ [Event Watcher] New music detected in complete folder. Triggering Beets auto-tagging...")
                    try:
                        for _ in podman_manager.organize_music():
                            pass
                    except Exception as e:
                        print("Music folder watcher execution error:", e)
                    last_files_count, last_mtime = _get_dir_state(complete_dir)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

def run():
    if is_port_in_use(PORT):
        print(f"[i] PodMedia is already running on http://localhost:{PORT}")
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{PORT}")
        except Exception:
            pass
        sys.exit(0)

    server_address = ('0.0.0.0', PORT)
    httpd = AppHTTPServer(server_address, AppHandler)
    
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    # Start event-driven music folder watcher (debounced 4-second file arrival observer)
    start_music_folder_watcher(debounce_seconds=4)

    # Start granular media folder watcher for Plex section refreshes (movies, series, music)
    import media_watcher
    media_dir = os.path.abspath(os.path.join(podman_manager.project_root, podman_manager.MEDIA_DIR)) if not os.path.isabs(podman_manager.MEDIA_DIR) else podman_manager.MEDIA_DIR
    media_watcher.start_media_watcher(media_dir)

    print(f"==================================================")
    print(f" 🍿 PodMedia App running at http://localhost:{PORT}")
    print(f"==================================================")

    try:
        import tray
        app_tray = tray.PodMediaTrayApp(PORT, on_quit_callback=lambda: httpd.shutdown())
        app_tray.run()
    except Exception as e:
        print("Running in headless console mode (System Tray disabled):", e)
        server_thread.join()

if __name__ == '__main__':
    run()
