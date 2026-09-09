"""Catalog contracts and scope boundaries, with a disposable user home."""
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


class CatalogContractTests(unittest.TestCase):
    def test_catalog_contract_and_legacy_export_come_from_one_data_source(self):
        path = Path(pa.__file__).with_name("client_catalog.json")
        catalog = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema_version"], pa.CATALOG_VERSION)
        ids = [row["id"] for row in catalog["clients"]]
        self.assertEqual(len(ids), len(set(ids)))
        for row in catalog["clients"]:
            with self.subTest(client=row["id"]):
                self.assertRegex(row["id"], r"^[a-z0-9-]{1,18}$")
                self.assertIn(row["mcp_writer"], (None, "claude", "codex"))
                if row["verification"] == "documented":
                    self.assertTrue(row["sources"])
                    self.assertTrue(all(url.startswith("https://") for url in row["sources"]))
                else:
                    self.assertFalse(row["skills"])
                    self.assertFalse(row["may_create"])
                    self.assertEqual(row["global"] + row["project"], [])
                for path in row["project"]:
                    self.assertFalse(Path(path).is_absolute())
                    self.assertNotIn("..", Path(path).parts)
                if "legacy_default" in row:
                    self.assertEqual(pa.DEFAULT_TOOLS[row["id"]], row["legacy_default"])
        self.assertEqual(pa.catalog_client("codex")["global"], ["~/.agents/skills"])
        self.assertEqual(pa.catalog_client("amp")["global"], ["~/.config/agents/skills"])
        self.assertFalse(pa.catalog_client("openclaw")["project"])
        self.assertFalse(pa.catalog_client("claude-desktop")["skills"])


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="switchboard-scope-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.user = self.root / "user"
        self.user.mkdir()
        self.project = self.root / "project one"
        self.project.mkdir()
        self.home = self.user / ".hermes"
        self.home.mkdir()
        self.home_env = patch.dict(os.environ, {"HOME": str(self.user), "USERPROFILE": str(self.user), "HERMES_HOME": str(self.home), "OPENCODE_CONFIG_DIR": str(self.user / ".config/opencode")})
        self.home_env.start()
        self.addCleanup(self.home_env.stop)
        self.home_patch = patch.object(Path, "home", return_value=self.user)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.addCleanup(pa.reset_core)
        skill = self.home / "skills" / "coding" / "review-code"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: review-code\ndescription: Review source code.\n---\nUse deliberate checks.\n", encoding="utf-8")
        self.sid = "coding/review-code"
        self.core = pa.SkillsToggleCore(self.home, pa.load_tools_config(self.home))

    def activate(self, client="cursor", scope="project", project=None):
        root = (project or self.project) if scope == "project" else None
        catalog = self.core.clients(str(root) if root else None)
        row = next(row for row in catalog["clients"] if row["id"] == client)
        candidate = next(c for c in row["candidates"] if c["scope"] == scope)
        return self.core.enable_client(client, scope, str(root) if root else None, candidate["index"], candidate["dir"])

    def test_browsing_only_checks_catalog_locations_and_never_creates_folders(self):
        before = set(self.root.rglob("*"))
        with patch.object(Path, "rglob", side_effect=AssertionError("no whole-computer crawl")):
            global_only = self.core.clients()
            scoped = self.core.clients(str(self.project))
        self.assertEqual(before, set(self.root.rglob("*")))
        self.assertTrue(all(c["scope"] == "global" for row in global_only["clients"] for c in row["candidates"]))
        for row in scoped["clients"]:
            for candidate in row["candidates"]:
                if "dir" in candidate:
                    root = self.project if candidate["scope"] == "project" else self.user
                    self.assertTrue(pa.is_inside(Path(candidate["dir"]), root))
        self.assertFalse((self.project / ".cursor").exists())

    def test_every_verified_candidate_resolves_and_remains_inside_its_scope(self):
        for row in pa.CLIENT_CATALOG:
            for scope in ("global", "project"):
                for index in range(len(row[scope])):
                    with self.subTest(client=row["id"], scope=scope):
                        spec = pa.catalog_target(row["id"], scope, self.project, index)
                        target = pa.validate_scoped_target(spec)
                        expected_root = self.project if scope == "project" else self.user
                        self.assertTrue(pa.is_inside(target, expected_root))
                        self.assertNotEqual(target, expected_root)
                        self.assertFalse(target.exists())

    def test_global_and_two_project_targets_are_distinct_and_undo_survives_reload(self):
        second = self.root / "project two"
        second.mkdir()
        records = [self.activate(scope="global"), self.activate(), self.activate(project=second)]
        self.assertEqual(len({row["tool"] for row in records}), 3)
        self.assertEqual(self.activate()["tool"], records[1]["tool"])
        for row in records:
            with self.subTest(scope=row["scope"], tool=row["tool"]):
                target = Path(row["dir"])
                self.assertFalse(target.exists())  # activation saves configuration only
                plan = self.core.plan_bulk([self.sid], row["tool"], True)
                self.assertEqual(plan["would_change"], [self.sid])
                applied = self.core.execute_bulk(plan["would_change"], row["tool"], True)
                self.assertTrue((target / "review-code").is_symlink())
                reloaded = pa.SkillsToggleCore(self.home, pa.load_tools_config(self.home))
                undo = reloaded.undo_bulk(applied["receipt"]["receipt_id"])
                self.assertEqual(undo["changed"], 1)
                self.assertFalse((target / "review-code").is_symlink())
        state = self.core.state()
        self.assertTrue(all(row["configured"] for row in state["tools"] if row["id"] in {r["tool"] for r in records}))

    def test_apply_revalidates_metadata_changed_after_preview(self):
        active = self.activate()
        plan = self.core.plan_bulk([self.sid], active["tool"], True)
        (self.home / "skills" / self.sid / "SKILL.md").write_text("No frontmatter", encoding="utf-8")
        result = self.core.execute_bulk(plan["would_change"], active["tool"], True)
        self.assertEqual(result["changed"], 0)
        self.assertEqual(result["results"][0]["code"], "skill-incompatible")
        self.assertFalse(Path(active["dir"]).exists())

    def test_existing_custom_and_legacy_settings_are_preserved_on_activation(self):
        old = self.home / "skills-toggle.json"
        data = {"tools": {"special-client": str(self.root / "custom"), "cursor": {"label": "My cursor", "dir": str(self.root / "other")}}, "preferences": {"keep": True}}
        old.write_text(json.dumps(data), encoding="utf-8")
        self.core = pa.SkillsToggleCore(self.home, pa.load_tools_config(self.home))
        result = self.activate(scope="global")
        saved = json.loads(pa.user_config_path(self.home).read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["preferences"], data["preferences"])
        self.assertEqual(saved["tools"]["special-client"], data["tools"]["special-client"])
        self.assertEqual(saved["tools"]["cursor"], data["tools"]["cursor"])
        self.assertNotEqual(result["tool"], "cursor")
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertEqual(json.loads(old.read_text(encoding="utf-8")), data)

    def test_scope_changes_symlink_escape_and_moved_projects_fail_closed(self):
        active = self.activate()
        tool = active["tool"]
        self.core.execute_bulk([self.sid], tool, True, "before-move")
        native = self.project / ".cursor"
        aside = self.project / ".cursor-original"
        native.rename(aside)
        outside = self.root / "outside"
        (outside / "skills").mkdir(parents=True)
        native.symlink_to(outside, target_is_directory=True)
        for operation in (lambda: self.core.toggle(self.sid, tool, False),
                          lambda: self.core.plan_bulk([self.sid], tool, True),
                          lambda: self.core.ensure_tool_dir(tool),
                          lambda: self.core.undo_bulk("before-move")):
            with self.assertRaises(pa.SkillsToggleError):
                operation()
        self.assertEqual(list((outside / "skills").iterdir()), [])
        self.assertTrue((aside / "skills/review-code").is_symlink())
        self.assertTrue(next(t for t in self.core.state()["tools"] if t["id"] == tool)["read_only"])
        self.assertTrue(self.core.diff()["path_errors"])
        for inventory in (self.core.import_scan, self.core.list_backups, self.core.health):
            self.assertTrue(inventory()["ok"])
        native.unlink()
        aside.rename(native)
        self.project.rename(self.root / "moved-project")
        with self.assertRaises(pa.SkillsToggleError):
            self.core.toggle(self.sid, tool, False)

    def test_preview_refuses_reserved_and_nonstandard_skills_before_any_write(self):
        active = self.activate("claude")
        for name, frontmatter in (("synced", "name: synced\ndescription: Reserved name."), ("No spaces", "name: No spaces\ndescription: Invalid folder."), ("no-name", "description: Missing name.")):
            path = self.home / "skills/coding" / name
            path.mkdir()
            (path / "SKILL.md").write_text("---\n" + frontmatter + "\n---\n", encoding="utf-8")
            sid = "coding/" + name
            plan = self.core.plan_bulk([sid], active["tool"], True)
            self.assertEqual(plan["would_change"], [])
            self.assertEqual(plan["refused"][0]["code"], "skill-incompatible")
            with self.assertRaises(pa.SkillsToggleError):
                self.core.toggle(sid, active["tool"], True)
        self.assertFalse(Path(active["dir"]).exists())

    def test_shared_folder_is_disclosed_not_mistaken_for_every_installed_app(self):
        shared = self.user / ".agents/skills"
        shared.mkdir(parents=True)
        tools = pa.load_tools_config(self.home)
        self.assertNotIn("codex", tools)  # no Codex installation evidence
        self.assertIn("agents", tools)
        (self.user / ".codex").mkdir()
        tools = pa.load_tools_config(self.home)
        self.assertEqual(tools["codex"]["dir"], str(shared))
        self.core = pa.SkillsToggleCore(self.home, tools)
        candidates = next(row for row in self.core.clients()["clients"] if row["id"] == "codex")["candidates"]
        self.assertTrue(candidates[0]["shared"])
        self.assertIn("Shared Agent Skills", candidates[0]["shared_with"])

    def test_old_codex_location_is_not_silently_retargeted(self):
        old = self.user / ".codex/skills"
        old.mkdir(parents=True)
        (self.user / ".agents/skills").mkdir(parents=True)
        tool = pa.load_tools_config(self.home)["codex"]
        self.assertEqual(tool["dir"], str(old))
        self.assertEqual(tool["scope"], "custom")

    def test_unverified_candidates_are_read_only_until_explicit_custom_configuration(self):
        grok = self.user / ".grok/skills"
        grok.mkdir(parents=True)
        self.core = pa.SkillsToggleCore(self.home, pa.load_tools_config(self.home))
        with self.assertRaises(pa.SkillsToggleError):
            self.core.toggle(self.sid, "grok", True)
        with self.assertRaises(pa.SkillsToggleError):
            self.core.enable_client("grok", "global", expected_dir=str(grok))
        self.core.set_tool("grok", "My verified custom client", str(grok))
        reloaded = pa.SkillsToggleCore(self.home, pa.load_tools_config(self.home))
        self.assertTrue(reloaded.toggle(self.sid, "grok", True)["ok"])

    def test_invalid_selection_or_unreviewed_target_never_saves_a_mapping(self):
        good = pa.catalog_target("cursor", "project", self.project)["dir"]
        for root in (None, "relative/path", "", str(self.root / "missing")):
            with self.subTest(root=root), self.assertRaises(pa.SkillsToggleError):
                self.core.enable_client("cursor", "project", root, expected_dir=good)
        for args in (("cursor", "unknown", self.project, 0, good), ("cursor", "project", self.project, True, good), ("cursor", "project", self.project, 0, str(self.root / "other"))):
            with self.assertRaises(pa.SkillsToggleError):
                self.core.enable_client(*args)
        self.assertFalse(pa.user_config_path(self.home).exists())
        with patch.dict(os.environ, {"OPENCODE_CONFIG_DIR": str(self.root / "outside")}):
            row = next(row for row in self.core.clients()["clients"] if row["id"] == "opencode")
            self.assertIn("error", row["candidates"][0])

    def test_ambiguous_canonical_names_cannot_replace_each_other(self):
        duplicate = self.home / "skills/other/review-code"
        duplicate.mkdir(parents=True)
        (duplicate / "SKILL.md").write_text("---\nname: review-code\ndescription: Other\n---\n", encoding="utf-8")
        active = self.activate()
        plan = self.core.plan_bulk([self.sid], active["tool"], True)
        self.assertEqual(plan["refused"][0]["code"], "ambiguous-skill")
        self.assertFalse(Path(active["dir"]).exists())

    def test_malformed_configuration_never_falls_back_to_default_writable_targets(self):
        path = pa.user_config_path(self.home)
        for content in ('{broken', '{"tools": []}', '{"schema_version": 999}', '{"schema_version": true}', '{"tools":{"x":{"dir":"/x","scope":"surprise"}}}'):
            path.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(pa.SkillsToggleError):
                pa.load_tools_config(self.home)
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_http_catalog_activation_and_config_repair(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("HTTP dependencies are optional on the Python 3.9 core gate")
        pa.reset_core()
        app = FastAPI()
        app.include_router(pa.router)
        with TestClient(app) as client:
            candidate = next(row for row in client.get("/clients", params={"project_root": str(self.project)}).json()["clients"] if row["id"] == "cursor")["candidates"][1]
            active = client.post("/clients/enable", json={"client_id": "cursor", "scope": "project", "project_root": str(self.project), "candidate": 0, "expected_dir": candidate["dir"]}).json()
            self.assertTrue(active["ok"])
            self.assertIn(active["tool"], {row["id"] for row in client.get("/state").json()["tools"]})
            path = pa.user_config_path(self.home)
            old = path.read_text(encoding="utf-8")
            path.write_text('{invalid', encoding="utf-8")
            for route in ("/state", "/clients", "/import/scan", "/drift", "/backups"):
                response = client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["ok"])
                self.assertEqual(response.json()["code"], "config-invalid")
            path.write_text(old, encoding="utf-8")
            self.assertTrue(client.get("/state").json()["ok"])


if __name__ == "__main__":
    unittest.main()
