"""P1-A6 Verified PR Delivery: authority, candidate identity, receipts,
the state machine over REAL git in temporary repositories, crash
reconciliation, revocation, base drift, and the durable status
projection.

Hermetic: temporary directories only, a local bare repository standing
in for the remote through ``url.<bare>.insteadOf`` (so the record can
bind the canonical GitHub URL grammar while every transfer stays local),
HOME redirected to a temporary home for the protected store, and a
transport whose ``gh`` half is structurally replaced — ``_gh`` raises,
so no ``gh`` process can ever start from this module (the module itself
imports no subprocess, socket, urllib, or http). The git half is the real
production transport, so every commit, push, fetch, read-tree, and
update-ref here is the production argv running through the installed
Herdr hooks and the receipt path.

Standalone: PYTHONPATH=$PWD python3 tests/test_pr_delivery.py
"""

import atexit
import copy
import hashlib
import inspect
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hermetic_git import run_git, run_git_completed        # noqa: E402

from herdr import guards                                     # noqa: E402
from herdr import delivery_evidence                          # noqa: E402
from workflow_authority import authorization as mission_auth  # noqa: E402
from workflow_authority import record as wa_record           # noqa: E402
from workflow_authority.digest import (                      # noqa: E402
    text_digest,
)
from pr_delivery import authorization as auth                # noqa: E402
from pr_delivery import boundary as boundary_module          # noqa: E402
from pr_delivery import candidate as candidate_module        # noqa: E402
from pr_delivery import cli as cli_module                    # noqa: E402
from pr_delivery import machine as machine_module            # noqa: E402
from pr_delivery import pr_text                              # noqa: E402
from pr_delivery import receipts                             # noqa: E402
from pr_delivery import store as store_module                # noqa: E402
from pr_delivery import transport as transport_module        # noqa: E402

GITHUB_URL = "https://github.com/octo/repo.git"
REPO_URL = "https://github.com/octo/repo"
SOURCE_BRANCH = "feature/p1-a6"
BASE_BRANCH = "main"

# UPPERCASE step names on purpose: the hermetic-git AST guard classifies
# lowercase git subcommand words in call arguments; the package's own
# constants are what the tests pass around.
BASE_REFRESH = auth.STEP_BASE_REFRESH
COMMIT_STEP = auth.STEP_COMMIT
PUSH_STEP = auth.STEP_PUSH
PR_CREATE = auth.STEP_PR_CREATE


class Crash(Exception):
    """Injected crash: not a transport error, so the machine cannot
    catch it — exactly like a killed process."""


def git(*argv, cwd):
    """Hermetic git through the shared chokepoint; returns stdout."""
    return run_git("-C", str(cwd), *argv)


def git_rc(argv, cwd):
    return run_git_completed(["-C", str(cwd)] + list(argv), check=False)


class TestTransport(transport_module.DeliveryTransport):
    """Real git; structurally isolated GitHub half.

    ``_gh`` raises before any process could start, so no verb here can
    reach GitHub. The four ``gh_*`` verbs are answered from in-memory
    state that the test controls.
    """

    def __init__(self, repo_path):
        super(TestTransport, self).__init__()
        self.repo_path = str(repo_path)
        self.open_prs = []
        self.created = []
        self.check_runs = []
        self.check_runs_error = False
        self.create_error = False
        self.view_calls = 0
        self.next_number = 41

    def _gh(self, argv, stdin_bytes=None):
        raise AssertionError("gh must never run from a test: %r" % (argv,))

    def gh_check_runs(self, owner, repo, sha):
        if self.check_runs_error:
            raise transport_module.DeliveryTransportError("gh api unreachable")
        return list(self.check_runs)

    def gh_pr_list(self, owner, repo, head_branch, base_branch):
        # Every state, like the production verb (--state all).
        return [
            dict(item) for item in self.open_prs
            if item["headRefName"] == head_branch
            and item["baseRefName"] == base_branch
        ]

    def gh_pr_create(self, owner, repo, head_branch, base_branch, title,
                     body_text):
        if self.create_error:
            raise transport_module.DeliveryTransportError("gh pr create 502")
        head_oid = self.ls_remote(self.repo_path, "origin",
                                  "refs/heads/" + head_branch)
        number = self.next_number
        self.next_number += 1
        item = {
            "number": number,
            "url": "%s/pull/%d" % (REPO_URL, number),
            "headRefOid": head_oid,
            "headRefName": head_branch,
            "baseRefName": base_branch,
            "state": "OPEN",
        }
        self.open_prs.append(item)
        self.created.append((title, body_text))
        return item["url"]

    def gh_pr_view(self, owner, repo, number):
        self.view_calls += 1
        for item in self.open_prs:
            if item["number"] == number:
                return dict(item)
        raise transport_module.DeliveryTransportError("no such PR")


# One template per module (round-02 B1): the bare "remote" and the working
# repository with the staged candidate are built ONCE, and every case
# copies them. Copying two tiny repositories and rewriting the one
# path-bearing config entry costs milliseconds; building them costs
# twenty git processes. The default-argument authority is assembled once
# on the template by the real ceremony and path-patched per case; a case
# that passes overrides runs the real ceremony on its own copy.
_TEMPLATE = {}
_NOW = 1_800_000_000.0


def _template_args(base, **overrides):
    marker = base / "reverified.log"
    values = {
        "repo": str(base / "work"),
        "workflow_id": "wf-p1a6",
        "herd_evidence": str(base / "herd-evidence.json"),
        "verification_log": str(base / "verification.log"),
        "verification_command": "python3 -m nothing --serial",
        "verification_exit_status": 0,
        "verification_ran_at": 1_799_999_500.0,
        "reverify_command": " ".join(
            '"%s"' % item if " " in item else item
            for item in _reverify_argv(marker)
        ),
        "title": "P1-A6: verified PR delivery",
        "objective": "Deliver the reviewed candidate exactly once.",
        "architecture_notes": "One bounded state machine.",
        "nonblocking_risks": "None known.",
        "base_branch": BASE_BRANCH,
        "remote": "origin",
        "validity_seconds": 3600,
        "mission_workflow_id": None,
        "mission_authorization_digest": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _reverify_argv(marker):
    return [
        sys.executable, "-c",
        "import sys; open(sys.argv[1], 'a').write('ran\\n')",
        str(marker),
    ]


def _write_evidence_files(base):
    (base / "herd-evidence.json").write_text(json.dumps({
        "engineering_complete": {
            "task_id": "20260904-150441-159120",
            "status": "COMPLETE",
            "task_state_sha256": "a" * 64,
            "recorded_at": 1_799_999_000,
        },
        "reviewer_approve": {
            "task_id": "20260904-150441-159120",
            "round": 2,
            "review_file_name": "20260904-150441-159120-round-02.md",
            "review_file_sha256": "b" * 64,
            "decision": "APPROVE",
            "recorded_at": 1_799_999_000,
        },
    }))
    (base / "verification.log").write_bytes(b"suite: OK\n")


def _configure_repo(repo, bare):
    git("config", "user.name", "Delivery Human", cwd=repo)
    git("config", "user.email", "human@example.com", cwd=repo)
    git("config", "url.%s.insteadOf" % bare, GITHUB_URL, cwd=repo)


def _template():
    if _TEMPLATE:
        return _TEMPLATE
    temp = tempfile.TemporaryDirectory()
    atexit.register(temp.cleanup)
    base = Path(temp.name)
    bare = base / "remote.git"
    work = base / "work"
    run_git("init", "-q", "--bare", "-b", BASE_BRANCH, str(bare))
    run_git("init", "-q", "-b", BASE_BRANCH, str(work))
    _configure_repo(work, bare)
    git("remote", "add", "origin", GITHUB_URL, cwd=work)
    (work / "README.md").write_text("readme v1\n")
    (work / "assets" / "brand").mkdir(parents=True)
    (work / "assets" / "brand" / "banner.svg").write_text("<svg>1</svg>\n")
    (work / "keep.txt").write_text("keep v1\n")
    (work / "old.txt").write_text("to be deleted\n")
    (work / "tool.sh").write_text("#!/bin/sh\n")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "base", cwd=work)
    git("push", "-q", "origin", BASE_BRANCH + ":" + BASE_BRANCH, cwd=work)
    git("checkout", "-q", "-b", SOURCE_BRANCH, cwd=work)
    baseline = git("rev-parse", "HEAD", cwd=work)
    # The reviewed candidate: A, M, D, and a mode change.
    (work / "src").mkdir()
    (work / "src" / "pkg.py").write_text("print('new')\n")
    (work / "keep.txt").write_text("keep v2\n")
    (work / "old.txt").unlink()
    os.chmod(work / "tool.sh", 0o755)
    git("add", "-A", cwd=work)
    _write_evidence_files(base)
    transport = TestTransport(work)
    entries = candidate_module.parse_raw_z(
        transport.diff_index_raw(str(work), baseline)
    )
    digest = candidate_module.identity_digest(entries)
    authority = cli_module.assemble_authority(
        transport, _template_args(base), _NOW, "human",
        lambda prompt: digest[:cli_module.CONFIRMATION_CHARS],
        out=io.StringIO(),
    )
    _TEMPLATE.update({
        "base": base, "bare": bare, "work": work, "baseline": baseline,
        "authority_json": json.dumps(authority),
        "base_str": str(base), "base_real": os.path.realpath(str(base)),
    })
    return _TEMPLATE


class DeliveryFixture(object):
    """A bare 'remote', a working repository on a feature branch sitting
    on the base with a staged candidate, hooks on request, HOME redirected
    to a temporary protected store, and an independent clone (lazily) that
    can advance the base behind the delivery's back. Copied from the
    module template; see ``_template``."""

    def __init__(self, case, transport_class=TestTransport, hooks=False):
        """``hooks`` installs the Herdr git guards. Off by default (B1):
        the machine's own checks are what most cases exercise, and every
        hook is a Python process start. Cases that prove the receipt path
        THROUGH the hooks ask for them explicitly."""
        template = _template()
        self.case = case
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.home = base / "home"
        self.home.mkdir()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        case.addCleanup(self._restore_home)
        self.bare = base / "remote.git"
        self.work = base / "work"
        self.clone = base / "clone"
        shutil.copytree(str(template["bare"]), str(self.bare))
        shutil.copytree(str(template["work"]), str(self.work),
                        symlinks=True)
        git("config", "--unset", "url.%s.insteadOf" % template["bare"],
            cwd=self.work)
        git("config", "url.%s.insteadOf" % self.bare, GITHUB_URL,
            cwd=self.work)
        # The copy carries stale stat data in the index; refresh it so the
        # copy behaves exactly like the freshly built repository.
        git("update-index", "-q", "--refresh", cwd=self.work)
        self.baseline = template["baseline"]
        if hooks:
            guards.install_git_guard(self.work)
        self._clone_ready = False
        self.transport = transport_class(self.work)
        self.store = store_module.DeliveryStore(
            store_module.store_directory()
        )
        self.now = [_NOW]
        self.machine = machine_module.DeliveryMachine(
            self.store, self.transport, self.clock,
        )
        self.marker = base / "reverified.log"
        self.reverify_argv = _reverify_argv(self.marker)
        self.evidence_path = base / "herd-evidence.json"
        self.log_path = base / "verification.log"
        _write_evidence_files(base)

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def _configure(self, repo):
        _configure_repo(repo, self.bare)

    def clock(self):
        return self.now[0]

    def args(self, **overrides):
        return _template_args(Path(self.temp.name), **overrides)

    def _patched_authority(self):
        """The template's ceremony output with the template paths
        replaced by this case's paths (both the literal and the resolved
        spelling), so the record binds THIS copy."""
        template = _template()
        base = str(Path(self.temp.name))
        text = template["authority_json"]
        text = text.replace(template["base_real"], os.path.realpath(base))
        text = text.replace(template["base_str"], base)
        return json.loads(text)

    def live_digest(self):
        entries = candidate_module.parse_raw_z(
            self.transport.diff_index_raw(str(self.work), self.baseline)
        )
        return candidate_module.identity_digest(entries)

    def authorize(self, **overrides):
        if overrides:
            digest = self.live_digest()
            authority = cli_module.assemble_authority(
                self.transport, self.args(**overrides), self.clock(),
                "human",
                lambda prompt: digest[:cli_module.CONFIRMATION_CHARS],
                out=io.StringIO(),
            )
        else:
            authority = self._patched_authority()
        record = auth.new_authorization("prd-test", authority, self.clock())
        with self.store.lock():
            document = self.store.load()
            ok, problem, _ = store_module.add_delivery(document, record)
            assert ok, problem
            self.store.save(document)
        return record["delivery_id"]

    def record(self, delivery_id="prd-test"):
        return self.machine.load(delivery_id)

    def _ensure_clone(self):
        if not self._clone_ready:
            run_git("clone", "-q", str(self.bare), str(self.clone))
            self._configure(self.clone)
            self._clone_ready = True

    def advance_base(self, files, message="advance"):
        """Move the remote base forward from the independent clone."""
        self._ensure_clone()
        for relative, content in files.items():
            path = self.clone / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                path.unlink()
            else:
                path.write_text(content)
        git("add", "-A", cwd=self.clone)
        git("commit", "-qm", message, cwd=self.clone)
        git("push", "-q", "origin", "HEAD:" + BASE_BRANCH, cwd=self.clone)
        return git("rev-parse", "HEAD", cwd=self.clone)

    def remote_oid(self, ref):
        text = git("ls-remote", str(self.bare), ref, cwd=self.work)
        return text.split()[0] if text else None

    def head(self):
        return git("rev-parse", "HEAD", cwd=self.work)


# ---------------------------------------------------------------- authority


class AuthorizationRecordTests(unittest.TestCase):
    def setUp(self):
        self.fx = DeliveryFixture(self)
        self.fx.authorize()
        self.record = self.fx.record()

    def test_round_trip_through_the_store_validates(self):
        loaded = self.fx.store.load()["deliveries"]["prd-test"]
        auth.validate_authorization(loaded)
        self.assertEqual(loaded["phase"], auth.PHASE_AUTHORIZED)
        self.assertEqual(loaded["mode"], auth.MODE_PULL_REQUEST)
        self.assertEqual(loaded["remote"]["url_exact"], GITHUB_URL)
        self.assertEqual(loaded["remote"]["url_fetch"], str(self.fx.bare))
        self.assertEqual(loaded["remote"]["url_push"], str(self.fx.bare))
        self.assertEqual(loaded["repository"]["repository_url"], REPO_URL)
        self.assertEqual(loaded["source"]["branch"], SOURCE_BRANCH)
        self.assertEqual(loaded["original_baseline"]["commit_sha"],
                         self.fx.baseline)
        self.assertEqual(loaded["allowed_actions"], list(auth.STEPS))
        self.assertEqual(
            loaded["human_authorization"]["source"],
            auth.AUTHORIZATION_SOURCE_LOCAL_TERMINAL,
        )
        self.assertEqual(loaded["expiration"]["policy"],
                         auth.EXPIRATION_POLICY_ABSOLUTE)

    def test_every_authority_field_is_digest_bound(self):
        for key in auth.AUTHORITY_KEYS:
            tampered = copy.deepcopy(self.record)
            value = tampered[key]
            if isinstance(value, dict):
                inner = sorted(value)[0]
                value[inner] = "tampered" if not isinstance(
                    value[inner], (int, float)
                ) else 999
            elif isinstance(value, list):
                value.append(value[0] if value else "x")
            elif isinstance(value, int) and not isinstance(value, bool):
                tampered[key] = value + 1
            elif value is None:
                tampered[key] = "prd-other"
            else:
                tampered[key] = "tampered"
            with self.assertRaises(auth.AuthorizationError) as caught:
                auth.validate_authorization(tampered)
            self.assertIn(
                caught.exception.problem,
                (auth.PROBLEM_AUTHORITY_DIGEST, auth.PROBLEM_BAD_VALUE,
                 auth.PROBLEM_BAD_TYPE, auth.PROBLEM_MODE,
                 auth.PROBLEM_ALLOWED_ACTIONS, auth.PROBLEM_REMOTE_GRAMMAR,
                 auth.PROBLEM_REPOSITORY_IDENTITY, auth.PROBLEM_REF_GRAMMAR,
                 auth.PROBLEM_CANDIDATE_IDENTITY, auth.PROBLEM_EVIDENCE,
                 auth.PROBLEM_SCHEMA_VERSION, auth.PROBLEM_TOO_LARGE,
                 auth.PROBLEM_EXPIRATION_POLICY, auth.PROBLEM_UNKNOWN_KEY,
                 auth.PROBLEM_MISSING_KEY, auth.PROBLEM_CANDIDATE_ENTRY),
                key,
            )

    def _expect(self, mutate, problem):
        tampered = copy.deepcopy(self.record)
        mutate(tampered)
        tampered["authority_digest_sha256"] = auth.authority_digest(tampered)
        with self.assertRaises(auth.AuthorizationError) as caught:
            auth.validate_authorization(tampered)
        self.assertEqual(caught.exception.problem, problem)

    def test_widened_action_set_refuses(self):
        self._expect(lambda r: r["allowed_actions"].append("MERGE"),
                     auth.PROBLEM_ALLOWED_ACTIONS)
        self._expect(lambda r: r["allowed_actions"].append(PUSH_STEP),
                     auth.PROBLEM_ALLOWED_ACTIONS)

    def test_unknown_and_missing_keys_refuse(self):
        self._expect(lambda r: r.__setitem__("merge_method", "squash"),
                     auth.PROBLEM_UNKNOWN_KEY)
        self._expect(lambda r: r.pop("revocation"), auth.PROBLEM_MISSING_KEY)

    def test_mode_other_than_pull_request_refuses(self):
        # Keyword form on purpose: the hermetic-git AST guard classifies
        # positional string arguments that spell a git subcommand.
        self._expect(lambda r: r.update(mode="merge"), auth.PROBLEM_MODE)

    def test_evidence_bound_to_another_candidate_refuses(self):
        self._expect(
            lambda r: r["evidence"]["reviewer_approve"].__setitem__(
                "candidate_identity_digest_sha256", "c" * 64
            ),
            auth.PROBLEM_EVIDENCE,
        )

    def test_reviewer_decision_other_than_approve_refuses(self):
        self._expect(
            lambda r: r["evidence"]["reviewer_approve"].__setitem__(
                "decision", "REJECT"
            ),
            auth.PROBLEM_BAD_VALUE,
        )

    def test_non_green_verification_refuses(self):
        self._expect(
            lambda r: r["evidence"]["independent_verification"].__setitem__(
                "exit_status", 1
            ),
            auth.PROBLEM_TOO_LARGE,
        )

    def test_engineering_status_other_than_complete_refuses(self):
        self._expect(
            lambda r: r["evidence"]["engineering_complete"].__setitem__(
                "status", "ACTIVE"
            ),
            auth.PROBLEM_BAD_VALUE,
        )

    def test_expiration_beyond_the_bound_refuses(self):
        self._expect(
            lambda r: r["expiration"].__setitem__(
                "expires_at",
                r["human_authorization"]["authorized_at"]
                + auth.MAX_AUTHORIZATION_VALIDITY_SECONDS + 1,
            ),
            auth.PROBLEM_EXPIRATION_POLICY,
        )

    def test_remote_grammar_accepts_git_suffix_and_refuses_ssh(self):
        target = auth.parse_exact_remote_url(GITHUB_URL)
        self.assertEqual(target.repository_url, REPO_URL)
        for bad in ("git@github.com:octo/repo.git",
                    "ssh://git@github.com/octo/repo",
                    "https://github.com/octo/repo.git/",
                    "https://gitlab.com/octo/repo"):
            with self.assertRaises(auth.AuthorizationError) as caught:
                auth.parse_exact_remote_url(bad)
            self.assertEqual(caught.exception.problem,
                             auth.PROBLEM_REMOTE_GRAMMAR)

    def test_shell_as_reverification_argv_refuses(self):
        self._expect(
            lambda r: r["reverification"].__setitem__(
                "argv", ["bash", "-c", "true"]
            ),
            auth.PROBLEM_BAD_VALUE,
        )

    def test_mission_authorization_stays_separate(self):
        # The delivery record carries no Mission Authorization content
        # key, and the Mission Authorization schema is unchanged: neither
        # can stand in for the other.
        content_keys = set(mission_auth.ALLOWED_AUTHORIZATION_KEYS) - {
            "workflow_id", "revision",
        }
        self.assertFalse(content_keys & set(auth.AUTHORITY_KEYS))
        self.assertIn("delivery_authority",
                      mission_auth.ALLOWED_AUTHORIZATION_KEYS)
        self.assertEqual(wa_record.DELIVERY_AUTHORITY_NONE, "none")
        with self.assertRaises(auth.AuthorizationError):
            auth.validate_authorization({
                key: None for key in mission_auth.ALLOWED_AUTHORIZATION_KEYS
            })

    def test_transitions_are_closed(self):
        with self.assertRaises(auth.AuthorizationError):
            auth.validate_transition(auth.PHASE_AUTHORIZED,
                                     auth.PHASE_COMPLETE)
        with self.assertRaises(auth.AuthorizationError):
            auth.validate_transition(auth.PHASE_COMPLETE,
                                     auth.PHASE_AUTHORIZED)
        auth.validate_transition(auth.PHASE_PR_OPENED, auth.PHASE_COMPLETE)
        for phase in auth.TERMINAL_PHASES:
            self.assertEqual(auth.ALLOWED_TRANSITIONS[phase], frozenset())


# ---------------------------------------------------------------- candidate


class CandidateIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fx = DeliveryFixture(self)
        self.raw = self.fx.transport.diff_index_raw(str(self.fx.work),
                                                    self.fx.baseline)
        self.entries = candidate_module.parse_raw_z(self.raw)

    def test_entries_cover_add_modify_delete_and_mode(self):
        by_path = {entry["path"]: entry for entry in self.entries}
        self.assertEqual(sorted(by_path), ["keep.txt", "old.txt",
                                           "src/pkg.py", "tool.sh"])
        self.assertEqual(by_path["src/pkg.py"]["status"], "A")
        self.assertEqual(by_path["keep.txt"]["status"], "M")
        self.assertEqual(by_path["old.txt"]["status"], "D")
        self.assertEqual(by_path["tool.sh"]["status"], "M")
        self.assertEqual(by_path["tool.sh"]["mode"], "100755")
        self.assertEqual(
            [entry["path"] for entry in self.entries],
            sorted(by_path, key=lambda item: item.encode("utf-8")),
        )

    def test_identity_is_independent_of_unrelated_base_files(self):
        before = candidate_module.identity_digest(self.entries)
        new_base = self.fx.advance_base({
            "README.md": "readme v2\n",
            "assets/brand/banner.svg": "<svg>2</svg>\n",
        })
        git("fetch", "-q", "origin", BASE_BRANCH, cwd=self.fx.work)
        git("read-tree", "-m", "-u", self.fx.baseline, new_base,
            cwd=self.fx.work)
        live = candidate_module.parse_raw_z(
            self.fx.transport.diff_index_raw(str(self.fx.work), new_base)
        )
        self.assertEqual(candidate_module.identity_digest(live), before)
        self.assertEqual(candidate_module.compare(self.entries, live),
                         (None, None))

    def test_each_mutation_has_its_own_problem(self):
        def mutated(change):
            entries = copy.deepcopy(self.entries)
            change(entries)
            return entries
        by = lambda entries, path: next(
            entry for entry in entries if entry["path"] == path
        )
        cases = [
            (lambda e: e.remove(by(e, "keep.txt")),
             candidate_module.PROBLEM_PATH_MISSING),
            (lambda e: e.append({"path": "zzz.txt", "status": "A",
                                 "mode": "100644", "blob": "1" * 40}),
             candidate_module.PROBLEM_PATH_EXTRA),
            (lambda e: by(e, "keep.txt").__setitem__("status", "A"),
             candidate_module.PROBLEM_STATUS_CHANGED),
            (lambda e: by(e, "keep.txt").__setitem__("mode", "100755"),
             candidate_module.PROBLEM_MODE_CHANGED),
            (lambda e: by(e, "keep.txt").__setitem__("blob", "2" * 40),
             candidate_module.PROBLEM_CONTENT_CHANGED),
        ]
        seen = set()
        for change, expected in cases:
            problem, _ = candidate_module.compare(self.entries,
                                                  mutated(change))
            self.assertEqual(problem, expected)
            seen.add(problem)
        self.assertEqual(len(seen), 5)
        for change, expected in cases:
            live = mutated(change)
            self.assertNotEqual(candidate_module.identity_digest(live),
                                candidate_module.identity_digest(
                                    self.entries))

    def test_refusals(self):
        def raw(meta, path):
            return meta + b"\0" + path + b"\0"
        good = b":000000 100644 " + b"0" * 40 + b" " + b"1" * 40 + b" A"
        cases = [
            (raw(b":000000 160000 " + b"0" * 40 + b" " + b"1" * 40 + b" A",
                 b"sub"), candidate_module.PROBLEM_SUBMODULE),
            (raw(b":100644 120000 " + b"1" * 40 + b" " + b"2" * 40 + b" T",
                 b"f"), candidate_module.PROBLEM_STATUS),
            (raw(good, b"a\xff"), candidate_module.PROBLEM_PATH),
            (raw(good, b"a\nb"), candidate_module.PROBLEM_PATH),
            (raw(good, b"a") + raw(good, b"a"),
             candidate_module.PROBLEM_DUPLICATE),
            (b"", candidate_module.PROBLEM_EMPTY),
            # A rename record carries two paths: not a meta/path pair
            # stream, refused as such (--no-renames makes it impossible).
            (b":100644 100644 " + b"1" * 40 + b" " + b"2" * 40 + b" R100"
             + b"\0old\0new\0", candidate_module.PROBLEM_RAW_FORMAT),
        ]
        for data, expected in cases:
            with self.assertRaises(candidate_module.CandidateError) as c:
                candidate_module.parse_raw_z(data)
            self.assertEqual(c.exception.problem, expected, data)
        too_many = b"".join(
            raw(good, b"p%05d" % index)
            for index in range(auth.MAX_CANDIDATE_ENTRIES + 1)
        )
        with self.assertRaises(candidate_module.CandidateError) as c:
            candidate_module.parse_raw_z(too_many)
        self.assertEqual(c.exception.problem, candidate_module.PROBLEM_TOO_MANY)

    def test_overlap_covers_exact_and_prefix_both_ways(self):
        self.assertEqual(candidate_module.overlaps(["a/b"], ["c"]), [])
        self.assertEqual(candidate_module.overlaps(["a/b"], ["a/b"]),
                         [("a/b", "a/b")])
        self.assertEqual(candidate_module.overlaps(["a"], ["a/b"]),
                         [("a", "a/b")])
        self.assertEqual(candidate_module.overlaps(["a/b"], ["a"]),
                         [("a/b", "a")])
        self.assertEqual(candidate_module.overlaps(["ab"], ["a"]), [])


# ---------------------------------------------------------------- machine


class HappyPathTests(unittest.TestCase):
    def setUp(self):
        # Hooks ON: the full delivery is proven through the installed
        # guards, with no legacy token anywhere.
        self.fx = DeliveryFixture(self, hooks=True)
        self.fx.authorize()

    def test_full_delivery_without_drift(self):
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE, record)
        self.assertEqual(record["phase"], auth.PHASE_COMPLETE)
        steps = record["steps"]
        self.assertEqual(steps[BASE_REFRESH]["state"], auth.STEP_NOT_NEEDED)
        for step in (COMMIT_STEP, PUSH_STEP, PR_CREATE):
            self.assertEqual(steps[step]["state"], auth.STEP_SUCCEEDED, step)
            self.assertEqual(steps[step]["receipt"]["state"],
                             auth.RECEIPT_SUCCEEDED)
        head = self.fx.head()
        self.assertEqual(steps[COMMIT_STEP]["receipt"]["observed"][
            "commit_oid"], head)
        self.assertEqual(git("rev-parse", "HEAD^1", cwd=self.fx.work),
                         self.fx.baseline)
        self.assertEqual(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH),
                         head)
        self.assertEqual(len(self.fx.transport.created), 1)
        self.assertEqual(record["pull_request"]["url"],
                         REPO_URL + "/pull/41")
        self.assertEqual(record["pull_request"]["head_sha"], head)
        # The commit carries the bound committer, and no hook bypass.
        identity = git("log", "-1", "--format=%an|%ae|%cn|%ce",
                       cwd=self.fx.work)
        self.assertEqual(identity, "Delivery Human|human@example.com|"
                                   "Delivery Human|human@example.com")
        self.assertEqual(git("status", "--porcelain", cwd=self.fx.work), "")
        self.assertEqual(self.fx.remote_oid("refs/heads/" + BASE_BRANCH),
                         self.fx.baseline)

    def test_status_projection_answers_from_state(self):
        before = boundary_module.project_status(self.fx.record(),
                                                self.fx.clock())
        self.assertTrue(before["authorization"]["valid"])
        self.assertEqual(before["engineering"]["status"], "COMPLETE")
        self.assertEqual(before["verification"]["recorded"]["exit_status"], 0)
        self.assertIsNone(before["pr_url"])
        self.assertIsNone(before["blocker"])
        self.assertEqual(before["next_action"],
                         {"action": boundary_module.NEXT_ADVANCE,
                          "step": BASE_REFRESH})
        boundary = boundary_module.PrDeliveryBoundary(self.fx.machine)
        after = boundary.advance("prd-test")
        self.assertEqual(after["outcome"], machine_module.OUTCOME_COMPLETE)
        self.assertEqual(after["phase"], auth.PHASE_COMPLETE)
        self.assertEqual(after["pr_url"], REPO_URL + "/pull/41")
        self.assertEqual(after["commit"]["commit_oid"], self.fx.head())
        self.assertEqual(after["push"]["remote_oid"], self.fx.head())
        self.assertEqual(after["base_refresh"]["state"],
                         auth.STEP_NOT_NEEDED)
        self.assertEqual(after["next_action"]["action"],
                         boundary_module.NEXT_COMPLETE)
        for key in ("authorization", "engineering", "verification",
                    "base_refresh", "commit", "push", "pr_url", "blocker",
                    "next_action"):
            self.assertIn(key, after)

    def test_pr_text_is_deterministic_and_carries_no_provenance(self):
        record = self.fx.record()
        body_a = pr_text.body(record)
        body_b = pr_text.body(copy.deepcopy(record))
        self.assertEqual(body_a, body_b)
        self.assertEqual(text_digest(body_a), pr_text.body_digest(record))
        lowered = body_a.lower() + pr_text.title(record).lower()
        for token in ("co-authored-by", "generated with", "claude", "codex",
                      "gpt", "grok", "chain of thought"):
            self.assertNotIn(token, lowered)
        self.assertIn("src/pkg.py", body_a)
        self.assertIn(record["candidate"]["identity_digest_sha256"], body_a)
        self.assertIn("APPROVE in round 2", body_a)
        self.assertIn("exit status 0", body_a)
        self.fx.machine.advance("prd-test")
        title, body = self.fx.transport.created[0]
        self.assertEqual(body, body_a)
        self.assertEqual(title, pr_text.title(record))


class PreconditionTests(unittest.TestCase):
    def setUp(self):
        self.fx = DeliveryFixture(self)

    def _blocked(self, problem):
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED, record)
        self.assertEqual(record["phase"], auth.PHASE_BLOCKED)
        self.assertEqual(record["blocker"]["problem"], problem,
                         record["blocker"])
        return record

    def test_changed_bytes_after_authorization_block(self):
        self.fx.authorize()
        (self.fx.work / "keep.txt").write_text("keep v3\n")
        git("add", "-A", cwd=self.fx.work)
        self._blocked(candidate_module.PROBLEM_CONTENT_CHANGED)
        self.assertEqual(self.fx.head(), self.fx.baseline)

    def test_extra_file_blocks(self):
        self.fx.authorize()
        (self.fx.work / "extra.txt").write_text("x\n")
        git("add", "-A", cwd=self.fx.work)
        self._blocked(candidate_module.PROBLEM_PATH_EXTRA)

    def test_missing_file_blocks(self):
        self.fx.authorize()
        git("rm", "-q", "--cached", "src/pkg.py", cwd=self.fx.work)
        (self.fx.work / "src" / "pkg.py").unlink()
        self._blocked(candidate_module.PROBLEM_PATH_MISSING)

    def test_mode_change_blocks(self):
        self.fx.authorize()
        os.chmod(self.fx.work / "keep.txt", 0o755)
        git("add", "-A", cwd=self.fx.work)
        self._blocked(candidate_module.PROBLEM_MODE_CHANGED)

    def test_reverting_a_mode_change_is_a_missing_path(self):
        # tool.sh's only change was its mode; reverting it makes the
        # entry disappear, which is reported as the path going missing.
        self.fx.authorize()
        os.chmod(self.fx.work / "tool.sh", 0o644)
        git("add", "-A", cwd=self.fx.work)
        self._blocked(candidate_module.PROBLEM_PATH_MISSING)

    def test_unstaged_change_blocks(self):
        self.fx.authorize()
        (self.fx.work / "keep.txt").write_text("keep v3 unstaged\n")
        self._blocked(machine_module.PROBLEM_CANDIDATE_UNSTAGED)

    def test_wrong_branch_checked_out_blocks(self):
        self.fx.authorize()
        git("checkout", "-q", "-b", "other", cwd=self.fx.work)
        self._blocked(machine_module.PROBLEM_BRANCH_NOT_CHECKED_OUT)

    def test_wrong_remote_blocks(self):
        self.fx.authorize()
        git("remote", "set-url", "origin",
            "https://github.com/other/repo.git", cwd=self.fx.work)
        self._blocked(machine_module.PROBLEM_WRONG_REMOTE)

    def _rewrite_case(self, key, value):
        """A remote rewrite added AFTER authorization (round-01 B2). The
        fixture's own insteadOf (which points the GitHub URL at the local
        bare repository) is removed first: git honours the longest
        matching prefix, so the post-authorization rewrite must be the
        one that resolves, exactly as in the Lead's reproduction."""
        self.fx.authorize()
        evil = Path(self.fx.temp.name) / "evil.git"
        run_git("init", "-q", "--bare", "-b", BASE_BRANCH, str(evil))
        if "pushInsteadOf" not in key:
            git("config", "--unset", "url.%s.insteadOf" % self.fx.bare,
                cwd=self.fx.work)
        git("config", key % str(evil), value, cwd=self.fx.work)
        record = self._blocked(machine_module.PROBLEM_WRONG_REMOTE)
        self.assertIn(str(evil), record["blocker"]["detail"])
        self.assertIsNone(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH))
        self.assertEqual(
            git("ls-remote", str(evil), cwd=self.fx.work), ""
        )

    def test_post_authorization_insteadof_rewrite_blocks(self):
        self._rewrite_case("url.%s.insteadOf", GITHUB_URL)

    def test_post_authorization_pushinsteadof_rewrite_blocks(self):
        self._rewrite_case("url.%s.pushInsteadOf", GITHUB_URL)

    def test_post_authorization_pushurl_blocks(self):
        self.fx.authorize()
        evil = Path(self.fx.temp.name) / "evil.git"
        run_git("init", "-q", "--bare", "-b", BASE_BRANCH, str(evil))
        git("config", "remote.origin.pushurl", str(evil), cwd=self.fx.work)
        self._blocked(machine_module.PROBLEM_WRONG_REMOTE)
        self.assertEqual(git("ls-remote", str(evil), cwd=self.fx.work), "")

    def test_push_receipt_binds_the_expanded_push_url(self):
        self.fx.authorize()
        self.fx.machine.advance_once("prd-test")
        self.fx.machine.advance_once("prd-test")
        head = self.fx.head()
        # The rewrite lands after the commit: PUSH derivation refuses.
        evil = Path(self.fx.temp.name) / "evil.git"
        run_git("init", "-q", "--bare", "-b", BASE_BRANCH, str(evil))
        git("config", "url.%s.pushInsteadOf" % evil, GITHUB_URL,
            cwd=self.fx.work)
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
        self.assertEqual(record["blocker"]["problem"],
                         machine_module.PROBLEM_WRONG_REMOTE)
        self.assertEqual(self.fx.head(), head)
        self.assertIsNone(record["steps"][PUSH_STEP]["receipt"])
        self.assertEqual(git("ls-remote", str(evil), cwd=self.fx.work), "")

    def test_expired_authorization_blocks(self):
        self.fx.authorize()
        self.fx.now[0] += 3601
        self._blocked(receipts.PROBLEM_EXPIRED)
        status = boundary_module.project_status(self.fx.record(),
                                                self.fx.clock())
        self.assertEqual(status["next_action"]["action"],
                         boundary_module.NEXT_EXPIRED)

    def test_step_outside_allowed_actions_refuses_derivation(self):
        self.fx.authorize()
        record = self.fx.record()
        record["allowed_actions"] = [BASE_REFRESH, COMMIT_STEP]
        record["authority_digest_sha256"] = auth.authority_digest(record)
        record["phase"] = auth.PHASE_COMMITTED
        problem, _ = receipts.precondition_problem(record, PUSH_STEP,
                                                   self.fx.clock())
        self.assertEqual(problem, receipts.PROBLEM_STEP_NOT_ALLOWED)
        problem, _ = receipts.precondition_problem(record, COMMIT_STEP,
                                                   self.fx.clock())
        self.assertEqual(problem, receipts.PROBLEM_PHASE_FORBIDS_STEP)

    def test_stale_evidence_refuses_derivation(self):
        self.fx.authorize()
        record = self.fx.record()
        record["evidence"]["reviewer_approve"]["base_oid"] = "f" * 40
        problem, detail = receipts.precondition_problem(
            record, BASE_REFRESH, self.fx.clock(),
        )
        self.assertEqual(problem, receipts.PROBLEM_EVIDENCE_STALE)
        self.assertIn("reviewer_approve", detail)

    def test_ceremony_refuses_wrong_confirmation_and_unstaged_tree(self):
        with self.assertRaises(cli_module.CeremonyError):
            cli_module.assemble_authority(
                self.fx.transport, self.fx.args(), self.fx.clock(), "human",
                lambda prompt: "000000000000", out=io.StringIO(),
            )
        self.assertEqual(self.fx.store.load()["deliveries"], {})
        with self.assertRaises(cli_module.CeremonyError):
            cli_module.assemble_authority(
                self.fx.transport,
                self.fx.args(verification_exit_status=1), self.fx.clock(),
                "human", lambda prompt: self.fx.live_digest()[:12],
                out=io.StringIO(),
            )
        (self.fx.work / "stray.txt").write_text("x\n")
        with self.assertRaises(cli_module.CeremonyError):
            cli_module.assemble_authority(
                self.fx.transport, self.fx.args(), self.fx.clock(), "human",
                lambda prompt: self.fx.live_digest()[:12], out=io.StringIO(),
            )


class BaseDriftTests(unittest.TestCase):
    def setUp(self):
        self.fx = DeliveryFixture(self)
        self.fx.authorize()

    def test_p1_a5_shape_refreshes_automatically(self):
        # Hooks ON here: the compare-and-swap ref move goes through the
        # reference-transaction guard with a BASE_REFRESH receipt.
        guards.install_git_guard(self.fx.work)
        new_base = self.fx.advance_base({
            "README.md": "readme v2\n",
            "assets/brand/banner.svg": "<svg>2</svg>\n",
        })
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE, record)
        refresh = record["steps"][BASE_REFRESH]
        self.assertEqual(refresh["state"], auth.STEP_SUCCEEDED)
        binding = refresh["receipt"]["binding"]
        self.assertEqual(binding["old_base_oid"], self.fx.baseline)
        self.assertEqual(binding["new_base_oid"], new_base)
        self.assertTrue(binding["fast_forward"])
        self.assertEqual(binding["source_ref"], "refs/heads/" + SOURCE_BRANCH)
        observed = refresh["receipt"]["observed"]
        self.assertEqual(observed["reverification_exit_status"], 0)
        self.assertEqual(observed["base_ci"], machine_module.BASE_CI_NONE)
        self.assertEqual(self.fx.marker.read_text(), "ran\n")
        self.assertEqual(record["base_state"]["current_base_oid"], new_base)
        self.assertEqual(git("rev-parse", "HEAD^1", cwd=self.fx.work),
                         new_base)
        self.assertEqual((self.fx.work / "README.md").read_text(),
                         "readme v2\n")
        self.assertEqual((self.fx.work / "keep.txt").read_text(),
                         "keep v2\n")
        live = candidate_module.parse_raw_z(
            self.fx.transport.diff_tree_raw(str(self.fx.work), new_base,
                                            self.fx.head())
        )
        self.assertEqual(candidate_module.identity_digest(live),
                         record["candidate"]["identity_digest_sha256"])
        self.assertEqual(record["pull_request"]["number"], 41)

    def _blocked(self, problem):
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED, record)
        self.assertEqual(record["blocker"]["problem"], problem,
                         record["blocker"])
        self.assertEqual(self.fx.head(), self.fx.baseline)
        return record

    def test_overlapping_advance_blocks_before_any_effect(self):
        self.fx.advance_base({"keep.txt": "keep from base\n"})
        record = self._blocked(machine_module.PROBLEM_BASE_OVERLAP)
        self.assertIsNone(record["steps"][BASE_REFRESH]["receipt"])
        self.assertEqual((self.fx.work / "keep.txt").read_text(),
                         "keep v2\n")

    def test_directory_file_prefix_overlap_blocks(self):
        self.fx.advance_base({"src": "a file where the candidate has a"
                                     " directory\n"})
        self._blocked(machine_module.PROBLEM_BASE_OVERLAP)

    def test_non_fast_forward_base_blocks(self):
        self.fx._ensure_clone()
        git("checkout", "-q", "--orphan", "rewrite", cwd=self.fx.clone)
        (self.fx.clone / "README.md").write_text("rewritten\n")
        git("add", "-A", cwd=self.fx.clone)
        git("commit", "-qm", "rewrite", cwd=self.fx.clone)
        git("push", "-q", "--force", "origin", "HEAD:" + BASE_BRANCH,
            cwd=self.fx.clone)
        self._blocked(machine_module.PROBLEM_BASE_NOT_FAST_FORWARD)

    def test_red_base_ci_blocks(self):
        self.fx.advance_base({"README.md": "readme v2\n"})
        self.fx.transport.check_runs = [
            {"name": "ci", "status": "completed", "conclusion": "failure"},
        ]
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
        self.assertEqual(record["blocker"]["problem"],
                         machine_module.PROBLEM_BASE_CI_RED)

    def test_reverification_failure_blocks(self):
        self.fx.advance_base({"README.md": "readme v2\n"})
        record = self.fx.record()
        self.fx.machine.revoke("prd-test", "human", "reset for test")
        # A fresh authorization with a failing reverification argv.
        with self.fx.store.lock():
            document = self.fx.store.load()
            document["deliveries"] = {}
            self.fx.store.save(document)
        self.fx.authorize(reverify_command="%s -c \"raise SystemExit(3)\""
                          % sys.executable)
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
        self.assertEqual(record["blocker"]["problem"],
                         machine_module.PROBLEM_REVERIFICATION_FAILED)
        self.assertIn("exited 3", record["blocker"]["detail"])

    def test_disjoint_advance_after_commit_is_recorded_and_continues(self):
        self.fx.machine.advance_once("prd-test")   # BASE_REFRESH not needed
        self.fx.machine.advance_once("prd-test")   # COMMIT
        record = self.fx.record()
        self.assertEqual(record["phase"], auth.PHASE_COMMITTED)
        new_base = self.fx.advance_base({"README.md": "readme v2\n"})
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE, record)
        advance = record["base_state"]["advance_after_commit"]
        self.assertEqual(advance["old_base_oid"], self.fx.baseline)
        self.assertEqual(advance["new_base_oid"], new_base)
        self.assertEqual(record["base_state"]["current_base_oid"], new_base)
        status = boundary_module.project_status(record, self.fx.clock())
        self.assertEqual(status["base_refresh"]["advance_after_commit"],
                         advance)
        self.assertIn("not re-run", status["verification"]["post_commit_note"])

    def test_overlapping_advance_after_commit_blocks(self):
        self.fx.machine.advance_once("prd-test")
        self.fx.machine.advance_once("prd-test")
        head = self.fx.head()
        self.fx.advance_base({"keep.txt": "base wins\n"})
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
        self.assertEqual(
            record["blocker"]["problem"],
            machine_module.PROBLEM_BASE_ADVANCED_OVERLAPPING_AFTER_COMMIT,
        )
        self.assertEqual(self.fx.head(), head)
        self.assertIsNone(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH))


class CrashReconciliationTests(unittest.TestCase):
    """Crash injection before and after every external effect: the next
    advance reconciles forward with no duplicate consequential effect."""

    def _fixture(self, transport_class):
        fx = DeliveryFixture(self, transport_class=transport_class)
        fx.authorize()
        return fx

    def _complete_after_crash(self, fx, expected_crashes=1):
        crashes = 0
        while True:
            try:
                outcome = fx.machine.advance("prd-test")
                break
            except Crash:
                crashes += 1
                fx.transport.crash_armed = False
        self.assertEqual(crashes, expected_crashes)
        record = fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE, record)
        head = fx.head()
        self.assertEqual(git("rev-parse", "HEAD^1", cwd=fx.work),
                         record["base_state"]["current_base_oid"])
        self.assertEqual(fx.remote_oid("refs/heads/" + SOURCE_BRANCH), head)
        self.assertEqual(len(fx.transport.created), 1)
        self.assertEqual(
            git("rev-list", "--count",
                record["base_state"]["current_base_oid"] + "..HEAD",
                cwd=fx.work),
            "1",
        )
        return record

    def test_crash_before_commit_effect(self):
        class T(TestTransport):
            crash_armed = True

            def commit(self, path, name, email, message):
                if self.crash_armed:
                    raise Crash()
                return super(T, self).commit(path, name, email, message)
        fx = self._fixture(T)
        record = self._complete_after_crash(fx)
        step = record["steps"][COMMIT_STEP]
        self.assertEqual(len(step["voided"]), 1)
        self.assertEqual(step["receipt"]["attempt"], 2)

    def test_crash_after_commit_effect(self):
        class T(TestTransport):
            crash_armed = True

            def commit(self, path, name, email, message):
                super(T, self).commit(path, name, email, message)
                if self.crash_armed:
                    raise Crash()
        fx = self._fixture(T)
        record = self._complete_after_crash(fx)
        step = record["steps"][COMMIT_STEP]
        self.assertEqual(step["voided"], [])
        self.assertEqual(step["receipt"]["attempt"], 1)
        self.assertEqual(step["receipt"]["observed"]["commit_oid"],
                         fx.head())

    def test_crash_before_push_effect(self):
        class T(TestTransport):
            crash_armed = True

            def push(self, path, remote_name, source_ref, destination_ref):
                if self.crash_armed:
                    raise Crash()
                return super(T, self).push(path, remote_name, source_ref,
                                           destination_ref)
        fx = self._fixture(T)
        record = self._complete_after_crash(fx)
        self.assertEqual(len(record["steps"][PUSH_STEP]["voided"]), 1)

    def test_crash_after_push_effect(self):
        class T(TestTransport):
            crash_armed = True

            def push(self, path, remote_name, source_ref, destination_ref):
                super(T, self).push(path, remote_name, source_ref,
                                    destination_ref)
                if self.crash_armed:
                    raise Crash()
        fx = self._fixture(T)
        record = self._complete_after_crash(fx)
        step = record["steps"][PUSH_STEP]
        self.assertEqual(step["voided"], [])
        self.assertTrue(step["receipt"]["observed"]["reconciled"])

    def test_crash_before_pr_create_effect(self):
        class T(TestTransport):
            crash_armed = True

            def gh_pr_create(self, *args):
                if self.crash_armed:
                    raise Crash()
                return super(T, self).gh_pr_create(*args)
        fx = self._fixture(T)
        record = self._complete_after_crash(fx)
        self.assertEqual(len(record["steps"][PR_CREATE]["voided"]), 1)

    def test_crash_after_pr_create_effect(self):
        class T(TestTransport):
            crash_armed = True

            def gh_pr_create(self, *args):
                url = super(T, self).gh_pr_create(*args)
                if self.crash_armed:
                    raise Crash()
                return url
        fx = self._fixture(T)
        record = self._complete_after_crash(fx)
        step = record["steps"][PR_CREATE]
        self.assertEqual(step["voided"], [])
        self.assertTrue(step["receipt"]["observed"]["reconciled"])
        self.assertEqual(record["pull_request"]["number"], 41)

    def test_crash_between_read_tree_and_ref_move(self):
        class T(TestTransport):
            crash_armed = True

            def update_ref(self, path, ref, new_oid, old_oid):
                if self.crash_armed:
                    raise Crash()
                return super(T, self).update_ref(path, ref, new_oid, old_oid)
        fx = self._fixture(T)
        new_base = fx.advance_base({"README.md": "readme v2\n"})
        record = self._complete_after_crash(fx)
        step = record["steps"][BASE_REFRESH]
        self.assertEqual(step["state"], auth.STEP_SUCCEEDED)
        self.assertEqual(step["voided"], [])
        self.assertEqual(record["base_state"]["current_base_oid"], new_base)

    def test_crash_before_read_tree(self):
        class T(TestTransport):
            crash_armed = True

            def read_tree_two_way(self, path, old_oid, new_oid):
                if self.crash_armed:
                    raise Crash()
                return super(T, self).read_tree_two_way(path, old_oid,
                                                        new_oid)
        fx = self._fixture(T)
        fx.advance_base({"README.md": "readme v2\n"})
        record = self._complete_after_crash(fx)
        self.assertEqual(len(record["steps"][BASE_REFRESH]["voided"]), 1)

    def test_transport_failure_on_pr_create_is_retryable_then_reconciles(self):
        fx = self._fixture(TestTransport)
        fx.transport.create_error = True
        outcome = fx.machine.advance("prd-test")
        record = fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_RETRY)
        self.assertEqual(record["phase"], auth.PHASE_PUSHED)
        self.assertEqual(record["steps"][PR_CREATE]["state"],
                         auth.STEP_FAILED_RETRYABLE)
        status = boundary_module.project_status(record, fx.clock())
        self.assertEqual(status["next_action"],
                         {"action": boundary_module.NEXT_WAIT_RETRY,
                          "step": PR_CREATE})
        fx.transport.create_error = False
        outcome = fx.machine.advance("prd-test")
        record = fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE, record)
        self.assertEqual(len(fx.transport.created), 1)
        self.assertEqual(len(record["steps"][PR_CREATE]["voided"]), 1)

    def test_existing_exact_pr_is_adopted_not_duplicated(self):
        fx = self._fixture(TestTransport)
        fx.machine.advance_once("prd-test")
        fx.machine.advance_once("prd-test")
        fx.machine.advance_once("prd-test")
        record = fx.record()
        self.assertEqual(record["phase"], auth.PHASE_PUSHED)
        fx.transport.open_prs.append({
            "number": 7, "url": REPO_URL + "/pull/7",
            "headRefOid": fx.head(), "headRefName": SOURCE_BRANCH,
            "baseRefName": BASE_BRANCH, "state": "OPEN",
        })
        outcome = fx.machine.advance("prd-test")
        record = fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE, record)
        self.assertEqual(fx.transport.created, [])
        self.assertEqual(record["pull_request"]["number"], 7)
        self.assertTrue(record["steps"][PR_CREATE]["receipt"]["observed"][
            "reconciled"])

    def test_closed_exact_pr_blocks_instead_of_duplicating(self):
        # Round-01 B3: a closed (or merged) exact pull request is seen and
        # stops delivery; a second equivalent one is never created.
        for state in ("CLOSED", "MERGED"):
            with self.subTest(state=state):
                fx = self._fixture(TestTransport)
                fx.machine.advance_once("prd-test")
                fx.machine.advance_once("prd-test")
                fx.machine.advance_once("prd-test")
                fx.transport.open_prs.append({
                    "number": 9, "url": REPO_URL + "/pull/9",
                    "headRefOid": fx.head(), "headRefName": SOURCE_BRANCH,
                    "baseRefName": BASE_BRANCH, "state": state,
                })
                outcome = fx.machine.advance("prd-test")
                record = fx.record()
                self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
                self.assertEqual(record["blocker"]["problem"],
                                 machine_module.PROBLEM_PR_NOT_OPEN)
                self.assertIn("#9", record["blocker"]["detail"])
                self.assertEqual(fx.transport.created, [])
                self.assertIsNone(record["pull_request"])

    def test_closed_other_pr_does_not_block(self):
        fx = self._fixture(TestTransport)
        fx.transport.open_prs.append({
            "number": 3, "url": REPO_URL + "/pull/3",
            "headRefOid": "9" * 40, "headRefName": SOURCE_BRANCH,
            "baseRefName": BASE_BRANCH, "state": "CLOSED",
        })
        outcome = fx.machine.advance("prd-test")
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE)
        self.assertEqual(len(fx.transport.created), 1)

    def test_foreign_pr_on_the_same_head_base_blocks(self):
        fx = self._fixture(TestTransport)
        fx.transport.open_prs.append({
            "number": 8, "url": REPO_URL + "/pull/8",
            "headRefOid": "9" * 40, "headRefName": SOURCE_BRANCH,
            "baseRefName": BASE_BRANCH, "state": "OPEN",
        })
        outcome = fx.machine.advance("prd-test")
        record = fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
        self.assertEqual(record["blocker"]["problem"],
                         machine_module.PROBLEM_PR_AMBIGUOUS)
        self.assertEqual(fx.transport.created, [])

    def test_unexpected_remote_ref_movement_blocks_push(self):
        fx = self._fixture(TestTransport)
        fx.machine.advance_once("prd-test")
        fx.machine.advance_once("prd-test")
        # Someone else lands a different commit on the source branch.
        fx._ensure_clone()
        git("checkout", "-q", "-b", SOURCE_BRANCH, cwd=fx.clone)
        (fx.clone / "foreign.txt").write_text("x\n")
        git("add", "-A", cwd=fx.clone)
        git("commit", "-qm", "foreign", cwd=fx.clone)
        git("push", "-q", "origin", "HEAD:" + SOURCE_BRANCH, cwd=fx.clone)
        foreign = git("rev-parse", "HEAD", cwd=fx.clone)
        outcome = fx.machine.advance("prd-test")
        record = fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
        self.assertEqual(record["blocker"]["problem"],
                         machine_module.PROBLEM_UNEXPECTED_REF_MOVEMENT)
        self.assertEqual(fx.remote_oid("refs/heads/" + SOURCE_BRANCH),
                         foreign)


class RevocationTests(unittest.TestCase):
    def setUp(self):
        self.fx = DeliveryFixture(self)
        self.fx.authorize()

    def test_revocation_before_any_effect_stops_everything(self):
        self.fx.machine.revoke("prd-test", "human", "changed my mind")
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
        self.assertEqual(record["phase"], auth.PHASE_REVOKED)
        self.assertEqual(self.fx.head(), self.fx.baseline)
        self.assertTrue(record["revocation"]["revoked"])
        status = boundary_module.project_status(record, self.fx.clock())
        self.assertEqual(status["next_action"]["action"],
                         boundary_module.NEXT_REVOKED)

    def test_revocation_after_commit_preserves_it_and_stops_the_push(self):
        self.fx.machine.advance_once("prd-test")
        self.fx.machine.advance_once("prd-test")
        head = self.fx.head()
        self.assertNotEqual(head, self.fx.baseline)
        self.fx.machine.revoke("prd-test", "human", "stop")
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_BLOCKED)
        self.assertEqual(record["phase"], auth.PHASE_REVOKED)
        self.assertEqual(self.fx.head(), head)
        self.assertEqual(record["steps"][COMMIT_STEP]["state"],
                         auth.STEP_SUCCEEDED)
        self.assertIsNone(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH))

    def test_revocation_between_derivation_and_effect_voids_the_receipt(self):
        fx = self.fx

        class T(TestTransport):
            def commit(self, path, name, email, message):
                raise AssertionError("the effect must not run once revoked")
        fx.transport = T(fx.work)
        machine = machine_module.DeliveryMachine(fx.store, fx.transport,
                                                 fx.clock)
        original_persist = machine._persist

        def persist_then_revoke(record):
            original_persist(record)
            if record["steps"][COMMIT_STEP]["state"] == auth.STEP_EXECUTING:
                machine_module.DeliveryMachine(
                    fx.store, fx.transport, fx.clock,
                ).revoke("prd-test", "other terminal", "now")
                machine._persist = original_persist
        machine._persist = persist_then_revoke
        outcome = machine.advance("prd-test")
        record = fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_REVOKED)
        self.assertEqual(record["phase"], auth.PHASE_REVOKED)
        self.assertEqual(self.fx.head(), self.fx.baseline)
        self.assertEqual(len(record["steps"][COMMIT_STEP]["voided"]), 1)

    def test_receipt_replay_after_the_effect_refuses(self):
        self.fx.machine.advance_once("prd-test")
        self.fx.machine.advance_once("prd-test")
        record = self.fx.record()
        receipt = record["steps"][COMMIT_STEP]["receipt"]
        live = guards._delivery_commit_live(self.fx.work)
        ok, reason = receipts.guard_decision(
            self.fx.work, COMMIT_STEP, live, self.fx.clock(),
        )
        self.assertFalse(ok)
        self.assertIn("no executing", reason)
        # Even forced back to 'executing', the binding no longer matches.
        receipt["state"] = auth.RECEIPT_EXECUTING
        record["steps"][COMMIT_STEP]["state"] = auth.STEP_EXECUTING
        record["phase"] = auth.PHASE_BASE_CURRENT
        with self.fx.store.lock():
            document = self.fx.store.load()
            document["deliveries"]["prd-test"] = record
            self.fx.store.save(document)
        ok, reason = receipts.guard_decision(
            self.fx.work, COMMIT_STEP, live, self.fx.clock(),
        )
        self.assertFalse(ok)
        self.assertIn("head_before", reason)


class MergeExclusionTests(unittest.TestCase):
    def test_transport_has_no_merge_release_or_force_verb(self):
        names = {
            name for name in dir(transport_module.DeliveryTransport)
            if not name.startswith("_")
        }
        words = set()
        for name in names:
            words.update(name.lower().split("_"))
        words.update(transport_module.ALLOWED_GIT_VERBS)
        for argv in transport_module.ALLOWED_GH_ARGV:
            words.update(item.lower().strip("-") for item in argv)
        for forbidden in ("merge", "release", "tag", "deploy", "publish",
                          "reset", "rebase", "checkout", "delete", "force",
                          "review", "close", "post", "put", "patch"):
            self.assertNotIn(forbidden, words, sorted(words))
        real = transport_module.DeliveryTransport()
        # Split at run time on purpose: the hermetic-git AST guard
        # classifies literal argv lists carrying git identity words.
        for text in ("pr merge 1", "pr review", "release create",
                     "api --method POST x", "pr close 1", "repo delete"):
            with self.assertRaises(transport_module.DeliveryTransportError):
                real._gh(text.split())
        # Round-01 N1: the git verb set is enforced at call time too.
        for text in ("branch --list", "reset --hard", "checkout x",
                     "--no-optional-locks branch", "tag --list"):
            with self.assertRaises(transport_module.DeliveryTransportError):
                real._git("/", text.split())

    def test_delivery_stops_at_complete_with_no_merge_authority(self):
        fx = DeliveryFixture(self)
        fx.authorize()
        fx.machine.advance("prd-test")
        record = fx.record()
        self.assertEqual(record["phase"], auth.PHASE_COMPLETE)
        self.assertEqual(auth.ALLOWED_TRANSITIONS[auth.PHASE_COMPLETE],
                         frozenset())
        self.assertEqual(fx.remote_oid("refs/heads/" + BASE_BRANCH),
                         fx.baseline)
        self.assertNotIn("merge", json.dumps(record).lower())


# ---------------------------------------------------- staged diff digest


INVALID_UTF8_MESSAGE = (
    "staged diff is not valid UTF-8; the legacy guard could not bind it"
    " either"
)
# A 3-byte character: 65536 = 1 (mod 3), so stream chunk boundaries fall
# inside these characters.
EURO = "€".encode("utf-8")
EURO_LINE = EURO * 40 + b"\n"


def _incompressible(size, seed):
    """Deterministic incompressible bytes: a sha256 counter stream."""
    out = bytearray()
    counter = 0
    while len(out) < size:
        out.extend(hashlib.sha256(b"%s:%d" % (seed, counter)).digest())
        counter += 1
    return bytes(out[:size])


def _staged_repo(base, name, files):
    """A repository whose index stages ``files`` (relative path -> bytes)
    on top of one base commit."""
    repo = base / name
    run_git("init", "-q", str(repo))
    (repo / "base.txt").write_bytes(b"base\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    for relative, content in files.items():
        (repo / relative).write_bytes(content)
    git("add", "-A", cwd=repo)
    return repo


def _raw_staged_diff(repo, out_path):
    """The exact bytes of ``diff --cached --binary``, taken independently
    of the transport. git writes them to a file (``--output``) because
    the hermetic helper decodes stdout as text."""
    run_git_completed(["-C", str(repo), "diff", "--cached", "--binary",
                       "--output=" + str(out_path)])
    return out_path.read_bytes()


class _StandInDiffChild(object):
    """Stands in for the ``git diff`` child on the digest path only.

    Real git cannot END a diff stream inside a multi-byte sequence: every
    diff line, ``\\ No newline at end of file`` included, ends with a
    newline. So the EOF-truncation and posture cases drive the production
    read loop over a scripted stdout instead of a real child.
    """

    def __init__(self, payload, returncode=0, stderr=b""):
        self.payload = payload
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = None
        self.calls = []
        self.waits = []

    def popen(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if self.stderr:
            kwargs["stderr"].write(self.stderr)
        self.stdout = io.BytesIO(self.payload)
        return self

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.returncode

    def kill(self):
        raise AssertionError("the digest path never kills its child")


class StagedDiffDigestTests(unittest.TestCase):
    """The COMMIT binding's ``staged_sha256`` over the REAL transport.

    ``staged_diff_sha256`` streams ``diff --cached --binary`` without the
    captured-output bound (nothing is captured), so a candidate of any
    size binds; every other capture keeps the bound.
    """

    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.base = Path(temp.name)
        cls.large = _staged_repo(cls.base, "large", {
            "base.txt": b"base\nchanged\n",
            # NUL first: git classifies the file binary, so the diff
            # carries a base85 "GIT binary patch" (--binary matters).
            "blob.bin": b"\x00" + _incompressible(700000, b"blob"),
            "euro.txt": EURO_LINE * 12000,
        })
        cls.large_raw = _raw_staged_diff(cls.large,
                                         cls.base / "large.diff")

    def run_stand_in(self, child):
        path = str(self.base)
        with patch.object(transport_module.subprocess, "Popen",
                          child.popen):
            return TestTransport(path).staged_diff_sha256(path)

    def test_a_staged_diff_over_the_output_bound_binds(self):
        raw = self.large_raw
        # Anti-vacuity: comfortably over the bound, a real binary patch,
        # and a character split by a stream chunk boundary.
        self.assertGreater(
            len(raw), 2 * transport_module.MAX_TRANSPORT_OUTPUT_BYTES
        )
        self.assertIn(b"GIT binary patch", raw)
        chunk = transport_module._STREAM_CHUNK_BYTES
        self.assertTrue(any(
            0x80 <= raw[offset] <= 0xBF
            for offset in range(chunk, len(raw), chunk)
        ))
        digest = TestTransport(self.large).staged_diff_sha256(
            str(self.large)
        )
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_a_small_staged_diff_is_byte_compatible_with_the_legacy(self):
        repo = _staged_repo(self.base, "small", {
            "base.txt": b"base\nna\xc3\xafve \xe2\x98\x83\n",
            "tiny.bin": b"\x00\x01\x02\xff" * 16,
        })
        transport = TestTransport(repo)
        # The pre-fix computation verbatim: the bounded capture through
        # ``_git``, then the strict UTF-8 text round trip.
        _, stdout, _ = transport._git(str(repo),
                                      ["diff", "--cached", "--binary"])
        legacy = hashlib.sha256(
            stdout.decode("utf-8").encode("utf-8")
        ).hexdigest()
        raw = _raw_staged_diff(repo, self.base / "small.diff")
        self.assertEqual(stdout, raw)
        self.assertIn(b"GIT binary patch", raw)
        self.assertEqual(transport.staged_diff_sha256(str(repo)), legacy)

    def test_invalid_utf8_from_real_git_refuses_with_the_legacy_message(
        self,
    ):
        # Reachable through real git: a file with no NUL byte is diffed as
        # TEXT, and --binary passes its raw bytes through, so a Latin-1
        # byte reaches the stream. Once at the head, once past the first
        # MiB (decoding must continue across every chunk).
        cases = {
            "early": {"latin1.txt": b"caf\xe9\n"},
            "late": {"a-euro.txt": EURO_LINE * 10000,
                     "z-latin1.txt": b"caf\xe9\n"},
        }
        for name, files in sorted(cases.items()):
            with self.subTest(case=name):
                repo = _staged_repo(self.base, "invalid-" + name, files)
                raw = _raw_staged_diff(
                    repo, self.base / ("invalid-%s.diff" % name)
                )
                with self.assertRaises(UnicodeDecodeError):
                    raw.decode("utf-8")
                if name == "late":
                    self.assertGreater(
                        raw.index(b"caf\xe9"),
                        transport_module.MAX_TRANSPORT_OUTPUT_BYTES,
                    )
                with self.assertRaises(
                    transport_module.DeliveryTransportError
                ) as caught:
                    TestTransport(repo).staged_diff_sha256(str(repo))
                self.assertEqual(str(caught.exception),
                                 INVALID_UTF8_MESSAGE)

    def test_a_sequence_truncated_at_eof_refuses(self):
        # The character starts in the first chunk and is cut off in the
        # second, at EOF: an incremental decoder that is never finalized
        # would accept it. The completed stream (a character split across
        # the boundary) binds as the raw bytes' sha256.
        prefix = b"+" * (transport_module._STREAM_CHUNK_BYTES - 1)
        with self.assertRaises(
            transport_module.DeliveryTransportError
        ) as caught:
            self.run_stand_in(_StandInDiffChild(prefix + EURO[:2]))
        self.assertEqual(str(caught.exception), INVALID_UTF8_MESSAGE)
        complete = prefix + EURO + b"\n"
        self.assertEqual(self.run_stand_in(_StandInDiffChild(complete)),
                         hashlib.sha256(complete).hexdigest())

    def test_a_failed_git_reports_its_exit_before_any_utf8_verdict(self):
        child = _StandInDiffChild(b"caf\xe9\n", returncode=1,
                                  stderr=b"fatal: boom\n")
        with self.assertRaises(
            transport_module.DeliveryTransportError
        ) as caught:
            self.run_stand_in(child)
        self.assertEqual(str(caught.exception),
                         "git diff failed (1): fatal: boom")
        # And from real git, with its own stderr head.
        missing = str(self.base / "missing")
        with self.assertRaises(
            transport_module.DeliveryTransportError
        ) as caught:
            TestTransport(missing).staged_diff_sha256(missing)
        self.assertRegex(str(caught.exception),
                         r"^git diff failed \(\d+\): \S")

    def test_the_digest_child_keeps_the_deadline_and_stderr_posture(self):
        payload = b"diff --git a/x b/x\n"
        child = _StandInDiffChild(payload)
        self.assertEqual(self.run_stand_in(child),
                         hashlib.sha256(payload).hexdigest())
        self.assertEqual(len(child.calls), 1)
        argv, kwargs = child.calls[0]
        self.assertEqual(argv[0], "git")
        self.assertEqual(argv[1:3], ["-C", str(self.base)])
        self.assertEqual(argv[3:], ["diff", "--cached", "--binary"])
        process = transport_module.subprocess
        self.assertIs(kwargs["stdout"], process.PIPE)
        self.assertIs(kwargs["stdin"], process.DEVNULL)
        # stderr is a temporary FILE, never a second pipe.
        self.assertNotIn(kwargs["stderr"],
                         (process.PIPE, process.STDOUT, None))
        self.assertTrue(callable(getattr(kwargs["stderr"], "fileno")))
        # No deadline: no timeout on the child or on the wait.
        self.assertNotIn("timeout", kwargs)
        self.assertEqual(child.waits, [None])

    def test_verb_closure_holds_on_the_digest_path(self):
        narrowed = tuple(verb for verb in transport_module.ALLOWED_GIT_VERBS
                         if verb != "diff")
        child = _StandInDiffChild(b"")
        with patch.object(transport_module, "ALLOWED_GIT_VERBS", narrowed):
            with self.assertRaises(
                transport_module.DeliveryTransportError
            ) as caught:
                self.run_stand_in(child)
        self.assertEqual(str(caught.exception),
                         "git verb 'diff' is outside the closed verb set")
        self.assertEqual(child.calls, [])

    def test_every_other_capture_keeps_the_output_bound(self):
        self.assertEqual(transport_module.MAX_TRANSPORT_OUTPUT_BYTES,
                         1048576)
        transport = TestTransport(self.large)
        path = str(self.large)
        empty_tree = git("hash-object", "-t", "tree", "/dev/null",
                         cwd=self.large)
        staged_tree = transport.write_tree(path)
        # The SAME oversized stream through the ordinary bounded path,
        # and a second verb: both still refuse with the existing message.
        for argv in (["diff", "--cached", "--binary"],
                     ["diff-tree", "-r", "-p", "--binary", empty_tree,
                      staged_tree]):
            with self.subTest(verb=argv[0]):
                with self.assertRaises(
                    transport_module.DeliveryTransportError
                ) as caught:
                    transport._git(path, argv)
                self.assertEqual(
                    str(caught.exception),
                    "git produced more than 1048576 bytes; refusing to"
                    " parse a partial output",
                )
        # No parameter exists through which a caller could relax it.
        real = transport_module.DeliveryTransport
        self.assertEqual(list(inspect.signature(real._run).parameters),
                         ["self", "argv", "cwd", "stdin_bytes"])
        self.assertEqual(list(inspect.signature(real._git).parameters),
                         ["self", "path", "argv", "allow_fail", "config"])
        self.assertEqual(
            list(inspect.signature(real.staged_diff_sha256).parameters),
            ["self", "path"],
        )

    def test_commit_binds_a_candidate_over_the_output_bound(self):
        # The blocker itself: COMMIT binds a staged candidate larger than
        # the captured-output bound, through the installed guards, instead
        # of returning retry before any receipt exists.
        fx = DeliveryFixture(self, hooks=True)
        (fx.work / "src" / "blob.bin").write_bytes(
            b"\x00" + _incompressible(600000, b"e2e")
        )
        (fx.work / "src" / "euro.txt").write_bytes(EURO_LINE * 6000)
        git("add", "-A", cwd=fx.work)
        raw = _raw_staged_diff(fx.work,
                               Path(fx.temp.name) / "candidate.diff")
        self.assertGreater(len(raw),
                           transport_module.MAX_TRANSPORT_OUTPUT_BYTES)
        fx.authorize(validity_seconds=3600)
        outcome = fx.machine.advance("prd-test")
        record = fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE,
                         record["steps"][COMMIT_STEP])
        receipt = record["steps"][COMMIT_STEP]["receipt"]
        self.assertEqual(receipt["state"], auth.RECEIPT_SUCCEEDED)
        self.assertEqual(receipt["binding"]["staged_sha256"],
                         hashlib.sha256(raw).hexdigest())


class StoreTests(unittest.TestCase):
    def test_store_fails_closed_and_prunes_only_terminal(self):
        fx = DeliveryFixture(self)
        fx.authorize()
        path = Path(fx.store.path)
        os.chmod(path, 0o640)
        with self.assertRaises(store_module.StoreError):
            fx.store.load()
        os.chmod(path, 0o600)
        document = fx.store.load()
        self.assertEqual(sorted(document["deliveries"]), ["prd-test"])
        for index in range(store_module.MAX_PR_DELIVERY_RECORDS - 1):
            record = copy.deepcopy(document["deliveries"]["prd-test"])
            record["delivery_id"] = "prd-%03d" % index
            record["authority_digest_sha256"] = auth.authority_digest(record)
            for receipt_holder in record["steps"].values():
                receipt_holder["receipt"] = None
            ok, _, _ = store_module.add_delivery(document, record)
            self.assertTrue(ok)
        extra = copy.deepcopy(document["deliveries"]["prd-test"])
        extra["delivery_id"] = "prd-extra"
        extra["authority_digest_sha256"] = auth.authority_digest(extra)
        ok, problem, pruned = store_module.add_delivery(document, extra)
        self.assertFalse(ok)
        self.assertEqual(problem, store_module.PROBLEM_STORE_FULL)
        self.assertEqual(pruned, 0)
        path.write_text("{not json")
        with self.assertRaises(store_module.StoreError):
            fx.store.load()

    def test_workflow_store_is_never_touched(self):
        fx = DeliveryFixture(self)
        fx.authorize()
        fx.machine.advance("prd-test")
        names = sorted(os.listdir(fx.store.directory))
        self.assertNotIn("workflows.json", names)
        self.assertIn(store_module.STORE_FILE_NAME, names)
        mode = stat.S_IMODE(os.stat(fx.store.path).st_mode)
        self.assertEqual(mode, 0o600)


class EvidenceCollectionTests(unittest.TestCase):
    def _herd(self, status="COMPLETE", decision="APPROVE", token="APPROVE"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        reviews = root / ".herd" / "state" / "reviews"
        reviews.mkdir(parents=True)
        task_id = "20260904-150441-159120"
        review = reviews / ("%s-round-02.md" % task_id)
        review.write_text(
            "# Reviewer round 2\n\nReviewer: `reviewer1` / `pane`\n\n"
            "Protocol token: `%s`\n\n## Transcript\n\nHERD_DECISION: %s\n"
            % (token, token)
        )
        (reviews / ("%s-round-01.md" % task_id)).write_text(
            "# Reviewer round 1\n\nProtocol token: `REJECT`\n"
        )
        (reviews / "20260101-000000-abcdef-round-09.md").write_text(
            "# other task\n\nProtocol token: `APPROVE`\n"
        )
        (root / ".herd" / "state" / "task.json").write_text(json.dumps({
            "id": task_id, "status": status, "review_rounds": 2,
            "last_review_decision": decision,
            "last_review_file": str(review),
        }))
        return root, review

    def test_collects_the_latest_round_for_this_task_only(self):
        root, review = self._herd()
        document = delivery_evidence.collect(root, now=5.0)
        self.assertEqual(document["engineering_complete"]["status"],
                         "COMPLETE")
        self.assertEqual(document["reviewer_approve"]["round"], 2)
        self.assertEqual(document["reviewer_approve"]["review_file_name"],
                         review.name)
        self.assertEqual(
            document["reviewer_approve"]["review_file_sha256"],
            hashlib.sha256(review.read_bytes()).hexdigest(),
        )
        self.assertEqual(sorted(document), ["engineering_complete",
                                            "reviewer_approve"])

    def test_refuses_incomplete_task_and_non_approve(self):
        for kwargs in ({"status": "ACTIVE"}, {"decision": "REJECT"},
                       {"token": "REJECT"}, {"token": "ACCEPT"}):
            root, _ = self._herd(**kwargs)
            with self.assertRaises(delivery_evidence.EvidenceError):
                delivery_evidence.collect(root)


class CliTests(unittest.TestCase):
    def test_authorize_status_advance_revoke_through_the_cli(self):
        fx = DeliveryFixture(self)
        digest = fx.live_digest()
        args = fx.args()
        delivery_id = cli_module.authorize_cmd(
            args, confirmation_reader=lambda prompt: digest[:12],
            out=io.StringIO(),
        )
        self.assertTrue(delivery_id.startswith("prd-"))
        # The CLI builds the REAL transport; drive the rest through the
        # fixture's isolated one instead of letting gh run.
        boundary = boundary_module.PrDeliveryBoundary(
            machine_module.DeliveryMachine(fx.store, fx.transport, fx.clock)
        )
        status = boundary.status(delivery_id)
        self.assertEqual(status["phase"], auth.PHASE_AUTHORIZED)
        status = boundary.revoke(delivery_id, "human", "not today")
        self.assertEqual(status["phase"], auth.PHASE_REVOKED)
        self.assertEqual(status["next_action"]["action"],
                         boundary_module.NEXT_REVOKED)

    def test_non_interactive_ceremony_refuses(self):
        fx = DeliveryFixture(self)
        with patch("sys.stdin", io.StringIO("000000000000\n")):
            with self.assertRaises(cli_module.CeremonyError):
                cli_module.authorize_cmd(fx.args(), out=io.StringIO())
        self.assertEqual(fx.store.load()["deliveries"], {})


class MissionParentAttestationTests(unittest.TestCase):
    """Task 7, Stage 2 (Supervisor scope decision, condition 7): the receipt
    attestation path run END TO END through the REAL delivery machine —
    a Mission approved for ``github_pr``, a delivery authorized with that
    Mission as its parent, the machine driven to COMPLETE through the
    installed guards, and every stored receipt then attested through
    ``pr_delivery.mission_parent.attest_validated_receipt``: the unchanged
    ``validate_authorization`` / ``validate_receipt`` first, the read-only
    parent check, then the Mission Core's distinct operation. No mocked
    VALID string anywhere: the receipts are the machine's own. The
    attestation performs zero repository or transport action and changes
    no delivery record and no Mission authority."""

    def setUp(self):
        from test_mission_core import Clock, make_context, proposal
        from test_mission_state import contract
        from mission import decision as mission_decision
        from mission import service as mission_service
        from mission import store as mission_store
        self.fx = DeliveryFixture(self, hooks=True)
        self.mission_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.mission_dir.cleanup)
        self.mission_store = mission_store.MissionStore(
            os.path.join(self.mission_dir.name, "protected"))
        self.mission_clock = Clock(int(_NOW))
        self.mission_service = mission_service.MissionService(
            self.mission_store, self.mission_clock)
        self.context = make_context("1")
        request_id = self.mission_service.mint_request_id(self.context)
        self.mission_id = self.mission_service.propose(
            request_id, proposal(proof_contract=contract()), self.context)["mission_id"]
        envelope = mission_decision.HumanDecisionEnvelope(
            context=self.context,
            decision_id=self.mission_service.mint_decision_id(self.context),
            mission_id=self.mission_id, revision=1,
            decision=mission_decision.DECISION_APPROVE,
            received_at=self.mission_clock(),
            approved_action_scope=["engineering_change", "repository_read"],
            approved_delivery_targets=["github_pr"], expires_at=None)
        self.approved = self.mission_service.apply_human_decision(envelope)
        self.mission_clock.advance(1)
        self.mission_service.activate_proof_contract(
            self.mission_id, self.mission_service.mint_state_operation_id(self.context),
            0, self.context)
        # The delivery record names the Mission as its parent: the SAME
        # two-key optional block the P1-A6 seam has always read.
        self.fx.authorize(
            mission_workflow_id=self.mission_id,
            mission_authorization_digest=self.approved["authorization_digest_sha256"])

    def mission_bytes(self):
        with open(self.mission_store.path, "rb") as handle:
            return handle.read()

    def test_every_machine_receipt_is_attested_through_the_real_path(self):
        from pr_delivery import mission_parent
        from mission import state as mission_state
        outcome = self.fx.machine.advance("prd-test")
        record = self.fx.record()
        self.assertEqual(outcome, machine_module.OUTCOME_COMPLETE, record)
        self.assertEqual(record["phase"], auth.PHASE_COMPLETE)
        auth.validate_authorization(record)
        head = self.fx.head()
        created = len(self.fx.transport.created)
        delivery_bytes = self.fx.store.load()
        parent = mission_parent.parent_mission_authority(record, self.mission_service)
        self.assertTrue(parent["valid"], parent)
        authority_before = json.dumps({
            "a": self.mission_store.load()["authorizations"],
            "l": self.mission_store.load()["authority_ledger"]}, sort_keys=True)
        attested = {}
        for step in (COMMIT_STEP, PUSH_STEP, PR_CREATE):
            receipt = record["steps"][step]["receipt"]
            self.assertEqual(receipt["state"], auth.RECEIPT_SUCCEEDED, step)
            sequence = self.mission_service.get_state(self.mission_id)["sequence"]
            result = mission_parent.attest_validated_receipt(
                record, step, self.mission_service,
                self.mission_service.mint_state_operation_id(self.context), sequence,
                self.context)
            self.assertTrue(result["valid"], result)
            self.assertTrue(result["succeeded"], step)
            self.assertEqual(result["receipt_id"], receipt["receipt_id"])
            self.assertEqual(result["receipt_state"], auth.RECEIPT_SUCCEEDED)
            self.assertEqual(result["mission_id"], self.mission_id)
            self.assertEqual(result["authorization_id"], self.approved["authorization_id"])
            attested[step] = result["outcome"]["artifact_id"]
        # BASE_REFRESH was not needed: no receipt, so nothing to attest,
        # and the refusal is a projection, not a Mission call.
        absent = mission_parent.attest_validated_receipt(
            record, BASE_REFRESH, self.mission_service,
            self.mission_service.mint_state_operation_id(self.context),
            self.mission_service.get_state(self.mission_id)["sequence"], self.context)
        self.assertFalse(absent["valid"])
        self.assertEqual(absent["problem"], mission_parent.PROBLEM_RECEIPT_ABSENT)
        # The Mission holds exactly three attested references, each bound
        # to the machine's receipt by id and by the receipt contract's own
        # digest, to this delivery, to the record's authority digest and
        # to the Mission Authorization; all three are completed effects.
        state = self.mission_service.get_state(self.mission_id)["record"]
        marked = mission_state.attested_artifacts(state)
        self.assertEqual(sorted(a["artifact_id"] for a in marked), sorted(attested.values()))
        for step, artifact_id in attested.items():
            artifact = [a for a in marked if a["artifact_id"] == artifact_id][0]
            receipt = record["steps"][step]["receipt"]
            self.assertEqual(artifact["locator"], receipt["receipt_id"])
            self.assertEqual(artifact["content_digest_sha256"], auth.receipt_digest(receipt))
            marker = artifact["receipt_attestation"]
            self.assertEqual(marker["delivery_id"], record["delivery_id"])
            self.assertEqual(marker["step"], step)
            self.assertEqual(marker["receipt_state"], auth.RECEIPT_SUCCEEDED)
            self.assertEqual(marker["step_state"], auth.STEP_SUCCEEDED)
            self.assertEqual(marker["parent_authority_digest_sha256"],
                             record["authority_digest_sha256"])
            self.assertEqual(marker["authorization_digest_sha256"],
                             self.approved["authorization_digest_sha256"])
            self.assertTrue(mission_state.receipt_effect_completed(marker))
        head_cursor = self.mission_service.get_journal(self.mission_id)["cursor"]
        report = self.mission_service.observe(self.mission_id,
                                              {"cursor": head_cursor, "reports": {}})
        value = report["delivery_receipts"]["value"]
        self.assertEqual(sorted(value["effects_completed"]), sorted(attested.values()))
        self.assertEqual(value["unattested"], [])
        # Zero external action and no delivery-side change: the working
        # tree, the remote, the created PR count and the delivery store
        # are exactly as the machine left them; the Mission authority is
        # unchanged; the parent check is still read-only.
        self.assertEqual(self.fx.head(), head)
        self.assertEqual(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH), head)
        self.assertEqual(len(self.fx.transport.created), created)
        self.assertEqual(self.fx.store.load(), delivery_bytes)
        self.assertEqual(git("status", "--porcelain", cwd=self.fx.work), "")
        self.assertEqual(json.dumps({
            "a": self.mission_store.load()["authorizations"],
            "l": self.mission_store.load()["authority_ledger"]}, sort_keys=True),
            authority_before)
        before = self.mission_bytes()
        self.assertTrue(mission_parent.parent_mission_authority(
            record, self.mission_service)["valid"])
        self.assertEqual(self.mission_bytes(), before)
        # A receipt tampered after the machine wrote it is refused by the
        # unchanged validator before any Mission call; the Mission store
        # is byte-identical afterwards.
        tampered = copy.deepcopy(record)
        tampered["steps"][PUSH_STEP]["receipt"]["binding"]["source_commit"] = "0" * 40
        operation_id = self.mission_service.mint_state_operation_id(self.context)
        before = self.mission_bytes()
        with self.assertRaises(auth.AuthorizationError):
            mission_parent.attest_validated_receipt(
                tampered, PUSH_STEP, self.mission_service, operation_id,
                self.mission_service.get_state(self.mission_id)["sequence"], self.context)
        self.assertEqual(self.mission_bytes(), before)
        # Re-labelling the record as standalone after the fact (dropping the
        # Mission block and recomputing the authority digest) is refused by
        # the unchanged validator first: every stored receipt is bound to
        # the ORIGINAL authority digest. The genuine standalone shape is
        # covered by the P1-A6 regressions (test_mission_core F2) and the
        # consumer refusals (F7): it never reaches the Mission call.
        relabelled = dict(record, mission=None)
        relabelled["authority_digest_sha256"] = auth.authority_digest(relabelled)
        before = self.mission_bytes()
        with self.assertRaises(auth.AuthorizationError):
            mission_parent.attest_validated_receipt(
                relabelled, PUSH_STEP, self.mission_service, operation_id,
                self.mission_service.get_state(self.mission_id)["sequence"], self.context)
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(len(mission_state.attested_artifacts(
            self.mission_service.get_state(self.mission_id)["record"])), 3)


class DotsAttestedCeremonyTests(unittest.TestCase):
    """Task 8, user decision: the minimal Dots-attested P1-A6 ceremony,
    PRESENT plus ATTEST on the existing store, record, expiry, one-shot
    consumption and revocation. The phone human replies with a simple
    affirmative and never types a digest; the full binding lives in the
    presented proposal and the attested record. Operator-attested, NOT
    independently verified. No live delivery: the real local fixture
    repositories only, and nothing is advanced, pushed or opened."""

    REPLY_TO = "dots-message-0001"
    RELAY_REF = "relay-0001"

    def setUp(self):
        self.fx = DeliveryFixture(self)

    def present(self, **overrides):
        return cli_module.present_dots_cmd(self.fx.args(**overrides),
                                           out=io.StringIO())

    def attest(self, presented, reply="approved", digest=None, proposal=None,
               reply_to=REPLY_TO, relay_ref=RELAY_REF):
        args = SimpleNamespace(
            proposal_digest=(presented["proposal_digest_sha256"]
                             if digest is None else digest),
            reply_to=reply_to, relay_ref=relay_ref)
        stdin = json.dumps({
            "delivery_proposal": (presented["delivery_proposal"]
                                  if proposal is None else proposal),
            "relayed_reply": reply})
        return cli_module.attest_dots_cmd(args, stdin, out=io.StringIO())

    def refused(self, presented, **kwargs):
        with self.assertRaises(cli_module.CeremonyError) as caught:
            self.attest(presented, **kwargs)
        self.assertEqual(self.fx.store.load()["deliveries"], {})
        return str(caught.exception)

    def test_D1_records_honest_source_identity_and_relayed_confirmation(self):
        presented = self.present(mission_workflow_id="mn-" + "1" * 32,
                                 mission_authorization_digest="2" * 64)
        self.assertIn(presented["delivery_proposal"]["binding"]["candidate"][
            "identity_digest_sha256"], presented["display"])
        self.assertIn("no digest is ever typed", presented["reply"])
        delivery_id = self.attest(presented)
        record = self.fx.store.load()["deliveries"][delivery_id]
        auth.validate_authorization(record)
        human = record["human_authorization"]
        self.assertEqual(human["source"], "dots_operator_attested")
        self.assertNotEqual(human["source"], auth.AUTHORIZATION_SOURCE_LOCAL_TERMINAL)
        self.assertEqual(human["identity"],
                         "operator_attested_relay:outer_operator_relay")
        attestation = human["attestation"]
        self.assertEqual(attestation["confirmation"], "operator_relayed")
        self.assertEqual(attestation["provenance"],
                         "operator_attested_not_independently_verified")
        self.assertIn("could fabricate it", attestation["residual_risk"])
        self.assertIn("not verified authorship", attestation["residual_risk"])
        self.assertEqual(attestation["relayed_reply"], "approved")
        self.assertEqual(attestation["reply_to"], self.REPLY_TO)
        self.assertEqual(attestation["proposal_digest_sha256"],
                         presented["proposal_digest_sha256"])
        # The FULL binding: candidate, Mission, revision, scope, target, expiry.
        binding = presented["delivery_proposal"]["binding"]
        self.assertEqual(record["candidate"]["identity_digest_sha256"],
                         self.fx.live_digest())
        for key in auth.DELIVERY_PROPOSAL_BINDING_KEYS:
            self.assertEqual(record[key], binding[key], key)
        self.assertEqual(record["mission"]["workflow_id"], "mn-" + "1" * 32)
        self.assertEqual(record["expiration"]["expires_at"],
                         presented["delivery_proposal"]["expires_at"])
        self.assertEqual(record["phase"], auth.PHASE_AUTHORIZED)

    def test_D2_only_the_whole_affirmative_counts_and_refusals_write_nothing(self):
        presented = self.present()
        digest = presented["proposal_digest_sha256"]
        for reply in ("no", "not approved", "disapproved", "approved, thanks",
                      "\"approved\"", "'approved'", "he said approved",
                      "approved " + digest, digest, "", "   "):
            with self.subTest(reply=reply):
                self.assertIn("not an exact affirmative",
                              self.refused(presented, reply=reply))
        # The phone human's whole reply is just the word: no digest typed.
        self.assertTrue(self.attest(presented, reply="  Approved ").startswith("prd-"))

    def test_D3_ambiguous_or_substituted_candidates_are_refused(self):
        presented = self.present()
        other = self.present(title="a different delivery")
        for kwargs in ({"digest": other["proposal_digest_sha256"]},
                       {"reply_to": ""}, {"relay_ref": ""}):
            with self.subTest(kwargs=sorted(kwargs)):
                self.refused(presented, **kwargs)
        substituted = copy.deepcopy(presented["delivery_proposal"])
        substituted["binding"]["candidate"]["identity_digest_sha256"] = "9" * 64
        self.assertIn("ambiguous or substituted",
                      self.refused(presented, proposal=substituted))
        # The live candidate changed after presentation: refused even though
        # the reply links to the presented proposal exactly.
        (self.fx.work / "late.txt").write_text("staged after presentation\n")
        git("add", "late.txt", cwd=self.fx.work)
        self.assertIn("substituted or different candidate",
                      self.refused(presented))

    def test_D4_expiry_one_shot_and_revocation_still_hold(self):
        presented = self.present()
        stale = copy.deepcopy(presented["delivery_proposal"])
        # A deadline that has passed (deterministic: before presentation).
        stale["expires_at"] = stale["presented_at"] - 1
        self.assertIn("expired", self.refused(
            presented, proposal=stale,
            digest=auth.delivery_proposal_digest(stale)))
        delivery_id = self.attest(presented)
        with self.assertRaises(cli_module.CeremonyError) as caught:
            self.attest(presented)
        self.assertIn("already attested", str(caught.exception))
        self.assertEqual(list(self.fx.store.load()["deliveries"]), [delivery_id])
        boundary = boundary_module.PrDeliveryBoundary(self.fx.machine)
        status = boundary.revoke(delivery_id, "human", "not today")
        self.assertEqual(status["phase"], auth.PHASE_REVOKED)

    def test_D8_capacity_pruning_never_launders_a_revoked_proposal(self):
        """Round 18: attest, revoke, capacity pruning pressure, restart,
        then replay the identical proposal and reply BEFORE expiry: refused,
        no new AUTHORIZED record. Once the proposal's deadline passes, the
        record is reclaimable again, so capacity is never consumed for good."""
        presented = self.present()
        proposal_digest = presented["proposal_digest_sha256"]
        deadline = presented["delivery_proposal"]["expires_at"]
        # 1. attest
        attested_id = self.attest(presented)
        attested_at = self.fx.store.load()["deliveries"][attested_id][
            "human_authorization"]["authorized_at"]
        # 2. revoke
        self.fx.now[0] = attested_at + 10
        boundary = boundary_module.PrDeliveryBoundary(self.fx.machine)
        self.assertEqual(boundary.revoke(attested_id, "human", "no")["phase"],
                         auth.PHASE_REVOKED)
        # 3. capacity pruning pressure: fill the store with ordinary
        # terminal records that are all NEWER than the revoked one (so the
        # old oldest-first pruning would have evicted it first), then make
        # an ordinary insertion through the real ceremony.
        self.fx.now[0] = attested_at + 100
        self.fx.authorize(title="a filler delivery")
        boundary.revoke("prd-test", "human", "filler")
        with self.fx.store.lock():
            document = self.fx.store.load()
            filler = document["deliveries"]["prd-test"]
            for index in range(store_module.MAX_PR_DELIVERY_RECORDS - 2):
                record = copy.deepcopy(filler)
                record["delivery_id"] = "prd-filler-%03d" % index
                record["authority_digest_sha256"] = auth.authority_digest(record)
                ok, problem, _ = store_module.add_delivery(document, record)
                self.assertTrue(ok, problem)
            self.fx.store.save(document)
        self.assertEqual(len(self.fx.store.load()["deliveries"]),
                         store_module.MAX_PR_DELIVERY_RECORDS)
        other = self.present(title="another delivery under pressure")
        pressure_id = self.attest(other, reply_to="dots-message-0002",
                                  relay_ref="relay-0002")
        # 4. restart: a fresh store read from disk still holds the revoked
        # record (the ordinary insertion pruned a filler instead).
        restarted = store_module.DeliveryStore(store_module.store_directory())
        before = restarted.load()["deliveries"]
        self.assertEqual(len(before), store_module.MAX_PR_DELIVERY_RECORDS)
        self.assertIn(pressure_id, before)
        self.assertEqual(before[attested_id]["phase"], auth.PHASE_REVOKED)
        # 5. replay the identical proposal and reply before expiry.
        self.assertLess(time.time(), deadline)
        with self.assertRaises(cli_module.CeremonyError) as caught:
            self.attest(presented)
        self.assertIn("already attested", str(caught.exception))
        after = restarted.load()["deliveries"]
        self.assertEqual(sorted(after), sorted(before))
        self.assertEqual([
            delivery_id for delivery_id, record in after.items()
            if record["phase"] == auth.PHASE_AUTHORIZED
            and record["human_authorization"].get("attestation", {}).get(
                "proposal_digest_sha256") == proposal_digest
        ], [])
        # A store full of protected records refuses rather than pruning.
        protected = {"deliveries": {}}
        for index in range(store_module.MAX_PR_DELIVERY_RECORDS):
            record = copy.deepcopy(after[attested_id])
            record["delivery_id"] = "prd-protected-%03d" % index
            record["authority_digest_sha256"] = auth.authority_digest(record)
            protected["deliveries"][record["delivery_id"]] = record
        incoming = copy.deepcopy(after[pressure_id])
        incoming["delivery_id"] = "prd-incoming-before"
        incoming["authority_digest_sha256"] = auth.authority_digest(incoming)
        self.assertEqual(store_module.add_delivery(protected, incoming),
                         (False, store_module.PROBLEM_STORE_FULL, 0))
        # 6. after the deadline the expired record is reclaimable: an
        # ordinary insertion at deadline+1 evicts it (oldest first).
        with patch("time.time", return_value=deadline + 1):
            late = self.present(title="a delivery after the deadline")
            late_id = self.attest(late, reply_to="dots-message-0003",
                                  relay_ref="relay-0003")
        reclaimed = restarted.load()["deliveries"]
        self.assertNotIn(attested_id, reclaimed)
        self.assertIn(late_id, reclaimed)
        incoming = copy.deepcopy(reclaimed[late_id])
        incoming["delivery_id"] = "prd-incoming-after"
        incoming["authority_digest_sha256"] = auth.authority_digest(incoming)
        ok, problem, pruned = store_module.add_delivery(protected, incoming)
        self.assertEqual((ok, problem, pruned), (True, None, 1))
        # Replaying the original proposal after its deadline stays refused.
        with patch("time.time", return_value=deadline + 2):
            with self.assertRaises(cli_module.CeremonyError) as caught:
                self.attest(presented)
        self.assertIn("expired", str(caught.exception))

    def test_D9_the_dots_display_states_the_absolute_presentation_deadline(self):
        presented = self.present()
        deadline = presented["delivery_proposal"]["expires_at"]
        self.assertIn("Expires       : %s (absolute; 3600 seconds from"
                      " presentation, not from approval)" % time.strftime(
                          "%Y-%m-%dT%H:%M:%SZ", time.gmtime(deadline)),
                      presented["display"])
        self.assertNotIn("from authorization", presented["display"])
        # The terminal ceremony's wording is unchanged.
        shown = io.StringIO()
        digest = self.fx.live_digest()
        cli_module.assemble_authority(
            self.fx.transport, self.fx.args(), self.fx.clock(), "human",
            lambda prompt: digest[:cli_module.CONFIRMATION_CHARS], out=shown)
        self.assertIn("Expires       : 3600 seconds from authorization",
                      shown.getvalue())

    def test_D5_no_record_can_claim_local_terminal_or_a_person(self):
        presented = self.present()
        delivery_id = self.attest(presented)
        record = self.fx.store.load()["deliveries"][delivery_id]
        tampered_cases = (
            lambda r: r["human_authorization"].update(source="local_terminal"),
            lambda r: r["human_authorization"].update(identity="alice"),
            lambda r: r["human_authorization"]["attestation"].update(
                relayed_reply="not approved"),
            lambda r: r["human_authorization"]["attestation"].update(
                provenance="verified_human"),
            lambda r: r["human_authorization"].update(
                confirmation_digest_sha256="0" * 64),
            lambda r: r["candidate"].update(identity_digest_sha256="9" * 64),
            # A binding field the human never saw: only the proposal-digest
            # re-proof catches it.
            lambda r: r["pr_content"].update(title="a substituted title"),
        )
        for index, tamper in enumerate(tampered_cases):
            with self.subTest(case=index):
                changed = copy.deepcopy(record)
                tamper(changed)
                changed["authority_digest_sha256"] = auth.authority_digest(changed)
                with self.assertRaises(auth.AuthorizationError):
                    auth.validate_authorization(changed)

    def test_D6_local_terminal_and_its_tty_requirement_are_unchanged(self):
        with patch("sys.stdin", io.StringIO("000000000000\n")):
            with self.assertRaises(cli_module.CeremonyError) as caught:
                cli_module.authorize_cmd(self.fx.args(), out=io.StringIO())
        self.assertIn("interactive terminal", str(caught.exception))
        self.assertEqual(self.fx.store.load()["deliveries"], {})
        self.assertEqual(auth.AUTHORIZATION_SOURCES,
                         ("local_terminal", "dots_operator_attested"))

    def test_D7_an_engineering_approval_alone_yields_no_delivery(self):
        # A Mission engineering approval is not a delivery proposal: without
        # a presented delivery proposal there is nothing to attest, and
        # nothing is written.
        presented = self.present(mission_workflow_id="mn-" + "1" * 32,
                                 mission_authorization_digest="2" * 64)
        engineering_only = {"mission_id": "mn-" + "1" * 32,
                            "decision": "APPROVE", "relayed_reply": "approved"}
        with self.assertRaises(cli_module.CeremonyError):
            cli_module.attest_dots_cmd(
                SimpleNamespace(proposal_digest="2" * 64, reply_to="r",
                                relay_ref="x"),
                json.dumps(engineering_only), out=io.StringIO())
        self.assertEqual(self.fx.store.load()["deliveries"], {})
        self.assertNotIn("delivery_authority", presented["delivery_proposal"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
