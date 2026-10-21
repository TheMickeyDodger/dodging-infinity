"""Run CI's per-file test loop with bounded parallelism.

CI runs every ``tests/test_*.py`` file as its own program:
``PYTHONPATH="$PWD" python tests/test_X.py``. This runner keeps that
exact per-file form -- each file in its OWN interpreter process,
``[sys.executable, "<repo>/tests/test_X.py"]``, with ``PYTHONPATH`` set
to the repository root and the repository root as the working
directory -- and changes only how many files run at once.

It never uses ``python -m unittest tests.test_X``. That form imports
``tests/__init__.py``, which installs ``_scope_hygiene``'s guard, and it
fails to load at all without ``tests/`` on ``sys.path``.

Contract (pinned by tests/test_ci_runner.py):

- At most ``--jobs`` files run at the same time. The default is
  ``min(4, os.cpu_count())``, and at least 1.
- Every selected file runs, even when another file fails. There is no
  fail-fast and no per-file timeout: each test's own bounds decide when
  it ends, as before.
- The exit status is 0 only when every file exited 0.
- A file's stdout and stderr are captured together and printed as ONE
  block as soon as that file finishes, so a cancelled job still shows
  every file that had already finished. Blocks therefore appear in
  completion order. A ``start`` line is printed when each file
  launches, so a file that hangs can be named from the log.
- A passing file's block is collapsed with a GitHub Actions
  ``::group::``. A failing file's block is printed in full and is not
  collapsed. Captured text is wrapped in ``::stop-commands::``, so
  nothing a test prints can open or close a group or issue any other
  workflow command.
- A summary sorted by file name ends the run: each file's status, exit
  code and duration, then the failure count, then the verdict.
- On SIGINT or SIGTERM the runner starts no more files. It prints what
  each still-running file has written so far, not collapsed, stops
  those processes, prints the summary and exits with 128 + the signal
  number.

Standard library only; runs on Python 3.9 and 3.13.

Usage: python tests/_ci_runner.py [--jobs N] [--exclude test_X.py ...]
"""

import argparse
import glob
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time

JOB_CAP = 4
TESTS_DIRNAME = "tests"
TEST_PATTERN = "test_*.py"
POLL_INTERVAL = 0.05
STOP_GRACE = 3.0
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASSED = "passed"
FAILED = "failed"
INTERRUPTED = "interrupted"
NOT_STARTED = "not started"

_LABELS = {
    PASSED: "PASS",
    FAILED: "FAIL",
    INTERRUPTED: "INTERRUPTED",
    NOT_STARTED: "NOT STARTED",
}


class SelectionError(Exception):
    """The requested file selection cannot be used (exit status 2)."""


class FileResult(object):
    """The outcome of one test file."""

    def __init__(self, relpath, status, returncode=None, duration=None):
        self.relpath = relpath
        self.status = status
        self.returncode = returncode
        self.duration = duration

    def __repr__(self):
        return "FileResult(%r, %r, returncode=%r)" % (
            self.relpath, self.status, self.returncode)


class _Running(object):
    def __init__(self, relpath, process, capture_path, started):
        self.relpath = relpath
        self.process = process
        self.capture_path = capture_path
        self.started = started


def default_jobs():
    """``min(JOB_CAP, os.cpu_count())``; 1 when the count is unknown."""
    count = os.cpu_count()
    if not count or count < 1:
        return 1
    return min(JOB_CAP, count)


def select_files(repo, excludes=()):
    """Sorted repo-relative paths of ``tests/test_*.py`` under ``repo``,
    minus the excluded basenames.

    Every exclusion must be a bare file name that matches a selected
    file. A misspelt exclusion is an error rather than a silent no-op,
    and an empty selection is an error rather than a vacuous pass.
    """
    excludes = list(excludes)
    for name in excludes:
        if (not name or name in (".", "..")
                or os.path.basename(name) != name):
            raise SelectionError(
                "--exclude takes a bare file name, got %r" % (name,))
    pattern = os.path.join(glob.escape(repo), TESTS_DIRNAME, TEST_PATTERN)
    found = sorted(
        os.path.basename(path) for path in glob.glob(pattern)
        if os.path.isfile(path)
    )
    missing = sorted(set(excludes) - set(found))
    if missing:
        raise SelectionError(
            "excluded file(s) not found in %s: %s"
            % (os.path.join(repo, TESTS_DIRNAME), ", ".join(missing)))
    excluded = set(excludes)
    selected = [
        TESTS_DIRNAME + "/" + name for name in found if name not in excluded
    ]
    if not selected:
        raise SelectionError("no test files selected")
    return selected


def build_command(repo, relpath):
    """CI's per-file form: this interpreter running the file by path."""
    return [sys.executable, os.path.join(repo, relpath)]


def child_environment(repo, base=None):
    """The parent environment with ``PYTHONPATH`` set to ``repo``,
    replacing any inherited value, as ``PYTHONPATH="$PWD"`` does."""
    env = dict(os.environ if base is None else base)
    env["PYTHONPATH"] = repo
    return env


def _write(out, text):
    out.write(text.encode("utf-8") if isinstance(text, str) else text)
    out.flush()


def _exit_text(returncode):
    if returncode is None:
        return "-"
    if returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = "unknown"
        return "signal %d (%s)" % (-returncode, name)
    return "exit %d" % returncode


def _seconds(duration):
    return "-" if duration is None else "%.2fs" % duration


def _read_capture(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as error:
        return ("[ci-runner] could not read captured output: %s\n"
                % (error,)).encode("utf-8")


def _quoted_body(body, token):
    """``body`` between stop-commands lines, ending with a newline so
    the resume line always starts a line of its own."""
    if body and not body.endswith(b"\n"):
        body += b"\n"
    return (("::stop-commands::%s\n" % token).encode("ascii") + body
            + ("::%s::\n" % token).encode("ascii"))


def emit_block(out, result, body, token):
    """Print one file's captured output as one block."""
    label = _LABELS[result.status]
    if result.status == PASSED:
        head = "::group::%s %s (%s, %s)\n" % (
            label, result.relpath, _exit_text(result.returncode),
            _seconds(result.duration))
        tail = "::endgroup::\n"
    else:
        if result.status == INTERRUPTED:
            detail = "still running after %s; partial output" % (
                _seconds(result.duration),)
        else:
            detail = "%s, %s" % (_exit_text(result.returncode),
                                 _seconds(result.duration))
        head = "[ci-runner] ======== %s %s (%s) ========\n" % (
            label, result.relpath, detail)
        tail = "[ci-runner] ======== end %s %s ========\n" % (
            label, result.relpath)
    _write(out, head.encode("utf-8") + _quoted_body(body, token)
           + tail.encode("utf-8"))


def _launch(repo, relpath, env, capture_dir, index, out):
    capture_path = os.path.join(
        capture_dir, "%04d-%s.log" % (index, os.path.basename(relpath)))
    _write(out, "[ci-runner] start %s\n" % relpath)
    started = time.monotonic()
    with open(capture_path, "wb") as capture:
        try:
            process = subprocess.Popen(
                build_command(repo, relpath), cwd=repo, env=env,
                stdin=subprocess.DEVNULL, stdout=capture,
                stderr=subprocess.STDOUT,
            )
        except OSError as error:
            capture.write(("[ci-runner] could not start %s: %s\n"
                           % (relpath, error)).encode("utf-8"))
            return None, capture_path, started
    return _Running(relpath, process, capture_path, started), None, None


def _stop_processes(running):
    for item in running:
        try:
            item.process.terminate()
        except OSError:
            pass
    deadline = time.monotonic() + STOP_GRACE
    for item in running:
        try:
            item.process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            item.process.kill()
            item.process.wait()


def run_files(repo, relpaths, jobs, out, should_stop=None):
    """Run every file in ``relpaths``, at most ``jobs`` at a time.

    ``out`` is a binary stream. ``should_stop`` is polled on every
    pass of the loop and immediately before every launch; once it
    returns true no further file starts, and the files still running
    are reported with their partial output and stopped.

    Returns ``(results, stopped)``: one FileResult per file, sorted by
    path, and whether the run was stopped early.
    """
    if jobs < 1:
        raise ValueError("jobs must be at least 1, got %r" % (jobs,))
    token = secrets.token_hex(16)
    env = child_environment(repo)
    pending = list(relpaths)
    running = []
    results = {}
    stopped = False
    with tempfile.TemporaryDirectory(prefix="ci-runner-") as capture_dir:
        index = 0
        try:
            while True:
                still_running = []
                for item in running:
                    returncode = item.process.poll()
                    if returncode is None:
                        still_running.append(item)
                        continue
                    result = FileResult(
                        item.relpath,
                        PASSED if returncode == 0 else FAILED,
                        returncode, time.monotonic() - item.started)
                    results[item.relpath] = result
                    emit_block(out, result, _read_capture(item.capture_path),
                               token)
                running = still_running
                if should_stop is not None and should_stop():
                    stopped = True
                    break
                while pending and len(running) < jobs:
                    # Checked before EVERY launch, so a stop request that
                    # arrives while a batch is being started ends it there.
                    if should_stop is not None and should_stop():
                        stopped = True
                        break
                    relpath = pending.pop(0)
                    index += 1
                    item, failed_capture, started = _launch(
                        repo, relpath, env, capture_dir, index, out)
                    if item is None:
                        result = FileResult(
                            relpath, FAILED, None,
                            time.monotonic() - started)
                        results[relpath] = result
                        emit_block(out, result,
                                   _read_capture(failed_capture), token)
                    else:
                        running.append(item)
                if stopped or (not running and not pending):
                    break
                time.sleep(POLL_INTERVAL)
        finally:
            # Reached on a stop request, and on any unexpected error, so
            # no child outlives the runner unreported. The partial output
            # goes out first, before the processes are stopped, so it is
            # already in the log if the runner itself is killed next.
            for item in running:
                result = FileResult(item.relpath, INTERRUPTED, None,
                                    time.monotonic() - item.started)
                results[item.relpath] = result
                try:
                    emit_block(out, result,
                               _read_capture(item.capture_path), token)
                except Exception:
                    pass  # the output itself failed; still stop children
            _stop_processes(running)
            for item in running:
                results[item.relpath].returncode = item.process.returncode
            for relpath in pending:
                results[relpath] = FileResult(relpath, NOT_STARTED)
    return [results[relpath] for relpath in sorted(results)], stopped


def emit_summary(out, results):
    """The deterministic end-of-run summary, sorted by file name."""
    ordered = sorted(results, key=lambda result: result.relpath)
    lines = ["[ci-runner] summary, sorted by file:"]
    for result in ordered:
        lines.append("[ci-runner]   %-11s  %-20s  %9s  %s" % (
            _LABELS[result.status], _exit_text(result.returncode),
            _seconds(result.duration), result.relpath))
    counts = {}
    for result in ordered:
        counts[result.status] = counts.get(result.status, 0) + 1
    failures = len(ordered) - counts.get(PASSED, 0)
    breakdown = ", ".join(
        "%d %s" % (counts[status], status)
        for status in (PASSED, FAILED, INTERRUPTED, NOT_STARTED)
        if counts.get(status))
    lines.append("[ci-runner] files: %d (%s)" % (len(ordered), breakdown))
    lines.append("[ci-runner] failures: %d" % failures)
    lines.append("[ci-runner] verdict: %s" % ("PASS" if failures == 0
                                              else "FAIL"))
    _write(out, "\n".join(lines) + "\n")
    return failures


def _positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not an integer: %r" % (text,))
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1: %r" % (text,))
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tests/_ci_runner.py",
        description="Run tests/test_*.py files in parallel, one "
                    "interpreter per file, the way CI runs them.")
    parser.add_argument(
        "--jobs", type=_positive_int, default=None,
        help="files to run at once (default: min(%d, CPU count))"
             % JOB_CAP)
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="BASENAME",
        help="a tests/ file name to leave out; repeat for more")
    parser.add_argument(
        "--repo", default=REPO_ROOT,
        help="repository root (default: the directory above tests/)")
    return parser


def main(argv=None, out=None):
    args = build_parser().parse_args(argv)
    if out is None:
        sys.stdout.flush()
        out = sys.stdout.buffer
    repo = os.path.abspath(args.repo)
    try:
        relpaths = select_files(repo, args.exclude)
    except SelectionError as error:
        sys.stderr.write("[ci-runner] error: %s\n" % (error,))
        return 2
    jobs = args.jobs if args.jobs is not None else default_jobs()
    _write(out, (
        "[ci-runner] interpreter: %s (Python %s)\n"
        "[ci-runner] repository: %s\n"
        "[ci-runner] jobs: %d (%s)\n"
        "[ci-runner] files: %d selected; excluded: %s\n"
    ) % (
        sys.executable, sys.version.split()[0], repo, jobs,
        "--jobs" if args.jobs is not None
        else "default min(%d, os.cpu_count()=%s)" % (JOB_CAP,
                                                     os.cpu_count()),
        len(relpaths), ", ".join(sorted(args.exclude)) or "none",
    ))

    received = []

    def on_signal(signum, frame):
        received.append(signum)

    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, on_signal)
    try:
        results, stopped = run_files(
            repo, relpaths, jobs, out, should_stop=lambda: bool(received))
        if stopped:
            _write(out, "[ci-runner] stopped by signal %d; no further "
                        "files started\n" % received[0])
        failures = emit_summary(out, results)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if stopped:
        return 128 + received[0]
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
