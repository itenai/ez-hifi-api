<div align="center">

# 🎵 ez-hifi-api

### *Tidal Music Proxy with Dual Token Support (V1 + V2)*

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-Enabled-007800?style=for-the-badge&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)

<br />

<p align="center">
  <a href="#-overview">Overview</a> •
  <a href="#-features">Features</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-authentication">Authentication</a> •
  <a href="#-v1-vs-v2-comparison">V1 vs V2</a> •
  <a href="#-v1-endpoints-tokenjson">V1 Endpoints</a> •
  <a href="#-v2-endpoints-token_hifijson">V2 Endpoints</a> •
  <a href="#-configuration">Configuration</a> •
  <a href="#-examples">Examples</a> •
  <a href="#-troubleshooting">Troubleshooting</a>
</p>

---

</div>

> [!IMPORTANT]  
> **Educational Use Only:** Music piracy is illegal in most countries. This project is intended exclusively for use with a valid Tidal account for educational and testing purposes (e.g., within your local homelab). Public hosting or open-internet deployment is strongly discouraged and unsupported.

> [!WARNING]  
> **Risk Disclaimer:** Tidal actively monitors and bans accounts using unauthorized API proxies. Use this tool at your own risk.

---

## 📌 Overview

**ez-hifi-api** is an enhanced proxy server for Tidal. It bridges standard client capabilities with high-performance features, enabling direct lossy and lossless audio extraction with minimal overhead.

---

## ✨ Features

* 🔑 **Dual Token Engine:** Supports both **V1** (DASH manifests, full account scope) and **V2** (Direct HiFi stream URLs) simultaneously.
* ⚡ **Unified Authentication:** Interactive Auth Wizard with support for built-in credentials and automatic public Gist credential pools.
* 🛡️ **Anonymity & Anti-Detection:** Per-request User-Agent rotation simulating a pool of 14+ Android devices alongside continuous proxy health checks.
* 🎼 **Auto Audio Processing:** Seamless `FFmpeg` integration for automatic M4A-to-FLAC conversion and multi-segment DASH manifest stitching.
* 🔓 **DRM License Proxying:** Built-in proxy engine handling Widevine DRM requests for protected high-tier streams.
* 🚀 **High-Throughput Pooling:** Optimized keepalive HTTP connection pools handling up to 500 parallel sockets with automated exponential retries.

---

## 🚀 Quick Start

### 1. Prerequisites & Installation

Ensure **Python 3.9+** and **FFmpeg** are installed on your system.

```bash
# Clone repository
git clone [https://github.com/your-username/ez-hifi-api.git](https://github.com/itenai/ez-hifi-api.git)
cd ez-hifi-api

# Install dependencies
pip install -r requirements.txt

```
### 2. Generate Authentication Tokens
Run the unified authorization script:
```bash
python tidal_auth.py

```
Select your desired authorization mode from the menu:
```text
Select mode:
  1. V1 - Standard Tidal API (token.json)
  2. V2 - HiFi/Specialized API (token_hifi.json)
  3. V1 with Gist credentials (try multiple)
  4. V2 with Gist credentials (try multiple)

```
### 3. Start the Server
```bash
python main.py

```
> 🌐 **Live Endpoint:** http://localhost:8000
> 
## 🔄 Authentication
The unified script tidal_auth.py combines all client workflows into a single entry point.
| Mode | Target File | Granted Scope | Credential Source | Description |
|---|---|---|---|---|
| **1** | token.json | r_usr + w_usr + w_sub | Built-in | Full V1 API access (DASH, metadata, user actions) |
| **2** | token_hifi.json | r_usr | Built-in | V2 API access (read-only HiFi streaming) |
| **3** | token.json | r_usr + w_usr + w_sub | Public Gist Pool | Auto-cycles Gist pool credentials for V1 |
| **4** | token_hifi.json | r_usr | Public Gist Pool | Auto-cycles Gist pool credentials for V2 |
> [!TIP]
> **Why use Gist Credential Pools (Modes 3 & 4)?**
> If your personal developer credentials expire or hit rate limits, Modes 3 and 4 automatically fetch, test, and apply working client credentials from a maintained public pool.
> 
## 🆚 V1 vs V2 Comparison
| Feature | V1 API (/download/) | V2 API (/downloadv2/) |
|---|---|---|
| **Token Credential File** | token.json | token_hifi.json |
| **API Backend** | playbackinfo + Manifest Parsing | streamurl Direct Stream |
| **OAuth Scope Required** | r_usr, w_usr, w_sub | r_usr (Read-only) |
| **Quality Control** | 🎯 Strict (Exact requested quality) | 🚀 Auto-Upgrades to HI_RES_LOSSLESS |
| **DASH Manifest Support** | ✅ Yes (Stitches segments) | ❌ No (Direct downloads only) |
| **Processing Speed** | Moderate (Manifest processing required) | ⚡ Fast (Instant direct link generation) |
## 📥 V1 Endpoints (token.json)
### 🎵 Track & Metadata
| Endpoint | Method | Query Parameters | Description |
|---|---|---|---|
| /track/ | GET | id, quality, immersiveaudio | Get track playback info |
| /info/ | GET | id | Get raw track metadata |
| /trackManifests/ | GET | id, formats | Fetch DASH manifests |
| /recommendations/ | GET | id | Get related track recommendations |
| /lyrics/ | GET | id | Get synced or static lyrics |
### 🔍 Search & Discovery
| Endpoint | Method | Query Parameters | Description |
|---|---|---|---|
| /search/ | GET | s, a, al, v, p, i | Search tracks (s), artists (a), albums (al), videos (v), playlists (p), or ISRC (i) |
| /album/ | GET | id, limit, offset | Get album details and tracklist |
| /playlist/ | GET | id, limit, offset | Get playlist details and tracklist |
| /artist/ | GET | id or f, skip_tracks | Get artist profile or discography |
| /artist/similar/ | GET | id, cursor | Get similar artists |
| /album/similar/ | GET | id, cursor | Get similar albums |
| /mix/ | GET | id | Get mix contents |
### 🖼️ Media, DRM & Downloads
| Endpoint | Method | Query Parameters | Output Format |
|---|---|---|---|
| /cover/ | GET | id or q | Cover Art Image |
| /video/ | GET | id, quality, mode | Video playback info |
| /topvideos/ | GET | countryCode, limit | Recommended videos list |
| /widevine | GET / POST | *Payload* | Widevine DRM license proxy |
| /download/ | GET | id, quality | Audio File Stream |
| /download/link/ | GET | id, quality | JSON with direct stream URL |
| /download/album/ | GET | id, quality | ZIP archive (Album) |
| /download/playlist/ | GET | id, quality, limit | ZIP archive (Playlist) |
| /download/multi/ | GET | ids, quality | ZIP archive (ids delimited by , or +) |
| /download/link/album/ | GET | id, quality | JSON with album track URLs |
| /download/link/playlist/ | GET | id, quality, limit | JSON with playlist track URLs |
| /download/link/multi/ | GET | ids, quality | JSON with multiple track URLs |
## 📥 V2 Endpoints (token_hifi.json)
> ℹ️ *V2 uses Tidal's direct stream URL backend. It is optimized for maximum download speed and direct audio access.*
> 
| Endpoint | Method | Query Parameters | Response Format |
|---|---|---|---|
| /trackv2/ | GET | id, quality, show_all_qualities | Stream details (JSON) |
| /trackv2/quality/ | GET | id, quality | Specific quality payload (JSON) |
| /trackv2/check/ | GET | id | Available stream tiers (JSON) |
| /downloadv2/ | GET | id, quality | Direct Audio File Stream |
| /downloadv2/link/ | GET | id, quality | JSON direct stream link |
| /downloadv2/album/ | GET | id, quality | ZIP archive (Album) |
| /downloadv2/playlist/ | GET | id, quality, limit | ZIP archive (Playlist) |
| /downloadv2/multi/ | GET | ids, quality | ZIP archive (ids delimited by , or +) |
| /downloadv2/link/album/ | GET | id, quality | JSON (Album URLs) |
| /downloadv2/link/playlist/ | GET | id, quality, limit | JSON (Playlist URLs) |
| /downloadv2/link/multi/ | GET | ids, quality | JSON (Multiple Track URLs) |
## 🔧 Configuration
Configure environment variables inside the .env file located in the root directory:
```env
# Token File Configuration
TOKEN_FILE_V1=token.json
TOKEN_FILE_V2=token_hifi.json

# Localization
COUNTRY_CODE=US

# Proxy Settings
USE_PROXIES=False
PROXIES_FILE=proxies.txt
ROTATE_PROXIES_ON_REFRESH=False
FALLBACK_TO_DIRECT_CONNECTION=False

# Connection & Debug Controls
MAX_RETRIES=2
DEV_MODE=False
USER_AGENT=

```
### 📄 Proxy File Format (proxies.txt)
Define proxies line-by-line in proxies.txt:
```text
http://user:pass@hostname:port
https://user:pass@hostname:port
socks5://user:pass@hostname:port
http://hostname:port

```
## 💡 Examples
### 🎵 Single Track Download
```bash
# V1 Download (Strict Quality Match)
curl -O "http://localhost:8000/download/?id=1234567890&quality=LOSSLESS"

# V2 Download (HiFi Direct Stream)
curl -O "http://localhost:8000/downloadv2/?id=1234567890&quality=LOSSLESS"

```
### 📦 Batch & Archive Downloads
```bash
# Download Full Album as ZIP (V1)
curl -O "http://localhost:8000/download/album/?id=123456789&quality=LOSSLESS"

# Download Playlist as ZIP (V2)
curl -O "http://localhost:8000/downloadv2/playlist/?id=abc12345-6789-def0-1234-56789abcdef0&quality=LOSSLESS&limit=100"

# Download Multiple Specific Tracks
curl -O "http://localhost:8000/download/multi/?ids=123,456,789&quality=LOSSLESS"
curl -O "http://localhost:8000/download/multi/?ids=123+456+789&quality=LOSSLESS"

```
### 🔗 Extract Direct Stream Links (JSON)
```bash
# Single Track Direct Link
curl "http://localhost:8000/download/link/?id=1234567890&quality=LOSSLESS"

# Album Direct Links Array
curl "http://localhost:8000/download/link/album/?id=1234567890&quality=LOSSLESS"

```
### 🔍 Discovery & Metadata Queries
```bash
# Search for tracks by name
curl "http://localhost:8000/search/?s=Daft%20Punk&limit=10"

# Fetch Track Lyrics
curl "http://localhost:8000/lyrics/?id=1234567890"

# Check Available Qualities on V2
curl "http://localhost:8000/trackv2/check/?id=1234567890"

```
## ⚠️ Troubleshooting
| Problem | Root Cause | Recommended Action |
|---|---|---|
| **Token Generation Fails** | Expired client ID or network block | Run python tidal_auth.py and select **Mode 3** or **Mode 4** (Gist credentials). |
| **401 Unauthorized** | Expired auth token | Re-run tidal_auth.py to refresh or replace token.json / token_hifi.json. |
| **404 Not Found** | Invalid Track ID or Region Lock | Check if the item is restricted in your account's primary country region. |
| **429 Too Many Requests** | Rate limit triggered | The API auto-retries with exponential backoff. Enable proxy support if persistent. |
| **FFmpeg Errors** | Missing or broken FFmpeg | Ensure ffmpeg is installed and added to your system's PATH. |
| **All Proxies Failing** | Unreachable proxy servers | Set FALLBACK_TO_DIRECT_CONNECTION=True inside .env. |
> [!NOTE]
> Enable verbose debug output by setting DEV_MODE=True in your .env file.
> 
## 📁 File Structure
```text
ez-hifi-api/
├── main.py              # Main API server application
├── tidal_auth.py        # Unified token generator & auth wizard
├── token.json           # V1 API credentials
├── token_hifi.json      # V2 API credentials
├── proxies.txt          # Proxy addresses configuration
├── .env                 # Environment variables file
└── requirements.txt     # Python package dependencies

```
## 📝 Additional Notes
 * **Region Locking:** Tidal enforces region restrictions on an account level rather than via the countryCode query parameter. While /track/ endpoints remain generally unblocked, search and discovery endpoints adapt to your account's region.
 * **Dolby Atmos:** Spatial audio streams contain DOLBY_ATMOS flags under mediaMetadata.tags. Query /trackManifests/ with target spatial formats to extract multi-channel streams.
 * **Connection Pooling:** Built-in connection pooling reuses HTTP sockets across requests (supporting up to 500 keepalive connections with a 30-second expiry window).
## 🙏 Credits
 * **Original Concept:** [sachinsenal0x64/hifi](https://github.com/sachinsenal0x64/hifi)
 * **API Base Fork:** [binimum/hifi-api](https://github.com/binimum/hifi-api)
<div align="center">
<sub>Educational tool for homelab environments. Distributed under MIT License.</sub>
</div>
```
