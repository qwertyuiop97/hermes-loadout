# Prompt to give Hermes

Work in `/Users/bakirmousa/projects/skills-toggle` and execute the active product
stabilization queue in `BACKLOG.md` from top to bottom. The full authoritative
specification and acceptance criteria are in `IMPLEMENTATION_PLAN.md`. Read both
files completely, plus `AGENTS.md`, `DECISIONS.md`, and the relevant current
code/tests before dispatching work.

Act as the orchestrator. It is acceptable for the main session to use a fast
model such as DeepSeek Flash. Do not change my Hermes model/provider/MOA/
fallback/delegation configuration. Decide when bots are useful; do not require
an MOA consultation or ask the owner whether to delegate. Use the bots already
available to you and select them by capability rather than assuming a specific
model identifier:

- strongest reasoning bot for UX architecture and Hermes SDK compatibility;
- strong React/coding bot for frontend work;
- strong Python/filesystem-safety bot for backend work;
- lower-cost reliable coding bot for mechanical fixtures/tests;
- strong independent reviewer, preferably a different model from the
  implementer, before each item is accepted.

Use Grok 4.6 through the already configured bot/model routing as an explicit
independent reviewer. Give it the committed screenshot-shaped baseline fixture,
the observed layout evidence recorded in item 6, the relevant section of
`IMPLEMENTATION_PLAN.md`, and the relevant diff. Ask it to challenge the proposed
information architecture, identify controls that remain confusing or hidden at
split-window width, and look for unsafe filesystem/configuration edge cases. Use
it at least once before accepting the responsive shell/tool-first design and
again before the final hardening item is accepted. Grok 4.6 may propose changes,
but the orchestrator must verify its findings against the code, Hermes SDK
documentation, and tests before applying them. Do not change the global Hermes
configuration to accomplish this; use the existing Grok 4.6 bot route.

Operate autonomously. Do not ask the owner about routine implementation choices,
wording, spacing, component boundaries, test structure, worker selection, or
whether to proceed to the next backlog item. Resolve ordinary ambiguity from the
authoritative plan, current code, tests, recorded decisions, and verified Hermes
SDK behavior. When bots disagree, inspect their evidence and make the decision;
disagreement alone is not a reason to contact the owner.

Contact the owner only when an unresolved choice would materially change one or
more of the following: intended product behavior, Hermes as the canonical skill
store, backward compatibility or a public API, filesystem safety or data-loss
risk, required permissions or secrets, external publication/release authority,
or an irreversible/destructive action. Before escalating, exhaust safe read-only
checks and available independent review. State the exact decision, evidence,
options, recommended default, and what work can continue. Record it in
`QUESTIONS_FOR_HERMES.md`, then continue every safe independent task. Do not wait
for an answer unless no safe in-scope work remains.

Use tokens efficiently without lowering quality. Token efficiency means avoiding
duplicated work and unnecessary context, not skipping investigation, tests,
review, or acceptance criteria. Apply these rules:

- keep the main model focused on orchestration, integration decisions, diff
  inspection, and acceptance; delegate bounded implementation or investigation;
- give each bot only the relevant plan section, owned files, known evidence, and
  required output contract instead of pasting the entire conversation;
- do not ask multiple bots to independently rediscover the same repository facts
  unless an independent review is intentionally required;
- use strong reasoning models for architecture, difficult debugging, safety, and
  final review; use reliable lower-cost bots for mechanical tests, fixtures,
  documentation synchronization, and narrow edits;
- batch independent read-only tasks in one delegation call, but serialize tasks
  that depend on earlier results or touch the same files;
- run the smallest relevant tests during iteration, then run the complete required
  suites once at the backlog item's acceptance gate; rerun full suites only after
  relevant changes or a failure;
- reference repository files and exact plan headings instead of repeatedly
  restating long specifications;
- require concise structured bot reports and do not request chain-of-thought,
  repeated summaries, or speculative essays;
- do not poll running bots or repeat unchanged status checks; continue independent
  work and consume each result once;
- reuse verified findings and test evidence within the same backlog item, but
  re-verify anything that may have changed after subsequent edits;
- never trade away correctness, filesystem safety, accessibility, live-Hermes
  verification, or independent review merely to reduce token use.

Use no more than three workers concurrently. Parallelize only read-only analysis
or non-overlapping file ownership. Never allow two bots to edit
`desktop/plugin.js` or `dashboard/plugin_api.py` concurrently in the same
checkout. Use isolated worktrees when useful; otherwise serialize edits.

For every delegation, pass the repository path, exact owned files, relevant
acceptance criteria, test commands, and a prohibition on unrelated edits.
Require the bot to return: findings, files changed, tests run with results,
remaining risks, and commit hash. Inspect every diff and the actual test output;
do not accept self-reported completion.

Preserve these product decisions:

1. Hermes remains the canonical skill library.
2. Additional folders are scan/import sources or configured tool targets, not
   alternate canonical stores in this release.
3. The default UX is tool-first.
4. The compact pane is a summary; the workspace Control Center contains full
   management.
5. The matrix is an optional wide-screen expert view and must never silently
   omit tools.
6. Every bulk change requires preview, confirmation, per-item results, and
   precise undo or restore guidance.
7. Never overwrite or delete real directories, foreign links, or skill sources.
8. Preserve stable backend APIs and Python 3.9 core compatibility.
9. Do not add agent targets or MCP writers until stabilization items 6-12 pass.

Start with backlog item 6. Commit and push after every completed backlog item.
Finishing one item means starting the next. Put genuine blockers and unresolved
decisions in `QUESTIONS_FOR_HERMES.md` without pausing work that can proceed.
The standing order already authorizes these scoped commits and pushes; do not ask
for confirmation before each one. It does not authorize changing global Hermes
configuration, credentials, the owner's live skill directories, or publishing a
release early. Do not release or tag until every release gate in
`IMPLEMENTATION_PLAN.md` passes. Continue until the active stabilization queue is
complete or no safe, in-scope work remains.
