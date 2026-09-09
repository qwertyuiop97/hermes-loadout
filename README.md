# Hermes Switchboard

[![tests](https://github.com/qwertyuiop97/hermes-switchboard/actions/workflows/tests.yml/badge.svg)](https://github.com/qwertyuiop97/hermes-switchboard/actions/workflows/tests.yml)

Manage which skills your local coding tools can see, from inside Hermes Desktop.
Keep one skill library in Hermes instead of maintaining a separate copy for
every agent. Review changes before applying them, see what succeeded, and undo
only the changes Switchboard actually made.

Switchboard is useful when you use several coding agents or maintain a large
skill collection. One agent with a few skills probably does not need it. This
is an alpha, not a claim that every listed client has been tested end to end.

![Switchboard's compact pane and tool-first workspace](docs/images/hermes-switchboard.png)

_Earlier alpha layout, using disposable test directories. The screenshot is not
the current client support list or a verification of this revision._

## How it works

Hermes stays the source of truth. Skills live at
`<hermes_home>/skills/<category>/<name>/SKILL.md`; the default home is
`~/.hermes`. Other tools receive symbolic links to those directories, not copies.
Disabling a consumer removes its managed link, not the original skill. The
Hermes switch instead updates that skill's membership in `skills.disabled`.

The compact **Skills** pane is a status summary and launcher. The workspace has
five sections: **Tools** for detected/configured clients, **Sets** for reusable
selections, **Problems** for broken links and conflicting copies, **MCP** for
supported server projections, and **Advanced** for imports, backups, watch
preferences, and machine blueprints. Manage one tool to get one switch per
skill. Bulk and category actions show a preview first.

The optional matrix appears only in wide layouts and includes every eligible
target with horizontal scrolling. It is not the default daily interface.

## Clients and scope

The maintained [client catalog](dashboard/client_catalog.json) records paths,
evidence, platform filters, format rules, and skill/MCP capabilities separately.
It drives detection and **Tools -> Add Tool**. The table below lists its documented
skill-path candidates, with links to the primary client documentation. Paths
are not a certification that every client, version, or operating system has
been tested. Detection means a candidate directory exists, not that an app is
installed or has loaded the skills.

<!-- client-catalog:start -->
| Client | Global path | Project path |
|---|---|---|
| [Shared Agent Skills](https://developers.openai.com/codex/skills/) | `~/.agents/skills` | `.agents/skills` |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills` | `.claude/skills` |
| [Codex](https://developers.openai.com/codex/skills/) | `~/.agents/skills` | `.agents/skills` |
| [OpenCode](https://opencode.ai/docs/skills/) | `${OPENCODE_CONFIG_DIR:-~/.config/opencode}/skills` | `.opencode/skills` |
| [Cursor](https://cursor.com/docs/skills) | `~/.cursor/skills` | `.cursor/skills` |
| [Cline](https://docs.cline.bot/customization/skills) | `~/.cline/skills` | `.cline/skills` |
| [Gemini CLI](https://geminicli.com/docs/cli/skills/) | `~/.gemini/skills` | `.gemini/skills` |
| [GitHub Copilot](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills) | `~/.copilot/skills` | `.github/skills` |
| [Kilo Code](https://kilo.ai/docs/customize/skills) | `~/.kilo/skills` | `.kilo/skills` |
| [Kiro](https://kiro.dev/docs/skills/) | `~/.kiro/skills` | `.kiro/skills` |
| [Amp](https://ampcode.com/docs/customize/skills) | `~/.config/agents/skills` | `.agents/skills` |
| [Mistral Vibe](https://docs.mistral.ai/vibe/code/cli/skills) | `~/.vibe/skills` | `.vibe/skills` |
| [Windsurf / Cascade](https://docs.devin.ai/desktop/cascade/skills) | `~/.codeium/windsurf/skills` | `.windsurf/skills` |
| [OpenClaw](https://docs.openclaw.ai/tools/skills) | `~/.openclaw/skills` | Not offered |
<!-- client-catalog:end -->

Hermes itself uses the active canonical library, not an additional target.
Claude Desktop has an MCP writer but no catalog skill-directory target; it is
not the same client as Claude Code.

**Global** uses the documented location in your user account. **Project** uses
an existing project folder you explicitly select. Switchboard checks only the
supported relative paths inside that folder; it never searches your computer
for repositories or infers a root from the process working directory. A saved
project mapping is rechecked before use. Moved, missing, or redirected paths
stay visible with writes disabled until corrected.

Global paths are constrained to the user's home. Project paths cannot escape
the selected root through a symlink or overlap the canonical library. A
nonstandard directory, including an OpenCode configuration outside the home,
requires an explicit **Custom** mapping instead of silently relaxing that scope.

Important client differences:

- Codex's documented path is now `.agents/skills`. Existing `.codex/skills`
  mappings are preserved, never silently moved. A shared `.agents/skills`
  directory can be read by several clients. Changing it affects all readers;
  Switchboard will not create independent controls for the same target path.
- Native skill trust, precedence, validation, and permission rules still apply.
  For example, Gemini also reads `.agents/skills`, Vibe requires a trusted
  project, and Kiro CLI custom agents may need `skill://` resources. A switch
  controls a path, not every way an agent can discover or execute a skill.
- OpenClaw is offered at its global path only. Switchboard does not change its
  external-link trust settings to make project links work. Local links into a
  Hermes home are not portable to cloud agents, teammates, or another machine.

Grok, ZCode, Kimi, pi, and Roo Code are **unverified candidates**, not advertised
built-in skill integrations. Explicit existing custom mappings are preserved.
Legacy unverified directories may remain visible read-only; a label-only
setting does not grant write access. Use **Custom** only after checking the
client's skill contract in disposable directories. New custom IDs use 1 to 32
lowercase letters, digits, or hyphens; existing longer IDs remain loadable.

## Add a tool and import skills

Open **Tools -> Add Tool** to search **Detected**, **Available**, and **Custom**
entries. Choose Global or Project. For Project, enter an existing absolute root
and choose **Review project**. Check the resolved path and any shared-folder
warning before **Use path**. Adding a target saves a mapping only: it does not
install the client, create its skills directory, or enable any skills. A later
explicit skill operation may create the reviewed target directory.

The first-run scan preselects detected clients and labels their scope. You can
add another read-only scan folder, review copies, duplicates, drift, and
protected entries, then select the safe imports and confirm the dry run. Extra
scan folders never become canonical stores. An empty scan changes nothing and
offers another scan or a return to Tools. Originals moved during a reviewed
import are kept as backups, with per-item restore information in the receipt.

Documented Agent Skills targets require a matching, lowercase hyphenated skill
name, valid frontmatter, and a nonempty description. Unsupported formats,
reserved names, and ambiguous duplicate names are refused rather than rewritten.
Legacy/custom mappings retain their existing compatibility behavior.

## Safety, receipts, and recovery

Normal toggles protect real directories and foreign links, including dangling
foreign links. They do not overwrite a protected entry merely because it has a
matching name. Explicit import/conflict operations explain the move and backup
before replacing a reviewed copy. A failed link repair preserves the prior link.

Bulk previews use explicit skill IDs; apply does not silently rescan a broader
selection. Files are rechecked before mutation. Configuration changes back up
the original and use same-directory temporary writes before replacement.
Malformed JSON/TOML and unsupported configuration shapes fail closed.

Bulk receipts are persisted under
`<hermes_home>/data/hermes-switchboard/receipts/`. They record successful changes
and exact before/after states. The latest receipt can be reloaded after reopening
the workspace. **Undo** uses that saved evidence, not an inverse "toggle all":
it skips external changes, preserves unrelated configuration edits, and refuses
a receipt whose target has been redirected. Repeating a completed undo does not
apply it twice. Import receipts use their separate per-item restore actions.

If the receipt store is unwritable, bulk changes are refused before writing.
A failure while saving the final receipt is shown explicitly and automatic undo
is disabled. Preserve the recovery record and inspect the affected paths; do not
assume a missing final receipt means nothing changed.

Windows directory-link replacement uses a rename-aside and rollback sequence,
not a single atomic replacement. An interrupted operation can leave a hidden
`.hermes-switchboard-previous-*` recovery link. Do not delete it until its target
and the current entry have been inspected. Creating links on Windows may require
Developer Mode or appropriate privileges.

UI state and mutation logs redact secret-bearing MCP values, including arguments,
URLs, environment values, and headers. **Original configuration backups can still
contain credentials** and are not encrypted by Switchboard. Paths and server
names can also be sensitive. Do not publish raw configurations, backups, receipts,
or logs without reviewing them. Linking a skill does not audit any code it contains.

## Install and update

Use a Hermes Desktop version that supports unified plugins and the
[desktop plugin SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk).
The repository currently requires authorized GitHub access; no new release is
tagged by this change. Review a revision before installing it into a real profile.

For the default profile, clone into the plugins directory and enable the backend:

```bash
git clone https://github.com/qwertyuiop97/hermes-switchboard.git \
  ~/.hermes/plugins/hermes-switchboard
hermes plugins enable hermes-switchboard
```

Alternatively, use `hermes plugins install qwertyuiop97/hermes-switchboard`, then
enable the backend. The CLI can pin a full immutable commit with `--ref` as
explained in the [Hermes plugin guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins).
These commands install the default branch unless you explicitly select a revision;
unmerged pull-request changes are not included automatically.

In Hermes, enable **Hermes Switchboard** in **Settings -> Plugins**, fully quit
and reopen the app, then open the Skills pane and choose **Open Switchboard**.
Both the backend and desktop interface are opt-in. Reloading desktop plugins
alone does not reload Python routes or manifests.

For a Git clone, update only after reviewing the changes:

```bash
git -C ~/.hermes/plugins/hermes-switchboard pull --ff-only
```

Keep local edits and pinned revisions in mind; do not force an update over them.
Fully restart after backend/manifest changes. **Reload desktop plugins** suffices
only for an isolated `desktop/plugin.js` update. There is no frontend build step.

`$HERMES_HOME` takes precedence over `$HERMES_PROFILE`, then `~/.hermes` is the
default. For a named profile, use that profile's `plugins/hermes-switchboard`
directory and enable the plugin in the same profile. The paths shown in the UI
belong to the active backend's filesystem, which may not be the desktop machine
when a remote Hermes backend is in use.

## Configuration and migration

Settings live outside the plugin folder, at
`<hermes_home>/hermes-switchboard.json`. If that file is absent, Switchboard reads
the old `skills-toggle.json` filename. Merely reading settings never migrates
or rewrites them. An explicit target save writes schema version 2, backs up the
previous configuration, retains unrelated fields and mappings, and leaves the
legacy original intact.

Existing string paths, dictionary mappings, label-only default overrides, and
long existing client IDs remain compatible. Label-only overrides retain the old
default path, including Codex's `.codex/skills`; new documented targets use the
current catalog. Invalid or future-schema settings are not replaced with defaults.
Correct the file or restore a known-good backup, then Retry or restart the backend.

For a manually reviewed custom directory:

```json
{
  "schema_version": 2,
  "tools": {
    "my-client": {
      "label": "My client",
      "dir": "~/my-client-skills",
      "scope": "custom"
    }
  }
}
```

Paths support `~` and `${VAR:-default}` expansion. Custom paths still cannot
overlap the canonical skill tree. Prefer Add Tool for Global/Project targets:
it stores the client ID, scope root, canonical target, and candidate index needed
for revalidation. Do not remove those fields to bypass a path error.

## MCP connections

Hermes `config.yaml`'s `mcp_servers` is the source. Skill support does not imply
an MCP writer. Switchboard writes only:

| Client | Supported projection |
|---|---|
| Claude Desktop | Local stdio servers using `command`, `args`, and `env` in `claude_desktop_config.json` |
| Codex | Local stdio or supported HTTP URL servers in `config.toml`, including header translation |

Existing client-specific policies, disabled flags, and unrelated server settings
are preserved. Drifted definitions require explicit overwrite confirmation;
servers found only in a client remain untouched. A malformed writer is disabled
without disabling the other client. Partial success and refusals remain visible.

Not every Hermes MCP field or transport is portable. Unsupported combinations
are refused instead of guessed. There is no OpenCode JSONC writer, account sync,
or automatic OAuth setup. Client restarts/reloads and native trust settings may
still be needed for projected servers to become usable.

## Troubleshooting

**Backend unavailable, 404/405, or version mismatch:** check that the agent plugin
is enabled, fully quit and reopen Hermes, then Retry. New JavaScript with old
Python still in memory cannot safely use the new APIs.

**A project is visible but disabled:** restore the original project location or
review a new mapping. Do not manually strip scope metadata. A redirected symlink
or missing root is not permission to create a different project.

**A switch or bulk action was refused:** inspect the reason in Problems or the
receipt. Real directories, foreign links, conflicting names, and format errors
need explicit review; repeated clicks will not override the protection.

**Skills are linked but the client cannot see them:** check that client's own
reload, trust, format, and path-precedence rules. A local symlink does not make
the same file available to a remote/cloud agent.

## Development and validation

The core supports Python 3.9+ without installing runtime dependencies. Its pinned
TOML reader is bundled with attribution in [dashboard/_vendor](dashboard/_vendor/README.md).
FastAPI and httpx are needed to run HTTP tests rather than skip them. The frontend
harness uses Node.js and Bash with pinned React test dependencies installed on
first use; it does not build the plugin.

```bash
python -m pip install fastapi httpx
python -m unittest discover -s tests -p 'test_*.py' -v
python tests/check_frontend.py
./tests/run_render_harness.sh
```

CI runs the full Python/HTTP suite on Ubuntu, macOS, and Windows, a Python 3.9
core gate without third-party installations, and React render/interaction checks.
Regressions cover protected entries, exact bulk/undo receipts, failed optimistic
mutations, malformed settings, project redirection, legacy migration, native MCP
policy, secret redaction, and narrow/wide UI behavior. Package tests keep the
README path table aligned with the catalog.

The React harness uses a stubbed Hermes SDK. When the host source is absent,
static checks warn that SDK exports were not cross-checked. Passing CI is not
native desktop verification, client certification, or outside-user validation.
Use disposable profiles for native installation, keyboard/focus, reload, and
client-discovery checks before relying on a new revision.

## Uninstall

First use reviewed disable/remove actions for any skill links or MCP projections
you no longer want. Then disable the backend:

```bash
hermes plugins disable hermes-switchboard
```

Disable the desktop half in **Settings -> Plugins**, fully quit Hermes, and remove
only this plugin's installation folder, or use `hermes plugins remove
hermes-switchboard` for a CLI-managed installation. Confirm the active profile's
path before deleting anything.

Uninstalling does not undo earlier operations. The canonical skill library,
client links, projected MCP entries, custom settings, backups, and receipts remain
unless you remove them separately. Do not delete the Hermes skill library merely
to remove this plugin. You can reinstall to use the preserved receipts and
reviewed removal actions.

## License

MIT. See [LICENSE](LICENSE). The bundled TOML reader retains its own
[MIT license](dashboard/_vendor/tomli/LICENSE).
