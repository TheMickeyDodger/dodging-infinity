"""The evidence library's ONE admitted content class (item 2 / original 10):
a Mission Core RESULT ARTIFACT recorded with a ``REPOSITORY_PATH`` locator
and a content digest, read on demand through ``target_runtime.artifact_content``
and the catalog owner's ``/artifact`` route.

Built on the accepted catalog fixtures (``test_catalog_v2.V2Fixture``: a REAL
private registry and Mission Core store, a synthetic herd root the fake
listing observes, so the Mission is really ``bound``). Every case discriminates
against its near neighbour:

1. ADMISSION: a recorded artifact whose bytes equal its recorded digest is
   returned verbatim, with its locator, digest, byte count and an honest read
   account; HTML is returned as text with a ``text/html`` label only (the
   renderer never renders it).
2. CORROBORATION: no fresh catalog answer, a listed cursor that is not the
   corroborated one, a Mission head that moved after listing, a Mission that
   is not ``bound`` and a version 1 owner all REFUSE, each by its own code,
   before any workspace byte is read.
3. THE REFERENCE: an unknown artifact id, a locator kind with no content, an
   artifact recorded unavailable or without a digest, and every unsafe
   locator spelling refuse by their own codes.
4. CONTAINMENT (the existing hardened reader, unchanged): a symlinked leaf, a
   symlinked directory component, a hardlink, a missing file, an oversized
   file, non-UTF-8 bytes, control characters and bytes that no longer match
   the recorded digest all refuse with NO content.
5. RACES: the Mission moving DURING the read, or the corroborating answer
   expiring during it, discards the content.
6. ACCOUNTING: every read is HELD before it is made; a refused file read keeps
   its whole hold; an unaffordable read is never attempted; one read at a
   time.
7. THE HTTP ROUTE: same bearer/Host/Origin boundary, exactly three query
   fields, each of a fixed grammar; nothing is written.
"""

import copy
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import service as mission_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from mission_sources import contract_v2 as v2  # noqa: E402
from mission_sources import query_v2  # noqa: E402
from mission_sources import registry as registry_module  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402
from target_runtime import artifact_content  # noqa: E402
from target_runtime import catalog_http  # noqa: E402
from target_runtime import prepare as prepare_module  # noqa: E402
from target_runtime import state_artifact  # noqa: E402
import test_catalog_v2 as v2_tests  # noqa: E402
from test_mission_sources import AUTHENTICATED  # noqa: E402

TEXT = b"Result: 12 passed, 0 failed\nsee reports/summary\n"
TEST_LOG_DIGEST_SOURCE = b"verification record of the fixture suite\n"
HTML_PAGE = (b"<html><body><h1>Report</h1><script>alert('x')</script>"
             b"<img src=\"https://example.invalid/p.png\" onerror=\"steal()\">"
             b"<a href=\"javascript:run()\">run</a></body></html>\n")
PRIVATE_LOG = b"session: the agent pasted a token here\n"
SECRET = b"outside the workspace: never readable\n"
NOT_LISTED = "0" * 64


def sha(data):
    return hashlib.sha256(data).hexdigest()


#: The fixture Mission's APPROVED proof contract DECLARES its deliverables,
#: each PINNED to exact bytes (Mission Core requires the expected digest):
#: ``result_report`` (TEXT) and ``report_page`` (HTML_PAGE), both PRODUCED.
#: Every other artifact is undeclared unless an ACCEPTED evidence record of a
#: verifying kind references it.
def declaring_contract():
    import test_local_request
    value = test_local_request.contract()
    value["required_artifacts"] = [
        {"key": "result_report", "role": "PRODUCED",
         "expected_content_digest_sha256": sha(TEXT)},
        {"key": "report_page", "role": "PRODUCED",
         "expected_content_digest_sha256": sha(HTML_PAGE)},
    ]
    value["requirements"][0]["required_artifact_keys"] = [
        "report_page", "result_report"]
    return value


class ArtifactFixture(v2_tests.V2Fixture):
    """A REAL running Mission, bound, with files in its own workspace and a
    file OUTSIDE it that no read may ever reach. Its approved proof contract
    DECLARES required artifacts (``declaring_contract``)."""

    def propose(self, state_dir, objective="Fixture mission"):
        import test_mission_sources as base
        surface = base.surface_module.build_bridgeless_surface(state_dir,
                                                               self.clock)
        out = surface.submit(base.mission_request(
            objective=objective, proof_contract=declaring_contract()))
        return surface, out["request_ref"], out["mission_id"]

    def other_principal(self):
        from mission import record as mission_record
        return mission_record.AuthenticatedContext(
            transport="local",
            principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:verifier")

    def evidence(self, artifact_ids, data=TEST_LOG_DIGEST_SOURCE,
                 kind="VERIFICATION_RECORD", accept=True, invalidate=False):
        """An evidence record referencing ``artifact_ids``: submitted, and
        ACCEPTED by another principal unless ``accept`` is False."""
        submitted = self.missions.submit_evidence(
            self.mission_id,
            self.missions.mint_state_operation_id(AUTHENTICATED),
            self.missions.get_state(self.mission_id)["sequence"],
            "tests_pass", kind, sha(data), list(artifact_ids), AUTHENTICATED)
        if accept:
            self.clock.now += 1
            verifier = self.other_principal()
            self.missions.accept_evidence(
                self.mission_id,
                self.missions.mint_state_operation_id(verifier),
                self.missions.get_state(self.mission_id)["sequence"],
                submitted["evidence_id"], sha(data), verifier)
        if invalidate:
            self.missions.invalidate_evidence(
                self.mission_id,
                self.missions.mint_state_operation_id(AUTHENTICATED),
                self.missions.get_state(self.mission_id)["sequence"],
                submitted["evidence_id"], "superseded", AUTHENTICATED)
        return submitted["evidence_id"]

    def setUp(self):
        super(ArtifactFixture, self).setUp()
        (self.state_dir, self.source_id, self.mission_id,
         self.workspace) = self.running()
        self.mission_ref = "%s:%s" % (self.source_id, self.mission_id)
        self.missions = mission_service.MissionService(
            mission_store.MissionStore(self.state_dir), self.clock)
        self.outside = os.path.join(self.root, "outside")
        os.makedirs(self.outside)
        self.secret = os.path.join(self.outside, "secret.txt")
        with open(self.secret, "wb") as handle:
            handle.write(SECRET)

    def put(self, relative, data):
        path = os.path.join(self.workspace, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def record(self, locator, data=TEXT, kind="REPOSITORY_PATH",
               role="PRODUCED", available=True, digest=True,
               key="result_report"):
        return self.missions.record_artifact(
            self.mission_id,
            self.missions.mint_state_operation_id(AUTHENTICATED),
            self.missions.get_state(self.mission_id)["sequence"], key, role,
            kind, locator, sha(data) if digest is True else digest,
            available, [], AUTHENTICATED)["artifact_id"]

    def move_the_head(self):
        self.missions.open_blocker(
            self.mission_id,
            self.missions.mint_state_operation_id(AUTHENTICATED),
            self.missions.get_state(self.mission_id)["sequence"],
            "synthetic.blocker", "SYNTHETIC", AUTHENTICATED)

    def listed(self, owner):
        """Refresh the owner ONCE; the journal digest its answer lists."""
        answer = owner.request(5)
        self.assertIsNotNone(answer["response"], answer["detail"])
        mission = [m for m in answer["response"]["missions"]
                   if m["mission_ref"] == self.mission_ref][0]
        self.assertEqual(mission["binding"]["state"], "bound")
        return mission["mission_state"]["report"]["provenance"][
            "journal_digest_sha256"]

    def read(self, owner, artifact_id, cursor):
        answer = owner.artifact_content(self.mission_ref, artifact_id, cursor)
        self.assertEqual(set(answer), set(artifact_content.ANSWER_KEYS))
        self.assertEqual((answer["schema"], answer["version"]),
                         (artifact_content.SCHEMA, artifact_content.VERSION))
        self.assertEqual(set(answer["accounting"]),
                         set(artifact_content.ACCOUNTING_KEYS))
        self.assertLessEqual(answer["accounting"]["charged_bytes"],
                             answer["accounting"]["max_bytes"])
        return answer

    def assert_refused(self, answer, problem):
        self.assertEqual((answer["state"], answer["problem"]),
                         (artifact_content.STATE_REFUSED, problem),
                         answer["detail"])
        self.assertIsNone(answer["content"])
        self.assertIsInstance(answer["detail"], str)
        self.assertLessEqual(len(answer["detail"]),
                             artifact_content.MAX_DETAIL_CHARS)
        self.assertNotIn(SECRET.decode().strip(), json.dumps(answer))

    def intercept_reads(self):
        """Both the item 2 read step and the hardened primitive under it,
        from the content request on (a catalog refresh re-hashes recorded
        candidates through the same primitive)."""
        calls = []
        real_primitive = state_artifact.read_state_artifact
        real_read = artifact_content._read_file

        def primitive(*args):
            calls.append(("primitive",) + args)
            return real_primitive(*args)

        def read(*args):
            calls.append(("read_file",) + args)
            return real_read(*args)
        for owner, name, value in ((state_artifact, "read_state_artifact",
                                    primitive),
                                   (artifact_content, "_read_file", read)):
            patcher = mock.patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def store_bytes(self):
        with open(os.path.join(self.state_dir, mission_store.MISSIONS_FILE_NAME),
                  "rb") as handle:
            return handle.read()


class AdmissionTests(ArtifactFixture):

    def test_a_recorded_artifact_whose_bytes_match_is_admitted_verbatim(self):
        self.put("reports/result.txt", TEXT)
        artifact_id = self.record("reports/result.txt")
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        before = self.store_bytes()
        answer = self.read(owner, artifact_id, cursor)
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])
        self.assertIsNone(answer["problem"])
        self.assertEqual(answer["content"], {
            "text": TEXT.decode("utf-8"), "byte_count": len(TEXT),
            "digest_sha256": sha(TEXT), "media": "text/plain"})
        self.assertEqual(answer["artifact"], {
            "key": "result_report", "role": "PRODUCED",
            "locator_kind": "REPOSITORY_PATH",
            "locator": "reports/result.txt",
            "content_digest_sha256": sha(TEXT),
            "recorded_at": self.clock(),
            "admission_basis": [artifact_content.BASIS_CONTRACT_REQUIRED],
            # The bound Mission's read stands on its active binding (the
            # original path, unchanged; completed-Mission reads differ).
            "execution_basis": artifact_content.EXECUTION_ACTIVE})
        self.assertEqual((answer["mission_ref"], answer["artifact_id"],
                          answer["cursor"]),
                         (self.mission_ref, artifact_id, cursor))
        self.assertEqual(answer["observed_at"], self.clock())
        account = answer["accounting"]
        self.assertEqual(account["max_bytes"],
                         artifact_content.MAX_REQUEST_READ_BYTES)
        # the registry and the store, each read twice (before the file and
        # again before release), and the file: each settled to its bytes.
        self.assertEqual(account["charged_bytes"],
                         2 * self.registry_bytes() + 2 * len(before)
                         + len(TEXT))
        self.assertEqual((account["reads_refused"], account["exhausted"]),
                         (0, False))
        self.assertEqual(self.store_bytes(), before, "nothing is written")

    def registry_bytes(self):
        return os.path.getsize(self.registry.path)

    def test_html_is_returned_as_text_and_only_labelled(self):
        page = HTML_PAGE
        self.put("reports/page.html", page)
        artifact_id = self.record("reports/page.html", page, key="report_page")
        owner = self.owner(version=2)
        answer = self.read(owner, artifact_id, self.listed(owner))
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED)
        self.assertEqual(answer["content"]["media"], "text/html")
        self.assertEqual(answer["content"]["text"], page.decode("utf-8"))

    def test_the_bound_is_the_hardened_readers_own_unchanged(self):
        self.assertEqual(artifact_content.MAX_CONTENT_BYTES,
                         prepare_module.MAX_INSTRUCTION_FILE_BYTES)
        self.assertEqual(
            artifact_content.MAX_REQUEST_READ_BYTES,
            (registry_module.MAX_REGISTRY_BYTES + 1)
            + 2 * (sources_module.MAX_STORE_READ_BYTES + 1)
            + (prepare_module.MAX_INSTRUCTION_FILE_BYTES + 1))
        exact = b"x" * artifact_content.MAX_CONTENT_BYTES
        self.put("reports/exact.txt", exact)
        artifact_id = self.record("reports/exact.txt", exact, key="full_log")
        self.evidence([artifact_id])
        owner = self.owner(version=2)
        answer = self.read(owner, artifact_id, self.listed(owner))
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED)
        self.assertEqual(answer["content"]["byte_count"], len(exact))


class CorroborationTests(ArtifactFixture):

    def setUp(self):
        super(CorroborationTests, self).setUp()
        self.put("reports/result.txt", TEXT)
        self.artifact_id = self.record("reports/result.txt")

    def spy_reads(self):
        """Every hardened read from now on (a catalog refresh re-hashes
        recorded candidates through the same reader)."""
        calls = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            calls.append(args)
            return real(*args)
        patcher = mock.patch.object(state_artifact, "read_state_artifact",
                                    spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_no_fresh_catalog_answer_refuses_before_any_read(self):
        calls = self.spy_reads()
        owner = self.owner(version=2)
        answer = self.read(owner, self.artifact_id, NOT_LISTED)
        self.assert_refused(answer, artifact_content.PROBLEM_NOT_CORROBORATED)
        self.assertEqual(answer["accounting"]["charged_bytes"], 0)
        cursor = self.listed(owner)
        del calls[:]                     # the refresh's own candidate re-hash
        self.clock.now += 3600           # the answer has expired
        self.assert_refused(self.read(owner, self.artifact_id, cursor),
                            artifact_content.PROBLEM_NOT_CORROBORATED)
        self.assertEqual(calls, [])

    def test_a_cursor_the_answer_does_not_list_refuses(self):
        owner = self.owner(version=2)
        self.listed(owner)
        self.assert_refused(self.read(owner, self.artifact_id, NOT_LISTED),
                            artifact_content.PROBLEM_CURSOR_NOT_LISTED)

    def test_a_head_that_moved_after_listing_never_resolves_anew(self):
        calls = self.spy_reads()
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        del calls[:]                     # the refresh's own candidate re-hash
        self.move_the_head()
        answer = self.read(owner, self.artifact_id, cursor)
        self.assert_refused(answer, artifact_content.PROBLEM_CURSOR_MOVED)
        self.assertEqual(calls, [], "no workspace byte is read")

    def test_a_mission_that_is_not_bound_is_refused_by_its_state(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        answer = owner.request(5)          # cached: still bound
        mission = [m for m in answer["response"]["missions"]
                   if m["mission_ref"] == self.mission_ref][0]
        for state in ("unbound", "disappeared", "ambiguous", "unverified",
                      "not_applicable"):
            with self.subTest(state):
                with mock.patch.dict(mission["binding"], {"state": state}):
                    refused = self.read(owner, self.artifact_id, cursor)
                self.assert_refused(refused,
                                    artifact_content.PROBLEM_NOT_BOUND)
                self.assertIn(state, refused["detail"])

    def test_an_unlisted_mission_is_refused(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        answer = owner.artifact_content(
            "%s:mn-%s" % (self.source_id, "f" * 32), self.artifact_id, cursor)
        self.assert_refused(answer, artifact_content.PROBLEM_MISSION_NOT_LISTED)

    def test_a_version_1_owner_has_no_content_route(self):
        owner = self.owner()
        answer = owner.artifact_content(self.mission_ref, self.artifact_id,
                                        NOT_LISTED)
        self.assert_refused(answer, artifact_content.PROBLEM_CONTRACT_VERSION)


class ReferenceTests(ArtifactFixture):

    def admitted_cursor(self, owner):
        return self.listed(owner)

    def test_an_unknown_artifact_id_is_not_recorded(self):
        owner = self.owner(version=2)
        answer = self.read(owner, "mf-" + "a" * 32, self.listed(owner))
        self.assert_refused(answer, artifact_content.PROBLEM_ARTIFACT_ABSENT)

    def test_locator_kinds_without_content_are_refused_by_kind(self):
        ids = dict((kind, self.record("opaque:%s" % kind, kind=kind,
                                      key="k_%s" % kind.lower()))
                   for kind in ("OPAQUE_REFERENCE", "CONTENT_ADDRESS"))
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        for kind, artifact_id in ids.items():
            with self.subTest(kind):
                answer = self.read(owner, artifact_id, cursor)
                self.assert_refused(answer,
                                    artifact_content.PROBLEM_LOCATOR_KIND)
                self.assertIn(kind, answer["detail"])

    def test_recorded_unavailable_or_without_a_digest_is_refused(self):
        self.put("reports/result.txt", TEXT)
        unavailable = self.record("reports/result.txt", available=False,
                                  key="k_unavailable")
        undigested = self.record("reports/result.txt", digest=None,
                                 key="k_undigested")
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        self.assert_refused(self.read(owner, unavailable, cursor),
                            artifact_content.PROBLEM_RECORDED_UNAVAILABLE)
        self.assert_refused(self.read(owner, undigested, cursor),
                            artifact_content.PROBLEM_NO_DIGEST)

    def test_every_unsafe_locator_spelling_is_refused_unread(self):
        spellings = ("../outside/secret.txt", "/etc/passwd",
                     self.secret, "reports/../../outside/secret.txt",
                     "reports/./result.txt", "reports//result.txt",
                     "reports\\result.txt", "", "reports/",
                     "/".join(["d"] * 17))
        ids = [(s, self.record(s, key="k%d" % i))
               for i, s in enumerate(spellings) if s != ""]
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        calls = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            calls.append(args)
            return real(*args)
        with mock.patch.object(state_artifact, "read_state_artifact", spy):
            for spelling, artifact_id in ids:
                with self.subTest(spelling):
                    self.assert_refused(
                        self.read(owner, artifact_id, cursor),
                        artifact_content.PROBLEM_LOCATOR_UNSAFE)
        self.assertEqual(calls, [])


class DisclosurePolicyTests(ArtifactFixture):
    """LINKAGE is not DISCLOSURE: a canonically recorded file whose digest
    matches is the recorded artifact, and still is never read unless it is
    in a supported safe evidence class. The refusal comes BEFORE the read:
    proved by intercepting the reader, not by the error text."""

    def recorded(self, locator, data):
        """A file in the workspace, recorded LEGITIMATELY with its exact
        digest, and DECLARED by an accepted evidence record: the linkage is
        genuine and the class is declared; only disclosure can refuse."""
        self.put(locator, data)
        artifact_id = self.record(locator, data, key="test_output")
        self.evidence([artifact_id])
        return artifact_id

    def refused_unread(self, cases, problem):
        ids = [(locator, self.recorded(locator, data))
               for locator, data in cases]
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        calls = self.intercept_reads()
        for locator, artifact_id in ids:
            with self.subTest(locator):
                answer = self.read(owner, artifact_id, cursor)
                self.assert_refused(answer, problem)
                self.assertNotIn("token-value", json.dumps(answer))
        self.assertEqual(calls, [], "no byte of a refused class is read")

    def test_herd_state_is_refused_before_any_read(self):
        with open(os.path.join(self.workspace, ".herd", "state",
                               "task.json"), "rb") as handle:
            task = handle.read()
        ids = self.record(".herd/state/task.json", task, key="test_output")
        self.put(".herd/state/reviews/notes.md", b"# private\n")
        notes = self.record(".herd/state/reviews/notes.md", b"# private\n",
                            key="test_output")
        self.evidence([ids, notes])       # declared: disclosure decides
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        calls = self.intercept_reads()
        for artifact_id in (ids, notes):
            answer = self.read(owner, artifact_id, cursor)
            self.assert_refused(answer,
                                artifact_content.PROBLEM_PRIVATE_LOCATION)
            self.assertIn(".herd", answer["detail"])
        self.assertEqual(calls, [])

    def test_hidden_and_private_locations_are_refused_unread(self):
        self.refused_unread((
            (".env", b"API=token-value\n"),
            (".git/config.txt", b"[remote] token-value\n"),
            ("config/.secrets/report.md", b"token-value\n"),
            ("reports/.hidden.md", b"token-value\n")),
            artifact_content.PROBLEM_PRIVATE_LOCATION)

    def test_credential_like_and_key_material_are_refused_unread(self):
        self.refused_unread((
            ("config/credentials.json", b"{\"key\": \"token-value\"}\n"),
            ("reports/secret-notes.md", b"token-value\n"),
            ("deploy/api_key.txt", b"token-value\n"),
            ("auth/password-reset.html", b"token-value\n"),
            ("keys/server.pem", b"token-value\n"),
            ("keys/id_rsa", b"token-value\n"),
            ("tokens/session.txt", b"token-value\n")),
            artifact_content.PROBLEM_CREDENTIAL_LIKE)

    def test_a_declared_artifact_in_no_text_format_is_refused_unread(self):
        self.refused_unread((
            ("bin/tool", b"token-value\n"),
            ("logs/agent-session.jsonl", b"{\"token-value\": 1}\n"),
            ("logs/terminal.log", b"token-value\n"),
            ("config/app.yaml", b"token-value: 1\n"),
            ("scripts/run.sh", b"echo token-value\n"),
            ("reports/summary.MD.bak", b"token-value\n")),
            artifact_content.PROBLEM_FORMAT_UNSUPPORTED)

    def test_every_supported_text_format_is_admitted(self):
        for extension in artifact_content.TEXT_FORMATS:
            with self.subTest(extension):
                self.assertTrue(artifact_content.disclosure_problem(
                    ("reports", "result" + extension)) is None)
                self.assertTrue(artifact_content.disclosure_problem(
                    ("reports", "RESULT" + extension.upper())) is None)
        locators = ("reports/result.md", "out/coverage.json",
                    "test-results/junit.xml", "reports/table.csv")
        ids = [(locator, self.recorded(locator, TEXT))
               for locator in locators]
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        for locator, artifact_id in ids:
            with self.subTest(locator):
                self.assertEqual(self.read(owner, artifact_id, cursor)[
                    "state"], artifact_content.STATE_ADMITTED)

    def test_the_policy_never_widens_containment(self):
        """Traversal stays its OWN refusal, distinct from disclosure."""
        artifact_id = self.record("../outside/secret.txt", SECRET)
        owner = self.owner(version=2)
        self.assert_refused(self.read(owner, artifact_id, self.listed(owner)),
                            artifact_content.PROBLEM_LOCATOR_UNSAFE)


class DeclaredClassTests(ArtifactFixture):
    """The ADMISSION BASIS is what the canonical record DECLARES, never the
    path shape: a required artifact of the approved proof contract (same key
    and role, and its pinned digest when the contract pins one), or an
    artifact an ACCEPTED, non-invalidated evidence record of a verifying kind
    references. Anything else is unclassifiable and never read."""

    def undeclared(self, locator, data):
        self.put(locator, data)
        return self.record(locator, data, key="session_notes")

    def test_an_ordinary_named_private_log_is_refused_before_the_read(self):
        """The case an extension rule would wave through: genuine linkage,
        correct digest, an innocuous name and a text extension."""
        cases = (("reports/private-log.txt", b"agent said: token-value\n"),
                 ("logs/session.json", b"{\"turn\": \"token-value\"}\n"),
                 ("notes/summary.md", b"# token-value\n"))
        ids = [(locator, self.undeclared(locator, data))
               for locator, data in cases]
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        calls = self.intercept_reads()
        for locator, artifact_id in ids:
            with self.subTest(locator):
                answer = self.read(owner, artifact_id, cursor)
                self.assert_refused(answer,
                                    artifact_content.PROBLEM_CLASS_NOT_DECLARED)
                self.assertNotIn("token-value", json.dumps(answer))
        self.assertEqual(calls, [], "no byte of an undeclared artifact is read")

    def admitted_basis(self, artifact_id):
        owner = self.owner(version=2)
        answer = self.read(owner, artifact_id, self.listed(owner))
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])
        return answer["artifact"]["admission_basis"]

    def test_a_contract_required_artifact_is_admitted_by_that_basis(self):
        self.put("reports/result.txt", TEXT)
        self.assertEqual(self.admitted_basis(self.record("reports/result.txt")),
                         [artifact_content.BASIS_CONTRACT_REQUIRED])

    def test_an_accepted_evidence_artifact_is_admitted_by_that_basis(self):
        artifact_id = self.undeclared("test-results/junit.xml", TEST_LOG)
        self.evidence([artifact_id])
        self.assertEqual(self.admitted_basis(artifact_id),
                         [artifact_content.BASIS_ACCEPTED_EVIDENCE])

    def test_both_bases_are_reported_when_both_hold(self):
        self.put("reports/result.txt", TEXT)
        artifact_id = self.record("reports/result.txt")
        self.evidence([artifact_id])
        self.assertEqual(self.admitted_basis(artifact_id),
                         [artifact_content.BASIS_ACCEPTED_EVIDENCE,
                          artifact_content.BASIS_CONTRACT_REQUIRED])

    def test_evidence_that_is_not_accepted_or_verifying_declares_nothing(self):
        submitted = self.undeclared("reports/a.txt", TEXT)
        self.evidence([submitted], accept=False)
        invalidated = self.undeclared("reports/b.txt", TEXT)
        self.evidence([invalidated], invalidate=True)
        narrative = self.undeclared("reports/c.txt", TEXT)
        self.evidence([narrative], kind="NARRATIVE_CLAIM", accept=False)
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        calls = self.intercept_reads()
        for artifact_id in (submitted, invalidated, narrative):
            self.assert_refused(self.read(owner, artifact_id, cursor),
                                artifact_content.PROBLEM_CLASS_NOT_DECLARED)
        self.assertEqual(calls, [])

    def test_a_pinned_contract_digest_must_be_the_recorded_one(self):
        self.put("reports/pinned.txt", TEXT + b"other bytes\n")
        other = self.record("reports/pinned.txt", TEXT + b"other bytes\n")
        self.put("reports/exact.txt", TEXT)
        exact = self.record("reports/exact.txt", TEXT)
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        self.assert_refused(self.read(owner, other, cursor),
                            artifact_content.PROBLEM_CLASS_NOT_DECLARED)
        self.assertEqual(self.read(owner, exact, cursor)["state"],
                         artifact_content.STATE_ADMITTED)

    def test_a_non_verifying_kind_declares_nothing_even_if_accepted(self):
        """Mission Core never stores an accepted NARRATIVE_CLAIM or
        PROCESS_EXIT (a valid store cannot reach this), so the kind rule is
        defense in depth: pinned here on the pure function."""
        artifact = {"artifact_id": "mf-" + "1" * 32, "key": None,
                    "role": "PRODUCED", "content_digest_sha256": "a" * 64}
        for kind, expected in (("NARRATIVE_CLAIM", []), ("PROCESS_EXIT", []),
                               ("VERIFICATION_RECORD",
                                [artifact_content.BASIS_ACCEPTED_EVIDENCE])):
            state = {"contract_activations": [], "evidence": [{
                "kind": kind, "artifact_ids": [artifact["artifact_id"]],
                "acceptance": {"accepted_at": 1}, "invalidation": None}]}
            with self.subTest(kind):
                self.assertEqual(artifact_content.declared_basis(
                    None, None, state, artifact), expected)

    def test_the_class_refusal_is_distinct_from_traversal_and_disclosure(self):
        codes = {artifact_content.PROBLEM_LOCATOR_UNSAFE,
                 artifact_content.PROBLEM_CLASS_NOT_DECLARED,
                 artifact_content.PROBLEM_PRIVATE_LOCATION,
                 artifact_content.PROBLEM_CREDENTIAL_LIKE,
                 artifact_content.PROBLEM_FORMAT_UNSUPPORTED}
        self.assertEqual(len(codes), 5)
        self.assertLessEqual(codes, set(artifact_content.PROBLEMS))


class ContainmentTests(ArtifactFixture):

    def refused(self, locator, problem, data=TEXT):
        artifact_id = self.record(locator, data, key="test_output")
        self.evidence([artifact_id])     # DECLARED: the reader decides
        owner = self.owner(version=2)
        answer = self.read(owner, artifact_id, self.listed(owner))
        self.assert_refused(answer, problem)
        return answer

    def test_a_symlinked_leaf_is_never_followed(self):
        os.makedirs(os.path.join(self.workspace, "reports"))
        os.symlink(self.secret,
                   os.path.join(self.workspace, "reports", "link.txt"))
        self.refused("reports/link.txt", artifact_content.PROBLEM_NOT_REGULAR,
                     SECRET)

    def test_a_symlinked_directory_component_never_carries_a_read_out(self):
        # A name in a supported class, so the HARDENED READER is what
        # refuses (the disclosure policy would refuse "secret" first).
        with open(os.path.join(self.outside, "report.txt"), "wb") as handle:
            handle.write(SECRET)
        os.symlink(self.outside, os.path.join(self.workspace, "linked"))
        self.refused("linked/report.txt", artifact_content.PROBLEM_ESCAPES,
                     SECRET)

    def test_a_hardlink_is_refused(self):
        os.makedirs(os.path.join(self.workspace, "reports"))
        os.link(self.secret, os.path.join(self.workspace, "reports",
                                          "hard.txt"))
        self.refused("reports/hard.txt", artifact_content.PROBLEM_HARDLINK,
                     SECRET)

    def test_a_missing_file_is_missing_never_empty(self):
        answer = self.refused("reports/never-written.txt",
                              artifact_content.PROBLEM_FILE_MISSING)
        # A refused file read KEEPS its whole hold (the composer's rule).
        held = artifact_content.MAX_CONTENT_BYTES + 1
        self.assertEqual(answer["accounting"]["charged_bytes"],
                         os.path.getsize(self.registry.path)
                         + len(self.store_bytes()) + held)

    def test_an_oversized_file_is_refused_unread_past_its_bound(self):
        big = b"y" * (artifact_content.MAX_CONTENT_BYTES + 1)
        self.put("reports/big.txt", big)
        self.refused("reports/big.txt", artifact_content.PROBLEM_OVERSIZED,
                     big)

    def test_non_utf8_and_control_characters_are_not_text(self):
        for name, data in (("latin1.txt", b"caf\xe9\n"),
                           ("nul.txt", b"abc\x00def\n"),
                           ("escape.txt", b"\x1b[31mred\x1b[0m\n"),
                           ("bell.txt", b"ding\x07\n")):
            with self.subTest(name):
                self.put("reports/" + name, data)
                self.refused("reports/" + name, artifact_content.PROBLEM_BINARY,
                             data)

    def test_changed_bytes_never_present_as_the_recorded_artifact(self):
        self.put("reports/result.txt", TEXT)
        artifact_id = self.record("reports/result.txt")    # contract-pinned
        self.put("reports/result.txt", TEXT + b"edited later\n")
        owner = self.owner(version=2)
        self.assert_refused(self.read(owner, artifact_id, self.listed(owner)),
                            artifact_content.PROBLEM_DIGEST_MISMATCH)


class RaceTests(ArtifactFixture):

    def setUp(self):
        super(RaceTests, self).setUp()
        self.put("reports/result.txt", TEXT)
        self.artifact_id = self.record("reports/result.txt")

    def during_the_read(self, action):
        real = state_artifact.read_state_artifact

        def racing(*args):
            result = real(*args)
            action()
            return result
        patcher = mock.patch.object(state_artifact, "read_state_artifact",
                                    racing)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_mission_moving_during_the_read_discards_the_content(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        self.during_the_read(self.move_the_head)
        self.assert_refused(self.read(owner, self.artifact_id, cursor),
                            artifact_content.PROBLEM_MOVED_DURING_READ)

    def test_the_answer_expiring_during_the_read_discards_the_content(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)

        def expire():
            self.clock.now += 3600
        self.during_the_read(expire)
        self.assert_refused(self.read(owner, self.artifact_id, cursor),
                            artifact_content.PROBLEM_NOT_CORROBORATED)

    def test_one_read_at_a_time(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        entered, release = threading.Event(), threading.Event()
        real = state_artifact.read_state_artifact

        def slow(*args):
            entered.set()
            release.wait(30)
            return real(*args)
        results = []
        with mock.patch.object(state_artifact, "read_state_artifact", slow):
            first = threading.Thread(target=lambda: results.append(
                owner.artifact_content(self.mission_ref, self.artifact_id,
                                       cursor)))
            first.start()
            self.assertTrue(entered.wait(30))
            self.assert_refused(self.read(owner, self.artifact_id, cursor),
                                artifact_content.PROBLEM_BUSY)
            release.set()
            first.join(30)
        self.assertEqual(results[0]["state"], artifact_content.STATE_ADMITTED)


class AccountingTests(ArtifactFixture):

    def test_an_unaffordable_read_is_never_attempted(self):
        self.put("reports/result.txt", TEXT)
        artifact_id = self.record("reports/result.txt")
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        calls = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            calls.append(args)
            return real(*args)
        # Room for the registry and load 1 as they SETTLE (a settled read
        # returns its unused room), one byte short of the file's fixed hold.
        room = (os.path.getsize(self.registry.path) + len(self.store_bytes())
                + artifact_content.MAX_CONTENT_BYTES)
        with mock.patch.object(artifact_content, "MAX_REQUEST_READ_BYTES", room), \
                mock.patch.object(state_artifact, "read_state_artifact", spy):
            answer = self.read(owner, artifact_id, cursor)
        self.assert_refused(answer, artifact_content.PROBLEM_BUDGET)
        self.assertEqual(calls, [])
        self.assertTrue(answer["accounting"]["exhausted"])
        self.assertEqual(answer["accounting"]["reads_refused"], 1)

    def test_the_revalidation_is_reserved_before_any_content_byte(self):
        """Round 17: the re-reads before release (the registry, fixed, and
        the store) are covered BEFORE the file is read, at their bounds,
        within the UNCHANGED cap; a request that cannot cover them is
        refused through the existing budget refusal, no content byte
        read."""
        self.put("reports/result.txt", TEXT)
        artifact_id = self.record("reports/result.txt")
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        registry_bytes = os.path.getsize(self.registry.path)
        store_bytes = len(self.store_bytes())
        exact = (registry_bytes + store_bytes
                 + (artifact_content.MAX_CONTENT_BYTES + 1)
                 + (registry_module.MAX_REGISTRY_BYTES + 1)
                 + (sources_module.MAX_STORE_READ_BYTES + 1))
        with mock.patch.object(artifact_content, "MAX_REQUEST_READ_BYTES",
                               exact):
            answer = self.read(owner, artifact_id, cursor)
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])
        self.assertEqual(answer["accounting"]["charged_bytes"],
                         2 * registry_bytes + 2 * store_bytes + len(TEXT))
        calls = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            calls.append(args)
            return real(*args)
        with mock.patch.object(artifact_content, "MAX_REQUEST_READ_BYTES",
                               exact - 1), \
                mock.patch.object(state_artifact, "read_state_artifact", spy):
            answer = self.read(owner, artifact_id, cursor)
        self.assert_refused(answer, artifact_content.PROBLEM_BUDGET)
        self.assertEqual(calls, [], "no content byte is read")
        self.assertEqual((answer["accounting"]["reads_refused"],
                          answer["accounting"]["exhausted"]), (1, True))

    def test_the_budget_is_the_reviewed_refresh_ledger(self):
        self.assertTrue(issubclass(artifact_content.RequestReadBudget,
                                   query_v2.RefreshReadBudget))


class HttpRouteTests(ArtifactFixture):

    def setUp(self):
        super(HttpRouteTests, self).setUp()
        self.put("reports/result.txt", TEXT)
        self.artifact_id = self.record("reports/result.txt")

    def path(self, mission_ref=None, artifact_id=None, cursor=None):
        return "/artifact?mission_ref=%s&artifact_id=%s&cursor=%s" % (
            mission_ref or self.mission_ref, artifact_id or self.artifact_id,
            cursor)

    def test_the_route_answers_with_the_owners_answer(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        server = self.serve(owner)
        status, headers, body = self.request(server, self.path(cursor=cursor))
        self.assertEqual(status, 200)
        self.assert_no_token(body, repr(headers))
        self.assertEqual(self.header(headers, "Content-Type"),
                         catalog_http.CONTENT_TYPE)
        self.assertEqual(self.header(headers, "Cache-Control"), "no-store")
        answer = json.loads(body)
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED)
        self.assertEqual(answer["content"]["text"], TEXT.decode())

    def test_the_access_boundary_is_the_catalogs(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        server = self.serve(owner)
        status = self.request(server, self.path(cursor=cursor), auth=False)[0]
        self.assertEqual(status, 401)
        status = self.request(server, self.path(cursor=cursor),
                              headers=(("Origin", "http://evil.invalid"),))[0]
        self.assertEqual(status, 403)
        status = self.request(server, self.path(cursor=cursor),
                              method="POST",
                              headers=(("Content-Length", "0"),))[0]
        self.assertEqual(status, 405)

    def test_exactly_three_fields_of_fixed_grammar(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        server = self.serve(owner)
        good = self.path(cursor=cursor)
        for target in (
                "/artifact",
                "/artifact?mission_ref=%s&artifact_id=%s" % (
                    self.mission_ref, self.artifact_id),
                good + "&cursor=" + cursor,
                good + "&path=/etc/passwd",
                good + "&wait_seconds=1",
                self.path(cursor=cursor.upper()),
                self.path(cursor=cursor[:-1]),
                self.path(artifact_id="mf-" + "g" * 32, cursor=cursor),
                self.path(artifact_id="../reports/result.txt",
                          cursor=cursor),
                self.path(mission_ref="mc1:/etc/passwd", cursor=cursor),
                self.path(mission_ref="x" * 200 + ":mn-" + "a" * 32,
                          cursor=cursor),
                "/artifact?locator=reports/result.txt"):
            with self.subTest(target):
                status, _headers, body = self.request(server, target)
                self.assertEqual(status, 400)
                self.assertNotIn(TEXT.decode().strip(), body.decode())

    def test_the_route_is_get_only_and_named(self):
        self.assertEqual(catalog_http.ROUTES,
                         ("/catalog", "/diagnostics", "/healthz",
                          "/artifact"))

    def test_the_adapters_grammar_is_the_modules(self):
        self.assertEqual(
            [(name, grammar.pattern)
             for name, grammar in catalog_http._ARTIFACT_FIELDS],
            [("mission_ref", artifact_content.MISSION_REF_PATTERN),
             ("artifact_id", artifact_content.ARTIFACT_ID_PATTERN),
             ("cursor", artifact_content.CURSOR_PATTERN)])


class VocabularyTests(unittest.TestCase):

    def test_the_observation_class_is_the_task_native_producers_own(self):
        """``artifact_content`` may not import the task-native reader; its
        re-declared observation facts are pinned EQUAL to the producer's."""
        from target_runtime import herdr_task_source
        from herdr import vintage
        self.assertEqual(
            (artifact_content.OBSERVATION_KIND,
             artifact_content.OBSERVATION_LIFECYCLE_MODEL,
             artifact_content.OBSERVATION_SCHEMA,
             artifact_content.OBSERVATION_SCHEMA_VERSION,
             artifact_content.OBSERVATION_IDENTITY_SOURCE),
            (herdr_task_source.KIND, herdr_task_source.LIFECYCLE_MODEL,
             herdr_task_source.SCHEMA, herdr_task_source.SCHEMA_VERSION,
             herdr_task_source.IDENTITY_SOURCE))
        self.assertTrue(set(artifact_content.OBSERVATION_COMPLETED_STATUSES)
                        < set(vintage.TERMINAL_TASK_STATUSES))
        for status in artifact_content.OBSERVATION_COMPLETED_STATUSES:
            self.assertEqual(herdr_task_source.standing(status), "terminal")

    def test_the_panel_has_words_for_every_execution_identity_code(self):
        with open(os.path.join(REPO_ROOT, "integrations", "pixel-agents",
                               "webview-ui", "src", "components",
                               "diEvidenceFormat.ts"),
                  encoding="utf-8") as handle:
            text = handle.read()
        block = text.split("// BEGIN EXECUTION_IDENTITY_CODES")[1].split(
            "// END EXECUTION_IDENTITY_CODES")[0]
        found = set(re.findall(r"^  (execution_[a-z_]+):", block, re.M))
        self.assertEqual(found, set(v2.EXECUTION_IDENTITY_PROBLEMS) | {
            v2.PROBLEM_EXECUTION_NOT_COMPLETED})

    def test_the_office_bridge_knows_exactly_this_vocabulary(self):
        """The office re-checks every answer against ITS copy of the closed
        problem list (``server/src/di/evidenceContent.ts``): the same list,
        in the same order, and the same two admission bases."""
        path = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "src"
                / "di" / "evidenceContent.ts")
        text = path.read_text(encoding="utf-8")
        block = text.split("// BEGIN ARTIFACT_CONTENT_PROBLEMS")[1].split(
            "// END ARTIFACT_CONTENT_PROBLEMS")[0]
        self.assertEqual(tuple(re.findall(r"'([a-z0-9_]+)'", block)),
                         artifact_content.PROBLEMS)
        bases = re.search(r"ADMISSION_BASES = \[([^\]]*)\]", text).group(1)
        self.assertEqual(sorted(re.findall(r"'([a-z_]+)'", bases)),
                         sorted([artifact_content.BASIS_CONTRACT_REQUIRED,
                                 artifact_content.BASIS_ACCEPTED_EVIDENCE]))

    def test_every_reader_status_but_read_is_mapped_explicitly(self):
        self.assertEqual(
            set(artifact_content.READ_REFUSALS),
            set(prepare_module.INSTRUCTION_STATUSES)
            - {prepare_module.INSTRUCTION_READ})
        self.assertLessEqual(set(artifact_content.READ_REFUSALS.values()),
                             set(artifact_content.PROBLEMS))

    def test_the_problem_vocabulary_is_closed(self):
        self.assertEqual(len(set(artifact_content.PROBLEMS)),
                         len(artifact_content.PROBLEMS))
        with self.assertRaises(ValueError):
            artifact_content.refusal("anything_else", "x", None, None, None)


#: The office consumer's fixture (``integrations/pixel-agents``): REAL answers
#: of this module's owner, exported NORMALIZED with the D1 conventions.
EXPORT_FILE = "artifact-content-v1.FIXTURE.json"
CANARY = "TRANSCRIPT-CANARY-NEVER-EXPORTED"
#: The SYNTHETIC routed candidate of the exported review round (a fixture
#: digest; it names no real candidate).
EXPORT_CANDIDATE = hashlib.sha256(b"artifact-content-v1 synthetic candidate"
                                  ).hexdigest()
EXPORT_FINDINGS = (
    ("blocker", "target_runtime/artifact_content.py:381",
     "a recorded locator is admitted on containment alone; decide the"
     " declared class before the read"),
    ("non_blocking", "-",
     "findings render as text: <b>bold</b> <script>alert(1)</script>"),
)
TEST_LOG = b"<testsuite name=\"unit\" tests=\"42\" failures=\"0\"/>\n"
#: A REAL machine temp root, never the record's literal placeholder text
#: (``/var/folders/<a>/<b>/T/<dir>``): the D1 round-13 form excludes ``<``.
REAL_TEMP_ROOT = re.compile(
    r"(?:/private)?/var/folders/[^/<\"\s]+/[^/<\"\s]+/T/|/private/tmp/")


class ExportFixture(ArtifactFixture):
    """One REAL bound Mission with every artifact class the office shows,
    an accepted verification record and a canonical review round whose
    transcript (a canary) must never leave the backend."""

    def review_round(self):
        """ONE review round through the REAL local writer (this repository's
        ``herdctl.py``, loaded in isolation, never the installed producer):
        ROUTED, then DECIDED over a reviewer capture holding private prose,
        a transcript canary, a terse tail and ONE declared findings envelope.
        Only the envelope may become findings; nothing else may leave."""
        import contextlib
        import importlib.util
        import io
        import types
        from target_runtime import review_findings as rf
        spec = importlib.util.spec_from_file_location(
            "herdctl_export_writer", REPO_ROOT / "herdctl.py")
        herdctl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(herdctl)
        herdctl.resolve_repo_ref = lambda ref: Path(self.workspace)
        herdctl.resolve_role = lambda repo, name: "pod-reviewer1"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            herdctl.review_findings_route_cmd(types.SimpleNamespace(
                repo=self.workspace, reviewer="reviewer1",
                candidate_sha256=EXPORT_CANDIDATE))
        nonce = json.loads(out.getvalue())["nonce"]
        envelope = [rf.BEGIN_LINE, "nonce %s" % nonce,
                    "task %s" % v2_tests.TASK, "round 1",
                    "reviewer reviewer1", "candidate %s" % EXPORT_CANDIDATE,
                    "count 2"] + [
            "[F%d] %s | %s | %s [/F%d]" % (i, sev, area, text, i)
            for i, (sev, area, text) in enumerate(EXPORT_FINDINGS, 1)] + [
            "%s nonce=%s count=2" % (rf.END_PREFIX, nonce)]
        capture = "\n".join(
            ["\u2022 I read the diff and the tests. " + CANARY,
             "  artifact_content.py | private tail | not a finding | -"]
            + ["  " + line for line in envelope]
            + ["  HERD_DECISION: APPROVE"])
        herdctl.reviewer_transcript = lambda agent, lines=500: capture
        with contextlib.redirect_stdout(io.StringIO()):
            herdctl.review_decision_cmd(types.SimpleNamespace(
                repo=self.workspace, reviewer="reviewer1", lines=500))

    def build_export(self):
        """``(raw, temp_root)``: the REAL answers, before normalization."""
        self.review_round()
        self.put("reports/result.txt", TEXT)
        self.put("reports/page.html", HTML_PAGE)
        self.put("test-results/junit.xml", TEST_LOG)
        self.put("reports/changed.txt", TEXT)
        self.put("reports/private-log.txt", PRIVATE_LOG)
        os.symlink(self.secret,
                   os.path.join(self.workspace, "reports", "link.txt"))
        ids = {
            "admitted_text": self.record("reports/result.txt"),
            "admitted_html": self.record("reports/page.html", HTML_PAGE,
                                         key="report_page"),
            "admitted_verification_log": self.record(
                "test-results/junit.xml", TEST_LOG, role="VERIFICATION",
                key="test_log"),
            "refused_locator_kind": self.record(
                "opaque:specification", kind="OPAQUE_REFERENCE",
                role="ORIGINAL_INPUT", key="specification"),
            "refused_digest_mismatch": self.record("reports/changed.txt"),
            "refused_locator_unsafe": self.record(
                "../outside/secret.txt", SECRET, key="escaping_report"),
            "refused_not_regular": self.record("reports/link.txt", TEXT),
            "refused_class_not_declared": self.record(
                "reports/private-log.txt", PRIVATE_LOG, key="session_notes"),
        }
        with open(os.path.join(self.workspace, ".herd", "state",
                               "task.json"), "rb") as handle:
            task = handle.read()
        ids["refused_private_location"] = self.record(
            ".herd/state/task.json", task, key="test_output")
        self.put("reports/changed.txt", TEXT + b"edited after recording\n")
        # ONE accepted verification record DECLARES the test result and
        # (as a recorded reference may) a private Herdr file: the class is
        # declared for both, and disclosure still refuses the second.
        self.evidence([ids["admitted_verification_log"],
                       ids["refused_private_location"]], data=TEST_LOG)
        owner = self.owner(version=2)
        answer = owner.request(5)
        cursor = self.listed(owner)
        content = dict((case, owner.artifact_content(
            self.mission_ref, artifact_id, cursor))
            for case, artifact_id in sorted(ids.items()))
        import test_herdr_task_source as hts_tests
        temp_root = os.path.realpath(self.root)
        # The LISTED head and, after the move, the moved one: both are
        # recomputed by the product over the mapped records.
        self.digests = hts_tests.journal_digests(self.state_dir, temp_root)
        self.move_the_head()
        self.digests.update(hts_tests.journal_digests(self.state_dir,
                                                      temp_root))
        content["refused_cursor_moved"] = owner.artifact_content(
            self.mission_ref, ids["admitted_text"], cursor)
        raw = {"catalog_answer": answer, "content_answers": content}
        return raw, temp_root

    def normalized(self, raw, temp_root):
        """The D1 conventions, in ONE pass: the payload is normalized by the
        accepted exporter (``test_herdr_task_source.normalize_export``,
        journal digests recomputed by the product); THEN the record of what
        was done is attached, counting the payload's real roots only."""
        import test_herdr_task_source as hts_tests
        text = json.dumps(raw)
        occurrences = text.count(temp_root)
        normalized, report = hts_tests.normalize_export(
            raw, temp_root, digests=self.digests)
        return dict(normalized, fixture="artifact-content-v1", normalization={
            "transformation": (
                "machine-specific temporary root (/var/folders/<a>/<b>/T/"
                "<dir>, as its /private realpath) replaced by %s; Mission"
                " Core journal digests recomputed by the product over the"
                " same records with the root mapped; no other byte of the"
                " emission changed. Byte counts (accounting.charged_bytes)"
                " are as emitted over the real paths. Fresh exports are not"
                " byte-reproducible (random Mission Core ids)."
                % hts_tests.SYNTHETIC_FIXTURE_ROOT),
            "synthetic_root": hts_tests.SYNTHETIC_FIXTURE_ROOT,
            "temporary_roots": 1,
            "occurrences": occurrences,
            "journal_digests_recomputed": report["journal_digests"]})


class NormalizedExportIsCoherent(ExportFixture):

    def test_the_export_is_portable_coherent_and_leaks_nothing(self):
        import test_herdr_task_source as hts_tests
        from mission_sources import contract_v2
        raw, temp_root = self.build_export()
        fixture = self.normalized(raw, temp_root)
        again = self.normalized(json.loads(json.dumps(raw)), temp_root)
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(fixture, sort_keys=True))
        text = json.dumps(fixture)
        payload = json.dumps(dict((k, fixture[k]) for k in (
            "catalog_answer", "content_answers")))
        for leak in (temp_root, CANARY, SECRET.decode().strip(),
                     PRIVATE_LOG.decode().strip()):
            self.assertNotIn(leak, text)
        self.assertIsNone(REAL_TEMP_ROOT.search(text))
        self.assertNotIn("/var/folders/", payload)
        # The record keeps the literal pattern text: a placeholder, not a path.
        self.assertIn("/var/folders/<a>/<b>/T/<dir>",
                      fixture["normalization"]["transformation"])
        self.assertIn(hts_tests.SYNTHETIC_FIXTURE_ROOT, payload)
        self.assertEqual(fixture["normalization"]["occurrences"],
                         payload.count(hts_tests.SYNTHETIC_FIXTURE_ROOT))
        self.assertNotIn(hts_tests.SYNTHETIC_FIXTURE_ROOT + "-2", text)
        contract_v2.validate_response_v2(fixture["catalog_answer"]["response"])
        [mission] = fixture["catalog_answer"]["response"]["missions"]
        listed = mission["mission_state"]["report"]["provenance"][
            "journal_digest_sha256"]
        answers = fixture["content_answers"]
        for case, answer in answers.items():
            with self.subTest(case):
                self.assertEqual(answer["cursor"], listed)
                self.assertEqual(answer["mission_ref"], mission["mission_ref"])
                self.assertEqual(answer["state"], case.split("_")[0])
        self.assertEqual(
            dict((case, a["problem"]) for case, a in answers.items()
                 if a["state"] == "refused"),
            {"refused_locator_kind": artifact_content.PROBLEM_LOCATOR_KIND,
             "refused_digest_mismatch":
                 artifact_content.PROBLEM_DIGEST_MISMATCH,
             "refused_locator_unsafe": artifact_content.PROBLEM_LOCATOR_UNSAFE,
             "refused_not_regular": artifact_content.PROBLEM_NOT_REGULAR,
             "refused_private_location":
                 artifact_content.PROBLEM_PRIVATE_LOCATION,
             "refused_class_not_declared":
                 artifact_content.PROBLEM_CLASS_NOT_DECLARED,
             "refused_cursor_moved": artifact_content.PROBLEM_CURSOR_MOVED})
        self.assertEqual(answers["admitted_html"]["content"]["media"],
                         artifact_content.MEDIA_HTML)
        self.assertEqual(
            answers["admitted_text"]["artifact"]["admission_basis"],
            [artifact_content.BASIS_CONTRACT_REQUIRED])
        self.assertEqual(
            answers["admitted_verification_log"]["artifact"]["admission_basis"],
            [artifact_content.BASIS_ACCEPTED_EVIDENCE])
        review = mission["admitted_artifacts"]["canonical_review"]
        self.assertEqual((review["state"], review["verdict"]),
                         ("admitted", "APPROVE"), review)
        findings = review["findings"]
        self.assertEqual((findings["state"], findings["count"],
                          findings["candidate_sha256"]),
                         ("present", 2, EXPORT_CANDIDATE))
        self.assertEqual([(f["severity"], f["summary"])
                          for f in findings["items"]],
                         [(sev, text) for sev, _, text in EXPORT_FINDINGS])
        self.assertNotIn("private tail", text)
        [evidence] = mission["mission_state"]["report"]["evidence"]["value"]
        self.assertTrue(evidence["accepted"])


@unittest.skipUnless(os.environ.get("DI_EXPORT_ARTIFACT_CONTENT"),
                     "exports the office consumer fixture only when asked")
class ExportForConsumerTests(ExportFixture):

    def test_export(self):
        raw, temp_root = self.build_export()
        fixture = self.normalized(raw, temp_root)
        path = os.path.join(os.environ["DI_EXPORT_ARTIFACT_CONTENT"],
                            EXPORT_FILE)
        with open(path, "w") as handle:
            json.dump(fixture, handle, indent=2, sort_keys=True)
            handle.write("\n")


class ConnectedReviewReaderTests(ExportFixture):
    """Round 15 F3 at the CONNECTED reader: the real local writer records the
    round, the task's findings reference is then damaged to ``[]`` / ``{}``
    in its ``state``, and the REAL catalog owner answers (runtime snapshot
    capture, chain observation, contract v2 query). The review must stay
    ADMITTED with its preamble and verdict; only findings are REFUSED."""

    def answer_with_state(self, odd):
        self.review_round()
        path = os.path.join(self.workspace, ".herd", "state", "task.json")
        with open(path) as handle:
            task = json.load(handle)
        task["last_review_findings"]["state"] = odd
        with open(path, "w") as handle:
            json.dump(task, handle)
        owner = self.owner(version=2)
        answer = owner.request(5)
        self.assertIsNotNone(answer["response"], answer["detail"])
        missions = [m for m in answer["response"]["missions"]
                    if m["mission_ref"] == self.mission_ref]
        self.assertEqual(len(missions), 1, "the Mission is still listed")
        return missions[0]["admitted_artifacts"]["canonical_review"]

    def test_a_list_state_refuses_findings_and_keeps_the_review(self):
        self.assert_kept(self.answer_with_state([]))

    def test_an_object_state_refuses_findings_and_keeps_the_review(self):
        self.assert_kept(self.answer_with_state({}))

    def assert_kept(self, review):
        self.assertEqual(review["state"], "admitted", review)
        self.assertEqual(review["verdict"], "APPROVE")
        self.assertEqual(review["record"]["round"], 1)
        self.assertEqual(review["record"]["reviewer_logical"], "reviewer1")
        self.assertEqual(
            (review["findings"]["state"], review["findings"]["problem"]),
            ("refused", "findings_reference_malformed"))
        self.assertEqual(review["findings"]["items"], [])
        self.assertNotIn(CANARY, json.dumps(review))


class EvidenceLinksReachTheAnswerTests(ArtifactFixture):
    """Round 15 F4, backend half: Mission Core admits up to 64 links per
    evidence record (``mission.state.MAX_ARTIFACT_LINKS``); the REAL catalog
    owner's answer carries every one of them to the office, none dropped."""

    def test_sixty_four_recorded_links_are_all_in_the_answer(self):
        from mission import state as mission_state_module
        self.assertEqual(mission_state_module.MAX_ARTIFACT_LINKS, 64)
        ids = [self.record("reports/r%02d.txt" % i, key="report_%02d" % i)
               for i in range(64)]
        self.evidence(ids)
        owner = self.owner(version=2)
        answer = owner.request(5)
        self.assertIsNotNone(answer["response"], answer["detail"])
        mission = [m for m in answer["response"]["missions"]
                   if m["mission_ref"] == self.mission_ref][0]
        records = mission["mission_state"]["report"]["evidence"]["value"]
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["artifact_ids"]), 64)
        self.assertEqual(set(records[0]["artifact_ids"]), set(ids))


class CompletedFixture(ArtifactFixture):
    """A REAL COMPLETED Mission, through Mission Core's NORMAL APIs only, in
    an isolated store: proposed, approved, a PREPARED digest-bound workspace,
    an intent and a receipt naming the task, observed running; then both
    contract deliverables recorded, the run result and the test requirement
    ACCEPTED by another principal, and a verification with every conjunct
    holding (RUNNING -> COMPLETED). Its root holds a COMPLETE task and no
    runtime agents: no live team."""

    def propose(self, state_dir, objective="Completed fixture mission"):
        """The SAME declaring contract, plus the ``run_result`` requirement
        Mission Core needs before a run can be VERIFIED."""
        import test_mission_sources as base
        value = declaring_contract()
        value["requirements"].append(dict(
            value["requirements"][0], key="run_result",
            description="the bound target result", required_artifact_keys=[]))
        surface = base.surface_module.build_bridgeless_surface(state_dir,
                                                               self.clock)
        out = surface.submit(base.mission_request(objective=objective,
                                                  proof_contract=value))
        return surface, out["request_ref"], out["mission_id"]

    def setUp(self):
        super(ArtifactFixture, self).setUp()
        self.state_dir, self.source_id = self.fresh_store()
        surface, ref, self.mission_id = self.propose(self.state_dir)
        presented = self.authorize(surface, ref)
        self.workspace = self.drive_run(self.state_dir, self.mission_id,
                                        presented, v2_tests.TASK)
        self.herd(root=self.workspace, task_id=v2_tests.TASK,
                  status="COMPLETE", runtime=False)
        self.register(self.source_id, store=self.state_dir)
        self.mission_ref = "%s:%s" % (self.source_id, self.mission_id)
        self.missions = mission_service.MissionService(
            mission_store.MissionStore(self.state_dir), self.clock)
        self.outside = os.path.join(self.root, "outside")
        os.makedirs(self.outside)
        self.secret = os.path.join(self.outside, "secret.txt")
        with open(self.secret, "wb") as handle:
            handle.write(SECRET)
        self.put("reports/result.txt", TEXT)
        self.put("reports/page.html", HTML_PAGE)
        self.deliverables = {
            "result": self.record("reports/result.txt"),
            "page": self.record("reports/page.html", HTML_PAGE,
                                key="report_page")}

    def accepted(self, requirement, artifact_ids, data):
        """An evidence record under ``requirement``, ACCEPTED."""
        from mission import record as mission_record
        submitted = self.missions.submit_evidence(
            self.mission_id,
            self.missions.mint_state_operation_id(AUTHENTICATED),
            self.missions.get_state(self.mission_id)["sequence"],
            requirement, mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
            sha(data), list(artifact_ids), AUTHENTICATED)
        self.clock.now += 1
        verifier = self.other_principal()
        self.missions.accept_evidence(
            self.mission_id, self.missions.mint_state_operation_id(verifier),
            self.missions.get_state(self.mission_id)["sequence"],
            submitted["evidence_id"], sha(data), verifier)
        return submitted["evidence_id"]

    def complete(self, also_named=()):
        """``also_named``: further artifacts the ONE accepted test record
        names (two accepted records of one requirement would contradict)."""
        from mission import record as mission_record
        result = self.accepted(mission_record.RUN_RESULT_REQUIREMENT_KEY,
                               sorted(self.deliverables.values()),
                               b"the run result\n")
        self.accepted("tests_pass", sorted(self.deliverables.values())
                      + list(also_named), TEST_LOG_DIGEST_SOURCE)
        self.missions.record_verification(
            self.mission_id,
            dict((name, True) for name, _ in mission_record.VERIFY_CONJUNCTS),
            "PARTIAL", True, sha(b"the run result\n"), result, "COMPLETE",
            AUTHENTICATED)
        record = self.missions.get(self.mission_id)["record"]
        self.assertEqual(record["state"], mission_record.STATE_COMPLETED,
                         record.get("run", {}).get("pending_proof"))
        return record

    def listed_completed(self, owner):
        """Refresh ONCE; the completed Mission as listed, and its cursor."""
        answer = owner.request(5)
        self.assertIsNotNone(answer["response"], answer["detail"])
        mission = [m for m in answer["response"]["missions"]
                   if m["mission_ref"] == self.mission_ref][0]
        identity = mission["mission_state"][v2.EXECUTION_IDENTITY_KEY]
        return mission, identity


class CompletedMissionReadTests(CompletedFixture):

    def test_a_completed_missions_declared_artifact_is_read_historically(self):
        self.complete()
        owner = self.owner(version=2)
        mission, identity = self.listed_completed(owner)
        self.assertEqual(mission["lifecycle"]["state"], "COMPLETED")
        self.assertEqual((mission["binding"]["state"],
                          mission["binding"]["reason"]),
                         ("not_applicable", "mission_not_active"))
        self.assertEqual(mission["members"], [], "no live team is invented")
        self.assertEqual(identity["state"], v2.EXECUTION_IDENTITY_ESTABLISHED,
                         identity["problem"])
        journal = mission["mission_state"]["report"]["provenance"][
            "journal_digest_sha256"]
        self.assertNotEqual(identity["cursor_sha256"], journal)
        before = self.store_bytes()
        answer = self.read(owner, self.deliverables["result"],
                           identity["cursor_sha256"])
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])
        self.assertEqual(answer["content"]["text"], TEXT.decode("utf-8"))
        self.assertEqual(answer["content"]["digest_sha256"], sha(TEXT))
        self.assertEqual(answer["artifact"]["execution_basis"],
                         artifact_content.EXECUTION_HISTORICAL)
        self.assertEqual(answer["artifact"]["admission_basis"], [
            artifact_content.BASIS_ACCEPTED_EVIDENCE,
            artifact_content.BASIS_CONTRACT_REQUIRED])
        self.assertEqual(self.store_bytes(), before, "nothing is written")


class CompletedMissionWriteBoundaryTests(CompletedFixture):
    """Why a completed Mission's readable artifact must be recorded BEFORE
    completion (as the operator documentation says): Mission Core refuses
    recording a result artifact on a terminal Mission."""

    def test_recording_after_completion_is_refused_by_mission_core(self):
        from mission import record as mission_record
        self.complete()
        self.put("reports/late.txt", TEXT)
        with self.assertRaises(mission_record.MissionError):
            self.record("reports/late.txt", key="late_report")


class CompletedIdentityMutationTests(CompletedFixture):
    """THE JOURNAL HEAD IS NOT ENOUGH. ``journal.head_cursor`` covers the
    state sequence, the revision and the evidence journal ONLY: the run, the
    lifecycle chain and the source registration are outside it. Each case
    below changes one of those AFTER the listing, proves the record still
    passes Mission Core's own load validation and that the journal cursor is
    UNCHANGED, and requires a refusal with no file byte read."""

    def setUp(self):
        super(CompletedIdentityMutationTests, self).setUp()
        self.complete()
        self.cached_owner = self.owner(version=2)
        _, identity = self.listed_completed(self.cached_owner)
        self.cursor = identity["cursor_sha256"]
        self.head = self.journal_head()

    def journal_head(self):
        from mission import journal
        document = mission_store.MissionStore(self.state_dir).load()
        return journal.head_cursor(
            document["missions"][self.mission_id],
            document["mission_state"].get(self.mission_id))[
                "journal_digest_sha256"]

    def edit_record(self, change):
        """Edit the stored record directly (an isolated store), then PROVE it
        still validates natively and its journal cursor did not move."""
        path = os.path.join(self.state_dir, mission_store.MISSIONS_FILE_NAME)
        with open(path) as handle:
            document = json.load(handle)
        change(document["missions"][self.mission_id])
        with open(path, "w") as handle:
            json.dump(document, handle, indent=1, sort_keys=True)
        self.assertEqual(self.journal_head(), self.head,
                         "the journal cursor is UNCHANGED by this mutation")

    def edit_registration(self, field, value):
        with open(self.registry.path) as handle:
            document = json.load(handle)
        for source in document["sources"]:
            if source["source_id"] == self.source_id:
                source[field] = value
        with open(self.registry.path, "w") as handle:
            json.dump(document, handle)
        self.registry.load()                    # still a valid registry
        self.assertEqual(self.journal_head(), self.head)

    def assert_refused_unread(self, problem):
        reads = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            reads.append(args)
            return real(*args)
        with mock.patch.object(state_artifact, "read_state_artifact", spy):
            answer = self.read(self.cached_owner, self.deliverables["result"],
                               self.cursor)
        self.assert_refused(answer, problem)
        self.assertEqual(reads, [], "no content byte is read")

    def test_a_receipt_change_is_refused(self):
        def change(mission):
            mission["run"]["receipt"]["identity_source"] = "reconciliation"
        self.edit_record(change)
        self.assert_refused_unread(artifact_content.PROBLEM_HISTORICAL_CHANGED)

    def test_a_prepared_binding_change_is_refused(self):
        def change(mission):
            mission["run"]["workspace"]["recorded_at"] -= 1
        self.edit_record(change)
        self.assert_refused_unread(artifact_content.PROBLEM_HISTORICAL_CHANGED)

    def test_a_lifecycle_chain_change_is_refused(self):
        def change(mission):
            mission["lifecycle"][-1]["recorded_at"] += 1
            mission["updated_at"] += 1
        self.edit_record(change)
        self.assert_refused_unread(artifact_content.PROBLEM_HISTORICAL_CHANGED)

    def test_a_lifecycle_state_change_is_refused(self):
        """COMPLETED becomes a VALID BLOCKED record (a failed verification),
        the evidence journal untouched."""
        from mission import record as mission_record
        name, code = mission_record.VERIFY_CONJUNCTS[0]

        def change(mission):
            verification = mission["run"]["verification"]
            verification["verified"] = False
            verification["failed_conjunct"] = code
            verification["result_evidence_id"] = None
            for conjunct in verification["conjuncts"]:
                if conjunct["name"] == name:
                    conjunct["holds"] = False
            mission["lifecycle"][-1]["to_state"] = mission_record.STATE_BLOCKED
            mission["lifecycle"][-1]["reason"] = code
            mission["state"] = mission_record.STATE_BLOCKED
        self.edit_record(change)
        self.assert_refused_unread(artifact_content.PROBLEM_HISTORICAL_CHANGED)

    def test_a_source_registration_change_is_refused(self):
        self.edit_registration("producer", "another_producer")
        self.assert_refused_unread(
            artifact_content.PROBLEM_REGISTRATION_CHANGED)

    def test_a_run_change_while_the_file_is_read_discards_the_content(self):
        real = state_artifact.read_state_artifact

        def racing(*args):
            result = real(*args)

            def change(mission):
                mission["run"]["receipt"]["recorded_at"] += 1
            self.edit_record(change)
            return result
        with mock.patch.object(state_artifact, "read_state_artifact", racing):
            answer = self.read(self.cached_owner, self.deliverables["result"],
                               self.cursor)
        self.assert_refused(
            answer, artifact_content.PROBLEM_HISTORICAL_CHANGED_DURING_READ)
        self.assertIsNone(answer["content"])

    def test_the_bare_journal_digest_is_not_a_completed_missions_cursor(self):
        """A completed Mission is read only at its HISTORICAL cursor; its
        journal digest alone names the evidence journal, not the execution."""
        mission, _ = self.listed_completed(self.cached_owner)
        journal_digest = mission["mission_state"]["report"]["provenance"][
            "journal_digest_sha256"]
        self.assertEqual(journal_digest, self.head)
        self.cursor = journal_digest
        self.assert_refused_unread(artifact_content.PROBLEM_CURSOR_NOT_LISTED)

    def test_an_older_listings_cursor_never_resolves_through_a_newer_identity(
            self):
        """Listed (cursor 1); the run changes, journal UNCHANGED; the owner
        refreshes and now lists cursor 2. A read naming cursor 1 is refused:
        it is never answered with the newer identity."""
        def change(mission):
            mission["run"]["receipt"]["identity_source"] = "reconciliation"
        self.edit_record(change)
        from target_runtime import catalog
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS   # a REAL refresh
        _, identity = self.listed_completed(self.cached_owner)
        self.assertEqual(identity["state"], v2.EXECUTION_IDENTITY_ESTABLISHED)
        self.assertNotEqual(identity["cursor_sha256"], self.cursor)
        self.assert_refused_unread(artifact_content.PROBLEM_CURSOR_NOT_LISTED)
        self.cursor = identity["cursor_sha256"]      # the NEW listing reads
        answer = self.read(self.cached_owner, self.deliverables["result"],
                           self.cursor)
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])

    def test_an_unchanged_record_is_still_admitted(self):
        """The control: the same cached listing, nothing changed."""
        answer = self.read(self.cached_owner, self.deliverables["result"],
                           self.cursor)
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])


class CompletedAdmissionRefusalTests(CompletedFixture):
    """What a completed Mission's read REFUSES, each with its own code, and
    the existing class, disclosure and file refusals unchanged on the
    historical path. Completion is not a disclosure grant."""

    def store_edit(self, change):
        path = os.path.join(self.state_dir, mission_store.MISSIONS_FILE_NAME)
        with open(path) as handle:
            document = json.load(handle)
        change(document["missions"][self.mission_id])
        with open(path, "w") as handle:
            json.dump(document, handle, indent=1, sort_keys=True)

    def unread(self, owner, artifact_id, cursor, problem):
        reads = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            reads.append(args)
            return real(*args)
        with mock.patch.object(state_artifact, "read_state_artifact", spy):
            answer = self.read(owner, artifact_id, cursor)
        self.assert_refused(answer, problem)
        self.assertEqual(reads, [], "no content byte is read")
        return answer

    def journal_of(self, mission):
        return mission["mission_state"]["report"]["provenance"][
            "journal_digest_sha256"]

    def test_a_legacy_record_without_a_prepared_binding_is_never_upgraded(self):
        self.complete()

        def legacy(mission):
            del mission["run"]["workspace"]
        self.store_edit(legacy)
        mission_store.MissionStore(self.state_dir).load()   # still valid
        owner = self.owner(version=2)
        mission, identity = self.listed_completed(owner)
        self.assertEqual((identity["state"], identity["problem"]), (
            v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
            v2.PROBLEM_EXECUTION_BINDING_ABSENT))
        self.assertIsNone(identity["cursor_sha256"])
        answer = self.unread(owner, self.deliverables["result"],
                             self.journal_of(mission),
                             artifact_content.PROBLEM_HISTORICAL_IDENTITY)
        self.assertIn(v2.PROBLEM_EXECUTION_BINDING_ABSENT, answer["detail"])

    def test_an_inconsistent_record_fails_the_native_validation_boundary(self):
        self.complete()
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)

        def mismatch(mission):
            mission["run"]["intent"]["workspace_realpath"] = os.path.join(
                self.root, "another-workspace")
        self.store_edit(mismatch)
        with self.assertRaises(Exception):
            mission_store.MissionStore(self.state_dir).load()
        self.unread(owner, self.deliverables["result"],
                    identity["cursor_sha256"], artifact_content.PROBLEM_STORE)

    def test_another_mission_claiming_the_same_task_is_a_conflict(self):
        self.complete()
        surface, ref, other = self.propose(self.state_dir, "Second mission")
        presented = self.authorize(surface, ref)
        self.drive_run(self.state_dir, other, presented, v2_tests.TASK,
                       workspace_name="ws-second")
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        self.unread(owner, self.deliverables["result"],
                    identity["cursor_sha256"],
                    artifact_content.PROBLEM_HISTORICAL_CONFLICT)

    def test_uniqueness_is_never_inferred_from_an_incomplete_listing(self):
        self.complete()
        self.register("mc-missing", store=os.path.join(self.root, "absent"))
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        self.unread(owner, self.deliverables["result"],
                    identity["cursor_sha256"],
                    artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)

    def test_an_inactive_mission_that_is_not_completed_keeps_its_refusal(self):
        from mission import record as mission_record
        result = self.accepted(mission_record.RUN_RESULT_REQUIREMENT_KEY,
                               sorted(self.deliverables.values()),
                               b"the run result\n")
        holds = dict((n, True) for n, _ in mission_record.VERIFY_CONJUNCTS)
        holds[mission_record.VERIFY_CONJUNCTS[0][0]] = False
        self.missions.record_verification(
            self.mission_id, holds, "PARTIAL", True, sha(b"x"), result,
            "COMPLETE", AUTHENTICATED)
        owner = self.owner(version=2)
        mission, identity = self.listed_completed(owner)
        self.assertEqual(mission["lifecycle"]["state"],
                         mission_record.STATE_BLOCKED)
        self.assertEqual((identity["state"], identity["problem"]), (
            v2.EXECUTION_IDENTITY_NOT_APPLICABLE,
            v2.PROBLEM_EXECUTION_NOT_COMPLETED))
        self.unread(owner, self.deliverables["result"],
                    self.journal_of(mission),
                    artifact_content.PROBLEM_NOT_BOUND)

    def test_a_disappeared_source_is_refused(self):
        self.complete()
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        with open(self.registry.path) as handle:
            document = json.load(handle)
        document["sources"] = [x for x in document["sources"]
                               if x["source_id"] != self.source_id]
        with open(self.registry.path, "w") as handle:
            json.dump(document, handle)
        self.unread(owner, self.deliverables["result"],
                    identity["cursor_sha256"],
                    artifact_content.PROBLEM_SOURCE_NOT_REGISTERED)

    def test_a_disappeared_root_reads_nothing(self):
        self.complete()
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        os.rename(self.workspace, self.workspace + "-moved")
        answer = self.read(owner, self.deliverables["result"],
                           identity["cursor_sha256"])
        self.assertEqual(answer["state"], artifact_content.STATE_REFUSED)
        self.assertIsNone(answer["content"])

    def test_an_expired_listing_corroborates_nothing(self):
        self.complete()
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        self.clock.now += 3600
        self.unread(owner, self.deliverables["result"],
                    identity["cursor_sha256"],
                    artifact_content.PROBLEM_NOT_CORROBORATED)

    def test_every_class_disclosure_and_file_refusal_holds_when_completed(self):
        cases = {}
        self.put("reports/private-log.txt", PRIVATE_LOG)
        cases[artifact_content.PROBLEM_CLASS_NOT_DECLARED] = self.record(
            "reports/private-log.txt", PRIVATE_LOG, key="session_notes")
        with open(os.path.join(self.workspace, ".herd", "state",
                               "task.json"), "rb") as handle:
            task = handle.read()
        private = self.record(".herd/state/task.json", task, key="private")
        self.put("keys/api_token.txt", TEXT)
        credential = self.record("keys/api_token.txt", key="credential")
        self.put("reports/chart.png", TEXT)
        binary = self.record("reports/chart.png", key="chart")
        self.put("reports/changed.txt", TEXT)
        changed = self.record("reports/changed.txt", key="changed")
        missing = self.record("reports/never-written.txt", key="missing")
        os.symlink(self.secret, os.path.join(self.workspace, "reports",
                                             "link.txt"))
        link = self.record("reports/link.txt", TEXT, key="link")
        declared = [private, credential, binary, changed, missing, link]
        self.complete(also_named=declared)
        self.put("reports/changed.txt", TEXT + b"edited after recording\n")
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        cursor = identity["cursor_sha256"]
        expected = {
            cases[artifact_content.PROBLEM_CLASS_NOT_DECLARED]:
                artifact_content.PROBLEM_CLASS_NOT_DECLARED,
            private: artifact_content.PROBLEM_PRIVATE_LOCATION,
            credential: artifact_content.PROBLEM_CREDENTIAL_LIKE,
            binary: artifact_content.PROBLEM_FORMAT_UNSUPPORTED,
        }
        for artifact_id, problem in expected.items():
            with self.subTest(problem):
                self.unread(owner, artifact_id, cursor, problem)
        for artifact_id, problem in (
                (changed, artifact_content.PROBLEM_DIGEST_MISMATCH),
                (missing, artifact_content.PROBLEM_FILE_MISSING)):
            with self.subTest(problem):
                self.assert_refused(self.read(owner, artifact_id, cursor),
                                    problem)
        answer = self.read(owner, link, cursor)
        self.assertEqual(answer["state"], artifact_content.STATE_REFUSED)
        self.assertIsNone(answer["content"])
        self.assertNotIn(SECRET.decode(), json.dumps(answer))


class SameExecutionObservationTests(CompletedFixture):
    """One execution, two source kinds (the Lead's same-execution decision):
    a registered ``task_native`` source OBSERVES the completed Mission's own
    herd root through the REAL task-native producer, so the listing carries
    the canonical row AND a ``herdr.task/1`` row with the SAME task and
    workspace. That observation is not a competing claim; it supplies no
    read authority; every real conflict still refuses."""

    def observe(self, roots=None, source_id="herd", name="di-registry"):
        """A task-native registry (``repos.json``, the locator) over
        ``roots`` ({alias: path}), registered as a ``task_native`` source."""
        directory = os.path.realpath(os.path.join(self.root, name))
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "repos.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"version": 1, "repos": dict(
                (alias, {"path": root, "registered_at": 1800000000})
                for alias, root in (roots or {"completed": self.workspace}
                                    ).items())}, indent=2) + "\n")
        os.chmod(path, 0o644)
        self.register(source_id=source_id, kind="task_native",
                      store=directory, producer="herdctl")
        return directory

    def native_rows(self, answer, source_id="herd"):
        return [m for m in answer["response"]["missions"]
                if m["source_id"] == source_id]

    def test_a_task_native_observation_of_the_same_execution_admits(self):
        self.complete()
        self.observe()
        owner = self.owner(version=2)
        answer = owner.request(5)
        self.assertIsNotNone(answer["response"], answer["detail"])
        canonical = [m for m in answer["response"]["missions"]
                     if m["mission_ref"] == self.mission_ref][0]
        natives = self.native_rows(answer)
        self.assertEqual(len(natives), 1)
        native = natives[0]
        self.assertEqual(native["lifecycle"]["model"], "herdr.task/1")
        self.assertEqual(native["execution"]["identity_source"],
                         "herdr.task_json")
        self.assertEqual(
            (native["execution"]["task_id"],
             native["execution"]["workspace_realpath"]),
            (canonical["execution"]["task_id"],
             canonical["execution"]["workspace_realpath"]),
            "the SAME execution, observed through a second source kind")
        self.assertEqual(canonical["members"], [], "no live team invented")
        identity = canonical["mission_state"][v2.EXECUTION_IDENTITY_KEY]
        answer_ = self.read(owner, self.deliverables["result"],
                            identity["cursor_sha256"])
        self.assertEqual(answer_["state"], artifact_content.STATE_ADMITTED,
                         (answer_["problem"], answer_["detail"]))
        self.assertEqual(answer_["artifact"]["execution_basis"],
                         artifact_content.EXECUTION_HISTORICAL)
        self.assertEqual(answer_["content"]["text"], TEXT.decode("utf-8"))


class SameExecutionRefusalTests(SameExecutionObservationTests):
    """What the observation rule must STILL refuse (never widened): every
    one-coordinate mismatch, an inconsistent or contradictory observation,
    untrusted or malformed native provenance, a genuine second canonical
    claim even with an identical task and path, a partial observation
    source, and changed corroboration. Each refuses with no file byte read.
    A native observation never repairs a missing canonical fact."""

    def other_root(self, name, task_id, status="COMPLETE", repo=None):
        root = os.path.realpath(os.path.join(self.root, name))
        os.makedirs(root, exist_ok=True)
        state = os.path.join(root, ".herd", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "task.json"), "w") as handle:
            json.dump({"version": 1, "id": task_id, "status": status,
                       "description": "SYNTHETIC task",
                       "repo": root if repo is None else repo,
                       "started_at": 1800000000}, handle)
        return root

    def task_json(self, **fields):
        path = os.path.join(self.workspace, ".herd", "state", "task.json")
        with open(path) as handle:
            task = json.load(handle)
        task.update(fields)
        with open(path, "w") as handle:
            json.dump(task, handle)

    def listing(self):
        owner = self.owner(version=2)
        answer = owner.request(5)
        self.assertIsNotNone(answer["response"], answer["detail"])
        canonical = [m for m in answer["response"]["missions"]
                     if m["mission_ref"] == self.mission_ref][0]
        cursor = canonical["mission_state"][v2.EXECUTION_IDENTITY_KEY][
            "cursor_sha256"]
        return owner, answer, cursor

    def refused_unread(self, owner, cursor, problem, contains=None):
        reads = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            reads.append(args)
            return real(*args)
        with mock.patch.object(state_artifact, "read_state_artifact", spy):
            answer = self.read(owner, self.deliverables["result"], cursor)
        self.assert_refused(answer, problem)
        self.assertEqual(reads, [], "no content byte is read")
        if contains is not None:
            self.assertIn(contains, answer["detail"])
        return answer

    # -- the real producer ---------------------------------------------------

    def test_same_task_in_a_different_workspace_competes(self):
        self.complete()
        self.observe({"other": self.other_root("elsewhere", v2_tests.TASK)})
        owner, _, cursor = self.listing()
        self.refused_unread(owner, cursor,
                            artifact_content.PROBLEM_HISTORICAL_CONFLICT,
                            "different task or a different workspace")

    def test_the_same_workspace_with_a_different_task_competes(self):
        self.complete()
        self.task_json(id="20261016-999999-ffffff")
        self.observe()
        owner, _, cursor = self.listing()
        self.refused_unread(owner, cursor,
                            artifact_content.PROBLEM_HISTORICAL_CONFLICT,
                            "different task or a different workspace")

    def test_an_inconsistent_observation_competes(self):
        """``task.json`` does not name the root it was read from."""
        self.complete()
        self.task_json(repo=os.path.join(self.root, "not-this-root"))
        self.observe()
        owner, _, cursor = self.listing()
        self.refused_unread(owner, cursor,
                            artifact_content.PROBLEM_HISTORICAL_CONFLICT,
                            "not consistent and unambiguous")

    def test_a_contradictory_native_status_competes(self):
        for status in ("ACTIVE", "FAILED", "ABORTED", "ERROR", "SOMEDAY"):
            with self.subTest(status=status):
                self.setUp()
                self.complete()
                self.task_json(status=status)
                self.observe()
                owner, _, cursor = self.listing()
                self.refused_unread(
                    owner, cursor,
                    artifact_content.PROBLEM_HISTORICAL_CONFLICT,
                    "contradicts the completed execution")

    def test_a_genuine_workflow_claim_with_the_identical_task_and_path(self):
        self.complete()
        store = self.store_dir("wf")
        self.workflow(store, task_id=v2_tests.TASK, workspace=self.workspace)
        self.register(source_id="wf", kind="workflow_store", store=store,
                      producer="grok_bot")
        owner, answer, cursor = self.listing()
        claims = [m for m in answer["response"]["missions"]
                  if m["source_id"] == "wf"]
        self.assertEqual(
            (claims[0]["execution"]["task_id"],
             claims[0]["execution"]["workspace_realpath"]),
            (v2_tests.TASK, self.workspace), "identical task AND path")
        self.refused_unread(owner, cursor,
                            artifact_content.PROBLEM_HISTORICAL_CONFLICT,
                            "claim of a workflow_store source")

    def test_a_partial_observation_source_proves_nothing(self):
        """The task-native source cannot be read: not fresh, so the listing
        cannot show the task and workspace are claimed once."""
        self.complete()
        directory = os.path.realpath(os.path.join(self.root, "empty-reg"))
        os.makedirs(directory)
        self.register(source_id="herd", kind="task_native", store=directory,
                      producer="herdctl")
        owner, _, cursor = self.listing()
        self.refused_unread(owner, cursor,
                            artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)

    def test_two_qualifying_observations_both_observe_and_both_are_checked(self):
        """Duplicate observations: two task-native sources over the SAME root.
        Each is judged on its own; neither is chosen or merged; both agree,
        so the read is admitted — and if either stops qualifying, it is not."""
        self.complete()
        self.observe(source_id="herd")
        self.observe(source_id="herd-b", name="di-registry-b")
        owner, answer, cursor = self.listing()
        self.assertEqual(len(self.native_rows(answer, "herd")), 1)
        self.assertEqual(len(self.native_rows(answer, "herd-b")), 1)
        admitted = self.read(owner, self.deliverables["result"], cursor)
        self.assertEqual(admitted["state"], artifact_content.STATE_ADMITTED,
                         admitted["detail"])
        doctored = copy.deepcopy(answer)
        for row in doctored["response"]["missions"]:
            if row["source_id"] == "herd-b":
                row["execution"]["consistent"] = False
        with mock.patch.object(owner, "_corroborating_answer",
                               lambda: doctored):
            self.refused_unread(owner, cursor,
                                artifact_content.PROBLEM_HISTORICAL_CONFLICT,
                                "herd-b:")

    def test_an_observation_never_repairs_a_missing_canonical_fact(self):
        self.complete()
        path = os.path.join(self.state_dir, mission_store.MISSIONS_FILE_NAME)
        with open(path) as handle:
            document = json.load(handle)
        del document["missions"][self.mission_id]["run"]["workspace"]
        with open(path, "w") as handle:
            json.dump(document, handle, indent=1, sort_keys=True)
        self.observe()
        owner = self.owner(version=2)
        answer = owner.request(5)
        canonical = [m for m in answer["response"]["missions"]
                     if m["mission_ref"] == self.mission_ref][0]
        journal = canonical["mission_state"]["report"]["provenance"][
            "journal_digest_sha256"]
        self.refused_unread(owner, journal,
                            artifact_content.PROBLEM_HISTORICAL_IDENTITY)

    # -- changed corroboration -----------------------------------------------

    def test_the_observing_source_registration_is_rechecked_at_read(self):
        self.complete()
        self.observe()
        owner, _, cursor = self.listing()
        with open(self.registry.path) as handle:
            document = json.load(handle)
        for source in document["sources"]:
            if source["source_id"] == "herd":
                source["producer"] = "another"
        with open(self.registry.path, "w") as handle:
            json.dump(document, handle)
        self.refused_unread(owner, cursor,
                            artifact_content.PROBLEM_REGISTRATION_CHANGED,
                            "herd")

    def test_an_observation_that_changes_during_the_read_discards(self):
        self.complete()
        self.observe()
        owner, _, cursor = self.listing()
        real = state_artifact.read_state_artifact

        def racing(*args):
            result = real(*args)
            self.task_json(status="FAILED")
            from target_runtime import catalog
            self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
            owner.request(5)                       # the owner refreshes
            return result
        with mock.patch.object(state_artifact, "read_state_artifact", racing):
            answer = self.read(owner, self.deliverables["result"], cursor)
        self.assertEqual(answer["state"], artifact_content.STATE_REFUSED)
        self.assertIsNone(answer["content"])

    def test_a_still_qualifying_observation_change_during_the_read_discards(
            self):
        """The observation set is part of the corroboration re-checked before
        release: even a change that keeps it qualifying refuses."""
        self.complete()
        self.observe()
        owner, _, cursor = self.listing()
        real = state_artifact.read_state_artifact

        def racing(*args):
            result = real(*args)
            self.task_json(status="COMPLETED")         # still agrees
            from target_runtime import catalog
            self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
            owner.request(5)
            return result
        with mock.patch.object(state_artifact, "read_state_artifact", racing):
            answer = self.read(owner, self.deliverables["result"], cursor)
        self.assert_refused(answer, artifact_content.PROBLEM_BINDING_CHANGED)
        self.assertIsNone(answer["content"])

    # -- provenance the real producer never emits (a doctored listing) -----

    def test_untrusted_or_malformed_native_provenance_competes(self):
        self.complete()
        self.observe()
        owner, answer, cursor = self.listing()

        def kind(m, r):
            [b for b in r["response"]["sources"] if b["source_id"] == "herd"][
                0]["kind"] = "mission_core"

        cases = {
            "lifecycle model": lambda m, r: m["lifecycle"].update(
                model="herdr.task/2"),
            "producer schema": lambda m, r: m["producer"]["recorded"].update(
                schema="di.catalog.other"),
            "schema version": lambda m, r: m["producer"]["recorded"].update(
                schema_version=2),
            "identity source": lambda m, r: m["execution"].update(
                identity_source="start_result"),
            "registered kind": kind,
            "ambiguous record": lambda m, r: m["execution"].update(
                record_ambiguous=True),
            "run not started": lambda m, r: m["execution"].update(
                run_started=False),
            "no producer record": lambda m, r: m["producer"].update(
                recorded=None),
            "a missing fact": lambda m, r: m["execution"].pop(
                "identity_source"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                doctored = copy.deepcopy(answer)
                row = [m for m in doctored["response"]["missions"]
                       if m["source_id"] == "herd"][0]
                change(row, doctored)
                with mock.patch.object(owner, "_corroborating_answer",
                                       lambda: doctored):
                    self.refused_unread(
                        owner, cursor,
                        artifact_content.PROBLEM_HISTORICAL_CONFLICT)

    def test_a_second_canonical_claim_with_the_identical_task_and_path(self):
        """Mission Core claims are never observations: a second canonical
        row naming the identical task AND workspace refuses."""
        self.complete()
        owner, answer, cursor = self.listing()
        doctored = copy.deepcopy(answer)
        twin = copy.deepcopy([m for m in doctored["response"]["missions"]
                              if m["mission_ref"] == self.mission_ref][0])
        twin["mission_ref"] = "%s:mn-%s" % (self.source_id, "f" * 32)
        twin["native_id"] = "mn-" + "f" * 32
        doctored["response"]["missions"].append(twin)
        for block in doctored["response"]["sources"]:
            if block["source_id"] == self.source_id:
                block["record_count"] += 1
        with mock.patch.object(owner, "_corroborating_answer",
                               lambda: doctored):
            self.refused_unread(owner, cursor,
                                artifact_content.PROBLEM_HISTORICAL_CONFLICT,
                                "claim of a mission_core source")


class ExecutionIdentityTests(CompletedFixture):
    """``mission_state.execution_identity`` over the REAL completed record,
    one required fact taken away at a time: each is NOT established with its
    own code; only the whole chain is established."""

    def test_each_missing_or_disagreeing_fact_has_its_own_code(self):
        from target_runtime import mission_state
        import copy as copy_module
        self.complete()
        document = mission_store.MissionStore(self.state_dir).load()
        record = document["missions"][self.mission_id]
        source = [x for x in self.registry.load()["sources"]
                  if x["source_id"] == self.source_id][0]
        whole = mission_state.execution_identity(source, record)
        self.assertEqual(whole["state"], v2.EXECUTION_IDENTITY_ESTABLISHED)

        def run(change):
            changed = copy_module.deepcopy(record)
            change(changed)
            return mission_state.execution_identity(source, changed)
        cases = (
            (lambda m: m.update(state="RUNNING"),
             v2.EXECUTION_IDENTITY_NOT_APPLICABLE,
             v2.PROBLEM_EXECUTION_NOT_COMPLETED),
            (lambda m: m.pop("run"), v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_RUN_ABSENT),
            (lambda m: m["run"]["verification"].update(verified=False),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_NOT_VERIFIED),
            (lambda m: m["run"].update(intent=None),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_INTENT_ABSENT),
            (lambda m: m["run"]["receipt"].update(task_id=None),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_RECEIPT_TASK_ABSENT),
            (lambda m: m["run"].pop("workspace"),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_BINDING_ABSENT),
            (lambda m: m["run"]["workspace"].update(state="recorded"),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_BINDING_NOT_PREPARED),
            (lambda m: m["run"]["workspace"].update(
                binding_digest_sha256="0" * 64),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_BINDING_DISAGREES),
            (lambda m: m["run"]["intent"].update(
                observed_baseline_commit_sha="e" * 40),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_BINDING_DISAGREES),
            (lambda m: m["run"].update(workspace=["not", "a", "binding"]),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_BINDING_NOT_PREPARED),
            (lambda m: m["run"]["workspace"].pop("path_realpath"),
             v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
             v2.PROBLEM_EXECUTION_RECORD_SHAPE),
        )
        for index, (change, state_name, problem) in enumerate(cases):
            with self.subTest(index=index, problem=problem):
                found = run(change)
                self.assertEqual((found["state"], found["problem"],
                                  found["identity_sha256"]),
                                 (state_name, problem, None))

    def test_the_identity_covers_the_source_registration(self):
        from target_runtime import mission_state
        self.complete()
        document = mission_store.MissionStore(self.state_dir).load()
        record = document["missions"][self.mission_id]
        source = [x for x in self.registry.load()["sources"]
                  if x["source_id"] == self.source_id][0]
        a = mission_state.execution_identity(source, record)
        b = mission_state.execution_identity(dict(source, producer="other"),
                                             record)
        self.assertNotEqual(a["identity_sha256"], b["identity_sha256"])
        self.assertNotEqual(
            mission_state.historical_cursor("a" * 64, a["identity_sha256"]),
            mission_state.historical_cursor("a" * 64, b["identity_sha256"]))


class LegacyPrivateArtifactBoundaryTests(CompletedFixture):
    """The LEGACY capability limit (supervisor boundary 07eb1919), at the
    real boundary: a completed Mission whose recorded artifacts lie beneath
    `.herd/evidence` (the shape Root's inventory found on every artifact of
    the four existing completed Missions). Its identity is ESTABLISHED, a
    declared body is still refused as a PRIVATE location with NO read
    primitive invoked, and every admission failure that comes FIRST keeps its
    own cause: it is never collapsed into the private-location refusal, and
    the private-location refusal is never blamed on missing identity."""

    unread = CompletedAdmissionRefusalTests.unread
    store_edit = CompletedAdmissionRefusalTests.store_edit
    journal_of = CompletedAdmissionRefusalTests.journal_of

    def setUp(self):
        super().setUp()
        self.put(".herd/evidence/run/test-output.txt", TEXT)
        self.private = self.record(".herd/evidence/run/test-output.txt",
                                   key="evidence_test_output")
        self.put(".herd/evidence/run/notes.txt", TEXT)
        self.undeclared = self.record(".herd/evidence/run/notes.txt",
                                      key="evidence_notes")
        self.complete(also_named=[self.private])

    def test_a_declared_private_body_is_refused_unread(self):
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        self.assertEqual(identity["state"], v2.EXECUTION_IDENTITY_ESTABLISHED)
        answer = self.unread(owner, self.private, identity["cursor_sha256"],
                             artifact_content.PROBLEM_PRIVATE_LOCATION)
        self.assertIsNone(answer["content"])
        self.assertIn(".herd", answer["detail"])
        # Linkage is necessary, never disclosure authority: the class check
        # comes FIRST and passed (an accepted verification record names this
        # artifact), yet the body is refused.
        # The SAME completed Mission still admits its declared PUBLIC body.
        self.assertEqual(self.read(owner, self.deliverables["result"],
                                   identity["cursor_sha256"])["state"],
                         artifact_content.STATE_ADMITTED)

    def test_an_undeclared_private_body_keeps_its_class_refusal(self):
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        self.unread(owner, self.undeclared, identity["cursor_sha256"],
                    artifact_content.PROBLEM_CLASS_NOT_DECLARED)

    def test_a_cursor_that_is_not_the_listed_one_keeps_its_cause(self):
        owner = self.owner(version=2)
        mission, _ = self.listed_completed(owner)
        self.unread(owner, self.private, self.journal_of(mission),
                    artifact_content.PROBLEM_CURSOR_NOT_LISTED)

    def test_an_expired_listing_keeps_its_cause(self):
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        self.clock.now += 3600
        self.unread(owner, self.private, identity["cursor_sha256"],
                    artifact_content.PROBLEM_NOT_CORROBORATED)

    def test_no_prepared_binding_keeps_its_identity_cause(self):
        def legacy(mission):
            del mission["run"]["workspace"]
        self.store_edit(legacy)
        owner = self.owner(version=2)
        mission, identity = self.listed_completed(owner)
        self.assertEqual(identity["problem"],
                         v2.PROBLEM_EXECUTION_BINDING_ABSENT)
        self.unread(owner, self.private, self.journal_of(mission),
                    artifact_content.PROBLEM_HISTORICAL_IDENTITY)


class CompletedHttpRouteTests(CompletedFixture):
    """A COMPLETED Mission read END TO END over the REAL loopback server, as
    the office asks for it: the listing through GET /catalog (the canonical
    row plus the task-native observation of the same execution), then the
    content through GET /artifact at the listed HISTORICAL cursor. The
    declared public artifact was recorded BEFORE completion. TEST DATA: no
    real execution, verification or authorization."""

    observe = SameExecutionObservationTests.observe

    def artifact_path(self, artifact_id, cursor):
        return "/artifact?mission_ref=%s&artifact_id=%s&cursor=%s" % (
            self.mission_ref, artifact_id, cursor)

    def test_a_completed_missions_artifact_reads_over_http(self):
        self.complete()
        self.observe()
        owner = self.owner(version=2, roots=[self.workspace])
        # As the launcher builds it for version 2: the served version's own
        # freshness operation (catalog_http.main).
        server = self.serve(owner, server_class=(
            lambda owner_, token, clock, port: catalog_http.
            LoopbackCatalogServer(owner_, token, clock, port,
                                  owner_.response_freshness)))
        status, _headers, _body, answer = self.catalog_get(server)
        self.assertEqual(status, 200)
        missions = answer["response"]["missions"]
        [canonical] = [m for m in missions
                       if m["mission_ref"] == self.mission_ref]
        natives = [m for m in missions
                   if m["lifecycle"]["model"] == "herdr.task/1"]
        self.assertEqual(len(natives), 1)
        self.assertEqual(
            (natives[0]["execution"]["task_id"],
             natives[0]["execution"]["workspace_realpath"]),
            (canonical["execution"]["task_id"],
             canonical["execution"]["workspace_realpath"]))
        self.assertEqual(canonical["members"], [])
        identity = canonical["mission_state"][v2.EXECUTION_IDENTITY_KEY]
        self.assertEqual(identity["state"], v2.EXECUTION_IDENTITY_ESTABLISHED)
        for artifact_id, body in ((self.deliverables["result"], TEXT),
                                  (self.deliverables["page"], HTML_PAGE)):
            status, headers, raw = self.request(
                server, self.artifact_path(artifact_id,
                                           identity["cursor_sha256"]))
            self.assertEqual(status, 200)
            self.assert_no_token(raw, repr(headers))
            self.assertEqual(self.header(headers, "Cache-Control"),
                             "no-store")
            read = json.loads(raw)
            self.assertEqual(read["state"], artifact_content.STATE_ADMITTED,
                             read["detail"])
            self.assertEqual(read["artifact"]["execution_basis"],
                             artifact_content.EXECUTION_HISTORICAL)
            self.assertEqual(read["content"]["text"], body.decode("utf-8"))
            self.assertEqual(read["content"]["digest_sha256"], sha(body))
            self.assertEqual(read["content"]["digest_sha256"],
                             read["artifact"]["content_digest_sha256"])
        # The same route, the bare journal digest: refused, nothing shown.
        journal = canonical["mission_state"]["report"]["provenance"][
            "journal_digest_sha256"]
        refused = json.loads(self.request(server, self.artifact_path(
            self.deliverables["result"], journal))[2])
        self.assertEqual(refused["problem"],
                         artifact_content.PROBLEM_CURSOR_NOT_LISTED)
        self.assertIsNone(refused["content"])


class RegistrationRevalidationTests(CompletedFixture):
    """Round 17 (F1, F2): a COMPLETED Mission's read stands on the CURRENT
    registrations, re-read from the REAL registry before admission AND again
    before release. Every mutation here is a supported registry operation
    (``SourceRegistry.register`` / ``unregister``) on the isolated registry,
    and the owner is NEVER refreshed during it (asserted), so its cached
    listing cannot hide anything."""

    observe = SameExecutionObservationTests.observe
    unread = CompletedAdmissionRefusalTests.unread

    def setUp(self):
        super(RegistrationRevalidationTests, self).setUp()
        self.complete()
        self.observer = self.observe()

    def listed_owner(self):
        owner = self.owner(version=2)
        _, identity = self.listed_completed(owner)
        return owner, identity["cursor_sha256"]

    def unrelated_store(self, name):
        """A REAL Mission Core store holding one unrelated proposal."""
        state_dir = self.mission_state(name)
        self.propose(state_dir, objective="An unrelated proposal")
        return state_dir

    def during_read(self, owner, artifact_id, cursor, mutate):
        """The read with ``mutate`` run WHILE the content is read (after its
        bytes are in hand), the owner not refreshed: (answer, reads)."""
        reads = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            reads.append(args)
            result = real(*args)
            mutate()
            return result
        cached = owner._corroborating_answer()
        with mock.patch.object(state_artifact, "read_state_artifact", spy):
            answer = self.read(owner, artifact_id, cursor)
        self.assertIs(owner._corroborating_answer(), cached,
                      "the owner was not refreshed")
        return answer, reads

    def assert_discarded(self, answer, reads, problem):
        self.assert_refused(answer, problem)
        self.assertIsNone(answer["content"])
        self.assertEqual(len(reads), 1, "the content was read, then dropped")
        self.assertIn("while the file was read", answer["detail"])

    def repoint(self, source_id, **changes):
        """Re-register ``source_id`` with ``changes`` over its CURRENT
        registration (supported operations: unregister, then register)."""
        [current] = [x for x in self.registry.sources()
                     if x["source_id"] == source_id]
        fields = {"kind": current["kind"], "store": current["store_dir"],
                  "producer": current["producer"],
                  "max_age": current["max_age_seconds"]}
        fields.update(changes)
        self.registry.unregister(source_id)
        self.register(source_id=source_id, **fields)

    def test_a_source_registered_after_the_listing_is_unverifiable(self):
        owner, cursor = self.listed_owner()
        late = self.unrelated_store("mc-late")
        self.register(source_id="mc-late", store=late)
        cached = owner._corroborating_answer()
        answer = self.unread(owner, self.deliverables["result"], cursor,
                             artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)
        self.assertIs(owner._corroborating_answer(), cached)
        self.assertIn("refresh", answer["detail"])
        # Until REFRESHED: once listed, the new source is part of the
        # universe uniqueness is shown over, and the read stands.
        from target_runtime import catalog
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        _, identity = self.listed_completed(owner)
        listed = owner._corroborating_answer()["response"]["sources"]
        self.assertIn("mc-late", [b["source_id"] for b in listed])
        answer = self.read(owner, self.deliverables["result"],
                           identity["cursor_sha256"])
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])

    def test_a_listed_source_removed_before_the_request_is_unverifiable(self):
        self.register(source_id="mc-other",
                      store=self.unrelated_store("mc-other"))
        owner, cursor = self.listed_owner()
        self.registry.unregister("mc-other")
        self.unread(owner, self.deliverables["result"], cursor,
                    artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)

    def test_a_source_registered_during_the_read_discards(self):
        owner, cursor = self.listed_owner()
        late = self.unrelated_store("mc-late")
        answer, reads = self.during_read(
            owner, self.deliverables["result"], cursor,
            lambda: self.register(source_id="mc-late", store=late))
        self.assert_discarded(answer, reads,
                              artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)

    def test_another_listed_source_removed_during_the_read_discards(self):
        self.register(source_id="mc-other",
                      store=self.unrelated_store("mc-other"))
        owner, cursor = self.listed_owner()
        answer, reads = self.during_read(
            owner, self.deliverables["result"], cursor,
            lambda: self.registry.unregister("mc-other"))
        self.assert_discarded(answer, reads,
                              artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)

    def test_the_missions_source_removed_during_the_read_discards(self):
        owner, cursor = self.listed_owner()
        answer, reads = self.during_read(
            owner, self.deliverables["result"], cursor,
            lambda: self.registry.unregister(self.source_id))
        self.assert_discarded(answer, reads,
                              artifact_content.PROBLEM_SOURCE_NOT_REGISTERED)

    def test_the_missions_source_repointed_during_the_read_discards(self):
        owner, cursor = self.listed_owner()
        # The SAME records at another location: still not the listed source.
        elsewhere = os.path.join(self.root, "stores", "elsewhere")
        shutil.copytree(self.state_dir, elsewhere)
        os.chmod(elsewhere, 0o700)
        answer, reads = self.during_read(
            owner, self.deliverables["result"], cursor,
            lambda: self.repoint(self.source_id, store=elsewhere))
        self.assert_discarded(answer, reads,
                              artifact_content.PROBLEM_REGISTRATION_CHANGED)

    def test_an_observing_sources_registration_change_discards(self):
        owner, cursor = self.listed_owner()
        answer, reads = self.during_read(
            owner, self.deliverables["result"], cursor,
            lambda: self.repoint("herd", max_age=120))
        self.assert_discarded(answer, reads,
                              artifact_content.PROBLEM_REGISTRATION_CHANGED)

    def test_an_observing_source_removed_during_the_read_discards(self):
        owner, cursor = self.listed_owner()
        answer, reads = self.during_read(
            owner, self.deliverables["result"], cursor,
            lambda: self.registry.unregister("herd"))
        self.assert_discarded(answer, reads,
                              artifact_content.PROBLEM_REGISTRATION_CHANGED)

    def test_an_unchanged_registry_releases_the_content(self):
        owner, cursor = self.listed_owner()
        answer, reads = self.during_read(
            owner, self.deliverables["result"], cursor, lambda: None)
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])
        self.assertEqual(len(reads), 1)


class ActiveRegistrationRevalidationTests(ArtifactFixture):
    """Round 17 (F1) on the ACTIVE path too: a bound Mission's read is
    released only while its source is still registered exactly as it was
    when the read was admitted (re-read from the real registry; the owner
    not refreshed). Uniqueness is the binding's, so another source changing
    is not this read's concern."""

    during_read = RegistrationRevalidationTests.during_read
    assert_discarded = RegistrationRevalidationTests.assert_discarded
    repoint = RegistrationRevalidationTests.repoint

    def setUp(self):
        super(ActiveRegistrationRevalidationTests, self).setUp()
        self.put("reports/result.txt", TEXT)
        self.artifact_id = self.record("reports/result.txt")

    def test_the_bound_missions_source_removed_during_the_read(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        answer, reads = self.during_read(
            owner, self.artifact_id, cursor,
            lambda: self.registry.unregister(self.source_id))
        self.assert_discarded(answer, reads,
                              artifact_content.PROBLEM_SOURCE_NOT_REGISTERED)

    def test_the_bound_missions_source_repointed_during_the_read(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        elsewhere = os.path.join(self.root, "stores", "elsewhere")
        shutil.copytree(self.state_dir, elsewhere)
        os.chmod(elsewhere, 0o700)
        answer, reads = self.during_read(
            owner, self.artifact_id, cursor,
            lambda: self.repoint(self.source_id, store=elsewhere))
        self.assert_discarded(answer, reads,
                              artifact_content.PROBLEM_REGISTRATION_CHANGED)

    def test_another_source_registered_meanwhile_is_not_its_concern(self):
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        other = self.mission_state("mc-other")
        self.propose(other, objective="An unrelated proposal")
        answer, reads = self.during_read(
            owner, self.artifact_id, cursor,
            lambda: self.register(source_id="mc-other", store=other))
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED,
                         answer["detail"])


EXPORT_HISTORICAL_FILE = "artifact-content-historical-v1.FIXTURE.json"


def execution_digests(state_dir, source, temp_root, journals):
    """``{exported digest: normalized digest}`` for every ESTABLISHED
    execution identity and historical cursor in the fixture's store: the
    PRODUCT's own derivation (``mission_state.execution_identity`` /
    ``historical_cursor``) over the store's records and the source's
    registration, then over the same with the root mapped (``journals``: the
    journal digest map, old to new)."""
    import test_herdr_task_source as hts_tests
    from target_runtime import mission_state
    with open(os.path.join(state_dir, "missions.json")) as handle:
        raw = json.load(handle)

    def mapped(value):
        return hts_tests._map_strings(value, lambda v: v.replace(
            temp_root, hts_tests.SYNTHETIC_FIXTURE_ROOT))
    from mission import journal
    found = {}
    for mission_id, mission in raw["missions"].items():
        old = mission_state.execution_identity(source, mission)
        if old["state"] != v2.EXECUTION_IDENTITY_ESTABLISHED:
            continue
        moved = mapped(mission)
        # The prepared binding's digest is itself derived from the (mapped)
        # paths: the product's own derivation over the mapped binding.
        from mission import record as mission_record
        binding = moved["run"]["workspace"]
        binding["binding_digest_sha256"] = (
            mission_record.workspace_binding_digest(
                moved["mission_id"], binding["path_realpath"],
                binding["repository_realpath"],
                binding["target_repository_url"], binding["revision"],
                binding["proposal_digest_sha256"],
                binding["baseline_commit_sha"]))
        new = mission_state.execution_identity(mapped(source), moved)
        if new["state"] != v2.EXECUTION_IDENTITY_ESTABLISHED:
            raise AssertionError("the mapped identity is not established: %s"
                                 % new["problem"])
        head = journal.head_cursor(mission, raw["mission_state"].get(
            mission_id))["journal_digest_sha256"]
        found[old["identity_sha256"]] = new["identity_sha256"]
        found[mission_state.historical_cursor(head, old["identity_sha256"])] = (
            mission_state.historical_cursor(journals[head],
                                            new["identity_sha256"]))
    return found


class HistoricalExportFixture(CompletedFixture):
    """ONE REAL COMPLETED Mission (normal Mission Core APIs, no live team),
    OBSERVED through the real task-native producer at its own root, and the
    REAL owner's answers to content reads of it: the office consumer's
    historical fixture. Same D1 conventions as the bound export."""

    observe = SameExecutionObservationTests.observe

    def build_export(self):
        self.put("reports/private-log.txt", PRIVATE_LOG)
        undeclared = self.record("reports/private-log.txt", PRIVATE_LOG,
                                 key="session_notes")
        with open(os.path.join(self.workspace, ".herd", "state",
                               "task.json"), "rb") as handle:
            task = handle.read()
        private = self.record(".herd/state/task.json", task, key="test_output")
        self.put("reports/changed.txt", TEXT)
        changed = self.record("reports/changed.txt", key="changed")
        self.complete(also_named=[private, changed])
        self.put("reports/changed.txt", TEXT + b"edited after recording\n")
        self.observe()
        # The Pod's herd root REGISTERED with the owner, as deployed (a
        # completed Mission's root is never discovered from an active one):
        # it holds a COMPLETE task and no runtime agents — no live team.
        owner = self.owner(version=2, roots=[self.workspace])
        answer = owner.request(5)
        self.assertIsNotNone(answer["response"], answer["detail"])
        canonical = [m for m in answer["response"]["missions"]
                     if m["mission_ref"] == self.mission_ref][0]
        cursor = canonical["mission_state"][v2.EXECUTION_IDENTITY_KEY][
            "cursor_sha256"]
        journal_digest = canonical["mission_state"]["report"]["provenance"][
            "journal_digest_sha256"]
        ids = {"admitted_text": self.deliverables["result"],
               "admitted_html": self.deliverables["page"],
               "refused_class_not_declared": undeclared,
               "refused_private_location": private,
               "refused_digest_mismatch": changed}
        content = dict((case, owner.artifact_content(
            self.mission_ref, artifact_id, cursor))
            for case, artifact_id in sorted(ids.items()))
        # The bare journal digest is not a completed Mission's cursor.
        content["refused_cursor_not_listed"] = owner.artifact_content(
            self.mission_ref, self.deliverables["result"], journal_digest)
        import test_herdr_task_source as hts_tests
        temp_root = os.path.realpath(self.root)
        self.digests = hts_tests.journal_digests(self.state_dir, temp_root)
        source = [x for x in self.registry.load()["sources"]
                  if x["source_id"] == self.source_id][0]
        self.execution = execution_digests(self.state_dir, source, temp_root,
                                           self.digests)
        return {"catalog_answer": answer, "content_answers": content}, \
            temp_root

    def normalized(self, raw, temp_root):
        import test_herdr_task_source as hts_tests
        text = json.dumps(raw)
        occurrences = text.count(temp_root)
        normalized, report = hts_tests.normalize_export(
            raw, temp_root, digests=dict(self.digests, **self.execution))
        return dict(normalized, fixture="artifact-content-historical-v1",
                    normalization={
                        "transformation": (
                            "machine-specific temporary root (/var/folders/"
                            "<a>/<b>/T/<dir>, as its /private realpath)"
                            " replaced by %s; Mission Core journal digests,"
                            " execution identity digests and historical"
                            " cursors recomputed by the product over the same"
                            " records and registration with the root mapped;"
                            " no other byte of the emission changed. Byte"
                            " counts (accounting.charged_bytes) are as emitted"
                            " over the real paths. Fresh exports are not"
                            " byte-reproducible (random Mission Core ids)."
                            % hts_tests.SYNTHETIC_FIXTURE_ROOT),
                        "synthetic_root": hts_tests.SYNTHETIC_FIXTURE_ROOT,
                        "temporary_roots": 1,
                        "occurrences": occurrences,
                        "journal_digests_recomputed": report["journal_digests"],
                        "execution_digests_recomputed": len(self.execution)})


class NormalizedHistoricalExportIsCoherent(HistoricalExportFixture):

    def test_the_historical_export_is_portable_and_coherent(self):
        import test_herdr_task_source as hts_tests
        raw, temp_root = self.build_export()
        fixture = self.normalized(raw, temp_root)
        again = self.normalized(json.loads(json.dumps(raw)), temp_root)
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(fixture, sort_keys=True))
        text = json.dumps(fixture)
        for leak in (temp_root, SECRET.decode().strip(),
                     PRIVATE_LOG.decode().strip()):
            self.assertNotIn(leak, text)
        self.assertIsNone(REAL_TEMP_ROOT.search(text))
        v2.validate_response_v2(fixture["catalog_answer"]["response"])
        missions = fixture["catalog_answer"]["response"]["missions"]
        [canonical] = [m for m in missions
                       if m["lifecycle"]["model"] != "herdr.task/1"]
        [native] = [m for m in missions
                    if m["lifecycle"]["model"] == "herdr.task/1"]
        self.assertEqual(
            (native["execution"]["task_id"],
             native["execution"]["workspace_realpath"]),
            (canonical["execution"]["task_id"],
             canonical["execution"]["workspace_realpath"]))
        identity = canonical["mission_state"][v2.EXECUTION_IDENTITY_KEY]
        self.assertEqual(identity["state"], v2.EXECUTION_IDENTITY_ESTABLISHED)
        self.assertEqual(canonical["members"], [])
        # Recomputed coherently: the identity and cursor are the product's
        # over the NORMALIZED records, and every answer names that cursor.
        self.assertEqual(fixture["normalization"][
            "execution_digests_recomputed"], 2)
        self.assertNotIn(identity["identity_sha256"], json.dumps(raw))
        answers = fixture["content_answers"]
        for case, answer in answers.items():
            with self.subTest(case):
                self.assertEqual(answer["state"], case.split("_")[0])
                if case != "refused_cursor_not_listed":
                    self.assertEqual(answer["cursor"],
                                     identity["cursor_sha256"])
        self.assertEqual(
            dict((case, a["problem"]) for case, a in answers.items()
                 if a["state"] == "refused"),
            {"refused_class_not_declared":
                 artifact_content.PROBLEM_CLASS_NOT_DECLARED,
             "refused_private_location":
                 artifact_content.PROBLEM_PRIVATE_LOCATION,
             "refused_digest_mismatch":
                 artifact_content.PROBLEM_DIGEST_MISMATCH,
             "refused_cursor_not_listed":
                 artifact_content.PROBLEM_CURSOR_NOT_LISTED})
        for case in ("admitted_text", "admitted_html"):
            self.assertEqual(answers[case]["artifact"]["execution_basis"],
                             artifact_content.EXECUTION_HISTORICAL)
        self.assertIn(hts_tests.SYNTHETIC_FIXTURE_ROOT, text)


@unittest.skipUnless(os.environ.get("DI_EXPORT_ARTIFACT_CONTENT"),
                     "exports the office consumer fixture only when asked")
class ExportHistoricalForConsumerTests(HistoricalExportFixture):

    def test_export(self):
        raw, temp_root = self.build_export()
        fixture = self.normalized(raw, temp_root)
        path = os.path.join(os.environ["DI_EXPORT_ARTIFACT_CONTENT"],
                            EXPORT_HISTORICAL_FILE)
        with open(path, "w") as handle:
            json.dump(fixture, handle, indent=2, sort_keys=True)
            handle.write("\n")


class ShutdownTests(ArtifactFixture):

    def test_a_shut_down_owner_reads_nothing(self):
        self.put("reports/result.txt", TEXT)
        artifact_id = self.record("reports/result.txt")
        owner = self.owner(version=2)
        cursor = self.listed(owner)
        owner.shutdown()
        self.assert_refused(self.read(owner, artifact_id, cursor),
                            artifact_content.PROBLEM_SHUT_DOWN)


if __name__ == "__main__":
    unittest.main()
