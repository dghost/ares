#!/usr/bin/env python3
"""Map cryoarchive.systems modules to rooms/pages.

Reads the decomposed module index and HTML pages to determine which modules
belong to which room. Produces a per-room breakdown and optionally organizes
modules into a semantic directory structure.

Usage:
  python3 map_rooms.py --modules <modules_dir> --html <html_dir> [--output <output.json>]
  python3 map_rooms.py --modules <modules_dir> --html <html_dir> --organize <organized_dir>
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict


# All known pages and their HTML filenames
PAGES = {
    "index_landing": "index_landing.html",
    "cargo": "cargo.html",
    "indx": "indx.html",
    "steerage": "steerage.html",
    "revival": "revival.html",
    "biostock": "biostock.html",
    "preservation": "preservation.html",
    "cryohub": "cryohub.html",
    "example": "example.html",
}


def extract_chunks_from_html(html_path):
    """Extract chunk filenames from script src attributes in an HTML file."""
    try:
        with open(html_path) as f:
            html = f.read()
    except FileNotFoundError:
        return set()

    # Match chunk JS filenames (not CSS, not turbopack runtime)
    all_chunks = re.findall(r'chunks/([a-f0-9]+\.js)', html)
    # Also catch turbopack runtime
    all_chunks += re.findall(r'chunks/(turbopack-[a-f0-9]+\.js)', html)
    return set(all_chunks)


def build_chunk_to_pages(html_dir):
    """Map each chunk filename to the set of pages that load it."""
    chunk_pages = defaultdict(set)
    for page_id, html_file in PAGES.items():
        path = os.path.join(html_dir, html_file)
        for chunk in extract_chunks_from_html(path):
            chunk_pages[chunk].add(page_id)
    return dict(chunk_pages)


def classify_chunks(chunk_pages, page_count):
    """Classify chunks as shared (all pages) or page-specific."""
    shared = set()
    page_specific = defaultdict(set)  # page_id -> set of chunks

    for chunk, pages in chunk_pages.items():
        # Skip runtime and vendor bundles
        if chunk.startswith("turbopack-") or chunk == "a6dad97d9634a72d.js":
            continue

        if len(pages) >= page_count - 1:  # on all or almost all pages = shared
            shared.add(chunk)
        else:
            for page_id in pages:
                page_specific[page_id].add(chunk)

    return shared, dict(page_specific)


def build_module_to_pages(modules, chunk_pages):
    """Map each module ID to the pages it's available on (via its chunks)."""
    mod_pages = {}
    for mid, meta in modules.items():
        pages = set()
        for chunk in meta.get("chunks", []):
            if chunk in chunk_pages:
                pages.update(chunk_pages[chunk])
        mod_pages[mid] = pages
    return mod_pages


def classify_modules(modules, shared_chunks, page_specific_chunks):
    """Classify modules as page-exclusive, shared, or multi-page."""
    # Module is page-exclusive if ALL its chunks are specific to one page
    # (or a mix of shared + one page's specific chunks)
    page_exclusive = defaultdict(set)  # page_id -> set of module IDs
    shared_modules = set()
    multi_page = {}  # mid -> set of pages

    for mid, meta in modules.items():
        chunks = set(meta.get("chunks", []))

        # Which page-specific chunk sets does this module appear in?
        pages_via_specific = set()
        in_shared = False
        for chunk in chunks:
            if chunk in shared_chunks:
                in_shared = True
            for page_id, page_chunks in page_specific_chunks.items():
                if chunk in page_chunks:
                    pages_via_specific.add(page_id)

        if not pages_via_specific:
            # Only in shared chunks — this is infrastructure
            shared_modules.add(mid)
        elif len(pages_via_specific) == 1:
            # In exactly one page's specific chunks — page-exclusive
            page_exclusive[list(pages_via_specific)[0]].add(mid)
        else:
            # In multiple pages' specific chunks — multi-page module
            multi_page[mid] = pages_via_specific

    return dict(page_exclusive), shared_modules, multi_page


def trace_imports(mid, modules, depth=0, max_depth=10, visited=None):
    """Recursively trace imports from a module, returning the dependency tree."""
    if visited is None:
        visited = set()
    if mid in visited or depth > max_depth:
        return {}
    visited.add(mid)

    meta = modules.get(mid, {})
    imports = [str(i) for i in meta.get("imports", [])]
    tree = {}
    for imp in imports:
        tree[imp] = trace_imports(imp, modules, depth + 1, max_depth, visited)
    return tree


def collect_transitive_imports(mids, modules, max_depth=10):
    """Collect all transitive imports from a set of module IDs."""
    visited = set()
    queue = list(mids)
    while queue:
        mid = queue.pop(0)
        if mid in visited:
            continue
        visited.add(mid)
        meta = modules.get(mid, {})
        for imp in meta.get("imports", []):
            imp_str = str(imp)
            if imp_str not in visited:
                queue.append(imp_str)
    return visited - mids  # Return only the dependencies, not the roots


def build_room_report(page_id, exclusive_mids, modules, shared_modules):
    """Build a detailed report for one room."""
    report = {
        "page": page_id,
        "exclusive_module_count": len(exclusive_mids),
        "exclusive_modules": [],
        "apis": set(),
        "hooks": set(),
        "stores": [],
        "providers": [],
        "shared_dependencies": [],
        "shared_dep_count": 0,
    }

    for mid in sorted(exclusive_mids, key=int):
        meta = modules.get(mid, {})
        mod_info = {
            "id": int(mid),
            "name": meta.get("name", ""),
            "kind": meta.get("kind", ""),
            "size": meta.get("size", 0),
            "imports": meta.get("imports", []),
            "apis": meta.get("apis", []),
            "hooks": [h for h in meta.get("hooks", [])
                       if h.startswith("use") and h not in BUILTIN_HOOKS],
            "exports": meta.get("exports", []),
        }
        report["exclusive_modules"].append(mod_info)

        # Aggregate
        for api in meta.get("apis", []):
            report["apis"].add(api)
        for hook in meta.get("hooks", []):
            if hook.startswith("use") and hook not in BUILTIN_HOOKS:
                report["hooks"].add(hook)
        if meta.get("kind") == "store":
            report["stores"].append({"id": int(mid), "name": meta.get("name", "")})
        if meta.get("kind") == "provider":
            report["providers"].append({"id": int(mid), "name": meta.get("name", "")})

    # Trace shared dependencies
    shared_deps = collect_transitive_imports(exclusive_mids, modules)
    shared_deps_in_shared = shared_deps & shared_modules
    report["shared_dep_count"] = len(shared_deps_in_shared)

    # Find the most interesting shared deps (providers, stores, api-clients)
    interesting_deps = []
    for mid in sorted(shared_deps_in_shared, key=int):
        meta = modules.get(mid, {})
        kind = meta.get("kind", "")
        if kind in ("provider", "store", "api-client", "hook"):
            interesting_deps.append({
                "id": int(mid),
                "name": meta.get("name", ""),
                "kind": kind,
            })
    report["shared_dependencies"] = interesting_deps

    # Convert sets to sorted lists for JSON
    report["apis"] = sorted(report["apis"])
    report["hooks"] = sorted(report["hooks"])

    return report


# React built-in hooks (not interesting for room analysis)
BUILTIN_HOOKS = {
    "useCallback", "useContext", "useEffect", "useId", "useLayoutEffect",
    "useMemo", "useOptimistic", "useReducer", "useRef", "useState",
    "useSyncExternalStore", "useTransition", "useDebugValue",
    "useDeferredValue", "useImperativeHandle", "useInsertionEffect",
}


# ---------------------------------------------------------------------------
# Module categorization for shared modules
# ---------------------------------------------------------------------------

# Patterns for categorizing shared/infra modules into subdirectories.
# Order matters — first match wins.
_CATEGORY_RULES = [
    # Vendor libraries (by name pattern)
    ("vendor/three-r3f",    lambda m, n: "three_r3f" in n),
    ("vendor/hls-wavesurfer", lambda m, n: "hls_wavesurfer" in n),
    ("vendor/motion",       lambda m, n: any(x in n for x in (
        "motion", "MotionConfig", "VisualElement", "DOMVisualElement",
        "animateTarget", "Animation", "Keyframe", "keyframe",
        "setStyle", "findValueType", "isAnimationControls",
        "isKeyframesTarget", "isZeroValueString", "fillWildcards",
        "testValueType", "calcGenerator", "generateLinearEasing",
        "statsBuffer", "createRenderBatcher", "pipe", "mix", "inertia",
        "backIn", "filter", "isVariantLabel", "variantPriority",
        "computeChangedPath", "supportsLinearEasing", "supportsScrollTimeline",
        "startWaapiAnimation", "frameloopDriver", "cancelFrame",
        "isSVGSVGElement", "isSVGElement", "SVG", "svg_elements",
        "addValueToWillChange", "getAnimatableNone", "createBox",
        "numberValueTypes", "dimensionValueTypes", "updateMotionValues",
        "isControllingVariants", "scaleCorrectors", "convertBoundingBox",
        "isForcedMotionValue", "applyBoxDelta", "measurePageBox",
        "scrapeMotionValues", "replaceTransitionType", "WithPromise",
        "JSAnimation", "getFinalKeyframe", "AsyncMotionValue",
        "initPrefersReducedMotion", "hasReducedMotionListener",
        "easingDefinition", "springDefaults", "cubicBezier",
        "isBezierDefinition", "circIn", "anticipate", "memo",
        "DOMKeyframesResolver", "parseValueFromTransform",
        "cancelIdleCallback", "resolveElements", "has2DTranslate",
        "animateSingleValue", "useCreateMotionContext", "fillOffset",
    )) or "motion_" in n),
    ("vendor/zod",          lambda m, n: any(x in n for x in (
        "_zod", "_Zod", "Zod", "_brand", "_decode", "decode",
        "bigint", "BIGINT_FORMAT", "endsWith", "createStandardJSON",
        "ZodFirstPartyTypeKind", "_ZodAny", "_ZodCheck", "_ZodError",
        "_ZodAsyncError", "_ZodRegistry", "ZodISODate", "TimePrecision",
        "version", "Doc",
    ))),
    ("vendor/swr",          lambda m, n: "swr" in n),
    ("vendor/lodash",       lambda m, n: n.startswith("lodash") or n == "useConstant"),

    # React / Next.js internals
    ("framework/react",     lambda m, n: n.startswith("react") or
        n.startswith("reexport_react") or n.startswith("reexport:react") or
        n in (
            "unresolvedThenable", "reportGlobalError",
            "describeHasCheckingStringProperty",
        )),
    ("framework/next",      lambda m, n: any(x in n for x in (
        "next_", "next-", "Next", "App", "PageRoot",
        "ErrorBoundary", "RedirectBoundary", "MetadataBoundary",
        "BailoutToCSR", "StaticGenBailout", "DynamicServer",
        "Postpone", "REDIRECT_ERROR", "ACTION_HEADER", "ACTION_HMR",
        "METADATA_BOUNDARY", "HEAD_REQUEST_KEY", "DYNAMIC_STALETIME",
        "DEFAULT_SEGMENT_KEY", "INTERCEPTION_ROUTE",
        "RouterContext", "AppRouterContext", "NavigationPromises",
        "ServerInsertedHTML", "ImageConfig", "VALID_LOADERS",
        "getImgProps", "getImageProps", "getImageBlurSvg", "Image",
        "getAppBuildId", "getDeploymentId", "getAssetPrefix",
        "normalizeAppPath", "createPrefetchURL", "formatUrl",
        "appendLayoutVaryPath", "doesStaticSegmentAppear",
        "createParamsFromClient", "dispatchAppRouterAction",
        "createMutableActionQueue", "handleHardNavError",
        "handleClientScriptLoad", "convertServerPatch",
        "refreshDynamicData", "createInitialRSCPayload",
        "cancelPrefetchTask", "bindSnapshot", "deleteFromLru",
        "isRequestAPICallable", "isHangingPromise",
        "extractInfoFromServerReference", "ActionDidNotRevalidate",
        "UnrecognizedActionError", "HTTPAccessError",
        "RedirectStatusCode", "EntryStatus", "FreshnessPolicy",
        "FetchStrategy", "Fallback", "defaultHead", "useLinkStatus",
        "IDLE_LINK_STATUS", "ReadonlyURLSearchParams",
        "interopRequireDefault", "getRequireWildcardCache",
        "getProperError", "isRecoverableError", "getCacheSignal",
        "actionAsyncStorage", "workAsyncStorage",
        "setCacheBustingSearchParam", "HTML_LIMITED_BOT",
        "createPrerenderSearchParams", "chunk_loader", "appBootstrap",
        "ERROR_REVALIDATE_EVENT", "hasInterceptionRouteInCurrentTree",
        "ClientSegmentRoot", "findClosestQuality", "IconMark",
    )) or m.get("kind") == "vendor"),

    # Polyfills / environment
    ("infra/polyfill",      lambda m, n: any(x in n for x in (
        "polyfill", "process", "buffer",
    ))),

    # App-level shared game code
    ("app/providers",       lambda m, n: m.get("kind") == "provider" or
        n in ("ClientArgState",)),
    ("app/stores",          lambda m, n: m.get("kind") == "store"),
    ("app/hooks",           lambda m, n: m.get("kind") == "hook"),
    ("app/api",             lambda m, n: m.get("kind") == "api-client"),
    ("app/schemas",         lambda m, n: m.get("kind") == "schema"),
]


def categorize_module(mid, meta, imported_by_category=None):
    """Return the subdirectory for a shared module."""
    name = meta.get("name", "")
    for subdir, test in _CATEGORY_RULES:
        if test(meta, name):
            return subdir
    # Fallback: if it has APIs, it's app logic
    if meta.get("apis"):
        return "app/api"
    # Fallback: use import graph — if most importers are in one category,
    # this module belongs there too
    if imported_by_category:
        cat = imported_by_category.get(mid)
        if cat:
            return cat
    return "infra/other"


def propagate_categories(modules, shared_modules):
    """Use import graph to assign categories to uncategorized modules.

    If a module is only imported by motion modules, it's motion.
    If only imported by next modules, it's next. Etc.
    """
    # First pass: categorize what we can by name/pattern
    direct = {}
    for mid in shared_modules:
        meta = modules.get(mid, {})
        name = meta.get("name", "")
        for subdir, test in _CATEGORY_RULES:
            if test(meta, name):
                direct[mid] = subdir
                break

    # Build reverse import map: mid -> set of mids that import it
    importers = defaultdict(set)
    for mid, meta in modules.items():
        for imp in meta.get("imports", []):
            importers[str(imp)].add(mid)

    # Propagate: for uncategorized modules, look at their importers' categories
    inferred = {}
    for _round in range(3):  # Multiple rounds for transitive inference
        changed = False
        for mid in shared_modules:
            if mid in direct or mid in inferred:
                continue
            cats = defaultdict(int)
            for importer in importers.get(mid, set()):
                cat = direct.get(importer) or inferred.get(importer)
                if cat:
                    # Use top-level category (vendor/motion -> vendor/motion)
                    cats[cat] += 1
            if cats:
                best = max(cats, key=cats.get)
                # Only infer if majority of importers agree
                total = sum(cats.values())
                if cats[best] >= total * 0.6:
                    inferred[mid] = best
                    changed = True
        if not changed:
            break

    return {**direct, **inferred}


def organize_modules(modules_dir, organize_dir, modules, page_exclusive,
                     multi_page, shared_modules):
    """Copy module files into a semantic directory structure."""
    import shutil

    if os.path.exists(organize_dir):
        shutil.rmtree(organize_dir)

    counts = defaultdict(int)

    def place_module(mid, subdir):
        """Copy a module file into the organized directory."""
        meta = modules.get(mid, {})
        src_file = meta.get("file", "")
        if not src_file:
            return
        src = os.path.join(modules_dir, src_file)
        if not os.path.exists(src):
            return
        dst_dir = os.path.join(organize_dir, subdir)
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(dst_dir, src_file))
        counts[subdir] += 1

    # 1. Room-exclusive modules
    for page_id, mids in page_exclusive.items():
        for mid in mids:
            place_module(mid, f"rooms/{page_id}")

    # 2. Multi-page modules — put in "shared-game" with a tag
    for mid, pages in multi_page.items():
        place_module(mid, "shared-game")

    # 3. Shared/infrastructure modules — categorize with import graph propagation
    categories = propagate_categories(modules, shared_modules)
    for mid in shared_modules:
        meta = modules.get(mid, {})
        subdir = categories.get(mid) or categorize_module(mid, meta)
        place_module(mid, subdir)

    # 4. Copy index.json and vendor bundles
    idx_src = os.path.join(modules_dir, "index.json")
    if os.path.exists(idx_src):
        shutil.copy2(idx_src, os.path.join(organize_dir, "index.json"))
    for f in os.listdir(modules_dir):
        if f.endswith(".js") and not any(
            modules.get(mid, {}).get("file") == f
            for mid in modules
        ):
            # Vendor bundle or untracked file
            shutil.copy2(
                os.path.join(modules_dir, f),
                os.path.join(organize_dir, "vendor", f),
            )
            os.makedirs(os.path.join(organize_dir, "vendor"), exist_ok=True)

    # Print summary
    print(f"\nOrganized {sum(counts.values())} modules into {organize_dir}/")
    for subdir in sorted(counts.keys()):
        print(f"  {subdir + '/':.<45} {counts[subdir]:>3} modules")


def print_room_report(report):
    """Pretty-print a room report."""
    page = report["page"]
    print(f"\n{'='*70}")
    print(f"  {page.upper()}")
    print(f"{'='*70}")
    print(f"  Exclusive modules: {report['exclusive_module_count']}")
    print(f"  Shared dependencies: {report['shared_dep_count']}")

    if report["apis"]:
        print(f"\n  APIs:")
        for api in report["apis"]:
            print(f"    {api}")

    if report["hooks"]:
        print(f"\n  Custom hooks:")
        for hook in sorted(report["hooks"]):
            print(f"    {hook}")

    if report["stores"]:
        print(f"\n  Stores:")
        for s in report["stores"]:
            print(f"    [{s['id']}] {s['name']}")

    if report["providers"]:
        print(f"\n  Providers:")
        for p in report["providers"]:
            print(f"    [{p['id']}] {p['name']}")

    print(f"\n  Modules:")
    for mod in report["exclusive_modules"]:
        name = mod["name"] or str(mod["id"])
        kind_tag = f" ({mod['kind']})" if mod['kind'] else ""
        size_kb = mod["size"] / 1024
        api_tag = f"  API: {', '.join(mod['apis'])}" if mod["apis"] else ""
        hook_tag = f"  hooks: {', '.join(mod['hooks'])}" if mod["hooks"] else ""
        imports_str = f"  imports: {mod['imports']}" if mod["imports"] else ""
        print(f"    [{mod['id']:>5}] {name:<40} {size_kb:>6.1f}KB{kind_tag}{api_tag}{hook_tag}")

    if report["shared_dependencies"]:
        print(f"\n  Key shared dependencies:")
        for dep in report["shared_dependencies"]:
            print(f"    [{dep['id']:>5}] {dep['name']:<40} ({dep['kind']})")


def main():
    parser = argparse.ArgumentParser(description="Map modules to rooms/pages")
    parser.add_argument("--modules", required=True, help="Path to modules directory")
    parser.add_argument("--html", required=True, help="Path to HTML directory")
    parser.add_argument("--output", help="Output JSON path (optional)")
    parser.add_argument("--organize", help="Output directory for organized module tree")
    args = parser.parse_args()

    # Load module index
    index_path = os.path.join(args.modules, "index.json")
    with open(index_path) as f:
        index = json.load(f)
    modules = index["modules"]

    print(f"Loaded {len(modules)} modules from {index_path}")

    # Step 1: Map chunks to pages
    chunk_pages = build_chunk_to_pages(args.html)
    print(f"Mapped {len(chunk_pages)} chunks across {len(PAGES)} pages")

    # Step 2: Classify chunks
    shared_chunks, page_specific = classify_chunks(chunk_pages, len(PAGES))
    print(f"\nChunk classification:")
    print(f"  Shared (infrastructure): {len(shared_chunks)} chunks")
    for page_id in sorted(page_specific.keys()):
        chunks = page_specific[page_id]
        print(f"  {page_id}: {len(chunks)} exclusive chunk(s) — {', '.join(sorted(chunks))}")

    # Step 3: Classify modules
    page_exclusive, shared_modules, multi_page = classify_modules(
        modules, shared_chunks, page_specific
    )

    print(f"\nModule classification:")
    print(f"  Shared (infrastructure): {len(shared_modules)} modules")
    for page_id in sorted(page_exclusive.keys()):
        mids = page_exclusive[page_id]
        print(f"  {page_id}: {len(mids)} exclusive modules")
    if multi_page:
        print(f"  Multi-page: {len(multi_page)} modules")
        for mid, pages in sorted(multi_page.items(), key=lambda x: int(x[0])):
            name = modules.get(mid, {}).get("name", "")
            print(f"    [{mid}] {name} — {', '.join(sorted(pages))}")

    # Step 4: Build per-room reports
    reports = {}
    for page_id in sorted(page_exclusive.keys()):
        report = build_room_report(
            page_id, page_exclusive[page_id], modules, shared_modules
        )
        reports[page_id] = report
        print_room_report(report)

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    total_exclusive = sum(r["exclusive_module_count"] for r in reports.values())
    print(f"  Total exclusive modules: {total_exclusive} / {len(modules)}")
    print(f"  Shared infrastructure:   {len(shared_modules)}")
    print(f"  Multi-page:              {len(multi_page)}")
    print(f"\n  API surface by room:")
    for page_id in sorted(reports.keys()):
        apis = reports[page_id]["apis"]
        if apis:
            print(f"    {page_id}: {', '.join(apis)}")

    # Organize into directory tree
    if args.organize:
        organize_modules(
            args.modules, args.organize, modules,
            page_exclusive, multi_page, shared_modules,
        )

    # Write JSON output
    if args.output:
        output = {
            "chunk_classification": {
                "shared": sorted(shared_chunks),
                "page_specific": {k: sorted(v) for k, v in page_specific.items()},
            },
            "module_classification": {
                "shared_count": len(shared_modules),
                "shared_module_ids": sorted(shared_modules, key=int),
                "multi_page": {k: sorted(v) for k, v in multi_page.items()},
            },
            "rooms": {},
        }
        for page_id, report in reports.items():
            # Convert for JSON serialization
            output["rooms"][page_id] = report

        with open(args.output, "w") as f:
            json.dump(output, f, indent=2, default=list)
        print(f"\n  Written to {args.output}")


if __name__ == "__main__":
    main()
