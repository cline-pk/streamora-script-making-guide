#!/usr/bin/env python3
"""
================================================================================
  STREAMORA PROVIDER SCRIPT — DEVELOPER TEMPLATE
================================================================================

  This is a fully documented demo script showing how to build a provider for
  Streamora. It covers ALL actions and patterns you need.

  Streamora calls your script as a subprocess with command-line arguments.
  Your script does its work and returns JSON to stdout.

  IMPORTANT RULES:
    - stdout  = JSON result ONLY (for sync actions). Never print debug here.
    - stderr  = Log messages (shown in Streamora terminal UI in real time).
    - For interactive mode: stdout is streamed to terminal too (not parsed as JSON).

  ACTIONS (Streamora calls your script with action=XXX):
    channels    — Return list of all available channels
    manifest    — Return stream URL + DRM info for a specific channel
    cdm         — Acquire DRM keys (fallback, rarely needed)
    interactive — Interactive terminal with stdin/stdout (user types input)

  PROFILE DEFAULTS (auto-configure stream profiles on first sync):
    Scripts can return profile_defaults in action=channels output. When Streamora
    creates a new profile (from the "group" field), it applies these defaults
    automatically. This eliminates manual profile configuration.

    profile_defaults keys (all optional):
      running_mode          — "internal_remux" (recommended for DASH) or "ffmpeg_remux" (for HLS)
      output_format         — "hls" (default) or "mpegts"
      manifest_refresh_min  — Minutes between CDN token refreshes (0 = disabled). Set 5-8 for expiring tokens.
      skip_prestart_refresh — 1 to skip calling script before channel start (for non-expiring sources)
      proxy_from_provider   — true to copy provider's proxy config to the profile
      shared_headers        — 1 if all channels share same CDN auth (headers stored on profile, not per-channel)
      remux_segment_duration — HLS segment duration in seconds (default 6)
      remux_playlist_window  — Number of segments in HLS playlist (default 10)
      remux_playlist_duration — Max playlist duration in seconds (default 60)
      remux_speed_up         — 1 to burst initial segments faster (default)
      live_edge_segments     — Skip to last N segments for live sources (0 = play from start)

  ARGUMENTS (passed as key=value on command line):
    action=channels|manifest|cdm|interactive
    id=channel_id           (for manifest/cdm actions)
    user=email@example.com  (from provider Credentials JSON)
    password=secret123      (from provider Credentials JSON)
    proxy=http://proxy:8080 (from provider Config JSON, optional)
    any_key=any_value       (all Credentials + Config keys are passed)

  FILE STRUCTURE:
    /home/streamora/scripts/
      your_provider.py      <- This script
      your_provider/         <- Data directory (auto-created, for tokens/cache)
        session.json
        cookies.json

  HOW TO TEST:
    python3 demo_provider.py action=channels user=test password=test
    python3 demo_provider.py action=manifest id=clear_axinom_1080p user=test password=test
    python3 -u demo_provider.py action=interactive user=test password=test

================================================================================
"""

import json
import sys
import os
import time
import hashlib


# ==============================================================================
# 1. ARGUMENT PARSING — Parse key=value pairs from command line
# ==============================================================================

def parse_param(args, key, default=""):
    """Extract a parameter from command-line arguments.
    Example: parse_param(sys.argv, "user") extracts value from "user=john@email.com"
    """
    for a in args:
        if a.startswith(key + "="):
            return a.split("=", 1)[1]
    return default


# Parse standard arguments
action = parse_param(sys.argv, "action")       # channels|manifest|cdm|interactive
channel_id = parse_param(sys.argv, "id")        # channel ID (for manifest/cdm)
user = parse_param(sys.argv, "user")            # from Credentials JSON
password = parse_param(sys.argv, "password")    # from Credentials JSON
proxy = parse_param(sys.argv, "proxy")          # from Config JSON (optional)
api_key = parse_param(sys.argv, "api_key")      # custom field example


# ==============================================================================
# 2. LOGGING — Print to stderr (shown in Streamora terminal)
# ==============================================================================

def log(msg):
    """Print to stderr — appears in Streamora terminal in real time.

    Color tags (optional):
        [color:red]Error message      -> red text
        [color:green]Success message   -> green text
        [color:yellow]Warning message  -> yellow text
        [color:blue]Info message       -> blue text

    IMPORTANT: Always use flush=True to avoid buffered output.
    """
    print(msg, file=sys.stderr, flush=True)


# ==============================================================================
# 3. DATA DIRECTORY — Store tokens, cookies, cached data
# ==============================================================================

# Convention: use a subdirectory named after your script (without .py)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = os.path.splitext(os.path.basename(__file__))[0]
DATA_DIR = os.path.join(SCRIPT_DIR, SCRIPT_NAME)
os.makedirs(DATA_DIR, exist_ok=True)


# ==============================================================================
# 4. SESSION / TOKEN MANAGEMENT — Cache auth tokens to avoid re-login
# ==============================================================================

def load_session():
    """Load cached session from disk. Returns None if expired or missing."""
    path = os.path.join(DATA_DIR, "session.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        # Check expiry
        if data.get("expires_at", 0) < time.time():
            log("Cached session expired")
            return None
        return data
    except Exception:
        return None


def save_session(data):
    """Save session to disk for reuse across invocations."""
    path = os.path.join(DATA_DIR, "session.json")
    with open(path, "w") as f:
        json.dump(data, f)


# ==============================================================================
# 5. AUTHENTICATION — Login with session fallback
# ==============================================================================

def authenticate():
    """Authenticate and return a session/token.

    RECOMMENDED AUTH PATTERN — handles these scenarios gracefully:
      1. Cached session valid → use it (fastest, no network call)
      2. Cached session expired + valid credentials → fresh login
      3. Cached session expired + WRONG credentials → fall back to cached session
         (session cookies often outlive the expiry estimate)
      4. No credentials + cached session → use cached session
      5. No credentials + no session → error with guidance

    This ensures the provider keeps working even if the user changes their
    password, as long as the old session file is still valid.
    """
    session = load_session()
    has_cached = session is not None

    # 1. Try cached session first (works even without credentials)
    if session:
        log(f"Using cached session (expires in {int(session['expires_at'] - time.time())}s)")
        return session

    # 2. Have credentials? Try login
    login_error = None
    if user and password:
        log("Logging in...")
        try:
            # --- Replace this block with your real login API call ---
            token = hashlib.md5(f"{user}:{password}".encode()).hexdigest()
            expires_at = time.time() + 86400  # 24 hours
            session = {
                "access_token": token,
                "user": user,
                "expires_at": expires_at,
            }
            save_session(session)
            log("[color:green]Login successful!")
            return session
        except Exception as e:
            login_error = str(e)
            log(f"[color:yellow]Login failed: {e}")

    # 3. Login failed but cached session exists? Retry it
    #    (the session might still work even if load_session() said "expired" —
    #     many services accept tokens past their stated expiry)
    if login_error and has_cached:
        log("[color:yellow]Credentials failed, falling back to cached session...")
        path = os.path.join(DATA_DIR, "session.json")
        try:
            with open(path) as f:
                fallback = json.load(f)
            if fallback.get("access_token"):
                log("[color:green]Using cached session as fallback")
                return fallback
        except Exception:
            pass

    # 4. Nothing worked
    if login_error:
        log(f"[color:red]All auth methods failed (login error: {login_error})")
        print(json.dumps({"error": f"Login failed: {login_error}. No valid cached session."}))
    else:
        log("[color:red]No credentials and no cached session")
        print(json.dumps({
            "error": "No cached session. Provide user and password in Credentials JSON."
        }))
    sys.exit(1)


# ==============================================================================
# 6. CHANNEL LISTING — Real working demo streams
# ==============================================================================

# These are real, publicly available test streams that actually work
DEMO_CHANNELS = [
    # Clear DASH streams (no DRM)
    {
        "id": "clear_axinom_1080p",
        "name": "Clear | Axinom v7 1080p",
        "logo": "",
        "group": "Demo Clear",
        "source_url": "https://media.axprod.net/TestVectors/v7-Clear/Manifest_1080p.mpd",
        "source_type": "dash",
        "drm_type": "none",
    },
    {
        "id": "clear_axinom_cmaf",
        "name": "Clear | Axinom CMAF H.264",
        "logo": "",
        "group": "Demo Clear",
        "source_url": "https://media.axprod.net/TestVectors/Cmaf/clear_1080p_h264/manifest.mpd",
        "source_type": "dash",
        "drm_type": "none",
    },
    {
        "id": "clear_axinom_dash",
        "name": "Clear | Axinom DASH H.264",
        "logo": "",
        "group": "Demo Clear",
        "source_url": "https://media.axprod.net/TestVectors/Dash/not_protected_dash_1080p_h264/manifest.mpd",
        "source_type": "dash",
        "drm_type": "none",
    },
    {
        "id": "clear_unified_tears",
        "name": "Clear | Unified Tears of Steel",
        "logo": "",
        "group": "Demo Clear",
        "source_url": "https://demo.unified-streaming.com/k8s/features/stable/video/tears-of-steel/tears-of-steel.ism/.mpd",
        "source_type": "dash",
        "drm_type": "none",
    },

    # ClearKey DRM streams (keys provided)
    {
        "id": "clearkey_axinom_single",
        "name": "ClearKey | Axinom v7 SingleKey 1080p",
        "logo": "",
        "group": "Demo ClearKey",
        "source_url": "https://media.axprod.net/TestVectors/v7-MultiDRM-SingleKey/Manifest_1080p.mpd",
        "source_type": "dash",
        "drm_type": "clearkey",
        "keys": "9eb4050de44b4802932e27d75083e266:166634c675823c235a4a9446fad52e4d",
    },
    {
        "id": "clearkey_axinom_multi",
        "name": "ClearKey | Axinom v7 MultiKey 1080p",
        "logo": "",
        "group": "Demo ClearKey",
        "source_url": "https://media.axprod.net/TestVectors/v7-MultiDRM-MultiKey/Manifest_1080p.mpd",
        "source_type": "dash",
        "drm_type": "clearkey",
        "keys": "80399bf58a2140148053e27e748e98c0:dda1e9a73676837637c0ad6e3675179a\n90953e096cb249a3a2607a5fefead499:cec98a5bb32af549f3e51ee8506785f3\n0e4da92bd0e84a668c3fc25a97eb6532:5266187c66fbce7ba814040cefd6b21f\n585f233f307246f19fa46dc22c66a014:8dac8aa42ded98fab860a5e46a96bc14\n4222bd78bc4541bfb63e6f814dc391df:180322f6ff766fd71ae720706a9b4df9",
    },
    {
        "id": "clearkey_shaka_angel",
        "name": "ClearKey | Shaka Angel One",
        "logo": "",
        "group": "Demo ClearKey",
        "source_url": "https://storage.googleapis.com/shaka-demo-assets/angel-one-clearkey/dash.mpd",
        "source_type": "dash",
        "drm_type": "clearkey",
        "keys": "feedf00deedeadbeeff0baadf00dd00d:00112233445566778899aabbccddeeff",
    },

    # HLS streams (no DRM)
    {
        "id": "hls_bbb",
        "name": "Clear | HLS Big Buck Bunny",
        "logo": "",
        "group": "Demo HLS",
        "source_url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
        "source_type": "hls",
        "drm_type": "none",
    },
    {
        "id": "hls_sintel",
        "name": "Clear | HLS Sintel (Bitmovin)",
        "logo": "",
        "group": "Demo HLS",
        "source_url": "https://cdn.bitmovin.com/content/assets/sintel/sintel.mpd",
        "source_type": "dash",
        "drm_type": "none",
    },

    # Direct MP4 stream
    {
        "id": "direct_bbb",
        "name": "Clear | Direct MP4 Big Buck Bunny",
        "logo": "",
        "group": "Demo Direct",
        "source_url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
        "source_type": "direct",
        "drm_type": "none",
    },
]


def get_channels(token):
    """Return all demo channels."""
    log("Fetching demo channel list...")
    log(f"[color:green]Found {len(DEMO_CHANNELS)} channels")
    return DEMO_CHANNELS


# ==============================================================================
# 7. MANIFEST — Return stream URL + DRM info for a specific channel
# ==============================================================================

def get_manifest(token, ch_id):
    """Get stream URL and DRM details for a channel."""
    log(f"Getting stream info for channel {ch_id}...")

    for ch in DEMO_CHANNELS:
        if ch["id"] == ch_id:
            result = {
                "manifest_url": ch["source_url"],
                "source_type": ch["source_type"],
                "drm_type": ch["drm_type"],
                "license_url": "",
                "license_headers": {},
                "manifest_headers": {},
                "pssh": "",
                "keys": ch.get("keys", ""),
                "error": "",
            }
            return result

    return {"error": f"Channel {ch_id} not found"}


# ==============================================================================
# 8. CDM (KEY ACQUISITION) — Fallback for DRM key fetching
# ==============================================================================

def get_keys(token, ch_id):
    """Acquire DRM decryption keys for a channel."""
    log(f"Acquiring keys for channel {ch_id}...")

    for ch in DEMO_CHANNELS:
        if ch["id"] == ch_id:
            return ch.get("keys", "")

    return ""


# ==============================================================================
# 9. BANNER — Optional visual header for terminal output
# ==============================================================================

def banner():
    """Print a visual banner in the terminal. Optional but looks nice."""
    log("")
    log("[color:blue] =========================================")
    log("[color:blue]   DEMO PROVIDER for Streamora")
    log("[color:blue]   Developer Template Script")
    log("[color:blue] =========================================")
    log("")


# ==============================================================================
# 10. MAIN — Action dispatcher
# ==============================================================================

# ========================
# ACTION: channels
# ========================
if action == "channels":
    banner()
    session = authenticate()
    items = get_channels(session["access_token"])

    channels = []
    for item in items:
        channels.append({
            "id": item["id"],
            "name": item["name"],
            "logo": item.get("logo", ""),
            "group": item.get("group", ""),
            "source_url": item.get("source_url", ""),
            "source_type": item.get("source_type", "dash"),
            "drm_type": item.get("drm_type", "none"),
            "epg_id": "",
        })
        drm = item.get("drm_type", "none")
        has_keys = " [keys]" if item.get("keys") else ""
        log(f"  {item['name']} (drm={drm}{has_keys})")

    # PROFILE DEFAULTS — auto-configure profiles created from "group" names.
    # Applied only when a profile is first created (existing profiles not overwritten).
    # Use "Update profile settings" checkbox in sync modal to re-apply to existing profiles.
    demo_profile = {
        "running_mode": "internal_remux",   # Best for DASH sources
        "output_format": "hls",             # Most compatible output
        "shared_headers": 1,                # All channels share same CDN auth
        # "manifest_refresh_min": 5,        # Uncomment if CDN tokens expire
        # "proxy_from_provider": True,      # Uncomment if proxy needed for CDN
        # "remux_segment_duration": 6,      # HLS segment duration
        # "remux_playlist_window": 10,      # Segments in m3u8
        # "remux_playlist_duration": 60,    # Max playlist seconds
    }
    groups = set(ch["group"] for ch in channels if ch.get("group"))
    profile_defaults = {g: demo_profile for g in groups}

    log(f"Total: {len(channels)} channels")

    # OUTPUT: Print JSON to stdout — this is what Streamora reads
    print(json.dumps({"channels": channels, "profile_defaults": profile_defaults}))


# ========================
# ACTION: manifest
# ========================
elif action == "manifest":
    if not channel_id:
        print(json.dumps({"error": "channel id required"}))
        sys.exit(1)

    session = authenticate()
    manifest = get_manifest(session["access_token"], channel_id)

    if manifest.get("error"):
        print(json.dumps({"error": manifest["error"]}))
        sys.exit(0)

    log(f"[color:green]URL: {manifest['manifest_url'][:120]}")
    if manifest.get("keys"):
        log(f"[color:green]Keys: {len(manifest['keys'].splitlines())} key(s)")

    # OUTPUT: Print the manifest JSON
    print(json.dumps(manifest))


# ========================
# ACTION: cdm
# ========================
elif action == "cdm":
    if not channel_id:
        print(json.dumps({"keys": "", "error": "channel id required"}))
        sys.exit(1)

    session = authenticate()
    keys = get_keys(session["access_token"], channel_id)

    if keys:
        log(f"[color:green]Got {len(keys.splitlines())} key(s)")
    else:
        log("[color:yellow]No keys acquired (Streamora built-in CDM will be used)")

    # OUTPUT: Print keys JSON
    print(json.dumps({"keys": keys, "error": ""}))


# ========================
# ACTION: interactive
# ========================
elif action == "interactive":
    banner()
    log("[color:blue]Interactive Terminal Mode")
    log("")

    session = authenticate()
    log(f"[color:green]Authenticated as {session.get('user', 'demo')}")

    cached_channels = []

    log("")
    log("Options:")
    log("  1 - List all channels")
    log("  2 - Get manifest for a channel (by # from list or channel ID)")
    log("  3 - Show session info")
    log("  0 - Exit")
    log("")

    while True:
        print("Choose option (1-3, 0=exit):")
        sys.stdout.flush()
        choice = input().strip()

        if choice == "0":
            log("Goodbye!")
            break

        elif choice == "1":
            log("")
            cached_channels = get_channels(session["access_token"])
            log(f"[color:green]{len(cached_channels)} channels:")
            log("")
            for i, item in enumerate(cached_channels):
                drm = item.get("drm_type", "none")
                drm_tag = f" [color:yellow][DRM:{drm}]" if drm != "none" else ""
                log(f"  {i+1:3d}. {item['name']}{drm_tag}")
            log("")

        elif choice == "2":
            print("Enter channel # or ID:")
            sys.stdout.flush()
            ch_input = input().strip()
            if not ch_input:
                log("[color:red]No input")
                continue

            ch_id = ch_input
            ch_name = ch_id
            if ch_input.isdigit() and cached_channels:
                idx = int(ch_input)
                if 1 <= idx <= len(cached_channels):
                    ch_id = cached_channels[idx - 1]["id"]
                    ch_name = cached_channels[idx - 1]["name"]
                else:
                    log(f"[color:red]Invalid #")
                    continue

            manifest = get_manifest(session["access_token"], ch_id)
            if manifest.get("error"):
                log(f"[color:red]{manifest['error']}")
            else:
                log(f"[color:green]Stream: {manifest['manifest_url']}")
                log(f"  Type: {manifest['source_type']} | DRM: {manifest['drm_type']}")
                if manifest.get("keys"):
                    for kl in manifest["keys"].splitlines():
                        log(f"  Key: {kl}")
            log("")

        elif choice == "3":
            log("")
            log("[color:blue]Session Info:")
            log(f"  User: {session.get('user', 'N/A')}")
            remaining = int(session.get("expires_at", 0) - time.time())
            if remaining > 0:
                log(f"  Expires in: {remaining // 3600}h {(remaining % 3600) // 60}m")
            else:
                log(f"  [color:red]Expired!")
            log("")

        else:
            log(f"[color:yellow]Unknown option: {choice}")
            log("")

    sys.exit(0)


# ========================
# UNKNOWN ACTION
# ========================
else:
    print(json.dumps({"error": f"unknown action: {action}"}))
    sys.exit(1)
