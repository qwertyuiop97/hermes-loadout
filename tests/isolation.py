"""Test-only path ownership and isolated user environments, with real file I/O."""
from __future__ import annotations

import functools
import inspect
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_ROOTS = set()


@contextmanager
def disposable_root():
    """Only directories allocated here may own configuration-writer fixtures."""
    with tempfile.TemporaryDirectory(prefix='loadout-isolated-') as name:
        root = Path(name).resolve()
        _ROOTS.add(root)
        try:
            yield root
        finally:
            _ROOTS.remove(root)


def _owned_path(value, field, root=None):
    if not isinstance(value, (str, Path)) or not str(value) or not Path(value).is_absolute():
        raise AssertionError(f'{field} requires an explicit absolute disposable path')
    path = Path(value)
    candidates = (root,) if root is not None else tuple(_ROOTS)
    for candidate in candidates:
        # Check lexical containment before resolving links. Never probe a real
        # configuration merely to decide whether it is safe for a test.
        if candidate == path or candidate in path.parents:
            resolved = path.resolve()
            if resolved == candidate or candidate in resolved.parents:
                return candidate
    raise AssertionError(f'{field} is outside the owning disposable fixture')


def guard_mcp_construction(api):
    """Refuse missing/ambient paths BEFORE the production constructor runs.

    This does not replace readers, writers, discovery, or foreign-server logic.
    All constructor parameters other than optional logging are configuration
    paths today. A future client parameter therefore also needs an explicit path.
    """
    original = api.McpCore.__init__
    signature = inspect.signature(original)
    required = set(signature.parameters) - {'self', 'log_path'}

    @functools.wraps(original)
    def checked(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs).arguments
        root = _owned_path(bound.get('home'), 'home')
        for name in sorted(required - {'home'}):
            _owned_path(bound.get(name), name, root)
        if bound.get('log_path') is not None:
            _owned_path(bound['log_path'], 'log_path', root)
        original(self, *args, **kwargs)

    api.McpCore.__init__ = checked


def user_environment(user, hermes=None):
    """Redirect every supported home/config variable, including Windows APPDATA."""
    root = _owned_path(user, 'user home')
    user = Path(user)
    hermes = Path(hermes) if hermes is not None else user / '.hermes'
    _owned_path(hermes, 'Hermes home', root)
    env = dict(os.environ)
    env.pop('HERMES_PROFILE', None)
    env.update({
        'HOME': str(user), 'USERPROFILE': str(user),
        'HOMEDRIVE': user.drive, 'HOMEPATH': str(user)[len(user.drive):],
        'HERMES_HOME': str(hermes), 'CODEX_HOME': str(user / '.codex'),
        'CLAUDE_CONFIG_DIR': str(user / '.claude'),
        'APPDATA': str(user / 'AppData' / 'Roaming'),
        'LOCALAPPDATA': str(user / 'AppData' / 'Local'),
        'XDG_CONFIG_HOME': str(user / '.config'),
        'OPENCODE_CONFIG_DIR': str(user / '.config' / 'opencode'),
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    return env


@contextmanager
def isolated_user_home(user, hermes=None):
    user = Path(user)
    env = user_environment(user, hermes)
    user.mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, env, clear=True), patch.object(Path, 'home', return_value=user):
        yield user


def run_isolated_python(source, user, hermes=None, timeout=60):
    """Default-path probes must run in a fresh process with a disposable home."""
    env = user_environment(user, hermes)
    Path(user).mkdir(parents=True, exist_ok=True)
    return subprocess.run([sys.executable, '-c', source], cwd=user, env=env,
                          capture_output=True, text=True, timeout=timeout)


@contextmanager
def bind_test_cores(api, core, mcp, frozen=True):
    """Inject both adapters without constructing a throwaway default-path MCP.

    Restore all singleton state, including the service, even when a test fails.
    Unfrozen mode retains actual HTTP skill-config reload behavior.
    """
    root = _owned_path(core.home, 'home')
    for name in ('home', 'config_path', 'claude_config', 'codex_config'):
        _owned_path(getattr(mcp, name), name, root)
    # Mutations may call reset_core(), so frozen singletons alone are not an
    # isolation boundary. A later real rebuild must still resolve this fixture.
    with isolated_user_home(root / 'user', core.home), \
            patch.multiple(api, _CORE=core, _CORE_SIG=('test', id(core)),
                           _CORE_FROZEN=frozen, _MCP_CORE=mcp, _LOADOUT_SERVICE=None):
        yield
