"""Post-bootstrap startup failure: a bounded, private diagnostic written BEFORE cleanup.

The defect this closes: a role-binding failure after bootstrap destroyed its own diagnosis. The 30 s settle
window expired, ``establish_role_bindings`` raised, ``start_herd``'s handler closed the workspace and removed
``runtime.json``, and the caller discarded the exception object. Nothing said which role lacked which binding
field.

What these tests pin (local scripted seams only: no real Herdr, provider, spawn or transport):

- the failure CARRIES its gaps: a typed exception with the stage and one entry per affected logical role, built
  from ``identity.binding_gap``'s existing reason strings; an absent session is recorded as ABSENT, never guessed
  and never fabricated; the legacy message text is unchanged;
- the diagnostic is written BEFORE the workspace is closed, survives the cleanup, and a diagnostic that cannot be
  written never replaces the original failure, skips cleanup or turns a failure into success;
- an unknown failure after bootstrap is labelled UNKNOWN (its class name only: its message is never stored);
- nothing private is collected: canary values planted in every place over-collection would show are absent;
- every bound is exact-pinned, oversized input is bounded, retention is capped and independent of the other kind;
- a successful startup is unchanged.

The earlier lost exception stays unknown. Nothing here claims to reproduce it.
"""

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from herdr import identity
from herdr import lifecycle
from herdr import runtime as runtime_module
from herdr.lifecycle import BindingsNotEstablished, start_herd

from test_lifecycle import HerdrLifecycleTests, _bound_probe

# Values planted where over-collection would show. None of them may appear in any diagnostic or summary.
SESSION_CANARY = "SESSION-VALUE-CANARY-7731"
PROMPT_CANARY = "PROMPT-TEXT-CANARY-5520"
TRANSCRIPT_CANARY = "PRIVATE-PANE-TRANSCRIPT-CANARY-9014"
ENV_CANARY = "ENVIRONMENT-VALUE-CANARY-3388"
EXCEPTION_CANARY = "EXCEPTION-MESSAGE-CANARY-2207"
STATUS_CANARY = "STATUS-VALUE-CANARY-6612"
ROLE_FILE_CANARY = "ROLE-FILE-CONTENT-CANARY-4419"
# The one CREDENTIAL-SHAPED canary (the others cover session values, prompts, transcripts, the environment,
# exception text, statuses and role files). Its shape is the point, so it is assembled at run time: the value is
# unchanged, but no credential-shaped literal exists in this file for a scanner to match.
TOKEN = "sk-" + "live" + "0123456789" + "abcdef" + "XYZ"
CANARIES = (SESSION_CANARY, PROMPT_CANARY, TRANSCRIPT_CANARY, ENV_CANARY, EXCEPTION_CANARY,
            STATUS_CANARY, ROLE_FILE_CANARY, TOKEN)

AGENTS = {"supervisor": "h-sup", "lead1": "h-lead1", "executor1": "h-exec1", "reviewer1": "h-rev1"}


def probe_answer(agent, session=True, stable=True, status="idle", **extra):
    """Herdr's answer for one agent: ``session`` and ``stable`` choose which binding fields the record carries."""
    record = {}
    if stable:
        record.update(name=agent, cwd="/repo", workspace_id="ws1", pane_id="pane-" + agent)
    if session:
        record["agent_session"] = {"value": SESSION_CANARY}
    record.update(extra)
    return {"status": status, "raw": {"result": {"agent": record}}}


def plant(record):
    """The same record with private-looking data attached everywhere a careless collector would look."""
    return dict(record, prompt=PROMPT_CANARY, transcript=TRANSCRIPT_CANARY,
                env={"SECRET_NAME": ENV_CANARY}, argv=["codex", "--api-key", TOKEN])


def prober_for(per_agent):
    def prober(agent):
        return per_agent.get(agent, probe_answer(agent))
    return prober


def establish(prober, agents=AGENTS):
    temp = tempfile.TemporaryDirectory()
    herd = SimpleNamespace(herd_root=Path(temp.name) / ".herd", repo=Path(temp.name))
    herd.herd_root.mkdir()
    try:
        return lifecycle.establish_role_bindings(
            herd, agents, prober=prober, settle_seconds=0.0, sleeper=lambda _seconds: None)
    finally:
        temp.cleanup()


def gap_of(prober):
    with_error = None
    try:
        establish(prober)
    except BindingsNotEstablished as error:
        with_error = error
    assert with_error is not None, "establish_role_bindings did not raise"
    return with_error


class FailureCarriesItsGapsTests(unittest.TestCase):

    def test_done_or_interactive_ready_but_missing_session_is_recorded_ABSENT(self):
        for status in ("idle", "done"):
            with self.subTest(status=status):
                error = gap_of(prober_for({"h-lead1": probe_answer("h-lead1", session=False, status=status)}))
                self.assertEqual(error.stage, lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS)
                self.assertEqual(
                    error.roles,
                    [{"logical": "lead1", "agent": "h-lead1",
                      "reason": identity.binding_gap({"logical": "lead1", "agent": "h-lead1",
                                                      "stable": {"name": "x"}, "session": None}),
                      "session": identity.SESSION_ABSENT}])
                self.assertEqual(error.roles[0]["reason"], "the recorded binding carries no session")

    def test_partial_and_multiple_role_gaps_each_get_their_own_exact_entry(self):
        error = gap_of(prober_for({
            "h-sup": {"status": "missing", "raw": None},                      # no readable record
            "h-lead1": probe_answer("h-lead1", session=False),                # record, no session
            "h-exec1": probe_answer("h-exec1", stable=False),                 # session, no stable identity
        }))
        by_role = {entry["logical"]: entry for entry in error.roles}
        self.assertEqual(sorted(by_role), ["executor1", "lead1", "supervisor"])   # reviewer1 is fine: not listed
        self.assertEqual(by_role["supervisor"]["session"], identity.SESSION_UNKNOWN)
        self.assertIn("no readable record", by_role["supervisor"]["reason"])
        self.assertEqual(by_role["lead1"]["session"], identity.SESSION_ABSENT)
        self.assertEqual(by_role["lead1"]["reason"], "the recorded binding carries no session")
        self.assertEqual(by_role["executor1"]["session"], identity.SESSION_PRESENT)
        self.assertEqual(by_role["executor1"]["reason"], "the recorded binding carries no stable identity")
        self.assertEqual([entry["logical"] for entry in error.roles], sorted(by_role),
                         "entries are in a stable (sorted) order")

    def test_nothing_is_fabricated_a_present_session_value_is_never_stored(self):
        error = gap_of(prober_for({"h-exec1": probe_answer("h-exec1", stable=False)}))
        self.assertEqual(error.roles[0]["session"], identity.SESSION_PRESENT)
        self.assertNotIn(SESSION_CANARY, json.dumps(error.roles))
        absent = gap_of(prober_for({"h-lead1": probe_answer("h-lead1", session=False)}))
        self.assertEqual(absent.roles[0]["session"], identity.SESSION_ABSENT)
        self.assertEqual(set(absent.roles[0]), {"logical", "agent", "reason", "session"})

    def test_the_legacy_message_and_the_binding_predicate_are_unchanged(self):
        error = gap_of(prober_for({"h-lead1": probe_answer("h-lead1", session=False),
                                   "h-rev1": {"status": "missing", "raw": None}}))
        self.assertEqual(
            str(error),
            "could not bind 2 role(s) from live evidence within 0s: "
            "lead1: the recorded binding carries no session; "
            "reviewer1: Herdr returned no readable record for `h-rev1` (status 'missing')."
            " The herd is NOT reported ready, because an unbound role classifies as UNBOUND and nothing later"
            " can tell that apart from a role that never started")
        self.assertEqual(lifecycle.BINDING_PROBE_SETTLE_SECONDS, 30.0)
        self.assertEqual(lifecycle.BINDING_PROBE_POLL_SECONDS, 0.5)

    def test_an_unexpected_status_text_is_labelled_UNKNOWN_in_the_entry_not_copied(self):
        error = gap_of(prober_for({"h-rev1": {"status": STATUS_CANARY, "raw": None}}))
        self.assertNotIn(STATUS_CANARY, json.dumps(error.roles))
        self.assertIn("UNKNOWN", error.roles[0]["reason"])

    def test_every_known_real_status_is_kept(self):
        for status in ("blocked", "done", "idle", "working", "missing", "unknown"):
            error = gap_of(prober_for({"h-rev1": {"status": status, "raw": None}}))
            self.assertIn("status %s" % status, error.roles[0]["reason"], status)

    def test_a_successful_bind_is_unchanged(self):
        bindings = establish(prober_for({}))
        self.assertEqual(sorted(bindings["roles"]), sorted(AGENTS))
        self.assertEqual(bindings["roles"]["lead1"]["session"], SESSION_CANARY)  # the real bind keeps the real id

    def test_the_old_two_value_evidence_function_still_returns_what_it_returned(self):
        binding, gap = lifecycle.role_binding_evidence("lead1", "h-lead1", lambda a: probe_answer(a, session=False))
        self.assertIsNone(binding)
        self.assertEqual(gap, "the recorded binding carries no session")
        binding, gap = lifecycle.role_binding_evidence("lead1", "h-lead1", lambda a: probe_answer(a))
        self.assertIsNone(gap)
        self.assertEqual(binding["logical"], "lead1")


class StartHerdFixture(unittest.TestCase):
    """Drives the REAL ``start_herd`` over scripted seams, recording the order of cleanup against the diagnostic."""

    def run_start(self, prober=None, establish_override=None, start_agent_override=None, heartbeat_override=None,
                  capture_override=None, build_override=None, role_file_canary=False, expect=BindingsNotEstablished,
                  settle=0.0, prepare=None):
        temp, herd = HerdrLifecycleTests.make_instance(self)
        self.addCleanup(temp.cleanup)
        if prepare is not None:
            prepare(herd)
        if role_file_canary:
            (herd.herd_root / "roles" / "lead.md").write_text("# lead\n" + ROLE_FILE_CANARY + "\n")
        self.herd = herd
        state = herd.herd_root / "state"
        self.state = state
        self.log = []

        def diagnostics_now():
            directory = state / "diagnostics"
            return sorted(p.name for p in directory.glob(runtime_module.BINDING_DIAGNOSTIC_FILE_PREFIX + "*.json")
                          ) if directory.exists() else []

        def lifecycle_run(cmd, *args, **kwargs):
            self.log.append(("run", tuple(cmd), diagnostics_now(), (state / "runtime.json").exists()))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_prompt(agent, text, timeout, wait):
            self.log.append(("prompt", agent))
            return SimpleNamespace(returncode=0, stdout=TRANSCRIPT_CANARY, stderr=PROMPT_CANARY)

        splits = iter(["pane-lead", "pane-executor", "pane-reviewer", "pane-controller"])
        patches = [
            patch("herdr.lifecycle.agent_info", new=prober or _bound_probe),
            patch("herdr.lifecycle.run", side_effect=lifecycle_run),
            patch("herdr.lifecycle.prompt", side_effect=fake_prompt),
            patch("herdr.lifecycle.start_agent", new=start_agent_override or (lambda *a, **k: None)),
            patch("herdr.lifecycle.split", side_effect=lambda *a, **k: next(splits)),
            patch("herdr.lifecycle.jrun", return_value={"result": {
                "workspace": {"workspace_id": "ws1"}, "root_pane": {"pane_id": "pane-root"}}}),
            patch.object(lifecycle, "BINDING_PROBE_SETTLE_SECONDS", settle),
            patch("builtins.print"),
        ]
        if establish_override is not None:
            patches.append(patch("herdr.lifecycle.establish_role_bindings", new=establish_override))
        if heartbeat_override is not None:
            patches.append(patch("herdr.lifecycle.heartbeat_process_command", new=heartbeat_override))
        if capture_override is not None:
            patches.append(patch.object(runtime_module, "capture_binding_failure", capture_override))
        if build_override is not None:
            patches.append(patch.object(runtime_module, "build_binding_failure_record", build_override))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        if expect is None:
            return start_herd(herd)
        with self.assertRaises(expect) as caught:
            start_herd(herd)
        return caught.exception

    def only_diagnostic(self):
        files = sorted((self.state / "diagnostics").glob(runtime_module.BINDING_DIAGNOSTIC_FILE_PREFIX + "*.json"))
        self.assertEqual(len(files), 1, files)
        return files[0]

    def close_entries(self):
        return [entry for entry in self.log if entry[0] == "run" and entry[1][:3] == ("herdr", "workspace", "close")]


def missing_session(agent):
    record = probe_answer(agent, session=False)
    record["raw"]["result"]["agent"].update(prompt=PROMPT_CANARY, transcript=TRANSCRIPT_CANARY,
                                            env={"SECRET_NAME": ENV_CANARY})
    return record


class DiagnosticIsWrittenBeforeCleanupTests(StartHerdFixture):

    def test_a_binding_failure_writes_the_diagnostic_BEFORE_close_and_it_survives_cleanup(self):
        error = self.run_start(prober=missing_session)
        closes = self.close_entries()
        self.assertEqual(len(closes), 1)
        close = closes[0]
        self.assertEqual(close[1], ("herdr", "workspace", "close", "ws1"))
        self.assertEqual(len(close[2]), 1, "the diagnostic did not exist when the workspace was closed")
        self.assertTrue(close[3], "runtime.json was already gone when the workspace was closed")
        self.assertFalse((self.state / "runtime.json").exists())
        survivor = self.only_diagnostic()
        self.assertEqual(survivor.name, close[2][0])
        self.assertIsInstance(error, BindingsNotEstablished)
        self.assertEqual(oct(stat.S_IMODE(survivor.stat().st_mode)), "0o600")
        self.assertEqual(oct(stat.S_IMODE(survivor.parent.stat().st_mode)), "0o700")

    def test_the_record_binds_the_exact_attempt_stage_and_every_affected_role(self):
        self.run_start(prober=missing_session)
        record = json.loads(self.only_diagnostic().read_text())
        self.assertEqual(record["schema"], runtime_module.BINDING_DIAGNOSTIC_SCHEMA)
        self.assertEqual(record["stage"], lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS)
        self.assertEqual(record["failure"], {"type": "BINDINGS_NOT_ESTABLISHED", "class": "BindingsNotEstablished"})
        self.assertEqual(record["workspace"]["workspace_id"], "ws1")
        self.assertEqual(os.path.realpath(record["workspace"]["repo"]), os.path.realpath(str(self.herd.repo)))
        self.assertIsInstance(record["workspace"]["runtime_created_at"], int)
        self.assertEqual(record["settle_seconds"], 0.0)
        self.assertEqual(record["roles_total"], 4)
        self.assertFalse(record["roles_truncated"])
        self.assertEqual(sorted(r["logical"] for r in record["roles"]),
                         ["executor1", "lead1", "reviewer1", "supervisor"])
        for entry in record["roles"]:
            self.assertEqual(entry["session"], identity.SESSION_ABSENT)
            self.assertEqual(entry["reason"], "the recorded binding carries no session")
            self.assertTrue(entry["agent"].endswith({"supervisor": "-sup", "lead1": "-lead1",
                                                     "executor1": "-exec1", "reviewer1": "-rev1"}[entry["logical"]]))
        panes = {r["logical"]: r["pane"] for r in record["roles"]}
        self.assertEqual(panes["supervisor"], "pane-root")
        self.assertEqual(panes["lead1"], "pane-lead")
        self.assertTrue(record["limitations"])

    def test_the_original_exception_is_raised_unchanged_with_no_side_channel_attached(self):
        error = self.run_start(prober=missing_session)
        self.assertIsInstance(error, BindingsNotEstablished)
        self.assertFalse(hasattr(error, "startup_diagnostic"),
                         "the retained FILE is the one channel; nothing is attached to the exception")
        self.assertEqual(error.roles[0]["logical"], "executor1")      # the typed failure still carries its gaps

    def test_the_settle_bound_and_the_call_order_are_unchanged(self):
        self.run_start(prober=missing_session)
        order = [entry[1] if entry[0] == "prompt" else "close" for entry in self.log
                 if entry[0] == "prompt" or (entry[0] == "run" and entry[1][:3] == ("herdr", "workspace", "close"))]
        self.assertEqual(order[-1], "close")
        self.assertEqual(len([o for o in order if o != "close"]), 4)       # every role was still bootstrapped first
        self.assertEqual(lifecycle.BINDING_PROBE_SETTLE_SECONDS, 0.0)       # patched by this fixture only
        # the module's own default, read from source rather than from the patched attribute
        self.assertIn("BINDING_PROBE_SETTLE_SECONDS = 30.0", Path(lifecycle.__file__).read_text())

    def test_the_diagnostic_directory_is_the_existing_private_one(self):
        self.run_start(prober=missing_session)
        self.assertEqual(self.only_diagnostic().parent, self.state / "diagnostics")


class DiagnosticFailureNeverReplacesTheOriginalTests(StartHerdFixture):

    def assert_original_and_cleanup(self, error):
        self.assertIsInstance(error, BindingsNotEstablished)
        self.assertEqual(error.stage, lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS)
        self.assertEqual(len(self.close_entries()), 1, "the workspace was not closed")
        self.assertFalse((self.state / "runtime.json").exists(), "runtime.json survived the cleanup")
        self.assertIn("could not bind", str(error))

    def test_a_write_that_raises_still_raises_the_ORIGINAL_failure_and_still_cleans_up(self):
        def boom(*args, **kwargs):
            raise OSError("disk full")
        error = self.run_start(prober=missing_session, capture_override=boom)
        self.assert_original_and_cleanup(error)
        self.assertNotIsInstance(error, OSError)
        directory = self.state / "diagnostics"
        self.assertFalse(directory.exists() and list(directory.glob("*.json")), "nothing half-written is left")

    def test_a_builder_that_raises_is_swallowed_the_same_way(self):
        def boom(*args, **kwargs):
            raise KeyError("unexpected shape")
        error = self.run_start(prober=missing_session, build_override=boom)
        self.assert_original_and_cleanup(error)
        self.assertFalse((self.state / "diagnostics").exists()
                         and list((self.state / "diagnostics").glob("*.json")),
                         "no record is invented when none could be built")

    def test_an_unwritable_diagnostic_directory_is_swallowed(self):
        def block(herd):
            (herd.herd_root / "state").mkdir(exist_ok=True)
            (herd.herd_root / "state" / "diagnostics").write_text("a file where the directory should be")
        error = self.run_start(prober=missing_session, prepare=block)
        self.assert_original_and_cleanup(error)

    def test_a_KeyboardInterrupt_in_the_diagnostic_is_not_swallowed_and_cleanup_still_ran(self):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt()
        interrupted = self.run_start(prober=missing_session, capture_override=interrupt, expect=KeyboardInterrupt)
        self.assertIsInstance(interrupted, KeyboardInterrupt)       # an interrupt is never swallowed ...
        self.assertEqual(len(self.close_entries()), 1)              # ... and the cleanup still ran
        self.assertFalse((self.state / "runtime.json").exists())


class UnknownAndOutOfScopeFailuresTests(StartHerdFixture):

    def test_an_unknown_failure_after_bootstrap_is_labelled_UNKNOWN_with_its_class_only(self):
        def explode(herd, agents):
            raise RuntimeError(EXCEPTION_CANARY + " " + TOKEN)
        error = self.run_start(establish_override=explode, expect=RuntimeError)
        self.assertIn(EXCEPTION_CANARY, str(error), "the ORIGINAL failure still carries its own message")
        record = json.loads(self.only_diagnostic().read_text())
        self.assertEqual(record["failure"], {"type": "UNKNOWN", "class": "RuntimeError"})
        self.assertEqual(record["roles"], [])
        self.assertEqual(record["roles_total"], 0)
        self.assertEqual(record["stage"], lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS)
        text = self.only_diagnostic().read_text()
        for canary in (EXCEPTION_CANARY, TOKEN):
            self.assertNotIn(canary, text)

    def test_a_failure_after_the_binding_stage_is_captured_with_its_own_stage(self):
        def explode(repo):
            raise ValueError(EXCEPTION_CANARY)
        error = self.run_start(heartbeat_override=explode, expect=ValueError)
        record = json.loads(self.only_diagnostic().read_text())
        self.assertEqual(record["stage"], lifecycle.STAGE_HEARTBEAT_CONTROLLER)
        self.assertEqual(record["failure"]["type"], "UNKNOWN")
        self.assertNotIn(EXCEPTION_CANARY, self.only_diagnostic().read_text())
        self.assertIn(EXCEPTION_CANARY, str(error), "the ORIGINAL failure still carries its own message")

    def test_a_failure_BEFORE_bootstrap_writes_no_binding_diagnostic(self):
        def refuse(*args, **kwargs):
            raise RuntimeError("start failed before any role was contracted")
        error = self.run_start(start_agent_override=refuse, expect=RuntimeError)
        directory = self.state / "diagnostics"
        self.assertFalse(directory.exists() and list(directory.glob(
            runtime_module.BINDING_DIAGNOSTIC_FILE_PREFIX + "*.json")))
        self.assertEqual(len(self.close_entries()), 1, "cleanup is unchanged for the earlier stage")

    def test_an_exception_with_a_hostile_class_is_labelled_by_its_bounded_name_only(self):
        class Hostile(Exception):
            def __str__(self):
                raise RuntimeError("str() of this exception raises")

            def __repr__(self):
                raise RuntimeError("repr() of this exception raises")

        def explode(herd, agents):
            raise Hostile(EXCEPTION_CANARY)
        error = self.run_start(establish_override=explode, expect=Hostile)
        self.assertEqual(len(self.close_entries()), 1)
        record = json.loads(self.only_diagnostic().read_text())
        self.assertEqual(record["failure"], {"type": "UNKNOWN", "class": "Hostile"})
        self.assertNotIn(EXCEPTION_CANARY, self.only_diagnostic().read_text())
        self.assertIsInstance(error, Hostile)


class SuccessfulStartupIsUnchangedTests(StartHerdFixture):

    def test_a_good_start_returns_the_runtime_state_writes_no_diagnostic_and_adds_no_command(self):
        state = self.run_start(expect=None)
        self.assertEqual(state["workspace_id"], "ws1")
        self.assertEqual(sorted(state["agents"]), sorted(AGENTS))
        directory = self.state / "diagnostics"
        self.assertFalse(directory.exists() and list(directory.iterdir()))
        commands = [entry[1] for entry in self.log if entry[0] == "run"]
        # the only lifecycle `run` calls on success are the heartbeat controller's, exactly as before
        self.assertEqual([c[:3] for c in commands], [("herdr", "pane", "run")])
        self.assertTrue((self.state / "runtime.json").exists())
        self.assertTrue((self.state / identity.BINDINGS_FILE).exists())


class PrivacyCeilingTests(StartHerdFixture):

    def test_planted_private_data_never_reaches_the_file_the_summary_or_the_message(self):
        def planted(agent):
            return {"status": STATUS_CANARY, "raw": {"result": {"agent": plant(dict(
                name=agent, cwd="/repo/" + ENV_CANARY, workspace_id="ws1", pane_id="pane-" + agent))}}}
        self.run_start(prober=planted, role_file_canary=True)
        text = self.only_diagnostic().read_text()
        for canary in CANARIES:
            self.assertNotIn(canary, text, canary)
        # ... and a record whose session IS present (but whose stable identity is not), private data attached
        def present_but_gapped(agent):
            return probe_answer(agent, stable=False, status="idle",
                                **{"prompt": PROMPT_CANARY, "transcript": TRANSCRIPT_CANARY})
        self.log = []
        self.run_start(prober=present_but_gapped)
        text = "".join(p.read_text() for p in (self.state / "diagnostics").glob("*.json"))
        for canary in CANARIES:
            self.assertNotIn(canary, text, canary)

    def test_the_record_holds_only_a_closed_list_of_known_fields(self):
        self.run_start(prober=missing_session)
        record = json.loads(self.only_diagnostic().read_text())
        self.assertEqual(set(record), {"schema", "redaction", "captured_at", "stage", "failure", "workspace",
                                       "settle_seconds", "roles_total", "roles_truncated", "roles",
                                       "limitations"})
        self.assertEqual(set(record["workspace"]), {"workspace_id", "repo", "runtime_created_at"})
        self.assertEqual(set(record["failure"]), {"type", "class"})
        for entry in record["roles"]:
            self.assertEqual(set(entry), {"logical", "agent", "pane", "reason", "session"})

    def test_the_file_states_the_redaction_limits_honestly(self):
        self.run_start(prober=missing_session)
        record = json.loads(self.only_diagnostic().read_text())
        self.assertIn("best-effort", record["redaction"])
        self.assertIn("not a guarantee", record["redaction"])
        joined = " ".join(record["limitations"])
        for needed in ("UNKNOWN", "message", "best-effort", "affected"):
            self.assertIn(needed, joined)


class CanaryShapeTests(unittest.TestCase):
    """The absence assertions are only as strong as their canaries. A bland value would pass them while proving less,
    so the credential-shaped canary is held to its shape AND its exact bytes."""

    def test_the_credential_canary_is_still_credential_shaped_and_byte_identical(self):
        # Behavioural: the existing redaction pass recognises it as a credential, which is what makes "never
        # appears in the file" mean something for credential-shaped content.
        self.assertEqual(runtime_module.redact_text(TOKEN), runtime_module.REDACTED)
        self.assertNotEqual(runtime_module.redact_text("PROMPT-TEXT-CANARY-5520"), runtime_module.REDACTED,
                            "the bland canaries are NOT credential-shaped: only this one covers that content")
        # Exact bytes: assembling it at run time did not change the value the assertions look for.
        self.assertEqual(len(TOKEN), 26)
        self.assertEqual(hashlib.sha256(TOKEN.encode("utf-8")).hexdigest(),
                         "d2d6280dad5ee5a450aa80bed5f516e7521981da5b83b43d4fde82049388ac17")


class BoundsAndRetentionTests(unittest.TestCase):

    def test_every_new_herdr_bound_is_exact_pinned(self):
        # herdr/ is outside BoundConstantPinTests' derivation (it walks product code outside herdr/), so the
        # bounds this module owns are pinned HERE by exact value.
        r = runtime_module
        self.assertEqual(r.BINDING_DIAGNOSTIC_MAX_ROLES, 32)
        self.assertEqual(r.BINDING_DIAGNOSTIC_ID_MAX_CHARS, 200)
        self.assertEqual(r.BINDING_DIAGNOSTIC_REASON_MAX_CHARS, 300)
        self.assertEqual(r.BINDING_DIAGNOSTIC_REPO_MAX_CHARS, 1024)
        self.assertEqual(r.BINDING_DIAGNOSTIC_FILE_MAX_CHARS, 65536)
        self.assertEqual(r.BINDING_DIAGNOSTIC_FILE_PREFIX, "startup-binding-failure-")
        self.assertEqual(r.BINDING_DIAGNOSTIC_SCHEMA, "herdr_startup_binding_failure_diagnostic_v1")
        self.assertEqual(r.DIAGNOSTIC_MAX_FILES, 20)          # retention is shared, not re-invented
        from target_runtime import prepare
        self.assertLessEqual(r.BINDING_DIAGNOSTIC_FILE_MAX_CHARS, prepare.MAX_INSTRUCTION_FILE_BYTES,
                             "the bridge reads this file back through the hardened 64 KiB reader")
        self.assertEqual(lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS, "establish_role_bindings")
        self.assertEqual(lifecycle.STAGE_HEARTBEAT_CONTROLLER, "heartbeat_controller")
        self.assertEqual(identity.KNOWN_AGENT_STATUSES,
                         ("blocked", "done", "idle", "working", "missing", "unknown"))
        self.assertEqual((identity.SESSION_ABSENT, identity.SESSION_PRESENT, identity.SESSION_UNKNOWN),
                         ("ABSENT", "PRESENT", "UNKNOWN"))

    def roles(self, count, size):
        return [{"logical": "role%d-" % i + "L" * size, "agent": "agent-" + "A" * size,
                 "pane": "pane-" + "P" * size, "reason": "R" * size, "session": identity.SESSION_ABSENT}
                for i in range(count)]

    def record(self, roles, **overrides):
        arguments = dict(stage=lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS,
                         failure={"type": "BINDINGS_NOT_ESTABLISHED", "class": "BindingsNotEstablished"},
                         workspace={"workspace_id": "w" * 5000, "repo": "/r/" + "x" * 5000,
                                    "runtime_created_at": 1},
                         settle_seconds=30.0, roles=roles)
        arguments.update(overrides)
        return runtime_module.build_binding_failure_record(**arguments)

    def test_oversized_input_is_bounded_counted_and_marked(self):
        record = self.record(self.roles(100, 5000))
        text = json.dumps(record)
        self.assertLessEqual(len(text), runtime_module.BINDING_DIAGNOSTIC_FILE_MAX_CHARS)
        self.assertEqual(record["roles_total"], 100)
        self.assertTrue(record["roles_truncated"])
        self.assertEqual(len(record["roles"]), runtime_module.BINDING_DIAGNOSTIC_MAX_ROLES)
        for entry in record["roles"]:
            for key in ("logical", "agent", "pane"):
                self.assertLessEqual(len(entry[key]), runtime_module.BINDING_DIAGNOSTIC_ID_MAX_CHARS + 20)
            self.assertLessEqual(len(entry["reason"]), runtime_module.BINDING_DIAGNOSTIC_REASON_MAX_CHARS + 20)
        self.assertLessEqual(len(record["workspace"]["repo"]), runtime_module.BINDING_DIAGNOSTIC_REPO_MAX_CHARS + 20)
        self.assertLessEqual(len(record["workspace"]["workspace_id"]),
                             runtime_module.BINDING_DIAGNOSTIC_ID_MAX_CHARS + 20)

    def test_the_largest_accepted_record_fits_the_file_cap_without_dropping_anything_else(self):
        record = self.record(self.roles(runtime_module.BINDING_DIAGNOSTIC_MAX_ROLES, 5000))
        self.assertLessEqual(len(json.dumps(record, indent=1, sort_keys=True)),
                             runtime_module.BINDING_DIAGNOSTIC_FILE_MAX_CHARS)
        self.assertFalse(record["roles_truncated"])

    def test_secret_shaped_values_are_redacted_by_the_existing_pass(self):
        record = self.record([{"logical": "lead1", "agent": TOKEN, "pane": "lc-" + "ab12" * 16,
                               "reason": "password=hunter2pass", "session": identity.SESSION_ABSENT}])
        text = json.dumps(record)
        for secret in (TOKEN, "lc-" + "ab12" * 16, "hunter2pass"):
            self.assertNotIn(secret, text)
        self.assertIn(runtime_module.REDACTED, text)

    def test_a_non_string_or_missing_field_is_bounded_not_trusted(self):
        record = self.record([{"logical": 5, "agent": None, "pane": {"x": 1}, "reason": ["a"], "session": "MADE-UP"},
                              "not a dict"])
        entry = record["roles"][0]
        self.assertEqual(entry["session"], identity.SESSION_UNKNOWN, "an unrecognised session state is not copied")
        self.assertEqual(len(record["roles"]), 1, "a malformed entry is dropped, not stringified")
        self.assertEqual(record["roles_total"], 2)

    def test_retention_is_capped_for_this_kind_and_independent_of_the_other(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "diagnostics"
            directory.mkdir(mode=0o700)
            other = [directory / ("agent-start-failure-x-%d.json" % i) for i in range(25)]
            for path in other:
                path.write_text("{}")
            for index in range(runtime_module.DIAGNOSTIC_MAX_FILES + 7):
                record = self.record(self.roles(1, 5))
                runtime_module.capture_binding_failure(directory, record)
            ours = sorted(directory.glob(runtime_module.BINDING_DIAGNOSTIC_FILE_PREFIX + "*.json"))
            self.assertEqual(len(ours), runtime_module.DIAGNOSTIC_MAX_FILES)
            self.assertEqual(len(list(directory.glob("agent-start-failure-*.json"))), 25,
                             "the existing diagnostic kind's files must not be pruned by this one")

    def test_a_symlinked_directory_is_refused_and_nothing_is_written_through_it(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "elsewhere"
            target.mkdir()
            link = Path(raw) / "diagnostics"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                runtime_module.capture_binding_failure(link, self.record(self.roles(1, 5)))
            self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
