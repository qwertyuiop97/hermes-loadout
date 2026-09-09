---
name: Loadout for Hermes feedback
about: Report a bug, confusing interface, or unexpected filesystem change
title: "[alpha] "
labels: ["alpha-feedback"]
---

Describe the action and its result. Do not attach raw configuration files or
backups: they may contain credentials. Redact tokens, command arguments, URLs,
headers, environment values, personal paths, and sensitive server names from
receipts, logs, and screenshots.

## What you were trying to do

- [ ] Add Tool / client detection
- [ ] Global / Project / Custom path setup
- [ ] First-run scan / import
- [ ] Manage one tool (enable / disable / category / selected)
- [ ] Bulk preview / confirm / persisted undo
- [ ] Problems (repair or drift)
- [ ] MCP (Claude Desktop or Codex)
- [ ] Configuration migration / backend version mismatch
- [ ] Advanced (blueprint / backups / watch / auto-link)
- [ ] Compact pane / workspace / matrix
- [ ] Something else:

## What happened

Include the exact error code or refusal reason, with sensitive details removed.

## What you expected

## Setup and reproduction

Hermes version / OS:
Loadout commit or version:
Client and version:
Scope (Global / Project / Custom):
Local or remote Hermes backend:
Reproduction steps in disposable directories:

- [ ] Compact pane (about 320 px)
- [ ] Split / medium workspace
- [ ] Wide workspace / matrix

## Safety

Did anything get overwritten, deleted, or moved that was not in the preview?
Did a protected foreign link or real directory change?
Was a project or target path moved or redirected after setup?
Did the issue occur during Apply, Undo, or after a restart/interruption?

## Receipt and recovery

Receipt ID, operation status, and redacted error:
Were a backup or recovery record created?
Did Retry or a full Hermes restart change the result?

Preserve backups and recovery records locally. Do not delete them to reproduce
a failure, and do not post their full contents here.
