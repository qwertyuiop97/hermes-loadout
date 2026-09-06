# PROPOSAL-v3.md — skills-toggle v3 (proposal only, awaiting approval)

Status: **APPROVED 2026-09-06** — ship 1, 2, 4, 5, 6 first, then 3, 7, 8.
Owner decisions on the four questions: blueprint apply is ADDITIVE-ONLY
(removals stay manual); watch mode = native notifications when unfocused with
per-class toggles; MCP Codex/OpenCode proceeds on the spike without a
go/no-go stop. No code was in this document; implementation follows the order
below (this file stays the record of what was approved).

## Positioning

v2 made skills-toggle the **control surface**: one pane where skills and MCP
servers are switched per tool, with adoption, drift detection, presets, and
undo. v3's theme is closing the loop: **resolve everything the pane can see,
safely reach the second machine, and finish the switchboard** — plus making the
safety story (backups, restore) a first-class UI instead of files you know are
there.

## Proposed features (priority order)

### 1. Conflict resolution, completed (Added C + D31 finish) — size M
v2 detects drift and adoption conflicts and can push-canonical-out. v3
completes the loop with one shared conflict view (side-by-side: paths, mtimes,
SKILL.md hashes) and three explicit actions per conflict:
- **Use Hermes** (exists today) — backup tool copy, link canonical.
- **Use tool copy** — external version is written into the skills tree
  (category `imported/` when the name is free; same-name replacement requires
  a second confirm and keeps a tree-side backup).
- **Keep both** — tool copy adopted under a suffixed name.
Why first: it's the only v2 surface that still ends in "resolve manually", and
it unblocks #2 and #3 below. Backend is small (one `pull` route + tree-side
backup rule); the view is the work.

### 2. Machine blueprint — export/apply the whole link map — size M
A superset of presets: one JSON describing **every** skill→tool link plus the
`skills.disabled` set. "Export blueprint" on the Pro, "Apply blueprint" on the
Air (or a fresh machine): creates missing tool dirs, links everything, applies
Hermes-offs — additive and dry-run-first, with the usual per-item refusals.
This is the Air↔Pro story as one click instead of a checklist, and it's the
strongest "fresh machine" feature for new users.
Includes a **blueprint diff** before apply: what will be created / turned off,
counted and sampled (dry-run B semantics).

### 3. MCP writers: Codex + OpenCode — size L (research-heavy)
Completes the switchboard per D34's deferral. Codex writes `~/.codex/config.toml`
(`[mcp_servers.<name>]` TOML tables — needs a comment-preserving surgical TOML
editor or full-file regeneration with explicit comment-loss warning; stdlib has
no TOML writer). OpenCode writes its json (`mcp` key — schema must be verified
against a real install first). Same model as Claude Desktop: catalog
projection, drifted confirm, foreign never-touched, timestamped backups.
Gated on a schema-verification pass against real installs before any code.

### 4. Adoption + drift undo — size S
v2 undo covers toggles/presets/arrivals. v3 extends the 30s undo banner to
adoption and drift-push by making the inverse a real backend operation
(restore the backup-renamed dir, remove the symlink) instead of "recover
manually from the backup path". Safety story becomes fully symmetric.

### 5. Watch mode (opt-in) — size M
A pane toggle: when on, the existing state/diff polls compare against a
snapshot and fire a **native notification** (ctx.os.notify — fires only when
the app is unfocused, throttled) when new skills arrive, links break, or drift
appears. Turns the pane from something you check into something that taps your
shoulder. Strictly opt-in, off by default, per-event-class toggles.

### 6. Backup browser + restore — size S
A Setup-panel section listing every backup the plugin ever created
(`config.yaml.bak.skills-toggle.*`, `skills-toggle.json.bak.*`,
`*.skills-toggle-backup-*`) with content preview and one-click **Restore**
(restore itself backs up the current state first — belt and braces). Closes
the loop on "every destructive-adjacent action is reversible" by making the
reversal visible instead of implied.

### 7. Per-category auto-link — size S
D23's deferred upgrade: auto-link preference becomes per tool **and** per
category pattern (e.g. auto-link `creative/*` to Claude only). Storage schema
stays backward-compatible (boolean prefs keep working).

### 8. Windows support pass — size M
Symlink behavior on Windows (developer-mode / junction fallback), path
candidates verified, CI matrix extended to windows-latest for the backend
suite. Honest README support matrix. (Pane is untestable on CI; backend is.)

## Non-goals for v3 (unless you say otherwise)

- Skill **content editing/creation** — Hermes core owns that surface.
- Cross-machine **sync transports** (git/icloud/ssh) — blueprint files ride
  whatever the user already uses; we stay file-based.
- Telemetry/analytics of any kind.
- Codex/OpenCode MCP **spec-creation** — we mirror, we don't define schemas.

## Suggested ship order

1 → 4 → 6 (conflict loop + undo + backups = one "safety" release, ~1 session)
then 2 → 5 ("multi-machine + watch" release) then 3 → 7 → 8 (switchboard
completion + Windows). Each release ships tagged and CI-green.

## Decisions I need from you

- [ ] Approve all 8? Strike any? (My default: ship 1, 2, 4, 5, 6 first.)
- [ ] #2 blueprint: should applying a blueprint also **remove** links that
  aren't in it (full replace, dangerous) — or additive-only like presets?
  Default: additive-only, removals stay manual.
- [ ] #5 watch mode: native notifications OK, or in-pane banner only?
  Default: native when unfocused, per-class toggles.
- [ ] #3 MCP: proceed on Codex/OpenCode with a schema-verification spike
  first, or hold until you confirm you actually use MCP there?
  Default: spike, then stop for your go/no-go.
