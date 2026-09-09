"""Safety and receipt tests for backend-authored explicit-id bulk operations."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from test_plugin_api import Fixture, call, pa, _mk_skill  # noqa: E402


class BulkPlanningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.core = self.fx.core

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_plan_all_on_all_off_and_noop(self) -> None:
        ids = ["apple/apple-notes", "productivity/two words"]
        on = self.core.plan_bulk(ids, "codex", True)
        self.assertEqual(on["would_change"], ids)
        self.assertEqual(on["totals"], {"would_change": 2, "already_satisfied": 0, "refused": 0})
        self.assertEqual([row["skill_id"] for row in on["sample"]], ids)
        self.assertEqual(on["sample"][0]["current_state"], "missing")
        self.assertEqual(on["sample"][0]["next_state"], "enabled")
        self.assertFalse(self.core.log_path.exists(), "planning must not write a mutation log")

        self.core.execute_bulk(on["would_change"], "codex", True)
        off = self.core.plan_bulk(ids, "codex", False)
        self.assertEqual(off["would_change"], ids)
        self.assertEqual(off["already_satisfied"], [])
        self.core.execute_bulk(off["would_change"], "codex", False)
        noop = self.core.plan_bulk(ids, "codex", False)
        self.assertEqual(noop["would_change"], [])
        self.assertEqual(noop["already_satisfied"], ids)
        empty_apply = self.core.execute_bulk(noop["would_change"], "codex", False)
        self.assertEqual(empty_apply["receipt"]["changed"], 0)

        legacy = self.core.toggle_bulk([ids[0], ids[0]], "codex", False)
        self.assertEqual(len(legacy["results"]), 2)
        self.assertEqual(legacy["changed"], 2)  # legacy endpoint counts successful no-ops

    def test_plan_mixed_refused_deduped_and_validation(self) -> None:
        mixed = self.core.plan_bulk(
            ["creative/architecture-diagram", "apple/apple-notes", "apple/apple-notes"],
            "claude",
            True,
        )
        self.assertEqual(
            mixed["ordered_explicit_ids"],
            ["creative/architecture-diagram", "apple/apple-notes"],
        )
        self.assertEqual(mixed["already_satisfied"], ["creative/architecture-diagram"])
        self.assertEqual(mixed["would_change"], ["apple/apple-notes"])

        protected = self.core.plan_bulk(
            ["apple/apple-notes", "productivity/airtable", "bogus/missing"], "grok", True
        )
        self.assertEqual(protected["would_change"], [])
        self.assertEqual(
            [(row["skill"], row["code"]) for row in protected["refused"]],
            [
                ("apple/apple-notes", "foreign-link"),
                ("productivity/airtable", "unmanaged-dir"),
                ("bogus/missing", "unknown-skill"),
            ],
        )
        for args in (([], "codex", True), (["apple/apple-notes"], "bad", True), (["apple/apple-notes"], "codex", "yes")):
            self.assertFalse(call(self.core.plan_bulk, *args)["ok"])

    def test_plan_execute_uses_exact_ids_not_a_rescanned_scope(self) -> None:
        ids = ["apple/apple-notes", "productivity/two words"]
        plan = self.core.plan_bulk(ids, "codex", True)
        _mk_skill(self.fx.home, "new", "arrived-after-preview")
        result = self.core.execute_bulk(plan["would_change"], "codex", True, "exact-plan")
        self.assertEqual([row["skill"] for row in result["results"]], ids)
        self.assertFalse((self.fx.codex / "arrived-after-preview").exists())
        self.assertEqual(result["receipt"]["receipt_id"], "exact-plan")

    def test_partial_failure_receipt_and_precise_undo(self) -> None:
        ids = ["apple/apple-notes", "productivity/two words"]
        plan = self.core.plan_bulk(ids, "codex", True)
        (self.fx.codex / "two words").mkdir()
        applied = self.core.execute_bulk(plan["would_change"], "codex", True)
        by_skill = {row["skill"]: row for row in applied["results"]}
        self.assertTrue(by_skill["apple/apple-notes"]["ok"])
        self.assertFalse(by_skill["productivity/two words"]["ok"])
        self.assertTrue(by_skill["productivity/two words"]["changed_since_preview"])
        self.assertEqual(by_skill["productivity/two words"]["code"], "unmanaged-dir")
        receipt = applied["receipt"]
        self.assertEqual((receipt["changed"], receipt["failed"], receipt["refused"]), (1, 1, 1))
        self.assertEqual(receipt["undone_by"], [{"skill": "apple/apple-notes", "enabled": False}])

        undo_ids = [row["skill"] for row in receipt["undone_by"]]
        undo = self.core.execute_bulk(undo_ids, "codex", False)
        self.assertEqual(undo["receipt"]["changed"], 1)
        self.assertFalse((self.fx.codex / "apple-notes").exists())
        self.assertTrue((self.fx.codex / "two words").is_dir())

    def test_concurrent_changes_are_reported_and_protected(self) -> None:
        ids = ["apple/apple-notes", "productivity/two words"]
        plan = self.core.plan_bulk(ids, "codex", True)
        self.core.toggle("apple/apple-notes", "codex", True)
        foreign_target = self.fx.tmp / "foreign-two-words"
        foreign_target.mkdir()
        os.symlink(foreign_target, self.fx.codex / "two words")

        result = self.core.execute_bulk(plan["would_change"], "codex", True)
        by_skill = {row["skill"]: row for row in result["results"]}
        self.assertEqual(by_skill["apple/apple-notes"]["code"], "changed-since-preview")
        self.assertTrue(by_skill["apple/apple-notes"]["changed_since_preview"])
        self.assertEqual(by_skill["productivity/two words"]["code"], "foreign-link")
        self.assertTrue(by_skill["productivity/two words"]["changed_since_preview"])
        self.assertTrue(pa.same_path(Path(os.readlink(self.fx.codex / "two words")), foreign_target))
        self.assertEqual(result["receipt"]["changed"], 0)

    def test_hermes_bulk_uses_one_backup_and_one_write(self) -> None:
        ids = ["apple/apple-notes", "creative/architecture-diagram"]
        result = self.core.execute_bulk(self.core.plan_bulk(ids, "hermes", False)["would_change"], "hermes", False)
        self.assertEqual(result["receipt"]["changed"], 2)
        self.assertEqual(len(list(self.fx.home.glob("config.yaml.bak.skills-toggle.*"))), 1)
        text = (self.fx.home / "config.yaml").read_text(encoding="utf-8")
        self.assertIn('"apple-notes"', text)
        self.assertIn('"architecture-diagram"', text)

    def test_mutation_log_redacts_nested_secret_values(self) -> None:
        secret = "sk-live-do-not-log"
        self.core._log(
            action="bulk",
            receipt_id="redaction-test",
            env={"SERVICE_TOKEN": secret},
            receipt={"api_key": secret, "nested": {"password": secret}},
        )
        rows = [json.loads(line) for line in self.core.log_path.read_text(encoding="utf-8").splitlines()]
        serialized = json.dumps(rows)
        self.assertNotIn(secret, serialized)
        self.assertEqual(rows[-1]["env"], ["SERVICE_TOKEN"])
        self.assertEqual(rows[-1]["receipt"]["api_key"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
