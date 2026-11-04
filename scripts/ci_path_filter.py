"""Decide which CI suites a change has to run.

A docs or README-only change does not run the Pixel UI browser suite.
A change that only touches the webview UI does not run the Python suite.
Anything else runs both. A manual run, an unreadable diff, or an empty
diff runs both: a suite is skipped only when the diff was read and every
file in it is outside that suite.

The workflow writes the decision to ``$GITHUB_OUTPUT`` (``python``,
``pixel_ui``, ``reason``). The required check jobs stay in the workflow
and exit success when their suite is skipped, so a skipped suite does
not leave a required check pending.
"""

import os
import subprocess
import sys

WEBVIEW_PREFIX = "integrations/pixel-agents/webview-ui/"
DOC_MEDIA_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".svg", ".md",
)


def is_doc(path):
    """True for README, docs, license, and the repo's documentation media."""
    name = path.rsplit("/", 1)[-1]
    if name.startswith(("README", "LICENSE", "COPYING")):
        return True
    if "/" not in path and path.endswith((".md", ".txt")):
        return True
    if path.startswith("docs/"):
        return True
    if path.startswith("assets/") and path.endswith(DOC_MEDIA_SUFFIXES):
        return True
    return False


def is_webview_ui(path):
    return path.startswith(WEBVIEW_PREFIX)


def classify(paths):
    """``python`` and ``pixel_ui`` booleans, plus a one-line ``reason``.

    ``paths`` is None when the diff could not be read. An empty list is
    an empty diff. Both run every suite.
    """
    if paths is None:
        return {
            "python": True, "pixel_ui": True,
            "reason": "diff unknown; running the Python suite and the Pixel UI suite",
        }
    if not paths:
        return {
            "python": True, "pixel_ui": True,
            "reason": "empty diff; running the Python suite and the Pixel UI suite",
        }
    docs = all(is_doc(path) for path in paths)
    webview = all(is_webview_ui(path) for path in paths)
    if docs and webview:
        reason = ("docs inside the webview UI only; skipping the Python suite "
                  "and the Pixel UI suite")
    elif docs:
        reason = "docs/README-only change; skipping the Pixel UI suite"
    elif webview:
        reason = "webview UI files only; skipping the Python suite"
    else:
        reason = "this change runs the Python suite and the Pixel UI suite"
    return {"python": not webview, "pixel_ui": not docs, "reason": reason}


def _git_paths(*spec):
    out = subprocess.check_output(
        ["git", "diff", "--name-only", *spec], text=True)
    return [line for line in out.splitlines() if line]


def changed_paths():
    """The files this GitHub event changed, or None when that is unknown.

    ``workflow_dispatch`` and a push with no ``before`` (a new branch, all
    zeros) are unknown. A pull request diffs against
    ``github.event.pull_request.base.sha`` (``BASE_SHA``). A push diffs
    ``BEFORE_SHA`` against HEAD. The caller has fetched enough history
    for that commit to be present; a missing commit fails the filter
    rather than skipping a suite.
    """
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event in ("", "workflow_dispatch"):
        return None
    if event == "pull_request":
        base = os.environ.get("BASE_SHA", "")
        if not base:
            return None
        return _git_paths("%s...HEAD" % base)
    if event == "push":
        before = os.environ.get("BEFORE_SHA", "")
        if not before or set(before) <= set("0"):
            return None
        return _git_paths(before, "HEAD")
    return None


def emit(decision):
    print(decision["reason"])
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("python=%s\n" % ("true" if decision["python"] else "false"))
        handle.write("pixel_ui=%s\n" % ("true" if decision["pixel_ui"] else "false"))
        handle.write("reason=%s\n" % decision["reason"])


def main():
    emit(classify(changed_paths()))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print("ci_path_filter: git failed (%s); refusing to skip a suite" % exc,
              file=sys.stderr)
        raise SystemExit(1)
