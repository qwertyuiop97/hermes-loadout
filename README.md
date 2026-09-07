# skills-toggle

[![tests](https://github.com/qwertyuiop97/skills-toggle/actions/workflows/tests.yml/badge.svg)](https://github.com/qwertyuiop97/skills-toggle/actions/workflows/tests.yml)

A Hermes desktop plugin that makes **Hermes the source of truth for your skills
across every coding tool**. A native pane inside the Hermes desktop app lists
every skill under `~/.hermes/skills/<category>/<name>/SKILL.md` and gives you a
per-tool on/off switch for each one — Hermes itself, Claude, Codex, OpenCode,
Grok, and ZCode — plus broken-link repair, a health diff, search, filters, and
bulk actions.

Consumer tools get **symlinks** into the Hermes skills tree (never copies), so
there is exactly one copy of every skill on your machine. Disabling a skill for
a tool removes that tool's symlink; the skill source is never deleted. For
Hermes itself, toggling edits the `skills.disabled` list in `config.yaml`.

```
┌──────────────────────────────────────────────────────────┐
│ Skills                      3 skills  1 broken  Repair all│
│ [Search skills…                                 ] Refresh │
│ All · Hermes · Claude · Codex · OpenCode · Grok · ZCode   │
│ ( All | Issues | Off )                                    │
│                                                           │
│ APPLE ────────────────────────────────────── 2 ────────── │
│ ● apple-notes                              apple          │
│   control Apple Notes via JXA                             │
│   [●] Hermes [○] Claude [●] Codex [○] OpenCode …          │
│ …                                                         │
└──────────────────────────────────────────────────────────┘
```

## Package layout (unified: one folder, both SDKs)

```
skills-toggle/
├── plugin.yaml              # agent half — metadata only
├── dashboard/
│   ├── manifest.json        # {"name": "skills-toggle", "api": "plugin_api.py"}
│   └── plugin_api.py        # FastAPI routes → /api/plugins/skills-toggle/…
├── desktop/
│   └── plugin.js            # the native desktop pane (+ ⌘K command, page)
├── tests/                   # backend round-trips + HTTP tests + render harness
├── README.md                # this file
├── DECISIONS.md             # every material build decision, with reasons
├── LICENSE                  # MIT
└── .gitignore
```

## Install

```bash
git clone https://github.com/qwertyuiop97/skills-toggle.git
cp -r skills-toggle ~/.hermes/plugins/          # $HERMES_HOME/plugins on profiles
```

Or with the Hermes CLI:

```bash
hermes plugins install qwertyuiop97/skills-toggle
```

Or one click in the desktop app: [Install in Hermes](hermes://plugin/install?repo=qwertyuiop97/skills-toggle) (deep links always ask before installing).

## Enable gates (both halves are opt-in by design)

| Half | Gate |
|------|------|
| Python backend (`plugin_api.py`) | add `skills-toggle` to `plugins.enabled` in `~/.hermes/config.yaml`, or run `hermes plugins enable skills-toggle`. Without it the pane renders an explicit "backend unavailable" error state; with it, routes mount at gateway start (restart the gateway once after enabling). |
| Desktop pane (`desktop/plugin.js`) | **Settings → Plugins → Skills Toggle** → switch on (it inventories disabled). |

Then in the desktop app: **⌘K → "Reload desktop plugins"**. Open the pane from
the right-pane tabs, the **⌘K palette ("Skills: toggle…")**, or the
`/skills-toggle` page. Everything lives inside the Hermes desktop app — there is
no external UI.

## Uninstall

```bash
hermes plugins disable skills-toggle   # if enabled
rm -rf ~/.hermes/plugins/skills-toggle
```

The plugin only ever creates/removes symlinks named after skills and appends to
`skills.disabled` (with timestamped `config.yaml` backups). Removing the plugin
folder removes nothing else. Tool dirs the plugin auto-created (e.g. a fresh
`~/.codex/skills/`) are left in place — they contain only links you made.

## Where each tool's links live

| Tool | Skills path | Notes |
|------|-------------|-------|
| Hermes | `~/.hermes/skills/<category>/<name>` | source of truth; per-skill off = `skills.disabled` (bare names) in `config.yaml` |
| Claude | `~/.claude/skills/<name> → skills tree` | covers Claude Code CLI, Claude Desktop, code-through-desktop |
| Codex | `~/.codex/skills/<name> → skills tree` | |
| OpenCode | `${OPENCODE_CONFIG_DIR:-~/.config/opencode}/skills/<name> → skills tree` | honors `OPENCODE_CONFIG_DIR` |
| Grok | `~/.grok/skills/<name> → skills tree` | real dirs / foreign links are reported, never touched |
| ZCode | `~/.agents/skills/<name> → skills tree` (fallback `~/.zcode/skills`) | detected at runtime |

All paths resolve through `~`/`$HOME`; the Hermes root honors `$HERMES_HOME`,
then `$HERMES_PROFILE` (`~/.hermes/profiles/<name>`), then `~/.hermes`. There are
zero hardcoded usernames or absolute paths.

### Custom tool map (optional)

Defaults live in code; override or add tools by writing
`<hermes_home>/skills-toggle.json` (outside the plugin folder, so updates don't
clobber it):

```json
{
  "tools": {
    "claude": "~/.claude/skills",
    "aider": { "label": "Aider", "dir": "~/.aider/skills" }
  }
}
```

`~` and `${VAR:-default}` are expanded in `dir`.

## What the pane shows

- **Per-tool switch per skill.** Switch on → real symlink created (absolute
  target, link name = skill name). Switch off → that symlink removed. Hermes's
  switch edits `skills.disabled` instead (with a `config.yaml.bak.skills-toggle.<ts>` backup).
- **State dots.** `enabled` (green), `missing` (muted), `broken-link` (amber,
  with a one-click *fix* button), `foreign-link`/`unmanaged-dir` (red, locked —
  the plugin refuses to touch symlinks pointing outside the skills tree or real
  directories, and tells you why on hover).
- **Absent tool dirs.** A tool whose skills dir doesn't exist shows its switches
  as available; the first enable creates the directory and links in one step
  (toast confirms `created-dir+linked`).
- **Repair all** (header, when broken links exist) and **per-category Link
  all / Unlink all** for the selected tool (confirm dialog first, per-skill
  results reported).
- **Search** (200 ms debounce), **tool filter chips**, and an
  **All / Issues / Off** view switch. Filter + view persist via plugin storage.
- **Toasts** confirm every mutation; failures roll the optimistic toggle back
  and name the error. All colors come from theme variables — the pane follows
  light/dark and every theme automatically.

## API (mounted at `/api/plugins/skills-toggle/`)

| Route | Body/Query | Effect |
|-------|------------|--------|
| `GET /health` | — | liveness + resolved paths |
| `GET /state` | — | every skill + per-tool state (lean: descriptions truncated; cached with mtime invalidation) |
| `GET /detail` | `?skill=category/name` | full SKILL.md text + states |
| `GET /diff` | — | unlinked skills, broken/foreign/unmanaged links |
| `POST /toggle` | `{skill, tool, enabled}` | add/remove one symlink or edit `skills.disabled` |
| `POST /toggle-bulk` | `{skills: […], tool, enabled}` | bulk toggle with per-skill results |
| `POST /repair` | `{skill, tool}` | re-point a broken link |
| `POST /repair-all` | — | repair every broken link that points into the skills tree |
| `POST /ensure-tool-dir` | `{tool}` | create a missing tool skills dir |

Every route returns `{ok: true, …}` or `{ok: false, error, code}`. Skill ids are
validated against an allowlist built from the real skills tree (path traversal,
unknown ids, non-boolean `enabled` → rejected). All mutations are logged to
`data/mutations.log` (JSONL) inside the plugin folder.

## Tests — receipts

```
python3 -m unittest tests.test_plugin_api        # 46 tests — core round-trips
./.venv/bin/python -m unittest tests.test_routes_http   # 9 tests — real FastAPI HTTP round-trips
python3 tests/check_frontend.py                  # SDK-constraint static checks
./tests/run_render_harness.sh                    # 19 assertions — real React renderToString of the pane
```

Covered: link/unlink/repair/diff round-trips per tool · absent-dir creation ·
broken-link repair (including links to moved/deleted skills, by name) ·
foreign-link and unmanaged-dir refusal (never delete real data) · path-traversal
and unknown-id rejection (unicode names, names with spaces, `..`, absolute
paths) · hermes config editing across YAML shapes (block list, inline list,
scalar, null, missing keys, comments preserved, CRLF preserved, timestamped
backup) · cache invalidation · concurrent toggles · mutation logging · every
HTTP route · ESM parse / zero JSX / only-allowed imports / all rendered
identifiers imported / no hardcoded colors / no `localStorage`/`document` ·
pane renders loading skeleton, error banner, empty states, filters, bulk
actions, unicode, badges.

### Test matrix — verified per tool against /tmp fixtures

| Tool | link | unlink | repair | absent-dir |
|------|------|--------|--------|------------|
| Hermes (config) | ✅ | ✅ | n/a (config-based, refused) | n/a |
| Claude | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |
| Codex | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |
| OpenCode | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |
| Grok | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |
| ZCode | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |

Rows were verified through the core test suite (46 tests) and the HTTP suite
(9 tests); each link/unlink round-trip runs for every tool id, and
`ensure-tool-dir` covers the absent-dir column for every link tool. Grok's
real-dir / foreign-link refusal paths get dedicated tests. The desktop half's
staging render harness verifies the pane against a fixture shaped like the Air
(unicode categories, spaced names, all link states).

## Fresh-machine checklist

1. `git clone https://github.com/qwertyuiop97/skills-toggle.git && cp -r skills-toggle ~/.hermes/plugins/`
2. Gate 1: `hermes plugins enable skills-toggle` (adds to `plugins.enabled`); restart the gateway (backend routes mount at startup).
3. Gate 2: Hermes desktop → **Settings → Plugins** → enable **Skills Toggle**.
4. **⌘K → "Reload desktop plugins"**.
5. Open the pane (right-pane tab or ⌘K "Skills: toggle…") — it should render your skills with a skeleton first, never a blank pane, and no error toast.
6. Toggle a skill on for Claude → toast "Linked for Claude" → check `ls -la ~/.claude/skills/` shows the symlink.
7. Toggle it off → toast "Unlinked for Claude" → symlink gone, `~/.hermes/skills/…` source still present.
8. Toggle Hermes off for a skill → `grep -A3 '^skills:' ~/.hermes/config.yaml` shows it under `disabled:` (a `config.yaml.bak.skills-toggle.*` backup appears next to it).
9. Restart the desktop app → pane, filters, and link states persist (links are real symlinks; filters live in plugin storage).

## Using the core from another app

`dashboard/plugin_api.py` is a **shared, dependency-free core** with a stable
function API (see `__all__` in the module — additions only; removals/renames
are breaking). It imports stdlib at module level; `fastapi` is optional and
guarded (without it, `router is None` and everything else works). Enforced by
`tests/test_core_api.py`.

```python
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "skills_toggle_core",
    "~/.hermes/plugins/skills-toggle/dashboard/plugin_api.py",
)
core_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core_mod)

home = core_mod.hermes_home()                 # $HERMES_HOME > profile > ~/.hermes
tools = core_mod.load_tools_config(home)      # defaults + skills-toggle.json
core = core_mod.SkillsToggleCore(home, tools) # explicit construction, no globals

state = core.state()                          # {ok, skills, tools, counts, …}
core.toggle("creative/architecture-diagram", "claude", True)
diff = core.diff()                            # unlinked / broken / foreign / unmanaged
```

Stable surface: `SkillsToggleCore` (`.state .detail .diff .toggle .toggle_bulk
.repair .repair_all .ensure_tool_dir .import_scan .import_apply .drift
.drift_push .set_tool .health .invalidate`), `hermes_home`, `expand_path`,
`parse_skill_markdown`, `parse_disabled`, `set_disabled_member`,
`load_tools_config`, `user_config_path`, `get_core`/`reset_core`,
`SkillsToggleError` (`.code`), `ConfigEditError`, `DEFAULT_TOOLS`,
`PLUGIN_ID`, `PLUGIN_VERSION`. The FastAPI `router` (when fastapi is present)
mounts the same operations at `/api/plugins/skills-toggle/…`.

## v3 — what's new

- **Conflict resolution completed** — the Drift view now offers three actions per same-name conflict: **Use Hermes** (backup tool copy, link canonical), **Use tool copy** (Hermes source backed up dotted inside its category, tool copy becomes canonical, tool links to it), and **Keep both** (tool copy adopted as `imported/<name>.from-<tool>`). Every path preserves both originals; failures roll back.
- **Undo symmetry** — the 30-second undo banner now also covers adoption, drift-push, pull, and keep-both via real backend reverts (`/conflict/revert-*`), not just "find the backup yourself".
- **Machine blueprint** — export the entire link map (every skill→tool link + the `skills.disabled` set) as one JSON file and apply it on another machine. Additive-only by owner decision: it creates missing links and Hermes-offs, never removes. Dry-run preview (counts + refusals) before apply; idempotent (re-applying a satisfied blueprint plans nothing).
- **Backup browser + restore** — the Setup panel lists every backup the plugin ever created (config, tools-json, tool-link, hermes-copy) with one-click **Restore**; restore always backs up the current state first and only accepts paths from its own live scan.
- **Watch mode** — opt-in native notifications (only when the app is unfocused) for new skills, broken links, and drift, with per-class toggles.
- **Per-category auto-link** — auto-link preferences take an optional category regex (e.g. `creative|note-taking`), not just all-or-nothing.
- **MCP: Codex writer** — the MCP tab now also mirrors the catalog into `~/.codex/config.toml` (`[mcp_servers.<name>]` tables, comment-preserving block surgery, drift/foreign guards). OpenCode is pending one schema artifact (see QUESTIONS_FOR_HERMES.md Q6).
- **Optional agent targets** — Cursor, Windsurf, Copilot, Gemini, and Kimi skills dirs are offered as opt-in tools in Setup (they appear in the rows/filters only once their dir exists).
- **Windows** — backend CI runs on windows-latest; path identity survives `\?\` extended paths, 8.3 short names, and case differences via the `same_path`/`is_inside` canonicalizers (exported for sibling apps).

## MCP switchboard (v2.2)

The pane's **MCP** tab applies the same source-of-truth model to MCP servers:
the `mcp_servers` map in Hermes' `config.yaml` is the catalog, and the tab
mirrors entries into **Claude Desktop** (`claude_desktop_config.json`, resolved
macOS → Linux → Windows `%APPDATA%`).

- **Hermes switch** — flips the entry's `enabled:` flag (surgical edit, timestamped backup, re-parse self-check). This is Hermes' own on/off.
- **Claude switch** — ON syncs the catalog entry (universal keys only: `command`, `args`, `env`, `url`, `headers`; hermes-only keys like `enabled` are stripped); OFF removes the entry. Configs that drifted from the catalog require an explicit confirm (the old copy is kept in a timestamped backup either way).
- **Foreign servers** (present in Claude Desktop but not in the Hermes catalog) are listed and **never touched**.
- Claude Desktop reloads its config on window focus/restart — flip, then focus Claude.

## MCP core API (sibling-safe)

`McpCore` joins the stable surface: `new McpCore(home, claude_desktop_config?, log_path?)` with
`.catalog() .mcp_state() .toggle_hermes(name, enabled) .sync_to_claude(name)
.remove_from_claude(name, force?)`, plus the standalone helpers
`parse_mcp_servers(text)` and `set_mcp_server_enabled(text, name, enabled)`.
Same rules as the skills core: stdlib-only, explicit paths, `{ok}` envelopes.

## Troubleshooting

**The pane renders but shows "Skills backend unavailable" with `404 … Headless backend (hermes serve): web UI disabled`.**
The gateway mounts plugin API routes **only at startup** — your gateway was started before `skills-toggle` was added to `plugins.enabled`, so the router never mounted. Fix:

```bash
hermes plugins enable skills-toggle   # if not already in plugins.enabled
hermes gateway restart                # mounts the routes
```

Then hit **Retry** in the pane (the pane detects this exact failure and shows this remedy inline; the raw error stays visible underneath).

**`404 {"detail": "Plugin not found"}`** — the per-request gate rejected the plugin: `skills-toggle` is missing from `plugins.enabled` (or present in `plugins.disabled`). Same two commands as above.

**Pane doesn't appear at all.** Confirm `desktop/plugin.js` is installed, enable it in **Settings → Plugins**, then ⌘K → **Reload desktop plugins**. The folder name must equal the plugin `id` (`skills-toggle`).

**`ctx.rest` 404 after edits to `plugin_api.py`.** Python routes import at gateway start too — `hermes gateway restart` picks them up.

## Requirements

- Hermes desktop app (disk-plugin capable build) with `@hermes/plugin-sdk`
- The gateway process (for `plugin_api.py`; FastAPI ships with Hermes)
- Python ≥ 3.10 to run the test suite; Node ≥ 18 for the render harness
- No third-party runtime dependencies — the backend core is stdlib-only

## License

MIT — see [LICENSE](LICENSE).

## v2 — what's new

- **Responsive pane** — the pane measures its own width (ResizeObserver): 2-column tool grid and scrollable filters under 360 px, 3-column above. Usable at the default 320 px dock width.
- **Onboarding / Setup panel** — when no tool skills folders exist, the pane offers one-click folder creation, custom-tool addition (written to `skills-toggle.json` with backups), per-tool **auto-link** opt-in, and a "Find copies to adopt" scanner.
- **New-skill prompt** — skills added under the Hermes tree since your last visit trigger one banner: pick tools, link, or ignore. Silent on first run (nothing is ever enabled unprompted). The ⚡ chip on any tool opts that tool into auto-linking future arrivals.
- **Health chip + ⌘K report** — statusbar badge shows broken/unlinked counts; two palette commands ("Skills: toggle…", "Skills: health report").
- **Bulk everywhere** — per-skill "all / none" (link into every tool at once), per-category bulk for the selected tool, built-in presets (Coding / Writing / Minimal), preset import (paste JSON) and copy-current-as-JSON export.
- **Undo** — every bulk/preset/arrival action shows an in-pane Undo banner for 30 s that restores the previous states through the same safety-checked toggles.
- **Adoption & drift** — `GET /import/scan` classifies every tool dir (managed / broken / foreign / adoptable copies / drifted hashes); `POST /import/apply` adopts a copy (into `imported/`, original preserved as a timestamped backup, symlink swapped in, same-name conflicts refused); `GET /drift` lists same-name skills whose SKILL.md hashes differ from the Hermes source.

Preset file format (v1): `{"version": 1, "name": "…", "skills": ["category/name", …], "tools": ["claude", …]}` — applying is additive; unknown ids are skipped and reported.
