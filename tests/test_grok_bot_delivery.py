"""Tests for the Grok Bot delivery ceremony relay (``grok_bot.delivery``),
Task 8 slice 3: the SEPARATE, exact commit / push / pull-request flow,
proposed, bound and approved through pr_delivery's own ``present-dots`` and
``attest-dots``, and NEVER performed.

What is REAL here: the adapter, its presentation receipts, pr_delivery's
``present_dots_cmd`` / ``attest_dots_cmd`` / ``_mint``, its authorization
validator, its store (in a temporary directory) and its status projection.

What is SYNTHETIC, labelled so:

- ``RecordingDeliveryTransport`` stands in for pr_delivery's WHOLE
  transport, git half included (it does not subclass the real one). It
  answers ONLY the read verbs the Dots ceremony uses, from a scripted
  repository, and records every call. Any other verb (every git or GitHub
  effect the four steps would need) is recorded and raises, so nothing can
  be performed even by mistake.
- ``ProcessSeams`` makes that STRUCTURAL: before each test it replaces every
  process seam (the real transport's runners and reverification,
  ``subprocess.Popen``, the ``os`` process primitives) with a recorder that
  raises, and asserts in cleanup that none was reached. No git or ``gh``
  process can start from this module, no repository is touched and nothing
  reaches a network, even under a mutant. This module imports no process,
  socket or HTTP machinery and none of the existing real-Git delivery
  fixtures (``tests/test_pr_delivery.py`` and friends), which is pinned.
- ``pr_delivery.cli.build_machine`` is patched, for each test only, to
  return a real ``DeliveryMachine`` over that double. Production keeps
  ``pr_delivery/cli.py`` as the only site constructing the real
  ``DeliveryTransport()``.
- The scripted Operator and run bridge of ``tests/test_grok_bot.py`` for the
  engineering Mission, and every "approved" reply, are test data, not a live
  Grok Bot conversation.

Step names stay UPPERCASE (pr_delivery's own constants), as in
``tests/test_pr_delivery.py``, for the hermetic-git call guard.

Termination: the SIGALRM watchdog of ``tests/test_grok_bot.py`` bounds every
test; nothing else blocks.
"""

import ast
import json
import math
import os
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import unittest  # noqa: E402

from pr_delivery import authorization as delivery_auth  # noqa: E402
from pr_delivery import cli as delivery_cli  # noqa: E402
from pr_delivery import transport as delivery_transport  # noqa: E402
from pr_delivery.machine import DeliveryMachine  # noqa: E402
from pr_delivery.store import DeliveryStore  # noqa: E402

from grok_bot import adapter as adapter_module  # noqa: E402
from grok_bot import delivery as delivery_module  # noqa: E402
from grok_bot import index as index_module  # noqa: E402

from test_grok_bot import EVIDENCE, Fixture, leaf_lines  # noqa: E402

ORIGINAL_BUILD_MACHINE = delivery_cli.build_machine

BASELINE = "a" * 40
REMOTE_URL = "https://github.com/octo/repo.git"
SOURCE_REF = "refs/heads/feature/grok-delivery"
ENTRY_COUNT = 60  # more than pr_delivery's own display shows (50)


def raw_candidate(count, offset=0):
    """``git diff-index --raw -z`` bytes for ``count`` modified files. A
    candidate entry carries the NEW blob, so ``offset`` changes the
    candidate's identity."""
    records = []
    for index in range(count):
        old = "%040d" % 0
        new = "%040d" % (index + 1 + offset)
        records.append(b":100644 100644 " + old.encode() + b" " + new.encode()
                       + b" M\0" + ("src/file_%03d.py" % index).encode() + b"\0")
    return b"".join(records)


class RecordingDeliveryTransport(object):
    """SYNTHETIC transport: the Dots ceremony's read verbs only, answered
    from a scripted repository; EVERY call recorded; any other verb is
    recorded and raises, so no effect can be performed."""

    READS = frozenset({
        "toplevel", "git_dir", "symbolic_ref_head", "remote_url",
        "remote_fetch_url", "remote_push_url", "head_oid", "ls_remote",
        "is_ancestor", "status_porcelain", "diff_index_raw", "config_get",
    })

    def __init__(self, repo):
        self.repo = repo
        self.calls = []
        self.raw = raw_candidate(ENTRY_COUNT)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def verb(*args):
            self.calls.append(name)
            if name not in self.READS:
                raise AssertionError("a delivery effect was attempted: %s" % name)
            return self.answer(name, args)
        return verb

    def answer(self, name, args):
        if name in ("toplevel",):
            return self.repo
        if name == "git_dir":
            return os.path.join(self.repo, ".git")
        if name == "symbolic_ref_head":
            return SOURCE_REF
        if name in ("remote_url", "remote_fetch_url", "remote_push_url"):
            return REMOTE_URL
        if name in ("head_oid", "ls_remote"):
            return BASELINE
        if name == "is_ancestor":
            return True
        if name == "status_porcelain":
            return "".join("M  src/file_%03d.py\n" % i for i in range(ENTRY_COUNT))
        if name == "diff_index_raw":
            return self.raw
        if name == "config_get":
            return {"user.name": "Delivery Human",
                    "user.email": "human@example.com"}[args[1]]
        raise AssertionError(name)


class ProcessSeams(object):
    """STRUCTURAL containment: every seam through which this process could
    start another one (the real delivery transport's runners, its
    reverification, ``subprocess.Popen`` and the ``os`` process
    primitives) is replaced for the duration of a test by a recorder that
    RAISES, and the test's cleanup asserts none was reached. A guard that
    a mutant disables therefore still cannot start a git or gh process.
    ``subprocess`` is reached through the delivery transport's own module
    reference, so this test module imports no process machinery itself."""

    OS_PRIMITIVES = ("system", "popen", "fork", "forkpty", "posix_spawn",
                     "posix_spawnp", "execl", "execle", "execlp", "execlpe",
                     "execv", "execve", "execvp", "execvpe", "spawnl",
                     "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
                     "spawnvp", "spawnvpe")
    SUBPROCESS_ENTRIES = ("Popen", "run", "call", "check_call", "check_output",
                          "getoutput", "getstatusoutput")
    TRANSPORT_RUNNERS = ("_run", "_git", "_gh", "run_reverification")

    def __init__(self, case):
        self.calls = []
        targets = [(delivery_transport.subprocess, name, "subprocess." + name)
                   for name in self.SUBPROCESS_ENTRIES]
        targets += [(os, name, "os." + name) for name in self.OS_PRIMITIVES
                    if hasattr(os, name)]
        targets += [(delivery_transport.DeliveryTransport, name,
                     "DeliveryTransport." + name)
                    for name in self.TRANSPORT_RUNNERS]
        for owner, attribute, label in targets:
            patcher = mock.patch.object(owner, attribute, self.refuser(label))
            patcher.start()
            case.addCleanup(patcher.stop)
        self.patched = [label for _, _, label in targets]
        case.addCleanup(lambda: case.assertEqual(
            self.calls, [], "a process seam was reached"))

    def refuser(self, label):
        def refused(*args, **kwargs):
            self.calls.append(label)
            raise AssertionError("process seam reached in a delivery test: %s"
                                 % label)
        return refused

    def take(self):
        calls, self.calls[:] = list(self.calls), []
        return calls


class DeliveryFixture(Fixture):
    """The slice-1 fixture plus pr_delivery over the recording double,
    with every process seam contained FIRST."""

    def setUp(self):
        self.seams = ProcessSeams(self)
        super(DeliveryFixture, self).setUp()
        base = os.path.realpath(self.tmp.name)
        self.repo = os.path.join(base, "work")
        os.makedirs(self.repo)
        self.evidence = os.path.join(base, "herd-evidence.json")
        with open(self.evidence, "w") as handle:
            json.dump({
                "engineering_complete": {
                    "task_id": "20261007-113755-ce3f43", "status": "COMPLETE",
                    "task_state_sha256": "a" * 64, "recorded_at": 1_799_999_000},
                "reviewer_approve": {
                    "task_id": "20261007-113755-ce3f43", "round": 2,
                    "review_file_name": "20261007-113755-ce3f43-round-02.md",
                    "review_file_sha256": "b" * 64, "decision": "APPROVE",
                    "recorded_at": 1_799_999_000},
            }, handle)
        self.log = os.path.join(base, "verification.log")
        with open(self.log, "wb") as handle:
            handle.write(b"suite: OK\n")
        self.delivery_store_dir = os.path.join(base, "deliveries")
        self.transport = RecordingDeliveryTransport(self.repo)
        self.machine = DeliveryMachine(DeliveryStore(self.delivery_store_dir),
                                       self.transport, lambda: 1_800_000_000.0)
        patcher = mock.patch.object(delivery_cli, "build_machine",
                                    lambda store_dir=None: self.machine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def present_arguments(self, **changes):
        arguments = {
            "repo": self.repo, "workflow_id": "wf-grok-delivery",
            "herd_evidence": self.evidence, "verification_log": self.log,
            "verification_command": "python3 -m unittest --serial",
            "verification_exit_status": 0,
            "title": "Task 8: Grok Bot transport",
            "objective": "Deliver the reviewed Grok Bot transport.",
            "base_branch": "main", "remote": "origin",
            "validity_seconds": 3600,
        }
        arguments.update(changes)
        return arguments

    def presented_delivery(self, **changes):
        return self.ok(self.adapter.present_delivery(
            **self.present_arguments(**changes)))

    def delivery_approval(self, shown, **changes):
        arguments = dict(shown["approval_binding"], relayed_reply="approved",
                         reply_to="grok-message-0001", relay_ref="relay-0001")
        arguments.update(changes)
        return arguments

    def deliveries(self):
        return self.machine.store.load()["deliveries"]

    def assert_nothing_performed(self):
        effects = [call for call in self.transport.calls
                   if call not in RecordingDeliveryTransport.READS]
        self.assertEqual(effects, [])
        for record in self.deliveries().values():
            self.assertEqual(record["phase"], delivery_auth.PHASE_AUTHORIZED)
            for step in delivery_auth.STEPS:
                self.assertEqual(record["steps"][step]["state"],
                                 delivery_auth.STEP_PENDING, step)


# ====================================================================
# Present: the exact, COMPLETE delivery binding, recorded as displayed
# ====================================================================


class PresentDeliveryTests(DeliveryFixture):

    def test_present_shows_the_complete_delivery_proposal(self):
        shown = self.presented_delivery()
        proposal = shown["delivery_proposal"]
        lines = shown["display_text"].splitlines()
        for line in leaf_lines("delivery_proposal", proposal):
            self.assertIn(line, lines)
        entries = proposal["binding"]["candidate"]["entries"]
        self.assertEqual(len(entries), ENTRY_COUNT)
        for entry in entries:
            self.assertIn(json.dumps(entry["path"]), shown["display_text"])
        self.assertNotIn("and 10 more", shown["display_text"])
        self.assertEqual(shown["approval_binding"], {
            "proposal_digest_sha256": shown["proposal_digest_sha256"],
            "expires_at": proposal["expires_at"]})
        self.assertEqual(shown["proposal_digest_sha256"],
                         delivery_auth.delivery_proposal_digest(proposal))
        self.assertEqual(proposal["binding"]["allowed_actions"],
                         list(delivery_auth.STEPS))
        for phrase in ("separate from any engineering approval",
                       "Never authorized: merge, auto-merge, tag, release,"
                       " deploy", "operator-attested",
                       "not cryptographically authenticated",
                       "reply with a separate message containing only: approved",
                       "performs no delivery step"):
            self.assertIn(phrase, shown["display_text"])
        self.assertEqual(shown["delivery_authority"], "none")
        self.assertEqual(shown["evidence_status"], EVIDENCE)
        receipt = index_module.RequestIndex(self.state).delivery_presentation(
            shown["proposal_digest_sha256"])
        self.assertEqual(receipt["proposal"], proposal)
        self.assertEqual(self.deliveries(), {})
        self.assert_nothing_performed()

    def test_present_relays_every_ceremony_argument_and_nothing_else(self):
        """The tool's fields are exactly present-dots' own arguments."""
        parser = delivery_cli.build_parser()
        subparsers = [a for a in parser._actions
                      if a.__class__.__name__ == "_SubParsersAction"][0]
        dests = sorted(a.dest for a in subparsers.choices["present-dots"]._actions
                       if a.dest not in ("help", "fn"))
        self.assertEqual(sorted(adapter_module.TOOLS["present_delivery"]), dests)
        self.refused("grok_bot_unknown_field", self.adapter.call(
            "present_delivery", dict(self.present_arguments(),
                                     allowed_actions=["MERGE"])))
        self.assertEqual(self.transport.calls, [])

    def test_unsafe_or_malformed_arguments_are_refused_before_any_read(self):
        for changes in (dict(objective="@/etc/passwd"),
                        dict(architecture_notes="@notes.md"),
                        dict(nonblocking_risks="@x"),
                        dict(repo="relative/work"), dict(herd_evidence="e.json"),
                        dict(verification_log=None), dict(title=None),
                        dict(verification_exit_status="0"),
                        dict(verification_exit_status=True),
                        dict(validity_seconds=1.5),
                        dict(verification_ran_at=float("nan"))):
            with self.subTest(changes=changes):
                self.assert_labelled("grok_bot_bad_request",
                                     self.adapter.present_delivery(
                                         **self.present_arguments(**changes)))
        self.assertEqual(self.transport.calls, [])

    def test_S3_N1_an_unrepresentable_number_is_a_labelled_refusal(self):
        """S3-N1: ``math.isfinite`` raised OverflowError on an int too large
        for a float, so a well-shaped call escaped the labelled path. The
        same sweep found ``validity_seconds``: present-dots computes
        ``now + validity`` before pr_delivery bounds the span (at mint), so
        a huge int overflowed inside the ceremony. Each is now refused by
        its own label, before anything is read, recorded or performed."""
        span = delivery_auth.MAX_AUTHORIZATION_VALIDITY_SECONDS
        ran_at = "present_delivery verification_ran_at must be a finite number"
        validity = ("present_delivery validity_seconds must be an integer from"
                    " 1 to %d (pr_delivery's own authorization span)" % span)
        top = int(sys.float_info.max)
        for changes, reason in (
            (dict(verification_ran_at=10 ** 400), ran_at),
            (dict(verification_ran_at=-10 ** 400), ran_at),
            (dict(verification_ran_at=top + 1), ran_at),
            (dict(validity_seconds=10 ** 400), validity),
            (dict(validity_seconds=-10 ** 400), validity),
            (dict(validity_seconds=0), validity),
            (dict(validity_seconds=span + 1), validity),
        ):
            with self.subTest(field=sorted(changes), sign=list(
                    changes.values())[0] > 0):
                result = self.assert_labelled(
                    "grok_bot_bad_request", self.adapter.present_delivery(
                        **self.present_arguments(**changes)))
                self.assertEqual(result["reason"], reason)
        self.assertEqual(index_module.RequestIndex(self.state).load().get(
            "delivery_presentations", {}), {})
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.seams.take(), [])

    def test_numbers_present_dots_could_hold_are_relayed_unchanged(self):
        """The bound is exact and refuses nothing present-dots could hold:
        an int up to the largest finite float, a float, and the full
        authorization span, each passed through with its own type."""
        span = delivery_auth.MAX_AUTHORIZATION_VALIDITY_SECONDS
        for ran_at, seconds in ((int(sys.float_info.max), span),
                                (1_799_999_000.5, 1)):
            with self.subTest(ran_at_type=type(ran_at).__name__):
                proposal = self.presented_delivery(
                    verification_ran_at=ran_at,
                    validity_seconds=seconds)["delivery_proposal"]
                relayed = proposal["binding"]["evidence"][
                    "independent_verification"]["ran_at"]
                self.assertIs(type(relayed), type(ran_at))
                self.assertEqual(relayed, ran_at)
                self.assertEqual(proposal["expires_at"]
                                 - proposal["presented_at"], seconds)
        self.assertEqual(self.deliveries(), {})
        self.assert_nothing_performed()

    def test_a_ceremony_refusal_is_a_labelled_refusal(self):
        result = self.assert_labelled(
            "grok_bot_delivery_refused", self.adapter.present_delivery(
                **self.present_arguments(verification_exit_status=1)))
        self.assertIn("only a green", result["reason"])
        self.assertEqual(result["ceremony_problem"], "CeremonyError")


# ====================================================================
# Approve: compare with the receipt, then relay to attest-dots, once
# ====================================================================


class ApproveDeliveryTests(DeliveryFixture):

    def test_the_relayed_reply_authorizes_exactly_the_presented_proposal(self):
        shown = self.presented_delivery()
        result = self.ok(self.adapter.approve_delivery(
            **self.delivery_approval(shown)))
        self.assertEqual(result["status"],
                         "delivery_authorized_by_operator_attestation")
        record = self.deliveries()[result["delivery_id"]]
        delivery_auth.validate_authorization(record)
        human = record["human_authorization"]
        self.assertEqual(human["source"], "dots_operator_attested")
        self.assertNotEqual(human["source"],
                            delivery_auth.AUTHORIZATION_SOURCE_LOCAL_TERMINAL)
        attestation = human["attestation"]
        self.assertEqual(attestation["proposal_digest_sha256"],
                         shown["proposal_digest_sha256"])
        self.assertEqual(attestation["provenance"],
                         "operator_attested_not_independently_verified")
        self.assertEqual(attestation["reply_to"], "grok-message-0001")
        self.assertEqual(record["allowed_actions"], list(delivery_auth.STEPS))
        self.assertEqual(record["candidate"],
                         shown["delivery_proposal"]["binding"]["candidate"])
        grant = result["delivery_authorization"]
        self.assertEqual(grant["delivery_id"], result["delivery_id"])
        self.assertIs(grant["granted_by_this_transport"], False)
        self.assertEqual(grant["authorized_steps"], list(delivery_auth.STEPS))
        self.assertEqual(grant["performed_steps"], [])
        self.assertEqual(result["delivery_authority"], "none")
        self.assertEqual(result["evidence_status"], EVIDENCE)
        self.assert_nothing_performed()

    def test_a_second_relay_of_the_same_proposal_authorizes_nothing_more(self):
        shown = self.presented_delivery()
        self.ok(self.adapter.approve_delivery(**self.delivery_approval(shown)))
        again = self.assert_labelled(
            "grok_bot_delivery_refused",
            self.adapter.approve_delivery(**self.delivery_approval(shown)))
        self.assertIn("already attested", again["reason"])
        self.assertEqual(len(self.deliveries()), 1)
        self.assert_nothing_performed()

    def test_a_binding_that_differs_from_the_display_is_refused(self):
        shown = self.presented_delivery()
        displayed = shown["approval_binding"]["expires_at"]
        for expires_at in (displayed - 1, displayed + 1, int(displayed),
                           str(displayed), [displayed]):
            with self.subTest(expires_at=repr(expires_at)):
                result = self.assert_labelled(
                    "grok_bot_binding_not_displayed",
                    self.adapter.approve_delivery(**self.delivery_approval(
                        shown, expires_at=expires_at)))
                self.assertEqual(result["fields"], ["expires_at"])
        self.assertEqual(self.deliveries(), {})

    def test_a_digest_never_presented_here_is_refused(self):
        shown = self.presented_delivery()
        real = shown["proposal_digest_sha256"]
        # One hex digit changed, to a value it provably is not; the
        # mutation is asserted BEFORE approval, so the case can never
        # relay the genuine digest (the digest varies run to run).
        altered = real[:-1] + ("1" if real[-1] == "0" else "0")
        for digest in ("f" * 64, altered):
            self.assertNotEqual(digest, real)
            with self.subTest(digest=digest):
                self.assert_labelled("grok_bot_not_presented",
                                     self.adapter.approve_delivery(
                                         **self.delivery_approval(
                                             shown, proposal_digest_sha256=digest)))
        self.assertEqual(self.deliveries(), {})

    def test_the_ceremony_refuses_what_the_receipt_cannot(self):
        """Each refused by pr_delivery itself, with its own reason."""
        shown = self.presented_delivery()
        for changes, reason in (
            (dict(relayed_reply="approved, but not the tests"),
             "not an exact affirmative"),
            (dict(relayed_reply="yes"), "not an exact affirmative"),
            (dict(reply_to=None), "--reply-to is required"),
            (dict(relay_ref="two\nlines"), "--relay-ref is required"),
        ):
            with self.subTest(changes=changes):
                result = self.assert_labelled(
                    "grok_bot_delivery_refused", self.adapter.approve_delivery(
                        **self.delivery_approval(shown, **changes)))
                self.assertIn(reason, result["reason"])
        self.assertEqual(self.deliveries(), {})

    def test_both_displayed_binding_fields_are_required(self):
        """The reply restates the WHOLE displayed binding: an absent field
        is refused here, before the ceremony is reached."""
        shown = self.presented_delivery()
        for name in ("proposal_digest_sha256", "expires_at"):
            with self.subTest(absent=name):
                arguments = self.delivery_approval(shown)
                del arguments[name]
                result = self.assert_labelled(
                    "grok_bot_bad_request",
                    self.adapter.approve_delivery(**arguments))
                self.assertIn(name, result["reason"])
        self.assertEqual(self.deliveries(), {})
        self.assert_nothing_performed()

    def test_a_changed_live_candidate_is_never_authorized(self):
        shown = self.presented_delivery()
        self.transport.raw = raw_candidate(ENTRY_COUNT, offset=1000)
        result = self.assert_labelled(
            "grok_bot_delivery_refused",
            self.adapter.approve_delivery(**self.delivery_approval(shown)))
        self.assertIn("no longer matches", result["reason"])
        self.assertEqual(self.deliveries(), {})

    def test_an_expired_presentation_is_refused(self):
        shown = self.presented_delivery()
        late = shown["approval_binding"]["expires_at"] + 1
        with mock.patch.object(delivery_cli.time, "time", lambda: late):
            result = self.assert_labelled(
                "grok_bot_delivery_refused",
                self.adapter.approve_delivery(**self.delivery_approval(shown)))
        self.assertIn("expired", result["reason"])
        self.assertEqual(self.deliveries(), {})

    def test_status_reads_the_authorization_and_nothing_is_performed(self):
        shown = self.presented_delivery()
        approved = self.ok(self.adapter.approve_delivery(
            **self.delivery_approval(shown)))
        status = self.ok(self.adapter.delivery_status(
            delivery_id=approved["delivery_id"]))
        self.assertEqual(status["delivery_id"], approved["delivery_id"])
        self.assertEqual(status["delivery_authority"], "none")
        self.assert_labelled("grok_bot_delivery_refused",
                             self.adapter.delivery_status(delivery_id="prd-" + "0" * 24))
        self.assert_labelled("grok_bot_bad_request",
                             self.adapter.delivery_status(delivery_id=[]))
        self.assert_nothing_performed()


# ====================================================================
# Separation: engineering approval never authorizes delivery; the adapter
# grants nothing itself; merge, release and deploy are refused
# ====================================================================


class SeparationTests(DeliveryFixture):

    def test_an_engineering_approval_alone_authorizes_no_delivery(self):
        out = self.requested()
        shown = self.presented(out["request_ref"])
        approved = self.ok(self.adapter.approve(**self.approval(shown)))
        self.assertEqual(approved["delivery_authority"], "none")
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.transport.calls, [])
        # The engineering binding is not a delivery binding: refused.
        self.assert_labelled("grok_bot_unknown_field", self.adapter.call(
            "approve_delivery", dict(shown["approval_binding"],
                                     relayed_reply="approved")))
        self.assert_labelled("grok_bot_not_presented", self.adapter.approve_delivery(
            proposal_digest_sha256=out["proposal_digest_sha256"],
            expires_at=shown["approval_binding"]["expires_at"],
            relayed_reply="approved", reply_to="r", relay_ref="r"))
        self.assertEqual(self.deliveries(), {})

    def test_merge_release_deploy_and_performing_steps_have_no_tool(self):
        for tool in ("merge", "release", "deploy", "advance", "advance_delivery",
                     "perform_delivery", "revoke_delivery", "authorize"):
            with self.subTest(tool=tool):
                self.assert_labelled("grok_bot_unknown_tool",
                                     self.adapter.call(tool, {}))
        self.assertEqual(
            sorted(t for t in adapter_module.TOOLS if "deliver" in t),
            ["approve_delivery", "delivery_status", "present_delivery"])

    def test_B11_the_adapter_never_mints_constructs_or_performs(self):
        for path in sorted((REPO_ROOT / "grok_bot").glob("*.py")):
            with self.subTest(path=path.name):
                self.assertEqual(grant_violations(path.read_text()), [])

    def test_B11_the_grant_detector_fires_on_planted_probes(self):
        planted = ("from pr_delivery.transport import DeliveryTransport\n"
                   "t = DeliveryTransport()\n"
                   "delivery_cli._mint(m, a, 0, None)\n"
                   "boundary.advance(d)\n"
                   "auth.new_authorization(d, a, 0)\n")
        self.assertEqual(grant_violations(planted), [
            "DeliveryTransport", "DeliveryTransport", "_mint", "advance",
            "new_authorization"])


GRANT_NAMES = frozenset({
    "_mint", "new_authorization", "add_delivery", "DeliveryTransport",
    "DeliveryMachine", "DeliveryStore", "advance", "advance_cmd",
    "authorize_cmd", "assemble_authority", "revoke", "revoke_cmd",
    "_live_repository", "_gather",
})


def grant_violations(source):
    """Every reference, by name or attribute, to a way of minting a
    delivery authorization, DIRECTLY constructing a delivery transport,
    machine or store, or performing or revoking a step. (Construction is
    core-owned, by pr_delivery's own ``build_machine()``, which is not a
    grant name: ``grok_bot.delivery`` calls it solely for the read-only
    status projection.)"""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in GRANT_NAMES:
            found.append((node.lineno, node.col_offset, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in GRANT_NAMES:
            found.append((node.lineno, node.col_offset, node.attr))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in GRANT_NAMES:
                    found.append((node.lineno, node.col_offset, alias.name))
    return [name for _, _, name in sorted(found)]


# ====================================================================
# Structural containment: no process can start from this module
# ====================================================================


FORBIDDEN_TEST_IMPORTS = frozenset({
    "subprocess", "socket", "urllib", "http", "pty", "multiprocessing",
    "asyncio", "_hermetic_git", "test_hermetic_git", "test_pr_delivery",
    "test_pr_delivery_guards", "test_push_gate",
})


def process_import_violations(source):
    """Roots this module imports that could start a process or reach a
    network, and the existing real-Git delivery fixtures."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        found += [n for n in names if n.split(".")[0] in FORBIDDEN_TEST_IMPORTS]
    return found


class ContainmentTests(DeliveryFixture):

    def test_every_process_seam_is_contained_and_reached_by_nothing(self):
        """A REAL delivery transport, deliberately constructed here and
        asked for a git read, and a direct process start, are both stopped
        at the seam (recorded, raised) before any process exists."""
        real = delivery_transport.DeliveryTransport()
        with self.assertRaises(AssertionError):
            real.head_oid(self.repo)
        with self.assertRaises(AssertionError):
            delivery_transport.subprocess.Popen(["true"])
        with self.assertRaises(AssertionError):
            os.system("true")
        self.assertEqual(self.seams.take(), [
            "DeliveryTransport._git", "subprocess.Popen", "os.system"])
        for label in ("subprocess.Popen", "subprocess.run",
                      "subprocess.check_output", "os.posix_spawn", "os.fork",
                      "DeliveryTransport._run", "DeliveryTransport._gh",
                      "DeliveryTransport.run_reverification"):
            self.assertIn(label, self.seams.patched)

    def test_with_the_injection_disabled_the_real_transport_still_starts_nothing(self):
        """The guard switched off: pr_delivery's REAL ``build_machine`` (so
        the real ``DeliveryTransport()``, over this test's temporary store)
        serves the ceremony. Its first git read reaches a contained seam,
        raises, and no process exists; nothing is presented or recorded."""
        def real_machine(store_dir=None):
            return ORIGINAL_BUILD_MACHINE(self.delivery_store_dir)
        with mock.patch.object(delivery_cli, "build_machine", real_machine):
            with self.assertRaises(AssertionError):
                self.adapter.present_delivery(**self.present_arguments())
        calls = self.seams.take()
        self.assertTrue(calls)
        self.assertEqual(set(calls), {"DeliveryTransport._git"}, calls)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(index_module.RequestIndex(self.state).load().get(
            "delivery_presentations", {}), {})

    def test_the_double_is_not_the_real_transport_or_a_subclass_of_it(self):
        self.assertFalse(issubclass(RecordingDeliveryTransport,
                                    delivery_transport.DeliveryTransport))
        self.assertEqual(RecordingDeliveryTransport.__mro__,
                         (RecordingDeliveryTransport, object))
        self.assertIs(delivery_cli.build_machine().transport, self.transport)

    def test_this_module_imports_no_process_or_real_git_machinery(self):
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertEqual(process_import_violations(source), [])

    def test_B11_the_import_detector_fires_on_planted_probes(self):
        planted = ("import subprocess\nfrom http import client\n"
                   "import socket, json\nfrom test_pr_delivery import TestTransport\n"
                   "import urllib.request\n")
        self.assertEqual(process_import_violations(planted), [
            "subprocess", "http", "socket", "test_pr_delivery", "urllib.request"])


# ====================================================================
# Mutation self-check (in memory; nothing on disk changes). Every named
# test still runs under ProcessSeams, so a mutant cannot start a process.
# ====================================================================

MUTANTS = (
    ("the delivery receipt is not compared",
     adapter_module, "displayed_mismatch", lambda *args, **kwargs: [],
     ("ApproveDeliveryTests.test_a_binding_that_differs_from_the_display_is"
      "_refused",)),
    ("the delivery display omits the candidate",
     adapter_module, "rendered_lines",
     lambda prefix, value: leaf_lines(prefix, dict(value, binding=dict(
         value["binding"], candidate={}))) if prefix == "delivery_proposal"
     else leaf_lines(prefix, value),
     ("PresentDeliveryTests.test_present_shows_the_complete_delivery"
      "_proposal",)),
    ("S3-N1 restored: the number check asks math.isfinite of an int",
     delivery_module, "fits_finite_float",
     lambda value: not isinstance(value, bool) and math.isfinite(value),
     ("PresentDeliveryTests.test_S3_N1_an_unrepresentable_number_is_a"
      "_labelled_refusal",)),
    ("the validity span is unbounded",
     delivery_module, "VALIDITY_SPAN", 10 ** 500,
     ("PresentDeliveryTests.test_S3_N1_an_unrepresentable_number_is_a"
      "_labelled_refusal",)),
)


class MutationSelfCheckTests(unittest.TestCase):

    def run_named(self, names):
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromName(name, sys.modules[__name__])
            for name in names)
        result = unittest.TestResult()
        suite.run(result)
        return result

    def test_B11_every_delivery_mutant_is_caught_and_the_original_passes(self):
        for label, owner, name, mutant, names in MUTANTS:
            with self.subTest(mutant=label):
                with mock.patch.object(owner, name, mutant):
                    broken = self.run_named(names)
                failed = set(getattr(test, "test_case", test).id()
                             for test, _ in broken.failures + broken.errors)
                self.assertEqual(len(failed), len(names), (label, failed))
                restored = self.run_named(names)
                self.assertTrue(restored.wasSuccessful(),
                                (label, restored.failures, restored.errors))


if __name__ == "__main__":
    unittest.main()
