// Exercise the actual React handlers against delayed successes and failures.
// Named exports are appended only to this disposable test copy, never shipped.
import { createElement } from 'react'
import TestRenderer, { act } from 'react-test-renderer'
import { readFileSync, writeFileSync } from 'fs'
import assert from 'node:assert/strict'

const testPath = process.env.STAGING_PLUGIN.replace(/plugin\.js$/, 'mutation-plugin.mjs')
writeFileSync(testPath, readFileSync(process.env.STAGING_PLUGIN, 'utf8') + '\nexport { SkillsPane, ToolsOverview, McpPane }\n')
const { default: plugin, SkillsPane, ToolsOverview, McpPane } = await import(testPath)
const state = {
  ok: true, skills_root_exists: true,
  tools: [{ id: 'hermes', label: 'Hermes', special: 'config', present: true },
    { id: 'codex', label: 'Codex', present: true, dir: '/tmp/fixture-codex' }],
  counts: { skills: 1, unlinked: 1 },
  skills: [{ id: 'software/safe-skill', category: 'software', name: 'safe-skill', description: 'Fixture',
    tools: { hermes: { state: 'enabled' }, codex: { state: 'missing' } } }]
}
const channel = {
  state, mode: 'ready', bundle: {}, notifications: [], invalidated: [], restCalls: [],
  diff: { ok: true, broken: [], foreign: [], unmanaged: [], unlinked: [], counts: {} },
  drift: { ok: true, drifted: [], count: 0 }
}
globalThis.__SKT = channel
let resolveToggle, rejectToggle
let undoFailure = false
const storage = new Map([['setupDismissed', true], ['onboarding', { version: 1, complete: true }]])
const receipt = { receipt_id: 'fixture-receipt', tool: 'codex', changed: 1, failed: 0, undo_available: true,
  items: [{ skill: 'software/safe-skill', ok: true, from: 'missing', to: 'enabled' }] }
plugin.register({
  source: 'plugin:hermes-switchboard',
  rest: async (path, options = {}) => {
    channel.restCalls.push({ path, body: options.body })
    if (path === '/toggle') return new Promise((resolve, reject) => { resolveToggle = resolve; rejectToggle = reject })
    if (path === '/toggle-bulk') return { ok: true, changed: 2, failed: 1, receipt }
    if (path === '/bulk/undo') return undoFailure ? { ok: false, error: 'Changed target; nothing modified' }
      : { ok: true, changed: 1, failed: 0, results: [], receipt: { ...receipt, undo_available: false } }
    if (path.startsWith('/bulk/receipt?')) return { ok: true, receipt }
    return { ok: true }
  },
  storage: { get: (key, fallback) => storage.has(key) ? storage.get(key) : fallback,
    set: (key, value) => storage.set(key, value) },
  i18n: { register: bundle => Object.assign(channel.bundle, bundle) },
  registerMany: () => {}, os: {}, socket: () => () => {}
})

const button = (tree, label) => tree.root.findAllByType('button').find(node => node.children.join('') === label)
const toggle = tree => tree.root.findAllByType('button').find(node => node.props['aria-label'] === 'safe-skill — Codex')
let tree
await act(async () => { tree = TestRenderer.create(createElement(ToolsOverview, { layout: 'narrow' })) })
await act(async () => { button({ root: tree.root.findByProps({ 'data-tool-card': 'codex' }) }, 'Manage').props.onClick() })
assert.equal(toggle(tree).props['aria-checked'], 'false')
await act(async () => { toggle(tree).props.onClick(); await Promise.resolve() })
assert.equal(toggle(tree).props['aria-checked'], 'false', 'Tools does not present an unconfirmed mutation as successful')
assert.equal(toggle(tree).props.disabled, true, 'pending mutation disables repeated toggles')
await act(async () => { resolveToggle({ ok: false, error: 'Protected fixture' }) })
assert.equal(toggle(tree).props['aria-checked'], 'false', 'resolved application failure leaves the visible switch unchanged')
assert.equal(channel.state, state, 'failed mutation preserves the exact previous query state')
assert(channel.notifications.some(n => n.kind === 'error' && n.message === 'Protected fixture'))
await act(async () => { toggle(tree).props.onClick(); await Promise.resolve() })
await act(async () => { rejectToggle(new Error('Fixture transport error')) })
assert.equal(toggle(tree).props['aria-checked'], 'false', 'network rejection also preserves the visible state')
assert(channel.invalidated.includes('state') && channel.invalidated.includes('diff'))
await act(async () => { tree.unmount() })
console.log('ok  actual Tools switches preserve state on application and transport failures')

await act(async () => { tree = TestRenderer.create(createElement(SkillsPane, { section: 'sets' })) })
await act(async () => { button(tree, 'Coding').props.onClick() })
await act(async () => { button(tree, 'Apply').props.onClick(); await Promise.resolve() })
assert(button(tree, 'Undo'), 'partial preset success offers receipt-backed undo')
await act(async () => { button(tree, 'Undo').props.onClick(); await Promise.resolve() })
const undo = channel.restCalls.filter(call => call.path === '/bulk/undo').at(-1)
assert.deepEqual(undo.body, { receipt_id: 'fixture-receipt' }, 'preset undo does not invert failed or no-op intentions')
await act(async () => { tree.unmount() })
console.log('ok  presets undo only the persisted successful changes')

storage.set('lastBulkReceipt', 'fixture-receipt')
await act(async () => { tree = TestRenderer.create(createElement(ToolsOverview, { layout: 'narrow' })) })
await act(async () => { button(tree, 'Last change').props.onClick(); await Promise.resolve() })
assert(button(tree, 'Undo'), 'a fresh mount retrieves durable undo from the backend')
assert(channel.restCalls.some(call => call.path === '/bulk/receipt?receipt_id=fixture-receipt'))
undoFailure = true
const notificationsBefore = channel.notifications.length
await act(async () => { button(tree, 'Undo').props.onClick(); await Promise.resolve() })
assert(button(tree, 'Undo'), 'failed undo keeps its retry action')
assert(channel.notifications.slice(notificationsBefore).every(n => n.kind === 'error'), 'failed undo never reports success')
await act(async () => { tree.unmount() })
console.log('ok  saved receipt reload and failed-undo recovery survive a fresh workspace')
channel.mcpState = { ok: true, partial_failure: true, counts: { catalog: 2, foreign: 0 },
  writers: { claude: { label: 'Claude Desktop', present: false, available: false }, codex: { label: 'Codex', present: true, available: true } },
  rows: [{ name: 'remote', enabled: true, writers: { claude: 'unsupported', codex: 'disabled' } },
    { name: 'local', enabled: true, writers: { claude: 'unavailable', codex: 'enabled' } }] }
await act(async () => { tree = TestRenderer.create(createElement(McpPane)) })
for (const label of ['remote — Claude', 'remote — Codex', 'local — Claude']) {
  assert.equal(tree.root.findAllByType('button').find(n => n.props['aria-label'] === label).props.disabled, true)
}
assert(!tree.root.findAllByType('button').find(n => n.props['aria-label'] === 'local — Codex').props.disabled)
assert(button(tree, 'Retry'), 'partial writer failure offers recovery without hiding healthy writers')
await act(async () => { tree.unmount() })
console.log('ok  MCP partial failures, native-disabled flags, and unsupported transports stay non-mutating')
console.log('MUTATION HARNESS: ALL CHECKS PASSED')
