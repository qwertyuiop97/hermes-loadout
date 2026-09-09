// Actual React navigation and handlers, with a deterministic backend contract fixture.
// Filesystem outcomes are checked independently in Python; this is not native Hermes.
import { renderToString } from 'react-dom/server'
import TestRenderer, { act } from 'react-test-renderer'
import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

const errors = [], warnings = []
const originalError = console.error, originalWarn = console.warn
console.error = (...args) => { errors.push(args.join(' ')); originalError(...args) }
console.warn = (...args) => { warnings.push(args.join(' ')); originalWarn(...args) }
const state = JSON.parse(readFileSync(new URL('./fixtures/screenshot-shaped-105-skills.json', import.meta.url), 'utf8'))
state.capabilities = { reviewed_operations: 1, named_loadouts: 1 }
const scanPlan = JSON.parse(readFileSync(new URL('./fixtures/first-run-scan-plan.json', import.meta.url), 'utf8'))
scanPlan.plan_id = 'reviewed-import-token'
const channel = { mode: 'ready', state, bundle: {}, notifications: [], registry: [], restCalls: [], navigations: [], workspaces: [], invalidated: [],
  operation: { ok: true, receipt: null, recovery_required: false }, loadouts: { ok: true, loadouts: [] },
  diff: { ok: true, unlinked: ['research/deep-research'], broken: [{ tool: 'claude', name: 'broken-skill', target: '/missing' }], foreign: [{ tool: 'opencode', name: 'foreign-skill', target: '/team/source' }], unmanaged: [{ tool: 'grok', name: 'real-skill-dir' }], counts: { broken: 1, foreign: 1, unmanaged: 1, unlinked: 1 }, catalog_bypasses: [] },
  drift: { ok: true, count: 1, drifted: [{ tool: 'codex', name: 'architecture-diagram', skill_id: 'creative/architecture-diagram' }] },
  mcpState: { ok: true, rows: [{ name: 'docs', enabled: true, writers: { claude: 'enabled', codex: 'drifted' } }, { name: 'weather', enabled: false, writers: { claude: 'missing', codex: 'disabled' } }, { name: 'local', enabled: true, writers: { claude: 'unavailable', codex: 'enabled' } }], writers: {}, foreign: [], counts: { catalog: 3 } }
}
globalThis.__SKT = channel
const plugin = (await import(process.env.STAGING_PLUGIN)).default
const storage = new Map()
let pendingPlan = null, holdScan = false, resolveScan, holdImport = false, resolveImport
const receipts = items => ({ receipt_id: 'abc123abc123', label: 'Fixture change', kind: 'selection', status: 'complete', changed: items.filter(row => row.status === 'completed').length, failed: items.filter(row => row.ok === false).length, skipped: 0, items, undo_available: items.some(row => row.status === 'completed') })
plugin.register({ source: 'plugin:hermes-loadout', os: {}, socket: () => () => {},
  storage: { get: (key, fallback) => storage.has(key) ? storage.get(key) : fallback, set: (key, value) => storage.set(key, value) },
  i18n: { register: bundle => Object.assign(channel.bundle, bundle) }, registerMany: entries => channel.registry.push(...entries),
  rest: async (path, options = {}) => {
    const body = options.body
    channel.restCalls.push({ path, body })
    if (path === '/state') return channel.state
    if (path === '/diff') return channel.diff
    if (path === '/drift') return channel.drift
    if (path === '/mcp/state') return channel.mcpState
    if (path === '/operations/latest') return channel.operation
    if (path === '/loadouts') return channel.loadouts
    if (path === '/operations/plan') {
      pendingPlan = body.states.map((row, index) => ({ ...row, status: index < 3 ? 'enable' : index === 3 ? 'unchanged' : 'protected', ...(index > 3 ? { error: 'Protected foreign entry' } : {}) }))
      return { ok: true, plan_id: 'immutable-operation-token', label: body.label, items: pendingPlan }
    }
    if (path === '/operations/apply') {
      assert.deepEqual(body, { plan_id: 'immutable-operation-token' }, 'apply is authorized only by the immutable server-side plan ID')
      const items = pendingPlan.map((row, index) => ({ ...row, status: index < 2 ? 'completed' : row.status === 'enable' ? 'conflict' : row.status, ok: index < 2 || row.status === 'unchanged' }))
      channel.operation = { ok: true, receipt: receipts(items) }
      return { ok: true, partial: true, receipt: channel.operation.receipt, changed: channel.operation.receipt.changed }
    }
    if (path === '/operations/undo-plan') return { ok: true, plan_id: 'immutable-undo-token', label: 'Undo fixture change', items: (channel.operation.receipt?.items || []).filter(row => row.status === 'completed').map(row => ({ ...row, status: 'restore' })) }
    if (path === '/operations/undo') {
      assert.deepEqual(body, { plan_id: 'immutable-undo-token' })
      const receipt = { ...channel.operation.receipt, status: 'undone', undo_available: false }
      channel.operation = { ok: true, receipt }
      return { ok: true, receipt, changed: receipt.changed }
    }
    if (path === '/import/plan') return holdScan ? new Promise(resolve => { resolveScan = resolve }) : scanPlan
    if (path === '/import/apply-plan') {
      assert.equal(body.plan_id, scanPlan.plan_id)
      const response = { ok: true, adopted: 1, failed: body.entries.length - 1, refused: 0,
        results: body.entries.map((entry, index) => ({ ...entry, ok: index === 0, code: index === 0 ? 'adopted' : 'copy-failed', skill: 'imported/' + entry.name, error: index ? 'Copy failed; original preserved' : undefined })),
        receipt: { receipt_id: 'import-receipt-001', adopted: 1, failed: body.entries.length - 1, refused: 0, undo: [{ path: '/fixture/source/solo-copy', backup: '/fixture/private/solo-copy', kind: 'restore-tool-entry' }] },
        operation: receipts([{ kind: 'import', app: 'codex', id: 'solo-copy', status: 'completed', ok: true }]) }
      channel.operation = { ok: true, receipt: response.operation }
      return holdImport ? new Promise(resolve => { resolveImport = () => resolve(response) }) : response
    }
    if (path === '/catalog-bypass/plan') return { ok: true, plan_id: 'bypass-token', label: 'Repair catalog bypass', items: [{ tool: body.tool, name: body.name, code: 'catalog-bypass', error: 'Broad library link' }] }
    if (path === '/catalog-bypass/repair') throw new Error('Cancellation must not call repair')
    if (path === '/conflict/plan') return { ok: true, plan_id: 'conflict-token', label: 'Resolve skill conflict', items: [{ app: body.tool, id: body.name, status: 'replace', reason: 'Preserve both originals' }] }
    throw new Error('Unexpected UI request: ' + path)
  }
})
const contributions = channel.registry
const open = contributions.find(row => row.data?.label === 'Loadout: Open')
const mcp = contributions.find(row => row.data?.label === 'Loadout: MCP connections')
const report = contributions.find(row => row.data?.label === 'Loadout: Health report')
assert(open && mcp && report, 'all commands use the searchable product name')
open.data.run()
const workspace = channel.workspaces.at(-1)
assert.equal(workspace.id, 'hermes-loadout.control-center')
assert.equal(workspace.minWidth, '320px')
assert.equal(channel.navigations.length, 0)
const text = tree => JSON.stringify(tree.toJSON())
const button = (tree, label) => tree.root.findAllByType('button').find(node => node.children.join('') === label)
const clicks = async (tree, label) => { const node = button(tree, label); assert(node, 'Button exists: ' + label); assert(!node.props.disabled, 'Button is available: ' + label); await act(async () => { node.props.onClick(); await Promise.resolve() }) }
const switchSection = async section => act(async () => { channel.atoms[0].set(section); await Promise.resolve() })
let tree
await act(async () => { tree = TestRenderer.create(workspace.render()) })
assert.deepEqual(tree.root.findByProps({ 'aria-label': 'Loadout section' }).findAllByType('option').map(node => node.children.join('')), ['Applications', 'Loadouts', 'Issues', 'MCP', 'Advanced'])
assert.equal(tree.root.findAll(node => node.props['data-tool-card']).length, 6)
assert(!text(tree).includes('Kimi'))
assert.equal(tree.root.findAll(node => node.props['data-more-filters']).length, 0)
assert(tree.root.findByProps({ 'data-loadout-picker': 'true' }))
assert(!text(tree).includes('Auto-link:'))
assert(tree.root.findByProps({ 'data-onboarding-entry': 'true' }))
await act(async () => { tree.unmount() })
console.log('ok  compact loadout selector, five scalable sections, and detected/configured application cards')

// Drive actual container widths, not a window-width guess.
let resize
const oldRO = globalThis.ResizeObserver, oldWindow = globalThis.window
globalThis.ResizeObserver = class { constructor(callback) { resize = callback } observe() { this.fire() } fire() { this.callback?.([]) } disconnect() {} }
globalThis.window = { matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }) }
await act(async () => { tree = TestRenderer.create(workspace.render(), { createNodeMock: node => node.props['data-hermes-loadout-root'] ? { getBoundingClientRect: () => ({ width: 1200 }) } : null }); await Promise.resolve() })
await act(async () => { resize([{ contentRect: { width: 1200 } }]); await Promise.resolve() })
assert.equal(tree.root.findByProps({ 'data-hermes-loadout-root': 'true' }).props['data-layout'], 'wide')
assert(text(tree).includes('animation:none'))
await clicks(tree, 'Matrix')
const matrix = tree.root.findByProps({ 'data-expert-matrix': 'true' })
assert(matrix.props || matrix)
const categories = tree.root.findAllByType('button').filter(node => typeof node.props['aria-expanded'] === 'boolean' && node.parent?.type === 'th')
assert(categories.length > 0)
assert(categories.every(node => node.props['aria-expanded'] === false), 'large matrix initially collapses categories')
assert(tree.root.findAllByProps({ role: 'switch' }).length < 100)
const matrixSearch = tree.root.findAllByType('input').find(node => node.props.placeholder === 'Search skills…')
await act(async () => { matrixSearch.props.onChange('diagram'); await new Promise(resolve => setTimeout(resolve, 220)) })
assert(text(tree).includes('hand-drawn-diagrams'))
await act(async () => { matrixSearch.props.onChange(''); await new Promise(resolve => setTimeout(resolve, 220)) })
for (const category of tree.root.findAllByType('button').filter(node => typeof node.props['aria-expanded'] === 'boolean' && node.parent?.type === 'th')) await act(async () => category.props.onClick())
assert.equal(tree.root.findAllByProps({ role: 'switch' }).length, 105 * 6)
assert(tree.root.findAllByProps({ role: 'switch' }).every(node => typeof node.props['aria-label'] === 'string' && node.props['aria-label'].includes(' — ')))
await act(async () => tree.root.findAllByType('button').find(node => node.props['aria-pressed'] === true).props.onClick())
const codex = tree.root.findByProps({ 'data-tool-card': 'codex' })
await act(async () => button({ root: codex }, 'Manage').props.onClick())
assert.equal(tree.root.findAll(node => node.props['data-single-tool-skill']).length, 105)
const search = tree.root.findAllByType('input').find(node => node.props.placeholder === 'Search skills…')
await act(async () => { search.props.onChange('diagram'); await new Promise(resolve => setTimeout(resolve, 220)) })
assert(tree.root.findAll(node => node.props['data-single-tool-skill']).length < 105)
await act(async () => { search.props.onChange(''); await new Promise(resolve => setTimeout(resolve, 220)) })
for (const mode of ['Enabled', 'Off', 'Issues', 'All']) await act(async () => tree.root.findAllByProps({ role: 'radio' }).find(node => node.children.join('') === mode).props.onClick())
await clicks(tree, 'Filters')
const categorySelect = tree.root.findByProps({ 'aria-label': 'Category' })
await act(async () => categorySelect.props.onChange({ target: { value: 'creative' } }))
assert(tree.root.findAll(node => node.props['data-single-tool-skill']).every(node => node.props['data-single-tool-skill'].startsWith('creative/')))
await clicks(tree, 'Clear filters')
await act(async () => tree.root.findByProps({ 'aria-label': 'Select all visible' }).props.onChange({ target: { checked: true } }))
assert(tree.root.findByProps({ 'data-selection-bar': 'sticky' }))
await clicks(tree, 'Enable selected')
assert(text(tree).includes('Protected foreign entry'))
const plan = channel.restCalls.filter(row => row.path === '/operations/plan').at(-1)
assert.deepEqual(plan.body.states.map(row => row.id), state.skills.map(row => row.id))
assert(plan.body.states.every(row => row.app === 'codex' && row.kind === 'skill' && row.enabled))
await clicks(tree, 'Apply reviewed changes')
assert(tree.root.findByProps({ 'data-operation-panel': 'true' }))
await clicks(tree, 'Show receipt')
assert(text(tree).includes('completed') && text(tree).includes('conflict'))
assert(channel.invalidated.includes('state') && channel.invalidated.includes('diff'))
await clicks(tree, 'Undo last change')
assert(channel.restCalls.some(row => row.path === '/operations/undo-plan'))
assert(!channel.restCalls.some(row => row.path === '/operations/undo'))
await clicks(tree, 'Apply reviewed changes')
assert(channel.restCalls.some(row => row.path === '/operations/undo'))
await act(async () => { resize([{ contentRect: { width: 320 } }]); await Promise.resolve() })
assert(tree.root.findByProps({ 'aria-label': 'Loadout section' }))
await act(async () => tree.unmount())
globalThis.ResizeObserver = oldRO; globalThis.window = oldWindow
console.log('ok  105-skill matrix, narrow fallback, reduced motion, filtering, exact reviewed bulk, and previewed undo')

await switchSection('onboarding')
await act(async () => { tree = TestRenderer.create(workspace.render()) })
assert(tree.root.findByProps({ 'data-first-run-wizard': 'welcome' }))
await clicks(tree, 'Get started')
assert.equal(tree.root.findAllByType('input').filter(node => node.props.type === 'checkbox' && node.props.checked).length, 5)
const folder = tree.root.findAllByType('input').find(node => node.props.placeholder === '/path/to/another/skills folder')
await act(async () => folder.props.onChange({ target: { value: '/fixture/team/skills' } }))
await clicks(tree, 'Add folder')
holdScan = true
await clicks(tree, 'Run scan')
assert(tree.root.findByProps({ 'data-wizard-scanning': 'true' }))
assert.deepEqual(channel.restCalls.filter(row => row.path === '/import/plan').at(-1).body.scan_roots, ['/fixture/team/skills'])
await act(async () => resolveScan(scanPlan)); holdScan = false
assert(tree.root.findByProps({ 'data-first-run-wizard': 'review' }))
for (const kind of ['managed','unmanaged-skill','identical-duplicate','drifted','name-conflict','broken-link','foreign-link','unmanaged-dir']) assert(tree.root.findByProps({ 'data-classification': kind }))
await clicks(tree, 'Continue')
assert.equal(tree.root.findAll(node => node.props['data-adopt-entry']).length, 2)
await clicks(tree, 'Review dry run')
await clicks(tree, 'Apply plan…')
assert(tree.root.findByProps({ role: 'dialog' }))
holdImport = true
await clicks(tree, 'Confirm adoption')
assert(tree.root.findByProps({ 'data-wizard-applying': 'true' }))
const sent = channel.restCalls.filter(row => row.path === '/import/apply-plan').at(-1)
assert.equal(sent.body.plan_id, scanPlan.plan_id)
assert.deepEqual(sent.body.entries, scanPlan.adoptable.map(row => ({ name: row.name, source: row.source, tool: row.tool })))
await act(async () => { resolveImport(); await Promise.resolve() }); holdImport = false
assert(text(tree).includes('1 adopted') && text(tree).includes('1 failed'))
await clicks(tree, 'Undo')
assert(tree.root.findByProps({ role: 'dialog' }))
await clicks(tree, 'Cancel')
await clicks(tree, 'View Tools')
assert.equal(storage.get('onboarding').complete, true)
assert(!('entries' in storage.get('onboarding')))
console.log('ok  onboarding source review, bounded import choices, immutable token, partial receipt, and cancellation')

await switchSection('loadouts')
assert(tree.root.findByProps({ 'data-loadout-manager': 'true' }))
assert(!text(tree).includes('Coding') && !text(tree).includes('Writing'), 'no guessed starter loadouts')
await switchSection('problems')
assert(text(tree).includes('Use library copy') && text(tree).includes('Use application copy'))
assert(text(tree).includes('foreign-skill') && text(tree).includes('real-skill-dir'))
assert(!text(tree).includes('data-unlinked-skills'), 'intentional Off is not a problem')
channel.diff = { ...channel.diff, catalog_bypasses: [{ tool: 'codex', name: 'shared', path: '/fixture/codex/shared', target: '/fixture/hermes/skills' }] }
await act(async () => { for (const fn of channel.queryListeners || []) fn() })
await clicks(tree, 'Review bypass repair')
assert(text(tree).includes('Broad library link'))
await clicks(tree, 'Cancel')
assert(!channel.restCalls.some(row => row.path === '/catalog-bypass/repair'))
await clicks(tree, 'Use application copy')
assert(text(tree).includes('Preserve both originals'))
await clicks(tree, 'Cancel')
await switchSection('mcp')
assert.equal(tree.root.findAllByProps({ role: 'switch' }).length, 9)
assert(tree.root.findAllByProps({ role: 'switch' }).find(row => row.props['aria-label'] === 'docs in Codex').props.disabled)
assert(!tree.root.findAllByProps({ role: 'switch' }).find(row => row.props['aria-label'] === 'weather in Codex').props.disabled)
await switchSection('advanced')
assert(text(tree).includes('Browse backups') && text(tree).includes('Manage application paths'))
assert(!text(tree).includes('Blueprint') && !text(tree).includes('Auto-link:'))
await switchSection('tools')
await act(async () => channel.atoms[1].set(['new/arriving-skill']))
assert(text(tree).includes('Discovery does not enable them.'))
await clicks(tree, 'Dismiss')
await act(async () => tree.unmount())
console.log('ok  bypass/conflict cancellation, protected entries, explicit MCP controls, and observation-only discovery')

const sdk = await import(new URL('./node_modules/@hermes/plugin-sdk/index.js', 'file://' + process.env.STAGING_PLUGIN).href)
const oldOpen = sdk.host.openWorkspace
sdk.host.openWorkspace = undefined
open.data.run()
assert(channel.navigations.includes('/hermes-loadout'))
const page = contributions.find(row => row.area === 'routes')
assert(renderToString(page.render()).includes('Loadout for Hermes'))
sdk.host.openWorkspace = oldOpen
assert.equal(errors.length, 0, errors.join('\n'))
assert.equal(warnings.length, 0, warnings.join('\n'))
console.error = originalError; console.warn = originalWarn
console.log('WORKSPACE HARNESS: ALL CHECKS PASSED')
