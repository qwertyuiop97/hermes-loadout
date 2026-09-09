# Live-host acceptance checklist — skills-toggle

Backlog item 6 deliverable. This checklist proves the plugin inside a **real
installed Hermes** (gateway + desktop app), exercising integration the
`renderToString` harness in `tests/render_harness_body.mjs` cannot: the actual
split-window layout, live `ResizeObserver` measurement, palette-driven atom
switches on a mounted pane, socket/query invalidation, gateway plugin-route
mounting, and persistence across restart.

Render-to-string harness coverage is **documented, not duplicated**, here — every
item below is a case the static harness is known NOT to prove.

## Ground rules (safety)

- All mutations run against **disposable fixture paths**, never the owner's real
  skill tree (`~/.hermes/skills`) or real tool directories. Use a throwaway
  `HERMES_HOME` fixture (e.g. `/tmp/skt-live/skills`) and fake tool targets.
- Never overwrite or delete real directories, foreign links, or skill sources
  (product decisions 6–7).
- Record every terminal intervention, ambiguous label, clipped control, and
  unexpected mutation as a defect.

## Setup / install

- [x] Install from a clean plugin copy into a disposable `HERMES_HOME`:
  `~/.hermes/plugins/skills-toggle/{plugin.yaml, dashboard/{manifest.json, plugin_api.py}, desktop/plugin.js}`.
  (2026-09-08 isolated profile `skt-livetest`; local copy — private-repo CLI
  install is not non-interactive. See `docs/dogfood-record.md`.)
- [ ] Both halves enabled: agent half in `plugins.enabled`, desktop half in
  Settings → Plugins (ships `defaultEnabled: false`).
  Agent half: [x] `skt-livetest plugins enable skills-toggle`. Desktop half: still needs the owner's window.
- [x] **Gateway restart** performed after first install (plugin API routes are
  mounted at gateway startup only — see D19). Headless `serve` on loopback
  returned 401 (not 404) on `/api/plugins/skills-toggle/state` and `/health`.
- [ ] Desktop "Reload desktop plugins" (⌘K) applied so the desktop half is live.

## Entry points

- [ ] Status-chip / compact-pane Problems shortcut navigates to Control Center.
- [ ] ⌘K `Skills: toggle…` opens Control Center **Tools**.
- [ ] ⌘K `Skills: health report` opens Control Center **Problems**.
- [ ] ⌘K `MCP: toggle…` opens Control Center **MCP** while Control Center is already
  mounted (in-app section atom switch via `ccSectionAtom`, not a pane remount).
- [ ] ⌘K `Skills: toggle…` switches back to **Tools** live.

## Split-window / compact pane (the supplied screenshot shape)

- [ ] Pane renders at its default 320 px right-side placement.
- [ ] At split width the pane is **fully usable**: no horizontal overflow, no
  clipped or unreadable tool switches, no six-switch-per-skill rows that force
  hunting.
- [ ] Resize the pane continuously across narrow/medium/wide; observe the
  container-measured breakpoints switch correctly (ResizeObserver live, not the
  SSR default). **This is the single most important case the render harness
  cannot test** (SSR never runs the ResizeObserver effect).
- [ ] No tool is ever silently hidden at any width (release gate: "every
  detected tool is reachable even when the matrix cannot fit").

## Data flow / live truth

- [ ] `/state`, `/diff`, `/drift`, `/mcp` REST round-trips succeed from the real
  gateway (not the stub). Route *mount* proved on isolated `serve` (401-not-404);
  authenticated round-trips still need a desktop session or a documented token.
- [ ] A mutation performed in the pane reflects on a second surface or after
  refresh (React Query invalidation + server cache generation bump, D16).
- [ ] Optimistic toggle rollback: force a mutation to fail and confirm the switch
  reverts visibly.

## Mutations (disposable fixtures only)

- [ ] Run one **no-op preview** (idempotent) and confirm zero changes.
- [ ] Run one **reversible mutation** (e.g. enable one category on a disposable
  tool target), confirm the receipt, then **undo** and confirm it restores only
  that receipt's changes.
- [ ] Protected entries are visibly refused and never overwritten.
- [ ] Backup files are created before each config/mutation write and a restore
  path is reachable.

## Persistence across restart

- [ ] Set a UI preference (e.g. pane tab, view filter, auto-link opt-in).
- [ ] Restart gateway **and** desktop.
- [ ] UI preference persisted (via `ctx.storage`, not DOM/localStorage).
- [ ] Filesystem truth re-derived fresh from backend state (not a stale cache);
  config membership and symlink state match reality after restart.

## MCP (Flow F)

- [ ] Hermes / Claude / Codex columns unambiguous; drift requires explicit
  overwrite confirmation.
- [ ] Secret env values never appear in UI state, receipts, logs, or the
  mutation log (mutation-log redaction, D14).
- [ ] Foreign servers remain visible but untouched.

## Known harness blind spots — verify here, in order of risk

1. Split-window layout and live resize (ResizeObserver). **Highest risk.**
2. Palette-command live atom switching on an already-mounted pane.
3. Gateway plugin-route mounting (headless-404 remedy) after a real restart.
4. Socket-driven query invalidation and server cache generation bumps.
5. `ctx.storage` persistence across a real restart (SSR harness can't restart).
6. Filesystem truth after mutations (config.yaml surgical edits, symlinks).

## Solo-validation run-through (plan §12)

Repeat the above at **narrow, split, and wide** sizes:
1. complete the scan wizard; 2. manage a tool from the split workspace;
3. preview and disable all for a disposable tool target; 4. undo it;
5. enable one category; 6. apply a named set; 7. add and distribute a new
fixture skill; 8. resolve a fixture conflict three ways; 9. inspect Problems and
MCP; 10. restart gateway and desktop.

Every terminal intervention, ambiguous label, clipped control, and unexpected
mutation is a **defect** to record — not a cosmetic nit.
