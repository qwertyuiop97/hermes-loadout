# BACKLOG — work top to bottom, commit+push after each item, never stop
1. [x] DONE 2.2.0 — MCP/connectors tab: Hermes catalog + Claude Desktop writer, tests (9 backend + HTTP) + README docs. Codex/OpenCode writers deferred (D34).
2. [x] DONE — curated stdlib fallback when sys.stdlib_module_names is absent (py3.9); suite verified.
3. [x] DONE — Download preset file (Blob+anchor) and Open file… import (FileReader → paste flow); export JSON built by one shared pure builder.
4. [x] CLOSED by owner decision — Q2(b) deep link only, Q3(b) no GIF (D35/D36). Nothing to do.
5. [x] DONE — pane tab state moved to an SDK atom: ⌘K 'MCP: toggle…'/'Skills: toggle…' now switch tabs LIVE (no remount needed); storage persistence kept. Responsive rows/empty states/toasts were re-verified in the 49-assertion harness.
RULE: finishing an item means starting the next. QUESTIONS_FOR_HERMES.md for blockers without stopping.

## v3 extended backlog (owner-added, top-down after PROPOSAL-v3 items)
(a) [x] Pro-side dogfood complete — exported 202-link blueprint (grok 64/codex 62/claude 39/opencode 37, 33 hermes-off); dry-run self-apply = empty plan (idempotent); apply = no-op. Blueprint saved at /tmp/skills-toggle-blueprint-pro.json for the Air (unreachable from this session — one 'Apply blueprint' there finishes the loop).
(b) Extra agent targets — scan for Kimi, Cursor, Windsurf, Copilot, Gemini skills dirs; offer as opt-in tools.
(c) [x] SHIPPED (v2) + verified — statusbar chip (broken/unlinked counts) + ⌘K 'Skills: health report'; harness-asserted.
(d) [x] SHIPPED (v2) + verified — choose-your-tools panel, custom-tool add, auto-link prefs, adoption scanner; harness-asserted.
(e) [x] SHIPPED (v2) + verified — per-tool opt-in reconciler, now with per-category regex patterns (v3-7).
(f) Tag v2.2.0 + v3 releases with notes.
(g) README refresh with the MCP tab and v3 features.
