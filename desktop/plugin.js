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
      onClick: () => host.navigate('/skills-toggle'),
      children: jsx(StatusDot, { tone: 'good' })
    })
  }
  return jsx(Badge, {
    variant: broken ? 'warn' : 'outline',
    size: 'xs',
    children: jsx('button', {
      type: 'button',
      className: 'inline-flex cursor-pointer items-center gap-1',
      onClick: () => host.navigate('/skills-toggle'),
      children: broken ? `${broken} broken` : `${unlinked} unlinked`
    })
  })
}

// Drift view (#11, D31) — same-name skills whose tool copy differs from the
// Hermes source. "Use Hermes" backs up the tool copy (never deletes) and
// swaps in the canonical symlink.
function DriftPanel({ drift, tools, onPush, busy }) {
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
              jsx(Button, {
                variant: 'secondary', size: 'xs', className: 'ml-auto', disabled: busy,
                onClick: () => onPush(item), children: t('useHermes')
              })
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
function SetupPanel({ tools, onClose, onEnsureDir, onAddTool, busy, autoLink, onAutoLink, adopt, onScanAdopt, onAdoptTool }) {
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
      jsxs('div', { className: 'mt-2 flex flex-col gap-1', children: linkTools.map(tool =>
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
  const [arrivals, setArrivals] = useState([])
  const [showSetup, setShowSetup] = useState(() => storeGet('setupDismissed', false) !== true)
  const [undo, setUndo] = useState(null)
  const [autoLink, setAutoLinkState] = useState(() => getAutoLinkPrefs())
  const [adopt, setAdopt] = useState(null)
  const [showPresetImport, setShowPresetImport] = useState(false)
  const [presetText, setPresetText] = useState('')
  const [taskBusy, setTaskBusy] = useState(false)

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

  // -- arrivals (D22) + auto-link (D23) --------------------------------------
  // Diff EVERY snapshot against the persisted seen-set (no once-guard): a
  // skill dropped while the pane is open must still prompt. Merging dedupes
  // until the arrival is linked or dismissed (which is what marks it seen).
  useEffect(() => {
    if (!state || !state.ok || !Array.isArray(state.skills)) return
    const ids = state.skills.map(s => s.id)
    const rawSeen = storeGet('seenSkills', null)
    if (rawSeen === null) {
      // first ever load: adopt the current snapshot silently — never prompt
      // about pre-existing skills (opt-in constraint)
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
    if (fresh.length) setArrivals(prev => Array.from(new Set([...prev, ...fresh])))
  }, [state])

  const autoLinkRef = useRef(false)
  useEffect(() => {
    if (!arrivals.length || !state || !state.ok) return
    const prefs = getAutoLinkPrefs()
    const autoTools = linkTools.filter(tool => prefs[tool.id] && tool.present !== false)
    if (!autoTools.length || autoLinkRef.current) return
    autoLinkRef.current = true
    const byId = new Map(skills.map(s => [s.id, s]))
    const valid = arrivals.filter(id => byId.has(id))
    ;(async () => {
      let changed = 0
      for (const tool of autoTools) {
        try {
          const res = await pluginCtx.rest('/toggle-bulk', {
            method: 'POST',
            body: { skills: valid, tool: tool.id, enabled: true }
          })
          if (res && res.ok) changed += res.changed || 0
        } catch (_err) {
          /* per-tool failure surfaces via the next invalidate + toast below */
        }
      }
      markSkillsSeen(arrivals)
      setArrivals([])
      autoLinkRef.current = false
      if (changed) host.notify({ kind: 'success', message: t('toastAutoLinked', changed) })
      qc.invalidateQueries({ queryKey: STATE_KEY })
      qc.invalidateQueries({ queryKey: DIFF_KEY })
    })()
  }, [arrivals, state, linkTools, skills, t, qc])

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
    for (const action of undo.actions) {
      const key = `${action.tool}|${action.enabled}`
      const entry = byKey.get(key) || { tool: action.tool, enabled: action.enabled, ids: [] }
      entry.ids.push(action.skill)
      byKey.set(key, entry)
    }
    setUndo(null)
    runBulkEntries(Array.from(byKey.values()))
  }, [undo, runBulkEntries])

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
    const ids = skills
      .filter(s => linkTools.some(tool => s.tools[tool.id] && s.tools[tool.id].state === 'enabled'))
      .map(s => s.id)
    const payload = {
      version: 1,
      name: 'my-skills',
      skills: ids,
      tools: linkTools.map(tool => tool.id)
    }
    const text = JSON.stringify(payload, null, 2)
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
            if (res && res.ok) host.notify({ kind: 'success', message: t('adoptDone', res.adopted || 0) })
            else host.notify({ kind: 'error', message: res && res.error ? res.error : t('adoptFailed') })
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

  const onPushDrift = item => {
    setConfirm({
      title: t('useHermesTitle', item.name),
      description: t('useHermesDesc', item.tool),
      confirmLabel: t('useHermes'),
      destructive: false,
      action: () => {
        setTaskBusy(true)
        pluginCtx
          .rest('/drift/push', { method: 'POST', body: { tool: item.tool, name: item.name } })
          .then(res => {
            if (res && res.ok) host.notify({ kind: 'success', message: t('pushDone', item.name) })
            else host.notify({ kind: 'error', message: res && res.error ? res.error : t('pushFailed') })
          })
          .catch(err => host.notifyError(err, t('pushFailed')))
          .finally(() => {
            setTaskBusy(false)
            qc.invalidateQueries({ queryKey: STATE_KEY })
            qc.invalidateQueries({ queryKey: DIFF_KEY })
            qc.invalidateQueries({ queryKey: DRIFT_KEY })
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
    setArrivals([])
    runBulkEntries(entries, undoActions)
  }

  const onArrivalDismiss = () => {
    markSkillsSeen(arrivals)
    setArrivals([])
  }

  const onToggleAutoLink = toolId => {
    const prefs = getAutoLinkPrefs()
    if (prefs[toolId]) delete prefs[toolId]
    else prefs[toolId] = true
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
    body = jsx(DriftPanel, { drift: drift, tools: tools, onPush: onPushDrift, busy: busy })
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
            adopt: adopt,
            onScanAdopt: onScanAdopt,
            onAdoptTool: onAdoptTool
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
      writer
        ? jsxs('div', { className: 'flex items-center gap-1.5 text-xs text-muted-foreground', children: [
            jsx(StatusDot, { tone: writer.present ? 'good' : 'muted' }),
            jsx('span', { className: 'truncate', children: t('mcpWriterLine', writer.label, writer.present ? '' : t('dirAbsentTip')) })
          ] })
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
            jsxs('div', { className: 'mt-1.5 grid grid-cols-2 gap-2 pl-3', children: [
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
              jsxs('span', { className: 'inline-flex items-center justify-between gap-1', children: [
                jsx('span', { className: 'text-[0.625rem] text-muted-foreground', children: 'Claude' }),
                jsxs('span', { className: 'inline-flex items-center gap-1', children: [
                  jsx(Switch, {
                    size: 'xs',
                    checked: claude === 'enabled',
                    disabled: busyName !== null,
                    'aria-label': row.name + ' — Claude Desktop',
                    onCheckedChange: next => {
                      if (next && !drifted) {
                        run(row.name, '/mcp/sync', { name: row.name }, 'mcpSynced')
                      } else if (next) {
                        setConfirm({
                          title: t('mcpSyncTitle', row.name),
                          description: t('mcpOverwriteDesc'),
                          confirmLabel: t('mcpSync'),
                          destructive: false,
                          action: () => run(row.name, '/mcp/sync', { name: row.name }, 'mcpSynced')
                        })
                      } else if (!drifted) {
                        run(row.name, '/mcp/remove', { name: row.name }, 'mcpRemoved')
                      } else {
                        setConfirm({
                          title: t('mcpRemoveTitle', row.name),
                          description: t('mcpRemoveForceDesc'),
                          confirmLabel: t('mcpRemoveForce'),
                          destructive: true,
                          action: () => run(row.name, '/mcp/remove', { name: row.name, force: true }, 'mcpRemoved')
                        })
                      }
                    }
                  }),
                  drifted
                    ? jsx(Button, {
                        variant: 'secondary', size: 'xs', className: 'h-4 px-1 text-[0.625rem]',
                        disabled: busyName !== null,
                        onClick: () => run(row.name, '/mcp/sync', { name: row.name }, 'mcpSynced'),
                        children: t('mcpSync')
                      })
                    : null
                ] })
              ] })
            ] })
          ]
        }, row.name)
      })
    })
  }

  return jsxs('div', {
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
// Pane root — Skills / MCP tabs (persisted per pane in ctx.storage)
// ---------------------------------------------------------------------------

function PaneRoot() {
  const t = usePluginI18n(ID)
  const [tab, setTab] = useState(() => storeGet('paneTab', 'skills'))
  useEffect(() => storeSet('paneTab', tab), [tab])
  return jsxs('div', {
    className: 'flex h-full min-w-0 flex-col',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-1 px-3 pt-2',
        children: ['skills', 'mcp'].map(name =>
          jsx(
            'button',
            {
              type: 'button',
              onClick: () => setTab(name),
              className: cn(
                'rounded-[4px] px-2 py-0.5 text-xs transition-colors',
                tab === name
                  ? 'bg-primary/10 font-medium text-primary'
                  : 'text-muted-foreground hover:bg-(--chrome-action-hover) hover:text-foreground'
              ),
              children: name === 'skills' ? t('skillsTab') : t('mcpTab')
            },
            name
          )
        )
      }),
      jsx('div', { className: 'min-h-0 flex-1', children: tab === 'mcp' ? jsx(McpPane, {}) : jsx(SkillsPane, {}) })
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
        autoLinkDesc: 'Auto-link: new skills are linked automatically (opt-in per tool)',
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
        rowAllTip: 'Link this skill into every tool',
        rowNoneTip: 'Unlink this skill from every tool',
        viewDrift: 'Drift',
        driftEmpty: 'No drift detected',
        driftDesc: 'Same-name skills whose tool copy differs from the Hermes source. "Use Hermes" backs up the tool copy and swaps in the canonical symlink — the original is never deleted.',
        useHermes: 'Use Hermes',
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
        render: () => jsx(PaneRoot, {})
      },
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/skills-toggle' },
        render: () => jsx(PaneRoot, {})
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
      },
      {
        id: 'mcp',
        area: PALETTE_AREA,
        data: {
          id: 'skills-toggle.mcp',
          label: 'MCP: toggle…',
          keywords: ['mcp', 'servers', 'claude desktop', 'toggle'],
          detail: () => 'Enable or disable MCP servers per app',
          run: () => {
            storeSet('paneTab', 'mcp')
            host.navigate('/skills-toggle')
          }
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
          run: () => host.navigate('/skills-toggle')
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
