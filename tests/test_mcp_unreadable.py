"""Unreadable client settings are never treated as empty writable documents."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mcp_backend import McpFixture, needs_yaml, pa


@needs_yaml
class McpUnreadableTests(unittest.TestCase):
    def test_unreadable_writer_refuses_sync_and_remove_without_changing_files(self):
        fixture = McpFixture()
        self.addCleanup(fixture.cleanup)
        codex = fixture.tmp / 'codex.toml'
        codex.write_bytes(b'[mcp_servers.docs]\r\ncommand="preserve"\r\n')
        mcp = pa.McpCore(fixture.home, fixture.claude, codex_config=codex)
        original_open = Path.open
        for writer, target, sync, remove in (
            ('codex', codex, mcp.sync_to_codex, mcp.remove_from_codex),
            ('claude', fixture.claude, mcp.sync_to_claude, mcp.remove_from_claude),
        ):
            with self.subTest(writer=writer):
                before = target.read_bytes()
                entries = sorted(str(path) for path in fixture.tmp.rglob('*'))

                def denied(path, mode='r', *args, **kwargs):
                    if path == target and 'r' in mode:
                        raise PermissionError('fixture read permission denied')
                    return original_open(path, mode, *args, **kwargs)

                with patch.object(Path, 'open', denied):
                    for operation in (sync, remove):
                        with self.assertRaises(pa.LoadoutError) as error:
                            operation('docs', force=True)
                        self.assertIn(error.exception.code, ('config-unreadable', 'config-invalid'))
                    state = mcp.mcp_state()
                    self.assertTrue(state['partial_failure'])
                    self.assertFalse(state['writers'][writer]['available'])
                    other = 'claude' if writer == 'codex' else 'codex'
                    self.assertTrue(state['writers'][other]['available'])
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual(sorted(str(path) for path in fixture.tmp.rglob('*')), entries)


if __name__ == '__main__':
    unittest.main()
