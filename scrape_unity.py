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
PAYLOADS_JSON = "json/unity-payloads.json"
ASSETS_JSON = "json/unity-assets.json"

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


def scrape_launcher_assets(out_dir):
    """Download launcher JS/CSS bundles."""
    print("Downloading launcher assets...")
    for asset in LAUNCHER_ASSETS:
        path = os.path.join(os.path.normpath(out_dir), os.path.normpath(asset.lstrip("/")))
        download(f"{BASE_URL}{asset}", path)


def sync_assets(out_dir, all_asset_urls, downloaded_map):
    """Download remaining assets not already fetched by structured dumps.

    Args:
        all_asset_urls: set of all known /a/ asset paths
        downloaded_map: dict of {asset_path: local_dest} for files already
                        downloaded by structured dumps (crossword, media)

    Loads the existing manifest, merges in all known URLs with their local
    destinations, downloads anything not yet on disk to a/, and saves the
    updated manifest.
    """
    # Load existing manifest: {asset_path: local_path}
    manifest = {}
    if os.path.exists(ASSETS_JSON):
        with open(ASSETS_JSON, "r") as f:
            manifest = json.load(f)

    # Merge in structured downloads
    manifest.update(downloaded_map)

    # Add any remaining URLs with default a/ destination
    for url in all_asset_urls:
        if url not in manifest:
            manifest[url] = url.lstrip("/")

    # Download anything not yet on disk
    fetched = 0
    for asset_path, local_rel in sorted(manifest.items()):
        local = os.path.join(os.path.normpath(out_dir), os.path.normpath(local_rel))
        if not os.path.exists(local):
            download(f"{BASE_URL}{asset_path}", local)
            fetched += 1

    if fetched:
        print(f"  Downloaded {fetched} new assets")
    else:
        print(f"  All {len(manifest)} assets already on disk")

    # Save updated manifest
    with open(ASSETS_JSON, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    print(f"Saved asset manifest to {ASSETS_JSON} ({len(manifest)} assets)")


def scrape_and_decrypt(codes, out_dir):
    """Download .bin files, decrypt with known codes, discover new routes recursively."""
    all_routes = dict(INITIAL_ROUTES)
    code_map = {}  # hash -> code
    pending_codes = list(codes)
    processed_codes = set()
    discovered_asset_urls = set()
    all_texts = {}  # code -> list of decoded text strings
    all_payloads = {}  # code -> raw decrypted payload (for archival)

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

        # Archive the raw payload (minus JS/CSS which are saved as files)
        archived = {}
        if payload.get("routes"):
            archived["routes"] = payload["routes"]
        if payload.get("keys"):
            archived["keys"] = payload["keys"]
        archived["bin"] = bin_path
        archived["has_js"] = bool(payload.get("js"))
        archived["has_css"] = bool(payload.get("css"))
        all_payloads[code] = archived

        print(f"  Saved decrypted payload to {dec_dir}")

        # Discover assets and text from decrypted JS
        if payload.get("js"):
            new_assets = extract_assets_from_js(payload["js"])
            discovered_asset_urls |= new_assets

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

    return all_routes, code_map, all_texts, all_payloads, discovered_asset_urls


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


def _decode_nt_table(js_source):
    """Extract and decode the nt[] string table from terminal module JS."""
    m = re.search(r'var nt=\[', js_source)
    if not m:
        return None

    # Find matching ] with string-aware bracket tracking
    start = m.start() + len('var nt=')
    depth = 0
    in_string = None
    i = start
    while i < len(js_source):
        ch = js_source[i]
        if in_string:
            if ch == '\\':
                i += 2
                continue
            if ch == in_string:
                in_string = None
        else:
            if ch in ('"', "'"):
                in_string = ch
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    break
        i += 1

    nt_source = js_source[start:i + 1]

    # Decode each t('...') / e('...') entry in order
    entries = []
    for match in re.finditer(r"""[et]\(('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\)""", nt_source):
        raw = match.group(1)[1:-1]
        try:
            unescaped = _unescape_js_string(raw)
            decoded = ptyz_decode(unescaped)
            entries.append(decoded)
        except:
            entries.append("")
    return entries


def _resolve_nt_concat(expr, nt):
    """Resolve an expression like nt[158]+nt[147] to a string."""
    parts = []
    for m in re.finditer(r'nt\[(\d+)\]', expr):
        idx = int(m.group(1))
        if idx < len(nt):
            parts.append(nt[idx])
    return "".join(parts) if parts else None


def _find_matching_brace(js, start):
    """Find the matching closing brace for an opening { at start, handling strings."""
    depth = 0
    in_string = None
    in_template = 0
    i = start
    while i < len(js):
        ch = js[i]
        if in_string:
            if ch == '\\':
                i += 2
                continue
            if ch == in_string:
                in_string = None
        elif in_template > 0 and ch == '`':
            in_template -= 1
        else:
            if ch in ('"', "'"):
                in_string = ch
            elif ch == '`':
                in_template += 1
            elif ch == '{' and in_template == 0:
                depth += 1
            elif ch == '}' and in_template == 0:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _parse_g_children(body, nt):
    """Parse children inside a G({...}) block, returning a dict of name -> node."""
    children = {}

    # Match [nt[N]]:B(...) and [nt[N]]:G({...}) and [nt[N]]:P(...)
    # Also handle compound keys like [nt[59]+nt[72]+nt[56]+nt[174]]
    pos = 0
    while pos < len(body):
        # Find next key: [nt[...]] or [nt[...]+nt[...]+...]
        key_match = re.search(r'\[(nt\[\d+\](?:\+nt\[\d+\])*)\]\s*:', body[pos:])
        if not key_match:
            break

        key_start = pos + key_match.start()
        key_expr = key_match.group(1)
        name = _resolve_nt_concat(key_expr, nt)
        if not name:
            pos = key_start + len(key_match.group(0))
            continue

        # What follows the colon?
        after_colon = body[pos + key_match.end():]
        after_colon_stripped = after_colon.lstrip()

        if after_colon_stripped.startswith('B('):
            # File node - extract content from B(...)
            b_start = after_colon.index('B(')
            paren_start = pos + key_match.end() + b_start + 2
            # Check if it's a template literal B(`...`)
            rest = body[paren_start:].lstrip()
            if rest.startswith('`'):
                # Template literal - mark as template
                children[name] = {"type": "file", "content": "<template>"}
            else:
                # nt[] concatenation - find the closing )
                depth = 1
                j = paren_start
                while j < len(body) and depth > 0:
                    if body[j] == '(': depth += 1
                    elif body[j] == ')': depth -= 1
                    j += 1
                content_expr = body[paren_start:j-1]
                content = _resolve_nt_concat(content_expr, nt)
                children[name] = {"type": "file", "content": content or ""}
            pos = key_start + len(key_match.group(0)) + 1

        elif after_colon_stripped.startswith('G('):
            # Directory node - find the G({...}) block
            g_pos = pos + key_match.end() + after_colon.index('G(')
            brace_pos = body.index('{', g_pos)
            brace_end = _find_matching_brace(body, brace_pos)
            if brace_end < 0:
                pos = key_start + len(key_match.group(0)) + 1
                continue
            inner = body[brace_pos + 1:brace_end]
            sub_children = _parse_g_children(inner, nt)
            children[name] = {"type": "dir", "children": sub_children}
            pos = brace_end + 1

        elif after_colon_stripped.startswith('P('):
            # Dynamic file node
            children[name] = {"type": "file", "content": "<dynamic>"}
            pos = key_start + len(key_match.group(0)) + 1

        else:
            pos = key_start + len(key_match.group(0)) + 1

    return children


def extract_terminal_filesystem(js_source):
    """Extract the simulated UNIX filesystem from the terminal module JS.

    Returns a dict tree: {name: {type: "dir", children: {...}} | {type: "file", content: "..."}}
    """
    nt = _decode_nt_table(js_source)
    if not nt:
        return None

    # Find the root filesystem: const Z=function(t){...return G(((r={})...r))}
    m = re.search(r'const Z=function\(t\)\{', js_source)
    if not m:
        return None

    # Find the function body
    func_start = m.start()
    brace_start = js_source.index('{', func_start)
    func_end = _find_matching_brace(js_source, brace_start)
    if func_end < 0:
        return None

    func_body = js_source[brace_start + 1:func_end]

    # Parse top-level r[nt[N]]=G({...}) and r[nt[N]]=function(...) assignments
    tree = {}
    pos = 0
    while pos < len(func_body):
        # Match r[nt[N]]= assignments
        assign_match = re.search(r'r\[(nt\[\d+\])\]\s*=\s*', func_body[pos:])
        if not assign_match:
            break

        key_expr = assign_match.group(1)
        name = _resolve_nt_concat(key_expr, nt)
        after = func_body[pos + assign_match.end():].lstrip()

        if after.startswith('G('):
            # Directory
            brace_pos = func_body.index('{', pos + assign_match.end())
            brace_end = _find_matching_brace(func_body, brace_pos)
            if brace_end < 0:
                break
            inner = func_body[brace_pos + 1:brace_end]
            children = _parse_g_children(inner, nt)
            tree[name] = {"type": "dir", "children": children}
            pos = brace_end + 1
        elif after.startswith('function'):
            # Dynamic directory (like /proc)
            fn_brace = func_body.index('{', pos + assign_match.end())
            fn_end = _find_matching_brace(func_body, fn_brace)
            if fn_end < 0:
                break
            tree[name] = {"type": "dir", "children": {"<dynamic>": {"type": "file", "content": "<generated at runtime>"}}}
            pos = fn_end + 1
        else:
            pos += assign_match.end() + 1
            continue

    return tree if tree else None


def dump_terminal_filesystem(out_dir, fs_tree):
    """Write the extracted filesystem tree to disk as individual files and a JSON manifest."""
    if not fs_tree:
        return

    # Save JSON manifest
    term_dir = os.path.join(os.path.normpath(out_dir), "terminal")
    os.makedirs(term_dir, exist_ok=True)
    json_path = os.path.join(term_dir, "filesystem.json")
    with open(json_path, "w") as f:
        json.dump(fs_tree, f, indent=2)
    print(f"  Filesystem JSON -> {json_path}")

    # Write individual files
    fs_dir = os.path.join(os.path.normpath(out_dir), "terminal", "fs")
    file_count = 0

    def write_node(node, path):
        nonlocal file_count
        if node["type"] == "file":
            content = node.get("content", "")
            if content in ("<dynamic>", "<template>", "<generated at runtime>"):
                return
            local_path = os.path.join(fs_dir, path.lstrip("/"))
            try:
                os.makedirs(os.path.dirname(local_path))
            except:
                pass
            with open(local_path, "w") as f:
                f.write(content)
            file_count += 1
        elif node["type"] == "dir":
            for name, child in node.get("children", {}).items():
                if name.startswith("<"):
                    continue
                write_node(child, os.path.join(path, name))

    for name, node in fs_tree.items():
        write_node(node, "/" + name)

    print(f"  Wrote {file_count} files -> {fs_dir}/")


def _rot_n(text, n):
    """Apply ROT-N to alphabetic characters only."""
    out = []
    for c in text:
        if 'A' <= c <= 'Z':
            out.append(chr((ord(c) - ord('A') - n) % 26 + ord('A')))
        elif 'a' <= c <= 'z':
            out.append(chr((ord(c) - ord('a') - n) % 26 + ord('a')))
        else:
            out.append(c)
    return ''.join(out)


def _clean_corruption(text):
    """Replace corrupted ptyz-artifact sections with [corrupted] markers."""
    def char_score(c):
        if c in '\n\r':
            return 1
        if not c.isprintable() and c != '\t':
            return -2
        if c.isalpha() or c.isdigit() or c in ' \t':
            return 1
        if c in '.,;:!?\'"-()[]':
            return 0.5
        if c in '@#$>─':
            return 0.3
        if c in '{}|~`^\\':
            return -0.5
        if ord(c) > 127:
            return -1
        return 0

    window = 8
    scores = [char_score(c) for c in text]

    is_corrupt = [False] * len(text)
    for i in range(len(text)):
        start = max(0, i - window // 2)
        end = min(len(text), i + window // 2 + 1)
        avg = sum(scores[start:end]) / (end - start)
        if avg < 0.3:
            is_corrupt[i] = True

    expanded = list(is_corrupt)
    for i in range(len(text)):
        if is_corrupt[i]:
            for j in range(max(0, i - 2), min(len(text), i + 3)):
                if scores[j] < 0.5:
                    expanded[j] = True

    out = []
    i = 0
    while i < len(text):
        if not expanded[i]:
            out.append(text[i])
            i += 1
        else:
            j = i
            while j < len(text) and expanded[j]:
                j += 1
            out.append('[corrupted]')
            i = j

    result = ''.join(out)
    result = re.sub(r'\[corrupted\](.{1,3})\[corrupted\]', '[corrupted]', result)
    while '[corrupted][corrupted]' in result:
        result = result.replace('[corrupted][corrupted]', '[corrupted]')
    return result


def generate_decoded_files(fs_tree, fs_dir):
    """Create decoded.* versions of encoded/corrupted filesystem files."""
    if not fs_tree:
        return

    def get_node(path):
        parts = path.strip('/').split('/')
        node = fs_tree
        for p in parts:
            if isinstance(node, dict):
                if node.get('type') == 'dir':
                    node = node['children'].get(p)
                elif p in node:
                    node = node[p]
                else:
                    return None
            else:
                return None
            if node is None:
                return None
        return node

    decodings = []

    # 1. ROT-7 cipher
    node = get_node('var/data/recovered/6bccdf29-d678-4ed5-ba92-61cf74c0a374.dat')
    if node and node.get('content'):
        content = node['content']
        lines = content.split('\n')
        header, body = [], []
        in_body = False
        for line in lines:
            (body if in_body else header).append(line)
            if line.startswith('---'):
                in_body = True
        decoded_body = _rot_n('\n'.join(body), 7)
        fixed = '\n'.join(header) + '\n' + decoded_body
        fixed = fixed.replace('STATUS: encrypted / unresolved', 'STATUS: DECRYPTED (was ROT-7)')
        path = os.path.join(fs_dir, 'var/data/recovered/decoded.6bccdf29-d678-4ed5-ba92-61cf74c0a374.dat')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(fixed)
        decodings.append(('var/data/recovered/6bccdf29-d678-4ed5-ba92-61cf74c0a374.dat',
                         'ROT-7 Caesar cipher', 'Shifted alphabetic chars back by 7'))

    # 2. Reversed text
    node = get_node('var/data/recovered/756706df-eb6d-4c37-885a-be32ab6287d2.dat')
    if node and node.get('content'):
        content = node['content']
        lines = content.split('\n')
        header, body = [], []
        in_body = False
        for line in lines:
            (body if in_body else header).append(line)
            if line.startswith('---'):
                in_body = True
        reversed_text = '\n'.join(body).strip()[::-1]
        fixed_header = '\n'.join(header).replace(
            'STATUS: encoding anomaly (reversed?)', 'STATUS: DECODED (was reversed text)')
        fixed = fixed_header + '\n\n' + reversed_text + '\n'
        path = os.path.join(fs_dir, 'var/data/recovered/decoded.756706df-eb6d-4c37-885a-be32ab6287d2.dat')
        with open(path, 'w') as f:
            f.write(fixed)
        decodings.append(('var/data/recovered/756706df-eb6d-4c37-885a-be32ab6287d2.dat',
                         'Reversed text', 'Reversed character sequence'))

    # 3. Corrupted Leela text
    node = get_node('var/data/recovered/ca7bf847-4038-471f-9b14-f807cc3e1307.dat')
    if node and node.get('content'):
        cleaned = _clean_corruption(node['content'])
        cleaned = cleaned.replace('STATUS: partially corrupted',
            'STATUS: CLEANED (non-printable bytes removed, corruption markers remain)')
        path = os.path.join(fs_dir, 'var/data/recovered/decoded.ca7bf847-4038-471f-9b14-f807cc3e1307.dat')
        with open(path, 'w') as f:
            f.write(cleaned)
        decodings.append(('var/data/recovered/ca7bf847-4038-471f-9b14-f807cc3e1307.dat',
                         'Corrupted plaintext', 'Removed non-printable bytes, added [corrupted] markers'))

    # 4. Cleartext with annotation
    node = get_node('var/data/recovered/a1c605d1-1214-4e87-b934-7f0219153400.dat')
    if node and node.get('content'):
        fixed = node['content'].replace('STATUS: cleartext / anomalous origin',
            'STATUS: cleartext / anomalous origin\n'
            'NOTE: Bungie developer quote about finishing Marathon map geometry, Dec 14 1996')
        path = os.path.join(fs_dir, 'var/data/recovered/decoded.a1c605d1-1214-4e87-b934-7f0219153400.dat')
        with open(path, 'w') as f:
            f.write(fixed)
        decodings.append(('var/data/recovered/a1c605d1-1214-4e87-b934-7f0219153400.dat',
                         'Cleartext', 'Added annotation note'))

    # 5-7. Chat logs
    for logfile in ['0801.log', '0802.log', '0803.log']:
        node = get_node(f'var/spool/msg/{logfile}')
        if node and node.get('content'):
            cleaned = _clean_corruption(node['content'])
            markers = cleaned.count('[corrupted]')
            path = os.path.join(fs_dir, f'var/spool/msg/decoded.{logfile}')
            with open(path, 'w') as f:
                f.write(cleaned)
            decodings.append((f'var/spool/msg/{logfile}',
                             'Corrupted chat log', f'Cleaned {markers} corruption zones'))

    # Write index file
    if decodings:
        index_path = os.path.join(os.path.dirname(fs_dir), 'decoded.txt')
        with open(index_path, 'w') as f:
            f.write('DECODED FILES INDEX\n')
            f.write('===================\n\n')
            f.write('Each file below has a "decoded." prefixed version alongside the original.\n')
            f.write('The original files are preserved exactly as extracted from the terminal module JS.\n\n')
            for orig_path, encoding, method in decodings:
                f.write(f'{orig_path}\n')
                f.write(f'  Encoding: {encoding}\n')
                f.write(f'  Decoding: {method}\n\n')
        print(f"  Generated {len(decodings)} decoded files + index -> {index_path}")


def _find_array(js, var_pattern, search_start=0):
    """Find a JS array by variable pattern and return its source string."""
    m = re.search(var_pattern, js[search_start:])
    if not m:
        return None
    abs_pos = search_start + m.start()
    start = js.index('[', abs_pos)
    depth = 0
    in_str = None
    i = start
    while i < len(js) and i < start + 50000:
        ch = js[i]
        if in_str:
            if ch == '\\':
                i += 2
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    break
        i += 1
    return js[start:i + 1]


def _split_array_entries(arr_src):
    """Split a JS array source into top-level entries."""
    entries = []
    d = 0
    current = ''
    in_s = None
    for ch in arr_src[1:-1]:
        if in_s:
            current += ch
            if ch == '\\':
                continue
            if ch == in_s:
                in_s = None
            continue
        if ch in ('"', "'"):
            in_s = ch
            current += ch
        elif ch in ('(', '[', '{'):
            d += 1
            current += ch
        elif ch in (')', ']', '}'):
            d -= 1
            current += ch
        elif ch == ',' and d == 0:
            entries.append(current.strip())
            current = ''
        else:
            current += ch
    if current.strip():
        entries.append(current.strip())
    return entries


def _decode_ptyz_field(raw_with_quotes):
    """Decode a ptyz-encoded field value (with surrounding quotes)."""
    raw = raw_with_quotes[1:-1]
    return ptyz_decode(_unescape_js_string(raw))


def extract_terminal_data(js_source):
    """Extract structured data from the terminal module (D5GY78C).

    Returns a dict with classified_ads, scan_output, ident_output, dmesg_lines,
    and mail_messages.
    """
    nt = _decode_nt_table(js_source)
    if not nt:
        return None

    data = {}

    # Classified ads (dt array) — 16 in-universe black market listings
    dt_src = _find_array(js_source, r'\bdt=\[', 244000)
    if dt_src:
        entries = _split_array_entries(dt_src)
        data["classified_ads"] = [_resolve_nt_concat(e, nt) or e for e in entries]

    # Scan output (ct) — static string
    m = re.search(r'\bct=(nt\[\d+\](?:\+nt\[\d+\])*)', js_source[244000:])
    if m:
        data["scan_output"] = _resolve_nt_concat(m.group(1), nt)

    # Ident output (ut) — static string
    m = re.search(r'\but=(nt\[\d+\](?:\+nt\[\d+\])*)', js_source[244000:])
    if m:
        data["ident_output"] = _resolve_nt_concat(m.group(1), nt)

    # Boot/dmesg lines (at array)
    at_src = _find_array(js_source, r'\bat=\[', 244000)
    if at_src:
        entries = _split_array_entries(at_src)
        data["dmesg_lines"] = [_resolve_nt_concat(e, nt) or e for e in entries]

    # Mail messages — conditional o() calls gated by game state flags
    mail_messages = []
    mail_m = re.search(r'f\("xword_complete"\)&&\(o\(""\)', js_source)
    if mail_m:
        chunk = js_source[mail_m.start():mail_m.start() + 1000]
        # Extract each conditional block
        for flag_m in re.finditer(r'f\("(\w+)"\)&&\(', chunk):
            flag = flag_m.group(1)
            block_start = flag_m.end()
            # Collect content until closing ))
            block = chunk[block_start:]
            # Find the paired ))
            paren_depth = 1
            end = 0
            in_s = None
            for ch in block:
                if in_s:
                    if ch == '\\':
                        end += 1
                        continue
                    if ch == in_s:
                        in_s = None
                elif ch in ('"', "'"):
                    in_s = ch
                elif ch == '(':
                    paren_depth += 1
                elif ch == ')':
                    paren_depth -= 1
                    if paren_depth == 0:
                        break
                end += 1
            block_src = block[:end]

            # Extract the o() call contents in order
            lines = []
            o_pos = 0
            while o_pos < len(block_src):
                om = re.search(r'o\(', block_src[o_pos:])
                if not om:
                    break
                arg_start = o_pos + om.end()
                # Find matching ) handling nested parens and template literals
                pd = 1
                in_tpl = 0
                in_s = None
                j = arg_start
                while j < len(block_src) and pd > 0:
                    ch = block_src[j]
                    if in_s:
                        if ch == '\\':
                            j += 1
                        elif ch == in_s:
                            in_s = None
                    elif in_tpl > 0:
                        if ch == '`':
                            in_tpl -= 1
                        elif ch == '\\':
                            j += 1
                    else:
                        if ch == '`':
                            in_tpl += 1
                        elif ch in ('"', "'"):
                            in_s = ch
                        elif ch == '(':
                            pd += 1
                        elif ch == ')':
                            pd -= 1
                    j += 1
                arg = block_src[arg_start:j - 1]
                o_pos = j

                if arg == '""' or arg == "''":
                    lines.append('')
                elif '`' in arg:
                    tpl = arg[arg.index('`') + 1:]
                    if tpl.endswith('`'):
                        tpl = tpl[:-1]
                    lines.append(re.sub(r'\$\{[^}]+\}', '<timestamp>', tpl))
                elif arg.startswith(('t(', 'e(')):
                    pm = re.search(r"""[te]\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\)""", arg)
                    if pm:
                        try:
                            lines.append(_decode_ptyz_field(pm.group(1)))
                        except:
                            lines.append(arg)
                elif arg.startswith(('"', "'")):
                    lines.append(arg[1:-1])
                else:
                    lines.append(arg)

            mail_messages.append({
                "condition": flag,
                "lines": lines,
            })
    if mail_messages:
        data["mail_messages"] = mail_messages

    return data if data else None


def extract_media_gallery(js_source):
    """Extract the media gallery array from the media module (374468739BD7269A48).

    Returns a list of {thumb, full, title, credit, type} dicts.
    """
    m = re.search(r'p=\[\{', js_source)
    if not m:
        return None

    arr_src = _find_array(js_source, r'p=\[\{')
    if not arr_src:
        return None

    obj_strs = re.split(r'\},\{', arr_src[2:-2])
    entries = []
    for obj_src in obj_strs:
        entry = {}
        for field in ['thumb', 'full', 'title', 'credit', 'type']:
            # Encoded: field:e("...")/t("...")
            pat = field + r""":([et])\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\)"""
            fm = re.search(pat, obj_src)
            if fm:
                try:
                    entry[field] = _decode_ptyz_field(fm.group(2))
                except:
                    pass
            else:
                # Plaintext: field:"..."
                fm = re.search(field + r':"([^"]*)"', obj_src)
                if fm:
                    entry[field] = fm.group(1)
        entries.append(entry)

    return entries if entries else None


def extract_crossword_data(js_source):
    """Extract crossword clues and answers from the crossword module (XWORD7K).

    Returns {across: [...], down: [...]} with each entry having
    num, row, col, answer, clueImage, clueUrl, imageAR.
    """
    def decode_clue_array(arr_src):
        """Parse a crossword clue array and decode ptyz strings."""
        def decode_inline(match):
            raw = match.group(1)[1:-1]
            try:
                return json.dumps(ptyz_decode(_unescape_js_string(raw)))
            except:
                return match.group(0)
        readable = re.sub(r"""[a-z]\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\)""", decode_inline, arr_src)
        # Parse as JSON (need to add quotes to keys)
        jsonified = re.sub(r'(?<=[{,])(\w+):', r'"\1":', readable)
        # Fix JS shorthand floats (.699 -> 0.699)
        jsonified = re.sub(r':\.(\d)', r':0.\1', jsonified)
        try:
            return json.loads(jsonified)
        except:
            return None

    # Across clues (C array)
    c_src = _find_array(js_source, r'C=\[\{')
    across = decode_clue_array(c_src) if c_src else None

    # Down clues (D array, right after C)
    if c_src:
        c_end = js_source.index(c_src) + len(c_src)
        d_src = _find_array(js_source, r'D=\[\{', c_end)
        down = decode_clue_array(d_src) if d_src else None
    else:
        down = None

    if across or down:
        result = {}
        if across:
            result["across"] = across
        if down:
            result["down"] = down
        return result
    return None


def extract_fabricate_data(js_source):
    """Extract signal IDs, character names, and game state mappings from the
    fabricate module (MTBFAB7).

    Returns a dict with signal_ids, character_names, and game_state_defaults.
    """
    data = {}

    # Signal IDs
    sigs = set()
    for m in re.finditer(r"""([et])\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\)""", js_source):
        raw = m.group(2)[1:-1]
        try:
            decoded = ptyz_decode(_unescape_js_string(raw))
        except:
            continue
        if re.match(r'^SIG-\d{3}[a-z]?$', decoded):
            sigs.add(decoded)
    if sigs:
        data["signal_ids"] = sorted(sigs)

    # Character names from card rendering
    names = []
    name_m = re.search(r"""\{text:([et])\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\)""", js_source[80000:])
    if name_m:
        # Look for the array of {text: ..., size: ..., y: ...} objects
        chunk = js_source[80000 + name_m.start() - 10:80000 + name_m.start() + 500]
        for nm in re.finditer(r"""text:([et])\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\)""", chunk):
            try:
                names.append(_decode_ptyz_field(nm.group(2)))
            except:
                pass
    if names:
        data["card_names"] = names

    # Game state defaults (pe object) — references ve[] indices
    # First decode ve array
    ve_decoded = []
    ve_src = _find_array(js_source, r'\bvar ve=\[')
    if ve_src:
        ve_decoded = decode_all_ptyz_strings(ve_src)

    # Find the pe={first_boot:ve[...], ...} object (not the actor pe={self:...})
    for pe_m in re.finditer(r'pe=\{first_boot:', js_source):
        brace_start = js_source.index('{', pe_m.start())
        depth = 0
        in_str = None
        i = brace_start
        while i < len(js_source) and i < brace_start + 500:
            ch = js_source[i]
            if in_str:
                if ch == '\\':
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            else:
                if ch in ('"', "'"):
                    in_str = ch
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        pe_src = js_source[brace_start:i + 1]
        defaults = {}
        for kv in re.finditer(r'(\w+):ve\[(\d+)\]', pe_src):
            key = kv.group(1)
            idx = int(kv.group(2))
            if idx < len(ve_decoded):
                defaults[key] = ve_decoded[idx]
        if defaults:
            data["game_state_defaults"] = defaults
        break

    return data if data else None


def dump_terminal_data(out_dir, terminal_data):
    """Write terminal module data as flat files alongside the filesystem."""
    term_dir = os.path.join(os.path.normpath(out_dir), "terminal")
    os.makedirs(term_dir, exist_ok=True)

    # Full JSON for machine consumption
    json_path = os.path.join(term_dir, "terminal.json")
    with open(json_path, "w") as f:
        json.dump(terminal_data, f, indent=2)

    # Classified ads — one per line
    if terminal_data.get("classified_ads"):
        path = os.path.join(term_dir, "classified-ads.txt")
        with open(path, "w") as f:
            for ad in terminal_data["classified_ads"]:
                f.write(ad + "\n")

    # Command outputs
    for key, filename in [("scan_output", "scan.txt"), ("ident_output", "ident.txt")]:
        if terminal_data.get(key):
            with open(os.path.join(term_dir, filename), "w") as f:
                f.write(terminal_data[key] + "\n")

    # Dmesg lines
    if terminal_data.get("dmesg_lines"):
        path = os.path.join(term_dir, "dmesg-boot.txt")
        with open(path, "w") as f:
            for line in terminal_data["dmesg_lines"]:
                f.write(line + "\n")

    # Mail messages
    if terminal_data.get("mail_messages"):
        path = os.path.join(term_dir, "mail.txt")
        with open(path, "w") as f:
            for msg in terminal_data["mail_messages"]:
                f.write(f"[unlocked by: {msg['condition']}]\n")
                for line in msg["lines"]:
                    f.write(line + "\n")
                f.write("\n")

    count = sum(1 for k in ("classified_ads", "scan_output", "ident_output",
                            "dmesg_lines", "mail_messages") if terminal_data.get(k))
    print(f"  Terminal data -> {term_dir}/ ({count} files + terminal.json)")


def _download_asset(asset_path, dest_path):
    """Download an asset to a named destination path.

    Returns (asset_path, dest_rel) for the manifest, or None on failure.
    """
    url = f"{BASE_URL}{asset_path}"
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    if not os.path.exists(dest_path):
        print(f"  Fetching {url}...")
        urllib.request.urlretrieve(url, filename=dest_path)
    return os.path.exists(dest_path)


def dump_crossword_data(out_dir, xword_data, downloaded):
    """Write crossword data as flat files — one per clue with answer and image."""
    xword_dir = os.path.join(os.path.normpath(out_dir), "crossword")
    clues_dir = os.path.join(xword_dir, "clues")
    os.makedirs(clues_dir, exist_ok=True)

    # Full JSON
    with open(os.path.join(xword_dir, "crossword.json"), "w") as f:
        json.dump(xword_data, f, indent=2)

    total = 0
    for direction in ("across", "down"):
        tag = "A" if direction == "across" else "D"
        for clue in xword_data.get(direction, []):
            num = clue["num"]
            answer = clue["answer"]
            fname = f"{num:02d}{tag}-{answer}.txt"
            with open(os.path.join(clues_dir, fname), "w") as f:
                f.write(f"answer: {answer}\n")
                f.write(f"direction: {direction}\n")
                f.write(f"position: row {clue['row']}, col {clue['col']}\n")
                if clue.get("clueUrl"):
                    f.write(f"clue-image: {clue['clueUrl']}\n")
            if clue.get("clueUrl"):
                ext = os.path.splitext(clue["clueUrl"])[1]
                dest_rel = os.path.join("crossword", "clues", f"{num:02d}{tag}-{answer}_clue{ext}")
                dest = os.path.join(os.path.normpath(out_dir), dest_rel)
                _download_asset(clue["clueUrl"], dest)
                downloaded[clue["clueUrl"]] = dest_rel
            total += 1

    print(f"  Crossword -> {xword_dir}/ ({total} clue files + crossword.json)")


def dump_media_gallery(out_dir, gallery, downloaded):
    """Write media gallery as flat files — one per entry with named assets."""
    media_dir = os.path.join(os.path.normpath(out_dir), "media")
    os.makedirs(media_dir, exist_ok=True)

    # Full JSON
    with open(os.path.join(media_dir, "media-gallery.json"), "w") as f:
        json.dump(gallery, f, indent=2)

    for entry in gallery:
        title = entry.get("title", "untitled")
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '-').lower()
        with open(os.path.join(media_dir, f"{safe_title}.txt"), "w") as f:
            f.write(f"title: {title}\n")
            if entry.get("type"):
                f.write(f"type: {entry['type']}\n")
            if entry.get("credit"):
                f.write(f"credit: {entry['credit']}\n")
            if entry.get("thumb"):
                f.write(f"thumbnail: {entry['thumb']}\n")
            if entry.get("full"):
                f.write(f"full: {entry['full']}\n")

        if entry.get("thumb"):
            ext = os.path.splitext(entry["thumb"])[1]
            dest_rel = os.path.join("media", f"{safe_title}_thumb{ext}")
            dest = os.path.join(os.path.normpath(out_dir), dest_rel)
            _download_asset(entry["thumb"], dest)
            downloaded[entry["thumb"]] = dest_rel
        if entry.get("full"):
            ext = os.path.splitext(entry["full"])[1]
            dest_rel = os.path.join("media", f"{safe_title}{ext}")
            dest = os.path.join(os.path.normpath(out_dir), dest_rel)
            _download_asset(entry["full"], dest)
            downloaded[entry["full"]] = dest_rel

    print(f"  Media gallery -> {media_dir}/ ({len(gallery)} entries + media-gallery.json)")


def dump_fabricate_data(out_dir, fab_data):
    """Write fabricate module data to a folder."""
    fab_dir = os.path.join(os.path.normpath(out_dir), "fabricate")
    os.makedirs(fab_dir, exist_ok=True)

    # Full JSON
    with open(os.path.join(fab_dir, "fabricate.json"), "w") as f:
        json.dump(fab_data, f, indent=2)

    # Signal IDs
    if fab_data.get("signal_ids"):
        with open(os.path.join(fab_dir, "signals.txt"), "w") as f:
            for sig in fab_data["signal_ids"]:
                f.write(sig + "\n")

    # Card names
    if fab_data.get("card_names"):
        with open(os.path.join(fab_dir, "card-names.txt"), "w") as f:
            for name in fab_data["card_names"]:
                f.write(name + "\n")

    # Game state
    if fab_data.get("game_state_defaults"):
        with open(os.path.join(fab_dir, "game-state.txt"), "w") as f:
            for flag, slot in fab_data["game_state_defaults"].items():
                f.write(f"{flag}: {slot}\n")

    print(f"  Fabricate -> {fab_dir}/ ({', '.join(fab_data.keys())})")


def extract_module_data(out_dir):
    """Extract structured data from all decrypted modules and save to folders.

    Structured dumps (crossword, media) download their assets directly with
    readable names. Returns a dict mapping asset_path -> local_rel for all
    assets already downloaded, so sync_assets can skip them.
    """
    dec_dir = os.path.join(os.path.normpath(out_dir), "decrypted")
    downloaded = {}  # asset_path -> local_rel_path

    # Terminal module (D5GY78C)
    terminal_path = os.path.join(dec_dir, "D5GY78C", "module.js")
    if os.path.exists(terminal_path):
        with open(terminal_path, "r") as f:
            js = f.read()
        terminal_data = extract_terminal_data(js)
        if terminal_data:
            dump_terminal_data(out_dir, terminal_data)

    # Media module (374468739BD7269A48)
    media_path = os.path.join(dec_dir, "374468739BD7269A48", "module.js")
    if os.path.exists(media_path):
        with open(media_path, "r") as f:
            js = f.read()
        gallery = extract_media_gallery(js)
        if gallery:
            dump_media_gallery(out_dir, gallery, downloaded)

    # Crossword module (XWORD7K)
    xword_path = os.path.join(dec_dir, "XWORD7K", "module.js")
    if os.path.exists(xword_path):
        with open(xword_path, "r") as f:
            js = f.read()
        xword = extract_crossword_data(js)
        if xword:
            dump_crossword_data(out_dir, xword, downloaded)

    # Fabricate module (MTBFAB7)
    fab_path = os.path.join(dec_dir, "MTBFAB7", "module.js")
    if os.path.exists(fab_path):
        with open(fab_path, "r") as f:
            js = f.read()
        fab = extract_fabricate_data(js)
        if fab:
            dump_fabricate_data(out_dir, fab)

    return downloaded


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


def save_payload_archive(all_payloads):
    """Archive decrypted payload metadata to JSON for offline processing."""
    with open(PAYLOADS_JSON, "w") as f:
        json.dump(all_payloads, f, indent=4)
    print(f"Saved payload archive to {PAYLOADS_JSON} ({len(all_payloads)} payloads)")


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

    scrape_launcher_assets(OUT_DIR)

    print("Processing codes and encrypted payloads...")
    all_routes, code_map, all_texts, all_payloads, js_asset_urls = scrape_and_decrypt(codes, OUT_DIR)
    save_route_manifest(all_routes, code_map)
    save_payload_archive(all_payloads)

    print("Extracting decoded text content...")
    dump_extracted_text(OUT_DIR, all_texts)

    # Extract structured module data — crossword/media download their own assets
    print("Extracting structured module data...")
    downloaded = extract_module_data(OUT_DIR)

    # Extract terminal filesystem from D5GY78C module
    terminal_js_path = os.path.join(os.path.normpath(OUT_DIR), "decrypted", "D5GY78C", "module.js")
    if os.path.exists(terminal_js_path):
        print("Extracting terminal filesystem...")
        with open(terminal_js_path, "r") as f:
            terminal_js = f.read()
        fs_tree = extract_terminal_filesystem(terminal_js)
        if fs_tree:
            dump_terminal_filesystem(OUT_DIR, fs_tree)
            fs_dir = os.path.join(os.path.normpath(OUT_DIR), "terminal", "fs")
            print("Generating decoded files...")
            generate_decoded_files(fs_tree, fs_dir)
        else:
            print("  Could not extract filesystem (structure not found)")

    # Sync all /a/ assets — downloads anything not already fetched by structured dumps
    all_asset_urls = set(STATIC_ASSETS) | js_asset_urls
    print("Syncing assets...")
    sync_assets(OUT_DIR, all_asset_urls, downloaded)

    print("Done.")
