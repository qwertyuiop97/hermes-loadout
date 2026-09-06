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
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ID = "skills-toggle"
PLUGIN_VERSION = "1.0.0"

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
            # list becomes empty — rewrite the block cleanly as `disabled: []`
            new_lines = lines[:dis_idx]
            new_lines.append(f"{dis_indent}disabled: []")
            new_lines.extend(lines[items[-1][0] + 1 :])
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
        return expand_path(tool["dir"]) if isinstance(tool["dir"], str) else tool["dir"]

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
            if resolved == skill["dir"].resolve():
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
                    elif self.skills_root_resolved not in resolved.parents:
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
        skill_dir_resolved = skill["dir"].resolve()

        def link_state() -> tuple[str, str | None]:
            if link.is_symlink():
                target = os.readlink(link)
                base = Path(target) if os.path.isabs(target) else (link.parent / target)
                resolved = base.resolve()
                if resolved == skill_dir_resolved:
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
            os.symlink(str(skill_dir_resolved), str(link))
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
                if self.skills_root_resolved not in base.resolve().parents:
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


def set_core_for_testing(core: SkillsToggleCore | None) -> None:
    global _CORE, _CORE_SIG, _CORE_FROZEN
    if core is None:
        _CORE = None
        _CORE_SIG = None
        _CORE_FROZEN = False
        return
    _CORE = core
    _CORE_SIG = ("test", id(core))
    _CORE_FROZEN = True


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


else:  # pragma: no cover — non-gateway import (tests); keep attribute defined
    router = None
