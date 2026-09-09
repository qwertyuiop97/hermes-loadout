# QUESTIONS_FOR_HERMES.md

This file contains only unresolved decisions that meet the escalation threshold
in `AGENTS.md`. Resolved history lives in `DECISIONS.md`.

An entry here does not pause unrelated work. Hermes should first inspect the
plan, code, tests, and verified SDK/config behavior, then use configured bots or
independent review if that can resolve the issue. Contact the owner only when a
material choice remains unresolved.

## Deferred, non-blocking

- [ ] Q6: OpenCode MCP writer — the live `~/.config/opencode/opencode.jsonc`
  contains no real `mcp` entry to verify, and regenerating JSONC could destroy
  user comments. Keep OpenCode absent from the pane for now. Resume only with a
  populated real-world sample to mirror exactly, or explicit owner acceptance
  of a tested comment-preserving writer. This does not block items 6-13.
