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

The separate Python 3.9 gate installs no dependencies. Optional HTTP tests and
MCP YAML interpretation tests are not executed there; the three-OS dependency
matrix exercises them. MCP catalog interpretation and whole-YAML backup restore
require PyYAML at runtime and must refuse with explicit parser-required errors
when it is unavailable. That refusal is tested instead of silently falling back
to a partial handwritten YAML parser.
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

## Recovery triage

Use this procedure when Loadout reports an interrupted change. It is a read-only
triage process, not permission to force a restore or delete a journal.

1. **Stop writers.** Do not retry Apply/Undo, move skill folders, or edit the
   affected client configuration while diagnosing it. A lost response does not
   establish whether the last write happened.
2. **Identify the right backend.** Record the plugin revision, Hermes version,
   active profile, operation type and visible item statuses. With a remote
   backend, inspect that machine's paths, not similarly named local files.
3. **Preserve evidence privately.** Keep `data/hermes-loadout/pending-operation.json`,
   `last-operation.json` if present, and every referenced original/backup. Work
   from private disposable copies; configurations can contain credentials.
4. **Classify each affected item.** Compare the current entry with the journal's
   before/after identity and backup references. Do not infer success from a
   filename alone. A `pending` item may have changed before the response or
   journal update was interrupted.
5. **Choose the case below.** Record what is known, what is uncertain and the
   intended final state before attempting any repair with maintainer guidance.

| Case | Inspect without changing originals |
|---|---|
| Activation interrupted | The specific skill link or MCP entry, its recorded before/after state, and any configuration backup. Do not restore an entire configuration merely to reverse one entry. |
| Import interrupted | Source copy, preserved original, canonical destination and Hermes disabled state. Check each separately; a visible destination does not prove all steps completed. |
| Undo interrupted | Which entries were restored, which remained unchanged, and which are uncertain. Do not apply the original operation again as a substitute for finishing undo. |
| Backup restore interrupted | The selected backup, replacement target and pre-restore original. Compare contents/identities before deciding which copy is authoritative. |
| Journal or backup unreadable/changed | Preserve everything. Resolve the read problem or seek private assistance; do not recreate a plausible record from memory. |

There is no general-purpose "mark recovered" endpoint or safe universal sequence
of shell commands for these cases. A maintainer-assisted reconciliation must
account for every affected item and retain originals before changing recovery
state. Restarting invalidates previews but does not resolve a persisted journal.
A warning disappearing by itself is not proof of a correct final configuration.

For assistance, share a sanitized description of the error, revision, operation
kind and statuses through the process in [Security](../SECURITY.md). Do not attach
raw journals or backup files to a public issue. Reproduce the case in isolated
fixtures before proposing a recovery change.

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
