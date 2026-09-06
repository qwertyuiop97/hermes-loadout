"""v2 backend tests: import/adoption, drift, custom tool config.

Everything runs against /tmp fixtures; originals are preserved (backup-renamed,
never deleted).
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import importlib.util
import shutil as _shutil
import sys

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tests"))

# Single module load — importing plugin_api twice (once here, once inside
# test_plugin_api) would create two distinct SkillsToggleError classes.
from test_plugin_api import Fixture, call, pa  # noqa: E402

SkillsToggleCore = pa.SkillsToggleCore
SkillsToggleError = pa.SkillsToggleError


class ImportDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.core = self.fx.core
        # a standalone skill in codex's dir: a REAL dir with SKILL.md (a copy,
        # not a symlink) — the classic "existing user" topology
        self.local = self.fx.codex / "my-local-skill"
        self.local.mkdir()
        (self.local / "SKILL.md").write_text(
            "---\nname: my-local-skill\ndescription: born outside hermes\n---\nbody",
            encoding="utf-8",
        )
        # and a drifted copy of a hermes skill: same name, different content
        self.drifted = self.fx.codex / "architecture-diagram"
        self.drifted.mkdir()
        (self.drifted / "SKILL.md").write_text(
            "---\nname: architecture-diagram\ndescription: local edit, drifted from hermes\n---\nbody",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_import_scan_classifies(self) -> None:
        scan = self.core.import_scan()
        self.assertTrue(scan["ok"])
        by_tool = {t["tool"]: t for t in scan["tools"]}
        kinds = {e["name"]: e["kind"] for e in by_tool["codex"]["entries"]}
        self.assertEqual(kinds.get("my-local-skill"), "unmanaged-skill")
        self.assertEqual(kinds.get("architecture-diagram"), "unmanaged-skill")
        # managed links classified in claude
        k2 = {e["name"]: e["kind"] for e in by_tool["claude"]["entries"]}
        self.assertEqual(k2.get("architecture-diagram"), "managed")
        # grok's foreign link + unmanaged dir classified
        k3 = {e["name"]: e["kind"] for e in by_tool["grok"]["entries"]}
        self.assertEqual(k3.get("apple-notes"), "foreign-link")
        self.assertEqual(k3.get("airtable"), "unmanaged-dir")
        # absent tool reported present:False
        self.assertFalse(by_tool["zcode"]["present"])
        counts = scan["counts"]
        self.assertGreaterEqual(counts["adoptable"], 2)
        self.assertGreaterEqual(counts["drifted"], 1)

    def test_scan_flags_conflict_and_drift_hash(self) -> None:
        scan = self.core.import_scan()
        by_tool = {t["tool"]: t for t in scan["tools"]}
        entry = next(e for e in by_tool["codex"]["entries"] if e["name"] == "architecture-diagram")
        self.assertTrue(entry["conflict"])
        self.assertEqual(entry["skill_id"], "creative/architecture-diagram")
        self.assertTrue(entry["drifted"])
        self.assertNotEqual(entry["hash"], entry["hermes_hash"])

    def test_import_apply_adopts_and_preserves_original(self) -> None:
        r = self.core.import_apply("codex", ["my-local-skill"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["adopted"], 1)
        # copied into the tree under imported/
        dest = self.fx.home / "skills" / "imported" / "my-local-skill" / "SKILL.md"
        self.assertTrue(dest.is_file())
        self.assertIn("born outside hermes", dest.read_text())
        # tool dir entry is now a symlink to the tree
        link = self.fx.codex / "my-local-skill"
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(os.readlink(link)).resolve(), dest.parent.resolve())
        # original preserved as a backup dir, never deleted
        backups = list(self.fx.codex.glob("my-local-skill.skills-toggle-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "SKILL.md").is_file())
        # the new skill shows up in /state and is linkable everywhere
        st = self.core.state()
        ids = [s["id"] for s in st["skills"]]
        self.assertIn("imported/my-local-skill", ids)

    def test_import_apply_conflict_reported_not_merged(self) -> None:
        r = self.core.import_apply("codex", ["architecture-diagram"])  # name exists in hermes
        self.assertTrue(r["ok"])
        self.assertEqual(r["adopted"], 0)
        self.assertFalse(r["results"][0]["ok"])
        self.assertTrue(r["results"][0].get("conflict"))
        self.assertIn("already exists", r["results"][0]["error"])
        # nothing changed
        self.assertTrue(self.drifted.is_dir() and not self.drifted.is_symlink())
        self.assertFalse((self.fx.home / "skills" / "imported" / "architecture-diagram").exists())

    def test_import_apply_rejects_unknown_and_keeps_state(self) -> None:
        r = self.core.import_apply("codex", ["nope", "my-local-skill", 42])
        self.assertEqual(r["adopted"], 1)
        self.assertFalse(r["results"][0]["ok"])
        self.assertFalse(r["results"][2]["ok"])
        self.assertTrue(r["results"][1]["ok"])
        # adopted one still fine
        self.assertTrue((self.fx.home / "skills" / "imported" / "my-local-skill" / "SKILL.md").is_file())

    def test_import_apply_bad_category_and_missing_dir(self) -> None:
        r = call(self.core.import_apply, "codex", ["my-local-skill"], "../escape")
        self.assertFalse(r["ok"])
        r = call(self.core.import_apply, "zcode", ["whatever"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "absent-dir")
        r = call(self.core.import_apply, "hermes", ["x"])
        self.assertFalse(r["ok"])
        # hermes untouched
        self.assertTrue(self.local.is_dir() and not self.local.is_symlink())

    def test_import_apply_swap_failure_rolls_back(self) -> None:
        # make the symlink step fail deterministically; the copy must be
        # rolled back out of the tree AND the original restored in place
        orig = os.symlink
        try:
            def boom(*a, **k):
                raise OSError("simulated failure")
            os.symlink = boom
            r = self.core.import_apply("codex", ["my-local-skill"])
        finally:
            os.symlink = orig
        self.assertEqual(r["adopted"], 0)
        self.assertIn("link swap failed", r["results"][0]["error"])
        # copy rolled back out of the tree
        self.assertFalse((self.fx.home / "skills" / "imported" / "my-local-skill").exists())
        # original restored exactly where it was, backup cleaned up by restore
        self.assertTrue(self.local.is_dir())
        self.assertFalse(self.local.is_symlink())
        self.assertEqual(list(self.fx.codex.glob("my-local-skill.skills-toggle-backup-*")), [])

    def test_drift_reports_differing_copies(self) -> None:
        d = self.core.drift()
        self.assertTrue(d["ok"])
        self.assertEqual(d["count"], 1)
        item = d["drifted"][0]
        self.assertEqual(item["tool"], "codex")
        self.assertEqual(item["skill_id"], "creative/architecture-diagram")
        self.assertNotEqual(item["external_hash"], item["hermes_hash"])
        # after the drifted copy is replaced by a proper link, it's no longer flagged
        self.core.import_apply("codex", ["my-local-skill"])  # unrelated adopt first
        _shutil.rmtree(self.drifted)
        self.core.toggle("creative/architecture-diagram", "codex", True)
        d2 = self.core.drift()
        self.assertEqual(d2["count"], 0)


class DriftPushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.core = self.fx.core
        self.drifted = self.fx.codex / "architecture-diagram"
        self.drifted.mkdir()
        (self.drifted / "SKILL.md").write_text(
            "---\nname: architecture-diagram\ndescription: local drifted edit\n---\nbody",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_push_backs_up_and_links_canonical(self) -> None:
        r = self.core.drift_push("codex", "architecture-diagram")
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "pushed")
        link = self.fx.codex / "architecture-diagram"
        self.assertTrue(link.is_symlink())
        self.assertEqual(
            Path(os.readlink(link)).resolve(),
            (self.fx.home / "skills" / "creative" / "architecture-diagram").resolve(),
        )
        backups = list(self.fx.codex.glob("architecture-diagram.skills-toggle-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertIn("local drifted edit", (backups[0] / "SKILL.md").read_text())
        # drift is resolved
        self.assertEqual(self.core.drift()["count"], 0)

    def test_push_is_noop_when_already_managed(self) -> None:
        self.core.drift_push("codex", "architecture-diagram")
        r = self.core.drift_push("codex", "architecture-diagram")
        self.assertEqual(r["action"], "noop")

    def test_push_refusals(self) -> None:
        # unknown in tree
        r = call(self.core.drift_push, "codex", "ghost-skill")
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "unknown-skill")
        # traversal / bad name
        for bad in ("../..", "a/b", "", None):
            r = call(self.core.drift_push, "codex", bad)
            self.assertFalse(r["ok"])
        # hermes
        r = call(self.core.drift_push, "hermes", "architecture-diagram")
        self.assertFalse(r["ok"])
        # real dir without SKILL.md is untouched (name must exist in tree to
        # reach this check — apple-notes does)
        plain = self.fx.codex / "apple-notes"
        plain.mkdir()
        r = call(self.core.drift_push, "codex", "apple-notes")
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "unmanaged-dir")
        self.assertTrue(plain.is_dir() and not plain.is_symlink())
        # symlink swap failure restores the original
        orig = os.symlink
        try:
            def boom(*a, **k):
                raise OSError("simulated")
            os.symlink = boom
            r = call(self.core.drift_push, "codex", "architecture-diagram")
        finally:
            os.symlink = orig
        self.assertFalse(r["ok"])
        self.assertIn("drift push failed", r["error"])
        self.assertTrue(self.drifted.is_dir() and not self.drifted.is_symlink())


class ConfigToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.core = self.fx.core

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_set_tool_writes_config_with_backup(self) -> None:
        r = self.core.set_tool("cursor", "Cursor", "~/cursor-skills")
        self.assertTrue(r["ok"])
        cfg = self.fx.home / "skills-toggle.json"
        self.assertTrue(cfg.is_file())
        data = json.loads(cfg.read_text())
        self.assertEqual(data["tools"]["cursor"]["label"], "Cursor")
        backups = list(self.fx.home.glob("skills-toggle.json.bak.skills-toggle.*"))
        self.assertEqual(len(backups), 0)  # first write: no prior file to back up
        # a second write backs up the first version
        self.core.set_tool("cursor", "Cursor 2", "~/cursor2")
        backups = list(self.fx.home.glob("skills-toggle.json.bak.skills-toggle.*"))
        self.assertEqual(len(backups), 1)

    def test_set_tool_rejects_bad_input(self) -> None:
        for bad in (("HERMES", "x", "~/y"), ("bad id", "x", "~/y"), ("ok-id", "", "~/y"), ("ok-id", "x", "")):
            r = call(self.core.set_tool, *bad)
            self.assertFalse(r["ok"])
        self.assertFalse((self.fx.home / "skills-toggle.json").exists())

    def test_set_tool_then_state_includes_it(self) -> None:
        with self.assertRaises(SkillsToggleError):
            self.core.set_tool("hermes", "Nope", "~/x")  # locked: hermes is config-backed
        self.core.set_tool("aider", "Aider", str(self.fx.tmp / "aider-skills"))
        # the loader sees the new tool on rebuild
        tools = pa.load_tools_config(self.fx.home)
        self.assertIn("aider", tools)
        self.assertEqual(tools["aider"]["dir"], str(self.fx.tmp / "aider-skills"))


if __name__ == "__main__":
    unittest.main()
