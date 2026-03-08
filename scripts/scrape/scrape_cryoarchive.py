#!/usr/bin/env python3
"""Scrape cryoarchive.systems — Marathon ARG CCTV surveillance site.

Downloads all publicly accessible content and organizes it:
- assets/     — per-room directories: {room}/{state}.mp4, {room}/background.png, plus error/ and artifacts.png
- fonts/      — CodeLanguage pictographic font, PPFraktionMono
- splats/     — Biostock 3D Gaussian splat scene
- cursors/    — In-world cursor SVGs
- .raw/       — disposable scrape artifacts (HTML, JSON, CSS, UUID originals)

No dependencies beyond stdlib.
"""

import json
import os
import re
import shutil
import urllib.request
import urllib.parse


BASE_URL = "https://cryoarchive.systems"
CDN_URL = "https://assets.thecdn.io"
OUT_DIR = "./cryoarchive.systems/"
RAW_DIR = "./cryoarchive.systems/.raw/"

# All known routes (pages that return 200)
ROUTES = [
    "/cargo",
    "/indx",
    "/steerage",
    "/revival",
    "/biostock",
    "/preservation",
    "/cryohub",
    "/example",
]

# Camera IDs and their video UUIDs
CAMERAS = {
    "cargo": {
        "unstable": "158967d7-59ac-4871-b8e0-7fc683e982ca",
        "stable": "6a5235a5-0e07-469d-8e0f-c664c154c1ba",
        "glitch": "a2e4633e-47e8-48e5-9393-60668f7780a1",
    },
    "index": {
        "unstable": "b45b183e-b255-48cf-b888-257a62fb46bd",
        "stable": "02d34536-1155-41d2-9f05-9e7087a0fc7f",
        "glitch": "da9fb869-e197-4c88-a1f9-b9c85d27ec51",
    },
    "revival": {
        "unstable": "217f7544-4806-4441-8f7a-8e3db39a5266",
        "stable": "a633f52e-c79c-4320-b2d5-16ded608143b",
        "glitch": "f128c76c-dafb-4a93-ae3a-85882540eeab",
    },
    "biostock": {
        "unstable": "5a55954c-e776-4bc4-a6ca-edf18666071d",
        "stable": "a0f9fc28-dc29-46c3-94c2-641003fd99e4",
        "glitch": "10701f62-d7ab-4e4a-b4c3-7e50269a1727",
    },
    "steerage": {
        "unstable": "855ab978-dd9f-4bfd-a6e1-3f16c9357384",
        "stable": "d63c42ce-f626-4c49-84e5-e9724bfb3e6d",
        "glitch": "883646ff-fbe4-4e9b-8759-818bf80220eb",
    },
    "preservation": {
        "unstable": "7aebbab5-cb5a-46c6-babb-3ff7505fbcdd",
        "stable": "32a8fe9e-00a2-4dbf-a19e-38c51b3dd8df",
        "glitch": "cc7bc850-ee98-4883-b3a4-f0a877b5492a",
    },
    "cryoHub": {
        "unstable": "f86f4c9a-6818-4d56-8a3e-cf95038ef7d2",
        "stable": "3032d56e-75dd-4974-b634-20e3478d343c",
        "glitch": "04869cfd-65c7-403f-9f6f-5cfd9ded85fe",
    },
    "camera06": {
        "unstable": "a9b3e8f3-5867-4cb4-a1b2-531765cc71d8",
        "stable": "61306bbd-6339-4d8f-ab99-330210d7c006",
        "glitch": "ce430770-9cdc-4adf-a3c4-f09512d06abc",
    },
    "camera09": {
        "unstable": "30e998c5-c0b6-4b28-b58b-da9bfac15692",
        "stable": "8c466925-d96e-4d6f-8e81-3d32b970f83d",
        "glitch": "03741510-1c4e-4160-b56d-027f5ca2843a",
    },
}

# Per-page background images
BG_IMAGES = {
    "cargo": "ce621aa9-d63b-4432-9411-c45a880c3288",
    "index": "5742b3a1-ff6b-4bc6-93df-70b990bfc0d4",
    "revival": "1532f938-a803-497f-81ae-fe1f29e6c757",
    "biostock": "89f9849a-3684-4dc7-beba-709b83d40e84",
    "steerage": "098e5a48-2cc6-44bb-a601-d0b569032ea1",
    "preservation": "a9c41cc3-8185-43f6-b827-34af221eff20",
    "cryoHub": "e16a58b3-a50f-4556-b1f4-124d916eeac9",
}

BG_VIDEO = "f5d8b1e5-d89a-4fb8-b1cd-2681c4d81a23"

PHANTOM_UUIDS = [
    "a6ce4d77-f881-49fe-b06d-9bf82392f64f",  # biostock glitch alt
    "6487caee-379c-43b8-8822-96af959f6d0a",  # camera06 glitch alt
    "18589bde-2597-4159-8fa8-d6b2a2a31161",  # camera09 unstable alt
    "803426eb-2c62-4f4d-a259-11e44869688d",  # camera09 glitch alt
]


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
        urllib.request.urlretrieve(safe_url, filename=path)
    except urllib.error.HTTPError as e:
        print(f"  {e.code}: {safe_url}")
        return False
    return True


def fetch_text(url):
    """Fetch URL, return text."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def fetch_json(url):
    """Fetch URL, return parsed JSON."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_json(data, path):
    """Save data as formatted JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_rsc_payloads(html):
    """Extract RSC data from self.__next_f.push() calls."""
    payloads = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL):
        try:
            payloads.append(m.group(1).encode().decode("unicode_escape"))
        except (UnicodeDecodeError, ValueError):
            pass
    return payloads


def extract_alt_filenames(payloads):
    """Extract filename -> alt_filename mappings from RSC payloads."""
    names = {}
    for p in payloads:
        for m in re.finditer(r'"filename"\s*:\s*"([^"]+)"[^}]*"alt_filename"\s*:\s*"([^"]+)"', p):
            names[m.group(1)] = m.group(2)
        for m in re.finditer(r'"alt_filename"\s*:\s*"([^"]+)"[^}]*"filename"\s*:\s*"([^"]+)"', p):
            names[m.group(2)] = m.group(1)
    return names


def main():
    print("=" * 60)
    print("cryoarchive.systems scraper")
    print("=" * 60)

    # --- 1. Public API snapshots -> .raw/ ---
    print("\n[1/7] Public API state...")
    state = fetch_json(f"{BASE_URL}/api/public/state")
    save_json(state, f"{RAW_DIR}json/state.json")
    stabilization = fetch_json(f"{BASE_URL}/api/public/cctv-cameras/stabilization")
    save_json(stabilization, f"{RAW_DIR}json/stabilization.json")

    # --- 2. HTML pages -> .raw/ ---
    print("\n[2/7] HTML pages...")
    all_payloads = []
    root_html = fetch_text(BASE_URL)
    os.makedirs(f"{RAW_DIR}html/", exist_ok=True)
    with open(f"{RAW_DIR}html/root.html", "w") as f:
        f.write(root_html)
    all_payloads.extend(extract_rsc_payloads(root_html))

    for route in ROUTES:
        slug = route.strip("/")
        try:
            html = fetch_text(f"{BASE_URL}{route}")
            with open(f"{RAW_DIR}html/{slug}.html", "w") as f:
                f.write(html)
            all_payloads.extend(extract_rsc_payloads(html))
        except urllib.error.HTTPError as e:
            print(f"  {e.code}: {route}")

    # Build asset manifest from RSC payloads
    alt_names = extract_alt_filenames(all_payloads)
    manifest = {"alt_filenames": alt_names, "phantom_uuids": PHANTOM_UUIDS}
    save_json(manifest, f"{RAW_DIR}json/asset-manifest.json")

    # --- 3. Camera videos -> assets/{room}/{state}.mp4 ---
    print("\n[3/7] Camera videos...")
    for cam_id, videos in CAMERAS.items():
        name = cam_id.lower()
        for vid_type, uuid in videos.items():
            dst = f"{OUT_DIR}assets/{name}/{vid_type}.mp4"
            if not os.path.exists(dst):
                download(f"{CDN_URL}/{uuid}.mp4", dst)

    # --- 4. Background assets -> assets/{room}/background.png ---
    print("\n[4/7] Background assets...")
    download(f"{CDN_URL}/{BG_VIDEO}.mp4", f"{OUT_DIR}assets/landing/background.mp4")
    for page, uuid in BG_IMAGES.items():
        name = page.lower()
        download(f"{CDN_URL}/{uuid}.png", f"{OUT_DIR}assets/{name}/background.png")

    # --- 5. Static assets ---
    print("\n[5/7] Static assets...")
    download(f"{BASE_URL}/artifacts.png", f"{OUT_DIR}assets/artifacts.png")
    download(f"{BASE_URL}/error/error-fallback.png", f"{OUT_DIR}assets/error/error-fallback.png")
    download(f"{BASE_URL}/error/unknown.png", f"{OUT_DIR}assets/error/unknown.png")
    download(f"{BASE_URL}/icon.svg", f"{OUT_DIR}icon.svg")
    download(f"{BASE_URL}/cursors/decryptor-cable-cursor.svg", f"{OUT_DIR}cursors/decryptor-cable-cursor.svg")

    # Fonts
    os.makedirs(f"{OUT_DIR}fonts/", exist_ok=True)
    all_text = root_html + "\n".join(all_payloads)
    for m in re.finditer(r'/_next/static/media/([^"\']+\.(woff2|otf|ttf))', all_text):
        font_file = m.group(1)
        ext = m.group(2)
        clean = font_file.split("-s.p.")[0] if "-s.p." in font_file else font_file.split(".")[0]
        download(f"{BASE_URL}/_next/static/media/{font_file}", f"{OUT_DIR}fonts/{clean}.{ext}")

    # --- 6. Gaussian splat ---
    print("\n[6/7] Gaussian splat data...")
    download(f"{BASE_URL}/splats/012226_biostock_splats.spz", f"{OUT_DIR}splats/012226_biostock_splats.spz")

    # --- 7. CSS -> .raw/ ---
    print("\n[7/7] CSS...")
    for m in re.finditer(r'/_next/static/chunks/([0-9a-f]+\.css)', root_html):
        css_file = m.group(1)
        download(f"{BASE_URL}/_next/static/chunks/{css_file}", f"{RAW_DIR}css/{css_file}")

    # --- Phantom UUID check ---
    print("\nChecking phantom UUIDs...")
    for uuid in PHANTOM_UUIDS:
        try:
            req = urllib.request.Request(f"{CDN_URL}/{uuid}.mp4", method="HEAD")
            with urllib.request.urlopen(req, timeout=10):
                print(f"  LIVE: {uuid}")
                download(f"{CDN_URL}/{uuid}.mp4", f"{OUT_DIR}assets/phantom/{uuid}.mp4")
        except urllib.error.HTTPError as e:
            print(f"  {e.code}: {uuid}")

    # --- Summary ---
    print("\n" + "=" * 60)
    kc = state["state"]["uescKillCount"]
    mem = state["state"]["memoryUnlocked"]
    pages = state["state"]["pages"]
    unlocked = [k for k, v in pages.items() if v["unlocked"]]
    completed = [k for k, v in pages.items() if v["completed"]]
    print(f"Kill count: {kc:,}  |  Memory: {mem}")
    print(f"Unlocked: {', '.join(unlocked) or 'none'}  |  Completed: {', '.join(completed) or 'none'}")
    total = sum(len(files) for _, _, files in os.walk(OUT_DIR) if ".raw" not in _)
    raw_total = sum(len(files) for _, _, files in os.walk(RAW_DIR))
    print(f"World content: {total} files  |  Raw scrape data: {raw_total} files")
    print("=" * 60)


if __name__ == "__main__":
    main()
