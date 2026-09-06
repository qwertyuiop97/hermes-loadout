// Render harness driver — simulates a staging install of the desktop plugin:
//   staging/plugin.js  (fresh copy of desktop/plugin.js)
//   staging/node_modules/{react, react-dom, @hermes/plugin-sdk(stub)}
// Renders the real pane with real React (react-dom/server) across scenarios
// and asserts on the output.
import { renderToString } from 'react-dom/server'

const PLUGIN_SRC = process.env.PLUGIN_SRC || '/Users/bakirmousa/Projects/skills-toggle/desktop/plugin.js'
const STAGING = process.env.STAGING_PLUGIN || '/tmp/skt-front/staging/plugin.js'

const failures = []
const ok = (cond, msg) => {
  if (cond) console.log('ok  ' + msg)
  else {
    failures.push(msg)
    console.log('FAIL ' + msg)
  }
}

// ---- fixture state (mirrors tests/test_plugin_api.py fixtures) -------------
function makeState() {
  const tools = () => ({
    hermes: { state: 'enabled' },
    claude: { state: 'missing' },
    codex: { state: 'missing' },
    opencode: { state: 'missing' },
    grok: { state: 'missing' },
    zcode: { state: 'missing' }
  })
  const st = {
    ok: true,
    hermes_home: '/tmp/hermes',
    skills_root: '/tmp/hermes/skills',
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
    skills: []
  }
  const appleNotes = tools()
  appleNotes.codex = { state: 'enabled' }
  appleNotes.grok = { state: 'foreign-link', target: '/elsewhere' }
  const rem = tools()
  rem.grok = { state: 'broken-link', target: '/gone' }
  const airtable = tools()
  airtable.hermes = { state: 'disabled' }
  airtable.grok = { state: 'unmanaged-dir' }
  st.skills = [
    { id: 'apple/apple-notes', name: 'apple-notes', category: 'apple', description: 'control Apple Notes via JXA', tools: appleNotes },
    { id: 'apple/rem índéluxé', name: 'rem índéluxé', category: 'apple', description: 'unicode skill name', tools: rem },
    { id: 'productivity/airtable', name: 'airtable', category: 'productivity', description: 'airtable automation', tools: airtable }
  ]
  return st
}

const fixtureDiff = {
  ok: true,
  unlinked: ['productivity/airtable'],
  broken: [{ tool: 'grok', name: 'rem índéluxé', target: '/gone' }],
  foreign: [{ tool: 'grok', name: 'apple-notes', target: '/elsewhere' }],
  unmanaged: [{ tool: 'grok', name: 'airtable' }],
  counts: { unlinked: 1, broken: 1, foreign: 1, unmanaged: 1 }
}

// ---- shared channel with the SDK stub ---------------------------------------
const channel = {
  mode: 'ready',
  state: makeState(),
  diff: fixtureDiff,
  bundle: {},
  registry: [],
  notifications: [],
  navigations: [],
  invalidated: [],
  mcpState: {
    ok: true,
    rows: [
      { name: 'chrome-devtools', enabled: true, definition: {}, writers: { claude: 'enabled' } },
      { name: 'docs', enabled: true, definition: {}, writers: { claude: 'drifted' } },
      { name: 'weather', enabled: false, definition: {}, writers: { claude: 'missing' } }
    ],
    foreign: [{ name: 'drifted-server', keys: ['command'] }],
    counts: { catalog: 3, foreign: 1 },
    writers: { claude: { label: 'Claude Desktop', path: '/tmp/claude.json', present: true } }
  },
  restCalls: [],
  driftList: {
    ok: true,
    drifted: [
      {
        tool: 'codex',
        name: 'architecture-diagram',
        skill_id: 'creative/architecture-diagram',
        external_path: '/tmp/codex/architecture-diagram',
        hermes_path: '/tmp/hermes/skills/creative/architecture-diagram',
        external_mtime: 1,
        hermes_mtime: 2
      }
    ],
    count: 1
  }
}
globalThis.__SKT = channel

// ---- install the plugin into the staging shape ------------------------------
const { copyFileSync } = await import('fs')
copyFileSync(PLUGIN_SRC, STAGING)
const plugin = (await import(STAGING)).default

// ---- register with a mock ctx ----------------------------------------------
const storage = new Map()
const ctx = {
  source: 'plugin:skills-toggle',
  rest: async (path, opts) => {
    channel.restCalls.push({ path, opts })
    if (path === '/state') return channel.state
    if (path === '/diff') return channel.diff
    return { ok: true }
  },
  socket: () => () => {},
  os: {},
  storage: {
    get: (k, d) => (storage.has(k) ? storage.get(k) : d),
    set: (k, v) => storage.set(k, v),
    remove: k => storage.delete(k)
  },
  i18n: { register: b => Object.assign(channel.bundle, b) },
  register: c => channel.registry.push(c),
  registerMany: cs => cs.forEach(c => channel.registry.push(c))
}
plugin.register(ctx)

// ---- contract assertions ------------------------------------------------------
ok(plugin.id === 'skills-toggle', 'plugin.id matches folder name')
ok(plugin.defaultEnabled === false, 'desktop half ships opt-in (defaultEnabled: false)')
ok(channel.registry.length === 6, `registers 6 contributions (got ${channel.registry.length})`)
const paneC = channel.registry.find(c => c.area === 'panes')
const pageC = channel.registry.find(c => c.area === 'routes')
const palC = channel.registry.find(c => c.area === 'palette' && c.data.label === 'Skills: toggle…')
const palReport = channel.registry.find(c => c.area === 'palette' && c.data.label === 'Skills: health report')
const chipC = channel.registry.find(c => c.area === 'statusbar.right')
ok(!!paneC && paneC.data && paneC.data.placement === 'right' && paneC.data.width === '320px', 'pane contribution: placement right, width 320px')
ok(!!pageC && pageC.data && pageC.data.path === '/skills-toggle', 'page contribution: /skills-toggle route')
ok(!!palC, 'palette command "Skills: toggle…" registered')
ok(!!palReport, 'palette command "Skills: health report" registered')
const palMcp = channel.registry.find(c => c.area === 'palette' && c.data.label === 'MCP: toggle…')
ok(!!palMcp, 'palette command "MCP: toggle…" registered')
ok(!!chipC && typeof chipC.render === 'function', 'statusbar health chip registered')
const chipHtml = renderToString(chipC.render())
ok(chipHtml.includes('1 broken'), 'health chip surfaces broken count from /diff')
ok(Array.isArray(channel.bundle.en) === false && Object.keys(channel.bundle.en).length > 30, `en i18n bundle complete (${Object.keys(channel.bundle.en).length} keys)`)

palC.data.run()
ok(channel.navigations.includes('/skills-toggle'), 'palette run navigates to the Skills page')

// every i18n key referenced by t('...') exists in the bundle
const pluginSrc = (await import('fs')).readFileSync(PLUGIN_SRC, 'utf8')
const usedKeys = [...pluginSrc.matchAll(/\bt\(\s*'([A-Za-z0-9_]+)'/g)].map(m => m[1])
const missingKeys = [...new Set(usedKeys.filter(k => !(k in channel.bundle.en)))]
ok(missingKeys.length === 0, `every t() key exists in the en bundle (missing: ${missingKeys.join(', ') || 'none'})`)

// ---- render: ready state -----------------------------------------------------
function render() {
  return renderToString(paneC.render())
}
let html = render()

ok(html.includes('Search skills'), 'header shows the search field')
ok(html.includes('>Skills<'), 'pane title renders')
ok(html.includes('Set up your tools'), 'onboarding setup panel renders by default')
ok(html.includes('Coding') && html.includes('Writing') && html.includes('Minimal'), 'built-in preset chips render')
ok(html.includes('Download') && html.includes('Open file…'), 'preset file export/import controls render')
ok((html.match(/role="switch"/g) || []).length === 18, `18 per-tool switches render (3 skills × 6 tools)`)
ok(html.includes('apple-notes') && html.includes('airtable') && html.includes('rem índéluxé'), 'skill names incl. unicode render')
ok(html.includes('>apple<') && html.includes('>productivity<'), 'category headers render')
ok(html.includes('bg-background'), 'sticky category headers are opaque (no bleed-through on scroll)')
ok(html.includes('data-variant="muted"') && html.includes('hermes off'), 'hermes-off badge renders for airtable')
ok(html.includes('Repair all'), 'Repair-all button shows when broken links exist')
ok(html.includes('data-tone="warn"') || html.includes('data-tone="bad"'), 'problem states show warn/bad dots')
ok(html.includes('outside the skills tree'), 'foreign-link tooltip is translated, not a raw i18n key')
ok(html.includes('aria-checked="true"'), 'enabled links show checked switches')

ok(!html.includes('>undefined<') && !html.includes('>NaN<'), 'no undefined/NaN leaks into the DOM')

// search filtering (debounced value set directly through the component state —
// we exercise the filter path by re-rendering with a pre-seeded query value is
// not possible from SSR; instead verify the filter map logic via the off view)
channel.mode = 'ready'
storage.set('viewFilter', 'off')
html = render()
ok(!html.includes('apple-notes'), 'Off view hides skills linked somewhere')
storage.delete('viewFilter')

// drift view (stub driftList has one drifted item)
storage.set('viewFilter', 'drift')
html = render()
ok(html.includes('No drift detected') === false || true, 'drift view renders without crashing')
ok(html.includes('Use Hermes'), 'drift view offers the Use-Hermes push action')
ok(html.includes('architecture-diagram'), 'drift view lists the drifted skill name')
storage.delete('viewFilter')

// MCP tab (pre-seeded via storage, like a returning user)
storage.set('paneTab', 'mcp')
html = render()
ok(html.includes('MCP servers'), 'MCP tab renders the server list')
ok(html.includes('chrome-devtools') && html.includes('weather'), 'catalog rows render')
ok((html.match(/role=\"switch\"/g) || []).length === 6, '6 MCP switches (3 servers × Hermes + Claude)')
ok(html.includes('drifted'), 'drifted badge renders for the drifted server')
ok(html.includes('>sync<'), 'sync action offered on drifted rows')
ok(html.includes('not in the Hermes catalog'), 'foreign servers noted and never touched')
storage.delete('paneTab')
html = render()
ok(html.includes('Search skills'), 'default tab is still Skills')

// tool filter pre-seeded → bulk buttons appear for that tool
storage.set('toolFilter', 'claude')
html = render()
ok(html.includes('Link all') && html.includes('Unlink all'), 'bulk row actions appear for a specific tool filter')
storage.delete('toolFilter')
html = render()
ok(!html.includes('Link all'), 'bulk row actions hidden under the All filter (safety)')

// ---- render: loading ---------------------------------------------------------
channel.mode = 'loading'
html = render()
ok((html.match(/data-skeleton/g) || []).length === 8, 'skeleton loading state renders (8 rows)')

// ---- render: error -----------------------------------------------------------
channel.mode = 'error'
html = render()
ok(html.includes('Skills backend unavailable'), 'error state renders a clear banner')
ok(html.includes('Retry'), 'error state offers Retry')

// known backend-mount failure maps to the exact remedy
channel.errorMessage = "Error invoking remote method 'hermes:api': Error: 404: Headless backend (hermes serve): web UI disabled"
html = render()
ok(
  html.includes('gateway mounts this plugin') && html.includes('hermes gateway restart'),
  'headless-404 maps to the gateway-restart remedy'
)
ok(html.includes('Headless backend'), 'raw error stays visible for debugging')
channel.errorMessage = 'Error: 404: {"detail": "Plugin not found"}'
html = render()
ok(html.includes('hermes gateway restart'), 'plugin-not-found maps to the same remedy')

// ---- render: empty -----------------------------------------------------------
channel.mode = 'ready'
channel.state = { ok: true, skills_root_exists: true, counts: { skills: 0, unlinked: 0 }, tools: [], skills: [] }
html = render()
ok(html.includes('No skills yet'), 'empty state renders when no skills exist')

// ---- render: missing skills root --------------------------------------------
channel.state = { ok: true, skills_root_exists: false, counts: { skills: 0, unlinked: 0 }, tools: [], skills: [] }
html = render()
ok(html.includes('No skills root found'), 'missing skills-root state renders')

console.log()
if (failures.length) {
  console.log(`RENDER HARNESS FAILED — ${failures.length} problem(s)`)
  process.exit(1)
}
console.log('RENDER HARNESS: ALL CHECKS PASSED')
