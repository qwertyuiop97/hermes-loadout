"""Scope validation and data-driven client setup contracts."""
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


class ConfigScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="loadout-migration-")
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




    def test_malformed_scope_metadata_is_a_recoverable_error_not_a_type_error(self):
        valid = pa.catalog_target("cursor", "project", self.project)
        for key, value in (("scope_root", None), ("scope_root", []),
                           ("project_root", 42), ("dir", None), ("dir", "/\0invalid")):
            with self.subTest(key=key, value=value):
                spec = dict(valid, **{key: value})
                with self.assertRaises(pa.LoadoutError):
                    pa.validate_scoped_target(spec)
                core = pa.HermesLoadoutCore(self.home, {"bad": dict(spec, configured=True)})
                state = core.state()
                self.assertTrue(state["ok"])
                self.assertTrue(state["tools"][0]["read_only"])
                self.assertTrue(state["tools"][0]["path_error"])
        self.assertFalse((self.project / ".cursor").exists())

    def test_one_catalog_entry_drives_discovery_activation_and_labels(self):
        row = copy.deepcopy(pa.catalog_client("cursor"))
        row.update({"id": "fixture-client", "label": "Fixture client",
                    "global": ["~/.fixture-client/skills"], "project": [".fixture-client/skills"]})
        with patch.object(pa, "CLIENT_CATALOG", pa.CLIENT_CATALOG + [row]):
            tools = pa.load_tools_config(self.home)
            core = pa.HermesLoadoutCore(self.home, tools)
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
