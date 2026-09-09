# Solo dogfood record — skills-toggle alpha packaging

Plan §12 / BACKLOG item 13. This is a dated record, not a release claim.
Do not treat isolated-profile smoke as owner GUI dogfood.

## Scope of this record (2026-09-08)

Isolated Hermes profile `skt-livetest` at
`~/.hermes/profiles/skt-livetest/`. Plugin copied from the local `main`
checkout (`1a8d3de` lineage: item-12 hardening + F1–F3 fixes). Real
`~/.hermes/skills` and real tool directories were not used as mutation
targets.

## Automated gates (repo root, same session)

| Gate | Result |
|------|--------|
| `python3 -m unittest tests.test_core_api tests.test_mcp_backend tests.test_plugin_api tests.test_v2_backend tests.test_bulk_planning tests.test_scan_wizard` | 127 OK |
| `.venv/bin/python3 -m unittest tests.test_routes_http` | 13 OK |
| `python3 tests/check_frontend.py` | pass |
| `./tests/run_render_harness.sh` | both bodies pass; zero React-key / console.error / console.warn; F1 Problems repair, F2 receipt sibling, F3 arrivals-on-Tools assertions pass |

## Isolated-profile smoke (plan §12 items that do not need the owner's desktop)

| Checklist item | Result |
|----------------|--------|
| Install from a clean plugin copy into a disposable profile | Pass. `rsync` of repo (excluding `.git`/`tests`/`docs`/`node_modules`) into `profiles/skt-livetest/plugins/skills-toggle/`. `hermes plugins install` from GitHub failed because the repo is private without non-interactive git credentials; local copy is the documented fallback. |
| Agent half enabled | Pass. `skt-livetest plugins enable skills-toggle` → `enabled user 3.0.0 skills-toggle`. Override permission declined (plugin does not need it). |
| Gateway start after enable | Pass. `skt-livetest gateway run` reached `Gateway running` with no plugin errors in `errors.log`. |
| Plugin routes mounted on real `serve` (D19 / headless-404) | Pass. `skt-livetest serve --host 127.0.0.1 --port 8792 --no-open` returned HTTP 200 on `/` and `{"detail":"Unauthorized"}` (not 404) on `/api/plugins/skills-toggle/state` and `/health`. Auth wrap is platform middleware; 401 proves the router mounted. |
| No plugin exceptions in isolated `errors.log` | Pass (only unrelated registry / credential-pool noise). |

## Not run here (owner GUI / live ResizeObserver)

These remain open on `docs/live-host-acceptance.md` because they need the
Hermes desktop window, not a headless `serve`:

1. Live ResizeObserver across narrow / split / wide (highest harness blind spot).
2. ⌘K palette atom switching on an already-mounted pane.
3. Desktop "Reload desktop plugins" + Settings → Plugins enable of the desktop half.
4. Status-chip / compact-pane click → Control Center.
5. Scan wizard, Manage, Disable all → undo, enable category, apply a named set,
   add a fixture skill, three-way conflict, Problems, MCP — in the real window.
6. Restart gateway **and** desktop; `ctx.storage` persistence; filesystem truth
   re-derived.
7. Current screenshots of the shipped shell.

Until those are ticked, do not tag a release. Item 13 packaging (README, this
record, issue template) can land without a tag.

## Defects observed during isolated smoke

- `hermes plugins install https://github.com/qwertyuiop97/skills-toggle.git` cannot clone a private repo in a non-interactive agent session. Documented install still works via local copy / `hermes plugins install qwertyuiop97/skills-toggle` when `gh` credentials are available to the user.
- `hermes dashboard` is the wrong surface for this plugin: the desktop app talks to `hermes serve`. Hitting dashboard `/api/plugins/skills-toggle/state` is not the live-host proof; `serve` is.

No unexpected mutations. No real skill tree or real tool directory was written.

## Cleanup

Isolated `gateway run` / `serve` / leftover `serve --port 0` processes were
stopped. Dummy `dashboard.basic_auth` added during an auth experiment was
removed from `profiles/skt-livetest/config.yaml`. The throwaway profile itself
is left in place for a later GUI pass; it is not the owner's daily profile.

## Re-verification run (2026-09-09, agent headless)

Owner reported the desktop half enabled in Settings → Plugins with a desktop
plugin reload. Agent-side follow-up on the same disposable profile:

- Profile copy re-synced from `main` (`a8c1023`; previously stale at the Sep-8
  smoke). `diff -rq` clean.
- Automated gates re-run green on the repo root: 127 OK
  (`test_core_api`, `test_mcp_backend`, `test_plugin_api`, `test_v2_backend`,
  `test_bulk_planning`, `test_scan_wizard`), 13 OK (`test_routes_http`),
  `check_frontend.py` pass, render + workspace harnesses pass with zero
  React-key / console.error / console.warn.
- Headless `serve` (loopback `:8792`) re-verified with the fresh copy: HTTP 200
  on `/`, 401-not-404 on `/api/plugins/skills-toggle/state` and `/health`;
  no plugin exceptions in the serve log (server stopped afterwards; one
  `<defunct>` reaper entry left to the platform — port confirmed free).
- Backend mutation exercise on fresh disposable `/tmp/skt-live` paths (real
  `~/.hermes/skills` and real tool dirs untouched):
  - no-op disable preview: `would_change == []`;
  - enable-all on 2 fixture skills: receipt `changed == 2`, symlinks point into
    the disposable Hermes tree; idempotent re-apply: `would_change == []`;
  - disable-all: `changed == 2`, links removed;
  - hermes-config disable: `config-updated`, timestamped
    `config.yaml.bak.skills-toggle.*` created, surfaced by `list_backups`
    (`count == 1`), `restore_backup` reverted it (`ok`);
  - foreign symlink + unmanaged real dir on the disposable target untouched;
  - malformed disposable `config.yaml` refused with `config-edit` (no write).

## Defects observed during re-verification

None. No unexpected mutations. GUI-only items (live ResizeObserver, palette
atom switching, click navigation, scan wizard / Manage / Disable-all / undo in
the real window, gateway+desktop restart persistence, current screenshots)
remain open on `docs/live-host-acceptance.md` for the owner window pass. No
release tag created.
