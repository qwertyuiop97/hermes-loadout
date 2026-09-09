"""Validated, minimally scoped edits to Codex TOML, without a build step.

Only the selected server's statements are rewritten. Foreign statements retain
exact bytes and comments. Layouts we cannot edit safely are refused, not
normalized into a replacement configuration.
"""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
import math
import re
import sys
import threading
from datetime import date, datetime, time
from pathlib import Path

_LOCK = threading.RLock()
_READER = None


def reader():
    global _READER
    with _LOCK:
        if _READER is None:
            path = Path(__file__).parent / '_vendor' / 'tomli' / '__init__.py'
            name = '_loadout_tomli_' + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
            module = sys.modules.get(name)
            if module is None:
                spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(path.parent)])
                if spec is None or spec.loader is None:
                    raise ValueError('bundled TOML reader is missing; reinstall Loadout')
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(name, None)
                    raise
            _READER = module
        return _READER


def load(text: str) -> dict:
    try:
        doc = reader().loads(text)
    except (ValueError, TypeError, RecursionError) as exc:
        # Parser diagnostics can include a line containing a credential.
        raise ValueError('Codex config is not valid TOML; repair it before syncing') from exc
    servers = doc.get('mcp_servers', {})
    if not isinstance(servers, dict) or any(not isinstance(v, dict) for v in servers.values()):
        raise ValueError('Codex mcp_servers must contain server tables')
    for server in servers.values():
        if 'enabled' in server and not isinstance(server['enabled'], bool):
            raise ValueError('Codex server enabled flags must be booleans')
    return doc


def key(value: str) -> str:
    return value if re.fullmatch(r'[A-Za-z0-9_-]+', value) else json.dumps(value, ensure_ascii=False)


def scalar(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return ('nan' if math.isnan(value) else ('inf' if value > 0 else '-inf')) if not math.isfinite(value) else repr(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, list):
        return '[' + ', '.join(scalar(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(key(k) + ' = ' + scalar(v) for k, v in value.items()) + '}'
    raise ValueError('unsupported Codex setting value; no changes made')


def block(name: str, definition: dict, newline: str = '\n') -> str:
    if not isinstance(name, str) or not name or '\0' in name:
        raise ValueError('invalid MCP server name')
    result = '[mcp_servers.' + key(name) + ']' + newline
    result += ''.join(key(k) + ' = ' + scalar(v) + newline for k, v in definition.items())
    # Check our serializer too, not just the document we read.
    load(result)
    return result


def _statements(text: str):
    """Yield (semantic key, original start, original end, is-header).

    Input is validated by load before this tokenizer is used. CRLF offsets map
    back to the original source so untouched slices retain their exact bytes.
    """
    parser = sys.modules[reader().__name__ + '._parser']
    chars, offsets = [], []
    i = 0
    while i < len(text):
        if text[i:i+2] == '\r\n':
            offsets.append(i); chars.append('\n'); i += 2
        else:
            offsets.append(i); chars.append(text[i]); i += 1
    offsets.append(len(text))
    src = ''.join(chars)
    pos, context = 0, ()
    while pos < len(src):
        line_start = pos
        pos = parser.skip_chars(src, pos, ' \t')
        if pos >= len(src):
            break
        if src[pos] == '\n':
            pos += 1; continue
        if src[pos] == '#':
            end = src.find('\n', pos)
            pos = len(src) if end < 0 else end + 1
            continue
        header = src[pos] == '['
        if header:
            array = src[pos:pos+2] == '[['
            pos, context = parser.parse_key(src, pos + (2 if array else 1))
            pos += 2 if array else 1
            path = context
        else:
            pos, parts, value = parser.parse_key_value_pair(src, pos, float, 0)
            path = context + parts
        end = src.find('\n', pos)
        pos = len(src) if end < 0 else end + 1
        yield path, offsets[line_start], offsets[pos], header


def replace_server(text: str, name: str, definition: dict | None) -> str:
    """Replace only one server, with a whole-document semantic self-check."""
    old = load(text)
    spans = []
    for path, start, end, is_header in _statements(text):
        if path == ('mcp_servers',) and not is_header:
            raise ValueError('inline mcp_servers layout is read-only; use separate server tables before syncing')
        if len(path) >= 2 and path[:2] == ('mcp_servers', name):
            spans.append((start, end))
    remaining, previous = [], 0
    for start, end in spans:
        remaining.append(text[previous:start]); previous = end
    remaining.append(text[previous:])
    result = ''.join(remaining)
    newline = '\r\n' if '\r\n' in text else '\n'
    if definition is not None:
        result += (newline if result and not result.endswith('\n') else '')
        result += newline + block(name, definition, newline)
    expected = copy.deepcopy(old)
    servers = expected.setdefault('mcp_servers', {})
    if definition is None:
        servers.pop(name, None)
    else:
        servers[name] = definition
    actual = load(result)
    # Removing the last table may omit the otherwise empty parent namespace.
    if not expected.get('mcp_servers'):
        expected.pop('mcp_servers', None)
        if not actual.get('mcp_servers'):
            actual.pop('mcp_servers', None)
    # NaN has non-reflexive equality; compare its serialized semantic form.
    if scalar(expected) != scalar(actual):
        # Table order is irrelevant, unlike content and array order.
        if json.dumps(expected, sort_keys=True, default=str) != json.dumps(actual, sort_keys=True, default=str):
            raise ValueError('Codex edit self-check failed; original configuration left untouched')
    return result
