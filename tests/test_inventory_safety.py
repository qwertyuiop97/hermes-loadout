"""Library inventory must not imply activation; reviewed inputs stay immutable."""
from __future__ import annotations
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa


class InventorySafetyTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.core = self.fx.core
        self.source = self.fx.tmp / 'source'
        self.source.mkdir()
        self.skill = self.source / 'new-skill'
        self.skill.mkdir()
        (self.skill / 'SKILL.md').write_text('---\nname: new-skill\ndescription: A test skill.\n---\nBody\n', encoding='utf-8')

    def plan(self, tools=None, roots=None):
        return self.core.import_plan(tools or [], roots or [str(self.source)])

    def apply(self, plan):
        return self.core.import_apply_plan(plan['adoptable'], plan_id=plan['plan_id'])

    def test_plain_folder_needs_no_client_and_imports_off_everywhere(self):
        before = self.core.state()
        names = set(self.fx.home.rglob('*'))
        plan = self.plan()
        self.assertEqual(names, set(self.fx.home.rglob('*')), 'Preview is read-only')
        result = self.apply(plan)
        self.assertEqual(result['adopted'], 1)
        row = next(r for r in self.core.state()['skills'] if r['id'] == 'imported/new-skill')
        self.assertEqual(row['tools']['hermes']['state'], 'disabled')
        self.assertTrue(all(v['state'] != 'enabled' for v in row['tools'].values()))
        for old in before['skills']:
            current = next(r for r in self.core.state()['skills'] if r['id'] == old['id'])
            self.assertEqual(current['tools'], old['tools'])
        self.assertTrue(self.skill.is_dir())

    def test_active_source_stays_active_without_enabling_hermes_or_other_clients(self):
        target = self.fx.codex / self.skill.name
        os.rename(self.skill, target)
        plan = self.core.import_plan(['codex'], [])
        result = self.core.import_apply_plan([r for r in plan['adoptable'] if r['name'] == 'new-skill'], plan_id=plan['plan_id'])
        row = next(r for r in self.core.state()['skills'] if r['id'] == 'imported/new-skill')
        self.assertEqual(row['tools']['codex']['state'], 'enabled')
        self.assertEqual(row['tools']['hermes']['state'], 'disabled')
        backup = Path(result['results'][0]['backup'])
        self.assertTrue(backup.is_dir())
        self.assertFalse(pa.is_inside(backup, self.fx.codex), 'Preserved originals must not remain discoverable')

    def test_redirected_destination_is_refused_at_preview_and_apply(self):
        outside = self.fx.tmp / 'outside'; outside.mkdir()
        category = self.core.skills_root / 'imported'
        os.symlink(outside, category, target_is_directory=True)
        with self.assertRaises(pa.LoadoutError):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])
        category.unlink()
        plan = self.plan()
        os.symlink(outside, category, target_is_directory=True)
        result = self.apply(plan)
        self.assertEqual(result['adopted'], 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.core.config_path.exists() and 'new-skill' in pa.parse_disabled(self.core.config_path.read_text()))

    def test_review_includes_support_files_not_only_markdown_and_is_one_shot(self):
        script = self.skill / 'helper.py'; script.write_text('print(1)\n')
        plan = self.plan()
        script.write_text('print(2)\n')
        result = self.apply(plan)
        self.assertEqual(result['adopted'], 0)
        self.assertEqual(result['results'][0]['code'], 'changed-since-preview')
        self.assertFalse((self.core.skills_root / 'imported/new-skill').exists())
        with self.assertRaises(pa.LoadoutError):
            self.apply(plan)

    def test_unreviewed_entry_or_modified_category_cannot_be_added_to_apply(self):
        plan = self.plan()
        with self.assertRaises(pa.LoadoutError):
            self.core.import_apply_plan([{'name': 'other', 'source': str(self.source), 'tool': None}], plan_id=plan['plan_id'])
        plan = self.plan()
        with self.assertRaises(pa.LoadoutError):
            self.core.import_apply_plan(plan['adoptable'], 'elsewhere', plan_id=plan['plan_id'])
        self.assertFalse((self.core.skills_root / 'elsewhere').exists())

    def test_copy_failure_preserves_source_and_leaves_no_active_import(self):
        plan = self.plan()
        with patch.object(pa.shutil, 'copytree', side_effect=OSError('disk full')):
            result = self.apply(plan)
        self.assertEqual(result['adopted'], 0)
        self.assertTrue(self.skill.is_dir())
        self.assertFalse((self.core.skills_root / 'imported/new-skill').exists())
        self.assertNotIn('new-skill', pa.parse_disabled(self.core.config_path.read_text()))

    def test_external_support_link_is_not_copied_or_followed(self):
        private = self.fx.tmp / 'private'; private.write_text('do not read')
        os.symlink(private, self.skill / 'data')
        plan = self.plan()
        self.assertFalse(plan['adoptable'])
        self.assertEqual(private.read_text(), 'do not read')


class CurrentBackupTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(); self.addCleanup(self.fx.cleanup)
        self.core = self.fx.core
        self.path = pa.user_config_path(self.fx.home)
        self.path.write_text(json.dumps({'schema_version': 1, 'tools': {}, 'keep': 1}), encoding='utf-8')

    def test_current_save_list_restore_roundtrip_and_candidate_validation(self):
        before = self.path.read_bytes()
        result = self.core.set_tool('fixture-client', 'Fixture', str(self.fx.tmp / 'target'))
        backup = Path(result['backup'])
        self.assertIn('.bak.hermes-loadout.', backup.name)
        self.assertIn(str(backup), [r['path'] for r in self.core.list_backups()['backups']])
        self.core.restore_backup(str(backup))
        self.assertEqual(self.path.read_bytes(), before)
        bad = self.path.with_name(self.path.name + '.bak.hermes-loadout.20260909-121212')
        bad.write_text('{broken')
        stable = self.path.read_bytes()
        with self.assertRaises(pa.LoadoutError): self.core.restore_backup(str(bad))
        self.assertEqual(self.path.read_bytes(), stable)
        bad.unlink(); os.symlink(self.path, bad)
        with self.assertRaises(pa.LoadoutError): self.core.restore_backup(str(bad))
        self.assertEqual(self.path.read_bytes(), stable)


if __name__ == '__main__': unittest.main()

class CatalogBypassTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(); self.addCleanup(self.fx.cleanup)
        self.core = self.fx.core
        self.link = self.fx.codex / 'shared'
        self.link.symlink_to(self.core.skills_root, target_is_directory=True)

    def test_bypass_is_visible_cancellation_is_read_only_and_repair_is_exact(self):
        rows = self.core.diff()['catalog_bypasses']
        self.assertEqual([(r['tool'], r['name']) for r in rows], [('codex', 'shared')])
        tool = next(t for t in self.core.state()['tools'] if t['id'] == 'codex')
        self.assertTrue(tool['read_only'])
        with self.assertRaises(pa.LoadoutError):
            self.core.toggle('apple/apple-notes', 'codex', False)
        plan = self.core.plan_catalog_repair('codex', 'shared')
        self.assertTrue(self.link.is_symlink(), 'Preview/cancel cannot change the link')
        self.core.repair_catalog(plan['plan_id'])
        self.assertFalse(self.link.is_symlink())
        self.assertTrue(self.core.skills_root.is_dir())
        self.assertEqual(self.core.catalog_bypasses(), [])
        self.core.toggle('apple/apple-notes', 'codex', True)
        self.core.toggle('apple/apple-notes', 'codex', False)
        self.assertFalse((self.fx.codex / 'apple-notes').exists())

    def test_foreign_or_real_replacement_between_review_and_repair_is_preserved(self):
        plan = self.core.plan_catalog_repair('codex', 'shared')
        self.link.unlink(); self.link.mkdir()
        (self.link / 'keep').write_text('owner data')
        with self.assertRaises(pa.LoadoutError): self.core.repair_catalog(plan['plan_id'])
        self.assertEqual((self.link / 'keep').read_text(), 'owner data')
        with self.assertRaises(pa.LoadoutError): self.core.plan_catalog_repair('codex', 'shared')

    def test_import_cannot_activate_through_an_unrepaired_broad_link(self):
        root = self.fx.tmp / 'source'; skill = root / 'new-item'; skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: new-item\ndescription: fixture\n---\n')
        plan = self.core.import_plan([], [str(root)])
        result = self.core.import_apply_plan(plan['adoptable'], plan_id=plan['plan_id'])
        self.assertEqual(result['results'][0]['code'], 'catalog-bypass')
        self.assertFalse((self.core.skills_root / 'imported/new-item').exists())
        self.assertTrue(self.link.is_symlink())
