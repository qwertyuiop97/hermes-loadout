"""Regressions for destructive failure paths, using disposable files only."""
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
        self.addCleanup(self.fx.cleanup)

    def test_foreign_broken_link_is_refused_by_preview_toggle_and_repair(self):
        link = self.fx.codex / "apple-notes"
        target = self.fx.tmp / "not-ours" / "missing"
        os.symlink(target, link)
        core = self.fx.core
        plan = core.plan_bulk(["apple/apple-notes"], "codex", True)
        self.assertEqual(plan["would_change"], [])
        self.assertEqual(plan["refused"][0]["code"], "foreign-link")
        for operation in (lambda: core.toggle("apple/apple-notes", "codex", True),
                          lambda: core.repair("apple/apple-notes", "codex")):
            with self.assertRaises(pa.SkillsToggleError) as error:
                operation()
            self.assertEqual(error.exception.code, "foreign-link")
            self.assertEqual(os.readlink(link), str(target))

    def test_failed_repair_does_not_unlink_the_previous_managed_target(self):
        link = self.fx.codex / "apple-notes"
        target = self.fx.home / "skills" / "old" / "apple-notes"
        os.symlink(target, link)
        with patch.object(pa.os, "symlink", side_effect=OSError("fixture denial")):
            with self.assertRaises(pa.SkillsToggleError):
                self.fx.core.toggle("apple/apple-notes", "codex", True)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(target))
        self.assertEqual(sorted(p.name for p in self.fx.codex.iterdir()), ["apple-notes"])


class ConfigSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_invalid_settings_are_not_replaced_with_an_empty_mapping(self):
        path = pa.user_config_path(self.fx.home)
        for raw in ('{broken', '[]', '{"tools": []}', '{"tools":{},"tools":{}}'):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(pa.SkillsToggleError):
                    self.fx.core.set_tool("custom", "Custom", str(self.fx.tmp / "custom"))
                self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_first_renamed_config_write_preserves_legacy_settings(self):
        legacy = pa.legacy_user_config_path(self.fx.home)
        data = {"tools": {"existing": {"label": "Existing", "dir": str(self.fx.tmp / "existing")}},
                "preferences": {"keep": True}}
        legacy.write_text(json.dumps(data), encoding="utf-8")
        self.fx.core.set_tool("new", "New", str(self.fx.tmp / "new"))
        new = json.loads(pa.user_config_path(self.fx.home).read_text(encoding="utf-8"))
        self.assertEqual(new["tools"]["existing"], data["tools"]["existing"])
        self.assertEqual(new["preferences"], data["preferences"])
        self.assertEqual(json.loads(legacy.read_text(encoding="utf-8")), data)


class McpSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fx = McpFixture()
        self.addCleanup(self.fx.cleanup)
        self.mcp = self.fx.mcp

    def test_invalid_claude_config_fails_closed_and_stays_byte_identical(self):
        for raw in ('{broken', '[]', 'null', '{"mcpServers": []}',
                    '{"mcpServers": {}, "mcpServers": {}}'):
            with self.subTest(raw=raw):
                self.fx.claude.write_text(raw, encoding="utf-8")
                with self.assertRaises(pa.SkillsToggleError):
                    self.mcp.sync_to_claude("weather")
                self.assertEqual(self.fx.claude.read_text(encoding="utf-8"), raw)
                self.assertFalse(list(self.fx.claude.parent.glob("*.bak.hermes-switchboard.*")))

    def test_drifted_sync_requires_confirmation_for_both_writers(self):
        codex = self.fx.tmp / "codex.toml"
        codex.write_text('[mcp_servers.docs]\ncommand = "different"\n', encoding="utf-8")
        self.mcp.codex_config = codex
        for method, path in ((self.mcp.sync_to_claude, self.fx.claude),
                             (self.mcp.sync_to_codex, codex)):
            with self.subTest(writer=method.__name__):
                before = path.read_bytes()
                with self.assertRaises(pa.SkillsToggleError) as error:
                    method("docs")
                self.assertEqual(error.exception.code, "drifted")
                self.assertEqual(path.read_bytes(), before)
                self.assertTrue(method("docs", force=True)["ok"])
        doc = json.loads(self.fx.claude.read_text(encoding="utf-8"))
        self.assertIn("drifted-server", doc["mcpServers"])
        self.assertEqual(doc["preferences"], {"theme": "dark"})

    def test_atomic_write_failure_leaves_previous_config_intact(self):
        before = self.fx.claude.read_bytes()
        with patch.object(pa.os, "replace", side_effect=OSError("fixture denial")):
            with self.assertRaises((pa.SkillsToggleError, OSError)):
                self.mcp.sync_to_claude("weather")
        self.assertEqual(self.fx.claude.read_bytes(), before)
        self.assertFalse(list(self.fx.claude.parent.glob(".hermes-switchboard-*")))

    def test_state_and_logs_do_not_disclose_secret_bearing_fields(self):
        secret = "fixture-private-value-not-a-real-key"
        source = (self.fx.home / "config.yaml")
        source.write_text('mcp_servers:\n  private:\n    command: node\n'
                          '    args:\n      - ' + secret + '\n'
                          '    url: https://example.invalid/mcp?key=' + secret + '\n'
                          '    headers:\n      Authorization: ' + secret + '\n'
                          '    env:\n      KEY: ' + secret + '\n'
                          '    custom_secret: ' + secret + '\n', encoding="utf-8")
        state = self.mcp.mcp_state()
        self.assertNotIn(secret, json.dumps(state))
        self.mcp._log(action="fixture", headers={"Authorization": secret},
                      args=[secret], url="https://example.invalid/" + secret,
                      nested={"token": secret})
        self.assertNotIn(secret, self.mcp.log_path.read_text(encoding="utf-8"))
        # Redaction is only for public state, not the actual client projection.
        self.mcp.sync_to_claude("private")
        written = json.loads(self.fx.claude.read_text(encoding="utf-8"))
        self.assertEqual(written["mcpServers"]["private"]["env"]["KEY"], secret)


if __name__ == "__main__":
    unittest.main()
