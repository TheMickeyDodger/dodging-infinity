"""CI's whitespace check judges every tracked byte, including the cmd launcher.

The Python job ends with ``git diff --check`` against the empty tree. A
carriage return is trailing whitespace. ``impeccable.cmd`` was stored with
CRLF, so every line failed that check, and four markdown files had a real
trailing space or a blank line at EOF. The fix is the bytes: the launcher
is LF, and those markdown lines are not padded. No path opts out of the
check.

Git goes through ``tests/_hermetic_git.py``. A dynamic ``git`` argv in
this file is an identity-guard failure even when the subcommand is only
``diff`` or ``hash-object``.
"""

import os
import shutil
import tempfile
import unittest

from _hermetic_git import run_git, run_git_completed

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CMD_PATH = os.path.join(
    ".claude", "skills", "impeccable", "scripts", "impeccable.cmd",
)
CI_CHECK = 'git diff --check "$(git hash-object -t tree /dev/null)"'
MARKDOWN_TRAILING_SPACE = (
    (os.path.join(".claude", "skills", "impeccable", "reference",
                  "harden.md"), (143, 144, 145, 262)),
    (os.path.join(".claude", "skills", "impeccable", "reference",
                  "optimize.md"), (37,)),
    (os.path.join(".impeccable", "critique",
                  "2026-11-08T05-01-50Z__webview-ui-src-components-"
                  "discanpanel-tsx-735256e7.md"), (5,)),
)


def _empty_tree(repo):
    return run_git("hash-object", "-t", "tree", "/dev/null", cwd=repo)


def _diff_check(repo):
    return run_git_completed(
        ["diff", "--check", _empty_tree(repo)],
        cwd=repo, check=False, text=False,
    )


class LiveWhitespaceTests(unittest.TestCase):

    def test_ci_command_is_unchanged(self):
        workflow = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
        with open(workflow, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(CI_CHECK, text)

    def test_cmd_launcher_has_no_carriage_return_or_trailing_space(self):
        path = os.path.join(REPO_ROOT, CMD_PATH)
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.endswith(b"\n"))
        self.assertFalse(data.endswith(b"\n\n"))
        for number, line in enumerate(data.split(b"\n")[:-1], 1):
            self.assertFalse(
                line.endswith((b" ", b"\t")),
                "%s:%d has trailing whitespace" % (CMD_PATH, number),
            )

    def test_cmd_launcher_is_not_exempt_from_the_whitespace_check(self):
        attributes = os.path.join(os.path.dirname(CMD_PATH), ".gitattributes")
        self.assertFalse(
            os.path.exists(os.path.join(REPO_ROOT, attributes)),
            attributes,
        )
        result = run_git_completed(
            ["check-attr", "whitespace", "--", CMD_PATH],
            cwd=REPO_ROOT, check=False, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "%s: whitespace: unspecified" % CMD_PATH,
        )

    def test_markdown_lines_that_failed_ci_have_no_trailing_space(self):
        for relative, numbers in MARKDOWN_TRAILING_SPACE:
            path = os.path.join(REPO_ROOT, relative)
            with open(path, "rb") as handle:
                data = handle.read()
            self.assertNotIn(b"\r", data, relative)
            lines = data.split(b"\n")
            for number in numbers:
                self.assertFalse(
                    lines[number - 1].endswith((b" ", b"\t")),
                    "%s:%d still has trailing whitespace" % (relative, number),
                )
        extract = os.path.join(
            REPO_ROOT, ".claude", "skills", "impeccable", "reference",
            "extract.md",
        )
        with open(extract, "rb") as handle:
            data = handle.read()
        self.assertTrue(data.endswith(b"\n"))
        self.assertFalse(data.endswith(b"\n\n"))

    def test_repository_passes_the_ci_whitespace_check(self):
        result = _diff_check(REPO_ROOT)
        detail = (result.stdout + result.stderr).decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, detail)


class CheckerStillSeesRealWhitespaceTests(unittest.TestCase):
    """A temp repository. The check fails on the bytes CI reported."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ci-whitespace-")
        self.addCleanup(shutil.rmtree, self.repo)
        run_git("init", "-q", cwd=self.repo)

    def _check(self, name, data):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        run_git("add", "-A", cwd=self.repo)
        return _diff_check(self.repo)

    def test_a_carriage_return_is_trailing_whitespace(self):
        result = self._check("impeccable.cmd", b"@echo off\r\nsetlocal\r\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"impeccable.cmd:1: trailing whitespace.", result.stdout)

    def test_a_trailing_space_fails(self):
        result = self._check("note.md", b"hello \n")
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"note.md:1: trailing whitespace.", result.stdout)

    def test_a_blank_line_at_eof_fails(self):
        result = self._check("note.md", b"hello\n\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"new blank line at EOF", result.stdout)

    def test_lf_without_a_trailer_passes(self):
        result = self._check("impeccable.cmd", b"@echo off\nsetlocal\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
