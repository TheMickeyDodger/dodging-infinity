"""Item 3 (original 20) — the live code review through the REAL catalog owner
over a REAL running Mission, and the ``/code_review`` route.

``test_code_review_read.py`` drives ``code_review.read`` with a synthetic
owner answer; this module is where that shape is proven to be the real one:
a genuine Mission Core record bound to a genuine task at a genuine workspace
(``test_catalog_v2.V2Fixture``), refreshed by the real owner, read through
``CatalogService.code_review`` and over the loopback adapter.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from target_runtime import catalog  # noqa: E402
from target_runtime import catalog_http  # noqa: E402
from target_runtime import code_review  # noqa: E402
from target_runtime import git_transport  # noqa: E402
import test_catalog_v2 as v2_tests  # noqa: E402
import test_code_review_git as gitfx  # noqa: E402
from test_runtime_snapshot import TASK  # noqa: E402


class RealMissionFixture(v2_tests.V2Fixture):
    """A real running Mission whose workspace is a real git repository the
    agent has been working in (the herd's private state is ignored, as the
    herd keeps it)."""

    def setUp(self):
        super(RealMissionFixture, self).setUp()
        (self.state_dir, self.source_id, self.mission_id,
         self.workspace) = self.running()
        self.mission_ref = "%s:%s" % (self.source_id, self.mission_id)
        git = lambda *args: gitfx.run_git(self.workspace, *args)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        self.put("src/app.py", "def main():\n    return 1\n")
        self.put(".gitignore", ".herd/\n")
        git("add", "src", ".gitignore")
        git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
        self.put("src/app.py", "def main():\n    return 2\n")
        self.put("notes/todo.txt", "write more tests\n")

    def put(self, relative, text):
        path = os.path.join(self.workspace, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)

    def refreshed(self, **kwargs):
        owner = self.owner(version=2, **kwargs)
        answer = owner.request(5)
        self.assertIsNotNone(answer["response"], answer["detail"])
        return owner, answer

    def mission_row(self, answer):
        return [m for m in answer["response"]["missions"]
                if m["mission_ref"] == self.mission_ref][0]


class RealOwnerTests(RealMissionFixture):

    def test_the_real_catalog_row_carries_the_fields_this_module_reads(self):
        owner, answer = self.refreshed()
        row = self.mission_row(answer)
        self.assertEqual(row["lifecycle"]["standing"], "active")
        self.assertEqual(row["binding"]["state"], "bound")
        self.assertEqual(row["binding"]["task_id"], TASK)
        execution = row["execution"]
        self.assertEqual((execution["task_id"], execution["workspace_realpath"],
                          execution["consistent"], execution["record_ambiguous"]),
                         (TASK, self.workspace, True, False))
        blocks = [b for b in answer["response"]["sources"]
                  if b["source_id"] == self.source_id]
        self.assertEqual(len(blocks), 1)
        for field in code_review._REGISTRATION_FIELDS:
            self.assertIn(field, blocks[0])

    def test_an_active_assignment_is_reviewed_end_to_end(self):
        owner, _answer = self.refreshed()
        listing = owner.code_review(self.mission_ref, TASK)
        self.assertEqual((listing["state"], listing["problem"]),
                         ("observed", None), listing["detail"])
        files = dict((f["path"], f) for f in listing["overview"]["files"])
        self.assertEqual(sorted(files), ["notes/todo.txt", "src/app.py"],
                         ".herd is ignored, never listed")
        self.assertEqual(listing["target"]["branch"], "main")
        opened = owner.code_review(self.mission_ref, TASK, listing["snapshot"],
                                   files["src/app.py"]["file_id"])
        self.assertEqual(opened["state"], "observed", opened["detail"])
        self.assertEqual([l["text"] for l in opened["file"]["hunks"][0]["lines"]],
                         ["def main():", "    return 1", "    return 2"])

    def test_a_review_never_starts_a_refresh_or_writes(self):
        owner, _answer = self.refreshed()
        before = gitfx.digest(gitfx.git_state(self.workspace))
        store_before = open(os.path.join(self.state_dir, "missions.json"),
                            "rb").read() if os.path.exists(os.path.join(
                                self.state_dir, "missions.json")) else None
        with mock.patch.object(owner, "request",
                               side_effect=AssertionError("refreshed")):
            listing = owner.code_review(self.mission_ref, TASK)
            for entry in listing["overview"]["files"]:
                owner.code_review(self.mission_ref, TASK, listing["snapshot"],
                                  entry["file_id"])
        self.assertEqual(gitfx.digest(gitfx.git_state(self.workspace)), before)
        if store_before is not None:
            self.assertEqual(open(os.path.join(self.state_dir, "missions.json"),
                                  "rb").read(), store_before)

    def test_before_any_refresh_there_is_nothing_to_corroborate(self):
        owner = self.owner(version=2)
        reply = owner.code_review(self.mission_ref, TASK)
        self.assertEqual((reply["state"], reply["problem"]),
                         ("refused", code_review.PROBLEM_NOT_CORROBORATED))

    def test_the_task_the_client_was_shown_must_still_be_the_missions(self):
        owner, _answer = self.refreshed()
        reply = owner.code_review(self.mission_ref, "20261014-000000-bbbbbb")
        self.assertEqual(reply["problem"], code_review.PROBLEM_TASK_CHANGED)

    def test_a_removed_registration_is_seen_without_a_refresh(self):
        owner, _answer = self.refreshed()
        self.registry.unregister(self.source_id)
        reply = owner.code_review(self.mission_ref, TASK)
        self.assertEqual(reply["problem"], code_review.PROBLEM_SOURCE_NOT_REGISTERED)

    def test_a_mission_the_catalog_does_not_list_is_refused(self):
        owner, _answer = self.refreshed()
        reply = owner.code_review("mc1:mn-" + "f" * 32, TASK)
        self.assertEqual(reply["problem"], code_review.PROBLEM_MISSION_NOT_LISTED)

    def test_a_version_1_owner_corroborates_no_workspace(self):
        owner = self.owner()
        owner.request(5)
        reply = owner.code_review(self.mission_ref, TASK)
        self.assertEqual(reply["problem"], code_review.PROBLEM_CONTRACT_VERSION)

    def test_a_shut_down_owner_takes_no_new_work(self):
        owner, _answer = self.refreshed()
        owner.shutdown()
        reply = owner.code_review(self.mission_ref, TASK)
        self.assertEqual(reply["problem"], code_review.PROBLEM_SHUT_DOWN)

    def test_one_review_read_at_a_time(self):
        owner, _answer = self.refreshed()
        self.assertTrue(owner._review_slot.acquire(False))
        try:
            reply = owner.code_review(self.mission_ref, TASK)
            self.assertEqual(reply["problem"], code_review.PROBLEM_BUSY)
        finally:
            owner._review_slot.release()
        self.assertEqual(owner.code_review(self.mission_ref, TASK)["state"],
                         "observed", "the slot is released")

    def test_the_slot_is_released_when_a_read_fails(self):
        owner, _answer = self.refreshed()
        with mock.patch.object(code_review, "read",
                               side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                owner.code_review(self.mission_ref, TASK)
        self.assertEqual(owner.code_review(self.mission_ref, TASK)["state"],
                         "observed")

    def test_the_git_boundary_is_constructor_injected(self):
        class Fake(object):
            def __getattr__(self, name):
                def call(*args, **kwargs):
                    raise git_transport.GitTransportError("injected")
                return call
        owner = catalog.CatalogService(
            self.registry.directory, self.clock, None, self.plan(),
            monotonic=self.ticks, discover=True, contract_version=2,
            git_transport=Fake())
        self.addCleanup(owner.shutdown)
        self.assertIsInstance(owner._git_transport, Fake)
        self.assertIsNone(self.owner(version=2)._git_transport,
                          "the real boundary is built only when first asked")

    def test_the_real_boundary_is_built_without_arguments_on_first_use(self):
        owner, _answer = self.refreshed()
        self.assertIsNone(owner._git_transport)
        owner.code_review(self.mission_ref, TASK)
        self.assertIsInstance(owner._git_transport, git_transport.GitTransport)


class RouteTests(RealMissionFixture):

    def get(self, server, **query):
        path = "/code_review?" + urlencode(query)
        return self.request(server, path)

    def serve_owner(self):
        owner, _answer = self.refreshed()
        return owner, self.serve(owner)

    def test_the_overview_and_a_file_over_the_loopback_adapter(self):
        owner, server = self.serve_owner()
        status, headers, body = self.get(server, mission_ref=self.mission_ref,
                                         task_id=TASK)
        self.assertEqual(status, 200)
        self.assertEqual(self.header(headers, "Content-Type"),
                         catalog_http.CONTENT_TYPE)
        listing = json.loads(body)
        self.assertEqual(listing["state"], "observed", listing["detail"])
        self.assertEqual(set(listing), set(code_review.ANSWER_KEYS))
        entry = [f for f in listing["overview"]["files"]
                 if f["path"] == "src/app.py"][0]
        status, _headers, body = self.get(
            server, mission_ref=self.mission_ref, task_id=TASK,
            snapshot=listing["snapshot"], file_id=entry["file_id"])
        self.assertEqual(status, 200)
        opened = json.loads(body)
        self.assertEqual((opened["state"], opened["file"]["path"]),
                         ("observed", "src/app.py"))

    def test_a_refusal_is_still_a_200_with_its_own_code(self):
        owner, server = self.serve_owner()
        status, _h, body = self.get(server, mission_ref=self.mission_ref,
                                    task_id="20261014-000000-bbbbbb")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["problem"],
                         code_review.PROBLEM_TASK_CHANGED)

    def test_any_other_query_is_a_400_that_reaches_no_owner(self):
        owner, server = self.serve_owner()
        calls = []
        owner.code_review = lambda *a, **k: calls.append(a)
        good = dict(mission_ref=self.mission_ref, task_id=TASK)
        snapshot, file_id = "a" * 64, "rf-" + "b" * 32
        bad = [
            "",
            urlencode(dict(good, extra="1")),
            urlencode({"mission_ref": self.mission_ref}),
            urlencode({"task_id": TASK}),
            urlencode(dict(good, snapshot=snapshot)),
            urlencode(dict(good, file_id=file_id)),
            urlencode(dict(good, mission_ref="../../etc/passwd")),
            urlencode(dict(good, task_id="not-a-task")),
            urlencode(dict(good, snapshot="xyz", file_id=file_id)),
            urlencode(dict(good, snapshot=snapshot, file_id="rf-short")),
            urlencode(dict(good, path="/etc/passwd")),
            urlencode(dict(good, ref="HEAD~1")),
            urlencode(dict(good, git_option="--output=/tmp/x")),
            urlencode(dict(good, pathspec=":(glob)**")),
            "mission_ref=%s&mission_ref=%s&task_id=%s" % (
                self.mission_ref, self.mission_ref, TASK),
            urlencode(good) + "&snapshot=%s&snapshot=%s&file_id=%s" % (
                snapshot, snapshot, file_id),
        ]
        for query in bad:
            with self.subTest(query=query):
                status, _h, _b = self.request(server,
                                              "/code_review?" + query)
                self.assertEqual(status, 400)
        self.assertEqual(calls, [])
        # a fragment is no route at all, as for every other route
        status, _h, _b = self.request(
            server, "/code_review?" + urlencode(good) + "#fragment")
        self.assertEqual(status, 404)
        self.assertEqual(calls, [])

    def test_the_route_is_get_only_and_authenticated_like_the_others(self):
        owner, server = self.serve_owner()
        path = "/code_review?" + urlencode(dict(
            mission_ref=self.mission_ref, task_id=TASK))
        self.assertEqual(self.request(server, path, auth=False)[0], 401)
        self.assertEqual(self.request(server, path, method="POST")[0], 405)
        self.assertEqual(self.request(server, path, method="PUT")[0], 405)
        self.assertEqual(self.request(server, path, method="DELETE")[0], 405)
        self.assertEqual(self.request(server, "/code_review/extra")[0], 404)
        self.assertEqual(self.request(server, "//code_review")[0], 404)

    def test_the_route_is_listed_with_the_others(self):
        self.assertIn("/code_review", catalog_http.ROUTES)
        self.assertEqual(len(catalog_http.ROUTES), 5)


# -- the office consumer's fixture: the REAL answers, exported -------------

EXPORT_FILE = "code-review-v1.FIXTURE.json"
WITHHELD_PEM_BODY_MARKER = "S3CR3T-KEY-MATERIAL-NEVER-SHOWN"
WITHHELD_ENV_BODY_MARKER = "hunter2-NEVER-SHOWN"
SYNTAX_BASE = """import { readFileSync } from 'fs';

export interface Config {
  retries: number;
  endpoint: string;
}

export function load(path: string): Config {
  const text = readFileSync(path, 'utf-8');
  return JSON.parse(text) as Config;
}

// -- helpers kept apart so a change above and a change below do not share a hunk --
// 1. each helper below is unchanged
// 2. they only exist to put distance between the edits
// 3. git groups edits closer than seven lines into one hunk
// 4. so this file shows more than one
// 5. and the reader can collapse each on its own
// 6. the next line is the last of the filler
// 7. filler
// 8. filler
// 9. filler
// 10. filler

export function describe(config: Config): string {
  return config.endpoint + ' x' + config.retries;
}

export const DEFAULT_RETRIES = 3;
"""
SYNTAX_CHANGED = """import { readFileSync } from 'fs';

export interface Config {
  retries: number;
  endpoint: string;
  // seconds before a request is abandoned
  timeoutSeconds: number;
}

export function load(path: string): Config {
  const text = readFileSync(path, 'utf-8');
  const config = JSON.parse(text) as Config;
  if (config.retries < 0 || config.retries > 10) {
    throw new RangeError(`retries out of range: ${config.retries}`);
  }
  return config;
}

// -- helpers kept apart so a change above and a change below do not share a hunk --
// 1. each helper below is unchanged
// 2. they only exist to put distance between the edits
// 3. git groups edits closer than seven lines into one hunk
// 4. so this file shows more than one
// 5. and the reader can collapse each on its own
// 6. the next line is the last of the filler
// 7. filler
// 8. filler
// 9. filler
// 10. filler

export function describe(config: Config): string {
  return `${config.endpoint} x${config.retries} (${config.timeoutSeconds}s)`;
}

export const DEFAULT_RETRIES = 5; /* raised after the incident */
export const DEFAULT_TIMEOUT_SECONDS = 30;
"""
CSS_BASE = ".panel {\n  color: #202020;\n  padding: 8px;\n}\n"
CSS_CHANGED = ".panel {\n  color: #101010;\n  padding: 12px;\n  border: 1px solid #909090;\n}\n"
LARGE_BASE = "".join("row %d\n" % n for n in range(100))
LARGE_CHANGED = "".join("row %d changed\n" % n for n in range(7000))
LONG_BASE = "const a = 1;\n"
LONG_CHANGED = "const payload = '" + "0123456789" * 500 + "';\n"
EXPORT_EXTRAS = {
    "src/syntax_sample.ts": (SYNTAX_BASE, SYNTAX_CHANGED),
    "styles/theme.css": (CSS_BASE, CSS_CHANGED),
    "data/large.txt": (LARGE_BASE, LARGE_CHANGED),
    "web/long_line.js": (LONG_BASE, LONG_CHANGED),
    "secrets/server.pem": ("-----BEGIN-----\nold\n", "-----BEGIN-----\n"
                           + WITHHELD_PEM_BODY_MARKER + "\n"),
}


class ReviewExportFixture(v2_tests.V2Fixture):
    """One REAL bound Mission whose workspace is a git repository in every
    state the review shows: staged, unstaged, both, deleted, renamed, binary,
    empty, untracked (text, over the reader's bound, not UTF-8), withheld, a
    very large change, a very long line, a multi-hunk source file."""

    def setUp(self):
        super(ReviewExportFixture, self).setUp()
        (self.state_dir, self.source_id, self.mission_id,
         self.workspace) = self.running()
        self.mission_ref = "%s:%s" % (self.source_id, self.mission_id)
        # a tracked submodule (part of the base commit), whose recorded commit
        # then moves, with work inside it that is never read; and an untracked
        # nested repository
        origin = gitfx.Repo(self.root, "vendor-lib-origin")
        origin.write("a.txt", "a\n")
        origin.stage("a.txt")
        origin.save("origin base")
        self.repo = gitfx.Repo.adopt(self.workspace)
        self.repo.git("-c", "protocol.file.allow=always", "submodule", "add",
                      "-q", origin.path, "vendor/lib")
        gitfx.busy_workspace(self.repo, EXPORT_EXTRAS)
        inner = gitfx.Repo.__new__(gitfx.Repo)
        inner.path = os.path.join(self.workspace, "vendor", "lib")
        inner.git("config", "user.email", "t@example.com")
        inner.git("config", "user.name", "T")
        inner.write("b.txt", "b\n")
        inner.stage("b.txt")
        inner.save("the submodule moved")
        inner.write("dirty.txt", "uncommitted inside the submodule\n")
        nested = os.path.join(self.workspace, "vendor", "clone")
        os.makedirs(nested)
        clone = gitfx.Repo.adopt(nested)
        clone.write("c.txt", "c\n")
        clone.stage("c.txt")
        clone.save("nested base")
        self.repo.write("docs/new-guide.md", "# New guide\n\nWrite it.\n")
        self.repo.write("generated/bundle.min.js", "x" * 70001)
        self.repo.write("legacy/latin1.txt", b"caf\xe9\n")
        self.repo.write(".env", WITHHELD_ENV_BODY_MARKER + "\n")

    def build_export(self):
        """``(raw, temp_root)``: the REAL answers, before normalization."""
        import test_herdr_task_source as hts_tests
        owner = self.owner(version=2)
        catalog_answer = owner.request(5)
        self.assertIsNotNone(catalog_answer["response"],
                             catalog_answer["detail"])
        overview = owner.code_review(self.mission_ref, TASK)
        self.assertEqual(overview["state"], "observed", overview["detail"])
        files = {}
        for entry in overview["overview"]["files"]:
            answer = owner.code_review(self.mission_ref, TASK,
                                       overview["snapshot"], entry["file_id"])
            self.assertEqual(answer["state"], "observed", answer["detail"])
            files[entry["path"]] = answer
        refusals = {
            "assignment_task_changed": owner.code_review(
                self.mission_ref, "20261014-000000-bbbbbb"),
            "file_not_in_snapshot": owner.code_review(
                self.mission_ref, TASK, overview["snapshot"],
                "rf-" + "0" * 32),
        }
        some_file = overview["overview"]["files"][0]["file_id"]
        self.repo.write("src/appeared_later.py", "later = True\n")
        refusals["snapshot_moved"] = owner.code_review(
            self.mission_ref, TASK, overview["snapshot"], some_file)
        temp_root = os.path.realpath(self.root)
        digests = hts_tests.journal_digests(self.state_dir, temp_root)
        raw = {"catalog_answer": catalog_answer, "overview": overview,
               "files": files, "refusals": refusals}
        return raw, temp_root, digests

    def normalized(self, raw, temp_root, digests):
        """The D1 conventions (``test_herdr_task_source.normalize_export``),
        then the worktree's display name (a temporary directory's random
        name) made constant."""
        import test_herdr_task_source as hts_tests
        text = json.dumps(raw)
        occurrences = text.count(temp_root)
        normalized, report = hts_tests.normalize_export(
            raw, temp_root, digests=digests)
        worktree = os.path.join(os.path.basename(os.path.dirname(
            self.workspace)), os.path.basename(self.workspace))
        encoded = json.dumps(normalized)
        self.assertIn(json.dumps(worktree), encoded)
        normalized = json.loads(encoded.replace(
            json.dumps(worktree), json.dumps("di-fixture-root/workspace")))
        return dict(normalized, fixture="code-review-v1", normalization={
            "transformation": (
                "machine-specific temporary root (/var/folders/<a>/<b>/T/"
                "<dir>, as its /private realpath) replaced by %s; Mission"
                " Core journal digests recomputed by the product over the"
                " same records with the root mapped; each target.worktree"
                " (a random temporary directory's name and its child) made"
                " the constant di-fixture-root/workspace; no other byte of"
                " the emission changed. Snapshots, file ids and git object"
                " ids are as emitted (they hash this run's paths and"
                " times). Fresh exports are not byte-reproducible."
                % hts_tests.SYNTHETIC_FIXTURE_ROOT),
            "synthetic_root": hts_tests.SYNTHETIC_FIXTURE_ROOT,
            "temporary_roots": 1, "occurrences": occurrences,
            "journal_digests_recomputed": report["journal_digests"]})


class NormalizedReviewExportIsCoherent(ReviewExportFixture):

    def test_the_export_is_portable_coherent_and_leaks_nothing(self):
        raw, temp_root, digests = self.build_export()
        fixture = self.normalized(raw, temp_root, digests)
        again = self.normalized(json.loads(json.dumps(raw)), temp_root,
                                digests)
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(fixture, sort_keys=True))
        text = json.dumps(fixture)
        payload = json.dumps(dict((k, fixture[k]) for k in (
            "catalog_answer", "overview", "files", "refusals")))
        for leak in (temp_root, WITHHELD_PEM_BODY_MARKER, WITHHELD_ENV_BODY_MARKER):
            self.assertNotIn(leak, text)
        self.assertNotIn("/var/folders/", payload)
        # The record keeps the literal pattern text: a placeholder, not a path.
        self.assertIn("/var/folders/<a>/<b>/T/<dir>",
                      fixture["normalization"]["transformation"])
        listing = fixture["overview"]
        self.assertEqual(listing["target"]["worktree"],
                         "di-fixture-root/workspace")
        states = dict((f["path"], f["state"])
                      for f in listing["overview"]["files"])
        self.assertEqual(states["secrets/server.pem"], "withheld")
        self.assertEqual(states[".env"], "withheld")
        self.assertEqual(states["generated/bundle.min.js"], "oversized")
        self.assertEqual(states["legacy/latin1.txt"], "not_utf8")
        self.assertEqual(states["bin/blob.bin"], "binary")
        self.assertEqual(states["vendor/lib"], "submodule")
        self.assertEqual(states["vendor/clone"], "unsupported")
        self.assertNotIn("vendor/lib/dirty.txt", states)
        pointer = fixture["files"]["vendor/lib"]["file"]
        self.assertEqual([l["kind"] for h in pointer["hunks"]
                          for l in h["lines"]], ["del", "add"])
        self.assertEqual(set(fixture["files"]), set(states))
        self.assertTrue(fixture["files"]["data/large.txt"]["file"]["truncated"])
        self.assertEqual(
            sorted(fixture["refusals"]),
            ["assignment_task_changed", "file_not_in_snapshot",
             "snapshot_moved"])
        for name, answer in fixture["refusals"].items():
            self.assertEqual((answer["state"], answer["problem"]),
                             ("refused", name))


@unittest.skipUnless(os.environ.get("DI_EXPORT_CODE_REVIEW"),
                     "exports the office consumer fixture only when asked")
class ExportForConsumerTests(ReviewExportFixture):

    def test_export(self):
        raw, temp_root, digests = self.build_export()
        fixture = self.normalized(raw, temp_root, digests)
        path = os.path.join(os.environ["DI_EXPORT_CODE_REVIEW"], EXPORT_FILE)
        with open(path, "w") as handle:
            json.dump(fixture, handle, indent=2, sort_keys=True)
            handle.write("\n")



if __name__ == "__main__":
    unittest.main()
