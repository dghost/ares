#!/usr/bin/env python3

import hashlib
import json
import os
import re
import shutil
import sys
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

import util

BASE_URL = "https://unityispower.io"
OUT_DIR = "./unityispower.io/"
ROUTES_JSON = "json/unity-routes.json"

# Initial route table (hardcoded in launcher JS)
INITIAL_ROUTES = {
    "8ad81bb4a6b0e7288a92c89478dc8a3a5bd7004d91cf301568665d2b9ac614d7": "/assets/b514e4e7.bin",
    "3820cfe44fcc288861e192ba8552f333ab5dacaf6a09ae77d27f0c8fe1294088": "/assets/7111c8d1.bin",
}

# Static assets discovered by deobfuscating launcher/sfx/fabricate JS
STATIC_ASSETS = [
    "/a/0af60c37.ogg",
    "/a/0f354c2d.ogg",
    "/a/1f935aa0.ogg",
    "/a/258c9b5c.ogg",
    "/a/2691fe25.ogg",
    "/a/2a7a5ea4.ogg",
    "/a/42ae4c8a.png",
    "/a/43a79fc6.ogg",
    "/a/4ee22e62.ogg",
    "/a/4f452c55.ogg",
    "/a/52bffdc2.ogg",
    "/a/76399edd.ogg",
    "/a/795dddf8.ogg",
    "/a/7ff31d61.ogg",
    "/a/85a70a95.ogg",
    "/a/8915bdd9.ogg",
    "/a/8c395b8a.png",
    "/a/9794be3e.ogg",
    "/a/a5103f5e.ogg",
    "/a/ae519fac.png",
    "/a/ceda2dfb.ogg",
    "/a/d45e6b5d.ogg",
    "/a/e5da4c95.ogg",
    "/a/e97d5ebc.png",
    "/a/fc8feff4.ogg",
]

# Launcher JS/CSS bundles
LAUNCHER_ASSETS = [
    "/assets/launcher-04b14837.js",
    "/assets/_ptyz-C-eQwEZn.js",
    "/assets/sfx-registry-BrqjwdWT.js",
    "/assets/crypto-CNrN-MH3.js",
    "/assets/shader-utils-iius7U5f.js",
    "/assets/crypto-CT9NFM2O.css",
    "/assets/launcher-DiBPDwMH.css",
    "/assets/card-viewer-standalone-r1rwLUzQ.js",
    "/assets/fabricate-card-viewer-CReE24rc.js",
    "/assets/card-viewer-standalone-3jPVkXr1.css",
]


# Inline ptyz decoder (from _ptyz-C-eQwEZn.js) for extracting obfuscated asset paths
_PTYZ_N = [198,223,149,197,149,219,149,217]
_PTYZ_O = [53,7,15,15,0,51,7,7]
_PTYZ_T = [188,188,186,190,183,184,177,188]
_PTYZ_R = [66,70,180,70,180,181,66,70]
_PTYZ_E = [170,85,195,60]
_PTYZ_I = [31,17,43,53]

def _ptyz_u(u):
    c = u & 3
    f = u >> 2
    tbl = [_PTYZ_N, _PTYZ_O, _PTYZ_T, _PTYZ_R][c]
    s = tbl[f]
    d = _PTYZ_E[c]
    return s + d - ((s & d) << 1) - _PTYZ_I[c]

def ptyz_decode(encoded):
    out = []
    for r, ch in enumerate(encoded):
        k = _ptyz_u(r % 32)
        e = ord(ch)
        c = e & k
        f = e + k - c
        out.append(chr(f - c))
    return "".join(out)

def _unescape_js_string(s):
    """Unescape a JS string literal (without surrounding quotes)."""
    out = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            nxt = s[i+1]
            if nxt == 'n': out.append('\n')
            elif nxt == 't': out.append('\t')
            elif nxt == 'r': out.append('\r')
            elif nxt == 'b': out.append('\b')
            elif nxt == 'f': out.append('\f')
            elif nxt == 'v': out.append('\v')
            elif nxt == '0': out.append('\0')
            elif nxt == '\\': out.append('\\')
            elif nxt == "'": out.append("'")
            elif nxt == '"': out.append('"')
            elif nxt == 'x':
                out.append(chr(int(s[i+2:i+4], 16)))
                i += 3
            elif nxt == 'u':
                out.append(chr(int(s[i+2:i+6], 16)))
                i += 5
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)

def decode_all_ptyz_strings(js_source):
    """Decode all ptyz-obfuscated strings from e("...") and t("...") calls in JS source."""
    decoded_strings = []
    for match in re.finditer(r'[et]\(("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')\)', js_source):
        raw = match.group(1)[1:-1]  # strip quotes
        try:
            unescaped = _unescape_js_string(raw)
            decoded = ptyz_decode(unescaped)
            decoded_strings.append(decoded)
        except:
            pass
    return decoded_strings


def extract_assets_from_js(js_source):
    """Extract /a/ asset paths by decoding obfuscated strings in JS source."""
    assets = set()
    for decoded in decode_all_ptyz_strings(js_source):
        if re.match(r'^/a/[a-f0-9]+\.\w+$', decoded):
            assets.add(decoded)
    return assets


def extract_text_from_js(js_source):
    """Extract human-readable text content from decoded ptyz strings."""
    texts = []
    seen = set()
    for decoded in decode_all_ptyz_strings(js_source):
        # Skip asset paths
        if re.match(r'^/a/[a-f0-9]+\.\w+$', decoded):
            continue
        # Skip strings that are mostly non-printable or garbage
        printable = sum(1 for c in decoded if c.isprintable() or c in '\n\r\t')
        if len(decoded) < 2 or printable / len(decoded) < 0.6:
            continue
        # Skip sfx_ event names (noise)
        if decoded.startswith("sfx_"):
            continue
        # Deduplicate
        if decoded in seen:
            continue
        seen.add(decoded)
        texts.append(decoded)
    return texts


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def decrypt_bin(code, data):
    """Decrypt an AES-256-GCM payload using PBKDF2-derived key from the code."""
    salt = data[:16]
    iv = data[16:28]
    ciphertext = data[28:]

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = kdf.derive(code.upper().encode())

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def download(url, path):
    """Download a URL to a local path, creating parent dirs as needed."""
    try:
        os.makedirs(os.path.dirname(path))
    except:
        pass
    if not os.path.exists(path):
        print(f"  Fetching {url}...")
        urllib.request.urlretrieve(url, filename=path)
    else:
        print(f"  Skipping {path} (exists)")


def scrape_static_assets(out_dir):
    """Download all known static /a/ assets."""
    print("Downloading static assets...")
    for asset in STATIC_ASSETS:
        path = os.path.join(os.path.normpath(out_dir), os.path.normpath(asset.lstrip("/")))
        download(f"{BASE_URL}{asset}", path)


def scrape_launcher_assets(out_dir):
    """Download launcher JS/CSS bundles."""
    print("Downloading launcher assets...")
    for asset in LAUNCHER_ASSETS:
        path = os.path.join(os.path.normpath(out_dir), os.path.normpath(asset.lstrip("/")))
        download(f"{BASE_URL}{asset}", path)


def scrape_and_decrypt(codes, out_dir):
    """Download .bin files, decrypt with known codes, discover new routes recursively."""
    all_routes = dict(INITIAL_ROUTES)
    code_map = {}  # hash -> code
    pending_codes = list(codes)
    processed_codes = set()
    discovered_assets = set()
    all_texts = {}  # code -> list of decoded text strings

    # Map provided codes to hashes
    for code in pending_codes:
        h = sha256(code.upper())
        code_map[h] = code.upper()

    while pending_codes:
        code = pending_codes.pop(0).upper()
        if code in processed_codes:
            continue
        processed_codes.add(code)

        h = sha256(code)
        if h not in all_routes:
            print(f"  Code {code} (hash {h[:12]}...) has no known route, skipping")
            continue

        bin_path = all_routes[h]
        local_bin = os.path.join(os.path.normpath(out_dir), os.path.normpath(bin_path.lstrip("/")))
        url = f"{BASE_URL}{bin_path}"

        # Download the .bin
        download(url, local_bin)

        # Decrypt
        print(f"  Decrypting {bin_path} with code {code}...")
        try:
            with open(local_bin, "rb") as f:
                data = f.read()
            payload = decrypt_bin(code, data)
        except Exception as e:
            print(f"  Decryption failed: {e}")
            continue

        # Save decrypted content
        dec_dir = os.path.join(os.path.normpath(out_dir), "decrypted", code)
        try:
            os.makedirs(dec_dir)
        except:
            pass

        if payload.get("js"):
            with open(os.path.join(dec_dir, "module.js"), "w") as f:
                f.write(payload["js"])
        if payload.get("css"):
            with open(os.path.join(dec_dir, "module.css"), "w") as f:
                f.write(payload["css"])

        meta = {}
        if payload.get("routes"):
            meta["routes"] = payload["routes"]
        if payload.get("keys"):
            meta["keys"] = payload["keys"]
        if meta:
            with open(os.path.join(dec_dir, "meta.json"), "w") as f:
                json.dump(meta, f, indent=4)

        print(f"  Saved decrypted payload to {dec_dir}")

        # Discover assets and text from decrypted JS
        if payload.get("js"):
            new_assets = extract_assets_from_js(payload["js"])
            for asset in sorted(new_assets):
                if asset not in discovered_assets:
                    print(f"  Discovered asset: {asset}")
                    discovered_assets.add(asset)

            texts = extract_text_from_js(payload["js"])
            if texts:
                all_texts[code] = texts
                print(f"  Extracted {len(texts)} text strings")

        # Discover new routes
        if payload.get("routes"):
            for route_hash, route_path in payload["routes"].items():
                if route_hash not in all_routes:
                    print(f"  Discovered new route: {route_hash[:12]}... -> {route_path}")
                all_routes[route_hash] = route_path

        # Discover new codes
        if payload.get("keys"):
            for label, new_code in payload["keys"].items():
                new_hash = sha256(new_code.upper())
                code_map[new_hash] = new_code.upper()
                if new_code.upper() not in processed_codes:
                    print(f"  Discovered new code: {new_code} ({label})")
                    pending_codes.append(new_code)

    # Download any .bin files we discovered but couldn't decrypt
    print("Downloading remaining .bin files...")
    for h, bin_path in all_routes.items():
        local_bin = os.path.join(os.path.normpath(out_dir), os.path.normpath(bin_path.lstrip("/")))
        download(f"{BASE_URL}{bin_path}", local_bin)

    # Download assets discovered from decrypted payloads
    if discovered_assets:
        print("Downloading assets discovered from decrypted payloads...")
        for asset in sorted(discovered_assets):
            path = os.path.join(os.path.normpath(out_dir), os.path.normpath(asset.lstrip("/")))
            download(f"{BASE_URL}{asset}", path)

    # Extract text from launcher JS files too
    for asset in LAUNCHER_ASSETS:
        if not asset.endswith(".js"):
            continue
        local_path = os.path.join(os.path.normpath(out_dir), os.path.normpath(asset.lstrip("/")))
        if os.path.exists(local_path):
            with open(local_path, "r") as f:
                js_source = f.read()
            texts = extract_text_from_js(js_source)
            if texts:
                label = os.path.basename(asset)
                all_texts[label] = texts
                print(f"  Extracted {len(texts)} text strings from {label}")

    return all_routes, code_map, all_texts


def dump_extracted_text(out_dir, all_texts):
    """Dump all decoded text content to files, organized by source module."""
    text_dir = os.path.join(os.path.normpath(out_dir), "extracted-text")
    try:
        os.makedirs(text_dir)
    except:
        pass

    # Per-module files
    for source, texts in sorted(all_texts.items()):
        fname = os.path.join(text_dir, f"{source}.txt")
        with open(fname, "w") as f:
            for text in sorted(texts, key=lambda s: s.lower()):
                if '\n' in text:
                    f.write(f"--- [{len(text)} chars] ---\n{text}\n\n")
                else:
                    f.write(f"{text}\n")
        print(f"  {source}: {len(texts)} strings -> {fname}")

    # Combined flat dump
    all_unique = set()
    for texts in all_texts.values():
        all_unique.update(texts)

    fname = os.path.join(text_dir, "all-text.txt")
    with open(fname, "w") as f:
        for text in sorted(all_unique, key=lambda s: s.lower()):
            if '\n' in text:
                f.write(f"--- [{len(text)} chars] ---\n{text}\n\n")
            else:
                f.write(f"{text}\n")
    print(f"  Combined: {len(all_unique)} unique strings -> {fname}")

    # Separate file for long narrative content (likely ARG content)
    narratives = []
    for texts in all_texts.values():
        for text in texts:
            if len(text) >= 80 and '\n' in text:
                narratives.append(text)

    if narratives:
        fname = os.path.join(text_dir, "narrative.txt")
        narratives.sort(key=lambda s: s[:60].lower())
        with open(fname, "w") as f:
            for i, text in enumerate(narratives):
                f.write(f"{'='*60}\n[Fragment {i+1}, {len(text)} chars]\n{'='*60}\n{text}\n\n")
        print(f"  Narrative fragments: {len(narratives)} -> {fname}")


def save_route_manifest(all_routes, code_map):
    """Save the route manifest mapping hashes to bin paths and known codes."""
    manifest = {}
    for h, bin_path in sorted(all_routes.items()):
        entry = {"bin": bin_path}
        if h in code_map:
            entry["code"] = code_map[h]
        manifest[h] = entry

    with open(ROUTES_JSON, "w") as f:
        json.dump(manifest, f, indent=4)
    print(f"Saved route manifest to {ROUTES_JSON}")


if __name__ == "__main__":
    codes = sys.argv[1:]
    if not codes:
        print("Usage: python3 scrape_unity.py CODE1 [CODE2 ...]")
        print("Example: python3 scrape_unity.py D5GY78C")
        sys.exit(1)

    # Create output directory
    odir = os.path.normpath(OUT_DIR)
    try:
        os.makedirs(odir)
    except:
        pass

    scrape_static_assets(OUT_DIR)
    scrape_launcher_assets(OUT_DIR)

    print("Processing codes and encrypted payloads...")
    all_routes, code_map, all_texts = scrape_and_decrypt(codes, OUT_DIR)
    save_route_manifest(all_routes, code_map)

    print("Extracting decoded text content...")
    dump_extracted_text(OUT_DIR, all_texts)

    print("Done.")
