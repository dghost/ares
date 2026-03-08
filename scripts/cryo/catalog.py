#!/usr/bin/env python3
"""Download and exhaustively catalog all client-side code from cryoarchive.systems.

Outputs:
  .raw/js/          — all JS chunks
  .raw/css/         — all CSS
  .raw/json/catalog.json — structured catalog of everything found

No dependencies beyond stdlib.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict

BASE_URL = "https://cryoarchive.systems"
OUT = "./cryoarchive.systems/.raw/"
HTML_DIR = f"{OUT}html/"
JS_DIR = f"{OUT}js/"
CSS_DIR = f"{OUT}css/"

HEADERS = {"User-Agent": "Mozilla/5.0"}

ROUTES = ["/", "/cargo", "/indx", "/index", "/steerage", "/revival",
          "/biostock", "/preservation", "/cryohub", "/example"]


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def fetch_text(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def download(url, path):
    if os.path.exists(path):
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        urllib.request.urlretrieve(url, filename=path)
        return True
    except urllib.error.HTTPError as e:
        print(f"  {e.code}: {url}")
        return False


# ---------------------------------------------------------------------------
# Phase 1: Fetch all HTML pages (refresh from live site)
# ---------------------------------------------------------------------------

def fetch_pages():
    print("[1] Fetching HTML pages...")
    os.makedirs(HTML_DIR, exist_ok=True)
    pages = {}
    for route in ROUTES:
        slug = route.strip("/") or "root"
        try:
            html = fetch_text(f"{BASE_URL}{route}")
            path = f"{HTML_DIR}{slug}.html"
            with open(path, "w") as f:
                f.write(html)
            pages[slug] = html
            print(f"  {route} -> {len(html):,} bytes")
        except urllib.error.HTTPError as e:
            print(f"  {route} -> {e.code}")
        time.sleep(0.3)
    return pages


# ---------------------------------------------------------------------------
# Phase 2: Extract and download all static assets (JS, CSS, fonts, media refs)
# ---------------------------------------------------------------------------

def extract_all_static_refs(pages):
    """Extract every /_next/static/ reference across all pages."""
    js_chunks = {}       # hash -> set of pages
    css_files = set()
    font_files = set()
    other_static = set()
    turbopack = None

    for slug, html in pages.items():
        # JS chunks (hex hash only)
        for m in re.finditer(r'/_next/static/chunks/([0-9a-f]+)\.js', html):
            h = m.group(1)
            js_chunks.setdefault(h, set()).add(slug)

        # Turbopack bootstrap
        for m in re.finditer(r'/_next/static/chunks/(turbopack-[0-9a-f]+\.js)', html):
            turbopack = m.group(1)
            js_chunks.setdefault(m.group(1), set()).add(slug)

        # CSS
        for m in re.finditer(r'/_next/static/chunks/([0-9a-f]+\.css)', html):
            css_files.add(m.group(1))

        # Fonts / media
        for m in re.finditer(r'/_next/static/media/([^\s"\']+)', html):
            font_files.add(m.group(1))

        # Any other static refs
        for m in re.finditer(r'(/_next/static/[^\s"\']+)', html):
            ref = m.group(1)
            if not any(ref.endswith(ext) for ext in ['.js', '.css']):
                other_static.add(ref)

    return js_chunks, css_files, font_files, other_static, turbopack


def download_chunks(js_chunks, css_files):
    print("\n[2] Downloading JS chunks...")
    os.makedirs(JS_DIR, exist_ok=True)
    for chunk in sorted(js_chunks):
        fname = chunk if chunk.endswith('.js') else f"{chunk}.js"
        url = f"{BASE_URL}/_next/static/chunks/{fname}"
        path = f"{JS_DIR}{fname}"
        if not os.path.exists(path):
            print(f"  {fname}")
            download(url, path)
            time.sleep(0.15)

    print("\n[3] Downloading CSS...")
    os.makedirs(CSS_DIR, exist_ok=True)
    for css in sorted(css_files):
        url = f"{BASE_URL}/_next/static/chunks/{css}"
        path = f"{CSS_DIR}{css}"
        if not os.path.exists(path):
            print(f"  {css}")
            download(url, path)


# ---------------------------------------------------------------------------
# Phase 3: Analyze JS chunks
# ---------------------------------------------------------------------------

# Patterns to extract from JS
PATTERNS = {
    "api_endpoints": r'["\'](/api/[a-zA-Z0-9/_\-?=&]+)["\']',
    "zod_schemas": r'(z\.(?:object|enum|string|number|boolean|literal|array|union|optional|nullable)\([^)]*\))',
    "zustand_stores": r'(create(?:Store)?|useStore)\s*\(',
    "use_swr": r'useSWR\s*\(\s*["\']([^"\']+)["\']',
    "module_ids": r'(\d{4,5}):\s*(?:function|e\s*=>)',
    "react_components": r'function\s+([A-Z][a-zA-Z0-9]+)\s*\(',
    "class_names": r'class\s+([A-Z][a-zA-Z0-9]+)\s*(?:extends|{)',
    "cdn_urls": r'(https?://assets\.thecdn\.io/[^\s"\']+)',
    "hls_refs": r'(\.m3u8|video_url|poster_url|HLSVideo)',
    "websocket": r'(WebSocket|wss?://[^\s"\']+)',
    "localstorage_keys": r'localStorage\.\w+Item\s*\(\s*["\']([^"\']+)["\']',
    "cookie_names": r'["\']([a-zA-Z_]+)["\'].*(?:cookie|Cookie)',
    "canvas_webgl": r'(getContext\s*\(\s*["\'](?:2d|webgl|webgl2)["\'])',
    "event_listeners": r'addEventListener\s*\(\s*["\']([^"\']+)["\']',
    "keyboard_keys": r'(?:key|code)\s*===?\s*["\']([^"\']+)["\']',
    "named_exports": r'export\s+(?:const|function|class)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)',
}

# Strings that indicate interesting content
SIGNAL_STRINGS = [
    "initialState", "gameState", "memoryUnlocked", "uescKillCount",
    "allCamerasCaptured", "camerasCaptured", "indexAuthenticated",
    "stabilization", "nextStabilizationAt", "dacId", "goliath",
    "CodeLanguage", "PPFraktionMono", "feTurbulence",
    "pointCloud", "gaussian", "splat",
    "fogOfWar", "steerage", "preservation", "decryptor",
    "quiz", "revival", "biostock",
    "WaveSurfer", "wavesurfer",
    "Unauthorized", "PRTSC", "Shift+S",
]


def analyze_chunk(path, filename):
    """Analyze a single JS chunk, return structured findings."""
    try:
        code = open(path).read()
    except Exception:
        return {"error": "unreadable"}

    info = {
        "filename": filename,
        "size_bytes": os.path.getsize(path),
        "sha256": hashlib.sha256(code.encode()).hexdigest()[:16],
    }

    # Run all pattern extractions
    for name, pattern in PATTERNS.items():
        matches = re.findall(pattern, code)
        if matches:
            # Deduplicate and limit
            unique = sorted(set(matches))
            info[name] = unique

    # Check for signal strings
    signals = [s for s in SIGNAL_STRINGS if s in code]
    if signals:
        info["signals"] = signals

    # Extract string literals that look like component/page names
    # (quoted strings starting with uppercase, 3-30 chars)
    component_strings = set(re.findall(r'["\']([A-Z][a-zA-Z]{2,29})["\']', code))
    # Filter out common noise
    noise = {"GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH",
             "String", "Number", "Boolean", "Object", "Array", "Function",
             "Error", "Promise", "Symbol", "Map", "Set", "Date", "RegExp",
             "TypeError", "SyntaxError", "RangeError", "JSON", "Math",
             "Infinity", "NaN", "Proxy", "Reflect", "Window", "Document",
             "Element", "Node", "Event", "Image", "Canvas", "Audio", "Video",
             "HTMLElement", "SVGElement", "CSSStyleDeclaration",
             "XMLHttpRequest", "AbortController", "ReadableStream",
             "TextDecoder", "TextEncoder", "Uint8Array", "Float32Array",
             "ArrayBuffer", "DataView", "SharedArrayBuffer",
             "Headers", "Request", "Response", "URL", "URLSearchParams"}
    component_strings -= noise
    if component_strings:
        info["named_strings"] = sorted(component_strings)[:50]

    # Detect third-party libraries
    libs = []
    if "three" in code.lower() and ("WebGLRenderer" in code or "Scene" in code):
        libs.append("three.js")
    if "gsap" in code.lower() or "TweenMax" in code:
        libs.append("gsap")
    if "framer-motion" in code or "motion.div" in code or "AnimatePresence" in code:
        libs.append("motion")
    if "zustand" in code or "createStore" in code:
        libs.append("zustand")
    if "useSWR" in code or "swr" in code:
        libs.append("swr")
    if re.search(r'\bz\.object\b', code):
        libs.append("zod")
    if "wavesurfer" in code.lower() or "WaveSurfer" in code:
        libs.append("wavesurfer")
    if "GaussianSplats" in code or "gaussian" in code.lower():
        libs.append("gaussian-splats-3d")
    if "hls.js" in code.lower() or "Hls.Events" in code:
        libs.append("hls.js")
    if "jose" in code or "jwtVerify" in code or "SignJWT" in code:
        libs.append("jose")
    if "jsonwebtoken" in code or "jwt.sign" in code or "jwt.verify" in code:
        libs.append("jsonwebtoken")
    if libs:
        info["libraries"] = libs

    # Minified vs readable heuristic
    lines = code.count('\n')
    if lines < 10 and info["size_bytes"] > 5000:
        info["minified"] = True
    else:
        info["avg_line_length"] = info["size_bytes"] // max(lines, 1)
        info["minified"] = info["avg_line_length"] > 500

    return info


# ---------------------------------------------------------------------------
# Phase 4: Analyze CSS
# ---------------------------------------------------------------------------

def analyze_css(path, filename):
    try:
        code = open(path).read()
    except Exception:
        return {"error": "unreadable"}

    info = {
        "filename": filename,
        "size_bytes": os.path.getsize(path),
    }

    # Extract CSS custom properties (variables)
    variables = sorted(set(re.findall(r'(--[a-zA-Z0-9_-]+)', code)))
    if variables:
        info["css_variables"] = variables

    # Extract color values
    colors = sorted(set(re.findall(r'(#[0-9a-fA-F]{3,8})\b', code)))
    if colors:
        info["colors"] = colors

    # Extract font families
    fonts = sorted(set(re.findall(r'font-family:\s*([^;}{]+)', code)))
    if fonts:
        info["font_families"] = [f.strip() for f in fonts]

    # Extract animation names
    animations = sorted(set(re.findall(r'@keyframes\s+([a-zA-Z_-]+)', code)))
    if animations:
        info["animations"] = animations

    # Extract class names (from selectors)
    classes = sorted(set(re.findall(r'\.([a-zA-Z_][a-zA-Z0-9_-]{2,})\b', code)))
    if classes:
        info["class_count"] = len(classes)
        # Just keep interesting-looking ones
        interesting = [c for c in classes if not re.match(r'^[a-z]{1,2}[A-Z]', c)]
        if interesting:
            info["notable_classes"] = interesting[:30]

    return info


# ---------------------------------------------------------------------------
# Phase 5: RSC payload analysis
# ---------------------------------------------------------------------------

def extract_rsc_payloads(html):
    payloads = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL):
        try:
            payloads.append(m.group(1).encode().decode("unicode_escape"))
        except (UnicodeDecodeError, ValueError):
            pass
    return payloads


def analyze_rsc(slug, html):
    """Analyze RSC payloads from a page's HTML."""
    payloads = extract_rsc_payloads(html)
    if not payloads:
        return {"payload_count": 0}

    info = {
        "payload_count": len(payloads),
        "total_size": sum(len(p) for p in payloads),
    }

    # Parse RSC records
    modules = []
    components = set()
    data_keys = set()

    for p in payloads:
        # Module references: I[moduleId,[chunks],"export"]
        for m in re.finditer(r'I\[(\d+),\s*\[([^\]]*)\],\s*"([^"]*)"', p):
            modules.append({
                "module_id": int(m.group(1)),
                "export": m.group(3),
            })

        # Component tree nodes: ["$","ComponentName",...
        for m in re.finditer(r'\["\$","([A-Za-z][A-Za-z0-9_.]+)"', p):
            components.add(m.group(1))

        # Data keys in props
        for m in re.finditer(r'"([a-zA-Z_][a-zA-Z0-9_]{2,})"\s*:', p):
            data_keys.add(m.group(1))

    if modules:
        # Deduplicate by module_id + export
        seen = set()
        unique_modules = []
        for mod in modules:
            key = (mod["module_id"], mod["export"])
            if key not in seen:
                seen.add(key)
                unique_modules.append(mod)
        info["modules"] = sorted(unique_modules, key=lambda m: m["module_id"])

    if components:
        info["components"] = sorted(components)

    if data_keys:
        # Filter to interesting keys only (skip generic ones)
        interesting = {k for k in data_keys if len(k) > 4 and not k.startswith("__")}
        info["data_key_count"] = len(interesting)
        # Highlight known important ones
        known = {"initialState", "pages", "unlocked", "completed", "camerasCaptured",
                 "memoryUnlocked", "uescKillCount", "shipDate", "stabilizationLevel",
                 "nextStabilizationAt", "filename", "alt_filename", "mimeType",
                 "videoPreview", "videoPreviewUnstable", "videoFull",
                 "indexAuthenticated", "allCamerasCaptured", "dacId"}
        found_known = sorted(interesting & known)
        if found_known:
            info["known_data_keys"] = found_known

    return info


# ---------------------------------------------------------------------------
# Phase 6: Build page map (which chunk does what)
# ---------------------------------------------------------------------------

def build_page_map(js_chunks, chunk_analyses):
    """Classify chunks: shared framework, shared app, page-specific."""
    page_map = {}
    for chunk_name, page_set in js_chunks.items():
        fname = chunk_name if chunk_name.endswith('.js') else f"{chunk_name}.js"
        analysis = chunk_analyses.get(fname, {})

        classification = "shared" if len(page_set) >= 8 else "page-specific"
        if classification == "page-specific" and len(page_set) > 1:
            classification = f"shared ({','.join(sorted(page_set))})"

        # Determine role from content — APIs are strongest signal, then libs
        role = "unknown"
        libs = analysis.get("libraries", [])
        signals = analysis.get("signals", [])
        apis = set(analysis.get("api_endpoints", []))
        size = analysis.get("size_bytes", 0)
        named = analysis.get("named_strings", [])

        if any("/api/point-cloud/" in a for a in apis):
            role = "biostock-game"
        elif any("/api/decryptor/" in a for a in apis):
            role = "preservation-game"
        elif any("/api/quiz/" in a for a in apis):
            role = "revival-game"
        elif "/api/indx/auth" in apis:
            role = "index-auth"
        elif any("/api/cctv-cameras/" in a for a in apis) or "PRTSC" in signals:
            role = "cctv-grid"
        elif "/api/public/state" in apis and "ClientArgStateProvider" in named:
            role = "app-state"
        elif "three.js" in libs and "gaussian-splats-3d" in libs:
            role = "gaussian-splat-renderer"
        elif "three.js" in libs:
            role = "3d-renderer"
        elif "hls.js" in libs:
            role = "hls-player"
        elif "wavesurfer" in libs:
            role = "audio-player"
        elif "memoryUnlocked" in signals and "uescKillCount" in signals and len(apis) == 0:
            role = "cryohub-room"
        elif "zustand" in libs and "zod" in libs and size < 50000:
            role = "game-store"
        elif "zustand" in libs and size < 30000:
            role = "page-store"
        elif "motion" in libs and size < 50000:
            role = "ui-components"
        elif "fogOfWar" in signals:
            role = "steerage-game"
        elif "SessionProvider" in named:
            role = "session-provider"
        elif size > 200000:
            role = "vendor-library"
        elif analysis.get("minified") and size > 50000:
            role = "vendor-library"
        elif "ClientPageRoot" in named or "ClientSegmentRoot" in named:
            role = "next-framework"
        elif "HandleISRError" in named or "RedirectStatusCode" in named:
            role = "next-framework"
        elif size < 1000:
            role = "polyfill"

        page_map[fname] = {
            "scope": classification,
            "pages": sorted(page_set),
            "role": role,
            "size": size,
        }

    return page_map


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Phase 1: Fetch pages
    pages = fetch_pages()

    # Phase 2: Extract refs and download
    js_chunks, css_files, font_files, other_static, turbopack = extract_all_static_refs(pages)
    print(f"\nFound: {len(js_chunks)} JS chunks, {len(css_files)} CSS, "
          f"{len(font_files)} fonts, {len(other_static)} other")
    download_chunks(js_chunks, css_files)

    # Phase 3: Analyze JS
    print("\n[4] Analyzing JS chunks...")
    chunk_analyses = {}
    for fname in sorted(os.listdir(JS_DIR)):
        if not fname.endswith('.js'):
            continue
        path = f"{JS_DIR}{fname}"
        chunk_analyses[fname] = analyze_chunk(path, fname)

    # Phase 4: Analyze CSS
    print("[5] Analyzing CSS...")
    css_analyses = {}
    for fname in sorted(os.listdir(CSS_DIR)):
        if not fname.endswith('.css'):
            continue
        path = f"{CSS_DIR}{fname}"
        css_analyses[fname] = analyze_css(path, fname)

    # Phase 5: RSC per page
    print("[6] Analyzing RSC payloads...")
    rsc_analyses = {}
    for slug, html in pages.items():
        rsc_analyses[slug] = analyze_rsc(slug, html)

    # Phase 6: Page map
    page_map = build_page_map(js_chunks, chunk_analyses)

    # Build catalog
    catalog = {
        "summary": {
            "total_js_chunks": len(chunk_analyses),
            "total_css_files": len(css_analyses),
            "total_pages": len(pages),
            "font_files": sorted(font_files),
            "turbopack_hash": turbopack,
            "total_js_bytes": sum(a.get("size_bytes", 0) for a in chunk_analyses.values()),
        },
        "page_map": page_map,
        "js_chunks": chunk_analyses,
        "css": css_analyses,
        "rsc_payloads": rsc_analyses,
    }

    # Aggregate: all libraries found
    all_libs = set()
    for a in chunk_analyses.values():
        all_libs.update(a.get("libraries", []))
    catalog["summary"]["libraries"] = sorted(all_libs)

    # Aggregate: all API endpoints found
    all_apis = set()
    for a in chunk_analyses.values():
        all_apis.update(a.get("api_endpoints", []))
    catalog["summary"]["api_endpoints"] = sorted(all_apis)

    # Aggregate: all keyboard keys
    all_keys = set()
    for a in chunk_analyses.values():
        all_keys.update(a.get("keyboard_keys", []))
    catalog["summary"]["keyboard_keys"] = sorted(all_keys)

    # Aggregate: all RSC modules (deduplicated)
    all_modules = {}
    for slug, rsc in rsc_analyses.items():
        for mod in rsc.get("modules", []):
            mid = mod["module_id"]
            if mid not in all_modules:
                all_modules[mid] = {"export": mod["export"], "pages": []}
            if slug not in all_modules[mid]["pages"]:
                all_modules[mid]["pages"].append(slug)
    catalog["summary"]["rsc_modules"] = {
        str(k): v for k, v in sorted(all_modules.items())
    }

    # Save
    out_path = f"{OUT}json/catalog.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"JS chunks:   {catalog['summary']['total_js_chunks']} "
          f"({catalog['summary']['total_js_bytes']:,} bytes)")
    print(f"CSS files:   {len(css_analyses)}")
    print(f"Pages:       {len(pages)}")
    print(f"Libraries:   {', '.join(catalog['summary']['libraries'])}")
    print(f"API routes:  {len(catalog['summary']['api_endpoints'])}")
    print(f"RSC modules: {len(all_modules)}")
    print(f"Fonts:       {len(font_files)}")
    print(f"Keyboard:    {', '.join(sorted(all_keys)[:15])}...")

    print(f"\nPage-specific chunks:")
    for fname, info in sorted(page_map.items(), key=lambda x: x[1]["scope"]):
        if info["scope"] != "shared":
            print(f"  {fname:30s}  {info['role']:20s}  {info['scope']}")

    print(f"\nSaved: {out_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
