"""MCP writer regression tests use disposable files and fake credentials only."""
from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mcp_backend import McpFixture, pa


class WriterPreservation(unittest.TestCase):
    def setUp(self):
        self.fx = McpFixture()
        self.path = self.fx.tmp / 'codex.toml'
        self.fx.mcp.codex_config = self.path

    def tearDown(self):
        self.fx.cleanup()

    def test_malformed_codex_is_preserved_with_typed_writer_error(self):
        for raw in ('[mcp_servers.bad\ncommand = "bad"', '[mcp_servers.bad]\nargs = ["unfinished"',
                    '[mcp_servers.bad]\ncommand = "one"\ncommand = "two"'):
            self.path.write_text(raw)
            with self.assertRaises(pa.SkillsToggleError):
                self.fx.mcp.sync_to_codex('weather')
            self.assertEqual(self.path.read_text(), raw)
            self.assertIn('error', self.fx.mcp.mcp_state()['writers']['codex'])

    def test_remote_headers_are_real_codex_tables_not_stringified_dicts(self):
        source = self.fx.home / 'config.yaml'
        source.write_text('mcp_servers:\n  remote.v1:\n    url: https://example.test/mcp\n    headers:\n      X-Private: fixture-secret\n')
        self.fx.mcp.sync_to_codex('remote.v1')
        text = self.path.read_text()
        self.assertIn('http_headers', text)
        parsed = pa.parse_codex_mcp(text)
        self.assertIn('remote.v1', parsed)
        self.assertEqual(parsed['remote.v1']['definition']['headers'], {'X-Private': 'fixture-secret'})
        self.assertEqual(self.fx.mcp.mcp_state()['rows'][0]['writers']['codex'], 'enabled')
        self.assertNotIn('fixture-secret', json.dumps(self.fx.mcp.mcp_state()))

    def test_arguments_with_commas_escapes_and_hashes_roundtrip(self):
        expected = {'command': 'node', 'args': ['one,two', 'a#b', 'say "hello"', 'line\nbreak']}
        parsed = pa.parse_codex_mcp(pa.codex_server_block('quoted.name', expected))
        self.assertEqual(parsed['quoted.name']['definition'], expected)

    def test_foreign_quoted_name_and_comments_survive_sync_and_remove(self):
        foreign = '# keep this\n[mcp_servers."foreign.v1"]\ncommand = "keep"\nargs = ["comma,argument"]\n\n[other]\nmode = "safe"\n'
        self.path.write_text(foreign)
        self.fx.mcp.sync_to_codex('weather')
        self.assertIn(foreign.rstrip('\n'), self.path.read_text())
        self.fx.mcp.remove_from_codex('weather')
        self.assertIn(foreign.rstrip('\n'), self.path.read_text())
        self.assertIn('foreign.v1', pa.parse_codex_mcp(self.path.read_text()))

    def test_sync_preserves_client_specific_safety_and_timeout_fields(self):
        self.path.write_text('[mcp_servers.docs]\ncommand = "node"\nargs = ["old.js"]\nenabled = false\nstartup_timeout_sec = 60\ndisabled_tools = ["dangerous"]\n')
        self.fx.mcp.sync_to_codex('docs', force=True)
        parsed = pa.parse_codex_mcp(self.path.read_text())['docs']
        self.assertEqual(parsed['definition']['disabled_tools'], ['dangerous'])
        self.assertEqual(parsed['definition']['startup_timeout_sec'], 60)
        self.assertTrue(parsed['enabled'])
        row = next(r for r in self.fx.mcp.mcp_state()['rows'] if r['name'] == 'docs')
        self.assertEqual(row['writers']['codex'], 'enabled')

    def test_codex_unreadable_file_is_never_empty_configuration(self):
        self.path.write_text('[mcp_servers.keep]\ncommand = "keep"\n')
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == self.path:
                raise PermissionError('fixture permission denied')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            with self.assertRaises(pa.SkillsToggleError):
                self.fx.mcp.sync_to_codex('weather')
        self.assertIn('keep', self.path.read_text())

    def test_failed_hermes_toggle_preserves_config_bytes(self):
        before = (self.fx.home / 'config.yaml').read_bytes()
        with patch.object(pa.os, 'replace', side_effect=OSError('fixture replace failure')):
            with self.assertRaises(pa.SkillsToggleError):
                self.fx.mcp.toggle_hermes('weather', True)
        self.assertEqual((self.fx.home / 'config.yaml').read_bytes(), before)

    def test_python39_fallback_roundtrips_headers_and_quoted_arguments(self):
        projection = {'command': 'node', 'args': ['a,b', 'hash#literal', 'say "hi"'], 'headers': {'X-Key': 'fixture'}}
        with patch.dict(sys.modules, {'tomllib': None}):
            parsed = pa.parse_codex_mcp(pa.codex_server_block('fixture.v1', projection))
            self.assertEqual(parsed['fixture.v1']['definition'], projection)
            self.fx.mcp.sync_to_codex('weather')
            self.fx.mcp.remove_from_codex('weather')

    def test_python39_unsupported_and_invalid_values_are_not_guessed(self):
        with patch.dict(sys.modules, {'tomllib': None}):
            for value in ('[null]', '[{"key": "value"}]', '[NaN]', '"raw' + chr(127) + 'control"', "\"\"\"multiline\"\"\""):
                self.path.write_text('[mcp_servers.keep]\nargs = ' + value + '\n')
                before = self.path.read_bytes()
                with self.assertRaises(pa.SkillsToggleError):
                    self.fx.mcp.sync_to_codex('weather')
                self.assertEqual(self.path.read_bytes(), before)

    def test_complex_table_write_is_a_typed_refusal(self):
        raw = '[mcp_servers.weather]\nurl = "https://weather.example/mcp"\n[[other.items]]\nname = "keep"\n'
        self.path.write_text(raw)
        with self.assertRaises(pa.SkillsToggleError):
            self.fx.mcp.sync_to_codex('weather', force=True)
        self.assertEqual(self.path.read_text(), raw)

    @unittest.skipIf(os.name == 'nt', 'POSIX file mode contract')
    def test_config_backups_are_private_and_never_overwritten(self):
        self.path.write_text('fixture-secret')
        self.path.chmod(0o644)
        first = Path(self.fx.mcp._backup(self.path))
        self.path.write_text('new-secret')
        second = Path(self.fx.mcp._backup(self.path))
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(), 'fixture-secret')
        self.assertEqual(second.read_text(), 'new-secret')
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(second.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
