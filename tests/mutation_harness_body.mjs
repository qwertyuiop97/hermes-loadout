// Actual React event paths, deferred responses, and persisted recovery after remount.
import { createElement, Fragment } from 'react'
import TestRenderer, { act } from 'react-test-renderer'
import { readFileSync, writeFileSync } from 'fs'
import assert from 'node:assert/strict'

const path = process.env.STAGING_PLUGIN.replace(/plugin\.js$/, 'mutation-plugin.mjs')
writeFileSync(path, readFileSync(process.env.STAGING_PLUGIN, 'utf8') + '\nexport { ToolsOverview, OperationsPanel, McpPane }\n')
const { default: plugin, ToolsOverview, OperationsPanel, McpPane } = await import(path)
const state = { ok: true, capabilities: { reviewed_operations: 1 }, skills_root_exists: true,
  tools: [{ id: 'hermes', label: 'Hermes', special: 'config', present: true },
    { id: 'codex', label: 'Codex', present: true, dir: '/fixture/codex' }],
  counts: { skills: 1, unlinked: 1 }, skills: [{ id: 'software/safe-skill', category: 'software', name: 'safe-skill', description: 'Fixture',
    tools: { hermes: { state: 'enabled' }, codex: { state: 'missing' } } }] }
const channel = { state, mode: 'ready', bundle: {}, notifications: [], invalidated: [], restCalls: [],
  diff: { ok: true, broken: [], foreign: [], unmanaged: [], unlinked: [], counts: {} }, drift: { ok: true, drifted: [], count: 0 } }
globalThis.__LOADOUT_TEST = channel
const storage = new Map([['onboarding', { version: 1, complete: true }]])
const desired = { kind: 'skill', app: 'codex', id: 'software/safe-skill', enabled: true }
const receipt = { operation_id: 'persisted-fixture', label: 'Enable one skill', changed: 1, skipped: 0, failed: 0, undo_available: true,
  items: [{ ...desired, status: 'completed' }] }
let resolvePlan, resolveApply, rejectApply, undoFailure = false
const calls = channel.restCalls
const context = { source: 'plugin:hermes-loadout',
  rest: async (url, options = {}) => {
    calls.push({ path: url, body: options.body })
    if (url === '/operations/plan') return new Promise(resolve => { resolvePlan = resolve })
    if (url === '/operations/apply') return new Promise((resolve, reject) => { resolveApply = resolve; rejectApply = reject })
    if (url === '/operations/latest') return channel.operation || { ok: true, receipt: null, recovery_required: false }
    if (url === '/operations/undo-plan') return { ok: true, plan_id: 'undo-reviewed', label: 'Undo last change', items: [{ ...desired, status: 'restore' }] }
    if (url === '/operations/undo') return undoFailure ? { ok: false, error: 'Target changed after review', failed: 1 } : { ok: true, changed: 1, receipt: { ...receipt, label: 'Undo', undo_available: false } }
    throw new Error('Unexpected mutation route ' + url)
  }, storage: { get: (key, fallback) => storage.has(key) ? storage.get(key) : fallback, set: (key, value) => storage.set(key, value) },
  i18n: { register: bundle => Object.assign(channel.bundle, bundle) }, registerMany: () => {}, os: {}, socket: () => () => {} }
plugin.register(context)
const buttons = tree => tree.root.findAllByType('button')
const button = (tree, label) => buttons(tree).find(node => node.children.join('') === label)
const toggle = tree => buttons(tree).find(node => node.props['aria-label'] === 'safe-skill — Codex')
const text = tree => JSON.stringify(tree.toJSON())
const render = () => createElement(Fragment, null, createElement(ToolsOverview, { layout: 'narrow' }), createElement(OperationsPanel))
let tree
await act(async () => { tree = TestRenderer.create(render()) })
await act(async () => { button({ root: tree.root.findByProps({ 'data-tool-card': 'codex' }) }, 'Manage').props.onClick() })
await act(async () => { toggle(tree).props.onClick() })
assert.equal(toggle(tree).props['aria-checked'], 'false')
assert.equal(toggle(tree).props.disabled, true)
await act(async () => { resolvePlan({ ok: true, plan_id: 'review-1', items: [{ ...desired, status: 'enable' }] }) })
assert(button(tree, 'Apply reviewed changes'))
const reviewDialog = tree.root.findByProps({ 'data-operation-review': 'true' })
assert(reviewDialog.props['aria-labelledby'])
assert.equal(typeof reviewDialog.props.onKeyDown, 'function')
assert(text(tree).includes('1 to apply'))
assert.equal(calls.filter(row => row.path === '/operations/apply').length, 0)
await act(async () => { button(tree, 'Cancel').props.onClick() })
assert.equal(calls.filter(row => row.path === '/operations/apply').length, 0)
assert.equal(toggle(tree).props['aria-checked'], 'false')

for (const transport of [false, true]) {
  await act(async () => { toggle(tree).props.onClick() })
  await act(async () => { resolvePlan({ ok: true, plan_id: 'review-failure', items: [{ ...desired, status: 'enable' }] }) })
  await act(async () => { button(tree, 'Apply reviewed changes').props.onClick() })
  assert.deepEqual(calls.at(-1), { path: '/operations/apply', body: { plan_id: 'review-failure' } })
  assert.equal(toggle(tree).props.disabled, true)
  assert.equal(toggle(tree).props['aria-checked'], 'false')
  await act(async () => { transport ? rejectApply(new Error('Disconnected')) : resolveApply({ ok: false, error: 'Protected fixture', failed: 1 }) })
  assert.equal(toggle(tree).props['aria-checked'], 'false')
  assert.equal(channel.state, state, 'Failures do not replace filesystem truth with speculative state')
  assert.match(text(tree), transport ? /response was interrupted/ : /Protected fixture/)
}
assert(channel.invalidated.includes('state') && channel.invalidated.includes('mcp'))
await act(async () => { tree.unmount() })
console.log('ok  delayed review, cancellation, HTTP-200 refusal, and lost apply response never pretend a switch succeeded')

channel.operation = { ok: true, receipt, recovery_required: false }
plugin.register(context)
await act(async () => { tree = TestRenderer.create(render()) })
assert.equal(button(tree, 'Undo last change').props.disabled, false, 'Fresh mount reads server-side last operation')
await act(async () => { button(tree, 'Undo last change').props.onClick() })
assert.equal(calls.filter(row => row.path === '/operations/undo').length, 0)
undoFailure = true
await act(async () => { button(tree, 'Apply reviewed changes').props.onClick() })
assert.deepEqual(calls.find(row => row.path === '/operations/undo').body, { plan_id: 'undo-reviewed' })
assert.match(text(tree), /Target changed after review/)
assert.equal(button(tree, 'Undo last change').props.disabled, false)
assert(channel.operation.receipt.undo_available, 'Failed undo retains its persisted recovery evidence')
await act(async () => { tree.unmount() })
channel.operation = { ok: true, receipt, recovery_required: true }
plugin.register(context)
await act(async () => { tree = TestRenderer.create(createElement(OperationsPanel)) })
assert.equal(button(tree, 'Undo last change').props.disabled, true)
assert.match(text(tree), /pending-operation.json/)
await act(async () => { tree.unmount() })
console.log('ok  previewed undo survives reopening; unsafe undo retains evidence and interrupted recovery blocks another undo')

channel.operation = { ok: true, receipt: null, recovery_required: false }
channel.mcpState = { ok: true, partial_failure: true, counts: { catalog: 2, foreign: 0 },
  writers: { claude: { label: 'Claude Desktop', present: false, available: false }, codex: { label: 'Codex', present: true, available: true } },
  rows: [{ name: 'remote', enabled: true, writers: { claude: 'unsupported', codex: 'disabled' } },
    { name: 'local', enabled: true, writers: { claude: 'unavailable', codex: 'enabled' } }] }
plugin.register(context)
await act(async () => { tree = TestRenderer.create(createElement(Fragment, null, createElement(McpPane), createElement(OperationsPanel))) })
for (const label of ['remote in Claude Desktop', 'local in Claude Desktop']) {
  assert.equal(buttons(tree).find(node => node.props['aria-label'] === label).props.disabled, true)
}
const nativeDisabled = buttons(tree).find(node => node.props['aria-label'] === 'remote in Codex')
assert(!nativeDisabled.props.disabled, 'Native disabled is an explicit enabling choice, not an unusable client')
await act(async () => { nativeDisabled.props.onClick() })
assert.deepEqual(calls.at(-1).body.states, [{ kind: 'mcp', app: 'codex', id: 'remote', enabled: true }])
await act(async () => { resolvePlan({ ok: false, error: 'Source unavailable' }) })
assert.match(text(tree), /Source unavailable/)
assert.equal(calls.some(row => /\/toggle|\/bulk\/|\/mcp\/(sync|remove)/.test(row.path)), false)
await act(async () => { tree.unmount() })
console.log('ok  usable MCP clients retain explicit activation while unavailable writers stay protected')
channel.mcpState = { ok: true, rows: [], writers: {}, counts: { catalog: 0 }, config: { label: 'Hermes config', path: '/fixture/hermes/config.yaml' }, catalog_error: { code: 'config-invalid', error: 'Could not parse Hermes MCP config', path: '/fixture/hermes/config.yaml' } }
plugin.register(context)
await act(async () => { tree = TestRenderer.create(createElement(McpPane)) })
assert.match(text(tree), /Could not parse Hermes MCP config/)
assert.match(text(tree), /\/fixture\/hermes\/config.yaml/)
assert(!text(tree).includes('No MCP servers configured'))
await act(async () => { tree.unmount() })
console.log('ok  MCP catalog/config errors are visible instead of successful-empty')
console.log('MUTATION HARNESS: ALL CHECKS PASSED')
