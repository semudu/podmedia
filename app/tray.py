import sys
import os
import json
import time
import urllib.request
import urllib.parse
import webbrowser
import threading
from PIL import Image, ImageDraw
import pystray

import autostart
import podman_manager


def create_tray_icon():
    """Loads the PodMedia brand icon for the system tray (with Pillow fallback)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for fname in ("apple-touch-icon.png", "favicon.ico"):
        icon_path = os.path.join(base_dir, fname)
        if os.path.exists(icon_path):
            try:
                resampling = getattr(Image, "Resampling", Image).LANCZOS
                with Image.open(icon_path) as im:
                    return im.convert("RGBA").resize((64, 64), resampling)
            except Exception:
                pass

    # Fallback generated icon if image assets are unavailable
    width = 64
    height = 64
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=15, fill=(79, 70, 229, 255))
    draw.polygon([(26, 20), (26, 44), (44, 32)], fill=(255, 255, 255, 255))
    return image


class PodMediaTrayApp:
    def __init__(self, port, on_quit_callback=None):
        self.port = port
        self.on_quit_callback = on_quit_callback
        self.icon = None
        self._stop_poller = False
        self._state = {
            "groups": {"plex": False, "downloads": False, "music": False},
            "containers": {},
            "is_busy": False,
            "trakt_configured": podman_manager.is_plextraktsync_configured(),
        }

    def _refresh_state_once(self):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/status")
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            new_state = {
                "groups": data.get("groups") or {"plex": False, "downloads": False, "music": False},
                "containers": data.get("containers") or {},
                "is_busy": bool(data.get("is_busy", False)),
                "trakt_configured": bool(data.get("trakt_configured", False)),
            }
            changed = (new_state != self._state)
            self._state = new_state
            if changed and self.icon:
                try:
                    self.icon.update_menu()
                except Exception:
                    pass
        except Exception:
            pass

    def _state_poll_loop(self):
        # Initial fast read after HTTP server starts
        time.sleep(1.0)
        while not self._stop_poller:
            self._refresh_state_once()
            for _ in range(6):
                if self._stop_poller:
                    return
                time.sleep(1.0)

    def _run_api_action(self, action, target=""):
        """Delegates service actions to the local PodMedia HTTP API so state files,
        is_busy lock, PlexTraktSync coupling, and ImportListSync stay 100% consistent."""
        def _worker():
            try:
                qs = urllib.parse.urlencode({"action": action, "target": target})
                req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/stream?{qs}")
                with urllib.request.urlopen(req, timeout=180) as resp:
                    # Drain SSE stream until the action completes
                    while resp.read(4096):
                        pass
            except Exception as e:
                print(f"[Tray] Action '{action}' ({target}) notice:", e)
            finally:
                self._refresh_state_once()

        threading.Thread(target=_worker, daemon=True).start()

    def open_app(self, icon=None, item=None):
        webbrowser.open(f"http://localhost:{self.port}")

    def open_plex(self, icon=None, item=None):
        webbrowser.open("http://localhost:32400/web")

    def toggle_mode(self, mode):
        def _handler(icon=None, item=None):
            self._run_api_action("toggle-group", mode)
        return _handler

    def start_all_services(self, icon=None, item=None):
        self._run_api_action("start-all")

    def stop_all_services(self, icon=None, item=None):
        self._run_api_action("stop-all")

    def sync_trakt_watchlist(self, icon=None, item=None):
        def _worker():
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/trakt/sync")
                urllib.request.urlopen(req, timeout=30).close()
            except Exception as e:
                print("[Tray] Trakt sync notice:", e)
            finally:
                self._refresh_state_once()
        threading.Thread(target=_worker, daemon=True).start()

    def toggle_autostart(self, icon, item):
        currently_enabled = autostart.is_autostart_enabled()
        autostart.set_autostart(not currently_enabled)
        icon.update_menu()

    def quit_app(self, icon, item):
        print("[!] Quitting PodMedia: Stopping all running containers & releasing external disk...")
        self._stop_poller = True
        try:
            import media_watcher
            media_watcher.stop_media_watcher()
        except Exception:
            pass
        podman_manager.stop_all_containers_sync()
        podman_manager.remove_state_file()
        if self.on_quit_callback:
            self.on_quit_callback()
        icon.stop()
        os._exit(0)

    def build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                f"Open PodMedia (:{self.port})",
                self.open_app,
                default=True,
            ),
            pystray.MenuItem(
                "Open Plex Player",
                self.open_plex,
                enabled=lambda item: bool(self._state["containers"].get("media_center_plex", False)),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Cinema & Watch",
                self.toggle_mode("plex"),
                checked=lambda item: bool(self._state["groups"].get("plex", False)),
                enabled=lambda item: not self._state["is_busy"],
            ),
            pystray.MenuItem(
                "Library & Downloads",
                self.toggle_mode("downloads"),
                checked=lambda item: bool(self._state["groups"].get("downloads", False)),
                enabled=lambda item: not self._state["is_busy"],
            ),
            pystray.MenuItem(
                "Music Library",
                self.toggle_mode("music"),
                checked=lambda item: bool(self._state["groups"].get("music", False)),
                enabled=lambda item: not self._state["is_busy"],
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start All Services",
                self.start_all_services,
                enabled=lambda item: not self._state["is_busy"],
            ),
            pystray.MenuItem(
                "Stop All Services",
                self.stop_all_services,
                enabled=lambda item: not self._state["is_busy"],
            ),
            pystray.MenuItem(
                "Sync Watchlist (Trakt)",
                self.sync_trakt_watchlist,
                visible=lambda item: bool(self._state.get("trakt_configured", False)),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start on System Boot",
                self.toggle_autostart,
                checked=lambda item: autostart.is_autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Quit PodMedia",
                self.quit_app,
            ),
        )

    def run(self):
        image = create_tray_icon()
        menu = self.build_menu()
        self.icon = pystray.Icon("PodMedia", image, "PodMedia", menu)
        threading.Thread(target=self._state_poll_loop, daemon=True).start()
        print("[✓] PodMedia System Tray Icon initialized.")
        self.icon.run()
