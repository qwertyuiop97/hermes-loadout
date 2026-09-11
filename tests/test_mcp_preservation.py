"""MCP writers must preserve native policy and refuse ambiguous input."""
from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import pa
from test_mcp_backend import McpFixture, needs_yaml


@needs_yaml
class McpPreservationTests(unittest.TestCase):
    def setUp(self):
        self.fx = McpFixture()
        self.addCleanup(self.fx.cleanup)
        self.codex = self.fx.tmp / 'codex.toml'
        self.mcp = pa.McpCore(self.fx.home, self.fx.claude, codex_config=self.codex)

    def test_quoted_names_arrays_and_headers_roundtrip_without_touching_foreign_text(self):
        foreign = '# keep this exactly\r\n[agents]\r\nmax_depth = 2 # native\r\n[mcp_servers.docs]\r\ncommand = "unrelated"\r\n'
        old = '[mcp_servers."docs.cloud"]\r\ncommand = "old"\r\nargs = ["one,two", "a#b"]\r\n'
        self.codex.write_bytes((foreign + old).encode())
        source = 'mcp_servers:\n  docs.cloud:\n    url: https://example.invalid/mcp\n    headers:\n      X-Api-Key: fixture-only-value\n'
        (self.fx.home / 'config.yaml').write_text(source)
        self.assertEqual(pa.parse_codex_mcp(old)['docs.cloud']['definition']['args'], ['one,two','a#b'])
        self.mcp.sync_to_codex('docs.cloud', force=True)
        raw = self.codex.read_bytes().decode()
        self.assertIn(foreign, raw)
        parsed = pa.parse_codex_mcp(raw)
        self.assertEqual(parsed['docs.cloud']['definition']['http_headers'], {'X-Api-Key': 'fixture-only-value'})
        self.assertEqual(parsed['docs']['definition']['command'], 'unrelated')
        self.mcp.remove_from_codex('docs.cloud')
        self.assertIn(foreign, self.codex.read_bytes().decode())
        self.assertNotIn('docs.cloud', pa.parse_codex_mcp(self.codex.read_text()))

    def test_sync_preserves_disabled_flag_timeouts_and_tool_policy(self):
        self.codex.write_text('[mcp_servers.docs]\ncommand="old"\nargs=["old.js"]\nenabled=false\nstartup_timeout_sec=120\nenabled_tools=["lookup"]\n')
        result = self.mcp.sync_to_codex('docs', force=True)
        parsed = pa.parse_codex_mcp(self.codex.read_text())['docs']
        self.assertFalse(parsed['enabled'])
        self.assertEqual(parsed['definition']['startup_timeout_sec'], 120)
        self.assertEqual(parsed['definition']['enabled_tools'], ['lookup'])
        self.assertEqual(result['state'], 'disabled')
        self.assertEqual(next(r for r in self.mcp.mcp_state()['rows'] if r['name']=='docs')['writers']['codex'], 'disabled')
        # Native policy is not a difference in the shared server definition.
        self.mcp.sync_to_codex('docs')

    def test_malformed_or_unsupported_toml_never_changes_the_file(self):
        for raw in ('[mcp_servers.docs]\ncommand="a"\ncommand="b"\n',
                    '[mcp_servers.docs]\nargs=["unterminated]\n',
                    'mcp_servers = "not a table"\n',
                    'mcp_servers={docs={command="node",args=["docs-server.js"]}}\n'):
            with self.subTest(raw=raw):
                self.codex.write_text(raw)
                before = self.codex.read_bytes()
                with self.assertRaises(pa.LoadoutError):
                    self.mcp.sync_to_codex('docs', force=True)
                self.assertEqual(self.codex.read_bytes(), before)

    def test_multiline_strings_with_fake_headers_do_not_remove_foreign_servers(self):
        raw = '''[mcp_servers.docs]
command="old"
[notes]
text = """
[mcp_servers.docs]
command = "not a real server"
"""
[mcp_servers.other]
command = "keep"
'''
        self.codex.write_text(raw)
        self.mcp.sync_to_codex('docs', force=True)
        self.assertIn(raw[raw.index('[notes]'):], self.codex.read_text())
        self.assertEqual(pa.parse_codex_mcp(self.codex.read_text())['other']['definition']['command'], 'keep')

    def test_one_malformed_writer_does_not_hide_the_other_writer(self):
        self.codex.write_text('[bad]\nvalue="fixture-secret-with-no-end\n')
        state = self.mcp.mcp_state()
        self.assertTrue(state['ok'])
        self.assertTrue(state['partial_failure'])
        self.assertFalse(state['writers']['codex']['available'])
        self.assertTrue(state['writers']['claude']['available'])
        self.assertEqual(state['rows'][0]['writers']['codex'], 'unavailable')
        self.assertNotIn('fixture-secret-with-no-end', json.dumps(state))

    def test_claude_preserves_unknown_settings_and_refuses_remote_projection(self):
        data = json.loads(self.fx.claude.read_text())
        data['mcpServers']['docs']['native_policy'] = {'allow': False}
        self.fx.claude.write_text(json.dumps(data))
        self.mcp.sync_to_claude('docs', force=True)
        after = json.loads(self.fx.claude.read_text())
        self.assertEqual(after['mcpServers']['docs']['native_policy'], {'allow': False})
        self.assertEqual(after['preferences'], data['preferences'])
        source = self.fx.home / 'config.yaml'
        source.write_text('mcp_servers:\n  remote:\n    url: https://example.invalid/mcp\n')
        before = self.fx.claude.read_bytes()
        with self.assertRaises(pa.LoadoutError) as error:
            self.mcp.sync_to_claude('remote')
        self.assertEqual(error.exception.code, 'unsupported-transport')
        self.assertEqual(self.fx.claude.read_bytes(), before)
        state = self.mcp.mcp_state()
        self.assertEqual(state['rows'][0]['writers']['claude'], 'unsupported')

    def test_codex_foreign_servers_are_reported_without_values(self):
        self.codex.write_text('[mcp_servers.foreign]\ncommand="fixture-private-command"\n')
        state = self.mcp.mcp_state()
        self.assertIn({'name':'foreign','writer':'codex','keys':['command']}, state['foreign'])
        self.assertNotIn('fixture-private-command', json.dumps(state))

if __name__ == '__main__':
    unittest.main()
