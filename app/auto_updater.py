import os
import time
import subprocess
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def subproc_kwargs():
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs

class ContainerAutoUpdater(threading.Thread):
    """
    Monitors running PodMedia containers while active.
    When containers are running on-demand, checks their registries for new image releases.
    If an update is found, gracefully pulls and recreates the container with the latest version.
    """
    def __init__(self, check_interval_seconds=7200, initial_delay_seconds=120):
        super().__init__(daemon=True)
        self.check_interval = check_interval_seconds
        self.initial_delay = initial_delay_seconds
        self.running = True
        self.last_check_time = 0
        self.last_result = "Henüz kontrol edilmedi"
        self.is_checking = False
        self.lock = threading.Lock()

    def get_status(self):
        with self.lock:
            return {
                "active": self.is_checking,
                "last_check_time": self.last_check_time,
                "last_result": self.last_result,
                "interval_hours": round(self.check_interval / 3600, 1)
            }

    def check_and_update_running(self):
        """Checks and updates only currently running containers."""
        with self.lock:
            if self.is_checking:
                return {"status": "busy", "message": "Güncelleme kontrolü zaten sürüyor."}
            self.is_checking = True

        try:
            # 1. Discover currently running PodMedia containers
            res = subprocess.run(
                ['podman', 'ps', '--filter', 'name=media_center_', '--format', '{{.Names}}'],
                capture_output=True, text=True, **subproc_kwargs()
            )
            if res.returncode != 0:
                with self.lock:
                    self.last_result = "Podman servisine erişilemedi"
                return {"status": "error", "message": "Podman erişilemedi."}

            running = [c.strip() for c in res.stdout.strip().splitlines() if c.strip()]
            if not running:
                with self.lock:
                    self.last_result = "Açık konteyner yok (Beklemede)"
                    self.last_check_time = time.time()
                return {"status": "ok", "updated": [], "message": "Çalışan konteyner yok."}

            updated_containers = []

            for container in running:
                # Get current running image hash
                res_cur = subprocess.run(
                    ['podman', 'inspect', container, '--format', '{{.Image}}'],
                    capture_output=True, text=True, **subproc_kwargs()
                )
                if res_cur.returncode != 0:
                    continue
                current_img_id = res_cur.stdout.strip()

                # Get image name/tag (e.g. lscr.io/linuxserver/sonarr:latest)
                res_name = subprocess.run(
                    ['podman', 'inspect', container, '--format', '{{.Config.Image}}'],
                    capture_output=True, text=True, **subproc_kwargs()
                )
                img_name = res_name.stdout.strip()
                if not img_name or img_name == "<none>":
                    continue

                # Check registry by pulling latest manifest
                pull_res = subprocess.run(['podman', 'pull', img_name], capture_output=True, text=True, **subproc_kwargs())
                if pull_res.returncode != 0:
                    continue

                # Check if pulled image ID is different from current running image ID
                res_new = subprocess.run(
                    ['podman', 'inspect', img_name, '--format', '{{.Id}}'],
                    capture_output=True, text=True, **subproc_kwargs()
                )
                latest_img_id = res_new.stdout.strip()

                if latest_img_id and latest_img_id != current_img_id:
                    print(f"🔄 [AutoUpdater] Yeni sürüm tespit edildi: {container} ({img_name}). Konteyner yenileniyor...")
                    
                    # Recreate container
                    self._recreate_container(container)
                    updated_containers.append(container)

            # Prune obsolete image layers if updates occurred
            if updated_containers:
                subprocess.run(['podman', 'image', 'prune', '-f'], capture_output=True, text=True, **subproc_kwargs())
                result_msg = f"{len(updated_containers)} konteyner güncellendi: {', '.join(updated_containers)}"
            else:
                result_msg = f"Tüm açık konteynerler güncel ({len(running)} aktif)"

            with self.lock:
                self.last_check_time = time.time()
                self.last_result = result_msg

            return {"status": "ok", "updated": updated_containers, "message": result_msg}

        except Exception as e:
            with self.lock:
                self.last_result = f"Hata: {str(e)}"
            return {"status": "error", "message": str(e)}

        finally:
            with self.lock:
                self.is_checking = False

    def _recreate_container(self, container_name):
        """Gracefully stops old container and starts new one with fresh image."""
        try:
            import sys
            app_dir = os.path.join(PROJECT_ROOT, "app")
            if app_dir not in sys.path:
                sys.path.insert(0, app_dir)
            import podman_manager

            # Determine service name from container name
            if container_name == 'media_center_plex':
                service_cmd = 'plex'
                compose_dir = os.path.join(PROJECT_ROOT, 'services', 'plex')
            else:
                service_name = podman_manager.TOOL_MAP.get(container_name)
                if not service_name:
                    return
                service_cmd = service_name
                compose_dir = os.path.join(PROJECT_ROOT, 'services', 'tools')

            # Force remove old container
            subprocess.run(['podman', 'rm', '-f', container_name], capture_output=True, text=True, **subproc_kwargs())

            # Recreate with latest image via podman_manager's environment injection
            for _ in podman_manager.run_command_generator(f'podman-compose up -d {service_cmd}', cwd=compose_dir):
                pass

            print(f"✓ [AutoUpdater] {container_name} başarıyla en son sürüme güncellendi.")

        except Exception as e:
            print(f"Error recreating {container_name}:", e)

    def run(self):
        # Initial sleep after startup so user doesn't experience delay when starting app
        time.sleep(self.initial_delay)

        while self.running:
            try:
                self.check_and_update_running()
            except Exception as e:
                print("AutoUpdater background loop error:", e)

            # Sleep in small increments to allow responsive shutdown
            for _ in range(int(self.check_interval / 5)):
                if not self.running:
                    break
                time.sleep(5)

    def stop(self):
        self.running = False

# Global instance
auto_updater = ContainerAutoUpdater(check_interval_seconds=7200, initial_delay_seconds=60)

def start_auto_updater():
    if not auto_updater.is_alive():
        auto_updater.start()
        print("[AutoUpdater] Arka plan otomatik imaj güncelleyici aktif.")
