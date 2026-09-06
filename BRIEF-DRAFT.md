# skills-toggle v2 brief (draft by Hermes — needs a reasoning pass)

## Context
Hermes desktop plugin (pane + Python backend), unified package at
`~/.hermes/plugins/skills-toggle/`. Source of truth: `~/.hermes/skills/`.
Toggles symlink farms for Claude (~/.claude/skills), Codex (~/.codex/skills),
OpenCode (~/.config/opencode/skills), Grok (~/.grok/skills), ZCode (detect).
Hermes self = skills.disabled in config.yaml. Backend routes: state, detail,
diff, toggle, toggle-bulk, repair, repair-all, ensure-tool-dir, health.
v1 done, tested (47 core + 9 HTTP + render harness), doctor green.

## v2 features (all requested by owner, unordered)
1. Presets: named skill-sets per tool ("coding", "writing", "minimal"),
   one-click apply. Beginners first.
2. MCP/connectors tab: same switchboard for MCP servers across apps.
   Note: each app stores MCP differently (Hermes config.yaml, Claude
   Desktop extensions+mcpServers, Codex config.toml, OpenCode json) —
   design a per-app writer, Hermes catalog as source.
3. Bulk toggles: per-skill "all on/off" across agents + per-category
   all-off, in one clean non-bulky control.
4. More agents: default list seeded from Skills-Manager's 32; auto-detect
   installed agents on first run; manual enable toggle per agent; respect
   custom skills dirs (OPENCODE_CONFIG_DIR etc.).
5. New-skill flow: skill lands in Hermes → pane prompts "enable where?"
   instead of silent on/off. Kills missing-link bugs.
6. Import/adoption wizard: scan tool dirs, classify copies vs symlinks vs
   foreign links; one-click "Unify under Hermes" (copy in, symlink out,
   never delete originals, show diff before merge).
7. Auto-link per tool: new Hermes skills auto-link into opted-in tools.
   Reconciler over /diff + per-tool pref; same safety rules.
8. Drift detection: same name + different hash across libraries = "drifted"
   with push-canonical-outward / pull-external-in. Periodic rescan.
9. Health chip: statusbar chip (broken links / unlinked count) + Cmd+K
   health report.
10. Responsive layout fix (reported bug): rows currently force a very wide
    pane to see all toggles. Redesign rows to wrap/stack at narrow widths;
    pane must be fully usable at default right-pane width.
11. Distribution: install deep-link (verify hermes:// scheme first), demo
    GIF, GitHub Actions CI + badge, tag v1.0.0, submit to Hermes community
    plugin index.
12. Onboarding empty states: "Choose your tools" panel when no consumer
    dirs exist (backend ensure-tool-dir already exists).
13. Shareable presets as files (export/import).

## Constraints (non-negotiable)
Unified package layout. SDK rules: ESM, jsx() only, imports only
@hermes/plugin-sdk + react + react/jsx-runtime. Theme vars only.
Never delete sources/real dirs/foreign links. Timestamped config backups.
Zero hardcoded paths ($HERMES_HOME, profiles, win/mac/linux). Tests for
every route + frontend checks. Conventional commits, push to
qwertyuiop97/skills-toggle as you go.
