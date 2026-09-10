"""Reject stale package identities and obvious accidental private artifacts.

This is a tracked-tree guard, not a complete secret scanner or history audit.
Findings print locations only, never credential-like values.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = re.compile(r'skills(?:-|_| )?toggle|hermes(?:-|_| )?switchboard', re.I)
PRIVATE = [
    re.compile(r'/Users/(?!demo\b|fixture\b)[A-Za-z][^/\s"\']+/'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{40,}'),
]

def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    failures = []
    for name in filter(None, names):
        path = ROOT / name
        if not path.exists():
            continue  # locally staged removal, absent in the final committed tree
        if IDENTITY.search(name):
            failures.append(name + ': obsolete filename')
        if path.suffix in ('.log', '.pem', '.key', '.pyc') or name.split('/')[0] in ('data', 'node_modules'):
            failures.append(name + ': runtime or private artifact')
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeError:
            continue
        for line, value in enumerate(text.splitlines(), 1):
            if IDENTITY.search(value):
                failures.append(f'{name}:{line}: obsolete package identity')
            if any(pattern.search(value) for pattern in PRIVATE):
                failures.append(f'{name}:{line}: possible private content')
        if name.startswith('.github/workflows/') and re.search(r'contents:\s*write', text):
            failures.append(name + ': write-enabled implementation workflow')
    if failures:
        print('\n'.join(failures))
        return 1
    print('Tracked-tree identity/private-artifact checks passed; no historical audit implied.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
