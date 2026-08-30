"""I2: herdctl.py must not carry hand-copied package logic.

TWO LAYERS, and which one is load-bearing is stated rather than
implied.

`CliPackageParityTests` is the EXECUTED guarantee: it drives the CLI
command and the package function over the same fixture and asserts
they leave the same durable state, so the CLI having "one
implementation behind it" is a fact about what runs.

`DuplicationCensusTests` is fast structural feedback in front of that
executed guarantee. It reads SOURCE, so within it neither path's
behaviour is observable; it exists to fail the moment a NEW copy of a
package function is
added to `herdctl.py`, which is the re-divergence that produced the
`bootstrap_text` NameError.
"""

import ast
import difflib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))

from herdr import tasks
from herdr import lifecycle
from herdr.instance import HerdrInstance

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_PATH = os.path.join(REPO_ROOT, "herdctl.py")
PACKAGE_DIR = os.path.join(REPO_ROOT, "herdr")

#: Similarity alone, at a threshold tuned to obvious copies, MISSES a
#: drifted one. `clear_contexts_internal` scored 0.66 against
#: `clear_contexts`, so a 0.70 cut would have passed the one copy this
#: increment exists to remove. The census therefore takes three
#: independent channels and accepts a hit from one or more of them;
#: `test_similarity_alone_would_have_missed_the_known_copy` runs that
#: comparison against the pre-collapse file and proves it.
SIMILARITY_ONLY_THRESHOLD = 0.70
SIMILARITY_HIT = 0.60
NAME_MATCH_SIMILARITY = 0.25
NAME_MATCH_JACCARD = 0.30
CALL_JACCARD_HIT = 0.55
CALL_SHARED_MINIMUM = 4
#: A copy that was RENAMED has no name evidence and may share few
#: calls, so a fourth channel reports a strong body match on its own.
#: lead1's independent enumeration found four such pairs this census
#: missed (`archive_task`, `cfg`, `load_mission`, `save_mission`), and
#: they are the reason this channel exists.
RENAMED_COPY_SIMILARITY = 0.72
MINIMUM_BODY_TOKENS = 12

#: The duplication surface that ALREADY EXISTED when I2 started,
#: derived by the census below and frozen here. Its purpose is to make
#: a NEW copy fail while recording the old ones honestly rather than
#: hiding them behind a passing test.
#:
#: This list may SHRINK, deliberately: removing a duplicate from
#: `herdctl.py` without removing its name here fails
#: `test_the_baseline_has_no_stale_entries`. It may not grow silently.
#: `clear_contexts_internal` is absent because I2 removed it, and the
#: five R-10 guards are absent because I2b collapsed them onto
#: `herdr.guards` — together that took the census from 38 entries at
#: HEAD to 32. Their absence from THIS SET is what makes a fresh hand
#: copy of one of them FAIL `test_no_new_duplicate_may_be_added`, which
#: `GuardCollapseBiteTests` proves by execution rather than by
#: assertion. The reach-around it does not close: an author who adds the
#: name back to this set, or who copies a body the census's four
#: channels do not match, is not stopped here —
#: `test_the_baseline_has_no_stale_entries` and the census's own
#: channel tests are what make those visible.
KNOWN_DUPLICATES_AT_I2_START = frozenset({
    # Both are DEAD in the CLI: defined here, called only from
    # `herdr/guards.py`. Excused by name because they predate this
    # work. The exemption is worth revisiting, because the reachable
    # versions live in the package: drift in these two copies would
    # surface late, if at all. Named
    # as a deferred input in
    # `.herd/state/exec1-I2b-deferred-dead-cli-copies.md` (I2b round
    # 01, follow-up 8.3). Out of I2b's scope; do not remove them here.
    "_consume_push_approval_on_transfer",
    "_install_pre_push_hook",
    "aname",
    "approval_path",
    "approval_valid",
    "effective_policy",
    "ensure_local_herd_exclude",
    "gitout",
    "guard_precommit_cmd",
    "heartbeat",
    "human_duration",
    "install_git_guard",
    "load_mission",
    "load_task",
    "local_exclude_path",
    "prefix",
    "push_approval_path",
    "push_approval_valid",
    "push_identity",
    "register_repo",
    "registry_load",
    "registry_save",
    "repo_identity",
    "role_type_for_logical",
    "save_mission",
    "save_state",
    "save_task",
    "send_runtime_reset",
    "set_nested_policy_value",
    "simple_git_commit",
    "simple_git_push",
    "task_path",
})


#: Candidates the census SAW and DECLINED, with the reason. This is
#: deliberately NOT part of `KNOWN_DUPLICATES_AT_I2_START`, and the
#: distinction is load-bearing in a way worth stating: the guard
#: computes `added = found - KNOWN`, so ABSENCE from that set is the
#: STRICT direction and PRESENCE is the exemption. Listing a declined
#: candidate as "known" would SUPPRESS a future finding at that site
#: rather than protect against one.
#:
#: Recording them here keeps the judgement reviewable without turning
#: it into an exemption — a census whose declined candidates are
#: invisible is the "presents itself as exhaustive but is not" class.
JUDGED_AND_EXCLUDED = {
    "cfg": (
        "A CLI-boundary layering difference, not a hand copy."
        " `herdctl.cfg` uses `p.exists()` + `SystemExit`;"
        " `HerdrInstance.load_config` uses `self.initialized` +"
        " `RuntimeError`. Supervisor ruling R-11 decided this exact"
        " shape for `push_identity`: the library raises and the CLI"
        " translates at its own boundary. At 42 body tokens it is"
        " well above MINIMUM_BODY_TOKENS, so the census saw it and"
        " declined it on all three channels rather than missing it."
    ),
    "archive_task": (
        "Same idiom, different responsibility."
        " `herdctl.archive_task` writes into an archive keyed by task"
        " id; `tasks.save_task` writes the current task file. lead1"
        " raised it, then retracted it, and reviewer1 agreed it is a"
        " false positive."
    ),
}


def excluded_candidates_that_leaked(excluded, known):
    """Declined candidates that ALSO appear in the exemption set.

    Extracted so the decision can be driven with synthetic inputs:
    asserting it only against the live lists left round-02 mutant N10
    alive, because healthy lists trip neither branch.
    """
    return sorted(name for name in excluded if name in known)


def _functions(source, module):
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
    return found


def _body_tokens(node):
    """Structure and identifier tokens. String constants are dropped so
    a reworded message does not read as a rewritten algorithm."""
    tokens = []
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name):
            tokens.append("N:" + inner.id)
        elif isinstance(inner, ast.Attribute):
            tokens.append("A:" + inner.attr)
        elif isinstance(inner, ast.Constant) and isinstance(
            inner.value, str
        ):
            continue
        else:
            tokens.append(type(inner).__name__)
    return tokens


def _calls(node):
    names = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            name = getattr(func, "attr", getattr(func, "id", None))
            if name:
                names.add(name)
    return names


def _normalised(name):
    for suffix in ("_internal", "_cmd", "_impl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip("_")


def package_functions():
    found = {}
    for entry in sorted(os.listdir(PACKAGE_DIR)):
        if not entry.endswith(".py"):
            continue
        path = os.path.join(PACKAGE_DIR, entry)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for name, node in _functions(source, entry).items():
            found.setdefault(name, []).append(("herdr/" + entry, node))
    return found


def census(cli_source, similarity_only=False):
    """Every herdctl function that duplicates a package function.

    Three independent channels — similarity, NAME evidence, and shared
    call-graph evidence — because each one alone has a blind spot of
    its own. With `similarity_only` the census runs on the similarity
    channel at `SIMILARITY_ONLY_THRESHOLD`, which is how the
    calibration test demonstrates that channel's blind spot rather
    than asserting it.
    """
    cli = _functions(cli_source, "herdctl.py")
    package = package_functions()
    hits = {}
    for cli_name, cli_node in cli.items():
        cli_tokens = _body_tokens(cli_node)
        if len(cli_tokens) < MINIMUM_BODY_TOKENS:
            continue
        cli_calls = _calls(cli_node)
        for pkg_name, entries in package.items():
            for module, pkg_node in entries:
                pkg_tokens = _body_tokens(pkg_node)
                if len(pkg_tokens) < MINIMUM_BODY_TOKENS:
                    continue
                similarity = difflib.SequenceMatcher(
                    None, cli_tokens, pkg_tokens
                ).ratio()
                if similarity_only:
                    flagged = similarity >= SIMILARITY_ONLY_THRESHOLD
                else:
                    pkg_calls = _calls(pkg_node)
                    shared = cli_calls & pkg_calls
                    union = cli_calls | pkg_calls
                    jaccard = len(shared) / len(union) if union else 0.0
                    name_match = _normalised(cli_name) == _normalised(
                        pkg_name
                    )
                    flagged = (
                        similarity >= SIMILARITY_HIT
                        or (name_match and (
                            similarity >= NAME_MATCH_SIMILARITY
                            or jaccard >= NAME_MATCH_JACCARD
                        ))
                        or (jaccard >= CALL_JACCARD_HIT
                            and len(shared) >= CALL_SHARED_MINIMUM)
                    )
                    if flagged and not name_match:
                        # Without name evidence, only a STRONG body
                        # match reports — that is the renamed-copy
                        # channel. A weak similarity with no name and
                        # no call overlap is two functions that merely
                        # look alike, and is not reported.
                        flagged = similarity >= RENAMED_COPY_SIMILARITY
                if flagged:
                    previous = hits.get(cli_name)
                    # Attribution prefers a NAME-matching counterpart
                    # over a merely-more-similar one. Without this, a
                    # newly added package module can become the
                    # reported source of an unrelated copy —
                    # `herdr/identity.py`, added by this increment,
                    # briefly displaced `herdr/registry.py` for three
                    # rows.
                    name_match_now = _normalised(cli_name) == (
                        _normalised(pkg_name)
                    )
                    if previous is None:
                        better = True
                    elif name_match_now and not previous[3]:
                        better = True
                    elif previous[3] and not name_match_now:
                        better = False
                    else:
                        better = similarity > previous[1]
                    if better:
                        hits[cli_name] = (pkg_name, similarity,
                                          module, name_match_now)
    return {name: value[:3] for name, value in hits.items()}


def current_cli_source():
    with open(CLI_PATH, encoding="utf-8") as handle:
        return handle.read()


def head_cli_source():
    """`herdctl.py` as of HEAD — the pre-collapse file, which still
    contains `clear_contexts_internal`. Used only by the calibration
    test, so the method is proven against the copy we know about."""
    result = subprocess.run(
        ["git", "show", "HEAD:herdctl.py"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return result.stdout


class DuplicationCensusTests(unittest.TestCase):
    """Fast structural feedback in front of `CliPackageParityTests`,
    which is the executed guarantee.

    This class reads SOURCE, so within it neither code path's
    behaviour is observable; it fails when a NEW hand copy of a package function appears in
    `herdctl.py`, which is the re-divergence that produced the
    `bootstrap_text` NameError.
    """

    def test_the_census_finds_the_known_surface(self):
        hits = census(current_cli_source())
        self.assertGreater(
            len(hits), 10,
            "the census found almost nothing — the detector is"
            " broken, so a clean result proves little",
        )

    def test_no_new_duplicate_may_be_added(self):
        found = set(census(current_cli_source()))
        added = sorted(found - KNOWN_DUPLICATES_AT_I2_START)
        self.assertEqual(
            added, [],
            "herdctl.py gained %d hand-copied package function(s):"
            " %s. Call the package implementation instead — that"
            " duplication is what produced the bootstrap_text"
            " NameError." % (len(added), added),
        )

    def test_the_baseline_has_no_stale_entries(self):
        found = set(census(current_cli_source()))
        stale = sorted(KNOWN_DUPLICATES_AT_I2_START - found)
        self.assertEqual(
            stale, [],
            "these names are in the frozen baseline but are no longer"
            " duplicated: %s. Remove them from"
            " KNOWN_DUPLICATES_AT_I2_START so the list keeps"
            " shrinking deliberately." % stale,
        )

    def test_the_excluded_candidates_are_recorded_and_not_exempted(self):
        """D.3: the judgement is visible, and it is NOT an exemption.

        Source is the only feasible level here, and the reason is that
        the subject IS a bookkeeping property of two source-derived
        lists: whether a declined candidate was recorded, and whether
        it leaked into the set that suppresses findings.
        """
        self.assertTrue(JUDGED_AND_EXCLUDED)
        for name, reason in JUDGED_AND_EXCLUDED.items():
            with self.subTest(name=name):
                self.assertGreater(
                    len(reason), 60,
                    "a declined candidate needs a reason a reviewer"
                    " can weigh, not a label",
                )
        leaked = excluded_candidates_that_leaked(
            JUDGED_AND_EXCLUDED, KNOWN_DUPLICATES_AT_I2_START
        )
        self.assertEqual(
            leaked, [],
            "%s is recorded as judged-and-excluded AND as a known"
            " duplicate; the second suppresses a future finding at"
            " that site, which is the opposite of what recording the"
            " judgement is for" % leaked,
        )

    def test_the_leak_check_is_not_vacuous(self):
        """EXECUTED PIN on the decision itself, driven with synthetic
        lists. Round-02 mutant N10 neutered the live assertion and
        survived, because the real lists are disjoint and so trip
        neither branch."""
        self.assertEqual(
            excluded_candidates_that_leaked({"a": "r"}, {"a"}), ["a"],
            "a name in BOTH lists was not detected",
        )
        self.assertEqual(
            excluded_candidates_that_leaked({"a": "r"}, {"b"}), [],
            "a disjoint pair was reported as leaking",
        )
        self.assertEqual(
            excluded_candidates_that_leaked({}, {"a"}), [])

    def test_clear_contexts_is_no_longer_duplicated(self):
        found = census(current_cli_source())
        self.assertNotIn("clear_contexts_internal", found)
        self.assertNotIn(
            "clear_contexts_internal", current_cli_source(),
            "the CLI duplicate of clear_contexts is back",
        )

    def test_bootstrap_text_is_not_called_unimported_in_the_cli(self):
        """Fast structural feedback in front of
        `CliPackageParityTests`, which is the executed guarantee.
        This reads SOURCE, so within it the command's runtime
        behaviour is not observable. It exists because the NameError
        it looks for is a STATIC defect — a call to a name the file
        neither imports nor defines — and a static defect is cheapest
        to catch statically."""
        source = current_cli_source()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported.add(alias.asname or alias.name)
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(
                node.func, ast.Name
            ):
                called.add(node.func.id)
        defined = set(_functions(source, "herdctl.py"))
        builtins_and_locals = defined | imported | set(dir(__builtins__))
        unresolved = sorted(
            name for name in called
            if name not in builtins_and_locals
            and not hasattr(__builtins__, name)
        )
        self.assertNotIn(
            "bootstrap_text", unresolved,
            "herdctl.py calls bootstrap_text without importing or"
            " defining it — the exact NameError shape",
        )


#: The five functions I2b collapsed onto `herdr.guards`.
COLLAPSED_GUARDS = (
    "guard_pretool",
    "guard_precommit",
    "guard_reference_transaction",
    "guard_prepush",
    "_install_one_git_hook",
)


def source_with_a_fresh_hand_copy(name):
    """The CURRENT `herdctl.py` with `name`'s wrapper replaced by
    HEAD's hand-copied body — a re-divergence, synthesised in memory.

    This function builds the specimen in memory and throws it away,
    because the census takes source text and so has no need to write
    one out. That is a statement about this function, not about the
    module: the callers below read `herdctl.py` and run `git show`,
    both of which touch the working tree and the object store as
    readers.
    """
    import ast
    head = head_cli_source()
    current = current_cli_source()
    head_tree = ast.parse(head)
    current_tree = ast.parse(current)

    def segment(tree, text):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(text, node)
        return None

    copied = segment(head_tree, head)
    wrapper = segment(current_tree, current)
    if copied is None or wrapper is None:
        return None
    return current.replace(wrapper, copied, 1)


class GuardCollapseBiteTests(unittest.TestCase):
    """I2b: the parity guard's extension to the guards BITES.

    An extension whose only evidence is its own presence leaves open
    whether it fires, which is the failure mode this class exists to
    rule out. Each test here rebuilds a real re-divergence — HEAD's hand-copied body pasted back
    over the collapsed wrapper — and asserts the census reports it and
    that the baseline no longer excuses it.

    Source is the only feasible level for this class, and the reason is
    that its subject IS source: what a duplication census reports about
    a body of text has no runtime behaviour of its own. The behavioural
    guarantee it fronts is
    `tests/test_guard_collapse.py::BeforeAndAfterTests`.
    """

    def test_each_collapsed_guard_is_absent_from_the_baseline(self):
        for name in COLLAPSED_GUARDS:
            with self.subTest(name=name):
                self.assertNotIn(
                    name, KNOWN_DUPLICATES_AT_I2_START,
                    "%s is still excused by the baseline, so a fresh"
                    " copy of it would be permitted" % name,
                )

    def test_a_fresh_hand_copy_of_a_collapsed_guard_is_reported(self):
        for name in COLLAPSED_GUARDS:
            with self.subTest(name=name):
                specimen = source_with_a_fresh_hand_copy(name)
                if specimen is None:
                    self.skipTest(
                        "%s is missing from HEAD or from the current"
                        " file; the specimen cannot be built" % name
                    )
                self.assertNotEqual(
                    specimen, current_cli_source(),
                    "the specimen is identical to the current file, so"
                    " it is not a re-divergence at all",
                )
                found = census(specimen)
                self.assertIn(
                    name, found,
                    "the census does not report a fresh hand copy of"
                    " %s; the extension does not bite" % name,
                )

    def test_a_fresh_hand_copy_would_FAIL_the_guard(self):
        """The guard's own arithmetic, driven with the specimen:
        `added = found - KNOWN` must be non-empty."""
        for name in COLLAPSED_GUARDS:
            with self.subTest(name=name):
                specimen = source_with_a_fresh_hand_copy(name)
                if specimen is None:
                    self.skipTest("specimen unavailable for %s" % name)
                added = set(census(specimen)) - (
                    KNOWN_DUPLICATES_AT_I2_START
                )
                self.assertIn(
                    name, added,
                    "re-copying %s would pass"
                    " test_no_new_duplicate_may_be_added" % name,
                )

    def test_the_current_file_itself_adds_nothing(self):
        """Anti-vacuity for the three above: the specimens fail only
        because they are specimens, not because the guard fails on
        everything."""
        added = set(census(current_cli_source())) - (
            KNOWN_DUPLICATES_AT_I2_START
        )
        self.assertEqual(added, set())


class CensusCalibrationTests(unittest.TestCase):
    """The census's own calibration, proven rather than asserted.

    Source is the only feasible level here, and the reason is that the
    subject IS a source-analysis method: what is being checked is
    which functions a detector reports, which has no runtime
    behaviour of its own.
    """

    def test_the_census_catches_the_known_drifted_copy(self):
        source = head_cli_source()
        if "clear_contexts_internal" not in source:
            self.skipTest(
                "HEAD's herdctl.py no longer contains the specimen"
            )
        hits = census(source)
        self.assertIn(
            "clear_contexts_internal", hits,
            "the census misses the one copy this increment exists to"
            " remove; a method calibrated to miss it is calibrated to"
            " miss the class",
        )

    def test_similarity_alone_would_have_missed_the_known_copy(self):
        source = head_cli_source()
        if "clear_contexts_internal" not in source:
            self.skipTest("HEAD's herdctl.py lacks the specimen")
        similarity_hits = census(source, similarity_only=True)
        self.assertNotIn(
            "clear_contexts_internal", similarity_hits,
            "the similarity-only channel at %.2f now catches the"
            " specimen; the calibration warning this guard is built"
            " around no longer holds and the thresholds need"
            " re-deriving" % SIMILARITY_ONLY_THRESHOLD,
        )


class CliPackageParityTests(unittest.TestCase):
    """THE EXECUTED GUARANTEE: the CLI command and the package
    function leave the same durable state.

    Scope: the state `clear_contexts` writes, over one fixture, with
    the runtime seams injected. Outside it, and disclosed: this does
    not compare the two paths' console output, and it does not run the
    real `herdr` binary.
    """

    def make_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        herd = repo / ".herd"
        (herd / "state").mkdir(parents=True)
        (herd / "roles").mkdir(parents=True)
        for filename in ("supervisor.md", "lead.md", "executor.md",
                         "reviewer.md"):
            (herd / "roles" / filename).write_text("ROLE: " + filename)
        (herd / "herd.config.json").write_text(json.dumps({
            "version": 4,
            "project": {"name": "parity"},
            "orchestration": {"agent_task_timeout_ms": 1000},
            "roles": {
                "supervisor": {"kind": "claude"},
                "lead": {"kind": "claude"},
                "executor": {"kind": "claude"},
                "reviewer": {"kind": "claude"},
            },
            "context": {"reset_commands": {"claude": "/clear"}},
            "policy": {"rules": [], "git": {}},
        }))
        (herd / "state" / "runtime.json").write_text(json.dumps({
            "agents": {"supervisor": "sup1", "lead1": "lead1"},
            "panes": {},
        }))
        (herd / "state" / "task.json").write_text(
            json.dumps({"status": "IDLE"})
        )
        # R-53 AQ-2: a reset runs against BOUND roles, and within
        # this fixture the bindings come from the PRODUCTION producer
        # rather than a literal document — a fixture that builds the
        # artifact under test is unable to notice a missing producer.
        lifecycle.establish_role_bindings(
            HerdrInstance(repo),
            {"supervisor": "sup1", "lead1": "lead1"},
            prober=lambda agent: {
                "status": "idle",
                "raw": {"result": {"agent": {
                    "name": "sup1", "cwd": "/x",
                    "workspace_id": "w1", "pane_id": "w1:p1",
                    "agent_session": {"value": "s-1"},
                }}},
            },
            settle_seconds=0.0, sleeper=lambda _seconds: None,
        )
        return repo

    def run_both(self):
        states = []
        for use_cli in (False, True):
            repo = self.make_repo()
            with patch("herdr.tasks.agent_info") as info, \
                 patch("herdr.tasks.send_runtime_reset") as reset, \
                 patch("herdr.tasks.prompt") as prompt_fn:
                info.return_value = {
                    "status": "idle",
                    "raw": {"result": {"agent": {
                        "name": "sup1", "cwd": "/x",
                        "workspace_id": "w1", "pane_id": "w1:p1",
                        "agent_session": {"value": "s-1"},
                    }}},
                }
                reset.return_value = SimpleNamespace(
                    returncode=0, stdout="", stderr="")
                prompt_fn.return_value = SimpleNamespace(
                    returncode=0, stdout="", stderr="")
                if use_cli:
                    import herdctl
                    with patch.object(
                        herdctl, "resolve_repo_ref", lambda ref: repo
                    ):
                        herdctl.clear_contexts_cmd(
                            SimpleNamespace(repo=str(repo))
                        )
                else:
                    tasks.clear_contexts(HerdrInstance(repo))
            states.append(json.loads(
                (repo / ".herd" / "state"
                 / tasks.RESET_STATE_FILE).read_text()
            ))
        return states

    def test_both_paths_leave_the_same_durable_state(self):
        package_state, cli_state = self.run_both()
        self.assertEqual(
            package_state, cli_state,
            "the CLI command and the package function disagree on the"
            " durable state they leave",
        )

    def test_the_shared_path_actually_ran(self):
        package_state, _ = self.run_both()
        self.assertTrue(
            package_state["roles"],
            "no role was processed; the parity comparison above would"
            " be comparing two empty documents",
        )
        for logical, entry in package_state["roles"].items():
            with self.subTest(logical=logical):
                self.assertEqual(entry["phase"],
                                 tasks.RESET_PHASE_RESEEDED)


if __name__ == "__main__":
    unittest.main()
