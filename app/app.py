import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUN_DIR = os.path.join(_PROJECT_ROOT, 'run')

if sys.stdout is None or sys.stderr is None:
    try:
        os.makedirs(_RUN_DIR, exist_ok=True)
        _log_path = os.path.join(_RUN_DIR, 'app.log')
        if os.path.exists(_log_path) and os.path.getsize(_log_path) > 2 * 1024 * 1024:
            open(_log_path, 'w', encoding='utf-8').close()
        _log_file = open(_log_path, 'a', encoding='utf-8', errors='replace', buffering=1)
    except Exception:
        _log_file = open(os.devnull, 'w')
    if sys.stdout is None:
        sys.stdout = _log_file
    if sys.stderr is None:
        sys.stderr = _log_file

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
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
    """Routes UI & CLI action requests to the declarative mode/container engine in podman_manager."""
    states, groups = podman_manager.get_container_states()

    if action == 'start-all':
        return podman_manager.reconcile_modes(set(podman_manager.VALID_MODES))

    elif action == 'stop-all':
        return podman_manager.stop_all_containers(power_down=None)

    elif action == 'quit-all':
        return podman_manager.stop_all_containers(power_down=True)

    elif action == 'restart-services':
        import settings_manager
        return settings_manager.restart_services_generator(target)

    elif action in ('toggle-group', 'start-group', 'stop-group'):
        mode = podman_manager.normalize_mode(target)
        if mode not in podman_manager.VALID_MODES:
            def bad_gen():
                yield f"[X] Unknown mode: {target}\n"
            return bad_gen()

        active_modes = {m for m, is_on in groups.items() if is_on}
        # Also honor any mode state file currently written (e.g. concurrent autostart calls)
        for m in podman_manager.VALID_MODES:
            if podman_manager.is_mode_state_active(m):
                active_modes.add(m)

        current_on = groups.get(mode, False)
        should_start = (action == 'start-group') or (action == 'toggle-group' and not current_on)

        if should_start:
            active_modes.add(mode)
        else:
            active_modes.discard(mode)

        return podman_manager.reconcile_modes(active_modes)

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
            'aurral': 'media_center_aurral',
            'slskd': 'media_center_slskd'
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


class AppHTTPServer(ThreadingHTTPServer):
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
                for m in podman_manager.VALID_MODES:
                    if groups.get(m):
                        podman_manager.write_mode_state_file(m)
                    else:
                        podman_manager.remove_mode_state_file(m)
            ext_disk = podman_manager.get_external_disk_info()
            transfer_state = transfer_manager.get_status()
            plex_disk_mode = podman_manager.get_plex_disk_mode(containers.get('media_center_plex', False))
            self.send_json({
                "containers": containers,
                "groups": groups,
                "plex_disk_mode": plex_disk_mode,
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
                podman_manager.get_external_disk_info()
                self.send_json(res)
            else:
                self.send_json({"status": "error", "message": "Eksik parametre: drive"}, 400)

        elif path == '/api/disks/sleep':
            active_disk_svcs = podman_manager.get_containers_using_external_disk()
            if active_disk_svcs:
                self.send_json({
                    "status": "error",
                    "message": "Diski uyutmadan önce harici diski kullanan modları (Film & Dizi / İndirme) kapatın."
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

        elif path in ('/api/autostart/video', '/api/autostart/plex', '/api/autostart/downloads', '/api/autostart/music'):
            mode = podman_manager.normalize_mode(path.rsplit('/', 1)[-1])
            podman_manager.write_mode_state_file(mode)
            self.send_json({"status": "starting", "service": mode})

            def _bg_start_mode(target_mode=mode):
                global is_busy
                for _ in range(240):
                    if not is_busy:
                        break
                    time.sleep(0.5)
                is_busy = True
                try:
                    for _ in get_action_generator('start-group', target_mode):
                        pass
                except Exception as e:
                    print(f"Background {target_mode} start error:", e)
                finally:
                    is_busy = False
            threading.Thread(target=_bg_start_mode, daemon=True).start()

        elif path in ('/api/autostop/video', '/api/autostop/plex', '/api/autostop/downloads', '/api/autostop/music'):
            mode = podman_manager.normalize_mode(path.rsplit('/', 1)[-1])
            podman_manager.remove_mode_state_file(mode)

            self.send_json({
                "status": f"stopping_{mode}",
                "message": f"{mode} durduruluyor; başka aktif mod kalmazsa PodMedia ve harici disk kapatılacak..."
            })

            server_ref = self.server
            def _bg_autostop(target_mode=mode):
                global is_busy
                for _ in range(240):
                    if not is_busy:
                        break
                    time.sleep(0.5)
                is_busy = True
                should_exit = False
                try:
                    for _ in get_action_generator('stop-group', target_mode):
                        pass

                    containers_after, groups_after = podman_manager.get_container_states()
                    any_container_up = any(containers_after.values())
                    any_mode_up = any(groups_after.values())
                    any_mode_file = podman_manager.any_mode_state_file_exists()

                    if not any_container_up and not any_mode_up and not any_mode_file:
                        should_exit = True
                except Exception as e:
                    print(f"Background {target_mode} stop error:", e)
                finally:
                    is_busy = False

                if should_exit:
                    print("[!] All modes & containers are stopped after autostop — sleeping external disk and shutting down PodMedia...")
                    podman_manager.remove_state_file()
                    try:
                        server_ref.socket.close()
                    except Exception:
                        pass
                    try:
                        import media_watcher
                        media_watcher.stop_media_watcher()
                    except Exception:
                        pass
                    podman_manager.stop_all_containers_sync(power_down=True)
                    os._exit(0)

            threading.Thread(target=_bg_autostop, daemon=True).start()

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
                podman_manager.stop_all_containers_sync(power_down=True)
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


def is_port_in_use(port):
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0
    except Exception:
        return False


def _parse_start_modes_from_argv():
    modes = []
    if '--start-video' in sys.argv or '--start-plex' in sys.argv:
        modes.append('video')
    if '--start-downloads' in sys.argv:
        modes.append('downloads')
    if '--start-music' in sys.argv:
        modes.append('music')
    return modes


def _parse_stop_mode_from_argv():
    for flag, mode in (
        ('--stop-video', 'video'),
        ('--stop-plex', 'video'),
        ('--stop-downloads', 'downloads'),
        ('--stop-music', 'music'),
    ):
        if flag in sys.argv:
            return mode
    return None


def run():
    global is_busy
    if '--stop' in sys.argv:
        podman_manager.stop_all_containers_sync(power_down=True)
        os._exit(0)

    stop_mode = _parse_stop_mode_from_argv()
    if stop_mode:
        import urllib.request
        podman_manager.remove_mode_state_file(stop_mode)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostop/{stop_mode}", timeout=4.0).close()
        except Exception:
            for _ in get_action_generator('stop-group', stop_mode):
                pass
            containers_after, groups_after = podman_manager.get_container_states()
            if not any(containers_after.values()) and not any(groups_after.values()) and not podman_manager.any_mode_state_file_exists():
                podman_manager.stop_all_containers_sync(power_down=True)
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/system/quit", timeout=1.5).close()
                except Exception:
                    pass
        os._exit(0)

    start_modes = _parse_start_modes_from_argv()
    is_daemon = ('--daemon' in sys.argv)

    # On Windows, if invoked without --daemon (e.g. from scripts/*.bat or Homebridge),
    # detach immediately with closed stdio handles so callers never hang on open pipes.
    if os.name == 'nt' and not is_daemon:
        import urllib.request
        import subprocess
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=0.6)
            if start_modes:
                for m in start_modes:
                    podman_manager.write_mode_state_file(m)
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostart/{m}", timeout=1.0).close()
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

        podman_manager.write_state_file()
        for m in start_modes:
            podman_manager.write_mode_state_file(m)
        pythonw_exe = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        if not os.path.exists(pythonw_exe):
            pythonw_exe = sys.executable
        cmd = [pythonw_exe, os.path.abspath(__file__), '--daemon']
        for m in start_modes:
            cmd.append(f'--start-{m}')
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
        if start_modes:
            import urllib.request
            for m in start_modes:
                podman_manager.write_mode_state_file(m)
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/autostart/{m}", timeout=1.0).close()
                except Exception:
                    pass
        else:
            try:
                import webbrowser
                webbrowser.open(f"http://localhost:{PORT}")
            except Exception:
                pass
        os._exit(0)

    atexit.register(podman_manager.stop_all_containers_sync)
    podman_manager.write_state_file()
    for m in start_modes:
        podman_manager.write_mode_state_file(m)

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    def _startup_init():
        global is_busy
        if not start_modes:
            return
        is_busy = True
        try:
            for _ in podman_manager.reconcile_modes(set(start_modes)):
                pass
        except Exception as e:
            print("Startup initialization error:", e)
        finally:
            is_busy = False

    threading.Thread(target=_startup_init, daemon=True).start()

    import media_watcher
    media_dir = os.path.abspath(os.path.join(podman_manager.project_root, podman_manager.MEDIA_DIR)) if not os.path.isabs(podman_manager.MEDIA_DIR) else podman_manager.MEDIA_DIR
    media_watcher.start_media_watcher(media_dir)

    import auto_updater
    auto_updater.start_auto_updater()

    print("==================================================")
    print(f" PodMedia running at http://localhost:{PORT}")
    print("==================================================")

    try:
        import tray
        app_tray = tray.PodMediaTrayApp(PORT, on_quit_callback=lambda: httpd.shutdown())
        app_tray.run()
    except Exception as e:
        print("Running in headless console mode (System Tray disabled):", e)
        server_thread.join()


if __name__ == '__main__':
    run()
