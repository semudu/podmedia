# 🍿 PodMedia

**PodMedia** is a lightweight, containerized Home Media Suite powered by **Podman**, **Python**, and a modern dark-mode Single Page Dashboard.

It provides selective, fine-grained control over your media automation ecosystem (Plex, Radarr, Sonarr, Prowlarr, qBittorrent, Slskd, Beets, FlareSolverr) without heavy dependencies.

---

## ⚡ Features

- **Automated Initialization**: One-command setup (`python setup.py`) that prepares directory structures, configuration templates, and verifies Podman VM health.
- **Selective Container Control**: Fast 1-second toggle switches for individual applications or predefined mode groups (*Stream Mode*, *Download Mode*, *Music Mode*).
- **Real-Time Stream Logs**: Built-in Server-Sent Events (SSE) modal streams live Podman CLI logs directly to your browser.
- **Rootless & Secure**: Built from the ground up for Podman & Podman Compose with clean, dynamic template configs.
- **Automated Music Management**: Dedicated Beets auto-tagging trigger for FLAC/MP3 libraries downloaded via Slskd or torrents.

---

## 🛠️ Stack Overview

| Application | Role | Port |
| :--- | :--- | :--- |
| **Plex** | Media Server & Streaming | `32400` |
| **Radarr** | Movie Management | `7878` |
| **Sonarr** | TV Show Management | `8989` |
| **Prowlarr** | Indexer Management | `9696` |
| **qBittorrent** | Download Client | `8080` |
| **Slskd** | Soulseek FLAC Downloader | `5030` |
| **Beets** | Music Tagging & Library Manager | Custom CLI |
| **FlareSolverr** | Cloudflare Bypass Proxy | `8191` |

---

## 🚀 Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/PodMedia.git
   cd PodMedia
   ```

2. **Run Initialization Script**:
   ```bash
   python setup.py
   ```

3. **Start the Web Dashboard**:
   ```bash
   python app.py
   ```

4. **Access Dashboard**:
   Open [http://localhost:3000](http://localhost:3000) in your browser or phone.

---

## 📄 License

MIT License. Free and open-source for personal home media setups.
