import sys
import os
import webbrowser
import threading
from PIL import Image, ImageDraw
import pystray

import autostart
import podman_manager

def create_tray_icon():
    """Generates a 64x64 PodMedia System Tray Icon using Pillow."""
    width = 64
    height = 64
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    # Outer circle (Dark blue background #0f172a)
    draw.ellipse((4, 4, 60, 60), fill=(15, 23, 42, 255), outline=(56, 189, 248, 255), width=3)
    
    # Play icon triangle (Sky blue #38bdf8)
    draw.polygon([(26, 18), (26, 46), (46, 32)], fill=(56, 189, 248, 255))
    
    # Green status dot (#22c55e)
    draw.ellipse((44, 44, 58, 58), fill=(34, 197, 94, 255), outline=(15, 23, 42, 255), width=2)
    
    return image

class PodMediaTrayApp:
    def __init__(self, port, on_quit_callback=None):
        self.port = port
        self.on_quit_callback = on_quit_callback
        self.icon = None

    def open_dashboard(self, icon=None, item=None):
        webbrowser.open(f"http://localhost:{self.port}")

    def toggle_autostart(self, icon, item):
        currently_enabled = autostart.is_autostart_enabled()
        autostart.set_autostart(not currently_enabled)
        icon.update_menu()

    def start_all_services(self, icon=None, item=None):
        def _run():
            target_containers = set(podman_manager.ALL_CONTAINERS)
            for _ in podman_manager.apply_target_state(target_containers):
                pass
        threading.Thread(target=_run, daemon=True).start()

    def stop_all_services(self, icon=None, item=None):
        def _run():
            for _ in podman_manager.stop_all_containers():
                pass
        threading.Thread(target=_run, daemon=True).start()

    def quit_app(self, icon, item):
        if self.on_quit_callback:
            self.on_quit_callback()
        icon.stop()
        sys.exit(0)

    def build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                f"🍿 Open PodMedia Dashboard (Port {self.port})",
                self.open_dashboard,
                default=True
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "▶️ Start All Services",
                self.start_all_services
            ),
            pystray.MenuItem(
                "🛑 Stop All Services",
                self.stop_all_services
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "🚀 Start on System Boot",
                self.toggle_autostart,
                checked=lambda item: autostart.is_autostart_enabled()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "❌ Quit PodMedia",
                self.quit_app
            )
        )

    def run(self):
        image = create_tray_icon()
        menu = self.build_menu()
        self.icon = pystray.Icon("PodMedia", image, "PodMedia Hub", menu)
        print("[✓] PodMedia System Tray Icon initialized.")
        self.icon.run()
