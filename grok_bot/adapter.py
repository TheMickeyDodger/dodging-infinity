"""The Grok Bot transport adapter: one method per tool, each a thin call
into an existing DI seam. It decides nothing.

Two lanes, told apart by the request_ref's family (I2 of task
20261010-164104-1761f6):

- the CONFORMING route (``gr-`` refs, ``grok_bot.conforming``) is the
  default and the only one a new ``request`` reaches. ``request`` follows
  OPERATOR_PROTOCOL.md route (b): the INITIAL Operator turn, through this
  adapter's operator session (``prepare`` then ``execute``), either
  clarifies in prose (returned quoted; nothing proposed or recorded) or
  emits the DI-REMOTE-2 routing signal (its body discarded), which triggers
  the SEPARATE fresh restrictive planning turn, through the route's own
  planning session; its answer set is exactly ``mission_authorization``.
  The shared intake seam validates the document and the trusted local seam
  stamps its bindings (workflow id, approval, origin, and the exact
  accepted text as ``human_intent``); ONE PLANNED DI-REMOTE-2 workflow is
  saved in the workflow store the Runtime claims from, and nothing goes to
  the local request surface or Mission Core. ``present``: the EXACT
  rendered Mission Authorization, its binding and its local arming
  command, the receipt durable first. ``approve``: compare, then fire,
  then apply (below), consuming the workflow's approval one-shot; only a
  DI Runtime running against that workflow store advances it. ``status`` and ``result`` are READ-ONLY
  projections of the durable record (I3 of task 20261010-164104-1761f6:
  ``conforming.WorkflowRoute.status`` / ``.result``, a lock-free snapshot
  as of the read, every claim attributed to the record). ``cancel``
  withdraws the caller's OWN still-pending request (its one-shot
  capability, or the sealed origin after a lost reply) and refuses by name
  once the approval was consumed: no running work is stopped, because
  nothing here can stop it. ``recover`` and ``run`` do not apply and are
  refused by name.
- the LEGACY lane (``lr-`` refs: Mission-Core requests made before I2)
  stays READABLE: ``status``, ``present`` and ``result`` read it (``result``
  returns a VERIFIED run's actual result and review text, recovered by the
  ONE implementation the local operator surface also runs,
  ``target_runtime.legacy_result``: only the two recorded locators, through
  the hardened reader, re-hashed; anything gone or changed is
  UNRECOVERABLE), and ``recover`` and ``cancel`` are the surface's own
  operations. Every one names the lane.
  ``present`` offers no binding and no arming command, and states the
  remedy instead. ``approve`` and ``run`` on it are refused,
  ``grok_bot_legacy_reauthorization_required``, with the remedy: send a
  request that DIFFERS (its text, or its conversation_ref), because an
  identical one is answered with the legacy request itself
  (``conforming.FRESH_REQUEST_REMEDY``). A Mission-Core proposal is never
  converted into DI-REMOTE-2 authority, and nothing is approved or started.
- Grok Bot RUNS NOTHING (I3, B-2): this adapter is built over a surface
  with NO run bridge, never calls ``run_command``, and nothing in its
  import closure reaches the dispatch path. The local operator surface
  (``direquest.py``) keeps its run commands, under the operator's own
  authority on the DI machine.

- ``approve`` on the conforming route: compare, then fire, then refuse or
  apply. The caller's binding must equal, field by field and type-exactly,
  the one last displayed for its request (``grok_bot_not_presented`` /
  ``grok_bot_binding_not_displayed`` otherwise, naming the fields; an
  absent field differs). The relayed reply must be an exact affirmative.
  The record must still carry that binding and still await its first
  decision. Then the approval must have been ARMED by the local arming
  command on the DI machine (``grok_bot.arming``): the caller's
  ``approval_code`` must reproduce the stored one-way commitment over
  exactly the displayed binding, and an armed approval fires once
  (``grok_bot_approval_not_armed`` / ``_code_mismatch`` / ``_consumed``).
  Its consumption is durable BEFORE anything is applied. Nothing is filled
  in or corrected from the receipt. Nothing reachable over MCP can arm:
  only the local ``grokbot.py ... authorize-workflow`` command writes such
  a commitment (``grok_bot.authorize``, never imported here).
- ``recover`` and ``cancel`` on the legacy lane: the surface's own
  operations. ``run`` keeps its argument-shape checks (a malformed call is
  still ``grok_bot_bad_request``), then refuses every command by name.
- ``cancel`` after a LOST ``request`` reply (task d9e17d): instead of the
  control capability, the originating conversation restates its origin
  (the request's exact ``text`` and ``conversation_ref``). The adapter
  unseals the capability it sealed under that origin (``grok_bot.index``)
  and hands it to the surface's own ``cancel``, whose preconditions and
  refusal codes are unchanged: an added gate, never a removed one. A proof
  binds exactly one request_ref; a request_ref or Mission id alone is never
  enough. Interrupted local persistence
  (``grok_bot_cancel_recovery_interrupted``) and a record with no
  recoverable capability (``grok_bot_cancel_recovery_unavailable``) are
  refused as what they are;
  no capability is ever fabricated, and none is ever returned except in
  the ``request`` reply itself.

- ``present_delivery``, ``approve_delivery``, ``delivery_status`` (slice
  3): the SEPARATE delivery ceremony, relayed to pr_delivery's own
  ``present-dots`` and ``attest-dots`` (``grok_bot.delivery``), with the
  same complete display and compare-then-refuse receipt discipline as a
  Mission proposal, and the same local arming (``authorize-delivery``)
  before ``approve_delivery`` fires. pr_delivery's ceremony alone records a
  delivery authorization; nothing here performs a step of it.

Every result is the surface's (or the ceremony's) result, labelled with
``transport`` (``grok_bot``, a transport only), ``delivery_authority``
("none": what THIS transport grants, always nothing) and the relay's
``evidence_status`` (``operator_attestation_only_not_sender_evidence``).
The adapter cannot grant delivery authority itself: it never mints, never
performs a step, and never constructs a delivery transport DIRECTLY
(construction is core-owned, by pr_delivery's own ``build_machine()``; see
``grok_bot.delivery``). It has no merge, release, deploy or step-performing
tool, and an engineering approval stays an engineering approval (it never
reaches the delivery ceremony).
"""

import json

from codex_gateway import contract as gateway_contract
from local_request import store as request_store
from local_request import surface as surface_module
from workflow_authority.digest import json_digest, text_digest

from grok_bot import arming
from grok_bot import conforming
from grok_bot import framing
from grok_bot import index as index_module

TRANSPORT = framing.TRANSPORT
DELIVERY_AUTHORITY = surface_module.DELIVERY_AUTHORITY
EVIDENCE_STATUS = request_store.EVIDENCE_STATUS

PROBLEM_BAD_REQUEST = "grok_bot_bad_request"
PROBLEM_UNKNOWN_TOOL = "grok_bot_unknown_tool"
PROBLEM_UNKNOWN_FIELD = "grok_bot_unknown_field"
PROBLEM_OPERATOR_FAILED = "grok_bot_operator_failed"
PROBLEM_IN_FLIGHT = "grok_bot_request_in_flight"
PROBLEM_NOT_PRESENTED = "grok_bot_not_presented"
PROBLEM_NOT_DISPLAYED = "grok_bot_binding_not_displayed"
# The request tool continues NO Operator session.
PROBLEM_OPERATOR_SESSION_REFUSED = "grok_bot_operator_session_refused"
# Cancel recovery after a lost request reply (task d9e17d).
PROBLEM_ORIGIN_MISMATCH = "grok_bot_cancel_origin_mismatch"
PROBLEM_RECOVERY_INTERRUPTED = "grok_bot_cancel_recovery_interrupted"
PROBLEM_RECOVERY_UNAVAILABLE = "grok_bot_cancel_recovery_unavailable"
MAX_REF_CHARS = 128

RECOVERY_KEPT = (
    "kept sealed under this request's exact text and conversation_ref: if"
    " this reply is lost, cancel with the request_ref, that text and that"
    " conversation_ref instead of the capability (repeat the identical"
    " request to read the request_ref again)")
RECOVERY_NOT_KEPT = (
    "not kept: no recovery material is held for this request (it is kept"
    " only for a request that carries a conversation_ref), so this"
    " capability is the only way to cancel this pending proposal")

LIMITATION = (
    "Approval through Grok Bot is an operator-attested relay of a separate"
    " plain-text reply. It is not cryptographically authenticated: Grok Bot"
    " gives DI no signed sender attribution, so DI does not establish who"
    " sent the reply.")

APPROVAL_FIELDS = (
    "request_ref", "mission_id", "revision", "proposal_digest_sha256",
    "approved_action_scope", "approved_delivery_targets", "expires_at",
    "relayed_reply", "relay_ref",
)
# I2: the conforming route's binding adds exactly these two fields; the
# Mission-Core-only fields below are never part of it.
WORKFLOW_ONLY_FIELDS = ("workflow_id", "authorization_digest_sha256")
LEGACY_ONLY_FIELDS = ("mission_id", "proposal_digest_sha256",
                      "approved_action_scope", "approved_delivery_targets")
# The approve tool's arguments: either lane's binding, the relayed reply,
# and the one-time code the local arming printed.
APPROVE_ARGUMENTS = APPROVAL_FIELDS + WORKFLOW_ONLY_FIELDS + ("approval_code",)
# The arguments each run command's bridge operation takes, exactly, with
# the type each must have (None: any value; the bridge validates it).
RUN_ARGUMENTS = {"dispatch": {},
                 "verify": {"reported_result": None},
                 "prove": {"operation": str, "arguments": dict}}
# The OPTIONAL arguments, typed the same way (Task 8 final). An ordinary
# dispatch takes none: DI prepares the Mission's own isolated workspace.
# ``workspace_path`` is OPERATOR RECOVERY only: it may name nothing but that
# same, already bound workspace, under every same check; never needed
# otherwise, and never a way to bind another path.
RUN_OPTIONAL_ARGUMENTS = {"dispatch": {"workspace_path": str}}
# I3 (B-2): Grok Bot RUNS NOTHING, and each legacy run command gets the
# advice that is true for it (every surface run command in exactly one
# group, pinned by test): ``result`` is answered by the read-only result;
# ``observe`` points at the recorded reads; pausing, resuming or cancelling
# running work points at the operator's local surface (a new request would
# not affect it); and dispatching or advancing work needs a new
# authorization.
LEGACY_RUN_READ = ("result",)
LEGACY_RUN_OBSERVE = ("observe",)
LEGACY_RUN_CONTROL = ("pause", "resume", "cancel")
LEGACY_RUN_REAUTHORIZE = ("dispatch", "reconcile", "verify", "prove")
PROBLEM_LEGACY_RESULT_UNAVAILABLE = "grok_bot_legacy_result_unavailable"
# Slice 3, the SEPARATE delivery ceremony: exactly pr_delivery present-dots'
# own arguments (pinned by test against its parser), as
# (name, kind, required, default when absent).
DELIVERY_PRESENT_FIELDS = (
    ("repo", "path", True, None),
    ("workflow_id", "text", True, None),
    ("herd_evidence", "path", True, None),
    ("verification_log", "path", True, None),
    ("verification_command", "text", True, None),
    ("verification_exit_status", "integer", True, None),
    ("verification_ran_at", "number", False, None),
    ("reverify_command", "text", False, None),
    ("title", "text", True, None),
    ("objective", "text", False, ""),
    ("architecture_notes", "text", False, ""),
    ("nonblocking_risks", "text", False, ""),
    ("base_branch", "text", False, "main"),
    ("remote", "text", False, "origin"),
    ("validity_seconds", "integer", False, None),
    ("mission_workflow_id", "text", False, None),
    ("mission_authorization_digest", "text", False, None),
    # Both or neither (present-dots refuses one alone): an EXISTING open
    # pull request, which selects pr_delivery's ``pr_update`` kind.
    ("pr_number", "integer", False, None),
    ("head_branch", "text", False, None),
)
# The delivery binding a reply must restate (as displayed) and the relay.
DELIVERY_APPROVAL_FIELDS = ("proposal_digest_sha256", "expires_at",
                            "relayed_reply", "reply_to", "relay_ref",
                            "approval_code")
TOOLS = {
    "request": ("text", "conversation_ref", "operator_session_id"),
    "present": ("request_ref",),
    "approve": APPROVE_ARGUMENTS,
    "status": ("request_ref",),
    "result": ("request_ref",),
    "recover": ("request_ref",),
    "cancel": ("request_ref", "control_capability", "text", "conversation_ref"),
    "run": ("request_ref", "command", "arguments"),
    "present_delivery": tuple(field[0] for field in DELIVERY_PRESENT_FIELDS),
    "approve_delivery": DELIVERY_APPROVAL_FIELDS,
    "delivery_status": ("delivery_id",),
}


def _refuse(problem, reason, **details):
    raise surface_module.LocalRequestRefusal(problem, reason, **details)


def _label(result):
    labelled = dict(result)
    labelled.setdefault("delivery_authority", DELIVERY_AUTHORITY)
    labelled.setdefault("evidence_status", EVIDENCE_STATUS)
    labelled["transport"] = TRANSPORT
    return labelled


def refused(problem, reason):
    """A labelled refusal for a caller that never reached a tool."""
    return _label(surface_module.LocalRequestRefusal(problem, reason).as_dict())


# Every code point ``str.splitlines`` treats as a line boundary (pinned by
# test against all of Unicode). JSON escapes those below U+0020 itself; with
# ``ensure_ascii=False`` it would leave U+0085, U+2028 and U+2029 raw.
LINE_BOUNDARIES = "\n\r\x0b\x0c\x1c\x1d\x1e\x85  "


def _escaped(char):
    return char in LINE_BOUNDARIES or "\ud800" <= char <= "\udfff"


def one_line(text):
    """``text`` with every line boundary AND every lone surrogate written
    as its ``\\uXXXX`` escape. Nothing is dropped: inside a JSON value each
    escape decodes back to the exact original code point. Boundaries could
    forge lines; a surrogate (which Mission Core accepts from an escaped
    ``\\ud800`` in JSON) has no UTF-8 form, so left raw it would make an
    accepted proposal impossible to display or digest. The result is one
    line of valid UTF-8."""
    if not any(_escaped(char) for char in text):
        return text
    return "".join("\\u%04x" % ord(char) if _escaped(char) else char
                   for char in text)


def rendered_lines(prefix, value):
    """Every leaf of ``value`` as one ``path: json`` line, empty containers
    included, keys sorted. Generic over the record, so a field Mission Core
    adds is displayed without a change here. Values are JSON-encoded and
    every line boundary is escaped, so no field can start a line of its
    own."""
    if isinstance(value, dict):
        if not value:
            return [one_line("%s: {}" % prefix)]
        return [line for key in sorted(value)
                for line in rendered_lines("%s.%s" % (prefix, key), value[key])]
    if isinstance(value, list):
        if not value:
            return [one_line("%s: []" % prefix)]
        return [line for index, item in enumerate(value)
                for line in rendered_lines("%s[%d]" % (prefix, index), item)]
    return [one_line("%s: %s" % (prefix, json.dumps(value, ensure_ascii=False)))]


def display_text(presented, binding):
    """The human-facing text: the binding a reply must match, then the
    COMPLETE recorded proposal (its proof_contract lines are the
    constraints, evidence requirements and budget). Never shortened, and
    every line is one line: ``splitlines`` finds exactly the joins."""
    return "\n".join(one_line(line) for line in [
        "DODGING INFINITY MISSION PROPOSAL",
        "Mission: %s" % binding["mission_id"],
        "Revision: %d" % binding["revision"],
        "Proposal digest (sha256): %s" % binding["proposal_digest_sha256"],
        "Approved action scope: %s" % json.dumps(binding["approved_action_scope"]),
        "Approved delivery targets: %s" % json.dumps(
            binding["approved_delivery_targets"]),
        "Recorded approval window ends at (unix seconds): %d"
        % binding["expires_at"],
        "",
        "The complete proposal, every field exactly as recorded (nothing is"
        " omitted; the proof_contract lines are its constraints, evidence"
        " requirements and budget):",
    ] + rendered_lines("proposal", presented["proposal"]) + [
        "",
        "THIS PROPOSAL CAN NO LONGER BE APPROVED THROUGH GROK BOT. It is a"
        " legacy Mission-Core proposal, kept readable exactly as recorded;"
        " no arming command and no approval apply to it.",
        conforming.LEGACY_REAUTHORIZATION_REMEDY,
        "Delivery authority: none (no commit, push, pull request, merge,"
        " release or deploy).",
    ])


def _same_as_shown(given, shown):
    """Type-exact, order-exact equality with a displayed value, which is a
    string, an integer or a list of strings (the receipt's validated
    shapes). Never recursive and never hashed: a nested, non-finite or
    non-JSON value is simply not equal."""
    if type(given) is not type(shown):
        return False
    if type(shown) is list:
        return len(given) == len(shown) and all(
            type(item) is str and item == expected
            for item, expected in zip(given, shown))
    return given == shown


def displayed_mismatch(displayed, caller, keys=index_module.DISPLAYED_BINDING_KEYS):
    """The fields the caller supplied that differ from what was displayed.
    An absent field is not compared: the surface refuses it with its own
    existing code (a delivery approval refuses it before comparing)."""
    return sorted(name for name in keys
                  if caller.get(name) is not None
                  and not _same_as_shown(caller[name], displayed[name]))


class GrokBotAdapter(object):

    def __init__(self, surface, operator_session, repository, index, clock,
                 delivery_repository=None, delivery_workspaces_root=None,
                 local_command=None, route=None, route_missing=None,
                 legacy_result=None):
        self._surface = surface
        # I3: the ONE legacy result recovery, as a narrowed READ-ONLY
        # callable ``(missions, mission_id)`` (``grok_bot.cli`` hands in
        # ``recover_legacy_result``); never a bridge or anything that can
        # start work. None: a legacy result read is refused as unavailable.
        self._legacy_result = legacy_result
        # The INITIAL Operator turn's session (route (b)): every request
        # runs through its ``prepare`` then ``execute``.
        self._session = operator_session
        self._repository = repository
        self._index = index
        self._clock = clock
        # I2: the conforming route (``grok_bot.conforming.WorkflowRoute``),
        # or None with the flags whose absence leaves it unconfigured.
        self._route = route
        self._route_missing = tuple(route_missing or (
            ("--control-repo", "--workflow-store") if route is None else ()))
        # The configured, approved identity the delivery tools accept (the
        # serve/run --workspace-repository and --workspaces-root); None
        # refuses every delivery tool.
        self._delivery_repository = delivery_repository
        self._delivery_workspaces_root = delivery_workspaces_root
        # The local command prefix the arming argv starts with (the entry
        # script, its state directory and configured workspaces). It only
        # TELLS the human what to run; nothing here runs it.
        self._local_command = list(local_command or (
            "grokbot.py", "--state-dir", index.directory))

    def call(self, tool, arguments):
        """One tool call: a closed tool name and a closed argument set."""
        def checked():
            if tool not in TOOLS:
                _refuse(PROBLEM_UNKNOWN_TOOL, "unknown tool %r; the tools are %s"
                        % (tool, ", ".join(sorted(TOOLS))))
            if not isinstance(arguments, dict):
                _refuse(PROBLEM_BAD_REQUEST, "tool arguments must be an object")
            unknown = sorted(k for k in arguments if k not in TOOLS[tool])
            if unknown:
                _refuse(PROBLEM_UNKNOWN_FIELD, "unknown argument(s) %s for %s"
                        % (", ".join(repr(k) for k in unknown[:8]), tool))
            # Shape first: nothing below hashes, compares or hands on a
            # value nested past the bound.
            if framing.nesting_exceeds(arguments, framing.MAX_NESTING_DEPTH):
                _refuse(PROBLEM_BAD_REQUEST,
                        "tool arguments nest lists or objects more than %d"
                        " deep" % framing.MAX_NESTING_DEPTH)
            reference = arguments.get("request_ref")
            if reference is not None and not isinstance(reference, str):
                _refuse(PROBLEM_BAD_REQUEST,
                        "request_ref must be null or a string")
            return getattr(self, "_" + tool)(**arguments)
        return self._guarded(checked)

    def _guarded(self, operation):
        # At call time, never at import (see grok_bot.conforming.workflow_store).
        workflow_store_module = conforming.workflow_store()
        try:
            result = operation()
        except surface_module.LocalRequestRefusal as refusal:
            result = refusal.as_dict()
        except (request_store.LocalRequestStoreError,
                index_module.RequestIndexError) as exc:
            result = {"ok": False, "status": "store_error",
                      "problem": exc.problem, "reason": str(exc)}
        except workflow_store_module.StoreError as exc:
            result = {"ok": False, "status": "store_error",
                      "problem": conforming.PROBLEM_WORKFLOW_STORE_UNREADABLE,
                      "reason": str(exc)}
        except surface_module.MISSION_CORE_ERRORS as exc:
            result = {"ok": False, "status": "refused",
                      "problem": getattr(exc, "problem", None),
                      "reason": str(exc)}
        return _label(result)

    # -- the tools: keyword arguments, absent means None ------------------

    def request(self, **arguments):
        return self.call("request", arguments)

    def present(self, **arguments):
        return self.call("present", arguments)

    def approve(self, **arguments):
        return self.call("approve", arguments)

    def status(self, **arguments):
        return self.call("status", arguments)

    def result(self, **arguments):
        return self.call("result", arguments)

    def recover(self, **arguments):
        return self.call("recover", arguments)

    def cancel(self, **arguments):
        return self.call("cancel", arguments)

    def run(self, **arguments):
        return self.call("run", arguments)

    def present_delivery(self, **arguments):
        return self.call("present_delivery", arguments)

    def approve_delivery(self, **arguments):
        return self.call("approve_delivery", arguments)

    def delivery_status(self, **arguments):
        return self.call("delivery_status", arguments)

    # -- implementations ---------------------------------------------------

    @staticmethod
    def _origin(text, conversation_ref):
        """A request's origin: exactly what keys it in the index."""
        if not isinstance(text, str) or not text.strip() or len(text) > (
            framing.MAX_TEXT_CHARS
        ):
            _refuse(PROBLEM_BAD_REQUEST,
                    "text must be non-empty plain text of at most %d characters;"
                    " it is never truncated" % framing.MAX_TEXT_CHARS)
        if conversation_ref is not None and (not isinstance(conversation_ref, str)
                                             or len(conversation_ref) > MAX_REF_CHARS):
            _refuse(PROBLEM_BAD_REQUEST, "conversation_ref must be null or a"
                    " string of at most %d characters" % MAX_REF_CHARS)
        return {"text": text, "conversation_ref": conversation_ref}

    def _request(self, text=None, conversation_ref=None, operator_session_id=None):
        origin = self._origin(text, conversation_ref)
        if operator_session_id is not None and (
            not isinstance(operator_session_id, str)
            or len(operator_session_id) > MAX_REF_CHARS
        ):
            _refuse(PROBLEM_BAD_REQUEST, "operator_session_id must be null or a"
                    " string of at most %d characters" % MAX_REF_CHARS)
        if operator_session_id is not None:
            # Before the index or the provider: nothing is recorded and no
            # turn runs. Not a foreign id only, but every id, including one
            # this transport's own reply carried: the restrictive posture is
            # fresh-only (its verifier refuses a ``resume``), so no session
            # can be continued under it.
            _refuse(PROBLEM_OPERATOR_SESSION_REFUSED,
                    "the request tool continues no Operator session: every"
                    " request is one fresh, read-only Operator turn. Send the"
                    " whole request again as text (with the answer to any"
                    " question the Operator asked) and no operator_session_id;"
                    " nothing was run or recorded")
        route = self._require_route()
        if self._session is None:
            _refuse(conforming.PROBLEM_NOT_CONFIGURED,
                    "the conforming engineering route has no operator session"
                    " for its initial Operator turn; nothing was run or"
                    " recorded", missing=["operator session"])
        if any("\ud800" <= char <= "\udfff" for char in text):
            # Before the index or the planning turn: a lone surrogate has no
            # UTF-8 form, so it could be neither recorded nor digested.
            _refuse(PROBLEM_BAD_REQUEST,
                    "text must be valid Unicode text: a lone surrogate code"
                    " point cannot be recorded or digested; nothing was run")
        key = json_digest(origin)
        state, request_ref = self._index.begin(key, self._clock())
        if state == index_module.STATE_PROPOSED:
            result = self._status(request_ref)
            result.update(duplicate=True, request_ref=request_ref)
            return result
        if state is not None:
            _refuse(PROBLEM_IN_FLIGHT,
                    "an identical request is in flight or its outcome is unknown;"
                    " nothing was proposed again. Read the pending proposal"
                    " with present, or change the request text to ask anew")
        # I2, the conforming route (OPERATOR_PROTOCOL.md route (b)). The
        # INITIAL Operator turn runs through this adapter's operator
        # session: ``prepare`` builds the request, ``execute`` makes its one
        # provider call with that same request. It clarifies in prose or
        # emits the routing signal; the signal triggers the SEPARATE fresh
        # planning turn (``route.plan``), whose Mission Authorization the
        # trusted local seam stamps and saves as one PLANNED workflow.
        # Nothing goes to the local request surface or Mission Core.
        framed, neutralized = framing.operator_text(text)
        try:
            try:
                prepared = self._session.prepare(
                    framed, route.control_repository, session_id=None,
                    source=TRANSPORT)
                outcome = self._session.execute(prepared)
            except Exception as exc:
                _refuse(PROBLEM_OPERATOR_FAILED, "the Operator turn raised %s;"
                        " nothing was proposed" % type(exc).__name__)
            if outcome.status != gateway_contract.STATUS_COMPLETED:
                _refuse(PROBLEM_OPERATOR_FAILED, "the Operator turn ended %r;"
                        " nothing was proposed" % (outcome.status,),
                        operator_status=outcome.status)
            kind, clarification = conforming.classify_operator_reply(
                outcome.message)
            if kind == conforming.REPLY_CLARIFICATION:
                # Nothing proposed, nothing recorded, no pending entry left.
                self._index.abandon(key)
                return {
                    "ok": True, "status": "clarification",
                    "lane": conforming.LANE_WORKFLOW, "proposed": False,
                    "duplicate": False, "clarification": clarification,
                    "neutralized": neutralized,
                    "note": "The Operator asked a question; nothing was"
                            " proposed or recorded. Answer by sending the"
                            " whole request again as text.",
                }
            entry, request_ref = route.plan(text, self._index, self._clock())
            voided = route.record(entry, self._clock())
        except Exception:
            self._index.abandon(key)
            raise
        now = self._clock()
        capability = route.mint_capability()
        try:
            # Recovery needs an origin no reader of the authorization can
            # restate: never kept for a request without a conversation_ref.
            recovery = index_module.seal_capability(
                origin, request_ref, capability) if conversation_ref else None
            self._index.record_workflow_proposed(
                key, request_ref, entry["workflow_id"],
                request_store.token_digest(capability),
                now + request_store.CONTROL_CAPABILITY_VALIDITY_SECONDS, now,
                recovery)
        except Exception:
            # Never leave an unindexed record pending approval. The index
            # entry stays IN_FLIGHT: its outcome is unknown from here, so an
            # identical request is held, never planned twice.
            route.void_one(entry["workflow_id"])
            raise
        return {
            "ok": True, "status": "authorization_proposed",
            "lane": conforming.LANE_WORKFLOW, "duplicate": False,
            "request_ref": request_ref, "workflow_id": entry["workflow_id"],
            "phase": entry["phase"],
            "authorization_digest_sha256": (
                entry["mission_authorization"]["digest_sha256"]),
            "expires_at": entry["approval"]["expires_at"],
            "expired_requests_voided": len(voided),
            "control_capability": capability,
            "control_capability_recovery": (
                RECOVERY_KEPT if recovery is not None else RECOVERY_NOT_KEPT),
            "next": "Call present with this request_ref and show the human"
                    " its display_text whole.",
        }

    def _require_route(self):
        """The conforming route, or a refusal naming each missing flag."""
        if self._route is None:
            _refuse(conforming.PROBLEM_NOT_CONFIGURED,
                    "the conforming engineering route is not configured: %s"
                    " missing. --control-repo is the control repository the"
                    " planning turn runs in and every Mission Authorization is"
                    " pinned to; --workflow-store is the DI-REMOTE-2 workflow"
                    " store the Runtime (dirun) claims from. Neither has a"
                    " default. Nothing was run or recorded"
                    % " and ".join(self._route_missing),
                    missing=list(self._route_missing))
        return self._route

    def _present(self, request_ref=None):
        if index_module.is_workflow_ref(request_ref):
            return self._present_workflow(request_ref)
        presented = self._present_legacy(request_ref)
        # Readable as recorded, labelled truthfully, and offering NO
        # arming and no binding to relay: this lane's approval is refused,
        # so the remedy is shown here, before the human acts (I3, B-1).
        presented.update(lane=conforming.LANE_LEGACY, approvable=False,
                         approval_remedy=conforming.LEGACY_REAUTHORIZATION_REMEDY)
        return presented

    def _present_workflow(self, request_ref):
        """The EXACT rendered Mission Authorization, its binding and its
        local arming command. One critical section with every approval and
        arming: the record read and the receipt write land together, and
        the receipt is durable BEFORE the display is returned."""
        route = self._require_route()
        with self._index.serialized():
            held = self._index.workflow_request(request_ref)
            if held is None:
                _refuse(conforming.PROBLEM_UNKNOWN_REQUEST,
                        "no conforming-route request %r is held here; nothing"
                        " was displayed" % (request_ref,))
            entry = route.entry(request_ref, held)
            if not conforming.is_pending(entry):
                _refuse(conforming.PROBLEM_NOT_AWAITING,
                        "workflow %s is %s and no longer awaiting its first"
                        " decision; nothing was displayed. %s"
                        % (entry["workflow_id"], entry["phase"],
                           conforming.not_awaiting_remedy(entry)),
                        phase=entry["phase"])
            now = self._clock()
            if now >= entry["approval"]["expires_at"]:
                _refuse(conforming.PROBLEM_EXPIRED,
                        "the Mission Authorization for request %s expired at"
                        " %d; it can never be approved. %s Nothing was"
                        " displayed" % (request_ref,
                                        entry["approval"]["expires_at"],
                                        conforming.FRESH_REQUEST_REMEDY))
            binding = conforming.binding_of(entry, request_ref)
            text = conforming.display_text(entry, binding)
            # Recorded BEFORE it is returned: nothing is displayed unrecorded.
            self._index.record_workflow_presentation(
                binding, text_digest(text), now)
        return {
            "ok": True, "status": "authorization_presented",
            "lane": conforming.LANE_WORKFLOW, "request_ref": request_ref,
            "workflow_id": entry["workflow_id"], "phase": entry["phase"],
            "approval_binding": binding, "display_text": text,
            "display_digest_sha256": text_digest(text),
            "rendered_text": entry["mission_authorization"]["rendered_text"],
            "arming": arming.arming(arming.workflow_arming_argv(
                self._local_command, binding, text_digest(text))),
            "limitation": LIMITATION,
        }

    def _present_legacy(self, request_ref):
        # One critical section with every approval: the surface read and
        # the receipt write land together (see RequestIndex.serialized).
        with self._index.serialized():
            presented = self._surface.present(request_ref)
            binding = {
                "request_ref": presented["request_ref"],
                "mission_id": presented["mission_id"],
                "revision": presented["revision"],
                "proposal_digest_sha256": presented["proposal_digest_sha256"],
                "approved_action_scope": list(presented["approved_action_scope"]),
                "approved_delivery_targets": list(
                    presented["approved_delivery_targets"]),
                "expires_at": presented["latest_expires_at"],
            }
            text = display_text(presented, binding)
            # Recorded BEFORE it is returned: nothing is displayed unrecorded.
            self._index.record_presentation(binding, text_digest(text),
                                            self._clock())
        # No ``approval_binding`` and no ``arming`` (I3, B-1): an approval
        # of this proposal is guaranteed to be refused, so nothing invites
        # one.
        presented.update(display_text=text,
                         display_digest_sha256=text_digest(text))
        return presented

    def _approve(self, **binding):
        """The conforming route's approval for a ``gr-`` request; for any
        other request, the legacy lane's reauthorization remedy (I2): a
        Mission-Core proposal is never approved here any more, and never
        converted into DI-REMOTE-2 authority."""
        request_ref = binding.get("request_ref")
        if index_module.is_workflow_ref(request_ref):
            return self._approve_workflow(binding)
        if not isinstance(request_ref, str) or request_store.request_ref_problem(
            request_ref
        ) is not None:
            _refuse(PROBLEM_BAD_REQUEST,
                    "approve needs the request_ref a request returned;"
                    " nothing was recorded")
        # Read-only, so the refusal states the legacy request's real state,
        # Mission Core's and the local request's both (a withdrawn proposal
        # stays AWAITING_DECISION in Mission Core while its request is
        # CANCELLED); an unknown request is refused by the surface's own
        # code.
        legacy = self._surface.status(request_ref)
        _refuse(conforming.PROBLEM_LEGACY_REAUTHORIZATION,
                conforming.LEGACY_REAUTHORIZATION_REMEDY,
                request_ref=request_ref, lane=conforming.LANE_LEGACY,
                legacy_mission_state=(legacy.get("run") or {}).get(
                    "lifecycle_state"),
                legacy_request_state=legacy.get("surface_state"))

    def _approve_workflow(self, binding):
        """Compare, then fire, then apply, on the conforming route. The
        caller's binding must equal the one last displayed for its request
        in EVERY field, type-exactly (an absent field is a difference);
        nothing is filled in or corrected from the receipt. The relayed
        reply must be an exact affirmative. Then the record must still be
        approvable, the arming must fire for exactly that binding (consumed
        durably BEFORE anything is applied), and only then is the approval
        consumed one-shot in the workflow record. The comparison, the
        firing and the application run in ONE critical section."""
        route = self._require_route()
        foreign = sorted(name for name in LEGACY_ONLY_FIELDS
                         if binding.get(name) is not None)
        if foreign:
            _refuse(conforming.PROBLEM_WRONG_LANE_FIELDS,
                    "a Mission Authorization binding carries no %s (those are"
                    " Mission-Core fields); nothing was recorded"
                    % ", ".join(foreign), fields=foreign)
        relayed_reply, relay_ref = binding.get("relayed_reply"), binding.get(
            "relay_ref")
        if not isinstance(relayed_reply, str) or len(relayed_reply) > (
            request_store.MAX_RELAYED_REPLY_CHARS
        ) or not request_store.is_exact_affirmative(relayed_reply):
            _refuse(surface_module.PROBLEM_NOT_AFFIRMATIVE,
                    "the relayed reply is not an exact affirmative: only the"
                    " whole reply %s (case-insensitive, surrounding whitespace"
                    " ignored) counts; nothing was recorded"
                    % " or ".join(repr(r) for r in
                                  request_store.AFFIRMATIVE_REPLIES))
        if not isinstance(relay_ref, str) or len(relay_ref) > (
            request_store.MAX_RELAY_REF_CHARS
        ):
            _refuse(PROBLEM_BAD_REQUEST, "relay_ref must be a short string")
        request_ref = binding["request_ref"]
        with self._index.serialized():
            receipt = self._index.workflow_presentation(request_ref)
            held = self._index.workflow_request(request_ref)
            if receipt is None or held is None:
                _refuse(PROBLEM_NOT_PRESENTED,
                        "nothing was displayed for request %r through this"
                        " adapter; present it, show the human that exact text,"
                        " then relay the reply. Nothing was recorded"
                        % (request_ref,))
            shown = receipt["binding"]
            # The same comparator as every other approval here; an ABSENT
            # field is a difference too (nothing is filled in).
            differing = sorted(set(displayed_mismatch(
                shown, binding, index_module.WORKFLOW_BINDING_KEYS)) | set(
                name for name in index_module.WORKFLOW_BINDING_KEYS
                if binding.get(name) is None))
            if differing:
                _refuse(PROBLEM_NOT_DISPLAYED,
                        "the relayed binding differs from the binding last"
                        " displayed for request %s in %s; nothing was recorded"
                        " and nothing was corrected" % (
                            request_ref, ", ".join(differing)),
                        fields=differing)
            display = receipt["display_digest_sha256"]
            route.check_approvable(request_ref, held, shown, self._clock())
            self._fire(arming.workflow_key(request_ref),
                       lambda code: arming.workflow_preimage(shown, display,
                                                             code),
                       binding.get("approval_code"))
            result = route.consume(request_ref, held, shown, self._clock())
        # Labelled as what it is, with the legacy lane's own label: an
        # operator attestation, not independently verified.
        result.update(relayed_reply=relayed_reply, relay_ref=relay_ref,
                      provenance_label=dict(surface_module.PROVENANCE_LABEL),
                      arming={"locally_armed": True,
                              "arming_source": "local_shell_command"},
                      limitation=LIMITATION)
        return result

    def _fire(self, key, preimage_for, approval_code):
        """Fire the approval armed under ``key``, or refuse; the caller holds
        ``serialized()``. The code must reproduce the stored commitment over
        exactly the displayed binding. A wrong code is counted (the
        commitment dies at ``arming.MAX_CODE_FAILURES``); a right one is
        recorded CONSUMED, durably, before anything is applied, so an armed
        approval fires once and a crash after this point never applies it
        twice. This reads the commitments; it never writes them."""
        if approval_code is None:
            _refuse(arming.PROBLEM_NOT_ARMED,
                    "an approval_code is required: arm the approval with the"
                    " local arming command on the DI machine first; nothing was"
                    " recorded")
        if not arming.is_code(approval_code):
            _refuse(PROBLEM_BAD_REQUEST,
                    "approval_code must be the %d lowercase hex characters the"
                    " local arming printed" % arming.CODE_HEX_CHARS)
        try:
            record = arming.load_commitments(
                self._index.directory)["commitments"].get(key)
        except arming.CommitmentsError as exc:
            raise index_module.RequestIndexError(str(exc), exc.problem)
        if record is None:
            _refuse(arming.PROBLEM_NOT_ARMED,
                    "nothing is armed for this approval: run the arming"
                    " command presented with it in the local shell on the DI"
                    " machine; nothing was recorded")
        digest = record["commitment_sha256"]
        marker = self._index.consumption(digest)
        if marker is not None and marker["state"] != index_module.CONSUMPTION_OPEN:
            _refuse(arming.PROBLEM_CONSUMED,
                    "this armed approval was already %s; arm it again to"
                    " approve. Nothing was recorded" % marker["state"].lower(),
                    state=marker["state"])
        if not arming.reproduces(digest, preimage_for(approval_code)):
            marker = self._index.record_code_failure(
                digest, self._clock(), arming.MAX_CODE_FAILURES)
            _refuse(arming.PROBLEM_CODE_MISMATCH,
                    "the approval_code does not fire the approval armed for"
                    " exactly this binding (%d of %d wrong codes; at %d it is"
                    " killed); nothing was recorded"
                    % (marker["failures"], arming.MAX_CODE_FAILURES,
                       arming.MAX_CODE_FAILURES),
                    failures=marker["failures"])
        self._index.record_consumed(digest, self._clock())

    def _held_workflow(self, request_ref):
        route = self._require_route()
        held = self._index.workflow_request(request_ref)
        if held is None:
            _refuse(conforming.PROBLEM_UNKNOWN_REQUEST,
                    "no conforming-route request %r is held here"
                    % (request_ref,))
        return route, held

    def _status(self, request_ref=None):
        """READ-ONLY, and it names the request's LANE truthfully: the
        conforming route's workflow (``gr-``, a lock-free snapshot of the
        durable record: ``conforming.WorkflowRoute.status``) or a legacy
        Mission-Core request (``lr-``, the surface's own read)."""
        if index_module.is_workflow_ref(request_ref):
            route, held = self._held_workflow(request_ref)
            return route.status(request_ref, held, self._clock())
        result = self._surface.status(request_ref)
        result.update(lane=conforming.LANE_LEGACY, approvable=False,
                      remedy=conforming.LEGACY_REAUTHORIZATION_REMEDY)
        return result

    def _result(self, request_ref=None):
        """READ-ONLY. A ``gr-`` workflow's verified result by pull from its
        durable record (independent of any Telegram delivery lane); a
        legacy request's VERIFIED run result and review text, recovered by
        the one implementation through the narrowed ``legacy_result``
        callable (or UNRECOVERABLE, never a verified result). Starts,
        advances, records and consumes nothing."""
        if index_module.is_workflow_ref(request_ref):
            route, held = self._held_workflow(request_ref)
            return route.result(request_ref, held, self._clock())
        if not isinstance(request_ref, str) or request_store.request_ref_problem(
            request_ref
        ) is not None:
            _refuse(PROBLEM_BAD_REQUEST,
                    "result needs the request_ref a request returned")
        if self._legacy_result is None:
            _refuse(PROBLEM_LEGACY_RESULT_UNAVAILABLE,
                    "this adapter was built without the legacy result"
                    " recovery; nothing was read")
        result = self._surface.read_result(request_ref, self._legacy_result)
        result.update(lane=conforming.LANE_LEGACY,
                      note=conforming.LEGACY_RESULT_NOTE)
        return result

    def _recover(self, request_ref=None):
        if index_module.is_workflow_ref(request_ref):
            _refuse(conforming.PROBLEM_RECOVER_NOT_APPLICABLE,
                    "recover binds an interrupted Mission-Core proposal; request"
                    " %s is a conforming-route workflow, which has none to"
                    " recover. Read it with status; nothing was changed"
                    % request_ref)
        return self._surface.recover(request_ref)

    def _cancel(self, request_ref=None, control_capability=None, text=None,
                conversation_ref=None):
        if text is None and conversation_ref is None:
            if index_module.is_workflow_ref(request_ref):
                return self._withdraw_workflow(request_ref, control_capability)
            return self._surface.cancel(request_ref, control_capability)
        if control_capability is not None:
            _refuse(PROBLEM_BAD_REQUEST,
                    "cancel takes either the control_capability, or the original"
                    " request's text and conversation_ref (recovery after a lost"
                    " reply), never both; nothing was cancelled")
        origin = self._origin(text, conversation_ref)
        if index_module.is_workflow_ref(request_ref):
            return self._recover_withdraw_workflow(request_ref, origin)
        return self._recover_cancel(request_ref, origin)

    def _withdraw_workflow(self, request_ref, control_capability):
        """Pending withdrawal on the conforming route, proven by the
        request's own one-shot capability (``WorkflowRoute.withdraw``)."""
        route = self._require_route()
        with self._index.serialized():
            held = self._index.workflow_request(request_ref)
            if held is None:
                _refuse(conforming.PROBLEM_UNKNOWN_REQUEST,
                        "no conforming-route request %r is held here; nothing"
                        " was cancelled" % (request_ref,))
            return route.withdraw(request_ref, held, control_capability,
                                  self._clock())

    def _recover_withdraw_workflow(self, request_ref, origin):
        """Pending withdrawal after a LOST request reply: the capability
        sealed under ``origin`` (the request's exact text and
        conversation_ref) is unsealed and goes to the same withdrawal, with
        every same check. A request_ref alone never suffices."""
        route = self._require_route()
        with self._index.serialized():
            entry = self._index.entry(json_digest(origin))
            if entry is not None and entry["state"] == index_module.STATE_IN_FLIGHT:
                _refuse(PROBLEM_RECOVERY_INTERRUPTED,
                        "the original request's local record was interrupted:"
                        " its index entry is still IN_FLIGHT, so no recovery"
                        " material was kept and none is fabricated; nothing"
                        " was cancelled", condition="in_flight",
                        request_ref=request_ref)
            if entry is None or entry["request_ref"] != request_ref:
                _refuse(PROBLEM_ORIGIN_MISMATCH,
                        "the origin proof (the exact request text and"
                        " conversation_ref) does not name request %s; nothing was"
                        " cancelled. A proof binds exactly the one request it"
                        " created, and knowing a request_ref or a workflow id"
                        " never permits control" % (request_ref,),
                        request_ref=request_ref)
            held = self._index.workflow_request(request_ref)
            if held is None:
                _refuse(conforming.PROBLEM_UNKNOWN_REQUEST,
                        "no conforming-route request %r is held here; nothing"
                        " was cancelled" % (request_ref,))
            recovery = entry.get("recovery")
            if recovery is None:
                _refuse(PROBLEM_RECOVERY_UNAVAILABLE,
                        "request %s holds no recovery material: it carried no"
                        " conversation_ref, which recovery requires (the exact"
                        " text alone may be restated from the authorization"
                        " anyone can read). Its withdrawal capability cannot be"
                        " recovered and is never fabricated; nothing was"
                        " cancelled" % request_ref, request_ref=request_ref)
            result = route.withdraw(
                request_ref, held,
                index_module.unseal_capability(origin, request_ref, recovery),
                self._clock())
        result.update(cancelled_through="origin_recovery")
        return result

    def _recover_cancel(self, request_ref, origin):
        """Cancel through the capability sealed under ``origin``. Every
        surface check still applies: the unsealed capability goes to
        ``LocalRequestSurface.cancel`` exactly like a caller's own."""
        with self._index.serialized():
            entry = self._index.entry(json_digest(origin))
            if entry is not None and entry["state"] == index_module.STATE_IN_FLIGHT:
                _refuse(PROBLEM_RECOVERY_INTERRUPTED,
                        "the original request's local record was interrupted: its"
                        " index entry is still IN_FLIGHT (the process stopped, or a"
                        " write failed, between proposing and recording the"
                        " proposal), so no recovery material was kept and none is"
                        " fabricated; nothing was cancelled. status and present"
                        " read what the surface holds and recover binds an"
                        " interrupted proposal; only the control capability its"
                        " request reply carried can cancel it",
                        condition="in_flight", request_ref=request_ref)
            if entry is None and not self._index.indexes_request(request_ref):
                self._surface.status(request_ref)  # the surface's own refusal
                _refuse(PROBLEM_RECOVERY_INTERRUPTED,
                        "request %s is held by the local request surface, but this"
                        " adapter's index holds no entry naming it (it was not"
                        " created through this adapter, or its index entry was"
                        " never recorded), so no recovery material exists and"
                        " none is fabricated; nothing was cancelled. Only the"
                        " control capability its creation reply carried can"
                        " cancel it" % request_ref,
                        condition="not_indexed", request_ref=request_ref)
            if entry is None or entry["request_ref"] != request_ref:
                _refuse(PROBLEM_ORIGIN_MISMATCH,
                        "the origin proof (the exact request text and"
                        " conversation_ref) does not name request %s; nothing was"
                        " cancelled. A proof binds exactly the one request it"
                        " created, and knowing a request_ref or a Mission id never"
                        " permits control" % (request_ref,),
                        request_ref=request_ref)
            status = self._surface.status(request_ref)
            if status["surface_state"] == request_store.STATE_CANCELLED:
                _refuse(surface_module.PROBLEM_CANCELLED,
                        surface_module.CANCELLED_REASON, request_ref=request_ref)
            incomplete = status.get("local_cancellation") == "INCOMPLETE"
            recovery = entry.get("recovery")
            if recovery is None and incomplete:
                _refuse(PROBLEM_RECOVERY_INTERRUPTED,
                        "local cancellation of request %s is INCOMPLETE: Mission"
                        " Core already carries its withdrawal marker (every"
                        " decision on it is refused) but the local save did not"
                        " land. This adapter holds no recovery material for it;"
                        " the surface's remedy is to run cancel again with the"
                        " same control capability" % request_ref,
                        condition="local_cancellation_incomplete",
                        request_ref=request_ref)
            if recovery is None:
                why = ("it carried no conversation_ref, which recovery requires"
                       " (the exact text alone may be restated from the proposal"
                       " anyone can read)" if not origin["conversation_ref"] else
                       "it was recorded before this adapter kept any")
                # The Mission's state as Mission Core reports it NOW, never
                # assumed (the status read above).
                mission_state = (status.get("run") or {}).get("lifecycle_state")
                if mission_state is None:
                    state = ("its Mission's current state could not be read"
                             " from Mission Core")
                elif mission_state == (
                    surface_module.mission_record.STATE_AWAITING_DECISION
                ):
                    state = ("its Mission is still AWAITING_DECISION, and"
                             " without the capability its original request"
                             " reply carried it cannot be cancelled through"
                             " this transport")
                else:
                    state = "its Mission is %s" % mission_state
                _refuse(PROBLEM_RECOVERY_UNAVAILABLE,
                        "request %s holds no recovery material: %s. Its control"
                        " capability cannot be recovered and is never"
                        " fabricated, and nothing was cancelled; %s"
                        % (request_ref, why, state),
                        request_ref=request_ref, mission_state=mission_state)
            result = self._surface.cancel(request_ref, index_module.unseal_capability(
                origin, request_ref, recovery))
        result.update(cancelled_through="origin_recovery",
                      completed_interrupted_cancellation=incomplete)
        return result

    # -- the SEPARATE delivery ceremony (slice 3) --------------------------
    #
    # pr_delivery is imported lazily, by these three alone (grok_bot.delivery
    # is the one module allowed to import it), so no other tool, and no
    # startup, loads the delivery package.

    def _present_delivery(self, **arguments):
        from grok_bot import delivery as delivery_module
        args = delivery_module.ceremony_arguments(arguments)
        # The identity check, on the filesystem, BEFORE the ceremony (and so
        # before any Git process): the ceremony receives exactly the checked
        # name, which is its own realpath.
        args.repo = delivery_module.approved_repository(
            args.repo, self._delivery_repository, self._delivery_workspaces_root)
        # One critical section with every delivery approval, exactly as for
        # Mission presentations: the ceremony's read and the receipt write
        # land together.
        with self._index.serialized():
            document = delivery_module.present(args)
            proposal = document["delivery_proposal"]
            digest = document["proposal_digest_sha256"]
            text = delivery_module.display_text(document)
            # Recorded BEFORE it is returned: nothing is displayed unrecorded.
            self._index.record_delivery_presentation(digest, proposal,
                                                     text_digest(text))
        return {
            "ok": True, "status": "delivery_presented",
            "proposal_digest_sha256": digest, "delivery_proposal": proposal,
            "approval_binding": {"proposal_digest_sha256": digest,
                                 "expires_at": proposal["expires_at"]},
            "display_text": text, "display_digest_sha256": text_digest(text),
            "arming": arming.arming(arming.delivery_arming_argv(
                self._local_command, digest, proposal["expires_at"],
                text_digest(text), args.repo)),
            "reply": document["reply"], "source": document["source"],
            "residual_risk": document["residual_risk"], "limitation": LIMITATION,
        }

    def _approve_delivery(self, proposal_digest_sha256=None, expires_at=None,
                          relayed_reply=None, reply_to=None, relay_ref=None,
                          approval_code=None):
        """Compare, then refuse, then relay. The digest the human's reply
        answers names the presented proposal: the receipt is content-
        addressed (its key is that proposal's own digest), so the proposal
        handed to the ceremony is byte-for-byte the one displayed under it,
        never a different or current one. A displayed field that differs is
        refused by name and never corrected."""
        from grok_bot import delivery as delivery_module
        # The WHOLE displayed binding is restated, or nothing is relayed.
        for name, value in (("proposal_digest_sha256", proposal_digest_sha256),
                            ("expires_at", expires_at)):
            if value is None:
                _refuse(PROBLEM_BAD_REQUEST, "approve_delivery needs %s, exactly"
                        " as displayed" % name)
        for name, value in (("relayed_reply", relayed_reply),
                            ("reply_to", reply_to), ("relay_ref", relay_ref)):
            if value is not None and not isinstance(value, str):
                _refuse(PROBLEM_BAD_REQUEST, "%s must be null or a string" % name)
        digest = proposal_digest_sha256
        if not isinstance(digest, str):
            _refuse(PROBLEM_BAD_REQUEST, "proposal_digest_sha256 must be a string")
        with self._index.serialized():
            receipt = self._index.delivery_presentation(digest)
            if receipt is None:
                _refuse(PROBLEM_NOT_PRESENTED,
                        "no delivery proposal %r was displayed through this"
                        " adapter; present it, show the human that exact text,"
                        " then relay the reply. Nothing was recorded" % (digest,))
            differing = displayed_mismatch(
                {"expires_at": receipt["proposal"]["expires_at"]},
                {"expires_at": expires_at}, ("expires_at",))
            if differing:
                _refuse(PROBLEM_NOT_DISPLAYED,
                        "the relayed delivery binding differs from the one"
                        " displayed in %s; nothing was recorded and nothing was"
                        " corrected" % ", ".join(differing), fields=differing)
            # The same identity as present_delivery, re-checked before the
            # ceremony re-reads the live repository with Git.
            realpath = delivery_module.bound_repository(
                receipt["proposal"], self._delivery_repository,
                self._delivery_workspaces_root)
            display = receipt["display_digest_sha256"]
            shown_expiry = receipt["proposal"]["expires_at"]
            self._fire(arming.delivery_key(digest),
                       lambda code: arming.delivery_preimage(
                           digest, shown_expiry, display, realpath, code),
                       approval_code)
            delivery_id = delivery_module.attest(
                receipt["proposal"], digest, relayed_reply, reply_to, relay_ref)
        return {
            "ok": True, "status": "delivery_authorized_by_operator_attestation",
            "delivery_id": delivery_id, "proposal_digest_sha256": digest,
            "delivery_authorization": delivery_module.grant(
                delivery_id, receipt["proposal"]),
            "arming": {"locally_armed": True,
                       "arming_source": "local_shell_command"},
        }

    def _delivery_status(self, delivery_id=None):
        from grok_bot import delivery as delivery_module
        if not isinstance(delivery_id, str):
            _refuse(PROBLEM_BAD_REQUEST, "delivery_id must be a string")
        return {"ok": True, "status": "delivery_status", "delivery_id": delivery_id,
                "delivery": delivery_module.status(
                    delivery_id, self._delivery_repository,
                    self._delivery_workspaces_root)}

    def _run(self, request_ref=None, command=None, arguments=None):
        """RETIRED as an execution path (I3, B-2): Grok Bot runs nothing,
        and nothing here reaches a run bridge (this adapter is built without
        one). The arguments are still checked for shape (a malformed call
        keeps its bad-request refusal). ``result`` is answered by the
        read-only result, on either lane; every other command is refused
        by name with the advice that is true for it (``LEGACY_RUN_*``): a
        conforming-route workflow is the Runtime's to advance, and a legacy
        Mission-Core request is never dispatched or advanced here."""
        if command is not None and not isinstance(command, str):
            _refuse(PROBLEM_BAD_REQUEST, "command must be null or a string")
        if command in LEGACY_RUN_READ and arguments in (None, {}):
            return self._result(request_ref)
        if index_module.is_workflow_ref(request_ref):
            _refuse(conforming.PROBLEM_RUN_NOT_APPLICABLE,
                    "request %s is a conforming-route workflow: Grok Bot runs"
                    " nothing, and only a DI Runtime (dirun) running against"
                    " its workflow store advances it. Read it with status"
                    " and result; nothing was run" % request_ref)
        if command not in self._surface.RUN_COMMANDS:
            _refuse(PROBLEM_BAD_REQUEST, "unknown run command %r; nothing was"
                    " run" % (command,))
        arguments = {} if arguments is None else arguments
        expected = RUN_ARGUMENTS.get(command, {})
        optional = RUN_OPTIONAL_ARGUMENTS.get(command, {})
        if not isinstance(arguments, dict) or not set(expected) <= set(
            arguments
        ) or set(arguments) - set(expected) - set(optional):
            _refuse(PROBLEM_BAD_REQUEST, "run %r takes exactly the arguments %s"
                    "%s" % (command, sorted(expected), (
                        " and optionally %s" % sorted(optional)
                        if optional else "")))
        kinds = dict(expected)
        kinds.update((name, optional[name]) for name in arguments
                     if name in optional)
        for name, kind in sorted(kinds.items()):
            if kind is not None and not isinstance(arguments[name], kind):
                _refuse(PROBLEM_BAD_REQUEST, "run %s argument %r must be a %s"
                        % (command, name, kind.__name__))
        if not isinstance(request_ref, str) or request_store.request_ref_problem(
            request_ref
        ) is not None:
            _refuse(PROBLEM_BAD_REQUEST,
                    "run needs the request_ref a request returned; Grok Bot"
                    " runs nothing")
        # Read-only, so the refusal states the legacy request's real
        # state; an unknown request is refused by the surface's own code.
        legacy = self._surface.status(request_ref)
        states = {"request_ref": request_ref, "lane": conforming.LANE_LEGACY,
                  "command": command,
                  "legacy_mission_state": (legacy.get("run") or {}).get(
                      "lifecycle_state"),
                  "legacy_request_state": legacy.get("surface_state")}
        if command in LEGACY_RUN_OBSERVE:
            _refuse(conforming.PROBLEM_LEGACY_RUN_UNAVAILABLE,
                    conforming.LEGACY_OBSERVE_ADVICE, **states)
        if command in LEGACY_RUN_CONTROL:
            _refuse(conforming.PROBLEM_LEGACY_RUN_UNAVAILABLE,
                    conforming.legacy_control_advice(
                        command, request_ref, self._index.directory,
                        None if self._route is None
                        else self._route.control_repository), **states)
        _refuse(conforming.PROBLEM_LEGACY_REAUTHORIZATION,
                conforming.LEGACY_RUN_REMEDY, **states)
