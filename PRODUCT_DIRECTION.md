# Product direction: one active product, two existing shells

Status: current direction for stabilization, 2026-09-08.

## Decision

`skills-toggle` is the active product. It is a Hermes-native plugin for people
who use Hermes as their canonical skills library and want Hermes to distribute
skills and MCP configuration to their other AI tools.

`/Users/bakirmousa/projects/skills-dash` is a parked standalone prototype. It
implements much of the same product in a Tauri application that can run while
Hermes is closed. Preserve it, its tests, and its research, but do not develop
or release it in parallel during skills-toggle stabilization.

## Why

The two repositories currently overlap on skill inventory, per-tool toggles,
bulk operations, presets, imports, drift handling, MCP management, and safety
rules. Their meaningful distinction is delivery shell:

- skills-toggle runs inside Hermes and uses the Hermes desktop SDK/gateway;
- Skills Dash is a separate desktop application with a Rust/Python bridge.

That distinction does not justify maintaining two full implementations before
the core Hermes product is usable and validated. Skills Dash also vendors an
older snapshot of the skills-toggle Python core, creating a second safety and
compatibility surface that can drift.

## How to use Skills Dash during stabilization

Treat it as read-only reference material. Inspect its full-window information
architecture, import wizard, bulk controls, MCP writers, menu-bar work, tests,
and research for useful behavior. Port ideas only after checking them against
the current skills-toggle core and Hermes SDK. Do not copy its vanilla-JS UI or
vendored core wholesale into the plugin.

Any generally useful filesystem or configuration fix should land in the active
skills-toggle core first with tests. Do not attempt shared-package extraction
as part of the current stabilization; that is infrastructure work without user
validation.

## Reconsideration gate

Reconsider a standalone release only after the skills-toggle alpha passes its
release gates and evidence shows that users materially need one of these:

- management while Hermes is closed or not installed;
- a system-wide menu-bar/global shortcut independent of Hermes;
- deployment outside the Hermes user base.

If that evidence appears, decide whether Skills Dash should become a thin shell
over a shared versioned core. Until then, it remains parked and preserved.
