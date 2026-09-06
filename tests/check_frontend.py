#!/usr/bin/env python3
"""Static checks for desktop/plugin.js — SDK constraints are hard failures.

1. ESM syntax parses (node --input-type=module --check).
2. ZERO JSX syntax (the disk loader compiles nothing).
3. Only @hermes/plugin-sdk, react, react/jsx-runtime are imported.
4. Every identifier used as a jsx()/jsxs() component is imported or defined.
5. No hardcoded colors — theme vars / theme-mapped tailwind tokens only.

Run: python3 tests/check_frontend.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "desktop" / "plugin.js"
SDK_INDEX = Path.home() / ".hermes" / "hermes-agent" / "apps" / "desktop" / "src" / "sdk" / "index.ts"

ALLOWED_IMPORTS = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}

fails: list[str] = []
warns: list[str] = []


def fail(msg: str) -> None:
    fails.append(msg)


src = PLUGIN.read_text(encoding="utf-8")

# -- 1. ESM syntax -----------------------------------------------------------
node = subprocess.run(
    ["node", "--input-type=module", "--check"],
    input=src,
    capture_output=True,
    text=True,
)
if node.returncode != 0:
    fail(f"ESM syntax check failed:\n{node.stderr.strip()}")
else:
    print("ok  ESM syntax parses")

# -- 2. JSX syntax ------------------------------------------------------------
JSX_PATTERNS = [
    (r"</[A-Za-z]", "closing JSX tag"),
    (r"/>\s*$", "self-closing JSX tag"),
    (r"<[A-Z][A-Za-z0-9]*[\s/>]", "JSX component element"),
    (r"<(div|span|section|button|input|p|h[1-6]|ul|li|a|img|svg)\b", "JSX host element"),
    (r"\breturn\s+<", "JSX return"),
    (r"\)\s*<\w", "JSX after paren"),
]
jsx_hits = []
for pat, why in JSX_PATTERNS:
    for m in re.finditer(pat, src):
        # allow '<' inside comments describing the rule? none expected.
        line = src[: m.start()].count("\n") + 1
        jsx_hits.append(f"line {line}: {why}: {m.group(0)!r}")
if jsx_hits:
    for h in jsx_hits[:20]:
        fail(f"JSX syntax found — {h}")
else:
    print("ok  zero JSX syntax")

# -- 3. Banned imports ---------------------------------------------------------
specifiers = re.findall(r"""^import[^'"]*from\s+['"]([^'"]+)['"]""", src, re.M)
specifiers += re.findall(r"""^import\s+['"]([^'"]+)['"]""", src, re.M)
dynamic = re.findall(r"""import\(\s*['"]([^'"]+)['"]""", src)
require = re.findall(r"""\brequire\s*\(""", src)
banned = [s for s in specifiers + dynamic if s not in ALLOWED_IMPORTS]
if banned:
    fail(f"banned import specifiers: {banned}")
if require:
    fail("require() present — ESM only")
if not banned and not require:
    print(f"ok  imports restricted to {sorted(ALLOWED_IMPORTS)} (found: {sorted(set(specifiers))})")

# -- 3b. every SDK name we import must exist in the real SDK -------------------
m = re.search(r"import\s*\{([^}]+)\}\s*from\s*'@hermes/plugin-sdk'", src, re.S)
if m and SDK_INDEX.is_file():
    sdk_src = SDK_INDEX.read_text(encoding="utf-8")
    names = [n.strip().split(" as ")[0] for n in m.group(1).split(",") if n.strip()]
    missing = []
    for name in names:
        pat = re.compile(
            rf"export\s+(?:const|function|class|type|interface)?\s*{re.escape(name)}\b"
            rf"|export\s*\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}"
            rf"|export\s+\*\s+as\s+{re.escape(name)}\b"
        )
        if not pat.search(sdk_src):
            missing.append(name)
    if missing:
        fail(f"names imported from @hermes/plugin-sdk but NOT exported by the real SDK: {missing}")
    else:
        print(f"ok  all {len(names)} SDK imports exist in the real SDK index")
elif not SDK_INDEX.is_file():
    warns.append("SDK index.ts not found — skipped cross-check")

# -- 4. rendered identifiers ---------------------------------------------------
jsx_components = set(re.findall(r"\bjsxs?\(\s*([A-Za-z_$][\w$]*)", src))
host_elements = {
    "div", "span", "section", "button", "input", "p", "h1", "h2", "h3", "h4",
    "h5", "h6", "ul", "li", "a", "img", "svg", "code", "pre", "table", "form",
}
defined = set(re.findall(r"\b(?:function|class)\s+([A-Za-z_$][\w$]*)", src))
defined |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", src))
imported = set()
for m in re.finditer(r"import\s*\{([^}]+)\}\s*from\s*'([^']+)'", src, re.S):
    imported |= {n.strip().split(" as ")[-1].strip() for n in m.group(1).split(",") if n.strip()}
for m in re.finditer(r"import\s+(\w+)\s*,?\s*(?:\{[^}]*\})?\s*from\s*'([^']+)'", src):
    imported.add(m.group(1))
# function parameters are neither imported nor defined at top level — collect them
param_names = set(re.findall(r"function\s+\w+\s*\(([^)]*)\)", src))
for m in re.finditer(r"\(([^)]*)\)\s*=>", src):
    param_names.add(m.group(1))
params: set[str] = set()
for chunk in param_names:
    for p in chunk.split(","):
        p = p.strip().replace("...", "")
        p = re.sub(r"=.*$", "", p).strip()
        if re.match(r"^[A-Za-z_$][\w$]*$", p):
            params.add(p)
# destructured object params like { skill, tool, st } 
for m in re.finditer(r"\{\s*([^}]+)\}\s*[,)]", src[:0]):  # placeholder; handled below
    pass
for m in re.finditer(r"function\s+\w+\s*\(\s*\{([^}]*)\}", src):
    for p in m.group(1).split(","):
        p = p.strip().split("=")[0].strip().split(":")[0].strip()
        if re.match(r"^[A-Za-z_$][\w$]*$", p):
            params.add(p)

unknown = sorted(
    c for c in jsx_components
    if c not in host_elements and c not in defined and c not in imported and c not in params
)
if unknown:
    fail(f"jsx() component identifiers not imported/defined: {unknown}")
else:
    print(f"ok  all {len(jsx_components)} rendered identifiers resolve ({len(imported)} imported, {len(defined)} local)")

# -- 5. hardcoded colors --------------------------------------------------------
COLOR_PATTERNS = [
    (r"#[0-9a-fA-F]{3,8}\b", "hex color"),
    (r"\brgba?\(", "rgb()/rgba()"),
    (r"\bhsl\(", "hsl()"),
    (r"\b(?:text|bg|border|ring|fill|stroke)-(?:white|black|red|green|blue|yellow|gray|slate|zinc|neutral|stone|amber|emerald|indigo|cyan|rose|orange|lime|teal|violet|fuchsia|pink|sky)\b(?:-\d{2,3})?\b", "raw tailwind palette color"),
]
color_hits = []
for pat, why in COLOR_PATTERNS:
    for m in re.finditer(pat, src):
        line = src[: m.start()].count("\n") + 1
        color_hits.append(f"line {line}: {why}: {m.group(0)!r}")
if color_hits:
    for h in color_hits[:20]:
        fail(f"hardcoded color — {h}")
else:
    print("ok  no hardcoded colors (theme vars / theme-mapped tokens only)")

# -- 6. persistence hygiene ------------------------------------------------------
# localStorage is banned (persistence must go through ctx.storage). document.*
# and window.* ARE allowed — native disk plugins legitimately touch the DOM
# (the shipped theme template does) and window.location is avoided by design,
# but state that should survive reloads never lives in the DOM.
for pat, why in [(r"\blocalStorage\b", "localStorage"), (r"\bsessionStorage\b", "sessionStorage")]:
    for m in re.finditer(pat, src):
        line = src[: m.start()].count("\n") + 1
        fail(f"{why} at line {line} — persistence must go through ctx.storage")
if not any("ctx.storage" in f for f in fails):
    print("ok  no web-storage persistence (ctx.storage only)")

print()
for w in warns:
    print(f"warn: {w}")
if fails:
    print(f"FAILED — {len(fails)} problem(s):")
    for f in fails:
        print(f"  ✗ {f}")
    sys.exit(1)
print("ALL FRONTEND STATIC CHECKS PASSED")
