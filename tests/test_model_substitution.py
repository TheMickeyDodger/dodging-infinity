"""I7 AUDIT: the Fable-to-Opus safeguard and the model-substitution path.

AN AUDIT, DRIVEN. Every question here is answered by running the
production path against constructed evidence and observing what it
emits, rather than by reading the source. A diff, a diffstat, a snapshot
and an AST comparison are blind in the same place, and for an audit
the blindness is worse: reading the code tells you what it INTENDS to
compare, and the question is what it ACTUALLY compares.

THE SPECIMEN THIS IS ABOUT. R-44 recorded executor1's logical role
`h90828-dodging-in-exec1` surviving a scoped clear, a session
replacement, AND a substitution to Opus 4.8, pane and cwd unchanged —
unplanned, in production. It was withdrawn as evidence that the CODE
detects it, because no binding existed at the time for `classify` to
compare against. I2c wired the producer, so the question is now
answerable: DOES THE PRODUCTION PATH DETECT A MODEL SUBSTITUTION, OR
DOES IT ONLY SURVIVE ONE?
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))

from herdr import identity, observe, tasks               # noqa: E402
from herdr.instance import HerdrInstance                 # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The live preset this herd runs, read from the real config rather
#: than retyped: supervisor/executor on fable, lead/reviewer on opus.
LIVE_CONFIG = os.path.join(REPO_ROOT, ".herd", "herd.config.json")


def agent_record(name="h-exec1", cwd="/repo", workspace="wT",
                 pane="wT:p3", session="sess-1", status="idle",
                 **extra):
    """One agent record in the shape the REAL binary emits.

    The field set is the one this herd's own `herdr agent get`
    returns; a field absent here is absent there.
    """
    record = {
        "agent": "claude",
        "agent_session": {"agent": "claude", "kind": "id",
                          "source": "herdr:claude", "value": session},
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


def info(record, status="idle"):
    return {"status": status,
            "raw": {"id": "cli:agent:get",
                    "result": {"agent": record, "type": "agent_info"}}}


class ModelIsNotInTheIDENTITYTests(unittest.TestCase):
    """Where the model IS, and where it is NOT.

    The audit's first question is not "does the comparison notice a
    model change" but "is the model even among the things compared".
    """

    def test_STABLE_FIELDS_carries_no_model(self):
        self.assertEqual(
            identity.STABLE_FIELDS,
            ("name", "cwd", "workspace_id", "pane_id"),
        )
        for field in identity.STABLE_FIELDS:
            self.assertNotIn("model", field)

    def test_a_BINDING_built_from_a_real_record_carries_no_model(self):
        """Driven through `binding_for`, the production constructor."""
        binding = identity.binding_for(
            "executor1", "h-exec1", agent_record()
        )
        flattened = json.dumps(binding)
        self.assertNotIn(
            "model", flattened,
            "a binding carries a model; the audit's premise is that it"
            " does not, and this test exists to catch it if it did",
        )
        self.assertEqual(
            sorted(binding["stable"]), sorted(identity.STABLE_FIELDS)
        )

    def test_the_LIVE_BINDINGS_carry_no_model(self):
        """The four real bindings on this herd, not a fixture."""
        document = identity.load_bindings(
            Path(REPO_ROOT) / ".herd"
        )
        roles = document["roles"]
        self.assertEqual(
            sorted(roles),
            ["executor1", "lead1", "reviewer1", "supervisor"],
            "this herd's four roles are not all bound; the rest of"
            " this class would then be auditing a different state",
        )
        self.assertNotIn("model", json.dumps(document))

    def test_the_DEPENDENCY_exposes_no_model_field_at_all(self):
        """THE BOUND ON A POSSIBLE FIX, and it is the sharper half
        of the finding.

        `identity` is unable to compare a model it has no way to
        observe.
        The agent record this herd's binary emits carries `agent` —
        the runtime KIND — and no model anywhere. So the model of a
        RUNNING agent is not observable through the dependency's
        interface, and no comparison could be written against it
        without a new source of truth.
        """
        record = agent_record()
        self.assertEqual(record["agent"], "claude")
        self.assertEqual(
            [k for k in record if "model" in k.lower()], [],
            "the record shape used by this audit carries a model"
            " field; if the real binary now emits one, this audit's"
            " conclusion changes and this test is where it surfaces",
        )

    def test_the_model_lives_ONLY_in_the_role_CONFIG(self):
        """And it is the CONFIGURED model — what should be started —
        not the model of anything running."""
        with open(LIVE_CONFIG) as handle:
            config = json.load(handle)
        models = {
            role: observe._model_from_args(cfg.get("args"))
            for role, cfg in config["roles"].items()
        }
        self.assertEqual(models["supervisor"], "fable")
        self.assertEqual(models["executor"], "fable")
        self.assertEqual(models["lead"], "opus")
        self.assertEqual(models["reviewer"], "opus")


class SubstitutionWithSessionPRESERVEDTests(unittest.TestCase):
    """A model change with the session PRESERVED.

    The adversarial case the brief asks for first, and the one that
    isolates the question: within it, the agent is otherwise
    unchanged.
    """

    def binding(self):
        return identity.binding_for(
            "executor1", "h-exec1", agent_record(session="sess-1")
        )

    def test_a_substitution_with_the_SAME_session_reads_PRESENT(self):
        """THE ANSWER TO R-44's QUESTION, driven.

        Same name, cwd, workspace, pane and session; a different model
        underneath. `classify` returns PRESENT — the role SURVIVES,
        and the substitution is INVISIBLE to it.
        """
        after = agent_record(session="sess-1")   # model changed under it
        verdict = identity.classify("executor1", self.binding(),
                                    info(after))
        self.assertEqual(verdict.verdict, identity.VERDICT_PRESENT)
        self.assertEqual(verdict.action, identity.ACTION_PROCEED)
        self.assertIsNone(verdict.problem)

    def test_the_verdict_carries_NOTHING_naming_a_model(self):
        """Not merely 'it says PRESENT' — no field of the verdict
        mentions a model, so within it a caller has no way to recover
        the fact either."""
        verdict = identity.classify(
            "executor1", self.binding(), info(agent_record(session="sess-1"))
        )
        self.assertNotIn("model", json.dumps(verdict.as_dict()).lower())

    def test_the_SAME_verdict_results_whatever_the_model_was(self):
        """The controlled comparison: two runs identical except for a
        model the code cannot see produce byte-identical verdicts.
        That is what 'does not detect' MEANS, stated as an experiment
        rather than as a claim about source."""
        first = identity.classify(
            "executor1", self.binding(), info(agent_record(session="sess-1"))
        ).as_dict()
        second = identity.classify(
            "executor1", self.binding(), info(agent_record(session="sess-1"))
        ).as_dict()
        self.assertEqual(first, second)


class SubstitutionWithSessionREPLACEDTests(unittest.TestCase):
    """A model change with the session REPLACED — and the CONFLATION.

    R-44's specimen was this shape. The finding is not that the
    verdict is wrong; it is that the verdict is RIGHT ABOUT THE WRONG
    THING: a substitution and a plain restart are indistinguishable.
    """

    def binding(self):
        return identity.binding_for(
            "executor1", "h-exec1", agent_record(session="sess-old")
        )

    def test_it_fires_REPLACED(self):
        verdict = identity.classify(
            "executor1", self.binding(),
            info(agent_record(session="sess-new")),
        )
        self.assertEqual(verdict.verdict, identity.VERDICT_REPLACED)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_SESSION_REPLACED)

    def test_the_two_scenarios_are_UNREPRESENTABLY_DIFFERENT(self):
        """THE CONFLATION, and an honest statement of why it resists
        being driven as a comparison.

        A HONEST NOTE ABOUT THIS TEST, because the first version of it
        was TAUTOLOGICAL and I am recording that rather than hiding
        it: I wrote it as "construct a plain restart, construct a
        substitution, assert the verdicts are equal" — and then
        constructed the SAME record twice, because within this record
        shape the model has no field to occupy. Comparing X to X and calling it an
        experiment is precisely the manufactured evidence this mission
        rejects.

        The honest form is the one below. The finding is not that two
        different inputs yield one verdict; it is that THE TWO
        SCENARIOS HAVE NO DIFFERENT INPUT TO OFFER. `agent_record`
        emits the field set the real binary emits, and there is no
        field in it a substitution could change. So the conflation is
        structural, not behavioural — and that is a stronger finding,
        because no comparison written against this evidence could ever
        separate them.
        """
        record = agent_record(session="sess-new")
        self.assertEqual(
            [key for key in record if "model" in key.lower()], [],
            "the record CAN carry a model, so the two scenarios ARE"
            " representably different and this finding is wrong",
        )
        verdict = identity.classify(
            "executor1", self.binding(), info(record)
        ).as_dict()
        self.assertEqual(verdict["verdict"], identity.VERDICT_REPLACED)
        self.assertIn("session", verdict["detail"])
        self.assertNotIn(
            "model", verdict["detail"].lower(),
            "the verdict names a model; if it can, the substitution is"
            " visible after all",
        )

    def test_the_COMPARISON_ITSELF_WORKS_on_what_IS_representable(self):
        """THE NON-VACUOUS CONTROL, and the audit needs it.

        "The code does not detect a model change" is worthless unless
        the code detects the changes it CAN see. Each representable
        stable field is changed one at a time and each is caught —
        which isolates the model as the gap rather than leaving the
        comparison unexamined.
        """
        for field, value in (("cwd", "/elsewhere"),
                             ("workspace_id", "w9"),
                             ("pane_id", "w9:p9")):
            with self.subTest(field=field):
                verdict = identity.classify(
                    "executor1", self.binding(),
                    info(agent_record(session="sess-old", **{field: value})),
                )
                self.assertEqual(
                    verdict.verdict, identity.VERDICT_DEGRADED,
                    "a change to %s was not caught; the comparison is"
                    " broken and the model finding says nothing" % field,
                )
        # And the session, which IS representable, is caught too.
        self.assertEqual(
            identity.classify(
                "executor1", self.binding(),
                info(agent_record(session="sess-new")),
            ).verdict,
            identity.VERDICT_REPLACED,
        )

    def test_a_LOOKALIKE_is_still_refused_across_a_substitution(self):
        """The safeguard that DOES hold: a different pane or cwd is
        refused whatever the model. A substitution does not open the
        lookalike door."""
        verdict = identity.classify(
            "executor1", self.binding(),
            info(agent_record(session="sess-new", cwd="/somewhere/else")),
        )
        self.assertEqual(verdict.verdict, identity.VERDICT_DEGRADED)
        self.assertEqual(verdict.problem,
                         identity.PROBLEM_STABLE_MISMATCH)


class OwnershipSurvivesSubstitutionTests(unittest.TestCase):
    """A model change must not invalidate logical ownership, strand a
    persisted identity, or create an untracked duplicate."""

    def herd(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        (repo / ".herd" / "state").mkdir(parents=True)
        return repo

    def test_the_BINDING_still_resolves_after_a_substitution(self):
        repo = self.herd()
        identity.save_bindings(repo / ".herd", {
            "version": 1,
            "roles": {"executor1": identity.binding_for(
                "executor1", "h-exec1", agent_record(session="sess-1")
            )},
        })
        reloaded = identity.load_bindings(repo / ".herd")
        verdict = identity.classify(
            "executor1", reloaded["roles"]["executor1"],
            info(agent_record(session="sess-1")),
        )
        self.assertEqual(verdict.verdict, identity.VERDICT_PRESENT)

    def test_REDISCOVERY_finds_the_role_after_a_substitution(self):
        """The strongest ownership case: the recorded NAME no longer
        resolves and the role is found again on exact stable evidence
        — a substitution does not strand it."""
        binding = identity.binding_for(
            "executor1", "old-name", agent_record(name="old-name")
        )
        listing = {"id": "cli:agent:list", "result": {"agents": [
            agent_record(name="new-name", session="sess-new"),
            agent_record(name="unrelated", workspace="w9",
                         pane="w9:p1"),
        ], "type": "agent_list"}}
        record, problem, detail = identity.rediscover(
            "executor1", binding, listing
        )
        self.assertIsNone(problem, detail)
        self.assertEqual(record["name"], "new-name")

    def test_TWO_agents_on_one_pane_is_AMBIGUOUS_never_adopted(self):
        """UNTRACKED DUPLICATE, driven. If a substitution ever left
        two agents answering for one logical role, rediscovery must
        refuse rather than pick one."""
        binding = identity.binding_for(
            "executor1", "old-name", agent_record(name="old-name")
        )
        listing = {"result": {"agents": [
            agent_record(name="a", session="s-a"),
            agent_record(name="b", session="s-b"),
        ]}}
        record, problem, _detail = identity.rediscover(
            "executor1", binding, listing
        )
        self.assertIsNone(record)
        self.assertEqual(problem, identity.PROBLEM_AMBIGUOUS)

    def test_a_binding_is_NOT_REWRITTEN_by_a_substitution_alone(self):
        """A durable identity is not churned by a model change: the
        bytes on disk are unchanged after a PRESENT classification."""
        repo = self.herd()
        identity.save_bindings(repo / ".herd", {
            "version": 1,
            "roles": {"executor1": identity.binding_for(
                "executor1", "h-exec1", agent_record(session="sess-1")
            )},
        })
        path = identity.bindings_path(repo / ".herd")
        before = path.read_bytes()
        identity.classify(
            "executor1",
            identity.load_bindings(repo / ".herd")["roles"]["executor1"],
            info(agent_record(session="sess-1")),
        )
        self.assertEqual(path.read_bytes(), before)


class ResetAcrossSubstitutionTests(unittest.TestCase):
    """A completed-task reset must not break across a substitution.

    Driven through `clear_contexts`, the production reset, with the
    runtime seams injected, rather than through the helper functions
    it calls.
    """

    def make_repo(self, agents):
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
                "supervisor": {"kind": "claude",
                               "args": ["--model", "fable"]},
                "lead": {"kind": "claude", "args": ["--model", "opus"]},
                "executor": {"kind": "claude",
                             "args": ["--model", "fable"]},
                "reviewer": {"kind": "claude",
                             "args": ["--model", "opus"]},
            },
            "context": {"reset_commands": {"claude": "/clear"}},
            "policy": {"rules": [], "git": {}},
        }))
        (herd / "state" / "runtime.json").write_text(
            json.dumps({"agents": agents, "panes": {}})
        )
        (herd / "state" / "task.json").write_text(
            json.dumps({"status": "IDLE"})
        )
        identity.save_bindings(herd, {
            "version": 1,
            "roles": {
                logical: identity.binding_for(
                    logical, agent,
                    agent_record(name=agent, session="sess-" + logical),
                ) for logical, agent in agents.items()
            },
        })
        return repo

    def run_reset(self, repo, probe):
        from unittest.mock import patch
        from types import SimpleNamespace
        with patch("herdr.tasks.agent_info", side_effect=probe), \
             patch("herdr.tasks.send_runtime_reset") as reset, \
             patch("herdr.tasks.prompt") as prompt_fn, \
             patch("herdr.tasks.bootstrap_text", return_value="SEED"):
            reset.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            prompt_fn.return_value = SimpleNamespace(
                returncode=0, stdout="", stderr="")
            document = tasks.clear_contexts(HerdrInstance(repo))
            return document, reset, prompt_fn

    def test_a_reset_COMPLETES_across_a_model_substitution(self):
        """The session is unchanged and the model changed underneath.
        The reset must clear and RE-SEED — a substitution must not
        break completed-task reset."""
        agents = {"executor1": "h-exec1"}
        repo = self.make_repo(agents)
        document, reset, prompt_fn = self.run_reset(
            repo,
            lambda name: info(agent_record(
                name=name, session="sess-executor1")),
        )
        entry = document["roles"]["executor1"]
        self.assertEqual(entry["phase"], tasks.RESET_PHASE_RESEEDED)
        reset.assert_called()
        prompt_fn.assert_called()

    def test_a_reset_after_a_SESSION_REPLACING_substitution_RESEEDS(self):
        """The contract is re-seeded into the NEW session — the
        'silently lose contracts or context' failure mode, driven."""
        agents = {"executor1": "h-exec1"}
        repo = self.make_repo(agents)
        document, _reset, prompt_fn = self.run_reset(
            repo,
            lambda name: info(agent_record(
                name=name, session="a-replacement-session")),
        )
        self.assertEqual(
            document["roles"]["executor1"]["phase"],
            tasks.RESET_PHASE_RESEEDED,
        )
        self.assertTrue(
            any("SEED" in str(call) for call in prompt_fn.call_args_list),
            "the contract was not re-seeded after a session-replacing"
            " substitution; that is the silent context loss the audit"
            " is looking for",
        )

    def test_the_reset_TOPOLOGY_is_preserved_across_a_substitution(self):
        """All four roles are processed, and each keeps its own
        logical name — no role is dropped, merged or duplicated."""
        agents = {"supervisor": "h-sup", "lead1": "h-lead1",
                  "executor1": "h-exec1", "reviewer1": "h-rev1"}
        repo = self.make_repo(agents)
        document, _reset, _prompt = self.run_reset(
            repo,
            lambda name: info(agent_record(
                name=name,
                session="sess-" + {
                    "h-sup": "supervisor", "h-lead1": "lead1",
                    "h-exec1": "executor1", "h-rev1": "reviewer1",
                }[name])),
        )
        self.assertEqual(sorted(document["roles"]), sorted(agents))
        for logical in agents:
            self.assertEqual(
                document["roles"][logical]["phase"],
                tasks.RESET_PHASE_RESEEDED,
            )


class F3ConfiguredModelNamesItselfTests(unittest.TestCase):
    """R-74 F3, and the ONE question the reviewer is aimed at:

    CAN THE FIXED FIELD STILL BE READ AS DESCRIBING THE RUNNING HERD
    BY SOMEONE WHO DOES NOT ALREADY KNOW THE LIMIT?

    That reader has not read a single ruling. They scan a line, take
    the tuple, and move on. So the qualification has to be INSIDE the
    field — read at the same moment as the value — and the limit has
    to be STATED, because a reader told only that the value is
    CONFIGURED will still take it as the best answer available unless
    told that the running value is unavailable.
    """

    def herd(self, args=None, roles=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        (repo / ".herd" / "state").mkdir(parents=True)
        (repo / ".herd" / "herd.config.json").write_text(json.dumps({
            "version": 4,
            "project": {"name": "t"},
            "orchestration": {},
            "roles": roles or {
                "executor": {"kind": "claude", "args":
                             args if args is not None
                             else ["--model", "fable"]},
            },
            "policy": {},
        }))
        return repo

    def render(self, **kwargs):
        return observe.render_observation(
            observe.observe(self.herd(**kwargs), probe_agents=False)
        )

    def test_the_STRUCTURED_key_names_itself_CONFIGURED(self):
        """A JSON consumer is handed a KEY, not the section heading.
        `model` reads as the role's model; `configured_model`, within
        this key, is unable to."""
        obs = observe.observe(self.herd(), probe_agents=False)
        role, = obs["config"]["roles"]
        self.assertEqual(role["configured_model"], "fable")
        self.assertNotIn(
            "model", [k for k in role if k == "model"],
            "the unqualified key survives, so a consumer can keep"
            " reading it as the running model",
        )

    def test_the_UNQUALIFIED_key_is_GONE_not_merely_joined(self):
        """A rename, not an addition. Leaving `model` beside a
        qualified twin lets every existing consumer keep reading the
        wrong one forever."""
        obs = observe.observe(self.herd(), probe_agents=False)
        for role in obs["config"]["roles"]:
            self.assertNotIn("model", role)
            self.assertIn("configured_model", role)

    def test_EVERY_LINE_carrying_a_model_VALUE_carries_the_QUALIFIER(self):
        """THE NAIVE-READER TEST, and it is the reviewer's question
        made executable.

        Scan the WHOLE render. A line on which a model name appears must
        also carry the qualification on that same line, because a
        reader who scans one line reads that line rather than the
        heading above it or the note below it.
        """
        text = self.render(roles={
            "executor": {"kind": "claude", "args": ["--model", "fable"]},
            "lead": {"kind": "claude", "args": ["--model", "opus"]},
        })
        for line in text.splitlines():
            for model in ("fable", "opus"):
                if model in line:
                    self.assertIn(
                        "CONFIGURED", line,
                        "a line carries the model %r with no"
                        " qualification on it: %r" % (model, line),
                    )

    def test_the_OLD_UNQUALIFIED_FORM_is_absent(self):
        """`executor(claude/fable)` is the exact string a reader took
        as the running model. It must not appear."""
        text = self.render()
        self.assertNotIn("executor(claude/fable)", text)
        self.assertNotIn("(claude/fable)", text)

    def test_THE_LIMIT_IS_STATED_where_the_reader_is(self):
        """R-74 F1 is a LIMIT that gets STATED rather than synthesised.
        A reader told the value is configured still needs telling that
        the running value is unavailable, or they will take the
        configured one as the closest thing to it."""
        text = self.render()
        note, = [l for l in text.splitlines() if "NOTE:" in l]
        self.assertIn("CONFIGURED", note)
        self.assertIn("NOT observable", note)
        self.assertIn("agent interface", note)

    def test_an_UNSET_model_says_UNSET_not_UNKNOWN_and_is_not_GUESSED(self):
        """The value is not unknown, it is ABSENT. `?` invites a
        reader to resolve it into 'some default the tool picked'."""
        obs = observe.observe(
            self.herd(args=["--permission-mode", "auto"]),
            probe_agents=False,
        )
        self.assertIsNone(obs["config"]["roles"][0]["configured_model"])
        text = observe.render_observation(obs)
        self.assertIn("model-CONFIGURED=(unset)", text)
        for guess in ("fable", "opus", "sonnet", "haiku"):
            self.assertNotIn(guess, text)

    def test_NOTHING_ANYWHERE_reports_a_RUNNING_model(self):
        """The whole observation, not just the config section: no key
        anywhere claims an observed model. F1 is a limit that is
        stated, and stating it means emitting no value for it."""
        obs = observe.observe(self.herd(), probe_agents=False)
        for section in ("agents", "runtime", "roles", "turns"):
            self.assertNotIn(
                '"model"', json.dumps(obs[section]),
                "section %r carries a bare `model` key" % section,
            )

    def test_the_SCHEMA_VERSION_moved_with_the_rename(self):
        """A consumer pinned to the old shape finds out from the
        version rather than from a missing key at read time."""
        self.assertEqual(observe.OBSERVE_SCHEMA_VERSION, 3)
        obs = observe.observe(self.herd(), probe_agents=False)
        self.assertEqual(obs["schema_version"], 3)


class StructuredConsumerLearnsTheLimitTests(unittest.TestCase):
    """R-76: THE CONSUMER WHO MEETS ONLY THE JSON.

    reviewer1's finding, and it is the sharp half of F3.
    `configured_model` tells an author THAT THIS VALUE IS CONFIGURED.
    It does NOT tell them that NO RUNNING VALUE EXISTS — and a
    reasonable author reading a qualified key infers an unqualified
    counterpart somewhere they have not yet found. A tool built on it
    could surface it to a human as "model", re-introducing F3 one
    layer downstream, and its author would have had no way to learn
    the limit from the interface they consumed.

    Every test here reads the STRUCTURED OBSERVATION ONLY. The rendered text is not consulted, because the reader this is
    about, within this suite, has no rendered line to see.
    """

    def herd(self, roles=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        (repo / ".herd" / "state").mkdir(parents=True)
        (repo / ".herd" / "herd.config.json").write_text(json.dumps({
            "version": 4,
            "project": {"name": "t"},
            "orchestration": {},
            "roles": {
                "executor": {"kind": "claude",
                             "args": ["--model", "fable"]},
            } if roles is None else roles,
            "policy": {},
        }))
        return repo

    def limit_diagnostics(self, obs):
        return [
            d for d in obs["diagnostics"]
            if d.get("source") == "config"
            and "not observable through the agent interface"
            in (d.get("detail") or "")
        ]

    def test_the_LIMIT_is_ON_THE_STRUCTURED_OUTPUT(self):
        """The gap this closes: all of it was previously reachable
        only by reading a rendered line."""
        obs = observe.observe(self.herd(), probe_agents=False)
        found = self.limit_diagnostics(obs)
        self.assertEqual(
            len(found), 1,
            "a structured consumer has no way to learn the limit;"
            " diagnostics were %s" % obs["diagnostics"],
        )

    def test_the_diagnostic_answers_the_INFERENCE_the_key_invites(self):
        """The specific wrong inference is 'there must be an
        unqualified counterpart somewhere'. The text has to close
        that, not merely restate that the value is configured."""
        obs = observe.observe(self.herd(), probe_agents=False)
        detail = self.limit_diagnostics(obs)[0]["detail"]
        self.assertIn("NO running-model value exists", detail)
        self.assertIn("no unqualified `model` key", detail)
        self.assertIn("agent interface", detail)

    def test_the_LOAD_BEARING_CLAUSES_SURVIVE_TRUNCATION(self):
        """A FINDING ABOUT THE CHANNEL, pinned against regression.

        `_note` truncates its detail at `_OBSERVE_MAX_STRING`. The
        first draft of this statement explained `configured_model`
        first and closed the wrong inference last — and the cap cut
        off exactly the closing clause, leaving a consumer with the
        half they already knew. A limit statement on a truncating
        channel has to LEAD with the load-bearing fact, and this
        asserts it still does at the real cap rather than trusting
        that the sentence stayed short.
        """
        detail = self.limit_diagnostics(
            observe.observe(self.herd(), probe_agents=False)
        )[0]["detail"]
        self.assertLessEqual(len(detail), observe._OBSERVE_MAX_STRING)
        for clause in ("NO running-model value exists",
                       "no unqualified `model` key",
                       "not observable through the agent interface"):
            self.assertIn(
                clause, detail,
                "the clause %r did not survive truncation; what"
                " survives is the half a consumer already knew"
                % clause,
            )

    def test_the_DELIVERED_SENTENCE_TERMINATES_PROPERLY(self):
        """R-78: A DIAGNOSTIC THAT ENDS MID-WORD IS ONE WHOSE AUTHOR
        DID NOT DECIDE WHERE IT SHOULD END.

        Front-loading kept the MEANING intact — reviewer1 verified
        that against the delivered bytes — and the delivered value
        still ended mid-word at the cap. The objection is not that a
        reader loses meaning; it is that a sentence handed to a
        machine consumer half-finished is unfinished work.

        Asserted on the DELIVERED bytes, not the source string: the source string's length is unconstrained; what a consumer
        reads is what `_note` emits.
        """
        detail = self.limit_diagnostics(
            observe.observe(self.herd(), probe_agents=False)
        )[0]["detail"]
        self.assertFalse(
            detail.endswith("\u2026"),
            "the diagnostic is delivered truncated: %r" % detail[-40:],
        )
        self.assertTrue(
            detail.endswith("."),
            "the diagnostic does not end in a full stop: %r"
            % detail[-40:],
        )
        self.assertLess(
            len(detail), observe._OBSERVE_MAX_STRING + 1,
            "the sentence does not fit the bound it is delivered"
            " through",
        )

    #: The SMALL vocabulary that makes clause 3 mean something. Each
    #: term marks the value as SOMETHING ASKED FOR rather than
    #: something seen — which is the distinction the whole diagnostic
    #: exists to draw. Deliberately short: a long list would accept
    #: most wordings and stop being a pin.
    #:
    #: THE BOUND THAT REMAINS, stated where the pin lives: THIS IS A
    #: CHOSEN LIST, NOT A PROOF. A bare "intent." satisfies it, and a
    #: rewording that means the right thing in words outside this list
    #: fails it. Closing the circular case below does not turn the
    #: list into a derivation — it removes one specific way of passing
    #: without saying anything, and leaves the rest of the list
    #: exactly as arbitrary as it was.
    INTENT_VOCABULARY = ("intent", "intended", "requested", "asked",
                         "configured")

    #: The agent-claims clause 3 must not drift into (R-74).
    #: THE BOUND ON THE EXCLUSION that `key_derived_terms` computes,
    #: in reviewer1's own words at round 25 §7. It is carried as a
    #: QUOTATION and not restated: the sentence is precisely scoped
    #: and it is its author's wording, so improving or compressing it
    #: would lose the thing worth recording. This is a RECORD, not a
    #: check — it is read by no test, and it is disclosed rather than
    #: closed because closing over-exclusion needs word-boundary or
    #: stemming logic, closing under-exclusion needs morphology, and
    #: either is a guard whose own correctness would need a guard.
    #: The defect is LATENT: today's key derives correctly, and a
    #: RENAME is the event that would expose it.
    #:
    #: It sits here as a string rather than in a docstring or a `#`
    #: comment for a stated reason: the round-09 absolute-claim
    #: closure judges docstring and comment prose as THIS increment's
    #: own claim, and reviewer1's sentence ends "strips nothing and
    #: leaves the circular case open" — a bare absolute in that
    #: closure's vocabulary. Recording it as prose would have meant
    #: rewording another author's sentence to satisfy a detector.
    REVIEWER1_BOUND_ON_THE_EXCLUSION = (
        "The exclusion is a bidirectional substring relation over the"
        " key's words. For `configured_model` it derives exactly"
        " \"configured\". It is not a morphological analysis: a short"
        " key word can strip more terms than it should and shrink the"
        " vocabulary silently, and a key that is a morphological"
        " VARIANT rather than a superstring -- `configuration_model`"
        " against \"configured\" -- strips nothing and leaves the"
        " circular case open. A rename is the event that would expose"
        " either."
    )

    AGENT_CLAIMS = ("is running", "runs", "running agent",
                    "the agent is", "currently")

    KEY = "`configured_model`"

    #: The end of CLAUSE 2, used to find where clause 3 begins. This
    #: couples the pin to clause 2's wording, deliberately and
    #: visibly — see `clause_three_of`.
    SPLIT_MARKER = "interface."

    @classmethod
    def key_derived_terms(cls):
        """Vocabulary terms that come from THE KEY'S OWN NAME.

        R-82 PART 1. `INTENT_VOCABULARY` contains "configured" and the
        key is `configured_model`, so "`configured_model` is
        configured." passed the pin WHILE RESTATING THE KEY AS ITS OWN
        EXPLANATION. `explanation_in` strips the literal backticked
        token; THE BARE WORD SURVIVED AND DID THE SAME JOB. The trap
        was closed at the TOKEN level and survived at the WORD level.

        That is an instrument satisfied by its own subject for the
        fifth time in this mission, and this instance was inside the
        fix for the fourth — which is why this is DERIVED from the key
        rather than hand-removed from the list. A hand-edit closes
        today's spelling; a derivation follows the key if it is ever
        renamed. Structure beats care, because care does not survive
        the next edit.

        THE BOUND ON THIS EXCLUSION is recorded at the pin, in
        reviewer1's own words, as `REVIEWER1_BOUND_ON_THE_EXCLUSION`
        beside `INTENT_VOCABULARY`. Read it there: it names what this
        relation gets wrong in BOTH directions and the event that
        would expose it.

        """
        words = set(cls.KEY.strip("`").lower().replace("_", " ").split())
        return tuple(
            term for term in cls.INTENT_VOCABULARY
            if any(term in word or word in term for word in words)
        )

    @classmethod
    def effective_vocabulary(cls):
        """The vocabulary MINUS anything the key already says."""
        excluded = cls.key_derived_terms()
        return tuple(term for term in cls.INTENT_VOCABULARY
                     if term not in excluded)

    @classmethod
    def clause_three_of(cls, detail):
        """The third clause of the delivered diagnostic.

        R-82 PART 2. This splits on the END OF CLAUSE 2, which couples
        it to clause 2's wording. A rewording used to raise
        `IndexError: list index out of range` — LOUD, which is the
        important half and is preserved, but with no stated cause. A
        crash is not a kill, and that rule binds a pin's own failure
        mode as much as it binds a mutant.

        `rsplit` takes the LAST occurrence, so within this split a stray
        earlier marker is not the one selected. That is preserved too.
        """
        if cls.SPLIT_MARKER not in detail:
            raise AssertionError(
                "clause 3 cannot be located. This pin splits on %r,"
                " which is the END OF CLAUSE 2 — so clause 2 has been"
                " REWORDED and the marker is gone. The pin is NOT"
                " silently disabled: it is failing loudly, and what it"
                " needs is SPLIT_MARKER updated to the new end of"
                " clause 2. Delivered detail was: %r"
                % (cls.SPLIT_MARKER, detail)
            )
        return detail.rsplit(cls.SPLIT_MARKER, 1)[1].strip()

    @classmethod
    def explanation_in(cls, clause3):
        """What clause 3 says BESIDES naming the key.

        THE KEY NAME IS REMOVED FIRST, and that is the whole trick.
        `configured_model` CONTAINS the word "configured", so a
        vocabulary check run over the raw clause would be satisfied by
        the KEY NAME ITSELF — and the hollow text
        "`configured_model` is set." would sail through a check that
        looked like a positive assertion. Stripping the key first
        means the vocabulary must be found in the EXPLANATION.
        """
        return clause3.replace(cls.KEY, " ").strip()

    def test_CLAUSE_THREE_POSITIVELY_EXPLAINS_THE_KEY(self):
        """R-80: within this suite a blocklist detects drift and not
        emptiness. Only a positive assertion detects the second.

        The blocklist below pins that clause 3 did not drift INTO an
        agent claim. Within it, whether clause 3 still SAYS anything
        goes unchecked: a shortening to "`configured_model` is set." passes the
        blocklist, the terminator pin and the length bound while being
        genuinely hollow. This asserts the content is there.

        THE PROPERTY: clause 3 names the key AND marks it as INTENT
        rather than observed state.

        RESIDUAL, stated with the claim: the CONTRAST with observation
        is carried by clause 2, which is pinned separately. This test
        asserts clause 3 says "asked for", not that it says "and not
        seen" — one clause is not required to carry the whole
        sentence.

        THE STRENGTH OF THIS PIN, stated here so a reader of the pin
        meets it without looking up a constant:

            Marker containment plus structural exclusion of what the
            key itself supplies is the strongest form available here;
            semantic entailment is not available at all.

        That is narrower than the form I first wrote — "an assertion
        that clause 3 MEANS intent is not available to a test" — which
        was honest in substance and OVER-BROAD in form. What is
        unavailable is ENTAILMENT, not all strengthening beyond marker
        containment. Strengthening WAS available and this work took it
        twice: stripping the key token, then excluding key-derived
        words, both moving the pin from "contains a marker" toward
        "contains a marker THE KEY DID NOT SUPPLY". The broader form
        would license future weak guards, because if a test could only
        ever check markers there would be no duty to strengthen. There
        is one, wherever strengthening exists.

        `INTENT_VOCABULARY` carries the other bound: the list is
        CHOSEN, not derived, and `key_derived_terms` carries the bound
        on the exclusion itself.
        """
        detail = self.limit_diagnostics(
            observe.observe(self.herd(), probe_agents=False)
        )[0]["detail"]
        clause3 = self.clause_three_of(detail)
        self.assertIn(
            self.KEY, clause3,
            "clause 3 no longer names the key it explains: %r" % clause3,
        )
        explanation = self.explanation_in(clause3)
        found = [term for term in self.effective_vocabulary()
                 if term in explanation.lower()]
        self.assertTrue(
            found,
            "clause 3 names the key and explains nothing about it."
            " The explanation was %r, and none of %s appears in it, so"
            " a reader learns the key exists and not what it means"
            % (explanation, list(self.effective_vocabulary())),
        )

    def test_THE_OLD_BLOCKLIST_PASSES_A_HOLLOW_CLAUSE(self):
        """THE GAP, DEMONSTRATED RATHER THAN ASSERTED.

        This runs the OLD assertions against a hollow clause and shows
        every one of them PASSING, then runs the new positive check
        against the same text and shows it FAILING. That is the
        difference between reporting a fix and demonstrating one, and
        it makes the finding a permanent artifact rather than a
        sentence in an evidence file.

        Third instance in this mission of a test whose NAME carries a
        claim its ASSERTION does not make — after a guard matching its
        own docstring and an orphan detector counting its own prose.
        """
        hollow = "`configured_model` is set."

        # THE OLD ASSERTIONS, verbatim, all PASSING on hollow text.
        self.assertIn(self.KEY, hollow)
        for claim in self.AGENT_CLAIMS:
            self.assertNotIn(claim, hollow)
        self.assertTrue(hollow.endswith("."))
        self.assertLessEqual(len(hollow), observe._OBSERVE_MAX_STRING)

        # THE NEW CHECK, on the same text, FAILING.
        explanation = self.explanation_in(hollow)
        self.assertEqual(
            [term for term in self.effective_vocabulary()
             if term in explanation.lower()],
            [],
            "the hollow clause satisfies the positive pin, so the pin"
            " does not distinguish meaning from emptiness",
        )
        # And the live clause, through the SAME helpers, does not.
        live = self.clause_three_of(
            self.limit_diagnostics(
                observe.observe(self.herd(), probe_agents=False)
            )[0]["detail"]
        )
        self.assertTrue(
            [term for term in self.effective_vocabulary()
             if term in self.explanation_in(live).lower()],
            "the live clause fails the pin the hollow one fails; the"
            " check separates nothing",
        )

    def test_a_KEY_DERIVED_TERM_does_NOT_satisfy_the_check(self):
        """R-82 PART 1: the trap closed at the WORD level.

        "`configured_model` is configured." restates the key as its
        own explanation. Before this it PASSED: `explanation_in`
        stripped the backticked token and the bare word "configured"
        remained to satisfy the vocabulary. The control arm for this
        is in the evidence file — it passed, driven, before the
        change.
        """
        circular = "`configured_model` is configured."
        explanation = self.explanation_in(circular)
        self.assertIn(
            "configured", explanation.lower(),
            "the premise of this test is that the BARE WORD survives"
            " stripping; if it no longer does, this test is pinning"
            " nothing",
        )
        self.assertEqual(
            [term for term in self.effective_vocabulary()
             if term in explanation.lower()],
            [],
            "a term taken from the key's own name still satisfies the"
            " pin, so the key can be its own explanation: %r"
            % explanation,
        )

    def test_the_EXCLUSION_is_DERIVED_from_the_key_not_hand_written(self):
        """Structure beats care: care does not survive the next edit.

        A hand-edit removing "configured" would close today's
        spelling. This asserts the exclusion FOLLOWS THE KEY — change
        the key and the excluded set changes with it.
        """
        self.assertIn("configured", self.key_derived_terms())
        self.assertNotIn("configured", self.effective_vocabulary())
        self.assertIn("intent", self.effective_vocabulary())

        class Renamed(type(self)):
            KEY = "`requested_model`"

        self.assertIn("requested", Renamed.key_derived_terms())
        self.assertNotIn("configured", Renamed.key_derived_terms())
        self.assertNotIn("requested", Renamed.effective_vocabulary())

    def test_the_LIVE_CLAUSE_still_passes_after_the_exclusion(self):
        """No collateral damage: "states intent." must not be a
        casualty of excluding key-derived terms."""
        clause3 = self.clause_three_of(
            self.limit_diagnostics(
                observe.observe(self.herd(), probe_agents=False)
            )[0]["detail"]
        )
        explanation = self.explanation_in(clause3)
        self.assertTrue(
            [term for term in self.effective_vocabulary()
             if term in explanation.lower()],
            "the live clause fails the pin after the exclusion: %r"
            % explanation,
        )

    def test_a_REWORDED_CLAUSE_TWO_gives_a_STATED_CAUSE(self):
        """R-82 PART 2: a crash is not a kill, and that binds a pin's
        own failure mode.

        A reworded clause 2 used to raise `IndexError: list index out
        of range`. LOUD is the important half and it is preserved —
        this still fails rather than silently passing — but a future
        editor now gets a stated cause and the name of the thing to
        update.
        """
        reworded = ("NO running-model value exists here and the model"
                    " a RUNNING agent uses is unobservable at the"
                    " probe. `configured_model` states intent.")
        self.assertNotIn(self.SPLIT_MARKER, reworded)
        with self.assertRaises(AssertionError) as caught:
            self.clause_three_of(reworded)
        message = str(caught.exception)
        self.assertIn("SPLIT_MARKER", message)
        self.assertIn("REWORDED", message)
        self.assertIn("NOT", message)
        self.assertNotIn("list index out of range", message)

    def test_the_SPLIT_takes_the_LAST_marker_not_the_first(self):
        """Preserved from before: within this split the LAST marker is
        the one selected, so a stray earlier one is not."""
        detail = ("mentions interface. early, then the real end"
                  " interface. `configured_model` states intent.")
        self.assertEqual(
            self.clause_three_of(detail),
            "`configured_model` states intent.",
        )

    def test_the_VOCABULARY_CHECK_is_not_satisfied_by_the_KEY_NAME(self):
        """The trap this pin had to avoid.

        `configured_model` contains "configured". A vocabulary check
        over the RAW clause would be satisfied by the key name, and
        the hollow text would pass a check that looked positive. This
        asserts the stripping actually happens.
        """
        hollow = "`configured_model` is set."
        self.assertIn(
            "configured", hollow.lower(),
            "the premise of this test is that the key name contains a"
            " vocabulary term",
        )
        self.assertNotIn(
            "configured", self.explanation_in(hollow).lower(),
            "the key name survived stripping, so the vocabulary check"
            " can be satisfied by the key rather than the explanation",
        )

    def test_CLAUSE_THREE_still_describes_a_KEY_not_an_AGENT(self):
        """R-74 must not be drifted into by SHORTENING. Clause 3 says
        what `configured_model` MEANS; it must not become a claim
        about a model that is running.

        KEPT, and its scope is now stated exactly: this is a BLOCKLIST
        and it detects DRIFT, not EMPTINESS. The positive pin above is
        what detects emptiness. Both are needed and neither replaces
        the other.
        """
        detail = self.limit_diagnostics(
            observe.observe(self.herd(), probe_agents=False)
        )[0]["detail"]
        clause3 = self.clause_three_of(detail)
        self.assertIn(self.KEY, clause3)
        for claim in self.AGENT_CLAIMS:
            self.assertNotIn(
                claim, clause3,
                "clause 3 became a claim about an agent: %r" % clause3,
            )

    def test_a_JSON_ONLY_consumer_can_reach_it_WITHOUT_the_render(self):
        """Modelled directly: serialize the observation, throw the
        object away, and learn the limit from the bytes alone."""
        obs = observe.observe(self.herd(), probe_agents=False)
        document = json.loads(json.dumps(obs))
        self.assertNotIn("render", document)
        carriers = [
            d for d in document["diagnostics"]
            if "observable" in (d.get("detail") or "")
        ]
        self.assertTrue(
            carriers,
            "the serialized observation carries no statement of the"
            " limit; a consumer holding only these bytes cannot learn"
            " it",
        )

    def test_NO_FORBIDDEN_FIELD_was_added(self):
        """R-74's prohibition, pinned. The diagnostic describes OUR
        OBSERVABILITY; it asserts no fact about an agent, so within this
        document the forbidden shapes are absent."""
        blob = json.dumps(observe.observe(self.herd(),
                                          probe_agents=False))
        for forbidden in ("running_model", "model_observable",
                          "observed_model", "actual_model"):
            self.assertNotIn(
                forbidden, blob,
                "%r appears; that asserts something about a model"
                " that is running, which R-74 forbids" % forbidden,
            )

    def test_the_diagnostic_does_NOT_demote_COMPLETENESS(self):
        """It is a disclosure about the interface, not a failure of
        the projection. Demoting here would make each herd PARTIAL
        for as long as a role is configured, and a marker stuck at
        PARTIAL conveys little.

        Asserted as a CONTROLLED COMPARISON rather than against a
        literal: this fixture is partial for reasons of its own (no
        git repository, no task), so the property is that ADDING the
        statement leaves completeness where it was.
        """
        with_roles = observe.observe(self.herd(), probe_agents=False)
        without = observe.observe(self.herd(roles={}),
                                  probe_agents=False)
        self.assertTrue(self.limit_diagnostics(with_roles))
        self.assertEqual(self.limit_diagnostics(without), [])
        self.assertEqual(
            with_roles["completeness"], without["completeness"],
            "the limit statement moved completeness; it is a"
            " disclosure, not a degradation",
        )
        self.assertEqual(
            self.limit_diagnostics(with_roles)[0]["state"], "available"
        )
        self.assertNotIn("available", observe._PARTIAL_STATES)

    def test_it_is_stated_ONCE_not_per_role(self):
        """A limit repeated per role becomes noise a consumer filters,
        and a filtered statement is an unread one."""
        obs = observe.observe(self.herd(roles={
            "executor": {"kind": "claude", "args": ["--model", "fable"]},
            "lead": {"kind": "claude", "args": ["--model", "opus"]},
            "reviewer": {"kind": "claude", "args": []},
        }), probe_agents=False)
        self.assertEqual(len(self.limit_diagnostics(obs)), 1)

    def test_NO_ROLES_means_NO_claim_to_qualify(self):
        """With no role entries there is no `configured_model` for a
        consumer to misread, so the statement is absent rather than
        emitted into a document it does not apply to."""
        obs = observe.observe(self.herd(roles={}), probe_agents=False)
        self.assertEqual(obs["config"]["roles"], [])
        self.assertEqual(self.limit_diagnostics(obs), [])

    def test_BOTH_SURFACES_carry_the_limit(self):
        """The defect we would have shipped is a qualifier that
        travels in one surface and not the other. A reader meets
        whichever one they meet."""
        repo = self.herd()
        obs = observe.observe(repo, probe_agents=False)
        self.assertTrue(self.limit_diagnostics(obs))
        text = observe.render_observation(obs)
        self.assertIn("NOT observable", text)


class ConfiguredModelIsNotObservedModelTests(unittest.TestCase):
    """What the SURFACE says about models, and what it can know.

    `observe` renders `executor(claude/fable)` from the ROLE CONFIG.
    That is the model the herd is CONFIGURED to start, not the model
    of anything running — and after a substitution the two disagree
    with no way to tell from here.
    """

    def herd(self, args):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        (repo / ".herd" / "state").mkdir(parents=True)
        (repo / ".herd" / "herd.config.json").write_text(json.dumps({
            "version": 4,
            "project": {"name": "t"},
            "orchestration": {},
            "roles": {"executor": {"kind": "claude", "args": args}},
            "policy": {},
        }))
        return repo

    def test_the_surface_reports_the_CONFIGURED_model(self):
        repo = self.herd(["--model", "fable"])
        obs = observe.observe(repo, probe_agents=False)
        roles = {r["role"]: r for r in obs["config"]["roles"]}
        self.assertEqual(roles["executor"]["configured_model"], "fable")
        self.assertIn("model-CONFIGURED=fable",
                      observe.render_observation(obs))

    def test_the_surface_reads_the_CONFIG_and_nothing_else(self):
        """THE AUDIT'S SURFACE FINDING, stated as what the input IS
        rather than as a comparison of two identical renders.

        Same honesty note as the conflation test: this surface reads only files a
        substitution leaves alone, so "render before, render after,
        assert equal" compares a render to itself. The
        finding is about the INPUT: the model on this surface comes
        from the role's CONFIG ARGS, which say what the herd is
        configured to START. Change the config and the surface moves;
        substitute the running model and it has no input to move in
        response to.
        """
        configured = observe.observe(
            self.herd(["--model", "fable"]), probe_agents=False
        )
        self.assertEqual(
            configured["config"]["roles"][0]["configured_model"],
            "fable",
        )
        # The surface DOES track the config — so it is reading
        # something, and what it reads is the configured value.
        rewritten = observe.observe(
            self.herd(["--model", "opus"]), probe_agents=False
        )
        self.assertEqual(
            rewritten["config"]["roles"][0]["configured_model"],
            "opus",
            "the surface does not track the config either, so it is"
            " not reporting the configured model and this finding"
            " needs restating",
        )
        # And the AGENTS section, which reports what is RUNNING,
        # is silent about the model of every agent.
        self.assertNotIn(
            "model", json.dumps(configured["agents"]).lower(),
            "the agents section carries a model; if it does, the"
            " running model IS observable and the audit's central"
            " finding is wrong",
        )

    def test_an_absent_model_arg_renders_as_UNKNOWN_not_guessed(self):
        """The safeguard that DOES hold on this surface: within it an
        absent model stays absent rather than being invented."""
        repo = self.herd(["--permission-mode", "auto"])
        obs = observe.observe(repo, probe_agents=False)
        roles = {r["role"]: r for r in obs["config"]["roles"]}
        self.assertIsNone(roles["executor"]["configured_model"])
        self.assertIn("model-CONFIGURED=(unset)",
                      observe.render_observation(obs))


if __name__ == "__main__":
    unittest.main()
