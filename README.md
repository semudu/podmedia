# <img src="app/favicon.svg" width="32" align="absmiddle" alt="PodMedia"> PodMedia

**PodMedia** is a lightweight, unified, containerized Home Media Suite powered by **Podman**, **Python**, and a clean 3-tab Web Application (**Home**, **Library**, **Admin**) with Soft Light & Calm Dark themes and TR/EN localization.

Instead of juggling ten separate tools, PodMedia gives you one cohesive experience for streaming, watchlist automation, media search, library management, lossless music tagging, external drive archiving, and granular service administration — across **Windows**, **macOS**, and **Linux**.

---

## 📂 Project Architecture

```text
PodMedia/
├── README.md                      # Project documentation
├── requirements.txt               # Python package dependencies
├── setup.py                       # Cross-platform setup, config & launcher generator
├── config.properties.example      # Root server, path & automation template
├── app/                           # PodMedia Web App & Automation Engine
│   ├── app.py                     # HTTP Server, SSE Log Streamer & API Router
│   ├── index.html                 # Unified 3-Tab Web UI (Light/Dark, TR/EN)
│   ├── hub_api.py                 # Unified Media Search, Library & Queue API
│   ├── settings_manager.py        # Unified App & Service Settings + Auto-Restart
│   ├── podman_manager.py          # Container & Mode Orchestration Engine
│   ├── media_watcher.py           # File Watcher, Trakt/Plex Sync & Watched Cleanup
│   ├── radarr_sonarr_sync.py      # Radarr/Sonarr Library & ImportList Sync
│   ├── disk_manager.py            # External Drive Detection, Sleep & Safe Release
│   ├── transfer_manager.py        # Background Local-to-External Media Transfer
│   ├── auto_updater.py            # Container Image Update Checker
│   ├── tray.py                    # Cross-Platform System Tray App
│   ├── autostart.py               # OS Boot Startup Manager
│   ├── favicon.svg                # Original PodMedia Vector Icon
│   ├── favicon.ico                # Multi-size Browser & Windows Icon
│   └── apple-touch-icon.png       # High-DPI Touch & System Tray Icon
└── services/                      # Container Service Definitions & Templates
    ├── plex/
    │   ├── podman-compose.yml
    │   └── .env.example
    └── tools/
        ├── podman-compose.yml
        ├── beets/                 # config.yaml.example
        ├── metube/                # YouTube / web media downloader
        ├── plextraktsync/         # config.yml.example (Optional Plex <-> Trakt)
        ├── prowlarr/              # Indexer manager
        ├── qbittorrent/           # qBittorrent.conf.example
        ├── radarr/                # config.xml.example
        ├── slskd/                 # slskd.yml.example
        └── sonarr/                # config.xml.example
```

---

## ⚡ Key Features

- **Unified 3-Tab Web Experience**:
  - **Home**: One-click control over 3 core modes (*Cinema & Watch*, *Library & Downloads*, *Music Library*), connected Plex/Trakt status, and compact hybrid storage overview.
  - **Library**: Search any movie or TV show and add/download in one click via Radarr/Sonarr, monitor live qBittorrent downloads, and filter your full media library.
  - **Admin**: System & disk controls, per-service toggles and links, plus a collapsible **Advanced Application & Service Settings** panel.
- **Trakt.tv & Plex Watchlist Automation**: Direct Trakt ↔ Plex Watchlist synchronization into Radarr & Sonarr (on startup and every `N` minutes) plus optional `PlexTraktSync` coupled to Plex's lifecycle.
- **Smart Watched Media Cleanup Policies**: Configurable from Admin Settings:
  - `keep` — Keep files on disk (default).
  - `delete_after_rating` — Deletes watched movies from disk & watchlists only *after* you rate them (on **either Plex or Trakt**), ensuring two-way watched & rating sync completes first and protecting re-downloaded favorites via `addedAt`.
  - `delete_after_24h` / `delete_immediate` — Automatic cleanup after watching, while always skipping actively streaming sessions.
- **Hybrid Local + External Disk Management**: Recognizes external archive drives by hardware serial number (even if drive letters change), transfers local media to external storage in the background with live progress/ETA, supports safe disk sleep (0 RPM) / wake, and cleanly releases mounts on shutdown.
- **Automated Music Pipeline**: Soulseek (Slskd) and MeTube download audio into `/downloads/music`, where Beets auto-tags via MusicBrainz/Discogs/iTunes and organizes files into `/media/music`.
- **Unified Settings & Auto-Updates**: Edit panel port, watched media policy, sync interval, qBittorrent speed limits, `{tmdb}/{tvdb}` auto-renaming, LAN `AllowedHosts`, Plex advertise IP/hostname/TZ, and Trakt sync options directly from the UI — with automatic restart of affected services and one-click reset to defaults.
- **Smart Home / Homebridge Ready**: `setup.py` generates symmetric mode scripts (`start_plex` / `stop_plex`, `start_downloads` / `stop_downloads`, `start`, `stop`, `status`) with `.running` state files and Windows Scheduled Tasks for zero-UAC headless triggering.
- **Rootless & Portable**: Built for Podman & Podman Compose with git-ignored personal configs, tokens, and generated launchers.

---

## 🛠️ Stack Overview

| Application | Role | Default Port |
| :--- | :--- | :--- |
| **Plex** | Media Server & Streaming | `32400` |
| **PlexTraktSync** | Optional Plex ↔ Trakt.tv Sync | Background |
| **Radarr** | Movie Management & Automation | `7878` |
| **Sonarr** | TV Show Management & Automation | `8989` |
| **Prowlarr** | Indexer Management | `9696` |
| **qBittorrent** | Torrent Download Client | `8080` |
| **FlareSolverr** | Cloudflare Bypass Proxy | `8191` |
| **Slskd** | Soulseek FLAC Downloader | `5030` |
| **MeTube** | YouTube / Web Audio & Video Downloader | `8081` |
| **Beets** | Music Auto-Tagger & Library Manager | CLI / Auto |

---

## 🚀 Step-by-Step Installation

### Prerequisites
- **Python 3.9+** ([python.org](https://www.python.org/downloads/))
- **Podman** ([podman.io](https://podman.io)) & `podman-compose`

---

### Step 1: Clone Repository
```bash
git clone https://github.com/your-username/PodMedia.git
cd PodMedia
```

### Step 2: Run Automated Setup Script
```bash
python setup.py
```
*This installs Python dependencies (`pystray`, `Pillow`, `podman-compose`), creates all pipeline & service config directories (`downloads/`, `media/`, `services/tools/*/config`), initializes local templates (`config.properties`, `services/plex/.env`, `slskd.yml`, `beets/config.yaml`, `qBittorrent.conf`, `radarr/config.xml`, `sonarr/config.xml`, `plextraktsync/config.yml`), verifies Podman health, and generates OS-specific launchers (`start`/`stop`/`status` + `plex` & `downloads` mode scripts).*

### Step 3: (Optional) Customize Paths & Automation
Edit the generated `config.properties` (or adjust automation settings later from **Admin → Advanced Application & Service Settings**):
```ini
[server]
port = 3000

[paths]
media_dir = ./media
downloads_dir = ./downloads

[podman]
network = media_center_net

[automation]
watchlist_sync_minutes = 5
auto_update_enabled = true
watched_media_action = keep
```

### Step 4: (Optional) Connect Trakt.tv via PlexTraktSync
If you want two-way Plex ↔ Trakt.tv watched, rating, and watchlist synchronization, run the one-time interactive login after starting Plex:
```bash
podman run --rm -it --network media_center_net \
  -v ./services/tools/plextraktsync/config:/app/config \
  ghcr.io/taxel/plextraktsync:latest login
```
Once `.pytrakt.json` and `servers.yml` exist in `services/tools/plextraktsync/config/`, PodMedia automatically detects Trakt and starts/stops `PlexTraktSync` alongside Plex.

### Step 5: Launch & Stop PodMedia
- **Windows**:
  - Launch dashboard: `.\start.bat` (or double-click `start.bat`)
  - Control modes directly: `.\start_plex.bat`, `.\stop_plex.bat`, `.\start_downloads.bat`, `.\stop_downloads.bat`
  - Stop everything & release external drives: `.\stop.bat`
- **macOS / Linux**:
  - Launch dashboard: `./start.sh`
  - Control modes directly: `./start_plex.sh`, `./stop_plex.sh`, `./start_downloads.sh`, `./stop_downloads.sh`
  - Stop everything & release external drives: `./stop.sh`

### Step 6: Open PodMedia
Open **`http://localhost:3000`** in your browser (or `http://<your-lan-ip>:3000` from your phone/tablet on the same network).

---

## 📄 License

MIT License. Free and open-source for personal home media setups.
