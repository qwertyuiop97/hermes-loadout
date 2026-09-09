"""Durable, target-bound undo, exercised only on disposable configurations."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import Fixture, pa


class ReceiptUndoTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.core = self.fx.core
        self.skill = "apple/apple-notes"
        self.link = self.fx.codex / "apple-notes"

    def apply(self, enabled=True, receipt_id=None):
        return self.core.execute_bulk([self.skill], "codex", enabled, receipt_id)["receipt"]

    def test_repaired_broken_link_restores_its_exact_relative_target_after_restart(self):
        target = os.path.relpath(self.fx.home / "skills" / "old" / "apple-notes", self.fx.codex)
        os.symlink(target, self.link)
        receipt = self.apply()
        restarted = pa.SkillsToggleCore(self.fx.home, self.core.tools)
        result = restarted.undo_bulk(receipt["receipt_id"])
        self.assertEqual(result["changed"], 1)
        self.assertEqual(os.readlink(self.link), target)
        self.assertFalse(self.link.exists())
        self.assertFalse(result["receipt"]["undo_available"])
        # Repeating a request does not reverse the restored pointer again.
        self.assertEqual(restarted.undo_bulk(receipt["receipt_id"]), result)

    def test_undo_preserves_external_changes_and_only_reverts_successful_items(self):
        other = self.fx.codex / "two words"
        ids = [self.skill, "productivity/two words", "productivity/airtable"]
        (self.fx.codex / "airtable").mkdir()
        receipt = self.core.execute_bulk(ids, "codex", True)["receipt"]
        self.link.unlink()
        outside = self.fx.tmp / "foreign"
        outside.mkdir()
        os.symlink(outside, self.link)
        result = self.core.undo_bulk(receipt["receipt_id"])
        self.assertEqual((result["changed"], result["failed"]), (1, 1))
        self.assertEqual(result["results"][0]["code"], "changed-since-apply")
        self.assertEqual(os.readlink(self.link), str(outside))
        self.assertFalse(other.exists())
        self.assertTrue((self.fx.codex / "airtable").is_dir())

    def test_receipt_is_bound_to_the_original_target_directory(self):
        receipt = self.apply()
        replacement = self.fx.tmp / "replacement-codex"
        replacement.mkdir()
        self.core.tools["codex"]["dir"] = str(replacement)
        with self.assertRaises(pa.SkillsToggleError) as error:
            self.core.undo_bulk(receipt["receipt_id"])
        self.assertEqual(error.exception.code, "target-changed")
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(list(replacement.iterdir()), [])

    def test_receipt_collision_and_invalid_ids_never_overwrite_evidence(self):
        receipt = self.apply(receipt_id="named-receipt")
        with self.assertRaises(pa.SkillsToggleError):
            self.apply(False, receipt_id="named-receipt")
        self.assertTrue(self.link.is_symlink())
        for bad in ("../other", "", None, ["x"]):
            with self.subTest(receipt_id=bad), self.assertRaises(pa.SkillsToggleError):
                self.core.undo_bulk(bad)
        self.assertEqual(self.core.get_bulk_receipt(receipt["receipt_id"])["receipt"]["changed"], 1)

    def test_unwritable_receipt_store_refuses_changes_before_mutation(self):
        with patch.object(self.core, "_reserve_receipt", side_effect=pa.SkillsToggleError("fixture", "receipt-write")):
            with self.assertRaises(pa.SkillsToggleError):
                self.apply()
        self.assertFalse(self.link.is_symlink())

    def test_failed_final_receipt_write_is_explicit_and_recovery_record_survives(self):
        with patch.object(self.core, "_save_receipt", side_effect=OSError("fixture denial")):
            result = self.core.execute_bulk([self.skill], "codex", True)
        self.assertTrue(self.link.is_symlink())
        self.assertFalse(result["receipt"]["undo_available"])
        self.assertEqual(result["receipt"]["status"], "persistence-failed")
        self.assertIn("receipt_error", result)
        saved = self.core.get_bulk_receipt(result["receipt"]["receipt_id"])["receipt"]
        self.assertEqual(saved["status"], "applying")
        with self.assertRaises(pa.SkillsToggleError) as error:
            self.core.undo_bulk(saved["receipt_id"])
        self.assertEqual(error.exception.code, "incomplete-receipt")

    def test_hermes_undo_preserves_unrelated_edits_and_disabled_members(self):
        receipt = self.core.execute_bulk([self.skill], "hermes", False)["receipt"]
        path = self.fx.home / "config.yaml"
        text = path.read_text(encoding="utf-8")
        text = pa.set_disabled_member(text, "another-skill", add=True)
        path.write_text(text + "\nnew_setting: keep-me\n", encoding="utf-8")
        result = self.core.undo_bulk(receipt["receipt_id"])
        self.assertEqual(result["changed"], 1)
        changed = path.read_text(encoding="utf-8")
        self.assertNotIn("apple-notes", pa.parse_disabled(changed))
        self.assertIn("another-skill", pa.parse_disabled(changed))
        self.assertIn("new_setting: keep-me", changed)

    def test_replacement_between_bulk_items_is_not_mistaken_for_our_write(self):
        original = self.core._link_tool
        replacement = self.fx.home / "skills" / "productivity" / "two words"
        def change_between(skill, tool, enabled):
            if skill["name"] == "two words":
                self.link.unlink()
                os.symlink(replacement, self.link)
            return original(skill, tool, enabled)
        with patch.object(self.core, "_link_tool", side_effect=change_between):
            receipt = self.core.execute_bulk([self.skill, "productivity/two words"], "codex", True)["receipt"]
        undone = self.core.undo_bulk(receipt["receipt_id"])
        self.assertEqual(undone["results"][0]["code"], "changed-since-apply")
        self.assertEqual(os.readlink(self.link), str(replacement))

    def test_invalid_later_receipt_item_refuses_the_whole_undo_before_writing(self):
        receipt = self.core.execute_bulk([self.skill, "productivity/two words"], "codex", True)["receipt"]
        receipt["items"][1]["before"] = {"kind": "symlink", "target": None}
        self.core._save_receipt(receipt)
        with self.assertRaises(pa.SkillsToggleError) as error:
            self.core.undo_bulk(receipt["receipt_id"])
        self.assertEqual(error.exception.code, "invalid-receipt")
        self.assertTrue(self.link.is_symlink())
        self.assertTrue((self.fx.codex / "two words").is_symlink())

    def test_receipt_directory_and_symlink_evidence_cannot_escape_home(self):
        self.apply(receipt_id="first")
        path = self.core._receipt_path("first")
        outside = self.fx.tmp / "outside.json"
        outside.write_text('{"keep":true}', encoding="utf-8")
        path.unlink()
        os.symlink(outside, path)
        with self.assertRaises(pa.SkillsToggleError):
            self.core.undo_bulk("first")
        self.assertEqual(json.loads(outside.read_text(encoding="utf-8")), {"keep": True})


if __name__ == "__main__":
    unittest.main()
