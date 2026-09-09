// Minimal @hermes/plugin-sdk stub for the render harness. State flows through
// globalThis.__SKT so the harness can drive loading/ready/error scenarios.
import { createElement, Fragment } from 'react'

const S = () => globalThis.__SKT || {}

const el = (type, props, ...kids) => {
  const { children, ...rest } = props || {}
  const list = Array.isArray(children) ? children : children !== undefined && children !== null ? [children] : []
  return createElement(type, rest, ...list, ...kids)
}

export const host = {
  notify: opts => { S().notifications.push(opts) },
  notifyError: (err, fallback) => { S().notifications.push({ kind: 'error', message: fallback || String(err) }) },
  navigate: p => { S().navigations.push(p) },
  openWorkspace: (id, options) => {
    const rec = {
      id,
      dock: options?.dock,
      title: options?.title,
      minWidth: options?.minWidth,
      uncloseable: options?.uncloseable,
      render: options?.render,
      onClose: options?.onClose
    }
    const workspaces = (S().workspaces = S().workspaces || [])
    workspaces.push(rec)
    S().activeWorkspace = rec
    return () => {
      S().workspaceCloses = (S().workspaceCloses || 0) + 1
      if (typeof rec.onClose === 'function') rec.onClose()
    }
  },
  state: {}
}

export const haptic = () => {}
export const cn = (...args) => args.filter(Boolean).join(' ')

export const Button = (props) => el('button', { onClick: props.onClick, disabled: props.disabled, 'data-variant': props.variant, 'data-size': props.size }, props.children)
export const Badge = (props) => el('span', { 'data-variant': props.variant, 'data-size': props.size }, props.children)
export const Switch = (props) => el('button', { role: 'switch', 'aria-label': props['aria-label'], 'aria-checked': props.checked ? 'true' : 'false', disabled: props.disabled || undefined, 'data-size': props.size }, null)
export const StatusDot = (props) => el('span', { 'data-tone': props.tone }, null)
export const EmptyState = (props) => el('div', {}, el('div', {}, props.title), props.description ? el('div', {}, props.description) : null, props.children ?? null)
export const ErrorState = (props) => el('div', {}, el('div', {}, props.title), typeof props.description === 'string' ? el('div', {}, props.description) : null, props.children ?? null)
export const Input = (props) => el('input', { value: props.value, placeholder: props.placeholder, className: props.className, onChange: e => props.onChange && props.onChange(e && e.target ? e.target.value : props.value) })
export const SearchField = (props) => el('input', { placeholder: props.placeholder, value: props.value, 'aria-label': props['aria-label'], onChange: () => {} })
export const Skeleton = (props) => el('div', { 'data-skeleton': 'true', className: props.className }, null)
export const ScrollArea = (props) => el('div', {}, props.children)
export const Separator = () => el('hr', {}, null)
export const Tip = (props) => el('span', { 'data-tip': String(props.label) }, props.children)
export const ConfirmDialog = (props) => (props.open ? el('div', { role: 'dialog' }, el('div', {}, props.title), el('div', {}, props.description ?? '')) : null)
export const SegmentedControl = (props) => el('div', { role: 'radiogroup' }, props.options.map(o => el('button', { role: 'radio', 'aria-checked': props.value === o.id, key: o.id }, o.label)))

export function useQuery({ queryKey }) {
  const mode = S().mode || 'ready'
  if (queryKey[1] === 'mcp') {
    if (mode === 'loading') return { data: undefined, isLoading: true, isPending: true, isError: false, error: null, refetch: () => {} }
    if (mode === 'error') return { data: undefined, isLoading: false, isPending: false, isError: true, error: new Error('x') }
    return { data: S().mcpState, isLoading: false, isPending: false, isError: false, error: null, refetch: () => {} }
  }
  if (queryKey[1] === 'drift') {
    if (mode === 'error') return { data: undefined, isLoading: false, isError: true, error: new Error('x') }
    return { data: S().drift || S().driftList || { ok: true, drifted: [], count: 0 }, isLoading: false, isPending: false, isError: false, error: null, refetch: () => {} }
  }
  if (queryKey[1] === 'state') {
    if (mode === 'loading') return { data: undefined, isLoading: true, isPending: true, isError: false, error: null, refetch: () => {} }
    if (mode === 'error') return { data: undefined, isLoading: false, isPending: false, isError: true, error: new Error(S().errorMessage || 'boom'), refetch: () => {} }
    return { data: S().state, isLoading: false, isPending: false, isError: false, error: null, refetch: () => {} }
  }
  if (mode === 'error') return { data: undefined, isLoading: false, isPending: false, isError: true, error: new Error('x') }
  return { data: S().diff, isLoading: false, isPending: false, isError: false, error: null, refetch: () => {} }
}

export function useMutation(opts) {
  return {
    isPending: false,
    mutate: vars => {
      const prev = S().state
      opts.onMutate && opts.onMutate(vars)
      const result = S().mutationResult || { ok: true }
      if (result.ok) opts.onSuccess && opts.onSuccess(result, vars, { previous: prev })
      else opts.onError && opts.onError(new Error(result.error || 'mutation failed'), vars, { previous: prev })
      opts.onSettled && opts.onSettled()
    }
  }
}

export const useQueryClient = () => ({
  setQueryData: (key, up) => { S().patchedKeys = S().patchedKeys || []; S().patchedKeys.push(key[1]) },
  getQueryData: () => S().state,
  cancelQueries: async () => {},
  invalidateQueries: ({ queryKey }) => { S().invalidated = S().invalidated || []; S().invalidated.push(queryKey[1]) }
})

export function usePluginI18n(id) {
  return (key, ...args) => {
    const entry = (S().bundle || { en: {} }).en[key]
    if (typeof entry === 'function') return entry(...args)
    if (typeof entry === 'string') return entry
    return key
  }
}

export const atom = initial => {
  const a = {
    _subs: new Set(),
    get: () => ((S().atomStore = S().atomStore || new Map()).get(a) ?? initial),
    set: v => {
      ;(S().atomStore = S().atomStore || new Map()).set(a, v)
      for (const fn of a._subs) fn(v)
    },
    subscribe: fn => {
      a._subs.add(fn)
      return () => a._subs.delete(fn)
    }
  }
  ;(S().atoms = S().atoms || []).push(a)
  return a
}
export const useValue = a => (typeof a === 'function' ? a() : a.get())
export const PANES_AREA = 'panes'
export const ROUTES_AREA = 'routes'
export const PALETTE_AREA = 'palette'
export const STATUSBAR_AREAS = { left: 'statusbar.left', right: 'statusbar.right' }
export { Fragment }
