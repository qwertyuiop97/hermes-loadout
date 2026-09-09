"""Private configuration backups must never follow or replace existing entries."""
from __future__ import annotations

import os
import stat
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa


class BackupIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.source = self.fx.tmp / 'client.json'
        self.source.write_bytes(b'fixture-private-value\r\nline\n\x00end')
        self.mcp = pa.McpCore(self.fx.home, claude_desktop_config=self.source,
                              codex_config=self.fx.tmp / "codex.toml")

    def test_private_distinct_backups_preserve_bytes_for_both_adapters(self):
        for adapter in (self.fx.core, self.mcp):
            self.source.write_bytes(b'fixture-private-value\r\nline\n\x00end')
            self.source.chmod(0o644)
            first = Path(adapter._backup(self.source))
            self.source.write_bytes(b'new-fixture-value')
            second = Path(adapter._backup(self.source))
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b'fixture-private-value\r\nline\n\x00end')
            self.assertEqual(second.read_bytes(), b'new-fixture-value')
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(second.stat().st_mode), 0o600)

    def test_dangling_collision_never_writes_through_a_foreign_link(self):
        class FixedTime:
            @staticmethod
            def now(*args, **kwargs):
                return datetime(2026, 1, 1, 12)
        occupied = self.source.with_name(self.source.name + '.bak.hermes-loadout.20260101-120000')
        external = self.fx.tmp / 'foreign-target'
        occupied.symlink_to(external)
        original_target = os.readlink(occupied)
        with patch.object(pa, 'datetime', FixedTime):
            for adapter in (self.fx.core, self.mcp):
                backup = Path(adapter._backup(self.source))
                self.assertNotEqual(backup, occupied)
                self.assertEqual(backup.read_bytes(), self.source.read_bytes())
                self.assertTrue(occupied.is_symlink())
                self.assertEqual(os.readlink(occupied), original_target)
                self.assertFalse(external.exists())

    def test_linked_config_is_not_copied_and_failed_backup_cleans_only_owned_file(self):
        alias = self.fx.tmp / 'alias.json'
        alias.symlink_to(self.source)
        for adapter in (self.fx.core, self.mcp):
            with self.assertRaises(pa.LoadoutError):
                adapter._backup(alias)
        before = self.source.read_bytes()
        with patch.object(pa.shutil, 'copyfileobj', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError):
                self.fx.core._backup(self.source)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.source.parent.glob('client.json.bak.hermes-loadout.*')), [])


if __name__ == '__main__':
    unittest.main()
