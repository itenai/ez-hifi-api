#ez-hifi-api

<p align="center">Tidal Music Proxy with Dual Token Support (V1 + V2)</p>

[!IMPORTANT]
Music piracy is illegal. This project is intended for educational purposes with a valid Tidal account. Use at your own risk.

---

📌 Overview

This is a fork of binimum/hifi-api with significant enhancements:

· ✅ Dual Token Support – V1 (token.json) & V2 (token_hifi.json)
· ✅ Unified Authentication – Single script for both V1 & V2 tokens
· ✅ Multi-Client Support – Automatically tries credentials from public Gist pool
· ✅ User-Agent Rotation – Random Android UA for each request
· ✅ Proxy Rotation – Automatic proxy health checking & rotation
· ✅ FFmpeg Integration – Automatic M4A → FLAC conversion
· ✅ DASH Support – V1 supports DASH manifests with segment combining

For all original features and endpoints (/search/, /album/, /playlist/, /artist/, /cover/, /lyrics/, /video/, /track/, /info/, /recommendations/, /mix/, /artist/similar/, /album/similar/, /topvideos/, /trackManifests/, /widevine), refer to binimum/hifi-api.

---

🆕 New Features

1. Unified Authentication

The authentication system has been completely reworked into a single script:

```bash
python tidal_auth.py
# Select: 1 (V1), 2 (V2), 3 (V1+Gist), or 4 (V2+Gist)
```

Mode Token File Scope Source
1 token.json r_usr+w_usr+w_sub Built-in
2 token_hifi.json r_usr Built-in
3 token.json r_usr+w_usr+w_sub Gist Pool
4 token_hifi.json r_usr Gist Pool

What's New:

· No more separate tidal_auth_hifi.py
· Multi-client support – tries multiple credentials automatically
· Public Gist credential pool for when credentials expire
· Auto-verification after token generation

---

2. V2 API Support (token_hifi.json)

New V2 endpoints using Tidal's direct streamurl API:

```bash
# Track info
GET /trackv2/?id={id}&quality={quality}&show_all_qualities={bool}
GET /trackv2/quality/?id={id}&quality={quality}
GET /trackv2/check/?id={id}

# Downloads
GET /downloadv2/?id={id}&quality={quality}
GET /downloadv2/link/?id={id}&quality={quality}
GET /downloadv2/album/?id={id}&quality={quality}
GET /downloadv2/playlist/?id={id}&quality={quality}&limit={limit}
GET /downloadv2/multi/?ids={id1,id2,id3}&quality={quality}
GET /downloadv2/link/album/?id={id}&quality={quality}
GET /downloadv2/link/playlist/?id={id}&quality={quality}&limit={limit}
GET /downloadv2/link/multi/?ids={id1,id2,id3}&quality={quality}
```

V2 Benefits:

· Faster (direct URL, no manifest parsing)
· May upgrade quality if HI_RES_LOSSLESS is available

---

3. V1 Download Enhancements

New download endpoints added:

```bash
# Single track
GET /download/?id={id}&quality={quality}
GET /download/link/?id={id}&quality={quality}

# Bulk
GET /download/album/?id={id}&quality={quality}
GET /download/playlist/?id={id}&quality={quality}&limit={limit}
GET /download/multi/?ids={id1,id2,id3}&quality={quality}

# JSON links
GET /download/link/album/?id={id}&quality={quality}
GET /download/link/playlist/?id={id}&quality={quality}&limit={limit}
GET /download/link/multi/?ids={id1,id2,id3}&quality={quality}
```

Features:

· ids accepts comma-separated (123,456,789) or plus-separated (123+456+789)
· Automatic M4A → FLAC conversion via FFmpeg

---

4. User-Agent Rotation

Automatic rotation from a pool of 14+ Android devices to avoid detection. Set custom UA via USER_AGENT env var.

---

5. Proxy Enhancements

Variable Default Description
USE_PROXIES False Enable proxy support
PROXIES_FILE proxies.txt Path to proxy list
ROTATE_PROXIES_ON_REFRESH False Rotate proxy on token refresh
FALLBACK_TO_DIRECT_CONNECTION False ⚠️ Fallback if proxies fail
MAX_RETRIES 2 Retry attempts with new proxies

Proxy Format (proxies.txt):

```bash
http://user:pass@hostname:port
https://user:pass@hostname:port
socks5://user:pass@hostname:port
```

---

🆚 V1 vs V2

Feature V1 V2
Token token.json token_hifi.json
Scope r_usr+w_usr+w_sub r_usr
API playbackinfo + manifest streamurl direct
DASH ✅ Yes ❌ No
Speed Slower Faster
Quality Exact control May upgrade

---

💡 Examples

```bash
# Download track (V1)
curl -O "http://localhost:8000/download/?id=495566820&quality=LOSSLESS"

# Download track (V2)
curl -O "http://localhost:8000/downloadv2/?id=495566820&quality=LOSSLESS"

# Download album
curl -O "http://localhost:8000/download/album/?id=123456789&quality=LOSSLESS"

# Download multiple tracks
curl -O "http://localhost:8000/download/multi/?ids=123,456,789&quality=LOSSLESS"

# Get download link (JSON)
curl "http://localhost:8000/download/link/?id=495566820&quality=LOSSLESS"

# V2 - Show all qualities
curl "http://localhost:8000/trackv2/?id=495566820&show_all_qualities=true"

# V2 - Check available qualities
curl "http://localhost:8000/trackv2/check/?id=495566820"
```

---

🔧 Configuration (.env)

```env
# Token files
TOKEN_FILE_V1=token.json
TOKEN_FILE_V2=token_hifi.json

# Country code
COUNTRY_CODE=US

# Proxy
USE_PROXIES=False
PROXIES_FILE=proxies.txt
ROTATE_PROXIES_ON_REFRESH=False
FALLBACK_TO_DIRECT_CONNECTION=False

# Advanced
MAX_RETRIES=2
DEV_MODE=False
USER_AGENT=
```

---

📁 File Structure

```bash
ez-hifi-api/
├── main.py              # Main API server
├── tidal_auth.py        # Unified token generator
├── token.json           # V1 credentials
├── token_hifi.json      # V2 credentials
├── proxies.txt          # Optional proxy list
├── .env                 # Environment variables
└── requirements.txt     # Dependencies
```

---

⚠️ Troubleshooting

Issue Solution
Token generation fails Try mode 3 or 4 (Gist credentials)
401 Unauthorized Token expired – refresh or regenerate
404 Not Found Invalid ID or region-locked
429 Too Many Requests Automatic retry with backoff
FFmpeg errors Install FFmpeg for FLAC conversion
All proxies fail Set FALLBACK_TO_DIRECT_CONNECTION=True

Enable debug logs:

```bash
DEV_MODE=True
```

---

📝 Notes

· Region-locking: Tidal region locks by account, not countryCode. The /track/ endpoint is not region-locked.
· Dolby Atmos: Use trackManifests endpoint with appropriate formats.
· Rate Limiting: Exponential backoff with retries for 429 responses (max 3 retries).
· Connection Pooling: Max 500 keepalive connections with 30s expiry.

---

🙏 Credits

· Original: sachinsenal0x64/hifi
· Fork: binimum/hifi-api
