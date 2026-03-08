#!/usr/bin/env python3
"""Snapshot cryoarchive.systems public state, assets, and deploy metadata.

Run periodically to track changes. Saves timestamped JSON snapshots and diffs
against the previous run. No dependencies beyond stdlib.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://cryoarchive.systems"
CDN_URL = "https://assets.thecdn.io"
OUT_DIR = "./cryoarchive.systems/.raw/json/"

ROUTES = [
    "/", "/cargo", "/indx", "/index", "/steerage", "/revival",
    "/biostock", "/preservation", "/cryohub", "/example",
    # Speculative
    "/memory", "/archive", "/uesc",
]

# All known CDN asset UUIDs (videos + backgrounds + landing)
KNOWN_CDN_ASSETS = {
    # Camera videos: {camera}_{state}
    "158967d7-59ac-4871-b8e0-7fc683e982ca.mp4": "cargo/unstable",
    "6a5235a5-0e07-469d-8e0f-c664c154c1ba.mp4": "cargo/stable",
    "a2e4633e-47e8-48e5-9393-60668f7780a1.mp4": "cargo/glitch",
    "b45b183e-b255-48cf-b888-257a62fb46bd.mp4": "index/unstable",
    "02d34536-1155-41d2-9f05-9e7087a0fc7f.mp4": "index/stable",
    "da9fb869-e197-4c88-a1f9-b9c85d27ec51.mp4": "index/glitch",
    "217f7544-4806-4441-8f7a-8e3db39a5266.mp4": "revival/unstable",
    "a633f52e-c79c-4320-b2d5-16ded608143b.mp4": "revival/stable",
    "f128c76c-dafb-4a93-ae3a-85882540eeab.mp4": "revival/glitch",
    "5a55954c-e776-4bc4-a6ca-edf18666071d.mp4": "biostock/unstable",
    "a0f9fc28-dc29-46c3-94c2-641003fd99e4.mp4": "biostock/stable",
    "10701f62-d7ab-4e4a-b4c3-7e50269a1727.mp4": "biostock/glitch",
    "855ab978-dd9f-4bfd-a6e1-3f16c9357384.mp4": "steerage/unstable",
    "d63c42ce-f626-4c49-84e5-e9724bfb3e6d.mp4": "steerage/stable",
    "883646ff-fbe4-4e9b-8759-818bf80220eb.mp4": "steerage/glitch",
    "7aebbab5-cb5a-46c6-babb-3ff7505fbcdd.mp4": "preservation/unstable",
    "32a8fe9e-00a2-4dbf-a19e-38c51b3dd8df.mp4": "preservation/stable",
    "cc7bc850-ee98-4883-b3a4-f0a877b5492a.mp4": "preservation/glitch",
    "f86f4c9a-6818-4d56-8a3e-cf95038ef7d2.mp4": "cryohub/unstable",
    "3032d56e-75dd-4974-b634-20e3478d343c.mp4": "cryohub/stable",
    "04869cfd-65c7-403f-9f6f-5cfd9ded85fe.mp4": "cryohub/glitch",
    "a9b3e8f3-5867-4cb4-a1b2-531765cc71d8.mp4": "camera06/unstable",
    "61306bbd-6339-4d8f-ab99-330210d7c006.mp4": "camera06/stable",
    "ce430770-9cdc-4adf-a3c4-f09512d06abc.mp4": "camera06/glitch",
    "30e998c5-c0b6-4b28-b58b-da9bfac15692.mp4": "camera09/unstable",
    "8c466925-d96e-4d6f-8e81-3d32b970f83d.mp4": "camera09/stable",
    "03741510-1c4e-4160-b56d-027f5ca2843a.mp4": "camera09/glitch",
    # Backgrounds
    "ce621aa9-d63b-4432-9411-c45a880c3288.png": "cargo/background",
    "5742b3a1-ff6b-4bc6-93df-70b990bfc0d4.png": "index/background",
    "1532f938-a803-497f-81ae-fe1f29e6c757.png": "revival/background",
    "89f9849a-3684-4dc7-beba-709b83d40e84.png": "biostock/background",
    "098e5a48-2cc6-44bb-a601-d0b569032ea1.png": "steerage/background",
    "a9c41cc3-8185-43f6-b827-34af221eff20.png": "preservation/background",
    "e16a58b3-a50f-4556-b1f4-124d916eeac9.png": "cryohub/background",
    # Landing video
    "f5d8b1e5-d89a-4fb8-b1cd-2681c4d81a23.mp4": "landing/background",
}

# Static assets on site origin (not CDN)
KNOWN_STATIC_ASSETS = [
    "/artifacts.png",
    "/error/error-fallback.png",
    "/error/unknown.png",
    "/icon.svg",
    "/cursors/decryptor-cable-cursor.svg",
    "/splats/012226_biostock_splats.spz",
]

PHANTOM_UUIDS = [
    "a6ce4d77-f881-49fe-b06d-9bf82392f64f",
    "6487caee-379c-43b8-8822-96af959f6d0a",
    "18589bde-2597-4159-8fa8-d6b2a2a31161",
    "803426eb-2c62-4f4d-a259-11e44869688d",
]

# Fields that always change — still recorded but suppressed from diff alerts
VOLATILE_PATHS = {
    "api.public_state.headers",
    "api.stabilization.headers",
    "api.decryptor_state.headers",
    "api.public_state.body.state.uescKillCount",
    "api.public_state.body.state.uescKillCountNextUpdateAt",
    "api.stabilization.body",
    "meta",
    "diff",
    "state_summary.kill_count",
    "state_summary.stabilization_levels",
    "state_summary.next_stabilization_at",
}

# Per-route fields that change every request
VOLATILE_ROUTE_FIELDS = {"headers", "rsc_payload_hashes", "rsc_payload_count"}


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_with_meta(url, method="GET"):
    """Fetch URL, return (status, headers_dict, body_str). Never raises."""
    req = urllib.request.Request(url, method=method, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            body = resp.read().decode("utf-8") if method != "HEAD" else ""
            return resp.status, hdrs, body
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        return e.code, hdrs, body
    except Exception as e:
        return 0, {}, str(e)


def head_asset(url):
    """HEAD request, return dict of useful headers + status."""
    req = urllib.request.Request(url, method="HEAD", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {
                "status": resp.status,
                "content_type": resp.getheader("Content-Type"),
                "content_length": resp.getheader("Content-Length"),
                "last_modified": resp.getheader("Last-Modified"),
                "etag": resp.getheader("ETag"),
            }
    except urllib.error.HTTPError as e:
        return {"status": e.code}
    except Exception as e:
        return {"status": 0, "error": str(e)}


def pick_headers(hdrs):
    """Extract interesting response headers."""
    keys = ["x-vercel-id", "x-vercel-cache", "cache-control",
            "x-matched-path", "content-type"]
    return {k: hdrs[k] for k in keys if k in hdrs}


# ---------------------------------------------------------------------------
# RSC extraction
# ---------------------------------------------------------------------------

def extract_rsc_payloads(html):
    payloads = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL):
        try:
            payloads.append(m.group(1).encode().decode("unicode_escape"))
        except (UnicodeDecodeError, ValueError):
            pass
    return payloads


def extract_asset_objects(payloads):
    """Extract all asset records from RSC payloads — full objects, not just alt_filenames."""
    assets = {}
    # Match any JSON-ish object containing a thecdn.io URL or UUID filename
    uuid_pat = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    for p in payloads:
        # Find all filename fields
        for m in re.finditer(rf'"filename"\s*:\s*"({uuid_pat}\.[a-z0-9]+)"', p):
            fname = m.group(1)
            if fname in assets:
                continue
            # Extract surrounding object fields
            obj = {"filename": fname}
            # Look around this match for sibling fields
            start = max(0, m.start() - 500)
            end = min(len(p), m.end() + 500)
            ctx = p[start:end]
            for field in ["alt_filename", "alt", "url", "mimeType"]:
                fm = re.search(rf'"{field}"\s*:\s*"([^"]*)"', ctx)
                if fm:
                    obj[field] = fm.group(1)
            for field in ["width", "height"]:
                fm = re.search(rf'"{field}"\s*:\s*(\d+)', ctx)
                if fm:
                    obj[field] = int(fm.group(1))
            assets[fname] = obj
    return assets


def extract_initial_state(payloads):
    for p in payloads:
        if '"initialState"' not in p:
            continue
        try:
            start = p.index('"initialState":') + len('"initialState":')
            depth = 0
            i = start
            while i < len(p):
                if p[i] == '{':
                    depth += 1
                elif p[i] == '}':
                    depth -= 1
                    if depth == 0:
                        return json.loads(p[start:i+1].replace('"$D', '"'))
                i += 1
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def extract_route_segments(payloads):
    for p in payloads:
        m = re.search(r'"c"\s*:\s*(\[[^\]]*\])', p)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    return None


def extract_chunks(html):
    """JS and CSS chunk hashes from HTML."""
    js = sorted(set(re.findall(r'/_next/static/chunks/([0-9a-f]+)\.js', html)))
    css = sorted(set(re.findall(r'/_next/static/chunks/([0-9a-f]+\.css)', html)))
    return js, css


def extract_deploy_id(html):
    m = re.search(r'dpl=(dpl_[A-Za-z0-9]+)', html)
    return m.group(1) if m else None


def extract_fonts(html):
    return sorted(set(re.findall(r'/_next/static/media/([^\s"\']+\.(?:woff2|otf|ttf))', html)))


def payload_hashes(payloads):
    """SHA-256 of each decoded RSC payload for cheap change detection."""
    return [hashlib.sha256(p.encode()).hexdigest()[:16] for p in payloads]


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def flatten(obj, prefix=""):
    """Flatten nested dict to dot-path keys."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}{k}.") if prefix else flatten(v, f"{k}."))
    elif isinstance(obj, list):
        out[prefix.rstrip(".")] = obj
    else:
        out[prefix.rstrip(".")] = obj
    return out


def is_volatile(path):
    for vp in VOLATILE_PATHS:
        if path == vp or path.startswith(vp + "."):
            return True
    # Per-route volatile fields (headers, rsc hashes change every request)
    if path.startswith("routes."):
        parts = path.split(".", 2)
        if len(parts) >= 3 and parts[2].split(".")[0] in VOLATILE_ROUTE_FIELDS:
            return True
    return False


def compute_diff(old, new):
    """Structured diff between two snapshots. Returns significant changes only."""
    old_flat = flatten(old)
    new_flat = flatten(new)
    all_keys = set(old_flat) | set(new_flat)

    changes = {}
    for k in sorted(all_keys):
        if is_volatile(k):
            continue
        ov = old_flat.get(k)
        nv = new_flat.get(k)
        if ov != nv:
            changes[k] = {"old": ov, "new": nv}

    # Pull out kill count delta separately (always interesting even though volatile)
    old_kc = old.get("api", {}).get("public_state", {}).get("body", {}).get("state", {}).get("uescKillCount")
    new_kc = new.get("api", {}).get("public_state", {}).get("body", {}).get("state", {}).get("uescKillCount")
    kill_delta = None
    if old_kc is not None and new_kc is not None:
        kill_delta = new_kc - old_kc

    return {
        "previous_timestamp": old.get("meta", {}).get("timestamp"),
        "kill_count_delta": kill_delta,
        "significant_changes": changes,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    errors = []
    snapshot = {"meta": {"timestamp": ts, "script_version": "2.0"}}

    # --- Phase 1: Public APIs ---
    print("APIs...")
    api = {}
    for name, path in [("public_state", "/api/public/state"),
                        ("stabilization", "/api/public/cctv-cameras/stabilization"),
                        ("decryptor_state", "/api/decryptor/state")]:
        status, hdrs, body = fetch_with_meta(f"{BASE_URL}{path}")
        entry = {"status": status, "headers": pick_headers(hdrs)}
        try:
            entry["body"] = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            entry["body"] = body[:500] if body else None
            if status == 0:
                errors.append({"phase": "api", "url": path, "error": body})
        api[name] = entry
        print(f"  {path} -> {status}")
    snapshot["api"] = api

    # --- Phase 2: Routes + RSC ---
    print("Routes...")
    routes = {}
    all_payloads_by_route = {}
    all_html = {}
    deploy_ids = set()

    for route in ROUTES:
        slug = route if route != "/" else "/"
        status, hdrs, body = fetch_with_meta(f"{BASE_URL}{route}")
        entry = {"status": status, "headers": pick_headers(hdrs)}

        if status == 200 and body:
            payloads = extract_rsc_payloads(body)
            all_payloads_by_route[slug] = payloads
            all_html[slug] = body
            js_chunks, css_chunks = extract_chunks(body)
            entry["segments"] = extract_route_segments(payloads)
            entry["js_chunks"] = js_chunks
            entry["css_chunks"] = css_chunks
            entry["fonts"] = extract_fonts(body)
            entry["rsc_payload_hashes"] = payload_hashes(payloads)
            entry["rsc_payload_count"] = len(payloads)
            dpl = extract_deploy_id(body)
            if dpl:
                deploy_ids.add(dpl)
                entry["deploy_id"] = dpl
        else:
            entry["segments"] = None

        routes[slug] = entry
        seg_str = entry.get("segments") or entry["status"]
        print(f"  {route} -> {seg_str}")
        time.sleep(0.3)

    snapshot["routes"] = routes

    # --- Phase 3: Deploy metadata ---
    snapshot["deploy"] = {
        "deploy_ids": sorted(deploy_ids),
        "uniform": len(deploy_ids) <= 1,
    }

    # --- Phase 4: RSC asset extraction ---
    print("Assets...")
    all_payloads = []
    for payloads in all_payloads_by_route.values():
        all_payloads.extend(payloads)

    discovered = extract_asset_objects(all_payloads)
    initial_state = extract_initial_state(all_payloads)

    # Tag which routes each asset appears on
    for slug, payloads in all_payloads_by_route.items():
        route_assets = extract_asset_objects(payloads)
        for fname in route_assets:
            if fname in discovered:
                discovered[fname].setdefault("found_on", [])
                if slug not in discovered[fname]["found_on"]:
                    discovered[fname]["found_on"].append(slug)

    snapshot["assets"] = {"discovered": discovered}
    if initial_state:
        snapshot["initial_state"] = initial_state

    # --- Phase 5: Asset health (HEAD checks) ---
    print("Asset health...")
    health = {}

    # CDN assets
    for fname in sorted(KNOWN_CDN_ASSETS):
        url = f"{CDN_URL}/{fname}"
        health[url] = head_asset(url)
        time.sleep(0.15)

    # Any newly discovered CDN assets not in the known list
    for fname, obj in discovered.items():
        url = obj.get("url") or f"{CDN_URL}/{fname}"
        if url not in health:
            health[url] = head_asset(url)
            time.sleep(0.15)

    # Static origin assets (just check a few key ones, skip large splat)
    for path in ["/artifacts.png", "/error/error-fallback.png",
                 "/error/unknown.png", "/icon.svg"]:
        url = f"{BASE_URL}{path}"
        health[url] = head_asset(url)

    # Phantoms
    phantom_status = {}
    for uuid in PHANTOM_UUIDS:
        url = f"{CDN_URL}/{uuid}.mp4"
        result = head_asset(url)
        phantom_status[uuid] = result
        health[url] = result
        status_str = "LIVE" if result["status"] == 200 else str(result["status"])
        print(f"  phantom {uuid[:8]}... -> {status_str}")

    snapshot["assets"]["health"] = health
    snapshot["assets"]["phantoms"] = phantom_status

    healthy = sum(1 for v in health.values() if v.get("status") == 200)
    print(f"  {healthy}/{len(health)} assets healthy")

    # --- Phase 6: Summary ---
    state_body = api.get("public_state", {}).get("body", {})
    state = state_body.get("state", state_body)
    pages = state.get("pages", {})

    summary = {
        "kill_count": state.get("uescKillCount"),
        "memory_unlocked": state.get("memoryUnlocked"),
        "ship_date": state.get("shipDate"),
        "pages_unlocked": sorted(k for k, v in pages.items() if v.get("unlocked")),
        "pages_completed": sorted(k for k, v in pages.items() if v.get("completed")),
        "pages_locked": sorted(k for k, v in pages.items()
                               if not v.get("unlocked") and not v.get("completed")),
        "total_assets_discovered": len(discovered),
        "assets_healthy": healthy,
        "assets_total": len(health),
        "phantoms_live": sum(1 for v in phantom_status.values() if v.get("status") == 200),
        "deploy_id": sorted(deploy_ids)[0] if deploy_ids else None,
        "example_route_status": routes.get("/example", {}).get("status"),
    }

    # Stabilization levels
    stab_body = api.get("stabilization", {}).get("body", {})
    if isinstance(stab_body, dict):
        levels = {}
        for cam_id, cam_data in stab_body.items():
            if isinstance(cam_data, dict) and "stabilizationLevel" in cam_data:
                levels[cam_id] = cam_data["stabilizationLevel"]
        if levels:
            summary["stabilization_levels"] = levels
            next_at = None
            for cam_data in stab_body.values():
                if isinstance(cam_data, dict):
                    next_at = cam_data.get("nextStabilizationAt", next_at)
            if next_at:
                summary["next_stabilization_at"] = next_at

    snapshot["state_summary"] = summary

    # --- Phase 7: Diff ---
    latest_path = f"{OUT_DIR}snapshot_latest.json"
    diff = None
    if os.path.exists(latest_path):
        try:
            with open(latest_path) as f:
                old = json.load(f)
            diff = compute_diff(old, snapshot)
        except (json.JSONDecodeError, OSError):
            pass
    snapshot["diff"] = diff

    # --- Finalize ---
    elapsed = int((time.monotonic() - t0) * 1000)
    snapshot["meta"]["elapsed_ms"] = elapsed
    snapshot["meta"]["errors"] = errors

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = f"{OUT_DIR}snapshot_{now.strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
    with open(latest_path, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)

    if diff and diff.get("significant_changes"):
        diff_path = f"{OUT_DIR}snapshot_diff.json"
        with open(diff_path, "w") as f:
            json.dump(diff, f, indent=2, ensure_ascii=False, default=str)

    # --- Print summary ---
    print(f"\n{'=' * 60}")
    kc = summary["kill_count"]
    print(f"Timestamp:     {ts}")
    print(f"Kill count:    {kc:,}" if kc else "Kill count:    ?")
    if diff and diff.get("kill_count_delta") is not None:
        print(f"  delta:       +{diff['kill_count_delta']:,} since last")
    print(f"Memory:        {summary['memory_unlocked']}")
    print(f"Pages:         {', '.join(summary['pages_unlocked']) or 'none'} unlocked"
          f" | {len(summary['pages_locked'])} locked")
    if summary.get("stabilization_levels"):
        nonzero = {k: v for k, v in summary["stabilization_levels"].items() if v > 0}
        if nonzero:
            parts = [f"{k}={v}" for k, v in sorted(nonzero.items())]
            print(f"Stabilization: {' '.join(parts)}")
    print(f"Phantoms:      {summary['phantoms_live']}/{len(PHANTOM_UUIDS)} live")
    print(f"Assets:        {summary['assets_healthy']}/{summary['assets_total']} healthy"
          f", {summary['total_assets_discovered']} in RSC")
    print(f"Deploy:        {summary['deploy_id'] or '?'}")
    print(f"Example:       {summary['example_route_status']}")
    print(f"Elapsed:       {elapsed}ms")
    print(f"Saved:         {out_path}")

    if diff and diff.get("significant_changes"):
        print(f"\n--- SIGNIFICANT CHANGES ({len(diff['significant_changes'])}) ---")
        for path, change in sorted(diff["significant_changes"].items()):
            print(f"  {path}: {change['old']} -> {change['new']}")
    elif diff:
        print("\n--- no significant changes ---")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
