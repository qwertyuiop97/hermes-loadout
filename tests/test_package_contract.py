"""Keep public package metadata and documented paths aligned with runtime data."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_plugin_api import pa

ROOT = Path(__file__).resolve().parents[1]


class PackageContractTests(unittest.TestCase):
    def test_package_identity_and_version_agree(self):
        agent = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        desktop = (ROOT / "desktop/plugin.js").read_text(encoding="utf-8")
        dashboard = json.loads((ROOT / "dashboard/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(re.search(r"^name: (.+)$", agent, re.M).group(1), pa.PLUGIN_ID)
        self.assertEqual(re.search(r"^version: (.+)$", agent, re.M).group(1), pa.PLUGIN_VERSION)
        self.assertEqual(dashboard["name"], pa.PLUGIN_ID)
        self.assertTrue((ROOT / "dashboard" / dashboard["api"]).is_file())
        self.assertIn("const ID = '" + pa.PLUGIN_ID + "'", desktop)
        self.assertIn("name: 'Loadout for Hermes'", desktop)
        self.assertTrue((ROOT / "README.md").read_text(encoding="utf-8").startswith("# Loadout for Hermes\n"))

    def test_readme_lists_exact_documented_skill_candidates(self):
        def paths(value):
            return "<br>".join("`" + path + "`" for path in value) if value else "Not offered"

        rows = [row for row in pa.CLIENT_CATALOG
                if row["verification"] == "documented" and row["skills"]
                and (row["global"] or row["project"])]
        expected = "| Client | Global path | Project path |\n|---|---|---|\n" + "\n".join(
            f"| [{row['label']}]({row['sources'][0]}) | {paths(row['global'])} | {paths(row['project'])} |"
            for row in rows)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start, end = "<!-- client-catalog:start -->", "<!-- client-catalog:end -->"
        self.assertEqual(readme.count(start), 1)
        self.assertEqual(readme.count(end), 1)
        actual = readme.split(start, 1)[1].split(end, 1)[0].strip()
        self.assertEqual(actual, expected, "Update the README catalog table with the evidenced catalog change.")


if __name__ == "__main__":
    unittest.main()
