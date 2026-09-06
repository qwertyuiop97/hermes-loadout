"""MCP switchboard tests — catalog parse, hermes toggle, Claude Desktop writer.

Fixtures only; no real config files are touched.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tests"))
from test_plugin_api import Fixture, pa  # noqa: E402

McpCore = pa.McpCore
SkillsToggleError = pa.SkillsToggleError

CONFIG = """# header comment
model:
  primary: sonnet

mcp_servers:
  chrome-devtools:
    enabled: true
    command: npx
    args:
      - -y
      - chrome-devtools-mcp@latest
      - --browser-url=http://127.0.0.1:9222
  weather:
    enabled: false
    command: uvx
    args:
      - mcp-weather
    env:
      API_KEY: sk-test-123
  docs:
    command: node
    args:
      - docs-server.js

plugins:
  enabled: []
"""

CLAUDE_CONFIG = {
    "mcpServers": {
        "chrome-devtools": {"command": "npx", "args": ["-y", "chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9222"]},
        "docs": {"command": "node", "args": ["old.js"]},
        "drifted-server": {"command": "python", "args": ["-m", "foreign"]},
    },
    "preferences": {"theme": "dark"},
}


class McpFixture(Fixture):
    def __init__(self) -> None:
        super().__init__()
        (self.home / "config.yaml").write_text(CONFIG, encoding="utf-8")
        self.claude = self.tmp / "claude" / "claude_desktop_config.json"
        self.claude.parent.mkdir(parents=True, exist_ok=True)
        self.claude.write_text(json.dumps(CLAUDE_CONFIG, indent=2), encoding="utf-8")
        self.mcp = McpCore(self.home, claude_desktop_config=self.claude, log_path=self.tmp / "data" / "m.log")


class ParseTests(unittest.TestCase):
    def test_parse_mcp_servers(self) -> None:
        parsed = pa.parse_mcp_servers(CONFIG)
        self.assertEqual(sorted(parsed), ["chrome-devtools", "docs", "weather"])
        chrome = parsed["chrome-devtools"]
        self.assertTrue(chrome["enabled"])
        self.assertEqual(chrome["definition"]["command"], "npx")
        self.assertEqual(chrome["definition"]["args"][-1], "--browser-url=http://127.0.0.1:9222")
        weather = parsed["weather"]
        self.assertFalse(weather["enabled"])
        self.assertEqual(weather["definition"]["env"], {"API_KEY": "sk-test-123"})

    def test_parse_edge_cases(self) -> None:
        text = (
            "mcp_servers:\n"
            "  # a comment first\n"
            "  url-server:\n"
            "    enabled: true\n"
            "    url: https://example.com/mcp?v=1#frag\n"
            "    headers:\n"
            "      Authorization: Bearer abc # trailing comment stripped\n"
            "  quoted:\n"
            "    command: \"/bin/do a # thing\"\n"
            "    args:\n"
            "      - \"x # y\"\n"
            "  colon-value:\n"
            "    command: npx\n"
            "    env:\n"
            "      TOKEN: a:b:c\n"
        )
        parsed = pa.parse_mcp_servers(text)
        u = parsed["url-server"]["definition"]
        assert u["url"] == "https://example.com/mcp?v=1#frag"
        assert u["headers"]["Authorization"] == "Bearer abc"
        assert parsed["quoted"]["definition"]["command"] == "/bin/do a # thing"
        assert parsed["quoted"]["definition"]["args"] == ["x # y"]
        assert parsed["colon-value"]["definition"]["env"]["TOKEN"] == "a:b:c"
        # edit round-trip preserves tricky content
        out = pa.set_mcp_server_enabled(text, "url-server", False)
        p3 = pa.parse_mcp_servers(out)
        assert p3["url-server"]["enabled"] is False
        assert p3["url-server"]["definition"]["url"] == "https://example.com/mcp?v=1#frag"
        assert p3["url-server"]["definition"]["headers"]["Authorization"] == "Bearer abc"
        assert p3["quoted"]["definition"]["args"] == ["x # y"]
        assert p3["colon-value"]["definition"]["env"]["TOKEN"] == "a:b:c"

    def test_parse_no_block_and_garbage(self) -> None:
        self.assertEqual(pa.parse_mcp_servers("model: x\n"), {})
        self.assertEqual(pa.parse_mcp_servers(""), {})

    def test_set_enabled_flip_insert_and_selfcheck(self) -> None:
        out = pa.set_mcp_server_enabled(CONFIG, "weather", True)
        self.assertTrue(pa.parse_mcp_servers(out)["weather"]["enabled"])
        self.assertTrue(pa.parse_mcp_servers(out)["chrome-devtools"]["enabled"])  # untouched
        self.assertIn("# header comment", out)
        out2 = pa.set_mcp_server_enabled(out, "weather", False)
        self.assertFalse(pa.parse_mcp_servers(out2)["weather"]["enabled"])
        # flag missing entirely -> inserted as first key of the entry
        text = "mcp_servers:\n  bare:\n    command: node\n"
        out3 = pa.set_mcp_server_enabled(text, "bare", False)
        self.assertFalse(pa.parse_mcp_servers(out3)["bare"]["enabled"])
        self.assertIn("command: node", out3)
        # unknown server -> refuse, never corrupt
        try:
            pa.set_mcp_server_enabled(CONFIG, "ghost", True)
            self.fail("expected ConfigEditError")
        except pa.ConfigEditError:
            pass
        self.assertEqual(pa.parse_mcp_servers(CONFIG)["weather"]["enabled"], False)


CODEX_TOML = """# codex config
[desktop]
mode = "steps"

[mcp_servers.node_repl]
command = "/bin/node_repl"
args = ["--serve"]
startup_timeout_sec = 120

[mcp_servers.node_repl.env]
NODE_ENV = "production"

[mcp_servers.computer-use]
command = "./cu"
enabled = false

[mcp_servers.docs]
command = "node"
args = ["docs-server.js"]

[agents]
max_depth = 2 # trailing section
"""


class CodexWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = McpFixture()
        self.codex = self.fx.tmp / "codex" / "config.toml"
        self.codex.parent.mkdir(parents=True, exist_ok=True)
        self.codex.write_text(CODEX_TOML, encoding="utf-8")
        self.mcp = McpCore(self.fx.home, claude_desktop_config=self.fx.claude, log_path=None, codex_config=self.codex)

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_parse_codex_mcp(self) -> None:
        parsed = pa.parse_codex_mcp(CODEX_TOML)
        self.assertEqual(sorted(parsed), ["computer-use", "docs", "node_repl"])
        self.assertTrue(parsed["node_repl"]["enabled"])
        self.assertEqual(parsed["node_repl"]["definition"]["command"], "/bin/node_repl")
        self.assertEqual(parsed["node_repl"]["definition"]["args"], ["--serve"])
        self.assertEqual(parsed["node_repl"]["definition"]["env"]["NODE_ENV"], "production")
        self.assertFalse(parsed["computer-use"]["enabled"])
        self.assertIn("max_depth = 2", self.codex.read_text())

    def test_state_includes_codex_writer(self) -> None:
        st = self.mcp.mcp_state()
        rows = {r["name"]: r for r in st["rows"]}
        # docs matches the catalog entry command/args -> enabled
        self.assertEqual(rows["docs"]["writers"]["codex"], "enabled")
        # chrome-devtools is in the catalog but not in codex
        self.assertEqual(rows["chrome-devtools"]["writers"]["codex"], "missing")
        self.assertEqual(st["writers"]["codex"]["present"], True)

    def test_sync_appends_block_and_preserves_rest(self) -> None:
        r = self.mcp.sync_to_codex("chrome-devtools")
        self.assertEqual(r["action"], "created")
        text = self.codex.read_text()
        parsed = pa.parse_codex_mcp(text)
        self.assertEqual(parsed["chrome-devtools"]["definition"]["command"], "npx")
        # unrelated tables untouched
        self.assertIn("[desktop]", text)
        self.assertIn("max_depth = 2", text)
        self.assertIn("# codex config", text)
        self.assertTrue(list(self.codex.parent.glob("config.toml.bak.skills-toggle.*")))

    def test_sync_update_replaces_block(self) -> None:
        r = self.mcp.sync_to_codex("docs")
        self.assertEqual(r["action"], "updated")
        text = self.codex.read_text()
        self.assertEqual(text.count("[mcp_servers.docs]"), 1)
        parsed = pa.parse_codex_mcp(text)
        self.assertEqual(parsed["docs"]["definition"], {"command": "node", "args": ["docs-server.js"]})
        # node_repl env sub-table untouched
        self.assertEqual(pa.parse_codex_mcp(text)["node_repl"]["definition"]["env"]["NODE_ENV"], "production")

    def test_remove_drifted_refusal_and_force(self) -> None:
        # node_repl differs from any catalog entry -> foreign (not in catalog)
        try:
            self.mcp.remove_from_codex("node_repl")
            self.fail("expected error")
        except SkillsToggleError as exc:
            self.assertEqual(exc.code, "unknown-server")
        self.assertIn("[mcp_servers.node_repl]", self.codex.read_text())
        # remove docs (managed) -> block gone, rest intact
        r = self.mcp.remove_from_codex("docs")
        self.assertEqual(r["action"], "removed")
        text = self.codex.read_text()
        self.assertNotIn("[mcp_servers.docs]", text)
        self.assertIn("[mcp_servers.node_repl]", text)
        # noop on missing
        self.assertEqual(self.mcp.remove_from_codex("docs")["action"], "noop")

    def test_codex_state_on_missing_file(self) -> None:
        self.codex.unlink()
        mcp = McpCore(self.fx.home, claude_desktop_config=self.fx.claude, log_path=None, codex_config=self.fx.tmp / "nope.toml")
        st = mcp.mcp_state()
        self.assertFalse(st["writers"]["codex"]["present"])


class McpCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = McpFixture()
        self.mcp = self.fx.mcp

    def tearDown(self) -> None:
        self.fx.cleanup()

    def _claude_servers(self) -> dict:
        return json.loads(self.fx.claude.read_text())["mcpServers"]

    def test_state_rows_and_foreign(self) -> None:
        st = self.mcp.mcp_state()
        self.assertTrue(st["ok"])
        rows = {r["name"]: r for r in st["rows"]}
        self.assertEqual(rows["chrome-devtools"]["writers"]["claude"], "enabled")
        self.assertEqual(rows["chrome-devtools"]["enabled"], True)
        self.assertEqual(rows["weather"]["writers"]["claude"], "missing")
        self.assertEqual(rows["weather"]["enabled"], False)
        self.assertEqual([f["name"] for f in st["foreign"]], ["drifted-server"])  # in Claude, not in catalog
        # claude's non-mcpServers keys exist but are irrelevant to state
        self.assertEqual(st["counts"], {"catalog": 3, "foreign": 1})

    def test_toggle_hermes(self) -> None:
        r = self.mcp.toggle_hermes("weather", True)
        self.assertEqual(r["action"], "config-updated")
        self.assertTrue(pa.parse_mcp_servers((self.fx.home / "config.yaml").read_text())["weather"]["enabled"])
        backups = list(self.fx.home.glob("config.yaml.bak.skills-toggle.*"))
        self.assertEqual(len(backups), 1)
        self.assertIn("# header comment", (self.fx.home / "config.yaml").read_text())
        # idempotent
        self.assertEqual(self.mcp.toggle_hermes("weather", True)["action"], "noop")
        # unknown
        try:
            self.mcp.toggle_hermes("ghost", True)
            self.fail("expected error")
        except SkillsToggleError as exc:
            self.assertEqual(exc.code, "unknown-server")

    def test_sync_creates_and_preserves_other_keys(self) -> None:
        r = self.mcp.sync_to_claude("weather")
        self.assertEqual(r["action"], "created")
        servers = self._claude_servers()
        self.assertEqual(servers["weather"]["command"], "uvx")
        self.assertEqual(servers["weather"]["env"], {"API_KEY": "sk-test-123"})
        self.assertNotIn("enabled", servers["weather"])  # hermes-only key stripped
        doc = json.loads(self.fx.claude.read_text())
        self.assertEqual(doc["preferences"], {"theme": "dark"})  # untouched
        self.assertTrue(list(self.fx.claude.parent.glob("claude_desktop_config.json.bak.skills-toggle.*")))
        # re-sync is an update, idempotent content
        r = self.mcp.sync_to_claude("weather")
        self.assertEqual(r["action"], "updated")
        self.assertEqual(len(self._claude_servers()["weather"]["args"]), 1)

    def test_remove_noop_refusal_and_force(self) -> None:
        # missing -> noop
        self.assertEqual(self.mcp.remove_from_claude("weather")["action"], "noop")
        # drifted (same name, different content) -> refused without force
        try:
            self.mcp.remove_from_claude("docs")
            self.fail("expected error")
        except SkillsToggleError as exc:
            self.assertEqual(exc.code, "drifted")
        self.assertIn("docs", self._claude_servers())
        # force removes only that entry
        r = self.mcp.remove_from_claude("docs", force=True)
        self.assertEqual(r["action"], "removed")
        self.assertNotIn("docs", self._claude_servers())
        # managed (matches catalog) removes without force
        r = self.mcp.remove_from_claude("chrome-devtools")
        self.assertEqual(r["action"], "removed")
        # present in Claude but NOT in the catalog -> foreign, never touched
        try:
            self.mcp.remove_from_claude("drifted-server")
            self.fail("expected error")
        except SkillsToggleError as exc:
            self.assertEqual(exc.code, "unknown-server")
        # unknown to both -> noop (nothing to remove)
        self.assertEqual(self.mcp.remove_from_claude("totally-foreign")["action"], "noop")
        doc = json.loads(self.fx.claude.read_text())
        self.assertEqual(doc["preferences"], {"theme": "dark"})

    def test_sync_unknown_server(self) -> None:
        try:
            self.mcp.sync_to_claude("ghost")
            self.fail("expected error")
        except SkillsToggleError as exc:
            self.assertEqual(exc.code, "unknown-server")

    def test_env_values_redacted_in_state(self) -> None:
        st = self.mcp.mcp_state()
        row = next(r for r in st["rows"] if r["name"] == "weather")
        self.assertEqual(row["definition"]["env"], ["API_KEY"])  # key names only
        # but the writer still applies real values
        self.mcp.sync_to_claude("weather")
        self.assertEqual(self._claude_servers()["weather"]["env"], {"API_KEY": "sk-test-123"})

    def test_state_on_missing_claude_file(self) -> None:
        self.fx.claude.unlink()
        st = self.mcp.mcp_state()
        self.assertTrue(st["ok"])
        self.assertFalse(st["writers"]["claude"]["present"])
        rows = {r["name"]: r for r in st["rows"]}
        self.assertEqual(rows["chrome-devtools"]["writers"]["claude"], "missing")
        # sync creates the file (and parent dirs) from scratch
        r = self.mcp.sync_to_claude("chrome-devtools")
        self.assertEqual(r["action"], "created")
        self.assertEqual(self._claude_servers()["chrome-devtools"]["command"], "npx")


if __name__ == "__main__":
    unittest.main()
