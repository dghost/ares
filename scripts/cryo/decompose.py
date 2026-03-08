#!/usr/bin/env python3
"""Decompose Turbopack chunks into individual named modules.

Turbopack architecture (three layers):

1. Runtime (turbopack-*.js) — The module loader. Defines e.r(), e.i(), e.s(),
   etc. Replaces globalThis.TURBOPACK array with {push: I} to intercept chunk
   registrations. Not application code — skipped entirely.

2. Chunks (*.js) — Delivery vehicles. Each calls globalThis.TURBOPACK.push([
   scriptRef, id, factory, id, id, factory, ...]) to register module factories
   into the global module map. The chunk file itself is meaningless packaging.

3. Modules — The actual code units. Each has a numeric ID and a factory function
   (e, t, r) => { ... } where e=loader, t=module record, r=exports.
   Modules import each other by ID via e.r(id) / e.i(id).

This script extracts layer 3, discarding layers 1 and 2. When the same module
is bundled into multiple page-specific chunks, only one copy is kept.

Module naming recovers semantic names from minified code by exploiting signals
that survive minification: RSC module references in HTML, export registration
strings (e.s(), Object.defineProperty), library fingerprints, and content
heuristics. See resolve_module_names() for the full strategy list.

Factory parameter conventions:
  e = module loader (.r() require, .i() import/namespace, .s() set exports,
      .l() load chunk, .v() set value, .n() set namespace, .g globalThis,
      .c module cache, .a() async module, .A() async import)
  t = module record (.exports)
  r = exports object

Usage:
  python3 decompose_chunks.py --input <pretty_js_dir> --output <modules_dir> [--html <html_dir>]
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict


# ---------------------------------------------------------------------------
# JS tokenizer — handles strings, template literals, regex, comments
# ---------------------------------------------------------------------------

def find_matching_brace(code, start):
    """Find the closing } that matches the { at position start.
    Properly handles strings, template literals, and nested braces.
    Expects prettified input (js-beautify) so regex ambiguity is minimal."""
    assert code[start] == '{', f"Expected '{{' at position {start}, got '{code[start]}'"
    depth = 0
    i = start
    length = len(code)

    while i < length:
        c = code[i]

        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        elif c in ('"', "'"):
            quote = c
            i += 1
            while i < length and code[i] != quote:
                if code[i] == '\\':
                    i += 1
                i += 1
        elif c == '`':
            i += 1
            tdepth = 0
            while i < length:
                if code[i] == '\\':
                    i += 1
                elif code[i] == '`' and tdepth == 0:
                    break
                elif code[i] == '$' and i + 1 < length and code[i + 1] == '{':
                    tdepth += 1
                    i += 1
                elif code[i] == '}' and tdepth > 0:
                    tdepth -= 1
                i += 1
        elif c == '/' and i + 1 < length:
            if code[i + 1] == '/':
                while i < length and code[i] != '\n':
                    i += 1
                continue
            elif code[i + 1] == '*':
                i += 2
                while i < length - 1 and not (code[i] == '*' and code[i + 1] == '/'):
                    i += 1
                i += 1
        i += 1

    return -1


def find_factory_end(code, arrow_pos):
    """Given position of '=>' in an arrow function, find the end of its body."""
    i = arrow_pos + 2
    while i < len(code) and code[i] in ' \t\n\r':
        i += 1
    if i < len(code) and code[i] == '{':
        end = find_matching_brace(code, i)
        if end != -1:
            return end + 1
    return -1


# ---------------------------------------------------------------------------
# Chunk parser
# ---------------------------------------------------------------------------

def is_turbopack_runtime(code):
    """Detect turbopack runtime files (loader infrastructure, not modules)."""
    return bool(re.search(r'runtimeModuleIds', code[:500]))


def is_turbopack_chunk(code):
    """Check if this file is a Turbopack chunk (has the TURBOPACK.push pattern)."""
    return bool(re.search(
        r'(?:globalThis\.TURBOPACK|TURBOPACK)\s*(?:\|\|[^)]*\))?\s*\)\s*\.push\(\[',
        code[:300]
    ))


def extract_modules_from_chunk(code, chunk_filename):
    """Parse a Turbopack chunk and extract individual modules.

    Returns dict of {module_id: {"factory": str, "shared_with": list}}.
    """
    modules = {}

    # Find the push call
    push_match = re.search(
        r'(?:globalThis\.TURBOPACK|TURBOPACK)\s*(?:\|\|[^)]*\))?\s*\)\s*\.push\(\[',
        code
    )
    if not push_match:
        return modules

    # Skip the scriptRef expression (ternary for document.currentScript)
    pos = push_match.end()
    depth = 0
    while pos < len(code):
        c = code[pos]
        if c == '?' or c == '(':
            depth += 1
        elif c == ':' and depth > 0:
            depth -= 1
        elif c == ',' and depth == 0:
            pos += 1
            break
        pos += 1

    # Parse alternating: [id1, id2, ..., factory, id3, factory, ...]
    pending_ids = []
    content = code[pos:]

    i = 0
    while i < len(content):
        c = content[i]

        if c in ' \t\n\r':
            i += 1
            continue

        if c == ']':
            break

        if c == ',':
            i += 1
            continue

        # Number (module ID)
        if c.isdigit():
            j = i
            while j < len(content) and content[j].isdigit():
                j += 1
            module_id = int(content[i:j])
            pending_ids.append(module_id)
            i = j
            continue

        # Arrow function: (e,t,r)=>{...} or e=>{...}
        arrow_match = None

        if c == '(':
            paren_end = content.find(')', i)
            if paren_end != -1:
                # Skip whitespace after close paren to find =>
                k = paren_end + 1
                while k < len(content) and content[k] in ' \t\n\r':
                    k += 1
                if k + 1 < len(content) and content[k:k+2] == '=>':
                    factory_body_end = find_factory_end(content, k)
                    if factory_body_end != -1:
                        arrow_match = (i, factory_body_end)

        elif c.isalpha() and i + 1 < len(content):
            j = i
            while j < len(content) and content[j].isalpha():
                j += 1
            # Skip whitespace between identifier and =>
            k = j
            while k < len(content) and content[k] in ' \t':
                k += 1
            if j - i <= 2 and k + 1 < len(content) and content[k:k+2] == '=>':
                factory_body_end = find_factory_end(content, k)
                if factory_body_end != -1:
                    arrow_match = (i, factory_body_end)

        if arrow_match:
            fstart, fend = arrow_match
            factory_code = content[fstart:fend]

            if pending_ids:
                for mid in pending_ids:
                    modules[mid] = {
                        "factory": factory_code,
                        "shared_with": [x for x in pending_ids if x != mid] if len(pending_ids) > 1 else [],
                    }
                pending_ids = []

            i = fend
            continue

        i += 1

    # Fallback for mega-chunks where brace matching couldn't find the end
    if pending_ids and not any(mid in modules for mid in pending_ids):
        arrow = re.search(r'=>\s*\{', content)
        if arrow:
            factory_code = content[arrow.start():]
            factory_code = re.sub(r'\]\s*\)\s*;?\s*$', '', factory_code)
            for mid in pending_ids:
                modules[mid] = {
                    "factory": factory_code,
                    "shared_with": [x for x in pending_ids if x != mid] if len(pending_ids) > 1 else [],
                }

    return modules


# ---------------------------------------------------------------------------
# Module deduplication
# ---------------------------------------------------------------------------

def content_hash(code):
    """SHA-256 of factory code for dedup."""
    return hashlib.sha256(code.encode()).hexdigest()[:16]


def deduplicate_modules(chunk_modules):
    """Given {chunk: {mid: mod}} from all chunks, produce canonical modules.

    When the same module ID appears in multiple chunks (Turbopack bundles
    shared modules into each page chunk), keep one copy and track provenance.

    Returns {mid: mod} with mod["chunks"] listing all source chunks.
    """
    # Collect all instances of each module ID
    by_id = defaultdict(list)  # mid -> [(chunk, mod), ...]
    for chunk, modules in chunk_modules.items():
        for mid, mod in modules.items():
            by_id[mid].append((chunk, mod))

    canonical = {}
    for mid, instances in by_id.items():
        if len(instances) == 1:
            chunk, mod = instances[0]
            mod["chunks"] = [chunk]
            canonical[mid] = mod
        else:
            # Multiple chunks contain this module — group by content hash
            by_hash = defaultdict(list)
            for chunk, mod in instances:
                h = content_hash(mod["factory"])
                by_hash[h].append((chunk, mod))

            # Take the first variant as canonical
            first_hash = list(by_hash.keys())[0]
            first_chunk, first_mod = by_hash[first_hash][0]
            first_mod["chunks"] = [c for c, _ in instances]

            if len(by_hash) > 1:
                # Different content for the same ID across chunks — flag it
                first_mod["content_variants"] = len(by_hash)

            canonical[mid] = first_mod

    return canonical


# ---------------------------------------------------------------------------
# Mega-factory sub-module extraction
# ---------------------------------------------------------------------------

def extract_sub_modules(modules):
    """For mega-factories (one factory defining many sub-modules via e.s()),
    register the additional sub-module IDs."""
    all_ids = set(modules.keys())
    new_modules = {}

    for mid, mod in modules.items():
        for m in re.finditer(r'e\.s\(\[([^\]]*)\](?:,\s*(\d+))\)', mod["factory"]):
            sub_id = int(m.group(2))
            if sub_id not in all_ids and sub_id not in new_modules:
                export_list = m.group(1)
                names = re.findall(r'"([^"]+)"', export_list)
                new_modules[sub_id] = {
                    "factory": mod["factory"],  # same factory
                    "shared_with": sorted(all_ids),
                    "mega_factory_primary": mid,
                    "sub_module_exports": names[:10],
                    "chunks": mod.get("chunks", []),
                }

    modules.update(new_modules)
    return modules


# ---------------------------------------------------------------------------
# Module analysis
# ---------------------------------------------------------------------------

def analyze_module(mid, mod):
    """Extract metadata from a module's factory code."""
    code = mod["factory"]
    info = {}

    # Exports via e.s([name, getter, ...], moduleId)
    exports = []
    for m in re.finditer(r'e\.s\(\[([^\]]*)\](?:,\s*(\d+))?\)', code):
        export_list = m.group(1)
        target_id = int(m.group(2)) if m.group(2) else mid
        names = re.findall(r'"([^"]+)"', export_list)
        for name in names:
            exports.append({"name": name, "module_id": target_id})

    # Exports via Object.defineProperty(x, "name", ...)
    for m in re.finditer(r'Object\.defineProperty\(\w+,\s*"([^"]+)"', code):
        name = m.group(1)
        if name != "__esModule":
            exports.append({"name": name, "module_id": mid})

    # Exports via t.exports = ...
    if "t.exports=" in code.replace(" ", "") or "t.exports =" in code:
        if not any(e["name"] == "default" for e in exports):
            exports.append({"name": "default (t.exports)", "module_id": mid})

    if exports:
        info["exports"] = exports

    # Imports via e.r(moduleId) or e.i(moduleId)
    imports = set()
    for m in re.finditer(r'e\.[ri]\((\d+)\)', code):
        imports.add(int(m.group(1)))
    if imports:
        info["imports"] = sorted(imports)

    # React hooks
    hooks = set()
    for h in ["useState", "useEffect", "useLayoutEffect", "useRef", "useCallback",
              "useMemo", "useContext", "useReducer", "useId", "createContext",
              "useOptimistic", "useSyncExternalStore"]:
        if h in code:
            hooks.add(h)
    if hooks:
        info["hooks"] = sorted(hooks)

    if ".jsx(" in code or ".jsxs(" in code:
        info["has_jsx"] = True

    # API calls
    apis = set()
    for m in re.finditer(r'["\'](/api/[a-zA-Z0-9/_\-]+)["\']', code):
        apis.add(m.group(1))
    for m in re.finditer(r'`(/api/[^`]*)`', code):
        apis.add(m.group(1))
    if apis:
        info["api_endpoints"] = sorted(apis)

    if "fetch(" in code:
        info["has_fetch"] = True

    if "z.object" in code or "z.enum" in code or "z.string" in code:
        info["has_zod"] = True

    if 'getContext("2d"' in code or "getContext('2d'" in code:
        info["uses_canvas_2d"] = True
    if 'getContext("webgl"' in code or 'getContext("webgl2"' in code:
        info["uses_webgl"] = True

    keys = set()
    for m in re.finditer(r'(?:\.key|\.code)\s*===?\s*"([^"]+)"', code):
        keys.add(m.group(1))
    if keys:
        info["keyboard_keys"] = sorted(keys)

    for m in re.finditer(r'localStorage\.\w+Item\(\s*"([^"]+)"', code):
        info.setdefault("localstorage_keys", []).append(m.group(1))

    if "goliath_public" in code:
        info["reads_cookie"] = "goliath_public"

    info["size"] = len(code)
    info["kind"] = classify_module(code, info)

    return info


def classify_module(code, info):
    """Classify a module's purpose."""
    exports = [e["name"] for e in info.get("exports", [])]

    if any("Provider" in e for e in exports):
        return "provider"
    if any("Store" in e or "store" in e for e in exports):
        return "store"
    if info.get("has_jsx") and info.get("hooks"):
        return "component"
    if info.get("has_jsx"):
        return "component"
    if info.get("has_zod"):
        return "schema"
    if info.get("api_endpoints") or info.get("has_fetch"):
        return "api-client"
    if any("use" == e[:3] for e in exports):
        return "hook"
    if len(code) > 50000:
        return "vendor"
    if exports and not info.get("hooks") and not info.get("has_jsx"):
        return "utility"
    return "unknown"


# ---------------------------------------------------------------------------
# Module naming — recover semantic names from minified code
#
# Minification destroys variable names but not string literals or structural
# patterns. These strategies exploit surviving signals, applied in priority
# order. Each returns (name, source, confidence) or None.
# ---------------------------------------------------------------------------

def _strip_comment_header(source):
    """Strip leading // comment lines (the header we prepend to output files).
    Used so byte-offset fingerprints check actual code, not metadata."""
    lines = source.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith('//'):
            return '\n'.join(lines[i:])
    return source


# Strategy 1: RSC module registry in HTML pages
def extract_rsc_names(html_dir):
    """Extract module ID -> export name from I[moduleId, [chunks], "ExportName"]
    in self.__next_f.push() payloads. These are the framework's own module
    references — highest confidence signal."""
    names = {}
    if not html_dir or not os.path.isdir(html_dir):
        return names

    pattern = re.compile(r'I\[(\d+),\[([^\]]*)\],"([^"]*)"\]')
    for fname in sorted(os.listdir(html_dir)):
        if not fname.endswith('.html'):
            continue
        content = open(os.path.join(html_dir, fname)).read()
        pushes = re.findall(
            r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', content, re.DOTALL
        )
        for push in pushes:
            decoded = push.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
            for m in pattern.finditer(decoded):
                mid = int(m.group(1))
                export_name = m.group(3)
                if export_name and export_name != 'default':
                    names[mid] = export_name
    return names


# Strategy 2-4: Export registration patterns
def _extract_export_names(factory_code):
    """Extract named exports from factory code.

    Pattern 1: e.s(["name", () => value, ...], moduleId) — Turbopack registration
    Pattern 2: Object.defineProperty(exports, "name", ...) — CommonJS
    Pattern 3: { name: function() { return x } } — barrel exports
    """
    results = []

    # Pattern 1: e.s() for sub-module exports
    for m in re.finditer(r'e\.s\(\[([^\]]{3,})\],\s*(\d+)\)', factory_code):
        inner = m.group(1)
        sub_mid = int(m.group(2))
        export_names = [n for n in re.findall(r'"([^"]+)"', inner)
                        if len(n) > 1 and '()' not in n]
        for name in export_names:
            results.append(('es_export', name, sub_mid))

    # Pattern 2: Object.defineProperty(x, "name", ...)
    for m in re.finditer(r'Object\.defineProperty\(\w+,\s*"([^"]+)"', factory_code[:4000]):
        name = m.group(1)
        if name not in ('__esModule', 'default'):
            results.append(('defineProperty', name, None))

    # Pattern 3: barrel exports { name: function() { return x } }
    for m in re.finditer(r'(\w{3,}):\s*function\(\)\s*\{\s*return\s+\w', factory_code[:2500]):
        name = m.group(1)
        if name not in ('default', 'value', 'enumerable', 'get', 'configurable',
                        'writable', 'set'):
            results.append(('barrel', name, None))

    return results


# Strategy 5: Re-export detection
def _detect_reexport(factory_code):
    """Check if module is a pure re-export. Returns target ID or None.
    Recognizes: t.exports = e.r(id), bare e.r(id), and property access re-exports."""
    body = factory_code.strip()

    for pat in [
        r'^\s*(?:\([^)]*\)\s*=>\s*\{)?\s*"use strict";\s*t\.exports\s*=\s*e\.r\((\d+)\)',
        r'^\s*(?:\([^)]*\)\s*=>\s*\{)?\s*"use strict";\s*e\.r\((\d+)\)\s*[}\s]*$',
        r'^\s*t\.exports\s*=\s*e\.r\((\d+)\)\s*$',
        r'^\s*t\.exports\s*=\s*e\.r\((\d+)\)\.\w+\s*$',
    ]:
        m = re.match(pat, body)
        if m:
            return int(m.group(1))
    return None


# Strategy 6: String literal mining
_BUILTIN_HOOKS = frozenset({
    'useEffect', 'useState', 'useRef', 'useCallback', 'useMemo',
    'useContext', 'useReducer', 'useLayoutEffect', 'useId',
    'useSyncExternalStore', 'useOptimistic', 'useInsertionEffect',
    'useTransition', 'useDeferredValue', 'useImperativeHandle',
    'useDebugValue',
})

def _mine_string_signals(factory_code):
    """Extract naming signals from string literals (survive minification)."""
    signals = {}
    prefix = factory_code[:5000]

    m = re.search(r'"(use[A-Z][a-zA-Z]+)"', prefix)
    if m and m.group(1) not in _BUILTIN_HOOKS:
        signals['custom_hook'] = m.group(1)

    m = re.search(r'name:\s*"([^"]+)"', prefix[:3000])
    if m:
        signals['store_name'] = m.group(1)

    m = re.search(r'createContext\("([^"]+)"\)', factory_code)
    if m:
        signals['context_name'] = m.group(1)
    elif 'createContext(' in factory_code:
        signals['has_context'] = True

    apis = re.findall(r'["\'](/api/[a-zA-Z0-9/_\-]+)["\']', prefix)
    if apis:
        signals['api'] = apis[0]

    return signals


# Strategy 7: Library fingerprinting
_LIBRARY_FINGERPRINTS = [
    # React-DOM: production error URL + DOM node checks
    (lambda s: '"https://react.dev/errors/"' in s[:3000] and 'nodeType' in s[:5000],
     'react-dom', 'react'),
    # React core: Symbol.for("react.transitional.element")
    (lambda s: 'react.transitional.element' in s[:2000] and 'react.portal' not in s[:2000],
     'react', 'react'),
    # React JSX runtime
    (lambda s: 'react.transitional.element' in s[:1000]
     and s.strip().startswith('"use strict";\n    var n = Symbol.for'),
     'react-jsx-runtime', 'react'),
    # React-DOM entry: devtools hook
    (lambda s: '__REACT_DEVTOOLS_GLOBAL_HOOK__' in s[:500] and 'checkDCE' in s[:500],
     'react-dom-entry', 'react'),
    # React-DOM server lite: portal + optimistic_key
    (lambda s: 'react.portal' in s[:2000] and 'react.optimistic_key' in s[:2000],
     'react-dom-server-lite', 'react'),
    # React scheduler: priority queue heap
    (lambda s: '>>> 1' in s[:800] and '0 < n;' in s[:800],
     'react-scheduler', 'react'),
    # React server DOM client
    (lambda s: 'r.status = "fulfilled"' in s[:3000] and 'r.status = "rejected"' in s[:3000],
     'react-server-dom-client', 'react'),
    # React useSyncExternalStore shim
    (lambda s: 'Object.is' in s[:300] and '1 / e == 1 / t' in s[:500],
     'react-useSyncExternalStore', 'react'),
    # Three.js: mouse button constants
    (lambda s: 'LEFT: 0' in s[:500] and 'MIDDLE: 1' in s[:500] and 'RIGHT: 2' in s[:500],
     'three_r3f_mega', 'three.js'),
    # HLS.js
    (lambda s: 'Number.isFinite' in s[:500] and 'MAX_SAFE_INTEGER' in s[:500],
     'hls_wavesurfer_mega', 'hls/wavesurfer'),
    # Motion core
    (lambda s: 'MotionContext' in s[:5000] and 'PresenceContext' in s[:5000],
     'motion-core', 'motion'),
    # Motion config context
    (lambda s: 'transformPagePoint' in s[:300] and 'createContext' in s[:300],
     'MotionConfigContext', 'motion'),
    # lodash.debounce: NaN sentinel + numeric regex suite
    (lambda s: '0 / 0' in s[:500] and '/^0b[01]+$/i' in s[:800]
     and 'parseInt' in s[:800] and len(s) > 2000,
     'lodash.debounce', 'vendor'),
    # lodash.toNumber (shorter version of same pattern)
    (lambda s: '0 / 0' in s[:500] and '/^0b[01]+$/i' in s[:800] and len(s) < 1000,
     'lodash._toNumber', 'vendor'),
    # lodash.throttle: wraps debounce
    (lambda s: '"Expected a function"' in s[:1500] and 'leading' in s[:500],
     'lodash.throttle', 'vendor'),
    # lodash._baseGetTag
    (lambda s: 'toStringTag' in s[:300] and '"[object Undefined]"' in s[:500],
     'lodash._baseGetTag', 'vendor'),
    # lodash._getRawTag
    (lambda s: 'toStringTag' in s[:200] and 'hasOwnProperty' in s[:200] and len(s) < 700,
     'lodash._getRawTag', 'vendor'),
    # lodash._objectToString
    (lambda s: 'Object.prototype.toString' in s[:200] and len(s) < 300,
     'lodash._objectToString', 'vendor'),
    # lodash._root
    (lambda s: 'e.g.Object === Object' in s[:200] and len(s) < 250,
     'lodash._root', 'vendor'),
    # lodash._Symbol
    (lambda s: '.Symbol' in s[:200] and len(s) < 250,
     'lodash._Symbol', 'vendor'),
    # lodash._freeGlobal
    (lambda s: 'self.Object === Object' in s[:300] and 'Function("return this")' in s[:400],
     'lodash._freeGlobal', 'vendor'),
    # lodash.isObject
    (lambda s: '"object" == t || "function" == t' in s[:300] and len(s) < 350,
     'lodash.isObject', 'vendor'),
    # lodash.isObjectLike
    (lambda s: '"object" == typeof e' in s[:200] and len(s) < 300 and 'null' in s[:200],
     'lodash.isObjectLike', 'vendor'),
    # lodash.isSymbol
    (lambda s: '"symbol" == typeof e' in s[:300] and '"[object Symbol]"' in s[:400],
     'lodash.isSymbol', 'vendor'),
    # lodash._trimmedEndIndex
    (lambda s: '/\\s/' in s[:200] and 'charAt' in s[:300] and len(s) < 350,
     'lodash._trimmedEndIndex', 'vendor'),
    # lodash._baseTrim
    (lambda s: '/^\\s+/' in s[:200] and '.slice(0,' in s[:300] and len(s) < 400,
     'lodash._baseTrim', 'vendor'),
    # lodash._now
    (lambda s: '.Date.now()' in s[:300] and len(s) < 350,
     'lodash._now', 'vendor'),
    # Buffer polyfill
    (lambda s: 'byteLength' in s[:500] and 'toByteArray' in s[:500],
     'buffer-polyfill', 'vendor'),
    # Process polyfill
    (lambda s: 'setTimeout has not been defined' in s[:1000],
     'process-polyfill', 'vendor'),
    # Polyfills (trimStart/trimEnd)
    (lambda s: 'trimStart' in s[:300] and 'trimEnd' in s[:300] and 'String.prototype' in s[:300],
     'polyfills', 'vendor'),
    # interopRequireDefault
    (lambda s: '__esModule' in s[:200] and len(s) < 300,
     'interopRequireDefault', 'infra'),
    # getRequireWildcardCache
    (lambda s: 'WeakMap' in s[:300] and 'function n(e)' in s[:100] and len(s) < 1200,
     'getRequireWildcardCache', 'infra'),
    # Process env proxy
    (lambda s: 'e.g.process' in s[:300] and '.env' in s[:300] and len(s) < 500,
     'process-env', 'infra'),
    # SWR/Zod init
    (lambda s: 'e.s(["default"' in s[:200] and len(s) < 200,
     'swr-init', 'swr/zod'),
]


def _fingerprint_library(factory_code):
    """Try to identify the module as a known library.
    Returns (name, category) or (None, None)."""
    for test_fn, name, category in _LIBRARY_FINGERPRINTS:
        try:
            if test_fn(factory_code):
                return name, category
        except Exception:
            continue
    return None, None


# Strategy 9: Content heuristics for small/remaining modules
def _heuristic_name(factory_code, meta):
    """Last-resort naming based on content patterns."""
    body = factory_code.strip()

    # Empty body = mega-factory stub
    if not body or len(body) < 10:
        primary = meta.get('mega_factory_primary')
        if primary is not None:
            return f'stub:{primary}'
        return 'empty'

    # Empty module: t.exports = {}
    if re.match(r'^\s*"use strict";\s*t\.exports\s*=\s*\{\s*\}\s*$', body):
        return 'empty-module'

    # SVG element list (Motion)
    if '"animate", "circle"' in body:
        return 'motion_svg_elements'

    # Motion context factory
    if len(body) < 6000 and 'MotionContext' in body:
        return 'motion_context_factory'

    # Chunk loader
    if 'e.v(' in body[:100] and 'Promise.all' in body[:200]:
        return 'chunk-loader'

    # e.s() with named export
    m = re.search(r'e\.s\(\["(\w+)"', body)
    if m and m.group(1) != 'default':
        return m.group(1)

    # App-specific component patterns
    if 'uescKillCountGoal' in body:
        return 'KillCounter'
    if 'media:' in body[:200] and 'isDimmed' in body[:300]:
        return 'MediaThumbnail'
    if 'colorFilter' in body[:200] and 'ColorFilterProvider' in body[:500]:
        return 'ColorFilteredPage'
    if 'memoryUnlocked' in body[:500] and 'F24723' in body[:500]:
        return 'LockedPageImage'
    if 'grid-cols-8' in body and 'grid-rows-4' in body:
        return 'CameraGrid'
    if 'useFogOfWarStore' in body:
        return 'FogOfWarThumbnail'
    if 'usePointCloudStore' in body and 'hasLoaded' in body:
        return 'PointCloudThumbnail'
    if 'hasAnimatedIn' in body[:300] and 'useFogOfWarStore' not in body:
        return 'AnimatedMediaThumbnail'
    if 'startsWith("video/")' in body and 'startsWith("image/")' in body:
        return 'classifyMimeType'
    if 'var(--' in body and 'getVariableValue' in body:
        return 'getVariableValue'

    # lodash.debounce function (Math.max + Math.min + "Expected a function")
    if 'Math.max' in body[:300] and 'Math.min' in body[:300] and '"Expected a function"' in body:
        return 'lodash.debounce'
    # lodash.throttle (wraps debounce)
    if '"Expected a function"' in body[:500] and 'leading' in body[:500] and len(body) < 600:
        return 'lodash.throttle'

    # Next.js default-export wrappers (identified by import graph)
    imports = set(int(m.group(1)) for m in re.finditer(r'e\.(?:r|i)\((\d+)\)', body[:500]))

    if len(body) > 5000 and 'IntersectionObserver' in body:
        return 'next-Router'
    if 12354 in imports:  # HandleISRError
        return 'next-ErrorBoundaryHandler'
    if 67585 in imports and 52157 in imports:  # BailoutToCSR + PreloadChunks
        return 'next-PageRoot'
    if 70965 in imports and 43369 in imports:  # findClosestQuality + getDeploymentId
        return 'next-ImageDefault'
    if 8372 in imports and 'Object.defineProperty' in body[:200]:  # AppRouterContext
        return 'next-App'
    if 55682 in imports and len(imports) <= 3 and 'Object.defineProperty' in body[:200]:
        return 'next-interop'
    if len(body) < 1500 and len(imports) <= 2 and 'Object.defineProperty' in body[:200]:
        return 'next-internal'

    return None


# ---------------------------------------------------------------------------
# Name resolver — orchestrates all strategies
# ---------------------------------------------------------------------------

def resolve_module_names(modules, module_info, html_dir):
    """Run all naming strategies and return {mid: {"name": str, "source": str, "confidence": str}}.

    Strategy order (first match wins per module):
      1. RSC payload references in HTML         (high confidence)
      2. e.s() sub-module exports               (high)
      3. Object.defineProperty named exports     (high)
      4. Barrel exports { name: function() }     (high)
      5. Re-export detection (t.exports=e.r(x))  (high)
      6. String literal mining (hooks/stores/etc) (medium)
      7. Library fingerprinting                   (high)
      8. Mega-factory group propagation           (medium)
      9. Content heuristics                       (low)
    """
    names = {}  # mid -> {"name", "source", "confidence"}

    # --- Strategy 1: RSC names ---
    rsc_names = extract_rsc_names(html_dir)
    for mid, name in rsc_names.items():
        names[mid] = {"name": name, "source": "rsc", "confidence": "high"}

    # --- Strategy 2: Sub-module exports from mega-factories ---
    for mid, mod in modules.items():
        if module_info.get(mid, {}).get("size", 0) < 5000:
            continue
        for sig_type, name, sub_mid in _extract_export_names(mod["factory"]):
            if sig_type == 'es_export' and sub_mid is not None and sub_mid not in names:
                names[sub_mid] = {"name": name, "source": f"es_export:{mid}", "confidence": "high"}

    # --- Per-module strategies 3-9 ---
    for mid, mod in modules.items():
        if mid in names:
            continue

        factory = mod["factory"]
        info = module_info.get(mid, {})

        # Strategy 3-4: Own export names (defineProperty + barrel)
        own_exports = [(t, n) for t, n, sid in _extract_export_names(factory) if sid is None]
        if own_exports:
            names[mid] = {"name": own_exports[0][1], "source": own_exports[0][0], "confidence": "high"}
            continue

        # Strategy 5: Re-export
        target = _detect_reexport(factory)
        if target is not None:
            names[mid] = {"name": f"reexport:{target}", "source": "reexport", "confidence": "high"}
            continue

        # Strategy 6: String literal mining
        signals = _mine_string_signals(factory)
        if signals.get('custom_hook'):
            names[mid] = {"name": signals['custom_hook'], "source": "hook_string", "confidence": "medium"}
            continue
        if signals.get('store_name'):
            names[mid] = {"name": f"store:{signals['store_name']}", "source": "store_string", "confidence": "medium"}
            continue
        if signals.get('context_name'):
            names[mid] = {"name": f"context:{signals['context_name']}", "source": "context_string", "confidence": "medium"}
            continue
        if signals.get('api'):
            parts = signals['api'].strip('/').split('/')
            names[mid] = {"name": f"api:{parts[-1]}", "source": "api_string", "confidence": "medium"}
            continue

        # Strategy 7: Library fingerprinting
        lib_name, lib_cat = _fingerprint_library(factory)
        if lib_name:
            names[mid] = {"name": lib_name, "source": "fingerprint", "confidence": "high"}
            continue

        # Strategy 9: Content heuristics
        h_name = _heuristic_name(factory, mod)
        if h_name:
            names[mid] = {"name": h_name, "source": "heuristic", "confidence": "low"}
            continue

    # --- Strategy 8: Mega-factory group propagation ---
    # Modules with identical (size, imports) share a factory. Name the group.
    size_groups = defaultdict(list)
    for mid, mod in modules.items():
        info = module_info.get(mid, {})
        key = (info.get("size", 0), tuple(sorted(info.get("imports", []))))
        size_groups[key].append(mid)

    for key, members in size_groups.items():
        if len(members) < 2:
            continue
        named_member = None
        for mid in members:
            if mid in names:
                named_member = names[mid]["name"]
                break
        if named_member:
            for mid in members:
                if mid not in names:
                    names[mid] = {"name": named_member, "source": "group_propagation", "confidence": "medium"}

    # --- Resolve re-export chains ---
    for mid, entry in names.items():
        name = entry["name"]
        if name.startswith("reexport:") and name[9:].isdigit():
            target_id = int(name[9:])
            if target_id in names:
                target_name = names[target_id]["name"]
                # Follow one more level
                if target_name.startswith("reexport:") and not target_name[9:].isdigit():
                    entry["name"] = f"reexport:{target_name[9:]}"
                else:
                    entry["name"] = f"reexport:{target_name}"

    return names


def name_to_filename(mid, resolved_name):
    """Convert a resolved name to a safe filename. Returns just the stem (no .js).
    Falls back to numeric ID for low-quality names."""
    if not resolved_name:
        return str(mid)

    name = resolved_name["name"]
    confidence = resolved_name["confidence"]

    # Skip non-descriptive names
    if name in ('default', 'empty', 'empty-module') or name.startswith('stub:'):
        return str(mid)

    # For low-confidence names that are generic patterns, prefer the ID
    if confidence == "low" and name.startswith(('next-internal', 'next-interop')):
        return str(mid)

    safe = re.sub(r'[^a-zA-Z0-9_.]', '_', name)
    # Truncate very long names
    if len(safe) > 50:
        safe = safe[:50]
    return f"{mid}_{safe}"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def build_module_header(mid, info, mod, resolved_names):
    """Build a comment header for a module file."""
    lines = []
    lines.append(f"// Module {mid}")

    rn = resolved_names.get(mid)
    if rn and rn["name"] not in ('default', 'empty', 'empty-module') and not rn["name"].startswith('stub:'):
        lines.append(f"// Name: {rn['name']}  ({rn['source']}, {rn['confidence']})")

    chunks = mod.get("chunks", [])
    if len(chunks) == 1:
        lines.append(f"// Source chunk: {chunks[0]}")
    elif len(chunks) > 1:
        lines.append(f"// Bundled in {len(chunks)} chunks: {', '.join(chunks)}")

    lines.append(f"// Kind: {info.get('kind', 'unknown')}")
    lines.append(f"// Size: {info.get('size', 0):,} bytes")

    if mod.get("mega_factory_primary") is not None:
        lines.append(f"// Sub-module of mega-factory (primary: {mod['mega_factory_primary']})")

    if mod.get("content_variants"):
        lines.append(f"// WARNING: {mod['content_variants']} content variants across chunks")

    exports = info.get("exports", [])
    own_exports = [e["name"] for e in exports if e["module_id"] == mid]
    if own_exports:
        lines.append(f"// Exports: {', '.join(own_exports)}")

    imports = info.get("imports", [])
    if imports:
        import_strs = []
        for imp in imports:
            imp_rn = resolved_names.get(imp)
            if imp_rn and imp_rn["name"] not in ('default', 'empty'):
                import_strs.append(f"{imp} ({imp_rn['name']})")
            else:
                import_strs.append(str(imp))
        lines.append(f"// Imports: {', '.join(import_strs)}")

    if info.get("api_endpoints"):
        lines.append(f"// APIs: {', '.join(info['api_endpoints'])}")
    if info.get("hooks"):
        lines.append(f"// Hooks: {', '.join(info['hooks'])}")
    if info.get("keyboard_keys"):
        lines.append(f"// Keys: {', '.join(info['keyboard_keys'])}")
    if info.get("localstorage_keys"):
        lines.append(f"// localStorage: {', '.join(info['localstorage_keys'])}")
    if info.get("reads_cookie"):
        lines.append(f"// Cookie: {info['reads_cookie']}")

    lines.append("//")
    return "\n".join(lines)


def unwrap_factory(code):
    """Strip the outer arrow function wrapper, returning just the body.
    Input is already prettified by js-beautify."""
    m = re.match(r'^(?:\([^)]*\)|[a-z])\s*=>\s*\{', code)
    if m:
        body = code[m.end():]
        if body.rstrip().endswith('}'):
            body = body.rstrip()[:-1]
        return body
    return code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Decompose Turbopack chunks into individual named modules.'
    )
    parser.add_argument('--input', '-i', required=True,
        help='Directory of prettified JS chunks (js-beautify output)')
    parser.add_argument('--output', '-o', required=True,
        help='Output directory for decomposed modules')
    parser.add_argument('--html',
        help='Directory of HTML pages for RSC name extraction')
    args = parser.parse_args()

    js_dir = args.input.rstrip('/') + '/'
    out_dir = args.output.rstrip('/') + '/'
    html_dir = args.html

    if not os.path.isdir(js_dir):
        print(f"Error: input directory not found: {js_dir}", file=sys.stderr)
        sys.exit(1)

    # Phase 1: Parse all chunks, skipping non-module files
    print("Phase 1: Parsing chunks...")
    chunk_modules = {}  # chunk_filename -> {mid: mod}
    skipped = []
    vendor_files = []

    for fname in sorted(os.listdir(js_dir)):
        if not fname.endswith('.js'):
            continue
        path = f"{js_dir}{fname}"
        code = open(path).read()

        if fname.startswith('turbopack-') or is_turbopack_runtime(code):
            skipped.append((fname, "turbopack runtime"))
            continue

        if not is_turbopack_chunk(code):
            vendor_files.append((fname, code))
            skipped.append((fname, "non-turbopack vendor bundle"))
            continue

        modules = extract_modules_from_chunk(code, fname)
        if modules:
            chunk_modules[fname] = modules
            print(f"  {fname}: {len(modules)} modules")
        else:
            skipped.append((fname, "no modules found"))

    for fname, reason in skipped:
        print(f"  {fname}: skipped ({reason})")

    total_raw = sum(len(m) for m in chunk_modules.values())
    print(f"\n  Raw: {total_raw} module instances from {len(chunk_modules)} chunks")

    # Phase 2: Deduplicate — same module ID across page chunks
    print("\nPhase 2: Deduplicating...")
    modules = deduplicate_modules(chunk_modules)

    multi_chunk = sum(1 for m in modules.values() if len(m.get("chunks", [])) > 1)
    print(f"  {total_raw} instances -> {len(modules)} unique ({multi_chunk} shared)")

    # Phase 3: Extract sub-modules from mega-factories
    print("\nPhase 3: Sub-module extraction...")
    before = len(modules)
    modules = extract_sub_modules(modules)
    if len(modules) > before:
        print(f"  +{len(modules) - before} sub-modules from mega-factories")
    else:
        print(f"  No additional sub-modules")

    # Phase 4: Analyze all modules
    print(f"\nPhase 4: Analyzing {len(modules)} modules...")
    module_info = {}
    for mid, mod in sorted(modules.items()):
        module_info[mid] = analyze_module(mid, mod)

    import_counts = defaultdict(int)
    for mid, info in module_info.items():
        for dep in info.get("imports", []):
            import_counts[dep] += 1

    # Phase 5: Resolve names
    print(f"\nPhase 5: Resolving names...")
    resolved_names = resolve_module_names(modules, module_info, html_dir)

    named_count = sum(1 for rn in resolved_names.values()
                      if rn["name"] not in ('default', 'empty'))
    print(f"  {named_count}/{len(modules)} modules named ({100*named_count/len(modules):.0f}%)")

    by_source = defaultdict(int)
    for rn in resolved_names.values():
        by_source[rn["source"]] += 1
    for src in sorted(by_source, key=by_source.get, reverse=True)[:8]:
        print(f"    {src}: {by_source[src]}")

    # Phase 6: Write output
    print(f"\nPhase 6: Writing to {out_dir}...")
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    # Detect shared factories
    factory_hash_to_mids = defaultdict(list)
    for mid in sorted(modules):
        h = content_hash(modules[mid]["factory"])
        factory_hash_to_mids[h].append(mid)

    shared_factory_primary = {}
    for h, mids in factory_hash_to_mids.items():
        if len(mids) > 1 and len(modules[mids[0]]["factory"]) > 10000:
            primary = mids[0]
            for mid in mids[1:]:
                shared_factory_primary[mid] = primary

    written_files = {}

    for mid in sorted(modules):
        mod = modules[mid]
        info = module_info[mid]

        stem = name_to_filename(mid, resolved_names.get(mid))
        filename = f"{stem}.js"

        written_files[mid] = filename
        header = build_module_header(mid, info, mod, resolved_names)

        sfp = shared_factory_primary.get(mid)
        primary = sfp if sfp is not None else mod.get("mega_factory_primary")
        if primary is not None:
            primary_file = written_files.get(primary, f"{primary}.js")
            with open(f"{out_dir}{filename}", "w") as f:
                f.write(header + "\n")
                f.write(f"// Shared factory — full source in: {primary_file}\n")
                sub_exports = mod.get("sub_module_exports", [])
                own_exports = [e["name"] for e in info.get("exports", []) if e["module_id"] == mid]
                exports_list = sub_exports or own_exports
                if exports_list:
                    f.write(f"// This module exports: {', '.join(exports_list[:20])}\n")
        else:
            body = unwrap_factory(mod["factory"])
            with open(f"{out_dir}{filename}", "w") as f:
                f.write(header + "\n" + body + "\n")

    # Write vendor bundles as-is
    for fname, code in vendor_files:
        out_name = f"vendor_{fname}"
        with open(f"{out_dir}{out_name}", "w") as f:
            f.write(f"// Vendor bundle: {fname}\n")
            f.write(f"// Size: {len(code):,} bytes\n")
            f.write(f"// Non-Turbopack IIFE — not part of the module graph\n//\n")
            f.write(code)
        written_files[f"vendor:{fname}"] = out_name

    # Write index
    index = {
        "total_modules": len(modules),
        "total_unique": len(modules),
        "vendor_bundles": [f for f, _ in vendor_files],
        "skipped_files": {f: r for f, r in skipped},
        "modules": {},
    }

    for mid in sorted(modules):
        mod = modules[mid]
        info = module_info[mid]
        rn = resolved_names.get(mid)

        entry = {
            "kind": info.get("kind", "unknown"),
            "size": info.get("size", 0),
            "chunks": mod.get("chunks", []),
        }

        if rn:
            entry["name"] = rn["name"]
            entry["name_source"] = rn["source"]
            entry["name_confidence"] = rn["confidence"]

        exports = info.get("exports", [])
        own_exports = [e["name"] for e in exports if e["module_id"] == mid]
        if own_exports:
            entry["exports"] = own_exports

        if info.get("imports"):
            entry["imports"] = info["imports"]
        if info.get("api_endpoints"):
            entry["apis"] = info["api_endpoints"]
        if info.get("hooks"):
            entry["hooks"] = info["hooks"]
        if info.get("keyboard_keys"):
            entry["keys"] = info["keyboard_keys"]
        if info.get("localstorage_keys"):
            entry["localstorage"] = info["localstorage_keys"]
        if info.get("reads_cookie"):
            entry["cookie"] = info["reads_cookie"]
        if info.get("has_jsx"):
            entry["jsx"] = True
        if info.get("uses_canvas_2d"):
            entry["canvas_2d"] = True
        if info.get("uses_webgl"):
            entry["webgl"] = True
        if mod.get("mega_factory_primary") is not None:
            entry["mega_factory_primary"] = mod["mega_factory_primary"]
        if mod.get("content_variants"):
            entry["content_variants"] = mod["content_variants"]

        entry["imported_by"] = import_counts.get(mid, 0)
        entry["file"] = written_files.get(mid)

        index["modules"][str(mid)] = entry

    with open(f"{out_dir}index.json", "w") as f:
        json.dump(index, f, indent=2)
        f.write("\n")

    # Summary
    print(f"\n{'=' * 60}")
    by_kind = defaultdict(list)
    for mid, info in module_info.items():
        by_kind[info.get("kind", "unknown")].append(mid)

    for kind in sorted(by_kind):
        mids = by_kind[kind]
        print(f"  {kind:20s}  {len(mids):3d} modules")

    print(f"\nMost imported:")
    for mid, count in sorted(import_counts.items(), key=lambda x: -x[1])[:15]:
        rn = resolved_names.get(mid)
        label = rn["name"] if rn else str(mid)
        print(f"  {mid:>6d}  ({count:2d}x)  {label}")

    print(f"\nAPI surface:")
    all_apis = set()
    for mid, info in module_info.items():
        for api in info.get("api_endpoints", []):
            normalized = re.sub(r'\$\{[^}]+\}', '*', api)
            all_apis.add(normalized)
    for api in sorted(all_apis):
        mids = [mid for mid, info in module_info.items() if api in
                [re.sub(r'\$\{[^}]+\}', '*', a) for a in info.get("api_endpoints", [])]]
        print(f"  {api:50s}  ({len(mids)} modules)")

    print(f"\n{len(modules)} modules + {len(vendor_files)} vendor bundle(s) written to {out_dir}")
    print(f"Index: {out_dir}index.json")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
