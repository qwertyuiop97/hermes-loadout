# Standing orders (from the owner via Hermes — obey alongside the prompt brief)
- BACKLOG.md in the repo root is the work queue: work it top to bottom, finishing an item means starting the next. Commit and push after every item.
- `IMPLEMENTATION_PLAN.md` is the authoritative specification for the active stabilization queue. Historical briefs and proposals remain context, not current scope.
- `PRODUCT_DIRECTION.md` defines the repository boundary. `skills-toggle` is active; sibling `skills-dash` is a parked, read-only reference unless the owner explicitly reactivates it.
- test_core_api.py must pass on python 3.9 (no sys.stdlib_module_names — fallback set).
- Work autonomously on routine implementation choices. Resolve ambiguity from the plan, code, tests, and verified Hermes SDK behavior; use configured bots or an independent review when useful, but do not require an MOA consultation.
- Ask the owner only when an unresolved choice would materially change product behavior, the canonical storage model, backward compatibility/public APIs, safety or data-loss boundaries, permissions/secrets/external publishing, or would require an irreversible/destructive action.
- Never stop for a non-blocking question. Record genuine blockers in QUESTIONS_FOR_HERMES.md and continue every safe, independent backlog task.
