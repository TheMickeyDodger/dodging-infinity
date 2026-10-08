"""Regression: the retired Grok Bot surface stays retired, and the
product starts without it (Task 8).

The Grok Bot MCP interaction endpoint (the ``grok_mcp`` package and the
``grokmcp.py`` entry script) was removed from the active product. Grok as
a model or Herdr agent runtime is a separate thing and is NOT retired:
``herdctl.py`` keeps ``grok`` in ``SUPPORTED`` / ``INTEGRATIONS`` and
nothing here touches it.

Each check fails if the surface is reintroduced:

1. on disk, or anywhere in the derived product tree;
2. as an installed console entry (``scripts/install.sh``);
3. on the startup path: every installed entry script runs ``--help`` to
   exit 0 in a fresh interpreter whose import system REFUSES any
   ``grok_mcp`` / ``grokmcp`` module, and loads none.

Planted-probe self-checks prove each detector fires, so no check can
pass on an empty or mis-filtered scope. Every child interpreter carries
an independent ``timeout`` bound (CONTRIBUTING.md termination rule).
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_workflow_authority import derive_product_python_files  # noqa: E402

RETIRED_MODULES = ("grok_mcp", "grokmcp")
RETIRED_PATHS = ("grok_mcp", "grokmcp.py", "grok_mcp.py")
# The console entries the installer has always exposed; the derived list
# must contain at least these, so the startup check is never vacuous.
KNOWN_ENTRIES = ("herdctl.py", "codexgw.py", "tgop.py", "dirun.py")
ENTRY_RE = re.compile(r'exec python3 "\$ROOT/([A-Za-z0-9_]+\.py)"')
CHILD_TIMEOUT_SECONDS = 120
REFUSAL_MARK = "retired Grok Bot surface imported:"

# Runs one script with ``--help`` under an import hook that refuses the
# retired modules, then reports any that loaded anyway.
STARTUP_PROBE = """
import importlib.abc
import runpy
import sys

RETIRED = %r


class RefuseRetired(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in RETIRED:
            raise ImportError(%r + " " + name)
        return None


sys.meta_path.insert(0, RefuseRetired())
script = sys.argv[1]
sys.argv = [script, "--help"]
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    if exc.code is None:
        code = 0
    elif isinstance(exc.code, int):
        code = exc.code
    else:
        code = 1
loaded = sorted(n for n in sys.modules if n.split(".")[0] in RETIRED)
if loaded:
    sys.stderr.write("retired modules loaded: %%s\\n" %% loaded)
    code = 3
sys.exit(code)
""" % (RETIRED_MODULES, REFUSAL_MARK)


def retired_surface_problems(root):
    """Every retired Grok Bot path present under ``root``."""
    root = Path(root)
    problems = [name for name in RETIRED_PATHS if (root / name).exists()]
    for path in derive_product_python_files(root):
        relpath = path.relative_to(root)
        if any(Path(part).stem in RETIRED_MODULES for part in relpath.parts):
            problems.append(relpath.as_posix())
    return sorted(set(problems))


def installed_entries(install_text):
    """Entry scripts the installer exposes as console commands."""
    return ENTRY_RE.findall(install_text)


def run_startup_probe(script):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-c", STARTUP_PROBE, str(script)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
    )


class RetiredSurfaceAbsentTests(unittest.TestCase):

    def test_no_grok_bot_surface_in_the_tree(self):
        self.assertTrue(derive_product_python_files(REPO_ROOT))
        self.assertEqual(retired_surface_problems(REPO_ROOT), [])

    def test_detector_finds_a_planted_surface(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "keep").mkdir()
            (root / "keep" / "module.py").write_text("X = 1\n")
            self.assertEqual(retired_surface_problems(root), [])
            (root / "grok_mcp").mkdir()
            (root / "grok_mcp" / "__init__.py").write_text("")
            (root / "grokmcp.py").write_text("")
            (root / "keep" / "grok_mcp").mkdir()
            (root / "keep" / "grok_mcp" / "server.py").write_text("")
            self.assertEqual(retired_surface_problems(root), [
                "grok_mcp", "grok_mcp/__init__.py", "grokmcp.py",
                "keep/grok_mcp/server.py",
            ])


class InstallerEntryTests(unittest.TestCase):

    def test_installer_exposes_no_grok_bot_entry(self):
        text = (REPO_ROOT / "scripts" / "install.sh").read_text()
        entries = installed_entries(text)
        self.assertTrue(set(KNOWN_ENTRIES) <= set(entries), entries)
        for entry in entries:
            self.assertNotIn(Path(entry).stem, RETIRED_MODULES, entry)
            self.assertTrue((REPO_ROOT / entry).is_file(), entry)

    def test_entry_parser_finds_a_planted_grok_entry(self):
        planted = 'exec python3 "$ROOT/grokmcp.py" "\\$@"\n'
        self.assertEqual(installed_entries(planted), ["grokmcp.py"])


class StartupWithoutGrokTests(unittest.TestCase):

    def test_every_installed_entry_starts_with_grok_refused(self):
        text = (REPO_ROOT / "scripts" / "install.sh").read_text()
        entries = installed_entries(text)
        self.assertTrue(set(KNOWN_ENTRIES) <= set(entries), entries)
        for entry in entries:
            with self.subTest(entry):
                result = run_startup_probe(REPO_ROOT / entry)
                self.assertEqual(
                    result.returncode, 0,
                    (entry, result.stdout[-2000:], result.stderr[-2000:]),
                )
                self.assertIn("usage", result.stdout.lower(), entry)
                self.assertNotIn(REFUSAL_MARK, result.stderr, entry)

    def test_probe_refuses_a_planted_grok_import(self):
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp) / "control.py"
            control.write_text("print('usage: control')\n")
            result = run_startup_probe(control)
            self.assertEqual(result.returncode, 0, result.stderr)
            planted = Path(temp) / "planted.py"
            planted.write_text("import grok_mcp\n")
            result = run_startup_probe(planted)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(REFUSAL_MARK + " grok_mcp", result.stderr)


if __name__ == "__main__":
    unittest.main()
