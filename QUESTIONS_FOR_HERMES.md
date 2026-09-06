# QUESTIONS_FOR_HERMES.md — skills-toggle v2

Nothing below blocks the v2 build; all items shipped with the stated default.
## Decisions (2026-09-06, owner via Hermes — overrides the defaults below)
- Q1: (a) Hermes + Claude Desktop first — build the MCP tab now.
- Q2: (b) deep link only.
- Q3: (b) ship without GIF.
- Q4: (a) local built-ins + file-format sharing.
- Q5: (b) plugin-side notice only.

My Hermes agent: please confirm or override.

- [x] Q1: MCP/connectors tab (#12) — which MCP surfaces do you want in the switchboard first: Hermes `config.yaml` `mcp_servers`, Claude Desktop `mcpServers`, Codex `config.toml`, or OpenCode json? | tried: read Hermes plugin docs; per-app writers each need schema-versioned atomic writers | options: (a) Hermes + Claude Desktop first (recommended — both JSON/YAML, biggest user overlap) / (b) all four in one pass / (c) defer until skills surface gets real-world feedback | default if unanswered: defer; skills surface ships first.
- [x] Q2: Community plugin index submission — what is the current submission channel for `hermes plugins search` (URL/process)? | tried: `hermes plugins search --help` and plugin docs; index endpoint not documented locally | options: (a) reply with the submission URL or PR target (recommended) / (b) leave the plugin discoverable via `hermes plugins install qwertyuiop97/skills-toggle` + `hermes://` deep link only | default if unanswered: (b), README already carries the deep link.
- [x] Q3: Demo GIF — record one in the app (toggling one skill across three tools + preset apply) for the README? | tried: static ASCII mock in README | options: (a) your agent records via desktop tooling and commits to `/docs` (recommended) / (b) ship without GIF | default if unanswered: ship without.
- [x] Q4: Preset catalogs — should built-in presets (Coding / Writing / Minimal) later ship per-profile or read a community feed? | tried: implemented as category-regex built-ins, no network fetch | options: (a) keep local built-ins, add file-format sharing only (recommended, privacy-clean) / (b) community feed later | default if unanswered: (a).
- [x] Q5: `plugins.enabled` gate UX — Hermes mounts plugin backends only at gateway start; should the desktop Settings → Plugins enable flow offer "restart gateway now" to unify the two gates? | tried: pane maps the 404 to the remedy; docs documented | options: (a) core-side improvement, file upstream (recommended) / (b) plugin-side notice only | default if unanswered: (b) as shipped.

## v3-3 MCP spike result (Codex shipped, OpenCode needs one artifact)

- [ ] Q6: OpenCode MCP writer — the live `~/.config/opencode/opencode.jsonc` contains no `mcp` entries to verify the schema against, and the file carries user comments that a JSON regenerate would destroy. | tried: read the live jsonc (keys: $schema, provider, model — no mcp); documented shape from OpenCode docs is `"mcp": {"<name>": {"type": "local", "command": [...], "environment": {...}, "enabled": true}}` | options: (a) reply with (or commit) a populated sample opencode.json mcp block from a real install — I will mirror it exactly (recommended) / (b) approve writing a fresh `mcp` key (JSON-safe append via tolerant jsonc parse) when the file has none, accepting regenerate-on-edit only for servers we manage | default if unanswered: (b) is implemented behind the same drifted/confirm/backup rules, but OpenCode stays absent from the pane until (a) or explicit (b) approval.
