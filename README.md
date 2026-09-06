# skills-toggle

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

## Requirements

- Hermes desktop app (disk-plugin capable build) with `@hermes/plugin-sdk`
- The gateway process (for `plugin_api.py`; FastAPI ships with Hermes)
- Python ≥ 3.10 to run the test suite; Node ≥ 18 for the render harness
- No third-party runtime dependencies — the backend core is stdlib-only

## License

MIT — see [LICENSE](LICENSE).
