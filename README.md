# Streamora Provider Script Development Guide

This guide covers everything needed to write a production-quality Streamora provider script.
Follow every section — missing any part means the script will need fixes later.

---

## 1. File Structure

```
/home/streamora/scripts/
  your_provider.py          <- Your script
  your_provider/            <- Data directory (auto-created)
    session.json            <- Cached auth tokens
    device_id.txt           <- Cached device ID
    channel_cache.json      <- Cached channel metadata
    token_user@email.txt    <- Per-user token cache
```

**Data directory convention**: Use the script name (without `.py`) as the subdirectory:

```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = os.path.splitext(os.path.basename(__file__))[0]
DATA_DIR = os.path.join(SCRIPT_DIR, SCRIPT_NAME)
os.makedirs(DATA_DIR, exist_ok=True)
```

---

## 2. Argument Parsing

Streamora passes all Credentials JSON keys + Config JSON keys as `key=value` command-line args.

```python
def parse_param(args, key, default=""):
    for a in args:
        if a.startswith(key + "="):
            return a.split("=", 1)[1]
    return default

action     = parse_param(sys.argv, "action")      # channels|manifest|cdm|interactive
channel_id = parse_param(sys.argv, "id")           # channel ID (for manifest/cdm)
email      = parse_param(sys.argv, "user")         # from Credentials JSON
password   = parse_param(sys.argv, "password")     # from Credentials JSON
proxy      = parse_param(sys.argv, "proxy")        # from Config JSON
```

**Standard keys**: `action`, `id`, `user`, `password`, `proxy`, `wvd_device_path`.
Any custom keys in Credentials/Config JSON are also passed automatically.

---

## 3. Output Rules

| Stream     | Purpose                | Format                  |
|------------|------------------------|-------------------------|
| **stdout** | JSON result ONLY       | Parsed by Streamora     |
| **stderr** | Log messages           | Shown in terminal UI    |

**NEVER print debug/log to stdout** — it breaks JSON parsing.

```python
def log(msg):
    print(msg, file=sys.stderr, flush=True)
```

### Log Color Tags (optional)

```
[color:red]Error message         -> red text
[color:green]Success message     -> green text
[color:yellow]Warning message    -> yellow text
[color:blue]Info message         -> blue text
```

---

## 4. Actions (Required)

Every script MUST implement these 4 actions:

### 4.1 `action=channels` — Return channel list

Fetch all available channels and return as JSON to stdout.

```python
print(json.dumps({
    "channels": [
        {
            "id": "unique_channel_id",     # REQUIRED — used in manifest/cdm calls
            "name": "Channel Name",         # REQUIRED — display name
            "logo": "https://...",          # URL or empty string (MUST be string, never object)
            "group": "Group Name",          # grouping in UI
            "source_url": "",               # empty — fetched at manifest time
            "source_type": "dash",          # dash|hls|direct
            "drm_type": "widevine",         # widevine|clearkey|playready|none
            "epg_id": "",                   # EPG identifier
        }
    ]
}))
```

**IMPORTANT**: `logo` MUST be a string. If the API returns an object, convert it:
```python
"logo": tile.get("Image", "") if isinstance(tile.get("Image"), str) else "",
```

#### Profile Defaults (Optional)

Scripts can auto-configure stream profiles by returning `profile_defaults` alongside channels.
Keys map to group names. Applied only when profiles are first created.

```python
print(json.dumps({
    "channels": [...],
    "profile_defaults": {
        "Group Name": {
            "running_mode": "internal_remux",    # internal_remux (DASH) | ffmpeg_remux (HLS)
            "output_format": "hls",              # hls | mpegts
            "manifest_refresh_min": 5,           # CDN token refresh interval (0 = off)
            "skip_prestart_refresh": 0,          # 1 = skip script call before channel start
            "proxy_from_provider": True,         # Copy provider's proxy to profile
            "shared_headers": 1,                 # 1 = headers stored on profile, not per-channel
            "remux_segment_duration": 6,         # HLS segment duration (seconds)
            "remux_playlist_window": 10,         # Segments in HLS playlist
            "remux_playlist_duration": 60,       # Max playlist duration (seconds)
            "live_edge_segments": 0,             # Skip to last N segs (0 = play from start)
        }
    }
}))
```

**Fields reference:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `running_mode` | string | `""` | `internal_remux` (DASH, recommended), `ffmpeg_remux` (HLS) |
| `output_format` | string | `""` | `hls` (recommended) or `mpegts` |
| `manifest_refresh_min` | int | `0` | Minutes between CDN token refresh (0 = disabled) |
| `skip_prestart_refresh` | int | `0` | 1 = skip pre-start script call |
| `proxy_from_provider` | bool | `false` | Copy proxy from provider Config JSON |
| `shared_headers` | int | `0` | 1 = CDN headers on profile (shared across channels) |
| `remux_segment_duration` | int | `6` | HLS segment length (internal_remux) |
| `remux_playlist_window` | int | `10` | Segments in m3u8 |
| `remux_playlist_duration` | int | `60` | Max playlist seconds |
| `live_edge_segments` | int | `0` | Skip to last N segments for live |

**Shared Headers**: Set `shared_headers: 1` when all channels share the same CDN auth token.
Token refreshes update the profile instead of each channel, so one refresh benefits all channels.

**Update existing profiles**: Check "Update profile settings from script" in the sync modal
to re-apply defaults. Only script-defined values are changed — user customizations preserved.

### 4.2 `action=manifest` — Return stream URL + DRM info + keys

Called with `id=channel_id`. Returns the playback URL and DRM details.

```python
print(json.dumps({
    "manifest_url": "https://...",              # REQUIRED — stream URL
    "source_type": "dash",                       # dash|hls|direct
    "drm_type": "widevine",                      # widevine|clearkey|none
    "manifest_headers": {"key": "value"},        # headers for fetching manifest (dict)
    "media_headers": {},                         # headers for media/segment requests (dict, optional)
    "license_url": "https://...",                # Widevine license server URL
    "license_headers": {"Authorization": "..."},  # headers for license requests (dict)
    "pssh": "AAAA...",                           # Widevine PSSH base64
    "keys": "kid:key\nkid2:key2",               # Pre-acquired keys (newline-separated)
    "error": "",                                 # Empty on success
}))
```

**`media_headers`**: HTTP headers for downloading segments. If omitted or empty and the profile
has `shared_headers=1`, `manifest_headers` are automatically copied to `media_headers`. Use this
when the CDN requires the same auth token for both manifest and segment requests (most providers).

**Key acquisition in manifest**: Always try to acquire keys in the manifest action itself.
This is more reliable than relying on built-in CDM because:
- The script has the auth token needed for the license server
- The script can route through the correct proxy
- Built-in CDM may not have the right headers

```python
# Extract PSSH from MPD/HLS
pssh = extract_wv_pssh(manifest_url, headers)

# Acquire keys
keys_str = ""
if pssh:
    keys_str = acquire_keys_wv(token, license_url, pssh)

output = {
    "manifest_url": url,
    "keys": keys_str,        # <-- Script provides keys directly
    "pssh": pssh,             # <-- Also provide for built-in CDM fallback
    "license_url": la_url,    # <-- Also provide for built-in CDM fallback
    "license_headers": {...}, # <-- Include auth token for built-in CDM fallback
    ...
}
```

### 4.3 `action=cdm` — Key acquisition fallback

Called if manifest didn't return keys. Return empty with no error if keys
are already handled in manifest:

```python
# If keys are acquired in manifest action, return empty — no error
print(json.dumps({"keys": "", "error": ""}))
```

**NEVER return an error string here** unless there's a real error.
An error in CDM causes Streamora to log `cdm script error: ...` for every channel.

### 4.4 `action=interactive` — Terminal mode

Interactive menu for testing and debugging. Users type input via stdin.

```python
elif action == "interactive":
    banner()
    # ... authenticate ...

    while True:
        print("Choose option (1-4, 0=exit):")
        sys.stdout.flush()           # REQUIRED — flush before input()
        choice = input().strip()
        # ... handle options ...
```

---

## 5. Profile Auto-Configuration

When Streamora syncs a provider, it creates profiles from channel `group` names. Scripts
define profile defaults so these profiles come pre-configured with the right settings.

### Typical DASH + Widevine Provider

```python
profile = {
    "running_mode": "internal_remux",
    "output_format": "hls",
    "manifest_refresh_min": 5,
    "proxy_from_provider": True,
    "shared_headers": 1,
    "remux_segment_duration": 6,
    "remux_playlist_window": 10,
    "remux_playlist_duration": 60,
}
groups = set(ch["group"] for ch in channels if ch.get("group"))
profile_defaults = {g: profile for g in groups}
print(json.dumps({"channels": channels, "profile_defaults": profile_defaults}))
```

### Typical HLS Provider

```python
profile = {
    "running_mode": "ffmpeg_remux",
    "output_format": "hls",
    "proxy_from_provider": True,
    "shared_headers": 1,
}
groups = set(ch["group"] for ch in channels if ch.get("group"))
profile_defaults = {g: profile for g in groups}
print(json.dumps({"channels": channels, "profile_defaults": profile_defaults}))
```

### When to Use Each Setting

| Scenario | Settings |
|----------|----------|
| DASH + Widevine + geo-proxy | `internal_remux`, `proxy_from_provider: True`, `shared_headers: 1` |
| DASH + Widevine, no proxy | `internal_remux`, `shared_headers: 1` |
| HLS + Widevine | `ffmpeg_remux`, `shared_headers: 1` |
| Expiring CDN tokens (Akamai, DAZN) | Add `manifest_refresh_min: 5` (adjust for token lifetime) |
| Static URLs, keys don't expire | Add `skip_prestart_refresh: 1` |
| Live streams, start near live edge | Add `live_edge_segments: 5` |

---

## 6. Authentication — Session Fallback Pattern

**This is the most important pattern. Every script MUST implement this.**

The fallback chain ensures the provider keeps working even if:
- The user changes their password
- Credentials are temporarily wrong
- The login API is down
- No credentials are set but a cached session exists

### Auth Priority Order

```
authenticate()
  |
  ├─ 1. Try cached session/token
  |     ✓ Valid → return immediately (fastest, no network)
  |     ✗ Expired → continue
  |
  ├─ 2. Have user + password? → try login
  |     ✓ Success → save session, return
  |     ✗ Fail → DON'T EXIT, save error, continue ↓
  |
  ├─ 3. Login failed + cached session exists? → RETRY cached session
  |     ✓ Success → return (wrong password but old session still works)
  |     ✗ Fail → continue ↓
  |
  └─ 4. Nothing worked → exit with descriptive error
```

### Template Implementation

```python
def authenticate():
    cached_tok = None

    # Find cached token (even without credentials set)
    if email:
        cached_tok = loadToken(email)
    else:
        token_files = glob.glob(os.path.join(PATH, "token_*.txt"))
        if token_files:
            with open(token_files[0]) as f:
                cached_tok = f.read().strip()

    # 1. Try cached token first
    if cached_tok:
        # ... validate/refresh ...
        if valid:
            return cached_tok

    # 2. Have credentials? Try login
    login_error = None
    if email and password:
        try:
            token = do_login(email, password)
            save_token(token)
            return token
        except Exception as e:
            login_error = str(e)         # DON'T EXIT — save error
            log(f"[color:yellow]Login failed: {e}")

    # 3. Login failed but cached token exists? Use as fallback
    if login_error and cached_tok:
        log("[color:yellow]Credentials failed, falling back to cached token...")
        return cached_tok

    # 4. Nothing worked — descriptive error
    if login_error:
        print(json.dumps({"error": f"Login failed: {login_error}"}))
    else:
        print(json.dumps({
            "error": "No cached session and no credentials provided.\n"
                     "Set Credentials JSON: {\"user\": \"email@example.com\", \"password\": \"yourpass\"}\n"
                     "Note: use key \"user\" (not \"username\" or \"email\").\n"
                     "Optional Config JSON: {\"proxy\": \"http://user:pass@host:port\"}"
        }))
    sys.exit(1)
```

**Key rules**:
- NEVER `sys.exit(1)` on login failure if a cached session exists
- ALWAYS search for cached tokens even when `user` is empty (glob for `token_*.txt`)
- ALWAYS show the expected Credentials JSON format in "no credentials" error

---

## 7. Error Messages — Be Specific and Helpful

Bad error messages waste everyone's time. Follow these patterns:

### Wrong credentials
```python
raise RuntimeError(
    "Wrong email or password. Check your Credentials JSON uses the correct keys.\n"
    "  Expected format: {\"user\": \"email@example.com\", \"password\": \"yourpass\"}\n"
    "  Common mistake: using 'username' or 'email' instead of 'user'")
```

### Proxy blocked
```python
raise RuntimeError(
    "Service blocked the request. This usually means:\n"
    "  - The proxy IP is blocked (datacenter IP). Use a residential proxy.\n"
    "  - Too many login attempts. Wait a few minutes and retry.")
```

### No credentials
```python
print(json.dumps({
    "error": "No cached session and no credentials provided.\n"
             "Set Credentials JSON: {\"user\": \"email\", \"password\": \"pass\"}\n"
             "Note: use key \"user\" (not \"username\" or \"email\").\n"
             "Optional Config JSON: {\"proxy\": \"http://user:pass@host:port\"}"
}))
```

### Account/subscription issue
```python
raise RuntimeError("Content needs additional subscription")
raise RuntimeError("Account suspended or inactive")
```

---

## 8. Interactive Mode — Full Feature Checklist

Every interactive mode MUST have:

### 8.1 Menu with numbered options

```
Options:
  1 - List all channels
  2 - List live events (if applicable)
  3 - Get manifest + keys for a channel (by # or ID)
  4 - Show session info
  0 - Exit
```

### 8.2 Guard on manifest option

Don't let users request manifest without loading channels first:

```python
elif choice == "3":
    if not cached_items:
        log("[color:yellow]No channels loaded. Use option 1 first.")
        continue
```

### 8.3 Hints after channel listing

```python
log(f"Total: {len(channels)} channels")
log("[color:yellow]Tip: Use option 3, then type the # number to get manifest + keys")
```

### 8.4 Key dumping in manifest option

The manifest option MUST extract PSSH and dump Widevine keys:

```python
log("Extracting PSSH...")
pssh = extract_wv_pssh(mpd_url, headers)
if pssh:
    log(f"  PSSH: {pssh[:80]}...")
    log("Acquiring Widevine keys...")
    keys = acquire_keys_wv(token, license_url, pssh)
    if keys:
        log(f"[color:green]  Keys ({len(keys.splitlines())}):")
        for kl in keys.splitlines():
            log(f"    {kl}")
    else:
        log("[color:yellow]  No keys acquired (pywidevine may not be installed or WVD missing)")
else:
    log("[color:yellow]  Could not extract PSSH from MPD")
```

### 8.5 Session info option

Show everything useful for debugging:

```python
elif choice == "4":
    log("[color:blue]Session Info:")
    log(f"  User: {email or '(from cache)'}")
    log(f"  Proxy: {proxy or '(none)'}")
    log(f"  Device ID: {device_id}")
    log(f"  Country: {country}")
    log(f"  Token expires: {expiry} ({remaining}h {min}m)")
    log(f"  Channels cached: {len(cached_items)}")
```

### 8.6 Credential prompt fallback

Only prompt for credentials if no cached session exists:

```python
_has_cache = bool(loadToken(email) if email else glob.glob(os.path.join(PATH, "token_*.txt")))
if not _has_cache and (not email or not password):
    print("Enter email:")
    sys.stdout.flush()
    email = input().strip()
    print("Enter password:")
    sys.stdout.flush()
    password = input().strip()
```

---

## 9. Widevine PSSH Extraction

### From DASH MPD

```python
WV_SYSTEM_ID = "edef8ba979d64acea3c827dcd51d21ed"

def extract_wv_pssh(mpd_url, headers=None):
    r = requests.get(mpd_url, headers=headers, timeout=15)
    for m in re.findall(r"<(?:cenc:)?pssh[^>]*>([A-Za-z0-9+/=]{20,})</(?:cenc:)?pssh>", r.text):
        raw = base64.b64decode(m)
        if raw[12:28].hex() == WV_SYSTEM_ID:
            return m
    return ""
```

### From HLS manifest

```python
# Check master playlist for EXT-X-SESSION-KEY
for line in text.splitlines():
    if "EXT-X-SESSION-KEY" in line or "EXT-X-KEY" in line:
        uri_match = re.search(r'URI="([^"]+)"', line)
        if uri_match:
            pssh = uri_match.group(1).split(",")[-1]
```

**Always filter by Widevine SystemID** — MPDs may contain PlayReady PSSH too.

---

## 10. Widevine Key Acquisition

```python
def acquire_keys_wv(token, license_url, pssh_b64):
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device
    from pywidevine.pssh import PSSH

    # Find WVD device file
    wvd_path = None
    for p in ["/home/streamora/cdm/device.wvd", "/root/cdm ext/cdm/device.wvd"]:
        if os.path.isfile(p):
            wvd_path = p
            break
    # Also check wvd_device_path arg
    for a in sys.argv:
        if a.startswith("wvd_device_path="):
            p = a.split("=", 1)[1]
            if os.path.isfile(p):
                wvd_path = p
    if not wvd_path:
        return ""

    device = Device.load(wvd_path)
    cdm = Cdm.from_device(device)
    sess = cdm.open()
    challenge = cdm.get_license_challenge(sess, PSSH(pssh_b64))

    # Send challenge to license server WITH auth headers
    lic_headers = {
        "content-type": "application/octet-stream",
        "authorization": "Bearer " + token,
        # ... other service-specific headers ...
    }
    r = requests.post(license_url, headers=lic_headers, data=challenge)

    cdm.parse_license(sess, r.content)
    keys = []
    for key in cdm.get_keys(sess):
        if key.type == "CONTENT":
            keys.append(f"{key.kid.hex}:{key.key.hex()}")
    cdm.close(sess)
    return "\n".join(keys)
```

**IMPORTANT**: The license request needs auth headers (Bearer token etc.).
The built-in CDM uses `license_headers` from manifest, but the script CDM
can use the full auth context directly — more reliable.

---

## 11. Device ID Management

Auto-generate and cache device IDs instead of requiring them as args:

```python
def getDeviceId():
    # 1. From arg
    did = parse_param(sys.argv, "device")
    if did:
        saveDeviceId(did)
        return did
    # 2. From cache
    did = loadDeviceId()
    if did:
        return did
    # 3. Generate new
    did = str(uuid4())
    saveDeviceId(did)
    return did
```

---

## 12. Browser-Like Headers (WAF Bypass)

Some services (DAZN, etc.) block requests without full browser headers.
Always include these for services with aggressive bot detection:

```python
def browser_headers(device_id=None):
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://www.service.com",
        "referer": "https://www.service.com/",
        "sec-ch-ua": '"Chromium";v="134", "Google Chrome";v="134"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "user-agent": UA,
    }
```

---

## 13. Banner

Every script should have a banner for interactive mode:

```python
def banner():
    log("")
    log("[color:blue] ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄")
    log("[color:blue] █                                   █")
    log("[color:blue] █   S T R E A M O R A               █")
    log("[color:blue] █                                   █")
    log("[color:blue] ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀")
    log("")
    log("[color:green]  Service Name  |  Content Description")
    log("[color:yellow]  Provider      |  DASH + Widevine DRM")
    log("[color:yellow]  Region        |  Country/Region")
    log("")
    log(" ───────────────────────────────────")
    log("")
```

---

## 14. Proxy Handling

```python
proxy = parse_param(sys.argv, "proxy")
PROXIES = {"http": proxy, "https": proxy} if proxy else {}
```

Pass `proxies=PROXIES` to every `requests` call. Never hardcode proxies.

---

## 15. Testing Checklist

Before marking a script as done, verify ALL of these:

### Sync test
- [ ] `action=channels` returns valid JSON with all required fields
- [ ] `logo` is always a string (never object/dict)
- [ ] Channel IDs are stable (same ID for same channel across runs)

### Key refresh test
- [ ] `action=manifest` returns `keys` with actual `kid:key` pairs
- [ ] `action=manifest` returns `pssh` + `license_url` + `license_headers` (for built-in CDM fallback)
- [ ] `license_headers` includes auth token (Bearer)
- [ ] `action=cdm` returns `{"keys": "", "error": ""}` (empty, no error)

### Auth fallback test
- [ ] Works with cached session and NO credentials set
- [ ] Works with WRONG credentials but valid cached session
- [ ] Shows helpful error with JSON format example when no credentials and no cache

### Interactive test
- [ ] Option to list channels works
- [ ] Guard: "No channels loaded" when trying manifest before listing
- [ ] Hint: "Tip: Use option X" after listing
- [ ] Manifest option dumps PSSH + Widevine keys
- [ ] Session info shows: user, proxy, device, country, token expiry, cached count
- [ ] Credential prompt only shown when no cached session exists

### Error messages test
- [ ] Wrong password → shows expected Credentials JSON format
- [ ] No credentials → shows full Credentials + Config JSON examples
- [ ] Proxy blocked → explains residential proxy needed
- [ ] Subscription issue → clear message about subscription level

---

## 16. Complete Minimal Script Template

```python
#!/usr/bin/env python3
"""MyService provider for Streamora. Requires user (email), password."""
import json, sys, os, re, time, base64, glob
from uuid import uuid4
# import requests  # or: from curl_cffi import requests

def parse_param(args, key, default=""):
    for a in args:
        if a.startswith(key + "="):
            return a.split("=", 1)[1]
    return default

def log(msg):
    print(msg, file=sys.stderr, flush=True)

action     = parse_param(sys.argv, "action")
channel_id = parse_param(sys.argv, "id")
email      = parse_param(sys.argv, "user")
password   = parse_param(sys.argv, "password")
proxy      = parse_param(sys.argv, "proxy")

PATH = os.path.dirname(os.path.abspath(__file__)) + "/my_service/"
os.makedirs(PATH, exist_ok=True)
PROXIES = {"http": proxy, "https": proxy} if proxy else {}

# --- Auth with session fallback (Section 6) ---
# --- Content fetching (Section 4.1) ---
# --- Playback + key acquisition (Section 4.2, 9, 10) ---
# --- Banner (Section 13) ---

if action == "channels":
    banner()
    token = authenticate()
    channels = fetch_channels(token)
    print(json.dumps({"channels": channels}))

elif action == "manifest":
    token = authenticate()
    result = get_manifest(token, channel_id)  # includes keys
    print(json.dumps(result))

elif action == "cdm":
    print(json.dumps({"keys": "", "error": ""}))

elif action == "interactive":
    banner()
    # ... full interactive mode (Section 8) ...

else:
    print(json.dumps({"error": f"unknown action: {action}"}))
    sys.exit(1)
```

---

## Reference Scripts

| Script | Good example of |
|--------|----------------|
| `demo_provider.py` | Basic template, session management |
| `dazn_de.py` | Full-featured: browser WAF bypass, key dump, device ID, events + linear |
| `dazn.py` | Global multi-region, auto-detect from JWT |
| `nba.py` | Multi-source channels, HLS PSSH, Akamai tokens, nbaidentity cookie auth |
| `shoq.py` | Simple DRM provider, Vualto license proxy format |
| `tataplay.py` | OTP login flow, AES-encrypted session |
| `jiotv.py` | OTP login flow, credential file caching |
| `viaplay.py` | Cookie-based session, event discovery |
