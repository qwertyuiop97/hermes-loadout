"""Regression tests for refusal, rollback, and secret-safe writer failures."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa
from test_mcp_backend import McpFixture


class LinkSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_foreign_dangling_link_refused_in_preview_and_apply(self):
        link = self.fx.grok / 'apple-notes'
        link.unlink()
        outside = self.fx.tmp / 'missing-foreign-skill'
        os.symlink(str(outside), str(link), target_is_directory=True)
        before = os.readlink(link)
        plan = self.fx.core.plan_bulk(['apple/apple-notes'], 'grok', True)
        self.assertEqual(plan['would_change'], [])
        self.assertEqual(plan['refused'][0]['code'], 'foreign-link')
        with self.assertRaises(pa.SkillsToggleError) as raised:
            self.fx.core.toggle('apple/apple-notes', 'grok', True)
        self.assertEqual(raised.exception.code, 'foreign-link')
        self.assertEqual(os.readlink(link), before)

    def test_failed_repair_keeps_original_managed_broken_link(self):
        link = self.fx.grok / 'rem índéluxé'
        before = os.readlink(link)
        with patch.object(pa.os, 'symlink', side_effect=OSError('fixture failure')):
            with self.assertRaises(pa.SkillsToggleError):
                self.fx.core.toggle('apple/rem índéluxé', 'grok', True)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), before)
        self.assertFalse(list(link.parent.glob('.switchboard-link-*')))

    def test_custom_config_invalid_json_is_not_replaced(self):
        config = pa.user_config_path(self.fx.home)
        config.write_text('{"tools":', encoding='utf-8')
        before = config.read_bytes()
        with self.assertRaises(pa.SkillsToggleError):
            self.fx.core.set_tool('custom', 'Custom', str(self.fx.tmp / 'custom'))
        self.assertEqual(config.read_bytes(), before)

    def test_custom_edit_preserves_legacy_config_and_unknown_fields(self):
        legacy = pa.legacy_user_config_path(self.fx.home)
        data = {'tools': {'old': {'label': 'Old', 'dir': str(self.fx.tmp / 'old')}}, 'preference': 7}
        legacy.write_text(json.dumps(data), encoding='utf-8')
        self.fx.core.set_tool('custom', 'Custom', str(self.fx.tmp / 'custom'))
        actual = json.loads(pa.user_config_path(self.fx.home).read_text())
        self.assertEqual(actual['tools']['old'], data['tools']['old'])
        self.assertEqual(actual['preference'], 7)
        self.assertEqual(json.loads(legacy.read_text()), data)


class McpSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fx = McpFixture()
        self.fx.mcp.codex_config = self.fx.tmp / 'codex' / 'config.toml'

    def tearDown(self):
        self.fx.cleanup()

    def test_invalid_json_and_wrong_shapes_are_preserved(self):
        for text in ('{"mcpServers":', '[]', '{"mcpServers": []}', '{"mcpServers": null}',
                     '{"mcpServers":{},"mcpServers":{}}'):
            with self.subTest(text=text):
                self.fx.claude.write_text(text, encoding='utf-8')
                with self.assertRaises(pa.SkillsToggleError) as raised:
                    self.fx.mcp.sync_to_claude('weather')
                self.assertEqual(raised.exception.code, 'config-invalid')
                self.assertEqual(self.fx.claude.read_text(), text)
                state = self.fx.mcp.mcp_state()
                self.assertTrue(state['ok'])
                self.assertIn('error', state['writers']['claude'])
                self.assertTrue(all(row['writers']['claude'] == 'error' for row in state['rows']))

    def test_sync_drift_requires_explicit_confirmation(self):
        before = self.fx.claude.read_bytes()
        with self.assertRaises(pa.SkillsToggleError) as raised:
            self.fx.mcp.sync_to_claude('docs')
        self.assertEqual(raised.exception.code, 'drifted')
        self.assertEqual(self.fx.claude.read_bytes(), before)
        result = self.fx.mcp.sync_to_claude('docs', force=True)
        self.assertTrue(result['ok'])
        doc = json.loads(self.fx.claude.read_text())
        self.assertEqual(doc['mcpServers']['drifted-server']['command'], 'python')
        self.assertEqual(doc['preferences'], {'theme': 'dark'})

    def test_display_redacts_headers_arguments_and_url_credentials(self):
        config = self.fx.home / 'config.yaml'
        config.write_text('mcp_servers:\n  remote:\n    url: https://user:fixture-password@example.test/mcp?key=fixture-query#fixture-fragment\n    headers:\n      X-Private: fixture-header\n    args:\n      - fixture-argument\n    env:\n      KEY: fixture-env\n', encoding='utf-8')
        state = self.fx.mcp.mcp_state()
        exposed = json.dumps(state)
        for secret in ('fixture-password', 'fixture-query', 'fixture-fragment', 'fixture-header', 'fixture-argument', 'fixture-env'):
            self.assertNotIn(secret, exposed)
        self.fx.mcp.sync_to_claude('remote')
        written = json.loads(self.fx.claude.read_text())['mcpServers']['remote']
        self.assertEqual(written['headers']['X-Private'], 'fixture-header')
        self.assertEqual(written['args'], ['fixture-argument'])
        self.assertEqual(written['env']['KEY'], 'fixture-env')

    def test_failed_config_replace_preserves_original_bytes(self):
        before = self.fx.claude.read_bytes()
        with patch.object(pa.os, 'replace', side_effect=OSError('fixture write failure')):
            with self.assertRaises(pa.SkillsToggleError):
                self.fx.mcp.sync_to_claude('weather')
        self.assertEqual(self.fx.claude.read_bytes(), before)
        self.assertFalse(list(self.fx.claude.parent.glob('.switchboard-write-*')))


if __name__ == '__main__':
    unittest.main()
