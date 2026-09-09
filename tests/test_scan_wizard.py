"""Filesystem-safety tests for the first-run scan/adoption backend."""

from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from test_plugin_api import Fixture, SKILL_MD_TEMPLATE, call, pa  # noqa: E402


def make_skill(root: Path, name: str, desc: str = "external") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        SKILL_MD_TEMPLATE.format(name=name, desc=desc), encoding="utf-8"
    )
    return skill


def selected(entry: dict) -> dict:
    return {"name": entry["name"], "source": entry["source"], "tool": entry.get("tool")}


class ScanWizardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.core = self.fx.core
        self.scan_a = self.fx.tmp / "scan-a"
        self.scan_b = self.fx.tmp / "scan-b"
        self.scan_a.mkdir()
        self.scan_b.mkdir()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_multiple_roots_group_duplicates_and_classify_against_hermes(self) -> None:
        make_skill(self.scan_a, "unique-a")
        make_skill(self.scan_a, "same-name", "first")
        make_skill(self.scan_b, "same-name", "second")
        shutil.copytree(
            self.fx.home / "skills" / "apple" / "apple-notes",
            self.scan_a / "apple-notes",
        )
        make_skill(self.scan_b, "architecture-diagram", "different external copy")

        plan = self.core.import_plan(["codex"], [str(self.scan_a), str(self.scan_b)], "imported")

        self.assertTrue(plan["ok"])
        by_name = {}
        for entry in plan["entries"]:
            by_name.setdefault(entry["name"], []).append(entry)
        self.assertEqual(by_name["apple-notes"][0]["kind"], "identical-duplicate")
        self.assertEqual(by_name["architecture-diagram"][0]["kind"], "drifted")
        self.assertEqual({row["kind"] for row in by_name["same-name"]}, {"name-conflict"})
        self.assertTrue(all(row["conflict"] for row in by_name["same-name"]))
        duplicate = next(group for group in plan["duplicate_groups"] if group["name"] == "same-name")
        self.assertEqual(len(duplicate["entries"]), 2)
        self.assertEqual([row["name"] for row in plan["adoptable"]], ["unique-a"])
        self.assertEqual(plan["totals"]["sources"], 3)  # codex plus two selected roots

    def test_invalid_missing_file_nested_and_symlinked_roots_are_refused(self) -> None:
        missing = self.fx.tmp / "missing"
        file_root = self.fx.tmp / "not-a-directory"
        file_root.write_text("x", encoding="utf-8")
        nested = self.scan_a / "nested"
        nested.mkdir()
        linked = self.fx.tmp / "linked-root"
        os.symlink(self.scan_b, linked)

        plan = self.core.import_plan(
            ["codex"],
            ["", str(missing), str(file_root), str(self.scan_a), str(nested), str(linked), 7],
            "imported",
        )

        refused = {(str(row["root"]), row["code"]) for row in plan["refused"]}
        self.assertIn(("", "invalid-root"), refused)
        self.assertIn((str(missing), "not-dir"), refused)
        self.assertIn((str(file_root), "not-dir"), refused)
        self.assertIn((str(nested), "nested-root"), refused)
        self.assertIn((str(linked), "symlink-root"), refused)
        self.assertIn(("7", "invalid-root"), refused)

    def test_plan_is_read_only_and_validates_tools_and_category(self) -> None:
        make_skill(self.scan_a, "read-only")
        before = sorted(str(path.relative_to(self.fx.tmp)) for path in self.fx.tmp.rglob("*"))
        plan = self.core.import_plan(["codex"], [str(self.scan_a)], "new-category")
        after = sorted(str(path.relative_to(self.fx.tmp)) for path in self.fx.tmp.rglob("*"))

        self.assertTrue(plan["ok"])
        self.assertEqual(before, after)
        self.assertFalse(self.core.log_path.exists())
        self.assertEqual(list(self.fx.tmp.rglob("*hermes-loadout-backup*")), [])
        for args in (([], [], "imported"), (["hermes"], [], "imported"), (["bogus"], [], "imported"), (["codex"], [], "../bad")):
            self.assertFalse(call(self.core.import_plan, *args)["ok"])

    def test_apply_uses_only_exact_selected_entries(self) -> None:
        make_skill(self.fx.codex, "from-tool")
        make_skill(self.scan_a, "from-folder")
        plan = self.core.import_plan(["codex"], [str(self.scan_a)], "wizard")
        chosen = [selected(row) for row in plan["adoptable"]]
        make_skill(self.scan_a, "arrived-later")

        applied = self.core.import_apply_plan(chosen, "wizard", plan_id=plan["plan_id"])

        self.assertEqual({row["name"] for row in applied["results"] if row["ok"]}, {"from-tool", "from-folder"})
        self.assertFalse((self.fx.home / "skills" / "wizard" / "arrived-later").exists())
        self.assertTrue((self.fx.codex / "from-tool").is_symlink())
        self.assertTrue((self.scan_a / "from-folder").is_dir())
        self.assertFalse((self.scan_a / "from-folder").is_symlink())

    def test_selected_root_matching_a_known_tool_dir_is_linked(self) -> None:
        make_skill(self.fx.grok, "selected-tool-root")
        plan = self.core.import_plan(["codex"], [str(self.fx.grok)], "wizard")
        entry = next(row for row in plan["adoptable"] if row["name"] == "selected-tool-root")
        self.assertEqual(entry["tool"], "grok")

        applied = self.core.import_apply_plan([selected(entry)], "wizard", plan_id=plan["plan_id"])

        self.assertEqual(applied["adopted"], 1)
        self.assertTrue((self.fx.grok / "selected-tool-root").is_symlink())

    def test_apply_refuses_changed_since_preview(self) -> None:
        make_skill(self.scan_a, "became-conflict")
        plan = self.core.import_plan(["codex"], [str(self.scan_a)], "imported")
        entry = next(row for row in plan["adoptable"] if row["name"] == "became-conflict")
        make_skill(self.fx.home / "skills" / "late", "became-conflict", "canonical arrived")

        applied = self.core.import_apply_plan([selected(entry)], "imported", plan_id=plan["plan_id"])

        result = applied["results"][0]
        self.assertFalse(result["ok"])
        self.assertTrue(result["changed_since_preview"])
        self.assertEqual(result["code"], "changed-since-preview")
        self.assertFalse((self.fx.home / "skills" / "imported" / "became-conflict").exists())

    def test_apply_reports_entry_replaced_by_foreign_link_as_changed(self) -> None:
        source = make_skill(self.scan_a, "replaced-after-plan")
        plan = self.core.import_plan(["codex"], [str(self.scan_a)], "imported")
        entry = next(row for row in plan["adoptable"] if row["name"] == "replaced-after-plan")
        shutil.rmtree(source)
        os.symlink(self.fx.foreign_target, source)

        result = self.core.import_apply_plan([selected(entry)], "imported", plan_id=plan["plan_id"])["results"][0]

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "foreign-link")
        self.assertTrue(result["changed_since_preview"])

    def test_protected_entries_are_never_overwritten(self) -> None:
        plan = self.core.import_plan(["grok"], [], "imported")
        protected = [row for row in plan["entries"] if row["kind"] in {"foreign-link", "unmanaged-dir", "broken-link"}]
        before = {
            row["name"]: (os.readlink(Path(row["path"])) if Path(row["path"]).is_symlink() else "directory")
            for row in protected
        }

        applied = self.core.import_apply_plan([selected(row) for row in protected], "imported", plan_id=plan["plan_id"])

        self.assertEqual(applied["receipt"]["refused"], 3)
        self.assertTrue(all(not row["ok"] for row in applied["results"]))
        after = {
            row["name"]: (os.readlink(Path(row["path"])) if Path(row["path"]).is_symlink() else "directory")
            for row in protected
        }
        self.assertEqual(after, before)

    def test_receipt_has_precise_tool_restore_and_plain_copy_undo(self) -> None:
        make_skill(self.fx.codex, "tool-adopt")
        make_skill(self.scan_a, "plain-adopt")
        plan = self.core.import_plan(["codex"], [str(self.scan_a)], "wizard")
        applied = self.core.import_apply_plan([selected(row) for row in plan["adoptable"]], "wizard", plan_id=plan["plan_id"])

        receipt = applied["receipt"]
        self.assertTrue(receipt["receipt_id"])
        self.assertEqual((receipt["adopted"], receipt["failed"], receipt["refused"]), (2, 0, 0))
        undo = {row["kind"]: row for row in receipt["undo"]}
        self.assertEqual(undo["restore-tool-entry"]["path"], str(self.fx.codex / "tool-adopt"))
        self.assertTrue(Path(undo["restore-tool-entry"]["backup"]).is_dir())
        self.assertEqual(
            undo["remove-canonical-copy"],
            {"path": str(self.fx.home / "skills" / "wizard" / "plain-adopt"), "backup": None, "kind": "remove-canonical-copy"},
        )

        tool_item = next(row for row in receipt["items"] if row["name"] == "tool-adopt")
        restored = self.core.revert_adopt("codex", "tool-adopt", tool_item["backup"], tool_item["skill"])
        self.assertTrue(restored["ok"])
        self.assertTrue((self.fx.codex / "tool-adopt").is_dir())
        self.assertFalse((self.fx.codex / "tool-adopt").is_symlink())

    def test_plain_scan_root_never_becomes_canonical_and_log_is_redacted(self) -> None:
        secret = "sk-secret-must-not-log"
        source = make_skill(self.scan_a, "plain-only")
        plan = self.core.import_plan(["codex"], [str(self.scan_a)], "imported")
        entry = next(row for row in plan["adoptable"] if row["name"] == "plain-only")
        request = selected(entry)
        request["api_key"] = secret

        applied = self.core.import_apply_plan([request], "imported", plan_id=plan["plan_id"])

        destination = self.fx.home / "skills" / "imported" / "plain-only"
        self.assertTrue(destination.is_dir())
        self.assertFalse(destination.is_symlink())
        self.assertTrue(source.is_dir())
        self.assertFalse(source.is_symlink())
        log_text = self.core.log_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, log_text)
        log_row = json.loads(log_text.splitlines()[-1])
        self.assertEqual(log_row["action"], "import-plan-apply")
        self.assertNotIn(str(self.scan_a), json.dumps(log_row))


if __name__ == "__main__":
    unittest.main()
