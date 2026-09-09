"""Reviewed conflict and recovery operations preserve originals and current ownership."""
from __future__ import annotations

import builtins
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa, SKILL_MD_TEMPLATE
from test_loadouts import module
from test_mcp_backend import CONFIG


class ReviewedMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.core = self.fx.core
        self.core.config_path.write_text(CONFIG, encoding='utf-8')
        self.mcp = pa.McpCore(self.fx.home, codex_config=self.fx.tmp / 'client.toml',
                             claude_desktop_config=self.fx.tmp / 'client.json')
        self.service = module.LoadoutService(self.core, self.mcp, pa)
        self.canonical = self.core.skills_root / 'apple/apple-notes'
        self.external = self.fx.codex / 'apple-notes'
        self.external.mkdir()
        (self.external / 'SKILL.md').write_text(SKILL_MD_TEMPLATE.format(name='apple-notes', desc='external copy'), encoding='utf-8')

    def test_conflict_preview_is_read_only_apply_preserves_activation_and_undo_restores_both(self):
        for choice in ('library', 'source'):
            with self.subTest(choice=choice):
                canonical = (self.canonical / 'SKILL.md').read_bytes()
                external = (self.external / 'SKILL.md').read_bytes()
                config = self.core.config_path.read_bytes()
                review = self.service.plan_conflict('codex', 'apple-notes', choice)
                self.assertFalse(self.external.is_symlink())
                self.assertFalse(self.service._path('last-operation.json').exists())
                result = self.service.apply_conflict(review['plan_id'])
                self.assertEqual(result['changed'], 1, result)
                self.assertTrue(self.external.is_symlink())
                self.assertEqual(self.core.config_path.read_bytes(), config)
                self.assertEqual((self.canonical / 'SKILL.md').read_bytes(), external if choice == 'source' else canonical)
                preview = self.service.undo_plan()
                self.assertEqual(preview['counts']['restore'], 1, preview)
                self.assertEqual(self.service.undo(preview['plan_id'])['changed'], 1)
                self.assertFalse(self.external.is_symlink())
                self.assertEqual((self.external / 'SKILL.md').read_bytes(), external)
                self.assertEqual((self.canonical / 'SKILL.md').read_bytes(), canonical)
                self.service._path('last-operation.json').unlink()

    def test_conflict_cannot_apply_stale_content_or_undo_an_external_edit(self):
        review = self.service.plan_conflict('codex', 'apple-notes', 'source')
        (self.external / 'asset.txt').write_text('new material')
        with self.assertRaises(pa.LoadoutError): self.service.apply_conflict(review['plan_id'])
        result = self.service.apply_conflict(self.service.plan_conflict('codex', 'apple-notes', 'library')['plan_id'])
        self.assertEqual(result['changed'], 1)
        self.external.unlink()
        self.external.mkdir()
        (self.external / 'owner.txt').write_text('do not overwrite')
        undo = self.service.undo_plan()
        self.assertEqual(undo['counts']['restore'], 0)
        self.service.undo(undo['plan_id'])
        self.assertEqual((self.external / 'owner.txt').read_text(), 'do not overwrite')

    def test_redirected_category_and_linked_source_content_are_refused_before_mutation(self):
        outside = self.fx.tmp / 'outside'
        outside.mkdir()
        (self.external / 'linked').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(pa.LoadoutError): self.service.plan_conflict('codex', 'apple-notes', 'source')
        (self.external / 'linked').unlink()
        original = self.core.skills_root / 'apple'
        original.rename(self.core.skills_root / '.apple-original')
        original.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(pa.LoadoutError): self.service.plan_conflict('codex', 'apple-notes', 'source')
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.external.is_symlink())

    def test_current_config_and_mcp_backups_are_listed_previewed_restored_and_undone_without_secrets(self):
        targets = [('config', self.core.config_path, CONFIG + '\n# after\n'),
                   ('tools-json', self.fx.home / 'hermes-loadout.json', '{"tools": {}, "note": "after"}\n'),
                   ('mcp-codex', self.mcp.codex_config, 'owner = "after"\n'),
                   ('mcp-claude', self.mcp.claude_config, '{"mcpServers": {}, "note":"after"}\n')]
        for kind, target, after in targets:
            with self.subTest(kind=kind):
                before = CONFIG if kind == 'config' else ('{"tools": {}}\n' if kind == 'tools-json' else
                         'token = "secret-fixture"\n' if kind == 'mcp-codex' else '{"mcpServers": {}}\n')
                target.write_text(before, encoding='utf-8')
                backup = self.core._backup(target)
                target.write_text(after, encoding='utf-8')
                rows = self.service.list_backups()['backups']
                self.assertTrue(any(row['path'] == backup and row['kind'] == kind for row in rows))
                if kind == 'config':
                    try:
                        import yaml
                    except ImportError:
                        with self.assertRaises(pa.LoadoutError) as error:
                            self.service.plan_backup(backup)
                        self.assertEqual(error.exception.code, 'yaml-unavailable')
                        self.assertEqual(target.read_text(), after)
                        continue
                preview = self.service.plan_backup(backup)
                self.assertEqual(target.read_text(), after)
                result = self.service.apply_backup(preview['plan_id'])
                self.assertEqual(result['changed'], 1, result)
                self.assertEqual(target.read_text(), before)
                self.assertNotIn('secret-fixture', self.service._path('last-operation.json').read_text())
                self.assertEqual(self.service.undo(self.service.undo_plan()['plan_id'])['changed'], 1)
                self.assertEqual(target.read_text(), after)

    def test_preserved_application_copy_restore_and_undo_do_not_destroy_either_copy(self):
        original = (self.external / 'SKILL.md').read_bytes()
        self.service.apply_conflict(self.service.plan_conflict('codex', 'apple-notes', 'library')['plan_id'])
        backup = next(row for row in self.service.list_backups()['backups'] if row['kind'] == 'tool-link')['path']
        result = self.service.apply_backup(self.service.plan_backup(backup)['plan_id'])
        self.assertEqual(result['changed'], 1, result)
        self.assertFalse(self.external.is_symlink())
        self.assertEqual((self.external / 'SKILL.md').read_bytes(), original)
        self.assertTrue(Path(backup).is_dir())
        result = self.service.undo(self.service.undo_plan()['plan_id'])
        self.assertEqual(result['changed'], 1, result)
        self.assertTrue(self.external.is_symlink())
        self.assertEqual((Path(backup) / 'SKILL.md').read_bytes(), original)

    def test_backup_restoration_refuses_unsafe_malformed_missing_and_changed_candidates(self):
        target = self.fx.home / 'hermes-loadout.json'
        target.write_text('{"tools": {}}')
        backup = Path(self.core._backup(target))
        before = target.read_bytes()
        for text in ('not json', '{"tools": []}', '{"schema_version": 99, "tools":{}}'):
            backup.write_text(text)
            with self.assertRaises((pa.LoadoutError, ValueError)): self.service.plan_backup(str(backup))
            self.assertEqual(target.read_bytes(), before)
        backup.write_bytes(before)
        preview = self.service.plan_backup(str(backup))
        backup.write_text('{"tools": {}, "note": "changed"}')
        with self.assertRaises(pa.LoadoutError): self.service.apply_backup(preview['plan_id'])
        backup.unlink()
        with self.assertRaises(pa.LoadoutError): self.service.plan_backup(str(backup))
        backup.symlink_to(target)
        with self.assertRaises(pa.LoadoutError): self.service.plan_backup(str(backup))
        self.assertEqual(target.read_bytes(), before)

    def test_full_yaml_restore_rejects_malformed_and_ambiguous_backups(self):
        target = self.core.config_path
        original = target.read_bytes()
        backup = Path(self.core._backup(target))
        cases = ['skills: {}\nother: [unterminated', 'skills: {}\nskills: {disabled: []}\n',
                 'skills: {disabled: surprise}\n', 'skills: {}\nmcp_servers: []\n']
        for text in cases:
            with self.subTest(text=text):
                backup.write_text(text, encoding='utf-8')
                with self.assertRaises(pa.LoadoutError) as error:
                    self.service.plan_backup(str(backup))
                self.assertIn(error.exception.code, ('invalid-backup', 'yaml-unavailable'))
                self.assertEqual(target.read_bytes(), original)
        backup.write_bytes(original)
        original_import = builtins.__import__
        def no_yaml(name, *args, **kwargs):
            if name == 'yaml':
                raise ImportError('fixture: optional YAML parser absent')
            return original_import(name, *args, **kwargs)
        with patch.object(builtins, '__import__', side_effect=no_yaml):
            with self.assertRaises(pa.LoadoutError) as error:
                self.service.plan_backup(str(backup))
        self.assertEqual(error.exception.code, 'yaml-unavailable')
        self.assertEqual(target.read_bytes(), original)

    def test_interrupted_conflict_preserves_both_originals_and_records_recovery(self):
        original = (self.canonical / 'SKILL.md').read_bytes()
        preview = self.service.plan_conflict('codex', 'apple-notes', 'source')
        with patch.object(os, 'symlink', side_effect=OSError('simulate link privilege failure')):
            result = self.service.apply_conflict(preview['plan_id'])
        self.assertFalse(result['ok'])
        self.assertTrue(self.service.latest()['recovery_required'])
        pending = self.service._read('pending-operation.json', {})['items'][0]
        self.assertEqual((Path(pending['canonical_backup']) / 'SKILL.md').read_bytes(), original)
        self.assertTrue((Path(pending['tool_backup']) / 'SKILL.md').exists())


if __name__ == '__main__':
    unittest.main()
