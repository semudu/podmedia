import os
import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import radarr_sonarr_sync
import podman_manager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_trakt_cache = {"ts": 0, "data": None}
_summary_cache = {"ts": 0, "data": None}


def _get_plex_integration(plex_running):
    prefs = os.path.join(
        PROJECT_ROOT, "services", "plex", "config",
        "Library", "Application Support", "Plex Media Server", "Preferences.xml"
    )
    info = {"connected": False, "username": "", "server_name": "", "online": bool(plex_running)}
    if os.path.exists(prefs):
        try:
            attrs = ET.parse(prefs).getroot().attrib
            tok = attrs.get("PlexOnlineToken", "")
            info["username"] = attrs.get("PlexOnlineUsername", "")
            info["server_name"] = attrs.get("FriendlyName", "")
            info["connected"] = bool(tok or info["username"])
        except Exception:
            pass
    return info


def _get_trakt_integration(trakt_running):
    cfg_dir = os.path.join(PROJECT_ROOT, "services", "tools", "plextraktsync", "config")
    pytrakt_path = os.path.join(cfg_dir, ".pytrakt.json")
    env_path = os.path.join(cfg_dir, ".env")
    configured = podman_manager.is_plextraktsync_configured()
    info = {
        "connected": configured,
        "username": "",
        "watchlist_count": None,
        "online": bool(trakt_running),
    }
    if not configured:
        return info

    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("TRAKT_USERNAME="):
                        info["username"] = line.strip().split("=", 1)[1].strip()
                        break
        except Exception:
            pass

    now = time.time()
    if _trakt_cache["data"] is not None and (now - _trakt_cache["ts"] < 60):
        info["watchlist_count"] = _trakt_cache["data"]
        return info

    try:
        with open(pytrakt_path, "r", encoding="utf-8") as f:
            trakt_cfg = json.load(f)
        client_id = trakt_cfg.get("CLIENT_ID")
        oauth_token = trakt_cfg.get("OAUTH_TOKEN")
        if client_id and oauth_token:
            req = urllib.request.Request(
                "https://api.trakt.tv/sync/watchlist",
                headers={
                    "Content-Type": "application/json",
                    "trakt-api-version": "2",
                    "trakt-api-key": client_id,
                    "Authorization": f"Bearer {oauth_token}",
                    "User-Agent": "PodMedia/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                wl = json.loads(resp.read().decode("utf-8"))
                info["watchlist_count"] = len(wl)
                _trakt_cache["ts"] = now
                _trakt_cache["data"] = len(wl)
    except Exception:
        if _trakt_cache["data"] is not None:
            info["watchlist_count"] = _trakt_cache["data"]
    return info


def _get_qbittorrent_downloads():
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/api/v2/torrents/info")
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        # Sort: active downloading first, then by added_on descending
        def _sort_key(t):
            state = t.get("state", "")
            is_dl = state in ("downloading", "forcedDL", "metaDL", "stalledDL", "queuedDL", "checkingDL")
            return (0 if is_dl else 1, -(t.get("added_on") or 0))
        raw.sort(key=_sort_key)

        items = []
        active_count = 0
        for t in raw[:15]:
            state = t.get("state", "")
            progress = round((t.get("progress") or 0) * 100, 1)
            dlspeed_mb = round((t.get("dlspeed") or 0) / (1024 * 1024), 2)
            eta = t.get("eta", -1)
            if eta is None or eta >= 8640000 or eta < 0:
                eta = 0
            is_active = progress < 100 and state not in ("pausedDL", "stoppedDL", "error", "missingFiles")
            if is_active:
                active_count += 1
            items.append({
                "name": t.get("name", ""),
                "progress": progress,
                "speed_mbps": dlspeed_mb,
                "eta": eta,
                "state": state,
                "size_gb": round((t.get("size") or 0) / (1024 ** 3), 2),
                "category": t.get("category", ""),
                "is_active": is_active,
            })
        return {"online": True, "active_count": active_count, "total_count": len(raw), "items": items}
    except Exception:
        return {"online": False, "active_count": 0, "total_count": 0, "items": []}


def _get_radarr_library():
    key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.RADARR_DIR, "config.xml"))
    if not key:
        return {"online": False, "total": 0, "downloaded": 0, "missing": 0, "items": []}
    try:
        movies = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", key, "/api/v3/movie", timeout=4) or []
        items = []
        downloaded = 0
        for m in movies:
            has_file = bool(m.get("hasFile"))
            if has_file:
                downloaded += 1
            poster = ""
            for img in m.get("images") or []:
                if img.get("coverType") == "poster" and img.get("remoteUrl"):
                    poster = img["remoteUrl"]
                    break
            items.append({
                "type": "movie",
                "id": m.get("tmdbId"),
                "title": m.get("title", ""),
                "year": m.get("year"),
                "has_file": has_file,
                "monitored": bool(m.get("monitored")),
                "size_gb": round((m.get("sizeOnDisk") or 0) / (1024 ** 3), 2),
                "added": m.get("added", ""),
                "poster": poster,
            })
        return {
            "online": True,
            "total": len(movies),
            "downloaded": downloaded,
            "missing": len(movies) - downloaded,
            "items": items,
        }
    except Exception:
        return {"online": False, "total": 0, "downloaded": 0, "missing": 0, "items": []}


def _get_sonarr_library():
    key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.SONARR_DIR, "config.xml"))
    if not key:
        return {"online": False, "total": 0, "downloaded": 0, "missing": 0, "items": []}
    try:
        series = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", key, "/api/v3/series", timeout=4) or []
        items = []
        downloaded = 0
        for s in series:
            stats = s.get("statistics") or {}
            ep_files = stats.get("episodeFileCount", 0) or 0
            ep_total = stats.get("episodeCount", 0) or 0
            has_file = ep_files > 0
            if has_file:
                downloaded += 1
            poster = ""
            for img in s.get("images") or []:
                if img.get("coverType") == "poster" and img.get("remoteUrl"):
                    poster = img["remoteUrl"]
                    break
            items.append({
                "type": "show",
                "id": s.get("tvdbId"),
                "title": s.get("title", ""),
                "year": s.get("year"),
                "has_file": has_file,
                "monitored": bool(s.get("monitored")),
                "episodes_have": ep_files,
                "episodes_total": ep_total,
                "size_gb": round((stats.get("sizeOnDisk") or 0) / (1024 ** 3), 2),
                "added": s.get("added", ""),
                "poster": poster,
            })
        return {
            "online": True,
            "total": len(series),
            "downloaded": downloaded,
            "missing": len(series) - downloaded,
            "items": items,
        }
    except Exception:
        return {"online": False, "total": 0, "downloaded": 0, "missing": 0, "items": []}


def get_hub_summary(force=False):
    now = time.time()
    if not force and _summary_cache["data"] is not None and (now - _summary_cache["ts"] < 4):
        return _summary_cache["data"]

    containers, _ = podman_manager.get_container_states()
    plex_int = _get_plex_integration(containers.get("media_center_plex", False))
    trakt_int = _get_trakt_integration(containers.get("media_center_plextraktsync", False))
    downloads = _get_qbittorrent_downloads() if containers.get("media_center_qbittorrent") else {"online": False, "active_count": 0, "total_count": 0, "items": []}
    radarr = _get_radarr_library() if containers.get("media_center_radarr") else {"online": False, "total": 0, "downloaded": 0, "missing": 0, "items": []}
    sonarr = _get_sonarr_library() if containers.get("media_center_sonarr") else {"online": False, "total": 0, "downloaded": 0, "missing": 0, "items": []}

    combined_items = radarr["items"] + sonarr["items"]
    combined_items.sort(key=lambda x: x.get("added") or "", reverse=True)

    payload = {
        "integrations": {
            "plex": plex_int,
            "trakt": trakt_int,
        },
        "library": {
            "radarr_online": radarr["online"],
            "sonarr_online": sonarr["online"],
            "movies_total": radarr["total"],
            "movies_downloaded": radarr["downloaded"],
            "movies_missing": radarr["missing"],
            "shows_total": sonarr["total"],
            "shows_downloaded": sonarr["downloaded"],
            "shows_missing": sonarr["missing"],
            "items": combined_items[:120],
        },
        "downloads": downloads,
    }
    _summary_cache["ts"] = now
    _summary_cache["data"] = payload
    return payload


def search_media(term, media_type="all"):
    term = (term or "").strip()
    if not term:
        return {"results": [], "radarr_online": False, "sonarr_online": False}

    results = []
    radarr_online = False
    sonarr_online = False
    q = urllib.parse.quote(term)

    if media_type in ("all", "movie"):
        r_key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.RADARR_DIR, "config.xml"))
        if r_key and radarr_sonarr_sync._is_service_online("http://127.0.0.1:7878", r_key):
            radarr_online = True
            try:
                existing = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", r_key, "/api/v3/movie", timeout=5) or []
                existing_ids = {m.get("tmdbId") for m in existing}
                found = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", r_key, f"/api/v3/movie/lookup?term={q}", timeout=10) or []
                for m in found[:6]:
                    poster = m.get("remotePoster") or ""
                    if not poster:
                        for img in m.get("images") or []:
                            if img.get("coverType") == "poster" and img.get("remoteUrl"):
                                poster = img["remoteUrl"]
                                break
                    results.append({
                        "type": "movie",
                        "id": m.get("tmdbId"),
                        "title": m.get("title", ""),
                        "year": m.get("year"),
                        "overview": (m.get("overview") or "")[:180],
                        "poster": poster,
                        "already_added": m.get("tmdbId") in existing_ids,
                    })
            except Exception:
                pass

    if media_type in ("all", "show"):
        s_key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.SONARR_DIR, "config.xml"))
        if s_key and radarr_sonarr_sync._is_service_online("http://127.0.0.1:8989", s_key):
            sonarr_online = True
            try:
                existing = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", s_key, "/api/v3/series", timeout=5) or []
                existing_ids = {s.get("tvdbId") for s in existing}
                found = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", s_key, f"/api/v3/series/lookup?term={q}", timeout=10) or []
                for s in found[:6]:
                    poster = s.get("remotePoster") or ""
                    if not poster:
                        for img in s.get("images") or []:
                            if img.get("coverType") == "poster" and img.get("remoteUrl"):
                                poster = img["remoteUrl"]
                                break
                    results.append({
                        "type": "show",
                        "id": s.get("tvdbId"),
                        "title": s.get("title", ""),
                        "year": s.get("year"),
                        "overview": (s.get("overview") or "")[:180],
                        "poster": poster,
                        "already_added": s.get("tvdbId") in existing_ids,
                    })
            except Exception:
                pass

    return {"results": results, "radarr_online": radarr_online, "sonarr_online": sonarr_online}


def add_media_item(media_type, item_id):
    if not item_id:
        return {"status": "error", "message": "Missing media ID."}

    try:
        item_id_int = int(item_id)
    except Exception:
        return {"status": "error", "message": "Invalid media ID."}

    if media_type == "movie":
        key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.RADARR_DIR, "config.xml"))
        if not key or not radarr_sonarr_sync._is_service_online("http://127.0.0.1:7878", key):
            return {"status": "error", "message": "İndirme Modu (Radarr) şu an kapalı."}
        existing = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", key, f"/api/v3/movie?tmdbId={item_id_int}", timeout=5)
        if existing:
            return {"status": "ok", "message": "Zaten kütüphanede mevcut."}
        lists = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", key, "/api/v3/importlist", timeout=5) or []
        q_id = lists[0].get("qualityProfileId") if lists else None
        root_path = lists[0].get("rootFolderPath") if lists else None
        if not q_id:
            profs = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", key, "/api/v3/qualityprofile", timeout=5) or []
            q_id = profs[0]["id"] if profs else 1
        if not root_path:
            roots = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", key, "/api/v3/rootfolder", timeout=5) or []
            root_path = roots[0]["path"] if roots else "/movies"
        lookup = radarr_sonarr_sync._api_request("http://127.0.0.1:7878", key, f"/api/v3/movie/lookup/tmdb?tmdbId={item_id_int}", timeout=12)
        if not lookup:
            return {"status": "error", "message": "Film bulunamadı."}
        lookup["qualityProfileId"] = q_id
        lookup["rootFolderPath"] = root_path
        lookup["monitored"] = True
        lookup["addOptions"] = {"monitor": "movieOnly", "searchForMovie": True}
        radarr_sonarr_sync._api_request("http://127.0.0.1:7878", key, "/api/v3/movie", method="POST", payload=lookup, timeout=15)
        _summary_cache["ts"] = 0
        return {"status": "ok", "title": lookup.get("title", "")}

    elif media_type == "show":
        key = radarr_sonarr_sync._get_api_key(os.path.join(radarr_sonarr_sync.SONARR_DIR, "config.xml"))
        if not key or not radarr_sonarr_sync._is_service_online("http://127.0.0.1:8989", key):
            return {"status": "error", "message": "İndirme Modu (Sonarr) şu an kapalı."}
        existing = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", key, f"/api/v3/series?tvdbId={item_id_int}", timeout=5)
        if existing:
            return {"status": "ok", "message": "Zaten kütüphanede mevcut."}
        lists = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", key, "/api/v3/importlist", timeout=5) or []
        q_id = lists[0].get("qualityProfileId") if lists else None
        root_path = lists[0].get("rootFolderPath") if lists else None
        if not q_id:
            profs = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", key, "/api/v3/qualityprofile", timeout=5) or []
            q_id = profs[0]["id"] if profs else 1
        if not root_path:
            roots = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", key, "/api/v3/rootfolder", timeout=5) or []
            root_path = roots[0]["path"] if roots else "/tv"
        lookup_list = radarr_sonarr_sync._api_request("http://127.0.0.1:8989", key, f"/api/v3/series/lookup?term=tvdb:{item_id_int}", timeout=12) or []
        if not lookup_list:
            return {"status": "error", "message": "Dizi bulunamadı."}
        lookup = lookup_list[0]
        lookup["qualityProfileId"] = q_id
        lookup["rootFolderPath"] = root_path
        lookup["monitored"] = True
        lookup["addOptions"] = {"searchForMissingEpisodes": True}
        radarr_sonarr_sync._api_request("http://127.0.0.1:8989", key, "/api/v3/series", method="POST", payload=lookup, timeout=15)
        _summary_cache["ts"] = 0
        return {"status": "ok", "title": lookup.get("title", "")}

    return {"status": "error", "message": "Unsupported media type."}
