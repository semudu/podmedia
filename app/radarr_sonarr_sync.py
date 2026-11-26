import os
import re
import sys
import glob
import time
import json
import sqlite3
import threading
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
RUN_DIR = os.path.join(PROJECT_ROOT, "run")
STATE_FILE = os.path.join(RUN_DIR, "media_sync_state.json")

VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".m4v", ".ts", ".webm",
    ".mov", ".wmv", ".iso", ".mpg", ".mpeg", ".flv",
}

_state_lock = threading.RLock()


def _default_state():
    return {
        "bootstrapped": False,
        "known_movies": {},      # str(tmdb_id) -> {tmdb_id, imdb_id, title, year, path, had_file}
        "known_series": {},      # str(tvdb_id) -> {tvdb_id, imdb_id, title, year, path, had_file}
        "excluded_movies": {},   # str(tmdb_id) -> {tmdb_id, imdb_id, title, year, reason, ts}
        "excluded_series": {},   # str(tvdb_id) -> {tvdb_id, imdb_id, title, year, reason, ts}
        "plex_guid_map": {},     # ratingKey -> {kind, tmdb, tvdb, imdb, title, year}
        "synced_watchlist": {},  # canonical_key -> {kind, tmdb, tvdb, imdb, plex_guid, title, year, in_plex, in_trakt}
    }


def load_media_sync_state():
    with _state_lock:
        base = _default_state()
        if not os.path.exists(STATE_FILE):
            return base
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                base["bootstrapped"] = bool(data.get("bootstrapped", False))
                for k in ("known_movies", "known_series", "excluded_movies", "excluded_series", "plex_guid_map", "synced_watchlist"):
                    if isinstance(data.get(k), dict):
                        base[k] = data[k]
        except Exception:
            pass
        return base


def save_media_sync_state(state):
    with _state_lock:
        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        except Exception:
            pass


def _get_plex_token():
    prefs_path = os.path.join(
        PROJECT_ROOT, "services", "plex", "config",
        "Library", "Application Support", "Plex Media Server", "Preferences.xml"
    )
    if os.path.exists(prefs_path):
        try:
            root = ET.parse(prefs_path).getroot()
            return root.attrib.get("PlexOnlineToken", "")
        except Exception:
            pass
    return ""


def _get_trakt_headers():
    pytrakt_path = os.path.join(PROJECT_ROOT, "services", "tools", "plextraktsync", "config", ".pytrakt.json")
    if not os.path.exists(pytrakt_path):
        return None
    try:
        with open(pytrakt_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cid = cfg.get("CLIENT_ID")
        tok = cfg.get("OAUTH_TOKEN")
        if cid and tok:
            return {
                "Content-Type": "application/json",
                "trakt-api-version": "2",
                "trakt-api-key": cid,
                "Authorization": f"Bearer {tok}",
                "User-Agent": "PodMedia/1.0",
            }
    except Exception:
        pass
    return None


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


def _api_request(base_url, api_key, endpoint, method="GET", payload=None, timeout=4):
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"X-Api-Key": api_key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def _is_service_online(base_url, api_key):
    if not api_key:
        return False
    try:
        _api_request(base_url, api_key, "/api/v3/system/status")
        return True
    except Exception:
        return False


def _has_video_files(folder_path):
    """Returns True if folder_path contains at least one video media file."""
    if not folder_path or not os.path.exists(folder_path):
        return False
    if os.path.isfile(folder_path):
        return os.path.splitext(folder_path)[1].lower() in VIDEO_EXTS
    try:
        for root, _, files in os.walk(folder_path):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in VIDEO_EXTS:
                    return True
    except Exception:
        return True  # On transient I/O error, assume present as a failsafe
    return False


def trigger_radarr_command(name, body=None):
    """Sends a command to Radarr REST API if container is currently active."""
    api_key = _get_api_key(os.path.join(RADARR_DIR, "config.xml"))
    if not api_key:
        return
    url = "http://127.0.0.1:7878/api/v3/command"
    payload = {"name": name}
    if body:
        payload.update(body)
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception:
        pass


def trigger_sonarr_command(name, body=None):
    """Sends a command to Sonarr REST API if container is currently active."""
    api_key = _get_api_key(os.path.join(SONARR_DIR, "config.xml"))
    if not api_key:
        return
    url = "http://127.0.0.1:8989/api/v3/command"
    payload = {"name": name}
    if body:
        payload.update(body)
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Api-Key": api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception:
        pass


def resolve_plex_watchlist_items(plex_token=None):
    """Fetches the full Plex Watchlist and resolves external IDs (tmdb, tvdb, imdb) per ratingKey using local cache."""
    tok = plex_token or _get_plex_token()
    if not tok:
        return []

    raw_items = []
    start = 0
    page_size = 50
    while start < 500:
        req = urllib.request.Request(
            "https://discover.provider.plex.tv/library/sections/watchlist/all",
            headers={
                "X-Plex-Token": tok,
                "Accept": "application/json",
                "X-Plex-Client-Identifier": "podmedia-sync",
                "X-Plex-Container-Start": str(start),
                "X-Plex-Container-Size": str(page_size),
                "User-Agent": "PodMedia/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pdata = json.loads(resp.read().decode("utf-8"))
        mc = pdata.get("MediaContainer", {}) or {}
        meta = mc.get("Metadata", []) or []
        raw_items.extend(meta)
        total_size = int(mc.get("totalSize", len(meta)))
        start += len(meta)
        if not meta or start >= total_size:
            break

    with _state_lock:
        state = load_media_sync_state()
        guid_map = state["plex_guid_map"]
        dirty = False

        for m in raw_items:
            rk = str(m.get("ratingKey") or "").strip()
            if not rk:
                continue
            if rk not in guid_map:
                info = {
                    "kind": "show" if m.get("type") in ("show", "series") else "movie",
                    "tmdb": None,
                    "tvdb": None,
                    "imdb": None,
                    "title": m.get("title", ""),
                    "year": m.get("year"),
                }
                try:
                    mreq = urllib.request.Request(
                        f"https://discover.provider.plex.tv/library/metadata/{rk}?includeGuids=1",
                        headers={
                            "X-Plex-Token": tok,
                            "Accept": "application/json",
                            "X-Plex-Client-Identifier": "podmedia-sync",
                            "User-Agent": "PodMedia/1.0",
                        },
                    )
                    with urllib.request.urlopen(mreq, timeout=6) as mresp:
                        md = json.loads(mresp.read().decode("utf-8"))
                        full_m = (md.get("MediaContainer", {}).get("Metadata") or [{}])[0]
                        if full_m.get("year") and not info["year"]:
                            info["year"] = full_m.get("year")
                        for g in full_m.get("Guid") or []:
                            gid = str(g.get("id") or "")
                            if gid.startswith("tmdb://"):
                                try:
                                    info["tmdb"] = int(gid.replace("tmdb://", ""))
                                except Exception:
                                    pass
                            elif gid.startswith("tvdb://"):
                                try:
                                    info["tvdb"] = int(gid.replace("tvdb://", ""))
                                except Exception:
                                    pass
                            elif gid.startswith("imdb://"):
                                info["imdb"] = gid.replace("imdb://", "")
                except Exception:
                    pass
                guid_map[rk] = info
                dirty = True

        if dirty:
            save_media_sync_state(state)

        resolved = []
        for m in raw_items:
            rk = str(m.get("ratingKey") or "").strip()
            if not rk:
                continue
            cached = dict(guid_map.get(rk) or {})
            cached["ratingKey"] = rk
            cached["title"] = m.get("title") or cached.get("title") or ""
            cached["year"] = m.get("year") or cached.get("year")
            cached["kind"] = "show" if m.get("type") in ("show", "series") else cached.get("kind", "movie")
            resolved.append(cached)
        return resolved


def is_media_excluded(kind, tmdb_id=None, tvdb_id=None, imdb_id=None):
    """Checks local persistent exclusion registry (and returns exclusion record dict or None)."""
    state = load_media_sync_state()
    if kind == "movie":
        excl = state["excluded_movies"]
        if tmdb_id and str(tmdb_id) in excl:
            return excl[str(tmdb_id)]
        if imdb_id:
            for v in excl.values():
                if v.get("imdb_id") == str(imdb_id):
                    return v
    elif kind == "show":
        excl = state["excluded_series"]
        if tvdb_id and str(tvdb_id) in excl:
            return excl[str(tvdb_id)]
        if imdb_id:
            for v in excl.values():
                if v.get("imdb_id") == str(imdb_id):
                    return v
    return None


def ensure_radarr_exclusion(tmdb_id, title="", year=0, imdb_id="", reason="deleted"):
    """Adds tmdb_id to both local state and Radarr's ImportExclusions (via API if online, or SQLite if offline)."""
    if not tmdb_id:
        return False
    tmdb_int = int(tmdb_id)
    with _state_lock:
        state = load_media_sync_state()
        existing = state["excluded_movies"].get(str(tmdb_int), {})
        state["excluded_movies"][str(tmdb_int)] = {
            "tmdb_id": tmdb_int,
            "imdb_id": str(imdb_id or existing.get("imdb_id") or ""),
            "title": str(title or existing.get("title") or tmdb_int),
            "year": int(year or existing.get("year") or 0),
            "reason": reason,
            "ts": existing.get("ts") or int(time.time()),
        }
        # Remove from synced_watchlist so two-way sync never resurrects it
        for k in list(state["synced_watchlist"].keys()):
            v = state["synced_watchlist"][k]
            if v.get("kind") == "movie" and (v.get("tmdb") == tmdb_int or (imdb_id and v.get("imdb") == str(imdb_id))):
                state["synced_watchlist"].pop(k, None)
        save_media_sync_state(state)

    api_key = _get_api_key(os.path.join(RADARR_DIR, "config.xml"))
    base_url = "http://127.0.0.1:7878"
    if _is_service_online(base_url, api_key):
        try:
            exclusions = _api_request(base_url, api_key, "/api/v3/exclusions", timeout=5) or []
            if not any(int(e.get("tmdbId") or 0) == tmdb_int for e in exclusions):
                _api_request(
                    base_url,
                    api_key,
                    "/api/v3/exclusions",
                    method="POST",
                    payload={
                        "tmdbId": tmdb_int,
                        "movieTitle": str(title or tmdb_int),
                        "movieYear": int(year or 0),
                    },
                    timeout=6,
                )
                print(f"🛡️ [RadarrSync] Added Import Exclusion: {title or tmdb_int} ({year or ''}) [tmdb:{tmdb_int}]")
            return True
        except Exception as e:
            print(f"⚠️ [RadarrSync] Could not add Radarr exclusion via API for {tmdb_int}: {e}")
            return False

    db_path = os.path.join(RADARR_DIR, "radarr.db")
    if os.path.exists(db_path):
        try:
            con = sqlite3.connect(db_path, timeout=5)
            cur = con.cursor()
            row = cur.execute("SELECT Id FROM ImportExclusions WHERE TmdbId = ?", (tmdb_int,)).fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO ImportExclusions (TmdbId, MovieTitle, MovieYear) VALUES (?, ?, ?)",
                    (tmdb_int, str(title or tmdb_int), int(year or 0)),
                )
                con.commit()
                print(f"🛡️ [RadarrSync] Added offline DB Import Exclusion: {title or tmdb_int} [tmdb:{tmdb_int}]")
            con.close()
            return True
        except Exception:
            pass
    return False


def ensure_sonarr_exclusion(tvdb_id, title="", year=0, imdb_id="", reason="deleted"):
    """Adds tvdb_id to both local state and Sonarr's ImportListExclusions (via API if online, or SQLite if offline)."""
    if not tvdb_id:
        return False
    tvdb_int = int(tvdb_id)
    with _state_lock:
        state = load_media_sync_state()
        existing = state["excluded_series"].get(str(tvdb_int), {})
        state["excluded_series"][str(tvdb_int)] = {
            "tvdb_id": tvdb_int,
            "imdb_id": str(imdb_id or existing.get("imdb_id") or ""),
            "title": str(title or existing.get("title") or tvdb_int),
            "year": int(year or existing.get("year") or 0),
            "reason": reason,
            "ts": existing.get("ts") or int(time.time()),
        }
        for k in list(state["synced_watchlist"].keys()):
            v = state["synced_watchlist"][k]
            if v.get("kind") == "show" and (v.get("tvdb") == tvdb_int or (imdb_id and v.get("imdb") == str(imdb_id))):
                state["synced_watchlist"].pop(k, None)
        save_media_sync_state(state)

    api_key = _get_api_key(os.path.join(SONARR_DIR, "config.xml"))
    base_url = "http://127.0.0.1:8989"
    if _is_service_online(base_url, api_key):
        try:
            exclusions = _api_request(base_url, api_key, "/api/v3/importlistexclusion", timeout=5) or []
            if not any(int(e.get("tvdbId") or 0) == tvdb_int for e in exclusions):
                _api_request(
                    base_url,
                    api_key,
                    "/api/v3/importlistexclusion",
                    method="POST",
                    payload={"tvdbId": tvdb_int, "title": str(title or tvdb_int)},
                    timeout=6,
                )
                print(f"🛡️ [SonarrSync] Added Import List Exclusion: {title or tvdb_int} [tvdb:{tvdb_int}]")
            return True
        except Exception as e:
            print(f"⚠️ [SonarrSync] Could not add Sonarr exclusion via API for {tvdb_int}: {e}")
            return False

    db_path = os.path.join(SONARR_DIR, "sonarr.db")
    if os.path.exists(db_path):
        try:
            con = sqlite3.connect(db_path, timeout=5)
            cur = con.cursor()
            row = cur.execute("SELECT Id FROM ImportListExclusions WHERE TvdbId = ?", (tvdb_int,)).fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO ImportListExclusions (TvdbId, Title) VALUES (?, ?)",
                    (tvdb_int, str(title or tvdb_int)),
                )
                con.commit()
                print(f"🛡️ [SonarrSync] Added offline DB Import List Exclusion: {title or tvdb_int} [tvdb:{tvdb_int}]")
            con.close()
            return True
        except Exception:
            pass
    return False


def unexclude_media(kind, item_id):
    """Removes an exclusion when the user intentionally re-adds a movie/show (e.g. via Hub Search '+ Add & Download')."""
    if not item_id:
        return
    item_int = int(item_id)
    with _state_lock:
        state = load_media_sync_state()
        if kind == "movie":
            state["excluded_movies"].pop(str(item_int), None)
        elif kind == "show":
            state["excluded_series"].pop(str(item_int), None)
        save_media_sync_state(state)

    if kind == "movie":
        api_key = _get_api_key(os.path.join(RADARR_DIR, "config.xml"))
        base_url = "http://127.0.0.1:7878"
        if _is_service_online(base_url, api_key):
            try:
                exclusions = _api_request(base_url, api_key, "/api/v3/exclusions", timeout=5) or []
                for e in exclusions:
                    if int(e.get("tmdbId") or 0) == item_int and e.get("id"):
                        _api_request(base_url, api_key, f"/api/v3/exclusions/{e['id']}", method="DELETE", timeout=5)
            except Exception:
                pass
    elif kind == "show":
        api_key = _get_api_key(os.path.join(SONARR_DIR, "config.xml"))
        base_url = "http://127.0.0.1:8989"
        if _is_service_online(base_url, api_key):
            try:
                exclusions = _api_request(base_url, api_key, "/api/v3/importlistexclusion", timeout=5) or []
                for e in exclusions:
                    if int(e.get("tvdbId") or 0) == item_int and e.get("id"):
                        _api_request(base_url, api_key, f"/api/v3/importlistexclusion/{e['id']}", method="DELETE", timeout=5)
            except Exception:
                pass


def cancel_arr_queue_items(kind, tmdb_id=None, tvdb_id=None, arr_internal_id=None):
    """Cancels and removes any active download in Radarr/Sonarr queue (and qBittorrent) for a deleted media item."""
    if kind == "movie":
        api_key = _get_api_key(os.path.join(RADARR_DIR, "config.xml"))
        base_url = "http://127.0.0.1:7878"
        if not _is_service_online(base_url, api_key):
            return
        try:
            target_movie_ids = set()
            if arr_internal_id:
                target_movie_ids.add(int(arr_internal_id))
            if tmdb_id:
                found = _api_request(base_url, api_key, f"/api/v3/movie?tmdbId={int(tmdb_id)}", timeout=4) or []
                for m in found:
                    if m.get("id"):
                        target_movie_ids.add(int(m["id"]))
            if not target_movie_ids:
                return
            qdata = _api_request(base_url, api_key, "/api/v3/queue?pageSize=100&includeUnknownMovieItems=false", timeout=5) or {}
            for rec in qdata.get("records") or []:
                if rec.get("movieId") in target_movie_ids and rec.get("id"):
                    _api_request(
                        base_url,
                        api_key,
                        f"/api/v3/queue/{rec['id']}?removeFromClient=true&blocklist=false",
                        method="DELETE",
                        timeout=6,
                    )
                    print(f"🛑 [RadarrSync] Removed active download from queue: {rec.get('title')}")
        except Exception:
            pass

    elif kind == "show":
        api_key = _get_api_key(os.path.join(SONARR_DIR, "config.xml"))
        base_url = "http://127.0.0.1:8989"
        if not _is_service_online(base_url, api_key):
            return
        try:
            target_series_ids = set()
            if arr_internal_id:
                target_series_ids.add(int(arr_internal_id))
            if tvdb_id:
                found = _api_request(base_url, api_key, f"/api/v3/series?tvdbId={int(tvdb_id)}", timeout=4) or []
                for s in found:
                    if s.get("id"):
                        target_series_ids.add(int(s["id"]))
            if not target_series_ids:
                return
            qdata = _api_request(base_url, api_key, "/api/v3/queue?pageSize=100", timeout=5) or {}
            for rec in qdata.get("records") or []:
                if rec.get("seriesId") in target_series_ids and rec.get("id"):
                    _api_request(
                        base_url,
                        api_key,
                        f"/api/v3/queue/{rec['id']}?removeFromClient=true&blocklist=false",
                        method="DELETE",
                        timeout=6,
                    )
                    print(f"🛑 [SonarrSync] Removed active download from queue: {rec.get('title')}")
        except Exception:
            pass


def remove_from_watchlists(kind, tmdb_id=None, tvdb_id=None, imdb_id=None, plex_guid=None, title="", year=None):
    """Removes a movie or show from BOTH Plex Watchlist and Trakt Watchlist so neither can re-populate the other."""
    plex_token = _get_plex_token()
    if plex_token:
        target_rks = set()
        if plex_guid:
            target_rks.add(str(plex_guid).rsplit("/", 1)[-1])

        state = load_media_sync_state()
        for rk, info in (state.get("plex_guid_map") or {}).items():
            if info.get("kind") != kind:
                continue
            if tmdb_id and info.get("tmdb") == int(tmdb_id):
                target_rks.add(rk)
            elif tvdb_id and info.get("tvdb") == int(tvdb_id):
                target_rks.add(rk)
            elif imdb_id and info.get("imdb") == str(imdb_id):
                target_rks.add(rk)

        # If not found in local cache yet, check live Plex Watchlist
        if not target_rks and (tmdb_id or tvdb_id or imdb_id or title):
            try:
                live_plex = resolve_plex_watchlist_items(plex_token)
                for p in live_plex:
                    if p.get("kind") != kind:
                        continue
                    if (
                        (tmdb_id and p.get("tmdb") == int(tmdb_id))
                        or (tvdb_id and p.get("tvdb") == int(tvdb_id))
                        or (imdb_id and p.get("imdb") == str(imdb_id))
                        or (title and p.get("title", "").strip().lower() == title.strip().lower() and (not year or not p.get("year") or int(p["year"]) == int(year)))
                    ):
                        target_rks.add(p["ratingKey"])
            except Exception:
                pass

        for rk in target_rks:
            try:
                rm_req = urllib.request.Request(
                    f"https://discover.provider.plex.tv/actions/removeFromWatchlist?ratingKey={rk}",
                    headers={
                        "X-Plex-Token": plex_token,
                        "X-Plex-Client-Identifier": "podmedia-sync",
                        "User-Agent": "PodMedia/1.0",
                    },
                    method="PUT",
                )
                with urllib.request.urlopen(rm_req, timeout=6):
                    print(f"🧹 [Watchlist] Removed from Plex Watchlist: {title or rk}")
            except Exception:
                pass

    trakt_headers = _get_trakt_headers()
    if trakt_headers:
        ids_obj = {}
        if tmdb_id:
            ids_obj["tmdb"] = int(tmdb_id)
        if tvdb_id:
            ids_obj["tvdb"] = int(tvdb_id)
        if imdb_id:
            ids_obj["imdb"] = str(imdb_id)
        if ids_obj:
            payload = {"movies": [{"ids": ids_obj}]} if kind == "movie" else {"shows": [{"ids": ids_obj}]}
            try:
                tr_rm = urllib.request.Request(
                    "https://api.trakt.tv/sync/watchlist/remove",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=trakt_headers,
                    method="POST",
                )
                with urllib.request.urlopen(tr_rm, timeout=6):
                    pass
            except Exception:
                pass


def exclude_and_purge_media(
    kind,
    tmdb_id=None,
    tvdb_id=None,
    imdb_id=None,
    plex_guid=None,
    title="",
    year=0,
    reason="deleted",
    cancel_queue=True,
    arr_internal_id=None,
):
    """Full protection shield: adds Import Exclusion, cancels active queue downloads, and removes from Plex & Trakt Watchlists."""
    if kind == "movie" and tmdb_id:
        ensure_radarr_exclusion(tmdb_id, title=title, year=year, imdb_id=imdb_id, reason=reason)
        if cancel_queue:
            cancel_arr_queue_items("movie", tmdb_id=tmdb_id, arr_internal_id=arr_internal_id)
        remove_from_watchlists("movie", tmdb_id=tmdb_id, imdb_id=imdb_id, plex_guid=plex_guid, title=title, year=year)
    elif kind == "show" and tvdb_id:
        ensure_sonarr_exclusion(tvdb_id, title=title, year=year, imdb_id=imdb_id, reason=reason)
        if cancel_queue:
            cancel_arr_queue_items("show", tvdb_id=tvdb_id, arr_internal_id=arr_internal_id)
        remove_from_watchlists("show", tvdb_id=tvdb_id, imdb_id=imdb_id, plex_guid=plex_guid, title=title, year=year)


def _bootstrap_historical_deletions(state, current_movie_tmdbs, current_series_tvdbs):
    """One-time bootstrap: imports previously deleted movies/series from Radarr/Sonarr logs so old deleted items never resurrect."""
    del_movie_re = re.compile(r"Deleted movie \[(.+?) \((\d{4})\)\]\[(tt\d+)?,\s*(\d+)\]")
    for lf in sorted(glob.glob(os.path.join(RADARR_DIR, "logs", "radarr*.txt"))):
        try:
            with open(lf, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "Deleted movie [" not in line:
                        continue
                    m = del_movie_re.search(line)
                    if m:
                        title, yr, imdb_id, tmdb_s = m.group(1), int(m.group(2)), m.group(3) or "", int(m.group(4))
                        if tmdb_s not in current_movie_tmdbs and str(tmdb_s) not in state["excluded_movies"]:
                            state["excluded_movies"][str(tmdb_s)] = {
                                "tmdb_id": tmdb_s,
                                "imdb_id": imdb_id,
                                "title": title,
                                "year": yr,
                                "reason": "historical_radarr_delete",
                                "ts": int(time.time()),
                            }
        except Exception:
            pass

    del_series_re = re.compile(r"Deleted series \[(\d+)\]\[(.+?)\]")
    for lf in sorted(glob.glob(os.path.join(SONARR_DIR, "logs", "sonarr*.txt"))):
        try:
            with open(lf, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "Deleted series [" not in line:
                        continue
                    m = del_series_re.search(line)
                    if m:
                        tvdb_s, title = int(m.group(1)), m.group(2)
                        if tvdb_s not in current_series_tvdbs and str(tvdb_s) not in state["excluded_series"]:
                            state["excluded_series"][str(tvdb_s)] = {
                                "tvdb_id": tvdb_s,
                                "imdb_id": "",
                                "title": title,
                                "year": 0,
                                "reason": "historical_sonarr_delete",
                                "ts": int(time.time()),
                            }
        except Exception:
            pass


def reconcile_arr_deletions():
    """Detects movies/series deleted directly inside Radarr or Sonarr UI (even when 'Add List Exclusion' was unchecked)
    and immediately adds Import Exclusions + removes them from Plex & Trakt Watchlists BEFORE ImportListSync runs."""
    with _state_lock:
        state = load_media_sync_state()

    radarr_key = _get_api_key(os.path.join(RADARR_DIR, "config.xml"))
    radarr_online = _is_service_online("http://127.0.0.1:7878", radarr_key)
    sonarr_key = _get_api_key(os.path.join(SONARR_DIR, "config.xml"))
    sonarr_online = _is_service_online("http://127.0.0.1:8989", sonarr_key)

    movies = None
    series_list = None
    if radarr_online:
        try:
            movies = _api_request("http://127.0.0.1:7878", radarr_key, "/api/v3/movie", timeout=5)
        except Exception:
            movies = None
    if sonarr_online:
        try:
            series_list = _api_request("http://127.0.0.1:8989", sonarr_key, "/api/v3/series", timeout=5)
        except Exception:
            series_list = None

    # One-time bootstrap when both services (or DBs) are first inspected
    if not state["bootstrapped"] and (movies is not None or series_list is not None):
        cur_m_ids = {int(m["tmdbId"]) for m in (movies or []) if m.get("tmdbId")}
        cur_s_ids = {int(s["tvdbId"]) for s in (series_list or []) if s.get("tvdbId")}
        _bootstrap_historical_deletions(state, cur_m_ids, cur_s_ids)
        state["bootstrapped"] = True
        save_media_sync_state(state)
        # Push bootstrapped exclusions to Radarr/Sonarr so their native PlexImport lists also respect them
        for v in list(state["excluded_movies"].values()):
            ensure_radarr_exclusion(v["tmdb_id"], title=v.get("title", ""), year=v.get("year", 0), imdb_id=v.get("imdb_id", ""), reason=v.get("reason", "deleted"))
        for v in list(state["excluded_series"].values()):
            ensure_sonarr_exclusion(v["tvdb_id"], title=v.get("title", ""), year=v.get("year", 0), imdb_id=v.get("imdb_id", ""), reason=v.get("reason", "deleted"))

    # 1. Reconcile Radarr movies
    if movies is not None:
        with _state_lock:
            state = load_media_sync_state()
            prev_known = dict(state["known_movies"])
        current_map = {}
        for m in movies:
            tmdb = m.get("tmdbId")
            if not tmdb:
                continue
            k = str(int(tmdb))
            current_map[k] = {
                "id": m.get("id"),
                "tmdb_id": int(tmdb),
                "imdb_id": str(m.get("imdbId") or ""),
                "title": m.get("title", ""),
                "year": int(m.get("year") or 0),
                "path": m.get("path", ""),
                "had_file": bool(m.get("hasFile") or m.get("movieFileId")),
                "monitored": bool(m.get("monitored")),
            }

        # Movies that WERE in Radarr and are now GONE -> user deleted them in Radarr UI!
        deleted_keys = set(prev_known.keys()) - set(current_map.keys())
        for dk in deleted_keys:
            dm = prev_known[dk]
            print(f"🗑️ [RadarrSync] Detected movie deleted in Radarr UI: '{dm.get('title')}' ({dm.get('year')}). Excluding & removing from Watchlists...")
            exclude_and_purge_media(
                "movie",
                tmdb_id=dm.get("tmdb_id"),
                imdb_id=dm.get("imdb_id"),
                title=dm.get("title", ""),
                year=dm.get("year", 0),
                reason="deleted_from_radarr",
                cancel_queue=True,
                arr_internal_id=dm.get("id"),
            )

        # Movies that previously had a downloaded file and lost it + became unmonitored (e.g. deleted in Plex & caught by Radarr DiskScan first)
        for k, cm in current_map.items():
            pm = prev_known.get(k)
            if pm and pm.get("had_file") and not cm.get("had_file") and not cm.get("monitored"):
                if not is_media_excluded("movie", tmdb_id=cm.get("tmdb_id"), imdb_id=cm.get("imdb_id")):
                    print(f"🗑️ [RadarrSync] Detected movie file removed & unmonitored: '{cm.get('title')}'. Excluding & removing from Watchlists...")
                    exclude_and_purge_media(
                        "movie",
                        tmdb_id=cm.get("tmdb_id"),
                        imdb_id=cm.get("imdb_id"),
                        title=cm.get("title", ""),
                        year=cm.get("year", 0),
                        reason="deleted_from_disk_or_plex",
                        cancel_queue=False,
                        arr_internal_id=cm.get("id"),
                    )

        # Also sync any manual exclusions added directly inside Radarr UI into local state
        try:
            r_excls = _api_request("http://127.0.0.1:7878", radarr_key, "/api/v3/exclusions", timeout=5) or []
        except Exception:
            r_excls = []

        with _state_lock:
            state = load_media_sync_state()
            for e in r_excls:
                tid = e.get("tmdbId")
                if tid and str(int(tid)) not in state["excluded_movies"]:
                    state["excluded_movies"][str(int(tid))] = {
                        "tmdb_id": int(tid),
                        "imdb_id": "",
                        "title": e.get("movieTitle", str(tid)),
                        "year": int(e.get("movieYear") or 0),
                        "reason": "radarr_exclusion",
                        "ts": int(time.time()),
                    }
            # If user manually re-added a previously excluded movie inside Radarr with monitored=True, clear local exclusion
            for k, cm in current_map.items():
                if k not in prev_known and cm.get("monitored"):
                    state["excluded_movies"].pop(k, None)
            state["known_movies"] = current_map
            save_media_sync_state(state)

    # 2. Reconcile Sonarr series
    if series_list is not None:
        with _state_lock:
            state = load_media_sync_state()
            prev_known_s = dict(state["known_series"])
        current_s_map = {}
        for s in series_list:
            tvdb = s.get("tvdbId")
            if not tvdb:
                continue
            stats = s.get("statistics") or {}
            k = str(int(tvdb))
            current_s_map[k] = {
                "id": s.get("id"),
                "tvdb_id": int(tvdb),
                "imdb_id": str(s.get("imdbId") or ""),
                "title": s.get("title", ""),
                "year": int(s.get("year") or 0),
                "path": s.get("path", ""),
                "had_file": bool((stats.get("episodeFileCount") or 0) > 0),
                "monitored": bool(s.get("monitored")),
            }

        deleted_s_keys = set(prev_known_s.keys()) - set(current_s_map.keys())
        for dk in deleted_s_keys:
            ds = prev_known_s[dk]
            print(f"🗑️ [SonarrSync] Detected series deleted in Sonarr UI: '{ds.get('title')}'. Excluding & removing from Watchlists...")
            exclude_and_purge_media(
                "show",
                tvdb_id=ds.get("tvdb_id"),
                imdb_id=ds.get("imdb_id"),
                title=ds.get("title", ""),
                year=ds.get("year", 0),
                reason="deleted_from_sonarr",
                cancel_queue=True,
                arr_internal_id=ds.get("id"),
            )

        for k, cs in current_s_map.items():
            ps = prev_known_s.get(k)
            if ps and ps.get("had_file") and not cs.get("had_file") and not cs.get("monitored"):
                if not is_media_excluded("show", tvdb_id=cs.get("tvdb_id"), imdb_id=cs.get("imdb_id")):
                    print(f"🗑️ [SonarrSync] Detected series files removed & unmonitored: '{cs.get('title')}'. Excluding & removing from Watchlists...")
                    exclude_and_purge_media(
                        "show",
                        tvdb_id=cs.get("tvdb_id"),
                        imdb_id=cs.get("imdb_id"),
                        title=cs.get("title", ""),
                        year=cs.get("year", 0),
                        reason="deleted_from_disk_or_plex",
                        cancel_queue=False,
                        arr_internal_id=cs.get("id"),
                    )

        try:
            s_excls = _api_request("http://127.0.0.1:8989", sonarr_key, "/api/v3/importlistexclusion", timeout=5) or []
        except Exception:
            s_excls = []

        with _state_lock:
            state = load_media_sync_state()
            for e in s_excls:
                tid = e.get("tvdbId")
                if tid and str(int(tid)) not in state["excluded_series"]:
                    state["excluded_series"][str(int(tid))] = {
                        "tvdb_id": int(tid),
                        "imdb_id": "",
                        "title": e.get("title", str(tid)),
                        "year": 0,
                        "reason": "sonarr_exclusion",
                        "ts": int(time.time()),
                    }
            for k, cs in current_s_map.items():
                if k not in prev_known_s and cs.get("monitored"):
                    state["excluded_series"].pop(k, None)
            state["known_series"] = current_s_map
            save_media_sync_state(state)


def _map_sonarr_path_to_host(container_path, external_series_path, local_series_path):
    if not container_path:
        return None, False
    p = container_path.replace("\\", "/")
    if p.startswith("/series_external/") or p.startswith("/tv_external/"):
        if external_series_path and os.path.exists(external_series_path):
            rel = p.replace("/series_external/", "", 1).replace("/tv_external/", "", 1).strip("/")
            return os.path.join(external_series_path, *rel.split("/")), True
        return None, False
    if p.startswith("/series/") or p.startswith("/tv/"):
        rel = p.replace("/series/", "", 1).replace("/tv/", "", 1).strip("/")
        return os.path.join(local_series_path, *rel.split("/")), True
    return None, False


def sync_radarr(external_movies_path=None, local_movies_path=None):
    """
    Synchronizes Radarr movie paths between local (/movies) and external (/movies_external) storage,
    and ensures any movie deleted on disk/Plex or in Radarr is unmonitored, excluded, and purged from Watchlists.
    """
    db_path = os.path.join(RADARR_DIR, "radarr.db")
    if not os.path.exists(db_path):
        return 0

    if not local_movies_path:
        local_movies_path = os.path.join(LOCAL_MEDIA_DIR, "movies")

    # Catch any UI deletions first before updating state
    reconcile_arr_deletions()

    updated_count = 0
    api_key = _get_api_key(os.path.join(RADARR_DIR, "config.xml"))
    base_url = "http://127.0.0.1:7878"

    if _is_service_online(base_url, api_key):
        try:
            ext_folders = set()
            ext_drive_ready = False
            if external_movies_path and os.path.exists(external_movies_path):
                try:
                    ext_folders = {
                        f for f in os.listdir(external_movies_path)
                        if os.path.isdir(os.path.join(external_movies_path, f))
                    }
                    # Failsafe: never treat an empty external root folder as mass deletion
                    ext_drive_ready = len(ext_folders) > 0
                except Exception:
                    ext_drive_ready = False

            local_folders = set()
            if os.path.exists(local_movies_path):
                try:
                    local_folders = {
                        f for f in os.listdir(local_movies_path)
                        if os.path.isdir(os.path.join(local_movies_path, f))
                    }
                except Exception:
                    pass

            movies = _api_request(base_url, api_key, "/api/v3/movie", timeout=6) or []
            for m in movies:
                m_path = m.get("path", "")
                folder = m_path.rstrip("/").split("/")[-1] if m_path else ""
                changed = False

                if folder in ext_folders and m_path == f"/movies/{folder}":
                    m["path"] = f"/movies_external/{folder}"
                    changed = True
                elif folder in local_folders and m_path == f"/movies_external/{folder}":
                    m["path"] = f"/movies/{folder}"
                    changed = True

                cur_path = m.get("path", "")
                host_path = None
                is_drive_mounted = False
                if cur_path.startswith("/movies_external/"):
                    if ext_drive_ready:
                        rel = cur_path.replace("/movies_external/", "", 1).strip("/")
                        host_path = os.path.join(external_movies_path, rel)
                        is_drive_mounted = True
                elif cur_path.startswith("/movies/"):
                    if os.path.exists(local_movies_path):
                        rel = cur_path.replace("/movies/", "", 1).strip("/")
                        host_path = os.path.join(local_movies_path, rel)
                        is_drive_mounted = True

                had_media_file = bool(m.get("hasFile") or m.get("movieFileId", 0) != 0 or (m.get("movieFile") or {}).get("id"))
                # Detect deletion when folder is gone OR when a downloaded movie's video file was deleted in Plex
                file_deleted_on_disk = False
                if is_drive_mounted and host_path:
                    if not os.path.exists(host_path) and (had_media_file or str(m.get("tmdbId")) in load_media_sync_state()["known_movies"] and load_media_sync_state()["known_movies"][str(m.get("tmdbId"))].get("had_file")):
                        file_deleted_on_disk = True
                    elif had_media_file and not _has_video_files(host_path):
                        file_deleted_on_disk = True

                if file_deleted_on_disk:
                    mf_id = m.get("movieFileId") or (m.get("movieFile") or {}).get("id") or 0
                    if m.get("monitored") or mf_id != 0:
                        print(f"🗑️ [RadarrSync] Movie deleted on disk/Plex: '{m.get('title', folder)}'. Unmonitoring, excluding & cleaning Watchlists...")
                        m["monitored"] = False
                        changed = True
                        if mf_id:
                            try:
                                _api_request(base_url, api_key, f"/api/v3/moviefile/{mf_id}", method="DELETE", timeout=6)
                            except Exception:
                                pass
                        exclude_and_purge_media(
                            "movie",
                            tmdb_id=m.get("tmdbId"),
                            imdb_id=m.get("imdbId"),
                            title=m.get("title", folder),
                            year=m.get("year", 0),
                            reason="deleted_from_disk_or_plex",
                            cancel_queue=True,
                            arr_internal_id=m.get("id"),
                        )

                if changed:
                    _api_request(base_url, api_key, f"/api/v3/movie/{m['id']}", method="PUT", payload=m, timeout=6)
                    updated_count += 1

            reconcile_arr_deletions()
            if updated_count > 0:
                print(f"🎬 [RadarrSync] Updated {updated_count} movies via Radarr API.")
                trigger_radarr_command("DownloadedMoviesScan")
            return updated_count
        except Exception as e:
            print("Error during Radarr API sync:", e)
            return updated_count

    # Offline SQLite sync (when Radarr container is stopped, e.g. Video Mode only)
    try:
        con = sqlite3.connect(db_path, timeout=5)
        cur = con.cursor()

        cur.execute("SELECT Path FROM RootFolders")
        existing_roots = [r[0] for r in cur.fetchall()]
        if "/movies_external/" not in existing_roots and "/movies_external" not in existing_roots:
            cur.execute("INSERT INTO RootFolders (Path) VALUES ('/movies_external/')")

        ext_folders = []
        ext_drive_ready = False
        if external_movies_path and os.path.exists(external_movies_path):
            try:
                ext_folders = [
                    f for f in os.listdir(external_movies_path)
                    if os.path.isdir(os.path.join(external_movies_path, f))
                ]
                ext_drive_ready = len(ext_folders) > 0
            except Exception:
                ext_folders = []

            for folder in ext_folders:
                cur.execute(
                    "UPDATE Movies SET Path = ? WHERE Path = ?",
                    (f"/movies_external/{folder}", f"/movies/{folder}"),
                )
                if cur.rowcount > 0:
                    updated_count += cur.rowcount

        if os.path.exists(local_movies_path):
            try:
                local_folders = [
                    f for f in os.listdir(local_movies_path)
                    if os.path.isdir(os.path.join(local_movies_path, f))
                ]
            except Exception:
                local_folders = []

            for folder in local_folders:
                cur.execute(
                    "UPDATE Movies SET Path = ? WHERE Path = ?",
                    (f"/movies/{folder}", f"/movies_external/{folder}"),
                )
                if cur.rowcount > 0:
                    updated_count += cur.rowcount

        cur.execute(
            """
            SELECT m.Id, m.Path, m.Monitored, m.MovieFileId, mm.TmdbId, mm.ImdbId, mm.Title, mm.Year
            FROM Movies m
            LEFT JOIN MovieMetadata mm ON mm.Id = m.MovieMetadataId
            """
        )
        all_movies = cur.fetchall()
        offline_purges = []
        for m_id, m_path, m_monitored, m_file_id, tmdb_id, imdb_id, title, year in all_movies:
            host_path = None
            is_drive_mounted = False
            rel = ""

            if m_path.startswith("/movies_external/"):
                if ext_drive_ready:
                    rel = m_path.replace("/movies_external/", "", 1).strip("/")
                    host_path = os.path.join(external_movies_path, rel)
                    is_drive_mounted = True
            elif m_path.startswith("/movies/"):
                if os.path.exists(local_movies_path):
                    rel = m_path.replace("/movies/", "", 1).strip("/")
                    host_path = os.path.join(local_movies_path, rel)
                    is_drive_mounted = True

            if is_drive_mounted and host_path and m_file_id != 0 and (not os.path.exists(host_path) or not _has_video_files(host_path)):
                print(f"🗑️ [RadarrSync] Movie deleted on disk/Plex (offline): '{title or rel}'. Unmonitoring & excluding...")
                cur.execute("UPDATE Movies SET Monitored = 0, MovieFileId = 0 WHERE Id = ?", (m_id,))
                cur.execute("DELETE FROM MovieFiles WHERE MovieId = ?", (m_id,))
                if tmdb_id:
                    ex_row = cur.execute("SELECT Id FROM ImportExclusions WHERE TmdbId = ?", (int(tmdb_id),)).fetchone()
                    if not ex_row:
                        cur.execute(
                            "INSERT INTO ImportExclusions (TmdbId, MovieTitle, MovieYear) VALUES (?, ?, ?)",
                            (int(tmdb_id), str(title or rel), int(year or 0)),
                        )
                    offline_purges.append((int(tmdb_id), str(imdb_id or ""), str(title or rel), int(year or 0)))
                updated_count += 1

        con.commit()
        con.close()

        for tmdb_id, imdb_id, title, year in offline_purges:
            exclude_and_purge_media(
                "movie",
                tmdb_id=tmdb_id,
                imdb_id=imdb_id,
                title=title,
                year=year,
                reason="deleted_from_disk_or_plex",
                cancel_queue=False,
            )

        if updated_count > 0:
            print(f"🎬 [RadarrSync] Updated {updated_count} movie entries in Radarr database.")
            trigger_radarr_command("DownloadedMoviesScan")

    except Exception as e:
        print("Error during Radarr sync:", e)

    return updated_count


def sync_sonarr(external_series_path=None, local_series_path=None):
    """
    Synchronizes Sonarr series paths between local (/tv or /series) and external (/tv_external or /series_external),
    unmonitors individual episodes deleted in Plex, and excludes/purges whole series deleted on disk or in Sonarr.
    """
    db_path = os.path.join(SONARR_DIR, "sonarr.db")
    if not os.path.exists(db_path):
        return 0

    if not local_series_path:
        local_series_path = os.path.join(LOCAL_MEDIA_DIR, "series")

    reconcile_arr_deletions()

    updated_count = 0
    api_key = _get_api_key(os.path.join(SONARR_DIR, "config.xml"))
    base_url = "http://127.0.0.1:8989"

    if _is_service_online(base_url, api_key):
        try:
            ext_folders = set()
            ext_drive_ready = False
            if external_series_path and os.path.exists(external_series_path):
                try:
                    ext_folders = {
                        f for f in os.listdir(external_series_path)
                        if os.path.isdir(os.path.join(external_series_path, f))
                    }
                    ext_drive_ready = len(ext_folders) > 0
                except Exception:
                    ext_drive_ready = False

            local_folders = set()
            if os.path.exists(local_series_path):
                try:
                    local_folders = {
                        f for f in os.listdir(local_series_path)
                        if os.path.isdir(os.path.join(local_series_path, f))
                    }
                except Exception:
                    pass

            series_list = _api_request(base_url, api_key, "/api/v3/series", timeout=6) or []
            for s in series_list:
                s_path = s.get("path", "")
                folder = s_path.rstrip("/").split("/")[-1] if s_path else ""
                changed = False

                if folder in ext_folders and s_path in (f"/tv/{folder}", f"/series/{folder}"):
                    s["path"] = f"/tv_external/{folder}"
                    changed = True
                elif folder in local_folders and s_path in (f"/tv_external/{folder}", f"/series_external/{folder}"):
                    s["path"] = f"/tv/{folder}"
                    changed = True

                cur_path = s.get("path", "")
                host_path = None
                is_drive_mounted = False
                if cur_path.startswith("/series_external/") or cur_path.startswith("/tv_external/"):
                    if ext_drive_ready:
                        rel = cur_path.replace("/series_external/", "", 1).replace("/tv_external/", "", 1).strip("/")
                        host_path = os.path.join(external_series_path, rel)
                        is_drive_mounted = True
                elif cur_path.startswith("/series/") or cur_path.startswith("/tv/"):
                    if os.path.exists(local_series_path):
                        rel = cur_path.replace("/series/", "", 1).replace("/tv/", "", 1).strip("/")
                        host_path = os.path.join(local_series_path, rel)
                        is_drive_mounted = True

                stats = s.get("statistics") or {}
                ep_file_count = int(stats.get("episodeFileCount") or 0)

                if is_drive_mounted and host_path:
                    whole_series_deleted = (
                        (not os.path.exists(host_path) and ep_file_count > 0)
                        or (ep_file_count > 0 and not _has_video_files(host_path))
                    )
                    if whole_series_deleted:
                        if s.get("monitored") or ep_file_count > 0:
                            print(f"🗑️ [SonarrSync] Series deleted on disk/Plex: '{s.get('title', folder)}'. Unmonitoring, excluding & cleaning Watchlists...")
                            s["monitored"] = False
                            changed = True
                            try:
                                eps = _api_request(base_url, api_key, f"/api/v3/episode?seriesId={s['id']}", timeout=6) or []
                                ep_ids = [ep["id"] for ep in eps if ep.get("id") and ep.get("monitored")]
                                if ep_ids:
                                    _api_request(base_url, api_key, "/api/v3/episode/monitor", method="PUT", payload={"episodeIds": ep_ids, "monitored": False}, timeout=6)
                                efiles = _api_request(base_url, api_key, f"/api/v3/episodefile?seriesId={s['id']}", timeout=6) or []
                                for ef in efiles:
                                    if ef.get("id"):
                                        _api_request(base_url, api_key, f"/api/v3/episodefile/{ef['id']}", method="DELETE", timeout=6)
                            except Exception:
                                pass
                            exclude_and_purge_media(
                                "show",
                                tvdb_id=s.get("tvdbId"),
                                imdb_id=s.get("imdbId"),
                                title=s.get("title", folder),
                                year=s.get("year", 0),
                                reason="deleted_from_disk_or_plex",
                                cancel_queue=True,
                                arr_internal_id=s.get("id"),
                            )
                    elif ep_file_count > 0 and os.path.exists(host_path):
                        # Check individual episodes deleted in Plex while other episodes remain
                        try:
                            efiles = _api_request(base_url, api_key, f"/api/v3/episodefile?seriesId={s['id']}", timeout=5) or []
                            missing_ef_ids = set()
                            for ef in efiles:
                                ef_host, ef_mounted = _map_sonarr_path_to_host(
                                    ef.get("path", ""),
                                    external_series_path if ext_drive_ready else None,
                                    local_series_path,
                                )
                                if ef_mounted and ef_host and not os.path.exists(ef_host) and ef.get("id"):
                                    missing_ef_ids.add(int(ef["id"]))
                            if missing_ef_ids:
                                eps = _api_request(base_url, api_key, f"/api/v3/episode?seriesId={s['id']}", timeout=5) or []
                                unmon_ids = [ep["id"] for ep in eps if ep.get("episodeFileId") in missing_ef_ids and ep.get("id")]
                                if unmon_ids:
                                    _api_request(base_url, api_key, "/api/v3/episode/monitor", method="PUT", payload={"episodeIds": unmon_ids, "monitored": False}, timeout=6)
                                for ef_id in missing_ef_ids:
                                    try:
                                        _api_request(base_url, api_key, f"/api/v3/episodefile/{ef_id}", method="DELETE", timeout=5)
                                    except Exception:
                                        pass
                                print(f"🗑️ [SonarrSync] Unmonitored {len(unmon_ids)} deleted episode(s) in '{s.get('title', folder)}' so they won't re-download.")
                                updated_count += 1
                        except Exception:
                            pass

                if changed:
                    _api_request(base_url, api_key, f"/api/v3/series/{s['id']}", method="PUT", payload=s, timeout=6)
                    updated_count += 1

            reconcile_arr_deletions()
            if updated_count > 0:
                print(f"📺 [SonarrSync] Updated {updated_count} series via Sonarr API.")
                trigger_sonarr_command("RescanSeries")
            return updated_count
        except Exception as e:
            print("Error during Sonarr API sync:", e)
            return updated_count

    # Offline SQLite sync (when Sonarr container is stopped)
    try:
        con = sqlite3.connect(db_path, timeout=5)
        cur = con.cursor()

        cur.execute("SELECT Path FROM RootFolders")
        existing_roots = [r[0] for r in cur.fetchall()]
        if "/tv_external/" not in existing_roots and "/tv_external" not in existing_roots:
            cur.execute("INSERT INTO RootFolders (Path) VALUES ('/tv_external/')")
        if "/series_external/" not in existing_roots and "/series_external" not in existing_roots:
            cur.execute("INSERT INTO RootFolders (Path) VALUES ('/series_external/')")

        ext_folders = []
        ext_drive_ready = False
        if external_series_path and os.path.exists(external_series_path):
            try:
                ext_folders = [
                    f for f in os.listdir(external_series_path)
                    if os.path.isdir(os.path.join(external_series_path, f))
                ]
                ext_drive_ready = len(ext_folders) > 0
            except Exception:
                ext_folders = []

            for folder in ext_folders:
                cur.execute(
                    "UPDATE Series SET Path = ? WHERE Path = ? OR Path = ?",
                    (f"/tv_external/{folder}", f"/tv/{folder}", f"/series/{folder}"),
                )
                if cur.rowcount > 0:
                    updated_count += cur.rowcount

        if os.path.exists(local_series_path):
            try:
                local_folders = [
                    f for f in os.listdir(local_series_path)
                    if os.path.isdir(os.path.join(local_series_path, f))
                ]
            except Exception:
                local_folders = []

            for folder in local_folders:
                cur.execute(
                    "UPDATE Series SET Path = ? WHERE Path = ? OR Path = ?",
                    (f"/tv/{folder}", f"/tv_external/{folder}", f"/series_external/{folder}"),
                )
                if cur.rowcount > 0:
                    updated_count += cur.rowcount

        cur.execute("SELECT Id, TvdbId, ImDbId, Title, Year, Path, Monitored FROM Series")
        all_series = cur.fetchall()
        offline_s_purges = []
        for s_id, tvdb_id, imdb_id, title, year, s_path, s_monitored in all_series:
            host_path = None
            is_drive_mounted = False
            rel = ""

            if s_path.startswith("/series_external/") or s_path.startswith("/tv_external/"):
                if ext_drive_ready:
                    rel = s_path.replace("/series_external/", "", 1).replace("/tv_external/", "", 1).strip("/")
                    host_path = os.path.join(external_series_path, rel)
                    is_drive_mounted = True
            elif s_path.startswith("/series/") or s_path.startswith("/tv/"):
                if os.path.exists(local_series_path):
                    rel = s_path.replace("/series/", "", 1).replace("/tv/", "", 1).strip("/")
                    host_path = os.path.join(local_series_path, rel)
                    is_drive_mounted = True

            ep_files_cnt = cur.execute("SELECT COUNT(*) FROM EpisodeFiles WHERE SeriesId = ?", (s_id,)).fetchone()[0]
            if is_drive_mounted and host_path and ep_files_cnt > 0 and (not os.path.exists(host_path) or not _has_video_files(host_path)):
                print(f"🗑️ [SonarrSync] Series deleted on disk/Plex (offline): '{title or rel}'. Unmonitoring & excluding...")
                cur.execute("UPDATE Series SET Monitored = 0 WHERE Id = ?", (s_id,))
                cur.execute("UPDATE Episodes SET Monitored = 0, EpisodeFileId = 0 WHERE SeriesId = ?", (s_id,))
                cur.execute("DELETE FROM EpisodeFiles WHERE SeriesId = ?", (s_id,))
                if tvdb_id:
                    ex_row = cur.execute("SELECT Id FROM ImportListExclusions WHERE TvdbId = ?", (int(tvdb_id),)).fetchone()
                    if not ex_row:
                        cur.execute(
                            "INSERT INTO ImportListExclusions (TvdbId, Title) VALUES (?, ?)",
                            (int(tvdb_id), str(title or rel)),
                        )
                    offline_s_purges.append((int(tvdb_id), str(imdb_id or ""), str(title or rel), int(year or 0)))
                updated_count += 1

        con.commit()
        con.close()

        for tvdb_id, imdb_id, title, year in offline_s_purges:
            exclude_and_purge_media(
                "show",
                tvdb_id=tvdb_id,
                imdb_id=imdb_id,
                title=title,
                year=year,
                reason="deleted_from_disk_or_plex",
                cancel_queue=False,
            )

        if updated_count > 0:
            print(f"📺 [SonarrSync] Updated {updated_count} series entries in Sonarr database.")
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
