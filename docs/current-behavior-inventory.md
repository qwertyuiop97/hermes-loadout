# Current-Behavior Inventory — skills-toggle desktop pane + route (pre-refactor baseline)

- Repository: `/Users/bakirmousa/projects/skills-toggle`
- Commit / HEAD: `ec4aaab` (`ec4aaabcbcd628e406379a308a609fa3f815f827`)
- Source read: `desktop/plugin.js` (2645 lines), `tests/render_harness_body.mjs`, `tests/sdk_stub.mjs`, `IMPLEMENTATION_PLAN.md` (§2, §4.1, §4.2, Phase 6), `DECISIONS.md` (D24, D25, D41).
- Scope: BACKLOG item 6 baseline. **No behavior change.** This document records current behavior only.
- The desktop pane and the `/skills-toggle` route render the **same** `PaneRoot` component (both registered contributions point `render` at `PaneRoot`, see §1). D41: the contributed pane and the route share one implementation.

---

## 1. Registered contributions (confirmed from `plugin.js` `register()`, `ctx.registerMany([...])` at ~L2582)

Six contributions, one i18n bundle, plugin `id = 'skills-toggle'`, `defaultEnabled: false` (opt-in desktop half).

| # | id | area (SDK const → string) | data | render |
|---|----|---------------------------|------|--------|
| 1 | `pane` | `PANES_AREA` → `'panes'` | `{ placement: 'right', width: '320px' }`, `title: 'skills'` | `() => jsx(PaneRoot,{})` |
| 2 | `page` | `ROUTES_AREA` → `'routes'` | `{ path: '/skills-toggle' }` | `() => jsx(PaneRoot,{})` |
| 3 | `open` | `PALETTE_AREA` → `'palette'` | `id:'skills-toggle.open'`, `label:'Skills: toggle…'`, keywords `['skills','toggle','sync','claude','codex','opencode','grok','zcode']`, detail `'Enable or disable skills per tool'`; `run()` → `paneTabAtom.set('skills')`, `storeSet('paneTab','skills')`, `host.navigate('/skills-toggle')` | — |
| 4 | `mcp` | palette | `id:'skills-toggle.mcp'`, `label:'MCP: toggle…'`, keywords `['mcp','servers','claude desktop','toggle']`; `run()` → `paneTabAtom.set('mcp')`, `storeSet('paneTab','mcp')`, `host.navigate('/skills-toggle')` | — |
| 5 | `report` | palette | `id:'skills-toggle.report'`, `label:'Skills: health report'`, keywords `['skills','health','broken','diff','repair']`, detail `'Broken links, unlinked skills, drift'`; `run()` → `host.navigate('/skills-toggle')` **only** (does not change tab) | — |
| 6 | `chip` | `STATUSBAR_AREAS.right` → `'statusbar.right'` | `order: 140` | `() => jsx(HealthChip,{})` |

- Palette commands all land on the `/skills-toggle` route; `Skills: toggle…`/`MCP: toggle…` also force the active tab via the `paneTab` atom; `Skills: health report` does not.
- i18n registered at `register()` start via `ctx.i18n.register({ en: {…} })` (179 top-level `en` keys).
- Harness (`render_harness_body.mjs`) asserts the registry holds 6 contributions and separately finds pane/page/palette-`Skills: toggle…`/palette-`report`/palette-`MCP: toggle…`/statusbar-chip; asserts pane `placement==='right' && width==='320px'`, page path `'/skills-toggle'`, health chip HTML contains `'1 broken'` given the broken fixture.

### Health chip (`HealthChip`, ~L519)
- `useQuery` on `DIFF_KEY` (`[ID,'diff']`), `queryFn: ctx.rest('/diff')`, `staleTime:30000`, `refetchInterval:60000`, `refetchOnWindowFocus:false`, `retry:0`.
- **D25 thresholds:** if pending or no broken and no unlinked → renders a plain green `StatusDot` button (good tone); if broken > 0 → warn `Badge` with text `` `${broken} broken` `` (broken takes precedence); else if unlinked > 0 → outline `Badge` text `` `${unlinked} unlinked` ``.
- The dot/badge is always a clickable button → `host.navigate('/skills-toggle')`.

---

## 2. Pane layout — container-measured breakpoints (`usePaneLayout`, ~L144)

- `rootRef` attached to the pane's outer `<div>` (`className:'flex h-full min-w-0 flex-col text-sm'`).
- `usePaneLayout(ref)`: initial state `'medium'`; if `typeof ResizeObserver === 'undefined'` returns `undefined` early and the state stays `'medium'`. Otherwise a `ResizeObserver` reads `entry.contentRect.width`; on `width===0` it returns without changing; else sets:
  - `w < 360` → `'narrow'`
  - `w < 560` → `'medium'`
  - `else` → `'wide'`
- **D24 breakpoint boundaries (exact):** narrow `<360`; medium `360–559`; wide `≥560`.
- **What each layout actually changes in `plugin.js` render** (the only consumer switches are two-way `narrow` vs not; there is **no** code branch distinguishing `medium` from `wide` — both use 3 columns, show descriptions, and `flex-wrap` filters; D24's "wide = roomier spacing" is not implemented as a distinct branch in this file):

| Render target | narrow | medium (and wide) |
|---|---|---|
| Per-skill tool grid (`SkillRow`, ~L294) | `grid-cols-2` | `grid-cols-3` |
| Skill description | hidden (`layout !== 'narrow' && skill.description` false) | shown: `mt-0.5 truncate pl-3 text-xs text-muted-foreground`, `title={description}` (full text on hover) |
| `ToolFilter` chips (~L435) | `overflow-x-auto whitespace-nowrap`, each chip `shrink-0` (horizontal scroll) | `flex-wrap` |
| Tool-cell columns | 6 tools → **3 rows of 2** | 6 tools → **2 rows of 3** |

- Fixed pane default width is `320px` (`data.width:'320px'`) → fresh pane falls in **narrow** band by default (D24: "default pane width stays 320 px, now fully usable").
- Note asymmetry: the Skills tool grid uses the **container** `layout` value, but the MCP catalog grid is hardcoded to viewport Tailwind `grid-cols-2 sm:grid-cols-3` (~L2265) — not container-adaptive.

---

## 3. Skills/tools view — render shape (`SkillsPane`)

### Data contract consumed
`/state` returns `{ ok, hermes_home, skills_root, skills_root_exists, tools:[{id,label,present,special?,optional?,dir?}], counts:{skills,unlinked}, skills:[{id,name,category,description,tools:{<toolId>:{state,target}}}] }`.
- `tools` shown in rows/filters = `allTools.filter(tool => !tool.optional || tool.present)` (~L982) — **optional tools are excluded from rows/filters until present/created**; they surface only in Setup.
- Default backend catalog: `hermes` (config; `special:'config'`) + `claude`, `codex`, `opencode`, `grok`, `zcode` = **6 tools**.

### Vertical stack rendered by `SkillsPane` (top→bottom)
1. `header` (`px-3 pb-2 pt-3`, `flex-col gap-2`): row [`paneTitle` 'Skills' span] + right cluster [`HeaderBadges`, `Setup` ghost button, `Refresh` ghost button (disabled while `stateQuery.isFetching`)]. `HeaderBadges` (~L404) shows total `skillsCount` badge; warn `brokenCount` badge + `Repair all` button only when `diff.counts.broken > 0`; `unlinkedCount` outline badge when `> 0`.
2. `SearchField` (placeholder `'Search skills…'`), value `rawQuery`, debounced **200 ms** into `searchQuery`.
3. `ToolFilter` chips (`role="tablist"`): options `[All] + each tool.label` (`All` default).
4. `SegmentedControl` view: `all` / `issues` / `off` / `drift` (labels `All`/`Issues`/`Off`/`Drift`).
5. Optional `SetupPanel` (`showSetup` default `true` unless `setupDismissed===true` persisted).
6. Optional `ArrivalBanner` (when `arrivals.length`).
7. Optional `UndoBanner` (when an undo window is open).
8. Presets row (`px-3 pb-2`): label `Presets` + 3 built-in buttons `Coding` / `Writing` / `Minimal` + `Import…` + `Download` (file) + `Open file…` (file input) + `Copy current` (clipboard).
9. Optional preset-paste box (`showPresetImport`) with a text `Input` + `Apply`.
10. `ScrollArea` (`min-h-0 flex-1`) wrapping `body`.
11. Optional `setupNudge` footer when `allAbsent && !showSetup && state.ok`.
12. `ConfirmDialog` bound to `confirm`.

### Body resolution order (first match)
`stateQuery.isPending/loading` → **8 `Skeleton` rows** (`h-10 w-full`). Else `stateQuery.isError` → `ErrorState` (title `'Skills backend unavailable'`; if message contains `'Headless backend'`, `'web UI disabled'`, or `'Plugin not found'` shows the gateway-restart remedy text `errorNeedsRestart` + raw error string in a `max-w-[280px]` note + `Retry`; otherwise `errorDesc` + `Retry`). Else `view==='drift'` → `DriftPanel`. Else `state.ok && !skills_root_exists` → `EmptyState` `'No skills root found'`. Else `skills.length===0` → `EmptyState` `'No skills yet'`. Else `filtered.length===0` → `EmptyState` `'No skills match'`. Else the category groups (below).

### Filtering logic
- **Search** (`filtered` `useMemo`, ~L1827): `q = searchQuery.trim().toLowerCase()`; a skill is kept if `q` is empty **or** `name`/`category`/`description` contains `q`; kept skills grouped into `Map<category, skills[]>` → array of `{category, skills}`. Group order = first-seen order from the `/state` `skills` array.
- **Tool filter (`activeTool`) does NOT filter the tool cells.** The grid always renders every catalog tool (all 6) per skill. `activeTool` only (a) arms category bulk `Link all`/`Unlink all` when `activeTool !== 'all'`, and (b) is persisted. This is the §2 problem "rows omit tool columns that remain present in the top filter" observed inversely (all columns always show regardless of the selected tool chip). Harness confirms: bulk buttons present when `toolFilter='claude'`, absent under `'all'`.
- **View filter** applied per-`SkillRow` via `shown` (~L243): `all` → show; `issues` → show if **any** tool state is a problem (`broken-link`/`foreign-link`/`unmanaged-dir`, `PROBLEM_STATES`); `off` → show if **no** non-hermes tool has state `enabled` (`enabledSomewhere` explicitly excludes `hermes`). `drift` is not a row view — it is a separate panel chosen in body resolution.

### Category group (`CategoryGroup`, ~L319)
- `mb-3` `<section>`; header `sticky top-0 z-10 mb-1 … bg-background px-1 py-1` (opaque, no bleed-through on scroll) containing: uppercase category name (`0.65rem` semibold tracking-wider), outline count `Badge` (`skills.length`), and — **only when `activeTool !== 'all'`** — `ml-auto` `Link all` + `Unlink all` ghost buttons (`onBulk(ids, true/false)`). Each group closes with a `Separator`.
- Bulk `Link all`/`Unlink all` route through a `ConfirmDialog` (`bulkOnTitle`/`bulkOffTitle`, destructive when unlinking) then `bulkMutation` → `POST /toggle-bulk`.

### Skill row (`SkillRow`, ~L235)
- Row `<div>` `group rounded-md px-2 py-2`, hover `bg-(--chrome-action-hover)`, `opacity-70` when `busy`.
- Header line: `StatusDot` (tone `warn` if any problem else `good` if `enabledSomewhere` else `muted`); skill `name` (`min-w-0 truncate`, `0.8125rem` font-medium); optional `Badge` `'hermes off'` (tip `hermesOffTip`) when `skill.tools.hermes.state==='disabled'`; right cluster (`ml-auto`): `category` text + `all` button (`rowAll` → link this skill into **every present** link tool) + `none` button (`rowNone` → unlink from every present link tool), each `ghost xs` `h-4 px-1` `0.625rem`.
- Description (`text-xs`, truncated, `title` = full) shown per §2 only when not narrow and description exists.
- Tool grid: `mt-1.5 grid gap-x-2 gap-y-1 pl-3`, columns per §2 (`2` narrow else `3`); renders `tools.map` → **one `ToolCell` per catalog tool** (key `tool.id`).

### Tool cell (`ToolCell`, ~L163)
- Reads `skill.tools[tool.id].state`; missing entry → state `'missing'`.
- `checked = state==='enabled'`. Switch `size:'xs'`, `aria-label: \`${skill.name} — ${tool.label}\``.
- `locked = state==='foreign-link' || state==='unmanaged-dir'` → Switch `disabled` + cell `opacity-60` (never auto-touched).
- `broken = state==='broken-link'` → renders an extra `fix` ghost button (`onRepair` → `POST /repair`) in addition to the switch.
- Switch `disabled` also while `busy`.
- Label = `StatusDot` (tone via `dotToneFor`: `enabled`→good, `disabled`/`missing`→muted, `broken-link`→warn, else bad) + `tool.label` truncated (`0.625rem` muted).
- Whole cell wrapped in a `Tip` whose label depends on state/tool (`hermesOnTip`/`hermesOffTip`/`foreignLinkTip`/`unmanagedDirTip`/`brokenLinkTip`/`dirAbsentTip`/`linkedTip`/`unlinkTip`).

### Per-skill switch math + the 105-skill case
- **Switch cells per skill = number of tools in the filtered catalog `tools` array** (optional-but-absent excluded). Default = **6** (`hermes` + 5 link tools). One row therefore contributes 6 switches.
- Fixture (3 skills × 6 tools) = **18 switches** — harness asserts exactly `(role="switch")` count `=== 18` (L179).
- **105-skill screenshot-shaped case:** 105 skills × 6 configured tools = **630 switch cells** in one Skills page body when all six tools are present in `/state.tools`. (Exact count falls by 6 per optional-not-present tool excluded.) At 3-column layout each skill still renders its 6 switches over 2 grid rows; at narrow 2-column each renders over 3 grid rows. 105 skills → ~210 grid rows (3-col) / ~315 grid rows (2-col) of switches plus 105 row headers inside `ScrollArea`.

---

## 4. Width problems at split-window — mapping IMPLEMENTATION_PLAN §2 to source evidence

Plan §2 lists these as product defects. Current-code evidence for each:

- **"Individual rows omit tool columns that remain present in the top filter."** Tool filter chips (`activeTool`) do not hide cells; every row always draws all 6 tool cells. Filtering to one tool still shows a 6-column-per-skill row (all tools), so the active chip and the visible columns disagree.
- **"Tool labels and switches are difficult to associate."** Each cell is `justify-between` with a small truncated tool label (`0.625rem` muted) on the left and the `xs` switch on the right, all inside `gap-1` with only ~320/560px of pane width per row's 1/3 share; `aria-label` `<skill> — <tool>` exists but is not visible.
- **"Descriptions clip."** `description` uses `truncate` (single-line ellipsis) and is hidden entirely at narrow (<360). Full text only on `title` hover.
- **"Six-tool rows are unsuitable for a narrow or half-window surface."** The grid is a fixed 6-cell-per-skill matrix; at default 320px pane that is 3×2 (medium absent at default width → 2-col narrow = 3 rows of 2). There is no tool-per-skill-tab or single-tool view; the 105-skill case yields hundreds of switch cells inside a single scroll area (§3).
- **"New-skill controls, presets, filters, and setup dominate the first viewport."** `SkillsPane` renders Setup (default open), the search field, tool-filter chips, segmented view control, then presets row **before** the `ScrollArea` body. At a short/half-window split these stacked headers consume the first viewport and push the catalog below the fold. Setup also opens by default (`showSetup` from `!setupDismissed`).
- **"Users must scroll through the catalog for common whole-tool operations."** Whole-tool actions exist only as (a) per-skill `all`/`none` row buttons and (b) category bulk `Link all`/`Unlink all` that appear **only** when a specific tool is selected in the filter; there is no global "all skills for tool X" surface above the catalog.
- **"Bulk actions exist but are conditional and difficult to discover."** Bulk `Link all`/`Unlink all` are gated on `activeTool !== 'all'` (`bulkable`), live only in the sticky category header, and are hidden entirely under the default `All` filter.
- **"Advanced maintenance features compete with daily management."** Setup panel exposes dirs creation, add custom tool, auto-link toggles + regex, watch mode, blueprint export/apply, backups list/restore, and adoption scan — all in the same pane surface above the main list, open by default, in Setup + presets rows.
- **"The static render harness does not prove integration inside a real Hermes layout."** See §8.

---

## 5. MCP tab (`McpPane`, ~L2145)

- Query: `MCP_KEY` (`[ID,'mcp']`), `ctx.rest('/mcp/state')`, `staleTime:10000`, `refetchInterval:30000`, `retry:1`. `/mcp/state` returns `{ok, rows:[{name,enabled,definition,writers:{claude,codex}}], foreign:[…], counts:{catalog,foreign}, writers:{claude:{label,path,present}}}`.
- Header: title `'MCP servers'` + `mcpCount` catalog badge (`${n} in Hermes`) + `Refresh`; writer-status line (`writers.claude` present dot + `mcpWriterLine` = `'Claude Desktop — config found'` or dir-absent suffix); when `counts.foreign > 0` a `Tip`-wrapped outline badge `mcpForeignNote` (`${n} server(s) in Claude Desktop are not in the Hermes catalog (never touched)`).
- Body resolution: pending → 4 `Skeleton`s; error → `ErrorState` + `Retry`; `rows.length===0` → `EmptyState` `'No MCP servers in Hermes'`; else catalog rows.
- **Per catalog row** (rounded, hover bg, key `row.name`):
  - Header: `StatusDot` (tone `row.enabled ? (drifted?'warn':'good') : 'muted'`), `row.name`, `drifted` warn `Badge` when `claude==='drifted'`, muted `Badge` `'hermes off'` when `!row.enabled`.
  - Control grid: `mt-1.5 grid grid-cols-2 gap-2 pl-3 sm:grid-cols-3` (viewport `sm:` — not container-adaptive). Contains:
    - **Hermes** switch (`checked: row.enabled`) → on change `run(name,'/mcp/toggle',{name,enabled},next?'mcpOn':'mcpOff')`.
    - **Per writer** (`claude`, `codex`; `writers` array hardcodes labels `Claude`/`Codex` and `syncPath`/`removePath` = `/mcp/sync` & `/mcp/remove` for claude, `/mcp/codex/sync` & `/mcp/codex/remove` for codex): writer label + switch + optional action. Switch checked = writer state `'enabled'`. Change handling (L2287-2308):
      - enable, not drifted → `syncPath` (`mcpSynced`).
      - enable, drifted → `ConfirmDialog` (overwrite, non-destructive) then `syncPath`.
      - disable, not drifted → `removePath` (`mcpRemoved`).
      - disable, drifted → `ConfirmDialog` (force remove, destructive) then `removePath {force:true}`.
    - Drifted rows additionally show a `sync` secondary button next to the switch.
  - All switches disabled while any MCP mutation is busy (`busyName !== null`); `run()` uses a single `busyName` so only one row/tool busy at a time, toggles path via `ctx.rest(path,{method:'POST',body})`, toasts `t(successKey,name)`, and on settle invalidates `MCP_KEY`.
- **Foreign servers untouched:** foreign entries appear **only** as a header count/note; they are not rendered as rows and no mutation references them (mirrors backend protection; `/mcp/state.foreign` is a separate, non-mutated list).
- Harness: 3 fixture rows (`chrome-devtools`, `docs` [claude drifted], `weather` [disabled]) → asserts `9` MCP switches (3 rows × Hermes + Claude + Codex); `'drifted'` badge; `'>sync<'` action on drifted rows; `'not in the Hermes catalog'` note.

---

## 6. i18n bundle and storage keys

### i18n
- All copy routed through `const t = usePluginI18n(ID)` (`ID='skills-toggle'`). Bundle registered in `register()`: `ctx.i18n.register({ en: { … } })` with **179 top-level `en` keys** (string values and arrow functions taking `(n)`, `(tool)`, `(a,b)` positional args, e.g. `skillsCount: n => \`${n} skills\``, `toastBulk:(changed,failed)`).
- Call sites: `t('literalKey')` and `t('key', ...args)`; some keys are built dynamically, e.g. `t('watch' + Cls.charAt(0).toUpperCase() + Cls.slice(1))` → `watchArrivals`/`watchBroken`/`watchDrift`, and `t(tip)`/`t(successKey)` where the key is a variable holding a literal key name. Harness regex-scans `t('…')` uses and asserts every statically used key exists in the bundle (L163-166).
- Single `en` locale; no other locales registered in this file.

### ctx.storage keys (`storeGet`/`storeSet`, try/catch best-effort)
- `paneTab` — atom seed `storeGet('paneTab','skills')`; persisted on tab switch (`setTab`) and by palette `run`; atom `paneTabAtom` (module-level) drives the active tab.
- `toolFilter` — default `'all'`; persisted on change (effect L941). Only affects bulk-button arming (§3).
- `viewFilter` — default `'all'`; persisted on change (effect L942). Values: `all`/`issues`/`off`/`drift`.
- `seenSkills` — JSON array of skill ids seen (arrivals baseline). First-ever load adopts the whole current snapshot silently; subsequent diffs add fresh ids; linking or dismissing marks them seen (`markSkillsSeen`).
- `autoLink` — JSON prefs object `{ <toolId>: true | '' | '<categoryRegex>' }`; `true`/`''`/absent = all categories, string = case-insensitive regex on the skill category (`autoLinkMatches`; invalid regex never matches, kept as typed text). Toggled from Setup and ArrivalBanner ⚡ buttons; per-pattern text input writes a regex string.
- `setupDismissed` — bool; Setup panel default-open unless `true`.
- `watchPrefs` — JSON `{ on, arrivals, broken, drift }`; default `{ on:false, arrivals:true, broken:true, drift:true }` when null.

---

## 7. Data flow — React Query keys, REST, mutations

### Query keys (module constants, ~L62-65)
```
STATE_KEY = ['skills-toggle','state']
DIFF_KEY  = ['skills-toggle','diff']
DRIFT_KEY = ['skills-toggle','drift']
MCP_KEY   = ['skills-toggle','mcp']
```

### Queries
| Query | Key | queryFn | staleTime | refetchInterval | retry | refetchOnWindowFocus |
|---|---|---|---|---|---|---|
| `SkillsPane.stateQuery` | `STATE_KEY` | `ctx.rest('/state')` | 10 000 | 15 000 | 1 | false |
| `SkillsPane.diffQuery` | `DIFF_KEY` | `ctx.rest('/diff')` | 10 000 | 30 000 | 1 | false |
| `SkillsPane.driftQuery` | `DRIFT_KEY` | `ctx.rest('/drift')` | 30 000 | 60 000 | 0 | false |
| `HealthChip.diffQuery` | `DIFF_KEY` | `ctx.rest('/diff')` | 30 000 | 60 000 | 0 | false |
| `McpPane.stateQuery` | `MCP_KEY` | `ctx.rest('/mcp/state')` | 10 000 | 30 000 | 1 | false |

### REST endpoints called via `pluginCtx.rest(path, {method, body})`
- Reads: `/state`, `/diff`, `/drift`, `/mcp/state` (GET-style, no opts).
- Mutations:
  - `POST /toggle` `{skill, tool, enabled}` (`toggleMutation`).
  - `POST /repair` `{skill, tool}`; `POST /repair-all` `{}`.
  - `POST /toggle-bulk` `{skills:[ids], tool, enabled}` (`bulkMutation`, and every imperative runner: `runBulkEntries`, row `all`/`none`, arrival link, auto-link, presets, undo toggle-groups).
  - `POST /ensure-tool-dir` `{tool}`; `POST /config/tools` `{id,label,dir}` (add custom tool; id slugified from label).
  - `GET /import/scan` (no body) + `POST /import/apply` `{tool, names}` (adoption).
  - Conflict/drift: `POST /drift/push {tool,name}`, `POST /conflict/pull {tool,name}`, `POST /conflict/keep-both {tool,name}`, and undo reverts `POST /conflict/revert-push`, `/conflict/revert-pull`, `/conflict/revert-adopt` (payload `action`).
  - Blueprint: `GET /blueprint/export`, `POST /blueprint/apply {blueprint, dry_run:true|false}`.
  - Backups: `GET /backups`, `POST /backups/restore {path}`.
  - MCP: `POST /mcp/toggle {name,enabled}`; writer ops `POST /mcp/sync` & `/mcp/remove` (claude) and `/mcp/codex/sync` & `/mcp/codex/remove` (codex), all `{name}` plus optional `{force:true}`.

### Mutations + invalidation + optimistic rollback
- **`toggleMutation`** (per-cell switch) is the only optimistic one:
  - `onMutate`: `await qc.cancelQueries({queryKey:STATE_KEY})`; `previous = qc.getQueryData(STATE_KEY)`; `patchSkillTool(qc, vars.skill, vars.tool, vars.enabled ? 'enabled':'missing')` — i.e. optimistically the cell becomes `enabled` or `missing`, flipping the switch immediately. Returns `{previous}`.
  - `onSuccess(data,vars,ctx)`: if `data.ok !== true` → restore `previous` + error notify. Else `haptic('tap')`; if tool `'hermes'` → patch final to `enabled`/`disabled` + `toastConfig`; else → `toastLinked`/`toastUnlinked` (label looked up from `toolsRef`).
  - `onError(err,_vars,ctx)`: restore `previous`; `host.notifyError(err, 'Toggle failed — change rolled back')`.
  - `onSettled`: `invalidateQueries STATE_KEY` and `DIFF_KEY`.
- **`repairMutation` / `repairAllMutation` / `bulkMutation`** (no optimistic): toast `ok?`/error; `onSettled` invalidate `STATE_KEY` + `DIFF_KEY`.
- **Imperative bulk runner `runBulkEntries(entries, undoActions)`** (presets, row all/none, arrivals, auto-link, undo): loops entries calling `/toggle-bulk` per entry, accumulates changed/failed, `finally` invalidates `STATE_KEY`, `DIFF_KEY`, `DRIFT_KEY`, then opens a 30 s undo window (`setUndo`) and toasts.
- **Undo (`onUndo`)**: splits `undo.actions` — toggle actions regrouped by `` `${tool}|${enabled}` `` into `/toggle-bulk` entries; non-toggle revert actions (`revert-push`/`revert-pull`/`revert-adopt`) POSTed to their revert endpoints; finally re-runs `runBulkEntries` and invalidates all three keys.
- **Drift `runConflictAction(path,payload,successKey)`** (`/drift/push`, `/conflict/pull`, `/conflict/keep-both`): confirm-first; `finally` invalidates `STATE_KEY`, `DIFF_KEY`, `DRIFT_KEY`; successful push/pull/keep-both record a 30 s undo (backups passed back).
- **Auto-link** (~L1176): when arrivals exist and tools have matching prefs, sequentially `POST /toggle-bulk` per tool for matching arrivals, then `markSkillsSeen(arrivals)`, clear arrivals, toast `toastAutoLinked`, invalidate `STATE`+`DIFF`.
- **Watch mode** (~L1211): when `watchPrefs.on`, on each query tick if broken/drift counts rise, raise a native `ctx.os.notify` (fallback `host.notify`); track prior counts in a ref.
- MCP `run()` (L2178) invalidates `MCP_KEY` on settle.

---

## 8. Known limitations the render-to-string harness CANNOT catch (→ live-host checklist)

The harness (`render_harness_body.mjs` + `sdk_stub.mjs`) renders `PaneRoot` to a string via `react-dom/server` with a stubbed SDK. It verifies presence/counts/strings but not real behavior:

1. **Container width / ResizeObserver real behavior.** `usePaneLayout` reads `contentRect.width`; in node `ResizeObserver` is undefined so layout is permanently `'medium'` and every rendered grid is 3-col. Narrow vs wide rendering, real column counts at 320px, and live resize never happen in the harness. No test proves the narrow 2-col / clipped description path or the default 320px pane outcome.
2. **Real workspace split layout / CSS layout.** Assertions are string contains (`grid-cols-2`, `bg-background`, counts). Actual clipping, ellipsis, sticky-header overlap/bleed, scroll depth, header-stack-then-fold behavior at split width (plan §2) are unverified by code.
3. **`socket`.** `plugin.js` never calls `ctx.socket` (the stub provides an inert `socket:()=>()=>{}`), but no real socket/channel behavior exists to test — nothing exercises live updates outside React Query polling.
4. **Palette live atom switch.** Harness calls `palette.run()` then **re-renders**, so `useValue(paneTabAtom)` reads the already-mutated atom value; it does not prove that a **live mounted** pane re-renders on `atom.set` (real subscription), nor that `host.navigate` opens the workspace in a real router.
5. **Gateway mount / headless 404.** Harness injects crafted error strings and only checks the mapped remedy text renders (`'gateway mounts this plugin'`, `'hermes gateway restart'`). It never exercises a real `/state` 404 from a live gateway, nor the true "gateway started before plugin enabled" restart condition, nor `retry`/`refetch` against a live backend.
6. **Mutation round-trips.** `useMutation` stub always resolves `{ok:true}` by default and `ctx.rest` stubs `/state`,`/diff`; no real `/toggle`, `/toggle-bulk`, `/repair`, `/mcp/*`, blueprint, backup, import, or drift endpoints run; optimistic-rollback-on-error is never exercised against a failing backend.
7. **Confirm dialogs / undo windows / toasts / native notifications.** Dialogs only render markup when `open`; 30 s undo timers, `host.notify`, `ctx.os.notify`, clipboard, and file download (`Blob`/anchor) are all inert in node.
8. **i18n real fallback / pluralization.** Stub returns the bundle entry or the raw key; real SDK locale handling is untested.
9. **Real scroll/sticky.** `ScrollArea` is a passthrough `<div>` in the stub; 105-skill scroll and sticky-category-header behavior are not exercised.

These feed the Phase 6 live-host acceptance checklist (workspace split layout, resize behavior, live palette tab switch, gateway-restart path on a fresh enable).
