"""Secret-free named selections, reviewed operations, and one recoverable undo point.

The host injects the existing skill/MCP adapters. This module owns orchestration,
not client paths or credential formats. No module import writes user data.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


class LoadoutService:
    FORMAT = 'hermes-loadout-operation-v1'

    def __init__(self, core, mcp, api):
        self.core, self.mcp, self.api = core, mcp, api
        self.Error = api.LoadoutError

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    def _path(self, name, create=False):
        root = self.core.home / 'data' / self.api.PLUGIN_ID
        boundary = self.core.home.resolve()
        for directory in (root, *root.parents):
            if self.api.same_path(directory, boundary):
                break
            if directory.is_symlink() or not self.api.is_inside(directory, boundary):
                raise self.Error('The Loadout data folder is redirected. Review it before writing.', 'store-protected')
        if create:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / name
        if path.is_symlink():
            raise self.Error('The Loadout record is a link, not a private data file.', 'store-protected')
        return path

    def _read(self, name, default):
        path = self._path(name)
        if not path.exists():
            return copy.deepcopy(default)
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise self.Error('The saved Loadout record is invalid or too large.', 'record-invalid')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError, UnicodeError) as exc:
            raise self.Error('The saved Loadout record cannot be read. Restore or correct it.', 'record-invalid') from exc
        if not isinstance(data, dict):
            raise self.Error('The saved Loadout record is not an object.', 'record-invalid')
        return data

    def _write(self, name, data):
        path = self._path(name, create=True)
        self.api._atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + '\n')

    def _id(self, value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{12}', value):
            raise self.Error('Choose a saved loadout.', 'unknown-loadout')
        return value

    def _name(self, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 64 or any(ord(c) < 32 for c in name):
            raise self.Error('Use a loadout name of 1 to 64 characters.', 'invalid-name')
        return name.strip()

    def _states(self, states):
        if not isinstance(states, list) or len(states) > 2048:
            raise self.Error('A loadout may contain at most 2,048 explicit selections.', 'invalid-states')
        result, seen = [], set()
        for row in states:
            if (not isinstance(row, dict) or set(row) != {'kind', 'app', 'id', 'enabled'}
                    or row.get('kind') not in ('skill', 'mcp') or type(row.get('enabled')) is not bool
                    or not isinstance(row.get('app'), str) or not re.fullmatch(r'[a-z0-9-]{1,64}', row['app'])
                    or not isinstance(row.get('id'), str) or not row['id'].strip() or len(row['id']) > 256
                    or any(ord(char) < 32 for char in row['id'])):
                raise self.Error('Selections may contain only capability IDs, applications, and on/off states, not configuration or secrets.', 'invalid-states')
            if row['kind'] == 'skill' and (not self.api._SKILL_ID_RE.fullmatch(row['id'])
                    or any(part in ('.', '..') or '\\' in part for part in row['id'].split('/'))):
                raise self.Error('Use a skill ID from the library.', 'invalid-states')
            key = (row['kind'], row['app'], row['id'])
            if key in seen:
                raise self.Error('A capability has more than one desired state.', 'duplicate-state')
            seen.add(key)
            result.append(dict(row))
        return result

    def _loadouts(self):
        data = self._read('loadouts.json', {'version': 1, 'loadouts': []})
        if data.get('version') != 1 or not isinstance(data.get('loadouts'), list) or len(data['loadouts']) > 100:
            raise self.Error('Unsupported or invalid loadout records.', 'record-invalid')
        seen = set()
        for record in data['loadouts']:
            if not isinstance(record, dict) or set(record) != {'id', 'name', 'states'}:
                raise self.Error('Saved loadout fields are invalid.', 'record-invalid')
            identity = self._id(record['id'])
            if identity in seen:
                raise self.Error('Duplicate saved loadout identity.', 'record-invalid')
            seen.add(identity)
            self._name(record['name'])
            self._states(record['states'])
        return data

    def list_loadouts(self):
        with self.core._lock:
            return {'ok': True, **self._loadouts()}

    def save_loadout(self, name, states, loadout_id=None):
        with self.core._lock:
            name, states = self._name(name), self._states(states)
            data = self._loadouts()
            record = next((r for r in data['loadouts'] if r['id'] == loadout_id), None)
            if loadout_id is not None and record is None:
                raise self.Error('This loadout no longer exists.', 'unknown-loadout')
            if record is None:
                if len(data['loadouts']) >= 100:
                    raise self.Error('Remove an unused loadout before saving another.', 'loadout-limit')
                record = {'id': uuid.uuid4().hex[:12]}
                data['loadouts'].append(record)
            record.update(name=name, states=states)
            self._write('loadouts.json', data)
            return {'ok': True, 'loadout': copy.deepcopy(record)}

    def edit_loadout(self, loadout_id, action, name=None):
        with self.core._lock:
            data = self._loadouts()
            record = next((r for r in data['loadouts'] if r['id'] == self._id(loadout_id)), None)
            if record is None:
                raise self.Error('This loadout no longer exists.', 'unknown-loadout')
            if action == 'duplicate':
                return self.save_loadout(name or (record['name'][:57] + ' copy'), record['states'])
            if action == 'rename':
                record['name'] = self._name(name)
            elif action == 'delete':
                data['loadouts'].remove(record)
            else:
                raise self.Error('Unknown loadout action.', 'invalid-action')
            self._write('loadouts.json', data)
            return {'ok': True, 'loadout': copy.deepcopy(record), 'action': action}

    def capture(self, apps):
        """Save-only source data. This does not change filesystem or MCP state."""
        if not isinstance(apps, list) or any(not isinstance(app, str) for app in apps):
            raise self.Error('Select applications to capture.', 'invalid-body')
        states = []
        inventory = self.core.state()
        for app in dict.fromkeys(apps):
            if app in self.core.tools:
                for skill in inventory['skills']:
                    state = skill['tools'][app]['state']
                    if state in ('enabled', 'disabled', 'missing'):
                        states.append({'kind': 'skill', 'app': app, 'id': skill['id'], 'enabled': state == 'enabled'})
            if app in ('hermes', 'codex', 'claude-desktop'):
                for row in self.mcp.mcp_state()['rows']:
                    state = ('enabled' if row['enabled'] else 'disabled') if app == 'hermes' else row['writers'].get('claude' if app == 'claude-desktop' else app)
                    if state in ('enabled', 'disabled', 'missing'):
                        states.append({'kind': 'mcp', 'app': app, 'id': row['name'], 'enabled': state == 'enabled'})
        return {'ok': True, 'states': states}

    CLASSIFICATIONS = ('Portable', 'Hermes-specific', 'Codex-specific', 'Claude-specific',
                       'Other application-specific', 'Unclassified')

    def metadata(self):
        data = self._read('inventory-metadata.json', {'version': 1, 'skills': {}})
        if data.get('version') != 1 or not isinstance(data.get('skills'), dict):
            raise self.Error('The inventory labels cannot be read. Restore the metadata file.', 'record-invalid')
        for skill, fields in data['skills'].items():
            if (not isinstance(fields, dict) or set(fields) - {'classification', 'source'}
                    or fields.get('classification', 'Unclassified') not in self.CLASSIFICATIONS
                    or not isinstance(fields.get('source', 'Existing library'), str)):
                raise self.Error('An inventory label is invalid.', 'record-invalid')
        return {'ok': True, **data, 'classifications': list(self.CLASSIFICATIONS)}

    def classify(self, skill, classification):
        with self.core._lock:
            self.core._validate_skill(skill)
            if classification not in self.CLASSIFICATIONS:
                raise self.Error('Choose an informational classification.', 'invalid-classification')
            data = self.metadata()
            data['skills'].setdefault(skill, {})['classification'] = classification
            self._write('inventory-metadata.json', {k: data[k] for k in ('version', 'skills')})
            return {'ok': True, 'skill': skill, 'classification': classification}

    def _native_mcp(self, app, name):
        if app == 'hermes':
            source = next((r for r in self.mcp.catalog()['catalog'] if r['name'] == name), None)
            if source is None:
                raise self.Error('This server is not available in Hermes.', 'unknown-server')
            return self.mcp.config_path, source
        if app == 'codex':
            _, text = self.mcp._read_codex()
            doc = self.api._toml_support().load(text)
            return self.mcp.codex_config, doc.get('mcp_servers', {}).get(name)
        if app == 'claude-desktop':
            doc, _ = self.mcp._read_claude()
            return self.mcp.claude_config, doc.get('mcpServers', {}).get(name)
        raise self.Error('No MCP writer is available for this application.', 'no-writer')

    def _snapshot(self, row):
        if row['kind'] == 'skill':
            self.core._validate_tool(row['app'])
            self.core._validate_skill(row['id'])
            skill = self.core._scan_skills()[row['id']]
            if row['app'] == 'hermes':
                return {'type': 'hermes-skill', 'context': self.api._canonical(str(self.core.config_path)),
                        'disabled': skill['name'] in self.core._disabled_set()}
            target = self.core.tool_dir(row['app'])
            if target is None:
                raise self.Error('The application has no reviewed skill folder.', 'no-dir')
            return {'type': 'skill-link', 'context': self.api._canonical(str(target)),
                    'root': str(self.core.skills_root_resolved),
                    'entry': self.core._entry_snapshot(target / skill['name'])}
        path, native = self._native_mcp(row['app'], row['id'])
        if path is None:
            raise self.Error('No MCP configuration path is available.', 'no-writer')
        source = next((r for r in self.mcp.catalog()['catalog'] if r['name'] == row['id']), None)
        return {'type': 'mcp', 'context': self.api._canonical(str(path)),
                'present': native is not None, 'enabled': native.get('enabled', True) if isinstance(native, dict) else False,
                'fingerprint': self.digest(native), 'source': self.digest(source['definition'] if source else None)}

    def _disposition(self, row):
        if row['kind'] == 'skill':
            self.core._validate_tool(row['app'])
            self.core._validate_skill(row['id'])
            skill = self.core._scan_skills()[row['id']]
            if row['enabled']:
                self.core._validate_client_skill(skill, row['app'])
            result = self.core._bulk_disposition(skill, row['app'], row['enabled'], self.core._disabled_set())
            if result['kind'] == 'refused':
                raise self.Error(result.get('reason', 'This entry is protected.'), result.get('code', 'protected'))
            return 'unchanged' if result['kind'] == 'satisfied' else ('enable' if row['enabled'] else 'disable')
        public = self.mcp.mcp_state()
        entry = next((r for r in public['rows'] if r['name'] == row['id']), None)
        if entry is None:
            raise self.Error('This server is unavailable in the Hermes inventory.', 'unknown-server')
        if row['app'] == 'hermes':
            state = 'enabled' if entry['enabled'] else 'disabled'
        else:
            writer = 'claude' if row['app'] == 'claude-desktop' else row['app']
            state = entry['writers'].get(writer, 'unavailable')
        if state == 'drifted':
            raise self.Error('This application has a different server definition. Resolve it separately.', 'conflict')
        if state in ('unsupported', 'unavailable'):
            raise self.Error('This server cannot be projected into this application.', state)
        current = state == 'enabled'
        return 'unchanged' if current == row['enabled'] else ('enable' if row['enabled'] else 'disable')

    def _public_error(self, row, exc):
        code = exc.code if isinstance(exc, self.Error) else 'filesystem-error'
        status = 'protected' if code in ('foreign-link', 'unmanaged-dir', 'catalog-bypass', 'config-protected', 'read-only') else (
            'conflict' if code in ('conflict', 'drifted', 'changed-since-preview', 'changed-since-apply', 'review-mismatch') else (
                'failed' if code in ('filesystem-error', 'copy-failed', 'symlink-unsupported', 'recovery-required') else 'unavailable'))
        # Reader/parser exceptions can embed credentials; expose stable explanations only.
        explanation = str(exc) if isinstance(exc, self.Error) and row.get('kind') != 'mcp' else 'Check this application or server configuration, then review again.'
        return {**row, 'status': status, 'code': code, 'error': explanation, 'ok': False}

    def plan(self, states, label='Selection', loadout_id=None):
        with self.core._lock, self.mcp._lock:
            states = self._states(states)
            items = []
            aliases = {}
            for row in states:
                try:
                    status = self._disposition(row)
                    before = self._snapshot(row)
                    item = {**row, 'status': status, 'before': before}
                    alias = (row['kind'], before['context'], row['id'])
                    previous = aliases.get(alias)
                    if previous is not None:
                        if previous['enabled'] != row['enabled']:
                            previous.update(status='conflict', code='shared-target', error='These applications share a physical target and request opposite states.')
                            item.update(status='conflict', code='shared-target', error='These applications share a physical target and request opposite states.')
                        else:
                            item['status'] = 'unchanged'
                            item['reason'] = 'The same shared target is already included in this preview.'
                    else:
                        aliases[alias] = item
                    items.append(item)
                except (self.Error, OSError, ValueError) as exc:
                    items.append(self._public_error(row, exc))
            payload = {'label': self._name(label), 'items': items, 'loadout_id': loadout_id}
            if loadout_id is not None:
                record = next(r for r in self._loadouts()['loadouts'] if r['id'] == loadout_id)
                payload['definition_hash'] = self.digest(record)
            plan_id = self.core._new_review('operation', payload)
            public_items = [{k: v for k, v in item.items() if k != 'before'} for item in items]
            return {'ok': True, 'plan_id': plan_id, 'label': label, 'items': public_items,
                    'counts': {key: sum(r['status'] == key for r in items) for key in ('enable', 'disable', 'unchanged', 'unavailable', 'conflict', 'protected')},
                    'expires_in': 600}

    def plan_loadout(self, loadout_id):
        with self.core._lock:
            record = next((r for r in self._loadouts()['loadouts'] if r['id'] == self._id(loadout_id)), None)
            if record is None:
                raise self.Error('This loadout no longer exists.', 'unknown-loadout')
            return self.plan(record['states'], record['name'], record['id'])

    def _receipt(self, label, kind):
        if self._path('pending-operation.json').exists():
            raise self.Error('An interrupted operation needs recovery. Inspect the pending record and backups before continuing.', 'recovery-required')
        return {'format': self.FORMAT, 'receipt_id': uuid.uuid4().hex[:12], 'kind': kind,
                'label': label, 'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'pending', 'items': []}

    def _summary(self, receipt):
        items = [{k: v for k, v in row.items() if k in ('kind', 'app', 'id', 'enabled', 'status', 'code', 'error', 'ok')}
                 for row in receipt['items']]
        changed = sum(row.get('status') == 'completed' for row in items)
        return {'receipt_id': receipt['receipt_id'], 'label': receipt['label'], 'kind': receipt['kind'],
                'status': receipt['status'], 'items': items, 'changed': changed,
                'skipped': sum(row.get('status') == 'unchanged' for row in items),
                'failed': sum(row.get('status') not in ('completed', 'unchanged') for row in items),
                'undo_available': changed > 0 and receipt['status'] == 'complete'}

    def _complete(self, receipt):
        receipt['status'] = 'complete'
        changed = any(row.get('status') == 'completed' for row in receipt['items'])
        if changed:
            self._write('last-operation.json', receipt)
        self._path('pending-operation.json').unlink(missing_ok=True)
        self.core.invalidate()
        return {'ok': True, 'receipt': self._summary(receipt), 'changed': self._summary(receipt)['changed']}

    def apply(self, plan_id):
        with self.core._lock, self.mcp._lock:
            plan = self.core._take_review(plan_id, 'operation')
            if plan.get('loadout_id'):
                record = next((r for r in self._loadouts()['loadouts'] if r['id'] == plan['loadout_id']), None)
                if record is None or self.digest(record) != plan['definition_hash']:
                    raise self.Error('The named loadout changed. Review it again.', 'review-mismatch')
            receipt = self._receipt(plan['label'], 'loadout' if plan.get('loadout_id') else 'selection')
            actionable = any(r['status'] in ('enable', 'disable') for r in plan['items'])
            if actionable:
                self._write('pending-operation.json', receipt)  # fail before the first mutation
            for item in plan['items']:
                row = copy.deepcopy(item)
                if row['status'] not in ('enable', 'disable'):
                    row['ok'] = row['status'] == 'unchanged'
                    receipt['items'].append(row)
                    continue
                try:
                    if self._snapshot(row) != row['before']:
                        raise self.Error('This entry changed after preview. It was left untouched.', 'changed-since-preview')
                    self._disposition(row)
                    row['status'] = 'pending'
                    receipt['items'].append(row)
                    self._write('pending-operation.json', receipt)
                    if row['kind'] == 'skill':
                        self.core.toggle(row['id'], row['app'], row['enabled'])
                    else:
                        result = self.mcp.set_activation(row['id'], row['app'], row['enabled'])
                        backup = result.get('backup')
                        if backup:
                            row['backup'] = str(backup)
                            row['backup_hash'] = hashlib.sha256(Path(backup).read_bytes()).hexdigest()
                    row.update(after=self._snapshot(row), status='completed', ok=True)
                    self._write('pending-operation.json', receipt)
                except (self.Error, OSError, ValueError) as exc:
                    if row not in receipt['items']:
                        receipt['items'].append(row)
                    # A mutation followed by receipt-store failure must not masquerade as no change.
                    if (row.get('status') == 'completed' or (row.get('status') == 'pending' and
                            self._snapshot(row) != row['before'])):
                        receipt['status'] = 'recovery-required'
                        return {'ok': False, 'code': 'recovery-required', 'error': 'A change completed but its recovery record could not be finalized. Preserve the pending record.', 'receipt': self._summary(receipt)}
                    row.update(self._public_error({k: row[k] for k in ('kind', 'app', 'id', 'enabled')}, exc))
            try:
                return self._complete(receipt)
            except (self.Error, OSError) as exc:
                receipt['status'] = 'recovery-required'
                return {'ok': False, 'code': 'recovery-required', 'error': 'The changes could not be finalized. Inspect the pending record and backups.', 'receipt': self._summary(receipt)}

    def _validate_receipt(self, receipt):
        if (receipt.get('format') != self.FORMAT or not isinstance(receipt.get('items'), list)
                or len(receipt['items']) > 2048 or not isinstance(receipt.get('label'), str)):
            raise self.Error('The last change record is invalid.', 'record-invalid')
        self._id(receipt.get('receipt_id'))
        for row in receipt['items']:
            if not isinstance(row, dict) or row.get('kind') not in ('skill', 'mcp', 'import', 'catalog-bypass'):
                raise self.Error('The last change record has an invalid item.', 'record-invalid')
            if row.get('status') != 'completed':
                continue
            if not isinstance(row.get('before' if row['kind'] != 'import' else 'after'), dict) or not isinstance(row.get('after'), dict):
                raise self.Error('The previous change is missing its exact state.', 'record-invalid')
            if row['kind'] in ('skill', 'mcp'):
                self._states([{k: row.get(k) for k in ('kind', 'app', 'id', 'enabled')}])
            elif row['kind'] == 'import':
                if (not isinstance(row.get('skill'), str) or not self.api._SKILL_ID_RE.fullmatch(row['skill'])
                        or type(row.get('hermes_disabled_before')) is not bool or not isinstance(row.get('path'), str)):
                    raise self.Error('The import recovery record is invalid.', 'record-invalid')

    def latest(self):
        with self.core._lock:
            receipt = self._read('last-operation.json', {})
            pending = self._path('pending-operation.json').exists()
            if receipt:
                self._validate_receipt(receipt)
            summary = self._summary(receipt) if receipt else None
            if summary and pending:
                summary['undo_available'] = False
            return {'ok': True, 'receipt': summary, 'recovery_required': pending}

    def _mcp_backup_entry(self, row):
        path, _ = self._native_mcp(row['app'], row['id'])
        before = row['before']
        if not before['present']:
            return None
        backup = Path(row.get('backup', ''))
        if (not backup.is_absolute() or backup.is_symlink() or not backup.is_file()
                or not self.api.same_path(backup.parent, path.parent)
                or not re.fullmatch(re.escape(path.name) + r'\.bak\.hermes-loadout\.\d{8}-\d{6}(?:-\d+)?', backup.name)
                or hashlib.sha256(backup.read_bytes()).hexdigest() != row.get('backup_hash')):
            raise self.Error('The original server backup is missing or changed.', 'backup-changed')
        text = backup.read_text(encoding='utf-8')
        if row['app'] == 'codex':
            return self.api._toml_support().load(text).get('mcp_servers', {}).get(row['id'])
        if row['app'] == 'claude-desktop':
            doc, _ = self.api._read_json_mapping(backup, 'mcpServers')
            return doc.get('mcpServers', {}).get(row['id'])
        return None

    def _undo_check(self, row):
        if row.get('kind') in ('skill', 'mcp'):
            self._states([{k: row.get(k) for k in ('kind', 'app', 'id', 'enabled')}])
            if self._snapshot(row) != row.get('after'):
                raise self.Error('This entry changed after the operation. It will be left untouched.', 'changed-since-apply')
            before = row.get('before', {})
            if row['kind'] == 'skill' and row['app'] != 'hermes':
                entry = before.get('entry', {})
                if entry.get('kind') not in ('missing', 'symlink'):
                    raise self.Error('The prior skill state is not safely restorable.', 'record-invalid')
                if entry.get('kind') == 'symlink':
                    target = Path(entry.get('target', ''))
                    directory = self.core.tool_dir(row['app'])
                    target = target if target.is_absolute() else directory / target
                    if not self.api.is_inside(target, self.core.skills_root_resolved):
                        raise self.Error('The original link target is no longer managed.', 'record-invalid')
            if row['kind'] == 'mcp' and row['app'] != 'hermes':
                original = self._mcp_backup_entry(row)
                if self.digest(original) != before['fingerprint']:
                    raise self.Error('The original server does not match its recorded state.', 'backup-changed')
        elif row.get('kind') == 'catalog-bypass':
            self.core._validate_tool(row['app'])
            directory = self.core.tool_dir(row['app'])
            if (directory is None or self.api._canonical(str(directory)) != row['context']
                    or self.core._entry_snapshot(directory / self.core._entry_name(row['id'])) != row['after']):
                raise self.Error('The application entry changed after repair.', 'changed-since-apply')
            target = Path(row['before']['target'])
            if not self.api.is_inside(target if target.is_absolute() else directory / target, self.core.skills_root_resolved):
                raise self.Error('The original broad link no longer targets the library.', 'changed-since-apply')
        elif row.get('kind') == 'import':
            destination = self.core._import_destination(*row['skill'].split('/', 1))
            if (str(destination) != row['path'] or self.core._import_identity(destination) != row['after']
                    or self.core._skill_fingerprint(destination) != row['fingerprint']
                    or row['id'] not in self.core._disabled_set()):
                raise self.Error('The imported skill or its activation changed. It will be preserved.', 'changed-since-apply')
            for app in self.core.tools:
                target = self.core._inventory_dir(app)
                if target is None:
                    continue
                entry = target / row['id']
                if row.get('source_app') and self.api.same_path(target, self.core.tool_dir(row['source_app'])):
                    if self.core._entry_snapshot(entry) != row.get('source_after'):
                        raise self.Error('The source application entry changed.', 'changed-since-apply')
                elif entry.is_symlink() and self.api.is_inside(entry, destination):
                    raise self.Error('Another application now uses this imported skill.', 'changed-since-apply')
            if self.core.catalog_bypasses():
                raise self.Error('A broad link now exposes the imported skill.', 'catalog-bypass')
            if row.get('source_app'):
                target = self.core.tool_dir(row['source_app'])
                self.core._checked_backup(Path(row['backup']), self.core._tool_backup_root(target),
                    re.escape(row['id']) + r'\.bak\.hermes-loadout\.\d{8}-\d{6}-[a-f0-9]{8}')
                if self.core._skill_fingerprint(Path(row['backup'])) != row['fingerprint']:
                    raise self.Error('The original import backup changed.', 'backup-changed')
        else:
            raise self.Error('This receipt has an unknown operation type.', 'record-invalid')

    def undo_plan(self):
        with self.core._lock, self.mcp._lock:
            if self._path('pending-operation.json').exists():
                raise self.Error('An interrupted operation needs recovery before undo.', 'recovery-required')
            receipt = self._read('last-operation.json', {})
            if receipt.get('format') != self.FORMAT or receipt.get('status') != 'complete':
                raise self.Error('No completed change is available to undo.', 'no-undo')
            items = []
            self._validate_receipt(receipt)
            eligible = []
            for index, row in enumerate(receipt['items']):
                if row.get('status') != 'completed':
                    continue
                public = {k: row.get(k) for k in ('kind', 'app', 'id')}
                try:
                    self._undo_check(row)
                    eligible.append(index)
                    items.append({**public, 'status': 'restore', 'reason': 'Restore the exact state before this change.'})
                except (self.Error, OSError, ValueError, KeyError) as exc:
                    items.append(self._public_error(public, exc))
            plan_id = self.core._new_review('undo-operation', {'receipt_hash': self.digest(receipt), 'eligible': eligible})
            return {'ok': True, 'plan_id': plan_id, 'label': 'Undo ' + receipt['label'], 'items': items,
                    'counts': {'restore': sum(r['status'] == 'restore' for r in items),
                               'protected': sum(r['status'] != 'restore' for r in items)}}

    def _undo_one(self, row):
        self._undo_check(row)
        if row['kind'] == 'skill':
            if row['app'] == 'hermes':
                self.core._hermes_toggle(row['id'].split('/')[-1], not row['before']['disabled'])
            else:
                directory = self.core.tool_dir(row['app'])
                entry = directory / row['id'].split('/')[-1]
                before = row['before']['entry']
                if before['kind'] == 'missing':
                    entry.unlink()
                else:
                    temporary = entry.with_name('.hermes-loadout-undo-' + uuid.uuid4().hex)
                    try:
                        os.symlink(before['target'], temporary, target_is_directory=before['directory'])
                        if entry.is_symlink():
                            self.core._replace_managed_link(temporary, entry)
                        else:
                            os.rename(temporary, entry)
                    finally:
                        if temporary.is_symlink():
                            temporary.unlink()
        elif row['kind'] == 'mcp':
            if row['app'] == 'hermes':
                self.mcp.toggle_hermes(row['id'], row['before']['enabled'])
            else:
                self.mcp.restore_server(row['id'], row['app'], self._mcp_backup_entry(row), row['after']['fingerprint'])
        elif row['kind'] == 'catalog-bypass':
            directory = self.core.tool_dir(row['app'])
            os.symlink(row['before']['target'], directory / row['id'], target_is_directory=row['before']['directory'])
        else:  # import, preserve the owned canonical copy outside discovery as well
            destination = Path(row['path'])
            parked = self._path('undo-copies', create=True) / (uuid.uuid4().hex + '-' + row['id'])
            parked.parent.mkdir(exist_ok=True, mode=0o700)
            os.rename(destination, parked)
            try:
                if row.get('source_app'):
                    self.core._restore_tool_entry(self.core.tool_dir(row['source_app']), row['id'], Path(row['backup']))
            except (self.Error, OSError):
                if not os.path.lexists(destination):
                    os.rename(parked, destination)
                raise
            if not row['hermes_disabled_before']:
                self.core._hermes_toggle(row['id'], True)

    def undo(self, plan_id):
        with self.core._lock, self.mcp._lock:
            review = self.core._take_review(plan_id, 'undo-operation')
            receipt = self._read('last-operation.json', {})
            if self.digest(receipt) != review['receipt_hash'] or self._path('pending-operation.json').exists():
                raise self.Error('The last change is different. Review undo again.', 'review-mismatch')
            self._validate_receipt(receipt)
            result = self._receipt('Undo ' + receipt['label'], 'undo')
            eligible = set(review['eligible'])
            if eligible:
                self._write('pending-operation.json', result)
            for index in reversed(range(len(receipt['items']))):
                row = receipt['items'][index]
                if row.get('status') != 'completed':
                    continue
                public = {k: row.get(k) for k in ('kind', 'app', 'id')}
                if index not in eligible:
                    result['items'].append({**public, 'status': 'protected', 'ok': False,
                        'error': 'This item was not eligible in the reviewed undo and was left untouched.'})
                    continue
                try:
                    self._undo_one(row)
                    row['status'] = 'undone'
                    result['items'].append({**public, 'status': 'completed', 'ok': True})
                    # Persist progress on the same single undo point, not a new history.
                    self._write('last-operation.json', receipt)
                    self._write('pending-operation.json', result)
                except (self.Error, OSError, ValueError, KeyError) as exc:
                    if row.get('status') == 'undone':
                        result['status'] = 'recovery-required'
                        return {'ok': False, 'code': 'recovery-required',
                                'error': 'Undo changed an entry but could not save progress. Preserve the pending record.',
                                'receipt': self._summary(result)}
                    result['items'].append(self._public_error(public, exc))
            receipt['status'] = 'complete' if any(row.get('status') == 'completed' for row in receipt['items']) else 'undone'
            try:
                self._write('last-operation.json', receipt)
                self._path('pending-operation.json').unlink(missing_ok=True)
            except (self.Error, OSError):
                result['status'] = 'recovery-required'
                return {'ok': False, 'code': 'recovery-required', 'error': 'Undo progress needs recovery before another operation.',
                        'receipt': self._summary(result)}
            self.core.invalidate()
            result['status'] = 'undone'
            return {'ok': True, 'receipt': self._summary(result)}

    def apply_import(self, entries, category, plan_id):
        with self.core._lock:
            receipt = self._receipt('Import skills', 'import')
            self._write('pending-operation.json', receipt)
            try:
                result = self.core.import_apply_plan(entries, category, plan_id=plan_id)
            except (self.Error, OSError):
                self._path('pending-operation.json').unlink()
                raise
            for row in result['results']:
                item = {**row, 'kind': 'import', 'app': row.get('tool') or 'library', 'id': row['name'],
                        'source_app': row.get('tool'), 'status': 'completed' if row['ok'] else 'protected'}
                receipt['items'].append(item)
            if any(row.get('recovery_required') for row in result['results']):
                self._write('pending-operation.json', receipt)
                return {**result, 'ok': False, 'code': 'recovery-required'}
            self._write('pending-operation.json', receipt)
            summary = self._complete(receipt)
            result['operation'] = summary['receipt']
            return result

    def apply_catalog_repair(self, plan_id):
        with self.core._lock:
            receipt = self._receipt('Repair catalog bypass', 'repair')
            self._write('pending-operation.json', receipt)
            try:
                result = self.core.repair_catalog(plan_id)
            except (self.Error, OSError):
                self._path('pending-operation.json').unlink()
                raise
            receipt['items'].append({**result, 'kind': 'catalog-bypass', 'app': result['tool'],
                                     'id': result['name'], 'status': 'completed'})
            self._write('pending-operation.json', receipt)
            return self._complete(receipt)
