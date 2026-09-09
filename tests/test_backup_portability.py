"""Reproduce the Windows dangling-backup collision on every CI platform."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa


class BackupPortabilityTests(unittest.TestCase):
    def test_dangling_entry_is_skipped_when_exclusive_open_would_follow_it(self):
        fixture = Fixture()
        self.addCleanup(fixture.cleanup)
        source = fixture.tmp / 'client.json'
        source.write_bytes(b'fixture-private-value\r\nline\n\x00end')
        mcp = pa.McpCore(fixture.home, source)
        occupied = source.with_name(source.name + '.bak.hermes-loadout.20260101-120000')
        external = fixture.tmp / 'untouched-foreign-target'
        occupied.symlink_to(external)
        original_target = os.readlink(occupied)
        original_open = pa.os.open

        class FixedTime:
            @staticmethod
            def now(*args, **kwargs):
                return datetime(2026, 1, 1, 12)

        def follows_dangling(path, flags, *args, **kwargs):
            # Emulate the observed create-new behavior without changing any
            # files except disposable fixtures. Removing the entry guard must
            # fail here on POSIX too, rather than only in the Windows job.
            if Path(path) == occupied and flags & os.O_CREAT:
                flags &= ~os.O_EXCL
            return original_open(path, flags, *args, **kwargs)

        with patch.object(pa, 'datetime', FixedTime), patch.object(pa.os, 'open', follows_dangling):
            for adapter in (fixture.core, mcp):
                backup = Path(adapter._backup(source))
                self.assertNotEqual(backup, occupied)
                self.assertTrue(occupied.is_symlink())
                self.assertEqual(os.readlink(occupied), original_target)
                self.assertFalse(external.exists())
                self.assertFalse(backup.is_symlink())
                self.assertEqual(backup.read_bytes(), source.read_bytes())


if __name__ == '__main__':
    unittest.main()
