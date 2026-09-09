"""Preserve supported legacy settings without weakening scoped-path validation."""
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


class ConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="switchboard-migration-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.user = self.root / "user"
        self.home = self.user / ".hermes"
        self.home.mkdir(parents=True)
        self.project = self.root / "project"
        self.project.mkdir()
        env = patch.dict(os.environ, {"HOME": str(self.user), "USERPROFILE": str(self.user),
                                      "OPENCODE_CONFIG_DIR": str(self.user / ".config/opencode")})
        env.start()
        self.addCleanup(env.stop)
        home = patch.object(Path, "home", return_value=self.user)
        home.start()
        self.addCleanup(home.stop)
        self.addCleanup(pa.reset_core)

    def write_legacy(self, tools):
        path = pa.legacy_user_config_path(self.home)
        path.write_text(json.dumps({"tools": tools, "other": {"keep": True}}), encoding="utf-8")
        return path

    def test_label_only_overrides_keep_legacy_paths_without_granting_unverified_writes(self):
        old = self.write_legacy({"claude": {"label": "My Claude"},
                                 "codex": {"label": "My Codex"},
                                 "grok": {"label": "My Grok"}})
        before = old.read_bytes()
        (self.user / ".agents/skills").mkdir(parents=True)
        tools = pa.load_tools_config(self.home)
        self.assertEqual(tools["claude"]["label"], "My Claude")
        self.assertTrue(pa.same_path(pa.expand_path(tools["codex"]["dir"]), self.user / ".codex/skills"))
        self.assertEqual(tools["codex"]["scope"], "custom")
        self.assertTrue(tools["grok"]["read_only"])
        core = pa.SkillsToggleCore(self.home, tools)
        with self.assertRaises(pa.SkillsToggleError):
            core.ensure_tool_dir("grok")
        self.assertFalse((self.user / ".grok").exists())
        self.assertFalse(pa.user_config_path(self.home).exists())
        self.assertEqual(old.read_bytes(), before)

    def test_long_existing_ids_survive_explicit_save_and_reload(self):
        tid = "existing-custom-" + "x" * 40
        original = {"label": "Existing client", "dir": str(self.root / "original")}
        old = self.write_legacy({tid: original, "claude": {"label": "My Claude"}})
        before = old.read_bytes()
        core = pa.SkillsToggleCore(self.home, pa.load_tools_config(self.home))
        result = core.set_tool(tid, "Updated client", str(self.root / "updated"))
        saved = json.loads(pa.user_config_path(self.home).read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["other"], {"keep": True})
        self.assertEqual(saved["tools"]["claude"], {"label": "My Claude"})
        self.assertEqual(pa.load_tools_config(self.home)[tid]["label"], "Updated client")
        self.assertEqual(Path(result["backup"]).read_bytes(), before)
        self.assertEqual(old.read_bytes(), before)
        self.assertFalse((self.root / "updated").exists())
        with self.assertRaises(pa.SkillsToggleError):
            core.set_tool("new-" + "x" * 40, "New client", str(self.root / "new"))

    def test_malformed_scope_metadata_is_a_recoverable_error_not_a_type_error(self):
        valid = pa.catalog_target("cursor", "project", self.project)
        for key, value in (("scope_root", None), ("scope_root", []),
                           ("project_root", 42), ("dir", None), ("dir", "/\0invalid")):
            with self.subTest(key=key, value=value):
                spec = dict(valid, **{key: value})
                with self.assertRaises(pa.SkillsToggleError):
                    pa.validate_scoped_target(spec)
                core = pa.SkillsToggleCore(self.home, {"bad": dict(spec, configured=True)})
                state = core.state()
                self.assertTrue(state["ok"])
                self.assertTrue(state["tools"][0]["read_only"])
                self.assertTrue(state["tools"][0]["path_error"])
        self.assertFalse((self.project / ".cursor").exists())

    def test_one_catalog_entry_drives_discovery_activation_and_labels(self):
        row = copy.deepcopy(pa.catalog_client("cursor"))
        row.update({"id": "fixture-client", "label": "Fixture client",
                    "global": ["~/.fixture-client/skills"], "project": [".fixture-client/skills"]})
        row.pop("legacy_default", None)
        with patch.object(pa, "CLIENT_CATALOG", pa.CLIENT_CATALOG + [row]):
            tools = pa.load_tools_config(self.home)
            core = pa.SkillsToggleCore(self.home, tools)
            candidate = next(r for r in core.clients()["clients"] if r["id"] == row["id"])["candidates"][0]
            self.assertEqual(tools[row["id"]]["label"], row["label"])
            self.assertFalse(candidate["detected"])
            result = core.enable_client(row["id"], "global", expected_dir=candidate["dir"])
            reloaded = pa.load_tools_config(self.home)
            self.assertTrue(reloaded[result["tool"]]["configured"])
            self.assertEqual(reloaded[result["tool"]]["label"], row["label"])
            self.assertFalse(Path(candidate["dir"]).exists())


if __name__ == "__main__":
    unittest.main()
