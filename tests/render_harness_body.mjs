// Compact-pane render harness. Workspace behavior is tested separately.
import { renderToString } from 'react-dom/server'

const originalConsoleError = console.error
const originalConsoleWarn = console.warn
const consoleErrors = []
const consoleWarnings = []
const formatConsole = args => args.map(value => value instanceof Error ? value.stack || value.message : String(value)).join(' ')
console.error = (...args) => {
  consoleErrors.push(formatConsole(args))
  originalConsoleError(...args)
}
console.warn = (...args) => {
  consoleWarnings.push(formatConsole(args))
  originalConsoleWarn(...args)
}

const PLUGIN_SRC = process.env.PLUGIN_SRC
const STAGING = process.env.STAGING_PLUGIN
const failures = []
const ok = (condition, message) => {
  if (condition) console.log('ok  ' + message)
  else {
    failures.push(message)
    console.log('FAIL ' + message)
  }
}

function makeState() {
  const blank = () => ({
    hermes: { state: 'enabled' }, claude: { state: 'missing' }, codex: { state: 'missing' },
    opencode: { state: 'missing' }, grok: { state: 'missing' }, zcode: { state: 'missing' }
  })
  const apple = blank()
  apple.codex = { state: 'enabled' }
  apple.grok = { state: 'foreign-link' }
  const unicode = blank()
  unicode.grok = { state: 'broken-link' }
  const airtable = blank()
  airtable.hermes = { state: 'disabled' }
  airtable.grok = { state: 'unmanaged-dir' }
  return {
    ok: true,
    skills_root_exists: true,
    tools: [
      { id: 'hermes', label: 'Hermes', present: true, special: 'config' },
      { id: 'claude', label: 'Claude', present: true },
      { id: 'codex', label: 'Codex', present: true },
      { id: 'opencode', label: 'OpenCode', present: false },
      { id: 'grok', label: 'Grok', present: true },
      { id: 'zcode', label: 'ZCode', present: false }
    ],
    counts: { skills: 3, unlinked: 1 },
    skills: [
      { id: 'apple/apple-notes', name: 'apple-notes', category: 'apple', tools: apple },
      { id: 'apple/rem', name: 'rem', category: 'apple', tools: unicode },
      { id: 'productivity/airtable', name: 'airtable', category: 'productivity', tools: airtable }
    ]
  }
}

const channel = {
  mode: 'ready',
  state: makeState(),
  diff: { ok: true, counts: { broken: 1, foreign: 1, unmanaged: 1, unlinked: 1 } },
  drift: { ok: true, count: 1, drifted: [] },
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

ok(plugin.id === 'skills-toggle', 'plugin id matches')
ok(plugin.defaultEnabled === false, 'desktop half remains opt-in')
ok(channel.registry.length === 6, 'six contributions remain registered')
const pane = channel.registry.find(c => c.area === 'panes')
const page = channel.registry.find(c => c.area === 'routes')
const chip = channel.registry.find(c => c.area === 'statusbar.right')
ok(pane?.data?.placement === 'right' && pane?.data?.width === '320px', 'pane stays right at 320px')
ok(page?.data?.path === '/skills-toggle', 'route fallback stays /skills-toggle')
ok(renderToString(chip.render()).includes('1 broken'), 'status chip retains health summary')

const source = (await import('fs')).readFileSync(PLUGIN_SRC, 'utf8')
const usedKeys = [...source.matchAll(/\bt\(\s*'([A-Za-z0-9_]+)'\s*[,)]/g)].map(match => match[1])
const missingKeys = [...new Set(usedKeys.filter(key => !(key in channel.bundle.en)))]
ok(missingKeys.length === 0, `all static i18n keys exist (${missingKeys.join(', ') || 'none missing'})`)

const render = () => renderToString(pane.render())
let html = render()
ok(html.includes('>Skills<'), 'compact pane title renders')
ok(html.includes('3 skills · 5 tools'), 'compact stat line renders')
ok((html.match(/data-summary-tool=/g) || []).length === 6, 'five link tools plus Hermes render')
ok(html.includes('2 on') && html.includes('1 on') && html.includes('0 on'), 'enabled counts render as labels')
ok((html.match(/role="switch"/g) || []).length === 0, 'compact pane renders zero switches')
ok(html.includes('Open Control Center') && html.includes('Scan') && html.includes('Problems (5)'), 'three quick actions render for problems')
ok(!html.includes('Search skills') && !html.includes('Set up your tools'), 'catalog controls are absent from the pane')

const fs = await import('fs')
for (const [name, expectedTools, expectedRows] of [['tools-zero', 0, 1], ['tools-one', 1, 2], ['tools-many', 4, 5]]) {
  channel.state = JSON.parse(fs.readFileSync(new URL(`./fixtures/${name}.json`, import.meta.url), 'utf8'))
  channel.diff = { ok: true, counts: {} }
  channel.drift = { ok: true, count: 0, drifted: [] }
  html = render()
  ok(html.includes(`1 skills · ${expectedTools} tools`), `${name} reports the correct detected-tool count`)
  ok((html.match(/data-summary-tool=/g) || []).length === expectedRows, `${name} renders every eligible row plus Hermes`)
}
const mixed = JSON.parse(fs.readFileSync(new URL('./fixtures/mixed-problems.json', import.meta.url), 'utf8'))
channel.state = makeState()
channel.diff = mixed.diff
channel.drift = mixed.drift
html = render()
ok(html.includes('2 broken · 5 drift · 4 foreign · 4 unlinked') && html.includes('Problems (15)'), 'mixed problem totals combine foreign and unmanaged')

channel.mode = 'loading'
html = render()
ok((html.match(/data-skeleton/g) || []).length === 3, 'loading uses three compact skeletons')

channel.mode = 'error'
html = render()
ok(html.includes('Skills backend unavailable') && html.includes('Retry'), 'error state offers retry')
channel.errorMessage = 'Headless backend: web UI disabled'
html = render()
ok(html.includes('gateway mounts this plugin') && html.includes('hermes gateway restart'), 'gateway error shows restart remedy')

channel.mode = 'ready'
channel.state = { ok: true, skills_root_exists: true, counts: { skills: 0 }, tools: [], skills: [] }
channel.diff = { ok: true, counts: {} }
channel.drift = { ok: true, count: 0, drifted: [] }
html = render()
ok(html.includes('0 skills · 0 tools') && html.includes('No skills yet'), 'empty summary retains quick access')
ok((html.match(/role="switch"/g) || []).length === 0, 'empty pane still has zero switches')

channel.state = { ok: true, skills_root_exists: false, counts: { skills: 0 }, tools: [], skills: [] }
html = render()
ok(html.includes('No skills root found'), 'missing-root state renders')

const fixturePath = new URL('./fixtures/screenshot-shaped-105-skills.json', import.meta.url).pathname
const largeState = JSON.parse(fs.readFileSync(fixturePath, 'utf8'))
channel.state = largeState
html = render()
ok(html.includes('105 skills · 5 tools'), '105-skill summary reports five link tools')
ok((html.match(/data-summary-tool=/g) || []).length === 6, 'large fixture still renders exactly six summary rows')
ok((html.match(/role="switch"/g) || []).length === 0, '105-skill pane renders zero switches')
const enabledCounts = new Map(largeState.tools.map(tool => [tool.id, 0]))
for (const skill of largeState.skills) {
  for (const [toolId, entry] of Object.entries(skill.tools)) {
    if (entry.state === 'enabled') enabledCounts.set(toolId, (enabledCounts.get(toolId) || 0) + 1)
  }
}
ok(['claude', 'codex', 'grok', 'opencode', 'zcode'].every(id => html.includes(`${enabledCounts.get(id)} on`)), 'large fixture per-tool counts are derived from state')

const reactKeyWarnings = consoleErrors.concat(consoleWarnings).filter(message => /unique key|same key|key prop/i.test(message))
ok(reactKeyWarnings.length === 0, `render paths emit zero React key warnings${reactKeyWarnings.length ? `: ${reactKeyWarnings.join(' | ')}` : ''}`)
ok(consoleErrors.length === 0, `render paths emit zero console.error messages${consoleErrors.length ? `: ${consoleErrors.join(' | ')}` : ''}`)
ok(consoleWarnings.length === 0, `render paths emit zero console.warn messages${consoleWarnings.length ? `: ${consoleWarnings.join(' | ')}` : ''}`)
console.error = originalConsoleError
console.warn = originalConsoleWarn

console.log()
if (failures.length) {
  console.log(`RENDER HARNESS FAILED — ${failures.length} problem(s)`)
  process.exit(1)
}
console.log('RENDER HARNESS: ALL CHECKS PASSED')
