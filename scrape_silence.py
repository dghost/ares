#!/usr/bin/env python3

import json
import os
import re
import urllib.request
import urllib.parse


BASE_URL = "https://hearoursilence.com"
OUT_DIR = "./hearoursilence.com/"
JSON_DIR = "./json/"


def download(url, path):
    """Download a URL to a local path, creating parent dirs as needed."""
    if not url:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        parsed = urllib.parse.urlparse(url)
        encoded_path = urllib.parse.quote(parsed.path, safe="/:@!$&'()*+,;=-._~%")
        safe_url = parsed._replace(path=encoded_path).geturl()
        print(f"  Fetching {safe_url}...")
        try:
            urllib.request.urlretrieve(safe_url, filename=path)
        except urllib.error.HTTPError as e:
            print(f"  ERROR {e.code}: {safe_url}")
            return False
    else:
        print(f"  Skipping {path} (exists)")
    return True


def fetch_html(url):
    """Fetch a URL and return the HTML content as a string."""
    print(f"  Fetching {url}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def extract_rsc_payloads(html):
    """Extract all RSC data payloads from Next.js HTML.

    Returns a list of unescaped payload strings from self.__next_f.push() calls.
    """
    payloads = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL):
        raw = m.group(1)
        try:
            unescaped = raw.encode().decode("unicode_escape")
            payloads.append(unescaped)
        except (UnicodeDecodeError, ValueError):
            pass
    return payloads


def extract_json_object(text, start_idx):
    """Extract a complete JSON object from text starting at start_idx (a '{').

    Uses brace-depth tracking with string awareness.
    """
    if start_idx >= len(text) or text[start_idx] != "{":
        return None
    depth = 0
    in_str = False
    i = start_idx
    while i < len(text):
        c = text[i]
        if c == "\\" and in_str:
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start_idx : i + 1]
        i += 1
    return None


def extract_json_array(text, start_idx):
    """Extract a complete JSON array from text starting at start_idx (a '[')."""
    if start_idx >= len(text) or text[start_idx] != "[":
        return None
    depth = 0
    in_str = False
    i = start_idx
    while i < len(text):
        c = text[i]
        if c == "\\" and in_str:
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return text[start_idx : i + 1]
        i += 1
    return None


def find_array_by_key(payloads, key):
    """Find and parse a JSON array value for a given key in RSC payloads.

    Searches for "key":[...] patterns and returns the parsed list.
    """
    search = f'"{key}"'
    for payload in payloads:
        idx = payload.find(search)
        if idx < 0:
            continue
        # Skip past the key and colon to find the array
        after_key = idx + len(search)
        rest = payload[after_key:].lstrip()
        if not rest.startswith(":"):
            continue
        rest = rest[1:].lstrip()
        if rest.startswith("["):
            arr_start = payload.index("[", after_key)
            arr_str = extract_json_array(payload, arr_start)
            if arr_str:
                try:
                    return json.loads(arr_str)
                except json.JSONDecodeError:
                    pass
    return None


def find_object_by_key(payloads, key):
    """Find and parse a JSON object value for a given key in RSC payloads."""
    search = f'"{key}"'
    for payload in payloads:
        idx = payload.find(search)
        if idx < 0:
            continue
        after_key = idx + len(search)
        rest = payload[after_key:].lstrip()
        if not rest.startswith(":"):
            continue
        rest = rest[1:].lstrip()
        if rest.startswith("{"):
            obj_start = payload.index("{", after_key)
            obj_str = extract_json_object(payload, obj_start)
            if obj_str:
                try:
                    return json.loads(obj_str)
                except json.JSONDecodeError:
                    pass
    return None


def find_containing_object(payloads, key):
    """Find and parse the JSON object that contains the given key."""
    search = f'"{key}"'
    for payload in payloads:
        idx = payload.find(search)
        if idx < 0:
            continue
        brace_idx = payload.rfind("{", 0, idx)
        if brace_idx < 0:
            continue
        json_str = extract_json_object(payload, brace_idx)
        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
    return None


def safe_filename(name):
    """Convert a display name to a safe filename."""
    return re.sub(r"[^\w-]", "_", name.lower()).strip("_")


def ext_from_url(url):
    """Extract file extension from a URL."""
    parsed = urllib.parse.urlparse(url)
    return os.path.splitext(parsed.path)[1] or ""


def scrape_zine():
    """Scrape the hearoursilence.com zine page — config, backgrounds, stickers, triggers."""
    print("Fetching /zine page...")
    html = fetch_html(f"{BASE_URL}/zine")

    # Save raw HTML
    html_path = os.path.join(OUT_DIR, "zine.html")
    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    with open(html_path, "w") as f:
        f.write(html)
    print(f"  Saved zine.html ({len(html)} bytes)")

    # Extract RSC payloads
    payloads = extract_rsc_payloads(html)
    print(f"  Extracted {len(payloads)} RSC payloads")

    # Try to find the full zine config object containing all arrays
    # Look for keys that indicate the zine data structure
    backgrounds = find_array_by_key(payloads, "backgrounds")
    stickers = find_array_by_key(payloads, "stickers")
    triggers = find_array_by_key(payloads, "triggers")
    colors = find_array_by_key(payloads, "colors")
    brush_sizes = find_array_by_key(payloads, "brushSizes")
    intro = find_object_by_key(payloads, "landscapeVideo") or find_containing_object(payloads, "landscapeVideo")

    # Build a combined config
    config = {}
    if backgrounds is not None:
        config["backgrounds"] = backgrounds
        print(f"  Found {len(backgrounds)} backgrounds")
    else:
        print("  WARNING: Could not find backgrounds")

    if stickers is not None:
        config["stickers"] = stickers
        print(f"  Found {len(stickers)} stickers")
    else:
        print("  WARNING: Could not find stickers")

    if triggers is not None:
        config["triggers"] = triggers
        print(f"  Found {len(triggers)} triggers")
    else:
        print("  WARNING: Could not find triggers")

    if colors is not None:
        config["colors"] = colors
        print(f"  Found {len(colors)} colors")
    else:
        print("  WARNING: Could not find colors")

    if brush_sizes is not None:
        config["brushSizes"] = brush_sizes
        print(f"  Found {len(brush_sizes)} brush sizes")

    if intro is not None:
        config["intro"] = intro
        print(f"  Found intro config")

    # Save full config JSON
    os.makedirs(JSON_DIR, exist_ok=True)
    with open(os.path.join(JSON_DIR, "silence-zine.json"), "w") as f:
        json.dump(config, f, indent=2)
    print(f"  Saved json/silence-zine.json")

    # Save raw payloads for debugging
    with open(os.path.join(JSON_DIR, "silence-rsc-payloads.json"), "w") as f:
        json.dump(payloads, f, indent=2)

    return config


def download_backgrounds(config):
    """Download all background images (landscape + portrait)."""
    backgrounds = config.get("backgrounds", [])
    if not backgrounds:
        return
    print(f"\nDownloading {len(backgrounds)} backgrounds...")
    dest = os.path.join(OUT_DIR, "backgrounds")

    for bg in backgrounds:
        name = safe_filename(bg.get("name", bg.get("id", "unknown")))
        image = bg.get("image", {})

        landscape_url = image.get("landscape", "")
        portrait_url = image.get("portrait", "")

        if landscape_url:
            ext = ext_from_url(landscape_url)
            download(landscape_url, os.path.join(dest, f"{name}_landscape{ext}"))
        if portrait_url:
            ext = ext_from_url(portrait_url)
            download(portrait_url, os.path.join(dest, f"{name}_portrait{ext}"))

        # Thumbnail
        thumb_url = bg.get("thumbnail", "")
        if thumb_url:
            ext = ext_from_url(thumb_url)
            download(thumb_url, os.path.join(dest, "thumbnails", f"{name}{ext}"))


def download_stickers(config):
    """Download all sticker SVGs."""
    stickers = config.get("stickers", [])
    if not stickers:
        return
    print(f"\nDownloading {len(stickers)} stickers...")
    dest = os.path.join(OUT_DIR, "stickers")

    for sticker in stickers:
        name = safe_filename(sticker.get("name", sticker.get("id", "unknown")))
        url = sticker.get("sticker", "")
        if url:
            ext = ext_from_url(url) or ".svg"
            download(url, os.path.join(dest, f"{name}{ext}"))


def download_triggers(config):
    """Download all trigger effect videos (landscape + portrait)."""
    triggers = config.get("triggers", [])
    if not triggers:
        return
    print(f"\nDownloading {len(triggers)} triggers...")
    dest = os.path.join(OUT_DIR, "triggers")

    for trigger in triggers:
        name = safe_filename(trigger.get("name", trigger.get("id", "unknown")))
        ttype = trigger.get("type", "unknown")
        effect = trigger.get("effect", {})

        landscape_url = effect.get("landscape", "")
        portrait_url = effect.get("portrait", "")

        subdir = os.path.join(dest, f"{name}_{ttype}")
        if landscape_url:
            ext = ext_from_url(landscape_url)
            download(landscape_url, os.path.join(subdir, f"landscape{ext}"))
        if portrait_url:
            ext = ext_from_url(portrait_url)
            download(portrait_url, os.path.join(subdir, f"portrait{ext}"))


def download_intro(config):
    """Download intro videos if present."""
    intro = config.get("intro", {})
    if not intro:
        return

    landscape = intro.get("landscapeVideo", "")
    portrait = intro.get("portraitVideo", "")

    if landscape or portrait:
        print("\nDownloading intro videos...")
        dest = os.path.join(OUT_DIR, "intro")
        if landscape:
            ext = ext_from_url(landscape)
            download(landscape, os.path.join(dest, f"intro_landscape{ext}"))
        if portrait:
            ext = ext_from_url(portrait)
            download(portrait, os.path.join(dest, f"intro_portrait{ext}"))
    else:
        print("\nIntro videos: empty (not yet populated)")


def scrape_fonts():
    """Download custom font files referenced in CSS."""
    print("\nFetching CSS for font URLs...")
    # Fetch the main CSS chunks to extract font URLs
    html = fetch_html(f"{BASE_URL}/zine")

    # Find CSS chunk URLs
    css_urls = re.findall(r'href="(/_next/static/chunks/[^"]+\.css)"', html)
    font_urls = set()

    for css_path in css_urls:
        css_url = f"{BASE_URL}{css_path}"
        try:
            css_content = fetch_html(css_url)
            # Extract font URLs from @font-face src
            for m in re.finditer(r'url\(([^)]+\.(?:woff2?|otf|ttf))\)', css_content):
                font_ref = m.group(1)
                # Resolve relative paths — Next.js fonts live under /_next/static/media/
                if font_ref.startswith("/"):
                    font_urls.add(f"{BASE_URL}{font_ref}")
                elif font_ref.startswith("http"):
                    font_urls.add(font_ref)
                else:
                    # Resolve ../media/foo relative to /_next/static/chunks/
                    css_dir = os.path.dirname(css_path)
                    resolved = os.path.normpath(f"{css_dir}/{font_ref}")
                    font_urls.add(f"{BASE_URL}{resolved}")
        except Exception as e:
            print(f"  Error fetching CSS {css_url}: {e}")

    if font_urls:
        print(f"  Found {len(font_urls)} font files")
        dest = os.path.join(OUT_DIR, "fonts")
        for url in sorted(font_urls):
            parsed = urllib.parse.urlparse(url)
            # Strip the hash suffix from Next.js font filenames for readability
            filename = os.path.basename(parsed.path)
            download(url, os.path.join(dest, filename))
    else:
        print("  No font files found in CSS")


def scrape_root():
    """Scrape the root page (separate from /zine — has ARG flavor text)."""
    print("\nFetching root page...")
    html = fetch_html(f"{BASE_URL}/")
    path = os.path.join(OUT_DIR, "index.html")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(html)
    print(f"  Saved index.html ({len(html)} bytes)")

    # Extract RSC payloads from root page too
    payloads = extract_rsc_payloads(html)
    if payloads:
        with open(os.path.join(JSON_DIR, "silence-root-payloads.json"), "w") as f:
            json.dump(payloads, f, indent=2)
        print(f"  Saved {len(payloads)} root RSC payloads")


def check_other_routes():
    """Probe for additional routes beyond /zine."""
    print("\nProbing for other routes...")
    candidates = [
        "/",
        "/about",
        "/archive",
        "/gallery",
        "/submit",
        "/issues",
        "/issue/1",
        "/zine/1",
        "/api",
        "/api/zine",
    ]
    for route in candidates:
        url = f"{BASE_URL}{route}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                final_url = resp.geturl()
                status = resp.status
                length = len(resp.read())
                if final_url != f"{BASE_URL}/zine" or route == "/zine":
                    print(f"  {route} -> {status} ({length} bytes) -> {final_url}")
                else:
                    print(f"  {route} -> redirects to /zine")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"  {route} -> {e.code}")
        except Exception:
            pass


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(JSON_DIR, exist_ok=True)

    # Scrape and parse the zine page RSC data
    config = scrape_zine()

    # Scrape root page (ARG flavor text, separate from /zine)
    scrape_root()

    # Download all assets by type
    download_backgrounds(config)
    download_stickers(config)
    download_triggers(config)
    download_intro(config)
    scrape_fonts()

    # Check for undiscovered routes
    check_other_routes()

    print("\nDone.")
