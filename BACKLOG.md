# BACKLOG — active work queue

Work top to bottom. Commit and push after each completed item, then immediately
start the next. Put genuine blockers in `QUESTIONS_FOR_HERMES.md` without
pausing safe independent work.

## Active product stabilization queue (start here)

The authoritative implementation specification is `IMPLEMENTATION_PLAN.md`.
Complete these items in order. Do not start new targets or configuration writers
until items 6-12 pass their acceptance gates.

6. [x] Baseline, product reconciliation, and live-host reproduction — preserve
   the current screenshot-shaped fixture, document current Hermes tile behavior,
   inventory reusable UX/test/MCP ideas from the parked `skills-dash` repository,
   and add a live-host acceptance checklist that catches failures the
   render-to-string harness cannot. Do not resume or merge Skills Dash.
7. [ ] Responsive product shell — make the contributed side pane a compact summary;
   make the route/workspace surface the management UI; support narrow, split, and
   wide containers without hidden tools, clipped descriptions, or ambiguous switches.
8. [ ] Tool-first overview — default to one card/row per detected tool with enabled,
   disabled, and issue counts plus Manage, Enable all, and Disable all actions.
9. [ ] Single-tool management + bulk planning — one switch per skill for the selected
   tool; selection/category/all operations; dry-run preview, protected-entry report,
   confirmation, mutation receipt, and undo.
10. [ ] First-run scan wizard — promote the existing import/adoption scan into guided
    onboarding; auto-detect known folders, accept additional scan folders, classify
    duplicates/conflicts, keep Hermes as the canonical destination, and apply only
    after a reviewed dry run.
11. [ ] Information architecture cleanup — primary sections Tools, Sets, Problems,
    and MCP; move blueprints, backups, watch mode, custom paths, and auto-link regexes
    under Advanced; keep an expert Matrix view only for sufficiently wide containers.
12. [ ] Performance, accessibility, and safety gates — validate 100+ skills, keyboard
    use, focus/labels, reduced motion, atomic/rollback behavior, Python 3.9 core
    compatibility, HTTP routes, and installed-Hermes smoke behavior.
13. [ ] Alpha packaging and solo validation — execute the repeatable dogfood script,
    update README/screenshots/install instructions, tag only after every release gate
    in `IMPLEMENTATION_PLAN.md` passes, and record outside-user feedback when available.

## Deferred until the active queue passes

- [ ] OpenCode MCP writer: resolve Q6 in `QUESTIONS_FOR_HERMES.md`; do not let
  this block stabilization.
- [ ] Tag the next release only as item 13, after every release gate passes.

## Completed history

1. [x] DONE 2.2.0 — MCP/connectors tab: Hermes catalog + Claude Desktop writer, tests (9 backend + HTTP) + README docs. Codex/OpenCode writers deferred (D34).
2. [x] DONE — curated stdlib fallback when sys.stdlib_module_names is absent (py3.9); suite verified.
3. [x] DONE — Download preset file (Blob+anchor) and Open file… import (FileReader → paste flow); export JSON built by one shared pure builder.
4. [x] CLOSED by owner decision — Q2(b) deep link only, Q3(b) no GIF (D35/D36). Nothing to do.
5. [x] DONE — pane tab state moved to an SDK atom: ⌘K 'MCP: toggle…'/'Skills: toggle…' now switch tabs LIVE (no remount needed); storage persistence kept. Responsive rows/empty states/toasts were re-verified in the 49-assertion harness.

### v3 extended backlog (owner-added)

(a) [x] Pro-side dogfood complete — exported 202-link blueprint (grok 64/codex 62/claude 39/opencode 37, 33 hermes-off); dry-run self-apply = empty plan (idempotent); apply = no-op. Blueprint saved at /tmp/skills-toggle-blueprint-pro.json for the Air (unreachable from this session — one 'Apply blueprint' there finishes the loop).
(b) [x] SHIPPED (v3) — scan for Kimi, Cursor, Windsurf, Copilot, Gemini skills dirs; offer as opt-in tools.
(c) [x] SHIPPED (v2) + verified — statusbar chip (broken/unlinked counts) + ⌘K 'Skills: health report'; harness-asserted.
(d) [x] SHIPPED (v2) + verified — choose-your-tools panel, custom-tool add, auto-link prefs, adoption scanner; harness-asserted.
(e) [x] SHIPPED (v2) + verified — per-tool opt-in reconciler, now with per-category regex patterns (v3-7).
(f) Moved to active stabilization item 13; no earlier tag.
(g) [x] DONE — README refreshed with the MCP tab and v3 features; revise again for the stabilized UX in item 13.
