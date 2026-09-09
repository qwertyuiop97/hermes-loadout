# Development and validation

## Structure

`dashboard/client_catalog.json` owns client path evidence and capabilities.
`dashboard/plugin_api.py` provides filesystem adapters and the plugin router.
`dashboard/loadout_service.py` owns named selections, server-held reviews, the
latest operation, and safe undo. `dashboard/toml_document.py` preserves native
TOML statements using the pinned, attributed reader under `_vendor/`.
`desktop/plugin.js` is the single no-build ESM desktop entry. It may import only
`@hermes/plugin-sdk`, `react`, and `react/jsx-runtime`.

Use a disposable Hermes home and client directories. Do not test mutation flows
against a developer's real skill tree. The test fixtures create temporary roots.

## Gates

For Python 3.12+ HTTP tests, create a virtual environment, then:

```bash
python -m pip install -r tests/requirements-http.txt
python -m unittest discover -s tests -p 'test_*.py' -v
python tests/check_frontend.py
./tests/run_render_harness.sh
```

Configuration-writer tests must allocate an owned `disposable_root` from
`tests/isolation.py`. Supply explicit absolute Hermes, Claude, and Codex paths
to every `McpCore`, even when a test only exercises one client or backups. The
test loader rejects missing, unresolved, or out-of-fixture paths before the real
constructor runs. A source contract also checks constructor calls and aliases.
Foreign-server detection still reads real disposable JSON/TOML files.

Use `isolated_user_home` for catalog discovery and home-relative path tests. It
redirects HOME, USERPROFILE, APPDATA, client config variables, and Hermes profile
selection, then restores the environment. Inject HTTP adapters with
`bind_test_cores`; do not construct default adapters merely to replace them.
Intentional default-path construction belongs in a fresh `run_isolated_python`
subprocess, with every candidate inside its disposable home. The ambient-config
regression seeds a separate fake home and verifies that it neither influences
the fixture nor receives writes.

The separate Python 3.9 gate installs no dependencies. Optional HTTP tests are
not executed there; the three-OS HTTP matrix exercises them. Whole-YAML backup
restore must refuse without PyYAML, and this refusal is tested instead of skipped.
The shipped plugin has no build step or declared type-check pipeline. Python
syntax/import tests, ESM syntax, SDK import constraints, and focused behavior
tests are the applicable gates, not a fictional successful production build.

CI checks out the exact PR head and tests Ubuntu, macOS, and Windows.
Action revisions and test dependencies are
pinned. The frontend job also checks all imported SDK names against the official
Hermes source at `d9e64e916500fca93920b2fae31b362628f042e6`; it fails if the
source or an export is missing. Local checks use an explicit `--sdk-index` or
`HERMES_SDK_INDEX`, never an implicit checkout in the developer's home.
This checks exports, not component props or
runtime behavior. To run it against an explicit host checkout:

```bash
python tests/check_frontend.py --require-sdk --sdk-index /path/to/hermes-agent/apps/desktop/src/sdk/index.ts
```

React harnesses use real React and an SDK substitute. They cover rendering,
container-responsive navigation, keyboard names, review cancellation, errors,
partial receipts, undo, immutable import selection, the client library, and
loadout editing. They do not replace a disposable native Hermes session.

## State and recovery

`hermes-loadout.json` stores targets. The plugin's `data/hermes-loadout/` folder
stores `loadouts.json`, `inventory-metadata.json`, `last-operation.json`, and,
only while needed, `pending-operation.json`. Reviews are in-memory, expire after
ten minutes, and are bound to exact prior state and selected contents. Restarting
the backend invalidates an unapplied preview but not persisted recovery evidence.

A loadout contains only `{kind, app, id, enabled}` selections. A missing selection
means unchanged. Never put server definitions or credentials into it. Receipts
use content hashes, state identities, and backup references. The referenced
original configuration backups can contain secrets and must not be attached to
public issues. New successful operations replace the single undo point.

Restore validates every relevant current target and preserved original before
writing. A partial or interrupted restore must retain its journal and originals,
not erase the warning. Recover it manually after inspecting all referenced
paths; there is intentionally no “discard evidence and retry” button.

## Adding a client

Add a documented candidate to the JSON catalog, including a primary-source URL,
checked date, scopes, platforms, format, creation policy, and limitations.
Update the README table and run catalog contracts. Do not make absence of evidence
mean support. Shared paths must be disclosed and deduplicated. An MCP writer
requires separately tested native parsing and preservation; a skill path alone
is not enough.

## Native acceptance

Before relying on a revision, test it in a disposable profile with the installed
Hermes version: plugin loading, backend restart, narrow/wide layouts, actual tab
focus and dialog focus restoration, Global/Project setup, new-session client
skill discovery, import without activation, and reopening/undo recovery.
A screenshot of an SDK-fixture render is labeled as such, never as native proof.
