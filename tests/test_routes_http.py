"""Exercise the mounted, reviewed API against disposable client files."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa, SKILL_MD_TEMPLATE
from test_mcp_backend import CONFIG
from isolation import bind_test_cores

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, 'HTTP dependencies are optional in the Python 3.9 core gate')
class RouteRoundTrip(unittest.TestCase):
    PREFIX = '/api/plugins/hermes-loadout'

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        mcp = pa.McpCore(self.fx.home, codex_config=self.fx.tmp / 'client.toml',
                                  claude_desktop_config=self.fx.tmp / 'client.json')
        binding = bind_test_cores(pa, self.fx.core, mcp)
        binding.__enter__()
        self.addCleanup(binding.__exit__, None, None, None)
        app = FastAPI()
        app.include_router(pa.router, prefix=self.PREFIX)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def get(self, path, **params):
        response = self.client.get(self.PREFIX + path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def post(self, route, **body):
        response = self.client.post(self.PREFIX + route, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def skill(app='codex', enabled=True, identity='apple/apple-notes'):
        return {'kind': 'skill', 'app': app, 'id': identity, 'enabled': enabled}

    def apply(self, states):
        preview = self.post('/operations/plan', states=states)
        self.assertTrue(preview['ok'], preview)
        return self.post('/operations/apply', plan_id=preview['plan_id'])

    def test_read_only_state_health_detail_and_diagnostics(self):
        self.assertEqual(self.get('/health')['plugin'], 'hermes-loadout')
        state = self.get('/state')
        self.assertEqual(state['counts']['skills'], 7)
        self.assertEqual(state['capabilities']['reviewed_operations'], 1)
        self.assertIn('# apple-notes', self.get('/detail', skill='apple/apple-notes')['markdown'])
        self.assertEqual(self.get('/detail', skill='../../etc/passwd')['code'], 'invalid-skill')
        self.assertIn('media/orphan-skill', self.get('/diff')['unlinked'])
        self.assertTrue(self.get('/drift')['ok'])
        self.assertFalse((self.fx.home / 'data/hermes-loadout/last-operation.json').exists())

    def test_only_reviewed_activation_and_restore_routes_are_exposed(self):
        removed = ['/toggle', '/toggle-bulk', '/bulk/apply', '/bulk/undo', '/repair', '/repair-all',
                   '/ensure-tool-dir', '/mcp/toggle', '/mcp/sync', '/mcp/remove', '/mcp/codex/sync',
                   '/mcp/codex/remove', '/blueprint/apply', '/conflict/revert-push', '/conflict/revert-pull',
                   '/conflict/revert-adopt', '/conflict/pull', '/conflict/keep-both', '/drift/push', '/import/apply']
        for path in removed:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(self.PREFIX + path, json={}).status_code, 404)
        self.assertFalse((self.fx.codex / 'apple-notes').exists())
        for path in ['/operations/apply', '/operations/undo', '/catalog-bypass/repair', '/conflict/apply', '/backups/restore']:
            with self.subTest(path=path):
                self.assertFalse(self.post(path)['ok'])

    def test_preview_cancel_exact_selection_and_replayed_plan(self):
        states = [self.skill(), self.skill(identity='résearch/arxiv')]
        preview = self.post('/operations/plan', states=states)
        self.assertFalse((self.fx.codex / 'apple-notes').is_symlink())
        self.assertIsNone(self.get('/operations/latest')['receipt'])
        applied = self.post('/operations/apply', plan_id=preview['plan_id'], states=[self.skill(identity='media/orphan-skill')])
        self.assertEqual(applied['changed'], 2, applied)
        self.assertFalse((self.fx.codex / 'orphan-skill').is_symlink())
        self.assertFalse(self.post('/operations/apply', plan_id=preview['plan_id'])['ok'])
        self.assertEqual(self.apply(states)['changed'], 0)

    def test_independent_activation_and_persisted_previewed_undo(self):
        config = self.fx.core.config_path.read_bytes()
        self.assertEqual(self.apply([self.skill()])['changed'], 1)
        self.assertEqual(self.fx.core.config_path.read_bytes(), config)
        pa._LOADOUT_SERVICE = None
        self.assertTrue(self.get('/operations/latest')['receipt']['undo_available'])
        undo = self.post('/operations/undo-plan')
        self.assertTrue((self.fx.codex / 'apple-notes').is_symlink())
        self.assertEqual(self.post('/operations/undo', plan_id=undo['plan_id'])['changed'], 1)
        self.assertFalse((self.fx.codex / 'apple-notes').is_symlink())
        self.assertEqual(self.apply([self.skill(app='hermes', enabled=False)])['changed'], 1)
        self.assertIn('apple-notes', pa.parse_disabled(self.fx.core.config_path.read_text()))
        self.assertFalse((self.fx.codex / 'apple-notes').is_symlink())

    def test_partial_refusal_and_external_change_are_not_successful_items(self):
        preview = self.post('/operations/plan', states=[self.skill(), self.skill(identity='résearch/arxiv')])
        entry = self.fx.codex / 'apple-notes'
        entry.mkdir()
        (entry / 'owner.txt').write_text('preserve')
        result = self.post('/operations/apply', plan_id=preview['plan_id'])
        self.assertEqual(result['changed'], 1, result)
        self.assertTrue(result['partial'])
        self.assertEqual(result['receipt']['failed'], 1)
        self.assertEqual((entry / 'owner.txt').read_text(), 'preserve')
        self.assertTrue((self.fx.codex / 'arxiv').is_symlink())

    def test_repair_is_a_reviewed_selection_with_the_same_receipt(self):
        before = os.readlink(self.fx.grok / 'rem índéluxé')
        result = self.apply([self.skill('grok', True, 'apple/rem índéluxé')])
        self.assertEqual(result['changed'], 1, result)
        self.assertTrue((self.fx.grok / 'rem índéluxé').exists())
        undo = self.post('/operations/undo-plan')
        self.assertEqual(self.post('/operations/undo', plan_id=undo['plan_id'])['changed'], 1)
        self.assertEqual(os.readlink(self.fx.grok / 'rem índéluxé'), before)

    def test_folder_only_import_is_off_in_hermes_and_undo_preserves_original(self):
        root = self.fx.tmp / 'folder'
        source = root / 'new-skill'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text(SKILL_MD_TEMPLATE.format(name='new-skill', desc='From a folder'), encoding='utf-8')
        original = (source / 'SKILL.md').read_bytes()
        preview = self.post('/import/plan', tools=[], scan_roots=[str(root)], category='imports')
        self.assertTrue(preview['ok'], preview)
        result = self.post('/import/apply-plan', plan_id=preview['plan_id'], entries=preview['adoptable'], category='imports')
        self.assertEqual(result['adopted'], 1, result)
        self.assertEqual(self.get('/inventory/metadata')['skills']['imports/new-skill']['source'], 'Folder import')
        self.assertIn('new-skill', pa.parse_disabled(self.fx.core.config_path.read_text()))
        self.assertFalse((self.fx.codex / 'new-skill').exists())
        undo = self.post('/operations/undo-plan')
        self.assertEqual(self.post('/operations/undo', plan_id=undo['plan_id'])['changed'], 1)
        self.assertEqual((source / 'SKILL.md').read_bytes(), original)
        self.assertFalse((self.fx.home / 'skills/imports/new-skill').exists())

    def test_source_app_import_preserves_existing_app_and_requires_content_review(self):
        source = self.fx.codex / 'http-skill'
        source.mkdir()
        (source / 'SKILL.md').write_text(SKILL_MD_TEMPLATE.format(name='http-skill', desc='Existing app'), encoding='utf-8')
        self.assertTrue(self.get('/import/scan')['ok'])
        preview = self.post('/import/plan', tools=['codex'])
        entries = [row for row in preview['adoptable'] if row['name'] == 'http-skill']
        result = self.post('/import/apply-plan', plan_id=preview['plan_id'], entries=entries)
        self.assertEqual(result['adopted'], 1, result)
        self.assertTrue(source.is_symlink())
        self.assertIn('http-skill', pa.parse_disabled(self.fx.core.config_path.read_text()))
        self.assertTrue(self.post('/config/tools', id='custom', label='Custom', dir=str(self.fx.tmp / 'custom'))['ok'])
        self.assertFalse(self.post('/config/tools', id='hermes', label='No', dir=str(self.fx.tmp / 'bad'))['ok'])

    def test_loadout_crud_capture_mcp_and_labels_do_not_activate_until_confirmed(self):
        self.fx.core.config_path.write_text(CONFIG, encoding='utf-8')
        before = self.fx.core.config_path.read_bytes()
        captured = self.post('/loadouts/capture', apps=['hermes', 'codex'])
        self.assertTrue(captured['ok'], captured)
        self.assertTrue(any(row['kind'] == 'mcp' for row in captured['states']))
        selections = [self.skill(), {'kind': 'mcp', 'app': 'hermes', 'id': 'local_stdio', 'enabled': False}]
        # Use an actual fixture server, not a hard-coded assumption about a client.
        selections[1]['id'] = self.get('/mcp/state')['rows'][0]['name']
        record = self.post('/loadouts/save', name='Research', states=selections)['loadout']
        self.assertTrue(self.post('/loadouts/edit', loadout_id=record['id'], action='rename', name='Writing')['ok'])
        duplicate = self.post('/loadouts/edit', loadout_id=record['id'], action='duplicate')['loadout']
        self.assertNotEqual(duplicate['id'], record['id'])
        self.assertTrue(self.post('/inventory/classify', skill='apple/apple-notes', classification='Hermes-specific')['ok'])
        self.assertEqual(self.get('/inventory/metadata')['skills']['apple/apple-notes']['classification'], 'Hermes-specific')
        self.assertEqual(self.fx.core.config_path.read_bytes(), before)
        self.assertFalse((self.fx.codex / 'apple-notes').is_symlink())
        preview = self.post('/loadouts/plan', loadout_id=record['id'])
        result = self.post('/operations/apply', plan_id=preview['plan_id'])
        self.assertGreaterEqual(result['changed'], 1, result)
        self.assertTrue((self.fx.codex / 'apple-notes').is_symlink())
        self.assertTrue(self.post('/loadouts/edit', loadout_id=record['id'], action='delete')['ok'])
        self.assertEqual(len(self.get('/loadouts')['loadouts']), 1)

    def test_unreadable_hermes_config_is_visible_over_http(self):
        self.fx.core.config_path.write_text(CONFIG, encoding='utf-8')
        original_open = Path.open
        target = self.fx.core.config_path

        def denied(path, mode='r', *args, **kwargs):
            if path == target and 'r' in mode:
                raise PermissionError('fixture read permission denied')
            return original_open(path, mode, *args, **kwargs)

        from unittest.mock import patch
        with patch.object(Path, 'open', denied):
            state = self.get('/state')
            self.assertTrue(state['ok'], state)
            self.assertEqual(state['config']['code'], 'config-unreadable')
            self.assertEqual(state['skills'][0]['tools']['hermes']['state'], 'config-unreadable')
            mcp = self.get('/mcp/state')
            self.assertTrue(mcp['partial_failure'], mcp)
            self.assertEqual(mcp['catalog_error']['code'], 'config-unreadable')
            captured = self.post('/loadouts/capture', apps=['hermes'])
            self.assertTrue(captured['ok'], captured)
            self.assertFalse(captured['states'])
            self.assertTrue(captured['excluded'])

    def test_bypass_conflict_and_backup_preview_apply_undo_over_http(self):
        link = self.fx.codex / 'shared'
        link.symlink_to(self.fx.core.skills_root, target_is_directory=True)
        review = self.post('/catalog-bypass/plan', tool='codex', name='shared')
        self.assertTrue(link.is_symlink())
        self.assertTrue(self.post('/catalog-bypass/repair', plan_id=review['plan_id'])['ok'])
        self.assertFalse(link.is_symlink())
        external = self.fx.codex / 'apple-notes'
        external.mkdir()
        (external / 'SKILL.md').write_text(SKILL_MD_TEMPLATE.format(name='apple-notes', desc='Other'), encoding='utf-8')
        plan = self.post('/conflict/plan', tool='codex', name='apple-notes', choice='library')
        self.assertFalse(external.is_symlink())
        self.assertEqual(self.post('/conflict/apply', plan_id=plan['plan_id'])['changed'], 1)
        backup = next(row for row in self.get('/backups')['backups'] if row['kind'] == 'tool-link')
        plan = self.post('/backups/plan', path=backup['path'])
        self.assertEqual(self.post('/backups/restore', plan_id=plan['plan_id'])['changed'], 1)
        self.assertFalse(external.is_symlink())
        undo = self.post('/operations/undo-plan')
        self.assertEqual(self.post('/operations/undo', plan_id=undo['plan_id'])['changed'], 1)
        self.assertTrue(external.is_symlink())

    def test_malformed_requests_fail_without_mutation_or_sensitive_error_text(self):
        before = self.fx.core.config_path.read_bytes()
        for path, body in [('/operations/plan', {'states': [{'command': 'secret-fixture'}]}),
                           ('/loadouts/save', {'name': [], 'states': []}),
                           ('/operations/apply', {'plan_id': '../escape'}),
                           ('/loadouts/capture', {'apps': ['nonexistent']}),
                           ('/inventory/classify', {'skill': '../escape', 'classification': 'Portable'}),
                           ('/backups/plan', {'path': '/outside/unreviewed'})]:
            with self.subTest(path=path):
                result = self.post(path, **body)
                self.assertFalse(result['ok'], result)
                self.assertNotIn('secret-fixture', json.dumps(result))
        self.assertEqual(self.fx.core.config_path.read_bytes(), before)
        self.assertFalse((self.fx.home / 'data/hermes-loadout/last-operation.json').exists())

    def test_mutations_logged_without_receipt_secret_values(self):
        self.apply([self.skill()])
        log = self.fx.tmp / 'data/mutations.log'
        self.assertTrue(log.is_file())
        self.assertIn('"action": "toggle"', log.read_text())


if __name__ == '__main__':
    unittest.main()
