import os
import time
import shutil
import threading
import urllib.request

class TransferManager:
    """
    Manages non-blocking background file transfers from local media (movies, series)
    to external disk (E:/media) with live progress tracking, immediate rollback,
    and graceful stop capabilities.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.worker_thread = None
        self.reset_state()

    def reset_state(self):
        self.state = {
            "active": False,
            "phase": "idle",  # "idle", "scanning", "transferring", "stopping_graceful", "completed", "cancelled", "error"
            "current_file": "",
            "current_file_bytes": 0,
            "current_file_total": 0,
            "current_file_percent": 0.0,
            "transferred_bytes": 0,
            "total_bytes": 0,
            "overall_percent": 0.0,
            "transferred_files": 0,
            "total_files": 0,
            "speed_mbps": 0.0,
            "eta_seconds": 0,
            "message": "Hazır",
            "stop_requested": None,  # None, "immediate", "graceful"
            "error": None
        }

    def get_status(self):
        with self.lock:
            # Return a copy of the state
            return dict(self.state)

    def stop_transfer(self, mode="immediate"):
        """
        mode:
          - 'immediate': Aborts the current file copy immediately, deletes .part on target, preserves source.
          - 'graceful': Finishes the current file copy, cleans up its source, and stops before starting the next file.
        """
        with self.lock:
            if not self.state["active"]:
                return {"status": "ignored", "message": "Aktif bir aktarım işlemi yok."}
            
            if mode == "immediate":
                self.state["stop_requested"] = "immediate"
                self.state["phase"] = "stopping_immediate"
                self.state["message"] = "Hemen durduruluyor ve yarım dosya geri alınıyor..."
                return {"status": "ok", "mode": "immediate", "message": "Hemen durdurma sinyali gönderildi."}
            else:
                self.state["stop_requested"] = "graceful"
                self.state["phase"] = "stopping_graceful"
                self.state["message"] = "Mevcut dosya tamamlandıktan sonra aktarım durdurulacak..."
                return {"status": "ok", "mode": "graceful", "message": "Kontrollü durdurma sinyali gönderildi."}

    def start_transfer(self, source_base, target_base, categories=("movies", "series")):
        with self.lock:
            if self.state["active"]:
                return {"status": "error", "message": "Zaten devam eden bir aktarım işlemi var."}

            self.reset_state()
            self.state["active"] = True
            self.state["phase"] = "scanning"
            self.state["message"] = "Aktarılacak dosyalar taranıyor..."

            self.worker_thread = threading.Thread(
                target=self._worker,
                args=(source_base, target_base, categories),
                daemon=True
            )
            self.worker_thread.start()
            return {"status": "ok", "message": "Aktarım arka planda başlatıldı."}

    def _cleanup_empty_dirs(self, target_dir):
        """Recursively removes empty leftover folders in source directory."""
        if not os.path.exists(target_dir):
            return
        for root, dirs, files in os.walk(target_dir, topdown=False):
            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                except Exception:
                    pass

    def _trigger_plex_scan(self):
        """Notifies Plex to refresh sections so moved files are immediately linked."""
        try:
            req = urllib.request.Request("http://localhost:32400/library/sections/all/refresh")
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

    def _worker(self, source_base, target_base, categories):
        try:
            source_base = os.path.abspath(source_base)
            target_base = os.path.abspath(target_base)

            # Ensure external target drive is accessible
            if not os.path.exists(target_base):
                # Try to create if parent drive exists
                drive = os.path.splitdrive(target_base)[0]
                if drive and not os.path.exists(drive + "\\"):
                    with self.lock:
                        self.state["active"] = False
                        self.state["phase"] = "error"
                        self.state["error"] = f"Harici disk ({drive}) takılı veya erişilebilir değil."
                        self.state["message"] = self.state["error"]
                    return
                os.makedirs(target_base, exist_ok=True)

            file_list = []
            total_bytes = 0

            # Scan files in specified categories (e.g. movies, series)
            for cat in categories:
                cat_src = os.path.join(source_base, cat)
                if not os.path.exists(cat_src):
                    continue

                for root, dirs, files in os.walk(cat_src):
                    for f in files:
                        # Skip system/temporary clutter
                        if f.startswith('.') or f.endswith('.tmp') or f.lower() in ('thumbs.db', 'desktop.ini'):
                            continue
                        
                        src_path = os.path.join(root, f)
                        rel_path = os.path.relpath(src_path, source_base)
                        dst_path = os.path.join(target_base, rel_path)

                        try:
                            size = os.path.getsize(src_path)
                            file_list.append((src_path, dst_path, size, f))
                            total_bytes += size
                        except Exception:
                            pass

            if not file_list:
                with self.lock:
                    self.state["active"] = False
                    self.state["phase"] = "completed"
                    self.state["message"] = "Yerel diskte aktarılacak film veya dizi bulunamadı."
                return

            with self.lock:
                self.state["phase"] = "transferring"
                self.state["total_files"] = len(file_list)
                self.state["total_bytes"] = total_bytes
                self.state["message"] = f"Toplam {len(file_list)} dosya ({round(total_bytes / (1024**3), 2)} GB) aktarılıyor..."

            overall_bytes_done = 0
            files_done = 0
            speed_window = []  # List of (timestamp, bytes)
            chunk_size = 8 * 1024 * 1024  # 8 MB high performance chunk for fast USB 3.0 / SSD transfers

            for src_path, dst_path, file_size, filename in file_list:
                # 1. Check if Graceful Stop was requested before starting a new file
                with self.lock:
                    if self.state["stop_requested"] == "graceful":
                        self.state["active"] = False
                        self.state["phase"] = "completed"
                        self.state["message"] = f"Kontrollü durduruldu. Son tamamlanan dosya: {files_done} adet dosya aktarıldı."
                        break

                    if self.state["stop_requested"] == "immediate":
                        self.state["active"] = False
                        self.state["phase"] = "cancelled"
                        self.state["message"] = "Aktarım hemen durduruldu."
                        break

                    self.state["current_file"] = filename
                    self.state["current_file_total"] = file_size
                    self.state["current_file_bytes"] = 0
                    self.state["current_file_percent"] = 0.0

                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                dst_temp = dst_path + ".part"

                # If a stale .part existed from an earlier crash, clean it
                if os.path.exists(dst_temp):
                    try:
                        os.remove(dst_temp)
                    except Exception:
                        pass

                current_bytes = 0
                aborted_immediate = False

                try:
                    with open(src_path, 'rb') as f_in, open(dst_temp, 'wb') as f_out:
                        while True:
                            # 2. Check for Immediate Stop during active copy
                            with self.lock:
                                if self.state["stop_requested"] == "immediate":
                                    aborted_immediate = True
                                    break

                            chunk = f_in.read(chunk_size)
                            if not chunk:
                                break

                            f_out.write(chunk)
                            current_bytes += len(chunk)
                            overall_bytes_done += len(chunk)

                            # Speed and ETA calculation
                            now = time.time()
                            speed_window.append((now, len(chunk)))
                            # Keep only last 4 seconds for rolling speed calculation
                            speed_window = [w for w in speed_window if now - w[0] <= 4.0]

                            window_bytes = sum(w[1] for w in speed_window)
                            window_duration = max(now - speed_window[0][0], 0.1) if speed_window else 1.0
                            speed_bps = window_bytes / window_duration
                            speed_mbps = round(speed_bps / (1024 * 1024), 1)

                            remaining_bytes = max(total_bytes - overall_bytes_done, 0)
                            eta_sec = int(remaining_bytes / speed_bps) if speed_bps > 1024 else 0

                            with self.lock:
                                self.state["current_file_bytes"] = current_bytes
                                self.state["current_file_percent"] = round((current_bytes / file_size) * 100, 1) if file_size else 100.0
                                self.state["transferred_bytes"] = overall_bytes_done
                                self.state["overall_percent"] = round((overall_bytes_done / total_bytes) * 100, 1) if total_bytes else 100.0
                                self.state["speed_mbps"] = speed_mbps
                                self.state["eta_seconds"] = eta_sec

                    if aborted_immediate:
                        # Clean up partial target file immediately (Rollback)
                        if os.path.exists(dst_temp):
                            try:
                                os.remove(dst_temp)
                            except Exception:
                                pass
                        # Source file on D: is untouched!
                        with self.lock:
                            self.state["active"] = False
                            self.state["phase"] = "cancelled"
                            self.state["message"] = f"Aktarım iptal edildi: '{filename}' yarım kopyası silindi, yerel kopya korundu."
                        return

                    # Sync and verify file size
                    if os.path.exists(dst_temp) and os.path.getsize(dst_temp) == file_size:
                        # Preserve file timestamps
                        try:
                            shutil.copystat(src_path, dst_temp)
                        except Exception:
                            pass
                        
                        # Atomic rename .part -> final filename
                        os.replace(dst_temp, dst_path)
                        
                        # Safely delete local copy on D: now that it is fully on E:
                        os.remove(src_path)
                        files_done += 1

                        with self.lock:
                            self.state["transferred_files"] = files_done

                        # Clean up empty parent directory if no files remain
                        src_parent = os.path.dirname(src_path)
                        try:
                            if not os.listdir(src_parent):
                                os.rmdir(src_parent)
                        except Exception:
                            pass
                    else:
                        raise IOError(f"Boyut doğrulaması başarısız oldu: {filename}")

                except Exception as file_err:
                    if aborted_immediate:
                        return
                    # On unexpected error, clean partial .part file and leave source intact
                    if os.path.exists(dst_temp):
                        try:
                            os.remove(dst_temp)
                        except Exception:
                            pass
                    raise file_err

            # Clean all empty folders in source categories
            for cat in categories:
                self._cleanup_empty_dirs(os.path.join(source_base, cat))

            # Rescan Plex
            self._trigger_plex_scan()

            with self.lock:
                if self.state["phase"] != "completed":
                    self.state["active"] = False
                    self.state["phase"] = "completed"
                    self.state["overall_percent"] = 100.0
                    self.state["message"] = f"Tüm aktarım başarıyla tamamlandı! {files_done} dosya harici diske taşındı."

        except Exception as e:
            with self.lock:
                self.state["active"] = False
                self.state["phase"] = "error"
                self.state["error"] = str(e)
                self.state["message"] = f"Aktarım hatası: {str(e)}"

# Global singleton instance
transfer_manager = TransferManager()

