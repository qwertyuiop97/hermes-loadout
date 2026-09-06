"""skills-toggle — backend routes for the Hermes desktop pane.

# MIT License — Copyright (c) 2026 qwertyuiop97
# See LICENSE at the package root.

Unified Hermes plugin package (see the Desktop Plugin SDK doc, "One package,
both SDKs")::

    ~/.hermes/plugins/skills-toggle/
    ├── plugin.yaml               # agent half (metadata only)
    ├── dashboard/
    │   ├── manifest.json         # {"name": "skills-toggle", "api": "plugin_api.py"}
    │   └── plugin_api.py         # THIS FILE — exports `router` (FastAPI APIRouter)
    └── desktop/
        └── plugin.js             # desktop half — pane UI, calls ctx.rest('/...')

Routes mount under ``/api/plugins/skills-toggle/``:

    GET  /health          → liveness + resolved paths (for the pane's error banner)
    GET  /state           → every skill + per-tool state (lean payload, cached)
    GET  /detail?skill=   → full SKILL.md text for one skill
    POST /toggle          → {skill, tool, enabled} link/unlink (or config edit for hermes)
    POST /toggle-bulk     → {skills: [...], tool, enabled}
    POST /repair          → {skill, tool} fix a broken link
    POST /repair-all      → fix every broken link that points into the skills tree
    POST /ensure-tool-dir → {tool} create a missing tool skills dir
    GET  /diff            → skills unlinked everywhere + dangling/foreign/unmanaged links

Design rules (see DECISIONS.md):
  * Hermes (~/.hermes/skills/<category>/<name>/SKILL.md) is the source of truth.
  * Consumer tools get SYMLINKS (never copies); link name == skill name.
  * Never delete a skill source dir, a real (non-symlink) dir, or a foreign
    symlink — only symlinks that resolve inside the managed skills tree.
  * The hermes tool toggles membership in ``skills.disabled`` (bare names) in
    ``config.yaml`` via a surgical line edit with a timestamped backup.
  * Zero hardcoded paths: home resolves via ``~``/``$HOME``, the Hermes root via
    ``$HERMES_HOME`` or ``$HERMES_PROFILE`` (→ ~/.hermes/profiles/<name>) or
    ``~/.hermes``; tool target dirs are overridable via
    ``<hermes_home>/skills-toggle.json`` with ``~`` and ``${VAR:-default}``
    expansion.

The core is deliberately dependency-free (stdlib only) so it can be imported
and tested without fastapi/pyyaml; the router layer is guarded so this same
file exports ``router`` inside the gateway process.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ID = "skills-toggle"
PLUGIN_VERSION = "2.2.0"

# ---------------------------------------------------------------------------
# Stable core API (v2) — consumed by the desktop pane's backend mount AND by
# sibling apps that import this module directly. Guarantee:
#   * module-level imports are STDLIB ONLY (fastapi is imported inside a
#     try/except ImportError guard; if absent, `router` is None and everything
#     else works identically) — no gateway/hermes imports anywhere
#   * constructing SkillsToggleCore(home, tools, log_path) directly performs
#     no I/O beyond the paths you hand it; all mutations are explicit calls
#   * skill ids are "category/name" strings; tools are plain dicts; every
#     route returns a JSON-able dict: {ok: true, ...} or {ok: false, error, code}
# Additions are allowed; removals/renames of anything in __all__ are breaking.
# ---------------------------------------------------------------------------

__all__ = [
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "DEFAULT_TOOLS",
    "SkillsToggleError",
    "SkillsToggleCore",
    "ConfigEditError",
    "hermes_home",
    "expand_path",
    "parse_skill_markdown",
    "parse_disabled",
    "set_disabled_member",
    "load_tools_config",
    "user_config_path",
    "get_core",
    "reset_core",
    "McpCore",
    "parse_mcp_servers",
    "set_mcp_server_enabled",
    "parse_codex_mcp",
    "codex_server_block",
    "same_path",
    "is_inside",
]

# ---------------------------------------------------------------------------
# Tool map: defaults (overridable via <hermes_home>/skills-toggle.json)
# ---------------------------------------------------------------------------

DEFAULT_TOOLS: dict = {
    "hermes": {"label": "Hermes", "special": "config"},  # skills.disabled in config.yaml
    "claude": {"label": "Claude", "dir": "~/.claude/skills"},
    "codex": {"label": "Codex", "dir": "~/.codex/skills"},
    "opencode": {"label": "OpenCode", "dir": "${OPENCODE_CONFIG_DIR:-~/.config/opencode}/skills"},
    "grok": {"label": "Grok", "dir": "~/.grok/skills"},
    # ZCode: user skills live in ~/.agents/skills when present (verified on the
    # reference machine), else ~/.zcode/skills. Overridable via config JSON.
    "zcode": {"label": "ZCode", "dir": "~/.agents/skills", "fallback_dir": "~/.zcode/skills"},
}

DESCRIPTION_TRUNC = 160
CACHE_TTL_SECONDS = 2.0
# First-pass shape check only: exactly one '/', no empty/NUL segments. Spaces
# and unicode are legal in skill dir names. The real traversal boundary is the
# allowlist membership check against the scanned skills tree.
_SKILL_ID_RE = re.compile(r"^[^/\0]+/[^/\0]+$")

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def hermes_home() -> Path:
    """Resolve the Hermes home: $HERMES_HOME > $HERMES_PROFILE > ~/.hermes."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    profile = os.environ.get("HERMES_PROFILE")
    if profile:
        candidate = Path.home() / ".hermes" / "profiles" / profile
        if candidate.is_dir():
            return candidate
    return Path.home() / ".hermes"


def _strip_extended(path_str: str) -> str:
    r"""Drop the Windows extended-length \\?\ prefix (keep UNC form)."""
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[8:]
    if path_str.startswith("\\\\?\\"):
        return path_str[4:]
    return path_str


def _canonical(path_str: str) -> str:
    """strip \\?\ → realpath → strip again (realpath re-adds it on Windows)
    → normcase/normpath."""
    once = _strip_extended(path_str)
    twice = _strip_extended(os.path.realpath(once))
    return os.path.normcase(os.path.normpath(twice))


def same_path(a: Path, b: Path) -> bool:
    r"""Path identity that survives Windows symlink resolution: readlink and
    resolve() can disagree on the \?\ prefix and 8.3 short names."""
    return _canonical(str(a)) == _canonical(str(b))


def is_inside(child: Path, parent: Path) -> bool:
    """Containment check with the same Windows canonicalization."""
    nc = _canonical(str(child))
    np_ = _canonical(str(parent))
    if nc == np_:
        return True
    sep = "\\" if os.name == "nt" or "\\" in nc or "\\" in np_ else "/"
    return nc.startswith(np_.rstrip(sep) + sep)


_VAR_DEFAULT_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_VAR_BARE_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def expand_path(value: str) -> Path:
    """Expand ``~``, ``${VAR:-default}`` and ``$VAR`` in a config path string."""
    value = value.strip()

    def _sub(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        got = os.environ.get(name)
        if got is not None and got != "":
            return got
        return default if default is not None else ""

    value = _VAR_DEFAULT_RE.sub(_sub, value)
    value = _VAR_BARE_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if not value:
        # e.g. '${MISSING_VAR:-}' — an empty expansion must never resolve to
        # the process CWD, which Path('').absolute() would yield.
        raise ValueError(f"path expands to empty string: {value!r}")
    return Path(os.path.expanduser(value)).absolute()


# ---------------------------------------------------------------------------
# SKILL.md frontmatter (tolerant parser; stdlib only)
# ---------------------------------------------------------------------------


def parse_skill_markdown(text: str) -> tuple[str, str]:
    """Return (name, description) from SKILL.md YAML frontmatter, tolerantly."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", ""
    fm: list[str] = []
    for ln in lines[1:]:
        if ln.strip() in ("---", "..."):
            break
        fm.append(ln)

    key_re = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
    out: dict[str, str] = {}
    i = 0
    while i < len(fm):
        m = key_re.match(fm[i].rstrip())
        if not m:
            i += 1
            continue
        key, value = m.group(1), m.group(2).strip()
        if value in (">-", ">", "|-", "|", ">+", "|+"):
            block: list[str] = []
            base_indent = None
            i += 1
            while i < len(fm):
                raw = fm[i]
                if not raw.strip():
                    block.append("")
                    i += 1
                    continue
                indent = len(raw) - len(raw.lstrip())
                if base_indent is None:
                    base_indent = indent
                if indent < (base_indent or 0) or (raw.lstrip().startswith("- ") and indent == base_indent):
                    break
                block.append(raw.strip())
                i += 1
            fold = value.startswith(">")
            joined = " ".join(b for b in block if b) if fold else "\n".join(block)
            out[key] = joined.strip()
            continue
        if value == "":
            # block mapping or empty; peek for a nested `description:`? keep simple: skip
            i += 1
            continue
        out[key] = _yaml_unquote(value)
        i += 1
    return out.get("name", ""), out.get("description", "")


def _yaml_unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    # strip trailing comment on unquoted values
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value


# ---------------------------------------------------------------------------
# config.yaml surgical editor for skills.disabled (bare names)
# ---------------------------------------------------------------------------


class ConfigEditError(Exception):
    pass


_RE_TOP_SKILLS = re.compile(r"^skills:\s*(.*)$")
_RE_DISABLED_KEY = re.compile(r"^(\s+)disabled:\s*(.*)$")
_RE_LIST_ITEM = re.compile(r"^(\s*)-\s*(.+)$")


def _split_flow(inner: str) -> list[str]:
    """Split a flow-sequence/map body on top-level commas (bracket-aware)."""
    parts: list[str] = []
    buf = ""
    quote = None
    depth = 0
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf += ch
        elif ch in "[{":
            depth += 1
            buf += ch
        elif ch in "]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def _flow_items(value: str) -> list[str]:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        raise ConfigEditError(f"unexpected disabled value: {value!r}")
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [_yaml_unquote(p) for p in _split_flow(inner)]


def _find_top_skills(lines: list[str]) -> tuple[int | None, str]:
    for i, ln in enumerate(lines):
        m = _RE_TOP_SKILLS.match(ln)
        if m:
            return i, m.group(1).strip()
    return None, ""


def _find_disabled(lines: list[str], skills_idx: int) -> tuple[int | None, str, str]:
    """Locate `disabled:` inside the `skills:` block. Returns (idx, indent, value)."""
    for j in range(skills_idx + 1, len(lines)):
        ln = lines[j]
        if ln[:1].strip() and not ln.startswith((" ", "\t")):
            break  # next top-level key — skills block ended
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        m = _RE_DISABLED_KEY.match(ln)
        if m:
            return j, m.group(1), m.group(2).strip()
    return None, "", ""


def _block_list_items(lines: list[str], start: int) -> tuple[list[tuple[int, str]], int]:
    """Collect `  - name` items following lines[start] (the `disabled:` line).

    Returns ([(line_index, bare_name)], item_indent).
    """
    items: list[tuple[int, str]] = []
    item_indent = -1
    for k in range(start + 1, len(lines)):
        ln = lines[k]
        if not ln.strip() or ln.strip().startswith("#"):
            # blank/comment — only continues the list if a later item shares indent
            items.append((k, ""))
            continue
        m = _RE_LIST_ITEM.match(ln)
        if m:
            indent = len(m.group(1))
            if item_indent < 0:
                item_indent = indent
            if indent != item_indent:
                break
            items.append((k, _yaml_unquote(m.group(2))))
        else:
            break
    cleaned = [(idx, name) for idx, name in items if name != ""]
    return cleaned, item_indent


def parse_disabled(text: str) -> set[str]:
    """Parse the set of bare skill names under `skills.disabled` (tolerant)."""
    lines = text.splitlines()
    skills_idx, tail = _find_top_skills(lines)
    if skills_idx is None:
        return set()
    if tail.startswith("{"):
        for part in _split_flow(tail.strip()[1:-1]):
            k, _, v = part.partition(":")
            if k.strip() == "disabled":
                try:
                    return set(_flow_items(v.strip()))
                except ConfigEditError:
                    return set()
        return set()
    if tail.startswith("["):
        return set()  # skills: [...] is a list, not a map with disabled
    dis_idx, _, value = _find_disabled(lines, skills_idx)
    if dis_idx is None:
        return set()
    if value.startswith("["):
        try:
            return set(_flow_items(value))
        except ConfigEditError:
            return set()
    if value and not value.startswith("#"):
        if value in ("null", "~"):
            return set()
        return {_yaml_unquote(value)}  # scalar shorthand
    items, _ = _block_list_items(lines, dis_idx)
    return {name for _, name in items}


def _detect_child_indent(lines: list[str], top_idx: int) -> str:
    for j in range(top_idx + 1, len(lines)):
        ln = lines[j]
        if ln[:1].strip() and not ln.startswith((" ", "\t")):
            break
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        stripped = ln.lstrip(" \t")
        return ln[: len(ln) - len(stripped)]
    return "  "


def set_disabled_member(text: str, name: str, *, add: bool) -> str:
    """Add/remove `name` from skills.disabled, preserving all other formatting.

    Raises ConfigEditError when the resulting file would not re-parse to the
    intended membership (self-check).
    """
    if "\r\n" in text:
        lf = text.replace("\r\n", "\n")
        out = _set_disabled_member_lf(lf, name, add=add)
        return out.replace("\n", "\r\n")
    return _set_disabled_member_lf(text, name, add=add)


def _set_disabled_member_lf(text: str, name: str, *, add: bool) -> str:
    quoted = json.dumps(name)  # double-quoted, escapes unicode safely
    lines = text.splitlines()
    skills_idx, tail = _find_top_skills(lines)

    fresh_block = ["skills:", "  disabled:", f"    - {quoted}"]
    if skills_idx is None:
        if not add:
            return text
        if text and text.strip() and not text.endswith("\n"):
            text += "\n"
        if text.strip():
            text += "\n"
        return text + "\n".join(fresh_block) + "\n"

    dis_idx, dis_indent, value = _find_disabled(lines, skills_idx)
    if dis_idx is None:
        if not add:
            return text
        child = _detect_child_indent(lines, skills_idx)
        new_lines = lines[: skills_idx + 1]
        new_lines.append(f"{child}disabled:")
        new_lines.append(f"{child}  - {quoted}")
        new_lines.extend(lines[skills_idx + 1 :])
        return _finish("\n".join(new_lines) + "\n", name, add)

    # inline flow list:  disabled: [a, "b"]
    if value.startswith("["):
        try:
            current = _flow_items(value)
        except ConfigEditError as exc:
            raise ConfigEditError(f"cannot parse inline disabled list: {exc}") from exc
        if add and name in current:
            return text
        if not add and name not in current:
            return text
        target = [c for c in current if c != name]
        if add:
            target.append(name)
        rendered = "[" + ", ".join(json.dumps(t) for t in target) + "]"
        new_lines = lines[:dis_idx]
        new_lines.append(f"{dis_indent}disabled: {rendered}")
        new_lines.extend(lines[dis_idx + 1 :])
        return _finish("\n".join(new_lines) + "\n", name, add)

    # scalar shorthand (disabled: foo) → convert to block list
    if value and not value.startswith("#") and value not in ("null", "~"):
        current = [_yaml_unquote(value)]
        new_lines = lines[:dis_idx]
        new_lines.append(f"{dis_indent}disabled:")
        for c in current:
            new_lines.append(f"{dis_indent}  - {json.dumps(c)}")
        if add and name not in current:
            new_lines.append(f"{dis_indent}  - {quoted}")
        if not add:
            new_lines = [ln for ln in new_lines if not ln.endswith(f"- {quoted}")]
        new_lines.extend(lines[dis_idx + 1 :])
        return _finish("\n".join(new_lines) + "\n", name, add)

    # empty/null or block list
    items, item_indent = _block_list_items(lines, dis_idx)
    names = [n for _, n in items]
    if add and name in names:
        return text
    if not add and name not in names:
        return text

    if add and not names:
        # empty, null, or bare `disabled:` — replace the line itself so a
        # scalar like `null` can't orphan the new item below it.
        new_lines = lines[:dis_idx]
        new_lines.append(f"{dis_indent}disabled:")
        new_lines.append(f"{dis_indent}  - {quoted}")
        new_lines.extend(lines[dis_idx + 1 :])
    elif add:
        last_item_line = items[-1][0]
        item_pad = " " * item_indent if item_indent > 0 else dis_indent + "  "
        new_lines = lines[: last_item_line + 1]
        new_lines.append(f"{item_pad}- {quoted}")
        new_lines.extend(lines[last_item_line + 1 :])
    else:  # remove
        drop = {idx for idx, n in items if n == name}
        if names and len(names) == 1:
            # list becomes empty — rewrite the block cleanly as `disabled: []`,
            # dropping only up to the last REAL item (trailing comments survive)
            last_real = max(idx for idx, n in items if n)
            new_lines = lines[:dis_idx]
            new_lines.append(f"{dis_indent}disabled: []")
            new_lines.extend(lines[last_real + 1 :])
        else:
            new_lines = [ln for idx, ln in enumerate(lines) if idx not in drop]
    return _finish("\n".join(new_lines) + "\n", name, add)


def _finish(new_text: str, name: str, add: bool) -> str:
    got = parse_disabled(new_text)
    want = got | {name} if add else got - {name}
    if got != want:
        raise ConfigEditError(
            f"self-check failed after editing skills.disabled (wanted {'+' if add else '-'}{name!r}, got {sorted(got)!r})"
        )
    return new_text


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------


class SkillsToggleError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


class SkillsToggleCore:
    """All state/mutation logic. Hermes root + tools config injected for tests."""

    def __init__(self, home: Path, tools: dict, log_path: Path | None = None):
        self.home = home
        self.skills_root = home / "skills"
        # macOS /var → /private/var and similar symlinks: containment checks
        # must compare fully-resolved paths.
        self.skills_root_resolved = self.skills_root.resolve()
        self.config_path = home / "config.yaml"
        self.tools = tools  # id -> {label, dir?, special?, present-cache}
        self.log_path = log_path
        self._lock = threading.RLock()
        self._cache: dict | None = None
        self._cache_sig: tuple | None = None
        self._cache_at = 0.0
        self._generation = 0

    # -- construction ------------------------------------------------------

    @classmethod
    def build_default(cls) -> "SkillsToggleCore":
        home = hermes_home()
        tools = load_tools_config(home)
        plugin_root = Path(__file__).resolve().parent.parent
        return cls(home, tools, log_path=plugin_root / "data" / "mutations.log")

    # -- logging -----------------------------------------------------------

    def _log(self, **rec: object) -> None:
        if not self.log_path:
            return
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        rec.setdefault("plugin", PLUGIN_ID)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass  # logging must never break a mutation

    # -- scanning ----------------------------------------------------------

    def _scan_skills(self) -> dict[str, dict]:
        """id ('category/name') -> {name, category, dir, description}."""
        skills: dict[str, dict] = {}
        if not self.skills_root.is_dir():
            return skills
        for cat_dir in sorted(self.skills_root.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue
            for skill_dir in sorted(cat_dir.iterdir()):
                if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                try:
                    raw = skill_md.read_text(encoding="utf-8", errors="replace")[:8192]
                except OSError:
                    raw = ""
                name, description = parse_skill_markdown(raw)
                skills[f"{cat_dir.name}/{skill_dir.name}"] = {
                    "name": skill_dir.name,
                    "category": cat_dir.name,
                    "front_name": name or skill_dir.name,
                    "dir": skill_dir,
                    "description": description.strip(),
                }
        return skills

    def _disabled_set(self) -> set[str]:
        try:
            text = self.config_path.read_text(encoding="utf-8") if self.config_path.is_file() else ""
        except OSError:
            return set()
        return parse_disabled(text)

    def tool_dir(self, tool_id: str) -> Path | None:
        tool = self.tools.get(tool_id)
        if not tool or "dir" not in tool:
            return None
        try:
            expanded = expand_path(tool["dir"]) if isinstance(tool["dir"], str) else tool["dir"]
        except ValueError:
            return None  # empty expansion — treat as unconfigured, never CWD
        return expanded

    def _tool_states(self, skill: dict, disabled: set[str]) -> dict:
        states: dict[str, dict] = {}
        for tool_id in self.tools:
            states[tool_id] = self._one_state(skill, tool_id, disabled)
        return states

    def _one_state(self, skill: dict, tool_id: str, disabled: set[str]) -> dict:
        if tool_id == "hermes":
            return {"state": "disabled" if skill["name"] in disabled else "enabled"}
        tool_dir = self.tool_dir(tool_id)
        if tool_dir is None:
            return {"state": "missing"}
        link = tool_dir / skill["name"]
        if link.is_symlink():
            target = os.readlink(link)
            base = Path(target) if os.path.isabs(target) else (link.parent / target)
            resolved = base.resolve()
            if same_path(resolved, skill["dir"]):
                return {"state": "enabled", "target": target}
            if not resolved.exists():
                return {"state": "broken-link", "target": target}
            return {"state": "foreign-link", "target": target}
        if link.exists():
            return {"state": "unmanaged-dir"}
        return {"state": "missing"}

    # -- cache -------------------------------------------------------------

    def _signature(self) -> tuple:
        parts: list = [self._generation]
        try:
            cat_dirs = sorted(p for p in self.skills_root.iterdir() if p.is_dir()) if self.skills_root.is_dir() else []
        except OSError:
            cat_dirs = []
        probes = [self.skills_root, self.config_path, user_config_path(self.home)] + cat_dirs
        for tool_id in self.tools:
            d = self.tool_dir(tool_id)
            if d is not None:
                probes.append(d)
        for p in probes:
            try:
                parts.append((str(p), p.stat().st_mtime_ns))
            except OSError:
                parts.append((str(p), None))
        return tuple(parts)

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._cache = None

    # -- validation --------------------------------------------------------

    def _validate_skill(self, skill_id: object) -> str:
        if not isinstance(skill_id, str) or not _SKILL_ID_RE.match(skill_id):
            raise SkillsToggleError(
                f"invalid skill id {skill_id!r} — expected 'category/name' from the skills index", "invalid-skill"
            )
        if any(seg in (".", "..") for seg in skill_id.split("/")):
            raise SkillsToggleError(f"invalid skill id {skill_id!r} — path segments may not be '.' or '..'", "invalid-skill")
        if skill_id not in self._scan_skills():
            raise SkillsToggleError(
                f"unknown skill {skill_id!r} — not present under {self.skills_root}", "unknown-skill"
            )
        return skill_id

    def _validate_tool(self, tool_id: object) -> str:
        if not isinstance(tool_id, str) or tool_id not in self.tools:
            raise SkillsToggleError(f"unknown tool {tool_id!r}", "unknown-tool")
        return tool_id

    # -- routes: read ------------------------------------------------------

    def state(self) -> dict:
        with self._lock:
            sig = self._signature()
            now = time.time()
            if self._cache is not None and self._cache_sig == sig and (now - self._cache_at) < CACHE_TTL_SECONDS:
                return self._cache

            skills = self._scan_skills()
            disabled = self._disabled_set()
            tools_meta = []
            for tool_id, tool in self.tools.items():
                d = self.tool_dir(tool_id)
                tools_meta.append(
                    {
                        "id": tool_id,
                        "label": tool.get("label", tool_id),
                        "dir": str(d) if d is not None else None,
                        "present": True if d is None else d.is_dir(),  # hermes has no dir requirement
                        "special": tool.get("special"),
                    }
                )
            payload_skills = []
            unlinked = 0
            for sid, skill in sorted(skills.items()):
                states = self._tool_states(skill, disabled)
                # "unlinked" is about consumer symlinks — hermes' config state
                # is a separate axis and must not mask a missing link.
                linked_any = any(v["state"] == "enabled" for tid, v in states.items() if tid != "hermes")
                if not linked_any:
                    unlinked += 1
                desc = skill["description"]
                if len(desc) > DESCRIPTION_TRUNC:
                    desc = desc[: DESCRIPTION_TRUNC - 1].rstrip() + "…"
                payload_skills.append(
                    {
                        "id": sid,
                        "name": skill["name"],
                        "category": skill["category"],
                        "description": desc,
                        "tools": states,
                    }
                )
            payload = {
                "ok": True,
                "plugin_version": PLUGIN_VERSION,
                "hermes_home": str(self.home),
                "skills_root": str(self.skills_root),
                "skills_root_exists": self.skills_root.is_dir(),
                "tools": tools_meta,
                "skills": payload_skills,
                "counts": {"skills": len(payload_skills), "unlinked": unlinked},
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self._cache = payload
            self._cache_sig = sig
            self._cache_at = time.time()
            return payload

    def detail(self, skill_id: str) -> dict:
        skill_id = self._validate_skill(skill_id)
        skills = self._scan_skills()
        skill = skills[skill_id]
        skill_md = skill["dir"] / "SKILL.md"
        markdown = ""
        try:
            markdown = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        states = self._tool_states(skill, self._disabled_set())
        return {
            "ok": True,
            "skill": skill_id,
            "name": skill["name"],
            "category": skill["category"],
            "description": skill["description"],
            "markdown": markdown,
            "path": str(skill["dir"]),
            "tools": states,
        }

    def diff(self) -> dict:
        payload = self.state()
        disabled = self._disabled_set()
        skills = self._scan_skills()
        unlinked = []
        for entry in payload["skills"]:
            states = entry["tools"]
            if all(v["state"] != "enabled" for tid, v in states.items() if tid != "hermes"):
                unlinked.append(entry["id"])
        broken: list[dict] = []
        foreign: list[dict] = []
        unmanaged: list[dict] = []
        for tool_id in self.tools:
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None or not tool_dir.is_dir():
                continue
            try:
                entries = sorted(tool_dir.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink():
                    target = os.readlink(entry)
                    base = Path(target) if os.path.isabs(target) else (entry.parent / target)
                    resolved = base.resolve()
                    if not resolved.exists():
                        broken.append({"tool": tool_id, "name": entry.name, "target": target})
                    elif not is_inside(resolved, self.skills_root_resolved):
                        foreign.append({"tool": tool_id, "name": entry.name, "target": target})
                elif entry.is_dir() and entry.name in {s["name"] for s in skills.values()}:
                    unmanaged.append({"tool": tool_id, "name": entry.name})
        return {
            "ok": True,
            "unlinked": unlinked,
            "broken": broken,
            "foreign": foreign,
            "unmanaged": unmanaged,
            "counts": {
                "unlinked": len(unlinked),
                "broken": len(broken),
                "foreign": len(foreign),
                "unmanaged": len(unmanaged),
            },
        }

    # -- routes: mutate ----------------------------------------------------

    def _backup(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak.skills-toggle.{stamp}")
        n = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak.skills-toggle.{stamp}-{n}")
            n += 1
        shutil.copy2(path, backup)
        return str(backup)

    def _backup_config(self) -> str | None:
        if not self.config_path.is_file():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.config_path.with_name(f"config.yaml.bak.skills-toggle.{stamp}")
        n = 1
        while backup.exists():
            backup = self.config_path.with_name(f"config.yaml.bak.skills-toggle.{stamp}-{n}")
            n += 1
        shutil.copy2(self.config_path, backup)
        return str(backup)

    def _hermes_toggle(self, skill_name: str, enabled: bool) -> str:
        text = ""
        if self.config_path.is_file():
            try:
                text = self.config_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise SkillsToggleError(f"cannot read {self.config_path}: {exc}", "config-unreadable") from exc
        try:
            new_text = set_disabled_member(text, skill_name, add=not enabled)
        except ConfigEditError as exc:
            raise SkillsToggleError(f"config.yaml edit refused: {exc}", "config-edit") from exc
        if new_text == text:
            return "noop"
        backup = self._backup_config()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(new_text, encoding="utf-8")
        self._log(action="config-edit", tool="hermes", skill=skill_name, enabled=enabled, backup=backup)
        return "config-updated"

    def _link_tool(self, skill: dict, tool_id: str, enabled: bool) -> tuple[str, dict]:
        """Enable/disable one symlink. Returns (action, new_state)."""
        tool_dir = self.tool_dir(tool_id)
        if tool_dir is None:
            raise SkillsToggleError(f"tool {tool_id} has no target dir configured", "no-dir")
        link = tool_dir / skill["name"]
        skill_dir_resolved = skill["dir"].resolve()  # compared via same_path

        def link_state() -> tuple[str, str | None]:
            if link.is_symlink():
                target = os.readlink(link)
                base = Path(target) if os.path.isabs(target) else (link.parent / target)
                resolved = base.resolve()
                if same_path(resolved, skill_dir_resolved):
                    return "enabled", target
                if not resolved.exists():
                    return "broken-link", target
                return "foreign-link", target
            if link.exists():
                return "unmanaged-dir", None
            return "missing", None

        state, target = link_state()

        if enabled:
            if state == "enabled":
                return "noop", {"state": "enabled", "target": target}
            if state == "foreign-link":
                raise SkillsToggleError(
                    f"{link} is a symlink to {target} (outside this skill) — resolve it manually before enabling",
                    "foreign-link",
                )
            if state == "unmanaged-dir":
                raise SkillsToggleError(
                    f"{link} is a real directory/file, not a symlink — refusing to overwrite (never delete real dirs)",
                    "unmanaged-dir",
                )
            created_dir = False
            if not tool_dir.is_dir():
                if tool_dir.exists():
                    raise SkillsToggleError(
                        f"{tool_dir} exists but is not a directory — refusing to replace it", "not-a-dir"
                    )
                try:
                    tool_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise SkillsToggleError(f"cannot create {tool_dir}: {exc}", "mkdir-failed") from exc
                created_dir = True
                self._log(action="create-tool-dir", tool=tool_id, dir=str(tool_dir))
            if state == "broken-link":
                link.unlink()
            try:
                os.symlink(str(skill_dir_resolved), str(link))
            except (OSError, NotImplementedError) as exc:
                raise SkillsToggleError(
                    f"could not create symlink at {link}: {exc} "
                    "(on Windows, enable Developer Mode or run elevated)",
                    "symlink-unsupported",
                ) from exc
            if created_dir:
                action = "created-dir+linked"
            elif state == "broken-link":
                action = "repaired-link"
            else:
                action = "linked"
            return action, {"state": "enabled", "target": str(skill_dir_resolved)}

        # disabling
        if state == "missing":
            return "noop", {"state": "missing"}
        if state == "enabled" or state == "broken-link":
            # broken links are only removed when they point inside our skills tree
            if state == "broken-link":
                base = Path(target) if os.path.isabs(target) else (link.parent / target)
                if not is_inside(base.resolve(), self.skills_root_resolved):
                    raise SkillsToggleError(
                        f"{link} points at {target} which is outside the skills tree — refusing", "foreign-link"
                    )
            link.unlink()
            return "unlinked", {"state": "missing"}
        if state == "foreign-link":
            raise SkillsToggleError(f"{link} is a foreign symlink ({target}) — refusing to remove", "foreign-link")
        raise SkillsToggleError(f"{link} is a real directory/file — refusing to remove", "unmanaged-dir")

    def toggle(self, skill_id: object, tool_id: object, enabled: object) -> dict:
        with self._lock:
            skill_id = self._validate_skill(skill_id)
            tool_id = self._validate_tool(tool_id)
            if not isinstance(enabled, bool):
                raise SkillsToggleError("'enabled' must be a boolean", "invalid-body")
            skills = self._scan_skills()
            skill = skills[skill_id]
            before = self._one_state(skill, tool_id, self._disabled_set())["state"]
            if tool_id == "hermes":
                action = self._hermes_toggle(skill["name"], enabled)
                after = "enabled" if enabled else "disabled"
            else:
                action, new_state = self._link_tool(skill, tool_id, enabled)
                after = new_state["state"]
            self._log(action="toggle", skill=skill_id, tool=tool_id, enabled=enabled, **{"from": before, "to": after})
            self.invalidate()
            return {"ok": True, "skill": skill_id, "tool": tool_id, "enabled": enabled, "state": after, "action": action}

    def toggle_bulk(self, skill_ids: object, tool_id: object, enabled: object) -> dict:
        if not isinstance(skill_ids, list) or not skill_ids:
            raise SkillsToggleError("'skills' must be a non-empty list of skill ids", "invalid-body")
        results = []
        for sid in skill_ids:
            try:
                res = self.toggle(sid, tool_id, enabled)
                results.append({"skill": sid, "ok": True, "state": res["state"]})
            except SkillsToggleError as exc:
                results.append({"skill": sid, "ok": False, "error": str(exc), "code": exc.code})
        changed = sum(1 for r in results if r.get("ok") and r.get("state") in ("enabled", "disabled", "missing"))
        return {"ok": True, "results": results, "changed": changed, "failed": len(results) - sum(1 for r in results if r["ok"])}

    def repair(self, skill_id: object, tool_id: object) -> dict:
        with self._lock:
            skill_id = self._validate_skill(skill_id)
            tool_id = self._validate_tool(tool_id)
            if tool_id == "hermes":
                raise SkillsToggleError("hermes state lives in config.yaml — use toggle instead", "not-a-link")
            skills = self._scan_skills()
            skill = skills[skill_id]
            state = self._one_state(skill, tool_id, self._disabled_set())["state"]
            if state == "enabled":
                self._log(action="repair", skill=skill_id, tool=tool_id, result="noop")
                return {"ok": True, "skill": skill_id, "tool": tool_id, "state": "enabled", "action": "noop"}
            if state in ("foreign-link", "unmanaged-dir"):
                raise SkillsToggleError(
                    f"cannot repair {state} at {self.tool_dir(tool_id) / skill['name']} — resolve manually", state
                )
            action, new_state = self._link_tool(skill, tool_id, True)
            self._log(action="repair", skill=skill_id, tool=tool_id, result="fixed")
            self.invalidate()
            return {"ok": True, "skill": skill_id, "tool": tool_id, "state": new_state["state"], "action": action}

    def repair_all(self) -> dict:
        with self._lock:
            diff = self.diff()
            fixed: list[dict] = []
            unfixable: list[dict] = []
            skills = self._scan_skills()
            by_name = {s["name"]: sid for sid, s in skills.items()}
            for item in diff["broken"]:
                # Map the link back to a skill by NAME (the link name is the
                # skill name by convention) — the stale target may point at a
                # moved or deleted skill, which is exactly why it's broken.
                sid = by_name.get(item["name"])
                if sid is None:
                    # fallback: stale target inside the tree, e.g. renamed category
                    tool_dir = self.tool_dir(item["tool"])
                    if tool_dir is None:
                        unfixable.append({**item, "reason": "tool dir not configured"})
                        continue
                    link = tool_dir / item["name"]
                    target = item["target"]
                    base = Path(target) if os.path.isabs(target) else (link.parent / target)
                    resolved = base.resolve()
                    try:
                        rel = resolved.relative_to(self.skills_root)
                    except ValueError:
                        unfixable.append({**item, "reason": "no skill with this name; target outside skills tree"})
                        continue
                    if len(rel.parts) != 2:
                        unfixable.append({**item, "reason": "target is not <category>/<name>"})
                        continue
                    sid = f"{rel.parts[0]}/{rel.parts[1]}"
                    if sid not in skills:
                        unfixable.append({**item, "reason": f"target {sid} does not exist anymore"})
                        continue
                try:
                    res = self.repair(sid, item["tool"])
                    fixed.append({"skill": sid, "tool": item["tool"], "state": res.get("state")})
                except SkillsToggleError as exc:
                    unfixable.append({**item, "reason": str(exc)})
            return {"ok": True, "fixed": fixed, "unfixable": unfixable}

    def ensure_tool_dir(self, tool_id: object) -> dict:
        tool_id = self._validate_tool(tool_id)
        if tool_id == "hermes":
            raise SkillsToggleError("hermes has no skills dir (config.yaml based)", "no-dir")
        tool_dir = self.tool_dir(tool_id)
        if tool_dir is None:
            raise SkillsToggleError(f"tool {tool_id} has no target dir configured", "no-dir")
        if tool_dir.is_dir():
            return {"ok": True, "tool": tool_id, "dir": str(tool_dir), "created": False}
        if tool_dir.exists():
            raise SkillsToggleError(f"{tool_dir} exists but is not a directory — refusing to replace it", "not-a-dir")
        try:
            tool_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SkillsToggleError(f"cannot create {tool_dir}: {exc}", "mkdir-failed") from exc
        self._log(action="create-tool-dir", tool=tool_id, dir=str(tool_dir))
        self.invalidate()
        return {"ok": True, "tool": tool_id, "dir": str(tool_dir), "created": True}

    # -- v2: import / adoption / drift ---------------------------------------

    @staticmethod
    def _hash_file(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""

    def _classify_entry(self, entry: Path, skills: dict) -> dict | None:
        """Classify one entry in a tool skills dir for the import/drift views."""
        try:
            if entry.is_symlink():
                target = os.readlink(entry)
                base = Path(target) if os.path.isabs(target) else (entry.parent / target)
                resolved = base.resolve()
                if not resolved.exists():
                    return {"name": entry.name, "kind": "broken-link", "target": target}
                inside = self.skills_root_resolved in resolved.parents
                sid = next(
                    (s for s in skills.values() if s["dir"].resolve() == resolved), None
                )
                if inside and sid:
                    return {
                        "name": entry.name,
                        "kind": "managed",
                        "skill_id": f"{sid['category']}/{sid['name']}",
                        "target": target,
                    }
                if inside:
                    return {"name": entry.name, "kind": "managed-mismatch", "target": target}
                return {"name": entry.name, "kind": "foreign-link", "target": target}
            if entry.is_dir():
                skill_md = entry / "SKILL.md"
                if skill_md.is_file():
                    _, description = parse_skill_markdown(
                        skill_md.read_text(encoding="utf-8", errors="replace")[:8192]
                    )
                    info = {
                        "name": entry.name,
                        "kind": "unmanaged-skill",
                        "path": str(entry),
                        "description": description.strip()[:DESCRIPTION_TRUNC],
                        "hash": self._hash_file(skill_md),
                        "mtime": entry.stat().st_mtime,
                    }
                    existing = next(
                        (s for s in skills.values() if s["name"] == entry.name), None
                    )
                    if existing:
                        info["conflict"] = True
                        info["skill_id"] = f"{existing['category']}/{existing['name']}"
                        info["hermes_hash"] = self._hash_file(existing["dir"] / "SKILL.md")
                        info["drifted"] = info["hash"] != info["hermes_hash"]
                    return info
                return {"name": entry.name, "kind": "unmanaged-dir", "path": str(entry)}
            return None
        except OSError:
            return None

    def import_scan(self) -> dict:
        skills = self._scan_skills()
        per_tool = []
        for tool_id in self.tools:
            if tool_id == "hermes":
                continue
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None or not tool_dir.is_dir():
                per_tool.append({"tool": tool_id, "present": False, "entries": []})
                continue
            entries = []
            try:
                children = sorted(tool_dir.iterdir())
            except OSError:
                children = []
            for entry in children:
                info = self._classify_entry(entry, skills)
                if info:
                    entries.append(info)
            per_tool.append(
                {
                    "tool": tool_id,
                    "present": True,
                    "dir": str(tool_dir),
                    "entries": entries,
                    "adoptable": sum(1 for e in entries if e["kind"] == "unmanaged-skill"),
                    "drifted": sum(1 for e in entries if e.get("drifted")),
                }
            )
        adoptable = sum(t.get("adoptable", 0) for t in per_tool)
        drifted = sum(t.get("drifted", 0) for t in per_tool)
        return {"ok": True, "tools": per_tool, "counts": {"adoptable": adoptable, "drifted": drifted}}

    def import_apply(self, tool_id: object, names: object, category: str = "imported") -> dict:
        """Adopt unmanaged skills: copy into the skills tree, then replace the
        tool's real dir with a symlink — the original is PRESERVED as a
        timestamped `<name>.skills-toggle-backup-<ts>` sibling (never deleted)."""
        tool_id = self._validate_tool(tool_id)
        if tool_id == "hermes":
            raise SkillsToggleError("hermes has no importable dir", "no-dir")
        if not isinstance(names, list) or not names:
            raise SkillsToggleError("'names' must be a non-empty list", "invalid-body")
        if not isinstance(category, str) or not re.match(r"^[^/\0]+$", category) or category in (".", ".."):
            raise SkillsToggleError(f"invalid category {category!r}", "invalid-category")
        skills = self._scan_skills()
        tool_dir = self.tool_dir(tool_id)
        if tool_dir is None or not tool_dir.is_dir():
            raise SkillsToggleError(f"tool dir for {tool_id} is missing", "absent-dir")

        # validate every requested name against a fresh classification; a name
        # that already exists anywhere in the tree is a CONFLICT — adopting it
        # would create a duplicate-named skill, so refuse (the drift flow is
        # the resolution path), never merge.
        tree_names = {s["name"] for s in skills.values()}
        adoptable = {
            e["name"]: e
            for e in (
                self._classify_entry(p, skills) for p in sorted(tool_dir.iterdir())
            )
            if e and e["kind"] == "unmanaged-skill"
        }
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        results = []
        for name in names:
            if not isinstance(name, str) or name not in adoptable:
                results.append({"name": name, "ok": False, "error": "not an adoptable skill dir"})
                continue
            if name in tree_names:
                results.append(
                    {
                        "name": name,
                        "ok": False,
                        "error": "a skill with this name already exists in the skills tree — resolve via drift push instead",
                        "conflict": True,
                    }
                )
                continue
            dest_dir = self.skills_root / category
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / name
            if dest.exists():
                results.append({"name": name, "ok": False, "error": f"destination {dest} already exists"})
                continue
            src = tool_dir / name
            try:
                shutil.copytree(src, dest, symlinks=True)
            except OSError as exc:
                results.append({"name": name, "ok": False, "error": f"copy failed: {exc}"})
                continue
            backup = tool_dir / f"{name}.skills-toggle-backup-{stamp}"
            try:
                os.rename(src, backup)
                os.symlink(str(dest.resolve()), str(tool_dir / name))
            except OSError as exc:
                # put the original back exactly where it was — the tool must
                # never lose the entry because the symlink step failed
                try:
                    if not (tool_dir / name).exists():
                        os.rename(backup, src)
                except OSError:
                    pass  # original remains recoverable at the backup path
                shutil.rmtree(dest, ignore_errors=True)
                results.append({"name": name, "ok": False, "error": f"link swap failed: {exc}"})
                continue
            self._log(action="import", tool=tool_id, skill=f"{category}/{name}", backup=str(backup))
            results.append(
                {"name": name, "ok": True, "skill": f"{category}/{name}", "backup": str(backup)}
            )
        self.invalidate()
        changed = sum(1 for r in results if r["ok"])
        return {"ok": True, "tool": tool_id, "results": results, "adopted": changed, "failed": len(results) - changed}

    def drift(self) -> dict:
        """Same-name skills where the tool's copy differs from the Hermes source."""
        skills = self._scan_skills()
        by_name = {}
        for s in skills.values():
            by_name.setdefault(s["name"], s)
        items = []
        for tool_id in self.tools:
            if tool_id == "hermes":
                continue
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None or not tool_dir.is_dir():
                continue
            try:
                children = sorted(tool_dir.iterdir())
            except OSError:
                continue
            for entry in children:
                if entry.is_symlink() or not entry.is_dir():
                    continue
                skill_md = entry / "SKILL.md"
                if not skill_md.is_file():
                    continue
                existing = by_name.get(entry.name)
                if not existing:
                    continue
                external_hash = self._hash_file(skill_md)
                hermes_hash = self._hash_file(existing["dir"] / "SKILL.md")
                if external_hash == hermes_hash:
                    continue
                items.append(
                    {
                        "tool": tool_id,
                        "name": entry.name,
                        "skill_id": f"{existing['category']}/{existing['name']}",
                        "external_path": str(entry),
                        "hermes_path": str(existing["dir"]),
                        "external_hash": external_hash,
                        "hermes_hash": hermes_hash,
                        "external_mtime": entry.stat().st_mtime,
                        "hermes_mtime": (existing["dir"] / "SKILL.md").stat().st_mtime,
                    }
                )
        return {"ok": True, "drifted": items, "count": len(items)}

    def drift_push(self, tool_id: object, name: object) -> dict:
        """Push the Hermes-canonical copy out to a tool (D31): the tool's real
        dir for `name` is backup-renamed (NEVER deleted) and replaced with a
        symlink into the skills tree. Also resolves adoption conflicts, since
        both are 'same-name real dir in a tool dir' shapes."""
        with self._lock:
            tool_id = self._validate_tool(tool_id)
            if tool_id == "hermes":
                raise SkillsToggleError("hermes already is the source of truth", "no-dir")
            if not isinstance(name, str) or "/" in name or name in (".", "..") or not name.strip():
                raise SkillsToggleError(f"invalid skill name {name!r}", "invalid-name")
            skills = self._scan_skills()
            existing = next((s for s in skills.values() if s["name"] == name), None)
            if not existing:
                raise SkillsToggleError(f"no skill named {name!r} in the skills tree", "unknown-skill")
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None or not tool_dir.is_dir():
                raise SkillsToggleError(f"tool dir for {tool_id} is missing", "absent-dir")
            entry = tool_dir / name
            if entry.is_symlink():
                return {"ok": True, "tool": tool_id, "name": name, "action": "noop", "state": "managed"}
            if not entry.is_dir() or not (entry / "SKILL.md").is_file():
                raise SkillsToggleError(
                    f"{entry} is not a skill directory — refusing to touch it", "unmanaged-dir"
                )
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = tool_dir / f"{name}.skills-toggle-backup-{stamp}"
            try:
                os.rename(entry, backup)
                os.symlink(str(existing["dir"].resolve()), str(entry))
            except OSError as exc:
                try:
                    if not entry.exists():
                        os.rename(backup, entry)
                except OSError:
                    pass  # original remains recoverable at the backup path
                raise SkillsToggleError(f"drift push failed: {exc}", "drift-push-failed") from exc
            self._log(action="drift-push", tool=tool_id, skill=f"{existing['category']}/{name}", backup=str(backup))
            self.invalidate()
            return {
                "ok": True,
                "tool": tool_id,
                "name": name,
                "skill": f"{existing['category']}/{name}",
                "action": "pushed",
                "backup": str(backup),
                "tool_backup": str(backup),
            }

    # -- v3: conflict resolution (PROPOSAL-v3 #1, D31 completion) --------------

    def _conflict_context(self, tool_id: object, name: object, require_in_tree: bool):
        if not isinstance(name, str) or "/" in name or name in (".", "..") or not name.strip():
            raise SkillsToggleError(f"invalid skill name {name!r}", "invalid-name")
        tool_id = self._validate_tool(tool_id)
        if tool_id == "hermes":
            raise SkillsToggleError("hermes already is the source of truth", "no-dir")
        skills = self._scan_skills()
        existing = next((sk for sk in skills.values() if sk["name"] == name), None)
        if require_in_tree and not existing:
            raise SkillsToggleError(f"no skill named {name!r} in the skills tree", "unknown-skill")
        tool_dir = self.tool_dir(tool_id)
        if tool_dir is None or not tool_dir.is_dir():
            raise SkillsToggleError(f"tool dir for {tool_id} is missing", "absent-dir")
        entry = tool_dir / name
        if entry.is_symlink() or not entry.is_dir() or not (entry / "SKILL.md").is_file():
            raise SkillsToggleError(
                f"{entry} is not a skill directory — refusing to touch it", "unmanaged-dir"
            )
        return existing, tool_dir, entry

    def conflict_pull(self, tool_id: object, name: object) -> dict:
        """Use the tool's copy as the new canonical source (D31 'pull external
        in', completed): the hermes source is backed up dotted inside its
        category, the external copy becomes canonical, and the tool entry is
        swapped to a symlink. Originals are never deleted."""
        with self._lock:
            existing, tool_dir, entry = self._conflict_context(tool_id, name, require_in_tree=True)
            hermes_dir = existing["dir"]
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            hermes_backup = hermes_dir.parent / f".skills-toggle-backup-{name}-{stamp}"
            tool_backup = tool_dir / f"{name}.skills-toggle-backup-{stamp}"
            try:
                os.rename(hermes_dir, hermes_backup)
            except OSError as exc:
                raise SkillsToggleError(f"could not back up the hermes copy: {exc}", "pull-failed") from exc
            try:
                shutil.copytree(entry, hermes_dir, symlinks=True)
                os.rename(entry, tool_backup)
                os.symlink(str(hermes_dir.resolve()), str(entry))
            except OSError as exc:
                # roll everything back: drop the half-copy, restore both sides
                shutil.rmtree(hermes_dir, ignore_errors=True)
                try:
                    os.rename(hermes_backup, hermes_dir)
                except OSError:
                    pass
                try:
                    if not entry.exists():
                        os.rename(tool_backup, entry)
                except OSError:
                    pass
                raise SkillsToggleError(f"drift pull failed (rolled back): {exc}", "pull-failed") from exc
            skill_id = f"{existing['category']}/{existing['name']}"
            self._log(action="conflict-pull", tool=tool_id, skill=skill_id,
                      hermes_backup=str(hermes_backup), tool_backup=str(tool_backup))
            self.invalidate()
            return {
                "ok": True, "tool": tool_id, "name": name, "skill": skill_id,
                "action": "pulled", "hermes_backup": str(hermes_backup),
                "tool_backup": str(tool_backup),
            }

    def conflict_keep_both(self, tool_id: object, name: object) -> dict:
        """Keep both copies: the tool's copy is adopted under a free suffixed
        name (imported/<name>.from-<tool>), and the tool entry links to it."""
        with self._lock:
            _, tool_dir, entry = self._conflict_context(tool_id, name, require_in_tree=False)
            skills = self._scan_skills()
            taken = {sk["name"] for sk in skills.values()}
            base = f"{name}.from-{tool_id}"
            dest_name = base
            n = 2
            while dest_name in taken:
                dest_name = f"{base}-{n}"
                n += 1
            dest_dir = self.skills_root / "imported"
            dest = dest_dir / dest_name
            if dest.exists():
                raise SkillsToggleError(f"destination {dest} already exists", "invalid-destination")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            tool_backup = tool_dir / f"{name}.skills-toggle-backup-{stamp}"
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(entry, dest, symlinks=True)
                os.rename(entry, tool_backup)
                os.symlink(str(dest.resolve()), str(tool_dir / name))
            except OSError as exc:
                shutil.rmtree(dest, ignore_errors=True)
                try:
                    if not (tool_dir / name).exists():
                        os.rename(tool_backup, entry)
                except OSError:
                    pass
                raise SkillsToggleError(f"keep-both failed (rolled back): {exc}", "keep-both-failed") from exc
            self._log(action="conflict-keep-both", tool=tool_id, skill=f"imported/{dest_name}",
                      tool_backup=str(tool_backup))
            self.invalidate()
            return {
                "ok": True, "tool": tool_id, "name": name,
                "skill": f"imported/{dest_name}", "action": "kept-both",
                "tool_backup": str(tool_backup),
            }

    # -- v3-4: undo symmetry for adoption/drift operations ---------------------

    def _restore_tool_entry(self, tool_dir: Path, name: str, backup: Path) -> None:
        """Swap a managed symlink back to its backed-up real dir. Refuses if
        the current entry is not our symlink or the backup is missing."""
        entry = tool_dir / name
        if not entry.is_symlink():
            raise SkillsToggleError(f"{entry} is not a symlink — refusing to revert", "not-managed")
        if not backup.is_dir():
            raise SkillsToggleError(f"backup {backup} is missing", "backup-missing")
        resolved = Path(os.readlink(entry))
        base = resolved if resolved.is_absolute() else (entry.parent / resolved)
        if not is_inside(base, self.skills_root_resolved):
            raise SkillsToggleError(f"{entry} does not point into the skills tree", "not-managed")
        entry.unlink()
        os.rename(backup, entry)

    def revert_push(self, tool_id: object, name: object, tool_backup: object) -> dict:
        """Undo drift_push: restore the backed-up tool copy and drop the
        canonical symlink. The hermes tree is untouched by push, so nothing
        else changes."""
        with self._lock:
            tool_id = self._validate_tool(tool_id)
            if tool_id == "hermes":
                raise SkillsToggleError("hermes has no tool entry", "no-dir")
            if not isinstance(name, str) or not name.strip():
                raise SkillsToggleError("invalid name", "invalid-name")
            if not isinstance(tool_backup, str):
                raise SkillsToggleError("missing tool_backup", "invalid-body")
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None:
                raise SkillsToggleError("tool dir missing", "absent-dir")
            self._restore_tool_entry(tool_dir, name, Path(tool_backup))
            self._log(action="revert-push", tool=tool_id, name=name)
            self.invalidate()
            return {"ok": True, "tool": tool_id, "name": name, "action": "reverted"}

    def revert_pull(self, tool_id: object, name: object, hermes_backup: object, tool_backup: object) -> dict:
        """Undo conflict_pull: the external copy that became canonical is
        removed (it still exists at the tool backup), the dotted hermes backup
        is restored as canonical, and the tool entry returns to a real dir."""
        with self._lock:
            tool_id = self._validate_tool(tool_id)
            if not isinstance(name, str) or not name.strip():
                raise SkillsToggleError("invalid name", "invalid-name")
            if not isinstance(hermes_backup, str) or not isinstance(tool_backup, str):
                raise SkillsToggleError("missing backup paths", "invalid-body")
            skills = self._scan_skills()
            existing = next((sk for sk in skills.values() if sk["name"] == name), None)
            if not existing:
                raise SkillsToggleError(f"no skill named {name!r} in the tree", "unknown-skill")
            hermes_dir = existing["dir"]
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None:
                raise SkillsToggleError("tool dir missing", "absent-dir")
            if not (tool_dir / name).is_symlink():
                raise SkillsToggleError(
                    f"{tool_dir / name} is not a symlink — refusing to revert", "not-managed"
                )
            hb = Path(hermes_backup)
            if not hb.is_dir():
                raise SkillsToggleError(f"hermes backup {hb} is missing", "backup-missing")
            self._restore_tool_entry(tool_dir, name, Path(tool_backup))
            # canonical: move the pulled copy aside (dotted), restore original
            pulled_aside = hermes_dir.parent / f".skills-toggle-reverted-{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            os.rename(hermes_dir, pulled_aside)
            try:
                os.rename(hb, hermes_dir)
            except OSError:
                os.rename(pulled_aside, hermes_dir)  # never leave it half-done
                raise SkillsToggleError("could not restore hermes backup", "pull-failed")
            self._log(action="revert-pull", tool=tool_id, name=name, pulled_aside=str(pulled_aside))
            self.invalidate()
            return {
                "ok": True, "tool": tool_id, "name": name, "action": "reverted",
                "pulled_aside": str(pulled_aside),
            }

    def revert_adopt(self, tool_id: object, name: object, tool_backup: object, skill: object) -> dict:
        """Undo an adoption: restore the tool's real dir from its backup and
        move the adopted tree copy aside (dotted, never deleted)."""
        with self._lock:
            tool_id = self._validate_tool(tool_id)
            if not isinstance(name, str) or not name.strip():
                raise SkillsToggleError("invalid name", "invalid-name")
            if not isinstance(skill, str) or len(skill.split("/")) != 2:
                raise SkillsToggleError(f"invalid adopted skill id {skill!r}", "invalid-skill")
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None:
                raise SkillsToggleError("tool dir missing", "absent-dir")
            self._restore_tool_entry(tool_dir, name, Path(tool_backup))
            adopted = self.skills_root / skill.split("/")[0] / skill.split("/")[1]
            if adopted.is_dir():
                aside = adopted.parent / f".skills-toggle-reverted-{adopted.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                os.rename(adopted, aside)
            else:
                aside = None
            self._log(action="revert-adopt", tool=tool_id, name=name, aside=str(aside) if aside else None)
            self.invalidate()
            return {
                "ok": True, "tool": tool_id, "name": name, "action": "reverted",
                "aside": str(aside) if aside else None,
            }

    # -- v3-2: machine blueprint (additive-only, dry-run first) -----------------

    def blueprint_export(self) -> dict:
        """The machine's full link map + hermes-off set as a portable JSON
        shape (superset of presets)."""
        st = self.state()
        links = []
        for sk in st["skills"]:
            for tool_id, w in sk["tools"].items():
                if tool_id != "hermes" and w["state"] == "enabled":
                    links.append({"tool": tool_id, "skill": sk["id"]})
        return {
            "ok": True,
            "blueprint": {
                "version": 2,
                "generated_by": f"skills-toggle {PLUGIN_VERSION}",
                "links": links,
                "skills_disabled": sorted(self._disabled_set()),
            },
            "counts": {"links": len(links), "skills_disabled": len(self._disabled_set())},
        }

    def blueprint_apply(self, blueprint: object, dry_run: bool = False) -> dict:
        """Apply a machine blueprint ADDITIVELY (owner decision): creates
        missing links and adds skills.disabled entries. Nothing is ever
        removed or unlinked; refusals are reported per row."""
        with self._lock:
            if (
                not isinstance(blueprint, dict)
                or blueprint.get("version") != 2
                or not isinstance(blueprint.get("links"), list)
                or not isinstance(blueprint.get("skills_disabled"), list)
            ):
                raise SkillsToggleError(
                    "blueprint must be {version: 2, links: [{tool, skill}], skills_disabled: [names]}",
                    "invalid-blueprint",
                )
            if not isinstance(dry_run, bool):
                dry_run = False
            skills = self._scan_skills()
            disabled_now = self._disabled_set()
            link_plan = []
            disable_plan = []
            refused = []
            for row in blueprint["links"]:
                if not isinstance(row, dict):
                    refused.append({"row": row, "error": "malformed row"})
                    continue
                tool_id, skill_id = row.get("tool"), row.get("skill")
                if tool_id not in self.tools or tool_id == "hermes":
                    refused.append({"row": row, "error": f"unknown tool {tool_id!r}"})
                    continue
                if skill_id not in skills:
                    refused.append({"row": row, "error": f"unknown skill {skill_id!r}"})
                    continue
                tool_dir = self.tool_dir(tool_id)
                if tool_dir is None:
                    refused.append({"row": row, "error": "tool has no dir configured"})
                    continue
                link = tool_dir / skills[skill_id]["name"]
                state = self._one_state(skills[skill_id], tool_id, disabled_now)["state"]
                if state == "enabled":
                    continue  # already satisfied — not part of the plan
                if state in ("foreign-link", "unmanaged-dir"):
                    refused.append({"row": row, "error": f"{state} at target — manual resolution required"})
                    continue
                link_plan.append({"tool": tool_id, "skill": skill_id, "reason": state})
            for name in blueprint["skills_disabled"]:
                if not isinstance(name, str) or name not in {sk["name"] for sk in skills.values()}:
                    refused.append({"row": name, "error": f"unknown skill {name!r}"})
                    continue
                if name not in disabled_now:
                    disable_plan.append(name)
            plan = {
                "links": link_plan,
                "skills_disabled": disable_plan,
                "refused": refused,
                "counts": {
                    "links": len(link_plan),
                    "skills_disabled": len(disable_plan),
                    "refused": len(refused),
                },
            }
            if dry_run:
                return {"ok": True, "dry_run": True, "plan": plan}
            results = []
            by_tool: dict = {}
            for row in link_plan:
                by_tool.setdefault(row["tool"], []).append(row["skill"])
            for tool_id, skill_ids in by_tool.items():
                for sid in skill_ids:
                    try:
                        res = self.toggle(sid, tool_id, True)
                        results.append({"skill": sid, "tool": tool_id, "ok": True, "action": res["action"]})
                    except SkillsToggleError as exc:
                        results.append({"skill": sid, "tool": tool_id, "ok": False, "error": str(exc)})
            for name in disable_plan:
                try:
                    self._hermes_toggle(name, False)
                    results.append({"skill": name, "tool": "hermes", "ok": True, "action": "config-updated"})
                except SkillsToggleError as exc:
                    results.append({"skill": name, "tool": "hermes", "ok": False, "error": str(exc)})
            self._log(action="blueprint-apply", planned=len(link_plan), disabled=len(disable_plan))
            return {
                "ok": True,
                "applied": {"links": sum(1 for r in results if r["ok"] and r["tool"] != "hermes"),
                            "skills_disabled": sum(1 for r in results if r["ok"] and r["tool"] == "hermes"),
                            "failed": sum(1 for r in results if not r["ok"])},
                "results": results,
                "refused": refused,
            }

    # -- v3-6: backup browser + restore ----------------------------------------

    _BACKUP_PATTERNS = (
        re.compile(r"^config\.yaml\.bak\.skills-toggle\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^skills-toggle\.json\.bak\.skills-toggle\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^(.+)\.skills-toggle-backup\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^(.+)\.skills-toggle-backup-(\d{8}-\d{6})$"),
        re.compile(r"^\.skills-toggle-(?:backup|reverted)-(.+)-(\d{8}-\d{6})$"),
    )

    def list_backups(self) -> dict:
        rows = []
        home = self.home
        for ln in sorted(home.glob("config.yaml.bak.skills-toggle.*")):
            rows.append({"path": str(ln), "kind": "config", "name": ln.name})
        for ln in sorted(home.glob("skills-toggle.json.bak.skills-toggle.*")):
            rows.append({"path": str(ln), "kind": "tools-json", "name": ln.name})
        for tool_id in self.tools:
            if tool_id == "hermes":
                continue
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None or not tool_dir.is_dir():
                continue
            try:
                children = sorted(tool_dir.iterdir())
            except OSError:
                continue
            for ln in children:
                if ln.name.endswith(".skills-toggle-backup") or ".skills-toggle-backup-" in ln.name:
                    if ln.is_dir():
                        rows.append({"path": str(ln), "kind": "tool-link", "tool": tool_id, "name": ln.name})
        if self.skills_root.is_dir():
            for cat_dir in sorted(self.skills_root.iterdir()):
                if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                    continue
                try:
                    children = sorted(cat_dir.iterdir())
                except OSError:
                    continue
                for ln in children:
                    if ln.name.startswith(".skills-toggle-backup-") or ln.name.startswith(".skills-toggle-reverted-"):
                        if ln.is_dir():
                            rows.append({"path": str(ln), "kind": "hermes-copy", "name": ln.name, "category": cat_dir.name})
        rows.sort(key=lambda r: r["path"], reverse=True)
        return {"ok": True, "backups": rows[:200], "count": len(rows)}

    def restore_backup(self, path: object) -> dict:
        """Restore a plugin-created backup. The path must appear in the live
        backup scan (no arbitrary files), and the current state is backed up
        before anything moves."""
        with self._lock:
            if not isinstance(path, str):
                raise SkillsToggleError("missing path", "invalid-body")
            live = {r["path"]: r for r in self.list_backups()["backups"]}
            row = live.get(path)
            if not row:
                raise SkillsToggleError("not a known skills-toggle backup", "unknown-backup")
            src = Path(path)
            kind = row["kind"]
            if kind in ("config", "tools-json"):
                target = self.config_path if kind == "config" else user_config_path(self.home)
                if not target.is_file():
                    raise SkillsToggleError(f"{target} is missing — nothing to replace", "restore-failed")
                pre = self._backup(target)
                shutil.copy2(src, target)
                self._log(action="restore", kind=kind, path=path, pre_restore_backup=pre)
                self.invalidate()
                reset_core()
                return {"ok": True, "kind": kind, "action": "restored", "pre_restore_backup": pre}
            if kind == "tool-link":
                tool_dir = self.tool_dir(row["tool"])
                if tool_dir is None:
                    raise SkillsToggleError("tool dir missing", "absent-dir")
                name = row["name"].split(".skills-toggle-backup")[0]
                self._restore_tool_entry(tool_dir, name, src)
                self._log(action="restore", kind=kind, path=path)
                self.invalidate()
                return {"ok": True, "kind": kind, "action": "restored", "name": name}
            if kind == "hermes-copy":
                # dotted name: .skills-toggle-backup-<name>-<stamp> or .skills-toggle-reverted-<name>-<stamp>
                m = re.match(r"^\.skills-toggle-(?:backup|reverted)-(.+)-\d{8}-\d{6}$", row["name"])
                if not m:
                    raise SkillsToggleError("cannot parse backup name", "restore-failed")
                name = m.group(1)
                canonical = self.skills_root / row["category"] / name
                if not canonical.is_dir():
                    raise SkillsToggleError(f"canonical {canonical} is missing", "restore-failed")
                aside = canonical.parent / f".skills-toggle-replaced-{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                os.rename(canonical, aside)
                try:
                    os.rename(src, canonical)
                except OSError:
                    os.rename(aside, canonical)
                    raise SkillsToggleError("restore failed — rolled back", "restore-failed")
                self._log(action="restore", kind=kind, path=path, replaced_aside=str(aside))
                self.invalidate()
                return {"ok": True, "kind": kind, "action": "restored", "name": name, "replaced_aside": str(aside)}
            raise SkillsToggleError(f"restore not supported for kind {kind}", "restore-failed")

    # -- v2: custom tool config ------------------------------------------------

    def set_tool(self, tool_id: object, label: object, dir_str: object) -> dict:
        """Add or override a tool target dir in <hermes_home>/skills-toggle.json
        (timestamped backup first). Hermes itself is config-backed and locked."""
        with self._lock:
            if (
                not isinstance(tool_id, str)
                or not re.match(r"^[a-z0-9-]{1,32}$", tool_id)
                or tool_id == "hermes"
            ):
                raise SkillsToggleError(f"invalid tool id {tool_id!r}", "invalid-tool-id")
            if not isinstance(label, str) or not label.strip() or len(label) > 40:
                raise SkillsToggleError("'label' must be a 1-40 char string", "invalid-label")
            if not isinstance(dir_str, str) or not dir_str.strip() or "\0" in dir_str:
                raise SkillsToggleError("'dir' must be a non-empty path string", "invalid-dir")
            try:
                expand_path(dir_str)
            except ValueError:
                raise SkillsToggleError(f"'dir' expands to an empty path: {dir_str!r}", "invalid-dir")
            cfg_path = user_config_path(self.home)
            data = {}
            if cfg_path.is_file():
                try:
                    data = json.loads(cfg_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = {}
            backup = None
            if cfg_path.is_file():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_path = cfg_path.with_name(f"{cfg_path.name}.bak.skills-toggle.{stamp}")
                shutil.copy2(cfg_path, backup_path)
                backup = str(backup_path)
            tools = data.setdefault("tools", {})
            tools[tool_id] = {"label": label.strip(), "dir": dir_str.strip()}
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self._log(action="config-tools", tool=tool_id, dir=str(dir_str), backup=backup)
            self.invalidate()
            reset_core()  # tool map is loaded at core build time — rebuild singleton
            return {"ok": True, "tool": tool_id, "label": label.strip(), "dir": str(expand_path(dir_str)), "backup": backup}

    def health(self) -> dict:
        return {
            "ok": True,
            "plugin": PLUGIN_ID,
            "version": PLUGIN_VERSION,
            "hermes_home": str(self.home),
            "skills_root": str(self.skills_root),
            "skills_root_exists": self.skills_root.is_dir(),
            "tools": {tid: (str(self.tool_dir(tid)) if self.tool_dir(tid) else None) for tid in self.tools},
        }


# ---------------------------------------------------------------------------
# Tools config loading (defaults + <hermes_home>/skills-toggle.json override)
# ---------------------------------------------------------------------------


def user_config_path(home: Path) -> Path:
    return home / "skills-toggle.json"


def load_tools_config(home: Path) -> dict:
    tools = copy.deepcopy(DEFAULT_TOOLS)

    # ZCode default detection: prefer an existing ~/.agents/skills
    zcode = tools.get("zcode", {})
    primary = expand_path(zcode.get("dir", "~/.agents/skills"))
    if not primary.is_dir() and zcode.get("fallback_dir"):
        zcode["dir"] = zcode["fallback_dir"]

    cfg_path = user_config_path(home)
    if cfg_path.is_file():
        try:
            user_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            user_cfg = {}
        for tool_id, spec in (user_cfg.get("tools") or {}).items():
            if not isinstance(tool_id, str) or not re.match(r"^[a-z0-9-]+$", tool_id):
                continue
            if tool_id not in tools:
                tools[tool_id] = {"label": tool_id.title()}
            if isinstance(spec, str):
                tools[tool_id]["dir"] = spec
            elif isinstance(spec, dict):
                if "label" in spec:
                    tools[tool_id]["label"] = str(spec["label"])
                if "dir" in spec:
                    tools[tool_id]["dir"] = str(spec["dir"])
    return tools


# ---------------------------------------------------------------------------
# v2.2 — MCP switchboard core (Q1a: Hermes catalog + Claude Desktop writer)
# Hermes config.yaml `mcp_servers` is the source of truth; entries carry an
# `enabled:` flag (Hermes' own on/off). Claude Desktop mirrors entries into
# its claude_desktop_config.json `mcpServers` map (presence = enabled).
# ---------------------------------------------------------------------------

CODEX_CONFIG_CANDIDATES = ["~/.codex/config.toml"]


def _toml_scalar(value: str):
    v = value.strip()
    if v in ("true", "false"):
        return v == "true"
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return json.loads(v)
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [json.loads(item.strip()) for item in inner.split(",") if item.strip()]
    return v


def parse_codex_mcp(text: str) -> dict:
    """Line-based parse of [mcp_servers.<name>] tables (+ sub-tables like env)
    from a codex config.toml. {name: {enabled, definition}} — enabled defaults
    to True; a native `enabled = false` flag is honored."""
    out: dict = {}
    current = None
    current_sub = None
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        m = re.match(r"^\[mcp_servers\.(.+)\]$", ln)
        if m:
            key = m.group(1).strip().strip('"')
            if "." in key:
                current, current_sub = key.split(".", 1)
                out.setdefault(current, {"enabled": True, "definition": {}})
                out[current]["definition"].setdefault(current_sub, {})
            else:
                current = key
                current_sub = None
                out.setdefault(current, {"enabled": True, "definition": {}})
            continue
        if current is None or ln.startswith("["):
            if ln.startswith("["):
                current, current_sub = None, None  # left the mcp_servers area
            continue
        m = re.match(r"^([^=]+?)\s*=\s*(.*)$", ln)
        if not m:
            continue
        key = m.group(1).strip().strip('"')
        value = _toml_scalar(m.group(2))
        if current_sub:
            out[current]["definition"].setdefault(current_sub, {})[key] = value
        elif key == "enabled":
            out[current]["enabled"] = bool(value)
        else:
            out[current]["definition"][key] = value
    return out


def _toml_string(value: str) -> str:
    return json.dumps(str(value))  # basic string; close enough for our values


def codex_server_block(name: str, projection: dict) -> str:
    """Render one [mcp_servers.<name>] block (universal keys only)."""
    safe_name = name.replace('"', '')
    lines = [f"[mcp_servers.{safe_name}]"]
    for key in ("command", "url", "headers"):
        if key in projection:
            lines.append(f"{key} = {_toml_string(projection[key])}")
    if "args" in projection:
        lines.append(f"args = [{', '.join(_toml_string(a) for a in projection['args'])}]")
    lines.append("")
    if isinstance(projection.get("env"), dict) and projection["env"]:
        lines.append(f"[mcp_servers.{safe_name}.env]")
        for k, v in projection["env"].items():
            lines.append(f"{k} = {_toml_string(v)}")
        lines.append("")
    return "\n".join(lines)


def _remove_codex_block(text: str, name: str) -> str:
    """Delete the [mcp_servers.<name>] block and its sub-tables."""
    safe = name.replace('"', '')
    lines = text.splitlines()
    out = []
    skipping = False
    for ln in lines:
        stripped = ln.strip()
        m = re.match(r"^\[mcp_servers\.(.+)\]$", stripped)
        if m:
            key = m.group(1).strip().strip('"')
            is_ours = key == safe or key.split(".", 1)[0] == safe
            skipping = is_ours
            if skipping:
                continue
        if skipping and stripped.startswith("["):
            skipping = False  # reached an unrelated table
        if not skipping:
            out.append(ln)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


CLAUDE_DESKTOP_CONFIG_CANDIDATES = [
    "~/Library/Application Support/Claude/claude_desktop_config.json",  # macOS
    "~/.config/Claude/claude_desktop_config.json",  # Linux
    "${APPDATA:-~/AppData/Roaming}/Claude/claude_desktop_config.json",  # Windows
]
_MCP_UNIVERSAL_KEYS = ("command", "args", "env", "url", "headers")


def _coerce_scalar(value: str):
    v = _yaml_unquote(value)
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if re.match(r"^-?\d+$", v):
        return int(v)
    return v


def parse_mcp_servers(text: str) -> dict:
    """Tolerant parse of the `mcp_servers:` block: {name: {enabled, definition}}.

    definition keeps the universal MCP keys (command, args, env, url, headers)
    plus any other scalars found; enabled defaults to True (hermes treats a
    missing flag as enabled)."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^mcp_servers:\s*(#.*)?$", ln):
            start = i
            break
    if start is None:
        return {}
    out: dict = {}
    current = None
    current_def_key = None
    base_indent = None
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln[:1].strip() and not ln.startswith((" ", "\t")):
            break  # next top-level key
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        stripped = ln.strip()
        if base_indent is None:
            base_indent = indent
        if indent == base_indent:
            m = re.match(r"^([^:#]+):\s*(.*)$", stripped)
            if m:
                current = _yaml_unquote(m.group(1).strip())
                out[current] = {"enabled": True, "definition": {}}
                current_def_key = None
            continue
        if current is None:
            continue
        if indent == base_indent + 2:
            m = re.match(r"^([^:#]+):\s*(.*)$", stripped)
            if not m:
                continue
            key = _yaml_unquote(m.group(1).strip())
            value = m.group(2).strip()
            if value == "":
                current_def_key = key  # nested map/list (e.g. env:, args:)
            else:
                if key == "enabled":
                    out[current]["enabled"] = bool(_coerce_scalar(value))
                else:
                    out[current]["definition"][key] = _coerce_scalar(value)
                current_def_key = None
            continue
        # deeper lines belong to the last def key — a list (args) or a map
        # (env, headers, …), discovered from the first child line
        if current_def_key is not None:
            m_list = _RE_LIST_ITEM.match(ln)
            if m_list:
                out[current]["definition"].setdefault(current_def_key, []).append(
                    _yaml_unquote(m_list.group(2))
                )
            else:
                m_kv = re.match(r"^([^:#]+):\s*(.*)$", stripped)
                if m_kv:
                    container = out[current]["definition"].setdefault(current_def_key, {})
                    if isinstance(container, dict):
                        container[_yaml_unquote(m_kv.group(1).strip())] = _coerce_scalar(m_kv.group(2))
    return {k: v for k, v in out.items() if v["definition"] or not v["enabled"]}


def set_mcp_server_enabled(text: str, name: str, enabled: bool) -> str:
    """Surgically flip `enabled:` for one mcp_servers entry (insert the flag
    as the entry's first key when missing). Self-checks by re-parse."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^mcp_servers:\s*(#.*)?$", ln):
            start = i
            break
    if start is None:
        raise ConfigEditError("no mcp_servers block in config")
    base_indent = None
    entry_start = None
    entry_end = len(lines)
    enabled_line = None
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln[:1].strip() and not ln.startswith((" ", "\t")):
            entry_end = j
            break
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        if base_indent is None:
            base_indent = indent
        if indent == base_indent:
            key = ln.strip().split(":", 1)[0]
            if _yaml_unquote(key) == name:
                entry_start = j
            elif entry_start is not None:
                entry_end = j
                break
            continue
        if entry_start is not None and indent == base_indent + 2 and ln.strip().startswith("enabled:"):
            enabled_line = j
    if entry_start is None:
        raise ConfigEditError(f"no mcp_servers entry named {name!r}")
    pad = " " * (base_indent + 2)
    flag = "true" if enabled else "false"
    if enabled_line is not None:
        lines[enabled_line] = re.sub(r"enabled:\s*.*$", f"enabled: {flag}", lines[enabled_line])
    else:
        lines.insert(entry_start + 1, f"{pad}enabled: {flag}")
    new_text = "\n".join(lines) + "\n"
    got = parse_mcp_servers(new_text)
    if name not in got or bool(got[name]["enabled"]) != enabled:
        raise ConfigEditError(f"self-check failed editing mcp_servers.{name}.enabled")
    return new_text


def _redact_env(definition: dict) -> dict:
    """env values never leave the gateway: the pane sees key names only."""
    redacted = dict(definition)
    if isinstance(redacted.get("env"), dict):
        redacted["env"] = sorted(redacted["env"].keys())
    return redacted


def _claude_projection(definition: dict) -> dict:
    """Catalog definition -> the keys Claude Desktop understands."""
    return {k: v for k, v in definition.items() if k in _MCP_UNIVERSAL_KEYS}


class McpCore:
    """MCP switchboard operations. Same construction pattern as
    SkillsToggleCore: explicit paths, stdlib-only, JSON-able results."""

    def __init__(self, home: Path, claude_desktop_config: Path | None = None, log_path: Path | None = None, codex_config: Path | None = None):
        self.home = home
        self.config_path = home / "config.yaml"
        self.claude_config = claude_desktop_config or self._default_claude_config()
        self.codex_config = codex_config or self._default_codex_config()
        self.log_path = log_path
        self._lock = threading.RLock()

    def _default_codex_config(self) -> Path | None:
        for candidate in CODEX_CONFIG_CANDIDATES:
            try:
                expanded = expand_path(candidate)
            except ValueError:
                continue
            if expanded.is_file():
                return expanded
        return None

    def _default_claude_config(self) -> Path | None:
        for candidate in CLAUDE_DESKTOP_CONFIG_CANDIDATES:
            try:
                expanded = expand_path(candidate)
            except ValueError:
                continue
            if expanded.is_file():
                return expanded
        return expand_path(CLAUDE_DESKTOP_CONFIG_CANDIDATES[0])

    def _log(self, **rec: object) -> None:
        if not self.log_path:
            return
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _backup(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak.skills-toggle.{stamp}")
        n = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak.skills-toggle.{stamp}-{n}")
            n += 1
        shutil.copy2(path, backup)
        return str(backup)

    # -- catalog ------------------------------------------------------------

    def catalog(self) -> dict:
        try:
            text = self.config_path.read_text(encoding="utf-8") if self.config_path.is_file() else ""
        except OSError:
            text = ""
        parsed = parse_mcp_servers(text)
        catalog = []
        for name in sorted(parsed):
            catalog.append(
                {
                    "name": name,
                    "enabled": bool(parsed[name]["enabled"]),
                    "definition": parsed[name]["definition"],
                    "projection": _claude_projection(parsed[name]["definition"]),
                }
            )
        return {"ok": True, "catalog": catalog, "count": len(catalog)}

    # -- claude side ----------------------------------------------------------

    def _read_claude(self) -> tuple[dict, str]:
        if self.claude_config is None or not self.claude_config.is_file():
            return {}, ""
        try:
            text = self.claude_config.read_text(encoding="utf-8")
            return json.loads(text), text
        except (OSError, json.JSONDecodeError):
            return {}, ""

    def _read_codex(self) -> tuple[dict, str]:
        if self.codex_config is None or not self.codex_config.is_file():
            return {}, ""
        try:
            text = self.codex_config.read_text(encoding="utf-8")
            return parse_codex_mcp(text), text
        except OSError:
            return {}, ""

    def _write_codex(self, name: object, create: bool, force: bool = False) -> dict:
        if not isinstance(name, str) or not name.strip():
            raise SkillsToggleError("invalid server name", "invalid-name")
        if self.codex_config is None:
            raise SkillsToggleError("no codex config path resolved", "no-writer")
        cat = self.catalog()
        entry = next((c for c in cat["catalog"] if c["name"] == name), None)
        codex, raw = self._read_codex()
        if create and entry is None:
            raise SkillsToggleError(f"unknown MCP server {name!r} in the catalog", "unknown-server")
        if not create and name not in codex:
            return {"ok": True, "name": name, "action": "noop", "state": "missing"}
        if not create and entry is None:
            raise SkillsToggleError(f"{name!r} is not in the Hermes catalog — refusing to remove", "unknown-server")
        if not create and (codex[name]["definition"] != entry["projection"]) and not force:
            raise SkillsToggleError(
                f"Codex's copy of {name!r} differs from the catalog — pass force to overwrite", "drifted"
            )
        backup = self._backup(self.codex_config)
        if create:
            base = _remove_codex_block(raw, name) if name in codex else raw
            new_text = (base.rstrip("\n") + "\n\n" if base.strip() else "") + codex_server_block(name, entry["projection"])
            action = "updated" if name in codex else "created"
            state = "enabled"
        else:
            new_text = _remove_codex_block(raw, name)
            action = "removed"
            state = "missing"
        self.codex_config.write_text(new_text, encoding="utf-8")
        self._log(action=f"mcp-codex-{action}", server=name, backup=backup)
        return {"ok": True, "name": name, "writer": "codex", "action": action, "state": state, "backup": backup}

    def sync_to_codex(self, name: object) -> dict:
        with self._lock:
            return self._write_codex(name, create=True)

    def remove_from_codex(self, name: object, force: object = False) -> dict:
        with self._lock:
            if not isinstance(force, bool):
                force = False
            return self._write_codex(name, create=False, force=force)

    def mcp_state(self) -> dict:
        cat = self.catalog()
        claude, _raw = self._read_claude()
        servers = claude.get("mcpServers") if isinstance(claude, dict) else None
        servers = servers if isinstance(servers, dict) else {}
        codex_servers, _codex_raw = self._read_codex()
        codex_servers = codex_servers if codex_servers else None
        projection_by_name = {c["name"]: c["projection"] for c in cat["catalog"]}
        foreign = []
        for name, definition in servers.items():
            if name not in projection_by_name:
                foreign.append({"name": name, "keys": sorted(definition.keys()) if isinstance(definition, dict) else []})
        rows = []
        for c in cat["catalog"]:
            name = c["name"]
            if name not in servers:
                claude_state = "missing"
            elif servers[name] == c["projection"]:
                claude_state = "enabled"
            else:
                claude_state = "drifted"
            codex_state = "missing"
            if codex_servers is not None and name in codex_servers:
                if not codex_servers[name]["definition"]:
                    codex_state = "missing"
                elif codex_servers[name]["definition"] == c["projection"]:
                    codex_state = "enabled" if codex_servers[name]["enabled"] else "missing"
                else:
                    codex_state = "drifted"
            rows.append(
                {
                    "name": name,
                    "enabled": c["enabled"],
                    # env VALUES are secrets — the payload carries key names only
                    "definition": _redact_env(c["definition"]),
                    "writers": {"claude": claude_state, "codex": codex_state},
                }
            )
        return {
            "ok": True,
            "rows": rows,
            "foreign": foreign,
            "counts": {"catalog": len(rows), "foreign": len(foreign)},
            "writers": {
                "claude": {
                    "label": "Claude Desktop",
                    "path": str(self.claude_config) if self.claude_config else None,
                    "present": bool(self.claude_config and self.claude_config.is_file()),
                },
                "codex": {
                    "label": "Codex",
                    "path": str(self.codex_config) if self.codex_config else None,
                    "present": bool(self.codex_config and self.codex_config.is_file()),
                },
            },
        }

    def toggle_hermes(self, name: object, enabled: object) -> dict:
        with self._lock:
            if not isinstance(name, str) or not name.strip():
                raise SkillsToggleError("invalid server name", "invalid-name")
            if not isinstance(enabled, bool):
                raise SkillsToggleError("'enabled' must be a boolean", "invalid-body")
            if not self.config_path.is_file():
                raise SkillsToggleError("config.yaml not found", "config-unreadable")
            try:
                text = self.config_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise SkillsToggleError(f"cannot read {self.config_path}: {exc}", "config-unreadable") from exc
            current = parse_mcp_servers(text)
            if name not in current:
                raise SkillsToggleError(f"unknown MCP server {name!r} in the catalog", "unknown-server")
            if bool(current[name]["enabled"]) == enabled:
                return {"ok": True, "name": name, "enabled": enabled, "action": "noop"}
            backup = self._backup(self.config_path)
            try:
                new_text = set_mcp_server_enabled(text, name, enabled)
            except ConfigEditError as exc:
                raise SkillsToggleError(f"config.yaml edit refused: {exc}", "config-edit") from exc
            self.config_path.write_text(new_text, encoding="utf-8")
            self._log(action="mcp-toggle", server=name, enabled=enabled, backup=backup)
            return {"ok": True, "name": name, "enabled": enabled, "action": "config-updated", "backup": backup}

    def sync_to_claude(self, name: object) -> dict:
        with self._lock:
            return self._write_claude(name, create=True)

    def remove_from_claude(self, name: object, force: object = False) -> dict:
        with self._lock:
            if not isinstance(force, bool):
                force = False
            return self._write_claude(name, create=False, force=force)

    def _write_claude(self, name: object, create: bool, force: bool = False) -> dict:
        if not isinstance(name, str) or not name.strip():
            raise SkillsToggleError("invalid server name", "invalid-name")
        cat = self.catalog()
        entry = next((c for c in cat["catalog"] if c["name"] == name), None)
        claude, raw = self._read_claude()
        servers = claude.get("mcpServers") if isinstance(claude, dict) and isinstance(claude.get("mcpServers"), dict) else {}
        if create and entry is None:
            raise SkillsToggleError(f"unknown MCP server {name!r} in the catalog", "unknown-server")
        if not create and name not in servers:
            return {"ok": True, "name": name, "action": "noop", "state": "missing"}
        if not create and entry is None:
            # present in Claude but not in the catalog: foreign, never touched
            raise SkillsToggleError(f"{name!r} is not in the Hermes catalog — refusing to remove", "unknown-server")
        if not create and servers[name] != entry["projection"] and not force:
            raise SkillsToggleError(
                f"Claude's copy of {name!r} differs from the catalog — pass force to overwrite", "drifted"
            )
        if self.claude_config is None:
            raise SkillsToggleError("no Claude Desktop config path resolved", "no-writer")
        backup = self._backup(self.claude_config)
        if raw == "" or not claude:
            doc = {"mcpServers": servers}
        else:
            doc = claude if isinstance(claude, dict) else {"mcpServers": servers}
        if create:
            doc["mcpServers"] = {**servers, name: entry["projection"]}
            action = "updated" if name in servers else "created"
        else:
            remaining = {k: v for k, v in servers.items() if k != name}
            doc["mcpServers"] = remaining
            action = "removed"
        self.claude_config.parent.mkdir(parents=True, exist_ok=True)
        self.claude_config.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._log(action=f"mcp-{action}", server=name, writer="claude", backup=backup)
        return {
            "ok": True,
            "name": name,
            "writer": "claude",
            "action": action,
            "state": "enabled" if create else "missing",
            "backup": backup,
        }

    def health(self) -> dict:
        return {"ok": True, "catalog": self.catalog()["count"], "writer": "claude"}


# ---------------------------------------------------------------------------
# Singleton for the route layer
# ---------------------------------------------------------------------------

_CORE: SkillsToggleCore | None = None
_CORE_SIG: tuple | None = None
_CORE_FROZEN = False


def _core_signature() -> tuple:
    home = hermes_home()
    return (str(home), str(user_config_path(home)))


def get_core() -> SkillsToggleCore:
    global _CORE, _CORE_SIG
    if _CORE_FROZEN and _CORE is not None:
        return _CORE
    sig = _core_signature()
    if _CORE is None or sig != _CORE_SIG:
        _CORE = SkillsToggleCore.build_default()
        _CORE_SIG = sig
    return _CORE


_MCP_CORE: "McpCore | None" = None


def get_mcp_core() -> "McpCore":
    global _MCP_CORE
    if _CORE_FROZEN and _MCP_CORE is not None:
        return _MCP_CORE  # test env: frozen alongside the skills core
    if _MCP_CORE is None:
        home = hermes_home()
        _MCP_CORE = McpCore(home, log_path=Path(__file__).resolve().parent.parent / "data" / "mutations.log")
    return _MCP_CORE


def set_core_for_testing(core: SkillsToggleCore | None) -> None:
    global _CORE, _CORE_SIG, _CORE_FROZEN, _MCP_CORE
    if core is None:
        _CORE = None
        _CORE_SIG = None
        _CORE_FROZEN = False
        _MCP_CORE = None
        return
    _CORE = core
    _CORE_SIG = ("test", id(core))
    _CORE_FROZEN = True
    # the MCP singleton must follow the fixture home, never the real one
    _MCP_CORE = McpCore(core.home, log_path=core.log_path)


def reset_core() -> None:
    """Force the singleton to rebuild on next get_core() (after config writes
    that change the tool map, which is loaded at core build time)."""
    global _CORE, _CORE_SIG, _CORE_FROZEN
    _CORE = None
    _CORE_SIG = None
    _CORE_FROZEN = False


# ---------------------------------------------------------------------------
# FastAPI route layer (mounted at /api/plugins/skills-toggle/)
# ---------------------------------------------------------------------------

try:
    from fastapi import APIRouter  # type: ignore
except ImportError:  # tests / non-gateway environments
    APIRouter = None  # type: ignore[assignment]

if APIRouter is not None:

    router = APIRouter()

    def _call(fn, *args, **kwargs) -> dict:
        try:
            return fn(*args, **kwargs)
        except SkillsToggleError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}

    @router.get("/health")
    async def health() -> dict:
        return get_core().health()

    @router.get("/state")
    async def state() -> dict:
        return get_core().state()

    @router.get("/detail")
    async def detail(skill: str) -> dict:
        return _call(get_core().detail, skill)

    @router.get("/diff")
    async def diff() -> dict:
        return get_core().diff()

    @router.post("/toggle")
    async def toggle(body: dict) -> dict:
        return _call(get_core().toggle, body.get("skill"), body.get("tool"), body.get("enabled"))

    @router.post("/toggle-bulk")
    async def toggle_bulk(body: dict) -> dict:
        return _call(get_core().toggle_bulk, body.get("skills"), body.get("tool"), body.get("enabled"))

    @router.post("/repair")
    async def repair(body: dict) -> dict:
        return _call(get_core().repair, body.get("skill"), body.get("tool"))

    @router.post("/repair-all")
    async def repair_all() -> dict:
        return get_core().repair_all()

    @router.post("/ensure-tool-dir")
    async def ensure_tool_dir(body: dict) -> dict:
        return _call(get_core().ensure_tool_dir, body.get("tool"))

    @router.get("/import/scan")
    async def import_scan() -> dict:
        return _call(get_core().import_scan)

    @router.post("/import/apply")
    async def import_apply(body: dict) -> dict:
        return _call(get_core().import_apply, body.get("tool"), body.get("names"), body.get("category", "imported"))

    @router.get("/drift")
    async def drift() -> dict:
        return _call(get_core().drift)

    @router.post("/config/tools")
    async def config_tools(body: dict) -> dict:
        return _call(get_core().set_tool, body.get("id"), body.get("label"), body.get("dir"))

    @router.get("/mcp/state")
    async def mcp_state() -> dict:
        return _call(get_mcp_core().mcp_state)

    @router.post("/mcp/toggle")
    async def mcp_toggle(body: dict) -> dict:
        return _call(get_mcp_core().toggle_hermes, body.get("name"), body.get("enabled"))

    @router.post("/mcp/sync")
    async def mcp_sync(body: dict) -> dict:
        return _call(get_mcp_core().sync_to_claude, body.get("name"))

    @router.post("/mcp/remove")
    async def mcp_remove(body: dict) -> dict:
        return _call(get_mcp_core().remove_from_claude, body.get("name"), body.get("force", False))

    @router.post("/mcp/codex/sync")
    async def mcp_codex_sync(body: dict) -> dict:
        return _call(get_mcp_core().sync_to_codex, body.get("name"))

    @router.post("/mcp/codex/remove")
    async def mcp_codex_remove(body: dict) -> dict:
        return _call(get_mcp_core().remove_from_codex, body.get("name"), body.get("force", False))

    @router.get("/blueprint/export")
    async def blueprint_export() -> dict:
        return get_core().blueprint_export()

    @router.post("/blueprint/apply")
    async def blueprint_apply(body: dict) -> dict:
        return _call(
            get_core().blueprint_apply,
            body.get("blueprint"),
            bool(body.get("dry_run", False)),
        )

    @router.get("/backups")
    async def list_backups() -> dict:
        return _call(get_core().list_backups)

    @router.post("/backups/restore")
    async def restore_backup(body: dict) -> dict:
        return _call(get_core().restore_backup, body.get("path"))

    @router.post("/conflict/revert-push")
    async def revert_push(body: dict) -> dict:
        return _call(get_core().revert_push, body.get("tool"), body.get("name"), body.get("tool_backup"))

    @router.post("/conflict/revert-pull")
    async def revert_pull(body: dict) -> dict:
        return _call(
            get_core().revert_pull,
            body.get("tool"), body.get("name"), body.get("hermes_backup"), body.get("tool_backup"),
        )

    @router.post("/conflict/revert-adopt")
    async def revert_adopt(body: dict) -> dict:
        return _call(
            get_core().revert_adopt,
            body.get("tool"), body.get("name"), body.get("tool_backup"), body.get("skill"),
        )

    @router.post("/conflict/pull")
    async def conflict_pull(body: dict) -> dict:
        return _call(get_core().conflict_pull, body.get("tool"), body.get("name"))

    @router.post("/conflict/keep-both")
    async def conflict_keep_both(body: dict) -> dict:
        return _call(get_core().conflict_keep_both, body.get("tool"), body.get("name"))

    @router.post("/drift/push")
    async def drift_push(body: dict) -> dict:
        return _call(get_core().drift_push, body.get("tool"), body.get("name"))


else:  # pragma: no cover — non-gateway import (tests); keep attribute defined
    router = None
