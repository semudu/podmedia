import os
import sys
import sqlite3
import json
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(PROJECT_ROOT, "app") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))

from disk_manager import disk_manager
RADARR_DIR = os.path.join(PROJECT_ROOT, "services", "tools", "radarr", "config")
SONARR_DIR = os.path.join(PROJECT_ROOT, "services", "tools", "sonarr", "config")
LOCAL_MEDIA_DIR = os.path.join(PROJECT_ROOT, "media")

def _get_api_key(xml_path):
    if not os.path.exists(xml_path):
        return None
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        key_el = root.find("ApiKey")
        if key_el is not None and key_el.text:
            return key_el.text.strip()
    except Exception:
        pass
    return None

def trigger_radarr_command(name, body=None):
    """Sends a command to Radarr REST API if container is currently active."""
    api_key = _get_api_key(os.path.join(RADARR_DIR, "config.xml"))
    if not api_key:
        return
    url = "http://localhost:7878/api/v3/command"
    payload = {"name": name}
    if body:
        payload.update(body)
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception:
        pass

def trigger_sonarr_command(name, body=None):
    """Sends a command to Sonarr REST API if container is currently active."""
    api_key = _get_api_key(os.path.join(SONARR_DIR, "config.xml"))
    if not api_key:
        return
    url = "http://localhost:8989/api/v3/command"
    payload = {"name": name}
    if body:
        payload.update(body)
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception:
        pass

def sync_radarr(external_movies_path=None, local_movies_path=None):
    """
    Synchronizes Radarr movie paths between local (/movies) and external (/movies_external) storage.
    Ensures Radarr always knows the exact location of movies so it never re-downloads them.
    """
    db_path = os.path.join(RADARR_DIR, "radarr.db")
    if not os.path.exists(db_path):
        return 0

    if not local_movies_path:
        local_movies_path = os.path.join(LOCAL_MEDIA_DIR, "movies")

    updated_count = 0
    try:
        con = sqlite3.connect(db_path, timeout=5)
        cur = con.cursor()

        # 1. Ensure RootFolders table has /movies_external/
        cur.execute("SELECT Path FROM RootFolders")
        existing_roots = [r[0] for r in cur.fetchall()]
        if "/movies_external/" not in existing_roots and "/movies_external" not in existing_roots:
            cur.execute("INSERT INTO RootFolders (Path) VALUES ('/movies_external/')")

        # 2. Check external movies (if disk is connected)
        if external_movies_path and os.path.exists(external_movies_path):
            try:
                ext_folders = [f for f in os.listdir(external_movies_path) if os.path.isdir(os.path.join(external_movies_path, f))]
            except Exception:
                ext_folders = []

            for folder in ext_folders:
                # If folder exists on external drive, movie path in Radarr should be /movies_external/...
                cur.execute(
                    "UPDATE Movies SET Path = ? WHERE Path = ?",
                    (f"/movies_external/{folder}", f"/movies/{folder}")
                )
                if cur.rowcount > 0:
                    updated_count += cur.rowcount

        # 3. Check local movies (in case files were moved back to local D:)
        if os.path.exists(local_movies_path):
            try:
                local_folders = [f for f in os.listdir(local_movies_path) if os.path.isdir(os.path.join(local_movies_path, f))]
            except Exception:
                local_folders = []

            for folder in local_folders:
                cur.execute(
                    "UPDATE Movies SET Path = ? WHERE Path = ?",
                    (f"/movies/{folder}", f"/movies_external/{folder}")
                )
                if cur.rowcount > 0:
                    updated_count += cur.rowcount

        con.commit()
        con.close()

        if updated_count > 0:
            print(f"🎬 [RadarrSync] Updated {updated_count} movie paths in Radarr database.")
            trigger_radarr_command("DownloadedMoviesScan")

    except Exception as e:
        print("Error during Radarr sync:", e)

    return updated_count

def sync_sonarr(external_series_path=None, local_series_path=None):
    """
    Synchronizes Sonarr series paths between local (/tv or /series) and external (/tv_external or /series_external).
    Ensures Sonarr always knows the exact location of series so it never re-downloads them.
    """
    db_path = os.path.join(SONARR_DIR, "sonarr.db")
    if not os.path.exists(db_path):
        return 0

    if not local_series_path:
        local_series_path = os.path.join(LOCAL_MEDIA_DIR, "series")

    updated_count = 0
    try:
        con = sqlite3.connect(db_path, timeout=5)
        cur = con.cursor()

        # 1. Ensure RootFolders has /tv_external/ and /series_external/
        cur.execute("SELECT Path FROM RootFolders")
        existing_roots = [r[0] for r in cur.fetchall()]
        if "/tv_external/" not in existing_roots and "/tv_external" not in existing_roots:
            cur.execute("INSERT INTO RootFolders (Path) VALUES ('/tv_external/')")
        if "/series_external/" not in existing_roots and "/series_external" not in existing_roots:
            cur.execute("INSERT INTO RootFolders (Path) VALUES ('/series_external/')")

        # 2. Check external series
        if external_series_path and os.path.exists(external_series_path):
            try:
                ext_folders = [f for f in os.listdir(external_series_path) if os.path.isdir(os.path.join(external_series_path, f))]
            except Exception:
                ext_folders = []

            for folder in ext_folders:
                # Update /tv/<folder> or /series/<folder> to /tv_external/<folder>
                cur.execute(
                    "UPDATE Series SET Path = ? WHERE Path = ? OR Path = ?",
                    (f"/tv_external/{folder}", f"/tv/{folder}", f"/series/{folder}")
                )
                if cur.rowcount > 0:
                    updated_count += cur.rowcount

        # 3. Check local series
        if os.path.exists(local_series_path):
            try:
                local_folders = [f for f in os.listdir(local_series_path) if os.path.isdir(os.path.join(local_series_path, f))]
            except Exception:
                local_folders = []

            for folder in local_folders:
                cur.execute(
                    "UPDATE Series SET Path = ? WHERE Path = ? OR Path = ?",
                    (f"/tv/{folder}", f"/tv_external/{folder}", f"/series_external/{folder}")
                )
                if cur.rowcount > 0:
                    updated_count += cur.rowcount

        con.commit()
        con.close()

        if updated_count > 0:
            print(f"📺 [SonarrSync] Updated {updated_count} series paths in Sonarr database.")
            trigger_sonarr_command("RescanSeries")

    except Exception as e:
        print("Error during Sonarr sync:", e)

    return updated_count

def sync_all():
    """Performs full sync across both Radarr and Sonarr using currently active external disk."""
    active_disk = disk_manager.get_active_disk()
    ext_movies = active_disk.get("movies_path") if (active_disk and active_disk.get("connected")) else None
    ext_series = active_disk.get("series_path") if (active_disk and active_disk.get("connected")) else None
    
    r_count = sync_radarr(external_movies_path=ext_movies)
    s_count = sync_sonarr(external_series_path=ext_series)
    return {"radarr_updated": r_count, "sonarr_updated": s_count}

if __name__ == "__main__":
    res = sync_all()
    print("Sync complete:", res)
