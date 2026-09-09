// Control Center workspace and route-fallback render harness.
import { renderToString } from 'react-dom/server'
import TestRenderer, { act } from 'react-test-renderer'
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
  rest: async (path, options = {}) => {
    channel.restCalls.push({ path, body: options.body })
    if (path === '/state') return channel.state
    if (path === '/diff') return channel.diff
    if (path === '/drift') return channel.drift
    if (path === '/mcp/state') return channel.mcpState
    if (path === '/bulk/plan') {
      const ids = options.body.skills
      const wouldChange = ids.slice(0, 3)
      const already = ids.slice(3, 4)
      const refused = ids.slice(4).map(skill => ({ skill, code: 'foreign-link', reason: 'Protected foreign link was left untouched' }))
      return {
        ok: true,
        would_change: wouldChange,
        already_satisfied: already,
        refused,
        ordered_explicit_ids: ids,
        sample: wouldChange.slice(0, 2).map(skillId => {
          const skill = state.skills.find(row => row.id === skillId)
          return { skill_id: skillId, name: skill.name, category: skill.category, current_state: 'missing', next_state: 'enabled' }
        }),
        totals: { would_change: wouldChange.length, already_satisfied: already.length, refused: refused.length }
      }
    }
    if (path === '/bulk/apply') {
      const ids = options.body.skills
      const undoing = options.body.enabled === false
      const results = ids.map((skill, index) => undoing || index < 2
        ? { skill, ok: true, state: options.body.enabled ? 'enabled' : 'disabled' }
        : { skill, ok: false, state: 'unmanaged-dir', code: 'unmanaged-dir', error: 'Became protected after preview' })
      const changedIds = results.filter(row => row.ok).map(row => row.skill)
      return {
        ok: true,
        results,
        receipt: {
          receipt_id: undoing ? 'receipt-undo' : 'receipt-apply',
          tool: options.body.tool,
          enabled: options.body.enabled,
          items: results,
          changed: changedIds.length,
          failed: results.length - changedIds.length,
          refused: results.length - changedIds.length,
          undone_by: changedIds.map(skill => ({ skill, enabled: !options.body.enabled }))
        }
      }
    }
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
const presentTools = state.tools.filter(tool => tool.id === 'hermes' || !tool.optional || tool.present)
ok((html.match(/data-tool-card=/g) || []).length === presentTools.length, 'Tools renders one ToolCard per present/configured tool including Hermes')
ok(presentTools.every(tool => html.includes(`data-tool-card="${tool.id}"`) && html.includes(tool.label)), 'every eligible tool has a labeled card')
ok(!html.includes('data-tool-card="kimi"') && !html.includes('>Kimi<'), 'absent optional tools stay out of the daily overview')
ok((html.match(/>Manage<\/button>/g) || []).length === presentTools.length, 'every ToolCard has Manage')
ok((html.match(/>Enable all<\/button>/g) || []).length === presentTools.length, 'every ToolCard has Enable all')
ok((html.match(/>Disable all<\/button>/g) || []).length === presentTools.length, 'every ToolCard has Disable all')
ok(html.includes('/Users/demo/.hermes/skills') && html.includes('/Users/demo/.codex/skills'), 'ToolCards show detected paths')
ok(html.includes('45 on / 105') && html.includes('60</div><div class="text-muted-foreground">off'), 'Hermes card counts enabled and off correctly')
ok(html.includes('79 on / 105') && html.includes('13</div><div class="text-muted-foreground">off') && html.includes('13</div><div class="text-muted-foreground">problems'), 'Claude card counts enabled, off, and problems correctly')
ok((html.match(/role="switch"/g) || []).length === 0 && !html.includes('Search skills'), 'Tools overview replaces the transitional skill matrix')
ok(html.includes('Add Tool'), 'Tools overview keeps absent optional targets reachable through Add Tool')

let interactive
await act(async () => { interactive = TestRenderer.create(workspace.render()) })
const grokCard = interactive.root.findByProps({ 'data-tool-card': 'grok' })
const manageGrok = grokCard.findAllByType('button').find(node => node.children.join('') === 'Manage')
await act(async () => { manageGrok.props.onClick() })
let singleRows = interactive.root.findAll(node => node.props['data-single-tool-skill'])
let singleSwitches = interactive.root.findAll(node => node.props.role === 'switch')
ok(interactive.root.findByProps({ 'data-single-tool': 'grok' }) && singleRows.length === state.skills.length, 'Manage opens the complete single-tool skill list')
ok(singleSwitches.length === state.skills.length && singleSwitches.every((node, index) => node.props['aria-label'].includes(state.skills[index].name) && node.props['aria-label'].includes('Grok')), 'single-tool rows render one accessible skill-and-tool switch each')

const search = interactive.root.findByProps({ placeholder: 'Search skills…' })
await act(async () => {
  search.props.onChange('test-driven-development')
  await new Promise(resolve => setTimeout(resolve, 230))
})
singleRows = interactive.root.findAll(node => node.props['data-single-tool-skill'])
ok(singleRows.length === 1 && singleRows[0].props['data-single-tool-skill'] === 'software-development/test-driven-development', 'single-tool search is debounced and filters the full catalog')
await act(async () => {
  interactive.root.findByProps({ placeholder: 'Search skills…' }).props.onChange('')
  await new Promise(resolve => setTimeout(resolve, 230))
})

const issuesButton = interactive.root.findAllByProps({ role: 'radio' }).find(node => node.children.join('') === 'Issues')
await act(async () => { issuesButton.props.onClick() })
singleRows = interactive.root.findAll(node => node.props['data-single-tool-skill'])
ok(singleRows.length > 0 && singleRows.length < state.skills.length, 'All / Enabled / Off / Issues segmented views filter single-tool rows')
const allButton = interactive.root.findAllByProps({ role: 'radio' }).find(node => node.children.join('') === 'All')
await act(async () => { allButton.props.onClick() })

const categorySelect = interactive.root.findByProps({ 'aria-label': 'Category' })
await act(async () => { categorySelect.props.onChange({ target: { value: 'apple' } }) })
singleRows = interactive.root.findAll(node => node.props['data-single-tool-skill'])
ok(singleRows.length === 5 && singleRows.every(node => node.props['data-single-tool-skill'].startsWith('apple/')), 'category filter scopes the single-tool list')
const categoryToggle = interactive.root.findByProps({ 'data-tool-category': 'apple' }).findAllByType('button').find(node => String(node.children.join('')).includes('apple'))
await act(async () => { categoryToggle.props.onClick() })
ok(interactive.root.findAll(node => node.props['data-single-tool-skill']).length === 0, 'category headings collapse and expand')
await act(async () => { categoryToggle.props.onClick() })

const selectAll = interactive.root.findByProps({ 'aria-label': 'Select all visible' })
await act(async () => { selectAll.props.onChange({ target: { checked: true } }) })
ok(interactive.root.findByProps({ 'data-selection-bar': 'sticky' }) && JSON.stringify(interactive.toJSON()).includes('5 selected'), 'Select all visible creates the sticky selected-action bar')
const enableSelected = interactive.root.findAllByType('button').find(node => node.children.join('') === 'Enable selected')
await act(async () => { enableSelected.props.onClick(); await Promise.resolve() })
let treeText = JSON.stringify(interactive.toJSON())
ok(treeText.includes('3 will change') && treeText.includes('1 already set') && treeText.includes('1 protected/refused'), 'bulk preview dialog shows planned change, satisfied, and refused counts')
ok(treeText.includes('Protected foreign link was left untouched') && interactive.root.findAll(node => node.props['data-plan-refused']).length === 1, 'bulk preview visibly names refused entries and reasons')
const planned = channel.restCalls.filter(call => call.path === '/bulk/plan').at(-1)
const confirmEnable = interactive.root.findAllByType('button').find(node => node.children.join('') === 'Confirm enable')
await act(async () => { confirmEnable.props.onClick(); await Promise.resolve() })
const applied = channel.restCalls.filter(call => call.path === '/bulk/apply').at(-1)
ok(JSON.stringify(applied.body.skills) === JSON.stringify(planned.body.skills.slice(0, 3)), 'apply uses the exact immutable would_change ids returned by the plan')
treeText = JSON.stringify(interactive.toJSON())
ok(treeText.includes('2 changed, 1 failed') && treeText.includes('Became protected after preview'), 'receipt exposes changed totals and per-item partial failure')
ok(channel.invalidated.includes('state') && channel.invalidated.includes('diff'), 'apply invalidates state and diff so counts refresh from backend state')
const undoButton = interactive.root.findAllByType('button').find(node => node.children.join('') === 'Undo')
await act(async () => { undoButton.props.onClick(); await Promise.resolve(); await Promise.resolve() })
const undoApply = channel.restCalls.filter(call => call.path === '/bulk/apply').at(-1)
ok(undoApply.body.enabled === false && JSON.stringify(undoApply.body.skills) === JSON.stringify(planned.body.skills.slice(0, 2)), 'Undo applies only the receipt.undone_by skill set')
const disableCategory = interactive.root.findAllByType('button').find(node => node.children.join('') === 'Disable category')
await act(async () => { disableCategory.props.onClick(); await Promise.resolve() })
const categoryPlan = channel.restCalls.filter(call => call.path === '/bulk/plan').at(-1)
ok(categoryPlan.body.enabled === false && categoryPlan.body.skills.length === 5 && categoryPlan.body.skills.every(id => id.startsWith('apple/')), 'category actions plan the explicit filtered category ids')
const cancelCategory = interactive.root.findAllByType('button').find(node => node.children.join('') === 'Cancel')
await act(async () => { cancelCategory.props.onClick() })
const backToTools = interactive.root.findAllByType('button').find(node => node.children.join('') === 'Back to Tools')
await act(async () => { backToTools.props.onClick() })
const overviewGrok = interactive.root.findByProps({ 'data-tool-card': 'grok' })
const overviewEnableAll = overviewGrok.findAllByType('button').find(node => node.children.join('') === 'Enable all')
await act(async () => { overviewEnableAll.props.onClick(); await Promise.resolve() })
const overviewPlan = channel.restCalls.filter(call => call.path === '/bulk/plan').at(-1)
ok(overviewPlan.body.enabled === true && overviewPlan.body.skills.length === state.skills.length, 'overview page-level actions also use the bulk planner with explicit ids')

channel.atoms[0].set('sets')
html = renderToString(workspace.render())
ok(html.includes('Coding') && html.includes('Writing') && html.includes('Minimal'), 'Sets mounts existing presets')
ok(html.includes('Download') && html.includes('Open file…') && html.includes('Copy current'), 'Sets retains preset import and export controls')
ok(!html.includes('Search skills'), 'Sets is a distinct section, not the Tools catalog')

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
