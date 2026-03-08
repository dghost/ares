#!/usr/bin/env python3
"""Recover semantic names for decomposed Turbopack modules.

Minification destroys variable names but not string literals or structural
patterns. This script exploits several categories of surviving signal to
map numeric module IDs back to human-readable names.

Naming strategies (applied in priority order):

  1. RSC module registry — HTML contains I[moduleId, [chunks], "ExportName"].
     React Server Components needs these strings at runtime to wire component
     trees, so they survive minification. Highest-confidence signal.

  2. e.s() export registration — Turbopack's module system calls
     e.s(["name", () => value], moduleId) to register exports. The string
     names are the module's public API — load-bearing, can't be minified.

  3. Object.defineProperty exports — Next.js CommonJS modules use
     Object.defineProperty(exports, "name", {get: ...}). Same principle:
     property name strings survive minification.

  4. Named function-property exports — Top-level { name: function() { return x } }
     is the CommonJS barrel-export pattern. Keys are public API strings.

  5. Re-export detection — `t.exports = e.r(otherId)` means the module is a
     pure proxy. Named by resolving the pointer chain.

  6. String literal mining — API endpoints ("/api/..."), Zustand store names
     (name: "..."), custom hook names ("useXxx"), createContext names, cookie
     names, localStorage keys. All runtime values that survive minification.

  7. Library fingerprinting — Known code patterns identify vendor libraries.
     React's "https://react.dev/errors/", Three.js's {LEFT:0,MIDDLE:1,RIGHT:2},
     lodash's 0/0 NaN sentinel, etc.

  8. Mega-factory grouping — Modules with identical (size, import-set) share a
     factory function. Identify one member, name the whole group.

  9. Content heuristics — For small remaining modules, structural patterns
     reveal purpose: polyfills (String.prototype patching), schedulers
     (heap push/pop), config objects, etc.

Usage:
    python3 name_modules.py [--modules DIR] [--html DIR] [--output PATH]

    --modules DIR   Path to decomposed modules dir (default: ./modules or auto-detect)
    --html DIR      Path to HTML pages dir for RSC extraction (default: ./html or auto-detect)
    --output PATH   Where to write the name map JSON (default: ./module_names.json)

The output JSON maps module ID strings to {name, source, confidence, category} objects.
Also updates index.json in the modules directory with the resolved names.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict


# ---------------------------------------------------------------------------
# Strategy 1: RSC module registry
# ---------------------------------------------------------------------------

def extract_rsc_names(html_dir):
    """Extract module ID -> export name from RSC payloads in HTML pages.

    HTML contains self.__next_f.push([1,"..."]) with RSC payload strings.
    Inside those payloads, module references look like:
        I[moduleId, ["chunk1.js","chunk2.js"], "ExportName"]

    The export name is what the component tree uses to reference the module.
    """
    names = {}
    if not os.path.isdir(html_dir):
        return names

    pattern = re.compile(r'I\[(\d+),\[([^\]]*)\],"([^"]*)"\]')

    for fname in sorted(os.listdir(html_dir)):
        if not fname.endswith('.html'):
            continue
        page = fname.replace('.html', '')
        content = open(os.path.join(html_dir, fname)).read()

        # Extract RSC payload strings
        pushes = re.findall(
            r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', content, re.DOTALL
        )
        for push in pushes:
            decoded = push.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
            for m in pattern.finditer(decoded):
                mid = int(m.group(1))
                export_name = m.group(3)
                if export_name and export_name != 'default':
                    if mid not in names:
                        names[mid] = export_name

    return names


# ---------------------------------------------------------------------------
# Strategy 2-4: Export registration patterns
# ---------------------------------------------------------------------------

def extract_export_names(source):
    """Extract named exports from module source code.

    Three patterns, checked in order of specificity:

    1. e.s(["name", () => value, ...], moduleId)
       Turbopack's export registration. Returns list of (name, module_id).

    2. Object.defineProperty(exports, "name", {enumerable: true, get: ...})
       Next.js CommonJS pattern. Returns list of names.

    3. { name: function() { return x } }  at top level
       Barrel-export pattern. Returns list of names.
    """
    results = []
    # Strip comment header for position-sensitive searches
    body = strip_comment_header(source)

    # Pattern 1: e.s() — Turbopack export registration
    for m in re.finditer(r'e\.s\(\[([^\]]{3,})\],\s*(\d+)\)', source):
        inner = m.group(1)
        sub_mid = int(m.group(2))
        export_names = re.findall(r'"([^"]+)"', inner)
        # Filter out arrow function junk from regex over-matching
        export_names = [n for n in export_names if len(n) > 1 and '()' not in n]
        for name in export_names:
            results.append(('es_export', name, sub_mid))

    # Pattern 2: Object.defineProperty(exports_var, "name", ...)
    # The exports variable can be r, i, n, exports, or any single letter
    for m in re.finditer(
        r'Object\.defineProperty\(\w+,\s*"([^"]+)"', body[:4000]
    ):
        name = m.group(1)
        if name not in ('__esModule', 'default'):
            results.append(('defineProperty', name, None))

    # Pattern 3: top-level barrel exports { name: function() { return x } }
    #   This appears in the first ~2KB of Next.js internal modules as:
    #     var n = { exportName: function() { return someVar }, ... }
    for m in re.finditer(
        r'(\w{3,}):\s*function\(\)\s*\{\s*return\s+\w', body[:2500]
    ):
        name = m.group(1)
        if name not in ('default', 'value', 'enumerable', 'get', 'configurable',
                        'writable', 'set'):
            results.append(('barrel', name, None))

    return results


# ---------------------------------------------------------------------------
# Strategy 5: Re-export detection
# ---------------------------------------------------------------------------

def detect_reexport(source):
    """Check if module is a pure re-export, returning target ID or None.

    Pure re-exports have no logic — just:
        "use strict"; t.exports = e.r(targetId)
    or:
        "use strict"; e.r(targetId)
    or without "use strict":
        t.exports = e.r(targetId)
    """
    # Strip comment lines
    body = '\n'.join(
        l for l in source.split('\n') if not l.strip().startswith('//')
    ).strip()

    # With "use strict"
    m = re.match(r'\s*"use strict";\s*t\.exports\s*=\s*e\.r\((\d+)\)', body)
    if m:
        return int(m.group(1))

    m = re.match(r'\s*"use strict";\s*e\.r\((\d+)\)\s*$', body)
    if m:
        return int(m.group(1))

    # Without "use strict" — bare re-export
    m = re.match(r'\s*t\.exports\s*=\s*e\.r\((\d+)\)\s*$', body)
    if m:
        return int(m.group(1))

    # Property access re-export: t.exports = e.r(xxx).Symbol etc.
    m = re.match(r'\s*t\.exports\s*=\s*e\.r\((\d+)\)\.\w+\s*$', body)
    if m:
        return int(m.group(1))

    return None


# ---------------------------------------------------------------------------
# Strategy 6: String literal mining
# ---------------------------------------------------------------------------

def mine_string_signals(source):
    """Extract naming signals from string literals in module source.

    Returns dict of signal_type -> value for the first match of each type.
    """
    signals = {}
    body = strip_comment_header(source)
    prefix = body[:5000]  # Most signals appear early

    # Custom React hooks (not built-in)
    BUILTIN_HOOKS = {
        'useEffect', 'useState', 'useRef', 'useCallback', 'useMemo',
        'useContext', 'useReducer', 'useLayoutEffect', 'useId',
        'useSyncExternalStore', 'useOptimistic', 'useInsertionEffect',
        'useTransition', 'useDeferredValue', 'useImperativeHandle',
        'useDebugValue',
    }
    m = re.search(r'"(use[A-Z][a-zA-Z]+)"', prefix)
    if m and m.group(1) not in BUILTIN_HOOKS:
        signals['custom_hook'] = m.group(1)

    # Zustand store name
    m = re.search(r'name:\s*"([^"]+)"', prefix[:3000])
    if m:
        signals['store_name'] = m.group(1)

    # createContext with name
    m = re.search(r'createContext\("([^"]+)"\)', source)
    if m:
        signals['context_name'] = m.group(1)
    elif 'createContext(' in source:
        signals['has_context'] = True

    # API endpoints
    apis = re.findall(r'["\'](/api/[a-zA-Z0-9/_\-]+)["\']', prefix)
    if apis:
        signals['api'] = apis[0]

    # Cookie names
    m = re.search(r'"(goliath[^"]*)"', prefix)
    if m:
        signals['cookie'] = m.group(1)

    # localStorage keys
    m = re.search(r'(?:getItem|setItem)\("([^"]+)"\)', prefix)
    if m:
        signals['localStorage'] = m.group(1)

    return signals


# ---------------------------------------------------------------------------
# Strategy 7: Library fingerprinting
# ---------------------------------------------------------------------------

# Each fingerprint is (test_function, name, category)
# test_function takes source code (first N bytes) and returns True if matched
LIBRARY_FINGERPRINTS = [
    # React-DOM: production error URL
    (lambda s: '"https://react.dev/errors/"' in s[:3000] and 'nodeType' in s[:5000],
     'react-dom', 'react'),

    # React core: Symbol.for("react.transitional.element")
    (lambda s: 'react.transitional.element' in s[:2000],
     'react', 'react'),

    # React JSX runtime: createElement-like with key handling
    (lambda s: 'react.transitional.element' in s[:1000] and s.strip().startswith('"use strict";\n    var n = Symbol.for'),
     'react-jsx-runtime', 'react'),

    # React-DOM entry: __REACT_DEVTOOLS_GLOBAL_HOOK__ + checkDCE
    (lambda s: '__REACT_DEVTOOLS_GLOBAL_HOOK__' in s[:500] and 'checkDCE' in s[:500],
     'react-dom-entry', 'react'),

    # React-DOM server lite: react.portal + smaller than full react-dom
    (lambda s: 'react.portal' in s[:2000] and 'react.optimistic_key' in s[:2000],
     'react-dom-server-lite', 'react'),

    # React scheduler: priority queue with heap operations
    (lambda s: ('function r(e, t)' in s[:500] or 'function r(e,t)' in s[:500])
     and '0 < n;' in s[:800] and '>>> 1' in s[:800],
     'react-scheduler', 'react'),

    # React server DOM client: r.status = "fulfilled"
    (lambda s: 'r.status = "fulfilled"' in s[:3000] and 'r.status = "rejected"' in s[:3000],
     'react-server-dom-client', 'react'),

    # Three.js: mouse button constants
    (lambda s: 'LEFT: 0' in s[:500] and 'MIDDLE: 1' in s[:500] and 'RIGHT: 2' in s[:500],
     'three_r3f_mega', 'three.js'),

    # HLS.js: Number.isFinite polyfill + MAX_SAFE_INTEGER
    (lambda s: 'Number.isFinite' in s[:500] and 'MAX_SAFE_INTEGER' in s[:500],
     'hls_wavesurfer_mega', 'hls/wavesurfer'),

    # Motion core: MotionContext + PresenceContext
    (lambda s: 'MotionContext' in s[:5000] and 'PresenceContext' in s[:5000],
     'motion-core', 'motion'),

    # lodash.debounce: NaN via 0/0, hex/binary/octal regex
    (lambda s: '0 / 0' in s[:200] and '/^0b[01]+$/i' in s[:500],
     'lodash.debounce', 'vendor'),

    # lodash.toNumber: same NaN/regex pattern but different shape (shorter)
    (lambda s: '0 / 0' in s[:500] and '/^0b[01]+$/i' in s[:800]
     and len(s) < 1000 and 'parseInt' in s,
     'lodash._toNumber', 'vendor'),

    # lodash.throttle: wraps debounce with maxWait
    (lambda s: '"Expected a function"' in s[:1500] and '"maxWait"' in s[:500]
     and 'leading' in s[:500],
     'lodash.throttle', 'vendor'),

    # lodash._baseGetTag: toStringTag + "[object Undefined/Null]"
    (lambda s: 'toStringTag' in s[:300] and '"[object Undefined]"' in s[:500],
     'lodash._baseGetTag', 'vendor'),

    # lodash._getRawTag: toStringTag + hasOwnProperty
    (lambda s: 'toStringTag' in s[:200] and 'hasOwnProperty' in s[:200]
     and len(s) < 700,
     'lodash._getRawTag', 'vendor'),

    # lodash._objectToString: Object.prototype.toString
    (lambda s: 'Object.prototype.toString' in s[:200] and len(s) < 300,
     'lodash._objectToString', 'vendor'),

    # lodash._root: globalThis detection chain
    (lambda s: 'e.g.Object === Object' in s[:200] and len(s) < 250,
     'lodash._root', 'vendor'),

    # lodash._Symbol: exports Symbol from root
    (lambda s: '.Symbol' in s[:200] and len(s) < 250,
     'lodash._Symbol', 'vendor'),

    # lodash._freeGlobal: Object === Object check, short
    (lambda s: 'self.Object === Object' in s[:300] and 'Function("return this")' in s[:400],
     'lodash._freeGlobal', 'vendor'),

    # lodash.isObject: null check + typeof "object"/"function"
    (lambda s: '"object" == t || "function" == t' in s[:300] and len(s) < 350,
     'lodash.isObject', 'vendor'),

    # lodash.isObjectLike: typeof "object" + null check
    (lambda s: ('"object" == typeof e' in s[:200] or 'typeof e' in s[:200])
     and len(s) < 300 and 'null' in s[:200],
     'lodash.isObjectLike', 'vendor'),

    # lodash.isSymbol: "[object Symbol]" check
    (lambda s: '"symbol" == typeof e' in s[:300] and '"[object Symbol]"' in s[:400],
     'lodash.isSymbol', 'vendor'),

    # lodash._trimmedEndIndex: /\s/ regex + charAt loop
    (lambda s: '/\\s/' in s[:200] and 'charAt' in s[:300] and len(s) < 350,
     'lodash._trimmedEndIndex', 'vendor'),

    # lodash._baseTrim: /^\s+/ + trimmedEndIndex
    (lambda s: '/^\\s+/' in s[:200] and '.slice(0,' in s[:300] and len(s) < 400,
     'lodash._baseTrim', 'vendor'),

    # lodash.now: Date.now wrapper
    (lambda s: '.Date.now()' in s[:300] and len(s) < 350,
     'lodash._now', 'vendor'),

    # React useSyncExternalStore shim: Object.is polyfill + useState + useEffect
    (lambda s: 'Object.is' in s[:300] and '1 / e == 1 / t' in s[:500],
     'react-useSyncExternalStore', 'react'),

    # React _interopRequireDefault: __esModule check
    (lambda s: '__esModule' in s[:200] and len(s) < 300,
     'interopRequireDefault', 'infrastructure'),

    # React _getRequireWildcardCache: WeakMap factory
    (lambda s: 'WeakMap' in s[:300] and '_' in s[:100] and len(s) < 1200,
     'getRequireWildcardCache', 'infrastructure'),

    # Buffer polyfill: byteLength, toByteArray, fromByteArray
    (lambda s: 'byteLength' in s[:500] and 'toByteArray' in s[:500] and 'fromByteArray' in s[:2000],
     'buffer-polyfill', 'vendor'),

    # Process polyfill: "setTimeout has not been defined"
    (lambda s: 'setTimeout has not been defined' in s[:1000],
     'process-polyfill', 'vendor'),

    # Polyfills: trimStart/trimEnd/Symbol.description patching
    (lambda s: 'trimStart' in s[:300] and 'trimEnd' in s[:300] and 'String.prototype' in s[:300],
     'polyfills', 'vendor'),

    # Node process env proxy
    (lambda s: 'e.g.process' in s[:300] and '.env' in s[:300] and len(s) < 500,
     'process-env', 'infrastructure'),

    # Motion MotionConfigContext: createContext with transformPagePoint
    (lambda s: 'transformPagePoint' in s[:300] and 'createContext' in s[:300],
     'MotionConfigContext', 'motion'),

    # SWR/Zod init: e.i(98821) + e.s(["default", ...])
    (lambda s: 'e.s(["default"' in s[:200] and len(s) < 200,
     'swr-init', 'swr/zod'),
]


def strip_comment_header(source):
    """Strip leading // comment lines from module source.
    The decompose script prepends multi-line comment headers that push
    actual code past the byte offsets used by fingerprints."""
    lines = source.split('\n')
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith('//'):
            start = i
            break
    return '\n'.join(lines[start:])


def fingerprint_library(source):
    """Try to identify the module as a known library.
    Returns (name, category) or (None, None)."""
    # Strip comment header so fingerprints check actual code, not metadata
    body = strip_comment_header(source)
    for test_fn, name, category in LIBRARY_FINGERPRINTS:
        try:
            if test_fn(body):
                return name, category
        except Exception:
            continue
    return None, None


# ---------------------------------------------------------------------------
# Strategy 8: Mega-factory grouping
# ---------------------------------------------------------------------------

def group_mega_factories(index):
    """Group modules by identical (size, imports) as a proxy for shared factory.

    Modules that share a factory function will have identical size and import
    sets, since the factory IS the code. Groups of 2+ are mega-factory siblings.

    Returns dict of (size, imports_tuple) -> [module_ids].
    """
    groups = defaultdict(list)
    for mid_str, meta in index['modules'].items():
        mid = int(mid_str)
        size = meta.get('size', 0)
        imports = tuple(sorted(meta.get('imports', [])))
        groups[(size, imports)].append(mid)

    # Only return actual groups (2+ members)
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


# ---------------------------------------------------------------------------
# Strategy 9: Content heuristics for small modules
# ---------------------------------------------------------------------------

def heuristic_name(source, meta):
    """Last-resort naming for small modules based on content patterns."""
    body = '\n'.join(
        l for l in source.split('\n') if not l.strip().startswith('//')
    ).strip()

    # Empty body = mega-factory stub (content lives in primary)
    if not body or len(body) < 10:
        primary = meta.get('mega_factory_primary')
        if primary is not None:
            return f'stub:{primary}'
        return 'empty'

    # Single-line re-export variants
    if re.match(r'^\s*"use strict";\s*(?:t\.exports\s*=\s*)?e\.r\(\d+\)', body):
        return None  # handled by re-export detection

    # Simple t.exports = e.r(xxx) without "use strict"
    m = re.match(r'^\s*t\.exports\s*=\s*e\.r\((\d+)\)', body)
    if m:
        return None  # re-export detection handles this

    # Constant exports: module that just exports a string/number/object
    m = re.match(r'^\s*"use strict";\s*t\.exports\s*=\s*"([^"]+)"', body)
    if m:
        return f'const:{m.group(1)}'

    m = re.match(r'^\s*"use strict";\s*t\.exports\s*=\s*(\d+)', body)
    if m:
        return f'const:{m.group(1)}'

    # Empty module: t.exports = {}
    if re.match(r'^\s*"use strict";\s*t\.exports\s*=\s*\{\s*\}\s*$', body):
        return 'empty-module'

    # SVG element list (Motion uses these)
    if '["animate", "circle", "defs"' in body or '"animate", "circle"' in body:
        return 'motion_svg_elements'

    # Motion context factory: imports MotionContext/PresenceContext, small size
    if meta.get('size', 0) < 6000 and 'MotionContext' in source:
        return 'motion_context_factory'

    # Chunk loader: e.v(t => Promise.all([chunks].map(...)))
    if 'e.v(' in body[:100] and 'Promise.all' in body[:200]:
        return 'chunk-loader'

    # e.s() with named export at end (catches modules the export extractor missed)
    m = re.search(r'e\.s\(\["(\w+)"', body)
    if m and m.group(1) != 'default':
        return m.group(1)

    # JSX component with destructured props — name from characteristic props
    # e.g. function({uescKillCountGoal:...}) -> KillCounter
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
    if 'hasAnimatedIn' in body[:300]:
        return 'AnimatedMediaThumbnail'

    # Next.js app bootstrap
    if 'appBootstrap' in body[:500]:
        return 'next-appBootstrap'

    # __esModule setup modules (Next.js wrapper pattern)
    if '__esModule' in body[:200] and 'Object.defineProperty' in body[:200]:
        # Check for specific export patterns
        if 'e.r(66849)' in body or 'polyfills' in source:
            return 'next-polyfill-loader'

    # Mime type classifier
    if 'startsWith("video/")' in body and 'startsWith("image/")' in body:
        return 'classifyMimeType'

    # CSS variable resolver
    if 'var(--' in body and 'getVariableValue' in body:
        return 'getVariableValue'

    # lodash.debounce function: imports isObject + now + toNumber, has Math.max/min
    if 'Math.max' in body[:300] and 'Math.min' in body[:300] and '"Expected a function"' in body:
        return 'lodash.debounce'

    # lodash.throttle: wraps debounce with leading/trailing options
    if '"Expected a function"' in body[:500] and 'leading' in body[:500] and 'trailing' in body[:500] and len(body) < 600:
        return 'lodash.throttle'

    # getRequireWildcardCache: WeakMap factory pattern
    if 'WeakMap' in body[:300] and len(body) < 1200 and 'function n(e)' in body[:100]:
        return 'getRequireWildcardCache'

    # Next.js default-export wrappers (identified by characteristic imports)
    # These modules export only "default" and are Next.js internal wrappers
    imports = set(int(m.group(1)) for m in re.finditer(r'e\.(?:r|i)\((\d+)\)', body[:500]))

    # Next.js Router (large, imports react + react-dom-entry + AppRouterContext)
    if len(body) > 5000 and 'bottom' in body and 'height' in body and 'IntersectionObserver' in body:
        return 'next-Router'

    # Next.js error boundary wrapper (imports HandleISRError)
    if 12354 in imports:
        return 'next-ErrorBoundaryHandler'

    # Next.js page wrapper (imports BailoutToCSR + PreloadChunks)
    if 67585 in imports and 52157 in imports:
        return 'next-PageRoot'

    # Next.js image default wrapper (imports findClosestQuality + getDeploymentId)
    if 70965 in imports and 43369 in imports:
        return 'next-ImageDefault'

    # Next.js default wrapper (imports react only, small)
    if len(body) < 1500 and len(imports) <= 2 and 'Object.defineProperty' in body[:200]:
        return 'next-internal'

    # Next.js App (imports AppRouterContext)
    if 8372 in imports and 'Object.defineProperty' in body[:200]:
        return 'next-App'

    # Next.js interop wrapper: imports interopRequireDefault + another module
    if 55682 in imports and len(imports) <= 3 and 'Object.defineProperty' in body[:200]:
        return 'next-interop'

    return None


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------

# Keywords that indicate category membership
CATEGORY_RULES = [
    # (category, test_function)
    ('three.js', lambda name, _: any(k in name.lower() for k in
        ['three', 'r3f', 'pmrem', 'webglrenderer', 'shaderchunk', 'shaderlib',
         'usethree', 'fiberprovider', 'camera_positions', 'pointcloud',
         'acesfilmic'])),
    ('hls/wavesurfer', lambda name, _: any(k in name.lower() for k in
        ['hls', 'wavesurfer'])),
    ('motion', lambda name, meta: any(k in name.lower() for k in
        ['motion', 'animate', 'easing', 'keyframe', 'spring', 'inertia',
         'bezier', 'interpolat', 'visual_element', 'visualelement']) or
        name in MOTION_MODULES),
    ('swr/zod', lambda name, _: any(k in name.lower() for k in
        ['zod', 'swr', '$brand', '$zod'])),
    ('react', lambda name, _: name.startswith('react') or name in
        ('unresolvedThenable', 'isThenable', 'createPromiseWithResolvers')),
    ('next.js', lambda name, _: name.startswith('next-') or name.startswith('next_') or
        name in NEXTJS_MODULES),
    ('vendor', lambda name, _: any(k in name.lower() for k in
        ['polyfill', 'lodash', 'buffer-'])),
]

# Known Motion library module names (partial list — extend as needed)
MOTION_MODULES = {
    'mixNumber', 'mix', 'getDefaultValueType', 'activeAnimations', 'reducer',
    'variantPriorityOrder', 'filter', 'calcGeneratorVelocity', 'getFinalKeyframe',
    'isGenerator', 'getOptimisedAppearId', 'VisualElement', 'resolveVariant',
    'containsCSSVariable', 'KeyframeResolver', 'createGeneratorEasing', 'hsla',
    'setAttributesFromProps', 'scaleCorrectors', 'velocityPerSecond',
    'convertBoundingBoxToBox', 'inertia', 'createBox', 'supportsScrollTimeline',
    'floatRegex', 'isForcedMotionValue', 'isVariantLabel',
    'easingDefinitionToFunction', 'warnOnce', 'addUniqueItem', 'numberValueTypes',
    'optimizedAppearDataAttribute', 'startWaapiAnimation', 'applyBoxDelta',
    'isSVGSVGElement', 'easeIn', 'initPrefersReducedMotion', 'pipe',
    'animateMotionValue', 'cubicBezier', 'backIn', 'AsyncMotionValueAnimation',
    'isObject', 'makeAnimationInstant', 'interpolate', 'isNumOrPxType',
    'replaceTransitionType', 'buildHTMLStyles', 'DOMKeyframesResolver',
    'isZeroValueString', 'setTarget', 'renderHTML', 'animate', 'resolveElements',
    'createRenderBatcher', 'supportsLinearEasing', 'measurePageBox',
    'springDefaults', 'generateLinearEasing', 'transformPropOrder',
    'updateMotionValuesFromProps', 'MotionGlobalConfig', 'microtask', 'noop',
    'isBezierDefinition', 'memo', 'hex', 'positionalKeys', 'findValueType',
    'rgba', 'calcGeneratorDuration', 'isPostpone', 'scrapeMotionValuesFromProps',
    'animateSingleValue', 'frameloopDriver', 'analyseComplexValue',
    'SubscriptionManager', 'hasReducedMotionListener', 'visualElementStore',
    'animateTarget', 'isNumericalString', 'spring', 'isKeyframesTarget',
    'getValueTransition', 'getAnimatableNone', 'SVGVisualElement', 'alpha',
    'clamp', 'has2DTranslate', 'dimensionValueTypes', 'anticipate', 'color',
    'addValueToWillChange', 'buildSVGAttrs', 'JSAnimation', 'isMotionValue',
    'progress', 'isAnimationControls', 'collectMotionValues',
    'resolveVariantFromProps', 'HTMLVisualElement', 'cancelFrame',
    'isControllingVariants', 'renderSVG', 'isSVGElement', 'correctBorderRadius',
    'circIn', 'isEasingArray', 'camelToDash', 'setStyle', 'DOMVisualElement',
    'isSVGTag', 'millisecondsToSeconds', 'degrees', 'time',
}

# Known Next.js internal module names
NEXTJS_MODULES = {
    'prefetch', 'ensureLeadingSlash', 'ReadonlyURLSearchParams', 'addBasePath',
    'createRenderParamsFromClient', 'encodeURIPath', 'fillWildcards',
    'HandleISRError', 'unstable_rethrow', 'callServer', 'HasLoadingBoundary',
    'notFound', 'HTML_LIMITED_BOT_UA_RE', 'actionAsyncStorageInstance',
    'removeTrailingSlash', 'pathHasPrefix', 'afterTaskAsyncStorage',
    'addPathPrefix', 'workAsyncStorageInstance', 'HeadManagerContext',
    'serverActionReducer', 'workUnitAsyncStorageInstance', 'ClientPageRoot',
    'handleMutable', 'isNavigatingToNewRootLayout', 'createHrefFromUrl',
    'PreloadChunks', 'hasBasePath', 'matchSegment', 'actionAsyncStorage',
    'workAsyncStorage', 'invariant', 'isNextRouterError', 'FogOfWarContext',
    'createRenderSearchParamsFromClient', 'BailoutToCSR',
    'HTTPAccessFallbackBoundary', 'createRouterCacheKey', 'parsePath',
    'isLocalURL', 'restoreReducer', 'dynamicAccessAsyncStorageInstance',
    'RedirectStatusCode', 'createCacheKey', 'WithPromise',
    'normalizePathTrailingSlash', 'afterTaskAsyncStorageInstance',
    'errorOnce', 'computeCacheBustingSearchParam', 'hmrRefreshReducer',
    'removeBasePath', 'dynamicAccessAsyncStorage', 'serverPatchReducer',
    'disableSmoothScrollDuringRouteTransition', 'findSourceMapURL',
    'ClientSegmentRoot', 'forbidden',
}


def categorize(name, meta):
    """Assign a module to a category based on its resolved name."""
    for category, test in CATEGORY_RULES:
        if test(name, meta):
            return category
    if name.startswith('reexport:') or name.startswith('api:') or \
       name.startswith('store:') or name.startswith('context:') or \
       name.startswith('const:') or name.startswith('stub:'):
        # Stubs inherit category from their primary
        if name.startswith('stub:'):
            try:
                primary_id = int(name.split(':')[1])
                # Check if primary is a known mega-factory
                if primary_id == 0 or primary_id == 23097:
                    return 'three.js'
                if primary_id == 5 or primary_id == 18566:
                    return 'hls/wavesurfer'
                if primary_id == 22219:
                    return 'swr/zod'
                if primary_id in (46932, 64978, 72846, 74008):
                    return 'motion'
            except (ValueError, IndexError):
                pass
        return 'infrastructure'
    if name == 'empty' or name == 'empty-module':
        # Empty modules are often mega-factory stubs — check imports
        imports = meta.get('imports', [])
        if any(i in (8155, 68834, 43476, 71645, 90072) for i in imports):
            return 'three.js'
        if any(i in (18566, 28884) for i in imports):
            return 'hls/wavesurfer'
        return 'infrastructure'
    return 'app'


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

def resolve_names(modules_dir, html_dir):
    """Run all naming strategies and return the complete name map.

    Returns dict of module_id (int) -> {name, source, confidence, category}
    """
    index_path = os.path.join(modules_dir, 'index.json')
    index = json.load(open(index_path))

    results = {}

    # ---- Strategy 1: RSC names (highest confidence) ----
    rsc_names = extract_rsc_names(html_dir)
    for mid, name in rsc_names.items():
        results[mid] = {'name': name, 'source': 'rsc', 'confidence': 'high'}

    # ---- Strategy 2-4: Export registration from mega-factories ----
    # Scan large modules for e.s() calls that define sub-module exports
    for mid_str, meta in index['modules'].items():
        mid = int(mid_str)
        if meta.get('size', 0) < 5000:
            continue
        fpath = os.path.join(modules_dir, f'{mid}.js')
        # Also try named files
        if not os.path.exists(fpath):
            for f in os.listdir(modules_dir):
                if f.startswith(f'{mid}_') and f.endswith('.js'):
                    fpath = os.path.join(modules_dir, f)
                    break
        if not os.path.exists(fpath):
            continue

        source = open(fpath).read()
        for sig_type, name, sub_mid in extract_export_names(source):
            if sig_type == 'es_export' and sub_mid is not None:
                if sub_mid not in results:
                    results[sub_mid] = {
                        'name': name, 'source': f'es_export:{mid}',
                        'confidence': 'high'
                    }

    # ---- Per-module strategies (2-9) ----
    for mid_str, meta in index['modules'].items():
        mid = int(mid_str)
        if mid in results:
            continue

        # Find the actual file
        fpath = os.path.join(modules_dir, f'{mid}.js')
        if not os.path.exists(fpath):
            for f in os.listdir(modules_dir):
                if f.startswith(f'{mid}_') and f.endswith('.js'):
                    fpath = os.path.join(modules_dir, f)
                    break
        if not os.path.exists(fpath):
            continue

        source = open(fpath).read()

        # Strategy 2-4: Own export names
        exports = extract_export_names(source)
        own_exports = [(t, n) for t, n, sid in exports if sid is None]
        if own_exports:
            # Use first non-trivial defineProperty or barrel export
            name = own_exports[0][1]
            results[mid] = {
                'name': name, 'source': own_exports[0][0],
                'confidence': 'high'
            }
            continue

        # Strategy 5: Re-export detection
        target = detect_reexport(source)
        if target is not None:
            results[mid] = {
                'name': f'reexport:{target}',
                'source': 'reexport', 'confidence': 'high'
            }
            continue

        # Strategy 6: String literal mining
        signals = mine_string_signals(source)
        if signals.get('custom_hook'):
            results[mid] = {
                'name': signals['custom_hook'],
                'source': 'hook_string', 'confidence': 'medium'
            }
            continue
        if signals.get('store_name'):
            results[mid] = {
                'name': f"store:{signals['store_name']}",
                'source': 'store_string', 'confidence': 'medium'
            }
            continue
        if signals.get('context_name'):
            results[mid] = {
                'name': f"context:{signals['context_name']}",
                'source': 'context_string', 'confidence': 'medium'
            }
            continue
        if signals.get('api'):
            parts = signals['api'].strip('/').split('/')
            api_name = parts[-1]
            results[mid] = {
                'name': f'api:{api_name}',
                'source': 'api_string', 'confidence': 'medium'
            }
            continue

        # Strategy 7: Library fingerprinting
        lib_name, lib_cat = fingerprint_library(source)
        if lib_name:
            results[mid] = {
                'name': lib_name, 'source': 'fingerprint',
                'confidence': 'high', 'category': lib_cat
            }
            continue

        # Strategy 9: Content heuristics
        h_name = heuristic_name(source, meta)
        if h_name:
            results[mid] = {
                'name': h_name, 'source': 'heuristic',
                'confidence': 'low'
            }
            continue

    # ---- Strategy 8: Mega-factory group propagation ----
    # If one member of a (size, imports) group is named, name the rest
    groups = group_mega_factories(index)
    for key, members in groups.items():
        # Find any named member
        named_member = None
        for mid in members:
            if mid in results:
                named_member = results[mid]['name']
                break
        if named_member:
            for mid in members:
                if mid not in results:
                    results[mid] = {
                        'name': named_member,
                        'source': 'group_propagation',
                        'confidence': 'medium'
                    }

    # ---- Resolve re-export chains ----
    # Replace reexport:NNN with reexport:actual_name
    for mid, entry in results.items():
        name = entry['name']
        if name.startswith('reexport:'):
            target_str = name[len('reexport:'):]
            if target_str.isdigit():
                target_id = int(target_str)
                if target_id in results:
                    target_name = results[target_id]['name']
                    # Follow one more level if also a reexport
                    if target_name.startswith('reexport:') and not target_name[9:].isdigit():
                        entry['name'] = f'reexport:{target_name[9:]}'
                    else:
                        entry['name'] = f'reexport:{target_name}'

    # ---- Merge with existing index.json names ----
    # Existing names from decompose_chunks.py take precedence when they're
    # more specific than what we found (e.g., they already resolved via catalog)
    for mid_str, meta in index['modules'].items():
        mid = int(mid_str)
        existing_name = meta.get('name')
        if existing_name and mid not in results:
            results[mid] = {
                'name': existing_name, 'source': 'index',
                'confidence': 'high'
            }
        elif existing_name and mid in results:
            # Keep existing if it's a proper name (not "default")
            if existing_name != 'default' and results[mid]['name'] == 'default':
                results[mid]['name'] = existing_name
                results[mid]['source'] = 'index'

    # ---- Assign categories ----
    for mid, entry in results.items():
        if 'category' not in entry:
            meta = index['modules'].get(str(mid), {})
            entry['category'] = categorize(entry['name'], meta)

    return results, index


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(results, index):
    """Print a summary report of naming coverage."""
    total = len(index['modules'])
    named = len(results)
    unnamed = [int(k) for k in index['modules'] if int(k) not in results]

    print(f"\nNaming coverage: {named}/{total} ({100*named/total:.0f}%)")

    # By source
    sources = defaultdict(int)
    for entry in results.values():
        sources[entry['source']] += 1
    print(f"\nBy source:")
    for src in sorted(sources, key=sources.get, reverse=True):
        print(f"  {src:30s} {sources[src]:>4d}")

    # By confidence
    conf = defaultdict(int)
    for entry in results.values():
        conf[entry['confidence']] += 1
    print(f"\nBy confidence:")
    for c in ['high', 'medium', 'low']:
        print(f"  {c:10s} {conf.get(c, 0):>4d}")

    # By category
    cats = defaultdict(int)
    for entry in results.values():
        cats[entry.get('category', 'unknown')] += 1
    print(f"\nBy category:")
    for cat in sorted(cats, key=cats.get, reverse=True):
        print(f"  {cat:20s} {cats[cat]:>4d}")

    # Unnamed
    if unnamed:
        print(f"\nUnnamed modules ({len(unnamed)}):")
        unnamed_metas = [(mid, index['modules'][str(mid)]) for mid in unnamed]
        unnamed_metas.sort(key=lambda x: x[1].get('size', 0), reverse=True)
        for mid, meta in unnamed_metas[:20]:
            print(f"  {mid:>6} ({meta.get('kind','?')}, {meta.get('size',0):,}B)")
        if len(unnamed) > 20:
            print(f"  ... and {len(unnamed) - 20} more (all <{unnamed_metas[20][1].get('size',0):,}B)")


def write_output(results, index, modules_dir, output_path):
    """Write name map JSON and update index.json."""

    # Write standalone name map
    name_map = {}
    for mid in sorted(results.keys()):
        entry = results[mid]
        name_map[str(mid)] = {
            'name': entry['name'],
            'source': entry['source'],
            'confidence': entry['confidence'],
            'category': entry.get('category', 'unknown'),
        }

    with open(output_path, 'w') as f:
        json.dump(name_map, f, indent=2)
        f.write('\n')
    print(f"\nWrote name map: {output_path}")

    # Update index.json with resolved names
    index_path = os.path.join(modules_dir, 'index.json')
    for mid_str, meta in index['modules'].items():
        mid = int(mid_str)
        if mid in results:
            meta['name'] = results[mid]['name']
            meta['name_source'] = results[mid]['source']
            meta['category'] = results[mid].get('category', 'unknown')

    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)
        f.write('\n')
    print(f"Updated index: {index_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_dir(candidates, label):
    """Find the first existing directory from a list of candidates."""
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Recover semantic names for decomposed Turbopack modules.'
    )
    parser.add_argument('--modules', '-m',
        help='Path to decomposed modules directory')
    parser.add_argument('--html',
        help='Path to HTML pages directory (for RSC extraction)')
    parser.add_argument('--output', '-o', default='module_names.json',
        help='Output path for name map JSON (default: module_names.json)')
    parser.add_argument('--dry-run', '-n', action='store_true',
        help='Print report only, do not write files')

    args = parser.parse_args()

    # Auto-detect directories
    modules_dir = args.modules or find_dir([
        './modules',
        './cryoarchive.systems/.raw/modules',
    ], 'modules')
    html_dir = args.html or find_dir([
        './html',
        './cryoarchive.systems/.raw/html',
    ], 'html')

    if not modules_dir or not os.path.exists(os.path.join(modules_dir, 'index.json')):
        print("Error: Could not find modules directory with index.json", file=sys.stderr)
        print("Use --modules to specify the path", file=sys.stderr)
        sys.exit(1)

    if not html_dir:
        print("Warning: No HTML directory found — RSC names will not be extracted",
              file=sys.stderr)
        html_dir = '/dev/null'  # will be caught by isdir check

    print(f"Modules: {modules_dir}")
    print(f"HTML:    {html_dir}")
    print(f"Output:  {args.output}")

    results, index = resolve_names(modules_dir, html_dir)
    print_report(results, index)

    if not args.dry_run:
        write_output(results, index, modules_dir, args.output)


if __name__ == '__main__':
    main()
