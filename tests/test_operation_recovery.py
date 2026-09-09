"""Interruptions must retain recovery evidence, not imply an operation did nothing."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa, SKILL_MD_TEMPLATE
from test_mcp_backend import CONFIG
from test_loadouts import module, selection


class OperationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.core = self.fx.core
        self.core.config_path.write_text(CONFIG, encoding='utf-8')
        self.mcp = pa.McpCore(self.fx.home, codex_config=self.fx.tmp / 'codex.toml',
                             claude_desktop_config=self.fx.tmp / 'claude.json')
        self.service = module.LoadoutService(self.core, self.mcp, pa)

    def import_skill(self, active=False):
        root = self.fx.codex if active else self.fx.tmp / 'source'
        skill = root / 'new-skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text(SKILL_MD_TEMPLATE.format(name='new-skill', desc='fixture'), encoding='utf-8')
        plan = self.core.import_plan(['codex'] if active else [], [] if active else [str(root)])
        return self.service.apply_import(plan['adoptable'], 'imported', plan['plan_id'])

    def test_import_journals_identity_before_discovery_or_activation_can_change(self):
        toggle = self.core._hermes_toggle
        def inspect(name, enabled):
            if name == 'new-skill' and not enabled:
                pending = self.service._read('pending-operation.json', {})
                self.assertEqual(len(pending['items']), 1)
                item = pending['items'][0]
                self.assertEqual(item['id'], name)
                self.assertEqual(item['status'], 'pending')
                self.assertIn('fingerprint', item)
                self.assertIn('path', item)
                self.assertFalse((self.core.skills_root / 'imported/new-skill').exists())
            return toggle(name, enabled)
        with patch.object(self.core, '_hermes_toggle', side_effect=inspect):
            result = self.import_skill()
        self.assertEqual(result['adopted'], 1)

    def test_import_undo_failure_after_restoring_source_keeps_pending_recovery(self):
        self.import_skill(active=True)
        review = self.service.undo_plan()
        with patch.object(self.core, '_hermes_toggle', side_effect=OSError('cannot finish cleanup')):
            result = self.service.undo(review['plan_id'])
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['code'], 'recovery-required')
        latest = self.service.latest()
        self.assertTrue(latest['recovery_required'])
        self.assertFalse(latest['receipt']['undo_available'])
        self.assertTrue((self.fx.codex / 'new-skill/SKILL.md').exists())
        self.assertFalse((self.fx.codex / 'new-skill').is_symlink())

    def test_catalog_repair_journals_before_unlink_and_retains_record_on_interruption(self):
        shared = self.fx.codex / 'shared'
        shared.symlink_to(self.core.skills_root, target_is_directory=True)
        plan = self.core.plan_catalog_repair('codex', 'shared')
        unlink = Path.unlink
        def inspect(path, *args, **kwargs):
            if path == shared:
                record = self.service._read('pending-operation.json', {})
                self.assertEqual(record['items'][0]['before']['kind'], 'symlink')
                self.assertEqual(record['items'][0]['id'], 'shared')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', inspect), patch.object(self.core, '_log', side_effect=OSError('after unlink')):
            result = self.service.apply_catalog_repair(plan['plan_id'])
        self.assertFalse(result['ok'], result)
        self.assertFalse(shared.is_symlink())
        self.assertTrue(self.service.latest()['recovery_required'])
        self.assertEqual(self.service._read('pending-operation.json', {})['items'][0]['id'], 'shared')

    def test_failed_selection_does_not_overwrite_previous_undo_and_reports_partial(self):
        original = self.service.apply(self.service.plan([selection()])['plan_id'])
        plan = self.service.plan([selection('skill', 'codex', 'media/orphan-skill'),
                                 selection('skill', 'codex', 'unknown/not-in-library')])
        result = self.service.apply(plan['plan_id'])
        self.assertTrue(result['partial'], result)
        self.assertEqual(result['receipt']['changed'], 1)
        self.assertEqual(result['receipt']['counts']['unavailable'], 1)
        previous = self.service.latest()['receipt']['receipt_id']
        failed = self.service.apply(self.service.plan([selection('skill', 'codex', 'unknown/not-in-library')])['plan_id'])
        self.assertEqual(failed['changed'], 0)
        self.assertEqual(self.service.latest()['receipt']['receipt_id'], previous)
        self.assertNotEqual(previous, original['receipt']['receipt_id'])

    def test_modified_named_definition_cannot_apply_an_old_preview(self):
        saved = self.service.save_loadout('Coding', [selection()])['loadout']
        review = self.service.plan_loadout(saved['id'])
        self.service.save_loadout('Coding', [selection(enabled=False)], saved['id'])
        with self.assertRaises(pa.LoadoutError):
            self.service.apply(review['plan_id'])
        self.assertFalse((self.fx.codex / 'apple-notes').is_symlink())
        self.assertIsNone(self.service.latest()['receipt'])

    def test_unknown_capture_application_is_not_silently_dropped(self):
        with self.assertRaises(pa.LoadoutError):
            self.service.capture(['hermes', 'not-an-application'])
        self.assertFalse(self.service._path('loadouts.json').exists())


if __name__ == '__main__':
    unittest.main()
