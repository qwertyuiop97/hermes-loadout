// Exercise the shipped loadout editor, not an imitation of its state machine.
import { createElement, Fragment } from 'react'
import TestRenderer, { act } from 'react-test-renderer'
import { readFileSync, writeFileSync } from 'fs'
import assert from 'node:assert/strict'
const path = process.env.STAGING_PLUGIN.replace(/plugin\.js$/, 'loadouts-plugin.mjs')
writeFileSync(path, readFileSync(process.env.STAGING_PLUGIN, 'utf8') + '\nexport { LoadoutManager, LoadoutPicker, SkillDetails }\n')
const { default: plugin, LoadoutManager, LoadoutPicker, SkillDetails } = await import(path)
const skill = { id: 'writing/draft', name: 'draft', category: 'writing', description: 'Write a draft', tools: { hermes: { state: 'disabled' }, codex: { state: 'missing' } } }
const state = { ok: true, capabilities: { reviewed_operations: 1 }, skills_root_exists: true, counts: { skills: 1 }, tools: [
  { id: 'hermes', label: 'Hermes', special: 'config', present: true }, { id: 'codex', label: 'Codex', present: true }], skills: [skill] }
const channel = { state, mode: 'ready', bundle: {}, notifications: [], invalidated: [], restCalls: [],
  loadouts: { ok: true, loadouts: [{ id: '111111111111', name: 'Existing', states: [{ kind: 'skill', app: 'hermes', id: skill.id, enabled: false }] }] },
  metadata: { ok: true, classifications: ['Portable', 'Hermes-specific', 'Codex-specific', 'Claude-specific', 'Other application-specific', 'Unclassified'], skills: {} },
  mcpState: { ok: true, rows: [{ name: 'reference', enabled: false, writers: { codex: 'missing', claude: 'missing' } }], writers: {} } }
globalThis.__LOADOUT_TEST = channel
const storage = new Map([['selectedLoadout', '111111111111']])
let counter = 2, failSave = false
const copy = value => JSON.parse(JSON.stringify(value))
const records = copy(channel.loadouts.loadouts)
plugin.register({ source: 'plugin:hermes-loadout', rest: async (url, options = {}) => {
  const body = options.body
  channel.restCalls.push({ path: url, body: copy(body || {}) })
  if (url === '/loadouts') return { ok: true, loadouts: copy(records) }
  if (url === '/loadouts/capture') return { ok: true, states: body.apps.map(app => ({ kind: 'skill', app, id: skill.id, enabled: false })), excluded: [] }
  if (url === '/loadouts/save') {
    if (failSave) return { ok: false, error: 'Disk unavailable; draft not saved' }
    let row = records.find(row => row.id === body.loadout_id)
    if (!row) { row = { id: String(counter++).repeat(12) }; records.push(row) }
    Object.assign(row, { name: body.name, states: copy(body.states) })
    return { ok: true, loadout: copy(row) }
  }
  if (url === '/loadouts/edit') {
    const original = records.find(row => row.id === body.loadout_id)
    if (body.action === 'duplicate') { const row = { ...copy(original), id: String(counter++).repeat(12), name: original.name + ' copy' }; records.push(row); return { ok: true, loadout: copy(row) } }
    if (body.action === 'delete') { records.splice(records.indexOf(original), 1); return { ok: true, loadout: copy(original) } }
  }
  if (url === '/inventory/classify') { channel.metadata = { ...channel.metadata, skills: { [body.skill]: { classification: body.classification } } }; return { ok: true } }
  if (url === '/inventory/metadata') return channel.metadata
  throw new Error('Unexpected write ' + url)
}, storage: { get: (key, fallback) => storage.has(key) ? storage.get(key) : fallback, set: (key, value) => storage.set(key, value) },
  i18n: { register: bundle => Object.assign(channel.bundle, bundle) }, registerMany: () => {}, os: {}, socket: () => () => {} })
const button = (tree, label) => tree.root.findAllByType('button').find(row => row.children.join('') === label)
const labeled = (tree, label) => tree.root.findAll(row => typeof row.type === 'string' && row.props['aria-label'] === label)[0]
const change = async (tree, label, value) => { await act(async () => { const row = labeled(tree, label); row.props.onChange({ target: { value } }) }) }
const click = async (tree, label) => { await act(async () => { button(tree, label).props.onClick() }) }
let tree
await act(async () => { tree = TestRenderer.create(createElement(Fragment, null, createElement(LoadoutPicker), createElement(LoadoutManager))) })
assert.equal(labeled(tree, 'Loadout name').props.value, 'Existing')
const pickerIds = tree.root.findAllByType('select').filter(row => row.props['aria-label'] === 'Saved loadout').map(row => row.props.id)
assert.equal(new Set(pickerIds).size, 2, 'Two visible pickers have distinct label targets')
await click(tree, 'New loadout')
assert(labeled(tree, 'Loadout name'), 'New from an existing selected record keeps the new draft open')
await change(tree, 'Loadout name', 'Research')
await act(async () => { labeled(tree, 'Include Codex').props.onChange({ target: { checked: true } }) })
await click(tree, 'Edit capabilities')
await change(tree, 'Desired skill draft in codex', 'on')
await change(tree, 'Desired mcp reference in codex', 'off')
const before = JSON.stringify(state)
await click(tree, 'Save loadout')
assert.deepEqual(records[1].states, [{ kind: 'skill', app: 'codex', id: skill.id, enabled: true }, { kind: 'mcp', app: 'codex', id: 'reference', enabled: false }])
assert.equal(JSON.stringify(state), before)
await change(tree, 'Loadout name', 'Research revised')
// Query refreshes must not erase unsaved user typing.
channel.loadouts = { ok: true, loadouts: copy(records) }
await act(async () => { for (const notify of channel.queryListeners || []) notify() })
assert.equal(labeled(tree, 'Loadout name').props.value, 'Research revised')
failSave = true
await click(tree, 'Save loadout')
assert.match(JSON.stringify(tree.toJSON()), /Disk unavailable/)
assert.equal(labeled(tree, 'Loadout name').props.value, 'Research revised')
failSave = false
await click(tree, 'Save loadout')
assert.equal(records[1].name, 'Research revised')
await click(tree, 'Duplicate')
assert.equal(records.length, 3)
assert.notEqual(records[1].id, records[2].id)
await change(tree, 'Loadout name', 'Independent copy')
await click(tree, 'Save loadout')
assert.equal(records[1].name, 'Research revised')
await click(tree, 'Capture current selections')
assert.deepEqual(channel.restCalls.at(-1).body.apps, ['codex'])
assert.match(JSON.stringify(tree.toJSON()), /Nothing was activated/)
await click(tree, 'Save loadout')
assert.equal(records[2].states[0].enabled, false)
await click(tree, 'Delete loadout')
await click(tree, 'Cancel')
assert.equal(records.length, 3)
await click(tree, 'Delete loadout')
await act(async () => { const dialog = tree.root.findByProps({ role: 'dialog' }); button({ root: dialog }, 'Delete loadout').props.onClick() })
assert.equal(records.length, 2)
assert.equal(JSON.stringify(state), before)
assert(channel.restCalls.every(row => /^\/loadouts(\/|$)/.test(row.path)))
await act(async () => { tree.unmount() })
console.log('ok  create/edit/rename/duplicate/delete, capture, failed save, and background refresh preserve independent user choices without activation')
await act(async () => { tree = TestRenderer.create(createElement(SkillDetails, { skill, onClose: () => {} })) })
await change(tree, 'Designed for draft', 'Hermes-specific')
assert.equal(channel.metadata.skills[skill.id].classification, 'Hermes-specific')
assert.equal(JSON.stringify(state), before)
assert.match(JSON.stringify(tree.toJSON()), /not a compatibility guarantee/)
await act(async () => { tree.unmount() })
console.log('ok  informational classification updates only metadata, not activation or contents')
console.log('LOADOUT EDITOR HARNESS: ALL CHECKS PASSED')
