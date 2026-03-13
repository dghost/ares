#!/usr/bin/env python3
"""Solve cryoarchive.systems room puzzles — biostock and steerage.

Authenticates, runs the game APIs with known solutions, downloads win videos
and tile images. Requires ffmpeg for HLS→MP4 transcoding.

Usage:
    # Solve both puzzles
    python3 scripts/scrape/solve_games.py --dac path/to/dac.png --password 'THE PASSWORD'

    # Solve just one
    python3 scripts/scrape/solve_games.py --dac path/to/dac.png --password 'THE PASSWORD' --game biostock
    python3 scripts/scrape/solve_games.py --dac path/to/dac.png --password 'THE PASSWORD' --game steerage
"""

import argparse
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import uuid as uuid_mod
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://cryoarchive.systems"
OUT_DIR = "./cryoarchive.systems/assets/"
RAW_HLS_DIR = "./cryoarchive.systems/.raw/hls/"

# Biostock move sequence: U=forward, D=back, L=left, R=right
BIOSTOCK_MOVES = "ULRURURLUDRLRLLURU"
DIRECTION_MAP = {"U": "forward", "D": "back", "L": "left", "R": "right"}

# Steerage fog-of-war areas: (x, y, w, h) covering the 60x40 grid.
# i=1 for first area, i=0 for subsequent.
STEERAGE_AREAS = [
    (1, 1, 20, 15),
    (21, 1, 13, 8),
    (34, 1, 16, 10),
    (50, 1, 10, 6),
    (1, 14, 21, 16),
    (21, 9, 13, 11),
    (34, 11, 16, 9),
    (49, 6, 11, 15),
    (20, 19, 12, 10),
    (31, 19, 11, 7),
    (41, 20, 19, 10),
    (1, 29, 20, 11),
    (21, 28, 10, 12),
    (31, 25, 10, 15),
    (41, 30, 19, 10),
]

# Delay between moves/areas (seconds)
BIOSTOCK_DELAY = 12  # 5s exit + 6s enter + 1s buffer (client animation timing)
STEERAGE_DELAY = 3

# Global opener — set up in main()
opener = None


# =========================================================
# Network helpers
# =========================================================


def api_post(path, data, params=None):
    """POST JSON to an API endpoint. Returns parsed JSON response.

    Handles 429 rate limiting with automatic retry.
    """
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        },
        data=payload,
    )

    while True:
        try:
            resp = opener.open(req, timeout=30)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = 10
                try:
                    body = json.loads(e.read().decode())
                    retry_after = body.get("retryAfter", retry_after)
                except (json.JSONDecodeError, ValueError):
                    pass
                wait = retry_after + 5
                print(f"  429 rate limited — waiting {wait}s (retryAfter={retry_after})")
                time.sleep(wait)
                # Rebuild request (consumed by previous attempt)
                req = urllib.request.Request(
                    url,
                    method="POST",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Content-Type": "application/json",
                    },
                    data=payload,
                )
                continue
            else:
                print(f"  HTTP {e.code}: {url}")
                try:
                    print(f"  Response: {e.read().decode()[:500]}")
                except Exception:
                    pass
                raise


def fetch_json(url):
    """GET JSON from a URL via the authenticated opener."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def download(url, path, skip_existing=True):
    """Download a URL to a local path."""
    if not url:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if skip_existing and os.path.exists(path):
        return True
    parsed = urllib.parse.urlparse(url)
    encoded_path = urllib.parse.quote(parsed.path, safe="/:@!$&'()*+,;=-._~%")
    safe_url = parsed._replace(path=encoded_path).geturl()
    print(f"  GET {safe_url}")
    try:
        urllib.request.urlretrieve(safe_url, filename=path)
    except urllib.error.HTTPError as e:
        print(f"  {e.code}: {safe_url}")
        return False
    return True


def save_json(data, path):
    """Save data as formatted JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# =========================================================
# Authentication (mirrors scrape_cryoarchive.py)
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


def auth_index(password):
    """Authenticate with the index room password."""
    payload = json.dumps({"password": password}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/api/indx/auth",
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
    """Full auth flow: session -> DAC upload -> index room auth."""
    print("\n[auth] Creating session...")
    session_id = create_session()
    print(f"  Session: {session_id}")

    print("[auth] Uploading DAC...")
    ok, user_data = upload_dac(dac_path)
    if not ok:
        print("  ERROR: DAC upload failed")
        return False
    print(f"  User: {user_data.get('username')} ({user_data.get('userId')})")

    print("[auth] Authenticating index room...")
    try:
        ok = auth_index(password)
        print(f"  {'OK' if ok else 'FAILED'}")
    except urllib.error.HTTPError as e:
        print(f"  {e.code}: index auth failed")

    return True


# =========================================================
# HLS video download
# =========================================================


def download_hls_video(video_url, output_mp4, label):
    """Download an HLS stream and transcode to MP4.

    video_url is the base URL (e.g. https://b-stream.thecdn.io/{uuid}/).
    Fetches playlist.m3u8 -> 1080p/video.m3u8 -> .ts segments -> ffmpeg concat.

    HLS intermediates go in .raw/hls/{label}/, final MP4 goes to output_mp4.
    """
    hls_dir = os.path.join(RAW_HLS_DIR, label)
    os.makedirs(hls_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_mp4), exist_ok=True)

    base = video_url.rstrip("/")

    # 1. Master playlist
    master_url = f"{base}/playlist.m3u8"
    master_path = os.path.join(hls_dir, "playlist.m3u8")
    print(f"\n  Fetching master playlist: {master_url}")
    if not download(master_url, master_path, skip_existing=False):
        print("  ERROR: Could not fetch master playlist")
        return False

    # 2. 1080p variant playlist
    variant_url = f"{base}/1080p/video.m3u8"
    variant_path = os.path.join(hls_dir, "video_1080p.m3u8")
    print(f"  Fetching 1080p playlist: {variant_url}")
    if not download(variant_url, variant_path, skip_existing=False):
        print("  ERROR: Could not fetch 1080p playlist")
        return False

    # 3. Download .ts segments referenced in the variant playlist
    with open(variant_path) as f:
        variant_text = f.read()

    segments = [line.strip() for line in variant_text.splitlines()
                if line.strip() and not line.strip().startswith("#")]

    print(f"  Downloading {len(segments)} segments...")
    for seg in segments:
        seg_url = f"{base}/1080p/{seg}"
        seg_path = os.path.join(hls_dir, seg)
        download(seg_url, seg_path)

    # 4. Rewrite the variant playlist with local paths for ffmpeg
    local_m3u8 = os.path.join(hls_dir, "video_1080p_local.m3u8")
    with open(variant_path) as f:
        lines = f.readlines()
    with open(local_m3u8, "w") as f:
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                # Segment filename — write as relative path
                f.write(stripped + "\n")
            else:
                f.write(line)

    # 5. Transcode with ffmpeg
    print(f"  Transcoding to {output_mp4}...")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", local_m3u8,
                "-c", "copy",
                "-bsf:a", "aac_adtstoasc",
                output_mp4,
            ],
            capture_output=True,
            text=True,
            cwd=hls_dir,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"  ffmpeg error: {result.stderr[-500:]}")
            return False
    except FileNotFoundError:
        print("  ERROR: ffmpeg not found — install it to transcode HLS to MP4")
        return False

    if os.path.exists(output_mp4):
        size_mb = os.path.getsize(output_mp4) / (1024 * 1024)
        print(f"  OK: {output_mp4} ({size_mb:.1f} MB)")
        return True
    else:
        print(f"  ERROR: output file not created")
        return False


# =========================================================
# Biostock puzzle solver
# =========================================================


def solve_biostock():
    """Solve the biostock point-cloud direction sequence puzzle.

    Move sequence: ULRURURLUDRLRLLURU
    Each move has a 12s delay to match client animation timing.
    """
    print("\n" + "=" * 60)
    print("[biostock] Starting point-cloud puzzle")
    print(f"  Sequence: {BIOSTOCK_MOVES} ({len(BIOSTOCK_MOVES)} moves)")
    print("=" * 60)

    # Start the game
    print("\n  Starting game...")
    resp = api_post("/api/point-cloud/start", {"state": None})
    state = resp.get("state")
    if not state:
        print("  ERROR: No state returned from start")
        return None
    print(f"  Game started (state length: {len(state)})")

    # Execute move sequence
    for i, move_char in enumerate(BIOSTOCK_MOVES):
        direction = DIRECTION_MAP[move_char]

        print(f"\n  Move {i + 1}/{len(BIOSTOCK_MOVES)}: {move_char} ({direction})")

        if i > 0:
            print(f"  Waiting {BIOSTOCK_DELAY}s (animation timing)...")
            time.sleep(BIOSTOCK_DELAY)

        resp = api_post("/api/point-cloud/move", {"direction": direction, "state": state})
        state = resp.get("state")
        status = resp.get("status")

        if status == "complete":
            print(f"\n  COMPLETE — puzzle solved after {i + 1} moves")
            win_video = resp.get("winVideo", {})
            return win_video

        # Show previous directions if available
        prev = resp.get("previousDirections", [])
        if prev:
            trail = "".join(prev)
            print(f"  Trail: {trail}")

    print("\n  WARNING: Sequence exhausted without completion")
    return None


# =========================================================
# Steerage puzzle solver
# =========================================================


def solve_steerage():
    """Solve the steerage fog-of-war puzzle.

    Reveals 15 rectangular areas covering the 60x40 grid.
    Each reveal returns a tile image URL. Final reveal returns winVideo.
    """
    print("\n" + "=" * 60)
    print("[steerage] Starting fog-of-war puzzle")
    print(f"  Grid: 60x40, {len(STEERAGE_AREAS)} areas to reveal")
    print("=" * 60)

    tile_dir = f"{OUT_DIR}steerage/tiles/"
    os.makedirs(tile_dir, exist_ok=True)

    # We need a game state JWT. Start by getting one from the first steerage call.
    # The first call uses i=1 (init), subsequent use i=0.
    state = None
    tiles = []
    win_video = None

    for idx, (x, y, w, h) in enumerate(STEERAGE_AREAS):
        is_first = idx == 0
        i_val = 1 if is_first else 0

        print(f"\n  Area {idx + 1}/{len(STEERAGE_AREAS)}: ({x},{y}) {w}x{h}")

        if idx > 0:
            print(f"  Waiting {STEERAGE_DELAY}s...")
            time.sleep(STEERAGE_DELAY)

        params = {"x": x, "y": y, "w": w, "h": h, "i": i_val}
        body = {"state": state}

        resp = api_post("/api/game/steerage", body, params=params)

        state = resp.get("state", state)
        success = resp.get("success", False)
        tile_url = resp.get("url")
        completed = resp.get("completed", False)

        if success and tile_url:
            tile_name = f"tile_{idx + 1:02d}_{x}_{y}_{w}x{h}.webp"
            tile_path = os.path.join(tile_dir, tile_name)
            download(tile_url, tile_path, skip_existing=False)
            tiles.append({
                "index": idx + 1,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "url": tile_url,
                "file": tile_name,
            })
            print(f"  OK: {tile_name}")
        elif success:
            print(f"  OK (no tile URL)")
        else:
            print(f"  FAILED: {resp}")

        if completed:
            print(f"\n  COMPLETE — all {idx + 1} areas revealed")
            win_video = resp.get("winVideo", {})
            break

    # Save tile manifest
    manifest = {
        "grid": {"width": 60, "height": 40},
        "areas": len(tiles),
        "tiles": tiles,
    }
    manifest_path = os.path.join(tile_dir, "manifest.json")
    save_json(manifest, manifest_path)
    print(f"\n  Tile manifest: {manifest_path} ({len(tiles)} tiles)")

    return win_video


# =========================================================
# Win video handler
# =========================================================


def download_win_video(win_video, game_name):
    """Download a win video (HLS) and its thumbnail.

    win_video: {video_url: "https://...", poster_url: "https://..."}
    """
    if not win_video:
        print(f"\n  No win video data for {game_name}")
        return

    video_url = win_video.get("video_url", "")
    poster_url = win_video.get("poster_url", "")

    win_dir = f"{OUT_DIR}{game_name}/win/"
    mp4_path = os.path.join(win_dir, f"{game_name}_win.mp4")
    thumb_path = os.path.join(win_dir, "thumbnail.jpg")

    print(f"\n[{game_name}] Downloading win video")
    print(f"  HLS base: {video_url}")

    if video_url:
        if os.path.exists(mp4_path):
            print(f"  Win video already exists: {mp4_path}")
        else:
            download_hls_video(video_url, mp4_path, f"{game_name}_win")

    if poster_url:
        print(f"  Downloading thumbnail: {poster_url}")
        download(poster_url, thumb_path, skip_existing=False)


# =========================================================
# Main
# =========================================================


def main():
    global opener

    parser = argparse.ArgumentParser(
        description="Solve cryoarchive.systems room puzzles (biostock, steerage)"
    )
    parser.add_argument("--dac", required=True, help="Path to DAC PNG file")
    parser.add_argument("--password", required=True, help="Index room password")
    parser.add_argument(
        "--game",
        choices=["biostock", "steerage"],
        help="Run only one puzzle (default: both)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.dac):
        print(f"ERROR: DAC file not found: {args.dac}")
        sys.exit(1)

    # Set up cookie-based session
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar)
    )

    print("=" * 60)
    print("cryoarchive.systems puzzle solver")
    print("=" * 60)

    # Authenticate
    try:
        ok = authenticate(args.dac, args.password)
        if not ok:
            print("ERROR: Authentication failed")
            sys.exit(1)
    except urllib.error.HTTPError as e:
        print(f"ERROR: Auth failed with HTTP {e.code}")
        sys.exit(1)

    run_biostock = args.game in (None, "biostock")
    run_steerage = args.game in (None, "steerage")

    # Solve biostock
    if run_biostock:
        try:
            win_video = solve_biostock()
            download_win_video(win_video, "biostock")
        except Exception as e:
            print(f"\n  ERROR solving biostock: {e}")
            if run_steerage:
                print("  Continuing to steerage...")

    # Solve steerage
    if run_steerage:
        try:
            win_video = solve_steerage()
            download_win_video(win_video, "steerage")
        except Exception as e:
            print(f"\n  ERROR solving steerage: {e}")

    # Report public state
    print("\n" + "=" * 60)
    print("[state] Fetching public state...")
    try:
        state = fetch_json(f"{BASE_URL}/api/public/state")
        s = state["state"]
        pages = s["pages"]

        print(f"  Kill count: {s['uescKillCount']:,}")
        print(f"  Memory unlocked: {s.get('memoryUnlocked', False)}")
        print(f"  Memory completed: {s.get('memoryCompleted', False)}")
        print(f"  Ship date: {s.get('shipDate', '?')}")
        print()
        print(f"  {'Room':<16} {'Unlocked':<10} {'Completed':<10}")
        print(f"  {'─' * 36}")
        for room, info in pages.items():
            unlocked = "yes" if info.get("unlocked") else "no"
            completed = "yes" if info.get("completed") else "no"
            print(f"  {room:<16} {unlocked:<10} {completed:<10}")
    except Exception as e:
        print(f"  Could not fetch state: {e}")

    print("\n" + "=" * 60)
    print("Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
