import os
import json
import ctypes
import shutil

class DiskManager:
    """
    Manages detection, identification, and selection of external/removable storage drives.
    Uses Win32 Volume Serial Numbers and .podmedia_disk_id markers so that drives are
    reliably identified even if their drive letters change (e.g. from E: to F: or Z:).
    """
    def __init__(self, project_root):
        self.project_root = os.path.abspath(project_root)
        self.config_path = os.path.join(self.project_root, 'disk_config.json')
        self.kernel32 = ctypes.windll.kernel32 if os.name == 'nt' else None
        self._ensure_config()

    def _ensure_config(self):
        if not os.path.exists(self.config_path):
            default_config = {
                "target_serial": "",
                "target_label": "",
                "folder_name": "media"
            }
            try:
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2)
            except Exception:
                pass

    def get_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {"target_serial": "", "target_label": "", "folder_name": "media"}

    def save_config(self, serial, label, folder_name="media"):
        config = {
            "target_serial": serial,
            "target_label": label,
            "folder_name": folder_name
        }
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            print("Error saving disk_config.json:", e)
            return False

    def list_available_drives(self):
        """Scans and lists all external/secondary drives attached to the system (excluding C: and project drive)."""
        drives = []
        if not self.kernel32:
            return drives

        bitmask = self.kernel32.GetLogicalDrives()
        app_drive = os.path.splitdrive(self.project_root)[0].upper()
        sys_drive = os.environ.get('SystemDrive', 'C:').upper()
        cfg = self.get_config()
        target_serial = cfg.get("target_serial", "")

        for i in range(26):
            if bitmask & (1 << i):
                letter = f"{chr(65 + i)}:"
                root = letter + "\\"
                drive_type = self.kernel32.GetDriveTypeW(root)

                # 2 = DRIVE_REMOVABLE, 3 = DRIVE_FIXED
                if drive_type in (2, 3) and letter != sys_drive and letter != app_drive:
                    vol_buf = ctypes.create_unicode_buffer(1024)
                    fs_buf = ctypes.create_unicode_buffer(1024)
                    serial = ctypes.c_ulong()

                    res = self.kernel32.GetVolumeInformationW(
                        root, vol_buf, ctypes.sizeof(vol_buf),
                        ctypes.byref(serial), None, None,
                        fs_buf, ctypes.sizeof(fs_buf)
                    )
                    if res:
                        hex_serial = f"{serial.value:08X}"
                        formatted_serial = f"{hex_serial[:4]}-{hex_serial[4:]}"
                        
                        try:
                            usage = shutil.disk_usage(root)
                            free_gb = round(usage.free / (1024**3), 1)
                            total_gb = round(usage.total / (1024**3), 1)
                        except Exception:
                            free_gb, total_gb = 0.0, 0.0

                        marker_file = os.path.join(root, '.podmedia_disk_id')
                        has_marker = os.path.exists(marker_file)
                        is_target = (formatted_serial == target_serial) or has_marker

                        drives.append({
                            "letter": letter,
                            "label": vol_buf.value or "İsimsiz Sürücü",
                            "serial": formatted_serial,
                            "fs": fs_buf.value,
                            "free_gb": free_gb,
                            "total_gb": total_gb,
                            "is_removable": (drive_type == 2),
                            "has_marker": has_marker,
                            "is_selected": is_target
                        })
        return drives

    def get_active_disk(self):
        """
        Locates the registered PodMedia target disk across all currently attached drive letters.
        Returns drive info dictionary if found, or None if the disk is unplugged.
        """
        cfg = self.get_config()
        target_serial = cfg.get("target_serial", "")
        folder_name = cfg.get("folder_name", "media")
        all_drives = self.list_available_drives()

        for d in all_drives:
            # Match by marker file (.podmedia_disk_id) or registered target Volume Serial Number
            if d["has_marker"] or (target_serial and d["serial"] == target_serial):
                media_path = os.path.join(d["letter"] + "\\", folder_name)
                return {
                    "connected": True,
                    "letter": d["letter"],
                    "label": d["label"],
                    "serial": d["serial"],
                    "fs": d["fs"],
                    "free_gb": d["free_gb"],
                    "total_gb": d["total_gb"],
                    "media_path": media_path,
                    "movies_path": os.path.join(media_path, "movies"),
                    "series_path": os.path.join(media_path, "series")
                }

        return None

    def set_target_disk(self, drive_letter):
        """
        Registers the given drive as the PodMedia archive disk.
        Writes the .podmedia_disk_id marker file and creates target folders.
        """
        drive_letter = drive_letter.upper().rstrip('\\').rstrip('/')
        if not drive_letter.endswith(':'):
            drive_letter += ':'

        root = drive_letter + "\\"
        if not os.path.exists(root):
            return {"status": "error", "message": f"{drive_letter} sürücüsü bulunamadı."}

        vol_info = None
        for d in self.list_available_drives():
            if d["letter"] == drive_letter:
                vol_info = d
                break

        if not vol_info:
            return {"status": "error", "message": f"{drive_letter} sürücüsü bilgileri okunamadı."}

        serial = vol_info["serial"]
        label = vol_info["label"]

        # Write marker file to root of the drive for instant recognition anywhere
        marker_path = os.path.join(root, '.podmedia_disk_id')
        marker_data = {
            "app": "PodMedia",
            "volume_serial": serial,
            "label": label,
            "target": "media_archive"
        }
        try:
            with open(marker_path, 'w', encoding='utf-8') as f:
                json.dump(marker_data, f, indent=2)
        except Exception as e:
            print("Warning writing marker file:", e)

        # Create target directories on the selected drive
        media_path = os.path.join(root, 'media')
        os.makedirs(os.path.join(media_path, 'movies'), exist_ok=True)
        os.makedirs(os.path.join(media_path, 'series'), exist_ok=True)

        # Save config
        self.save_config(serial, label, "media")

        return {
            "status": "ok",
            "message": f"'{label}' ({drive_letter}) başarıyla PodMedia Harici Arşiv Diski olarak tanımlandı.",
            "disk": {
                "letter": drive_letter,
                "label": label,
                "serial": serial,
                "free_gb": vol_info["free_gb"],
                "total_gb": vol_info["total_gb"]
            }
        }

# Global instance
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
disk_manager = DiskManager(project_root)

