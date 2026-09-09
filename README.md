# skills-toggle

[![tests](https://github.com/qwertyuiop97/skills-toggle/actions/workflows/tests.yml/badge.svg)](https://github.com/qwertyuiop97/skills-toggle/actions/workflows/tests.yml)

A Hermes desktop plugin that makes **Hermes the source of truth for your skills
across every coding tool**. The compact right pane is a summary. Full management
lives in Control Center: one card per tool, single-tool switches, bulk preview
and undo, a first-run scan wizard, Problems, MCP, and Advanced. An optional
expert matrix appears only on wide screens and never silently omits a tool.

Consumer tools get **symlinks** into the Hermes skills tree (never copies), so
there is exactly one copy of every skill on your machine. Disabling a skill for
a tool removes that tool's symlink; the skill source is never deleted. For
Hermes itself, toggling edits the `skills.disabled` list in `config.yaml`.

```
Compact pane (right, ~320px)          Control Center (workspace / /skills-toggle)
┌──────────────────────────┐          ┌ Tools | Sets | Problems | MCP | Advanced ─┐
│ Skills   105 · 5 tools   │          │ Claude     39 on / 105                    │
│ 3 broken · 1 drift       │          │ [Manage]  [Enable all]  [Disable all]     │
│ Claude    39 on          │          │ …                                         │
│ Codex     62 on          │          │ Single-tool: one switch per skill         │
│ [Open Control Center]    │          │ Preview → confirm → receipt → Undo        │
│ [Scan]  [Problems (n)]   │          └───────────────────────────────────────────┘
└──────────────────────────┘
```

## Package layout (unified: one folder, both SDKs)

```
skills-toggle/
├── plugin.yaml              # agent half — metadata only
├── dashboard/
│   ├── manifest.json        # {"name": "skills-toggle", "api": "plugin_api.py"}
│   └── plugin_api.py        # FastAPI routes → /api/plugins/skills-toggle/…
├── desktop/
│   └── plugin.js            # compact pane + Control Center + ⌘K commands
├── tests/                   # backend, HTTP, static SDK checks, render harness
├── docs/                    # shell design, live-host checklist, dogfood record
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
hermes plugins enable skills-toggle
```

Or one click in the desktop app: [Install in Hermes](hermes://plugin/install?repo=qwertyuiop97/skills-toggle) (deep links always ask before installing).

## Enable gates (both halves are opt-in by design)

| Half | Gate |
|------|------|
| Python backend (`plugin_api.py`) | add `skills-toggle` to `plugins.enabled` in `~/.hermes/config.yaml`, or run `hermes plugins enable skills-toggle`. Without it the pane renders an explicit "backend unavailable" error state; with it, routes mount at gateway start (restart the gateway once after enabling). |
| Desktop pane (`desktop/plugin.js`) | **Settings → Plugins → Skills Toggle** → switch on (it inventories disabled). |

Then in the desktop app: **⌘K → "Reload desktop plugins"**. Open the compact
pane from the right-pane tabs. Open Control Center from **Open Control Center**,
the **⌘K palette ("Skills: toggle…")**, or the `/skills-toggle` page.

## Uninstall

```bash
hermes plugins disable skills-toggle   # if enabled
rm -rf ~/.hermes/plugins/skills-toggle
```

The plugin only ever creates/removes skill-named symlinks and appends to
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

## What you actually use

**Compact pane (right, default 320 px).** Counts, per-tool on/off totals, a
Problems shortcut, and **Open Control Center**. It is a summary, not the
six-switch catalog.

**Control Center → Tools.** One card per detected tool with enabled / off /
problem counts plus **Manage**, **Enable all**, and **Disable all**. Manage
opens a single-tool list: one switch per skill, category filters, selection,
and bulk actions. Every bulk change is preview → confirm → receipt → Undo.
Protected entries (foreign links, real directories) are refused and named.

**First-run scan.** If no tool folders exist, or from Scan & import: pick
detected tools and optional extra folders, review classifications, dry-run,
confirm, then apply. Hermes is the only canonical write target. Extra folders
are scan sources, never a second store.

**Sets.** Named presets (Coding / Writing / Minimal) plus import/export of a
preset JSON. Additive except Minimal, which unlinks consumer tools and keeps
Hermes sources.

**Problems.** Broken-link repair (per item and Repair all) plus drift: Use
Hermes, Use tool copy, or Keep both. Originals are preserved.

**MCP.** Hermes catalog is source of truth. Mirror into Claude Desktop and
Codex (`config.toml`). Foreign servers stay visible and untouched. OpenCode
MCP writing is deferred (see `QUESTIONS_FOR_HERMES.md` Q6).

**Advanced.** Setup (create missing tool dirs, custom tools), watch mode,
machine blueprint, backup browser, auto-link regexes. Not on the daily Tools
surface.

**Expert matrix.** Optional, wide containers only (`layout === 'wide'`).
Columns are Hermes plus every present tool; no tool is silently omitted.
Categories start collapsed; descriptions stay hidden until asked.

Arrivals of new Hermes skills surface prominently on **Tools**, and the same
banner repeats in Setup/Advanced where auto-link preferences live.

## API (mounted at `/api/plugins/skills-toggle/`)

Stable routes are additive. Existing `/import/scan`, `/import/apply`,
`/bulk/plan`, `/bulk/apply`, `/import/plan`, and `/import/apply-plan` stay.

| Route | Body/Query | Effect |
|-------|------------|--------|
| `GET /health` | — | liveness + resolved paths |
| `GET /state` | — | every skill + per-tool state (lean; cached with mtime invalidation) |
| `GET /detail` | `?skill=category/name` | full SKILL.md text + states |
| `GET /diff` | — | unlinked skills, broken/foreign/unmanaged links |
| `POST /toggle` | `{skill, tool, enabled}` | add/remove one symlink or edit `skills.disabled` |
| `POST /toggle-bulk` | `{skills: […], tool, enabled}` | bulk toggle with per-skill results |
| `POST /bulk/plan` | `{skills: […], tool, enabled}` | dry-run preview (changed / already / refused) |
| `POST /bulk/apply` | `{skills, tool, enabled, receipt_id}` | apply only the reviewed plan; receipt + undo token |
| `POST /repair` | `{skill, tool}` | re-point a broken link |
| `POST /repair-all` | — | repair every broken link that points into the skills tree |
| `POST /ensure-tool-dir` | `{tool}` | create a missing tool skills dir |
| `GET /import/scan` | — | classify tool dirs (managed / broken / foreign / adoptable / drift) |
| `POST /import/apply` | selected copies | adopt copies into Hermes (legacy apply) |
| `POST /import/plan` | `{tools?, scan_roots?, category?}` | first-run / scan-wizard dry-run |
| `POST /import/apply-plan` | exact planned entries | apply only the reviewed plan |
| `GET /drift` | — | same-name skills whose SKILL.md hashes differ |
| `POST /config/tools` | tool map | write `<hermes_home>/skills-toggle.json` |
| `GET /mcp/state` | — | Hermes catalog + Claude/Codex mirror state |
| `POST /mcp/toggle` | `{name, enabled}` | Hermes `enabled:` flag |
| `POST /mcp/sync` `/mcp/remove` | `{name}` | Claude Desktop mirror |
| `POST /mcp/codex/sync` `/mcp/codex/remove` | `{name}` | Codex `config.toml` mirror |
| `GET /blueprint/export` | — | entire link map as JSON |
| `POST /blueprint/apply` | blueprint | additive-only apply (creates missing links / Hermes-offs) |
| `GET /backups` | — | backups the plugin created |
| `POST /backups/restore` | `{path}` | restore one backup (backs up current first) |
| `POST /conflict/*` `/drift/push` | — | Use Hermes / Use tool copy / Keep both + revert |

Every route returns `{ok: true, …}` or `{ok: false, error, code}`. Skill ids are
validated against an allowlist built from the real skills tree. All mutations
are logged to `data/mutations.log` (JSONL) inside the plugin folder. Secret
values are redacted recursively before they reach UI state, receipts, or logs.

## Tests

From the repo root:

```
python3 -m unittest tests.test_core_api tests.test_mcp_backend tests.test_plugin_api tests.test_v2_backend tests.test_bulk_planning tests.test_scan_wizard
.venv/bin/python3 -m unittest tests.test_routes_http
python3 tests/check_frontend.py
./tests/run_render_harness.sh
```

Local baseline verified 2026-09-08 after item 12: 127 core backend tests on
system Python, 13 HTTP tests in `.venv`, static SDK checks, and both render
harness bodies (compact pane + Control Center) with zero React-key /
`console.error` / `console.warn` output. Treat the counts as a dated baseline,
not a hardcoded ceiling — new behavior should add coverage.

CI (`.github/workflows/tests.yml`): Ubuntu / macOS / Windows on Python 3.12 for
backend + HTTP; a dedicated Ubuntu Python 3.9 job running all six core suites;
Ubuntu Node 20 for the frontend static checks and render harness.

Covered: link/unlink/repair/diff per tool · absent-dir creation · broken-link
repair · foreign-link and unmanaged-dir refusal · path-traversal / unknown-id
rejection · Hermes config editing across YAML shapes with timestamped backup ·
bulk plan → apply → receipt → undo · scan-wizard plan → apply-plan · cache
invalidation · concurrent toggles · mutation logging · HTTP routes · ESM parse /
zero JSX / allowed imports / no hardcoded colors / no web-storage persistence ·
compact pane, tool cards, single-tool view, first-run wizard, wide-only matrix,
Problems repair UI, arrivals on Tools, reduced-motion CSS.

### Test matrix — verified per tool against /tmp fixtures

| Tool | link | unlink | repair | absent-dir |
|------|------|--------|--------|------------|
| Hermes (config) | ✅ | ✅ | n/a (config-based, refused) | n/a |
| Claude | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |
| Codex | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |
| OpenCode | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |
| Grok | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |
| ZCode | ✅ | ✅ | ✅ | ✅ (create-dir-and-link) |

Backend tests use temp fixtures under `/tmp`, never the real `~/.hermes` tree.
The desktop half's staging render harness verifies the pane against a
screenshot-shaped 105-skill fixture.

## Fresh-machine checklist

1. `git clone https://github.com/qwertyuiop97/skills-toggle.git && cp -r skills-toggle ~/.hermes/plugins/`
2. Gate 1: `hermes plugins enable skills-toggle`; restart the gateway (backend routes mount at startup).
3. Gate 2: Hermes desktop → **Settings → Plugins** → enable **Skills Toggle**.
4. **⌘K → "Reload desktop plugins"**.
5. Compact pane should render counts (never a blank pane). **Open Control Center**.
6. If this is a first install, complete the scan wizard (detected tools are pre-selected; extra folders are optional).
7. On Tools, **Manage** one disposable tool → preview Disable all → confirm → Undo.
8. Restart the desktop app → pane, filters, and link states persist (links are real symlinks; filters live in plugin storage).

For the cases the render harness cannot prove (live ResizeObserver, palette atom
switching, gateway route mount, restart persistence), use
[`docs/live-host-acceptance.md`](docs/live-host-acceptance.md). Recorded
isolated-profile smoke: [`docs/dogfood-record.md`](docs/dogfood-record.md).

## Using the core from another app

`dashboard/plugin_api.py` is a **shared, dependency-free core** with a stable
function API (see `__all__` in the module — additions only; removals/renames
are breaking). It imports stdlib at module level; `fastapi` is optional and
guarded (without it, `router is None` and everything else works). Enforced by
`tests/test_core_api.py`.

```python
import importlib.util

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

## MCP switchboard

The **MCP** section applies the same source-of-truth model to MCP servers:
the `mcp_servers` map in Hermes' `config.yaml` is the catalog, mirrored into
**Claude Desktop** (`claude_desktop_config.json`) and **Codex** (`~/.codex/config.toml`).

- **Hermes switch** — flips the entry's `enabled:` flag (surgical edit, timestamped backup).
- **Claude / Codex switches** — ON syncs universal keys only (`command`, `args`, `env`, `url`, `headers`); OFF removes the entry. Drifted copies require explicit confirm.
- **Foreign servers** (present in the target but not in the Hermes catalog) are listed and **never touched**.
- OpenCode MCP writing is deferred until a real populated `opencode.jsonc` sample exists (Q6).

`McpCore` joins the stable surface: `new McpCore(home, claude_desktop_config?, log_path?)` with
`.catalog() .mcp_state() .toggle_hermes(name, enabled) .sync_to_claude(name)
.remove_from_claude(name, force?)`, plus `parse_mcp_servers` /
`set_mcp_server_enabled`. Same rules as the skills core: stdlib-only, explicit
paths, `{ok}` envelopes.

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
- Python 3.9+ for the dependency-free core/import contract; the full local/CI
  development environment currently runs on newer Python. Node ≥ 18 is required
  for the render harness.
- No third-party runtime dependencies — the backend core is stdlib-only

## License

MIT — see [LICENSE](LICENSE).

## What's new in the stabilization (items 6–12)

- Compact pane is a summary; Control Center is the management UI.
- Tool-first overview with Manage / Enable all / Disable all.
- Single-tool management with dry-run bulk plan, protected-entry report, receipt, and undo.
- First-run scan wizard: detected tools, extra scan folders, classify, dry-run, apply.
- Information architecture: Tools, Sets, Problems, MCP, Advanced. Expert matrix is wide-only.
- 105-skill fixture stays responsive; reduced-motion CSS; keyboard names on switches;
  Python 3.9 core gate; macOS/Linux/Windows CI; zero React-key warnings in the harness.
- Installed-Hermes smoke on an isolated profile: plugin enables, `serve` mounts
  `/api/plugins/skills-toggle/*` (401-not-404 after auth wrap).

Do not tag a release until every gate in `IMPLEMENTATION_PLAN.md` §11 passes,
including owner live-host dogfood of ResizeObserver, palette atom switching,
and restart persistence. This README describes the shipped UI; it is not a
release announcement.
