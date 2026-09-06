"""skills-toggle — agent half of a unified Hermes plugin package.

The agent half is intentionally inert: the feature's surface is the desktop
pane (desktop/plugin.js) and its backend routes (dashboard/plugin_api.py,
mounted at /api/plugins/skills-toggle/ once the plugin is in
`plugins.enabled`). register() is a no-op — no tools, hooks, or commands are
added to the agent loop; this plugin is a user-facing control pane, not an
agent capability.

MIT License — see LICENSE at the package root.
"""


def register(ctx) -> None:
    """No-op registration. Required by the plugin loader contract."""
    return None
