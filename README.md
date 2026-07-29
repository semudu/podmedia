# 🍿 PodMedia

**PodMedia** is a lightweight, containerized Home Media Suite powered by **Podman**, **Python**, and a modern dark-mode Single Page Web Dashboard.

It provides selective, fine-grained control over your media automation ecosystem (Plex, Radarr, Sonarr, Prowlarr, qBittorrent, Slskd, Beets, FlareSolverr) without heavy dependencies or system bloat.

---

## ⚡ Key Features

- **Automated Initialization**: Single command setup (`python setup.py`) that prepares pipeline directory structures, personal configuration templates, and verifies Podman VM health.
- **Selective Container Control**: 1-second instant toggle switches for individual applications or predefined operation modes (*Stream Mode*, *Download Mode*, *Music Mode*).
- **Automated Music Pipeline**: Soulseek (Slskd) downloads FLAC/MP3 tracks directly into `/downloads/music`, where Beets auto-tags them via MusicBrainz/Discogs/iTunes and moves them cleanly to `/media/music`.
- **Real-Time Stream Logs**: Built-in Server-Sent Events (SSE) modal streams live Podman CLI execution logs directly to your browser.
- **Dynamic Single Source of Truth**: All paths (`media_dir`, `downloads_dir`) are dynamically resolved from `config.properties` and injected into container volume mounts.
- **Rootless & Secure**: Built from the ground up for Podman & Podman Compose with clean, git-ignored template configs.

---

## 🛠️ Stack Overview

| Application | Role | Default Port |
| :--- | :--- | :--- |
| **Plex** | Media Server & Streaming | `32400` |
| **Radarr** | Movie Management & Automation | `7878` |
| **Sonarr** | TV Show Management & Automation | `8989` |
| **Prowlarr** | Indexer Management | `9696` |
| **qBittorrent** | Torrent Download Client | `8080` |
| **Slskd** | Soulseek FLAC Downloader | `5030` |
| **Beets** | Music Auto-Tagger & Library Manager | Custom CLI |
| **FlareSolverr** | Cloudflare Bypass Proxy | `8191` |

---

## 🚀 Step-by-Step Installation

### Prerequisites
- **Python 3.8+** ([python.org](https://www.python.org/downloads/))
- **Podman** ([podman.io](https://podman.io)) & `podman-compose` (`pip install podman-compose`)

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
*This step creates local directory structures (`downloads/`, `media/`), generates personalized local `.env`, `slskd.yml`, `config.yaml`, `qBittorrent.conf`, and verifies your Podman machine health.*

### Step 3: (Optional) Custom Directory Paths
If you store your media on external drives (e.g. `E:/Media` or `D:/Downloads`), edit your generated `config.properties`:
```ini
[paths]
media_dir = E:/Media
downloads_dir = D:/Downloads
```

### Step 4: Launch Web Dashboard
```bash
python app.py
```

### Step 5: Access Control Panel
Open **`http://localhost:3000`** in your browser or phone to start managing your media services!

---

## 📄 License

MIT License. Free and open-source for personal home media setups.
