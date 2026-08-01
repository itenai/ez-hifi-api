#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════════════════════════════════╗
║                         HiFi-RestAPI - Tidal Music Proxy                                      ║
║                                   Version 2.10                                                ║
║                                                                                               ║
║  V1 Endpoints (token.json)     → /track/, /download/, /album/, /playlist/, /search/ ...      ║
║  V2 Endpoints (token_hifi.json) → /trackv2/, /downloadv2/, /downloadv2/link/ ...             ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import json
import os
import random
import time
import base64
import re
import tempfile
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Union

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

import logging

logger = logging.getLogger(__name__)

load_dotenv()

API_VERSION = "2.10"

# ================================================================================================
#                                       SHARED HTTP CLIENT
# ================================================================================================

_http_client: Optional[httpx.AsyncClient] = None
_http_client_proxy_url: Optional[str] = None
_http_client_lock = asyncio.Lock()

# One lock per credential to avoid global contention during token refreshes
_refresh_locks: Dict[str, asyncio.Lock] = {}

# Loaded credential set from token.json; each entry will be enriched with access cache
_creds: List[dict] = []

# Global semaphore to limit concurrent album track fetches across all requests
_album_tracks_sem = asyncio.Semaphore(20)

# List of proxies loaded from file at startup
_proxies: List[str] = []

# Cache of the last proxy confirmed to be working
_last_known_good_proxy: Optional[str] = None

# DASH namespace for XML manifest parsing
DASH_NS = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}

# ================================================================================================
#                                       USER AGENT ROTATION
# ================================================================================================

_USER_AGENTS = [
    "Dalvik/2.1.0 (Linux; U; Android 14; SM-S928B Build/AP2A.240905.003)",
    "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Pro Build/AP2A.240905.003)",
    "Dalvik/2.1.0 (Linux; U; Android 14; SM-G998B Build/UP1A.231005.007)",
    "Dalvik/2.1.0 (Linux; U; Android 13; SM-A546B Build/TP1A.220624.014)",
    "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 7 Build/TQ3A.230901.001)",
    "Dalvik/2.1.0 (Linux; U; Android 13; SM-S911B Build/TP1A.220624.014)",
    "Dalvik/2.1.0 (Linux; U; Android 12; SM-G991B Build/SP1A.210812.016)",
    "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SP2A.220405.004)",
    "Dalvik/2.1.0 (Linux; U; Android 14; OnePlus CPH2423 Build/AP2A.240905.003)",
    "Dalvik/2.1.0 (Linux; U; Android 13; moto g84 5G Build/U1TDS33.73-27)",
    "Dalvik/2.1.0 (Linux; U; Android 14; Xiaomi 13 Pro Build/AP2A.240905.003)",
    "Dalvik/2.1.0 (Linux; U; Android 14; OnePlus 12 Build/AP2A.240905.003)",
    "Dalvik/2.1.0 (Linux; U; Android 13; Google Pixel 6a Build/TQ3A.230901.001)",
    "Dalvik/2.1.0 (Linux; U; Android 13; Samsung Galaxy S23 Build/TP1A.220624.014)",
]

_custom_ua = os.getenv("USER_AGENT")

def random_user_agent() -> str:
    """Return a random User-Agent string for rotation"""
    if _custom_ua:
        return _custom_ua
    return random.choice(_USER_AGENTS)

def _tidal_headers(extra: dict | None = None) -> dict:
    """Generate Tidal-specific headers with random User-Agent"""
    h = {
        "User-Agent": random_user_agent(),
        "Accept": "*/*",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Platform": "android",
        "X-Tidal-Platform": "android",
    }
    if extra:
        h.update(extra)
    return h

_TIDAL_DEFAULT_HEADERS = _tidal_headers()

# ================================================================================================
#                                       CONFIGURATION
# ================================================================================================

DEV_MODE = os.getenv("DEV_MODE", "False").lower() in ("true", "1", "yes")

_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 1.0
_RATE_LIMIT_MAX_DELAY = 10.0

def _log_response(method: str, url: str, resp: httpx.Response):
    """Log HTTP response details when DEV_MODE is enabled"""
    if not DEV_MODE:
        return
    logger.info(
        "[DEV] %s %s → %s\n  headers: %s\n  body: %s",
        method,
        url,
        resp.status_code,
        dict(resp.headers),
        resp.text[:2000],
    )

def _build_http_client(proxy_url: Optional[str] = None) -> httpx.AsyncClient:
    """Build HTTP client with proper proxy support and connection pooling"""
    client_kwargs = {
        "http2": True,
        "headers": _tidal_headers(),
        "timeout": httpx.Timeout(connect=3.0, read=12.0, write=8.0, pool=12.0),
        "limits": httpx.Limits(
            max_keepalive_connections=500,
            max_connections=1000,
            keepalive_expiry=30.0,
        ),
    }
    try:
        return httpx.AsyncClient(proxy=proxy_url, **client_kwargs)
    except TypeError:
        legacy_proxies = {"all://": proxy_url} if proxy_url else None
        return httpx.AsyncClient(proxies=legacy_proxies, **client_kwargs)

def _build_proxy_test_client(proxy_url: str) -> httpx.AsyncClient:
    try:
        return httpx.AsyncClient(proxy=proxy_url, timeout=5.0)
    except TypeError:
        return httpx.AsyncClient(proxies={"all://": proxy_url}, timeout=5.0)

# ================================================================================================
#                                       LIFESPAN MANAGEMENT
# ================================================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for HTTP client lifecycle"""
    global _http_client, _http_client_proxy_url
    if DEV_MODE:
        logger.warning("DEV_MODE is enabled — upstream responses will be logged at DEBUG level")
    if _http_client is None:
        proxy_url = None
        if USE_PROXIES:
            proxy_url = await get_working_proxy()
            if not proxy_url and not FALLBACK_TO_DIRECT_CONNECTION:
                logger.error("Could not find a working proxy and FALLBACK_TO_DIRECT_CONNECTION is False. Shutting down.")
                raise RuntimeError("No working proxies available")
            elif not proxy_url and FALLBACK_TO_DIRECT_CONNECTION:
                logger.warning("Could not find a working proxy, falling back to direct connection. HOST IP MAY BE EXPOSED!")
        _http_client = _build_http_client(proxy_url)
        _http_client_proxy_url = proxy_url
    try:
        yield
    finally:
        if _http_client:
            await _http_client.aclose()
            _http_client = None
            _http_client_proxy_url = None

app = FastAPI(
    title="HiFi-RestAPI",
    version=API_VERSION,
    description="Tidal Music Proxy with Dual Token Support (V1: token.json, V2: token_hifi.json)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================================================================================================
#                                       ENVIRONMENT CONFIG
# ================================================================================================

CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
REFRESH_TOKEN: Optional[str] = os.getenv("REFRESH_TOKEN")
USER_ID = os.getenv("USER_ID")

# Token files - SEPARATE for V1 and V2
TOKEN_FILE_V1 = "token.json"
TOKEN_FILE_V2 = "token_hifi.json"

COUNTRY_CODE = os.getenv("COUNTRY_CODE", "US")

USE_PROXIES = os.getenv("USE_PROXIES", "False").lower() in ("true", "1", "yes")
ROTATE_PROXIES_ON_REFRESH = os.getenv("ROTATE_PROXIES_ON_REFRESH", "False").lower() in ("true", "1", "yes")
PROXIES_FILE = os.getenv("PROXIES_FILE", "proxies.txt")
FALLBACK_TO_DIRECT_CONNECTION = os.getenv("FALLBACK_TO_DIRECT_CONNECTION", "False").lower() in ("true", "1", "yes")
MAX_PROXY_CANDIDATES = 10
_PROXY_TEST_CONCURRENCY = 5

_max_retries_raw = os.getenv("MAX_RETRIES", "2")
try:
    MAX_RETRIES = int(_max_retries_raw)
except ValueError:
    MAX_RETRIES = 2
if MAX_RETRIES < 1:
    MAX_RETRIES = 1

def load_proxies():
    """Load proxies from file into the global _proxies list."""
    global _proxies
    if not os.path.exists(PROXIES_FILE):
        logger.warning(f"Proxies file {PROXIES_FILE} not found.")
        _proxies = []
        return
    with open(PROXIES_FILE, "r") as f:
        _proxies = [line.strip() for line in f if line.strip()]
    logger.info(f"Loaded {len(_proxies)} proxies.")

async def test_proxy(proxy_url: str) -> bool:
    try:
        async with _build_proxy_test_client(proxy_url) as client:
            resp = await client.get("http://example.com")
            return resp.status_code == 200
    except Exception:
        return False

async def get_working_proxy(avoid_proxy: Optional[str] = None) -> Optional[str]:
    global _last_known_good_proxy

    if not _proxies:
        return None

    if _last_known_good_proxy and _last_known_good_proxy != avoid_proxy:
        if await test_proxy(_last_known_good_proxy):
            return _last_known_good_proxy

    shuffled_proxies = _proxies[:]
    random.shuffle(shuffled_proxies)

    if avoid_proxy:
        candidate_proxies = [p for p in shuffled_proxies if p != avoid_proxy]
        if not candidate_proxies:
            candidate_proxies = shuffled_proxies
    else:
        candidate_proxies = shuffled_proxies

    if _last_known_good_proxy:
        candidate_proxies = [p for p in candidate_proxies if p != _last_known_good_proxy]
    candidate_proxies = candidate_proxies[:MAX_PROXY_CANDIDATES]

    sem = asyncio.Semaphore(_PROXY_TEST_CONCURRENCY)
    found_event = asyncio.Event()
    selected_proxy: List[Optional[str]] = [None]

    async def probe(proxy: str) -> None:
        if found_event.is_set():
            return
        async with sem:
            if found_event.is_set():
                return
            if await test_proxy(proxy):
                if not found_event.is_set():
                    selected_proxy[0] = proxy
                    found_event.set()

    await asyncio.gather(*[probe(p) for p in candidate_proxies], return_exceptions=True)

    if selected_proxy[0]:
        _last_known_good_proxy = selected_proxy[0]
    return selected_proxy[0]

async def _delayed_close(client: httpx.AsyncClient):
    await asyncio.sleep(15)
    await client.aclose()

async def update_global_client(force_new_proxy: bool = False):
    global _http_client, _http_client_proxy_url
    async with _http_client_lock:
        proxy_to_avoid = None
        if force_new_proxy and _http_client_proxy_url:
            proxy_to_avoid = _http_client_proxy_url

        proxy_url = None
        if USE_PROXIES:
            proxy_url = await get_working_proxy(avoid_proxy=proxy_to_avoid)
            if not proxy_url:
                if FALLBACK_TO_DIRECT_CONNECTION:
                    logger.warning("Could not find a working proxy, falling back to direct connection. HOST IP MAY BE EXPOSED!")
                else:
                    logger.error("Could not find a working proxy and FALLBACK_TO_DIRECT_CONNECTION is False.")
                    raise HTTPException(status_code=503, detail="Service Unavailable")

        if _http_client and _http_client_proxy_url == proxy_url:
            return

        new_client = _build_http_client(proxy_url)
        old_client = _http_client
        _http_client = new_client
        _http_client_proxy_url = proxy_url

        if old_client is not None:
            asyncio.create_task(_delayed_close(old_client))

if USE_PROXIES:
    load_proxies()

# ================================================================================================
#                                       V1 TOKEN MANAGEMENT (token.json)
# ================================================================================================

_creds_v1: List[dict] = []
_refresh_locks_v1: Dict[str, asyncio.Lock] = {}

def _load_credentials_v1():
    """Load V1 credentials from token.json"""
    global _creds_v1
    if not os.path.exists(TOKEN_FILE_V1):
        logger.warning(f"V1 token file {TOKEN_FILE_V1} not found.")
        return
    
    with open(TOKEN_FILE_V1, "r") as tok:
        token_data = json.load(tok)
        if isinstance(token_data, dict):
            token_data = [token_data]
        
        for entry in token_data:
            cred = {
                "client_id": entry.get("client_ID") or entry.get("client_id") or CLIENT_ID,
                "client_secret": entry.get("client_secret") or CLIENT_SECRET,
                "refresh_token": entry.get("refresh_token") or REFRESH_TOKEN,
                "user_id": entry.get("userID") or entry.get("user_id") or USER_ID,
                "access_token": None,
                "expires_at": 0,
            }
            if cred["refresh_token"]:
                _creds_v1.append(cred)
                logger.info(f"Loaded V1 credential: {cred['client_id'][:15]}...")
    
    if REFRESH_TOKEN:
        env_cred = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "user_id": USER_ID,
            "access_token": None,
            "expires_at": 0,
        }
        if not any(c["refresh_token"] == REFRESH_TOKEN for c in _creds_v1):
            _creds_v1.append(env_cred)
            logger.info("Added V1 credential from environment")

def _pick_credential_v1() -> dict:
    if not _creds_v1:
        raise HTTPException(status_code=500, detail="No V1 Tidal credentials available; populate token.json")
    return random.choice(_creds_v1)

def _lock_for_cred_v1(cred: dict) -> asyncio.Lock:
    key = f"{cred['client_id']}:{cred['refresh_token']}"
    lock = _refresh_locks_v1.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks_v1[key] = lock
    return lock

async def refresh_tidal_token_v1(cred: Optional[dict] = None):
    cred = cred or _pick_credential_v1()
    async with _lock_for_cred_v1(cred):
        if cred["access_token"] and time.time() < cred["expires_at"]:
            return cred["access_token"]

        if USE_PROXIES and ROTATE_PROXIES_ON_REFRESH:
            await update_global_client(force_new_proxy=True)

        max_retries = MAX_RETRIES if USE_PROXIES else 1
        for attempt in range(max_retries):
            try:
                client = await get_http_client()
                res = await client.post(
                    "https://auth.tidal.com/v1/oauth2/token",
                    data={
                        "client_id": cred["client_id"],
                        "refresh_token": cred["refresh_token"],
                        "grant_type": "refresh_token",
                        "scope": "r_usr+w_usr+w_sub",
                    },
                    auth=(cred["client_id"], cred["client_secret"]),
                )
                _log_response("POST", "https://auth.tidal.com/v1/oauth2/token", res)

                if res.status_code in [400, 401]:
                    try:
                        error_data = res.json()
                        if error_data.get("error") in ["invalid_client", "invalid_grant"]:
                            logger.error(f"V1 Tidal Auth Error: {error_data}")
                            raise HTTPException(status_code=401, detail=f"V1 Tidal Auth Error: {error_data.get('error_description')}")
                    except ValueError:
                        pass

                res.raise_for_status()
                data = res.json()
                new_token = data["access_token"]
                expires_in = data.get("expires_in", 3600)

                cred["access_token"] = new_token
                cred["expires_at"] = time.time() + expires_in - 60
                logger.info(f"V1 token refreshed, expires in {expires_in}s")
                return new_token
            except httpx.RequestError as e:
                if USE_PROXIES and attempt < max_retries - 1:
                    logger.warning(f"Proxy failed during V1 token refresh: {e}")
                    await update_global_client(force_new_proxy=True)
                    continue
                raise HTTPException(status_code=401, detail=f"V1 Token refresh failed: {str(e)}")
            except httpx.HTTPStatusError as e:
                if USE_PROXIES and e.response.status_code in [403, 429] and attempt < max_retries - 1:
                    logger.warning(f"Proxy blocked during V1 token refresh ({e.response.status_code})")
                    await update_global_client(force_new_proxy=True)
                    continue
                raise HTTPException(status_code=401, detail=f"V1 Token refresh failed: {str(e)}")

async def get_tidal_token_for_cred_v1(force_refresh: bool = False, cred: Optional[dict] = None):
    cred = cred or _pick_credential_v1()
    if not force_refresh and cred["access_token"] and time.time() < cred["expires_at"]:
        return cred["access_token"], cred
    token = await refresh_tidal_token_v1(cred)
    return token, cred

_load_credentials_v1()

# ================================================================================================
#                                       V2 TOKEN MANAGEMENT (token_hifi.json)
# ================================================================================================

_creds_v2: List[dict] = []
_refresh_locks_v2: Dict[str, asyncio.Lock] = {}

def _load_credentials_v2():
    """Load V2 credentials from token_hifi.json"""
    global _creds_v2
    if not os.path.exists(TOKEN_FILE_V2):
        logger.warning(f"V2 token file {TOKEN_FILE_V2} not found.")
        return
    
    with open(TOKEN_FILE_V2, "r") as tok:
        token_data = json.load(tok)
        if isinstance(token_data, dict):
            token_data = [token_data]
        
        for entry in token_data:
            cred = {
                "client_id": entry.get("client_ID") or entry.get("client_id") or CLIENT_ID,
                "client_secret": entry.get("client_secret") or CLIENT_SECRET,
                "refresh_token": entry.get("refresh_token") or REFRESH_TOKEN,
                "user_id": entry.get("userID") or entry.get("user_id") or USER_ID,
                "access_token": None,
                "expires_at": 0,
            }
            if cred["refresh_token"]:
                _creds_v2.append(cred)
                logger.info(f"Loaded V2 credential: {cred['client_id'][:15]}...")

def _pick_credential_v2() -> dict:
    if not _creds_v2:
        raise HTTPException(status_code=500, detail="No V2 Tidal credentials available; populate token_hifi.json")
    return random.choice(_creds_v2)

def _lock_for_cred_v2(cred: dict) -> asyncio.Lock:
    key = f"{cred['client_id']}:{cred['refresh_token']}"
    lock = _refresh_locks_v2.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks_v2[key] = lock
    return lock

async def refresh_tidal_token_v2(cred: Optional[dict] = None):
    cred = cred or _pick_credential_v2()
    async with _lock_for_cred_v2(cred):
        if cred["access_token"] and time.time() < cred["expires_at"]:
            return cred["access_token"]

        if USE_PROXIES and ROTATE_PROXIES_ON_REFRESH:
            await update_global_client(force_new_proxy=True)

        max_retries = MAX_RETRIES if USE_PROXIES else 1
        for attempt in range(max_retries):
            try:
                client = await get_http_client()
                res = await client.post(
                    "https://auth.tidal.com/v1/oauth2/token",
                    data={
                        "client_id": cred["client_id"],
                        "refresh_token": cred["refresh_token"],
                        "grant_type": "refresh_token",
                        "scope": "r_usr",
                    },
                    auth=(cred["client_id"], cred["client_secret"]),
                )
                _log_response("POST", "https://auth.tidal.com/v1/oauth2/token", res)

                if res.status_code in [400, 401]:
                    try:
                        error_data = res.json()
                        logger.error(f"V2 Auth Error: {error_data}")
                    except:
                        pass

                res.raise_for_status()
                data = res.json()
                new_token = data["access_token"]
                expires_in = data.get("expires_in", 3600)
                cred["access_token"] = new_token
                cred["expires_at"] = time.time() + expires_in - 60
                logger.info(f"V2 token refreshed, expires in {expires_in}s")
                return new_token
            except Exception as e:
                if attempt < max_retries - 1:
                    continue
                raise HTTPException(status_code=401, detail=f"V2 Token refresh failed: {str(e)}")

async def get_tidal_token_for_cred_v2(force_refresh: bool = False, cred: Optional[dict] = None):
    cred = cred or _pick_credential_v2()
    if not force_refresh and cred["access_token"] and time.time() < cred["expires_at"]:
        return cred["access_token"], cred
    token = await refresh_tidal_token_v2(cred)
    return token, cred

_load_credentials_v2()

# ================================================================================================
#                                       SHARED HTTP CLIENT GETTER
# ================================================================================================

async def get_http_client() -> httpx.AsyncClient:
    global _http_client, _http_client_proxy_url
    if _http_client is None:
        async with _http_client_lock:
            if _http_client is None:
                proxy_url = None
                if USE_PROXIES:
                    proxy_url = await get_working_proxy()
                    if not proxy_url and not FALLBACK_TO_DIRECT_CONNECTION:
                        raise HTTPException(status_code=503, detail="Service Unavailable")
                    elif not proxy_url and FALLBACK_TO_DIRECT_CONNECTION:
                        logger.warning("Could not find a working proxy, falling back to direct connection. HOST IP MAY BE EXPOSED!")
                _http_client = _build_http_client(proxy_url)
                _http_client_proxy_url = proxy_url
    return _http_client

# ================================================================================================
#                                       V1 REQUEST HELPERS
# ================================================================================================

async def make_request_v1(url: str, token: Optional[str] = None, params: Optional[dict] = None, cred: Optional[dict] = None):
    if token is None:
        token, cred = await get_tidal_token_for_cred_v1(cred=cred)
    client = await get_http_client()
    headers = {"authorization": f"Bearer {token}"}

    try:
        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            resp = await client.get(url, headers=headers, params=params)
            _log_response("GET", url, resp)

            if resp.status_code == 401:
                token, cred = await get_tidal_token_for_cred_v1(force_refresh=True, cred=cred)
                headers = {"authorization": f"Bearer {token}"}
                resp = await client.get(url, headers=headers, params=params)
                _log_response("GET (retry after 401)", url, resp)

            if resp.status_code == 429 and attempt < _RATE_LIMIT_MAX_RETRIES:
                delay = min(_RATE_LIMIT_BASE_DELAY * (2 ** attempt), _RATE_LIMIT_MAX_DELAY)
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = min(delay, max(float(retry_after), 0))
                    except ValueError:
                        pass
                delay = min(delay, _RATE_LIMIT_MAX_DELAY)
                logger.warning("Upstream 429 for %s, retrying in %.1fs (attempt %d/%d)", url, delay, attempt + 1, _RATE_LIMIT_MAX_RETRIES)
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 404:
                fresh_token, fresh_cred = await get_tidal_token_for_cred_v1(force_refresh=True, cred=cred)
                if fresh_token != token:
                    headers = {"authorization": f"Bearer {fresh_token}"}
                    resp = await client.get(url, headers=headers, params=params)
                    _log_response("GET (retry after 404 token refresh)", url, resp)
                    token, cred = fresh_token, fresh_cred

            break

        resp.raise_for_status()
        return {"version": API_VERSION, "data": resp.json()}

    except httpx.HTTPStatusError as e:
        logger.error("Upstream API error %s %s %s", e.response.status_code, url, e.response.text[:1000], exc_info=e)
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Resource not found")
        raise HTTPException(status_code=e.response.status_code, detail="Upstream API error")
    except httpx.RequestError as e:
        if isinstance(e, httpx.TimeoutException):
            raise HTTPException(status_code=429, detail="Upstream timeout")
        raise HTTPException(status_code=503, detail="Connection error to Tidal")

async def authed_get_json_v1(
    url: str,
    *,
    params: Optional[dict] = None,
    token: Optional[str] = None,
    cred: Optional[dict] = None,
):
    if token is None:
        token, cred = await get_tidal_token_for_cred_v1(cred=cred)
    client = await get_http_client()
    headers = {"authorization": f"Bearer {token}"}

    try:
        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            resp = await client.get(url, headers=headers, params=params)
            _log_response("GET", url, resp)

            if resp.status_code == 401:
                token, cred = await get_tidal_token_for_cred_v1(force_refresh=True, cred=cred)
                headers["authorization"] = f"Bearer {token}"
                resp = await client.get(url, headers=headers, params=params)
                _log_response("GET (retry after 401)", url, resp)

            if resp.status_code == 429 and attempt < _RATE_LIMIT_MAX_RETRIES:
                delay = min(_RATE_LIMIT_BASE_DELAY * (2 ** attempt), _RATE_LIMIT_MAX_DELAY)
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = min(delay, max(float(retry_after), 0))
                    except ValueError:
                        pass
                delay = min(delay, _RATE_LIMIT_MAX_DELAY)
                logger.warning("Upstream 429 for %s, retrying in %.1fs (attempt %d/%d)", url, delay, attempt + 1, _RATE_LIMIT_MAX_RETRIES)
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 404:
                fresh_token, fresh_cred = await get_tidal_token_for_cred_v1(force_refresh=True, cred=cred)
                if fresh_token != token:
                    headers["authorization"] = f"Bearer {fresh_token}"
                    resp = await client.get(url, headers=headers, params=params)
                    _log_response("GET (retry after 404 token refresh)", url, resp)
                    token, cred = fresh_token, fresh_cred

            break

        resp.raise_for_status()
        return resp.json(), token, cred

    except httpx.HTTPStatusError as e:
        logger.error("Upstream API error %s %s %s", e.response.status_code, url, e.response.text[:1000], exc_info=e)
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Resource not found")
        if e.response.status_code == 429:
            raise HTTPException(status_code=429, detail="Upstream rate limited")
        raise HTTPException(status_code=e.response.status_code, detail="Upstream API error")
    except httpx.RequestError as e:
        if isinstance(e, httpx.TimeoutException):
            raise HTTPException(status_code=429, detail="Upstream timeout")
        raise HTTPException(status_code=503, detail="Connection error to Tidal")

# ================================================================================================
#                                       V1 API ENDPOINTS (token.json)
# ================================================================================================

@app.get("/")
async def index():
    return {
        "version": API_VERSION,
        "Repo": "https://github.com/binimum/hifi-api",
        "v1_token_file": TOKEN_FILE_V1,
        "v2_token_file": TOKEN_FILE_V2,
        "v1_endpoints": "/track/, /download/, /album/, /playlist/, /search/, /artist/, /cover/, /lyrics/, /recommendations/, /mix/, /video/, /topvideos/, /artist/similar/, /album/similar/",
        "v2_endpoints": "/trackv2/, /downloadv2/, /downloadv2/link/"
    }

@app.get("/track/")
async def get_track(id: int, quality: str = "HI_RES_LOSSLESS", immersiveaudio: bool = False):
    track_url = f"https://api.tidal.com/v1/tracks/{id}/playbackinfo"
    params = {
        "audioquality": quality,
        "playbackmode": "STREAM",
        "assetpresentation": "FULL",
        "immersiveaudio": immersiveaudio
    }
    return await make_request_v1(track_url, params=params)

@app.get("/info/")
async def get_info(id: int):
    url = f"https://api.tidal.com/v1/tracks/{id}/"
    return await make_request_v1(url, params={"countryCode": COUNTRY_CODE})

@app.get("/trackManifests/")
async def get_track_manifests(
    id: str,
    request: Request,
    formats: List[str] = Query(default=["HEAACV1", "AACLC", "FLAC", "FLAC_HIRES", "EAC3_JOC"]),
    adaptive: str = Query(default="true"),
    manifestType: str = Query(default="MPEG_DASH"),
    uriScheme: str = Query(default="HTTPS"),
    usage: str = Query(default="PLAYBACK")
):
    url = f"https://openapi.tidal.com/v2/trackManifests/{id}"
    params = [("adaptive", adaptive), ("manifestType", manifestType), ("uriScheme", uriScheme), ("usage", usage)]
    for f in formats:
        params.append(("formats", f))
    res = await make_request_v1(url, params=params)
    try:
        drm_data = res["data"]["data"]["attributes"]["drmData"]
        if drm_data:
            proxy_url = str(request.base_url).rstrip("/") + "/widevine"
            drm_data["licenseUrl"] = proxy_url
            drm_data["certificateUrl"] = proxy_url
    except (KeyError, TypeError):
        pass
    return res

@app.api_route("/widevine", methods=["GET", "POST"])
async def widevine_proxy(request: Request):
    client = await get_http_client()
    body = await request.body()
    url = "https://api.tidal.com/v2/widevine"
    token, cred = await get_tidal_token_for_cred_v1()
    headers = {
        "authorization": f"Bearer {token}",
        "Content-Type": request.headers.get("Content-Type", "application/octet-stream")
    }

    try:
        resp = await client.request(request.method, url, headers=headers, content=body)
        _log_response(request.method, url, resp)

        if resp.status_code == 401:
            token, cred = await get_tidal_token_for_cred_v1(force_refresh=True, cred=cred)
            headers["authorization"] = f"Bearer {token}"
            resp = await client.request(request.method, url, headers=headers, content=body)
            _log_response(f"{request.method} (retry)", url, resp)

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers={"Content-Type": resp.headers.get("Content-Type", "application/json")}
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail="Error communicating with widevine server")

@app.get("/recommendations/")
async def get_recommendations(id: int):
    url = f"https://api.tidal.com/v1/tracks/{id}/recommendations"
    return await make_request_v1(url, params={"limit": "20", "countryCode": COUNTRY_CODE})

@app.api_route("/search/", methods=["GET"])
async def search(
    s: Union[str, None] = Query(default=None),
    a: Union[str, None] = Query(default=None),
    al: Union[str, None] = Query(default=None),
    v: Union[str, None] = Query(default=None),
    p: Union[str, None] = Query(default=None),
    i: Union[str, None] = Query(default=None, description="ISRC query"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=500),
):
    isrc_query = i.strip() if isinstance(i, str) else None
    if isrc_query:
        return await make_request_v1(
            "https://api.tidal.com/v1/tracks",
            params={"isrc": isrc_query, "limit": limit, "offset": offset, "countryCode": COUNTRY_CODE},
        )

    queries = (
        (s, "https://api.tidal.com/v1/search/tracks", {"query": s, "limit": limit, "offset": offset, "countryCode": COUNTRY_CODE}),
        (a, "https://api.tidal.com/v1/search/top-hits", {"query": a, "limit": limit, "offset": offset, "types": "ARTISTS,TRACKS", "countryCode": COUNTRY_CODE}),
        (al, "https://api.tidal.com/v1/search/top-hits", {"query": al, "limit": limit, "offset": offset, "types": "ALBUMS", "countryCode": COUNTRY_CODE}),
        (v, "https://api.tidal.com/v1/search/top-hits", {"query": v, "limit": limit, "offset": offset, "types": "VIDEOS", "countryCode": COUNTRY_CODE}),
        (p, "https://api.tidal.com/v1/search/top-hits", {"query": p, "limit": limit, "offset": offset, "types": "PLAYLISTS", "countryCode": COUNTRY_CODE}),
    )

    for value, url, params in queries:
        if value:
            return await make_request_v1(url, params=params)

    raise HTTPException(status_code=400, detail="Provide one of s, a, al, v, p, or i")

@app.get("/album/")
async def get_album(
    id: int = Query(..., description="Album ID"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    token, cred = await get_tidal_token_for_cred_v1()
    album_url = f"https://api.tidal.com/v1/albums/{id}"
    items_url = f"https://api.tidal.com/v1/albums/{id}/items"

    async def fetch(url: str, params: Optional[dict] = None):
        payload, _, _ = await authed_get_json_v1(url, params=params, token=token, cred=cred)
        return payload

    tasks = [fetch(album_url, {"countryCode": COUNTRY_CODE})]
    max_chunk = 100
    current_offset = offset
    remaining_limit = limit

    while remaining_limit > 0:
        chunk_size = min(remaining_limit, max_chunk)
        tasks.append(fetch(items_url, {"countryCode": COUNTRY_CODE, "limit": chunk_size, "offset": current_offset}))
        current_offset += chunk_size
        remaining_limit -= chunk_size

    results = await asyncio.gather(*tasks)
    album_data = results[0]
    items_pages = results[1:]
    all_items = []

    for page in items_pages:
        page_items = page.get("items", page) if isinstance(page, dict) else page
        if isinstance(page_items, list):
            all_items.extend(page_items)

    album_data["items"] = all_items
    return {"version": API_VERSION, "data": album_data}

@app.get("/mix/")
async def get_mix(id: str = Query(..., description="Mix ID")):
    token, cred = await get_tidal_token_for_cred_v1()
    url = "https://api.tidal.com/v1/pages/mix"
    params = {"mixId": id, "countryCode": COUNTRY_CODE, "deviceType": "BROWSER"}
    data, _, _ = await authed_get_json_v1(url, params=params, token=token, cred=cred)

    header = {}
    items = []

    rows = data.get("rows", [])
    for row in rows:
        modules = row.get("modules", [])
        for module in modules:
            if module.get("type") == "MIX_HEADER":
                header = module.get("mix", {})
            elif module.get("type") == "TRACK_LIST":
                paged_list = module.get("pagedList", {})
                items = paged_list.get("items", [])

    return {
        "version": API_VERSION,
        "mix": header,
        "items": [item.get("item", item) if isinstance(item, dict) else item for item in items],
    }

@app.get("/playlist/")
async def get_playlist(
    id: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    token, cred = await get_tidal_token_for_cred_v1()
    playlist_url = f"https://api.tidal.com/v1/playlists/{id}"
    items_url = f"https://api.tidal.com/v1/playlists/{id}/items"

    async def fetch(url: str, params: Optional[dict] = None):
        payload, _, _ = await authed_get_json_v1(url, params=params, token=token, cred=cred)
        return payload

    playlist_data, items_data = await asyncio.gather(
        fetch(playlist_url, {"countryCode": COUNTRY_CODE}),
        fetch(items_url, {"countryCode": COUNTRY_CODE, "limit": limit, "offset": offset}),
    )

    return {
        "version": API_VERSION,
        "playlist": playlist_data,
        "items": items_data.get("items", items_data) if isinstance(items_data, dict) else items_data,
    }

def _extract_uuid_from_tidal_url(href: str) -> Optional[str]:
    parts = href.split("/") if href else []
    return "-".join(parts[4:9]) if len(parts) >= 9 else None

@app.get("/artist/similar/")
async def get_similar_artists(id: int = Query(...), cursor: Union[int, str, None] = None):
    url = f"https://openapi.tidal.com/v2/artists/{id}/relationships/similarArtists"
    params = {"page[cursor]": cursor, "countryCode": COUNTRY_CODE, "include": "similarArtists,similarArtists.profileArt"}
    payload, _, _ = await authed_get_json_v1(url, params=params)

    included = payload.get("included", [])
    artists_map = {i["id"]: i for i in included if i["type"] == "artists"}
    artworks_map = {i["id"]: i for i in included if i["type"] == "artworks"}

    def resolve_artist(entry):
        aid = entry["id"]
        inc = artists_map.get(aid, {})
        attr = inc.get("attributes", {})

        pic_id = None
        if art_data := inc.get("relationships", {}).get("profileArt", {}).get("data"):
            if artwork := artworks_map.get(art_data[0].get("id")):
                if files := artwork.get("attributes", {}).get("files"):
                    pic_id = _extract_uuid_from_tidal_url(files[0].get("href"))

        return {
            **attr,
            "id": int(aid) if str(aid).isdigit() else aid,
            "picture": pic_id or attr.get("selectedAlbumCoverFallback"),
            "url": f"http://www.tidal.com/artist/{aid}",
            "relationType": "SIMILAR_ARTIST"
        }

    return {"version": API_VERSION, "artists": [resolve_artist(e) for e in payload.get("data", [])]}

@app.get("/album/similar/")
async def get_similar_albums(id: int = Query(...), cursor: Union[int, str, None] = None):
    url = f"https://openapi.tidal.com/v2/albums/{id}/relationships/similarAlbums"
    params = {"page[cursor]": cursor, "countryCode": COUNTRY_CODE, "include": "similarAlbums,similarAlbums.coverArt,similarAlbums.artists"}
    payload, _, _ = await authed_get_json_v1(url, params=params)

    included = payload.get("included", [])
    albums_map = {i["id"]: i for i in included if i["type"] == "albums"}
    artworks_map = {i["id"]: i for i in included if i["type"] == "artworks"}
    artists_map = {i["id"]: i for i in included if i["type"] == "artists"}

    def resolve_album(entry):
        aid = entry["id"]
        inc = albums_map.get(aid, {})
        attr = inc.get("attributes", {})

        cover_id = None
        if art_data := inc.get("relationships", {}).get("coverArt", {}).get("data"):
            if artwork := artworks_map.get(art_data[0].get("id")):
                if files := artwork.get("attributes", {}).get("files"):
                    cover_id = _extract_uuid_from_tidal_url(files[0].get("href"))

        artist_list = []
        if art_data := inc.get("relationships", {}).get("artists", {}).get("data"):
            for a_entry in art_data:
                if a_obj := artists_map.get(a_entry["id"]):
                    a_id = a_obj["id"]
                    artist_list.append({
                        "id": int(a_id) if str(a_id).isdigit() else a_id,
                        "name": a_obj["attributes"]["name"]
                    })

        return {
            **attr,
            "id": int(aid) if str(aid).isdigit() else aid,
            "cover": cover_id,
            "artists": artist_list,
            "url": f"http://www.tidal.com/album/{aid}"
        }

    return {"version": API_VERSION, "albums": [resolve_album(e) for e in payload.get("data", [])]}

@app.get("/artist/")
async def get_artist(
    id: Optional[int] = Query(default=None),
    f: Optional[int] = Query(default=None),
    skip_tracks: bool = Query(default=False),
):
    if id is None and f is None:
        raise HTTPException(status_code=400, detail="Provide id or f query param")

    token, cred = await get_tidal_token_for_cred_v1()

    if id is not None:
        artist_url = f"https://api.tidal.com/v1/artists/{id}"
        artist_data, token, cred = await authed_get_json_v1(artist_url, params={"countryCode": COUNTRY_CODE}, token=token, cred=cred)

        picture = artist_data.get("picture")
        fallback = artist_data.get("selectedAlbumCoverFallback")

        if not picture and fallback:
            artist_data["picture"] = fallback
            picture = fallback

        cover = None
        if picture:
            slug = picture.replace("-", "/")
            cover = {
                "id": artist_data.get("id"),
                "name": artist_data.get("name"),
                "750": f"https://resources.tidal.com/images/{slug}/750x750.jpg",
            }

        return {"version": API_VERSION, "artist": artist_data, "cover": cover}

    albums_url = f"https://api.tidal.com/v1/artists/{f}/albums"
    common_params = {"countryCode": COUNTRY_CODE, "limit": 100}

    tasks = [
        authed_get_json_v1(albums_url, params=common_params, token=token, cred=cred),
        authed_get_json_v1(albums_url, params={**common_params, "filter": "EPSANDSINGLES"}, token=token, cred=cred),
    ]

    if skip_tracks:
        tasks.append(
            authed_get_json_v1(
                f"https://api.tidal.com/v1/artists/{f}/toptracks",
                params={"countryCode": COUNTRY_CODE, "limit": 15},
                token=token,
                cred=cred
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    unique_releases = []
    seen_ids = set()

    for res in results[:2]:
        if isinstance(res, tuple) and len(res) > 0:
            data = res[0]
            items = data.get("items", []) if isinstance(data, dict) else data
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("id") and item["id"] not in seen_ids:
                        unique_releases.append(item)
                        seen_ids.add(item["id"])
        elif isinstance(res, Exception):
            logger.warning("Error fetching artist releases: %s", res)

    album_ids: List[int] = [item["id"] for item in unique_releases]
    page_data = {"items": unique_releases}

    if skip_tracks:
        top_tracks = []
        if len(results) > 2:
            res = results[2]
            if isinstance(res, tuple) and len(res) > 0:
                data = res[0]
                top_tracks = data.get("items", []) if isinstance(data, dict) else data
            elif isinstance(res, Exception):
                logger.warning("Error fetching top tracks: %s", res)

        return {"version": API_VERSION, "albums": page_data, "tracks": top_tracks}

    if not album_ids:
        return {"version": API_VERSION, "albums": page_data, "tracks": []}

    async def fetch_album_tracks(album_id: int):
        async with _album_tracks_sem:
            album_data, _, _ = await authed_get_json_v1(
                "https://api.tidal.com/v1/pages/album",
                params={
                    "albumId": album_id,
                    "countryCode": COUNTRY_CODE,
                    "deviceType": "BROWSER",
                },
                token=token,
                cred=cred,
            )

            rows = album_data.get("rows", [])
            if len(rows) < 2:
                return []
            modules = rows[1].get("modules", [])
            if not modules:
                return []
            paged_list = modules[0].get("pagedList", {})
            items = paged_list.get("items", [])
            tracks = [track.get("item", track) if isinstance(track, dict) else track for track in items]
            return tracks

    results = await asyncio.gather(
        *(fetch_album_tracks(album_id) for album_id in album_ids),
        return_exceptions=True,
    )

    tracks: List[dict] = []
    for res in results:
        if isinstance(res, Exception):
            continue
        tracks.extend(res)

    return {"version": API_VERSION, "albums": page_data, "tracks": tracks}

@app.get("/cover/")
async def get_cover(
    id: Optional[int] = Query(default=None),
    q: Optional[str] = Query(default=None),
):
    if id is None and q is None:
        raise HTTPException(status_code=400, detail="Provide id or q query param")

    token, cred = await get_tidal_token_for_cred_v1()

    def build_cover_entry(cover_slug: str, name: Optional[str], track_id: Optional[int]):
        slug = cover_slug.replace("-", "/")
        return {
            "id": track_id,
            "name": name,
            "1280": f"https://resources.tidal.com/images/{slug}/1280x1280.jpg",
            "640": f"https://resources.tidal.com/images/{slug}/640x640.jpg",
            "80": f"https://resources.tidal.com/images/{slug}/80x80.jpg",
        }

    if id is not None:
        track_data, token, cred = await authed_get_json_v1(
            f"https://api.tidal.com/v1/tracks/{id}/",
            params={"countryCode": COUNTRY_CODE},
            token=token,
            cred=cred,
        )

        album = track_data.get("album") or {}
        cover_slug = album.get("cover")
        if not cover_slug:
            raise HTTPException(status_code=404, detail="Cover not found")

        entry = build_cover_entry(
            cover_slug,
            album.get("title") or track_data.get("title"),
            album.get("id") or id,
        )
        return {"version": API_VERSION, "covers": [entry]}

    search_data, token, cred = await authed_get_json_v1(
        "https://api.tidal.com/v1/search/tracks",
        params={"countryCode": COUNTRY_CODE, "query": q, "limit": 10},
        token=token,
        cred=cred,
    )

    items = search_data.get("items", [])[:10]
    if not items:
        raise HTTPException(status_code=404, detail="Cover not found")

    covers = []
    for track in items:
        album = track.get("album") or {}
        cover_slug = album.get("cover")
        if not cover_slug:
            continue
        covers.append(
            build_cover_entry(
                cover_slug,
                track.get("title"),
                track.get("id"),
            )
        )

    if not covers:
        raise HTTPException(status_code=404, detail="Cover not found")

    return {"version": API_VERSION, "covers": covers}

@app.get("/lyrics/")
async def get_lyrics(id: int):
    url = f"https://api.tidal.com/v1/tracks/{id}/lyrics"
    data, token, cred = await authed_get_json_v1(
        url,
        params={"countryCode": COUNTRY_CODE, "locale": "en_US", "deviceType": "BROWSER"},
    )

    if not data:
        raise HTTPException(status_code=404, detail="Lyrics not found")

    return {"version": API_VERSION, "lyrics": data}

@app.get("/topvideos/")
async def get_top_videos(
    countryCode: str = Query(default="US"),
    locale: str = Query(default="en_US"),
    deviceType: str = Query(default="BROWSER"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    token, cred = await get_tidal_token_for_cred_v1()
    url = "https://api.tidal.com/v1/pages/mymusic_recommended_videos"
    params = {"countryCode": countryCode, "locale": locale, "deviceType": deviceType}

    data, token, cred = await authed_get_json_v1(
        url,
        params=params,
        token=token,
        cred=cred,
    )

    rows = data.get("rows", [])
    all_videos = []
    for row in rows:
        modules = row.get("modules", [])
        for module in modules:
            module_type = module.get("type")
            if module_type in ("VIDEO_PLAYLIST", "VIDEO_ROW", "PAGED_LIST"):
                paged_list = module.get("pagedList", {})
                if paged_list:
                    items = paged_list.get("items", [])
                    for item in items:
                        video = item.get("item", item) if isinstance(item, dict) else item
                        all_videos.append(video)
            elif module_type == "VIDEO" or (module_type and "video" in module_type.lower()):
                item = module.get("item", module)
                if isinstance(item, dict):
                    all_videos.append(item)

    paginated = all_videos[offset:offset + limit]

    return {
        "version": API_VERSION,
        "videos": paginated,
        "total": len(all_videos),
    }

@app.get("/video/")
async def get_video(
    id: int = Query(..., description="Video ID"),
    quality: str = Query(default="HIGH", description="Video quality (HIGH, MEDIUM, LOW)"),
    mode: str = Query(default="STREAM", description="Playback mode (STREAM, OFFLINE)"),
    presentation: str = Query(default="FULL", description="Asset presentation (FULL, PREVIEW)"),
):
    token, cred = await get_tidal_token_for_cred_v1()
    url = f"https://api.tidal.com/v1/videos/{id}/playbackinfo"
    params = {
        "videoquality": quality,
        "playbackmode": mode,
        "assetpresentation": presentation,
    }

    data, token, cred = await authed_get_json_v1(
        url,
        params=params,
        token=token,
        cred=cred,
    )

    return {"version": API_VERSION, "video": data}

# ================================================================================================
#                                       V1 DOWNLOAD ENDPOINTS (token.json)
# ================================================================================================

async def get_manifest_info_v1(track_id: int, quality: str = "HI_RES_LOSSLESS") -> dict:
    """Get manifest info for V1 download"""
    track_url = f"https://api.tidal.com/v1/tracks/{track_id}/playbackinfo"
    params = {"audioquality": quality, "playbackmode": "STREAM", "assetpresentation": "FULL"}
    
    result = await make_request_v1(track_url, params=params)
    playback_data = result["data"]
    
    info_result = await make_request_v1(f"https://api.tidal.com/v1/tracks/{track_id}/", params={"countryCode": COUNTRY_CODE})
    track_info = info_result["data"]
    track_title = track_info.get("title", f"track_{track_id}")
    artist_name = track_info.get("artist", {}).get("name", "unknown")
    
    manifest_b64 = playback_data.get("manifest")
    if not manifest_b64:
        raise HTTPException(400, f"No manifest for track {track_id} with quality {quality}")
    
    try:
        manifest_decoded = base64.b64decode(manifest_b64).decode()
    except Exception as e:
        raise HTTPException(400, f"Failed to decode manifest: {str(e)}")
    
    manifest_decoded = manifest_decoded.strip()
    
    # JSON manifest (direct URL)
    if manifest_decoded.startswith('{'):
        try:
            manifest_json = json.loads(manifest_decoded)
            urls = manifest_json.get("urls", [])
            codecs = manifest_json.get("codecs", "unknown")
            mime_type = manifest_json.get("mimeType", "audio/mp4")
            
            if "flac" in codecs.lower():
                ext = "flac"
                mime_type = "audio/flac"
            else:
                ext = "m4a"
            
            return {
                "track_id": track_id,
                "title": track_title,
                "artist": artist_name,
                "quality": quality,
                "codecs": codecs,
                "mime_type": mime_type,
                "type": "direct",
                "extension": ext,
                "url": urls[0] if urls else None
            }
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid JSON manifest: {str(e)}")
    
    # XML/DASH manifest
    elif manifest_decoded.startswith('<'):
        try:
            root = ET.fromstring(manifest_decoded)
            
            seg_template = root.find(".//mpd:SegmentTemplate", DASH_NS)
            if seg_template is None:
                seg_template = root.find(".//SegmentTemplate")
            
            if seg_template is None:
                raise HTTPException(400, "No SegmentTemplate found in MPD")
            
            base_url_elem = root.find(".//mpd:BaseURL", DASH_NS)
            base_url = ""
            if base_url_elem is not None and base_url_elem.text:
                base_url = base_url_elem.text
            
            init_template = seg_template.get("initialization")
            media_template = seg_template.get("media")
            
            timeline = root.find(".//mpd:SegmentTimeline", DASH_NS)
            if timeline is None:
                timeline = root.find(".//SegmentTimeline")
            
            total_segments = 0
            if timeline is not None:
                for s in timeline.findall(".//mpd:S", DASH_NS):
                    repeat = int(s.get("r", "0"))
                    total_segments += repeat + 1
            else:
                total_segments = 10
            
            return {
                "track_id": track_id,
                "title": track_title,
                "artist": artist_name,
                "quality": quality,
                "type": "dash",
                "extension": "flac",
                "base_url": base_url,
                "init_template": init_template,
                "media_template": media_template,
                "total_segments": total_segments,
                "segment_range": f"0-{total_segments - 1}"
            }
        except ET.ParseError as e:
            raise HTTPException(400, f"Invalid XML manifest: {str(e)}")
    
    else:
        raise HTTPException(400, f"Unknown manifest format")

async def download_single_track_v1(track_id: int, quality: str = "HI_RES_LOSSLESS") -> tuple[bytes, str, str]:
    """Download a single track using V1 API"""
    info = await get_manifest_info_v1(track_id, quality)
    
    ext = info.get("extension", "flac")
    mime_type = "audio/flac" if ext == "flac" else "audio/mp4"
    safe_filename = f"{info['artist']} - {info['title']}.{ext}".replace("/", "_").replace("\\", "_").replace(":", "_").replace("?", "").replace("*", "").replace('"', "'")
    
    if info["type"] == "direct":
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(info["url"])
            if resp.status_code != 200:
                raise HTTPException(502, f"Failed to download track {track_id}")
            content = resp.content
            
            if content[:4] == b'fLaC':
                return content, safe_filename, mime_type
            
            if b'ftyp' in content[:12]:
                temp_dir = tempfile.mkdtemp()
                try:
                    temp_input = os.path.join(temp_dir, "input.m4a")
                    temp_output = os.path.join(temp_dir, "output.flac")
                    with open(temp_input, "wb") as f:
                        f.write(content)
                    
                    result = subprocess.run(
                        ["ffmpeg", "-i", temp_input, "-c:a", "flac", "-compression_level", "8", temp_output, "-y"],
                        capture_output=True,
                        text=True
                    )
                    
                    if result.returncode != 0:
                        logger.error(f"FFmpeg error: {result.stderr}")
                        return content, safe_filename.replace(".flac", ".m4a"), "audio/mp4"
                    
                    with open(temp_output, "rb") as f:
                        converted = f.read()
                    return converted, safe_filename, "audio/flac"
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            
            return content, safe_filename, mime_type
    
    else:  # DASH
        temp_dir = tempfile.mkdtemp()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if info.get("init_template"):
                    init_url = info["base_url"] + info["init_template"]
                    resp = await client.get(init_url)
                    if resp.status_code != 200:
                        raise HTTPException(502, f"Failed to download init segment")
                    init_data = resp.content
                else:
                    init_data = b""
                
                segments_data = []
                for i in range(1, info["total_segments"]):
                    media_url = info["base_url"] + info["media_template"].replace("$Number$", str(i))
                    resp = await client.get(media_url)
                    if resp.status_code != 200:
                        logger.warning(f"Failed to download segment {i}")
                        continue
                    segments_data.append(resp.content)
            
            combined = init_data + b"".join(segments_data)
            
            temp_input = os.path.join(temp_dir, "input.m4a")
            temp_output = os.path.join(temp_dir, "output.flac")
            with open(temp_input, "wb") as f:
                f.write(combined)
            
            result = subprocess.run(
                ["ffmpeg", "-i", temp_input, "-c:a", "flac", "-compression_level", "8", temp_output, "-y"],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                return combined, safe_filename.replace(".flac", ".m4a"), "audio/mp4"
            
            with open(temp_output, "rb") as f:
                data = f.read()
            
            return data, safe_filename, "audio/flac"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

@app.get("/download/")
async def download_track_v1(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    """Download a single track (V1 - uses token.json)"""
    try:
        data, filename, mime_type = await download_single_track_v1(id, quality)
        return Response(
            content=data,
            media_type=mime_type,
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""}
        )
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/album/")
async def download_album_v1(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    """Download entire album as ZIP (V1 - uses token.json)"""
    token, cred = await get_tidal_token_for_cred_v1()
    items_url = f"https://api.tidal.com/v1/albums/{id}/items"
    items_data, _, _ = await authed_get_json_v1(items_url, params={"countryCode": COUNTRY_CODE, "limit": 100}, token=token, cred=cred)
    
    tracks = []
    for item in items_data.get("items", []):
        if isinstance(item, dict):
            track = item.get("item", item)
            track_id = track.get("id")
            if track_id:
                tracks.append(track_id)
    
    if not tracks:
        raise HTTPException(404, f"No tracks found in album {id}")
    
    safe_zip_name = f"album_{id}"
    logger.info(f"Downloading {len(tracks)} tracks from album {id}")
    
    async def download_with_index(idx_tid):
        idx, tid = idx_tid
        try:
            data, filename, _ = await download_single_track_v1(tid, quality)
            return (idx, data, filename, None)
        except Exception as e:
            return (idx, None, None, str(e))
    
    results = await asyncio.gather(*[download_with_index((i, tid)) for i, tid in enumerate(tracks)])
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, data, filename, error in results:
            if data:
                zip_file.writestr(filename, data)
            else:
                logger.error(f"Failed to download track {idx}: {error}")
    
    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{safe_zip_name}.zip\""}
    )

@app.get("/download/playlist/")
async def download_playlist_v1(id: str, quality: str = Query(default="HI_RES_LOSSLESS"), limit: int = Query(default=500, ge=1, le=500)):
    """Download playlist as ZIP (V1 - uses token.json)"""
    token, cred = await get_tidal_token_for_cred_v1()
    items_url = f"https://api.tidal.com/v1/playlists/{id}/items"
    
    all_tracks = []
    offset = 0
    chunk_size = 100
    
    while True:
        items_data, _, _ = await authed_get_json_v1(
            items_url, 
            params={"countryCode": COUNTRY_CODE, "limit": chunk_size, "offset": offset}, 
            token=token, 
            cred=cred
        )
        
        items = items_data.get("items", [])
        if not items:
            break
        
        for item in items:
            if isinstance(item, dict):
                track = item.get("item", item)
                if track.get("type") == "track" and track.get("id"):
                    all_tracks.append(track["id"])
        
        offset += len(items)
        if offset >= limit or len(items) < chunk_size:
            break
    
    if not all_tracks:
        raise HTTPException(404, f"No tracks found in playlist {id}")
    
    safe_zip_name = f"playlist_{id}"
    logger.info(f"Downloading {len(all_tracks)} tracks from playlist {id}")
    
    async def download_with_index(idx_tid):
        idx, tid = idx_tid
        try:
            data, filename, _ = await download_single_track_v1(tid, quality)
            return (idx, data, filename, None)
        except Exception as e:
            return (idx, None, None, str(e))
    
    results = await asyncio.gather(*[download_with_index((i, tid)) for i, tid in enumerate(all_tracks)])
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, data, filename, error in results:
            if data:
                zip_file.writestr(filename, data)
            else:
                logger.error(f"Failed to download track {idx}: {error}")
    
    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{safe_zip_name}.zip\""}
    )

@app.get("/download/multi/")
async def download_multiple_v1(ids: str = Query(...), quality: str = Query(default="HI_RES_LOSSLESS")):
    """Download multiple tracks as ZIP (V1 - uses token.json)"""
    if "+" in ids:
        track_ids = [int(x.strip()) for x in ids.split("+") if x.strip().isdigit()]
    else:
        track_ids = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    
    if not track_ids:
        raise HTTPException(400, "Provide at least one valid track ID")
    
    logger.info(f"Downloading {len(track_ids)} selected tracks")
    
    async def download_with_index(idx_tid):
        idx, tid = idx_tid
        try:
            data, filename, _ = await download_single_track_v1(tid, quality)
            return (idx, data, filename, None)
        except Exception as e:
            return (idx, None, None, str(e))
    
    results = await asyncio.gather(*[download_with_index((i, tid)) for i, tid in enumerate(track_ids)])
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, data, filename, error in results:
            if data:
                zip_file.writestr(filename, data)
            else:
                logger.error(f"Failed to download track {track_ids[idx]}: {error}")
    
    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"tracks_{len(track_ids)}.zip\""}
    )

@app.get("/download/link/")
async def download_track_link_v1(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    """Get track download link info as JSON (V1 - uses token.json)"""
    info = await get_manifest_info_v1(id, quality)
    return {"version": API_VERSION, "data": info}

@app.get("/download/link/album/")
async def download_album_link_v1(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    """Get album tracks download links as JSON (V1 - uses token.json)"""
    token, cred = await get_tidal_token_for_cred_v1()
    items_url = f"https://api.tidal.com/v1/albums/{id}/items"
    items_data, _, _ = await authed_get_json_v1(items_url, params={"countryCode": COUNTRY_CODE, "limit": 100}, token=token, cred=cred)
    
    tracks = []
    for item in items_data.get("items", []):
        if isinstance(item, dict):
            track = item.get("item", item)
            track_id = track.get("id")
            if track_id:
                tracks.append(track_id)
    
    all_tracks_info = []
    for track_id in tracks:
        try:
            info = await get_manifest_info_v1(track_id, quality)
            all_tracks_info.append(info)
        except Exception as e:
            all_tracks_info.append({"track_id": track_id, "error": str(e)})
    
    return {"version": API_VERSION, "tracks": all_tracks_info}

@app.get("/download/link/playlist/")
async def download_playlist_link_v1(id: str, quality: str = Query(default="HI_RES_LOSSLESS"), limit: int = Query(default=500, ge=1, le=500)):
    """Get playlist tracks download links as JSON (V1 - uses token.json)"""
    token, cred = await get_tidal_token_for_cred_v1()
    items_url = f"https://api.tidal.com/v1/playlists/{id}/items"
    
    all_tracks = []
    offset = 0
    chunk_size = 100
    
    while True:
        items_data, _, _ = await authed_get_json_v1(
            items_url, 
            params={"countryCode": COUNTRY_CODE, "limit": chunk_size, "offset": offset}, 
            token=token, 
            cred=cred
        )
        
        items = items_data.get("items", [])
        if not items:
            break
        
        for item in items:
            if isinstance(item, dict):
                track = item.get("item", item)
                if track.get("type") == "track" and track.get("id"):
                    all_tracks.append(track["id"])
        
        offset += len(items)
        if offset >= limit or len(items) < chunk_size:
            break
    
    all_tracks_info = []
    for track_id in all_tracks:
        try:
            info = await get_manifest_info_v1(track_id, quality)
            all_tracks_info.append(info)
        except Exception as e:
            all_tracks_info.append({"track_id": track_id, "error": str(e)})
    
    return {"version": API_VERSION, "tracks": all_tracks_info}

@app.get("/download/link/multi/")
async def download_multiple_link_v1(ids: str = Query(...), quality: str = Query(default="HI_RES_LOSSLESS")):
    """Get multiple tracks download links as JSON (V1 - uses token.json)"""
    if "+" in ids:
        track_ids = [int(x.strip()) for x in ids.split("+") if x.strip().isdigit()]
    else:
        track_ids = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    
    if not track_ids:
        raise HTTPException(400, "Provide at least one valid track ID")
    
    all_tracks_info = []
    for track_id in track_ids:
        try:
            info = await get_manifest_info_v1(track_id, quality)
            all_tracks_info.append(info)
        except Exception as e:
            all_tracks_info.append({"track_id": track_id, "error": str(e)})
    
    return {"version": API_VERSION, "tracks": all_tracks_info}

# ================================================================================================
#                                       V2 ENDPOINTS (token_hifi.json)
# ================================================================================================

async def get_streamurl_v2(track_id: int, quality: str = "HI_RES_LOSSLESS") -> dict:
    """Get stream URL using V2 API"""
    token, cred = await get_tidal_token_for_cred_v2()
    
    url = f"https://api.tidal.com/v1/tracks/{track_id}/streamurl"
    params = {"soundQuality": quality}
    
    client = await get_http_client()
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": random_user_agent(),
        "Accept-Encoding": "gzip"
    }
    
    resp = await client.get(url, headers=headers, params=params)
    _log_response("GET", url, resp)
    
    if resp.status_code == 401:
        logger.info(f"V2 token expired for track {track_id}, refreshing...")
        token, cred = await get_tidal_token_for_cred_v2(force_refresh=True, cred=cred)
        headers["authorization"] = f"Bearer {token}"
        resp = await client.get(url, headers=headers, params=params)
        _log_response("GET (retry after refresh)", url, resp)
    
    if resp.status_code != 200:
        try:
            error_data = resp.json()
            error_msg = error_data.get("userMessage", error_data.get("message", f"HTTP {resp.status_code}"))
            raise HTTPException(status_code=resp.status_code, detail=error_msg)
        except:
            resp.raise_for_status()
    
    stream_data = resp.json()
    
    info_url = f"https://api.tidal.com/v1/tracks/{track_id}/"
    info_resp = await client.get(info_url, headers={"authorization": f"Bearer {token}"}, params={"countryCode": COUNTRY_CODE})
    track_info = info_resp.json() if info_resp.status_code == 200 else {}
    
    track_title = track_info.get("title", f"track_{track_id}")
    artist_name = track_info.get("artist", {}).get("name", "unknown")
    
    codec = stream_data.get("codec", "AAC")
    actual_quality = stream_data.get("soundQuality", quality)
    
    if codec == "FLAC":
        ext = "flac"
        mime_type = "audio/flac"
        codecs = "flac"
        manifest_type = "direct"
    else:
        ext = "m4a"
        mime_type = "audio/mp4"
        codecs = "mp4a.40.2"
        manifest_type = "direct"
    
    return {
        "track_id": track_id,
        "title": track_title,
        "artist": artist_name,
        "quality": actual_quality,
        "codecs": codecs,
        "mime_type": mime_type,
        "type": manifest_type,
        "extension": ext,
        "url": stream_data.get("url")
    }

async def get_all_qualities_v2(track_id: int) -> dict:
    """Get ALL available qualities for a track"""
    qualities = ["HI_RES_LOSSLESS", "LOSSLESS", "HIGH", "LOW"]
    results = {}
    
    token, cred = await get_tidal_token_for_cred_v2()
    client = await get_http_client()
    headers = {
        "authorization": f"Bearer {token}",
        "User-Agent": random_user_agent(),
        "Accept-Encoding": "gzip"
    }
    
    for quality in qualities:
        try:
            url = f"https://api.tidal.com/v1/tracks/{track_id}/streamurl"
            params = {"soundQuality": quality}
            
            resp = await client.get(url, headers=headers, params=params)
            
            if resp.status_code == 200:
                data = resp.json()
                results[quality] = {
                    "available": True,
                    "url": data.get("url"),
                    "codec": data.get("codec"),
                    "actual_quality": data.get("soundQuality")
                }
            elif resp.status_code == 401:
                token, cred = await get_tidal_token_for_cred_v2(force_refresh=True, cred=cred)
                headers["authorization"] = f"Bearer {token}"
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    results[quality] = {
                        "available": True,
                        "url": data.get("url"),
                        "codec": data.get("codec"),
                        "actual_quality": data.get("soundQuality")
                    }
                else:
                    results[quality] = {"available": False, "error": f"HTTP {resp.status_code}"}
            else:
                results[quality] = {"available": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            results[quality] = {"available": False, "error": str(e)}
    
    return results

async def download_single_track_v2(track_id: int, quality: str = "HI_RES_LOSSLESS") -> tuple[bytes, str, str]:
    """Download a track using V2 API"""
    info = await get_streamurl_v2(track_id, quality)
    
    codec = info.get("codec", "AAC")
    actual_quality = info.get("quality", quality)
    
    if codec == "FLAC" or "flac" in info.get("url", "").lower():
        ext = "flac"
        mime_type = "audio/flac"
    else:
        ext = "m4a"
        mime_type = "audio/mp4"
    
    safe_filename = f"track_{track_id}_{actual_quality}.{ext}"
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(info["url"])
        if resp.status_code != 200:
            raise HTTPException(502, f"Failed to download track {track_id}")
        content = resp.content
        
        if codec != "FLAC" and b'ftyp' in content[:12]:
            temp_dir = tempfile.mkdtemp()
            try:
                temp_input = os.path.join(temp_dir, "input.m4a")
                temp_output = os.path.join(temp_dir, "output.flac")
                with open(temp_input, "wb") as f:
                    f.write(content)
                
                result = subprocess.run(
                    ["ffmpeg", "-i", temp_input, "-c:a", "flac", "-compression_level", "8", temp_output, "-y"],
                    capture_output=True,
                    text=True
                )
                
                if result.returncode == 0:
                    with open(temp_output, "rb") as f:
                        content = f.read()
                    safe_filename = safe_filename.replace(".m4a", ".flac")
                    mime_type = "audio/flac"
                else:
                    logger.error(f"FFmpeg error: {result.stderr}")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        return content, safe_filename, mime_type

@app.get("/trackv2/")
async def get_track_v2(id: int, quality: str = Query(default="HI_RES_LOSSLESS"), show_all_qualities: bool = Query(default=False)):
    if show_all_qualities:
        result = await get_all_qualities_v2(id)
        return {
            "track_id": id,
            "available_qualities": result,
            "message": "Use quality parameter to select specific quality"
        }
    else:
        info = await get_streamurl_v2(id, quality)
        return {"version": API_VERSION, "data": info}

@app.get("/trackv2/quality/")
async def get_track_v2_quality(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    result = await get_streamurl_v2(id, quality)
    return result

@app.get("/downloadv2/")
async def download_track_v2(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    try:
        data, filename, mime_type = await download_single_track_v2(id, quality)
        return Response(
            content=data,
            media_type=mime_type,
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""}
        )
    except Exception as e:
        logger.error(f"Download V2 failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/downloadv2/link/")
async def download_track_link_v2(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    info = await get_streamurl_v2(id, quality)
    return {"version": API_VERSION, "data": info}

@app.get("/downloadv2/album/")
async def download_album_v2(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    token, cred = await get_tidal_token_for_cred_v2()
    items_url = f"https://api.tidal.com/v1/albums/{id}/items"
    items_data, _, _ = await authed_get_json_v1(items_url, params={"countryCode": COUNTRY_CODE, "limit": 100}, token=token, cred=cred)
    
    tracks = []
    for item in items_data.get("items", []):
        if isinstance(item, dict):
            track = item.get("item", item)
            track_id = track.get("id")
            if track_id:
                tracks.append(track_id)
    
    if not tracks:
        raise HTTPException(404, f"No tracks found in album {id}")
    
    safe_zip_name = f"album_{id}_{quality}"
    logger.info(f"Downloading {len(tracks)} tracks from album {id} using V2 with quality {quality}")
    
    async def download_with_index(idx_tid):
        idx, tid = idx_tid
        try:
            data, filename, _ = await download_single_track_v2(tid, quality)
            return (idx, data, filename, None)
        except Exception as e:
            return (idx, None, None, str(e))
    
    results = await asyncio.gather(*[download_with_index((i, tid)) for i, tid in enumerate(tracks)])
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, data, filename, error in results:
            if data:
                zip_file.writestr(filename, data)
            else:
                logger.error(f"Failed to download track {idx}: {error}")
    
    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{safe_zip_name}.zip\""}
    )

@app.get("/downloadv2/playlist/")
async def download_playlist_v2(id: str, quality: str = Query(default="HI_RES_LOSSLESS"), limit: int = Query(default=500, ge=1, le=500)):
    token, cred = await get_tidal_token_for_cred_v2()
    items_url = f"https://api.tidal.com/v1/playlists/{id}/items"
    
    all_tracks = []
    offset = 0
    chunk_size = 100
    
    while True:
        items_data, _, _ = await authed_get_json_v1(
            items_url, 
            params={"countryCode": COUNTRY_CODE, "limit": chunk_size, "offset": offset}, 
            token=token, 
            cred=cred
        )
        
        items = items_data.get("items", [])
        if not items:
            break
        
        for item in items:
            if isinstance(item, dict):
                track = item.get("item", item)
                if track.get("type") == "track" and track.get("id"):
                    all_tracks.append(track["id"])
        
        offset += len(items)
        if offset >= limit or len(items) < chunk_size:
            break
    
    if not all_tracks:
        raise HTTPException(404, f"No tracks found in playlist {id}")
    
    safe_zip_name = f"playlist_{id}_{quality}"
    logger.info(f"Downloading {len(all_tracks)} tracks from playlist {id} using V2 with quality {quality}")
    
    async def download_with_index(idx_tid):
        idx, tid = idx_tid
        try:
            data, filename, _ = await download_single_track_v2(tid, quality)
            return (idx, data, filename, None)
        except Exception as e:
            return (idx, None, None, str(e))
    
    results = await asyncio.gather(*[download_with_index((i, tid)) for i, tid in enumerate(all_tracks)])
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, data, filename, error in results:
            if data:
                zip_file.writestr(filename, data)
            else:
                logger.error(f"Failed to download track {idx}: {error}")
    
    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{safe_zip_name}.zip\""}
    )

@app.get("/downloadv2/multi/")
async def download_multiple_v2(ids: str = Query(...), quality: str = Query(default="HI_RES_LOSSLESS")):
    if "+" in ids:
        track_ids = [int(x.strip()) for x in ids.split("+") if x.strip().isdigit()]
    else:
        track_ids = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    
    if not track_ids:
        raise HTTPException(400, "Provide at least one valid track ID")
    
    logger.info(f"Downloading {len(track_ids)} selected tracks using V2 with quality {quality}")
    
    async def download_with_index(idx_tid):
        idx, tid = idx_tid
        try:
            data, filename, _ = await download_single_track_v2(tid, quality)
            return (idx, data, filename, None)
        except Exception as e:
            return (idx, None, None, str(e))
    
    results = await asyncio.gather(*[download_with_index((i, tid)) for i, tid in enumerate(track_ids)])
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, data, filename, error in results:
            if data:
                zip_file.writestr(filename, data)
            else:
                logger.error(f"Failed to download track {track_ids[idx]}: {error}")
    
    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"tracks_{len(track_ids)}_{quality}.zip\""}
    )

@app.get("/downloadv2/link/album/")
async def download_album_link_v2(id: int, quality: str = Query(default="HI_RES_LOSSLESS")):
    token, cred = await get_tidal_token_for_cred_v2()
    items_url = f"https://api.tidal.com/v1/albums/{id}/items"
    items_data, _, _ = await authed_get_json_v1(items_url, params={"countryCode": COUNTRY_CODE, "limit": 100}, token=token, cred=cred)
    
    tracks = []
    for item in items_data.get("items", []):
        if isinstance(item, dict):
            track = item.get("item", item)
            track_id = track.get("id")
            if track_id:
                tracks.append(track_id)
    
    all_tracks_info = []
    for track_id in tracks:
        try:
            info = await get_streamurl_v2(track_id, quality)
            all_tracks_info.append(info)
        except Exception as e:
            all_tracks_info.append({"track_id": track_id, "error": str(e)})
    
    return {
        "album_id": id,
        "requested_quality": quality,
        "tracks": all_tracks_info
    }

@app.get("/downloadv2/link/playlist/")
async def download_playlist_link_v2(id: str, quality: str = Query(default="HI_RES_LOSSLESS"), limit: int = Query(default=500, ge=1, le=500)):
    token, cred = await get_tidal_token_for_cred_v2()
    items_url = f"https://api.tidal.com/v1/playlists/{id}/items"
    
    all_tracks = []
    offset = 0
    chunk_size = 100
    
    while True:
        items_data, _, _ = await authed_get_json_v1(
            items_url, 
            params={"countryCode": COUNTRY_CODE, "limit": chunk_size, "offset": offset}, 
            token=token, 
            cred=cred
        )
        
        items = items_data.get("items", [])
        if not items:
            break
        
        for item in items:
            if isinstance(item, dict):
                track = item.get("item", item)
                if track.get("type") == "track" and track.get("id"):
                    all_tracks.append(track["id"])
        
        offset += len(items)
        if offset >= limit or len(items) < chunk_size:
            break
    
    all_tracks_info = []
    for track_id in all_tracks:
        try:
            info = await get_streamurl_v2(track_id, quality)
            all_tracks_info.append(info)
        except Exception as e:
            all_tracks_info.append({"track_id": track_id, "error": str(e)})
    
    return {
        "playlist_id": id,
        "requested_quality": quality,
        "total_tracks": len(all_tracks),
        "tracks": all_tracks_info
    }

@app.get("/downloadv2/link/multi/")
async def download_multiple_link_v2(ids: str = Query(...), quality: str = Query(default="HI_RES_LOSSLESS")):
    if "+" in ids:
        track_ids = [int(x.strip()) for x in ids.split("+") if x.strip().isdigit()]
    else:
        track_ids = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    
    if not track_ids:
        raise HTTPException(400, "Provide at least one valid track ID")
    
    all_tracks_info = []
    for track_id in track_ids:
        try:
            info = await get_streamurl_v2(track_id, quality)
            all_tracks_info.append(info)
        except Exception as e:
            all_tracks_info.append({"track_id": track_id, "error": str(e)})
    
    return {
        "requested_quality": quality,
        "tracks": all_tracks_info
    }

@app.get("/trackv2/check/")
async def check_track_qualities(id: int):
    return await get_all_qualities_v2(id)

# ================================================================================================
#                                       MAIN ENTRY POINT
# ================================================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)