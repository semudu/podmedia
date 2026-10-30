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
        self.kernel32 = ctypes.windll.kernel32 if os.name == 'nt' else None

    def list_available_drives(self):
        """Scans and lists all external/secondary drives attached to the system (excluding C: and project drive)."""
        drives = []
        if not self.kernel32:
            return drives

        bitmask = self.kernel32.GetLogicalDrives()
        app_drive = os.path.splitdrive(self.project_root)[0].upper()
        sys_drive = os.environ.get('SystemDrive', 'C:').upper()

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

                        drives.append({
                            "letter": letter,
                            "label": vol_buf.value or "İsimsiz Sürücü",
                            "serial": formatted_serial,
                            "fs": fs_buf.value,
                            "free_gb": free_gb,
                            "total_gb": total_gb,
                            "is_removable": (drive_type == 2),
                            "has_marker": has_marker,
                            "is_selected": has_marker
                        })
        return drives

    def get_active_disk(self):
        """
        Locates the registered PodMedia target disk across all currently attached drive letters.
        Returns drive info dictionary if found, or None if the disk is unplugged.
        """
        folder_name = "media"
        all_drives = self.list_available_drives()

        for d in all_drives:
            # Match by marker file (.podmedia_disk_id) located directly on the drive root
            if d["has_marker"]:
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

        # Clear marker from any other drive so only one active archive disk is designated
        for d in self.list_available_drives():
            if d["letter"] != drive_letter and d["has_marker"]:
                old_marker = os.path.join(d["letter"] + "\\", '.podmedia_disk_id')
                try:
                    if os.path.exists(old_marker):
                        os.remove(old_marker)
                except Exception:
                    pass

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

        # Sync Radarr and Sonarr libraries
        try:
            import radarr_sonarr_sync
            radarr_sonarr_sync.sync_all()
        except Exception:
            pass

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

    def safely_release_drive(self, drive_letter):
        """
        Generically releases locks and dismounts an external/secondary drive volume.
        1. Uses Windows Restart Manager API to identify and close any processes locking the drive path.
        2. Calls FSCTL_DISMOUNT_VOLUME via DeviceIoControl to flush dirty buffers and dismount the filesystem.
        """
        if os.name != 'nt' or not drive_letter:
            return False

        clean_letter = drive_letter[0].upper()
        volume_path = f"\\\\.\\{clean_letter}:"

        # 1. Close processes locking paths on this drive via Windows Restart Manager
        try:
            from ctypes import wintypes
            rm = ctypes.WinDLL('rstrtmgr')
            CCH_RM_SESSION_KEY = 32
            CCH_RM_MAX_APP_NAME = 255
            CCH_RM_MAX_SVC_NAME = 63

            class RM_UNIQUE_PROCESS(ctypes.Structure):
                _fields_ = [
                    ('dwProcessId', wintypes.DWORD),
                    ('ProcessStartTime', wintypes.FILETIME)
                ]

            class RM_PROCESS_INFO(ctypes.Structure):
                _fields_ = [
                    ('Process', RM_UNIQUE_PROCESS),
                    ('strAppName', wintypes.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
                    ('strServiceShortName', wintypes.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
                    ('ApplicationType', wintypes.DWORD),
                    ('AppStatus', wintypes.ULONG),
                    ('TSSessionId', wintypes.DWORD),
                    ('bRestartable', wintypes.BOOL)
                ]

            sess = wintypes.DWORD()
            key = (wintypes.WCHAR * (CCH_RM_SESSION_KEY + 1))()
            if rm.RmStartSession(ctypes.byref(sess), 0, key) == 0:
                try:
                    files = (wintypes.LPCWSTR * 1)(f"{clean_letter}:\\")
                    if rm.RmRegisterResources(sess, 1, files, 0, None, 0, None) == 0:
                        needed = wintypes.DWORD()
                        count = wintypes.DWORD()
                        reasons = wintypes.DWORD()
                        rm.RmGetList(sess, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
                        if needed.value > 0:
                            apps = (RM_PROCESS_INFO * needed.value)()
                            count.value = needed.value
                            if rm.RmGetList(sess, ctypes.byref(needed), ctypes.byref(count), apps, ctypes.byref(reasons)) == 0:
                                current_pid = os.getpid()
                                for i in range(count.value):
                                    pid = apps[i].Process.dwProcessId
                                    if pid > 4 and pid != current_pid:
                                        try:
                                            h_proc = self.kernel32.OpenProcess(0x0001, False, pid)
                                            if h_proc:
                                                self.kernel32.TerminateProcess(h_proc, 1)
                                                self.kernel32.CloseHandle(h_proc)
                                        except Exception:
                                            pass
                finally:
                    rm.RmEndSession(sess)
        except Exception:
            pass

        # 2. Tell filesystem driver to flush buffers and dismount the volume
        try:
            from ctypes import wintypes
            GENERIC_READ = 0x80000000
            GENERIC_WRITE = 0x40000000
            FILE_SHARE_READ = 1
            FILE_SHARE_WRITE = 2
            OPEN_EXISTING = 3
            FSCTL_DISMOUNT_VOLUME = 0x00090020

            h = self.kernel32.CreateFileW(
                volume_path,
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None
            )
            if h != -1:
                bytes_ret = wintypes.DWORD()
                self.kernel32.DeviceIoControl(h, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(bytes_ret), None)
                self.kernel32.CloseHandle(h)
                return True
        except Exception:
            pass
        return False

    def release_all_external_drives(self):
        """Releases and dismounts all detected external/secondary drives."""
        letters = set()
        active = self.get_active_disk()
        if active and active.get('letter'):
            letters.add(active['letter'][0].upper())
        for d in self.list_available_drives():
            if d.get('letter'):
                letters.add(d['letter'][0].upper())
        letters.update(['E', 'F', 'G'])

        for l in letters:
            self.safely_release_drive(l)

# Global instance
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
disk_manager = DiskManager(project_root)

