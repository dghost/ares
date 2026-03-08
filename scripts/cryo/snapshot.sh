#!/usr/bin/env bash
set -euo pipefail

# Snapshot cryoarchive.systems — detect new deployments and archive them.
#
# Usage: ./snapshot.sh [--force] [--check]
#   --force  Re-snapshot even if deploy ID hasn't changed
#   --check  Quick status check only (API state, stabilization, deploy ID). No downloads.
#
# Requires: curl, rg (ripgrep), js-beautify, python3, git
# Expects decompose.py in the same directory as this script.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/../../cryoarchive.systems/deploys"
DECOMPOSE="$SCRIPT_DIR/decompose.py"
SITE="https://cryoarchive.systems"
PAGES=("" "cargo" "indx" "steerage" "revival" "biostock" "preservation" "cryohub" "example")

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --check) CHECK_ONLY=1 ;;
    esac
done

# --- Preflight checks ---
for cmd in curl rg js-beautify python3 git; do
    command -v "$cmd" >/dev/null || { echo "ERROR: $cmd not found"; exit 1; }
done
[[ -f "$DECOMPOSE" ]] || { echo "ERROR: decompose.py not found at $DECOMPOSE"; exit 1; }
[[ -d "$DEPLOY_DIR/.git" ]] || { echo "ERROR: $DEPLOY_DIR is not a git repo"; exit 1; }

# --- Step 1: Detect current deploy ---
echo "Fetching $SITE..."
LANDING_HTML=$(curl -sL "$SITE/")
DEPLOY_ID=$(echo "$LANDING_HTML" | rg -o 'dpl=[^"&]+' -r '$0' | head -1 | sed 's/dpl=//')
TURBOPACK=$(echo "$LANDING_HTML" | rg -o 'turbopack-([a-f0-9]+)\.js' -r '$1' | head -1)

if [[ -z "$DEPLOY_ID" ]]; then
    echo "ERROR: Could not extract deploy ID from $SITE"
    exit 1
fi

# Extract deploy timestamp from turbopack runtime Last-Modified header.
# This is the most reliable build timestamp — it always changes between deploys,
# and Vercel sets Last-Modified to the time the asset was first uploaded.
TURBOPACK_FILE="turbopack-${TURBOPACK}.js"
DEPLOY_TIME=$(curl -sI "$SITE/_next/static/chunks/${TURBOPACK_FILE}?dpl=${DEPLOY_ID}" | grep -i last-modified | sed 's/[Ll]ast-[Mm]odified: //' | tr -d '\r')

# Also grab per-chunk Last-Modified for all new chunks (stored in manifest for forensics)
echo "Live deploy: $DEPLOY_ID (turbopack: $TURBOPACK)"
echo "  Deployed at: $DEPLOY_TIME"

# --- Quick check mode ---
if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo ""
    echo "=== API State ==="
    API_STATE=$(curl -sL "$SITE/api/public/state")
    echo "$API_STATE" | python3 -c "
import sys, json
s = json.load(sys.stdin)['state']
print(f'  Kill count:       {s[\"uescKillCount\"]:,}')
print(f'  memoryUnlocked:   {s[\"memoryUnlocked\"]}')
print(f'  memoryCompleted:  {s.get(\"memoryCompleted\", \"N/A\")}')
pages = s['pages']
for name, p in pages.items():
    status = 'COMPLETED' if p['completed'] else ('UNLOCKED' if p['unlocked'] else 'locked')
    print(f'  {name:<15} {status}')
"

    echo ""
    echo "=== Camera Stabilization ==="
    STAB_STATE=$(curl -sL "$SITE/api/public/cctv-cameras/stabilization")
    echo "$STAB_STATE" | python3 -c "
import sys, json
from datetime import datetime, timezone
stab = json.load(sys.stdin)['stabilization']
now = datetime.now(timezone.utc)
for cam in ('cargo','index','steerage','revival','biostock','preservation','cryoHub','camera06','camera09'):
    if cam in stab:
        level = stab[cam]['stabilizationLevel']
        next_at = stab[cam]['nextStabilizationAt']
        bar = '#' * level + '.' * (100 - level)
        dt = datetime.fromisoformat(next_at.replace('Z','+00:00'))
        delta = dt - now
        mins = int(delta.total_seconds() / 60)
        sign = '+' if mins >= 0 else ''
        print(f'  {cam:<15} {level:>3}/100  [{bar[:20]}]  next: {next_at} ({sign}{mins}m)')
"

    echo ""
    # Check if this deploy is already archived
    cd "$DEPLOY_DIR"
    BRANCH="deploy/$DEPLOY_ID"
    if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
        echo "Deploy already archived."

        # Compare stabilization with what's saved
        SAVED_STAB_FILE=$(mktemp)
        LIVE_STAB_FILE=$(mktemp)
        git show "$BRANCH":api/stabilization.json > "$SAVED_STAB_FILE" 2>/dev/null || echo "{}" > "$SAVED_STAB_FILE"
        echo "$STAB_STATE" > "$LIVE_STAB_FILE"
        if [[ -s "$SAVED_STAB_FILE" ]]; then
            echo ""
            echo "=== Stabilization Delta (vs last snapshot) ==="
            SAVED_STAB="$SAVED_STAB_FILE" LIVE_STAB="$LIVE_STAB_FILE" python3 << 'PYEOF'
import os, json

saved = json.load(open(os.environ["SAVED_STAB"])).get("stabilization", {})
live = json.load(open(os.environ["LIVE_STAB"])).get("stabilization", {})
changed = False
for cam in ("cargo","index","steerage","revival","biostock","preservation","cryoHub","camera06","camera09"):
    old = saved.get(cam, {}).get("stabilizationLevel", "?")
    new = live.get(cam, {}).get("stabilizationLevel", "?")
    if old != new:
        print(f"  {cam:<15} {old} -> {new}")
        changed = True
if not changed:
    print("  (no changes)")
PYEOF
        fi
        rm -f "$SAVED_STAB_FILE" "$LIVE_STAB_FILE"
    else
        echo "*** NEW DEPLOY — not yet archived. Run without --check to snapshot. ***"
    fi
    exit 0
fi

# --- Step 2: Check if already archived ---
BRANCH="deploy/$DEPLOY_ID"
cd "$DEPLOY_DIR"

if git show-ref --verify --quiet "refs/heads/$BRANCH" && [[ "$FORCE" -eq 0 ]]; then
    echo "Deploy $DEPLOY_ID already archived on branch $BRANCH. Use --force to re-snapshot."
    exit 0
fi

# --- Step 3: Collect all chunk filenames across all pages ---
echo "Scanning all pages for chunk references..."
CHUNK_LIST=$(mktemp)
for page in "${PAGES[@]}"; do
    curl -sL "$SITE/$page" | rg -o 'src="/_next/static/chunks/([^"?]+)' -r '$1'
done | sort -u > "$CHUNK_LIST"

CHUNK_COUNT=$(wc -l < "$CHUNK_LIST" | tr -d ' ')
echo "  Found $CHUNK_COUNT unique chunks"

# Also collect CSS
CSS_LIST=$(mktemp)
for page in "${PAGES[@]}"; do
    curl -sL "$SITE/$page" | rg -o 'href="/_next/static/chunks/([^"?]+\.css)' -r '$1'
done | sort -u > "$CSS_LIST"

# --- Step 4: Determine base branch ---
# Find the most recent deploy branch to branch from
LATEST_BRANCH=$(git branch --list 'deploy/dpl_*' --sort=-committerdate | head -1 | tr -d ' *')
if [[ -n "$LATEST_BRANCH" ]]; then
    echo "Branching from $LATEST_BRANCH"
    git checkout "$LATEST_BRANCH" 2>/dev/null
    # Get previous chunk list for diffing
    PREV_CHUNKS=$(mktemp)
    ls chunks/*.js 2>/dev/null | xargs -I{} basename {} | sort > "$PREV_CHUNKS"
else
    echo "No previous deploy branch found, starting fresh"
    PREV_CHUNKS=$(mktemp)
    touch "$PREV_CHUNKS"
fi

# Create or reset the branch
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git checkout "$BRANCH"
else
    git checkout -b "$BRANCH"
fi

# --- Step 5: Download chunks ---
echo "Downloading chunks..."

# Remove chunks no longer present
REMOVED=$(comm -23 "$PREV_CHUNKS" "$CHUNK_LIST" || true)
if [[ -n "$REMOVED" ]]; then
    echo "  Removing old chunks:"
    while IFS= read -r old; do
        [[ -n "$old" ]] && echo "    - $old" && rm -f "chunks/$old"
    done <<< "$REMOVED"
fi

# Download new/changed chunks, collecting Last-Modified for all
NEW_CHUNKS=$(comm -13 "$PREV_CHUNKS" "$CHUNK_LIST" || true)
EXISTING_CHUNKS=$(comm -12 "$PREV_CHUNKS" "$CHUNK_LIST" || true)

# File to accumulate chunk timestamps: "filename\tLast-Modified"
CHUNK_TIMES=$(mktemp)

if [[ -n "$NEW_CHUNKS" ]]; then
    echo "  Downloading new chunks:"
    while IFS= read -r chunk; do
        [[ -z "$chunk" ]] && continue
        # Download with headers saved to extract Last-Modified
        HEADERS=$(mktemp)
        curl -sL -D "$HEADERS" "$SITE/_next/static/chunks/${chunk}?dpl=${DEPLOY_ID}" -o "chunks/$chunk"
        SIZE=$(wc -c < "chunks/$chunk" | tr -d ' ')
        LM=$(grep -i last-modified "$HEADERS" | sed 's/[Ll]ast-[Mm]odified: //' | tr -d '\r')
        echo "    + $chunk ($SIZE bytes, $LM)"
        echo -e "$chunk\t$LM" >> "$CHUNK_TIMES"
        rm -f "$HEADERS"
    done <<< "$NEW_CHUNKS"
fi

# Verify existing chunks haven't changed content, collect their timestamps too
echo "  Verifying existing chunks..."
CONTENT_CHANGED=0
while IFS= read -r chunk; do
    [[ -z "$chunk" ]] && continue
    TMPFILE=$(mktemp)
    HEADERS=$(mktemp)
    curl -sL -D "$HEADERS" "$SITE/_next/static/chunks/${chunk}?dpl=${DEPLOY_ID}" -o "$TMPFILE"
    LM=$(grep -i last-modified "$HEADERS" | sed 's/[Ll]ast-[Mm]odified: //' | tr -d '\r')
    echo -e "$chunk\t$LM" >> "$CHUNK_TIMES"
    if ! diff -q "chunks/$chunk" "$TMPFILE" > /dev/null 2>&1; then
        SIZE_OLD=$(wc -c < "chunks/$chunk" | tr -d ' ')
        SIZE_NEW=$(wc -c < "$TMPFILE" | tr -d ' ')
        echo "    ~ $chunk content changed ($SIZE_OLD -> $SIZE_NEW bytes)"
        cp "$TMPFILE" "chunks/$chunk"
        CONTENT_CHANGED=$((CONTENT_CHANGED + 1))
    fi
    rm -f "$TMPFILE" "$HEADERS"
done <<< "$EXISTING_CHUNKS"
echo "  $CONTENT_CHANGED existing chunks had content changes"

# --- Step 6: Download CSS ---
echo "Downloading CSS..."
rm -f css/*.css
while IFS= read -r cssfile; do
    [[ -z "$cssfile" ]] && continue
    curl -sL "$SITE/_next/static/chunks/${cssfile}?dpl=${DEPLOY_ID}" -o "css/$cssfile"
    SIZE=$(wc -c < "css/$cssfile" | tr -d ' ')
    echo "  $cssfile ($SIZE bytes)"
done < "$CSS_LIST"

# --- Step 7: Download HTML pages ---
echo "Downloading HTML pages..."
for page in "${PAGES[@]}"; do
    fname="${page:-index_landing}.html"
    curl -sL "$SITE/$page" -o "html/$fname"
    SIZE=$(wc -c < "html/$fname" | tr -d ' ')
    echo "  $fname ($SIZE bytes)"
done

# --- Step 8: Grab API state and asset headers ---
echo "Fetching API state..."
mkdir -p api
API_STATE=$(curl -sL "$SITE/api/public/state")
STAB_STATE=$(curl -sL "$SITE/api/public/cctv-cameras/stabilization")
echo "$API_STATE" > api/state.json
echo "$STAB_STATE" > api/stabilization.json

KILL_COUNT=$(echo "$API_STATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['state']['uescKillCount'])" 2>/dev/null || echo "unknown")
MEMORY_UNLOCKED=$(echo "$API_STATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['state']['memoryUnlocked'])" 2>/dev/null || echo "unknown")
MEMORY_COMPLETED=$(echo "$API_STATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['state'].get('memoryCompleted', 'N/A'))" 2>/dev/null || echo "unknown")
STAB_SUMMARY=$(echo "$STAB_STATE" | python3 -c "
import sys, json
stab = json.load(sys.stdin)['stabilization']
parts = []
for cam in ('cargo','index','steerage','revival','biostock','preservation','cryoHub','camera06','camera09'):
    if cam in stab:
        parts.append(f'{cam}={stab[cam][\"stabilizationLevel\"]}')
print(' '.join(parts))
" 2>/dev/null || echo "unknown")
echo "  Kill count: $KILL_COUNT"
echo "  memoryUnlocked: $MEMORY_UNLOCKED"
echo "  memoryCompleted: $MEMORY_COMPLETED"
echo "  Stabilization: $STAB_SUMMARY"

# Check known asset URLs for changes (Last-Modified / ETag)
echo "Checking asset headers..."
ASSET_HEADERS=$(mktemp)
# Background video, page backgrounds, camera videos, static assets
KNOWN_ASSETS=(
    "https://assets.thecdn.io/f5d8b1e5-d89a-4fb8-b1cd-2681c4d81a23.mp4"
    "$SITE/artifacts.png"
    "$SITE/icon.svg"
    "$SITE/error/error-fallback.png"
    "$SITE/error/unknown.png"
)
for asset_url in "${KNOWN_ASSETS[@]}"; do
    HDRS=$(curl -sI "$asset_url" 2>/dev/null)
    LM=$(echo "$HDRS" | grep -i last-modified | tr -d '\r')
    ETAG=$(echo "$HDRS" | grep -i etag | tr -d '\r')
    CL=$(echo "$HDRS" | grep -i content-length | tr -d '\r')
    echo "$asset_url|$LM|$ETAG|$CL" >> "$ASSET_HEADERS"
done
echo "  $(wc -l < "$ASSET_HEADERS" | tr -d ' ') assets checked"

# --- Step 9: Prettify and decompose ---
echo "Prettifying chunks..."
PRETTY_DIR=$(mktemp -d)
for f in chunks/*.js; do
    js-beautify "$f" > "$PRETTY_DIR/$(basename "$f")"
done

echo "Decomposing modules..."
rm -rf modules/*
python3 "$DECOMPOSE" --input "$PRETTY_DIR" --output modules/ --html html/
rm -rf "$PRETTY_DIR"

MODULE_COUNT=$(ls modules/*.js 2>/dev/null | wc -l | tr -d ' ')

# --- Step 10: Write manifest ---
SNAPSHOT_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CHUNK_TIMES_PATH="$CHUNK_TIMES" \
ASSET_HEADERS_PATH="$ASSET_HEADERS" \
DEPLOY_ID="$DEPLOY_ID" \
TURBOPACK="$TURBOPACK" \
DEPLOY_TIME="$DEPLOY_TIME" \
SNAPSHOT_TIME="$SNAPSHOT_TIME" \
MODULE_COUNT="$MODULE_COUNT" \
KILL_COUNT="$KILL_COUNT" \
MEMORY_UNLOCKED="$MEMORY_UNLOCKED" \
MEMORY_COMPLETED="$MEMORY_COMPLETED" \
STAB_SUMMARY="$STAB_SUMMARY" \
python3 << 'PYEOF'
import json, os

# Parse chunk timestamps
chunk_timestamps = {}
ct_path = os.environ.get("CHUNK_TIMES_PATH", "")
if ct_path and os.path.exists(ct_path):
    with open(ct_path) as f:
        for line in f:
            line = line.strip()
            if '\t' in line:
                name, ts = line.split('\t', 1)
                chunk_timestamps[name] = ts

# Parse asset headers
asset_headers = {}
ah_path = os.environ.get("ASSET_HEADERS_PATH", "")
if ah_path and os.path.exists(ah_path):
    with open(ah_path) as f:
        for line in f:
            line = line.strip()
            parts = line.split('|')
            if len(parts) >= 4:
                url = parts[0]
                lm = parts[1].split(': ', 1)[-1] if ': ' in parts[1] else ''
                etag = parts[2].split(': ', 1)[-1] if ': ' in parts[2] else ''
                cl = parts[3].split(': ', 1)[-1] if ': ' in parts[3] else ''
                entry = {}
                if lm: entry['last_modified'] = lm
                if etag: entry['etag'] = etag
                if cl: entry['content_length'] = cl
                if entry: asset_headers[url] = entry

kill_count_str = os.environ["KILL_COUNT"]
mem_unlocked = os.environ["MEMORY_UNLOCKED"]
mem_completed = os.environ["MEMORY_COMPLETED"]

manifest = {
    'deploy_id': os.environ["DEPLOY_ID"],
    'turbopack_hash': os.environ["TURBOPACK"],
    'deploy_time': os.environ["DEPLOY_TIME"],
    'snapshot_time': os.environ["SNAPSHOT_TIME"],
    'chunks': sorted(os.listdir('chunks')),
    'chunk_count': len(os.listdir('chunks')),
    'chunk_timestamps': chunk_timestamps,
    'module_count': int(os.environ["MODULE_COUNT"]),
    'css': sorted(os.listdir('css')),
    'html': sorted(os.listdir('html')),
    'kill_count': int(kill_count_str) if kill_count_str != 'unknown' else None,
    'memory_unlocked': mem_unlocked == 'True',
    'memory_completed': mem_completed == 'True' if mem_completed not in ('N/A', 'unknown') else None,
    'stabilization': os.environ.get("STAB_SUMMARY", ""),
    'asset_headers': asset_headers,
}
json.dump(manifest, open('manifest.json', 'w'), indent=2)
print(json.dumps({k: v for k, v in manifest.items() if k not in ('chunks', 'chunk_timestamps', 'asset_headers')}, indent=2))
PYEOF

# --- Step 11: Commit ---
echo "Committing..."
git add -A
git commit -m "$(cat <<COMMITEOF
Snapshot $DEPLOY_ID

Deployed: $DEPLOY_TIME
Snapshot: $SNAPSHOT_TIME
Turbopack: $TURBOPACK
Modules: $MODULE_COUNT, Kill count: $KILL_COUNT
memoryUnlocked: $MEMORY_UNLOCKED, memoryCompleted: $MEMORY_COMPLETED
Stabilization: $STAB_SUMMARY

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
COMMITEOF
)"

# --- Cleanup ---
rm -f "$CHUNK_LIST" "$CSS_LIST" "$PREV_CHUNKS" "$CHUNK_TIMES" "$ASSET_HEADERS"

echo ""
echo "============================================================"
echo "  Deploy $DEPLOY_ID archived on branch $BRANCH"
echo "  Deployed: $DEPLOY_TIME"
echo "  $MODULE_COUNT modules, kill count $KILL_COUNT"
echo "============================================================"
