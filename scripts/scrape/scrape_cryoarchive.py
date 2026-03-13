#!/usr/bin/env python3
"""Scrape cryoarchive.systems — Marathon ARG CCTV surveillance site.

Discovers all content dynamically from APIs and RSC payloads:
- Room list derived from /api/public/state
- Camera videos derived from /cargo RSC initialConfig
- Background images derived from per-page HTML preloads
- Static assets discovered from HTML/CSS pattern matching
- Phantom UUIDs derived from alt_filename analysis
- Authenticated room content (slot-based assets + text excerpts)

No hardcoded asset lists. Everything from first principles.

Usage:
    # Public-only scrape (no auth)
    python3 scripts/scrape/scrape_cryoarchive.py

    # Authenticated scrape (DAC + password unlocks gated rooms like /indx)
    python3 scripts/scrape/scrape_cryoarchive.py --dac path/to/dac.png --password 'THE PASSWORD'

Downloads and organizes:
- assets/     — per-room: {room}/{state}.mp4, background.png, plus error/ and artifacts.png
- fonts/      — discovered from HTML preloads + CSS @font-face
- splats/     — 3D Gaussian splat scenes
- cursors/    — in-world cursor SVGs
- .raw/       — disposable scrape artifacts (HTML, JSON, CSS, UUID originals)
"""

import argparse
import http.cookiejar
import json
import os
import re
import time
import uuid as uuid_mod
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://cryoarchive.systems"
CDN_URL = "https://assets.thecdn.io"
OUT_DIR = "./cryoarchive.systems/"
RAW_DIR = "./cryoarchive.systems/.raw/"

UUID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
)

# Known room auth endpoints: room_id -> (auth_path, page_path)
# Discovered from deploy module analysis. New rooms can be added here as they
# become known; the scraper will attempt auth for any room in this map when
# credentials are provided.
ROOM_AUTH = {
    "index": ("/api/indx/auth", "/indx"),
}

# Global opener — set up in main(), used by fetch helpers
opener = None


# =========================================================
# Network helpers
# =========================================================


def download(url, path):
    """Download a URL to a local path, skip if exists."""
    if not url:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return True
    parsed = urllib.parse.urlparse(url)
    encoded_path = urllib.parse.quote(parsed.path, safe="/:@!$&'()*+,;=-._~%")
    safe_url = parsed._replace(path=encoded_path).geturl()
    print(f"  GET {safe_url}")
    try:
        # Use opener for authenticated downloads from our domain
        if opener and "cryoarchive.systems" in safe_url:
            resp = opener.open(safe_url, timeout=30)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(resp.read())
        else:
            urllib.request.urlretrieve(safe_url, filename=path)
    except urllib.error.HTTPError as e:
        print(f"  {e.code}: {safe_url}")
        return False
    return True


def _fetch(url, parse_json=False, retries=3):
    """Fetch URL via authenticated opener with retry on transient errors."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=30) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data) if parse_json else data
        except (urllib.error.URLError, ConnectionResetError, OSError) as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  Retry {attempt + 1}/{retries} after {e} (wait {wait}s)")
                time.sleep(wait)
            else:
                raise


def fetch_text(url):
    """Fetch URL via authenticated opener, return text."""
    return _fetch(url)


def fetch_json(url):
    """Fetch URL via authenticated opener, return parsed JSON."""
    return _fetch(url, parse_json=True)


def save_json(data, path):
    """Save data as formatted JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# =========================================================
# Authentication
# =========================================================


def create_session():
    """Create a new session. Returns session ID."""
    req = urllib.request.Request(
        f"{BASE_URL}/api/session/create",
        method="POST",
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
        data=b"{}",
    )
    resp = opener.open(req, timeout=30)
    data = json.loads(resp.read().decode())
    return data.get("sessionId")


def upload_dac(dac_path):
    """Upload DAC file via multipart FormData. Returns (ok, user_data)."""
    with open(dac_path, "rb") as f:
        dac_data = f.read()

    boundary = uuid_mod.uuid4().hex
    filename = os.path.basename(dac_path)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="data"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n"
        f"\r\n"
    ).encode() + dac_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{BASE_URL}/api/auth/login",
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        data=body,
    )
    resp = opener.open(req, timeout=30)
    data = json.loads(resp.read().decode())
    return data.get("ok", False), data.get("data", {})


def auth_room(room_id, password):
    """Authenticate with a room-specific password. Returns True on success."""
    if room_id not in ROOM_AUTH:
        return False
    auth_path, _ = ROOM_AUTH[room_id]
    payload = json.dumps({"password": password}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{auth_path}",
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        },
        data=payload,
    )
    resp = opener.open(req, timeout=30)
    data = json.loads(resp.read().decode())
    return data.get("ok", False)


def authenticate(dac_path, password):
    """Full auth flow: session -> DAC upload -> room auth for all known rooms."""
    print("\n[auth] Creating session...")
    session_id = create_session()
    print(f"  Session: {session_id}")

    print("[auth] Uploading DAC...")
    ok, user_data = upload_dac(dac_path)
    if not ok:
        print("  ERROR: DAC upload failed")
        return False
    print(f"  User: {user_data.get('username')} ({user_data.get('userId')})")

    # Authenticate with each known room
    for room_id in ROOM_AUTH:
        print(f"[auth] Authenticating room: {room_id}...")
        try:
            ok = auth_room(room_id, password)
            print(f"  {'OK' if ok else 'FAILED'}")
        except urllib.error.HTTPError as e:
            print(f"  {e.code}: auth failed")

    return True


# =========================================================
# RSC / content extraction
# =========================================================


def extract_rsc_payloads(html):
    """Extract RSC data from self.__next_f.push() calls."""
    payloads = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL):
        try:
            payloads.append(m.group(1).encode().decode("unicode_escape"))
        except (UnicodeDecodeError, ValueError):
            pass
    return payloads


def extract_balanced_json(text, start_idx):
    """Extract a balanced JSON object starting at start_idx in text."""
    depth = 0
    for i in range(start_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        if depth == 0:
            return text[start_idx : i + 1]
    return None


def extract_initial_config(payloads):
    """Extract initialConfig.cameras from RSC payloads (found on /cargo page)."""
    for p in payloads:
        idx = p.find('"initialConfig":')
        if idx == -1:
            continue
        brace = p.index("{", idx + len('"initialConfig":'))
        raw = extract_balanced_json(p, brace)
        if raw:
            return json.loads(raw)
    return None


def extract_alt_filenames(payloads):
    """Extract filename -> alt_filename mappings from RSC payloads."""
    names = {}
    for p in payloads:
        for m in re.finditer(
            r'"filename"\s*:\s*"([^"]+)"[^}]*"alt_filename"\s*:\s*"([^"]+)"', p
        ):
            names[m.group(1)] = m.group(2)
        for m in re.finditer(
            r'"alt_filename"\s*:\s*"([^"]+)"[^}]*"filename"\s*:\s*"([^"]+)"', p
        ):
            names[m.group(2)] = m.group(1)
    return names


def extract_slot_assets(payloads):
    """Extract slot-based assets from RSC payloads (used by gated rooms like /indx).

    Returns list of dicts with slotId + content (type: media or text).
    """
    assets = []
    all_text = "\n".join(payloads)
    for m in re.finditer(r'\{"slotId":(\d+),"content":\{', all_text):
        start = m.start()
        depth = 0
        for i in range(start, len(all_text)):
            if all_text[i] == "{":
                depth += 1
            elif all_text[i] == "}":
                depth -= 1
            if depth == 0:
                raw = all_text[start : i + 1]
                try:
                    obj = json.loads(raw)
                    assets.append(obj)
                except (json.JSONDecodeError, ValueError):
                    pass
                break
    return assets


def derive_phantom_uuids(cameras):
    """Derive phantom UUIDs from camera alt_filename fields.

    A phantom UUID is when alt_filename is itself a UUID (not a human-readable name),
    indicating an alternate asset on the CDN.
    """
    phantoms = []
    video_keys = ["videoPreviewUnstable", "videoPreview", "videoFull"]
    for cam in cameras:
        for vk in video_keys:
            video = cam.get(vk, {})
            if not video:
                continue
            filename = video.get("filename", "")
            alt_filename = video.get("alt_filename", "")
            if not alt_filename or alt_filename == filename:
                continue
            alt_stem = (
                alt_filename.rsplit(".", 1)[0] if "." in alt_filename else alt_filename
            )
            if UUID_RE.match(alt_stem):
                phantoms.append(alt_stem)
    return phantoms


# =========================================================
# Room content scraping
# =========================================================


def scrape_room_slots(room_id, payloads):
    """Scrape slot-based content from a room's RSC payloads.

    Each slot has an entry name (ENTRY_NNNN, zero-padded from slotId) and typed
    content: media (PNG with thumbnail), text (excerpt), or youtubeVideo.

    Downloads assets and builds a manifest mapping entry names to content.
    """
    assets = extract_slot_assets(payloads)
    if not assets:
        return 0

    entries_dir = f"{OUT_DIR}assets/{room_id}/entries/"
    os.makedirs(entries_dir, exist_ok=True)
    counts = {"media": 0, "text": 0, "youtubeVideo": 0}
    entries = []  # manifest entries

    for asset in sorted(assets, key=lambda x: x["slotId"]):
        content = asset["content"]
        entry_name = f"ENTRY_{asset['slotId']:04d}"
        entry = {"entry": entry_name, "slotId": asset["slotId"], "type": content["type"]}

        if content["type"] == "media":
            file_info = content["file"]
            alt_filename = file_info.get("alt_filename", file_info["filename"])
            safe_name = alt_filename.replace(" ", "_")
            ext = file_info.get("mimeType", "image/png").split("/")[-1]
            if ext == "jpeg":
                ext = "jpg"

            # Full image
            download(file_info["url"], f"{entries_dir}{safe_name}.{ext}")
            # Thumbnail
            if content.get("thumbnail"):
                thumb = content["thumbnail"]
                download(thumb["url"], f"{entries_dir}{safe_name}_thumb.{ext}")

            entry["file"] = f"entries/{safe_name}.{ext}"
            entry["alt_filename"] = alt_filename
            entry["alt"] = file_info.get("alt", "")
            entry["url"] = file_info["url"]
            entry["thumbnail_url"] = content["thumbnail"]["url"] if content.get("thumbnail") else None
            entry["dimensions"] = f"{file_info.get('width', '?')}x{file_info.get('height', '?')}"
            counts["media"] += 1

        elif content["type"] == "text":
            text = content["text"]
            m = re.search(r"excerpt(\d+(?:-\d+)?)", text)
            name = m.group(0) if m else f"slot_{asset['slotId']}"
            with open(f"{entries_dir}{name}.txt", "w") as f:
                f.write(text)
            entry["excerpt"] = name
            entry["file"] = f"entries/{name}.txt"
            counts["text"] += 1

        elif content["type"] == "youtubeVideo":
            video_id = content.get("youtubeVideoId", "")
            entry["youtubeVideoId"] = video_id
            entry["url"] = f"https://www.youtube.com/watch?v={video_id}"
            counts["youtubeVideo"] += 1

        entries.append(entry)

    parts = [f"{v} {k}" for k, v in counts.items() if v > 0]
    print(f"  {room_id}: {', '.join(parts)}")

    # Save raw asset data
    save_json(assets, f"{RAW_DIR}json/{room_id}-assets.json")
    # Save entry manifest
    save_json(entries, f"{OUT_DIR}assets/{room_id}/entries.json")
    return len(assets)


def main():
    global opener

    parser = argparse.ArgumentParser(description="Scrape cryoarchive.systems")
    parser.add_argument("--dac", help="Path to DAC PNG file for authentication")
    parser.add_argument("--password", help="Password for room authentication")
    args = parser.parse_args()

    # Set up cookie-based session for all requests
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar)
    )

    print("=" * 60)
    print("cryoarchive.systems scraper (dynamic discovery)")
    print("=" * 60)

    # =========================================================
    # 0. Authenticate (if credentials provided)
    # =========================================================
    authenticated = False
    if args.dac and args.password:
        if not os.path.exists(args.dac):
            print(f"ERROR: DAC file not found: {args.dac}")
            return
        try:
            authenticated = authenticate(args.dac, args.password)
        except urllib.error.HTTPError as e:
            print(f"  Auth error: {e.code}")
            print("  Continuing with public-only scrape...")
    elif args.dac or args.password:
        print("WARNING: Both --dac and --password required for auth, skipping")

    # =========================================================
    # 1. Fetch APIs — derive room list
    # =========================================================
    print("\n[1/9] Public API state...")
    state = fetch_json(f"{BASE_URL}/api/public/state")
    save_json(state, f"{RAW_DIR}json/state.json")
    stabilization = fetch_json(f"{BASE_URL}/api/public/cctv-cameras/stabilization")
    save_json(stabilization, f"{RAW_DIR}json/stabilization.json")

    rooms = list(state["state"]["pages"].keys())
    camera_ids = list(stabilization["stabilization"].keys())
    print(f"  Rooms from API: {rooms}")
    print(f"  Camera IDs: {camera_ids}")

    # =========================================================
    # 2. Fetch /cargo page — extract initialConfig (cameras, routes)
    # =========================================================
    print("\n[2/9] Camera config from /cargo RSC...")
    all_html = {}
    all_payloads = []
    room_payloads = {}  # room_id -> [payloads] for per-room content extraction

    cargo_html = fetch_text(f"{BASE_URL}/cargo")
    os.makedirs(f"{RAW_DIR}html/", exist_ok=True)
    with open(f"{RAW_DIR}html/cargo.html", "w") as f:
        f.write(cargo_html)
    all_html["cargo"] = cargo_html

    cargo_payloads = extract_rsc_payloads(cargo_html)
    all_payloads.extend(cargo_payloads)
    room_payloads["cargo"] = cargo_payloads

    config = extract_initial_config(cargo_payloads)
    if not config or "cameras" not in config:
        print("  ERROR: Could not extract initialConfig from /cargo")
        return

    cameras = config["cameras"]
    print(f"  Found {len(cameras)} cameras")

    # Build route map from cameras
    routes = {}  # room_id -> route_path
    for cam in cameras:
        if cam.get("page"):
            routes[cam["id"]] = cam["page"]
    print(f"  Routes: {routes}")

    # =========================================================
    # 3. Fetch all room pages — collect RSC payloads
    # =========================================================
    print("\n[3/9] Fetching room pages...")

    # Root page
    root_html = fetch_text(BASE_URL)
    with open(f"{RAW_DIR}html/root.html", "w") as f:
        f.write(root_html)
    all_html["root"] = root_html
    root_payloads = extract_rsc_payloads(root_html)
    all_payloads.extend(root_payloads)

    # Room pages from camera routes (skip /cargo, already fetched)
    for room_id, route in routes.items():
        if room_id == "cargo":
            continue
        slug = route.strip("/")
        try:
            time.sleep(0.5)
            html = fetch_text(f"{BASE_URL}{route}")
            with open(f"{RAW_DIR}html/{slug}.html", "w") as f:
                f.write(html)
            all_html[room_id] = html
            payloads = extract_rsc_payloads(html)
            all_payloads.extend(payloads)
            room_payloads[room_id] = payloads
            print(f"  OK: {route}")
        except urllib.error.HTTPError as e:
            print(f"  {e.code}: {route}")

    # Authenticated room pages (not already fetched via camera routes)
    if authenticated:
        for room_id, (_, page_path) in ROOM_AUTH.items():
            if room_id in all_html:
                continue
            try:
                time.sleep(0.5)
                html = fetch_text(f"{BASE_URL}{page_path}")
                slug = page_path.strip("/")
                with open(f"{RAW_DIR}html/{slug}.html", "w") as f:
                    f.write(html)
                all_html[room_id] = html
                payloads = extract_rsc_payloads(html)
                all_payloads.extend(payloads)
                room_payloads[room_id] = payloads
                print(f"  OK: {page_path} (authenticated)")
            except urllib.error.HTTPError as e:
                print(f"  {e.code}: {page_path}")

    # =========================================================
    # 4. Download camera videos (derived from initialConfig)
    # =========================================================
    print("\n[4/9] Camera videos...")
    video_map = {
        "videoPreviewUnstable": "unstable",
        "videoPreview": "stable",
        "videoFull": "glitch",
    }
    for cam in cameras:
        cam_name = cam["id"].lower()
        for video_key, file_label in video_map.items():
            video = cam.get(video_key)
            if not video or not video.get("url"):
                continue
            dst = f"{OUT_DIR}assets/{cam_name}/{file_label}.mp4"
            if not os.path.exists(dst):
                download(video["url"], dst)

    # =========================================================
    # 5. Download background assets (derived from HTML preloads + RSC)
    # =========================================================
    print("\n[5/9] Background assets...")

    # Landing page background video — from root RSC payload
    for p in root_payloads:
        m = re.search(
            r'"background":\{"url":"(https://assets\.thecdn\.io/[^"]+\.mp4)"', p
        )
        if m:
            download(m.group(1), f"{OUT_DIR}assets/landing/background.mp4")
            break

    # Per-room background PNGs — from HTML <link rel="preload" as="image">
    for room_id, html in all_html.items():
        if room_id == "root":
            continue
        m = re.search(
            r'<link[^>]*rel="preload"[^>]*href="(https://assets\.thecdn\.io/[^"]+\.png)"[^>]*as="image"',
            html,
        )
        if m:
            room_name = room_id.lower()
            download(m.group(1), f"{OUT_DIR}assets/{room_name}/background.png")

    # Background images also appear in RSC as src props on background divs
    for room_id, payloads in room_payloads.items():
        for p in payloads:
            m = re.search(
                r'"src":"(https://assets\.thecdn\.io/[^"]+\.png)","alt":"[^"]*Background"',
                p,
            )
            if m:
                room_name = room_id.lower()
                download(m.group(1), f"{OUT_DIR}assets/{room_name}/background.png")

    # =========================================================
    # 6. Static assets (discovered from HTML/CSS)
    # =========================================================
    print("\n[6/9] Static assets...")

    download(f"{BASE_URL}/artifacts.png", f"{OUT_DIR}assets/artifacts.png")

    # Icon from <link rel="icon">
    for html in all_html.values():
        m = re.search(r'<link[^>]*rel="icon"[^>]*href="(/icon\.svg[^"]*)"', html)
        if m:
            download(f"{BASE_URL}/icon.svg", f"{OUT_DIR}icon.svg")
            break

    # Error images from RSC payloads
    all_text = "\n".join(all_payloads) + "\n".join(all_html.values())
    error_images = set()
    for m in re.finditer(r'"/error/([^"]+\.png)"', all_text):
        error_images.add(m.group(1))
    for img in sorted(error_images):
        download(f"{BASE_URL}/error/{img}", f"{OUT_DIR}assets/error/{img}")

    # Cursors
    cursor_paths = set()
    for m in re.finditer(r'"/cursors/([^"]+)"', all_text):
        cursor_paths.add(m.group(1))
    for cursor in sorted(cursor_paths):
        download(f"{BASE_URL}/cursors/{cursor}", f"{OUT_DIR}cursors/{cursor}")

    # Splats
    splat_paths = set()
    for m in re.finditer(r'"/splats/([^"]+)"', all_text):
        splat_paths.add(m.group(1))
    for m in re.finditer(r'"(/splats/[^"]+\.spz)"', all_text):
        splat_paths.add(m.group(1).lstrip("/splats/"))
    for splat in sorted(splat_paths):
        download(f"{BASE_URL}/splats/{splat}", f"{OUT_DIR}splats/{splat}")

    # Fonts
    print("\n  Fonts...")
    os.makedirs(f"{OUT_DIR}fonts/", exist_ok=True)
    font_files = set()
    for m in re.finditer(r'/_next/static/media/([^"\']+\.(woff2|otf|ttf))', all_text):
        font_files.add((m.group(1), m.group(2)))

    for html in all_html.values():
        for m in re.finditer(r'/_next/static/chunks/([0-9a-f]+\.css)', html):
            css_file = m.group(1)
            css_path = f"{RAW_DIR}css/{css_file}"
            if not os.path.exists(css_path):
                download(f"{BASE_URL}/_next/static/chunks/{css_file}", css_path)
            if os.path.exists(css_path):
                with open(css_path) as f:
                    css_text = f.read()
                for fm in re.finditer(
                    r"url\([./]*media/([^)]+\.(woff2|otf|ttf))\)", css_text
                ):
                    font_files.add((fm.group(1), fm.group(2)))

    for font_file, ext in sorted(font_files):
        clean = (
            font_file.split("-s.p.")[0]
            if "-s.p." in font_file
            else font_file.split(".")[0]
        )
        download(
            f"{BASE_URL}/_next/static/media/{font_file}",
            f"{OUT_DIR}fonts/{clean}.{ext}",
        )

    # =========================================================
    # 7. CSS chunks
    # =========================================================
    print("\n[7/9] CSS...")
    for html in all_html.values():
        for m in re.finditer(r'/_next/static/chunks/([0-9a-f]+\.css)', html):
            css_file = m.group(1)
            download(
                f"{BASE_URL}/_next/static/chunks/{css_file}",
                f"{RAW_DIR}css/{css_file}",
            )

    # =========================================================
    # 8. Room slot content (authenticated rooms)
    # =========================================================
    print("\n[8/9] Room content...")
    for room_id, payloads in room_payloads.items():
        slot_count = scrape_room_slots(room_id, payloads)
        if slot_count == 0 and room_id not in ("cargo", "root"):
            # No slot content — that's normal for camera-only rooms
            pass

    # =========================================================
    # 9. Phantom UUID check (derived from alt_filename analysis)
    # =========================================================
    print("\n[9/9] Phantom UUIDs...")
    phantoms = derive_phantom_uuids(cameras)
    if phantoms:
        print(f"  Derived {len(phantoms)} phantom UUIDs from camera alt_filenames")
    else:
        print("  No phantom UUIDs found")

    for uuid in phantoms:
        try:
            req = urllib.request.Request(f"{CDN_URL}/{uuid}.mp4", method="HEAD")
            with urllib.request.urlopen(req, timeout=10):
                print(f"  LIVE: {uuid}")
                download(
                    f"{CDN_URL}/{uuid}.mp4", f"{OUT_DIR}assets/phantom/{uuid}.mp4"
                )
        except urllib.error.HTTPError as e:
            print(f"  {e.code}: {uuid}")

    # =========================================================
    # Build manifest from discovered data
    # =========================================================
    alt_names = extract_alt_filenames(all_payloads)
    manifest = {
        "alt_filenames": alt_names,
        "phantom_uuids": phantoms,
        "cameras": {
            cam["id"]: {
                "displayName": cam["displayName"],
                "page": cam.get("page"),
                "uiSlot": cam.get("uiSlot"),
            }
            for cam in cameras
        },
        "routes": routes,
        "authenticated": authenticated,
    }
    save_json(manifest, f"{RAW_DIR}json/asset-manifest.json")

    # =========================================================
    # Summary
    # =========================================================
    print("\n" + "=" * 60)
    kc = state["state"]["uescKillCount"]
    mem = state["state"]["memoryUnlocked"]
    pages = state["state"]["pages"]
    unlocked = [k for k, v in pages.items() if v["unlocked"]]
    completed = [k for k, v in pages.items() if v["completed"]]
    print(f"Kill count: {kc:,}  |  Memory: {mem}")
    print(
        f"Unlocked: {', '.join(unlocked) or 'none'}  |  Completed: {', '.join(completed) or 'none'}"
    )
    if authenticated:
        print("Auth: YES (DAC + room passwords)")
    else:
        print("Auth: public only")
    total = sum(len(files) for _, _, files in os.walk(OUT_DIR) if ".raw" not in _)
    raw_total = sum(len(files) for _, _, files in os.walk(RAW_DIR))
    print(f"World content: {total} files  |  Raw scrape data: {raw_total} files")
    print("=" * 60)


if __name__ == "__main__":
    main()
