// Control Center workspace and route-fallback render harness.
import { renderToString } from 'react-dom/server'
import { readFileSync } from 'fs'

const STAGING = process.env.STAGING_PLUGIN
const failures = []
const ok = (condition, message) => {
  if (condition) console.log('ok  ' + message)
  else {
    failures.push(message)
    console.log('FAIL ' + message)
  }
}

const fixturePath = new URL('./fixtures/screenshot-shaped-105-skills.json', import.meta.url).pathname
const state = JSON.parse(readFileSync(fixturePath, 'utf8'))
const channel = {
  mode: 'ready',
  state,
  diff: { ok: true, counts: { broken: 1, foreign: 1, unmanaged: 1, unlinked: 1 } },
  drift: {
    ok: true,
    count: 1,
    drifted: [{ tool: 'codex', name: 'architecture-diagram', skill_id: 'creative/architecture-diagram' }]
  },
  mcpState: {
    ok: true,
    rows: [
      { name: 'chrome-devtools', enabled: true, writers: { claude: 'enabled', codex: 'enabled' } },
      { name: 'docs', enabled: true, writers: { claude: 'drifted', codex: 'missing' } },
      { name: 'weather', enabled: false, writers: { claude: 'missing', codex: 'missing' } }
    ],
    foreign: [{ name: 'foreign-server' }],
    counts: { catalog: 3, foreign: 1 },
    writers: { claude: { label: 'Claude Desktop', path: '/tmp/claude.json', present: true } }
  },
  bundle: {}, registry: [], notifications: [], navigations: [], invalidated: [], workspaces: [], restCalls: []
}
globalThis.__SKT = channel

const plugin = (await import(STAGING)).default
const storage = new Map()
plugin.register({
  source: 'plugin:skills-toggle',
  rest: async path => {
    channel.restCalls.push({ path })
    if (path === '/state') return channel.state
    if (path === '/diff') return channel.diff
    if (path === '/drift') return channel.drift
    if (path === '/mcp/state') return channel.mcpState
    return { ok: true }
  },
  socket: () => () => {},
  os: {},
  storage: {
    get: (key, fallback) => (storage.has(key) ? storage.get(key) : fallback),
    set: (key, value) => storage.set(key, value),
    remove: key => storage.delete(key)
  },
  i18n: { register: bundle => Object.assign(channel.bundle, bundle) },
  registerMany: contributions => contributions.forEach(c => channel.registry.push(c))
})

const open = channel.registry.find(c => c.id === 'open')
const mcp = channel.registry.find(c => c.id === 'mcp')
const report = channel.registry.find(c => c.id === 'report')
const page = channel.registry.find(c => c.id === 'page')
open.data.run()
ok(channel.workspaces.length === 1, 'palette opens one workspace')
const workspace = channel.activeWorkspace
ok(workspace.id === 'skills-toggle.control-center', 'workspace uses stable id')
ok(workspace.minWidth === '680px' && workspace.title === 'Skills Control Center', 'workspace options set title and minimum width')
ok(workspace.dock === undefined && typeof workspace.render === 'function', 'workspace keeps default dock and supplies render')
ok(channel.navigations.length === 0, 'workspace path does not navigate to fallback route')

let html = renderToString(workspace.render())
const navLabels = [...html.matchAll(/aria-label="(Tools|Sets|Problems|MCP|Advanced)"/g)].map(match => match[1])
ok(new Set(navLabels).size === 5, 'PrimaryNav exposes exactly five named sections')
ok(html.includes('aria-current="page"') && html.includes('Manage daily skill availability by tool.'), 'Tools is the deterministic default')
ok(!html.includes('aria-label="Matrix"') && !html.includes('>Matrix<'), 'Matrix is absent from item 7 nav')
ok(html.includes('Search skills') && html.includes('Coding') && html.includes('Set up your tools'), 'Tools mounts the existing management surface')
ok((html.match(/role="switch"/g) || []).length === 630, 'Tools retains the 105-skill catalog in the workspace')
ok(state.tools.every(tool => html.includes(tool.label)), 'Tools surface exposes Hermes and every configured tool')
ok(html.includes('whitespace-normal break-words') && !html.includes('title="A comprehensive workflow skill'), 'workspace descriptions wrap instead of clipping')

channel.atoms[0].set('sets')
html = renderToString(workspace.render())
ok(html.includes('Coding') && html.includes('Writing') && html.includes('Minimal'), 'Sets mounts existing presets')

report.data.run()
html = renderToString(channel.activeWorkspace.render())
ok(html.includes('architecture-diagram') && html.includes('Use Hermes'), 'Problems mounts the live drift panel')

mcp.data.run()
html = renderToString(channel.activeWorkspace.render())
ok(html.includes('MCP servers') && html.includes('chrome-devtools'), 'MCP mounts the live MCP pane')
ok((html.match(/role="switch"/g) || []).length === 9, 'MCP keeps nine live projection switches')

channel.atoms[0].set('advanced')
html = renderToString(workspace.render())
ok(html.includes('Set up your tools') && html.includes('Watch mode') && html.includes('Machine blueprint'), 'Advanced mounts setup, watch, blueprint, and backup controls')

const sdk = await import(new URL('./node_modules/@hermes/plugin-sdk/index.js', 'file://' + STAGING).href)
const realOpenWorkspace = sdk.host.openWorkspace
sdk.host.openWorkspace = undefined
channel.navigations.length = 0
open.data.run()
ok(channel.navigations.includes('/skills-toggle'), 'older hosts navigate to the route fallback')
html = renderToString(page.render())
ok(html.includes('Skills Control Center') && html.includes('aria-label="Tools"'), 'route fallback renders the same Control Center')
sdk.host.openWorkspace = realOpenWorkspace

ok((readFileSync(process.env.PLUGIN_SRC, 'utf8').match(/jsx\(BackgroundHost/g) || []).length === 2, 'both roots mount BackgroundHost')
ok((readFileSync(process.env.PLUGIN_SRC, 'utf8').match(/function useBackgroundSync/g) || []).length === 1, 'background effects have one shared hook')

console.log()
if (failures.length) {
  console.log(`WORKSPACE HARNESS FAILED — ${failures.length} problem(s)`)
  process.exit(1)
}
console.log('WORKSPACE HARNESS: ALL CHECKS PASSED')
