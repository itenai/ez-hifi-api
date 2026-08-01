#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════════════════════════════════╗
║                     Tidal Auth - Unified Token Generator                                      ║
║                                                                                               ║
║  V1 (token.json)       → Standard Tidal API (r_usr+w_usr+w_sub)                              ║
║  V2 (token_hifi.json)  → HiFi/Specialized API (r_usr)                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import json
import os
import random
import webbrowser
from pathlib import Path
import base64

import httpx
import rich

# ================================================================================================
#                                       CONFIGURATION
# ================================================================================================

TOKEN_FILE_V1 = Path(os.getenv("TOKEN_FILE_V1", Path(__file__).resolve().parent / "token.json"))
TOKEN_FILE_V2 = Path(os.getenv("TOKEN_FILE_V2", Path(__file__).resolve().parent / "token_hifi.json"))

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

def _random_ua() -> str:
    return _custom_ua if _custom_ua else random.choice(_USER_AGENTS)

# ================================================================================================
#                                       CREDENTIAL CONFIGURATIONS
# ================================================================================================

# V1 Credentials (token.json) - Standard Tidal API
V1_CLIENT_ID = base64.b64decode("ZlgySnhkbW50WldLMGl4VA==").decode("iso-8859-1")
V1_CLIENT_SECRET = base64.b64decode(
    "MU5tNUFmREFqeHJnSkZKYktOV0xlQXlLR1ZHbUlOdVhQUExIVlhBdnhBZz0=",
).decode("iso-8859-1")
V1_SCOPE = "r_usr+w_usr+w_sub"

# V2 Credentials (token_hifi.json) - HiFi/Specialized API
V2_CLIENT_ID = base64.b64decode("OEt5N0lRQXoyQVZudXk0eA==").decode("iso-8859-1")
V2_CLIENT_SECRET = base64.b64decode(
    "VFg2QzJ6cHNTNEpubWR1emx0RVlFWG5wTTROTzhWckl1QkNuaE1ISUhwbz0=",
).decode("iso-8859-1")
V2_SCOPE = "r_usr"

# ================================================================================================
#                                       AUTH CLASSES
# ================================================================================================

class Hifi:
    def __init__(self, client_id, scope, url, client_secret):
        self.client_id = client_id
        self.scope = scope
        self.url = url
        self.client_secret = client_secret

    @staticmethod
    def Quality(quality):
        rate = {quality: "HI_RES"}
        return rate[quality]


class Auth(Hifi):
    def __init__(self, client_id, scope, url, client_secret):
        super().__init__(client_id, scope, url, client_secret)
        self.response = None

    async def get_auth_response(self):
        data = {"client_id": self.client_id, "scope": self.scope}
        headers = {
            "User-Agent": _random_ua(),
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Platform": "android",
        }

        async with httpx.AsyncClient(headers=headers) as client:
            response = await client.post(self.url, data=data, headers=headers)
            self.response = response

    def __str__(self):
        return str(self.response)


# ================================================================================================
#                                       TOKEN MANAGEMENT
# ================================================================================================

def load_tokens(token_file: Path):
    """Load tokens from the specified file"""
    if token_file.exists():
        with open(token_file, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return [data]
    return []


def save_token_entry(entry, token_file: Path):
    """Save a token entry to the specified file"""
    tokens = load_tokens(token_file)
    tokens = [t for t in tokens if not (
        t.get("client_ID") == entry["client_ID"] and t.get("refresh_token") == entry["refresh_token"]
    )]
    tokens.append(entry)
    with open(token_file, "w") as f:
        json.dump(tokens, f, indent=4)


def get_token_file(mode: str) -> Path:
    """Get the appropriate token file based on mode"""
    if mode == "v2":
        return TOKEN_FILE_V2
    return TOKEN_FILE_V1


def get_credentials(mode: str) -> tuple:
    """Get the appropriate credentials based on mode"""
    if mode == "v2":
        return V2_CLIENT_ID, V2_CLIENT_SECRET, V2_SCOPE
    return V1_CLIENT_ID, V1_CLIENT_SECRET, V1_SCOPE


# ================================================================================================
#                                       NETWORK HELPERS
# ================================================================================================

async def poll_for_authorization(url, data, auth):
    """Poll for authorization completion"""
    headers = {
        "User-Agent": _random_ua(),
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Platform": "android",
    }
    async with httpx.AsyncClient(headers=headers) as client:
        while True:
            response = await client.post(url, data=data, auth=auth)
            if response.status_code == 200:
                return response.json()
            await asyncio.sleep(5)


async def fetch_credentials_from_gist():
    """Fetch Tidal credentials from GitHub Gist (public pool)"""
    url = "https://api.github.com/gists/48d01f5a24b4b7b37f19443977c22cd6"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        gist_data = resp.json()
        
        content_str = gist_data["files"]["tidal-api-key.json"]["content"]
        keys_data = json.loads(content_str)
        
        v1_creds = [(V1_CLIENT_ID, V1_CLIENT_SECRET)]
        v2_creds = [(V2_CLIENT_ID, V2_CLIENT_SECRET)]
        
        for key_entry in keys_data["keys"]:
            if key_entry.get("valid") == "True":
                cred = (key_entry["clientId"], key_entry["clientSecret"])
                formats = key_entry.get("formats", "").lower()
                
                if "hifi" in formats:
                    v2_creds.append(cred)
                else:
                    v1_creds.append(cred)
        
        return v1_creds, v2_creds


# ================================================================================================
#                                       MAIN AUTH FLOW
# ================================================================================================

async def run_auth_flow(mode: str = "v1", use_gist: bool = False):
    """
    Run the authentication flow for the specified mode
    
    Args:
        mode: "v1" or "v2"
        use_gist: If True, attempt to fetch additional credentials from Gist
    """
    mode = mode.lower()
    if mode not in ("v1", "v2"):
        print(f"Invalid mode: {mode}. Choose 'v1' or 'v2'.")
        return False
    
    token_file = get_token_file(mode)
    client_id, client_secret, scope = get_credentials(mode)
    
    # Get credentials from Gist if requested
    if use_gist:
        try:
            v1_creds, v2_creds = await fetch_credentials_from_gist()
            if mode == "v1" and v1_creds:
                all_creds = v1_creds
            elif mode == "v2" and v2_creds:
                all_creds = v2_creds
            else:
                all_creds = [(client_id, client_secret)]
        except Exception as e:
            rich.print(f"[yellow]Failed to fetch credentials from Gist: {e}[/yellow]")
            all_creds = [(client_id, client_secret)]
    else:
        all_creds = [(client_id, client_secret)]
    
    random.shuffle(all_creds)
    
    async def run_link_flow():
        authrize = None
        for cid, csecret in all_creds:
            rich.print(f"[cyan]Trying Client ID: {cid[:15]}...[/cyan]")
            authrize = Auth(
                client_id=cid,
                scope=scope,
                url="https://auth.tidal.com/v1/oauth2/device_authorization",
                client_secret=csecret,
            )

            try:
                await authrize.get_auth_response()
                if authrize.response.status_code == 200:
                    break
                elif authrize.response.status_code == 401:
                    rich.print(f"[yellow]Client ID {cid[:15]}... failed with 401. Trying next...[/yellow]")
                    continue
                else:
                    rich.print(f"[red]Error {authrize.response.status_code}. Trying next...[/red]")
                    continue
            except Exception as e:
                rich.print(f"[red]Exception: {e}. Trying next...[/red]")
                continue
        else:
            rich.print("[red]All credentials failed.[/red]")
            return False

        res = authrize.response.json()

        verifyurl = res["verificationUriComplete"]
        dcode = res["deviceCode"]

        rich.print(f"\n[bold green]Open this URL in your browser:[/bold green]")
        rich.print(f"[bold blue]{verifyurl}[/bold blue]")
        rich.print(f"\n[bold yellow]Device Code:[/bold yellow] {dcode}\n")

        # Open browser automatically
        webbrowser.open(verifyurl)

        # Poll for token
        url2 = "https://auth.tidal.com/v1/oauth2/token"
        data2 = {
            "client_id": authrize.client_id,
            "scope": scope,
            "device_code": dcode,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        basic = (authrize.client_id, authrize.client_secret)

        rich.print("[cyan]Waiting for authorization...[/cyan]")
        auth_response = await poll_for_authorization(url2, data2, basic)

        access_token = auth_response["access_token"]
        refresh_token = auth_response["refresh_token"]
        user_id = auth_response["user"]["userId"]
        
        accs = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "userID": user_id,
            "client_ID": authrize.client_id,
            "client_secret": authrize.client_secret,
        }
        
        # Save token
        save_token_entry(accs, token_file)
        rich.print("[green]✓ Token saved successfully![/green]")
        rich.print(accs)
        
        # Verify token works
        acs_tok = access_token
        quality = "HI_RES"
        
        url3 = f"https://api.tidal.com/v1/tracks/493546859/playbackinfopostpaywall?countryCode=en_US&audioquality={quality}&playbackmode=STREAM&assetpresentation=FULL"
        headers = {
            "authorization": f"Bearer {acs_tok}",
            "User-Agent": _random_ua(),
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Platform": "android",
            "X-Tidal-Platform": "android",
        }

        async with httpx.AsyncClient(headers=headers) as client:
            res3 = await client.get(url3)
            if res3.status_code == 200:
                rich.print("[green]✓ Token verified successfully![/green]")
            else:
                rich.print(f"[yellow]Token verification returned status {res3.status_code}[/yellow]")

        return True

    success = await run_link_flow()
    return success


# ================================================================================================
#                                       MAIN ENTRY POINT
# ================================================================================================

async def main():
    print("""
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                       Tidal Auth - Unified Token Generator                          ║
║                                                                                     ║
║  V1 (token.json)       → Standard Tidal API (r_usr+w_usr+w_sub)                    ║
║  V2 (token_hifi.json)  → HiFi/Specialized API (r_usr)                              ║
║                                                                                     ║
║  Option 3/4 will try multiple credentials from a public Gist pool                  ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
    """)
    
    print("Select mode:")
    print("  1. V1 - Standard Tidal API (token.json)")
    print("  2. V2 - HiFi/Specialized API (token_hifi.json)")
    print("  3. V1 with Gist credentials (try multiple)")
    print("  4. V2 with Gist credentials (try multiple)")
    
    choice = input("\nEnter choice (1-4): ").strip()
    
    mode = "v1"
    use_gist = False
    
    if choice == "1":
        mode = "v1"
        use_gist = False
    elif choice == "2":
        mode = "v2"
        use_gist = False
    elif choice == "3":
        mode = "v1"
        use_gist = True
    elif choice == "4":
        mode = "v2"
        use_gist = True
    else:
        print("[red]Invalid choice. Defaulting to V1.[/red]")
        mode = "v1"
        use_gist = False
    
    print(f"\n[cyan]Generating {mode.upper()} token...[/cyan]")
    success = await run_auth_flow(mode, use_gist)
    
    if success:
        print("\n[green]✓ Token generation complete![/green]")
        token_file = get_token_file(mode)
        print(f"[cyan]Token saved to: {token_file}[/cyan]")
    else:
        print("\n[red]✗ Token generation failed.[/red]")


if __name__ == "__main__":
    asyncio.run(main())