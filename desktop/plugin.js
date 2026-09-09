/**
 * skills-toggle — desktop half of the unified Hermes plugin package.
 *
 * MIT License — Copyright (c) 2026 qwertyuiop97. See LICENSE at the package root.
 *
 * A native Hermes desktop pane (plus a full-page route and a ⌘K palette
 * command) that treats ~/.hermes/skills as the single source of truth for
 * skills across coding tools (Hermes itself, Claude, Codex, OpenCode, Grok,
 * ZCode): per-tool on/off switches, broken-link repair, diff, and bulk
 * actions — all through this plugin's own backend namespace
 * (`dashboard/plugin_api.py`, reached via ctx.rest('/…')).
 *
 * Install (unified package): ~/.hermes/plugins/skills-toggle/
 *   ├── plugin.yaml            agent half (metadata)
 *   ├── dashboard/manifest.json  {"name":"skills-toggle","api":"plugin_api.py"}
 *   ├── dashboard/plugin_api.py  backend routes (gated by `plugins.enabled`)
 *   └── desktop/plugin.js      THIS FILE (enable in Settings → Plugins)
 * Then: ⌘K → "Reload desktop plugins".
 *
 * SDK constraints honored (hard failures if broken):
 *   - Plain ESM, loaded uncompiled → jsx()/jsxs() calls only, ZERO JSX syntax.
 *   - Only these import specifiers: @hermes/plugin-sdk, react, react/jsx-runtime.
 *   - Every identifier rendered inside jsx()/jsxs() is imported or defined here.
 *   - Theme variables only — no hardcoded colors or backgrounds.
 *   - Persistence only via ctx.storage. Data via React Query; mutations
 *     invalidate. Optimistic toggle updates roll back on error.
 */

import {
  host,
  haptic,
  cn,
  Button,
  Badge,
  Input,
  Switch,
  StatusDot,
  EmptyState,
  ErrorState,
  SearchField,
  Skeleton,
  ScrollArea,
  Separator,
  Tip,
  ConfirmDialog,
  SegmentedControl,
  useQuery,
  useMutation,
  useQueryClient,
  usePluginI18n,
  atom,
  useValue,
  PANES_AREA,
  ROUTES_AREA,
  PALETTE_AREA,
  STATUSBAR_AREAS
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'skills-toggle'
const STATE_KEY = [ID, 'state']
const DIFF_KEY = [ID, 'diff']
const DRIFT_KEY = [ID, 'drift']
const MCP_KEY = [ID, 'mcp']
const ccSectionAtom = atom('tools')
const arrivalsAtom = atom([])
const watchPrefsEpochAtom = atom(0)
const WORKSPACE_ID = 'skills-toggle.control-center'
let workspaceDispose = null
let bgHosted = false
const bgWaiters = new Set()

// ctx captured at register() so helpers outside components can use storage.
let pluginCtx = null

function storeGet(key, fallback) {
  try {
    return pluginCtx && pluginCtx.storage ? pluginCtx.storage.get(key, fallback) : fallback
  } catch (_err) {
    return fallback
  }
}

function storeSet(key, value) {
  try {
    if (pluginCtx && pluginCtx.storage) pluginCtx.storage.set(key, value)
  } catch (_err) {
    /* storage is best-effort persistence for view preferences */
  }
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const PROBLEM_STATES = ['broken-link', 'foreign-link', 'unmanaged-dir']

function downloadText(text, filename) {
  const blob = new Blob([text], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

function isProblemState(state) {
  return PROBLEM_STATES.indexOf(state) !== -1
}

export function presentLinkTools(state) {
  const allTools = state && state.ok && Array.isArray(state.tools) ? state.tools : []
  return allTools.filter(tool => tool.id !== 'hermes' && tool.special !== 'config' && (!tool.optional || tool.present))
}

export function countEnabledByTool(state) {
  const counts = new Map()
  const skills = state && state.ok && Array.isArray(state.skills) ? state.skills : []
  for (const skill of skills) {
    const entries = skill && skill.tools ? skill.tools : {}
    for (const toolId of Object.keys(entries)) {
      if (entries[toolId] && entries[toolId].state === 'enabled') {
        counts.set(toolId, (counts.get(toolId) || 0) + 1)
      }
    }
  }
  return counts
}

export function summaryProblemTotals(diff, drift) {
  const counts = diff && diff.ok && diff.counts ? diff.counts : {}
  return {
    broken: counts.broken || 0,
    drifted: drift && drift.ok ? drift.count || 0 : 0,
    foreign: counts.foreign || 0,
    unmanaged: counts.unmanaged || 0,
    unlinked: counts.unlinked || 0
  }
}

function openControlCenter(section = 'tools') {
  ccSectionAtom.set(section)
  if (typeof host.openWorkspace === 'function') {
    workspaceDispose = host.openWorkspace(WORKSPACE_ID, {
      title: 'Skills Control Center',
      minWidth: '680px',
      render: () => jsx(ControlCenter, {}),
      onClose: () => {
        workspaceDispose = null
      }
    })
    return
  }
  host.navigate('/skills-toggle')
}

function closeControlCenter() {
  if (!workspaceDispose) return
  workspaceDispose()
  workspaceDispose = null
}

function dotToneFor(state) {
  if (state === 'enabled') return 'good'
  if (state === 'disabled') return 'muted'
  if (state === 'missing') return 'muted'
  if (state === 'broken-link') return 'warn'
  return 'bad' // foreign-link, unmanaged-dir
}

function patchSkillTool(qc, skillId, toolId, nextState) {
  qc.setQueryData(STATE_KEY, old => {
    if (!old || !old.ok || !Array.isArray(old.skills)) return old
    return {
      ...old,
      skills: old.skills.map(s =>
        s.id === skillId
          ? { ...s, tools: { ...s.tools, [toolId]: { ...s.tools[toolId], state: nextState } } }
          : s
      )
    }
  })
}

function useDebounced(value, delay) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

// Container-measured layout: viewport media queries are wrong for dockable
// panes (a 320px pane on a 1600px screen still matches `sm:`). D24:
// narrow <360 — 2-col tool grid, no descriptions, scrollable filter chips;
// medium 360–559 — 3-col grid; wide ≥560 — 3-col with roomier spacing.
function usePaneLayout(ref) {
  const [layout, setLayout] = useState('medium')
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(entries => {
      const w = entries && entries[0] ? entries[0].contentRect.width : 0
      if (!w) return
      setLayout(w < 360 ? 'narrow' : w < 560 ? 'medium' : 'wide')
    })
    if (ref.current) ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref])
  return layout
}

// ---------------------------------------------------------------------------
// Tool switch cell — one (skill, tool) pair
// ---------------------------------------------------------------------------

function ToolCell({ skill, tool, st, onToggle, onRepair, busy }) {
  const t = usePluginI18n(ID)
  const state = st ? st.state : 'missing'
  const isHermes = tool.special === 'config'
  const checked = state === 'enabled'
  const locked = state === 'foreign-link' || state === 'unmanaged-dir'
  const broken = state === 'broken-link'

  let tip = ''
  if (isHermes) {
    tip = checked ? 'hermesOnTip' : 'hermesOffTip'
  } else if (state === 'foreign-link') {
    tip = 'foreignLinkTip'
  } else if (state === 'unmanaged-dir') {
    tip = 'unmanagedDirTip'
  } else if (broken) {
    tip = 'brokenLinkTip'
  } else if (!tool.present) {
    tip = 'dirAbsentTip'
  } else {
    tip = checked ? 'linkedTip' : 'unlinkTip'
  }

  const labelNode = jsxs('span', {
    className: 'inline-flex min-w-0 items-center gap-1',
    children: [
      jsx(StatusDot, { tone: dotToneFor(state) }),
      jsx('span', { className: 'truncate text-[0.625rem] leading-3 text-muted-foreground', children: tool.label })
    ]
  })

  const controls = jsxs('span', {
    className: 'inline-flex items-center gap-1',
    children: [
      jsx(Switch, {
        size: 'xs',
        checked: checked,
        disabled: locked || busy,
        'aria-label': `${skill.name} — ${tool.label}`,
        onCheckedChange: next => onToggle(skill, tool, next)
      }),
      broken
        ? jsx(Tip, {
            label: t('repairTip'),
            children: jsx(Button, {
              variant: 'ghost',
              size: 'xs',
              className: 'h-4 px-1 text-[0.625rem]',
              disabled: busy,
              onClick: () => onRepair(skill, tool),
              children: t('fix')
            })
          })
        : null
    ]
  })

  const cellBody = jsxs('span', {
    className: cn(
      'inline-flex min-w-0 items-center justify-between gap-1 rounded-[4px] px-1 py-0.5',
      locked && 'opacity-60'
    ),
    children: [labelNode, controls]
  })

  return jsx(Tip, { label: t(tip), children: cellBody })
}

// ---------------------------------------------------------------------------
// Skill row
// ---------------------------------------------------------------------------

function SkillRow({ skill, tools, view, activeTool, onToggle, onRepair, onRowAll, onRowNone, busy, layout }) {
  const t = usePluginI18n(ID)
  const hermes = skill.tools.hermes
  const hermesOff = hermes && hermes.state === 'disabled'
  const anyProblem = Object.keys(skill.tools).some(k => isProblemState(skill.tools[k].state))
  const enabledSomewhere = Object.keys(skill.tools).some(
    k => k !== 'hermes' && skill.tools[k].state === 'enabled'
  )
  const shown = view === 'issues' ? anyProblem : view === 'off' ? !enabledSomewhere : true
  if (!shown) return null

  const dotTone = anyProblem ? 'warn' : enabledSomewhere ? 'good' : 'muted'

  return jsxs('div', {
    className: cn(
      'group rounded-md px-2 py-2 transition-colors hover:bg-(--chrome-action-hover)',
      busy && 'opacity-70'
    ),
    children: [
      jsxs('div', {
        className: 'flex min-w-0 items-center gap-1.5',
        children: [
          jsx(StatusDot, { tone: dotTone }),
          jsx('span', { className: 'min-w-0 truncate text-[0.8125rem] font-medium', children: skill.name }),
          hermesOff
            ? jsx(Tip, {
                label: t('hermesOffTip'),
                children: jsx(Badge, { variant: 'muted', size: 'xs', children: t('hermesOffBadge') })
              })
            : null,
          jsxs('span', { className: 'ml-auto flex shrink-0 items-center gap-1', children: [
            jsx('span', { className: 'text-[0.625rem] text-muted-foreground', children: skill.category }),
            jsx(Tip, {
              label: t('rowAllTip'),
              children: jsx(Button, {
                variant: 'ghost', size: 'xs', className: 'h-4 px-1 text-[0.625rem]', disabled: busy,
                onClick: () => onRowAll(skill), children: t('rowAll')
              })
            }),
            jsx(Tip, {
              label: t('rowNoneTip'),
              children: jsx(Button, {
                variant: 'ghost', size: 'xs', className: 'h-4 px-1 text-[0.625rem]', disabled: busy,
                onClick: () => onRowNone(skill), children: t('rowNone')
              })
            })
          ] })
        ]
      }),
      skill.description
        ? jsx('div', {
            className: 'mt-0.5 whitespace-normal break-words pl-3 text-xs text-muted-foreground',
            children: skill.description
          })
        : null,
      jsxs('div', {
        className: cn(
          'mt-1.5 grid gap-x-2 gap-y-1 pl-3',
          layout === 'narrow' ? 'grid-cols-2' : 'grid-cols-3'
        ),
        children: tools.map(tool =>
          jsx(
            ToolCell,
            {
              skill: skill,
              tool: tool,
              st: skill.tools[tool.id],
              onToggle: onToggle,
              onRepair: onRepair,
              busy: busy
            },
            tool.id
          )
        )
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Category group with bulk actions
// ---------------------------------------------------------------------------

function CategoryGroup({
  category,
  skills,
  tools,
  view,
  activeTool,
  onToggle,
  onRepair,
  onBulk,
  onRowAll,
  onRowNone,
  busy,
  layout
}) {
  const t = usePluginI18n(ID)
  const bulkable = activeTool !== 'all'
  const ids = skills.map(s => s.id)

  return jsxs('section', {
    className: 'mb-3',
    children: [
      jsxs('div', {
        className: 'sticky top-0 z-10 mb-1 flex items-center gap-2 bg-background px-1 py-1',
        children: [
          jsx('span', {
            className: 'text-[0.65rem] font-semibold uppercase tracking-wider text-muted-foreground',
            children: category
          }),
          jsx(Badge, { variant: 'outline', size: 'xs', children: String(skills.length) }),
          bulkable
            ? jsxs('span', { className: 'ml-auto flex items-center gap-1', children: [
                jsx(Tip, {
                  label: t('bulkOnTip'),
                  children: jsx(Button, {
                    variant: 'ghost',
                    size: 'xs',
                    disabled: busy,
                    onClick: () => onBulk(ids, true),
                    children: t('bulkOn')
                  })
                }),
                jsx(Tip, {
                  label: t('bulkOffTip'),
                  children: jsx(Button, {
                    variant: 'ghost',
                    size: 'xs',
                    disabled: busy,
                    onClick: () => onBulk(ids, false),
                    children: t('bulkOff')
                  })
                })
              ] })
            : null
        ]
      }),
      jsxs('div', {
        className: 'flex flex-col',
        children: skills.map(skill =>
          jsx(
            SkillRow,
            {
              skill: skill,
              tools: tools,
              view: view,
              activeTool: activeTool,
              onToggle: onToggle,
              onRepair: onRepair,
              onRowAll: onRowAll,
              onRowNone: onRowNone,
              busy: busy,
              layout: layout
            },
            skill.id
          )
        )
      }),
      jsx(Separator, {})
    ]
  })
}

// ---------------------------------------------------------------------------
// Header controls
// ---------------------------------------------------------------------------

function HeaderBadges({ state, diff, onRepairAll, busy }) {
  const t = usePluginI18n(ID)
  if (!state || !state.ok) return null
  const broken = diff && diff.ok ? diff.counts.broken : 0
  return jsxs('div', {
    className: 'flex items-center gap-1.5',
    children: [
      jsx(Tip, { label: t('totalTip'), children: jsx(Badge, { variant: 'default', size: 'xs', children: t('skillsCount', state.counts.skills) }) }),
      broken > 0
        ? jsx(Tip, {
            label: t('brokenTip'),
            children: jsx(Badge, {
              variant: 'warn',
              size: 'xs',
              children: t('brokenCount', broken)
            })
          })
        : null,
      state.counts.unlinked > 0
        ? jsx(Tip, {
            label: t('unlinkedTip'),
            children: jsx(Badge, { variant: 'outline', size: 'xs', children: t('unlinkedCount', state.counts.unlinked) })
          })
        : null,
      broken > 0
        ? jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: onRepairAll, children: t('repairAll') })
        : null
    ]
  })
}

function ToolFilter({ tools, active, onChange, layout }) {
  const t = usePluginI18n(ID)
  const options = [{ id: 'all', label: t('toolAll') }].concat(
    tools.map(tool => ({ id: tool.id, label: tool.label }))
  )
  return jsx('div', {
    className: cn(
      'flex items-center gap-1',
      layout === 'narrow' ? 'overflow-x-auto whitespace-nowrap pb-0.5' : 'flex-wrap'
    ),
    role: 'tablist',
    'aria-label': 'Filter by tool',
    children: options.map(opt =>
      jsx(
        'button',
        {
          type: 'button',
          role: 'tab',
          'aria-selected': active === opt.id,
          onClick: () => onChange(opt.id),
          className: cn(
            'rounded-[4px] px-1.5 py-0.5 text-[0.6875rem] transition-colors',
            layout === 'narrow' && 'shrink-0',
            active === opt.id
              ? 'bg-primary/10 font-medium text-primary'
              : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
          ),
          children: opt.label
        },
        opt.id
      )
    )
  })
}

// ---------------------------------------------------------------------------
// v2 — presets (D20/D21), arrival + auto-link machinery (D22/D23), undo (D29)
// ---------------------------------------------------------------------------

const BUILT_IN_PRESETS = [
  { id: 'coding', label: 'Coding', catRe: /(software|devops|web|autonomous|delegating|coding)/i },
  { id: 'writing', label: 'Writing', catRe: /(creative|note-taking|email|research)/i },
  { id: 'minimal', label: 'Minimal', disableAll: true }
]

function getAutoLinkPrefs() {
  try {
    return JSON.parse(storeGet('autoLink', '{}')) || {}
  } catch (_err) {
    return {}
  }
}

// V3-7: a pref value is `true` (all categories), '' (all), or a category
// regex string. A skill matches when the pattern is empty/all or its
// category matches the pattern (case-insensitive; invalid patterns never
// match — they surface in the Setup panel as typed text, not crashes).
function autoLinkMatches(pref, category) {
  if (pref === true || pref === '' || pref === undefined) return true
  if (typeof pref !== 'string') return false
  try {
    return new RegExp(pref, 'i').test(category)
  } catch (_err) {
    return false
  }
}

function setAutoLinkPrefs(prefs) {
  storeSet('autoLink', JSON.stringify(prefs || {}))
}

function markSkillsSeen(ids) {
  let seen = []
  try {
    seen = JSON.parse(storeGet('seenSkills', '[]')) || []
  } catch (_err) {
    seen = []
  }
  const set = new Set(seen)
  for (const id of ids) set.add(id)
  storeSet('seenSkills', JSON.stringify(Array.from(set)))
}

function useBackgroundSync() {
  const t = usePluginI18n(ID)
  const qc = useQueryClient()
  const arrivals = useValue(arrivalsAtom)
  const watchPrefsEpoch = useValue(watchPrefsEpochAtom)
  const stateQuery = useQuery({
    queryKey: STATE_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/state') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 15000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const diffQuery = useQuery({
    queryKey: DIFF_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/diff') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 30000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const driftQuery = useQuery({
    queryKey: DRIFT_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/drift') : Promise.reject(new Error('no backend'))),
    staleTime: 30000,
    refetchInterval: 60000,
    refetchOnWindowFocus: false,
    retry: 0
  })
  const state = stateQuery.data
  const diff = diffQuery.data
  const drift = driftQuery.data

  useEffect(() => {
    if (!state || !state.ok || !Array.isArray(state.skills)) return
    const ids = state.skills.map(skill => skill.id)
    const rawSeen = storeGet('seenSkills', null)
    if (rawSeen === null) {
      markSkillsSeen(ids)
      return
    }
    let seen
    try {
      seen = new Set(JSON.parse(rawSeen) || [])
    } catch (_err) {
      seen = new Set(ids)
    }
    const fresh = ids.filter(id => !seen.has(id))
    if (!fresh.length) return
    arrivalsAtom.set(Array.from(new Set([...arrivalsAtom.get(), ...fresh])))
    try {
      const rawPrefs = storeGet('watchPrefs', null)
      const prefs = rawPrefs ? JSON.parse(rawPrefs) : null
      if (prefs && prefs.on && prefs.arrivals && pluginCtx && pluginCtx.os && typeof pluginCtx.os.notify === 'function') {
        pluginCtx.os.notify({ title: t('watchArrivalsTitle'), body: t('watchArrivalsBody', fresh.length) })
      }
    } catch (_err) {
      /* watch prefs are optional */
    }
  }, [state, t])

  const autoLinkRef = useRef(false)
  useEffect(() => {
    if (!arrivals.length || !state || !state.ok || autoLinkRef.current) return
    const prefs = getAutoLinkPrefs()
    const tools = presentLinkTools(state).filter(tool => prefs[tool.id] && tool.present !== false)
    if (!tools.length) return
    autoLinkRef.current = true
    const skills = Array.isArray(state.skills) ? state.skills : []
    const byId = new Map(skills.map(skill => [skill.id, skill]))
    const valid = arrivals.filter(id => byId.has(id))
    ;(async () => {
      let changed = 0
      for (const tool of tools) {
        const wanted = valid.filter(id => autoLinkMatches(prefs[tool.id], byId.get(id).category || ''))
        if (!wanted.length) continue
        try {
          const result = await pluginCtx.rest('/toggle-bulk', {
            method: 'POST',
            body: { skills: wanted, tool: tool.id, enabled: true }
          })
          if (result && result.ok) changed += result.changed || 0
        } catch (_err) {
          /* the invalidate below reconciles per-tool failures */
        }
      }
      markSkillsSeen(arrivals)
      arrivalsAtom.set([])
      autoLinkRef.current = false
      if (changed) host.notify({ kind: 'success', message: t('toastAutoLinked', changed) })
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
    })()
  }, [arrivals, state, t, qc])

  const watchRef = useRef({ initialized: false, broken: null, drift: null })
  useEffect(() => {
    let prefs = { on: false, arrivals: true, broken: true, drift: true }
    try {
      const raw = storeGet('watchPrefs', null)
      if (raw) prefs = JSON.parse(raw)
    } catch (_err) {
      /* malformed optional preference falls back to off */
    }
    if (!prefs.on) {
      watchRef.current.initialized = false
      return
    }
    const broken = diff && diff.ok ? diff.counts.broken : 0
    const driftCount = drift && drift.ok ? drift.count : 0
    if (!watchRef.current.initialized) {
      watchRef.current = { initialized: true, broken: broken, drift: driftCount }
      return
    }
    const notify = (title, body) => {
      if (pluginCtx && pluginCtx.os && typeof pluginCtx.os.notify === 'function') {
        pluginCtx.os.notify({ title: title, body: body })
      } else {
        host.notify({ kind: 'info', message: title + ' — ' + body })
      }
    }
    if (prefs.broken && broken > watchRef.current.broken) notify(t('watchBrokenTitle'), t('watchBrokenBody', broken))
    if (prefs.drift && driftCount > watchRef.current.drift) notify(t('watchDriftTitle'), t('watchDriftBody', driftCount))
    watchRef.current = { initialized: true, broken: broken, drift: driftCount }
  }, [diff, drift, t, watchPrefsEpoch])
}

function BackgroundRunner() {
  useBackgroundSync()
  return null
}

function BackgroundHost() {
  const owner = useRef(false)
  const [, rerender] = useState(0)
  if (!owner.current && !bgHosted) {
    bgHosted = true
    owner.current = true
  }
  useEffect(() => {
    const wake = () => rerender(value => value + 1)
    bgWaiters.add(wake)
    return () => {
      bgWaiters.delete(wake)
      if (owner.current) {
        owner.current = false
        bgHosted = false
        for (const waiter of bgWaiters) waiter()
      }
    }
  }, [])
  return owner.current ? jsx(BackgroundRunner, {}) : null
}

// Health chip for the status bar — always mounted, cheap polling.
function HealthChip() {
  const diffQuery = useQuery({
    queryKey: DIFF_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/diff') : Promise.reject(new Error('no backend'))),
    staleTime: 30000,
    refetchInterval: 60000,
    refetchOnWindowFocus: false,
    retry: 0
  })
  const diff = diffQuery && diffQuery.data
  const broken = diff && diff.ok ? diff.counts.broken : 0
  const unlinked = diff && diff.ok ? diff.counts.unlinked : 0
  if (diffQuery.isPending || (!broken && !unlinked)) {
    return jsx('button', {
      type: 'button',
      className: 'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover)',
      onClick: () => openControlCenter('problems'),
      children: jsx(StatusDot, { tone: 'good' })
    })
  }
  return jsx(Badge, {
    variant: broken ? 'warn' : 'outline',
    size: 'xs',
    children: jsx('button', {
      type: 'button',
      className: 'inline-flex cursor-pointer items-center gap-1',
      onClick: () => openControlCenter('problems'),
      children: broken ? `${broken} broken` : `${unlinked} unlinked`
    })
  })
}

function CompactSummaryPane() {
  const t = usePluginI18n(ID)
  const rootRef = useRef(null)
  const layout = usePaneLayout(rootRef)
  const stateQuery = useQuery({
    queryKey: STATE_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/state') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 15000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const diffQuery = useQuery({
    queryKey: DIFF_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/diff') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 30000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const driftQuery = useQuery({
    queryKey: DRIFT_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/drift') : Promise.reject(new Error('no backend'))),
    staleTime: 30000,
    refetchInterval: 60000,
    refetchOnWindowFocus: false,
    retry: 0
  })
  const state = stateQuery.data
  const linkTools = presentLinkTools(state)
  const hermes = state && state.ok && Array.isArray(state.tools)
    ? state.tools.find(tool => tool.id === 'hermes' || tool.special === 'config')
    : null
  const rows = [hermes || { id: 'hermes', label: 'Hermes', special: 'config' }, ...linkTools]
  const enabled = countEnabledByTool(state)
  const problems = summaryProblemTotals(diffQuery.data, driftQuery.data)
  const protectedProblems = problems.foreign + problems.unmanaged
  const problemTotal = problems.broken + problems.drifted + protectedProblems + problems.unlinked
  const skillList = state && state.ok && Array.isArray(state.skills) ? state.skills : []
  const rowProblems = toolId => skillList.reduce((count, skill) => {
    const entry = skill.tools && skill.tools[toolId]
    return count + (entry && isProblemState(entry.state) ? 1 : 0)
  }, 0)

  let body
  if (stateQuery.isPending || (stateQuery.isLoading && !state)) {
    body = jsx('div', {
      className: 'flex flex-col gap-2 px-3 py-2',
      children: [0, 1, 2].map(index => jsx(Skeleton, { className: 'h-8 w-full' }, `summary-${index}`))
    })
  } else if (stateQuery.isError) {
    const raw = stateQuery.error && stateQuery.error.message ? stateQuery.error.message : ''
    const needsRestart = raw.indexOf('Headless backend') !== -1 || raw.indexOf('web UI disabled') !== -1 || raw.indexOf('Plugin not found') !== -1
    body = jsx(ErrorState, {
      title: t('errorTitle'),
      description: needsRestart ? t('errorNeedsRestart') : raw || t('errorDesc'),
      children: jsxs('div', {
        className: 'flex flex-col items-center gap-2',
        children: [
          jsx(Button, {
            variant: 'secondary',
            size: 'xs',
            onClick: () => {
              stateQuery.refetch()
              diffQuery.refetch()
            },
            children: t('retry')
          }),
          needsRestart && raw
            ? jsx('span', { className: 'max-w-[280px] text-center text-[0.625rem] text-muted-foreground', children: raw })
            : null
        ]
      })
    })
  } else if (state && state.ok && !state.skills_root_exists) {
    body = jsx(EmptyState, { title: t('noRootTitle'), description: t('noRootDesc') })
  } else {
    const total = state && state.ok && state.counts ? state.counts.skills || 0 : 0
    body = jsxs('div', {
      className: cn('flex flex-col', layout === 'wide' ? 'gap-2 px-4 pb-4' : 'gap-1 px-3 pb-3'),
      children: [
        jsx('div', { className: 'text-xs text-muted-foreground', children: t('summaryLine', total, linkTools.length) }),
        total === 0 ? jsx('div', { className: 'text-xs text-muted-foreground', children: t('emptyTitle') }) : null,
        jsx('div', {
          className: 'flex flex-col gap-1 py-1',
          children: rows.map(tool => {
            const count = enabled.get(tool.id) || 0
            const issues = rowProblems(tool.id)
            return jsxs('div', {
              'data-summary-tool': tool.id,
              className: 'flex min-w-0 items-center gap-2 rounded-md border border-(--ui-stroke-secondary) px-2 py-1.5',
              children: [
                jsx(StatusDot, { tone: issues ? 'warn' : count > 0 ? 'good' : 'muted' }),
                jsx('span', { className: 'min-w-0 truncate', children: tool.label }),
                issues ? jsx(Badge, { variant: 'warn', size: 'xs', children: `${issues}!` }) : null,
                jsx('span', { className: 'ml-auto shrink-0 text-xs text-muted-foreground', children: t('enabledOn', count) })
              ]
            }, tool.id)
          })
        }),
        problemTotal > 0
          ? jsx('button', {
              type: 'button',
              className: 'flex items-center gap-2 py-1 text-left text-xs text-muted-foreground',
              onClick: () => openControlCenter('problems'),
              children: jsxs('span', {
                className: 'inline-flex items-center gap-2',
                children: [
                  jsx(StatusDot, { tone: 'warn' }),
                  t('problemLine', problems.broken, problems.drifted, protectedProblems, problems.unlinked)
                ]
              })
            })
          : null,
        jsxs('div', {
          'data-quick-actions': 'true',
          className: layout === 'narrow' ? 'flex flex-col gap-1 pt-1' : 'grid grid-cols-2 gap-1 pt-1',
          children: [
            jsx(Button, {
              variant: 'primary',
              size: 'sm',
              className: layout === 'narrow' ? 'w-full' : 'col-span-2 w-full',
              onClick: () => openControlCenter(),
              children: t('openControlCenter')
            }),
            jsx(Button, {
              variant: 'secondary',
              size: 'sm',
              className: 'w-full',
              onClick: () => openControlCenter('tools'),
              children: t('scan')
            }),
            problemTotal > 0
              ? jsx(Button, {
                  variant: 'secondary',
                  size: 'sm',
                  className: 'w-full',
                  onClick: () => openControlCenter('problems'),
                  children: t('problemsAction', problemTotal)
                })
              : null
          ]
        })
      ]
    })
  }

  return jsxs('div', {
    ref: rootRef,
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      jsx(BackgroundHost, {}),
      jsxs('div', {
        className: 'flex items-center gap-2 px-3 pb-1 pt-3',
        children: [
          jsx('span', { className: 'text-sm font-medium', children: t('paneTitle') }),
          jsx(Button, {
            variant: 'ghost',
            size: 'xs',
            className: 'ml-auto',
            disabled: stateQuery.isFetching,
            onClick: () => {
              stateQuery.refetch()
              diffQuery.refetch()
              driftQuery.refetch()
            },
            children: t('refresh')
          })
        ]
      }),
      jsx(ScrollArea, { className: 'min-h-0 flex-1', children: body })
    ]
  })
}

// Drift view (#11, D31) — same-name skills whose tool copy differs from the
// Hermes source. "Use Hermes" backs up the tool copy (never deletes) and
// swaps in the canonical symlink.
function DriftPanel({ drift, tools, onPush, onPull, onKeepBoth, busy }) {
  const t = usePluginI18n(ID)
  if (!drift || !drift.ok) {
    return jsx(EmptyState, { title: t('errorTitle'), description: t('adoptFailed') })
  }
  if (!drift.count) {
    return jsx(EmptyState, { title: t('driftEmpty'), description: t('driftDesc') })
  }
  const toolById = new Map(tools.map(x => [x.id, x]))
  return jsxs('div', {
    className: 'flex flex-col gap-2 px-3 pb-4',
    children: [
      jsx('div', { className: 'px-1 text-xs text-muted-foreground', children: t('driftDesc') }),
      drift.drifted.map(item =>
        jsxs('div', {
          className: 'rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
          children: [
            jsxs('div', { className: 'flex items-center gap-1.5', children: [
              jsx(StatusDot, { tone: 'warn' }),
              jsx('span', { className: 'font-medium', children: item.name }),
              jsx('span', {
                className: 'text-muted-foreground',
                children: (toolById.get(item.tool) || { label: item.tool }).label
              }),
              jsxs('span', { className: 'ml-auto inline-flex items-center gap-1', children: [
                jsx(Button, {
                  variant: 'secondary', size: 'xs', disabled: busy,
                  onClick: () => onPush(item), children: t('useHermes')
                }),
                jsx(Tip, {
                  label: t('useToolCopyTip'),
                  children: jsx(Button, {
                    variant: 'ghost', size: 'xs', disabled: busy,
                    onClick: () => onPull(item), children: t('useToolCopy')
                  })
                }),
                jsx(Tip, {
                  label: t('keepBothTip'),
                  children: jsx(Button, {
                    variant: 'ghost', size: 'xs', disabled: busy,
                    onClick: () => onKeepBoth(item), children: t('keepBoth')
                  })
                })
              ] })
            ] })
          ]
        },
        item.tool + '/' + item.name
      )
      )
    ]
  })
}

// Setup / onboarding panel — create tool dirs, add custom tools, manage
// auto-link prefs, and find copies to adopt. Purely user-initiated (opt-in).
function SetupPanel({
  allTools, tools, onClose, onEnsureDir, onAddTool, busy, autoLink, onAutoLink, adopt, onScanAdopt, onAdoptTool,
  watchPrefs, onWatchPref, onBlueprintExport, onBlueprintFile, blueprintPreview, onBlueprintApply,
  backups, onScanBackups, onRestoreBackup, onAutoLinkPattern
}) {
  const t = usePluginI18n(ID)
  const [label, setLabel] = useState('')
  const [dir, setDir] = useState('')
  const linkTools = tools.filter(tool => tool.special !== 'config')

  return jsxs('div', {
    className: 'mx-3 mb-2 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
    children: [
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx('span', { className: 'font-medium', children: t('setupTitle') }),
        jsx('span', { className: 'ml-auto' }),
        jsx(Button, { variant: 'ghost', size: 'xs', onClick: onClose, children: t('close') })
      ] }),
      jsx('div', { className: 'mt-1 text-muted-foreground', children: t('setupDesc') }),
      jsxs('div', { className: 'mt-2 flex flex-col gap-1', children: allTools.filter(tool => tool.special !== 'config').map(tool =>
        jsxs('div', { className: 'flex items-center gap-2', children: [
          jsx(StatusDot, { tone: tool.present ? 'good' : 'muted' }),
          jsx('span', { className: 'w-20 shrink-0 truncate', children: tool.label }),
          jsx('span', { className: 'min-w-0 flex-1 truncate text-muted-foreground', children: tool.dir || '' }),
          tool.present
            ? jsx(Badge, { variant: 'success', size: 'xs', children: t('present') })
            : jsx(Button, {
                variant: 'secondary', size: 'xs', disabled: busy,
                onClick: () => onEnsureDir(tool), children: t('createDir')
              })
        ] }, tool.id)
      ) }),
      jsxs('div', { className: 'mt-2', children: [
        jsx('div', { className: 'mb-1 text-muted-foreground', children: t('addTool') }),
        jsxs('div', { className: 'flex items-center gap-1', children: [
          jsx(Input, {
            value: label,
            onChange: e => setLabel(e && e.target ? e.target.value : e),
            placeholder: t('toolLabel'),
            className: 'h-6 w-24 text-xs'
          }),
          jsx(Input, {
            value: dir,
            onChange: e => setDir(e && e.target ? e.target.value : e),
            placeholder: t('toolDir'),
            className: 'h-6 min-w-0 flex-1 text-xs'
          }),
          jsx(Button, {
            variant: 'secondary', size: 'xs', disabled: busy || !label.trim() || !dir.trim(),
            onClick: () => { onAddTool(label, dir); setLabel(''); setDir('') },
            children: t('add')
          })
        ] })
      ] }),
      jsxs('div', { className: 'mt-2', children: [
        jsx('div', { className: 'mb-1 text-muted-foreground', children: t('autoLinkDesc') }),
        jsxs('div', { className: 'flex flex-wrap gap-1', children: linkTools.map(tool =>
          jsx('button', {
            type: 'button',
            onClick: () => onAutoLink(tool.id),
            className: cn(
              'rounded-[4px] px-1.5 py-0.5 text-[0.6875rem] transition-colors',
              autoLink[tool.id]
                ? 'bg-primary/10 font-medium text-primary'
                : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
            ),
            children: autoLink[tool.id] ? `⚡ ${tool.label}` : tool.label
          }, tool.id)
        ) })
      ] }),
      jsxs('div', { className: 'mt-2', children: [
        jsx('div', { className: 'mb-1 text-muted-foreground', children: t('watchDesc') }),
        jsxs('div', { className: 'flex flex-wrap items-center gap-1', children: [
          jsx('button', {
            type: 'button',
            onClick: () => onWatchPref('on', !watchPrefs.on),
            className: cn(
              'rounded-[4px] px-1.5 py-0.5 text-[0.6875rem] transition-colors',
              watchPrefs.on ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
            ),
            children: watchPrefs.on ? '⚡ ' + t('watchOn') : t('watchOff')
          }),
          linkTools.map(tool =>
            jsxs('span', { className: 'inline-flex items-center gap-1', children: [
              jsx('button', {
                type: 'button',
                onClick: () => onAutoLink(tool.id),
                className: cn(
                  'rounded-[4px] px-1.5 py-0.5 text-[0.6875rem] transition-colors',
                  autoLink[tool.id]
                    ? 'bg-primary/10 font-medium text-primary'
                    : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
                ),
                children: autoLink[tool.id] ? '⚡ ' + tool.label : tool.label
              }, 'al-' + tool.id),
              autoLink[tool.id]
                ? jsx('input', {
                    value: typeof autoLink[tool.id] === 'string' ? autoLink[tool.id] : '',
                    placeholder: t('autoLinkPattern'),
                    className: 'h-5 w-28 rounded-[4px] border border-(--ui-stroke-secondary) bg-transparent px-1 text-[0.625rem]',
                    onChange: e => onAutoLinkPattern(tool.id, e && e.target ? e.target.value : e)
                  }, 'alp-' + tool.id)
                : null
            ] }, 'alw-' + tool.id)
          ),
          watchPrefs.on
            ? ['arrivals', 'broken', 'drift'].map(cls =>
                jsx('button', {
                  type: 'button',
                  onClick: () => onWatchPref(cls, !watchPrefs[cls]),
                  className: cn(
                    'rounded-[4px] px-1 py-0.5 text-[0.625rem] transition-colors',
                    watchPrefs[cls] ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-(--chrome-action-hover)'
                  ),
                  children: t('watch' + cls.charAt(0).toUpperCase() + cls.slice(1))
                }, cls)
              )
            : null
        ] })
      ] }),
      jsxs('div', { className: 'mt-2', children: [
        jsx('div', { className: 'mb-1 text-muted-foreground', children: t('blueprintDesc') }),
        jsxs('div', { className: 'flex flex-wrap items-center gap-1', children: [
          jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: onBlueprintExport, children: t('blueprintExport') }),
          jsx('label', {
            className: cn(
              'cursor-pointer rounded-[4px] border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground transition-colors',
              'hover:bg-(--chrome-action-hover) hover:text-foreground',
              busy && 'opacity-50'
            ),
            children: [
              t('blueprintOpen'),
              jsx('input', {
                type: 'file',
                accept: '.json,application/json',
                className: 'hidden',
                onChange: e => {
                  const file = e && e.target && e.target.files ? e.target.files[0] : null
                  onBlueprintFile(file)
                  e.target.value = ''
                }
              }, 'blueprint-file-input')
            ]
          })
        ] }),
        blueprintPreview
          ? jsxs('div', { className: 'mt-1 text-muted-foreground', children: [
              jsx('span', { children: t('blueprintPreview', blueprintPreview.counts.links, blueprintPreview.counts.skills_disabled, blueprintPreview.counts.refused) }),
              blueprintPreview.counts.links + blueprintPreview.counts.skills_disabled > 0
                ? jsx(Button, {
                    variant: 'secondary', size: 'xs', className: 'ml-2', disabled: busy,
                    onClick: onBlueprintApply, children: t('blueprintApply')
                  })
                : null
            ] })
          : null
      ] }),
      jsxs('div', { className: 'mt-2', children: [
        jsx(Button, {
          variant: 'secondary', size: 'xs', disabled: busy,
          onClick: onScanBackups,
          children: t('backupList')
        }),
        backups && backups.ok
          ? jsxs('div', { className: 'mt-1 flex flex-col gap-0.5', children: [
              jsx('span', { className: 'text-[0.625rem] text-muted-foreground', children: t('backupCount', backups.count) }),
              backups.backups.slice(0, 12).map(b =>
                jsxs('div', { className: 'flex items-center gap-2', children: [
                  jsx('span', { className: 'min-w-0 flex-1 truncate text-muted-foreground', children: b.name }),
                  jsx(Badge, { variant: 'outline', size: 'xs', children: b.kind }),
                  jsx(Button, {
                    variant: 'ghost', size: 'xs', className: 'h-4 px-1 text-[0.625rem]', disabled: busy,
                    onClick: () => onRestoreBackup(b), children: t('backupRestore')
                  })
                ] }, b.path)
              )
            ] })
          : null
      ] }),
      jsxs('div', { className: 'mt-2', children: [
        jsx(Button, {
          variant: 'secondary', size: 'xs', disabled: busy,
          onClick: onScanAdopt,
          children: t('adoptScan')
        }),
        adopt && adopt.ok
          ? jsxs('div', { className: 'mt-1 flex flex-col gap-1', children: [
              adopt.tools
                .filter(x => x.present && (x.adoptable > 0 || x.drifted > 0))
                .map(x =>
                  jsxs('div', { className: 'flex items-center gap-2', children: [
                    jsx('span', { className: 'w-20 shrink-0 truncate', children: (tools.find(t2 => t2.id === x.tool) || { label: x.tool }).label }),
                    jsx('span', { className: 'min-w-0 flex-1 truncate text-muted-foreground', children: t('adoptToolCount', x.adoptable, x.drifted) }),
                    x.adoptable > 0
                      ? jsx(Button, {
                          variant: 'secondary', size: 'xs', disabled: busy,
                          onClick: () => onAdoptTool(x),
                          children: t('adoptAll')
                        })
                      : null
                  ] }, x.tool)
                ),
              adopt.counts.adoptable === 0 && adopt.counts.drifted === 0
                ? jsx('span', { className: 'text-muted-foreground', children: t('adoptNone') })
                : null
            ] })
          : null
      ] })
    ]
  })
}

// Arrival banner — new skills detected since last visit. Never auto-enables
// unless a tool has an explicit auto-link preference (D22/D23).
function ArrivalBanner({ arrivals, tools, autoLink, onLink, onDismiss, onAutoLink, busy }) {
  const t = usePluginI18n(ID)
  const [selected, setSelected] = useState(() => new Set())
  const linkTools = tools.filter(tool => tool.special !== 'config')
  const toggleSel = id => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const sample = arrivals.slice(0, 3).join(', ')
  return jsxs('div', {
    className: 'mx-3 mb-2 rounded-md border border-(--ui-stroke-secondary) bg-background p-2 text-xs',
    children: [
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx(StatusDot, { tone: 'good' }),
        jsx('span', { className: 'font-medium', children: t('arrivalsTitle', arrivals.length) }),
        jsx(Button, {
          variant: 'ghost', size: 'xs', className: 'ml-auto', disabled: busy,
          onClick: onDismiss, children: t('dismiss')
        })
      ] }),
      jsx('div', { className: 'mt-0.5 truncate text-muted-foreground', children: sample }),
      jsxs('div', { className: 'mt-1.5 flex flex-wrap items-center gap-1', children: [
        linkTools.map(tool =>
          jsxs('span', { className: 'inline-flex items-center gap-1', children: [
            jsx('button', {
              type: 'button',
              onClick: () => toggleSel(tool.id),
              className: cn(
                'rounded-[4px] px-1.5 py-0.5 text-[0.6875rem] transition-colors',
                selected.has(tool.id)
                  ? 'bg-primary/10 font-medium text-primary'
                  : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
              ),
              children: tool.label
            }),
            jsx(Tip, {
              label: t('alwaysAuto'),
              children: jsx('button', {
                type: 'button',
                onClick: () => onAutoLink(tool.id),
                className: cn(
                  'rounded-[4px] px-1 py-0.5 text-[0.625rem] transition-colors',
                  autoLink[tool.id]
                    ? 'bg-primary/10 font-medium text-primary'
                    : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
                ),
                children: '⚡'
              })
            })
          ] }, tool.id)
        ),
        jsx(Button, {
          variant: 'secondary', size: 'xs', className: 'ml-auto',
          disabled: busy || selected.size === 0,
          onClick: () => onLink(Array.from(selected)),
          children: t('linkChecked', arrivals.length)
        })
      ] })
    ]
  })
}

// Undo banner — 30 s window over the last bulk/preset/arrival action (D29).
function UndoBanner({ undo, onUndo, busy }) {
  const t = usePluginI18n(ID)
  if (!undo) return null
  return jsxs('div', {
    className: 'mx-3 mb-2 flex items-center gap-2 rounded-md border border-(--ui-stroke-secondary) bg-background px-2 py-1 text-xs',
    children: [
      jsx('span', { className: 'text-muted-foreground', children: t('undoAvail', undo.count) }),
      jsx(Button, {
        variant: 'secondary', size: 'xs', className: 'ml-auto', disabled: busy,
        onClick: onUndo, children: t('undo')
      })
    ]
  })
}


// ---------------------------------------------------------------------------
// Main pane
// ---------------------------------------------------------------------------

function SkillsPane({ section = 'tools' }) {
  const t = usePluginI18n(ID)
  const qc = useQueryClient()
  const rootRef = useRef(null)
  const layout = usePaneLayout(rootRef)

  const [rawQuery, setRawQuery] = useState('')
  const searchQuery = useDebounced(rawQuery, 200)
  const [activeTool, setActiveTool] = useState(() => storeGet('toolFilter', 'all'))
  const [view, setView] = useState(() => storeGet('viewFilter', 'all'))
  const [confirm, setConfirm] = useState(null)
  const arrivals = useValue(arrivalsAtom)
  const [showSetup, setShowSetup] = useState(() => storeGet('setupDismissed', false) !== true)
  const [undo, setUndo] = useState(null)
  const [autoLink, setAutoLinkState] = useState(() => getAutoLinkPrefs())
  const [adopt, setAdopt] = useState(null)
  const [showPresetImport, setShowPresetImport] = useState(false)
  const [presetText, setPresetText] = useState('')
  const [taskBusy, setTaskBusy] = useState(false)
  const [watchPrefs, setWatchPrefsState] = useState(
    () => storeGet('watchPrefs', null) || { on: false, arrivals: true, broken: true, drift: true }
  )
  const setWatchPrefs = next => {
    setWatchPrefsState(next)
    storeSet('watchPrefs', JSON.stringify(next))
    watchPrefsEpochAtom.set(watchPrefsEpochAtom.get() + 1)
  }
  const [blueprintBp, setBlueprintBp] = useState(null)
  const [blueprintPreview, setBlueprintPreview] = useState(null)
  const [backups, setBackups] = useState(null)

  useEffect(() => storeSet('toolFilter', activeTool), [activeTool])
  useEffect(() => storeSet('viewFilter', view), [view])

  // -- undo window (D29) ---------------------------------------------------
  useEffect(() => {
    if (!undo) return undefined
    const timer = setTimeout(() => setUndo(null), Math.max(0, undo.expires - Date.now()))
    return () => clearTimeout(timer)
  }, [undo])

  const stateQuery = useQuery({
    queryKey: STATE_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/state') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 15000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const diffQuery = useQuery({
    queryKey: DIFF_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/diff') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 30000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const driftQuery = useQuery({
    queryKey: DRIFT_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/drift') : Promise.reject(new Error('no backend'))),
    staleTime: 30000,
    refetchInterval: 60000,
    refetchOnWindowFocus: false,
    retry: 0
  })

  const state = stateQuery.data
  const diff = diffQuery.data
  const drift = driftQuery.data
  const allTools = state && state.ok ? state.tools : []
  // optional targets (v3) stay in Setup but stay out of the rows/filters
  // until their dir exists or is created
  const tools = allTools.filter(tool => !tool.optional || tool.present)
  const toolsRef = useRef(tools)
  toolsRef.current = tools

  // -- mutations ----------------------------------------------------------

  const toggleMutation = useMutation({
    mutationFn: vars => pluginCtx.rest('/toggle', { method: 'POST', body: vars }),
    onMutate: async vars => {
      await qc.cancelQueries({ queryKey: STATE_KEY })
      const previous = qc.getQueryData(STATE_KEY)
      patchSkillTool(qc, vars.skill, vars.tool, vars.enabled ? 'enabled' : 'missing')
      return { previous }
    },
    onSuccess: (data, vars, ctx) => {
      if (!data || data.ok !== true) {
        if (ctx && ctx.previous) qc.setQueryData(STATE_KEY, ctx.previous)
        host.notify({ kind: 'error', message: data && data.error ? data.error : t('toggleFailed') })
        return
      }
      haptic('tap')
      if (vars.tool === 'hermes') {
        patchSkillTool(qc, vars.skill, 'hermes', vars.enabled ? 'enabled' : 'disabled')
        host.notify({ kind: 'success', message: t('toastConfig', vars.enabled) })
      } else {
        const toolLabel =
          (toolsRef.current.find(x => x.id === vars.tool) || { label: vars.tool }).label
        host.notify({
          kind: 'success',
          message: vars.enabled ? t('toastLinked', toolLabel) : t('toastUnlinked', toolLabel)
        })
      }
    },
    onError: (err, _vars, ctx) => {
      if (ctx && ctx.previous) qc.setQueryData(STATE_KEY, ctx.previous)
      host.notifyError(err, t('toggleFailed'))
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
    }
  })

  const repairMutation = useMutation({
    mutationFn: vars => pluginCtx.rest('/repair', { method: 'POST', body: vars }),
    onSuccess: data => {
      if (!data || data.ok !== true) {
        host.notify({ kind: 'error', message: data && data.error ? data.error : t('repairFailed') })
        return
      }
      haptic('tap')
      host.notify({ kind: 'success', message: t('toastRepaired') })
    },
    onError: err => host.notifyError(err, t('repairFailed')),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
    }
  })

  const repairAllMutation = useMutation({
    mutationFn: () => pluginCtx.rest('/repair-all', { method: 'POST', body: {} }),
    onSuccess: data => {
      if (!data || data.ok !== true) {
        host.notify({ kind: 'error', message: t('repairFailed') })
        return
      }
      host.notify({
        kind: 'success',
        message: t('toastRepairedAll', data.fixed.length, data.unfixable.length)
      })
    },
    onError: err => host.notifyError(err, t('repairFailed')),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
    }
  })

  const bulkMutation = useMutation({
    mutationFn: vars => pluginCtx.rest('/toggle-bulk', { method: 'POST', body: vars }),
    onSuccess: data => {
      if (!data || data.ok !== true) {
        host.notify({ kind: 'error', message: t('bulkFailed') })
        return
      }
      host.notify({ kind: 'success', message: t('toastBulk', data.changed, data.failed) })
    },
    onError: err => host.notifyError(err, t('bulkFailed')),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
    }
  })

  // -- handlers -----------------------------------------------------------

  const onEnsureDir = tool => {
    setTaskBusy(true)
    pluginCtx
      .rest('/ensure-tool-dir', { method: 'POST', body: { tool: tool.id } })
      .then(res => {
        if (res && res.ok) host.notify({ kind: 'success', message: t('dirCreated', tool.label) })
        else host.notify({ kind: 'error', message: res && res.error ? res.error : t('toolAddFailed') })
      })
      .catch(err => host.notifyError(err, t('toolAddFailed')))
      .finally(() => {
        setTaskBusy(false)
        qc.invalidateQueries({ queryKey: STATE_KEY })
      })
  }

  const onToggle = useCallback(
    (skill, tool, enabled) => {
      toggleMutation.mutate({ skill: skill.id, tool: tool.id, enabled: enabled })
    },
    [toggleMutation]
  )

  const onRepair = useCallback(
    (skill, tool) => {
      repairMutation.mutate({ skill: skill.id, tool: tool.id })
    },
    [repairMutation]
  )

  const onBulk = useCallback(
    (skillIds, enabled) => {
      const toolLabel =
        (toolsRef.current.find(x => x.id === activeTool) || { label: activeTool }).label
      setConfirm({
        title: enabled ? t('bulkOnTitle', toolLabel) : t('bulkOffTitle', toolLabel),
        description: t('bulkDesc', skillIds.length, toolLabel),
        confirmLabel: enabled ? t('bulkOn') : t('bulkOff'),
        destructive: !enabled,
        action: () =>
          bulkMutation.mutate({ skills: skillIds, tool: activeTool, enabled: enabled })
      })
    },
    [activeTool, bulkMutation, t]
  )

  const onRepairAll = useCallback(() => {
    setConfirm({
      title: t('repairAllTitle'),
      description: t('repairAllDesc'),
      confirmLabel: t('repairAll'),
      destructive: false,
      action: () => repairAllMutation.mutate()
    })
  }, [repairAllMutation, t])

  // -- derived view ---------------------------------------------------------

  const skills = state && state.ok && Array.isArray(state.skills) ? state.skills : []
  const linkTools = tools.filter(tool => tool.special !== 'config')
  const busy = taskBusy || toggleMutation.isPending || repairMutation.isPending || repairAllMutation.isPending || bulkMutation.isPending

  // -- imperative bulk runner with undo capture ------------------------------
  // entries: [{ tool, ids, enabled }]; undoActions: [{ skill, tool, enabled }]
  // carrying the RESTORE polarity captured before the change.
  const runBulkEntries = useCallback(
    async (entries, undoActions) => {
      setTaskBusy(true)
      let changed = 0
      let failed = 0
      try {
        for (const entry of entries) {
          if (!entry.ids.length) continue
          try {
            const res = await pluginCtx.rest('/toggle-bulk', {
              method: 'POST',
              body: { skills: entry.ids, tool: entry.tool, enabled: entry.enabled }
            })
            if (res && res.ok) {
              changed += res.changed || 0
              failed += res.failed || 0
            } else {
              failed += entry.ids.length
            }
          } catch (_err) {
            failed += entry.ids.length
          }
        }
      } finally {
        setTaskBusy(false)
        qc.invalidateQueries({ queryKey: STATE_KEY })
        qc.invalidateQueries({ queryKey: DIFF_KEY })
        qc.invalidateQueries({ queryKey: DRIFT_KEY })
      }
      if (undoActions && undoActions.length) {
        setUndo({ actions: undoActions, count: undoActions.length, expires: Date.now() + 30000 })
      }
      host.notify({
        kind: failed ? 'error' : 'success',
        message: failed ? t('toastBulk', changed, failed) : t('toastBulkDone', changed)
      })
      return changed
    },
    [qc, t]
  )

  const onUndo = useCallback(() => {
    if (!undo) return
    const byKey = new Map()
    const reverts = []
    for (const action of undo.actions) {
      if (action.kind && action.kind !== 'toggle') {
        reverts.push(action)
        continue
      }
      const key = `${action.tool}|${action.enabled}`
      const entry = byKey.get(key) || { tool: action.tool, enabled: action.enabled, ids: [] }
      entry.ids.push(action.skill)
      byKey.set(key, entry)
    }
    setUndo(null)
    setTaskBusy(true)
    ;(async () => {
      const entries = Array.from(byKey.values())
      for (const action of reverts) {
        const path =
          action.kind === 'revert-push'
            ? '/conflict/revert-push'
            : action.kind === 'revert-pull'
              ? '/conflict/revert-pull'
              : '/conflict/revert-adopt'
        try {
          await pluginCtx.rest(path, { method: 'POST', body: action })
        } catch (_err) {
          host.notify({ kind: 'error', message: t('undoFailed') })
        }
      }
      if (entries.length) await runBulkEntries(entries)
    })().finally(() => {
      setTaskBusy(false)
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
      qc.invalidateQueries({ queryKey: DRIFT_KEY })
    })
  }, [undo, runBulkEntries, qc, t])

  // -- presets (D20/D21) ------------------------------------------------------
  const captureEnableUndo = (ids, tool) => {
    const byId = new Map(skills.map(s => [s.id, s]))
    const undoActions = []
    for (const id of ids) {
      const s = byId.get(id)
      const st = s && s.tools[tool] ? s.tools[tool].state : 'missing'
      if (st !== 'enabled') undoActions.push({ skill: id, tool: tool, enabled: false })
    }
    return undoActions
  }

  const applyEnablePreset = preset => {
    const ids = skills
      .filter(s => preset.catRe && preset.catRe.test(s.category))
      .map(s => s.id)
    const targets = linkTools.filter(tool => tool.present !== false)
    if (!ids.length || !targets.length) {
      host.notify({ kind: 'info', message: t('presetNoop') })
      return
    }
    const sample = ids.slice(0, 5).join(', ')
    setConfirm({
      title: t('presetApplyTitle', preset.label),
      description: t('presetApplyDesc', ids.length, targets.length, sample),
      confirmLabel: t('applyPreset'),
      destructive: false,
      action: () => {
        const undoActions = []
        const entries = targets.map(tool => {
          undoActions.push(...captureEnableUndo(ids, tool.id))
          return { tool: tool.id, ids: ids, enabled: true }
        })
        runBulkEntries(entries, undoActions)
      }
    })
  }

  const applyMinimalPreset = () => {
    const byId = new Map(skills.map(s => [s.id, s]))
    const entries = []
    const undoActions = []
    let total = 0
    for (const tool of linkTools) {
      if (tool.present === false) continue
      const ids = skills
        .filter(s => s.tools[tool.id] && s.tools[tool.id].state === 'enabled')
        .map(s => s.id)
      if (!ids.length) continue
      total += ids.length
      for (const id of ids) undoActions.push({ skill: id, tool: tool.id, enabled: true })
      entries.push({ tool: tool.id, ids: ids, enabled: false })
    }
    if (!total) {
      host.notify({ kind: 'info', message: t('presetNoop') })
      return
    }
    setConfirm({
      title: t('minimalTitle'),
      description: t('minimalDesc', total),
      confirmLabel: t('applyPreset'),
      destructive: true,
      action: () => runBulkEntries(entries, undoActions)
    })
  }

  const buildPresetExport = () => ({
    version: 1,
    name: 'my-skills',
    skills: skills
      .filter(sk => linkTools.some(tool => sk.tools[tool.id] && sk.tools[tool.id].state === 'enabled'))
      .map(sk => sk.id),
    tools: linkTools.map(tool => tool.id)
  })

  const downloadPresetFile = () => {
    downloadText(JSON.stringify(buildPresetExport(), null, 2), 'skills-toggle-preset.json')
    host.notify({ kind: 'success', message: t('downloaded') })
  }

  const importPresetFile = file => {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setPresetText(String(reader.result || ''))
      setShowPresetImport(true)
    }
    reader.onerror = () => host.notify({ kind: 'error', message: t('invalidPreset') })
    reader.readAsText(file)
  }

  const importPreset = () => {
    let parsed
    try {
      parsed = JSON.parse(presetText)
      if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.skills)) throw new Error('bad shape')
    } catch (_err) {
      host.notify({ kind: 'error', message: t('invalidPreset') })
      return
    }
    const known = new Set(skills.map(s => s.id))
    const ids = parsed.skills.filter(id => known.has(id))
    const skipped = parsed.skills.length - ids.length
    const knownTools = new Set(linkTools.map(tool => tool.id))
    const toolIds = (Array.isArray(parsed.tools) ? parsed.tools : []).filter(id => knownTools.has(id))
    const targets = linkTools.filter(tool => toolIds.includes(tool.id) && tool.present !== false)
    if (!ids.length || !targets.length) {
      host.notify({ kind: 'error', message: t('invalidPreset') })
      return
    }
    const sample = ids.slice(0, 5).join(', ')
    setConfirm({
      title: t('presetApplyTitle', String(parsed.name || 'preset')),
      description: t('presetImportDesc', ids.length, targets.length, skipped, sample),
      confirmLabel: t('applyPreset'),
      destructive: false,
      action: () => {
        const undoActions = []
        const entries = targets.map(tool => {
          undoActions.push(...captureEnableUndo(ids, tool.id))
          return { tool: tool.id, ids: ids, enabled: true }
        })
        runBulkEntries(entries, undoActions)
        setPresetText('')
        setShowPresetImport(false)
      }
    })
  }

  const exportPreset = async () => {
    const text = JSON.stringify(buildPresetExport(), null, 2)
    try {
      if (pluginCtx && pluginCtx.os && typeof pluginCtx.os.writeClipboard === 'function') {
        const okDone = await pluginCtx.os.writeClipboard(text)
        host.notify({ kind: okDone ? 'success' : 'error', message: okDone ? t('copied') : t('copyFailed') })
        return
      }
    } catch (_err) {
      /* fall through to error toast */
    }
    host.notify({ kind: 'error', message: t('copyFailed') })
  }

  // -- setup / adoption (D26, #10) --------------------------------------------
  const onAddTool = (label, dir) => {
    setTaskBusy(true)
    pluginCtx
      .rest('/config/tools', {
        method: 'POST',
        body: { id: label.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32), label: label, dir: dir }
      })
      .then(res => {
        if (res && res.ok) host.notify({ kind: 'success', message: t('toolAdded', label) })
        else host.notify({ kind: 'error', message: res && res.error ? res.error : t('toolAddFailed') })
      })
      .catch(err => host.notifyError(err, t('toolAddFailed')))
      .finally(() => {
        setTaskBusy(false)
        qc.invalidateQueries({ queryKey: STATE_KEY })
      })
  }

  const onScanAdopt = () => {
    setTaskBusy(true)
    pluginCtx
      .rest('/import/scan')
      .then(res => setAdopt(res))
      .catch(err => host.notifyError(err, t('adoptFailed')))
      .finally(() => setTaskBusy(false))
  }

  const onAdopt = scanResult => {
    const targets = scanResult.tools.filter(tool => tool.adoptable > 0)
    const sample = targets
      .flatMap(tool => tool.entries.filter(e => e.kind === 'unmanaged-skill').slice(0, 2).map(e => `${tool.tool}/${e.name}`))
      .slice(0, 5)
      .join(', ')
    setConfirm({
      title: t('adoptConfirmTitle'),
      description: t('adoptConfirmDesc', scanResult.counts.adoptable, sample),
      confirmLabel: t('adoptAll'),
      destructive: false,
      action: () => {
        setTaskBusy(true)
        ;(async () => {
          let adopted = 0
          for (const target of targets) {
            try {
              const res = await pluginCtx.rest('/import/apply', {
                method: 'POST',
                body: {
                  tool: target.tool,
                  names: target.entries.filter(e => e.kind === 'unmanaged-skill' && !e.conflict).map(e => e.name)
                }
              })
              if (res && res.ok) adopted += res.adopted || 0
            } catch (_err) {
              /* reported per-run by the summary toast */
            }
          }
          host.notify({ kind: 'success', message: t('adoptDone', adopted) })
        })().finally(() => {
          setTaskBusy(false)
          qc.invalidateQueries({ queryKey: STATE_KEY })
          qc.invalidateQueries({ queryKey: DIFF_KEY })
        })
      }
    })
  }

  const onAdoptTool = toolScan => {
    const names = toolScan.entries
      .filter(e => e.kind === 'unmanaged-skill' && !e.conflict)
      .map(e => e.name)
    if (!names.length) {
      host.notify({ kind: 'info', message: t('presetNoop') })
      return
    }
    const sample = names.slice(0, 5).join(', ')
    setConfirm({
      title: t('adoptConfirmTitle'),
      description: t('adoptConfirmDesc', names.length, sample),
      confirmLabel: t('adoptAll'),
      destructive: false,
      action: () => {
        setTaskBusy(true)
        pluginCtx
          .rest('/import/apply', { method: 'POST', body: { tool: toolScan.tool, names: names } })
          .then(res => {
            if (res && res.ok) {
              host.notify({ kind: 'success', message: t('adoptDone', res.adopted || 0) })
              const undoActions = (res.results || [])
                .filter(x => x.ok && x.backup)
                .map(x => ({ kind: 'revert-adopt', tool: toolScan.tool, name: x.name, tool_backup: x.backup, skill: x.skill }))
              if (undoActions.length) {
                setUndo({ actions: undoActions, count: undoActions.length, expires: Date.now() + 30000 })
              }
            } else {
              host.notify({ kind: 'error', message: res && res.error ? res.error : t('adoptFailed') })
            }
          })
          .catch(err => host.notifyError(err, t('adoptFailed')))
          .finally(() => {
            setTaskBusy(false)
            qc.invalidateQueries({ queryKey: STATE_KEY })
            qc.invalidateQueries({ queryKey: DIFF_KEY })
            onScanAdopt()
          })
      }
    })
  }

  const runConflictAction = (path, payload, successKey) => {
    setTaskBusy(true)
    return pluginCtx
      .rest(path, { method: 'POST', body: payload })
      .then(res => {
        if (res && res.ok) host.notify({ kind: 'success', message: t(successKey, payload.name) })
        else host.notify({ kind: 'error', message: res && res.error ? res.error : t('pushFailed') })
        return res
      })
      .catch(err => {
        host.notifyError(err, t('pushFailed'))
        return null
      })
      .finally(() => {
        setTaskBusy(false)
        qc.invalidateQueries({ queryKey: STATE_KEY })
        qc.invalidateQueries({ queryKey: DIFF_KEY })
        qc.invalidateQueries({ queryKey: DRIFT_KEY })
      })
  }

  const onPushDrift = item => {
    setConfirm({
      title: t('useHermesTitle', item.name),
      description: t('useHermesDesc', item.tool),
      confirmLabel: t('useHermes'),
      destructive: false,
      action: () => {
        runConflictAction('/drift/push', { tool: item.tool, name: item.name }, 'pushDone').then(res => {
          if (res && res.ok && res.tool_backup) {
            setUndo({
              actions: [{ kind: 'revert-push', tool: item.tool, name: item.name, tool_backup: res.tool_backup }],
              count: 1,
              expires: Date.now() + 30000
            })
          }
        })
      }
    })
  }

  const onPullDrift = item => {
    setConfirm({
      title: t('useToolCopyTitle', item.name, item.tool),
      description: t('useToolCopyDesc'),
      confirmLabel: t('useToolCopy'),
      destructive: true,
      action: () => {
        runConflictAction('/conflict/pull', { tool: item.tool, name: item.name }, 'pullDone').then(res => {
          if (res && res.ok) {
            setUndo({
              actions: [{
                kind: 'revert-pull', tool: item.tool, name: item.name,
                hermes_backup: res.hermes_backup, tool_backup: res.tool_backup
              }],
              count: 1,
              expires: Date.now() + 30000
            })
          }
        })
      }
    })
  }

  const onKeepBothDrift = item => {
    setConfirm({
      title: t('keepBothTitle', item.name),
      description: t('keepBothDesc'),
      confirmLabel: t('keepBoth'),
      destructive: false,
      action: () => {
        runConflictAction('/conflict/keep-both', { tool: item.tool, name: item.name }, 'keepBothDone').then(res => {
          if (res && res.ok) {
            setUndo({
              actions: [{ kind: 'revert-adopt', tool: item.tool, name: item.name, tool_backup: res.tool_backup, skill: res.skill }],
              count: 1,
              expires: Date.now() + 30000
            })
          }
        })
      }
    })
  }

  // -- machine blueprint (v3-2, additive-only) --------------------------------
  const onBlueprintExport = () => {
    setTaskBusy(true)
    pluginCtx
      .rest('/blueprint/export')
      .then(res => {
        if (res && res.ok) {
          downloadText(JSON.stringify(res.blueprint, null, 2), 'skills-toggle-blueprint.json')
          host.notify({ kind: 'success', message: t('blueprintExported') })
        } else host.notify({ kind: 'error', message: t('blueprintFailed') })
      })
      .catch(err => host.notifyError(err, t('blueprintFailed')))
      .finally(() => setTaskBusy(false))
  }

  const onBlueprintFile = file => {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      let bp
      try {
        bp = JSON.parse(String(reader.result || ''))
        if (!bp || bp.version !== 2) throw new Error('version')
      } catch (_err) {
        host.notify({ kind: 'error', message: t('blueprintInvalid') })
        return
      }
      setBlueprintBp(bp)
      setTaskBusy(true)
      pluginCtx
        .rest('/blueprint/apply', { method: 'POST', body: { blueprint: bp, dry_run: true } })
        .then(res => {
          if (res && res.ok) setBlueprintPreview(res.plan)
          else host.notify({ kind: 'error', message: t('blueprintFailed') })
        })
        .catch(err => host.notifyError(err, t('blueprintFailed')))
        .finally(() => setTaskBusy(false))
    }
    reader.readAsText(file)
  }

  const onBlueprintApply = () => {
    if (!blueprintBp) return
    setConfirm({
      title: t('blueprintApplyTitle'),
      description: t('blueprintApplyDesc', blueprintPreview.counts.links, blueprintPreview.counts.skills_disabled),
      confirmLabel: t('blueprintApply'),
      destructive: false,
      action: () => {
        setTaskBusy(true)
        pluginCtx
          .rest('/blueprint/apply', { method: 'POST', body: { blueprint: blueprintBp, dry_run: false } })
          .then(res => {
            if (res && res.ok) {
              host.notify({ kind: 'success', message: t('blueprintApplied', res.applied.links, res.applied.skills_disabled, res.applied.failed) })
              setBlueprintBp(null)
              setBlueprintPreview(null)
            } else host.notify({ kind: 'error', message: res && res.error ? res.error : t('blueprintFailed') })
          })
          .catch(err => host.notifyError(err, t('blueprintFailed')))
          .finally(() => {
            setTaskBusy(false)
            qc.invalidateQueries({ queryKey: STATE_KEY })
            qc.invalidateQueries({ queryKey: DIFF_KEY })
          })
      }
    })
  }

  // -- backups (v3-6) ----------------------------------------------------------
  const onScanBackups = () => {
    setTaskBusy(true)
    pluginCtx
      .rest('/backups')
      .then(res => setBackups(res))
      .catch(err => host.notifyError(err, t('backupScanFailed')))
      .finally(() => setTaskBusy(false))
  }

  const onRestoreBackup = backup => {
    setConfirm({
      title: t('backupRestoreTitle'),
      description: t('backupRestoreDesc', backup.name),
      confirmLabel: t('backupRestore'),
      destructive: false,
      action: () => {
        setTaskBusy(true)
        pluginCtx
          .rest('/backups/restore', { method: 'POST', body: { path: backup.path } })
          .then(res => {
            if (res && res.ok) host.notify({ kind: 'success', message: t('backupRestored', backup.name) })
            else host.notify({ kind: 'error', message: res && res.error ? res.error : t('backupScanFailed') })
          })
          .catch(err => host.notifyError(err, t('backupScanFailed')))
          .finally(() => {
            setTaskBusy(false)
            qc.invalidateQueries({ queryKey: STATE_KEY })
            onScanBackups()
          })
      }
    })
  }

  const onArrivalLink = toolIds => {
    const byId = new Map(skills.map(s => [s.id, s]))
    const undoActions = []
    const entries = toolIds
      .filter(id => linkTools.some(tool => tool.id === id))
      .map(toolId => {
        undoActions.push(...captureEnableUndo(arrivals, toolId))
        return { tool: toolId, ids: arrivals.slice(), enabled: true }
      })
    markSkillsSeen(arrivals)
    arrivalsAtom.set([])
    runBulkEntries(entries, undoActions)
  }

  const onArrivalDismiss = () => {
    markSkillsSeen(arrivals)
    arrivalsAtom.set([])
  }

  const onToggleAutoLink = toolId => {
    const prefs = getAutoLinkPrefs()
    if (prefs[toolId]) delete prefs[toolId]
    else prefs[toolId] = true
    setAutoLinkPrefs(prefs)
    setAutoLinkState({ ...prefs })
  }

  const onAutoLinkPattern = (toolId, pattern) => {
    const prefs = getAutoLinkPrefs()
    prefs[toolId] = pattern.trim() === '' ? true : pattern
    setAutoLinkPrefs(prefs)
    setAutoLinkState({ ...prefs })
  }

  // per-skill all/none across every present link tool (#5)
  const onRowAll = useCallback(
    skill => {
      const targets = linkTools.filter(tool => tool.present !== false)
      const undoActions = []
      const entries = targets.map(tool => {
        undoActions.push(...captureEnableUndo([skill.id], tool.id))
        return { tool: tool.id, ids: [skill.id], enabled: true }
      })
      runBulkEntries(entries, undoActions)
    },
    [linkTools, captureEnableUndo, runBulkEntries]
  )

  const onRowNone = useCallback(
    skill => {
      const entries = []
      const undoActions = []
      for (const tool of linkTools) {
        if (tool.present === false) continue
        const st = skill.tools[tool.id]
        if (st && (st.state === 'enabled' || st.state === 'broken-link')) {
          undoActions.push({ skill: skill.id, tool: tool.id, enabled: true })
          entries.push({ tool: tool.id, ids: [skill.id], enabled: false })
        }
      }
      runBulkEntries(entries, undoActions)
    },
    [linkTools, runBulkEntries]
  )

  const filtered = useMemo(() => {

    const q = searchQuery.trim().toLowerCase()
    const groups = new Map()
    for (const skill of skills) {
      if (
        q &&
        skill.name.toLowerCase().indexOf(q) === -1 &&
        skill.category.toLowerCase().indexOf(q) === -1 &&
        (skill.description || '').toLowerCase().indexOf(q) === -1
      ) {
        continue
      }
      if (!groups.has(skill.category)) groups.set(skill.category, [])
      groups.get(skill.category).push(skill)
    }
    return Array.from(groups.entries()).map(([category, list]) => ({ category, skills: list }))
  }, [skills, searchQuery])

  // -- render ---------------------------------------------------------------

  const header = jsxs('div', {
    className: 'flex flex-col gap-2 px-3 pb-2 pt-3',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('span', { className: 'text-sm font-medium', children: t('paneTitle') }),
          jsxs('div', { className: 'ml-auto flex items-center gap-1.5', children: [
            jsx(HeaderBadges, {
              state: state,
              diff: diff,
              onRepairAll: onRepairAll,
              busy: busy
            }),
            jsx(Button, {
              variant: 'ghost',
              size: 'xs',
              onClick: () => setShowSetup(v => !v),
              children: t('setup')
            }),
            jsx(Button, {
              variant: 'ghost',
              size: 'xs',
              disabled: stateQuery.isFetching,
              onClick: () => {
                qc.invalidateQueries({ queryKey: STATE_KEY })
                qc.invalidateQueries({ queryKey: DIFF_KEY })
              },
              children: t('refresh')
            })
          ] })
        ]
      }),
      jsx(SearchField, {
        placeholder: t('searchPlaceholder'),
        value: rawQuery,
        onChange: setRawQuery,
        containerClassName: 'w-full',
        'aria-label': t('searchPlaceholder')
      }),
      jsx(ToolFilter, { tools: tools, active: activeTool, onChange: setActiveTool, layout: layout }),
      jsx(SegmentedControl, {
        options: [
          { id: 'all', label: t('viewAll') },
          { id: 'issues', label: t('viewIssues') },
          { id: 'off', label: t('viewOff') },
          { id: 'drift', label: t('viewDrift') }
        ],
        value: view,
        onChange: setView
      })
    ]
  })

  let body = null
  if (stateQuery.isPending || (stateQuery.isLoading && !state)) {
    body = jsxs('div', {
      className: 'flex flex-col gap-2 px-3',
      children: [0, 1, 2, 3, 4, 5, 6, 7].map(i =>
        jsx(Skeleton, { className: 'h-10 w-full' }, `sk-${i}`)
      )
    })
  } else if (stateQuery.isError) {
    const raw = stateQuery.error && stateQuery.error.message ? stateQuery.error.message : ''
    // Map known backend-mount failures to their exact remedy (see README
    // troubleshooting): the gateway mounts plugin API routes at startup only.
    const needsRestart =
      raw.indexOf('Headless backend') !== -1 ||
      raw.indexOf('web UI disabled') !== -1 ||
      raw.indexOf('Plugin not found') !== -1
    body = jsx(ErrorState, {
      title: t('errorTitle'),
      description: needsRestart ? t('errorNeedsRestart') : raw || t('errorDesc'),
      children: jsxs('div', {
        className: 'flex flex-col items-center gap-2',
        children: [
          jsx(Button, {
            variant: 'secondary',
            size: 'xs',
            onClick: () => stateQuery.refetch(),
            children: t('retry')
          }),
          needsRestart && raw
            ? jsx('span', {
                className: 'max-w-[280px] text-center text-[0.625rem] text-muted-foreground',
                children: raw
              })
            : null
        ]
      })
    })
  } else if (view === 'drift') {
    body = jsx(DriftPanel, {
      drift: drift,
      tools: tools,
      onPush: onPushDrift,
      onPull: onPullDrift,
      onKeepBoth: onKeepBothDrift,
      busy: busy
    })
  } else if (state && state.ok && !state.skills_root_exists) {
    body = jsx(EmptyState, { title: t('noRootTitle'), description: t('noRootDesc') })
  } else if (skills.length === 0) {
    body = jsx(EmptyState, { title: t('emptyTitle'), description: t('emptyDesc') })
  } else if (filtered.length === 0) {
    body = jsx(EmptyState, { title: t('noMatchTitle'), description: t('noMatchDesc') })
  } else {
    body = jsxs('div', {
      className: 'flex flex-col px-2 pb-4',
      children: filtered.map(group =>
        jsx(
          CategoryGroup,
          {
            category: group.category,
            skills: group.skills,
            tools: tools,
            view: view,
            activeTool: activeTool,
            onToggle: onToggle,
            onRepair: onRepair,
            onBulk: onBulk,
            onRowAll: onRowAll,
            onRowNone: onRowNone,
            busy: busy,
            layout: layout
          },
          group.category
        )
      )
    })
  }

  const allAbsent = linkTools.length > 0 && linkTools.every(tool => tool.present === false)

  const sharedConfirm = jsx(ConfirmDialog, {
    open: !!confirm,
    onClose: () => setConfirm(null),
    onConfirm: confirm ? confirm.action : () => undefined,
    title: confirm ? confirm.title : '',
    description: confirm ? confirm.description : undefined,
    confirmLabel: confirm ? confirm.confirmLabel : undefined,
    destructive: confirm ? confirm.destructive : false
  })

  if (section === 'sets') {
    const presetControls = jsxs('div', {
      className: 'flex flex-wrap items-center gap-2 p-3',
      children: [
        jsx('span', { className: 'mr-1 text-xs font-medium', children: t('presets') }),
        BUILT_IN_PRESETS.map(preset =>
          jsx('button', {
            type: 'button',
            onClick: () => (preset.disableAll ? applyMinimalPreset() : applyEnablePreset(preset)),
            disabled: busy,
            className: 'rounded-[4px] border border-(--ui-stroke-secondary) px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
            children: preset.label
          }, preset.id)
        ),
        jsx(Button, {
          variant: 'secondary', size: 'xs', disabled: busy,
          onClick: () => setShowPresetImport(value => !value), children: t('presetImport')
        }),
        jsx(Button, {
          variant: 'secondary', size: 'xs', disabled: busy,
          onClick: downloadPresetFile, children: t('downloadPreset')
        }),
        jsx('label', {
          className: cn(
            'cursor-pointer rounded-[4px] border border-(--ui-stroke-secondary) px-2 py-1 text-xs text-muted-foreground transition-colors',
            'hover:bg-(--chrome-action-hover) hover:text-foreground',
            busy && 'opacity-50'
          ),
          children: [
            t('importFile'),
            jsx('input', {
              type: 'file',
              accept: '.json,application/json',
              className: 'hidden',
              onChange: event => {
                const file = event && event.target && event.target.files ? event.target.files[0] : null
                importPresetFile(file)
                event.target.value = ''
              }
            }, 'sets-preset-file')
          ]
        }),
        jsx(Button, {
          variant: 'secondary', size: 'xs', disabled: busy,
          onClick: exportPreset, children: t('copyPreset')
        })
      ]
    })
    return jsxs('div', {
      className: 'flex h-full min-w-0 flex-col text-sm',
      children: [
        presetControls,
        showPresetImport
          ? jsxs('div', {
              className: 'mx-3 mb-2 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
              children: [
                jsx('div', { className: 'mb-1 text-muted-foreground', children: t('pasteHint') }),
                jsx(Input, {
                  value: presetText,
                  onChange: event => setPresetText(event && event.target ? event.target.value : event),
                  placeholder: '{"version": 1, "name": "…", "skills": ["…"], "tools": ["…"]}',
                  className: 'h-6 w-full text-xs'
                }),
                jsx(Button, {
                  variant: 'secondary', size: 'xs', disabled: busy || !presetText.trim(),
                  onClick: importPreset, children: t('applyPreset')
                })
              ]
            })
          : null,
        jsx(ScrollArea, {
          className: 'min-h-0 flex-1',
          children: jsx(EmptyState, { title: t('ccSets'), description: t('toolsLandingHint') })
        }),
        jsx(UndoBanner, { undo: undo, onUndo: onUndo, busy: busy }),
        sharedConfirm
      ]
    })
  }

  if (section === 'problems') {
    return jsxs('div', {
      className: 'flex h-full min-w-0 flex-col text-sm',
      children: [
        jsx(ScrollArea, {
          className: 'min-h-0 flex-1',
          children: jsx(DriftPanel, {
            drift: drift,
            tools: tools,
            onPush: onPushDrift,
            onPull: onPullDrift,
            onKeepBoth: onKeepBothDrift,
            busy: busy
          })
        }),
        jsx(UndoBanner, { undo: undo, onUndo: onUndo, busy: busy }),
        sharedConfirm
      ]
    })
  }

  if (section === 'advanced') {
    return jsxs('div', {
      className: 'flex h-full min-w-0 flex-col text-sm',
      children: [
        jsx(ScrollArea, {
          className: 'min-h-0 flex-1',
          children: jsx(SetupPanel, {
            tools: tools,
            onClose: () => ccSectionAtom.set('tools'),
            onEnsureDir: onEnsureDir,
            onAddTool: onAddTool,
            busy: busy,
            autoLink: autoLink,
            onAutoLink: onToggleAutoLink,
            allTools: allTools,
            adopt: adopt,
            onScanAdopt: onScanAdopt,
            onAdoptTool: onAdoptTool,
            onAutoLinkPattern: onAutoLinkPattern,
            watchPrefs: watchPrefs,
            onWatchPref: (cls, value) => {
              const next = { ...watchPrefs, [cls]: value }
              setWatchPrefs(next)
            },
            onBlueprintExport: onBlueprintExport,
            onBlueprintFile: onBlueprintFile,
            blueprintPreview: blueprintPreview,
            onBlueprintApply: onBlueprintApply,
            backups: backups,
            onScanBackups: onScanBackups,
            onRestoreBackup: onRestoreBackup
          })
        }),
        arrivals.length
          ? jsx(ArrivalBanner, {
              arrivals: arrivals,
              tools: tools,
              autoLink: autoLink,
              onLink: onArrivalLink,
              onDismiss: onArrivalDismiss,
              onAutoLink: onToggleAutoLink,
              busy: busy
            })
          : null,
        jsx(UndoBanner, { undo: undo, onUndo: onUndo, busy: busy }),
        sharedConfirm
      ]
    })
  }

  if (section === 'sets') {
    return jsxs('div', {
      className: 'flex h-full min-w-0 flex-col gap-2 p-3 text-sm',
      children: [
        jsx('div', { className: 'text-xs text-muted-foreground', children: t('toolsLandingHint') }),
        jsxs('div', {
          className: 'flex flex-wrap items-center gap-1',
          children: [
            jsx('span', { className: 'mr-1 text-[0.625rem] uppercase tracking-wider text-muted-foreground', children: t('presets') }),
            BUILT_IN_PRESETS.map(preset =>
              jsx('button', {
                type: 'button',
                onClick: () => (preset.disableAll ? applyMinimalPreset() : applyEnablePreset(preset)),
                disabled: busy,
                className: 'rounded-[4px] border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground',
                children: preset.label
              }, preset.id)
            ),
            jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => setShowPresetImport(v => !v), children: t('presetImport') }),
            jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: downloadPresetFile, children: t('downloadPreset') }),
            jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: exportPreset, children: t('copyPreset') })
          ]
        }),
        showPresetImport
          ? jsxs('div', {
              className: 'rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
              children: [
                jsx(Input, { value: presetText, onChange: e => setPresetText(e && e.target ? e.target.value : e), placeholder: t('pasteHint'), className: 'h-6 w-full text-xs' }),
                jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy || !presetText.trim(), onClick: importPreset, children: t('applyPreset') })
              ]
            })
          : null,
        jsx(UndoBanner, { undo: undo, onUndo: onUndo, busy: busy }),
        sharedConfirm
      ]
    })
  }

  return jsxs('div', {
    ref: rootRef,
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      header,
      showSetup
        ? jsx(SetupPanel, {
            tools: tools,
            onClose: () => {
              setShowSetup(false)
              storeSet('setupDismissed', true)
            },
            onEnsureDir: onEnsureDir,
            onAddTool: onAddTool,
            busy: busy,
            autoLink: autoLink,
            onAutoLink: onToggleAutoLink,
            allTools: allTools,
            adopt: adopt,
            onScanAdopt: onScanAdopt,
            onAdoptTool: onAdoptTool,
            onAutoLinkPattern: onAutoLinkPattern,
            watchPrefs: watchPrefs,
            onWatchPref: (cls, value) => {
              const next = { ...watchPrefs, [cls]: value }
              setWatchPrefs(next)
            },
            onBlueprintExport: onBlueprintExport,
            onBlueprintFile: onBlueprintFile,
            blueprintPreview: blueprintPreview,
            onBlueprintApply: onBlueprintApply,
            backups: backups,
            onScanBackups: onScanBackups,
            onRestoreBackup: onRestoreBackup
          })
        : null,
      arrivals.length
        ? jsx(ArrivalBanner, {
            arrivals: arrivals,
            tools: tools,
            autoLink: autoLink,
            onLink: onArrivalLink,
            onDismiss: onArrivalDismiss,
            onAutoLink: onToggleAutoLink,
            busy: busy
          })
        : null,
      jsx(UndoBanner, { undo: undo, onUndo: onUndo, busy: busy }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-1 px-3 pb-2',
        children: [
          jsx('span', { className: 'mr-1 text-[0.625rem] uppercase tracking-wider text-muted-foreground', children: t('presets') }),
          BUILT_IN_PRESETS.map(preset =>
            jsx(
              'button',
              {
                type: 'button',
                onClick: () => (preset.disableAll ? applyMinimalPreset() : applyEnablePreset(preset)),
                disabled: busy,
                className: cn(
                  'rounded-[4px] border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.6875rem] transition-colors',
                  'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
                ),
                children: preset.label
              },
              preset.id
            )
          ),
          jsx('button', {
            type: 'button',
            onClick: () => setShowPresetImport(v => !v),
            disabled: busy,
            className: 'rounded-[4px] border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
            children: t('presetImport')
          }),
          jsx('button', {
            type: 'button',
            onClick: downloadPresetFile,
            disabled: busy,
            className: 'rounded-[4px] border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
            children: t('downloadPreset')
          }),
          jsx('label', {
            className: cn(
              'cursor-pointer rounded-[4px] border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground transition-colors',
              'hover:bg-(--chrome-action-hover) hover:text-foreground',
              busy && 'opacity-50'
            ),
            children: [
              t('importFile'),
              jsx('input', {
                type: 'file',
                accept: '.json,application/json',
                className: 'hidden',
                onChange: e => {
                  const file = e && e.target && e.target.files ? e.target.files[0] : null
                  importPresetFile(file)
                  e.target.value = ''
                }
              }, 'preset-file-input')
            ]
          }),
          jsx('button', {
            type: 'button',
            onClick: exportPreset,
            disabled: busy,
            className: 'rounded-[4px] border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
            children: t('copyPreset')
          })
        ]
      }),
      showPresetImport
        ? jsxs('div', {
            className: 'mx-3 mb-2 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
            children: [
              jsx('div', { className: 'mb-1 text-muted-foreground', children: t('pasteHint') }),
              jsx(Input, {
                value: presetText,
                onChange: e => setPresetText(e && e.target ? e.target.value : e),
                placeholder: '{"version": 1, "name": "…", "skills": ["…"], "tools": ["…"]}',
                className: 'h-6 w-full text-xs'
              }),
              jsx('div', { className: 'mt-1 flex justify-end' }),
              jsx(Button, {
                variant: 'secondary',
                size: 'xs',
                disabled: busy || !presetText.trim(),
                onClick: importPreset,
                children: t('applyPreset')
              })
            ]
          })
        : null,
      jsx(ScrollArea, { className: 'min-h-0 flex-1', children: body }),
      allAbsent && !showSetup && state && state.ok
        ? jsxs('div', {
            className: 'mx-3 mb-3 flex items-center gap-2 rounded-md border border-(--ui-stroke-secondary) px-2 py-1.5 text-xs',
            children: [
              jsx('span', { className: 'text-muted-foreground', children: t('setupNudge') }),
              jsx(Button, {
                variant: 'secondary', size: 'xs', className: 'ml-auto',
                onClick: () => setShowSetup(true), children: t('setup')
              })
            ]
          })
        : null,
      jsx(ConfirmDialog, {
        open: !!confirm,
        onClose: () => setConfirm(null),
        onConfirm: confirm ? confirm.action : () => undefined,
        title: confirm ? confirm.title : '',
        description: confirm ? confirm.description : undefined,
        confirmLabel: confirm ? confirm.confirmLabel : undefined,
        destructive: confirm ? confirm.destructive : false
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// MCP switchboard pane (#12, Q1a) — Hermes catalog + Claude Desktop writer
// ---------------------------------------------------------------------------

function McpPane() {
  const t = usePluginI18n(ID)
  const qc = useQueryClient()
  const rootRef = useRef(null)
  const layout = usePaneLayout(rootRef)
  const [busyName, setBusyName] = useState(null)
  const [confirm, setConfirm] = useState(null)

  const stateQuery = useQuery({
    queryKey: MCP_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/mcp/state') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 30000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const st = stateQuery.data
  const rows = st && st.ok && Array.isArray(st.rows) ? st.rows : []
  const writer = st && st.ok && st.writers ? st.writers.claude : null

  const writers = [
    {
      id: 'claude',
      label: 'Claude',
      syncPath: '/mcp/sync',
      removePath: '/mcp/remove'
    },
    {
      id: 'codex',
      label: 'Codex',
      syncPath: '/mcp/codex/sync',
      removePath: '/mcp/codex/remove'
    }
  ]

  const run = useCallback(
    (name, path, payload, successKey) => {
      setBusyName(name + path)
      pluginCtx
        .rest(path, { method: 'POST', body: payload })
        .then(res => {
          if (res && res.ok) host.notify({ kind: 'success', message: t(successKey, name) })
          else host.notify({ kind: 'error', message: res && res.error ? res.error : t('mcpFailed') })
        })
        .catch(err => host.notifyError(err, t('mcpFailed')))
        .finally(() => {
          setBusyName(null)
          qc.invalidateQueries({ queryKey: MCP_KEY })
        })
    },
    [qc, t]
  )

  const busy = busyName !== null

  const header = jsxs('div', {
    className: 'flex flex-col gap-2 px-3 pb-2 pt-3',
    children: [
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx('span', { className: 'text-sm font-medium', children: t('mcpTitle') }),
        st && st.ok
          ? jsx(Badge, { variant: 'default', size: 'xs', children: t('mcpCount', st.counts.catalog) })
          : null,
        jsx(Button, {
          variant: 'ghost', size: 'xs', className: 'ml-auto',
          disabled: stateQuery.isFetching,
          onClick: () => qc.invalidateQueries({ queryKey: MCP_KEY }),
          children: t('refresh')
        })
      ] }),
      st && st.ok && st.writers
        ? jsx('div', {
            className: 'flex flex-wrap items-center gap-2 text-xs text-muted-foreground',
            children: Object.keys(st.writers).map(wid =>
              jsxs('span', { className: 'inline-flex items-center gap-1', children: [
                jsx(StatusDot, { tone: st.writers[wid].present ? 'good' : 'muted' }),
                jsx('span', { className: 'truncate', children: t('mcpWriterLine', st.writers[wid].label, st.writers[wid].present ? '' : t('dirAbsentTip')) })
              ] }, wid)
            )
          })
        : null,
      st && st.ok && st.counts.foreign > 0
        ? jsx(Tip, {
            label: t('mcpForeignTip'),
            children: jsx(Badge, { variant: 'outline', size: 'xs', children: t('mcpForeignNote', st.counts.foreign) })
          })
        : null
    ]
  })

  let body = null
  if (stateQuery.isPending || (stateQuery.isLoading && !st)) {
    body = jsxs('div', {
      className: 'flex flex-col gap-2 px-3',
      children: [0, 1, 2, 3].map(i => jsx(Skeleton, { className: 'h-8 w-full' }, 'mcp-sk-' + i))
    })
  } else if (stateQuery.isError) {
    body = jsx(ErrorState, {
      title: t('errorTitle'),
      description: stateQuery.error && stateQuery.error.message ? stateQuery.error.message : t('errorDesc'),
      children: jsx(Button, {
        variant: 'secondary', size: 'xs',
        onClick: () => stateQuery.refetch(), children: t('retry')
      })
    })
  } else if (rows.length === 0) {
    body = jsx(EmptyState, { title: t('mcpEmpty'), description: t('mcpEmptyDesc') })
  } else {
    body = jsxs('div', {
      className: 'flex flex-col px-2 pb-4',
      children: rows.map(row => {
        const claude = row.writers.claude
        const drifted = claude === 'drifted'
        return jsxs('div', {
          className: 'rounded-md px-2 py-2 transition-colors hover:bg-(--chrome-action-hover)',
          children: [
            jsxs('div', { className: 'flex items-center gap-1.5', children: [
              jsx(StatusDot, { tone: row.enabled ? (drifted ? 'warn' : 'good') : 'muted' }),
              jsx('span', { className: 'min-w-0 truncate text-[0.8125rem] font-medium', children: row.name }),
              drifted ? jsx(Badge, { variant: 'warn', size: 'xs', children: t('mcpDrifted') }) : null,
              row.enabled ? null : jsx(Badge, { variant: 'muted', size: 'xs', children: t('mcpDisabledHermes') })
            ] }),
            jsxs('div', { className: cn('mt-1.5 grid gap-2 pl-3', layout === 'narrow' ? 'grid-cols-2' : 'grid-cols-3'), children: [
              jsxs('span', { className: 'inline-flex items-center justify-between gap-1', children: [
                jsx('span', { className: 'text-[0.625rem] text-muted-foreground', children: 'Hermes' }),
                jsx(Switch, {
                  size: 'xs',
                  checked: row.enabled,
                  disabled: busyName !== null,
                  'aria-label': row.name + ' — Hermes',
                  onCheckedChange: next => run(row.name, '/mcp/toggle', { name: row.name, enabled: next }, next ? 'mcpOn' : 'mcpOff')
                })
              ] }),
              writers.map(writer => {
                const wstate = row.writers[writer.id] || 'missing'
                const wdrifted = wstate === 'drifted'
                return jsxs('span', { className: 'inline-flex items-center justify-between gap-1', children: [
                  jsx('span', { className: 'text-[0.625rem] text-muted-foreground', children: writer.label }),
                  jsxs('span', { className: 'inline-flex items-center gap-1', children: [
                    jsx(Switch, {
                      size: 'xs',
                      checked: wstate === 'enabled',
                      disabled: busyName !== null,
                      'aria-label': row.name + ' — ' + writer.label,
                      onCheckedChange: next => {
                        if (next && !wdrifted) {
                          run(row.name, writer.syncPath, { name: row.name }, 'mcpSynced')
                        } else if (next) {
                          setConfirm({
                            title: t('mcpSyncTitle', row.name),
                            description: t('mcpOverwriteDesc'),
                            confirmLabel: t('mcpSync'),
                            destructive: false,
                            action: () => run(row.name, writer.syncPath, { name: row.name }, 'mcpSynced')
                          })
                        } else if (!wdrifted) {
                          run(row.name, writer.removePath, { name: row.name }, 'mcpRemoved')
                        } else {
                          setConfirm({
                            title: t('mcpRemoveTitle', row.name),
                            description: t('mcpRemoveForceDesc'),
                            confirmLabel: t('mcpRemoveForce'),
                            destructive: true,
                            action: () => run(row.name, writer.removePath, { name: row.name, force: true }, 'mcpRemoved')
                          })
                        }
                      }
                    }),
                    wdrifted
                      ? jsx(Button, {
                          variant: 'secondary', size: 'xs', className: 'h-4 px-1 text-[0.625rem]',
                          disabled: busyName !== null,
                          onClick: () => run(row.name, writer.syncPath, { name: row.name }, 'mcpSynced'),
                          children: t('mcpSync')
                        })
                      : null
                  ] })
                ] }, writer.id)
              })
            ] })
          ]
        }, row.name)
      })
    })
  }

  return jsxs('div', {
    ref: rootRef,
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      header,
      jsx(ScrollArea, { className: 'min-h-0 flex-1', children: body }),
      jsx(ConfirmDialog, {
        open: !!confirm,
        onClose: () => setConfirm(null),
        onConfirm: confirm ? confirm.action : () => undefined,
        title: confirm ? confirm.title : '',
        description: confirm ? confirm.description : undefined,
        confirmLabel: confirm ? confirm.confirmLabel : undefined,
        destructive: confirm ? confirm.destructive : false
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Full workspace shell — shared by openWorkspace and the route fallback.
// ---------------------------------------------------------------------------

function ToolCard({ tool, counts, busy, onManage, onEnableAll, onDisableAll }) {
  const t = usePluginI18n(ID)
  return jsxs('section', {
    'data-tool-card': tool.id,
    className: 'flex min-w-0 flex-col gap-3 rounded-lg border border-(--ui-stroke-secondary) p-3',
    children: [
      jsxs('div', {
        className: 'flex min-w-0 items-start gap-2',
        children: [
          jsx(StatusDot, { tone: counts.problem ? 'warn' : counts.enabled ? 'good' : 'muted' }),
          jsxs('div', {
            className: 'min-w-0 flex-1',
            children: [
              jsx('h3', { className: 'font-medium', children: tool.label }),
              jsx('div', {
                'data-tool-path': tool.id,
                className: 'whitespace-normal break-words text-xs text-muted-foreground',
                children: tool.dir || t('toolPathUnknown')
              })
            ]
          }),
          jsx(Button, {
            variant: 'secondary', size: 'xs', disabled: busy,
            onClick: onManage, children: t('manageTool')
          })
        ]
      }),
      jsxs('div', {
        className: 'grid grid-cols-3 gap-2 text-xs',
        children: [
          jsxs('div', {
            children: [
              jsx('div', { className: 'font-medium', children: t('enabledOfTotal', counts.enabled, counts.total) }),
              jsx('div', { className: 'text-muted-foreground', children: t('enabledLabel') })
            ]
          }),
          jsxs('div', {
            children: [
              jsx('div', { className: 'font-medium', children: counts.off }),
              jsx('div', { className: 'text-muted-foreground', children: t('offLabel') })
            ]
          }),
          jsxs('div', {
            children: [
              jsx('div', { className: counts.problem ? 'font-medium text-(--ui-text-warning)' : 'font-medium', children: counts.problem }),
              jsx('div', { className: 'text-muted-foreground', children: t('problemLabel') })
            ]
          })
        ]
      }),
      jsxs('div', {
        className: 'flex flex-wrap gap-2',
        children: [
          jsx(Button, {
            variant: 'secondary', size: 'xs', disabled: busy || counts.total === 0,
            onClick: onEnableAll, children: t('enableAll')
          }),
          jsx(Button, {
            variant: 'secondary', size: 'xs', disabled: busy || counts.total === 0,
            onClick: onDisableAll, children: t('disableAll')
          })
        ]
      })
    ]
  })
}

function BulkPlanPreview({ plan }) {
  const t = usePluginI18n(ID)
  if (!plan) return null
  const totals = plan.totals || {}
  const sample = Array.isArray(plan.sample) ? plan.sample : []
  const refused = Array.isArray(plan.refused) ? plan.refused : []
  return jsxs('div', {
    'data-bulk-plan': 'preview',
    className: 'flex max-h-64 flex-col gap-2 overflow-y-auto text-left text-xs',
    children: [
      jsx('div', { className: 'font-medium', children: t('toolBulkPreview', totals.would_change || 0, totals.already_satisfied || 0, totals.refused || 0) }),
      sample.length ? jsxs('div', { children: [
        jsx('div', { className: 'text-muted-foreground', children: t('bulkSampleTitle') }),
        sample.map(row => jsx('div', {
          'data-plan-sample': row.skill_id,
          children: t('bulkSampleLine', row.name, row.category, row.current_state, row.next_state)
        }, row.skill_id))
      ] }) : null,
      refused.length ? jsxs('div', { children: [
        jsx('div', { className: 'font-medium text-(--ui-text-warning)', children: t('bulkRefusedTitle') }),
        refused.map(row => jsx('div', {
          'data-plan-refused': row.skill,
          className: 'text-(--ui-text-warning)',
          children: t('bulkRefusedLine', row.skill, row.code, row.reason)
        }, `${row.skill}-${row.code}`))
      ] }) : null
    ]
  })
}

function BulkReceipt({ receipt, tool }) {
  const t = usePluginI18n(ID)
  if (!receipt) return null
  const details = receipt.receipt || {}
  const results = Array.isArray(receipt.results) ? receipt.results : []
  return jsxs('div', {
    'data-bulk-receipt': tool.id,
    className: 'mx-3 mb-2 max-h-48 overflow-y-auto rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
    children: [
      jsx('div', { className: 'mb-1 font-medium', children: t('bulkReceiptTitle', tool.label, details.changed || 0, details.failed || 0, details.refused || 0) }),
      details.receipt_id ? jsx('div', { className: 'mb-1 text-muted-foreground', children: t('bulkReceiptId', details.receipt_id) }) : null,
      results.map(result => jsx('div', {
        'data-bulk-result': result.skill,
        className: result.ok ? 'text-muted-foreground' : 'text-(--ui-text-danger)',
        children: t('bulkResultLine', result.skill, result.ok ? t('bulkResultOk', result.state) : t('bulkResultFailed', result.error || result.code || t('bulkFailed')))
      }, result.skill))
    ]
  })
}

function SingleToolView({ tool, skills, busy, onBack, onToggle, onBulk }) {
  const t = usePluginI18n(ID)
  const [rawQuery, setRawQuery] = useState('')
  const query = useDebounced(rawQuery, 200).trim().toLowerCase()
  const [view, setView] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [collapsed, setCollapsed] = useState(() => new Set())
  const [selected, setSelected] = useState(() => new Set())
  const categories = useMemo(() => Array.from(new Set(skills.map(skill => skill.category))), [skills])
  const visible = useMemo(() => skills.filter(skill => {
    const entry = skill.tools && skill.tools[tool.id]
    const stateName = entry ? entry.state : 'missing'
    if (categoryFilter !== 'all' && skill.category !== categoryFilter) return false
    if (query && skill.name.toLowerCase().indexOf(query) === -1 && skill.category.toLowerCase().indexOf(query) === -1 && (skill.description || '').toLowerCase().indexOf(query) === -1) return false
    if (view === 'enabled') return stateName === 'enabled'
    if (view === 'issues') return isProblemState(stateName)
    if (view === 'off') return stateName !== 'enabled' && !isProblemState(stateName)
    return true
  }), [skills, tool.id, categoryFilter, query, view])
  const groups = useMemo(() => {
    const grouped = new Map()
    for (const skill of visible) {
      if (!grouped.has(skill.category)) grouped.set(skill.category, [])
      grouped.get(skill.category).push(skill)
    }
    return Array.from(grouped.entries()).map(([category, rows]) => ({ category: category, skills: rows }))
  }, [visible])
  const visibleIds = visible.map(skill => skill.id)
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every(id => selected.has(id))
  const setAllVisible = checked => setSelected(previous => {
    const next = new Set(previous)
    for (const id of visibleIds) checked ? next.add(id) : next.delete(id)
    return next
  })
  const toggleSelected = id => setSelected(previous => {
    const next = new Set(previous)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const toggleCollapsed = category => setCollapsed(previous => {
    const next = new Set(previous)
    if (next.has(category)) next.delete(category)
    else next.add(category)
    return next
  })
  const selectedIds = Array.from(selected)

  return jsxs('div', {
    'data-single-tool': tool.id,
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      jsxs('div', { className: 'flex flex-col gap-2 border-b border-(--ui-stroke-secondary) p-3', children: [
        jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
          jsx(Button, { variant: 'secondary', size: 'xs', onClick: onBack, children: t('backToTools') }),
          jsx('h2', { className: 'font-medium', children: tool.label }),
          jsx(Badge, { variant: 'outline', size: 'xs', children: t('skillsCount', skills.length) }),
          jsxs('span', { className: 'ml-auto flex gap-1', children: [
            jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy || skills.length === 0, onClick: () => onBulk(skills.map(skill => skill.id), true, t('scopeAll')), children: t('enableAll') }),
            jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy || skills.length === 0, onClick: () => onBulk(skills.map(skill => skill.id), false, t('scopeAll')), children: t('disableAll') })
          ] })
        ] }),
        jsx(SearchField, { placeholder: t('searchPlaceholder'), value: rawQuery, onChange: setRawQuery, containerClassName: 'w-full', 'aria-label': t('searchPlaceholder') }),
        jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
          jsx('label', { className: 'text-xs text-muted-foreground', children: t('categoryFilter') }),
          jsx('select', {
            'aria-label': t('categoryFilter'), value: categoryFilter,
            onChange: event => setCategoryFilter(event.target.value),
            className: 'rounded-md border border-(--ui-stroke-secondary) bg-background px-2 py-1 text-xs',
            children: [jsx('option', { value: 'all', children: t('allCategories') }, 'all')].concat(categories.map(category => jsx('option', { value: category, children: category }, category)))
          }),
          jsx(SegmentedControl, {
            options: [{ id: 'all', label: t('viewAll') }, { id: 'enabled', label: t('viewEnabled') }, { id: 'off', label: t('viewOff') }, { id: 'issues', label: t('viewIssues') }],
            value: view,
            onChange: setView
          })
        ] }),
        jsxs('label', { className: 'flex items-center gap-2 text-xs', children: [
          jsx('input', { type: 'checkbox', checked: allVisibleSelected, onChange: event => setAllVisible(event.target.checked), 'aria-label': t('selectAllVisible') }),
          t('selectAllVisible'),
          jsx('span', { className: 'text-muted-foreground', children: t('visibleCount', visible.length) })
        ] })
      ] }),
      jsx(ScrollArea, { className: 'min-h-0 flex-1', children: groups.length
        ? jsx('div', { className: 'flex flex-col gap-2 p-3 pb-20', children: groups.map(group => {
            const isCollapsed = collapsed.has(group.category)
            const ids = group.skills.map(skill => skill.id)
            return jsxs('section', { 'data-tool-category': group.category, children: [
              jsxs('div', { className: 'sticky top-0 z-10 flex items-center gap-2 bg-background py-1', children: [
                jsx('button', { type: 'button', 'aria-expanded': !isCollapsed, onClick: () => toggleCollapsed(group.category), className: 'font-medium', children: `${isCollapsed ? '▸' : '▾'} ${group.category}` }),
                jsx(Badge, { variant: 'outline', size: 'xs', children: String(group.skills.length) }),
                jsxs('span', { className: 'ml-auto flex gap-1', children: [
                  jsx(Button, { variant: 'ghost', size: 'xs', disabled: busy, onClick: () => onBulk(ids, true, t('scopeCategory', group.category)), children: t('enableCategory') }),
                  jsx(Button, { variant: 'ghost', size: 'xs', disabled: busy, onClick: () => onBulk(ids, false, t('scopeCategory', group.category)), children: t('disableCategory') })
                ] })
              ] }),
              isCollapsed ? null : jsx('div', { children: group.skills.map(skill => {
                const entry = skill.tools && skill.tools[tool.id]
                const stateName = entry ? entry.state : 'missing'
                const locked = stateName === 'foreign-link' || stateName === 'unmanaged-dir'
                return jsxs('div', { 'data-single-tool-skill': skill.id, className: 'flex items-center gap-2 rounded-md px-2 py-2 hover:bg-(--chrome-action-hover)', children: [
                  jsx('input', { type: 'checkbox', checked: selected.has(skill.id), onChange: () => toggleSelected(skill.id), 'aria-label': t('selectSkill', skill.name) }),
                  jsx(StatusDot, { tone: isProblemState(stateName) ? 'warn' : stateName === 'enabled' ? 'good' : 'muted' }),
                  jsxs('span', { className: 'min-w-0 flex-1', children: [
                    jsx('span', { className: 'block truncate font-medium', children: skill.name }),
                    skill.description ? jsx('span', { className: 'block truncate text-xs text-muted-foreground', children: skill.description }) : null
                  ] }),
                  jsx('span', { className: 'text-xs text-muted-foreground', children: stateName }),
                  jsx(Switch, { size: 'xs', checked: stateName === 'enabled', disabled: busy || locked, 'aria-label': `${skill.name} — ${tool.label}`, onCheckedChange: enabled => onToggle(skill, enabled) })
                ] }, skill.id)
              }) })
            ] }, group.category)
          }) })
        : jsx(EmptyState, { title: t('noMatchTitle'), description: t('noMatchDesc') }) }),
      selectedIds.length ? jsxs('div', { 'data-selection-bar': 'sticky', className: 'sticky bottom-0 flex items-center gap-2 border-t border-(--ui-stroke-secondary) bg-background p-3', children: [
        jsx('span', { className: 'text-xs font-medium', children: t('selectedCount', selectedIds.length) }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => onBulk(selectedIds, true, t('scopeSelected', selectedIds.length)), children: t('enableSelected') }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => onBulk(selectedIds, false, t('scopeSelected', selectedIds.length)), children: t('disableSelected') })
      ] }) : null
    ]
  })
}

function ToolsOverview({ layout }) {
  const t = usePluginI18n(ID)
  const qc = useQueryClient()
  const [selectedTool, setSelectedTool] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [undo, setUndo] = useState(null)
  const [receipt, setReceipt] = useState(null)
  const [taskBusy, setTaskBusy] = useState(false)
  const stateQuery = useQuery({
    queryKey: STATE_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/state') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 15000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const diffQuery = useQuery({
    queryKey: DIFF_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/diff') : Promise.reject(new Error('no backend'))),
    staleTime: 10000,
    refetchInterval: 30000,
    refetchOnWindowFocus: false,
    retry: 1
  })
  const driftQuery = useQuery({
    queryKey: DRIFT_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/drift') : Promise.reject(new Error('no backend'))),
    staleTime: 30000,
    refetchInterval: 60000,
    refetchOnWindowFocus: false,
    retry: 0
  })
  const state = stateQuery.data
  const skills = state && state.ok && Array.isArray(state.skills) ? state.skills : []
  const enabledByTool = countEnabledByTool(state)
  const linkTools = presentLinkTools(state)
  const hermes = state && state.ok && Array.isArray(state.tools)
    ? state.tools.find(tool => tool.id === 'hermes' || tool.special === 'config')
    : null
  const tools = [hermes || { id: 'hermes', label: 'Hermes', special: 'config' }, ...linkTools]
  const problemTotals = summaryProblemTotals(diffQuery.data, driftQuery.data)
  const overviewProblems = problemTotals.broken + problemTotals.foreign + problemTotals.unmanaged

  useEffect(() => {
    if (!undo) return undefined
    const timer = setTimeout(() => setUndo(null), Math.max(0, undo.expires - Date.now()))
    return () => clearTimeout(timer)
  }, [undo])

  const countsFor = toolId => {
    let off = 0
    let problem = 0
    for (const skill of skills) {
      const entry = skill.tools && skill.tools[toolId]
      const stateName = entry ? entry.state : 'missing'
      if (isProblemState(stateName)) problem += 1
      else if (stateName !== 'enabled') off += 1
    }
    return { enabled: enabledByTool.get(toolId) || 0, total: skills.length, off: off, problem: problem }
  }

  const applyMutation = useMutation({
    mutationFn: vars => pluginCtx.rest('/bulk/apply', {
      method: 'POST', body: { skills: vars.skillIds, tool: vars.tool.id, enabled: vars.enabled, receipt_id: vars.receiptId }
    }),
    onSuccess: (data, vars) => {
      if (!data || data.ok !== true) {
        host.notify({ kind: 'error', message: t('bulkFailed') })
        return
      }
      const resultReceipt = data.receipt || {}
      const undoActions = (Array.isArray(resultReceipt.undone_by) ? resultReceipt.undone_by : [])
        .map(item => ({ skill: item.skill, tool: vars.tool.id, enabled: item.enabled }))
      if (undoActions.length) {
        setUndo({ actions: undoActions, count: undoActions.length, expires: Date.now() + 30000 })
      }
      setReceipt({ tool: vars.tool, results: Array.isArray(data.results) ? data.results : [], receipt: resultReceipt })
      haptic('tap')
      host.notify({ kind: resultReceipt.failed ? 'error' : 'success', message: t('toastBulk', resultReceipt.changed || 0, resultReceipt.failed || 0) })
    },
    onError: err => host.notifyError(err, t('bulkFailed')),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
      qc.invalidateQueries({ queryKey: DRIFT_KEY })
    }
  })

  const planMutation = useMutation({
    mutationFn: vars => pluginCtx.rest('/bulk/plan', { method: 'POST', body: { skills: vars.skillIds, tool: vars.tool.id, enabled: vars.enabled } }),
    onSuccess: (plan, vars) => {
      if (!plan || plan.ok !== true) {
        host.notify({ kind: 'error', message: t('bulkPlanFailed') })
        return
      }
      const exactIds = Array.isArray(plan.would_change) ? plan.would_change.slice() : []
      setConfirm({
        title: vars.enabled ? t('bulkOnScopeTitle', vars.scope, vars.tool.label) : t('bulkOffScopeTitle', vars.scope, vars.tool.label),
        description: jsx(BulkPlanPreview, { plan: plan }),
        confirmLabel: vars.enabled ? t('confirmEnable') : t('confirmDisable'),
        destructive: !vars.enabled,
        action: () => {
          setConfirm(null)
          applyMutation.mutate({ skillIds: exactIds, tool: vars.tool, enabled: vars.enabled })
        }
      })
    },
    onError: err => host.notifyError(err, t('bulkPlanFailed'))
  })

  const openBulkPlan = (tool, enabled, skillIds, scope) => {
    planMutation.mutate({ tool: tool, enabled: enabled, skillIds: skillIds.slice(), scope: scope || t('scopeAll') })
  }

  const toggleMutation = useMutation({
    mutationFn: vars => pluginCtx.rest('/toggle', { method: 'POST', body: { skill: vars.skill.id, tool: vars.tool.id, enabled: vars.enabled } }),
    onSuccess: data => {
      if (!data || data.ok !== true) host.notify({ kind: 'error', message: data && data.error ? data.error : t('toggleFailed') })
      else haptic('tap')
    },
    onError: err => host.notifyError(err, t('toggleFailed')),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
      qc.invalidateQueries({ queryKey: DRIFT_KEY })
    }
  })

  const onUndo = useCallback(() => {
    if (!undo) return
    const byEnabled = new Map()
    for (const action of undo.actions) {
      const entry = byEnabled.get(action.enabled) || []
      entry.push(action.skill)
      byEnabled.set(action.enabled, entry)
    }
    setUndo(null)
    setTaskBusy(true)
    Promise.all(Array.from(byEnabled.entries()).map(([enabled, skillIds]) =>
      pluginCtx.rest('/bulk/apply', {
        method: 'POST', body: { skills: skillIds, tool: undo.actions[0].tool, enabled: enabled }
      })
    )).then(responses => {
      const response = responses[responses.length - 1]
      if (response && response.receipt) {
        const tool = tools.find(item => item.id === response.receipt.tool) || { id: response.receipt.tool, label: response.receipt.tool }
        setReceipt({ tool: tool, results: response.results || [], receipt: response.receipt })
      }
      host.notify({ kind: 'success', message: t('toastBulkDone', undo.count) })
    }).catch(err => host.notifyError(err, t('undoFailed'))).finally(() => {
      setTaskBusy(false)
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
      qc.invalidateQueries({ queryKey: DRIFT_KEY })
    })
  }, [undo, qc, t])

  const busy = taskBusy || planMutation.isPending || applyMutation.isPending || toggleMutation.isPending
  const sharedConfirm = jsx(ConfirmDialog, {
    open: !!confirm,
    onClose: () => setConfirm(null),
    onConfirm: confirm ? confirm.action : () => undefined,
    title: confirm ? confirm.title : '',
    description: confirm ? confirm.description : undefined,
    confirmLabel: confirm ? confirm.confirmLabel : undefined,
    destructive: confirm ? confirm.destructive : false
  })

  if (selectedTool) {
    return jsxs('div', {
      className: 'flex h-full min-w-0 flex-col',
      children: [
        jsx(SingleToolView, {
          tool: selectedTool,
          skills: skills,
          busy: busy,
          onBack: () => setSelectedTool(null),
          onToggle: (skill, enabled) => toggleMutation.mutate({ skill: skill, tool: selectedTool, enabled: enabled }),
          onBulk: (skillIds, enabled, scope) => openBulkPlan(selectedTool, enabled, skillIds, scope)
        }),
        receipt ? jsx(BulkReceipt, { receipt: receipt, tool: receipt.tool }) : null,
        jsx(UndoBanner, { undo: undo, onUndo: onUndo, busy: busy }),
        sharedConfirm
      ]
    })
  }

  let body
  if (stateQuery.isPending || (stateQuery.isLoading && !state)) {
    body = jsx('div', { className: 'grid grid-cols-1 gap-3 p-4', children: [0, 1, 2].map(index => jsx(Skeleton, { className: 'h-40 w-full' }, `tool-card-${index}`)) })
  } else if (stateQuery.isError) {
    body = jsx(ErrorState, {
      title: t('errorTitle'), description: t('errorDesc'),
      children: jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => stateQuery.refetch(), children: t('retry') })
    })
  } else if (state && state.ok && !state.skills_root_exists) {
    body = jsx(EmptyState, { title: t('noRootTitle'), description: t('noRootDesc') })
  } else {
    body = jsx('div', {
      className: cn('grid gap-3 p-4', layout === 'narrow' ? 'grid-cols-1' : 'grid-cols-2'),
      children: tools.map(tool => jsx(ToolCard, {
        tool: tool,
        counts: countsFor(tool.id),
        busy: busy,
        onManage: () => setSelectedTool(tool),
        onEnableAll: () => openBulkPlan(tool, true, skills.map(skill => skill.id), t('scopeAll')),
        onDisableAll: () => openBulkPlan(tool, false, skills.map(skill => skill.id), t('scopeAll'))
      }, tool.id))
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3',
        children: [
          jsx('h2', { className: 'font-medium', children: t('ccTools') }),
          jsx(Badge, { variant: 'outline', size: 'xs', children: t('skillsCount', skills.length) }),
          overviewProblems ? jsx(Badge, { variant: 'warn', size: 'xs', children: t('overviewProblems', overviewProblems) }) : null,
          jsx(Button, { variant: 'secondary', size: 'xs', className: 'ml-auto', onClick: () => ccSectionAtom.set('advanced'), children: t('addToolAction') })
        ]
      }),
      jsx(ScrollArea, { className: 'min-h-0 flex-1', children: body }),
      receipt ? jsx(BulkReceipt, { receipt: receipt, tool: receipt.tool }) : null,
      jsx(UndoBanner, { undo: undo, onUndo: onUndo, busy: busy }),
      sharedConfirm
    ]
  })
}

function SectionPlaceholder({ title, hint }) {
  return jsx(EmptyState, { title: title, description: hint })
}

function PrimaryNav({ sections, active, onSelect, layout }) {
  const horizontal = layout === 'narrow'
  return jsx('nav', {
    'aria-label': 'Control Center sections',
    className: horizontal
      ? 'w-full shrink-0 overflow-x-auto border-b border-(--ui-stroke-secondary)'
      : 'w-[180px] shrink-0 border-r border-(--ui-stroke-secondary)',
    children: jsx('div', {
      className: horizontal ? 'flex min-w-max gap-1 p-2' : 'flex flex-col gap-1 p-3',
      children: sections.map(section =>
        jsx('button', {
          type: 'button',
          'aria-label': section.label,
          'aria-current': active === section.id ? 'page' : undefined,
          onClick: () => onSelect(section.id),
          className: cn(
            'rounded-md px-2 py-1.5 text-left text-xs transition-colors',
            horizontal && 'shrink-0',
            active === section.id
              ? 'bg-primary/10 font-medium text-primary'
              : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
          ),
          children: section.label
        }, section.id)
      )
    })
  })
}

function ControlCenter() {
  const t = usePluginI18n(ID)
  const rootRef = useRef(null)
  const layout = usePaneLayout(rootRef)
  const active = useValue(ccSectionAtom)
  const sections = [
    { id: 'tools', label: t('ccTools') },
    { id: 'sets', label: t('ccSets') },
    { id: 'problems', label: t('ccProblems') },
    { id: 'mcp', label: t('ccMcp') },
    { id: 'advanced', label: t('ccAdvanced') }
  ]
  const bodies = {
    tools: () => jsx(ToolsOverview, { layout: layout }),
    sets: () => jsx(SkillsPane, { section: 'sets' }),
    problems: () => jsx(SkillsPane, { section: 'problems' }),
    mcp: () => jsx(McpPane, {}),
    advanced: () => jsx(SkillsPane, { section: 'advanced' })
  }
  const renderBody = bodies[active] || (() => jsx(SectionPlaceholder, { title: t('ccTools'), hint: t('toolsLandingHint') }))
  return jsxs('div', {
    ref: rootRef,
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      jsx(BackgroundHost, {}),
      jsxs('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsx('span', { className: 'font-medium', children: t('ccTitle') }),
          jsx('span', { className: 'text-xs text-muted-foreground', children: t('toolsLandingHint') })
        ]
      }),
      jsxs('div', {
        className: layout === 'narrow' ? 'flex min-h-0 flex-1 flex-col' : 'flex min-h-0 flex-1 flex-row',
        children: [
          jsx(PrimaryNav, { sections: sections, active: active, onSelect: next => ccSectionAtom.set(next), layout: layout }),
          jsx('main', { className: 'min-h-0 min-w-0 flex-1', children: renderBody() })
        ]
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Plugin entry — register pane, page, and palette command
// ---------------------------------------------------------------------------

export default {
  id: ID, // must match the folder name
  name: 'Skills Toggle',
  defaultEnabled: false, // unified-package desktop halves ship opt-in
  register(ctx) {
    pluginCtx = ctx

    ctx.i18n.register({
      en: {
        paneTitle: 'Skills',
        toolAll: 'All',
        viewAll: 'All',
        viewIssues: 'Issues',
        viewOff: 'Off',
        searchPlaceholder: 'Search skills…',
        refresh: 'Refresh',
        retry: 'Retry',
        ccTitle: 'Skills Control Center',
        ccTools: 'Tools',
        ccSets: 'Sets',
        ccProblems: 'Problems',
        ccMcp: 'MCP',
        ccAdvanced: 'Advanced',
        openControlCenter: 'Open Control Center',
        scan: 'Scan',
        problemsAction: n => `Problems (${n})`,
        enabledOn: n => `${n} on`,
        summaryLine: (skills, tools) => `${skills} skills · ${tools} tools`,
        problemLine: (broken, drifted, foreign, unlinked) => `${broken} broken · ${drifted} drift · ${foreign} foreign · ${unlinked} unlinked`,
        toolsLandingHint: 'Manage daily skill availability by tool.',
        toolPathUnknown: 'Path not detected',
        manageTool: 'Manage',
        enabledOfTotal: (enabled, total) => `${enabled} on / ${total}`,
        enabledLabel: 'enabled',
        offLabel: 'off',
        problemLabel: 'problems',
        enableAll: 'Enable all',
        disableAll: 'Disable all',
        overviewProblems: n => `${n} problems`,
        addToolAction: 'Add Tool',
        toolBulkPreview: (changed, already, refused) => `${changed} will change · ${already} already set · ${refused} protected/refused`,
        bulkSampleTitle: 'Sample changes',
        bulkSampleLine: (name, category, current, next) => `${name} (${category}): ${current} → ${next}`,
        bulkRefusedTitle: 'Protected or refused',
        bulkRefusedLine: (skill, code, reason) => `${skill} [${code}] — ${reason}`,
        bulkPlanFailed: 'Could not prepare the bulk preview',
        confirmEnable: 'Confirm enable',
        confirmDisable: 'Confirm disable',
        bulkOnScopeTitle: (scope, tool) => `Enable ${scope} for ${tool}?`,
        bulkOffScopeTitle: (scope, tool) => `Disable ${scope} for ${tool}?`,
        scopeAll: 'all skills',
        scopeCategory: category => `${category} category`,
        scopeSelected: n => `${n} selected skill(s)`,
        backToTools: 'Back to Tools',
        bulkReceiptTitle: (tool, changed, failed, refused) => `${tool}: ${changed} changed, ${failed} failed, ${refused} refused`,
        bulkReceiptId: id => `Receipt ${id}`,
        bulkResultLine: (skill, result) => `${skill} — ${result}`,
        bulkResultOk: state => `OK — ${state}`,
        bulkResultFailed: reason => `Failed — ${reason}`,
        categoryFilter: 'Category',
        allCategories: 'All categories',
        viewEnabled: 'Enabled',
        selectAllVisible: 'Select all visible',
        visibleCount: n => `${n} visible`,
        selectSkill: skill => `Select ${skill}`,
        selectedCount: n => `${n} selected`,
        enableSelected: 'Enable selected',
        disableSelected: 'Disable selected',
        enableCategory: 'Enable category',
        disableCategory: 'Disable category',
        skillsCount: n => `${n} skills`,
        brokenCount: n => `${n} broken`,
        unlinkedCount: n => `${n} unlinked`,
        totalTip: 'Total skills discovered under the Hermes skills root',
        brokenTip: 'Symlinks pointing at nothing — repair them',
        unlinkedTip: 'Skills not linked into any coding tool',
        repairAll: 'Repair all',
        repairAllTitle: 'Repair all broken links?',
        repairAllDesc:
          'Every symlink that points into the skills tree but no longer resolves is re-pointed at the matching skill. Foreign links and real directories are left untouched.',
        bulkOn: 'Link all',
        bulkOff: 'Unlink all',
        bulkOnTip: 'Link every skill in this category for the selected tool',
        bulkOffTip: 'Unlink every skill in this category for the selected tool',
        bulkOnTitle: tool => `Link all for ${tool}?`,
        bulkOffTitle: tool => `Unlink all for ${tool}?`,
        bulkDesc: (n, tool) => `${n} skills will be toggled for ${tool}. You can undo this by toggling back.`,
        toastLinked: tool => `Linked for ${tool}`,
        toastUnlinked: tool => `Unlinked for ${tool}`,
        toastConfig: enabled => (enabled ? 'Skill enabled for Hermes' : 'Skill disabled for Hermes'),
        toastRepaired: 'Link repaired',
        toastRepairedAll: (fixed, unfixable) => `Repaired ${fixed} link(s), ${unfixable} left alone`,
        toastBulk: (changed, failed) => `Toggled ${changed} skill(s), ${failed} failed`,
        toastBulkDone: n => `Applied ${n} change(s)`,
        toggleFailed: 'Toggle failed — change rolled back',
        repairFailed: 'Repair failed',
        bulkFailed: 'Bulk toggle failed',
        errorTitle: 'Skills backend unavailable',
        errorDesc: 'The plugin backend did not answer. Check that skills-toggle is in `plugins.enabled` in config.yaml, then retry.',
        errorNeedsRestart: "The gateway mounts this plugin's backend only at startup — it looks like the gateway started before skills-toggle was enabled. Run `hermes gateway restart` (or restart from Settings), then Retry.",
        noRootTitle: 'No skills root found',
        noRootDesc: 'The Hermes skills directory does not exist yet. Create skills and reload.',
        emptyTitle: 'No skills yet',
        emptyDesc: 'Skills added under ~/.hermes/skills/<category>/<name>/ appear here automatically.',
        noMatchTitle: 'No skills match',
        noMatchDesc: 'Try a different search, or switch the tool filter back to All.',
        hermesOnTip: 'Enabled for Hermes (not in skills.disabled)',
        hermesOffTip: 'Toggle membership in skills.disabled (Hermes itself)',
        hermesOffBadge: 'hermes off',
        linkedTip: 'Linked into this tool — switch off to remove the symlink',
        unlinkTip: 'Not linked — switch on to create the symlink',
        dirAbsentTip: 'This tool directory does not exist yet — switching on creates it and links',
        repairTip: 'Repair this link into the skills tree',
        fix: 'fix',
        brokenLinkTip: 'Symlink points at nothing — fix it or toggle to recreate',
        foreignLinkTip: 'Symlink points outside the skills tree — resolve it manually (never touched automatically)',
        unmanagedDirTip: 'A real directory sits here (not a symlink) — never touched automatically',
        setup: 'Setup',
        close: 'Close',
        setupTitle: 'Set up your tools',
        setupDesc: 'Create skills folders for each coding tool so skills can be linked into them. Nothing is linked until you flip a switch.',
        setupNudge: 'No tool skills folders found yet — set up tools to start linking.',
        createDir: 'Create',
        present: 'ready',
        addTool: 'Add a custom tool (writes skills-toggle.json)',
        toolLabel: 'Label',
        toolDir: '~/path/to/skills',
        add: 'Add',
        toolAdded: label => `${label} added`,
        toolAddFailed: 'Could not save the tool',
        dirCreated: label => `Created skills folder for ${label}`,
        autoLinkDesc: 'Auto-link: new skills are linked automatically (opt-in per tool; optional category regex)',
        autoLinkPattern: 'category regex…',
        adoptScan: 'Find copies to adopt',
        adoptCounts: (a, d) => `${a} adoptable copies, ${d} drifted`,
        adoptAll: 'Adopt…',
        adoptConfirmTitle: 'Adopt copies into Hermes?',
        adoptConfirmDesc: (n, sample) => `Copies each skill into ~/.hermes/skills/imported/, keeps the original as a timestamped backup, and replaces it with a symlink. ${n} candidate(s), e.g. ${sample}`,
        adoptDone: n => `Adopted ${n} skill(s)`,
        adoptFailed: 'Adoption scan failed',
        arrivalsTitle: n => `${n} new skill(s) found`,
        dismiss: 'Ignore',
        alwaysAuto: 'Auto-link new skills for this tool (opt-in)',
        linkChecked: n => `Link ${n} skill(s)`,
        toastAutoLinked: n => `Auto-linked ${n} new skill(s)`,
        undoAvail: n => `${n} change(s) applied`,
        undo: 'Undo',
        presets: 'Presets',
        presetApplyTitle: name => `Apply preset "${name}"?`,
        presetApplyDesc: (n, m, sample) => `Enables ${n} skills across ${m} tool(s) — additive only, nothing is turned off. Examples: ${sample}…`,
        presetImportDesc: (n, m, skipped, sample) => `Enables ${n} known skills across ${m} tool(s) (${skipped} unknown skipped). Examples: ${sample}…`,
        presetNoop: 'Nothing to apply for this preset',
        applyPreset: 'Apply',
        minimalTitle: 'Unlink every tool?',
        minimalDesc: n => `Removes ${n} consumer link(s). Sources stay in Hermes; Undo restores them for 30 seconds.`,
        presetImport: 'Import…',
        downloadPreset: 'Download',
        downloaded: 'Preset file downloaded',
        importFile: 'Open file…',
        pasteHint: 'Paste a shared preset JSON:',
        copyPreset: 'Copy current',
        copied: 'Preset JSON copied to clipboard',
        copyFailed: 'Clipboard unavailable in this context',
        invalidPreset: 'Invalid preset JSON (need version 1, skills array)',
        rowAll: 'all',
        rowNone: 'none',
        skillsTab: 'Skills',
        mcpTab: 'MCP',
        mcpTitle: 'MCP servers',
        mcpCount: n => `${n} in Hermes`,
        mcpEmpty: 'No MCP servers in Hermes',
        mcpEmptyDesc: 'Servers configured under mcp_servers in config.yaml appear here and can be mirrored into Claude Desktop.',
        mcpWriterLine: (label, absent) => `${label} — ${absent ? absent : 'config found'}`,
        mcpForeignNote: n => `${n} server(s) in Claude Desktop are not in the Hermes catalog (never touched)`,
        mcpForeignTip: 'Foreign entries are managed outside Hermes and are left alone',
        mcpDrifted: 'drifted',
        mcpDisabledHermes: 'hermes off',
        mcpSync: 'sync',
        mcpOn: name => `${name} enabled for Hermes`,
        mcpOff: name => `${name} disabled for Hermes`,
        mcpSynced: name => `${name} synced to Claude Desktop`,
        mcpRemoved: name => `${name} removed from Claude Desktop`,
        mcpSyncTitle: name => `Overwrite the Claude Desktop copy of "${name}"?`,
        mcpOverwriteDesc: 'Its current config differs from the Hermes catalog. The old copy is kept in a timestamped backup next to the config file.',
        mcpRemoveTitle: name => `Remove "${name}" from Claude Desktop?`,
        mcpRemoveForceDesc: 'Its config differs from the Hermes catalog. The old copy is kept in a timestamped backup next to the config file.',
        mcpRemoveForce: 'Remove anyway',
        mcpFailed: 'MCP change failed',
        watchDesc: 'Watch mode — notify me when skills or links change (only when the app is in the background)',
        watchOn: 'watching',
        watchOff: 'Watch: off',
        watchArrivals: 'new skills',
        watchBroken: 'broken links',
        watchDrift: 'drift',
        watchBrokenTitle: 'Skills: broken links detected',
        watchBrokenBody: n => `${n} broken link(s) — open the Skills pane to repair`,
        watchDriftTitle: 'Skills: drift detected',
        watchDriftBody: n => `${n} drifted skill(s) — open the Skills pane to resolve`,
        watchArrivalsTitle: 'Skills: new skills found',
        watchArrivalsBody: n => `${n} new skill(s) — open the Skills pane to enable`,
        blueprintDesc: 'Machine blueprint — export the full link map, apply it on another machine (additive only: nothing is removed).',
        blueprintExport: 'Export blueprint',
        blueprintOpen: 'Open blueprint…',
        blueprintPreview: (l, d, r) => `Will create ${l} link(s) and disable ${d} skill(s) for Hermes (${r} refused).`,
        blueprintApply: 'Apply blueprint',
        blueprintApplyTitle: 'Apply this machine blueprint?',
        blueprintApplyDesc: (l, d) => `Creates ${l} link(s) and disables ${d} skill(s) in Hermes. Additive only — existing links are never removed.`,
        blueprintApplied: (l, d, f) => `Blueprint applied: ${l} link(s), ${d} hermes-off, ${f} failed`,
        blueprintExported: 'Blueprint downloaded',
        blueprintFailed: 'Blueprint operation failed',
        blueprintInvalid: 'Invalid blueprint file (need version 2)',
        backupList: 'List backups',
        backupCount: n => `${n} backup(s) found`,
        backupRestore: 'Restore',
        backupRestoreTitle: 'Restore this backup?',
        backupRestoreDesc: name => `"${name}" will be restored. The current state is backed up first.`,
        backupRestored: name => `"${name}" restored`,
        backupScanFailed: 'Backup scan failed',
        rowAllTip: 'Link this skill into every tool',
        rowNoneTip: 'Unlink this skill from every tool',
        viewDrift: 'Drift',
        driftEmpty: 'No drift detected',
        driftDesc: 'Same-name skills whose tool copy differs from the Hermes source. "Use Hermes" backs up the tool copy and swaps in the canonical symlink — the original is never deleted.',
        useHermes: 'Use Hermes',
        useToolCopy: 'Use tool copy',
        useToolCopyTip: 'Make the tool copy the canonical Hermes source (originals backed up)',
        useToolCopyTitle: (name, tool) => `Use the tool copy of "${name}" (${tool})?`,
        useToolCopyDesc: 'The Hermes source is backed up (dotted, inside its category), the tool copy becomes canonical, and the tool links to it.',
        pullDone: name => `"${name}" replaced by the tool copy`,
        keepBoth: 'Keep both',
        keepBothTip: 'Adopt the tool copy under a separate name',
        keepBothTitle: name => `Keep both copies of "${name}"?`,
        keepBothDesc: 'The tool copy is adopted into the skills tree under its own name and the tool links to it. Nothing is overwritten.',
        keepBothDone: name => `"${name}" kept as a separate skill`,
        undoFailed: 'Part of the undo failed — check the Setup panel backups',
        useHermesTip: 'Back up the tool copy and link the Hermes version',
        useHermesTitle: name => 'Use the Hermes copy of "' + name + '"?',
        useHermesDesc: tool => 'The ' + tool + ' copy is moved aside to a timestamped backup and replaced with a symlink to the Hermes source.',
        pushDone: name => '"' + name + '" now links to the Hermes source',
        pushFailed: 'Drift push failed — the original was restored',
        adoptToolCount: (a, d) => a + ' adoptable, ' + d + ' drifted',
        adoptNone: 'No adoptable copies or drift found.'
      }
    })

    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'skills',
        data: { placement: 'right', width: '320px' },
        render: () => jsx(CompactSummaryPane, {})
      },
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/skills-toggle' },
        render: () => jsx(ControlCenter, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'skills-toggle.open',
          label: 'Skills: toggle…',
          keywords: ['skills', 'toggle', 'sync', 'claude', 'codex', 'opencode', 'grok', 'zcode'],
          detail: () => 'Enable or disable skills per tool',
          run: () => openControlCenter('tools')
        }
      },
      {
        id: 'mcp',
        area: PALETTE_AREA,
        data: {
          id: 'skills-toggle.mcp',
          label: 'MCP: toggle…',
          keywords: ['mcp', 'servers', 'claude desktop', 'toggle'],
          detail: () => 'Enable or disable MCP servers per app',
          run: () => openControlCenter('mcp')
        }
      },
      {
        id: 'report',
        area: PALETTE_AREA,
        data: {
          id: 'skills-toggle.report',
          label: 'Skills: health report',
          keywords: ['skills', 'health', 'broken', 'diff', 'repair'],
          detail: () => 'Broken links, unlinked skills, drift',
          run: () => openControlCenter('problems')
        }
      },
      {
        id: 'chip',
        area: STATUSBAR_AREAS.right,
        order: 140,
        render: () => jsx(HealthChip, {})
      }
    ])
  }
}
