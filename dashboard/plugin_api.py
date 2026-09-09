"""hermes-switchboard — backend routes for the Hermes desktop pane.

# MIT License — Copyright (c) 2026 qwertyuiop97
# See LICENSE at the package root.

Unified Hermes plugin package (see the Desktop Plugin SDK doc, "One package,
both SDKs")::

    ~/.hermes/plugins/hermes-switchboard/
    ├── plugin.yaml               # agent half (metadata only)
    ├── dashboard/
    │   ├── manifest.json         # {"name": "hermes-switchboard", "api": "plugin_api.py"}
    │   └── plugin_api.py         # THIS FILE — exports `router` (FastAPI APIRouter)
    └── desktop/
        └── plugin.js             # desktop half — pane UI, calls ctx.rest('/...')

Routes mount under ``/api/plugins/hermes-switchboard/``:

    GET  /health          → liveness + resolved paths (for the pane's error banner)
    GET  /state           → every skill + per-tool state (lean payload, cached)
    GET  /detail?skill=   → full SKILL.md text for one skill
    POST /toggle          → {skill, tool, enabled} link/unlink (or config edit for hermes)
    POST /toggle-bulk     → {skills: [...], tool, enabled}
    POST /bulk/plan       → immutable explicit-id bulk preview
    POST /bulk/apply      → exact-id execution + durable undo receipt
    POST /repair          → {skill, tool} fix a broken link
    POST /repair-all      → fix every broken link that points into the skills tree
    POST /ensure-tool-dir → {tool} create a missing tool skills dir
    GET  /diff            → skills unlinked everywhere + dangling/foreign/unmanaged links
    POST /import/plan     → read-only multi-source adoption preview
    POST /import/apply-plan → exact-entry adoption + durable restore receipt

Design rules:
  * Hermes (~/.hermes/skills/<category>/<name>/SKILL.md) is the source of truth.
  * Consumer tools get SYMLINKS (never copies); link name == skill name.
  * Never delete a skill source dir, a real (non-symlink) dir, or a foreign
    symlink — only symlinks that resolve inside the managed skills tree.
  * The hermes tool toggles membership in ``skills.disabled`` (bare names) in
    ``config.yaml`` via a surgical line edit with a timestamped backup.
  * Zero hardcoded paths: home resolves via ``~``/``$HOME``, the Hermes root via
    ``$HERMES_HOME`` or ``$HERMES_PROFILE`` (→ ~/.hermes/profiles/<name>) or
    ``~/.hermes``; tool target dirs are overridable via
    ``<hermes_home>/hermes-switchboard.json`` with ``~`` and ``${VAR:-default}``
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
import sys
import threading
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ID = "hermes-switchboard"
PLUGIN_VERSION = "3.0.0"
LEGACY_PLUGIN_ID = "skills-toggle"

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
# Tool map: defaults (overridable via <hermes_home>/hermes-switchboard.json)
# ---------------------------------------------------------------------------

# This package-local, read-only catalog is the only built-in client data source.
# Keep DEFAULT_TOOLS as the legacy public import surface; runtime discovery below
# uses documented paths and does not equate a shared folder with an installed app.
CLIENT_CATALOG = json.loads(Path(__file__).with_name("client_catalog.json").read_text(encoding="utf-8"))["clients"]
DEFAULT_TOOLS: dict = {row["id"]: copy.deepcopy(row["legacy_default"])
                      for row in CLIENT_CATALOG if "legacy_default" in row}
CATALOG_VERSION = 1
CONFIG_SCHEMA_VERSION = 2

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
    r"""strip \\?\ → realpath → strip again (realpath re-adds it on Windows)
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



def _atomic_write_text(path: Path, text: str) -> None:
    """Replace a complete file, preserving an existing symlink and permissions.

    A failed write/replace leaves the previous file intact. Temporary files are
    private, live on the same filesystem, and are removed on every failure path.
    """
    if path.is_symlink() and not path.is_file():
        raise SkillsToggleError("configuration symlink has no regular-file target", "config-invalid")
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = destination.stat().st_mode & 0o777 if destination.exists() else 0o600
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", delete=False,
                                         prefix=".hermes-switchboard-", dir=str(destination.parent)) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _read_json_mapping(path: Path, mapping_key: str) -> tuple[dict, str]:
    """Read settings without interpreting corrupt/unreadable data as empty."""
    if not path.exists() and not path.is_symlink():
        return {}, ""
    if not path.is_file():
        raise SkillsToggleError("configuration is not a regular file", "config-invalid")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate configuration key")
            result[key] = value
        return result

    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text, object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SkillsToggleError("configuration cannot be read as valid JSON; repair it before making changes",
                                "config-invalid") from exc
    if not isinstance(data, dict) or (mapping_key in data and not isinstance(data[mapping_key], dict)):
        raise SkillsToggleError("configuration must be an object containing a mapping for " + mapping_key,
                                "config-invalid")
    return data, text


_SECRET_LOG_KEY_RE = re.compile(
    r"(?:api[_-]?key|apikey|secret|token|password|credential|authorization|private[_-]?key|access[_-]?key|env|headers|args|url)",
    re.I,
)


def _redact_log_record(value: object, key: str = "") -> object:
    """Recursively prevent credentials and environment values reaching logs."""
    if key and _SECRET_LOG_KEY_RE.search(key):
        if key.lower() == "env" and isinstance(value, dict):
            return sorted(str(name) for name in value)
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact_log_record(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_log_record(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_log_record(item) for item in value]
    return value


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
        return cls(home, tools, log_path=home / "data" / PLUGIN_ID / "mutations.log")

    # -- logging -----------------------------------------------------------

    def _log(self, **rec: object) -> None:
        if not self.log_path:
            return
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        rec.setdefault("plugin", PLUGIN_ID)
        rec = _redact_log_record(rec)
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
            if not cat_dir.is_dir() or cat_dir.name.startswith(".") or not is_inside(cat_dir, self.skills_root_resolved):
                continue
            for skill_dir in sorted(cat_dir.iterdir()):
                if not skill_dir.is_dir() or skill_dir.name.startswith(".") or not is_inside(skill_dir, self.skills_root_resolved):
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file() or not is_inside(skill_md, skill_dir.resolve()):
                    continue
                try:
                    raw = skill_md.read_text(encoding="utf-8", errors="replace")[:8192]
                except OSError:
                    raw = ""
                name, description = parse_skill_markdown(raw)
                skills[f"{cat_dir.name}/{skill_dir.name}"] = {
                    "name": skill_dir.name,
                    "category": cat_dir.name,
                    "front_name": name,
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
        if tool.get("scope") in ("global", "project"):
            return validate_scoped_target(tool)
        return expanded

    def _inventory_dir(self, tool_id: str) -> Path | None:
        try:
            return self.tool_dir(tool_id)
        except (SkillsToggleError, OSError, ValueError):
            return None  # state() supplies a visible path_error and recovery action

    def _tool_states(self, skill: dict, disabled: set[str]) -> dict:
        states: dict[str, dict] = {}
        for tool_id in self.tools:
            try:
                states[tool_id] = self._one_state(skill, tool_id, disabled)
            except (SkillsToggleError, OSError, ValueError):
                states[tool_id] = {"state": "scope-error"}
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
        probes = [
            self.skills_root,
            self.config_path,
            user_config_path(self.home),
            legacy_user_config_path(self.home),
        ] + cat_dirs
        for tool_id in self.tools:
            try:
                d = self.tool_dir(tool_id)
                if d is not None:
                    probes.append(d)
            except (SkillsToggleError, OSError, ValueError):
                parts.append((tool_id, "scope-error"))
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
        indexed = self._scan_skills()
        if skill_id not in indexed:
            raise SkillsToggleError(
                f"unknown skill {skill_id!r} — not present under {self.skills_root}", "unknown-skill"
            )
        name = indexed[skill_id]["name"]
        if sum(1 for item in indexed.values() if os.path.normcase(item["name"]) == os.path.normcase(name)) > 1:
            raise SkillsToggleError("multiple canonical skills share this target folder name; rename the duplicate first",
                                    "ambiguous-skill")
        return skill_id

    def _validate_tool(self, tool_id: object) -> str:
        if not isinstance(tool_id, str) or tool_id not in self.tools:
            raise SkillsToggleError(f"unknown tool {tool_id!r}", "unknown-tool")
        tool = self.tools[tool_id]
        if tool.get("read_only"):
            raise SkillsToggleError("unverified legacy client; review and save an explicit Custom path first", "client-unverified")
        d = self.tool_dir(tool_id)
        if d is not None and (is_inside(d, self.skills_root_resolved) or is_inside(self.skills_root_resolved, d)):
            raise SkillsToggleError("target overlaps the canonical Hermes library", "scope-escape")
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
                path_error = None
                try:
                    d = self.tool_dir(tool_id)
                except (SkillsToggleError, OSError, ValueError):
                    d = None
                    path_error = "Target no longer matches its selected scope. Review the client path."
                tools_meta.append(
                    {
                        "id": tool_id,
                        "label": tool.get("label", tool_id),
                        "dir": str(d) if d is not None else None,
                        "present": False if path_error else (True if d is None else d.is_dir()),
                        "special": tool.get("special"),
                        "optional": bool(tool.get("optional")),
                        "configured": bool(tool.get("configured")),
                        "client_id": tool.get("client_id"),
                        "scope": tool.get("scope", "custom" if d else "global"),
                        "project_root": tool.get("project_root"),
                        "read_only": bool(tool.get("read_only")) or bool(path_error),
                        "path_error": path_error,
                        "verification": tool.get("verification", "custom"),
                        "notes": tool.get("notes", ""),
                        "shared_with": [],
                    }
                )
            for meta in tools_meta:
                if meta["dir"]:
                    meta["shared_with"] = [other["label"] for other in tools_meta
                                           if other["id"] != meta["id"] and other["dir"]
                                           and same_path(Path(other["dir"]), Path(meta["dir"]))]
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
                "capabilities": {"client_catalog": CATALOG_VERSION, "scopes": ["global", "project", "custom"], "receipt_undo": True},
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
            tool_dir = self._inventory_dir(tool_id)
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
            "path_errors": [{"tool": tool["id"], "error": tool["path_error"]}
                            for tool in payload["tools"] if tool.get("path_error")],
        }

    # -- routes: mutate ----------------------------------------------------

    def _backup(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak.hermes-switchboard.{stamp}")
        n = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak.hermes-switchboard.{stamp}-{n}")
            n += 1
        shutil.copy2(path, backup)
        return str(backup)

    def _backup_config(self) -> str | None:
        if not self.config_path.is_file():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.config_path.with_name(f"config.yaml.bak.hermes-switchboard.{stamp}")
        n = 1
        while backup.exists():
            backup = self.config_path.with_name(f"config.yaml.bak.hermes-switchboard.{stamp}-{n}")
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
        _atomic_write_text(self.config_path, new_text)
        self._log(action="config-edit", tool="hermes", skill=skill_name, enabled=enabled, backup=backup)
        return "config-updated"

    def _validate_client_skill(self, skill: dict, tool_id: str) -> None:
        tool = self.tools[tool_id]
        if tool.get("scope") not in ("global", "project"):
            return
        client = catalog_client(tool.get("client_id"))
        name = skill["name"]
        if name.casefold() in {value.casefold() for value in client.get("reserved_names", [])}:
            raise SkillsToggleError("this skill folder name is reserved by the selected client", "skill-incompatible")
        if (not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64
                or skill.get("front_name") != name or not skill.get("description")):
            raise SkillsToggleError("this client needs an Agent Skills name matching its folder and a description; fix SKILL.md first",
                                    "skill-incompatible")

    def _link_tool(self, skill: dict, tool_id: str, enabled: bool) -> tuple[str, dict]:
        """Enable/disable one symlink. Returns (action, new_state)."""
        tool_dir = self.tool_dir(tool_id)
        if tool_dir is None:
            raise SkillsToggleError(f"tool {tool_id} has no target dir configured", "no-dir")
        if enabled:
            self._validate_client_skill(skill, tool_id)
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
        if state == "broken-link":
            base = Path(target) if os.path.isabs(target) else link.parent / target
            if not is_inside(base, self.skills_root_resolved):
                raise SkillsToggleError("broken symlink points outside the Hermes skills tree; left untouched",
                                        "foreign-link")

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
            temporary = None
            try:
                if state == "broken-link":
                    temporary = link.with_name(".hermes-switchboard-" + uuid.uuid4().hex)
                    os.symlink(str(skill_dir_resolved), str(temporary), target_is_directory=True)
                    if not link.is_symlink() or os.readlink(link) != target:
                        raise SkillsToggleError("target changed during repair; refresh and review it again",
                                                "changed-since-preview")
                    self._replace_managed_link(temporary, link)
                else:
                    os.symlink(str(skill_dir_resolved), str(link), target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                raise SkillsToggleError(
                    f"could not create symlink at {link}: {exc} "
                    "(on Windows, enable Developer Mode or run elevated)",
                    "symlink-unsupported",
                ) from exc
            finally:
                if temporary is not None and temporary.is_symlink():
                    temporary.unlink()
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
        """Backward-compatible bulk toggle, now backed by one plan/apply pass.

        The legacy response keys remain intact.  In particular, entries that
        are already in the requested state are still successful no-ops.
        """
        plan = self.plan_bulk(skill_ids, tool_id, enabled)
        applied = self.execute_bulk(plan["would_change"], tool_id, enabled)
        results = []
        for sid in skill_ids:
            applied_item = next((item for item in applied["results"] if item["skill"] == sid), None)
            refused_item = next((item for item in plan["refused"] if item["skill"] == sid), None)
            if applied_item is not None:
                results.append(applied_item)
            elif sid in plan["already_satisfied"]:
                state = "enabled" if enabled else ("disabled" if tool_id == "hermes" else "missing")
                results.append({"skill": sid, "ok": True, "state": state})
            else:
                results.append({
                    "skill": sid, "ok": False, "error": refused_item["reason"], "code": refused_item["code"]
                })
        return {
            "ok": True,
            "results": results,
            # Legacy /toggle-bulk counted every successful item, including
            # no-ops; retain that contract for the item-8 frontend.
            "changed": sum(1 for item in results if item["ok"]),
            "failed": sum(1 for item in results if not item["ok"]),
            "receipt": applied["receipt"],
            **({"receipt_error": applied["receipt_error"]} if "receipt_error" in applied else {}),
        }

    def _bulk_inputs(
        self, skill_ids: object, tool_id: object, enabled: object, allow_empty: bool = False
    ) -> tuple[list, str, bool]:
        if not isinstance(skill_ids, list) or (not skill_ids and not allow_empty):
            raise SkillsToggleError("'skills' must be a non-empty list of skill ids", "invalid-body")
        tool = self._validate_tool(tool_id)
        if not isinstance(enabled, bool):
            raise SkillsToggleError("'enabled' must be a boolean", "invalid-body")
        ordered = []
        for sid in skill_ids:
            if sid not in ordered:
                ordered.append(sid)
        return ordered, tool, enabled

    def _bulk_disposition(self, skill: dict, tool_id: str, enabled: bool, disabled: set[str]) -> dict:
        """Read-only mirror of toggle's state-dependent decisions."""
        state_info = self._one_state(skill, tool_id, disabled)
        state = state_info["state"]
        desired = "enabled" if enabled else ("disabled" if tool_id == "hermes" else "missing")
        if tool_id == "hermes":
            return {"kind": "satisfied" if state == desired else "change", "state": state, "next": desired}

        tool_dir = self.tool_dir(tool_id)
        if tool_dir is None:
            return {
                "kind": "refused", "state": state, "next": state, "code": "no-dir",
                "reason": f"tool {tool_id} has no target dir configured",
            }
        link = tool_dir / skill["name"]
        if state == "foreign-link":
            target = state_info.get("target")
            if enabled:
                reason = f"{link} is a symlink to {target} (outside this skill) — resolve it manually before enabling"
            else:
                reason = f"{link} is a foreign symlink ({target}) — refusing to remove"
            return {"kind": "refused", "state": state, "next": state, "code": "foreign-link", "reason": reason}
        if state == "unmanaged-dir":
            if enabled:
                reason = f"{link} is a real directory/file, not a symlink — refusing to overwrite (never delete real dirs)"
            else:
                reason = f"{link} is a real directory/file — refusing to remove"
            return {"kind": "refused", "state": state, "next": state, "code": "unmanaged-dir", "reason": reason}
        if state == "broken-link":
            target = state_info.get("target")
            base = Path(target) if os.path.isabs(target) else (link.parent / target)
            if not is_inside(base.resolve(), self.skills_root_resolved):
                return {
                    "kind": "refused", "state": state, "next": state, "code": "foreign-link",
                    "reason": f"{link} points at {target} which is outside the skills tree — refusing",
                }
        if enabled and state == "missing" and tool_dir.exists() and not tool_dir.is_dir():
            return {
                "kind": "refused", "state": state, "next": state, "code": "not-a-dir",
                "reason": f"{tool_dir} exists but is not a directory — refusing to replace it",
            }
        return {"kind": "satisfied" if state == desired else "change", "state": state, "next": desired}

    def plan_bulk(self, skill_ids: object, tool_id: object, enabled: object) -> dict:
        """Build a deterministic, mutation-free plan for explicit skill ids."""
        with self._lock:
            ordered, tool_id, enabled = self._bulk_inputs(skill_ids, tool_id, enabled)
            skills = self._scan_skills()
            disabled = self._disabled_set()
            hermes_text = ""
            hermes_read_error = None
            if tool_id == "hermes" and self.config_path.is_file():
                try:
                    hermes_text = self.config_path.read_text(encoding="utf-8")
                    disabled = parse_disabled(hermes_text)
                except OSError as exc:
                    hermes_read_error = SkillsToggleError(
                        f"cannot read {self.config_path}: {exc}", "config-unreadable"
                    )
            would_change = []
            already_satisfied = []
            refused = []
            sample = []
            for sid in ordered:
                try:
                    valid_sid = self._validate_skill(sid)
                except SkillsToggleError as exc:
                    refused.append({"skill": sid, "code": exc.code, "reason": str(exc)})
                    continue
                skill = skills[valid_sid]
                if enabled and tool_id != "hermes":
                    try:
                        self._validate_client_skill(skill, tool_id)
                    except SkillsToggleError as exc:
                        refused.append({"skill": valid_sid, "code": exc.code, "reason": str(exc)})
                        continue
                disposition = self._bulk_disposition(skill, tool_id, enabled, disabled)
                if tool_id == "hermes":
                    if hermes_read_error is not None:
                        disposition = {
                            "kind": "refused", "state": disposition["state"], "next": disposition["state"],
                            "code": hermes_read_error.code, "reason": str(hermes_read_error),
                        }
                    else:
                        try:
                            next_text = set_disabled_member(hermes_text, skill["name"], add=not enabled)
                            disposition = {
                                **disposition,
                                "kind": "satisfied" if next_text == hermes_text else "change",
                            }
                            hermes_text = next_text
                        except ConfigEditError as exc:
                            disposition = {
                                "kind": "refused", "state": disposition["state"], "next": disposition["state"],
                                "code": "config-edit", "reason": f"config.yaml edit refused: {exc}",
                            }
                if disposition["kind"] == "refused":
                    refused.append({"skill": valid_sid, "code": disposition["code"], "reason": disposition["reason"]})
                elif disposition["kind"] == "satisfied":
                    already_satisfied.append(valid_sid)
                else:
                    would_change.append(valid_sid)
                    if len(sample) < 5:
                        sample.append({
                            "skill_id": valid_sid,
                            "name": skill["name"],
                            "category": skill["category"],
                            "current_state": disposition["state"],
                            "next_state": disposition["next"],
                        })
            return {
                "ok": True,
                "would_change": would_change,
                "already_satisfied": already_satisfied,
                "refused": refused,
                "ordered_explicit_ids": ordered,
                "sample": sample,
                "totals": {
                    "would_change": len(would_change),
                    "already_satisfied": len(already_satisfied),
                    "refused": len(refused),
                },
            }

    def _receipt_path(self, receipt_id: object) -> Path:
        if not isinstance(receipt_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", receipt_id):
            raise SkillsToggleError("invalid receipt identifier", "invalid-receipt")
        root = self.home / "data" / PLUGIN_ID / "receipts"
        if not is_inside(root, self.home):
            raise SkillsToggleError("receipt storage resolves outside the Hermes home", "unsafe-receipt")
        path = root / (hashlib.sha256(receipt_id.encode("utf-8")).hexdigest() + ".json")
        if path.is_symlink():
            raise SkillsToggleError("receipt files must not be symlinks", "unsafe-receipt")
        return path

    def _reserve_receipt(self, receipt: dict) -> None:
        """Write before-images before changing anything; never reuse an id."""
        path = self._receipt_path(receipt["receipt_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise SkillsToggleError("a receipt with this identifier already exists", "receipt-exists") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            path.unlink(missing_ok=True)
            raise

    def _save_receipt(self, receipt: dict) -> None:
        _atomic_write_text(self._receipt_path(receipt["receipt_id"]),
                           json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")

    def get_bulk_receipt(self, receipt_id: object) -> dict:
        path = self._receipt_path(receipt_id)
        if not path.is_file():
            raise SkillsToggleError("receipt not found", "unknown-receipt")
        data, _ = _read_json_mapping(path, "context")
        if data.get("format") != "switchboard-bulk-v1" or data.get("receipt_id") != receipt_id:
            raise SkillsToggleError("receipt format does not match this operation", "invalid-receipt")
        # Validate the whole document before undo touches its first entry.
        # A truncated or hand-edited receipt is evidence to inspect, not a plan.
        context = data.get("context")
        items = data.get("items")
        if (not isinstance(context, dict) or not isinstance(items, list)
                or not isinstance(data.get("tool"), str)
                or data.get("status") not in ("applying", "complete", "undone")
                or any(not isinstance(context.get(key), str) or not Path(context[key]).is_absolute()
                       for key in ("target_dir", "skills_root"))):
            raise SkillsToggleError("receipt structure is incomplete", "invalid-receipt")
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise SkillsToggleError("receipt contains an invalid entry", "invalid-receipt")
            if not item.get("ok"):
                continue
            sid = item.get("skill")
            if (not isinstance(sid, str) or not _SKILL_ID_RE.fullmatch(sid)
                    or any(part in (".", "..") or "\\" in part for part in sid.split("/"))
                    or sid in seen or not isinstance(item.get("from"), str)
                    or not isinstance(item.get("to"), str)):
                raise SkillsToggleError("receipt contains an invalid skill", "invalid-receipt")
            seen.add(sid)
            for key in ("before", "after"):
                image = item.get(key)
                kind = image.get("kind") if isinstance(image, dict) else None
                valid = (kind == "config" and isinstance(image.get("disabled"), bool)) if data["tool"] == "hermes" else (
                    kind == "missing" or (kind == "symlink" and isinstance(image.get("target"), str)
                    and "\0" not in image["target"] and isinstance(image.get("identity"), list)
                    and len(image["identity"]) == 4 and all(isinstance(n, int) for n in image["identity"])
                    and isinstance(image.get("directory", True), bool)))
                if not valid:
                    raise SkillsToggleError("receipt contains an invalid before/after image", "invalid-receipt")
                if kind == "symlink":
                    image.setdefault("directory", True)  # old receipts describe skill directories
        if data.get("undo_result") is not None and (data.get("status") != "undone"
                or not isinstance(data["undo_result"], dict) or data["undo_result"].get("ok") is not True):
            raise SkillsToggleError("receipt contains an invalid undo result", "invalid-receipt")
        return {"ok": True, "receipt": data}

    @staticmethod
    def _entry_snapshot(path: Path) -> dict:
        """Record link identity as well as its destination to detect replacement."""
        try:
            stat = path.lstat()
            if path.is_symlink():
                return {"kind": "symlink", "target": os.readlink(path),
                        "directory": bool(getattr(stat, "st_file_attributes", 16) & 16),
                        "identity": [stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns]}
            return {"kind": "protected"}
        except FileNotFoundError:
            return {"kind": "missing"}
        except OSError:
            return {"kind": "unreadable"}

    @staticmethod
    def _replace_managed_link(temporary: Path, destination: Path) -> None:
        """Replace a checked link without discarding its before-image on error.

        NTFS refuses replacement of directory symlinks with os.replace. Move
        the old link aside there, then rename exclusively and roll back on
        failure. A process interruption leaves that recoverable link beside
        the destination, not a deleted skill directory.
        """
        if sys.platform != "win32":
            os.replace(temporary, destination)
            return
        if not destination.is_symlink() or not temporary.is_symlink():
            raise SkillsToggleError("link changed before replacement", "changed-since-preview")
        parked = destination.with_name(".hermes-switchboard-previous-" + uuid.uuid4().hex)
        os.rename(destination, parked)
        try:
            # Unlike replace, Windows rename refuses a newly occupied path.
            os.rename(temporary, destination)
        except OSError:
            if not destination.exists() and not destination.is_symlink():
                os.rename(parked, destination)
            raise
        parked.unlink()

    def undo_bulk(self, receipt_id: object) -> dict:
        """Restore only recorded changes whose post-apply state still matches.

        Before-images survive a restart. An interrupted or unpersisted operation
        requires review, never a guessed inverse. Completed undo is idempotent.
        """
        with self._lock:
            receipt = self.get_bulk_receipt(receipt_id)["receipt"]
            if receipt.get("undo_result") is not None:
                return receipt["undo_result"]
            if receipt.get("status") != "complete":
                raise SkillsToggleError("this operation has incomplete evidence; inspect the saved receipt and backups",
                                        "incomplete-receipt")
            context = receipt.get("context", {})
            tool_id = self._validate_tool(receipt.get("tool"))
            expected_dir = context.get("target_dir")
            current_dir = self.config_path.resolve() if tool_id == "hermes" else self.tool_dir(tool_id)
            if (current_dir is None or not isinstance(expected_dir, str)
                    or not same_path(current_dir, Path(expected_dir))
                    or not same_path(self.skills_root, Path(context.get("skills_root", "")))):
                raise SkillsToggleError("the target changed since this receipt; no files were modified", "target-changed")
            results = []
            config_ready = []
            for item in receipt["items"]:
                if not item.get("ok") or item.get("from") == item.get("to"):
                    continue
                sid = item["skill"]
                before, after = item.get("before", {}), item.get("after", {})
                name = sid.split("/")[-1]
                try:
                    if not _SKILL_ID_RE.fullmatch(sid) or name in (".", "..") or "\\" in name:
                        raise SkillsToggleError("receipt contains an invalid skill name", "invalid-receipt")
                    if tool_id == "hermes":
                        now = {"kind": "config", "disabled": name in self._disabled_set()}
                    else:
                        link = current_dir / name
                        now = self._entry_snapshot(link)
                    if now != after or now.get("kind") in ("protected", "unreadable"):
                        raise SkillsToggleError("entry changed after this operation; left untouched", "changed-since-apply")
                    if tool_id == "hermes":
                        config_ready.append((sid, name, before["disabled"]))
                        continue
                    if before.get("kind") == "missing" and now.get("kind") == "symlink":
                        target = Path(now["target"])
                        resolved = target if target.is_absolute() else link.parent / target
                        if not is_inside(resolved, self.skills_root):
                            raise SkillsToggleError("managed target changed; left untouched", "changed-since-apply")
                        link.unlink()
                    elif before.get("kind") == "symlink":
                        raw = before.get("target")
                        if not isinstance(raw, str):
                            raise SkillsToggleError("receipt has no original link target", "invalid-receipt")
                        target = Path(raw)
                        resolved = target if target.is_absolute() else link.parent / target
                        if not is_inside(resolved, self.skills_root):
                            raise SkillsToggleError("original target is no longer inside the skill library", "changed-since-apply")
                        if now.get("kind") == "missing":
                            # Exclusive symlink creation refuses a concurrently created entry.
                            os.symlink(raw, link, target_is_directory=before["directory"])
                        else:
                            temporary = link.with_name(".hermes-switchboard-" + uuid.uuid4().hex)
                            try:
                                os.symlink(raw, temporary, target_is_directory=before["directory"])
                                if self._entry_snapshot(link) != after:
                                    raise SkillsToggleError("entry changed during undo; left untouched", "changed-since-apply")
                                self._replace_managed_link(temporary, link)
                            finally:
                                if temporary.is_symlink():
                                    temporary.unlink()
                    else:
                        raise SkillsToggleError("receipt has no safe before-image", "invalid-receipt")
                    results.append({"skill": sid, "ok": True, "state": item["from"]})
                except (SkillsToggleError, OSError) as exc:
                    results.append({"skill": sid, "ok": False, "error": str(exc),
                                    "code": exc.code if isinstance(exc, SkillsToggleError) else "filesystem-error"})
            if config_ready:
                try:
                    text = self.config_path.read_text(encoding="utf-8") if self.config_path.is_file() else ""
                    new_text = text
                    for _, name, disabled in config_ready:
                        new_text = set_disabled_member(new_text, name, add=disabled)
                    if new_text != text:
                        self._backup_config()
                        _atomic_write_text(self.config_path, new_text)
                    results.extend({"skill": sid, "ok": True, "state": "disabled" if disabled else "enabled"}
                                   for sid, _, disabled in config_ready)
                except (ConfigEditError, SkillsToggleError, OSError) as exc:
                    results.extend({"skill": sid, "ok": False, "error": str(exc), "code": "config-write"}
                                   for sid, _, _ in config_ready)
            changed = sum(bool(row["ok"]) for row in results)
            failed = len(results) - changed
            summary = {"receipt_id": receipt_id, "tool": tool_id, "changed": changed,
                       "failed": failed, "refused": failed, "undo_available": False, "status": "undone"}
            result = {"ok": True, "changed": changed, "failed": failed, "results": results, "receipt": summary}
            receipt.update(status="undone", undo_available=False, undo_result=result)
            try:
                self._save_receipt(receipt)
            except (SkillsToggleError, OSError) as exc:
                result["receipt_error"] = "Undo completed but its final receipt could not be saved; inspect the changed entries."
            self._log(action="bulk-undo", receipt_id=receipt_id, tool=tool_id, changed=changed, failed=failed)
            if changed:
                self.invalidate()
            return result

    def execute_bulk(
        self, planned_ids: object, tool_id: object, enabled: object, receipt_id: object = None
    ) -> dict:
        """Apply exactly the ids supplied by a reviewed plan and emit a receipt."""
        with self._lock:
            ordered, tool_id, enabled = self._bulk_inputs(planned_ids, tool_id, enabled, allow_empty=True)
            if receipt_id is None:
                receipt_id = uuid.uuid4().hex[:12]
            if not isinstance(receipt_id, str) or not re.match(r"^[A-Za-z0-9._:-]{1,128}$", receipt_id):
                raise SkillsToggleError("'receipt_id' must be a short identifier", "invalid-body")

            skills = self._scan_skills()
            disabled = self._disabled_set()
            results = []
            receipt_items = []
            ready = []
            for sid in ordered:
                try:
                    valid_sid = self._validate_skill(sid)
                except SkillsToggleError as exc:
                    results.append({
                        "skill": sid, "ok": False, "state": "unknown", "error": str(exc),
                        "code": exc.code, "changed_since_preview": True,
                    })
                    receipt_items.append({"skill": sid, "ok": False, "from": "unknown", "to": "unknown"})
                    continue
                skill = skills[valid_sid]
                if enabled and tool_id != "hermes":
                    try:
                        self._validate_client_skill(skill, tool_id)
                    except SkillsToggleError as exc:
                        results.append({"skill": valid_sid, "ok": False, "state": "refused",
                                        "code": exc.code, "error": str(exc), "changed_since_preview": True})
                        receipt_items.append({"skill": valid_sid, "ok": False, "from": "refused", "to": "refused"})
                        continue
                disposition = self._bulk_disposition(skill, tool_id, enabled, disabled)
                if disposition["kind"] != "change":
                    if disposition["kind"] == "refused":
                        code = disposition["code"]
                        error = disposition["reason"]
                    else:
                        code = "changed-since-preview"
                        error = f"{valid_sid} is already {disposition['state']} — state changed since preview"
                    results.append({
                        "skill": valid_sid, "ok": False, "state": disposition["state"], "error": error,
                        "code": code, "changed_since_preview": True,
                    })
                    receipt_items.append({
                        "skill": valid_sid, "ok": False, "from": disposition["state"], "to": disposition["state"]
                    })
                    continue
                ready.append((valid_sid, skill, disposition))

            context_dir = self.config_path.resolve() if tool_id == "hermes" else self.tool_dir(tool_id)
            before_images = {
                sid: ({"kind": "config", "disabled": skill["name"] in disabled} if tool_id == "hermes"
                      else self._entry_snapshot(context_dir / skill["name"]))
                for sid, skill, _ in ready
            }
            durable = {"format": "switchboard-bulk-v1", "receipt_id": receipt_id, "tool": tool_id,
                       "enabled": enabled, "status": "applying", "undo_available": False,
                       "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "context": {"target_dir": str(context_dir.resolve()) if context_dir else None,
                                   "skills_root": str(self.skills_root.resolve())},
                       "items": [{"skill": sid, "before": before_images[sid]} for sid, _, _ in ready]}
            try:
                self._reserve_receipt(durable)
            except OSError as exc:
                raise SkillsToggleError("receipt storage is not writable; no target changes were made", "receipt-write") from exc

            changed_ids = set()
            after_images = {}
            if tool_id == "hermes" and ready:
                try:
                    text = self.config_path.read_text(encoding="utf-8") if self.config_path.is_file() else ""
                    new_text = text
                    for _, skill, _ in ready:
                        new_text = set_disabled_member(new_text, skill["name"], add=not enabled)
                    backup = self._backup_config() if new_text != text else None
                    if new_text != text:
                        self.config_path.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write_text(self.config_path, new_text)
                        self._log(
                            action="config-edit", tool="hermes", skills=[sid for sid, _, _ in ready],
                            enabled=enabled, backup=backup,
                        )
                    changed_ids.update(sid for sid, _, _ in ready)
                except (ConfigEditError, OSError) as exc:
                    code = "config-edit" if isinstance(exc, ConfigEditError) else "config-write"
                    for sid, _, disposition in ready:
                        results.append({"skill": sid, "ok": False, "state": disposition["state"], "error": str(exc), "code": code})
                        receipt_items.append({"skill": sid, "ok": False, "from": disposition["state"], "to": disposition["state"]})
            elif tool_id != "hermes":
                for sid, skill, disposition in ready:
                    try:
                        _, new_state = self._link_tool(skill, tool_id, enabled)
                        # Capture each mutation before moving to the next skill.
                        after_images[sid] = self._entry_snapshot(context_dir / skill["name"])
                        changed_ids.add(sid)
                        state = new_state["state"]
                        results.append({"skill": sid, "ok": True, "state": state})
                        receipt_items.append({"skill": sid, "ok": True, "from": disposition["state"], "to": state})
                        self._log(action="toggle", skill=sid, tool=tool_id, enabled=enabled, **{"from": disposition["state"], "to": state})
                    except (SkillsToggleError, OSError) as exc:
                        code = exc.code if isinstance(exc, SkillsToggleError) else "filesystem-error"
                        results.append({"skill": sid, "ok": False, "state": disposition["state"], "error": str(exc), "code": code})
                        receipt_items.append({"skill": sid, "ok": False, "from": disposition["state"], "to": disposition["state"]})

            if tool_id == "hermes":
                for sid, _, disposition in ready:
                    if sid in changed_ids:
                        results.append({"skill": sid, "ok": True, "state": disposition["next"]})
                        receipt_items.append({"skill": sid, "ok": True, "from": disposition["state"], "to": disposition["next"]})
                        self._log(action="toggle", skill=sid, tool=tool_id, enabled=enabled, **{"from": disposition["state"], "to": disposition["next"]})

            results.sort(key=lambda item: ordered.index(item["skill"]))
            receipt_items.sort(key=lambda item: ordered.index(item["skill"]))
            changed = sum(1 for item in receipt_items if item["ok"] and item["from"] != item["to"])
            failed = sum(1 for item in receipt_items if not item["ok"])
            refused_codes = {"foreign-link", "unmanaged-dir", "no-dir", "not-a-dir"}
            refused_count = sum(1 for item in results if item.get("code") in refused_codes)
            undone_by = [{"skill": item["skill"], "enabled": not enabled} for item in receipt_items if item["ok"] and item["from"] != item["to"]]
            for item in receipt_items:
                if item["ok"]:
                    item["before"] = before_images[item["skill"]]
                    item["after"] = ({"kind": "config", "disabled": not enabled} if tool_id == "hermes"
                                     else after_images[item["skill"]])
            receipt = {
                **durable, "status": "complete", "undo_available": bool(changed),
                "items": receipt_items, "changed": changed, "failed": failed,
                "refused": refused_count, "undone_by": undone_by,
            }
            response = {"ok": True, "results": results, "receipt": receipt, "changed": changed, "failed": failed}
            try:
                self._save_receipt(receipt)
            except (SkillsToggleError, OSError):
                receipt.update(status="persistence-failed", undo_available=False)
                response["receipt_error"] = "Changes completed but the final receipt could not be saved; review the before-images and backups."
            self._log(action="bulk", receipt_id=receipt_id, tool=tool_id, enabled=enabled, receipt=receipt)
            if changed:
                self.invalidate()
            return response

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
                sid = next(
                    (s for s in skills.values() if same_path(s["dir"], resolved)), None
                )
                inside = is_inside(resolved, self.skills_root_resolved)
                if sid is not None:
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
            tool_dir = self._inventory_dir(tool_id)
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

    @staticmethod
    def _validate_import_category(category: object) -> str:
        if not isinstance(category, str) or not re.match(r"^[^/\0]+$", category) or category in (".", "..") or "\\" in category:
            raise SkillsToggleError(f"invalid category {category!r}", "invalid-category")
        return category

    def import_plan(self, tool_ids: object, scan_roots: object, category: object = "imported") -> dict:
        """Build a mutation-free adoption plan across known tool dirs and
        owner-selected roots. Selected roots are read sources only; they never
        alter the canonical Hermes destination."""
        category = self._validate_import_category(category)
        if not isinstance(tool_ids, list) or not tool_ids:
            raise SkillsToggleError("'tools' must be a non-empty list", "invalid-body")
        ordered_tools = []
        for tool_id in tool_ids:
            tool_id = self._validate_tool(tool_id)
            if tool_id == "hermes":
                raise SkillsToggleError("hermes has no importable dir", "no-dir")
            if tool_id not in ordered_tools:
                ordered_tools.append(tool_id)
        if not isinstance(scan_roots, list):
            raise SkillsToggleError("'scan_roots' must be a list", "invalid-body")

        refused = []
        sources = []
        seen_roots = set()
        known_tool_roots = {}
        for known_tool_id in self.tools:
            if known_tool_id == "hermes":
                continue
            known_root = self._inventory_dir(known_tool_id)
            if known_root is not None and known_root.is_dir():
                known_tool_roots.setdefault(_canonical(str(known_root)), known_tool_id)
        for tool_id in ordered_tools:
            root = self.tool_dir(tool_id)
            if root is None or not root.is_dir():
                refused.append({"root": str(root) if root is not None else None, "tool": tool_id, "code": "not-dir"})
                continue
            canonical = _canonical(str(root))
            if canonical not in seen_roots:
                sources.append({"root": root, "source": str(root), "tool": tool_id})
                seen_roots.add(canonical)

        selected = []
        for raw_root in scan_roots:
            if not isinstance(raw_root, str) or not raw_root.strip():
                refused.append({"root": str(raw_root), "code": "invalid-root"})
                continue
            try:
                root = expand_path(raw_root)
            except (OSError, ValueError):
                refused.append({"root": raw_root, "code": "invalid-root"})
                continue
            if root.is_symlink():
                refused.append({"root": raw_root, "code": "symlink-root"})
                continue
            if not root.is_dir():
                refused.append({"root": raw_root, "code": "not-dir"})
                continue
            selected.append({"raw": raw_root, "root": root, "canonical": _canonical(str(root))})

        # Overlapping owner roots would scan one subtree twice and make source
        # identity ambiguous. Keep the outer root and explicitly refuse nested
        # selections. Known tool dirs are exact scan locations, not recursive.
        for candidate in selected:
            nested = any(
                candidate["canonical"] != other["canonical"]
                and is_inside(candidate["root"], other["root"])
                for other in selected
            )
            if nested:
                refused.append({"root": candidate["raw"], "code": "nested-root"})
                continue
            if candidate["canonical"] in seen_roots:
                continue
            sources.append({
                "root": candidate["root"], "source": str(candidate["root"]),
                "tool": known_tool_roots.get(candidate["canonical"]),
            })
            seen_roots.add(candidate["canonical"])

        skills = self._scan_skills()
        entries = []
        for source in sources:
            root = source["root"]
            try:
                children = sorted(root.iterdir())
            except OSError:
                refused.append({"root": source["source"], "tool": source["tool"], "code": "unreadable"})
                continue
            for child in children:
                # iterdir is one level only, but retain an explicit boundary
                # check so future scanner changes cannot introduce traversal.
                if not same_path(child.parent, root):
                    refused.append({"root": source["source"], "path": str(child), "code": "outside-root"})
                    continue
                info = self._classify_entry(child, skills)
                if not info:
                    continue
                row = dict(info)
                row.update({
                    "source": source["source"],
                    "tool": source["tool"],
                    "path": str(child),
                    "conflict": bool(info.get("conflict")),
                    "drifted": bool(info.get("drifted")),
                })
                if row["kind"] == "managed-mismatch":
                    row["kind"] = "name-conflict"
                    row["conflict"] = True
                elif row["kind"] == "unmanaged-skill" and row["conflict"]:
                    row["kind"] = "drifted" if row["drifted"] else "identical-duplicate"
                entries.append(row)

        grouped = {}
        for row in entries:
            grouped.setdefault(row["name"], []).append(row)
        duplicate_groups = []
        for name, rows in grouped.items():
            if len(rows) < 2:
                continue
            duplicate_groups.append({
                "name": name,
                "entries": [{"source": row["source"], "tool": row["tool"], "kind": row["kind"]} for row in rows],
            })
            for row in rows:
                row["conflict"] = True
                if row["kind"] == "unmanaged-skill":
                    row["kind"] = "name-conflict"

        adoptable = [row for row in entries if row["kind"] == "unmanaged-skill" and not row["conflict"]]
        conflicts = [row for row in entries if row["conflict"]]
        drifted = [row for row in entries if row["drifted"]]
        kinds = {}
        for row in entries:
            kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        return {
            "ok": True,
            "category": category,
            "entries": entries,
            "adoptable": adoptable,
            "conflicts": conflicts,
            "drifted": drifted,
            "refused": refused,
            "duplicate_groups": duplicate_groups,
            "totals": {
                "sources": len(sources), "entries": len(entries), "adoptable": len(adoptable),
                "conflicts": len(conflicts), "drifted": len(drifted), "refused": len(refused), "kinds": kinds,
            },
        }

    def import_apply_plan(self, entries: object, category: object = "imported") -> dict:
        """Apply only explicit source/name/tool tuples from a reviewed plan.

        Every source is reclassified while holding the mutation lock. Plain
        scan roots are copied into Hermes and left untouched; configured tool
        roots use the legacy copy/backup/link rollback sequence.
        """
        with self._lock:
            category = self._validate_import_category(category)
            if not isinstance(entries, list) or not entries:
                raise SkillsToggleError("'entries' must be a non-empty list", "invalid-body")

            requested = []
            for item in entries:
                if not isinstance(item, dict):
                    raise SkillsToggleError("each entry must be an object", "invalid-body")
                name = item.get("name")
                source_value = item.get("source")
                tool_id = item.get("tool")
                if (
                    not isinstance(name, str)
                    or not name
                    or name in (".", "..")
                    or "/" in name
                    or "\\" in name
                    or "\0" in name
                ):
                    raise SkillsToggleError(f"invalid import name {name!r}", "invalid-name")
                if not isinstance(source_value, str) or not source_value.strip():
                    raise SkillsToggleError("entry source must be a directory path", "invalid-body")
                try:
                    source = expand_path(source_value)
                except (OSError, ValueError) as exc:
                    raise SkillsToggleError(f"invalid source {source_value!r}", "invalid-root") from exc
                if source.is_symlink():
                    raise SkillsToggleError(f"source {source_value!r} is a symlink", "symlink-root")
                if not source.is_dir():
                    raise SkillsToggleError(f"source {source_value!r} is not a directory", "not-dir")
                if tool_id is not None:
                    tool_id = self._validate_tool(tool_id)
                    if tool_id == "hermes":
                        raise SkillsToggleError("hermes has no importable dir", "no-dir")
                    configured = self.tool_dir(tool_id)
                    if configured is None or not configured.is_dir() or not same_path(source, configured):
                        raise SkillsToggleError(
                            f"source does not match configured tool dir for {tool_id}", "source-mismatch"
                        )
                src = source / name
                if not same_path(src.parent, source):
                    raise SkillsToggleError(f"entry {name!r} escapes its source root", "outside-root")
                requested.append({"name": name, "source": source, "tool": tool_id, "src": src})

            receipt_id = uuid.uuid4().hex[:12]
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            results = []
            undo = []
            skills = self._scan_skills()
            tree_names = {skill["name"] for skill in skills.values()}

            for item in requested:
                name = item["name"]
                src = item["src"]
                info = self._classify_entry(src, skills)
                if not info or info["kind"] != "unmanaged-skill":
                    code = info["kind"] if info else "missing-entry"
                    results.append({
                        "name": name, "source": str(item["source"]), "tool": item["tool"],
                        "ok": False, "code": code, "error": "entry is no longer an adoptable skill",
                        "changed_since_preview": True,
                    })
                    continue
                if info.get("conflict") or name in tree_names:
                    results.append({
                        "name": name, "source": str(item["source"]), "tool": item["tool"],
                        "ok": False, "code": "changed-since-preview",
                        "error": "a same-name Hermes skill now exists",
                        "changed_since_preview": True, "conflict": True,
                    })
                    continue

                dest_dir = self.skills_root / category
                dest = dest_dir / name
                if dest.exists() or dest.is_symlink():
                    results.append({
                        "name": name, "source": str(item["source"]), "tool": item["tool"],
                        "ok": False, "code": "changed-since-preview",
                        "error": f"destination {dest} now exists", "changed_since_preview": True,
                    })
                    continue
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(src, dest, symlinks=True)
                except OSError as exc:
                    shutil.rmtree(dest, ignore_errors=True)
                    results.append({
                        "name": name, "source": str(item["source"]), "tool": item["tool"],
                        "ok": False, "code": "copy-failed", "error": f"copy failed: {exc}",
                        "changed_since_preview": False,
                    })
                    continue

                backup = None
                if item["tool"] is not None:
                    backup_path = item["source"] / f"{name}.hermes-switchboard-backup-{stamp}"
                    try:
                        os.rename(src, backup_path)
                        os.symlink(str(dest.resolve()), str(src))
                        backup = str(backup_path)
                    except OSError as exc:
                        try:
                            if src.is_symlink():
                                src.unlink()
                            if backup_path.is_dir() and not src.exists():
                                os.rename(backup_path, src)
                        except OSError:
                            pass
                        shutil.rmtree(dest, ignore_errors=True)
                        results.append({
                            "name": name, "source": str(item["source"]), "tool": item["tool"],
                            "ok": False, "code": "link-swap-failed", "error": f"link swap failed: {exc}",
                            "changed_since_preview": False,
                        })
                        continue
                    undo.append({"path": str(src), "backup": backup, "kind": "restore-tool-entry"})
                else:
                    undo.append({"path": str(dest), "backup": None, "kind": "remove-canonical-copy"})

                skill_id = f"{category}/{name}"
                result = {
                    "name": name, "source": str(item["source"]), "tool": item["tool"],
                    "ok": True, "code": "adopted", "skill": skill_id, "path": str(dest),
                    "backup": backup, "changed_since_preview": False,
                }
                results.append(result)
                tree_names.add(name)
                skills[skill_id] = {"name": name, "category": category, "dir": dest}

            adopted = sum(1 for row in results if row["ok"])
            refused_codes = {
                "broken-link", "foreign-link", "unmanaged-dir", "managed", "managed-mismatch",
                "missing-entry", "changed-since-preview",
            }
            refused_count = sum(1 for row in results if row.get("code") in refused_codes)
            failed = len(results) - adopted - refused_count
            receipt = {
                "receipt_id": receipt_id, "items": results, "adopted": adopted,
                "failed": failed, "refused": refused_count, "undo": undo,
            }
            # Log only stable identifiers and dispositions. Owner-selected
            # filesystem paths and unrecognized request fields stay out.
            self._log(
                action="import-plan-apply", receipt_id=receipt_id,
                items=[{"name": row["name"], "tool": row["tool"], "ok": row["ok"], "code": row["code"]} for row in results],
                adopted=adopted, failed=failed, refused=refused_count,
            )
            if adopted:
                self.invalidate()
            return {
                "ok": True, "results": results, "receipt": receipt,
                "adopted": adopted, "failed": failed, "refused": refused_count,
            }

    def import_apply(self, tool_id: object, names: object, category: str = "imported") -> dict:
        """Adopt unmanaged skills: copy into the skills tree, then replace the
        tool's real dir with a symlink — the original is PRESERVED as a
        timestamped `<name>.hermes-switchboard-backup-<ts>` sibling (never deleted)."""
        tool_id = self._validate_tool(tool_id)
        if tool_id == "hermes":
            raise SkillsToggleError("hermes has no importable dir", "no-dir")
        if not isinstance(names, list) or not names:
            raise SkillsToggleError("'names' must be a non-empty list", "invalid-body")
        category = self._validate_import_category(category)
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
            backup = tool_dir / f"{name}.hermes-switchboard-backup-{stamp}"
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
            tool_dir = self._inventory_dir(tool_id)
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
            if not isinstance(name, str) or any(c in name for c in ("/", "\\", "\0")) or name in (".", "..") or not name.strip():
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
            backup = tool_dir / f"{name}.hermes-switchboard-backup-{stamp}"
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

    # -- conflict resolution --------------------------------------------------

    def _conflict_context(self, tool_id: object, name: object, require_in_tree: bool):
        if not isinstance(name, str) or any(c in name for c in ("/", "\\", "\0")) or name in (".", "..") or not name.strip():
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
            hermes_backup = hermes_dir.parent / f".hermes-switchboard-backup-{name}-{stamp}"
            tool_backup = tool_dir / f"{name}.hermes-switchboard-backup-{stamp}"
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
            tool_backup = tool_dir / f"{name}.hermes-switchboard-backup-{stamp}"
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

    @staticmethod
    def _entry_name(name: object) -> str:
        if (not isinstance(name, str) or not name.strip() or name in (".", "..")
                or any(char in name for char in ("/", "\\", "\0"))):
            raise SkillsToggleError("Expected one skill directory name", "invalid-name")
        return name

    @staticmethod
    def _checked_backup(backup: Path, parent: Path, pattern: str) -> Path:
        if not backup.is_dir():
            raise SkillsToggleError("The preserved backup directory is missing", "backup-missing")
        if (not backup.is_absolute() or backup.is_symlink()
                or not same_path(backup.parent, parent)
                or not is_inside(backup, parent)
                or not re.fullmatch(pattern, backup.name)):
            raise SkillsToggleError("Backup is not a recognized preserved entry in this target", "invalid-backup")
        return backup

    def _restore_tool_entry(self, tool_dir: Path, name: str, backup: Path) -> None:
        """Restore only a named sibling backup; retain the link on failure."""
        name = self._entry_name(name)
        entry = tool_dir / name
        if not entry.is_symlink():
            raise SkillsToggleError("The current entry is not a managed link", "not-managed")
        self._checked_backup(backup, tool_dir,
            re.escape(name) + r"\.(?:hermes-switchboard|skills-toggle)-backup[-.]\d{8}-\d{6}(?:-\d+)?")
        resolved = Path(os.readlink(entry))
        base = resolved if resolved.is_absolute() else (entry.parent / resolved)
        if not is_inside(base, self.skills_root_resolved):
            raise SkillsToggleError("The current link is outside the Hermes library", "not-managed")
        parked = entry.with_name(".switchboard-restore-" + uuid.uuid4().hex)
        try:
            os.rename(entry, parked)
            try:
                if os.path.lexists(entry):
                    raise OSError("target changed during restore")
                os.rename(backup, entry)
            except OSError:
                if not os.path.lexists(entry):
                    os.rename(parked, entry)
                raise
            parked.unlink()
        except OSError as exc:
            raise SkillsToggleError("Restore failed; preserved data was not deleted", "restore-failed") from exc

    def revert_push(self, tool_id: object, name: object, tool_backup: object) -> dict:
        """Undo drift_push: restore the backed-up tool copy and drop the
        canonical symlink. The hermes tree is untouched by push, so nothing
        else changes."""
        with self._lock:
            tool_id = self._validate_tool(tool_id)
            if tool_id == "hermes":
                raise SkillsToggleError("hermes has no tool entry", "no-dir")
            name = self._entry_name(name)
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
            name = self._entry_name(name)
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
            self._checked_backup(hb, hermes_dir.parent,
                r"\.(?:hermes-switchboard|skills-toggle)-backup-" + re.escape(name) + r"-\d{8}-\d{6}(?:-\d+)?")
            if not same_path(tool_dir / name, hermes_dir):
                raise SkillsToggleError("The link no longer points to the pulled skill", "changed-since-preview")
            self._restore_tool_entry(tool_dir, name, Path(tool_backup))
            # canonical: move the pulled copy aside (dotted), restore original
            pulled_aside = hermes_dir.parent / f".hermes-switchboard-reverted-{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
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
            name = self._entry_name(name)
            skill = self._validate_skill(skill)
            if not isinstance(tool_backup, str):
                raise SkillsToggleError("missing tool_backup", "invalid-body")
            tool_dir = self.tool_dir(tool_id)
            if tool_dir is None:
                raise SkillsToggleError("tool dir missing", "absent-dir")
            adopted = self._scan_skills()[skill]["dir"]
            if not same_path(tool_dir / name, adopted):
                raise SkillsToggleError("The link no longer points to the adopted skill", "changed-since-preview")
            self._restore_tool_entry(tool_dir, name, Path(tool_backup))
            if adopted.is_dir():
                aside = adopted.parent / f".hermes-switchboard-reverted-{adopted.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
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
                "generated_by": f"hermes-switchboard {PLUGIN_VERSION}",
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
                try:
                    self._validate_tool(tool_id)
                    self._validate_skill(skill_id)
                    self._validate_client_skill(skills[skill_id], tool_id)
                    tool_dir = self.tool_dir(tool_id)
                except SkillsToggleError as exc:
                    refused.append({"row": row, "error": str(exc), "code": exc.code})
                    continue
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
        re.compile(r"^config\.yaml\.bak\.hermes-switchboard\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^hermes-switchboard\.json\.bak\.hermes-switchboard\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^(.+)\.hermes-switchboard-backup\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^(.+)\.hermes-switchboard-backup-(\d{8}-\d{6})$"),
        re.compile(r"^\.hermes-switchboard-(?:backup|reverted)-(.+)-(\d{8}-\d{6})$"),
        re.compile(r"^config\.yaml\.bak\.skills-toggle\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^skills-toggle\.json\.bak\.skills-toggle\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^(.+)\.skills-toggle-backup\.(\d{8}-\d{6})(?:-\d+)?$"),
        re.compile(r"^(.+)\.skills-toggle-backup-(\d{8}-\d{6})$"),
        re.compile(r"^\.skills-toggle-(?:backup|reverted)-(.+)-(\d{8}-\d{6})$"),
    )

    def list_backups(self) -> dict:
        rows = []
        home = self.home
        for ln in sorted(home.glob("config.yaml.bak.hermes-switchboard.*")):
            rows.append({"path": str(ln), "kind": "config", "name": ln.name})
        for ln in sorted(home.glob("config.yaml.bak.skills-toggle.*")):
            rows.append({"path": str(ln), "kind": "config", "name": ln.name})
        for ln in sorted(home.glob("hermes-switchboard.json.bak.hermes-switchboard.*")):
            rows.append({"path": str(ln), "kind": "tools-json", "name": ln.name})
        for ln in sorted(home.glob("skills-toggle.json.bak.skills-toggle.*")):
            rows.append({"path": str(ln), "kind": "tools-json", "name": ln.name})
        for tool_id in self.tools:
            if tool_id == "hermes":
                continue
            tool_dir = self._inventory_dir(tool_id)
            if tool_dir is None or not tool_dir.is_dir():
                continue
            try:
                children = sorted(tool_dir.iterdir())
            except OSError:
                continue
            for ln in children:
                if (
                    ln.name.endswith(".hermes-switchboard-backup")
                    or ".hermes-switchboard-backup-" in ln.name
                    or ln.name.endswith(".skills-toggle-backup")
                    or ".skills-toggle-backup-" in ln.name
                ):
                    if ln.is_dir():
                        rows.append({"path": str(ln), "kind": "tool-link", "tool": tool_id, "name": ln.name})
        if self.skills_root.is_dir():
            for cat_dir in sorted(self.skills_root.iterdir()):
                if not cat_dir.is_dir() or cat_dir.name.startswith(".") or not is_inside(cat_dir, self.skills_root_resolved):
                    continue
                try:
                    children = sorted(cat_dir.iterdir())
                except OSError:
                    continue
                for ln in children:
                    if ln.name.startswith((
                        ".hermes-switchboard-backup-",
                        ".hermes-switchboard-reverted-",
                        ".skills-toggle-backup-",
                        ".skills-toggle-reverted-",
                    )):
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
                raise SkillsToggleError("not a known hermes-switchboard backup", "unknown-backup")
            src = Path(path)
            kind = row["kind"]
            if kind in ("config", "tools-json"):
                if kind == "config":
                    target = self.config_path
                elif row["name"].startswith("skills-toggle.json."):
                    target = legacy_user_config_path(self.home)
                else:
                    target = user_config_path(self.home)
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
                name = re.split(r"\.(?:hermes-switchboard|skills-toggle)-backup", row["name"], maxsplit=1)[0]
                self._restore_tool_entry(tool_dir, name, src)
                self._log(action="restore", kind=kind, path=path)
                self.invalidate()
                return {"ok": True, "kind": kind, "action": "restored", "name": name}
            if kind == "hermes-copy":
                # Accept current and pre-rename backup names.
                m = re.match(r"^\.(?:hermes-switchboard|skills-toggle)-(?:backup|reverted)-(.+)-\d{8}-\d{6}$", row["name"])
                if not m:
                    raise SkillsToggleError("cannot parse backup name", "restore-failed")
                name = m.group(1)
                canonical = self.skills_root / row["category"] / name
                if not canonical.is_dir():
                    raise SkillsToggleError(f"canonical {canonical} is missing", "restore-failed")
                aside = canonical.parent / f".hermes-switchboard-replaced-{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
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
        """Add or override a tool target dir in <hermes_home>/hermes-switchboard.json
        (timestamped backup first). Hermes itself is config-backed and locked."""
        with self._lock:
            if (
                not isinstance(tool_id, str)
                or not re.fullmatch(r"[a-z0-9-]+", tool_id)
                or (len(tool_id) > 32 and tool_id not in self.tools)
                or tool_id == "hermes"
            ):
                raise SkillsToggleError(f"invalid tool id {tool_id!r}", "invalid-tool-id")
            if not isinstance(label, str) or not label.strip() or len(label) > 40:
                raise SkillsToggleError("'label' must be a 1-40 char string", "invalid-label")
            if not isinstance(dir_str, str) or not dir_str.strip() or "\0" in dir_str:
                raise SkillsToggleError("'dir' must be a non-empty path string", "invalid-dir")
            if not Path(os.path.expanduser(dir_str)).is_absolute() and not dir_str.startswith("$"):
                raise SkillsToggleError("choose an absolute or home-relative Custom path", "invalid-dir")
            try:
                target = expand_path(dir_str)
                if is_inside(target, self.skills_root_resolved) or is_inside(self.skills_root_resolved, target):
                    raise SkillsToggleError("target overlaps the canonical Hermes library", "scope-escape")
            except ValueError:
                raise SkillsToggleError(f"'dir' expands to an empty path: {dir_str!r}", "invalid-dir")
            cfg_path = user_config_path(self.home)
            source_path = cfg_path
            if not source_path.exists() and legacy_user_config_path(self.home).is_file():
                source_path = legacy_user_config_path(self.home)
            data, _ = _read_json_mapping(source_path, "tools")
            backup = self._backup(source_path)
            tools = data.setdefault("tools", {})
            tools[tool_id] = {"label": label.strip(), "dir": dir_str.strip(), "scope": "custom"}
            data["schema_version"] = CONFIG_SCHEMA_VERSION
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(cfg_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            self._log(action="config-tools", tool=tool_id, dir=str(dir_str), backup=backup)
            self.invalidate()
            reset_core()  # tool map is loaded at core build time — rebuild singleton
            return {"ok": True, "tool": tool_id, "label": label.strip(), "dir": str(expand_path(dir_str)), "backup": backup}

    def clients(self, project_root: object = None) -> dict:
        root = explicit_project_root(project_root) if project_root else None
        rows = []
        for source in CLIENT_CATALOG:
            row = {key: copy.deepcopy(value) for key, value in source.items() if key != "legacy_default"}
            candidates = []
            for scope in ("global", "project"):
                if scope == "project" and root is None:
                    continue
                for index, _ in enumerate(source.get(scope, [])):
                    try:
                        spec = catalog_target(source["id"], scope, root, index)
                        directory = validate_scoped_target(spec)
                        marker = source.get("detect_marker")
                        detected = directory.is_dir() and (not marker or expand_path(marker).is_dir())
                        shared_with = []
                        configured_id = None
                        for tid, tool in self.tools.items():
                            try:
                                target = self.tool_dir(tid)
                            except (SkillsToggleError, OSError, ValueError):
                                continue
                            if target and same_path(target, directory):
                                shared_with.append(tool.get("label", tid))
                                if tool.get("client_id") == source["id"] and tool.get("configured"):
                                    configured_id = tid
                        candidates.append({"scope": scope, "index": index, "dir": str(directory),
                                           "project_root": spec.get("project_root"), "detected": detected,
                                           "configured_id": configured_id, "shared_with": shared_with,
                                           "shared": source.get("shared", False) or ".agents" in directory.parts})
                    except (SkillsToggleError, OSError, ValueError):
                        candidates.append({"scope": scope, "index": index, "error": "Path is unavailable or outside this scope. Use an explicit Custom path instead."})
            row["candidates"] = candidates
            rows.append(row)
        return {"ok": True, "catalog_version": CATALOG_VERSION, "clients": rows,
                "project_root": str(root) if root else None}

    def enable_client(self, client_id: object, scope: object, project_root: object = None,
                      candidate: object = 0, expected_dir: object = None) -> dict:
        with self._lock:
            if isinstance(candidate, bool) or not isinstance(candidate, int):
                raise SkillsToggleError("invalid candidate", "invalid-client")
            spec = catalog_target(client_id, scope, project_root, candidate)
            target = validate_scoped_target(spec)
            if not isinstance(expected_dir, str) or not same_path(Path(expected_dir), target):
                raise SkillsToggleError("client path changed or was not reviewed; reload the library", "changed-since-preview")
            if is_inside(target, self.skills_root_resolved) or is_inside(self.skills_root_resolved, target):
                raise SkillsToggleError("target overlaps the canonical Hermes library", "scope-escape")
            cfg = user_config_path(self.home)
            old = cfg if cfg.exists() or cfg.is_symlink() else legacy_user_config_path(self.home)
            data, _ = _read_json_mapping(old, "tools")
            saved = data.setdefault("tools", {})
            suffix = hashlib.sha256((str(scope) + "\0" + _canonical(str(target))).encode()).hexdigest()[:10]
            tid = str(client_id) if scope == "global" else str(client_id) + "-p-" + suffix
            if tid in self.tools:
                current = self.tools[tid]
                try:
                    current_dir = self.tool_dir(tid)
                except (SkillsToggleError, OSError, ValueError):
                    current_dir = None
                if current_dir is None or not same_path(current_dir, target):
                    tid = str(client_id) + ("-p-" if scope == "project" else "-g-") + suffix
            # Do not overwrite a manually configured map sharing a generated id.
            if tid in saved:
                previous = saved[tid]
                previous_dir = previous if isinstance(previous, str) else previous.get("dir", "")
                if not previous_dir or not same_path(expand_path(previous_dir), target):
                    raise SkillsToggleError("client id already maps to another path; keep the existing mapping", "client-conflict")
            backup = self._backup(old)
            saved[tid] = spec
            data["schema_version"] = CONFIG_SCHEMA_VERSION
            _atomic_write_text(cfg, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            self.tools[tid] = dict(spec, configured=True, optional=False)
            self.invalidate()
            reset_core()
            # Activation saves a mapping only. The first reviewed skill operation
            # may create the target directory; browsing never creates directories.
            return {"ok": True, "tool": tid, "dir": str(target), "scope": scope,
                    "project_root": spec.get("project_root"), "created": False, "backup": backup}

    def health(self) -> dict:
        return {
            "ok": True,
            "plugin": PLUGIN_ID,
            "version": PLUGIN_VERSION,
            "hermes_home": str(self.home),
            "skills_root": str(self.skills_root),
            "skills_root_exists": self.skills_root.is_dir(),
            "tools": {tid: (str(self._inventory_dir(tid)) if self._inventory_dir(tid) else None) for tid in self.tools},
            "capabilities": {"client_catalog": CATALOG_VERSION, "receipt_undo": True},
        }


# ---------------------------------------------------------------------------
# Tools config loading (defaults + <hermes_home>/hermes-switchboard.json override)
# ---------------------------------------------------------------------------


def user_config_path(home: Path) -> Path:
    return home / "hermes-switchboard.json"


def legacy_user_config_path(home: Path) -> Path:
    """Return the pre-rename path so existing custom tool maps still load."""
    return home / f"{LEGACY_PLUGIN_ID}.json"


def catalog_client(client_id: object) -> dict:
    for row in CLIENT_CATALOG:
        if row["id"] == client_id:
            return row
    raise SkillsToggleError("unknown client", "invalid-client")


def explicit_project_root(value: object) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip() or "\0" in str(value):
        raise SkillsToggleError("select an existing project folder", "invalid-project")
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise SkillsToggleError("select an existing absolute project folder", "invalid-project")
    return path.resolve()


def catalog_target(client_id: object, scope: object, project_root: object = None, candidate: int = 0) -> dict:
    row = catalog_client(client_id)
    if not row["skills"] or row["verification"] != "documented" or not row["may_create"]:
        raise SkillsToggleError("client has no verified skill target; use Custom only after verifying its format", "client-unverified")
    if sys.platform not in row["platforms"] or scope not in ("global", "project"):
        raise SkillsToggleError("client or scope is unavailable on this platform", "unsupported-scope")
    paths = row.get(scope, [])
    if candidate < 0 or candidate >= len(paths):
        raise SkillsToggleError("client does not support this scope", "unsupported-scope")
    root = explicit_project_root(project_root) if scope == "project" else Path.home().resolve()
    directory = root / paths[candidate] if scope == "project" else expand_path(paths[candidate])
    if not is_inside(directory, root) or same_path(directory, root):
        raise SkillsToggleError("client path escapes its selected scope", "scope-escape")
    label = row["label"] if scope == "global" else (row["label"] + " · " + root.name)[:40]
    spec = {"label": label, "client_id": client_id, "dir": str(directory.absolute()),
            "scope": scope, "scope_root": str(root), "candidate": candidate,
            "resolved_dir": _canonical(str(directory)), "verification": "documented", "notes": row.get("notes", "")}
    if scope == "project":
        spec["project_root"] = str(root)
    return spec


def validate_scoped_target(spec: dict) -> Path:
    """Recheck the selected scope before every read or mutation, not only setup."""
    scope = spec.get("scope")
    row = catalog_client(spec.get("client_id"))
    if scope not in ("global", "project") or not row.get("skills") or row.get("verification") != "documented":
        raise SkillsToggleError("invalid scoped target", "scope-escape")
    raw_root = spec.get("project_root") if scope == "project" else spec.get("scope_root")
    # Malformed saved metadata must reach the recoverable scope-error UI,
    # not leak a Path(None)/Path(list) TypeError through the HTTP boundary.
    for value in (raw_root, spec.get("scope_root"), spec.get("dir")):
        if not isinstance(value, str) or not value or "\0" in value or not Path(value).is_absolute():
            raise SkillsToggleError("scope metadata needs an absolute path", "scope-escape")
    root = Path(raw_root)
    if not root.is_dir() or not same_path(root, Path(spec["scope_root"])):
        raise SkillsToggleError("selected scope is unavailable", "scope-escape")
    if scope == "global" and not same_path(root, Path.home()):
        raise SkillsToggleError("global target does not belong to the current user home", "scope-escape")
    candidate = spec.get("candidate", 0)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0 or candidate >= len(row.get(scope, [])):
        raise SkillsToggleError("invalid scope candidate", "scope-escape")
    expected = root / row[scope][candidate] if scope == "project" else expand_path(row[scope][candidate])
    directory = Path(spec.get("dir", ""))
    if (not directory.is_absolute() or not is_inside(directory, root) or same_path(directory, root)
            or not same_path(directory, expected) or _canonical(str(directory)) != spec.get("resolved_dir")):
        raise SkillsToggleError("target moved or escapes its selected scope; review the client path", "scope-escape")
    return directory


def load_tools_config(home: Path) -> dict:
    tools = {"hermes": {"label": "Hermes", "special": "config", "scope": "global", "configured": True}}
    for row in CLIENT_CATALOG:
        if row["id"] == "hermes":
            continue
        tid = row["id"]
        if row["verification"] == "documented" and row.get("global"):
            try:
                spec = catalog_target(tid, "global")
                # A shared folder alone is not detection evidence for every reader.
                if row.get("detect_marker") and not expand_path(row["detect_marker"]).is_dir():
                    continue
                tools[tid] = dict(spec, optional=True, configured=False)
            except (SkillsToggleError, ValueError, OSError):
                continue
        legacy = row.get("legacy_default", {})
        if not legacy.get("dir"):
            continue
        old_dir = expand_path(legacy["dir"])
        if not old_dir.is_dir() and legacy.get("fallback_dir"):
            old_dir = expand_path(legacy["fallback_dir"])
        current_dir = tools.get(tid, {}).get("dir")
        if old_dir.is_dir() and (not current_dir or not same_path(old_dir, Path(current_dir))):
            tools[tid] = dict(legacy, dir=str(old_dir), optional=True, configured=False,
                              scope="custom", verification="legacy", notes="Preserved legacy location; review client compatibility.",
                              read_only=row["verification"] != "documented")
    cfg_path = user_config_path(home)
    if not cfg_path.exists() and not cfg_path.is_symlink():
        cfg_path = legacy_user_config_path(home)
    user_cfg, _ = _read_json_mapping(cfg_path, "tools")
    if type(user_cfg.get("schema_version", 1)) is not int or user_cfg.get("schema_version", 1) not in (1, CONFIG_SCHEMA_VERSION):
        raise SkillsToggleError("configuration version is newer than this plugin; update Switchboard", "config-version")
    for tool_id, spec in user_cfg.get("tools", {}).items():
        if tool_id == "hermes":
            # Preserve the old display-label customization, never redirect storage.
            if isinstance(spec, dict) and isinstance(spec.get("label"), str) and spec["label"].strip():
                tools["hermes"]["label"] = spec["label"][:40]
            continue
        # The original file format placed no length limit on existing IDs.
        # Keep those mappings addressable; only new Custom IDs are capped.
        if not isinstance(tool_id, str) or not re.fullmatch(r"[a-z0-9-]+", tool_id):
            raise SkillsToggleError("invalid client id in configuration", "config-invalid")
        legacy_read_only = False
        if (isinstance(spec, dict) and "dir" not in spec and "scope" not in spec
                and tool_id in DEFAULT_TOOLS):
            # A legacy label-only override inherited the old default path.
            # In particular, never redirect Codex from .codex to .agents here.
            inherited = copy.deepcopy(DEFAULT_TOOLS[tool_id])
            if inherited.get("fallback_dir") and not expand_path(inherited["dir"]).is_dir():
                inherited["dir"] = inherited["fallback_dir"]
            inherited.update(spec)
            spec = dict(inherited, scope="custom", verification="legacy",
                        notes="Preserved legacy location; review client compatibility.")
            # Changing a label is not consent to write to an unverified client.
            legacy_read_only = catalog_client(tool_id)["verification"] != "documented"
        if isinstance(spec, str):
            spec = {"dir": spec}
        if not isinstance(spec, dict) or not isinstance(spec.get("dir"), str) or not spec["dir"].strip():
            raise SkillsToggleError("invalid client path in configuration", "config-invalid")
        if spec.get("scope", "custom") not in ("custom", "global", "project"):
            raise SkillsToggleError("unknown client scope in configuration", "config-invalid")
        tools[tool_id] = dict(spec, label=str(spec.get("label") or DEFAULT_TOOLS.get(tool_id, {}).get("label") or tool_id.title()),
                              configured=True, optional=False, scope=spec.get("scope", "custom"), read_only=legacy_read_only)
    return tools


# ---------------------------------------------------------------------------
# v2.2 — MCP switchboard core (Q1a: Hermes catalog + Claude Desktop writer)
# Hermes config.yaml `mcp_servers` is the source of truth; entries carry an
# `enabled:` flag (Hermes' own on/off). Claude Desktop mirrors entries into
# its claude_desktop_config.json `mcpServers` map (presence = enabled).
# ---------------------------------------------------------------------------

CODEX_CONFIG_CANDIDATES = ["~/.codex/config.toml"]


_TOML_SUPPORT = None
_TOML_LOCK = threading.RLock()


def _toml_support():
    """Load packaged helpers by path, independent of the host's sys.path."""
    global _TOML_SUPPORT
    with _TOML_LOCK:
        if _TOML_SUPPORT is None:
            import importlib.util
            path = Path(__file__).with_name("toml_document.py")
            spec = importlib.util.spec_from_file_location("_switchboard_toml_document", path)
            if spec is None or spec.loader is None:
                raise SkillsToggleError("TOML support is missing; reinstall Switchboard", "config-unreadable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _TOML_SUPPORT = module
        return _TOML_SUPPORT


def parse_codex_mcp(text: str) -> dict:
    try:
        servers = _toml_support().load(text).get("mcp_servers", {})
    except ValueError as exc:
        raise SkillsToggleError(str(exc), "config-invalid") from exc
    return {name: {"enabled": entry.get("enabled", True),
                   "definition": {k: v for k, v in entry.items() if k != "enabled"}}
            for name, entry in servers.items()}


def codex_server_block(name: str, projection: dict) -> str:
    try:
        return _toml_support().block(name, _codex_projection(projection))
    except ValueError as exc:
        raise SkillsToggleError(str(exc), "config-edit") from exc


def _remove_codex_block(text: str, name: str) -> str:
    try:
        return _toml_support().replace_server(text, name, None)
    except ValueError as exc:
        raise SkillsToggleError(str(exc), "config-edit") from exc


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
    redacted = {}
    for key in _MCP_UNIVERSAL_KEYS:
        if key not in definition:
            continue
        value = definition[key]
        if key in ("env", "headers") and isinstance(value, dict):
            redacted[key] = sorted(str(name) for name in value)
        elif key == "args" and isinstance(value, list):
            redacted[key] = ["[REDACTED]" for _ in value]
        else:
            # Executable strings may also include inline credentials. The UI
            # needs only field presence, not raw command lines or endpoints.
            redacted[key] = "[REDACTED]"
    return redacted


def _claude_projection(definition: dict) -> dict:
    """Legacy helper name for the shared Hermes projection."""
    return {k: v for k, v in definition.items() if k in _MCP_UNIVERSAL_KEYS}


def _codex_projection(definition: dict) -> dict:
    projection = _claude_projection(definition)
    if "headers" in projection:
        projection["http_headers"] = projection.pop("headers")
    return projection


def _shared_mcp_definition(definition: dict, writer: str) -> dict:
    keys = ("command", "args", "env", "url", "http_headers", "headers") if writer == "codex" else _MCP_UNIVERSAL_KEYS
    return {k: v for k, v in definition.items() if k in keys}


def _check_mcp_projection(projection: dict, writer: str) -> None:
    command, url = projection.get("command"), projection.get("url")
    if bool(command) == bool(url) or (writer == "claude" and url):
        raise SkillsToggleError("this transport is unsupported by the selected writer; use the client's native connector setup", "unsupported-transport")
    for field in ("command", "url"):
        if field in projection and not isinstance(projection[field], str):
            raise SkillsToggleError("MCP commands and URLs must be strings", "invalid-definition")
    args = projection.get("args", [])
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise SkillsToggleError("MCP arguments must be a list of strings", "invalid-definition")
    for field in ("env", "headers", "http_headers"):
        mapping = projection.get(field, {})
        if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
            raise SkillsToggleError("MCP environment and header values must be strings", "invalid-definition")
    if command and ("headers" in projection or "http_headers" in projection):
        raise SkillsToggleError("HTTP headers require a URL transport", "unsupported-transport")
    if url and ("args" in projection or "env" in projection):
        raise SkillsToggleError("command arguments and environment values require a local command", "unsupported-transport")


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
        return expand_path(CODEX_CONFIG_CANDIDATES[0])

    def _default_claude_config(self) -> Path | None:
        for candidate in CLAUDE_DESKTOP_CONFIG_CANDIDATES:
            try:
                expanded = expand_path(candidate)
            except ValueError:
                continue
            if expanded.is_file():
                return expanded
        index = 2 if sys.platform == "win32" else (0 if sys.platform == "darwin" else 1)
        return expand_path(CLAUDE_DESKTOP_CONFIG_CANDIDATES[index])

    def _log(self, **rec: object) -> None:
        if not self.log_path:
            return
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        rec = _redact_log_record(rec)
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
        backup = path.with_name(f"{path.name}.bak.hermes-switchboard.{stamp}")
        n = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak.hermes-switchboard.{stamp}-{n}")
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
        if self.claude_config is None:
            return {}, ""
        if self.claude_config.is_symlink():
            raise SkillsToggleError("Claude config is a symlink; manage it in the client", "config-protected")
        return _read_json_mapping(self.claude_config, "mcpServers")

    def _read_codex(self) -> tuple[dict, str]:
        if self.codex_config is None:
            return {}, ""
        if self.codex_config.is_symlink():
            raise SkillsToggleError("Codex config is a symlink; use the client to manage this configuration", "config-protected")
        try:
            with self.codex_config.open("r", encoding="utf-8", newline="") as stream:
                text = stream.read()
        except FileNotFoundError:
            return {}, ""
        except (OSError, UnicodeError) as exc:
            raise SkillsToggleError("Codex config cannot be read; no changes made", "config-unreadable") from exc
        return parse_codex_mcp(text), text

    def _write_codex(self, name: object, create: bool, force: bool = False) -> dict:
        if not isinstance(name, str) or not name.strip() or "\0" in name:
            raise SkillsToggleError("invalid server name", "invalid-name")
        if self.codex_config is None:
            raise SkillsToggleError("no Codex config path resolved", "no-writer")
        cat = self.catalog()
        entry = next((c for c in cat["catalog"] if c["name"] == name), None)
        codex, raw = self._read_codex()
        if not create and name not in codex:
            return {"ok": True, "name": name, "action": "noop", "state": "missing"}
        if entry is None:
            raise SkillsToggleError("server is not in the Hermes catalog; no changes made", "unknown-server")
        projection = _codex_projection(entry["projection"])
        if create:
            _check_mcp_projection(projection, "codex")
        current = codex.get(name, {}).get("definition", {})
        if name in codex and _shared_mcp_definition(current, "codex") != projection and not force:
            raise SkillsToggleError("Codex's server definition differs from Hermes; confirm overwrite", "drifted")
        definition = None
        if create:
            # Preserve client-only flags, timeouts, auth references, and tool policy.
            definition = {k: v for k, v in current.items() if k not in _MCP_UNIVERSAL_KEYS and k != "http_headers"}
            definition.update(projection)
            if name in codex and not codex[name]["enabled"]:
                definition["enabled"] = False
        try:
            new_text = _toml_support().replace_server(raw, name, definition)
        except ValueError as exc:
            raise SkillsToggleError(str(exc), "config-edit") from exc
        # External edits during planning are refused, not overwritten.
        if self._read_codex()[1] != raw:
            raise SkillsToggleError("Codex config changed during review; refresh and retry", "changed-since-preview")
        backup = self._backup(self.codex_config)
        _atomic_write_text(self.codex_config, new_text)
        action = ("updated" if name in codex else "created") if create else "removed"
        state = ("disabled" if definition.get("enabled") is False else "enabled") if create else "missing"
        self._log(action=f"mcp-codex-{action}", server=name, backup=backup)
        return {"ok": True, "name": name, "writer": "codex", "action": action, "state": state, "backup": backup}

    def sync_to_codex(self, name: object, force: object = False) -> dict:
        with self._lock:
            if not isinstance(force, bool):
                raise SkillsToggleError("'force' must be a boolean", "invalid-body")
            return self._write_codex(name, create=True, force=force)

    def remove_from_codex(self, name: object, force: object = False) -> dict:
        with self._lock:
            if not isinstance(force, bool):
                force = False
            return self._write_codex(name, create=False, force=force)

    def mcp_state(self) -> dict:
        cat = self.catalog()
        writers = {
            "claude": {"label": "Claude Desktop", "path": str(self.claude_config) if self.claude_config else None,
                       "present": bool(self.claude_config and self.claude_config.is_file()), "available": True},
            "codex": {"label": "Codex", "path": str(self.codex_config) if self.codex_config else None,
                      "present": bool(self.codex_config and self.codex_config.is_file()), "available": True},
        }
        entries = {"claude": {}, "codex": {}}
        for writer, read in (("claude", self._read_claude), ("codex", self._read_codex)):
            try:
                doc, _ = read()
                entries[writer] = doc.get("mcpServers", {}) if writer == "claude" else doc
            except SkillsToggleError as exc:
                writers[writer].update(available=False, error=str(exc), code=exc.code)
        names = {c["name"] for c in cat["catalog"]}
        foreign = []
        for writer, servers in entries.items():
            for name, definition in servers.items():
                if name not in names:
                    definition = definition["definition"] if writer == "codex" else definition
                    foreign.append({"name": name, "writer": writer, "keys": sorted(definition) if isinstance(definition, dict) else []})
        rows = []
        for c in cat["catalog"]:
            states = {}
            for writer in writers:
                projection = _codex_projection(c["projection"]) if writer == "codex" else c["projection"]
                if not writers[writer]["available"]:
                    states[writer] = "unavailable"
                    continue
                try:
                    _check_mcp_projection(projection, writer)
                except SkillsToggleError:
                    states[writer] = "unsupported"
                    continue
                current = entries[writer].get(c["name"])
                if current is None:
                    states[writer] = "missing"
                elif not isinstance(current, dict):
                    states[writer] = "drifted"
                else:
                    definition = current["definition"] if writer == "codex" else current
                    same = _shared_mcp_definition(definition, writer) == projection
                    states[writer] = ("disabled" if writer == "codex" and not current["enabled"] else "enabled") if same else "drifted"
            rows.append({"name": c["name"], "enabled": c["enabled"],
                         "definition": _redact_env(c["definition"]), "writers": states})
        return {"ok": True, "rows": rows, "foreign": foreign,
                "counts": {"catalog": len(rows), "foreign": len(foreign)}, "writers": writers,
                "partial_failure": any(not writer["available"] for writer in writers.values())}

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
            _atomic_write_text(self.config_path, new_text)
            self._log(action="mcp-toggle", server=name, enabled=enabled, backup=backup)
            return {"ok": True, "name": name, "enabled": enabled, "action": "config-updated", "backup": backup}

    def sync_to_claude(self, name: object, force: object = False) -> dict:
        with self._lock:
            if not isinstance(force, bool):
                raise SkillsToggleError("'force' must be a boolean", "invalid-body")
            return self._write_claude(name, create=True, force=force)

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
        if create:
            _check_mcp_projection(entry["projection"], "claude")
        current = servers.get(name, {})
        shared = _shared_mcp_definition(current, "claude") if isinstance(current, dict) else None
        if name in servers and entry is not None and shared != entry["projection"] and not force:
            raise SkillsToggleError(
                f"Claude's copy of {name!r} differs from the catalog — pass force to overwrite", "drifted"
            )
        if self.claude_config is None:
            raise SkillsToggleError("no Claude Desktop config path resolved", "no-writer")
        if self._read_claude()[1] != raw:
            raise SkillsToggleError("Claude config changed during review; refresh and retry", "changed-since-preview")
        backup = self._backup(self.claude_config)
        if raw == "" or not claude:
            doc = {"mcpServers": servers}
        else:
            doc = claude if isinstance(claude, dict) else {"mcpServers": servers}
        if create:
            native = {k: v for k, v in current.items() if k not in _MCP_UNIVERSAL_KEYS} if isinstance(current, dict) else {}
            doc["mcpServers"] = {**servers, name: {**native, **entry["projection"]}}
            action = "updated" if name in servers else "created"
        else:
            remaining = {k: v for k, v in servers.items() if k != name}
            doc["mcpServers"] = remaining
            action = "removed"
        self.claude_config.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(self.claude_config, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
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
        return {"ok": True, "catalog": self.catalog()["count"], "writer": "claude", "writers": ["claude", "codex"]}


# ---------------------------------------------------------------------------
# Singleton for the route layer
# ---------------------------------------------------------------------------

_CORE: SkillsToggleCore | None = None
_CORE_SIG: tuple | None = None
_CORE_FROZEN = False


def _core_signature() -> tuple:
    home = hermes_home()
    result = [str(home)]
    for path in (user_config_path(home), legacy_user_config_path(home)):
        try:
            st = path.stat()
            result.append((str(path), st.st_mtime_ns, st.st_size, st.st_ino))
        except OSError:
            result.append((str(path), None))
    return tuple(result)


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
    home = hermes_home()
    if _MCP_CORE is None or not same_path(_MCP_CORE.home, home):
        _MCP_CORE = McpCore(home, log_path=home / "data" / PLUGIN_ID / "mutations.log")
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
# FastAPI route layer (mounted at /api/plugins/hermes-switchboard/)
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

    def _core_call(method, *args, **kwargs) -> dict:
        return _call(lambda: getattr(get_core(), method)(*args, **kwargs))

    @router.get("/clients")
    async def clients(project_root: str = "") -> dict:
        return _core_call("clients", project_root or None)

    @router.post("/clients/enable")
    async def enable_client(body: dict) -> dict:
        return _core_call("enable_client", body.get("client_id"), body.get("scope"),
                          body.get("project_root"), body.get("candidate", 0), body.get("expected_dir"))

    @router.get("/health")
    async def health() -> dict:
        return _core_call("health")

    @router.get("/state")
    async def state() -> dict:
        return _core_call("state")

    @router.get("/detail")
    async def detail(skill: str) -> dict:
        return _core_call("detail", skill)

    @router.get("/diff")
    async def diff() -> dict:
        return _core_call("diff")

    @router.post("/toggle")
    async def toggle(body: dict) -> dict:
        return _core_call("toggle", body.get("skill"), body.get("tool"), body.get("enabled"))

    @router.post("/toggle-bulk")
    async def toggle_bulk(body: dict) -> dict:
        return _core_call("toggle_bulk", body.get("skills"), body.get("tool"), body.get("enabled"))

    @router.post("/bulk/plan")
    async def bulk_plan(body: dict) -> dict:
        return _core_call("plan_bulk", body.get("skills"), body.get("tool"), body.get("enabled"))

    @router.post("/bulk/apply")
    async def bulk_apply(body: dict) -> dict:
        return _core_call("execute_bulk",
            body.get("skills"),
            body.get("tool"),
            body.get("enabled"),
            body.get("receipt_id"),
        )

    @router.get("/bulk/receipt")
    async def bulk_receipt(receipt_id: str) -> dict:
        return _core_call("get_bulk_receipt", receipt_id)

    @router.post("/bulk/undo")
    async def bulk_undo(body: dict) -> dict:
        return _core_call("undo_bulk", body.get("receipt_id"))

    @router.post("/repair")
    async def repair(body: dict) -> dict:
        return _core_call("repair", body.get("skill"), body.get("tool"))

    @router.post("/repair-all")
    async def repair_all() -> dict:
        return _core_call("repair_all")

    @router.post("/ensure-tool-dir")
    async def ensure_tool_dir(body: dict) -> dict:
        return _core_call("ensure_tool_dir", body.get("tool"))

    @router.get("/import/scan")
    async def import_scan() -> dict:
        return _core_call("import_scan")

    @router.post("/import/apply")
    async def import_apply(body: dict) -> dict:
        return _core_call("import_apply", body.get("tool"), body.get("names"), body.get("category", "imported"))

    @router.post("/import/plan")
    async def import_plan(body: dict) -> dict:
        return _core_call("import_plan",
            body.get("tools"),
            body.get("scan_roots", []),
            body.get("category", "imported"),
        )

    @router.post("/import/apply-plan")
    async def import_apply_plan(body: dict) -> dict:
        return _core_call("import_apply_plan",
            body.get("entries"),
            body.get("category", "imported"),
        )

    @router.get("/drift")
    async def drift() -> dict:
        return _core_call("drift")

    @router.post("/config/tools")
    async def config_tools(body: dict) -> dict:
        return _core_call("set_tool", body.get("id"), body.get("label"), body.get("dir"))

    @router.get("/mcp/state")
    async def mcp_state() -> dict:
        return _call(get_mcp_core().mcp_state)

    @router.post("/mcp/toggle")
    async def mcp_toggle(body: dict) -> dict:
        return _call(get_mcp_core().toggle_hermes, body.get("name"), body.get("enabled"))

    @router.post("/mcp/sync")
    async def mcp_sync(body: dict) -> dict:
        return _call(get_mcp_core().sync_to_claude, body.get("name"), body.get("force", False))

    @router.post("/mcp/remove")
    async def mcp_remove(body: dict) -> dict:
        return _call(get_mcp_core().remove_from_claude, body.get("name"), body.get("force", False))

    @router.post("/mcp/codex/sync")
    async def mcp_codex_sync(body: dict) -> dict:
        return _call(get_mcp_core().sync_to_codex, body.get("name"), body.get("force", False))

    @router.post("/mcp/codex/remove")
    async def mcp_codex_remove(body: dict) -> dict:
        return _call(get_mcp_core().remove_from_codex, body.get("name"), body.get("force", False))

    @router.get("/blueprint/export")
    async def blueprint_export() -> dict:
        return _core_call("blueprint_export")

    @router.post("/blueprint/apply")
    async def blueprint_apply(body: dict) -> dict:
        return _core_call("blueprint_apply",
            body.get("blueprint"),
            bool(body.get("dry_run", False)),
        )

    @router.get("/backups")
    async def list_backups() -> dict:
        return _core_call("list_backups")

    @router.post("/backups/restore")
    async def restore_backup(body: dict) -> dict:
        return _core_call("restore_backup", body.get("path"))

    @router.post("/conflict/revert-push")
    async def revert_push(body: dict) -> dict:
        return _core_call("revert_push", body.get("tool"), body.get("name"), body.get("tool_backup"))

    @router.post("/conflict/revert-pull")
    async def revert_pull(body: dict) -> dict:
        return _core_call("revert_pull",
            body.get("tool"), body.get("name"), body.get("hermes_backup"), body.get("tool_backup"),
        )

    @router.post("/conflict/revert-adopt")
    async def revert_adopt(body: dict) -> dict:
        return _core_call("revert_adopt",
            body.get("tool"), body.get("name"), body.get("tool_backup"), body.get("skill"),
        )

    @router.post("/conflict/pull")
    async def conflict_pull(body: dict) -> dict:
        return _core_call("conflict_pull", body.get("tool"), body.get("name"))

    @router.post("/conflict/keep-both")
    async def conflict_keep_both(body: dict) -> dict:
        return _core_call("conflict_keep_both", body.get("tool"), body.get("name"))

    @router.post("/drift/push")
    async def drift_push(body: dict) -> dict:
        return _core_call("drift_push", body.get("tool"), body.get("name"))


else:  # pragma: no cover — non-gateway import (tests); keep attribute defined
    router = None
