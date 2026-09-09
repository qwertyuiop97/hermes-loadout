"""Undo must not accept arbitrary paths or discard a link on rename failure."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa


class RestoreSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.core = self.fx.core
        self.name = 'apple-notes'
        self.skill = self.fx.home / 'skills/apple/apple-notes'
        self.link = self.fx.codex / self.name
        self.core.toggle('apple/apple-notes', 'codex', True)

    def backup(self):
        path = self.fx.codex / (self.name + '.hermes-loadout-backup-20260909-120000')
        path.mkdir()
        (path / 'SKILL.md').write_text('Original local skill', encoding='utf-8')
        return path

    def test_arbitrary_backup_and_traversal_never_move_unrelated_data(self):
        outside = self.fx.tmp / 'unrelated'
        outside.mkdir()
        (outside / 'important.txt').write_text('keep', encoding='utf-8')
        for name in (self.name, '../codex/apple-notes', '..\\codex\\apple-notes', '/absolute', '.'):
            with self.subTest(name=name):
                with self.assertRaises(pa.LoadoutError):
                    self.core.revert_push('codex', name, str(outside))
                self.assertTrue(self.link.is_symlink())
                self.assertEqual((outside / 'important.txt').read_text(encoding='utf-8'), 'keep')

    def test_backup_symlink_is_not_treated_as_a_real_preserved_directory(self):
        outside = self.fx.tmp / 'unrelated'
        outside.mkdir()
        backup = self.fx.codex / (self.name + '.hermes-loadout-backup-20260909-120000')
        backup.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(pa.LoadoutError):
            self.core.revert_push('codex', self.name, str(backup))
        self.assertTrue(backup.is_symlink())
        self.assertTrue(self.link.is_symlink())

    def test_rename_failure_restores_the_exact_managed_link(self):
        backup = self.backup()
        original = os.readlink(self.link)
        real_rename = pa.os.rename
        def deny_backup(source, destination):
            if Path(source) == backup:
                raise OSError('fixture denial')
            return real_rename(source, destination)
        with patch.object(pa.os, 'rename', side_effect=deny_backup):
            with self.assertRaises(pa.LoadoutError):
                self.core.revert_push('codex', self.name, str(backup))
        self.assertEqual(os.readlink(self.link), original)
        self.assertTrue((backup / 'SKILL.md').is_file())

    def test_adopt_undo_validates_the_entire_request_before_touching_the_link(self):
        backup = self.backup()
        for skill in ('../unrelated', 'apple/other-skill', 'apple/../apple-notes'):
            with self.subTest(skill=skill):
                with self.assertRaises(pa.LoadoutError):
                    self.core.revert_adopt('codex', self.name, str(backup), skill)
                self.assertTrue(self.link.is_symlink())
                self.assertTrue(backup.is_dir())

    def test_pull_undo_rejects_unrelated_canonical_backup_before_tool_restore(self):
        backup = self.backup()
        outside = self.fx.tmp / 'unrelated'
        outside.mkdir()
        with self.assertRaises(pa.LoadoutError):
            self.core.revert_pull('codex', self.name, str(outside), str(backup))
        self.assertTrue(self.link.is_symlink())
        self.assertTrue(self.skill.is_dir())
        self.assertTrue(outside.is_dir())
