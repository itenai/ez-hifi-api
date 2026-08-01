# ez-hifi-api

<p align="center">Tidal Music Proxy with Dual Token Support (V1 + V2)</p>

> [!IMPORTANT]
> Music piracy is illegal in most countries. This project is intended for use with a valid Tidal account for educational purposes (for example, in your homelab). I won't provide support for people hosting this API on the open internet.

> [!WARNING]
> Tidal has begun blocking accounts en masse. Use at your own risk.

---

## 📋 Table of Contents

- [✨ Features](#-features)
- [🚀 Quick Start](#-quick-start)
- [🔄 Authentication](#-authentication)
- [📥 V1 Endpoints](#-v1-endpoints-tokenjson)
- [📥 V2 Endpoints](#-v2-endpoints-token_hifijson)
- [🔧 Configuration](#-configuration)
- [🆚 V1 vs V2](#-v1-vs-v2)
- [💡 Examples](#-examples)
- [⚠️ Troubleshooting](#️-troubleshooting)
- [📝 Notes](#-notes)

---

## ✨ Features

- **Dual Token Support**: V1 (`token.json`) and V2 (`token_hifi.json`)
- **Multi-Client Authentication**: Try multiple credentials automatically
- **User-Agent Rotation**: Random User-Agent for each request
- **Proxy Support**: Automatic proxy rotation with health checking
- **FFmpeg Integration**: Automatic M4A → FLAC conversion
- **DASH Support**: V1 supports DASH manifests with segment combining
- **Widevine DRM Proxy**: Built-in proxy for Widevine license requests
- **Connection Pooling**: Reuses HTTP connections for better performance
- **Comprehensive Retry Logic**: Exponential backoff with token refresh

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

2. Generate Tokens

Run the unified auth script:

```bash
python tidal_auth.py
```

You'll be prompted to select a mode:

```
Select mode:
  1. V1 - Standard Tidal API (token.json)
  2. V2 - HiFi/Specialized API (token_hifi.json)
  3. V1 with Gist credentials (try multiple)
  4. V2 with Gist credentials (try multiple)
```

The script will:

1. Try credentials from a public Gist pool (modes 3 & 4)
2. Open your browser for authorization
3. Save the token to the appropriate file
4. Verify the token works

3. Run the Server

```bash
python main.py
```

Server runs on http://localhost:8000

---

🔄 Authentication

Unified Token Generator (tidal_auth.py)

The authentication system has been completely reworked into a single unified script:

Mode Token File Scope Description
1 token.json r_usr+w_usr+w_sub Standard V1 API (full access)
2 token_hifi.json r_usr V2 API (read-only)
3 token.json r_usr+w_usr+w_sub V1 with Gist credential pool
4 token_hifi.json r_usr V2 with Gist credential pool

Key Changes

· Single Script: No more separate tidal_auth.py and tidal_auth_hifi.py
· Multi-Client Support: Automatically tries multiple credentials from Gist
· Better Error Handling: Clear feedback on which credentials work
· Auto-Verification: Tests the token after generation

Gist Credential Pool

Modes 3 & 4 fetch credentials from a public Gist containing working Tidal client IDs/secrets. This is useful when:

· Your credentials are expired
· You don't have your own credentials
· You want to try multiple accounts

---

📥 V1 Endpoints (token.json)

Track & Metadata

Endpoint Method Parameters Description
/track/ GET id, quality, immersiveaudio Get track playback info
/info/ GET id Get track metadata
/trackManifests/ GET id, formats Get DASH manifests
/recommendations/ GET id Get track recommendations
/lyrics/ GET id Get track lyrics

Search & Discovery

Endpoint Method Parameters Description
/search/ GET s, a, al, v, p, i Search all types
/album/ GET id, limit, offset Get album with tracks
/playlist/ GET id, limit, offset Get playlist with tracks
/artist/ GET id or f, skip_tracks Get artist info or albums
/artist/similar/ GET id, cursor Get similar artists
/album/similar/ GET id, cursor Get similar albums
/mix/ GET id Get mix contents

Media

Endpoint Method Parameters Description
/cover/ GET id or q Get album cover
/video/ GET id, quality, mode Get video playback info
/topvideos/ GET countryCode, limit Get recommended videos

DRM

Endpoint Method Description
/widevine GET/POST Widevine license proxy

Downloads

Endpoint Method Parameters Response
/download/ GET id, quality Audio file
/download/link/ GET id, quality JSON with URL
/download/album/ GET id, quality ZIP file
/download/playlist/ GET id, quality, limit ZIP file
/download/multi/ GET ids, quality ZIP file
/download/link/album/ GET id, quality JSON (album URLs)
/download/link/playlist/ GET id, quality, limit JSON (playlist URLs)
/download/link/multi/ GET ids, quality JSON (track URLs)

Notes:

· ids accepts comma-separated (123,456,789) or plus-separated (123+456+789)
· V1 uses playbackinfo API with manifest parsing
· Supports both direct URLs and DASH manifests
· Automatic M4A → FLAC conversion via FFmpeg

---

📥 V2 Endpoints (token_hifi.json)

ℹ️ V2 uses Tidal's direct streamurl API. It may return higher quality than requested.

Track Info

Endpoint Method Parameters Response
/trackv2/ GET id, quality, show_all_qualities JSON (single or all qualities)
/trackv2/quality/ GET id, quality JSON with specific quality
/trackv2/check/ GET id Shows available qualities

Downloads

Endpoint Method Parameters Response
/downloadv2/ GET id, quality Audio file
/downloadv2/link/ GET id, quality JSON with URL
/downloadv2/album/ GET id, quality ZIP file
/downloadv2/playlist/ GET id, quality, limit ZIP file
/downloadv2/multi/ GET ids, quality ZIP file
/downloadv2/link/album/ GET id, quality JSON (album URLs)
/downloadv2/link/playlist/ GET id, quality, limit JSON (playlist URLs)
/downloadv2/link/multi/ GET ids, quality JSON (track URLs)

Notes:

· V2 is faster (direct URL, no manifest parsing)
· May upgrade quality if HI_RES_LOSSLESS is available
· No DASH support (direct downloads only)

---

🔧 Configuration

Environment Variables (.env)

```env
# Token files
TOKEN_FILE_V1=token.json
TOKEN_FILE_V2=token_hifi.json

# Country code
COUNTRY_CODE=US

# Proxy configuration
USE_PROXIES=False
PROXIES_FILE=proxies.txt
ROTATE_PROXIES_ON_REFRESH=False
FALLBACK_TO_DIRECT_CONNECTION=False

# Advanced
MAX_RETRIES=2
DEV_MODE=False
USER_AGENT=
```

Proxy Configuration

Variable Default Description
USE_PROXIES False Enable proxy support
PROXIES_FILE proxies.txt Path to proxy list
ROTATE_PROXIES_ON_REFRESH False Rotate proxy on token refresh
FALLBACK_TO_DIRECT_CONNECTION False ⚠️ Fallback if proxies fail
MAX_RETRIES 2 Retry attempts with new proxies

Proxy Format (proxies.txt):

```
http://user:pass@hostname:port
https://user:pass@hostname:port
socks5://user:pass@hostname:port
http://hostname:port
```

User-Agent Rotation

The API automatically rotates User-Agent headers from a large pool of Android devices. You can set a custom User-Agent via USER_AGENT environment variable.

---

🆚 V1 vs V2

Feature V1 (/download/*) V2 (/downloadv2/*)
Token File token.json token_hifi.json
API Used playbackinfo + manifest streamurl direct
Scope r_usr+w_usr+w_sub r_usr
Quality Control Exact requested May upgrade
DASH Support ✅ Yes (segments) ❌ No
Speed Slower (manifest parsing) Faster (direct URL)
Best For Exact quality control Best available quality

---

💡 Examples

Download Track (V1)

```bash
curl -O "http://localhost:8000/download/?id=495566820&quality=LOSSLESS"
```

Download Track (V2)

```bash
curl -O "http://localhost:8000/downloadv2/?id=495566820&quality=LOSSLESS"
```

Download Album (V1)

```bash
curl -O "http://localhost:8000/download/album/?id=123456789&quality=LOSSLESS"
```

Download Album (V2)

```bash
curl -O "http://localhost:8000/downloadv2/album/?id=123456789&quality=LOSSLESS"
```

Download Playlist (V1)

```bash
curl -O "http://localhost:8000/download/playlist/?id=abc12345-6789-def0-1234-56789abcdef0&quality=LOSSLESS&limit=100"
```

Download Multiple Tracks

```bash
# Comma-separated
curl -O "http://localhost:8000/download/multi/?ids=123,456,789&quality=LOSSLESS"

# Plus-separated
curl -O "http://localhost:8000/download/multi/?ids=123+456+789&quality=LOSSLESS"
```

Get Download Links (JSON)

```bash
# Single track
curl "http://localhost:8000/download/link/?id=495566820&quality=LOSSLESS"

# Album
curl "http://localhost:8000/download/link/album/?id=123456789&quality=LOSSLESS"

# Playlist
curl "http://localhost:8000/download/link/playlist/?id=abc123&quality=LOSSLESS"

# Multiple tracks
curl "http://localhost:8000/download/link/multi/?ids=123,456,789&quality=LOSSLESS"
```

V2 - Show All Qualities

```bash
curl "http://localhost:8000/trackv2/?id=495566820&show_all_qualities=true"
```

V2 - Check Available Qualities

```bash
curl "http://localhost:8000/trackv2/check/?id=495566820"
```

Search

```bash
# Search tracks
curl "http://localhost:8000/search/?s=artist%20name&limit=10"

# Search albums
curl "http://localhost:8000/search/?al=album%20name"

# Search by ISRC
curl "http://localhost:8000/search/?i=USUM71234567"
```

Metadata

```bash
# Track metadata
curl "http://localhost:8000/info/?id=495566820"

# Lyrics
curl "http://localhost:8000/lyrics/?id=495566820"

# Cover art
curl "http://localhost:8000/cover/?id=495566820"
curl "http://localhost:8000/cover/?q=artist%20song"

# Artist info
curl "http://localhost:8000/artist/?id=123456"

# Album with tracks
curl "http://localhost:8000/album/?id=123456789&limit=50"
```

---

⚠️ Troubleshooting

Token Generation Issues

Issue Solution
Browser doesn't open Manually copy the verification URL
401 errors Try mode 3 or 4 with Gist credentials
Invalid client Credentials expired, use different mode
Token verification fails Check internet connection

Download Issues

Issue Solution
404 Not Found Invalid ID or region-locked
429 Too Many Requests Wait and retry (automatic retry with backoff)
503 Service Unavailable Check proxy or try direct connection
FFmpeg errors Install FFmpeg for FLAC conversion
No manifest found Try different quality setting

Proxy Issues

Issue Solution
All proxies fail Set FALLBACK_TO_DIRECT_CONNECTION=True
Proxy slow Test proxy manually first
Proxies blocked Try different proxies or direct connection
Invalid proxy format Use http://user:pass@host:port format

Enable Debug Logging

Set DEV_MODE=True in your .env file to see detailed logs:

```env
DEV_MODE=True
```

---

📁 File Structure

```
ez-hifi-api/
├── main.py                 # Main API server
├── tidal_auth.py           # Unified token generator
├── token.json              # V1 credentials
├── token_hifi.json         # V2 credentials
├── proxies.txt             # Optional proxy list
├── .env                    # Environment variables
└── requirements.txt        # Dependencies
```

---

📝 Notes

Region-locking

Tidal region locks by account, not by countryCode. The /track/ endpoint is not region-locked. You can use the OpenAPI with countryCode for search/album endpoints then stream via this API.

Dolby Atmos

Tracks with Atmos support have DOLBY_ATMOS in mediaMetadata.tags. Use the trackManifests endpoint with appropriate formats.

Rate Limiting

The API implements exponential backoff with retries for 429 responses. Maximum retries: 3 with delays of 1s, 2s, 4s.

Connection Pooling

HTTP connections are reused for better performance. Max 500 keepalive connections with 30s expiry.

---

📄 License

This project is for educational purposes only.

🙏 Credits

· Original: sachinsenal0x64/hifi
· Fork: binimum/hifi-api

```
