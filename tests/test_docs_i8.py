"""I8: THE FOUR DOCUMENTS ARE CLAIMS, AND THIS IS WHERE THEY ARE PINNED.

The normative-prose rule for this increment: prose in these documents
is a CLAIM, and each claim is either PINNED by something executable or
DISCLOSED as unpinned. A claim that is neither gets deleted rather than
softened.

THE PERMANENT ROW SET is an explicit committed expectation.  Each
protected normative unit has an independent content fingerprint and
must still exist in its real document and have a row.  This preserves
the authoring-time exhaustiveness guarantee without making a clean
checkout depend on a dirty diff, an old HEAD, or local mission state.

WHAT A ROW IS WORTH. A `doc<->code` row PARSES its document and drives
production with what it parsed, so falsifying the SENTENCE fails the
pin. A `fact-only` row names a pin that holds the fact but would stay
green if the sentence alone were falsified — it is early warning, and
it is labelled as such. A row whose pin text begins `NO PIN` is a
DISCLOSURE: the claim is recorded as unpinned rather than presented as
guarded.

Coverage is matched against BOTH maps — this one and the I1
`CLAIM_PIN_MAP` — because SECURITY.md and OPERATOR_PROTOCOL.md are
governed by the I1 map already, and a second copy of those rows would
be two maps free to disagree.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

import herdr.observe as observe
import herdr.turns as turns
from test_workspace_trust import (CLAIM_PIN_MAP, REPO_ROOT, document_units,
                                  flat)
from _di_remote2_surface import (I8_DOCUMENT_UNIT_DIGESTS,
                                 protected_document_units, unit_digest)

I8_DOCS = ("README.md", "SECURITY.md", "OPERATOR_PROTOCOL.md",
           "CHANGELOG.md")

def doc_text(document):
    with open(os.path.join(REPO_ROOT, document), encoding="utf-8") as fh:
        return fh.read()


def added_units(document):
    """Protected current normative units and missing fingerprints."""
    return protected_document_units(
        doc_text(document), I8_DOCUMENT_UNIT_DIGESTS[document],
        document_units,
    )


def unmapped_units(units, rows, exemptions=()):
    """Units not covered by an independent claim row or exemption."""
    return [
        unit for unit in units
        if not any(row in flat(unit) for row in rows)
        and not any(flat(phrase) in flat(unit)
                    for _reason, phrase in exemptions)
    ]


def herd_fixture(case, roles=None, args=None):
    """A minimal herd whose ROLE CONFIGURATION is what the model
    claims are about."""
    temp = tempfile.TemporaryDirectory()
    case.addCleanup(temp.cleanup)
    repo = Path(temp.name)
    (repo / ".herd" / "state").mkdir(parents=True)
    (repo / ".herd" / "herd.config.json").write_text(json.dumps({
        "version": 4,
        "project": {"name": "t"},
        "orchestration": {},
        "roles": roles if roles is not None else {
            "executor": {"kind": "claude",
                         "args": ["--model", "fable"]
                         if args is None else args},
        },
        "policy": {},
    }))
    return repo


def one_line(document):
    return re.sub(r"\s+", " ", doc_text(document))


# ---------------------------------------------------------------- map

I8_CLAIM_PIN_MAP = (
    # -- README.md ---------------------------------------------------
    ("README.md", "fact-only",
     "Read this before the architecture, not after it",
     "ClaimPinMapI8Tests.test_every_added_unit_has_a_row"),
    ("README.md", "doc<->code",
     "The model a RUNNING agent uses is not observable through the"
     " agent interface (F1)",
     "ModelHonestyIsProductionsTests."
     "test_readme_forbidden_field_names_are_absent_from_the_projection"),
    ("README.md", "fact-only",
     "A verdict cannot distinguish a model substitution from a restart",
     "test_model_substitution.SubstitutionWithSessionPRESERVEDTests."
     "test_the_two_scenarios_are_UNREPRESENTABLY_DIFFERENT"),
    ("README.md", "fact-only",
     "Observation is a reporting surface, not a gate",
     "test_observe.NonMutationTests.test_no_write_path_is_reachable"),
    ("README.md", "doc<->code",
     "Skew is reported, naming both the build that wrote the record"
     " and the build on disk",
     "OperatorLimitsAreProductionsTests."
     "test_operator_skew_label_is_the_one_the_render_emits"),
    ("README.md", "fact-only",
     "DI-REMOTE-2 is hermetically proven and LIVE-UNVERIFIED",
     "NO PIN — DISCLOSED AS UNPINNED. It states what the automated"
     " suite does NOT exercise; a suite cannot evidence its own"
     " absence of live traffic, and the human validation items are"
     " listed in CHANGELOG.md under LIVE-UNVERIFIED"),
    ("README.md", "doc<->code",
     "The projection is schema version 3",
     "SchemaSurfaceIsProductionsTests."
     "test_readme_schema_version_is_the_one_production_emits"),
    ("README.md", "doc<->code",
     "schema_version generated_at completeness repository config",
     "SchemaSurfaceIsProductionsTests."
     "test_readme_top_level_keys_are_the_keys_production_emits"),
    ("README.md", "doc<->code",
     "`vintage`, `checkpoint`, `roles` and `turns` arrived after"
     " schema v1",
     "SchemaSurfaceIsProductionsTests."
     "test_readme_named_new_sections_are_in_the_projection"),
    ("README.md", "doc<->code",
     "A role's model appears in the projection under the key"
     " `configured_model`",
     "ModelHonestyIsProductionsTests."
     "test_readme_model_key_is_the_key_production_emits"),
    ("README.md", "doc<->code",
     "The projection also carries the limit itself as a diagnostic",
     "ModelHonestyIsProductionsTests."
     "test_readme_quoted_diagnostic_is_the_delivered_sentence"),
    ("README.md", "doc<->code",
     "NO running-model value exists in this document",
     "ModelHonestyIsProductionsTests."
     "test_readme_quoted_diagnostic_is_the_delivered_sentence"),
    ("README.md", "doc<->code",
     "A role whose configuration names no model renders `(unset)`",
     "ModelHonestyIsProductionsTests."
     "test_readme_unset_marker_is_the_one_production_writes"),
    # -- CHANGELOG.md ------------------------------------------------
    ("CHANGELOG.md", "doc<->code",
     "Herdr observability is schema version 3",
     "SchemaSurfaceIsProductionsTests."
     "test_changelog_schema_version_is_the_one_production_emits"),
    ("CHANGELOG.md", "doc<->code",
     "The projection's role model key is `configured_model`",
     "ModelHonestyIsProductionsTests."
     "test_changelog_model_key_and_qualifier_are_productions"),
    ("CHANGELOG.md", "fact-only",
     "The managed workspace trust boundary is documented in three"
     " parts in SECURITY.md",
     "test_workspace_trust.ClaimPinMapTests."
     "test_three_part_boundary_key_is_the_key_production_writes"),
)

#: Added text that carries no contract claim, each with the reason it
#: is exempt. An exemption is a deliberate, reviewable act.
NON_NORMATIVE_EXEMPTIONS = ()


class ClaimPinMapI8Tests(unittest.TestCase):
    """The map itself: every mapped claim present, every added unit
    mapped, the labels closed, and the derivation shown to be
    non-vacuous.

    Source is the only feasible level for this class, and the reason
    is that its subject IS text: an unpinned SENTENCE is the defect,
    and a sentence has no runtime behaviour to execute. The DECISION
    does, and `test_an_unpinned_sentence_is_DETECTED` drives it. These
    checks are also fast structural feedback in front of the executed
    pins below, each of which parses its document and then drives
    production with what it parsed.
    """

    def all_rows(self):
        return tuple(I8_CLAIM_PIN_MAP) + tuple(CLAIM_PIN_MAP)

    def test_every_mapped_claim_is_present_in_its_document(self):
        """A phantom row records a claim the document does not make."""
        for document, label, claim, pin in I8_CLAIM_PIN_MAP:
            with self.subTest(claim=claim[:48]):
                self.assertIn(
                    flat(claim), flat(doc_text(document)),
                    "%s row is a phantom: the claim is not in %s"
                    % (label, document),
                )

    def test_every_added_unit_has_a_row(self):
        """THE DOCUMENT -> MAP direction. Without it, a sentence added
        tomorrow with no row is undetectable and the map silently
        stops being exhaustive."""
        rows_by_doc = {}
        for document, _label, claim, _pin in self.all_rows():
            rows_by_doc.setdefault(document, []).append(flat(claim))
        unpinned, covered = [], 0
        for document in I8_DOCS:
            units, missing = added_units(document)
            self.assertEqual(
                missing, [],
                "%s lost or changed protected normative unit(s): %s"
                % (document, missing),
            )
            self.assertTrue(
                units,
                "the committed normative domain for %s is empty"
                % document,
            )
            rows = rows_by_doc.get(document, [])
            self.assertTrue(rows, "no rows at all for %s" % document)
            gaps = unmapped_units(
                units, rows, NON_NORMATIVE_EXEMPTIONS,
            )
            covered += len(units) - len(gaps)
            unpinned.extend(
                "%s: %s" % (document, unit[:150]) for unit in gaps
            )
        self.assertEqual(
            unpinned, [],
            "UNPINNED NORMATIVE SURFACE (%d). Each needs a claim->pin"
            " row, or an exemption naming why it carries no contract"
            " claim:\n  %s" % (len(unpinned), "\n  ".join(unpinned)),
        )
        self.assertGreater(covered, 0, "vacuous: nothing was covered")

    def test_the_map_labels_are_closed(self):
        self.assertEqual(
            {row[1] for row in I8_CLAIM_PIN_MAP},
            {"doc<->code", "fact-only"},
        )

    def test_an_unpinned_sentence_is_DETECTED(self):
        """NON-VACUITY CONTROL on the exhaustiveness check itself.

        The check above passing proves the documents are covered ONLY
        if the check can fail. This drives the same decision over a
        synthetic unit that no row matches and asserts it is reported.
        """
        rows = [flat(row[2]) for row in self.all_rows()]
        invented = ("This sentence states a contract that no row in"
                    " either map covers, at all.")
        self.assertEqual(
            unmapped_units([invented], rows), [invented],
            "an invented sentence matched a row, so the matcher is"
            " loose enough to cover text nobody mapped",
        )

    def test_the_committed_domain_resists_mutation_and_self_observation(self):
        """A changed document unit disappears; prose here is not evidence."""
        document = "README.md"
        units, missing = added_units(document)
        self.assertFalse(missing)
        expected = {unit_digest(units[0])}
        mutated = units[0] + " synthetic violation"
        _found, mutation_missing = protected_document_units(
            mutated, expected, document_units,
        )
        self.assertEqual(mutation_missing, sorted(expected))

        prose_only_in_this_test = (
            "This sentence describes a protected normative condition"
            " but does not occur in the document under test."
        )
        expected = {unit_digest(prose_only_in_this_test)}
        _found, self_observation_missing = protected_document_units(
            doc_text(document), expected, document_units,
        )
        self.assertEqual(self_observation_missing, sorted(expected))

    def test_the_disclosed_rows_are_visible_as_disclosures(self):
        """A `NO PIN` row is a DISCLOSURE, and it stays labelled as
        one: the row set below is asserted so that turning a
        disclosure into a silent claim changes this test."""
        disclosed = tuple(
            row[2] for row in I8_CLAIM_PIN_MAP
            if row[3].startswith("NO PIN")
        )
        self.assertEqual(
            disclosed,
            ("DI-REMOTE-2 is hermetically proven and LIVE-UNVERIFIED",),
        )


class SchemaSurfaceIsProductionsTests(unittest.TestCase):
    """EXECUTED PIN: each test parses its document, then drives
    `observe` and asserts on the projection production actually emits.
    Falsifying the sentence fails the test."""

    def observation(self):
        return observe.observe(herd_fixture(self), probe_agents=False)

    def test_readme_schema_version_is_the_one_production_emits(self):
        match = re.search(r"The projection is schema version (\d+)",
                          one_line("README.md"))
        self.assertIsNotNone(match, "README names no schema version")
        self.assertEqual(
            int(match.group(1)), self.observation()["schema_version"],
            "README names a schema version production does not emit",
        )

    def test_changelog_schema_version_is_the_one_production_emits(self):
        match = re.search(
            r"Herdr observability is schema version (\d+)",
            one_line("CHANGELOG.md"),
        )
        self.assertIsNotNone(match, "CHANGELOG names no schema version")
        self.assertEqual(
            int(match.group(1)), self.observation()["schema_version"],
        )

    def test_readme_top_level_keys_are_the_keys_production_emits(self):
        """The document claims an ORDER, so the comparison is ordered:
        a key list that matches as a set and not as a sequence would
        still mislead a reader following it down the page."""
        block = re.search(
            r"emits them:\s*```text\n(.*?)```",
            doc_text("README.md"), re.S,
        )
        self.assertIsNotNone(block, "README lists no top-level keys")
        listed = [line.strip() for line in block.group(1).splitlines()
                  if line.strip()]
        self.assertEqual(
            listed, list(self.observation()),
            "README lists keys production does not emit in that order",
        )

    def test_readme_named_new_sections_are_in_the_projection(self):
        match = re.search(
            r"((?:`[a-z_]+`(?:, | and )?)+) arrived after schema v1",
            one_line("README.md"),
        )
        self.assertIsNotNone(match, "README names no new sections")
        named = re.findall(r"`([a-z_]+)`", match.group(1))
        self.assertTrue(named, "the sentence named no section at all")
        observation = self.observation()
        for section in named:
            with self.subTest(section=section):
                self.assertIn(
                    section, observation,
                    "README says %r arrived; the projection has no such"
                    " key" % section,
                )


class ModelHonestyIsProductionsTests(unittest.TestCase):
    """EXECUTED PIN: for F1 and F3, each test parses the model claim
    out of README or CHANGELOG and drives it against a live
    projection. Falsifying the sentence fails the test."""

    def observation(self, **kwargs):
        return observe.observe(herd_fixture(self, **kwargs),
                               probe_agents=False)

    def test_readme_model_key_is_the_key_production_emits(self):
        match = re.search(
            r"under the key `([a-z_]+)`", one_line("README.md")
        )
        self.assertIsNotNone(match, "README names no model key")
        parsed = match.group(1)
        role, = self.observation()["config"]["roles"]
        self.assertEqual(
            role.get(parsed), "fable",
            "README names key %r; the projection's role carries %r"
            % (parsed, sorted(role)),
        )
        self.assertNotIn(
            "model", list(role),
            "the unqualified key is present beside the qualified one,"
            " so a consumer can keep reading the wrong one",
        )

    def test_readme_render_qualifier_is_the_one_production_writes(self):
        match = re.search(
            r"in the human render as `([A-Za-z-]+=)`",
            one_line("README.md"),
        )
        self.assertIsNotNone(match, "README names no render qualifier")
        text = observe.render_observation(self.observation())
        self.assertIn(
            match.group(1), text,
            "README says the render says %r; it does not"
            % match.group(1),
        )

    def test_readme_forbidden_field_names_are_absent_from_the_projection(self):
        """The F1 claim, driven: the names README says are ABSENT are
        parsed out of README and looked for in the serialized
        projection.

        NON-VACUITY: a name the projection DOES carry is looked for by
        the same search, so a search too weak to find a present name
        fails here instead of passing every absence.
        """
        sentence = re.search(
            r"The projection carries no (.*?), because such a field",
            one_line("README.md"),
        )
        self.assertIsNotNone(sentence, "README states no F1 absence")
        forbidden = re.findall(r"`([a-z_]+)`", sentence.group(1))
        self.assertTrue(forbidden, "the sentence named no field")
        serialized = json.dumps(self.observation())
        for name in forbidden:
            with self.subTest(field=name):
                self.assertNotIn('"%s"' % name, serialized)
        self.assertIn(
            '"configured_model"', serialized,
            "CONTROL: the key the projection DOES carry was not found"
            " by this search, so the absences above prove little",
        )

    def test_readme_quoted_diagnostic_is_the_delivered_sentence(self):
        """README QUOTES the diagnostic. A quotation that drifts from
        what production delivers teaches the wrong sentence, so the
        quoted block is compared to the delivered detail."""
        block = re.search(
            r"reading this document:\s*```text\n(.*?)```",
            doc_text("README.md"), re.S,
        )
        self.assertIsNotNone(block, "README quotes no diagnostic")
        quoted = " ".join(block.group(1).split())
        delivered = [
            d for d in self.observation()["diagnostics"]
            if d.get("source") == "config"
            and "not observable through the agent interface"
            in (d.get("detail") or "")
        ]
        self.assertEqual(
            len(delivered), 1,
            "the projection delivered %d limit diagnostics"
            % len(delivered),
        )
        self.assertEqual(
            quoted, " ".join(delivered[0]["detail"].split()),
            "README quotes a sentence production does not deliver",
        )

    def test_readme_unset_marker_is_the_one_production_writes(self):
        match = re.search(
            r"names no model renders `(\([a-z]+\))`",
            one_line("README.md"),
        )
        self.assertIsNotNone(match, "README names no unset marker")
        text = observe.render_observation(self.observation(args=[]))
        qualifier = re.search(
            r"in the human render as `([A-Za-z-]+=)`",
            one_line("README.md"),
        )
        self.assertIsNotNone(qualifier)
        # The marker is asserted IN THE MODEL FIELD, not merely
        # somewhere in the render: `(unknown)` appears elsewhere in a
        # normal render, so a whole-text search passes for a marker
        # the model field does not use. That is how the first version
        # of this pin let a falsified README survive.
        self.assertIn(
            qualifier.group(1) + match.group(1), text,
            "README says an unconfigured model renders %r in the %r"
            " field; the render says otherwise"
            % (match.group(1), qualifier.group(1)),
        )

    def test_changelog_model_key_and_qualifier_are_productions(self):
        text = one_line("CHANGELOG.md")
        key = re.search(r"role model key is `([a-z_]+)`", text)
        qualifier = re.search(r"human render says `([A-Za-z-]+=)`", text)
        self.assertIsNotNone(key, "CHANGELOG names no model key")
        self.assertIsNotNone(qualifier, "CHANGELOG names no qualifier")
        observation = self.observation()
        role, = observation["config"]["roles"]
        self.assertEqual(role.get(key.group(1)), "fable")
        self.assertIn(qualifier.group(1),
                      observe.render_observation(observation))


class OperatorLimitsAreProductionsTests(unittest.TestCase):
    """EXECUTED PIN: for the OPERATOR_PROTOCOL limits section, each
    test parses the label an operator is told to look for and drives
    the render that is supposed to emit it. Falsifying the sentence
    fails the test."""

    CURRENT = "20260828-182050-8807ad"

    def herd_with_task(self):
        repo = herd_fixture(self)
        state = repo / ".herd" / "state"
        (state / "task.json").write_text(json.dumps(
            {"id": self.CURRENT, "status": "ACTIVE"}
        ))
        return repo

    def test_operator_model_key_is_the_key_production_emits(self):
        match = re.search(
            r"`observe` reports `([a-z_]+)`",
            one_line("OPERATOR_PROTOCOL.md"),
        )
        self.assertIsNotNone(match, "the section names no model key")
        role, = observe.observe(
            herd_fixture(self), probe_agents=False
        )["config"]["roles"]
        self.assertEqual(role.get(match.group(1)), "fable")

    def test_operator_skew_label_is_the_one_the_render_emits(self):
        """Parses the LABEL out of the document, then writes a turn
        record from another build and asserts the render carries that
        label AND BOTH BUILD NAMES — which is what the sentence
        promises the operator."""
        match = re.search(
            r"reported as ([A-Z ]+), naming both the build",
            one_line("OPERATOR_PROTOCOL.md"),
        )
        self.assertIsNotNone(match, "the section names no skew label")
        label = match.group(1).strip()
        repo = self.herd_with_task()
        stale = turns.new_turn("t-old", self.CURRENT, "lead1", now=1)
        stale["observer_build"] = "0000deadbeef"
        turns.append_turn(repo / ".herd", stale)
        observation = observe.observe(repo, probe_agents=False)
        text = observe.render_observation(observation)
        self.assertIn(label, text)
        self.assertIn("0000deadbeef", text)
        current = observation["turns"]["observer_build"]
        self.assertTrue(current, "the projection named no current build")
        self.assertIn(
            str(current), text,
            "the render names the record's build and not the build on"
            " disk, so a reader learns half the disagreement",
        )

    def test_operator_omitted_label_is_the_one_the_render_emits(self):
        """The omission branch, driven: a role with no turn recorded is
        listed under the label the document names."""
        match = re.search(
            r"is ([A-Z]+) from the turn listing",
            one_line("OPERATOR_PROTOCOL.md"),
        )
        self.assertIsNotNone(match, "the section names no omission label")
        repo = self.herd_with_task()
        (repo / ".herd" / "state" / "runtime.json").write_text(
            json.dumps({"agents": {"executor1": "h-exec1",
                                   "lead1": "h-lead1"},
                        "panes": {}})
        )
        # ONE role gets a turn; the other is the omission under test.
        turns.observe_control_roles(
            repo / ".herd", {"executor1": "h-exec1"}, self.CURRENT,
            lambda agent: {"status": "working", "raw": None},
        )
        observation = observe.observe(repo, probe_agents=False)
        self.assertIn(
            "lead1", observation["turns"]["omitted_roles"],
            "a role with no turn recorded is missing from"
            " `omitted_roles`, so its absence is invisible",
        )
        self.assertNotIn(
            "executor1", observation["turns"]["omitted_roles"],
            "CONTROL: the role that DOES have a turn was also reported"
            " omitted, so the list names roles regardless of evidence",
        )
        self.assertIn(match.group(1),
                      observe.render_observation(observation))


class BoundsAreProductionsTests(unittest.TestCase):
    """EXECUTED PIN: on the two documented bounds a consumer is most
    likely to rely on: the numbers are parsed from README, compared to
    the constants production enforces, and the string bound is driven
    through a real projection."""

    def test_readme_string_bound_is_the_one_production_enforces(self):
        match = re.search(r"(\d+)-character projected strings",
                          one_line("README.md"))
        self.assertIsNotNone(match, "README states no string bound")
        bound = int(match.group(1))
        self.assertEqual(bound, observe._OBSERVE_MAX_STRING)
        long_name = "x" * (bound + 50)
        repo = herd_fixture(self, roles={
            long_name: {"kind": "claude", "args": ["--model", "fable"]},
        })
        serialized = json.dumps(
            observe.observe(repo, probe_agents=False)
        )
        self.assertNotIn(
            long_name, serialized,
            "a string longer than the documented bound reached the"
            " projection whole",
        )

    def test_readme_probe_bound_is_the_one_production_enforces(self):
        match = re.search(r"(\d+) live agent probes",
                          one_line("README.md"))
        self.assertIsNotNone(match, "README states no probe bound")
        self.assertEqual(int(match.group(1)),
                         observe._OBSERVE_MAX_AGENT_PROBES)


if __name__ == "__main__":
    unittest.main()
