"""Round-trip tests for skills-toggle's backend core.

Everything runs against throwaway fixtures in a temp dir — the machine's real
~/.hermes and tool dirs are never touched.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import importlib.util

_REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("plugin_api", _REPO / "dashboard" / "plugin_api.py")
pa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pa)

SkillsToggleCore = pa.SkillsToggleCore
SkillsToggleError = pa.SkillsToggleError


LONG_DESCRIPTION = "A deliberately long description that exceeds the truncation window. " * 6

CONFIG_YAML = """# top comment — must survive edits
model:
  primary: sonnet

skills:
  creation_nudge_interval: 15
  disabled:
    - airtable  # trailing comment
    - "two words"

plugins:
  enabled: []
"""

SKILL_MD_TEMPLATE = """---
name: {name}
description: {desc}
---

# {name}

Body text for {name}.
"""


def _mk_skill(root: Path, category: str, name: str, desc: str = "does things") -> None:
    d = root / "skills" / category / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(SKILL_MD_TEMPLATE.format(name=name, desc=desc), encoding="utf-8")


class Fixture:
    """Fake hermes home + five consumer tool dirs covering every state."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="skills-toggle-test-"))
        self.home = self.tmp / "hermes"
        (self.home / "skills").mkdir(parents=True)
        (self.home / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")

        _mk_skill(self.home, "apple", "apple-notes", "control Apple Notes via JXA")
        _mk_skill(self.home, "apple", "rem índéluxé", "unicode skill name")  # unicode
        _mk_skill(self.home, "creative", "architecture-diagram", "draw diagrams")
        _mk_skill(self.home, "productivity", "airtable", "airtable automation")
        _mk_skill(self.home, "productivity", "two words", "name with a space")
        _mk_skill(self.home, "résearch", "arxiv", LONG_DESCRIPTION)  # unicode category + long desc
        _mk_skill(self.home, "media", "orphan-skill", "linked nowhere")  # for /diff unlinked

        # tool dirs
        self.claude = self.tmp / "claude-skills"
        self.codex = self.tmp / "codex-skills"
        self.grok = self.tmp / "grok-skills"
        self.opencode = self.tmp / "opencode" / "skills"  # ABSENT until toggled
        self.zcode = self.tmp / "zcode-skills"  # ABSENT
        for d in (self.claude, self.codex, self.grok):
            d.mkdir()

        # claude: one good link
        os.symlink(self.home / "skills" / "creative" / "architecture-diagram", self.claude / "architecture-diagram")
        # grok: real dir (unmanaged), foreign link (exists elsewhere), broken link (into tree, missing target)
        (self.grok / "airtable").mkdir()
        self.foreign_target = self.tmp / "elsewhere" / "airtable"
        self.foreign_target.mkdir(parents=True)
        os.symlink(self.foreign_target, self.grok / "apple-notes")
        os.symlink(self.home / "skills" / "apple" / "gone", self.grok / "rem índéluxé")

        self.tools = {
            "hermes": {"label": "Hermes", "special": "config"},
            "claude": {"label": "Claude", "dir": self.claude},
            "codex": {"label": "Codex", "dir": self.codex},
            "opencode": {"label": "OpenCode", "dir": self.opencode},
            "grok": {"label": "Grok", "dir": self.grok},
            "zcode": {"label": "ZCode", "dir": self.zcode},
        }
        self.core = SkillsToggleCore(self.home, self.tools, log_path=self.tmp / "data" / "mutations.log")

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def call(fn, *args, **kwargs):
    """Run a core method, converting SkillsToggleError into {ok:False} like the route layer."""
    try:
        return fn(*args, **kwargs)
    except SkillsToggleError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}


class FixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    # -- helpers ------------------------------------------------------------

    def skill_entry(self, state: dict, sid: str) -> dict:
        return next(s for s in state["skills"] if s["id"] == sid)

    # -- state --------------------------------------------------------------

    def test_state_shape_and_lean_payload(self) -> None:
        st = self.fx.core.state()
        self.assertTrue(st["ok"])
        self.assertTrue(st["skills_root_exists"])
        self.assertEqual(st["counts"]["skills"], 7)
        by_id = {s["id"]: s for s in st["skills"]}
        self.assertIn("résearch/arxiv", by_id)  # unicode category discovered dynamically
        self.assertIn("productivity/two words", by_id)
        # descriptions truncated
        self.assertLessEqual(len(by_id["résearch/arxiv"]["description"]), 160)
        self.assertTrue(by_id["résearch/arxiv"]["description"].endswith("…"))
        # tool presence flags
        present = {t["id"]: t["present"] for t in st["tools"]}
        self.assertEqual(
            present, {"hermes": True, "claude": True, "codex": True, "opencode": False, "grok": True, "zcode": False}
        )
        # hermes membership from config (bare names)
        self.assertEqual(by_id["productivity/airtable"]["tools"]["hermes"]["state"], "disabled")
        self.assertEqual(by_id["apple/apple-notes"]["tools"]["hermes"]["state"], "enabled")

    def test_state_link_statuses(self) -> None:
        st = self.fx.core.state()
        by_id = {s["id"]: s for s in st["skills"]}
        self.assertEqual(by_id["creative/architecture-diagram"]["tools"]["claude"]["state"], "enabled")
        self.assertEqual(by_id["productivity/airtable"]["tools"]["grok"]["state"], "unmanaged-dir")
        self.assertEqual(by_id["apple/apple-notes"]["tools"]["grok"]["state"], "foreign-link")
        self.assertEqual(by_id["apple/rem índéluxé"]["tools"]["grok"]["state"], "broken-link")
        self.assertEqual(by_id["apple/apple-notes"]["tools"]["claude"]["state"], "missing")

    # -- toggle round-trips --------------------------------------------------

    def test_toggle_link_unlink_roundtrip(self) -> None:
        core = self.fx.core
        r = core.toggle("apple/apple-notes", "claude", True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "linked")
        link = self.fx.claude / "apple-notes"
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(os.readlink(link)), (self.fx.home / "skills" / "apple" / "apple-notes").resolve())
        # idempotent
        self.assertEqual(core.toggle("apple/apple-notes", "claude", True)["action"], "noop")
        # unlink
        r = core.toggle("apple/apple-notes", "claude", False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "unlinked")
        self.assertFalse(link.exists() or link.is_symlink())
        # unlink again is a noop, never an error
        self.assertEqual(core.toggle("apple/apple-notes", "claude", False)["action"], "noop")

    def test_toggle_unicode_and_spaces(self) -> None:
        core = self.fx.core
        for sid, tool in (("apple/rem índéluxé", "codex"), ("productivity/two words", "codex"), ("résearch/arxiv", "claude")):
            r = core.toggle(sid, tool, True)
            self.assertTrue(r["ok"], r)
            self.assertEqual(core.state()["ok"], True)
            st = self.skill_entry(core.state(), sid)
            self.assertEqual(st["tools"][tool]["state"], "enabled")
            core.toggle(sid, tool, False)
            st = self.skill_entry(core.state(), sid)
            self.assertEqual(st["tools"][tool]["state"], "missing")

    def test_toggle_creates_absent_tool_dir(self) -> None:
        core = self.fx.core
        r = core.toggle("creative/architecture-diagram", "opencode", True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "created-dir+linked")
        self.assertTrue(self.fx.opencode.is_dir())
        self.assertTrue((self.fx.opencode / "architecture-diagram").is_symlink())
        present = {t["id"]: t["present"] for t in core.state()["tools"]}
        self.assertTrue(present["opencode"])
        # disable with dir present → link removed, dir kept
        core.toggle("creative/architecture-diagram", "opencode", False)
        self.assertTrue(self.fx.opencode.is_dir())
        self.assertFalse((self.fx.opencode / "architecture-diagram").exists())

    def test_toggle_hermes_edits_config_with_backup(self) -> None:
        core = self.fx.core
        r = core.toggle("apple/apple-notes", "hermes", False)  # disable
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "config-updated")
        text = (self.fx.home / "config.yaml").read_text()
        self.assertIn("- \"apple-notes\"", text)
        self.assertIn("# top comment — must survive edits", text)
        self.assertIn("creation_nudge_interval: 15", text)
        self.assertIn("- airtable", text)  # pre-existing member untouched
        # backup created
        backups = list(self.fx.home.glob("config.yaml.bak.skills-toggle.*"))
        self.assertEqual(len(backups), 1)
        self.assertNotIn("- \"apple-notes\"", backups[0].read_text())
        # idempotent
        self.assertEqual(core.toggle("apple/apple-notes", "hermes", False)["action"], "noop")
        # re-enable
        r = core.toggle("apple/apple-notes", "hermes", True)
        self.assertEqual(r["action"], "config-updated")
        self.assertNotIn("\"apple-notes\"", (self.fx.home / "config.yaml").read_text())
        # state reflects it
        st = self.skill_entry(core.state(), "apple/apple-notes")
        self.assertEqual(st["tools"]["hermes"]["state"], "enabled")

    def test_toggle_hermes_names_with_spaces(self) -> None:
        core = self.fx.core
        self.assertEqual(core.toggle("productivity/two words", "hermes", False)["action"], "noop")  # already disabled
        self.assertEqual(core.toggle("productivity/two words", "hermes", True)["action"], "config-updated")
        self.assertEqual(core.toggle("productivity/two words", "hermes", False)["action"], "config-updated")

    def test_toggle_refuses_unmanaged_dir_both_ways(self) -> None:
        core = self.fx.core
        r = call(core.toggle, "productivity/airtable", "grok", True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "unmanaged-dir")
        r = call(core.toggle, "productivity/airtable", "grok", False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "unmanaged-dir")
        self.assertTrue((self.fx.grok / "airtable").is_dir())  # untouched

    def test_toggle_refuses_foreign_link(self) -> None:
        core = self.fx.core
        r = call(core.toggle, "apple/apple-notes", "grok", True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "foreign-link")
        r = call(core.toggle, "apple/apple-notes", "grok", False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "foreign-link")
        self.assertEqual(Path(os.readlink(self.fx.grok / "apple-notes")), self.fx.foreign_target)

    def test_toggle_broken_link_repair_on_enable_and_clean_removal(self) -> None:
        core = self.fx.core
        # enabling replaces the broken link with the correct target
        r = core.toggle("apple/rem índéluxé", "grok", True)
        self.assertEqual(r["action"], "repaired-link")
        link = self.fx.grok / "rem índéluxé"
        self.assertEqual(Path(os.readlink(link)), (self.fx.home / "skills" / "apple" / "rem índéluxé").resolve())
        # make it broken again, then disable → removed (target inside tree)
        link.unlink()
        os.symlink(self.fx.home / "skills" / "apple" / "vanished", link)
        r = core.toggle("apple/rem índéluxé", "grok", False)
        self.assertEqual(r["action"], "unlinked")
        # source dir never touched
        self.assertTrue((self.fx.home / "skills" / "apple" / "rem índéluxé").is_dir())

    # -- traversal / validation ----------------------------------------------

    def test_traversal_and_unknown_rejected(self) -> None:
        core = self.fx.core
        bad_skills = [
            "../../etc/passwd",
            "../..",
            "/etc/passwd",
            "apple/../creative/architecture-diagram",
            "apple",  # missing category half
            "apple/../../..",
            "notacategory/notaskill",
            "apple/sub/dir/notes",  # too many segments
            42,
            None,
            "",
        ]
        for bad in bad_skills:
            r = call(core.toggle, bad, "claude", True)
            self.assertFalse(r["ok"], f"expected rejection for {bad!r}")
            self.assertIn(r["code"], ("invalid-skill", "unknown-skill"))
        for bad_tool in ("../..", "bogus", "", None, 7):
            r = call(core.toggle, "apple/apple-notes", bad_tool, True)
            self.assertFalse(r["ok"], f"expected rejection for tool {bad_tool!r}")
            self.assertEqual(r["code"], "unknown-tool")
        r = call(core.toggle, "apple/apple-notes", "claude", "yes")
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "invalid-body")
        # nothing was created anywhere
        self.assertEqual(sorted(p.name for p in self.fx.claude.iterdir()), ["architecture-diagram"])

    def test_detail_full_text(self) -> None:
        core = self.fx.core
        d = core.detail("apple/apple-notes")
        self.assertTrue(d["ok"])
        self.assertIn("control Apple Notes via JXA", d["description"])
        self.assertIn("# apple-notes", d["markdown"])
        self.assertIn("Body text", d["markdown"])
        r = call(core.detail, "../../etc/passwd")
        self.assertFalse(r["ok"])

    # -- diff / repair --------------------------------------------------------

    def test_diff(self) -> None:
        core = self.fx.core
        d = core.diff()
        self.assertTrue(d["ok"])
        self.assertIn("media/orphan-skill", d["unlinked"])
        # apple-notes has no consumer symlink anywhere (grok's is foreign) → unlinked
        self.assertIn("apple/apple-notes", d["unlinked"])
        # creative/architecture-diagram is linked into claude → not unlinked
        self.assertNotIn("creative/architecture-diagram", d["unlinked"])
        broken = {(b["tool"], b["name"]) for b in d["broken"]}
        self.assertIn(("grok", "rem índéluxé"), broken)
        self.assertEqual({(u["tool"], u["name"]) for u in d["unmanaged"]}, {("grok", "airtable")})
        self.assertEqual({(f["tool"], f["name"]) for f in d["foreign"]}, {("grok", "apple-notes")})

    def test_repair_and_repair_all(self) -> None:
        core = self.fx.core
        r = core.repair("apple/rem índéluxé", "grok")
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "enabled")
        # repair on healthy link is a noop
        self.assertEqual(core.repair("apple/rem índéluxé", "grok")["action"], "noop")
        # unmanaged repair refused
        r = call(core.repair, "productivity/airtable", "grok")
        self.assertFalse(r["ok"])
        # hermes repair not applicable
        r = call(core.repair, "apple/apple-notes", "hermes")
        self.assertFalse(r["ok"])
        # repair-all: create two broken links, one inside tree, one outside
        os.symlink(self.fx.home / "skills" / "media" / "gone", self.fx.codex / "orphan-skill")
        os.symlink(self.fx.tmp / "nowhere" / "at-all", self.fx.codex / "stray")
        res = core.repair_all()
        fixed = {(f["tool"], f["skill"]) for f in res["fixed"]}
        self.assertIn(("codex", "media/orphan-skill"), fixed)
        # the inside-tree broken link got re-pointed to the real skill
        link = self.fx.codex / "orphan-skill"
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(os.readlink(link)), (self.fx.home / "skills" / "media" / "orphan-skill").resolve())
        # the outside-tree one is reported unfixable and left alone
        reasons = {(u["name"], u["reason"]) for u in res["unfixable"]}
        self.assertTrue(any(n == "stray" for n, _ in reasons), reasons)
        self.assertTrue((self.fx.codex / "stray").is_symlink())

    # -- bulk / ensure-dir -----------------------------------------------------

    def test_toggle_bulk_mixed_results(self) -> None:
        core = self.fx.core
        res = core.toggle_bulk(["apple/apple-notes", "productivity/two words", "bogus/x"], "codex", True)
        self.assertTrue(res["ok"])
        by_skill = {r["skill"]: r for r in res["results"]}
        self.assertTrue(by_skill["apple/apple-notes"]["ok"])
        self.assertTrue(by_skill["productivity/two words"]["ok"])
        self.assertFalse(by_skill["bogus/x"]["ok"])
        r = call(core.toggle_bulk, [], "codex", True)
        self.assertFalse(r["ok"])

    def test_ensure_tool_dir(self) -> None:
        core = self.fx.core
        r = core.ensure_tool_dir("zcode")
        self.assertTrue(r["ok"] and r["created"])
        self.assertTrue(self.fx.zcode.is_dir())
        self.assertFalse(core.ensure_tool_dir("zcode")["created"])
        r = call(core.ensure_tool_dir, "hermes")
        self.assertFalse(r["ok"])

    # -- cache ------------------------------------------------------------------

    def test_cache_invalidation_on_mutation(self) -> None:
        core = self.fx.core
        st1 = core.state()
        self.assertEqual(self.skill_entry(st1, "apple/apple-notes")["tools"]["codex"]["state"], "missing")
        core.toggle("apple/apple-notes", "codex", True)
        st2 = core.state()  # must not serve the stale cache
        self.assertEqual(self.skill_entry(st2, "apple/apple-notes")["tools"]["codex"]["state"], "enabled")

    def test_cache_hits_same_payload_object(self) -> None:
        core = self.fx.core
        st1 = core.state()
        st2 = core.state()
        self.assertIs(st1, st2)

    # -- logging ------------------------------------------------------------------

    def test_mutations_logged(self) -> None:
        core = self.fx.core
        core.toggle("apple/apple-notes", "claude", True)
        core.toggle("apple/apple-notes", "hermes", False)
        log_path = self.fx.tmp / "data" / "mutations.log"
        self.assertTrue(log_path.is_file())
        lines = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
        kinds = {(l.get("action"), l.get("tool")) for l in lines}
        self.assertIn(("toggle", "claude"), kinds)
        self.assertIn(("config-edit", "hermes"), kinds)
        for l in lines:
            self.assertIn("ts", l)

    # -- concurrency -----------------------------------------------------------------

    def test_concurrent_toggles_and_reads(self) -> None:
        core = self.fx.core
        sids = [s["id"] for s in core.state()["skills"]]
        errors: list[Exception] = []

        def worker(sid: str) -> None:
            try:
                for _ in range(6):
                    core.toggle(sid, "codex", True)
                    core.state()
                    core.toggle(sid, "codex", False)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(sid,)) for sid in sids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        final = core.state()
        for sid in sids:
            self.assertEqual(self.skill_entry(final, sid)["tools"]["codex"]["state"], "missing")

    def test_relative_symlink_target_recognized(self) -> None:
        core = self.fx.core
        # craft a RELATIVE link into the skills tree — must resolve identically
        rel = os.path.relpath(self.fx.home / "skills" / "apple" / "apple-notes", self.fx.codex)
        os.symlink(rel, self.fx.codex / "apple-notes")
        st = self.skill_entry(core.state(), "apple/apple-notes")
        self.assertEqual(st["tools"]["codex"]["state"], "enabled")
        r = core.toggle("apple/apple-notes", "codex", False)
        self.assertEqual(r["action"], "unlinked")

    def test_tool_dir_that_is_a_file(self) -> None:
        core = self.fx.core
        bogus = self.fx.tmp / "bogus-skills"
        bogus.write_text("i am a file", encoding="utf-8")
        core.tools["bogus"] = {"label": "Bogus", "dir": bogus}
        core.invalidate()
        r = call(core.toggle, "apple/apple-notes", "bogus", True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "not-a-dir")
        r = call(core.ensure_tool_dir, "bogus")
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "not-a-dir")
        self.assertTrue(bogus.is_file())  # untouched

class ConfigEditorTests(unittest.TestCase):
    """The surgical skills.disabled editor across config shapes."""

    def _roundtrip(self, text: str, add_name: str, remove_name: str | None = None) -> None:
        added = pa.set_disabled_member(text, add_name, add=True)
        self.assertIn(add_name, pa.parse_disabled(added))
        removed = pa.set_disabled_member(added, remove_name if remove_name else add_name, add=False)
        self.assertNotIn(add_name, pa.parse_disabled(removed))

    def test_empty_file(self) -> None:
        self._roundtrip("", "apple-notes")

    def test_missing_skills_key(self) -> None:
        text = "model: sonnet\nprovider: anthropic\n"
        out = pa.set_disabled_member(text, "x-skill", add=True)
        self.assertIn("x-skill", pa.parse_disabled(out))
        self.assertIn("provider: anthropic", out)

    def test_skills_without_disabled(self) -> None:
        text = "skills:\n  creation_nudge_interval: 15\n"
        out = pa.set_disabled_member(text, "airtable", add=True)
        self.assertIn("airtable", pa.parse_disabled(out))
        self.assertIn("creation_nudge_interval: 15", out)

    def test_inline_empty_list(self) -> None:
        self._roundtrip("skills:\n  disabled: []\n", "apple-notes")

    def test_inline_populated_list(self) -> None:
        text = 'skills:\n  disabled: [airtable, "two words"]\n'
        out = pa.set_disabled_member(text, "arxiv", add=True)
        self.assertEqual(pa.parse_disabled(out), {"airtable", "two words", "arxiv"})
        out2 = pa.set_disabled_member(out, "two words", add=False)
        self.assertEqual(pa.parse_disabled(out2), {"airtable", "arxiv"})

    def test_block_list_add_remove(self) -> None:
        text = "# c\nskills:\n  disabled:\n    - airtable\n    - arxiv\nother: 1\n"
        out = pa.set_disabled_member(text, "banana", add=True)
        self.assertEqual(pa.parse_disabled(out), {"airtable", "arxiv", "banana"})
        self.assertIn("other: 1", out)
        self.assertIn("# c", out)
        out2 = pa.set_disabled_member(out, "airtable", add=False)
        self.assertEqual(pa.parse_disabled(out2), {"arxiv", "banana"})

    def test_block_list_remove_last_member(self) -> None:
        text = "skills:\n  disabled:\n    - airtable\n"
        out = pa.set_disabled_member(text, "airtable", add=False)
        self.assertEqual(pa.parse_disabled(out), set())
        self.assertIn("disabled: []", out)

    def test_scalar_shorthand(self) -> None:
        text = "skills:\n  disabled: airtable\n"
        out = pa.set_disabled_member(text, "arxiv", add=True)
        self.assertEqual(pa.parse_disabled(out), {"airtable", "arxiv"})

    def test_disabled_null(self) -> None:
        text = "skills:\n  disabled: null\n"
        out = pa.set_disabled_member(text, "arxiv", add=True)
        self.assertEqual(pa.parse_disabled(out), {"arxiv"})

    def test_noop_cases(self) -> None:
        text = "skills:\n  disabled:\n    - airtable\n"
        self.assertEqual(pa.set_disabled_member(text, "airtable", add=True), text)  # already present
        self.assertEqual(pa.set_disabled_member(text, "arxiv", add=False), text)  # already absent

    def test_flow_map_parse_only(self) -> None:
        self.assertEqual(pa.parse_disabled("skills: {disabled: [a, b], other: 1}\n"), {"a", "b"})
        self.assertEqual(pa.parse_disabled("skills: [a, b]\n"), set())
        self.assertEqual(pa.parse_disabled("nonsense: true\n"), set())

    def test_comments_between_items_preserved_semantics(self) -> None:
        text = "skills:\n  disabled:\n    - airtable\n    # keep me\n    - arxiv\n"
        out = pa.set_disabled_member(text, "banana", add=True)
        self.assertEqual(pa.parse_disabled(out), {"airtable", "arxiv", "banana"})

    def test_selfcheck_raises_on_garbage(self) -> None:
        # a disabled value we cannot interpret must refuse, not corrupt
        with self.assertRaises(pa.ConfigEditError):
            pa.set_disabled_member("skills:\n  disabled: [unbalanced\n", "x", add=True)

    def test_crlf_preserved(self) -> None:
        text = "model: sonnet\r\nskills:\r\n  disabled:\r\n    - airtable\r\n"
        out = pa.set_disabled_member(text, "arxiv", add=True)
        self.assertIn("arxiv", pa.parse_disabled(out.replace("\r\n", "\n")))
        self.assertIn("\r\n", out)
        self.assertNotIn("\n", out.replace("\r\n", ""))  # no bare LF introduced
        out2 = pa.set_disabled_member(out, "airtable", add=False)
        self.assertNotIn("airtable", pa.parse_disabled(out2.replace("\r\n", "\n")))
        self.assertIn("\r\n", out2)


class FrontmatterTests(unittest.TestCase):
    def test_basic(self) -> None:
        name, desc = pa.parse_skill_markdown("---\nname: airtable\ndescription: bases and records\n---\nbody")
        self.assertEqual((name, desc), ("airtable", "bases and records"))

    def test_folded_block_scalar(self) -> None:
        text = "---\nname: x\ndescription: >-\n  one two\n  three\nother: 1\n---\n"
        self.assertEqual(pa.parse_skill_markdown(text)[1], "one two three")

    def test_literal_block_scalar(self) -> None:
        text = "---\nname: x\ndescription: |\n  line1\n  line2\n---\n"
        self.assertEqual(pa.parse_skill_markdown(text)[1], "line1\nline2")

    def test_quoted_and_unicode(self) -> None:
        text = '---\nname: "café"\ndescription: "hello # world"\n---\n'
        name, desc = pa.parse_skill_markdown(text)
        self.assertEqual(name, "café")
        self.assertEqual(desc, "hello # world")

    def test_no_frontmatter(self) -> None:
        self.assertEqual(pa.parse_skill_markdown("# just body\n"), ("", ""))


class PathAndConfigTests(unittest.TestCase):
    def test_expand_path(self) -> None:
        os.environ["SKT_TEST_VAR"] = "/from-env"
        try:
            self.assertEqual(pa.expand_path("${SKT_TEST_VAR}/skills"), Path("/from-env/skills"))
            self.assertEqual(pa.expand_path("${SKT_MISSING_VAR:-/opt/x}/s"), Path("/opt/x/s"))
            self.assertEqual(pa.expand_path("~/skills"), Path.home() / "skills")
        finally:
            del os.environ["SKT_TEST_VAR"]

    def test_user_tool_overrides_and_custom_tool(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "skills-toggle.json").write_text(
                json.dumps(
                    {
                        "tools": {
                            "claude": "/custom/claude",
                            "ghost": {"label": "Ghost", "dir": "~/ghost-skills"},
                            "hermes": {"label": "Hermes (renamed)"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            tools = pa.load_tools_config(home)
            self.assertEqual(pa.expand_path(tools["claude"]["dir"]), Path("/custom/claude"))
            self.assertEqual(tools["ghost"]["label"], "Ghost")
            self.assertEqual(tools["hermes"].get("special"), "config")  # hermes stays config-backed
            self.assertEqual(tools["hermes"]["label"], "Hermes (renamed)")

    def test_user_config_garbage_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "skills-toggle.json").write_text("{not json", encoding="utf-8")
            tools = pa.load_tools_config(home)
            self.assertIn("claude", tools)

    def test_hermes_home_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prof = Path(td) / ".hermes" / "profiles" / "lab"
            prof.mkdir(parents=True)
            env = dict(os.environ)
            env.pop("HERMES_HOME", None)
            env["HERMES_PROFILE"] = "lab"
            old_home = env.get("HOME")
            env["HOME"] = td
            try:
                os.environ.clear()
                os.environ.update(env)
                self.assertEqual(pa.hermes_home(), prof)
                os.environ["HERMES_HOME"] = "/explicit/home"
                self.assertEqual(pa.hermes_home(), Path("/explicit/home"))
            finally:
                os.environ.clear()
                os.environ.update({k: v for k, v in env.items()})
                os.environ["HOME"] = old_home or os.environ.get("HOME", "")

    def test_missing_skills_root_reports_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            core = SkillsToggleCore(Path(td) / "nothere", {"hermes": {"label": "Hermes", "special": "config"}})
            st = core.state()
            self.assertTrue(st["ok"])
            self.assertEqual(st["skills"], [])
            self.assertFalse(st["skills_root_exists"])
            r = call(core.toggle, "a/b", "hermes", True)
            self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
