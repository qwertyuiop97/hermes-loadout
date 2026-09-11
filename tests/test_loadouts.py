"""Named selections exercise real temporary skill and MCP files, never a live profile."""
from __future__ import annotations
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa, SKILL_MD_TEMPLATE
from isolation import bind_test_cores
from test_mcp_backend import CONFIG, needs_yaml

spec = importlib.util.spec_from_file_location('loadout_service_tests', Path(__file__).resolve().parents[1] / 'dashboard/loadout_service.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def selection(kind='skill', app='codex', identity='apple/apple-notes', enabled=True):
    return {'kind': kind, 'app': app, 'id': identity, 'enabled': enabled}


@needs_yaml
class LoadoutTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.fx.core.config_path.write_text(CONFIG, encoding='utf-8')
        self.codex = self.fx.tmp / 'codex-config.toml'
        self.claude = self.fx.tmp / 'claude-config.json'
        self.mcp = pa.McpCore(self.fx.home, codex_config=self.codex, claude_desktop_config=self.claude)
        self.service = module.LoadoutService(self.fx.core, self.mcp, pa)

    def tearDown(self):
        self.fx.cleanup()

    def apply(self, states):
        plan = self.service.plan(states)
        return self.service.apply(plan['plan_id'])

    def undo(self):
        return self.service.undo(self.service.undo_plan()['plan_id'])

    def test_crud_is_secret_free_and_never_activates(self):
        initial = self.fx.core.config_path.read_bytes()
        result = self.service.save_loadout('Research', [selection(), selection('mcp', 'codex', 'weather')])
        identity = result['loadout']['id']
        self.service.edit_loadout(identity, 'rename', 'Writing')
        duplicate = self.service.edit_loadout(identity, 'duplicate')['loadout']
        self.assertNotEqual(identity, duplicate['id'])
        self.assertEqual(duplicate['states'], result['loadout']['states'])
        self.service.edit_loadout(identity, 'delete')
        self.assertEqual(len(self.service.list_loadouts()['loadouts']), 1)
        self.assertFalse((self.fx.codex / 'apple-notes').exists())
        self.assertFalse(self.codex.exists())
        self.assertEqual(initial, self.fx.core.config_path.read_bytes())
        self.assertNotIn('sk-test-123', self.service._path('loadouts.json').read_text())
        with self.assertRaises(pa.LoadoutError):
            self.service.save_loadout('Secret', [{**selection(), 'env': {'KEY': 'secret'}}])

    def test_loadout_skill_and_mcp_apply_undo_preserves_other_apps_and_fields(self):
        self.codex.write_text('model = "owner-choice"\n\n[mcp_servers.foreign]\ncommand = "owner-server"\n', encoding='utf-8')
        states = [selection(), selection('skill', 'hermes', 'apple/apple-notes', False),
                  selection('mcp', 'codex', 'weather', True), selection('mcp', 'hermes', 'weather', True)]
        record = self.service.save_loadout('Research', states)['loadout']
        plan = self.service.plan_loadout(record['id'])
        self.assertEqual(plan['counts']['enable'], 3)
        self.assertEqual(plan['counts']['disable'], 1)
        result = self.service.apply(plan['plan_id'])
        self.assertEqual(result['changed'], 4, result)
        self.assertIn('apple-notes', self.fx.core._disabled_set())
        self.assertTrue((self.fx.codex / 'apple-notes').is_symlink())
        self.assertFalse((self.fx.claude / 'apple-notes').exists())
        text = self.codex.read_text()
        self.assertIn('owner-server', text)
        self.codex.write_text(text + '\n# unrelated subsequent owner comment\n', encoding='utf-8')
        saved = self.service._path('last-operation.json').read_text()
        self.assertNotIn('sk-test-123', saved)
        self.service = module.LoadoutService(self.fx.core, self.mcp, pa)  # reopened
        preview = self.service.undo_plan()
        self.assertEqual(preview['counts']['restore'], 4, preview)
        undone = self.service.undo(preview['plan_id'])
        self.assertEqual(undone['receipt']['changed'], 4, undone)
        self.assertFalse((self.fx.codex / 'apple-notes').exists())
        self.assertNotIn('apple-notes', self.fx.core._disabled_set())
        self.assertNotIn('weather', pa.parse_codex_mcp(self.codex.read_text()))
        self.assertIn('subsequent owner comment', self.codex.read_text())
        self.assertFalse(self.service.latest()['receipt']['undo_available'])

    def test_native_disabled_mcp_flag_is_explicitly_enabled_and_restored(self):
        self.mcp.sync_to_codex('weather')
        doc = pa._toml_support().load(self.codex.read_text())
        doc['mcp_servers']['weather'].update(enabled=False, startup_timeout_sec=41)
        raw = pa._toml_support().replace_server(self.codex.read_text(), 'weather', doc['mcp_servers']['weather'])
        self.codex.write_text(raw, encoding='utf-8')
        result = self.apply([selection('mcp', 'codex', 'weather', True)])
        self.assertEqual(result['changed'], 1, result)
        current = pa._toml_support().load(self.codex.read_text())['mcp_servers']['weather']
        self.assertTrue(current.get('enabled', True))
        self.assertEqual(current['startup_timeout_sec'], 41)
        self.undo()
        self.assertEqual(pa._toml_support().load(self.codex.read_text())['mcp_servers']['weather'], doc['mcp_servers']['weather'])

    def test_preview_binds_definition_and_external_files_and_reports_partial_failure(self):
        record = self.service.save_loadout('Small', [selection(), selection('skill', 'grok', 'apple/apple-notes')])['loadout']
        plan = self.service.plan_loadout(record['id'])
        self.assertEqual(plan['counts']['protected'], 1, plan)
        changed = self.service.apply(plan['plan_id'])
        self.assertEqual(changed['changed'], 1)
        self.assertEqual(changed['receipt']['failed'], 1)
        self.assertTrue(changed['receipt']['undo_available'])
        plan = self.service.plan_loadout(record['id'])
        self.service.edit_loadout(record['id'], 'rename', 'New name')
        with self.assertRaises(pa.LoadoutError): self.service.apply(plan['plan_id'])
        self.fx.core.toggle('apple/apple-notes', 'codex', False)
        plan = self.service.plan([selection()])
        (self.fx.codex / 'apple-notes').mkdir()
        (self.fx.codex / 'apple-notes' / 'owner.txt').write_text('keep')
        refused = self.service.apply(plan['plan_id'])
        self.assertEqual(refused['changed'], 0)
        self.assertEqual((self.fx.codex / 'apple-notes' / 'owner.txt').read_text(), 'keep')
        self.assertEqual(self.service.latest()['receipt']['receipt_id'], changed['receipt']['receipt_id'])

    def test_undo_preview_refuses_owner_replacement_and_missing_mcp_backup(self):
        self.apply([selection(), selection('mcp', 'claude-desktop', 'weather')])
        link = self.fx.codex / 'apple-notes'
        link.unlink()
        link.mkdir()
        (link / 'owner.txt').write_text('owner')
        plan = self.service.undo_plan()
        self.assertEqual(plan['counts']['restore'], 1, plan)
        self.assertEqual(plan['counts']['protected'], 1, plan)
        result = self.service.undo(plan['plan_id'])
        self.assertEqual(result['receipt']['changed'], 1)
        self.assertTrue((link / 'owner.txt').exists())
        self.assertEqual(json.loads(self.claude.read_text())['mcpServers'], {})

    def test_store_failure_is_preflight_and_postwrite_interruption_remains_visible(self):
        plan = self.service.plan([selection()])
        with patch.object(self.service, '_write', side_effect=OSError('read only')):
            with self.assertRaises(OSError): self.service.apply(plan['plan_id'])
        self.assertFalse((self.fx.codex / 'apple-notes').exists())
        plan = self.service.plan([selection()])
        real_write = self.service._write
        def fail_after_write(name, data):
            if any(item.get('status') == 'completed' for item in data.get('items', [])):
                raise OSError('disk full')
            return real_write(name, data)
        with patch.object(self.service, '_write', side_effect=fail_after_write):
            result = self.service.apply(plan['plan_id'])
        self.assertFalse(result['ok'])
        self.assertTrue((self.fx.codex / 'apple-notes').is_symlink())
        self.assertTrue(self.service.latest()['recovery_required'])
        with self.assertRaises(pa.LoadoutError): self.service.undo_plan()

    def test_shared_target_cannot_request_opposite_activation(self):
        self.fx.core.tools['other'] = {'dir': self.fx.codex, 'label': 'Other'}
        plan = self.service.plan([selection(), selection('skill', 'other', 'apple/apple-notes', False)])
        self.assertEqual(plan['counts']['conflict'], 2)
        self.assertEqual(self.service.apply(plan['plan_id'])['changed'], 0)
        self.assertFalse((self.fx.codex / 'apple-notes').exists())

    def test_import_and_bypass_repair_support_reviewed_restart_safe_undo(self):
        source = self.fx.tmp / 'sources'
        skill = source / 'new-skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text(SKILL_MD_TEMPLATE.format(name='new-skill', desc='Test'), encoding='utf-8')
        plan = self.fx.core.import_plan([], [str(source)])
        result = self.service.apply_import(plan['adoptable'], 'imported', plan['plan_id'])
        self.assertTrue(result['operation']['undo_available'], result)
        self.assertEqual(self.service.undo_plan()['counts']['restore'], 1)
        self.undo()
        self.assertTrue(skill.exists())
        self.assertFalse((self.fx.home / 'skills/imported/new-skill').exists())
        shared = self.fx.codex / 'shared'
        os.symlink(self.fx.core.skills_root, shared, target_is_directory=True)
        plan = self.fx.core.plan_catalog_repair('codex', 'shared')
        receipt = self.service.apply_catalog_repair(plan['plan_id'])
        self.assertTrue(receipt['receipt']['undo_available'])
        self.assertFalse(shared.exists())
        self.undo()
        self.assertTrue(shared.is_symlink())
        self.assertEqual(len(self.fx.core.catalog_bypasses('codex')), 1)

    def test_capture_is_observe_only_and_handles_hermes_without_external_tools(self):
        before = self.fx.core.config_path.read_bytes()
        captured = self.service.capture(['hermes'])
        self.assertTrue(captured['states'])
        self.assertTrue(all(row['app'] == 'hermes' for row in captured['states']))
        self.assertEqual(before, self.fx.core.config_path.read_bytes())
        self.assertFalse(self.service._path('loadouts.json').exists())
        self.fx.core.tools['hermes']['scope'] = 'global'
        result = self.apply([selection('skill', 'hermes', 'apple/apple-notes', False)])
        self.assertEqual(result['changed'], 1, result)

    def test_unreadable_hermes_config_blocks_fabricated_skill_and_mcp_states(self):
        original_open = Path.open
        target = self.fx.core.config_path

        def denied(path, mode='r', *args, **kwargs):
            if path == target and 'r' in mode:
                raise PermissionError('fixture read permission denied')
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', denied):
            state = self.fx.core.state()
            hermes = state['skills'][0]['tools']['hermes']
            self.assertEqual(hermes['state'], 'config-unreadable')
            captured = self.service.capture(['hermes'])
            self.assertFalse(captured['states'])
            self.assertTrue(captured['excluded'])
            plan = self.service.plan([selection('skill', 'hermes', 'apple/apple-notes', False)])
            self.assertEqual(plan['counts']['unavailable'], 1, plan)
            mcp_plan = self.service.plan([selection('mcp', 'hermes', 'weather', True)])
            self.assertEqual(mcp_plan['counts']['unavailable'], 1, mcp_plan)

    def test_hermes_config_decoding_errors_block_fabricated_states(self):
        self.fx.core.config_path.write_bytes(b'\xff\xfe\x00not utf8')
        state = self.fx.core.state()
        self.assertEqual(state['config']['code'], 'config-unreadable')
        self.assertEqual(state['skills'][0]['tools']['hermes']['state'], 'config-unreadable')
        captured = self.service.capture(['hermes'])
        self.assertFalse(captured['states'])
        self.assertTrue(captured['excluded'])
        mcp_state = self.mcp.mcp_state()
        self.assertTrue(mcp_state['partial_failure'])
        self.assertEqual(mcp_state['catalog_error']['code'], 'config-unreadable')

    def test_generated_operation_label_accepts_long_valid_skill_name(self):
        long_name = 'a' * 60
        skill_dir = self.fx.home / 'skills' / 'long' / long_name
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text(
            SKILL_MD_TEMPLATE.format(name=long_name, desc='Long but valid for client validation'),
            encoding='utf-8',
        )
        self.fx.core.invalidate()
        long_id = f'long/{long_name}'
        enable_label = 'Enable ' + long_name
        disable_label = 'Disable ' + long_name
        self.assertGreater(len(enable_label), 64)
        enabled_plan = self.service.plan([selection('skill', 'codex', long_id, True)], label=enable_label)
        self.assertTrue(enabled_plan['ok'])
        result = self.service.apply(enabled_plan['plan_id'])
        self.assertEqual(result['changed'], 1, result)
        disabled_plan = self.service.plan([selection('skill', 'codex', long_id, False)], label=disable_label)
        self.assertTrue(disabled_plan['ok'])
        self.assertEqual(self.service.apply(disabled_plan['plan_id'])['changed'], 1)
        with self.assertRaises(pa.LoadoutError):
            self.service.save_loadout('x' * 65, [])

    def test_undo_scope_cannot_grow_after_preview(self):
        states = [selection(), selection('skill', 'codex', 'media/orphan-skill')]
        self.apply(states)
        entry = self.fx.codex / 'orphan-skill'
        original = os.readlink(entry)
        entry.unlink()
        entry.mkdir()
        preview = self.service.undo_plan()
        self.assertEqual(preview['counts']['restore'], 1)
        entry.rmdir()
        os.symlink(original, entry, target_is_directory=True)
        self.service.undo(preview['plan_id'])
        self.assertFalse((self.fx.codex / 'apple-notes').exists())
        self.assertTrue(entry.is_symlink(), 'An entry excluded from preview must not be undone later.')

    def test_invalid_saved_receipt_is_rejected_before_any_undo(self):
        self.apply([selection(), selection('skill', 'codex', 'media/orphan-skill')])
        record = self.service._read('last-operation.json', {})
        record['items'][1]['kind'] = 'unknown'
        self.service._write('last-operation.json', record)
        with self.assertRaises(pa.LoadoutError): self.service.undo_plan()
        self.assertTrue((self.fx.codex / 'apple-notes').is_symlink())
        self.assertTrue((self.fx.codex / 'orphan-skill').is_symlink())

    def test_import_from_active_app_undo_and_classification_never_change_other_activation(self):
        skill = self.fx.codex / 'new-skill'
        skill.mkdir()
        (skill / 'SKILL.md').write_text(SKILL_MD_TEMPLATE.format(name='new-skill', desc='Test'), encoding='utf-8')
        original = (skill / 'SKILL.md').read_bytes()
        plan = self.fx.core.import_plan(['codex'], [])
        result = self.service.apply_import(plan['adoptable'], 'imported', plan['plan_id'])
        self.assertTrue(result['operation']['undo_available'], result)
        self.assertIn('new-skill', self.fx.core._disabled_set())
        before = self.fx.core.config_path.read_bytes()
        self.service.classify('imported/new-skill', 'Hermes-specific')
        self.assertEqual(before, self.fx.core.config_path.read_bytes())
        self.assertEqual((skill / 'SKILL.md').read_bytes(), original)
        self.assertTrue(skill.is_symlink())
        preview = self.service.undo_plan()
        self.assertEqual(preview['counts']['restore'], 1, preview)
        self.service.undo(preview['plan_id'])
        self.assertFalse(skill.is_symlink())
        self.assertEqual((skill / 'SKILL.md').read_bytes(), original)
        self.assertFalse((self.fx.home / 'skills/imported/new-skill').exists())
        self.assertNotIn('new-skill', self.fx.core._disabled_set())

    def test_mcp_refusals_and_receipt_failures_never_leak_values(self):
        self.codex.write_text('[mcp_servers.weather]\ncommand = "private-owner-command"\n', encoding='utf-8')
        preview = self.service.plan([selection('mcp', 'codex', 'weather')])
        self.assertEqual(preview['counts']['conflict'], 1)
        raw = self.codex.read_bytes()
        result = self.service.apply(preview['plan_id'])
        self.assertEqual(result['changed'], 0)
        self.assertEqual(self.codex.read_bytes(), raw)
        self.assertNotIn('private-owner-command', json.dumps(result))
        self.codex.unlink()
        preview = self.service.plan([selection('mcp', 'codex', 'weather')])
        setter = self.mcp.set_activation
        def interrupted(*args):
            setter(*args)
            raise OSError('after write')
        with patch.object(self.mcp, 'set_activation', side_effect=interrupted):
            result = self.service.apply(preview['plan_id'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'recovery-required')
        self.assertTrue(self.service.latest()['recovery_required'])
        self.assertNotIn('sk-test-123', self.service._path('pending-operation.json').read_text())

    def test_http_save_preview_apply_and_undo_use_real_adapter_files(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest('HTTP dependencies are optional in the Python 3.9 core gate')
        binding = bind_test_cores(pa, self.fx.core, self.mcp)
        binding.__enter__()
        self.addCleanup(binding.__exit__, None, None, None)
        app = FastAPI()
        app.include_router(pa.router, prefix='/api/plugins/hermes-loadout')
        client = TestClient(app)
        prefix = '/api/plugins/hermes-loadout'
        try:
            record = client.post(prefix + '/loadouts/save', json={'name': 'Coding', 'states': [selection()]}).json()
            self.assertTrue(record['ok'], record)
            self.assertFalse((self.fx.codex / 'apple-notes').exists())
            planned = client.post(prefix + '/loadouts/plan', json={'loadout_id': record['loadout']['id']}).json()
            applied = client.post(prefix + '/operations/apply', json={'plan_id': planned['plan_id']}).json()
            self.assertEqual(applied['changed'], 1, applied)
            latest = client.get(prefix + '/operations/latest').json()
            self.assertTrue(latest['receipt']['undo_available'], latest)
            review = client.post(prefix + '/operations/undo-plan', json={}).json()
            result = client.post(prefix + '/operations/undo', json={'plan_id': review['plan_id']}).json()
            self.assertEqual(result['receipt']['changed'], 1, result)
            self.assertFalse((self.fx.codex / 'apple-notes').exists())
            rejected = client.post(prefix + '/operations/plan', json={'states': [{'env': {'TOKEN': 'never-store'}}]}).json()
            self.assertFalse(rejected['ok'])
            self.assertNotIn('never-store', str(rejected))
        finally:
            client.close()

if __name__ == '__main__': unittest.main()
