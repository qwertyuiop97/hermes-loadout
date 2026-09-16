"""Bounded records, complete capture, and efficient read-only reviews."""
from __future__ import annotations

import itertools
import json
import unittest
from unittest.mock import patch

from test_plugin_api import Fixture, pa, _mk_skill
from test_loadouts import module, selection
from test_mcp_backend import needs_yaml


class LoadoutReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.mcp = pa.McpCore(self.fx.home,
                              claude_desktop_config=self.fx.tmp / 'claude-config.json',
                              codex_config=self.fx.tmp / 'codex-config.toml')
        self.service = module.LoadoutService(self.fx.core, self.mcp, pa)

    def test_record_limit_counts_utf8_bytes_and_preserves_previous_record(self):
        name = 'inventory-metadata.json'
        original = {'version': 1, 'skills': {}}
        self.service._write(name, original)
        previous = self.service._path(name).read_bytes()
        data = {'text': 'ع' * 50}
        size = len((json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
        with patch.object(module.LoadoutService, 'MAX_RECORD_BYTES', size, create=True):
            self.service._write(name, data)
            self.assertEqual(self.service._read(name, {}), data)
        self.service._path(name).write_bytes(previous)
        with patch.object(module.LoadoutService, 'MAX_RECORD_BYTES', size - 1, create=True):
            with self.assertRaises(pa.LoadoutError) as caught:
                self.service._write(name, data)
            self.assertEqual(caught.exception.code, 'record-too-large')
        self.assertEqual(self.service._path(name).read_bytes(), previous)
        self.assertEqual(self.service._read(name, {}), original)

    def test_rejected_record_does_not_create_store_or_erase_pending_evidence(self):
        with patch.object(module.LoadoutService, 'MAX_RECORD_BYTES', 32, create=True):
            with self.assertRaises(pa.LoadoutError):
                self.service._write('loadouts.json', {'text': 'x' * 100})
        self.assertFalse(self.service._path('loadouts.json').parent.exists())
        self.service._write('pending-operation.json', {'status': 'pending'})
        previous = self.service._path('pending-operation.json').read_bytes()
        with patch.object(module.LoadoutService, 'MAX_RECORD_BYTES', 32, create=True):
            with self.assertRaises(pa.LoadoutError):
                self.service._write('pending-operation.json', {'text': 'x' * 100})
        self.assertEqual(self.service._path('pending-operation.json').read_bytes(), previous)

    def test_save_that_exceeds_capacity_keeps_all_existing_loadouts_readable(self):
        record = self.service.save_loadout('Keep me', [selection()])['loadout']
        path = self.service._path('loadouts.json')
        previous = path.read_bytes()
        with patch.object(module.LoadoutService, 'MAX_RECORD_BYTES', len(previous) + 1, create=True):
            with self.assertRaises(pa.LoadoutError) as caught:
                self.service.save_loadout('Not enough room', [selection()])
            self.assertEqual(caught.exception.code, 'record-too-large')
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(self.service.list_loadouts()['loadouts'], [record])

    def test_real_capacity_cannot_be_crossed_by_valid_large_loadouts(self):
        states = [selection(identity='catalog/' + 'ع' * 240 + f'{index:04d}') for index in range(2048)]
        records = []
        for number in range(10):
            previous = self.service._path('loadouts.json').read_bytes() if records else None
            try:
                records.append(self.service.save_loadout(f'Large {number}', states)['loadout'])
            except pa.LoadoutError as exc:
                self.assertEqual(exc.code, 'record-too-large')
                self.assertTrue(records)
                self.assertEqual(self.service._path('loadouts.json').read_bytes(), previous)
                self.assertEqual(self.service.list_loadouts()['loadouts'], records)
                break
        else:
            self.fail('Valid saves must be refused before exceeding the 4 MiB read limit')

    def test_capture_refuses_broken_catalog_instead_of_fabricating_empty_mcp(self):
        self.fx.core.config_path.write_text('mcp_servers: [\n', encoding='utf-8')
        previous = self.fx.core.config_path.read_bytes()
        for app in ('hermes', 'codex', 'claude-desktop'):
            with self.subTest(app=app):
                with self.assertRaises(pa.LoadoutError) as caught:
                    self.service.capture([app])
                self.assertEqual(caught.exception.code, 'capture-unavailable')
        self.assertEqual(self.fx.core.config_path.read_bytes(), previous)
        self.assertFalse(self.service._path('loadouts.json').exists())

    def test_capture_does_not_consult_mcp_for_skill_only_clients(self):
        with patch.object(self.mcp, 'mcp_state', side_effect=AssertionError('unneeded MCP read')):
            result = self.service.capture(['claude'])
        self.assertTrue(result['states'])
        self.assertTrue(all(row['kind'] == 'skill' and row['app'] == 'claude' for row in result['states']))

    def test_empty_capture_is_observe_only_without_inventory_reads(self):
        with patch.object(self.fx.core, 'state', side_effect=AssertionError('unneeded inventory read')):
            self.assertEqual(self.service.capture([]), {'ok': True, 'states': [], 'excluded': []})

    def test_capture_reads_mcp_once_and_deduplicates_applications(self):
        # An absent catalog is legitimate, including without the optional parser.
        self.fx.core.config_path.write_text('', encoding='utf-8')
        with patch.object(self.mcp, 'mcp_state', wraps=self.mcp.mcp_state) as read:
            result = self.service.capture(['hermes', 'codex', 'claude-desktop', 'codex'])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(result['states']), len({(r['kind'], r['app'], r['id']) for r in result['states']}))

    def test_selected_unreadable_writer_blocks_capture_but_other_apps_work(self):
        self.fx.core.config_path.write_text('', encoding='utf-8')
        self.mcp.codex_config.write_text('SECRET broken TOML = [', encoding='utf-8')
        with self.assertRaises(pa.LoadoutError) as caught:
            self.service.capture(['codex'])
        self.assertEqual(caught.exception.code, 'capture-unavailable')
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertTrue(self.service.capture(['hermes'])['ok'])
        self.assertTrue(self.service.capture(['claude-desktop'])['ok'])

    @needs_yaml
    def test_capture_preserves_known_unavailable_entry_explanations(self):
        self.fx.core.config_path.write_text('mcp_servers:\n  remote:\n    url: https://example.invalid/mcp\n', encoding='utf-8')
        result = self.service.capture(['claude-desktop'])
        self.assertEqual(result['states'], [])
        self.assertEqual(result['excluded'], [{'kind': 'mcp', 'app': 'claude-desktop', 'id': 'remote', 'status': 'unsupported'}])

    def test_skill_review_scans_library_once_not_per_selection(self):
        for index in range(80):
            _mk_skill(self.fx.home, 'bulk', f'skill-{index}', 'A test skill')
        states = [selection(identity=f'bulk/skill-{index}') for index in range(80)]
        with patch.object(self.fx.core, '_scan_skills', wraps=self.fx.core._scan_skills) as scan:
            plan = self.service.plan(states)
        self.assertEqual(plan['counts']['enable'], 80)
        self.assertEqual(scan.call_count, 1, 'Read-only review should share one request-local library snapshot')
        self.assertFalse(any(self.fx.codex.iterdir()), 'Review must remain read-only')

    def test_apply_revalidates_after_review_snapshot_and_next_review_rescans(self):
        plan = self.service.plan([selection()])
        foreign = self.fx.codex / 'apple-notes'
        foreign.mkdir()
        (foreign / 'keep.txt').write_text('owner data', encoding='utf-8')
        result = self.service.apply(plan['plan_id'])
        self.assertEqual(result['changed'], 0)
        self.assertEqual((foreign / 'keep.txt').read_text(encoding='utf-8'), 'owner data')
        skill = self.fx.home / 'skills/apple/apple-notes/SKILL.md'
        skill.unlink()
        next_plan = self.service.plan([selection()])
        self.assertEqual(next_plan['items'][0]['code'], 'unknown-skill')

    def test_duplicate_skill_names_still_refused_with_snapshot(self):
        _mk_skill(self.fx.home, 'duplicate', 'apple-notes', 'Conflicting name')
        plan = self.service.plan([selection()])
        self.assertEqual(plan['items'][0]['code'], 'ambiguous-skill')
        self.assertEqual(plan['counts']['enable'], 0)

    def test_every_alias_in_opposing_shared_target_group_is_a_conflict(self):
        for app in ('alias-one', 'alias-two'):
            self.fx.core.tools[app] = {'label': app, 'dir': self.fx.codex}
        states = [selection(), selection(app='alias-one', enabled=False), selection(app='alias-two')]
        for ordered in itertools.permutations(states):
            with self.subTest(order=[r['app'] for r in ordered]):
                plan = self.service.plan(list(ordered))
                self.assertEqual(plan['counts']['conflict'], 3)
                self.assertTrue(all(row['code'] == 'shared-target' for row in plan['items']))
                result = self.service.apply(plan['plan_id'])
                self.assertEqual(result['changed'], 0)
                self.assertEqual(result['receipt']['counts']['conflict'], 3)
        self.assertFalse((self.fx.codex / 'apple-notes').exists())

    def test_consistent_aliases_still_apply_once_and_undo_once(self):
        self.fx.core.tools['alias-one'] = {'label': 'Alias', 'dir': self.fx.codex}
        plan = self.service.plan([selection(), selection(app='alias-one')])
        self.assertEqual(plan['counts']['enable'], 1)
        self.assertEqual(plan['counts']['unchanged'], 1)
        result = self.service.apply(plan['plan_id'])
        self.assertEqual(result['changed'], 1)
        result = self.service.undo(self.service.undo_plan()['plan_id'])
        self.assertEqual(result['receipt']['changed'], 1)
        self.assertFalse((self.fx.codex / 'apple-notes').exists())
