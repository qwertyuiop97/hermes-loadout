// Control Center workspace and route-fallback render harness.
import { renderToString } from 'react-dom/server'
import TestRenderer, { act } from 'react-test-renderer'
import { readFileSync } from 'fs'

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
const scanFixturePath = new URL('./fixtures/first-run-scan-plan.json', import.meta.url).pathname
const scanPlan = JSON.parse(readFileSync(scanFixturePath, 'utf8'))
const channel = {
  mode: 'ready',
  state,
  diff: {
    ok: true,
    unlinked: ['research/deep-research'],
    broken: [{ tool: 'claude', name: 'broken-skill', target: '/missing/skills' }],
    foreign: [{ tool: 'opencode', name: 'foreign-skill', target: '/opt/team/foreign-skill' }],
    unmanaged: [{ tool: 'grok', name: 'real-skill-dir' }],
    counts: { broken: 1, foreign: 1, unmanaged: 1, unlinked: 1 }
  },
  drift: {
    ok: true,
    count: 1,
    drifted: [{ tool: 'codex', name: 'architecture-diagram', skill_id: 'creative/architecture-diagram' }]
  },
  mcpState: {
    ok: true,
    rows: [
      { name: 'chrome-devtools', enabled: true, writers: { claude: 'enabled', codex: 'enabled' } },
      { name: 'docs', enabled: true, writers: { claude: 'enabled', codex: 'drifted' } },
      { name: 'weather', enabled: false, writers: { claude: 'missing', codex: 'missing' } }
    ],
    foreign: [{ name: 'foreign-server' }],
    counts: { catalog: 3, foreign: 1 },
    writers: {
      claude: { label: 'Claude Desktop', path: '/tmp/claude.json', present: true },
      codex: { label: 'Codex', path: '/tmp/config.toml', present: true }
    }
  },
  bundle: {}, registry: [], notifications: [], navigations: [], invalidated: [], workspaces: [], restCalls: [],
  holdScan: false, holdApply: false, resolveScan: null, resolveApply: null, scanResponse: null
}
globalThis.__SKT = channel

const plugin = (await import(STAGING)).default
const storage = new Map()
plugin.register({
  source: 'plugin:hermes-switchboard',
  rest: async (path, options = {}) => {
    channel.restCalls.push({ path, body: options.body })
    if (path === '/state') return channel.state
    if (path === '/diff') return channel.diff
    if (path === '/drift') return channel.drift
    if (path === '/mcp/state') return channel.mcpState
    if (path === '/import/plan') {
      if (channel.holdScan) return new Promise(resolve => { channel.resolveScan = resolve })
      return channel.scanResponse || scanPlan
    }
    if (path === '/import/apply-plan') {
      const entries = options.body.entries
      const response = {
        ok: true,
        results: entries.map((entry, index) => index === 0
          ? { ...entry, ok: true, code: 'adopted', skill: `imported/${entry.name}`, path: `/Users/demo/.hermes/skills/imported/${entry.name}`, backup: `${entry.source}/${entry.name}.hermes-switchboard-backup-fixture` }
          : { ...entry, ok: false, code: 'link-swap-failed', error: 'link swap failed; original state restored', changed_since_preview: false }),
        receipt: {
          receipt_id: 'import-receipt-001', adopted: 1, failed: entries.length - 1, refused: 0,
          items: [],
          undo: [{ path: `${entries[0].source}/${entries[0].name}`, backup: `${entries[0].source}/${entries[0].name}.hermes-switchboard-backup-fixture`, kind: 'restore-tool-entry' }]
        },
        adopted: 1, failed: entries.length - 1, refused: 0
      }
      response.receipt.items = response.results
      if (channel.holdApply) return new Promise(resolve => { channel.resolveApply = () => resolve(response) })
      return response
    }
    if (path === '/conflict/revert-adopt') return { ok: true, action: 'reverted', name: options.body.name, tool: options.body.tool }
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
    if (path === '/bulk/undo') {
      const applied = channel.restCalls.filter(call => call.path === '/bulk/apply').at(-1)
      const results = applied.body.skills.slice(0, 2).map(skill => ({ skill, ok: true, state: 'missing' }))
      return { ok: true, changed: 2, failed: 0, results,
        receipt: { receipt_id: options.body.receipt_id, tool: applied.body.tool, changed: 2, failed: 0, undo_available: false } }
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
          undo_available: true,
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
ok(workspace.id === 'hermes-switchboard.control-center', 'workspace uses stable id')
ok(workspace.minWidth === '680px' && workspace.title === 'Hermes Switchboard', 'workspace options set title and minimum width')
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
ok(!html.includes('data-expert-matrix'), 'default Tools render stays in tool-first cards mode')
ok(!['Watch mode', 'Export blueprint', 'List backups', 'category regex…'].some(value => html.includes(value)), 'Tools overview excludes Advanced watch, blueprint, backup, and auto-link controls')
ok(html.includes('Add Tool'), 'Tools overview keeps absent optional targets reachable through Add Tool')
ok(html.includes('data-onboarding-entry="true"') && html.includes('Start setup'), 'incomplete onboarding exposes a dedicated Tools entry point')

const previousResizeObserver = globalThis.ResizeObserver
const previousWindow = globalThis.window
class FakeRO {
  constructor(cb) { this.cb = cb }
  observe(_element) { this.cb([{ contentRect: { width: 900 } }]) }
  disconnect() {}
}
globalThis.ResizeObserver = FakeRO
globalThis.window = {
  matchMedia: query => ({
    media: query,
    matches: query === '(prefers-reduced-motion: reduce)',
    addEventListener: () => {},
    removeEventListener: () => {}
  })
}
let matrix
await act(async () => {
  matrix = TestRenderer.create(workspace.render(), { createNodeMock: () => ({}) })
  await Promise.resolve()
})
ok(matrix.root.findByProps({ 'data-layout': 'wide' }), 'ResizeObserver fixture drives the Control Center to wide layout')
const matrixToggle = matrix.root.findAllByType('button').find(node => node.children.join('') === 'Matrix')
ok(!!matrixToggle && matrixToggle.type === 'button' && matrixToggle.children.join('') === 'Matrix', 'wide Tools exposes a keyboard-reachable button named Matrix')
const reducedMotionCss = matrix.root.findByProps({ 'data-reduced-motion-guard': 'true' }).children.join('')
ok(matrix.root.findByProps({ 'data-reduced-motion': 'true' }) && reducedMotionCss.includes('transition:none') && reducedMotionCss.includes('animation:none'), 'reduced-motion preference disables plugin transitions and animations')
await act(async () => { matrixToggle.props.onClick() })
ok(matrix.root.findByProps({ 'data-expert-matrix': 'true' }), 'Matrix toggle renders ExpertMatrix as a Tools sub-state')
const matrixHeaders = matrix.root.findAll(node => node.props['data-matrix-tool'])
ok(matrixHeaders.length === presentTools.length && presentTools.every(tool => matrixHeaders.some(node => node.props['data-matrix-tool'] === tool.id)), 'matrix has a column for every present tool including Hermes')
ok(!matrixHeaders.some(node => node.props['data-matrix-tool'] === 'kimi'), 'absent optional Kimi is not a matrix column')
ok(matrix.root.findAll(node => node.props['data-matrix-skill']).length === 0, '105-skill matrix categories start collapsed')
const collapsedMatrixSwitches = matrix.root.findAll(node => node.props.role === 'switch')
ok(collapsedMatrixSwitches.length < state.skills.length, 'collapsed 105-skill matrix keeps the rendered switch count far below the full catalog')
const collapsedMatrixCategories = matrix.root.findAll(node => node.type === 'button' && node.props['aria-expanded'] === false)
ok(collapsedMatrixCategories.length === 8 && collapsedMatrixCategories.every(node => node.children.join('').trim().length > 0), 'matrix category buttons expose aria-expanded and a visible name')
const matrixSearch = matrix.root.findByProps({ placeholder: 'Search skills…' })
await act(async () => {
  matrixSearch.props.onChange('test-driven-development')
  await new Promise(resolve => setTimeout(resolve, 230))
})
ok(matrix.root.findAll(node => node.props['data-matrix-skill']).length === 1, 'matrix search reaches matching skills across collapsed categories')
await act(async () => {
  matrix.root.findByProps({ placeholder: 'Search skills…' }).props.onChange('')
  await new Promise(resolve => setTimeout(resolve, 230))
})
const matrixCategories = matrix.root.findAll(node => node.type === 'button' && node.props['aria-expanded'] === false)
await act(async () => { matrixCategories.forEach(node => node.props.onClick()) })
const matrixRows = matrix.root.findAll(node => node.props['data-matrix-skill'])
const matrixSwitches = matrix.root.findAll(node => node.props.role === 'switch')
ok(matrixRows.length === state.skills.length, 'expanding matrix categories exposes the complete 105-skill catalog')
ok(matrixSwitches.length === state.skills.length * presentTools.length && matrixRows.every(row => {
  const skill = state.skills.find(item => item.id === row.props['data-matrix-skill'])
  const switches = row.findAll(node => node.props.role === 'switch')
  return switches.length === presentTools.length && switches.every((node, index) => node.props['aria-label'].includes(skill.name) && node.props['aria-label'].includes(presentTools[index].label))
}), 'every matrix cell switch has a skill-and-tool accessible label')
ok(matrixSwitches.every(node => node.type === 'button' && typeof node.props['aria-label'] === 'string' && node.props['aria-label'].length > 0), 'matrix switches are focusable buttons with accessible names')
ok(!JSON.stringify(matrix.toJSON()).includes(state.skills[0].description), 'matrix descriptions are hidden by default')
matrix.unmount()
if (previousResizeObserver === undefined) delete globalThis.ResizeObserver
else globalThis.ResizeObserver = previousResizeObserver
if (previousWindow === undefined) delete globalThis.window
else globalThis.window = previousWindow

let interactive
await act(async () => { interactive = TestRenderer.create(workspace.render()) })
const grokCard = interactive.root.findByProps({ 'data-tool-card': 'grok' })
const manageGrok = grokCard.findAllByType('button').find(node => node.children.join('') === 'Manage')
await act(async () => { manageGrok.props.onClick() })
let singleRows = interactive.root.findAll(node => node.props['data-single-tool-skill'])
let singleSwitches = interactive.root.findAll(node => node.props.role === 'switch')
ok(interactive.root.findByProps({ 'data-single-tool': 'grok' }) && singleRows.length === state.skills.length, 'Manage opens the complete single-tool skill list')
ok(singleSwitches.length === state.skills.length && singleSwitches.every((node, index) => node.props['aria-label'].includes(state.skills[index].name) && node.props['aria-label'].includes('Grok')), 'single-tool rows render one accessible skill-and-tool switch each')
ok(singleSwitches.every(node => node.type === 'button' && typeof node.props['aria-label'] === 'string' && node.props['aria-label'].length > 0), 'single-tool switches are focusable buttons with accessible names')
const singleCategoryButtons = interactive.root.findAll(node => node.type === 'button' && typeof node.props['aria-expanded'] === 'boolean')
ok(singleCategoryButtons.length === 8 && singleCategoryButtons.every(node => node.children.join('').trim().length > 0), 'single-tool category buttons expose aria-expanded and a visible name')

const search = interactive.root.findByProps({ placeholder: 'Search skills…' })
await act(async () => {
  search.props.onChange('test-driven-development')
  await new Promise(resolve => setTimeout(resolve, 350))
})
singleRows = interactive.root.findAll(node => node.props['data-single-tool-skill'])
ok(singleRows.length === 1 && singleRows[0].props['data-single-tool-skill'] === 'software-development/test-driven-development', 'single-tool search is debounced and filters the full catalog')
await act(async () => {
  interactive.root.findByProps({ placeholder: 'Search skills…' }).props.onChange('')
  await new Promise(resolve => setTimeout(resolve, 350))
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
const singleToolContainer = interactive.root.findByProps({ 'data-single-tool': 'grok' })
const singleToolShell = interactive.root.findByProps({ 'data-single-tool-shell': 'true' })
const bulkReceipt = interactive.root.findByProps({ 'data-bulk-receipt': 'grok' })
const singleToolLayout = interactive.root.findByProps({ 'data-single-tool-layout': 'true' })
ok(singleToolLayout.findAllByProps({ 'data-single-tool': 'grok' }).length === 1 && singleToolLayout.findAllByProps({ 'data-single-tool-shell': 'true' }).length === 1 && singleToolLayout.findAllByProps({ 'data-bulk-receipt': 'grok' }).length === 1 && singleToolShell.findAllByProps({ 'data-bulk-receipt': 'grok' }).length === 0 && singleToolShell.props.className.includes('min-h-0') && singleToolShell.props.className.includes('flex-1'), 'single-tool receipt is a visible sibling of the scrollable SingleToolView container')
ok(channel.invalidated.includes('state') && channel.invalidated.includes('diff'), 'apply invalidates state and diff so counts refresh from backend state')
const undoButton = interactive.root.findAllByType('button').find(node => node.children.join('') === 'Undo')
await act(async () => { undoButton.props.onClick(); await Promise.resolve(); await Promise.resolve() })
const undoApply = channel.restCalls.filter(call => call.path === '/bulk/undo').at(-1)
ok(JSON.stringify(undoApply.body) === JSON.stringify({ receipt_id: 'receipt-apply' }) && channel.restCalls.filter(call => call.path === '/bulk/apply').at(-1) === applied, 'Undo uses the durable receipt, never an inverse bulk mutation')
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

const pane = channel.registry.find(c => c.id === 'pane')
let compact
await act(async () => { compact = TestRenderer.create(pane.render()) })
const compactScan = compact.root.findAllByType('button').find(node => node.children.join('') === 'Scan')
await act(async () => { compactScan.props.onClick() })
const wizardWorkspace = channel.activeWorkspace
let wizard
await act(async () => { wizard = TestRenderer.create(wizardWorkspace.render()); await Promise.resolve() })
ok(wizard.root.findByProps({ 'data-first-run-wizard': 'welcome' }) && JSON.stringify(wizard.toJSON()).includes('Hermes your canonical skills library'), 'wizard step 1 explains canonical storage and preserved backups')

const wizardButton = label => wizard.root.findAllByType('button').find(node => node.children.join('') === label)
await act(async () => { wizardButton('Get started').props.onClick(); await Promise.resolve() })
ok(wizard.root.findByProps({ 'data-first-run-wizard': 'sources' }), 'wizard step 2 renders source selection')
const detected = wizard.root.findAll(node => node.props['data-detected-tool'])
ok(detected.length === 5 && detected.every(node => node.findByType('input').props.checked), 'source selection auto-detects and selects every present non-Hermes tool')
const folder = wizard.root.findAllByType('input').find(node => node.props.placeholder === '/path/to/another/skills folder')
await act(async () => { folder.props.onChange({ target: { value: '/Volumes/team/skills' } }) })
await act(async () => { wizardButton('Add folder').props.onClick() })
ok(wizard.root.findByProps({ 'data-scan-root': '/Volumes/team/skills' }), 'source selection accepts an additional scan folder')

channel.holdScan = true
await act(async () => { wizardButton('Run scan').props.onClick(); await Promise.resolve() })
ok(wizard.root.findByProps({ 'data-first-run-wizard': 'scanning' }) && wizard.root.findByProps({ 'data-wizard-scanning': 'true' }), 'wizard step 3 renders while the fresh import plan is pending')
const scanCall = channel.restCalls.filter(call => call.path === '/import/plan').at(-1)
ok(scanCall.body.tools.length === 5 && scanCall.body.scan_roots[0] === '/Volumes/team/skills', 'scan posts selected tool ids and owner-added roots')
channel.holdScan = false
await act(async () => { channel.resolveScan(scanPlan); await Promise.resolve(); await Promise.resolve() })
ok(wizard.root.findByProps({ 'data-first-run-wizard': 'review' }), 'wizard step 4 renders the classification review')
ok(['managed', 'unmanaged-skill', 'identical-duplicate', 'drifted', 'name-conflict', 'broken-link', 'foreign-link', 'unmanaged-dir'].every(kind => wizard.root.findByProps({ 'data-classification': kind })), 'classification review covers all required groups')
ok(wizard.root.findByProps({ 'data-duplicate-groups': 'true' }) && JSON.stringify(wizard.toJSON()).includes('shared-name'), 'duplicate names across sources are grouped and visibly flagged')

await act(async () => { wizardButton('Continue').props.onClick() })
ok(wizard.root.findByProps({ 'data-first-run-wizard': 'choose' }), 'wizard step 5 renders tool and adoption checkboxes')
ok(wizard.root.findAll(node => node.props['data-manage-tool']).length === 5 && wizard.root.findAll(node => node.props['data-adopt-entry']).length === 2, 'choice step exposes detected tools and only safe adoptable entries')
ok(wizard.root.findByProps({ 'data-duplicates-flagged': 'true' }), 'choice step keeps duplicate and drift exclusions visible')

await act(async () => { wizardButton('Review dry run').props.onClick() })
ok(wizard.root.findByProps({ 'data-first-run-wizard': 'preview' }) && wizard.root.findByProps({ 'data-import-preview': 'true' }), 'wizard step 6 renders the dry-run plan')
let wizardText = JSON.stringify(wizard.toJSON())
ok(wizardText.includes('2 selected') && wizardText.includes('3 sources') && wizardText.includes('3 conflicts') && wizardText.includes('/missing/skills'), 'dry run shows counts, examples, paths, and refusals')
await act(async () => { wizardButton('Apply plan…').props.onClick() })
ok(wizardText !== JSON.stringify(wizard.toJSON()) && JSON.stringify(wizard.toJSON()).includes('Confirm adoption'), 'apply is gated by an explicit confirmation dialog')

channel.holdApply = true
await act(async () => { wizardButton('Confirm adoption').props.onClick(); await Promise.resolve() })
ok(wizard.root.findByProps({ 'data-first-run-wizard': 'apply' }) && wizard.root.findByProps({ 'data-wizard-applying': 'true' }), 'wizard step 7 renders while the exact reviewed plan is applying')
const importApply = channel.restCalls.filter(call => call.path === '/import/apply-plan').at(-1)
ok(JSON.stringify(importApply.body.entries) === JSON.stringify(scanPlan.adoptable.map(row => ({ name: row.name, source: row.source, tool: row.tool }))), 'apply posts only the exact chosen name/source/tool entries')
channel.holdApply = false
await act(async () => { channel.resolveApply(); await Promise.resolve(); await Promise.resolve() })
ok(wizard.root.findByProps({ 'data-first-run-wizard': 'receipt' }) && wizard.root.findByProps({ 'data-import-receipt': 'import-receipt-001' }), 'wizard step 8 renders the durable receipt')
wizardText = JSON.stringify(wizard.toJSON())
ok(wizardText.includes('1 adopted') && wizardText.includes('1 failed') && wizardText.includes('original state restored'), 'receipt clearly surfaces partial adoption failure and rollback status')
ok(wizard.root.findByProps({ 'data-import-undo': 'restore-tool-entry' }), 'receipt renders its durable Undo/Restore path')

await act(async () => { wizardButton('Undo').props.onClick(); await Promise.resolve(); await Promise.resolve() })
const importUndo = channel.restCalls.filter(call => call.path === '/conflict/revert-adopt').at(-1)
ok(importUndo.body.name === 'solo-copy' && importUndo.body.skill === 'imported/solo-copy', 'Undo restores only the adopted receipt item through the existing revert route')
ok(wizard.root.findByProps({ 'data-import-undo-results': 'true' }), 'undo result remains visible beside the durable receipt')
await act(async () => { wizardButton('View Tools').props.onClick() })
const onboarding = storage.get('onboarding')
ok(onboarding.version === 1 && onboarding.complete === true && !('plan' in onboarding) && !('entries' in onboarding), 'step 9 lands on Tools and persists only versioned UI progress/preferences')

channel.atoms[0].set('tools')
html = renderToString(workspace.render())
ok(!html.includes('data-onboarding-entry="true"'), 'completed onboarding hides the first-run entry without caching filesystem truth')

channel.scanResponse = { ok: true, entries: [], adoptable: [], duplicate_groups: [], counts: {} }
channel.atoms[0].set('onboarding')
let emptyWizard
await act(async () => { emptyWizard = TestRenderer.create(workspace.render()); await Promise.resolve() })
const emptyWizardButton = label => emptyWizard.root.findAllByType('button').find(node => node.children.join('') === label)
await act(async () => { emptyWizardButton('Get started').props.onClick(); await Promise.resolve() })
await act(async () => { emptyWizardButton('Run scan').props.onClick(); await Promise.resolve(); await Promise.resolve() })
const emptyWizardText = JSON.stringify(emptyWizard.toJSON())
ok(emptyWizardText.includes('No skills found') && emptyWizardText.includes('Nothing changed') && emptyWizardButton('View Tools') && !emptyWizardButton('Continue'), 'empty first-run scan ends clearly instead of advancing to a disabled adoption step')
channel.scanResponse = null

channel.atoms[0].set('sets')
html = renderToString(workspace.render())
ok(html.includes('Coding') && html.includes('Writing') && html.includes('Minimal'), 'Sets mounts existing presets')
ok(html.includes('Download') && html.includes('Open file…') && html.includes('Copy current'), 'Sets retains preset import and export controls')
ok(!html.includes('Search skills'), 'Sets is a distinct section, not the Tools catalog')

report.data.run()
html = renderToString(channel.activeWorkspace.render())
ok(html.includes('architecture-diagram') && html.includes('Use Hermes'), 'Problems mounts the live drift panel')
ok(html.includes('Repair all') && html.includes('data-broken-entry'), 'Problems exposes reachable repair actions for broken entries')
ok(html.includes('data-protected-entries') && html.includes('foreign-skill') && html.includes('real-skill-dir'), 'Problems shows protected foreign links and real directories instead of silently counting them')
ok(html.includes('data-unlinked-skills') && html.includes('research/deep-research'), 'Problems explains and lists skills counted as unlinked')

mcp.data.run()
html = renderToString(channel.activeWorkspace.render())
ok(html.includes('MCP servers') && html.includes('chrome-devtools'), 'MCP mounts the live MCP pane')
ok((html.match(/role="switch"/g) || []).length === 9, 'MCP keeps nine live projection switches')
ok(html.includes('Claude Desktop') && html.includes('Codex') && html.includes('drifted'), 'MCP labels both supported clients and surfaces drift from either writer')

channel.atoms[0].set('advanced')
html = renderToString(workspace.render())
ok(html.includes('Set up your tools') && html.includes('Watch mode') && html.includes('Machine blueprint') && html.includes('Export blueprint') && html.includes('List backups') && html.includes('Auto-link:'), 'Advanced mounts setup, watch, blueprint, backup, and auto-link controls')

channel.atoms[1].set(['new/arriving-skill'])
channel.atoms[0].set('tools')
html = renderToString(workspace.render())
ok(html.includes('1 new skill(s) found') && html.includes('new/arriving-skill'), 'Tools renders the arrivals banner on the daily surface')
channel.atoms[1].set([])

const sdk = await import(new URL('./node_modules/@hermes/plugin-sdk/index.js', 'file://' + STAGING).href)
const realOpenWorkspace = sdk.host.openWorkspace
sdk.host.openWorkspace = undefined
channel.navigations.length = 0
open.data.run()
ok(channel.navigations.includes('/hermes-switchboard'), 'older hosts navigate to the route fallback')
html = renderToString(page.render())
ok(html.includes('Hermes Switchboard') && html.includes('aria-label="Tools"'), 'route fallback renders the same Control Center')
sdk.host.openWorkspace = realOpenWorkspace

ok((readFileSync(process.env.PLUGIN_SRC, 'utf8').match(/jsx\(BackgroundHost/g) || []).length === 2, 'both roots mount BackgroundHost')
ok((readFileSync(process.env.PLUGIN_SRC, 'utf8').match(/function useBackgroundSync/g) || []).length === 1, 'background effects have one shared hook')

const reactKeyWarnings = consoleErrors.concat(consoleWarnings).filter(message => /unique key|same key|key prop/i.test(message))
ok(reactKeyWarnings.length === 0, `workspace paths emit zero React key warnings${reactKeyWarnings.length ? `: ${reactKeyWarnings.join(' | ')}` : ''}`)
ok(consoleErrors.length === 0, `workspace paths emit zero console.error messages${consoleErrors.length ? `: ${consoleErrors.join(' | ')}` : ''}`)
ok(consoleWarnings.length === 0, `workspace paths emit zero console.warn messages${consoleWarnings.length ? `: ${consoleWarnings.join(' | ')}` : ''}`)
console.error = originalConsoleError
console.warn = originalConsoleWarn

console.log()
if (failures.length) {
  console.log(`WORKSPACE HARNESS FAILED — ${failures.length} problem(s)`)
  process.exit(1)
}
console.log('WORKSPACE HARNESS: ALL CHECKS PASSED')
