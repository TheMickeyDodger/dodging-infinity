"""Deliverable 3, part 1 (integration across the REAL seam): one Mission Core Mission driven through its
whole local lifecycle by the real stack (the panel service, the bridge, Mission Core's own writers, the
retention coordinator and the startup path), with the REAL catalog answer captured at every step
(``query_v2`` with the default Mission Core reader and the real retention reader), exported for the server's
projection and the webview's consumed state.

Steps: of record (awaiting a decision) -> approved, never started -> start requested -> task observed ->
completed, canonical status ENDED, archived at once -> reclaimed by the STARTUP overdue sweep (with a second
Mission finished long ago and never seen by retention) -> deleted by its owner.

ENDED IS NOT VERIFIED. The panel's ``verify`` run is invoked, and Mission Core's record is COMPLETED with its
run verification recorded, but the producer's Mission State still reports progress IN_PROGRESS with
completion neither reported nor verified. The office's canonical status therefore reads Ended (D1's accepted
mapper: Verified needs the Mission State's completion to affirm verified success), and the step is named for
what it IS. The retention basis ``verified_target_stopped`` is a different fact (the capacity-release basis:
the record COMPLETED and its target stopped) and keeps its name.

Hermetic: every store lives in this test's own temporary directory (the panel fixture's), the clock is the
fixture's controlled clock, and nothing outside that directory is read or written; no user store is
touched. Two stand-ins make the export reproducible, and both are stated in the export's label:
``secrets.token_hex`` is a deterministic counter for this run only (so minted ids, and every digest over
them, are the same each run), and this test's temporary directory is written as a fixed placeholder, with
what is DERIVED from those paths recomputed for the placeholder by the producer's OWN functions, exactly as
it is computed: the store identity (``reclaim.store_identity``) and the harness's measured registry size.

ONE value is NORMALIZED rather than recomputed: a completed Mission's execution identity and historical
cursor (``mission_state.execution_identity``). Each digests this run's absolute workspace paths AND the
bridge's dispatch-request digest over them, which no public function recomputes; re-deriving it for the
placeholder would be inventing, not reproducing. So the test recomputes the REAL identity and cursor with the
producer's own ``listed_execution_identity`` and asserts them equal to the answer's (established), and the
export carries a labelled stand-in of the same shape (64 hex, derived from the Mission's own id).
"""
import hashlib
import itertools
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_mission_retention as rt  # noqa: E402
from mission_retention import reclaim  # noqa: E402

EXPORT = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "__tests__" / "fixtures" / "di-backend"
          / "mission-lifecycle-answers.FIXTURE.json")
PLACEHOLDER_ROOT = "/synthetic-test-root"
# The lifecycle Mission's objective: distinct from the overdue Mission's (the fixture's default), so a screen showing both never reads as one Mission
LIFECYCLE_OBJECTIVE = "Walk one Mission through every lifecycle step for the office preview"
IDENTITY_STAND_IN = "d3-lifecycle-normalized-execution-identity"
SOURCE_ID = "mc"
DAY = 24 * 60 * 60


def deterministic_token_hex():
    """A counter-based stand-in for ``secrets.token_hex``: the same ids every run (labelled in the export)."""
    counter = itertools.count(1)

    def token_hex(nbytes=None):
        n = 32 if nbytes is None else nbytes
        return hashlib.sha256(b"d3-lifecycle-%d" % next(counter)).hexdigest()[:2 * n]
    return token_hex


class MissionLifecycleAnswerTests(rt.RetentionFixture):
    """The same Mission through every local lifecycle step, read by the real catalog at each."""

    # the D2 startup helper, reused by reference (subclassing its test class would run its tests here too)
    started_panel = rt.StartupAndRunningPathTests.started_panel

    def setUp(self):
        patcher = mock.patch("secrets.token_hex", deterministic_token_hex())
        patcher.start()
        self.addCleanup(patcher.stop)
        super(MissionLifecycleAnswerTests, self).setUp()

    def observed_task(self, mission_id):
        """The runtime snapshot's sighting of this Mission's bound task, as the runtime capture would record it:
        the task its receipt names, in its prepared workspace, with one recorded role and NO live agent. A
        SYNTHETIC input (labelled in the export): no process is observed."""
        run = self.document("missions.json")["missions"][mission_id]["run"]
        return [{"task_id": run["receipt"]["task_id"], "workspace_realpath": run["workspace"]["path_realpath"],
                 "bindings": [{"logical": "lead", "role": "lead", "agent": "lead", "session": "session-lead",
                               "pane_id": "pane-lead", "workspace_id": "workspace-lead"}]}]

    def answer(self, tasks=()):
        """The REAL catalog answer now, composed as production composes it (target_runtime/catalog.py
        ``_request_v2``): ``query_v2`` with its default Mission Core reader, the Mission State composer's
        ``sample`` then ``compose`` (the producer's own progress and completion reports), and the real retention
        reader, over a registry document naming this test's store. No delivery store is registered (as in a
        process without one). Returns the answer and the exact registry document it measured."""
        from mission_retention import view as retention_view
        from mission_sources import contract
        from mission_sources import contract_v2
        from mission_sources import query_v2
        from target_runtime import mission_state as mission_state_module
        now = self.clock.now
        document = {"mission_sources_registry_schema_version": 1, "sources": [
            {"source_id": SOURCE_ID, "kind": "mission_core", "store_dir": self.state, "producer": "local_request",
             "max_age_seconds": 60, "registered_at": now}]}
        directory = os.path.join(self.tmp.name, "registry")

        class Registry(object):                    # the one method query_v2 calls
            def load_measured(self, absent_is_empty=True):
                return document, len(json.dumps(document))
        Registry.directory = directory
        snapshot = {"schema": contract.SNAPSHOT_SCHEMA, "version": contract.SNAPSHOT_VERSION,
                    "observed_at": now, "producer": "synthetic", "tasks": list(tasks), "agents": []}
        chains = {"schema": contract_v2.CHAIN_OBSERVATION_SCHEMA,
                  "version": contract_v2.CHAIN_OBSERVATION_VERSION, "observed_at": now,
                  "producer": "synthetic", "roots": [], "unavailable": [], "over_capacity": []}
        composer = mission_state_module.MissionStateComposer(self.clock, None)
        samples = composer.sample(document["sources"], None)

        def compose(source, views, chain_reading):
            return composer.compose(samples, source, views, chain_reading, None)
        reader = retention_view.RetentionReader()
        response = query_v2.query_v2(Registry(), snapshot, chains, now, compose=compose,
                                     retention_of=reader.block, retained_of=reader.retained,
                                     max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS)
        return response, document

    def mission(self, response, mission_id):
        return next(m for m in response["missions"] if m["native_id"] == mission_id)

    def lifecycle(self):
        steps = []

        def capture(step, tasks=()):
            response, document = self.answer(tasks)
            steps.append({"step": step, "now": self.clock.now, "response": response, "registry_document": document,
                          "missions": self.document("missions.json")["missions"]})

        # a second Mission, finished at once and never seen by retention, for the startup overdue sweep
        overdue = self.completed()
        self.clock.now += DAY
        # its own objective, so the two Missions read apart on every surface (the overdue one keeps the fixture's default objective)
        view = self.of_record(objective=LIFECYCLE_OBJECTIVE)
        capture("of_record")
        self.approve(view)
        capture("approved_never_started")
        self.service.run(view["request_ref"], "dispatch")
        capture("start_requested")
        self.observer.raw = rt.raw_observation()
        self.service.run(view["request_ref"], "observe")
        # the runtime sees the bound task, and no worker on it: observed, never "running"
        capture("task_observed", self.observed_task(view["mission_id"]))
        self.finish_team(view["mission_id"])
        self.service.run(view["request_ref"], "verify")
        capture("completed_ended_archived")
        # the deadline passes while the office is not running: the STARTUP path reclaims both, the
        # long-overdue Mission (never observed by retention) and this one exactly at its deadline
        self.clock.now = self.terminal_at(view["mission_id"]) + reclaim.RETENTION_SECONDS
        self.started_panel()
        capture("reclaimed_at_startup")
        self.retention.delete(view["mission_id"], "COMPLETED", self.retention.store_identity)
        capture("deleted_by_owner")
        return view["mission_id"], overdue["mission_id"], steps

    def test_ML1_the_real_catalog_reads_each_step_of_the_same_mission(self):
        mission_id, overdue_id, steps = self.lifecycle()
        read = [(step["step"], self.mission(step["response"], mission_id)) for step in steps]
        facts = [(name, m["lifecycle"]["state"], m["lifecycle"]["standing"], m["retention"]["eligible"],
                  m["retention"]["phase"]) for name, m in read]
        self.assertEqual(facts, [
            ("of_record", "AWAITING_DECISION", "inactive", False, "intact"),
            ("approved_never_started", "AUTHORIZED", "active", False, "intact"),
            ("start_requested", "AUTHORIZED", "active", False, "intact"),
            ("task_observed", "RUNNING", "active", False, "intact"),
            ("completed_ended_archived", "COMPLETED", "terminal", True, "intact"),
            ("reclaimed_at_startup", "COMPLETED", "terminal", True, "reclaimed"),
            ("deleted_by_owner", "COMPLETED", "terminal", True, "deleted"),
        ])
        # immediate archival is the VIEW (eligible at once); the payload stays until the 30-day deadline
        self.assertEqual(read[4][1]["retention"]["basis"], "verified_target_stopped")
        # the reclaimed Mission keeps its summary; the owner's delete removes it too
        self.assertIsNotNone(read[5][1]["retention"]["summary"])
        self.assertIsNone(read[6][1]["retention"]["summary"])
        # the STARTUP sweep reclaimed the long-overdue Mission that retention had never observed
        overdue = [self.mission(step["response"], overdue_id)["retention"]["phase"] for step in steps]
        self.assertEqual(overdue, ["intact"] * 5 + ["reclaimed"] * 2)

    def test_ML2_exports_the_answers_reproducibly(self):
        mission_id, overdue_id, steps = self.lifecycle()
        doc = json.dumps({
            "fixture": True,
            "generator": "tests/test_mission_lifecycle_answers.py ML2",
            "label": ("FIXTURE (generated, not live): the REAL catalog answer (query_v2, the Mission Core reader and"
                      " the retention reader, composed as production composes it) at each step of one Mission"
                      " Core Mission driven by the real stack"
                      " (panel service, bridge, Mission Core writers, retention coordinator, startup path) over this"
                      " test's own stores, with a controlled clock. The runtime snapshot is SYNTHETIC: empty at"
                      " every step except task_observed, where it sees the Mission's bound task with no live"
                      " agent. HARNESS, not observed: the bridge's observer and spawner are the panel fixture's"
                      " controlled stand-ins (no process runs) and the clock is controlled. MIXED SOURCES at"
                      " completed_ended_archived: Mission Core's record is COMPLETED with its run verification"
                      " recorded, while the producer's Mission State reports progress IN_PROGRESS and completion"
                      " not verified, so the canonical status is Ended, NOT Verified (the retention basis"
                      " verified_target_stopped is the capacity-release basis, a different fact). Stand-ins,"
                      " both for reproducibility only:"
                      " secrets.token_hex is a deterministic counter for this run, and the test's temporary"
                      " directory is written as " + PLACEHOLDER_ROOT + " (the store identity and the measured"
                      " registry size recomputed for it). NORMALIZED: a completed Mission's"
                      " mission_state.execution_identity identity_sha256 and cursor_sha256 are stand-ins of the"
                      " same shape (sha256 of '" + IDENTITY_STAND_IN + ":<identity|cursor>:<mission id>'); the"
                      " real ones digest this run's absolute paths, and the test asserts them equal to the"
                      " producer's own computation"),
            "source_id": SOURCE_ID,
            "mission_id": mission_id,
            "overdue_mission_id": overdue_id,
            "steps": [{"step": step["step"], "now": step["now"],
                       "response": json.loads(self.portable(step["response"], step["registry_document"],
                                                            step["missions"]))}
                      for step in steps],
        }, indent=2, sort_keys=True) + "\n"
        for root in self.roots():
            self.assertNotIn(root, doc)
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            EXPORT.write_text(doc, encoding="utf-8")
        self.assertEqual(EXPORT.read_text(encoding="utf-8"), doc)

    # -- reproducible text ------------------------------------------------------------------

    def roots(self):
        return sorted({self.tmp.name, os.path.realpath(self.tmp.name)}, key=len, reverse=True)

    def substitute(self, text):
        real = reclaim.store_identity("mission_core", self.state)
        placeholder_state = os.path.realpath(self.state)
        for root in self.roots():
            placeholder_state = placeholder_state.replace(root, PLACEHOLDER_ROOT)
        text = text.replace(real, reclaim.store_identity("mission_core", placeholder_state))
        for root in self.roots():
            text = text.replace(root, PLACEHOLDER_ROOT)
        return re.sub(r"(%s/[a-z]+)-[A-Za-z0-9_]{8}" % re.escape(PLACEHOLDER_ROOT), r"\1-0", text)

    def placeholder(self, value):
        """A JSON value with this test's temporary directory written as the placeholder (paths only)."""
        text = json.dumps(value)
        for root in self.roots():
            text = text.replace(root, PLACEHOLDER_ROOT)
        return json.loads(text)

    def identities(self, response, document, missions):
        """``{real digest: stand-in}`` for each COMPLETED Mission's execution identity and cursor. The real
        values are recomputed by the producer's own function and asserted equal to the answer's; the stand-in
        is labelled in the export (``IDENTITY_STAND_IN``)."""
        from target_runtime import mission_state as mission_state_module
        swaps = {}
        source = document["sources"][0]
        for mission in response["missions"]:
            block = (mission.get("mission_state") or {}).get("execution_identity") or {}
            if block.get("state") != "established":
                continue
            journal = mission["mission_state"]["report"]["cursor"]["journal_digest_sha256"]
            real = mission_state_module.listed_execution_identity(source, missions[mission["native_id"]], journal)
            self.assertEqual((real["identity_sha256"], real["cursor_sha256"]),
                             (block["identity_sha256"], block["cursor_sha256"]),
                             "the identity and cursor are the producer's own computation")
            for kind in ("identity", "cursor"):
                swaps[real[kind + "_sha256"]] = hashlib.sha256(
                    ("%s:%s:%s" % (IDENTITY_STAND_IN, kind, mission["native_id"])).encode()).hexdigest()
        return swaps

    def portable(self, response, document, missions):
        """The answer as the producer would emit it were the temporary directory the placeholder: the registry
        size is the harness's own measurement (``len(json.dumps(document))``), measured again over the
        substituted document; each completed Mission's execution identity and cursor are recomputed by the
        producer's own functions over the record at the placeholder path."""
        swaps = self.identities(response, document, missions)
        registry = response["registry"]
        self.assertEqual(registry["serialized_bytes"], len(json.dumps(document)),
                         "the registry size is the harness's measurement")
        response = dict(response, registry=dict(registry, serialized_bytes=len(self.substitute(json.dumps(document)))))
        text = json.dumps(response, sort_keys=True)
        for real, there in swaps.items():
            text = text.replace(real, there)
        return self.substitute(text)

if __name__ == "__main__":
    unittest.main()
