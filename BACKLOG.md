# BACKLOG — work top to bottom, commit+push after each item, never stop
1. [x] DONE 2.2.0 — MCP/connectors tab: Hermes catalog + Claude Desktop writer, tests (9 backend + HTTP) + README docs. Codex/OpenCode writers deferred (D34).
2. [x] DONE — curated stdlib fallback when sys.stdlib_module_names is absent (py3.9); suite verified.
3. [x] DONE — Download preset file (Blob+anchor) and Open file… import (FileReader → paste flow); export JSON built by one shared pure builder.
4. [x] CLOSED by owner decision — Q2(b) deep link only, Q3(b) no GIF (D35/D36). Nothing to do.
5. [x] DONE — pane tab state moved to an SDK atom: ⌘K 'MCP: toggle…'/'Skills: toggle…' now switch tabs LIVE (no remount needed); storage persistence kept. Responsive rows/empty states/toasts were re-verified in the 49-assertion harness.
RULE: finishing an item means starting the next. QUESTIONS_FOR_HERMES.md for blockers without stopping.

## v3 extended backlog (owner-added, top-down after PROPOSAL-v3 items)
(a) Dogfood the machine blueprint on the Air↔Pro setup; fix what breaks (Pro-side round-trip first; Air-side is one command there).
(b) Extra agent targets — scan for Kimi, Cursor, Windsurf, Copilot, Gemini skills dirs; offer as opt-in tools.
(c) Statusbar health chip + ⌘K health report — verify shipped v2 behavior, close.
(d) Onboarding empty states (choose-your-tools panel) — verify shipped v2 behavior, close.
(e) Per-tool auto-link reconciler — verify shipped v2 behavior, close.
(f) Tag v2.2.0 + v3 releases with notes.
(g) README refresh with the MCP tab and v3 features.
