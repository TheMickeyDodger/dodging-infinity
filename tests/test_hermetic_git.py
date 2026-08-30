"""Guard: test-created Git operations are hermetic w.r.t. identity.

Three parts, all load-bearing:

1. EXECUTED proof that tests/_hermetic_git.py really works with no
   ambient Git identity of any kind — and a negative control proving the
   scrubbed environment actually bites on THIS platform (macOS git
   auto-detects a user@host identity when unconfigured, so a bare env
   scrub is a vacuous proof here; ``user.useConfigOnly=true`` in the
   scrubbed global config deterministically disables auto-detection,
   modelling the Ubuntu CI failure mode — auto-detection fails — on
   every platform).

2. AST anti-reintroduction guard — FAST STRUCTURAL FEEDBACK: every
   identity-requiring Git invocation in tests/ (commit, merge,
   annotated/signed tag, cherry-pick, revert, rebase, am, stash, notes,
   commit-tree) must route through tests/_hermetic_git.py.  Discovery
   is mechanical and anti-vacuous: the scan must rediscover at least
   the mechanically enumerated site count, in at least the known files,
   or fail loudly.

3. EXECUTED identity sweep — THE PRIMARY GUARANTEE: the
   identity-relevant modules run under a transparent logging git shim
   first on PATH, and every identity-requiring git process the shim
   OBSERVES must carry explicit ``-c user.name=``/``-c user.email=`` on
   its EXECUTED argv.  The shim observes exactly the git processes
   that resolve ``git`` through the inherited PATH — not literally
   every git process (see the residual list).  Within that field of
   view the check is shape-independent: it does not care how the argv
   was constructed in source, so it catches every dynamically built
   shape the AST scan cannot see.  Each swept child also asserts, on
   entry AND after its tests ran, that ``shutil.which("git")`` still
   resolves to the shim — a swept process that loses the shim from its
   own PATH fails the sweep rather than silently leaving its field of
   view.

The AST scan classifies the combined token stream of list/tuple
literals and ``+``-concatenations of them (every candidate node
unconditionally — an inner literal is never skipped on the promise an
enclosing expression covers it), scans string-command arguments to
executor callables for a ``git`` token followed by an
identity-requiring verb (through ``%``/``.format``/``str.split``, and
f-strings joined into ONE text with interpolation placeholders), and
resolves helper calls to this chokepoint per-module.

Guard limits, re-derived in round 4 from the full 23-case smuggle
matrix, the reviewer's round-3 sweep attacks (A/B/C/D/E), and an
EXECUTED per-module enumeration of identity-requiring git across all
tests modules (see the round-4 evidence).  A shape the AST scan cannot
see but the executed sweep catches is NOT an unguarded residual —
within the swept modules the sweep is the guarantee.  What actually
remains unguarded:
- a GRANDCHILD process whose environment carries a rewritten PATH that
  excludes the shim (reviewer attack C): the shim only observes git
  resolved through the inherited PATH, and the child-side which-checks
  cover the swept process itself, not environments it hands to its own
  children.  The near-miss house idiom is test_observe.minimal_env,
  which builds exactly such a child PATH and stays observable only
  because it derives the git dir from live ``shutil.which`` resolution
  — which rule F (no absolute git-binary path literals in tests/, a
  fail-closed AST rule) now keeps true: the literal-path variant of
  that idiom, and reviewer attack B, are statically refused;
- a DYNAMICALLY built identity invocation in a module OUTSIDE the
  sweep set.  The sweep set is: modules where the AST scan finds
  routed identity sites, plus modules that DIRECTLY import a name from
  such a module (one level — how test_mitiq_narrative reaches git via
  make_git_repo), plus the pinned known five.  A module whose only
  identity invocations are statically invisible AND that imports no
  identity-helper module is not swept (reviewer attack E is the
  executable witness);
- within the AST layer only (sweep-covered in swept modules):
  constant-fragment tokens (``"gi" + "t"`` — the negative control's
  deliberate shape, and needs constant folding to see), argv/command
  strings from non-literal sources, and calls whose callee is not a
  Name/Attribute expression — the last is deliberately FAIL-OPEN in
  rule B because a literal argv handed to such a callee is already
  caught by rule A, and the sweep covers the executed remainder.
The executed scrubbed-environment CI legs remain the final backstop:
any statically invisible, unswept invocation still dies on an
identity-less runner.

Standalone: PYTHONPATH=$PWD python3 tests/test_hermetic_git.py
"""

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import _hermetic_git
from _hermetic_git import (
    HERMETIC_GIT_ARGS,
    IDENTITY_EMAIL,
    IDENTITY_NAME,
    hermetic_git_argv,
    run_git,
    run_git_completed,
)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HELPER_MODULE = "_hermetic_git"

IDENTITY_SUBCOMMANDS = frozenset({
    "commit", "merge", "cherry-pick", "revert", "rebase", "am",
    "stash", "notes", "commit-tree",
})
# "tag" needs identity only when annotated/signed.
TAG_IDENTITY_FLAGS = frozenset({"-a", "-s", "--annotate", "--sign", "-m"})
# Git global options that take a separate value argument.
VALUED_GIT_OPTIONS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--config-env",
})
# Callables that EXECUTE a command string/argv handed to them.  Only
# these can turn a "git commit ..." string into a process (rule E);
# parsers and assertions cannot.
STRING_EXECUTOR_NAMES = frozenset({
    "run", "call", "check_call", "check_output", "Popen", "system",
    "popen", "spawnl", "spawnlp", "spawnv", "spawnvp", "getoutput",
    "getstatusoutput",
})

# Anti-vacuity floor: mechanical enumeration on 2026-08-26 (task
# 20260826-211424-d566d0) found 15 identity-requiring sites across these
# five modules.  The floor counts LEGACY sites only — this guard file's
# own routed calls are excluded from the numerator, so the floor cannot
# be partly self-satisfied.  If the scan reports fewer, the scanner is
# broken or sites were removed — either way this must be looked at, not
# ignored; re-derive the floor deliberately, never weaken it in passing.
EXPECTED_MINIMUM_IDENTITY_SITES = 15
GUARD_FILE_NAME = os.path.basename(os.path.abspath(__file__))
EXPECTED_FILES_WITH_IDENTITY_SITES = frozenset({
    "test_evidence.py",
    "test_observe.py",
    "test_push_gate.py",
    "test_static.py",
    "test_target_runtime.py",
})


def scrubbed_identity_env(base_dir):
    """An environment with no ambient Git identity of any kind and no
    platform identity auto-detection (user.useConfigOnly=true grants no
    identity; it only forbids git to guess one)."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_")
        and not key.startswith("LC_")
        and key not in ("EMAIL", "HOME", "XDG_CONFIG_HOME", "LANG")
    }
    home = os.path.join(base_dir, "home")
    xdg = os.path.join(base_dir, "xdg")
    os.makedirs(home)
    os.makedirs(xdg)
    global_config = os.path.join(base_dir, "gitconfig")
    with open(global_config, "w") as handle:
        handle.write("[user]\n\tuseConfigOnly = true\n")
    env.update({
        "HOME": home,
        "XDG_CONFIG_HOME": xdg,
        "GIT_CONFIG_GLOBAL": global_config,
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    })
    return env


class HermeticExecutionTests(unittest.TestCase):
    """Executed proof, on the real git binary, with a negative control."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = scrubbed_identity_env(self.tmp.name)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        run_git("init", "-q", self.repo, env=self.env)
        with open(os.path.join(self.repo, "f.txt"), "w") as handle:
            handle.write("x\n")
        run_git("-C", self.repo, "add", "-A", env=self.env)

    def test_scrub_is_load_bearing_negative_control(self):
        # A commit that does NOT carry the helper's identity must FAIL
        # in this environment; if it succeeds, the scrub is vacuous on
        # this platform and every "passes when scrubbed" claim in this
        # file is worthless.  This is the single intentional
        # identity-free git invocation in tests/, and it is built in the
        # DISCLOSED residual shape the module docstring names — tokens
        # assembled from constant fragments, which the scanner cannot
        # constant-fold — so it does not trip the AST guard.  If the
        # scanner ever learns to fold constants, this must move to
        # whatever residual shape the re-derived limits then name.
        git_token = "gi" + "t"
        commit_token = "com" + "mit"
        completed = subprocess.run(
            [git_token, "-C", self.repo, "-c", "commit.gpgsign=false",
             commit_token, "-qm", "no identity"],
            env=self.env, capture_output=True, text=True,
        )
        self.assertNotEqual(
            completed.returncode, 0,
            "scrubbed environment failed to disable ambient/auto-detected "
            "Git identity — the hermeticity proof would be vacuous:\n"
            + completed.stdout + completed.stderr,
        )
        self.assertIn("Please tell me who you are", completed.stderr)

    def test_hermetic_commit_succeeds_with_no_ambient_identity(self):
        run_git("-C", self.repo, "commit", "-qm", "hermetic", env=self.env)
        identity = run_git(
            "-C", self.repo, "log", "-1",
            "--format=%an|%ae|%cn|%ce", env=self.env,
        )
        self.assertEqual(
            identity,
            "|".join([IDENTITY_NAME, IDENTITY_EMAIL,
                      IDENTITY_NAME, IDENTITY_EMAIL]),
        )

    def test_hermetic_annotated_tag_succeeds_with_no_ambient_identity(self):
        run_git("-C", self.repo, "commit", "-qm", "hermetic", env=self.env)
        run_git("-C", self.repo, "tag", "-a", "v-hermetic", "-m", "t",
                env=self.env)
        tagger = run_git(
            "-C", self.repo, "for-each-ref", "--format=%(taggername)",
            "refs/tags/v-hermetic", env=self.env,
        )
        self.assertEqual(tagger, IDENTITY_NAME)

    def test_run_git_completed_check_false_returns_completed_process(self):
        completed = run_git_completed(
            ["-C", self.repo, "rev-parse", "no-such-ref"],
            check=False, env=self.env,
        )
        self.assertNotEqual(completed.returncode, 0)


class HermeticArgvTests(unittest.TestCase):
    """The identity rides the EXECUTED argv (not source text)."""

    def _captured_argv(self, invoke):
        recorded = {}

        def fake_run(argv, **kwargs):
            recorded["argv"] = list(argv)
            completed = subprocess.CompletedProcess(argv, 0, "", "")
            return completed

        with patch.object(_hermetic_git.subprocess, "run", fake_run):
            invoke()
        return recorded["argv"]

    def assert_identity_prefix(self, argv):
        self.assertEqual(argv[0], "git")
        self.assertEqual(tuple(argv[1:1 + len(HERMETIC_GIT_ARGS)]),
                         HERMETIC_GIT_ARGS)
        self.assertIn("user.name=%s" % IDENTITY_NAME, argv)
        self.assertIn("user.email=%s" % IDENTITY_EMAIL, argv)
        self.assertIn("commit.gpgsign=false", argv)

    def test_run_git_executes_identity_argv(self):
        argv = self._captured_argv(
            lambda: run_git("commit", "-qm", "x")
        )
        self.assert_identity_prefix(argv)
        self.assertEqual(argv[-3:], ["commit", "-qm", "x"])

    def test_run_git_completed_executes_identity_argv(self):
        argv = self._captured_argv(
            lambda: run_git_completed(["commit", "-qm", "x"])
        )
        self.assert_identity_prefix(argv)
        self.assertEqual(argv[-3:], ["commit", "-qm", "x"])

    def test_argv_builder_places_identity_before_caller_args(self):
        argv = hermetic_git_argv(["-C", "/r", "commit"])
        self.assert_identity_prefix(argv)
        self.assertEqual(argv[-3:], ["-C", "/r", "commit"])


def _flatten_tokens(node):
    """The token stream of an argv-like expression: a List or Tuple
    literal, a string constant, or any ``+``-concatenation of such (the
    codebase's own argv idiom — ``["git"] + list(argv)``).  String
    constants appear as their values; every dynamic element or opaque
    operand (a Name, a Call, a Starred, ...) appears as a single None so
    positional reasoning stays honest about what it cannot see."""
    if isinstance(node, (ast.List, ast.Tuple)):
        out = []
        for element in node.elts:
            if (isinstance(element, ast.Constant)
                    and isinstance(element.value, str)):
                out.append(element.value)
            else:
                out.append(None)
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _flatten_tokens(node.left) + _flatten_tokens(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return [None]


def _first_subcommand(tokens):
    """First non-option token of a git argv tail, skipping the values of
    valued global options.  Returns ("DYNAMIC", None) when the
    subcommand position itself is dynamic."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token is None:
            return ("DYNAMIC", None)
        if token in VALUED_GIT_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return (token, tokens[index:])
    return (None, None)


def _classify_argv_tail(tokens):
    subcommand, rest = _first_subcommand(tokens)
    if subcommand == "DYNAMIC":
        return "dynamic"
    if subcommand in IDENTITY_SUBCOMMANDS:
        return "identity"
    if subcommand == "tag" and rest and any(
        token in TAG_IDENTITY_FLAGS for token in rest if token
    ):
        return "identity"
    return "other"


def _call_argv_tokens(call_node):
    """The concatenated token stream of a call's positional arguments
    (each flattened through lists, tuples, and ``+``-concatenations)."""
    tokens = []
    for argument in call_node.args:
        tokens.extend(_flatten_tokens(argument))
    return tokens


def _string_command_smuggles_identity(text):
    """True when a command STRING contains a ``git`` token followed by
    an identity-requiring verb (or an annotated/signed tag), at any
    position — ``cd X && git commit ...`` included."""
    tokens = re.split(r"[\s;|&()<>]+", text)
    for index, token in enumerate(tokens):
        if token != "git":
            continue
        tail = set(tokens[index + 1:])
        if IDENTITY_SUBCOMMANDS & tail:
            return True
        if "tag" in tail and TAG_IDENTITY_FLAGS & tail:
            return True
    return False


def _constant_strings_in(node):
    """Every string constant anywhere inside an expression — through
    ``%`` formatting, ``.format`` calls, ``.split()`` receivers, and the
    like.  An f-string (``ast.JoinedStr``) is scanned as ONE joined
    text, with a placeholder token standing in for each interpolation,
    so ``f"git -C {repo} commit ..."`` reads as
    ``git -C <X> commit ...`` — the interpolation syntax the author
    picked must not decide whether a command is visible."""
    joined_constant_ids = set()
    texts = []
    for inner in ast.walk(node):
        if isinstance(inner, ast.JoinedStr):
            pieces = []
            for value in inner.values:
                if (isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    joined_constant_ids.add(id(value))
                    pieces.append(value.value)
                else:
                    pieces.append(" <X> ")
            texts.append("".join(pieces))
    for inner in ast.walk(node):
        if (isinstance(inner, ast.Constant)
                and isinstance(inner.value, str)
                and id(inner) not in joined_constant_ids):
            texts.append(inner.value)
    return texts


def _strings_look_identity_requiring(strings):
    if IDENTITY_SUBCOMMANDS & set(strings):
        return True
    return "tag" in strings and bool(TAG_IDENTITY_FLAGS & set(strings))


def _dedup(entries):
    """Order-preserving exact-string dedup (one argv expression can be
    classified both as itself and inside an enclosing concatenation)."""
    seen = set()
    out = []
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            out.append(entry)
    return out


def _has_subprocess_call(function_node):
    for inner in ast.walk(function_node):
        if (isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in ("subprocess", "os")):
            return True
    return False


def scan_module(path):
    """Scan one tests/*.py module.

    Returns (routed_identity_sites, violations); each entry is a
    human-readable "file:line reason" string.
    """
    filename = os.path.basename(path)
    with open(path) as handle:
        tree = ast.parse(handle.read(), filename=path)

    hermetic_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == HELPER_MODULE:
            for alias in node.names:
                hermetic_names.add(alias.asname or alias.name)

    delegate_names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls_hermetic = any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id in hermetic_names
                for inner in ast.walk(node)
            )
            if calls_hermetic and not _has_subprocess_call(node):
                delegate_names.add(node.name)

    allowed = hermetic_names | delegate_names
    routed = []
    violations = []

    for node in ast.walk(tree):
        # Rule F: an absolute path to a git binary anywhere in tests/
        # is refused outright — executing git by absolute path bypasses
        # PATH resolution and with it the executed sweep's shim
        # (reviewer round-3 attack B).  Fail-closed: any such string
        # constant is a violation regardless of context.
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("/")
                and os.path.basename(node.value) == "git"):
            violations.append(
                "%s:%d absolute git binary path %r bypasses PATH "
                "resolution (and the executed sweep's shim) — resolve "
                "git via PATH" % (filename, node.lineno, node.value)
            )
        # Rule A: any argv expression — list, tuple, or +-concatenation
        # of them — whose combined token stream starts with "git".
        # Every candidate node is classified UNCONDITIONALLY: an inner
        # literal is never skipped on the promise that an enclosing
        # expression covers it (round-02 regression R2-B1 — the
        # enclosing expression may itself be unclassifiable when its
        # stream head is opaque).  A same-argv double report is folded
        # by the exact-string dedup at the end of this function.
        if (isinstance(node, (ast.List, ast.Tuple)) or (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Add))):
            tokens = _flatten_tokens(node)
            if tokens and tokens[0] == "git":
                verdict = _classify_argv_tail(tokens[1:])
                if verdict == "identity":
                    violations.append(
                        "%s:%d raw git argv (list/tuple/"
                        "concatenation) with an identity-requiring "
                        "subcommand bypasses _hermetic_git"
                        % (filename, node.lineno)
                    )
                elif verdict == "dynamic":
                    violations.append(
                        "%s:%d raw git argv whose subcommand "
                        "position is dynamic cannot be proven "
                        "identity-free — route it through "
                        "_hermetic_git" % (filename, node.lineno)
                    )
        # Rules B and E: calls carrying identity-requiring git argv.
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name):
            callee_name = callee.id
        elif isinstance(callee, ast.Attribute):
            callee_name = callee.attr
        else:
            continue
        # Rule E: a command STRING that reaches git, handed to something
        # that executes strings (parsers and assertions cannot).  String
        # constants are collected through %-formatting, .format,
        # f-string pieces, and .split() receivers.
        if callee_name in STRING_EXECUTOR_NAMES:
            for argument in node.args:
                if any(_string_command_smuggles_identity(text)
                       for text in _constant_strings_in(argument)):
                    violations.append(
                        "%s:%d string git command with an "
                        "identity-requiring subcommand bypasses "
                        "_hermetic_git" % (filename, node.lineno)
                    )
        # Rule B: any call whose positional arguments carry an
        # identity-requiring git verb (bare string, list/tuple element,
        # or +-concatenation operand) must resolve to the chokepoint.
        # unittest assert* methods compare values; they cannot execute
        # an argv, so an expected-value literal is not an invocation.
        if callee_name.startswith("assert"):
            continue
        tokens = _call_argv_tokens(node)
        strings = [token for token in tokens if token is not None]
        if not _strings_look_identity_requiring(strings):
            continue
        if tokens and tokens[0] == "git":
            continue  # that argv expression is rule A's finding
        if callee_name in allowed:
            routed.append(
                "%s:%d %s(...identity-requiring git argv...)"
                % (filename, node.lineno, callee_name)
            )
        else:
            violations.append(
                "%s:%d call to %r carries an identity-requiring git "
                "subcommand but does not resolve to _hermetic_git"
                % (filename, node.lineno, callee_name)
            )
    return _dedup(routed), _dedup(violations)


def scan_tests_directory(tests_dir=TESTS_DIR):
    routed = []
    violations = []
    scanned = []
    for filename in sorted(os.listdir(tests_dir)):
        if not filename.endswith(".py"):
            continue
        if filename == HELPER_MODULE + ".py":
            continue  # the chokepoint itself
        scanned.append(filename)
        module_routed, module_violations = scan_module(
            os.path.join(tests_dir, filename)
        )
        routed.extend(module_routed)
        violations.extend(module_violations)
    return scanned, routed, violations


class GitIdentityGuardTests(unittest.TestCase):
    """Anti-reintroduction guard over tests/*.py."""

    @classmethod
    def setUpClass(cls):
        cls.scanned, cls.routed, cls.violations = scan_tests_directory()

    def test_scan_actually_scanned_the_suite(self):
        self.assertGreaterEqual(
            len(self.scanned), 20,
            "the guard scanned almost nothing — discovery is broken",
        )
        self.assertNotIn(HELPER_MODULE + ".py", self.scanned)

    def test_no_identity_requiring_git_invocation_bypasses_the_helper(self):
        self.assertEqual(
            self.violations, [],
            "identity-requiring Git invocations must route through "
            "tests/_hermetic_git.py (explicit invocation-local identity; "
            "no ambient Git identity exists on CI):\n"
            + "\n".join(self.violations),
        )

    def test_discovery_is_anti_vacuous(self):
        legacy_routed = [
            entry for entry in self.routed
            if entry.split(":", 1)[0] != GUARD_FILE_NAME
        ]
        self.assertGreaterEqual(
            len(legacy_routed), EXPECTED_MINIMUM_IDENTITY_SITES,
            "the guard discovered fewer LEGACY identity-requiring sites "
            "(this file's own excluded) than the mechanical enumeration "
            "this floor was pinned against — the scanner regressed or "
            "sites were removed; re-derive the floor deliberately, do "
            "not weaken it:\n" + "\n".join(legacy_routed),
        )
        files_with_sites = {
            entry.split(":", 1)[0] for entry in legacy_routed
        }
        self.assertTrue(
            EXPECTED_FILES_WITH_IDENTITY_SITES <= files_with_sites,
            "expected identity-requiring sites in %s but the scan found "
            "them only in %s" % (
                sorted(EXPECTED_FILES_WITH_IDENTITY_SITES),
                sorted(files_with_sites),
            ),
        )


# ---------------------------------------------------------------------
# Executed identity sweep — the shape-INDEPENDENT guarantee.
#
# A transparent git shim first on PATH logs cwd + the full argv of
# every git process THAT RESOLVES `git` THROUGH THE INHERITED PATH
# while the identity-relevant test modules run — test-side,
# production-side, and hook-invoked alike, but NOT a process invoked by
# absolute binary path (statically refused by rule F) nor a grandchild
# whose environment strips the shim from PATH (disclosed residual; the
# child-side which-checks below cover the swept process itself).  The
# sweep asserts that every OBSERVED identity-requiring invocation
# carries explicit `-c user.name=` AND `-c user.email=`.  Within its
# field of view this does not care how the argv was constructed in
# source, so it catches every dynamically built shape the AST scan can
# never see (constant fragments, f-strings, .split(), file-derived
# argv, ...) — in the modules it sweeps.

# Anti-vacuity floor for the sweep: the round-4 measured run observed
# 526 identity-requiring git invocations across the six swept modules
# (evidence 180 / mitiq_narrative 2 / observe 30 / push_gate 17 /
# static 3 / target_runtime 294; the reviewer's independent full-suite
# shim enumeration also measured 526).  The floor sits at ~86% of
# measured so ordinary test edits don't trip it; note the count is
# CONCENTRATED (target_runtime alone is 294), so the floor catches any
# single module going quiet but not a partial within-module drop — the
# per-module > 0 checks and this comment carry that caveat.  A sweep
# observing fewer has lost real coverage — re-derive deliberately,
# never weaken in passing.
EXECUTED_IDENTITY_FLOOR = 450
_FIELD_SEP = "\x1f"

_SHIM_TEMPLATE = """#!/bin/sh
{{ printf '%s' "$PWD"; for a in "$@"; do printf '\\037%s' "$a"; done; \
printf '\\n'; }} >> "{log}"
exec "{real_git}" "$@"
"""

# Runs one swept module in a child interpreter, asserting on entry AND
# after the tests ran that `git` still resolves to the shim — a swept
# process that loses the shim from its own PATH exits 97/98 instead of
# silently leaving the sweep's field of view.
_CHILD_RUNNER = """
import os, runpy, shutil, sys
shim, module = sys.argv[1], sys.argv[2]
if shutil.which("git") != shim:
    sys.stderr.write("sweep child: git does not resolve to the shim "
                     "on entry\\n")
    sys.exit(97)
sys.path.insert(0, os.path.dirname(module))
import test_hermetic_git as _sweep_runner_module
_sweep_runner_module.mark_sweep_child()
sys.argv = [module]
code = 0
try:
    runpy.run_path(module, run_name="__main__")
except SystemExit as exc:
    code = exc.code
    if code is None:
        code = 0
    elif isinstance(code, bool):
        code = int(code)
    elif not isinstance(code, int):
        code = 1
if shutil.which("git") != shim:
    sys.stderr.write("sweep child: the swept module removed the shim "
                     "from PATH — sweep observation is incomplete\\n")
    sys.exit(98)
sys.exit(code)
"""


# How a swept child knows it is one. Round-01 finding B2 and round-02
# finding B.1: an ENVIRONMENT VARIABLE — even one carrying a nonce
# checked against a second variable naming a file — is assertable by
# anyone who can set the environment, because both inputs come from
# that party and the verifier holds no secret of its own. The reviewer forged it
# with two variables and a file it wrote itself.
#
# The control is therefore INVERTED. The runner does not describe the
# child to itself through the environment; it REACHES INTO the child
# and sets an in-process module attribute, using `_CHILD_RUNNER` —
# code this runner controls, executing inside that child. No ambient
# environment can set a module attribute, so R-6's stated property
# ("does not be asserted by anyone other than the sweep runner") holds
# by construction rather than by proxy.
_SWEEP_CHILD_ACTIVE = False


def mark_sweep_child():
    """Called by `_CHILD_RUNNER`, inside a child this runner launched.

    Scope of the guarantee: within this mechanism no environment
    variable, file or argument is consulted, so within it nothing the
    ambient environment supplies is read — the property rounds 01 and
    02 were bypassed on.

    Outside that boundary, and disclosed: this is an in-process flag,
    so code already executing in the process can set it. Injecting
    such code (for example `PYTHONPATH` plus a `sitecustomize.py`)
    reaches it, and is deliberately out of scope — that requires
    arbitrary code execution inside the test process, which is a
    different thing from the accidental leakage this closes.
    """
    global _SWEEP_CHILD_ACTIVE
    _SWEEP_CHILD_ACTIVE = True


def sweep_child_active():
    """True only inside a child `_CHILD_RUNNER` marked."""
    return _SWEEP_CHILD_ACTIVE


def _argv_has_explicit_identity(argv):
    values = {
        argv[index + 1]
        for index, token in enumerate(argv[:-1]) if token == "-c"
    }
    return (any(v.startswith("user.name=") for v in values)
            and any(v.startswith("user.email=") for v in values))


def sweep_module_set():
    """The modules the executed sweep covers:
    (1) every tests module in which the AST scan finds routed
        identity-requiring sites (a new module growing statically
        visible identity sites is swept automatically);
    (2) every tests module that DIRECTLY imports a name from a module
        in (1) — one level, module-level or nested — because such a
        module can reach git through an imported helper with no
        identity verb at its own call sites (test_mitiq_narrative
        reaches git via make_git_repo from test_target_runtime; found
        as reviewer finding R3-B2).  A DEEPER import chain is not
        followed — stated in the docstring residuals;
    (3) the pinned known five;
    minus this guard file itself."""
    scanned, routed, _ = scan_tests_directory()
    from_scan = {
        entry.split(":", 1)[0] for entry in routed
    } - {GUARD_FILE_NAME}
    swept = from_scan | set(EXPECTED_FILES_WITH_IDENTITY_SITES)
    routed_stems = {name[:-3] for name in from_scan}
    for filename in scanned:
        if filename in swept or filename == GUARD_FILE_NAME:
            continue
        with open(os.path.join(TESTS_DIR, filename)) as handle:
            tree = ast.parse(handle.read(), filename=filename)
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom)
                    and node.module in routed_stems):
                swept.add(filename)
                break
            if isinstance(node, ast.Import) and any(
                    alias.name in routed_stems for alias in node.names):
                swept.add(filename)
                break
    return sorted(swept)


class ExecutedIdentitySweepTests(unittest.TestCase):
    """Runs the identity-relevant modules under a logging git shim and
    asserts on the EXECUTED argv of every identity-requiring git
    process.  This is the primary hermeticity guarantee; the AST guard
    above is the fast structural feedback in front of it."""

    maxDiff = None

    def test_every_executed_identity_invocation_carries_identity(self):
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git, "no git binary on PATH")
        modules = sweep_module_set()
        self.assertTrue(
            set(EXPECTED_FILES_WITH_IDENTITY_SITES) <= set(modules)
        )
        repo_root = os.path.dirname(TESTS_DIR)
        started = time.monotonic()
        observed_identity = {}
        offenders = []
        with tempfile.TemporaryDirectory() as base:
            shim_dir = os.path.join(base, "bin")
            os.makedirs(shim_dir)
            for module in modules:
                log_path = os.path.join(base, module + ".log")
                shim_path = os.path.join(shim_dir, "git")
                with open(shim_path, "w") as handle:
                    handle.write(_SHIM_TEMPLATE.format(
                        log=log_path, real_git=real_git,
                    ))
                os.chmod(shim_path, 0o755)
                env = dict(os.environ)
                env["PATH"] = shim_dir + os.pathsep + env.get("PATH", "")
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                # NOTE: the swept-child signal is NOT passed through
                # `env`. It is set in-process by `_CHILD_RUNNER` (see
                # `mark_sweep_child`), because an environment variable
                # is assertable by anyone who can set it.
                env["PYTHONPATH"] = repo_root
                completed = subprocess.run(
                    [sys.executable, "-c", _CHILD_RUNNER, shim_path,
                     os.path.join(TESTS_DIR, module)],
                    env=env, capture_output=True, text=True,
                    cwd=repo_root,
                )
                self.assertEqual(
                    completed.returncode, 0,
                    "swept module %s did not pass under the logging "
                    "shim (exit 97/98 = the child-side which-check: "
                    "git stopped resolving to the shim) — sweep "
                    "results would be unreliable:\n%s"
                    % (module, completed.stdout[-2000:]
                       + completed.stderr[-2000:]),
                )
                self.assertTrue(
                    os.path.exists(log_path),
                    "shim log for %s was never created — the shim did "
                    "not observe a single git process; the sweep has "
                    "no observation to assert on" % module,
                )
                count = 0
                with open(log_path) as handle:
                    for line in handle:
                        fields = line.rstrip("\n").split(_FIELD_SEP)
                        argv = fields[1:]
                        if _classify_argv_tail(argv) != "identity":
                            continue
                        count += 1
                        if not _argv_has_explicit_identity(argv):
                            offenders.append(
                                "%s: git %s (cwd %s)"
                                % (module, " ".join(argv), fields[0])
                            )
                observed_identity[module] = count
        elapsed = time.monotonic() - started
        self.assertEqual(
            offenders, [],
            "EXECUTED identity-requiring git invocations without "
            "explicit -c user.name/-c user.email on the argv. However "
            "the argv was constructed this is a break of this suite's "
            "identity POLICY (identity rides the argv, via "
            "_hermetic_git); an invocation that is hermetic some other "
            "way (e.g. GIT_AUTHOR_*/GIT_COMMITTER_* env) still violates "
            "the policy and must route through the helper:\n"
            + "\n".join(offenders),
        )
        total = sum(observed_identity.values())
        self.assertGreaterEqual(
            total, EXECUTED_IDENTITY_FLOOR,
            "the sweep observed far fewer identity-requiring "
            "invocations (%d, %.1fs) than the pinned floor — the shim "
            "or the sweep lost coverage; re-derive deliberately: %r"
            % (total, elapsed, observed_identity),
        )
        for module in EXPECTED_FILES_WITH_IDENTITY_SITES:
            self.assertGreater(
                observed_identity.get(module, 0), 0,
                "swept module %s produced ZERO observed "
                "identity-requiring invocations — vacuous for that "
                "module: %r" % (module, observed_identity),
            )


if __name__ == "__main__":
    unittest.main(verbosity=1)
