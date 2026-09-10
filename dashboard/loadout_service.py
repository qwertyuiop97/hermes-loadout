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
        known = set(self.core.tools) | {'hermes', 'codex', 'claude-desktop'}
        if any(app not in known for app in apps):
            raise self.Error('An application is no longer available. Refresh the selection.', 'unknown-tool')
        states, excluded = [], []
        inventory = self.core.state()
        for app in dict.fromkeys(apps):
            if app in self.core.tools:
                for skill in inventory['skills']:
                    state = skill['tools'][app]['state']
                    if state in ('enabled', 'disabled', 'missing'):
                        states.append({'kind': 'skill', 'app': app, 'id': skill['id'], 'enabled': state == 'enabled'})
                    else:
                        excluded.append({'kind': 'skill', 'app': app, 'id': skill['id'], 'status': state})
            if app in ('hermes', 'codex', 'claude-desktop'):
                for row in self.mcp.mcp_state()['rows']:
                    state = ('enabled' if row['enabled'] else 'disabled') if app == 'hermes' else row['writers'].get('claude' if app == 'claude-desktop' else app)
                    if state in ('enabled', 'disabled', 'missing'):
                        states.append({'kind': 'mcp', 'app': app, 'id': row['name'], 'enabled': state == 'enabled'})
                    else:
                        excluded.append({'kind': 'mcp', 'app': app, 'id': row['name'], 'status': state})
        return {'ok': True, 'states': states, 'excluded': excluded}

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
        status = 'protected' if code in ('protected', 'foreign-link', 'unmanaged-dir', 'catalog-bypass', 'config-protected', 'store-protected', 'read-only') else (
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
        counts = {key: sum(row.get('status') == key for row in items) for key in
                  ('completed', 'unchanged', 'protected', 'conflict', 'unavailable', 'failed', 'pending')}
        failed = sum(row.get('status') not in ('completed', 'unchanged') for row in items)
        return {'receipt_id': receipt['receipt_id'], 'label': receipt['label'], 'kind': receipt['kind'],
                'status': receipt['status'], 'items': items, 'changed': changed, 'counts': counts,
                'skipped': counts['unchanged'], 'failed': failed, 'partial': changed > 0 and failed > 0,
                'undo_available': changed > 0 and receipt['status'] == 'complete'}

    def _interrupted(self, receipt):
        # Never clear evidence after a possibly completed filesystem operation.
        receipt['status'] = 'recovery-required'
        self.core.invalidate()
        return {'ok': False, 'code': 'recovery-required',
                'error': 'An operation was interrupted. Preserve the pending record and backups; inspect recovery before making another change.',
                'receipt': self._summary(receipt)}

    def _complete(self, receipt):
        receipt['status'] = 'complete'
        changed = any(row.get('status') == 'completed' for row in receipt['items'])
        if changed:
            self._write('last-operation.json', receipt)
        self._path('pending-operation.json').unlink(missing_ok=True)
        self.core.invalidate()
        summary = self._summary(receipt)
        return {'ok': True, 'receipt': summary, 'changed': summary['changed'], 'partial': summary['partial']}

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
                    uncertain = row.get('status') == 'completed'
                    if row.get('status') == 'pending':
                        try:
                            uncertain = self._snapshot(row) != row['before']
                        except (self.Error, OSError, ValueError):
                            uncertain = True
                    if uncertain:
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
            if not isinstance(row, dict) or row.get('kind') not in ('skill', 'mcp', 'import', 'catalog-bypass', 'conflict', 'backup'):
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
        elif row.get('kind') == 'conflict':
            self._check_conflict_undo(row)
        elif row.get('kind') == 'backup':
            self._check_backup_undo(row)
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
        elif row['kind'] == 'conflict':
            directory, canonical = self._check_conflict_undo(row)
            if row['choice'] == 'source':
                os.rename(canonical, row['undo_parked'])
                os.rename(row['canonical_backup'], canonical)
            self.core._restore_tool_entry(directory, row['id'], Path(row['tool_backup']))
        elif row['kind'] == 'backup':
            target = self._check_backup_undo(row)
            if row['descriptor']['kind'] in self._config_targets():
                if row['before']['kind'] == 'missing':
                    target.unlink()
                else:
                    self.api._atomic_write_text(target, Path(row['pre_backup']).read_text(encoding='utf-8'))
                if row['descriptor']['kind'] == 'tools-json':
                    self.api.reset_core()
            else:
                os.rename(target, row['undo_parked'])
                if row['before']['kind'] != 'missing':
                    os.rename(row['pre_backup'], target)
        else:  # import, preserve the owned canonical copy outside discovery as well
            destination = Path(row['path'])
            parked = Path(row['undo_parked'])
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
                attempted = False
                progress = None
                try:
                    self._undo_check(row)
                    if row['kind'] == 'import':
                        destination = Path(row['path'])
                        row['undo_parked'] = str(self.core._new_tool_backup(destination.parent, destination.name))
                    elif row['kind'] == 'conflict' and row['choice'] == 'source':
                        destination = Path(row['before']['path'])
                        row['undo_parked'] = str(self.core._new_tool_backup(destination.parent, destination.name))
                    elif row['kind'] == 'backup' and row['descriptor']['kind'] not in self._config_targets():
                        destination = Path(row['target'])
                        row['undo_parked'] = str(self.core._new_tool_backup(destination.parent, destination.name))
                    progress = {**public, 'status': 'pending', 'original': copy.deepcopy(row)}
                    result['items'].append(progress)
                    self._write('pending-operation.json', result)
                    attempted = True
                    self._undo_one(row)
                    row['status'] = 'undone'
                    progress.update(status='completed', ok=True)
                    # Persist progress on the same single undo point, not a new history.
                    self._write('last-operation.json', receipt)
                    self._write('pending-operation.json', result)
                except (self.Error, OSError, ValueError, KeyError) as exc:
                    if row.get('status') == 'undone':
                        return self._interrupted(result)
                    if attempted:
                        try:
                            self._undo_check(row)  # an exception may have followed a partial undo
                        except (self.Error, OSError, ValueError, KeyError):
                            return self._interrupted(result)
                    if progress is not None:
                        result['items'].remove(progress)
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
            summary = self._summary(result)
            return {'ok': True, 'receipt': summary, 'changed': summary['changed'], 'partial': summary['partial']}

    def apply_import(self, entries, category, plan_id):
        with self.core._lock:
            metadata = self.metadata()  # fail before import if saved labels need repair
            receipt = self._receipt('Import skills', 'import')
            self._write('pending-operation.json', receipt)

            def record(row, status):
                item = {**row, 'kind': 'import', 'app': row.get('tool') or 'library', 'id': row['name'],
                        'source_app': row.get('tool'), 'status': status}
                if status == 'failed':
                    error = self.Error('Import could not finish safely. Review this source and destination.', row.get('code', 'copy-failed'))
                    item.update(self._public_error(item, error))
                key = (item['id'], item.get('source'))
                receipt['items'] = [old for old in receipt['items'] if (old['id'], old.get('source')) != key] + [item]
                self._write('pending-operation.json', receipt)

            try:
                result = self.core.import_apply_plan(entries, category, plan_id=plan_id, _record=record)
                if any(row.get('recovery_required') for row in result['results']):
                    return {**result, **self._interrupted(receipt)}
                for item in receipt['items']:
                    if item['status'] == 'completed':
                        # An origin label is informational, never a guess about portability.
                        source_app = item.get('source_app')
                        source = self.core.tools.get(source_app, {}).get('label', source_app) if source_app else 'Folder import'
                        metadata['skills'].setdefault(item['skill'], {})['source'] = source
                if any(item['status'] == 'completed' for item in receipt['items']):
                    self._write('inventory-metadata.json', {k: metadata[k] for k in ('version', 'skills')})
                summary = self._complete(receipt)
                result.update(operation=summary['receipt'], partial=summary['partial'])
                return result
            except (self.Error, OSError, ValueError):
                if receipt['items']:
                    return self._interrupted(receipt)
                self._path('pending-operation.json').unlink()
                raise

    def apply_catalog_repair(self, plan_id):
        with self.core._lock:
            receipt = self._receipt('Repair catalog bypass', 'repair')
            self._write('pending-operation.json', receipt)

            def record(row, status):
                receipt['items'] = [{**row, 'kind': 'catalog-bypass', 'app': row['tool'],
                                     'id': row['name'], 'status': status}]
                self._write('pending-operation.json', receipt)

            try:
                self.core.repair_catalog(plan_id, _record=record)
                return self._complete(receipt)
            except (self.Error, OSError, ValueError):
                if receipt['items']:
                    return self._interrupted(receipt)
                self._path('pending-operation.json').unlink()
                raise

    def _object_state(self, path):
        """Filesystem evidence only. Never retain configuration contents in receipts."""
        path = Path(path)
        entry = self.core._entry_snapshot(path)
        if entry['kind'] == 'symlink' or not os.path.lexists(path):
            return entry
        if path.is_file():
            if path.stat().st_size > 4 * 1024 * 1024:
                raise self.Error('The configuration exceeds the 4 MiB review limit.', 'record-invalid')
            return {'kind': 'file', 'identity': self.core._import_identity(path),
                    'hash': hashlib.sha256(path.read_bytes()).hexdigest()}
        if path.is_dir():
            return {'kind': 'directory', 'identity': self.core._import_identity(path),
                    'hash': self.core._skill_fingerprint(path)}
        raise self.Error('This entry is not an ordinary file or skill folder.', 'protected')

    def plan_conflict(self, app, name, choice):
        with self.core._lock:
            if choice not in ('library', 'source'):
                raise self.Error('Choose the library copy or the application copy.', 'invalid-choice')
            existing, directory, source = self.core._conflict_context(app, name, True)
            if self.core.catalog_bypasses():
                raise self.Error('Review catalog bypass links before replacing shared content.', 'catalog-bypass')
            skill = existing['category'] + '/' + existing['name']
            canonical = self.core._import_destination(existing['category'], existing['name'])
            # Ambiguous names, redirected folders, or linked file content need manual review.
            self.core._validate_skill(skill)
            if canonical.is_symlink():
                raise self.Error('The library skill is itself a link. Review its owner separately.', 'protected')
            self.core._validate_client_skill(existing, app)
            before = {'source': self._object_state(source), 'canonical': self._object_state(canonical),
                      'directory': self.api._canonical(str(directory)), 'path': str(canonical)}
            payload = {'app': app, 'id': name, 'skill': skill, 'choice': choice, 'before': before}
            plan_id = self.core._new_review('conflict-resolution', payload)
            reason = ('Replace only the application copy with a link to the library. Preserve the original outside discovery.'
                      if choice == 'library' else
                      'Replace the shared library content with this application copy. This affects every app already using the library copy. Preserve both originals; activation stays unchanged.')
            return {'ok': True, 'plan_id': plan_id, 'label': 'Resolve skill conflict',
                    'items': [{'kind': 'conflict', 'app': app, 'id': name, 'status': 'replace', 'reason': reason}]}

    def apply_conflict(self, plan_id):
        with self.core._lock:
            row = self.core._take_review(plan_id, 'conflict-resolution')
            existing, directory, source = self.core._conflict_context(row['app'], row['id'], True)
            canonical = self.core._import_destination(*row['skill'].split('/', 1))
            if (self.core.catalog_bypasses() or str(canonical) != row['before']['path']
                    or self.api._canonical(str(directory)) != row['before']['directory']
                    or self._object_state(source) != row['before']['source']
                    or self._object_state(canonical) != row['before']['canonical']):
                raise self.Error('One of the reviewed copies changed. Review both copies again.', 'changed-since-preview')
            receipt = self._receipt('Resolve skill conflict', 'conflict')
            row.update(kind='conflict', status='pending', tool_backup=str(self.core._new_tool_backup(directory, row['id'])))
            if row['choice'] == 'source':
                row['canonical_backup'] = str(self.core._new_tool_backup(canonical.parent, canonical.name))
                row['stage'] = str(self.core._new_tool_backup(canonical.parent, canonical.name))
            receipt['items'].append(row)
            self._write('pending-operation.json', receipt)
            try:
                if row['choice'] == 'source':
                    self.api.shutil.copytree(source, row['stage'], symlinks=True)
                    if (self.core._skill_fingerprint(Path(row['stage'])) != row['before']['source']['hash']
                            or self._object_state(source) != row['before']['source']
                            or self._object_state(canonical) != row['before']['canonical']):
                        raise self.Error('A copy changed while being prepared.', 'changed-since-preview')
                    os.rename(canonical, row['canonical_backup'])
                    os.rename(row['stage'], canonical)
                os.rename(source, row['tool_backup'])
                os.symlink(str(canonical.resolve()), source, target_is_directory=True)
                row.update(status='completed', ok=True, after={'source': self._object_state(source), 'canonical': self._object_state(canonical)})
                self._write('pending-operation.json', receipt)
                return self._complete(receipt)
            except (self.Error, OSError, ValueError):
                # Originals are preserved at recorded paths. Do not delete uncertain partial copies.
                return self._interrupted(receipt)

    def _check_conflict_undo(self, row):
        self.core._validate_tool(row['app'])
        directory = self.core.tool_dir(row['app'])
        canonical = self.core._import_destination(*row['skill'].split('/', 1))
        source = directory / self.core._entry_name(row['id'])
        if (self.api._canonical(str(directory)) != row['before']['directory'] or str(canonical) != row['before']['path']
                or self._object_state(source) != row['after']['source']
                or self._object_state(canonical) != row['after']['canonical']):
            raise self.Error('A resolved copy changed afterward. It will be preserved.', 'changed-since-apply')
        self.core._checked_backup(Path(row['tool_backup']), self.core._tool_backup_root(directory),
            re.escape(row['id']) + r'\.bak\.hermes-loadout\.\d{8}-\d{6}-[a-f0-9]{8}')
        if self.core._skill_fingerprint(Path(row['tool_backup'])) != row['before']['source']['hash']:
            raise self.Error('The original application copy changed.', 'backup-changed')
        if row['choice'] == 'source':
            self.core._checked_backup(Path(row['canonical_backup']), self.core._tool_backup_root(canonical.parent),
                re.escape(canonical.name) + r'\.bak\.hermes-loadout\.\d{8}-\d{6}-[a-f0-9]{8}')
            if self.core._skill_fingerprint(Path(row['canonical_backup'])) != row['before']['canonical']['hash']:
                raise self.Error('The original library copy changed.', 'backup-changed')
        return directory, canonical

    def _config_targets(self):
        return {'config': self.core.config_path, 'tools-json': self.api.user_config_path(self.core.home),
                'mcp-codex': self.mcp.codex_config, 'mcp-claude': self.mcp.claude_config}

    def list_backups(self):
        with self.core._lock, self.mcp._lock:
            rows = list(self.core.list_backups()['backups'])
            known = {row['path'] for row in rows}
            for kind, target in self._config_targets().items():
                if target is None:
                    continue
                pattern = re.escape(target.name) + r'\.bak\.hermes-loadout\.\d{8}-\d{6}(?:-\d+)?'
                for candidate in target.parent.glob(target.name + '.bak.hermes-loadout.*'):
                    if (str(candidate) not in known and candidate.is_file() and not candidate.is_symlink()
                            and re.fullmatch(pattern, candidate.name) and self.api.same_path(candidate.parent, target.parent)):
                        rows.append({'kind': kind, 'path': str(candidate), 'name': candidate.name})
            return {'ok': True, 'backups': sorted(rows, key=lambda row: row['path'], reverse=True), 'count': len(rows)}

    def _backup_target(self, row):
        if row['kind'] in self._config_targets():
            target = self._config_targets()[row['kind']]
            if target is None:
                raise self.Error('The configuration location is unavailable.', 'unavailable')
            if target.is_symlink():
                raise self.Error('The configuration is a link. It will not be replaced.', 'protected')
            return target
        if row['kind'] == 'tool-link':
            self.core._validate_tool(row['tool'])
            target = self.core.tool_dir(row['tool']) / self.core._entry_name(row['skill_name'])
            if target.is_symlink() and not self.api.is_inside(target, self.core.skills_root_resolved):
                raise self.Error('The current application link is foreign.', 'foreign-link')
            return target
        if row['kind'] in ('hermes-copy', 'canonical-copy'):
            name = row.get('skill_name')
            if name is None:
                match = re.fullmatch(r'\.hermes-loadout-(?:backup|reverted|replaced)-(.+)-\d{8}-\d{6}(?:-\d+)?', row['name'])
                if not match:
                    raise self.Error('This preserved filename is invalid.', 'invalid-backup')
                name = match.group(1)
            target = self.core._import_destination(row['category'], name)
            if target.is_symlink():
                raise self.Error('The library target is a link.', 'protected')
            # A missing canonical target could unexpectedly activate a skill on restore.
            self.core._validate_skill(row['category'] + '/' + name)
            return target
        raise self.Error('This backup requires manual recovery.', 'invalid-backup')

    def _validate_config_backup(self, kind, source):
        text = source.read_text(encoding='utf-8')
        if kind == 'tools-json':
            data, _ = self.api._read_json_mapping(source, 'tools')
            self.api._validate_tools_document(data)
        elif kind == 'mcp-codex':
            self.api._toml_support().load(text)
        elif kind == 'mcp-claude':
            self.api._read_json_mapping(source, 'mcpServers')
        else:
            # Whole-document restore must not treat a recognizable first line as
            # proof that arbitrary YAML is valid. Hermes normally provides PyYAML;
            # the dependency-free core refuses this one operation without it.
            try:
                import yaml
            except ImportError as exc:
                raise self.Error('Full Hermes configuration restore requires PyYAML in the backend environment. Other recovery actions remain available.', 'yaml-unavailable') from exc
            class UniqueSafeLoader(yaml.SafeLoader):
                pass
            def unique_mapping(loader, node, deep=False):
                result = {}
                for key_node, value_node in node.value:
                    key = loader.construct_object(key_node, deep=deep)
                    if key in result:
                        raise ValueError('duplicate YAML key')
                    result[key] = loader.construct_object(value_node, deep=deep)
                return result
            UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
            try:
                data = yaml.load(text, Loader=UniqueSafeLoader)
                if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
                    raise ValueError('expected a mapping')
                skills = data.get('skills', {})
                if not isinstance(skills, dict):
                    raise ValueError('invalid skills settings')
                disabled = skills.get('disabled', [])
                if not isinstance(disabled, list) or any(not isinstance(name, str) for name in disabled):
                    raise ValueError('invalid disabled skills')
                if not isinstance(data.get('mcp_servers', {}), dict):
                    raise ValueError('invalid MCP settings')
            except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
                raise self.Error('The full configuration backup is malformed or ambiguous. It was not restored.', 'invalid-backup') from exc
        return text

    def plan_backup(self, path):
        with self.core._lock, self.mcp._lock:
            descriptor = next((row for row in self.list_backups()['backups'] if row['path'] == path), None)
            if descriptor is None:
                raise self.Error('Choose a recognized backup from the refreshed list.', 'unknown-backup')
            source, target = Path(path), self._backup_target(descriptor)
            if descriptor['kind'] in self._config_targets():
                self._validate_config_backup(descriptor['kind'], source)
            row = {'kind': 'backup', 'app': descriptor.get('tool', 'library'), 'id': target.name,
                   'descriptor': descriptor, 'target': str(target), 'context': self.api._canonical(str(target.parent)),
                   'before': self._object_state(target), 'source': self._object_state(source)}
            plan_id = self.core._new_review('backup-restore', row)
            return {'ok': True, 'plan_id': plan_id, 'label': 'Restore backup', 'items': [
                {'kind': 'backup', 'app': row['app'], 'id': target.name, 'status': 'replace',
                 'reason': 'Replace this entire file or skill copy with the reviewed backup, including its saved settings. Preserve the current version for one-step undo.'}]}

    def apply_backup(self, plan_id):
        with self.core._lock, self.mcp._lock:
            row = self.core._take_review(plan_id, 'backup-restore')
            source = Path(row['descriptor']['path'])
            target = self._backup_target(row['descriptor'])
            if (str(target) != row['target'] or self.api._canonical(str(target.parent)) != row['context']
                    or self._object_state(source) != row['source'] or self._object_state(target) != row['before']):
                raise self.Error('The backup or current entry changed. Review again.', 'changed-since-preview')
            receipt = self._receipt('Restore backup', 'backup')
            is_file = row['descriptor']['kind'] in self._config_targets()
            if is_file:
                text = self._validate_config_backup(row['descriptor']['kind'], source)
                row['pre_backup'] = self.core._backup(target)
            else:
                row['pre_backup'] = str(self.core._new_tool_backup(target.parent, target.name))
                row['stage'] = str(self.core._new_tool_backup(target.parent, target.name))
            row['status'] = 'pending'
            receipt['items'].append(row)
            self._write('pending-operation.json', receipt)
            try:
                if is_file:
                    self.api._atomic_write_text(target, text)
                else:
                    self.api.shutil.copytree(source, row['stage'], symlinks=True)
                    if self.core._skill_fingerprint(Path(row['stage'])) != row['source']['hash']:
                        raise self.Error('The backup changed during the copy.', 'backup-changed')
                    if self._object_state(target) != row['before']:
                        raise self.Error('The current entry changed.', 'changed-since-preview')
                    if os.path.lexists(target):
                        os.rename(target, row['pre_backup'])
                    os.rename(row['stage'], target)
                row.update(status='completed', ok=True, after=self._object_state(target))
                self._write('pending-operation.json', receipt)
                result = self._complete(receipt)
                if row['descriptor']['kind'] == 'tools-json':
                    self.api.reset_core()
                return result
            except (self.Error, OSError, ValueError):
                return self._interrupted(receipt)

    def _check_backup_undo(self, row):
        target = self._backup_target(row['descriptor'])
        if (str(target) != row['target'] or self.api._canonical(str(target.parent)) != row['context']
                or self._object_state(target) != row['after']):
            raise self.Error('The restored entry changed afterward. It will be preserved.', 'changed-since-apply')
        before = row['before']
        if before['kind'] != 'missing':
            saved = Path(row['pre_backup'])
            if row['descriptor']['kind'] in self._config_targets():
                if (saved.is_symlink() or not self.api.same_path(saved.parent, target.parent)
                        or not re.fullmatch(re.escape(target.name) + r'\.bak\.hermes-loadout\.\d{8}-\d{6}(?:-\d+)?', saved.name)):
                    raise self.Error('The pre-restore backup is unsafe.', 'invalid-backup')
            else:
                if (not self.api.same_path(saved.parent, self.core._tool_backup_root(target.parent))
                        or not re.fullmatch(re.escape(target.name) + r'\.bak\.hermes-loadout\.\d{8}-\d{6}-[a-f0-9]{8}', saved.name)):
                    raise self.Error('The pre-restore backup is unsafe.', 'invalid-backup')
            if before['kind'] == 'symlink':
                pointer = Path(before['target'])
                if not self.api.is_inside(pointer if pointer.is_absolute() else target.parent / pointer, self.core.skills_root_resolved):
                    raise self.Error('The original pointer is no longer inside the library.', 'invalid-backup')
            current = self._object_state(saved)
            if (current.get('kind') != before.get('kind') or
                    (current.get('hash') != before.get('hash') if before['kind'] in ('file', 'directory') else
                     current.get('target') != before.get('target'))):
                raise self.Error('The pre-restore backup changed.', 'backup-changed')
        return target
