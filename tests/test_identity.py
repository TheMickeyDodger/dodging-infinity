"""I2: logical role identity versus transient agent/session identity.

Every adversarial class named in the I2 brief has a test here whose
name states the class it kills. The contract tests at the bottom always
pin the consumed envelope, field paths, value domains, forwarding, and
classification against a deterministic dependency-shaped specimen. An
opt-in read-only integration specimen applies the same assertions to an
installed `herdr` binary when explicitly requested.
"""

import ast
import inspect
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

from herdr import identity, lifecycle, tasks
from herdr.config import PRESETS
from herdr.instance import HerdrInstance

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def agent_payload(name="sup1", cwd="/repo", workspace="w1",
                  pane="w1:p1", session="s-1", status="idle",
                  runtime="claude", **extra):
    """One agent record in the shape the real binary emits."""
    record = {
        "agent": runtime,
        "agent_session": {
            "agent": runtime,
            "kind": "id",
            "source": "herdr:" + runtime,
            "value": session,
        },
        "agent_status": status,
        "cwd": cwd,
        "focused": False,
        "foreground_cwd": cwd,
        "interactive_ready": True,
        "name": name,
        "pane_id": pane,
        "revision": 1,
        "state_change_seq": 0,
        "tab_id": workspace + ":t1",
        "terminal_id": "term_" + name,
        "workspace_id": workspace,
    }
    record.update(extra)
    return record


def get_envelope(record):
    return {"id": "cli:agent:get",
            "result": {"agent": record, "type": "agent_info"}}


def list_envelope(records):
    return {"id": "cli:agent:list",
            "result": {"agents": list(records), "type": "agent_list"}}


def info(record, status="idle"):
    return {"status": status, "raw": get_envelope(record)}


class ClassificationTests(unittest.TestCase):
    """MISSING, DEGRADED and REPLACED are decided apart from each
    other, each with its own code and action."""

    def binding(self, session="s-1"):
        return identity.binding_for(
            "supervisor", "sup1", agent_payload(session=session)
        )

    def test_present_when_stable_and_session_both_match(self):
        verdict = identity.classify(
            "supervisor", self.binding(), info(agent_payload())
        )
        self.assertEqual(verdict.verdict, identity.VERDICT_PRESENT)
        self.assertEqual(verdict.action, identity.ACTION_PROCEED)
        self.assertIsNone(verdict.problem)

    def test_a_stale_persisted_agent_id_reads_as_MISSING(self):
        """Adversarial class: the persisted runtime.json names an
        agent Herdr no longer knows."""
        verdict = identity.classify(
            "supervisor", self.binding(),
            {"status": "missing", "raw": None},
        )
        self.assertEqual(verdict.verdict, identity.VERDICT_MISSING)
        self.assertEqual(verdict.action, identity.ACTION_REDISCOVER)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_AGENT_MISSING)

    def test_an_unparsable_probe_reads_as_DEGRADED_not_healthy(self):
        """Adversarial class: Herdr answers in a shape this module
        does not parse. That is absence of evidence, so it blocks."""
        verdict = identity.classify(
            "supervisor", self.binding(),
            {"status": "unknown", "raw": None},
        )
        self.assertEqual(verdict.verdict, identity.VERDICT_DEGRADED)
        self.assertEqual(verdict.action, identity.ACTION_BLOCK)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_AGENT_DEGRADED)

    def test_a_replaced_session_reads_as_REPLACED_not_missing(self):
        """Adversarial class: replacement agent/session. The logical
        role survived; the process behind it did not."""
        verdict = identity.classify(
            "supervisor", self.binding(session="s-1"),
            info(agent_payload(session="s-2")),
        )
        self.assertEqual(verdict.verdict, identity.VERDICT_REPLACED)
        self.assertEqual(verdict.action, identity.ACTION_REBOOTSTRAP)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_SESSION_REPLACED)

    def test_the_four_verdicts_are_distinguished_from_each_other(self):
        # The defect this replaces collapsed all of them into one
        # refusal, so distinctness is the property.
        verdicts = {
            identity.classify("s", self.binding(),
                              info(agent_payload())).verdict,
            identity.classify("s", self.binding(),
                              {"status": "missing",
                               "raw": None}).verdict,
            identity.classify("s", self.binding(),
                              {"status": "unknown",
                               "raw": None}).verdict,
            identity.classify("s", self.binding(session="s-1"),
                              info(agent_payload(
                                  session="s-9"))).verdict,
        }
        self.assertEqual(len(verdicts), 4, verdicts)

    def test_a_lookalike_with_a_different_cwd_is_not_adopted(self):
        verdict = identity.classify(
            "supervisor", self.binding(),
            info(agent_payload(cwd="/somewhere/else")),
        )
        self.assertEqual(verdict.action, identity.ACTION_BLOCK)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_STABLE_MISMATCH)

    def test_a_model_switch_alone_does_not_read_as_a_new_identity(self):
        """Adversarial class: model switch. The `all-claude` preset
        runs supervisor/executor on Fable 5.1 and lead/reviewer on Opus.
        The model is an argv choice; it is not in the agent record, so
        switching it must not by itself look like a replacement."""
        preset = PRESETS["all-claude"]["roles"]
        self.assertEqual(preset["supervisor"]["args"][1], "claude-fable-5-1")
        self.assertEqual(preset["lead"]["args"][1], "opus")
        # Same session, same stable identity, different model: the
        # record is unchanged, so the verdict is PRESENT.
        verdict = identity.classify(
            "supervisor", self.binding(), info(agent_payload())
        )
        self.assertEqual(verdict.verdict, identity.VERDICT_PRESENT)
        # A model switch that DOES restart the session shows up as a
        # new session id, and then it is a REPLACEMENT — rebootstrap,
        # not a block.
        restarted = identity.classify(
            "supervisor", self.binding(),
            info(agent_payload(session="s-after-model-switch")),
        )
        self.assertEqual(restarted.action,
                         identity.ACTION_REBOOTSTRAP)

    def test_an_UNBOUND_live_role_is_NOT_present(self):
        """R-53 AQ-3, and this test REPLACES its own inverse.

        It read: `test_a_first_run_with_no_bound_session_is_not_
        suspicious`, and it asserted VERDICT_PRESENT for an empty
        binding. That assertion PINNED THE DEFECT. With no binding
        the lookalike guard iterated an empty mapping and the
        session guard tested a None, so both fell through to PRESENT
        and an unbound role reported healthy — and this test held
        that behaviour in place through a reviewer and an
        acceptance.

        A first run genuinely is not suspicious. It is also not
        CONFIRMED, and those are different claims. PRESENT means the
        session is the one last bound; with no binding recorded there is no such session, so within this
        herd the verdict is UNBOUND.
        """
        verdict = identity.classify("supervisor", {},
                                    info(agent_payload()))
        self.assertEqual(verdict.verdict, identity.VERDICT_UNBOUND)
        self.assertEqual(verdict.action, identity.ACTION_BIND)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_ROLE_UNBOUND)
        self.assertNotEqual(verdict.verdict, identity.VERDICT_PRESENT)

    def test_a_PARTIAL_binding_is_UNBOUND_not_present(self):
        """Each required field, one at a time. A binding missing one
        of them is unable to answer the question PRESENT claims to
        have answered, so a partial write does not read as whole."""
        complete = identity.binding_for(
            "supervisor", "h-sup", agent_payload()
        )
        for field in identity.BINDING_REQUIRED_FIELDS + ("stable",):
            with self.subTest(missing=field):
                partial = dict(complete)
                partial.pop(field)
                verdict = identity.classify(
                    "supervisor", partial, info(agent_payload())
                )
                self.assertEqual(
                    verdict.verdict, identity.VERDICT_UNBOUND,
                    "a binding with no %s was compared against as"
                    " though it were complete" % field,
                )

    def test_a_PRESENT_verdict_is_UNCONSTRUCTIBLE_without_its_session(self):
        """THE CONSTRUCTION HALF (AQ-3), driven.

        The requirement is that the wrong state be UNWRITABLE, not
        merely unwritten. A future caller — or a test — that tries to
        report an unbound role as PRESENT fails at construction,
        inside this process, rather than producing a verdict that
        reads healthy.
        """
        with self.assertRaises(ValueError):
            identity.Verdict(
                "supervisor", identity.VERDICT_PRESENT,
                identity.ACTION_PROCEED,
            )
        with self.assertRaises(ValueError):
            identity.Verdict(
                "supervisor", identity.VERDICT_PRESENT,
                identity.ACTION_PROCEED, bound_session="",
            )
        # And the same constructor accepts it WITH the session, so
        # the refusal above is about the missing evidence and not
        # about PRESENT being unconstructible in general.
        allowed = identity.Verdict(
            "supervisor", identity.VERDICT_PRESENT,
            identity.ACTION_PROCEED, bound_session="sess-1",
        )
        self.assertEqual(allowed.verdict, identity.VERDICT_PRESENT)

    def test_a_bound_role_whose_live_record_has_NO_session_degrades(self):
        """The binding names a session and the record carries none.
        That is an unanswerable comparison, not a match."""
        payload = agent_payload()
        payload["agent_session"] = {}
        binding = identity.binding_for(
            "supervisor", "h-sup", agent_payload()
        )
        verdict = identity.classify("supervisor", binding,
                                    info(payload))
        self.assertEqual(verdict.verdict, identity.VERDICT_DEGRADED)
        self.assertEqual(verdict.action, identity.ACTION_BLOCK)


class RediscoveryTests(unittest.TestCase):
    """Rediscovery accepts exact evidence and blocks on everything
    else."""

    def binding(self):
        return identity.binding_for(
            "supervisor", "sup1", agent_payload()
        )

    def test_exactly_one_match_is_rediscovered(self):
        listing = list_envelope([
            agent_payload(name="sup1-new", session="s-2"),
            agent_payload(name="other", workspace="w2",
                          pane="w2:p1"),
        ])
        record, problem, detail = identity.rediscover(
            "supervisor", self.binding(), listing
        )
        self.assertIsNone(problem, detail)
        self.assertEqual(record["name"], "sup1-new")

    def test_zero_candidates_blocks(self):
        listing = list_envelope([
            agent_payload(name="other", workspace="w9",
                          pane="w9:p1"),
        ])
        record, problem, _ = identity.rediscover(
            "supervisor", self.binding(), listing
        )
        self.assertIsNone(record)
        self.assertEqual(problem, identity.PROBLEM_NO_CANDIDATE)

    def test_multiple_candidates_block_rather_than_guess(self):
        listing = list_envelope([
            agent_payload(name="a"), agent_payload(name="b"),
        ])
        record, problem, _ = identity.rediscover(
            "supervisor", self.binding(), listing
        )
        self.assertIsNone(record)
        self.assertEqual(problem, identity.PROBLEM_AMBIGUOUS)

    def test_a_single_candidate_that_conflicts_is_not_adopted(self):
        listing = list_envelope([
            agent_payload(name="a", cwd="/a/different/repo"),
        ])
        record, problem, _ = identity.rediscover(
            "supervisor", self.binding(), listing
        )
        self.assertIsNone(record)
        self.assertEqual(problem,
                         identity.PROBLEM_CANDIDATE_CONFLICT)

    def test_an_incomplete_binding_cannot_rediscover(self):
        record, problem, _ = identity.rediscover(
            "supervisor", {"stable": {"cwd": "/repo"}},
            list_envelope([agent_payload()]),
        )
        self.assertIsNone(record)
        self.assertEqual(problem,
                         identity.PROBLEM_BINDING_INCOMPLETE)

    def test_an_unreadable_listing_blocks_rather_than_reading_empty(self):
        for listing in (None, {}, {"result": {}},
                        {"result": {"agents": "no"}}):
            with self.subTest(listing=listing):
                record, problem, _ = identity.rediscover(
                    "supervisor", self.binding(), listing
                )
                self.assertIsNone(record)
                self.assertEqual(problem,
                                 identity.PROBLEM_NO_CANDIDATE)


class ResetFixture(unittest.TestCase):
    """A herd whose runtime, config and roles are on disk."""

    def make_repo(self, agents=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        herd = repo / ".herd"
        (herd / "state").mkdir(parents=True)
        (herd / "roles").mkdir(parents=True)
        for filename in ("supervisor.md", "lead.md", "executor.md",
                         "reviewer.md"):
            (herd / "roles" / filename).write_text("ROLE " + filename)
        (herd / "herd.config.json").write_text(json.dumps({
            "version": 4,
            "project": {"name": "t"},
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
            "agents": agents or {"supervisor": "sup1",
                                 "lead1": "lead1"},
            "panes": {},
        }))
        (herd / "state" / "task.json").write_text(
            json.dumps({"status": "IDLE"})
        )
        self.bind_through_the_real_producer(
            repo, agents or {"supervisor": "sup1", "lead1": "lead1"}
        )
        return repo

    def bind_through_the_real_producer(self, repo, agents,
                                       payloads=None):
        """Bind this fixture's roles by RUNNING THE PRODUCTION
        PRODUCER, and within this helper never by hand-building one.

        THE TRAP R-53 NAMED, and the reason this helper exists: every
        binding in this file used to be written by a test calling
        `save_bindings` with a literal document. A test that supplies
        the artifact under test is, within this suite, unable to
        detect a missing producer — which is how one stayed invisible
        through a reviewer, an acceptance, and a supervisor.

        So the fixture calls `lifecycle.establish_role_bindings`, the
        same function bootstrap calls, with a prober standing in for
        Herdr. What is injected is the DEPENDENCY'S ANSWER; the code
        that decides what counts as evidence and writes the file is
        production's.
        """
        supplied = payloads or {}

        def prober(agent):
            if agent in supplied:
                return supplied[agent]
            # The DEFAULT record is the same one the reset tests hand
            # back from their own `agent_info` double, so a fixture
            # bound here and probed there sees one identity rather
            # than two. A test that needs a different record for a
            # role passes it in `payloads` and says so.
            return info(agent_payload())

        return lifecycle.establish_role_bindings(
            HerdrInstance(repo), agents, prober=prober,
            settle_seconds=0.0, sleeper=lambda _seconds: None,
        )

    def run_reset(self, repo, **kwargs):
        """Run a reset, converting an unexpected exception into an
        AUTHORED failure.

        Letting it propagate turned two mutants into crash kills. The
        guarantee under test is that an unresolvable role is RECORDED
        and left alone, so an exception escaping instead is a failure
        of that guarantee rather than a broken test."""
        try:
            return tasks.clear_contexts(HerdrInstance(repo), **kwargs)
        except Exception as exc:                       # noqa: BLE001
            self.fail(
                "clear_contexts raised %s(%s) instead of recording a"
                " durable outcome; an unresolvable role must be"
                " recorded and skipped, not raised through"
                % (type(exc).__name__, exc)
            )

    def role_entry(self, repo, logical):
        """The role's durable entry, asserted present before it is
        indexed. Indexing it directly turned three mutants into crash
        kills, which do not count as kills."""
        roles = self.disk_state(repo)["roles"]
        self.assertIn(
            logical, roles,
            "role %r has no durable record at all; a role that is"
            " skipped silently is the defect this state exists to"
            " prevent (recorded: %s)" % (logical, sorted(roles)),
        )
        return roles[logical]

    def disk_state(self, repo):
        """Read the reset record back FROM DISK rather than from the
        in-memory document — a fail-closed record asserted in memory
        is the recorded defect this replaces."""
        return json.loads(
            (repo / ".herd" / "state"
             / tasks.RESET_STATE_FILE).read_text()
        )


class PartialFailureTests(ResetFixture):
    """Adversarial class: partial failure mid-clear — the exact
    NameError shape, in which each context was cleared and no contract
    was re-seeded."""

    def test_a_failed_reseed_leaves_actionable_durable_state(self):
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = info(agent_payload())
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            prompt_fn.return_value = SimpleNamespace(
                returncode=1, stdout="", stderr="seed exploded")
            tasks.clear_contexts(HerdrInstance(repo))

        state = self.disk_state(repo)
        for logical, entry in state["roles"].items():
            with self.subTest(logical=logical):
                self.assertEqual(entry["phase"],
                                 tasks.RESET_PHASE_CLEARED)
                self.assertEqual(entry["problem"],
                                 tasks.PROBLEM_RESEED_FAILED)
                self.assertIn("seed exploded", entry["detail"])

    def test_the_exact_NameError_shape_is_recorded_not_silent(self):
        """The CLI used to clear every context and then die with
        NameError, leaving four agents cleared and unseeded with no
        record anywhere. An exception escaping the re-seed must still
        leave the cleared phase on disk."""
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.bootstrap_text") as seed:
            info_fn.return_value = info(agent_payload())
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            seed.side_effect = NameError(
                "name 'bootstrap_text' is not defined"
            )
            with self.assertRaises(NameError):
                tasks.clear_contexts(HerdrInstance(repo))

        state = self.disk_state(repo)
        self.assertTrue(state["roles"], "nothing was recorded at all")
        cleared = [
            logical for logical, entry in state["roles"].items()
            if entry["phase"] == tasks.RESET_PHASE_CLEARED
        ]
        self.assertTrue(
            cleared,
            "contexts were cleared and the record does not say so;"
            " that is the silently-unseeded-agents defect",
        )

    def test_a_RAISED_reseed_records_its_problem_code_and_detail(self):
        """Round-01 finding B, case B: the historical defect was an
        EXCEPTION, not a returncode. Before this, the returncode path
        recorded its reason and the raising path recorded none — in
        the one failure mode the record exists for."""
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.bootstrap_text") as seed:
            info_fn.return_value = info(agent_payload())
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            seed.side_effect = NameError(
                "name 'bootstrap_text' is not defined"
            )
            with self.assertRaises(NameError):
                tasks.clear_contexts(HerdrInstance(repo))

        entry = self.role_entry(repo, "supervisor")
        self.assertEqual(entry["phase"], tasks.RESET_PHASE_CLEARED)
        self.assertEqual(
            entry.get("problem"), tasks.PROBLEM_RESEED_FAILED,
            "the raising path recorded the cleared phase with no"
            " reason; a human reading this record cannot tell why",
        )
        self.assertIn("NameError", entry.get("detail") or "")
        self.assertIn("bootstrap_text", entry.get("detail") or "")

    def test_an_INTERRUPTED_reseed_records_its_problem_code(self):
        """Round-01 finding B, case C. A KeyboardInterrupt inherits
        from BaseException, so a bare `except Exception` would miss
        it — and an operator interrupting a reset mid-flight is
        exactly when the record matters."""
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = info(agent_payload())
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            prompt_fn.side_effect = KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                tasks.clear_contexts(HerdrInstance(repo))

        entry = self.role_entry(repo, "supervisor")
        self.assertEqual(entry["phase"], tasks.RESET_PHASE_CLEARED)
        self.assertEqual(entry.get("problem"),
                         tasks.PROBLEM_RESEED_FAILED)
        self.assertIn("KeyboardInterrupt", entry.get("detail") or "")

    def test_a_RAISED_clear_records_its_problem_code(self):
        """The symmetric half: an exception escaping the CLEAR loop
        records its reason at the `planned` phase."""
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset:
            info_fn.return_value = info(agent_payload())
            reset.side_effect = OSError("herdr socket closed")
            with self.assertRaises(OSError):
                tasks.clear_contexts(HerdrInstance(repo))
        entry = self.role_entry(repo, "supervisor")
        self.assertEqual(entry["phase"], tasks.RESET_PHASE_PLANNED)
        self.assertEqual(entry.get("problem"),
                         tasks.PROBLEM_RESET_FAILED)
        self.assertIn("OSError", entry.get("detail") or "")

    def test_a_failed_clear_records_its_problem_before_raising(self):
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset:
            info_fn.return_value = info(agent_payload())
            reset.return_value = SimpleNamespace(
                returncode=1, stdout="", stderr="clear refused")
            with self.assertRaises(RuntimeError):
                tasks.clear_contexts(HerdrInstance(repo))
        state = self.disk_state(repo)
        problems = {entry.get("problem")
                    for entry in state["roles"].values()}
        self.assertIn(tasks.PROBLEM_RESET_FAILED, problems)

    def test_the_record_exists_before_any_role_is_processed(self):
        """Mutant M21: the empty record written up-front distinguishes
        an interruption BEFORE the first role from a reset that did
        not start. Without it the file is absent, which leaves those
        two cases indistinguishable."""
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn:
            info_fn.side_effect = OSError("herdr went away")
            with self.assertRaises(OSError):
                tasks.clear_contexts(HerdrInstance(repo))
        path = repo / ".herd" / "state" / tasks.RESET_STATE_FILE
        self.assertTrue(
            path.exists(),
            "no reset record was written before the first role, so an"
            " interruption here leaves no evidence a reset began",
        )
        self.assertEqual(json.loads(path.read_text())["roles"], {})

    def test_the_happy_path_records_reseeded(self):
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = info(agent_payload())
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            prompt_fn.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            tasks.clear_contexts(HerdrInstance(repo))
        state = self.disk_state(repo)
        for entry in state["roles"].values():
            self.assertEqual(entry["phase"],
                             tasks.RESET_PHASE_RESEEDED)


class MissingTargetTests(ResetFixture):
    """Within this reset, a nonexistent target receives no reset and no
    re-seed."""

    def test_a_missing_supervisor_with_a_binding_blocks_on_no_candidate(self):
        """Adversarial class: missing Supervisor during a
        completed-task reset, with a binding to rediscover from."""
        repo = self.make_repo(agents={"supervisor": "gone"})
        identity.save_bindings(repo / ".herd", {"version": 1, "roles": {
            "supervisor": identity.binding_for(
                "supervisor", "gone", agent_payload(name="gone")
            )
        }})
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = {"status": "missing", "raw": None}
            self.run_reset(repo, lister=lambda: list_envelope([]))
            reset.assert_not_called()
            prompt_fn.assert_not_called()

        entry = self.role_entry(repo, "supervisor")
        self.assertEqual(entry["phase"], tasks.RESET_PHASE_BLOCKED)
        self.assertEqual(entry.get("problem"),
                         identity.PROBLEM_NO_CANDIDATE)
        self.assertEqual(self.disk_state(repo)["blocked"],
                         ["supervisor"])

    def test_a_missing_role_with_no_binding_cannot_rediscover(self):
        """With no recorded stable identity there is no field to match
        on, so this blocks rather than adopting whatever is listed.

        The binding is now REMOVED DELIBERATELY, because the fixture
        binds every role through the production producer. Before
        R-53's fix an unbound role was the DEFAULT state of every
        herd and this test got it for free; now it is a condition a
        test has to create, which is the change the whole increment
        is about.
        """
        repo = self.make_repo(agents={"supervisor": "gone"})
        identity.save_bindings(
            repo / ".herd", {"version": 1, "roles": {}}
        )
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = {"status": "missing", "raw": None}
            self.run_reset(
                repo, lister=lambda: list_envelope([agent_payload()])
            )
            reset.assert_not_called()
            prompt_fn.assert_not_called()
        entry = self.role_entry(repo, "supervisor")
        self.assertEqual(entry.get("problem"),
                         identity.PROBLEM_BINDING_INCOMPLETE)

    def test_a_degraded_probe_blocks_and_receives_no_command(self):
        repo = self.make_repo(agents={"supervisor": "sup1"})
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = {"status": "unknown", "raw": None}
            self.run_reset(repo)
            reset.assert_not_called()
            prompt_fn.assert_not_called()
        entry = self.role_entry(repo, "supervisor")
        self.assertEqual(entry["phase"], tasks.RESET_PHASE_BLOCKED)
        self.assertEqual(entry.get("problem"),
                         identity.PROBLEM_AGENT_DEGRADED)

    def test_an_exactly_rediscoverable_role_is_rebound_and_reseeded(self):
        """Restart recovery: the server came back and the agent has a
        new name, but the same pane. Exact evidence, so it rebinds."""
        repo = self.make_repo(agents={"supervisor": "old-name"})
        bindings = {"version": 1, "roles": {
            "supervisor": identity.binding_for(
                "supervisor", "old-name", agent_payload(name="old-name")
            )
        }}
        identity.save_bindings(repo / ".herd", bindings)
        replacement = agent_payload(name="new-name", session="s-9")
        calls = []
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.side_effect = lambda name: (
                {"status": "missing", "raw": None}
                if name == "old-name" else info(replacement)
            )
            reset.side_effect = lambda agent, cmd: (
                calls.append(agent),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            )[1]
            prompt_fn.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            self.run_reset(
                repo, lister=lambda: list_envelope([replacement])
            )

        self.assertEqual(
            calls, ["new-name"],
            "the reset went to the stale name instead of the"
            " rediscovered one",
        )
        rebound = identity.load_bindings(repo / ".herd")
        self.assertEqual(
            rebound["roles"]["supervisor"]["agent"], "new-name")
        self.assertEqual(
            self.role_entry(repo, "supervisor")["phase"],
            tasks.RESET_PHASE_RESEEDED,
        )

    def test_an_ambiguous_rediscovery_blocks_and_sends_nothing(self):
        repo = self.make_repo(agents={"supervisor": "old-name"})
        identity.save_bindings(repo / ".herd", {"version": 1, "roles": {
            "supervisor": identity.binding_for(
                "supervisor", "old-name", agent_payload(name="old-name")
            )
        }})
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = {"status": "missing", "raw": None}
            self.run_reset(repo, lister=lambda: list_envelope([
                agent_payload(name="a"), agent_payload(name="b"),
            ]))
            reset.assert_not_called()
            prompt_fn.assert_not_called()
        entry = self.role_entry(repo, "supervisor")
        self.assertEqual(entry.get("problem"),
                         identity.PROBLEM_AMBIGUOUS)


class UnboundRoleResetTests(ResetFixture):
    """R-53 AQ-3 at the RESET seam: an UNBOUND role blocks, and it is
    not bound here.

    Added because a mutant survived without it. Emptying the
    `ACTION_BIND` branch in `clear_contexts` changed, within this
    suite, no observable outcome: the verdict was correct and, within
    production, no code executed the routing that acts on it — the
    same computed-but-not-enforced shape this mission has ruled on
    four times.
    """

    def test_an_UNBOUND_role_BLOCKS_and_receives_no_command(self):
        repo = self.make_repo(agents={"supervisor": "sup1"})
        # Remove the binding the fixture's producer wrote, so this
        # role is in the state EVERY role was in before R-53.
        identity.save_bindings(
            repo / ".herd", {"version": 1, "roles": {}}
        )
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = info(agent_payload())
            self.run_reset(repo)
            reset.assert_not_called()
            prompt_fn.assert_not_called()
        entry = self.role_entry(repo, "supervisor")
        self.assertEqual(entry["phase"], tasks.RESET_PHASE_BLOCKED)
        self.assertEqual(entry.get("problem"),
                         identity.PROBLEM_ROLE_UNBOUND)
        self.assertEqual(entry.get("verdict"),
                         identity.VERDICT_UNBOUND)
        self.assertEqual(self.disk_state(repo)["blocked"],
                         ["supervisor"])

    def test_an_UNBOUND_role_is_NOT_bound_by_the_reset(self):
        """Binding is the bootstrap's job. Binding here would write
        whatever is live at reset time, which is the adopt-whatever-
        answers-to-the-name behaviour this module refuses."""
        repo = self.make_repo(agents={"supervisor": "sup1"})
        identity.save_bindings(
            repo / ".herd", {"version": 1, "roles": {}}
        )
        before = (repo / ".herd" / "state"
                  / "role-bindings.json").read_bytes()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset"), \
             patch("herdr.tasks.prompt"):
            info_fn.return_value = info(agent_payload())
            self.run_reset(repo)
        self.assertEqual(
            (repo / ".herd" / "state"
             / "role-bindings.json").read_bytes(), before,
            "the reset bound a role it had refused; a refusal must"
            " leave no durable effect",
        )

    def test_a_CORRUPT_bindings_document_blocks_every_role(self):
        """AQ-5 at the seam: a reset whose identity record is
        unreadable has, within this seam, no basis for a single role,
        and the refusal is DURABLE rather than an exception the caller
        may swallow."""
        repo = self.make_repo(agents={"supervisor": "sup1",
                                      "lead1": "lead1"})
        (repo / ".herd" / "state"
         / "role-bindings.json").write_text("{not json")
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = info(agent_payload())
            self.run_reset(repo)
            reset.assert_not_called()
            prompt_fn.assert_not_called()
        state = self.disk_state(repo)
        self.assertEqual(sorted(state["blocked"]),
                         ["lead1", "supervisor"])
        for logical in ("supervisor", "lead1"):
            entry = self.role_entry(repo, logical)
            self.assertEqual(entry.get("problem"),
                             identity.PROBLEM_BINDINGS_CORRUPT)
        self.assertEqual(
            (repo / ".herd" / "state"
             / "role-bindings.json").read_text(), "{not json",
            "the corrupt document was rebuilt by the reset",
        )


class RefusalOrderingTests(ResetFixture):
    """Round-01 E.2, raised to blocking by lead1: a refused operation
    leaves no durable side effect behind it."""

    def test_a_busy_agent_leaves_NO_durable_binding_write(self):
        """The binding file is UNCHANGED after a refusal. Asserted
        from a FRESH READ OF DISK, because the whole subject of this
        increment is durable state that disagrees with the
        operation's outcome.

        It read `assertFalse(exists())` before, and that was the right
        assertion while a bootstrapped herd had no bindings file at
        all. Now bootstrap writes one, so ABSENCE can no longer stand
        in for "was not written" — a refusal that OVERWROTE the
        bindings would satisfy the old assertion just as well as one
        that left them alone. The bytes are compared instead."""
        repo = self.make_repo(agents={"supervisor": "sup1"})
        bindings_file = repo / ".herd" / "state" / "role-bindings.json"
        self.assertTrue(
            bindings_file.exists(),
            "bootstrap did not bind the fixture's roles, so this test"
            " would pass by comparing an absent file to an absent one",
        )
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            # A busy agent whose recorded binding names a DIFFERENT
            # session, so the old ordering would classify REPLACED,
            # take the rebootstrap path and then refuse.
            info_fn.return_value = info(
                agent_payload(session="s-new", status="working"),
                status="working",
            )
            identity.save_bindings(repo / ".herd", {
                "version": 1,
                "roles": {"supervisor": identity.binding_for(
                    "supervisor", "sup1",
                    agent_payload(session="s-old"))},
            })
            before = bindings_file.read_bytes()
            with self.assertRaises(RuntimeError):
                tasks.clear_contexts(HerdrInstance(repo))
            reset.assert_not_called()
            prompt_fn.assert_not_called()
        self.assertEqual(
            bindings_file.read_bytes(), before,
            "a refused role had its binding rewritten to disk; a"
            " refusal must leave no durable effect",
        )

    def test_a_busy_REDISCOVERED_agent_leaves_no_binding_write(self):
        """The path the ordering change nearly opened: the recorded
        name is MISSING, so within that probe there is no observation
        of the agent that actually holds the role. The rediscovered
        agent is busy, and the binding must not be written before it
        is refused."""
        repo = self.make_repo(agents={"supervisor": "old-name"})
        bindings_file = repo / ".herd" / "state" / "role-bindings.json"
        identity.save_bindings(repo / ".herd", {"version": 1, "roles": {
            "supervisor": identity.binding_for(
                "supervisor", "old-name",
                agent_payload(name="old-name"))}})
        before = bindings_file.read_bytes()
        busy_replacement = agent_payload(
            name="new-name", session="s-9", status="working")
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = {"status": "missing", "raw": None}
            # Concrete returns, so that if the refusal is ever removed
            # the run continues far enough to trip the assertions
            # below rather than crashing on an unserialisable mock.
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            prompt_fn.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            with self.assertRaises(RuntimeError):
                tasks.clear_contexts(
                    HerdrInstance(repo),
                    lister=lambda: list_envelope([busy_replacement]),
                )
            reset.assert_not_called()
            prompt_fn.assert_not_called()
        self.assertEqual(
            bindings_file.read_bytes(), before,
            "a busy rediscovered agent had its binding written before"
            " the refusal",
        )

    def test_a_busy_agent_is_refused_before_a_rediscovery_listing(self):
        """The refusal runs ahead of the identity work, so the lister
        is not consulted for a role that is about to be refused."""
        repo = self.make_repo(agents={"supervisor": "sup1"})
        consulted = []
        with patch("herdr.tasks.agent_info") as info_fn:
            info_fn.return_value = info(
                agent_payload(status="working"), status="working")
            with self.assertRaises(RuntimeError):
                tasks.clear_contexts(
                    HerdrInstance(repo),
                    lister=lambda: consulted.append(1) or {},
                )
        self.assertEqual(
            consulted, [],
            "the live listing was consulted for a role that was then"
            " refused",
        )

    def test_a_probe_sentinel_is_not_treated_as_a_busy_agent(self):
        """Moving the refusal earlier must NOT restore the conflation
        I2 removes: `missing` and `unknown` are probe sentinels, not
        Herdr statuses, and they route to classification."""
        for sentinel in identity.PROBE_SENTINELS:
            with self.subTest(status=sentinel):
                self.assertFalse(identity.is_busy(sentinel))
        for busy in ("working", "blocked"):
            with self.subTest(status=busy):
                self.assertTrue(identity.is_busy(busy))
        for ok in identity.RESETTABLE_STATUSES:
            with self.subTest(status=ok):
                self.assertFalse(identity.is_busy(ok))


class UnconfiguredRoleTests(ResetFixture):
    """Round-01 E.1: the refusal that CHANGED during the collapse."""

    def test_an_unconfigured_role_blocks_and_the_others_continue(self):
        """The CLI copy raised and aborted the whole operation; the
        collapsed implementation records BLOCKED per role and carries
        on. Pinned because it is a behaviour change to a refusal made
        during a collapse."""
        repo = self.make_repo(agents={"supervisor": "sup1",
                                      "lead1": "lead1"})
        config_path = repo / ".herd" / "herd.config.json"
        config = json.loads(config_path.read_text())
        config["roles"]["supervisor"]["kind"] = "unconfigured-runtime"
        config_path.write_text(json.dumps(config))

        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = info(agent_payload())
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            prompt_fn.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            self.run_reset(repo)

        blocked = self.role_entry(repo, "supervisor")
        self.assertEqual(blocked["phase"], tasks.RESET_PHASE_BLOCKED)
        self.assertEqual(blocked.get("problem"),
                         tasks.PROBLEM_RESET_UNAVAILABLE)
        carried_on = self.role_entry(repo, "lead1")
        self.assertEqual(
            carried_on["phase"], tasks.RESET_PHASE_RESEEDED,
            "one unconfigured role stopped the others; the collapsed"
            " implementation blocks per role",
        )
        self.assertEqual(self.disk_state(repo)["blocked"],
                         ["supervisor"])


class RestartRecoveryTests(ResetFixture):
    """Adversarial class: restart recovery. The durable record
    survives the process and is readable by a fresh reader."""

    def test_the_record_is_readable_after_the_process_that_wrote_it(self):
        repo = self.make_repo()
        with patch("herdr.tasks.agent_info") as info_fn, \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn:
            info_fn.return_value = info(agent_payload())
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            prompt_fn.return_value = SimpleNamespace(
                returncode=1, stdout="", stderr="did not settle")
            tasks.clear_contexts(HerdrInstance(repo))

        # A FRESH reader, in a separate interpreter, with no access to
        # the object that wrote the file.
        script = (
            "import json,sys;"
            "d=json.load(open(sys.argv[1]));"
            "print(sorted((k, v['phase'], v.get('problem'))"
            " for k, v in d['roles'].items()))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script,
             str(repo / ".herd" / "state" / tasks.RESET_STATE_FILE)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(tasks.RESET_PHASE_CLEARED, result.stdout)
        self.assertIn(tasks.PROBLEM_RESEED_FAILED, result.stdout)


class BootstrapProducerTests(unittest.TestCase):
    """R-53 AQ-2: THE PRODUCER, and the trap that hid its absence.

    Every binding in this file used to be written by a test calling
    `save_bindings` with a literal document — five call sites. An instrument that manufactures its own input is, within this
    suite, unable to detect a missing producer — and this one was: `save_bindings` had exactly one
    production caller, inside ACTION_REDISCOVER, so within a normal
    bootstrap no binding was written; both identity guarantees
    read an absent file, and both fell through to PRESENT. That
    survived a reviewer, an acceptance and a supervisor while this
    suite stayed green.

    So within this class no test hand-builds a binding. What is
    injected is the DEPENDENCY'S ANSWER — a prober standing in for `herdr agent
    get`. Deciding what counts as evidence, and writing the file, is
    production's.
    """

    def herd(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        (repo / ".herd" / "state").mkdir(parents=True)
        return HerdrInstance(repo), repo

    AGENTS = {
        "supervisor": "h-sup", "lead1": "h-lead1",
        "executor1": "h-exec1", "reviewer1": "h-rev1",
    }

    def establish(self, agents=None, prober=None, **kwargs):
        herd, repo = self.herd()
        probe = prober or (
            lambda agent: info(agent_payload(
                name=agent, pane="w1:p" + agent[-1],
                session="sess-" + agent,
            ))
        )
        kwargs.setdefault("settle_seconds", 0.0)
        kwargs.setdefault("sleeper", lambda _seconds: None)
        document = lifecycle.establish_role_bindings(
            herd, agents or self.AGENTS, prober=probe, **kwargs
        )
        return repo, document

    def test_the_bootstrap_producer_WRITES_a_binding_per_role(self):
        """The defect, inverted: a herd that has just bootstrapped has
        a bindings file naming every logical role."""
        repo, document = self.establish()
        path = repo / ".herd" / "state" / identity.BINDINGS_FILE
        self.assertTrue(
            path.exists(),
            "bootstrap completed and wrote NO binding; that is the"
            " R-53 defect exactly",
        )
        on_disk = json.loads(path.read_text())
        self.assertEqual(
            sorted(on_disk["roles"]), sorted(self.AGENTS),
            "the bindings document does not name every logical role",
        )
        for logical, agent in self.AGENTS.items():
            binding = on_disk["roles"][logical]
            self.assertEqual(binding["agent"], agent)
            self.assertEqual(binding["session"], "sess-" + agent)
            self.assertIsNone(
                identity.binding_gap(binding),
                "the producer wrote a binding that `classify` will"
                " read as UNBOUND",
            )

    def test_a_role_bound_by_the_producer_classifies_PRESENT(self):
        """END TO END, and the assertion that makes the fix mean
        something: the producer's output is what the consumer needs.
        A test that only checked the file existed would pass on a
        document `classify` rejects."""
        repo, document = self.establish()
        for logical, agent in self.AGENTS.items():
            binding = document["roles"][logical]
            verdict = identity.classify(logical, binding, info(
                agent_payload(name=agent,
                              pane="w1:p" + agent[-1],
                              session="sess-" + agent)
            ))
            self.assertEqual(
                verdict.verdict, identity.VERDICT_PRESENT,
                "a freshly bound role did not read as PRESENT: %s"
                % verdict.detail,
            )
            self.assertEqual(verdict.bound_session, "sess-" + agent)

    def test_the_LOOKALIKE_GUARD_now_FIRES(self):
        """The guarantee R-53 found unreachable. With a real binding
        on disk, a live agent that disagrees on a stable field is
        REFUSED instead of adopted."""
        repo, document = self.establish()
        binding = document["roles"]["supervisor"]
        verdict = identity.classify("supervisor", binding, info(
            agent_payload(name="h-sup", pane="w1:pp",
                          cwd="/a/different/repo",
                          session="sess-h-sup")
        ))
        self.assertEqual(verdict.verdict, identity.VERDICT_DEGRADED)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_STABLE_MISMATCH)

    def test_the_SESSION_REPLACEMENT_detector_now_FIRES(self):
        """The other guarantee R-53 found unreachable."""
        repo, document = self.establish()
        binding = document["roles"]["supervisor"]
        verdict = identity.classify("supervisor", binding, info(
            agent_payload(name="h-sup", pane="w1:pp",
                          session="a-replacement-session")
        ))
        self.assertEqual(verdict.verdict, identity.VERDICT_REPLACED)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_SESSION_REPLACED)

    def test_BOTH_guards_are_UNREACHABLE_without_the_producer(self):
        """THE REGRESSION PIN for the defect itself.

        Drive the two guards with the state a herd was ACTUALLY in
        before this fix — no binding at all — and assert neither
        reports healthy. Before R-53 both of these returned PRESENT.
        """
        for probe in (
            info(agent_payload(cwd="/a/different/repo")),
            info(agent_payload(session="a-replacement-session")),
        ):
            with self.subTest(probe=probe["raw"]["result"]["agent"]["cwd"]):
                verdict = identity.classify("supervisor", {}, probe)
                self.assertNotEqual(
                    verdict.verdict, identity.VERDICT_PRESENT,
                    "an unbound role reported healthy; that is the"
                    " fail-open direction R-53 closed",
                )
                self.assertEqual(verdict.verdict,
                                 identity.VERDICT_UNBOUND)

    def test_a_role_with_NO_readable_record_is_NOT_bound(self):
        """Exact live evidence, and within this producer nothing
        less. A binding assembled from a partial answer would read as
        authoritative and compare wrongly for the herd's life."""
        def prober(agent):
            if agent == "h-rev1":
                return {"status": "missing", "raw": None}
            return info(agent_payload(name=agent))

        with self.assertRaises(lifecycle.BindingsNotEstablished) as ctx:
            self.establish(prober=prober)
        self.assertIn("h-rev1", str(ctx.exception))

    def test_a_role_with_NO_SESSION_is_NOT_bound(self):
        """The half a presence check would miss: a record exists and
        carries no session id, so within this comparison it supports
        nothing PRESENT asserts."""
        def prober(agent):
            payload = agent_payload(name=agent)
            if agent == "h-lead1":
                payload["agent_session"] = {}
            return info(payload)

        with self.assertRaises(lifecycle.BindingsNotEstablished):
            self.establish(prober=prober)

    def test_a_FAILED_bind_writes_NOTHING(self):
        """K-1's direction: a herd that could not bind every role must
        not be left with a PARTIAL document naming some of them, which
        would read as authoritative for the roles it omits."""
        def prober(agent):
            if agent == "h-rev1":
                return {"status": "missing", "raw": None}
            return info(agent_payload(name=agent))

        herd, repo = self.herd()
        with self.assertRaises(lifecycle.BindingsNotEstablished):
            lifecycle.establish_role_bindings(
                herd, self.AGENTS, prober=prober, settle_seconds=0.0,
                sleeper=lambda _seconds: None,
            )
        self.assertFalse(
            (repo / ".herd" / "state"
             / identity.BINDINGS_FILE).exists(),
            "a partial bindings document was left on disk; the roles"
            " it omits then classify as UNBOUND while the file itself"
            " looks intact",
        )

    def test_a_role_that_SETTLES_LATE_is_bound_rather_than_refused(self):
        """A role contracted seconds ago may not be listed yet. The
        bounded settle window is what keeps that from failing a
        healthy bootstrap; it is a local probe bound, and nothing an
        agent does is bounded by it."""
        answers = {"h-rev1": 2}

        def prober(agent):
            remaining = answers.get(agent, 0)
            if remaining:
                answers[agent] = remaining - 1
                return {"status": "missing", "raw": None}
            return info(agent_payload(name=agent))

        ticks = [0.0]

        repo, document = self.establish(
            prober=prober, settle_seconds=5.0,
            clock=lambda: ticks[0],
            sleeper=lambda seconds: ticks.__setitem__(
                0, ticks[0] + seconds
            ),
        )
        self.assertIn("reviewer1", document["roles"])

    def test_the_producer_is_CALLED_BY_BOOTSTRAP(self):
        """AQ-2 at the seam.

        SOURCE IS THE ONLY FEASIBLE LEVEL here, with the reason:
        `start_herd` opens a Herdr workspace, splits panes and starts
        four real agents, so there is no hermetic way to execute it
        and observe the call. A unit test of the producer proves it
        works while leaving open whether bootstrap RUNS it — which is
        the gap that produced this ruling — so the call site is
        asserted from the source instead.

        EXECUTED PIN carrying the behaviour: this class's own
        `test_the_bootstrap_producer_WRITES_a_binding_per_role` and
        `test_a_FAILED_bind_writes_NOTHING` drive what the call does,
        and `tests/test_lifecycle.py` executes `start_herd` end to end
        with the dependency mocked — it FAILED on the binding step
        until that test was given a probe to answer with, which is
        the executed evidence that the call is really on the path.
        """
        source = inspect.getsource(lifecycle.start_herd)
        self.assertIn(
            "establish_role_bindings(herd, agents)", source,
            "bootstrap does not call the binding producer, so a"
            " normally bootstrapped herd writes no binding — the R-53"
            " defect, restored",
        )
        tree = ast.parse(inspect.getsource(lifecycle))
        start = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "start_herd"
        )
        calls = [
            node for node in ast.walk(start)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "establish_role_bindings"
        ]
        self.assertEqual(
            len(calls), 1,
            "expected exactly one binding-establishment call in"
            " bootstrap; found %d" % len(calls),
        )

    def test_NO_TEST_IN_THIS_CLASS_hand_builds_a_binding(self):
        """THE TRAP, PINNED.

        SOURCE IS THE ONLY FEASIBLE LEVEL, and here that is not a
        concession: the property IS textual. "No test in this class
        builds its own input" is a statement about the test code, and
        test code has no behaviour of its own to execute — the same
        reasoning the absolute-claim closure records for prose.

        EXECUTED PIN behind it: `test_the_bootstrap_producer_WRITES_a
        _binding_per_role`, which fails if the producer stops
        producing — the failure this class exists to be able to see,
        and the one a hand-built binding would hide.

        This class exists to exercise the producer, and a hand-built
        binding inside it would restore the exact blindness R-53
        named: an instrument supplying its own
        input is, within this suite, unable to detect one.
        A NOTE ON THIS CHECK ITSELF, recorded rather than quietly
        fixed: the first version searched the class's SOURCE TEXT for
        "save_bindings" and failed on its own docstring, which names
        the function it forbids. A substring scan is, within this check, unable to tell a
        WRITE from a MENTION — the same wrong-domain shape as counting
        the words in one's own prose. It walks CALLS now.
        """
        tree = ast.parse(inspect.getsource(BootstrapProducerTests))
        forbidden = {"save_bindings", "binding_for"}
        offenders = sorted({
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for name in [getattr(node.func, "attr", None)
                         or getattr(node.func, "id", None)]
            if name in forbidden
        })
        self.assertEqual(
            offenders, [],
            "a test in the producer class CALLS %s, building the"
            " artifact under test by hand; that is the instrument"
            " manufacturing its own input" % ", ".join(offenders),
        )
        # And the check is not vacuous: the same walk over a class
        # that DOES hand-build finds it.
        control = ast.parse(inspect.getsource(RefusalOrderingTests))
        self.assertTrue(
            [node for node in ast.walk(control)
             if isinstance(node, ast.Call)
             and (getattr(node.func, "attr", None)
                  or getattr(node.func, "id", None)) in forbidden],
            "the detector finds no hand-built binding in a class that"
            " has several, so a clean result above proves nothing",
        )


class BindingsDocumentTests(unittest.TestCase):
    """R-53 AQ-5: each read failure DECIDED, and the write ATOMIC."""

    def herd_root(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / ".herd"
        (root / "state").mkdir(parents=True)
        return root

    def test_an_ABSENT_document_is_an_empty_document(self):
        """The one case that returns empty, and the reason: within
        this reader a herd that has bound nothing has no file."""
        loaded = identity.load_bindings(self.herd_root())
        self.assertEqual(loaded, {"version": 1, "roles": {}})

    def test_a_MALFORMED_document_is_REFUSED_not_rebuilt(self):
        root = self.herd_root()
        path = identity.bindings_path(root)
        path.write_text("{not json")
        with self.assertRaises(identity.BindingsCorrupt):
            identity.load_bindings(root)
        self.assertEqual(
            path.read_text(), "{not json",
            "the corrupt document was rebuilt; that destroys the only"
            " evidence that something wrote it wrong",
        )

    def test_a_WRONG_SHAPED_document_is_REFUSED(self):
        root = self.herd_root()
        for payload in ("[]", '"text"', "{}", '{"roles": []}'):
            with self.subTest(payload=payload):
                identity.bindings_path(root).write_text(payload)
                with self.assertRaises(identity.BindingsCorrupt):
                    identity.load_bindings(root)

    def test_an_UNREADABLE_document_is_REFUSED_not_read_as_absent(self):
        """An unreadable file is not an absent one. Treating it as
        absent would let the next save overwrite bindings this
        process could not see."""
        root = self.herd_root()
        path = identity.bindings_path(root)
        path.write_text('{"version": 1, "roles": {}}')
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o600)
        if os.access(path, os.R_OK):        # pragma: no cover
            self.skipTest("this process can read a mode-0 file")
        with self.assertRaises(identity.BindingsCorrupt):
            identity.load_bindings(root)

    def test_the_write_is_ATOMIC(self):
        """A crash mid-write must leave the PREVIOUS document, and
        within this write never a half-written one — a partial binding
        reads as authoritative for the roles it names and leaves the
        rest UNBOUND while the file looks intact.

        Driven by failing the write after the temp file exists and
        before the rename, which is the only window a non-atomic
        implementation could leave open.
        """
        root = self.herd_root()
        path = identity.bindings_path(root)
        identity.save_bindings(root, {"version": 1, "roles": {"a": 1}})
        before = path.read_bytes()
        with patch("herdr.identity.os.replace",
                   side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                identity.save_bindings(
                    root, {"version": 1, "roles": {"b": 2}}
                )
        self.assertEqual(
            path.read_bytes(), before,
            "a failed write changed the durable document; a partially"
            " written binding is worse than none because it reads as"
            " authoritative",
        )

    def test_no_PARTIAL_file_is_left_beside_the_document(self):
        root = self.herd_root()
        identity.save_bindings(root, {"version": 1, "roles": {}})
        leftovers = sorted(
            entry.name for entry in (root / "state").iterdir()
            if entry.name.endswith(".partial")
        )
        self.assertEqual(leftovers, [])

    def test_a_saved_document_reloads_EXACTLY(self):
        root = self.herd_root()
        document = {"version": 1, "roles": {
            "supervisor": identity.binding_for(
                "supervisor", "h-sup", agent_payload()
            ),
        }}
        identity.save_bindings(root, document)
        self.assertEqual(identity.load_bindings(root), document)


class _DependencyContractAssertions(object):
    """The dependency contract, shared by a hermetic fixture and live probe."""

    def records(self):
        return identity.listed_agents(self.listing)

    def test_the_consumer_reads_the_dependency_envelope(self):
        self.assertTrue(
            self.records(),
            "the listing produced no records through the consumer's"
            " own reader; the envelope has moved",
        )

    def test_every_stable_field_exists_on_every_dependency_record(self):
        for record in self.records():
            with self.subTest(name=record.get("name")):
                for field in identity.STABLE_FIELDS:
                    self.assertIsInstance(
                        record.get(field), str,
                        "%s is not a string on a dependency record" % field,
                    )

    def test_every_transient_field_exists_on_every_dependency_record(self):
        for record in self.records():
            with self.subTest(name=record.get("name")):
                for field in identity.TRANSIENT_FIELDS:
                    self.assertIn(field, record)

    def test_the_session_value_domain_is_a_non_empty_string(self):
        values = {identity.session_value(record)
                  for record in self.records()}
        self.assertNotIn(None, values,
                         "a dependency record carries no session value")
        self.assertEqual(
            len(values), len(self.records()),
            "two dependency agents share a session id; the replacement"
            " check compares on a value that is not unique",
        )

    def test_agent_status_domain_excludes_the_probe_sentinels(self):
        """Probe-failure sentinels are not dependency agent statuses."""
        statuses = {record.get("agent_status")
                    for record in self.records()}
        self.assertTrue(statuses)
        self.assertNotIn("missing", statuses)
        self.assertNotIn("unknown", statuses)

    def test_rediscovery_fields_identify_a_dependency_agent_uniquely(self):
        seen = {}
        for record in self.records():
            key = tuple(record.get(field)
                        for field in identity.REDISCOVERY_FIELDS)
            seen.setdefault(key, []).append(record.get("name"))
        collisions = {key: names for key, names in seen.items()
                      if len(names) > 1}
        self.assertEqual(
            collisions, {},
            "the fields rediscovery matches on are not unique across"
            " the dependency records, so an exact match could be"
            " ambiguous: %s" % collisions,
        )

    def test_a_dependency_record_classifies_against_its_own_binding(self):
        """A forwarded session value reaches and changes the verdict."""
        record = self.records()[0]
        binding = identity.binding_for(
            "probe", record.get("name"), record
        )
        present = identity.classify("probe", binding, info(record))
        self.assertEqual(present.verdict, identity.VERDICT_PRESENT)

        moved = json.loads(json.dumps(record))
        moved["agent_session"]["value"] = "a-different-session"
        replaced = identity.classify("probe", binding, info(moved))
        self.assertEqual(replaced.verdict, identity.VERDICT_REPLACED)
        self.assertNotEqual(present.action, replaced.action)


class HermeticDependencyContractTests(
        _DependencyContractAssertions, unittest.TestCase):
    """The committed dependency contract, driven by fixed records.

    This is the normal unit/regression specimen.  It preserves the exact
    envelope, field-domain, uniqueness, forwarding, and classification
    assertions without consulting an installed CLI or any active herd.
    """

    listing = list_envelope([
        agent_payload(name="fixture-supervisor", session="fixture-s1"),
        agent_payload(
            name="fixture-executor", workspace="fixture-w2",
            pane="fixture-w2:p1", session="fixture-s2",
        ),
    ])


class RealDependencyContractTests(
        _DependencyContractAssertions, unittest.TestCase):
    """Opt-in live specimen against the REAL `herdr` binary.

    Read-only: within this class the only command run is `agent
    list`. It starts, prompts, clears and kills no agent, which
    matters because most of the agents it observes belong to other
    herds.

    The hermetic class above always exercises equivalent contract
    assertions.  This live specimen is intentionally opt-in because an
    installed CLI and populated agent fleet are external integration state,
    not prerequisites of a clean-clone unit suite.
    """

    @classmethod
    def setUpClass(cls):
        if os.environ.get("DI_RUN_LIVE_HERDR_CONTRACT") != "1":
            raise unittest.SkipTest(
                "set DI_RUN_LIVE_HERDR_CONTRACT=1 to run the read-only"
                " installed-Herdr integration specimen"
            )
        try:
            result = subprocess.run(
                ["herdr", "agent", "list"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise unittest.SkipTest(
                "the installed `herdr agent list` integration specimen"
                " is unavailable: %s" % error
            )
        if result.returncode != 0:
            raise unittest.SkipTest(
                "the real `herdr agent list` did not succeed; the"
                " contract is UNVERIFIED here"
            )
        try:
            cls.listing = json.loads(result.stdout)
        except ValueError:
            raise unittest.SkipTest("real listing did not parse")


class SeamPinTests(unittest.TestCase):
    """The double accepts NO MORE than production does.

    Source is the only feasible level for reading the caller's keyword
    set out of the call site; the acceptance half below is proven by
    EXECUTION against the production callables.
    """

    def caller_keywords(self, module, attribute):
        tree = ast.parse(inspect.getsource(module))
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if getattr(func, "attr", None) != attribute:
                continue
            found.append((len(node.args),
                          sorted(k.arg for k in node.keywords)))
        return found

    def test_clear_contexts_accepts_exactly_what_the_cli_passes(self):
        import herdctl
        calls = self.caller_keywords(herdctl, "clear_contexts")
        self.assertEqual(len(calls), 1, calls)
        positional, keywords = calls[0]
        signature = inspect.signature(tasks.clear_contexts)
        signature.bind(*range(positional),
                       **{name: None for name in keywords})

    def test_clear_contexts_declares_no_undeclared_parameter(self):
        self.assertEqual(
            list(inspect.signature(tasks.clear_contexts).parameters),
            ["herd", "lister"],
        )

    def test_the_lister_seam_is_narrower_than_a_free_callable(self):
        """The injected lister stands in for `herdr agent list`. It is
        called with no arguments, so a double demanding one would be
        wider than production."""
        signature = inspect.signature(tasks._production_listing)
        self.assertEqual(list(signature.parameters), [])


if __name__ == "__main__":
    unittest.main()
