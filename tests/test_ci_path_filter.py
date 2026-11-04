"""The path filter that lets a docs change skip Pixel UI and a webview-only
change skip the Python suite. Skipping is decided from the file list; an
unknown or empty diff runs both suites."""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from ci_path_filter import classify  # noqa: E402


class ClassifyTests(unittest.TestCase):

    def test_an_unknown_diff_runs_both(self):
        both = classify(None)
        self.assertTrue(both["python"])
        self.assertTrue(both["pixel_ui"])
        self.assertIn("unknown", both["reason"])

    def test_an_empty_diff_runs_both(self):
        both = classify([])
        self.assertTrue(both["python"])
        self.assertTrue(both["pixel_ui"])

    def test_a_readme_skips_pixel_ui_and_runs_python(self):
        decision = classify(["README.md", "docs/ci.md", "assets/demo/DI-demo-v11d.mp4"])
        self.assertTrue(decision["python"])
        self.assertFalse(decision["pixel_ui"])
        self.assertIn("docs/README-only", decision["reason"])

    def test_a_root_markdown_file_is_docs(self):
        decision = classify(["AGENTS.md", "OPERATOR_PROTOCOL.md"])
        self.assertFalse(decision["pixel_ui"])
        self.assertTrue(decision["python"])

    def test_webview_ui_only_skips_python_and_runs_pixel(self):
        decision = classify([
            "integrations/pixel-agents/webview-ui/src/components/DiScanPanel.tsx",
            "integrations/pixel-agents/webview-ui/test/diScanFace.test.ts",
        ])
        self.assertFalse(decision["python"])
        self.assertTrue(decision["pixel_ui"])
        self.assertIn("webview UI", decision["reason"])

    def test_a_readme_inside_the_webview_skips_both(self):
        decision = classify([
            "integrations/pixel-agents/webview-ui/README.md",
        ])
        self.assertFalse(decision["python"])
        self.assertFalse(decision["pixel_ui"])

    def test_a_pixel_server_change_runs_both(self):
        decision = classify([
            "integrations/pixel-agents/server/src/server.ts",
        ])
        self.assertTrue(decision["python"])
        self.assertTrue(decision["pixel_ui"])

    def test_a_mixed_change_runs_both(self):
        decision = classify([
            "README.md",
            "mission_panel/service.py",
        ])
        self.assertTrue(decision["python"])
        self.assertTrue(decision["pixel_ui"])

    def test_office_sprites_are_not_documentation(self):
        decision = classify([
            "integrations/pixel-agents/webview-ui/public/assets/furniture/COFFEE/COFFEE.png",
        ])
        self.assertTrue(decision["pixel_ui"])
        self.assertFalse(decision["python"])

    def test_a_workflow_change_runs_both(self):
        decision = classify([".github/workflows/ci.yml"])
        self.assertTrue(decision["python"])
        self.assertTrue(decision["pixel_ui"])


if __name__ == "__main__":
    unittest.main()
