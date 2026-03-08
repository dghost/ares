#!/usr/bin/env python3

import json
import os
import re
import sys
import urllib.request
import urllib.parse


BASE_URL = "https://tauceti.world"
OUT_DIR = "./tauceti.world/"
JSON_DIR = "./json/"

# Known shell slugs (discovered from runners list in RSC payload)
SHELL_SLUGS = ["destroyer", "vandal", "recon", "assassin", "triage", "thief"]

# Known static assets on the site itself (not CDN)
STATIC_ASSETS = [
    "/images/og-image-social.png",
    "/images/og-image-twitter.png",
    "/icon.svg",
    "/not-found-mobile.png",
    "/not-found-desktop.png",
]


def download(url, path, silent_404=False):
    """Download a URL to a local path, creating parent dirs as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        # Encode spaces and special chars in the URL path (preserve existing %XX)
        parsed = urllib.parse.urlparse(url)
        encoded_path = urllib.parse.quote(parsed.path, safe="/:@!$&'()*+,;=-._~%")
        safe_url = parsed._replace(path=encoded_path).geturl()
        print(f"  Fetching {safe_url}...")
        try:
            urllib.request.urlretrieve(safe_url, filename=path)
        except urllib.error.HTTPError as e:
            if e.code == 404 and silent_404:
                return False
            raise
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
    Returns the substring including the matched braces, or None on failure.
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


def find_rsc_data(payloads, key):
    """Find and parse a JSON object containing the given key from RSC payloads.

    Searches for {"key": ...} patterns and returns the parsed dict.
    """
    search = f'"{key}"'
    for payload in payloads:
        idx = payload.find(search)
        if idx < 0:
            continue
        # Walk back to find the opening brace of the containing object
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


def collect_asset_urls(obj, urls=None):
    """Recursively collect all CDN asset URLs from a nested data structure."""
    if urls is None:
        urls = set()
    if isinstance(obj, dict):
        if "url" in obj and isinstance(obj["url"], str):
            url = obj["url"]
            if "thecdn.io" in url or "b-cdn.net" in url:
                urls.add(url)
        for v in obj.values():
            collect_asset_urls(v, urls)
    elif isinstance(obj, list):
        for item in obj:
            collect_asset_urls(item, urls)
    return urls


def download_asset(url, dest_dir, filename=None, silent_404=False):
    """Download a CDN asset to a destination directory.

    Uses the URL's filename if none is provided. Returns the local path or None on 404.
    """
    if not filename:
        # Extract filename from URL (handle URL-encoded names)
        parsed = urllib.parse.urlparse(url)
        filename = os.path.basename(urllib.parse.unquote(parsed.path))
    path = os.path.join(dest_dir, filename)
    if not download(url, path, silent_404=silent_404):
        return None
    return path


def download_all_assets(data, dest_dir):
    """Download all CDN assets referenced in a data structure to dest_dir."""
    urls = collect_asset_urls(data)
    for url in sorted(urls):
        download_asset(url, dest_dir)
    return urls


def download_asset_field(asset_obj, dest_dir, filename_prefix=""):
    """Download an asset from a CMS asset field ({url, filename, alt_filename, ...}).

    Uses alt_filename for a readable name if available, otherwise the UUID filename.
    Returns the local path.
    """
    if not asset_obj or not isinstance(asset_obj, dict):
        return None
    url = asset_obj.get("url")
    if not url:
        return None
    # Prefer alt_filename for readable names, but fall back to the UUID filename
    name = asset_obj.get("alt_filename") or asset_obj.get("filename") or ""
    if not name or name == ".":
        parsed = urllib.parse.urlparse(url)
        name = os.path.basename(urllib.parse.unquote(parsed.path))
    if filename_prefix:
        name = filename_prefix + name
    path = os.path.join(dest_dir, name)
    download(url, path)
    return path


# ---------------------------------------------------------------------------
# Scraping functions
# ---------------------------------------------------------------------------


def scrape_index_html():
    """Fetch and save the raw homepage HTML."""
    print("Downloading index.html...")
    html = fetch_html(f"{BASE_URL}/")
    path = os.path.join(OUT_DIR, "index.html")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(html)
    print(f"  Saved index.html ({len(html)} bytes)")


def scrape_static_assets():
    """Download known static assets from the site."""
    print("Downloading static assets...")
    dest = os.path.join(OUT_DIR, "static")
    for asset in STATIC_ASSETS:
        download(f"{BASE_URL}{asset}", os.path.join(dest, asset.lstrip("/")))


def scrape_explorer():
    """Scrape the explorer page — planet map, locations, and all referenced assets."""
    print("Scraping explorer...")
    html = fetch_html(f"{BASE_URL}/explorer")

    payloads = extract_rsc_payloads(html)
    data = find_rsc_data(payloads, "explorerConfig")
    if not data:
        print("  ERROR: Could not find explorerConfig in RSC payload")
        return None

    config = data.get("explorerConfig", data)

    # Save full config JSON
    explorer_dir = os.path.join(OUT_DIR, "explorer")
    os.makedirs(explorer_dir, exist_ok=True)
    config_path = os.path.join(explorer_dir, "explorer.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"  Saved explorer config -> {config_path}")

    # Save JSON snapshot
    os.makedirs(JSON_DIR, exist_ok=True)
    with open(os.path.join(JSON_DIR, "tauceti-explorer.json"), "w") as f:
        json.dump(config, f, indent=2)

    # Download planet assets
    planet = config.get("planet", {})
    planet_dir = os.path.join(explorer_dir, "planet")
    for key in ("line_geometry", "mesh"):
        download_asset_field(planet.get(key), planet_dir)

    # Download marathon ship
    ship = config.get("marathon_ship", {})
    download_asset_field(ship.get("line_geometry"), planet_dir)

    # Download per-location assets
    locations = config.get("locations", [])
    print(f"  {len(locations)} locations found")
    for loc in locations:
        name = loc.get("name", "unknown")
        slug = re.sub(r"[^\w-]", "-", name.lower()).strip("-")
        loc_dir = os.path.join(explorer_dir, "locations", slug)

        download_asset_field(loc.get("thumbnail"), loc_dir)
        download_asset_field(loc.get("loading_screen_landscape"), loc_dir)
        download_asset_field(loc.get("loading_screen_portrait"), loc_dir)

        env = loc.get("environment", {})
        download_asset_field(env.get("line_geometry"), loc_dir)
        download_asset_field(env.get("floor_texture"), loc_dir)
        download_asset_field(env.get("hotzones"), loc_dir)
        download_asset_field(env.get("runner_paths"), loc_dir)
        download_asset_field(env.get("uesc_troop_paths"), loc_dir)

        print(f"  Location: {name} -> {loc_dir}")

    return config


def scrape_shells():
    """Scrape all shell (character class) pages."""
    print("Scraping shells...")
    shells_dir = os.path.join(OUT_DIR, "shells")
    all_shell_data = {}
    runners_list = None

    for slug in SHELL_SLUGS:
        print(f"  Fetching shell: {slug}...")
        try:
            html = fetch_html(f"{BASE_URL}/shells/{slug}")
        except Exception as e:
            print(f"  ERROR fetching {slug}: {e}")
            continue

        payloads = extract_rsc_payloads(html)

        # Extract the runners list (available on every shell page)
        if runners_list is None:
            runners_data = find_rsc_data(payloads, "runners")
            if runners_data:
                runners_list = runners_data.get("runners", [])

        # Find the shell data — look for the {data: {name: "...", abilities: [...]}} object
        shell_data = None
        for payload in payloads:
            if '"abilities"' not in payload:
                continue
            # Find {"data":{"name": pattern
            data_m = re.search(r'\{"data":\{"name":', payload)
            if not data_m:
                continue
            json_str = extract_json_object(payload, data_m.start())
            if json_str:
                try:
                    parsed = json.loads(json_str)
                    shell_data = parsed.get("data", parsed)
                    break
                except json.JSONDecodeError:
                    pass

        if not shell_data:
            print(f"  WARNING: Could not extract data for {slug}")
            continue

        all_shell_data[slug] = shell_data

        # Save per-shell JSON
        shell_dir = os.path.join(shells_dir, slug)
        os.makedirs(shell_dir, exist_ok=True)
        with open(os.path.join(shell_dir, f"{slug}.json"), "w") as f:
            json.dump(shell_data, f, indent=2)

        # Download ability icons — named by ability
        abilities = shell_data.get("abilities", [])
        abilities_dir = os.path.join(shell_dir, "abilities")
        for i, ability in enumerate(abilities):
            icon = ability.get("icon")
            if not icon or not icon.get("url"):
                continue
            # First ability is the role overview icon; rest are named abilities
            if i == 0:
                safe_name = "role-icon"
            else:
                safe_name = re.sub(r"[^\w-]", "_", ability.get("name", "icon").lower()).strip("_")
            ext = os.path.splitext(icon.get("filename", ".png"))[1] or ".png"
            download_asset(icon["url"], abilities_dir, f"{safe_name}{ext}")

        # Download CCTV files — grouped by media type, named by body part
        cctv = shell_data.get("cctv", {})
        if isinstance(cctv, dict):
            cctv_items = cctv.get("cctvItems", [])
        else:
            cctv_items = []
        if not cctv_items:
            cctv_items = shell_data.get("cctvItems", [])

        if cctv_items and isinstance(cctv_items, dict):
            for part_name, item in cctv_items.items():
                if not isinstance(item, dict) or not item.get("enabled"):
                    continue
                file_obj = item.get("file")
                if file_obj and file_obj.get("url"):
                    mime = file_obj.get("mimeType", "")
                    subdir = "video" if "video" in mime else "audio" if "audio" in mime else "other"
                    cctv_dir = os.path.join(shell_dir, "cctv", subdir)
                    in_name = item.get("name", "")
                    # Strip extension from in-universe name, use real extension from mime
                    base = os.path.splitext(in_name)[0] if in_name else part_name
                    ext = os.path.splitext(file_obj.get("filename", ""))[1] or ".bin"
                    download_asset(file_obj["url"], cctv_dir, f"{part_name}--{base}{ext}")
        elif cctv_items and isinstance(cctv_items, list) and isinstance(cctv_items[0], dict):
            for item in cctv_items:
                file_obj = item.get("file")
                if file_obj and file_obj.get("url"):
                    mime = file_obj.get("mimeType", "")
                    subdir = "video" if "video" in mime else "audio" if "audio" in mime else "other"
                    cctv_dir = os.path.join(shell_dir, "cctv", subdir)
                    in_name = item.get("name", "")
                    base = os.path.splitext(in_name)[0] if in_name else "file"
                    ext = os.path.splitext(file_obj.get("filename", ""))[1] or ".bin"
                    download_asset(file_obj["url"], cctv_dir, f"{base}{ext}")

        # Download 3D model
        base_url = shell_data.get("base_url", "")
        model_path = shell_data.get("model_path", "")
        if base_url and model_path:
            model_url = f"{base_url}/{model_path}"
            model_dir = os.path.join(shell_dir, "model")
            download_asset(model_url, model_dir, os.path.basename(model_path))

        # Download skin assets (preview images + alternate models)
        for skin in shell_data.get("skins", []):
            if isinstance(skin, dict):
                skin_name = re.sub(r"[^\w-]", "_", skin.get("name", "skin").lower())
                skin_dir = os.path.join(shell_dir, "skins", skin_name)
                img = skin.get("image", {})
                if isinstance(img, dict) and img.get("path") and base_url:
                    img_url = f"{base_url}/{img['path']}"
                    download_asset(img_url, skin_dir, f"{skin_name}.png")
                skin_model = skin.get("model_path")
                if skin_model and base_url:
                    model_url = f"{base_url}/{skin_model}"
                    download_asset(model_url, skin_dir, f"{skin_name}.glb")

        print(f"  Shell: {shell_data.get('name', slug)} ({len(abilities)} abilities)")

    # Save runners list
    if runners_list:
        os.makedirs(shells_dir, exist_ok=True)
        with open(os.path.join(shells_dir, "shells.json"), "w") as f:
            json.dump(runners_list, f, indent=2)
        print(f"  Saved shells list ({len(runners_list)} runners)")

    # Save JSON snapshot
    os.makedirs(JSON_DIR, exist_ok=True)
    snapshot = {"runners": runners_list or [], "shells": all_shell_data}
    with open(os.path.join(JSON_DIR, "tauceti-shells.json"), "w") as f:
        json.dump(snapshot, f, indent=2, default=str)

    return all_shell_data


def scrape_weapons():
    """Scrape all weapon pages."""
    print("Scraping weapons...")
    weapons_dir = os.path.join(OUT_DIR, "weapons")
    all_weapon_data = {}
    weapons_list = None
    weapon_slugs = None

    # First, fetch one weapon page to discover the full weapons list
    print("  Discovering weapon slugs...")
    try:
        html = fetch_html(f"{BASE_URL}/weapons/m77-assault-rifle")
        payloads = extract_rsc_payloads(html)
        for payload in payloads:
            if '"weapons"' not in payload or '"slug"' not in payload:
                continue
            # Find {"data":{"name":"M77 ...
            data_m = re.search(r'\{"data":\{"name":', payload)
            if not data_m:
                continue
            json_str = extract_json_object(payload, data_m.start())
            if json_str:
                try:
                    parsed = json.loads(json_str)
                    weapons_list = parsed.get("weapons", [])
                    weapon_data = parsed.get("data")
                    if weapon_data:
                        all_weapon_data["m77-assault-rifle"] = weapon_data
                    break
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"  ERROR discovering weapons: {e}")

    if weapons_list:
        weapon_slugs = [w.get("slug") for w in weapons_list if w.get("slug")]
        print(f"  Found {len(weapon_slugs)} weapons ({sum(1 for w in weapons_list if w.get('redacted'))} redacted)")
    else:
        weapon_slugs = ["m77-assault-rifle"]
        print("  WARNING: Could not discover weapon list, using fallback")

    # Fetch each weapon page
    for slug in weapon_slugs:
        if slug in all_weapon_data:
            # Already fetched from discovery page
            weapon_data = all_weapon_data[slug]
        else:
            print(f"  Fetching weapon: {slug}...")
            try:
                html = fetch_html(f"{BASE_URL}/weapons/{slug}")
            except Exception as e:
                print(f"  ERROR fetching {slug}: {e}")
                continue

            payloads = extract_rsc_payloads(html)
            weapon_data = None
            for payload in payloads:
                data_m = re.search(r'\{"data":\{"name":', payload)
                if not data_m:
                    continue
                json_str = extract_json_object(payload, data_m.start())
                if json_str:
                    try:
                        parsed = json.loads(json_str)
                        weapon_data = parsed.get("data")
                        break
                    except json.JSONDecodeError:
                        pass

            if not weapon_data:
                print(f"  WARNING: Could not extract data for {slug}")
                continue
            all_weapon_data[slug] = weapon_data

        # Save per-weapon JSON
        wpn_dir = os.path.join(weapons_dir, slug)
        os.makedirs(wpn_dir, exist_ok=True)
        with open(os.path.join(wpn_dir, f"{slug}.json"), "w") as f:
            json.dump(weapon_data, f, indent=2)

        # Download CCTV files — named by in-universe filename, grouped by media type
        cctv = weapon_data.get("cctv", {})
        cctv_items = cctv.get("cctvItems", []) if isinstance(cctv, dict) else []
        if cctv_items:
            items_iter = cctv_items.values() if isinstance(cctv_items, dict) else cctv_items
            for item in items_iter:
                if not isinstance(item, dict):
                    continue
                file_obj = item.get("file")
                if file_obj and file_obj.get("url"):
                    mime = file_obj.get("mimeType", "")
                    subdir = "video" if "video" in mime else "audio" if "audio" in mime else "other"
                    cctv_dir = os.path.join(wpn_dir, "cctv", subdir)
                    # Use alt_filename (in-universe name with proper ext) or fall back
                    fname = file_obj.get("alt_filename") or file_obj.get("filename", "file.bin")
                    download_asset(file_obj["url"], cctv_dir, fname)
                audio = item.get("audio")
                if audio and audio.get("url"):
                    cctv_dir = os.path.join(wpn_dir, "cctv", "audio")
                    fname = audio.get("alt_filename") or audio.get("filename", "audio.bin")
                    download_asset(audio["url"], cctv_dir, fname)

        # Download 3D model
        base_url = weapon_data.get("base_url", "")
        model_path = weapon_data.get("model_path", "")
        if base_url and model_path:
            model_url = f"{base_url}/{model_path}"
            model_dir = os.path.join(wpn_dir, "model")
            download_asset(model_url, model_dir, os.path.basename(model_path))

        # Download skin alternate models (texture paths reference data inside .glb)
        for skin in weapon_data.get("skins", []):
            if isinstance(skin, dict):
                skin_model = skin.get("model_path")
                if skin_model and base_url:
                    skin_name = re.sub(r"[^\w-]", "_", skin.get("name", "skin").lower())
                    skin_dir = os.path.join(wpn_dir, "skins", skin_name)
                    model_url = f"{base_url}/{skin_model}"
                    download_asset(model_url, skin_dir, f"{skin_name}.glb", silent_404=True)

        # Download mod assets — organized by slot
        mods = weapon_data.get("mods", {})
        if isinstance(mods, dict):
            for mod_slot, mod_list in mods.items():
                if not isinstance(mod_list, list):
                    continue
                for mod in mod_list:
                    if not isinstance(mod, dict):
                        continue
                    mod_name = re.sub(r"[^\w-]", "_", mod.get("name", "mod").lower())
                    mod_dir = os.path.join(wpn_dir, "mods", mod_slot)
                    # Mod model
                    mod_model = mod.get("model_path")
                    if mod_model and base_url:
                        mod_url = f"{base_url}/{mod_model}"
                        download_asset(mod_url, mod_dir, f"{mod_name}.glb", silent_404=True)
                    # Mod thumbnail and image
                    for img_key in ("thumbnail", "image"):
                        img_path = mod.get(img_key)
                        if img_path and base_url:
                            img_url = f"{base_url}/{img_path}"
                            ext = os.path.splitext(img_path)[1] or ".webp"
                            download_asset(img_url, mod_dir, f"{mod_name}_{img_key}{ext}", silent_404=True)

        name = weapon_data.get("name", slug)
        locked = cctv.get("cctvLockedFiles", 0) if isinstance(cctv, dict) else 0
        print(f"  Weapon: {name} (CCTV: {len(cctv_items)} open, {locked} locked)")

    # Save weapons list
    if weapons_list:
        os.makedirs(weapons_dir, exist_ok=True)
        with open(os.path.join(weapons_dir, "weapons.json"), "w") as f:
            json.dump(weapons_list, f, indent=2)

    # Save JSON snapshot
    os.makedirs(JSON_DIR, exist_ok=True)
    snapshot = {"weapons": weapons_list or [], "weapon_data": all_weapon_data}
    with open(os.path.join(JSON_DIR, "tauceti-weapons.json"), "w") as f:
        json.dump(snapshot, f, indent=2, default=str)

    return all_weapon_data


def scrape_profiling():
    """Scrape the profiling/DAC upload page."""
    print("Scraping profiling page...")
    try:
        html = fetch_html(f"{BASE_URL}/profiling")
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

    payloads = extract_rsc_payloads(html)
    data = find_rsc_data(payloads, "dacUploadMessage")
    if not data:
        print("  WARNING: Could not find profiling data")
        return None

    # Clean out RSC component references
    clean = {}
    for k, v in data.items():
        if k == "children":
            continue
        clean[k] = v

    profiling_dir = os.path.join(OUT_DIR, "profiling")
    os.makedirs(profiling_dir, exist_ok=True)
    with open(os.path.join(profiling_dir, "profiling.json"), "w") as f:
        json.dump(clean, f, indent=2)
    print(f"  DAC message: {clean.get('dacUploadMessage', 'N/A')}")
    print(f"  Discord: {clean.get('discordChannelSharingUrl', 'N/A')}")

    os.makedirs(JSON_DIR, exist_ok=True)
    with open(os.path.join(JSON_DIR, "tauceti-profiling.json"), "w") as f:
        json.dump(clean, f, indent=2)

    return clean


def dump_text_summaries():
    """Extract readable text from JSON data into plain text files."""
    text_dir = os.path.join(OUT_DIR, "text")
    os.makedirs(text_dir, exist_ok=True)

    # Explorer locations
    explorer_path = os.path.join(JSON_DIR, "tauceti-explorer.json")
    if os.path.exists(explorer_path):
        with open(explorer_path) as f:
            explorer = json.load(f)
        lines = ["TAU CETI IV — EXPLORER", "=" * 40, ""]
        # Planet info
        colony = explorer.get("colony_marker", {})
        if colony:
            lines.append(f"Colony marker: lat {colony.get('latitude')}, lon {colony.get('longitude')}")
            lines.append("")

        for loc in explorer.get("locations", []):
            lines.append(f"## {loc['name']}")
            lines.append(f"Max crew: {loc.get('max_crew_count', '?')}")
            lines.append("")
            desc = loc.get("description", "")
            if desc:
                lines.append(desc.strip())
                lines.append("")

            # Regions
            regions = loc.get("regions", [])
            if regions:
                names = [r["name"] for r in regions if isinstance(r, dict) and r.get("name")]
                lines.append(f"Regions ({len(regions)}): {', '.join(names)}")

            # Landmarks
            landmarks = loc.get("landmarks", [])
            if landmarks:
                names = [l["name"] for l in landmarks if isinstance(l, dict) and l.get("name")]
                lines.append(f"Landmarks ({len(landmarks)}): {', '.join(names)}")

            # Lore points
            lore = loc.get("lore_points", [])
            if lore:
                names = [l["name"] for l in lore if isinstance(l, dict) and l.get("name")]
                lines.append(f"Lore points ({len(lore)}): {', '.join(names)}")

            # UESC troops
            troops = loc.get("uesc_troops", [])
            if troops:
                t0 = troops[0] if troops else {}
                types = t0.get("name", "") if isinstance(t0, dict) else ""
                lines.append(f"UESC troop spawns: {len(troops)} (types: {types})")

            # Counts
            lines.append(f"Exfils: {len(loc.get('exfils', []))}")
            lines.append(f"Runner spawn points: {len(loc.get('runner_spawn_points', []))}")
            lines.append("")
            lines.append("-" * 40)
            lines.append("")

        with open(os.path.join(text_dir, "explorer.txt"), "w") as f:
            f.write("\n".join(lines))
        print(f"  Wrote {text_dir}/explorer.txt")

    # Shells
    shells_path = os.path.join(JSON_DIR, "tauceti-shells.json")
    if os.path.exists(shells_path):
        with open(shells_path) as f:
            data = json.load(f)
        lines = ["TAU CETI IV — SHELLS (RUNNERS)", "=" * 40, ""]

        for slug, shell in data.get("shells", {}).items():
            lines.append(f"## {shell.get('name', slug)}")
            lines.append("")

            for a in shell.get("abilities", []):
                lines.append(f"  [{a.get('type', '')}] {a.get('name', '')}")
                if a.get("heading"):
                    lines.append(f"  {a['heading']}")
                if a.get("description"):
                    for dline in a["description"].strip().split("\n"):
                        lines.append(f"    {dline}")
                lines.append("")

            # Skins
            skins = shell.get("skins", [])
            if skins:
                skin_names = [s["name"] for s in skins if isinstance(s, dict)]
                lines.append(f"  Skins: {', '.join(skin_names)}")
                lines.append("")

            # CCTV
            cctv = shell.get("cctvItems", {})
            if isinstance(cctv, dict):
                enabled = [(part, item) for part, item in cctv.items()
                           if isinstance(item, dict) and item.get("enabled")]
                disabled = [(part, item) for part, item in cctv.items()
                            if isinstance(item, dict) and not item.get("enabled")]
                if enabled:
                    lines.append(f"  CCTV ({len(enabled)} unlocked, {len(disabled)} locked):")
                    for part, item in enabled:
                        f_obj = item.get("file", {})
                        alt = f_obj.get("alt", "") if isinstance(f_obj, dict) else ""
                        mime = f_obj.get("mimeType", "") if isinstance(f_obj, dict) else ""
                        tag = "video" if "video" in mime else "audio" if "audio" in mime else "file"
                        lines.append(f"    {part}: {item.get('name', '?')} [{tag}] \"{alt}\"")
                    lines.append("")

            lines.append("-" * 40)
            lines.append("")

        with open(os.path.join(text_dir, "shells.txt"), "w") as f:
            f.write("\n".join(lines))
        print(f"  Wrote {text_dir}/shells.txt")

    # Weapons
    weapons_path = os.path.join(JSON_DIR, "tauceti-weapons.json")
    if os.path.exists(weapons_path):
        with open(weapons_path) as f:
            data = json.load(f)
        lines = ["TAU CETI IV — WEAPONS", "=" * 40, ""]

        # Weapons list overview
        wlist = data.get("weapons", [])
        lines.append(f"Total weapons: {len(wlist)}")
        redacted = [w for w in wlist if w.get("redacted")]
        if redacted:
            lines.append(f"Redacted: {len(redacted)} ({', '.join(w['slug'] for w in redacted)})")
        lines.append("")

        for slug, wpn in data.get("weapon_data", {}).items():
            name = wpn.get("name", slug)
            lines.append(f"## {name}")

            # CCTV
            cctv = wpn.get("cctv", {})
            cctv_items = cctv.get("cctvItems", []) if isinstance(cctv, dict) else []
            locked = cctv.get("cctvLockedFiles", 0) if isinstance(cctv, dict) else 0
            if isinstance(cctv_items, list):
                lines.append(f"  CCTV: {len(cctv_items)} unlocked, {locked} locked")
                for item in cctv_items:
                    if isinstance(item, dict):
                        alt = item.get("file", {}).get("alt", "") if isinstance(item.get("file"), dict) else ""
                        lines.append(f"    {item.get('name', '?')} @ {item.get('location', '?')} \"{alt}\"")

            # Sliding puzzle
            sp = wpn.get("sliding_puzzle")
            if sp is not None:
                lines.append(f"  Sliding puzzle: {sp}")
            else:
                lines.append(f"  Sliding puzzle: null")

            # Mods
            mods = wpn.get("mods", {})
            if isinstance(mods, dict) and mods:
                lines.append(f"  Mods:")
                for slot, mod_list in mods.items():
                    if isinstance(mod_list, list):
                        for mod in mod_list:
                            if isinstance(mod, dict):
                                lines.append(f"    {slot}: {mod.get('name', '?')} ({mod.get('type', '?')}) — {mod.get('specs', '?')}")

            # Skins
            skins = wpn.get("skins", [])
            if skins:
                skin_names = [s["name"] for s in skins if isinstance(s, dict)]
                lines.append(f"  Skins: {', '.join(skin_names)}")

            # Stickers, charms
            stickers = wpn.get("stickers", [])
            charms = wpn.get("charms", [])
            if stickers:
                lines.append(f"  Stickers: {len(stickers)}")
            if charms:
                lines.append(f"  Charms: {len(charms)}")

            lines.append("")
            lines.append("-" * 40)
            lines.append("")

        with open(os.path.join(text_dir, "weapons.txt"), "w") as f:
            f.write("\n".join(lines))
        print(f"  Wrote {text_dir}/weapons.txt")

    # Profiling
    profiling_path = os.path.join(JSON_DIR, "tauceti-profiling.json")
    if os.path.exists(profiling_path):
        with open(profiling_path) as f:
            data = json.load(f)
        lines = ["TAU CETI IV — PROFILING", "=" * 40, ""]
        lines.append(f"DAC Upload Message: {data.get('dacUploadMessage', 'N/A')}")
        lines.append(f"Discord: {data.get('discordChannelSharingUrl', 'N/A')}")
        lines.append("")
        with open(os.path.join(text_dir, "profiling.txt"), "w") as f:
            f.write("\n".join(lines))
        print(f"  Wrote {text_dir}/profiling.txt")


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(JSON_DIR, exist_ok=True)

    scrape_index_html()
    scrape_static_assets()
    scrape_explorer()
    scrape_shells()
    scrape_weapons()
    scrape_profiling()

    print("\nExtracting text summaries...")
    dump_text_summaries()

    print("Done.")
