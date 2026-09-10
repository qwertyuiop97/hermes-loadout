/**
 * Loadout for Hermes, the no-build desktop entry.
 * The backend owns plans, filesystem checks, named selections, and recovery.
 * UI storage holds view preferences only. All capability changes are reviewed.
 * MIT License. See LICENSE at the package root.
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
import { useCallback, useEffect, useMemo, useRef, useState, useId } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-loadout'
const STATE_KEY = [ID, 'state']
const DIFF_KEY = [ID, 'diff']
const DRIFT_KEY = [ID, 'drift']
const MCP_KEY = [ID, 'mcp']
const ONBOARDING_KEY = 'onboarding'
const ONBOARDING_VERSION = 1
const ccSectionAtom = atom('tools')
const arrivalsAtom = atom([])
const watchPrefsEpochAtom = atom(0)
const WORKSPACE_ID = 'hermes-loadout.control-center'
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

const PROBLEM_STATES = ['broken-link', 'foreign-link', 'unmanaged-dir', 'scope-error', 'catalog-bypass']

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
    unlinked: counts.unlinked || 0,
    bypasses: diff && Array.isArray(diff.catalog_bypasses) ? diff.catalog_bypasses.length : 0
  }
}

function openControlCenter(section = 'tools') {
  ccSectionAtom.set(section)
  if (typeof host.openWorkspace === 'function') {
    workspaceDispose = host.openWorkspace(WORKSPACE_ID, {
      title: 'Loadout for Hermes',
      minWidth: '320px',
      render: () => jsx(ControlCenter, {}),
      onClose: () => {
        workspaceDispose = null
      }
    })
    return
  }
  host.navigate('/hermes-loadout')
}

function closeControlCenter() {
  if (!workspaceDispose) return
  workspaceDispose()
  workspaceDispose = null
}

function useDebounced(value, delay) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(query.matches)
    update()
    if (typeof query.addEventListener === 'function') {
      query.addEventListener('change', update)
      return () => query.removeEventListener('change', update)
    }
    if (typeof query.addListener === 'function') {
      query.addListener(update)
      return () => query.removeListener(update)
    }
    return undefined
  }, [])
  return reduced
}

function ReducedMotionGuard({ active }) {
  if (!active) return null
  return jsx('style', {
    'data-reduced-motion-guard': 'true',
    children: '[data-hermes-loadout-root="true"] *,[data-hermes-loadout-root="true"] *::before,[data-hermes-loadout-root="true"] *::after{animation:none!important;transition:none!important}'
  })
}

// Container-measured layout: viewport media queries are wrong for dockable
// panes (a 320px pane on a 1600px screen still matches `sm:`).
// Narrow <640 keeps core controls stacked; medium spans 640–959.
// Wide layouts (960+) may expose the optional horizontally scrollable matrix.
function usePaneLayout(ref) {
  const [layout, setLayout] = useState('narrow')
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(entries => {
      const w = entries && entries[0] ? entries[0].contentRect.width : 0
      if (!w) return
      setLayout(w < 640 ? 'narrow' : w < 960 ? 'medium' : 'wide')
    })
    if (ref.current) ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref])
  return layout
}

// ---------------------------------------------------------------------------
// Tool switch cell — one (skill, tool) pair
// ---------------------------------------------------------------------------

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
  const bypasses = diff && diff.ok && Array.isArray(diff.catalog_bypasses) ? diff.catalog_bypasses.length : 0
  if (diffQuery.isPending || (!broken && !bypasses)) {
    return jsx('button', {
      type: 'button',
      className: 'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover)',
      onClick: () => openControlCenter('problems'),
      'aria-label': 'Loadout health, no known broken links or bypasses',
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
      children: broken ? `${broken} broken` : `${bypasses} catalog bypasses`
    })
  })
}

function CompactSummaryPane() {
  const t = usePluginI18n(ID)
  const rootRef = useRef(null)
  const layout = usePaneLayout(rootRef)
  const reducedMotion = usePrefersReducedMotion()
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
  const issueTotal = problems.broken + problems.drifted + problems.bypasses
  const reviewTotal = issueTotal + protectedProblems
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
    body = jsx(EmptyState, { title: t('noRootTitle'), description: t('noRootDesc'), children: jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => stateQuery.refetch(), children: t('retry') }) })
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
        reviewTotal > 0
          ? jsx('button', {
              type: 'button',
              className: 'flex items-center gap-2 py-1 text-left text-xs text-muted-foreground',
              onClick: () => openControlCenter('problems'),
              children: jsxs('span', {
                className: 'inline-flex items-center gap-2',
                children: [
                  jsx(StatusDot, { tone: 'warn' }),
                  t('problemLine', problems.broken, problems.drifted, protectedProblems, problems.bypasses)
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
              onClick: () => openControlCenter('onboarding'),
              children: t('scan')
            }),
            issueTotal > 0
              ? jsx(Button, {
                  variant: 'secondary',
                  size: 'sm',
                  className: 'w-full',
                  onClick: () => openControlCenter('problems'),
                  children: t('problemsAction', issueTotal)
                })
              : null
          ]
        })
      ]
    })
  }

  return jsxs('div', {
    ref: rootRef,
    'data-hermes-loadout-root': 'true',
    'data-reduced-motion': reducedMotion ? 'true' : 'false',
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      jsx(ReducedMotionGuard, { active: reducedMotion }),
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

function BrokenLinksPanel({ skills, tools, onRepair, onRepairAll, busy }) {
  const t = usePluginI18n(ID)
  const toolById = new Map(tools.map(tool => [tool.id, tool]))
  const broken = []
  skills.forEach(skill => {
    Object.keys(skill.tools || {}).forEach(toolId => {
      const entry = skill.tools[toolId]
      if (entry && entry.state === 'broken-link') {
        broken.push({ skill: skill, tool: toolById.get(toolId) || { id: toolId, label: toolId } })
      }
    })
  })
  if (!broken.length) {
    return jsx(EmptyState, { title: t('brokenEmpty'), description: t('brokenDesc') })
  }
  return jsxs('div', {
    className: 'flex flex-col gap-2 px-3 pb-4',
    children: [
      jsxs('div', { className: 'flex items-center gap-2 px-1 text-xs text-muted-foreground', children: [
        jsx('span', { children: t('brokenDesc') }),
        jsx(Button, { variant: 'secondary', size: 'xs', className: 'ml-auto', disabled: busy, onClick: onRepairAll, children: t('repairAll') })
      ] }),
      broken.map(item => jsxs('div', {
        'data-broken-entry': `${item.skill.id}:${item.tool.id}`,
        className: 'flex flex-wrap items-center gap-2 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
        children: [
          jsx(StatusDot, { tone: 'warn' }),
          jsx('span', { className: 'min-w-0 flex-1 truncate font-medium', children: item.skill.name }),
          jsx('span', { className: 'text-muted-foreground', children: item.tool.label }),
          jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => onRepair(item.skill, item.tool), children: t('repair') })
        ]
      }, `${item.skill.id}:${item.tool.id}`))
    ]
  })
}

function ProtectedEntriesPanel({ diff, tools }) {
  const t = usePluginI18n(ID)
  const toolById = new Map(tools.map(tool => [tool.id, tool]))
  const foreign = diff && diff.ok && Array.isArray(diff.foreign) ? diff.foreign : []
  const unmanaged = diff && diff.ok && Array.isArray(diff.unmanaged) ? diff.unmanaged : []
  const rows = foreign.map(row => ({ ...row, kind: 'foreign' })).concat(
    unmanaged.map(row => ({ ...row, kind: 'unmanaged' }))
  )
  if (!rows.length) {
    return jsx(EmptyState, { title: t('protectedEmpty'), description: t('protectedDesc') })
  }
  return jsxs('section', {
    'data-protected-entries': 'true',
    className: 'flex flex-col gap-2 px-3 pb-4',
    children: [
      jsx('h3', { className: 'font-medium', children: t('protectedTitle', rows.length) }),
      jsx('p', { className: 'text-xs text-muted-foreground', children: t('protectedDesc') }),
      rows.map((row, index) => jsxs('div', {
        className: 'flex min-w-0 items-start gap-2 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
        children: [
          jsx(StatusDot, { tone: 'warn' }),
          jsxs('span', { className: 'min-w-0 flex-1', children: [
            jsx('span', { className: 'block break-words font-medium', children: row.name }),
            jsx('span', {
              className: 'block break-words text-muted-foreground',
              children: t(
                row.kind === 'foreign' ? 'protectedForeignLine' : 'protectedUnmanagedLine',
                (toolById.get(row.tool) || { label: row.tool }).label,
                row.target || ''
              )
            })
          ] })
        ]
      }, `${row.kind}-${row.tool}-${row.name}-${index}`))
    ]
  })
}

function MaintenancePane({ section }) {
  const qc = useQueryClient()
  const ui = useValue(operationUIAtom)
  const [backupSearch, setBackupSearch] = useState('')
  const [page, setPage] = useState(0)
  const [showBackups, setShowBackups] = useState(false)
  const [showLibrary, setShowLibrary] = useState(false)
  const [notifications, setNotifications] = useState(() => {
    try { return !!JSON.parse(storeGet('watchPrefs', '{}')).on } catch (_) { return false }
  })
  const stateQuery = useQuery({ queryKey: STATE_KEY, queryFn: () => pluginCtx.rest('/state'), staleTime: 10000 })
  const diffQuery = useQuery({ queryKey: DIFF_KEY, queryFn: () => pluginCtx.rest('/diff'), staleTime: 10000 })
  const driftQuery = useQuery({ queryKey: DRIFT_KEY, queryFn: () => pluginCtx.rest('/drift'), staleTime: 10000 })
  const backupsQuery = useQuery({ queryKey: [ID, 'backups'], queryFn: () => pluginCtx.rest('/backups').then(requireOk), enabled: showBackups, staleTime: 0 })
  const state = stateQuery.data
  const tools = state && state.ok && Array.isArray(state.tools) ? state.tools : []
  const skills = state && state.ok && Array.isArray(state.skills) ? state.skills : []
  const diff = diffQuery.data
  const drift = driftQuery.data
  const bypasses = diff && diff.ok && Array.isArray(diff.catalog_bypasses) ? diff.catalog_bypasses : []
  const busy = ui.busy || !!ui.preview || !backendReady(state)
  const appLabel = id => tools.find(tool => tool.id === id)?.label || id
  const repair = (skill, tool) => reviewSelections([{ kind: 'skill', app: tool.id, id: skill.id, enabled: true }], 'Repair skill link')
  const repairAll = () => {
    const states = []
    for (const skill of skills) for (const tool of tools) if (skill.tools?.[tool.id]?.state === 'broken-link') {
      states.push({ kind: 'skill', app: tool.id, id: skill.id, enabled: true })
    }
    reviewSelections(states, 'Repair broken links')
  }
  const backups = backupsQuery.data && backupsQuery.data.ok && Array.isArray(backupsQuery.data.backups) ? backupsQuery.data.backups : []
  const filtered = backups.filter(row => `${row.name} ${row.kind}`.toLowerCase().includes(backupSearch.toLowerCase()))
  if (stateQuery.isPending) return jsx(Skeleton, { className: 'm-3 h-32' })
  if (!backendReady(state)) return jsx(ErrorState, { title: 'Updated backend required', description: 'Restart Hermes with the current Loadout plugin. No recovery changes are available while the backend is mismatched.', children: jsx(Button, { onClick: () => stateQuery.refetch(), children: 'Retry' }) })
  if (section === 'problems') return jsx(ScrollArea, { className: 'h-full', children: jsxs('div', { className: 'flex min-w-0 flex-col gap-4 p-3', children: [
    jsxs('header', { className: 'flex flex-wrap items-center gap-2', children: [jsx('h2', { className: 'font-medium', children: 'Issues' }), jsx(Button, { variant: 'ghost', size: 'xs', onClick: () => refreshInventory(qc), children: 'Refresh' })] }),
    jsx('p', { className: 'text-xs text-muted-foreground', children: 'Off is a deliberate choice, not a problem. Review broken links, shared discovery bypasses, or conflicting copies here.' }),
    diffQuery.isError || driftQuery.isError ? jsx(ErrorState, { title: 'Diagnostics unavailable', children: jsx(Button, { onClick: () => { diffQuery.refetch(); driftQuery.refetch() }, children: 'Retry diagnostics' }) }) : null,
    jsx('section', { 'data-catalog-bypasses': 'true', className: 'flex flex-col gap-2', children: bypasses.length ? [
      jsx('h3', { className: 'font-medium', children: 'Catalog bypass' }, 'title'),
      jsx('p', { className: 'text-xs text-muted-foreground', children: 'A broad link exposes library skills regardless of individual switches. Review its removal before relying on Off. Your library and individual links are preserved; refresh the client afterward.' }, 'description'),
      ...bypasses.map(row => jsxs('div', { className: 'flex min-w-0 flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3 text-xs', children: [
        jsx('span', { className: 'font-medium break-words', children: `${appLabel(row.tool)} · ${row.name}` }),
        jsx('span', { className: 'break-all text-muted-foreground', children: row.path }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => reviewOperation('/catalog-bypass/plan', { tool: row.tool, name: row.name }, '/catalog-bypass/repair'), children: 'Review bypass repair' })
      ] }, row.tool + '/' + row.name))
    ] : jsx('p', { className: 'text-xs text-muted-foreground', children: 'No broad library links detected in configured application folders.' }) }),
    jsx('h3', { className: 'font-medium', children: 'Broken links' }),
    jsx(BrokenLinksPanel, { skills: skills, tools: tools, busy: busy, onRepair: repair, onRepairAll: repairAll }),
    jsx('section', { className: 'flex flex-col gap-2', children: [
      jsx('h3', { className: 'font-medium', children: 'Conflicting copies' }, 'title'),
      jsx('p', { className: 'text-xs text-muted-foreground', children: 'Keep both originals until you choose which copy to use. Nothing here rewrites or automatically renames a skill.' }, 'description'),
      ...(drift && drift.ok ? drift.drifted : []).map(row => jsxs('div', { 'data-conflict': `${row.tool}/${row.name}`, className: 'flex min-w-0 flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3 text-xs', children: [
        jsx('span', { className: 'break-words font-medium', children: `${row.name} · ${appLabel(row.tool)}` }),
        jsxs('div', { className: 'flex flex-wrap gap-2', children: [
          jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => reviewOperation('/conflict/plan', { tool: row.tool, name: row.name, choice: 'library' }, '/conflict/apply'), children: 'Use library copy' }),
          jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => reviewOperation('/conflict/plan', { tool: row.tool, name: row.name, choice: 'source' }, '/conflict/apply'), children: 'Use application copy' })
        ] })
      ] }, row.tool + '/' + row.name)),
      drift && drift.ok && !drift.count ? jsx('p', { className: 'text-xs text-muted-foreground', children: 'No differing same-name copies.' }, 'empty') : null
    ] }),
    jsx(ProtectedEntriesPanel, { diff: diff, tools: tools })
  ] }) })
  return jsx(ScrollArea, { className: 'h-full', children: jsxs('div', { 'data-maintenance': 'true', className: 'flex min-w-0 flex-col gap-4 p-3 text-sm', children: [
    jsx('h2', { className: 'font-medium', children: 'Advanced' }),
    jsx('p', { className: 'text-xs text-muted-foreground', children: 'Paths belong to the active Hermes backend, which may be a different computer. Discovery never enables capabilities.' }),
    jsxs('div', { className: 'flex flex-wrap gap-2', children: [
      jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => { storeSet(ONBOARDING_KEY, { ...wizardState(), complete: false, step: 'welcome' }); ccSectionAtom.set('onboarding') }, children: 'Scan and import skills' }),
      jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => setShowLibrary(value => !value), children: 'Manage application paths', 'aria-expanded': showLibrary }),
      jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => setShowBackups(value => !value), children: 'Browse backups', 'aria-expanded': showBackups })
    ] }),
    showLibrary ? jsx(ClientLibrary, { onBack: () => setShowLibrary(false) }) : null,
    jsxs('label', { className: 'flex flex-wrap items-center gap-2 text-xs', children: [
      jsx('input', { type: 'checkbox', checked: notifications, onChange: event => { const on = event.target.checked; setNotifications(on); storeSet('watchPrefs', JSON.stringify({ on: on, arrivals: true, broken: true, drift: true })); watchPrefsEpochAtom.set(watchPrefsEpochAtom.get() + 1) } }),
      'Notify about discoveries and problems, never enable automatically'
    ] }),
    showBackups ? jsxs('section', { className: 'flex min-w-0 flex-col gap-2', children: [
      jsx('h3', { className: 'font-medium', children: 'Preserved backups' }),
      jsx('p', { className: 'text-xs text-muted-foreground', children: 'Configuration backups may contain credentials. Restoration replaces the reviewed file or copy, not just one setting, and preserves the current version for undo. Never share raw backup contents.' }),
      jsx(Button, { variant: 'ghost', size: 'xs', onClick: () => backupsQuery.refetch(), children: 'Refresh backups' }),
      backupsQuery.isPending ? jsx(Skeleton, { className: 'h-16' }) : backupsQuery.isError || backupsQuery.data?.ok === false ? jsx(ErrorState, { title: 'Backups unavailable', children: jsx(Button, { onClick: () => backupsQuery.refetch(), children: 'Retry backups' }) }) : null,
      jsx(SearchField, { 'aria-label': 'Search backups', placeholder: 'Search backups', value: backupSearch, onChange: value => { setBackupSearch(value); setPage(0) } }),
      filtered.slice(page * 20, page * 20 + 20).map(row => jsxs('div', { className: 'flex min-w-0 flex-col gap-1 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs', children: [
        jsx('span', { className: 'break-all font-medium', children: row.name }),
        jsx('span', { className: 'break-all text-muted-foreground', children: row.path }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => reviewOperation('/backups/plan', { path: row.path }, '/backups/restore'), children: 'Review restore' })
      ] }, row.path)),
      jsx('p', { className: 'text-xs text-muted-foreground', children: `${filtered.length} matching backups` }),
      jsxs('div', { className: 'flex gap-2', children: [jsx(Button, { size: 'xs', disabled: page === 0, onClick: () => setPage(value => value - 1), children: 'Previous' }), jsx(Button, { size: 'xs', disabled: (page + 1) * 20 >= filtered.length, onClick: () => setPage(value => value + 1), children: 'Next' })] })
    ] }) : null
  ] }) })
}

function McpPane() {
  const qc = useQueryClient()
  const ui = useValue(operationUIAtom)
  const [search, setSearch] = useState('')
  const [app, setApp] = useState('hermes')
  const query = useQuery({ queryKey: MCP_KEY, queryFn: () => pluginCtx.rest('/mcp/state').then(requireOk), staleTime: 10000, refetchInterval: 30000, retry: 1 })
  const inventory = useQuery({ queryKey: STATE_KEY, queryFn: () => pluginCtx.rest('/state'), staleTime: 10000 })
  const state = query.data
  const rows = state && state.ok && Array.isArray(state.rows) ? state.rows : []
  const writers = [{ id: 'hermes', app: 'hermes', label: 'Hermes' }, { id: 'claude', app: 'claude-desktop', label: 'Claude Desktop' }, { id: 'codex', app: 'codex', label: 'Codex' }]
  const busy = ui.busy || !!ui.preview || !backendReady(inventory.data)
  const review = (servers, writer, enabled) => reviewSelections(servers.map(row => ({ kind: 'mcp', app: writer.app, id: row.name, enabled: enabled })), `${enabled ? 'Enable' : 'Disable'} MCP in ${writer.label}`)
  const filtered = rows.filter(row => row.name.toLowerCase().includes(search.toLowerCase()))
  const names = { missing: 'Not configured', disabled: 'Off', enabled: 'On', drifted: 'Conflict', unsupported: 'Unsupported', unavailable: 'Unavailable' }
  const refresh = () => { refreshInventory(qc); query.refetch() }
  if (query.isPending) return jsx('div', { className: 'p-3', children: jsx(Skeleton, { className: 'h-32 w-full' }) })
  if (query.isError || !state || !state.ok) return jsx(ErrorState, { title: 'MCP inventory unavailable', description: 'Check the Hermes server configuration, then refresh. No server settings were changed.', children: jsx(Button, { onClick: refresh, children: 'Retry' }) })
  return jsxs('div', { className: 'flex h-full min-w-0 flex-col text-sm', children: [
    jsxs('header', { className: 'flex min-w-0 flex-col gap-2 border-b border-(--ui-stroke-secondary) p-3', children: [
      jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [jsx('h2', { className: 'font-medium', children: 'MCP connections' }), jsx(Badge, { variant: 'outline', size: 'xs', children: `${rows.length} servers` }), jsx(Button, { variant: 'ghost', size: 'xs', onClick: refresh, children: 'Refresh' })] }),
      jsx('p', { className: 'text-xs text-muted-foreground', children: 'Hermes supplies server definitions. Each application has its own activation. Credentials stay in the original configurations, never in a loadout.' }),
      state.partial_failure ? jsx('p', { role: 'alert', className: 'text-xs text-(--ui-text-warning)', children: 'One client configuration is unavailable. Other supported clients remain usable. Check the client settings, then refresh.' }) : null,
      state.counts && state.counts.foreign ? jsx('p', { className: 'text-xs text-muted-foreground', children: `${state.counts.foreign} client-only servers are protected and not controlled by these switches.` }) : null,
      !backendReady(inventory.data) ? jsx('p', { role: 'alert', className: 'text-xs text-(--ui-text-warning)', children: 'Fully restart Hermes to load the matching backend before changing activation.' }) : null,
      jsx(SearchField, { 'aria-label': 'Find an MCP server', placeholder: 'Find a server', value: search, onChange: setSearch }),
      jsxs('div', { className: 'flex min-w-0 flex-wrap items-center gap-2', children: [
        jsx('label', { htmlFor: 'mcp-bulk-app', className: 'text-xs', children: 'Application' }),
        jsx('select', { id: 'mcp-bulk-app', 'aria-label': 'MCP bulk application', value: app, onChange: event => setApp(event.target.value), className: 'max-w-full rounded-md border border-(--ui-stroke-secondary) bg-background p-1 text-xs', children: writers.map(writer => jsx('option', { value: writer.app, children: writer.label }, writer.app)) }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy || !filtered.length, onClick: () => review(filtered, writers.find(writer => writer.app === app), true), children: 'Enable visible' }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy || !filtered.length, onClick: () => review(filtered, writers.find(writer => writer.app === app), false), children: 'Disable visible' })
      ] })
    ] }),
    jsx(ScrollArea, { className: 'min-h-0 flex-1', children: filtered.length ? jsx('div', { className: 'flex flex-col gap-2 p-3', children: filtered.map(row =>
      jsxs('article', { 'data-mcp-server': row.name, className: 'min-w-0 rounded-md border border-(--ui-stroke-secondary) p-3', children: [
        jsx('h3', { className: 'break-words font-medium', children: row.name }),
        jsx('div', { className: 'mt-2 flex flex-col gap-2', children: writers.map(writer => {
          const current = writer.id === 'hermes' ? row.enabled ? 'enabled' : 'disabled' : row.writers[writer.id] || 'unavailable'
          const blocked = ['drifted', 'unsupported', 'unavailable'].includes(current)
          return jsxs('div', { className: 'flex min-w-0 flex-wrap items-center gap-2 text-xs', children: [
            jsx('span', { className: 'min-w-0 flex-1', children: writer.label }),
            jsx('span', { className: blocked ? 'text-(--ui-text-warning)' : 'text-muted-foreground', children: names[current] || current }),
            jsx(Switch, { size: 'xs', checked: current === 'enabled', disabled: busy || blocked, 'aria-label': `${row.name} in ${writer.label}`, onCheckedChange: enabled => review([row], writer, enabled) }),
            blocked ? jsx('p', { className: 'w-full text-muted-foreground', children: current === 'drifted' ? 'This client has a different definition. Resolve it in the client configuration; applying a loadout will not overwrite it.' : current === 'unsupported' ? 'This client does not support the configured transport or fields.' : 'This client configuration could not be read safely. Correct it and refresh.' }) : null
          ] }, writer.app)
        }) })
      ] }, row.name)) }) : jsx(EmptyState, { title: rows.length ? 'No matching servers' : 'No MCP servers configured', description: 'Configure a server in Hermes first, then refresh here. Loadout never invents credentials.' }) })
  ] })
}

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
              jsx('span', { 'data-tool-scope': tool.id, className: 'text-xs text-muted-foreground', children: t(tool.scope === 'project' ? 'projectScope' : tool.scope === 'global' ? 'globalScope' : 'libraryCustom') }),
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
      tool.path_error || tool.read_only ? jsx('p', { role: 'alert', className: 'text-xs text-(--ui-text-warning)', children: tool.path_error || (tool.catalog_bypasses && tool.catalog_bypasses.length ? 'Catalog bypass: review the broad link in Issues before changing this application.' : tool.notes || t('libraryReviewCustom')) }) : null,
      tool.shared_with?.length ? jsx('p', { className: 'text-xs text-(--ui-text-warning)', children: t('sharedDirectoryWarning') }) : null,
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
            variant: 'secondary', size: 'xs', disabled: busy || !!tool.read_only || counts.total === 0,
            onClick: onEnableAll, children: t('enableAll')
          }),
          jsx(Button, {
            variant: 'secondary', size: 'xs', disabled: busy || !!tool.read_only || counts.total === 0,
            onClick: onDisableAll, children: t('disableAll')
          })
        ]
      })
    ]
  })
}

function SkillDetails({ skill, onClose }) {
  const qc = useQueryClient()
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const metadataQuery = useQuery({ queryKey: METADATA_KEY, queryFn: () => pluginCtx.rest('/inventory/metadata').then(requireOk), staleTime: 10000 })
  const data = metadataQuery.data
  const fields = data && data.ok && data.skills ? data.skills[skill.id] || {} : {}
  const labels = data && data.ok && Array.isArray(data.classifications) ? data.classifications : ['Unclassified']
  const save = async classification => {
    setBusy(true); setError(null)
    try {
      requireOk(await pluginCtx.rest('/inventory/classify', { method: 'POST', body: { skill: skill.id, classification: classification } }))
      qc.setQueryData(METADATA_KEY, requireOk(await pluginCtx.rest('/inventory/metadata')))
    } catch (caught) { setError(caught.message) }
    finally { setBusy(false) }
  }
  return jsxs('section', { 'data-skill-details': skill.id, className: 'flex min-w-0 flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3 text-xs', children: [
    jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [jsx('h3', { className: 'break-words font-medium', children: skill.name }), jsx(Button, { size: 'xs', variant: 'ghost', onClick: onClose, children: 'Close details' })] }),
    jsx('p', { className: 'break-words', children: skill.description || 'No description provided.' }),
    jsx('span', { children: `Source: ${fields.source || 'Existing library'}` }),
    jsx('label', { children: ['Designed for ', jsx('select', { 'aria-label': `Designed for ${skill.name}`, value: fields.classification || 'Unclassified', disabled: busy || !data?.ok, onChange: event => save(event.target.value), className: 'max-w-full rounded-md border border-(--ui-stroke-secondary) bg-background p-1', children: labels.map(label => jsx('option', { value: label, children: label }, label)) }, 'choice')] }),
    jsx('p', { className: 'text-muted-foreground', children: 'This is your informational label, not a compatibility guarantee. Changing it never changes activation or skill contents.' }),
    error || metadataQuery.isError ? jsx('div', { role: 'alert', children: [jsx('p', { children: error || 'Labels unavailable. Refresh after checking metadata.' }, 'error'), jsx(Button, { size: 'xs', onClick: () => metadataQuery.refetch(), children: 'Retry labels' }, 'retry')] }) : null
  ] })
}

function SingleToolView({ tool, skills, busy, onBack, onToggle, onBulk }) {
  const t = usePluginI18n(ID)
  const [rawQuery, setRawQuery] = useState('')
  const query = useDebounced(rawQuery, 200).trim().toLowerCase()
  const [view, setView] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [showFilters, setShowFilters] = useState(false)
  const [designedFor, setDesignedFor] = useState('all')
  const [sourceFilter, setSourceFilter] = useState('all')
  const [details, setDetails] = useState(null)
  const metadataQuery = useQuery({ queryKey: METADATA_KEY, queryFn: () => pluginCtx.rest('/inventory/metadata').then(requireOk), staleTime: 10000 })
  const metadata = metadataQuery.data && metadataQuery.data.ok ? metadataQuery.data : { skills: {}, classifications: ['Unclassified'] }
  const sources = Array.from(new Set(skills.map(skill => metadata.skills[skill.id]?.source || 'Existing library')))
  const [collapsed, setCollapsed] = useState(() => new Set())
  const [selected, setSelected] = useState(() => new Set())
  const categories = useMemo(() => Array.from(new Set(skills.map(skill => skill.category))), [skills])
  const visible = useMemo(() => skills.filter(skill => {
    const entry = skill.tools && skill.tools[tool.id]
    const stateName = entry ? entry.state : 'missing'
    if (categoryFilter !== 'all' && skill.category !== categoryFilter) return false
    const fields = metadata.skills[skill.id] || {}
    if (designedFor !== 'all' && (fields.classification || 'Unclassified') !== designedFor) return false
    if (sourceFilter !== 'all' && (fields.source || 'Existing library') !== sourceFilter) return false
    if (query && skill.name.toLowerCase().indexOf(query) === -1 && skill.category.toLowerCase().indexOf(query) === -1 && (skill.description || '').toLowerCase().indexOf(query) === -1) return false
    if (view === 'enabled') return stateName === 'enabled'
    if (view === 'issues') return isProblemState(stateName)
    if (view === 'off') return stateName !== 'enabled' && !isProblemState(stateName)
    return true
  }), [skills, tool.id, categoryFilter, query, view, designedFor, sourceFilter, metadataQuery.data])
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
          jsx(Button, { variant: 'secondary', size: 'xs', 'aria-expanded': showFilters, onClick: () => setShowFilters(value => !value), children: 'Filters' }),
          jsx(SegmentedControl, {
            options: [{ id: 'all', label: t('viewAll') }, { id: 'enabled', label: t('viewEnabled') }, { id: 'off', label: t('viewOff') }, { id: 'issues', label: t('viewIssues') }],
            value: view,
            onChange: setView
          })
        ] }),
        showFilters ? jsxs('section', { 'data-more-filters': 'true', className: 'flex min-w-0 flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs', children: [
          jsx('label', { children: ['Category ', jsx('select', { 'aria-label': 'Category', value: categoryFilter, onChange: event => setCategoryFilter(event.target.value), className: 'max-w-full bg-background', children: [jsx('option', { value: 'all', children: 'All categories' }, 'all')].concat(categories.map(category => jsx('option', { value: category, children: category }, category))) }, 'select')] }),
          jsx('label', { children: ['Designed for ', jsx('select', { 'aria-label': 'Designed for', value: designedFor, onChange: event => setDesignedFor(event.target.value), className: 'max-w-full bg-background', children: [jsx('option', { value: 'all', children: 'Any classification' }, 'all')].concat(metadata.classifications.map(label => jsx('option', { value: label, children: label }, label))) }, 'select')] }),
          jsx('label', { children: ['Source ', jsx('select', { 'aria-label': 'Source', value: sourceFilter, onChange: event => setSourceFilter(event.target.value), className: 'max-w-full bg-background', children: [jsx('option', { value: 'all', children: 'Any source' }, 'all')].concat(sources.map(source => jsx('option', { value: source, children: source }, source))) }, 'select')] }),
          jsx(Button, { variant: 'ghost', size: 'xs', onClick: () => { setCategoryFilter('all'); setDesignedFor('all'); setSourceFilter('all') }, children: 'Clear filters' })
        ] }) : null,
        details ? jsx(SkillDetails, { skill: details, onClose: () => setDetails(null) }) : null,
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
              jsxs('div', { className: 'sticky top-0 z-10 flex flex-wrap items-center gap-2 bg-background py-1', children: [
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
                const locked = isProblemState(stateName) && stateName !== 'broken-link'
                return jsxs('div', { 'data-single-tool-skill': skill.id, className: 'flex items-center gap-2 rounded-md px-2 py-2 hover:bg-(--chrome-action-hover)', children: [
                  jsx('input', { type: 'checkbox', checked: selected.has(skill.id), onChange: () => toggleSelected(skill.id), 'aria-label': t('selectSkill', skill.name) }),
                  jsx(StatusDot, { tone: isProblemState(stateName) ? 'warn' : stateName === 'enabled' ? 'good' : 'muted' }),
                  jsxs('span', { className: 'min-w-0 flex-1', children: [
                    jsx('button', { type: 'button', onClick: () => setDetails(skill), className: 'block max-w-full truncate text-left font-medium', 'aria-label': `Details for ${skill.name}`, children: skill.name }),
                    skill.description ? jsx('span', { className: 'block truncate text-xs text-muted-foreground', children: skill.description }) : null
                  ] }),
                  jsx('span', { className: 'text-xs text-muted-foreground', children: stateName }),
                  jsx(Switch, { size: 'xs', checked: stateName === 'enabled', disabled: busy || locked, 'aria-label': `${skill.name} — ${tool.label}`, onCheckedChange: enabled => onToggle(skill, enabled) })
                ] }, skill.id)
              }) })
            ] }, group.category)
          }) })
        : jsx(EmptyState, { title: t('noMatchTitle'), description: t('noMatchDesc') }) }),
      selectedIds.length ? jsxs('div', { 'data-selection-bar': 'sticky', className: 'sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-(--ui-stroke-secondary) bg-background p-3', children: [
        jsx('span', { className: 'text-xs font-medium', children: t('selectedCount', selectedIds.length) }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => onBulk(selectedIds, true, t('scopeSelected', selectedIds.length)), children: t('enableSelected') }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy, onClick: () => onBulk(selectedIds, false, t('scopeSelected', selectedIds.length)), children: t('disableSelected') })
      ] }) : null
    ]
  })
}

function ExpertMatrix({ skills, tools, busy, onToggle }) {
  const t = usePluginI18n(ID)
  const [rawQuery, setRawQuery] = useState('')
  const query = useDebounced(rawQuery, 200).trim().toLowerCase()
  const [showDescriptions, setShowDescriptions] = useState(false)
  const categories = useMemo(() => Array.from(new Set(skills.map(skill => skill.category))), [skills])
  const [collapsed, setCollapsed] = useState(() => new Set(categories))
  const groups = useMemo(() => {
    const grouped = new Map()
    for (const skill of skills) {
      if (
        query &&
        skill.name.toLowerCase().indexOf(query) === -1 &&
        skill.category.toLowerCase().indexOf(query) === -1 &&
        (skill.description || '').toLowerCase().indexOf(query) === -1
      ) {
        continue
      }
      if (!grouped.has(skill.category)) grouped.set(skill.category, [])
      grouped.get(skill.category).push(skill)
    }
    return Array.from(grouped.entries()).map(([category, rows]) => ({ category: category, skills: rows }))
  }, [skills, query])
  const toggleCategory = category => setCollapsed(previous => {
    const next = new Set(previous)
    if (next.has(category)) next.delete(category)
    else next.add(category)
    return next
  })

  return jsxs('div', {
    'data-expert-matrix': 'true',
    className: 'flex min-h-0 min-w-0 flex-1 flex-col',
    children: [
      jsxs('div', { className: 'flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3', children: [
        jsx('h3', { className: 'font-medium', children: t('matrixTitle') }),
        jsx(SearchField, {
          placeholder: t('searchPlaceholder'),
          value: rawQuery,
          onChange: setRawQuery,
          containerClassName: 'ml-auto min-w-[220px]',
          'aria-label': t('searchPlaceholder')
        }),
        jsxs('label', { className: 'flex items-center gap-2 text-xs text-muted-foreground', children: [
          jsx('input', {
            type: 'checkbox',
            checked: showDescriptions,
            onChange: event => setShowDescriptions(event.target.checked),
            'aria-label': t('showDescriptions')
          }),
          t('showDescriptions')
        ] })
      ] }),
      jsx('div', { className: 'min-h-0 min-w-0 flex-1 overflow-auto', children: groups.length
        ? jsxs('table', { className: 'w-max min-w-full border-separate border-spacing-0 text-xs', children: [
            jsx('thead', { children: jsxs('tr', { children: [
              jsx('th', {
                scope: 'col',
                className: 'sticky left-0 top-0 z-30 min-w-[220px] border-b border-r border-(--ui-stroke-secondary) bg-background px-3 py-2 text-left font-medium',
                children: t('matrixTitle')
              }),
              tools.map(tool => jsx('th', {
                scope: 'col',
                'data-matrix-tool': tool.id,
                className: 'sticky top-0 z-20 min-w-[112px] border-b border-(--ui-stroke-secondary) bg-background px-2 py-2 text-center font-medium',
                children: tool.label
              }, tool.id))
            ] }) }),
            groups.map(group => {
              const isCollapsed = !query && collapsed.has(group.category)
              return jsxs('tbody', { children: [
                jsx('tr', { children: jsx('th', {
                  colSpan: tools.length + 1,
                  scope: 'rowgroup',
                  className: 'sticky left-0 border-b border-(--ui-stroke-secondary) bg-background px-3 py-2 text-left',
                  children: jsxs('button', {
                    type: 'button',
                    'aria-expanded': !isCollapsed,
                    onClick: () => toggleCategory(group.category),
                    className: 'flex items-center gap-2 font-medium',
                    children: [`${isCollapsed ? '▸' : '▾'} ${group.category}`, jsx(Badge, { variant: 'outline', size: 'xs', children: String(group.skills.length) }, 'count')]
                  })
                }) }, `${group.category}-heading`),
                isCollapsed ? null : group.skills.map(skill => jsxs('tr', {
                  'data-matrix-skill': skill.id,
                  className: 'hover:bg-(--chrome-action-hover)',
                  children: [
                    jsx('th', {
                      scope: 'row',
                      className: 'sticky left-0 z-10 max-w-[280px] border-b border-r border-(--ui-stroke-secondary) bg-background px-3 py-2 text-left font-normal',
                      children: jsxs('span', { className: 'block min-w-0', children: [
                        jsx('span', { className: 'block truncate font-medium', children: skill.name }),
                        showDescriptions && skill.description
                          ? jsx('span', { className: 'block truncate text-muted-foreground', children: skill.description })
                          : null
                      ] })
                    }),
                    tools.map(tool => {
                      const entry = skill.tools && skill.tools[tool.id]
                      const stateName = entry ? entry.state : 'missing'
                      const locked = isProblemState(stateName) && stateName !== 'broken-link'
                      return jsx('td', {
                        className: 'border-b border-(--ui-stroke-secondary) px-2 py-2 text-center',
                        children: jsx(Switch, {
                          size: 'xs',
                          checked: stateName === 'enabled',
                          disabled: busy || !!tool.read_only || locked,
                          'aria-label': `${skill.name} — ${tool.label}`,
                          onCheckedChange: enabled => onToggle(skill, tool, enabled)
                        })
                      }, tool.id)
                    })
                  ]
                }, skill.id))
              ] }, group.category)
            })
          ] })
        : jsx(EmptyState, { title: t('noMatchTitle'), description: t('noMatchDesc') }) })
    ]
  })
}

// One backend catalog drives discovery, labels, and resolved target paths.
// The desktop bundle stays a single no-build ESM file for the Hermes loader.
function ClientLibrary({ onBack }) {
  const t = usePluginI18n(ID)
  const qc = useQueryClient()
  const [scope, setScope] = useState('global')
  const [search, setSearch] = useState('')
  const [projectInput, setProjectInput] = useState('')
  const [projectRoot, setProjectRoot] = useState('')
  const [revision, setRevision] = useState(0)
  const [catalog, setCatalog] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [customId, setCustomId] = useState('')
  const [customLabel, setCustomLabel] = useState('')
  const [customPath, setCustomPath] = useState('')
  const searchRef = useRef(null)

  useEffect(() => { searchRef.current?.focus() }, [])
  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setCatalog(null)
    const path = '/clients' + (scope === 'project' && projectRoot ? '?project_root=' + encodeURIComponent(projectRoot) : '')
    if (scope === 'project' && !projectRoot) {
      setLoading(false)
      return () => { active = false }
    }
    Promise.resolve().then(() => pluginCtx.rest(path)).then(response => {
      if (!active) return
      if (!response || response.ok !== true) throw new Error(response?.error || t('libraryUnavailable'))
      if (response.catalog_version !== 1 || !Array.isArray(response.clients)) throw new Error(t('libraryVersion'))
      setCatalog(response)
    }).catch(reason => { if (active) setError(reason.message || t('libraryUnavailable')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [scope, projectRoot, revision])

  const activate = async (client, candidate) => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await pluginCtx.rest('/clients/enable', { method: 'POST', body: {
        client_id: client.id, scope: candidate.scope, candidate: candidate.index,
        project_root: candidate.project_root || null, expected_dir: candidate.dir
      } })
      if (!response || response.ok !== true) throw new Error(response?.error || t('librarySaveFailed'))
      qc.invalidateQueries({ queryKey: STATE_KEY })
      setNotice(t('librarySaved', client.label))
      setRevision(value => value + 1)
    } catch (reason) { setError(reason.message || t('librarySaveFailed')) }
    finally { setBusy(false) }
  }
  const addCustom = async () => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await pluginCtx.rest('/config/tools', { method: 'POST', body: {
        id: customId.trim(), label: customLabel.trim(), dir: customPath.trim()
      } })
      if (!response || response.ok !== true) throw new Error(response?.error || t('librarySaveFailed'))
      qc.invalidateQueries({ queryKey: STATE_KEY })
      setNotice(t('librarySaved', customLabel.trim()))
      setCustomId(''); setCustomLabel(''); setCustomPath('')
      setRevision(value => value + 1)
    } catch (reason) { setError(reason.message || t('librarySaveFailed')) }
    finally { setBusy(false) }
  }
  const term = search.trim().toLocaleLowerCase()
  const clients = catalog ? catalog.clients.filter(client => !term || (client.label + ' ' + client.id).toLocaleLowerCase().includes(term)) : []
  const available = clients.filter(client => client.skills && client.may_create && client.verification === 'documented')
    .flatMap(client => (client.candidates || []).filter(candidate => candidate.scope === scope).map(candidate => ({ client, candidate })))
  const renderGroup = (group, rows) => jsxs('section', {
    'data-client-group': group,
    className: 'flex min-w-0 flex-col gap-2',
    children: [jsx('h3', { className: 'font-medium', children: t(group === 'detected' ? 'libraryDetected' : 'libraryAvailable') }),
      ...rows.map(({ client, candidate }) => jsxs('div', {
        'data-client-id': client.id, 'data-client-scope': candidate.scope,
        className: 'flex min-w-0 flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3',
        children: [
          jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
            jsx('span', { className: 'font-medium', children: client.label }),
            jsx(Badge, { size: 'xs', variant: 'outline', children: t(scope === 'global' ? 'globalScope' : 'projectScope') }),
            jsx(Button, { variant: 'secondary', size: 'xs', className: 'ml-auto',
              'aria-label': t('libraryConnectLabel', client.label, scope),
              disabled: busy || !!candidate.error || !!candidate.configured_id || (scope === 'project' && projectInput.trim() !== projectRoot),
              onClick: () => activate(client, candidate),
              children: t(candidate.configured_id ? 'libraryConfigured' : 'libraryConnect') })
          ] }),
          jsx('code', { 'data-candidate-path': client.id, className: 'whitespace-normal break-words text-xs', children: candidate.dir || candidate.error }),
          candidate.shared || (candidate.shared_with || []).length > 1 ? jsx('p', { className: 'text-xs text-(--ui-text-warning)', children: t('sharedDirectoryWarning') }) : null,
          client.notes ? jsx('p', { className: 'text-xs text-muted-foreground', children: client.notes }) : null
        ]
      }, client.id + '-' + candidate.index))]
  })
  return jsxs('div', { 'data-client-library': scope, className: 'flex h-full min-w-0 flex-col', children: [
    jsxs('div', { className: 'flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) p-3', children: [
      jsx(Button, { variant: 'ghost', size: 'xs', onClick: onBack, disabled: busy, children: t('libraryBack') }),
      jsx('h2', { className: 'font-medium', children: t('libraryTitle') })
    ] }),
    jsx(ScrollArea, { className: 'min-h-0 flex-1', children: jsxs('div', { className: 'flex min-w-0 flex-col gap-4 p-4', children: [
      jsx('p', { className: 'text-sm text-muted-foreground', children: t('libraryIntro') }),
      jsx('input', { ref: searchRef, type: 'search', value: search, onChange: event => setSearch(event.target.value),
        'aria-label': t('librarySearch'), placeholder: t('librarySearch'), className: 'w-full min-w-0 rounded-md border border-(--ui-stroke-secondary) bg-transparent p-2 text-sm' }),
      jsxs('div', { role: 'group', 'aria-label': t('libraryScope'), className: 'flex flex-wrap gap-2', children: [
        jsx(Button, { variant: scope === 'global' ? 'primary' : 'secondary', size: 'sm', disabled: busy, 'aria-pressed': scope === 'global', onClick: () => { setScope('global'); setNotice('') }, children: t('globalScope') }),
        jsx(Button, { variant: scope === 'project' ? 'primary' : 'secondary', size: 'sm', disabled: busy, 'aria-pressed': scope === 'project', onClick: () => { setScope('project'); setNotice('') }, children: t('projectScope') })
      ] }),
      jsx('p', { className: 'text-xs text-muted-foreground', children: t(scope === 'global' ? 'globalScopeDesc' : 'projectScopeDesc') }),
      scope === 'project' ? jsxs('div', { className: 'flex min-w-0 flex-col gap-2', children: [
        jsx(Input, { value: projectInput, onChange: event => setProjectInput(event.target.value), 'aria-label': t('projectFolder'), placeholder: t('projectFolder'), className: 'min-w-0 flex-1' }),
        jsx(Button, { variant: 'secondary', size: 'sm', disabled: busy || !projectInput.trim(), onClick: () => { setProjectRoot(projectInput.trim()); setRevision(value => value + 1) }, children: t('reviewProjectFolder') }),
        projectRoot ? jsx('code', { className: 'break-words text-xs', children: projectRoot }) : null,
        projectRoot && projectInput.trim() !== projectRoot ? jsx('p', { role: 'status', className: 'text-xs text-muted-foreground', children: t('projectReviewChanged') }) : null
      ] }) : null,
      notice ? jsx('div', { role: 'status', className: 'text-sm text-(--ui-text-success)', children: notice }) : null,
      error ? jsxs('div', { role: 'alert', className: 'flex flex-col gap-2 text-sm text-(--ui-text-warning)', children: [
        jsx('p', { children: error }), jsx('p', { className: 'text-xs', children: t('libraryRecovery') }),
        jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => setRevision(value => value + 1), children: t('retry') })
      ] }) : null,
      loading ? jsx(Skeleton, { className: 'h-40 w-full' }) : null,
      !loading && catalog ? renderGroup('detected', available.filter(row => row.candidate.detected || row.candidate.configured_id)) : null,
      !loading && catalog ? renderGroup('available', available.filter(row => !row.candidate.detected && !row.candidate.configured_id)) : null,
      !loading && catalog && !available.length ? jsx('p', { className: 'text-sm text-muted-foreground', children: t('libraryNoMatches') }) : null,
      jsxs('section', { 'data-client-group': 'custom', className: 'flex min-w-0 flex-col gap-2 border-t border-(--ui-stroke-secondary) pt-3', children: [
        jsx('h3', { className: 'font-medium', children: t('libraryCustom') }),
        jsx('p', { className: 'text-xs text-muted-foreground', children: t('libraryCustomDesc') }),
        clients.filter(client => client.verification !== 'documented').map(client => jsx('p', { className: 'text-xs text-muted-foreground', children: client.label + ': ' + client.notes }, client.id)),
        jsx(Input, { value: customId, onChange: event => setCustomId(event.target.value), 'aria-label': t('customClientId'), placeholder: t('customClientId') }),
        jsx(Input, { value: customLabel, onChange: event => setCustomLabel(event.target.value), 'aria-label': t('customClientLabel'), placeholder: t('customClientLabel') }),
        jsx(Input, { value: customPath, onChange: event => setCustomPath(event.target.value), 'aria-label': t('customClientPath'), placeholder: t('customClientPath') }),
        customPath.trim() ? jsx('code', { className: 'break-words text-xs', children: customPath.trim() }) : null,
        jsx(Button, { variant: 'secondary', size: 'sm', disabled: busy || !/^[a-z0-9-]{1,32}$/.test(customId.trim()) || customId.trim() === 'hermes' || !customLabel.trim() || !customPath.trim(), onClick: addCustom, children: t('saveCustomClient') })
      ] })
    ] }) })
  ] })
}

// All activation paths share one reviewed operation and one recoverable undo point.
const LOADOUTS_KEY = [ID, 'loadouts']
const OPERATION_KEY = [ID, 'operation']
const METADATA_KEY = [ID, 'metadata']
const operationUIAtom = atom({ busy: false, preview: null, response: null, error: null })
const selectedLoadoutAtom = atom(null)
let reviewGeneration = 0

function requireOk(response) {
  if (!response || response.ok !== true) throw new Error(response && response.error || 'The backend could not finish this request. Refresh and try again.')
  return response
}

function refreshInventory(qc) {
  for (const key of [STATE_KEY, DIFF_KEY, DRIFT_KEY, MCP_KEY, OPERATION_KEY, LOADOUTS_KEY, METADATA_KEY]) {
    qc.invalidateQueries({ queryKey: key })
  }
}

function backendReady(state) {
  return !!(state && state.ok && state.capabilities && state.capabilities.reviewed_operations === 1)
}

async function reviewOperation(path, body, applyPath = '/operations/apply') {
  if (operationUIAtom.get().busy) return
  const generation = ++reviewGeneration
  const context = pluginCtx
  operationUIAtom.set({ ...operationUIAtom.get(), busy: true, preview: null, error: null })
  try {
    const preview = requireOk(await context.rest(path, { method: 'POST', body: body }))
    if (!preview.plan_id || !Array.isArray(preview.items)) throw new Error('The backend is an older version. Restart Hermes and retry.')
    if (generation !== reviewGeneration || context !== pluginCtx) return
    operationUIAtom.set({ ...operationUIAtom.get(), busy: false, preview: { ...preview, applyPath: applyPath } })
  } catch (error) {
    if (generation !== reviewGeneration || context !== pluginCtx) return
    operationUIAtom.set({ ...operationUIAtom.get(), busy: false, preview: null, error: error.message })
  }
}

function reviewSelections(states, label) {
  return reviewOperation('/operations/plan', { states: states, label: label })
}

function cancelOperationReview() {
  if (operationUIAtom.get().busy) return
  reviewGeneration += 1
  operationUIAtom.set({ ...operationUIAtom.get(), preview: null })
}

async function publishOperation(response, qc) {
  operationUIAtom.set({ busy: false, preview: null, response: response, error: response && response.ok === false ? response.error : null })
  refreshInventory(qc)
  try {
    const latest = requireOk(await pluginCtx.rest('/operations/latest'))
    qc.setQueryData(OPERATION_KEY, latest)
  } catch (_) {
    // Keep the just-returned receipt visible even when a refresh cannot connect.
  }
}

async function applyOperation(qc) {
  const state = operationUIAtom.get()
  if (state.busy || !state.preview) return
  const preview = state.preview
  operationUIAtom.set({ ...state, busy: true, error: null })
  try {
    const response = await pluginCtx.rest(preview.applyPath, { method: 'POST', body: { plan_id: preview.plan_id } })
    await publishOperation(response, qc)
    if (response && response.ok) haptic('tap')
  } catch (_) {
    // A disconnected response is ambiguous, not evidence that no write happened.
    await publishOperation({ ok: false, error: 'The response was interrupted. Refresh the last change and inspect recovery before retrying.' }, qc)
  }
}

function OperationItems({ items, tools }) {
  const labelFor = app => app === 'library' ? 'Library' : app === 'claude-desktop' ? 'Claude Desktop' : ((tools || []).find(tool => tool.id === app)?.label || app)
  const labels = { enable: 'Enable', disable: 'Disable', unchanged: 'Unchanged', unavailable: 'Unavailable',
    conflict: 'Conflict', protected: 'Protected', completed: 'Completed', failed: 'Failed', replace: 'Replace reviewed copy', restore: 'Restore', pending: 'Needs recovery' }
  return jsx('div', { className: 'flex max-h-64 flex-col gap-2 overflow-y-auto text-xs', children: (items || []).map((item, index) =>
    jsxs('div', { 'data-operation-item': item.status || 'restore', className: 'min-w-0 rounded-md border border-(--ui-stroke-secondary) p-2', children: [
      jsx('span', { className: 'block break-words font-medium', children: `${item.id || item.name} · ${labelFor(item.app || item.tool) || 'Library'}` }),
      jsx('span', { className: 'text-muted-foreground', children: labels[item.status] || item.status || 'Remove broad link' }),
      item.error || item.reason ? jsx('p', { className: 'mt-1 break-words text-muted-foreground', children: item.error || item.reason }) : null
    ] }, `${item.kind || 'repair'}-${item.app || item.tool}-${item.id || item.name}-${index}`)) })
}

function operationPreviewDescription(preview, tools, actionable) {
  const labelFor = app => app === 'library' ? 'Library' : app === 'claude-desktop' ? 'Claude Desktop' : ((tools || []).find(tool => tool.id === app)?.label || app)
  const labels = { enable: 'Enable', disable: 'Disable', unchanged: 'Unchanged', unavailable: 'Unavailable',
    conflict: 'Conflict', protected: 'Protected', completed: 'Completed', failed: 'Failed', replace: 'Replace reviewed copy', restore: 'Restore', pending: 'Needs recovery' }
  const intro = preview.description || 'Only the listed selections will change. Other applications and unlisted capabilities remain unchanged.'
  const items = (preview.items || []).map(item => {
    const target = labelFor(item.app || item.tool) || 'Library'
    const action = labels[item.status] || item.status || 'Remove broad link'
    const detail = item.error || item.reason
    return `${item.id || item.name} · ${target}: ${action}${detail ? ` — ${detail}` : ''}`
  }).join('; ')
  const ending = actionable
    ? 'Refresh the affected application or start a new session afterward. Entries changed since this preview will be protected.'
    : 'Nothing in this preview can change safely. Close it and resolve the listed issues.'
  return [intro, items, ending].filter(Boolean).join(' ')
}

function OperationsPanel() {
  const qc = useQueryClient()
  const ui = useValue(operationUIAtom)
  const [expanded, setExpanded] = useState(false)
  const latestQuery = useQuery({ queryKey: OPERATION_KEY, queryFn: () => pluginCtx.rest('/operations/latest').then(requireOk), staleTime: 0, refetchOnWindowFocus: true, retry: 1 })
  const stateQuery = useQuery({ queryKey: STATE_KEY, queryFn: () => pluginCtx.rest('/state'), staleTime: 10000 })
  const latest = latestQuery.data
  const responseReceipt = ui.response && (ui.response.operation || ui.response.receipt)
  const receipt = responseReceipt || (latest && latest.receipt)
  const recovery = (latest && latest.recovery_required) || (ui.response && ui.response.code === 'recovery-required')
  const undo = latest && latest.receipt && latest.receipt.undo_available && !recovery
  const preview = ui.preview
  const actionable = preview && preview.items.some(item => ['enable', 'disable', 'restore', 'replace'].includes(item.status) || item.code === 'catalog-bypass')
  const refresh = () => {
    operationUIAtom.set({ ...operationUIAtom.get(), response: null, error: null })
    refreshInventory(qc)
    latestQuery.refetch()
  }
  return jsxs('div', { 'data-operation-panel': 'true', className: 'shrink-0 border-t border-(--ui-stroke-secondary) bg-background p-3 text-xs', children: [
    jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
      jsx('span', { role: 'status', className: 'min-w-0 flex-1 break-words', children: ui.busy ? 'Working…' : recovery ? 'An interrupted change needs recovery.' : receipt ? `${receipt.label}: ${receipt.changed || 0} completed, ${receipt.skipped || 0} unchanged, ${receipt.failed || 0} not completed.` : 'No change to undo yet.' }),
      receipt ? jsx(Button, { variant: 'ghost', size: 'xs', 'aria-expanded': expanded, onClick: () => setExpanded(value => !value), children: expanded ? 'Hide receipt' : 'Show receipt' }) : null,
      jsx(Button, { variant: 'secondary', size: 'xs', disabled: ui.busy || !undo, onClick: () => reviewOperation('/operations/undo-plan', {}, '/operations/undo'), children: 'Undo last change' }),
      jsx(Button, { variant: 'ghost', size: 'xs', disabled: ui.busy, onClick: refresh, children: 'Refresh last change' })
    ] }),
    jsx('p', { className: 'mt-1 text-muted-foreground', children: 'The next successful activation, import, repair, or restore replaces this undo point. Previewing or saving a loadout does not.' }),
    ui.error ? jsx('p', { role: 'alert', className: 'mt-2 break-words text-(--ui-text-danger)', children: ui.error }) : null,
    latestQuery.isError ? jsx('p', { role: 'alert', className: 'mt-2 text-(--ui-text-warning)', children: 'Recovery is unavailable. Fully restart Hermes after updating the backend, then refresh.' }) : null,
    recovery ? jsx('p', { role: 'alert', className: 'mt-2 break-words text-(--ui-text-warning)', children: 'Stop changing these files. Preserve data/hermes-loadout/pending-operation.json and backups in the active Hermes profile. Inspect the affected entries before manual recovery, then refresh. Do not delete a pending record just to retry.' }) : null,
    expanded && receipt ? jsx(OperationItems, { items: receipt.items, tools: stateQuery.data && stateQuery.data.tools }) : null,
    jsx(ConfirmDialog, { open: !!preview, title: preview ? preview.label || 'Repair catalog bypass' : '',
      description: preview ? operationPreviewDescription(preview, stateQuery.data && stateQuery.data.tools, actionable) : null,
      onClose: cancelOperationReview,
      onConfirm: () => { if (!ui.busy) actionable ? applyOperation(qc) : cancelOperationReview() },
      confirmLabel: ui.busy ? 'Working…' : actionable ? 'Apply reviewed changes' : 'Close preview' })
  ] })
}

function useSavedLoadouts() {
  return useQuery({ queryKey: LOADOUTS_KEY, queryFn: () => pluginCtx.rest('/loadouts').then(requireOk), staleTime: 10000, retry: 1 })
}

function selectLoadout(id) {
  selectedLoadoutAtom.set(id || null)
  storeSet('selectedLoadout', id || null)
}

function LoadoutPicker() {
  const controlId = useId()
  const query = useSavedLoadouts()
  const active = useValue(selectedLoadoutAtom) || ''
  const operation = useValue(operationUIAtom)
  const rows = query.data && query.data.ok && Array.isArray(query.data.loadouts) ? query.data.loadouts : []
  const selected = rows.some(row => row.id === active) ? active : ''
  return jsxs('div', { 'data-loadout-picker': 'true', className: 'flex min-w-0 flex-wrap items-center gap-2', children: [
    jsx('label', { htmlFor: controlId, className: 'text-xs text-muted-foreground', children: 'Loadout' }),
    jsx('select', { id: controlId, 'aria-label': 'Saved loadout', value: selected, disabled: query.isPending || operation.busy,
      className: 'min-w-0 max-w-full flex-1 rounded-md border border-(--ui-stroke-secondary) bg-background px-2 py-1 text-xs',
      onChange: event => selectLoadout(event.target.value), children: [jsx('option', { value: '', children: query.isError ? 'Unavailable, retry in Loadouts' : rows.length ? 'Choose a saved loadout' : 'No saved loadouts yet' }, 'none')].concat(rows.map(row => jsx('option', { value: row.id, children: row.name }, row.id))) }),
    jsx(Button, { variant: 'secondary', size: 'xs', disabled: !selected || operation.busy, onClick: () => reviewOperation('/loadouts/plan', { loadout_id: selected }), children: 'Review loadout' }),
    jsx(Button, { variant: 'ghost', size: 'xs', onClick: () => ccSectionAtom.set('loadouts'), children: 'Manage loadouts' })
  ] })
}

const selectionKey = row => `${row.kind}\u0000${row.app}\u0000${row.id}`

function LoadoutManager() {
  const editorId = useId()
  const editingSelection = useRef(undefined)
  const query = useSavedLoadouts()
  const qc = useQueryClient()
  const operation = useValue(operationUIAtom)
  const selected = useValue(selectedLoadoutAtom) || ''
  const [draft, setDraft] = useState(null)
  const [apps, setApps] = useState([])
  const [editingApp, setEditingApp] = useState('hermes')
  const [search, setSearch] = useState('')
  const [editingCapabilities, setEditingCapabilities] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const stateQuery = useQuery({ queryKey: STATE_KEY, queryFn: () => pluginCtx.rest('/state').then(requireOk), staleTime: 10000 })
  const mcpQuery = useQuery({ queryKey: MCP_KEY, queryFn: () => pluginCtx.rest('/mcp/state').then(requireOk), staleTime: 10000 })
  const state = stateQuery.data
  const list = query.data && Array.isArray(query.data.loadouts) ? query.data.loadouts : []
  const record = list.find(row => row.id === selected)
  const availableApps = state && state.ok ? state.tools.map(tool => ({ id: tool.id, label: tool.label })) : []
  for (const app of [{ id: 'hermes', label: 'Hermes' }, { id: 'codex', label: 'Codex' }, { id: 'claude-desktop', label: 'Claude Desktop' }]) {
    if (!availableApps.some(row => row.id === app.id)) availableApps.push(app)
  }
  for (const row of draft && draft.states || []) {
    if (!availableApps.some(app => app.id === row.app)) availableApps.push({ id: row.app, label: `${row.app} (unavailable)` })
  }
  const beginEdit = row => {
    setDraft({ id: row.id || null, name: row.name, states: row.states.map(item => ({ ...item })) })
    const ids = Array.from(new Set(row.states.map(item => item.app)))
    setApps(ids.length ? ids : ['hermes'])
    setEditingApp(ids[0] || 'hermes')
    setError(null); setNotice(null); setEditingCapabilities(false)
  }
  // Hydrate when selection changes, not on background refreshes that could
  // erase unsaved edits. An explicit New draft has no saved selection.
  useEffect(() => {
    if (editingSelection.current === selected || (selected && !record)) return
    editingSelection.current = selected
    if (record) beginEdit(record)
    else setDraft(null)
  }, [selected, record])
  const updateRecord = async (path, body) => {
    if (working) return
    setWorking(true); setError(null); setNotice(null)
    try {
      const result = requireOk(await pluginCtx.rest(path, { method: 'POST', body: body }))
      const data = requireOk(await pluginCtx.rest('/loadouts'))
      qc.setQueryData(LOADOUTS_KEY, data)
      if (body.action === 'delete') { selectLoadout(''); setDraft(null) }
      else { editingSelection.current = result.loadout.id; selectLoadout(result.loadout.id); beginEdit(result.loadout) }
      setNotice(body.action === 'delete' ? 'Loadout deleted. Application activation was not changed.' : 'Loadout saved. Application activation was not changed.')
    } catch (caught) { setError(caught.message) }
    finally { setWorking(false); setDeleting(false) }
  }
  const capture = async () => {
    if (working || !apps.length) return
    setWorking(true); setError(null)
    try {
      const data = requireOk(await pluginCtx.rest('/loadouts/capture', { method: 'POST', body: { apps: apps } }))
      setDraft(previous => ({ ...previous, states: data.states }))
      setNotice(`${data.states.length} current selections captured into this draft. ${(data.excluded || []).length} protected or unavailable selections were not captured. Nothing was activated.`)
      setEditingCapabilities(true)
    } catch (caught) { setError(caught.message) }
    finally { setWorking(false) }
  }
  const changeApp = (app, checked) => {
    setApps(previous => checked ? [...previous, app] : previous.filter(id => id !== app))
    if (!checked) setDraft(previous => ({ ...previous, states: previous.states.filter(row => row.app !== app) }))
    if (checked) setEditingApp(app)
  }
  const setDesired = (row, value) => setDraft(previous => {
    const rest = previous.states.filter(item => selectionKey(item) !== selectionKey(row))
    return { ...previous, states: value === 'ignore' ? rest : rest.concat({ kind: row.kind, app: row.app, id: row.id, enabled: value === 'on' }) }
  })
  const capabilities = []
  if (apps.includes(editingApp) && state && state.ok && state.tools.some(tool => tool.id === editingApp)) {
    for (const skill of state.skills) capabilities.push({ kind: 'skill', app: editingApp, id: skill.id, label: skill.name })
  }
  if (apps.includes(editingApp) && ['hermes', 'codex', 'claude-desktop'].includes(editingApp) && mcpQuery.data && mcpQuery.data.ok) {
    for (const server of mcpQuery.data.rows) capabilities.push({ kind: 'mcp', app: editingApp, id: server.name, label: server.name })
  }
  for (const row of draft && draft.states || []) {
    if (row.app === editingApp && !capabilities.some(item => selectionKey(item) === selectionKey(row))) capabilities.push({ ...row, label: row.id, unavailable: true })
  }
  const filtered = capabilities.filter(row => `${row.label} ${row.kind}`.toLowerCase().includes(search.toLowerCase()))
  const dirty = draft && (!record || draft.name !== record.name || JSON.stringify(draft.states) !== JSON.stringify(record.states))
  const busy = working || operation.busy || !!operation.preview || !backendReady(state)
  if (query.isPending) return jsx(Skeleton, { className: 'm-3 h-32' })
  if (query.isError || !query.data || query.data.ok !== true) return jsx(ErrorState, { title: 'Loadouts unavailable', description: 'Restart the updated backend or correct its saved records, then retry.', children: jsx(Button, { onClick: () => query.refetch(), children: 'Retry' }) })
  return jsx(ScrollArea, { className: 'h-full', children: jsxs('div', { 'data-loadout-manager': 'true', className: 'flex min-w-0 flex-col gap-3 p-3', children: [
    jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [jsx('h2', { className: 'font-medium', children: 'Named loadouts' }), jsx(Button, { variant: 'primary', size: 'xs', disabled: busy, onClick: () => { editingSelection.current = ''; selectLoadout(''); beginEdit({ name: '', states: [] }) }, children: 'New loadout' })] }),
    jsx('p', { className: 'text-xs text-muted-foreground', children: 'Save desired skill and MCP states for selected applications. Saving never applies them. Unlisted capabilities remain unchanged when you apply.' }),
    jsx(LoadoutPicker, {}),
    notice ? jsx('p', { role: 'status', className: 'text-xs text-muted-foreground', children: notice }) : null,
    error ? jsx('p', { role: 'alert', className: 'break-words text-xs text-(--ui-text-danger)', children: error }) : null,
    !draft ? jsx(EmptyState, { title: 'Choose or create a loadout', description: 'Start empty, or capture your current choices. No starter activates capabilities for you.' }) : jsxs('div', { className: 'flex min-w-0 flex-col gap-3', children: [
      jsx('label', { htmlFor: `${editorId}-name`, className: 'text-xs', children: 'Name' }),
      jsx(Input, { id: `${editorId}-name`, 'aria-label': 'Loadout name', maxLength: 64, value: draft.name, onChange: event => setDraft(previous => ({ ...previous, name: event.target.value })), placeholder: 'For example, Research', disabled: busy }),
      jsx('fieldset', { className: 'min-w-0', children: [jsx('legend', { className: 'mb-2 text-xs', children: 'Applications in this loadout' }, 'legend'), jsx('div', { className: 'flex flex-wrap gap-3', children: availableApps.map(app => jsxs('label', { className: 'flex items-center gap-1.5 text-xs', children: [jsx('input', { type: 'checkbox', checked: apps.includes(app.id), disabled: busy, 'aria-label': `Include ${app.label}`, onChange: event => changeApp(app.id, event.target.checked) }), app.label] }, app.id)) }, 'choices')] }),
      jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy || !apps.length || !backendReady(state), onClick: capture, children: 'Capture current selections' }),
        jsx(Button, { variant: 'ghost', size: 'xs', 'aria-expanded': editingCapabilities, onClick: () => setEditingCapabilities(value => !value), children: editingCapabilities ? 'Hide capability choices' : 'Edit capabilities' }),
        jsx('span', { className: 'text-xs text-muted-foreground', children: `${draft.states.length} explicit selections` })
      ] }),
      editingCapabilities ? jsxs('section', { className: 'flex min-w-0 flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3', children: [
        jsx('label', { htmlFor: `${editorId}-app`, className: 'text-xs', children: 'Edit one application' }),
        jsx('select', { id: `${editorId}-app`, 'aria-label': 'Edit application', value: editingApp, onChange: event => setEditingApp(event.target.value), className: 'max-w-full rounded-md border border-(--ui-stroke-secondary) bg-background p-1 text-xs', children: availableApps.filter(app => apps.includes(app.id)).map(app => jsx('option', { value: app.id, children: app.label }, app.id)) }),
        jsx(SearchField, { 'aria-label': 'Find a capability', placeholder: 'Find a skill or MCP server', value: search, onChange: setSearch }),
        jsx('p', { className: 'text-xs text-muted-foreground', children: 'Leave unchanged excludes an entry from this loadout. On and Off are explicit desired states, not live switches.' }),
        filtered.slice(0, 100).map(row => {
          const desired = draft.states.find(item => selectionKey(item) === selectionKey(row))
          return jsxs('label', { className: 'flex min-w-0 flex-wrap items-center gap-2 text-xs', children: [
            jsx('span', { className: 'min-w-0 flex-1 break-words', children: `${row.label} (${row.kind === 'mcp' ? 'MCP' : 'Skill'}${row.unavailable ? ', unavailable' : ''})` }),
            jsx('select', { 'aria-label': `Desired ${row.kind} ${row.label} in ${editingApp}`, value: desired ? desired.enabled ? 'on' : 'off' : 'ignore', disabled: busy,
              className: 'max-w-full rounded-md border border-(--ui-stroke-secondary) bg-background p-1', onChange: event => setDesired(row, event.target.value), children: [jsx('option', { value: 'ignore', children: 'Leave unchanged' }, 'ignore'), jsx('option', { value: 'on', children: 'On' }, 'on'), jsx('option', { value: 'off', children: 'Off' }, 'off')] })
          ] }, selectionKey(row))
        }),
        filtered.length > 100 ? jsx('p', { className: 'text-xs text-muted-foreground', children: `Showing 100 of ${filtered.length}. Refine the search to edit the rest; all saved selections are retained.` }) : null
      ] }) : null,
      jsxs('div', { className: 'flex flex-wrap gap-2', children: [
        jsx(Button, { variant: 'primary', size: 'xs', disabled: busy || !draft.name.trim(), onClick: () => updateRecord('/loadouts/save', { name: draft.name, states: draft.states, ...(draft.id ? { loadout_id: draft.id } : {}) }), children: 'Save loadout' }),
        jsx(Button, { variant: 'secondary', size: 'xs', disabled: busy || !draft.id || dirty, onClick: () => reviewOperation('/loadouts/plan', { loadout_id: draft.id }), children: 'Review saved loadout' }),
        jsx(Button, { variant: 'ghost', size: 'xs', disabled: busy || !draft.id, onClick: () => updateRecord('/loadouts/edit', { loadout_id: draft.id, action: 'duplicate' }), children: 'Duplicate' }),
        jsx(Button, { variant: 'ghost', size: 'xs', disabled: busy || !draft.id, onClick: () => setDeleting(true), children: 'Delete loadout' })
      ] }),
      dirty ? jsx('p', { className: 'text-xs text-muted-foreground', children: 'Save your edits before applying this loadout.' }) : null
    ] }),
    jsx(ConfirmDialog, { open: deleting, title: 'Delete this saved loadout?', description: 'This removes the saved selection only. No application or skill files will change.', confirmLabel: 'Delete loadout', onClose: () => setDeleting(false), onConfirm: () => draft && updateRecord('/loadouts/edit', { loadout_id: draft.id, action: 'delete' }) })
  ] }) })
}

function ArrivalBanner({ arrivals, onDismiss }) {
  return jsxs('aside', { 'data-arrivals': 'true', className: 'm-3 flex flex-wrap items-center gap-2 rounded-md border border-(--ui-stroke-secondary) p-3 text-xs', children: [
    jsx('p', { className: 'min-w-0 flex-1', children: `${arrivals.length} newly observed skills. Discovery does not enable them.` }),
    jsx(Button, { variant: 'ghost', size: 'xs', onClick: onDismiss, children: 'Dismiss' })
  ] })
}

function ToolsOverview({ layout }) {
  const t = usePluginI18n(ID)
  const qc = useQueryClient()
  const onboarding = storeGet(ONBOARDING_KEY, { version: ONBOARDING_VERSION, complete: false })
  const onboardingComplete = onboarding && onboarding.version === ONBOARDING_VERSION && onboarding.complete === true
  const [selectedTool, setSelectedTool] = useState(null)
  const [showLibrary, setShowLibrary] = useState(false)
  const [viewMode, setViewMode] = useState('cards')
  const operation = useValue(operationUIAtom)
  const arrivals = useValue(arrivalsAtom)
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
  const overviewIssues = problemTotals.broken + problemTotals.drifted + problemTotals.bypasses

  useEffect(() => {
    if (layout !== 'wide') setViewMode('cards')
  }, [layout])

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

  const busy = operation.busy || !!operation.preview || !backendReady(state)
  const openBulkPlan = (tool, enabled, skillIds) => reviewSelections(
    skillIds.map(id => ({ kind: 'skill', app: tool.id, id: id, enabled: enabled })),
    `${enabled ? 'Enable' : 'Disable'} skills in ${tool.label}`)
  const onToggle = (skill, tool, enabled) => reviewSelections(
    [{ kind: 'skill', app: tool.id, id: skill.id, enabled: enabled }],
    `${enabled ? 'Enable' : 'Disable'} ${skill.name}`)
  const onArrivalDismiss = () => { markSkillsSeen(arrivals); arrivalsAtom.set([]) }

  if (state && (!state.ok || !backendReady(state))) return jsx(ErrorState, {
    title: 'Backend update required', description: state.error || 'Fully restart Hermes after updating Loadout. Activation stays unavailable until the matching backend responds.',
    children: jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => stateQuery.refetch(), children: 'Retry' })
  })
  if (showLibrary) return jsx(ClientLibrary, { onBack: () => setShowLibrary(false) })
  if (selectedTool) {
    const currentTool = tools.find(tool => tool.id === selectedTool.id) || selectedTool
    return jsxs('div', {
      'data-single-tool-layout': 'true',
      className: 'flex h-full min-w-0 flex-col',
      children: [
        jsx('div', { 'data-single-tool-shell': 'true', className: 'min-h-0 min-w-0 flex-1', children: jsx(SingleToolView, {
          tool: currentTool,
          skills: skills,
          busy: busy || !!currentTool.read_only,
          onBack: () => setSelectedTool(null),
          onToggle: (skill, enabled) => onToggle(skill, currentTool, enabled),
          onBulk: (skillIds, enabled, scope) => openBulkPlan(selectedTool, enabled, skillIds, scope)
        }) })
      ]
    })
  }

  let body
  if (stateQuery.isPending || (stateQuery.isLoading && !state)) {
    body = jsx('div', { className: 'grid grid-cols-1 gap-3 p-4', children: [0, 1, 2].map(index => jsx(Skeleton, { className: 'h-40 w-full' }, `tool-card-${index}`)) })
  } else if (stateQuery.isError || (state && state.ok === false)) {
    body = jsx(ErrorState, {
      title: t('errorTitle'), description: state?.error || t('errorDesc'),
      children: jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => stateQuery.refetch(), children: t('retry') })
    })
  } else if (state && state.ok && !state.skills_root_exists) {
    body = jsx(EmptyState, { title: t('noRootTitle'), description: t('noRootDesc'), children: jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => stateQuery.refetch(), children: t('retry') }) })
  } else if (layout === 'wide' && viewMode === 'matrix') {
    body = jsx(ExpertMatrix, {
      skills: skills,
      tools: tools,
      busy: busy,
      onToggle: onToggle
    })
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
          overviewIssues ? jsx(Badge, { variant: 'warn', size: 'xs', children: t('overviewProblems', overviewIssues) }) : null,
          layout === 'wide'
            ? jsx(Button, {
                variant: 'secondary', size: 'xs', className: 'ml-auto',
                'aria-pressed': viewMode === 'matrix',
                onClick: () => setViewMode(current => current === 'cards' ? 'matrix' : 'cards'),
                children: viewMode === 'matrix' ? t('ccTools') : t('matrixToggle')
              })
            : null,
          jsx(Button, { variant: 'secondary', size: 'xs', className: layout === 'wide' ? undefined : 'ml-auto', onClick: () => ccSectionAtom.set('onboarding'), children: t('scanAndImport') }),
          jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => setShowLibrary(true), children: t('addToolAction') })
        ]
      }),
      !onboardingComplete
        ? jsxs('div', {
            'data-onboarding-entry': 'true',
            className: 'mx-4 mt-3 flex flex-wrap items-center gap-2 rounded-md border border-(--ui-stroke-secondary) p-3',
            children: [
              jsxs('div', { className: 'min-w-0 flex-1', children: [
                jsx('div', { className: 'font-medium', children: t('onboardingEntryTitle') }),
                jsx('div', { className: 'text-xs text-muted-foreground', children: t('onboardingEntryDesc') })
              ] }),
              jsx(Button, { variant: 'primary', size: 'xs', onClick: () => ccSectionAtom.set('onboarding'), children: t('startSetup') })
            ]
          })
        : null,
      arrivals.length
        ? jsx(ArrivalBanner, {
            arrivals: arrivals,
            tools: tools,
            onDismiss: onArrivalDismiss,
            busy: busy
          })
        : null,
      jsx(ScrollArea, { className: 'min-h-0 flex-1', children: body })
    ]
  })
}

const WIZARD_STEPS = ['welcome', 'sources', 'scanning', 'review', 'choose', 'preview', 'apply', 'receipt']

function wizardState() {
  const saved = storeGet(ONBOARDING_KEY, {})
  if (!saved || saved.version !== ONBOARDING_VERSION) {
    return { version: ONBOARDING_VERSION, complete: false, step: 'welcome', selectedTools: [], scanRoots: [], category: 'imported' }
  }
  return {
    version: ONBOARDING_VERSION,
    complete: saved.complete === true,
    step: WIZARD_STEPS.indexOf(saved.step) <= 1 ? saved.step : 'sources',
    selectedTools: Array.isArray(saved.selectedTools) ? saved.selectedTools : [],
    scanRoots: Array.isArray(saved.scanRoots) ? saved.scanRoots : [],
    category: typeof saved.category === 'string' && saved.category ? saved.category : 'imported',
    lastScan: saved.lastScan
  }
}

function ImportPlanPreview({ plan, chosen }) {
  const t = usePluginI18n(ID)
  const totals = plan && plan.totals ? plan.totals : {}
  const refused = plan && Array.isArray(plan.refused) ? plan.refused : []
  return jsxs('div', {
    'data-import-preview': 'true',
    className: 'flex max-h-72 flex-col gap-2 overflow-y-auto text-left text-xs',
    children: [
      jsx('div', { className: 'font-medium', children: t('wizardPreviewCounts', chosen.length, totals.sources || 0, totals.conflicts || 0, refused.length) }),
      chosen.slice(0, 5).map(row => jsxs('div', {
        'data-import-example': row.name,
        children: [
          jsx('div', { className: 'font-medium', children: row.name }),
          jsx('div', { className: 'break-words text-muted-foreground', children: row.path || `${row.source}/${row.name}` })
        ]
      }, `${row.source}-${row.name}`)),
      refused.length ? jsxs('div', { children: [
        jsx('div', { className: 'font-medium text-(--ui-text-warning)', children: t('wizardRefusals') }),
        refused.map((row, index) => jsx('div', {
          'data-import-refusal': row.code,
          className: 'break-words text-(--ui-text-warning)',
          children: t('wizardRefusalLine', row.root || row.path || row.tool || t('unknownPath'), row.code)
        }, `${row.root || row.path || row.tool}-${index}`))
      ] }) : null
    ]
  })
}

function ImportReceipt({ response, undoResults }) {
  const t = usePluginI18n(ID)
  const receipt = response && response.receipt ? response.receipt : {}
  const results = response && Array.isArray(response.results) ? response.results : []
  const undo = Array.isArray(receipt.undo) ? receipt.undo : []
  return jsxs('div', {
    'data-import-receipt': receipt.receipt_id || 'receipt',
    className: 'flex flex-col gap-3',
    children: [
      jsxs('div', { className: 'rounded-md border border-(--ui-stroke-secondary) p-3', children: [
        jsx('div', { className: 'font-medium', children: t('wizardReceiptCounts', receipt.adopted || 0, receipt.failed || 0, receipt.refused || 0) }),
        receipt.receipt_id ? jsx('div', { className: 'text-xs text-muted-foreground', children: t('bulkReceiptId', receipt.receipt_id) }) : null,
        results.map(row => jsx('div', {
          'data-import-result': row.name,
          className: row.ok ? 'text-xs text-muted-foreground' : 'text-xs text-(--ui-text-danger)',
          children: t('wizardResultLine', row.name, row.ok
            ? t(row.tool == null ? 'wizardAddedToLibrary' : 'wizardSourceKeptActive')
            : row.error || row.code || t('bulkFailed'))
        }, `${row.source}-${row.name}`))
      ] }),
      jsxs('div', { className: 'rounded-md border border-(--ui-stroke-secondary) p-3', children: [
        jsx('div', { className: 'font-medium', children: t('wizardUndoTitle') }),
        jsx('div', { className: 'mb-2 text-xs text-muted-foreground', children: t('wizardUndoDesc') }),
        undo.map((row, index) => jsx('div', {
          'data-import-undo': row.kind,
          className: 'break-words text-xs text-muted-foreground',
          children: t('wizardUndoLine', row.kind, row.path, row.backup || t('none'))
        }, `${row.path}-${index}`)),
        undoResults && undoResults.length ? jsx('div', {
          'data-import-undo-results': 'true',
          className: undoResults.some(row => !row.ok) ? 'mt-2 text-xs text-(--ui-text-danger)' : 'mt-2 text-xs text-muted-foreground',
          children: t('wizardUndoResult', undoResults.filter(row => row.ok).length, undoResults.filter(row => !row.ok).length)
        }) : null
      ] })
    ]
  })
}

function FirstRunWizard() {
  const t = usePluginI18n(ID)
  const qc = useQueryClient()
  const saved = wizardState()
  const [step, setStep] = useState(saved.step || 'welcome')
  const [selectedTools, setSelectedTools] = useState(() => new Set(saved.selectedTools))
  const [scanRoots, setScanRoots] = useState(saved.scanRoots)
  const [folderInput, setFolderInput] = useState('')
  const [category, setCategory] = useState(saved.category)
  const [plan, setPlan] = useState(null)
  const [chosenKeys, setChosenKeys] = useState(() => new Set())
  const [confirm, setConfirm] = useState(null)
  const [receipt, setReceipt] = useState(null)
  const [undoResults, setUndoResults] = useState(null)
  const stateQuery = useQuery({
    queryKey: STATE_KEY,
    queryFn: () => (pluginCtx ? pluginCtx.rest('/state') : Promise.reject(new Error('no backend'))),
    staleTime: 0,
    retry: 1
  })
  const state = stateQuery.data
  const knownToolIds = state && state.ok && Array.isArray(state.tools)
    ? state.tools.filter(tool => tool.id !== 'hermes' && tool.special !== 'config' && !tool.read_only).map(tool => tool.id)
    : []
  const detectedTools = state && state.ok && Array.isArray(state.tools)
    ? state.tools.filter(tool => tool.id !== 'hermes' && tool.special !== 'config' && tool.present && !tool.read_only)
    : []

  useEffect(() => {
    if (selectedTools.size || !detectedTools.length) return
    setSelectedTools(new Set(detectedTools.map(tool => tool.id)))
  }, [detectedTools.map(tool => tool.id).join('|')])

  const saveProgress = (nextStep, extra = {}) => {
    const payload = {
      version: ONBOARDING_VERSION,
      complete: false,
      step: nextStep,
      selectedTools: Array.from(selectedTools),
      scanRoots: scanRoots.slice(),
      category: category,
      ...extra
    }
    storeSet(ONBOARDING_KEY, payload)
    setStep(nextStep)
  }

  const entryKey = row => `${row.source}\u0000${row.name}`
  const entries = plan && Array.isArray(plan.entries) ? plan.entries : []
  const adoptable = plan && Array.isArray(plan.adoptable) ? plan.adoptable : []
  const chosen = adoptable.filter(row => chosenKeys.has(entryKey(row)) && (row.tool == null || selectedTools.has(row.tool)))
  const groupedKinds = [
    ['managed', 'wizardManaged'], ['unmanaged-skill', 'wizardUnique'],
    ['identical-duplicate', 'wizardIdentical'], ['drifted', 'wizardDrifted'],
    ['name-conflict', 'wizardConflicts'], ['broken-link', 'wizardBroken'],
    ['foreign-link', 'wizardForeign'], ['unmanaged-dir', 'wizardUnmanaged']
  ]

  const scanMutation = useMutation({
    mutationFn: vars => pluginCtx.rest('/import/plan', { method: 'POST', body: vars }),
    onSuccess: data => {
      if (!data || data.ok !== true) {
        host.notify({ kind: 'error', message: t('wizardScanFailed') })
        saveProgress('sources')
        return
      }
      setPlan(data)
      setChosenKeys(new Set((data.adoptable || []).map(entryKey)))
      storeSet(ONBOARDING_KEY, {
        version: ONBOARDING_VERSION, complete: false, step: 'sources',
        selectedTools: Array.from(selectedTools), scanRoots: scanRoots.slice(), category: category,
        lastScan: new Date().toISOString()
      })
      setStep('review')
    },
    onError: err => {
      host.notifyError(err, t('wizardScanFailed'))
      saveProgress('sources')
    }
  })

  const applyMutation = useMutation({
    mutationFn: vars => pluginCtx.rest('/import/apply-plan', { method: 'POST', body: vars }),
    onSuccess: data => {
      if (!data || data.ok !== true) {
        publishOperation(data || { ok: false, error: 'Import returned no receipt. Refresh recovery before retrying.' }, qc)
        setStep('sources')
        return
      }
      setReceipt(data)
      publishOperation(data, qc)
      setStep('receipt')
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
      qc.invalidateQueries({ queryKey: DRIFT_KEY })
      const details = data.receipt || {}
      host.notify({ kind: details.failed || details.refused ? 'error' : 'success', message: t('wizardApplied', details.adopted || 0, details.failed || 0, details.refused || 0) })
    },
    onError: () => {
      publishOperation({ ok: false, error: 'The import response was interrupted. Refresh recovery before another import.' }, qc)
      setStep('sources')
    }
  })

  const toggleTool = toolId => setSelectedTools(previous => {
    const next = new Set(previous)
    if (next.has(toolId)) next.delete(toolId)
    else next.add(toolId)
    return next
  })
  const toggleEntry = row => setChosenKeys(previous => {
    const next = new Set(previous)
    const key = entryKey(row)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })
  const addFolder = () => {
    const value = folderInput.trim()
    if (!value || scanRoots.indexOf(value) !== -1) return
    setScanRoots(previous => previous.concat(value))
    setFolderInput('')
  }
  const runScan = () => {
    const tools = Array.from(selectedTools)
    if (!tools.length && !scanRoots.length) return
    saveProgress('scanning')
    scanMutation.mutate({ tools: tools, scan_roots: scanRoots.slice(), category: category })
  }
  const startApply = () => setConfirm({
    title: t('wizardConfirmTitle'),
    description: t('wizardConfirmDescription', chosen.length),
    confirmLabel: t('wizardConfirmApply'),
    action: () => {
      setConfirm(null)
      setStep('apply')
      applyMutation.mutate({ plan_id: plan.plan_id, entries: chosen.map(row => ({ name: row.name, source: row.source, tool: row.tool == null ? null : row.tool })), category: category })
    }
  })
  const undoAdoption = () => reviewOperation('/operations/undo-plan', {}, '/operations/undo')
  const finish = () => {
    storeSet(ONBOARDING_KEY, {
      version: ONBOARDING_VERSION, complete: true, step: 'welcome',
      selectedTools: Array.from(selectedTools), scanRoots: scanRoots.slice(), category: category,
      lastScan: new Date().toISOString()
    })
    ccSectionAtom.set('tools')
  }

  const stepIndex = Math.max(0, WIZARD_STEPS.indexOf(step))
  let body
  if (step === 'welcome') {
    body = jsxs('div', { className: 'flex max-w-2xl flex-col gap-3', children: [
      jsx('h2', { className: 'text-lg font-medium', children: t('wizardWelcomeTitle') }),
      jsx('p', { className: 'text-sm text-muted-foreground', children: t('wizardWelcomeDesc') }),
      jsx('p', { className: 'text-sm text-muted-foreground', children: t('wizardBackupPromise') }),
      jsx(Button, { variant: 'primary', size: 'sm', onClick: () => saveProgress('sources'), children: t('wizardGetStarted') })
    ] })
  } else if (step === 'sources') {
    body = jsxs('div', { className: 'flex max-w-2xl flex-col gap-3', children: [
      jsx('h2', { className: 'text-lg font-medium', children: t('wizardSourcesTitle') }),
      jsx('p', { className: 'text-sm text-muted-foreground', children: t('wizardSourcesDesc') }),
      stateQuery.isPending ? jsx(Skeleton, { className: 'h-24 w-full' }) : detectedTools.map(tool => jsxs('label', {
        'data-detected-tool': tool.id,
        className: 'flex items-start gap-2 rounded-md border border-(--ui-stroke-secondary) p-2',
        children: [
          jsx('input', { type: 'checkbox', checked: selectedTools.has(tool.id), onChange: () => toggleTool(tool.id), 'aria-label': t('wizardSelectTool', tool.label) }),
          jsxs('span', { children: [jsx('span', { className: 'block font-medium', children: tool.label }), jsx('span', { className: 'block break-words text-xs text-muted-foreground', children: (tool.scope === 'project' ? t('projectScope') : t('globalScope')) + ': ' + tool.dir })] })
        ]
      }, tool.id)),
      jsxs('div', { className: 'flex gap-2', children: [
        jsx(Input, { value: folderInput, onChange: event => setFolderInput(event.target.value), placeholder: t('wizardFolderPlaceholder'), className: 'flex-1' }),
        jsx(Button, { variant: 'secondary', size: 'sm', disabled: !folderInput.trim(), onClick: addFolder, children: t('wizardAddFolder') })
      ] }),
      scanRoots.map(root => jsxs('div', { 'data-scan-root': root, className: 'flex items-center gap-2 text-xs text-muted-foreground', children: [
        jsx('span', { className: 'min-w-0 flex-1 break-words', children: root }),
        jsx(Button, { variant: 'ghost', size: 'xs', onClick: () => setScanRoots(previous => previous.filter(item => item !== root)), children: t('remove') })
      ] }, root)),
      jsxs('div', { className: 'flex gap-2', children: [
        jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => saveProgress('welcome'), children: t('back') }),
        jsx(Button, { variant: 'primary', size: 'sm', disabled: !selectedTools.size && !scanRoots.length, onClick: runScan, children: t('wizardRunScan') })
      ] })
    ] })
  } else if (step === 'scanning') {
    body = jsxs('div', { 'data-wizard-scanning': 'true', className: 'flex flex-col gap-3', children: [
      jsx(Skeleton, { className: 'h-16 w-full' }),
      jsx('h2', { className: 'font-medium', children: t('wizardScanningTitle') }),
      jsx('p', { className: 'text-sm text-muted-foreground', children: t('wizardScanningDesc') })
    ] })
  } else if (step === 'review') {
    body = entries.length === 0 ? jsxs('div', { className: 'flex max-w-2xl flex-col gap-3', children: [
      jsx('h2', { className: 'text-lg font-medium', children: t('wizardScanEmptyTitle') }),
      jsx('p', { className: 'text-sm text-muted-foreground', children: t('wizardScanEmptyDesc') }),
      jsxs('div', { className: 'flex gap-2', children: [
        jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => saveProgress('sources'), children: t('wizardRescan') }),
        jsx(Button, { variant: 'primary', size: 'sm', onClick: finish, children: t('wizardViewTools') })
      ] })
    ] }) : jsxs('div', { className: 'flex flex-col gap-3', children: [
      jsx('h2', { className: 'text-lg font-medium', children: t('wizardReviewTitle') }),
      plan && Array.isArray(plan.duplicate_groups) && plan.duplicate_groups.length
        ? jsx('div', { 'data-duplicate-groups': 'true', className: 'rounded-md border border-(--ui-stroke-secondary) p-2 text-xs text-(--ui-text-warning)', children: t('wizardDuplicateGroups', plan.duplicate_groups.length) })
        : null,
      jsx('div', { className: 'grid grid-cols-1 gap-2', children: groupedKinds.map(([kind, key]) => {
        const rows = entries.filter(row => row.kind === kind)
        return jsxs('section', { 'data-classification': kind, className: 'rounded-md border border-(--ui-stroke-secondary) p-2', children: [
          jsx('div', { className: 'font-medium', children: `${t(key)} (${rows.length})` }),
          rows.slice(0, 4).map(row => jsx('div', { className: 'break-words text-xs text-muted-foreground', children: `${row.name} — ${row.path}` }, `${row.source}-${row.name}`))
        ] }, kind)
      }) }),
      jsxs('div', { className: 'flex gap-2', children: [
        jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => saveProgress('sources'), children: t('wizardRescan') }),
        jsx(Button, { variant: 'primary', size: 'sm', onClick: () => setStep('choose'), children: t('continue') })
      ] })
    ] })
  } else if (step === 'choose') {
    body = jsxs('div', { className: 'flex flex-col gap-3', children: [
      jsx('h2', { className: 'text-lg font-medium', children: t('wizardChooseTitle') }),
      jsx('div', { className: 'flex flex-wrap gap-2', children: detectedTools.map(tool => jsxs('label', { 'data-manage-tool': tool.id, className: 'flex items-center gap-1 text-xs', children: [
        jsx('input', { type: 'checkbox', checked: selectedTools.has(tool.id), onChange: () => toggleTool(tool.id), 'aria-label': t('wizardManageTool', tool.label) }), tool.label
      ] }, tool.id)) }),
      adoptable.length ? adoptable.map(row => jsxs('label', {
        'data-adopt-entry': row.name,
        className: 'flex items-start gap-2 rounded-md border border-(--ui-stroke-secondary) p-2',
        children: [
          jsx('input', { type: 'checkbox', checked: chosenKeys.has(entryKey(row)), disabled: row.tool != null && !selectedTools.has(row.tool), onChange: () => toggleEntry(row), 'aria-label': t('wizardSelectEntry', row.name) }),
          jsxs('span', { children: [jsx('span', { className: 'block font-medium', children: row.name }), jsx('span', { className: 'block break-words text-xs text-muted-foreground', children: row.path })] })
        ]
      }, entryKey(row))) : jsx(EmptyState, { title: t('wizardNothingToAdopt'), description: t('wizardNothingDesc') }),
      entries.some(row => row.conflict || row.kind === 'identical-duplicate' || row.kind === 'drifted')
        ? jsx('div', { 'data-duplicates-flagged': 'true', className: 'text-xs text-(--ui-text-warning)', children: t('wizardDuplicatesFlagged') })
        : null,
      jsxs('div', { className: 'flex gap-2', children: [
        jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => setStep('review'), children: t('back') }),
        jsx(Button, { variant: 'primary', size: 'sm', disabled: !chosen.length, onClick: () => setStep('preview'), children: t('wizardReviewPlan') })
      ] })
    ] })
  } else if (step === 'preview') {
    body = jsxs('div', { className: 'flex max-w-2xl flex-col gap-3', children: [
      jsx('h2', { className: 'text-lg font-medium', children: t('wizardPreviewTitle') }),
      jsx(ImportPlanPreview, { plan: plan, chosen: chosen }),
      jsx('p', { className: 'text-xs text-muted-foreground', children: t('wizardApplySafety') }),
      jsxs('div', { className: 'flex gap-2', children: [
        jsx(Button, { variant: 'secondary', size: 'sm', onClick: () => setStep('choose'), children: t('back') }),
        jsx(Button, { variant: 'primary', size: 'sm', disabled: !chosen.length, onClick: startApply, children: t('wizardApply') })
      ] })
    ] })
  } else if (step === 'apply') {
    body = jsxs('div', { 'data-wizard-applying': 'true', className: 'flex flex-col gap-3', children: [
      jsx(Skeleton, { className: 'h-16 w-full' }),
      jsx('h2', { className: 'font-medium', children: t('wizardApplyingTitle') }),
      jsx('p', { className: 'text-sm text-muted-foreground', children: t('wizardApplyingDesc') })
    ] })
  } else {
    const undo = receipt && receipt.receipt && Array.isArray(receipt.receipt.undo) ? receipt.receipt.undo : []
    body = jsxs('div', { className: 'flex max-w-2xl flex-col gap-3', children: [
      jsx('h2', { className: 'text-lg font-medium', children: t('wizardReceiptTitle') }),
      jsx(ImportReceipt, { response: receipt, undoResults: undoResults }),
      jsxs('div', { className: 'flex gap-2', children: [
        jsx(Button, { variant: 'secondary', size: 'sm', disabled: !undo.length, onClick: undoAdoption, children: t('undo') }),
        jsx(Button, { variant: 'primary', size: 'sm', onClick: finish, children: t('wizardViewTools') })
      ] })
    ] })
  }

  return jsxs('div', {
    'data-first-run-wizard': step,
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      jsxs('div', { className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3', children: [
        jsx(Button, { variant: 'secondary', size: 'xs', onClick: () => ccSectionAtom.set('tools'), children: t('backToTools') }),
        jsx('span', { className: 'font-medium', children: t('wizardTitle') }),
        jsx(Badge, { variant: 'outline', size: 'xs', children: t('wizardStep', stepIndex + 1, WIZARD_STEPS.length) })
      ] }),
      jsx(ScrollArea, { className: 'min-h-0 flex-1', children: jsx('div', { className: 'p-4', children: body }) }),
      jsx(ConfirmDialog, {
        open: !!confirm,
        onClose: () => setConfirm(null),
        onConfirm: confirm ? confirm.action : () => undefined,
        title: confirm ? confirm.title : '',
        description: confirm ? confirm.description : undefined,
        confirmLabel: confirm ? confirm.confirmLabel : undefined,
        destructive: false
      })
    ]
  })
}

function SectionPlaceholder({ title, hint }) {
  return jsx(EmptyState, { title: title, description: hint })
}

function PrimaryNav({ sections, active, onSelect, layout }) {
  if (layout === 'narrow') return jsxs('nav', { 'aria-label': 'Loadout sections', className: 'flex w-full min-w-0 items-center gap-2 border-b border-(--ui-stroke-secondary) p-2', children: [
    jsx('label', { htmlFor: 'loadout-section', className: 'text-xs text-muted-foreground', children: 'Section' }),
    jsx('select', { id: 'loadout-section', 'aria-label': 'Loadout section', value: sections.some(section => section.id === active) ? active : 'tools',
      onChange: event => onSelect(event.target.value), className: 'min-w-0 flex-1 rounded-md border border-(--ui-stroke-secondary) bg-background px-2 py-1 text-sm',
      children: sections.map(section => jsx('option', { value: section.id, children: section.label }, section.id)) })
  ] })
  return jsx('nav', { 'aria-label': 'Loadout sections', className: 'w-[140px] shrink-0 border-r border-(--ui-stroke-secondary)', children:
    jsx('div', { className: 'flex flex-col gap-1 p-2', children: sections.map(section => jsx('button', {
      type: 'button', 'aria-label': section.label, 'aria-current': active === section.id ? 'page' : undefined,
      onClick: () => onSelect(section.id), className: cn('rounded-md px-2 py-1.5 text-left text-xs transition-colors', active === section.id ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'), children: section.label
    }, section.id)) }) })
}

function ControlCenter() {
  const t = usePluginI18n(ID)
  const rootRef = useRef(null)
  const layout = usePaneLayout(rootRef)
  const reducedMotion = usePrefersReducedMotion()
  const active = useValue(ccSectionAtom)
  const sections = [
    { id: 'tools', label: t('ccTools') },
    { id: 'loadouts', label: 'Loadouts' },
    { id: 'problems', label: t('ccProblems') },
    { id: 'mcp', label: t('ccMcp') },
    { id: 'advanced', label: t('ccAdvanced') }
  ]
  const bodies = {
    tools: () => jsx(ToolsOverview, { layout: layout }),
    onboarding: () => jsx(FirstRunWizard, {}),
    loadouts: () => jsx(LoadoutManager, {}),
    problems: () => jsx(MaintenancePane, { section: 'problems' }),
    mcp: () => jsx(McpPane, {}),
    advanced: () => jsx(MaintenancePane, { section: 'advanced' })
  }
  const renderBody = bodies[active] || (() => jsx(SectionPlaceholder, { title: t('ccTools'), hint: t('toolsLandingHint') }))
  return jsxs('div', {
    ref: rootRef,
    'data-layout': layout,
    'data-hermes-loadout-root': 'true',
    'data-reduced-motion': reducedMotion ? 'true' : 'false',
    className: 'flex h-full min-w-0 flex-col text-sm',
    children: [
      jsx(ReducedMotionGuard, { active: reducedMotion }),
      jsx(BackgroundHost, {}),
      jsxs('div', {
        className: 'flex flex-col gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsx('span', { className: 'font-medium', children: t('ccTitle') }),
          jsx(LoadoutPicker, {})
        ]
      }),
      jsxs('div', {
        className: layout === 'narrow' ? 'flex min-h-0 flex-1 flex-col' : 'flex min-h-0 flex-1 flex-row',
        children: [
          jsx(PrimaryNav, { sections: sections, active: active, onSelect: next => ccSectionAtom.set(next), layout: layout }),
          jsx('main', { className: 'min-h-0 min-w-0 flex-1', children: renderBody() })
        ]
      }),
      jsx(OperationsPanel, {}),
      jsx('style', { children: '[data-hermes-loadout-root="true"] :is(button,input,select,summary):focus-visible{outline:2px solid currentColor;outline-offset:2px}' })
    ]
  })
}

// ---------------------------------------------------------------------------
// Plugin entry — register pane, page, and palette command
// ---------------------------------------------------------------------------

export default {
  id: ID, // must match the folder name
  name: 'Loadout for Hermes',
  defaultEnabled: false, // unified-package desktop halves ship opt-in
  register(ctx) {
    pluginCtx = ctx
    reviewGeneration += 1
    operationUIAtom.set({ busy: false, preview: null, response: null, error: null })
    selectedLoadoutAtom.set(storeGet('selectedLoadout', null))

    ctx.i18n.register({
      en: {
        paneTitle: 'Loadout',
        viewAll: 'All',
        viewIssues: 'Issues',
        viewOff: 'Off',
        searchPlaceholder: 'Search skills…',
        refresh: 'Refresh',
        retry: 'Retry',
        ccTitle: 'Loadout for Hermes',
        matrixToggle: 'Matrix',
        matrixTitle: 'Expert matrix',
        showDescriptions: 'Show descriptions',
        ccTools: 'Applications',
        ccProblems: 'Issues',
        ccMcp: 'MCP',
        ccAdvanced: 'Advanced',
        openControlCenter: 'Open Loadout',
        scan: 'Scan',
        problemsAction: n => `Issues (${n})`,
        enabledOn: n => `${n} on`,
        summaryLine: (skills, tools) => `${skills} skill${skills === 1 ? '' : 's'} · ${tools} tool${tools === 1 ? '' : 's'}`,
        problemLine: (broken, drift, foreign, bypasses) => `${broken} broken · ${drift} conflicts · ${foreign} protected · ${bypasses} bypasses`,
        toolsLandingHint: 'Manage daily skill availability by tool.',
        toolPathUnknown: 'Path not detected',
        manageTool: 'Manage',
        enabledOfTotal: (enabled, total) => `${enabled} on / ${total}`,
        enabledLabel: 'enabled',
        offLabel: 'off',
        problemLabel: 'problems',
        enableAll: 'Enable all',
        disableAll: 'Disable all',
        overviewProblems: n => `${n} issue${n === 1 ? '' : 's'}`,
        addToolAction: 'Add Tool',
        libraryTitle: 'Client library',
        libraryBack: 'Back to Applications',
        libraryIntro: 'Choose a client and review its path. Adding it saves a mapping, not skills. Daily Tools shows only detected or configured targets.',
        librarySearch: 'Search clients',
        libraryScope: 'Client scope',
        globalScope: 'Global',
        projectScope: 'Project',
        globalScopeDesc: 'Use the client location in your user account. Client-specific permissions and precedence still apply.',
        projectScopeDesc: 'Choose one existing project folder. Loadout checks only supported paths inside it, never searches your computer for projects.',
        projectFolder: 'Absolute project folder path',
        reviewProjectFolder: 'Review project folder',
        projectReviewChanged: 'Review the edited project folder before using a path.',
        libraryDetected: 'Detected',
        libraryAvailable: 'Available',
        libraryCustom: 'Custom',
        libraryConnect: 'Use this path',
        libraryConfigured: 'Configured',
        libraryConnectLabel: (label, scope) => `Use ${scope} path for ${label}`,
        librarySaved: label => `${label} is configured. Return to Applications to choose its skills. No skill files were changed.`,
        librarySaveFailed: 'Could not save this client. Nothing was confirmed.',
        libraryUnavailable: 'The client library is unavailable.',
        libraryVersion: 'The desktop and backend catalog versions differ. Fully quit and reopen Hermes after updating both plugin halves.',
        libraryRecovery: 'Repair the reported path or settings, then retry. A missing route requires a full Hermes restart, not just Reload desktop plugins.',
        libraryNoMatches: 'No verified clients match this search and scope. Change the search or scope, or use an explicitly reviewed Custom path.',
        libraryCustomDesc: 'Use a Custom path only after checking the client reads compatible skill folders. This is an explicit exception to Global and Project boundaries, not a compatibility claim.',
        libraryReviewCustom: 'This client is unverified. Review and save its path as Custom in Add Tool before changing skills.',
        customClientId: 'Custom client ID (lowercase letters, numbers, hyphens)',
        customClientLabel: 'Custom client display name',
        customClientPath: 'Custom skills directory (absolute path)',
        saveCustomClient: 'Save reviewed Custom path',
        sharedDirectoryWarning: 'Shared folder: changes here affect every client that reads this directory, not only this card.',

        scanAndImport: 'Scan & import',
        onboardingEntryTitle: 'Bring existing skills into Hermes',
        onboardingEntryDesc: 'Scan detected tools, review every copy, and choose what Hermes should manage.',
        startSetup: 'Start setup',
        wizardTitle: 'First-run scan',
        wizardStep: (step, total) => `Step ${step} of ${total}`,
        wizardWelcomeTitle: 'Make Hermes your canonical skills library',
        wizardWelcomeDesc: 'This guided scan finds skills in your existing tools. You decide what to adopt; scanning never changes files.',
        wizardBackupPromise: 'When you adopt from a detected tool, the original is moved to a timestamped backup and replaced with a managed link. Originals are never deleted.',
        wizardGetStarted: 'Get started',
        wizardSourcesTitle: 'Choose where to scan',
        wizardSourcesDesc: 'Detected folders are preselected. Global folders serve your user account; Project folders belong to a project you added in Tools. Add another read-only source only when needed.',
        wizardSelectTool: label => `Scan ${label}`,
        wizardManageTool: label => `Manage ${label}`,
        wizardFolderPlaceholder: '/path/to/another/skills folder',
        wizardAddFolder: 'Add folder',
        wizardRunScan: 'Run scan',
        wizardScanningTitle: 'Scanning selected folders…',
        wizardScanningDesc: 'This is read-only. Filesystem state will be classified from a fresh backend scan.',
        wizardScanFailed: 'Could not complete the import scan',
        wizardReviewTitle: 'Review what was found',
        wizardScanEmptyTitle: 'No skills found',
        wizardScanEmptyDesc: 'No skills were found in the selected folders. Nothing changed. Choose different folders or return to Tools.',
        wizardDuplicateGroups: n => `${n} duplicate-name group(s) need review and will not be adopted automatically.`,
        wizardManaged: 'Managed links',
        wizardUnique: 'Unique copies',
        wizardIdentical: 'Identical duplicates',
        wizardDrifted: 'Drifted copies',
        wizardConflicts: 'Name conflicts',
        wizardBroken: 'Broken links',
        wizardForeign: 'Foreign links',
        wizardUnmanaged: 'Unmanaged entries',
        wizardRescan: 'Change sources',
        wizardChooseTitle: 'Choose tools and skills to manage',
        wizardSelectEntry: name => `Adopt ${name}`,
        wizardNothingToAdopt: 'No unique copies to adopt',
        wizardNothingDesc: 'Protected, duplicate, drifted, and already-managed entries remain unchanged.',
        wizardDuplicatesFlagged: 'Duplicates, drifted copies, and conflicts are clearly flagged and excluded from this adoption.',
        wizardReviewPlan: 'Review dry run',
        wizardPreviewTitle: 'Dry-run plan',
        wizardPreviewCounts: (chosen, sources, conflicts, refused) => `${chosen} selected · ${sources} sources · ${conflicts} conflicts · ${refused} scan refusals`,
        wizardRefusals: 'Refused scan sources',
        wizardRefusalLine: (path, code) => `${path} [${code}]`,
        unknownPath: 'Unknown path',
        none: 'none',
        wizardApplySafety: 'Only the exact selected source, name, and tool entries shown here will be submitted. Changed or newly conflicting entries are refused by the backend.',
        wizardApply: 'Apply plan…',
        wizardConfirmTitle: 'Adopt these skills into Hermes?',
        wizardConfirmDescription: count => `Apply the reviewed import for ${count} selected skill${count === 1 ? '' : 's'}? No other application or skill state will change.`,
        wizardConfirmApply: 'Confirm adoption',
        wizardApplyingTitle: 'Applying reviewed plan…',
        wizardApplyingDesc: 'Each entry is rolled back independently if its copy or link swap fails.',
        wizardApplied: (adopted, failed, refused) => `Adopted ${adopted}; ${failed} failed; ${refused} refused`,
        wizardReceiptTitle: 'Adoption receipt',
        wizardReceiptCounts: (adopted, failed, refused) => `${adopted} adopted · ${failed} failed · ${refused} refused`,
        wizardResultLine: (name, result) => `${name} — ${result}`,
        wizardAddedToLibrary: 'Added to the library; not activated',
        wizardSourceKeptActive: 'Added to the library; source application kept active',
        wizardUndoTitle: 'Undo / restore path',
        wizardUndoDesc: 'Keep this receipt ID and these paths. Detected-tool adoptions can be undone here; added-folder copies include the canonical path for manual restore review.',
        wizardUndoLine: (kind, path, backup) => `${kind}: ${path} · backup: ${backup}`,
        wizardUndoResult: (restored, failed) => `${restored} restored · ${failed} need manual review`,
        wizardViewTools: 'View Tools',
        remove: 'Remove',
        back: 'Back',
        continue: 'Continue',
        scopeAll: 'all skills',
        scopeCategory: category => `${category} category`,
        scopeSelected: n => `${n} selected skill(s)`,
        backToTools: 'Back to Applications',
        bulkReceiptId: id => `Receipt ${id}`,
        viewEnabled: 'Enabled',
        selectAllVisible: 'Select all visible',
        visibleCount: n => `${n} visible`,
        selectSkill: skill => `Select ${skill}`,
        selectedCount: n => `${n} selected`,
        enableSelected: 'Enable selected',
        disableSelected: 'Disable selected',
        enableCategory: 'Enable category',
        disableCategory: 'Disable category',
        skillsCount: n => `${n} skill${n === 1 ? '' : 's'}`,
        protectedTitle: n => `${n} protected tool entr${n === 1 ? 'y' : 'ies'}`,
        protectedEmpty: 'No protected tool entries',
        protectedDesc: 'Foreign links and real skill directories are shown for review and are never changed automatically.',
        protectedForeignLine: (tool, target) => `${tool} · foreign link${target ? ` → ${target}` : ''}`,
        protectedUnmanagedLine: tool => `${tool} · real directory`,
        brokenEmpty: 'No broken links',
        brokenDesc: 'Broken skill links can be recreated from the Hermes source.',
        repair: 'Repair',
        repairAll: 'Repair all',
        bulkFailed: 'Bulk toggle failed',
        errorTitle: 'Skills backend unavailable',
        errorDesc: 'The plugin backend did not answer. Check that hermes-loadout is in `plugins.enabled` in config.yaml, then retry.',
        errorNeedsRestart: "The gateway mounts this plugin's backend only at startup — it looks like the gateway started before hermes-loadout was enabled. Run `hermes gateway restart` (or restart from Settings), then Retry.",
        noRootTitle: 'No skills root found',
        noRootDesc: 'The Hermes skills directory does not exist yet. Create skills and reload.',
        emptyTitle: 'No skills yet',
        noMatchTitle: 'No skills match',
        noMatchDesc: 'Try a different search, or switch the tool filter back to All.',
        undo: 'Undo',
        watchBrokenTitle: 'Skills: broken links detected',
        watchBrokenBody: n => `${n} broken link(s) — open the Skills pane to repair`,
        watchDriftTitle: 'Skills: drift detected',
        watchDriftBody: n => `${n} drifted skill(s) — open the Skills pane to resolve`,
        watchArrivalsTitle: 'Skills: new skills found',
        watchArrivalsBody: n => `${n} new skill(s) — open the Skills pane to enable`,
      }
    })

    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'Loadout',
        data: { placement: 'right', width: '320px' },
        render: () => jsx(CompactSummaryPane, {})
      },
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/hermes-loadout' },
        render: () => jsx(ControlCenter, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-loadout.open',
          label: 'Loadout: Open',
          keywords: ['loadout', 'hermes', 'skills', 'toggle', 'sync', 'claude', 'codex', 'opencode', 'grok', 'zcode'],
          detail: () => 'Enable or disable skills per tool',
          run: () => openControlCenter('tools')
        }
      },
      {
        id: 'mcp',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-loadout.mcp',
          label: 'Loadout: MCP connections',
          keywords: ['loadout', 'hermes', 'mcp', 'servers', 'claude desktop', 'toggle'],
          detail: () => 'Enable or disable MCP servers per app',
          run: () => openControlCenter('mcp')
        }
      },
      {
        id: 'report',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-loadout.report',
          label: 'Loadout: Health report',
          keywords: ['loadout', 'hermes', 'skills', 'health', 'broken', 'diff', 'repair'],
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
