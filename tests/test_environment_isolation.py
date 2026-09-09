"""Configuration fixtures cannot consult the developer's ambient applications."""
from __future__ import annotations

import ast
import inspect
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation import disposable_root, isolated_user_home, run_isolated_python, bind_test_cores
from test_plugin_api import Fixture, pa
from test_mcp_backend import McpFixture

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationIsolationTests(unittest.TestCase):
    def test_ambient_configs_cannot_influence_fixture_or_receive_writes(self):
        # This home is deliberately OUTSIDE McpFixture's own temporary tree.
        # Use ordinary production parsers and writers, not mocked server lists.
        with disposable_root() as ambient, isolated_user_home(ambient / 'user') as user:
            hermes = user / '.hermes/config.yaml'
            codex = user / '.codex/config.toml'
            hermes.parent.mkdir(parents=True)
            hermes.write_text('mcp_servers:\n  ambient-hermes:\n    command: never-run\n', encoding='utf-8')
            codex.parent.mkdir(parents=True)
            codex.write_text('[mcp_servers.ambient-codex]\ncommand="never-run"\n', encoding='utf-8')
            # Cover every Claude candidate, including APPDATA on non-Windows.
            for candidate in pa.CLAUDE_DESKTOP_CONFIG_CANDIDATES:
                path = pa.expand_path(candidate)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'mcpServers': {'ambient-claude': {'command': 'never-run'}}}), encoding='utf-8')
            before = {str(path.relative_to(ambient)): path.read_bytes()
                      for path in ambient.rglob('*') if path.is_file()}
            entries = sorted(str(path.relative_to(ambient)) for path in ambient.rglob('*'))
            fixture = McpFixture()
            try:
                mcp = fixture.mcp
                for path in (mcp.home, mcp.claude_config, mcp.codex_config):
                    self.assertTrue(pa.is_inside(path, fixture.tmp))
                    self.assertFalse(pa.is_inside(path, ambient))
                state = mcp.mcp_state()
                self.assertEqual(sorted(row['name'] for row in state['rows']),
                                 ['chrome-devtools', 'docs', 'weather'])
                self.assertEqual([row['name'] for row in state['foreign']], ['drifted-server'])
                # Foreign entries INSIDE the fixture must still be discovered.
                fixture.codex_config.parent.mkdir(parents=True)
                fixture.codex_config.write_text('[mcp_servers.fixture-foreign]\ncommand="keep"\n', encoding='utf-8')
                self.assertEqual(sorted(row['name'] for row in mcp.mcp_state()['foreign']),
                                 ['drifted-server', 'fixture-foreign'])
                mcp.sync_to_codex('weather')
                mcp.sync_to_claude('weather')
                mcp.toggle_hermes('weather', True)
                self.assertIn('fixture-foreign', pa.parse_codex_mcp(fixture.codex_config.read_text()))
                self.assertEqual(entries, sorted(str(path.relative_to(ambient)) for path in ambient.rglob('*')))
                self.assertEqual(before, {str(path.relative_to(ambient)): path.read_bytes()
                                         for path in ambient.rglob('*') if path.is_file()})
            finally:
                fixture.cleanup()

    def test_constructor_guard_rejects_missing_none_relative_and_external_paths(self):
        fixture = Fixture()
        self.addCleanup(fixture.cleanup)
        construct = pa.McpCore
        valid = dict(home=fixture.home, claude_desktop_config=fixture.tmp / 'claude.json',
                     codex_config=fixture.tmp / 'codex.toml')
        # An existing disposable but independently owned tree is also forbidden.
        with disposable_root() as outside:
            for field in valid:
                for value in (None, '', Path('relative'), '~/config', outside / 'config'):
                    with self.subTest(field=field, value=str(value)), self.assertRaises(AssertionError):
                        construct(**dict(valid, **{field: value}))
                omitted = dict(valid)
                del omitted[field]
                with self.subTest(omitted=field), self.assertRaises((AssertionError, TypeError)):
                    construct(**omitted)
            with self.assertRaises(AssertionError):
                construct(**valid, log_path=outside / 'log')
        # Validate aliases and positional calls too. These use real adapters.
        core = construct(fixture.home, valid['claude_desktop_config'], None, valid['codex_config'])
        self.assertEqual(core.codex_config, valid['codex_config'])
        self.assertFalse(core.codex_config.exists())

    def test_guard_rejects_a_client_parent_redirected_outside_its_fixture(self):
        fixture = Fixture()
        self.addCleanup(fixture.cleanup)
        construct = pa.McpCore
        with disposable_root() as outside:
            redirected = fixture.tmp / 'redirected'
            redirected.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(AssertionError):
                construct(home=fixture.home, claude_desktop_config=fixture.tmp / 'claude.json',
                          codex_config=redirected / 'config.toml')
            self.assertEqual(list(outside.iterdir()), [])

    def test_guard_prevents_default_discovery_before_it_can_read(self):
        fixture = Fixture()
        self.addCleanup(fixture.cleanup)
        construct = pa.McpCore
        with patch.object(pa.McpCore, '_default_codex_config') as codex_default, \
                patch.object(pa.McpCore, '_default_claude_config') as claude_default:
            with self.assertRaises(AssertionError):
                construct(fixture.home)
            codex_default.assert_not_called()
            claude_default.assert_not_called()

    def test_test_sources_supply_every_configuration_constructor_parameter(self):
        # Runtime ownership guards cover values. This static gate also covers
        # tests not executed on the current OS, and common constructor aliases.
        signature = inspect.signature(pa.McpCore.__init__)
        required = set(signature.parameters) - {'self', 'log_path'}
        violations = []
        for path in sorted((ROOT / 'tests').glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            aliases = {'McpCore'}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    aliases.update(item.asname or item.name for item in node.names if item.name == 'McpCore')
                if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                    name = node.value.id if isinstance(node.value, ast.Name) else node.value.attr
                    if name == 'McpCore':
                        aliases.update(target.id for target in node.targets if isinstance(target, ast.Name))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, (ast.Name, ast.Attribute)):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                if name == 'set_core_for_testing':
                    violations.append(f'{path.name}:{node.lineno}: inject both adapters with bind_test_cores')
                if name not in aliases:
                    continue
                # Calls in this test file deliberately exercise the guard via
                # its local `construct` alias. They may not call defaults.
                if path == Path(__file__) and name == 'construct':
                    continue
                try:
                    bound = signature.bind_partial(None, *node.args,
                                                   **{kw.arg: kw.value for kw in node.keywords if kw.arg})
                    missing = [key for key in required if key not in bound.arguments or
                               isinstance(bound.arguments[key], ast.Constant) and bound.arguments[key].value is None]
                    if missing or any(kw.arg is None for kw in node.keywords):
                        violations.append(f'{path.name}:{node.lineno}: explicit paths required for {sorted(missing)}')
                except TypeError as error:
                    violations.append(f'{path.name}:{node.lineno}: {error}')
        self.assertEqual(violations, [])

    def test_default_resolution_in_a_safely_isolated_subprocess(self):
        # The only intentional default-path McpCore construction. The fresh
        # interpreter never inherits real home/profile/APPDATA path settings.
        probe = """
import importlib.util
import json
import os
from pathlib import Path
spec = importlib.util.spec_from_file_location('default_path_probe', MODULE)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
user = Path.home()
home = api.hermes_home()
home.mkdir(parents=True)
(home / 'config.yaml').write_text('mcp_servers:\\n  fixture-home:\\n    command: fixture\\n', encoding='utf-8')
for path in [api.expand_path(p) for p in api.CLAUDE_DESKTOP_CONFIG_CANDIDATES + api.CODEX_CONFIG_CANDIDATES]:
    assert api.is_inside(path, user)
for candidate in api.CLAUDE_DESKTOP_CONFIG_CANDIDATES:
    path = api.expand_path(candidate)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'mcpServers': {'fixture-claude': {'command': 'fixture'}}}), encoding='utf-8')
codex = user / '.codex/config.toml'
codex.parent.mkdir(parents=True)
codex.write_text('[mcp_servers.fixture-codex]\\ncommand="fixture"\\n', encoding='utf-8')
core = api.McpCore(home)
assert api.same_path(core.codex_config, codex)
assert api.is_inside(core.claude_config, user)
state = core.mcp_state()
assert [row['name'] for row in state['rows']] == ['fixture-home']
assert sorted(row['name'] for row in state['foreign']) == ['fixture-claude', 'fixture-codex']
print('ISOLATED_DEFAULTS_OK')
""".replace('MODULE', repr(str(ROOT / 'dashboard/plugin_api.py')))
        with disposable_root() as root:
            result = run_isolated_python(probe, root / 'user')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ISOLATED_DEFAULTS_OK', result.stdout)

    def test_singletons_remain_in_the_fixture_after_a_real_core_reset(self):
        with disposable_root() as ambient, isolated_user_home(ambient / 'user'):
            fixture = McpFixture()
            try:
                with bind_test_cores(pa, fixture.core, fixture.mcp):
                    pa.reset_core()
                    rebuilt = pa.get_core()
                    self.assertTrue(pa.same_path(rebuilt.home, fixture.home))
                    self.assertIs(pa.get_mcp_core(), fixture.mcp)
                    self.assertTrue(all(pa.is_inside(rebuilt.tool_dir(key), fixture.tmp)
                                        for key in rebuilt.tools if key != 'hermes'))
            finally:
                fixture.cleanup()

    def test_singleton_binding_restores_all_state_after_failure(self):
        fixture = McpFixture()
        self.addCleanup(fixture.cleanup)
        names = ('_CORE', '_CORE_SIG', '_CORE_FROZEN', '_MCP_CORE', '_LOADOUT_SERVICE')
        before = {name: getattr(pa, name) for name in names}
        with self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
            with bind_test_cores(pa, fixture.core, fixture.mcp):
                self.assertIs(pa.get_mcp_core(), fixture.mcp)
                self.assertIs(pa.get_core(), fixture.core)
                raise RuntimeError('fixture interruption')
        for name in names:
            self.assertIs(getattr(pa, name), before[name])


if __name__ == '__main__':
    unittest.main()
