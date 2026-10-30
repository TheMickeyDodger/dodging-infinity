"""The expected-step guard on pr_delivery's single-step method (owner decision, expected-step supplement, owner-decisions.md line 149; reports/owner-delivery-expected-step-supplement-20261029.md).

The approved condition (item 1): the caller supplies the exact expected step and the displayed delivery binding; the single-step method checks them against THE SAME LOADED RECORD it uses to select
its step, BEFORE any delivery-step effect; a missing, mismatched or stale binding REFUSES, and it must not dispatch a different step and merely report that afterwards. It is not a lock, transaction
isolation or an exactly-once proof: it refuses the selection of an unconfirmed step.

WHAT RUNS HERE, AND WHAT IS A STAND-IN:
  * REAL: DeliveryMachine.advance_once and advance, its load, the DeliveryStore (in a fresh directory under this run's TMPDIR, inside the pin), the record validator, and the records themselves
    (built by authorization.new_authorization from a written authority, then given their phase; every one is validated, so the store can hold it).
  * INERT STAND-INS: the step handlers (_step_base_refresh, _step_commit, _step_push, _step_pr_create, _observe_pr_update) are recorders in a test SUBCLASS that move the phase as a step would and
    perform nothing; the transport is an object on which EVERY attribute raises. For the whole module, pr_delivery's DeliveryTransport (in transport and cli), cli.build_machine and
    subprocess.Popen are replaced by raising tripwires. A tripwire guards only what is EXECUTED.
  * NOT RUN, an explicit evidence limit: tests/test_pr_delivery.py and tests/test_pr_delivery_guards.py, which exercise real Git; their coverage is unrun, never a pass.

WHAT IT PINS:
  (a) THE CHANGED STEP IS REFUSED BEFORE THE EFFECT: a confirmation admitted for COMMIT, the record moved by a concurrent driver at a handshake (no timers) so its phase selects PUSH, and the guarded
      call refuses: no handler ran, and the store file's bytes are unchanged.
  (b) THE MATCHING STEP RUNS, once, through the guard, for each office step of each kind.
  (c) UNCHANGED CALLERS: without `expected`, every branch behaves as before (each phase's handler, COMPLETE, BLOCKED, revoked, expired, the pr_update observation, and advance() running every
      remaining step). These tests also pass on the method BEFORE the guard existed.
  (d) A missing, malformed, stale or mismatched binding refuses with nothing done; the guard does not bypass revocation or expiry.
"""

import copy
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pr_delivery import authorization as auth  # noqa: E402
from pr_delivery import candidate as candidate_module  # noqa: E402
from pr_delivery import cli  # noqa: E402
from pr_delivery import machine as machine_module  # noqa: E402
from pr_delivery import store as store_module  # noqa: E402
from pr_delivery import transport as transport_module  # noqa: E402

from office_delivery import adapter as office  # noqa: E402

NOW = 1_800_000_000
DELIVERY = "prd-0123456789abcdef01234567"  # prd-<24 hex>: the id a pr_update record must carry
BASE = "a" * 40
PROBLEM = getattr(machine_module, "PROBLEM_EXPECTED_STEP_REFUSED", "pr_delivery_expected_step_refused")


class _Tripwire(Exception):
    pass


def _raise(*_args, **_kwargs):
    raise _Tripwire("a test path reached a REAL process or transport")


class _RaisingTransportClass(object):
    def __init__(self, *_args, **_kwargs):
        raise _Tripwire("a test path constructed the REAL DeliveryTransport")


_saved = {}
_directories = []


def setUpModule():
    _saved.update(transport=transport_module.DeliveryTransport, cli_transport=cli.DeliveryTransport, build_machine=cli.build_machine, popen=subprocess.Popen)
    transport_module.DeliveryTransport = _RaisingTransportClass
    cli.DeliveryTransport = _RaisingTransportClass
    cli.build_machine = _raise
    subprocess.Popen = _raise


def tearDownModule():
    transport_module.DeliveryTransport = _saved["transport"]
    cli.DeliveryTransport = _saved["cli_transport"]
    cli.build_machine = _saved["build_machine"]
    subprocess.Popen = _saved["popen"]
    for directory in _directories:
        shutil.rmtree(directory, ignore_errors=True)


class RaisingTransport(object):
    """Every attribute raises: the guard tests' handlers never reach the transport, and nothing else may."""

    def __getattr__(self, name):
        raise _Tripwire("the transport was reached: %s" % name)


class InertMachine(machine_module.DeliveryMachine):
    """The REAL single-step method and drive; only the step handlers are stand-ins, which move the phase as the step would and perform nothing."""

    def __init__(self, store, now=NOW):
        super(InertMachine, self).__init__(store, RaisingTransport(), lambda: now)
        self.ran = []

    def _move(self, record, step, *phases):
        self.ran.append(step)
        for phase in phases:
            auth.apply_transition(record, phase, self.clock())
        self._persist(record)

    def _step_base_refresh(self, record):
        self._move(record, auth.STEP_BASE_REFRESH, auth.PHASE_BASE_CURRENT)

    def _step_commit(self, record):
        self._move(record, auth.STEP_COMMIT, auth.PHASE_COMMITTED)

    def _step_push(self, record):
        self._move(record, auth.STEP_PUSH, auth.PHASE_PUSHED)

    def _step_pr_create(self, record):
        self._move(record, auth.STEP_PR_CREATE, auth.PHASE_PR_OPENED, auth.PHASE_COMPLETE)

    def _observe_pr_update(self, record):
        self._move(record, "OBSERVATION", auth.PHASE_COMPLETE)


def _entries():
    return [{"path": "src/guarded.py", "status": auth.CANDIDATE_STATUS_MODIFIED, "mode": "100644", "blob": "1" * 40}]


def authority(root, mode=auth.MODE_PULL_REQUEST):
    entries = _entries()
    digest = candidate_module.identity_digest(entries)
    evidence_common = {"candidate_identity_digest_sha256": digest, "base_oid": BASE, "recorded_at": NOW - 120}
    document = {
        "revision": 1,
        "previous_delivery_id": None,
        "workflow_identity": {"workflow_id": "wf-0123456789abcdef", "engineering_task_id": "20261029-000000-abcdef"},
        "mission": None,
        "repository": {"realpath": root + "/repo", "git_dir_realpath": root + "/repo/.git", "canonical_host": "github.com", "owner": "octo", "repo": "repo",
                       "repository_url": "https://github.com/octo/repo"},
        "remote": {"name": "origin", "url_exact": "https://github.com/octo/repo.git", "url_fetch": "https://github.com/octo/repo.git", "url_push": "https://github.com/octo/repo.git",
                   "repository_url": "https://github.com/octo/repo"},
        "mode": mode,
        "source": {"branch": "feature/guarded", "ref": "refs/heads/feature/guarded"},
        "target_base": {"branch": "main", "ref": "refs/heads/main"},
        "original_baseline": {"ref": "refs/heads/main", "commit_sha": BASE},
        "candidate": {"identity_digest_sha256": digest, "entry_count": len(entries), "entries": entries},
        "evidence": {
            "engineering_complete": dict(evidence_common, task_id="20261029-000000-abcdef", status=auth.EVIDENCE_ENGINEERING_STATUS_COMPLETE, task_state_sha256="2" * 64),
            "reviewer_approve": dict(evidence_common, task_id="20261029-000000-abcdef", round=1, review_file_name="review-round-1.md", review_file_sha256="3" * 64,
                                     decision=auth.EVIDENCE_REVIEW_DECISION_APPROVE),
            "independent_verification": dict(evidence_common, command_argv=["/usr/bin/true"], exit_status=0, log_sha256="4" * 64, log_bytes=0, ran_at=NOW - 130),
        },
        "allowed_actions": list(auth.PR_UPDATE_STEPS if mode == auth.MODE_PR_UPDATE else auth.STEPS),
        "committer": {"name": "Delivery Human", "email": "human@example.com"},
        "reverification": {"argv": ["/usr/bin/true"]},
        "pr_content": {"title": "Guarded change", "objective": "", "architecture_notes": "", "nonblocking_risks": ""},
        "human_authorization": {"identity": "travis", "source": auth.AUTHORIZATION_SOURCE_LOCAL_TERMINAL, "authorized_at": NOW - 60, "confirmation_digest_sha256": "5" * 64},
        "expiration": {"policy": auth.EXPIRATION_POLICY_ABSOLUTE, "expires_at": NOW + 3600},
    }
    if mode == auth.MODE_PR_UPDATE:
        document["delivery_id"] = DELIVERY
        document["pull_request_number"] = 7
        document["staged_sha256"] = "6" * 64
        # a pr_update candidate sits on the pull request HEAD: its baseline is the head branch ref
        document["original_baseline"] = {"ref": document["source"]["ref"], "commit_sha": BASE}
    return document


class Store(object):
    """A REAL DeliveryStore in a fresh directory under this run's TMPDIR."""

    def __init__(self):
        self.directory = tempfile.mkdtemp(prefix="pr-delivery-guard-")
        _directories.append(self.directory)
        os.chmod(self.directory, 0o700)
        self.store = store_module.DeliveryStore(os.path.realpath(self.directory))
        self.root = os.path.realpath(self.directory)

    def record(self, mode=auth.MODE_PULL_REQUEST, phase=auth.PHASE_AUTHORIZED, revoked=False, blocked=False):
        rec = auth.new_authorization(DELIVERY, authority(self.root, mode), NOW - 30)
        if phase != auth.PHASE_AUTHORIZED:
            rec["phase"] = phase
        if revoked:
            rec["revocation"] = {"revoked": True, "revoked_at": NOW - 10, "revoked_by": "travis", "reason": "test"}
        if blocked:
            rec["blocker"] = {"problem": "pr_delivery_transport_failure", "detail": "test", "recorded_at": NOW - 10}
        auth.validate_authorization(rec)
        return rec

    def put(self, rec):
        document = self.store.load()
        document["deliveries"][rec["delivery_id"]] = copy.deepcopy(rec)
        self.store.save(document)

    def stored(self):
        return self.store.load()["deliveries"][DELIVERY]

    def file_bytes(self):
        with open(self.store.path, "rb") as handle:
            return handle.read()


def binding(rec, step):
    return {"delivery_id": rec["delivery_id"], "revision": rec["revision"], "candidate_identity_digest_sha256": rec["candidate"]["identity_digest_sha256"], "step": step}


class FixtureTests(unittest.TestCase):
    def test_every_fixture_record_is_one_the_real_validator_and_store_accept(self):
        s = Store()
        for mode, phases in ((auth.MODE_PULL_REQUEST, (auth.PHASE_AUTHORIZED, auth.PHASE_BASE_CURRENT, auth.PHASE_COMMITTED, auth.PHASE_PUSHED, auth.PHASE_COMPLETE)),
                             (auth.MODE_PR_UPDATE, (auth.PHASE_AUTHORIZED, auth.PHASE_COMMITTED, auth.PHASE_PUSHED))):
            for phase in phases:
                rec = s.record(mode, phase)
                s.put(rec)
                self.assertEqual(s.stored()["phase"], phase)

    def test_the_tripwires_are_installed(self):
        for reach in (cli.build_machine, transport_module.DeliveryTransport, cli.DeliveryTransport, subprocess.Popen):
            with self.assertRaises(_Tripwire):
                reach()


class ChangedStepRefusedBeforeEffectTests(unittest.TestCase):
    def test_THE_INTERLEAVING_a_driver_moves_the_record_after_the_check_and_the_guarded_call_refuses_before_any_handler(self):
        s = Store()
        s.put(s.record(phase=auth.PHASE_BASE_CURRENT))
        confirmation = binding(s.stored(), auth.STEP_COMMIT)
        self.assertEqual(office.check_confirmation(s.stored(), NOW, confirmation), {"admitted": True, "step": auth.STEP_COMMIT})
        s.put(s.record(phase=auth.PHASE_COMMITTED))  # the concurrent driver, at the handshake between the check and the method's own read
        before = s.file_bytes()
        machine = InertMachine(s.store)
        with self.assertRaises(machine_module.MachineError) as refused:
            machine.advance_once(DELIVERY, expected=confirmation)
        self.assertEqual(refused.exception.problem, PROBLEM)
        self.assertEqual(machine.ran, [], "no handler ran")
        self.assertEqual(s.file_bytes(), before, "nothing was written")

    def test_a_terminal_record_selects_no_step_so_any_expected_step_refuses(self):
        s = Store()
        s.put(s.record(phase=auth.PHASE_COMPLETE))
        machine = InertMachine(s.store)
        with self.assertRaises(machine_module.MachineError) as refused:
            machine.advance_once(DELIVERY, expected=binding(s.stored(), auth.STEP_PR_CREATE))
        self.assertEqual((refused.exception.problem, machine.ran), (PROBLEM, []))


class ScriptedStore(store_module.DeliveryStore):
    """A REAL DeliveryStore whose FIRST load returns one document and every later load another: a second read that differs from the first."""

    def __init__(self, directory, first, later):
        super(ScriptedStore, self).__init__(directory)
        self.documents = [first, later]
        self.loads = 0

    def load(self):
        self.loads += 1
        return copy.deepcopy(self.documents[0 if self.loads == 1 else 1])

    def save(self, document):
        self.documents[1] = copy.deepcopy(document)


class SameRecordTests(unittest.TestCase):
    def test_the_guard_checks_the_very_record_the_selection_uses_not_a_second_read(self):
        s = Store()
        first = {"pr_delivery_store_schema_version": 1, "deliveries": {DELIVERY: s.record(phase=auth.PHASE_BASE_CURRENT)}}
        later = {"pr_delivery_store_schema_version": 1, "deliveries": {DELIVERY: s.record(phase=auth.PHASE_COMMITTED)}}
        machine = InertMachine(ScriptedStore(s.root, first, later))
        expected = binding(first["deliveries"][DELIVERY], auth.STEP_COMMIT)
        self.assertEqual(machine.advance_once(DELIVERY, expected=expected), machine_module.OUTCOME_ADVANCED)
        self.assertEqual(machine.ran, [auth.STEP_COMMIT], "the step selected from the record the guard checked")


class MatchingStepTests(unittest.TestCase):
    def test_the_matching_step_runs_once_through_the_guard_for_each_office_step_of_each_kind(self):
        for mode, phase, step in ((auth.MODE_PULL_REQUEST, auth.PHASE_BASE_CURRENT, auth.STEP_COMMIT), (auth.MODE_PULL_REQUEST, auth.PHASE_COMMITTED, auth.STEP_PUSH),
                                  (auth.MODE_PULL_REQUEST, auth.PHASE_PUSHED, auth.STEP_PR_CREATE), (auth.MODE_PR_UPDATE, auth.PHASE_AUTHORIZED, auth.STEP_COMMIT),
                                  (auth.MODE_PR_UPDATE, auth.PHASE_COMMITTED, auth.STEP_PUSH)):
            s = Store()
            s.put(s.record(mode, phase))
            machine = InertMachine(s.store)
            outcome = machine.advance_once(DELIVERY, expected=binding(s.stored(), step))
            self.assertEqual((outcome, machine.ran), (machine_module.OUTCOME_ADVANCED, [step]), (mode, phase))

    def test_expected_is_keyword_only_and_defaults_to_none(self):
        parameter = inspect.signature(machine_module.DeliveryMachine.advance_once).parameters["expected"]
        self.assertEqual((parameter.kind, parameter.default), (inspect.Parameter.KEYWORD_ONLY, None))


class RefusedBindingTests(unittest.TestCase):
    def assert_refused(self, s, expected):
        before = s.file_bytes()
        machine = InertMachine(s.store)
        with self.assertRaises(machine_module.MachineError) as refused:
            machine.advance_once(DELIVERY, expected=expected)
        self.assertEqual(refused.exception.problem, PROBLEM, expected)
        self.assertEqual(machine.ran, [], expected)
        self.assertEqual(s.file_bytes(), before, expected)

    def test_a_missing_malformed_stale_or_mismatched_binding_refuses_with_nothing_done(self):
        s = Store()
        s.put(s.record(phase=auth.PHASE_BASE_CURRENT))
        good = binding(s.stored(), auth.STEP_COMMIT)
        for bad in (
            {}, "COMMIT", [good], dict(good, extra=1),
            {**good, 1: "x"}, {1: "x"},  # keys of mixed types refuse, never raise (the pattern of round 42's advisory 3)
            {k: v for k, v in good.items() if k != "step"},
            {k: v for k, v in good.items() if k != "revision"},
            dict(good, delivery_id="prd-" + "f" * 24),
            dict(good, revision=2),
            dict(good, revision="1"),
            dict(good, revision=True),
            dict(good, candidate_identity_digest_sha256="e" * 64),
            dict(good, step=auth.STEP_PUSH),
            dict(good, step=auth.STEP_BASE_REFRESH),
        ):
            self.assert_refused(s, bad)

    def test_the_guard_does_not_bypass_revocation_or_expiry(self):
        s = Store()
        s.put(s.record(phase=auth.PHASE_BASE_CURRENT, revoked=True))
        machine = InertMachine(s.store)
        self.assertEqual(machine.advance_once(DELIVERY, expected=binding(s.stored(), auth.STEP_COMMIT)), machine_module.OUTCOME_REVOKED)
        self.assertEqual((machine.ran, s.stored()["phase"]), ([], auth.PHASE_REVOKED))
        s2 = Store()
        s2.put(s2.record(phase=auth.PHASE_BASE_CURRENT))
        late = InertMachine(s2.store, now=NOW + 7200)
        self.assertEqual(late.advance_once(DELIVERY, expected=binding(s2.stored(), auth.STEP_COMMIT)), machine_module.OUTCOME_BLOCKED)
        self.assertEqual((late.ran, s2.stored()["phase"]), ([], auth.PHASE_BLOCKED))


class UnchangedCallerTests(unittest.TestCase):
    """No `expected`: the behaviour every existing caller has. These pass on the method before the guard existed, and must after."""

    def test_each_phase_dispatches_its_own_handler(self):
        for mode, phase, step in ((auth.MODE_PULL_REQUEST, auth.PHASE_AUTHORIZED, auth.STEP_BASE_REFRESH), (auth.MODE_PULL_REQUEST, auth.PHASE_BASE_CURRENT, auth.STEP_COMMIT),
                                  (auth.MODE_PULL_REQUEST, auth.PHASE_COMMITTED, auth.STEP_PUSH), (auth.MODE_PULL_REQUEST, auth.PHASE_PUSHED, auth.STEP_PR_CREATE),
                                  (auth.MODE_PR_UPDATE, auth.PHASE_AUTHORIZED, auth.STEP_COMMIT), (auth.MODE_PR_UPDATE, auth.PHASE_COMMITTED, auth.STEP_PUSH),
                                  (auth.MODE_PR_UPDATE, auth.PHASE_PUSHED, "OBSERVATION")):
            s = Store()
            s.put(s.record(mode, phase))
            machine = InertMachine(s.store)
            self.assertEqual((machine.advance_once(DELIVERY), machine.ran), (machine_module.OUTCOME_ADVANCED, [step]), (mode, phase))

    def test_complete_and_blocked_records_run_nothing(self):
        s = Store()
        s.put(s.record(phase=auth.PHASE_COMPLETE))
        machine = InertMachine(s.store)
        self.assertEqual((machine.advance_once(DELIVERY), machine.ran), (machine_module.OUTCOME_COMPLETE, []))
        s2 = Store()
        s2.put(s2.record(phase=auth.PHASE_BLOCKED, blocked=True))
        blocked = InertMachine(s2.store)
        self.assertEqual((blocked.advance_once(DELIVERY), blocked.ran), (machine_module.OUTCOME_BLOCKED, []))

    def test_a_revoked_record_is_moved_to_REVOKED_and_an_expired_one_is_blocked(self):
        s = Store()
        s.put(s.record(phase=auth.PHASE_BASE_CURRENT, revoked=True))
        machine = InertMachine(s.store)
        self.assertEqual(machine.advance_once(DELIVERY), machine_module.OUTCOME_REVOKED)
        self.assertEqual((machine.ran, s.stored()["phase"]), ([], auth.PHASE_REVOKED))
        s2 = Store()
        s2.put(s2.record(phase=auth.PHASE_BASE_CURRENT))
        late = InertMachine(s2.store, now=NOW + 7200)
        self.assertEqual(late.advance_once(DELIVERY), machine_module.OUTCOME_BLOCKED)
        self.assertEqual((late.ran, s2.stored()["phase"]), ([], auth.PHASE_BLOCKED))

    def test_advance_still_runs_every_remaining_step(self):
        s = Store()
        s.put(s.record(phase=auth.PHASE_AUTHORIZED))
        machine = InertMachine(s.store)
        self.assertEqual(machine.advance(DELIVERY), machine_module.OUTCOME_COMPLETE)
        self.assertEqual(machine.ran, [auth.STEP_BASE_REFRESH, auth.STEP_COMMIT, auth.STEP_PUSH, auth.STEP_PR_CREATE])


if __name__ == "__main__":
    unittest.main()
