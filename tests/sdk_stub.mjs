// Minimal @hermes/plugin-sdk stub for the render harness. State flows through
// globalThis.__SKT so the harness can drive loading/ready/error scenarios.
import { createElement, Fragment, useSyncExternalStore, useState } from 'react'

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

export const Button = ({ variant, size, ...props }) => el('button', { ...props, 'data-variant': variant, 'data-size': size })
export const Badge = (props) => el('span', { 'data-variant': props.variant, 'data-size': props.size }, props.children)
export const Switch = (props) => el('button', { role: 'switch', 'aria-label': props['aria-label'], 'aria-checked': props.checked ? 'true' : 'false', disabled: props.disabled || undefined, 'data-size': props.size, onClick: () => { if (!props.disabled) props.onCheckedChange?.(!props.checked) } }, null)
export const StatusDot = (props) => el('span', { 'data-tone': props.tone }, null)
export const EmptyState = (props) => el('div', {}, el('div', {}, props.title), props.description ? el('div', {}, props.description) : null, props.children ?? null)
export const ErrorState = (props) => el('div', {}, el('div', {}, props.title), typeof props.description === 'string' ? el('div', {}, props.description) : null, props.children ?? null)
export const Input = (props) => el('input', { ...props, onChange: e => props.onChange && props.onChange(e && e.target ? e.target.value : e) })
export const SearchField = (props) => el('input', { placeholder: props.placeholder, value: props.value, 'aria-label': props['aria-label'], onChange: props.onChange })
export const Skeleton = (props) => el('div', { 'data-skeleton': 'true', className: props.className }, null)
export const ScrollArea = (props) => el('div', {}, props.children)
export const Separator = () => el('hr', {}, null)
export const Tip = (props) => el('span', { 'data-tip': String(props.label) }, props.children)
export const ConfirmDialog = (props) => (props.open ? el('div', { role: 'dialog' }, el('div', {}, props.title), el('div', {}, props.description ?? ''), el('button', { onClick: props.onClose }, 'Cancel'), el('button', { onClick: props.onConfirm }, props.confirmLabel || 'Confirm')) : null)
export const SegmentedControl = (props) => el('div', { role: 'radiogroup' }, props.options.map(o => el('button', { role: 'radio', 'aria-checked': props.value === o.id, onClick: () => props.onChange(o.id), key: o.id }, o.label)))

export function useQuery({ queryKey }) {
  const field = queryKey[1] === 'mcp' ? 'mcpState' : queryKey[1]
  const channel = S()
  useSyncExternalStore(fn => {
    const listeners = channel.queryListeners ||= new Set()
    listeners.add(fn)
    return () => listeners.delete(fn)
  }, () => channel[field], () => channel[field])
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
  const [isPending, setPending] = useState(false)
  return {
    isPending,
    mutate: async vars => {
      setPending(true)
      let context
      let data
      let error
      try {
        context = opts.onMutate ? await opts.onMutate(vars) : undefined
        data = opts.mutationFn ? await opts.mutationFn(vars) : (S().mutationResult || { ok: true })
        // React Query treats every resolved promise as success. Application
        // responses such as {ok:false} must be handled by the product itself.
        if (opts.onSuccess) await opts.onSuccess(data, vars, context)
        return data
      } catch (caught) {
        error = caught
        if (opts.onError) await opts.onError(error, vars, context)
      } finally {
        if (opts.onSettled) await opts.onSettled(data, error, vars, context)
        setPending(false)
      }
    }
  }
}

export const useQueryClient = () => ({
  setQueryData: (key, updater) => {
    const field = key[1] === 'mcp' ? 'mcpState' : key[1]
    const channel = S()
    channel[field] = typeof updater === 'function' ? updater(channel[field]) : updater
    ;(channel.patchedKeys ||= []).push(key[1])
    for (const listener of channel.queryListeners || []) listener()
  },
  getQueryData: key => S()[key[1] === 'mcp' ? 'mcpState' : key[1]],
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
