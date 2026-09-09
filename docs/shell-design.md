# BACKLOG item 7 — Responsive product shell: design

Repository: `/Users/bakirmousa/projects/skills-toggle`
Author: UX/architecture bot (design only). This file is the only artifact of item 7's design.
Status: ready for a frontend implementation bot. **No backend change**; `plugin_api.py` untouched.
Tie references: plan section (`IMPLEMENTATION_PLAN.md §n`), verified SDK facts (`SDK`), current source line (~Ln in `desktop/plugin.js`), `docs/current-behavior-inventory.md` (INV §n).

This document is prescriptive. An implementation bot should follow it in reviewable steps inside a
single `desktop/plugin.js` refactor, keep the 6-registration contract + i18n bundle intact, and
extend the render harness per §6/§7.

---

## 0. Goal and shape of the change (plan §7; BACKLOG item 7)

Replace the two-tab `PaneRoot` (`PaneRoot` ~L2351, `SkillsPane` ~L911, `McpPane` ~L2145) with two
distinct product surfaces that share React-Query data but no longer share one component:

1. **CompactSummaryPane** — the contributed right pane (`placement:'right', width:'320px'`, pane
   contribution ~L2584). A summary/launcher per plan §4.1 and product decision D5 ("the narrow pane
   is a summary/launcher, not the complete management UI"). Default width 320 px lands in the
   **narrow** band (INV §2). It **never renders the six-switch catalog** and **never renders the MCP
   grid**.
2. **ControlCenter** — the full management UI surfaced in the Hermes main workspace via
   `host.openWorkspace` (feature-detected, plan §7), with the `/skills-toggle` route as the
   full-page fallback on desktops that predate `host.openWorkspace` (verified `SDK`:
   `openWorkspace` at sdk/index.ts:1138, `typeof host.openWorkspace === 'function'` is the detect).

The `/skills-toggle` route and the workspace surface render the **same** `ControlCenter` component so
behavior and fixtures are identical across hosts. The old Skills/MCP two-tab split is **retired**:
Skills management lives in Control Center Tools (later items 8–9), MCP lives in Control Center MCP
(item 11), Setup/Advanced move into Control Center Advanced/Setup (item 11), Sets in Sets (item 10).
For item 7 every non-Tools section is a **placeholder shell** (see §2.4) that a later item fills.
`McpPane`, `DriftPanel`, `SetupPanel`, presets, blueprint, backups code are **kept in the file but
not mounted** by item 7's shell — removal/migration of their content is owned by items 8–11 so no
behavior silently vanishes (AGENTS.md: never destroy the feature you migrate; work in reviewable
steps, plan §7).

Open decisions that need the orchestrator are collected in §8.

---

## 1. COMPACT PANE (plan §4.1)

### 1.1 Component: `CompactSummaryPane()`

Top-level contributed-pane render. No props. Owns a root `ref`, its own `usePaneLayout` (same hook,
~L144, D24 breakpoints: narrow `<360`, medium `360–559`, wide `≥560`; ResizeObserver-guarded so SSR
stays `'medium'`, INV §8.1). Root `<div>` keeps `className:'flex h-full min-w-0 flex-col text-sm'`.

It must fit a **280 px** min container (plan §4.1) — so structural layout must not assume 320+.

**Data** — reuses the exact existing query keys so React Query cache is shared and invalidation is
unchanged (INV §7.1, STATE_KEY/DIFF_KEY/DRIFT_KEY ~L62-65):

| Query | Key | queryFn | source |
|---|---|---|---|
| `stateQuery` | `STATE_KEY = ['skills-toggle','state']` | `ctx.rest('/state')` | total skills, detected tools, per-tool tool list + per-skill `tools.<id>.state` |
| `diffQuery` | `DIFF_KEY  = ['skills-toggle','diff']` | `ctx.rest('/diff')` | `counts {unlinked,broken,foreign,unmanaged}` |
| `driftQuery` | `DRIFT_KEY = ['skills-toggle','drift']` | `ctx.rest('/drift')` | `count` (drifted entries) — **only** to show the plan §4.1 "drifted" problem count |

These match the queries `SkillsPane` already issues (~L951-974), so no new network shape and no
backend change; `HealthChip` already shares `DIFF_KEY` (INV §1).

**Derivations** (pure helpers, module scope, exported for tests):

```
function presentLinkTools(state)  // state.tools.filter(!t.special && !t.optional) && t.present → ordered
function countEnabledByTool(state)// returns Map<toolId, n> over state.skills where skills[id].tools[toolId].state==='enabled'
function summaryProblemTotals(diff, drift) // { broken, foreign, unmanaged, unlinked, drifted }
```

Rendering body resolution (first match — mirrors `SkillsPane` order ~L… but compact):

1. **Loading** (`stateQuery.isPending`/`isLoading`) → header line + 3 `Skeleton` rows (not 8).
2. **Backend error** (`isError`) → `ErrorState` `title: t('errorTitle')` ('Skills backend
   unavailable'), same gateway-restart remedy mapping as today (`errorNeedsRestart`), `Retry`
   button wired to `stateQuery.refetch()` AND `diffQuery.refetch()`. (Existing i18n keys reused:
   `errorTitle`, `errorDesc`, `errorNeedsRestart`, `retry`.)
3. **`state.ok && !skills_root_exists`** → `EmptyState` `noRootTitle`/`noRootDesc` (`'No skills root
   found'`).
4. **success** → summary (below). Empty-skill (`counts.skills === 0`) renders summary normally with
   `0 skills` — do not collapse to an EmptyState that hides quick actions.

### 1.2 What the summary renders (always ≤ 3 quick actions; plan §4.1)

Top→bottom vertical stack inside a `ScrollArea` (`min-h-0 flex-1`):

1. **Header line** (`px-3 pt-3 pb-1`): `paneTitle` `'Skills'` + `ml-auto` small `Refresh` ghost
   (disabled while `stateQuery.isFetching`, reuses `refresh` key) — matches today's header cluster
   but drops Setup.
2. **Stat row** (`px-3 pb-1`): one line, e.g. `` `${total} skills` · `${tools} tools` `` using
   existing `skillsCount`/`toolAll`-style keys **or** a new compact composite key (see §5 for the
   exact new i18n keys). Show `total = state.counts.skills`, `tools = presentLinkTools(state).length`.
3. **Per-tool compact rows** (plan §4.1 "enabled counts by tool in compact rows"): for each
   `presentLinkTools(state)` **plus** the `hermes` row, one row:
   `StatusDot` (tone: `good` if enabled>0 else `muted`; `warn` if that tool has any problem entry) +
   tool label (truncate, `min-w-0`) + `ml-auto` muted text `` `${enabled} on` `` (new key) — a label,
   not a switch. **No `Switch`, no `aria-label` toggle** anywhere in the pane. A per-tool problem
   `Badge` (`` `${n}!` `` or warn badge) appears only when `countEnabledByTool`/state shows problem
   states for that tool. `hermes` row shows "enabled" = not in `skills.disabled`.
   **No hidden tools invariant:** every present link tool row is present in this list regardless of
   container width — rows are single-line, so all fit even at 280 px. Optional-but-absent tools stay
   out of the pane (they surface in Control Center "Add tool", INV §3, plan §4.3) — that is by
   design, not "hidden".
4. **Problem counts line** (`px-3 pb-1`): when any of
   `{broken, drifted, foreign+unmanaged, unlinked}` is `> 0`, render one line: a warn
   `StatusDot`/`Badge` cluster `` `${broken} broken · ${drifted} drift · ${foreign+unmanaged}
   foreign · ${unlinked} unlinked` `` (reuse existing `brokenCount`/`unlinkedCount` keys + new
   composite). Problems > 0 is what makes the **Problems quick action** light up (§1.3). Clicking
   the line opens Control Center → Problems (same as Problems action).
5. **Quick actions** (`px-3 pb-3 pt-1`): exactly the plan §4.1 three, in fixed order:
   - **Open Control Center** — primary `Button` (full-width in narrow; see widths) →
     `openControlCenter()` (§3).
   - **Scan** — `Button` secondary → item 7 placeholder: `openControlCenter('tools')` (real scan flow
     is owned by later items; labeled honestly, §8 open decision).
   - **Problems** — secondary `Button` shown **only** when total problems `> 0`, label
     `` `Problems (${n})` ``. → `openControlCenter('problems')`.
   This keeps the action set ≤ 3 always (plan §4.1 "at most three quick actions").

**Width behavior within the pane** (container-measured, INV §2; summary is width-invariant except
spacing/geometry — the pane is never a catalog at any width):

| layout (container px) | behavior |
|---|---|
| narrow `<360` (default 320 px pane) | Tool rows single-line; quick actions stacked full-width (Open CC, then Scan + Problems each `w-full`). Description text never rendered. |
| medium `360–559` | Same summary; quick actions in a 2-col grid (Open CC spans 2), tool rows roomier `gap`. |
| wide `≥560` | Same summary, roomier padding. Still a summary — wide pane can't exceed what a docked pane gives, and full management is the Control Center's job. |

### 1.3 Quick-action handler semantics (shared module helper, §3)

`Scan` and `Problems` both resolve to *opening the Control Center on a section*, never to an inline
operation in the 320 px pane. `Open Control Center` opens the default Tools landing. This keeps the
pane pure (no hidden catalog, no crammed controls) and centralizes every real mutation in the
workspace where bulk preview/confirm/undo lives (plan §5 flows; items 8–11).

---

## 2. CONTROL CENTER workspace (plan §4.2)

### 2.1 Opener — feature-detected workspace open

Module-level helper (mirrors verified `SDK` `openWorkspace(id, opts)` signature at sdk/index.ts:1138;
returns a disposer that closes the tab):

```
const WORKSPACE_ID = 'skills-toggle.control-center'      // stable id → re-open fronts the same tab (SDK)
let workspaceDispose = null                               // module ref, not an atom (see §5)
function openControlCenter(section) {
  if (section) ccSectionAtom.set(section)
  if (typeof host.openWorkspace === 'function') {          // feature-detect (plan §7, SDK:1135-37)
    if (workspaceDispose) { closeControlCenter() }         // idempotent front (see §5 note)
    workspaceDispose = host.openWorkspace(WORKSPACE_ID, {
      title: t('ccTitle'),                                 // 'Skills Control Center' (new key)
      minWidth: '680px',                                   // never collapse ToolsOverview below usable width
      // dock omitted → default { pane:'workspace', pos:'center' } (SDK default)
      onClose: () => { workspaceDispose = null },
      render: () => jsx(ControlCenter, {})
    })
    return
  }
  host.navigate('/skills-toggle')                          // fallback route (SDK navigate sdk/index.ts:664)
}
function closeControlCenter() { if (workspaceDispose) { workspaceDispose(); workspaceDispose = null } }
```

- `host.openWorkspace` is **not** imported by name (used via the already-imported `host` object), so
  `check_frontend.py` #3b (SDK-name cross-check) is unaffected.
- Feature-detect default true branch: open workspace. Fallback: `host.navigate('/skills-toggle')` —
  the route contribution (unchanged path `'/skills-toggle'`, page contribution ~L2591) is re-pointed
  to render `ControlCenter` (§2.4) so both hosts land on the same full management UI.
- `minWidth:'680px'` chosen so two tool columns + description text fit without clipping; the
  "split layout" fixture (§6) simulates the container at `~700px` wide — above `minWidth`, wide band.

### 2.2 Component `ControlCenter()`

The shared shell. No props (source-agnostic). Renders:

- `layout` from `usePaneLayout` on its own root `ref` (container-measured, not viewport — INV §2;
  the workspace is a resizable dock, plan §7 "Do not assume the workspace occupies the entire
  application window").
- A left **PrimaryNav** rail (see §2.3) `w-[180px]` at `wide`/`medium`; collapses to a top
  **horizontal segmented rail** at `narrow` (all 5 section chips reachable by horizontal scroll —
  **no section hidden** at any width; keyboard/arrow nav like today's `ToolFilter` `role="tablist"`).
- The active section body in a `ScrollArea`. Section switch = `ccSectionAtom` change (live
  subscription → both surfaces and a mounted Control Center re-render, matching real-SDK `useValue`
  semantics INV §8.4).

Section → body dispatch (table-driven, mirrors TS style "table-driven beats condition ladders"):

| `ccSectionAtom` | body for item 7 |
|---|---|
| `'tools'` (default landing) | `ToolsOverview` skeleton (§2.5) |
| `'sets'` | `SectionPlaceholder` (`title: t('ccSets')`, hint: lands in Sets item 10) |
| `'problems'` | `SectionPlaceholder` (`title: t('ccProblems')`, hint: item 11) |
| `'mcp'` | `SectionPlaceholder` (`title: t('ccMcp')`, hint: MCP projections item 11) |
| `'advanced'` | `SectionPlaceholder` (`title: t('ccAdvanced')`, hint: item 11) |

The landing is always Tools on open (plan §4.2 "Tools — default daily view"). Section choice is
**not persisted** (§5) so every open presents the daily Tools view.

### 2.3 Component `PrimaryNav`

```
PrimaryNav({ sections, active, onSelect, layout, wide })
```

- `sections`: array of `{ id, label }` — Tools, Sets, Problems, MCP, Advanced (plan §4.2 order).
- `active`: the current `ccSectionAtom` value. `onSelect(id)`: `ccSectionAtom.set(id)`.
- `layout` + `wide`: item 7 renders the five sections. **Matrix** is *reserved* under Tools and gated
  `wide` (plan §4.2 "optional Matrix view can appear under Tools when the container is wide"); for
  item 7 `Matrix` is declared as a disabled/discreet entry only in `wide` and routes to a
  `SectionPlaceholder`, fully replaced by item 11's real `ExpertMatrix` (plan §7 component list).
  This makes the wide-only gate visible and testable now without shipping the matrix.
- Rendering: `role="navigation"` `<nav>`; each item a `button` with `aria-current` on the active
  section; `aria-label` per section (accessible-name assertion target §6). Keyboard: arrow keys
  move focus between items; Enter/Space select (native buttons give this free).

### 2.4 Placeholder shells

`SectionPlaceholder({ title, hint })`: a centered body = `EmptyState` `title` + `description`
=`hint` text noting the section ships in a later phase. It guarantees no section is an empty white
box and keeps nav meaningful before items 10–11 fill the real UIs.

### 2.5 `ToolsOverview` — item 7 skeleton (real cards land in item 8)

`ToolsOverview({ layout, wide })`. For item 7 it renders the **Tools landing container** only:
- Heading `` `Tools` `` + summary `total skills` badge (reuse `skillsCount`).
- **Placeholder per present link tool** as read-only rows (label + `StatusDot` + `` `${enabled}
  on` `` count) — a thin, honest preview of plan §4.3's "one card/row per tool" that is **not** the
  real item-8 card: no Manage/Enable-all/Disable-all yet, no mutation wiring. Rendered from the
  shared `stateQuery` so the shell already exercises `/state` on the wide surface.
- A `Separator` + a footnote `` `Tool cards with Manage / Enable all / Disable all land in the next
  phase.` `` so the disabled entry points are visibly planned, not missing.
- **No hidden tools invariant:** every present link tool row renders at every width (single column at
  narrow, wrap at medium/wide).

Item 8 will swap this skeleton's read-only rows for the real `ToolCard` with
`{tool, counts:{enabled,off,problem}, onManage, onEnableAll, onDisableAll}` per plan §4.3 — the
skeleton's prop contract is forward-compatible (layout/wide passed in now; the mutation handlers are
added in item 8).

### 2.6 Route wiring

- Pane contribution (~L2584): `render: () => jsx(CompactSummaryPane, {})` (was `PaneRoot`).
- Page contribution (`/skills-toggle`, ~L2591): `render: () => jsx(ControlCenter, {})` (was
  `PaneRoot`). This is the fallback full-page Control Center when `host.openWorkspace` is absent.
- Palette commands: `open` (Skills: toggle…) and `mcp` (MCP: toggle…) and `report` (health) all now
  call `openControlCenter(section)` — `'tools'`, `'mcp'`, and `'problems'` respectively — instead of
  `host.navigate` + tab-set. `chip` click → `openControlCenter()` (was `host.navigate`, INV §1).
  All still leave the 6-registration count unchanged and `label`/`keywords`/`detail` intact.

---

## 3. Responsive strategy (plan §7; §4.2; BACKLOG item 7)

**Principle: container measurement everywhere.** Reuse the existing `usePaneLayout` hook (~L144,
D24 breakpoints narrow `<360` / medium `360–559` / wide `≥560`) on both the pane root and the
Control Center root. **Never** viewport media queries for layout that changes *what is rendered* —
the existing MCP `sm:grid-cols-3` viewport usage (INV §5, ~L2265) is the anti-pattern to avoid; the
Control Center's matrix gate and column counts come from `usePaneLayout`, not `sm:`.

Guarantees (release gates §11):
- **No hidden tools** — pane lists all present link tools (§1.2); ToolsOverview lists all present
  tools (§2.5); PrimaryNav exposes all five sections at every width (§2.2). Only optional-absent
  tools are excluded by design (plan §4.3) and always reachable via Control Center Add Tool.
- **No clipped descriptions** — no `truncate`-only descriptions in the pane (pane has no
  description at all); ToolsOverview/section bodies live in a `ScrollArea` and, when the real cards
  land (item 8), will wrap/expand rather than rely on hover-title ellipsis. Item 7 ships no ellipsis
  content on the workspace.
- **No ambiguous switches** — item 7 mounts **zero** `Switch` in the pane. Single-tool switches
  return only in Control Center's single-tool view (item 9) at wide widths, with accessible labels
  carrying both skill and tool names (plan §4.4/§4.5).

**Split-window test case** (required, plan §7, §8.2): the item-6 screenshot shape is a Control
Center docked at ~`700px` beside the chat. At 700 px the container reads **wide** (`≥560`) → Tools
Overview wraps tool rows in ~2 columns; PrimaryNav is the left rail. Fixture locks this (see §6).

---

## 4. Component boundaries and prop contracts (item 7 scope; plan §7 list)

Item-7-in-scope components and their contracts. `ControlCenter`, `PrimaryNav`, `CompactSummaryPane`
are new; `SetupPanel`/`McpPane`/`DriftPanel`/presets/blu… are preserved but unmounted until items
8–11 re-mount them from the Control Center.

| Component | Contract (props/state) | Renders at narrow / medium+ |
|---|---|---|
| `CompactSummaryPane()` | none. internal: `layout`, `stateQuery/diffQuery/driftQuery`, `openControlCenter`/`closeControlCenter` imports. | §1.2 |
| `ControlCenter()` | none (source-agnostic). internal: `layout`, `ccSectionAtom` body dispatch. | §2.2 |
| `PrimaryNav({ sections, active, onSelect, layout, wide })` | `sections: {id,label}[]`; `active: string`; `onSelect(id)`; `layout`; `wide:boolean`. | narrow: horizontal scroll rail; medium/wide: left rail 180 px |
| `ToolsOverview({ layout, wide })` | read-only present-tool rows from shared `stateQuery` (real mutation props added item 8). | narrow single col; medium/wide wrap |
| `SectionPlaceholder({ title, hint })` | title string + hint string. | centered `EmptyState` |
| `usePaneLayout(ref)` | **unchanged** existing hook. | — |
| `openControlCenter(section?)`, `closeControlCenter()` | module helpers (§2.1). | — |

Plan §7's other names (`ToolCard`, `SingleToolView`, `BulkActionBar`, `BulkPreviewDialog`,
`FirstRunWizard`, `ProblemsView`, `SetsView`, `ExpertMatrix`, `McpView`, `AdvancedView`) are **out of
item-7 scope** — declared here so later items slot them in under `ToolsOverview`/the section bodies
without re-touching the shell.

---

## 5. State: atoms vs local; storage keys

**Atoms (shared, module-level, seeded at module init):**
- Replace `paneTabAtom` (line ~L66, `'skills'|'mcp'`) with **`ccSectionAtom`** defaulting to
  `'tools'`. Values: `'tools'|'sets'|'problems'|'mcp'|'advanced'` (matrix is a Tools sub-state, not a
  section, item 11). `useValue(ccSectionAtom)` drives `ControlCenter`'s active body AND can be read
  imperatively by palette commands (§2.6). Not persisted → every Control Center open lands on Tools
  (plan §4.2 default). Remove the `'skills'|'mcp'` tab switch from the pane entirely (pane has one
  surface).
- **No atom for the workspace-disposer** — `workspaceDispose` stays a module `let` (§2.1). Rationale:
  it is transient handle state only the opener needs; persisting it in an atom would subscribe
  components that must not re-render on open/close. This matches the "state lives with its
  authority, narrowest home" rule (desktop AGENTS).

**Local component state:** only per-render ephemera the component owns (e.g. nothing new in item 7
— no search/filter lives in the pane anymore). Query data stays in React Query (already the
authority for backend truth; INV §7).

**ctx.storage keys (ctx.storage only — `check_frontend.py` §6 enforces; no localStorage):**
- **Retire/keep for compatibility:** `paneTab` (no longer read), `toolFilter`, `viewFilter`,
  `setupDismissed`, `seenSkills`, `autoLink`, `watchPrefs` stay written/read by the preserved-but-
  unmounted panels so nothing breaks when items 8–11 re-mount them from Control Center. Do **not**
  delete existing keys in item 7.
- **New in item 7:** none required for the shell. If a future item wants a remembered last section,
  add `ccSection` later — deliberately not added now so the Tools landing is deterministic (§2.2).

**New i18n keys (add to the `en` bundle in `register()`, §… keep >30 keys and the used-keys regex
assertion green):** `ccTitle` ('Skills Control Center'), `ccTools/ccSets/ccProblems/ccMcp/ccAdvanced`
(nav labels), `openControlCenter`, `scan`, `problemsAction: n => 'Problems (' + n + ')'`,
`enabledOn: n => n + ' on'`, `summaryLine: (s,t) => s + ' skills · ' + t + ' tools'`,
`problemLine: (b,d,f,u) => b + ' broken · ' + d + ' drift · ' + f + ' foreign · ' + u + ' unlinked'`,
`toolsLandingHint`, `matrixSoon` (wide Matrix reserved entry), and per-section `hint*` strings.

---

## 6. Harness / test plan (plan §8.2, item 7 fixtures)

Existing harness structure: `tests/run_render_harness.sh` copies `desktop/plugin.js` + `tests/fixtures/`
into `/tmp/skt-front/staging`, runs `tests/render_harness_body.mjs` with `sdk_stub.mjs`. Item 7 keeps
that flow and **adds a second harness body** for the workspace surface (per the task: "A second
harness/body may be added"). Fixtures stay JSON files under `tests/fixtures/`.

### 6.1 New fixtures (in `tests/fixtures/`)
- `split-workspace.json` — mirrors the item-6 screenshot shape: 105 skills, 6 present tools, `tools`
  array, per-skill tool states. (Can **reuse** `screenshot-shaped-105-skills.json` unchanged; it
  already has 6 present tools and 105 skills — reuse to avoid duplication, fixture rides along, INV
  harness §… already loads it.)
- `tools-many.json`, `tools-one.json`, `tools-zero.json` — `/state` with 6 / 1 / 0 present link
  tools (small skill sets).
- `mixed-problems.json` — `/state` + `/diff` with broken + foreign + unmanaged + unlinked each > 0
  and `/drift` `count: N>0`.

### 6.2 Pane harness (existing body extended) — assertions
- **Registry stays 6**, pane `placement:'right' width:'320px'`, page path `/skills-toggle`,
  palette `label`s, chip text — all unchanged (contract preserved).
- **CHANGED:** pane `renderToString` now yields **`(role="switch").length === 0`** (was 18; and
  the 105-skill fixture yields 0, **was 630**). Assert the pane instead renders the compact summary:
  - `>Skills<` title, stat line `'105 skills · 5 tools'` (or with new summary key), and per-tool rows
    `' on'` counts (from the shared `countEnabledByTool` on `screenshot-shaped-105-skills.json`:
    claude 79, codex 67, grok 74, opencode 83, zcode 75 per §… fixture math).
  - Quick actions present and ≤ 3: `'Open Control Center'`, `'Scan'`, and `'Problems (…)'` when
    problems > 0.
  - Loading → 3 skeletons; error (`mode:'error'`) → `'Skills backend unavailable'` + `'Retry'`;
    gateway-restart remedy string still maps (`errorNeedsRestart`) when `errorMessage` contains
    'Headless backend'.
  - Empty state keys still render where appropriate.
- **REMOVED/re-scoped pane assertions** (were about the catalog): `'Set up your tools'`,
  `'Coding'/'Writing'/'Minimal'` presets, `grid-cols-3`, 18/630 switch counts, per-skill tooltip
  strings. Those now belong to the Control Center workspace surface (fixture assertions move, they
  are not deleted globally).

### 6.3 Workspace harness (new body, e.g. `workspace_harness_body.mjs`)
Drives both surfaces the way a real split does:
1. Call `palOpenCmd.data.run()` (or `openControlCenter()`) → assert **`host.openWorkspace` was
   called once** (recorded) with `id==='skills-toggle.control-center'`, `minWidth==='680px'`, dock
   defaulting (undefined), `title` truthy, `render` a function. Also assert `navigations` did **not**
   get `/skills-toggle` (workspace path taken).
2. Render the workspace: `renderToString(activeWorkspace.render())` where `activeWorkspace` is the
   recorded openWorkspace call's `render` — simulating the second docked surface beside the pane.
   Assert: `PrimaryNav` shows all five sections; **Tools body** shows the placeholder rows for all
   present tools (`'Claude'`,`'Codex'`,… plus `hermes`); Matrix reserved entry present in `wide`;
   `SectionPlaceholder` text for `sets/problems/mcp/advanced` when `ccSectionAtom` is switched.
3. **Fallback path:** stub `host.openWorkspace = undefined` (feature-detect false) in a second run →
   assert `navigations` **does** include `/skills-toggle` and the route contribution render is the
   same `ControlCenter`.
4. **Split shape** (medium page ~`700px` container, SSR `layout==='medium'`/`'wide'` boundary):
   since SSR has no ResizeObserver, layout is `'medium'` (INV §8.1) — assert the Tools placeholder
   and PrimaryNav render with **no hidden sections** and every present tool row present. The *real*
   700 px ResizeObserver geometry is a live-host check (INV §8.1), not SSR-assertable.
5. **MCP reachability:** `palMcp.data.run()` → assert `ccSectionAtom==='mcp'` and Control Center
   MCP body is the MCP `SectionPlaceholder` (was the 9-switch `McpPane` — assertion moved/deferred).

### 6.4 Keyboard + accessible-name assertions (plan §8.2)
- PrimaryNav items carry distinct accessible names (one per section, `aria-label`/text) → assert the
  set of `aria-label`s equals the five sections; active item has `aria-current`.
- Tool rows are text (label + count), not interactive switches → assert no `role="switch"`, no
  `role="tab"` clutter in the pane.
- Pane quick-action buttons each have a stable text label (no duplicate generic "button").

---

## 7. SDK stub additions (`tests/sdk_stub.mjs`)

Exact additive changes (keep every existing export/behavior so the unchanged checks still pass):

1. **`host.openWorkspace(id, options)`** — record + expose the render so the harness can SSR the
   second surface:
   ```js
   openWorkspace: (id, options) => {
     const rec = { id, dock: options?.dock, title: options?.title,
                   minWidth: options?.minWidth, uncloseable: options?.uncloseable,
                   render: options?.render, onClose: options?.onClose }
     const wl = (S().workspaces = S().workspaces || [])
     wl.push(rec)                       // record call order + args
     S().activeWorkspace = rec          // harness renders rec.render() for the docked surface
     return () => { S().workspaceCloses = (S().workspaceCloses || 0) + 1 }
   }
   ```
   The harness never actually mounts into a layout in SSR — it calls `renderToString(rec.render())`
   to represent the second split surface (§6.3). Because `render()` in the real SDK is re-invoked on
   re-open of the same `id`, recording the call also lets the harness assert fronting/dup behavior
   (`workspaces.length` stays 1 across two `openControlCenter()` calls if the plugin closes-then-
   reopens, or the plugin keeps one disposer — see the idempotency note in §2.1 and the open
   decision §8).
2. **`useValue` live subscription**: real `useValue` is a live nanostores subscription (verified
   SDK; INV §8.4). Keep the current synchronous read for SSR (renderToString runs no effects), and
   **add** an inert `subscribe` so the contract is representable and a future react-dom/client
   harness can assert a mounted surface re-renders on `ccSectionAtom.set`:
   ```js
   const atom = initial => { const a = { _val: initial, _subs: new Set(),
     get: () => a._val,
     set: v => { a._val = v; a._subs.forEach(f => f(v)) },
     subscribe: fn => { a._subs.add(fn); return () => a._subs.delete(fn) } }
     return a }
   export const useValue = a => (typeof a === 'function' ? a() : a.get())
   ```
   (The harness continues to assert live switches by calling the palette `run()` then re-rendering —
   unchanged approach, INV §8.4.)
3. **Add `host.openWorkspace` guard consistency**: no change to `navigate`, `ROUTES_AREA`,
   `PANES_AREA`. The workspace is recorded under `S().workspaces`; the route is *not* a workspace —
   keep the existing distinction (pane + `/skills-toggle` route render `ControlCenter` only in the
   fallback path, §2.6), asserted via the `openWorkspace=undefined` run (§6.3.3).
4. **Fixture plumbing:** the body sets `channel.mode`/`channel.state`/`channel.diff`; add
   `channel.drift` handling (today only `driftList`) so the pane's `driftQuery` can return a
   `{ok,count}` for the `mixed-problems` fixture. Keep `driftList` for the (now-workspace)
   drift-panel path.

No `tests/check_frontend.py` edits are allowed (task constraint); nothing above needs it. No
`plugin.js`/test edits are performed by this task — §6/§7 are the *prescription* the frontend
implementation and test bots will execute.

---

## 8. Open decisions for the orchestrator

1. **MCP reachability during the transition.** Removing `McpPane` from the pane and making Control
   Center MCP a `SectionPlaceholder` until item 11 means MCP server toggling is **not live** from any
   surface after item 7 until item 11 lands. Options: (a) accept the gap (cleanest item-7 cut; MCP
   fully returns item 11); (b) re-mount the existing `McpPane` under the Control Center MCP section
   in item 7 as a stopgap. **Recommend (a)** — items ship in order and item 11 is MCP's home; a
   stopgap contradicts the "placeholder filled later" directive. But the orchestrator must accept the
   temporary feature gap and drop the MCP harness assertions (§6.3.5) that expected a live 9-switch
   McpPane.
2. **`Scan` quick action behavior in item 7.** Plan §4.1 names Scan as a pane action, but the real
   scan/adopt flow (item 10) and bulk UX (item 9) are not built. This design makes item-7 Scan open
   the Control Center Tools landing as a placeholder. If the orchestrator wants Scan disabled (not a
   false affordance) in item 7 instead, say so — recommendation: keep it opening the Control Center,
   but the button should read honestly (no pretend scan run).
3. **Workspace idempotency/dup policy.** The SDK fronts an existing tab for the same id (SDK:1131-33)
   but does not expose whether a tab is already open. This design closes-then-reopens on each call
   (`closeControlCenter()` guard) to avoid stacking; the alternative is to call `openWorkspace`
   unconditionally and rely on the SDK to front. Verify against the real desktop; the harness records
   call count to make either policy assertable. **Recommend** relying on the SDK to front (fewer
   close/disposer edge cases), but confirm with a live-host smoke before locking the harness
   assertion.
4. **PrimaryNav Matrix reservation.** Showing a disabled "Matrix" entry on wide only is a stub the
   plan doesn't require until item 11. If the orchestrator prefers zero Matrix surface in item 7
   (pure Tools placeholder), drop §2.3's reserved entry and gate it fully in item 11. The declared
   `wide` gate is forward-compatible either way.
5. **Dropping old pane-assertion strings is intentional** (§6.2): the pane stopping its catalog and
   MCP render is the whole point of §4.1. Confirm the orchestrator will update `render_harness_body.mjs`
   (pane no longer renders 18/630 switches, `Set up your tools`, presets, or the MCP grid) rather
   than treat it as a regression — these are contract changes owned by item 7.

---

### Change-log vs plan
- plan §4.1 → §1 (compact pane, states, quick actions).
- plan §4.2 → §2 (workspace shell, nav, landing, placeholders).
- plan §7 container-measurement/feature-detect/split-case → §2.1, §3.
- plan §7 component list → §4 (item-7 subset concretized; rest declared out of scope).
- plan §8.2 fixtures incl. keyboard/a11y → §6.
- SDK facts → §2.1, §7 (stub additions); no rediscovery performed.

---

## 9. Revisions after the mandatory Grok 4.6 challenge review (orchestrator-accepted)

Reviewer: `grok -m grok-4.6`, read-only, over `docs/shell-design.md`, item-6
baseline evidence, the 105-skill fixture, and plan §3/§4/§5/§7/§8/§11. Verdict
**APPROVE-WITH-CHANGES**. Every finding was verified against `desktop/plugin.js`
source before acceptance. The five §8 open decisions are resolved below.

### 9.1 No Matrix in PrimaryNav (§2.3 superseded)
Plan §5 and §4.2 make Matrix a **Tools sub-state** ("optional Matrix view can
appear under Tools when the container is wide"), not a nav section. The wide-only
Matrix reservation is **removed from PrimaryNav entirely** in item 7; all Matrix
surface (including a gated/disabled entry) is deferred to item 11's `ExpertMatrix`.
`PrimaryNav` renders exactly the five sections Tools/Sets/Problems/MCP/Advanced.

### 9.2 presentLinkTools rule corrected (§1.1 superseded)
The proposed `!t.special && !t.optional && t.present` is wrong vs the current
source contract `desktop/plugin.js:984` (`allTools.filter(tool => !tool.optional
|| tool.present)`) and plan §4.3. Correct rule for both the compact pane and
ToolsOverview rows:
```
const toolRows = allTools.filter(tool => !tool.optional || tool.present)
```
and **hermes is always its own row** in both surfaces (Hermes is the canonical
library, plan §3.1). Optional-but-present tools (Cursor/Kimi/…) stay in the list
per the source rule; they are excluded only while absent (`optional && !present`),
and remain reachable via Control Center Add Tool. No present tool is ever hidden.

### 9.3 Background effects must not strand (HIGH — the "do not ship without" item)
Auto-link, watch notifications, and arrivals detection live in `SkillsPane`
(`desktop/plugin.js` ~L1144 arrivals, ~L1176 auto-link, ~L1212 watch). Unmounting
`SkillsPane` would **silently stop auto-link and watch while their prefs still say
on** — a real regression (AGENTS.md: never destroy the feature you secure).
Fix (mandatory): extract these three into a shared **`useBackgroundSync()`** hook
and mount it from a single-run owner so it survives any surface change:
- `BackgroundHost` component calls `useBackgroundSync()`; mounted in **both**
  `CompactSummaryPane` root and `ControlCenter` root.
- A module-level `let bgHosted = false` guards single ownership: the first mount
  claims it (`bgHosted = true`), cleanup releases it. Exactly one background host
  runs regardless of which surfaces are mounted or whether the pane is closed.
- The old inline effects are removed from `SkillsPane` (they move into the hook).

### 9.4 No dead-ends: transitional content, not empty placeholders (§2.2/§2.4 superseded)
Do **not** mount `SectionPlaceholder` for sections whose shipped capability exists
today. Item 7 relocates existing UI into the workspace so nothing is lost until
items 8–11 replace each section:
- **Tools** → mounts the existing `SkillsPane` content as the transitional daily
  view (catalog + bulk + presets + setup + drift + backups + blueprints stay
  reachable in the wide workspace). Item 8 swaps this for `ToolCard` overview;
  item 9 for `SingleToolView`/bulk. (`useBackgroundSync` effects removed from it.)
- **MCP** → mounts the existing `McpPane` (transitional). `MCP: toggle…` palette
  command therefore stays live, not a dead-end. Item 11 refines the MCP section.
- **Problems** → mounts the existing `DriftPanel` (transitional).
- **Sets** → mounts the existing presets row/panel (transitional).
- **Advanced** → mounts the existing `SetupPanel` + watch + paths + auto-link +
  blueprint + backups (transitional).
- `SectionPlaceholder` is retained only for sections with genuinely no shipped
  content yet (none in item 7) — effectively unused until item 10/11 add new
  surfaces; keep the component for later items.
This satisfies D41 (both surfaces usable if only one can open) and keeps every
existing mutation (Flow B disable, drift push/pull, presets, backup/restore,
blueprint apply, setup, MCP sync/remove) reachable through the transition.

### 9.5 `openControlCenter` semantics (§2.1 amended)
- `openControlCenter(section = 'tools')` **always** calls `ccSectionAtom.set(section)`
  (default `'tools'`), so every open deterministically lands on Tools; palette
  deep-links (`'mcp'`, `'problems'`) set the section explicitly. Fixes the §8
  concern that a leftover section atom would survive across opens.
- **Rely on the SDK to front by stable id** (`skills-toggle.control-center`) per
  sdk/index.ts:1131-33 ("fronts the existing tab instead of stacking a duplicate").
  Do **not** close-then-reopen in `openControlCenter`. Keep the `workspaceDispose`
  module ref only so `closeControlCenter()` can close on demand. Verify the
  fronting behavior in a live-host smoke (item 13) before locking a call-count
  assertion.

### 9.6 Decision log for §8 open decisions
1. MCP gap → **Rejected as a gap**: remount live `McpPane` under Control Center MCP (§9.4); old 9-switch pane assertions move to the workspace MCP section.
2. Scan quick action → Opens Control Center Tools landing (honest label, no fake scan). Real scan/adopt ships item 10.
3. Workspace idempotency → Rely on SDK fronting by stable id (§9.5); no close-reopen; disposer for explicit close.
4. Matrix → No PrimaryNav entry in item 7; Matrix is a Tools sub-state deferred to item 11 (§9.1).
5. Dropping old pane assertions → **Confirmed intentional contract change**: pane becomes summary-only (0 switches). `render_harness_body.mjs` is updated accordingly; those assertions move to the workspace surface.

### 9.7 Harness additions from the revisions
- Pane harness asserts `role="switch"` count **0**, exactly **6** tool rows
  (5 present link tools + hermes) at all widths, per-tool `N on` counts from the
  fixture, and ≤3 quick actions.
- Workspace harness: 5 sections; Tools shows **hermes + all present tools** rows;
  MCP section renders the **live `McpPane`** (assert its switch count, e.g. 9 for
  the 3-row fixture); `BackgroundHost` mounts exactly once (assert `bgHosted` toggles,
  no double auto-link); fallback path (`host.openWorkspace === undefined`) →
  `/skills-toggle` route renders `ControlCenter`.
- sdk_stub.mjs: `host.openWorkspace` recorder + disposer (§7); `useValue` stays a
  synchronous read with an inert `subscribe`; add `channel.drift` `{ok,count}` for
  the pane's `driftQuery`.
