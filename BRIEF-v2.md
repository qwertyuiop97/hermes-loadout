# skills-toggle v2 brief (reasoning-pass revision)

## Context
Hermes desktop plugin (pane + Python backend), unified package at
`~/.hermes/plugins/skills-toggle/`. Source of truth: `~/.hermes/skills/`.
Toggles symlink farms for Claude (`~/.claude/skills`), Codex (`~/.codex/skills`),
OpenCode (`~/.config/opencode/skills`), Grok (`~/.grok/skills`), ZCode (detect).
Hermes self = `skills.disabled` in config.yaml. Backend routes: state, detail,
diff, toggle, toggle-bulk, repair, repair-all, ensure-tool-dir, health.
v1 done, tested (47 core + 9 HTTP + render harness), doctor green.

## v2 features (ordered by impact-for-beginners × build-cost using existing routes)

1. **Responsive layout fix** — blocker bug; pane unusable at default width, kills every other feature's UX. Uses no new backend. Ship first.
2. **Onboarding empty states** — "Choose your tools" panel when no consumer dirs exist. Wraps existing `ensure-tool-dir`. Biggest first-run cliff for beginners, near-zero build.
3. **New-skill flow prompt** — "enable where?" modal on skill arrival. Fixes silent-drop bug; reuses `toggle`/`toggle-bulk`. Huge trust win, small build.
4. **Health chip + Cmd+K report** — statusbar chip (broken/unlinked counts) → existing `health` route. Cheap, high visibility, drives repair usage.
5. **Bulk toggles** — per-skill "all on/off" across agents + per-category all-off in one compact control. Backend `toggle-bulk` already exists; pure frontend.
6. **Presets (built-in)** — named skill-sets per tool ("coding", "writing", "minimal"), one-click apply via `toggle-bulk`. Beginners' headline feature. Ship built-ins before file format (see #7).
7. **Shareable presets (export/import files)** — JSON preset schema + import UI. Merged with #6's data model; do second to avoid schema churn.
8. **More agents + auto-detect** — seed default list from Skills-Manager's 32, auto-detect installed on first run, manual per-agent enable, respect custom skills dirs (`OPENCODE_CONFIG_DIR`, etc.). Medium build; expands TAM.
9. **Auto-link per tool** — new Hermes skills auto-link into opted-in tools via reconciler over `/diff` + per-tool pref; same safety rules. Depends on #3 and #8.
10. **Import/adoption wizard** — scan tool dirs; classify copies vs symlinks vs foreign links; one-click "Unify under Hermes" (copy in, symlink out, never delete originals, diff preview required). High value for existing users, medium-high build.
11. **Drift detection** — same-name/different-hash across libraries flagged "drifted" with push-canonical-out / pull-external-in actions; periodic rescan (interval + on-focus). Needs new hash route.
12. **MCP/connectors tab** — same switchboard for MCP servers. Per-app writers (Hermes `config.yaml`, Claude Desktop `extensions`+`mcpServers`, Codex `config.toml`, OpenCode json); Hermes catalog = source. Largest build; ship after skills surface is stable.
13. **Distribution** — verify `hermes://` scheme first; demo GIF; GitHub Actions CI + badge; tag `v1.0.0`; submit to Hermes community plugin index. Last, gated on 1–9 shipping.

## Added (high-value, missing)
- **A. Undo last action (30s toast)** — every toggle/preset/wizard action reversible via timestamped config backups (already required). Cheap; massive beginner safety net.
- **B. Dry-run preview for bulk/preset/wizard** — show diff of link changes before apply. Reuses `diff`; prevents "what did I just do" panic.
- **C. Conflict resolution UI for drift + adoption** — single component (side-by-side hash/mtime/path) shared by #10 and #11 to avoid double-building.

## Cut / merged
- Original #6 "Import/adoption wizard" and #8 "Drift detection" **share a conflict-resolution component** (Added C) — build once, mount twice.
- Original #1 and #13 **merged into one preset model** (built-ins first, file format second) to avoid schema rewrite.

## Ambiguities / missing acceptance criteria (needed before autonomous coding)
- **Preset scope**: per-tool, cross-tool, or both? Does applying a preset *replace* current state or *additively* enable? Confirm.
- **New-skill flow trigger**: filesystem watcher, poll interval, or backend event? Debounce window for batch drops?
- **Auto-link pref granularity**: per-tool boolean, per-tool+per-category, or per-skill allowlist?
- **Drift rescan cadence**: on-focus, interval (what?), manual only? Notification threshold?
- **Health chip thresholds**: green/yellow/red counts? Click behavior (open pane vs. open report)?
- **Responsive breakpoints**: define narrow/medium/wide px targets; enumerate right-pane default width across host apps.
- **Agent auto-detect signals**: dir existence, binary on PATH, config file present? Order of precedence?
- **MCP writer failure mode**: partial write across 4 apps — atomic transaction or per-app commit with rollback log?
- **ZCode**: "detect" is undefined — path candidates, config format, symlink support?
- **Preset file schema**: version field, tool ID stability, unknown-skill handling on import.
- **Grok skills dir**: confirm `~/.grok/skills` is real vs. speculative.
- **"Beginners first" success metric**: define (time-to-first-enabled-skill? empty-state completion rate?).

## Constraints (non-negotiable)
Opt-in only: first run must NEVER enable skills across a user's agents
unprompted. Import/auto-link/presets all require explicit user action;
defaults are observe-and-report (diff, health) until the user applies.
Unified package layout. SDK rules: ESM, `jsx()` only, imports only
`@hermes/plugin-sdk` + `react` + `react/jsx-runtime`. Theme vars only.
Never delete sources / real dirs / foreign links. Timestamped config backups
on every write. Every destructive-adjacent action reversible via Undo (Added A).
Zero hardcoded paths (`$HERMES_HOME`, profiles, win/mac/linux). Tests for
every route + frontend render checks. Conventional commits, push to
`qwertyuiop97/skills-toggle` as you go.