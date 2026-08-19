import subprocess
import tempfile
import unittest
from pathlib import Path

from herdr.guards import (
    install_git_guard,
    simple_git_commit,
    simple_git_push,
)


class HerdrGuardTests(unittest.TestCase):
    def make_repo(self):
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)

        subprocess.run(
            [
                "git",
                "init",
                str(repo),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        return temp, repo

    def test_commit_safety_parser(self):
        self.assertEqual(
            simple_git_commit(
                "git commit -m test"
            ),
            (
                True,
                "",
            ),
        )

        ok, reason = simple_git_commit(
            "git commit -m test --no-verify"
        )

        self.assertFalse(
            ok
        )

        self.assertIn(
            "forbidden",
            reason,
        )

    def test_push_safety_parser(self):
        ok, _ = simple_git_push(
            "git push --force"
        )

        self.assertFalse(
            ok
        )

        self.assertEqual(
            simple_git_push(
                "git push --dry-run"
            ),
            (
                True,
                "dry-run",
            ),
        )

    def test_installed_hooks_do_not_depend_on_herdctl(self):
        temp, repo = self.make_repo()
        self.addCleanup(
            temp.cleanup
        )

        install_git_guard(
            repo
        )

        git_dir = Path(
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "rev-parse",
                    "--git-dir",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )

        if not git_dir.is_absolute():
            git_dir = (
                repo
                / git_dir
            ).resolve()

        for name in [
            "pre-commit",
            "reference-transaction",
            "pre-push",
        ]:
            text = (
                git_dir
                / "hooks"
                / name
            ).read_text()

            self.assertNotIn(
                "herdctl",
                text,
            )

            self.assertIn(
                "herdr.guards",
                text,
            )


if __name__ == "__main__":
    unittest.main()
