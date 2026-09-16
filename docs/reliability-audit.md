# Reliability and usability audit

Reviewed source: `55d1261c013957835372dc68d1ecc91089505fee` (main).
Review date: September 16, 2026.

## Scope and priorities

The useful core is already present: one skill library, independent reviewed
activation, supported MCP writers, saved loadouts, protected external files,
and persisted undo/recovery. This pass improves those workflows rather than
adding unverified clients or automatic capability activation.

The implementation plan was to protect saved work first, make capture complete
and explicit about failures, improve large-library review and editing, then
cover the changes with backend, HTTP, and React regression tests. No schema
migration, provider configuration, new service, or automatic activation is added.

## Implemented findings

| Finding | Change | Regression evidence |
|---|---|---|
| The record reader rejected files larger than 4 MiB, but the writer could create them from valid loadouts, making subsequent reads fail. | Apply the same UTF-8 byte limit before creating or replacing a record. Preserve the previous store and pending recovery evidence on refusal. | Byte-boundary and multibyte tests, rejected-save preservation, and a sequence of valid large loadouts hitting the real limit. |
| Capture treated an unreadable MCP catalog as an empty catalog. A successful result could replace the draft with incomplete choices. | Refuse the entire capture when its selected catalog/writer is unreadable. Keep known unsupported entries in the explicit exclusion list. Read MCP once per capture and do not consult it for skill-only clients. | Malformed/unreadable catalog and writer tests, HTTP refusal checks, and a React test that preserves unsaved work on capture failure. |
| An 80-skill named review rescanned the whole library 320 times. | Share one library snapshot within the read-only review request. Apply and undo still perform fresh filesystem validation. | An 80-selection regression asserts one scan, plus duplicate-name, external-replacement, and fresh-next-review tests. This is a scan-count improvement, not a claim of 320 times faster end-to-end operation. |
| With three aliases of a shared folder, an opposing choice could be marked unchanged while its peers conflicted. | Resolve the complete physical-target group before classifying it. Every opposing alias is a conflict; agreeing aliases still apply and undo once. | All six orderings of a three-alias conflict, and consistent-alias apply/undo. |
| A save could succeed on the backend and be presented as failed when a subsequent list refresh failed. A retry could create a duplicate. | Use the acknowledged mutation response to update the cache and editor identity. Cancel older list reads before the mutation; a second GET is no longer required to establish success. | React scenarios for save/retry, duplicate, and delete when list reads fail, alongside genuine save-failure and dirty-navigation tests. |
| Malformed plugin-local drafts could crash the editor or associate a recovered draft with the wrong saved record. | Validate restored draft identity, shape, capability choices, and application list. Fall back to the saved record with a notice. Valid unsaved work, including an intentionally empty application selection, still restores. | Invalid draft variations, editor remount fallback, valid recovery, and unchanged backend records. |
| Only the first 100 capability matches were browseable without knowing a search term. | Add incremental Show more capabilities, resetting the visible window on search/application changes without losing selections. | Browse a 205-skill fixture, select the final skill, filter and reset, then save the off-screen choice. |
| The dependency-free Python 3.9 job expected a successful YAML catalog even without PyYAML. | Check native default-path resolution independently, and explicitly verify parser-required refusal when PyYAML is absent. The same isolated subprocess probe also runs with YAML import blocked. | Full dependency-free suite, without skipping the path-isolation test or installing the optional parser in that job. |

## Validation and boundaries

All configuration tests use disposable fixtures, never a live Hermes profile.
Run the gates described in [development.md](development.md). The pull request's
checks are the evidence for the exact committed revision and operating systems;
local Linux tests alone are not cross-platform or native Desktop acceptance.

No preview cache is reused by Apply or Undo. Imports, writer definitions, backup
formats, private-file permissions, one-shot previews, and the recovery journal
contract are unchanged. Capture failures now intentionally return `ok: false`
without `states`, instead of a successful but incomplete capture. Known
unsupported individual capabilities still appear as exclusions.

An acknowledged save is handled safely; a connection lost before any POST
acknowledgement still has an uncertain outcome. Inspect saved loadouts before
retrying a new save in that case. No end-to-end idempotency protocol is claimed.

## Next release priorities

1. **Native compatibility evidence.** Exercise the official Desktop SDK and real
   application discovery in a disposable profile on the advertised platforms.
   Record a tested minimum Desktop revision. Include actual dialog focus,
   reconnect/restart, remote backend, and profile switching, not just SDK exports.
2. **Profile/source isolation.** Audit plugin-local drafts and query keys during
   real profile and gateway switches. Bind UI state to a verified host-provided
   source identity before changing persistence keys; a guessed local path is not
   a reliable remote-backend identity.
3. **Broader configuration and recovery acceptance.** Add real-client fixtures
   for quoted/flow YAML, disabled-skill edits, and interrupted multi-file
   operations. Keep unrecognized syntax read-only until semantic preservation
   is proven. Do not make recovery warnings disappear by discarding evidence.
4. **Scale and maintainability.** Measure representative large libraries and MCP
   catalogs. Split the large frontend/backend files along existing adapter and
   UI boundaries only after behavior is covered. Preserve the no-build plugin
   entry and keep speculative client writers out of the support table.

These are follow-up acceptance and engineering priorities, not features claimed
as implemented or validated in this patch. No release is published by this audit.
