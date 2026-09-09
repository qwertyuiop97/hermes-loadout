// Real React interactions: discovery is separate from activation and file writes.
import { createElement } from 'react'
import TestRenderer, { act } from 'react-test-renderer'
import { readFileSync, writeFileSync } from 'fs'
import assert from 'node:assert/strict'

const path = process.env.STAGING_PLUGIN.replace(/plugin\.js$/, 'library-plugin.mjs')
writeFileSync(path, readFileSync(process.env.STAGING_PLUGIN, 'utf8') + '\nexport { ClientLibrary, ToolsOverview }\n')
const { default: plugin, ClientLibrary, ToolsOverview } = await import(path)
const channel = { state: { ok: true, capabilities: { reviewed_operations: 1 }, skills_root_exists: true, counts: { skills: 0 }, skills: [], tools: [
  { id: 'hermes', special: 'config', label: 'Hermes', present: true },
  { id: 'cursor', label: 'Cursor', optional: true, present: false, dir: '/fixture/home/.cursor/skills' }
] }, mode: 'ready', bundle: {}, notifications: [], invalidated: [], restCalls: [], diff: { ok: true, counts: {} } }
globalThis.__SKT = channel
let version = 1, failActivation = false, failCustom = false
const used = new Set()
const rest = async (path, options = {}) => {
  channel.restCalls.push({ path, body: options.body })
  if (path === '/clients/enable') {
    if (failActivation) return { ok: false, error: 'Target moved; review its path again' }
    used.add(options.body.client_id + options.body.scope)
    return { ok: true, tool: 'configured-fixture', dir: options.body.expected_dir, created: false }
  }
  if (path === '/config/tools') return failCustom ? { ok: false, error: 'Invalid Custom path' } : { ok: true }
  const project = path.includes('project_root=') ? decodeURIComponent(path.split('project_root=')[1]) : null
  const candidates = (id, folder, detected = false) => [{ scope: 'global', index: 0, dir: '/fixture/home/' + folder + '/skills', detected,
    configured_id: used.has(id + 'global') ? id : null }, ...(project ? [{ scope: 'project', index: 0, dir: project + '/' + folder + '/skills', project_root: project,
      configured_id: used.has(id + 'project') ? id + '-p-fixture' : null, detected: false, shared: id === 'agents' }] : [])]
  return { ok: true, catalog_version: version, clients: [
    { id: 'claude', label: 'Claude Code', skills: true, may_create: true, verification: 'documented', candidates: candidates('claude', '.claude', true) },
    { id: 'cursor', label: 'Cursor', skills: true, may_create: true, verification: 'documented', candidates: candidates('cursor', '.cursor') },
    { id: 'agents', label: 'Shared Agent Skills', skills: true, may_create: true, verification: 'documented', candidates: candidates('agents', '.agents') },
    { id: 'grok', label: 'Grok', skills: false, may_create: false, verification: 'unverified', notes: 'Not yet verified.', candidates: [] }
  ] }
}
plugin.register({ source: 'plugin:hermes-loadout', rest,
  storage: { get: (key, fallback) => key === 'onboarding' ? { version: 1, complete: true } : fallback, set: () => {} },
  i18n: { register: bundle => Object.assign(channel.bundle, bundle) }, registerMany: () => {}, os: {}, socket: () => () => {} })
const buttons = tree => tree.root.findAllByType('button')
const button = (tree, label) => buttons(tree).find(node => node.children.join('') === label)
const labeled = (tree, label) => tree.root.findAll(node => node.props['aria-label'] === label).find(node => typeof node.type === 'string')
const text = tree => JSON.stringify(tree.toJSON())
let tree
await act(async () => { tree = TestRenderer.create(createElement(ToolsOverview, { layout: 'narrow' })) })
assert.equal(tree.root.findAll(node => node.props['data-tool-card'] === 'cursor').length, 0)
await act(async () => { button(tree, 'Add Tool').props.onClick() })
assert.deepEqual(tree.root.findAll(node => node.props['data-client-group']).map(node => node.props['data-client-group']), ['detected', 'available', 'custom'])
assert.equal(button(tree, 'Back to Applications').props.disabled, false)
assert.equal(labeled(tree, 'Use global path for Claude Code').props.disabled, false)
assert.equal(tree.root.findAll(node => node.props['data-client-id'] === 'grok').length, 0)
await act(async () => { labeled(tree, 'Search clients').props.onChange({ target: { value: 'cursor' } }) })
assert.deepEqual(tree.root.findAll(node => node.props['data-client-id']).map(node => node.props['data-client-id']), ['cursor'])
await act(async () => { labeled(tree, 'Search clients').props.onChange({ target: { value: '' } }); button(tree, 'Project').props.onClick() })
assert.equal(tree.root.findAll(node => node.props['data-client-id']).length, 0, 'project paths need explicit selection')
assert.equal(channel.restCalls.some(call => call.path.includes('project_root=')), false)
await act(async () => { labeled(tree, 'Absolute project folder path').props.onChange('/fixture/project one'); button(tree, 'Review project folder').props.onClick() })
// Button handlers use the previous render; review after the input commits.
await act(async () => { button(tree, 'Review project folder').props.onClick() })
assert.equal(channel.restCalls.at(-1).path, '/clients?project_root=%2Ffixture%2Fproject%20one')
assert.match(text(tree), /Shared folder: changes here affect every client/)
assert.match(text(tree), /\/fixture\/project one\/\.cursor\/skills/)
await act(async () => { labeled(tree, 'Absolute project folder path').props.onChange('/fixture/different') })
assert.equal(labeled(tree, 'Use project path for Cursor').props.disabled, true, 'editing a root invalidates the earlier path review')
await act(async () => { labeled(tree, 'Absolute project folder path').props.onChange('/fixture/project one') })
await act(async () => { labeled(tree, 'Use project path for Cursor').props.onClick() })
const activation = channel.restCalls.find(call => call.path === '/clients/enable')
assert.deepEqual(activation.body, { client_id: 'cursor', scope: 'project', candidate: 0,
  project_root: '/fixture/project one', expected_dir: '/fixture/project one/.cursor/skills' })
assert.match(text(tree), /No skill files were changed/)
assert.equal(labeled(tree, 'Use project path for Cursor').props.disabled, true)
assert.equal(channel.restCalls.some(call => call.path.includes('/toggle') || call.path.includes('/bulk/')), false)
failActivation = true
await act(async () => { labeled(tree, 'Use project path for Claude Code').props.onClick() })
assert.match(text(tree), /Target moved; review its path again/)
assert.equal(labeled(tree, 'Use project path for Claude Code').props.disabled, false)
assert.ok(button(tree, 'Retry'))
await act(async () => {
  labeled(tree, 'Custom client ID (lowercase letters, numbers, hyphens)').props.onChange('custom')
  labeled(tree, 'Custom client display name').props.onChange('Custom fixture')
  labeled(tree, 'Custom skills directory (absolute path)').props.onChange('/fixture/custom')
})
failCustom = true
await act(async () => { button(tree, 'Save reviewed Custom path').props.onClick() })
assert.match(text(tree), /Invalid Custom path/)
assert.equal(labeled(tree, 'Custom skills directory (absolute path)').props.value, '/fixture/custom')
failCustom = false
await act(async () => { button(tree, 'Save reviewed Custom path').props.onClick() })
assert.equal(labeled(tree, 'Custom skills directory (absolute path)').props.value, '')
assert.ok(channel.invalidated.length)
version = 2
await act(async () => { button(tree, 'Global').props.onClick() })
assert.match(text(tree), /desktop and backend catalog versions differ/)
assert.ok(button(tree, 'Retry'))
await act(async () => { button(tree, 'Back to Applications').props.onClick() })
assert.equal(tree.root.findAll(node => node.props['data-client-library']).length, 0)
await act(async () => { tree.unmount() })
console.log('ok  searchable library keeps available clients out of everyday Tools')
console.log('ok  project paths need explicit review and activation saves only the reviewed mapping')
console.log('ok  shared-folder warnings, Custom failures, and backend-version recovery are visible')
console.log('CLIENT LIBRARY HARNESS: ALL CHECKS PASSED')
