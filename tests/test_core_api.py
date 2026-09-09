"""Scope-lock guarantees for the shared Python core.

A sibling app imports this module directly, so:

1. Module-level imports must be STDLIB ONLY (fastapi/yaml/gateway modules may
   appear only inside a try/except ImportError guard).
2. The module must import cleanly with fastapi AND yaml unimportable, and then
   behave identically except `router is None`.
3. The stable surface (__all__) must exist and round-trip on a fixture when
   the core is constructed directly (the sibling integration pattern).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "dashboard" / "plugin_api.py"
sys.path.insert(0, str(REPO / "tests"))
from isolation import disposable_root, isolated_user_home, run_isolated_python

# py3.10+ has sys.stdlib_module_names; older interpreters fall back to a
# curated superset of the stdlib top-levels this test could plausibly see.
if hasattr(sys, "stdlib_module_names"):
    STDLIB = set(sys.stdlib_module_names)
else:  # pragma: no cover — only exercised on py3.9
    STDLIB = set(sys.builtin_module_names) | {
        "__future__", "abc", "argparse", "ast", "asyncio", "base64", "builtins",
        "collections", "contextlib", "copy", "csv", "ctypes", "dataclasses",
        "datetime", "decimal", "difflib", "email", "enum", "fcntl", "fnmatch",
        "functools", "gc", "getpass", "glob", "gzip", "hashlib", "heapq",
        "html", "http", "importlib", "inspect", "io", "ipaddress", "itertools",
        "json", "logging", "math", "mimetypes", "mmap", "multiprocessing",
        "numbers", "operator", "os", "pathlib", "pickle", "platform", "pprint",
        "queue", "random", "re", "readline", "resource", "runpy", "secrets",
        "select", "shutil", "signal", "site", "socket", "sqlite3", "ssl",
        "stat", "string", "struct", "subprocess", "sys", "tarfile", "tempfile",
        "termios", "textwrap", "threading", "time", "traceback", "types",
        "typing", "unicodedata", "unittest", "urllib", "uuid", "warnings",
        "weakref", "webbrowser", "xml", "zipfile", "zlib",
    }


def _module_tree():
    return ast.parse(MODULE.read_text(encoding="utf-8"))


class ImportHygieneTests(unittest.TestCase):
    def test_module_level_imports_are_stdlib_only(self) -> None:
        """Unguarded module-level imports: stdlib only. No gateway, no
        third-party, no sibling-project imports."""
        tree = _module_tree()
        bad = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in STDLIB and root != "__future__":
                        bad.append((node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if node.level == 0 and root and root not in STDLIB and root != "__future__":
                    bad.append((node.lineno, node.module))
        self.assertEqual(bad, [], f"non-stdlib module-level imports: {bad}")

    def test_thirdparty_imports_are_guarded(self) -> None:
        """fastapi (and any other non-stdlib import) may only appear inside a
        try/except whose handler catches ImportError."""
        tree = _module_tree()
        guarded_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                catches_import_error = any(
                    isinstance(h.type, ast.Name) and h.type.id == "ImportError"
                    or isinstance(h.type, ast.Tuple)
                    and any(isinstance(t, ast.Name) and t.id == "ImportError" for t in h.type.elts)
                    for h in node.handlers
                )
                if catches_import_error:
                    for inner in ast.walk(node):
                        if isinstance(inner, ast.ImportFrom) and inner.module:
                            guarded_modules.add(inner.module.split(".")[0])
                        elif isinstance(inner, ast.Import):
                            for alias in inner.names:
                                guarded_modules.add(alias.name.split(".")[0])
        # every non-stdlib import ANYWHERE in the file must be one of the
        # guarded ones
        stray = []
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                root = n.split(".")[0]
                if root and root not in STDLIB and root != "__future__" and root not in guarded_modules:
                    stray.append((node.lineno, n))
        self.assertEqual(stray, [], f"unguarded third-party imports: {stray}")
        self.assertIn("fastapi", guarded_modules, "fastapi import lost its ImportError guard")

    def test_imports_cleanly_without_fastapi_and_yaml(self) -> None:
        """Run in a fresh interpreter with fastapi/yaml import-blocked; the
        module must load, export its stable surface, and set router=None."""
        probe = """
import sys
class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in BLOCKED:
            raise ImportError(name + ' is blocked in this probe')
BLOCKED = {'fastapi', 'yaml'}
sys.meta_path.insert(0, Blocker())
import importlib.util
spec = importlib.util.spec_from_file_location('sib_core', MODULE_PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert m.router is None, m.router
for name in m.__all__:
    assert hasattr(m, name), name
core = m.HermesLoadoutCore(m.Path.cwd() / 'absent-hermes', {'hermes': {'label': 'H', 'special': 'config'}})
st = core.state()
assert st['ok'] and st['skills'] == []
print('PROBE_OK', m.PLUGIN_VERSION)
""".replace('MODULE_PATH', repr(str(MODULE)))
        with disposable_root() as root:
            r = run_isolated_python(probe, root / "user")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PROBE_OK", r.stdout)

    def test_no_io_at_import_time(self) -> None:
        """Importing the module must not create files or dirs anywhere."""
        with disposable_root() as root:
            td = str(root / "user")
            probe = (
                "import sys, os\n"
                "os.chdir(r'%s')\n"
                "before = set(os.listdir('.'))\n"
                "import importlib.util\n"
                "spec = importlib.util.spec_from_file_location('sib_core2', r'%s')\n"
                "m = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(m)\n"
                "after = set(os.listdir('.'))\n"
                "assert before == after, after - before\n"
                "print('NO_IO_OK')\n"
            ) % (td, MODULE)
            r = run_isolated_python(probe, Path(td))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("NO_IO_OK", r.stdout)


def _load_module():
    spec = importlib.util.spec_from_file_location("pa_helpers", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WindowsPathHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pa = _load_module()
    r"""same_path / is_inside must survive \}? prefixes, case, and 8.3 names."""

    def test_strip_extended(self) -> None:
        self.assertEqual(self.pa._strip_extended(r"\\?\C:\x\y"), "C:\\x\\y")
        self.assertEqual(self.pa._strip_extended(r"\\?\UNC\server\share"), r"\\server\share")
        self.assertEqual(self.pa._strip_extended("C:/x"), "C:/x")

    def test_same_path_literal_windows_forms(self) -> None:
        a = self.pa.Path(r"\\?\C:\Users\runneradmin\Temp\x")
        b = self.pa.Path(r"C:\Users\runneradmin\Temp\x")
        self.assertTrue(self.pa.same_path(a, b))
        if os.name == "nt":
            # 8.3 short names expand via realpath only where the dir exists
            c = self.pa.Path(r"C:\Users\RUNNER~1\Temp\x")
            self.assertTrue(self.pa.same_path(a, c))

    def test_same_path_case_and_slash(self) -> None:
        self.assertTrue(self.pa.same_path(self.pa.Path("/Tmp/A"), self.pa.Path("/tmp/a")) if os.name == "nt" else True)
        self.assertTrue(self.pa.same_path(self.pa.Path("/tmp/../tmp/a"), self.pa.Path("/tmp/a")))

    def test_same_path_macos_alias(self) -> None:
        # on macOS /tmp is a symlink to /private/tmp — realpath canonicalizes
        if Path("/tmp").exists() and Path("/private/tmp").exists():
            self.assertTrue(self.pa.same_path(Path("/tmp"), Path("/private/tmp")))

    def test_is_inside(self) -> None:
        root = self.pa.Path("/tmp")
        self.assertTrue(self.pa.is_inside(self.pa.Path("/tmp/x/y"), root))
        self.assertFalse(self.pa.is_inside(self.pa.Path("/tmpx"), root))
        self.assertTrue(self.pa.is_inside(self.pa.Path("/tmp"), root))  # equal counts as inside


class StableSurfaceTests(unittest.TestCase):
    """The documented sibling integration pattern: load by path, construct
    the core with explicit home/tools, drive it as plain functions."""

    def setUp(self) -> None:
        sys.path.insert(0, str(REPO / "tests"))
        from test_plugin_api import Fixture, pa  # noqa: F401

        self.pa = pa
        self.fx = Fixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_all_matches_reality(self) -> None:
        for name in self.pa.__all__:
            self.assertTrue(hasattr(self.pa, name), f"__all__ entry missing: {name}")
        # __all__ is the stable contract; module imports/optionals (os, router,
        # ...) may also be public. What must NEVER be public: gateway modules
        # or anything that would make the core unusable without the gateway.
        leaked = [
            n for n in dir(self.pa)
            if not n.startswith("_") and ("hermes_cli" in n or "gateway" in n.lower())
        ]
        self.assertEqual(leaked, [], f"gateway leakage in module namespace: {leaked}")

    def test_sibling_roundtrip(self) -> None:
        core = self.pa.HermesLoadoutCore(self.fx.home, self.fx.tools, log_path=self.fx.tmp / "data" / "m.log")
        st = core.state()
        self.assertTrue(st["ok"])
        sid = "apple/apple-notes"
        r = core.toggle(sid, "claude", True)
        self.assertTrue(r["ok"])
        self.assertEqual(core.toggle(sid, "claude", False)["state"], "missing")
        # config editing helpers work standalone (no core instance needed)
        text = (self.fx.home / "config.yaml").read_text()
        out = self.pa.set_disabled_member(text, "some-skill", add=True)
        self.assertIn("some-skill", self.pa.parse_disabled(out))
        # Catalog candidates also depend on the user home and environment.
        with isolated_user_home(self.fx.tmp / "user", self.fx.home):
            tools = self.pa.load_tools_config(self.fx.home)
        self.assertIn("claude", tools)

    def test_expand_path_never_cwd(self) -> None:
        os.environ.pop("SKT_SIBLING_VAR", None)
        with self.assertRaises(ValueError):
            self.pa.expand_path("${SKT_SIBLING_VAR:-}")
        with self.assertRaises(ValueError):
            self.pa.expand_path("   ")
        # core treats un-expandable tool dirs as unconfigured, never CWD
        core = self.pa.HermesLoadoutCore(
            self.fx.home, {"ghost": {"label": "Ghost", "dir": "${SKT_SIBLING_VAR:-}"}}
        )
        self.assertIsNone(core.tool_dir("ghost"))


if __name__ == "__main__":
    unittest.main()
