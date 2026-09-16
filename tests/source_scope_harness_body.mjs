// Real React regression for profile/backend isolation. Two sources intentionally
// reuse the same loadout id so identity mistakes cannot hide behind unique ids.
import { createElement } from 'react'
import TestRenderer, { act } from 'react-test-renderer'
import { readFileSync, writeFileSync } from 'fs'
import assert from 'node:assert/strict'

const path = process.env.STAGING_PLUGIN.replace(/plugin\.js$/, 'source-scope-plugin.mjs')
writeFileSync(path, readFileSync(process.env.STAGING_PLUGIN, 'utf8') + '\nexport { LoadoutManager }\n')
const { default: plugin, LoadoutManager } = await import(path)

const sharedId = '111111111111'
const skill = { id: 'writing/draft', name: 'draft', category: 'writing', description: 'Write', tools: { hermes: { state: 'disabled' } } }
const baseState = { ok: true, capabilities: { reviewed_operations: 1 }, skills_root_exists: true, counts: { skills: 1 }, tools: [{ id: 'hermes', label: 'Hermes', special: 'config', present: true }], skills: [skill] }
const sourceData = name => ({
  state: structuredClone(baseState),
  loadouts: { ok: true, loadouts: [{ id: sharedId, name, states: [{ kind: 'skill', app: 'hermes', id: skill.id, enabled: false }] }] },
  mcpState: { ok: true, rows: [], writers: {} }
})
const sourceKey = source => `${source.connectionId}\u0000${source.profile}`
const storageScopeKey = source => JSON.stringify([source.connectionId, source.profile])
const sourceA = { connectionId: 'source-a', profile: 'research' }
const sourceB = { connectionId: 'source-b', profile: 'research' }
const dataA = sourceData('Source A saved')
const dataB = sourceData('Source B saved')
const channel = {
  source: sourceA,
  sources: { [sourceKey(sourceA)]: dataA, [sourceKey(sourceB)]: dataB },
  ...dataA,
  mode: 'ready', bundle: {}, notifications: [], invalidated: [], restCalls: []
}
globalThis.__LOADOUT_TEST = channel
const storage = new Map([
  ['selectedLoadout', sharedId], // legacy implementation, present only to make the RED behavior observable
  ['selectedLoadoutsBySource', { [storageScopeKey(sourceA)]: sharedId, [storageScopeKey(sourceB)]: sharedId }]
])
let deferredSave = null
let nextId = 2
const currentData = () => channel.sources[sourceKey(channel.source)]
const switchSource = async source => {
  channel.source = source
  Object.assign(channel, currentData()) // keeps an unscoped implementation observable too
  await act(async () => {
    for (const notify of channel.sourceListeners || []) notify()
    for (const notify of channel.queryListeners || []) notify()
  })
}

plugin.register({
  source: 'plugin:hermes-loadout',
  rest: async (url, options = {}) => {
    const requestSource = { ...channel.source }
    const data = channel.sources[sourceKey(requestSource)]
    const body = options.body || {}
    channel.restCalls.push({ url, source: requestSource, body: structuredClone(body) })
    if (url === '/loadouts') return structuredClone(data.loadouts)
    if (url === '/state') return structuredClone(data.state)
    if (url === '/mcp/state') return structuredClone(data.mcpState)
    if (url === '/loadouts/save') {
      let row = data.loadouts.loadouts.find(item => item.id === body.loadout_id)
      if (!row) {
        row = { id: String(nextId++).repeat(12), name: '', states: [] }
        data.loadouts.loadouts.push(row)
      }
      Object.assign(row, { name: body.name, states: structuredClone(body.states) })
      if (deferredSave) await deferredSave.promise
      return { ok: true, loadout: structuredClone(row) }
    }
    throw new Error(`Unexpected request ${url}`)
  },
  storage: { get: (key, fallback) => storage.has(key) ? storage.get(key) : fallback, set: (key, value) => storage.set(key, value) },
  i18n: { register: bundle => Object.assign(channel.bundle, bundle) }, registerMany: () => {}, onDispose: () => {}, os: {}, socket: () => () => {}
})

const labeled = (tree, label) => tree.root.findAll(row => typeof row.type === 'string' && row.props['aria-label'] === label)[0]
const button = (tree, label) => tree.root.findAllByType('button').find(row => row.children.join('') === label)
const change = async (tree, label, value) => act(async () => labeled(tree, label).props.onChange({ target: { value } }))
const click = async (tree, label) => act(async () => button(tree, label).props.onClick())

let tree
await act(async () => { tree = TestRenderer.create(createElement(LoadoutManager)) })
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source A saved')
assert.match(JSON.stringify(tree.toJSON()), /research · source-a/, 'the editor identifies its active profile and backend')
await change(tree, 'Loadout name', 'Source A unsaved')

await switchSource(sourceB)
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source B saved', 'switching sources must not restore source A draft against the same record id')
assert.match(JSON.stringify(tree.toJSON()), /research · source-b/)
await change(tree, 'Loadout name', 'Source B unsaved')

await switchSource(sourceA)
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source A unsaved', 'each source restores only its own unsaved draft')

let release
deferredSave = { promise: new Promise(resolve => { release = resolve }) }
await change(tree, 'Loadout name', 'Source A acknowledged later')
let pending
await act(async () => { pending = button(tree, 'Save loadout').props.onClick(); await Promise.resolve() })
await switchSource(sourceB)
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source B unsaved')
release()
await act(async () => { await pending })
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source B unsaved', 'a late response from the old source must not replace the current editor')
assert.doesNotMatch(JSON.stringify(tree.toJSON()), /Source A acknowledged later/)

await switchSource(sourceA)
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source A acknowledged later')
assert.equal(dataB.loadouts.loadouts[0].name, 'Source B saved', 'saving source A must never mutate source B')

deferredSave = { promise: new Promise(resolve => { release = resolve }) }
await click(tree, 'New loadout')
await change(tree, 'Loadout name', 'Source A new acknowledged later')
await act(async () => { pending = button(tree, 'Save loadout').props.onClick(); await Promise.resolve() })
await switchSource(sourceB)
await switchSource(sourceA)
assert.equal(button(tree, 'Save loadout').props.disabled, true, 'a source round-trip cannot bypass the in-flight mutation guard')
assert.equal(channel.restCalls.filter(call => call.url === '/loadouts/save' && !call.body.loadout_id).length, 1)
release()
await act(async () => { await pending })
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source A new acknowledged later', 'an acknowledged create becomes the selected saved record even after a source switch')
assert.equal(dataA.loadouts.loadouts.filter(row => row.name === 'Source A new acknowledged later').length, 1)
deferredSave = null
await click(tree, 'Save loadout')
assert.equal(dataA.loadouts.loadouts.filter(row => row.name === 'Source A new acknowledged later').length, 1, 'retry updates the acknowledged id instead of duplicating it')

const scopedDrafts = storage.get('loadoutDraftsBySource')
assert(scopedDrafts && Object.keys(scopedDrafts).length === 2, 'draft persistence is namespaced by connection and profile')
assert.equal(storage.has('loadoutDrafts'), false, 'new writes never use the ambiguous legacy draft bucket')
await act(async () => { tree.unmount() })
console.log('ok  connection/profile switching isolates caches, selections, drafts, and late mutation responses')

// Pre-isolation drafts cannot be assigned to a backend safely. Keep them
// recoverable, but require an explicit choice before copying one into a source.
const buckets = storage.get('loadoutDraftsBySource')
storage.set('loadoutDraftsBySource', { ...buckets, [storageScopeKey(sourceA)]: {} })
storage.set('selectedLoadoutsBySource', { ...storage.get('selectedLoadoutsBySource'), [storageScopeKey(sourceA)]: sharedId })
storage.set('loadoutDrafts', { [sharedId]: {
  draft: { id: sharedId, name: 'Legacy unsaved work', states: [] },
  apps: [], editingApp: 'hermes', editingCapabilities: false, search: ''
} })
await switchSource(sourceB)
await switchSource(sourceA)
await act(async () => { tree = TestRenderer.create(createElement(LoadoutManager)) })
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source A acknowledged later', 'legacy work is not silently attached to the active backend')
assert.match(JSON.stringify(tree.toJSON()), /earlier version.*no backend identity/)
await click(tree, 'Restore legacy draft here')
assert.equal(labeled(tree, 'Loadout name').props.value, 'Legacy unsaved work')
assert.equal(storage.get('loadoutDraftsBySource')[storageScopeKey(sourceA)][sharedId].draft.name, 'Legacy unsaved work')
assert.equal(storage.get('loadoutDrafts')[sharedId].draft.name, 'Legacy unsaved work', 'the ambiguous source remains recoverable')
await act(async () => { tree.unmount() })
console.log('ok  legacy drafts require explicit source assignment and remain recoverable')

storage.set('loadoutDraftsBySource', { ...storage.get('loadoutDraftsBySource'), [storageScopeKey(sourceA)]: {} })
storage.set('loadoutDrafts', { [sharedId]: {
  draft: { id: sharedId, name: 'Malformed legacy work', states: [] },
  apps: [], editingApp: 'hermes', editingCapabilities: false, search: null
} })
await act(async () => { tree = TestRenderer.create(createElement(LoadoutManager)) })
assert.equal(labeled(tree, 'Loadout name').props.value, 'Source A acknowledged later')
await click(tree, 'Restore legacy draft here')
assert.equal(labeled(tree, 'Loadout name').props.value, 'Malformed legacy work', 'malformed editor preferences are sanitized without discarding business draft data')
await act(async () => { tree.unmount() })

const newDraft = name => ({ draft: { id: null, name, states: [] }, apps: [], editingApp: 'hermes', editingCapabilities: false, search: '' })
storage.set('selectedLoadoutsBySource', { ...storage.get('selectedLoadoutsBySource'), [storageScopeKey(sourceA)]: null })
storage.set('loadoutDraftsBySource', { ...storage.get('loadoutDraftsBySource'), [storageScopeKey(sourceA)]: { __new: newDraft('Scoped new draft') } })
await switchSource(sourceB)
await switchSource(sourceA)
await act(async () => { tree = TestRenderer.create(createElement(LoadoutManager)) })
assert.equal(labeled(tree, 'Loadout name').props.value, 'Scoped new draft', 'a source-scoped unsaved new loadout survives remounting')
await act(async () => { tree.unmount() })

storage.set('loadoutDraftsBySource', { ...storage.get('loadoutDraftsBySource'), [storageScopeKey(sourceA)]: {} })
storage.set('loadoutDrafts', { __new: newDraft('Legacy new draft') })
await act(async () => { tree = TestRenderer.create(createElement(LoadoutManager)) })
assert.equal(tree.root.findAll(node => node.props?.['aria-label'] === 'Loadout name').length, 0, 'an unscoped new draft is not restored implicitly')
await click(tree, 'Restore legacy draft here')
assert.equal(labeled(tree, 'Loadout name').props.value, 'Legacy new draft')
await act(async () => { tree.unmount() })
console.log('ok  source-scoped and legacy unsaved new loadouts remain recoverable')
