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
from transfer_manager import transfer_manager
from disk_manager import disk_manager

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
        return podman_manager.stop_all_containers(power_down=False)

    elif action == 'quit-all':
        return podman_manager.stop_all_containers(power_down=True)

    elif action == 'music-sync':
        return podman_manager.organize_music()

    elif action == 'restart-services':
        import settings_manager
        return settings_manager.restart_services_generator(target)

    elif action in ('toggle-group', 'start-group', 'stop-group'):
        group = target.lower()
        current_group_on = groups.get(group, False)
        should_start = (action == 'start-group') or (action == 'toggle-group' and not current_group_on)

        group_apps_map = {
            'plex': ['plex'],
            'downloads': ['radarr', 'sonarr', 'prowlarr', 'qbittorrent', 'flaresolverr'],
            'music': ['plex', 'slskd', 'beets', 'metube']
        }
        app_container_map = {
            'plex': 'media_center_plex',
            'plextraktsync': 'media_center_plextraktsync',
            'radarr': 'media_center_radarr',
            'sonarr': 'media_center_sonarr',
            'prowlarr': 'media_center_prowlarr',
            'qbittorrent': 'media_center_qbittorrent',
            'flaresolverr': 'media_center_flaresolverr',
            'slskd': 'media_center_slskd',
            'beets': 'media_center_beets',
            'metube': 'media_center_metube'
        }

        group_apps = group_apps_map.get(group, [])
        if should_start:
            if group == 'plex':
                podman_manager.write_plex_state_file()
            elif group == 'downloads':
                podman_manager.write_downloads_state_file()
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
            if group == 'plex':
                podman_manager.remove_plex_state_file()
            elif group == 'downloads':
                podman_manager.remove_downloads_state_file()
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
            'plextraktsync': 'media_center_plextraktsync',
            'radarr': 'media_center_radarr',
            'sonarr': 'media_center_sonarr',
            'prowlarr': 'media_center_prowlarr',
            'qbittorrent': 'media_center_qbittorrent',
            'flaresolverr': 'media_center_flaresolverr',
            'slskd': 'media_center_slskd',
            'beets': 'media_center_beets',
            'metube': 'media_center_metube'
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
    allow_reuse_address = (os.name != 'nt')

    def server_bind(self):
        if os.name == 'nt':
            import socket
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

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

        if path == '/api/ping':
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(b'0')
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        elif path in ('/favicon.svg', '/favicon.ico', '/apple-touch-icon.png'):
            base_dir = os.path.dirname(__file__)
            asset_map = {
                '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
                '/apple-touch-icon.png': ('apple-touch-icon.png', 'image/png'),
                '/favicon.ico': ('favicon.ico', 'image/x-icon'),
            }
            fname, ctype = asset_map[path]
            fpath = os.path.join(base_dir, fname)
            if not os.path.exists(fpath):
                fpath = os.path.join(base_dir, 'favicon.svg')
                ctype = 'image/svg+xml'
            if os.path.exists(fpath):
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', ctype)
                    self.send_header('Cache-Control', 'public, max-age=86400')
                    self.end_headers()
                    with open(fpath, 'rb') as f:
                        self.wfile.write(f.read())
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass
            else:
                self.send_response(404)
                self.end_headers()

        elif path == '/' or path == '/index.html':
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
            if not is_busy:
                if groups.get("plex"):
                    podman_manager.write_plex_state_file()
                else:
                    podman_manager.remove_plex_state_file()
                if groups.get("downloads"):
                    podman_manager.write_downloads_state_file()
                else:
                    podman_manager.remove_downloads_state_file()
            ext_disk = podman_manager.get_external_disk_info()
            transfer_state = transfer_manager.get_status()
            self.send_json({
                "containers": containers,
                "groups": groups,
                "is_busy": is_busy,
                "external_disk": ext_disk,
                "transfer": transfer_state,
                "trakt_configured": podman_manager.is_plextraktsync_configured()
            })

        elif path == '/api/transfer/status':
            transfer_state = transfer_manager.get_status()
            ext_disk = podman_manager.get_external_disk_info()
            self.send_json({
                "transfer": transfer_state,
                "disk": ext_disk
            })

        elif path == '/api/transfer/start':
            ext_disk = podman_manager.get_external_disk_info()
            if not ext_disk.get("connected"):
                self.send_json({"status": "error", "message": "Harici disk bağlı veya tanımlı değil!"})
                return
            local_media = os.path.abspath(os.path.join(podman_manager.project_root, podman_manager.MEDIA_DIR)) if not os.path.isabs(podman_manager.MEDIA_DIR) else podman_manager.MEDIA_DIR
            target_ext = podman_manager.get_external_media_dir()
            res = transfer_manager.start_transfer(local_media, target_ext, categories=("movies", "series"))
            self.send_json(res)

        elif path == '/api/transfer/stop':
            query = urllib.parse.parse_qs(parsed.query)
            mode = query.get('mode', ['immediate'])[0]
            res = transfer_manager.stop_transfer(mode=mode)
            self.send_json(res)

        elif path == '/api/disks/list':
            drives = disk_manager.list_available_drives()
            active = disk_manager.get_active_disk()
            self.send_json({
                "drives": drives,
                "active": active
            })

        elif path == '/api/disks/select':
            query = urllib.parse.parse_qs(parsed.query)
            drive_letter = query.get('drive', [''])[0]
            if drive_letter:
                res = disk_manager.set_target_disk(drive_letter)
                # Refresh dynamic disk status
                podman_manager.get_external_disk_info()
                self.send_json(res)
            else:
                self.send_json({"status": "error", "message": "Eksik parametre: drive"}, 400)

        elif path == '/api/disks/sleep':
            containers, _ = podman_manager.get_container_states()
            if any(containers.values()):
                self.send_json({
                    "status": "error",
                    "message": "Diski uyutmadan önce çalışan servisleri durdurun (Stop All)."
                })
                return
            podman_manager.release_external_mounts(power_down=True)
            self.send_json({
                "status": "ok",
                "message": "Harici disk güvenle uyku moduna (Offline / 0 RPM) alındı.",
                "external_disk": podman_manager.get_external_disk_info()
            })

        elif path == '/api/disks/wake':
            woken = disk_manager.wake_all_external_drives()
            time.sleep(1.5)
            ext_disk = podman_manager.get_external_disk_info()
            self.send_json({
                "status": "ok",
                "woken": bool(woken),
                "external_disk": ext_disk
            })

        elif path == '/api/trakt/sync':
            if not podman_manager.is_plextraktsync_configured():
                self.send_json({"status": "error", "message": "PlexTraktSync henüz kurulmamış."})
                return
            try:
                import media_watcher
                ok = media_watcher.sync_trakt_watchlist()
                if media_watcher.watcher_instance and media_watcher.watcher_instance.watched_media_action != "keep":
                    threading.Thread(
                        target=media_watcher.cleanup_watched_media,
                        args=(media_watcher.watcher_instance.watched_media_action,),
                        daemon=True,
                    ).start()
                import radarr_sonarr_sync
                radarr_sonarr_sync.trigger_radarr_command("ImportListSync")
                radarr_sonarr_sync.trigger_sonarr_command("ImportListSync")
                import hub_api
                hub_api._trakt_cache["ts"] = 0
                hub_api._summary_cache["ts"] = 0
                self.send_json({"status": "ok" if ok else "busy", "synced": bool(ok)})
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, 500)

        elif path == '/api/hub/summary':
            try:
                import hub_api
                query = urllib.parse.parse_qs(parsed.query)
                force = query.get('force', ['0'])[0] == '1'
                self.send_json(hub_api.get_hub_summary(force=force))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == '/api/hub/search':
            try:
                import hub_api
                query = urllib.parse.parse_qs(parsed.query)
                term = query.get('q', [''])[0]
                mtype = query.get('type', ['all'])[0]
                self.send_json(hub_api.search_media(term, mtype))
            except Exception as e:
                self.send_json({"error": str(e), "results": []}, 500)

        elif path == '/api/hub/add':
            try:
                import hub_api
                query = urllib.parse.parse_qs(parsed.query)
                mtype = query.get('type', [''])[0]
                item_id = query.get('id', [''])[0]
                self.send_json(hub_api.add_media_item(mtype, item_id))
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, 500)

        elif path == '/api/settings':
            try:
                import settings_manager
                self.send_json(settings_manager.get_settings())
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, 500)

        elif path == '/api/updates/status':
            import auto_updater
            self.send_json(auto_updater.auto_updater.get_status())

        elif path == '/api/updates/check':
            import auto_updater
            res = auto_updater.auto_updater.check_and_update_running()
            self.send_json(res)

        elif path == '/api/autostart/plex':
            podman_manager.write_plex_state_file()
            self.send_json({"status": "starting", "service": "plex"})
            def _bg_start_plex():
                global is_busy
                for _ in range(240):
                    if not is_busy:
                        break
                    time.sleep(0.5)
                is_busy = True
                try:
                    disk_manager.wake_all_external_drives()
                    for _ in get_action_generator('start-group', 'plex'):
                        pass
                except Exception as e:
                    print("Background Plex start error:", e)
                finally:
                    is_busy = False
            threading.Thread(target=_bg_start_plex, daemon=True).start()

        elif path == '/api/autostop/plex':
            podman_manager.remove_plex_state_file()
            _, groups = podman_manager.get_container_states()
            downloads_running = groups.get('downloads', False)
            if downloads_running:
                self.send_json({"status": "stopping_plex", "downloads_running": True, "message": "Download modu açık olduğu için yalnızca Plex kapatılıyor..."})
                def _bg_stop_plex():
                    global is_busy
                    for _ in range(240):
                        if not is_busy:
                            break
                        time.sleep(0.5)
                    is_busy = True
                    try:
                        for _ in get_action_generator('stop-group', 'plex'):
                            pass
                    except Exception as e:
                        print("Background Plex stop error:", e)
                    finally:
                        is_busy = False
                threading.Thread(target=_bg_stop_plex, daemon=True).start()
            else:
                podman_manager.remove_state_file()
                self.send_json({"status": "stopping_all", "downloads_running": False, "message": "Download modu kapalı olduğu için Plex ve PodMedia tamamen kapatılıyor..."})
                try:
                    self.server.socket.close()
                except Exception:
                    pass
                def _async_quit_from_plex():
                    time.sleep(0.2)
                    try:
                        import media_watcher
                        media_watcher.stop_media_watcher()
                    except Exception:
                        pass
                    podman_manager.stop_all_containers_sync()
                    os._exit(0)
                threading.Thread(target=_async_quit_from_plex, daemon=True).start()

        elif path == '/api/autostart/downloads':
            podman_manager.write_downloads_state_file()
            self.send_json({"status": "starting", "service": "downloads"})
            def _bg_start_downloads():
                global is_busy
                for _ in range(240):
                    if not is_busy:
                        break
                    time.sleep(0.5)
                is_busy = True
                try:
                    disk_manager.wake_all_external_drives()
                    for _ in get_action_generator('start-group', 'downloads'):
                        pass
                except Exception as e:
                    print("Background Downloads start error:", e)
                finally:
                    is_busy = False
            threading.Thread(target=_bg_start_downloads, daemon=True).start()

        elif path == '/api/autostop/downloads':
            podman_manager.remove_downloads_state_file()
            _, groups = podman_manager.get_container_states()
            plex_running = groups.get('plex', False)
            if plex_running:
                self.send_json({"status": "stopping_downloads", "plex_running": True, "message": "Plex açık olduğu için yalnızca Download modu kapatılıyor..."})
                def _bg_stop_downloads():
                    global is_busy
                    for _ in range(240):
                        if not is_busy:
                            break
                        time.sleep(0.5)
                    is_busy = True
                    try:
                        for _ in get_action_generator('stop-group', 'downloads'):
                            pass
                    except Exception as e:
                        print("Background Downloads stop error:", e)
                    finally:
                        is_busy = False
                threading.Thread(target=_bg_stop_downloads, daemon=True).start()
            else:
                podman_manager.remove_state_file()
                self.send_json({"status": "stopping_all", "plex_running": False, "message": "Plex kapalı olduğu için Download modu ve PodMedia tamamen kapatılıyor..."})
                try:
                    self.server.socket.close()
                except Exception:
                    pass
                def _async_quit_from_downloads():
                    time.sleep(0.2)
                    try:
                        import media_watcher
                        media_watcher.stop_media_watcher()
                    except Exception:
                        pass
                    podman_manager.stop_all_containers_sync()
                    os._exit(0)
                threading.Thread(target=_async_quit_from_downloads, daemon=True).start()

        elif path == '/api/system/quit':
            podman_manager.remove_state_file()
            self.send_json({"status": "stopping", "message": "PodMedia kapatılıyor ve harici disk serbest bırakılıyor..."})
            try:
                self.server.socket.close()
            except Exception:
                pass
            def _async_quit():
                time.sleep(0.2)
                try:
                    import media_watcher
                    media_watcher.stop_media_watcher()
                except Exception:
                    pass
                podman_manager.stop_all_containers_sync()
                os._exit(0)
            threading.Thread(target=_async_quit, daemon=True).start()

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

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == '/api/settings':
            try:
                length = int(self.headers.get('Content-Length', 0))
                raw = self.rfile.read(length) if length > 0 else b'{}'
                payload = json.loads(raw.decode('utf-8'))
                import settings_manager
                self.send_json(settings_manager.save_settings(payload))
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, 400)
        elif path == '/api/settings/reset':
            try:
                import settings_manager
                self.send_json(settings_manager.reset_to_defaults())
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, 500)
        elif path == '/api/system/restart':
            import settings_manager
            new_port = settings_manager.get_settings().get("server_port", PORT)
            self.send_json({"status": "restarting", "port": new_port})
            def _async_restart_web():
                time.sleep(0.3)
                try:
                    self.server.socket.close()
                except Exception:
                    pass
                try:
                    import subprocess
                    app_script = os.path.abspath(__file__)
                    kwargs = podman_manager.subproc_kwargs()
                    subprocess.Popen([sys.executable, app_script], **kwargs)
                except Exception as e:
                    print("Self-restart error:", e)
                os._exit(0)
            threading.Thread(target=_async_restart_web, daemon=True).start()
        else:
            self.send_json({"status": "error", "message": "Not Found"}, 404)

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
            return (0, 0, 0, False)
        total_files = 0
        total_bytes = 0
        latest_mtime = 0
        is_locked = False
        now = time.time()
        try:
            for root, dirs, files in os.walk(target_dir):
                for f in files:
                    total_files += 1
                    fp = os.path.join(root, f)
                    try:
                        stat = os.stat(fp)
                        total_bytes += stat.st_size
                        if stat.st_mtime > latest_mtime:
                            latest_mtime = stat.st_mtime
                        if now - stat.st_mtime < 60:
                            try:
                                with open(fp, 'r+b'):
                                    pass
                            except (IOError, OSError):
                                is_locked = True
                    except Exception:
                        pass
        except Exception:
            pass
        return (total_files, total_bytes, latest_mtime, is_locked)

    def _worker():
        complete_dir = os.path.abspath(os.path.join(podman_manager.project_root, podman_manager.DOWNLOADS_DIR, "music", "complete")) if not os.path.isabs(podman_manager.DOWNLOADS_DIR) else os.path.join(podman_manager.DOWNLOADS_DIR, "music", "complete")
        
        last_state = _get_dir_state(complete_dir)
        pending_trigger = False
        last_change_time = 0

        while True:
            time.sleep(1)
            current_state = _get_dir_state(complete_dir)
            current_count, current_bytes, current_mtime, is_locked = current_state

            # File arrival, size growth, or modification event detected
            if current_count > 0 and (current_state[:3] != last_state[:3] or is_locked):
                last_state = current_state
                last_change_time = time.time()
                pending_trigger = True

            # Trigger only when file write/copy has settled (no changes and no locks for debounce_seconds)
            if pending_trigger and not is_locked and (time.time() - last_change_time >= debounce_seconds):
                pending_trigger = False
                global is_busy
                if not is_busy:
                    print(f"⚡ [Event Watcher] Music copy completed and settled in complete folder. Triggering Beets auto-tagging...")
                    try:
                        for _ in podman_manager.organize_music():
                            pass
                    except Exception as e:
                        print("Music folder watcher execution error:", e)
                    last_state = _get_dir_state(complete_dir)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

def run():
    global is_busy
    if '--stop' in sys.argv:
        podman_manager.stop_all_containers_sync()
        os._exit(0)

    if '--stop-plex' in sys.argv:
        import urllib.request
        podman_manager.remove_plex_state_file()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostop/plex", timeout=1.5)
        except Exception:
            _, groups = podman_manager.get_container_states()
            if groups.get('downloads', False):
                for _ in get_action_generator('stop-group', 'plex'):
                    pass
            else:
                podman_manager.stop_all_containers_sync()
        os._exit(0)

    if '--stop-downloads' in sys.argv:
        import urllib.request
        podman_manager.remove_downloads_state_file()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostop/downloads", timeout=1.5)
        except Exception:
            _, groups = podman_manager.get_container_states()
            if groups.get('plex', False):
                for _ in get_action_generator('stop-group', 'downloads'):
                    pass
            else:
                podman_manager.stop_all_containers_sync()
        os._exit(0)

    start_plex_flag = ('--start-plex' in sys.argv)
    start_downloads_flag = ('--start-downloads' in sys.argv)
    is_daemon = ('--daemon' in sys.argv)

    # On Windows, if invoked without --daemon (e.g. from start.bat, start_plex.bat, start_downloads.bat, or Homebridge),
    # detach immediately with closed file descriptors so callers like Node.js exec never hang on open stdio pipes.
    if os.name == 'nt' and not is_daemon:
        import urllib.request
        import subprocess
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=0.6)
            # Server is already running!
            if start_plex_flag:
                podman_manager.write_plex_state_file()
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostart/plex", timeout=1.0)
                except Exception:
                    pass
            elif start_downloads_flag:
                podman_manager.write_downloads_state_file()
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostart/downloads", timeout=1.0)
                except Exception:
                    pass
            else:
                try:
                    import webbrowser
                    webbrowser.open(f"http://localhost:{PORT}")
                except Exception:
                    pass
            os._exit(0)
        except Exception:
            pass

        # Server is not running yet -> write state files & spawn detached daemon with closed stdio handles
        podman_manager.write_state_file()
        if start_plex_flag:
            podman_manager.write_plex_state_file()
        if start_downloads_flag:
            podman_manager.write_downloads_state_file()
        pythonw_exe = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        if not os.path.exists(pythonw_exe):
            pythonw_exe = sys.executable
        cmd = [pythonw_exe, os.path.abspath(__file__), '--daemon']
        if start_plex_flag:
            cmd.append('--start-plex')
        if start_downloads_flag:
            cmd.append('--start-downloads')
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            cmd,
            cwd=podman_manager.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        )
        os._exit(0)

    server_address = ('0.0.0.0', PORT)
    try:
        httpd = AppHTTPServer(server_address, AppHandler)
    except OSError:
        print(f"[i] PodMedia is already running on http://localhost:{PORT}")
        if start_plex_flag:
            podman_manager.write_plex_state_file()
            try:
                import urllib.request
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostart/plex", timeout=1.0)
            except Exception:
                pass
        elif start_downloads_flag:
            podman_manager.write_downloads_state_file()
            try:
                import urllib.request
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostart/downloads", timeout=1.0)
            except Exception:
                pass
        else:
            try:
                import webbrowser
                webbrowser.open(f"http://localhost:{PORT}")
            except Exception:
                pass
        os._exit(0)

    # Register exit cleanup ONLY for the primary server instance & write root state file
    atexit.register(podman_manager.stop_all_containers_sync)
    podman_manager.write_state_file()
    if start_plex_flag:
        podman_manager.write_plex_state_file()
    if start_downloads_flag:
        podman_manager.write_downloads_state_file()

    # Start HTTP server thread IMMEDIATELY so /api/ping responds within milliseconds
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    def _startup_init():
        global is_busy
        if start_plex_flag or start_downloads_flag:
            is_busy = True
        try:
            # Wake up and bring online any external drives placed into offline/standby mode on previous exit
            disk_manager.wake_all_external_drives()
            if start_plex_flag:
                for _ in get_action_generator('start-group', 'plex'):
                    pass
            if start_downloads_flag:
                for _ in get_action_generator('start-group', 'downloads'):
                    pass
        except Exception as e:
            print("Startup initialization error:", e)
        finally:
            if start_plex_flag or start_downloads_flag:
                is_busy = False

    threading.Thread(target=_startup_init, daemon=True).start()

    # Start event-driven music folder watcher (debounced 4-second file arrival observer)
    start_music_folder_watcher(debounce_seconds=4)

    # Start granular media folder watcher for Plex section refreshes (movies, series, music)
    import media_watcher
    media_dir = os.path.abspath(os.path.join(podman_manager.project_root, podman_manager.MEDIA_DIR)) if not os.path.isabs(podman_manager.MEDIA_DIR) else podman_manager.MEDIA_DIR
    media_watcher.start_media_watcher(media_dir)

    # Start automated background container updater (keeps running containers on latest images)
    import auto_updater
    auto_updater.start_auto_updater()

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
