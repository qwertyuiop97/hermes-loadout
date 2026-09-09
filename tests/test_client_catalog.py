"""Catalog contracts and explicit, contained Global/Project target activation."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import pa


class ScopedTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='switchboard-scope-')
        self.root = Path(self.tmp.name).resolve()
        self.user = self.root / 'user'
        self.user.mkdir()
        self.home = self.user / '.hermes'
        self.home.mkdir()
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        self.other = self.root / 'other'
        self.other.mkdir()
        self.env = patch.object(pa.Path, 'home', return_value=self.user)
        self.env.start()
        self.core = self.rebuild()
        self.skill = self.home / 'skills' / 'development' / 'example'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('---\nname: example\ndescription: Test skill\n---\n')

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def rebuild(self):
        return pa.SkillsToggleCore(self.home, pa.load_tools_config(self.home))

    def selection(self, **kwargs):
        return {'client_id': 'cursor', 'scope': 'project', 'project_root': str(self.project), **kwargs}

    def activate(self, selection=None):
        selection = selection or self.selection()
        preview = self.core.preview_target(selection)
        result = self.core.activate_target(selection, preview['preview_id'])
        self.core = self.rebuild()
        return preview, result

    def assert_refused(self, fn, *args):
        with self.assertRaises(pa.SkillsToggleError) as raised:
            fn(*args)
        return raised.exception.code

    def test_catalog_is_one_evidenced_data_source(self):
        catalog = self.core.client_catalog()
        self.assertEqual(catalog['catalog_version'], 1)
        for entry in catalog['clients']:
            self.assertIn('skills', entry['capabilities'])
            self.assertIn('mcp_writer', entry['capabilities'])
            self.assertIn('platforms', entry)
            if entry['verified']:
                self.assertTrue(entry['sources'])
                self.assertTrue(all(url.startswith('https://') for url in entry['sources']))
                self.assertEqual(entry['format'], 'agent-skills')
        self.assertFalse(next(c for c in catalog['clients'] if c['id'] == 'cursor')['capabilities']['mcp_writer'])
        self.assertTrue(next(c for c in catalog['clients'] if c['id'] == 'codex')['capabilities']['mcp_writer'])
        self.assertEqual(set(pa.DEFAULT_TOOLS) - {'hermes'}, {c['id'] for c in catalog['clients'] if c['default_target']})

    def test_read_only_catalog_and_preview_do_not_create_paths(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        self.core.client_catalog()
        preview = self.core.preview_target(self.selection())
        after = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        self.assertEqual(before, after)
        self.assertEqual(preview['target']['dir'], str(self.project / '.cursor' / 'skills'))
        self.assertEqual(preview['target']['scope'], 'project')
        self.assertFalse(preview['present'])
        self.assertIn('Other clients', preview['notice'])

    def test_project_requires_explicit_existing_absolute_root(self):
        for value in (None, '', '.', 'relative/project', str(self.root / 'missing'), str(self.skill / 'SKILL.md')):
            with self.subTest(value=value):
                self.assert_refused(self.core.preview_target, self.selection(project_root=value))
        self.assertFalse((self.root / 'missing').exists())

    def test_activation_only_saves_target_then_toggle_links_inside_project(self):
        preview, result = self.activate()
        target = Path(preview['target']['dir'])
        self.assertFalse(target.exists())
        self.assertTrue(result['ok'])
        self.assertTrue(result['tool'].startswith('cursor-p-'))
        state = next(t for t in self.core.state()['tools'] if t['id'] == result['tool'])
        self.assertTrue(state['configured'])
        self.assertFalse(state['present'])
        self.assertEqual(state['project_root'], str(self.project))
        self.assertEqual(self.core.toggle('development/example', result['tool'], True)['state'], 'enabled')
        self.assertTrue((target / 'example').is_symlink())
        self.assertFalse((self.user / '.cursor').exists())
        self.core.toggle('development/example', result['tool'], False)
        self.assertTrue((self.skill / 'SKILL.md').exists())

    def test_global_and_two_projects_have_distinct_stable_ids(self):
        _, global_result = self.activate({'client_id': 'cursor', 'scope': 'global'})
        first, project_result = self.activate()
        _, other_result = self.activate(self.selection(project_root=str(self.other)))
        self.assertEqual(global_result['tool'], 'cursor')
        self.assertEqual(len({global_result['tool'], project_result['tool'], other_result['tool']}), 3)
        repeated = self.core.preview_target(self.selection())
        self.assertEqual(first['target']['id'], repeated['target']['id'])

    def test_preview_rejects_symlink_escape_and_activation_rechecks(self):
        hidden = self.project / '.cursor'
        hidden.symlink_to(self.other, target_is_directory=True)
        self.assertEqual(self.assert_refused(self.core.preview_target, self.selection()), 'scope-escape')
        hidden.unlink()
        preview = self.core.preview_target(self.selection())
        hidden.symlink_to(self.other, target_is_directory=True)
        self.assert_refused(self.core.activate_target, self.selection(), preview['preview_id'])
        self.assertFalse((self.other / 'skills').exists())
        self.assertFalse(pa.user_config_path(self.home).exists())

    def test_project_root_and_inner_symlink_retarget_disable_existing_target(self):
        alias = self.root / 'chosen-project'
        alias.symlink_to(self.project, target_is_directory=True)
        _, result = self.activate(self.selection(project_root=str(alias)))
        alias.unlink()
        alias.symlink_to(self.other, target_is_directory=True)
        self.assertEqual(self.assert_refused(self.core.toggle, 'development/example', result['tool'], True), 'scope-changed')
        state = next(t for t in self.core.state()['tools'] if t['id'] == result['tool'])
        self.assertIn('error', state)
        self.assertFalse((self.other / '.cursor').exists())

    def test_target_retarget_inside_same_project_is_also_refused(self):
        first = self.project / 'first'
        second = self.project / 'second'
        first.mkdir(); second.mkdir()
        hidden = self.project / '.cursor'
        hidden.symlink_to(first, target_is_directory=True)
        _, result = self.activate()
        hidden.unlink(); hidden.symlink_to(second, target_is_directory=True)
        self.assertEqual(self.assert_refused(self.core.ensure_tool_dir, result['tool']), 'scope-changed')
        self.assertFalse((second / 'skills').exists())

    def test_stale_or_tampered_preview_is_not_applied(self):
        preview = self.core.preview_target(self.selection())
        for token in ('', 'fabricated', None):
            self.assert_refused(self.core.activate_target, self.selection(), token)
        changed = self.selection(project_root=str(self.other))
        self.assertEqual(self.assert_refused(self.core.activate_target, changed, preview['preview_id']), 'preview-stale')
        pa.user_config_path(self.home).write_text('{"tools":{},"preference":9}')
        self.assertEqual(self.assert_refused(self.core.activate_target, self.selection(), preview['preview_id']), 'preview-stale')
        self.assertEqual(json.loads(pa.user_config_path(self.home).read_text())['tools'], {})

    def test_legacy_custom_settings_migrate_only_on_explicit_save(self):
        legacy = pa.legacy_user_config_path(self.home)
        data = {'tools': {'custom': str(self.root / 'custom'), 'grok': {'dir': str(self.root / 'grok'), 'label': 'Grok', 'future': 7}}, 'preference': {'retained': True}}
        legacy.write_text(json.dumps(data))
        before = legacy.read_bytes()
        self.core = self.rebuild()
        self.assertEqual(self.core.tools['custom']['dir'], data['tools']['custom'])
        self.assertTrue(self.core.tools['grok']['configured'])
        self.assertFalse(pa.user_config_path(self.home).exists())
        self.activate()
        saved = json.loads(pa.user_config_path(self.home).read_text())
        self.assertEqual(saved['schema_version'], 2)
        self.assertEqual(saved['tools']['custom'], data['tools']['custom'])
        self.assertEqual(saved['tools']['grok'], data['tools']['grok'])
        self.assertEqual(saved['preference'], data['preference'])
        self.assertEqual(legacy.read_bytes(), before)

    def test_invalid_and_future_configuration_fails_closed(self):
        for raw in ('[]', '{', '{"tools":[]}', '{"schema_version":99,"tools":{}}', '{"tools":{"custom":null}}'):
            with self.subTest(raw=raw):
                pa.user_config_path(self.home).write_text(raw)
                self.assert_refused(pa.load_tools_config, self.home)
                self.assert_refused(self.core.preview_target, self.selection())
                self.assertEqual(pa.user_config_path(self.home).read_text(), raw)

    def test_unverified_clients_are_not_automatically_enabled(self):
        for cid in ('grok', 'zcode', 'kimi'):
            self.assert_refused(self.core.preview_target, {'client_id': cid, 'scope': 'global'})
            self.assertEqual(self.assert_refused(self.core.ensure_tool_dir, cid), 'unverified-client')
        self.assertTrue(self.core.client_catalog()['unverified'])

    def test_existing_codex_directory_is_never_silently_relocated(self):
        old = self.user / '.codex' / 'skills'
        old.mkdir(parents=True)
        current = self.user / '.agents' / 'skills'
        current.mkdir(parents=True)
        core = self.rebuild()
        self.assertEqual(core.tool_dir('codex'), old)
        preview = core.preview_target({'client_id': 'codex', 'scope': 'global'})
        self.assertEqual(preview['target']['dir'], str(old))
        self.assertIn('legacy', preview['notice'].lower())

    def test_shared_directory_is_not_silently_given_two_controls(self):
        self.activate({'client_id': 'codex', 'scope': 'global'})
        self.assertEqual(self.assert_refused(self.core.preview_target, {'client_id': 'agents', 'scope': 'global'}), 'duplicate-target')

    def test_custom_path_cannot_overlap_canonical_skills_or_selected_scope(self):
        for path in (self.home, self.home / 'skills', self.skill, self.project / '..' / 'other'):
            selection = {'client_id': 'custom', 'scope': 'custom', 'id': 'mine', 'label': 'Mine', 'dir': str(path)}
            self.assert_refused(self.core.preview_target, selection)
        self.assert_refused(self.core.set_tool, 'bad', 'Bad', str(self.home / 'skills'))

    def test_new_catalog_entry_requires_no_detection_conditionals(self):
        entry = copy.deepcopy(pa.CLIENT_CATALOG['cline'])
        entry.update(label='Fixture client', global_dirs=['~/.fixture-client/skills'], project_dirs=['.fixture-client/skills'])
        with patch.dict(pa.CLIENT_CATALOG, {'fixture-client': entry}):
            catalog = self.core.client_catalog()
            self.assertTrue(any(c['id'] == 'fixture-client' for c in catalog['clients']))
            preview = self.core.preview_target({'client_id': 'fixture-client', 'scope': 'project', 'project_root': str(self.project)})
            self.assertEqual(preview['target']['dir'], str(self.project / '.fixture-client' / 'skills'))

    def test_missing_project_is_not_recreated_by_a_mutation(self):
        _, result = self.activate()
        self.project.rmdir()
        self.assertEqual(self.assert_refused(self.core.ensure_tool_dir, result['tool']), 'project-missing')
        self.assertFalse(self.project.exists())

    def test_format_refusal_matches_preview_and_preserves_source(self):
        _, result = self.activate()
        source = self.skill / 'SKILL.md'
        source.write_text('This is not an Agent Skills frontmatter block.')
        before = source.read_bytes()
        plan = self.core.plan_bulk(['development/example'], result['tool'], True)
        self.assertEqual(plan['refused'][0]['code'], 'skill-format')
        self.assertEqual(self.assert_refused(self.core.toggle, 'development/example', result['tool'], True), 'skill-format')
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.project / '.cursor').exists())

    def test_label_only_default_override_and_long_legacy_id_survive(self):
        data = {'tools': {'claude': {'label': 'Team Claude'}, 'custom-' + 'x' * 40: str(self.other)}}
        pa.user_config_path(self.home).write_text(json.dumps(data))
        loaded = pa.load_tools_config(self.home)
        self.assertEqual(loaded['claude']['label'], 'Team Claude')
        self.assertEqual(loaded['claude']['dir'], str(self.user / '.claude' / 'skills'))
        self.assertIn('custom-' + 'x' * 40, loaded)

    def test_malformed_scope_metadata_is_a_typed_error(self):
        for field in ('root_path', 'scope_root', 'resolved_dir', 'project_root'):
            pa.user_config_path(self.home).write_text(json.dumps({'tools': {'mine': {'dir': str(self.other), field: []}}}))
            self.assertEqual(self.assert_refused(pa.load_tools_config, self.home), 'config-invalid')


if __name__ == '__main__':
    unittest.main()
