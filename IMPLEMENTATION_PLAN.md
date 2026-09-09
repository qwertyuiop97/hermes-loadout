# Skills Toggle Product Stabilization and UX Plan

Status: approved planning brief for Hermes orchestration.  
Repository: `/Users/bakirmousa/projects/skills-toggle`  
Authority: newest owner instruction, `AGENTS.md`, `PRODUCT_DIRECTION.md`,
`BACKLOG.md`, this plan, then existing proposal/decision documents.

## 0. Product boundary

This plan applies to `skills-toggle`, the Hermes-native plugin. The sibling
`/Users/bakirmousa/projects/skills-dash` repository is a standalone Tauri
prototype covering much of the same behavior. It is parked during this program,
not a second active implementation target.

During phase 6, inspect Skills Dash as read-only reference material for useful
full-window UX, import, bulk, MCP, test, and research patterns. Port only ideas
that improve the Hermes-native product and verify them against the current
skills-toggle core and Hermes SDK. Do not copy its vanilla-JS UI or older
vendored Python core wholesale, do not commit changes there as part of this
queue, and do not extract a shared package during stabilization.

The standalone product may be reconsidered after the plugin alpha is usable and
there is evidence that Hermes users need management while Hermes is closed, or
that a separate non-Hermes audience exists. `PRODUCT_DIRECTION.md` is the
authoritative explanation of this boundary.

## 1. Outcome

Turn the current feature-rich prototype into a usable Hermes-native control
center for distributing Hermes skills and MCP server definitions to other AI
tools.

The primary promise is:

> From inside Hermes, see what every detected AI tool has, enable or disable a
> whole tool/category/selection safely, import existing skills into one canonical
> Hermes library, and resolve drift without editing files manually.

This is not a generic skill marketplace and not a replacement for Hermes's
built-in skill editor or hub. Hermes remains the home base and canonical skills
library. Other folders are scan/import sources and deployment targets.

## 2. Observed product problems

The live screenshot with 105 skills shows the product route in a split Hermes
workspace. The current UI assumes more width than the container provides:

- individual rows omit tool columns that remain present in the top filter;
- tool labels and switches are difficult to associate;
- descriptions clip;
- six-tool rows are unsuitable for a narrow or half-window surface;
- new-skill controls, presets, filters, and setup dominate the first viewport;
- users must scroll through the catalog for common whole-tool operations;
- bulk actions exist but are conditional and difficult to discover;
- advanced maintenance features compete with daily management;
- the static render harness does not prove integration inside a real Hermes
  layout.

Treat these as product defects, not cosmetic polish.

## 3. Product decisions

1. Hermes stays canonical. Do not redesign the storage engine around arbitrary
   canonical roots during this program.
2. Users may add arbitrary folders as scan/import sources and custom tool
   targets. Importing copies content into the Hermes tree and then links the
   target safely.
3. The default mental model is tool-first, not a skill-by-tool matrix.
4. The matrix remains an expert view on wide containers only.
5. The narrow pane is a summary/launcher, not the complete management UI.
6. Every bulk mutation has preview, confirmation, per-item result, and undo or
   restore guidance.
7. Foreign links and real directories remain protected. Never overwrite or
   delete them automatically.
8. Existing stable backend APIs remain backward compatible. Add APIs; do not
   rename or remove exported surface members without an explicit migration.
9. Do not add more agent targets or MCP writers until the core UX release gates
   pass.

## 4. Target information architecture

### 4.1 Compact pane

The contributed right/left pane must work at approximately 280-420 px. It shows:

- total skills and detected tools;
- enabled counts by tool in compact rows;
- problem counts: broken, drifted, foreign/unmanaged;
- at most three quick actions: Open Control Center, Scan, Problems;
- loading, backend-unavailable, and retry states.

It must not render six switches per skill.

### 4.2 Control Center workspace

Primary navigation:

1. **Tools** — default daily view.
2. **Sets** — named reusable distributions/presets.
3. **Problems** — drift, broken links, conflicts, and protected entries.
4. **MCP** — Hermes catalog projected into supported clients.
5. **Advanced** — blueprints, backups, watch mode, paths, auto-link rules.

An optional **Matrix** view can appear under Tools when the container is wide
enough. It is not the default.

### 4.3 Tools overview

One card or row per present/configured tool:

- label and detected path;
- enabled / total count;
- off count;
- problem count;
- Manage;
- Enable all;
- Disable all;
- optional overflow menu for path/settings.

Optional targets that are neither configured nor detected remain in Add Tool,
not the daily list.

### 4.4 Single-tool view

When a tool is selected:

- one switch per skill, never a multi-tool row;
- search;
- category filter/collapse;
- All / Enabled / Off / Issues views;
- row selection with Select all visible;
- sticky action bar for Enable selected / Disable selected;
- Enable all / Disable all at page level;
- category-level Enable category / Disable category;
- counts update after mutation receipts, not optimistic state alone.

For 100+ skills, use collapsed categories and/or windowed/paged rendering that
does not require a new unsupported SDK import. Preserve search across all data.

### 4.5 Expert matrix

Only render the matrix when there is sufficient width. Requirements:

- sticky skill-name column;
- sticky tool headers;
- explicit horizontal scrolling when necessary;
- no silently omitted tools;
- compact mode with descriptions hidden by default;
- accessible switch labels containing both skill and tool names.

## 5. User workflows and acceptance criteria

### Flow A: first run

1. Detect known tool skill directories.
2. Offer Add folder for another scan source.
3. Run the existing import scan across all selected locations.
4. Classify managed links, unique copies, identical duplicates, drifted copies,
   name conflicts, broken links, foreign links, and unmanaged entries.
5. Explain that Hermes will be canonical and that originals are backed up.
6. Let the user choose tools to manage and entries to adopt.
7. Show a dry-run plan with counts, examples, paths, and refusals.
8. Apply only after confirmation.
9. Show a durable receipt and an Undo/Restore path.
10. Land on the Tools overview.

Acceptance:

- zero manual directory creation for detected tools;
- no filesystem knowledge required;
- existing real directories and foreign links never overwritten;
- interrupted or failed adoption restores the original state;
- first useful managed link can be created in under two minutes.

### Flow B: disable a whole tool

1. On Tools, press Disable all for Claude (or another tool).
2. Preview changed, already-off, and refused/protected counts with examples.
3. Confirm.
4. Apply through the backend bulk planner/executor.
5. Show the receipt and Undo.

Acceptance:

- no catalog scrolling;
- Hermes sources remain untouched;
- unmanaged and foreign entries are skipped and named;
- a second identical operation is a no-op;
- Undo restores only changes from that receipt.

### Flow C: enable one category or selection

Acceptance:

- category and selection scopes are visually explicit;
- preview and mutation operate on the same immutable skill-id set;
- unknown or vanished skills are reported, not silently substituted;
- partial failure is visible per item.

### Flow D: add a new skill

Acceptance:

- the arrival banner is compact and does not block normal management;
- user can choose target tools once, select all detected tools, or ignore;
- existing opt-in auto-link rules continue to work;
- notifications remain opt-in.

### Flow E: resolve a conflict

Acceptance:

- Problems shows hashes/paths/timestamps and explains Use Hermes, Use tool
  copy, and Keep both;
- every action creates the documented backups;
- backend rollback tests cover failure between every destructive step;
- the UI links the mutation receipt to the relevant backup/undo action.

### Flow F: manage MCP projections

Acceptance:

- Hermes, Claude Desktop, and Codex columns are unambiguous;
- drift requires an explicit overwrite confirmation;
- secret environment values never enter state payloads or logs;
- foreign servers remain visible but untouched;
- OpenCode remains deferred until its live schema and comment-preserving writer
  behavior are verified.

## 6. Backend work

Preserve `SkillsToggleCore`, `McpCore`, existing routes, and existing stable
exports. Implement the smallest additions needed for trustworthy bulk UX.

### 6.1 Bulk plan and receipt

Add a pure planning function and route that accept:

- tool id;
- desired enabled boolean;
- explicit skill-id list produced by the UI selection/filter;
- optional request/receipt id.

The plan returns:

- would_change;
- already_satisfied;
- refused with code/reason;
- ordered explicit skill ids;
- sample display data;
- totals.

Execution must accept the exact planned skill ids rather than recomputing a
category or filter after confirmation. Return per-item results and a receipt
that can drive Undo. Reuse existing toggle safety checks and mutation logging.

Do not promise database-style atomicity across independent filesystem entries.
Instead, make partial outcomes explicit and make Undo precise.

### 6.2 Scan sources

Extend the existing import scanner only as needed to accept owner-selected,
validated scan roots. Requirements:

- paths are expanded/canonicalized with existing helpers;
- roots must be directories;
- traversal outside the selected root is rejected;
- scan is read-only;
- apply targets the Hermes tree only;
- duplicate skill names across sources are grouped for review;
- no arbitrary root becomes canonical in this version.

### 6.3 Onboarding state

Store only UI progress/preferences in plugin-scoped storage. Derive filesystem
truth fresh from backend state. Version the onboarding-complete flag so a future
wizard migration can rerun safely.

## 7. Frontend work

Refactor `desktop/plugin.js` in reviewable steps rather than one replacement.
Keep the no-build ESM and allowed-import constraints.

Suggested component boundaries:

- `CompactSummaryPane`
- `ControlCenter`
- `PrimaryNav`
- `ToolsOverview`
- `ToolCard`
- `SingleToolView`
- `BulkActionBar`
- `BulkPreviewDialog`
- `FirstRunWizard`
- `ProblemsView`
- `SetsView`
- `ExpertMatrix`
- `McpView`
- `AdvancedView`

Use container measurement, not viewport width. Every responsive branch must be
covered by rendered fixtures.

The Control Center opener should prefer the current Hermes main-workspace API
when available and feature-detect it. Maintain a compatible route/navigation
fallback for older desktop builds. Do not assume the workspace will occupy the
entire application window; the split layout in the supplied screenshot is a
required test case.

## 8. Test strategy

### 8.1 Preserve existing gates

- Python core suite, including Python 3.9 import hygiene.
- FastAPI HTTP round trips.
- frontend static checker.
- React render harness.
- Windows path/symlink guards.

### 8.2 Add tests

Backend:

- bulk plan all-on/all-off/no-op/mixed/refused;
- plan-execute exact-id stability;
- partial failure receipts;
- precise undo;
- multiple scan roots and duplicate classification;
- invalid, missing, file, nested, and symlinked scan roots;
- concurrent state change between preview and execute;
- mutation-log redaction.

Frontend render fixtures:

- compact pane;
- split workspace matching the supplied screenshot;
- medium page;
- wide matrix;
- 105-skill catalog;
- zero/one/many detected tools;
- mixed problem states;
- bulk preview and partial result;
- first-run wizard at every step;
- keyboard and accessible-name assertions.

Live Hermes acceptance:

- install/enable both halves;
- open from status chip and command palette;
- verify compact pane and workspace behavior;
- run one no-op preview and one reversible mutation against disposable fixture
  paths, never the owner's real skill tree;
- restart gateway/desktop and verify persisted UI preferences plus filesystem
  truth.

## 9. Delegation and model-routing guidance

The main Hermes model is an orchestrator. A fast model such as the owner's
preferred DeepSeek Flash is suitable if it follows this plan literally,
maintains the queue, verifies bot results, and does not make consequential UX or
filesystem-safety decisions from its own intuition.

Do not rewrite Hermes model, provider, MOA, fallback, or delegation
configuration for this project. Use the configured bot system. Select bots by
capability:

- **UX/architecture bot:** strongest available reasoning model. Read-only first.
  Produces component/state design and SDK compatibility findings.
- **Frontend implementation bot:** strongest available coding model with good
  React state and accessibility performance.
- **Backend implementation bot:** strong Python/filesystem-safety model.
- **Test bot:** reliable lower-cost coding model for fixtures and mechanical
  coverage, escalating unexpected semantics to a stronger reviewer.
- **Independent reviewer:** a strong model different from the primary
  implementer. Review safety, regressions, UX acceptance, and diff scope.

Use at most three concurrent workers. Parallelize read-only investigation and
non-overlapping test design. Do not let two bots edit `desktop/plugin.js` or
`dashboard/plugin_api.py` concurrently in the same checkout. If Hermes uses
worktree isolation, give each worker a single owned change and integrate only
after tests and review. Otherwise serialize overlapping edits.

Every delegated task must include:

- repository path;
- exact owned files;
- relevant phase and acceptance criteria from this document;
- commands to run;
- prohibition on unrelated changes;
- required final report: findings, files changed, tests run/results, remaining
  risks, commit hash.

The orchestrator must inspect diffs and test output; a bot's claim that work is
complete is not verification.

## 10. Work phases and commits

Each numbered backlog item is a separately reviewed unit. Commit and push after
each completed item, as required by `AGENTS.md`.

### Phase 6: baseline

Deliverables:

- committed screenshot-shaped render fixture;
- current behavior inventory;
- concise Skills Dash comparison identifying ideas to port, reject, or defer;
- live-host acceptance checklist;
- no product behavior changes.

### Phase 7: shell

Deliverables:

- compact pane;
- workspace Control Center shell;
- feature-detected workspace opening with fallback;
- narrow/split/medium/wide tests.

### Phase 8: Tools overview

Deliverables:

- tool cards/rows with accurate counts;
- Manage, Enable all, Disable all entry points;
- absent optional tools excluded from daily view.

### Phase 9: single-tool and bulk

Deliverables:

- single-tool list;
- selection/category/all scopes;
- backend planning and execution receipts;
- confirmation and undo;
- all safety and partial-failure tests.

### Phase 10: onboarding

Deliverables:

- auto-detection;
- additional scan-folder input;
- classification review;
- dry run;
- adoption with backups;
- completion landing page.

### Phase 11: organization

Deliverables:

- Tools/Sets/Problems/MCP/Advanced navigation;
- expert matrix gated by width;
- advanced controls removed from first viewport without functionality loss.

### Phase 12: hardening

Deliverables:

- 100+ skill performance check;
- accessibility pass;
- installed-Hermes smoke pass;
- full macOS/Linux/Windows CI appropriate to platform capabilities;
- no warnings in the render harness, including unique React keys.

### Phase 13: alpha

Deliverables:

- complete solo dogfood checklist and recorded results;
- current screenshots;
- concise installation/onboarding docs;
- version/tag/release notes only after all gates pass;
- feedback issue template for outside users.

## 11. Release gates

Do not call the stabilization complete until all are true:

- a user can disable all links for one tool without scrolling;
- all protected entries are visibly refused, never overwritten;
- the UI remains usable in the supplied split-window shape;
- every detected tool is reachable even when the matrix cannot fit;
- the 105-skill fixture remains responsive;
- first-run scan requires no manual folder creation for detected tools;
- all existing and new automated suites pass;
- live installed-Hermes acceptance passes;
- Python 3.9 core import compatibility passes;
- no secrets appear in UI state, receipts, logs, or fixtures;
- documentation matches the shipped UI.

## 12. Solo validation script

Outside recruitment is useful but not a blocker for the alpha. The owner can
validate with disposable fixtures and the existing large catalog:

1. install from a clean plugin copy;
2. complete the scan wizard;
3. manage a tool from the split workspace;
4. preview and disable all for a disposable tool target;
5. undo it;
6. enable one category;
7. apply a named set;
8. add and distribute a new fixture skill;
9. resolve a fixture conflict three ways;
10. inspect Problems and MCP;
11. restart gateway and desktop;
12. repeat at narrow, split, and wide sizes;
13. record every terminal intervention, ambiguous label, clipped control, and
    unexpected mutation as a defect.

After the alpha is public, use even a small number of external reports to revise
priorities. Do not claim broad adoption or market validation from solo testing.
