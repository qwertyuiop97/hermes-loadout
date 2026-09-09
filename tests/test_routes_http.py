"""HTTP round-trip of every mounted route against /tmp fixtures.

Uses a real FastAPI app + TestClient so the receipts cover the actual mount
surface (/api/plugins/skills-toggle/... semantics are the gateway's; here we
exercise the router itself, which is what the gateway mounts).

Run:  ./.venv/bin/python -m unittest tests.test_routes_http -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from test_plugin_api import Fixture, call, pa  # noqa: E402

fastapi = None
try:
    from fastapi import FastAPI  # type: ignore
    from fastapi.testclient import TestClient  # type: ignore

    fastapi = FastAPI
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed (run inside .venv)")
class RouteRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        pa.set_core_for_testing(self.fx.core)
        app = fastapi()
        app.include_router(pa.router, prefix="/api/plugins/skills-toggle")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        pa.set_core_for_testing(None)
        self.fx.cleanup()

    def test_health(self) -> None:
        r = self.client.get("/api/plugins/skills-toggle/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["plugin"], "skills-toggle")
        self.assertEqual(body["hermes_home"], str(self.fx.home))

    def test_state(self) -> None:
        r = self.client.get("/api/plugins/skills-toggle/state")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["counts"]["skills"], 7)
        self.assertEqual(len(body["skills"]), 7)

    def test_detail_ok_and_error(self) -> None:
        r = self.client.get("/api/plugins/skills-toggle/detail", params={"skill": "apple/apple-notes"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("# apple-notes", r.json()["markdown"])
        r = self.client.get("/api/plugins/skills-toggle/detail", params={"skill": "../../etc/passwd"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "invalid-skill")

    def test_diff(self) -> None:
        r = self.client.get("/api/plugins/skills-toggle/diff")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIn("media/orphan-skill", body["unlinked"])
        self.assertEqual(body["counts"]["broken"], 1)

    def test_toggle_roundtrip(self) -> None:
        base = "/api/plugins/skills-toggle/toggle"
        r = self.client.post(base, json={"skill": "apple/apple-notes", "tool": "claude", "enabled": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["state"], "enabled")
        r = self.client.post(base, json={"skill": "apple/apple-notes", "tool": "claude", "enabled": True})
        self.assertEqual(r.json()["action"], "noop")
        r = self.client.post(base, json={"skill": "apple/apple-notes", "tool": "claude", "enabled": False})
        self.assertEqual(r.json()["state"], "missing")
        # error envelope
        r = self.client.post(base, json={"skill": "nope/nope", "tool": "claude", "enabled": True})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "unknown-skill")
        # hermes toggle
        r = self.client.post(base, json={"skill": "apple/apple-notes", "tool": "hermes", "enabled": False})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["action"], "config-updated")

    def test_toggle_bulk(self) -> None:
        r = self.client.post(
            "/api/plugins/skills-toggle/toggle-bulk",
            json={"skills": ["apple/apple-notes", "résearch/arxiv"], "tool": "codex", "enabled": True},
        )
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["changed"], 2)
        self.assertEqual(body["failed"], 0)

    def test_bulk_plan_and_apply_roundtrip(self) -> None:
        base = "/api/plugins/skills-toggle/bulk"
        skills = ["apple/apple-notes", "résearch/arxiv"]
        planned = self.client.post(
            f"{base}/plan", json={"skills": skills, "tool": "codex", "enabled": True}
        )
        self.assertEqual(planned.status_code, 200)
        plan = planned.json()
        self.assertEqual(plan["would_change"], skills)
        applied = self.client.post(
            f"{base}/apply",
            json={
                "receipt_id": "http-roundtrip",
                "skills": plan["would_change"],
                "tool": "codex",
                "enabled": True,
            },
        )
        self.assertEqual(applied.status_code, 200)
        body = applied.json()
        self.assertEqual(body["receipt"]["receipt_id"], "http-roundtrip")
        self.assertEqual(body["receipt"]["changed"], 2)
        self.assertEqual([item["skill"] for item in body["results"]], skills)

    def test_repair_and_repair_all(self) -> None:
        r = self.client.post(
            "/api/plugins/skills-toggle/repair", json={"skill": "apple/rem índéluxé", "tool": "grok"}
        )
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["state"], "enabled")
        os.symlink(self.fx.home / "skills" / "media" / "gone", self.fx.codex / "orphan-skill")
        r = self.client.post("/api/plugins/skills-toggle/repair-all")
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["fixed"][0]["skill"], "media/orphan-skill")

    def test_ensure_tool_dir(self) -> None:
        r = self.client.post("/api/plugins/skills-toggle/ensure-tool-dir", json={"tool": "zcode"})
        self.assertTrue(r.json()["ok"])
        self.assertTrue(r.json()["created"])
        r = self.client.post("/api/plugins/skills-toggle/ensure-tool-dir", json={"tool": "hermes"})
        self.assertFalse(r.json()["ok"])

    def test_import_scan_apply_and_drift_and_config(self) -> None:
        base = "/api/plugins/skills-toggle"
        # create an adoptable copy in codex
        local = self.fx.codex / "http-local-skill"
        local.mkdir()
        (local / "SKILL.md").write_text(
            "---\nname: http-local-skill\ndescription: via http\n---\nbody", encoding="utf-8"
        )
        r = self.client.get(f"{base}/import/scan")
        body = r.json()
        self.assertTrue(body["ok"])
        codex = next(t for t in body["tools"] if t["tool"] == "codex")
        self.assertIn("http-local-skill", [e["name"] for e in codex["entries"]])
        r = self.client.post(f"{base}/import/apply", json={"tool": "codex", "names": ["http-local-skill"]})
        self.assertEqual(r.json()["adopted"], 1)
        self.assertTrue((self.fx.home / "skills" / "imported" / "http-local-skill" / "SKILL.md").is_file())
        # drift route answers
        r = self.client.get(f"{base}/drift")
        self.assertTrue(r.json()["ok"])
        # config tools route: valid + invalid
        r = self.client.post(f"{base}/config/tools", json={"id": "cursor", "label": "Cursor", "dir": "~/cursor-skills"})
        self.assertTrue(r.json()["ok"])
        r = self.client.post(f"{base}/config/tools", json={"id": "hermes", "label": "X", "dir": "~/y"})
        self.assertFalse(r.json()["ok"])

    def test_mcp_routes(self) -> None:
        base = "/api/plugins/skills-toggle"
        # hermes config has no mcp_servers in the fixture -> empty catalog
        r = self.client.get(f"{base}/mcp/state")
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["counts"]["catalog"], 0)
        r = self.client.post(f"{base}/mcp/toggle", json={"name": "ghost", "enabled": True})
        self.assertFalse(r.json()["ok"])
        self.assertEqual(r.json()["code"], "unknown-server")

    def test_mutations_logged_over_http(self) -> None:
        self.client.post("/api/plugins/skills-toggle/toggle", json={"skill": "apple/apple-notes", "tool": "codex", "enabled": True})
        log = self.fx.tmp / "data" / "mutations.log"
        self.assertTrue(log.is_file())
        self.assertIn('"action": "toggle"', log.read_text())


if __name__ == "__main__":
    unittest.main()
