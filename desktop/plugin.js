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
  PANES_AREA,
  ROUTES_AREA,
  PALETTE_AREA
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'skills-toggle'
const STATE_KEY = [ID, 'state']
const DIFF_KEY = [ID, 'diff']

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

function isProblemState(state) {
  return PROBLEM_STATES.indexOf(state) !== -1
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

function SkillRow({ skill, tools, view, activeTool, onToggle, onRepair, busy, layout }) {
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
          jsx('span', { className: 'ml-auto shrink-0 text-[0.625rem] text-muted-foreground', children: skill.category })
        ]
      }),
      layout !== 'narrow' && skill.description
        ? jsx('div', {
            className: 'mt-0.5 truncate pl-3 text-xs text-muted-foreground',
            title: skill.description,
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
// Main pane
// ---------------------------------------------------------------------------

function SkillsPane() {
  const t = usePluginI18n(ID)
  const qc = useQueryClient()
  const rootRef = useRef(null)
  const layout = usePaneLayout(rootRef)

  const [rawQuery, setRawQuery] = useState('')
  const searchQuery = useDebounced(rawQuery, 200)
  const [activeTool, setActiveTool] = useState(() => storeGet('toolFilter', 'all'))
  const [view, setView] = useState(() => storeGet('viewFilter', 'all'))
  const [confirm, setConfirm] = useState(null)

  useEffect(() => storeSet('toolFilter', activeTool), [activeTool])
  useEffect(() => storeSet('viewFilter', view), [view])

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

  const state = stateQuery.data
  const diff = diffQuery.data
  const tools = state && state.ok ? state.tools : []
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

  const busy =
    toggleMutation.isPending ||
    repairMutation.isPending ||
    repairAllMutation.isPending ||
    bulkMutation.isPending

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
          { id: 'off', label: t('viewOff') }
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
            busy: busy,
            layout: layout
          },
          group.category
        )
      )
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
        unmanagedDirTip: 'A real directory sits here (not a symlink) — never touched automatically'
      }
    })

    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'skills',
        data: { placement: 'right', width: '320px' },
        render: () => jsx(SkillsPane, {})
      },
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/skills-toggle' },
        render: () => jsx(SkillsPane, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'skills-toggle.open',
          label: 'Skills: toggle…',
          keywords: ['skills', 'toggle', 'sync', 'claude', 'codex', 'opencode', 'grok', 'zcode'],
          detail: () => 'Enable or disable skills per tool',
          run: () => host.navigate('/skills-toggle')
        }
      }
    ])
  }
}
